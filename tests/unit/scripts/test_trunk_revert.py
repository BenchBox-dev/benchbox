from __future__ import annotations

import importlib.util
import json
import shlex
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).resolve().parents[3]
OID = "b" * 40
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
RUN_URL = "https://github.com/BenchBox-dev/BenchBox/actions/runs/123"


def _load():
    spec = importlib.util.spec_from_file_location("trunk_revert", ROOT / "scripts/trunk_revert.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


trunk_revert = _load()


CURRENT_WORKTREE = "/work/BenchBox.wt-current"
WORKTREE = str(Path(CURRENT_WORKTREE).parent / "BenchBox.wt-revert-12")


class FakeRun:
    def __init__(
        self,
        *,
        pr_view: dict | None = None,
        fail: dict[str, int] | None = None,
        origin: str = "git@github.com:BenchBox-dev/BenchBox.git",
        runs: list[dict] | None = None,
    ):
        self.origin = origin
        self.pr_view = pr_view
        self.fail = fail or {}
        self.runs = _runs(("completed", "failure", timedelta(minutes=31))) if runs is None else runs
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str]) -> tuple[int, str]:
        self.calls.append(cmd)
        if cmd[:3] == ["gh", "pr", "view"]:
            return 0, json.dumps(self.pr_view)
        if cmd[:4] == ["git", "remote", "get-url", "--push"]:
            return 0, self.origin
        if cmd[:3] == ["git", "rev-parse", "--show-toplevel"]:
            return 0, f"{CURRENT_WORKTREE}\n"
        if cmd[:3] == ["gh", "pr", "create"] and "gh pr create" not in self.fail:
            return 0, "https://github.com/BenchBox-dev/BenchBox/pull/99\n"
        for prefix, code in self.fail.items():
            if " ".join(cmd).startswith(prefix):
                return code, "boom"
        if cmd[:3] == ["gh", "run", "list"]:
            return 0, json.dumps(self.runs)
        return 0, ""


def _merged(title: str = "fix(x): thing") -> dict:
    return {"state": "MERGED", "mergeCommit": {"oid": OID}, "title": title}


def test_refuses_a_pr_that_is_not_merged(capsys: pytest.CaptureFixture[str]) -> None:
    run = FakeRun(pr_view={"state": "OPEN", "mergeCommit": None, "title": "t"})
    assert trunk_revert.revert(12, run=run) == 1
    assert "not MERGED" in capsys.readouterr().err
    assert not any(call[:1] == ["make"] or call[:2] == ["git", "revert"] for call in run.calls)


def test_builds_the_expected_worktree_revert_and_pr_commands(capsys: pytest.CaptureFixture[str]) -> None:
    run = FakeRun(pr_view=_merged("fix(x): thing"))
    assert trunk_revert.revert(12, run=run) == 0
    assert run.calls == [
        ["gh", "pr", "view", "12", "--repo", trunk_revert.REPOSITORY, "--json", "state,mergeCommit,title"],
        [
            "gh",
            "run",
            "list",
            "--repo",
            trunk_revert.REPOSITORY,
            "--workflow",
            "trunk.yml",
            "--branch",
            "develop",
            "--limit",
            "30",
            "--json",
            trunk_revert.RUN_FIELDS,
        ],
        ["git", "remote", "get-url", "--push", "origin"],
        ["git", "rev-parse", "--show-toplevel"],
        ["git", "fetch", "origin", "develop", "--quiet"],
        ["git", "merge-base", "--is-ancestor", OID, "origin/develop"],
        ["make", "worktree-create", "BRANCH=fix/revert-12", f"WORKTREE_PATH={WORKTREE}"],
        ["git", "-C", WORKTREE, "revert", "--no-edit", OID],
        [
            "git",
            "-C",
            WORKTREE,
            "commit",
            "--amend",
            "-m",
            'Revert "fix(x): thing" (#12)',
            "-m",
            f"This reverts commit {OID}.",
        ],
        ["git", "-C", WORKTREE, "push", "-u", "origin", "fix/revert-12"],
        [
            "gh",
            "pr",
            "create",
            "--repo",
            trunk_revert.REPOSITORY,
            "--base",
            "develop",
            "--head",
            "fix/revert-12",
            "--title",
            'Revert "fix(x): thing" (#12)',
            "--body",
            f"Reverts #12 to address the failed trunk.yml run: {RUN_URL}",
        ],
    ]
    out = capsys.readouterr().out
    assert "pull/99" in out
    assert f"Revert worktree: {WORKTREE}" in out


def test_leaves_the_current_worktree_untouched() -> None:
    run = FakeRun(pr_view=_merged())
    assert trunk_revert.revert(12, run=run) == 0
    assert any(call[:2] == ["make", "worktree-create"] for call in run.calls)
    for call in run.calls:
        assert call[:2] != ["git", "switch"] and call[:2] != ["git", "checkout"]
        if call[:2] in (["git", "revert"], ["git", "commit"], ["git", "push"]):
            raise AssertionError(f"ran in the current worktree: {call}")


