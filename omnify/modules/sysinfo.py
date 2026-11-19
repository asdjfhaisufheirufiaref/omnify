"""AIDA64-style system information module.

The model (:class:`SystemInfo`) is pure Python with no GTK dependency.  Every
probe is read-only and goes through an injectable callable, so tests feed it a
fake filesystem without touching the real ``/proc``, ``/sys`` or spawning a
subprocess:

- ``read_text(path)``       — text of a virtual file (raises on failure).
- ``list_dir(path)``        — entry names of a virtual directory.
- ``statvfs(path)``         — ``(total, used, free)`` bytes for a mount.
- ``uname()``               — an ``os.uname_result``-like object.
- ``environ``               — mapping of environment variables.
- ``uid_name()``            — the current user name.
- ``iface_ipv4(name)``      — primary IPv4 address of a network interface.
- ``command_output(args)``  — optional ``lspci``/``nvidia-smi`` output.
- ``now()``                 — epoch seconds (uptime/boot time/report header).

The GUI (:class:`SystemInfoModule`) is a thin layer over the model: a compact
live CPU/RAM/GPU panel plus Summary, Hardware, Storage, Network, Software and
Sensors tabs, a Refresh button and Copy/Save report actions.  Any failed probe
degrades to ``"Not available"`` / ``"Not detected"`` instead of crashing.
"""

from __future__ import annotations

import os
import platform
import re
import socket
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .. import __version__
from ..core.commands import Command
from ..core.i18n import tr
from ..core.module_context import BaseModule
from .system import SystemMonitor

#: Filesystem types worth showing in Storage (the rest are pseudo-filesystems).
_REAL_FSTYPES = frozenset(
    {
        "ext2",
        "ext3",
        "ext4",
        "btrfs",
        "xfs",
        "vfat",
        "exfat",
        "ntfs",
        "ntfs3",
        "f2fs",
        "zfs",
        "reiserfs",
        "jfs",
        "squashfs",
        "udf",
        "iso9660",
        "bcachefs",
        "overlay",
    }
)

#: Class hints that mark a PCI device as a GPU in ``lspci -mm`` output.
_GPU_CLASS_HINTS = ("VGA", "3D controller", "Display controller")

_MOUNT_ESCAPE = re.compile(r"\\([0-7]{3})")


# ---------------------------------------------------------------------------
# Default readers (the only place the real filesystem/subprocess is touched)
# ---------------------------------------------------------------------------

