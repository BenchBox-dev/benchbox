from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from _project.scripts.oracle_reviewers import attempts, protocol, selection
from _project.scripts.oracle_reviewers.policy import Policy
from _project.scripts.oracle_reviewers.verdict import (
    RESPONSE_PLACEHOLDERS,
    VerdictError,
    normalize_reviewer,
    parse_output,
    shape_fingerprint,
    validate,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _payload(**changes: object) -> dict:
    return {
        "status": "complete",
        "incomplete_reason": "",
        "decision": "SHIP_WITH_FIXES",
        "summary": "The input can be lost.",
        "files_examined": ["input.py"],
        "defects": [
            {
                "severity": "High",
                "file": "input.py",
                "line": 1,
                "title": "Input lost",
                "detail": "The value is dropped.",
            }
        ],
        "prior_defects": [],
        **changes,
    }


@pytest.mark.parametrize("field", ["line_note", "end_line_note", "detail_extra"])
def test_known_finding_metadata_does_not_erase_a_blocking_finding(field: str) -> None:
    raw = _payload()
    raw["defects"][0][field] = "metadata"
    original = copy.deepcopy(raw)
    with pytest.raises(VerdictError):
        validate(raw)
    normalized, rewrites = normalize_reviewer(raw)
    verdict = validate(normalized)
    assert protocol.judge_planned(verdict, {"max_defects": 10}).decision == "SHIP_WITH_FIXES"
    assert verdict.defects[0].title == "Input lost"
    assert rewrites == ("defects_metadata",)
    assert raw == original


@pytest.mark.parametrize("field", ["severity", "file", "line", "end_line", "title", "detail"])
def test_prior_finding_metadata_is_invalid_not_stripped(field: str) -> None:
    prior = {"id": "D2", "status": "not_fixed", "evidence": "The old behavior remains.", field: "metadata"}
    normalized, _ = normalize_reviewer(_payload(defects=[], prior_defects=[prior]))
    with pytest.raises(VerdictError):
        validate(normalized)


def test_misfiled_finding_under_priors_cannot_ship() -> None:
    raw = _payload(
        defects=[],
        decision="SHIP",
        prior_defects=[
            {
                "id": "D99",
                "status": "fixed",
                "evidence": "misplaced",
                "severity": "High",
                "file": "input.py",
                "line": 1,
                "title": "Input lost",
                "detail": "The value is dropped.",
            }
        ],
    )
    normalized, _ = normalize_reviewer(raw)
    with pytest.raises(VerdictError):
        validate(normalized)


def test_unknown_prior_ids_invalidate_a_verdict_that_would_ship() -> None:
    priors = [
        {"id": "D2", "status": "fixed", "evidence": "fixed"},
        {"id": "D99", "status": "not_fixed", "evidence": "A new defect filed as a prior."},
    ]
    verdict = validate(_payload(decision="SHIP", defects=[], prior_defects=priors))
    assert protocol.contradiction(verdict, {"max_defects": 10, "protocol": {"prior": [{"id": "D2"}]}}) is not None


@pytest.mark.parametrize("decision", ["SHIP_WITH_FIXES", "DO_NOT_SHIP"])
def test_unknown_prior_ids_do_not_discard_a_blocking_verdict(decision: str) -> None:
    verdict = validate(_payload(decision=decision, prior_defects=[{"id": "D99", "status": "fixed", "evidence": "x"}]))
    plan = {"max_defects": 10, "protocol": {"prior": []}}
    assert protocol.contradiction(verdict, plan) is None
    assert protocol.judge_planned(verdict, plan).decision == decision


@pytest.mark.parametrize("embedded", [None, "D2", "#D2"])
def test_dictionary_priors_keep_the_key_and_status(embedded: str | None) -> None:
    value = {"status": "not_fixed", "evidence": "Still present"}
    if embedded is not None:
        value["id"] = embedded
    normalized, _ = normalize_reviewer(_payload(prior_defects={"#D2": value}))
    assert validate(normalized).prior_defects[0].to_json() == {
        "id": "D2",
        "status": "not_fixed",
        "evidence": "Still present",
    }


@pytest.mark.parametrize(
    "priors",
    [
        {"D2": "fixed"},
        {"D2": {"id": "D3", "status": "fixed", "evidence": "x"}},
        {"D 2": {"status": "fixed", "evidence": "x"}},
        {"D2": {"status": "fixed", "evidence": "x"}, "#D2": {"status": "fixed", "evidence": "x"}},
    ],
)
def test_dictionary_priors_never_repair_conflicting_or_duplicate_ids(priors: dict) -> None:
    with pytest.raises(VerdictError):
        normalized, _ = normalize_reviewer(_payload(prior_defects=priors))
        validate(normalized)


@pytest.mark.parametrize(
    "text",
    [
        '{"defects":[{"severity":"High"}],"defects":[]}',
        '{"prior_defects":{"D2":{"status":"not_fixed"},"D2":{"status":"fixed"}}}',
        '{"nested":{"decision":"DO_NOT_SHIP","decision":"SHIP"}}',
    ],
)
def test_duplicate_json_keys_cannot_hide_findings_or_prior_status(text: str) -> None:
    with pytest.raises(VerdictError):
        parse_output("text", text)


def test_json_envelope_does_not_bypass_duplicate_key_rejection() -> None:
    text = json.dumps({"result": '{"decision":"DO_NOT_SHIP","decision":"SHIP"}'})
    with pytest.raises(VerdictError):
        parse_output("claude", text)


@pytest.mark.parametrize("placeholder", RESPONSE_PLACEHOLDERS.values())
def test_unfilled_template_text_is_not_a_valid_review(placeholder: str) -> None:
    with pytest.raises(VerdictError):
        normalize_reviewer(_payload(summary=placeholder))
    raw = _payload()
    raw["defects"][0]["file"] = placeholder
    with pytest.raises(VerdictError):
        validate(raw)


@pytest.mark.parametrize("change", [{"issues": []}, {"defects": None}, {"decision": "approve"}])
def test_normalization_does_not_fill_or_drop_decision_bearing_fields(change: dict) -> None:
    with pytest.raises(VerdictError):
        normalized, _ = normalize_reviewer(_payload(**change))
        validate(normalized)


def test_missing_do_not_ship_summary_stays_invalid() -> None:
    raw = _payload(decision="DO_NOT_SHIP", defects=[])
    del raw["summary"]
    normalized, _ = normalize_reviewer(raw)
    with pytest.raises(VerdictError):
        validate(normalized)


def test_actual_pr3237_responses_preserve_unfixed_d6() -> None:
    path = Path(__file__).resolve().parents[3] / "fixtures/oracle_reviewers/pr3237_missing_text_fields.json"
    for record in json.loads(path.read_text(encoding="utf-8")):
        with pytest.raises(VerdictError):
            validate(record["payload"])
        normalized, _ = normalize_reviewer(record["payload"])
        verdict = validate(normalized)
        assert next(prior.status for prior in verdict.prior_defects if prior.id == "D6") == "not_fixed"
        plan = {"max_defects": 10, "protocol": {"prior": [{"id": "D5"}, {"id": "D6"}]}}
        assert protocol.judge_planned(verdict, plan).decision == "SHIP_WITH_FIXES"
        assert protocol.contradiction(verdict, plan) is None


def test_empty_swf_is_invalid_but_out_of_scope_findings_keep_followup_semantics() -> None:
    plan = {"max_defects": 10, "protocol": {"kind": "follow-up", "changed": ["other.py"]}}
    assert protocol.contradiction(validate(_payload(defects=[])), plan) is not None
    verdict = validate(_payload())
    assert protocol.contradiction(verdict, plan) is None
    judgement = protocol.judge_planned(verdict, plan)
    assert judgement.decision == "SHIP" and judgement.not_counted == verdict.defects


def test_forced_rejudge_does_not_clear_an_earlier_defect() -> None:
    verdict = validate(_payload(defects=[], prior_defects=[{"id": "D2", "status": "fixed", "evidence": "fixed"}]))
    plan = {"max_defects": 10, "protocol": {"prior": [{"id": "D2"}], "forced": True}}
    assert protocol.contradiction(verdict, plan) is None
    assert protocol.judge_planned(verdict, plan).decision == "SHIP_WITH_FIXES"


@pytest.mark.parametrize("malformation", ["empty_swf", "missing_summary"])
def test_stored_artifacts_are_not_repaired_and_cannot_replay_empty_swf(
    policy: Policy, tmp_path: Path, malformation: str
) -> None:
    payload = _payload(defects=[])
    if malformation == "missing_summary":
        del payload["summary"]
    plan = {
        "pr": 7,
        "head_sha": "a" * 40,
        "chain": [policy.reviewers["small"].to_json()],
        "max_attempts": 5,
        "max_defects": 10,
    }
    artifact = attempts.build_artifact(
        run_id="42", pr=7, head_sha="a" * 40, slot=1, reviewer="small", verdict=payload, missing=None
    )
    (tmp_path / "attempt-1.json").write_text(json.dumps(artifact), encoding="utf-8")
    loaded = attempts.load(tmp_path, plan, "42")
    assert not loaded.errors and not loaded.verdicts
    assert loaded.attempts[0].outcome == selection.ABSENT
    assert loaded.attempts[0].absence == "invalid"


def test_shape_fingerprint_is_bounded_and_omits_values_and_unsafe_keys() -> None:
    secret = "short-private-value"
    data = {"defects": [{"title": secret}], secret: "hidden", "unsafe\nname": "hidden", "nested": {"safe": 3}}
    text = shape_fingerprint(data, [secret])
    assert secret not in text and "hidden" not in text and "unsafe\\nname" not in text
    assert json.loads(text)["defects"] == {"list_items": 1}
    assert len(shape_fingerprint({f"field_{i}": {f"child_{i}": i} for i in range(1000)}).encode()) <= 512


def test_dictionary_id_collision_past_the_display_limit_is_not_hidden() -> None:
    priors = {f"D{index}": {"status": "fixed", "evidence": "fixed"} for index in range(1, 52)}
    priors["#D1"] = {"status": "not_fixed", "evidence": "still present"}
    with pytest.raises(VerdictError):
        normalize_reviewer(_payload(defects=[], prior_defects=priors))


@pytest.mark.parametrize(
    "text", ["<summ\u0001ary>", "<[summary](https://example.com)>", "[[<summary>](a)](b)", "[[[<summary>](a)](b)](c)"]
)
def test_transformed_placeholders_cannot_disagree_between_live_and_replay(text: str) -> None:
    with pytest.raises(VerdictError):
        normalize_reviewer(_payload(summary=text))


def test_nested_links_accepted_live_are_accepted_on_replay() -> None:
    normalized, _ = normalize_reviewer(_payload(summary="[[kept](a)](b)"))
    verdict = validate(normalized)
    stored = json.loads(json.dumps(verdict.to_json()))
    assert validate(stored, trusted=True).decision == verdict.decision