def test_qualifies_the_head_when_origin_is_a_fork() -> None:
    run = FakeRun(pr_view=_merged(), origin="https://github.com/someone/BenchBox.git")
    assert trunk_revert.revert(12, run=run) == 0
    create = next(call for call in run.calls if call[:3] == ["gh", "pr", "create"])
    assert create[create.index("--head") + 1] == "someone:fix/revert-12"


def test_title_with_quotes_and_command_substitution_stays_one_argument() -> None:
    title = 'fix: "quoted" $(touch /tmp/pwned) `id` ; rm -rf /'
    run = FakeRun(pr_view=_merged(title))
    assert trunk_revert.revert(5, run=run) == 0
    amend = next(call for call in run.calls if "--amend" in call)
    assert amend[amend.index("-m") + 1] == f'Revert "{title}" (#5)'
    create = next(call for call in run.calls if call[:3] == ["gh", "pr", "create"])
    assert create[create.index("--title") + 1] == f'Revert "{title}" (#5)'
    assert create[create.index("--body") + 1].endswith(RUN_URL)
    assert all(isinstance(part, str) for call in run.calls for part in call)
    assert not any(call[0] == "sh" or "-c" in call[:2] for call in run.calls)


def test_removes_the_new_worktree_when_the_revert_conflicts(capsys: pytest.CaptureFixture[str]) -> None:
    run = FakeRun(pr_view=_merged(), fail={"git -C": 1})
    assert trunk_revert.revert(12, run=run) == 1
    assert ["git", "-C", WORKTREE, "revert", "--abort"] in run.calls
    assert ["git", "worktree", "remove", "--force", WORKTREE] in run.calls
    assert ["git", "branch", "-D", "fix/revert-12"] in run.calls
    assert not any(call[3:4] == ["push"] for call in run.calls)
    assert "git revert failed" in capsys.readouterr().err


def test_removes_the_new_worktree_when_the_push_fails() -> None:
    run = FakeRun(pr_view=_merged(), fail={f"git -C {WORKTREE} push": 1})
    assert trunk_revert.revert(12, run=run) == 1
    assert ["git", "worktree", "remove", "--force", WORKTREE] in run.calls
    assert ["git", "branch", "-D", "fix/revert-12"] in run.calls


