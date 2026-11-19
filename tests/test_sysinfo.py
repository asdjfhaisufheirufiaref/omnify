"""Headless tests for the AIDA64-style system information model.

Every probe is fake: the tests feed a virtual filesystem, fake ``uname``,
fake environment and canned command output to :class:`SystemInfo`, so the
real ``/proc``/``/sys`` trees are never read and no command is spawned.
"""

from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omnify.modules.sysinfo import (  # noqa: E402
    SystemInfo,
    format_bytes,
    format_uptime,
    parse_cpuinfo,
    parse_default_gateway,
    parse_lspci_gpus,
    parse_meminfo,
    parse_mounts,
    parse_net_dev,
    parse_os_release,
    parse_resolv_conf,
)

GiB = 1024 ** 3
MiB = 1024 ** 2

OS_RELEASE = """NAME="Arch Linux"
PRETTY_NAME="Arch Linux"
ID=arch
VERSION_ID="2026.11"
"""

CPUINFO = """processor\t: 0
vendor_id\t: GenuineIntel
model name\t: Intel(R) Core(TM) i7-9700K CPU @ 3.60GHz
physical id\t: 0
cpu cores\t: 4
cpu MHz\t\t: 3600.000
cache size\t: 12288 KB
processor\t: 1
processor\t: 2
processor\t: 3
"""

MEMINFO = """MemTotal:       16777216 kB
MemFree:          524288 kB
MemAvailable:    8388608 kB
SwapTotal:       2097152 kB
SwapFree:        1048576 kB
"""

STAT = """cpu  100 0 100 800 0 0 0 0 0 0
btime 1700000000
"""

UPTIME = "3661.50 12345.00\n"

MOUNTS = """/dev/nvme0n1p2 / ext4 rw,relatime 0 0
proc /proc proc rw,nosuid 0 0
/dev/nvme0n1p1 /boot/efi vfat rw,relatime 0 0
tmpfs /tmp tmpfs rw,nosuid 0 0
/dev/nvme0n1p2 /home ext4 rw,relatime 0 0
"""

NET_DEV = """Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo: 1000 1 0 0 0 0 0 0 1000 1 0 0 0 0 0 0
wlan0: 2147483648 1 0 0 0 0 0 0 536870912 1 0 0 0 0 0 0
eth0: 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
"""

RESOLV = """# managed
nameserver 1.1.1.1
nameserver 8.8.8.8
nameserver 1.1.1.1
"""

ROUTE = """Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT
wlan0\t00000000\t0102A8C0\t0003\t0\t0\t600\t00000000\t0\t0\t0
"""

LSPCI = """00:02.0 "VGA compatible controller" "Intel Corporation" "Alder Lake-P GT2 [Iris Xe Graphics]" -r04 "Lenovo" "ThinkPad"
00:1f.3 "Audio device" "Intel Corporation" "Alder Lake PCH-P High Definition Audio Controller" -r01 "Lenovo" "ThinkPad"
01:00.0 "3D controller" "NVIDIA Corporation" "GA107M [GeForce RTX 3050 Mobile]" -r01 "Lenovo" "ThinkPad"
"""


class FakeEnvironment:
    """A virtual filesystem, process table and command runner."""

    def __init__(
        self,
        files=None,
        dirs=None,
        stats=None,
        commands=None,
        ipv4=None,
    ) -> None:
        self.files = dict(files or {})
        self.dirs = dict(dirs or {})
        self.stats = dict(stats or {})
        self.commands = dict(commands or {})
        self.ipv4 = dict(ipv4 or {})

    def read(self, path: str) -> str:
        if path in self.files:
            return self.files[path]
        raise FileNotFoundError(path)

    def listdir(self, path: str):
        if path in self.dirs:
            return list(self.dirs[path])
        raise FileNotFoundError(path)

    def statvfs(self, path: str):
        return self.stats.get(path)

    def command(self, args):
        key = tuple(args)
        if key in self.commands:
            return self.commands[key]
        raise FileNotFoundError(args[0])

    def iface_ipv4(self, name: str):
        return self.ipv4.get(name)


