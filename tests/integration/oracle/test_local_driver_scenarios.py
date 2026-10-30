from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from _project.scripts.oracle_reviewers import cli, local, report

from .fake_github import OWNER_TOKEN
from .harness import CHECKER, GATEWAY_TOKEN, JEV_KEY, Harness

pytestmark = [pytest.mark.integration, pytest.mark.medium]

PATCH = "def checker(a, b):\n    return a == b and a is not None\n"
SECOND_PATCH = "def checker(a, b):\n    return a == b and a is not None or a is b\n"
SHIP = {"decision": "SHIP", "summary": "No defects.", "files_examined": [CHECKER]}
KEYCHAIN_STEP = 'security add-generic-password -s TYPESAFE_API_KEY -a "$USER" -w'
TS_AUTHKEY = "tskey-auth-" + "Pq7Lm2Nv" * 4


def _defect(severity: str, line: int = 2) -> dict[str, Any]:
    return {
        "severity": severity,
        "file": CHECKER,
        "line": line,
        "title": f"{severity} issue {line}",
        "detail": "checker mishandles operands of different types, so a boolean column matches an integer column.",
    }


def _reply(decision: str, *defects: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "decision": decision,
        "summary": "Reviewed.",
        "files_examined": [CHECKER],
        "defects": list(defects),
        **extra,
    }


def _local(harness: Harness, branch: str = "feat/local", **options: Any):
    return harness.local_review(branch, **options)


def _prepare(harness: Harness, branch: str = "feat/local", patch: str = PATCH) -> None:
    harness.branch(branch, {CHECKER: patch}, "reject None")


def test_a_medium_only_ship_with_fixes_proceeds(harness: Harness) -> None:
    _prepare(harness)
    harness.script_omp([_reply("SHIP_WITH_FIXES", _defect("Medium"), _defect("Low", 3))])

    review = _local(harness)

    assert review.code == 0, review.stdout + review.stderr
    assert "decision SHIP WITH FIXES" in review.stdout
    assert "no Critical or High findings; pr-open may proceed" in review.stdout


def test_a_do_not_ship_from_the_max_defects_overflow_blocks(harness: Harness) -> None:
    _prepare(harness)
    harness.script_omp([_reply("SHIP_WITH_FIXES", *(_defect("Low", line) for line in range(1, 13)))])

    review = _local(harness)

    assert review.code == 1, review.stdout + review.stderr
    assert "decision DO NOT SHIP" in review.stdout and "more than the 10 allowed" in review.stdout
    assert "Refusing to open the PR" in review.stderr


def _first_round_with_a_critical(harness: Harness, pr: int) -> None:
    harness.branch("feat/checker", {CHECKER: "def checker(a, b):\n    return a is b\n"}, "compare by identity")
    harness.github.open_pull(pr, "feat/checker")
    harness.script_omp([_reply("SHIP_WITH_FIXES", _defect("Critical"))])
    run = harness.review(pr)
    assert [item["state"] for item in harness.github.oracle_statuses(run.plan["head_sha"])] == ["failure"]


def test_an_existing_pull_request_with_a_carried_critical_blocks_even_when_the_new_diff_is_clean(
    harness: Harness,
) -> None:
    _first_round_with_a_critical(harness, 61)
    harness.push("feat/checker", {"README.md": "seed\n\nA note.\n"}, "explain")
    harness.script_omp(
        [
            _reply(
                "SHIP_WITH_FIXES",
                priors={"D1": {"status": "not_fixed", "evidence": f"{CHECKER}:2 still compares by identity."}},
            )
        ]
    )

    review = harness.local_review("feat/checker")

    assert review.code == 1, review.stdout + review.stderr
    assert "[Critical]" in review.stdout and "Refusing to open the PR" in review.stderr


def test_an_edited_app_review_in_the_pull_requests_history_makes_the_review_refuse(harness: Harness) -> None:
    _first_round_with_a_critical(harness, 62)
    harness.github.edit_review(62, 0, harness.github.reviews[62][0]["body"] + "\nedited")
    harness.push("feat/checker", {"README.md": "seed\n\nA note.\n"}, "explain")
    calls = harness.omp_calls()

    review = harness.local_review("feat/checker")

    assert review.code == 2, review.stdout + review.stderr
    assert "an oracle review was edited" in review.stderr
    assert harness.omp_calls() == calls


