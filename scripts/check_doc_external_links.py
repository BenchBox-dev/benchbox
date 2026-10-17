#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

CLI_DESCRIPTION = "Check that external http(s) links on the published docs pages resolve."

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "docs"
IGNORE_FILE = DOCS_DIR / "linkcheck_ignore.txt"
PUBLISH_LIST_ROOTS = ("development", "operations")
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
TIMEOUT_SECONDS = 60
RETRIES = 2
WORKERS = 16
RATE_LIMITED = frozenset({429})

_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_INLINE_CODE_RE = re.compile(r"`[^`]*`")
_URL_RE = re.compile(r"https?://[^\s<>()\[\]`'\"]+")
_TRAILING = ".,;:!?*_"


@dataclass(frozen=True)
class LinkResult:
    url: str
    status: str
    detail: str


def read_path_list(path: Path) -> list[str]:
    if not path.exists():
        return []
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def load_ignore_patterns(path: Path = IGNORE_FILE) -> list[re.Pattern[str]]:
    return [re.compile(line) for line in read_path_list(path)]


def published_pages(docs_dir: Path = DOCS_DIR) -> list[Path]:
    exclusions = read_path_list(docs_dir / "publish-exclusions.txt")
    excluded_roots = tuple(entry for entry in exclusions if entry.endswith("/"))
    excluded_files = {entry for entry in exclusions if not entry.endswith("/")}
    allowlist = set(read_path_list(docs_dir / "publish-allowlist.txt"))
    pages = []
    for page in sorted(docs_dir.rglob("*.md")):
        relative = page.relative_to(docs_dir).as_posix()
        if relative.startswith(excluded_roots) or relative in excluded_files:
            continue
        if relative.split("/", 1)[0] in PUBLISH_LIST_ROOTS and relative not in allowlist:
            continue
        pages.append(page)
    return pages


def extract_urls(content: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    in_fence = False
    for line_number, line in enumerate(content.splitlines(), 1):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for match in _URL_RE.finditer(_INLINE_CODE_RE.sub("", line)):
            url = match.group(0).rstrip(_TRAILING)
            found.append((line_number, url))
    return found


def collect_links(docs_dir: Path = DOCS_DIR) -> dict[str, list[str]]:
    sources: dict[str, list[str]] = defaultdict(list)
    for doc in published_pages(docs_dir):
        relative = doc.relative_to(docs_dir.parent).as_posix()
        for line_number, url in extract_urls(doc.read_text(encoding="utf-8")):
            sources[url].append(f"{relative}:{line_number}")
    return dict(sources)


def is_ignored(url: str, patterns: list[re.Pattern[str]]) -> bool:
    return any(pattern.match(url) for pattern in patterns)


def _request(url: str, method: str) -> int:
    request = urllib.request.Request(url.split("#", 1)[0], method=method, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def check_url(url: str, request=_request) -> LinkResult:
    detail = ""
    for _ in range(RETRIES + 1):
        try:
            status = request(url, "HEAD")
            if status >= 400 and status not in RATE_LIMITED:
                status = request(url, "GET")
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            detail = str(getattr(error, "reason", error))
            continue
        if status < 400:
            return LinkResult(url, "ok", str(status))
        if status in RATE_LIMITED:
            return LinkResult(url, "rate-limited", str(status))
        detail = f"HTTP {status}"
    return LinkResult(url, "broken", detail)


def run(sources: dict[str, list[str]], patterns: list[re.Pattern[str]], check=check_url) -> list[LinkResult]:
    checked = sorted(url for url in sources if not is_ignored(url, patterns))
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        return list(pool.map(check, checked))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--report", type=Path, help="Write every checked link and its status as JSON")
    args = parser.parse_args(argv)

    sources = collect_links()
    results = run(sources, load_ignore_patterns())
    broken = [result for result in results if result.status == "broken"]
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        payload = [{"url": r.url, "status": r.status, "detail": r.detail, "sources": sources[r.url]} for r in results]
        args.report.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    for result in broken:
        print(f"broken: {result.url} ({result.detail}) in {', '.join(sources[result.url])}", file=sys.stderr)
    limited = sum(1 for result in results if result.status == "rate-limited")
    print(f"checked {len(results)} external links: {len(broken)} broken, {limited} rate-limited")
    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
