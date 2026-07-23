#!/usr/bin/env python3
"""Merge generated VLESS URLs into one file in an existing GitHub Gist."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from collections import OrderedDict
from pathlib import Path
from urllib.error import HTTPError, URLError


API_ROOT = "https://api.github.com"
API_VERSION = "2026-03-10"
USER_AGENT = "vless-replace-cf-gist-sync/1.0"


def url_key(url: str) -> tuple[str, str]:
    """Use a VLESS fragment as its name; fall back to the complete line."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.casefold() == "vless" and parsed.fragment:
        return "vless-name", urllib.parse.unquote(parsed.fragment)
    return "line", url


def unique_urls(content: str) -> OrderedDict[tuple[str, str], str]:
    """Deduplicate non-empty lines, keeping first position and latest value."""
    result: OrderedDict[tuple[str, str], str] = OrderedDict()
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if line:
            result[url_key(line)] = line
    return result


def merge_urls(remote_content: str, local_content: str) -> str:
    """Merge URL lists with local entries replacing remote entries of the same name."""
    merged = unique_urls(remote_content)
    for key, url in unique_urls(local_content).items():
        merged[key] = url
    return "".join(f"{url}\n" for url in merged.values())


def github_request(
    url: str,
    token: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
) -> dict:
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": API_VERSION,
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def read_remote_file(gist: dict, filename: str) -> str:
    files = gist.get("files")
    if not isinstance(files, dict):
        raise ValueError("GitHub API 返回的 Gist 数据中缺少 files")

    gist_file = files.get(filename)
    if gist_file is None:
        return ""
    if not isinstance(gist_file, dict):
        raise ValueError(f"Gist 文件 {filename!r} 的信息无效")

    content = gist_file.get("content", "")
    if not gist_file.get("truncated"):
        return content if isinstance(content, str) else ""

    raw_url = gist_file.get("raw_url")
    if not isinstance(raw_url, str) or not raw_url.startswith("https://"):
        raise ValueError(f"无法获取被截断的 Gist 文件 {filename!r}")
    request = urllib.request.Request(
        raw_url,
        headers={"Accept": "text/plain", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"未设置或未正确配置环境变量 {name}")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="合并本地 VLESS URL 并同步到 GitHub Gist")
    parser.add_argument(
        "local_file",
        nargs="?",
        default="vless_urls.txt",
        help="本地 URL 文件（默认：vless_urls.txt）",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        token = require_env("GIST_TOKEN")
        gist_id = require_env("GIST_ID")
        filename = require_env("GIST_FILENAME")
        local_content = Path(args.local_file).read_text(encoding="utf-8")
        if not unique_urls(local_content):
            raise ValueError("本地 URL 文件为空，已停止同步以避免意外覆盖")

        gist_url = f"{API_ROOT}/gists/{urllib.parse.quote(gist_id, safe='')}"
        gist = github_request(gist_url, token)
        remote_content = read_remote_file(gist, filename)
        merged_content = merge_urls(remote_content, local_content)
        github_request(
            gist_url,
            token,
            method="PATCH",
            payload={"files": {filename: {"content": merged_content}}},
        )
    except HTTPError as exc:
        try:
            details = exc.read().decode("utf-8", errors="replace")
        except OSError:
            details = str(exc)
        print(f"Gist API 请求失败（HTTP {exc.code}）：{details}", file=sys.stderr)
        return 1
    except (OSError, UnicodeError, URLError, ValueError) as exc:
        print(f"同步失败：{exc}", file=sys.stderr)
        return 1

    print(
        f"已将 {len(unique_urls(local_content))} 条本地 URL 合并同步到 "
        f"Gist {gist_id} 的 {filename}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
