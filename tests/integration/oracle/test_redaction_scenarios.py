from __future__ import annotations

import json
import re

import pytest

from _project.scripts.oracle_reviewers import protocol

from .harness import CHECKER, HELD_SECRET, Harness

pytestmark = [pytest.mark.integration, pytest.mark.medium]

FIRST = "def checker(a, b):\n    return a is b\n"
QUOTING_DEFECT = {
    "severity": "High",
    "file": CHECKER,
    "line": 2,
    "title": f"Identity comparison replaces equality, see {HELD_SECRET}",
    "detail": "checker returns a is b, so equal values held in distinct objects compare unequal.",
}


def _decoded(body: str) -> str:
    return json.dumps(protocol.decode_marker(body), sort_keys=True)


def test_a_secret_quoted_in_a_finding_title_reaches_neither_the_posted_review_nor_its_marker_nor_the_summary(
    harness: Harness,
) -> None:
    harness.branch("feat/checker", {CHECKER: FIRST}, "compare by identity")
    harness.github.open_pull(61, "feat/checker")
    harness.script_omp(
        [
            {
                "decision": "SHIP_WITH_FIXES",
                "summary": f"One defect; the reviewer saw {HELD_SECRET}.",
                "files_examined": [CHECKER],
                "defects": [QUOTING_DEFECT],
            }
        ]
    )

    run = harness.review(61)

    body = harness.github.reviews[61][-1]["body"]
    marker = _decoded(body)
    assert run.steps["redact"].code == 0, run.steps["redact"].stderr
    assert "Identity comparison replaces equality" in marker
    assert HELD_SECRET not in marker
    assert HELD_SECRET not in body
    assert HELD_SECRET not in run.step_summary()
    assert protocol.MARKER_OPEN in run.step_summary()
    assert protocol.decode_marker(body)["open_defects"][0]["id"] == "D1"


def test_the_redaction_step_fails_the_job_when_a_decoded_marker_still_holds_a_secret(harness: Harness) -> None:
    harness.branch("feat/checker", {CHECKER: FIRST}, "compare by identity")
    harness.github.open_pull(62, "feat/checker")
    harness.script_omp(
        [
            {
                "decision": "SHIP_WITH_FIXES",
                "summary": "One defect.",
                "files_examined": [CHECKER],
                "defects": [QUOTING_DEFECT],
            }
        ]
    )
    plan_run = harness.review(62)
    final = plan_run.temp / "oracle-final"
    review = json.loads((final / "review.json").read_text(encoding="utf-8"))
    record = protocol.decode_marker(review["body"])
    record["open_defects"][0]["title"] = f"see {HELD_SECRET}"
    forged = protocol.encode_marker(record)
    review["body"] = review["body"][: review["body"].rindex(protocol.MARKER_OPEN)] + forged
    (final / "review.json").write_text(json.dumps(review), encoding="utf-8")
    (plan_run.temp / "summary.md").unlink(missing_ok=True)

    step = harness.cli(
        "redact",
        ["redact-check", "--path", str(final), "--summary-file", str(final / "summary.json")],
        {**harness.base_env(plan_run.temp), "TS_OAUTH_CI_SECRET": HELD_SECRET},
    )

    assert step.code == 1
    assert "a secret value remains" in step.stderr
    assert HELD_SECRET not in step.stdout + step.stderr
    assert plan_run.step_summary() == ""


def test_no_loop_time_summary_line_reaches_the_step_summary_before_the_redaction_step(harness: Harness) -> None:
    harness.branch("feat/checker", {CHECKER: FIRST}, "compare by identity")
    harness.github.open_pull(63, "feat/checker")
    harness.script_omp(
        [
            {
                "decision": "SHIP_WITH_FIXES",
                "summary": "One defect.",
                "files_examined": [CHECKER],
                "defects": [QUOTING_DEFECT],
            }
        ]
    )

    run = harness.review(63)

    pre_loop = [line for line in run.loop_summary.splitlines() if line]
    assert pre_loop[0].startswith("oracle-verdict plan for #63:")
    assert all(re.fullmatch(r"\d+(\.\d+)*", line) for line in pre_loop[1:])
    assert "slot 1:" in run.step_summary()
    assert "oracle-verdict #63" in run.step_summary()
