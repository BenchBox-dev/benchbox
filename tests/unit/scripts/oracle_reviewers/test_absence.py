from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from _project.scripts.oracle_reviewers import absence
from _project.scripts.oracle_reviewers.absence import classify, parse_reset

pytestmark = [pytest.mark.unit, pytest.mark.fast]

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def _classify(harness: str, exit_code: int | None, stdout: str = "", stderr: str = "", timed_out: bool = False):
    return classify(harness, exit_code, stdout, stderr, timed_out=timed_out, now=NOW)


def test_agy_resource_exhausted_is_quota_with_a_reset_time() -> None:
    stderr = "Error: RESOURCE_EXHAUSTED (code 429): quota exceeded. Resets in 4h12m30s."
    result = _classify("agy", 1, stderr=stderr)
    assert result.kind == absence.QUOTA
    assert result.reset_at == NOW + timedelta(hours=4, minutes=12, seconds=30)


def test_agy_quota_without_a_reset_time_has_none() -> None:
    result = _classify("agy", 1, stderr="RESOURCE_EXHAUSTED (code 429)")
    assert result.kind == absence.QUOTA
    assert result.reset_at is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Resets in 45m", timedelta(minutes=45)),
        ("resets in 1d 2h", timedelta(days=1, hours=2)),
        ("Resets in 3h 5m 10s", timedelta(hours=3, minutes=5, seconds=10)),
    ],
)
def test_reset_durations(text: str, expected: timedelta) -> None:
    assert parse_reset(text, NOW) == NOW + expected


def test_no_reset_phrase_means_no_reset() -> None:
    assert parse_reset("try again later", NOW) is None


def test_agy_auto_denied_headless_run_is_empty() -> None:
    message = "No output produced: tool use was auto-denied in --print mode"
    assert _classify("agy", 0, stdout="", stderr=message).kind == absence.EMPTY


def test_exit_zero_without_output_is_empty_for_every_harness() -> None:
    for harness in ("agy", "claude", "codex", "muse"):
        result = _classify(harness, 0, stdout="  \n")
        assert result.kind == absence.EMPTY
        assert result.absent


def test_timeout_wins() -> None:
    assert _classify("claude", None, timed_out=True).kind == absence.TIMEOUT


def test_uncalibrated_quota_messages_are_errors_never_passes() -> None:
    for harness in ("claude", "codex", "muse"):
        result = _classify(
            harness, 1, stderr="429 Too Many Requests: usage limit reached, RESOURCE_EXHAUSTED (code 429)"
        )
        assert result.kind == absence.ERROR
        assert result.absent


def test_unstartable_reviewer_is_an_error() -> None:
    assert _classify("codex", None).kind == absence.ERROR


def test_output_with_exit_zero_is_ok() -> None:
    result = _classify("codex", 0, stdout='{"summary": "", "findings": []}')
    assert result.kind == absence.OK
    assert not result.absent


def test_calibration_tables_cover_every_harness() -> None:
    for table in (
        absence.CALIBRATED_QUOTA_PATTERNS,
        absence.CALIBRATED_AUTH_PATTERNS,
        absence.CALIBRATED_EMPTY_PATTERNS,
    ):
        assert set(table) == {"agy", "claude", "codex", "muse"}
    assert absence.CALIBRATED_QUOTA_PATTERNS["muse"] == ()


def test_agy_rejected_api_key_is_auth() -> None:
    stderr = (
        'AGY_ERROR: {"short_error":"agent executor error: generating and executing: Error 400, '
        'Message: API key not valid. Please pass a valid API key., Status: INVALID_ARGUMENT"}'
    )
    result = _classify("agy", 3, stderr=stderr)
    assert result.kind == absence.AUTH
    assert result.detail == "API key not valid. Please pass a valid API key."


CLAUDE_SESSION_LIMIT = "You've hit your session limit \u00b7 resets 7:50pm (America/New_York)"
CLAUDE_SESSION_LIMIT_ENVELOPE = (
    '{"type":"result","subtype":"success","is_error":true,"result":"' + CLAUDE_SESSION_LIMIT + '"}'
)
CLAUDE_AUTH_ENVELOPE = (
    '{"is_error":true,"num_turns":1,"subtype":"success","api_error_status":401,'
    '"result":"Failed to authenticate. API Error: 401 OAuth access token is invalid.","type":"result"}'
)
CODEX_USAGE_LIMIT = (
    "ERROR: You\u2019ve hit your usage limit. Visit https://chatgpt.com/codex/settings/usage to purchase more "
    "credits or try again at Oct 9th, 2026 6:08 PM."
)
CODEX_UNAUTHORIZED = (
    "ERROR: Reconnecting... 5/5\n"
    "ERROR: unexpected status 401 Unauthorized: Incorrect API key provided: sk-bogus*****7890. You can find your "
    "API key at https://platform.openai.com/account/api-keys., url: https://api.openai.com/v1/responses, "
    "auth error: 401, auth error code: invalid_api_key"
)
MUSE_REJECTED_KEY = "failed to fetch model catalog: authentication failed: your API key from META_API_KEY was rejected"


def test_claude_session_limit_is_quota() -> None:
    result = _classify("claude", 1, stdout=CLAUDE_SESSION_LIMIT_ENVELOPE)
    assert result.kind == absence.QUOTA
    assert result.detail == "You've hit your session limit"


