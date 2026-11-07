from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.unit.scripts.batch_loop_world import BATCH, MEMBERS, World, bi, build_world, git

pytestmark = [pytest.mark.unit, pytest.mark.medium]


def _ledger(integration: Path) -> dict:
    return json.loads((integration / ".todo-batch" / BATCH / "ledger.json").read_text(encoding="utf-8"))


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    return build_world(tmp_path, monkeypatch)


def _raises(match: str):
    return pytest.raises(bi.BatchError, match=match)


def _ready_for_evidence(world: World) -> Path:
    integration = world.start()
    world.deliver("zeta", {"src/a.py": "zeta change\n"})
    world.land_and_prepare("zeta")
    world.deliver("alpha", {"src/a.py": "alpha change\n"})
    world.land_and_prepare("alpha")
    world.deliver("mid", {"config/app.yaml": "k: v\n"}, tests=["tests/test_mid.py"])
    world.land_and_prepare("mid")
    (world.seed / "other").mkdir(exist_ok=True)
    (world.seed / "other" / "dev.txt").write_text("develop moved\n")
    git(world.seed, "add", "other/dev.txt")
    git(world.seed, "commit", "-qm", "develop moves")
    git(world.seed, "push", "-q", "origin", "develop")
    advanced = bi.advance_head(integration, "develop-merge")
    world.tracker.advance(advanced["payload"]["arguments"])
    world.tracker.bind(world.head())
    return integration


def test_evidence_reruns_required_members_at_the_final_head_and_writes_the_contract(world: World) -> None:
    integration = _ready_for_evidence(world)
    final = world.head()
    world.calls.clear()
    built = bi.build_evidence(integration)
    evidence = json.loads(Path(built["path"]).read_text(encoding="utf-8"))
    assert Path(built["path"]) == integration / ".todo-batch" / BATCH / "final-evidence.json"
    assert evidence["schema"] == "benchbox_batch_evidence_v2" and evidence["final_head"] == final
    assert evidence["integration_branch"] == f"feat/{BATCH}" and evidence["integration_worktree"] == str(integration)
    assert evidence["repository"] == "owner/name" and evidence["tracker_rev"] == str(world.tracker.rev)
    assert set(evidence["current_acceptance"]) == set(MEMBERS)
    for member in MEMBERS:
        assert evidence["current_acceptance"][member] == {
            "status": "passed",
            "revision": final,
            "clean": True,
            "suite": world.tracker.receipts[member]["verification"]["suite"],
            "command": world.tracker.receipts[member]["verification"]["command"],
            "ran_command": world.tracker.receipts[member]["verification"]["command"],
            "dropped_paths": [],
            "worktree": str(integration),
        }
    executed = [call for call in world.calls if "pytest" in call[0]]
    assert executed == [(world.tracker.receipts[m]["verification"]["command"], integration) for m in MEMBERS]
    assert evidence["changed_files"] == ["config/app.yaml", "src/a.py"]
    assert set(evidence["member_reviews"]) == set(MEMBERS)
    assert evidence["tracker"]["receipts"].keys() == world.tracker.receipts.keys()
    finishes = built["finish_payloads"]
    assert [item["arguments"]["id"] for item in finishes] == MEMBERS
    final_evidence = finishes[0]["arguments"]["final_evidence"]
    assert final_evidence["current_acceptance"] == evidence["current_acceptance"]
    assert final_evidence["final_pr"] == world.tracker.final_pr and final_evidence["tree_revision"] == final
    assert final_evidence["member_ranges"]["alpha"] == {
        "base": world.tracker.receipts["alpha"]["source_base"],
        "head": world.tracker.receipts["alpha"]["accepted_head"],
    }


def test_evidence_refuses_when_a_required_rerun_fails_even_if_an_older_run_passed(world: World) -> None:
    integration = _ready_for_evidence(world)
    ledger = _ledger(integration)
    assert ledger["acceptance"]["zeta"][-1]["status"] == "passed"
    assert ledger["acceptance"]["zeta"][-1]["revision"] != world.head()
    world.failing_tests.add("tests/test_zeta.py")
    with _raises("rerun of 'zeta' failed"):
        bi.build_evidence(integration)
    assert not (integration / ".todo-batch" / BATCH / "final-evidence.json").exists()


def test_a_second_evidence_invocation_executes_every_member_check_again(world: World) -> None:
    integration = _ready_for_evidence(world)
    bi.build_evidence(integration)
    world.calls.clear()
    bi.build_evidence(integration)
    assert len([call for call in world.calls if "pytest" in call[0]]) == len(MEMBERS)


def test_a_new_failed_attempt_leaves_no_usable_evidence_from_an_earlier_success(world: World) -> None:
    integration = _ready_for_evidence(world)
    bi.build_evidence(integration)
    world.failing_tests.add("tests/test_zeta.py")
    with _raises("rerun of 'zeta' failed"):
        bi.build_evidence(integration)
    assert not (integration / ".todo-batch" / BATCH / "final-evidence.json").exists()


def test_evidence_refuses_without_a_final_pr_bound_at_the_integration_head(world: World) -> None:
    integration = _ready_for_evidence(world)
    world.tracker.final_pr = None
    with _raises("final PR is not bound"):
        bi.build_evidence(integration)
    world.tracker.bind("0" * 40)
    with _raises("final PR is not bound"):
        bi.build_evidence(integration)


def test_evidence_refuses_before_any_rerun_when_the_final_change_is_not_reviewable(world: World) -> None:
    integration = _ready_for_evidence(world)
    reason = "needs about 900000 tokens of reading, above every reviewer's capacity; split"
    world.preflight_code = 3
    world.preflight_reasons = [reason]
    world.preflight_heads.clear()
    world.calls.clear()
    with _raises(f"is not reviewable:\n{reason}"):
        bi.build_evidence(integration)
    assert world.preflight_heads == [world.head()]
    assert not [call for call in world.calls if "pytest" in call[0]]


@pytest.mark.parametrize("defect", ["missing", "findings"])
def test_evidence_refuses_when_a_receipt_holds_no_accepted_review(world: World, defect: str) -> None:
    integration = _ready_for_evidence(world)
    verification = world.tracker.receipts["mid"]["verification"]
    if defect == "missing":
        del verification["review"]
    else:
        verification["review"] = {**verification["review"], "outcome": "findings"}
    with _raises("receipt has no accepted review record"):
        bi.build_evidence(integration)


def test_evidence_refuses_when_a_member_is_not_prepared(world: World) -> None:
    integration = _ready_for_evidence(world)
    del world.tracker.receipts["mid"]
    with _raises("lacks a prepared_work_v2 receipt"):
        bi.build_evidence(integration)