def test_an_existing_pull_requests_decided_head_is_judged_from_its_recorded_findings(harness: Harness) -> None:
    _first_round_with_a_critical(harness, 63)
    calls = harness.omp_calls()

    review = harness.local_review("feat/checker")

    assert review.code == 1, review.stdout + review.stderr
    assert "[Critical]" in review.stdout
    assert harness.omp_calls() == calls


def test_no_classifier_key_refuses_and_names_the_keychain_step(harness: Harness) -> None:
    _prepare(harness)
    harness.script_omp([SHIP])

    review = _local(harness, key=False)

    assert review.code == 2, review.stdout + review.stderr
    assert KEYCHAIN_STEP in review.stderr
    assert harness.omp_calls() == 0


def test_a_change_with_only_exempt_paths_needs_no_key(harness: Harness) -> None:
    harness.branch("feat/notes", {"NOTES.md": "notes\n"}, "add notes")

    review = harness.local_review("feat/notes", key=False)

    assert review.code == 0, review.stdout + review.stderr
    assert "no review needed" in review.stdout
    assert harness.omp_calls() == 0


@pytest.mark.skipif(sys.platform != "darwin", reason="the keychain is read on macOS")
def test_the_keychain_key_is_used_when_the_environment_has_none(harness: Harness) -> None:
    _prepare(harness)
    harness.script_omp([SHIP])
    security = harness.bin / "security"
    security.write_text(f"#!/bin/sh\necho {JEV_KEY}\n", encoding="utf-8")

    review = _local(harness, key=False)

    assert review.code == 0, review.stdout + review.stderr
    assert JEV_KEY not in review.stdout + review.stderr


def _gateway_directory(harness: Harness) -> Path:
    directory = harness.home / local.GATEWAY_DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "local-client.token").write_text(GATEWAY_TOKEN + "\n", encoding="utf-8")
    return directory


def test_the_canonical_gateway_token_file_does_not_trip_the_disk_scan(harness: Harness) -> None:
    _prepare(harness)
    _gateway_directory(harness)
    harness.script_omp([SHIP])

    review = _local(harness)

    assert review.code == 0, review.stdout + review.stderr


def test_a_planted_copy_of_the_local_gateway_token_in_a_scanned_root_fails_the_run(harness: Harness) -> None:
    _prepare(harness)
    directory = _gateway_directory(harness)
    (directory / "local-client.token.copy").write_text(GATEWAY_TOKEN + "\n", encoding="utf-8")
    harness.script_omp([SHIP])

    review = _local(harness)

    assert review.code == 2, review.stdout + review.stderr
    assert "ORACLE_HOST_TOKEN_LOCAL" not in review.stdout
    assert "a secret value is on disk" in review.stderr
    assert GATEWAY_TOKEN not in review.stdout + review.stderr
    assert harness.omp_calls() == 0


def test_a_planted_classifier_key_in_the_state_directory_fails_the_run(harness: Harness) -> None:
    _prepare(harness)
    harness.local_state.mkdir(parents=True)
    (harness.local_state / "stray.txt").write_text(JEV_KEY, encoding="utf-8")
    harness.script_omp([SHIP])

    review = _local(harness)

    assert review.code == 2, review.stdout + review.stderr
    assert harness.omp_calls() == 0


def test_a_quota_absence_persists_the_pool_block_for_the_next_review(harness: Harness) -> None:
    _prepare(harness)
    quota = {"kind": "error", "status": 429, "provider": "anthropic", "message": "rate_limit_error: 429 Resets in 5d."}
    harness.script_omp([quota, quota, quota, quota, quota])

    review = _local(harness)

    assert review.code == 2, review.stdout + review.stderr
    stored = json.loads((harness.local_state / local.POOL_BLOCKS_FILE).read_text(encoding="utf-8"))
    assert "claude" in stored and stored["claude"]["provider"] == "anthropic"


