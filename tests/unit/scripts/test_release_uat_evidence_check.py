from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "release_uat_evidence_check.py"
SPEC = importlib.util.spec_from_file_location("release_uat_evidence_check", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
detector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(detector)

TAG = "v0.5.0"


def _shard(root: Path, stage: int, name: str, attempted: int = 4, *, tag: str = TAG) -> Path:
    run = root / f"release-uat-{tag}-stage{stage}-{name}" / "run"
    run.mkdir(parents=True)
    (run / "uat_gate_summary.json").write_text(json.dumps({"accounting": {"attempted": attempted}}))
    return run


def _complete_evidence(root: Path) -> None:
    _shard(root, 1, "duckdb")
    _shard(root, 1, "sqlite", 7)
    _shard(root, 2, "starrocks")
    _shard(root, 3, "olap")


def test_complete_evidence_passes_and_counts_shards_and_cells(tmp_path: Path) -> None:
    _complete_evidence(tmp_path)
    violations, shards, executed = detector.check_evidence(tmp_path, TAG)
    assert (violations, shards, executed) == ([], 4, 19)


def test_an_evidence_root_with_no_shard_directories_fails_every_stage(tmp_path: Path) -> None:
    violations, shards, executed = detector.check_evidence(tmp_path, TAG)
    assert shards == executed == 0
    assert [violation.split(":")[0] for violation in violations] == ["stage 1", "stage 2", "stage 3"]


def test_a_missing_root_is_the_same_as_no_shard_directories(tmp_path: Path) -> None:
    violations, shards, _ = detector.check_evidence(tmp_path / "absent", TAG)
    assert shards == 0 and len(violations) == 3


def test_a_stage_without_any_shard_fails_even_when_other_stages_ran(tmp_path: Path) -> None:
    _shard(tmp_path, 1, "duckdb")
    _shard(tmp_path, 3, "olap")
    violations, _, _ = detector.check_evidence(tmp_path, TAG)
    assert violations == [f"stage 2: no shard evidence directory was downloaded under {tmp_path}"]


def test_a_shard_that_staged_no_run_fails(tmp_path: Path) -> None:
    _complete_evidence(tmp_path)
    (tmp_path / f"release-uat-{TAG}-stage2-lakesail").mkdir()
    violations, _, _ = detector.check_evidence(tmp_path, TAG)
    assert len(violations) == 1
    assert violations[0].startswith(f"release-uat-{TAG}-stage2-lakesail: no run/uat_gate_summary.json")


def test_a_shard_with_zero_attempted_cells_fails_even_when_others_executed(tmp_path: Path) -> None:
    _complete_evidence(tmp_path)
    _shard(tmp_path, 2, "lakesail", 0)
    violations, _, executed = detector.check_evidence(tmp_path, TAG)
    assert violations == [f"release-uat-{TAG}-stage2-lakesail: the shard executed zero cells"]
    assert executed == 19


def test_every_shard_at_zero_cells_also_reports_that_nothing_executed(tmp_path: Path) -> None:
    for stage, name in ((1, "duckdb"), (2, "starrocks"), (3, "olap")):
        _shard(tmp_path, stage, name, 0)
    violations, shards, executed = detector.check_evidence(tmp_path, TAG)
    assert shards == 3 and executed == 0
    assert violations[-1] == "no shard executed any cell"
    assert len(violations) == 4


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        "[]",
        "{}",
        '{"accounting": {}}',
        '{"accounting": {"attempted": "4"}}',
        '{"accounting": {"attempted": true}}',
    ],
)
def test_an_unreadable_or_incomplete_summary_fails(tmp_path: Path, payload: str) -> None:
    _complete_evidence(tmp_path)
    (tmp_path / f"release-uat-{TAG}-stage1-duckdb" / "run" / "uat_gate_summary.json").write_text(payload)
    violations, _, _ = detector.check_evidence(tmp_path, TAG)
    assert len(violations) == 1
    assert violations[0].startswith(f"release-uat-{TAG}-stage1-duckdb: gate summary ")


def test_evidence_of_another_tag_is_not_counted(tmp_path: Path) -> None:
    _shard(tmp_path, 1, "duckdb", tag="v0.5.0rc1")
    violations, shards, _ = detector.check_evidence(tmp_path, TAG)
    assert shards == 0 and len(violations) == 3


def test_a_stray_file_named_like_a_shard_directory_is_not_a_shard(tmp_path: Path) -> None:
    (tmp_path / f"release-uat-{TAG}-stage1-duckdb").write_text("")
    violations, shards, _ = detector.check_evidence(tmp_path, TAG)
    assert shards == 0 and len(violations) == 3


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), "--tag", TAG], capture_output=True, text=True, check=False
    )


def test_the_command_exits_zero_on_complete_evidence(tmp_path: Path) -> None:
    _complete_evidence(tmp_path)
    result = _run(tmp_path)
    assert result.returncode == 0
    assert "4 shard run(s), 19 executed cell(s)" in result.stdout


def test_the_command_exits_nonzero_and_annotates_each_violation(tmp_path: Path) -> None:
    result = _run(tmp_path)
    assert result.returncode == 1
    assert result.stderr.count("::error::") == 3
