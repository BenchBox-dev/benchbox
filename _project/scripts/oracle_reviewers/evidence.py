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


def _omp_reads(stdout: str) -> tuple[str, ...]:
    calls: dict[str, str] = {}
    succeeded: set[str] = set()
    for message in _completed_messages(stdout):
        if message.get("role") == "toolResult" and message.get("isError") is False:
            succeeded.add(str(message.get("toolCallId")))
            continue
        if message.get("role") != "assistant" or not isinstance(message.get("content"), list):
            continue
        for block in message["content"]:
            if not isinstance(block, dict) or block.get("type") != "toolCall" or block.get("name") not in OMP_READERS:
                continue
            arguments = block.get("arguments")
            if isinstance(arguments, dict) and isinstance(arguments.get("path"), str):
                calls[str(block.get("id"))] = arguments["path"]
    return tuple(path for call, path in calls.items() if call in succeeded and path)


def trace(harness: str, stdout: str) -> Trace:
    return Trace(_omp_reads(stdout)) if harness == "omp" else Trace()


def unread(harness: str, required: Iterable[str], run: Trace, workspace: Path) -> list[str]:
    if harness not in TRACED:
        return []
    read = {relative(_OMP_SELECTOR.sub("", path), workspace) for path in run.reads}
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
