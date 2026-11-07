import unittest

from mintcare import i18n, maintenance


class GpuHistory(unittest.TestCase):
    def setUp(self):
        maintenance.GPU_TEMPS_FILE.unlink(missing_ok=True)

    def test_saved_temperatures_come_back_capped_at_sixty(self):
        maintenance.save_gpu_temps([float(t) for t in range(80)])
        self.assertEqual(maintenance.load_gpu_temps(), [float(t) for t in range(20, 80)])

    def test_missing_or_broken_file_gives_empty_history(self):
        self.assertEqual(maintenance.load_gpu_temps(), [])
        maintenance.GPU_TEMPS_FILE.parent.mkdir(parents=True, exist_ok=True)
        maintenance.GPU_TEMPS_FILE.write_text("not json")
        self.assertEqual(maintenance.load_gpu_temps(), [])

    def test_language_is_a_short_code(self):
        lang = i18n.current_language()
        self.assertTrue(lang.isalpha() and len(lang) == 2, lang)


if __name__ == "__main__":
    unittest.main()