def build_environment() -> FakeEnvironment:
    files = {
        "/etc/os-release": OS_RELEASE,
        "/etc/timezone": "Europe/Kyiv\n",
        "/proc/uptime": UPTIME,
        "/proc/stat": STAT,
        "/proc/cpuinfo": CPUINFO,
        "/proc/meminfo": MEMINFO,
        "/proc/mounts": MOUNTS,
        "/proc/net/dev": NET_DEV,
        "/proc/net/route": ROUTE,
        "/etc/resolv.conf": RESOLV,
        "/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq": "4800000\n",
        "/sys/class/dmi/id/sys_vendor": "LENOVO\n",
        "/sys/class/dmi/id/product_name": "21CB\n",
        "/sys/class/dmi/id/board_vendor": "LENOVO\n",
        "/sys/class/dmi/id/board_name": "LNVNB161216\n",
        "/sys/class/dmi/id/bios_vendor": "LENOVO\n",
        "/sys/class/dmi/id/bios_version": "N3WET32W\n",
        "/sys/class/dmi/id/bios_date": "2026-01-15\n",
        "/sys/block/nvme0n1/device/vendor": "Samsung\n",
        "/sys/block/nvme0n1/device/model": "SSD 980 PRO 1TB\n",
        "/sys/block/nvme0n1/size": "1953525168\n",
        "/sys/block/nvme0n1/queue/rotational": "0\n",
        "/sys/block/nvme0n1/removable": "0\n",
        "/sys/block/sda/device/vendor": "Seagate\n",
        "/sys/block/sda/device/model": "ST2000DM008\n",
        "/sys/block/sda/size": "3907029168\n",
        "/sys/block/sda/queue/rotational": "1\n",
        "/sys/block/sda/removable": "0\n",
        "/sys/class/net/wlan0/operstate": "up\n",
        "/sys/class/net/wlan0/address": "aa:bb:cc:dd:ee:ff\n",
        "/sys/class/net/wlan0/speed": "866\n",
        "/sys/class/net/eth0/operstate": "down\n",
        "/sys/class/net/eth0/address": "11:22:33:44:55:66\n",
        "/sys/class/net/eth0/speed": "1000\n",
        "/sys/class/hwmon/hwmon0/name": "k10temp\n",
        "/sys/class/hwmon/hwmon0/temp1_input": "45000\n",
        "/sys/class/hwmon/hwmon0/temp1_label": "Tctl\n",
        "/sys/class/hwmon/hwmon0/fan1_input": "1200\n",
        "/sys/class/hwmon/hwmon0/fan1_label": "CPU fan\n",
        "/sys/class/hwmon/hwmon1/name": "nvme\n",
        "/sys/class/hwmon/hwmon1/temp1_input": "38000\n",
        "/sys/class/hwmon/hwmon1/temp1_label": "Composite\n",
        "/sys/class/power_supply/BAT0/capacity": "82\n",
        "/sys/class/power_supply/BAT0/status": "Discharging\n",
    }
    dirs = {
        "/sys/block": ["nvme0n1", "loop0", "sda"],
        "/sys/class/net": ["lo", "wlan0", "eth0"],
        "/sys/class/hwmon": ["hwmon0", "hwmon1"],
        "/sys/class/hwmon/hwmon0": [
            "name",
            "temp1_input",
            "temp1_label",
            "fan1_input",
            "fan1_label",
        ],
        "/sys/class/hwmon/hwmon1": ["name", "temp1_input", "temp1_label"],
        "/sys/class/power_supply": ["BAT0", "AC"],
        "/sys/class/thermal": [],
    }
    stats = {
        "/": (500 * GiB, 200 * GiB, 300 * GiB),
        "/boot/efi": (512 * MiB, 100 * MiB, 412 * MiB),
        "/home": (500 * GiB, 250 * GiB, 250 * GiB),
    }
    commands = {("lspci", "-mm"): LSPCI}
    ipv4 = {"wlan0": "192.168.2.42"}
    return FakeEnvironment(files, dirs, stats, commands, ipv4)


def make_info(environment: FakeEnvironment = None, **overrides) -> SystemInfo:
    env = environment or build_environment()
    options = dict(
        read_text=env.read,
        list_dir=env.listdir,
        statvfs=env.statvfs,
        uname=lambda: SimpleNamespace(
            sysname="Linux",
            nodename="testbox",
            release="6.8.7-arch1",
            version="#1 SMP",
            machine="x86_64",
        ),
        environ={
            "XDG_CURRENT_DESKTOP": "KDE",
            "XDG_SESSION_TYPE": "wayland",
            "SHELL": "/bin/zsh",
            "LANG": "uk_UA.UTF-8",
        },
        uid_name=lambda: "alice",
        iface_ipv4=env.iface_ipv4,
        command_output=env.command,
        now=lambda: 1700003661.5,
    )
    options.update(overrides)
    return SystemInfo(**options)


