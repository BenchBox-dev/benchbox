from __future__ import annotations

import json
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit.scripts.batch_loop_world import (
    BATCH,
    MEMBERS,
    PROJECT,
    SCOPE,
    TRUSTED_REVIEW_WRAPPER,
    World,
    bi,
    build_world,
    git,
)

TRACKER_SCOPE_HASH = "19ccf864114b7650486e01283d3af3b20d383cb3eb266699654ed626a849e624"

pytestmark = [pytest.mark.unit, pytest.mark.medium]

ROOT = Path(__file__).resolve().parents[3]


def _ledger(integration: Path) -> dict:
    return json.loads((integration / ".todo-batch" / BATCH / "ledger.json").read_text(encoding="utf-8"))


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    return build_world(tmp_path, monkeypatch)


def _raises(match: str):
    return pytest.raises(bi.BatchError, match=match)


def test_start_creates_a_role_integration_worktree_and_a_registrable_payload(world: World) -> None:
    worktree = world.start()
    arguments = world.payload["arguments"]
    assert bi.role_of(worktree) == "integration" and bi._config_get(worktree, "id") == BATCH
    assert git(worktree, "branch", "--show-current") == f"feat/{BATCH}"
    assert arguments["members"] == MEMBERS
    assert arguments["scope"] == {m: [*SCOPE[m], *bi.COMPANION_SCOPE] for m in MEMBERS}
    assert arguments["scope_hash"] == bi.scope_digest(arguments["scope"])
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


@pytest.mark.parametrize("members", [[], ["zeta"]])
def test_start_refuses_fewer_than_two_members_and_points_to_serial_work(world: World, members: list[str]) -> None:
    with _raises("at least two members.*serial"):
        bi.start_batch(world.primary, BATCH, members, {m: SCOPE[m] for m in members}, project_id=PROJECT)
    assert not world.calls_matching("worktree-create")
    assert not (world.primary.parent / f"{world.primary.name}.wt-{BATCH}").exists()
    assert git(world.primary, "branch", "--list", f"feat/{BATCH}") == ""


def test_start_freezes_the_companion_files_into_every_scope_and_its_hash(world: World) -> None:
    world.start()
    frozen = world.payload["arguments"]["scope"]
    for member in MEMBERS:
        assert frozen[member] == [*SCOPE[member], "changelog.d/*", "docs/internal/dev-loop-property-ledger.md"]
    assert world.payload["arguments"]["scope_hash"] == bi.scope_digest(frozen)
    assert _ledger(world.integration)["scope"] == frozen


def test_start_does_not_repeat_a_companion_pattern_the_scope_already_names(world: World) -> None:
    world.start(scope={"zeta": ["src/*", "changelog.d/*"], "alpha": ["src/*"], "mid": ["src/*"]})
    assert world.payload["arguments"]["scope"]["zeta"] == [
        "src/*",
        "changelog.d/*",
        "docs/internal/dev-loop-property-ledger.md",
    ]


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
    assert stored == record
    assert stored["trusted_sha"] == git(world.primary, "rev-parse", "refs/remotes/origin/develop")
    assert bi._read_check(member, member_head, "0" * 40) is None
    assert bi._read_check(member, "0" * 40, integration_head) is None
    preflight = [call for call in world.calls if call[0][:2] == ["make", "pr-preflight"]]
    assert preflight == [(["make", "pr-preflight", f"BASE={integration_head}"], member)]


def test_member_review_runs_the_wrapper_from_the_trusted_commit_not_the_checkout(world: World) -> None:
    world.start()
    member = world.member("zeta")
    head = world.commit(member, {"src/a.py": "member\n", bi.REVIEW_WRAPPER: "print('tampered')\n"})
    trusted = git(member, "rev-parse", "refs/remotes/origin/develop")

    result = bi._review_member_patch(member, world.head(), head, trusted)

    assert (member / bi.REVIEW_WRAPPER).read_text(encoding="utf-8") == "print('tampered')\n"
    assert world.review_wrappers == [TRUSTED_REVIEW_WRAPPER] and result["head_sha"] == head


def test_member_review_copies_the_trusted_support_modules_beside_the_wrapper(world: World) -> None:
    world.start()
    member = world.member("zeta")
    head = world.commit(member, {"src/a.py": "member\n", bi.REVIEW_SUPPORT[0]: "TAMPERED = 1\n"})
    trusted = git(member, "rev-parse", "refs/remotes/origin/develop")

    bi._review_member_patch(member, world.head(), head, trusted)

    assert world.review_support == [{Path(path).name: "TRUSTED_SUPPORT = 1\n" for path in bi.REVIEW_SUPPORT}]


def test_the_wrapper_runs_from_a_bare_copy_and_reaches_the_install_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    for name in (bi.REVIEW_WRAPPER, *bi.REVIEW_SUPPORT):
        (scratch / Path(name).name).write_bytes((ROOT / name).read_bytes())
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    repo_scripts = str(ROOT / "scripts")
    monkeypatch.setattr(sys, "path", [entry for entry in sys.path if entry != repo_scripts])
    monkeypatch.delitem(sys.modules, "theseus_cli", raising=False)
    monkeypatch.setattr(sys, "argv", ["oracle_local_review.py", "--trusted-ref", "HEAD"])
    monkeypatch.chdir(repo)

    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(str(scratch / Path(bi.REVIEW_WRAPPER).name), run_name="__main__")

    assert exit_info.value.code == 2
    assert Path(sys.modules["theseus_cli"].__file__).parent == scratch
    assert "the verified Theseus install" in capsys.readouterr().err


def test_member_check_records_nothing_when_preflight_fails(world: World) -> None:
    integration = world.start()
    member = world.member("zeta")
    head = world.commit(member, {"src/a.py": "member\n"})
    world.preflight_rc = 1
    with _raises("preflight failed"):
        world.check(member, ["tests/test_zeta.py"])
    assert bi._read_check(member, head, git(integration, "rev-parse", "HEAD")) is None


def test_member_check_runs_no_review_and_succeeds_when_the_review_would_fail(world: World) -> None:
    world.start()
    member = world.member("zeta")
    world.commit(member, {"src/a.py": "member\n"})
    world.review_rc = 1
    record = world.check(member, ["tests/test_zeta.py"])
    assert record["trusted_sha"] == git(world.primary, "rev-parse", "refs/remotes/origin/develop")
    assert "review_outcome" not in record
    assert world.review_wrappers == []


def test_member_check_refuses_a_path_outside_the_frozen_scope_before_any_check_runs(world: World) -> None:
    world.start()
    member = world.member("zeta")
    head = world.commit(member, {"src/a.py": "member\n", "config/x.yaml": "k: v\n"})
    with _raises(r"outside its frozen scope: config/x.yaml"):
        world.check(member, ["tests/test_zeta.py"])
    assert not world.calls_matching("pr-preflight")
    assert bi._read_check(member, head, world.head()) is None


