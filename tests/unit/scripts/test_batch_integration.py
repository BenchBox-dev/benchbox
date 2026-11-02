from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.unit.scripts.batch_loop_world import BATCH, MEMBERS, PROJECT, SCOPE, World, bi, git

TRACKER_SCOPE_HASH = "19ccf864114b7650486e01283d3af3b20d383cb3eb266699654ed626a849e624"

pytestmark = [pytest.mark.unit, pytest.mark.medium]


def _ledger(integration: Path) -> dict:
    return json.loads((integration / ".todo-batch" / BATCH / "ledger.json").read_text(encoding="utf-8"))


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    for key, value in {
        "GIT_AUTHOR_NAME": "Leader",
        "GIT_AUTHOR_EMAIL": "leader@example.com",
        "GIT_COMMITTER_NAME": "Leader",
        "GIT_COMMITTER_EMAIL": "leader@example.com",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "HOME": str(tmp_path),
    }.items():
        monkeypatch.setenv(key, value)
    return World(tmp_path, monkeypatch)


def _raises(match: str):
    return pytest.raises(bi.BatchError, match=match)


def test_start_creates_a_role_integration_worktree_and_a_registrable_payload(world: World) -> None:
    worktree = world.start()
    arguments = world.payload["arguments"]
    assert bi.role_of(worktree) == "integration" and bi._config_get(worktree, "id") == BATCH
    assert git(worktree, "branch", "--show-current") == f"feat/{BATCH}"
    assert arguments["members"] == MEMBERS
    assert arguments["scope"] == SCOPE and arguments["scope_hash"] == TRACKER_SCOPE_HASH
    assert arguments["develop_ref"] == "refs/remotes/origin/develop"
    assert arguments["start_head"] == git(world.primary, "rev-parse", "refs/remotes/origin/develop")
    assert arguments["integration_worktree"] == str(worktree) and arguments["project_id"] == "proj"
    assert arguments["repository"] == str(world.origin) and len(arguments["owner_generation"]) == 32
    assert (worktree / ".todo-batch" / BATCH / "ledger.json").is_file()
    assert bi._is_clean(worktree)


@pytest.mark.parametrize(
    ("members", "scope"),
    [
        (["zeta", "zeta"], {"zeta": ["src/*"]}),
        (["zeta", "alpha"], {"zeta": ["src/*"]}),
        (["zeta", "a--b"], {"zeta": ["src/*"], "a--b": ["src/*"]}),
    ],
)
def test_start_refuses_a_bad_member_set_or_scope_before_creating_anything(
    world: World, members: list[str], scope: dict
) -> None:
    with _raises("members|scope|identifier"):
        bi.start_batch(world.primary, BATCH, members, scope, project_id=PROJECT)
    assert not world.calls_matching("worktree-create")


def test_start_prints_one_update_item_step_per_member_merging_needs_with_dependencies(world: World) -> None:
    world.tracker.item_needs = {"alpha": ["external", "zeta"], "mid": ["external"]}
    world.start(dependencies={"alpha": ["zeta"], "mid": ["zeta", "alpha"]})
    after = world.payload["after_register"]
    assert [step["arguments"]["id"] for step in after["steps"]] == MEMBERS
    by_id = {step["arguments"]["id"]: step["arguments"] for step in after["steps"]}
    assert by_id["zeta"] == {
        "id": "zeta",
        "needs": [],
        "batch": {"batch_id": BATCH, "member_id": "zeta", "implementation_dependencies": []},
    }
    assert by_id["alpha"]["needs"] == ["external", "zeta"]
    assert by_id["alpha"]["batch"]["implementation_dependencies"] == ["zeta"]
    assert by_id["mid"]["needs"] == ["external", "zeta", "alpha"]
    assert all(step["tool"] == "update_item" for step in after["steps"])
    assert "after register_batch" in after["label"]
    assert len(after["reminders"]) == 2 and all("take it before batch-land" in text for text in after["reminders"])


@pytest.mark.parametrize(
    "dependencies",
    [{"alpha": ["ghost"]}, {"ghost": ["zeta"]}, {"alpha": ["alpha"]}, {"zeta": ["alpha"], "alpha": ["zeta"]}],
)
def test_start_refuses_unknown_self_or_cyclic_dependencies_before_creating_anything(
    world: World, dependencies: dict
) -> None:
    with _raises("registered members|itself|cycle"):
        world.start(dependencies=dependencies)
    assert not world.calls_matching("worktree-create")