def rows_to_dict(rows) -> dict:
    return {row.label: row.value for row in rows}


class ParserTests(unittest.TestCase):
    def test_os_release_unquotes_values(self):
        data = parse_os_release(OS_RELEASE + 'EXTRA="a \\"quote\\""\n')
        self.assertEqual(data["NAME"], "Arch Linux")
        self.assertEqual(data["ID"], "arch")
        self.assertEqual(data["EXTRA"], 'a "quote"')

    def test_os_release_skips_comments_and_junk(self):
        data = parse_os_release("# comment\nBROKEN\nKEY=value\n")
        self.assertEqual(data, {"KEY": "value"})

    def test_meminfo_numeric_fields(self):
        data = parse_meminfo(MEMINFO)
        self.assertEqual(data["MemTotal"], 16777216.0)
        self.assertEqual(data["SwapFree"], 1048576.0)

    def test_meminfo_ignores_non_numeric(self):
        data = parse_meminfo("HugePages_Total: 0\nWeird: maybe\n")
        self.assertEqual(data, {"HugePages_Total": 0.0})

    def test_cpuinfo_model_cores_and_frequency(self):
        data = parse_cpuinfo(CPUINFO)
        self.assertEqual(data["model"], "Intel(R) Core(TM) i7-9700K CPU @ 3.60GHz")
        self.assertEqual(data["vendor"], "GenuineIntel")
        self.assertEqual(data["threads"], 4)
        self.assertEqual(data["physical_cores"], 4)
        self.assertEqual(data["cache"], "12288 KB")
        self.assertEqual(data["mhz"], 3600.0)

    def test_cpuinfo_multiple_sockets_multiply_cores(self):
        text = (
            "processor : 0\nprocessor : 1\n"
            "physical id : 0\nphysical id : 1\ncpu cores : 8\n"
        )
        self.assertEqual(parse_cpuinfo(text)["physical_cores"], 16)

    def test_mounts_decode_octal_escapes(self):
        mounts = parse_mounts(
            "/dev/sda1 /media/my\\040disk ext4 rw 0 0\nshort line\n"
        )
        self.assertEqual(len(mounts), 1)
        self.assertEqual(mounts[0]["mount"], "/media/my disk")
        self.assertEqual(mounts[0]["fstype"], "ext4")

    def test_net_dev_counts(self):
        data = parse_net_dev(NET_DEV)
        self.assertEqual(data["wlan0"]["rx_bytes"], 2147483648.0)
        self.assertEqual(data["wlan0"]["tx_bytes"], 536870912.0)
        self.assertIn("lo", data)

    def test_resolv_conf_dedupes(self):
        self.assertEqual(parse_resolv_conf(RESOLV), ["1.1.1.1", "8.8.8.8"])

    def test_default_gateway_little_endian(self):
        self.assertEqual(parse_default_gateway(ROUTE), "192.168.2.1")

    def test_default_gateway_absent(self):
        self.assertIsNone(parse_default_gateway("Iface Destination\n"))

    def test_lspci_machine_output(self):
        gpus = parse_lspci_gpus(LSPCI)
        self.assertEqual(len(gpus), 2)
        self.assertIn("Intel Corporation Alder Lake-P GT2", gpus[0])
        self.assertIn("NVIDIA Corporation GA107M", gpus[1])

    def test_lspci_plain_output(self):
        text = (
            "00:02.0 VGA compatible controller: Intel Corporation UHD 620 "
            "[8086:5917] (rev 07)\n"
            "00:1f.3 Audio device: Intel Corporation Sunrise Point-LP\n"
        )
        self.assertEqual(
            parse_lspci_gpus(text), ["Intel Corporation UHD 620"]
        )


