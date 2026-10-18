from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

ROOT = Path(__file__).resolve().parents[3]
BRANCH = "feat/cloud-change"
NODE_ID = "PR_kwDOcloud7"
REPO = "benchbox-dev/benchbox"
OTHER_SHA = "f" * 40

skip_on_windows = pytest.mark.skipif(
    os.name == "nt",
    reason="the make recipes run through a POSIX shell and the shim is a shell script; Linux and macOS cover them",
)


def _load():
    spec = importlib.util.spec_from_file_location("remote_closeout", ROOT / "scripts/remote_closeout.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


remote_closeout = _load()
pr_landing = remote_closeout.pr_landing


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, text=True, capture_output=True).stdout.strip()


@dataclass
class World:
    origin: Path
    seed: Path
    clone: Path
    base: str
    head: str

    def digest(self, base: str, head: str) -> str:
        raw = subprocess.run(
            ["git", "diff", "--raw", "--no-renames", "--no-abbrev", base, head],
            cwd=self.seed,
            check=True,
            capture_output=True,
        ).stdout
        return hashlib.sha256(raw).hexdigest()

    def push_pr_commit(self, path: str) -> str:
        git(self.seed, "switch", "--quiet", BRANCH)
        (self.seed / path).parent.mkdir(parents=True, exist_ok=True)
        (self.seed / path).write_text(f"{path} revision\n", encoding="utf-8")
        git(self.seed, "add", path)
        git(self.seed, "commit", "--quiet", "-m", f"revise {path}")
        git(self.seed, "push", "--quiet", "--force", "origin", f"{BRANCH}:refs/pull/7/head")
        return git(self.seed, "rev-parse", "HEAD")


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Fixture")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "fixture@example.invalid")
        monkeypatch.setenv(f"GIT_{role}_DATE", "2026-01-01T00:00:00Z")
    monkeypatch.setenv(remote_closeout.STORE_ENV, str(tmp_path / "reviews"))


def build_world(tmp_path: Path, changed: str = "docs/note.md") -> World:
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    git(tmp_path, "init", "--quiet", "--bare", "-b", "develop", str(origin))
    git(tmp_path, "init", "--quiet", "-b", "develop", str(seed))
    (seed / "README.md").write_text("base\n", encoding="utf-8")
    git(seed, "add", "README.md")
    git(seed, "commit", "--quiet", "-m", "base")
    base = git(seed, "rev-parse", "HEAD")
    git(seed, "remote", "add", "origin", str(origin))
    git(seed, "switch", "--quiet", "-c", BRANCH)
    (seed / changed).parent.mkdir(parents=True, exist_ok=True)
    (seed / changed).write_text("change\n", encoding="utf-8")
    git(seed, "add", changed)
    git(seed, "commit", "--quiet", "-m", "change")
    head = git(seed, "rev-parse", "HEAD")
    git(seed, "switch", "--quiet", "develop")
    (seed / "later.md").write_text("develop moved on\n", encoding="utf-8")
    git(seed, "add", "later.md")
    git(seed, "commit", "--quiet", "-m", "later")
    git(seed, "push", "--quiet", "origin", "develop", f"{BRANCH}:refs/pull/7/head")
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "--quiet", str(origin), str(clone))
    return World(origin, seed, clone, base, head)


@pytest.fixture
def world(tmp_path: Path) -> World:
    return build_world(tmp_path)