def test_member_check_accepts_the_companion_files_every_scope_carries(world: World) -> None:
    world.start()
    member = world.member("zeta")
    world.commit(
        member,
        {
            "src/a.py": "member\n",
            "changelog.d/zeta.json": "{}\n",
            "docs/internal/dev-loop-property-ledger.md": "row\n",
        },
    )
    assert world.check(member, ["tests/test_zeta.py"])["member"] == "zeta"


@pytest.mark.parametrize("action", ["check", "land"])
def test_member_steps_refuse_primary_clone_edits_inside_the_member_scope(world: World, action: str) -> None:
    world.start()
    member = world.member("zeta")
    world.commit(member, {"src/a.py": "member\n"})
    if action == "land":
        world.check(member, ["tests/test_zeta.py"])
    (world.primary / "src" / "a.py").write_text("stray edit\n")
    (world.primary / "src" / "new.py").write_text("stray file\n")
    before = world.head()

    with _raises(r"primary clone .* has uncommitted changes in member 'zeta'.s scope: src/a.py, src/new.py"):
        world.check(member, ["tests/test_zeta.py"]) if action == "check" else world.land("zeta")

    assert world.head() == before


def test_member_check_sees_a_primary_clone_rename_out_of_the_member_scope(world: World) -> None:
    world.start()
    member = world.member("zeta")
    world.commit(member, {"src/a.py": "member\n"})
    (world.primary / "elsewhere").mkdir()
    git(world.primary, "mv", "src/a.py", "elsewhere/a.py")

    with _raises(r"member 'zeta'.s scope: src/a.py"):
        world.check(member, ["tests/test_zeta.py"])


def test_land_refuses_an_offline_cached_session_history(world: World) -> None:
    world.start()
    world.deliver("zeta", {"src/a.py": "member\n"})
    world.tracker.sessions_stale = True
    before = world.head()

    with _raises("tracker read is stale"):
        world.land("zeta")

    assert world.head() == before


def test_member_steps_ignore_primary_clone_edits_outside_every_scope(world: World) -> None:
    world.start()
    member = world.member("zeta")
    world.commit(member, {"src/a.py": "member\n"})
    (world.primary / "owner-notes.txt").write_text("unrelated\n")

    world.check(member, ["tests/test_zeta.py"])
    result = world.land("zeta")

    assert result["status"] == "merged"


def test_member_create_refuses_while_the_primary_clone_has_tracked_changes(world: World) -> None:
    world.start()
    (world.primary / "docs" / "note.md").write_text("stray\n")
    (world.primary / "owner-notes.txt").write_text("untracked is ignored\n")
    with _raises(r"primary clone .* uncommitted tracked changes: docs/note.md;"):
        world.member("zeta")
    git(world.primary, "checkout", "--", "docs/note.md")
    assert world.member("zeta").is_dir()


def test_member_check_refuses_tracked_primary_changes_outside_every_scope(world: World) -> None:
    world.start()
    member = world.member("zeta")
    world.commit(member, {"src/a.py": "member\n"})
    (world.primary / ".gitignore").write_text(".todo-batch/\nstray\n")
    (world.primary / "tests" / "test_mid.py").write_text("stray\n")
    with _raises(r"uncommitted tracked changes: .gitignore, tests/test_mid.py;"):
        world.check(member, ["tests/test_zeta.py"])
    assert bi._read_check(member, git(member, "rev-parse", "HEAD"), world.head()) is None
    git(world.primary, "checkout", "--", ".gitignore", "tests/test_mid.py")
    assert world.check(member, ["tests/test_zeta.py"])["member_head"] == git(member, "rev-parse", "HEAD")


def test_a_long_review_finding_is_printed_in_full_and_saved_at_landing(world: World) -> None:
    integration = world.start()
    member = world.deliver("zeta", {"src/a.py": "member\n"})
    head = git(member, "rev-parse", "HEAD")
    finding = "High finding: " + "detail " * 200 + "END-OF-FINDING"
    original = world.review

    def long_review(argv, cwd):
        result = original(argv, cwd)
        result.stderr = finding
        return result

    world.review = long_review
    world.review_rc = 1
    with pytest.raises(bi.BatchError) as raised:
        world.land("zeta")
    assert finding in str(raised.value)
    saved = Path(git(integration, "rev-parse", "--absolute-git-dir")) / f"member-review-{head[:12]}.txt"
    assert saved.read_text(encoding="utf-8").strip() == finding
    assert str(saved) in str(raised.value)
    assert git(integration, "status", "--porcelain") == ""


