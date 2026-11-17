from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts import corpus_drift

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REPO_SLUG = "BenchBox-dev/BenchBox"
NOW = 1_800_000_000.0
HOUR = 3600
BUNDLE = "results-data/bundles/shared.json"
OTHER_BUNDLE = "results-data/bundles/second.json"
NOTES = "results-data/CORPUS_NOTES.md"


def _git(root: Path, *args: str, when: float | None = None) -> str:
    env = dict(os.environ)
    if when is not None:
        stamp = f"{int(when)} +0000"
        env["GIT_COMMITTER_DATE"] = stamp
        env["GIT_AUTHOR_DATE"] = stamp
    return subprocess.run(
        ["git", *args], cwd=root, env=env, check=True, capture_output=True, encoding="utf-8"
    ).stdout.strip()


def _commit(root: Path, files: dict[str, str | None], message: str, hours_ago: float) -> str:
    for rel, content in files.items():
        path = root / rel
        if content is None:
            _git(root, "rm", "-q", "--", rel)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        _git(root, "add", "--", rel)
    _git(root, "commit", "-q", "--allow-empty", "-m", message, when=NOW - hours_ago * HOUR)
    return _git(root, "rev-parse", "HEAD")


class FakeGitHub:
    def __init__(self) -> None:
        self.pulls: list[dict[str, Any]] = []
        self.statuses: dict[str, list[dict[str, str]]] = {}
        self.failure: str | None = None

    def add_pull(self, number: int, head_sha: str, base_sha: str, **overrides: Any) -> None:
        pull: dict[str, Any] = {
            "number": number,
            "title": f"{corpus_drift.MIRROR_TITLE_PREFIX}abcd1234",
            "head": {
                "ref": f"{corpus_drift.MIRROR_BRANCH_PREFIX}abcd1234",
                "sha": head_sha,
                "repo": {"full_name": REPO_SLUG},
            },
            "base": {"ref": corpus_drift.PUBLISHED_BRANCH, "sha": base_sha},
        }
        pull.update(overrides)
        self.pulls.append(pull)

    def set_status(self, head_sha: str, state: str, context: str = corpus_drift.VALIDATE_STATUS_CONTEXT) -> None:
        self.statuses[head_sha] = [{"context": context, "state": state}]

    def __call__(self, path: str) -> Any:
        if self.failure:
            raise corpus_drift.LookupFailure(self.failure)
        if "/pulls?" in path:
            return self.pulls if "page=1" in path else []
        if path.endswith("/status"):
            sha = path.rsplit("/", 2)[1]
            return {"statuses": self.statuses.get(sha, [])}
        raise AssertionError(f"unexpected GitHub path {path}")


class Scenario:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        _git(root, "init", "-q", "-b", "develop")
        _git(root, "config", "user.email", "drift@example.com")
        _git(root, "config", "user.name", "Drift Test")
        self.github = FakeGitHub()
        self.published_tip = ""

    def publish(self, files: dict[str, str | None]) -> str:
        sha = _commit(self.root, files, "published tip", hours_ago=200)
        _git(self.root, "update-ref", "refs/remotes/origin/published-results", sha)
        self.published_tip = sha
        return sha

    def develop(self, files: dict[str, str | None], hours_ago: float) -> str:
        return _commit(self.root, files, "develop change", hours_ago=hours_ago)

    def mirror(
        self,
        files: dict[str, str | None],
        *,
        base: str | None = None,
        validated: str | None = "success",
        number: int = 7,
    ) -> str:
        develop_head = _git(self.root, "rev-parse", "HEAD")
        _git(self.root, "switch", "-q", "--detach", base or self.published_tip)
        head = _commit(self.root, files, "mirror", hours_ago=1)
        _git(self.root, "switch", "-q", "--detach", develop_head)
        _git(self.root, "switch", "-q", "develop")
        _git(self.root, "reset", "-q", "--hard", develop_head)
        self.github.add_pull(number, head, base or self.published_tip)
        if validated is not None:
            self.github.set_status(head, validated)
        return head

    def check(self, capsys: pytest.CaptureFixture[str], profile: str = "canary") -> tuple[int, str]:
        capsys.readouterr()
        code = corpus_drift.check_corpus(
            self.root,
            profile,
            "origin/published-results",
            "HEAD",
            REPO_SLUG,
            corpus_drift.DEFAULT_MAX_AGE_HOURS,
            self.github,
            NOW,
        )
        return code, capsys.readouterr().out