def test_dependency_input_parses_members_and_refuses_malformed_entries() -> None:
    assert bi.parse_dependencies("alpha=zeta+mid,mid=zeta") == {"alpha": ["zeta", "mid"], "mid": ["zeta"]}
    assert bi.parse_dependencies("") == {}
    for bad in ("alpha", "alpha=", "alpha=zeta,alpha=mid"):
        with _raises("must read member|names no dependency"):
            bi.parse_dependencies(bad)


def test_member_create_starts_at_the_current_integration_head_and_records_identity(world: World) -> None:
    integration = world.start()
    first = world.member("zeta")
    assert git(first, "branch", "--show-current") == f"feat/{BATCH}--zeta"
    assert git(first, "rev-parse", "HEAD") == world.head()
    assert [bi._config_get(first, key) for key in ("role", "id", "member", "source-base")] == [
        "member",
        BATCH,
        "zeta",
        world.head(),
    ]
    moved = world.commit(integration, {"src/a.py": "moved\n"}, "leader moves the head")
    second = world.member("alpha")
    assert git(second, "rev-parse", "HEAD") == moved == bi._config_get(second, "source-base")


def test_member_create_refuses_unknown_members_repeats_and_non_integration_worktrees(world: World) -> None:
    integration = world.start()
    with _raises("not a registered member"):
        bi.create_member(integration, "ghost")
    world.member("zeta")
    with _raises("already exists"):
        bi.create_member(integration, "zeta")
    with _raises("role=integration"):
        bi.create_member(world.primary, "alpha")


def test_member_check_refuses_an_unsynced_member(world: World) -> None:
    integration = world.start()
    member = world.member("zeta")
    world.commit(member, {"src/a.py": "member\n"})
    world.commit(integration, {"src/b.py": "leader\n"})
    with _raises("does not contain the current integration head"):
        world.check(member, ["tests/test_zeta.py"])
    world.sync(member)
    assert world.check(member, ["tests/test_zeta.py"])["member"] == "zeta"


def test_member_check_refuses_a_dirty_tree(world: World) -> None:
    world.start()
    member = world.member("zeta")
    world.commit(member, {"src/a.py": "member\n"})
    (member / "stray.txt").write_text("untracked")
    with _raises("not clean"):
        world.check(member, ["tests/test_zeta.py"])
    assert not world.calls_matching("changelog-check")


def test_member_check_refuses_non_python_changes_unless_tests_are_named(world: World) -> None:
    world.start()
    member = world.member("mid")
    world.commit(member, {"config/app.yaml": "key: value\n"})
    with _raises("need named tests"):
        world.check(member)
    with _raises("does not exist"):
        world.check(member, ["tests/test_missing.py"])
    record = world.check(member, ["tests/test_mid.py"])
    assert record["verification"]["command"][-1] == "tests/test_mid.py"


def test_member_check_records_a_result_keyed_by_member_head_and_integration_head(world: World) -> None:
    world.start()
    member = world.member("zeta")
    member_head = world.commit(member, {"src/a.py": "member\n"})
    integration_head = world.head()
    record = world.check(member, ["tests/test_zeta.py"])
    stored = bi._read_check(member, member_head, integration_head)
    assert stored == record and stored["review_outcome"] == "clear"
    assert stored["trusted_sha"] == git(world.primary, "rev-parse", "refs/remotes/origin/develop")
    assert bi._read_check(member, member_head, "0" * 40) is None
    assert bi._read_check(member, "0" * 40, integration_head) is None
    preflight = [call for call in world.calls if call[0][:2] == ["make", "pr-preflight"]]
    assert preflight == [(["make", "pr-preflight", f"BASE={integration_head}"], member)]


def test_member_check_records_an_exempt_only_member_as_not_reviewed(world: World) -> None:
    world.start()
    member = world.member("zeta")
    world.commit(member, {"docs/note.md": "changed\n"})
    record = world.check(member, ["tests/test_zeta.py"])
    assert record["review_outcome"] == "not-reviewed"


def test_member_check_records_nothing_when_preflight_or_review_fails(world: World) -> None:
    integration = world.start()
    member = world.member("zeta")
    head = world.commit(member, {"src/a.py": "member\n"})
    world.preflight_rc = 1
    with _raises("preflight failed"):
        world.check(member, ["tests/test_zeta.py"])
    world.preflight_rc = 0
    world.review_rc = 1
    with _raises("did not clear"):
        world.check(member, ["tests/test_zeta.py"])
    assert bi._read_check(member, head, git(integration, "rev-parse", "HEAD")) is None


