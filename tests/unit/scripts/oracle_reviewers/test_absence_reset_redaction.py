from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from _project.scripts.oracle_reviewers import absence
from _project.scripts.oracle_reviewers.absence import classify, parse_reset, redact_detail

pytestmark = [pytest.mark.unit, pytest.mark.fast]

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def test_wall_clock_reset_parses_to_zone_time() -> None:
    text = "rate_limit_error: slow down \u00b7 resets 7:50pm (America/New_York)"
    result = parse_reset(text, NOW)
    assert result == datetime(2026, 10, 5, 19, 50, tzinfo=ZoneInfo("America/New_York"))


def test_wall_clock_reset_rolls_to_next_day() -> None:
    late = datetime(2026, 10, 5, 23, 55, tzinfo=UTC)
    text = "resets 7:50pm (America/New_York)"
    result = parse_reset(text, late)
    assert result == datetime(2026, 10, 6, 19, 50, tzinfo=ZoneInfo("America/New_York"))


def test_try_again_reset_parses() -> None:
    text = "rate limit hit. Try again at Oct 9th, 2026 6:08 PM."
    assert parse_reset(text, NOW) == datetime(2026, 10, 9, 18, 8, tzinfo=UTC)


def test_unknown_zone_and_month_yield_no_reset() -> None:
    assert parse_reset("resets 7:50pm (Mars/Olympus)", NOW) is None
    assert parse_reset("try again at Foo 9th, 2026 6:08 PM", NOW) is None


def test_quota_absence_carries_parsed_reset() -> None:
    result = classify(
        "omp",
        1,
        stdout="",
        stderr="rate_limit_error: slow down. Resets in 4h12m30s.",
        timed_out=False,
        now=NOW,
    )
    assert result.kind == absence.QUOTA
    assert result.reset_at == NOW + timedelta(hours=4, minutes=12, seconds=30)
    assert timedelta(0) < (result.reset_at - NOW) < timedelta(days=1)


def test_quota_absence_with_retry_after_ms_has_no_reset() -> None:
    result = classify(
        "omp",
        1,
        stdout="",
        stderr="rate_limit_error with retry-after-ms=1500",
        timed_out=False,
        now=NOW,
    )
    assert result.kind == absence.QUOTA
    assert result.reset_at is None


def test_auth_detail_redacts_long_token() -> None:
    token = "sk-ant-oat01-Kj7Hg6Fd5Sa4Lp3Oi2Uy1Tr0Ew9Qz8Xc7Vb6"
    detail = f"Failed to authenticate. API Error: 401 OAuth access token {token} is invalid."
    redacted = redact_detail(detail)
    assert token not in redacted
    assert "[redacted]" in redacted


def test_redact_detail_redacts_typesafe_and_gateway_values(monkeypatch: pytest.MonkeyPatch) -> None:
    typesafe = "ts_Q7x9Lk2Pz8Rw4Tn6Yb1Vc3Hd5Jf0Gs7Ma2Ne"
    gateway = "omp-gateway-Zx8Cv7Bn6Mq5Wr4Ty3Ui2Op1As0Df9Gh8Jk7"
    monkeypatch.setenv("TYPESAFE_API_KEY", typesafe)
    monkeypatch.setenv("OMP_GATEWAY_TOKEN", gateway)
    for secret in (typesafe, gateway):
        redacted = redact_detail(f"request failed with 401 for key {secret}")
        assert secret not in redacted
        assert "[redacted]" in redacted


def test_classify_redacts_auth_detail() -> None:
    token = "sk-ant-oat01-Kj7Hg6Fd5Sa4Lp3Oi2Uy1Tr0Ew9Qz8Xc7Vb6"
    stderr = f"request failed with status 401: token {token} rejected as invalid_api_key"
    result = classify("omp", 1, stdout="", stderr=stderr, timed_out=False, now=NOW)
    assert result.kind == absence.AUTH
    assert token not in result.detail
    assert "[redacted]" in result.detail


def test_classify_never_leaks_credentials_into_quota_detail() -> None:
    token = "ts_Q7x9Lk2Pz8Rw4Tn6Yb1Vc3Hd5Jf0Gs7Ma2Ne"
    stderr = f"rate_limit_error for key {token}: 429 Too Many Requests"
    result = classify("omp", 1, stdout="", stderr=stderr, timed_out=False, now=NOW)
    assert result.kind == absence.QUOTA
    assert token not in result.detail


KOLKATA = ZoneInfo("Asia/Kolkata")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("resets 12:30pm (Asia/Kolkata)", datetime(2026, 10, 6, 12, 30, tzinfo=KOLKATA)),
        ("resets 12:15am (Asia/Kolkata)", datetime(2026, 10, 6, 0, 15, tzinfo=KOLKATA)),
        ("resets 7:05am (Asia/Kolkata)", datetime(2026, 10, 6, 7, 5, tzinfo=KOLKATA)),
        ("resets 7:50pm (Asia/Kolkata)", datetime(2026, 10, 5, 19, 50, tzinfo=KOLKATA)),
        ("Try again at Oct 9th, 2026 12:08 PM.", datetime(2026, 10, 9, 12, 8, tzinfo=UTC)),
        ("Try again at Oct 9th, 2026 12:08 AM.", datetime(2026, 10, 9, 0, 8, tzinfo=UTC)),
        ("Try again at Oct 9th, 2026 6:08 AM.", datetime(2026, 10, 9, 6, 8, tzinfo=UTC)),
    ],
)
def test_wall_clock_resets_read_noon_midnight_and_morning_in_the_named_zone(text: str, expected: datetime) -> None:
    result = parse_reset(text, datetime(2026, 10, 5, 12, 0, 30, 500000, tzinfo=UTC))
    assert result == expected and (result.second, result.microsecond) == (0, 0)


def test_a_wall_clock_reset_at_the_current_minute_is_tomorrow() -> None:
    now = datetime(2026, 10, 5, 14, 20, tzinfo=UTC)
    assert parse_reset("resets 7:50pm (Asia/Kolkata)", now) == datetime(2026, 10, 6, 19, 50, tzinfo=KOLKATA)


def test_redact_detail_hides_extra_values_and_clips_only_beyond_its_limit() -> None:
    assert "pw-Zq8Lm3Tr" not in redact_detail("rejected pw-Zq8Lm3Tr here", ["pw-Zq8Lm3Tr"])
    exact = "a b " * (absence.EXCERPT_LIMIT // 4)
    assert redact_detail(exact) == exact
    clipped = redact_detail(exact + "c")
    assert len(clipped) == absence.EXCERPT_LIMIT and clipped == exact[:-1] + "\u2026"


def test_an_unparsed_answer_is_kept_whole_up_to_its_limit() -> None:
    assert absence.unparsed("not json") == "not json"
    exact = "a b " * (absence.UNPARSED_LIMIT // 4)
    assert absence.unparsed(exact) == exact
    assert absence.unparsed(exact + "c") == "\u2026" + (exact + "c")[-absence.UNPARSED_LIMIT :]
