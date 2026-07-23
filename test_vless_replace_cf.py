import csv
import importlib.util
import json
import tempfile
import unittest
import urllib.parse
from collections import OrderedDict
from pathlib import Path


SCRIPT = Path(__file__).with_name("vless-replace-cf.py")
SPEC = importlib.util.spec_from_file_location("vless_replace_cf", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class VlessReplaceCfTests(unittest.TestCase):
    def setUp(self):
        self.groups = OrderedDict(
            [("电信", ["192.0.2.1", "192.0.2.2"]), ("联通", ["198.51.100.1"])]
        )

    def test_reads_fetcher_csv_headers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "ips.csv"
            with csv_path.open("w", encoding="utf-8-sig", newline="") as csv_file:
                writer = csv.writer(csv_file)
                writer.writerow(["#", "线路", "优选IP", "延迟"])
                writer.writerow(["1", "电信", "192.0.2.1", "10ms"])

            self.assertEqual(
                MODULE.read_ip_groups(csv_path),
                OrderedDict([("电信", ["192.0.2.1"])]),
            )

    def test_url_without_download_settings_consumes_one_ip(self):
        variants = MODULE.build_variants(
            "vless://uuid@example.com:443?security=tls#node", self.groups
        )

        self.assertEqual(len(variants), 3)
        self.assertEqual(urllib.parse.urlsplit(variants[0]).hostname, "192.0.2.1")
        self.assertTrue(variants[0].endswith("#node-cf-电信-1"))
        self.assertTrue(variants[1].endswith("#node-cf-电信-2"))
        self.assertTrue(variants[2].endswith("#node-cf-联通-1"))

    def test_url_with_download_settings_consumes_same_route_pair(self):
        extra = urllib.parse.quote(
            json.dumps({"downloadSettings": {"address": "old.example"}})
        )
        variants = MODULE.build_variants(
            f"vless://uuid@example.com:443?extra={extra}#node", self.groups
        )

        self.assertEqual(len(variants), 1)
        parsed = urllib.parse.urlsplit(variants[0])
        self.assertEqual(parsed.hostname, "192.0.2.1")
        params = dict(urllib.parse.parse_qsl(parsed.query))
        self.assertEqual(
            json.loads(params["extra"])["downloadSettings"]["address"],
            "192.0.2.2",
        )
        self.assertTrue(variants[0].endswith("#node-cf-电信-1"))

    def test_each_source_url_starts_with_full_ip_list(self):
        urls = [
            "vless://first@example.com:443#one",
            "vless://second@example.com:443#two",
        ]
        all_variants = [MODULE.build_variants(url, self.groups) for url in urls]

        self.assertEqual([len(items) for items in all_variants], [3, 3])
        self.assertEqual(
            [urllib.parse.urlsplit(items[0]).hostname for items in all_variants],
            ["192.0.2.1", "192.0.2.1"],
        )


if __name__ == "__main__":
    unittest.main()
