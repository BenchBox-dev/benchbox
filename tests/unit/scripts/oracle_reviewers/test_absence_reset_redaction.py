from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from _project.scripts.oracle_reviewers import absence
from _project.scripts.oracle_reviewers.absence import classify, parse_reset, redact_detail

pytestmark = [pytest.mark.unit, pytest.mark.fast]

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def test_claude_wall_clock_reset_parses_to_zone_time() -> None:
    text = "You've hit your session limit \u00b7 resets 7:50pm (America/New_York)"
    result = parse_reset(text, NOW)
    assert result == datetime(2026, 10, 5, 19, 50, tzinfo=ZoneInfo("America/New_York"))


def test_claude_wall_clock_reset_rolls_to_next_day() -> None:
    late = datetime(2026, 10, 5, 23, 55, tzinfo=UTC)
    text = "resets 7:50pm (America/New_York)"
    result = parse_reset(text, late)
    assert result == datetime(2026, 10, 6, 19, 50, tzinfo=ZoneInfo("America/New_York"))


def test_codex_try_again_reset_parses() -> None:
    text = "ERROR: You\u2019ve hit your usage limit. Try again at Oct 9th, 2026 6:08 PM."
    assert parse_reset(text, NOW) == datetime(2026, 10, 9, 18, 8, tzinfo=UTC)


def test_unknown_zone_and_month_yield_no_reset() -> None:
    assert parse_reset("resets 7:50pm (Mars/Olympus)", NOW) is None
    assert parse_reset("try again at Foo 9th, 2026 6:08 PM", NOW) is None


def test_quota_absence_carries_parsed_reset() -> None:
    result = classify(
        "claude",
        1,
        stdout='{"result": "You\'ve hit your session limit \u00b7 resets 7:50pm (America/New_York)"}',
        stderr="",
        timed_out=False,
        now=NOW,
    )
    assert result.kind == absence.QUOTA
    assert result.reset_at == datetime(2026, 10, 5, 19, 50, tzinfo=ZoneInfo("America/New_York"))
    assert timedelta(0) < (result.reset_at - NOW) < timedelta(days=1)


def test_auth_detail_redacts_long_token() -> None:
    token = "sk-ant-oat01-Kj7Hg6Fd5Sa4Lp3Oi2Uy1Tr0Ew9Qz8Xc7Vb6"
    detail = f"Failed to authenticate. API Error: 401 OAuth access token {token} is invalid."
    redacted = redact_detail(detail)
    assert token not in redacted
    assert "[redacted]" in redacted


def test_classify_redacts_auth_detail() -> None:
    token = "sk-ant-oat01-Kj7Hg6Fd5Sa4Lp3Oi2Uy1Tr0Ew9Qz8Xc7Vb6"
    envelope = (
        '{"is_error":true,"result":"Failed to authenticate. API Error: 401 token '
        + token
        + ' rejected.","type":"result"}'
    )
    result = classify("claude", 1, stdout=envelope, stderr="", timed_out=False, now=NOW)
    assert result.kind == absence.AUTH
    assert token not in result.detail
    assert "[redacted]" in result.detail
