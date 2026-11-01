from __future__ import annotations

import json
from pathlib import Path

import pytest

from .fake_github import ACCOUNTS, APP_TOKEN, JOB_TOKEN
from .harness import CHECKER, GATEWAY_TOKEN, RECORDED_STREAM, Harness

pytestmark = [pytest.mark.integration, pytest.mark.medium]

FIXED_CHECKER = "def checker(a, b):\n    if a is None or b is None:\n        return False\n    return a == b\n"

ORACLE_BOT = ACCOUNTS[APP_TOKEN]


def _ship(summary: str = "The change keeps checker semantics; no defects.") -> dict:
    return {"decision": "SHIP", "summary": summary, "files_examined": [CHECKER]}


def test_first_review_ships_posts_success_and_the_gate_passes(harness: Harness) -> None:
    head = harness.branch("feat/checker", {CHECKER: FIXED_CHECKER}, "guard None operands")
    harness.github.open_pull(41, "feat/checker")
    harness.script_omp([_ship()], leak_env="OMP_GATEWAY_TOKEN")

    run = harness.review(41)

    assert run.steps["prepare"].outputs["decision"] == "review"
    assert [item["outcome"] for item in run.attempts()] == ["verdict"]
    verdicts = harness.github.oracle_statuses(head)
    assert [(item["state"], item["creator"]["login"]) for item in verdicts] == [("success", "benchbox-oracle[bot]")]
    review = harness.github.reviews[41][-1]
    assert review["commit_id"] == head and f"### oracle-verdict: success for `{head}`" in review["body"]
    assert harness.github.dispatches == [
        {"workflow": "oracle-gate.yml", "ref": "develop", "inputs": {"pr": "41", "head_sha": head}}
    ]
    assert [(item["state"], item["description"]) for item in harness.github.gate_statuses(head)] == [
        ("success", f"oracle-review: pass (oracle verdict for {head})")
    ]
    assert harness.github.gate_statuses(head)[0]["creator"]["login"] == "benchbox-oracle[bot]"

    gate = harness.gate(41, head)

    assert gate.code == 0, gate.stderr
    assert [item["state"] for item in harness.github.gate_statuses(head)] == ["success", "success"]


def test_the_verdict_run_posts_a_failing_gate_for_a_held_head(harness: Harness) -> None:
    head = harness.branch("feat/checker", {CHECKER: FIXED_CHECKER}, "guard None operands")
    harness.github.open_pull(42, "feat/checker")
    harness.script_omp(
        [
            {
                "decision": "SHIP_WITH_FIXES",
                "summary": "One defect.",
                "files_examined": [CHECKER],
                "defects": [
                    {
                        "severity": "High",
                        "file": CHECKER,
                        "line": 2,
                        "title": "Identity comparison replaces equality",
                        "detail": "checker returns a is b, so equal values held in distinct objects compare unequal.",
                    }
                ],
            }
        ]
    )

    harness.review(42)

    assert [item["state"] for item in harness.github.oracle_statuses(head)] == ["failure"]
    assert [item["state"] for item in harness.github.gate_statuses(head)] == ["failure"]
    assert "latest verdict" in harness.github.gate_statuses(head)[0]["description"]


def test_an_open_oracle_thread_does_not_hold_the_gate_the_verdict_run_posts(harness: Harness) -> None:
    head = harness.branch("feat/checker", {CHECKER: FIXED_CHECKER}, "guard None operands")
    harness.github.open_pull(43, "feat/checker")
    harness.github.threads[43].append(
        {
            "id": "PRRT_open",
            "resolved": False,
            "path": CHECKER,
            "comments": [{"author": ORACLE_BOT, "association": "NONE", "body": "An earlier finding."}],
        }
    )
    harness.script_omp([_ship()])

    harness.review(43)

    assert [item["state"] for item in harness.github.gate_statuses(head)] == ["success"]


@pytest.mark.parametrize("held", [False, True], ids=["still-green", "verdict-gone"])
def test_a_head_change_marks_the_gate_pending_before_it_is_evaluated(harness: Harness, held: bool) -> None:
    head = harness.branch("feat/checker", {CHECKER: FIXED_CHECKER}, "guard None operands")
    harness.github.open_pull(44, "feat/checker")
    harness.script_omp([_ship()])
    harness.review(44)
    assert [item["state"] for item in harness.github.gate_statuses(head)] == ["success"]
    if held:
        harness.github.statuses[head] = [
            item for item in harness.github.statuses[head] if item["context"] != "oracle-verdict"
        ]

    gate = harness.gate(44, head, pending_first=True)

    assert gate.code in (0, 3), gate.stderr
    assert [item["state"] for item in harness.github.gate_statuses(head)] == [
        "success",
        "pending",
        "failure" if held else "success",
    ]


def test_a_failed_review_post_leaves_no_verdict_status_so_the_sweep_retries_the_head(harness: Harness) -> None:
    head = harness.branch("feat/checker", {CHECKER: FIXED_CHECKER}, "guard None operands")
    harness.github.open_pull(45, "feat/checker")
    harness.script_omp([_ship()])
    harness.github.fail("POST", r"/pulls/45/reviews", 500, 1)

    run = harness.review(45)
    harness.github.failures.clear()

    assert run.steps["publish"].code == 1
    assert harness.github.oracle_statuses(head) == []
    assert harness.github.gate_statuses(head) == []
    assert harness.github.reviews[45] == []
    harness.github.complete_run(run.run, "failure", ("Post the result",))
    harness.github.age(run.run, 40)

    sweep = harness.sweep()

    assert sweep.code == 0, sweep.stderr
    assert harness.github.dispatches[-1:] == [
        {
            "workflow": "oracle-verdict.yml",
            "ref": "develop",
            "inputs": {"pr": "45", "head": head, "reason": "stateless-retry"},
        }
    ], sweep.outputs["summary"]


