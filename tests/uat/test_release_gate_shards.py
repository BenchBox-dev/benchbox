from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from tests.uat import _cli, gate_summary, orchestrator
from tests.uat.config import load_config
from tests.uat.phases.enumerate import enumerate_cells_with_pruning, resolved_platforms
from tests.utilities.paths import REPO_ROOT

pytestmark = pytest.mark.medium

STAGES = gate_summary.EXPECTED_RELEASE_GATE_STAGES
PLATFORMS = ("duckdb", "sqlite")
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-v2.yml"
CONFIGS = REPO_ROOT / "tests" / "uat" / "configs"


@pytest.fixture
def configs_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "configs"
    directory.mkdir()
    for name in STAGES:
        (directory / f"{name}.yaml").write_text(
            yaml.safe_dump(
                {
                    "name": name,
                    "phases": ["execute"],
                    "platforms": {"groups": [], "include": list(PLATFORMS)},
                    "benchmarks": {"groups": [], "include": ["tpch", "ssb"]},
                    "scales": {"rungs": [0.01]},
                }
            ),
            encoding="utf-8",
        )
    return directory


def _row(key: gate_summary.CellKey) -> str:
    platform, benchmark, scale = key
    return json.dumps({"platform": platform, "benchmark": benchmark, "scale": scale, "status": "passed"}) + "\n"


def _write_shard(
    root: Path,
    stage: int,
    platforms: tuple[str, ...],
    plan: gate_summary.StagePlan,
    completed_at: str,
    *,
    lifecycle: str = "",
    drop: int = 0,
    extra_cells: tuple[gate_summary.CellKey, ...] = (),
    **overrides,
) -> Path:
    shard_dir = root / f"stage{stage}-{'-'.join(platforms)}" / "run"
    shard_dir.mkdir(parents=True)
    cells = sorted(key for key in plan.cells if key[0] in platforms)
    pruned = sorted(key for key in plan.pruned if key[0] in platforms)
    cells = cells[drop:] + list(extra_cells)
    (shard_dir / "cells.jsonl").write_text("".join(_row(key) for key in cells), encoding="utf-8")
    (shard_dir / "compatibility_pruned.jsonl").write_text("".join(_row(key) for key in pruned), encoding="utf-8")
    (shard_dir / "cells.jsonl.accounting.json").write_text("{}\n", encoding="utf-8")
    (shard_dir / "uat_lifecycle.log").write_text(lifecycle, encoding="utf-8")
    digests = {
        "cells_jsonl": hashlib.sha256((shard_dir / "cells.jsonl").read_bytes()).hexdigest(),
        "accounting_sidecar": hashlib.sha256((shard_dir / "cells.jsonl.accounting.json").read_bytes()).hexdigest(),
        "lifecycle_log": hashlib.sha256((shard_dir / "uat_lifecycle.log").read_bytes()).hexdigest(),
    }
    kwargs = {
        "config_name": STAGES[stage - 1],
        "source_commit_sha": "abc123",
        "source_dirty": False,
        "container_engine": "docker",
        "completed_at": completed_at,
        "dry_run": False,
        "aborted": False,
        "abort_phase": None,
        "abort_reason": None,
        "phase_exit_codes": {"execute": 0, "validate": 0, "report": 0},
        "accounting": gate_summary.PhaseAccounting(attempted=len(cells), passed=len(cells), total_defined=len(cells)),
        "unreachable_is_estimated": False,
        "validator_clean_rate": 1.0,
        "validator_clean_rate_floor": 1.0,
        "validator_floor_breached": False,
        "cross_scale_clean_pairs": 3,
        "cross_scale_floor": 5,
        "cross_scale_floor_breached": None,
        "artifact_digests": digests,
        "distribution_digest": "d" * 64,
        "shard_platforms": list(platforms),
        "verdict": "green",
    }
    kwargs.update(overrides)
    gate_summary.write_gate_summary(shard_dir, gate_summary.GateSummary(**kwargs))
    return shard_dir


def _docker_up(timestamp: str) -> str:
    return f"{timestamp} [docker] platform=starrocks action=up status=ok\n"


