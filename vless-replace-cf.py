#!/usr/bin/env python3
"""Replace VLESS server addresses with Cloudflare IPs from a CSV file."""

from __future__ import annotations

import argparse
import base64
import binascii
import csv
import json
import os
import sys
import urllib.parse
from collections import OrderedDict
from pathlib import Path
from typing import Iterable


DEFAULT_CSV = "cloudflare_ips.csv"
DEFAULT_OUTPUT = "vless_urls.txt"
URLS_ENV_VAR = "VLESS_URLS_BASE64"


def find_ip_header(fieldnames: list[str]) -> str:
    """Find an IP column, accepting IP, ip, and names such as 优选IP."""
    exact_matches = [name for name in fieldnames if name.strip().casefold() == "ip"]
    if exact_matches:
        return exact_matches[0]

    suffix_matches = [name for name in fieldnames if name.strip().casefold().endswith("ip")]
    if suffix_matches:
        return suffix_matches[0]

    raise ValueError("CSV 表头中未找到 IP 列（字段名应为 IP、ip 或以 IP 结尾）")


def read_ip_groups(path: Path) -> OrderedDict[str, list[str]]:
    """Read IPs from CSV and group them by route while preserving file order."""
    with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        if not reader.fieldnames:
            raise ValueError("CSV 文件缺少表头")

        fieldnames = [name.strip() for name in reader.fieldnames]
        ip_header = find_ip_header(fieldnames)
        try:
            route_header = next(name for name in fieldnames if name == "线路")
        except StopIteration as exc:
            raise ValueError("CSV 表头中未找到“线路”列") from exc

        groups: OrderedDict[str, list[str]] = OrderedDict()
        for line_number, raw_row in enumerate(reader, start=2):
            row = {(key or "").strip(): (value or "").strip() for key, value in raw_row.items()}
            ip = row.get(ip_header, "")
            route = row.get(route_header, "")
            if not ip and not route:
                continue
            if not ip or not route:
                raise ValueError(f"CSV 第 {line_number} 行的 IP 或线路为空")
            groups.setdefault(route, []).append(ip)

    if not groups:
        raise ValueError("CSV 文件中没有有效的 IP 记录")
    return groups


def decode_vless_urls(encoded: str) -> list[str]:
    """Decode a Base64-encoded, newline-separated VLESS URL list."""
    compact = "".join(encoded.split())
    if not compact:
        raise ValueError(f"环境变量 {URLS_ENV_VAR} 为空")

    compact += "=" * (-len(compact) % 4)
    try:
        decoded = base64.b64decode(compact, altchars=b"-_", validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise ValueError(f"环境变量 {URLS_ENV_VAR} 不是有效的 Base64 UTF-8 数据") from exc

    urls = [line.strip() for line in decoded.splitlines() if line.strip()]
    if not urls:
        raise ValueError("Base64 解码后没有找到 VLESS URL")
    for index, url in enumerate(urls, start=1):
        if urllib.parse.urlsplit(url).scheme.casefold() != "vless":
            raise ValueError(f"解码结果第 {index} 行不是 VLESS URL")
    return urls


def replace_url_host(parsed: urllib.parse.SplitResult, address: str) -> urllib.parse.SplitResult:
    """Replace only the host portion of a URL netloc."""
    userinfo, separator, host_and_port = parsed.netloc.rpartition("@")
    prefix = f"{userinfo}@" if separator else ""

    if host_and_port.startswith("["):
        closing_bracket = host_and_port.find("]")
        if closing_bracket == -1:
            raise ValueError("VLESS URL 中的 IPv6 地址缺少右方括号")
        port_part = host_and_port[closing_bracket + 1 :]
    else:
        host, colon, port = host_and_port.rpartition(":")
        port_part = f":{port}" if colon and host else ""

    formatted_address = f"[{address}]" if ":" in address else address
    return parsed._replace(netloc=f"{prefix}{formatted_address}{port_part}")


def parse_download_settings(parsed: urllib.parse.SplitResult) -> tuple[int | None, dict | None]:
    """Return the index and decoded extra object when downloadSettings exists."""
    query_items = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    for index, (key, value) in enumerate(query_items):
        if key != "extra":
            continue
        try:
            extra = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("VLESS URL 的 extra 参数不是有效的 JSON") from exc
        if not isinstance(extra, dict):
            raise ValueError("VLESS URL 的 extra 参数必须是 JSON 对象")
        if "downloadSettings" not in extra:
            return None, None
        if not isinstance(extra["downloadSettings"], dict):
            raise ValueError("VLESS URL 的 downloadSettings 参数必须是 JSON 对象")
        return index, extra
    return None, None


def update_download_address(
    parsed: urllib.parse.SplitResult,
    query_index: int,
    extra: dict,
    address: str,
) -> urllib.parse.SplitResult:
    query_items = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    extra_copy = dict(extra)
    extra_copy["downloadSettings"] = dict(extra["downloadSettings"])
    extra_copy["downloadSettings"]["address"] = address
    key, _ = query_items[query_index]
    query_items[query_index] = (
        key,
        json.dumps(extra_copy, ensure_ascii=False, separators=(",", ":")),
    )
    query = urllib.parse.urlencode(query_items, doseq=True, quote_via=urllib.parse.quote)
    return parsed._replace(query=query)


def build_variants(url: str, ip_groups: OrderedDict[str, list[str]]) -> list[str]:
    """Build all Cloudflare variants for one VLESS URL."""
    parsed = urllib.parse.urlsplit(url)
    query_index, extra = parse_download_settings(parsed)
    consumes_two = query_index is not None and extra is not None
    results: list[str] = []

    for route, addresses in ip_groups.items():
        step = 2 if consumes_two else 1
        for offset in range(0, len(addresses), step):
            selected = addresses[offset : offset + step]
            if len(selected) != step:
                continue

            variant = replace_url_host(parsed, selected[0])
            if consumes_two:
                variant = update_download_address(
                    variant, query_index, extra, selected[1]
                )

            sequence = offset // step + 1
            variant = variant._replace(fragment=f"{parsed.fragment}-cf-{route}-{sequence}")
            results.append(urllib.parse.urlunsplit(variant))

    return results


def write_urls(path: Path, urls: Iterable[str]) -> int:
    url_list = list(urls)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as output_file:
        for url in url_list:
            output_file.write(f"{url}\n")
    return len(url_list)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            f"从 {URLS_ENV_VAR} 读取 Base64 编码的 VLESS URL 列表，"
            "使用 CSV 中的 Cloudflare IP 生成替换结果"
        )
    )
    parser.add_argument(
        "-c", "--csv", default=DEFAULT_CSV, help=f"IP CSV 文件（默认：{DEFAULT_CSV}）"
    )
    parser.add_argument(
        "-o", "--output", default=DEFAULT_OUTPUT, help=f"输出文件（默认：{DEFAULT_OUTPUT}）"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        encoded_urls = os.environ.get(URLS_ENV_VAR)
        if encoded_urls is None:
            raise ValueError(f"未设置环境变量 {URLS_ENV_VAR}")

        ip_groups = read_ip_groups(Path(args.csv))
        source_urls = decode_vless_urls(encoded_urls)
        results = [
            variant
            for source_url in source_urls
            for variant in build_variants(source_url, ip_groups)
        ]
        count = write_urls(Path(args.output), results)
    except (OSError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    print(f"已根据 {len(source_urls)} 个源 URL 生成 {count} 条记录：{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
