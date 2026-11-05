"""Sound playback and desktop notifications for Omnify.

Thin, dependency-free wrappers around system command-line tools. This
system has no GNotify typelib, so desktop notifications go through
``notify-send``; sound files are played with the first available CLI
player (``paplay``, ``canberra-gtk-play``, ``ffplay``, ``aplay``). Both
the binary lookup (``which``) and process spawning (``runner``) are
injectable, so the module is unit-testable headlessly (see
``tests/test_alerts.py``) and degrades gracefully when nothing is
installed: every entry point returns ``False`` instead of raising.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from typing import Callable, List, Optional, Sequence

#: CLI players for plain PCM/WAV/FLAC, in order of preference.
_PCM_PLAYERS = ("paplay", "canberra-gtk-play", "aplay")
#: Player used for compressed formats (Ogg Vorbis, MP3, ...).
_COMPRESSED_PLAYER = "ffplay"

#: File extensions that the PCM players cannot decode.
_COMPRESSED_EXTS = (".oga", ".ogg", ".mp3", ".m4a", ".aac", ".opus", ".wav.mp3")


def _error_log_path(filename: str) -> str:
    """Where the stderr of the last spawned helper is captured."""
    return os.path.join(
        os.path.expanduser("~"), ".local", "share", "omnify", filename
    )


def _spawn_capturing(
    runner: Callable[..., "subprocess.Popen"],
    args: List[str],
    error_log: Optional[str],
) -> Optional["subprocess.Popen"]:
    """Spawn *args* via *runner*, capturing stderr to *error_log*.

    Returns the process, or ``None`` when the spawn failed. The log
    file is first opened to a plain fd: since Python 3.14,
    ``subprocess.Popen`` no longer accepts a string path as a stream
    argument (it calls ``.fileno()`` on non-int values), so passing the
    path itself crashes the spawn. That crash is exactly how a missing
    sound or a missing notification stayed invisible for days.

    Degrades to ``DEVNULL`` when the log directory cannot be created —
    capturing errors must never block playback or notifications.
    """
    if error_log is None:
        fd: int = subprocess.DEVNULL
        own_fd = False
    else:
        try:
            os.makedirs(os.path.dirname(error_log), exist_ok=True)
            fd = os.open(
                error_log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644
            )
            own_fd = True
        except OSError:
            fd = subprocess.DEVNULL
    try:
        proc = runner(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=fd,
        )
    except (OSError, TypeError, ValueError, AttributeError):
        proc = None
    finally:
        # The parent's copy of the fd is not the child's — Popen dups
        # it in, so close ours (Popen leaves passed-in fds open).
        if own_fd:
            try:
                os.close(fd)
            except OSError:
                pass
    return proc


class SoundPlayer:
    """Plays sound files with the first available CLI player.

    ``which`` resolves binary names (default ``shutil.which``) and
    ``runner`` spawns processes (default ``subprocess.Popen``); both are
    injectable for tests. Playback is non-blocking: :meth:`play` starts
    the player in the background and returns immediately.

    Looping is done by *this* class (replaying the finished clip until
    the requested duration elapses), not by the player binary: in the
    wild, ``paplay`` has no ``-l`` and ``ffplay`` has no
    ``-stream_loop`` — passing those flags made both exit immediately,
    which the old ``sound_ok=True`` log line happily hid.

    The player's stderr is captured to *error_log* (a file path, by
    default ``~/.local/share/omnify/sound_last_error.log``) so a
    runtime failure — a missing codec, a dead audio server — is visible
    instead of silent. The file is truncated on every play.
    """

    def __init__(
        self,
        which: Callable[[str], Optional[str]] = shutil.which,
        runner: Callable[..., "subprocess.Popen"] = subprocess.Popen,
        error_log: Optional[str] = None,
    ) -> None:
        self._which = which
        self._runner = runner
        self._error_log = (
            error_log if error_log is not None
            else _error_log_path("sound_last_error.log")
        )
        self._active: Optional["subprocess.Popen"] = None
        self._generation = 0
        self._lock = threading.Lock()

    # -- capability -------------------------------------------------------
    def _find(self, names: Sequence[str]) -> Optional[str]:
        for name in names:
            if self._which(name):
                return name
        return None

    def _pick_player(self, path: str) -> Optional[str]:
        """The best installed player for *path*, or ``None``."""
        if path.lower().endswith(_COMPRESSED_EXTS):
            return self._find((_COMPRESSED_PLAYER, *_PCM_PLAYERS))
        return self._find(_PCM_PLAYERS)

    def available(self, path: str = "") -> bool:
        """``True`` when a player that can handle *path* is installed."""
        return self._pick_player(path) is not None

    # -- playback -----------------------------------------------------------
    def _player_args(self, player: str, path: str) -> List[str]:
        """The command line for one pass of *path* with *player*."""
        args: List[str] = [self._which(player)]  # type: ignore[list-item]
        if player == _COMPRESSED_PLAYER:
            # ffplay opens an SDL window by default — keep it headless.
            args += ["-nodisp", "-loglevel", "quiet", "-autoexit"]
        args.append(path)
        return args

    def play(self, path: str, duration: Optional[float] = None) -> bool:
        """Start playing *path* in the background (non-blocking).

        With *duration* (seconds, ``> 0``) the clip is replayed until that
        much time has elapsed; without it the file is played once. Returns
        ``True`` when playback was started; playing a new file stops the
        previous one. Never raises.
        """
        if not path or not os.path.exists(path):
            return False
        player = self._pick_player(path)
        if player is None:
            return False
        self.stop()
        with self._lock:
            self._generation += 1
            generation = self._generation
        proc = _spawn_capturing(
            self._runner, self._player_args(player, path), self._error_log
        )
        if proc is None:
            return False
        with self._lock:
            if generation != self._generation:
                # Another play()/stop() won the race while we were spawning.
                self._terminate(proc)
                return False
            self._active = proc
        if duration is not None and duration > 0:
            thread = threading.Thread(
                target=self._loop,
                args=(generation, player, path, float(duration)),
                name="omnify-sound-loop",
                daemon=True,
            )
            thread.start()
        else:
            # Reap the child so a one-shot clip never leaves a zombie.
            threading.Thread(
                target=self._reap, args=(generation, proc),
                name="omnify-sound-reap", daemon=True,
            ).start()
        return True

    def _current(self, generation: int) -> Optional["subprocess.Popen"]:
        with self._lock:
            if generation != self._generation:
                return None
            return self._active

    def _reap(self, generation: int, proc: "subprocess.Popen") -> None:
        """Wait for a one-shot clip to end and drop it from ``_active``."""
        try:
            proc.wait()
        except Exception:
            pass
        with self._lock:
            if self._active is proc and generation == self._generation:
                self._active = None

    def _loop(
        self, generation: int, player: str, path: str, duration: float
    ) -> None:
        """Replay the clip until *duration* elapses (or playback changes)."""
        deadline = time.monotonic() + duration
        proc = self._current(generation)
        while proc is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                proc.wait(timeout=min(remaining, 0.2))
            except subprocess.TimeoutExpired:
                continue
            except Exception:
                return
            # The clip ended on its own — replay it if we are still current
            # and the requested duration has not elapsed yet.
            if self._generation != generation or time.monotonic() >= deadline:
                return
            new_proc = _spawn_capturing(
                self._runner, self._player_args(player, path), self._error_log
            )
            if new_proc is None:
                return
            with self._lock:
                if generation != self._generation:
                    self._terminate(new_proc)
                    return
                self._active = new_proc
            proc = new_proc
        self._stop_generation(generation)

    def _stop_generation(self, generation: int) -> None:
        """Stop playback only if *generation* is still the current one."""
        with self._lock:
            if generation != self._generation:
                return
            self._generation += 1
            proc, self._active = self._active, None
        self._terminate(proc)

    @staticmethod
    def _terminate(proc: Optional["subprocess.Popen"]) -> None:
        if proc is None:
            return
        try:
            proc.terminate()
        except Exception:
            pass

    def stop(self) -> None:
        """Stop whatever :meth:`play` started (never raises)."""
        with self._lock:
            self._generation += 1
            proc, self._active = self._active, None
        self._terminate(proc)
        if proc is not None:
            try:
                proc.wait(timeout=0.5)
            except Exception:
                pass


class Notifier:
    """Desktop notifications via ``notify-send``.

    ``timeout_ms`` follows ``notify-send`` semantics: a non-negative
    value is the auto-close time in milliseconds, ``-1`` keeps the
    notification on screen until it is dismissed. A *replace_id* updates
    (or closes) an existing notification in place — that is how the
    "x time left" background notification stays a single, quiet entry
    instead of a stream of pop-ups.
    """

    APP_NAME = "Omnify"

    def __init__(
        self,
        which: Callable[[str], Optional[str]] = shutil.which,
        runner: Callable[..., "subprocess.Popen"] = subprocess.Popen,
        error_log: Optional[str] = None,
    ) -> None:
        self._which = which
        self._runner = runner
        self._error_log = (
            error_log if error_log is not None
            else _error_log_path("notify_last_error.log")
        )

    @property
    def available(self) -> bool:
        """``True`` when ``notify-send`` is on the PATH."""
        return self._which("notify-send") is not None

    def notify(
        self,
        title: str,
        body: str = "",
        *,
        urgency: str = "normal",
        timeout_ms: int = 5000,
        replace_id: Optional[int] = None,
        icon: Optional[str] = None,
    ) -> bool:
        """Show one desktop notification; ``True`` when it was sent."""
        path = self._which("notify-send")
        if path is None:
            return False
        args = [path, "--app-name", self.APP_NAME]
        if replace_id is not None:
            args += ["-r", str(int(replace_id))]
        if icon:
            args += ["-i", icon]
        if urgency in ("low", "critical"):
            args += ["-u", urgency]  # "normal" is the notify-send default
        args += ["-t", str(int(timeout_ms))]
        args += [title, body]
        proc = _spawn_capturing(self._runner, args, self._error_log)
        return proc is not None

    def dismiss(self, replace_id: int) -> bool:
        """Close the persistent notification *replace_id* (empty update)."""
        return self.notify("", "", replace_id=replace_id, timeout_ms=-1)
