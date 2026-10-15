from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .policy import SEVERITIES

MAX_LISTED = 50
MAX_SUMMARY = 4000
MAX_REASON = 1000
MAX_TITLE = 200
MAX_DETAIL = 4000
MAX_EVIDENCE = 1000
MAX_PATH = 400
MAX_EXAMINED = 2000

COMPLETE = "complete"
INCOMPLETE = "incomplete"
STATUSES = (COMPLETE, INCOMPLETE)
SHIP = "SHIP"
SHIP_WITH_FIXES = "SHIP_WITH_FIXES"
DO_NOT_SHIP = "DO_NOT_SHIP"
NO_DECISION = "NONE"
DECISIONS = (SHIP, SHIP_WITH_FIXES, DO_NOT_SHIP, NO_DECISION)
FIXED = "fixed"
NOT_FIXED = "not_fixed"
WITHDRAWN = "withdrawn"
PRIOR_STATUSES = (FIXED, NOT_FIXED, WITHDRAWN)

FINDING_KEYS = ("severity", "file", "line", "end_line", "title", "detail")
OPTIONAL_FINDING_KEYS = frozenset({"end_line"})
PRIOR_KEYS = ("id", "status", "evidence")
VERDICT_KEYS = ("status", "incomplete_reason", "decision", "summary", "files_examined", "defects", "prior_defects")
TEXT_HARNESSES = frozenset({"muse", "text"})

VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": list(VERDICT_KEYS),
    "properties": {
        "status": {"type": "string", "enum": list(STATUSES)},
        "incomplete_reason": {"type": "string"},
        "decision": {"type": "string", "enum": list(DECISIONS)},
        "summary": {"type": "string"},
        "files_examined": {"type": "array", "items": {"type": "string"}},
        "defects": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": list(FINDING_KEYS),
                "properties": {
                    "severity": {"type": "string", "enum": list(SEVERITIES)},
                    "file": {"type": "string"},
                    "line": {"type": "integer"},
                    "end_line": {"type": ["integer", "null"]},
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                },
            },
        },
        "prior_defects": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": list(PRIOR_KEYS),
                "properties": {
                    "id": {"type": "string"},
                    "status": {"type": "string", "enum": list(PRIOR_STATUSES)},
                    "evidence": {"type": "string"},
                },
            },
        },
    },
}

SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.DOTALL),
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"),
    re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    re.compile(
        r"(?i)\b[\w-]*?(api[_-]?key|access[_-]?token|auth[_-]?token|token|secret|password|private[_-]?key)\b(\s*[:=]\s*)"
        r"['\"]?[^\s'\"]{8,}"
    ),
)
_MARKDOWN_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_URL = re.compile(r"(?i)\b(?:https?|ftp|file|data|javascript):[^\s<>()]+|\bwww\.[^\s<>()]+")
_MENTION = re.compile(r"(?<![A-Za-z0-9_`])@(?=[A-Za-z0-9])")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_COMMENT_OPEN = re.compile(r"<\s*!\s*-\s*-")
_COMMENT_CLOSE = re.compile(r"-\s*-\s*>")
REDACTED = "[redacted]"
LINK_REMOVED = "[link removed]"


class VerdictError(ValueError):
    pass


@dataclass(frozen=True)
class Finding:
    severity: str
    file: str
    line: int
    title: str
    detail: str
    end_line: int | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "file": self.file,
            "line": self.line,
            "end_line": self.end_line,
            "title": self.title,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class PriorDefect:
    id: str
    status: str
    evidence: str

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "status": self.status, "evidence": self.evidence}


@dataclass(frozen=True)
class Verdict:
    status: str
    incomplete_reason: str
    decision: str
    summary: str
    files_examined: tuple[str, ...]
    defects: tuple[Finding, ...]
    prior_defects: tuple[PriorDefect, ...] = ()
    defect_count: int | None = None

    @property
    def listed(self) -> int:
        return len(self.defects) if self.defect_count is None else self.defect_count

    def to_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "incomplete_reason": self.incomplete_reason,
            "decision": self.decision,
            "summary": self.summary,
            "files_examined": list(self.files_examined),
            "defects": [finding.to_json() for finding in self.defects],
            "prior_defects": [prior.to_json() for prior in self.prior_defects],
            "defect_count": self.listed,
        }


