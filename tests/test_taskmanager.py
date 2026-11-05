"""Headless tests for the Task Manager model (no GTK, no real /proc).

Run with:  python3 tests/test_taskmanager.py

Every reader is a fake closure over a canned string, the clock is a fake
monotonic, and kill is a recorder — the real ``/proc`` tree, ``nvidia-smi``,
``systemctl``, ``loginctl`` and ``nethogs`` are never touched.
"""

import os
import signal
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omnify.core.theme import resolve_color  # noqa: E402
from omnify.modules.taskmanager import (  # noqa: E402
    ProcessInfo,
    RingBuffer,
    TaskManager,
    StartupEntry,
    filter_processes,
    format_bytes,
    format_speed,
    format_uptime,
    parse_btime,
    parse_cpuinfo,
    parse_diskstats,
    parse_desktop,
    parse_gpu_apps,
    parse_loadavg,
    parse_net_dev,
    parse_nethogs,
    parse_proc_stat,
    parse_service_pids,
    parse_services,
    parse_sessions,
    parse_status_fields,
    parse_proc_io,
    sorted_by,
)


class FakeReader:
    """A mutable zero-arg reader whose text the test can swap at will."""

    def __init__(self, text: str = "") -> None:
        self.text = text

    def __call__(self) -> str:
        return self.text


class FakeClock:
    """A mutable monotonic clock."""

    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


# ---------------------------------------------------------------------------
# Canned /proc data
# ---------------------------------------------------------------------------

CPUINFO_4 = (
    "processor\t: 0\nmodel name\t: Fake CPU 4C\n"
    "processor\t: 1\nmodel name\t: Fake CPU 4C\n"
    "processor\t: 2\n"
    "processor\t: 3\n"
)

STAT_A = "cpu  100 0 0 100 0 0 0 0 0 0\ncpu0 100 0 0 50 0 0 0 0 0 0\ncpu1 100 0 0 50 0 0 0 0 0 0\n"
STAT_B = "cpu  200 0 0 200 0 0 0 0 0 0\ncpu0 150 0 0 50 0 0 0 0 0 0\ncpu1 100 0 0 50 0 0 0 0 0 0\n"
STAT_BAD = "cpu a b c d e f\ncpu0 1 2 3 4 5\n"
STAT_NO_AGG = "cpu0 10 0 0 10 0 0 0 0 0 0\n"

MEM_FULL = (
    "MemTotal:       8000000 kB\n"
    "MemFree:          500000 kB\n"
    "MemAvailable:   4000000 kB\n"
    "SwapTotal:      2048000 kB\n"
    "SwapFree:       1024000 kB\n"
)
MEM_NO_TOTAL = "MemFree:         500000 kB\nMemAvailable:   4000000 kB\n"

# /proc/[pid]/stat: pid (comm) state ppid … utime=20 stime=10 threads=3 start=1234
PROC_STAT_A = "42 (test) S 1 42 0 -1 4194304 100 200 5 7 20 0 0 0 0 0 0 3 0 1234 5000 25"
# utime+stime now 200 → delta 200 ticks
PROC_STAT_B = "42 (test) S 1 42 0 -1 4194304 100 200 5 7 20 200 0 0 0 0 0 3 0 1234 5000 25"
PROC_STATUS = (
    "Name:\ttest\n"
    "State:\tS (sleeping)\n"
    "VmRSS:\t  1234 kB\n"
    "Uid:\t  1000 (1)  1000 (2)  1000 (3)  1000 (4)\n"
)
PROC_IO_A = "rchar: 100\nread_bytes: 0\nwchar: 100\nwrite_bytes: 0\n"
PROC_IO_B = "rchar: 100\nread_bytes: 102400\nwchar: 100\nwrite_bytes: 204800\n"

NETDEV_A = (
    "Inter-|   Receive                                                |  Transmit\n"
    " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n"
    "    lo: 1000 10 0 0 0 0 0 0 200 5 0 0 0 0 0 0\n"
    "  eth0: 10000 100 0 0 0 0 0 0 1000 50 0 0 0 0 0 0\n"
)
NETDEV_B = (
    "    lo: 1100 11 0 0 0 0 0 0 250 5 0 0 0 0 0 0\n"
    "  eth0: 112400 110 0 0 0 0 0 0 205800 60 0 0 0 0 0 0\n"
)

