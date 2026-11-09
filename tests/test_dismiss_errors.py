"""Dismissing a kind of boot error hides it until it happens again, and never touches the journal."""
import unittest

from mintcare import journal


def group(who, sample, last, level=journal.WARN):
    return {"who": who, "count": 1, "sample": sample, "last": last, "level": level,
            "explanation": "", "action": None}


class Visibility(unittest.TestCase):
    def test_a_dismissed_kind_stays_hidden_until_it_happens_again(self):
        g = group("kernel", "FAT-fs (sdf2): error at 8059144", 100.0)
        hidden = {journal.dismiss_key(g): 100.0}
        self.assertEqual(journal.visible([g], hidden), [])
        again = dict(g, last=150.0)
        self.assertEqual(journal.visible([again], hidden), [again])

    def test_the_key_ignores_numbers_so_the_same_kind_matches(self):
        a = group("kernel", "FAT-fs (sdf2): error at 8059144", 1.0)
        b = group("kernel", "FAT-fs (sdf2): error at 9", 2.0)
        self.assertEqual(journal.dismiss_key(a), journal.dismiss_key(b))

    def test_other_kinds_are_still_shown(self):
        a = group("kernel", "FAT-fs error", 1.0)
        b = group("lightdm", "gkr-pam: couldn't unlock", 1.0)
        self.assertEqual(journal.visible([a, b], {journal.dismiss_key(a): 1.0}), [b])


class Buttons(unittest.TestCase):
    def test_notes_and_warnings_get_a_dismiss_button_that_saves_the_key_and_time(self):
        g = group("lightdm", "gkr-pam: couldn't unlock the login keyring.", 5.0, level=journal.INFO)
        [(label, kind, payload)] = journal.dismiss_actions([g])
        self.assertEqual(kind, "dismiss")
        self.assertEqual(payload, (journal.dismiss_key(g), 5.0))
        self.assertIn("gkr-pam", label)

    def test_serious_errors_and_catch_all_groups_get_no_button(self):
        serious = group("kernel", "I/O error, dev sda", 1.0, level=journal.FAIL)
        catch_all = group("(other kinds)", "", 1.0, level=journal.INFO)
        self.assertEqual(journal.dismiss_actions([serious, catch_all]), [])

    def test_long_messages_are_shortened_in_the_label(self):
        g = group("kernel", "x" * 100, 1.0)
        [(label, _kind, _payload)] = journal.dismiss_actions([g])
        self.assertLess(len(label), 80)


class Details(unittest.TestCase):
    def setUp(self):
        journal.clear_dismissed()

    def tearDown(self):
        journal.clear_dismissed()

    def test_restore_is_offered_only_while_something_is_hidden(self):
        from unittest import mock
        from mintcare import checks
        kernel = group("kernel", "FAT-fs error at 1", 100.0)
        with mock.patch.object(journal, "grouped", return_value=[kernel]):
            kinds = [a[1] for a in checks.details("booterrors")[2]]
            self.assertNotIn("restore", kinds)
            journal.set_dismissed(journal.dismiss_key(kernel), 100.0)
            kinds = [a[1] for a in checks.details("booterrors")[2]]
            self.assertIn("restore", kinds)
            self.assertNotIn("dismiss", kinds)


class Saving(unittest.TestCase):
    def test_a_dismissal_is_saved_and_cleared_and_other_settings_are_kept(self):
        from mintcare.maintenance import load_settings
        journal.clear_dismissed()
        journal.set_dismissed("kernel\tx", 42.0)
        self.assertEqual(journal.dismissed(), {"kernel\tx": 42.0})
        self.assertIn("maintenance", load_settings())
        journal.clear_dismissed()
        self.assertEqual(journal.dismissed(), {})


if __name__ == "__main__":
    unittest.main()