@pytest.fixture
def scenario(tmp_path: Path) -> Scenario:
    scene = Scenario(tmp_path / "repo")
    scene.publish({BUNDLE: "v1\n", OTHER_BUNDLE: "keep\n", NOTES: "notes\n"})
    return scene


@pytest.mark.parametrize("profile", ["canary", "nightly"])
def test_no_drift_passes_without_github_lookup(
    scenario: Scenario, capsys: pytest.CaptureFixture[str], profile: str
) -> None:
    scenario.github.failure = "must not be called"
    code, out = scenario.check(capsys, profile)
    assert code == 0
    assert "mirror PR" not in out


def test_fully_covered_fresh_drift_passes_and_names_the_pr(
    scenario: Scenario, capsys: pytest.CaptureFixture[str]
) -> None:
    scenario.develop({BUNDLE: "v2\n", NOTES: "notes2\n"}, hours_ago=3)
    scenario.mirror({BUNDLE: "v2\n", NOTES: "notes2\n"}, number=42)
    code, out = scenario.check(capsys)
    assert code == 0
    assert "::notice::2 stale path(s) are covered by validated mirror PR #42" in out
    assert "::error::" not in out


def test_uncovered_stale_path_fails(scenario: Scenario, capsys: pytest.CaptureFixture[str]) -> None:
    scenario.develop({BUNDLE: "v2\n", NOTES: "notes2\n"}, hours_ago=3)
    scenario.mirror({BUNDLE: "v2\n"})
    code, out = scenario.check(capsys)
    assert code == 1
    assert "::error::published-results corpus is 2 path(s) behind develop" in out
    assert "does not carry develop's content" in out


def test_mirror_with_different_content_for_a_path_fails(scenario: Scenario, capsys: pytest.CaptureFixture[str]) -> None:
    scenario.develop({BUNDLE: "v2\n"}, hours_ago=3)
    scenario.mirror({BUNDLE: "v2-edited\n"})
    code, out = scenario.check(capsys)
    assert code == 1
    assert "does not carry develop's content" in out


def test_covered_drift_older_than_a_day_fails(scenario: Scenario, capsys: pytest.CaptureFixture[str]) -> None:
    scenario.develop({BUNDLE: "v2\n"}, hours_ago=30)
    scenario.mirror({BUNDLE: "v2\n"})
    code, out = scenario.check(capsys)
    assert code == 1
    assert "oldest unpublished develop change is 30.0h old" in out
    assert "::error::" in out


def test_superseding_mirror_does_not_reset_the_clock(scenario: Scenario, capsys: pytest.CaptureFixture[str]) -> None:
    scenario.develop({BUNDLE: "v2\n"}, hours_ago=30)
    scenario.develop({BUNDLE: "v3\n"}, hours_ago=1)
    scenario.mirror({BUNDLE: "v3\n"})
    code, out = scenario.check(capsys)
    assert code == 1
    assert "30.0h old" in out


def test_age_counts_from_the_oldest_stale_path(scenario: Scenario, capsys: pytest.CaptureFixture[str]) -> None:
    scenario.develop({NOTES: "notes2\n"}, hours_ago=26)
    scenario.develop({BUNDLE: "v2\n"}, hours_ago=2)
    scenario.mirror({BUNDLE: "v2\n", NOTES: "notes2\n"})
    code, out = scenario.check(capsys)
    assert code == 1
    assert "26.0h old" in out


def test_change_reverted_to_published_content_restarts_the_clock(
    scenario: Scenario, capsys: pytest.CaptureFixture[str]
) -> None:
    scenario.develop({BUNDLE: "v2\n"}, hours_ago=40)
    scenario.develop({BUNDLE: "v1\n"}, hours_ago=30)
    scenario.develop({BUNDLE: "v3\n"}, hours_ago=2)
    scenario.mirror({BUNDLE: "v3\n"})
    code, out = scenario.check(capsys)
    assert code == 0
    assert "2.0h old" in out


