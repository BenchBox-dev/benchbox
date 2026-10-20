from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from .verdict import Finding, Verdict

INLINE = "inline"
_LINE_SUFFIX = re.compile(r":\d+(?:-\d+)?$")


TRACED = ("omp",)
OMP_READERS = frozenset({"read", "grep"})
_OMP_SELECTOR = re.compile(r"(?::(?:raw|[\d,+-]+))+$")
OMP_NO_MATCHES = "No matches found"
CITATION_PREFIX = "Citation not found in the head commit"


@dataclass(frozen=True)
class Trace:
    reads: tuple[str, ...] = ()


def _completed_messages(stdout: str) -> Iterable[dict]:
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "message_end" and isinstance(event.get("message"), dict):
            yield event["message"]


def _found_content(message: dict) -> bool:
    if message.get("isError") is not False:
        return False
    if message.get("toolName") != "grep":
        return True
    content = message.get("content")
    text = (
        "".join(block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text")
        if isinstance(content, list)
        else ""
    )
    return not text.strip().startswith(OMP_NO_MATCHES)


def _grep_matches(message: dict) -> tuple[str, ...]:
    details = message.get("details")
    files = details.get("files") if isinstance(details, dict) else None
    return tuple(path for path in files if isinstance(path, str) and path) if isinstance(files, list) else ()


def _omp_reads(stdout: str) -> tuple[str, ...]:
    calls: dict[str, str] = {}
    results: dict[str, tuple[str, ...] | None] = {}
    for message in _completed_messages(stdout):
        if message.get("role") == "toolResult":
            if _found_content(message):
                matched = _grep_matches(message) if message.get("toolName") == "grep" else None
                results[str(message.get("toolCallId"))] = matched
            continue
        if message.get("role") != "assistant" or not isinstance(message.get("content"), list):
            continue
        for block in message["content"]:
            if not isinstance(block, dict) or block.get("type") != "toolCall" or block.get("name") not in OMP_READERS:
                continue
            arguments = block.get("arguments")
            if isinstance(arguments, dict) and isinstance(arguments.get("path"), str):
                calls[str(block.get("id"))] = arguments["path"]
    reads: list[str] = []
    for call, path in calls.items():
        if call not in results or not path:
            continue
        matched = results[call]
        reads.extend((path,) if matched is None else matched)
    return tuple(reads)


def trace(harness: str, stdout: str) -> Trace:
    return Trace(_omp_reads(stdout)) if harness == "omp" else Trace()


def unread(harness: str, required: Iterable[str], run: Trace, workspace: Path) -> list[str]:
    if harness not in TRACED:
        return []
    parts = (part.strip() for path in run.reads for part in path.split(";"))
    read = {relative(_OMP_SELECTOR.sub("", part), workspace) for part in parts if part}
    return [path for path in required if path not in read]


def relative(path: str, workspace: Path) -> str:
    path = _LINE_SUFFIX.sub("", path)
    root = os.path.realpath(workspace)
    candidate = os.path.normpath(path if os.path.isabs(path) else os.path.join(root, path))
    if candidate.startswith(root + os.sep):
        candidate = candidate[len(root) + 1 :]
    elif os.path.realpath(candidate).startswith(root + os.sep):
        candidate = os.path.realpath(candidate)[len(root) + 1 :]
    return candidate.removeprefix("./")


def _line_count(workspace: Path, path: str) -> int | None:
    root = os.path.realpath(workspace)
    target = os.path.realpath(os.path.join(root, path))
    if not target.startswith(root + os.sep) or not os.path.isfile(target):
        return None
    with open(target, "rb") as handle:
        data = handle.read()
    return max(1, data.count(b"\n") + (0 if data.endswith(b"\n") or not data else 1))


def _citation_error(finding: Finding, workspace: Path) -> str | None:
    count = _line_count(workspace, finding.file)
    if count is None:
        return f"{finding.file} is not a file in the head commit"
    last = finding.end_line or finding.line
    if last > count:
        return f"{finding.file} has {count} lines, so line {last} does not exist"
    return None


def mark_citations(verdict: Verdict, workspace: Path) -> Verdict:
    marked = []
    for finding in verdict.defects:
        error = _citation_error(finding, workspace)
        detail = f"{CITATION_PREFIX}: {error}. {finding.detail}".rstrip() if error else finding.detail
        marked.append(replace(finding, detail=detail))
    return replace(verdict, defects=tuple(marked))


def _shown(paths: list[str]) -> str:
    return ", ".join(paths[:5]) + (f" and {len(paths) - 5} more" if len(paths) > 5 else "")


def check(
    verdict: Verdict,
    *,
    ships: bool,
    harness: str,
    brief_mode: str,
    workspace: Path,
    required: Iterable[str],
    run: Trace,
) -> str | None:
    if brief_mode == INLINE or not ships:
        return None
    needed = sorted(set(required))
    if harness in TRACED:
        if not run.reads:
            return "the verdict would ship but its trace shows no successful file read"
        missing = unread(harness, needed, run, workspace)
        if missing:
            return f"the verdict would ship but its trace shows no read of {_shown(missing)}"
    examined = {relative(path, workspace) for path in verdict.files_examined}
    missing = [path for path in needed if path not in examined]
    if missing:
        return f"the verdict would ship but did not examine {_shown(missing)}"
    return None
