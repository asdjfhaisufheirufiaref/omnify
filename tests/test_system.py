"""Headless tests for the system monitor (SystemMonitor + format_size).

Run with:  python3 tests/test_system.py

Every reader is a fake closure over a canned string — the real ``/proc``
tree and ``nvidia-smi`` are never touched.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omnify.modules.system import SystemMonitor, format_size  # noqa: E402


class FakeReader:
    """A mutable zero-arg reader whose text the test can swap at will."""

    def __init__(self, text: str = "") -> None:
        self.text = text

    def __call__(self) -> str:
        return self.text


# Aggregate "cpu " line: total 200 (100+0+0+100), idle+iowait 100.
STAT_IDLE_50 = "cpu  100 0 0 100 0 0 0 0 0 0\ncpu0 10 0 0 10 0 0 0 0 0 0\n"
# total 400 (200+0+0+200), idle 200 → 50% over the previous sample.
STAT_IDLE_50_B = "cpu  200 0 0 200 0 0 0 0 0 0\n"
# total 210, idle 90 → delta_total 10, delta_idle -10 → raw 200 → clamp 100.
STAT_OVER_100 = "cpu  120 0 0 90 0 0 0 0 0 0\n"
# total 210, idle 120 → delta_total 10, delta_idle 20 → raw -100 → clamp 0.
STAT_UNDER_0 = "cpu  90 0 0 120 0 0 0 0 0 0\n"

MEM_HALF = "MemTotal:       8000000 kB\nMemFree:         500000 kB\nMemAvailable:   4000000 kB\n"
MEM_ZERO = "MemTotal:       0 kB\nMemAvailable:   0 kB\n"
MEM_NO_TOTAL = "MemFree:         500000 kB\nMemAvailable:   4000000 kB\n"
MEM_NO_AVAILABLE = "MemTotal:       8000000 kB\nMemFree:         500000 kB\n"

GPU_OK = "NVIDIA GeForce RTX 4090, 42 %, 1024 MiB, 24564 MiB, 61\n"
GPU_TOO_FEW = "NVIDIA GeForce RTX 4090, 42 %\n"
GPU_NON_NUMERIC = "NVIDIA GeForce RTX 4090, abc, 1024 MiB, 24564 MiB, 61\n"
GPU_GARBAGE = "total junk with no commas\n"


class TestCpuPercent(unittest.TestCase):
    def setUp(self):
        self.stat = FakeReader(STAT_IDLE_50)
        self.mem = FakeReader(MEM_HALF)
        self.gpu = FakeReader(GPU_OK)
        self.mon = SystemMonitor(
            stat_reader=self.stat, meminfo_reader=self.mem, gpu_reader=self.gpu
        )

    def test_first_call_returns_zero(self):
        self.assertEqual(self.mon.cpu_percent(), 0.0)

    def test_half_usage(self):
        self.assertEqual(self.mon.cpu_percent(), 0.0)  # prime
        self.stat.text = STAT_IDLE_50_B
        self.assertEqual(self.mon.cpu_percent(), 50.0)

    def test_zero_delta_returns_zero(self):
        self.mon.cpu_percent()
        self.stat.text = STAT_IDLE_50  # same sample twice
        self.assertEqual(self.mon.cpu_percent(), 0.0)

    def test_clamped_to_100(self):
        self.mon.cpu_percent()
        self.stat.text = STAT_OVER_100
        self.assertEqual(self.mon.cpu_percent(), 100.0)

    def test_clamped_to_0_on_negative(self):
        self.mon.cpu_percent()
        self.stat.text = STAT_UNDER_0
        self.assertEqual(self.mon.cpu_percent(), 0.0)

    def test_missing_cpu_line_returns_zero(self):
        self.mon.cpu_percent()
        self.stat.text = "cpu0 10 0 0 10 0 0 0 0 0 0\n"  # per-core only
        self.assertEqual(self.mon.cpu_percent(), 0.0)

    def test_short_line_returns_zero_cache_unchanged(self):
        self.mon.cpu_percent()  # prime with total 200 / idle 100
        self.stat.text = "cpu 1 2 3\n"  # too few fields
        self.assertEqual(self.mon.cpu_percent(), 0.0)
        # The cache must still hold the last *good* sample, so the next
        # reading compares against it: delta_total 200, delta_idle 100 → 50%.
        self.stat.text = STAT_IDLE_50_B
        self.assertEqual(self.mon.cpu_percent(), 50.0)

    def test_non_numeric_line_returns_zero_cache_unchanged(self):
        self.mon.cpu_percent()
        self.stat.text = "cpu a b c d e f\n"
        self.assertEqual(self.mon.cpu_percent(), 0.0)
        self.stat.text = STAT_IDLE_50_B
        self.assertEqual(self.mon.cpu_percent(), 50.0)


class TestRam(unittest.TestCase):
    def test_values(self):
        mon = SystemMonitor(meminfo_reader=FakeReader(MEM_HALF))
        ram = mon.ram()
        self.assertEqual(ram["total_kb"], 8000000.0)
        self.assertEqual(ram["available_kb"], 4000000.0)
        self.assertEqual(ram["used_kb"], 4000000.0)
        self.assertEqual(ram["percent"], 50.0)

    def test_zero_total_percent_zero(self):
        mon = SystemMonitor(meminfo_reader=FakeReader(MEM_ZERO))
        self.assertEqual(mon.ram()["percent"], 0.0)

    def test_missing_memtotal_raises(self):
        mon = SystemMonitor(meminfo_reader=FakeReader(MEM_NO_TOTAL))
        with self.assertRaises(ValueError):
            mon.ram()

    def test_missing_memavailable_raises(self):
        mon = SystemMonitor(meminfo_reader=FakeReader(MEM_NO_AVAILABLE))
        with self.assertRaises(ValueError):
            mon.ram()

    def test_reader_raises_is_value_error(self):
        def boom() -> str:
            raise OSError("no meminfo")

        mon = SystemMonitor(meminfo_reader=boom)
        with self.assertRaises(ValueError):
            mon.ram()


class TestGpu(unittest.TestCase):
    def test_valid_line(self):
        mon = SystemMonitor(gpu_reader=FakeReader(GPU_OK))
        gpu = mon.gpu()
        self.assertEqual(
            gpu,
            {
                "name": "NVIDIA GeForce RTX 4090",
                "utilization": 42.0,
                "memory_used_mib": 1024.0,
                "memory_total_mib": 24564.0,
                "temperature_c": 61.0,
            },
        )

    def test_first_non_empty_line_wins(self):
        text = "\n\n" + GPU_OK + "\nOther GPU, 1 %, 1 MiB, 2 MiB, 2\n"
        mon = SystemMonitor(gpu_reader=FakeReader(text))
        self.assertEqual(mon.gpu()["name"], "NVIDIA GeForce RTX 4090")

    def test_reader_raises_returns_none(self):
        def boom() -> str:
            raise RuntimeError("nvidia-smi exited with code 9")

        self.assertIsNone(SystemMonitor(gpu_reader=boom).gpu())

    def test_too_few_fields_returns_none(self):
        self.assertIsNone(SystemMonitor(gpu_reader=FakeReader(GPU_TOO_FEW)).gpu())

    def test_non_numeric_returns_none(self):
        self.assertIsNone(SystemMonitor(gpu_reader=FakeReader(GPU_NON_NUMERIC)).gpu())

    def test_garbage_returns_none(self):
        self.assertIsNone(SystemMonitor(gpu_reader=FakeReader(GPU_GARBAGE)).gpu())

    def test_empty_returns_none(self):
        self.assertIsNone(SystemMonitor(gpu_reader=FakeReader("")).gpu())


class TestSnapshot(unittest.TestCase):
    def test_keys_and_values(self):
        mon = SystemMonitor(
            stat_reader=FakeReader(STAT_IDLE_50),
            meminfo_reader=FakeReader(MEM_HALF),
            gpu_reader=FakeReader(GPU_OK),
        )
        snap = mon.snapshot()
        self.assertEqual(set(snap), {"cpu_percent", "ram", "gpu"})
        self.assertEqual(snap["cpu_percent"], 0.0)  # first sample primes the cache
        self.assertEqual(snap["ram"]["percent"], 50.0)
        self.assertEqual(snap["gpu"]["name"], "NVIDIA GeForce RTX 4090")

    def test_failing_meminfo_yields_ram_none(self):
        def boom() -> str:
            raise OSError("no meminfo")

        mon = SystemMonitor(
            stat_reader=FakeReader(STAT_IDLE_50),
            meminfo_reader=boom,
            gpu_reader=FakeReader(GPU_OK),
        )
        snap = mon.snapshot()  # must not raise
        self.assertIsNone(snap["ram"])
        self.assertEqual(snap["gpu"]["name"], "NVIDIA GeForce RTX 4090")

    def test_all_readers_failing_still_returns_dict(self):
        def boom() -> str:
            raise OSError("gone")

        mon = SystemMonitor(
            stat_reader=boom, meminfo_reader=boom, gpu_reader=boom
        )
        snap = mon.snapshot()
        self.assertEqual(snap, {"cpu_percent": 0.0, "ram": None, "gpu": None})


class TestFormatSize(unittest.TestCase):
    def test_gib(self):
        self.assertEqual(format_size(1048576), "1.0 GiB")

    def test_mib(self):
        self.assertEqual(format_size(1536), "1.5 MiB")

    def test_boundary_just_below_gib(self):
        self.assertEqual(format_size(1048575), "1024.0 MiB")

    def test_negative_clamps_to_zero(self):
        self.assertEqual(format_size(-5), "0.0 MiB")

    def test_accepts_int_and_zero(self):
        self.assertEqual(format_size(2048), "2.0 MiB")
        self.assertEqual(format_size(0), "0.0 MiB")


if __name__ == "__main__":
    unittest.main()
