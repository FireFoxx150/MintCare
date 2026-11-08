"""Updates install only a release whose SHA256SUMS is signed by the release key shipped in the package."""
import hashlib
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from mintcare import selfupdate
from tests.helpers import TempDirTest

URL = "https://example.test/release/"


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
