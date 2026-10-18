from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
GUARD = REPO_ROOT / "scripts" / "check_untracked_handoffs.sh"


def _repo_with(tmp_path: Path, paths: list[str]) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for rel in paths:
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-f", "--", *paths], check=True)
    (tmp_path / "scripts").mkdir(exist_ok=True)
    shutil.copy(GUARD, tmp_path / "scripts" / GUARD.name)
    return tmp_path


def _run(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", "scripts/check_untracked_handoffs.sh"], cwd=repo, capture_output=True, text=True, check=False
    )


def test_frozen_history_skill_references_and_code_pass(tmp_path: Path) -> None:
    repo = _repo_with(
        tmp_path,
        [
            "_project/handoffs/2026-10-09-ci-devloop-batch-handoff.md",
            ".claude/skills/code/references/handoff.md",
            "docs/agent/handoffs.md",
            "benchbox/handoff_parser.py",
        ],
    )
    assert _run(repo).returncode == 0


@pytest.mark.parametrize(
    "path",
    [
        "_project/handoffs/2026-10-15-new-topic.md",
        ".claude/handoffs/resume.md",
        ".todo-batch/batch-ledger.txt",
        "docs/internal/snowflake-trial-exit-adversarial-review-handoff.md",
        "HANDOFF-tuning-fix-batch.md",
    ],
)
def test_new_tracked_handoff_fails_and_is_named(tmp_path: Path, path: str) -> None:
    result = _run(_repo_with(tmp_path, [path]))
    assert result.returncode == 1
    assert path in result.stderr
    assert "tracker item" in result.stderr


def test_repository_tracks_no_unlisted_handoff() -> None:
    result = subprocess.run(["sh", str(GUARD)], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
