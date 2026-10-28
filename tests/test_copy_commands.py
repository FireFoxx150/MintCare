import unittest

from mintcare import assistant


class CopyCommands(unittest.TestCase):
    def test_finds_each_code_block_without_the_fences(self):
        text = "Run this:\n```bash\nsudo apt update\n```\nand then:\n```\nls -la\n```\nDone."
        self.assertEqual(assistant.commands_in(text), ["sudo apt update", "ls -la"])

    def test_no_code_blocks_means_no_commands(self):
        self.assertEqual(assistant.commands_in("Just prose."), [])


if __name__ == "__main__":
    unittest.main()