def test_develop_ahead_new_path_is_aged_from_its_first_commit(
    scenario: Scenario, capsys: pytest.CaptureFixture[str]
) -> None:
    new_bundle = "results-data/bundles/new.json"
    scenario.develop({new_bundle: "a\n"}, hours_ago=5)
    scenario.develop({new_bundle: "b\n"}, hours_ago=1)
    scenario.mirror({new_bundle: "b\n"})
    code, out = scenario.check(capsys)
    assert code == 0
    assert "5.0h old" in out


@pytest.mark.parametrize("validated", ["failure", "pending", "error", None])
def test_failing_or_missing_validate_status_fails(
    scenario: Scenario, capsys: pytest.CaptureFixture[str], validated: str | None
) -> None:
    scenario.develop({BUNDLE: "v2\n"}, hours_ago=3)
    scenario.mirror({BUNDLE: "v2\n"}, validated=validated)
    code, out = scenario.check(capsys)
    assert code == 1
    assert f"{corpus_drift.VALIDATE_STATUS_CONTEXT} status {validated or 'missing'}, not success" in out


def test_status_under_another_context_does_not_count(scenario: Scenario, capsys: pytest.CaptureFixture[str]) -> None:
    scenario.develop({BUNDLE: "v2\n"}, hours_ago=3)
    head = scenario.mirror({BUNDLE: "v2\n"}, validated=None)
    scenario.github.set_status(head, "success", context="ci/other")
    code, _ = scenario.check(capsys)
    assert code == 1


def test_mirror_on_a_stale_published_base_fails(scenario: Scenario, capsys: pytest.CaptureFixture[str]) -> None:
    old_tip = scenario.published_tip
    scenario.develop({BUNDLE: "v2\n"}, hours_ago=3)
    scenario.mirror({BUNDLE: "v2\n"}, base=old_tip)
    _git(scenario.root, "switch", "-q", "--detach", old_tip)
    advanced = _commit(scenario.root, {OTHER_BUNDLE: "community\n"}, "community publication", hours_ago=2)
    _git(scenario.root, "update-ref", "refs/remotes/origin/published-results", advanced)
    _git(scenario.root, "switch", "-q", "develop")
    code, out = scenario.check(capsys)
    assert code == 1
    assert "is based on" in out


def test_no_open_mirror_pr_fails(scenario: Scenario, capsys: pytest.CaptureFixture[str]) -> None:
    scenario.develop({BUNDLE: "v2\n"}, hours_ago=3)
    code, out = scenario.check(capsys)
    assert code == 1
    assert "no open mirror PR targets published-results" in out


@pytest.mark.parametrize(
    "override",
    [
        {"title": "feat: something else"},
        {"head": {"ref": "feature/x", "sha": "0" * 40, "repo": {"full_name": REPO_SLUG}}},
        {"head": {"ref": "auto/results-mirror-abcd1234", "sha": "0" * 40, "repo": {"full_name": "fork/BenchBox"}}},
        {"base": {"ref": "develop", "sha": "0" * 40}},
    ],
)
def test_pr_not_created_by_the_sync_workflow_is_ignored(
    scenario: Scenario, capsys: pytest.CaptureFixture[str], override: dict[str, Any]
) -> None:
    scenario.develop({BUNDLE: "v2\n"}, hours_ago=3)
    scenario.mirror({BUNDLE: "v2\n"})
    scenario.github.pulls[0].update(override)
    code, out = scenario.check(capsys)
    assert code == 1
    assert "no open mirror PR targets published-results" in out


def test_second_mirror_pr_can_cover_when_the_first_does_not(
    scenario: Scenario, capsys: pytest.CaptureFixture[str]
) -> None:
    scenario.develop({BUNDLE: "v2\n"}, hours_ago=3)
    scenario.mirror({BUNDLE: "v1-ish\n"}, number=1)
    scenario.mirror({BUNDLE: "v2\n"}, number=2)
    code, out = scenario.check(capsys)
    assert code == 0
    assert "PR #2" in out


def test_lookup_error_fails_closed(scenario: Scenario, capsys: pytest.CaptureFixture[str]) -> None:
    scenario.develop({BUNDLE: "v2\n"}, hours_ago=3)
    scenario.mirror({BUNDLE: "v2\n"})
    scenario.github.failure = "HTTP 502"
    code, out = scenario.check(capsys)
    assert code == 1
    assert "lookup failed, failing closed: HTTP 502" in out
    assert "::error::published-results corpus is 1 path(s) behind develop" in out


