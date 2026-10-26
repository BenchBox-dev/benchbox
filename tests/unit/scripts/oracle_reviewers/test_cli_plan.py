from __future__ import annotations

import hashlib
import json
import re
import stat
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from _project.scripts.oracle_reviewers import cli, github, protocol, render
from _project.scripts.oracle_reviewers.dedup import fingerprint
from _project.scripts.oracle_reviewers.retry import State

from .conftest import POLICY_PATH

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO = "BenchBox-dev/BenchBox"
HEAD = "a" * 40
BASE = "b" * 40
DIFF = "diff --git a/x b/x\n"
MODE = "100644"
BUCKETS = ["trivial", "small", "medium", "large", "huge", "max"]


def _pull(**over: Any) -> dict[str, Any]:
    pull = {
        "number": 7,
        "state": "open",
        "draft": False,
        "base": {"ref": "develop", "sha": BASE},
        "head": {"sha": HEAD, "repo": {"full_name": REPO}},
    }
    pull.update(over)
    return pull


MERGE_BASE = "d" * 40


def _blob(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _sections(diff: str | None) -> dict[str, tuple[str, str, str]]:
    found = {}
    for section in re.split(r"(?m)^(?=diff --git )", diff or ""):
        header = re.match(r"diff --git a/(\S+) b/(\S+)\n", section)
        if header is None:
            continue
        old = re.search(r"(?m)^old mode (\d+)$", section)
        new = re.search(r"(?m)^new mode (\d+)$", section)
        found[header.group(2)] = (old.group(1) if old else MODE, new.group(1) if new else MODE, _blob(section))
    return found


def _head_entry(diff: str | None, path: str) -> tuple[str, str]:
    _, mode, blob = _sections(diff).get(path, (MODE, MODE, _blob(f"head:{path}")))
    return mode, blob


def _base_entry(diff: str | None, path: str, tag: str = "") -> tuple[str, str]:
    mode = _sections(diff).get(path, (MODE, MODE, ""))[0]
    return mode, _blob(f"base:{tag}:{path}")


def _states(diff: str, tag: str = "") -> list[protocol.FileState]:
    return [
        protocol.file_state(path, None, _base_entry(diff, path, tag), _head_entry(diff, path))
        for path in _sections(diff)
    ]


class FakeGitHub:
    def __init__(
        self,
        pull: dict[str, Any],
        files: list[dict[str, Any]],
        state: State | None = None,
        threads: Sequence[dict[str, Any]] | None = (),
        diff: str | None = DIFF,
    ) -> None:
        self.pull = pull
        self.files = files
        self.state = state
        self.threads = None if threads is None else list(threads)
        self.diff = diff
        self.dispatched: list[int] = []
        self.base_tag = ""
        self.merge_base: str | None = MERGE_BASE
        self.reviews: list[dict[str, Any]] | None = []
        self.reread_sha: str | None = None
        self._pull_reads = 0
        self.runs: list[dict[str, Any]] = []
        self.committed = datetime.now(UTC) - timedelta(days=1)
        self.comments: list[dict[str, Any]] = []
        self.failing_prs: set[int] = set()

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(github, "get_json", self._json)
        monkeypatch.setattr(github, "get_paginated", self._paginated)
        monkeypatch.setattr(github, "get_diff", self._diff)
        monkeypatch.setattr(github, "review_threads", self._threads)
        monkeypatch.setattr(github, "latest_state", lambda repo, pr: self.state)
        monkeypatch.setattr(github, "dispatch", self._dispatch)
        monkeypatch.setattr(github, "verdict_runs", lambda repo, workflow, since: list(self.runs))
        monkeypatch.setattr(github, "committed_at", lambda repo, sha: self.committed)
        monkeypatch.setattr(github, "oracle_reviews", self._reviews)
        monkeypatch.setattr(github, "merge_base", lambda repo, base, head: self.merge_base)
        monkeypatch.setattr(github, "tree_blobs", self._tree)

    def _dispatch(self, repo: str, workflow: str, pr: int) -> None:
        if pr in self.failing_prs:
            raise github.GitHubError(f"could not dispatch {workflow} for #{pr}")
        self.dispatched.append(pr)

    def _reviews(self, repo: str, pr: int) -> list[dict[str, Any]]:
        if self.reviews is None:
            raise github.GitHubError("reviews unavailable")
        return self.reviews

    def _json(self, path: str) -> dict[str, Any]:
        if path == f"repos/{REPO}/pulls/7":
            self._pull_reads += 1
            if self._pull_reads > 1 and self.reread_sha is not None:
                return {**self.pull, "head": {"sha": self.reread_sha, "repo": {"full_name": REPO}}}
        return self.pull

    def _threads(self, repo: str, pr: int) -> list[dict[str, Any]]:
        if self.threads is None:
            raise github.GitHubError("threads unavailable")
        return self.threads

    def _diff(self, repo: str, pr: int) -> bytes | None:
        return None if self.diff is None else self.diff.encode("utf-8")

    def _tree(self, repo: str, sha: str) -> dict[str, tuple[str, str]] | None:
        paths = [(item.get("previous_filename") or item["filename"], item["filename"]) for item in self.files]
        if sha == self.merge_base:
            return {old: _base_entry(self.diff, new, self.base_tag) for old, new in paths}
        if sha == HEAD:
            return {new: _head_entry(self.diff, new) for _, new in paths}
        return None

    def _paginated(self, path: str) -> list[dict[str, Any]]:
        if "/issues/" in path and path.split("?")[0].endswith("/comments"):
            return self.comments
        if path.startswith(f"repos/{REPO}/pulls?"):
            return [self.pull]
        return [{**item, "sha": _head_entry(self.diff, item["filename"])[1]} for item in self.files]


def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, event_name: str, event: dict[str, Any], ref: str) -> Path:
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(event), encoding="utf-8")
    output = tmp_path / "out.txt"
    output.write_text("", encoding="utf-8")
    for key, value in {
        "GITHUB_REPOSITORY": REPO,
        "GITHUB_RUN_ID": "99",
        "GITHUB_EVENT_NAME": event_name,
        "GITHUB_EVENT_PATH": str(event_path),
        "GITHUB_REF": ref,
        "GITHUB_OUTPUT": str(output),
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    return output


def _plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake: FakeGitHub,
    *,
    event_name: str = "pull_request_target",
    event: dict[str, Any] | None = None,
    ref: str = "refs/heads/develop",
) -> tuple[int, dict[str, str], dict[str, Any]]:
    fake.install(monkeypatch)
    output = _env(monkeypatch, tmp_path, event_name, event or {"action": "synchronize"}, ref)
    out_dir = tmp_path / "plan"
    code = cli.main(["plan", "--policy", str(POLICY_PATH), "--pr", "7", "--out", str(out_dir)])
    values = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())
    plan = json.loads((out_dir / "plan.json").read_text(encoding="utf-8")) if (out_dir / "plan.json").is_file() else {}
    return code, values, plan


SOUNDNESS = [{"filename": "benchbox/core/equivalence/checker.py", "additions": 4, "deletions": 1}]
CHECKER = "benchbox/core/equivalence/checker.py"
CAPTURE = "benchbox/core/expected_results/registry.py"


