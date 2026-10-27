from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from typing import Any

from .policy import SEVERITIES
from .verdict import Finding

_MARKER = re.compile(r"<!-- oracle-finding: (?P<print>[0-9a-f]{16}) -->\s*\Z")
_TITLE = re.compile(r"^\*\*(?P<severity>Critical|High|Medium|Low)\*\*: (?P<title>.+)$", re.MULTILINE)
_NOT_WORD = re.compile(r"[^a-z0-9]+")


def fingerprint(path: str, title: str) -> str:
    normal = _NOT_WORD.sub(" ", title.lower()).strip()
    return hashlib.sha256(f"{path}\0{normal}".encode()).hexdigest()[:16]


def marker(finding: Finding) -> str:
    return f"<!-- oracle-finding: {fingerprint(finding.file, finding.title)} -->"


def thread_fingerprint(path: str | None, body: str) -> str | None:
    match = _MARKER.search(body.rstrip())
    if match is not None:
        return match.group("print")
    title = _TITLE.search(body)
    return fingerprint(path, title.group("title").strip()) if path and title else None


def open_fingerprints(threads: Iterable[Mapping[str, Any]], login: str) -> dict[str, str]:
    prints: dict[str, str] = {}
    for thread in threads:
        if (
            thread.get("resolved")
            or thread.get("edited")
            or thread.get("author_type") != "Bot"
            or (thread.get("author") or "").removesuffix("[bot]") != login
        ):
            continue
        body = thread.get("body") or ""
        key = thread_fingerprint(thread.get("path"), body)
        title = _TITLE.search(body)
        if key is not None:
            severity = title.group("severity") if title else SEVERITIES[-1]
            prints[key] = min(prints.get(key, severity), severity, key=SEVERITIES.index)
    return dict(sorted(prints.items()))


def split(
    findings: Iterable[Finding], already_open: Mapping[str, str] | Iterable[str]
) -> tuple[tuple[Finding, ...], tuple[Finding, ...]]:
    known = dict(already_open) if isinstance(already_open, Mapping) else dict.fromkeys(already_open, SEVERITIES[0])
    fresh: list[Finding] = []
    repeated: list[Finding] = []
    for finding in findings:
        key = fingerprint(finding.file, finding.title)
        posted = known.get(key)
        covered = posted is not None and SEVERITIES.index(posted) <= SEVERITIES.index(finding.severity)
        (repeated if covered else fresh).append(finding)
        if not covered:
            known[key] = finding.severity
    return tuple(fresh), tuple(repeated)
