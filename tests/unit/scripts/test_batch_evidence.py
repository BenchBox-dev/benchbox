from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import pr_landing
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


LEGACY_TEST = "tests/test_legacy.py"


def _develop_commit(world: World, message: str, add: dict[str, str] | None = None, remove: list[str] | None = None):
    for name, text in (add or {}).items():
        (world.seed / name).parent.mkdir(parents=True, exist_ok=True)
        (world.seed / name).write_text(text)
        git(world.seed, "add", name)
    for name in remove or []:
        git(world.seed, "rm", "-q", name)
    git(world.seed, "commit", "-qm", message)
    git(world.seed, "push", "-q", "origin", "develop")


def _legacy_batch(world: World) -> Path:
    _develop_commit(world, "legacy test", add={LEGACY_TEST: "def test_l():\n    assert True\n"})
    integration = world.start()
    files = {"src/a.py": "zeta change\n"}
    world.deliver("zeta", files, tests=["tests/test_zeta.py", LEGACY_TEST])
    world.land_and_prepare("zeta")
    world.deliver("alpha", {"src/b.py": "alpha change\n"})
    world.land_and_prepare("alpha")
    world.deliver("mid", {"config/app.yaml": "k: v\n"}, tests=["tests/test_mid.py"])
    world.land_and_prepare("mid")
    return integration


def _merge_develop(world: World, integration: Path) -> None:
    advanced = bi.advance_head(integration, "develop-merge")
    world.tracker.advance(advanced["payload"]["arguments"])
    world.tracker.bind(world.head())


def test_evidence_drops_a_receipt_test_that_a_recorded_develop_merge_deleted(world: World) -> None:
    integration = _legacy_batch(world)
    _develop_commit(world, "develop retires the legacy test", remove=[LEGACY_TEST])
    _merge_develop(world, integration)
    assert LEGACY_TEST in world.tracker.receipts["zeta"]["verification"]["command"]
    world.calls.clear()

    built = bi.build_evidence(integration)

    reruns = {tuple(call[0]) for call in world.calls if "pytest" in call[0]}
    zeta_receipt = world.tracker.receipts["zeta"]["verification"]["command"]
    assert tuple(zeta_receipt) not in reruns
    assert tuple(arg for arg in zeta_receipt if arg != LEGACY_TEST) in reruns
    assert built["evidence"]["dropped_receipt_paths"] == {"zeta": [LEGACY_TEST]}
    assert built["evidence"]["current_acceptance"]["zeta"]["command"] == zeta_receipt
    assert _ledger(integration)["acceptance"]["zeta"][-1]["dropped_paths"] == [LEGACY_TEST]
    tracker = _tracker_view(world)
    assert (
        pr_landing._check_evidence_dropped_paths(
            integration, built["evidence"], world.head(), tracker, tracker["receipts"]
        )
        == []
    )


def test_landing_refuses_dropped_paths_that_no_develop_merge_removed(world: World) -> None:
    integration = _legacy_batch(world)
    _develop_commit(world, "develop retires the legacy test", remove=[LEGACY_TEST])
    _merge_develop(world, integration)
    built = bi.build_evidence(integration)
    tracker = _tracker_view(world)
    forged = dict(built["evidence"], dropped_receipt_paths={"zeta": ["tests/test_zeta.py"]})
    failures = pr_landing._check_evidence_dropped_paths(integration, forged, world.head(), tracker, tracker["receipts"])
    assert any("tests/test_zeta.py" in item for item in failures)
    bogus = dict(built["evidence"], dropped_receipt_paths={"zeta": ["tests/test_other.py"]})
    failures = pr_landing._check_evidence_dropped_paths(integration, bogus, world.head(), tracker, tracker["receipts"])
    assert any("not named by member zeta" in item for item in failures)


def test_landing_refuses_evidence_that_omits_the_dropped_receipt_paths(world: World) -> None:
    integration = _legacy_batch(world)
    _develop_commit(world, "develop retires the legacy test", remove=[LEGACY_TEST])
    _merge_develop(world, integration)
    built = bi.build_evidence(integration)
    tracker = _tracker_view(world)
    for evidence in (
        {key: value for key, value in built["evidence"].items() if key != "dropped_receipt_paths"},
        dict(built["evidence"], dropped_receipt_paths={}),
    ):
        failures = pr_landing._check_evidence_dropped_paths(
            integration, evidence, world.head(), tracker, tracker["receipts"]
        )
        assert any("not listed as dropped" in item and LEGACY_TEST in item for item in failures)


