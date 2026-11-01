import http.client
import io
import unittest

from mintcare import packages


class Reply:
    """Stands in for an HTTP reply: a body, and the headers urllib would give."""

    def __init__(self, data, declared=None):
        self._body = io.BytesIO(data)
        self.headers = {} if declared is None else {"Content-Length": str(declared)}

    def read(self, n=-1):
        return self._body.read(n)


class CappedListing(unittest.TestCase):
    def test_a_listing_within_the_cap_is_returned(self):
        self.assertEqual(packages._capped(Reply(b"abc", 3), cap=10), b"abc")

    def test_a_reply_without_a_length_is_read_to_the_end(self):
        self.assertEqual(packages._capped(Reply(b"abc"), cap=10), b"abc")

    def test_a_listing_over_the_cap_is_refused_not_cut_short(self):
        self.assertIsNone(packages._capped(Reply(b"x" * 20, 20), cap=10))

    def test_a_reply_cut_short_raises_as_it_did_before(self):
        with self.assertRaises(http.client.IncompleteRead):
            packages._capped(Reply(b"abc", 10), cap=10)


if __name__ == "__main__":
    unittest.main()