DISKSTATS_A = (
    " 259       0 nvme0n1 100 0 1000 50 200 0 2000 90 0 0 0 0 0 0 0 0 0\n"
    "   7       0 loop0 10 0 100 5 10 0 100 5 0 0 0 0 0 0 0 0 0\n"
    "   4       0 ram0 10 0 100 5 10 0 100 5 0 0 0 0 0 0 0 0 0\n"
)
DISKSTATS_B = (
    " 259       0 nvme0n1 150 0 3000 50 250 0 6000 90 0 0 0 0 0 0 0 0 0\n"
    "   7       0 loop0 10 0 100 5 10 0 100 5 0 0 0 0 0 0 0 0 0\n"
    "   4       0 ram0 10 0 100 5 10 0 100 5 0 0 0 0 0 0 0 0 0\n"
)

GPU_APPS_OK = "1234, 512 MiB, /usr/bin/firefox\n5678, 256 MiB, game\n"
GPU_APPS_BAD = "total junk, no commas\n"

NETHOQS_OK = (
    " 1234 (firefox)\nRX (KiB/s): 12.34\nTX (KiB/s): 5.67\n"
    " 5678 (game)\nRX (KiB/s): 0.10\nTX (KiB/s): 0.20\n"
)

SERVICES_OK = (
    "sshd.service loaded active running OpenSSH server daemon\n"
    "networkd.service loaded active running Network daemon\n"
    "old.service loaded inactive dead Some old unit\n"
)

DESKTOP_OK = (
    "[Desktop Entry]\n"
    "Name=My App\n"
    "Exec=/usr/bin/myapp --flag\n"
    "X-GNOME-Autostart-enabled=true\n"
)
DESKTOP_DISABLED = (
    "[Desktop Entry]\n"
    "Name=Other\n"
    "Exec=/usr/bin/other\n"
    "X-GNOME-Autostart-enabled=false\n"
)
DESKTOP_BROKEN = "this is not an ini file\nno section here\n"
DESKTOP_NO_SECTION = "[Other]\nName=X\n"


def make_info(pid, cpu=0.0, rss=0.0, vram=None, name=""):
    return ProcessInfo(
        pid=pid,
        name=name or f"proc{pid}",
        cmdline="",
        ppid=1,
        user="u",
        uid=1,
        state="S",
        threads=1,
        rss_kb=rss,
        cpu_percent=cpu,
        disk_read_kib_s=None,
        disk_write_kib_s=None,
        disk_read_total_kib=0.0,
        disk_write_total_kib=0.0,
        net_rx_kib_s=None,
        net_tx_kib_s=None,
        net_rx_total_kib=0.0,
        net_tx_total_kib=0.0,
        gpu_vram_mib=vram,
        uptime_seconds=1.0,
    )


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

class TestParseProcStat(unittest.TestCase):
    def test_valid(self):
        # PROC_STAT_A: utime=0 (idx 11), stime=0 (idx 12); PROC_STAT_B: utime=200
        parsed = parse_proc_stat(PROC_STAT_A)
        self.assertEqual(parsed["pid"], 42)
        self.assertEqual(parsed["name"], "test")
        self.assertEqual(parsed["state"], "S")
        self.assertEqual(parsed["ppid"], 1)
        self.assertEqual(parsed["utime"] + parsed["stime"], 0.0)
        self.assertEqual(parsed["threads"], 3)
        self.assertEqual(parsed["start_ticks"], 1234)
        parsed_b = parse_proc_stat(PROC_STAT_B)
        self.assertEqual(parsed_b["utime"] + parsed_b["stime"], 200.0)

    def test_name_with_parens_and_spaces(self):
        line = "7 (my (proc)) R 1 7 0 -1 4194304 100 1 2 3 4 5 6 7 8 9 0 2 0 99 1 1"
        parsed = parse_proc_stat(line)
        self.assertEqual(parsed["pid"], 7)
        self.assertEqual(parsed["name"], "my (proc)")
        self.assertEqual(parsed["state"], "R")

    def test_broken_input(self):
        self.assertIsNone(parse_proc_stat("no comm here"))
        self.assertIsNone(parse_proc_stat("42 (x) S 1"))
        self.assertIsNone(parse_proc_stat(""))


class TestStatusAndIoParsers(unittest.TestCase):
    def test_status_fields(self):
        fields = parse_status_fields(PROC_STATUS)
        self.assertEqual(fields["VmRSS"], 1234.0)
        self.assertEqual(fields["Uid"], 1000.0)
        self.assertNotIn("State", fields)  # non-numeric value skipped

    def test_status_empty(self):
        self.assertEqual(parse_status_fields(""), {})

    def test_proc_io(self):
        fields = parse_proc_io(PROC_IO_B)
        self.assertEqual(fields["read_bytes"], 102400.0)
        self.assertEqual(fields["write_bytes"], 204800.0)