class FormatTests(unittest.TestCase):
    def test_format_bytes_units(self):
        self.assertEqual(format_bytes(0), "0 B")
        self.assertEqual(format_bytes(1024), "1.0 KiB")
        self.assertEqual(format_bytes(1536), "1.5 KiB")
        self.assertEqual(format_bytes(2 * GiB), "2.0 GiB")
        self.assertEqual(format_bytes(None), "Not available")

    def test_format_uptime_units(self):
        self.assertEqual(format_uptime(0), "0s")
        self.assertEqual(format_uptime(65), "1m 5s")
        self.assertEqual(format_uptime(3661), "1h 1m")
        self.assertEqual(format_uptime(90061), "1d 1h 1m")
        self.assertEqual(format_uptime(None), "Not available")


class SummaryTests(unittest.TestCase):
    def test_summary_contains_host_os_and_session(self):
        rows = rows_to_dict(make_info().summary())
        self.assertEqual(rows["Host name"], "testbox")
        self.assertEqual(rows["Operating system"], "Arch Linux")
        self.assertEqual(rows["Kernel"], "6.8.7-arch1")
        self.assertEqual(rows["Architecture"], "x86_64")
        self.assertEqual(rows["Uptime"], "1h 1m")
        self.assertEqual(rows["User"], "alice")
        self.assertEqual(rows["Desktop"], "KDE")
        self.assertEqual(rows["Session"], "wayland")
        self.assertIn("Boot time", rows)

    def test_uptime_falls_back_to_btime(self):
        env = build_environment()
        env.files.pop("/proc/uptime")
        rows = rows_to_dict(make_info(env, now=lambda: 1700003600.0).summary())
        self.assertEqual(rows["Uptime"], "1h 0m")

    def test_uptime_unknown_when_no_sources(self):
        env = build_environment()
        env.files.pop("/proc/uptime")
        env.files["/proc/stat"] = ""
        rows = rows_to_dict(make_info(env).summary())
        self.assertEqual(rows["Uptime"], "Not available")

    def test_missing_session_values_degrade(self):
        rows = rows_to_dict(make_info(environ={}).summary())
        self.assertEqual(rows["Desktop"], "Not available")
        self.assertEqual(rows["Session"], "Not available")
        self.assertEqual(rows["User"], "alice")


class HardwareTests(unittest.TestCase):
    def test_cpu_memory_and_dmi(self):
        rows = rows_to_dict(make_info().hardware())
        self.assertEqual(
            rows["CPU"], "Intel(R) Core(TM) i7-9700K CPU @ 3.60GHz"
        )
        self.assertEqual(rows["CPU cores"], "4 threads")
        self.assertEqual(rows["CPU frequency"], "3600 MHz · max 4800 MHz")
        self.assertEqual(rows["CPU cache"], "12288 KB")
        self.assertEqual(rows["CPU vendor"], "GenuineIntel")
        self.assertEqual(rows["System"], "LENOVO 21CB")
        self.assertEqual(rows["Motherboard"], "LENOVO LNVNB161216")
        self.assertEqual(rows["BIOS"], "LENOVO N3WET32W 2026-01-15")
        self.assertEqual(rows["Memory"], "16.0 GiB total · 8.0 GiB available")
        self.assertEqual(rows["Swap"], "2.0 GiB total · 1.0 GiB free")

    def test_gpu_list_from_lspci(self):
        rows = rows_to_dict(make_info().hardware())
        self.assertIn("Intel Corporation", rows["GPU"])
        self.assertIn("NVIDIA Corporation", rows["GPU"])

    def test_gpu_not_detected_without_commands(self):
        env = build_environment()
        env.commands = {}
        rows = rows_to_dict(make_info(env).hardware())
        self.assertEqual(rows["GPU"], "Not detected")

    def test_physical_and_thread_counts(self):
        env = build_environment()
        env.files["/proc/cpuinfo"] = CPUINFO.replace("cpu cores\t: 4", "cpu cores\t: 2")
        rows = rows_to_dict(make_info(env).hardware())
        self.assertEqual(rows["CPU cores"], "2 cores / 4 threads")

    def test_no_swap(self):
        env = build_environment()
        env.files["/proc/meminfo"] = "MemTotal: 1000 kB\nMemAvailable: 500 kB\n"
        rows = rows_to_dict(make_info(env).hardware())
        self.assertEqual(rows["Swap"], "Not configured")


