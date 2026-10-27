#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

from benchbox.utils.clock import elapsed_seconds, mono_time

CLI_DESCRIPTION = (
    "Fail when a tracked Markdown file links to a missing heading anchor.\n"
    "\n"
    "A renamed heading once shipped an undetected dead anchor because only the\n"
    "site build resolved intra-doc fragments. This check scans every tracked\n"
    "``*.md`` file, extracts inline Markdown links with a ``#fragment``, and\n"
    "asserts the fragment exists in the link target: GitHub-style heading slugs\n"
    "(with ``-1``, ``-2`` suffixes for duplicate headings) plus explicit anchors\n"
    "(MyST ``(name)=`` targets, ``id``/``name`` attributes, and ``{#name}``).\n"
    "\n"
    "External URLs, ``mailto:`` and other schemes, server-absolute ``/paths``,\n"
    "code spans, fenced code blocks, and targets that are not tracked Markdown\n"
    "files are left to other checks and site routing and are not inspected here.\n"
    "\n"
    "Existing broken links in frozen site-owned documents are recorded exactly in\n"
    "quality/doc-anchor-baseline.json. New failures and stale baseline entries\n"
    "fail this check. After fixing links in their owning repository, use\n"
    "--write-baseline to shrink the baseline; it refuses to add failures.\n"
    "\n"
    "Usage:\n"
    "    python scripts/check_doc_anchors.py\n"
    "\n"
    "Exit codes:\n"
    "    0 - Every intra-doc anchor resolves\n"
    "    1 - At least one dead anchor was found\n"
)

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = "quality/doc-anchor-baseline.json"

