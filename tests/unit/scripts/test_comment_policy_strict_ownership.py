from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from check_comment_policy import main, ownership_base, validate_consumers

pytestmark = [pytest.mark.unit, pytest.mark.medium]

EMPTY_POLICY = {"version": 1, "external": [], "completed": [], "exceptions": []}
WHEEL = "vendor/tool-1.0-py3-none-any.whl"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


def _commit(root: Path, name: str) -> str:
    (root / name).write_text(name + "\n", encoding="utf-8")
    _git(root, "add", name)
    _git(root, "-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-qm", name)
    return _git(root, "rev-parse", "HEAD")


def _repo(tmp_path: Path) -> tuple[Path, str]:
    _git(tmp_path, "init", "-q")
    (tmp_path / "quality").mkdir()
    (tmp_path / "quality/comment-policy.json").write_text(json.dumps(EMPTY_POLICY), encoding="utf-8")
    _git(tmp_path, "add", "quality/comment-policy.json")
    return tmp_path, _commit(tmp_path, "first.txt")


def _publish_develop(root: Path, sha: str) -> None:
    _git(root, "update-ref", "refs/remotes/origin/develop", sha)


def test_strict_defaults_to_the_merge_base_with_origin_develop(tmp_path: Path) -> None:
    root, develop = _repo(tmp_path)
    _publish_develop(root, develop)
    _commit(root, "feature.txt")

    assert ownership_base(root, "strict", None, None) == develop


def test_strict_on_a_clean_develop_checkout_uses_head(tmp_path: Path) -> None:
    root, develop = _repo(tmp_path)
    _publish_develop(root, develop)

    assert ownership_base(root, "strict", None, None) == _git(root, "rev-parse", "HEAD")


def test_strict_fails_closed_when_origin_develop_is_unavailable(tmp_path: Path) -> None:
    root, _ = _repo(tmp_path)

    with pytest.raises(ValueError, match="origin/develop"):
        ownership_base(root, "strict", None, None)
    assert main(["--root", str(root), "--mode", "strict"]) == 2


def test_report_mode_without_a_base_applies_no_ownership_manifest(tmp_path: Path) -> None:
    root, develop = _repo(tmp_path)
    _publish_develop(root, develop)

    assert ownership_base(root, "report", None, None) is None


def test_explicit_base_must_be_on_origin_develop_history(tmp_path: Path) -> None:
    root, develop = _repo(tmp_path)
    _publish_develop(root, develop)
    feature = _commit(root, "feature.txt")

    assert ownership_base(root, "strict", None, develop) == develop
    with pytest.raises(ValueError, match="ancestor of the merge-base"):
        ownership_base(root, "strict", None, feature)


def test_explicit_base_is_accepted_when_origin_develop_is_unavailable(tmp_path: Path) -> None:
    root, first = _repo(tmp_path)

    assert ownership_base(root, "strict", None, first) == first


def _policy_with_wheel(root: Path) -> dict:
    (root / "vendor").mkdir(exist_ok=True)
    (root / "vendor/pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    return {
        **EMPTY_POLICY,
        "external": [{"path": WHEEL, "owner": "tool", "provenance": "vendor/pyproject.toml"}],
    }


def test_a_pinned_wheel_that_no_longer_exists_is_rejected(tmp_path: Path) -> None:
    root, _ = _repo(tmp_path)

    with pytest.raises(ValueError, match="external wheel missing"):
        validate_consumers(root, _policy_with_wheel(root))


def test_a_present_pinned_wheel_is_accepted(tmp_path: Path) -> None:
    root, _ = _repo(tmp_path)
    policy = _policy_with_wheel(root)
    (root / WHEEL).write_bytes(b"PK")

    validate_consumers(root, policy)


def test_staged_validation_reads_the_index_not_the_worktree(tmp_path: Path) -> None:
    root, _ = _repo(tmp_path)
    policy = _policy_with_wheel(root)
    (root / WHEEL).write_bytes(b"PK")
    _git(root, "add", "vendor/pyproject.toml")

    with pytest.raises(ValueError, match="external wheel missing"):
        validate_consumers(root, policy, staged=True)

    _git(root, "add", WHEEL)
    validate_consumers(root, policy, staged=True)


def test_staged_validation_rejects_a_missing_exception_consumer_that_the_worktree_still_has(tmp_path: Path) -> None:
    root, _ = _repo(tmp_path)
    (root / "consumer.py").write_text("x = 1\n", encoding="utf-8")
    policy = {**EMPTY_POLICY, "exceptions": [{"consumer": "consumer.py"}]}

    validate_consumers(root, policy)
    with pytest.raises(ValueError, match="exception consumer missing"):
        validate_consumers(root, policy, staged=True)