class TestNetDevParser(unittest.TestCase):
    def test_counts(self):
        parsed = parse_net_dev(NETDEV_A)
        self.assertEqual(set(parsed), {"lo", "eth0"})
        self.assertEqual(parsed["eth0"]["rx_bytes"], 10000.0)
        self.assertEqual(parsed["eth0"]["tx_bytes"], 1000.0)
        self.assertEqual(parsed["lo"]["rx_bytes"], 1000.0)

    def test_header_lines_ignored(self):
        self.assertNotIn("", parse_net_dev(NETDEV_A))
        self.assertEqual(parse_net_dev(""), {})


class TestDiskstatsParser(unittest.TestCase):
    def test_counts(self):
        parsed = parse_diskstats(DISKSTATS_A)
        self.assertIn("nvme0n1", parsed)
        self.assertEqual(parsed["nvme0n1"]["rsectors"], 1000.0)
        self.assertEqual(parsed["nvme0n1"]["wsectors"], 2000.0)

    def test_short_lines_skipped(self):
        self.assertEqual(parse_diskstats("259 0 nvme0n1\n"), {})


class TestMiscParsers(unittest.TestCase):
    def test_btime(self):
        self.assertEqual(parse_btime("cpu 1 2\nbtime 1700000000\nintr 5\n"), 1700000000.0)
        self.assertIsNone(parse_btime("cpu 1 2\n"))
        self.assertIsNone(parse_btime("btime notanumber\n"))

    def test_loadavg(self):
        self.assertEqual(parse_loadavg("0.52 0.71 0.88 1/234 5678"), (0.52, 0.71, 0.88))
        self.assertIsNone(parse_loadavg("oops"))

    def test_cpuinfo(self):
        info = parse_cpuinfo(CPUINFO_4)
        self.assertEqual(info["cores"], 4)
        self.assertEqual(info["model"], "Fake CPU 4C")

    def test_gpu_apps_valid(self):
        apps = parse_gpu_apps(GPU_APPS_OK)
        self.assertEqual(apps[1234], {"vram_mib": 512.0, "name": "/usr/bin/firefox"})
        self.assertEqual(apps[5678]["vram_mib"], 256.0)

    def test_gpu_apps_garbage(self):
        self.assertIsNone(parse_gpu_apps(GPU_APPS_BAD))

    def test_gpu_apps_empty(self):
        self.assertIsNone(parse_gpu_apps(""))

    def test_nethogs_valid(self):
        parsed = parse_nethogs(NETHOQS_OK)
        self.assertEqual(parsed[1234], (12.34, 5.67))
        self.assertEqual(parsed[5678], (0.1, 0.2))

    def test_nethogs_garbage(self):
        self.assertEqual(parse_nethogs("nothing to see here\n"), {})

    def test_services_valid(self):
        services = parse_services(SERVICES_OK)
        self.assertEqual(len(services), 3)
        self.assertEqual(services[0]["unit"], "sshd.service")
        self.assertEqual(services[0]["active"], "active")
        self.assertEqual(services[0]["sub"], "running")
        self.assertEqual(services[0]["description"], "OpenSSH server daemon")

    def test_services_empty_and_garbage(self):
        self.assertEqual(parse_services(""), [])
        self.assertEqual(parse_services("one two\n"), [])

    def test_service_pids(self):
        text = "Id=sshd.service\nMainPID=812\nType=notify\nId=old.service\nMainPID=0\n"
        self.assertEqual(parse_service_pids(text), {"sshd.service": 812})

    def test_sessions(self):
        text = "1  1000  sillycat  seat0  tty1  active  since 12345\n2  0  root  seat0  tty2  online\n"
        parsed = parse_sessions(text)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["session"], "1")
        self.assertEqual(parsed[0]["uid"], 1000)
        self.assertEqual(parsed[0]["user"], "sillycat")
        self.assertEqual(parsed[0]["state"], "active")
        self.assertEqual(parse_sessions("garbage line\n"), [])

    def test_desktop_valid_enabled(self):
        entry = parse_desktop(DESKTOP_OK, "/home/u/.config/autostart/my.desktop")
        self.assertIsInstance(entry, StartupEntry)
        self.assertEqual(entry.name, "My App")
        self.assertEqual(entry.exec, "/usr/bin/myapp --flag")
        self.assertTrue(entry.enabled)
        self.assertEqual(entry.path, "/home/u/.config/autostart/my.desktop")

    def test_desktop_disabled(self):
        entry = parse_desktop(DESKTOP_DISABLED, "/p/other.desktop")
        self.assertEqual(entry.name, "Other")
        self.assertFalse(entry.enabled)

    def test_desktop_broken_skipped(self):
        self.assertIsNone(parse_desktop(DESKTOP_BROKEN, "/p/broken.desktop"))

    def test_desktop_without_desktop_entry_section(self):
        self.assertIsNone(parse_desktop(DESKTOP_NO_SECTION, "/p/nope.desktop"))


