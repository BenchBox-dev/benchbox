from __future__ import annotations

import pytest

from _project.scripts.oracle_reviewers import absence, credentials
from _project.scripts.oracle_reviewers.verdict import REDACTED, VerdictError, sanitize, validate

pytestmark = [pytest.mark.unit, pytest.mark.fast]

SECRET = "fakecred-Aa1Bb2Cc3Dd4"
OTHER = "Cc3Dd4Ee5Ff6-fakecred2"
VERDICT = {
    "status": "complete",
    "incomplete_reason": "",
    "decision": "SHIP",
    "summary": "fine",
    "files_examined": ["a.py"],
    "defects": [],
    "prior_defects": [],
}


@pytest.mark.parametrize("name", credentials.NAMES)
def test_every_named_credential_is_redacted(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(name, SECRET)
    assert credentials.redact(f"before {SECRET} after") == f"before {REDACTED} after"


def test_overlapping_values_are_merged_into_one_redaction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "abcdefghij")
    monkeypatch.setenv("GEMINI_API_KEY", "ghijklmnop")
    text = credentials.redact("x abcdefghijklmnop y")
    assert text == f"x {REDACTED} y"


def test_control_characters_inside_a_value_do_not_hide_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    split = SECRET[:6] + "\x01\x02" + SECRET[6:]
    assert credentials.redact(f"a {split} b") == f"a {REDACTED} b"
    assert SECRET[6:] not in sanitize(f"a {split} b", 500)


def test_a_value_assembled_by_sanitizing_is_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    glued = f"{SECRET[:8]}[](u){SECRET[8:]}"
    assert SECRET not in sanitize(glued, 500)
    assert REDACTED in sanitize(glued, 500)


def test_a_value_straddling_the_limit_leaks_no_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    text = "x" * 190 + SECRET
    for limit in range(185, 215):
        cleaned = sanitize(text, limit)
        assert SECRET[:6] not in cleaned.replace("x", "")
    stderr = "error: " + "x " * 145 + SECRET + " tail\n"
    assert SECRET[:5] not in absence.excerpt("", stderr).replace("x", "")


def test_absence_details_are_redacted_whatever_their_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_CODEX_OAUTH_TOKEN", SECRET)
    assert SECRET not in absence.Absence(absence.INVALID, f"bad {SECRET}").detail
    assert SECRET not in absence.excerpt("", f"error: {SECRET}")
    assert SECRET not in absence.unparsed(f"{SECRET} " * 400)


@pytest.mark.parametrize("field", ["summary", "incomplete_reason"])
def test_validated_text_fields_are_redacted_in_place(field: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    verdict = validate({**VERDICT, field: f"saw {SECRET} here"})
    assert SECRET not in getattr(verdict, field)
    assert getattr(verdict, field) == f"saw {REDACTED} here"


def test_finding_and_prior_text_is_redacted_without_changing_structure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    finding = {"severity": "High", "file": "a.py", "line": 3, "title": f"t {SECRET}", "detail": f"d {SECRET}"}
    prior = {"id": "D1", "status": "not_fixed", "evidence": f"e {SECRET}"}
    verdict = validate({**VERDICT, "decision": "SHIP_WITH_FIXES", "defects": [finding], "prior_defects": [prior]})
    assert (verdict.defects[0].file, verdict.defects[0].line) == ("a.py", 3)
    assert verdict.prior_defects[0].id == "D1"
    assert SECRET not in str(verdict.to_json())


@pytest.mark.parametrize(
    "mutate",
    [
        lambda v: {**v, "files_examined": [f"pkg/{SECRET}.py"]},
        lambda v: {**v, "files_examined": ["pkg/" + SECRET[:5] + "\x01" + SECRET[5:] + ".py"]},
        lambda v: {
            **v,
            "defects": [{"severity": "High", "file": f"{SECRET}.py", "line": 1, "title": "t", "detail": ""}],
        },
        lambda v: {**v, "prior_defects": [{"id": SECRET, "status": "fixed", "evidence": ""}]},
        lambda v: {**v, SECRET: 1},
        lambda v: {
            **v,
            "defects": [
                {"severity": "High", "file": "a.py", "line": 1, "title": "t", "detail": "", SECRET: 1},
            ],
        },
    ],
)
def test_a_credential_in_a_structural_field_invalidates_with_a_fixed_message(
    mutate, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    with pytest.raises(VerdictError) as caught:
        validate(mutate(VERDICT))
    assert SECRET not in str(caught.value)
    assert SECRET[:6] not in str(caught.value)
    assert str(caught.value) == "the verdict carries a credential in a structural field"


def test_validator_error_snippets_never_echo_a_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    for bad in ({"nested": SECRET}, [SECRET], 5):
        with pytest.raises(VerdictError) as caught:
            validate({**VERDICT, "prior_defects": [{"id": bad, "status": "fixed", "evidence": ""}]})
        assert SECRET not in str(caught.value)
    with pytest.raises(VerdictError) as caught:
        validate({**VERDICT, "summary": 5, "extra_" + SECRET[:3]: 1})
    assert SECRET not in str(caught.value)


@pytest.mark.parametrize(
    "name", ["ORACLE_GATEWAY_URL", "TS_OAUTH_CLIENT_ID", "TS_AUDIENCE", "ACTIONS_ID_TOKEN_REQUEST_TOKEN"]
)
def test_the_runner_held_values_are_named_and_redacted(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    assert name in credentials.NAMES
    monkeypatch.setenv(name, SECRET)
    assert credentials.redact(f"before {SECRET} after") == f"before {REDACTED} after"
    split = SECRET[:6] + "\x01" + SECRET[6:]
    assert credentials.redact(f"before {split} after") == f"before {REDACTED} after"