class FakeGitHub:
    def __init__(self, world: World) -> None:
        self.clone = world.clone
        self.view = {
            "number": 7,
            "state": "OPEN",
            "isDraft": False,
            "labels": [{"name": "agent"}],
            "reviewDecision": "",
            "headRefOid": world.head,
            "headRefName": BRANCH,
            "baseRefName": "develop",
            "id": NODE_ID,
        }
        self.reported_head = world.head
        self.threads: list[dict] = [{"id": "T1", "isResolved": True, "isOutdated": False}]
        self.conclusions = dict.fromkeys(pr_landing.REQUIRED_CONTEXTS, "success")
        self.children: list[int] = []
        self.index: dict = {"schema_version": 1, "items": {}}
        self.auto_merge: dict | None = None
        self.merge_code = 0
        self.calls: list[list[str]] = []

    def set_head(self, head: str) -> None:
        self.view["headRefOid"] = head
        self.reported_head = head

    def __call__(self, cmd: Sequence[str]) -> tuple[int, str]:
        cmd = list(cmd)
        self.calls.append(cmd)
        if cmd == ["git", "remote", "get-url", "origin"]:
            return 0, "https://github.com/BenchBox-dev/BenchBox.git\n"
        if cmd[0] == "git":
            proc = subprocess.run(cmd, cwd=self.clone, capture_output=True, check=False)
            out = proc.stdout.decode("utf-8", "surrogateescape")
            return proc.returncode, out if proc.returncode == 0 else out + proc.stderr.decode()
        if cmd[:3] == ["gh", "pr", "view"]:
            if "--jq" in cmd:
                return 0, self.reported_head + "\n"
            return 0, json.dumps({**self.view, "autoMergeRequest": self.auto_merge})
        if cmd[:3] == ["gh", "pr", "list"]:
            return 0, json.dumps([{"number": child} for child in self.children])
        if cmd[:3] == ["gh", "pr", "merge"]:
            if "--disable-auto" in cmd:
                self.auto_merge = None
                return 0, ""
            if self.merge_code == 0:
                self.auto_merge = {"mergeMethod": "SQUASH"}
            return self.merge_code, "armed"
        if cmd[:3] == ["gh", "api", "graphql"]:
            page = {"nodes": self.threads, "pageInfo": {"hasNextPage": False, "endCursor": None}}
            return 0, json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": page}}}})
        if cmd[:2] == ["gh", "api"] and "/check-runs" in cmd[2]:
            head = cmd[2].split("/commits/")[1].split("/")[0]
            runs = [
                {"name": name, "conclusion": conclusion, "head_sha": head, "status": "completed", "started_at": "t"}
                for name, conclusion in self.conclusions.items()
            ]
            return 0, json.dumps({"total": len(runs), "runs": runs})
        if cmd[:2] == ["gh", "api"] and "contents/index.json?ref=todo-state" in cmd[-1]:
            return 0, json.dumps(self.index)
        raise AssertionError(f"unexpected command {cmd}")

    def arms(self) -> list[list[str]]:
        return [call for call in self.calls if call[:3] == ["gh", "pr", "merge"] and "--auto" in call]


def review(world: World, head: str | None = None, **overrides) -> dict:
    head = head or world.head
    record = {
        "pr_number": 7,
        "head_branch": BRANCH,
        "pr_node_id": NODE_ID,
        "base_sha": world.base,
        "head_sha": head,
        "manifest_digest": world.digest(world.base, head),
        "findings": [{"location": "docs/note.md:1", "reasoning": "wording", "severity": "low"}],
        "disposition": "approve",
        "reviewer": {"model": "other-family", "run_id": "run-1"},
    }
    record.update(overrides)
    return record


def store(tmp_path: Path, gh: FakeGitHub, record: dict, builder_head: str) -> int:
    path = tmp_path / f"record-{len(list(tmp_path.glob('record-*.json')))}.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    return remote_closeout.record("7", str(path), builder_head, run=gh)


def stored(world: World) -> dict:
    return json.loads(remote_closeout.store_path(REPO, 7).read_text(encoding="utf-8"))


@pytest.fixture
def ready_pr(tmp_path: Path, world: World) -> FakeGitHub:
    gh = FakeGitHub(world)
    assert remote_closeout.bind("7", BRANCH, run=gh) == 0
    assert store(tmp_path, gh, review(world), world.head) == 0
    return gh


def arm(world: World, gh: FakeGitHub, head: str | None = None, **kwargs) -> int:
    return remote_closeout.arm("7", head or world.head, run=gh, checkout=world.clone, **kwargs)