def test_unfetchable_mirror_head_fails_closed(scenario: Scenario, capsys: pytest.CaptureFixture[str]) -> None:
    scenario.develop({BUNDLE: "v2\n"}, hours_ago=3)
    scenario.github.add_pull(9, "1" * 40, scenario.published_tip)
    scenario.github.set_status("1" * 40, "success")
    code, out = scenario.check(capsys)
    assert code == 1
    assert "lookup failed, failing closed" in out


def test_published_content_absent_from_develop_history_fails_closed(
    scenario: Scenario, capsys: pytest.CaptureFixture[str]
) -> None:
    scenario.develop({BUNDLE: "v2\n"}, hours_ago=3)
    scenario.mirror({BUNDLE: "v2\n"})
    sha = _commit_published_rewrite(scenario)
    assert sha
    code, out = scenario.check(capsys)
    assert code == 1


def _commit_published_rewrite(scenario: Scenario) -> str:
    base = scenario.published_tip
    _git(scenario.root, "switch", "-q", "--detach", base)
    sha = _commit(scenario.root, {BUNDLE: "hand-edited\n"}, "hand edit", hours_ago=100)
    _git(scenario.root, "update-ref", "refs/remotes/origin/published-results", sha)
    _git(scenario.root, "switch", "-q", "develop")
    scenario.github.pulls[0]["base"]["sha"] = sha
    return sha


def test_shallow_boundary_commit_is_not_trusted_as_the_origin_of_a_path(
    scenario: Scenario, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scenario.develop({BUNDLE: "v2\n"}, hours_ago=60)
    scenario.develop({OTHER_BUNDLE: "tweak\n"}, hours_ago=1)
    scenario.mirror({BUNDLE: "v2\n", OTHER_BUNDLE: "tweak\n"})
    shallow = tmp_path / "shallow"
    _git(tmp_path, "clone", "-q", "--depth=1", f"file://{scenario.root}", str(shallow))
    _git(shallow, "fetch", "-q", "--depth=1", "origin", scenario.published_tip)
    _git(shallow, "update-ref", "refs/remotes/origin/published-results", scenario.published_tip)
    clone = Scenario.__new__(Scenario)
    clone.root = shallow
    clone.github = scenario.github
    code, out = clone.check(capsys)
    assert code == 1
    assert "::error::" in out


def test_published_only_paths_are_a_notice_not_a_failure(
    scenario: Scenario, capsys: pytest.CaptureFixture[str]
) -> None:
    scenario.develop({OTHER_BUNDLE: None}, hours_ago=3)
    scenario.github.failure = "must not be called"
    code, out = scenario.check(capsys)
    assert code == 0
    assert "::notice::published-results has 1 path(s) not on develop (published-only)." in out
    assert OTHER_BUNDLE in out


def test_nightly_profile_keeps_its_notice_and_failure_text(
    scenario: Scenario, capsys: pytest.CaptureFixture[str]
) -> None:
    scenario.develop({OTHER_BUNDLE: None, BUNDLE: "v2\n"}, hours_ago=3)
    code, out = scenario.check(capsys, "nightly")
    assert code == 1
    assert "(accepted archive submissions; not drift)." in out
    assert "::error::published-results corpus is 1 path(s) behind develop (develop-ahead or content-changed)." in out
    assert "Mirror develop-ahead paths with the non-destructive union overlay:" in out
    assert "Never use a wipe-based full mirror: published-only community submissions would be deleted." in out


def test_canary_profile_keeps_its_failure_text(scenario: Scenario, capsys: pytest.CaptureFixture[str]) -> None:
    scenario.develop({OTHER_BUNDLE: None, BUNDLE: "v2\n"}, hours_ago=3)
    code, out = scenario.check(capsys)
    assert code == 1
    assert "The public corpus is stale." in out
    assert "Do NOT use a wipe-based full mirror (git rm -rf results-data/bundles)" in out
    assert f"  {corpus_drift.SYNC_WORKFLOW_COMMAND}" in out


def test_missing_published_results_tree_exits_two(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    scene = Scenario(tmp_path / "empty")
    sha = _commit(scene.root, {"README.md": "x\n"}, "no results-data", hours_ago=1)
    _git(scene.root, "update-ref", "refs/remotes/origin/published-results", sha)
    code, out = scene.check(capsys)
    assert code == 2
    assert "has no results-data/ tree" in out
