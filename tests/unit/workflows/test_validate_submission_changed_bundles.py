from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tests.utilities.posix_shell import run_posix_shell, skip_without_posix_shell

pytestmark = [pytest.mark.unit, pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "validate-submission.yml"


def _changed_bundle_discovery_script() -> str:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    step = next(step for step in workflow["jobs"]["validate"]["steps"] if step.get("id") == "changed")
    run = step["run"]
    start = run.index("CHANGED=$(git diff")
    end = run.index('if [ -z "$CHANGED" ]')
    snippet = run[start:end]
    snippet = snippet.replace("${{ github.event.pull_request.base.sha }}", "$BASE_SHA")
    snippet = snippet.replace("${{ env.BASE_SHA }}", "$BASE_SHA")
    snippet = snippet.replace("${{ env.MERGE_SHA }}", "${MERGE_SHA:-HEAD}")
    snippet = snippet.replace("$EFFECTIVE_MERGE", "${MERGE_SHA:-HEAD}")
    snippet = snippet.replace("$EFFECTIVE_BASE", "$BASE_SHA")
    return snippet + 'printf "%s\\n" "$CHANGED"\n'


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _hash_object(repo: Path, content: str) -> str:
    result = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        input=content,
        encoding="utf-8",
    )
    return result.stdout.strip()