def test_the_verdict_status_is_posted_after_the_review_it_is_a_receipt_for(harness: Harness) -> None:
    head = harness.branch("feat/checker", {CHECKER: FIXED_CHECKER}, "guard None operands")
    harness.github.open_pull(46, "feat/checker")
    harness.script_omp([_ship()])

    harness.review(46)

    review = harness.github.reviews[46][-1]
    status = harness.github.oracle_statuses(head)[-1]
    assert review["submitted_at"] <= status["created_at"]
    assert [item["state"] for item in harness.github.gate_statuses(head)] == ["success"]


def test_attempt_capture_keeps_the_redacted_event_stream(harness: Harness) -> None:
    harness.branch("feat/checker", {CHECKER: FIXED_CHECKER}, "guard None operands")
    harness.github.open_pull(42, "feat/checker")
    harness.script_omp([_ship()], leak_env="OMP_GATEWAY_TOKEN")

    run = harness.review(42)

    assert run.steps["finalize"].code == 0, run.steps["finalize"].stderr
    [capture] = run.captures()
    text = json.dumps(capture)
    assert GATEWAY_TOKEN not in text and JOB_TOKEN not in text
    assert "[redacted]" in capture["stderr_excerpt"]
    assert capture["exit_code"] == 0 and capture["slot"] == 1 and capture["absence"] is None
    final = [event for event in capture["events"] if isinstance(event, dict) and event.get("type") == "message_end"][-1]
    answer = json.loads(final["message"]["content"][-1]["text"])
    assert answer["decision"] == "SHIP"
    recorded = [line for line in RECORDED_STREAM.read_text(encoding="utf-8").splitlines() if '"message_update"' in line]
    assert not any(isinstance(event, dict) and event["type"].endswith("_update") for event in capture["events"])
    assert 0 < capture["streaming_events_dropped"] <= len(recorded)
    tool_results = [event["result"] for event in capture["events"] if event["type"] == "tool_execution_end"]
    assert tool_results and all(item.startswith("[tool output withheld: ") for item in tool_results)
    uploaded = [item["name"] for item in harness.github.artifacts if item["workflow_run"]["id"] == run.run["id"]]
    assert "oracle-verdict-capture" in uploaded


@pytest.mark.parametrize("damage", [b"{}", b"[]"], ids=["empty-object", "array"])
def test_an_unreadable_attempt_artifact_finalizes_pending_for_integrity(harness: Harness, damage: bytes) -> None:
    head = harness.branch("feat/checker", {CHECKER: FIXED_CHECKER}, "guard None operands")
    harness.github.open_pull(43, "feat/checker")
    harness.script_omp([_ship()])

    def damage_artifact(attempts_dir: Path) -> None:
        for path in attempts_dir.glob("attempt-*.json"):
            path.write_bytes(damage)

    run = harness.review(43, tamper_attempts=damage_artifact)

    finalize = run.steps["finalize"]
    assert finalize.code == 0, finalize.stderr
    assert finalize.outputs["state"] == "pending"
    assert harness.github.artifact_json("oracle-verdict-state-43", "state.json")["pending_cause"] == "integrity"
    assert [item["state"] for item in harness.github.oracle_statuses(head)] == ["pending"]


def test_a_second_pull_request_at_the_same_head_does_not_strand_the_first(harness: Harness) -> None:
    head = harness.branch("feat/shared", {CHECKER: FIXED_CHECKER}, "guard None operands")
    harness.github.open_pull(61, "feat/shared")
    harness.github.open_pull(62, "feat/shared")
    harness.script_omp([_ship()])
    harness.review(61)
    assert harness.gate(61, head).code == 0
    harness.script_omp([_ship()])
    harness.review(62)

    assert len(harness.github.oracle_statuses(head)) == 2
    first = harness.gate(61, head)
    second = harness.gate(62, head)

    assert first.code == 0, first.stderr
    assert second.code == 0, second.stderr


@pytest.mark.parametrize("damage", [b"{not json", b"\xff\xfe\x00 not utf-8"], ids=["corrupt-json", "non-utf8"])
def test_an_attempt_artifact_that_cannot_be_redaction_checked_fails_the_finalize_step_and_posts_nothing(
    harness: Harness, damage: bytes
) -> None:
    head = harness.branch("feat/checker", {CHECKER: FIXED_CHECKER}, "guard None operands")
    harness.github.open_pull(44, "feat/checker")
    harness.script_omp([_ship()])

    def damage_artifact(attempts_dir: Path) -> None:
        for path in attempts_dir.glob("attempt-*.json"):
            path.write_bytes(damage)

    run = harness.review(44, tamper_attempts=damage_artifact)

    assert run.steps["finalize"].code == 1
    assert "publish" not in run.steps
    assert harness.github.oracle_statuses(head) == []
