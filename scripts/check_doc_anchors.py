#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path
from urllib.parse import unquote

from benchbox.utils.clock import elapsed_seconds, mono_time

CLI_DESCRIPTION = (
    "Fail when a tracked Markdown file links to a missing file or heading anchor.\n"
    "\n"
    "A renamed heading once shipped an undetected dead anchor because only the\n"
    "site build resolved intra-doc fragments, and a link into a deleted page\n"
    "passed silently. This check scans every tracked ``*.md`` file and extracts\n"
    "inline Markdown links. A relative link must point at a tracked file or\n"
    "directory. A ``#fragment`` into a tracked Markdown file must exist there:\n"
    "GitHub-style heading slugs (with ``-1``, ``-2`` suffixes for duplicate\n"
    "headings) plus explicit anchors (MyST ``(name)=`` targets, ``id``/``name``\n"
    "attributes, and ``{#name}``).\n"
    "\n"
    "External URLs, ``mailto:`` and other schemes, server-absolute ``/paths``,\n"
    "code spans, and fenced code blocks are not inspected. Source files under the\n"
    "roots in EXCLUDED_SOURCE_ROOTS are archival records and are not checked.\n"
    "\n"
    "Usage:\n"
    "    python scripts/check_doc_anchors.py\n"
    "\n"
    "Exit codes:\n"
    "    0 - Every relative link and anchor resolves\n"
    "    1 - At least one missing file or dead anchor was found\n"
)

REPO_ROOT = Path(__file__).resolve().parent.parent

EXCLUDED_SOURCE_ROOTS: dict[str, str] = {
    "_project/decisions/": "archival decision records; links reflect the tree at the time they were written",
    "_project/specs/": "archival specifications; links reflect the tree at the time they were written",
    "_project/audits/": "archival audit reports; links reflect the tree at the time they were written",
    "_project/todo-db-export/": "generated tracker export; not hand-edited",
}

_LINK_RE = re.compile(r"(?<!!)\[(?:[^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_INLINE_CODE_RE = re.compile(r"`[^`]*`")
_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
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
_WHITESPACE_RE = re.compile(r"\s")


def tracked_files() -> list[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files", "-z"],
            check=True,
            text=True,
            encoding="utf-8",
            capture_output=True,
        )
    except (subprocess.CalledProcessError, OSError):
        return sorted(
            path.relative_to(REPO_ROOT).as_posix()
            for path in REPO_ROOT.rglob("*")
            if path.is_file() and "_build" not in path.parts
        )
    return sorted(entry for entry in result.stdout.split("\0") if entry)


def tracked_directories(files: Iterable[str]) -> set[str]:
    directories: set[str] = set()
    for file in files:
        parent = os.path.dirname(file)
        while parent and parent not in directories:
            directories.add(parent)
            parent = os.path.dirname(parent)
    return directories


def is_excluded_source(source: str) -> bool:
    return any(source.startswith(root) for root in EXCLUDED_SOURCE_ROOTS)


def slugify_heading(text: str) -> str:
    parts = re.split(r"(`[^`]*`)", text)
    pieces: list[str] = []
    for part in parts:
        if part.startswith("`") and part.endswith("`") and len(part) > 1:
            pieces.append(part[1:-1])
            continue
        part = _IMAGE_RE.sub(r"\1", part)
        part = _LINK_TEXT_RE.sub(r"\1", part)
        part = _EMPHASIS_RE.sub("", part)
        pieces.append(_HTML_TAG_RE.sub("", part))
    text = html.unescape("".join(pieces)).lower().strip()
    text = _NON_SLUG_RE.sub("", text)
    return _WHITESPACE_RE.sub("-", text)


def prose_lines(content: str) -> Iterator[tuple[int, str]]:
    stack: list[tuple[str, int, bool]] = []
    for line_num, line in enumerate(content.splitlines(), 1):
        fence = _FENCE_RE.match(line)
        if fence is not None:
            marker, info = fence.group(1), fence.group(2).strip()
            if stack and marker[0] == stack[-1][0] and len(marker) >= stack[-1][1] and not info:
                stack.pop()
            elif not stack or not stack[-1][2]:
                stack.append((marker[0], len(marker), not info.startswith("{")))
            continue
        if not stack or not stack[-1][2]:
            yield line_num, line


def collect_anchors(content: str) -> set[str]:
    anchors: set[str] = set()
    slug_counts: dict[str, int] = {}
    for _, line in prose_lines(content):
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


def extract_links(content: str) -> list[tuple[int, str]]:
    results: list[tuple[int, str]] = []
    for line_num, line in prose_lines(content):
        stripped = _INLINE_CODE_RE.sub("", line)
        for match in _LINK_RE.finditer(stripped):
            target = match.group(1).strip()
            if target:
                results.append((line_num, target))
    return results


def resolve_target(source_rel: str, core: str) -> str | None:
    if not core:
        return source_rel
    candidate = os.path.normpath(os.path.join(os.path.dirname(source_rel), unquote(core)))
    if candidate == ".." or candidate.startswith("../") or os.path.isabs(candidate):
        return None
    return candidate


def collect_broken(
    sources: list[str], contents: dict[str, str], files: Iterable[str]
) -> tuple[list[tuple[str, int, str, str]], int]:
    file_set = set(files)
    directories = tracked_directories(file_set)
    anchor_cache: dict[str, set[str]] = {}
    broken: list[tuple[str, int, str, str]] = []
    checked = 0
    for source in sources:
        if is_excluded_source(source):
            continue
        for line_num, target in extract_links(contents[source]):
            if target.startswith("/") or _SCHEME_RE.match(target):
                continue
            core, _, anchor = target.partition("#")
            core = core.split("?", 1)[0]
            anchor = anchor.strip()
            resolved = resolve_target(source, core)
            checked += 1
            if resolved is None or (resolved not in file_set and resolved.rstrip("/") not in directories):
                broken.append((source, line_num, target, "missing file"))
                continue
            if not anchor or resolved not in contents or not resolved.lower().endswith(".md"):
                continue
            if resolved not in anchor_cache:
                anchor_cache[resolved] = collect_anchors(contents[resolved])
            if anchor not in anchor_cache[resolved]:
                broken.append((source, line_num, target, f"missing anchor in {resolved}"))
    return broken, checked


def main() -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.parse_args()

    started = mono_time()
    files = tracked_files()
    sources = [file for file in files if file.lower().endswith(".md")]
    contents = {source: (REPO_ROOT / source).read_text(encoding="utf-8") for source in sources}
    broken, checked = collect_broken(sources, contents, files)
    elapsed = elapsed_seconds(started)

    print("Checking Markdown links and anchors ...")
    print(f"  scanned files : {len(sources)}")
    print(f"  links checked : {checked}")
    print(f"  broken total  : {len(broken)}")
    print(f"  elapsed       : {elapsed:.1f}s (local only, no network access)")

    if broken:
        print("\n❌ Broken Markdown links:")
        for source, line_num, target, reason in broken:
            print(f"   {source}:{line_num} -> {target} ({reason})")
        print("\n❌ DOC LINK CHECK FAILED")
        return 1

    print("\n✅ Every relative Markdown link and anchor resolves.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  Check interrupted")
        sys.exit(1)
