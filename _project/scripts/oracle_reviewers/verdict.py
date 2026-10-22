from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from itertools import islice
from typing import Any

from . import credentials
from .credentials import REDACTED
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
RESPONSE_PLACEHOLDERS = {
    "status": "<status>",
    "incomplete_reason": "<incomplete_reason>",
    "decision": "<decision>",
    "summary": "<summary>",
    "file": "<file>",
    "files_examined": "<examined_file>",
    "severity": "<severity>",
    "title": "<title>",
    "detail": "<detail>",
    "prior_status": "<prior_status>",
    "evidence": "<evidence>",
}
FINDING_METADATA = frozenset({"line_note", "end_line_note", "detail_extra"})
_KEY_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,39}")
_PRIOR_ID = re.compile(r"[A-Za-z0-9-]{1,32}")

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
LINK_REMOVED = "[link removed]"
_SANITIZE_PASSES = 32


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


def sanitize(text: str, limit: int, extra: Iterable[str] = ()) -> str:
    known = tuple(extra)
    cleaned = _CONTROL.sub("", credentials.redact(text, known))
    for pattern in SECRET_PATTERNS:
        cleaned = pattern.sub(REDACTED, cleaned)
    cleaned = _MARKDOWN_LINK.sub(lambda match: match.group(1), cleaned)
    cleaned = _URL.sub(LINK_REMOVED, cleaned)
    cleaned = _MENTION.sub("", cleaned)
    cleaned = _COMMENT_OPEN.sub("&lt;!--", cleaned)
    cleaned = _COMMENT_CLOSE.sub("--&gt;", cleaned)
    cleaned = credentials.redact(cleaned, known)
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"


def _structural_strings(data: Any) -> Iterator[str]:
    if isinstance(data, dict):
        for key, value in data.items():
            yield str(key)
            yield from _structural_strings(value)
    elif isinstance(data, list):
        for item in data:
            yield from _structural_strings(item)


def _reject_structural_credentials(data: dict[str, Any]) -> None:
    strings = list(_structural_strings(data))
    examined = data.get("files_examined")
    strings += [item for item in examined if isinstance(item, str)] if isinstance(examined, list) else []
    for field, key in (("defects", "file"), ("prior_defects", "id")):
        items = data.get(field)
        for item in items if isinstance(items, list) else ():
            if isinstance(item, dict) and isinstance(item.get(key), str):
                strings.append(item[key])
    _require(
        not any(credentials.contains(text) for text in strings),
        "the verdict carries a credential in a structural field",
    )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise VerdictError(message)


def _key_labels(keys: Iterable[Any]) -> str:
    safe = []
    unsafe = 0
    for key in keys:
        if isinstance(key, str) and _KEY_NAME.fullmatch(key) and sanitize(key, 40) == key:
            safe.append(key)
        else:
            unsafe += 1
    labels = repr(sorted(safe)[:5])
    return f"{labels}, {unsafe} unsafe" if unsafe else labels


def _settled(text: str) -> str:
    for _ in range(_SANITIZE_PASSES):
        cleaned = sanitize(text, 2 * len(text) + 64)
        if cleaned == text:
            return text
        text = cleaned
    raise VerdictError("the response text does not settle under sanitization")


def _reject_placeholders(data: Any) -> None:
    pending = [data]
    placeholders = frozenset(RESPONSE_PLACEHOLDERS.values())
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            _require(_settled(item) not in placeholders, "the response template contains an unfilled placeholder")
        elif isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)


def normalize_reviewer(data: Any) -> tuple[Any, tuple[str, ...]]:
    if not isinstance(data, dict):
        return data, ()
    _reject_placeholders(data)
    normalized = dict(data)
    rewrites = []
    if "incomplete_reason" not in normalized:
        normalized["incomplete_reason"] = ""
        rewrites.append("default_incomplete_reason")
    if "summary" not in normalized and normalized.get("decision") != DO_NOT_SHIP:
        normalized["summary"] = ""
        rewrites.append("default_summary")
    priors = normalized.get("prior_defects")
    if isinstance(priors, dict):
        entries = []
        seen: set[str] = set()
        for key, value in priors.items():
            identifier = key.removeprefix("#") if isinstance(key, str) else key
            _require(
                isinstance(identifier, str) and _PRIOR_ID.fullmatch(identifier) is not None,
                "dictionary prior defect keys must be valid IDs",
            )
            _require(identifier not in seen, "a dictionary prior defect ID repeats after normalization")
            seen.add(identifier)
            _require(isinstance(value, dict), "dictionary prior defect values must be objects")
            embedded = value.get("id", key)
            embedded = embedded.removeprefix("#") if isinstance(embedded, str) else embedded
            _require(embedded == identifier, "dictionary prior defect key and embedded ID disagree")
            entries.append({**value, "id": identifier})
        normalized["prior_defects"] = entries
        rewrites.append("prior_dictionary")
    defects_entries = normalized.get("defects")
    if isinstance(defects_entries, list):
        cleaned = []
        removed = set()
        for entry in defects_entries:
            if isinstance(entry, dict):
                removed.update(set(entry) & FINDING_METADATA)
                cleaned.append({key: value for key, value in entry.items() if key not in FINDING_METADATA})
            else:
                cleaned.append(entry)
        if removed:
            normalized["defects"] = cleaned
            rewrites.append("defects_metadata")
    return normalized, tuple(rewrites)