def test_applied_only_change_discovers_paired_primary_bundle(tmp_path: Path) -> None:
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.invalid")

    primary = tmp_path / "results-data" / "bundles" / "result.json"
    primary.parent.mkdir(parents=True)
    primary.write_text("{}\n", encoding="utf-8")
    _git(tmp_path, "add", str(primary.relative_to(tmp_path)))
    _git(tmp_path, "commit", "--quiet", "-m", "base")
    base_sha = _git(tmp_path, "rev-parse", "HEAD")

    applied = primary.with_name("result.applied.json")
    applied.write_text('{"status": "passed"}\n', encoding="utf-8")
    _git(tmp_path, "add", str(applied.relative_to(tmp_path)))
    _git(tmp_path, "commit", "--quiet", "-m", "applied")

    skip_without_posix_shell()
    env = os.environ.copy()
    env["BASE_SHA"] = base_sha
    result = run_posix_shell(
        _changed_bundle_discovery_script(),
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["results-data/bundles/result.json"]


def test_case_insensitive_primary_bundle_discovery_matches_validator(tmp_path: Path) -> None:
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "commit", "--quiet", "--allow-empty", "-m", "base")
    base_sha = _git(tmp_path, "rev-parse", "HEAD")

    bundle_dir = tmp_path / "results-data" / "bundles"
    bundle_dir.mkdir(parents=True)
    primary = bundle_dir / "result.JSON"
    primary.write_text("{}\n", encoding="utf-8")
    (bundle_dir / "result.PLANS.JSON").write_text("{}\n", encoding="utf-8")
    _git(tmp_path, "add", str(bundle_dir.relative_to(tmp_path)))
    _git(tmp_path, "commit", "--quiet", "-m", "uppercase bundle")

    skip_without_posix_shell()
    env = os.environ.copy()
    env["BASE_SHA"] = base_sha
    result = run_posix_shell(
        _changed_bundle_discovery_script(),
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["results-data/bundles/result.JSON"]


def test_applied_companion_derivation_is_deletion_aware() -> None:
    script = _changed_bundle_discovery_script()
    assert "--diff-filter=ACMRD" in script
    assert "CHANGED_APPLIED" in script
    assert 'awk -v stem="$stem"' in script
    assert 'git cat-file -e "' in script and ':${bundle}"' in script


def test_applied_companion_rename_discovers_the_source_primary_bundle(tmp_path: Path) -> None:
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.invalid")

    primary = tmp_path / "results-data" / "bundles" / "result.json"
    primary.parent.mkdir(parents=True)
    primary.write_text("{}\n", encoding="utf-8")
    applied = primary.with_name("result.applied.json")
    applied.write_text('{"status": "passed"}\n', encoding="utf-8")
    _git(tmp_path, "add", str(primary.relative_to(tmp_path)), str(applied.relative_to(tmp_path)))
    _git(tmp_path, "commit", "--quiet", "-m", "base")
    base_sha = _git(tmp_path, "rev-parse", "HEAD")

    renamed = applied.with_name("renamed.applied.json")
    applied.rename(renamed)
    _git(tmp_path, "add", "-u", str(applied.relative_to(tmp_path)))
    _git(tmp_path, "add", str(renamed.relative_to(tmp_path)))
    _git(tmp_path, "commit", "--quiet", "-m", "rename-applied")

    skip_without_posix_shell()
    env = os.environ.copy()
    env["BASE_SHA"] = base_sha
    result = run_posix_shell(
        _changed_bundle_discovery_script(),
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["results-data/bundles/result.json"]


def test_companion_pairing_preserves_stem_case(tmp_path: Path) -> None:
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.invalid")

    blob_file = tmp_path / "blob"
    blob_file.write_text("{}\n", encoding="utf-8")
    base_blob = _git(tmp_path, "hash-object", "-w", str(blob_file))
    for path in (
        "results-data/bundles/FOO.JSON",
        "results-data/bundles/foo.json",
        "results-data/bundles/foo.applied.json",
    ):
        _git(tmp_path, "update-index", "--add", "--cacheinfo", f"100644,{base_blob},{path}")
    _git(tmp_path, "commit", "--quiet", "-m", "base")
    base_sha = _git(tmp_path, "rev-parse", "HEAD")

    blob_file.write_text('{"status": "passed"}\n', encoding="utf-8")
    applied_blob = _git(tmp_path, "hash-object", "-w", str(blob_file))
    _git(
        tmp_path,
        "update-index",
        "--add",
        "--cacheinfo",
        f"100644,{applied_blob},results-data/bundles/foo.applied.json",
    )
    _git(tmp_path, "commit", "--quiet", "-m", "applied")

    skip_without_posix_shell()
    env = os.environ.copy()
    env["BASE_SHA"] = base_sha
    result = run_posix_shell(
        _changed_bundle_discovery_script(),
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["results-data/bundles/foo.json"]


@pytest.mark.parametrize("companion_suffix", [".plans.json", ".tuning.json"])
def test_plans_or_tuning_only_change_discovers_paired_primary_bundle(tmp_path: Path, companion_suffix: str) -> None:
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.invalid")

    primary = tmp_path / "results-data" / "bundles" / "result.json"
    primary.parent.mkdir(parents=True)
    primary.write_text("{}\n", encoding="utf-8")
    _git(tmp_path, "add", str(primary.relative_to(tmp_path)))
    _git(tmp_path, "commit", "--quiet", "-m", "base")
    base_sha = _git(tmp_path, "rev-parse", "HEAD")

    companion = primary.with_name(f"result{companion_suffix}")
    companion.write_text('{"note": "companion only"}\n', encoding="utf-8")
    _git(tmp_path, "add", str(companion.relative_to(tmp_path)))
    _git(tmp_path, "commit", "--quiet", "-m", "companion only")

    skip_without_posix_shell()
    env = os.environ.copy()
    env["BASE_SHA"] = base_sha
    result = run_posix_shell(
        _changed_bundle_discovery_script(),
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["results-data/bundles/result.json"]


def test_each_companion_back_mapping_loop_discovers_its_primary_bundle(tmp_path: Path) -> None:
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.invalid")

    bundle_dir = tmp_path / "results-data" / "bundles"
    bundle_dir.mkdir(parents=True)
    companion_specs = [
        ("manifest-result", ".manifest.json"),
        ("applied-result", ".applied.json"),
        ("plans-result", ".plans.json"),
        ("tuning-result", ".tuning.json"),
    ]
    for stem, _suffix in companion_specs:
        (bundle_dir / f"{stem}.json").write_text("{}\n", encoding="utf-8")
    _git(tmp_path, "add", str(bundle_dir.relative_to(tmp_path)))
    _git(tmp_path, "commit", "--quiet", "-m", "base")
    base_sha = _git(tmp_path, "rev-parse", "HEAD")

    for stem, suffix in companion_specs:
        (bundle_dir / f"{stem}{suffix}").write_text("{}\n", encoding="utf-8")
    _git(tmp_path, "add", str(bundle_dir.relative_to(tmp_path)))
    _git(tmp_path, "commit", "--quiet", "-m", "add companions")

    skip_without_posix_shell()
    result = run_posix_shell(
        _changed_bundle_discovery_script(),
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "BASE_SHA": base_sha},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == [
        "results-data/bundles/manifest-result.json",
        "results-data/bundles/applied-result.json",
        "results-data/bundles/plans-result.json",
        "results-data/bundles/tuning-result.json",
    ]


@pytest.mark.parametrize("companion_suffix", [".plans.json", ".tuning.json"])
def test_live_orphan_companion_is_rejected(tmp_path: Path, companion_suffix: str) -> None:
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    placeholder = tmp_path / "README"
    placeholder.write_text("base\n", encoding="utf-8")
    _git(tmp_path, "add", "README")
    _git(tmp_path, "commit", "--quiet", "-m", "base")
    base_sha = _git(tmp_path, "rev-parse", "HEAD")

    companion = tmp_path / "results-data" / "bundles" / f"orphan{companion_suffix}"
    companion.parent.mkdir(parents=True)
    companion.write_text("{}\n", encoding="utf-8")
    _git(tmp_path, "add", str(companion.relative_to(tmp_path)))
    _git(tmp_path, "commit", "--quiet", "-m", "orphan")

    skip_without_posix_shell()
    env = {**os.environ, "BASE_SHA": base_sha}
    result = run_posix_shell(
        _changed_bundle_discovery_script(),
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode != 0
    assert companion.relative_to(tmp_path).as_posix() in result.stdout
    assert "has no primary bundle" in result.stdout


@pytest.mark.parametrize("companion_suffix", [".plans.json", ".tuning.json"])
def test_deleted_orphan_companion_is_allowed(tmp_path: Path, companion_suffix: str) -> None:
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    companion = tmp_path / "results-data" / "bundles" / f"orphan{companion_suffix}"
    companion.parent.mkdir(parents=True)
    companion.write_text("{}\n", encoding="utf-8")
    _git(tmp_path, "add", str(companion.relative_to(tmp_path)))
    _git(tmp_path, "commit", "--quiet", "-m", "base orphan")
    base_sha = _git(tmp_path, "rev-parse", "HEAD")
    companion.unlink()
    _git(tmp_path, "add", "-u")
    _git(tmp_path, "commit", "--quiet", "-m", "remove orphan")

    skip_without_posix_shell()
    env = {**os.environ, "BASE_SHA": base_sha}
    result = run_posix_shell(
        _changed_bundle_discovery_script(),
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


def test_deleted_bundle_and_manifest_do_not_inject_empty_bundle_path(tmp_path: Path) -> None:
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.invalid")

    bundle_dir = tmp_path / "results-data" / "bundles"
    bundle_dir.mkdir(parents=True)
    keep = bundle_dir / "keep.json"
    gone = bundle_dir / "gone.json"
    gone_manifest = bundle_dir / "gone.manifest.json"
    keep.write_text('{"keep": 1}\n', encoding="utf-8")
    gone.write_text('{"gone": 1}\n', encoding="utf-8")
    gone_manifest.write_text("{}\n", encoding="utf-8")
    _git(tmp_path, "add", str(bundle_dir.relative_to(tmp_path)))
    _git(tmp_path, "commit", "--quiet", "-m", "base")
    base_sha = _git(tmp_path, "rev-parse", "HEAD")

    keep.write_text('{"keep": 2}\n', encoding="utf-8")
    gone.unlink()
    gone_manifest.unlink()
    _git(tmp_path, "add", "-u", str(bundle_dir.relative_to(tmp_path)))
    _git(tmp_path, "commit", "--quiet", "-m", "modify keep; delete gone pair")

    skip_without_posix_shell()
    env = {**os.environ, "BASE_SHA": base_sha}
    result = run_posix_shell(
        _changed_bundle_discovery_script(),
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "results-data/bundles/keep.json\n"


@pytest.mark.parametrize("companion_suffix", [".manifest.json", ".plans.json", ".tuning.json", ".applied.json"])
def test_deleted_primary_requires_deleting_every_same_stem_companion(tmp_path: Path, companion_suffix: str) -> None:
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.invalid")

    bundle_dir = tmp_path / "results-data" / "bundles"
    bundle_dir.mkdir(parents=True)
    primary = bundle_dir / "gone.json"
    companion = bundle_dir / f"gone{companion_suffix}"
    primary.write_text('{"gone": 1}\n', encoding="utf-8")
    companion.write_text("{}\n", encoding="utf-8")
    _git(tmp_path, "add", str(bundle_dir.relative_to(tmp_path)))
    _git(tmp_path, "commit", "--quiet", "-m", "base")
    base_sha = _git(tmp_path, "rev-parse", "HEAD")

    primary.unlink()
    _git(tmp_path, "add", "-u", str(primary.relative_to(tmp_path)))
    _git(tmp_path, "commit", "--quiet", "-m", "delete primary only")

    skip_without_posix_shell()
    result = run_posix_shell(
        _changed_bundle_discovery_script(),
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "BASE_SHA": base_sha},
    )

    assert result.returncode != 0
    assert f"file=results-data/bundles/gone{companion_suffix}" in result.stdout
    assert "also requires deleting its same-stem companion" in result.stdout


def test_deleted_primary_with_many_surviving_files_does_not_sigpipe(tmp_path: Path) -> None:
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.invalid")

    bundle_dir = tmp_path / "results-data" / "bundles"
    bundle_dir.mkdir(parents=True)
    primary = bundle_dir / "000_gone.json"
    companion = bundle_dir / "000_gone.manifest.json"
    primary.write_text('{"gone": 1}\n', encoding="utf-8")
    companion.write_text("{}\n", encoding="utf-8")
    for i in range(1500):
        (bundle_dir / f"zzz_{i:04d}.json").write_text("{}\n", encoding="utf-8")

    _git(tmp_path, "add", str(bundle_dir.relative_to(tmp_path)))
    _git(tmp_path, "commit", "--quiet", "-m", "base")
    base_sha = _git(tmp_path, "rev-parse", "HEAD")

    primary.unlink()
    _git(tmp_path, "add", "-u", str(primary.relative_to(tmp_path)))
    _git(tmp_path, "commit", "--quiet", "-m", "delete primary only")

    skip_without_posix_shell()
    result = run_posix_shell(
        _changed_bundle_discovery_script(),
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "BASE_SHA": base_sha},
    )

    assert result.returncode != 0
    assert result.returncode != 141
    assert "file=results-data/bundles/000_gone.manifest.json" in result.stdout
    assert "also requires deleting its same-stem companion" in result.stdout


def test_deleted_primary_does_not_match_differently_cased_stem(tmp_path: Path) -> None:
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.invalid")

    bundle_dir = tmp_path / "results-data" / "bundles"
    bundle_dir.mkdir(parents=True)
    primary = bundle_dir / "gone.json"
    (bundle_dir / "GONE.MANIFEST.JSON").write_text("{}\n", encoding="utf-8")
    primary.write_text('{"gone": 1}\n', encoding="utf-8")
    _git(tmp_path, "add", str(bundle_dir.relative_to(tmp_path)))
    _git(tmp_path, "commit", "--quiet", "-m", "base")
    base_sha = _git(tmp_path, "rev-parse", "HEAD")

    primary.unlink()
    _git(tmp_path, "add", "-u", str(primary.relative_to(tmp_path)))
    _git(tmp_path, "commit", "--quiet", "-m", "delete primary only")

    skip_without_posix_shell()
    result = run_posix_shell(
        _changed_bundle_discovery_script(),
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "BASE_SHA": base_sha},
    )

    assert result.returncode == 0, result.stderr


def test_deleted_non_ascii_primary_requires_deleting_companion(tmp_path: Path) -> None:
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.invalid")

    bundle_dir = tmp_path / "results-data" / "bundles"
    bundle_dir.mkdir(parents=True)
    primary = bundle_dir / "café.json"
    companion = bundle_dir / "café.manifest.json"
    primary.write_text("{}\n", encoding="utf-8")
    companion.write_text("{}\n", encoding="utf-8")
    _git(tmp_path, "add", str(bundle_dir.relative_to(tmp_path)))
    _git(tmp_path, "commit", "--quiet", "-m", "base")
    base_sha = _git(tmp_path, "rev-parse", "HEAD")

    primary.unlink()
    _git(tmp_path, "add", "-u", str(primary.relative_to(tmp_path)))
    _git(tmp_path, "commit", "--quiet", "-m", "delete primary only")

    skip_without_posix_shell()
    result = run_posix_shell(
        _changed_bundle_discovery_script(),
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "BASE_SHA": base_sha},
    )

    assert result.returncode != 0
    assert "café.manifest.json" in result.stdout


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="a backslash is a path separator on Windows, so a file named foo\\bar.json cannot be created",
)
def test_deleted_backslash_primary_requires_deleting_companion(tmp_path: Path) -> None:
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.invalid")

    bundle_dir = tmp_path / "results-data" / "bundles"
    bundle_dir.mkdir(parents=True)
    primary = bundle_dir / "foo\\bar.json"
    companion = bundle_dir / "foo\\bar.manifest.json"
    primary.write_text("{}\n", encoding="utf-8")
    companion.write_text("{}\n", encoding="utf-8")
    _git(tmp_path, "add", str(bundle_dir.relative_to(tmp_path)))
    _git(tmp_path, "commit", "--quiet", "-m", "base")
    base_sha = _git(tmp_path, "rev-parse", "HEAD")

    primary.unlink()
    _git(tmp_path, "add", "-u", str(primary.relative_to(tmp_path)))
    _git(tmp_path, "commit", "--quiet", "-m", "delete primary only")

    skip_without_posix_shell()
    result = run_posix_shell(
        _changed_bundle_discovery_script(),
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "BASE_SHA": base_sha},
    )

    assert result.returncode != 0
    assert "foo\\bar.manifest.json" in result.stdout


def test_deleted_bundle_requires_deleting_referencing_legacy_manifest(tmp_path: Path) -> None:
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.invalid")

    bundle_dir = tmp_path / "results-data" / "bundles" / "duckdb"
    bundle_dir.mkdir(parents=True)
    primary = bundle_dir / "gone.json"
    legacy_manifest = bundle_dir / "submission-manifest.json"
    primary.write_text('{"gone": 1}\n', encoding="utf-8")
    legacy_manifest.write_text('{"bundle_file": "gone.json"}\n', encoding="utf-8")
    _git(tmp_path, "add", str(bundle_dir.relative_to(tmp_path)))
    _git(tmp_path, "commit", "--quiet", "-m", "base")
    base_sha = _git(tmp_path, "rev-parse", "HEAD")

    primary.unlink()
    _git(tmp_path, "add", "-u", str(bundle_dir.relative_to(tmp_path)))
    _git(tmp_path, "commit", "--quiet", "-m", "delete primary only")

    skip_without_posix_shell()
    result = run_posix_shell(
        _changed_bundle_discovery_script(),
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "BASE_SHA": base_sha},
    )

    assert result.returncode != 0
    assert "file=results-data/bundles/duckdb/submission-manifest.json" in result.stdout
    assert "also requires deleting its referencing legacy manifest" in result.stdout