def merge_command(head: str) -> list[str]:
    return ["gh", "pr", "merge", "7", "--repo", REPO, "--squash", "--auto", "--match-head-commit", head]


def local_state(clone: Path) -> tuple[str, ...]:
    return (
        git(clone, "status", "--porcelain", "--ignored"),
        git(clone, "for-each-ref", "refs/heads", "--format=%(refname) %(objectname)"),
        git(clone, "symbolic-ref", "HEAD"),
        (clone / ".git" / "config").read_text(encoding="utf-8"),
        "\n".join(sorted(path.name for path in (clone / ".git").glob("config*"))),
    )


def test_arms_the_recorded_head_pinned_with_match_head_commit(world: World, ready_pr: FakeGitHub) -> None:
    assert arm(world, ready_pr) == 0
    assert ready_pr.arms() == [merge_command(world.head)]
    armed = stored(world)["arms"]
    assert [(entry["head"], entry["via"], entry["owner_explained"]) for entry in armed] == [(world.head, "arm", None)]


def test_arming_changes_no_working_tree_file_branch_or_git_config(world: World, ready_pr: FakeGitHub) -> None:
    before = local_state(world.clone)
    assert arm(world, ready_pr) == 0
    assert local_state(world.clone) == before


def test_fetches_without_tags_or_maintenance_and_never_uses_the_compare_api(world: World, ready_pr: FakeGitHub) -> None:
    assert arm(world, ready_pr) == 0
    fetches = [call for call in ready_pr.calls if "fetch" in call]
    prefix = ["git", "-c", "gc.auto=0", "-c", "maintenance.auto=false", "fetch", "--no-tags", "origin"]
    assert fetches == [[*prefix, "refs/pull/7/head"], [*prefix, "develop"]]
    assert not any("compare" in part for call in ready_pr.calls for part in call)


