from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import worktree_finish
from scripts.worktree_lifecycle_metadata import init_metadata
from tests.unit.scripts.batch_loop_world import BATCH, World, bi, build_world, done, git

pytestmark = [pytest.mark.unit, pytest.mark.medium]

PR_NUMBER = 7


class Finished:
    def __init__(self, world: World, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(world.primary)
        self.world = world
        self.pr_state = "MERGED"
        self.lifecycle = "completed"
        self.worktrees: dict[str, Path] = {}
        self.branches: dict[str, str] = {}
        integration = world.start()
        for member in ("zeta", "alpha"):
            self.worktrees[member] = world.deliver(
                member, {f"tests/test_{member}.py": f"def test_{member}():\n    assert 1\n"}
            )
            world.land_and_prepare(member)
        self.worktrees["mid"] = world.member("mid")
        world.commit(self.worktrees["mid"], {"tests/test_mid.py": "def test_m():\n    assert 2\n"}, "mid work")
        for member in self.worktrees:
            self.branches[member] = bi.member_branch_name(BATCH, member)
        world.tracker.bind(world.head())
        world.tracker.final_pr["repository"] = bi._repo_slug(str(world.origin))
        world.tracker.final_pr["number"] = PR_NUMBER
        git(integration, "push", "-q", "origin", f"HEAD:refs/pull/{PR_NUMBER}/head")
        self.integration = integration
        init_metadata(integration, bi.integration_branch_name(BATCH))
        git(world.primary, "merge", "--no-ff", "-qm", "merge final PR", bi.integration_branch_name(BATCH))
        merge = git(world.primary, "rev-parse", "HEAD")
        git(world.primary, "push", "-q", "origin", "develop")
        evidence = world.tmp / "pr.json"
        evidence.write_text(
            json.dumps(
                {
                    "number": PR_NUMBER,
                    "merged": True,
                    "state": "closed",
                    "head": {"sha": world.head()},
                    "base": {"ref": "develop"},
                    "merge_commit_sha": merge,
                }
            )
        )
        original_preview = worktree_finish.evaluate_finish_preview

        def preview(**kwargs):
            return original_preview(**kwargs, evidence_file=evidence)

        monkeypatch.setattr(worktree_finish, "evaluate_finish_preview", preview)
        original_run = bi._run
        original_show = world.tracker.show

        def run(argv, cwd, **kwargs):
            if argv[0] == "gh":
                assert argv[1:4] == ["pr", "view", str(PR_NUMBER)]
                return done(0, json.dumps({"number": PR_NUMBER, "state": self.pr_state}))
            return original_run(argv, cwd, **kwargs)

        def show(args):
            result = original_show(args)
            payload = json.loads(result.stdout)
            if args[:2] == ["batch", "show"] and "--field" not in args:
                payload["data"]["lifecycle"] = self.lifecycle
            result.stdout = json.dumps(payload)
            return result

        monkeypatch.setattr(bi, "_run", run)
        monkeypatch.setattr(world.tracker, "show", show)

    def tip(self, member: str) -> str | None:
        return bi._ref_oid(self.integration, f"refs/heads/{self.branches[member]}")

    def snapshot(self) -> dict:
        return {
            "refs": git(self.integration, "for-each-ref", "--format=%(refname) %(objectname)"),
            "origin": git(self.world.origin, "for-each-ref", "--format=%(refname) %(objectname)"),
            "worktrees": git(self.integration, "worktree", "list", "--porcelain"),
        }

    def finish(self, execute: bool = True) -> dict:
        return bi.finish_batch(self.integration, execute=execute)


@pytest.fixture
def fin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Finished:
    return Finished(build_world(tmp_path, monkeypatch), monkeypatch)


def _status(report: dict) -> dict[str, str]:
    return {entry["member"]: entry["status"] for entry in report["members"]}


def test_dry_run_reports_the_plan_without_cleanup(fin: Finished) -> None:
    before = fin.snapshot()
    report = fin.finish(execute=False)
    assert report["mode"] == "dry-run"
    assert _status(report) == {"zeta": "would-delete", "alpha": "would-delete", "mid": "refused"}
    assert fin.snapshot() == before
    assert all(path.is_dir() for path in fin.worktrees.values())
    assert report["integration"]["status"] == "blocked"
    assert report["integration"]["preview"]["status"] == "actionable"
    assert fin.integration.is_dir()


def test_execute_deletes_only_qualifying_members_and_their_worktrees(fin: Finished) -> None:
    zeta_tip = fin.tip("zeta")
    alpha_tip = fin.tip("alpha")
    mid_tip = fin.tip("mid")
    git(fin.integration, "push", "-q", "origin", f"{zeta_tip}:refs/heads/{fin.branches['zeta']}")
    git(fin.integration, "push", "-q", "origin", f"{alpha_tip}~1:refs/heads/{fin.branches['alpha']}")
    git(fin.integration, "push", "-q", "origin", f"{mid_tip}:refs/heads/{fin.branches['mid']}")
    report = fin.finish()
    assert _status(report) == {"zeta": "deleted", "alpha": "refused", "mid": "refused"}
    assert report["ok"] is False
    assert fin.tip("zeta") is None and fin.tip("alpha") == alpha_tip
    assert not fin.worktrees["zeta"].exists() and fin.worktrees["alpha"].exists()
    assert fin.tip("mid") == mid_tip and fin.worktrees["mid"].is_dir()
    assert bi._ref_oid(fin.integration, f"refs/heads/{bi.integration_branch_name(BATCH)}") is not None
    origin_refs = git(fin.world.origin, "for-each-ref", "--format=%(refname) %(objectname)")
    assert f"refs/heads/{fin.branches['zeta']} " not in origin_refs
    assert f"refs/heads/{fin.branches['alpha']} " in origin_refs
    assert f"refs/heads/{fin.branches['mid']} {mid_tip}" in origin_refs
    alpha = next(entry for entry in report["members"] if entry["member"] == "alpha")
    assert "differs" in alpha["reason"]


def test_a_branch_that_moved_since_the_check_is_refused(fin: Finished) -> None:
    moved = fin.world.commit(fin.worktrees["zeta"], {"tests/test_zeta.py": "def test_zeta():\n    assert 3\n"}, "late")
    report = fin.finish()
    assert _status(report)["zeta"] == "refused"
    assert fin.tip("zeta") == moved
    assert fin.worktrees["zeta"].is_dir()
    assert _status(report)["alpha"] == "deleted"


def test_a_branch_that_moves_between_the_check_and_the_delete_survives(
    fin: Finished, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = bi._plan_member_cleanup
    moved: list[str] = []

    def plan_then_move(repo, batch_id, pull_head, member, receipts):
        entry = plan(repo, batch_id, pull_head, member, receipts)
        if member == "zeta":
            moved.append(fin.world.commit(fin.worktrees["zeta"], {"docs/late.md": "late\n"}, "late"))
        return entry

    monkeypatch.setattr(bi, "_plan_member_cleanup", plan_then_move)
    report = fin.finish()
    assert _status(report)["zeta"] == "refused"
    assert fin.tip("zeta") == moved[0]
    assert fin.worktrees["zeta"].is_dir()
    assert "worktree_removed" not in next(entry for entry in report["members"] if entry["member"] == "zeta")


def test_a_worktree_that_turns_dirty_between_the_check_and_the_delete_is_kept(
    fin: Finished, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = bi._plan_member_cleanup

    def plan_then_dirty(repo, batch_id, pull_head, member, receipts):
        entry = plan(repo, batch_id, pull_head, member, receipts)
        if member == "zeta":
            (fin.worktrees["zeta"] / "scratch.txt").write_text("unsaved\n")
        return entry

    monkeypatch.setattr(bi, "_plan_member_cleanup", plan_then_dirty)
    zeta_tip = fin.tip("zeta")
    report = fin.finish()
    assert _status(report)["zeta"] == "refused"
    assert fin.tip("zeta") == zeta_tip and (fin.worktrees["zeta"] / "scratch.txt").is_file()


def test_a_locked_member_worktree_is_refused_in_the_dry_run(fin: Finished) -> None:
    git(fin.integration, "worktree", "lock", str(fin.worktrees["zeta"]))
    report = fin.finish(execute=False)
    assert _status(report)["zeta"] == "refused"
    assert "locked" in next(entry for entry in report["members"] if entry["member"] == "zeta")["reason"]
    assert _status(report)["alpha"] == "would-delete"


def test_a_member_with_nothing_deletable_is_refused_not_reported_deleted(fin: Finished) -> None:
    alpha_tip = fin.tip("alpha")
    git(fin.integration, "worktree", "remove", str(fin.worktrees["alpha"]))
    git(fin.integration, "branch", "-q", "-D", fin.branches["alpha"])
    git(fin.integration, "push", "-q", "origin", f"{alpha_tip}~1:refs/heads/{fin.branches['alpha']}")
    report = fin.finish()
    alpha = next(entry for entry in report["members"] if entry["member"] == "alpha")
    assert alpha["status"] == "refused" and "differs" in alpha["reason"]
    assert report["ok"] is False
    assert f"refs/heads/{fin.branches['alpha']}" in git(fin.world.origin, "for-each-ref")


def test_a_missing_pull_request_head_ref_is_refused_before_anything_is_deleted(fin: Finished) -> None:
    git(fin.world.origin, "update-ref", "-d", f"refs/pull/{PR_NUMBER}/head")
    before = fin.snapshot()
    with pytest.raises(bi.BatchError, match=f"refs/pull/{PR_NUMBER}/head could not be fetched"):
        fin.finish()
    assert fin.snapshot() == before


def test_an_accepted_head_outside_the_pull_request_head_is_refused(fin: Finished) -> None:
    start = fin.world.tracker.log[0]["head"]
    git(fin.world.origin, "update-ref", f"refs/pull/{PR_NUMBER}/head", start)
    report = fin.finish()
    assert _status(report) == {"zeta": "refused", "alpha": "refused", "mid": "refused"}
    assert fin.tip("zeta") is not None and fin.tip("alpha") is not None


def test_an_unmerged_final_pr_is_refused_before_anything_is_deleted(fin: Finished) -> None:
    fin.pr_state = "OPEN"
    before = fin.snapshot()
    with pytest.raises(bi.BatchError, match="not merged"):
        fin.finish()
    assert fin.snapshot() == before


def test_a_batch_that_is_not_completed_is_refused(fin: Finished) -> None:
    fin.lifecycle = "active"
    before = fin.snapshot()
    with pytest.raises(bi.BatchError, match="not 'completed'"):
        fin.finish()
    assert fin.snapshot() == before


def test_a_dirty_member_worktree_keeps_its_worktree_and_branch(fin: Finished) -> None:
    (fin.worktrees["zeta"] / "scratch.txt").write_text("unsaved\n")
    zeta_tip = fin.tip("zeta")
    report = fin.finish()
    assert _status(report)["zeta"] == "refused"
    assert fin.tip("zeta") == zeta_tip
    assert (fin.worktrees["zeta"] / "scratch.txt").is_file()
    assert _status(report)["alpha"] == "deleted"


def test_a_branch_checked_out_in_a_foreign_worktree_is_kept(fin: Finished) -> None:
    config_key = f"{bi.CONFIG_PREFIX}.role"
    git(fin.worktrees["zeta"], "config", "--worktree", config_key, "other")
    report = fin.finish()
    assert _status(report)["zeta"] == "refused"
    assert fin.tip("zeta") is not None and fin.worktrees["zeta"].is_dir()


def test_finish_runs_only_in_an_integration_worktree(fin: Finished) -> None:
    with pytest.raises(bi.BatchError, match="role=integration"):
        bi.finish_batch(fin.worktrees["zeta"], execute=True)


def _remove_unaccepted_member(fin: Finished) -> None:
    git(fin.world.primary, "worktree", "remove", str(fin.worktrees["mid"]))
    git(fin.world.primary, "update-ref", "-d", f"refs/heads/{fin.branches['mid']}")


def test_integration_dry_run_performs_no_cleanup(fin: Finished) -> None:
    _remove_unaccepted_member(fin)
    before = fin.snapshot()
    report = fin.finish(execute=False)
    assert report["ok"]
    assert report["integration"]["status"] == "would-finish"
    assert fin.snapshot() == before
    assert fin.integration.is_dir()


def test_integration_is_removed_last(fin: Finished, monkeypatch: pytest.MonkeyPatch) -> None:
    _remove_unaccepted_member(fin)
    apply = worktree_finish.apply_finish_actions

    def remove(preview, primary):
        assert all(bi._ref_oid(primary, f"refs/heads/{branch}") is None for branch in fin.branches.values())
        assert all(bi._remote_branch_oid(primary, branch) is None for branch in fin.branches.values())
        return apply(preview, primary)

    monkeypatch.setattr(worktree_finish, "apply_finish_actions", remove)
    report = fin.finish()
    assert report["ok"] and report["integration"]["status"] == "finished"
    assert not fin.integration.exists()
    assert bi._ref_oid(fin.world.primary, f"refs/heads/{bi.integration_branch_name(BATCH)}") is None


@pytest.mark.parametrize("problem", ["dirty", "foreign", "head"])
def test_unsafe_integration_is_kept(fin: Finished, problem: str) -> None:
    _remove_unaccepted_member(fin)
    if problem == "dirty":
        (fin.integration / "scratch.txt").write_text("unsaved\n")
    elif problem == "foreign":
        git(fin.integration, "config", "--worktree", "benchbox.worktree.controller-kind", "foreign")
    else:
        fin.world.commit(fin.integration, {"late.txt": "late\n"}, "late")
    report = fin.finish()
    assert not report["ok"] and report["integration"]["status"] == "blocked"
    assert fin.integration.is_dir()
    assert bi._ref_oid(fin.world.primary, f"refs/heads/{bi.integration_branch_name(BATCH)}")


def test_rerun_finishes_after_members_are_deleted(fin: Finished) -> None:
    _remove_unaccepted_member(fin)
    (fin.integration / "scratch.txt").write_text("unsaved\n")
    first = fin.finish()
    assert _status(first) == {"zeta": "deleted", "alpha": "deleted", "mid": "already-deleted"}
    (fin.integration / "scratch.txt").unlink()
    second = fin.finish()
    assert set(_status(second).values()) == {"already-deleted"}
    assert second["ok"] and not fin.integration.exists()


def test_finish_from_inside_integration_reports_after_removal(
    fin: Finished, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _remove_unaccepted_member(fin)
    monkeypatch.chdir(fin.integration)
    assert bi.main(["finish", "--execute"]) == 0
    assert json.loads(capsys.readouterr().out)["integration"]["status"] == "finished"
    assert Path.cwd() == fin.world.primary
    assert not fin.integration.exists()


def test_final_pr_is_reread_before_integration_removal(fin: Finished, monkeypatch: pytest.MonkeyPatch) -> None:
    _remove_unaccepted_member(fin)
    cleanup = bi._execute_member_cleanup

    def clean(repo, entry):
        cleanup(repo, entry)
        fin.pr_state = "OPEN"

    monkeypatch.setattr(bi, "_execute_member_cleanup", clean)
    report = fin.finish()
    assert not report["ok"]
    assert "not merged" in report["integration"]["reason"]
    assert fin.integration.is_dir()
    assert bi._ref_oid(fin.world.primary, f"refs/heads/{bi.integration_branch_name(BATCH)}")


def test_unmerged_final_pr_dry_run_reports_blocked(fin: Finished) -> None:
    fin.pr_state = "OPEN"
    before = fin.snapshot()
    report = fin.finish(execute=False)
    assert report["integration"]["status"] == "blocked"
    assert "not merged" in report["integration"]["reason"]
    assert fin.snapshot() == before


@pytest.mark.parametrize("change", ["dirty", "merge-unreachable", "unmerged-evidence"])
def test_integration_finish_revalidates_evidence_after_member_cleanup(
    fin: Finished, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    _remove_unaccepted_member(fin)
    cleanup = bi._execute_member_cleanup

    def clean(repo, entry):
        cleanup(repo, entry)
        if change == "dirty":
            (fin.integration / "scratch.txt").write_text("unsaved\n")
        elif change == "merge-unreachable":
            git(fin.world.origin, "update-ref", "refs/heads/develop", fin.world.tracker.log[0]["head"])
        else:
            evidence = fin.world.tmp / "pr.json"
            pr = json.loads(evidence.read_text(encoding="utf-8"))
            pr["merged"] = False
            evidence.write_text(json.dumps(pr))

    monkeypatch.setattr(bi, "_execute_member_cleanup", clean)
    report = fin.finish()
    assert not report["ok"] and report["integration"]["status"] == "blocked"
    assert fin.integration.is_dir()
    assert bi._ref_oid(fin.world.primary, f"refs/heads/{bi.integration_branch_name(BATCH)}")


@pytest.mark.parametrize("state", ["dirty", "locked", "foreign", "switched"])
def test_missing_member_refs_do_not_hide_a_retained_worktree(fin: Finished, state: str) -> None:
    _remove_unaccepted_member(fin)
    member = fin.worktrees["zeta"]
    if state == "dirty":
        (member / "scratch.txt").write_text("unsaved\n")
    elif state == "locked":
        git(fin.world.primary, "worktree", "lock", str(member))
    elif state == "foreign":
        git(member, "config", "--worktree", f"{bi.CONFIG_PREFIX}.role", "foreign")
    else:
        git(member, "switch", "-qc", "retained-other-branch")
    git(fin.world.primary, "update-ref", "-d", f"refs/heads/{fin.branches['zeta']}")
    report = fin.finish()
    assert _status(report)["zeta"] == "refused"
    assert report["integration"]["status"] == "blocked"
    assert member.is_dir() and fin.integration.is_dir()


@pytest.mark.parametrize("change", ["identity", "branch", "path", "locked"])
def test_member_worktree_safety_is_rechecked_before_removal(
    fin: Finished, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    _remove_unaccepted_member(fin)
    plan = bi._plan_member_cleanup
    retained = [fin.worktrees["zeta"]]

    def plan_then_change(repo, batch_id, pull_head, member, receipts):
        entry = plan(repo, batch_id, pull_head, member, receipts)
        if member == "zeta":
            if change == "identity":
                git(retained[0], "config", "--worktree", f"{bi.CONFIG_PREFIX}.id", "other-batch")
            elif change == "branch":
                git(retained[0], "switch", "-qc", "retained-other-branch")
            elif change == "path":
                moved = fin.world.tmp / "moved-zeta"
                git(repo, "worktree", "move", str(retained[0]), str(moved))
                retained[0] = moved
            else:
                git(repo, "worktree", "lock", str(retained[0]))
        return entry

    monkeypatch.setattr(bi, "_plan_member_cleanup", plan_then_change)
    report = fin.finish()
    assert _status(report)["zeta"] == "refused"
    assert fin.tip("zeta")
    assert retained[0].is_dir() and fin.integration.is_dir()