class _Run:
    def __init__(self, tmp_path: Path, configs_dir: Path) -> None:
        self.tmp_path = tmp_path
        self.configs_dir = configs_dir
        self.plan = gate_summary.load_stage_plan(configs_dir / f"{STAGES[0]}.yaml")
        assert {key[0] for key in self.plan.cells} == set(PLATFORMS)

    def stage(self, stage: int, completed: tuple[str, str], **per_shard) -> list[Path]:
        shards = []
        for platform, done in zip(PLATFORMS, completed):
            shards.append(
                _write_shard(self.tmp_path, stage, (platform,), self.plan, done, **per_shard.get(platform, {}))
            )
        return shards

    def check(self, s1: list[Path], s2: list[Path], s3: list[Path], capsys) -> tuple[int, dict]:
        output = self.tmp_path / "evidence.json"
        rc = _cli.main(
            [
                "gate-check",
                "--stage1",
                *map(str, s1),
                "--stage2",
                *map(str, s2),
                "--stage3",
                *map(str, s3),
                "--configs-dir",
                str(self.configs_dir),
                "--output",
                str(output),
            ]
        )
        capsys.readouterr()
        return rc, json.loads(output.read_text(encoding="utf-8"))


@pytest.fixture
def run(tmp_path: Path, configs_dir: Path) -> _Run:
    return _Run(tmp_path, configs_dir)


def _all_green(run: _Run, build: tuple[int, ...] = (1, 2, 3)) -> tuple[list[Path], list[Path], list[Path]]:
    s1 = run.stage(1, ("2026-07-10T10:00:00", "2026-07-10T10:30:00")) if 1 in build else []
    s2 = (
        run.stage(
            2,
            ("2026-07-10T12:00:00", "2026-07-10T12:30:00"),
            **{p: {"lifecycle": _docker_up("2026-07-10T11:00:00")} for p in PLATFORMS},
        )
        if 2 in build
        else []
    )
    s3 = (
        run.stage(
            3,
            ("2026-07-10T14:00:00", "2026-07-10T14:30:00"),
            **{p: {"lifecycle": _docker_up("2026-07-10T13:00:00")} for p in PLATFORMS},
        )
        if 3 in build
        else []
    )
    return s1, s2, s3


def test_sharded_stages_whose_union_equals_the_plan_are_green(run: _Run, capsys):
    rc, payload = run.check(*_all_green(run), capsys)

    assert rc == 0, payload["reasons"]
    assert payload["verdict"] == "green"
    assert payload["stage_shard_platforms"][STAGES[0]] == [["duckdb"], ["sqlite"]]


def test_missing_cell_in_a_shard_is_red(run: _Run, capsys):
    s1 = run.stage(1, ("2026-07-10T10:00:00", "2026-07-10T10:30:00"), sqlite={"drop": 1})
    _, s2, s3 = _all_green(run, build=(2, 3))

    rc, payload = run.check(s1, s2, s3, capsys)

    assert rc == 1
    assert any("missing 1 planned cell" in reason for reason in payload["reasons"]), payload["reasons"]


def test_duplicate_cell_across_shards_is_red(run: _Run, capsys):
    borrowed = next(key for key in sorted(run.plan.cells) if key[0] == "duckdb")
    s1 = run.stage(1, ("2026-07-10T10:00:00", "2026-07-10T10:30:00"), sqlite={"extra_cells": (borrowed,)})
    _, s2, s3 = _all_green(run, build=(2, 3))

    rc, payload = run.check(s1, s2, s3, capsys)

    assert rc == 1
    assert any("duplicate 1 cell" in reason for reason in payload["reasons"]), payload["reasons"]
    assert any("not assigned" in reason for reason in payload["reasons"]), payload["reasons"]


def test_shard_missing_from_the_stage_is_red(run: _Run, capsys):
    s1, s2, s3 = _all_green(run)

    rc, payload = run.check(s1[:1], s2, s3, capsys)

    assert rc == 1
    assert any("sqlite" in reason and "missing" in reason for reason in payload["reasons"]), payload["reasons"]


