from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "changelog_fragments.py"
MANIFEST = REPO_ROOT / ".github" / "changelog-paths.txt"

pytestmark.append(
    pytest.mark.skipif(
        not (REPO_ROOT / "_project" / "scripts" / "soundness_paths.py").is_file() or not MANIFEST.is_file(),
        reason="_project and the path manifest are removed from the curated release tree",
    )
)

SHIPPED = "benchbox/core/runner.py"
CHANGELOG = "# Changelog\n\n## [Unreleased]\n\n## [0.5.0] - 2026-10-01\n\n- old\n"


def _check(tmp_path: Path, title: str, changed: list[str], *extra: str) -> subprocess.CompletedProcess[str]:
    listing = tmp_path / "changed.txt"
    listing.write_text("\n".join(changed) + "\n", encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--root",
            str(tmp_path),
            "check",
            "--title",
            title,
            "--manifest",
            str(MANIFEST),
            "--changed-files-from",
            str(listing),
            *extra,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    "title",
    [
        "feat: add a thing",
        "fix(runner): repair a thing",
        "perf: speed up a thing",
        "feat!: change a thing",
        "chore(deps): bump a thing",
        "Add a thing without a conventional title",
    ],
)
def test_user_facing_titles_touching_shipped_paths_need_a_fragment(tmp_path: Path, title: str) -> None:
    result = _check(tmp_path, title, [SHIPPED])
    assert result.returncode == 1
    assert "changelog entry required" in result.stdout


@pytest.mark.parametrize(
    "title",
    [
        "docs: explain a thing",
        "test: cover a thing",
        "ci: tune a thing",
        "refactor: reshape a thing",
        "chore: tidy a thing",
        'Revert "feat: add a thing"',
    ],
)
def test_internal_titles_touching_shipped_paths_need_no_fragment(tmp_path: Path, title: str) -> None:
    assert _check(tmp_path, title, [SHIPPED]).returncode == 0


def test_release_identity_change_exempts_a_preparation_pull_request(tmp_path: Path) -> None:
    assert _check(tmp_path, "feat: release 1.2.3", [SHIPPED, "pyproject.toml"]).returncode == 1
    exempt = _check(tmp_path, "feat: release 1.2.3", [SHIPPED, "pyproject.toml"], "--release-identity-changed")
    assert exempt.returncode == 0


@pytest.mark.parametrize("fragment", ["changelog.d/feat-thing.json"])
def test_adding_modifying_or_deleting_a_fragment_satisfies_the_rule(tmp_path: Path, fragment: str) -> None:
    assert _check(tmp_path, "feat: add a thing", [SHIPPED, fragment]).returncode == 0


def test_paths_outside_the_manifest_need_no_fragment(tmp_path: Path) -> None:
    assert _check(tmp_path, "feat: add a thing", ["docs/a.md", "tests/unit/test_a.py"]).returncode == 0


def test_a_line_added_under_unreleased_fails_with_or_without_a_title(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGELOG, encoding="utf-8")
    clean = _check(
        tmp_path, "docs: explain a thing", ["docs/a.md"], "--require-empty-unreleased", "--changelog", str(changelog)
    )
    assert clean.returncode == 0
    changelog.write_text(CHANGELOG.replace("## [Unreleased]\n", "## [Unreleased]\n\n- stray\n", 1), encoding="utf-8")
    dirty = _check(
        tmp_path, "docs: explain a thing", ["docs/a.md"], "--require-empty-unreleased", "--changelog", str(changelog)
    )
    assert dirty.returncode == 1
    assert "[Unreleased]" in dirty.stderr


def test_title_text_is_data_not_shell_syntax(tmp_path: Path) -> None:
    result = _check(tmp_path, 'feat: $(touch pwned) `id` "x"', [SHIPPED, "changelog.d/a.json"])
    assert result.returncode == 0
    assert not (tmp_path / "pwned").exists()


def test_modified_or_deleted_fragments_do_not_satisfy_when_only_added_files_count(tmp_path: Path) -> None:
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[3]
    changed = tmp_path / "changed.txt"
    changed.write_text("benchbox/core/x.py\nchangelog.d/other.json\n", encoding="utf-8")
    added = tmp_path / "added.txt"
    added.write_text("", encoding="utf-8")
    args = [
        sys.executable,
        str(root / "scripts" / "changelog_fragments.py"),
        "required",
        "--title",
        "fix: repair a thing",
        "--changed-files-from",
        str(changed),
        "--added-files-from",
        str(added),
    ]
    refused = subprocess.run(args, capture_output=True, text=True, cwd=root)
    assert refused.returncode == 1
    added.write_text("changelog.d/new.json\n", encoding="utf-8")
    accepted = subprocess.run(args, capture_output=True, text=True, cwd=root)
    assert accepted.returncode == 0, accepted.stderr