def test_soundness_pr_plans_a_review_over_the_bucket_chain(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    code, values, plan = _plan(monkeypatch, tmp_path, FakeGitHub(_pull(), SOUNDNESS))
    assert code == 0
    assert values["decision"] == "review" and values["post"] == "true" and values["head_sha"] == HEAD
    assert plan["scope_paths"] == [CHECKER]
    assert plan["gate_paths"] == []
    assert any("comparison, validation or result logic" in reason for reason in plan["scope_reasons"])
    assert [item["name"] for item in plan["chain"]] == BUCKETS
    assert {item["harness"] for item in plan["chain"]} == {"omp"}
    assert "tier" not in plan and "excluded_families" not in plan
    assert plan["brief_mode"] == "inline"
    assert plan["scope"] == "full" and plan["reviewed_head"] is None and plan["restart_summary"] is None
    assert plan["diff_inline"] is not None and plan["reviewed_files"] == [
        {"path": CHECKER, "previous_path": None, "additions": 4, "deletions": 1, "status": ""}
    ]
    assert plan["unchanged_files"] == [] and plan["untracked_files"] == []
    assert plan["max_defects"] == 10 and plan["brief_max_bytes"] == 100000
    assert plan["evidence_files"] == [CHECKER]
    assert plan["protocol"]["kind"] == "first" and plan["protocol"]["prior"] == []
    assert plan["protocol"]["strikes"] == 0 and plan["open_findings"] == {}
    patch = tmp_path / "plan" / "diff.patch"
    assert stat.S_IMODE(patch.stat().st_mode) == 0o600
    assert patch.read_text(encoding="utf-8") == DIFF
    assert not (tmp_path / "plan" / "brief.md").exists()


def test_gate_only_change_is_reviewed_with_gate_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    gate = "_project/scripts/oracle_reviewers/cli.py"
    files = [{"filename": gate, "additions": 2, "deletions": 0}]
    _, values, plan = _plan(monkeypatch, tmp_path, FakeGitHub(_pull(), files))
    assert values["decision"] == "review"
    assert plan["scope_paths"] == [gate] and plan["gate_paths"] == [gate]
    assert any("gate" in reason for reason in plan["scope_reasons"])
    assert plan["evidence_files"] == [gate]
    assert [item["name"] for item in plan["chain"]] == BUCKETS


def test_evidence_files_are_the_soundness_files_present_at_the_head(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    files = [
        *SOUNDNESS,
        {"filename": "benchbox/core/equivalence/gone.py", "additions": 0, "deletions": 3, "status": "removed"},
        {"filename": "docs/notes.md", "additions": 1, "deletions": 0},
    ]
    _, values, plan = _plan(monkeypatch, tmp_path, FakeGitHub(_pull(), files))
    assert values["decision"] == "review"
    assert plan["evidence_files"] == ["benchbox/core/equivalence/checker.py"]
    assert plan["scope_paths"] == ["benchbox/core/equivalence/checker.py", "benchbox/core/equivalence/gone.py"]


def test_exempt_only_pr_posts_success_without_reviewers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    files = [{"filename": "docs/notes.md", "additions": 1, "deletions": 0}]
    _, values, plan = _plan(monkeypatch, tmp_path, FakeGitHub(_pull(), files))
    assert values["decision"] == "success" and values["post"] == "true"
    assert plan["chain"] == [] and plan["scope_paths"] == [] and plan["gate_paths"] == []
    assert plan["decision_reason"] == "only exempt paths changed"
    assert not (tmp_path / "plan" / "diff.patch").exists()


def test_docs_only_pr_posts_success_without_reviewers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    files = [{"filename": "docs/guide/setup.md", "additions": 10, "deletions": 2}]
    _, values, plan = _plan(monkeypatch, tmp_path, FakeGitHub(_pull(), files))
    assert values["decision"] == "success" and values["post"] == "true"
    assert plan["chain"] == [] and plan["scope_paths"] == []
    assert plan["decision_reason"] == "only exempt paths changed"


def test_empty_file_list_posts_success_without_reviewers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _, values, plan = _plan(monkeypatch, tmp_path, FakeGitHub(_pull(), []))
    assert values["decision"] == "success" and values["post"] == "true"
    assert plan["chain"] == [] and plan["scope_paths"] == []
    assert plan["decision_reason"] == "only exempt paths changed"


def test_code_outside_the_soundness_list_requires_review(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    helper = "benchbox/utils/row_compare.py"
    files = [{"filename": helper, "additions": 3, "deletions": 1}]
    _, values, plan = _plan(monkeypatch, tmp_path, FakeGitHub(_pull(), files))
    assert values["decision"] == "review" and values["post"] == "true"
    assert plan["scope_paths"] == [helper] and plan["gate_paths"] == []
    assert plan["scope_reasons"] == ["changes 1 files outside the exempt paths"]


def test_rename_from_docs_into_code_requires_review(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    files = [{"filename": "benchbox/new.py", "previous_filename": "docs/old.md", "additions": 5, "deletions": 0}]
    _, values, plan = _plan(monkeypatch, tmp_path, FakeGitHub(_pull(), files))
    assert values["decision"] == "review" and values["post"] == "true"
    assert plan["scope_paths"] == ["benchbox/new.py", "docs/old.md"]


def test_fork_pr_never_reaches_a_reviewer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pull = _pull(head={"sha": HEAD, "repo": {"full_name": "someone/BenchBox"}})
    _, values, plan = _plan(monkeypatch, tmp_path, FakeGitHub(pull, SOUNDNESS))
    assert values["decision"] == "fork" and values["post"] == "true"
    assert plan["decision_reason"] == "fork: owner review"
    assert plan["chain"] == [] and plan["scope_paths"] == [CHECKER]
    assert not (tmp_path / "plan" / "diff.patch").exists()


@pytest.mark.parametrize(
    ("pull", "reason"),
    [
        (_pull(state="closed"), "not open"),
        (_pull(base={"ref": "release", "sha": BASE}), "does not target develop"),
        (_pull(draft=True), "draft"),
    ],
)
def test_guards_skip_without_posting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, pull: dict[str, Any], reason: str
) -> None:
    _, values, plan = _plan(monkeypatch, tmp_path, FakeGitHub(pull, SOUNDNESS))
    assert values["decision"] == "skip" and values["post"] == "false"
    assert reason in plan["decision_reason"]


def test_stale_event_head_is_left_to_the_newer_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    event = {"action": "synchronize", "pull_request": {"head": {"sha": "c" * 40}}}
    _, values, plan = _plan(monkeypatch, tmp_path, FakeGitHub(_pull(), SOUNDNESS), event=event)
    assert values["decision"] == "skip"
    assert "newer head" in plan["decision_reason"]


def test_moved_head_is_left_to_the_newer_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = FakeGitHub(_pull(), SOUNDNESS)
    fake.reread_sha = "d" * 40
    _, values, plan = _plan(monkeypatch, tmp_path, fake)
    assert values["decision"] == "skip" and values["post"] == "false"
    assert "head moved" in plan["decision_reason"]


def test_comment_run_resolves_the_head_through_the_api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    event = {"action": "created", "issue": {"number": 7, "pull_request": {}}, "comment": {"body": "/oracle-review"}}
    _, values, plan = _plan(
        monkeypatch, tmp_path, FakeGitHub(_pull(), SOUNDNESS), event_name="issue_comment", event=event
    )
    assert values["decision"] == "review"
    assert plan["head_sha"] == HEAD
    assert plan["manual"] is True


def test_dispatch_outside_develop_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    code, values, _ = _plan(
        monkeypatch,
        tmp_path,
        FakeGitHub(_pull(), SOUNDNESS),
        event_name="workflow_dispatch",
        event={"inputs": {"pr": "7"}},
        ref="refs/heads/feature",
    )
    assert code == 2
    assert values == {}


def test_comment_rerun_after_a_decisive_result_replans_the_same_head(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = State(7, HEAD, "success", datetime.now(UTC) - timedelta(hours=3))
    _, values, plan = _plan(
        monkeypatch, tmp_path, FakeGitHub(_pull(), SOUNDNESS, state), event_name="issue_comment", event={}
    )
    assert values["decision"] == "review" and values["post"] == "true"
    assert "manual same-head re-review within budget" in plan["decision_reason"]


def test_manual_retry_inside_backoff_waits_for_the_next_slot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    now = datetime.now(UTC)
    state = State(7, HEAD, "pending", now, (now,), pending_cause="all-absent")
    _, values, plan = _plan(
        monkeypatch, tmp_path, FakeGitHub(_pull(), SOUNDNESS, state), event_name="issue_comment", event={}
    )
    assert values["decision"] == "skip" and values["post"] == "false"
    assert "backoff" in plan["decision_reason"]


def test_pending_pool_reset_is_carried_into_the_plan(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    later = datetime.now(UTC) + timedelta(hours=2)
    earlier = datetime.now(UTC) - timedelta(hours=1)
    state = State(7, "c" * 40, "pending", earlier, (), {"claude": later})
    _, _, plan = _plan(monkeypatch, tmp_path, FakeGitHub(_pull(), SOUNDNESS, state))
    assert plan["pool_blocked_until"] == {"claude": later.isoformat()}


def test_invalid_pr_number_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    FakeGitHub(_pull(), SOUNDNESS).install(monkeypatch)
    _env(monkeypatch, tmp_path, "workflow_dispatch", {}, "refs/heads/develop")
    with pytest.raises(SystemExit, match="invalid pull request number"):
        cli.main(["plan", "--policy", str(POLICY_PATH), "--pr", "7; rm -rf /", "--out", str(tmp_path / "p")])


def test_sweep_dispatches_only_due_pending_prs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    due = State(7, HEAD, "pending", datetime.now(UTC) - timedelta(hours=2), pending_cause="all-absent")
    fake = FakeGitHub(_pull(), SOUNDNESS, due)
    fake.install(monkeypatch)
    _env(monkeypatch, tmp_path, "schedule", {}, "refs/heads/develop")
    assert cli.main(["sweep", "--policy", str(POLICY_PATH)]) == 0
    assert fake.dispatched == [7]
    fresh = FakeGitHub(_pull(), SOUNDNESS, State(7, HEAD, "pending", datetime.now(UTC), pending_cause="all-absent"))
    fresh.install(monkeypatch)
    assert cli.main(["sweep", "--policy", str(POLICY_PATH)]) == 0
    assert fresh.dispatched == []


def _due_state() -> State:
    return State(7, HEAD, "pending", datetime.now(UTC) - timedelta(hours=2), pending_cause="all-absent")


_RUN_IDS = iter(range(1000, 100000))


def _verdict_run(
    pr: int | str,
    *,
    status: str = "completed",
    conclusion: str | None = "success",
    event: str = "workflow_dispatch",
    age: timedelta = timedelta(hours=1),
) -> dict[str, Any]:
    return {
        "id": next(_RUN_IDS),
        "display_title": f"oracle-verdict ({event} pr={pr})",
        "event": event,
        "status": status,
        "conclusion": conclusion,
        "created_at": (datetime.now(UTC) - age).isoformat(),
    }


def _sweep(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake: FakeGitHub, event: str = "schedule") -> int:
    fake.install(monkeypatch)
    _env(monkeypatch, tmp_path, event, {}, "refs/heads/develop")
    return cli.main(["sweep", "--policy", str(POLICY_PATH)])


@pytest.mark.parametrize("status", ["queued", "in_progress", "waiting", "pending", "requested"])
@pytest.mark.parametrize("event", ["workflow_dispatch", "pull_request_target", "issue_comment"])
def test_sweep_does_not_dispatch_over_a_run_still_in_flight(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, status: str, event: str
) -> None:
    fake = FakeGitHub(_pull(), SOUNDNESS, _due_state())
    fake.runs = [_verdict_run(7, status=status, conclusion=None, event=event)]
    assert _sweep(monkeypatch, tmp_path, fake, "push") == 0
    assert fake.dispatched == []


def test_sweep_treats_a_run_started_moments_ago_as_in_flight(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = FakeGitHub(_pull(), SOUNDNESS, _due_state())
    fake.runs = [_verdict_run(7, age=timedelta(seconds=20))]
    assert _sweep(monkeypatch, tmp_path, fake) == 0
    assert fake.dispatched == []


def test_sweep_dispatches_once_when_only_other_runs_are_in_flight(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeGitHub(_pull(), SOUNDNESS, _due_state())
    fake.runs = [
        _verdict_run(70, status="queued", conclusion=None),
        _verdict_run("sweep", status="queued", conclusion=None, event="push"),
        _verdict_run("sweep", status="in_progress", conclusion=None, event="schedule"),
        _verdict_run(17, status="in_progress", conclusion=None),
    ]
    assert _sweep(monkeypatch, tmp_path, fake, "push") == 0
    assert fake.dispatched == [7]


def test_sweep_reads_every_page_of_runs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = FakeGitHub(_pull(), SOUNDNESS, _due_state())
    fake.runs = [_verdict_run(900 + index) for index in range(250)] + [
        _verdict_run(7, status="in_progress", conclusion=None, age=timedelta(days=3))
    ]
    assert _sweep(monkeypatch, tmp_path, fake) == 0
    assert fake.dispatched == []


def test_the_run_listing_follows_every_page(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [{"workflow_runs": [{"id": index}]} for index in range(3)]
    seen: list[tuple[str, ...]] = []

    def fake_gh(*args: str, accept: str | None = None) -> str:
        seen.append(args)
        return json.dumps(pages)

    monkeypatch.setattr(github, "_gh", fake_gh)
    assert github.verdict_runs(REPO, "oracle-verdict.yml", datetime(2026, 10, 19, tzinfo=UTC)) == [
        {"id": 0},
        {"id": 1},
        {"id": 2},
    ]
    assert seen[0][:2] == ("--paginate", "--slurp")
    assert "created=%3E%3D2026-10-19T00%3A00%3A00Z" in seen[0][2]


def _stateless_fake(*runs: dict[str, Any]) -> FakeGitHub:
    fake = FakeGitHub(_pull(), SOUNDNESS)
    fake.runs = list(runs)
    return fake


def test_a_head_whose_run_failed_without_state_is_redispatched_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    failed = _verdict_run(7, conclusion="failure", event="pull_request_target", age=timedelta(hours=3))
    fake = _stateless_fake(failed)
    assert _sweep(monkeypatch, tmp_path, fake) == 0
    assert fake.dispatched == [7]
    second = _verdict_run(7, conclusion="failure", event="workflow_dispatch", age=timedelta(hours=2))
    again = _stateless_fake(second, failed)
    assert _sweep(monkeypatch, tmp_path, again) == 0
    assert again.dispatched == []


def test_a_failure_before_the_head_was_committed_is_not_retried(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _stateless_fake(_verdict_run(7, conclusion="failure", age=timedelta(days=3)))
    assert _sweep(monkeypatch, tmp_path, fake) == 0
    assert fake.dispatched == []


@pytest.mark.parametrize("conclusion", ["failure", "timed_out", "cancelled"])
def test_every_unfinished_conclusion_counts_as_a_failure_without_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, conclusion: str
) -> None:
    fake = _stateless_fake(_verdict_run(7, conclusion=conclusion))
    assert _sweep(monkeypatch, tmp_path, fake) == 0
    assert fake.dispatched == [7]


@pytest.mark.parametrize("conclusion", ["success", "skipped"])
def test_a_run_that_finished_is_not_retried(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, conclusion: str) -> None:
    fake = _stateless_fake(
        _verdict_run(7, conclusion=conclusion, age=timedelta(minutes=30)), _verdict_run(7, conclusion="failure")
    )
    assert _sweep(monkeypatch, tmp_path, fake) == 0
    assert fake.dispatched == []


def test_a_failed_run_on_a_head_with_a_decisive_state_is_not_retried(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeGitHub(_pull(), SOUNDNESS, State(7, HEAD, "success", datetime.now(UTC) - timedelta(hours=3)))
    fake.runs = [_verdict_run(7, conclusion="failure")]
    assert _sweep(monkeypatch, tmp_path, fake) == 0
    assert fake.dispatched == []


def test_stateless_redispatch_is_bounded_by_the_daily_budget(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    policy_budget = cli.load_policy(POLICY_PATH).retry.daily_budget
    dispatches = [
        _verdict_run(7, conclusion="success", age=timedelta(hours=2 + index)) for index in range(policy_budget)
    ]
    fake = _stateless_fake(_verdict_run(7, conclusion="failure", event="pull_request_target"), *dispatches)
    assert _sweep(monkeypatch, tmp_path, fake) == 0
    assert fake.dispatched == []


def test_the_sweep_never_dispatches_more_than_its_cap(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cap = cli.load_policy(POLICY_PATH).retry.sweep_max_dispatches
    pulls = [
        _pull(number=number, head={"sha": HEAD, "repo": {"full_name": REPO}}) for number in range(20, 20 + cap + 3)
    ]
    fake = FakeGitHub(_pull(), SOUNDNESS)
    fake.runs = [_verdict_run(pull["number"], conclusion="failure") for pull in pulls]
    fake._paginated = lambda path: pulls if path.startswith(f"repos/{REPO}/pulls?") else []
    assert _sweep(monkeypatch, tmp_path, fake) == 0
    assert len(fake.dispatched) == cap


def test_one_pull_requests_error_does_not_stop_the_others(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pulls = [_pull(number=number) for number in (21, 22, 23)]
    fake = FakeGitHub(_pull(), SOUNDNESS)
    fake.runs = [_verdict_run(pull["number"], conclusion="failure") for pull in pulls]
    fake.failing_prs = {22}
    fake._paginated = lambda path: pulls if path.startswith(f"repos/{REPO}/pulls?") else []
    assert _sweep(monkeypatch, tmp_path, fake) == 1
    assert fake.dispatched == [21, 23]
    captured = capsys.readouterr()
    assert "#22" in captured.out
    assert "could not handle 1 pull request" in captured.err


def test_a_state_that_cannot_be_read_isolates_to_its_pull_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pulls = [_pull(number=number) for number in (21, 22)]
    fake = FakeGitHub(_pull(), SOUNDNESS)
    fake.runs = [_verdict_run(pull["number"], conclusion="failure") for pull in pulls]
    fake._paginated = lambda path: pulls if path.startswith(f"repos/{REPO}/pulls?") else []
    fake.install(monkeypatch)

    def latest(repo: str, pr: int) -> State | None:
        if pr == 21:
            raise github.GitHubError("artifact unreadable")
        return None

    monkeypatch.setattr(github, "latest_state", latest)
    _env(monkeypatch, tmp_path, "schedule", {}, "refs/heads/develop")
    assert cli.main(["sweep", "--policy", str(POLICY_PATH)]) == 1
    assert fake.dispatched == [22]


def _surface(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake: FakeGitHub, steps: list[str]
) -> tuple[int, dict[str, Any] | None, str]:
    fake.install(monkeypatch)
    monkeypatch.setattr(github, "failed_steps", lambda repo, run_id: steps)
    output = _env(monkeypatch, tmp_path, "pull_request_target", {}, "refs/heads/develop")
    out = tmp_path / "failure" / "status.json"
    code = cli.main(
        ["surface-failure", "--policy", str(POLICY_PATH), "--pr", "7", "--head-sha", HEAD, "--out", str(out)]
    )
    status = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
    return code, status, output.read_text(encoding="utf-8")


def test_a_first_failure_without_a_result_posts_no_status(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _stateless_fake(_verdict_run(7, status="in_progress", conclusion=None, event="pull_request_target"))
    code, status, output = _surface(monkeypatch, tmp_path, fake, ["Run the attempt loop"])
    assert code == 0 and status is None
    assert "post=false" in output


def test_a_second_failure_without_a_result_posts_a_status_naming_the_failed_step(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    earlier = _verdict_run(7, conclusion="failure", event="pull_request_target", age=timedelta(hours=2))
    fake = _stateless_fake(_verdict_run(99, status="in_progress", conclusion=None), earlier)
    steps = ["Scan the disk for secret values after the reviewer exits"]
    code, status, output = _surface(monkeypatch, tmp_path, fake, steps)
    assert code == 0 and "post=true" in output
    assert status is not None
    assert status["state"] == "pending" and status["context"] == "oracle-verdict"
    assert "Scan the disk for secret values after the reviewer exits" in status["description"]
    assert len(status["description"]) <= 140
    assert status["target_url"].endswith("/actions/runs/99")


def test_a_failure_after_a_finished_run_is_a_first_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    finished = _verdict_run(7, conclusion="success", age=timedelta(hours=1))
    older = _verdict_run(7, conclusion="failure", age=timedelta(hours=2))
    code, status, _ = _surface(monkeypatch, tmp_path, _stateless_fake(finished, older), ["Plan the review"])
    assert code == 0 and status is None


def test_a_second_failure_with_no_step_listed_still_posts_a_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _stateless_fake(_verdict_run(7, conclusion="failure"))
    _, status, _ = _surface(monkeypatch, tmp_path, fake, [])
    assert status is not None and "an earlier step" in status["description"]


def _reset_comments(head: str = HEAD, *, edited: bool = False) -> list[dict[str, Any]]:
    start = datetime.now(UTC) - timedelta(minutes=30)
    owner = {"login": "joeharris76", "type": "User"}
    return [
        {
            "user": owner,
            "body": f"Owner instruction: reset the backoff on {head}",
            "created_at": start.isoformat(),
            "updated_at": start.isoformat(),
        },
        {
            "user": owner,
            "body": f"Oracle backoff reset: {head}\nReason: the gateway was down",
            "created_at": (start + timedelta(minutes=5)).isoformat(),
            "updated_at": (start + timedelta(minutes=9 if edited else 5)).isoformat(),
        },
    ]


def _backed_off() -> State:
    now = datetime.now(UTC)
    return State(
        7, HEAD, "pending", now - timedelta(minutes=40), (now - timedelta(minutes=40),), pending_cause="all-absent"
    )


def test_the_sweep_dispatches_a_pending_head_after_the_owner_resets_its_backoff(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    waiting = FakeGitHub(_pull(), SOUNDNESS, _backed_off())
    assert _sweep(monkeypatch, tmp_path, waiting) == 0
    assert waiting.dispatched == []
    reset = FakeGitHub(_pull(), SOUNDNESS, _backed_off())
    reset.comments = _reset_comments()
    assert _sweep(monkeypatch, tmp_path, reset) == 0
    assert reset.dispatched == [7]
    edited = FakeGitHub(_pull(), SOUNDNESS, _backed_off())
    edited.comments = _reset_comments(edited=True)
    assert _sweep(monkeypatch, tmp_path, edited) == 0
    assert edited.dispatched == []


def test_a_manual_retry_inside_backoff_runs_once_after_the_owner_reset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    event = {"action": "created", "issue": {"number": 7, "pull_request": {}}, "comment": {"body": "/oracle-review"}}
    fake = FakeGitHub(_pull(), SOUNDNESS, _backed_off())
    fake.comments = _reset_comments()
    _, values, plan = _plan(monkeypatch, tmp_path, fake, event_name="issue_comment", event=event)
    assert values["decision"] == "review"
    assert "reset by the owner" in plan["decision_reason"]
    assert plan["backoff_reset_at"] is not None
    consumed = tmp_path / "consumed"
    consumed.mkdir()
    after = datetime.now(UTC)
    fake = FakeGitHub(_pull(), SOUNDNESS, State(7, HEAD, "pending", after, (after,), pending_cause="all-absent"))
    fake.comments = _reset_comments()
    _, values, plan = _plan(monkeypatch, consumed, fake, event_name="issue_comment", event=event)
    assert values["decision"] == "skip" and "backoff" in plan["decision_reason"]


@pytest.mark.parametrize(
    "pull",
    [_pull(draft=True), _pull(head={"sha": HEAD, "repo": {"full_name": "someone/BenchBox"}})],
    ids=["draft", "fork"],
)
def test_sweep_skips_draft_and_fork_prs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, pull: dict[str, Any]) -> None:
    due = State(7, HEAD, "pending", datetime.now(UTC) - timedelta(hours=2), pending_cause="all-absent")
    fake = FakeGitHub(pull, SOUNDNESS, due)
    looked_up: list[int] = []
    fake.install(monkeypatch)
    monkeypatch.setattr(github, "latest_state", lambda repo, pr: looked_up.append(pr) or due)
    _env(monkeypatch, tmp_path, "schedule", {}, "refs/heads/develop")
    assert cli.main(["sweep", "--policy", str(POLICY_PATH)]) == 0
    assert fake.dispatched == []
    assert looked_up == []


TWO_FILES = [
    {"filename": CHECKER, "additions": 4, "deletions": 1, "sha": "1" * 40},
    {"filename": CAPTURE, "additions": 2, "deletions": 0, "sha": "2" * 40},
]
TWO_FILE_DIFF = (
    f"diff --git a/{CHECKER} b/{CHECKER}\n--- a/{CHECKER}\n+++ b/{CHECKER}\n@@ -1,1 +1,2 @@\n a\n+checker change\n"
    f"diff --git a/{CAPTURE} b/{CAPTURE}\n--- a/{CAPTURE}\n+++ b/{CAPTURE}\n@@ -1,1 +1,2 @@\n a\n+capture change\n"
)
OLD_HEAD = "c" * 40


def _record(diff: str, decision: str, **over: Any) -> dict[str, Any]:
    paths = over.pop("paths", None)
    prints = protocol.fingerprints(_states(diff, over.pop("tag", "")))
    chosen = paths if paths is not None else sorted(prints)
    current = protocol.patch_map(prints, chosen)
    record = {
        "v": 1,
        "cycle": 1,
        "round": 1,
        "kind": "first",
        "decision": decision,
        "head_sha": OLD_HEAD,
        "base_ref": "develop",
        "reviewer": "medium",
        "tier": "medium",
        "strikes_after": 1 if decision == "DO_NOT_SHIP" else 0,
        "open_defects": [],
        "next_id": 1,
        "patch_digest": protocol.patch_digest(prints) if prints else f"unread-{OLD_HEAD}",
        "carried": False,
        "summary": "",
        **over,
    }
    return {**record, "patch_map": current}


def _review(record: dict[str, Any] | None, **over: Any) -> dict[str, Any]:
    body = f"### oracle-verdict: failure for `{OLD_HEAD}`\n\nDecision text.\n"
    if record is not None:
        patches = record.get("patch_map")
        body += "\n" + protocol.encode_marker({k: v for k, v in record.items() if k != "patch_map"}, patches)
    review = {
        "id": 1,
        "login": "benchbox-oracle[bot]",
        "user_type": "Bot",
        "state": "COMMENTED",
        "body": body,
        "submitted_at": "2026-10-08T10:00:00Z",
        "commit_id": OLD_HEAD,
    }
    review.update(over)
    return review


def _fake(
    *reviews: dict[str, Any], files: list[dict[str, Any]] | None = None, diff: str | None = TWO_FILE_DIFF, **pull: Any
):
    fake = FakeGitHub(_pull(**pull), files if files is not None else TWO_FILES, diff=diff)
    fake.reviews = list(reviews)
    return fake


DEFECT = {"id": "D1", "severity": "High", "file": CAPTURE, "line": 2, "end_line": None, "title": "Drops a row"}
REBASED = TWO_FILE_DIFF.replace("@@ -1,1 +1,2 @@", "@@ -7,1 +7,2 @@").replace(
    f"--- a/{CHECKER}", f"index 1111111..2222222 100644\n--- a/{CHECKER}"
)
CAPTURE_CHANGED = TWO_FILE_DIFF.replace("+capture change", "+capture fixed")


def test_no_review_history_gives_a_first_full_review(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _, values, plan = _plan(monkeypatch, tmp_path, _fake())
    assert values["decision"] == "review" and plan["scope"] == "full"
    assert plan["protocol"]["kind"] == "first" and plan["protocol"]["cycle"] == 1
    assert plan["protocol"]["prior"] == [] and plan["protocol"]["strikes"] == 0
    assert [item["name"] for item in plan["chain"]] == BUCKETS
    assert sorted(item["path"] for item in plan["reviewed_files"]) == [CHECKER, CAPTURE]
    assert plan["diff_inline"] is not None and "checker change" in plan["diff_inline"]
    assert (tmp_path / "plan" / "diff.patch").read_text(encoding="utf-8") == TWO_FILE_DIFF


NOTES_FILE = {"filename": "docs/notes.md", "additions": 1, "deletions": 0}
NOTES_SECTION = (
    "diff --git a/docs/notes.md b/docs/notes.md\n--- a/docs/notes.md\n+++ b/docs/notes.md\n@@ -1 +1 @@\n-a\n+b\n"
)


@pytest.mark.parametrize("decision", ["SHIP", "SHIP_WITH_FIXES", "DO_NOT_SHIP"])
def test_a_byte_identical_diff_carries_the_decision_without_a_reviewer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, decision: str
) -> None:
    diff = TWO_FILE_DIFF + NOTES_SECTION
    fake = _fake(
        _review(_record(diff, decision)),
        files=[*TWO_FILES, NOTES_FILE],
        diff=diff,
        base={"ref": "develop", "sha": "f" * 40},
    )
    _, values, plan = _plan(monkeypatch, tmp_path, fake)
    assert values["decision"] == "carry" and values["post"] == "true"
    assert plan["reviewed_head"] == OLD_HEAD and plan["chain"] == []
    assert plan["protocol"]["kind"] == "carry" and plan["protocol"]["previous"]["decision"] == decision
    assert not (tmp_path / "plan" / "diff.patch").exists()


@pytest.mark.parametrize(
    "change",
    ["relocated", "prose", "mode", "base-blob", "merge-base-missing"],
)
def test_any_difference_in_the_merge_base_diff_is_reviewed_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, change: str
) -> None:
    diff = TWO_FILE_DIFF + NOTES_SECTION
    record = _record(diff, "SHIP")
    current = {
        "relocated": diff.replace("@@ -1,1 +1,2 @@", "@@ -7,1 +7,2 @@"),
        "prose": diff.replace("+b\n", "+c\n"),
        "mode": diff.replace(f"--- a/{CAPTURE}", f"old mode 100644\nnew mode 100755\n--- a/{CAPTURE}"),
    }.get(change, diff)
    fake = _fake(_review(record), files=[*TWO_FILES, NOTES_FILE], diff=current)
    if change == "base-blob":
        fake.base_tag = "moved"
    if change == "merge-base-missing":
        fake.merge_base = None
    _, values, plan = _plan(monkeypatch, tmp_path, fake)
    assert values["decision"] == "review" and plan["protocol"]["kind"] == "follow-up"
    if change == "merge-base-missing":
        assert plan["protocol"]["patch_digest"] == f"unread-{HEAD}"


def test_a_file_without_an_api_blob_id_takes_its_head_blob_from_the_head_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _fake(_review(_record(TWO_FILE_DIFF, "SHIP")))
    fake._paginated = lambda path: (
        [fake.pull] if path.startswith(f"repos/{REPO}/pulls?") else [{**item, "sha": ""} for item in TWO_FILES]
    )
    _, values, _ = _plan(monkeypatch, tmp_path, fake)
    assert values["decision"] == "carry"


def test_a_missing_blob_id_for_one_file_carries_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    record = _record(TWO_FILE_DIFF, "SHIP")
    fake = _fake(_review(record))
    whole = fake._tree
    fake._tree = lambda repo, sha: {path: entry for path, entry in (whole(repo, sha) or {}).items() if path == CHECKER}
    _, values, plan = _plan(monkeypatch, tmp_path, fake)
    assert values["decision"] == "review"
    assert plan["protocol"]["patch_digest"] == f"unread-{HEAD}"


BINARY = "assets/blob.bin"
BASE_BLOB = "c" * 40
REVIEWED_BLOB = "1234567890" + "a" * 30
FORGED_BLOB = "1234567890" + "b" * 30


@pytest.mark.parametrize(("head_blob", "decision"), [(REVIEWED_BLOB, "carry"), (FORGED_BLOB, "review")])
def test_a_binary_file_with_the_same_abbreviated_id_but_a_different_full_id_does_not_carry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, head_blob: str, decision: str
) -> None:
    files = [{"filename": BINARY, "additions": 0, "deletions": 0, "status": "modified"}]
    record = _record(TWO_FILE_DIFF, "SHIP")
    record["patch_digest"] = protocol.patch_digest(
        protocol.fingerprints([protocol.file_state(BINARY, None, (MODE, BASE_BLOB), (MODE, REVIEWED_BLOB))])
    )
    fake = _fake(_review(record), files=files)
    fake._tree = lambda repo, sha: {BINARY: (MODE, head_blob if sha == HEAD else BASE_BLOB)}
    fake._paginated = lambda path: (
        [fake.pull] if path.startswith(f"repos/{REPO}/pulls?") else [{**files[0], "sha": head_blob}]
    )
    _, values, _ = _plan(monkeypatch, tmp_path, fake)
    assert values["decision"] == decision


def test_a_code_change_outside_soundness_paths_is_followed_up(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    helper = "benchbox/utils/row_compare.py"
    files = [*TWO_FILES, {"filename": helper, "additions": 3, "deletions": 1, "sha": "7" * 40}]
    section = f"diff --git a/{helper} b/{helper}\n--- a/{helper}\n+++ b/{helper}\n@@ -1,1 +1,2 @@\n a\n+helper\n"
    record = _record(TWO_FILE_DIFF + section, "SHIP")
    fake = _fake(_review(record), files=files, diff=TWO_FILE_DIFF + section.replace("+helper", "+helper changed"))
    _, values, plan = _plan(monkeypatch, tmp_path, fake)
    assert values["decision"] == "review" and plan["scope"] == "changed"
    assert plan["protocol"]["changed"] == [helper] and plan["evidence_files"] == [helper]
    assert plan["unchanged_files"] == [CHECKER, CAPTURE] and plan["untracked_files"] == []
    assert plan["scope_paths"] == [CHECKER, CAPTURE, helper]


def test_a_follow_up_changing_only_a_gate_template_reviews_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    template = "_project/scripts/oracle_reviewers/templates/gate.md"
    files = [*TWO_FILES, {"filename": template, "additions": 1, "deletions": 1, "sha": "7" * 40}]
    section = f"diff --git a/{template} b/{template}\n--- a/{template}\n+++ b/{template}\n@@ -1,1 +1,2 @@\n a\n+gate\n"
    record = _record(TWO_FILE_DIFF + section, "SHIP")
    fake = _fake(_review(record), files=files, diff=TWO_FILE_DIFF + section.replace("+gate", "+gate changed"))
    _, values, plan = _plan(monkeypatch, tmp_path, fake)
    assert values["decision"] == "review" and plan["scope"] == "changed"
    assert plan["protocol"]["changed"] == [template] and plan["evidence_files"] == [template]
    assert template in plan["scope_paths"] and template in plan["gate_paths"]


def test_a_file_leaving_the_pull_request_gets_a_full_follow_up_not_a_carry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    gone = "benchbox/core/equivalence/gone.py"
    section = f"diff --git a/{gone} b/{gone}\n--- a/{gone}\n+++ b/{gone}\n@@ -1 +1 @@\n-a\n+b\n"
    record = _record(TWO_FILE_DIFF + section, "SHIP_WITH_FIXES", open_defects=[DEFECT], next_id=2)
    _, values, plan = _plan(monkeypatch, tmp_path, _fake(_review(record)))
    assert values["decision"] == "review" and plan["scope"] == "full"
    assert plan["protocol"]["kind"] == "follow-up" and plan["protocol"]["changed"] == sorted([CHECKER, CAPTURE])


def test_two_unreadable_diffs_in_a_row_never_carry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    unread = _record("", "SHIP", paths=[CHECKER, CAPTURE])
    assert unread["patch_digest"] == f"unread-{OLD_HEAD}"
    assert set(unread["patch_map"].values()) == {"missing"}
    fake = _fake(_review(unread), changed_files=3001)
    fake.diff = None
    _, values, plan = _plan(monkeypatch, tmp_path, fake)
    assert values["decision"] == "review" and plan["protocol"]["kind"] == "follow-up"
    assert plan["protocol"]["patch_digest"] == f"unread-{HEAD}"
    assert plan["protocol"]["changed"] == sorted([CHECKER, CAPTURE])


def test_a_follow_up_after_ship_with_fixes_reviews_only_the_changed_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record = _record(TWO_FILE_DIFF, "SHIP_WITH_FIXES", open_defects=[DEFECT], next_id=2)
    _, values, plan = _plan(monkeypatch, tmp_path, _fake(_review(record), diff=CAPTURE_CHANGED))
    assert values["decision"] == "review" and plan["scope"] == "changed"
    rules = plan["protocol"]
    assert (rules["kind"], rules["cycle"], rules["round"], rules["next_id"]) == ("follow-up", 1, 2, 2)
    assert rules["prior"] == [DEFECT] and rules["changed"] == [CAPTURE]
    assert plan["evidence_files"] == [CAPTURE]
    assert [item["path"] for item in plan["reviewed_files"]] == [CAPTURE]
    assert plan["unchanged_files"] == [CHECKER] and plan["untracked_files"] == []
    assert plan["reviewed_head"] == OLD_HEAD and plan["restart_summary"] is None
    assert plan["follow_up_reviewer"] == "medium"
    assert plan["diff_inline"] is not None and "capture fixed" in plan["diff_inline"]
    assert "checker change" not in plan["diff_inline"]
    assert (tmp_path / "plan" / "diff.patch").read_text(encoding="utf-8") == CAPTURE_CHANGED


def test_a_stored_record_with_duplicate_defect_ids_still_plans_a_follow_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record = _record(TWO_FILE_DIFF, "SHIP_WITH_FIXES", open_defects=[DEFECT, dict(DEFECT)], next_id=2)
    _, values, plan = _plan(monkeypatch, tmp_path, _fake(_review(record), diff=CAPTURE_CHANGED))
    assert values["decision"] == "review" and plan["protocol"]["kind"] == "follow-up"
    assert plan["protocol"]["prior"] == [DEFECT]
    assert render.response_template([item["id"] for item in plan["protocol"]["prior"]])


def test_a_follow_up_after_ship_scopes_to_the_changed_file_without_tiers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record = _record(TWO_FILE_DIFF, "SHIP", reviewer="large", tier="large")
    _, values, plan = _plan(monkeypatch, tmp_path, _fake(_review(record), diff=CAPTURE_CHANGED))
    assert values["decision"] == "review" and plan["scope"] == "changed"
    assert plan["protocol"]["kind"] == "follow-up" and plan["protocol"]["prior"] == []
    assert plan["follow_up_reviewer"] == "large"
    assert "tier" not in plan


def test_a_follow_up_tries_the_previous_reviewer_first(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    record = _record(TWO_FILE_DIFF, "SHIP_WITH_FIXES", open_defects=[DEFECT], next_id=2, reviewer="large")
    _, _, plan = _plan(monkeypatch, tmp_path, _fake(_review(record), diff=CAPTURE_CHANGED))
    assert plan["follow_up_reviewer"] == "large"
    (tmp_path / "x").mkdir()
    _, _, first = _plan(monkeypatch, tmp_path / "x", _fake(diff=CAPTURE_CHANGED))
    assert [item["name"] for item in first["chain"]] == BUCKETS and first["follow_up_reviewer"] is None


def test_a_changed_patch_after_do_not_ship_restarts_with_the_previous_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record = _record(TWO_FILE_DIFF, "DO_NOT_SHIP", summary="The comparator ignores NULL ordering.")
    _, values, plan = _plan(monkeypatch, tmp_path, _fake(_review(record), diff=CAPTURE_CHANGED))
    assert values["decision"] == "review" and plan["scope"] == "full"
    assert (plan["protocol"]["kind"], plan["protocol"]["cycle"], plan["protocol"]["strikes"]) == ("first", 2, 1)
    assert plan["restart_summary"] == "The comparator ignores NULL ordering."
    assert sorted(item["path"] for item in plan["reviewed_files"]) == [CHECKER, CAPTURE]
    patch = (tmp_path / "plan" / "diff.patch").read_text(encoding="utf-8")
    assert "checker change" in patch and "capture fixed" in patch


def test_a_retarget_restarts_and_keeps_strikes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    struck = _record(TWO_FILE_DIFF.replace("a\n+", "z\n+"), "DO_NOT_SHIP", head_sha="e" * 40)
    latest = _record(TWO_FILE_DIFF, "SHIP", base_ref="release")
    fake = _fake(_review(struck, id=1), _review(latest, id=2, submitted_at="2026-10-08T11:00:00Z"))
    _, values, plan = _plan(monkeypatch, tmp_path, fake)
    assert values["decision"] == "review" and plan["scope"] == "full"
    assert plan["protocol"]["kind"] == "first" and plan["protocol"]["cycle"] == 2
    assert plan["protocol"]["reason"] == "the base changed from release to develop"
    assert plan["protocol"]["strikes"] == 1
    assert plan["protocol"]["struck_digests"] == [struck["patch_digest"]]


def test_a_head_that_already_has_a_decision_runs_no_reviewer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    record = _record(TWO_FILE_DIFF, "SHIP_WITH_FIXES", head_sha=HEAD)
    _, values, plan = _plan(monkeypatch, tmp_path, _fake(_review(record)))
    assert values["decision"] == "skip" and values["post"] == "false"
    assert "already has a SHIP WITH FIXES decision" in plan["decision_reason"]


def _strike(n: int, head: str = "e" * 40) -> dict[str, Any]:
    diff = TWO_FILE_DIFF.replace("checker change", f"checker change {n}")
    return _review(_record(diff, "DO_NOT_SHIP", head_sha=head, cycle=n), id=n, submitted_at=f"2026-10-08T0{n}:00:00Z")


def test_the_third_do_not_ship_refuses_further_review(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _, values, plan = _plan(monkeypatch, tmp_path, _fake(_strike(1), _strike(2), _strike(3)))
    assert values["decision"] == "refused" and values["post"] == "true"
    assert plan["protocol"]["kind"] == "refused"
    assert plan["protocol"]["strikes"] == 3 and plan["chain"] == []
    (tmp_path / "two").mkdir()
    _, values, _ = _plan(monkeypatch, tmp_path / "two", _fake(_strike(1), _strike(2)))
    assert values["decision"] == "review"


def test_a_refusal_posts_once_per_head(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    refusal = _review(
        _record(TWO_FILE_DIFF, "REFUSED", kind="refused", head_sha=HEAD, cycle=3, round=0, strikes_after=3),
        id=9,
        submitted_at="2026-10-08T09:00:00Z",
    )
    _, values, plan = _plan(monkeypatch, tmp_path, _fake(_strike(1), _strike(2), _strike(3), refusal))
    assert values["decision"] == "skip" and "already has the refusal" in plan["decision_reason"]


def test_strikes_count_distinct_decisive_patches_and_never_carried_ones(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    moved = TWO_FILE_DIFF.replace("capture change", "capture moved")
    carried = _review(_record(moved, "DO_NOT_SHIP", kind="carry", carried=True, cycle=2), id=7)
    _, values, plan = _plan(monkeypatch, tmp_path, _fake(_strike(1), _strike(1, "f" * 40), carried))
    assert values["decision"] == "review" and plan["protocol"]["strikes"] == 1
    assert plan["protocol"]["kind"] == "first" and plan["protocol"]["cycle"] == 3


def test_the_state_artifact_can_raise_the_strike_count(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _fake(_strike(1))
    fake.state = State(7, "e" * 40, "failure", datetime.now(UTC) - timedelta(hours=1), strikes=3)
    _, values, plan = _plan(monkeypatch, tmp_path, fake)
    assert values["decision"] == "refused"
    assert plan["protocol"]["kind"] == "refused"


def test_a_reopen_does_not_reset_the_history(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _fake(_strike(1), _strike(2), _strike(3))
    _, values, _ = _plan(monkeypatch, tmp_path, fake, event={"action": "reopened"})
    assert values["decision"] == "refused"


@pytest.mark.parametrize(
    "review",
    [
        _review(_record(TWO_FILE_DIFF, "DO_NOT_SHIP"), state="DISMISSED"),
        _review(_record(TWO_FILE_DIFF, "DO_NOT_SHIP"), login="someone", user_type="User"),
        _review(_record(TWO_FILE_DIFF, "DO_NOT_SHIP"), login="benchbox-oracle", user_type="User"),
        _review(_record(TWO_FILE_DIFF, "DO_NOT_SHIP"), user_type="User"),
        _review(None),
    ],
    ids=["dismissed", "other-author", "not-a-bot", "bot-login-user-type", "legacy-without-marker"],
)
def test_reviews_that_are_not_live_oracle_markers_are_ignored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, review: dict[str, Any]
) -> None:
    _, values, plan = _plan(monkeypatch, tmp_path, _fake(review))
    assert values["decision"] == "review" and plan["protocol"]["kind"] == "first"
    assert plan["protocol"]["strikes"] == 0


FORGED = _review(_record(TWO_FILE_DIFF, "SHIP"))


@pytest.mark.parametrize(
    "body",
    [
        FORGED["body"] + "\n" + FORGED["body"].rsplit("\n", 1)[-1],
        FORGED["body"].replace("-->", "--> trailing text"),
        FORGED["body"][:-20] + " -->",
    ],
    ids=["two-markers", "not-last", "corrupt"],
)
def test_a_malformed_marker_holds_the_result_pending(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: str
) -> None:
    _, values, plan = _plan(monkeypatch, tmp_path, _fake(_review(None, body=body)))
    assert values["decision"] == "hold" and values["post"] == "true"
    assert "protocol marker" in plan["decision_reason"]


def test_an_unreadable_review_list_holds_the_result_pending(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _fake()
    fake.reviews = None
    _, values, plan = _plan(monkeypatch, tmp_path, fake)
    assert values["decision"] == "hold" and "could not be read" in plan["decision_reason"]


def test_more_than_a_hundred_reviews_are_all_read(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    noise = [_review(None, id=index, submitted_at=f"2026-10-0{1 + index // 100}T00:00:00Z") for index in range(150)]
    latest = _review(_record(TWO_FILE_DIFF, "SHIP", head_sha=HEAD), id=500, submitted_at="2026-10-08T12:00:00Z")
    _, values, _ = _plan(monkeypatch, tmp_path, _fake(*noise, latest))
    assert values["decision"] == "skip"


def test_an_unreadable_diff_never_carries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    record = _record(TWO_FILE_DIFF, "SHIP_WITH_FIXES", open_defects=[DEFECT], next_id=2)
    fake = _fake(_review(record), changed_files=3001)
    _, values, plan = _plan(monkeypatch, tmp_path, fake)
    assert values["decision"] == "review" and plan["protocol"]["kind"] == "follow-up"
    assert plan["protocol"]["patch_digest"] == f"unread-{HEAD}"


def test_a_mode_only_change_is_a_changed_patch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    moded = TWO_FILE_DIFF.replace(f"--- a/{CAPTURE}", f"old mode 100644\nnew mode 100755\n--- a/{CAPTURE}")
    record = _record(TWO_FILE_DIFF, "SHIP")
    _, values, plan = _plan(monkeypatch, tmp_path, _fake(_review(record), diff=moded))
    assert values["decision"] == "review" and plan["protocol"]["changed"] == [CAPTURE]


def test_prose_is_untracked_apart_from_a_scoped_follow_up(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    notes = {"filename": "docs/notes.md", "additions": 1, "deletions": 0, "sha": "9" * 40}
    record = _record(TWO_FILE_DIFF, "SHIP")
    _, values, plan = _plan(
        monkeypatch, tmp_path, _fake(_review(record), files=[*TWO_FILES, notes], diff=CAPTURE_CHANGED)
    )
    assert values["decision"] == "review" and plan["scope"] == "changed"
    assert [item["path"] for item in plan["reviewed_files"]] == [CAPTURE]
    assert plan["unchanged_files"] == [CHECKER] and plan["untracked_files"] == ["docs/notes.md"]
    assert plan["evidence_files"] == [CAPTURE]


def test_a_text_data_file_is_tracked_like_code(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    data = "_sources/tpc-ds/tools/column_list.txt"
    files = [*TWO_FILES, {"filename": data, "additions": 1, "deletions": 0, "sha": "5" * 40}]
    section = f"diff --git a/{data} b/{data}\n--- a/{data}\n+++ b/{data}\n@@ -1 +1 @@\n-a\n+b\n"
    record = _record(TWO_FILE_DIFF + section, "SHIP")
    changed = TWO_FILE_DIFF + section.replace("+b", "+c")
    _, values, plan = _plan(monkeypatch, tmp_path, _fake(_review(record), files=files, diff=changed))
    assert values["decision"] == "review" and plan["protocol"]["changed"] == [data]


def test_open_threads_are_recorded_as_open_findings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    threads = [
        {
            "author": "benchbox-oracle[bot]",
            "author_type": "Bot",
            "resolved": False,
            "path": CAPTURE,
            "body": "**High**: Drops a row",
        },
        {"author": "someone", "author_type": "User", "resolved": False, "path": CAPTURE, "body": "**High**: Noise"},
    ]
    _, values, plan = _plan(monkeypatch, tmp_path, FakeGitHub(_pull(), SOUNDNESS, threads=threads))
    assert values["decision"] == "review"
    assert plan["open_findings"] == {fingerprint(CAPTURE, "Drops a row"): "High"}


def test_unreadable_threads_are_noted_not_fatal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, values, plan = _plan(monkeypatch, tmp_path, FakeGitHub(_pull(), SOUNDNESS, threads=None))
    assert values["decision"] == "review"
    assert plan["open_findings"] == {}
    assert "none are suppressed" in capsys.readouterr().out


def test_an_oversize_head_is_not_retried_until_a_new_push(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    oversize = State(7, HEAD, "pending", datetime.now(UTC) - timedelta(hours=3), pending_cause="oversize")
    comment = {"action": "created", "issue": {"number": 7, "pull_request": {}}, "comment": {"body": "/oracle-review"}}
    _, values, plan = _plan(
        monkeypatch, tmp_path, FakeGitHub(_pull(), SOUNDNESS, oversize), event_name="issue_comment", event=comment
    )
    assert values["decision"] == "skip" and "pending for oversize" in plan["decision_reason"]
    fake = FakeGitHub(_pull(), SOUNDNESS, oversize)
    fake.install(monkeypatch)
    _env(monkeypatch, tmp_path, "schedule", {}, "refs/heads/develop")
    assert cli.main(["sweep", "--policy", str(POLICY_PATH)]) == 0
    assert fake.dispatched == []
    pushed = tmp_path / "pushed"
    pushed.mkdir()
    _, values, plan = _plan(monkeypatch, pushed, FakeGitHub(_pull(), SOUNDNESS, oversize))
    assert values["decision"] == "review" and plan["brief_mode"] == "inline"


def _decided_head(open_defects: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return _record(TWO_FILE_DIFF, "SHIP_WITH_FIXES", head_sha=HEAD, open_defects=open_defects or [DEFECT], next_id=2)


def _disputed_thread(association: str | None = "OWNER") -> dict[str, Any]:
    return {
        "author": "benchbox-oracle[bot]",
        "author_type": "Bot",
        "resolved": False,
        "path": CAPTURE,
        "body": "**High**: Drops a row",
        "replies": [
            {
                "author": "joe",
                "author_type": "User",
                "association": association,
                "body": "Dispute: pkg/kept.py:12 keeps the row, so the defect is wrong.",
            }
        ],
    }


def _acked_thread() -> dict[str, Any]:
    return {
        "author": "benchbox-oracle[bot]",
        "author_type": "Bot",
        "resolved": False,
        "path": CAPTURE,
        "body": "**High**: Drops a row",
        "replies": [{"author": "joe", "author_type": "User", "association": "OWNER", "body": "done"}],
    }


def _thread_fake(record: dict[str, Any], threads: list[dict[str, Any]]) -> FakeGitHub:
    fake = FakeGitHub(_pull(), TWO_FILES, threads=threads, diff=TWO_FILE_DIFF)
    fake.reviews = [_review(record)]
    return fake


def test_comment_on_a_decided_head_forces_a_same_head_rereview(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    event = {"action": "created", "issue": {"number": 7, "pull_request": {}}, "comment": {"body": "/oracle-review"}}
    _, values, plan = _plan(
        monkeypatch,
        tmp_path,
        _thread_fake(_decided_head(), [_disputed_thread()]),
        event_name="issue_comment",
        event=event,
    )
    assert values["decision"] == "review" and values["post"] == "true"
    assert plan["manual"] is True
    assert plan["protocol"]["kind"] == "follow-up" and plan["protocol"]["forced"] is True


def test_comment_without_a_dispute_leaves_the_decided_head_in_place(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    event = {"action": "created", "issue": {"number": 7, "pull_request": {}}, "comment": {"body": "/oracle-review"}}
    _, values, plan = _plan(
        monkeypatch, tmp_path, _thread_fake(_decided_head(), [_acked_thread()]), event_name="issue_comment", event=event
    )
    assert values["decision"] == "skip" and values["post"] == "false"
    assert plan["manual"] is True


def test_push_with_a_disputed_thread_upgrades_carry_to_a_forced_rereview(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record = _record(TWO_FILE_DIFF, "SHIP_WITH_FIXES", open_defects=[DEFECT], next_id=2)
    _, values, plan = _plan(monkeypatch, tmp_path, _thread_fake(record, [_disputed_thread()]))
    assert values["decision"] == "review" and values["post"] == "true"
    assert plan["manual"] is False
    assert plan["protocol"]["kind"] == "follow-up" and plan["protocol"]["forced"] is True
    assert len(plan["disputes"]) == 1


def test_push_with_only_an_acknowledgement_still_carries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    record = _record(TWO_FILE_DIFF, "SHIP_WITH_FIXES", open_defects=[DEFECT], next_id=2)
    _, values, plan = _plan(monkeypatch, tmp_path, _thread_fake(record, [_acked_thread()]))
    assert values["decision"] == "carry" and values["post"] == "true"
    assert plan["manual"] is False
    assert plan["protocol"]["kind"] == "carry" and plan["protocol"]["forced"] is False
    assert plan.get("disputes", []) == []


@pytest.mark.parametrize("association", ["NONE", "CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", None])
def test_a_dispute_from_outside_the_repository_is_ignored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, association: str | None
) -> None:
    record = _record(TWO_FILE_DIFF, "SHIP_WITH_FIXES", open_defects=[DEFECT], next_id=2)
    _, values, plan = _plan(monkeypatch, tmp_path, _thread_fake(record, [_disputed_thread(association)]))
    assert values["decision"] == "carry"
    assert plan["protocol"]["forced"] is False
    assert plan.get("disputes", []) == []


def test_a_changed_patch_with_a_dispute_takes_the_ordinary_follow_up_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record = _record(TWO_FILE_DIFF, "SHIP_WITH_FIXES", open_defects=[DEFECT], next_id=2)
    fake = FakeGitHub(_pull(), TWO_FILES, threads=[_disputed_thread()], diff=CAPTURE_CHANGED)
    fake.reviews = [_review(record)]
    _, values, plan = _plan(monkeypatch, tmp_path, fake)
    assert values["decision"] == "review" and values["post"] == "true"
    assert plan["protocol"]["kind"] == "follow-up" and plan["protocol"]["forced"] is False


@pytest.mark.parametrize(
    "comment",
    [
        {"body": "Not a defect.", "user": {"login": "joe", "type": "User"}, "in_reply_to_id": 11},
        {"body": "Not a defect.", "user": {"login": "joe", "type": "User"}},
        {"body": "Noted.", "user": {"login": "benchbox-oracle[bot]", "type": "Bot"}, "in_reply_to_id": 11},
    ],
    ids=["thread-reply", "top-level", "bot-reply"],
)
def test_review_comment_events_never_plan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, comment: dict[str, Any]
) -> None:
    _, values, plan = _plan(
        monkeypatch,
        tmp_path,
        _thread_fake(_decided_head(), [_disputed_thread()]),
        event_name="pull_request_review_comment",
        event={"action": "created", "comment": comment},
    )
    assert values["decision"] == "skip" and values["post"] == "false"
    assert plan["manual"] is False


def test_guard_posts_a_forced_follow_up_once_and_skips_the_duplicate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    event = {"action": "created", "issue": {"number": 7, "pull_request": {}}, "comment": {"body": "/oracle-review"}}
    fake = _thread_fake(_decided_head(), [_disputed_thread()])
    _, values, plan = _plan(monkeypatch, tmp_path, fake, event_name="issue_comment", event=event)
    assert values["decision"] == "review" and plan["protocol"]["forced"] is True
    plan_path = tmp_path / "plan" / "plan.json"
    output = tmp_path / "guard.out"
    output.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    fake.install(monkeypatch)
    assert cli.main(["guard", "--plan", str(plan_path)]) == 0
    assert dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())["skip"] == "false"
    posted = {**_decided_head(), "round": 2, "kind": "follow-up", "forced": True}
    fake.reviews = [_review(_decided_head()), _review(posted, id=2, submitted_at="2026-10-08T11:00:00Z")]
    assert cli.main(["guard", "--plan", str(plan_path)]) == 0
    assert dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())["skip"] == "true"


def _unreadable_base_change(monkeypatch: pytest.MonkeyPatch) -> None:
    def unreadable(repo: str, pr: int) -> None:
        raise github.GitHubError("timeline unavailable")

    monkeypatch.setattr(github, "base_changed_at", unreadable)


def test_guard_lets_a_carry_publish_and_skips_once_the_carry_is_posted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _unreadable_base_change(monkeypatch)
    fake = _fake(_review(_decided_head()))
    _, values, plan = _plan(monkeypatch, tmp_path, fake, event={"action": "edited", "changes": {"base": {}}})
    assert values["decision"] == "carry" and plan["protocol"]["kind"] == "carry"
    plan_path = tmp_path / "plan" / "plan.json"
    output = tmp_path / "guard.out"
    output.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    fake.install(monkeypatch)
    assert cli.main(["guard", "--plan", str(plan_path)]) == 0
    assert dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())["skip"] == "false"
    carried = {**_decided_head(), "carried": True, "kind": "carry"}
    fake.reviews = [_review(_decided_head()), _review(carried, id=2, submitted_at="2026-10-08T11:00:00Z")]
    assert cli.main(["guard", "--plan", str(plan_path)]) == 0
    assert dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())["skip"] == "true"


def test_guard_still_sees_the_carry_after_an_earlier_review_is_dismissed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _unreadable_base_change(monkeypatch)
    older = {**_decided_head(), "round": 1}
    fake = _fake(_review(older), _review(_decided_head(), id=2, submitted_at="2026-10-08T10:30:00Z"))
    _, values, plan = _plan(monkeypatch, tmp_path, fake, event={"action": "edited", "changes": {"base": {}}})
    assert values["decision"] == "carry"
    plan_path = tmp_path / "plan" / "plan.json"
    output = tmp_path / "guard.out"
    output.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    carried = {**_decided_head(), "carried": True, "kind": "carry"}
    fake.reviews = [
        _review(older, state="DISMISSED"),
        _review(_decided_head(), id=2, submitted_at="2026-10-08T10:30:00Z"),
        _review(carried, id=3, submitted_at="2026-10-08T11:00:00Z"),
    ]
    fake.install(monkeypatch)
    assert cli.main(["guard", "--plan", str(plan_path)]) == 0
    assert dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())["skip"] == "true"


def test_unreadable_base_change_history_reviews_a_decided_head_whose_patch_changed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _unreadable_base_change(monkeypatch)
    fake = _fake(_review(_decided_head()), diff=CAPTURE_CHANGED)
    _, values, plan = _plan(monkeypatch, tmp_path, fake, event={"action": "edited", "changes": {"base": {}}})
    assert values["decision"] == "review"
    assert plan["protocol"]["kind"] == "follow-up"


def test_unreadable_base_change_history_carries_a_decided_head_whose_patch_is_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _unreadable_base_change(monkeypatch)
    fake = _fake(_review(_decided_head()))
    _, values, _ = _plan(monkeypatch, tmp_path, fake, event={"action": "edited", "changes": {"base": {}}})
    assert values["decision"] == "carry"


def _round(records: list[dict[str, Any]], tag: str = "", **kwargs: Any) -> protocol.Round:
    prints = protocol.fingerprints(_states(TWO_FILE_DIFF, tag))
    return protocol.plan_round(
        protocol.History(records),
        head_sha=HEAD,
        base_ref="develop",
        digest=protocol.patch_digest(prints),
        patches=protocol.patch_map(prints, sorted(prints)),
        paths=sorted(prints),
        strike_count=protocol.strikes(records),
        max_do_not_ship=3,
        **kwargs,
    )


def test_a_byte_identical_rebase_keeps_the_strike_count_and_the_forced_round_allowance() -> None:
    struck = _record(TWO_FILE_DIFF, "DO_NOT_SHIP")
    carried = _round([struck])
    assert carried.kind == protocol.CARRY
    records = [struck, _record(TWO_FILE_DIFF, "DO_NOT_SHIP", kind="carry", carried=True, head_sha=HEAD)]
    assert protocol.strikes(records) == 1

    forced = {"kind": "follow-up", "forced": True, "round": 2}
    spent = [_record(TWO_FILE_DIFF, "SHIP"), *(_record(TWO_FILE_DIFF, "SHIP", **forced) for _ in range(2))]
    refused = _round(spent, force=True)
    assert refused.kind == protocol.SKIP and "already had 2 forced re-reviews" in refused.reason


def test_a_rebase_onto_changed_files_is_a_new_digest_with_its_own_strike_and_forced_allowance() -> None:
    first = _record(TWO_FILE_DIFF, "DO_NOT_SHIP")
    rebased = _record(TWO_FILE_DIFF, "DO_NOT_SHIP", tag="moved", head_sha=HEAD)
    assert first["patch_digest"] != rebased["patch_digest"]
    assert _round([first], "moved").kind == protocol.FIRST
    assert protocol.strikes([first, rebased]) == 2

    forced = {"kind": "follow-up", "forced": True, "round": 2}
    spent = [_record(TWO_FILE_DIFF, "SHIP"), *(_record(TWO_FILE_DIFF, "SHIP", **forced) for _ in range(2))]
    fresh = _round(spent, "moved", force=True)
    assert fresh.kind == protocol.FOLLOW_UP and fresh.forced is False
    reviewed = [*spent, _record(TWO_FILE_DIFF, "SHIP", tag="moved")]
    allowed = _round(reviewed, "moved", force=True)
    assert allowed.kind == protocol.FOLLOW_UP and allowed.forced is True