def test_deleted_bundle_and_referencing_legacy_manifest_together_is_allowed(tmp_path: Path) -> None:
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.invalid")

    bundle_dir = tmp_path / "results-data" / "bundles" / "duckdb"
    bundle_dir.mkdir(parents=True)
    primary = bundle_dir / "gone.json"
    legacy_manifest = bundle_dir / "submission-manifest.json"
    primary.write_text('{"gone": 1}\n', encoding="utf-8")
    legacy_manifest.write_text('{"bundle_file": "gone.json"}\n', encoding="utf-8")
    _git(tmp_path, "add", str(bundle_dir.relative_to(tmp_path)))
    _git(tmp_path, "commit", "--quiet", "-m", "base")
    base_sha = _git(tmp_path, "rev-parse", "HEAD")

    primary.unlink()
    legacy_manifest.unlink()
    _git(tmp_path, "add", "-u", str(bundle_dir.relative_to(tmp_path)))
    _git(tmp_path, "commit", "--quiet", "-m", "delete primary and legacy manifest")

    skip_without_posix_shell()
    result = run_posix_shell(
        _changed_bundle_discovery_script(),
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "BASE_SHA": base_sha},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


def test_deleted_bundle_with_surviving_legacy_manifest_for_other_bundle_is_allowed(tmp_path: Path) -> None:
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.invalid")

    bundle_dir = tmp_path / "results-data" / "bundles" / "duckdb"
    bundle_dir.mkdir(parents=True)
    surviving = bundle_dir / "keep.json"
    primary = bundle_dir / "gone.json"
    legacy_manifest = bundle_dir / "submission-manifest.json"
    surviving.write_text('{"keep": 1}\n', encoding="utf-8")
    primary.write_text('{"gone": 1}\n', encoding="utf-8")
    legacy_manifest.write_text('{"bundle_file": "keep.json"}\n', encoding="utf-8")
    _git(tmp_path, "add", str(bundle_dir.relative_to(tmp_path)))
    _git(tmp_path, "commit", "--quiet", "-m", "base")
    base_sha = _git(tmp_path, "rev-parse", "HEAD")

    primary.unlink()
    _git(tmp_path, "add", "-u", str(bundle_dir.relative_to(tmp_path)))
    _git(tmp_path, "commit", "--quiet", "-m", "delete gone only")

    skip_without_posix_shell()
    result = run_posix_shell(
        _changed_bundle_discovery_script(),
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "BASE_SHA": base_sha},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