class StorageTests(unittest.TestCase):
    def test_disks_skip_loop_and_report_model(self):
        rows = rows_to_dict(make_info().storage())
        self.assertIn("Disk (nvme0n1)", rows)
        self.assertEqual(
            rows["Disk (nvme0n1)"],
            "Samsung SSD 980 PRO 1TB · 931.5 GiB · SSD",
        )
        self.assertEqual(
            rows["Disk (sda)"],
            "Seagate ST2000DM008 · 1.8 TiB · HDD",
        )
        self.assertNotIn("Disk (loop0)", rows)

    def test_mounts_filter_pseudo_and_report_usage(self):
        rows = rows_to_dict(make_info().storage())
        self.assertIn("Mount /", rows)
        self.assertIn("Mount /boot/efi", rows)
        self.assertNotIn("Mount /proc", rows)
        self.assertNotIn("Mount /tmp", rows)
        self.assertIn("ext4", rows["Mount /"])
        self.assertIn("300.0 GiB free of 500.0 GiB (40% used)", rows["Mount /"])
        self.assertIn("/dev/nvme0n1p2", rows["Mount /"])

    def test_mounts_sorted_root_first(self):
        rows = make_info().storage()
        labels = [row.label for row in rows if row.label.startswith("Mount")]
        self.assertEqual(labels, ["Mount /", "Mount /boot/efi", "Mount /home"])

    def test_no_storage_fallback(self):
        env = build_environment()
        env.dirs["/sys/block"] = []
        env.files["/proc/mounts"] = ""
        rows = rows_to_dict(make_info(env).storage())
        self.assertEqual(rows["Storage"], "No storage devices detected")


class NetworkTests(unittest.TestCase):
    def test_interfaces_and_traffic(self):
        rows = rows_to_dict(make_info().network())
        self.assertEqual(rows["Host name"], "testbox")
        self.assertIn("up", rows["wlan0"])
        self.assertIn("192.168.2.42", rows["wlan0"])
        self.assertIn("aa:bb:cc:dd:ee:ff", rows["wlan0"])
        self.assertIn("866 Mb/s", rows["wlan0"])
        self.assertIn("↓ 2.0 GiB ↑ 512.0 MiB", rows["wlan0"])
        self.assertIn("down", rows["eth0"])
        self.assertIn("1 Gb/s", rows["eth0"])
        self.assertNotIn("lo", rows)

    def test_gateway_and_dns(self):
        rows = rows_to_dict(make_info().network())
        self.assertEqual(rows["Default gateway"], "192.168.2.1")
        self.assertEqual(rows["DNS"], "1.1.1.1, 8.8.8.8")

    def test_loopback_used_when_only_interface(self):
        env = build_environment()
        env.dirs["/sys/class/net"] = ["lo"]
        env.files["/sys/class/net/lo/operstate"] = "unknown\n"
        env.files["/sys/class/net/lo/address"] = "00:00:00:00:00:00\n"
        rows = rows_to_dict(make_info(env).network())
        self.assertIn("lo", rows)

    def test_no_interfaces_fallback(self):
        env = build_environment()
        env.dirs["/sys/class/net"] = []
        env.files["/proc/net/dev"] = ""
        env.files["/proc/net/route"] = ""
        env.files["/etc/resolv.conf"] = ""
        rows = rows_to_dict(make_info(env).network())
        self.assertEqual(rows["Interfaces"], "No network interfaces detected")


class SoftwareTests(unittest.TestCase):
    def test_software_versions_and_locale(self):
        rows = rows_to_dict(make_info().software())
        self.assertEqual(rows["Operating system"], "Arch Linux")
        self.assertEqual(rows["Distribution"], "arch 2026.11")
        self.assertEqual(rows["Kernel"], "6.8.7-arch1")
        self.assertEqual(rows["Architecture"], "x86_64")
        self.assertEqual(rows["Desktop"], "KDE")
        self.assertEqual(rows["Session type"], "wayland")
        self.assertEqual(rows["Shell"], "/bin/zsh")
        self.assertEqual(rows["Locale"], "uk_UA.UTF-8")
        self.assertEqual(rows["Timezone"], "Europe/Kyiv")
        self.assertIn(".", rows["Python"])
        self.assertEqual(rows["Omnify"], "0.1.0")

    def test_locale_all_precedes_lang(self):
        rows = rows_to_dict(
            make_info(
                environ={"LC_ALL": "C.UTF-8", "LANG": "en_US.UTF-8"}
            ).software()
        )
        self.assertEqual(rows["Locale"], "C.UTF-8")

    def test_timezone_from_environment(self):
        rows = rows_to_dict(
            make_info(environ={"TZ": "UTC"}).software()
        )
        self.assertEqual(rows["Timezone"], "UTC")