def test_member_check_uses_an_overridden_preflight_command(world: World) -> None:
    world.start()
    member = world.member("zeta")
    world.commit(member, {"src/a.py": "member\n"})
    bi.check_member(member, ["tests/test_zeta.py"], preflight_command=["make", "pr-preflight", "SCOPE=fast"])
    assert any(call[0][:3] == ["make", "pr-preflight", "SCOPE=fast"] for call in world.calls)


def test_land_refuses_outside_an_integration_worktree(world: World) -> None:
    world.start()
    member = world.deliver("zeta", {"src/a.py": "member\n"})
    for repo in (world.primary, member):
        with _raises("role=integration"):
            bi.land_member(repo, "zeta")


def test_land_merges_checks_the_tree_and_prints_the_prepare_payload(world: World) -> None:
    integration = world.start()
    member = world.deliver("zeta", {"src/a.py": "member\n"})
    member_head = git(member, "rev-parse", "HEAD")
    before = world.head()
    result = world.land("zeta")
    merged = world.head()
    assert result["merge_head"] == merged and result["status"] == "merged"
    assert git(integration, "rev-parse", "HEAD^1") == before and git(integration, "rev-parse", "HEAD^2") == member_head
    assert git(integration, "rev-parse", "HEAD^{tree}") == git(member, "rev-parse", "HEAD^{tree}")
    take, prepare = result["payload"]["steps"]
    assert take == {"tool": "take", "arguments": {"id": "zeta"}}
    arguments = prepare["arguments"]
    assert (arguments["accepted_head"], arguments["source_base"], arguments["integration_head"]) == (
        member_head,
        before,
        merged,
    )
    assert (
        arguments["source_worktree"] == str(member)
        and arguments["scope_hash"] == world.payload["arguments"]["scope_hash"]
    )
    assert arguments["verification"]["revision"] == member_head and arguments["verification"]["status"] == "passed"
    assert git(world.origin, "rev-parse", f"refs/heads/feat/{BATCH}") == merged
    ledger = _ledger(integration)
    assert ledger["landings"]["zeta"]["merge_head"] == merged
    world.submit(result)
    assert world.tracker.head == merged and bi._is_clean(integration)


def test_a_dependent_member_is_not_landed_until_the_owner_holds_its_claim(world: World) -> None:
    world.start()
    world.deliver("zeta", {"src/a.py": "member\n"})
    world.land_and_prepare("zeta")
    world.deliver("alpha", {"src/b.py": "member\n"})
    before = world.head()
    with _raises("take it now, before landing"):
        world.land("alpha", dependencies=["zeta"])
    assert world.head() == before
    world.tracker.claims["alpha"] = "leader"
    result = world.land("alpha", dependencies=["zeta"])
    (prepare,) = result["payload"]["steps"]
    assert prepare["tool"] == "prepare" and prepare["arguments"]["implementation_dependencies"] == ["zeta"]


def test_a_member_whose_receipt_was_invalidated_is_prepared_again_at_the_current_head(world: World) -> None:
    world.start()
    world.deliver("zeta", {"src/a.py": "zeta\n"})
    zeta_merge = world.land_and_prepare("zeta")["merge_head"]
    world.deliver("alpha", {"src/b.py": "alpha\n"})
    world.land_and_prepare("alpha")
    world.tracker.receipts.pop("zeta")
    again = world.land("zeta")
    arguments = again["payload"]["steps"][-1]["arguments"]
    assert again["status"] == "already-merged"
    assert arguments["integration_head"] == world.head() != zeta_merge


def test_land_refuses_a_member_that_synced_before_the_integration_head_moved(world: World) -> None:
    integration = world.start()
    member = world.deliver("zeta", {"src/a.py": "member\n"})
    world.commit(integration, {"src/b.py": "leader fix\n"}, "fix")
    world.tracker.advance(
        {
            "reason": "fix",
            "expected_head": world.tracker.head,
            "head": world.head(),
            "changed_files": ["src/b.py"],
        }
    )
    before = world.head()
    with _raises("moved since the member synced|never ran batch-member-check"):
        world.land("zeta")
    world.sync(member)
    with _raises("moved since the member synced|never ran batch-member-check"):
        world.land("zeta")
    assert world.head() == before
    world.check(member, ["tests/test_zeta.py"])
    assert world.land("zeta")["status"] == "merged"


