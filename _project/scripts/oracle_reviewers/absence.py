from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import credentials
from .verdict import sanitize

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
    "omp": (
        re.compile(r"rate_limit_error"),
        re.compile(r"would exceed your account's rate limit"),
        re.compile(r"429 Too Many Requests"),
        re.compile(r"retry-after-ms=\d+"),
    ),
}
CALIBRATED_AUTH_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "omp": (
        re.compile(r"\b401\b[^\n]{0,80}"),
        re.compile(r"[Uu]nauthorized"),
        re.compile(r"invalid[_\s-]?api[_\s-]?key"),
        re.compile(r"No API key found for [\w-]+"),
        re.compile(r"invalid_grant"),
    ),
}
CALIBRATED_EMPTY_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "omp": (),
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
PUBLISHED_DETAIL_LIMIT = 160
UNPARSED_LIMIT = 2000


@dataclass(frozen=True)
class Absence:
    kind: str
    detail: str = ""
    reset_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "detail", credentials.redact(self.detail))

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


def redact_detail(detail: str, extra: Iterable[str] = (), limit: int = EXCERPT_LIMIT) -> str:
    text = _LONG_TOKEN.sub(credentials.REDACTED, sanitize(detail, EXCERPT_LIMIT * 4, extra))
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def published_detail(detail: str, extra: Iterable[str] = ()) -> str:
    return redact_detail(" ".join(detail.split()), extra, PUBLISHED_DETAIL_LIMIT)


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
    if timed_out:
        return Absence(TIMEOUT, "the reviewer did not finish within its timeout")
    if _first(CALIBRATED_EMPTY_PATTERNS.get(harness, ()), stderr) is not None:
        return Absence(EMPTY, "the reviewer produced no output because tool use was denied")
    failed = exit_code not in (0, None)
    quota = _first(CALIBRATED_QUOTA_PATTERNS.get(harness, ()), stderr) if failed else None
    if quota is not None:
        return Absence(QUOTA, redact_detail(quota.group(0)), parse_reset(stderr, now))
    auth = _first(CALIBRATED_AUTH_PATTERNS.get(harness, ()), stderr) if failed else None
    if auth is not None:
        return Absence(AUTH, redact_detail(auth.group(0)))
    if exit_code is None:
        return Absence(ERROR, "the reviewer could not be started")
    if exit_code != 0:
        return Absence(ERROR, f"exit code {exit_code}")
    if not stdout.strip():
        return Absence(EMPTY, "exit code 0 with no output")
    return Absence(OK)


def _redact(text: str, extra: Iterable[str], limit: int) -> str:
    return _LONG_TOKEN.sub(credentials.REDACTED, sanitize(text, limit, extra))


def excerpt(stdout: str, stderr: str, extra: Iterable[str] = ()) -> str:
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    lines = lines or [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        return ""
    errors = [line for line in lines if line.startswith("error:")]
    text = _redact((errors or lines)[-1], extra, EXCERPT_LIMIT * 4)
    return text if len(text) <= EXCERPT_LIMIT else text[: EXCERPT_LIMIT - 1] + "\u2026"


def unparsed(text: str, extra: Iterable[str] = ()) -> str:
    redacted = _redact(text, extra, 2 * len(text) + 64)
    tail = redacted[-UNPARSED_LIMIT:]
    return ("\u2026" if len(redacted) > UNPARSED_LIMIT else "") + tail
