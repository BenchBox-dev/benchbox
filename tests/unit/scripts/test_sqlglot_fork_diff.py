from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts import sqlglot_fork_diff

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@pytest.fixture
def fork(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "fork"
    repo.mkdir()
    git(repo, "init", "--quiet")
    (repo / "downstream").mkdir()
    (repo / "downstream" / "patches.json").write_text('{"patches": []}\n', encoding="utf-8")
    (repo / "sqlglot").mkdir()
    (repo / "sqlglot" / "dialects.py").write_text("A = 1\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base", "--quiet")
    old = git(repo, "rev-parse", "HEAD")
    (repo / "downstream" / "patches.json").write_text('{"patches": ["fix: retain 1"]}\n', encoding="utf-8")
    (repo / "sqlglot" / "dialects.py").write_text("A = 2\n", encoding="utf-8")
    git(repo, "commit", "-am", "fix: retain 1", "--quiet")
    return repo, old, git(repo, "rev-parse", "HEAD")


def test_diff_lists_commits_files_and_patches_first(fork: tuple[Path, str, str]) -> None:
    repo, old, new = fork
    text = sqlglot_fork_diff.fork_diff(old, new, str(repo), 50_000)
    assert f"Old fork_sha: `{old}`" in text
    assert f"New fork_sha: `{new}`" in text
    assert "Direction: forward" in text
    assert "fix: retain 1" in text
    assert text.index("downstream/patches.json\n") < text.index("+A = 2")
    assert "+A = 2" in text


def test_diff_flags_rollback_direction(fork: tuple[Path, str, str]) -> None:
    repo, old, new = fork
    text = sqlglot_fork_diff.fork_diff(new, old, str(repo), 50_000)
    assert "Direction: not-descendant" in text


def test_oversized_files_are_listed_not_silently_dropped(fork: tuple[Path, str, str]) -> None:
    repo, old, new = fork
    text = sqlglot_fork_diff.fork_diff(old, new, str(repo), 1)
    assert "Omitted for size (2 files)" in text
    assert "\nsqlglot/dialects.py\n" in text
    assert "+A = 2" not in text


def test_fence_outgrows_backtick_runs_in_fork_content() -> None:
    body = "line\n ```\n<!-- hidden\n````\nend"
    fenced = sqlglot_fork_diff._fence(body, "diff")
    assert fenced.startswith("`````diff\n")
    assert fenced.endswith("\n`````")
    assert sqlglot_fork_diff._fence("plain").startswith("```\n")


def test_markdown_fence_in_patch_cannot_close_the_block(fork: tuple[Path, str, str]) -> None:
    repo, old, new = fork
    (repo / "NOTES.md").write_text("```\n<!-- hidden\n", encoding="utf-8")
    git(repo, "add", "NOTES.md")
    git(repo, "commit", "-m", "notes", "--quiet")
    text = sqlglot_fork_diff.fork_diff(old, git(repo, "rev-parse", "HEAD"), str(repo), 50_000)
    assert "````diff\n" in text
    assert "+```\n+<!-- hidden" in text


def test_main_defaults_old_to_base_ref_pin_and_new_to_working_tree(
    fork: tuple[Path, str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, old, new = fork
    root = tmp_path / "benchbox"
    (root / "benchbox" / "_vendor").mkdir(parents=True)
    pin = root / "benchbox" / "_vendor" / "sqlglot.json"
    pin.write_text(json.dumps({"fork_sha": old}), encoding="utf-8")
    git(root, "init", "--quiet")
    git(root, "add", ".")
    git(root, "commit", "-m", "pin", "--quiet")
    pin.write_text(json.dumps({"fork_sha": new}), encoding="utf-8")
    monkeypatch.setattr(sqlglot_fork_diff, "ROOT", root)
    output = tmp_path / "diff.md"
    status = sqlglot_fork_diff.main(["--base-ref", "HEAD", "--repository", str(repo), "--output", str(output)])
    assert status == 0
    text = output.read_text(encoding="utf-8")
    assert f"Old fork_sha: `{old}`" in text
    assert f"New fork_sha: `{new}`" in text


def test_main_rejects_non_sha_arguments(capsys: pytest.CaptureFixture[str]) -> None:
    assert sqlglot_fork_diff.main(["--old", "main", "--new", "a" * 40]) == 1
    assert "40-character" in capsys.readouterr().err
