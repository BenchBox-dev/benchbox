from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


remote_closeout = _load("remote_closeout")
remote_review = _load("remote_review")
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


def build_world(tmp_path: Path, changed: str = "docs/note.md", base_files: Mapping[str, str] | None = None) -> World:
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    git(tmp_path, "init", "--quiet", "--bare", "-b", "develop", str(origin))
    git(tmp_path, "init", "--quiet", "-b", "develop", str(seed))
    for path, text in {"README.md": "base\n", **(base_files or {})}.items():
        (seed / path).parent.mkdir(parents=True, exist_ok=True)
        (seed / path).write_text(text, encoding="utf-8")
    git(seed, "add", ".")
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
            "isCrossRepository": False,
            "title": "Add a cloud note",
            "body": "Adds docs/note.md.",
        }
        self.reported_head = world.head
        self.threads: list[dict] = [{"id": "T1", "isResolved": True, "isOutdated": False}]
        self.conclusions = dict.fromkeys(pr_landing.REQUIRED_CONTEXTS, "success")
        self.stale_suites: list[dict[str, Any]] = []
        self.children: list[int] = []
        self.index: dict = {"schema_version": 1, "items": {}}
        self.auto_merge: dict | None = None
        self.merge_code = 0
        self.calls: list[list[str]] = []
        self.items: dict[str, dict] = {}
        self.reviews: list[dict] = []
        self.resolved: list[str] = []
        self.post_failures: list[str] = []
        self.post_attempts: list[dict] = []
        self.deleted: set[str] = set()
        self.gate_state = "success"
        self.dispatch_code = 0
        self.verdict: dict | None = {"verdict": "pass"}
        self.verdict_lookup = (0, None)
        self.verdict_bound_head: str | None = None
        self.verdict_download = (0, None)
        self.changed_files: list[str] = []
        self.statuses: dict[str, str] = {}
        self.status_descriptions: dict[str, str] = {}

    def _duration_verdict_reply(self, cmd: list[str]) -> tuple[int, str] | None:
        if cmd[:2] == ["gh", "api"] and "/actions/artifacts?name=fast-duration-verdict-" in cmd[-1]:
            code, text = self.verdict_lookup
            if code != 0:
                return code, text
            head = self.verdict_bound_head or self.view["headRefOid"]
            return 0, "" if self.verdict is None else f"555 {head}"
        if cmd[:3] == ["gh", "run", "download"]:
            code, text = self.verdict_download
            if code != 0:
                return code, text
            Path(cmd[cmd.index("-D") + 1], "fast-duration-verdict.json").write_text(
                text if text is not None else json.dumps(self.verdict), encoding="utf-8"
            )
            return 0, ""
        if cmd[:2] == ["gh", "api"] and cmd[-1].endswith("/pulls/7/files"):
            return 0, "\n".join(self.changed_files)
        return None

    def _todo_state_reply(self, cmd: list[str]) -> tuple[int, str] | None:
        if cmd[:2] == ["gh", "api"] and "contents/index.json?ref=todo-state" in cmd[-1]:
            return 0, json.dumps(self.index)
        if cmd[:2] == ["gh", "api"] and "contents/items/" in cmd[-1]:
            name = cmd[-1].split("contents/items/")[1].removesuffix(".json?ref=todo-state")
            return (0, json.dumps(self.items[name])) if name in self.items else (1, "gh: Not Found (HTTP 404)")
        return None

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
        if cmd[:3] == ["gh", "api", "graphql"] and "resolveReviewThread" in cmd[4]:
            return self._resolve(cmd[-1].removeprefix("thread="))
        if cmd[:3] == ["gh", "api", "graphql"]:
            page = {"nodes": self.threads, "pageInfo": {"hasNextPage": False, "endCursor": None}}
            return 0, json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": page}}}})
        if cmd[:4] == ["gh", "api", "--method", "POST"] and cmd[4] == f"repos/{REPO}/pulls/7/reviews":
            return self._post_review(json.loads(Path(cmd[cmd.index("--input") + 1]).read_text(encoding="utf-8")))
        if cmd[:3] == ["gh", "api", "--paginate"] and cmd[3] == f"repos/{REPO}/pulls/7/reviews":
            marker = re.search(r"benchbox-remote-review [0-9a-f]+", cmd[-1])
            commit = re.search(r'\.commit_id == "([0-9a-f]+)"', cmd[-1])
            assert marker and commit
            matches = [
                str(review["id"])
                for review in self.reviews
                if review["commit_id"] == commit[1] and marker[0] in review["body"]
            ]
            return 0, "".join(f"{match}\n" for match in matches)
        gate = self._gate(cmd)
        if gate is not None:
            return gate
        if cmd[:2] == ["gh", "api"] and "/rules/branches/" in cmd[-1]:
            return 0, "\n".join([*pr_landing.REQUIRED_CONTEXTS, "hold", "oracle-gate", "oracle-review"])
        if cmd[:2] == ["gh", "api"] and "/check-runs" in cmd[-1]:
            head = cmd[-1].split("/commits/")[1].split("/")[0]
            runs = [
                {
                    "id": index,
                    "name": name,
                    "conclusion": conclusion,
                    "head_sha": head,
                    "status": "completed" if conclusion else "in_progress",
                    "started_at": "t",
                    "details_url": f"https://github.com/o/r/actions/runs/{index}/job/{index}",
                    "check_suite": {"id": index},
                }
                for index, (name, conclusion) in enumerate(self.conclusions.items(), 1)
            ]
            runs += [{**stale, "head_sha": head} for stale in self.stale_suites]
            return 0, "\n".join(json.dumps(run) for run in runs)
        side_reply = self._duration_verdict_reply(cmd) or self._todo_state_reply(cmd)
        if side_reply is not None:
            return side_reply
        raise AssertionError(f"unexpected command {cmd}")

    def arms(self) -> list[list[str]]:
        return [call for call in self.calls if call[:3] == ["gh", "pr", "merge"] and "--auto" in call]

    def _gate(self, cmd: list[str]) -> tuple[int, str] | None:
        if cmd[:2] == ["gh", "api"] and cmd[-1].endswith("/status"):
            return 0, self.gate_state + "\n"
        if cmd[:2] == ["gh", "api"] and "/statuses?" in cmd[-1]:
            lines = [
                f"{name}\t{state}\t{self.status_descriptions.get(name, '')}" for name, state in self.statuses.items()
            ]
            return 0, "\n".join(lines)
        if cmd[:3] == ["gh", "workflow", "run"]:
            return self.dispatch_code, "" if self.dispatch_code == 0 else "gh: HTTP 403"
        return None

    def dispatches(self) -> list[list[str]]:
        return [call for call in self.calls if call[:3] == ["gh", "workflow", "run"]]

    def _resolve(self, thread_id: str) -> tuple[int, str]:
        if thread_id in self.deleted:
            return 1, f"gh: Could not resolve to a node with the global id of '{thread_id}'"
        self.resolved.append(thread_id)
        for thread in self.threads:
            if thread["id"] == thread_id:
                thread["isResolved"] = True
        return 0, json.dumps({"data": {"resolveReviewThread": {"thread": {"id": thread_id, "isResolved": True}}}})

    def _post_review(self, payload: dict) -> tuple[int, str]:
        self.post_attempts.append(payload)
        failure = self.post_failures.pop(0) if self.post_failures else None
        if (failure == "422" and payload["comments"]) or failure == "422-always":
            return 1, "gh: Unprocessable Entity (HTTP 422): Line could not be resolved"
        if failure == "error":
            return 1, "gh: Bad Gateway (HTTP 502)"
        review_id = 100 + len(self.reviews)
        self.reviews.append({"id": review_id, **payload})
        for comment in payload["comments"]:
            first = {"body": comment["body"], "path": comment["path"], "pullRequestReview": {"databaseId": review_id}}
            self.threads.append(
                {
                    "id": f"RT{len(self.threads)}",
                    "isResolved": False,
                    "isOutdated": False,
                    "comments": {"nodes": [first]},
                }
            )
        if failure == "lost":
            return 1, "gh: Bad Gateway (HTTP 502)"
        return 0, json.dumps({"id": review_id, "node_id": f"PRR_{review_id}"})


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
    assert fetches == [[*prefix, "refs/pull/7/head"], [*prefix, "refs/heads/develop"]]
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