class SensorTests(unittest.TestCase):
    def test_hwmon_temperatures_fans_and_battery(self):
        rows = rows_to_dict(make_info().sensors())
        self.assertEqual(rows["k10temp · Tctl"], "45.0 °C")
        self.assertEqual(rows["k10temp · CPU fan"], "1200 RPM")
        self.assertEqual(rows["nvme · Composite"], "38.0 °C")
        self.assertEqual(rows["Battery (BAT0)"], "82% · Discharging")

    def test_thermal_zone_fallback(self):
        env = build_environment()
        env.dirs["/sys/class/hwmon"] = []
        env.dirs["/sys/class/thermal"] = ["thermal_zone0"]
        env.files["/sys/class/thermal/thermal_zone0/type"] = "x86_pkg_temp\n"
        env.files["/sys/class/thermal/thermal_zone0/temp"] = "40000\n"
        rows = rows_to_dict(make_info(env).sensors())
        self.assertEqual(rows["x86_pkg_temp"], "40.0 °C")

    def test_invalid_readings_are_dropped(self):
        env = build_environment()
        env.files["/sys/class/hwmon/hwmon0/temp1_input"] = "999999\n"
        env.files["/sys/class/hwmon/hwmon0/fan1_input"] = "junk\n"
        rows = rows_to_dict(make_info(env).sensors())
        self.assertNotIn("k10temp · Tctl", rows)
        self.assertNotIn("k10temp · CPU fan", rows)

    def test_empty_sensors_fallback(self):
        env = build_environment()
        env.dirs["/sys/class/hwmon"] = []
        env.dirs["/sys/class/thermal"] = []
        env.dirs["/sys/class/power_supply"] = []
        rows = rows_to_dict(make_info(env).sensors())
        self.assertEqual(rows["Sensors"], "No hardware sensors detected")


class CollectAndReportTests(unittest.TestCase):
    def test_collect_order_and_titles(self):
        sections = make_info().collect()
        self.assertEqual(
            [section.key for section in sections],
            ["summary", "hardware", "storage", "network", "software", "sensors"],
        )
        self.assertEqual(sections[0].title, "Summary")
        for section in sections:
            self.assertTrue(section.rows)

    def test_collect_never_raises_with_broken_probes(self):
        def boom(*args, **kwargs):
            raise OSError("gone")

        info = SystemInfo(
            read_text=boom,
            list_dir=boom,
            statvfs=boom,
            uname=boom,
            environ={},
            uid_name=boom,
            iface_ipv4=boom,
            command_output=boom,
            now=boom,
        )
        sections = info.collect()
        self.assertEqual(len(sections), 6)
        for section in sections:
            self.assertTrue(section.rows)
        self.assertEqual(
            rows_to_dict(sections[0].rows)["Host name"], "Not available"
        )
        self.assertEqual(
            rows_to_dict(sections[-1].rows)["Sensors"],
            "No hardware sensors detected",
        )

    def test_report_contains_every_section_and_value(self):
        report = make_info().report()
        self.assertTrue(report.startswith("Omnify System Report"))
        self.assertIn("Generated:", report)
        self.assertIn("Summary", report)
        self.assertIn("Hardware", report)
        self.assertIn("Storage", report)
        self.assertIn("Network", report)
        self.assertIn("Software", report)
        self.assertIn("Sensors", report)
        self.assertIn("Host name: testbox", report)
        self.assertIn("CPU: Intel(R) Core(TM) i7-9700K", report)
        self.assertTrue(report.endswith("\n"))

    def test_report_translate_hook(self):
        report = make_info().report(
            translate=lambda text: {
                "Summary": "Резюме",
                "Host name": "Ім’я хоста",
            }.get(text, text)
        )
        self.assertIn("Резюме", report)
        self.assertIn("Ім’я хоста: testbox", report)


if __name__ == "__main__":
    unittest.main(verbosity=2)