def test_keeps_the_pushed_branch_and_says_how_to_finish_when_pr_creation_fails(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runs = _runs(("completed", "failure", timedelta(minutes=31)))
    runs[0]["url"] = RUN_URL + "?label='quoted'&value=$(touch /tmp/pwned)"
    run = FakeRun(pr_view=_merged('fix: "quoted" $(touch /tmp/pwned)'), fail={"gh pr create": 1}, runs=runs)
    assert trunk_revert.revert(12, run=run) == 1
    assert ["git", "branch", "-D", "fix/revert-12"] not in run.calls
    recovery = capsys.readouterr().err.split("open it with: ", 1)[1].strip()
    create = next(call for call in run.calls if call[:3] == ["gh", "pr", "create"])
    assert shlex.split(recovery) == create
    assert create[create.index("--body") + 1].endswith(runs[0]["url"])
    assert "--fill" not in create


def test_does_not_create_a_worktree_when_worktree_create_refuses() -> None:
    run = FakeRun(pr_view=_merged(), fail={"make worktree-create": 1})
    assert trunk_revert.revert(12, run=run) == 1
    assert not any(call[:3] == ["git", "worktree", "remove"] for call in run.calls)
    assert not any(call[:3] == ["git", "-C", WORKTREE] for call in run.calls)


def test_refuses_a_merge_commit_that_is_not_on_develop() -> None:
    run = FakeRun(pr_view=_merged(), fail={"git merge-base": 1})
    assert trunk_revert.revert(12, run=run) == 1
    assert not any(call[:1] == ["make"] for call in run.calls)


@pytest.mark.parametrize("runs", [[], [{"status": "completed", "conclusion": "failure"}]])
def test_refuses_missing_run_evidence_before_mutating(runs: list[dict], capsys: pytest.CaptureFixture[str]) -> None:
    run = FakeRun(pr_view=_merged(), runs=runs)
    assert trunk_revert.revert(12, run=run) == 1
    assert "trunk-revert: refusing:" in capsys.readouterr().err
    assert all(call[:1] == ["gh"] for call in run.calls)


def test_refuses_when_run_lookup_fails_before_mutating() -> None:
    run = FakeRun(pr_view=_merged(), fail={"gh run list": 1})
    assert trunk_revert.revert(12, run=run) == 1
    assert all(call[:1] == ["gh"] for call in run.calls)


def test_refuses_when_there_is_no_failed_run_before_mutating(capsys: pytest.CaptureFixture[str]) -> None:
    run = FakeRun(pr_view=_merged(), runs=_runs(("completed", "success", timedelta(hours=1))))
    assert trunk_revert.revert(12, run=run) == 1
    assert "no failed trunk.yml run" in capsys.readouterr().err
    assert all(call[:1] == ["gh"] for call in run.calls)


def test_revert_names_the_newest_failed_run_by_creation_with_a_pending_run() -> None:
    runs = _runs(
        ("completed", "failure", timedelta(hours=1)),
        ("in_progress", None, timedelta(minutes=1)),
        ("completed", "failure", timedelta(hours=3)),
    )
    runs[2]["updatedAt"] = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
    run = FakeRun(pr_view=_merged(), runs=runs)
    assert trunk_revert.revert(12, run=run) == 0
    create = next(call for call in run.calls if call[:3] == ["gh", "pr", "create"])
    assert create[create.index("--body") + 1].endswith(runs[0]["url"])


def _runs(*items: tuple[str, str | None, timedelta]) -> list[dict]:
    return [
        {
            "status": status,
            "conclusion": conclusion,
            "createdAt": (NOW - age - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "updatedAt": (NOW - age).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "databaseId": 123 + index,
            "url": f"https://github.com/BenchBox-dev/BenchBox/actions/runs/{123 + index}",
        }
        for index, (status, conclusion, age) in enumerate(items)
    ]


def _gate(runs: list[dict] | None, branch: str = "fix/thing", *, code: int = 0, out: str | None = None):
    payload = json.dumps(runs) if out is None else out

    def run(cmd: list[str]) -> tuple[int, str]:
        if cmd[:2] == ["gh", "api"]:
            return 1, "offline"
        assert cmd[:3] == ["gh", "run", "list"]
        assert cmd[cmd.index("--workflow") :][:6] == [
            "--workflow",
            "trunk.yml",
            "--branch",
            "develop",
            "--limit",
            "30",
        ]
        assert cmd[-2:] == ["--json", "conclusion,status,createdAt,updatedAt,databaseId,url,headSha"]
        return code, payload

    return trunk_revert.trunk_gate(branch, run=run, now=NOW)


def test_gate_allows_when_there_are_no_runs(capsys: pytest.CaptureFixture[str]) -> None:
    assert _gate([]) is None
    assert capsys.readouterr().err.count("warning") == 1


def test_gate_allows_when_gh_errors(capsys: pytest.CaptureFixture[str]) -> None:
    assert _gate(None, code=1, out="HTTP 404: workflow trunk.yml not found") is None
    assert capsys.readouterr().err.count("warning") == 1


@pytest.mark.parametrize("out", ["invalid JSON", "{}", '[{"status": "completed"}]'])
def test_gate_keeps_malformed_run_metadata_advisory(out: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert _gate(None, out=out) is None
    assert capsys.readouterr().err.count("warning") == 1


def test_gate_allows_when_the_newest_run_is_green() -> None:
    assert (
        _gate(_runs(("completed", "success", timedelta(minutes=5)), ("completed", "failure", timedelta(hours=3))))
        is None
    )


def test_gate_allows_a_red_run_under_thirty_minutes() -> None:
    assert _gate(_runs(("completed", "failure", timedelta(minutes=29)))) is None


def test_gate_allows_a_red_run_at_thirty_minutes() -> None:
    assert _gate(_runs(("completed", "failure", timedelta(minutes=30)))) is None


@pytest.mark.parametrize("conclusion", ["failure", "timed_out", "startup_failure"])
def test_gate_refuses_a_red_run_over_thirty_minutes(conclusion: str) -> None:
    refusal = _gate(_runs(("completed", conclusion, timedelta(minutes=31))))
    assert refusal is not None
    assert "red for 31 minutes" in refusal
    assert "make trunk-revert" in refusal
    assert RUN_URL in refusal


@pytest.mark.parametrize("conclusion", ["cancelled", "neutral", "skipped", "action_required", "stale"])
def test_gate_allows_when_the_newest_completed_run_is_not_red(conclusion: str) -> None:
    assert _gate(_runs(("completed", conclusion, timedelta(hours=1)))) is None


def test_gate_orders_by_creation_even_when_an_older_run_finishes_later() -> None:
    runs = _runs(
        ("completed", "failure", timedelta(hours=3)),
        ("completed", "failure", timedelta(minutes=40)),
    )
    runs[0]["updatedAt"] = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
    refusal = _gate(runs)
    assert refusal is not None
    assert "red for 40 minutes" in refusal
    assert runs[1]["url"] in refusal
    assert runs[0]["url"] not in refusal


def test_gate_orders_runs_created_in_the_same_second_by_run_id() -> None:
    runs = _runs(
        ("completed", "failure", timedelta(hours=1)),
        ("completed", "success", timedelta(hours=1)),
    )
    assert _gate(runs) is None


def test_gate_allows_red_then_green() -> None:
    runs = _runs(
        ("completed", "failure", timedelta(hours=2)),
        ("completed", "success", timedelta(hours=1)),
    )
    assert _gate(runs) is None


def test_gate_ages_only_the_newest_failure() -> None:
    runs = _runs(
        ("completed", "failure", timedelta(minutes=10)),
        ("completed", "failure", timedelta(minutes=50)),
        ("completed", "success", timedelta(hours=3)),
    )
    assert _gate(runs) is None


def test_gate_does_not_look_past_a_success() -> None:
    runs = _runs(
        ("completed", "failure", timedelta(minutes=10)),
        ("completed", "success", timedelta(minutes=20)),
        ("completed", "failure", timedelta(hours=2)),
    )
    assert _gate(runs) is None


@pytest.mark.parametrize("status", ["queued", "in_progress", "waiting", "pending", "requested"])
def test_gate_allows_when_the_newest_run_is_pending(status: str) -> None:
    runs = _runs(
        (status, None, timedelta(minutes=1)),
        ("completed", "failure", timedelta(hours=1)),
    )
    assert _gate(runs) is None


def test_gate_exempts_revert_branches() -> None:
    assert _gate(_runs(("completed", "failure", timedelta(hours=5))), branch="fix/revert-12") is None


def test_main_gate_opens_a_draft_instead_of_refusing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(trunk_revert, "trunk_gate", lambda branch, run=None, repo="": "develop is red")
    assert trunk_revert.main(["gate", "--branch", "fix/x"]) == 0
    assert "Opening as a draft: develop is red" in capsys.readouterr().err


def test_main_rejects_a_malformed_repo(capsys: pytest.CaptureFixture[str]) -> None:
    assert trunk_revert.main(["revert", "--pr", "3", "--repo", "not a repo"]) == 2


CULPRIT_SHA = "c" * 40


class ArmRun:
    def __init__(
        self,
        runs: list[dict],
        *,
        branch: str = "fix/thing",
        commits: list[dict] | None = None,
        pulls: list[dict] | None = None,
        develop: list[dict] | None = None,
        pulls_by_sha: dict[str, list[dict]] | None = None,
        runs_code: int = 0,
        develop_code: int = 0,
    ):
        self.runs = runs
        self.branch = branch
        self.commits = commits or [{"messageHeadline": "change", "messageBody": ""}]
        self.pulls = (
            [{"number": 77, "merged_at": "2026-10-03T11:00:00Z", "base": {"ref": "develop"}}]
            if pulls is None
            else pulls
        )
        self.develop = develop or [{"commit": {"message": "feat: unrelated"}}]
        self.runs_code = runs_code
        self.develop_code = develop_code
        self.pulls_by_sha = pulls_by_sha
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str]) -> tuple[int, str]:
        self.calls.append(cmd)
        if cmd[:3] == ["gh", "pr", "view"]:
            return 0, json.dumps({"headRefName": self.branch, "commits": self.commits})
        if cmd[:3] == ["gh", "run", "list"]:
            if self.runs_code != 0:
                return self.runs_code, "boom"
            ordered = sorted(self.runs, key=lambda item: (item["createdAt"], item["databaseId"]), reverse=True)
            return 0, json.dumps(ordered[: int(cmd[cmd.index("--limit") + 1])])
        if cmd[:2] == ["gh", "api"] and "/pulls" in cmd[2]:
            if self.pulls_by_sha is not None:
                sha = cmd[2].split("/commits/")[1].split("/")[0]
                return 0, json.dumps(self.pulls_by_sha.get(sha, []))
            return 0, json.dumps(self.pulls)
        if cmd[:2] == ["gh", "api"]:
            return self.develop_code, json.dumps(self.develop) if self.develop_code == 0 else "boom"
        raise AssertionError(f"unexpected command: {cmd}")


def _red_runs(*items: tuple[str, str | None, timedelta]) -> list[dict]:
    runs = _runs(*items)
    for item in runs:
        item["headSha"] = CULPRIT_SHA
    return runs


def test_arm_gate_names_the_culprit_pr_when_trunk_is_red_at_once() -> None:
    run = ArmRun(_red_runs(("completed", "failure", timedelta(minutes=1))))
    message = trunk_revert.arm_gate("fix/thing", run=run)
    assert message is not None
    assert "PR #77" in message
    assert "make trunk-revert PR=77" in message
    assert run.runs[0]["url"] in message


def test_arm_gate_blocks_through_a_newer_pending_run() -> None:
    run = ArmRun(
        _red_runs(
            ("in_progress", None, timedelta(minutes=1)),
            ("completed", "failure", timedelta(minutes=10)),
            ("completed", "success", timedelta(hours=2)),
        )
    )
    assert trunk_revert.arm_gate("fix/thing", run=run) is not None


def test_arm_gate_blocks_through_a_newer_cancelled_run() -> None:
    run = ArmRun(
        _red_runs(("completed", "cancelled", timedelta(minutes=1)), ("completed", "failure", timedelta(minutes=10)))
    )
    assert trunk_revert.arm_gate("fix/thing", run=run) is not None


def test_arm_gate_allows_once_trunk_is_green() -> None:
    run = ArmRun(
        _red_runs(("completed", "success", timedelta(minutes=1)), ("completed", "failure", timedelta(minutes=10)))
    )
    assert trunk_revert.arm_gate("fix/thing", run=run) is None


def test_arm_gate_allows_once_a_revert_of_the_culprit_is_on_develop() -> None:
    develop = [{"commit": {"message": f'Revert "x" (#77)\n\nThis reverts commit {CULPRIT_SHA}.'}}]
    run = ArmRun(_red_runs(("completed", "failure", timedelta(minutes=10))), develop=develop)
    assert trunk_revert.arm_gate("fix/thing", run=run) is None
    assert not any(call[:1] == ["git"] for call in run.calls)


def test_arm_gate_exempts_a_real_revert_of_the_culprit() -> None:
    commits = [{"messageHeadline": 'Revert "x" (#77)', "messageBody": f"This reverts commit {CULPRIT_SHA}."}]
    run = ArmRun(_red_runs(("completed", "failure", timedelta(minutes=10))), branch="fix/revert-77", commits=commits)
    assert trunk_revert.arm_gate("fix/revert-77", run=run) is None


@pytest.mark.parametrize(
    "commits",
    [
        [{"messageHeadline": "unrelated change", "messageBody": ""}],
        [{"messageHeadline": "revert", "messageBody": "This reverts commit " + "e" * 40}],
    ],
)
def test_arm_gate_refuses_a_revert_named_branch_that_does_not_revert_the_culprit(commits: list[dict]) -> None:
    run = ArmRun(_red_runs(("completed", "failure", timedelta(minutes=10))), branch="fix/revert-x", commits=commits)
    assert trunk_revert.arm_gate("fix/revert-x", run=run) is not None


def test_arm_gate_names_the_commit_when_no_pr_is_associated() -> None:
    run = ArmRun(_red_runs(("completed", "failure", timedelta(minutes=10))), pulls=[])
    message = trunk_revert.arm_gate("fix/thing", run=run)
    assert message is not None
    assert CULPRIT_SHA[:9] in message
    assert "PR=<number>" in message


def test_arm_gate_ignores_unmerged_and_foreign_base_pulls() -> None:
    pulls = [
        {"number": 5, "merged_at": None, "base": {"ref": "develop"}},
        {"number": 6, "merged_at": "2026-10-03T11:00:00Z", "base": {"ref": "release"}},
    ]
    run = ArmRun(_red_runs(("completed", "failure", timedelta(minutes=10))), pulls=pulls)
    message = trunk_revert.arm_gate("fix/thing", run=run)
    assert message is not None
    assert "PR #" not in message


def test_arm_gate_allows_when_trunk_has_never_run() -> None:
    assert trunk_revert.arm_gate("fix/thing", run=ArmRun([])) is None


FIRST_SHA = "a" * 40
LATER_SHA = "b" * 40


def _streak(*shas: str | None) -> list[dict]:
    runs = _runs(
        *[
            ("completed", "failure" if sha else "success", timedelta(minutes=10 * (len(shas) - i)))
            for i, sha in enumerate(shas)
        ]
    )
    for item, sha in zip(runs, shas, strict=True):
        item["headSha"] = sha or "g" * 40
    return list(reversed(runs))


def _merged_pulls(number: int) -> list[dict]:
    return [{"number": number, "merged_at": "2026-10-03T11:00:00Z", "base": {"ref": "develop"}}]


def _streak_run(**kwargs) -> ArmRun:
    runs = _streak(None, FIRST_SHA, LATER_SHA)
    return ArmRun(runs, pulls_by_sha={FIRST_SHA: _merged_pulls(3200), LATER_SHA: _merged_pulls(3220)}, **kwargs)


def test_arm_gate_blames_the_first_red_run_of_the_streak() -> None:
    message = trunk_revert.arm_gate("fix/thing", run=_streak_run())
    assert message is not None
    assert "PR #3200" in message
    assert "make trunk-revert PR=3200" in message
    assert "3220" not in message


def test_arm_gate_exempts_the_revert_of_the_first_red_commit() -> None:
    commits = [{"messageHeadline": 'Revert "x" (#3200)', "messageBody": f"This reverts commit {FIRST_SHA}."}]
    run = _streak_run(branch="fix/revert-3200", commits=commits)
    assert trunk_revert.arm_gate("fix/revert-3200", run=run) is None


def test_arm_gate_refuses_the_revert_of_a_later_innocent_commit() -> None:
    commits = [{"messageHeadline": 'Revert "x" (#3220)', "messageBody": f"This reverts commit {LATER_SHA}."}]
    run = _streak_run(branch="fix/revert-3220", commits=commits)
    message = trunk_revert.arm_gate("fix/revert-3220", run=run)
    assert message is not None
    assert "PR #3200" in message


def test_arm_gate_clears_once_the_first_red_commit_is_reverted_on_develop() -> None:
    develop = [{"commit": {"message": f'Revert "x" (#3200)\n\nThis reverts commit {FIRST_SHA}.'}}]
    assert trunk_revert.arm_gate("fix/thing", run=_streak_run(develop=develop)) is None


def test_arm_gate_streak_skips_cancelled_and_pending_runs() -> None:
    runs = _streak(None, FIRST_SHA, LATER_SHA)
    extra = _runs(("in_progress", None, timedelta(minutes=1)), ("completed", "cancelled", timedelta(minutes=15)))
    for item, sha in zip(extra, (LATER_SHA, LATER_SHA), strict=True):
        item["headSha"] = sha
    extra[0]["databaseId"], extra[1]["databaseId"] = 900, 901
    run = ArmRun(runs + extra, pulls_by_sha={FIRST_SHA: _merged_pulls(3200), LATER_SHA: _merged_pulls(3220)})
    message = trunk_revert.arm_gate("fix/thing", run=run)
    assert message is not None
    assert "PR #3200" in message


def _long_streak(red: int, *, green_first: bool) -> list[dict]:
    conclusions = (["success"] if green_first else []) + ["failure"] * red
    runs = _runs(*[("completed", c, timedelta(minutes=len(conclusions) - i + 10)) for i, c in enumerate(conclusions)])
    for index, item in enumerate(runs):
        item["headSha"] = f"{index:040x}"
    return runs


def test_arm_gate_finds_a_streak_start_beyond_the_first_page() -> None:
    runs = _long_streak(40, green_first=True)
    first_red = runs[1]["headSha"]
    run = ArmRun(runs, pulls_by_sha={first_red: _merged_pulls(3200)})
    message = trunk_revert.arm_gate("fix/thing", run=run)
    assert message is not None
    assert "PR #3200" in message
    assert [cmd[cmd.index("--limit") + 1] for cmd in run.calls if cmd[:3] == ["gh", "run", "list"]] == ["30", "100"]


def test_arm_gate_exempts_the_revert_of_a_streak_start_beyond_the_first_page() -> None:
    runs = _long_streak(40, green_first=True)
    first_red = runs[1]["headSha"]
    commits = [{"messageHeadline": "Revert", "messageBody": f"This reverts commit {first_red}."}]
    run = ArmRun(runs, branch="fix/revert-3200", commits=commits)
    assert trunk_revert.arm_gate("fix/revert-3200", run=run) is None


def test_arm_gate_treats_the_oldest_run_as_the_start_when_history_is_exhausted() -> None:
    runs = _long_streak(40, green_first=False)
    oldest = runs[0]["headSha"]
    run = ArmRun(runs, pulls_by_sha={oldest: _merged_pulls(3200)})
    message = trunk_revert.arm_gate("fix/thing", run=run)
    assert message is not None
    assert "PR #3200" in message


def test_arm_gate_fails_closed_when_the_streak_outruns_the_bounded_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(trunk_revert, "RUN_WINDOWS", (30, 100))
    runs = _long_streak(150, green_first=True)
    commits = [{"messageHeadline": "Revert", "messageBody": f"This reverts commit {runs[120]['headSha']}."}]
    run = ArmRun(runs, branch="fix/revert-x", commits=commits)
    with pytest.raises(trunk_revert.TrunkError, match="cannot be established"):
        trunk_revert.arm_gate("fix/revert-x", run=run)


def test_arm_gate_fails_closed_when_runs_are_unreadable() -> None:
    with pytest.raises(trunk_revert.TrunkError):
        trunk_revert.arm_gate("fix/thing", run=ArmRun([], runs_code=1))


def test_arm_gate_fails_closed_when_develop_commits_are_unreadable() -> None:
    run = ArmRun(_red_runs(("completed", "failure", timedelta(minutes=10))), develop_code=1)
    with pytest.raises(trunk_revert.TrunkError):
        trunk_revert.arm_gate("fix/thing", run=run)


def test_main_arm_gate_resolves_the_pr_head_branch(capsys: pytest.CaptureFixture[str]) -> None:
    run = ArmRun(_red_runs(("completed", "failure", timedelta(minutes=10))), branch="feat/other")
    assert trunk_revert.main(["arm-gate", "--pr", "9"], run=run) == 1
    err = capsys.readouterr().err
    assert "Refusing to arm 9" in err
    assert "PR #77" in err


@pytest.mark.parametrize("args", [[], ["--pr", "0"], ["--pr", "3", "--branch", "fix/x"]])
def test_main_arm_gate_requires_exactly_one_selector(args: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert trunk_revert.main(["arm-gate", *args]) == 2
    assert "exactly one of" in capsys.readouterr().err


@pytest.mark.parametrize("target", ["pr-arm", "pr-landing-ready ARM=1", "pr-arm-remote", "pr-landing-remote-ready"])
def test_arming_targets_run_the_arm_gate_before_arming(target: str) -> None:
    done = subprocess.run(
        ["make", "-n", "--no-print-directory", *target.split(), "PR=7", "HEAD=" + "a" * 40, "EVIDENCE=e.json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    gate = done.stdout.index("trunk_revert.py arm-gate")
    arm = max(
        done.stdout.index(name)
        for name in ("pr_arm.py", "pr_landing.py", "remote_closeout.py arm", "remote_closeout.py ready")
        if name in done.stdout
    )
    assert gate < arm


def test_status_names_the_first_red_commit_of_the_streak() -> None:
    message = trunk_revert.status(run=_streak_run())
    assert message is not None
    assert "PR #3200" in message
    assert "make trunk-revert PR=3200" in message
    assert "3220" not in message


def test_status_is_clear_when_trunk_is_green() -> None:
    run = ArmRun(_red_runs(("completed", "success", timedelta(minutes=10))))
    assert trunk_revert.status(run=run) is None


def test_status_is_clear_once_the_culprit_is_reverted() -> None:
    run = _streak_run(develop=[{"commit": {"message": f"Revert x\n\nThis reverts commit {FIRST_SHA}."}}])
    assert trunk_revert.status(run=run) is None


def test_main_status_exits_nonzero_naming_the_culprit(capsys: pytest.CaptureFixture[str]) -> None:
    assert trunk_revert.main(["status"], run=_streak_run()) == 1
    assert "PR #3200" in capsys.readouterr().err


def test_main_status_exits_zero_when_trunk_is_green() -> None:
    run = ArmRun(_red_runs(("completed", "success", timedelta(minutes=10))))
    assert trunk_revert.main(["status"], run=run) == 0


def test_main_status_fails_closed_when_runs_are_unreadable(capsys: pytest.CaptureFixture[str]) -> None:
    run = ArmRun(_red_runs(("completed", "failure", timedelta(minutes=10))), runs_code=1)
    assert trunk_revert.main(["status"], run=run) == 1
    assert "could not establish" in capsys.readouterr().err


def test_gate_names_the_first_red_commit_of_the_streak() -> None:
    runs = _streak(None, FIRST_SHA, LATER_SHA)
    run = ArmRun(runs, pulls_by_sha={FIRST_SHA: _merged_pulls(3200), LATER_SHA: _merged_pulls(3220)})
    message = trunk_revert.trunk_gate("fix/thing", run=run, now=NOW + timedelta(days=1))
    assert message is not None
    assert "PR #3200" in message
    assert "make trunk-revert PR=3200" in message


REPLAY = json.loads((ROOT / "tests/fixtures/trunk_red_20261024.json").read_text(encoding="utf-8"))
FIRST_RED_RUN = 40071232825
LATER_RED_RUN = 40071524792
FIX_HEAD = REPLAY["pr"]["headRefOid"]
OLDER_HEAD = "09aab4e9cab4cc83101db0677fa755272ea7a58a"
FIRST_RED_COMPLETED = datetime(2026, 10, 24, 7, 2, 1, tzinfo=UTC)


def _replayed_runs() -> list[dict]:
    runs = []
    for item in REPLAY["runs"]:
        created = datetime.fromisoformat(item["createdAt"].replace("Z", "+00:00"))
        runs.append(
            {
                **item,
                "status": "completed",
                "updatedAt": (created + timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "url": f"https://github.com/BenchBox-dev/BenchBox/actions/runs/{item['databaseId']}",
            }
        )
    return runs


def _authorization(
    head: str = FIX_HEAD,
    run_id: int = FIRST_RED_RUN,
    *,
    body: str | None = None,
    login: str = "joeharris76",
    user_type: str = "User",
    created_at: datetime = FIRST_RED_COMPLETED + timedelta(minutes=30),
    edited: bool = False,
) -> dict:
    created = created_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "login": login,
        "user_type": user_type,
        "body": body if body is not None else f"Fix-forward authorized: {head} for trunk run {run_id}",
        "created_at": created,
        "updated_at": (created_at + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ") if edited else created,
    }


class FixForwardRun(ArmRun):
    def __init__(
        self, comments: list[dict], *, head: str = FIX_HEAD, number: int = 3385, indent: int | None = None
    ) -> None:
        super().__init__(
            _replayed_runs(),
            branch=REPLAY["pr"]["headRefName"],
            pulls_by_sha={
                "24bfcf8a0fb048c9ad554ef2e9e99066f7027167": _merged_pulls(3380),
                "c962e3e8733e9107988ba05145d09a27717a8fda": _merged_pulls(3383),
            },
        )
        self.comments = comments
        self.head = head
        self.number = number
        self.indent = indent

    def __call__(self, cmd: list[str]) -> tuple[int, str]:
        if cmd[:3] == ["gh", "pr", "view"]:
            self.calls.append(cmd)
            payload = {
                "headRefName": self.branch,
                "commits": self.commits,
                "number": self.number,
                "headRefOid": self.head,
            }
            return 0, json.dumps(payload)
        if cmd[:3] == ["gh", "api", "--paginate"]:
            self.calls.append(cmd)
            assert cmd[3] == f"repos/{trunk_revert.REPOSITORY}/issues/{self.number}/comments"
            return 0, "\n".join(json.dumps(comment, indent=self.indent) for comment in self.comments)
        return super().__call__(cmd)


def test_replay_of_the_2026_10_24_red_streak_refuses_the_fix_forward_pr_without_authorization() -> None:
    message = trunk_revert.arm_gate(str(REPLAY["pr"]["number"]), run=FixForwardRun([]))
    assert message is not None
    assert "PR #3380" in message
    assert f"for trunk run {FIRST_RED_RUN}" in message


def test_replay_of_the_2026_10_24_red_streak_admits_the_authorized_fix_forward_pr() -> None:
    run = FixForwardRun([_authorization()])
    assert trunk_revert.arm_gate(str(REPLAY["pr"]["number"]), run=run) is None


def test_authorization_for_the_current_head_and_first_red_run_arms() -> None:
    assert trunk_revert.arm_gate("3385", run=FixForwardRun([_authorization()])) is None


@pytest.mark.parametrize("indent", [None, 2])
def test_authorization_is_read_from_compact_or_indented_comment_listings(indent: int | None) -> None:
    run = FixForwardRun([_authorization(body="unrelated"), _authorization()], indent=indent)
    assert trunk_revert.arm_gate("3385", run=run) is None


def test_arm_gate_fails_closed_when_the_comment_listing_is_unreadable() -> None:
    class Garbled(FixForwardRun):
        def __call__(self, cmd: list[str]) -> tuple[int, str]:
            if cmd[:3] == ["gh", "api", "--paginate"]:
                return 0, "not json"
            return super().__call__(cmd)

    with pytest.raises(trunk_revert.TrunkError):
        trunk_revert.arm_gate("3385", run=Garbled([]))


def test_authorization_is_found_among_unrelated_comments() -> None:
    comments = [{**_authorization(body="Owner instruction: fix forward"), "login": "someone"}, _authorization()]
    assert trunk_revert.arm_gate("3385", run=FixForwardRun(comments)) is None


@pytest.mark.parametrize(
    "comment",
    [
        pytest.param(_authorization(head=OLDER_HEAD), id="older-head"),
        pytest.param(_authorization(run_id=LATER_RED_RUN), id="later-failing-run"),
        pytest.param(_authorization(run_id=40069594552), id="green-run"),
        pytest.param(_authorization(edited=True), id="edited"),
        pytest.param(_authorization(login="someone-else"), id="non-allowlisted-login"),
        pytest.param(_authorization(user_type="Bot"), id="bot"),
        pytest.param(_authorization(created_at=FIRST_RED_COMPLETED - timedelta(minutes=1)), id="before-run-completed"),
        pytest.param(_authorization(created_at=FIRST_RED_COMPLETED), id="at-run-completion"),
        pytest.param(
            _authorization(body=f"Fix-forward authorized: {FIX_HEAD} for trunk run {FIRST_RED_RUN} please"), id="suffix"
        ),
        pytest.param(
            _authorization(body=f"Looks good.\nFix-forward authorized: {FIX_HEAD} for trunk run {FIRST_RED_RUN}"),
            id="prefix",
        ),
        pytest.param(
            _authorization(body=f"Fix-forward authorized: {FIX_HEAD[:9]} for trunk run {FIRST_RED_RUN}"), id="short-sha"
        ),
        pytest.param(
            _authorization(body=f"fix-forward authorized: {FIX_HEAD} for trunk run {FIRST_RED_RUN}"), id="wrong-case"
        ),
    ],
)
def test_arm_gate_refuses_an_authorization_that_does_not_qualify(comment: dict) -> None:
    assert trunk_revert.arm_gate("3385", run=FixForwardRun([comment])) is not None


def test_a_fix_forward_looking_branch_without_authorization_is_refused() -> None:
    run = FixForwardRun([])
    assert run.branch.startswith("fix/")
    assert trunk_revert.arm_gate("3385", run=run) is not None


def test_a_push_after_authorization_voids_it() -> None:
    run = FixForwardRun([_authorization()], head="f" * 40)
    assert trunk_revert.arm_gate("3385", run=run) is not None


def test_arm_gate_fails_closed_when_the_runs_are_unreadable_despite_an_authorization() -> None:
    run = FixForwardRun([_authorization()])
    run.runs_code = 1
    with pytest.raises(trunk_revert.TrunkError):
        trunk_revert.arm_gate("3385", run=run)


def test_main_arm_gate_admits_an_authorized_pr_and_refuses_an_unauthorized_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert trunk_revert.main(["arm-gate", "--pr", "3385"], run=FixForwardRun([_authorization()])) == 0
    assert trunk_revert.main(["arm-gate", "--pr", "3385"], run=FixForwardRun([])) == 1
    assert "Refusing to arm 3385" in capsys.readouterr().err


def test_main_gate_opens_a_draft_while_develop_has_been_red_over_thirty_minutes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    run = FixForwardRun([])
    assert trunk_revert.main(["gate", "--branch", "fix/comment-policy-notice-paths"], run=run) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "draft"
    assert "Opening as a draft" in captured.err
    assert trunk_revert.FIX_FORWARD_FORMAT in captured.err


def test_main_gate_opens_a_draft_that_arming_still_refuses_until_authorized() -> None:
    run = FixForwardRun([])
    assert trunk_revert.main(["gate", "--branch", "fix/comment-policy-notice-paths"], run=run) == 0
    assert trunk_revert.main(["arm-gate", "--pr", "3385"], run=run) == 1
    run.comments.append(_authorization())
    assert trunk_revert.main(["arm-gate", "--pr", "3385"], run=run) == 0


def test_main_gate_opens_ready_when_develop_has_been_red_under_thirty_minutes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    run = FixForwardRun([])
    just_now = datetime.now(UTC) - timedelta(minutes=5)
    for item in run.runs:
        item["updatedAt"] = just_now.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert trunk_revert.main(["gate", "--branch", "fix/comment-policy-notice-paths"], run=run) == 0
    assert capsys.readouterr().out.strip() == "ready"


def test_main_gate_opens_a_revert_branch_ready_even_when_develop_has_long_been_red(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert trunk_revert.main(["gate", "--branch", "fix/revert-3380"], run=FixForwardRun([])) == 0
    assert capsys.readouterr().out.strip() == "ready"