def test_distribution_digest_mismatch_across_shards_is_red(run: _Run, capsys):
    s1 = run.stage(
        1,
        ("2026-07-10T10:00:00", "2026-07-10T10:30:00"),
        sqlite={"distribution_digest": "e" * 64},
    )
    _, s2, s3 = _all_green(run, build=(2, 3))

    rc, payload = run.check(s1, s2, s3, capsys)

    assert rc == 1
    assert any("distribution digest differs" in reason for reason in payload["reasons"]), payload["reasons"]


def test_source_commit_mismatch_across_shards_is_red(run: _Run, capsys):
    s1 = run.stage(
        1,
        ("2026-07-10T10:00:00", "2026-07-10T10:30:00"),
        duckdb={"source_commit_sha": "other"},
    )
    _, s2, s3 = _all_green(run, build=(2, 3))

    rc, payload = run.check(s1, s2, s3, capsys)

    assert rc == 1
    assert any("source_commit_sha differs" in reason for reason in payload["reasons"]), payload["reasons"]


def test_one_red_shard_makes_the_stage_red(run: _Run, capsys):
    s1 = run.stage(
        1,
        ("2026-07-10T10:00:00", "2026-07-10T10:30:00"),
        sqlite={"verdict": "red", "phase_exit_codes": {"execute": 1}},
    )
    _, s2, s3 = _all_green(run, build=(2, 3))

    rc, payload = run.check(s1, s2, s3, capsys)

    assert rc == 1
    assert payload["stage_verdicts"][STAGES[0]] == "red"
    assert any("shard [sqlite]" in reason and "not green" in reason for reason in payload["reasons"])


def test_docker_start_before_the_slowest_stage1_shard_finished_is_red(run: _Run, capsys):
    s1 = run.stage(1, ("2026-07-10T10:00:00", "2026-07-10T12:10:00"))
    s2 = run.stage(
        2,
        ("2026-07-10T14:00:00", "2026-07-10T14:30:00"),
        duckdb={"lifecycle": _docker_up("2026-07-10T12:00:00")},
        sqlite={"lifecycle": _docker_up("2026-07-10T13:00:00")},
    )
    _, _, s3 = _all_green(run, build=(3,))

    rc, payload = run.check(s1, s2, s3, capsys)

    assert rc == 1
    assert any("stage1 boundary" in violation for violation in payload["ordering_violations"])


def test_stage3_docker_start_before_the_slowest_stage2_shard_finished_is_red(run: _Run, capsys):
    s1, s2, _ = _all_green(run, build=(1, 2))
    s3 = run.stage(
        3,
        ("2026-07-10T15:00:00", "2026-07-10T15:30:00"),
        duckdb={"lifecycle": _docker_up("2026-07-10T13:00:00")},
        sqlite={"lifecycle": _docker_up("2026-07-10T12:10:00")},
    )

    rc, payload = run.check(s1, s2, s3, capsys)

    assert rc == 1
    assert any("stage2 boundary" in violation for violation in payload["ordering_violations"])


def test_docker_shard_without_a_lifecycle_log_is_red(run: _Run, capsys):
    s1, s2, s3 = _all_green(run)
    (s2[1] / "uat_lifecycle.log").unlink()

    rc, payload = run.check(s1, s2, s3, capsys)

    assert rc == 1
    assert any("missing lifecycle log" in violation for violation in payload["ordering_violations"])


def test_cross_scale_floor_is_judged_on_the_union_of_shards(run: _Run, capsys):
    s1 = run.stage(
        1,
        ("2026-07-10T10:00:00", "2026-07-10T10:30:00"),
        duckdb={"cross_scale_clean_pairs": 2},
        sqlite={"cross_scale_clean_pairs": 2},
    )
    _, s2, s3 = _all_green(run, build=(2, 3))

    rc, payload = run.check(s1, s2, s3, capsys)

    assert rc == 1
    assert any("cross-scale clean pairs 4" in reason for reason in payload["reasons"]), payload["reasons"]


def test_one_directory_listed_twice_for_a_stage_is_red(run: _Run, capsys):
    s1, s2, s3 = _all_green(run)

    rc, payload = run.check([s1[0], s1[0]], s2, s3, capsys)

    assert rc == 1
    assert any("listed more than once" in reason for reason in payload["reasons"]), payload["reasons"]


