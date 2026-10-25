from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from tests.utilities.posix_shell import run_posix_shell, skip_without_posix_shell

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "validate-submission.yml"

_STEP_NAME = "Reject non-maintainer internal-source manifests"
_BUNDLE_DIR = "results-data/bundles"


def _step() -> dict:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    for step in workflow["jobs"]["validate"]["steps"]:
        if step.get("name") == _STEP_NAME:
            return step
    raise AssertionError(f"could not find the {_STEP_NAME!r} step in validate-submission.yml")


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _repo_with_manifest(tmp_path: Path, manifest: dict, *, name: str = "run.manifest.json") -> tuple[Path, str, str]:
    repo = tmp_path / "repo"
    (repo / _BUNDLE_DIR).mkdir(parents=True)
    _git(repo, "init", "-q")
    (repo / "README").write_text("base", encoding="utf-8")
    _git(repo, "add", "README")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / _BUNDLE_DIR / name).write_text(json.dumps(manifest), encoding="utf-8")
    _git(repo, "add", f"{_BUNDLE_DIR}/{name}")
    _git(repo, "commit", "-q", "-m", "submission")
    return repo, base, _git(repo, "rev-parse", "HEAD")


def _run(repo: Path, base: str, head: str, **overrides: str) -> subprocess.CompletedProcess[str]:
    skip_without_posix_shell()
    env = os.environ.copy()
    env.update(
        {
            "BASE_SHA": base,
            "MERGE_SHA": head,
            "AUTHOR_ASSOCIATION": "NONE",
            "IS_FORK": "true",
            "BASE_REF": "published-results",
            "HEAD_REF": "feature/community-result",
            "PR_AUTHOR": "contributor",
        }
    )
    env.update(overrides)
    return run_posix_shell(
        _step()["run"],
        capture_output=True,
        check=False,
        cwd=repo,
        env=env,
        text=True,
    )


def test_step_binds_trust_inputs_from_the_event_not_pr_content() -> None:
    env = _step()["env"]
    assert env["AUTHOR_ASSOCIATION"] == "${{ github.event.pull_request.author_association }}"
    assert env["PR_AUTHOR"] == "${{ github.event.pull_request.user.login }}"
    assert env["IS_FORK"] == "${{ github.event.pull_request.head.repo.fork }}"


def test_community_manifest_declaring_internal_is_rejected(tmp_path: Path) -> None:
    repo, base, head = _repo_with_manifest(tmp_path, {"bundle_file": "run.json", "result_source": "internal"})
    result = _run(repo, base, head)
    assert result.returncode == 1
    assert "::error file=results-data/bundles/run.manifest.json::" in result.stdout


def test_community_legacy_submission_manifest_declaring_internal_is_rejected(tmp_path: Path) -> None:
    repo, base, head = _repo_with_manifest(
        tmp_path,
        {"bundle_file": "run.json", "result_source": "internal"},
        name="submission-manifest.json",
    )
    assert _run(repo, base, head).returncode == 1


@pytest.mark.parametrize("source", ["community", "vendor", None])
def test_community_manifest_with_other_source_passes(tmp_path: Path, source: str | None) -> None:
    manifest: dict = {"bundle_file": "run.json"}
    if source is not None:
        manifest["result_source"] = source
    repo, base, head = _repo_with_manifest(tmp_path, manifest)
    assert _run(repo, base, head).returncode == 0


@pytest.mark.parametrize("association", ["OWNER", "MEMBER", "COLLABORATOR"])
def test_maintainer_internal_manifest_passes(tmp_path: Path, association: str) -> None:
    repo, base, head = _repo_with_manifest(tmp_path, {"bundle_file": "run.json", "result_source": "internal"})
    assert _run(repo, base, head, AUTHOR_ASSOCIATION=association).returncode == 0


def test_trusted_same_repo_mirror_internal_manifest_passes(tmp_path: Path) -> None:
    repo, base, head = _repo_with_manifest(tmp_path, {"bundle_file": "run.json", "result_source": "internal"})
    result = _run(
        repo,
        base,
        head,
        IS_FORK="false",
        HEAD_REF="auto/results-mirror-deadbeef",
        PR_AUTHOR="github-actions[bot]",
    )
    assert result.returncode == 0


@pytest.mark.parametrize(
    "shape",
    [
        {"IS_FORK": "true", "HEAD_REF": "auto/results-mirror-deadbeef", "PR_AUTHOR": "github-actions[bot]"},
        {"IS_FORK": "false", "HEAD_REF": "feature/x", "PR_AUTHOR": "github-actions[bot]"},
        {"IS_FORK": "false", "HEAD_REF": "auto/results-mirror-deadbeef", "PR_AUTHOR": "contributor"},
        {
            "IS_FORK": "false",
            "HEAD_REF": "auto/results-mirror-deadbeef",
            "PR_AUTHOR": "github-actions[bot]",
            "BASE_REF": "develop",
        },
    ],
)
def test_untrusted_mirror_shapes_do_not_bypass(tmp_path: Path, shape: dict[str, str]) -> None:
    repo, base, head = _repo_with_manifest(tmp_path, {"bundle_file": "run.json", "result_source": "internal"})
    assert _run(repo, base, head, **shape).returncode == 1