# ---------------------------------------------------------------------------
# CPU (aggregate + per-core + per-process)
# ---------------------------------------------------------------------------

class TestCpuAggregate(unittest.TestCase):
    def _mon(self, stat_text):
        return TaskManager(
            stat_reader=FakeReader(stat_text), cpuinfo_reader=FakeReader(CPUINFO_4)
        )

    def test_first_call_zero(self):
        self.assertEqual(self._mon(STAT_A).cpu_percent(), 0.0)

    def test_delta(self):
        mon = self._mon(STAT_A)
        mon.cpu_percent()  # prime: total 200, idle 100
        mon._stat_reader = FakeReader(STAT_B)  # total 400, idle 200 → 50%
        self.assertEqual(mon.cpu_percent(), 50.0)

    def test_zero_delta(self):
        mon = self._mon(STAT_A)
        mon.cpu_percent()
        self.assertEqual(mon.cpu_percent(), 0.0)

    def test_bad_line_cache_unchanged(self):
        mon = self._mon(STAT_A)
        mon.cpu_percent()  # prime
        mon._stat_reader = FakeReader(STAT_BAD)
        self.assertEqual(mon.cpu_percent(), 0.0)
        mon._stat_reader = FakeReader(STAT_B)
        self.assertEqual(mon.cpu_percent(), 50.0)

    def test_missing_aggregate_line(self):
        mon = self._mon(STAT_NO_AGG)
        self.assertEqual(mon.cpu_percent(), 0.0)


class TestPerCore(unittest.TestCase):
    def test_first_call_zero(self):
        mon = TaskManager(stat_reader=FakeReader(STAT_A))
        self.assertEqual(mon.per_core_percents(), {0: 0.0, 1: 0.0})

    def test_delta(self):
        mon = TaskManager(stat_reader=FakeReader(STAT_A))
        mon.per_core_percents()
        mon._stat_reader = FakeReader(STAT_B)
        # cpu0: total 100→150, idle 50→50 → 100%; cpu1 unchanged → 0%
        self.assertEqual(mon.per_core_percents(), {0: 100.0, 1: 0.0})


class TestProcessCpu(unittest.TestCase):
    def _mon(self):
        files = {
            42: {
                "stat": PROC_STAT_A,
                "status": PROC_STATUS,
                "io": PROC_IO_A,
                "comm": "test\n",
                "cmdline": "test\x00--flag\x00",
            }
        }
        clock = FakeClock(0.0)
        mon = TaskManager(
            time_fn=clock,
            pids_fn=lambda: [42],
            proc_file_fn=lambda pid, name: files[pid].get(name, ""),
            cpuinfo_reader=FakeReader(CPUINFO_4),
            uid_name_fn=lambda uid: "tester",
            wall_time_fn=lambda: 1700001000.0,
            stat_reader=FakeReader("btime 1700000000\n"),
            gpu_apps_reader=FakeReader(""),
            nethogs_reader=FakeReader(""),
        )
        return mon, files, clock

    def test_two_samples(self):
        mon, files, clock = self._mon()
        first = mon.scan_processes()[0]
        self.assertEqual(first.cpu_percent, 0.0)  # prime
        self.assertEqual(first.disk_read_kib_s, None)
        self.assertEqual(first.rss_kb, 1234.0)
        self.assertEqual(first.user, "tester")
        self.assertEqual(first.threads, 3)
        self.assertEqual(first.state, "S")
        self.assertEqual(first.ppid, 1)
        self.assertEqual(first.cmdline, "test --flag")
        self.assertEqual(first.disk_read_total_kib, 0.0)

        # +200 ticks over 1.0 s on 4 cores = 2 CPU-s / 1 s / 4 = 50%
        files[42]["stat"] = PROC_STAT_B
        files[42]["io"] = PROC_IO_B
        clock.t = 1.0
        info = mon.scan_processes()[0]
        self.assertEqual(info.cpu_percent, 50.0)
        self.assertEqual(info.disk_read_kib_s, 100.0)
        self.assertEqual(info.disk_write_kib_s, 200.0)
        self.assertEqual(info.disk_read_total_kib, 100.0)
        self.assertEqual(info.disk_write_total_kib, 200.0)
        # uptime = wall - btime - start_ticks/CLK_TCK = 1000 - 12.34
        self.assertAlmostEqual(info.uptime_seconds, 987.66, places=2)

    def test_vanished_process_skipped_and_cache_pruned(self):
        mon, files, clock = self._mon()
        mon.scan_processes()
        files[42]["stat"] = ""  # unreadable → vanished
        infos = mon.scan_processes()
        self.assertEqual(infos, [])
        self.assertNotIn(42, mon._proc_cpu_cache)
        self.assertNotIn(42, mon._io_cache)


