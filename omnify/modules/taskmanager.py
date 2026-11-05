"""Task Manager module: a Windows-11-Task-Manager-style process monitor.

The model (:class:`TaskManager`) is pure Python with no GTK dependency.
Everything it reads goes through injectable readers (zero-arg callables
returning ``str``, or small typed helpers for per-path reads) plus an
injectable clock, so tests drive it with canned ``/proc`` strings, a fake
monotonic time and a fake ``kill_fn`` without touching the real ``/proc``
tree or spawning a subprocess.

The GUI (:class:`TaskManagerModule`) is a thin layer over the model: six
tabs (Processes, Performance, Services, Startup, Users, Details) refreshed
by a 1-second ``GLib`` ticker, a self-drawn cairo :class:`LineGraph`
widget (no plotting libraries), sortable columns, live search, and process
control (End task / Force end / End tree) behind confirmation dialogs.

Graceful degradation is a first-class rule: missing ``nvidia-smi``,
``systemctl``, ``loginctl``, ``nethogs``, a vanished ``/proc`` entry or a
``PermissionError`` never crashes the module — the affected cell shows
``"-"`` / ``"Not available"`` and an error lands in the status bar.
"""

from __future__ import annotations

import configparser
import io
import os
import pwd
import re
import signal
import subprocess
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..core.i18n import tr
from ..core.module_context import BaseModule
from .system import format_size

#: Clock ticks per second (SC_CLK_TCK on Linux) for /proc/[pid]/stat times.
CLK_TCK = 100
#: /proc/diskstats counts sectors of this size in bytes.
SECTOR_BYTES = 512
#: Filesystem types treated as "real" mounts (the rest are pseudo-filesystems).
_REAL_FSTYPES = frozenset(
    {
        "ext2", "ext3", "ext4", "btrfs", "xfs", "vfat", "exfat",
        "ntfs", "ntfs3", "f2fs", "zfs", "reiserfs", "jfs",
        "squashfs", "udf", "iso9660",
    }
)


# ---------------------------------------------------------------------------
# Default readers (the only place the real filesystem / subprocess is touched)
# ---------------------------------------------------------------------------

def _read_file(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _default_stat_reader() -> str:
    """Return the text of ``/proc/stat``."""
    return _read_file("/proc/stat")


def _default_meminfo_reader() -> str:
    """Return the text of ``/proc/meminfo``."""
    return _read_file("/proc/meminfo")


def _default_loadavg_reader() -> str:
    """Return the text of ``/proc/loadavg``."""
    return _read_file("/proc/loadavg")


def _default_cpuinfo_reader() -> str:
    """Return the text of ``/proc/cpuinfo``."""
    return _read_file("/proc/cpuinfo")


def _default_netdev_reader() -> str:
    """Return the text of ``/proc/net/dev``."""
    return _read_file("/proc/net/dev")


def _default_diskstats_reader() -> str:
    """Return the text of ``/proc/diskstats``."""
    return _read_file("/proc/diskstats")


def _default_mounts_reader() -> str:
    """Return the text of ``/proc/mounts``."""
    return _read_file("/proc/mounts")


def _default_block_devices() -> List[str]:
    """Whole-disk device names from ``/sys/block`` (no partitions)."""
    return sorted(os.listdir("/sys/block"))


def _default_pids() -> List[int]:
    """All numeric directories under ``/proc`` (the live PID list)."""
    pids = []
    for entry in os.listdir("/proc"):
        if entry.isdigit():
            pids.append(int(entry))
    return sorted(pids)


def _default_proc_file(pid: int, name: str) -> str:
    """Return the text of ``/proc/[pid]/<name>`` (raises on error)."""
    return _read_file(f"/proc/{pid}/{name}")


def _default_uid_name(uid: int) -> str:
    """Resolve a numeric UID to a login name (falls back to the number)."""
    try:
        return pwd.getpwuid(uid).pw_name
    except (KeyError, ImportError, OverflowError):
        return str(uid)


def _default_cpufreq_reader(core: int, name: str) -> str:
    """Return the text of a per-core cpufreq sysfs file (raises on error)."""
    return _read_file(f"/sys/devices/system/cpu/cpu{core}/cpufreq/{name}")


def _default_net_state(iface: str) -> Optional[str]:
    """Return the operstate (``"up"``/``"down"``/…) of *iface*, else None."""
    try:
        text = _read_file(f"/sys/class/net/{iface}/operstate")
    except OSError:
        return None
    return text.strip() or None


def _default_statvfs(path: str) -> Optional[Tuple[int, int, int]]:
    """Return ``(total, used, free)`` bytes for *path*, else None."""
    try:
        st = os.statvfs(path)
    except OSError:
        return None
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    return (total, total - free, free)


def _default_gpu_reader() -> str:
    """Return the CSV output of ``nvidia-smi`` for the first GPU.

    Raises on a nonzero exit code or timeout; :meth:`TaskManager.gpu_info`
    treats any such failure as "no GPU data".
    """
    result = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
            "--format=csv,noheader",
        ],
        capture_output=True,
        text=True,
        timeout=2,
    )
    if result.returncode != 0:
        raise RuntimeError(f"nvidia-smi exited with code {result.returncode}")
    return result.stdout


def _default_gpu_apps_reader() -> str:
    """Return per-process VRAM usage from ``nvidia-smi`` (CSV, no header).

    Raises on a nonzero exit code or timeout; :meth:`TaskManager.gpu_apps`
    treats any failure as "no per-process GPU data".
    """
    result = subprocess.run(
        [
            "nvidia-smi",
            "--query-compute-apps=pid,used_memory,process_name",
            "--format=csv,noheader",
        ],
        capture_output=True,
        text=True,
        timeout=3,
    )
    if result.returncode != 0:
        raise RuntimeError(f"nvidia-smi exited with code {result.returncode}")
    return result.stdout


