from __future__ import annotations

import json

import pytest

from .fake_github import JOB_TOKEN
from .harness import CHECKER, GATEWAY_TOKEN, RECORDED_STREAM, Harness

pytestmark = [pytest.mark.integration, pytest.mark.medium]

FIXED_CHECKER = "def checker(a, b):\n    if a is None or b is None:\n        return False\n    return a == b\n"


def _ship(summary: str = "The change keeps checker semantics; no defects.") -> dict:
    return {"decision": "SHIP", "summary": summary, "files_examined": [CHECKER]}


def test_first_review_ships_posts_success_and_the_gate_passes(harness: Harness) -> None:
    head = harness.branch("feat/checker", {CHECKER: FIXED_CHECKER}, "guard None operands")
    harness.github.open_pull(41, "feat/checker")
    harness.script_omp([_ship()], leak_env="OMP_GATEWAY_TOKEN")

    run = harness.review(41)

    assert run.steps["plan"].outputs["decision"] == "review"
    assert [item["outcome"] for item in run.attempts()] == ["verdict"]
    verdicts = harness.github.oracle_statuses(head)
    assert [(item["state"], item["creator"]["login"]) for item in verdicts] == [("success", "benchbox-oracle[bot]")]
    review = harness.github.reviews[41][-1]
    assert review["commit_id"] == head and f"### oracle-verdict: success for `{head}`" in review["body"]
    assert harness.github.dispatches == [
        {"workflow": "oracle-gate.yml", "ref": "develop", "inputs": {"pr": "41", "head_sha": head}}
    ]

    gate = harness.gate(41, head)

    assert gate.code == 0, gate.stderr
    assert [item["state"] for item in harness.github.gate_statuses(head)] == ["success"]


def test_attempt_capture_keeps_the_redacted_event_stream(harness: Harness) -> None:
    harness.branch("feat/checker", {CHECKER: FIXED_CHECKER}, "guard None operands")
    harness.github.open_pull(42, "feat/checker")
    harness.script_omp([_ship()], leak_env="OMP_GATEWAY_TOKEN")

    run = harness.review(42)

    assert run.steps["redact"].code == 0, run.steps["redact"].stderr
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
