from __future__ import annotations

import pytest

from .harness import CHECKER, Harness, git

pytestmark = [pytest.mark.integration, pytest.mark.medium]

BASE = "".join(f"# line {number}\n" for number in range(1, 13)) + "def checker(a, b):\n    return a == b\n"
EDITED = BASE.replace("# line 3\n", "# line 3, tolerance documented\n")
LATER = BASE.replace("# line 11\n", "# line 11, reference note\n")
SHIP = {"decision": "SHIP", "summary": "The change keeps checker semantics; no defects.", "files_examined": [CHECKER]}


def _rebase(harness: Harness, branch: str) -> str:
    git(harness.repo, "switch", "--quiet", branch)
    git(harness.repo, "rebase", "--quiet", "develop")
    head = git(harness.repo, "rev-parse", "HEAD")
    git(harness.repo, "switch", "--quiet", "develop")
    return head


def _reviewed(harness: Harness, pr: int, branch: str) -> str:
    harness.write({CHECKER: BASE}, "document the checker")
    first = harness.branch(branch, {CHECKER: EDITED}, "document the tolerance")
    harness.github.open_pull(pr, branch)
    harness.script_omp([SHIP])
    run = harness.review(pr)
    assert [item["state"] for item in harness.github.oracle_statuses(first)] == ["success"]
    assert run.plan["decision"] == "review"
    return first


def test_a_rebase_with_an_identical_merge_base_diff_carries_the_decision(harness: Harness) -> None:
    first = _reviewed(harness, 71, "feat/identical")
    harness.write({"README.md": "seed\nunrelated\n"}, "touch readme")
    head = _rebase(harness, "feat/identical")
    assert head != first

    run = harness.review(71)

    assert run.plan["decision"] == "carry", run.plan["decision_reason"]
    assert harness.omp_calls() == 1
    [status] = harness.github.oracle_statuses(head)
    assert status["state"] == "success" and status["description"].startswith(f"carried from {first[:12]}: SHIP")
    assert f"carried from head `{first}`" in harness.github.reviews[71][-1]["body"]


def test_a_rebase_onto_a_develop_that_changed_the_same_file_is_reviewed_again(harness: Harness) -> None:
    first = _reviewed(harness, 72, "feat/relocated")
    harness.write({CHECKER: LATER}, "annotate the reference")
    head = _rebase(harness, "feat/relocated")
    assert head != first

    run = harness.review(72)

    assert run.plan["decision"] == "review", run.plan["decision_reason"]
    assert harness.omp_calls() == 2
    [status] = harness.github.oracle_statuses(head)
    assert status["state"] == "success" and not status["description"].startswith("carried")


def test_an_edited_verdict_review_holds_instead_of_carrying_a_pass(harness: Harness) -> None:
    first = _reviewed(harness, 73, "feat/edited")
    review = harness.github.reviews[73][-1]
    harness.github.edit_review(
        73, len(harness.github.reviews[73]) - 1, review["body"].replace("SHIP", "SHIP (approved)")
    )
    harness.write({"README.md": "seed\nunrelated\n"}, "touch readme")
    head = _rebase(harness, "feat/edited")
    assert head != first

    run = harness.review(73)

    assert run.plan["decision"] == "hold", run.plan["decision_reason"]
    assert "edited" in run.plan["decision_reason"]
    assert harness.omp_calls() == 1
    assert [(item["state"], "carried" in item["description"]) for item in harness.github.oracle_statuses(head)] == [
        ("pending", False)
    ]
    assert len(harness.github.reviews[73]) == 1


def test_an_edited_verdict_review_holds_a_changed_patch_instead_of_reviewing_it(harness: Harness) -> None:
    _reviewed(harness, 74, "feat/edited-push")
    harness.github.edit_review(74, 0, harness.github.reviews[74][0]["body"] + "\nApproved by the owner.\n")
    head = harness.push("feat/edited-push", {CHECKER: EDITED + "# another note\n"}, "extend the note")

    run = harness.review(74)

    assert run.plan["decision"] == "hold", run.plan["decision_reason"]
    assert harness.omp_calls() == 1
    assert [item["state"] for item in harness.github.oracle_statuses(head)] == ["pending"]
