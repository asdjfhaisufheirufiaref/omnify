"""System monitor module: live CPU, RAM and GPU usage.

The model (:class:`SystemMonitor`) is pure Python with no GTK dependency.
It is driven by three injectable zero-argument readers, each returning a
``str``:

- ``stat_reader``   — the text of ``/proc/stat`` (default).
- ``meminfo_reader`` — the text of ``/proc/meminfo`` (default).
- ``gpu_reader``    — the first CSV line from ``nvidia-smi`` (default runs
  the command via :mod:`subprocess` and raises on a nonzero exit code).

Tests pass fake readers with canned strings, so nothing here ever touches
the real ``/proc`` tree or spawns a subprocess unless explicitly asked to.
"""

from __future__ import annotations

import subprocess
from typing import Any, Callable, Dict, Optional, Tuple

from ..core.i18n import tr
from ..core.module_context import BaseModule


def _default_stat_reader() -> str:
    """Return the text of ``/proc/stat``."""
    with open("/proc/stat", encoding="utf-8") as fh:
        return fh.read()


def _default_meminfo_reader() -> str:
    """Return the text of ``/proc/meminfo``."""
    with open("/proc/meminfo", encoding="utf-8") as fh:
        return fh.read()


def _default_gpu_reader() -> str:
    """Return the CSV output of ``nvidia-smi`` for the first GPU.

    Raises on a nonzero exit code or when the command cannot be run within
    the timeout; :meth:`SystemMonitor.gpu` treats any such failure as
    "no GPU data".
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


class SystemMonitor:
    """A pure system monitor driven by injectable zero-arg text readers.

    Each accessor degrades gracefully on bad input: :meth:`cpu_percent`
    and :meth:`gpu` return a neutral value on an unparseable sample, and
    :meth:`snapshot` never raises at all.
    """

    def __init__(
        self,
        stat_reader: Callable[[], str] = _default_stat_reader,
        meminfo_reader: Callable[[], str] = _default_meminfo_reader,
        gpu_reader: Callable[[], str] = _default_gpu_reader,
    ) -> None:
        self._stat_reader = stat_reader
        self._meminfo_reader = meminfo_reader
        self._gpu_reader = gpu_reader
        #: ``(total, idle)`` of the last good CPU sample, or ``None``.
        self._prev_cpu: Optional[Tuple[float, float]] = None

    # -- CPU -----------------------------------------------------------------
    def cpu_percent(self) -> float:
        """CPU utilization as a percentage in ``[0, 100]``.

        Compares the aggregate ``cpu`` line of ``/proc/stat`` against the
        previous good sample. The first call only primes the cache and
        returns ``0.0``. A missing, short or non-numeric ``cpu`` line yields
        ``0.0`` without touching the cached sample, so the next good reading
        is compared against the last good one.
        """
        current = self._read_cpu()
        if current is None:
            return 0.0
        total, idle = current
        prev = self._prev_cpu
        self._prev_cpu = current
        if prev is None:
            return 0.0
        prev_total, prev_idle = prev
        delta_total = total - prev_total
        delta_idle = idle - prev_idle
        if delta_total <= 0:
            return 0.0
        percent = 100.0 * (1.0 - delta_idle / delta_total)
        return max(0.0, min(100.0, percent))

    def _read_cpu(self) -> Optional[Tuple[float, float]]:
        """Return ``(total, idle)`` from the aggregate ``cpu`` line, else None."""
        try:
            text = self._stat_reader()
        except Exception:
            return None
        for line in text.splitlines():
            # Only the aggregate line starts with "cpu " (cpu0, cpu1, ... do not).
            if not line.startswith("cpu "):
                continue
            fields = line.split()
            # Need cpu + at least user, nice, system, idle, iowait.
            if len(fields) < 6:
                return None
            try:
                values = [float(token) for token in fields[1:]]
            except ValueError:
                return None
            total = sum(values)
            idle = values[3] + values[4]  # idle + iowait
            return total, idle
        return None

    # -- RAM -----------------------------------------------------------------
    def ram(self) -> Dict[str, float]:
        """Memory usage parsed from ``MemTotal``/``MemAvailable`` (kB).

        Returns ``{"total_kb", "available_kb", "used_kb", "percent"}``.
        Raises :class:`ValueError` if either field is missing from the
        ``/proc/meminfo`` text.
        """
        total = self._meminfo_field("MemTotal:")
        available = self._meminfo_field("MemAvailable:")
        if total is None or available is None:
            raise ValueError("MemTotal/MemAvailable not found in meminfo")
        used = total - available
        percent = 100.0 * used / total if total > 0 else 0.0
        return {
            "total_kb": total,
            "available_kb": available,
            "used_kb": used,
            "percent": percent,
        }

    def _meminfo_field(self, label: str) -> Optional[float]:
        """Return the numeric value (kB) following ``label``, else None."""
        try:
            text = self._meminfo_reader()
        except Exception:
            return None
        for line in text.splitlines():
            if line.startswith(label):
                tokens = line.split()
                # e.g. ["MemTotal:", "16384000", "kB"]
                if len(tokens) >= 2:
                    try:
                        return float(tokens[1])
                    except ValueError:
                        return None
        return None

    # -- GPU -----------------------------------------------------------------
    def gpu(self) -> Optional[Dict[str, Any]]:
        """GPU info from ``nvidia-smi``; ``None`` on any error.

        Parses the first non-empty line, split on ``", "``, expecting exactly
        five fields: name, ``N %``, ``N MiB``, ``N MiB``, ``N`` (the last
        four converted to floats).
        """
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
            name = fields[0]
            try:
                utilization = float(fields[1].split()[0])
                memory_used = float(fields[2].split()[0])
                memory_total = float(fields[3].split()[0])
                temperature = float(fields[4].split()[0])
            except (ValueError, IndexError):
                return None
            return {
                "name": name,
                "utilization": utilization,
                "memory_used_mib": memory_used,
                "memory_total_mib": memory_total,
                "temperature_c": temperature,
            }
        return None

    # -- snapshot ------------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        """A point-in-time ``{"cpu_percent", "ram", "gpu"}`` reading.

        Never raises: a failing :meth:`ram` yields ``"ram": None``;
        :meth:`gpu` degrades to ``None`` on its own; :meth:`cpu_percent`
        yields ``0.0`` on a bad sample.
        """
        try:
            ram = self.ram()
        except Exception:
            ram = None
        return {
            "cpu_percent": self.cpu_percent(),
            "ram": ram,
            "gpu": self.gpu(),
        }


def format_size(kb: float) -> str:
    """Format a size in kB as a human-readable GiB/MiB string.

    Uses GiB when ``kb >= 1048576`` (1 GiB), else MiB, with one decimal.
    Negative input clamps to ``0.0 MiB``.
    """
    kb = max(0.0, float(kb))
    if kb >= 1048576:
        return f"{kb / 1048576:.1f} GiB"
    return f"{kb / 1024:.1f} MiB"


class SystemMonitorModule(BaseModule):
    """Live CPU / RAM / GPU usage view (id ``system``)."""

    id = "system"
    name = "System"
    icon = "🖥"
    description = "Live CPU, RAM and GPU usage."
    group = "System"

    _TICK_MS = 1000

    def build_view(self):
        """Build the three usage cards and start the 1 s ticker."""
        from gi.repository import Gtk

        self._model = SystemMonitor()
        self._timeout_id: Optional[int] = None

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        root.set_margin_top(24)
        root.set_margin_bottom(24)
        root.set_margin_start(24)
        root.set_margin_end(24)

        self._cpu_bar, self._cpu_value, self._cpu_details = self._make_card(
            root, "CPU"
        )
        self._ram_bar, self._ram_value, self._ram_details = self._make_card(
            root, "RAM"
        )
        self._gpu_bar, self._gpu_value, self._gpu_details = self._make_card(
            root, "GPU"
        )

        # Prime the view with the first sample, then keep ticking.
        self._on_tick()
        self._start_ticker()
        return root

    def _make_card(self, parent, title: str):
        """Append a usage card to ``parent``; return its bar/value/details."""
        from gi.repository import Gtk

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.get_style_context().add_class("omnify-card")

        title_label = Gtk.Label(label=title)
        title_label.get_style_context().add_class("omnify-card-title")
        title_label.set_halign(Gtk.Align.START)
        card.append(title_label)

        bar = Gtk.ProgressBar()
        card.append(bar)

        value_label = Gtk.Label(label="—")
        value_label.get_style_context().add_class("omnify-mono")
        value_label.set_halign(Gtk.Align.START)
        card.append(value_label)

        details_label = Gtk.Label(label="")
        details_label.get_style_context().add_class("omnify-dim")
        details_label.set_halign(Gtk.Align.START)
        card.append(details_label)

        parent.append(card)
        return bar, value_label, details_label

    # -- refreshing ----------------------------------------------------------
    def _on_tick(self) -> bool:
        """Take one snapshot and push it into the cards; always keep ticking."""
        try:
            snap = self._model.snapshot()
        except Exception:
            # Keep the last values on any unexpected error.
            return True
        self._refresh_cpu(snap["cpu_percent"])
        self._refresh_ram(snap["ram"])
        self._refresh_gpu(snap["gpu"])
        return True

    def _refresh_cpu(self, pct: float) -> None:
        self._cpu_bar.set_fraction(pct / 100.0)
        self._cpu_value.set_label(f"{pct:.0f}%")
        self._cpu_details.set_label("utilization")

    def _refresh_ram(self, ram: Optional[Dict[str, float]]) -> None:
        if ram is None:
            self._ram_bar.set_fraction(0.0)
            self._ram_value.set_label(tr("Not available"))
            self._ram_details.set_label("")
            return
        self._ram_bar.set_fraction(ram["percent"] / 100.0)
        self._ram_value.set_label(
            f"{format_size(ram['used_kb'])} / {format_size(ram['total_kb'])}"
        )
        self._ram_details.set_label(f"{ram['percent']:.0f}% used")

    def _refresh_gpu(self, gpu: Optional[Dict[str, Any]]) -> None:
        if gpu is None:
            self._gpu_bar.set_fraction(0.0)
            self._gpu_value.set_label(tr("Not available"))
            self._gpu_details.set_label("no GPU data")
            return
        self._gpu_bar.set_fraction(gpu["utilization"] / 100.0)
        self._gpu_value.set_label(f"{gpu['utilization']:.0f}%")
        self._gpu_details.set_label(
            f"{gpu['name']} · {format_size(gpu['memory_used_mib'] * 1024)} / "
            f"{format_size(gpu['memory_total_mib'] * 1024)} · "
            f"{gpu['temperature_c']:.0f} °C"
        )

    # -- ticker lifecycle ----------------------------------------------------
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
        self._start_ticker()

    def on_hide(self) -> None:
        self._stop_ticker()