def test_fails_closed_when_a_worktree_on_the_branch_has_an_unreadable_config(
    tmp_path: Path, world: World, ready_pr: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    integration = tmp_path / "integration"
    git(world.clone, "worktree", "add", "--quiet", "-b", BRANCH, str(integration), "origin/develop")
    git(world.clone, "config", "extensions.worktreeConfig", "true")
    worktree_config = Path(git(integration, "rev-parse", "--path-format=absolute", "--git-path", "config.worktree"))
    worktree_config.write_text("[[[not a config\n", encoding="utf-8")
    assert arm(world, ready_pr) == 1
    assert ready_pr.arms() == []
    assert "live state could not be verified" in capsys.readouterr().err


def test_refuses_a_pr_from_a_fork(world: World, ready_pr: FakeGitHub, capsys: pytest.CaptureFixture[str]) -> None:
    ready_pr.view["isCrossRepository"] = True
    assert "condition 1: fork PR" in _refusal(world, ready_pr, capsys)


def test_refuses_a_would_block_duration_verdict_unless_the_baseline_changes(
    world: World, ready_pr: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    ready_pr.verdict = {"verdict": "would-block", "sum_ratio": 1.5, "median_test_ratio": 1.25}
    err = _refusal(world, ready_pr, capsys)
    assert "summed-time ratio 1.500" in err
    assert "median per-test ratio 1.250" in err
    assert "scripts/build_fast_duration_baseline.py" in err


def test_refuses_until_the_fast_duration_verdict_exists(
    world: World, ready_pr: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    ready_pr.verdict = None
    ready_pr.conclusions["core"] = None
    err = _refusal(world, ready_pr, capsys)
    assert "no verify-fast-duration verdict is recorded" in err
    assert "core job" in err
    assert "has not finished" in err
    ready_pr.verdict = {"verdict": "pass"}
    assert arm(world, ready_pr) == 0
    assert len(ready_pr.arms()) == 1


@pytest.mark.parametrize("conclusion", ["failure", "skipped", "cancelled"])
def test_refuses_a_missing_verdict_when_the_producing_job_ended_without_one(
    world: World, ready_pr: FakeGitHub, capsys: pytest.CaptureFixture[str], conclusion: str
) -> None:
    ready_pr.verdict = None
    ready_pr.conclusions["core"] = conclusion
    err = _refusal(world, ready_pr, capsys)
    assert f"ended {conclusion}" in err
    assert "make pr-rerun PR=7" in err


@pytest.mark.parametrize(
    ("attribute", "value", "expected"),
    [
        ("verdict_lookup", (1, "gh: HTTP 502"), "could not look up the verify-fast-duration verdict"),
        ("verdict_bound_head", "0" * 40, "is not bound to"),
        ("verdict_download", (1, "gh: HTTP 404"), "could not download the verify-fast-duration verdict"),
        ("verdict_download", (0, "{not json"), "is unreadable"),
        ("verdict_download", (0, "[1]"), "is unreadable"),
    ],
)
def test_refuses_when_the_fast_duration_verdict_cannot_be_trusted(
    world: World,
    ready_pr: FakeGitHub,
    capsys: pytest.CaptureFixture[str],
    attribute: str,
    value: object,
    expected: str,
) -> None:
    setattr(ready_pr, attribute, value)
    assert expected in _refusal(world, ready_pr, capsys)


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


def gate_dispatch(head: str) -> list[str]:
    return ["gh", "workflow", "run", "oracle-gate.yml", "--repo", REPO, "--ref", "develop"] + [
        "-f",
        "pr=7",
        "-f",
        f"head_sha={head}",
    ]


@pytest.mark.parametrize("state", ["failure", "error", "pending", ""])
def test_dispatches_the_gate_before_arming_when_its_status_is_not_success(
    world: World, ready_pr: FakeGitHub, state: str
) -> None:
    ready_pr.gate_state = state
    assert arm(world, ready_pr) == 0
    assert ready_pr.dispatches() == [gate_dispatch(world.head)]
    assert ready_pr.calls.index(gate_dispatch(world.head)) < ready_pr.calls.index(merge_command(world.head))


def test_dispatches_nothing_when_the_gate_already_succeeded(world: World, ready_pr: FakeGitHub) -> None:
    assert arm(world, ready_pr) == 0
    assert ready_pr.dispatches() == []


def test_an_open_thread_refuses_without_dispatching(world: World, ready_pr: FakeGitHub) -> None:
    ready_pr.gate_state = "failure"
    ready_pr.threads = [{"id": "T1", "isResolved": False, "isOutdated": False}]
    assert arm(world, ready_pr) == 2
    assert ready_pr.dispatches() == []
    assert ready_pr.arms() == []


def test_a_failed_gate_dispatch_is_reported_and_arming_proceeds(
    world: World, ready_pr: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    ready_pr.gate_state = "failure"
    ready_pr.dispatch_code = 1
    assert arm(world, ready_pr) == 0
    assert ready_pr.arms() == [merge_command(world.head)]
    assert "dispatch for PR #7 failed; arming continues" in capsys.readouterr().out


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


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out", "action_required"])
def test_refuses_a_visible_failure_of_a_ruleset_required_check(
    world: World, ready_pr: FakeGitHub, capsys: pytest.CaptureFixture[str], conclusion: str
) -> None:
    ready_pr.conclusions["hold"] = conclusion
    err = _refusal(world, ready_pr, capsys)
    assert f"condition 6: required check 'hold' failed with conclusion {conclusion!r}" in err


def test_pending_ruleset_required_check_still_arms(world: World, ready_pr: FakeGitHub) -> None:
    ready_pr.conclusions["hold"] = None
    assert arm(world, ready_pr) == 0
    assert ready_pr.arms() == [merge_command(world.head)]


def test_non_required_failing_check_does_not_refuse_arming(world: World, ready_pr: FakeGitHub) -> None:
    ready_pr.conclusions["flaky-thing"] = "failure"
    assert arm(world, ready_pr) == 0
    assert ready_pr.arms() == [merge_command(world.head)]


@pytest.mark.parametrize("state", ["failure", "error"])
def test_refuses_a_failed_required_status(
    world: World, ready_pr: FakeGitHub, capsys: pytest.CaptureFixture[str], state: str
) -> None:
    ready_pr.statuses["oracle-gate"] = state
    err = _refusal(world, ready_pr, capsys)
    assert f"condition 6: required status 'oracle-gate' failed with state {state!r}" in err


def test_pending_required_status_still_arms(world: World, ready_pr: FakeGitHub) -> None:
    ready_pr.statuses["oracle-gate"] = "pending"
    assert arm(world, ready_pr) == 0
    assert ready_pr.arms() == [merge_command(world.head)]


def test_waiting_oracle_failure_status_refreshes_and_arms(world: World, ready_pr: FakeGitHub) -> None:
    ready_pr.statuses["oracle-gate"] = "failure"
    ready_pr.status_descriptions["oracle-gate"] = (
        f"oracle-review: waiting for the oracle's verdict on {world.head}, or a stand-in attestation"
    )
    ready_pr.gate_state = "failure"
    assert arm(world, ready_pr) == 0
    assert len(ready_pr.dispatches()) == 1
    assert ready_pr.arms() == [merge_command(world.head)]


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


def test_arm_refuses_a_pr_already_armed_at_another_head(
    tmp_path: Path, world: World, ready_pr: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    assert arm(world, ready_pr) == 0
    revised = world.push_pr_commit("docs/second.md")
    ready_pr.set_head(revised)
    assert store(tmp_path, ready_pr, review(world, head=revised), revised) == 0
    assert arm(world, ready_pr, head=revised) == 2
    assert "the PR was already armed; withdraw it with make pr-landing-remote-withdraw" in capsys.readouterr().err
    assert ready_pr.arms() == [merge_command(world.head)]


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


@pytest.mark.parametrize(("item", "skipped"), [(None, True), ("cloud-item", False)])
def test_bind_says_when_the_batch_member_check_will_be_skipped(
    world: World, capsys: pytest.CaptureFixture[str], item: str | None, skipped: bool
) -> None:
    assert remote_closeout.bind("7", BRANCH, item=item, run=FakeGitHub(world)) == 0
    assert ("batch-member check will be skipped" in capsys.readouterr().out) is skipped


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


def _make_env(tmp_path: Path, target: str, *assignments: str) -> dict[str, str]:
    shim = tmp_path / "uv"
    shim.write_text(
        '#!/bin/sh\necho "$@" > "$RECORD_DIR/argv"\n'
        'env | grep -E "^(REMOTE_CLOSEOUT_|PYTHONDONTWRITEBYTECODE=)" | sort > "$RECORD_DIR/env"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    names = {
        "PR",
        "HEAD",
        "REPO",
        "BRANCH",
        "ITEM",
        "RECORD",
        "BUILDER_HEAD",
        "OWNER_EXPLAINED",
        "MODEL",
        "DRY_RUN",
        "RESUME",
    }
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
    pairs = [line.split("=", 1) for line in (tmp_path / "env").read_text(encoding="utf-8").splitlines()]
    return dict(pairs)


@skip_on_windows
def test_the_make_wrapper_hands_values_over_untouched_and_runs_no_shell_metacharacters(tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    statement = f'Owner said "arm it"; touch {marker} `touch {marker}` $(touch {marker})'
    env = _make_env(tmp_path, "pr-arm-remote", f"PR=7; touch {marker}", f"OWNER_EXPLAINED={statement}")
    assert env["REMOTE_CLOSEOUT_PR"] == f"7; touch {marker}"
    assert env["REMOTE_CLOSEOUT_OWNER_EXPLAINED"] == statement
    assert not marker.exists()


@skip_on_windows
def test_the_review_target_hands_its_options_to_the_review_script(tmp_path: Path) -> None:
    options = ("PR=7", f"HEAD={OTHER_SHA}", "MODEL=gpt-x", "REVIEWER=claude", "DRY_RUN=1", "RESUME=1")
    env = _make_env(tmp_path, "pr-landing-remote-review", *options)
    argv = (tmp_path / "argv").read_text(encoding="utf-8").split()
    assert argv == ["run", "--no-project", "--", "python", "scripts/remote_review.py"]
    assert env["REMOTE_CLOSEOUT_HEAD"] == OTHER_SHA
    assert env["REMOTE_CLOSEOUT_MODEL"] == "gpt-x"
    assert env["REMOTE_CLOSEOUT_REVIEWER"] == "claude"
    assert env["REMOTE_CLOSEOUT_DRY_RUN"] == env["REMOTE_CLOSEOUT_RESUME"] == "1"
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"


FAKE_CODEX = """
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

STATE = Path(__STATE__)
LOCK = Path(__LOCK__)
args = sys.argv[1:]
if args == ["--version"]:
    print("codex-cli 9.9.9")
    raise SystemExit(0)
snapshot = STATE / f"call-{len(list(STATE.glob('call-*')))}"
shutil.copytree(Path(args[args.index("-C") + 1]).parent, snapshot, symlinks=True)
(snapshot / "argv.json").write_text(json.dumps(args))
(snapshot / "env.json").write_text(json.dumps(dict(os.environ)))
lock_free = True
if LOCK.exists():
    with LOCK.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_free = False
(snapshot / "lock_free.json").write_text(json.dumps(lock_free))
behaviour = json.loads((STATE / "next.json").read_text())
if "refresh" in behaviour:
    Path(os.environ["CODEX_HOME"], "auth.json").write_text(behaviour["refresh"])
if behaviour.get("hang"):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    (STATE / "child.pid").write_text(str(child.pid))
    time.sleep(60)
print(json.dumps({"type": "thread.started", "thread_id": "thread-0042"}))
if "raw_fail" in behaviour:
    print(json.dumps({"type": "item.completed", "item": {"text": behaviour["raw_fail"]}}))
    raise SystemExit(1)
if "fail" in behaviour:
    print(json.dumps({"type": "error", "message": behaviour["fail"]}))
    raise SystemExit(1)
Path(args[args.index("-o") + 1]).write_text(behaviour["output"])
"""
ORIGINAL_LOGIN = '{"tokens": "original"}'
PRODUCTION_DENIED_ROOTS = remote_review.DENIED_ROOTS


class FakeCodex:
    def __init__(self, state: Path, home: Path) -> None:
        self.state = state
        self.home = home

    def behave(self, **behaviour: object) -> None:
        (self.state / "next.json").write_text(json.dumps(behaviour), encoding="utf-8")

    def respond(self, findings: Sequence[dict] = (), previous: Sequence[dict] = (), **behaviour: object) -> None:
        output = json.dumps({"findings": list(findings), "previous_findings": list(previous)})
        self.behave(output=output, **behaviour)

    def runs(self) -> list[Path]:
        return sorted(self.state.glob("call-*"), key=lambda path: int(path.name.removeprefix("call-")))

    def read(self, run: Path, name: str) -> Any:
        return json.loads((run / name).read_text(encoding="utf-8"))

    def argv(self, run: Path) -> list[str]:
        return self.read(run, "argv.json")


@pytest.fixture
def denied_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    roots = (str(tmp_path / "denied-tmp"), str(tmp_path / "denied-var-tmp"))
    monkeypatch.setattr(remote_review, "DENIED_ROOTS", roots)
    monkeypatch.setenv(remote_review.RUNS_ENV, str(tmp_path / "review-runs"))
    return roots


@pytest.fixture
def codex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, denied_roots: tuple[str, str]) -> FakeCodex:
    bin_dir, state, home = tmp_path / "bin", tmp_path / "codex-state", tmp_path / "codex-home"
    for directory in (bin_dir, state, home):
        directory.mkdir()
    (home / "auth.json").write_text(ORIGINAL_LOGIN, encoding="utf-8")
    lock = remote_closeout.store_path(REPO, 7).with_name("pr-7.json.lock")
    script = FAKE_CODEX.replace("__STATE__", repr(str(state))).replace("__LOCK__", repr(str(lock)))
    (bin_dir / "codex").write_text(f"#!{sys.executable}\n{script}", encoding="utf-8")
    (bin_dir / "codex").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("CODEX_HOME", str(home))
    fake = FakeCodex(state, home)
    fake.respond()
    return fake


FAKE_CLAUDE = """
import json
import os
import shutil
import sys
from pathlib import Path

STATE = Path(__STATE__)
args = sys.argv[1:]
behaviour = json.loads((STATE / "next.json").read_text())
if args == ["--help"]:
    print(behaviour.get("help", "--safe-mode --restricted --tools --allowedTools --permission-mode "
          "--permission-prompts --setting-sources --strict-mcp-config --mcp-config --disable-slash-commands "
          "--no-session-persistence --no-chrome --add-dir --output-format --json-schema"))
    raise SystemExit(0)
if args == ["--version"]:
    print("2.1.302 (Claude Code)")
    raise SystemExit(0)
snapshot = STATE / f"call-{len(list(STATE.glob('call-*')))}"
shutil.copytree(Path.cwd().parent, snapshot, symlinks=True)
(snapshot / "argv.json").write_text(json.dumps(args))
(snapshot / "env.json").write_text(json.dumps(dict(os.environ)))
(snapshot / "cwd.json").write_text(json.dumps(str(Path.cwd())))
if "fail" in behaviour:
    print(json.dumps({"is_error": True, "result": behaviour["fail"]}))
    raise SystemExit(1)
print(json.dumps({
    "is_error": False,
    "session_id": "claude-session-42",
    "structured_output": json.loads(behaviour["output"]),
    "modelUsage": {"claude-sonnet-4-6": {"inputTokens": 100}},
}))
"""


@pytest.fixture
def claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, denied_roots: tuple[str, str]) -> FakeCodex:
    bin_dir, state, home = tmp_path / "claude-bin", tmp_path / "claude-state", tmp_path / "claude-config"
    for directory in (bin_dir, state, home):
        directory.mkdir()
    (home / ".credentials.json").write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "claude-test-login",
                    "refreshToken": "claude-test-refresh",
                    "scopes": ["user:inference"],
                },
                "mcpOAuth": {"secret": "excluded"},
            }
        ),
        encoding="utf-8",
    )
    (bin_dir / "claude").write_text(
        f"#!{sys.executable}\n{FAKE_CLAUDE.replace('__STATE__', repr(str(state)))}", encoding="utf-8"
    )
    (bin_dir / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    fake = FakeCodex(state, home)
    fake.respond()
    return fake


@pytest.fixture
def bound(world: World) -> FakeGitHub:
    gh = FakeGitHub(world)
    assert remote_closeout.bind("7", BRANCH, run=gh) == 0
    return gh


def finding(location: str, severity: str, reasoning: str = "the note contradicts the spec") -> dict:
    return {"location": location, "reasoning": reasoning, "severity": severity}


def run_review(world: World, gh: FakeGitHub, head: str | None = None, **kwargs) -> int:
    head = head or world.head
    options = {"model": "gpt-test", **kwargs}
    builder_head = None if options.get("dry_run") else head
    return remote_review.review("7", head, builder_head, run=gh, checkout=world.clone, **options)


def commit_pr_files(world: World, files: Mapping[str, str], links: Mapping[str, str] | None = None) -> str:
    git(world.seed, "switch", "--quiet", BRANCH)
    for path, text in files.items():
        (world.seed / path).parent.mkdir(parents=True, exist_ok=True)
        (world.seed / path).write_text(text, encoding="utf-8")
    for path, target in (links or {}).items():
        os.symlink(target, world.seed / path)
    git(world.seed, "add", ".")
    git(world.seed, "commit", "--quiet", "-m", "revise")
    git(world.seed, "push", "--quiet", "--force", "origin", f"{BRANCH}:refs/pull/7/head")
    return git(world.seed, "rev-parse", "HEAD")


RECORD_FIELDS = tuple(remote_closeout.RECORD_CHECKS)


def test_review_stores_a_record_with_a_computed_disposition_and_publishes_its_findings(
    world: World, bound: FakeGitHub, codex: FakeCodex
) -> None:
    findings = [
        finding("docs/note.md:1", "Required", "the note is wrong"),
        finding("README.md:1", "Critical", "the readme claims too much"),
        finding("docs/note.md", "Nit", "tone"),
    ]
    codex.respond(findings)
    assert run_review(world, bound) == 0
    [entry] = stored(world)["records"]
    assert {field: entry[field] for field in RECORD_FIELDS} == {
        "pr_number": 7,
        "head_branch": BRANCH,
        "pr_node_id": NODE_ID,
        "base_sha": world.base,
        "head_sha": world.head,
        "manifest_digest": world.digest(world.base, world.head),
        "findings": findings,
        "disposition": "changes-requested",
        "reviewer": {"model": "gpt-test", "run_id": "openai:thread-0042"},
    }
    assert (entry["builder_head"], entry["published"]) == (world.head, True)
    assert remote_closeout.record_problems({field: entry[field] for field in RECORD_FIELDS}) == []
    [posted] = bound.reviews
    assert (posted["event"], posted["commit_id"]) == ("COMMENT", world.head)
    assert posted["comments"] == [
        {
            "path": "docs/note.md",
            "line": 1,
            "side": "RIGHT",
            "body": "Independent reviewer, Required: the note is wrong",
        }
    ]
    marker, *body = posted["body"].split("\n")
    assert re.fullmatch(r"<!-- benchbox-remote-review [0-9a-f]{32} -->", marker)
    assert body == [
        f"Findings from the independent reviewer at {world.head}.",
        "",
        "Critical and Required findings outside the changed lines:",
        "- Critical, `README.md:1`: the readme claims too much",
        "",
        "Nits:",
        "- Nit, `docs/note.md`: tone",
    ]
    assert "gpt-test" not in json.dumps(posted)
    assert [(f["id"], f["location"], f["thread_id"], f["status"]) for f in stored(world)["review_findings"]] == [
        ("F1", "docs/note.md:1", "RT1", "open"),
        ("F2", "README.md:1", None, "open"),
    ]
    assert [thread["id"] for thread in entry["observation"]["threads"]] == ["RT1", "T1"]


@pytest.mark.parametrize(
    ("findings", "disposition", "published"),
    [
        ([], "approve", False),
        ([finding("docs/note.md:1", "Nit")], "approve", True),
        ([finding("docs/note.md:1", "Required")], "changes-requested", True),
        ([finding("docs/note.md", "Critical")], "changes-requested", True),
    ],
)
def test_the_disposition_approves_only_without_critical_or_required_findings(
    world: World, bound: FakeGitHub, codex: FakeCodex, findings: list, disposition: str, published: bool
) -> None:
    codex.respond(findings)
    assert run_review(world, bound) == 0
    assert stored(world)["records"][-1]["disposition"] == disposition
    assert bool(bound.reviews) is published


def test_the_reviewer_reads_the_head_tree_and_base_rules_exported_outside_the_repository(
    tmp_path: Path, codex: FakeCodex
) -> None:
    world = build_world(tmp_path, base_files={"AGENTS.md": "base rules\n", "docs/agent/review.md": "base doc\n"})
    head = world.push_pr_commit("AGENTS.md")
    gh = FakeGitHub(world)
    gh.set_head(head)
    gh.items["cloud-item"] = {"description": "Add a note", "acceptance": ["The note exists"], "context": "chat"}
    assert remote_closeout.bind("7", BRANCH, item="cloud-item", run=gh) == 0
    assert run_review(world, gh, head) == 0
    [run] = codex.runs()
    argv = codex.argv(run)
    workdir = Path(argv[argv.index("-C") + 1]).parent
    assert Path(argv[argv.index("-C") + 1]) == workdir / "input"
    assert not workdir.exists()
    assert not workdir.is_relative_to(world.clone) and not workdir.is_relative_to(ROOT)
    tree, inputs = run / "head", run / "input"
    assert sorted(path.relative_to(tree).as_posix() for path in tree.rglob("*") if path.is_file()) == [
        "AGENTS.md",
        "README.md",
        "docs/agent/review.md",
        "docs/note.md",
    ]
    assert (tree / "AGENTS.md").read_text(encoding="utf-8") == "AGENTS.md revision\n"
    assert (inputs / "rules" / "AGENTS.md").read_text(encoding="utf-8") == "base rules\n"
    assert (inputs / "rules" / "docs" / "agent" / "review.md").read_text(encoding="utf-8") == "base doc\n"
    assert (inputs / "diff.patch").read_text(encoding="utf-8") == git(
        world.seed, "diff", "--no-renames", world.base, head
    ) + "\n"
    assert hashlib.sha256((inputs / "manifest.txt").read_bytes()).hexdigest() == world.digest(world.base, head)
    assert codex.read(inputs, "tracker_item.json") == {
        "id": "cloud-item",
        "description": "Add a note",
        "acceptance": ["The note exists"],
    }
    assert codex.read(inputs, "pull_request.json")["title"] == "Add a cloud note"
    assert codex.read(inputs, "previous_findings.json") == []
    schema = codex.read(run / "run", "schema.json")
    assert schema["properties"]["findings"]["items"]["properties"]["severity"]["enum"] == [
        "Critical",
        "Required",
        "Nit",
    ]
    prompt = argv[-1]
    for expected in ("pull request #7", world.base, head, str(workdir / "head"), str(workdir / "input"), "rules/"):
        assert expected in prompt


def test_the_reviewer_runs_isolated_from_user_config_secrets_and_the_network(
    tmp_path: Path, world: World, bound: FakeGitHub, codex: FakeCodex, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GH_TOKEN", "gh-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    (codex.home / "config.toml").write_text('model = "gpt-test"\n[mcp_servers.todo]\ncommand = "todo"\n')
    assert run_review(world, bound) == 0
    [run] = codex.runs()
    argv = codex.argv(run)
    workdir = Path(argv[argv.index("-C") + 1]).parent
    env = codex.read(run, "env.json")
    assert set(env) - {"__CF_USER_TEXT_ENCODING"} == {"PATH", "HOME", "CODEX_HOME", "LANG"}
    assert (env["HOME"], env["CODEX_HOME"]) == (str(workdir / "home"), str(workdir / "home" / ".codex"))
    assert sorted(path.name for path in (run / "home" / ".codex").iterdir()) == ["auth.json"]
    assert (run / "home" / ".codex" / "auth.json").read_text(encoding="utf-8") == ORIGINAL_LOGIN
    assert argv[:7] == [
        "exec",
        "--ignore-user-config",
        "--ignore-rules",
        "--ephemeral",
        "--skip-git-repo-check",
        "--json",
        "-C",
    ]
    assert "-s" not in argv and "--sandbox" not in argv
    settings = [argv[index + 1] for index, value in enumerate(argv) if value == "-c"]
    profile = remote_review.PERMISSION_PROFILE
    readable = {
        ":minimal": "read",
        str(workdir / "input"): "read",
        str(workdir / "head"): "read",
        **dict.fromkeys(remote_review.DENIED_ROOTS, "none"),
    }
    filesystem = json.dumps(readable)
    assert settings == [
        f'default_permissions="{profile}"',
        f"permissions.{profile}.filesystem={filesystem.replace(': ', '=').replace(', ', ',')}",
        f"permissions.{profile}.network={{enabled=false}}",
        'web_search="disabled"',
        "mcp_servers={}",
    ]
    disabled = {argv[index + 1] for index, value in enumerate(argv) if value == "--disable"}
    assert {"apps", "plugins", "hooks", "memories", "computer_use", "browser_use"} <= disabled
    assert PRODUCTION_DENIED_ROOTS == ("/tmp", "/var/tmp")
    assert workdir.parent == (tmp_path / "review-runs").resolve()
    assert not remote_review._denied(workdir)


def test_the_review_directory_moves_out_of_a_denied_temporary_directory_into_a_private_home_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, denied_roots: tuple[str, str]
) -> None:
    monkeypatch.delenv(remote_review.RUNS_ENV)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    allowed = tmp_path / "allowed-tmp"
    allowed.mkdir()
    monkeypatch.setattr(remote_review.tempfile, "gettempdir", lambda: str(allowed))
    assert remote_review.runs_root() == allowed.resolve()
    monkeypatch.setattr(remote_review.tempfile, "gettempdir", lambda: str(Path(denied_roots[0], "T")))
    root = remote_review.runs_root()
    assert root == (tmp_path / "home" / ".benchbox" / "review-runs").resolve()
    assert root.stat().st_mode & 0o777 == 0o700
    monkeypatch.setenv(remote_review.RUNS_ENV, str(Path(denied_roots[1], "runs")))
    with pytest.raises(remote_review.Refused, match="which the sandbox denies"):
        remote_review.runs_root()


@pytest.mark.parametrize(
    ("refreshed", "symlinked", "kept"),
    [
        ('{"tokens": "rotated"}', False, '{"tokens": "rotated"}'),
        ("not json", False, ORIGINAL_LOGIN),
        ('{"tokens": "rotated"}', True, ORIGINAL_LOGIN),
    ],
)
def test_a_login_codex_refreshes_is_kept_only_when_it_is_valid_and_safe_to_replace(
    tmp_path: Path,
    world: World,
    bound: FakeGitHub,
    codex: FakeCodex,
    capsys: pytest.CaptureFixture[str],
    refreshed: str,
    symlinked: bool,
    kept: str,
) -> None:
    if symlinked:
        target = tmp_path / "real-auth.json"
        (codex.home / "auth.json").rename(target)
        (codex.home / "auth.json").symlink_to(target)
    codex.respond(refresh=refreshed)
    assert run_review(world, bound, dry_run="1") == 0
    assert (codex.home / "auth.json").is_symlink() is symlinked
    assert (codex.home / "auth.json").read_text(encoding="utf-8") == kept
    assert [path.name for path in codex.home.iterdir() if path.name.endswith(".tmp")] == []
    assert ("was not copied back" in capsys.readouterr().err) is (kept == ORIGINAL_LOGIN)


def test_the_head_tree_ignores_export_attributes_and_skips_symlinks_that_leave_it(
    world: World, bound: FakeGitHub, codex: FakeCodex
) -> None:
    files = {
        ".gitattributes": "hidden.txt export-ignore\nsubst.txt export-subst\n",
        "hidden.txt": "kept\n",
        "subst.txt": "$Format:%H$\n",
    }
    links = {"inside": "README.md", "escape": "../../outside", "absolute": "/etc/hosts"}
    head = commit_pr_files(world, files, links)
    bound.set_head(head)
    assert run_review(world, bound, head, dry_run="1") == 0
    [run] = codex.runs()
    tree = run / "head"
    assert (tree / "hidden.txt").read_text(encoding="utf-8") == "kept\n"
    assert (tree / "subst.txt").read_text(encoding="utf-8") == "$Format:%H$\n"
    assert os.readlink(tree / "inside") == "README.md"
    assert not (tree / "escape").is_symlink() and not (tree / "absolute").is_symlink()
    assert (run / "input" / "skipped_entries.txt").read_text(encoding="utf-8").splitlines() == [
        "absolute: symlink to /etc/hosts, which points outside the tree",
        "escape: symlink to ../../outside, which points outside the tree",
    ]


def test_review_changes_no_working_tree_file_branch_or_git_config(
    world: World, bound: FakeGitHub, codex: FakeCodex
) -> None:
    codex.respond([finding("docs/note.md:1", "Required")])
    before = local_state(world.clone)
    assert run_review(world, bound) == 0
    assert local_state(world.clone) == before


def test_a_dry_run_prints_the_record_and_creates_publishes_and_stores_nothing(
    world: World, bound: FakeGitHub, codex: FakeCodex, capsys: pytest.CaptureFixture[str]
) -> None:
    store = remote_closeout.store_path(REPO, 7)
    store.with_name("pr-7.json.lock").unlink()
    before = store.read_text(encoding="utf-8")
    codex.respond([finding("docs/note.md:1", "Required")])
    capsys.readouterr()
    assert run_review(world, bound, dry_run="1") == 0
    printed = json.loads(capsys.readouterr().out)
    assert (printed["disposition"], printed["head_sha"]) == ("changes-requested", world.head)
    assert remote_closeout.record_problems(printed) == []
    assert bound.reviews == []
    assert sorted(path.name for path in store.parent.iterdir()) == ["pr-7.json"]
    assert store.read_text(encoding="utf-8") == before


def _review_refusal(world: World, gh: FakeGitHub, capsys: pytest.CaptureFixture[str], code: int = 2, **kwargs) -> str:
    assert run_review(world, gh, **kwargs) == code
    assert gh.reviews == []
    assert gh.resolved == []
    path = remote_closeout.store_path(REPO, 7)
    assert not path.exists() or stored(world)["records"] == []
    return capsys.readouterr().err


def test_review_refuses_an_unbound_pr_before_running_the_reviewer(
    world: World, codex: FakeCodex, capsys: pytest.CaptureFixture[str]
) -> None:
    err = _review_refusal(world, FakeGitHub(world), capsys)
    assert "no binding is recorded for PR #7; bind it before reviewing" in err
    assert codex.runs() == []


def test_review_refuses_a_head_that_is_not_the_live_head(
    world: World, bound: FakeGitHub, codex: FakeCodex, capsys: pytest.CaptureFixture[str]
) -> None:
    err = _review_refusal(world, bound, capsys, head=world.base)
    assert f"the live PR head {world.head[:12]} is not HEAD {world.base[:12]}" in err
    assert codex.runs() == []


def test_review_stops_when_the_fetched_head_is_not_the_head_github_reports(
    world: World, bound: FakeGitHub, codex: FakeCodex, capsys: pytest.CaptureFixture[str]
) -> None:
    bound.reported_head = OTHER_SHA
    assert f"but GitHub reports {OTHER_SHA[:12]}; stopping" in _review_refusal(world, bound, capsys)
    assert codex.runs() == []


def test_review_refuses_when_the_head_moves_while_the_reviewer_runs(
    world: World, bound: FakeGitHub, codex: FakeCodex, capsys: pytest.CaptureFixture[str]
) -> None:
    real = remote_review._run_reviewer

    def moving(*args, **kwargs):
        outcome = real(*args, **kwargs)
        bound.set_head(OTHER_SHA)
        return outcome

    codex.respond([finding("docs/note.md:1", "Required")])
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(remote_review, "_run_reviewer", moving)
        err = _review_refusal(world, bound, capsys)
    assert f"the live PR head {OTHER_SHA[:12]} is not HEAD {world.head[:12]}" in err


def test_review_fails_closed_when_the_bound_item_cannot_be_read(
    world: World, codex: FakeCodex, capsys: pytest.CaptureFixture[str]
) -> None:
    gh = FakeGitHub(world)
    assert remote_closeout.bind("7", BRANCH, item="missing-item", run=gh) == 0
    err = _review_refusal(world, gh, capsys, code=1)
    assert "cannot read the tracker's items/missing-item.json" in err
    assert codex.runs() == []


def test_review_refuses_without_builder_head_before_any_command(
    world: World, bound: FakeGitHub, codex: FakeCodex, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = len(bound.calls)
    assert remote_review.review("7", world.head, None, model="gpt-test", run=bound) == 2
    assert "BUILDER_HEAD must be a full lowercase commit SHA" in capsys.readouterr().err
    assert len(bound.calls) == calls
    assert codex.runs() == []


@pytest.mark.parametrize("message", ["You've hit your usage limit. Try again at 12:23 PM.", "USAGE LIMIT reached"])
def test_review_falls_back_to_claude_only_on_a_codex_usage_limit(
    world: World,
    bound: FakeGitHub,
    codex: FakeCodex,
    claude: FakeCodex,
    message: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    codex.behave(fail=message)
    claude.respond([finding("docs/note.md:1", "Required")])
    assert run_review(world, bound) == 0
    record = stored(world)["records"][-1]
    assert record["reviewer"] == {"model": "claude-sonnet-4-6", "run_id": "anthropic:claude-session-42"}
    assert record["disposition"] == "changes-requested"
    assert record["published"] is True
    assert len(codex.runs()) == len(claude.runs()) == len(bound.reviews) == 1
    assert "Codex usage limit reached" in capsys.readouterr().err
    argv = claude.argv(claude.runs()[0])
    assert argv[argv.index("--model") + 1] == "sonnet"


def test_review_refuses_an_unstructured_codex_failure_mentioning_a_usage_limit(
    world: World,
    bound: FakeGitHub,
    codex: FakeCodex,
    claude: FakeCodex,
    capsys: pytest.CaptureFixture[str],
) -> None:
    codex.behave(raw_fail="the diff mentions a usage limit")
    refusal = _review_refusal(world, bound, capsys)
    assert "codex exec failed with exit code 1" in refusal
    assert claude.runs() == []
    assert stored(world)["records"] == []


@pytest.mark.parametrize("message", ["authentication failed", "network failure", "permission denied", "quota exceeded"])
def test_review_reports_other_codex_failures_without_falling_back(
    world: World,
    bound: FakeGitHub,
    codex: FakeCodex,
    claude: FakeCodex,
    message: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    codex.behave(fail=message)
    assert f"codex exec failed with exit code 1: {message}" in _review_refusal(world, bound, capsys)
    assert claude.runs() == []
    assert stored(world)["records"] == []


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("I approve.", "the reviewer's final message is not JSON"),
        (json.dumps({"findings": []}), "is not the object the output schema describes"),
        (
            json.dumps({"findings": [finding("a.py:1", "High")], "previous_findings": []}),
            "a finding without a location, reasoning, and Critical/Required/Nit severity",
        ),
        (
            json.dumps({"findings": [], "previous_findings": [{"id": "F9", "status": "fixed"}]}),
            "the reviewer marked previous findings ['F9'], but the open ones are []",
        ),
    ],
)
def test_review_refuses_reviewer_output_it_cannot_trust(
    world: World, bound: FakeGitHub, codex: FakeCodex, capsys: pytest.CaptureFixture[str], output: str, expected: str
) -> None:
    codex.behave(output=output)
    assert expected in _review_refusal(world, bound, capsys)


@pytest.mark.parametrize(("config", "recorded"), [('model = "gpt-configured"\n', "gpt-configured"), ("", None)])
def test_review_takes_the_default_model_from_the_codex_configuration(
    world: World,
    bound: FakeGitHub,
    codex: FakeCodex,
    capsys: pytest.CaptureFixture[str],
    config: str,
    recorded: str | None,
) -> None:
    (codex.home / "config.toml").write_text(config, encoding="utf-8")
    code = run_review(world, bound, model=None)
    if recorded is None:
        assert code == 2
        assert "MODEL is required" in capsys.readouterr().err
        assert codex.runs() == []
        return
    assert code == 0
    assert stored(world)["records"][-1]["reviewer"]["model"] == recorded
    [run] = codex.runs()
    assert codex.argv(run)[codex.argv(run).index("-m") + 1] == recorded


def test_a_re_review_resolves_only_the_threads_of_findings_marked_fixed(
    world: World, bound: FakeGitHub, codex: FakeCodex, capsys: pytest.CaptureFixture[str]
) -> None:
    first, second = finding("docs/note.md:1", "Required", "first"), finding("docs/note.md:1", "Critical", "second")
    codex.respond([first, second])
    assert run_review(world, bound) == 0
    assert [thread["id"] for thread in bound.threads] == ["T1", "RT1", "RT2"]

    revised = world.push_pr_commit("docs/other.md")
    bound.set_head(revised)
    codex.respond([], previous=[{"id": "F1", "status": "fixed"}, {"id": "F2", "status": "open"}])
    assert run_review(world, bound, revised) == 0
    shown = codex.read(codex.runs()[-1] / "input", "previous_findings.json")
    assert [(entry["id"], entry["reasoning"], entry["head_sha"]) for entry in shown] == [
        ("F1", "first", world.head),
        ("F2", "second", world.head),
    ]
    assert bound.resolved == ["RT1"]
    assert len(bound.reviews) == 1
    latest = stored(world)["records"][-1]
    assert (latest["head_sha"], latest["disposition"], latest["findings"]) == (revised, "changes-requested", [second])

    codex.respond([], previous=[{"id": "F1", "status": "open"}])
    assert run_review(world, bound, revised) == 2
    assert "the reviewer marked previous findings ['F1'], but the open ones are ['F2']" in capsys.readouterr().err
    assert bound.resolved == ["RT1"]

    codex.respond([], previous=[{"id": "F2", "status": "fixed"}])
    assert run_review(world, bound, revised) == 0
    assert bound.resolved == ["RT1", "RT2"]
    assert stored(world)["records"][-1]["disposition"] == "approve"
    assert [(entry["status"], entry["fixed_at_head"]) for entry in stored(world)["review_findings"]] == [
        ("fixed", revised),
        ("fixed", revised),
    ]


def test_a_body_only_blocking_finding_marked_fixed_resolves_no_thread(
    world: World, bound: FakeGitHub, codex: FakeCodex
) -> None:
    codex.respond([finding("README.md:1", "Required")])
    assert run_review(world, bound) == 0
    codex.respond([], previous=[{"id": "F1", "status": "fixed"}])
    assert run_review(world, bound) == 0
    assert bound.resolved == []
    assert stored(world)["records"][-1]["disposition"] == "approve"


def test_a_failed_publication_leaves_the_new_record_latest_until_a_rerun_publishes_it(
    world: World, bound: FakeGitHub, codex: FakeCodex, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_review(world, bound) == 0
    assert stored(world)["records"][-1]["disposition"] == "approve"
    codex.respond([finding("README.md:1", "Critical"), finding("docs/note.md:1", "Required")])
    bound.post_failures = ["error"]
    assert run_review(world, bound) == 1
    latest = stored(world)["records"][-1]
    assert (latest["disposition"], latest["published"]) == ("changes-requested", False)
    assert bound.reviews == []
    assert "the latest record's findings are not all published" in _refusal(world, bound, capsys)

    codex.respond([], previous=[{"id": "F1", "status": "open"}, {"id": "F2", "status": "open"}])
    assert run_review(world, bound) == 0
    assert len(bound.reviews) == 1
    state = stored(world)
    assert [record["published"] for record in state["records"]] == [True, True, True]
    assert [(entry["id"], entry["thread_id"]) for entry in state["review_findings"]] == [("F1", None), ("F2", "RT1")]
    assert state["records"][-1]["disposition"] == "changes-requested"


def test_a_review_github_kept_but_did_not_acknowledge_is_not_posted_twice(
    world: World, bound: FakeGitHub, codex: FakeCodex
) -> None:
    codex.respond([finding("docs/note.md:1", "Required")])
    bound.post_failures = ["lost"]
    assert run_review(world, bound) == 1
    assert len(bound.reviews) == 1
    codex.respond([], previous=[{"id": "F1", "status": "open"}])
    assert run_review(world, bound) == 0
    assert (len(bound.reviews), len(bound.post_attempts)) == (1, 1)
    assert stored(world)["review_findings"][0]["thread_id"] == "RT1"


def test_inline_comments_github_rejects_move_into_the_review_body(
    world: World, bound: FakeGitHub, codex: FakeCodex
) -> None:
    codex.respond([finding("docs/note.md:1", "Required", "the note is wrong")])
    bound.post_failures = ["422"]
    assert run_review(world, bound) == 0
    assert len(bound.post_attempts) == 2
    [posted] = bound.reviews
    assert posted["comments"] == []
    assert posted["body"].split("\n")[-2:] == [
        "Critical and Required findings GitHub would not place on the diff:",
        "- Required, `docs/note.md:1`: the note is wrong",
    ]
    assert stored(world)["review_findings"][0]["thread_id"] is None
    assert stored(world)["records"][-1]["published"] is True


def test_record_refuses_an_approve_record_while_blocking_findings_are_open(
    tmp_path: Path, world: World, bound: FakeGitHub, codex: FakeCodex, capsys: pytest.CaptureFixture[str]
) -> None:
    codex.respond([finding("docs/note.md:1", "Required")])
    assert run_review(world, bound) == 0
    capsys.readouterr()
    assert store(tmp_path, bound, review(world), world.head) == 2
    assert "an approve record cannot be stored while blocking findings F1 are open" in capsys.readouterr().err
    assert len(stored(world)["records"]) == 1


def test_the_reviewer_runs_without_holding_the_review_store_lock(
    world: World, bound: FakeGitHub, codex: FakeCodex
) -> None:
    assert run_review(world, bound) == 0
    [run] = codex.runs()
    assert codex.read(run, "lock_free.json") is True


def test_review_waits_only_a_bounded_time_for_the_review_store_lock(
    world: World,
    bound: FakeGitHub,
    codex: FakeCodex,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import fcntl

    monkeypatch.setattr(remote_review, "LOCK_WAIT", 0.2)
    monkeypatch.setattr(remote_review, "LOCK_ATTEMPTS", 3)
    codex.respond([finding("docs/note.md:1", "Required")])
    with remote_closeout.store_path(REPO, 7).with_name("pr-7.json.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        err = _review_refusal(world, bound, capsys)
    held = "another close-out command held the review store for PR #7 for over 0.2 seconds"
    assert [line for line in err.splitlines() if held in line] == [
        f"{held}; waiting again (1 of 3)",
        f"{held}; waiting again (2 of 3)",
        f"pr-landing-remote-review: refusing: {held}, 3 time(s)",
    ]


def test_a_reviewer_that_hangs_is_killed_with_its_process_group(
    world: World,
    bound: FakeGitHub,
    codex: FakeCodex,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(remote_review, "CODEX_TIMEOUT", 2)
    codex.behave(hang=True)
    assert "codex did not finish within 2 seconds" in _review_refusal(world, bound, capsys)
    child = int((codex.state / "child.pid").read_text(encoding="utf-8"))
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        pytest.fail(f"the reviewer's child process {child} survived the timeout")


def test_resume_finishes_pending_publication_without_running_the_reviewer(
    world: World, bound: FakeGitHub, codex: FakeCodex, capsys: pytest.CaptureFixture[str]
) -> None:
    codex.respond([finding("docs/note.md:1", "Required")])
    bound.post_failures = ["error"]
    assert run_review(world, bound) == 1
    assert stored(world)["records"][-1]["published"] is False
    capsys.readouterr()
    assert remote_review.resume("7", run=bound) == 0
    assert "finished 1 pending publication step(s) for PR #7" in capsys.readouterr().out
    assert len(codex.runs()) == 1
    assert len(bound.reviews) == 1
    assert stored(world)["records"][-1]["published"] is True
    assert stored(world)["review_findings"][0]["thread_id"] == "RT1"
    assert remote_review.resume("7", run=bound, flag="yes") == 2
    assert "RESUME must be 1 or unset" in capsys.readouterr().err


def test_a_deleted_thread_abandons_its_step_and_leaves_the_record_unpublished(
    world: World, bound: FakeGitHub, codex: FakeCodex, capsys: pytest.CaptureFixture[str]
) -> None:
    codex.respond([finding("docs/note.md:1", "Required")])
    assert run_review(world, bound) == 0
    bound.deleted.add("RT1")
    codex.respond([], previous=[{"id": "F1", "status": "fixed"}])
    assert run_review(world, bound) == 2
    err = capsys.readouterr().err
    assert "abandoned a publication step of record 1: review thread RT1 no longer exists" in err
    assert "the latest record for PR #7 is stored but unpublished, so make pr-arm-remote refuses it" in err
    state = stored(world)
    assert (state["records"][-1]["disposition"], state["records"][-1]["published"]) == ("approve", False)
    assert state["review_runs"][-1]["steps"][0]["status"] == "abandoned"
    assert "the latest record's findings are not all published" in _refusal(world, bound, capsys)
    assert remote_review.resume("7", run=bound) == 2
    codex.respond()
    assert run_review(world, bound) == 0
    assert stored(world)["records"][-1]["published"] is True


def test_a_review_github_refuses_even_without_inline_comments_is_abandoned(
    world: World, bound: FakeGitHub, codex: FakeCodex, capsys: pytest.CaptureFixture[str]
) -> None:
    codex.respond([finding("docs/note.md:1", "Required")])
    bound.post_failures = ["422", "422-always"]
    assert run_review(world, bound) == 2
    assert "GitHub refuses the review for PR #7 even without inline comments" in capsys.readouterr().err
    assert len(bound.post_attempts) == 2
    assert bound.reviews == []
    assert stored(world)["records"][-1]["published"] is False


def test_review_refuses_to_store_when_the_pr_was_armed_while_the_reviewer_ran(
    world: World, bound: FakeGitHub, codex: FakeCodex, capsys: pytest.CaptureFixture[str]
) -> None:
    real = remote_review._run_reviewer

    def armed_meanwhile(*args, **kwargs):
        outcome = real(*args, **kwargs)
        path = remote_closeout.store_path(REPO, 7)
        state = json.loads(path.read_text(encoding="utf-8"))
        state["arms"].append({"head": world.head, "via": "arm"})
        path.write_text(json.dumps(state), encoding="utf-8")
        return outcome

    codex.respond([finding("docs/note.md:1", "Required")])
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(remote_review, "_run_reviewer", armed_meanwhile)
        err = _review_refusal(world, bound, capsys)
    assert "PR #7 was armed while the reviewer ran; withdraw it with make pr-landing-remote-withdraw" in err


def test_claude_is_an_explicit_backend_with_the_same_record_and_publication_pipeline(
    world: World, bound: FakeGitHub, codex: FakeCodex, claude: FakeCodex
) -> None:
    claude.respond([finding("docs/note.md:1", "Required")])
    assert run_review(world, bound, reviewer="claude", model=None) == 0
    state = stored(world)
    assert state["records"][-1]["reviewer"] == {
        "model": "claude-sonnet-4-6",
        "run_id": "anthropic:claude-session-42",
    }
    assert state["records"][-1]["disposition"] == "changes-requested"
    assert state["review_runs"][-1]["reviewer_version"] == "2.1.302 (Claude Code)"
    assert len(bound.reviews) == 1
    assert codex.runs() == []
    claude.respond([], previous=[{"id": "F1", "status": "fixed"}])
    assert run_review(world, bound, reviewer="claude", model=None) == 0
    assert stored(world)["records"][-1]["disposition"] == "approve"
    assert bound.resolved == ["RT1"]


def test_claude_is_confined_to_read_only_tools_and_an_isolated_login(
    world: World, bound: FakeGitHub, claude: FakeCodex, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GH_TOKEN", "gh-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "api-secret")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-secret")
    (claude.home / "settings.json").write_text('{"hooks": {"SessionStart": []}}', encoding="utf-8")
    assert run_review(world, bound, reviewer="claude", model="opus") == 0
    [snapshot] = claude.runs()
    argv = claude.argv(snapshot)
    inputs = Path(argv[argv.index("--add-dir") + 1])
    workdir = inputs.parent
    assert claude.read(snapshot, "cwd.json") == str(inputs)
    assert argv == [
        "-p",
        "--safe-mode",
        "--restricted",
        "--tools",
        "Read,Glob,Grep",
        "--allowedTools",
        "Read,Glob,Grep",
        "--permission-mode",
        "dontAsk",
        "--permission-prompts",
        "none",
        "--setting-sources",
        "",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--disable-slash-commands",
        "--no-session-persistence",
        "--no-chrome",
        "--add-dir",
        str(inputs),
        str(workdir / "head"),
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(remote_review.OUTPUT_SCHEMA),
        "--model",
        "opus",
        "--",
        remote_review.review_prompt(workdir, 7, world.base, world.head),
    ]
    env = claude.read(snapshot, "env.json")
    assert set(env) - {"__CF_USER_TEXT_ENCODING"} == {"PATH", "HOME", "LANG"}
    assert env["HOME"] == str(workdir / "claude-home")
    config = snapshot / "claude-home" / ".claude"
    assert [path.name for path in config.iterdir()] == [".credentials.json"]
    assert claude.read(config, ".credentials.json") == {
        "claudeAiOauth": {"accessToken": "claude-test-login", "scopes": ["user:inference"]}
    }
    assert (config / ".credentials.json").stat().st_mode & 0o777 == 0o600
    assert (snapshot / "input" / "diff.patch").is_file()
    assert (snapshot / "head" / "docs" / "note.md").is_file()
    assert not workdir.exists()


def test_claude_refuses_an_expired_login_before_launching(
    world: World, bound: FakeGitHub, claude: FakeCodex, capsys: pytest.CaptureFixture[str]
) -> None:
    (claude.home / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "claude-test-login", "expiresAt": 1}}),
        encoding="utf-8",
    )
    assert "claude login has expired" in _review_refusal(world, bound, capsys, reviewer="claude", model=None)
    assert claude.runs() == []
    assert stored(world)["records"] == []


def test_claude_dry_run_changes_no_store_or_github_state(
    world: World, bound: FakeGitHub, claude: FakeCodex, capsys: pytest.CaptureFixture[str]
) -> None:
    path = remote_closeout.store_path(REPO, 7)
    lock = path.with_name("pr-7.json.lock")
    lock.unlink()
    before = path.read_bytes()
    claude.respond([finding("docs/note.md:1", "Nit")])
    capsys.readouterr()
    assert run_review(world, bound, reviewer="claude", model=None, dry_run="1") == 0
    record = json.loads(capsys.readouterr().out)
    assert record["reviewer"]["run_id"].startswith("anthropic:")
    assert set(record) == set(RECORD_FIELDS)
    assert path.read_bytes() == before
    assert not lock.exists()
    assert list((world.clone.parent / "review-runs").glob("benchbox-remote-review-*")) == []
    assert bound.reviews == []


def test_claude_refuses_when_the_cli_cannot_enforce_isolation(
    world: World, bound: FakeGitHub, claude: FakeCodex, capsys: pytest.CaptureFixture[str]
) -> None:
    claude.respond(help="--tools")
    assert "claude cannot enforce reviewer isolation" in _review_refusal(
        world, bound, capsys, reviewer="claude", model=None
    )
    assert claude.runs() == []
    assert stored(world)["records"] == []


def test_a_claude_failure_never_becomes_a_record_or_another_fallback(
    world: World, bound: FakeGitHub, claude: FakeCodex, capsys: pytest.CaptureFixture[str]
) -> None:
    claude.behave(fail="usage limit")
    assert "claude -p failed with exit code 1: usage limit" in _review_refusal(
        world, bound, capsys, reviewer="claude", model=None
    )
    assert len(claude.runs()) == 1
    assert stored(world)["records"] == []


def test_arming_discloses_a_claude_review_without_a_new_owner_approval_gate(
    world: World, bound: FakeGitHub, claude: FakeCodex, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_review(world, bound, reviewer="claude", model=None) == 0
    capsys.readouterr()
    assert arm(world, bound) == 0
    out = capsys.readouterr().out
    assert "reviewer family: anthropic; model: claude-sonnet-4-6" in out
    assert "less independent when the builder is also Claude-family" in out
    assert stored(world)["arms"][-1]["owner_explained"] is None


def test_claude_publication_can_resume_without_rerunning_the_reviewer(
    world: World, bound: FakeGitHub, claude: FakeCodex
) -> None:
    claude.respond([finding("docs/note.md:1", "Required")])
    bound.post_failures = ["error"]
    assert run_review(world, bound, reviewer="claude", model=None) == 1
    assert stored(world)["records"][-1]["published"] is False
    assert remote_review.resume("7", run=bound) == 0
    assert stored(world)["records"][-1]["published"] is True
    assert len(claude.runs()) == 1


def test_refuses_a_stale_failed_suite_hidden_behind_a_newer_success(
    world: World, ready_pr: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    template = {"name": "oracle-review", "status": "completed", "details_url": "", "check_suite": {"id": 0}}
    ready_pr.stale_suites = [
        {**template, "id": 98, "conclusion": "success", "started_at": "2", "check_suite": {"id": 8}},
        {
            **template,
            "id": 99,
            "conclusion": "failure",
            "started_at": "1",
            "details_url": "https://github.com/o/r/actions/runs/77/job/1",
            "check_suite": {"id": 9},
        },
    ]
    assert arm(world, ready_pr) == 2
    err = capsys.readouterr().err
    assert "condition 6: required check 'oracle-review' suite 9 failure" in err
    assert "actions/runs/77" in err
    assert ready_pr.arms() == []