def test_land_refuses_when_the_review_cache_has_no_result_even_if_member_config_says_clear(world: World) -> None:
    world.start()
    member = world.deliver("zeta", {"src/a.py": "member\n"})
    stored = bi._read_check(member, git(member, "rev-parse", "HEAD"), world.head())
    assert stored["review_outcome"] == "clear"
    world.review_cache.clear()
    before = world.head()
    with _raises("no result for this member's patch"):
        world.land("zeta")
    assert world.head() == before


def test_land_refuses_a_cached_findings_result(world: World) -> None:
    world.start()
    world.deliver("zeta", {"src/a.py": "member\n"})
    for key, value in list(world.review_cache.items()):
        world.review_cache[key] = {**value, "outcome": "findings"}
    with _raises("blocks landing"):
        world.land("zeta")


def test_land_accepts_a_not_reviewed_result_for_an_exempt_only_member(world: World) -> None:
    world.start()
    world.deliver("zeta", {"docs/note.md": "changed\n"})
    result = world.land("zeta")
    assert result["payload"]["steps"][1]["arguments"]["verification"]["review"]["outcome"] == "not-reviewed"


def test_land_refuses_paths_outside_the_members_frozen_scope(world: World) -> None:
    world.start()
    world.deliver("zeta", {"config/x.yaml": "k: v\n"}, tests=["tests/test_zeta.py"])
    with _raises("outside its frozen scope"):
        world.land("zeta")


def test_land_refuses_a_second_member_while_the_first_is_merged_but_not_prepared(world: World) -> None:
    world.start()
    world.deliver("zeta", {"src/a.py": "member\n"})
    world.deliver("alpha", {"src/b.py": "member\n"})
    world.land("zeta")
    before = world.head()
    with _raises("not a recorded head"):
        world.land("alpha")
    assert world.head() == before