def _default_read_text(path: str) -> str:
    """Return the text of *path* (raises on any error)."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _default_list_dir(path: str) -> List[str]:
    """Return the entry names of *path* (raises on any error)."""
    return list(os.listdir(path))


def _default_statvfs(path: str) -> Optional[Tuple[int, int, int]]:
    """Return ``(total, used, free)`` bytes for *path*, else ``None``."""
    try:
        st = os.statvfs(path)
    except OSError:
        return None
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    return (total, total - free, free)


def _default_uid_name() -> str:
    """Return the current login name, falling back to the numeric UID."""
    try:
        import pwd

        return pwd.getpwuid(os.getuid()).pw_name
    except Exception:
        return str(os.getuid())


def _default_iface_ipv4(iface: str) -> Optional[str]:
    """Return the primary IPv4 address of *iface* via ``SIOCGIFADDR``."""
    try:
        import fcntl
        import struct

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            request = struct.pack("256s", iface[:15].encode("utf-8"))
            packed = fcntl.ioctl(sock.fileno(), 0x8915, request)
            return socket.inet_ntoa(packed[20:24])
        finally:
            sock.close()
    except Exception:
        return None


def _default_command_output(args: Sequence[str]) -> str:
    """Run a short read-only command and return stdout (raises on failure)."""
    result = subprocess.run(
        list(args),
        capture_output=True,
        text=True,
        timeout=3,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{args[0]} exited with code {result.returncode}")
    return result.stdout or ""


# ---------------------------------------------------------------------------
# Parsers and formatters (module-level so tests can target them directly)
# ---------------------------------------------------------------------------

def parse_os_release(text: str) -> Dict[str, str]:
    """Parse ``/etc/os-release`` into a plain dict (quotes/escapes handled)."""
    data: Dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            quote = value[0]
            value = value[1:-1]
            if quote == '"':
                value = value.replace('\\"', '"').replace("\\\\", "\\")
        data[key.strip()] = value
    return data


def parse_meminfo(text: str) -> Dict[str, float]:
    """Parse numeric ``/proc/meminfo`` fields in kilobytes.

    ``MemTotal: 16384000 kB`` becomes ``{"MemTotal": 16384000.0}``.  Lines
    without a numeric first token are skipped.
    """
    data: Dict[str, float] = {}
    for line in text.splitlines():
        key, sep, rest = line.partition(":")
        if not sep:
            continue
        tokens = rest.split()
        if not tokens:
            continue
        try:
            data[key.strip()] = float(tokens[0])
        except ValueError:
            continue
    return data


def parse_cpuinfo(text: str) -> Dict[str, Any]:
    """Parse the interesting fields of ``/proc/cpuinfo``.

    Returns ``model``, ``vendor``, ``threads`` (``processor`` entries),
    ``physical_cores`` (``cpu cores`` × sockets, when available), ``cache``
    and ``mhz`` (first ``cpu MHz`` value).
    """
    model = ""
    vendor = ""
    cache = ""
    mhz: Optional[float] = None
    threads = 0
    cores_per_socket: Optional[int] = None
    sockets: set[str] = set()

    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key = key.strip().lower()
        value = value.strip()
        if key == "processor":
            threads += 1
        elif key == "model name" and not model:
            model = value
        elif key == "vendor_id" and not vendor:
            vendor = value
        elif key == "cache size" and not cache:
            cache = value
        elif key == "cpu mhz" and mhz is None:
            try:
                mhz = float(value)
            except ValueError:
                pass
        elif key == "cpu cores" and cores_per_socket is None:
            try:
                cores_per_socket = int(value)
            except ValueError:
                pass
        elif key == "physical id":
            sockets.add(value)

    physical: Optional[int] = cores_per_socket
    if cores_per_socket is not None and sockets:
        physical = cores_per_socket * len(sockets)
    return {
        "model": " ".join(model.split()),
        "vendor": vendor,
        "threads": threads,
        "physical_cores": physical,
        "cache": cache,
        "mhz": mhz,
    }


def parse_mounts(text: str) -> List[Dict[str, str]]:
    """Parse ``/proc/mounts`` into device/mount/fstype/options dicts.

    Octal escapes such as ``\\040`` in mount points are decoded.
    """
    mounts: List[Dict[str, str]] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        mounts.append(
            {
                "device": _unescape_mount(parts[0]),
                "mount": _unescape_mount(parts[1]),
                "fstype": parts[2],
                "options": parts[3],
            }
        )
    return mounts


def _unescape_mount(value: str) -> str:
    """Decode the octal escapes used by ``/proc/mounts``."""
    return _MOUNT_ESCAPE.sub(lambda match: chr(int(match.group(1), 8)), value)


def parse_net_dev(text: str) -> Dict[str, Dict[str, float]]:
    """Parse ``/proc/net/dev`` into ``{iface: {"rx_bytes", "tx_bytes"}}``."""
    data: Dict[str, Dict[str, float]] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        iface, rest = line.split(":", 1)
        fields = rest.split()
        if len(fields) < 9:
            continue
        try:
            data[iface.strip()] = {
                "rx_bytes": float(fields[0]),
                "tx_bytes": float(fields[8]),
            }
        except ValueError:
            continue
    return data


def parse_resolv_conf(text: str) -> List[str]:
    """Return the unique DNS servers listed in ``/etc/resolv.conf``."""
    servers: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        tokens = stripped.split()
        if len(tokens) >= 2 and tokens[0] == "nameserver":
            if tokens[1] not in servers:
                servers.append(tokens[1])
    return servers


def parse_default_gateway(text: str) -> Optional[str]:
    """Return the default IPv4 gateway from ``/proc/net/route`` (or None).

    The kernel stores addresses as little-endian hex, so ``0102A8C0`` is
    ``192.168.2.1``.
    """
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 3 or fields[1] != "00000000":
            continue
        try:
            raw = int(fields[2], 16)
        except ValueError:
            continue
        octets = [str((raw >> (8 * index)) & 0xFF) for index in range(4)]
        return ".".join(octets)
    return None


def parse_lspci_gpus(text: str) -> List[str]:
    """Return GPU names from ``lspci -mm`` (or plain ``lspci``) output."""
    names: List[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if not any(hint.lower() in line.lower() for hint in _GPU_CLASS_HINTS):
            continue
        quoted = re.findall(r'"([^"]*)"', line)
        if len(quoted) >= 3:
            vendor, device = quoted[1].strip(), quoted[2].strip()
            name = " ".join(part for part in (vendor, device) if part)
        else:
            match = re.search(
                r"(?:VGA compatible controller|3D controller|Display controller)"
                r"\s*:\s*(.*)",
                line,
                re.IGNORECASE,
            )
            name = match.group(1).strip() if match else ""
            name = re.sub(
                r"\s*\[[0-9a-fA-F]{4}:[0-9a-fA-F]{4}\].*$", "", name
            )
        if name and name not in names:
            names.append(name)
    return names


def format_bytes(value: Optional[float]) -> str:
    """Format a byte count with binary units (``"15.6 GiB"``)."""
    if value is None:
        return "Not available"
    amount = max(0.0, float(value))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024.0 or unit == "TiB":
            if unit == "B":
                return f"{int(amount)} B"
            return f"{amount:.1f} {unit}"
        amount /= 1024.0
    return f"{amount:.1f} TiB"


def format_uptime(seconds: Optional[float]) -> str:
    """Format an uptime as ``45s`` / ``12m 5s`` / ``1h 2m`` / ``1d 3h 4m``."""
    if seconds is None:
        return "Not available"
    total = max(0, int(seconds))
    days, total = divmod(total, 86400)
    hours, total = divmod(total, 3600)
    minutes, secs = divmod(total, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _format_speed_mbps(value: str) -> str:
    """Format a ``/sys/class/net/<iface>/speed`` value, or ``""``."""
    try:
        mbps = int(value.strip())
    except (TypeError, ValueError):
        return ""
    if mbps <= 0:
        return ""
    if mbps >= 1000:
        return f"{mbps / 1000:g} Gb/s"
    return f"{mbps} Mb/s"


# ---------------------------------------------------------------------------
# Pure model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class InfoRow:
    """One label/value pair in a system report."""

    label: str
    value: str


@dataclass(frozen=True)
class InfoSection:
    """A titled group of :class:`InfoRow` objects."""

    key: str
    title: str
    rows: Tuple[InfoRow, ...]


class SystemInfo:
    """Read-only system information collector with injectable probes."""

    #: Display order of the report sections.
    SECTION_TITLES: Tuple[Tuple[str, str], ...] = (
        ("summary", "Summary"),
        ("hardware", "Hardware"),
        ("storage", "Storage"),
        ("network", "Network"),
        ("software", "Software"),
        ("sensors", "Sensors"),
    )

    def __init__(
        self,
        read_text: Callable[[str], str] = _default_read_text,
        list_dir: Callable[[str], Iterable[str]] = _default_list_dir,
        statvfs: Callable[[str], Optional[Tuple[int, int, int]]] = _default_statvfs,
        uname: Callable[[], Any] = os.uname,
        environ: Optional[Mapping[str, str]] = None,
        uid_name: Callable[[], str] = _default_uid_name,
        iface_ipv4: Callable[[str], Optional[str]] = _default_iface_ipv4,
        command_output: Callable[[Sequence[str]], str] = _default_command_output,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._read_text = read_text
        self._list_dir = list_dir
        self._statvfs = statvfs
        self._uname = uname
        self._environ = dict(environ) if environ is not None else dict(os.environ)
        self._uid_name = uid_name
        self._iface_ipv4 = iface_ipv4
        self._command_output = command_output
        self._now = now

    # -- safe access ---------------------------------------------------------
    def _read(self, path: str, default: str = "") -> str:
        try:
            return self._read_text(path)
        except Exception:
            return default

    def _read_stripped(self, path: str, default: str = "") -> str:
        return self._read(path, default).strip()

    def _list(self, path: str) -> List[str]:
        try:
            return sorted(self._list_dir(path))
        except Exception:
            return []

    def _uname_info(self) -> Any:
        try:
            return self._uname()
        except Exception:
            return SimpleNamespace(
                sysname="Linux", nodename="", release="", version="", machine=""
            )

    def _os_release(self) -> Dict[str, str]:
        data = parse_os_release(self._read("/etc/os-release"))
        if not data:
            data = parse_os_release(self._read("/usr/lib/os-release"))
        return data

    def _pretty_os(self) -> str:
        data = self._os_release()
        pretty = data.get("PRETTY_NAME", "").strip()
        if pretty:
            return pretty
        name = data.get("NAME", "").strip() or "Linux"
        version = data.get("VERSION", "").strip() or data.get("VERSION_ID", "").strip()
        return f"{name} {version}".strip()

    def _desktop(self) -> str:
        for key in ("XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP", "DESKTOP_SESSION"):
            value = self._environ.get(key, "").strip()
            if value:
                return value.replace(":", ", ")
        return "Not available"

    def _session_type(self) -> str:
        return self._environ.get("XDG_SESSION_TYPE", "").strip() or "Not available"

    def _user_name(self) -> str:
        try:
            value = str(self._uid_name()).strip()
        except Exception:
            value = ""
        return value or "Not available"

    # -- time ----------------------------------------------------------------
    def boot_time(self) -> Optional[float]:
        """Return the boot epoch seconds from ``/proc/stat`` or ``None``."""
        for line in self._read("/proc/stat").splitlines():
            if line.startswith("btime "):
                try:
                    return float(line.split()[1])
                except (IndexError, ValueError):
                    return None
        return None

    def uptime_seconds(self) -> Optional[float]:
        """Return uptime from ``/proc/uptime``, falling back to ``btime``."""
        parts = self._read("/proc/uptime").split()
        if parts:
            try:
                return max(0.0, float(parts[0]))
            except ValueError:
                pass
        boot = self.boot_time()
        if boot is None:
            return None
        try:
            return max(0.0, self._now() - boot)
        except Exception:
            return None

    # -- summary -------------------------------------------------------------
    def summary(self) -> List[InfoRow]:
        """Host, OS, kernel, architecture, uptime, session and boot time."""
        info = self._uname_info()
        boot = self.boot_time()
        rows = [
            InfoRow("Host name", getattr(info, "nodename", "") or "Not available"),
            InfoRow("Operating system", self._pretty_os()),
            InfoRow("Kernel", getattr(info, "release", "") or "Not available"),
            InfoRow("Architecture", getattr(info, "machine", "") or "Not available"),
            InfoRow("Uptime", format_uptime(self.uptime_seconds())),
            InfoRow("User", self._user_name()),
            InfoRow("Desktop", self._desktop()),
            InfoRow("Session", self._session_type()),
        ]
        if boot is not None:
            try:
                rows.append(
                    InfoRow(
                        "Boot time",
                        datetime.fromtimestamp(boot).strftime("%Y-%m-%d %H:%M"),
                    )
                )
            except (OverflowError, OSError, ValueError):
                pass
        return rows

    # -- hardware ------------------------------------------------------------
    def _gpu_names(self) -> List[str]:
        try:
            names = parse_lspci_gpus(self._command_output(["lspci", "-mm"]))
        except Exception:
            names = []
        if names:
            return names
        try:
            text = self._command_output(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"]
            )
        except Exception:
            return []
        return [line.strip() for line in text.splitlines() if line.strip()]

    def _dmi(self, name: str) -> str:
        return self._read_stripped(f"/sys/class/dmi/id/{name}")

    def hardware(self) -> List[InfoRow]:
        """CPU, memory, motherboard, BIOS and GPU details."""
        cpu = parse_cpuinfo(self._read("/proc/cpuinfo"))
        mem = parse_meminfo(self._read("/proc/meminfo"))

        rows: List[InfoRow] = [
            InfoRow("CPU", cpu["model"] or "Not available"),
        ]
        physical = cpu["physical_cores"]
        threads = cpu["threads"]
        if physical and threads:
            if physical == threads:
                rows.append(InfoRow("CPU cores", f"{threads} threads"))
            else:
                rows.append(
                    InfoRow("CPU cores", f"{physical} cores / {threads} threads")
                )
        elif threads:
            rows.append(InfoRow("CPU cores", f"{threads} threads"))

        max_khz = 0
        for name in ("cpuinfo_max_freq", "scaling_max_freq"):
            try:
                max_khz = int(self._read_stripped(
                    f"/sys/devices/system/cpu/cpu0/cpufreq/{name}", "0"
                ))
            except ValueError:
                max_khz = 0
            if max_khz:
                break
        frequency_parts = []
        if cpu["mhz"]:
            frequency_parts.append(f"{cpu['mhz']:.0f} MHz")
        if max_khz:
            maximum = f"{max_khz / 1000:.0f} MHz"
            if maximum not in frequency_parts:
                frequency_parts.append(f"max {maximum}")
        if frequency_parts:
            rows.append(InfoRow("CPU frequency", " · ".join(frequency_parts)))
        if cpu["cache"]:
            rows.append(InfoRow("CPU cache", cpu["cache"]))
        if cpu["vendor"]:
            rows.append(InfoRow("CPU vendor", cpu["vendor"]))

        system = " ".join(
            part
            for part in (self._dmi("sys_vendor"), self._dmi("product_name"))
            if part
        )
        if system:
            rows.append(InfoRow("System", system))
        board = " ".join(
            part
            for part in (self._dmi("board_vendor"), self._dmi("board_name"))
            if part
        )
        if board:
            rows.append(InfoRow("Motherboard", board))
        bios = " ".join(
            part
            for part in (
                self._dmi("bios_vendor"),
                self._dmi("bios_version"),
                self._dmi("bios_date"),
            )
            if part
        )
        if bios:
            rows.append(InfoRow("BIOS", bios))

        total_kb = mem.get("MemTotal")
        available_kb = mem.get("MemAvailable")
        if total_kb is not None:
            if available_kb is not None:
                value = (
                    f"{format_bytes(total_kb * 1024)} total · "
                    f"{format_bytes(available_kb * 1024)} available"
                )
            else:
                value = f"{format_bytes(total_kb * 1024)} total"
            rows.append(InfoRow("Memory", value))
        else:
            rows.append(InfoRow("Memory", "Not available"))

        swap_kb = mem.get("SwapTotal", 0.0)
        if swap_kb:
            rows.append(
                InfoRow(
                    "Swap",
                    f"{format_bytes(swap_kb * 1024)} total · "
                    f"{format_bytes(mem.get('SwapFree', 0.0) * 1024)} free",
                )
            )
        else:
            rows.append(InfoRow("Swap", "Not configured"))

        gpus = self._gpu_names()
        rows.append(InfoRow("GPU", ", ".join(gpus) if gpus else "Not detected"))
        return rows

    # -- storage -------------------------------------------------------------
    def _disks(self) -> List[InfoRow]:
        rows: List[InfoRow] = []
        for name in self._list("/sys/block"):
            if name.startswith(("loop", "ram", "zram", "dm-", "md", "sr")):
                continue
            base = f"/sys/block/{name}"
            vendor = self._read_stripped(f"{base}/device/vendor")
            model = self._read_stripped(f"{base}/device/model")
            size_sectors = self._read_stripped(f"{base}/size", "0")
            try:
                size_bytes = max(0, int(size_sectors)) * 512
            except ValueError:
                size_bytes = 0
            if not model and not size_bytes:
                continue
            parts = [" ".join(part for part in (vendor, model) if part)]
            if size_bytes:
                parts.append(format_bytes(size_bytes))
            rotational = self._read_stripped(f"{base}/queue/rotational")
            if rotational == "1":
                parts.append("HDD")
            elif rotational == "0":
                parts.append("SSD")
            if self._read_stripped(f"{base}/removable") == "1":
                parts.append("Removable")
            rows.append(InfoRow(f"Disk ({name})", " · ".join(parts)))
        return rows

    def storage(self) -> List[InfoRow]:
        """Disks and real mounted filesystems with usage."""
        rows = self._disks()
        seen: set[str] = set()
        mounts: List[Tuple[str, InfoRow]] = []
        for entry in parse_mounts(self._read("/proc/mounts")):
            mount = entry["mount"]
            if entry["fstype"] not in _REAL_FSTYPES or mount in seen:
                continue
            try:
                stat = self._statvfs(mount)
            except Exception:
                stat = None
            if stat is None:
                continue
            total, used, free = stat
            if total <= 0:
                continue
            seen.add(mount)
            percent = 100.0 * used / total
            mounts.append(
                (
                    mount,
                    InfoRow(
                        f"Mount {mount}",
                        f"{entry['fstype']} · {format_bytes(free)} free of "
                        f"{format_bytes(total)} ({percent:.0f}% used) · "
                        f"{entry['device']}",
                    ),
                )
            )
        mounts.sort(key=lambda item: (item[0] != "/", item[0]))
        rows.extend(row for _mount, row in mounts)
        if not rows:
            rows.append(InfoRow("Storage", "No storage devices detected"))
        return rows

    # -- network -------------------------------------------------------------
    def _interface_row(
        self,
        iface: str,
        traffic: Mapping[str, Mapping[str, float]],
    ) -> InfoRow:
        base = f"/sys/class/net/{iface}"
        state = self._read_stripped(f"{base}/operstate") or "unknown"
        parts = [state]
        try:
            ipv4 = self._iface_ipv4(iface)
        except Exception:
            ipv4 = None
        if ipv4:
            parts.append(ipv4)
        mac = self._read_stripped(f"{base}/address")
        if mac:
            parts.append(mac)
        speed = _format_speed_mbps(self._read_stripped(f"{base}/speed"))
        if speed:
            parts.append(speed)
        stats = traffic.get(iface)
        if stats:
            parts.append(
                f"↓ {format_bytes(stats['rx_bytes'])} "
                f"↑ {format_bytes(stats['tx_bytes'])}"
            )
        return InfoRow(iface, " · ".join(parts))

    def network(self) -> List[InfoRow]:
        """Interfaces, addresses, link speed, traffic, gateway and DNS."""
        info = self._uname_info()
        rows = [
            InfoRow("Host name", getattr(info, "nodename", "") or "Not available")
        ]
        traffic = parse_net_dev(self._read("/proc/net/dev"))
        interfaces = [name for name in self._list("/sys/class/net") if name != "lo"]
        if not interfaces:
            interfaces = self._list("/sys/class/net")
        rows.extend(self._interface_row(name, traffic) for name in interfaces)
        if not interfaces:
            rows.append(InfoRow("Interfaces", "No network interfaces detected"))

        gateway = parse_default_gateway(self._read("/proc/net/route"))
        if gateway:
            rows.append(InfoRow("Default gateway", gateway))
        servers = parse_resolv_conf(self._read("/etc/resolv.conf"))
        if servers:
            rows.append(InfoRow("DNS", ", ".join(servers[:3])))
        return rows

    # -- software ------------------------------------------------------------
    def software(self) -> List[InfoRow]:
        """Distribution, kernel, desktop, locale and runtime versions."""
        info = self._uname_info()
        release = self._os_release()
        distribution = " ".join(
            part
            for part in (
                release.get("ID", "").strip(),
                release.get("VERSION_ID", "").strip(),
            )
            if part
        )
        locale = (
            self._environ.get("LC_ALL", "").strip()
            or self._environ.get("LC_MESSAGES", "").strip()
            or self._environ.get("LANG", "").strip()
            or "Not available"
        )
        timezone = (
            self._environ.get("TZ", "").strip()
            or self._read_stripped("/etc/timezone")
            or "Not available"
        )
        return [
            InfoRow("Operating system", self._pretty_os()),
            InfoRow("Distribution", distribution or "Not available"),
            InfoRow("Kernel", getattr(info, "release", "") or "Not available"),
            InfoRow("Architecture", getattr(info, "machine", "") or "Not available"),
            InfoRow("Desktop", self._desktop()),
            InfoRow("Session type", self._session_type()),
            InfoRow("Shell", self._environ.get("SHELL", "").strip() or "Not available"),
            InfoRow("Locale", locale),
            InfoRow("Timezone", timezone),
            InfoRow("Python", platform.python_version()),
            InfoRow("Omnify", __version__),
        ]

    # -- sensors -------------------------------------------------------------
    def _hwmon_rows(self) -> Tuple[List[InfoRow], bool]:
        rows: List[InfoRow] = []
        found = False
        for hwmon in self._list("/sys/class/hwmon"):
            base = f"/sys/class/hwmon/{hwmon}"
            name = self._read_stripped(f"{base}/name") or hwmon
            for entry in self._list(base):
                if entry.startswith("temp") and entry.endswith("_input"):
                    prefix = entry[: -len("_input")]
                    raw = self._read_stripped(f"{base}/{entry}")
                    try:
                        celsius = float(raw) / 1000.0
                    except ValueError:
                        continue
                    if not 0.0 <= celsius <= 150.0:
                        continue
                    label = self._read_stripped(f"{base}/{prefix}_label") or prefix
                    rows.append(
                        InfoRow(f"{name} · {label}", f"{celsius:.1f} °C")
                    )
                    found = True
                elif entry.startswith("fan") and entry.endswith("_input"):
                    prefix = entry[: -len("_input")]
                    raw = self._read_stripped(f"{base}/{entry}")
                    try:
                        rpm = float(raw)
                    except ValueError:
                        continue
                    if not 0.0 <= rpm <= 25000.0:
                        continue
                    label = self._read_stripped(f"{base}/{prefix}_label") or prefix
                    rows.append(InfoRow(f"{name} · {label}", f"{rpm:.0f} RPM"))
                    found = True
        return rows, found

    def _thermal_rows(self) -> List[InfoRow]:
        rows: List[InfoRow] = []
        for zone in self._list("/sys/class/thermal"):
            if not zone.startswith("thermal_zone"):
                continue
            base = f"/sys/class/thermal/{zone}"
            raw = self._read_stripped(f"{base}/temp")
            try:
                celsius = float(raw) / 1000.0
            except ValueError:
                continue
            if not 0.0 <= celsius <= 150.0:
                continue
            label = self._read_stripped(f"{base}/type") or zone
            rows.append(InfoRow(label, f"{celsius:.1f} °C"))
        return rows

    def _battery_rows(self) -> List[InfoRow]:
        rows: List[InfoRow] = []
        for name in self._list("/sys/class/power_supply"):
            if not name.startswith("BAT"):
                continue
            base = f"/sys/class/power_supply/{name}"
            capacity = self._read_stripped(f"{base}/capacity")
            status = self._read_stripped(f"{base}/status")
            parts = []
            if capacity:
                parts.append(f"{capacity}%")
            if status:
                parts.append(status)
            if parts:
                rows.append(InfoRow(f"Battery ({name})", " · ".join(parts)))
        return rows

    def sensors(self) -> List[InfoRow]:
        """Temperatures, fans, thermal zones and battery state."""
        rows, found = self._hwmon_rows()
        if not found:
            rows.extend(self._thermal_rows())
        rows.extend(self._battery_rows())
        if not rows:
            rows.append(InfoRow("Sensors", "No hardware sensors detected"))
        return rows

    # -- aggregation ---------------------------------------------------------
    def collect(self) -> List[InfoSection]:
        """Collect every section; no probe failure can escape this method."""
        builders = {
            "summary": self.summary,
            "hardware": self.hardware,
            "storage": self.storage,
            "network": self.network,
            "software": self.software,
            "sensors": self.sensors,
        }
        sections: List[InfoSection] = []
        for key, title in self.SECTION_TITLES:
            try:
                rows = builders[key]()
            except Exception:
                rows = [InfoRow("Status", "Not available")]
            sections.append(InfoSection(key, title, tuple(rows)))
        return sections

    def report(
        self,
        sections: Optional[Sequence[InfoSection]] = None,
        translate: Optional[Callable[[str], str]] = None,
    ) -> str:
        """Render a plain-text report suitable for Copy/Save."""
        tr_fn = translate or (lambda text: text)
        if sections is None:
            sections = self.collect()
        try:
            timestamp = datetime.fromtimestamp(self._now()).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        except Exception:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            tr_fn("Omnify System Report"),
            "=" * 20,
            f"{tr_fn('Generated')}: {timestamp}",
            "",
        ]
        for section in sections:
            title = tr_fn(section.title)
            lines.append(title)
            lines.append("-" * len(title))
            for row in section.rows:
                lines.append(f"{tr_fn(row.label)}: {tr_fn(row.value)}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# GTK view
# ---------------------------------------------------------------------------

class SystemInfoModule(BaseModule):
    """AIDA64-style system information view (id ``sysinfo``)."""

    id = "sysinfo"
    name = "System Information"
    icon = "\U0001F4BB"
    description = (
        "Hardware, software, storage, network and sensor details — an "
        "AIDA64-style system report."
    )
    group = "System"

    _TICK_MS = 1000

    # -- commands ------------------------------------------------------------
    def commands(self):
        def ready() -> bool:
            return getattr(self, "_info_stack", None) is not None

        def show_section(key: str):
            def invoke() -> bool:
                if not ready():
                    return False
                self._info_stack.set_visible_child_name(key)
                button = getattr(self, "_section_buttons", {}).get(key)
                if button is not None:
                    button.set_active(True)
                return True

            return invoke

        section_commands = tuple(
            Command(
                f"sysinfo.section.{key}",
                f"System Information: {title} tab",
                show_section(key),
                category="System Information",
                default_keys=(f"ctrl+{index}",),
                scope="sysinfo",
                when=ready,
            )
            for index, (key, title) in enumerate(SystemInfo.SECTION_TITLES, start=1)
        )
        return (
            Command(
                "sysinfo.refresh",
                "Refresh System Information",
                self._command_refresh,
                category="System Information",
                default_keys=("f5",),
                scope="sysinfo",
                when=ready,
            ),
            Command(
                "sysinfo.copy_report",
                "Copy system report",
                self._command_copy_report,
                category="System Information",
                default_keys=("ctrl+shift+c",),
                scope="sysinfo",
                when=ready,
            ),
            Command(
                "sysinfo.save_report",
                "Save system report",
                self._command_save_report,
                category="System Information",
                default_keys=("ctrl+s",),
                scope="sysinfo",
                when=ready,
            ),
            *section_commands,
        )

    def _command_refresh(self) -> bool:
        if getattr(self, "_info_stack", None) is None:
            return False
        self._refresh_info()
        self._on_tick()
        return True

    def _command_copy_report(self) -> bool:
        if getattr(self, "_info_stack", None) is None:
            return False
        self._copy_report()
        return True

    def _command_save_report(self) -> bool:
        if getattr(self, "_info_stack", None) is None:
            return False
        self._save_report()
        return True

    # -- construction --------------------------------------------------------
    def build_view(self):
        from gi.repository import Gtk

        self._info = SystemInfo()
        self._monitor = SystemMonitor()
        self._timeout_id: Optional[int] = None
        self._report_pending: Optional[str] = None
        self._section_boxes: Dict[str, Any] = {}
        self._section_buttons: Dict[str, Any] = {}

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        root.set_margin_top(12)
        root.set_margin_bottom(12)
        root.set_margin_start(12)
        root.set_margin_end(12)

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        refresh = Gtk.Button(label=tr("Refresh"))
        refresh.connect("clicked", lambda _button: self._command_refresh())
        copy_button = Gtk.Button(label=tr("Copy report"))
        copy_button.connect("clicked", lambda _button: self._copy_report())
        save_button = Gtk.Button(label=tr("Save report…"))
        save_button.connect("clicked", lambda _button: self._save_report())
        toolbar.append(refresh)
        toolbar.append(copy_button)
        toolbar.append(save_button)
        self._status = Gtk.Label(label="")
        self._status.get_style_context().add_class("omnify-dim")
        self._status.set_hexpand(True)
        self._status.set_halign(Gtk.Align.END)
        toolbar.append(self._status)
        root.append(toolbar)

        root.append(self._build_live_panel())

        self._info_stack = Gtk.Stack()
        self._info_stack.set_vexpand(True)
        self._info_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        switcher = Gtk.StackSwitcher()
        switcher.set_stack(self._info_stack)
        root.append(switcher)
        root.append(self._info_stack)

        for key, title in SystemInfo.SECTION_TITLES:
            scroller = Gtk.ScrolledWindow()
            scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            scroller.set_vexpand(True)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            scroller.set_child(box)
            self._info_stack.add_titled(scroller, key, tr(title))
            self._section_boxes[key] = box

        self._root = root
        self._refresh_info()
        self._on_tick()
        self._start_ticker()
        return root

    def _build_live_panel(self):
        """A compact three-card live CPU/RAM/GPU row."""
        from gi.repository import Gtk, Pango

        panel = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        panel.set_homogeneous(True)
        self._live: Dict[str, Dict[str, Any]] = {}
        for key, title in (("cpu", "CPU"), ("ram", "RAM"), ("gpu", "GPU")):
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            card.get_style_context().add_class("omnify-card")
            header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            name = Gtk.Label(label=tr(title), xalign=0.0)
            name.get_style_context().add_class("omnify-card-title")
            name.set_hexpand(True)
            value = Gtk.Label(label="—")
            value.get_style_context().add_class("omnify-mono")
            header.append(name)
            header.append(value)
            bar = Gtk.ProgressBar()
            details = Gtk.Label(label="", xalign=0.0)
            details.get_style_context().add_class("omnify-dim")
            details.set_ellipsize(Pango.EllipsizeMode.END)
            card.append(header)
            card.append(bar)
            card.append(details)
            panel.append(card)
            self._live[key] = {"bar": bar, "value": value, "details": details}
        return panel

    # -- section rendering ---------------------------------------------------
    def _refresh_info(self) -> None:
        """Re-collect the model and rebuild every section's rows."""
        if getattr(self, "_info_stack", None) is None:
            return
        from gi.repository import Gtk

        sections = {section.key: section for section in self._info.collect()}
        for key, box in self._section_boxes.items():
            while True:
                child = box.get_first_child()
                if child is None:
                    break
                box.remove(child)
            section = sections.get(key)
            if section is None:
                continue
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            card.get_style_context().add_class("omnify-card")
            for index, row in enumerate(section.rows):
                if index:
                    separator = Gtk.Separator()
                    separator.set_margin_start(12)
                    separator.set_margin_end(12)
                    card.append(separator)
                card.append(self._row_widget(row.label, row.value))
            box.append(card)
        try:
            stamp = time.strftime("%H:%M:%S")
        except Exception:
            stamp = ""
        self._updated_at = stamp
        self._update_status()

    def _update_status(self) -> None:
        stamp = getattr(self, "_updated_at", "")
        self._status.set_label(f"{tr('Updated')} {stamp}" if stamp else "")

    def retranslate(self) -> None:
        """Re-apply the language to dynamic status/live labels."""
        super().retranslate()
        self._update_status()
        if getattr(self, "_live", None):
            self._on_tick()

    def _row_widget(self, label: str, value: str):
        from gi.repository import Gtk

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.set_margin_top(7)
        row.set_margin_bottom(7)
        row.set_margin_start(12)
        row.set_margin_end(12)
        name = Gtk.Label(label=self._translate_label(label), xalign=0.0)
        name.get_style_context().add_class("omnify-dim")
        name.set_size_request(180, -1)
        name.set_valign(Gtk.Align.START)
        content = Gtk.Label(label=tr(value), xalign=0.0)
        content.get_style_context().add_class("omnify-mono")
        content.set_hexpand(True)
        content.set_wrap(True)
        content.set_selectable(True)
        content.set_valign(Gtk.Align.START)
        row.append(name)
        row.append(content)
        return row

    # -- live panel ----------------------------------------------------------
    def _on_tick(self) -> bool:
        try:
            snapshot = self._monitor.snapshot()
        except Exception:
            return True
        self._refresh_cpu(snapshot.get("cpu_percent", 0.0))
        self._refresh_ram(snapshot.get("ram"))
        self._refresh_gpu(snapshot.get("gpu"))
        return True

    def _refresh_cpu(self, percent: float) -> None:
        panel = self._live["cpu"]
        panel["bar"].set_fraction(max(0.0, min(1.0, percent / 100.0)))
        panel["value"].set_label(f"{percent:.0f}%")
        panel["details"].set_label(tr("utilization"))

    def _refresh_ram(self, ram: Optional[Dict[str, float]]) -> None:
        panel = self._live["ram"]
        if not ram:
            panel["bar"].set_fraction(0.0)
            panel["value"].set_label(tr("Not available"))
            panel["details"].set_label("")
            return
        panel["bar"].set_fraction(max(0.0, min(1.0, ram["percent"] / 100.0)))
        panel["value"].set_label(
            f"{format_bytes(ram['used_kb'] * 1024)} / "
            f"{format_bytes(ram['total_kb'] * 1024)}"
        )
        panel["details"].set_label(
            tr("{percent}% of memory used", percent=f"{ram['percent']:.0f}")
        )

    def _refresh_gpu(self, gpu: Optional[Dict[str, Any]]) -> None:
        panel = self._live["gpu"]
        if not gpu:
            panel["bar"].set_fraction(0.0)
            panel["value"].set_label(tr("Not available"))
            panel["details"].set_label(tr("No GPU data"))
            return
        panel["bar"].set_fraction(max(0.0, min(1.0, gpu["utilization"] / 100.0)))
        panel["value"].set_label(f"{gpu['utilization']:.0f}%")
        panel["details"].set_label(
            f"{gpu['name']} · {gpu['temperature_c']:.0f} °C"
        )

    # -- ticker lifecycle ----------------------------------------------------
    def _start_ticker(self) -> None:
        from gi.repository import GLib

        if self._timeout_id is None:
            self._timeout_id = GLib.timeout_add(self._TICK_MS, self._on_tick)

    def _stop_ticker(self) -> None:
        from gi.repository import GLib

        if self._timeout_id is not None:
            GLib.source_remove(self._timeout_id)
            self._timeout_id = None

    def on_show(self) -> None:
        if getattr(self, "_monitor", None) is None:
            return
        self._start_ticker()

    def on_hide(self) -> None:
        self._stop_ticker()

    # -- report actions ------------------------------------------------------
    def _translate_label(self, text: str) -> str:
        """Translate a row label, including ``Disk (…)``-style prefixes."""
        for word in ("Disk", "Mount", "Battery"):
            if text.startswith(f"{word} ") or text.startswith(f"{word} ("):
                return tr(word) + text[len(word):]
        return tr(text)

    def _report_text(self) -> str:
        try:
            return self._info.report(translate=self._translate_label)
        except Exception as exc:
            return f"{tr('Omnify System Report')}\n\n{exc}\n"

    def _copy_report(self) -> None:
        text = self._report_text()
        try:
            from gi.repository import GObject

            clipboard = self._root.get_clipboard()
            value = GObject.Value()
            value.init(GObject.TYPE_STRING)
            value.set_string(text)
            clipboard.set(value)
        except Exception as exc:
            self._notify(tr("Could not copy the report: {error}", error=str(exc)))
            return
        self._notify(tr("System report copied"))

    def _save_report(self) -> None:
        text = self._report_text()
        self._report_pending = text
        try:
            from gi.repository import Gtk

            dialog = Gtk.FileDialog()
            dialog.set_title(tr("Save system report"))
            dialog.set_initial_name(
                time.strftime("omnify-system-report-%Y%m%d-%H%M%S.txt")
            )
            dialog.save(self._root.get_root(), None, self._on_save_selected)
        except Exception:
            self._notify(tr("Could not open the save dialog"))

    def _on_save_selected(self, dialog, result) -> None:
        try:
            file = dialog.save_finish(result)
        except Exception:
            self._report_pending = None
            return
        path = file.get_path() if file is not None else None
        text = self._report_pending
        self._report_pending = None
        if not path or text is None:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        except OSError as exc:
            self._notify(tr("Could not save the report: {error}", error=str(exc)))
            return
        self._notify(tr("System report saved to {path}", path=path))

    def _notify(self, message: str) -> None:
        try:
            self.ctx.notify(message)
        except Exception:
            pass
