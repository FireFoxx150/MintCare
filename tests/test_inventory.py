import unittest

from mintcare import hardware

LSPCI = '''0000:01:00.0 "VGA compatible controller [0300]" "NVIDIA Corporation [10de]" "GA106 [GeForce RTX 3060 Lite Hash Rate] [2504]" -ra1 -p00 "Micro-Star International Co., Ltd. [MSI] [1462]" "GA106 [GeForce RTX 3060 Lite Hash Rate] [397d]"
0000:01:00.1 "Audio device [0403]" "NVIDIA Corporation [10de]" "GA106 High Definition Audio Controller [228e]" -ra1 -p00 "Micro-Star International Co., Ltd. [MSI] [1462]" "GA106 High Definition Audio Controller [397d]"
0000:04:00.0 "Ethernet controller [0200]" "Realtek Semiconductor Co., Ltd. [10ec]" "RTL8111/8168/8211/8411 PCI Express Gigabit Ethernet Controller [8168]" -r15 -p00 "ASUSTeK Computer Inc. [1043]" "Onboard RTL8111H Ethernet [8677]"
0000:03:00.0 "Network controller [0280]" "Intel Corporation [8086]" "Wi-Fi 6 AX200 [2723]" -r1a "Intel Corporation [8086]" "Wi-Fi 6 AX200NGW [0084]"
0000:0a:00.0 "VGA compatible controller [0300]" "Advanced Micro Devices, Inc. [AMD/ATI] [1002]" "Navi 23 [Radeon RX 6600/6600 XT/6600M] [73ff]" -rc7 -p00 "" ""
'''


class Inventory(unittest.TestCase):
    def test_parse_lspci(self):
        self.assertEqual(hardware.parse_lspci(LSPCI), [
            ("gpu", "0000:01:00.0", "NVIDIA GeForce RTX 3060 Lite Hash Rate"),
            ("network", "0000:04:00.0", "Realtek RTL8111/8168/8211/8411 PCI Express Gigabit Ethernet Controller"),
            ("network", "0000:03:00.0", "Intel Wi-Fi 6 AX200"),
            ("gpu", "0000:0a:00.0", "AMD Radeon RX 6600/6600 XT/6600M"),
        ])

    def test_vram_size(self):
        self.assertEqual(hardware._gb(12288 * 2 ** 20), "12 GB")
        self.assertEqual(hardware._gb(512 * 2 ** 20), "0.5 GB")

    def test_usb_name(self):
        lsusb = ("Bus 003 Device 004: ID 33fa:0010  UGREEN BT5.4 Adapter\n"
                 "Bus 001 Device 002: ID 1a40:0101 Terminus Technology Inc. Hub\n")
        self.assertEqual(hardware._usb_name(lsusb, "33fa", "0010"), "UGREEN BT5.4 Adapter")
        self.assertEqual(hardware._usb_name(lsusb, "1a40", "0101"), "Terminus Technology Inc. Hub")
        self.assertEqual(hardware._usb_name(lsusb, "dead", "beef"), "")

    def test_core_count(self):
        # Two physical CPUs with two cores each; a block without core ids (ARM style) is skipped
        blocks = []
        for phys in ("0", "1"):
            for core in ("0", "1"):
                blocks.append(f"physical id\t: {phys}\ncore id\t\t: {core}\nmodel name\t: X")
        blocks.append("processor\t: 9\nmodel name\t: ARM")
        self.assertEqual(hardware._core_count("\n\n".join(blocks)), 4)
        # Cores reported without a physical id count as one package
        self.assertEqual(hardware._core_count("core id : 0\n\ncore id : 1"), 2)
        self.assertEqual(hardware._core_count("processor : 0"), 0)

    def test_gpu_load_parses_nvidia_smi(self):
        from unittest import mock
        out = "NVIDIA GeForce RTX 3060, 48, 285, 12288, 49\n"
        with mock.patch.object(hardware, "has_cmd", return_value=True), \
                mock.patch.object(hardware, "run", return_value=(0, out)):
            g = hardware.gpu_load()
        self.assertEqual(g["name"], "RTX 3060")
        self.assertEqual(g["util"], 48.0)
        self.assertEqual(g["vram_used"], 285 * 2 ** 20)
        self.assertEqual(g["vram_total"], 12288 * 2 ** 20)
        self.assertEqual(g["temp"], 49.0)

    def test_gpu_load_unknown_values_are_none(self):
        from unittest import mock
        with mock.patch.object(hardware, "has_cmd", return_value=True), \
                mock.patch.object(hardware, "run", return_value=(0, "NVIDIA GeForce GTX 970, [N/A], 512, 4096, [N/A]\n")):
            g = hardware.gpu_load()
        self.assertIsNone(g["util"])
        self.assertIsNone(g["temp"])
        self.assertEqual(g["vram_total"], 4096 * 2 ** 20)

    def test_inventory_runs(self):
        kinds = [k for k, _n, _d in hardware.inventory()]
        self.assertIn("cpu", kinds)
        self.assertIn("memory", kinds)


if __name__ == "__main__":
    unittest.main()


class WirelessDetail(unittest.TestCase):
    def test_signal_from_proc_net_wireless(self):
        from unittest import mock
        table = ("Inter-| sta-|   Quality        |\n"
                 " face | tus | link level noise |\n"
                 " wlp3s0: 0000   70.  -52.  -256        0      0      0      0      0        0\n")
        with mock.patch.object(hardware, "read", return_value=table):
            self.assertEqual(hardware._signal("wlp3s0"), "-52 dBm")
            self.assertEqual(hardware._signal("eth0"), "")


class PowerReading(unittest.TestCase):
    def test_no_power_sensor_means_no_reading(self):
        from unittest import mock
        with mock.patch("glob.glob", return_value=[]):
            self.assertIsNone(hardware.power_draw())

    def test_battery_reading_is_in_watts(self):
        from unittest import mock
        with mock.patch.object(hardware, "glob", side_effect=lambda pat: [] if "hwmon" in pat else
                               ["/sys/class/power_supply/BAT0/power_now"]), \
                mock.patch.object(hardware, "read", return_value="12500000\n"):
            self.assertEqual(hardware.power_draw(), 12.5)


class LinkSpeed(unittest.TestCase):
    def test_gigabit_and_megabit_links(self):
        from unittest import mock
        with mock.patch.object(hardware, "glob", return_value=["/sys/class/net/enp4s0"]), \
                mock.patch.object(hardware.os.path, "realpath", return_value="/sys/devices/pci0000:00/0000:04:00.0"), \
                mock.patch.object(hardware, "read", return_value="1000\n"):
            self.assertEqual(hardware._link_speed("0000:04:00.0"), "1 Gb/s link")
        with mock.patch.object(hardware, "glob", return_value=["/sys/class/net/eth1"]), \
                mock.patch.object(hardware.os.path, "realpath", return_value="/sys/devices/0000:05:00.0"), \
                mock.patch.object(hardware, "read", return_value="100\n"):
            self.assertEqual(hardware._link_speed("0000:05:00.0"), "100 Mb/s link")
