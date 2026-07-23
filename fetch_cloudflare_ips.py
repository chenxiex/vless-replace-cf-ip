#!/usr/bin/env python3
"""Fetch Cloudflare preferred IP data from api.uouin.com and save it as CSV."""

from __future__ import annotations

import argparse
import csv
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_URL = "https://api.uouin.com/cloudflare.html"
DEFAULT_OUTPUT = "cloudflare_ips.csv"
USER_AGENT = "Mozilla/5.0 (compatible; cloudflare-ip-csv-exporter/1.0)"


class TableParser(HTMLParser):
    """Collect the text content of every HTML table."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table_depth = 0
        self._current_table: list[list[str]] | None = None
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        del attrs
        tag = tag.lower()
        if tag == "table":
            self._table_depth += 1
            if self._table_depth == 1:
                self._current_table = []
        elif self._table_depth == 1 and tag == "tr":
            self._current_row = []
        elif (
            self._table_depth == 1
            and tag in {"th", "td"}
            and self._current_row is not None
        ):
            self._current_cell = []

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if (
            self._table_depth == 1
            and tag in {"th", "td"}
            and self._current_cell is not None
            and self._current_row is not None
        ):
            text = " ".join("".join(self._current_cell).split())
            self._current_row.append(text)
            self._current_cell = None
        elif self._table_depth == 1 and tag == "tr":
            if self._current_row and self._current_table is not None:
                self._current_table.append(self._current_row)
            self._current_row = None
        elif tag == "table" and self._table_depth:
            if self._table_depth == 1 and self._current_table:
                self.tables.append(self._current_table)
                self._current_table = None
            self._table_depth -= 1


def fetch_html(url: str, timeout: float) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def extract_ip_table(html: str) -> tuple[list[str], list[list[str]]]:
    parser = TableParser()
    parser.feed(html)

    for table in parser.tables:
        if not table:
            continue
        headers = table[0]
        if "优选IP" not in headers:
            continue

        width = len(headers)
        rows = [row for row in table[1:] if len(row) == width]
        if not rows:
            raise ValueError("找到了优选 IP 表格，但表格中没有有效数据行")
        return headers, rows

    raise ValueError("页面中未找到包含“优选IP”列的表格，网页结构可能已改变")


def write_csv(path: Path, headers: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(headers)
        writer.writerows(rows)


def exclude_ipv6_rows(headers: list[str], rows: list[list[str]]) -> list[list[str]]:
    """Exclude records whose route is IPV6."""
    try:
        route_index = headers.index("线路")
    except ValueError as exc:
        raise ValueError("优选 IP 表格中缺少“线路”列") from exc
    return [row for row in rows if row[route_index].upper() != "IPV6"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="抓取 Cloudflare 优选 IP 表格并导出为 CSV"
    )
    parser.add_argument(
        "-o", "--output", default=DEFAULT_OUTPUT, help=f"输出文件（默认：{DEFAULT_OUTPUT}）"
    )
    parser.add_argument("--url", default=DEFAULT_URL, help=argparse.SUPPRESS)
    parser.add_argument("--timeout", type=float, default=30.0, help="请求超时秒数（默认：30）")
    parser.add_argument(
        "--include-ipv6",
        action="store_true",
        help="保留线路为 IPV6 的记录（默认排除）",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.timeout <= 0:
        print("错误：--timeout 必须大于 0", file=sys.stderr)
        return 2

    output = Path(args.output).expanduser()
    try:
        html = fetch_html(args.url, args.timeout)
        headers, rows = extract_ip_table(html)
        if not args.include_ipv6:
            rows = exclude_ipv6_rows(headers, rows)
        write_csv(output, headers, rows)
    except (HTTPError, URLError, TimeoutError) as exc:
        print(f"抓取失败：{exc}", file=sys.stderr)
        return 1
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"导出失败：{exc}", file=sys.stderr)
        return 1

    print(f"已导出 {len(rows)} 条记录到 {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
