from __future__ import annotations

import json
import stat
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from _project.scripts.oracle_reviewers import cli, github, protocol
from _project.scripts.oracle_reviewers.dedup import fingerprint
from _project.scripts.oracle_reviewers.retry import State

from .conftest import POLICY_PATH

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO = "BenchBox-dev/BenchBox"
HEAD = "a" * 40
BASE = "b" * 40
DIFF = "diff --git a/x b/x\n"
MERGE_BASE = "e" * 40
MODE = "100644"


def _pull(**over: Any) -> dict[str, Any]:
    pull = {
        "number": 7,
        "state": "open",
        "draft": False,
        "labels": [{"name": "author-family:codex"}],
        "base": {"ref": "develop", "sha": BASE},
        "head": {"sha": HEAD, "repo": {"full_name": REPO}},
    }
    pull.update(over)
    return pull


class FakeGitHub:
    def __init__(
        self,
        pull: dict[str, Any],
        files: list[dict[str, Any]],
        state: State | None = None,
        threads: Sequence[dict[str, Any]] | None = (),
        diff: str = DIFF,
    ) -> None:
        self.pull = pull
        self.files = files
        self.state = state
        self.threads = None if threads is None else list(threads)
        self.diff = diff
        self.diff_reads = 0
        self.merge_base: str | None = MERGE_BASE
        self.modes: dict[str, str] = {}
        self.dispatched: list[int] = []
        self.reviews: list[dict[str, Any]] | None = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(github, "get_json", self._json)
        monkeypatch.setattr(github, "get_paginated", self._paginated)
        monkeypatch.setattr(github, "get_diff", self._diff)
        monkeypatch.setattr(github, "review_threads", self._threads)
        monkeypatch.setattr(github, "latest_state", lambda repo, pr: self.state)
        monkeypatch.setattr(github, "dispatch", lambda repo, workflow, pr: self.dispatched.append(pr))
        monkeypatch.setattr(github, "oracle_reviews", self._reviews)

    def _reviews(self, repo: str, pr: int) -> list[dict[str, Any]]:
        if self.reviews is None:
            raise github.GitHubError("reviews unavailable")
        return self.reviews

    def _json(self, path: str) -> dict[str, Any]:
        if "/compare/" in path:
            return {"merge_base_commit": {"sha": self.merge_base}}
        if "/git/trees/" in path:
            tree = [{"path": item["filename"], "mode": self.modes.get(item["filename"], MODE)} for item in self.files]
            return {"truncated": False, "tree": tree}
        return self.pull

    def _threads(self, repo: str, pr: int) -> list[dict[str, Any]]:
        if self.threads is None:
            raise github.GitHubError("threads unavailable")
        return self.threads

    def _diff(self, repo: str, pr: int) -> str:
        self.diff_reads += 1
        return self.diff

    def _paginated(self, path: str) -> list[dict[str, Any]]:
        return [self.pull] if path.startswith(f"repos/{REPO}/pulls?") else self.files


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


