from __future__ import annotations

import pytest

from _project.scripts.oracle_reviewers import omp_bump, omp_pin

from .harness import CHECKER, Harness

pytestmark = [pytest.mark.integration, pytest.mark.medium]

FIXED_CHECKER = "def checker(a, b):\n    if a is None or b is None:\n        return False\n    return a == b\n"
SHIP = {"decision": "SHIP", "summary": "No defects.", "files_examined": [CHECKER]}


def _open(harness: Harness, pr: int) -> str:
    head = harness.branch(f"feat/pin-{pr}", {CHECKER: FIXED_CHECKER}, "guard None operands")
    harness.github.open_pull(pr, f"feat/pin-{pr}")
    harness.script_omp([SHIP])
    return head


def test_a_failed_install_posts_a_distinct_pending_status_runs_no_attempt_and_is_not_redispatched(
    harness: Harness,
) -> None:
    head = _open(harness, 71)

    run = harness.review(71, installable=False)

    assert run.steps["install"].code == 0
    assert run.steps["install"].outputs["state"] == "failed"
    assert "the omp download failed" in run.steps["install"].stderr
    assert run.steps["install"].outputs["env"] == ""
    assert "attempts" not in run.steps
    assert run.attempts() == [] and harness.omp_calls() == 0
    [status] = harness.github.oracle_statuses(head)
    assert status["state"] == "pending"
    assert status["description"] == "the pinned omp build could not be installed; no reviewer ran"
    assert harness.github.artifact_json("oracle-verdict-state-71", "state.json")["pending_cause"] == "omp-install"

    harness.github.age(run.run, 600)
    before = list(harness.github.dispatches)
    sweep = harness.sweep()

    assert sweep.code == 0, sweep.stderr
    assert harness.github.dispatches == before
    assert harness.omp_calls() == 0


def test_a_binary_swapped_after_the_install_is_refused_before_any_review_runs(harness: Harness) -> None:
    head = _open(harness, 72)

    run = harness.review(72, swap_after_install=True)

    assert run.steps["install"].outputs["state"] == "installed"
    attempts = run.attempts()
    assert attempts
    for attempt in attempts:
        assert attempt["outcome"] == "absent" and attempt["absence"]["kind"] == "error"
        assert "does not match the pinned sha256" in attempt["absence"]["detail"]
    assert harness.omp_calls() == 0
    assert [item["state"] for item in harness.github.oracle_statuses(head)] == ["pending"]


def test_a_pin_with_a_path_character_build_key_stops_the_plan(harness: Harness) -> None:
    _open(harness, 73)
    text = harness.policy_file.read_text(encoding="utf-8")
    assert "    linux-x64:" in text
    harness.policy_file.write_text(text.replace("    linux-x64:", "    ../escape-x64:"), encoding="utf-8")

    run = harness.review(73)

    assert run.steps["plan"].code != 0
    assert "not a platform name" in run.steps["plan"].stderr
    assert harness.github.oracle_statuses(harness.github.pulls[73]["head"]["sha"]) == []
    assert harness.omp_calls() == 0


BUMP_VERSION = "18.99.0"
BUMP_BRANCH = f"chore/omp-pin-{BUMP_VERSION}"
VERDICT = "oracle-verdict.yml"


def _bumped_policy(harness: Harness) -> str:
    text = harness.policy_file.read_text(encoding="utf-8")
    pin = omp_pin.pin_from_text(text)
    digests = {target: f"{index:02x}" * 32 for index, target in enumerate(sorted(pin.builds), start=10)}
    bumped = omp_pin.Pin(BUMP_VERSION, {t: omp_pin.Build(b.url, digests[t]) for t, b in pin.builds.items()})
    return omp_bump.replace_pin(text, bumped)


def test_a_new_bump_pull_request_is_opened_and_its_review_is_dispatched_for_its_head(harness: Harness) -> None:
    opened = harness.open_bump_pull(BUMP_VERSION, _bumped_policy(harness))

    assert opened.code == 0, opened.stdout + opened.stderr
    [pull] = harness.github.pulls.values()
    assert pull["title"] == f"chore(oracle): pin omp {BUMP_VERSION}"
    assert pull["head"]["ref"] == BUMP_BRANCH
    assert harness.github.dispatches == [
        {"workflow": VERDICT, "ref": "develop", "inputs": {"pr": str(pull["number"]), "head": pull["head"]["sha"]}}
    ]


def test_a_rerun_reviews_a_bump_head_only_once_and_reviews_a_moved_head(harness: Harness) -> None:
    policy = _bumped_policy(harness)
    harness.open_bump_pull(BUMP_VERSION, policy)
    [pull] = harness.github.pulls.values()
    number, first = pull["number"], pull["head"]["sha"]
    harness.github.add_run(VERDICT, event="workflow_dispatch", pr=number, head_sha="d" * 40, title_head=first)

    settled = harness.open_bump_pull(BUMP_VERSION, policy)

    assert settled.code == 0, settled.stdout + settled.stderr
    assert "already has a review" in settled.stdout
    assert len(harness.github.dispatches) == 1
    assert len(harness.github.pulls) == 1

    moved = harness.push(BUMP_BRANCH, {"NOTES.md": "adjusted\n"}, "retune the bump")
    again = harness.open_bump_pull(BUMP_VERSION, policy)

    assert again.code == 0, again.stdout + again.stderr
    assert [item["inputs"]["head"] for item in harness.github.dispatches] == [first, moved]
    assert len(harness.github.pulls) == 1


def test_a_closed_bump_pull_request_is_neither_reopened_nor_reviewed(harness: Harness) -> None:
    policy = _bumped_policy(harness)
    harness.open_bump_pull(BUMP_VERSION, policy)
    [pull] = harness.github.pulls.values()
    pull["state"] = "closed"
    harness.github.dispatches.clear()

    rerun = harness.open_bump_pull(BUMP_VERSION, policy)

    assert rerun.code == 0, rerun.stdout + rerun.stderr
    assert "closed pull request" in rerun.stdout
    assert harness.github.dispatches == []
    assert len(harness.github.pulls) == 1


def test_a_bump_head_that_already_has_a_verdict_status_is_not_re_reviewed_after_its_run_leaves_the_listing(
    harness: Harness,
) -> None:
    policy = _bumped_policy(harness)
    harness.open_bump_pull(BUMP_VERSION, policy)
    [pull] = harness.github.pulls.values()
    head = pull["head"]["sha"]
    harness.github.dispatches.clear()
    harness.github.statuses[head].append(
        {"id": 1, "state": "success", "context": "oracle-verdict", "description": "SHIP", "creator": {"login": "bot"}}
    )

    rerun = harness.open_bump_pull(BUMP_VERSION, policy)

    assert rerun.code == 0, rerun.stdout + rerun.stderr
    assert "already has a review" in rerun.stdout
    assert harness.github.dispatches == []
