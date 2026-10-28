from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pr_refresh_certification import (
    CERTIFICATION_FAST,
    DECISION_FULL,
    DECISION_SHADOW,
    ELIGIBILITY_PREDICATES,
    REASON_AFTER_HEAD_MISMATCH,
    REASON_BASE_DRIFT,
    REASON_CHAINED_REFRESH,
    REASON_CODES,
    REASON_DEPENDENCY_CHANGE,
    REASON_FORK_HEAD,
    REASON_GENERATED_DATA_CHANGE,
    REASON_HEAD_DRIFT,
    REASON_HEAD_OBJECT_MISSING,
    REASON_MALFORMED_PAYLOAD,
    REASON_MERGE_DRIVER_CHANGE,
    REASON_MERGE_TREE_FAILED,
    REASON_MERGE_TREE_MISMATCH,
    REASON_MISSING_EVENT_SHA,
    REASON_MISSING_PATH_EVIDENCE,
    REASON_MISSING_PRIOR_CHECK,
    REASON_NO_REQUIRED_CONTEXTS,
    REASON_NOT_SAME_REPOSITORY,
    REASON_NOT_SYNCHRONIZE,
    REASON_NOT_TWO_PARENTS,
    REASON_PARENT1_NOT_BEFORE,
    REASON_PARENT2_NOT_BASE,
    REASON_PRIOR_CHECK_NOT_SUCCESS,
    REASON_PRIOR_CHECK_UNBOUND,
    REASON_PRIOR_NOT_FULL,
    REASON_SCHEMA_CHANGE,
    REASON_SELF_CHANGE,
    REASON_SHARED_FIXTURE_CHANGE,
    REASON_SYNTHETIC_MERGE_AS_HEAD,
    REASON_UNKNOWN_PATH,
    REASON_WORKFLOW_CHANGE,
    REASON_WORKFLOW_FINGERPRINT,
    ClassificationRequest,
    classify,
    latest_check_run,
    main,
    request_from_mapping,
    required_contexts_from_ruleset,
    workflow_fingerprint,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "ci" / "pr-refresh"
ELIGIBLE_PATH = FIXTURE_DIR / "eligible.json"

H0 = "a" * 40
B0 = "b" * 40
B1 = "c" * 40
R1 = "d" * 40
SYN = "e" * 40


def _eligible_raw() -> dict:
    return json.loads(ELIGIBLE_PATH.read_text(encoding="utf-8"))


def _eligible_request(**overrides: object) -> ClassificationRequest:
    raw = _eligible_raw()
    raw.update(overrides)
    return request_from_mapping(raw)


def test_eligible_refresh_is_shadow_eligible() -> None:
    result = classify(_eligible_request())
    assert result.decision == DECISION_SHADOW
    assert result.reasons == []
    assert result.feature_head == R1
    assert result.parent1 == H0
    assert result.parent2 == B1
    assert result.tested_base_sha == B1
    assert result.certification_kind == "full"
    assert set(result.check_run_ids) == {
        "ci-required-result",
        "Results Explorer browser gate",
        "ruleset-drift",
    }


def test_cli_reads_eligible_fixture(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "decision.json"
    assert main(["--input", str(ELIGIBLE_PATH), "--json-out", str(out)]) == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["decision"] == DECISION_SHADOW
    printed = json.loads(capsys.readouterr().out)
    assert printed["decision"] == DECISION_SHADOW


def test_required_contexts_from_live_ruleset_shape() -> None:
    contexts = required_contexts_from_ruleset(
        {
            "rules": [
                {"type": "pull_request", "parameters": {}},
                {
                    "type": "required_status_checks",
                    "parameters": {
                        "strict_required_status_checks_policy": True,
                        "required_status_checks": [
                            {"context": "Results Explorer browser gate"},
                            {"context": "ci-required-result"},
                            {"context": "ruleset-drift"},
                        ],
                    },
                },
            ]
        }
    )
    assert contexts == (
        "Results Explorer browser gate",
        "ci-required-result",
        "ruleset-drift",
    )


def test_workflow_fingerprint_is_order_independent() -> None:
    left = workflow_fingerprint({"b.yml": "2", "a.yml": "1"})
    right = workflow_fingerprint({"a.yml": "1", "b.yml": "2"})
    assert left == right
    assert len(left) == 64


def test_latest_check_run_prefers_newest_started() -> None:
    raw = _eligible_raw()
    raw["check_runs"].append(
        {
            "id": 199,
            "name": "ci-required-result",
            "status": "completed",
            "conclusion": "failure",
            "started_at": "2026-08-13T00:00:00Z",
            "head_sha": H0,
            "run_id": 201,
        }
    )
    request = request_from_mapping(raw)
    latest = latest_check_run(request.check_runs, "ci-required-result")
    assert latest is not None
    assert latest.id == 101
    assert classify(request).decision == DECISION_SHADOW


def test_negative_not_synchronize_is_full_required() -> None:
    result = classify(_eligible_request(action="opened"))
    assert result.decision == DECISION_FULL
    assert result.reasons == [REASON_NOT_SYNCHRONIZE]


def test_negative_missing_event_sha_is_full_required() -> None:
    result = classify(_eligible_request(before="0" * 40))
    assert result.decision == DECISION_FULL
    assert result.reasons == [REASON_MISSING_EVENT_SHA]


def test_negative_after_head_mismatch_is_full_required() -> None:
    result = classify(_eligible_request(after=H0))
    assert result.decision == DECISION_FULL
    assert result.reasons == [REASON_AFTER_HEAD_MISMATCH]


def test_negative_fork_fallback_is_full_required() -> None:
    result = classify(_eligible_request(is_fork=True))
    assert result.decision == DECISION_FULL
    assert result.reasons == [REASON_FORK_HEAD]


def test_negative_repo_mismatch_is_full_required() -> None:
    result = classify(_eligible_request(head_repo_id=9))
    assert result.decision == DECISION_FULL
    assert result.reasons == [REASON_NOT_SAME_REPOSITORY]


def test_race_head_drift_is_full_required() -> None:
    result = classify(_eligible_request(current_head_sha="9" * 40))
    assert result.decision == DECISION_FULL
    assert result.reasons == [REASON_HEAD_DRIFT]


def test_race_base_drift_is_full_required() -> None:
    result = classify(_eligible_request(current_base_sha="9" * 40))
    assert result.decision == DECISION_FULL
    assert result.reasons == [REASON_BASE_DRIFT]


def test_tamper_synthetic_merge_as_head_is_full_required() -> None:
    result = classify(
        _eligible_request(
            head_sha=SYN,
            after=SYN,
            current_head_sha=SYN,
        )
    )
    assert result.decision == DECISION_FULL
    assert result.reasons == [REASON_SYNTHETIC_MERGE_AS_HEAD]


def test_negative_head_object_missing_is_full_required() -> None:
    raw = _eligible_raw()
    raw["commits"] = {}
    result = classify(request_from_mapping(raw))
    assert result.decision == DECISION_FULL
    assert result.reasons == [REASON_HEAD_OBJECT_MISSING]


def test_negative_head_object_key_and_embedded_sha_must_match() -> None:
    raw = _eligible_raw()
    raw["commits"][R1]["sha"] = H0
    result = classify(request_from_mapping(raw))
    assert result.decision == DECISION_FULL
    assert result.reasons == [REASON_HEAD_OBJECT_MISSING]


def test_negative_authored_commit_not_two_parents() -> None:
    raw = _eligible_raw()
    raw["commits"][R1]["parents"] = [H0]
    result = classify(request_from_mapping(raw))
    assert result.decision == DECISION_FULL
    assert result.reasons == [REASON_NOT_TWO_PARENTS]


def test_negative_parent1_not_before() -> None:
    raw = _eligible_raw()
    raw["before"] = B0
    raw["commits"][H0]
    result = classify(request_from_mapping(raw))
    assert result.decision == DECISION_FULL
    assert result.reasons == [REASON_PARENT1_NOT_BEFORE]


def test_negative_parent2_not_base() -> None:
    raw = _eligible_raw()
    raw["commits"][R1]["parents"] = [H0, B0]
    result = classify(request_from_mapping(raw))
    assert result.decision == DECISION_FULL
    assert result.reasons == [REASON_PARENT2_NOT_BASE]


def test_chain_refresh_parent_is_full_required() -> None:
    raw = _eligible_raw()
    raw["commits"][H0]["parents"] = [B0, "9" * 40]
    result = classify(request_from_mapping(raw))
    assert result.decision == DECISION_FULL
    assert result.reasons == [REASON_CHAINED_REFRESH]


def test_negative_merge_tree_mismatch_conflict_edit() -> None:
    result = classify(_eligible_request(merge_tree="3" * 40))
    assert result.decision == DECISION_FULL
    assert result.reasons == [REASON_MERGE_TREE_MISMATCH]


def test_negative_merge_tree_failed() -> None:
    result = classify(_eligible_request(merge_tree=None, merge_tree_error="conflict"))
    assert result.decision == DECISION_FULL
    assert result.reasons == [REASON_MERGE_TREE_FAILED]


def test_fallback_no_required_contexts() -> None:
    result = classify(_eligible_request(required_contexts=()))
    assert result.decision == DECISION_FULL
    assert result.reasons == [REASON_NO_REQUIRED_CONTEXTS]


def test_negative_missing_prior_check() -> None:
    raw = _eligible_raw()
    raw["check_runs"] = [item for item in raw["check_runs"] if item["name"] != "ruleset-drift"]
    result = classify(request_from_mapping(raw))
    assert result.decision == DECISION_FULL
    assert result.reasons == [REASON_MISSING_PRIOR_CHECK]


def test_negative_prior_check_not_success() -> None:
    raw = _eligible_raw()
    raw["check_runs"][0]["conclusion"] = "neutral"
    result = classify(request_from_mapping(raw))
    assert result.decision == DECISION_FULL
    assert result.reasons == [REASON_PRIOR_CHECK_NOT_SUCCESS]


def test_tamper_prior_check_unbound_head() -> None:
    raw = _eligible_raw()
    raw["actions_runs"][0]["head_sha"] = "9" * 40
    result = classify(request_from_mapping(raw))
    assert result.decision == DECISION_FULL
    assert result.reasons == [REASON_PRIOR_CHECK_UNBOUND]


def test_chain_prior_fast_certification_is_full_required() -> None:
    raw = _eligible_raw()
    for run in raw["actions_runs"]:
        run["certification_kind"] = CERTIFICATION_FAST
    result = classify(request_from_mapping(raw))
    assert result.decision == DECISION_FULL
    assert result.reasons == [REASON_PRIOR_NOT_FULL]


def test_skill_only_prior_certification_is_not_full_refresh_evidence() -> None:
    raw = _eligible_raw()
    for run in raw["actions_runs"]:
        run["certification_kind"] = "skill_integrity"

    result = classify(request_from_mapping(raw))

    assert result.decision == DECISION_FULL
    assert result.reasons == [REASON_PRIOR_NOT_FULL]


def test_tamper_workflow_fingerprint_mismatch() -> None:
    result = classify(_eligible_request(current_workflow_fingerprint="0" * 64))
    assert result.decision == DECISION_FULL
    assert result.reasons == [REASON_WORKFLOW_FINGERPRINT]


def test_fallback_self_change() -> None:
    result = classify(_eligible_request(authored_paths=["scripts/pr_refresh_certification.py"]))
    assert result.decision == DECISION_FULL
    assert result.reasons == [REASON_SELF_CHANGE]


@pytest.mark.parametrize("path", ["scripts/ruleset_drift_check.py", ".github/path-filters.yml"])
def test_fallback_required_context_helper_change(path: str) -> None:
    result = classify(_eligible_request(authored_paths=[path]))
    assert result.decision == DECISION_FULL
    assert result.reasons == [REASON_SELF_CHANGE]


def test_fallback_workflow_change() -> None:
    result = classify(_eligible_request(intervening_paths=[".github/workflows/pr.yml"]))
    assert result.decision == DECISION_FULL
    assert result.reasons == [REASON_WORKFLOW_CHANGE]


def test_fallback_merge_driver_change() -> None:
    result = classify(_eligible_request(authored_paths=[".gitattributes"]))
    assert result.decision == DECISION_FULL
    assert result.reasons == [REASON_MERGE_DRIVER_CHANGE]


def test_fallback_dependency_lock_change() -> None:
    result = classify(_eligible_request(intervening_paths=["uv.lock"]))
    assert result.decision == DECISION_FULL
    assert result.reasons == [REASON_DEPENDENCY_CHANGE]


def test_fallback_generated_data_change() -> None:
    result = classify(_eligible_request(authored_paths=["results-data/corpus-inventory.json"]))
    assert result.decision == DECISION_FULL
    assert result.reasons == [REASON_GENERATED_DATA_CHANGE]


def test_fallback_shared_fixture_change() -> None:
    result = classify(_eligible_request(authored_paths=["tests/conftest.py"]))
    assert result.decision == DECISION_FULL
    assert result.reasons == [REASON_SHARED_FIXTURE_CHANGE]


def test_fallback_schema_or_registry_change() -> None:
    result = classify(_eligible_request(authored_paths=["benchbox/platforms/registry.py"]))
    assert result.decision == DECISION_FULL
    assert result.reasons == [REASON_SCHEMA_CHANGE]


def test_fallback_unknown_path() -> None:
    result = classify(_eligible_request(authored_paths=["quality/new-area.txt"]))
    assert result.decision == DECISION_FULL
    assert result.reasons == [REASON_UNKNOWN_PATH]


@pytest.mark.parametrize("path", ["website/src/pages/index.astro", "results-explorer/src/db.ts", "_blog/post.md"])
def test_site_paths_moved_to_the_site_repository_are_unknown_roots(path: str) -> None:
    result = classify(_eligible_request(authored_paths=[path]))
    assert result.reasons == [REASON_UNKNOWN_PATH]


def test_fallback_missing_path_evidence() -> None:
    result = classify(_eligible_request(authored_paths=None, intervening_paths=None))
    assert result.decision == DECISION_FULL
    assert result.reasons == [REASON_MISSING_PATH_EVIDENCE]


def test_negative_malformed_pr_number() -> None:
    result = classify(_eligible_request(pr_number=0))
    assert result.decision == DECISION_FULL
    assert result.reasons == [REASON_MALFORMED_PAYLOAD]


def test_agent_instruction_budget_history_is_not_a_skip_proof() -> None:

    result = classify(
        _eligible_request(
            authored_paths=["AGENTS.md", "docs/agent/identity-instruction-boundary.md"],
            intervening_paths=["AGENTS.md", "Makefile"],
        )
    )
    assert result.decision == DECISION_SHADOW
    assert result.certification_kind == "full"


def test_cross_module_semantic_interaction_is_not_claimed_safe() -> None:

    result = classify(
        _eligible_request(
            authored_paths=["benchbox/platforms/duckdb.py"],
            intervening_paths=["benchbox/utils/clock.py"],
        )
    )
    assert result.decision == DECISION_SHADOW
    assert result.reasons == []
    assert result.decision != "behavior_equivalent"


PREDICATE_NEGATIVES: dict[str, tuple[dict[str, object], str]] = {
    "event_shas": ({"before": "short"}, REASON_MISSING_EVENT_SHA),
    "synchronize": ({"action": "reopened"}, REASON_NOT_SYNCHRONIZE),
    "after_is_head": ({"after": H0}, REASON_AFTER_HEAD_MISMATCH),
    "same_repository": ({"head_repo_id": 2}, REASON_NOT_SAME_REPOSITORY),
    "not_fork": ({"is_fork": True}, REASON_FORK_HEAD),
    "head_not_drifted": ({"current_head_sha": "9" * 40}, REASON_HEAD_DRIFT),
    "base_not_drifted": ({"current_base_sha": "9" * 40}, REASON_BASE_DRIFT),
    "not_synthetic_head": (
        {"head_sha": SYN, "after": SYN, "current_head_sha": SYN},
        REASON_SYNTHETIC_MERGE_AS_HEAD,
    ),
    "head_object": ({"commits": {}}, REASON_HEAD_OBJECT_MISSING),
    "two_parents": ({}, REASON_NOT_TWO_PARENTS),
    "parent1_before": ({"before": B0}, REASON_PARENT1_NOT_BEFORE),
    "parent2_base": ({}, REASON_PARENT2_NOT_BASE),
    "not_chained": ({}, REASON_CHAINED_REFRESH),
    "merge_tree": ({"merge_tree": "3" * 40}, REASON_MERGE_TREE_MISMATCH),
    "required_contexts": ({"required_contexts": []}, REASON_NO_REQUIRED_CONTEXTS),
    "prior_full_checks": ({"check_runs": []}, REASON_MISSING_PRIOR_CHECK),
    "fingerprint": ({"current_workflow_fingerprint": "0" * 64}, REASON_WORKFLOW_FINGERPRINT),
    "paths": ({"authored_paths": ["quality/new-area.txt"]}, REASON_UNKNOWN_PATH),
}


def _request_for_predicate(name: str) -> ClassificationRequest:
    overrides, _reason = PREDICATE_NEGATIVES[name]
    raw = _eligible_raw()
    if name == "two_parents":
        raw["commits"][R1]["parents"] = [H0]
    elif name == "parent2_base":
        raw["commits"][R1]["parents"] = [H0, B0]
    elif name == "not_chained":
        raw["commits"][H0]["parents"] = [B0, "9" * 40]
    raw.update(overrides)
    return request_from_mapping(raw)


def test_every_eligibility_predicate_has_a_negative_control() -> None:
    names = [name for name, _pred in ELIGIBILITY_PREDICATES]
    assert names == list(PREDICATE_NEGATIVES)
    for name, _pred in ELIGIBILITY_PREDICATES:
        result = classify(_request_for_predicate(name))
        expected = PREDICATE_NEGATIVES[name][1]
        assert result.decision == DECISION_FULL, name
        assert result.reasons == [expected], name


def test_deleting_any_predicate_would_drop_its_reason() -> None:

    for name, predicate in ELIGIBILITY_PREDICATES:
        request = _request_for_predicate(name)
        out_reason = PREDICATE_NEGATIVES[name][1]
        from pr_refresh_certification import Classification

        scratch = Classification(decision=DECISION_FULL, pr_number=request.pr_number)
        assert predicate(request, scratch) == out_reason


def test_reason_code_inventory_is_complete() -> None:
    assert REASON_MISSING_EVENT_SHA in REASON_CODES
    assert len(REASON_CODES) == len(set(REASON_CODES))
    assert DECISION_FULL == "full_required"
    assert DECISION_SHADOW == "shadow_eligible"


def test_malformed_mapping_defaults_to_full(capsys: pytest.CaptureFixture[str]) -> None:
    import io
    import sys

    sys.stdin = io.StringIO("[]\n")
    try:
        assert main([]) == 0
    finally:
        sys.stdin = sys.__stdin__
    payload = json.loads(capsys.readouterr().out)
    assert payload["decision"] == DECISION_FULL
    assert payload["reasons"] == [REASON_MALFORMED_PAYLOAD]


def test_malformed_nested_mapping_defaults_to_full(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    raw = _eligible_raw()
    raw["commits"][R1] = []
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(raw), encoding="utf-8")

    assert main(["--input", str(request_path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["decision"] == DECISION_FULL
    assert payload["reasons"] == [REASON_MALFORMED_PAYLOAD]


def test_malformed_authored_path_defaults_to_full(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    raw = _eligible_raw()
    raw["authored_paths"] = [7]
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(raw), encoding="utf-8")

    assert main(["--input", str(request_path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["decision"] == DECISION_FULL
    assert payload["reasons"] == [REASON_MALFORMED_PAYLOAD]


def test_request_copy_does_not_share_mutable_eligible_state() -> None:
    first = _eligible_raw()
    second = copy.deepcopy(first)
    first["action"] = "opened"
    assert classify(request_from_mapping(second)).decision == DECISION_SHADOW