def test_recover_takes_over_the_batch_worktrees_and_records_the_handoff(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    integration = world.start()
    member = world.member("zeta")
    claims: list[tuple[Path, bool]] = []

    def claim(path: Path, takeover: bool = False) -> str:
        claims.append((path, takeover))
        return "taken-over"

    monkeypatch.setattr("scripts.worktree_lifecycle_metadata.claim_worktree", claim)
    payload = bi.recover_owner(integration, **_recover_args())
    assert claims == [(integration.resolve(), True), (member.resolve(), True)]
    assert payload["worktree_takeover"] == [
        {"worktree": str(integration.resolve()), "status": "taken-over"},
        {"worktree": str(member.resolve()), "status": "taken-over"},
    ]
    handoffs = _ledger(integration)["handoffs"]
    assert len(handoffs) == 1
    assert handoffs[0]["expected_owner"] == "leader" and handoffs[0]["reason"] == "leader died"
    assert handoffs[0]["new_owner_generation"] == payload["arguments"]["new_owner_generation"]
    assert handoffs[0]["worktrees"] == payload["worktree_takeover"]


def test_recover_reports_a_takeover_that_could_not_be_recorded(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    integration = world.start()
    from scripts import worktree_lifecycle_metadata as lifecycle

    def claim(path: Path, takeover: bool = False) -> str:
        raise lifecycle.MetadataError("owner record unreadable")

    monkeypatch.setattr(lifecycle, "claim_worktree", claim)
    payload = bi.recover_owner(integration, **_recover_args())
    assert payload["worktree_takeover"][0]["status"] == "failed: owner record unreadable"


@pytest.mark.parametrize(
    "value",
    [
        "owner/name",
        "Owner/Name",
        "owner/name.git",
        "https://github.com/owner/name.git",
        "git@github.com:Owner/Name.git",
    ],
)
def test_repository_identity_ignores_form_case_and_git_suffix(value: str) -> None:
    assert bi.canonical_repository(value) == "owner/name"
    assert bi._same_repository(value, "https://github.com/OWNER/name")


@pytest.mark.parametrize("value", ["", "owner"])
def test_repository_identity_refuses_what_names_no_repository(value: str) -> None:
    with pytest.raises(bi.BatchError):
        bi.canonical_repository(value)


def _session(op: str, op_id: str, at: str) -> dict:
    return {"op": op, "op_id": op_id, "at": at, "actor": "mcp:omp-coding-agent:joe@host:shared"}


def test_land_refuses_member_tracker_writes_until_the_leader_accepts_them(world: World) -> None:
    world.start()
    world.deliver("zeta", {"src/a.py": "member\n"})
    world.tracker.session_page = 1
    world.tracker.item_sessions["zeta"] = [
        _session("update", "metadata", "2000-01-01T00:00:00Z"),
        _session("take", "leader-take", "2999-01-01T00:00:00Z"),
        _session("update", "member-note", "2999-01-01T00:00:01Z"),
        _session("prepare", "member-prepare", "2999-01-01T00:00:02Z"),
    ]
    before = world.head()

    with _raises("tracker writes between .* and its first landing .*member-note.*member-prepare") as raised:
        world.land("zeta")
    assert "metadata" not in str(raised.value) and "leader-take" not in str(raised.value)
    assert world.head() == before
    with _raises("member-prepare"):
        world.land("zeta", accepted_writes=frozenset({"member-note"}))

    result = world.land("zeta", accepted_writes=frozenset({"member-note", "member-prepare"}))

    assert result["status"] == "merged"
    ledger = bi.load_ledger(world.integration, BATCH)
    assert ledger["landings"]["zeta"]["accepted_tracker_writes"] == ["member-note", "member-prepare"]
    assert world.land("zeta")["status"] == "already-merged"


def test_land_refuses_a_member_write_in_the_same_second_as_the_worktree_creation(world: World) -> None:
    world.start()
    member = world.deliver("zeta", {"src/a.py": "member\n"})
    created = git(member, "config", "--worktree", "--get", f"{bi.CONFIG_PREFIX}.created-at")
    world.tracker.item_sessions["zeta"] = [_session("update", "same-second", created)]

    with _raises("same-second"):
        world.land("zeta")


def test_land_ignores_tracker_writes_present_when_the_member_worktree_was_created(world: World) -> None:
    world.start()
    world.tracker.item_sessions["zeta"] = [_session("update", "leader-metadata", "2999-01-01T00:00:00Z")]
    member = world.member("zeta")
    world.commit(member, {"src/a.py": "member\n"})
    world.check(member, ["tests/test_zeta.py"])
    world.tracker.item_sessions["zeta"].append(_session("update", "member-note", "2999-01-01T00:00:01Z"))

    with _raises("member-note") as raised:
        world.land("zeta")
    assert "leader-metadata" not in str(raised.value)


def test_a_leader_write_in_the_landing_second_does_not_block_resuming_the_landing(world: World) -> None:
    world.start()
    world.tracker.item_sessions["zeta"] = [_session("create", "created", "2000-01-01T00:00:00Z")]
    world.deliver("zeta", {"src/a.py": "member\n"})
    assert world.land("zeta")["status"] == "merged"
    landed_at = bi.load_ledger(world.integration, BATCH)["landings"]["zeta"]["landed_at"]
    world.tracker.item_sessions["zeta"].append(_session("invalidate", "leader-invalidate", landed_at))

    assert world.land("zeta")["status"] == "already-merged"


def test_land_undoes_the_merge_when_a_member_write_arrives_during_it(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.start()
    world.tracker.item_sessions["zeta"] = [_session("create", "created", "2000-01-01T00:00:00Z")]
    world.deliver("zeta", {"src/a.py": "member\n"})
    before = world.head()
    original = bi._git_merge

    def write_during_merge(*args, **kwargs):
        world.tracker.item_sessions["zeta"].append(_session("update", "late-note", "2999-01-01T00:00:00Z"))
        return original(*args, **kwargs)

    monkeypatch.setattr(bi, "_git_merge", write_during_merge)
    with _raises("late-note"):
        world.land("zeta")

    assert world.head() == before
    assert "zeta" not in bi.load_ledger(world.integration, BATCH)["landings"]


def test_land_checks_a_member_without_a_creation_time_from_the_batch_start(world: World) -> None:
    world.start()
    member = world.deliver("zeta", {"src/a.py": "member\n"})
    git(member, "config", "--worktree", "--unset", f"{bi.CONFIG_PREFIX}.created-at")
    world.tracker.item_sessions["zeta"] = [_session("update", "leader-metadata", "2000-01-01T00:00:05Z")]

    with _raises("between 2000-01-01T00:00:00Z and its first landing .*leader-metadata"):
        world.land("zeta")
    result = world.land("zeta", accepted_writes=frozenset({"leader-metadata"}))

    assert result["status"] == "merged"
    assert any("records no creation time" in warning for warning in result["warnings"])


def test_member_check_uses_an_overridden_preflight_command(world: World) -> None:
    world.start()
    member = world.member("zeta")
    world.commit(member, {"src/a.py": "member\n"})
    bi.check_member(member, ["tests/test_zeta.py"], preflight_command=["make", "pr-preflight", "SCOPE=fast"])
    assert any(call[0][:3] == ["make", "pr-preflight", "SCOPE=fast"] for call in world.calls)
    record = bi._read_check(member, git(member, "rev-parse", "HEAD"), world.head())
    assert record is not None and record["preflight"] == ["make", "pr-preflight", "SCOPE=fast", f"BASE={world.head()}"]


def test_land_prints_a_warning_when_the_recorded_preflight_was_not_the_default(world: World) -> None:
    world.start()
    member = world.member("zeta")
    world.commit(member, {"src/a.py": "member\n"})
    bi.check_member(member, ["tests/test_zeta.py"], preflight_command=["make", "pr-preflight", "SCOPE=fast"])
    warnings = world.land("zeta")["warnings"]
    assert any("preflight was not the default" in warning and "SCOPE=fast" in warning for warning in warnings)


def test_land_prints_no_preflight_warning_for_the_default_preflight(world: World) -> None:
    world.start()
    world.deliver("zeta", {"src/a.py": "member\n"})
    assert not [w for w in world.land("zeta")["warnings"] if "preflight" in w]


def test_start_fetches_develop_before_resolving_the_start_head(world: World) -> None:
    (world.seed / "other").mkdir(exist_ok=True)
    (world.seed / "other" / "dev.txt").write_text("develop moved\n")
    git(world.seed, "add", "other/dev.txt")
    git(world.seed, "commit", "-qm", "develop moves")
    git(world.seed, "push", "-q", "origin", "develop")
    moved = git(world.seed, "rev-parse", "HEAD")
    assert git(world.primary, "rev-parse", "refs/remotes/origin/develop") != moved
    integration = world.start()
    assert world.payload["arguments"]["start_head"] == moved == git(integration, "rev-parse", "HEAD")


def test_land_merges_the_checked_member_head_even_if_the_member_branch_moves_during_landing(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.start()
    member = world.deliver("zeta", {"src/a.py": "member\n"})
    checked = git(member, "rev-parse", "HEAD")
    original = bi._preflight_gate

    def move_member_branch(*args, **kwargs):
        git(member, "commit", "-q", "--allow-empty", "-m", "late")
        return original(*args, **kwargs)

    monkeypatch.setattr(bi, "_preflight_gate", move_member_branch)
    world.land("zeta")
    assert git(world.integration, "rev-parse", "HEAD^2") == checked


def test_a_stale_or_unrevisioned_tracker_read_is_refused_before_anything_lands(world: World) -> None:
    integration = world.start()
    world.deliver("zeta", {"src/a.py": "member\n"})
    world.tracker.stale = True
    for action in (
        lambda: world.land("zeta"),
        lambda: bi.batch_status(integration),
        lambda: bi.build_evidence(integration),
    ):
        with _raises("stale or carries no revision"):
            action()
    assert git(integration, "rev-list", "--merges", "--count", "HEAD") == "0"


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
    world.start(dependencies={"alpha": ["zeta"]})
    world.deliver("zeta", {"src/a.py": "member\n"})
    world.land_and_prepare("zeta")
    world.deliver("alpha", {"src/b.py": "member\n"})
    before = world.head()
    with _raises("take it now, before landing"):
        world.land("alpha")
    assert world.head() == before
    world.tracker.claims["alpha"] = "leader"
    result = world.land("alpha")
    (prepare,) = result["payload"]["steps"]
    assert prepare["tool"] == "prepare" and prepare["arguments"]["implementation_dependencies"] == ["zeta"]


def test_land_refuses_when_the_tracker_carries_no_batch_metadata_for_the_member(world: World) -> None:
    world.start()
    world.deliver("zeta", {"src/a.py": "member\n"})
    del world.tracker.item_batch["zeta"]
    before = world.head()
    with _raises("no batch metadata for member 'zeta'"):
        world.land("zeta")
    assert world.head() == before


def test_a_member_without_dependencies_lands_with_an_empty_registered_dependency_list(world: World) -> None:
    world.start(dependencies={"alpha": ["zeta"]})
    world.deliver("zeta", {"src/a.py": "member\n"})
    result = world.land("zeta")
    steps = {step["tool"]: step["arguments"] for step in result["payload"]["steps"]}
    assert steps["prepare"]["implementation_dependencies"] == [] and "take" in steps


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


def test_land_refuses_a_findings_result(world: World) -> None:
    world.start()
    world.deliver("zeta", {"src/a.py": "member\n"})
    world.review_rc = 1
    with _raises("blocks landing"):
        world.land("zeta")


def test_land_accepts_a_not_reviewed_result_for_an_exempt_only_member(world: World) -> None:
    world.start()
    world.deliver("zeta", {"docs/note.md": "changed\n"})
    result = world.land("zeta")
    assert result["payload"]["steps"][1]["arguments"]["verification"]["review"]["outcome"] == "not-reviewed"


def test_land_refuses_paths_outside_the_members_frozen_scope(world: World) -> None:
    integration = world.start()
    world.deliver("zeta", {"src/a.py": "member\n"}, tests=["tests/test_zeta.py"])
    ledger_path = integration / ".todo-batch" / BATCH / "ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["scope"]["zeta"] = ["tests/*"]
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    with _raises("outside its frozen scope: src/a.py"):
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


def _merge_by_hand(integration: Path, member: Path, extra: dict[str, str] | None = None) -> str:
    member_head = git(member, "rev-parse", "HEAD")
    git(integration, "merge", "--no-ff", "--no-commit", member_head)
    for name, text in (extra or {}).items():
        (integration / name).write_text(text)
        git(integration, "add", "--", name)
    git(integration, "commit", "-qm", "Merge by hand")
    return git(integration, "rev-parse", "HEAD")


def test_a_hand_made_merge_carrying_an_extra_edit_is_refused_and_never_prepared(world: World) -> None:
    integration = world.start()
    member = world.deliver("zeta", {"src/a.py": "member\n"})
    merged = _merge_by_hand(integration, member, {"src/b.py": "smuggled\n"})
    with _raises("carries edits the member never checked"):
        world.land("zeta")
    assert world.head() == merged
    status = bi.batch_status(integration)
    assert any("cannot be verified" in step for step in status["next"])
    with _raises("carries edits the member never checked"):
        world.land("zeta")


def test_a_hand_made_merge_whose_check_trusted_a_non_develop_commit_is_refused(world: World) -> None:
    integration = world.start()
    member = world.deliver("zeta", {"src/a.py": "member\n"})
    git(integration, "update-ref", "-d", "refs/remotes/origin/develop")
    _merge_by_hand(integration, member)
    with _raises("not an ancestor of origin/develop"):
        world.land("zeta")


def test_a_leader_commit_after_a_merge_that_is_not_recorded_refuses_the_prepare_payload(world: World) -> None:
    integration = world.start()
    world.deliver("zeta", {"src/a.py": "member\n"})
    world.land("zeta")
    world.commit(integration, {"src/b.py": "leader fix\n"}, "fix: leader commit")
    with _raises("unrecorded commits after the merge of zeta"):
        world.land("zeta")


def test_an_interrupted_hand_made_merge_binds_the_prepare_payload_to_the_merge_commit(world: World) -> None:
    integration = world.start()
    member = world.deliver("zeta", {"src/a.py": "member\n"})
    merged = _merge_by_hand(integration, member)
    again = world.land("zeta")
    assert again["status"] == "already-merged"
    assert again["payload"]["steps"][-1]["arguments"]["integration_head"] == merged


def test_a_genuinely_interrupted_landing_with_no_ledger_entry_resumes_from_git(world: World) -> None:
    integration = world.start()
    member = world.deliver("zeta", {"src/a.py": "member\n"})
    merged = _merge_by_hand(integration, member)
    resumed = world.land("zeta")
    assert resumed["status"] == "already-merged"
    assert resumed["payload"]["steps"][-1]["arguments"]["integration_head"] == merged
    assert git(integration, "rev-list", "--merges", "--count", "HEAD") == "1"


def test_make_member_check_accepts_no_review_override_variable(tmp_path: Path) -> None:
    recorder = tmp_path / "record.py"
    recorder.write_text(
        "import json, sys\nfrom pathlib import Path\nPath('argv.json').write_text(json.dumps(sys.argv[1:]))\n"
    )
    makefile = Path(bi.__file__).resolve().parents[1] / "make" / "batch.mk"
    proc = subprocess.run(
        [
            "make",
            "-f",
            str(makefile),
            "batch-member-check",
            f"BATCH_SCRIPT={sys.executable} {recorder}",
            "REVIEW_CMD=true",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "--review-command" not in json.loads((tmp_path / "argv.json").read_text(encoding="utf-8"))


def test_the_member_check_command_line_refuses_a_review_command_option() -> None:
    with pytest.raises(SystemExit) as raised:
        bi.main(["member-check", "--review-command", "true"])
    assert raised.value.code == 2
    with pytest.raises(TypeError):
        bi.check_member(Path("."), [], review_command=["true"])


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


PYTEST = ["uv", "run", "--", "python", "-m", "pytest", "-n", "0", "-q"]


def _develop_deletes(world: World, *paths: str) -> tuple[Path, str]:
    integration = world.start()
    base = world.head()
    git(world.seed, "rm", "-q", *paths)
    git(world.seed, "commit", "-qm", "develop deletes tests")
    git(world.seed, "push", "-q", "origin", "develop")
    git(integration, "fetch", "-q", "origin")
    git(integration, "merge", "-q", "--no-edit", bi.DEVELOP_REF)
    return integration, base


def _develop_commit_merged(world: World, integration: Path, *commands: list[str]) -> None:
    for command in commands:
        git(world.seed, *command)
    git(world.seed, "commit", "-qm", "develop change")
    git(world.seed, "push", "-q", "origin", "develop")
    git(integration, "fetch", "-q", "origin")
    git(integration, "merge", "-q", "--no-edit", bi.DEVELOP_REF)


def _rerun_alpha(integration: Path) -> dict:
    recorded = [*PYTEST, "tests/test_zeta.py", "tests/test_alpha.py"]
    return bi._rerun(integration, _ledger(integration), "zeta", recorded, "member-tests", "fix:x")


def test_a_rerun_does_not_drop_a_test_develop_renamed(world: World) -> None:
    integration = world.start()
    _develop_commit_merged(world, integration, ["mv", "tests/test_alpha.py", "tests/test_alpha_new.py"])
    entry = _rerun_alpha(integration)
    assert entry["dropped_paths"] == []


def test_a_rerun_does_not_drop_a_test_develop_replaced_by_a_heavily_edited_move(world: World) -> None:
    integration = world.start()
    (world.seed / "tests" / "test_alpha_new.py").write_text("def test_other():\n    assert 1 + 1 == 2\n")
    _develop_commit_merged(world, integration, ["rm", "-q", "tests/test_alpha.py"], ["add", "tests/test_alpha_new.py"])
    entry = _rerun_alpha(integration)
    assert entry["dropped_paths"] == []


def test_a_rerun_does_not_drop_a_test_deleted_in_a_commit_adding_an_unrelated_test(world: World) -> None:
    integration = world.start()
    (world.seed / "tests" / "test_unrelated.py").write_text("def test_u():\n    assert True\n")
    _develop_commit_merged(world, integration, ["rm", "-q", "tests/test_alpha.py"], ["add", "tests/test_unrelated.py"])
    entry = _rerun_alpha(integration)
    assert entry["dropped_paths"] == []


def test_a_rerun_drops_a_test_deleted_in_a_commit_adding_only_a_changelog_file(world: World) -> None:
    integration = world.start()
    (world.seed / "changelog.d").mkdir()
    (world.seed / "changelog.d" / "retire.json").write_text("{}\n")
    _develop_commit_merged(world, integration, ["rm", "-q", "tests/test_alpha.py"], ["add", "changelog.d/retire.json"])
    entry = _rerun_alpha(integration)
    assert entry["dropped_paths"] == ["tests/test_alpha.py"]


def test_a_rerun_drops_a_develop_deleted_test_path_runs_the_rest_and_records_both_commands(world: World) -> None:
    integration, _ = _develop_deletes(world, "tests/test_alpha.py")
    recorded = [*PYTEST, "tests/test_zeta.py::test_one", "tests/test_alpha.py"]
    world.calls.clear()
    entry = bi._rerun(integration, _ledger(integration), "zeta", recorded, "member-tests", "overlap:alpha")
    assert entry["status"] == "passed"
    assert entry["command"] == recorded
    assert entry["ran_command"] == [*PYTEST, "tests/test_zeta.py::test_one"]
    assert entry["dropped_paths"] == ["tests/test_alpha.py"]
    assert [call[0] for call in world.calls if "pytest" in call[0]] == [entry["ran_command"]]


def test_a_rerun_whose_test_paths_are_all_deleted_fails_closed_without_running_pytest(world: World) -> None:
    integration, _ = _develop_deletes(world, "tests/test_alpha.py", "tests/test_mid.py")
    recorded = [*PYTEST, "tests/test_alpha.py", "tests/test_mid.py"]
    world.calls.clear()
    entry = bi._rerun(integration, _ledger(integration), "zeta", recorded, "member-tests", "final-evidence")
    assert entry["status"] == "failed"
    assert entry["dropped_paths"] == ["tests/test_alpha.py", "tests/test_mid.py"]
    assert "absent from the tree" in entry["output"]
    assert not [call for call in world.calls if "pytest" in call[0]]


def test_a_rerun_fails_closed_when_only_an_option_value_names_a_test_after_dropping(world: World) -> None:
    integration, _ = _develop_deletes(world, "tests/test_alpha.py")
    world.commit(integration, {"tests/test_keep.py": "kept\n"}, "add test")
    recorded = [*PYTEST[:-1], "--deselect", "tests/test_keep.py::test_one", "-q", "tests/test_alpha.py"]
    world.calls.clear()
    entry = bi._rerun(integration, _ledger(integration), "zeta", recorded, "member-tests", "final-evidence")
    assert entry["status"] == "failed"
    assert entry["dropped_paths"] == ["tests/test_alpha.py"]
    assert not [call for call in world.calls if "pytest" in call[0]]


def test_a_rerun_never_drops_a_missing_argument_that_is_not_a_test_path(world: World) -> None:
    integration = world.start()
    world.commit(integration, {"tests/test_keep.py": "kept\n"}, "add test")
    recorded = [*PYTEST, "--rootdir", "missing_dir", "tests/test_keep.py"]
    entry = bi._rerun(integration, _ledger(integration), "zeta", recorded, "member-tests", "fix:x")
    assert entry["ran_command"] == recorded and entry["dropped_paths"] == []


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


def _land_with_a_failing_overlap(world: World) -> tuple[Path, str]:
    integration = world.start()
    world.deliver("zeta", {"src/a.py": "zeta change\n"})
    world.land_and_prepare("zeta")
    world.deliver("alpha", {"src/a.py": "alpha change\n"})
    before = world.head()
    world.failing_tests.add("tests/test_zeta.py")
    with _raises("overlap rerun failed for \\['zeta'\\]"):
        world.land("alpha")
    return integration, before


def test_a_failed_overlap_rerun_is_not_pushed_to_the_backup_branch(world: World) -> None:
    integration, before = _land_with_a_failing_overlap(world)
    assert git(world.origin, "rev-parse", f"refs/heads/feat/{BATCH}") == before
    assert world.head() != before and bi._is_clean(integration)


def test_landing_again_after_a_failed_overlap_rerun_is_refused_and_never_offers_a_payload(world: World) -> None:
    _, _ = _land_with_a_failing_overlap(world)
    merged = world.head()
    with _raises("overlap rerun failed for \\['zeta'\\].*reset the integration branch"):
        world.land("alpha")
    assert world.head() == merged


def test_resetting_after_a_failed_overlap_rerun_allows_landing_again_once_it_passes(world: World) -> None:
    integration, before = _land_with_a_failing_overlap(world)
    git(integration, "reset", "--hard", before)
    world.failing_tests.clear()
    result = world.land("alpha")
    assert result["status"] == "merged" and result["reruns"][0]["status"] == "passed"
    assert world.land("alpha")["status"] == "already-merged"
    assert result["merge_head"] == world.head()


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


def test_land_refuses_a_lock_bump_with_the_theseus_reasons_verbatim(world: World) -> None:
    world.start()
    world.deliver("zeta", {"src/a.py": "member\n"})
    reason = "contains a Theseus promotion; land the lock change as its own pull request"
    world.preflight_code = 3
    world.preflight_reasons = [reason]
    before = world.head()
    with _raises(f"not reviewable:\n{reason}"):
        world.land("zeta")
    assert world.head() == before


def test_land_refuses_an_oversize_landing_before_merging(world: World) -> None:
    world.start()
    world.deliver("zeta", {"src/a.py": "member\n"})
    reason = "needs about 900000 tokens of reading, above every reviewer's capacity; split"
    world.preflight_code = 3
    world.preflight_reasons = [reason]
    before = world.head()
    with _raises(f"is not reviewable:\n{reason}"):
        world.land("zeta")
    assert world.head() == before


def test_land_refuses_when_the_preflight_cannot_determine_reviewability(world: World) -> None:
    world.start()
    world.deliver("zeta", {"src/a.py": "member\n"})
    reason = "the model catalog cannot be read, so reviewer capacity cannot be determined"
    world.preflight_code = 2
    world.preflight_reasons = [reason]
    before = world.head()
    with _raises(f"could not determine whether.*:\n{reason}"):
        world.land("zeta")
    assert world.head() == before


def test_land_treats_an_unreadable_preflight_answer_as_undetermined(world: World) -> None:
    world.start()
    world.deliver("zeta", {"src/a.py": "member\n"})
    world.preflight_stdout = "not json"
    with _raises("could not determine whether"):
        world.land("zeta")


def test_land_passes_the_member_head_base_policy_and_json_flag_to_the_preflight(world: World) -> None:
    world.start()
    member = world.deliver("zeta", {"src/a.py": "member\n"})
    world.land("zeta")
    assert world.preflight_arguments == [
        [
            "--policy",
            bi.POLICY_PATH,
            "--base-ref",
            bi.DEVELOP_REF,
            "--head",
            git(member, "rev-parse", "HEAD"),
            "--json",
        ]
    ]


def test_land_reports_the_preflight_advisories_as_warnings(world: World) -> None:
    world.start()
    world.deliver("zeta", {"src/a.py": "member\n"})
    world.preflight_advisories = ["small: the change needs about 200000 tokens of reading"]
    result = world.land("zeta")
    assert "small: the change needs about 200000 tokens of reading" in result["warnings"]


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


def test_a_rerun_does_not_drop_a_test_the_batch_itself_deleted_while_develop_still_has_it(world: World) -> None:
    integration = world.start()
    git(integration, "rm", "-q", "tests/test_alpha.py")
    git(integration, "commit", "-qm", "delete own test")
    recorded = [*PYTEST, "tests/test_alpha.py", "tests/test_zeta.py"]
    entry = bi._rerun(integration, _ledger(integration), "zeta", recorded, "member-tests", "fix:x")
    assert entry["dropped_paths"] == [] and entry["ran_command"] == recorded


def test_a_rerun_does_not_drop_a_test_the_batch_added_and_later_deleted(world: World) -> None:
    integration = world.start()
    world.commit(integration, {"tests/test_new.py": "new\n"}, "add test")
    git(integration, "rm", "-q", "tests/test_new.py")
    git(integration, "commit", "-qm", "delete added test")
    recorded = [*PYTEST, "tests/test_new.py", "tests/test_zeta.py"]
    entry = bi._rerun(integration, _ledger(integration), "zeta", recorded, "member-tests", "fix:x")
    assert entry["dropped_paths"] == [] and entry["ran_command"] == recorded


@pytest.mark.parametrize(
    "tail",
    [
        ["--deselect", "tests/test_alpha.py::t", "tests/test_zeta.py"],
        ["tests/test_zeta.py", "--ignore", "tests/test_alpha.py"],
        ["--ignore", "tests/test_alpha.py", "tests/test_zeta.py", "tests/test_alpha.py"],
    ],
)
def test_a_rerun_never_drops_a_path_outside_the_trailing_positional_run(world: World, tail: list[str]) -> None:
    integration, _ = _develop_deletes(world, "tests/test_alpha.py")
    recorded = [*PYTEST, *tail]
    entry = bi._rerun(integration, _ledger(integration), "zeta", recorded, "member-tests", "fix:x")
    assert entry["dropped_paths"] == [] and entry["ran_command"] == recorded


def test_a_rerun_drops_a_file_develop_added_after_the_start_merged_in_and_later_deleted(world: World) -> None:
    integration = world.start()
    git(world.seed, "checkout", "-q", "-b", "adds")
    (world.seed / "tests" / "test_new.py").write_text("def test_n():\n    assert True\n")
    git(world.seed, "add", "tests/test_new.py")
    git(world.seed, "commit", "-qm", "develop adds a test")
    git(world.seed, "push", "-q", "origin", "adds:develop")
    git(integration, "fetch", "-q", "origin")
    git(integration, "merge", "-q", "--no-ff", "--no-edit", bi.DEVELOP_REF)
    ledger = _ledger(integration)
    ledger["advances"].append({"kind": "develop-merge", "head": world.head()})
    git(world.seed, "rm", "-q", "tests/test_new.py")
    git(world.seed, "commit", "-qm", "develop deletes it")
    git(world.seed, "push", "-q", "origin", "adds:develop")
    git(integration, "fetch", "-q", "origin")
    git(integration, "merge", "-q", "--no-ff", "--no-edit", bi.DEVELOP_REF)
    recorded = [*PYTEST, "tests/test_new.py", "tests/test_zeta.py"]
    entry = bi._rerun(integration, ledger, "zeta", recorded, "member-tests", "fix:x")
    assert entry["dropped_paths"] == ["tests/test_new.py"]


def test_a_rerun_does_not_drop_a_reused_filename_develop_deleted_before_the_start(world: World) -> None:
    (world.seed / "tests" / "test_old.py").write_text("def test_o():\n    assert True\n")
    git(world.seed, "add", "tests/test_old.py")
    git(world.seed, "commit", "-qm", "develop adds a test")
    git(world.seed, "rm", "-q", "tests/test_old.py")
    git(world.seed, "commit", "-qm", "develop deletes it")
    git(world.seed, "push", "-q", "origin", "develop")
    git(world.primary, "fetch", "-q", "origin")
    integration = world.start()
    world.commit(integration, {"tests/test_old.py": "member re-adds\n"}, "member reuses the name")
    git(integration, "rm", "-q", "tests/test_old.py")
    git(integration, "commit", "-qm", "member deletes it")
    recorded = [*PYTEST, "tests/test_old.py", "tests/test_zeta.py"]
    entry = bi._rerun(integration, _ledger(integration), "zeta", recorded, "member-tests", "fix:x")
    assert entry["dropped_paths"] == [] and entry["ran_command"] == recorded


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
    assert world.preflight_heads[-1] == world.head()
    ledger = _ledger(integration)
    assert ledger["advances"][-1]["kind"] == "develop-merge"


def test_advance_fix_runs_the_preflight_at_the_new_head(world: World) -> None:
    integration = _landed_pair(world)
    fix_head = world.commit(integration, {"src/a.py": "fixed\n"}, "fix")
    world.preflight_heads.clear()
    bi.advance_head(integration, "fix")
    assert world.preflight_heads == [fix_head]


def test_advance_fix_refuses_a_head_the_preflight_rejects_and_records_nothing(world: World) -> None:
    integration = _landed_pair(world)
    world.commit(integration, {"src/a.py": "fixed\n"}, "fix")
    reason = "the diff cannot be built"
    world.preflight_code = 2
    world.preflight_reasons = [reason]
    world.calls.clear()
    with _raises(f"could not determine whether.*:\n{reason}"):
        bi.advance_head(integration, "fix")
    assert not [call for call in world.calls if "pytest" in call[0]]
    assert _ledger(integration)["advances"] == []


def test_advance_develop_merge_refuses_a_head_the_preflight_rejects(world: World) -> None:
    integration = _landed_pair(world)
    (world.seed / "other").mkdir()
    (world.seed / "other" / "dev.txt").write_text("develop moved\n")
    git(world.seed, "add", "other/dev.txt")
    git(world.seed, "commit", "-qm", "develop moves")
    git(world.seed, "push", "-q", "origin", "develop")
    reason = "contains a Theseus promotion; land the lock change as its own pull request"
    world.preflight_code = 3
    world.preflight_reasons = [reason]
    with _raises(f"is not reviewable:\n{reason}"):
        bi.advance_head(integration, "develop-merge")
    assert _ledger(integration)["advances"] == []


def test_status_shows_the_preflight_reasons_without_refusing(world: World) -> None:
    integration = _landed_pair(world)
    reason = "needs about 900000 tokens of reading, above every reviewer's capacity; split"
    world.preflight_code = 3
    world.preflight_reasons = [reason]
    status = bi.batch_status(integration)
    assert status["review_preflight"] == {
        "state": "not-reviewable",
        "exit": 3,
        "reasons": [reason],
        "advisories": [],
    }


def test_status_reports_an_undetermined_preflight_when_the_install_is_refused(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    integration = _landed_pair(world)

    def refuse(repo):
        raise bi.BatchError("the Theseus install for the review preflight was refused: no network")

    monkeypatch.setattr(bi, "_theseus_launcher", refuse)
    status = bi.batch_status(integration)["review_preflight"]
    assert status["state"] == "undetermined" and status["exit"] is None
    assert "no network" in status["reasons"][0]


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


def test_status_lists_members_in_registered_order_with_preflight_and_acceptance_state(world: World) -> None:
    integration = world.start()
    world.deliver("zeta", {"src/a.py": "zeta change\n"})
    world.land_and_prepare("zeta")
    world.deliver("alpha", {"src/a.py": "alpha change\n"})
    world.land_and_prepare("alpha")
    status = bi.batch_status(integration)
    assert [m["member"] for m in status["members"]] == MEMBERS
    assert status["acceptance_required"] == ["zeta"] and status["acceptance_stale"] == []
    assert status["review_preflight"] == {"state": "reviewable", "exit": 0, "reasons": [], "advisories": []}
    assert world.preflight_heads[-1] == world.head()
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
    base = [*bi.TRACTABLE_COMMAND, "batch", "show", BATCH]
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


@pytest.mark.parametrize(
    ("target", "variables"),
    [
        (
            "batch-land",
            {"MEMBER": '$(touch pwned) `touch pwned` "; touch pwned; "', "ACCEPT_WRITES": "$(shell touch pwned)x"},
        ),
        ("batch-advance", {"REASON": 'x"; touch pwned; echo "'}),
        ("batch-member-create", {"MEMBER": "m`touch pwned`", "WORKTREE_PATH": "$(touch pwned)"}),
        (
            "batch-member-check",
            {"TESTS": "t`touch pwned`", "PREFLIGHT_CMD": 'p"; touch pwned; "'},
        ),
        (
            "batch-recover",
            {
                "EXPECTED_OWNER": "w`touch pwned`",
                "EXPECTED_OWNER_GENERATION": "$(touch pwned)",
                "REASON": 'x"; touch pwned; echo "',
            },
        ),
    ],
)
def test_make_targets_pass_user_variables_to_the_script_verbatim_and_never_through_the_shell(
    tmp_path: Path, target: str, variables: dict[str, str]
) -> None:
    recorder = tmp_path / "record.py"
    recorder.write_text(
        "import json, sys\nfrom pathlib import Path\nPath('argv.json').write_text(json.dumps(sys.argv[1:]))\n"
    )
    makefile = Path(bi.__file__).resolve().parents[1] / "make" / "batch.mk"
    proc = subprocess.run(
        [
            "make",
            "-f",
            str(makefile),
            target,
            f"BATCH_SCRIPT={sys.executable} {recorder}",
            *(f"{k}={v}" for k, v in variables.items()),
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert not (tmp_path / "pwned").exists()
    argv = json.loads((tmp_path / "argv.json").read_text(encoding="utf-8"))
    assert all(value in argv for value in variables.values())


def test_verify_accepts_every_commit_inside_a_recorded_multi_commit_fix_and_refuses_a_trailing_one(
    world: World,
) -> None:
    integration = _landed_pair(world)
    world.commit(integration, {"src/a.py": "first fix\n"}, "fix one")
    world.commit(integration, {"src/a.py": "second fix\n"}, "fix two")
    fix = bi.advance_head(integration, "fix")
    world.tracker.advance(fix["payload"]["arguments"])
    assert bi.verify_integration(integration)["ok"] is True
    stray = world.commit(integration, {"src/a.py": "trailing\n"}, "trailing")
    report = bi.verify_integration(integration)
    assert report["ok"] is False and report["unrecorded_first_parent_commits"] == [stray]


def test_verify_refuses_commits_inside_a_recorded_interval_that_is_not_a_fix(world: World) -> None:
    integration = _landed_pair(world)
    hidden = world.commit(integration, {"src/a.py": "hidden\n"}, "hidden")
    head = world.commit(integration, {"src/a.py": "fix\n"}, "fix")
    world.tracker.advance(
        {"expected_head": world.tracker.head, "head": head, "reason": "develop-merge", "changed_files": ["src/a.py"]}
    )
    report = bi.verify_integration(integration)
    assert report["ok"] is False and report["unrecorded_first_parent_commits"] == [hidden]


def test_a_fix_advance_with_a_bound_final_pr_publishes_nothing_and_orders_the_next_steps(world: World) -> None:
    integration = _landed_pair(world)
    old = world.head()
    world.tracker.bind(old)
    fix_head = world.commit(integration, {"src/a.py": "fixed\n"}, "fix")
    result = bi.advance_head(integration, "fix")
    assert git(world.origin, "rev-parse", f"refs/heads/feat/{BATCH}") != fix_head
    assert result["backup_push"].startswith("skipped")
    steps = result["next"].splitlines()
    assert f"make pr-landing-withdraw PR=7 HEAD={old}" in steps[0]
    assert "advance_batch_head" in steps[1]
    assert steps[2].split(": ", 1)[1] == f"git push origin {fix_head}:refs/heads/feat/{BATCH}"
    assert f"bind_batch_pr head={fix_head} expected_previous_head={old}" in steps[3]
    assert "make batch-evidence" in steps[4]
    assert "rearm" in steps[5]


def _overlapping_hand_merge(world: World) -> tuple[Path, str]:
    integration = world.start()
    world.deliver("zeta", {"src/a.py": "zeta change\n"})
    world.land_and_prepare("zeta")
    member = world.deliver("alpha", {"src/a.py": "alpha change\n"})
    return integration, _merge_by_hand(integration, member)


def test_an_interrupted_landing_executes_the_missing_overlap_rerun_instead_of_assuming_it_passed(
    world: World,
) -> None:
    integration, merged = _overlapping_hand_merge(world)
    assert "alpha" not in _ledger(integration)["landings"]
    zeta_command = world.tracker.receipts["zeta"]["verification"]["command"]
    world.calls.clear()
    result = world.land("alpha")
    assert result["status"] == "already-merged"
    assert [call for call in world.calls if call[0] == zeta_command] == [(zeta_command, integration)]
    entry = _ledger(integration)["acceptance"]["zeta"][-1]
    assert entry["status"] == "passed" and entry["revision"] == merged and entry["reason"] == "overlap:alpha"


def test_an_interrupted_landing_whose_missing_overlap_rerun_fails_offers_no_payload(world: World) -> None:
    integration, _ = _overlapping_hand_merge(world)
    world.failing_tests.add("tests/test_zeta.py")
    with _raises("overlap rerun failed for \\['zeta'\\]"):
        world.land("alpha")
    assert _ledger(integration)["acceptance"]["zeta"][-1]["status"] == "failed"


def test_a_ledger_with_a_landing_but_no_acceptance_row_still_reruns_the_overlap_on_retry(world: World) -> None:
    integration = world.start()
    world.deliver("zeta", {"src/a.py": "zeta change\n"})
    world.land_and_prepare("zeta")
    world.deliver("alpha", {"src/a.py": "alpha change\n"})
    world.land("alpha")
    ledger = _ledger(integration)
    ledger["acceptance"] = {}
    bi.save_ledger(integration, ledger)
    world.failing_tests.add("tests/test_zeta.py")
    with _raises("overlap rerun failed for \\['zeta'\\]"):
        world.land("alpha")


def test_a_rebuilt_ledger_carries_no_fabricated_acceptance_rows(world: World) -> None:
    integration = world.start()
    world.deliver("zeta", {"src/a.py": "zeta change\n"})
    world.land_and_prepare("zeta")
    world.deliver("alpha", {"src/a.py": "alpha change\n"})
    world.land_and_prepare("alpha")
    assert _ledger(integration)["acceptance"]["zeta"]
    for path in (integration / ".todo-batch" / BATCH).iterdir():
        path.unlink()
    status = bi.batch_status(integration)
    assert _ledger(integration)["acceptance"] == {}
    assert status["acceptance_stale"] == ["zeta"]


def _recover_args(**overrides: object) -> dict:
    return {"expected_owner": "leader", "expected_owner_generation": "g" * 32, "reason": "leader died", **overrides}


def test_recover_mints_a_generation_into_the_ledger_and_marks_the_checkout_before_printing(world: World) -> None:
    integration = world.start()
    old_generation = _ledger(integration)["owner_generation"]
    for path in (integration / ".todo-batch" / BATCH).iterdir():
        path.unlink()
    git(integration, "config", "--worktree", "--unset", "benchbox.batch.role")
    git(integration, "config", "--worktree", "--unset", "benchbox.batch.id")
    payload = bi.recover_owner(integration, **_recover_args())
    arguments = payload["arguments"]
    generation = arguments["new_owner_generation"]
    assert len(generation) == 32 and int(generation, 16) >= 0 and generation != old_generation
    assert payload["tool"] == "recover_batch_owner"
    assert arguments == {
        "batch_id": BATCH,
        "expected_owner": "leader",
        "expected_owner_generation": "g" * 32,
        "new_owner_generation": generation,
        "reason": "leader died",
    }
    ledger = _ledger(integration)
    assert ledger["owner_generation"] == generation and ledger["members"] == MEMBERS
    assert bi.role_of(integration) == "integration" and bi.require_role(integration, "integration") == BATCH
    assert generation not in json.dumps(world.calls, default=str)


def _relocated_checkout(world: World, integration: Path) -> Path:
    git(integration, "checkout", "-q", "--detach")
    target = world.tmp / "relocated"
    git(world.primary, "worktree", "add", "-q", str(target), f"feat/{BATCH}")
    return target


def test_recover_with_relocation_prints_the_expected_and_new_worktree_and_head(world: World) -> None:
    integration = world.start()
    target = _relocated_checkout(world, integration)
    payload = bi.recover_owner(target, **_recover_args(), relocate=True)
    arguments = payload["arguments"]
    assert arguments["expected_integration_worktree"] == str(integration)
    assert arguments["expected_integration_head"] == world.tracker.head
    assert arguments["integration_worktree"] == str(target.resolve())
    ledger = json.loads((target / ".todo-batch" / BATCH / "ledger.json").read_text(encoding="utf-8"))
    assert ledger["owner_generation"] == arguments["new_owner_generation"]
    assert ledger["integration_worktree"] == str(target.resolve())
    assert bi.require_role(target, "integration") == BATCH


def test_recover_without_relocation_refuses_a_checkout_that_is_not_the_registered_worktree(world: World) -> None:
    integration = world.start()
    target = _relocated_checkout(world, integration)
    with _raises("RELOCATE=1"):
        bi.recover_owner(target, **_recover_args())
    assert bi.role_of(target) is None


def test_recover_refuses_a_dirty_checkout_the_wrong_branch_head_or_repository(world: World) -> None:
    integration = world.start()
    (integration / "scratch.txt").write_text("dirty\n")
    with _raises("not clean"):
        bi.recover_owner(integration, **_recover_args())
    (integration / "scratch.txt").unlink()
    original = _ledger(integration)["owner_generation"]
    git(integration, "remote", "set-url", "origin", "https://example.com/other/repo.git")
    with _raises("not the registered repository"):
        bi.recover_owner(integration, **_recover_args())
    git(integration, "remote", "set-url", "origin", str(world.origin))
    world.commit(integration, {"src/a.py": "ahead\n"}, "ahead of tracker")
    with _raises("not the tracker integration head"):
        bi.recover_owner(integration, **_recover_args())
    git(integration, "checkout", "-q", "-b", "scratch")
    with _raises("not the registered"):
        bi.recover_owner(integration, **_recover_args(), batch_id=BATCH)
    assert _ledger(integration)["owner_generation"] == original


def test_cli_recover_prints_the_payload(world: World, capsys: pytest.CaptureFixture[str]) -> None:
    integration = world.start()
    code = bi.main(
        [
            "--worktree",
            str(integration),
            "recover",
            "--expected-owner",
            "leader",
            "--expected-owner-generation",
            "g" * 32,
            "--reason",
            "leader died",
        ]
    )
    assert code == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["arguments"]["new_owner_generation"] == _ledger(integration)["owner_generation"]