def test_deleted_bundle_with_case_variant_decoy_legacy_manifest_is_rejected(tmp_path: Path) -> None:
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.invalid")

    bundle_dir = "results-data/bundles/duckdb"
    (tmp_path / bundle_dir).mkdir(parents=True)
    primary = tmp_path / bundle_dir / "gone.json"
    primary.write_text('{"gone": 1}\n', encoding="utf-8")
    _git(tmp_path, "add", str(bundle_dir))
    _git(tmp_path, "commit", "--quiet", "-m", "base with primary")
    base_sha = _git(tmp_path, "rev-parse", "HEAD")

    _git(tmp_path, "update-index", "--force-remove", f"{bundle_dir}/gone.json")
    decoy_blob = _hash_object(tmp_path, '{"bundle_file": "unrelated.json"}\n')
    authoritative_blob = _hash_object(tmp_path, '{"bundle_file": "gone.json"}\n')
    _git(tmp_path, "update-index", "--add", "--cacheinfo", f"100644,{decoy_blob},{bundle_dir}/Submission-Manifest.json")
    _git(
        tmp_path,
        "update-index",
        "--add",
        "--cacheinfo",
        f"100644,{authoritative_blob},{bundle_dir}/submission-manifest.json",
    )
    tree = _git(tmp_path, "write-tree")
    head = subprocess.run(
        ["git", "commit-tree", tree, "-p", base_sha, "-m", "delete primary, keep dangling manifest"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    _git(tmp_path, "update-ref", "HEAD", head)

    skip_without_posix_shell()
    result = run_posix_shell(
        _changed_bundle_discovery_script(),
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "BASE_SHA": base_sha, "MERGE_SHA": head},
    )

    assert result.returncode != 0, result.stdout
    assert "file=results-data/bundles/duckdb/submission-manifest.json" in result.stdout
    assert "also requires deleting its referencing legacy manifest" in result.stdout
