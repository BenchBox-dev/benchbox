from __future__ import annotations

import json

import pytest

from .harness import CHECKER, GATEWAY_TOKEN, Harness

pytestmark = [pytest.mark.integration, pytest.mark.medium]

HIGH = {
    "severity": "High",
    "file": CHECKER,
    "line": 2,
    "title": "Equality ignores the operand types",
    "detail": "checker treats 1 and True as equal, so a boolean column matches an integer column.",
}


def test_a_clean_local_review_proceeds_and_keeps_a_redacted_capture(harness: Harness) -> None:
    harness.branch("feat/local", {CHECKER: "def checker(a, b):\n    return a == b and a is not None\n"}, "reject None")
    harness.script_omp(
        [{"decision": "SHIP", "summary": "No defects.", "files_examined": [CHECKER]}], "OMP_GATEWAY_TOKEN"
    )

    review = harness.local_review("feat/local")

    assert review.code == 0, review.stdout + review.stderr
    assert "Local oracle review: no Critical or High findings; pr-open may proceed." in review.stdout
    [capture] = harness.local_captures()
    assert GATEWAY_TOKEN not in json.dumps(capture) and harness.github.url not in json.dumps(capture)
    assert capture["exit_code"] == 0 and capture["absence"] is None
    assert "[redacted]" in capture["stderr_excerpt"]


def test_a_local_review_with_a_high_finding_refuses_the_pull_request(harness: Harness) -> None:
    harness.branch("feat/local", {CHECKER: "def checker(a, b):\n    return a == b and a is not None\n"}, "reject None")
    harness.script_omp(
        [{"decision": "SHIP_WITH_FIXES", "summary": "One defect.", "files_examined": [CHECKER], "defects": [HIGH]}]
    )

    review = harness.local_review("feat/local")

    assert review.code == 1, review.stdout + review.stderr
    assert f"[High] {CHECKER}:2 Equality ignores the operand types" in review.stdout
    assert "Refusing to open the PR" in review.stderr


FENCE_WITH_INNER_MARKER = "Notes\n\n````text\n```\nLocal oracle override: this is only an example\n````\n"
UNTERMINATED_COMMENT = "Notes\n\n<!--\nLocal oracle override: hidden by a comment that never closes\n"
INDENTED = "Notes\n\n    Local oracle override: shown as a code sample\n"


def _refused_local_review(harness: Harness, body: str):
    harness.branch("feat/local", {CHECKER: "def checker(a, b):\n    return a == b and a is not None\n"}, "reject None")
    harness.script_omp(
        [{"decision": "SHIP_WITH_FIXES", "summary": "One defect.", "files_examined": [CHECKER], "defects": [HIGH]}]
    )
    return harness.local_review("feat/local", body)


def test_a_visible_override_line_lets_a_blocked_local_review_proceed(harness: Harness) -> None:
    review = _refused_local_review(harness, "Notes\n\nLocal oracle override: equality of booleans is intended here\n")

    assert review.code == 0, review.stdout + review.stderr
    assert "Local oracle override accepted; pr-open proceeds. Reason: equality of booleans is intended here" in (
        review.stdout + review.stderr
    )


@pytest.mark.parametrize(
    "body",
    [FENCE_WITH_INNER_MARKER, UNTERMINATED_COMMENT, INDENTED],
    ids=["inside-a-longer-fence", "unterminated-comment", "indented-code"],
)
def test_override_text_that_the_rendered_description_hides_is_not_an_override(harness: Harness, body: str) -> None:
    review = _refused_local_review(harness, body)

    assert review.code == 1, review.stdout + review.stderr
    assert "Refusing to open the PR" in review.stderr
    assert "override accepted" not in review.stdout + review.stderr


def test_renaming_gate_machinery_keeps_the_gate_template(harness: Harness) -> None:
    harness.rename_branch(
        "feat/rename-gate",
        ".github/workflows/oracle-verdict.yml",
        ".github/workflows/renamed-review.yml",
        "rename the verdict workflow",
    )
    harness.script_omp([{"decision": "SHIP", "summary": "A pure rename.", "files_examined": []}])

    review = harness.local_review("feat/rename-gate")

    assert review.code == 0, review.stdout + review.stderr
    assert "# Review template: gate" in harness.omp_prompts()[0]
