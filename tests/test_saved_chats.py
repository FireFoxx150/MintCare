import unittest

from mintcare import assistant


class SavedChats(unittest.TestCase):
    def setUp(self):
        assistant.CHATS_FILE.unlink(missing_ok=True)

    def test_saves_newest_first_with_title_from_first_question(self):
        assistant.save_chat("\nYou: Why is my disk full?\n\nMintCare assistant: Check the caches.\n")
        assistant.save_chat("\nYou: Sound is quiet\n\nMintCare assistant: Raise the volume.\n")
        chats = assistant.load_chats()
        self.assertEqual([c["title"] for c in chats], ["Sound is quiet", "Why is my disk full?"])
        self.assertIn("Raise the volume", chats[0]["text"])

    def test_keeps_at_most_twenty(self):
        for i in range(25):
            assistant.save_chat(f"\nYou: question {i}\n")
        self.assertEqual(len(assistant.load_chats()), 20)


if __name__ == "__main__":
    unittest.main()