# ---------------------------------------------------------------------------
# RAM / network / disks
# ---------------------------------------------------------------------------

class TestRamInfo(unittest.TestCase):
    def test_values(self):
        mon = TaskManager(meminfo_reader=FakeReader(MEM_FULL))
        ram = mon.ram_info()
        self.assertEqual(ram["total_kb"], 8000000.0)
        self.assertEqual(ram["used_kb"], 4000000.0)
        self.assertEqual(ram["percent"], 50.0)
        self.assertEqual(ram["swap_total_kb"], 2048000.0)
        self.assertEqual(ram["swap_used_kb"], 1024000.0)
        self.assertEqual(ram["swap_percent"], 50.0)

    def test_missing_fields_none(self):
        mon = TaskManager(meminfo_reader=FakeReader(MEM_NO_TOTAL))
        self.assertIsNone(mon.ram_info())

    def test_reader_raises_none(self):
        def boom():
            raise OSError("gone")

        self.assertIsNone(TaskManager(meminfo_reader=boom).ram_info())


class TestNetwork(unittest.TestCase):
    def _mon(self):
        clock = FakeClock(0.0)
        reader = FakeReader(NETDEV_A)
        mon = TaskManager(time_fn=clock, netdev_reader=reader, net_state_fn=lambda i: "up")
        return mon, reader, clock

    def test_speeds_and_totals(self):
        mon, reader, clock = self._mon()
        out = mon.network()
        self.assertEqual(set(out), {"lo", "eth0"})
        self.assertIsNone(out["eth0"]["rx_kib_s"])  # first sample primes
        self.assertEqual(out["eth0"]["rx_bytes"], 10000.0)
        self.assertEqual(out["eth0"]["state"], "up")

        reader.text = NETDEV_B
        clock.t = 1.0
        out = mon.network()
        self.assertEqual(out["eth0"]["rx_kib_s"], 100.0)  # 102400 B / 1 s
        self.assertEqual(out["eth0"]["tx_kib_s"], 200.0)  # 204800 B / 1 s
        self.assertEqual(out["eth0"]["rx_bytes"], 112400.0)
        self.assertEqual(out["eth0"]["tx_bytes"], 205800.0)

    def test_lo_isolated(self):
        mon, reader, clock = self._mon()
        mon.network()
        reader.text = NETDEV_B
        clock.t = 1.0
        out = mon.network()
        # lo: 1000 → 1100 bytes over 1 s
        self.assertAlmostEqual(out["lo"]["rx_kib_s"], 100.0 / 1024.0, places=6)


class TestDisks(unittest.TestCase):
    def test_speeds_and_filter(self):
        clock = FakeClock(0.0)
        reader = FakeReader(DISKSTATS_A)
        mon = TaskManager(
            time_fn=clock,
            diskstats_reader=reader,
            block_devices_fn=lambda: ["nvme0n1", "loop0", "ram0", "zram0", "sda"],
            mounts_reader=FakeReader(""),
            statvfs_fn=lambda mp: (10_000_000_000, 4_000_000_000, 6_000_000_000),
        )
        disks = mon.disks()
        self.assertEqual([d["name"] for d in disks], ["nvme0n1", "sda"])
        self.assertIsNone(disks[0]["read_kib_s"])  # first sample
        self.assertIsNone(disks[0]["total_bytes"])  # no mount for the device

        reader.text = DISKSTATS_B
        clock.t = 1.0
        disks = mon.disks()
        self.assertEqual(disks[0]["read_kib_s"], 1000.0)  # 2000 sectors * 512 / 1024
        self.assertEqual(disks[0]["write_kib_s"], 2000.0)  # 4000 sectors * 512 / 1024
        self.assertIsNone(disks[1]["read_kib_s"])  # sda has no stats

    def test_mount_size(self):
        mon = TaskManager(
            time_fn=FakeClock(),
            diskstats_reader=FakeReader(DISKSTATS_A),
            block_devices_fn=lambda: ["nvme0n1"],
            mounts_reader=FakeReader("/dev/nvme0n1 / ext4 rw 0 0\n"),
            statvfs_fn=lambda mp: (20_000_000_000, 8_000_000_000, 12_000_000_000),
        )
        disks = mon.disks()
        self.assertEqual(disks[0]["total_bytes"], 20_000_000_000)
        self.assertEqual(disks[0]["used_bytes"], 8_000_000_000)
        self.assertEqual(disks[0]["free_bytes"], 12_000_000_000)

    def test_mounts(self):
        mon = TaskManager(
            mounts_reader=FakeReader(
                "overlay / overlay rw 0 0\n"
                "devtmpfs /dev devtmpfs rw 0 0\n"
                "/dev/nvme0n1p1 / ext4 rw 0 0\n"
                "/dev/nvme0n1p2 /home ext4 rw 0 0\n"
            ),
            statvfs_fn=lambda mp: (100, 40, 60),
        )
        mounts = mon.mounts()
        self.assertEqual([m["mount"] for m in mounts], ["/", "/home"])
        self.assertEqual(mounts[0]["percent"], 40.0)
        self.assertEqual(mounts[0]["fstype"], "ext4")


