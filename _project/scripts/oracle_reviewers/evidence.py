from __future__ import annotations

import fnmatch
import json
import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from .verdict import FIXED, NOT_FIXED, WITHDRAWN, Finding, Verdict, display_path, sanitize

INLINE = "inline"
_LINE_SUFFIX = re.compile(r":\d+(?:-\d+)?$")


TRACED = ("omp",)
OMP_READERS = frozenset({"read", "grep"})
_CHUNK = r"(?:L?\d+(?:(?:-|\.\.)(?:L?\d+)?|\+L?\d+)?|-\d+)"
_RANGES = rf"{_CHUNK}(?:,:?{_CHUNK})*"
_OMP_SELECTOR = re.compile(rf":(?:raw(?::{_RANGES})?|{_RANGES}(?::raw)?)$")
ARCHIVE_EXTENSIONS = (
    "tar.gz|tar.bz2|tar.xz|tar.zst|tar.z|tar|tgz|tbz2|tbz|txz|tzst|zip|jar|war|ear|apk|whl|ipa|xpi|vsix|nupkg|cbz"
    "|rar|cbr|7z|iso|cab|cpio|rpm|ar|a|lib|deb|lzh|lha|arj|asar|gz|bz2|xz|zst|z|lzma"
)
_ARCHIVE_READ = re.compile(
    rf"(?P<container>.+?\.(?:{ARCHIVE_EXTENSIONS}))(?::(?P<member>.*))?\Z", re.IGNORECASE | re.DOTALL
)
BINARY_ARCHIVE_ENTRY = "[Cannot read binary archive entry"
BINARY_PROBE_BYTES = 8000
FONT_EXTENSIONS = frozenset({".eot", ".otf", ".ttf", ".woff", ".woff2"})
_TRACE_PATHS = 5
_TRACE_PATH_WIDTH = 80
_TRACE_LIMIT = 700
OMP_NO_MATCHES = "No matches found"
CITATION_PREFIX = "Citation not found in the head commit"
_WITHDRAWAL_CITATION = re.compile(r"(?P<path>[A-Za-z0-9_][A-Za-z0-9_./-]*\.[A-Za-z0-9]+):(?P<line>\d+)")
_REPRO_CITATION = re.compile(r"(?i)\brepro\s*:\s*(?P<path>[A-Za-z0-9_][A-Za-z0-9_./-]*\.[A-Za-z0-9]+)")
UNVERIFIED_WITHDRAWAL = (
    "the withdrawal names no examined head-commit file and line that refutes it, so it counts as not fixed"
)
UNVERIFIED_MAINTAIN = (
    "the maintain names no examined head-commit file and line or reproduction, so the disposition is unsupported"
)
DEMOTED_FIXED = (
    "the fix names no verified examined head-commit file and line or reproduction, so it counts as not fixed"
)


@dataclass(frozen=True)
class Trace:
    reads: tuple[str, ...] = ()
    greps: tuple[str, ...] = ()
    elisions: tuple[tuple[str, int, int], ...] = ()
    failed: int = 0


def _completed_messages(stdout: str) -> Iterable[dict]:
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "message_end" and isinstance(event.get("message"), dict):
            yield event["message"]


