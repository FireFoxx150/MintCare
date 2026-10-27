import unittest

from mintcare import hardware

DMI = """Handle 0x0010, DMI type 16, 23 bytes
Physical Memory Array

Handle 0x0011, DMI type 17, 40 bytes
Memory Device
\tSize: 16 GB
\tType: DDR4
\tSpeed: 3200 MT/s
\tConfigured Memory Speed: 3200 MT/s

Handle 0x0012, DMI type 17, 40 bytes
Memory Device
\tSize: No Module Installed
\tType: Unknown
\tSpeed: Unknown

Handle 0x0013, DMI type 17, 40 bytes
Memory Device
\tSize: 16 GB
\tType: DDR4
\tSpeed: Unknown
"""


class MemoryDetail(unittest.TestCase):
    def test_installed_modules_only(self):
        self.assertEqual(hardware.memory_modules(DMI), [("DDR4", "3200 MT/s"), ("DDR4", "")])


if __name__ == "__main__":
    unittest.main()