# ---------------------------------------------------------------------------
# GPU apps
# ---------------------------------------------------------------------------

class TestGpuAppsReader(unittest.TestCase):
    def test_valid(self):
        mon = TaskManager(gpu_apps_reader=FakeReader(GPU_APPS_OK))
        apps = mon.gpu_apps()
        self.assertEqual(apps[1234]["vram_mib"], 512.0)
        self.assertEqual(apps[5678]["name"], "game")

    def test_garbage_none(self):
        self.assertIsNone(TaskManager(gpu_apps_reader=FakeReader(GPU_APPS_BAD)).gpu_apps())

    def test_raising_reader_none(self):
        def boom():
            raise RuntimeError("nvidia-smi exited with code 9")

        self.assertIsNone(TaskManager(gpu_apps_reader=boom).gpu_apps())

    def test_empty_none(self):
        self.assertIsNone(TaskManager(gpu_apps_reader=FakeReader("")).gpu_apps())


# ---------------------------------------------------------------------------
# Kill logic
# ---------------------------------------------------------------------------

_TREE_FILES = {
    1: {"stat": "1 (init) S 0 1 0 -1 4194304 100 200 5 7 20 0 0 0 0 0 0 1 0 100 1000 10"},
    2: {"stat": "2 (a) S 1 2 0 -1 4194304 100 200 5 7 20 0 0 0 0 0 0 1 0 100 1000 10"},
    3: {"stat": "3 (b) S 1 3 0 -1 4194304 100 200 5 7 20 0 0 0 0 0 0 1 0 100 1000 10"},
    4: {"stat": "4 (c) S 2 4 0 -1 4194304 100 200 5 7 20 0 0 0 0 0 0 1 0 100 1000 10"},
}


class TestKill(unittest.TestCase):
    def _mon(self, log, force=False):
        def kill(pid, sig):
            log.append((pid, sig))

        return TaskManager(
            pids_fn=lambda: [1, 2, 3, 4],
            proc_file_fn=lambda pid, name: _TREE_FILES[pid].get(name, ""),
            kill_fn=kill,
        )

    def test_kill_pid_sigterm(self):
        log = []
        mon = self._mon(log)
        mon.kill_pid(42)
        self.assertEqual(log, [(42, signal.SIGTERM)])

    def test_kill_pid_force_sigkill(self):
        log = []
        mon = self._mon(log)
        mon.kill_pid(42, force=True)
        self.assertEqual(log, [(42, signal.SIGKILL)])

    def test_tree_children_first(self):
        log = []
        mon = self._mon(log)
        count = mon.kill_tree(1)
        self.assertEqual(count, 4)
        self.assertEqual([pid for pid, _ in log], [4, 2, 3, 1])
        self.assertTrue(all(sig == signal.SIGTERM for _, sig in log))

    def test_tree_force_uses_sigkill(self):
        log = []
        mon = self._mon(log)
        mon.kill_tree(1, force=True)
        self.assertTrue(all(sig == signal.SIGKILL for _, sig in log))

    def test_children_of(self):
        log = []
        mon = self._mon(log)
        self.assertEqual(mon.children_of(1), [2, 3])
        self.assertEqual(mon.children_of(2), [4])
        self.assertEqual(mon.children_of(4), [])
        self.assertEqual(mon.children_of(999), [])

    def test_permission_error_propagates(self):
        def kill(pid, sig):
            raise PermissionError(1, "Operation not permitted")

        mon = TaskManager(kill_fn=kill)
        with self.assertRaises(PermissionError):
            mon.kill_pid(99)

    def test_tree_permission_error_propagates(self):
        def kill(pid, sig):
            if pid == 4:
                raise PermissionError(1, "Operation not permitted")
            return None

        mon = TaskManager(
            pids_fn=lambda: [1, 2, 3, 4],
            proc_file_fn=lambda pid, name: _TREE_FILES[pid].get(name, ""),
            kill_fn=kill,
        )
        with self.assertRaises(PermissionError):
            mon.kill_tree(1)