def test_soundness_pr_plans_a_review_with_a_private_brief(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    code, values, plan = _plan(monkeypatch, tmp_path, FakeGitHub(_pull(), SOUNDNESS))
    assert code == 0
    assert values["decision"] == "review" and values["post"] == "true" and values["head_sha"] == HEAD
    assert plan["tier"] == "medium-high"
    assert plan["excluded_families"] == ["codex"]
    assert {item["name"] for item in plan["chain"]} == {
        "codex-deep",
        "claude-deep",
        "gemini-deep",
        "muse-deep",
        "codex-routine",
    }
    assert plan["brief_mode"] == "inline"
    brief = tmp_path / "plan" / "brief.md"
    if sys.platform != "win32":
        assert stat.S_IMODE(brief.stat().st_mode) == 0o600
    assert HEAD in brief.read_text(encoding="utf-8")
    assert plan["max_defects"] == 10
    assert plan["evidence_files"] == ["benchbox/core/equivalence/checker.py"]
    assert "blocking" not in plan


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


def test_non_soundness_pr_posts_success_without_reviewers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    files = [{"filename": "README.md", "additions": 1, "deletions": 0}]
    _, values, plan = _plan(monkeypatch, tmp_path, FakeGitHub(_pull(), files))
    assert values["decision"] == "success" and values["post"] == "true"
    assert plan["chain"] == []


def test_fork_pr_never_reaches_a_reviewer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pull = _pull(head={"sha": HEAD, "repo": {"full_name": "someone/BenchBox"}})
    _, values, plan = _plan(monkeypatch, tmp_path, FakeGitHub(pull, SOUNDNESS))
    assert values["decision"] == "fork" and values["post"] == "true"
    assert plan["decision_reason"] == "fork: owner review"
    assert plan["chain"] == []
    assert not (tmp_path / "plan" / "brief.md").exists()


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
    _, values, _ = _plan(monkeypatch, tmp_path, FakeGitHub(_pull(), SOUNDNESS), event=event)
    assert values["decision"] == "skip"


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


def test_comment_rerun_after_a_decisive_result_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    state = State(7, HEAD, "success", datetime.now(UTC) - timedelta(hours=3))
    _, values, plan = _plan(
        monkeypatch, tmp_path, FakeGitHub(_pull(), SOUNDNESS, state), event_name="issue_comment", event={}
    )
    assert values["decision"] == "skip" and values["post"] == "false"
    assert "already has a success result" in plan["decision_reason"]


def test_pending_pool_reset_is_carried_into_the_plan(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    later = datetime.now(UTC) + timedelta(hours=2)
    earlier = datetime.now(UTC) - timedelta(hours=1)
    state = State(7, "c" * 40, "pending", earlier, (), {"claude": later, "codex": earlier})
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


CHECKER = "benchbox/core/equivalence/checker.py"
CAPTURE = "benchbox/core/expected_results/registry.py"
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
    patches = protocol.file_patches(diff)
    chosen = paths if paths is not None else sorted(patches)
    current = protocol.patch_map(patches, chosen)
    record = {
        "v": 1,
        "cycle": 1,
        "round": 1,
        "kind": "first",
        "decision": decision,
        "head_sha": OLD_HEAD,
        "base_ref": "develop",
        "reviewer": "sol",
        "tier": "medium-high",
        "strikes_after": 1 if decision == "DO_NOT_SHIP" else 0,
        "open_defects": [],
        "next_id": 1,
        "patch_digest": protocol.patch_digest(patches, chosen) or f"unread-{OLD_HEAD}",
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


def _fake(*reviews: dict[str, Any], files: list[dict[str, Any]] | None = None, diff: str = TWO_FILE_DIFF, **pull: Any):
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
    assert "Leave prior_defects empty." in (tmp_path / "plan" / "brief.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("diff", [TWO_FILE_DIFF, REBASED], ids=["identical", "rebased"])
@pytest.mark.parametrize("decision", ["SHIP", "SHIP_WITH_FIXES", "DO_NOT_SHIP"])
def test_an_unchanged_patch_carries_the_decision_without_a_reviewer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, diff: str, decision: str
) -> None:
    files = [*TWO_FILES, {"filename": "README.md", "additions": 1, "deletions": 0, "sha": "9" * 40}]
    readme = diff + "diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-a\n+b\n"
    fake = _fake(
        _review(_record(TWO_FILE_DIFF, decision)), files=files, diff=readme, base={"ref": "develop", "sha": "f" * 40}
    )
    _, values, plan = _plan(monkeypatch, tmp_path, fake)
    assert values["decision"] == "carry" and values["post"] == "true"
    assert plan["reviewed_head"] == OLD_HEAD and plan["chain"] == []
    assert plan["protocol"]["previous"]["decision"] == decision
    assert not (tmp_path / "plan" / "brief.md").exists()


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
    brief = (tmp_path / "plan" / "brief.md").read_text(encoding="utf-8")
    assert "capture fixed" in brief and "checker change" not in brief
    assert f"- D1 (High) {CAPTURE}:2: Drops a row" in brief
    assert "Give each of them a status in prior_defects" in brief


def test_a_follow_up_after_ship_is_scoped_even_at_the_very_high_tier(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record = _record(TWO_FILE_DIFF, "SHIP", tier="very-high", reviewer="opus")
    fake = _fake(_review(record), diff=CAPTURE_CHANGED, labels=[{"name": "oracle-tier:very-high"}])
    _, values, plan = _plan(monkeypatch, tmp_path, fake)
    assert values["decision"] == "review" and plan["scope"] == "changed" and plan["tier"] == "very-high"
    assert plan["protocol"]["kind"] == "follow-up" and plan["protocol"]["prior"] == []
    assert "Leave prior_defects empty. An earlier review decided SHIP" in (tmp_path / "plan" / "brief.md").read_text(
        encoding="utf-8"
    )


def test_a_follow_up_tries_the_previous_reviewer_first(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    record = _record(TWO_FILE_DIFF, "SHIP_WITH_FIXES", open_defects=[DEFECT], next_id=2, reviewer="muse-deep")
    _, _, plan = _plan(monkeypatch, tmp_path, _fake(_review(record), diff=CAPTURE_CHANGED))
    names = [item["name"] for item in plan["chain"]]
    assert names[0] == "muse-deep"
    assert set(names) == {"codex-deep", "claude-deep", "gemini-deep", "muse-deep", "codex-routine"}
    (tmp_path / "x").mkdir()
    _, _, first = _plan(monkeypatch, tmp_path / "x", _fake(diff=CAPTURE_CHANGED))
    assert {item["name"] for item in first["chain"]} == set(names)


def test_a_changed_patch_after_do_not_ship_restarts_with_the_previous_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record = _record(TWO_FILE_DIFF, "DO_NOT_SHIP", summary="The comparator ignores NULL ordering.")
    _, values, plan = _plan(monkeypatch, tmp_path, _fake(_review(record), diff=CAPTURE_CHANGED))
    assert values["decision"] == "review" and plan["scope"] == "full"
    assert (plan["protocol"]["kind"], plan["protocol"]["cycle"], plan["protocol"]["strikes"]) == ("first", 2, 1)
    brief = (tmp_path / "plan" / "brief.md").read_text(encoding="utf-8")
    assert "decided DO NOT SHIP" in brief and "The comparator ignores NULL ordering." in brief
    assert "checker change" in brief and "capture fixed" in brief


@pytest.mark.parametrize(
    ("over", "pull", "reason"),
    [
        ({"base_ref": "release"}, {}, "the base changed from release to develop"),
        ({"tier": "low-medium"}, {}, "the tier changed from low-medium to medium-high"),
    ],
)
def test_a_retarget_or_tier_change_restarts_and_keeps_strikes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, over: dict, pull: dict, reason: str
) -> None:
    struck = _record(TWO_FILE_DIFF.replace("a\n+", "z\n+"), "DO_NOT_SHIP", head_sha="e" * 40)
    latest = _record(TWO_FILE_DIFF, "SHIP", **over)
    fake = _fake(_review(struck, id=1), _review(latest, id=2, submitted_at="2026-10-08T11:00:00Z"), **pull)
    _, values, plan = _plan(monkeypatch, tmp_path, fake)
    assert values["decision"] == "review" and plan["scope"] == "full"
    assert plan["protocol"]["kind"] == "first" and plan["protocol"]["cycle"] == 2
    assert plan["protocol"]["reason"] == reason and plan["protocol"]["strikes"] == 1


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
    _, values, _ = _plan(monkeypatch, tmp_path, fake)
    assert values["decision"] == "refused"


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


def test_a_binary_change_is_never_mistaken_for_an_unchanged_patch() -> None:
    def binary(blob: str) -> str:
        return f"diff --git a/a.bin b/a.bin\nindex 111..{blob} 100644\nBinary files a/a.bin and b/a.bin differ\n"

    assert protocol.file_patches(binary("222")) != protocol.file_patches(binary("333"))
    text = "diff --git a/a.py b/a.py\nindex 111..{0} 100644\n--- a/a.py\n+++ b/a.py\n@@ -{1},1 +{1},2 @@\n a\n+b\n"
    assert protocol.file_patches(text.format("222", 1)) == protocol.file_patches(text.format("333", 9))


HELPER = "benchbox/utils/row_compare.py"


def test_a_code_change_outside_soundness_paths_is_followed_up(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    files = [*TWO_FILES, {"filename": HELPER, "additions": 3, "deletions": 1, "sha": "7" * 40}]
    helper = f"diff --git a/{HELPER} b/{HELPER}\n--- a/{HELPER}\n+++ b/{HELPER}\n@@ -1,1 +1,2 @@\n a\n+helper\n"
    record = _record(TWO_FILE_DIFF + helper, "SHIP")
    fake = _fake(_review(record), files=files, diff=TWO_FILE_DIFF + helper.replace("+helper", "+helper changed"))
    _, values, plan = _plan(monkeypatch, tmp_path, fake)
    assert values["decision"] == "review" and plan["scope"] == "changed"
    assert plan["protocol"]["changed"] == [HELPER] and plan["evidence_files"] == []
    brief = (tmp_path / "plan" / "brief.md").read_text(encoding="utf-8")
    unchanged = brief.split("identical since:", 1)[1].split("Review the first list", 1)[0]
    assert CHECKER in unchanged and CAPTURE in unchanged and HELPER not in unchanged


def test_prose_is_listed_apart_from_a_scoped_follow_up(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    readme = {"filename": "README.md", "additions": 1, "deletions": 0, "sha": "9" * 40}
    record = _record(TWO_FILE_DIFF, "SHIP")
    _, values, plan = _plan(
        monkeypatch, tmp_path, _fake(_review(record), files=[*TWO_FILES, readme], diff=CAPTURE_CHANGED)
    )
    assert values["decision"] == "review" and plan["scope"] == "changed"
    brief = (tmp_path / "plan" / "brief.md").read_text(encoding="utf-8")
    assert "README.md" in brief.split("not compared with that review:", 1)[1]


def test_a_text_data_file_is_tracked_like_code(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    data = "_sources/tpc-ds/tools/column_list.txt"
    files = [*TWO_FILES, {"filename": data, "additions": 1, "deletions": 0, "sha": "5" * 40}]
    section = f"diff --git a/{data} b/{data}\n--- a/{data}\n+++ b/{data}\n@@ -1 +1 @@\n-a\n+b\n"
    record = _record(TWO_FILE_DIFF + section, "SHIP")
    changed = TWO_FILE_DIFF + section.replace("+b", "+c")
    _, values, plan = _plan(monkeypatch, tmp_path, _fake(_review(record), files=files, diff=changed))
    assert values["decision"] == "review" and plan["protocol"]["changed"] == [data]


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
