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

    def test_inventory_runs(self):
        kinds = [k for k, _n, _d in hardware.inventory()]
        self.assertIn("cpu", kinds)
        self.assertIn("memory", kinds)


if __name__ == "__main__":
    unittest.main()