def test_an_ignored_stale_copy_of_a_deleted_test_does_not_keep_it_in_the_rerun(world: World) -> None:
    integration = _legacy_batch(world)
    _develop_commit(world, "develop retires the legacy test", remove=[LEGACY_TEST])
    _merge_develop(world, integration)
    exclude = Path(git(integration, "rev-parse", "--path-format=absolute", "--git-path", "info/exclude"))
    exclude.parent.mkdir(parents=True, exist_ok=True)
    exclude.write_text(f"{LEGACY_TEST}\n", encoding="utf-8")
    (integration / LEGACY_TEST).write_text("def test_l():\n    assert True\n")
    assert git(integration, "status", "--porcelain") == ""

    built = bi.build_evidence(integration)

    assert built["evidence"]["dropped_receipt_paths"] == {"zeta": [LEGACY_TEST]}
    assert all(LEGACY_TEST not in call[0] for call in world.calls if "pytest" in call[0] and call[1] == integration)


def test_evidence_still_fails_for_a_test_path_a_member_owns(world: World) -> None:
    integration = _legacy_batch(world)
    _develop_commit(world, "develop retires the legacy test", remove=[LEGACY_TEST])
    _merge_develop(world, integration)
    world.tracker.receipts["zeta"]["changed_files"].append(LEGACY_TEST)
    with _raises("rerun of 'zeta' failed"):
        bi.build_evidence(integration)
    assert not (integration / ".todo-batch" / BATCH / "final-evidence.json").exists()


def test_evidence_refuses_when_a_develop_merge_removed_every_receipt_verification_path(world: World) -> None:
    _develop_commit(world, "legacy test", add={LEGACY_TEST: "def test_l():\n    assert True\n"})
    integration = world.start()
    world.deliver("zeta", {"src/a.py": "zeta change\n"}, tests=[LEGACY_TEST])
    world.land_and_prepare("zeta")
    world.deliver("alpha", {"src/b.py": "alpha change\n"})
    world.land_and_prepare("alpha")
    world.deliver("mid", {"config/app.yaml": "k: v\n"}, tests=["tests/test_mid.py"])
    world.land_and_prepare("mid")
    _develop_commit(world, "develop retires the legacy test", remove=[LEGACY_TEST])
    _merge_develop(world, integration)
    with _raises("every verification path in member 'zeta'"):
        bi.build_evidence(integration)


def test_a_develop_rename_of_a_receipt_test_is_not_a_plain_deletion(world: World) -> None:
    integration = _legacy_batch(world)
    git(world.seed, "mv", LEGACY_TEST, "tests/test_renamed.py")
    git(world.seed, "commit", "-qm", "develop renames the legacy test")
    git(world.seed, "push", "-q", "origin", "develop")
    _merge_develop(world, integration)
    tracker = _tracker_view(world)
    assert not pr_landing.develop_plainly_deleted(integration, tracker, LEGACY_TEST)
    forged = {"dropped_receipt_paths": {"zeta": [LEGACY_TEST]}}
    failures = pr_landing._check_evidence_dropped_paths(integration, forged, world.head(), tracker, tracker["receipts"])
    assert any("not plainly deleted" in item and LEGACY_TEST in item for item in failures)


def test_a_deleted_path_outside_the_trailing_test_run_is_never_dropped(world: World) -> None:
    integration = _legacy_batch(world)
    _develop_commit(world, "develop retires the legacy test", remove=[LEGACY_TEST])
    _merge_develop(world, integration)
    tracker = bi.read_tracker(integration, BATCH)
    excluded = bi._excluded_paths(integration, world.head(), tracker)
    command = ["uv", "run", "--", "python", "-m", "pytest", "-q", "--ignore", LEGACY_TEST, "tests/test_zeta.py"]
    kept, removed = bi._drop_develop_deleted_targets(integration, world.head(), command, excluded, tracker)
    assert kept == command
    assert removed == []


def _tracker_view(world: World) -> dict:
    payload = json.loads(world.tracker.show(["batch", "show", BATCH]).stdout)["data"]
    return {key: value for key, value in payload.items() if key != "sections"}


def _pytest_calls(world: World) -> list[list[str]]:
    return [call[0] for call in world.calls if "pytest" in call[0]]


def test_final_evidence_rows_record_the_recorded_command_the_ran_command_and_the_dropped_paths(world: World) -> None:
    integration = _legacy_batch(world)
    _develop_commit(world, "develop retires the legacy test", remove=[LEGACY_TEST])
    _merge_develop(world, integration)
    recorded = world.tracker.receipts["zeta"]["verification"]["command"]

    built = bi.build_evidence(integration)

    row = built["evidence"]["current_acceptance"]["zeta"]
    assert row["command"] == recorded
    assert row["ran_command"] == [arg for arg in recorded if arg != LEGACY_TEST]
    assert row["dropped_paths"] == [LEGACY_TEST]
    untouched = built["evidence"]["current_acceptance"]["alpha"]
    assert untouched["ran_command"] == untouched["command"] and untouched["dropped_paths"] == []
    entry = _ledger(integration)["acceptance"]["zeta"][-1]
    assert entry["command"] == recorded and entry["ran_command"] == row["ran_command"]
    tracker = _tracker_view(world)
    assert (
        pr_landing._check_evidence_acceptance(
            integration, built["evidence"], world.head(), tracker, tracker["receipts"]
        )
        == []
    )