# ---------------------------------------------------------------------------
# Top-N, filter, sort
# ---------------------------------------------------------------------------

class TestTopFilterSort(unittest.TestCase):
    def test_top_cpu_desc(self):
        infos = [make_info(pid, cpu=float(pid)) for pid in range(1, 21)]
        mon = TaskManager()
        mon._last_procs = infos
        top = mon.top_processes("cpu", 10)
        self.assertEqual(len(top), 10)
        self.assertEqual([p.pid for p in top], list(range(20, 10, -1)))

    def test_top_rss_desc(self):
        infos = [make_info(pid, rss=float(pid)) for pid in (3, 1, 2)]
        mon = TaskManager()
        mon._last_procs = infos
        self.assertEqual([p.pid for p in mon.top_processes("rss", 10)], [3, 2, 1])

    def test_top_vram_none_last(self):
        infos = [
            make_info(1, vram=100.0),
            make_info(2, vram=None),
            make_info(3, vram=50.0),
        ]
        mon = TaskManager()
        mon._last_procs = infos
        self.assertEqual([p.pid for p in mon.top_processes("vram", 10)], [1, 3, 2])

    def test_filter_case_insensitive(self):
        infos = [
            make_info(1, name="Firefox"),
            make_info(2, name="bash"),
        ]
        infos[1].cmdline = "/bin/bash --firewall"
        out = filter_processes(infos, "FIRE")
        self.assertEqual([p.pid for p in out], [1, 2])

    def test_filter_empty_returns_all(self):
        infos = [make_info(1), make_info(2)]
        self.assertEqual(len(filter_processes(infos, "   ")), 2)

    def test_sorted_by_desc(self):
        infos = [make_info(1, cpu=1.0), make_info(2, cpu=3.0), make_info(3, cpu=2.0)]
        out = sorted_by(infos, lambda p: p.cpu_percent, reverse=True)
        self.assertEqual([p.pid for p in out], [2, 3, 1])

    def test_sorted_by_asc(self):
        infos = [make_info(1, cpu=1.0), make_info(2, cpu=3.0), make_info(3, cpu=2.0)]
        out = sorted_by(infos, lambda p: p.cpu_percent)
        self.assertEqual([p.pid for p in out], [1, 3, 2])

    def test_sorted_by_none_last_both_directions(self):
        infos = [
            make_info(1, vram=None),
            make_info(2, vram=5.0),
            make_info(3, vram=None),
            make_info(4, vram=1.0),
        ]
        desc = sorted_by(infos, lambda p: p.gpu_vram_mib, reverse=True)
        self.assertEqual(desc[0].gpu_vram_mib, 5.0)
        self.assertIsNone(desc[-1].gpu_vram_mib)
        asc = sorted_by(infos, lambda p: p.gpu_vram_mib)
        self.assertEqual(asc[0].gpu_vram_mib, 1.0)
        self.assertIsNone(asc[-1].gpu_vram_mib)


# ---------------------------------------------------------------------------
# Ring buffer (LineGraph storage)
# ---------------------------------------------------------------------------

class TestRingBuffer(unittest.TestCase):
    def test_capacity_and_eviction(self):
        buf = RingBuffer(10)
        for i in range(15):
            buf.add(i)
        self.assertEqual(len(buf), 10)
        self.assertEqual(buf.values(), list(range(5, 15)))  # oldest five dropped

    def test_add_many(self):
        buf = RingBuffer(3)
        buf.add_many([1, 2, 3, 4])
        self.assertEqual(buf.values(), [2, 3, 4])

    def test_default_capacity(self):
        buf = RingBuffer()
        self.assertGreaterEqual(buf.capacity, 60)
        self.assertLessEqual(buf.capacity, 90)


# ---------------------------------------------------------------------------
# Theme colour resolution (additive core helper used by LineGraph)
# ---------------------------------------------------------------------------

