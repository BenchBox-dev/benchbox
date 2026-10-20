from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

from _project.scripts.oracle_reviewers import absence
from _project.scripts.oracle_reviewers.absence import classify, parse_reset

pytestmark = [pytest.mark.unit, pytest.mark.fast]

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def _classify(harness: str, exit_code: int | None, stdout: str = "", stderr: str = "", timed_out: bool = False):
    return classify(harness, exit_code, stdout, stderr, timed_out=timed_out, now=NOW)


def test_rate_limit_error_is_quota_with_a_reset_time() -> None:
    stderr = "rate_limit_error: would exceed your account's rate limit. Resets in 4h12m30s."
    result = _classify("omp", 1, stderr=stderr)
    assert result.kind == absence.QUOTA
    assert result.reset_at == NOW + timedelta(hours=4, minutes=12, seconds=30)


def test_quota_without_a_reset_time_has_none() -> None:
    result = _classify("omp", 1, stderr="rate_limit_error: quota exceeded")
    assert result.kind == absence.QUOTA
    assert result.reset_at is None


@pytest.mark.parametrize(
    "stderr",
    [
        "rate_limit_error: slow down",
        "Error: request would exceed your account's rate limit, try later",
        "ERROR: 429 Too Many Requests",
        "failed with retry-after-ms=1500, backing off",
    ],
)
def test_quota_patterns(stderr: str) -> None:
    assert _classify("omp", 1, stderr=stderr).kind == absence.QUOTA


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Resets in 1d2h3m4s", timedelta(days=1, hours=2, minutes=3, seconds=4)),
        ("resets in 90s", timedelta(seconds=90)),
        ("Resets in 2h 5m", timedelta(hours=2, minutes=5)),
    ],
)
def test_reset_durations(text: str, expected: timedelta) -> None:
    assert parse_reset(text, NOW) == NOW + expected


def test_no_reset_phrase_means_no_reset() -> None:
    assert parse_reset("try again later", NOW) is None


def test_exit_zero_without_output_is_empty() -> None:
    result = _classify("omp", 0, stdout="", stderr="")
    assert result.kind == absence.EMPTY
    assert result.absent


def test_timeout_wins() -> None:
    assert _classify("omp", None, timed_out=True).kind == absence.TIMEOUT
    assert _classify("omp", 1, stderr="rate_limit_error", timed_out=True).kind == absence.TIMEOUT


def test_uncalibrated_messages_are_errors_never_passes() -> None:
    for text in ("RESOURCE_EXHAUSTED (code 429)", "session limit reached", "something broke"):
        result = _classify("omp", 1, stderr=text)
        assert result.kind == absence.ERROR
        assert result.absent


def test_unstartable_reviewer_is_an_error() -> None:
    assert _classify("omp", None).kind == absence.ERROR


def test_nonzero_exit_without_a_signal_is_a_numbered_error() -> None:
    result = _classify("omp", 2, stderr="boom")
    assert result.kind == absence.ERROR
    assert result.detail == "exit code 2"


def test_output_with_exit_zero_is_ok() -> None:
    result = _classify("omp", 0, stdout='{"summary": "", "findings": []}')
    assert result.kind == absence.OK
    assert not result.absent


def test_calibration_tables_cover_every_harness() -> None:
    for table in (
        absence.CALIBRATED_QUOTA_PATTERNS,
        absence.CALIBRATED_AUTH_PATTERNS,
        absence.CALIBRATED_EMPTY_PATTERNS,
    ):
        assert set(table) == {"omp"}
    assert absence.CALIBRATED_EMPTY_PATTERNS["omp"] == ()


@pytest.mark.parametrize(
    "stderr",
    [
        "request failed with status 401: bad credentials",
        "ERROR: Unauthorized: missing token",
        "invalid_api_key: key was revoked",
        "invalid api key provided",
        "error: No API key found for anthropic.",
        'oauth refresh failed: 400 {"error": "invalid_grant"}',
    ],
)
def test_auth_patterns(stderr: str) -> None:
    result = _classify("omp", 1, stderr=stderr)
    assert result.kind == absence.AUTH


def test_unknown_harness_has_no_calibrated_signals() -> None:
    assert _classify("nope", 1, stderr="rate_limit_error").kind == absence.ERROR
    assert _classify("nope", 1, stderr="401 Unauthorized").kind == absence.ERROR


def test_a_review_quoting_the_error_text_in_its_body_is_not_a_quota_absence() -> None:
    body = '{"summary": "the log says rate_limit_error happened", "findings": []}'
    assert _classify("omp", 0, stdout=body).kind == absence.OK


TYPESAFE_KEY = "ts_" + "Q7x9Lk2Pz8Rw4Tn6Yb1Vc3Hd5Jf0Gs7Ma2Ne"
BROKER_TOKEN = "omp-broker-" + "Zx8Cv7Bn6Mq5Wr4Ty3Ui2Op1As0Df9Gh8Jk7"
LONG_TOKEN = "Kj7Hg6Fd5Sa4Lp3Oi2Uy1Tr0Ew9Qz8Xc7Vb6"


@pytest.mark.parametrize("secret", [TYPESAFE_KEY, BROKER_TOKEN, LONG_TOKEN])
def test_excerpt_redacts_credentials(secret: str) -> None:
    text = absence.excerpt("", f"error: request with {secret} was rejected\n")
    assert secret not in text
    assert "[redacted]" in text


def test_excerpt_redacts_credential_env_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", TYPESAFE_KEY)
    monkeypatch.setenv("OMP_AUTH_BROKER_TOKEN", BROKER_TOKEN)
    redacted_typesafe = absence.redact_detail(f"401 bad key {TYPESAFE_KEY}")
    redacted_broker = absence.redact_detail(f"401 bad key {BROKER_TOKEN}")
    assert TYPESAFE_KEY not in redacted_typesafe
    assert BROKER_TOKEN not in redacted_broker
    assert os.environ["TYPESAFE_API_KEY"] not in redacted_typesafe


def test_excerpt_redacts_the_exact_credential_values() -> None:
    short_but_secret = "abc12345xyz"
    text = absence.excerpt("", f"bad key {short_but_secret}", [short_but_secret])
    assert short_but_secret not in text


def test_excerpt_prefers_the_error_line_over_a_trailing_stack_frame() -> None:
    stderr = "error: No API key found for anthropic.\n      at async #mo (/$bunfs/root/chunks/cli.js:46290:26)\n"
    assert absence.excerpt("", stderr) == "error: No API key found for anthropic."


def test_excerpt_is_one_capped_line() -> None:
    stderr = "first line with detail\n" + "x " * 1000 + "\n\n"
    text = absence.excerpt("raw review output", stderr)
    assert "first line" not in text
    assert len(text) <= absence.EXCERPT_LIMIT
    assert absence.excerpt("only stdout line\n", "") == "only stdout line"
    assert absence.excerpt("", "") == ""


def test_calibrated_text_after_a_successful_exit_is_never_an_absence() -> None:
    text = "rate_limit_error: slow down"
    assert _classify("omp", 0, stdout=text, stderr=text).kind == absence.OK


@pytest.mark.parametrize("exit_code", [None, 0])
def test_calibrated_text_without_a_failing_exit_is_not_quota_or_auth(exit_code: int | None) -> None:
    result = _classify("omp", exit_code, stderr="rate_limit_error: slow down")
    assert result.kind not in (absence.QUOTA, absence.AUTH)
