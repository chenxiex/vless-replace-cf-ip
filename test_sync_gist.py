import importlib.util
import unittest
import urllib.parse
from pathlib import Path


SCRIPT = Path(__file__).with_name("sync_gist.py")
SPEC = importlib.util.spec_from_file_location("sync_gist", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def vless(name: str, host: str) -> str:
    return f"vless://uuid@{host}:443#{urllib.parse.quote(name)}"


class SyncGistTests(unittest.TestCase):
    def test_local_same_name_replaces_remote(self):
        remote = f"{vless('same', 'old.example')}\n"
        local = f"{vless('same', 'new.example')}\n"

        self.assertEqual(MODULE.merge_urls(remote, local), local)

    def test_preserves_unique_urls_from_both_sides(self):
        remote_only = vless("remote", "remote.example")
        old_shared = vless("shared", "old.example")
        new_shared = vless("shared", "new.example")
        local_only = vless("local", "local.example")

        merged = MODULE.merge_urls(
            f"{remote_only}\n{old_shared}\n",
            f"{new_shared}\n{local_only}\n",
        ).splitlines()

        self.assertEqual(merged, [remote_only, new_shared, local_only])

    def test_compares_url_decoded_names(self):
        remote = "vless://uuid@old.example:443#node%20one"
        local = "vless://uuid@new.example:443#node one"

        self.assertEqual(MODULE.merge_urls(remote, local), f"{local}\n")

    def test_deduplicates_exact_unnamed_lines(self):
        url = "vless://uuid@example.com:443"
        self.assertEqual(MODULE.merge_urls(f"{url}\n", f"{url}\n"), f"{url}\n")


if __name__ == "__main__":
    unittest.main()