_LINK_RE = re.compile(r"(?<!!)\[(?:[^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_INLINE_CODE_RE = re.compile(r"`[^`]*`")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_MYST_TARGET_RE = re.compile(r"^\s*\(([^()\s]+)\)=\s*$")
_HTML_ID_RE = re.compile(r"<(?:span|a|h[1-6]|div|section)\b[^>]*?\b(?:id|name)\s*=\s*[\"']([^\"']+)[\"']")
_ATTR_ANCHOR_RE = re.compile(r"\{#([A-Za-z0-9_-]+)\}")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK_TEXT_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_EMPHASIS_RE = re.compile(r"[*~]+|(?<!\w)_+|_+(?!\w)")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_NON_SLUG_RE = re.compile(r"[^\w\s-]", flags=re.UNICODE)
_WHITESPACE_RE = re.compile(r"[\s]+")


def tracked_markdown_files() -> list[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files", "*.md"],
            check=True,
            text=True,
            encoding="utf-8",
            capture_output=True,
        )
    except (subprocess.CalledProcessError, OSError):
        return sorted(
            path.relative_to(REPO_ROOT).as_posix() for path in REPO_ROOT.rglob("*.md") if "_build" not in path.parts
        )
    return sorted(line for line in result.stdout.splitlines() if line)


def slugify_heading(text: str) -> str:
    text = _INLINE_CODE_RE.sub(lambda match: match.group(0)[1:-1], text)
    text = _IMAGE_RE.sub(r"\1", text)
    text = _LINK_TEXT_RE.sub(r"\1", text)
    text = _EMPHASIS_RE.sub("", text)
    text = _HTML_TAG_RE.sub("", text)
    text = html.unescape(text).lower().strip()
    text = _NON_SLUG_RE.sub("", text)
    return _WHITESPACE_RE.sub("-", text)


def collect_anchors(content: str) -> set[str]:
    anchors: set[str] = set()
    slug_counts: dict[str, int] = {}
    in_fence = False
    for line in content.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        heading = _HEADING_RE.match(line)
        if heading is not None:
            base = slugify_heading(heading.group(1))
            seen = slug_counts.get(base, 0)
            anchors.add(base if seen == 0 else f"{base}-{seen}")
            slug_counts[base] = seen + 1
            anchors.update(_ATTR_ANCHOR_RE.findall(heading.group(0)))
        myst_target = _MYST_TARGET_RE.match(line)
        if myst_target is not None:
            anchors.add(myst_target.group(1))
        anchors.update(_HTML_ID_RE.findall(line))
    return anchors


def extract_anchor_links(content: str) -> list[tuple[int, str]]:
    results: list[tuple[int, str]] = []
    in_fence = False
    for line_num, line in enumerate(content.splitlines(), 1):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        stripped = _INLINE_CODE_RE.sub("", line)
        for match in _LINK_RE.finditer(stripped):
            target = match.group(1).strip()
            if "#" in target:
                results.append((line_num, target))
    return results


def resolve_target(source_rel: str, core: str, tracked: set[str]) -> str | None:
    if not core:
        return source_rel
    if not core.lower().endswith(".md"):
        return None
    candidate = os.path.normpath(os.path.join(os.path.dirname(source_rel), core))
    if candidate.startswith("..") or os.path.isabs(candidate):
        return None
    if candidate not in tracked:
        return None
    return candidate


def collect_broken(sources: list[str], contents: dict[str, str]) -> tuple[list[tuple[str, int, str, str]], int]:
    tracked = set(sources)
    anchor_cache: dict[str, set[str]] = {}
    broken: list[tuple[str, int, str, str]] = []
    checked = 0
    for source in sources:
        for line_num, target in extract_anchor_links(contents[source]):
            if target.startswith("/") or _SCHEME_RE.match(target):
                continue
            core, _, anchor = target.partition("#")
            anchor = anchor.strip()
            if not anchor:
                continue
            resolved = resolve_target(source, core.split("?", 1)[0], tracked)
            if resolved is None:
                continue
            checked += 1
            if resolved not in anchor_cache:
                anchor_cache[resolved] = collect_anchors(contents[resolved])
            if anchor not in anchor_cache[resolved]:
                broken.append((source, line_num, target, resolved))
    return broken, checked


def main() -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--write-baseline", action="store_true", help="Remove resolved entries; never add failures")
    args = parser.parse_args()

    started = mono_time()
    sources = tracked_markdown_files()
    contents = {source: (REPO_ROOT / source).read_text(encoding="utf-8") for source in sources}
    broken, checked = collect_broken(sources, contents)
    elapsed = elapsed_seconds(started)
    baseline_path = REPO_ROOT / BASELINE_PATH
    baseline = (
        Counter(tuple(entry) for entry in json.loads(baseline_path.read_text(encoding="utf-8")))
        if baseline_path.exists()
        else Counter()
    )
    current = Counter((source, target, resolved) for source, _, target, resolved in broken)
    regressions = current - baseline
    stale = baseline - current

    print("Checking Markdown heading anchors ...")
    print(f"  scanned files : {len(sources)}")
    print(f"  anchors checked: {checked}")
    print(f"  broken total  : {len(broken)}")
    print(f"  elapsed       : {elapsed:.1f}s (local only, no network access)")

    if regressions:
        print("\n❌ Dead Markdown anchors:")
        for source, line_num, target, resolved in broken:
            if (source, target, resolved) not in regressions:
                continue
            print(f"   {source}:{line_num} -> {target} (missing in {resolved})")
        print("\n❌ DOC ANCHOR CHECK FAILED")
        return 1

    if args.write_baseline:
        baseline_path.write_text(
            json.dumps(sorted(current.elements()), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"Baseline shrunk to {sum(current.values())} existing broken links.")
        return 0

    if stale:
        print("\nStale anchor baseline entries; run --write-baseline after fixing links:")
        for (source, target, resolved), count in sorted(stale.items()):
            print(f"   {source} -> {target} (target {resolved}, stale occurrences {count})")
        return 1

    print(f"\nEvery Markdown anchor resolves or matches the exact baseline ({sum(baseline.values())} existing links).")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  Check interrupted")
        sys.exit(1)