def test_codex_usage_limit_is_quota() -> None:
    result = _classify("codex", 1, stderr=f"warning: retrying\n{CODEX_USAGE_LIMIT}\n{CODEX_USAGE_LIMIT}")
    assert result.kind == absence.QUOTA
    assert result.detail == "ERROR: You\u2019ve hit your usage limit"


def test_codex_usage_limit_with_a_straight_apostrophe_is_quota() -> None:
    assert _classify("codex", 1, stderr="ERROR: You've hit your usage limit.").kind == absence.QUOTA


def test_claude_invalid_token_is_auth() -> None:
    result = _classify("claude", 1, stdout=CLAUDE_AUTH_ENVELOPE)
    assert result.kind == absence.AUTH
    assert result.detail == "Failed to authenticate. API Error: 401 OAuth access token is invalid."


def test_codex_unauthorized_is_auth() -> None:
    result = _classify("codex", 1, stderr=CODEX_UNAUTHORIZED)
    assert result.kind == absence.AUTH
    assert result.detail == "ERROR: unexpected status 401 Unauthorized"


def test_muse_rejected_key_is_auth() -> None:
    result = _classify("muse", 1, stderr=MUSE_REJECTED_KEY)
    assert result.kind == absence.AUTH
    assert result.detail == "authentication failed: your API key from META_API_KEY was rejected"


@pytest.mark.parametrize(
    ("harness", "text"),
    [
        ("claude", CLAUDE_AUTH_ENVELOPE),
        ("claude", CLAUDE_SESSION_LIMIT_ENVELOPE),
        ("codex", CODEX_USAGE_LIMIT),
        ("codex", CODEX_UNAUTHORIZED),
        ("muse", MUSE_REJECTED_KEY),
    ],
)
def test_calibrated_patterns_do_not_cross_harnesses(harness: str, text: str) -> None:
    for other in ("claude", "codex", "muse"):
        if other != harness:
            assert _classify(other, 1, stderr=text).kind == absence.ERROR


def test_a_review_quoting_the_error_text_in_its_body_is_not_a_codex_quota_absence() -> None:
    body = '{"summary": "the log says ERROR: You\u2019ve hit your usage limit", "findings": []}'
    assert _classify("codex", 0, stdout=body).kind == absence.OK


META_KEY = "mta" + "Q7x9Lk2Pz8Rw4Tn6Yb1Vc3Hd5Jf0Gs7Ma2Ne"
OPENAI_KEY = "sk-" + "proj-" + "Zx8Cv7Bn6Mq5Wr4Ty3Ui2Op1As0Df9Gh8"
CLAUDE_OAUTH = "sk-ant-" + "oat01-" + "Kj7Hg6Fd5Sa4Lp3Oi2Uy1Tr0Ew9Qz8Xc7Vb6"


@pytest.mark.parametrize(
    "secret",
    [META_KEY, OPENAI_KEY, CLAUDE_OAUTH, f"META_API_KEY={META_KEY}", f"OPENAI_API_KEY: {OPENAI_KEY}"],
)
def test_excerpt_redacts_credentials(secret: str) -> None:
    text = absence.excerpt("", f"error: request with {secret} was rejected\n")
    assert "Q7x9Lk2Pz8" not in text
    assert "Zx8Cv7Bn6" not in text
    assert "Kj7Hg6Fd5" not in text
    assert "[redacted]" in text


def test_excerpt_redacts_the_exact_credential_values() -> None:
    short_but_secret = "abc12345xyz"
    text = absence.excerpt("", f"bad key {short_but_secret}", [short_but_secret])
    assert short_but_secret not in text


def test_excerpt_is_one_capped_line() -> None:
    stderr = "first line with detail\n" + "x " * 1000 + "\n\n"
    text = absence.excerpt("raw review output", stderr)
    assert "first line" not in text
    assert len(text) <= absence.EXCERPT_LIMIT
    assert absence.excerpt("only stdout line\n", "") == "only stdout line"
    assert absence.excerpt("", "") == ""


@pytest.mark.parametrize(
    ("harness", "text"),
    [
        ("claude", CLAUDE_SESSION_LIMIT_ENVELOPE),
        ("claude", CLAUDE_AUTH_ENVELOPE),
        ("codex", CODEX_USAGE_LIMIT),
        ("codex", CODEX_UNAUTHORIZED),
        ("muse", MUSE_REJECTED_KEY),
    ],
)
def test_calibrated_text_after_a_successful_exit_is_never_an_absence(harness: str, text: str) -> None:
    assert _classify(harness, 0, stdout=text, stderr=text).kind == absence.OK


def test_a_successful_claude_verdict_quoting_the_limit_text_keeps_its_verdict() -> None:
    summary = json.dumps({"summary": f"the log says {CLAUDE_SESSION_LIMIT}", "findings": []})
    envelope = json.dumps({"type": "result", "is_error": False, "result": summary})
    assert _classify("claude", 0, stdout=envelope, stderr=envelope).kind == absence.OK


@pytest.mark.parametrize("exit_code", [None, 0])
def test_calibrated_text_without_a_failing_exit_is_not_quota_or_auth(exit_code: int | None) -> None:
    result = _classify("codex", exit_code, stderr=CODEX_USAGE_LIMIT)
    assert result.kind not in (absence.QUOTA, absence.AUTH)