def _planned_and_judged(harness: Harness, branch: str, monkeypatch: pytest.MonkeyPatch) -> tuple[dict, Any]:
    argv, env, checkout = harness._local_review_command(branch)
    saved: dict[str, Any] = {}
    original = local._decided

    def spy(run: local.Run, plan: dict[str, Any]) -> local.Outcome:
        saved["plan"] = plan
        os.environ["GITHUB_RUN_ID"] = run.run_id
        _, loaded, step, errors = cli.decided_attempts(
            plan, run.layout.attempts, run.layout.classification, run.layout.workspace
        )
        saved["final"] = report.finalize(
            plan, step, errors, loaded.attempts, loaded.verdicts, {}, workspace=run.layout.workspace, secrets=()
        )
        return original(run, plan)

    monkeypatch.setattr(local, "_decided", spy)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.chdir(checkout)
    monkeypatch.setattr(local, "_handed_over", {})
    args = cli._parser().parse_args(argv[3:])
    assert local.command_review(args) in (0, 1)
    return saved["plan"], saved["final"]


ADAPTER_FIELDS = {"run_id", "run_uuid", "created_at", "pr", "head_sha", "decision_reason", "previous_state"}
PARITY_CASES = {
    "clean": ([_reply("SHIP")], False),
    "medium-only": ([_reply("SHIP_WITH_FIXES", _defect("Medium"))], False),
    "high": ([_reply("SHIP_WITH_FIXES", _defect("High"))], True),
    "overflow": ([_reply("SHIP_WITH_FIXES", *(_defect("Low", line) for line in range(1, 12)))], True),
}


@pytest.mark.parametrize("case", PARITY_CASES)
def test_the_ci_and_local_adapters_plan_judge_and_map_the_same_patch_identically(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    replies, blocked = PARITY_CASES[case]
    harness.branch("feat/ci", {CHECKER: PATCH}, "reject None")
    harness.github.open_pull(71, "feat/ci")
    harness.branch("feat/local", {CHECKER: PATCH}, "reject None, locally")
    harness.script_omp([*replies, *replies])
    ci = harness.review(71)
    monkeypatch.setenv("GITHUB_RUN_ID", str(ci.run["id"]))
    ci_plan = ci.plan
    _, ci_loaded, ci_step, ci_errors = cli.decided_attempts(
        ci_plan,
        ci.temp / "oracle-attempts",
        ci.temp / "oracle-classification" / cli.CLASSIFICATION_FILE,
        ci.temp / "workspace",
    )
    ci_final = report.finalize(
        ci_plan, ci_step, ci_errors, ci_loaded.attempts, ci_loaded.verdicts, {}, workspace=ci.temp / "workspace"
    )

    local_plan, local_final = _planned_and_judged(harness, "feat/local", monkeypatch)

    keep = lambda plan: {key: value for key, value in plan.items() if key not in ADAPTER_FIELDS}
    assert keep(local_plan) == keep(ci_plan)
    assert local_final.judgement == ci_final.judgement
    assert local_final.judgement is not None
    assert local.judged_outcome(local_final.judgement, "r").blocked is blocked
    assert (
        local.judged_outcome(ci_final.judgement, "r").blocked
        is local.judged_outcome(local_final.judgement, "r").blocked
    )


def test_no_withheld_secret_is_readable_in_any_ancestor_environment_of_the_local_reviewer(harness: Harness) -> None:
    _prepare(harness)
    probe = {
        "TYPESAFE_API_KEY": JEV_KEY,
        "TS_AUTHKEY": TS_AUTHKEY,
        "GH_TOKEN": OWNER_TOKEN,
        "OMP_GATEWAY_TOKEN": GATEWAY_TOKEN,
    }
    harness.script_omp([SHIP], ancestor_probe=probe)

    review = _local(harness, env={"TS_AUTHKEY": TS_AUTHKEY})

    assert review.code == 0, review.stdout + review.stderr
    chain = harness.omp_ancestors()
    assert chain, "the reviewer found no ancestor process to inspect"
    found = {name for ancestor in chain for name in ancestor["found"]}
    assert found <= {"OMP_GATEWAY_TOKEN"}, chain
    assert chain[-1]["found"] == [], chain
