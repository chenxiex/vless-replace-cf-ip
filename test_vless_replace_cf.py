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
            json.dumps(
                {
                    "downloadSettings": {
                        "address": "old.example",
                        "security": "tls",
                    }
                }
            )
        )
        variants = MODULE.build_variants(
            f"vless://uuid@example.com:443?security=tls&extra={extra}#node",
            self.groups,
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
            "vless://first@example.com:443?security=tls#one",
            "vless://second@example.com:443?security=tls#two",
        ]
        all_variants = [MODULE.build_variants(url, self.groups) for url in urls]

        self.assertEqual([len(items) for items in all_variants], [3, 3])
        self.assertEqual(
            [urllib.parse.urlsplit(items[0]).hostname for items in all_variants],
            ["192.0.2.1", "192.0.2.1"],
        )

    def test_limits_generated_url_count_for_each_route(self):
        groups = OrderedDict(
            [
                ("电信", ["192.0.2.1", "192.0.2.2", "192.0.2.3"]),
                ("联通", ["198.51.100.1", "198.51.100.2"]),
            ]
        )

        variants = MODULE.build_variants(
            "vless://uuid@example.com:443?security=tls#node",
            groups,
            limit_per_route=1,
        )

        self.assertEqual(
            [urllib.parse.urlsplit(item).hostname for item in variants],
            ["192.0.2.1", "198.51.100.1"],
        )

    def test_limit_counts_urls_when_each_variant_uses_two_ips(self):
        groups = OrderedDict(
            [("电信", [f"192.0.2.{index}" for index in range(1, 11)])]
        )
        extra = urllib.parse.quote(
            json.dumps(
                {
                    "downloadSettings": {
                        "address": "old.example",
                        "security": "tls",
                    }
                }
            )
        )

        variants = MODULE.build_variants(
            f"vless://uuid@example.com:443?security=tls&extra={extra}#node",
            groups,
            limit_per_route=4,
        )

        self.assertEqual(len(variants), 4)
        self.assertEqual(
            [urllib.parse.urlsplit(item).hostname for item in variants],
            ["192.0.2.1", "192.0.2.3", "192.0.2.5", "192.0.2.7"],
        )
        self.assertEqual(
            [
                json.loads(dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(item).query))["extra"])[
                    "downloadSettings"
                ]["address"]
                for item in variants
            ],
            ["192.0.2.2", "192.0.2.4", "192.0.2.6", "192.0.2.8"],
        )

    def test_only_outer_tls_address_is_replaced(self):
        extra = urllib.parse.quote(
            json.dumps(
                {
                    "downloadSettings": {
                        "address": "download.example",
                        "security": "reality",
                    }
                }
            )
        )

        variants = MODULE.build_variants(
            f"vless://uuid@outer.example:443?security=tls&extra={extra}#node",
            self.groups,
        )

        self.assertEqual(len(variants), 3)
        parsed = urllib.parse.urlsplit(variants[0])
        self.assertEqual(parsed.hostname, "192.0.2.1")
        params = dict(urllib.parse.parse_qsl(parsed.query))
        self.assertEqual(
            json.loads(params["extra"])["downloadSettings"]["address"],
            "download.example",
        )

    def test_only_download_tls_address_is_replaced(self):
        extra = urllib.parse.quote(
            json.dumps(
                {
                    "downloadSettings": {
                        "address": "download.example",
                        "security": "tls",
                    }
                }
            )
        )

        variants = MODULE.build_variants(
            f"vless://uuid@outer.example:443?security=reality&extra={extra}#node",
            self.groups,
        )

        self.assertEqual(len(variants), 3)
        parsed = urllib.parse.urlsplit(variants[0])
        self.assertEqual(parsed.hostname, "outer.example")
        params = dict(urllib.parse.parse_qsl(parsed.query))
        self.assertEqual(
            json.loads(params["extra"])["downloadSettings"]["address"],
            "192.0.2.1",
        )

    def test_original_url_is_preserved_when_neither_address_group_uses_tls(self):
        extra = urllib.parse.quote(
            json.dumps(
                {
                    "downloadSettings": {
                        "address": "download.example",
                        "security": "reality",
                    }
                }
            )
        )

        source = f"vless://uuid@outer.example:443?security=reality&extra={extra}#node"
        variants = MODULE.build_variants(source, self.groups)

        self.assertEqual(variants, [source])

    def test_download_group_without_address_is_not_created(self):
        extra = urllib.parse.quote(
            json.dumps({"downloadSettings": {"security": "tls"}})
        )

        source = f"vless://uuid@outer.example:443?security=reality&extra={extra}#node"
        variants = MODULE.build_variants(source, self.groups)

        self.assertEqual(variants, [source])


if __name__ == "__main__":
    unittest.main()