def _default_nethogs_reader() -> str:
    """Best-effort per-process network via ``nethogs`` (may be absent).

    Runs ``nethogs -t -c 1`` (terse mode, one cycle). Any failure — missing
    binary, timeout, nonzero exit — yields an empty string, which the model
    interprets as "per-process network unavailable".
    """
    try:
        result = subprocess.run(
            ["nethogs", "-t", "-c", "1"],
            capture_output=True,
            text=True,
            timeout=6,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout or ""


def _run_systemctl(args: List[str]) -> str:
    """Run ``systemctl <args>``; any failure yields an empty string."""
    try:
        result = subprocess.run(
            ["systemctl"] + args, capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout or ""


def _default_services_reader() -> str:
    """Service unit table from systemctl (empty string when unavailable)."""
    return _run_systemctl(["list-units", "--type=service", "--no-legend", "--plain"])


def _default_service_pids_reader() -> str:
    """Per-unit ``Id=``/``MainPID=`` dump from systemctl (empty on failure)."""
    return _run_systemctl(["show", "--all", "--type=service"])


def _default_service_action(action: str, unit: str) -> None:
    """Run ``systemctl <action> <unit>``; raises on a nonzero exit code."""
    result = subprocess.run(
        ["systemctl", action, unit], capture_output=True, text=True, timeout=10
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(message or f"systemctl {action} failed")


def _default_sessions_reader() -> str:
    """Login sessions from ``loginctl`` (empty string when unavailable)."""
    try:
        result = subprocess.run(
            ["loginctl", "list-sessions", "--no-legend"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout or ""


def _default_terminate_session(session: str) -> None:
    """Run ``loginctl terminate-session <session>``; raises on failure."""
    result = subprocess.run(
        ["loginctl", "terminate-session", session],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(message or "loginctl terminate-session failed")


def _default_autostart_dir() -> str:
    """The XDG user autostart directory."""
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "autostart")


def _default_write_file(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


# ---------------------------------------------------------------------------
# Pure parsers (module-level so tests can target them directly)
# ---------------------------------------------------------------------------

def _cpu_line_values(fields: List[str]) -> Optional[Tuple[float, float]]:
    """Return ``(total, idle)`` jiffies from the numeric fields of a CPU line.

    ``idle`` includes iowait. Returns None when the line is too short or
    non-numeric.
    """
    if len(fields) < 5:
        return None
    try:
        values = [float(token) for token in fields]
    except ValueError:
        return None
    return sum(values), values[3] + values[4]


def parse_proc_stat(text: str) -> Optional[Dict[str, Any]]:
    """Parse one ``/proc/[pid]/stat`` line.

    The comm field may contain spaces and parentheses, so the split happens
    at the *last* ``)``. Returns a dict with pid, name, state, ppid, utime,
    stime, threads, start_ticks and rss_pages — or None on bad input.
    """
    line = text.strip()
    if not line or ")" not in line:
        return None
    head, _, rest_text = line.rpartition(")")
    try:
        pid = int(head.split()[0])
    except (ValueError, IndexError):
        return None
    name = head[head.index("(") + 1 :]
    rest = rest_text.split()
    if len(rest) < 22:
        return None
    try:
        return {
            "pid": pid,
            "name": name,
            "state": rest[0],
            "ppid": int(rest[1]),
            "utime": float(rest[11]),
            "stime": float(rest[12]),
            "threads": int(rest[17]),
            "start_ticks": float(rest[19]),
            "rss_pages": float(rest[21]),
        }
    except (ValueError, IndexError):
        return None


def parse_status_fields(text: str) -> Dict[str, float]:
    """Parse numeric ``Name: value`` fields of ``/proc/[pid]/status``.

    Non-numeric values are skipped; missing text yields an empty dict.
    """
    out: Dict[str, float] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, rest = line.partition(":")
        tokens = rest.split()
        if not tokens:
            continue
        try:
            out[key.strip()] = float(tokens[0])
        except ValueError:
            continue
    return out


def parse_proc_io(text: str) -> Dict[str, float]:
    """Parse numeric ``key: value`` fields of ``/proc/[pid]/io``."""
    out: Dict[str, float] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, rest = line.partition(":")
        tokens = rest.split()
        if not tokens:
            continue
        try:
            out[key.strip()] = float(tokens[0])
        except ValueError:
            continue
    return out


def parse_net_dev(text: str) -> Dict[str, Dict[str, float]]:
    """Parse ``/proc/net/dev`` into ``{iface: {"rx_bytes", "tx_bytes"}}``.

    The two header lines are skipped naturally (they hold no 9+ numeric
    counters). Malformed interface lines are skipped.
    """
    out: Dict[str, Dict[str, float]] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        iface, rest = line.split(":", 1)
        iface = iface.strip()
        fields = rest.split()
        if len(fields) < 9:
            continue
        try:
            rx = float(fields[0])
            tx = float(fields[8])
        except ValueError:
            continue
        out[iface] = {"rx_bytes": rx, "tx_bytes": tx}
    return out


def parse_diskstats(text: str) -> Dict[str, Dict[str, float]]:
    """Parse ``/proc/diskstats`` into ``{name: {"rsectors", "wsectors"}}``.

    Field layout: major minor name reads rmerged rsectors rtime
    writes wmerged wsectors … (so the name is index 2, read sectors 5 and
    write sectors 9).
    """
    out: Dict[str, Dict[str, float]] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 10:
            continue
        try:
            out[parts[2]] = {
                "rsectors": float(parts[5]),
                "wsectors": float(parts[9]),
            }
        except ValueError:
            continue
    return out


def parse_btime(text: str) -> Optional[float]:
    """Return the boot epoch seconds from a ``/proc/stat`` text, else None."""
    for line in text.splitlines():
        if line.startswith("btime "):
            try:
                return float(line.split()[1])
            except (IndexError, ValueError):
                return None
    return None


def parse_loadavg(text: str) -> Optional[Tuple[float, float, float]]:
    """Return the 1/5/15-minute load averages, else None."""
    parts = text.split()
    if len(parts) < 3:
        return None
    try:
        return float(parts[0]), float(parts[1]), float(parts[2])
    except ValueError:
        return None


def parse_cpuinfo(text: str) -> Dict[str, Any]:
    """Return ``{"model": str, "cores": int}`` from ``/proc/cpuinfo``."""
    model = ""
    cores = 0
    for line in text.splitlines():
        if line.startswith("processor"):
            if ":" in line:
                cores += 1
        elif line.startswith("model name") and not model:
            _, _, rest = line.partition(":")
            model = rest.strip()
    return {"model": model, "cores": cores}


def parse_gpu_apps(text: str) -> Optional[Dict[int, Dict[str, Any]]]:
    """Parse ``nvidia-smi --query-compute-apps`` CSV output.

    Each valid line is ``pid, N MiB, process_name``. Strict like the GPU
    reader: a non-empty malformed line yields None (whole sample dropped);
    empty output yields None.
    """
    apps: Dict[int, Dict[str, Any]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        fields = line.split(", ")
        if len(fields) != 3:
            return None
        try:
            pid = int(fields[0].strip())
            vram = float(fields[1].split()[0])
        except (ValueError, IndexError):
            return None
        apps[pid] = {"vram_mib": vram, "name": fields[2].strip()}
    return apps if apps else None


_NETHOGS_PID = re.compile(r"^\s*(\d+)\s*\(")
_NETHOGS_RX = re.compile(r"^RX \(KiB/s\):\s*([0-9.]+)")
_NETHOGS_TX = re.compile(r"^TX \(KiB/s\):\s*([0-9.]+)")


def parse_nethogs(text: str) -> Dict[int, Tuple[float, float]]:
    """Parse terse ``nethogs -t`` output into ``{pid: (rx, tx)}`` KiB/s.

    Best-effort: anything that does not match is skipped, so garbage input
    yields an empty dict instead of an error.
    """
    out: Dict[int, Tuple[float, float]] = {}
    pending: Dict[int, float] = {}
    current: Optional[int] = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        m = _NETHOGS_PID.match(stripped)
        if m:
            current = int(m.group(1))
            continue
        if current is None:
            continue
        m = _NETHOGS_RX.match(stripped)
        if m:
            pending[current] = float(m.group(1))
            continue
        m = _NETHOGS_TX.match(stripped)
        if m:
            out[current] = (pending.get(current, 0.0), float(m.group(1)))
    return out


def parse_services(text: str) -> List[Dict[str, str]]:
    """Parse ``systemctl list-units --type=service --no-legend --plain``.

    Columns: UNIT LOAD ACTIVE SUB DESCRIPTION…; short or empty lines are
    skipped, so a missing systemctl (empty string) yields ``[]``.
    """
    out: List[Dict[str, str]] = []
    for line in text.splitlines():
        tokens = line.split()
        if len(tokens) < 4:
            continue
        out.append(
            {
                "unit": tokens[0],
                "load": tokens[1],
                "active": tokens[2],
                "sub": tokens[3],
                "description": " ".join(tokens[4:]),
            }
        )
    return out


def parse_service_pids(text: str) -> Dict[str, int]:
    """Parse a ``systemctl show`` dump into ``{unit: MainPID}``.

    ``MainPID=0`` (no main process) is dropped.
    """
    out: Dict[str, int] = {}
    current: Optional[str] = None
    for line in text.splitlines():
        if line.startswith("Id="):
            current = line[3:]
        elif line.startswith("MainPID=") and current is not None:
            try:
                pid = int(line[8:])
            except ValueError:
                continue
            if pid > 0:
                out[current] = pid
    return out


def parse_sessions(text: str) -> List[Dict[str, Any]]:
    """Parse ``loginctl list-sessions --no-legend``.

    Columns: SESSION UID USER SEAT TTY STATE …; lines without a numeric UID
    are skipped.
    """
    out: List[Dict[str, Any]] = []
    for line in text.splitlines():
        tokens = line.split()
        if len(tokens) < 3:
            continue
        try:
            uid = int(tokens[1])
        except ValueError:
            continue
        out.append(
            {
                "session": tokens[0],
                "uid": uid,
                "user": tokens[2],
                "state": tokens[5] if len(tokens) > 5 else "",
            }
        )
    return out


def parse_desktop(text: str, path: str) -> Optional["StartupEntry"]:
    """Parse a freedesktop ``.desktop`` file into a :class:`StartupEntry`.

    Files without a ``[Desktop Entry]`` section or that cannot be parsed at
    all are skipped (None) rather than raising.
    """
    try:
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        parser.optionxform = str  # keep key case (X-GNOME-Autostart-enabled)
        parser.read_string(text)
        if "Desktop Entry" not in parser:
            return None
        section = parser["Desktop Entry"]
        name = section.get("Name", "").strip() or os.path.basename(path)[:-8]
        exec_ = section.get("Exec", "").strip()
        enabled = section.get("X-GNOME-Autostart-enabled", "true").strip()
        return StartupEntry(
            path=path,
            name=name,
            exec=exec_,
            enabled=enabled.lower() != "false",
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def format_speed(kib_per_s: Optional[float]) -> str:
    """Format a KiB/s speed (``"512.0 KiB/s"`` / ``"2.0 MiB/s"`` / ``"-"``)."""
    if kib_per_s is None:
        return "-"
    value = max(0.0, float(kib_per_s))
    if value >= 1024.0:
        return f"{value / 1024.0:.1f} MiB/s"
    return f"{value:.1f} KiB/s"


def format_bytes(value: Optional[float]) -> str:
    """Format a byte count with binary units, or ``"-"`` for None."""
    if value is None:
        return "-"
    v = max(0.0, float(value))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if v < 1024.0 or unit == "TiB":
            if unit == "B":
                return f"{int(v)} B"
            return f"{v:.1f} {unit}"
        v /= 1024.0
    return f"{v:.1f} TiB"


def format_uptime(seconds: Optional[float]) -> str:
    """Format an uptime in ``45s`` / ``12m 5s`` / ``1h 2m`` / ``1d 3h`` form."""
    if seconds is None:
        return "-"
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h {int(seconds % 3600 // 60)}m"
    return f"{int(seconds // 86400)}d {int(seconds % 86400 // 3600)}h"


def format_hz(hz: Optional[float]) -> str:
    """Format a frequency in Hz as ``"2400 MHz"``, or ``"-"`` for None."""
    if hz is None:
        return "-"
    return f"{hz / 1e6:.0f} MHz"


def sorted_by(items: List[Any], key_fn: Callable[[Any], Any], reverse: bool = False) -> List[Any]:
    """Stable sort by *key_fn*; ``None`` keys always sink to the end.

    Two-pass: non-None items are sorted by value (optionally reversed),
    None items keep their relative order and are appended at the end —
    regardless of direction.
    """
    non_null = [item for item in items if key_fn(item) is not None]
    nulls = [item for item in items if key_fn(item) is None]
    non_null.sort(key=key_fn, reverse=reverse)
    return non_null + nulls


def filter_processes(infos: List["ProcessInfo"], query: str) -> List["ProcessInfo"]:
    """Case-insensitive live-search filter over name and command line.

    A blank query (empty or whitespace-only) matches everything. The
    query may already be lower-cased (as the GUI stores it); normalising
    is idempotent.
    """
    q = (query or "").strip().lower()
    if not q:
        return list(infos)
    return [
        info
        for info in infos
        if q in (info.name or "").lower() or q in (info.cmdline or "").lower()
    ]


def _iter_children(container: "Any"):
    """Yield the direct children of a GTK container.

    This GTK build's containers have no ``get_children()``, so walk the
    sibling chain via ``get_first_child`` / ``get_next_sibling``. Callers
    that remove children mid-loop must snapshot first: ``list(...)``.
    """
    child = container.get_first_child()
    while child is not None:
        yield child
        child = child.get_next_sibling()


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

@dataclass
class ProcessInfo:
    """One row of the process table (all readers already applied)."""

    pid: int
    name: str
    cmdline: str
    ppid: int
    user: str
    uid: int
    state: str
    threads: int
    rss_kb: float
    cpu_percent: float
    disk_read_kib_s: Optional[float]
    disk_write_kib_s: Optional[float]
    disk_read_total_kib: float
    disk_write_total_kib: float
    net_rx_kib_s: Optional[float]
    net_tx_kib_s: Optional[float]
    net_rx_total_kib: float
    net_tx_total_kib: float
    gpu_vram_mib: Optional[float]
    uptime_seconds: Optional[float]

    @property
    def disk_speed(self) -> Optional[float]:
        """Combined R/W speed in KiB/s, or None when no io data was read."""
        if self.disk_read_kib_s is None and self.disk_write_kib_s is None:
            return None
        return (self.disk_read_kib_s or 0.0) + (self.disk_write_kib_s or 0.0)

    @property
    def net_speed(self) -> Optional[float]:
        """Combined RX/TX speed in KiB/s, or None when unknown (no nethogs)."""
        if self.net_rx_kib_s is None and self.net_tx_kib_s is None:
            return None
        return (self.net_rx_kib_s or 0.0) + (self.net_tx_kib_s or 0.0)


@dataclass
class StartupEntry:
    """One autostart ``.desktop`` file."""

    path: str
    name: str
    exec: str
    enabled: bool


class RingBuffer:
    """Fixed-capacity buffer of floats; the oldest value is dropped on overflow.

    This is the storage behind :class:`LineGraph` and is kept free of GTK so
    it can be tested headlessly.
    """

    def __init__(self, capacity: int = 90) -> None:
        self.capacity = max(1, int(capacity))
        self._items: "deque" = deque(maxlen=self.capacity)

    def add(self, value: float) -> None:
        """Append one sample, evicting the oldest when full."""
        self._items.append(float(value))

    def add_many(self, values: List[float]) -> None:
        """Append several samples."""
        for value in values:
            self.add(value)

    def values(self) -> List[float]:
        """All stored samples, oldest first."""
        return list(self._items)

    def __len__(self) -> int:
        return len(self._items)


class TaskManager:
    """Pure task-manager model driven by injectable readers and a clock.

    Every source of truth is an injectable callable (see ``__init__``), so
    tests never touch the real ``/proc`` tree or subprocesses. All accessors
    degrade gracefully: bad input yields neutral values (``None`` / ``0.0``
    / ``[]``) instead of raising. :meth:`kill_pid` and :meth:`kill_tree`
    deliberately *do* raise (``PermissionError`` included) so the GUI can
    report the failure in the status bar.
    """

    def __init__(
        self,
        time_fn: Callable[[], float] = time.monotonic,
        wall_time_fn: Callable[[], float] = time.time,
        stat_reader: Callable[[], str] = _default_stat_reader,
        cpuinfo_reader: Callable[[], str] = _default_cpuinfo_reader,
        meminfo_reader: Callable[[], str] = _default_meminfo_reader,
        loadavg_reader: Callable[[], str] = _default_loadavg_reader,
        netdev_reader: Callable[[], str] = _default_netdev_reader,
        diskstats_reader: Callable[[], str] = _default_diskstats_reader,
        mounts_reader: Callable[[], str] = _default_mounts_reader,
        block_devices_fn: Callable[[], List[str]] = _default_block_devices,
        net_state_fn: Callable[[str], Optional[str]] = _default_net_state,
        statvfs_fn: Callable[[str], Optional[Tuple[int, int, int]]] = _default_statvfs,
        pids_fn: Callable[[], List[int]] = _default_pids,
        proc_file_fn: Callable[[int, str], str] = _default_proc_file,
        uid_name_fn: Callable[[int], str] = _default_uid_name,
        cpufreq_reader: Callable[[int, str], str] = _default_cpufreq_reader,
        gpu_reader: Callable[[], str] = _default_gpu_reader,
        gpu_apps_reader: Callable[[], str] = _default_gpu_apps_reader,
        nethogs_reader: Callable[[], str] = _default_nethogs_reader,
        services_reader: Callable[[], str] = _default_services_reader,
        service_pids_reader: Callable[[], str] = _default_service_pids_reader,
        service_action_fn: Callable[[str, str], None] = _default_service_action,
        sessions_reader: Callable[[], str] = _default_sessions_reader,
        terminate_session_fn: Callable[[str], None] = _default_terminate_session,
        autostart_dir_fn: Callable[[], str] = _default_autostart_dir,
        autostart_list_fn: Callable[[str], List[str]] = os.listdir,
        read_file_fn: Callable[[str], str] = _read_file,
        write_file_fn: Callable[[str, str], None] = _default_write_file,
        kill_fn: Callable[[int, int], None] = os.kill,
    ) -> None:
        self._time_fn = time_fn
        self._wall_time_fn = wall_time_fn
        self._stat_reader = stat_reader
        self._cpuinfo_reader = cpuinfo_reader
        self._meminfo_reader = meminfo_reader
        self._loadavg_reader = loadavg_reader
        self._netdev_reader = netdev_reader
        self._diskstats_reader = diskstats_reader
        self._mounts_reader = mounts_reader
        self._block_devices_fn = block_devices_fn
        self._net_state_fn = net_state_fn
        self._statvfs_fn = statvfs_fn
        self._pids_fn = pids_fn
        self._proc_file_fn = proc_file_fn
        self._uid_name_fn = uid_name_fn
        self._cpufreq_reader = cpufreq_reader
        self._gpu_reader = gpu_reader
        self._gpu_apps_reader = gpu_apps_reader
        self._nethogs_reader = nethogs_reader
        self._services_reader = services_reader
        self._service_pids_reader = service_pids_reader
        self._service_action_fn = service_action_fn
        self._sessions_reader = sessions_reader
        self._terminate_session_fn = terminate_session_fn
        self._autostart_dir_fn = autostart_dir_fn
        self._autostart_list_fn = autostart_list_fn
        self._read_file_fn = read_file_fn
        self._write_file_fn = write_file_fn
        self._kill_fn = kill_fn

        #: (total, idle) of the last good aggregate CPU sample, or None.
        self._prev_cpu: Optional[Tuple[float, float]] = None
        self._prev_core_cpu: Dict[int, Tuple[float, float]] = {}
        # pid -> (utime+stime ticks, sample time)
        self._proc_cpu_cache: Dict[int, Tuple[float, float]] = {}
        # pid -> (read_bytes, write_bytes, sample time)
        self._io_cache: Dict[int, Tuple[float, float, float]] = {}
        # pid -> (rx total KiB, tx total KiB, sample time)
        self._net_totals: Dict[int, Tuple[float, float, float]] = {}
        # iface -> (rx bytes, tx bytes, sample time)
        self._net_cache: Dict[str, Tuple[float, float, float]] = {}
        # device -> (rsectors, wsectors, sample time)
        self._disk_cache: Dict[str, Tuple[float, float, float]] = {}
        #: Last scan result (used by :meth:`top_processes` and :meth:`sessions`).
        self._last_procs: List[ProcessInfo] = []

    # -- small helpers ------------------------------------------------------
    def _safe_read_text(self, reader: Callable[[], str]) -> str:
        try:
            return reader()
        except Exception:
            return ""

    def _safe_proc(self, pid: int, name: str) -> Optional[str]:
        try:
            return self._proc_file_fn(pid, name)
        except Exception:
            return None

    # -- CPU -----------------------------------------------------------------
    def cpu_percent(self) -> float:
        """Aggregate CPU utilization in ``[0, 100]``.

        Same semantics as the system module: the first call primes the cache
        and returns 0.0; a missing/short/non-numeric ``cpu`` line returns
        0.0 with the cache unchanged.
        """
        try:
            text = self._stat_reader()
        except Exception:
            return 0.0
        for line in text.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] == "cpu":
                values = _cpu_line_values(parts[1:])
                if values is None:
                    return 0.0
                total, idle = values
                prev = self._prev_cpu
                self._prev_cpu = (total, idle)
                if prev is None:
                    return 0.0
                delta_total = total - prev[0]
                if delta_total <= 0:
                    return 0.0
                percent = 100.0 * (1.0 - (idle - prev[1]) / delta_total)
                return max(0.0, min(100.0, percent))
        return 0.0

    def per_core_percents(self) -> Dict[int, float]:
        """Per-core utilization in ``[0, 100]`` (``{core_index: percent}``).

        Cores seen for the first time report 0.0; vanished cores are
        dropped from the cache.
        """
        try:
            text = self._stat_reader()
        except Exception:
            return {}
        found: Dict[int, Tuple[float, float]] = {}
        for line in text.splitlines():
            parts = line.split()
            if len(parts) < 2 or not parts[0].startswith("cpu"):
                continue
            core_name = parts[0][3:]
            if not core_name.isdigit():
                continue
            values = _cpu_line_values(parts[1:])
            if values is None:
                continue
            found[int(core_name)] = values
        result: Dict[int, float] = {}
        for core, (total, idle) in found.items():
            prev = self._prev_core_cpu.get(core)
            self._prev_core_cpu[core] = (total, idle)
            if prev is None:
                result[core] = 0.0
                continue
            delta_total = total - prev[0]
            if delta_total <= 0:
                result[core] = 0.0
            else:
                percent = 100.0 * (1.0 - (idle - prev[1]) / delta_total)
                result[core] = max(0.0, min(100.0, percent))
        for core in [c for c in self._prev_core_cpu if c not in found]:
            del self._prev_core_cpu[core]
        return result

    def cpuinfo(self) -> Dict[str, Any]:
        """``{"model": str, "cores": int}`` from ``/proc/cpuinfo`` (safe)."""
        try:
            return parse_cpuinfo(self._cpuinfo_reader())
        except Exception:
            return {"model": "", "cores": 0}

    def core_count(self) -> int:
        """Number of logical cores (at least 1, used for CPU% normalization)."""
        return self.cpuinfo()["cores"] or 1

    def loadavg(self) -> Optional[Tuple[float, float, float]]:
        """The 1/5/15-minute load averages, or None when unavailable."""
        return parse_loadavg(self._safe_read_text(self._loadavg_reader))

    def cpu_frequencies(self) -> List[Dict[str, Optional[float]]]:
        """``[{"cur", "min", "max"}]`` in Hz per core (None when absent)."""
        out: List[Dict[str, Optional[float]]] = []
        for core in range(self.core_count()):
            entry: Dict[str, Optional[float]] = {}
            for key in ("cur", "min", "max"):
                try:
                    entry[key] = float(self._cpufreq_reader(core, f"scaling_{key}_freq").strip())
                except Exception:
                    entry[key] = None
            out.append(entry)
        return out

    # -- RAM -----------------------------------------------------------------
    def ram_info(self) -> Optional[Dict[str, float]]:
        """Memory + swap usage parsed from ``/proc/meminfo`` (kB).

        Returns None when ``MemTotal``/``MemAvailable`` are missing.
        """
        total = self._meminfo_field("MemTotal:")
        available = self._meminfo_field("MemAvailable:")
        if total is None or available is None:
            return None
        swap_total = self._meminfo_field("SwapTotal:") or 0.0
        swap_free = self._meminfo_field("SwapFree:") or 0.0
        used = total - available
        swap_used = max(0.0, swap_total - swap_free)
        return {
            "total_kb": total,
            "available_kb": available,
            "used_kb": used,
            "percent": 100.0 * used / total if total > 0 else 0.0,
            "swap_total_kb": swap_total,
            "swap_used_kb": swap_used,
            "swap_percent": 100.0 * swap_used / swap_total if swap_total > 0 else 0.0,
        }

    def _meminfo_field(self, label: str) -> Optional[float]:
        """Return the numeric value (kB) after *label* in meminfo, else None."""
        try:
            text = self._meminfo_reader()
        except Exception:
            return None
        for line in text.splitlines():
            if line.startswith(label):
                tokens = line.split()
                if len(tokens) >= 2:
                    try:
                        return float(tokens[1])
                    except ValueError:
                        return None
        return None

    # -- GPU -----------------------------------------------------------------
    def gpu_info(self) -> Optional[Dict[str, Any]]:
        """Overall GPU info from ``nvidia-smi``; None on any error."""
        try:
            text = self._gpu_reader()
        except Exception:
            return None
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            fields = line.split(", ")
            if len(fields) != 5:
                return None
            try:
                utilization = float(fields[1].split()[0])
                memory_used = float(fields[2].split()[0])
                memory_total = float(fields[3].split()[0])
                temperature = float(fields[4].split()[0])
            except (ValueError, IndexError):
                return None
            return {
                "name": fields[0],
                "utilization": utilization,
                "memory_used_mib": memory_used,
                "memory_total_mib": memory_total,
                "temperature_c": temperature,
            }
        return None

    def gpu_apps(self) -> Optional[Dict[int, Dict[str, Any]]]:
        """Per-process VRAM from ``nvidia-smi``; None on error/empty output."""
        try:
            text = self._gpu_apps_reader()
        except Exception:
            return None
        return parse_gpu_apps(text)

    # -- Processes -------------------------------------------------------------
    def scan_processes(self) -> List[ProcessInfo]:
        """One full scan of ``/proc`` → a list of :class:`ProcessInfo`.

        Processes that vanish between listing and reading are skipped; dead
        entries are pruned from the internal delta caches. Per-process CPU%
        is the delta of utime+stime normalized by core count; disk speed is
        the delta of read_bytes/write_bytes; per-process network comes from
        ``nethogs`` when available (totals accumulate since first sighting).
        """
        try:
            pids = self._pids_fn()
        except Exception:
            pids = []
        now = self._time_fn()
        try:
            wall = self._wall_time_fn()
        except Exception:
            wall = None
        btime = parse_btime(self._safe_read_text(self._stat_reader))
        cores = self.core_count()
        gpu_apps = self.gpu_apps() or {}
        nethogs = parse_nethogs(self._safe_read_text(self._nethogs_reader))

        infos: List[ProcessInfo] = []
        seen = set()
        for pid in pids:
            try:
                stat_text = self._proc_file_fn(pid, "stat")
            except Exception:
                continue
            parsed = parse_proc_stat(stat_text)
            if parsed is None:
                continue
            status_fields = parse_status_fields(self._safe_proc(pid, "status") or "")
            io_fields = parse_proc_io(self._safe_proc(pid, "io") or "")
            comm = self._safe_proc(pid, "comm") or ""
            comm_name = comm.strip().splitlines()[0] if comm.strip() else parsed["name"]
            cmdline = (self._safe_proc(pid, "cmdline") or "").replace("\x00", " ").strip()

            uid = int(status_fields.get("Uid", 0.0) or 0)
            rss_kb = status_fields.get("VmRSS", 0.0) or 0.0

            ticks = parsed["utime"] + parsed["stime"]
            cpu_percent = self._process_cpu(pid, ticks, now, cores)

            read_bytes = io_fields.get("read_bytes")
            write_bytes = io_fields.get("write_bytes")
            disk_read, disk_write = self._process_disk(pid, read_bytes, write_bytes, now)

            net_speeds = nethogs.get(pid)
            net_rx, net_tx, net_rx_total, net_tx_total = self._process_net(pid, net_speeds, now)

            vram_entry = gpu_apps.get(pid)
            uptime = None
            if wall is not None and btime is not None:
                uptime = wall - btime - parsed["start_ticks"] / CLK_TCK
                if uptime < 0:
                    uptime = 0.0

            infos.append(
                ProcessInfo(
                    pid=pid,
                    name=comm_name,
                    cmdline=cmdline,
                    ppid=parsed["ppid"],
                    user=self._uid_name_fn(uid),
                    uid=uid,
                    state=parsed["state"],
                    threads=parsed["threads"],
                    rss_kb=rss_kb,
                    cpu_percent=cpu_percent,
                    disk_read_kib_s=disk_read,
                    disk_write_kib_s=disk_write,
                    disk_read_total_kib=(read_bytes or 0.0) / 1024.0,
                    disk_write_total_kib=(write_bytes or 0.0) / 1024.0,
                    net_rx_kib_s=net_rx,
                    net_tx_kib_s=net_tx,
                    net_rx_total_kib=net_rx_total,
                    net_tx_total_kib=net_tx_total,
                    gpu_vram_mib=vram_entry["vram_mib"] if vram_entry else None,
                    uptime_seconds=uptime,
                )
            )
            seen.add(pid)

        for cache in (self._proc_cpu_cache, self._io_cache, self._net_totals):
            for key in [k for k in cache if k not in seen]:
                del cache[key]
        self._last_procs = infos
        return infos

    def _process_cpu(
        self, pid: int, ticks: float, now: float, cores: int
    ) -> float:
        """Delta-based per-process CPU% (0.0 on the first sighting)."""
        prev = self._proc_cpu_cache.get(pid)
        self._proc_cpu_cache[pid] = (ticks, now)
        if prev is None:
            return 0.0
        delta_t = now - prev[1]
        if delta_t <= 0:
            return 0.0
        percent = 100.0 * ((ticks - prev[0]) / CLK_TCK) / delta_t / cores
        return max(0.0, min(100.0, percent))

    def _process_disk(
        self, pid: int, read_bytes: Optional[float], write_bytes: Optional[float], now: float
    ) -> Tuple[Optional[float], Optional[float]]:
        """Per-process disk R/W speed in KiB/s from ``/proc/[pid]/io`` deltas."""
        read_s: Optional[float] = None
        write_s: Optional[float] = None
        prev = self._io_cache.get(pid)
        if read_bytes is not None and write_bytes is not None:
            if prev is not None:
                delta_t = now - prev[2]
                if delta_t > 0:
                    read_s = max(0.0, (read_bytes - prev[0])) / 1024.0 / delta_t
                    write_s = max(0.0, (write_bytes - prev[1])) / 1024.0 / delta_t
            self._io_cache[pid] = (read_bytes, write_bytes, now)
        return read_s, write_s

    def _process_net(
        self, pid: int, speeds: Optional[Tuple[float, float]], now: float
    ) -> Tuple[Optional[float], Optional[float], float, float]:
        """Per-process network (nethogs) with totals accumulated since first sight.

        Returns ``(rx_kib_s, tx_kib_s, rx_total_kib, tx_total_kib)``.
        """
        prev = self._net_totals.get(pid)
        rx_total = prev[0] if prev else 0.0
        tx_total = prev[1] if prev else 0.0
        if speeds is None:
            return None, None, rx_total, tx_total
        rx_s, tx_s = speeds
        if prev is not None:
            delta_t = now - prev[2]
            if delta_t > 0:
                rx_total += rx_s * delta_t
                tx_total += tx_s * delta_t
        self._net_totals[pid] = (rx_total, tx_total, now)
        return rx_s, tx_s, rx_total, tx_total

    def top_processes(self, key: str, n: int = 10) -> List[ProcessInfo]:
        """Top-*n* processes from the last scan by ``"cpu"``, ``"rss"`` or ``"vram"``."""
        if key == "cpu":
            ordered = sorted_by(self._last_procs, lambda p: p.cpu_percent, reverse=True)
        elif key == "rss":
            ordered = sorted_by(self._last_procs, lambda p: p.rss_kb, reverse=True)
        else:
            ordered = sorted_by(self._last_procs, lambda p: p.gpu_vram_mib, reverse=True)
        return ordered[:n]

    # -- Kill ------------------------------------------------------------------
    def kill_pid(self, pid: int, force: bool = False) -> None:
        """Send SIGTERM to *pid* (SIGKILL when *force*).

        Propagates ``PermissionError``/OSError — the GUI reports them.
        """
        self._kill_fn(pid, signal.SIGKILL if force else signal.SIGTERM)

    def children_of(self, pid: int) -> List[int]:
        """Direct children of *pid* from a fresh ``/proc`` scan (sorted)."""
        try:
            pids = self._pids_fn()
        except Exception:
            return []
        children: List[int] = []
        for candidate in pids:
            if candidate == pid:
                continue
            try:
                parsed = parse_proc_stat(self._proc_file_fn(candidate, "stat"))
            except Exception:
                continue
            if parsed is not None and parsed["ppid"] == pid:
                children.append(candidate)
        return sorted(children)

    def kill_tree(self, pid: int, force: bool = False) -> int:
        """Kill *pid* and all descendants — children first, then the parent.

        Returns the number of kill attempts; propagates errors mid-way.
        """
        order: List[int] = []

        def walk(node: int) -> None:
            for child in self.children_of(node):
                walk(child)
            order.append(node)

        walk(pid)
        for node in order:
            self.kill_pid(node, force)
        return len(order)

    # -- Network -----------------------------------------------------------------
    def network(self) -> Dict[str, Dict[str, Any]]:
        """Per-interface network counters and speeds from ``/proc/net/dev``."""
        raw = parse_net_dev(self._safe_read_text(self._netdev_reader))
        now = self._time_fn()
        out: Dict[str, Dict[str, Any]] = {}
        for iface, vals in raw.items():
            rx, tx = vals["rx_bytes"], vals["tx_bytes"]
            rx_s: Optional[float] = None
            tx_s: Optional[float] = None
            prev = self._net_cache.get(iface)
            if prev is not None:
                delta_t = now - prev[2]
                if delta_t > 0:
                    rx_s = max(0.0, (rx - prev[0])) / 1024.0 / delta_t
                    tx_s = max(0.0, (tx - prev[1])) / 1024.0 / delta_t
            self._net_cache[iface] = (rx, tx, now)
            out[iface] = {
                "rx_bytes": rx,
                "tx_bytes": tx,
                "rx_kib_s": rx_s,
                "tx_kib_s": tx_s,
                "state": self._net_state_fn(iface),
            }
        return out

    # -- Disks / mounts -----------------------------------------------------------
    def disks(self) -> List[Dict[str, Any]]:
        """Whole-disk rows (``/sys/block`` minus loop*/ram*) with R/W speed.

        Size comes from ``statvfs`` on the first mount point of ``/dev/<name>``
        when one exists; otherwise the size fields are None.
        """
        stats = parse_diskstats(self._safe_read_text(self._diskstats_reader))
        try:
            names = self._block_devices_fn()
        except Exception:
            names = []
        mounts_text = self._safe_read_text(self._mounts_reader)
        now = self._time_fn()
        out: List[Dict[str, Any]] = []
        for name in names:
            if name.startswith(("loop", "ram", "zram")):
                continue
            entry: Dict[str, Any] = {
                "name": name,
                "read_kib_s": None,
                "write_kib_s": None,
                "total_bytes": None,
                "used_bytes": None,
                "free_bytes": None,
            }
            cur = stats.get(name)
            if cur is not None:
                prev = self._disk_cache.get(name)
                if prev is not None:
                    delta_t = now - prev[2]
                    if delta_t > 0:
                        entry["read_kib_s"] = max(
                            0.0, (cur["rsectors"] - prev[0]) * SECTOR_BYTES / 1024.0 / delta_t
                        )
                        entry["write_kib_s"] = max(
                            0.0, (cur["wsectors"] - prev[1]) * SECTOR_BYTES / 1024.0 / delta_t
                        )
                self._disk_cache[name] = (cur["rsectors"], cur["wsectors"], now)
            mountpoint = self._mount_for_device(name, mounts_text)
            if mountpoint:
                st = self._statvfs_fn(mountpoint)
                if st is not None:
                    entry["total_bytes"], entry["used_bytes"], entry["free_bytes"] = st
            out.append(entry)
        return out

    @staticmethod
    def _mount_for_device(name: str, mounts_text: str) -> Optional[str]:
        """First mount point whose device is ``/dev/<name>``, else None."""
        dev = f"/dev/{name}"
        for line in mounts_text.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] == dev:
                return parts[1]
        return None

    def mounts(self) -> List[Dict[str, Any]]:
        """Real-filesystem mount rows from ``/proc/mounts`` with usage.

        Pseudo-filesystems are skipped; a mount whose ``statvfs`` fails is
        dropped. Unique by mount point.
        """
        out: List[Dict[str, Any]] = []
        seen = set()
        for line in self._safe_read_text(self._mounts_reader).splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue
            dev, mountpoint, fstype = parts[0], parts[1], parts[2]
            if fstype not in _REAL_FSTYPES or mountpoint in seen:
                continue
            st = self._statvfs_fn(mountpoint)
            if st is None:
                continue
            seen.add(mountpoint)
            total, used, free = st
            out.append(
                {
                    "mount": mountpoint,
                    "device": dev,
                    "fstype": fstype,
                    "total_bytes": total,
                    "used_bytes": used,
                    "free_bytes": free,
                    "percent": 100.0 * used / total if total > 0 else 0.0,
                }
            )
        return out

    # -- Services / sessions / startup ---------------------------------------------
    def services(self) -> List[Dict[str, Any]]:
        """Service units from systemctl (with MainPID when available).

        An empty result means "systemd not available" in the GUI.
        """
        pids = parse_service_pids(self._safe_read_text(self._service_pids_reader))
        out: List[Dict[str, Any]] = []
        for svc in parse_services(self._safe_read_text(self._services_reader)):
            svc = dict(svc)
            svc["pid"] = pids.get(svc["unit"])
            out.append(svc)
        return out

    def service_action(self, action: str, unit: str) -> None:
        """Run ``systemctl <action> <unit>``; raises on failure."""
        self._service_action_fn(action, unit)

    def sessions(self) -> List[Dict[str, Any]]:
        """Login sessions with CPU% and RAM aggregated from the last scan."""
        per_uid: Dict[int, Dict[str, float]] = {}
        for proc in self._last_procs:
            agg = per_uid.setdefault(proc.uid, {"cpu": 0.0, "rss_kb": 0.0})
            agg["cpu"] += proc.cpu_percent
            agg["rss_kb"] += proc.rss_kb
        out: List[Dict[str, Any]] = []
        for sess in parse_sessions(self._safe_read_text(self._sessions_reader)):
            sess = dict(sess)
            agg = per_uid.get(sess["uid"], {"cpu": 0.0, "rss_kb": 0.0})
            sess["cpu_percent"] = agg["cpu"]
            sess["rss_kb"] = agg["rss_kb"]
            out.append(sess)
        return out

    def terminate_session(self, session: str) -> None:
        """Terminate a login session; raises on failure."""
        self._terminate_session_fn(session)

    def startup_entries(self) -> List[StartupEntry]:
        """All parseable ``.desktop`` files in the autostart directory."""
        directory = self._autostart_dir_fn()
        try:
            names = self._autostart_list_fn(directory)
        except OSError:
            return []
        entries: List[StartupEntry] = []
        for file_name in sorted(names):
            if not file_name.endswith(".desktop"):
                continue
            path = os.path.join(directory, file_name)
            try:
                text = self._read_file_fn(path)
            except OSError:
                continue
            entry = parse_desktop(text, path)
            if entry is not None:
                entries.append(entry)
        return entries

    def set_startup_enabled(self, entry: StartupEntry, enabled: bool) -> None:
        """Rewrite the entry's file with ``X-GNOME-Autostart-enabled`` set.

        Raises ``ValueError`` on an unparseable file and propagates I/O
        errors so the GUI can report and revert.
        """
        text = self._read_file_fn(entry.path)
        try:
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            parser.optionxform = str  # keep key case when rewriting the file
            parser.read_string(text)
        except Exception:
            raise ValueError(f"cannot parse {entry.path}")
        if "Desktop Entry" not in parser:
            parser["Desktop Entry"] = {}
        parser["Desktop Entry"]["X-GNOME-Autostart-enabled"] = (
            "true" if enabled else "false"
        )
        buffer = io.StringIO()
        # space_around_delimiters=False keeps the conventional "Key=value"
        # form that freedesktop .desktop files use.
        parser.write(buffer, space_around_delimiters=False)
        self._write_file_fn(entry.path, buffer.getvalue())


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

def _make_line_graph_class():
    """Build the ``LineGraph`` widget class.

    Defined inside a function so the module stays importable headlessly
    (no top-level ``gi`` import); GTK is imported lazily here.
    """
    from gi.repository import Gtk

    class LineGraph(Gtk.DrawingArea):
        """A cairo line chart: ring buffers, grid, translucent fill.

        All colours come from the theme via
        :func:`omnify.core.theme.resolve_color` — nothing is hard-coded.
        *y_max* of None means "auto-scale to the data peak".
        """

        def __init__(
            self,
            series: Tuple[str, ...] = ("accent",),
            capacity: int = 90,
            y_max: Optional[float] = 100.0,
        ) -> None:
            super().__init__()
            self._series_names = tuple(series)
            self._buffers = tuple(RingBuffer(capacity) for _ in series)
            self._y_max = y_max
            self._theme = None
            self.set_draw_func(self._draw)
            self.set_size_request(200, 84)
            self.set_hexpand(True)

        def set_theme(self, theme) -> None:
            """Attach a :class:`~omnify.core.theme.Theme` for colour lookups."""
            self._theme = theme
            self.queue_draw()

        def add_sample(self, values: List[float]) -> None:
            """Append one sample per series (oldest is evicted)."""
            for buf, value in zip(self._buffers, values):
                buf.add(value)
            self.queue_draw()

        def _draw(self, _widget, cr, width: int, height: int) -> None:
            from ..core.theme import resolve_color

            # This GTK build's draw func passes (area, cr, width, height).
            if width < 4 or height < 4:
                return
            mode = self._theme.mode if self._theme is not None else "dark"
            accent = self._theme.accent if self._theme is not None else "blue"
            custom = self._theme.custom if self._theme is not None else None

            grid = resolve_color(mode, accent, "border", custom)
            cr.set_line_width(1.0)
            cr.set_source_rgba(grid[0], grid[1], grid[2], 0.55)
            for i in range(1, 5):
                y = height * i / 5.0
                cr.move_to(0, y)
                cr.line_to(width, y)
            for i in range(1, 4):
                x = width * i / 4.0
                cr.move_to(x, 0)
                cr.line_to(x, height)
            cr.stroke()

            peaks = [max(buf.values()) for buf in self._buffers if len(buf)]
            y_max = self._y_max
            if y_max is None or y_max <= 0:
                y_max = max([1.0] + peaks)

            for buf, name in zip(self._buffers, self._series_names):
                values = buf.values()
                if not values:
                    continue
                color = resolve_color(mode, accent, name, custom)
                capacity = buf.capacity
                offset = capacity - len(values)
                points = []
                for i, value in enumerate(values):
                    x = width * (offset + i) / max(1, capacity - 1)
                    frac = min(1.0, max(0.0, value / y_max))
                    y = height - 2 - (height - 4) * frac
                    points.append((x, y))
                # translucent fill under the line
                cr.move_to(points[0][0], height)
                for x, y in points:
                    cr.line_to(x, y)
                cr.line_to(points[-1][0], height)
                cr.close_path()
                cr.set_source_rgba(color[0], color[1], color[2], 0.15)
                cr.fill()
                # the line itself
                cr.move_to(points[0][0], points[0][1])
                for x, y in points[1:]:
                    cr.line_to(x, y)
                cr.set_source_rgba(color[0], color[1], color[2], 0.95)
                cr.set_line_width(1.6)
                cr.stroke()

    return LineGraph


class TaskManagerModule(BaseModule):
    """Task Manager view: processes, performance, services, startup, users.

    Thin GTK layer over :class:`TaskManager`: a 1-second ticker refreshes
    graphs and the visible tab; rows are updated in place (pid → row dict)
    instead of being rebuilt every tick.
    """

    id = "taskmanager"
    name = "Task Manager"
    icon = "\U0001F4CA"
    description = (
        "Processes, performance graphs, services, startup apps and users — "
        "a Windows Task Manager for Linux."
    )
    group = "System"

    _TICK_MS = 1000
    _TABS = (
        ("processes", "Processes"),
        ("performance", "Performance"),
        ("services", "Services"),
        ("startup", "Startup"),
        ("users", "Users"),
        ("details", "Details"),
    )

    _PROC_COLUMNS = (
        ("name", "Name"),
        ("pid", "PID"),
        ("user", "User"),
        ("cpu", "CPU %"),
        ("mem", "Memory"),
        ("disk", "Disk R/W"),
        ("net", "Net R/W"),
        ("gpu", "GPU"),
    )
    _DETAIL_COLUMNS = (
        ("pid", "PID"),
        ("ppid", "PPID"),
        ("user", "User"),
        ("state", "State"),
        ("threads", "Threads"),
        ("cpu", "CPU %"),
        ("mem", "RAM"),
        ("disk", "Disk R/W"),
        ("net", "Net R/W"),
        ("cmdline", "Cmdline"),
    )

    _PROC_KEY_FNS: Dict[str, Callable[["ProcessInfo"], Any]] = {
        "name": lambda p: p.name,
        "pid": lambda p: p.pid,
        "user": lambda p: p.user,
        "cpu": lambda p: p.cpu_percent,
        "mem": lambda p: p.rss_kb,
        "disk": lambda p: p.disk_speed,
        "net": lambda p: p.net_speed,
        "gpu": lambda p: p.gpu_vram_mib,
    }
    _DETAIL_KEY_FNS: Dict[str, Callable[["ProcessInfo"], Any]] = {
        "pid": lambda p: p.pid,
        "ppid": lambda p: p.ppid,
        "user": lambda p: p.user,
        "state": lambda p: p.state,
        "threads": lambda p: p.threads,
        "cpu": lambda p: p.cpu_percent,
        "mem": lambda p: p.rss_kb,
        "disk": lambda p: p.disk_speed,
        "net": lambda p: p.net_speed,
        "cmdline": lambda p: p.cmdline,
    }

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def build_view(self):
        """Build the tabbed view, prime data with one tick, start the ticker."""
        from gi.repository import Gtk

        self._model = TaskManager()
        self._timeout_id: Optional[int] = None
        self._active = False  # set True by on_show; the tick idles while hidden
        self._query = ""
        self._sort: Dict[str, Tuple[str, bool]] = {
            "processes": ("cpu", True),
            "details": ("cpu", True),
        }
        self._proc_rows: Dict[int, Any] = {}
        self._detail_rows: Dict[int, Any] = {}
        self._proc_info: Dict[int, ProcessInfo] = {}
        self._headers: Dict[str, Dict[str, Any]] = {}
        self._core_rows: List[Any] = []
        self._last: Dict[str, Any] = {
            "processes": [],
            "cpu": None,
            "ram": None,
            "gpu": None,
            "gpu_apps": None,
            "net": {},
            "disks": [],
            "mounts": [],
        }

        LineGraph = _make_line_graph_class()

        # Pages first (the tab toggle refreshes them on activation).
        self._stack = Gtk.Stack()
        self._stack.set_vexpand(True)
        self._stack.add_titled(self._build_processes_page(), "processes", "Processes")
        self._stack.add_titled(
            self._build_performance_page(LineGraph), "performance", "Performance"
        )
        self._stack.add_titled(self._build_services_page(), "services", "Services")
        self._stack.add_titled(self._build_startup_page(), "startup", "Startup")
        self._stack.add_titled(self._build_users_page(), "users", "Users")
        self._stack.add_titled(self._build_details_page(), "details", "Details")

        # Tab row (radio ToggleButtons, like the sidebar).
        tabs_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        first_button = None
        for key, label in self._TABS:
            button = Gtk.ToggleButton(label=label)
            if first_button is None:
                first_button = button
            else:
                button.set_group(first_button)
            button.connect("toggled", self._on_tab_toggled, key)
            tabs_box.append(button)
        assert first_button is not None
        first_button.set_active(True)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        root.set_margin_top(12)
        root.set_margin_bottom(12)
        root.set_margin_start(12)
        root.set_margin_end(12)
        root.append(tabs_box)
        root.append(self._stack)
        self._root = root

        self._do_tick()  # prime the view with one real scan
        self._start_ticker()
        return root

    # -- shared widget helpers ------------------------------------------------
    def _card(self, title: str):
        """A themed card box with a title label; returns the box."""
        from gi.repository import Gtk

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.get_style_context().add_class("omnify-card")
        label = Gtk.Label(label=title, xalign=0.0)
        label.get_style_context().add_class("omnify-card-title")
        card.append(label)
        return card

    def _dim_label(self) -> "Any":
        from gi.repository import Gtk
        from gi.repository import Pango

        label = Gtk.Label(label="", xalign=0.0)
        label.get_style_context().add_class("omnify-dim")
        label.set_ellipsize(Pango.EllipsizeMode.END)
        return label

    def _mono_label(self, text: str = "") -> "Any":
        from gi.repository import Gtk
        from gi.repository import Pango

        label = Gtk.Label(label=text, xalign=0.0)
        label.get_style_context().add_class("omnify-mono")
        label.set_ellipsize(Pango.EllipsizeMode.END)
        return label

    def _top_list_box(self) -> "Any":
        from gi.repository import Gtk

        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        return listbox

    def _column_header(self, page_key: str) -> "Any":
        """A row of sortable column-header buttons for *page_key*."""
        from gi.repository import Gtk

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        columns = (
            self._PROC_COLUMNS if page_key == "processes" else self._DETAIL_COLUMNS
        )
        self._headers[page_key] = {}
        for key, label in columns:
            button = Gtk.Button(label=label)
            button.set_halign(Gtk.Align.START)
            if key in ("name", "cmdline"):
                button.set_hexpand(True)
            button.connect("clicked", self._on_sort_clicked, page_key, key)
            box.append(button)
            self._headers[page_key][key] = (button, label)
        return box

    def _update_header_arrows(self, page_key: str) -> None:
        key, desc = self._sort[page_key]
        for col_key, (button, label) in self._headers.get(page_key, {}).items():
            arrow = (" " + ("▼" if desc else "▲")) if col_key == key else ""
            button.set_label(label + arrow)

    # -- Processes page ---------------------------------------------------------
    def _build_processes_page(self) -> "Any":
        from gi.repository import Gtk

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._proc_search = Gtk.SearchEntry()
        self._proc_search.set_hexpand(True)
        self._proc_search.set_placeholder_text("Search by name or command line…")
        self._proc_search.connect("search-changed", self._on_proc_search)
        header.append(self._proc_search)
        end_button = Gtk.Button(label="End task")
        end_button.get_style_context().add_class("omnify-danger")
        end_button.connect("clicked", self._on_end_task_clicked)
        header.append(end_button)
        page.append(header)

        page.append(self._column_header("processes"))

        scroller = Gtk.ScrolledWindow()
        # Horizontal AUTOMATIC so narrow windows scroll the wide table
        # instead of clipping the right-hand columns.
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        self._proc_list = Gtk.ListBox()
        self._proc_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        scroller.set_child(self._proc_list)
        page.append(scroller)
        return page

    def _make_proc_row(self, info: ProcessInfo) -> Tuple["Any", Dict[str, "Any"]]:
        """Build one process row; returns (row, label dict)."""
        from gi.repository import Gtk
        from gi.repository import Pango

        row = Gtk.ListBoxRow()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_margin_top(3)
        box.set_margin_bottom(3)
        box.set_margin_start(8)
        box.set_margin_end(8)

        name_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        name_box.set_hexpand(True)
        name_label = Gtk.Label(label=info.name, xalign=0.0)
        name_label.set_ellipsize(Pango.EllipsizeMode.END)
        name_box.append(name_label)
        sub_label = Gtk.Label(label="", xalign=0.0)
        sub_label.get_style_context().add_class("omnify-dim")
        sub_label.set_ellipsize(Pango.EllipsizeMode.END)
        name_box.append(sub_label)
        box.append(name_box)

        def column(width: int, text: str, mono: bool = True) -> "Any":
            label = Gtk.Label(label=text, xalign=0.0)
            label.set_width_chars(width)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            if mono:
                label.get_style_context().add_class("omnify-mono")
            return label

        labels: Dict[str, Any] = {
            "name": name_label,
            "cmd": sub_label,
            "pid": column(6, str(info.pid)),
            "user": column(9, info.user, mono=False),
            "cpu": column(7, "0.0%"),
            "mem": column(9, "-"),
            "disk": column(15, "-"),
            "net": column(15, "-"),
            "gpu": column(9, "-"),
        }
        for key in ("pid", "user", "cpu", "mem", "disk", "net", "gpu"):
            box.append(labels[key])
        row.set_child(box)

        gesture = Gtk.GestureClick()
        gesture.set_button(3)
        gesture.connect("pressed", self._on_row_right_press, info.pid, row)
        row.add_controller(gesture)
        return row, labels

    def _update_proc_row(self, row, labels: Dict[str, Any], info: ProcessInfo) -> None:
        """Push the latest values of *info* into an existing row."""
        labels["name"].set_label(info.name)
        labels["cmd"].set_label(info.cmdline)
        labels["pid"].set_label(str(info.pid))
        labels["user"].set_label(info.user)
        labels["cpu"].set_label(f"{info.cpu_percent:.1f}%")
        labels["mem"].set_label(format_size(info.rss_kb))
        if info.disk_speed is None:
            labels["disk"].set_label("-")
        else:
            labels["disk"].set_label(
                f"↓ {format_speed(info.disk_read_kib_s)}  ↑ {format_speed(info.disk_write_kib_s)}"
            )
        if info.net_speed is None:
            labels["net"].set_label("-")
        else:
            labels["net"].set_label(
                f"↓ {format_speed(info.net_rx_kib_s)}  ↑ {format_speed(info.net_tx_kib_s)}"
            )
        if info.gpu_vram_mib is None:
            labels["gpu"].set_label("-")
        else:
            labels["gpu"].set_label(f"{info.gpu_vram_mib:.0f} MiB")
        tip = (
            f"PID {info.pid} · {info.user} · state {info.state} · "
            f"{info.threads} threads · up {format_uptime(info.uptime_seconds)}"
        )
        if info.disk_read_kib_s is not None or info.disk_write_kib_s is not None:
            tip += (
                f"\nDisk total: ↓ {format_size(info.disk_read_total_kib)} "
                f"↑ {format_size(info.disk_write_total_kib)}"
            )
        row.set_tooltip_text(tip)

    def _sync_proc_rows(self, infos: List[ProcessInfo]) -> None:
        """Update existing rows in place, add new, drop dead, reorder, filter."""
        wanted = {info.pid for info in infos}
        for pid in list(self._proc_rows):
            if pid not in wanted:
                self._proc_list.remove(self._proc_rows[pid][0])
                del self._proc_rows[pid]
        for info in infos:
            if info.pid not in self._proc_rows:
                row, labels = self._make_proc_row(info)
                row._omnify_pid = info.pid
                self._proc_list.append(row)
                self._proc_rows[info.pid] = (row, labels)
        key, desc = self._sort["processes"]
        key_fn = self._PROC_KEY_FNS[key]
        visible = {info.pid for info in filter_processes(infos, self._query)}
        ordered = sorted_by(infos, key_fn, desc)
        for info in ordered:
            row, labels = self._proc_rows[info.pid]
            self._update_proc_row(row, labels, info)
            self._proc_info[info.pid] = info
            row.set_visible(info.pid in visible)
        self._reorder_if_changed(
            self._proc_list, self._proc_rows, [info.pid for info in ordered]
        )

    def _reorder_if_changed(self, listbox, rows, order: List[int]) -> None:
        """Re-append rows to match *order* — but only when it changed.

        Re-appending hundreds of rows on every tick forces a full
        relayout; the row set and its order usually stay stable between
        ticks, so skip the churn in that case.

        IMPORTANT (this GTK build): appending or inserting a row that is
        ALREADY a child leaves a phantom duplicate entry in the listbox's
        internal GSequence (verified: the sequence grows while the widget
        child list stays correct).  ``remove()`` deletes exactly one
        entry, so a phantom outlives the row it describes; once the row
        is freed (dead process, no other Python ref) the measure pass
        walks the sequence, hits the dangling pointer and trips
        ``gtk_widget_get_request_mode: GTK_IS_WIDGET`` -> SIGSEGV.
        Therefore rows that are already children must be REMOVED before
        being appended again.
        """
        current: List[int] = []
        child = listbox.get_first_child()
        while child is not None:
            pid = getattr(child, "_omnify_pid", None)
            if pid is not None:
                current.append(pid)
            child = child.get_next_sibling()
        if current == list(order):
            return
        for pid in order:
            entry = rows.get(pid)
            if entry is None:
                continue
            row = entry[0]
            if row.get_parent() is not None:
                listbox.remove(row)
            listbox.append(row)

    # -- Details page -------------------------------------------------------------
    def _build_details_page(self) -> "Any":
        from gi.repository import Gtk

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._detail_search = Gtk.SearchEntry()
        self._detail_search.set_hexpand(True)
        self._detail_search.set_placeholder_text("Filter by PID, name or command line…")
        self._detail_search.connect("search-changed", self._on_detail_search)
        header.append(self._detail_search)
        page.append(header)

        page.append(self._column_header("details"))

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        self._detail_list = Gtk.ListBox()
        self._detail_list.set_selection_mode(Gtk.SelectionMode.NONE)
        scroller.set_child(self._detail_list)
        page.append(scroller)
        return page

    def _make_detail_row(self, info: ProcessInfo) -> Tuple["Any", Dict[str, "Any"]]:
        from gi.repository import Gtk
        from gi.repository import Pango

        row = Gtk.ListBoxRow()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_top(2)
        box.set_margin_bottom(2)
        box.set_margin_start(8)
        box.set_margin_end(8)

        def column(width: int, text: str, mono: bool = True) -> "Any":
            label = Gtk.Label(label=text, xalign=0.0)
            label.set_width_chars(width)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            if mono:
                label.get_style_context().add_class("omnify-mono")
            return label

        labels: Dict[str, Any] = {
            "pid": column(6, str(info.pid)),
            "ppid": column(6, str(info.ppid)),
            "user": column(9, info.user, mono=False),
            "state": column(5, info.state),
            "threads": column(5, str(info.threads)),
            "cpu": column(7, "0.0%"),
            "mem": column(9, "-"),
            "disk": column(15, "-"),
            "net": column(15, "-"),
        }
        for key in ("pid", "ppid", "user", "state", "threads", "cpu", "mem", "disk", "net"):
            box.append(labels[key])
        cmdline = Gtk.Label(label="", xalign=0.0)
        cmdline.set_hexpand(True)
        cmdline.set_ellipsize(Pango.EllipsizeMode.END)
        cmdline.get_style_context().add_class("omnify-dim")
        box.append(cmdline)
        labels["cmdline"] = cmdline
        row.set_child(box)
        return row, labels

    def _update_detail_row(self, row, labels: Dict[str, Any], info: ProcessInfo) -> None:
        labels["pid"].set_label(str(info.pid))
        labels["ppid"].set_label(str(info.ppid))
        labels["user"].set_label(info.user)
        labels["state"].set_label(info.state)
        labels["threads"].set_label(str(info.threads))
        labels["cpu"].set_label(f"{info.cpu_percent:.1f}%")
        labels["mem"].set_label(format_size(info.rss_kb))
        if info.disk_speed is None:
            labels["disk"].set_label("-")
        else:
            labels["disk"].set_label(
                f"↓ {format_speed(info.disk_read_kib_s)}  ↑ {format_speed(info.disk_write_kib_s)}"
            )
        if info.net_speed is None:
            labels["net"].set_label("-")
        else:
            labels["net"].set_label(
                f"↓ {format_speed(info.net_rx_kib_s)}  ↑ {format_speed(info.net_tx_kib_s)}"
            )
        labels["cmdline"].set_label(info.cmdline)
        row.set_tooltip_text(f"PID {info.pid} · {info.cmdline}")

    def _sync_detail_rows(self, infos: List[ProcessInfo]) -> None:
        wanted = {info.pid for info in infos}
        for pid in list(self._detail_rows):
            if pid not in wanted:
                self._detail_list.remove(self._detail_rows[pid][0])
                del self._detail_rows[pid]
        for info in infos:
            if info.pid not in self._detail_rows:
                row, labels = self._make_detail_row(info)
                row._omnify_pid = info.pid
                self._detail_list.append(row)
                self._detail_rows[info.pid] = (row, labels)
        key, desc = self._sort["details"]
        key_fn = self._DETAIL_KEY_FNS[key]
        query = self._detail_query()
        ordered = sorted_by(infos, key_fn, desc)
        for info in ordered:
            row, labels = self._detail_rows[info.pid]
            self._update_detail_row(row, labels, info)
            haystack = f"{info.pid} {info.name} {info.cmdline}".lower()
            row.set_visible(not query or query in haystack)
        self._reorder_if_changed(
            self._detail_list, self._detail_rows, [info.pid for info in ordered]
        )

    def _detail_query(self) -> str:
        entry = getattr(self, "_detail_search", None)
        return entry.get_text().strip().lower() if entry is not None else ""

    # -- Performance page -----------------------------------------------------------
    def _build_performance_page(self, LineGraph) -> "Any":
        from gi.repository import Gtk

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        scroller.set_child(inner)
        page.append(scroller)
        theme = self.ctx.theme

        # CPU card
        cpu_card = self._card("CPU")
        self._cpu_graph = LineGraph(("accent",), capacity=90, y_max=100.0)
        self._cpu_graph.set_theme(theme)
        cpu_card.append(self._cpu_graph)
        self._cpu_value = self._mono_label("-")
        self._cpu_value.get_style_context().add_class("omnify-big-value")
        cpu_card.append(self._cpu_value)
        self._cpu_info1 = self._dim_label()
        self._cpu_info2 = self._dim_label()
        self._cpu_info3 = self._dim_label()
        for label in (self._cpu_info1, self._cpu_info2, self._cpu_info3):
            cpu_card.append(label)
        self._core_grid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        cpu_card.append(self._core_grid)
        self._section_label = Gtk.Label(label="Top processes by CPU", xalign=0.0)
        self._section_label.get_style_context().add_class("omnify-dim")
        cpu_card.append(self._section_label)
        self._cpu_top = self._top_list_box()
        cpu_card.append(self._cpu_top)
        inner.append(cpu_card)

        # Memory card
        ram_card = self._card("Memory")
        self._ram_graph = LineGraph(("accent",), capacity=90, y_max=100.0)
        self._ram_graph.set_theme(theme)
        ram_card.append(self._ram_graph)
        self._ram_value = self._mono_label("-")
        self._ram_value.get_style_context().add_class("omnify-big-value")
        self._ram_info = self._dim_label()
        ram_card.append(self._ram_value)
        ram_card.append(self._ram_info)
        section = Gtk.Label(label="Top processes by memory", xalign=0.0)
        section.get_style_context().add_class("omnify-dim")
        ram_card.append(section)
        self._ram_top = self._top_list_box()
        ram_card.append(self._ram_top)
        inner.append(ram_card)

        # GPU card
        gpu_card = self._card("GPU")
        self._gpu_graph = LineGraph(("accent",), capacity=90, y_max=100.0)
        self._gpu_graph.set_theme(theme)
        gpu_card.append(self._gpu_graph)
        self._gpu_value = self._mono_label("-")
        self._gpu_value.get_style_context().add_class("omnify-big-value")
        self._gpu_info = self._dim_label()
        gpu_card.append(self._gpu_value)
        gpu_card.append(self._gpu_info)
        section = Gtk.Label(label="Top processes by VRAM", xalign=0.0)
        section.get_style_context().add_class("omnify-dim")
        gpu_card.append(section)
        self._gpu_top = self._top_list_box()
        gpu_card.append(self._gpu_top)
        inner.append(gpu_card)

        # Disks card
        disk_card = self._card("Disks")
        self._disk_rows_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        disk_card.append(self._disk_rows_box)
        self._disk_graph = LineGraph(("accent", "success"), capacity=90, y_max=None)
        self._disk_graph.set_theme(theme)
        disk_card.append(self._disk_graph)
        section = Gtk.Label(label="Mounts", xalign=0.0)
        section.get_style_context().add_class("omnify-dim")
        disk_card.append(section)
        self._mount_rows_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        disk_card.append(self._mount_rows_box)
        inner.append(disk_card)

        # Network card
        net_card = self._card("Network")
        self._net_rows_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        net_card.append(self._net_rows_box)
        self._net_graph = LineGraph(("accent", "success"), capacity=90, y_max=None)
        self._net_graph.set_theme(theme)
        net_card.append(self._net_graph)
        self._net_totals = self._dim_label()
        net_card.append(self._net_totals)
        inner.append(net_card)
        return page

    def _ensure_core_bars(self, cores: int) -> None:
        """(Re)build the per-core mini progress bars when the count changes."""
        from gi.repository import Gtk

        if len(self._core_rows) == cores:
            return
        for child in list(_iter_children(self._core_grid)):
            self._core_grid.remove(child)
        self._core_rows = []
        for i in range(cores):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            label = Gtk.Label(label=f"C{i}", xalign=0.0)
            label.get_style_context().add_class("omnify-dim")
            label.set_width_chars(4)
            bar = Gtk.ProgressBar()
            bar.set_size_request(-1, 8)
            bar.set_hexpand(True)
            pct = Gtk.Label(label="-", xalign=1.0)
            pct.get_style_context().add_class("omnify-mono")
            pct.set_width_chars(6)
            row.append(label)
            row.append(bar)
            row.append(pct)
            self._core_grid.append(row)
            self._core_rows.append((bar, pct))

    def _refresh_top_list(self, listbox, items: List[Tuple[str, str]]) -> None:
        """Rebuild a small top-N list (cheap: ≤10 rows per tick)."""
        from gi.repository import Gtk
        from gi.repository import Pango

        for child in list(_iter_children(listbox)):
            listbox.remove(child)
        if not items:
            row = Gtk.ListBoxRow()
            note = Gtk.Label(label=tr("no data"), xalign=0.0)
            note.get_style_context().add_class("omnify-dim")
            note.set_margin_start(8)
            row.set_child(note)
            listbox.append(row)
            return
        for name, value in items:
            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            box.set_margin_start(8)
            name_label = Gtk.Label(label=name, xalign=0.0)
            name_label.set_hexpand(True)
            name_label.set_ellipsize(Pango.EllipsizeMode.END)
            value_label = Gtk.Label(label=value, xalign=1.0)
            value_label.get_style_context().add_class("omnify-mono")
            box.append(name_label)
            box.append(value_label)
            row.set_child(box)
            listbox.append(row)

    def _speed_cell(self, speed: Optional[float]) -> str:
        return format_speed(speed) if speed is not None else "-"

    def _refresh_performance(self) -> None:
        """Push the last tick's data into every Performance card."""
        from gi.repository import Gtk
        from gi.repository import Pango

        last = self._last
        cpu, ram, gpu = last.get("cpu"), last.get("ram"), last.get("gpu")

        # CPU card
        if cpu:
            self._cpu_value.set_label(f"{cpu['percent']:.1f}%")
            self._ensure_core_bars(cpu.get("cores") or 0)
            for i, (bar, pct) in enumerate(self._core_rows):
                value = cpu["per_core"].get(i, 0.0)
                bar.set_fraction(min(1.0, value / 100.0))
                pct.set_label(f"{value:.0f}%")
            model = cpu.get("model") or "unknown CPU"
            self._cpu_info1.set_label(
                tr(
                    "{count} cores · {model}",
                    count=cpu.get("cores") or "-",
                    model=model,
                )
            )
            loadavg = cpu.get("loadavg")
            if loadavg is not None:
                self._cpu_info2.set_label(
                    f"Load: {loadavg[0]:.2f}  {loadavg[1]:.2f}  {loadavg[2]:.2f}"
                )
            else:
                self._cpu_info2.set_label(tr("Load: -"))
            freq = cpu.get("freq") or []
            if freq:
                curs = [f["cur"] for f in freq if f["cur"] is not None]
                mins = [f["min"] for f in freq if f["min"] is not None]
                maxs = [f["max"] for f in freq if f["max"] is not None]
                cur = sum(curs) / len(curs) if curs else None
                self._cpu_info3.set_label(
                    f"Frequency: {format_hz(cur)} now · "
                    f"{format_hz(min(mins) if mins else None)} – "
                    f"{format_hz(max(maxs) if maxs else None)} range"
                )
            else:
                self._cpu_info3.set_label(tr("Frequency: -"))
        else:
            self._cpu_value.set_label(tr("Not available"))
        self._refresh_top_list(
            self._cpu_top,
            [(p.name, f"{p.cpu_percent:.1f}%") for p in self._model.top_processes("cpu", 10)],
        )

        # Memory card
        if ram:
            self._ram_value.set_label(
                f"{format_size(ram['used_kb'])} / {format_size(ram['total_kb'])}"
            )
            swap = (
                f" · swap {format_size(ram['swap_used_kb'])} / {format_size(ram['swap_total_kb'])}"
                if ram["swap_total_kb"] > 0
                else ""
            )
            self._ram_info.set_label(
                f"{ram['percent']:.0f}% used · available {format_size(ram['available_kb'])}{swap}"
            )
        else:
            self._ram_value.set_label(tr("Not available"))
            self._ram_info.set_label("")
        self._refresh_top_list(
            self._ram_top,
            [(p.name, format_size(p.rss_kb)) for p in self._model.top_processes("rss", 10)],
        )

        # GPU card
        gpu_apps = last.get("gpu_apps") or {}
        if gpu:
            self._gpu_value.set_label(
                f"{gpu['utilization']:.0f}% · VRAM "
                f"{format_size(gpu['memory_used_mib'] * 1024)} / "
                f"{format_size(gpu['memory_total_mib'] * 1024)}"
            )
            self._gpu_info.set_label(f"{gpu['name']} · {gpu['temperature_c']:.0f} °C")
        else:
            self._gpu_value.set_label(tr("Not available"))
            self._gpu_info.set_label(tr("no GPU data"))
        apps_sorted = sorted(
            gpu_apps.values(), key=lambda a: a["vram_mib"], reverse=True
        )[:10]
        self._refresh_top_list(
            self._gpu_top,
            [(a["name"], f"{a['vram_mib']:.0f} MiB") for a in apps_sorted],
        )

        # Disks card
        for child in list(_iter_children(self._disk_rows_box)):
            self._disk_rows_box.remove(child)
        for disk in last.get("disks", []):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            name = Gtk.Label(label=disk["name"], xalign=0.0)
            name.set_ellipsize(Pango.EllipsizeMode.END)
            name.set_width_chars(10)
            row.append(name)
            if disk["total_bytes"]:
                bar = Gtk.ProgressBar()
                fraction = (
                    disk["used_bytes"] / disk["total_bytes"]
                    if disk["total_bytes"]
                    else 0.0
                )
                bar.set_fraction(min(1.0, max(0.0, fraction)))
                bar.set_size_request(80, 8)
                bar.set_hexpand(True)
                size_label = Gtk.Label(
                    label=f"{format_bytes(disk['used_bytes'])} / {format_bytes(disk['total_bytes'])}",
                    xalign=0.0,
                )
                size_label.get_style_context().add_class("omnify-mono")
                size_label.set_width_chars(16)
                row.append(bar)
                row.append(size_label)
            speed_label = Gtk.Label(
                label=f"↓ {self._speed_cell(disk['read_kib_s'])}  ↑ {self._speed_cell(disk['write_kib_s'])}",
                xalign=0.0,
            )
            speed_label.get_style_context().add_class("omnify-mono")
            row.append(speed_label)
            self._disk_rows_box.append(row)

        for child in list(_iter_children(self._mount_rows_box)):
            self._mount_rows_box.remove(child)
        for mount in last.get("mounts", []):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            name = Gtk.Label(label=mount["mount"], xalign=0.0)
            name.set_ellipsize(Pango.EllipsizeMode.END)
            name.set_width_chars(14)
            row.append(name)
            bar = Gtk.ProgressBar()
            bar.set_fraction(min(1.0, mount["percent"] / 100.0))
            bar.set_size_request(80, 8)
            bar.set_hexpand(True)
            size_label = Gtk.Label(
                label=f"{format_bytes(mount['used_bytes'])} / {format_bytes(mount['total_bytes'])}",
                xalign=0.0,
            )
            size_label.get_style_context().add_class("omnify-mono")
            size_label.set_width_chars(16)
            row.append(bar)
            row.append(size_label)
            self._mount_rows_box.append(row)

        # Network card
        for child in list(_iter_children(self._net_rows_box)):
            self._net_rows_box.remove(child)
        net = last.get("net", {})
        ordered = sorted(net.items(), key=lambda kv: 0 if kv[0] != "lo" else 1)
        for iface, values in ordered:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            name = Gtk.Label(label=iface, xalign=0.0)
            name.set_ellipsize(Pango.EllipsizeMode.END)
            name.set_width_chars(8)
            if iface == "lo":
                name.get_style_context().add_class("omnify-dim")
            row.append(name)
            state = values.get("state")
            state_label = Gtk.Label(label=state or "-", xalign=0.0)
            state_label.get_style_context().add_class("omnify-dim")
            state_label.set_width_chars(7)
            row.append(state_label)
            speed_label = Gtk.Label(
                label=f"↓ {self._speed_cell(values.get('rx_kib_s'))}  ↑ {self._speed_cell(values.get('tx_kib_s'))}",
                xalign=0.0,
            )
            speed_label.get_style_context().add_class("omnify-mono")
            speed_label.set_hexpand(True)
            row.append(speed_label)
            totals_label = Gtk.Label(
                label=f"{format_bytes(values.get('rx_bytes'))} in · {format_bytes(values.get('tx_bytes'))} out",
                xalign=0.0,
            )
            totals_label.get_style_context().add_class("omnify-dim")
            row.append(totals_label)
            self._net_rows_box.append(row)

        rx_total = sum(v["rx_bytes"] for v in net.values() if v.get("rx_bytes"))
        tx_total = sum(v["tx_bytes"] for v in net.values() if v.get("tx_bytes"))
        self._net_totals.set_label(
            f"Totals (all interfaces): {format_bytes(rx_total)} in · {format_bytes(tx_total)} out"
        )

    # -- Services page ------------------------------------------------------------
    def _build_services_page(self) -> "Any":
        from gi.repository import Gtk

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._services_note = self._dim_label()
        self._services_note.set_hexpand(True)
        header.append(self._services_note)
        refresh_button = Gtk.Button(label="Refresh")
        refresh_button.connect("clicked", self._load_services)
        header.append(refresh_button)
        page.append(header)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        self._services_list = Gtk.ListBox()
        self._services_list.set_selection_mode(Gtk.SelectionMode.NONE)
        scroller.set_child(self._services_list)
        page.append(scroller)
        return page

    def _load_services(self, _button=None) -> None:
        from gi.repository import Gtk
        from gi.repository import Pango

        services = self._model.services()
        for child in list(_iter_children(self._services_list)):
            self._services_list.remove(child)
        if not services:
            self._services_note.set_label(tr("systemd not available"))
            return
        self._services_note.set_label(
            tr("{count} services", count=len(services))
        )
        for svc in services:
            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            box.set_margin_top(3)
            box.set_margin_bottom(3)
            box.set_margin_start(8)
            box.set_margin_end(8)
            unit = Gtk.Label(label=svc["unit"], xalign=0.0)
            unit.set_ellipsize(Pango.EllipsizeMode.END)
            unit.set_width_chars(24)
            box.append(unit)
            status = Gtk.Label(label=f"{svc['active']} ({svc['sub']})", xalign=0.0)
            status.get_style_context().add_class("omnify-dim")
            status.set_width_chars(18)
            box.append(status)
            pid = Gtk.Label(label=str(svc.get("pid") or "-"), xalign=0.0)
            pid.get_style_context().add_class("omnify-mono")
            pid.set_width_chars(7)
            box.append(pid)
            desc = Gtk.Label(label=svc["description"], xalign=0.0)
            desc.get_style_context().add_class("omnify-dim")
            desc.set_hexpand(True)
            desc.set_ellipsize(Pango.EllipsizeMode.END)
            box.append(desc)
            row.set_child(box)
            gesture = Gtk.GestureClick()
            gesture.set_button(3)
            gesture.connect("pressed", self._on_service_menu, svc["unit"], row)
            row.add_controller(gesture)
            self._services_list.append(row)

    def _on_service_menu(self, _gesture, _n, _x, _y, unit: str, row) -> None:
        self._popup_menu(
            row,
            (
                ("Start", lambda: self._request_service_action("start", unit)),
                ("Stop", lambda: self._request_service_action("stop", unit)),
                ("Restart", lambda: self._request_service_action("restart", unit)),
            ),
        )

    def _request_service_action(self, action: str, unit: str) -> None:
        label = action.capitalize()
        if self._confirm(
            f"{label} {unit}?",
            f"Omnify will run: systemctl {action} {unit}",
            label,
        ) != 1:
            return
        try:
            self._model.service_action(action, unit)
            self.ctx.notify(f"{unit}: {action} sent")
            self._load_services()
        except Exception as exc:
            self.ctx.notify(f"systemctl {action} {unit} failed: {exc}")

    # -- Startup page ----------------------------------------------------------------
    def _build_startup_page(self) -> "Any":
        from gi.repository import Gtk

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._startup_note = self._dim_label()
        self._startup_note.set_hexpand(True)
        header.append(self._startup_note)
        refresh_button = Gtk.Button(label="Refresh")
        refresh_button.connect("clicked", self._load_startup)
        header.append(refresh_button)
        page.append(header)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        self._startup_list = Gtk.ListBox()
        self._startup_list.set_selection_mode(Gtk.SelectionMode.NONE)
        scroller.set_child(self._startup_list)
        page.append(scroller)
        return page

    def _load_startup(self, _button=None) -> None:
        from gi.repository import Gtk
        from gi.repository import Pango

        entries = self._model.startup_entries()
        for child in list(_iter_children(self._startup_list)):
            self._startup_list.remove(child)
        if not entries:
            self._startup_note.set_label(tr("No autostart entries"))
            return
        self._startup_note.set_label(
            tr("{count} entries", count=len(entries))
        )
        for entry in entries:
            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            box.set_margin_top(3)
            box.set_margin_bottom(3)
            box.set_margin_start(8)
            box.set_margin_end(8)
            name = Gtk.Label(label=entry.name, xalign=0.0)
            name.set_ellipsize(Pango.EllipsizeMode.END)
            name.set_width_chars(24)
            box.append(name)
            exec_label = Gtk.Label(label=entry.exec or "-", xalign=0.0)
            exec_label.get_style_context().add_class("omnify-dim")
            exec_label.set_hexpand(True)
            exec_label.set_ellipsize(Pango.EllipsizeMode.END)
            box.append(exec_label)
            switch = Gtk.Switch()
            switch.set_active(entry.enabled)
            switch.connect("state-set", self._on_startup_toggle, entry)
            box.append(switch)
            row.set_child(box)
            self._startup_list.append(row)

    def _on_startup_toggle(self, switch, entry: StartupEntry) -> bool:
        enabled = switch.get_active()
        try:
            self._model.set_startup_enabled(entry, enabled)
            self.ctx.notify(
                f"Startup: {entry.name} {'enabled' if enabled else 'disabled'}"
            )
        except Exception as exc:
            switch.set_active(not enabled)
            self.ctx.notify(f"Could not update {entry.name}: {exc}")
        return False

    # -- Users page ---------------------------------------------------------------------
    def _build_users_page(self) -> "Any":
        from gi.repository import Gtk

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._users_note = self._dim_label()
        self._users_note.set_hexpand(True)
        header.append(self._users_note)
        refresh_button = Gtk.Button(label="Refresh")
        refresh_button.connect("clicked", self._load_users)
        header.append(refresh_button)
        page.append(header)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        self._users_list = Gtk.ListBox()
        self._users_list.set_selection_mode(Gtk.SelectionMode.NONE)
        scroller.set_child(self._users_list)
        page.append(scroller)
        return page

    def _load_users(self, _button=None) -> None:
        from gi.repository import Gtk
        from gi.repository import Pango

        sessions = self._model.sessions()
        for child in list(_iter_children(self._users_list)):
            self._users_list.remove(child)
        if not sessions:
            self._users_note.set_label(
                tr("no sessions found (loginctl unavailable?)")
            )
            return
        self._users_note.set_label(
            tr("{count} sessions", count=len(sessions))
        )
        for sess in sessions:
            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            box.set_margin_top(3)
            box.set_margin_bottom(3)
            box.set_margin_start(8)
            box.set_margin_end(8)
            user = Gtk.Label(label=sess["user"], xalign=0.0)
            user.set_ellipsize(Pango.EllipsizeMode.END)
            user.set_hexpand(True)
            box.append(user)
            sess_label = Gtk.Label(label=f"sess {sess['session']}", xalign=0.0)
            sess_label.get_style_context().add_class("omnify-dim")
            box.append(sess_label)
            cpu_label = Gtk.Label(label=f"{sess['cpu_percent']:.1f}%", xalign=0.0)
            cpu_label.get_style_context().add_class("omnify-mono")
            box.append(cpu_label)
            ram_label = Gtk.Label(label=format_size(sess["rss_kb"]), xalign=0.0)
            ram_label.get_style_context().add_class("omnify-mono")
            box.append(ram_label)
            logout = Gtk.Button(label="Log out")
            logout.get_style_context().add_class("omnify-danger")
            logout.connect("clicked", self._request_logout, sess["session"], sess["user"])
            box.append(logout)
            row.set_child(box)
            self._users_list.append(row)

    def _request_logout(self, _button, session: str, user: str) -> None:
        if (
            self._confirm(
                f"Log out {user}?",
                f"Session {session} will be terminated (loginctl).",
                "Log out",
            )
            != 1
        ):
            return
        try:
            self._model.terminate_session(session)
            self.ctx.notify(f"Session {session} ({user}) terminated")
            self._load_users()
        except Exception as exc:
            self.ctx.notify(f"Could not terminate session {session}: {exc}")

    # -- Menus, dialogs, kill flow ----------------------------------------------------------
    def _popup_menu(self, anchor, items) -> None:
        """Show a right-click menu (button list in a PopoverMenu) at *anchor*."""
        from gi.repository import Gtk

        menu_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for label, action in items:
            button = Gtk.Button(label=label)
            button.set_halign(Gtk.Align.FILL)
            button.set_margin_top(1)
            button.set_margin_bottom(1)
            button.connect("clicked", self._on_menu_action, action)
            menu_box.append(button)
        pad = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        pad.set_margin_top(8)
        pad.set_margin_bottom(8)
        pad.set_margin_start(8)
        pad.set_margin_end(8)
        pad.append(menu_box)
        # NOTE: this GTK build has no widget destroy/unparent/remove API,
        # so the popover stays parented to the anchor row after it is
        # popped down (a small, bounded leak per right-click menu).
        popover = Gtk.PopoverMenu()
        popover.set_child(pad)
        popover.set_parent(anchor)
        popover.popup()
        for child in list(_iter_children(menu_box)):
            child.connect("clicked", popover.popdown)

    def _on_menu_action(self, _button, action) -> None:
        action()

    def _parent_window(self):
        """The top-level application window, or None if unresolvable."""
        try:
            return self._root.get_root()
        except Exception:
            return None

    def _confirm(self, message: str, detail: str, confirm_label: str) -> int:
        """Show a blocking Cancel/confirm dialog; return the pressed index.

        This GTK build's ``Gtk.AlertDialog`` takes ``set_message`` /
        ``set_detail`` / ``set_buttons(labels)`` and a synchronous
        ``choose(parent)`` that returns the button index — there is no
        ``response`` signal and no ``add_button``/``set_heading``.
        """
        from gi.repository import Gtk

        dialog = Gtk.AlertDialog()
        dialog.set_message(message)
        dialog.set_detail(detail)
        dialog.set_buttons(["Cancel", confirm_label])
        dialog.set_cancel_button(0)
        dialog.set_default_button(1)
        dialog.set_modal(True)
        return dialog.choose(self._parent_window())

    def _on_row_right_press(self, _gesture, _n, _x, _y, pid: int, row) -> None:
        info = self._proc_info.get(pid)
        if info is None:
            return
        self._popup_menu(
            row,
            (
                ("End task", lambda: self._request_kill(pid, False, False, info.name)),
                ("Force end", lambda: self._request_kill(pid, True, False, info.name)),
                (
                    "End task and all children",
                    lambda: self._request_kill(pid, False, True, info.name),
                ),
            ),
        )

    def _on_end_task_clicked(self, _button) -> None:
        row = self._proc_list.get_selected_row()
        if row is None:
            self.ctx.notify("Select a process first")
            return
        pid = next(
            (p for p, (r, _) in self._proc_rows.items() if r is row), None
        )
        if pid is None:
            return
        info = self._proc_info.get(pid)
        self._request_kill(pid, False, False, info.name if info else str(pid))

    def _request_kill(self, pid: int, force: bool, tree: bool, name: str) -> None:
        heading = "Force end" if force else "End task"
        if tree:
            heading += " and all children"
        detail = "SIGKILL" if force else "SIGTERM"
        extra = " along with all its child processes" if tree else ""
        if (
            self._confirm(
                heading,
                f"PID {pid} ({name}) will be terminated with {detail}{extra}.",
                "Force end" if force else "End task",
            )
            != 1
        ):
            return
        try:
            if tree:
                count = self._model.kill_tree(pid, force)
                extra = f" and {count - 1} children" if count > 1 else ""
            else:
                self._model.kill_pid(pid, force)
                extra = ""
            self.ctx.notify(f"PID {pid} ({name}) closed{extra}")
        except PermissionError:
            self.ctx.notify(
                f"Permission denied: PID {pid} ({name}) is not owned by you"
            )
        except (OSError, ValueError) as exc:
            self.ctx.notify(f"Failed to end PID {pid} ({name}): {exc}")

    # -- Ticker / tabs / search ---------------------------------------------------------------
    def _on_tab_toggled(self, button, key: str) -> None:
        if not button.get_active():
            return
        self._stack.set_visible_child_name(key)
        self._refresh_page(key)

    def _refresh_page(self, key: str) -> None:
        if key == "processes":
            self._sync_proc_rows(self._last.get("processes", []))
        elif key == "details":
            self._sync_detail_rows(self._last.get("processes", []))
        elif key == "performance":
            self._refresh_performance()
        elif key == "services":
            self._load_services()
        elif key == "startup":
            self._load_startup()
        elif key == "users":
            self._load_users()

    def _on_proc_search(self, entry) -> None:
        self._query = entry.get_text().strip().lower()
        self._sync_proc_rows(self._last.get("processes", []))

    def _on_detail_search(self, entry) -> None:
        self._sync_detail_rows(self._last.get("processes", []))

    def _on_sort_clicked(self, _button, page_key: str, key: str) -> None:
        current_key, desc = self._sort[page_key]
        if current_key == key:
            desc = not desc
        else:
            desc = True
        self._sort[page_key] = (key, desc)
        self._update_header_arrows(page_key)
        self._refresh_page(page_key)

    def _on_tick(self) -> bool:
        """Ticker callback: refresh only while the module is shown."""
        if not self._active:
            return True
        return self._do_tick()

    def _do_tick(self) -> bool:
        """One refresh: scan, feed the graphs, update the visible tab."""
        try:
            model = self._model
            processes = model.scan_processes()
            cpu = {
                "percent": model.cpu_percent(),
                "per_core": model.per_core_percents(),
                "cores": model.core_count(),
                "model": model.cpuinfo()["model"],
                "loadavg": model.loadavg(),
                "freq": model.cpu_frequencies(),
            }
            ram = model.ram_info()
            gpu = model.gpu_info()
            gpu_apps = model.gpu_apps()
            net = model.network()
            disks = model.disks()
            mounts = model.mounts()
        except Exception:
            # Keep the last values on any unexpected error.
            return True

        if not processes:
            # A failed or empty scan must never wipe the visible lists;
            # keep showing the last known state instead.
            return True

        self._last.update(
            {
                "processes": processes,
                "cpu": cpu,
                "ram": ram,
                "gpu": gpu,
                "gpu_apps": gpu_apps,
                "net": net,
                "disks": disks,
                "mounts": mounts,
            }
        )

        if cpu is not None:
            self._cpu_graph.add_sample([cpu["percent"]])
        if ram is not None:
            self._ram_graph.add_sample([ram["percent"]])
        if gpu is not None:
            self._gpu_graph.add_sample([gpu["utilization"]])
        down = sum(
            v["rx_kib_s"]
            for k, v in net.items()
            if k != "lo" and v.get("rx_kib_s") is not None
        )
        up = sum(
            v["tx_kib_s"]
            for k, v in net.items()
            if k != "lo" and v.get("tx_kib_s") is not None
        )
        self._net_graph.add_sample([down, up])
        disk_read = sum(
            d["read_kib_s"] for d in disks if d.get("read_kib_s") is not None
        )
        disk_write = sum(
            d["write_kib_s"] for d in disks if d.get("write_kib_s") is not None
        )
        self._disk_graph.add_sample([disk_read, disk_write])

        self._refresh_page(self._stack.get_visible_child_name())
        self._audit_lists()
        return True

    def _audit_lists(self) -> None:
        """DEBUG (OMNIFY_AUDIT=1): verify listbox children match the rows.

        This GTK build corrupts a listbox's row sequence under heavy
        re-append load (stale pointer -> layout assertion -> SIGSEGV);
        the audit prints the first observable divergence so the offending
        tick can be identified.
        """
        import os

        if os.environ.get("OMNIFY_AUDIT") != "1":
            return
        proc_list = getattr(self, "_proc_list", None)
        detail_list = getattr(self, "_detail_list", None)
        if proc_list is None or detail_list is None:
            return
        for name, listbox, rows in (
            ("procs", proc_list, self._proc_rows),
            ("details", detail_list, self._detail_rows),
        ):
            n = 0
            child = listbox.get_first_child()
            while child is not None:
                n += 1
                child = child.get_next_sibling()
            if n != len(rows):
                print(f"AUDIT {name}: children={n} rows={len(rows)}", flush=True)

    # -- Ticker lifecycle ---------------------------------------------------------------
    def _start_ticker(self) -> None:
        """Start the 1 s ticker if it is not already running."""
        from gi.repository import GLib

        if self._timeout_id is None:
            self._timeout_id = GLib.timeout_add(self._TICK_MS, self._on_tick)

    def _stop_ticker(self) -> None:
        """Stop the ticker if it is running."""
        from gi.repository import GLib

        if self._timeout_id is not None:
            GLib.source_remove(self._timeout_id)
            self._timeout_id = None

    def on_show(self) -> None:
        if getattr(self, "_model", None) is None:
            return
        self._active = True
        self._start_ticker()
        self._refresh_page(self._stack.get_visible_child_name())

    def on_hide(self) -> None:
        self._active = False
        self._stop_ticker()