def test_manifest_reports_the_merge_base_and_digest_a_record_must_carry(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    gh = FakeGitHub(world)
    assert remote_closeout.manifest("7", world.head, run=gh) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["base_sha"] == world.base
    assert printed["manifest_digest"] == world.digest(world.base, world.head)


def test_the_digest_does_not_depend_on_core_abbrev(world: World, capsys: pytest.CaptureFixture[str]) -> None:
    git(world.seed, "config", "core.abbrev", "7")
    expected = world.digest(world.base, world.head)
    gh = FakeGitHub(world)
    digests = set()
    for abbrev in ("4", "12", "40"):
        git(world.clone, "config", "core.abbrev", abbrev)
        assert remote_closeout.manifest("7", world.head, run=gh) == 0
        digests.add(json.loads(capsys.readouterr().out)["manifest_digest"])
    assert digests == {expected}


def _refusal(world: World, gh: FakeGitHub, capsys: pytest.CaptureFixture[str], **kwargs) -> str:
    assert arm(world, gh, **kwargs) == 2
    assert gh.arms() == []
    return capsys.readouterr().err


def test_refuses_a_soundness_path_change(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    world = build_world(tmp_path, changed="benchbox/core/equivalence/new_rule.py")
    gh = FakeGitHub(world)
    assert remote_closeout.bind("7", BRANCH, run=gh) == 0
    assert store(tmp_path, gh, review(world), world.head) == 0
    assert "condition 1: the fetched base..head diff touches a soundness path" in _refusal(world, gh, capsys)


def test_refuses_a_pr_stacked_on_another_branch(
    world: World, ready_pr: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    ready_pr.view["baseRefName"] = "feat/parent"
    err = _refusal(world, ready_pr, capsys)
    assert "condition 1: stacked PR: it targets 'feat/parent'" in err
    assert "condition 6: the PR targets 'feat/parent', not 'develop'" in err


def test_refuses_the_bottom_of_a_stack(world: World, ready_pr: FakeGitHub, capsys: pytest.CaptureFixture[str]) -> None:
    ready_pr.children = [8]
    assert f"condition 1: stacked PR: open PR #8 is based on its head branch '{BRANCH}'" in _refusal(
        world, ready_pr, capsys
    )


@pytest.mark.parametrize(
    ("batch", "expected"),
    [
        ({"integration_branch": BRANCH}, "its head branch is the integration branch of batch 'feature-x'"),
        (
            {"final_pr": {"number": 7, "node_id": "PR_other", "head": OTHER_SHA}},
            "it is the final PR of batch 'feature-x'",
        ),
        (
            {"final_pr": {"number": 99, "node_id": NODE_ID, "head": OTHER_SHA}},
            "it is the final PR of batch 'feature-x'",
        ),
    ],
)
def test_refuses_a_registered_batch_integration_or_final_pr(
    world: World, ready_pr: FakeGitHub, capsys: pytest.CaptureFixture[str], batch: dict, expected: str
) -> None:
    registered = {"repository": "BenchBox-dev/BenchBox", "integration_branch": "feat/other", "members": ["a"]}
    ready_pr.index = {"schema_version": 3, "items": {}, "batches": {"feature-x": {**registered, **batch}}}
    assert f"condition 1: batch PR: {expected}" in _refusal(world, ready_pr, capsys)


@pytest.mark.parametrize(
    ("batch", "refused"),
    [
        ({"repository": "BenchBox-dev/BenchBox", "lifecycle": "active"}, True),
        ({"repository": "BenchBox-dev/BenchBox", "lifecycle": "aborted"}, False),
        ({"repository": "someone/else", "lifecycle": "active"}, False),
    ],
)
def test_refuses_a_pr_whose_bound_item_is_an_active_batch_member(
    tmp_path: Path, world: World, capsys: pytest.CaptureFixture[str], batch: dict, refused: bool
) -> None:
    gh = FakeGitHub(world)
    assert remote_closeout.bind("7", BRANCH, item="cloud-item", run=gh) == 0
    assert store(tmp_path, gh, review(world), world.head) == 0
    registered = {"integration_branch": "feat/other", "members": ["cloud-item"], **batch}
    gh.index = {"schema_version": 3, "items": {}, "batches": {"feature-x": registered}}
    assert arm(world, gh) == (2 if refused else 0)
    message = "condition 1: batch PR: its tracker item 'cloud-item' is a member of batch 'feature-x'"
    assert (message in capsys.readouterr().err) is refused


def test_refuses_a_branch_a_local_feature_batch_worktree_integrates(
    tmp_path: Path, world: World, ready_pr: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    integration = tmp_path / "integration"
    git(world.clone, "worktree", "add", "--quiet", "-b", BRANCH, str(integration), "origin/develop")
    git(world.clone, "config", "extensions.worktreeConfig", "true")
    remote_closeout.batch_integration._config_set(integration, "id", "feature-x")
    assert "condition 1: feature-mode PR" in _refusal(world, ready_pr, capsys)


def test_refuses_without_a_binding(tmp_path: Path, world: World, capsys: pytest.CaptureFixture[str]) -> None:
    gh = FakeGitHub(world)
    err = _refusal(world, gh, capsys)
    assert "condition 2: no binding is recorded for PR #7" in err
    assert "condition 3: no review record is stored for this PR" in err


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("id", "PR_replaced", "condition 2: the live PR node id 'PR_replaced' is not the bound 'PR_kwDOcloud7'"),
        ("headRefName", "feat/reused", "condition 2: the live PR head branch 'feat/reused' is not the bound"),
    ],
)
def test_refuses_when_the_live_pr_is_not_the_bound_one(
    world: World, ready_pr: FakeGitHub, capsys: pytest.CaptureFixture[str], field: str, value: str, expected: str
) -> None:
    ready_pr.view[field] = value
    assert expected in _refusal(world, ready_pr, capsys)


def test_refuses_when_the_latest_record_requests_changes(
    tmp_path: Path, world: World, ready_pr: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    assert store(tmp_path, ready_pr, review(world, disposition="changes-requested"), world.head) == 0
    assert "condition 3: the latest record's disposition is 'changes-requested'" in _refusal(world, ready_pr, capsys)


def test_refuses_when_the_latest_record_is_for_another_head(
    tmp_path: Path, world: World, ready_pr: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    assert store(tmp_path, ready_pr, review(world, head_sha=OTHER_SHA), world.head) == 0
    assert f"condition 3: the latest record reviewed {OTHER_SHA[:12]}" in _refusal(world, ready_pr, capsys)


def test_refuses_when_head_is_not_the_live_head(
    world: World, ready_pr: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    err = _refusal(world, ready_pr, capsys, head=world.base)
    assert f"condition 3: the live PR head {world.head[:12]} is not HEAD {world.base[:12]}" in err


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"base_sha": OTHER_SHA}, "condition 4: the recomputed merge-base"),
        ({"manifest_digest": "0" * 64}, "condition 4: the recomputed manifest digest"),
    ],
)
def test_refuses_when_the_recomputed_manifest_disagrees_with_the_record(
    tmp_path: Path,
    world: World,
    ready_pr: FakeGitHub,
    capsys: pytest.CaptureFixture[str],
    override: dict,
    expected: str,
) -> None:
    assert store(tmp_path, ready_pr, review(world, **override), world.head) == 0
    assert expected in _refusal(world, ready_pr, capsys)


def test_stops_when_the_fetched_head_is_not_the_head_github_reports(
    world: World, ready_pr: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    ready_pr.reported_head = OTHER_SHA
    err = _refusal(world, ready_pr, capsys)
    assert f"condition 4: fetched {world.head[:12]} for PR #7 but GitHub reports {OTHER_SHA[:12]}; stopping" in err


def test_refuses_when_the_builder_reported_another_head(
    tmp_path: Path, world: World, ready_pr: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    assert store(tmp_path, ready_pr, review(world), world.base) == 0
    assert f"condition 5: the builder's latest report names {world.base[:12]}" in _refusal(world, ready_pr, capsys)


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (
            {"labels": [{"name": "agent"}, {"name": "no-auto-merge"}]},
            "condition 6: the 'no-auto-merge' label is present",
        ),
        ({"isDraft": True}, "condition 6: the PR is a draft"),
        ({"reviewDecision": "CHANGES_REQUESTED"}, "condition 6: a reviewer requested changes"),
        ({"state": "CLOSED"}, "condition 6: the PR is 'CLOSED', not OPEN"),
    ],
)
def test_refuses_a_pr_that_is_held_draft_closed_or_has_requested_changes(
    world: World, ready_pr: FakeGitHub, capsys: pytest.CaptureFixture[str], change: dict, expected: str
) -> None:
    ready_pr.view.update(change)
    assert expected in _refusal(world, ready_pr, capsys)


def test_refuses_an_unresolved_review_thread(
    world: World, ready_pr: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    ready_pr.threads = [{"id": "T1", "isResolved": False, "isOutdated": False}]
    assert "condition 6: an unresolved, non-outdated review thread is open" in _refusal(world, ready_pr, capsys)


@pytest.mark.parametrize(("conclusion", "refused"), [("failure", True), ("cancelled", True), (None, False)])
def test_refuses_a_failing_required_check_but_not_a_pending_one(
    world: World, ready_pr: FakeGitHub, capsys: pytest.CaptureFixture[str], conclusion: str | None, refused: bool
) -> None:
    ready_pr.conclusions["core"] = conclusion
    assert arm(world, ready_pr) == (2 if refused else 0)
    expected = f"condition 6: required check 'core' failed with conclusion {conclusion!r}"
    assert (expected in capsys.readouterr().err) is refused


def test_refuses_a_changed_observation_unless_the_owner_explained_it(
    world: World, ready_pr: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    ready_pr.view["labels"] = [{"name": "agent"}, {"name": "needs-docs"}]
    ready_pr.threads = [*ready_pr.threads, {"id": "T2", "isResolved": True, "isOutdated": False}]
    err = _refusal(world, ready_pr, capsys)
    assert "condition 7: labels changed from ['agent'] to ['agent', 'needs-docs']" in err
    assert "condition 7: threads changed from 1 threads, 0 open to 2 threads, 0 open" in err
    statement = 'Owner: "the needs-docs label and the resolved thread are fine; arm it"'
    assert arm(world, ready_pr, owner_explained=statement) == 0
    armed = stored(world)["arms"][-1]
    assert armed["owner_explained"] == statement
    assert len(armed["observation_changes"]) == 2


def test_reports_every_failed_condition(
    tmp_path: Path, world: World, ready_pr: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    assert store(tmp_path, ready_pr, review(world, disposition="changes-requested"), world.base) == 0
    ready_pr.view["isDraft"] = True
    err = _refusal(world, ready_pr, capsys)
    for condition in ("condition 3:", "condition 5:", "condition 6:", "condition 7:"):
        assert condition in err


def test_passes_through_a_failing_merge(world: World, ready_pr: FakeGitHub) -> None:
    ready_pr.merge_code = 1
    assert arm(world, ready_pr) == 1
    assert stored(world)["arms"] == []


def test_withdraw_disables_auto_merge_for_the_bound_pr(world: World, ready_pr: FakeGitHub) -> None:
    assert arm(world, ready_pr) == 0
    assert remote_closeout.withdraw("7", run=ready_pr) == 0
    assert ["gh", "pr", "merge", "--repo", REPO, "--disable-auto", "7"] in ready_pr.calls
    assert ready_pr.auto_merge is None
    assert stored(world)["withdrawal"]["records_before"] == 1


def test_withdraw_refuses_a_pr_whose_node_id_is_not_the_bound_one(
    world: World, ready_pr: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    assert arm(world, ready_pr) == 0
    ready_pr.view["id"] = "PR_replaced"
    assert remote_closeout.withdraw("7", run=ready_pr) == 2
    assert "node id does not match" in capsys.readouterr().err
    assert ready_pr.auto_merge is not None
    assert stored(world)["withdrawal"] is None


def test_withdraw_refuses_an_unbound_pr(world: World, capsys: pytest.CaptureFixture[str]) -> None:
    gh = FakeGitHub(world)
    assert remote_closeout.withdraw("7", run=gh) == 2
    assert "no binding is recorded for PR #7" in capsys.readouterr().err
    assert gh.calls == []


def test_arm_refuses_a_withdrawn_pr(world: World, ready_pr: FakeGitHub, capsys: pytest.CaptureFixture[str]) -> None:
    assert remote_closeout.withdraw("7", run=ready_pr) == 0
    assert "the PR is withdrawn; re-arm it with make pr-landing-remote-ready" in _refusal(world, ready_pr, capsys)


def test_ready_requires_a_withdrawal(world: World, ready_pr: FakeGitHub, capsys: pytest.CaptureFixture[str]) -> None:
    assert remote_closeout.ready("7", world.head, run=ready_pr, checkout=world.clone) == 2
    assert "no withdrawal is recorded" in capsys.readouterr().err
    assert ready_pr.arms() == []


def test_ready_rearms_a_revised_head_only_with_a_record_stored_after_the_withdrawal(
    tmp_path: Path, world: World, ready_pr: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    assert arm(world, ready_pr) == 0
    assert remote_closeout.withdraw("7", run=ready_pr) == 0
    revised = world.push_pr_commit("docs/second.md")
    ready_pr.set_head(revised)

    assert remote_closeout.ready("7", revised, run=ready_pr, checkout=world.clone) == 2
    err = capsys.readouterr().err
    assert "no review record was stored after the withdrawal" in err
    assert f"condition 3: the latest record reviewed {world.head[:12]}, not HEAD {revised[:12]}" in err

    assert store(tmp_path, ready_pr, review(world, head=revised), revised) == 0
    assert remote_closeout.ready("7", revised, run=ready_pr, checkout=world.clone) == 0
    assert ready_pr.arms()[-1] == merge_command(revised)
    state = stored(world)
    assert state["withdrawal"] is None
    assert [(entry["head"], entry["via"]) for entry in state["arms"]] == [(world.head, "arm"), (revised, "ready")]
    assert state["arms"][-1]["cleared_withdrawal"]["records_before"] == 1


def test_ready_reapplies_every_condition_to_the_new_head(
    tmp_path: Path, world: World, ready_pr: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    assert remote_closeout.withdraw("7", run=ready_pr) == 0
    revised = world.push_pr_commit("benchbox/core/equivalence/new_rule.py")
    ready_pr.set_head(revised)
    assert store(tmp_path, ready_pr, review(world, head=revised), revised) == 0
    assert remote_closeout.ready("7", revised, run=ready_pr, checkout=world.clone) == 2
    assert "condition 1: the fetched base..head diff touches a soundness path" in capsys.readouterr().err
    assert stored(world)["withdrawal"] is not None


def test_bind_refuses_a_branch_the_pr_does_not_have(world: World, capsys: pytest.CaptureFixture[str]) -> None:
    gh = FakeGitHub(world)
    assert remote_closeout.bind("7", "feat/something-else", run=gh) == 2
    assert "not the reported 'feat/something-else'" in capsys.readouterr().err
    assert not remote_closeout.store_path(REPO, 7).exists()


def test_a_binding_never_changes(world: World, capsys: pytest.CaptureFixture[str]) -> None:
    gh = FakeGitHub(world)
    assert remote_closeout.bind("7", BRANCH, run=gh) == 0
    first = stored(world)["binding"]
    assert remote_closeout.bind("7", BRANCH, run=gh) == 0
    gh.view["id"] = "PR_replaced"
    assert remote_closeout.bind("7", BRANCH, run=gh) == 2
    assert "a binding never changes" in capsys.readouterr().err
    assert stored(world)["binding"] == first


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"head_sha": "abc123"}, "head_sha must be a full lowercase commit SHA"),
        ({"base_sha": "A" * 40}, "base_sha must be a full lowercase commit SHA"),
        ({"manifest_digest": "xyz"}, "manifest_digest must be a lowercase SHA-256 hex digest"),
        ({"disposition": "lgtm"}, "disposition must be 'approve' or 'changes-requested'"),
        ({"findings": [{"location": "a.py:1"}]}, "findings must be a list of objects"),
        ({"reviewer": {"model": "m"}}, "reviewer must be an object"),
        ({"pr_number": True}, "pr_number must be a positive integer"),
        ({"verdict": "approve"}, "unknown field 'verdict'"),
        ({"pr_node_id": "PR_other"}, "the record's pr_node_id disagree with the binding"),
        ({"head_branch": "feat/other"}, "the record's head_branch disagree with the binding"),
        ({"pr_number": 8}, "the record's pr_number disagree with the binding"),
    ],
)
def test_record_refuses_an_invalid_record_or_one_for_another_pr(
    tmp_path: Path, world: World, capsys: pytest.CaptureFixture[str], override: dict, expected: str
) -> None:
    gh = FakeGitHub(world)
    assert remote_closeout.bind("7", BRANCH, run=gh) == 0
    assert store(tmp_path, gh, review(world, **override), world.head) == 2
    assert expected in capsys.readouterr().err
    assert stored(world)["records"] == []


def test_record_refuses_a_missing_field_and_an_unbound_pr(
    tmp_path: Path, world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    gh = FakeGitHub(world)
    incomplete = review(world)
    del incomplete["reviewer"]
    assert store(tmp_path, gh, incomplete, world.head) == 2
    assert "missing field 'reviewer'" in capsys.readouterr().err
    assert store(tmp_path, gh, review(world), world.head) == 2
    assert "bind it before storing a record" in capsys.readouterr().err


def test_record_stores_the_builder_head_and_the_live_observation(world: World, ready_pr: FakeGitHub) -> None:
    [entry] = stored(world)["records"]
    assert entry["builder_head"] == world.head
    assert entry["observation"] == {
        "labels": ["agent"],
        "draft": False,
        "threads": [{"id": "T1", "resolved": True, "outdated": False}],
    }


@pytest.mark.parametrize("head", ["abc123", "A" * 40, "", "main"])
def test_a_head_that_is_not_a_full_lowercase_sha_is_refused_before_any_command(
    world: World, head: str, capsys: pytest.CaptureFixture[str]
) -> None:
    gh = FakeGitHub(world)
    assert remote_closeout.arm("7", head, run=gh, checkout=world.clone) == 2
    assert gh.calls == []
    assert "full lowercase commit SHA" in capsys.readouterr().err


def _make(tmp_path: Path, target: str, *assignments: str) -> tuple[list[str], dict[str, str]]:
    shim = tmp_path / "uv"
    shim.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$@" > "$RECORD_DIR/argv"\nenv | grep "^REMOTE_CLOSEOUT_" | sort > "$RECORD_DIR/env"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    names = {"PR", "HEAD", "REPO", "BRANCH", "ITEM", "RECORD", "BUILDER_HEAD", "OWNER_EXPLAINED"}
    env = {k: v for k, v in os.environ.items() if k not in names and not k.startswith("REMOTE_CLOSEOUT_")}
    env.update({"PATH": f"{tmp_path}{os.pathsep}{env['PATH']}", "RECORD_DIR": str(tmp_path)})
    result = subprocess.run(
        ["make", "--no-print-directory", target, *assignments],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    argv = (tmp_path / "argv").read_text(encoding="utf-8").splitlines()
    pairs = [line.split("=", 1) for line in (tmp_path / "env").read_text(encoding="utf-8").splitlines()]
    return argv, dict(pairs)


@pytest.mark.parametrize(
    ("target", "command"),
    [
        ("pr-arm-remote", "arm"),
        ("pr-landing-remote-withdraw", "withdraw"),
        ("pr-landing-remote-ready", "ready"),
        ("pr-landing-remote-bind", "bind"),
        ("pr-landing-remote-record", "record"),
        ("pr-landing-remote-manifest", "manifest"),
    ],
)
@skip_on_windows
def test_each_target_runs_its_subcommand_without_syncing_the_environment(
    tmp_path: Path, target: str, command: str
) -> None:
    argv, _ = _make(tmp_path, target, "PR=7")
    assert argv == ["run", "--no-sync", "--", "python", "scripts/remote_closeout.py", command]


@skip_on_windows
def test_the_make_wrapper_hands_values_over_untouched_and_runs_no_shell_metacharacters(tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    statement = f'Owner said "arm it"; touch {marker} `touch {marker}` $(touch {marker})'
    _, env = _make(tmp_path, "pr-arm-remote", f"PR=7; touch {marker}", f"OWNER_EXPLAINED={statement}")
    assert env["REMOTE_CLOSEOUT_PR"] == f"7; touch {marker}"
    assert env["REMOTE_CLOSEOUT_OWNER_EXPLAINED"] == statement
    assert not marker.exists()


def test_main_reads_the_make_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple] = []
    monkeypatch.setattr(remote_closeout, "ready", lambda *args: seen.append(args) or 0)
    env = {
        "REMOTE_CLOSEOUT_PR": "7",
        "REMOTE_CLOSEOUT_HEAD": OTHER_SHA,
        "REMOTE_CLOSEOUT_OWNER_EXPLAINED": "owner said so",
        "REMOTE_CLOSEOUT_REPO": "",
    }
    assert remote_closeout.main(["ready"], env=env) == 0
    assert seen == [("7", OTHER_SHA, remote_closeout.REPOSITORY, "owner said so")]
