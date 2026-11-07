import unittest

from mintcare.sources import Source


def kind_of(uri):
    return Source(file="test.list", line=1, fmt="list", type="deb", uri=uri, suite="noble", comps=["main"]).kind


class SourceKind(unittest.TestCase):
    def test_official_hosts_and_their_subdomains(self):
        self.assertEqual(kind_of("http://packages.linuxmint.com"), "Linux Mint")
        self.assertEqual(kind_of("https://user:pw@packages.linuxmint.com/apt"), "Linux Mint")
        self.assertEqual(kind_of("http://archive.ubuntu.com/ubuntu"), "Ubuntu")
        self.assertEqual(kind_of("http://de.archive.ubuntu.com/ubuntu"), "Ubuntu")
        self.assertEqual(kind_of("http://mirror.example.org/ubuntu"), "Ubuntu")

    def test_ppa_hosts(self):
        self.assertEqual(kind_of("http://ppa.launchpad.net/someone/tool/ubuntu"), "PPA")
        self.assertEqual(kind_of("http://ppa.launchpadcontent.net/someone/tool/ubuntu"), "PPA")

    def test_vendor_names_inside_other_hosts_or_paths_are_third_party(self):
        for uri in ("https://ubuntu.com.evil.example/apt", "https://evil.example/ubuntu.com",
                    "https://linuxmint.com.evil.example/", "https://evil.example/packages.linuxmint.com",
                    "https://brave-browser-apt-release.s3.brave.com", "https://example.com/apt"):
            with self.subTest(uri=uri):
                self.assertEqual(kind_of(uri), "Third-party")


if __name__ == "__main__":
    unittest.main()
