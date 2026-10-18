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


def test_explicit_base_is_rejected_when_origin_develop_is_unavailable(tmp_path: Path) -> None:
    root, first = _repo(tmp_path)

    with pytest.raises(ValueError, match="needs origin/develop"):
        ownership_base(root, "strict", None, first)


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


def _wheel_bytes(name: str = "tool", version: str = "2.0") -> bytes:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            f"{name}-{version}.dist-info/METADATA", f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n\n"
        )
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def _skip_remote_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BENCHBOX_TEST_SKIP_REMOTE_FETCH", "1")


def _bump_repo(tmp_path: Path, **overrides: object) -> tuple[Path, dict, dict]:
    import hashlib

    root, _ = _repo(tmp_path)
    old = "vendor/tool-1.0-py3-none-any.whl"
    new = "vendor/tool-2.0-py3-none-any.whl"
    wheel = overrides.get("wheel", _wheel_bytes())
    digest = overrides.get("digest", "sha256:" + hashlib.sha256(wheel).hexdigest())
    binding = overrides.get("binding", '{ path = "tool-2.0-py3-none-any.whl" }')
    dependency = overrides.get("dependency", "tool[extra]")
    version = overrides.get("version", "2.0")
    (root / "vendor").mkdir()
    (root / "vendor/tool-2.0-py3-none-any.whl").write_bytes(wheel)
    (root / "vendor/pyproject.toml").write_text(
        f'[project]\nname = "x"\ndependencies = ["{dependency}"]\n\n[tool.uv.sources]\ntool = {binding}\n',
        encoding="utf-8",
    )
    (root / "vendor/uv.lock").write_text(
        f'[[package]]\nname = "tool"\nversion = "{version}"\nsource = {binding}\n'
        f'wheels = [{{ filename = "tool-2.0-py3-none-any.whl", hash = "{digest}" }}]\n',
        encoding="utf-8",
    )
    raw_hash = hashlib.sha256(wheel).hexdigest()
    evidence_dir = root / "_project/analysis"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "batch-rollout-evidence.json").write_text(
        json.dumps(
            {
                "artifact": {
                    "filename": "tool-2.0-py3-none-any.whl",
                    "sha256": raw_hash,
                    "origin": "GitHub release asset",
                    "build": {
                        "source_input": f"git archive v{version} (0123456789012345678901234567890123456789)",
                        "reproducibility": {"matching_sha256": raw_hash},
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    entry = {"path": new, "owner": "tool", "provenance": "vendor/pyproject.toml"}
    baseline = {**EMPTY_POLICY, "external": [{**entry, "path": old}]}
    return root, {**EMPTY_POLICY, "external": [entry]}, baseline


def test_a_bound_wheel_bump_is_accepted(tmp_path: Path) -> None:
    from check_comment_policy import verify_wheel_bumps

    root, policy, baseline = _bump_repo(tmp_path)

    bumped = verify_wheel_bumps(root, policy, baseline, staged=False)

    assert bumped == {entry["path"] for entry in policy["external"] if entry not in baseline["external"]}
    assert bumped


@pytest.mark.parametrize(
    "override,message",
    [
        ({"digest": "sha256:" + "0" * 64}, "uv.lock does not record this file and hash"),
        ({"binding": '{ path = "other.whl" }'}, "does not bind tool"),
        ({"dependency": "unrelated"}, "not a declared dependency"),
        ({"wheel": _wheel_bytes(name="other")}, "METADATA does not match"),
        ({"wheel": _wheel_bytes(version="9.9")}, "METADATA does not match"),
        ({"wheel": b"not a zip"}, "wheel bump is not verified"),
    ],
)
def test_an_unbound_or_mismatched_wheel_bump_is_rejected(tmp_path: Path, override: dict, message: str) -> None:
    from check_comment_policy import verify_wheel_bumps

    root, policy, baseline = _bump_repo(tmp_path, **override)

    with pytest.raises(ValueError, match=message):
        verify_wheel_bumps(root, policy, baseline, staged=False)


def test_an_unchanged_external_entry_needs_no_bump_verification(tmp_path: Path) -> None:
    from check_comment_policy import verify_wheel_bumps

    root, policy, _ = _bump_repo(tmp_path)

    verify_wheel_bumps(root, policy, policy, staged=False)


def test_staged_bump_verification_reads_the_index(tmp_path: Path) -> None:
    from check_comment_policy import verify_wheel_bumps

    root, policy, baseline = _bump_repo(tmp_path)

    with pytest.raises(ValueError, match="missing"):
        verify_wheel_bumps(root, policy, baseline, staged=True)

    _git(root, "add", "vendor", "_project")
    verify_wheel_bumps(root, policy, baseline, staged=True)


def _bump_against_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **overrides: object) -> tuple[Path, str]:
    for name in ("GITHUB_EVENT_PATH", "GITHUB_REPOSITORY", "GITHUB_TOKEN", "GH_TOKEN", "GITHUB_ACTIONS"):
        monkeypatch.delenv(name, raising=False)
    root, policy, baseline = _bump_repo(tmp_path, **overrides)
    policy_path = root / "quality/comment-policy.json"
    policy_path.write_text(json.dumps(baseline), encoding="utf-8")
    (root / "module.py").write_text("x = 1\n", encoding="utf-8")
    _git(root, "add", "quality/comment-policy.json", "module.py", "_project")
    base = _commit(root, "base.txt")
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    return root, base


def test_main_accepts_a_bound_wheel_bump_against_the_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, base = _bump_against_base(tmp_path, monkeypatch)

    assert main(["--root", str(root), "--mode", "transition", "--base", base]) == 0


def test_main_rejects_an_unbound_wheel_bump_against_the_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root, base = _bump_against_base(tmp_path, monkeypatch, digest="sha256:" + "0" * 64)

    assert main(["--root", str(root), "--mode", "transition", "--base", base]) == 2
    assert "uv.lock does not record this file and hash" in capsys.readouterr().err


def test_main_without_a_base_skips_wheel_bump_verification(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _ = _bump_against_base(tmp_path, monkeypatch, digest="sha256:" + "0" * 64)

    assert main(["--root", str(root), "--mode", "report"]) == 0


def test_main_bootstrap_skips_wheel_bump_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import check_comment_policy

    root, base = _bump_against_base(tmp_path, monkeypatch, digest="sha256:" + "0" * 64)
    monkeypatch.setattr(check_comment_policy, "bootstrap_base_allowed", lambda _root, _base: True)

    assert main(["--root", str(root), "--mode", "transition", "--base", base]) == 2
    assert "wheel bump is not verified" in capsys.readouterr().err
    assert main(["--root", str(root), "--mode", "transition", "--base", base, "--bootstrap"]) == 0