def test_landing_refuses_an_acceptance_row_whose_ran_command_is_not_the_receipt_minus_the_dropped_paths(
    world: World,
) -> None:
    integration = _legacy_batch(world)
    _develop_commit(world, "develop retires the legacy test", remove=[LEGACY_TEST])
    _merge_develop(world, integration)
    built = bi.build_evidence(integration)
    tracker = _tracker_view(world)
    row = built["evidence"]["current_acceptance"]["zeta"]
    for forged_row in (
        dict(row, ran_command=["true"]),
        dict(row, ran_command=row["command"]),
        dict(row, dropped_paths=[]),
        dict(row, dropped_paths=["tests/test_zeta.py"]),
    ):
        evidence = dict(
            built["evidence"], current_acceptance={**built["evidence"]["current_acceptance"], "zeta": forged_row}
        )
        failures = pr_landing._check_evidence_acceptance(
            integration, evidence, world.head(), tracker, tracker["receipts"]
        )
        assert any("member zeta" in item for item in failures)


def test_a_fix_rerun_drops_the_path_a_develop_merge_deleted_and_records_it(world: World) -> None:
    integration = _legacy_batch(world)
    _develop_commit(world, "develop retires the legacy test", remove=[LEGACY_TEST])
    _merge_develop(world, integration)
    recorded = world.tracker.receipts["zeta"]["verification"]["command"]
    fix_head = world.commit(integration, {"src/a.py": "fixed\n"}, "fix")
    world.calls.clear()

    bi.advance_head(integration, "fix")

    expected = [arg for arg in recorded if arg != LEGACY_TEST]
    assert _pytest_calls(world) == [expected]
    entry = _ledger(integration)["acceptance"]["zeta"][-1]
    assert entry["reason"] == f"fix:{fix_head}" and entry["status"] == "passed"
    assert entry["command"] == recorded and entry["ran_command"] == expected and entry["dropped_paths"] == [LEGACY_TEST]


def test_an_overlap_rerun_drops_the_path_a_develop_merge_deleted_and_records_it(world: World) -> None:
    _develop_commit(world, "legacy test", add={LEGACY_TEST: "def test_l():\n    assert True\n"})
    integration = world.start()
    world.deliver("zeta", {"src/a.py": "zeta change\n"}, tests=["tests/test_zeta.py", LEGACY_TEST])
    world.land_and_prepare("zeta")
    recorded = world.tracker.receipts["zeta"]["verification"]["command"]
    _develop_commit(world, "develop retires the legacy test", remove=[LEGACY_TEST])
    _merge_develop(world, integration)
    world.deliver("mid", {"src/a.py": "mid change\n"}, tests=["tests/test_mid.py"])
    world.calls.clear()

    result = world.land("mid")

    expected = [arg for arg in recorded if arg != LEGACY_TEST]
    assert result["reruns"] == [{"member": "zeta", "paths": ["src/a.py"], "status": "passed"}]
    assert expected in _pytest_calls(world) and recorded not in _pytest_calls(world)
    entry = _ledger(integration)["acceptance"]["zeta"][-1]
    assert entry["reason"] == "overlap:mid" and entry["command"] == recorded
    assert entry["ran_command"] == expected and entry["dropped_paths"] == [LEGACY_TEST]


def test_a_rerun_whose_recorded_test_paths_are_all_gone_fails_without_running_pytest(world: World) -> None:
    _develop_commit(world, "legacy test", add={LEGACY_TEST: "def test_l():\n    assert True\n"})
    integration = world.start()
    world.deliver("zeta", {"src/a.py": "zeta change\n"}, tests=[LEGACY_TEST])
    world.land_and_prepare("zeta")
    _develop_commit(world, "develop retires the legacy test", remove=[LEGACY_TEST])
    _merge_develop(world, integration)
    world.deliver("mid", {"src/a.py": "mid change\n"}, tests=["tests/test_mid.py"])
    world.calls.clear()

    with _raises("overlap rerun failed for \\['zeta'\\]"):
        world.land("mid")

    assert all(LEGACY_TEST not in call for call in _pytest_calls(world))
    entry = _ledger(integration)["acceptance"]["zeta"][-1]
    assert entry["status"] == "failed" and entry["dropped_paths"] == [LEGACY_TEST]
    assert "every verification path in member 'zeta'" in entry["output"]


def test_an_overlap_rerun_still_fails_for_a_deleted_path_a_member_owns(world: World) -> None:
    integration = _legacy_batch(world)
    _develop_commit(world, "develop retires the legacy test", remove=[LEGACY_TEST])
    _merge_develop(world, integration)
    world.tracker.receipts["zeta"]["changed_files"].append(LEGACY_TEST)
    world.commit(integration, {"src/a.py": "fixed\n"}, "fix")
    world.calls.clear()

    with _raises("broke earlier members \\['zeta'\\]"):
        bi.advance_head(integration, "fix")

    entry = _ledger(integration)["acceptance"]["zeta"][-1]
    assert entry["dropped_paths"] == [] and entry["ran_command"] == entry["command"]