@dataclass(frozen=True)
class _Outcome:
    found: bool
    text: str
    spans: int
    lines: int


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _outcome(message: dict) -> _Outcome:
    content = message.get("content")
    text = (
        "".join(block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text")
        if isinstance(content, list)
        else ""
    )
    details = message.get("details")
    summary = details.get("summary") if isinstance(details, dict) else None
    spans, lines = (
        (_count(summary.get(key)) for key in ("elidedSpans", "elidedLines")) if isinstance(summary, dict) else (0, 0)
    )
    found = message.get("isError") is False
    if found and message.get("toolName") == "grep":
        found = not text.strip().startswith(OMP_NO_MATCHES)
    return _Outcome(found, text, spans, lines)


def _archive_container(part: str) -> str | None:
    match = None if ";" in part else _ARCHIVE_READ.fullmatch(part)
    if match is None:
        return None
    member = match.group("member") or ""
    if "?" in member or ".." in member.split("/"):
        return None
    return match.group("container")


def _omp_reads(stdout: str) -> Trace:
    calls: dict[str, tuple[str, str]] = {}
    results: dict[str, _Outcome] = {}
    for message in _completed_messages(stdout):
        if message.get("role") == "toolResult":
            results[str(message.get("toolCallId"))] = _outcome(message)
            continue
        if message.get("role") != "assistant" or not isinstance(message.get("content"), list):
            continue
        for block in message["content"]:
            if not isinstance(block, dict) or block.get("type") != "toolCall" or block.get("name") not in OMP_READERS:
                continue
            arguments = block.get("arguments")
            if isinstance(arguments, dict) and isinstance(arguments.get("path"), str) and arguments["path"]:
                calls[str(block.get("id"))] = (str(block.get("name")), arguments["path"])
    reads: list[str] = []
    greps: list[str] = []
    elisions: list[tuple[str, int, int]] = []
    failed = 0
    for call, (name, path) in calls.items():
        outcome = results.get(call)
        if outcome is None or not outcome.found:
            failed += 1
            continue
        if name == "grep":
            greps.append(path)
            continue
        if _archive_container(path) is not None and outcome.text.lstrip().startswith(BINARY_ARCHIVE_ENTRY):
            failed += 1
            continue
        reads.append(path)
        if outcome.spans or outcome.lines:
            elisions.append((path, outcome.spans, outcome.lines))
    return Trace(tuple(reads), tuple(greps), tuple(elisions), failed)


def trace(harness: str, stdout: str) -> Trace:
    return _omp_reads(stdout) if harness == "omp" else Trace()


def _plain(path: str) -> str | None:
    segments = path.split("/")
    colon = next((index for index, segment in enumerate(segments) if ":" in segment), None)
    if colon is not None and ".." in segments[colon:]:
        return None
    return path


def _inside(root: str, candidate: str) -> bool:
    return os.path.realpath(os.path.join(root, candidate)).startswith(root + os.sep)


def _literal(root: str, part: str, workspace: Path) -> str | None:
    target = _plain(part)
    if target is None:
        return None
    candidate = _normalize(target, workspace)
    return candidate if _inside(root, candidate) and os.path.exists(os.path.join(root, candidate)) else None


def _ambiguous(root: str, part: str, workspace: Path) -> bool:
    literal = _literal(root, part, workspace)
    if literal is None:
        return False
    for match in re.finditer(r"[:?]", part):
        prefix = part[: match.start()]
        if not prefix:
            continue
        candidate = _normalize(prefix, workspace)
        if candidate != literal and _inside(root, candidate) and os.path.isfile(os.path.join(root, candidate)):
            return True
    return False


def credited(run: Trace, workspace: Path) -> frozenset[str]:
    root = os.path.realpath(workspace)
    read: set[str] = set()
    for path in run.reads:
        parts = [item.strip() for item in path.split(";")]
        for part in parts:
            if not part or _ambiguous(root, part, workspace):
                continue
            if (literal := _literal(root, part, workspace)) is not None:
                read.add(literal)
                continue
            container = _archive_container(path.strip()) if len(parts) == 1 else None
            target = container if container is not None else _plain(_OMP_SELECTOR.sub("", part))
            if target is not None and _inside(root, candidate := _normalize(target, workspace)):
                read.add(candidate)
    for path in run.greps:
        request = path.strip()
        if not request or ";" in request or _ambiguous(root, request, workspace):
            continue
        literal = _literal(root, request, workspace)
        if literal is not None:
            if os.path.isfile(os.path.join(root, literal)):
                read.add(literal)
            continue
        target = _plain(_OMP_SELECTOR.sub("", request))
        if target is None:
            continue
        candidate = _normalize(target, workspace)
        if _inside(root, candidate) and os.path.isfile(os.path.join(root, candidate)):
            read.add(candidate)
    return frozenset(read)


def unread(harness: str, required: Iterable[str], run: Trace, workspace: Path) -> list[str]:
    if harness not in TRACED:
        return []
    return _missing(required, credited(run, workspace), workspace)


def _real(path: str, workspace: Path) -> str:
    return os.path.realpath(os.path.join(os.path.realpath(workspace), path))


def _missing(required: Iterable[str], seen: Iterable[str], workspace: Path) -> list[str]:
    real = {_real(path, workspace) for path in seen}
    return [path for path in required if _real(path, workspace) not in real]


def _clip(path: str) -> str:
    return path if len(path) <= _TRACE_PATH_WIDTH else "\u2026" + path[-(_TRACE_PATH_WIDTH - 1) :]


def _listed(paths: Iterable[str]) -> str:
    items = list(paths)
    shown = ", ".join(_clip(item) for item in items[:_TRACE_PATHS])
    return f"[{shown}{f' and {len(items) - _TRACE_PATHS} more' if len(items) > _TRACE_PATHS else ''}]"


def describe(run: Trace, required: Iterable[str], workspace: Path) -> str:
    needed = sorted(set(required))
    seen = credited(run, workspace)
    text = (
        f"trace: reads={len(run.reads)} greps={len(run.greps)} failed={run.failed}"
        f" credited={_listed(sorted(seen))} required={len(needed)}"
        f" missing={_listed(_missing(needed, seen, workspace))}"
        f" elided_reads={len(run.elisions)} elided_spans={sum(item[1] for item in run.elisions)}"
        f" elided_lines={sum(item[2] for item in run.elisions)}"
    )
    return sanitize(text, _TRACE_LIMIT)


def _normalize(path: str, workspace: Path) -> str:
    root = os.path.realpath(workspace)
    candidate = os.path.normpath(path if os.path.isabs(path) else os.path.join(root, path))
    if candidate.startswith(root + os.sep):
        candidate = candidate[len(root) + 1 :]
    elif os.path.realpath(candidate).startswith(root + os.sep):
        candidate = os.path.realpath(candidate)[len(root) + 1 :]
    return candidate.removeprefix("./")


def relative(path: str, workspace: Path) -> str:
    return _normalize(_LINE_SUFFIX.sub("", path), workspace)


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
        return f"{display_path(finding.file)} is not a file in the head commit"
    last = finding.end_line or finding.line
    if last > count:
        return f"{display_path(finding.file)} has {count} lines, so line {last} does not exist"
    return None


def _withdrawal_verified(evidence: str, workspace: Path, read: frozenset[str]) -> bool:
    for match in _WITHDRAWAL_CITATION.finditer(evidence or ""):
        line = int(match.group("line"))
        if line < 1:
            continue
        cited = match.group("path").removeprefix("./")
        if cited not in read:
            continue
        count = _line_count(workspace, cited)
        if count is not None and line <= count:
            return True
    return False


def withdrawal_cited(evidence_text: str | None) -> bool:
    return _WITHDRAWAL_CITATION.search(evidence_text or "") is not None


def _repro_verified(evidence: str, workspace: Path, read: frozenset[str]) -> bool:
    root = os.path.realpath(workspace)
    for match in _REPRO_CITATION.finditer(evidence or ""):
        cited = match.group("path").removeprefix("./")
        if cited not in read:
            continue
        target = os.path.realpath(os.path.join(root, cited))
        if not target.startswith(root + os.sep) or not os.path.isfile(target):
            continue
        return True
    return False


def maintain_verified(evidence: str, workspace: Path, read: frozenset[str]) -> bool:
    return _withdrawal_verified(evidence, workspace, read) or _repro_verified(evidence, workspace, read)


def mark_citations(
    verdict: Verdict, workspace: Path, disputed: Iterable[str] = (), read: frozenset[str] = frozenset()
) -> Verdict:
    marked = []
    for finding in verdict.defects:
        error = _citation_error(finding, workspace)
        detail = f"{CITATION_PREFIX}: {error}. {finding.detail}".rstrip() if error else finding.detail
        marked.append(replace(finding, detail=detail))
    wanted = set(disputed)
    priors = []
    for prior in verdict.prior_defects:
        if prior.status == WITHDRAWN and not _withdrawal_verified(prior.evidence, workspace, read):
            note = f"{prior.evidence} {UNVERIFIED_WITHDRAWAL}".strip()
            prior = replace(prior, status=NOT_FIXED, evidence=note, demoted="withdrawal")
        if prior.status == FIXED and prior.id in wanted and not maintain_verified(prior.evidence, workspace, read):
            note = f"{prior.evidence} {DEMOTED_FIXED}".strip()
            prior = replace(prior, status=NOT_FIXED, evidence=note, demoted="fixed")
        if prior.status == NOT_FIXED and prior.id in wanted and not maintain_verified(prior.evidence, workspace, read):
            note = f"{prior.evidence} {UNVERIFIED_MAINTAIN}".strip()
            prior = replace(prior, evidence=note)
        priors.append(prior)
    return replace(verdict, defects=tuple(marked), prior_defects=tuple(priors))


def _shown(paths: list[str]) -> str:
    return ", ".join(paths[:5]) + (f" and {len(paths) - 5} more" if len(paths) > 5 else "")


@dataclass(frozen=True)
class Gap:
    reason: str
    files: tuple[str, ...]


def is_binary_candidate(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in FONT_EXTENSIONS


def has_nul_head(workspace: Path, path: str) -> bool:
    root = os.path.realpath(workspace)
    target = os.path.realpath(os.path.join(root, path))
    if not target.startswith(root + os.sep) or not os.path.isfile(target):
        return False
    try:
        with open(target, "rb") as handle:
            return b"\0" in handle.read(BINARY_PROBE_BYTES)
    except OSError:
        return False


def _segments_match(path: tuple[str, ...], pattern: tuple[str, ...]) -> bool:
    if not pattern:
        return not path
    if pattern[0] == "**":
        return any(_segments_match(path[skip:], pattern[1:]) for skip in range(len(path) + 1))
    return bool(path) and fnmatch.fnmatchcase(path[0], pattern[0]) and _segments_match(path[1:], pattern[1:])


def glob_match(path: str, pattern: str) -> bool:
    return _segments_match(tuple(path.split("/")), tuple(pattern.split("/")))


def check(
    verdict: Verdict,
    *,
    ships: bool,
    harness: str,
    brief_mode: str,
    workspace: Path,
    required: Iterable[str],
    run: Trace,
    binary_files: Iterable[str] = (),
    exempt_paths: Iterable[str] = (),
    is_protected: Callable[[str], bool] | None = None,
) -> Gap | None:
    if brief_mode == INLINE or not ships:
        return None
    if is_protected is None:
        from _project.scripts.inert_paths import is_machine_read_doc
        from _project.scripts.soundness_paths import is_soundness_path

        def is_protected(path: str) -> bool:
            return is_soundness_path(path) or is_machine_read_doc(path)

    hinted = {relative(p, workspace) for p in binary_files}
    detected_binary = {path for path in hinted if is_binary_candidate(path) and has_nul_head(workspace, path)}
    patterns = tuple(exempt_paths)

    def _is_exempt(path: str) -> bool:
        norm = relative(path, workspace)
        if is_protected(norm) or is_protected(_normalize(path, workspace)):
            return False
        return norm in detected_binary or any(glob_match(norm, pattern) for pattern in patterns)

    required_paths = set(required)
    needed = sorted(path for path in required_paths if not _is_exempt(path))
    if harness in TRACED:
        if not run.reads and not run.greps:
            return Gap("the verdict would ship but its trace shows no successful file read", tuple(needed))
        missing = unread(harness, needed, run, workspace)
        if missing:
            return Gap(f"the verdict would ship but its trace shows no read of {_shown(missing)}", tuple(missing))
    examined = {relative(path, workspace) for path in verdict.files_examined}
    missing = [path for path in needed if path not in examined]
    if missing:
        return Gap(f"the verdict would ship but did not examine {_shown(missing)}", tuple(missing))
    return None
