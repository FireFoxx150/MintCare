import unittest

from mintcare import processes


class GpuProcesses(unittest.TestCase):
    """The programs holding graphics memory, read from nvidia-smi's compute-apps list."""

    def test_largest_first_and_commas_in_names_are_kept(self):
        text = "1234, python3, 2048\n555, app, with comma, 512\n777, [N/A], [N/A]\n"
        self.assertEqual(processes.parse_gpu_processes(text),
                         [(1234, "python3", 2048), (555, "app, with comma", 512)])

    def test_no_programs_gives_an_empty_list(self):
        self.assertEqual(processes.parse_gpu_processes(""), [])


if __name__ == "__main__":
    unittest.main()