def shape_fingerprint(data: Any, credentials: Iterable[str] = ()) -> str:
    secrets = tuple(value for value in credentials if value)

    def shape(value: Any, depth: int) -> Any:
        if isinstance(value, dict):
            if depth >= 3:
                return {"object_keys": len(value)}
            result = {}
            unsafe = 0
            for key, child in islice(value.items(), 20):
                if (
                    isinstance(key, str)
                    and _KEY_NAME.fullmatch(key)
                    and not any(secret in key for secret in secrets)
                    and sanitize(key, 40) == key
                ):
                    result[key] = shape(child, depth + 1)
                else:
                    unsafe += 1
            if unsafe:
                result["<unsafe keys>"] = unsafe
            if len(value) > 20:
                result["<omitted keys>"] = len(value) - 20
            return result
        if isinstance(value, list):
            return {"list_items": len(value)}
        return type(value).__name__

    text = json.dumps(shape(data, 0), ensure_ascii=True, separators=(",", ":"))
    return text if len(text) <= 512 else text[:509] + "..."


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
    required = set(FINDING_KEYS) - OPTIONAL_FINDING_KEYS
    unexpected = _key_labels(keys - set(FINDING_KEYS))
    _require(
        required <= keys <= set(FINDING_KEYS),
        f"each finding must have the keys {FINDING_KEYS}, of which {sorted(OPTIONAL_FINDING_KEYS)} may be omitted;"
        f" missing {sorted(required - keys)}, unexpected {unexpected}",
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
    identifier = data["id"].removeprefix("#") if isinstance(data["id"], str) else data["id"]
    _require(
        isinstance(identifier, str) and _PRIOR_ID.fullmatch(identifier) is not None,
        "prior defect id must be 1 to 32 letters, digits or hyphens",
    )
    _require(data["status"] in PRIOR_STATUSES, f"prior defect status must be one of {PRIOR_STATUSES}")
    _require(isinstance(data["evidence"], str), "prior defect evidence must be a string")
    return PriorDefect(identifier, data["status"], sanitize(data["evidence"], MAX_EVIDENCE))


def validate(data: Any, *, trusted: bool = False) -> Verdict:
    _require(isinstance(data, dict), "the verdict must be a JSON object")
    _reject_structural_credentials(data)
    _reject_placeholders(data)
    _require(trusted or "defect_count" not in data, "defect_count is set by the oracle, not the reviewer")
    keys = set(data) - {"defect_count"}
    _require(
        keys == set(VERDICT_KEYS),
        f"the verdict must have exactly the keys {VERDICT_KEYS};"
        f" missing {sorted(set(VERDICT_KEYS) - keys)}, unexpected {_key_labels(keys - set(VERDICT_KEYS))}",
    )
    _require(data["status"] in STATUSES, f"status must be one of {STATUSES}")
    _require(data["decision"] in DECISIONS, f"decision must be one of {DECISIONS}")
    complete = data["status"] == COMPLETE
    reason = "" if complete and data["incomplete_reason"] is None else data["incomplete_reason"]
    _require(isinstance(reason, str), "incomplete_reason must be a string")
    _require(isinstance(data["summary"], str), "summary must be a string")
    examined = data["files_examined"]
    _require(isinstance(examined, list) and all(isinstance(item, str) for item in examined), "files_examined bad")
    _require(isinstance(data["defects"], list), "defects must be a list")
    _require(isinstance(data["prior_defects"], list), "prior_defects must be a list")
    _require(complete != (data["decision"] == NO_DECISION), "a complete review decides, and only it does")
    count = data.get("defect_count", len(data["defects"]))
    _require(isinstance(count, int) and not isinstance(count, bool) and count >= 0, "defect_count must be >= 0")
    _require(count >= len(data["defects"]), "defect_count is below the listed defects")
    defects = tuple(_finding(item) for item in data["defects"][:MAX_LISTED])
    priors = tuple(_prior(item) for item in data["prior_defects"][:MAX_LISTED])
    _require(len({prior.id for prior in priors}) == len(priors), "a prior defect id repeats")
    return Verdict(
        status=data["status"],
        incomplete_reason=sanitize(reason, MAX_REASON),
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


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        _require(key not in result, "duplicate JSON object key in reviewer output")
        result[key] = value
    return result


def _stream_result(text: str) -> Any:
    for line in reversed(text.splitlines()):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            return event
    return None


def extract_omp_text(stdout: str) -> str:
    final = ""
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "message_end":
            continue
        message = event.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        final = "".join(
            block["text"]
            for block in content
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
        )
    return final


def parse_output(harness: str, text: str) -> Any:
    if harness == "omp":
        return parse_output("text", extract_omp_text(text))
    stripped = _strip_fence(text)
    try:
        payload = json.loads(stripped, object_pairs_hook=_unique_object)
    except json.JSONDecodeError as exc:
        payload = _stream_result(text) if harness == "claude" else None
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