def test_land_undoes_a_merge_whose_tree_differs_from_the_member_tree(world: World) -> None:
    integration = world.start()
    world.deliver("zeta", {"src/a.py": "member\n"})
    hooks = Path(git(integration, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "hooks"
    hooks.mkdir(exist_ok=True)
    hook = hooks / "post-merge"
    hook.write_text(
        "#!/bin/sh\n"
        "echo tampered > tampered.txt\n"
        "git add tampered.txt\n"
        'new=$(git commit-tree "$(git write-tree)" -p HEAD^1 -p HEAD^2 -m tampered)\n'
        'git update-ref HEAD "$new"\n'
    )
    hook.chmod(0o755)
    before = world.head()
    with _raises("differs from the member tree"):
        world.land("zeta")
    assert world.head() == before and bi._is_clean(integration)


def test_a_crash_between_merge_and_prepare_is_reported_and_never_merges_twice(world: World) -> None:
    integration = world.start()
    world.deliver("zeta", {"src/a.py": "member\n"})
    alpha = world.deliver("alpha", {"src/b.py": "member\n"})
    merged = world.land("zeta")["merge_head"]
    status = bi.batch_status(integration)
    assert [(m["member"], m["state"]) for m in status["members"]] == [
        ("zeta", "merged-unprepared"),
        ("alpha", "pending"),
        ("mid", "pending"),
    ]
    assert any("zeta" in step and "prepare" in step for step in status["next"])
    again = world.land("zeta")
    assert again["status"] == "already-merged" and world.head() == merged
    assert git(integration, "rev-list", "--merges", "--count", "HEAD") == "1"
    world.submit(again)
    assert bi.batch_status(integration)["next"] == []
    world.sync(alpha)
    world.check(alpha, ["tests/test_alpha.py"])
    assert world.land("alpha")["status"] == "merged"


def test_a_deleted_ledger_is_rebuilt_from_the_tracker_and_git(world: World) -> None:
    integration = world.start()
    world.deliver("zeta", {"src/a.py": "member\n"})
    world.land_and_prepare("zeta")
    world.deliver("alpha", {"src/b.py": "member\n"})
    merged = world.land("alpha")["merge_head"]
    ledger_dir = integration / ".todo-batch" / BATCH
    for path in ledger_dir.iterdir():
        path.unlink()
    status = bi.batch_status(integration)
    assert status["ledger"].startswith("rebuilt")
    assert [(m["member"], m["state"]) for m in status["members"]] == [
        ("zeta", "prepared"),
        ("alpha", "merged-unprepared"),
        ("mid", "pending"),
    ]
    ledger = _ledger(integration)
    assert ledger["members"] == MEMBERS and ledger["landings"]["zeta"]["changed_files"] == ["src/a.py"]
    assert ledger["landings"]["alpha"]["merge_head"] == merged
    before = world.head()
    assert world.land("alpha")["status"] == "already-merged" and world.head() == before


def test_an_overlap_reruns_the_earlier_members_recorded_verification_and_records_it(world: World) -> None:
    integration = world.start()
    world.deliver("zeta", {"src/a.py": "zeta change\n"})
    first = world.land_and_prepare("zeta")
    zeta_command = first["payload"]["steps"][1]["arguments"]["verification"]["command"]
    world.deliver("alpha", {"src/a.py": "alpha change\n"})
    world.calls.clear()
    result = world.land("alpha")
    reruns = [call for call in world.calls if call[0] == zeta_command]
    assert reruns == [(zeta_command, integration)]
    assert result["reruns"] == [{"member": "zeta", "paths": ["src/a.py"], "status": "passed"}]
    ledger = _ledger(integration)
    entry = ledger["acceptance"]["zeta"][-1]
    assert entry["revision"] == result["merge_head"] and entry["status"] == "passed" and entry["clean"] is True
    assert entry["reason"] == "overlap:alpha"


def test_no_overlap_triggers_no_rerun(world: World) -> None:
    world.start()
    world.deliver("zeta", {"src/a.py": "zeta change\n"})
    world.land_and_prepare("zeta")
    world.deliver("alpha", {"src/b.py": "alpha change\n"})
    world.calls.clear()
    assert world.land("alpha")["reruns"] == []
    assert not [call for call in world.calls if "pytest" in call[0]]


def test_a_failing_overlap_rerun_blocks_the_prepare_payload_and_is_recorded(world: World) -> None:
    integration = world.start()
    world.deliver("zeta", {"src/a.py": "zeta change\n"})
    world.land_and_prepare("zeta")
    world.deliver("alpha", {"src/a.py": "alpha change\n"})
    world.failing_tests.add("tests/test_zeta.py")
    with _raises("overlap rerun failed for \\['zeta'\\]"):
        world.land("alpha")
    ledger = _ledger(integration)
    assert ledger["acceptance"]["zeta"][-1]["status"] == "failed"


def test_a_silent_revert_prints_the_hunk_warning_and_still_lands(world: World) -> None:
    world.start()
    original = (world.primary / "src" / "a.py").read_text(encoding="utf-8")
    added = original.replace("a3 = 3\n", "a3 = 3\nfeature_flag = True\n")
    world.deliver("zeta", {"src/a.py": added})
    world.land_and_prepare("zeta")
    member = world.member("alpha")
    world.commit(member, {"src/a.py": original}, "quietly drops the earlier line")
    world.check(member, ["tests/test_alpha.py"])
    result = world.land("alpha")
    assert result["status"] == "merged"
    assert any("zeta" in warning and "no longer present" in warning for warning in result["warnings"])
    assert result["reruns"][0]["member"] == "zeta"


def test_a_clean_landing_prints_no_hunk_warning(world: World) -> None:
    world.start()
    world.deliver("zeta", {"src/a.py": "zeta change\n"})
    world.land_and_prepare("zeta")
    world.deliver("alpha", {"src/b.py": "alpha change\n"})
    assert world.land("alpha")["warnings"] == []


def test_land_refuses_a_landing_whose_file_list_exceeds_the_brief_cap(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.start()
    world.deliver("zeta", {"src/a.py": "member\n"})
    monkeypatch.setattr(bi, "_brief_cap", lambda repo: 300)
    before = world.head()
    with _raises("exceed brief_max_bytes"):
        world.land("zeta")
    assert world.head() == before


def test_land_warns_when_a_landing_moves_the_brief_out_of_inline_mode(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.start()
    world.deliver("zeta", {"src/a.py": "x = 1\n" * 2000})
    monkeypatch.setattr(bi, "_brief_cap", lambda repo: 40000)
    result = world.land("zeta")
    assert any("out of inline mode" in warning for warning in result["warnings"])
    small = world.deliver("alpha", {"src/b.py": "y = 1\n"})
    assert small.exists()


def test_brief_sizes_report_inline_and_file_list_modes(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    integration = world.start()
    monkeypatch.setattr(bi, "_brief_cap", lambda repo: 40000)
    world.commit(integration, {"src/a.py": "x = 1\n" * 10})
    assert bi.brief_sizes(integration, "HEAD")["mode"] == "inline"
    world.commit(integration, {"src/b.py": "x = 1\n" * 3000})
    sizes = bi.brief_sizes(integration, "HEAD")
    assert sizes["mode"] == "file-list" and sizes["inline_diff_bytes"] > 40000 - bi.DIFF_HEADROOM_BYTES


def _landed_pair(world: World) -> Path:
    integration = world.start()
    world.deliver("zeta", {"src/a.py": "zeta change\n"})
    world.land_and_prepare("zeta")
    world.deliver("alpha", {"src/b.py": "alpha change\n"})
    world.land_and_prepare("alpha")
    return integration


def test_a_fix_reruns_only_the_earlier_members_whose_files_it_touches(world: World) -> None:
    integration = _landed_pair(world)
    expected = world.head()
    fix_head = world.commit(integration, {"src/a.py": "fixed\n"}, "fix")
    world.calls.clear()
    result = bi.advance_head(integration, "fix")
    assert result["requires_acceptance"] == ["zeta"] and result["changed_files"] == ["src/a.py"]
    assert [call[0][-1] for call in world.calls if "pytest" in call[0]] == ["tests/test_zeta.py"]
    assert result["payload"] == {
        "tool": "advance_batch_head",
        "arguments": {
            "batch_id": BATCH,
            "owner_generation": world.payload["arguments"]["owner_generation"],
            "expected_head": expected,
            "head": fix_head,
            "reason": "fix",
            "changed_files": ["src/a.py"],
        },
    }
    ledger = _ledger(integration)
    assert (
        ledger["advances"][-1]["head"] == fix_head and ledger["acceptance"]["zeta"][-1]["reason"] == f"fix:{fix_head}"
    )
    assert git(world.origin, "rev-parse", f"refs/heads/feat/{BATCH}") == fix_head


def test_a_fix_that_breaks_an_earlier_member_prints_no_payload(world: World) -> None:
    integration = _landed_pair(world)
    world.commit(integration, {"src/a.py": "fixed\n"}, "fix")
    world.failing_tests.add("tests/test_zeta.py")
    with _raises("broke earlier members \\['zeta'\\]"):
        bi.advance_head(integration, "fix")


def test_a_fix_outside_every_member_scope_is_refused(world: World) -> None:
    integration = _landed_pair(world)
    world.commit(integration, {"other/z.py": "z = 1\n"}, "widening fix")
    with _raises("cannot widen scope"):
        bi.advance_head(integration, "fix")


def test_advance_refuses_when_there_is_nothing_new_or_a_member_is_unprepared(world: World) -> None:
    integration = world.start()
    with _raises("nothing to record"):
        bi.advance_head(integration, "fix")
    world.deliver("zeta", {"src/a.py": "member\n"})
    world.land("zeta")
    with _raises("merged but not prepared"):
        bi.advance_head(integration, "fix")


def test_a_develop_merge_is_recorded_without_any_member_rerun(world: World) -> None:
    integration = _landed_pair(world)
    (world.seed / "other").mkdir()
    (world.seed / "other" / "dev.txt").write_text("develop moved\n")
    git(world.seed, "add", "other/dev.txt")
    git(world.seed, "commit", "-qm", "develop moves")
    git(world.seed, "push", "-q", "origin", "develop")
    expected = world.head()
    world.calls.clear()
    result = bi.advance_head(integration, "develop-merge")
    assert result["requires_acceptance"] == [] and result["changed_files"] == ["other/dev.txt"]
    assert not [call for call in world.calls if "pytest" in call[0]]
    assert result["payload"]["arguments"]["reason"] == "develop-merge"
    assert result["payload"]["arguments"]["expected_head"] == expected
    assert len(git(integration, "rev-list", "--parents", "-n", "1", "HEAD").split()) == 3
    ledger = _ledger(integration)
    assert ledger["advances"][-1]["kind"] == "develop-merge"


def test_a_develop_merge_is_recorded_after_a_fix_advance_has_already_been_recorded(world: World) -> None:
    integration = _landed_pair(world)
    world.commit(integration, {"src/a.py": "fixed\n"}, "fix")
    fix = bi.advance_head(integration, "fix")
    world.tracker.advance(fix["payload"]["arguments"])
    (world.seed / "other").mkdir()
    (world.seed / "other" / "dev.txt").write_text("develop moved\n")
    git(world.seed, "add", "other/dev.txt")
    git(world.seed, "commit", "-qm", "develop moves")
    git(world.seed, "push", "-q", "origin", "develop")
    result = bi.advance_head(integration, "develop-merge")
    assert result["expected_head"] == fix["head"] and result["head"] == world.head() != fix["head"]
    assert result["payload"]["arguments"]["reason"] == "develop-merge"
    world.tracker.advance(result["payload"]["arguments"])
    with _raises("nothing to record"):
        bi.advance_head(integration, "fix")


def test_advance_retry_after_the_tracker_recorded_it_reuses_the_recorded_expected_head(world: World) -> None:
    integration = _landed_pair(world)
    fix_head = world.commit(integration, {"src/a.py": "fixed\n"}, "fix")
    first = bi.advance_head(integration, "fix")
    world.tracker.advance(first["payload"]["arguments"])
    retry = bi.advance_head(integration, "fix")
    assert retry["payload"]["arguments"]["head"] == fix_head
    assert retry["payload"]["arguments"]["expected_head"] == first["payload"]["arguments"]["expected_head"]


def test_verify_passes_after_origin_develop_moves_and_fails_on_an_unrecorded_first_parent_commit(
    world: World,
) -> None:
    integration = _landed_pair(world)
    (world.seed / "late.txt").write_text("late\n")
    git(world.seed, "add", "late.txt")
    git(world.seed, "commit", "-qm", "develop moves")
    git(world.seed, "push", "-q", "origin", "develop")
    git(integration, "fetch", "-q", "origin", "develop")
    report = bi.verify_integration(integration)
    assert report["ok"] is True and list(report["member_ancestry"]) == MEMBERS
    assert report["member_ancestry"] == {"zeta": True, "alpha": True, "mid": None}
    stray = world.commit(integration, {"src/a.py": "unrecorded\n"}, "stray")
    report = bi.verify_integration(integration)
    assert report["ok"] is False and report["unrecorded_first_parent_commits"] == [stray]


def test_verify_accepts_a_recorded_develop_merge_and_ignores_committer_identity(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    integration = _landed_pair(world)
    (world.seed / "late.txt").write_text("late\n")
    git(world.seed, "add", "late.txt")
    git(world.seed, "commit", "-qm", "develop moves")
    git(world.seed, "push", "-q", "origin", "develop")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Someone Else")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "else@example.com")
    result = bi.advance_head(integration, "develop-merge")
    world.tracker.advance(result["payload"]["arguments"])
    report = bi.verify_integration(integration)
    assert report["ok"] is True
    assert "Someone Else <else@example.com>" in report["committers_informational"]


def test_verify_fails_when_a_prepared_members_head_is_missing_from_the_integration_head(world: World) -> None:
    integration = _landed_pair(world)
    world.tracker.receipts["alpha"]["accepted_head"] = "0" * 40
    report = bi.verify_integration(integration)
    assert report["ok"] is False and report["member_ancestry"]["alpha"] is False


def test_status_lists_members_in_registered_order_with_brief_and_acceptance_state(world: World) -> None:
    integration = world.start()
    world.deliver("zeta", {"src/a.py": "zeta change\n"})
    world.land_and_prepare("zeta")
    world.deliver("alpha", {"src/a.py": "alpha change\n"})
    world.land_and_prepare("alpha")
    status = bi.batch_status(integration)
    assert [m["member"] for m in status["members"]] == MEMBERS
    assert status["acceptance_required"] == ["zeta"] and status["acceptance_stale"] == []
    assert status["brief"]["mode"] == "inline" and status["brief"]["brief_max_bytes"] == 100000
    assert status["integration_head"] == status["tracker_integration_head"] == world.head()
    world.commit(integration, {"src/a.py": "fixed\n"}, "fix")
    assert bi.batch_status(integration)["acceptance_stale"] == ["zeta"]


def test_status_names_an_unrecorded_leader_commit_as_the_next_step(world: World) -> None:
    integration = _landed_pair(world)
    world.commit(integration, {"src/a.py": "fixed\n"}, "fix")
    status = bi.batch_status(integration)
    assert any("batch-advance" in step for step in status["next"])


def test_tracker_reads_page_every_section_with_the_revision_pin(world: World) -> None:
    integration = world.start()
    world.deliver("zeta", {"src/a.py": "member\n"})
    world.land_and_prepare("zeta")
    expected = world.tracker.sections()
    world.tracker.window_cap = 40
    world.tracker.paged_sections = {"receipts", "integration_log"}
    world.calls.clear()
    tracker = bi.read_tracker(integration, BATCH)
    assert tracker["receipts"] == expected["receipts"] and tracker["integration_log"] == expected["integration_log"]
    base = [*bi.TODO_DB_COMMAND, "batch", "show", BATCH]
    argvs = [call[0] for call in world.calls]
    assert argvs[0] == base
    assert [
        *base,
        "--field",
        "receipts",
        "--offset",
        "40",
        "--budget",
        "6000",
        "--rev",
        str(world.tracker.rev),
    ] in argvs
    assert all(call[1] == integration for call in world.calls)


def test_an_unreadable_tracker_fails_closed(world: World) -> None:
    integration = world.start()
    world.deliver("zeta", {"src/a.py": "member\n"})
    world.tracker.unreadable = True
    with _raises("tracker read failed"):
        world.land("zeta")
    assert world.head() == git(integration, "rev-parse", "refs/heads/feat/b1")
    assert git(integration, "rev-list", "--merges", "--count", "HEAD") == "0"


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
    assert set(evidence["current_acceptance"]) == {"zeta"}
    zeta = evidence["current_acceptance"]["zeta"]
    assert zeta == {
        "status": "passed",
        "revision": final,
        "clean": True,
        "suite": "member-tests",
        "command": world.tracker.receipts["zeta"]["verification"]["command"],
        "worktree": str(integration),
    }
    assert [call for call in world.calls if "pytest" in call[0]] == [(zeta["command"], integration)]
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


def test_evidence_reuses_a_recorded_rerun_that_already_covers_the_final_head(world: World) -> None:
    integration = _ready_for_evidence(world)
    bi.build_evidence(integration)
    world.calls.clear()
    bi.build_evidence(integration)
    assert not [call for call in world.calls if "pytest" in call[0]]


def test_evidence_refuses_without_a_final_pr_bound_at_the_integration_head(world: World) -> None:
    integration = _ready_for_evidence(world)
    world.tracker.final_pr = None
    with _raises("final PR is not bound"):
        bi.build_evidence(integration)
    world.tracker.bind("0" * 40)
    with _raises("final PR is not bound"):
        bi.build_evidence(integration)


def test_evidence_refuses_when_a_member_has_no_accepted_review_for_its_receipt_range(world: World) -> None:
    integration = _ready_for_evidence(world)
    world.review_cache.clear()
    with _raises("no accepted stored review"):
        bi.build_evidence(integration)


def test_evidence_refuses_when_a_member_is_not_prepared(world: World) -> None:
    integration = _ready_for_evidence(world)
    del world.tracker.receipts["mid"]
    with _raises("lacks a prepared_work_v2 receipt"):
        bi.build_evidence(integration)


def test_cli_verify_reports_the_exit_status(world: World, capsys: pytest.CaptureFixture[str]) -> None:
    integration = _landed_pair(world)
    assert bi.main(["--worktree", str(integration), "verify"]) == 0
    world.commit(integration, {"src/a.py": "unrecorded\n"}, "stray")
    assert bi.main(["--worktree", str(integration), "verify"]) == 1
    capsys.readouterr()
    assert bi.main(["--worktree", str(world.primary), "verify"]) == 1
    assert "role=integration" in capsys.readouterr().err


def test_cli_prints_the_register_payload_for_start(world: World, capsys: pytest.CaptureFixture[str]) -> None:
    scope = world.tmp / "scope.json"
    scope.write_text(json.dumps({m: SCOPE[m] for m in MEMBERS}))
    code = bi.main(
        [
            "--worktree",
            str(world.primary),
            "start",
            "--batch",
            BATCH,
            "--members",
            ",".join(MEMBERS),
            "--scope-json",
            str(scope),
            "--project-id",
            PROJECT,
            "--confirm-cutover",
        ]
    )
    printed = json.loads(capsys.readouterr().out)
    assert code == 0 and printed["tool"] == "register_batch"
    assert printed["arguments"]["confirm_schema3_cutover"] is True and printed["arguments"]["members"] == MEMBERS
    assert subprocess.run(
        ["git", "worktree", "list"], cwd=world.primary, capture_output=True, encoding="utf-8"
    ).stdout.count(f"feat/{BATCH}")
