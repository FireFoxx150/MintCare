import types
import unittest

from mintcare import gui


class SnapshotDeleteDialog(unittest.TestCase):
    """The confirmation is Pango markup, so a snapshot name must be escaped the same way its comment is."""

    def test_snapshot_name_is_escaped_in_the_confirmation(self):
        name = 'x<a href="file:///home">open</a>'
        asked = []
        fake = types.SimpleNamespace(
            snap_sel={name: True}, snaps=[{"name": name, "comment": ""}],
            confirm=lambda title, body: asked.append(body) or False)     # answer "no": nothing is deleted
        gui.MintCareWindow.delete_snapshots(fake)
        self.assertNotIn("<a href", asked[0])
        self.assertIn("&lt;a href", asked[0])


if __name__ == "__main__":
    unittest.main()