class TestResolveColor(unittest.TestCase):
    def test_accent_blue(self):
        r, g, b = resolve_color("dark", "blue", "accent")
        self.assertAlmostEqual(r, 0x4F / 255.0)
        self.assertAlmostEqual(g, 0x8C / 255.0)
        self.assertAlmostEqual(b, 1.0)

    def test_bg_dark(self):
        self.assertEqual(
            resolve_color("dark", "blue", "bg"),
            (0x1E / 255.0, 0x1F / 255.0, 0x24 / 255.0),
        )

    def test_full_variable_name_matches_alias(self):
        self.assertEqual(
            resolve_color("light", "green", "--omnify-danger"),
            resolve_color("light", "green", "danger"),
        )

    def test_unknown_name_black(self):
        self.assertEqual(resolve_color("dark", "blue", "nope"), (0.0, 0.0, 0.0))

    def test_malformed_hex_black(self):
        self.assertEqual(resolve_color("dark", "blue", "border-nope"), (0.0, 0.0, 0.0))

    def test_all_named_colors_in_range(self):
        for mode in ("dark", "light"):
            for accent in ("blue", "purple", "green", "orange", "pink", "teal"):
                for name in (
                    "bg", "bg-alt", "bg-raised", "bg-hover", "fg", "fg-dim",
                    "border", "danger", "success", "warning", "accent", "accent-soft",
                ):
                    color = resolve_color(mode, accent, name)
                    self.assertEqual(len(color), 3)
                    for value in color:
                        self.assertGreaterEqual(value, 0.0)
                        self.assertLessEqual(value, 1.0)


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

class TestFormatters(unittest.TestCase):
    def test_format_speed(self):
        self.assertEqual(format_speed(512.0), "512.0 KiB/s")
        self.assertEqual(format_speed(2048.0), "2.0 MiB/s")
        self.assertEqual(format_speed(None), "-")
        self.assertEqual(format_speed(-5), "0.0 KiB/s")

    def test_format_bytes(self):
        self.assertEqual(format_bytes(512), "512 B")
        self.assertEqual(format_bytes(2048), "2.0 KiB")
        self.assertEqual(format_bytes(5 * 1024**3), "5.0 GiB")
        self.assertEqual(format_bytes(None), "-")

    def test_format_uptime(self):
        self.assertEqual(format_uptime(45), "45s")
        self.assertEqual(format_uptime(725), "12m 5s")
        self.assertEqual(format_uptime(3725), "1h 2m")
        self.assertEqual(format_uptime(90061), "1d 1h")
        self.assertEqual(format_uptime(None), "-")
        self.assertEqual(format_uptime(-10), "0s")


# ---------------------------------------------------------------------------
# Startup entries + toggling (fake filesystem)
# ---------------------------------------------------------------------------

class TestStartupEntries(unittest.TestCase):
    def _mon(self, files):
        return TaskManager(
            autostart_dir_fn=lambda: "/fake/autostart",
            autostart_list_fn=lambda d: [os.path.basename(p) for p in files],
            read_file_fn=lambda path: files[path],
            write_file_fn=lambda path, content: files.update({path: content}),
        )

    def test_entries_parsed(self):
        files = {
            "/fake/autostart/a.desktop": DESKTOP_OK,
            "/fake/autostart/b.desktop": DESKTOP_DISABLED,
            "/fake/autostart/c.desktop": DESKTOP_BROKEN,
        }
        entries = self._mon(files).startup_entries()
        self.assertEqual([e.name for e in entries], ["My App", "Other"])
        self.assertEqual(entries[0].enabled, True)
        self.assertEqual(entries[1].enabled, False)

    def test_missing_directory(self):
        mon = TaskManager(
            autostart_dir_fn=lambda: "/nonexistent",
            autostart_list_fn=os.listdir,
            read_file_fn=lambda path: (_ for _ in ()).throw(AssertionError("nope")),
        )
        self.assertEqual(mon.startup_entries(), [])

    def test_toggle_writes_file(self):
        files = {"/fake/autostart/a.desktop": DESKTOP_OK}
        mon = self._mon(files)
        entry = mon.startup_entries()[0]
        mon.set_startup_enabled(entry, False)
        self.assertIn("X-GNOME-Autostart-enabled=false", files[entry.path])
        mon.set_startup_enabled(entry, True)
        self.assertIn("X-GNOME-Autostart-enabled=true", files[entry.path])

    def test_toggle_unparseable_raises_value_error(self):
        files = {"/fake/autostart/x.desktop": "garbage without sections"}
        mon = self._mon(files)
        entry = StartupEntry(path="/fake/autostart/x.desktop", name="X", exec="", enabled=True)
        with self.assertRaises(ValueError):
            mon.set_startup_enabled(entry, True)


if __name__ == "__main__":
    unittest.main()
