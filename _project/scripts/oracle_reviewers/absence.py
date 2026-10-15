from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .verdict import REDACTED, sanitize

OK = "ok"
QUOTA = "quota"
AUTH = "auth"
TIMEOUT = "timeout"
INVALID = "invalid"
EMPTY = "empty"
ERROR = "error"
INCOMPLETE = "incomplete"
KINDS = (OK, QUOTA, AUTH, TIMEOUT, INVALID, EMPTY, ERROR, INCOMPLETE)

CALIBRATED_QUOTA_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "agy": (re.compile(r"RESOURCE_EXHAUSTED \(code 429\)"),),
    "claude": (re.compile(r"You[\u2019']ve hit your session limit"),),
    "codex": (re.compile(r"^ERROR: You[\u2019']ve hit your usage limit", re.MULTILINE),),
    "muse": (),
}
CALIBRATED_AUTH_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "agy": (re.compile(r"API key not valid\. Please pass a valid API key\."),),
    "claude": (re.compile(r"Failed to authenticate\. API Error: 401[^\"\n]*"),),
    "codex": (re.compile(r"^ERROR: unexpected status 401 Unauthorized", re.MULTILINE),),
    "muse": (re.compile(r"authentication failed: your API key from META_API_KEY was rejected"),),
}
CALIBRATED_EMPTY_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "agy": (re.compile(r"no output (?:was )?produced.*auto-denied", re.IGNORECASE | re.DOTALL),),
    "claude": (),
    "codex": (),
    "muse": (),
}

_RESET = re.compile(r"Resets in\s+(?P<duration>(?:\d+\s*[dhms]\s*)+)", re.IGNORECASE)
_DURATION_PART = re.compile(r"(\d+)\s*([dhms])", re.IGNORECASE)
_UNITS = {"d": "days", "h": "hours", "m": "minutes", "s": "seconds"}
_WALL_RESET = re.compile(r"resets\s+(\d{1,2}):(\d{2})\s*(am|pm)\s*\(([^)]+)\)", re.IGNORECASE)
_CODEX_RESET = re.compile(
    r"try again at\s+([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?,\s+(\d{4})\s+(\d{1,2}):(\d{2})\s*(AM|PM)",
    re.IGNORECASE,
)
_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_LONG_TOKEN = re.compile(r"[A-Za-z0-9_\-]{32,}")
EXCERPT_LIMIT = 300
REQUIRED_CREDENTIAL = {"agy": "GEMINI_API_KEY"}
CREDENTIAL_ENV = ("CLAUDE_CODE_OAUTH_TOKEN", "OPENAI_API_KEY", "CODEX_API_KEY", "META_API_KEY", "GEMINI_API_KEY")


@dataclass(frozen=True)
class Absence:
    kind: str
    detail: str = ""
    reset_at: datetime | None = None

    @property
    def absent(self) -> bool:
        return self.kind != OK


def parse_reset(text: str, now: datetime) -> datetime | None:
    match = _RESET.search(text)
    if match is not None:
        delta = timedelta()
        for amount, unit in _DURATION_PART.findall(match.group("duration")):
            delta += timedelta(**{_UNITS[unit.lower()]: int(amount)})
        return now + delta if delta else None
    wall = _WALL_RESET.search(text)
    if wall is not None:
        try:
            zone = ZoneInfo(wall.group(4))
        except (ZoneInfoNotFoundError, ValueError):
            return None
        hour = int(wall.group(1)) % 12 + (12 if wall.group(3).lower() == "pm" else 0)
        local = now.astimezone(zone).replace(hour=hour, minute=int(wall.group(2)), second=0, microsecond=0)
        if local <= now.astimezone(zone):
            local += timedelta(days=1)
        return local
    codex = _CODEX_RESET.search(text)
    if codex is not None:
        month = _MONTHS.get(codex.group(1).lower())
        if month is None:
            return None
        hour = int(codex.group(4)) % 12 + (12 if codex.group(6).upper() == "PM" else 0)
        return datetime(int(codex.group(3)), month, int(codex.group(2)), hour, int(codex.group(5)), tzinfo=now.tzinfo)
    return None


def redact_detail(detail: str, credentials: Iterable[str] = ()) -> str:
    seen: list[str] = []
    for credential in (*credentials, *(os.environ.get(name, "") for name in CREDENTIAL_ENV)):
        if len(credential) >= 8 and credential not in seen:
            seen.append(credential)
    text = detail
    for credential in seen:
        text = text.replace(credential, REDACTED)
    text = _LONG_TOKEN.sub(REDACTED, sanitize(text, EXCERPT_LIMIT * 4))
    return text if len(text) <= EXCERPT_LIMIT else text[: EXCERPT_LIMIT - 1] + "\u2026"


def _first(patterns: tuple[re.Pattern[str], ...], text: str) -> re.Match[str] | None:
    return next((match for pattern in patterns if (match := pattern.search(text)) is not None), None)


def classify(
    harness: str,
    exit_code: int | None,
    stdout: str,
    stderr: str,
    *,
    timed_out: bool,
    now: datetime,
) -> Absence:
    combined = f"{stdout}\n{stderr}"
    if timed_out:
        return Absence(TIMEOUT, "the reviewer did not finish within its timeout")
    if _first(CALIBRATED_EMPTY_PATTERNS.get(harness, ()), combined) is not None:
        return Absence(EMPTY, "the reviewer produced no output because tool use was denied")
    failed = exit_code not in (0, None)
    quota = _first(CALIBRATED_QUOTA_PATTERNS.get(harness, ()), combined) if failed else None
    if quota is not None:
        return Absence(QUOTA, redact_detail(quota.group(0)), parse_reset(combined, now))
    auth = _first(CALIBRATED_AUTH_PATTERNS.get(harness, ()), combined) if failed else None
    if auth is not None:
        return Absence(AUTH, redact_detail(auth.group(0)))
    if exit_code is None:
        return Absence(ERROR, "the reviewer could not be started")
    if exit_code != 0:
        return Absence(ERROR, f"exit code {exit_code}")
    if not stdout.strip():
        return Absence(EMPTY, "exit code 0 with no output")
    return Absence(OK)


def excerpt(stdout: str, stderr: str, credentials: Iterable[str] = ()) -> str:
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    lines = lines or [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        return ""
    text = lines[-1][: EXCERPT_LIMIT * 4]
    for credential in credentials:
        if len(credential) >= 8:
            text = text.replace(credential, REDACTED)
    text = _LONG_TOKEN.sub(REDACTED, sanitize(text, EXCERPT_LIMIT * 4))
    return text if len(text) <= EXCERPT_LIMIT else text[: EXCERPT_LIMIT - 1] + "\u2026"
