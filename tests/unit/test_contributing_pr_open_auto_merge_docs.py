from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRIBUTING = REPO_ROOT / "CONTRIBUTING.md"


@pytest.fixture(scope="module")
def contributing_text() -> str:
    return CONTRIBUTING.read_text(encoding="utf-8")


def test_contributing_does_not_claim_pr_open_arms_auto_merge(contributing_text: str) -> None:
    assert "merge --auto --squash" not in contributing_text, (
        "CONTRIBUTING still documents gh pr merge --auto --squash; pr-open withholds"
    )
    assert "open the PR with auto-merge in one shot" not in contributing_text


def test_contributing_documents_arming_the_exact_head_and_monitoring_to_merge(contributing_text: str) -> None:
    contributing_text = " ".join(contributing_text.split())
    for required in (
        "make pr-arm",
        "make pr-landing-withdraw",
        "refuses on a hold label",
        "monitor to merge",
        "You are done when the PR is merged",
        "[WRITE-CLOSEOUT-001]",
    ):
        assert required in contributing_text, f"CONTRIBUTING missing cue {required!r}"
    assert "blocks arming only" in contributing_text
    assert "gh pr merge <n> --disable-auto" in contributing_text
    assert "auto-merge-on-open" not in contributing_text


def test_contributing_does_not_teach_holding_a_finished_pr(contributing_text: str) -> None:
    lowered = contributing_text.lower()
    for forbidden in ("withhold", "remains held", "stays held", "when the branch is final", "marked final"):
        assert forbidden not in lowered, f"CONTRIBUTING still teaches holding a finished PR: {forbidden!r}"


