"""Updates install only a release whose SHA256SUMS is signed by the release key shipped in the package."""
import hashlib
import io
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from mintcare import selfupdate
from tests.helpers import TempDirTest

URL = "https://example.test/release/"


class FakeStream(io.BytesIO):
    """A download that counts how much was read from a stream of `total` bytes."""
    def __init__(self, total, headers=None):
        super().__init__()
        self.total, self.read_total, self.headers = total, 0, headers or {}

    def read(self, n=-1):
        left = self.total - self.read_total
        n = left if n < 0 else min(n, left)
        self.read_total += n
        return b"x" * n


class DownloadLimit(TempDirTest):
    """download() stops reading once the package is bigger than the release says, so a bad answer cannot fill memory."""
    ASSET = {"asset": "https://example.test/mintcare.deb", "sha256": "0" * 64}

    def fetch(self, stream, info):
        with mock.patch.object(selfupdate.urllib.request, "urlopen", return_value=stream):
            return selfupdate.download(info)

    def test_a_stream_longer_than_the_release_size_is_cut_off(self):
        stream = FakeStream(3 << 20)
        with self.assertRaises(RuntimeError):
            self.fetch(stream, dict(self.ASSET, size=1000))
        self.assertLess(stream.read_total, 2 << 20)

    def test_a_declared_length_over_the_limit_is_refused_before_reading(self):
        stream = FakeStream(3 << 20, headers={"Content-Length": str(3 << 20)})
        with self.assertRaises(RuntimeError):
            self.fetch(stream, dict(self.ASSET, size=1000))
        self.assertEqual(stream.read_total, 0)

    def test_an_unknown_size_is_capped_too(self):
        stream = FakeStream(3 << 20)
        with mock.patch.object(selfupdate, "MAX_PACKAGE", 1000), self.assertRaises(RuntimeError):
            self.fetch(stream, dict(self.ASSET))
        self.assertLess(stream.read_total, 2 << 20)

    def test_a_normal_download_is_returned_and_checked(self):
        data = b"x" * 5000
        info = {"asset": self.ASSET["asset"], "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        self.assertEqual(self.fetch(FakeStream(len(data)), info), data)


@unittest.skipUnless(shutil.which("openssl"), "needs the openssl command")
class SignedRelease(TempDirTest):
    def setUp(self):
        super().setUp()
        self.key = self._keypair("key")
        self.pub = self.key + ".pub"
        subprocess.run(["openssl", "pkey", "-in", self.key, "-pubout", "-out", self.pub], check=True, capture_output=True)
        self.name = "mintcare_9.9.9_all.deb"
        self.sha = hashlib.sha256(os.urandom(3000)).hexdigest()
        self.served = {}

    def _keypair(self, name):
        path = os.path.join(self.tmp, f"{name}.pem")
        subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", path], check=True, capture_output=True)
        return path

    def _sign(self, text, key=None):
        msg = os.path.join(self.tmp, "SHA256SUMS")
        Path(msg).write_text(text)
        sig = os.path.join(self.tmp, "SHA256SUMS.sig")
        subprocess.run(["openssl", "pkeyutl", "-sign", "-inkey", key or self.key, "-rawin", "-in", msg, "-out", sig],
                       check=True, capture_output=True)
        return Path(sig).read_bytes()

    def _release(self, text=None, sig=None, sha=None):
        """A release whose files are served from memory: the checksum list, its signature and GitHub's digest."""
        text = f"{self.sha}  {self.name}\n" if text is None else text
        sig = self._sign(text) if sig is None else sig
        self.served = {URL + "SHA256SUMS": text.encode(), URL + "SHA256SUMS.sig": sig}
        return {"asset_name": self.name, "sha256": sha, "sums": URL + "SHA256SUMS", "sums_sig": URL + "SHA256SUMS.sig"}

    def _verify(self, info, key=None):
        with mock.patch.object(selfupdate, "_fetch", side_effect=lambda url, timeout=30: self.served[url]):
            return selfupdate.signed_release(info, key=key or self.pub)

    def test_a_signed_list_gives_the_package_digest(self):
        self.assertEqual(self._verify(self._release())["sha256"], self.sha)

    def test_a_github_digest_that_disagrees_is_refused(self):
        with self.assertRaises(RuntimeError):
            self._verify(self._release(sha="0" * 64))

    def test_a_list_changed_after_signing_is_refused(self):
        sig = self._sign(f"{self.sha}  {self.name}\n")
        with self.assertRaises(RuntimeError):
            self._verify(self._release(text=f"{'1' * 64}  {self.name}\n", sig=sig))

    def test_a_signature_from_another_key_is_refused(self):
        other = self._keypair("other")
        with self.assertRaises(RuntimeError):
            self._verify(self._release(sig=self._sign(f"{self.sha}  {self.name}\n", key=other)))

    def test_a_package_the_signed_list_does_not_name_is_refused(self):
        with self.assertRaises(RuntimeError):
            self._verify(self._release(text=f"{self.sha}  mintcare_8.0.0_all.deb\n"))

    def test_an_install_without_the_release_key_refuses_to_update(self):
        with self.assertRaises(RuntimeError):
            self._verify(self._release(), key=os.path.join(self.tmp, "missing.pub"))

    def test_an_unsigned_release_is_refused(self):
        info = self._release()
        info["sums_sig"] = None
        with self.assertRaises(RuntimeError):
            self._verify(info)

    def test_both_sha256sum_formats_are_read(self):
        self.assertEqual(selfupdate._digests(f"{self.sha}  a.deb\n{'2' * 64} *b.deb\n"),
                         [(self.sha, "a.deb"), ("2" * 64, "b.deb")])


if __name__ == "__main__":
    unittest.main()