def test_shard_summary_keeps_the_configured_floor_without_breaching_it(tmp_path: Path):
    config = load_config(CONFIGS / f"{STAGES[0]}.yaml")
    report_summary = SimpleNamespace(
        attempted_count=3,
        pass_count=3,
        fail_count=0,
        timeout_count=0,
        unreachable_count=0,
        startup_failed_count=0,
        died_mid_platform_count=0,
        skipped_count=0,
        compatibility_pruned_count=0,
        early_stop_pruned_count=0,
        registry_pruned_count=0,
        total_defined_count=3,
        unvalidated_count=0,
        unreachable_count_is_estimated=False,
        cross_scale_clean_pairs=3,
        cross_scale_floor=None,
        cross_scale_floor_breached=False,
    )
    orchestrator._write_gate_summary_artifact(
        config=config,
        log_dir=tmp_path,
        source_info=orchestrator.RunSourceInfo(commit_sha="abc", commit_short_sha="abc", dirty=False),
        container_engine=None,
        completed_at=orchestrator._dt.datetime.now().astimezone(),
        aborted_phase=None,
        abort_reason=None,
        abort_kind=None,
        phase_exit_codes={"report": 0},
        execute_outcome=None,
        report_summary=report_summary,
        validate_result=None,
        shard_platforms=("sqlite", "duckdb"),
    )

    summary = gate_summary.read_gate_summary(tmp_path / gate_summary.GATE_SUMMARY_FILENAME)

    assert summary.shard_platforms == ["duckdb", "sqlite"]
    assert summary.cross_scale_floor == config.report.cross_scale_coverage_min_pairs
    assert summary.cross_scale_floor_breached is None


def test_sweep_refuses_a_platform_outside_the_config(capsys):
    rc = _cli.main(
        ["sweep", "--config", str(CONFIGS / f"{STAGES[0]}.yaml"), "--platforms", "not-a-platform", "--dry-run"]
    )

    assert rc == 2
    assert "not-a-platform" in capsys.readouterr().err


def _workflow_shards(stage: int) -> list[list[str]]:
    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    include = jobs[f"uat-stage{stage}"]["strategy"]["matrix"]["include"]
    return [entry["platforms"].split(",") for entry in include]


@pytest.mark.parametrize("stage", [1, 2, 3])
def test_workflow_shards_partition_each_release_gate_config(stage: int):
    config = load_config(CONFIGS / f"{STAGES[stage - 1]}.yaml")
    shards = _workflow_shards(stage)

    assigned = [platform for shard in shards for platform in shard]
    assert len(assigned) == len(set(assigned)), "a platform is assigned to more than one shard"
    assert set(assigned) == set(resolved_platforms(config)), "workflow shards drifted from the config platform set"

    full = enumerate_cells_with_pruning(config)
    shard_cells = []
    shard_pruned = []
    for shard in shards:
        enumeration = enumerate_cells_with_pruning(orchestrator.shard_config(config, tuple(shard)))
        shard_cells.extend(enumeration.cells)
        shard_pruned.extend(enumeration.compatibility_pruned)
    assert len(shard_cells) == len(set(shard_cells))
    assert set(shard_cells) == set(full.cells)
    assert len(shard_pruned) == len(set(shard_pruned))
    assert set(shard_pruned) == set(full.compatibility_pruned)


@pytest.mark.parametrize("stage_name", STAGES)
def test_release_gate_packaging_never_needs_a_public_pseudonym_salt(stage_name: str, tmp_path: Path):
    from tests.uat.phases import package

    config = load_config(CONFIGS / f"{stage_name}.yaml")
    assert "package" in config.phases
    assert config.package.submit_terminal_state != "cloud-uploaded"
    result = package.run_package(
        config,
        result_paths=[tmp_path / "r.json"],
        submissions_dir=tmp_path / "subs",
        runner=lambda argv, check=False: SimpleNamespace(returncode=0),
        warn=lambda _msg: None,
        classify_results=False,
    )
    assert result.invocations[0][:3] == ("benchbox", "publish", "run")
    assert "submit" not in result.invocations[0]