def sanitize(text: str, limit: int) -> str:
    cleaned = _CONTROL.sub("", text)
    for pattern in SECRET_PATTERNS:
        cleaned = pattern.sub(REDACTED, cleaned)
    cleaned = _MARKDOWN_LINK.sub(lambda match: match.group(1), cleaned)
    cleaned = _URL.sub(LINK_REMOVED, cleaned)
    cleaned = _MENTION.sub("", cleaned)
    cleaned = _COMMENT_OPEN.sub("&lt;!--", cleaned)
    cleaned = _COMMENT_CLOSE.sub("--&gt;", cleaned)
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise VerdictError(message)


def _valid_path(path: str) -> bool:
    parts = path.split("/")
    return (
        0 < len(path) <= MAX_PATH
        and not path.startswith("/")
        and "\\" not in path
        and all(part not in ("", ".", "..") for part in parts)
        and _CONTROL.search(path) is None
    )


def _is_line(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _finding(data: Any) -> Finding:
    _require(isinstance(data, dict), "each finding must be an object")
    keys = set(data)
    _require(
        set(FINDING_KEYS) - OPTIONAL_FINDING_KEYS <= keys <= set(FINDING_KEYS),
        f"each finding must have the keys {FINDING_KEYS}, of which {sorted(OPTIONAL_FINDING_KEYS)} may be omitted",
    )
    severity, path, line, title, detail = (data[key] for key in ("severity", "file", "line", "title", "detail"))
    end_line = data.get("end_line")
    _require(isinstance(severity, str) and severity in SEVERITIES, f"severity must be one of {SEVERITIES}")
    _require(isinstance(path, str) and _valid_path(path), "file must be a repository-relative path")
    _require(_is_line(line), "line must be a positive integer")
    _require(end_line is None or (_is_line(end_line) and end_line >= line), "end_line must be an integer >= line")
    _require(isinstance(title, str) and title.strip() != "", "title must be a non-empty string")
    _require(isinstance(detail, str), "detail must be a string")
    span_end = end_line if end_line is not None and end_line > line else None
    safe_path = _COMMENT_CLOSE.sub("--&gt;", _COMMENT_OPEN.sub("&lt;!--", path))
    return Finding(severity, safe_path, line, sanitize(title, MAX_TITLE), sanitize(detail, MAX_DETAIL), span_end)


def _prior(data: Any) -> PriorDefect:
    _require(
        isinstance(data, dict) and set(data) == set(PRIOR_KEYS), f"each prior defect must have the keys {PRIOR_KEYS}"
    )
    _require(isinstance(data["id"], str) and re.fullmatch(r"[A-Za-z0-9-]{1,32}", data["id"]) is not None, "bad id")
    _require(data["status"] in PRIOR_STATUSES, f"prior defect status must be one of {PRIOR_STATUSES}")
    _require(isinstance(data["evidence"], str), "prior defect evidence must be a string")
    return PriorDefect(data["id"], data["status"], sanitize(data["evidence"], MAX_EVIDENCE))


def validate(data: Any, *, trusted: bool = False) -> Verdict:
    _require(isinstance(data, dict), "the verdict must be a JSON object")
    _require(trusted or "defect_count" not in data, "defect_count is set by the oracle, not the reviewer")
    keys = set(data) - {"defect_count"}
    _require(keys == set(VERDICT_KEYS), f"the verdict must have exactly the keys {VERDICT_KEYS}")
    _require(data["status"] in STATUSES, f"status must be one of {STATUSES}")
    _require(data["decision"] in DECISIONS, f"decision must be one of {DECISIONS}")
    _require(isinstance(data["incomplete_reason"], str), "incomplete_reason must be a string")
    _require(isinstance(data["summary"], str), "summary must be a string")
    examined = data["files_examined"]
    _require(isinstance(examined, list) and all(isinstance(item, str) for item in examined), "files_examined bad")
    _require(isinstance(data["defects"], list), "defects must be a list")
    _require(isinstance(data["prior_defects"], list), "prior_defects must be a list")
    complete = data["status"] == COMPLETE
    _require(complete != (data["decision"] == NO_DECISION), "a complete review decides, and only it does")
    count = data.get("defect_count", len(data["defects"]))
    _require(isinstance(count, int) and not isinstance(count, bool) and count >= 0, "defect_count must be >= 0")
    _require(count >= len(data["defects"]), "defect_count is below the listed defects")
    defects = tuple(_finding(item) for item in data["defects"][:MAX_LISTED])
    priors = tuple(_prior(item) for item in data["prior_defects"][:MAX_LISTED])
    _require(len({prior.id for prior in priors}) == len(priors), "a prior defect id repeats")
    return Verdict(
        status=data["status"],
        incomplete_reason=sanitize(data["incomplete_reason"], MAX_REASON),
        decision=data["decision"],
        summary=sanitize(data["summary"], MAX_SUMMARY),
        files_examined=tuple(item for item in examined[:MAX_EXAMINED] if len(item) <= MAX_PATH * 4),
        defects=defects,
        prior_defects=priors,
        defect_count=count,
    )


def _strip_fence(text: str) -> str:
    stripped = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*\n(.*)\n```", stripped, re.DOTALL)
    return match.group(1) if match else stripped


def _stream_result(text: str) -> Any:
    for line in reversed(text.splitlines()):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            return event
    return None


def _repeated_value(text: str) -> Any:
    decoder = json.JSONDecoder()
    values: list[Any] = []
    index = 0
    while index < len(text):
        try:
            value, index = decoder.raw_decode(text, index)
        except json.JSONDecodeError as exc:
            raise VerdictError("the reviewer output is not JSON: Extra data") from exc
        values.append(value)
        while index < len(text) and text[index].isspace():
            index += 1
    distinct = len({json.dumps(value, sort_keys=True) for value in values})
    _require(distinct == 1, f"the reviewer output is not JSON: Extra data ({distinct} different values)")
    return values[0]


def parse_output(harness: str, text: str) -> Any:
    stripped = _strip_fence(text)
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as exc:
        payload = _stream_result(text) if harness == "claude" else None
        if payload is None and harness in TEXT_HARNESSES and exc.msg == "Extra data":
            payload = _repeated_value(stripped)
        if payload is None:
            raise VerdictError(f"the reviewer output is not JSON: {exc.msg}") from exc
    if harness == "claude":
        _require(isinstance(payload, dict), "the claude result envelope must be an object")
        _require(payload.get("is_error") is not True, "the claude result envelope reports an error")
        if "structured_output" in payload:
            return payload["structured_output"]
        result = payload.get("result")
        _require(isinstance(result, str), "the claude result envelope has no result")
        return parse_output("text", result)
    if harness == "agy" and isinstance(payload, dict) and {"status", "response"} <= set(payload):
        _require(payload["status"] == "SUCCESS", "the agy result envelope reports an error")
        response = payload["response"]
        _require(isinstance(response, (str, dict)) and bool(response), "the agy result envelope has no response")
        return response if isinstance(response, dict) else parse_output("text", response)
    return payload


@dataclass(frozen=True)
class Placement:
    inline: tuple[Finding, ...]
    summary: tuple[Finding, ...]


def place(findings: tuple[Finding, ...], commentable: Mapping[str, frozenset[int]]) -> Placement:
    inline = tuple(finding for finding in findings if finding.line in commentable.get(finding.file, frozenset()))
    summary = tuple(finding for finding in findings if finding not in inline)
    return Placement(inline, summary)


def comment_span(finding: Finding, commentable: Mapping[str, frozenset[int]]) -> tuple[int, int] | None:
    if finding.end_line is None:
        return None
    lines = commentable.get(finding.file, frozenset())
    if all(number in lines for number in range(finding.line, finding.end_line + 1)):
        return finding.line, finding.end_line
    return None
