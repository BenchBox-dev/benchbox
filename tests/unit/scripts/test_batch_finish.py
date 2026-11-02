from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.unit.scripts.batch_loop_world import BATCH, World, bi, build_world, done, git

pytestmark = [pytest.mark.unit, pytest.mark.medium]

PR_NUMBER = 7


class Finished:
    def __init__(self, world: World, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_dry_run_reports_the_plan_and_changes_nothing(fin: Finished) -> None:
    before = fin.snapshot()
    report = fin.finish(execute=False)
    assert report["mode"] == "dry-run"
    assert _status(report) == {"zeta": "would-delete", "alpha": "would-delete", "mid": "refused"}
    assert fin.snapshot() == before
    assert all(path.is_dir() for path in fin.worktrees.values())


def test_execute_deletes_only_qualifying_members_and_their_worktrees(fin: Finished) -> None:
    zeta_tip = fin.tip("zeta")
    alpha_tip = fin.tip("alpha")
    mid_tip = fin.tip("mid")
    git(fin.integration, "push", "-q", "origin", f"{zeta_tip}:refs/heads/{fin.branches['zeta']}")
    git(fin.integration, "push", "-q", "origin", f"{alpha_tip}~1:refs/heads/{fin.branches['alpha']}")
    git(fin.integration, "push", "-q", "origin", f"{mid_tip}:refs/heads/{fin.branches['mid']}")
    report = fin.finish()
    assert _status(report) == {"zeta": "deleted", "alpha": "deleted", "mid": "refused"}
    assert report["ok"] is False
    assert fin.tip("zeta") is None and fin.tip("alpha") is None
    assert not fin.worktrees["zeta"].exists() and not fin.worktrees["alpha"].exists()
    assert fin.tip("mid") == mid_tip and fin.worktrees["mid"].is_dir()
    assert bi._ref_oid(fin.integration, f"refs/heads/{bi.integration_branch_name(BATCH)}") is not None
    origin_refs = git(fin.world.origin, "for-each-ref", "--format=%(refname) %(objectname)")
    assert f"refs/heads/{fin.branches['zeta']} " not in origin_refs
    assert f"refs/heads/{fin.branches['alpha']} " in origin_refs
    assert f"refs/heads/{fin.branches['mid']} {mid_tip}" in origin_refs
    alpha = next(entry for entry in report["members"] if entry["member"] == "alpha")
    assert "remote_kept" in alpha


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
