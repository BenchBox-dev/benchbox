from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "release_curation_dry_run.py"

spec = importlib.util.spec_from_file_location("release_curation_dry_run", SCRIPT_PATH)
dry_run = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(dry_run)

MAKEFILE = """\
release-cut:
\tsh scripts/release_cut_start.sh "$(VERSION)"
\tgit rm -rf --ignore-unmatch devonly ':(exclude)devonly/keep.txt'
\tgit rm -f --ignore-unmatch AGENTS.md tests/test_dev_tooling.py
\tgit commit --no-verify -m "Release v$(VERSION)"

other:
\tgit rm -f not-curation.txt
"""

READS_STRIPPED = """\
from pathlib import Path

import pytest

pytestmark = pytest.mark.fast


def test_reads_agents_md():
    assert (Path(__file__).resolve().parents[1] / "AGENTS.md").read_text()
"""

READS_SHIPPED = """\
from pathlib import Path

import pytest

pytestmark = pytest.mark.fast


def test_reads_shipped_file():
    assert (Path(__file__).resolve().parents[1] / "shipped.txt").read_text()
"""

PYTEST = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-m", "fast"]


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "devonly").mkdir(parents=True)
    (root / "tests").mkdir()
    files = {
        "Makefile": MAKEFILE,
        "AGENTS.md": "agent rules\n",
        "shipped.txt": "shipped\n",
        "devonly/tool.py": "x = 1\n",
        "devonly/keep.txt": "kept\n",
        "tests/test_shipped.py": READS_SHIPPED,
        "tests/test_dev_tooling.py": READS_STRIPPED,
    }
    for name, text in files.items():
        (root / name).write_text(text)
    _git(root, "init", "-q", "-b", "develop")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "add", ".")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "base")
    return root


def _commit(root: Path, name: str, text: str) -> None:
    (root / name).write_text(text)
    _git(root, "add", name)
    _git(root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", name)


def test_rm_commands_come_only_from_the_release_cut_recipe(tmp_path: Path) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text(MAKEFILE)
    assert dry_run.release_cut_rm_commands(makefile) == [
        ["git", "rm", "-rf", "--ignore-unmatch", "devonly", ":(exclude)devonly/keep.txt"],
        ["git", "rm", "-f", "--ignore-unmatch", "AGENTS.md", "tests/test_dev_tooling.py"],
    ]


def test_repository_rm_commands_cover_development_only_paths() -> None:
    commands = dry_run.release_cut_rm_commands(REPO_ROOT / "Makefile")
    assert commands, "release-cut must curate development-only paths"
    assert all(c[:2] == ["git", "rm"] and "--ignore-unmatch" in c for c in commands)
    paths = {p for c in commands for p in c[2:] if not p.startswith("-")}
    assert {"AGENTS.md", "_project", ".pre-commit-config.yaml"} <= paths


def test_marker_selection_matches_the_release_test_job() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
    assert f'-m "{dry_run.FAST_SELECTION}"' in workflow


def test_curated_tree_applies_removals_and_is_cleaned_up(repo: Path) -> None:
    commands = dry_run.release_cut_rm_commands(repo / "Makefile")
    with dry_run.curated_tree(repo, commands) as tree:
        assert not (tree / "AGENTS.md").exists()
        assert not (tree / "devonly" / "tool.py").exists()
        assert (tree / "devonly" / "keep.txt").exists()
        assert (tree / "shipped.txt").exists()
    assert not tree.exists()
    assert str(tree) not in _git(repo, "worktree", "list")
    assert (repo / "AGENTS.md").exists()


def test_a_new_test_reading_a_stripped_path_fails(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _commit(repo, "tests/test_new_reader.py", READS_STRIPPED)
    assert dry_run.run(repo, [], command=PYTEST) == 1
    assert "release-cut strip list" in capsys.readouterr().err


def test_tests_reading_shipped_paths_pass(repo: Path) -> None:
    assert dry_run.run(repo, ["tests/test_shipped.py"], command=PYTEST) == 0


def test_targets_removed_by_release_cut_are_skipped(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert dry_run.run(repo, ["tests/test_dev_tooling.py"], command=PYTEST) == 0
    assert "skipped (removed by release-cut): tests/test_dev_tooling.py" in capsys.readouterr().out


def test_changed_test_files_lists_only_test_modules(repo: Path) -> None:
    base = _git(repo, "rev-parse", "HEAD").strip()
    _commit(repo, "tests/test_new_reader.py", READS_STRIPPED)
    _commit(repo, "tests/helper.py", "y = 2\n")
    assert dry_run.changed_test_files(repo, base) == ["tests/test_new_reader.py"]


def test_changed_medium_test_runs_under_routine_selection(repo: Path) -> None:
    reads_medium = """\
from pathlib import Path
import pytest
pytestmark = pytest.mark.medium

def test_reads_shipped_medium():
    assert (Path(__file__).resolve().parents[1] / "shipped.txt").read_text()
"""
    _commit(repo, "tests/test_medium_shipped.py", reads_medium)
    assert dry_run.run(repo, ["tests/test_medium_shipped.py"]) == 0


def test_retained_target_with_no_collected_tests_fails(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    reads_slow = """\
from pathlib import Path
import pytest
pytestmark = pytest.mark.slow

def test_reads_shipped_slow():
    assert (Path(__file__).resolve().parents[1] / "shipped.txt").read_text()
"""
    _commit(repo, "tests/test_slow_shipped.py", reads_slow)
    assert dry_run.run(repo, ["tests/test_slow_shipped.py"]) == 1
    assert "Retained test targets were selected, but no tests matched" in capsys.readouterr().err
