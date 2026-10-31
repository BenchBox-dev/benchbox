from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.uat import docker_assets, managed_runtime, matrix
from tests.uat.config import ExecuteConfig, UATConfig, validate_config
from tests.uat.conftest import docker_verb as _docker_verb, healthy_ps_stdout, platform_reachability
from tests.uat.docker_path_helpers import compose_path_ends_with
from tests.uat.phases import (
    enumerate as enum_phase,
    execute as exec_phase,
    package as package_phase,
    preflight as preflight_phase,
    report as report_phase,
)
from tests.uat.preflight_budget import (
    MemorySnapshot,
    check_memory_headroom,
    format_memory_headroom_failure,
)
from tests.uat.runner import CellResult, classify_for_submit, submit_state_is_cell_failure

pytestmark = pytest.mark.medium


def _write_submit_result(path: Path, *, failed: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "2.1",
        "run": {
            "id": "uat-test",
            "timestamp": "2026-01-01T00:00:00",
            "total_duration_ms": 1,
            "query_time_ms": 1,
            "iterations": 1,
            "streams": 1,
        },
        "benchmark": {"id": "tpch", "name": "TPC-H", "scale_factor": 0.01, "test_type": "power"},
        "platform": {"name": "DuckDB"},
        "summary": {
            "queries": {"total": 1, "passed": 0 if failed else 1, "failed": failed},
            "validation": {"status": "failed" if failed else "passed"},
        },
        "queries": [{"id": "Q1", "status": "ERROR" if failed else "SUCCESS", "ms": 1}],
        "phases": {},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _cfg(payload: dict) -> UATConfig:
    return validate_config({"name": "phase-test", **payload})


def test_preflight_aborts_below_min_free_space(tmp_path):
    with patch.object(preflight_phase, "free_space_gib", return_value=1.0):
        result = preflight_phase.run_preflight(
            free_space_path=tmp_path,
            free_space_min_gib=5.0,
        )
    assert result.aborted is True
    assert "free space" in (result.abort_reason or "")


def test_preflight_warns_on_high_load(tmp_path):
    with (
        patch.object(preflight_phase, "free_space_gib", return_value=100.0),
        patch.object(preflight_phase, "host_load_1m", return_value=20.0),
        patch.object(preflight_phase, "docker_reachable", return_value=True),
    ):
        result = preflight_phase.run_preflight(
            free_space_path=tmp_path,
            noisy_neighbor_warn_load=8.0,
        )
    assert result.aborted is False
    assert any("host load" in w for w in result.warnings)


def test_enumerate_filters_dataframe_against_sql_only():
    raw = {
        "platforms": {"include": ["polars-df"]},
        "benchmarks": {"include": ["vector_search", "tpch"]},
        "scales": {"rungs": [0.01]},
    }
    cells = enum_phase.enumerate_cells(_cfg(raw))
    benches = {c.benchmark for c in cells}
    assert "vector_search" not in benches
    assert "tpch" in benches


def test_enumerate_records_compatibility_pruned_cells():
    raw = {
        "platforms": {"include": ["polars-df"]},
        "benchmarks": {"include": ["vector_search", "tpch"]},
        "scales": {"rungs": [0.01, 0.1]},
    }

    result = enum_phase.enumerate_cells_with_pruning(_cfg(raw))

    assert {c.benchmark for c in result.cells} == {"tpch"}
    assert len(result.compatibility_pruned) == 2
    pruned = result.compatibility_pruned[0]
    assert pruned.platform == "polars-df"
    assert pruned.benchmark == "vector_search"
    assert pruned.rule_id == "uat.compat.dataframe.sql_only_benchmark"
    assert result.candidate_count == len(result.cells) + len(result.compatibility_pruned)


def test_enumerate_uses_registry_supports_dataframe_without_name_fallback():
    raw = {
        "platforms": {"include": ["polars-df"]},
        "benchmarks": {"include": ["vector_search"]},
        "scales": {"rungs": [0.01]},
    }
    benchmarks = {
        "vector_search": matrix.BenchmarkInfo(
            benchmark_id="vector_search",
            category="AI/ML",
            default_scale=0.01,
            min_scale=0.01,
            scale_options=(0.01,),
            supports_dataframe=True,
        )
    }

    result = enum_phase.enumerate_cells_with_pruning(_cfg(raw), benchmarks=benchmarks)

    assert [(c.platform, c.benchmark, c.scale) for c in result.cells] == [("polars-df", "vector_search", 0.01)]
    assert result.compatibility_pruned == ()


def test_enumerate_records_registry_benchmark_gates():
    raw = {
        "platforms": {"include": ["lakesail"]},
        "benchmarks": {"include": ["ai_primitives", "metadata_primitives", "vector_search", "tpch"]},
        "scales": {"rungs": [0.01]},
    }

    result = enum_phase.enumerate_cells_with_pruning(_cfg(raw))

    assert {c.benchmark for c in result.cells} == {"tpch"}
    assert len(result.compatibility_pruned) == 3
    pruned_by_benchmark = {c.benchmark: c for c in result.compatibility_pruned}
    assert pruned_by_benchmark["ai_primitives"].platform == "lakesail"
    assert pruned_by_benchmark["ai_primitives"].rule_id == "uat.compat.lakesail.ai_primitives.benchmark_gate"
    assert pruned_by_benchmark["metadata_primitives"].rule_id == (
        "uat.compat.lakesail.metadata_primitives.benchmark_gate"
    )
    assert pruned_by_benchmark["vector_search"].rule_id == "uat.compat.lakesail.vector_search.benchmark_gate"
    assert pruned_by_benchmark["metadata_primitives"].evidence.startswith("benchbox.sql_compat benchmark_gate")


def test_enumerate_records_datafusion_clickhouse_pruned_benchmark_gates():
    raw = {
        "platforms": {"include": ["datafusion", "clickhouse-local"]},
        "benchmarks": {
            "include": [
                "write_primitives",
                "transaction_primitives",
                "ai_primitives",
                "metadata_primitives",
                "vector_search",
                "tpch",
            ]
        },
        "scales": {"rungs": [0.01]},
    }

    result = enum_phase.enumerate_cells_with_pruning(_cfg(raw))

    assert {(c.platform, c.benchmark) for c in result.cells} == {
        ("clickhouse-local", "vector_search"),
        ("clickhouse-local", "tpch"),
        ("datafusion", "metadata_primitives"),
        ("datafusion", "tpch"),
    }
    pruned = {(c.platform, c.benchmark): c for c in result.compatibility_pruned}
    expected_pruned = {
        ("datafusion", "write_primitives"),
        ("datafusion", "transaction_primitives"),
        ("datafusion", "ai_primitives"),
        ("datafusion", "vector_search"),
        ("clickhouse-local", "write_primitives"),
        ("clickhouse-local", "transaction_primitives"),
        ("clickhouse-local", "ai_primitives"),
        ("clickhouse-local", "metadata_primitives"),
    }
    assert set(pruned) == expected_pruned
    for platform, benchmark in expected_pruned:
        assert pruned[(platform, benchmark)].rule_id == f"uat.compat.{platform}.{benchmark}.benchmark_gate"
        assert pruned[(platform, benchmark)].status == "blocked"
    assert "DataFusion" in pruned[("datafusion", "transaction_primitives")].reason
    assert "AI primitives" in pruned[("clickhouse-local", "ai_primitives")].reason
    assert "SHOW USERS" in pruned[("clickhouse-local", "metadata_primitives")].reason
    assert result.candidate_count == len(result.cells) + len(result.compatibility_pruned)


def test_enumerate_records_dataframe_pruned_mutation_and_maintenance_gates():
    raw = {
        "platforms": {"include": ["polars-df", "pandas-df", "pyspark-df", "dask-df", "datafusion-df"]},
        "benchmarks": {"include": ["write_primitives", "transaction_primitives", "tpcdi", "tpch"]},
        "scales": {"rungs": [0.01]},
    }

    result = enum_phase.enumerate_cells_with_pruning(_cfg(raw))

    cell_pairs = {(c.platform, c.benchmark) for c in result.cells}
    for platform in ("polars-df", "pandas-df", "pyspark-df"):
        assert (platform, "write_primitives") in cell_pairs
        assert (platform, "tpch") in cell_pairs
    for platform in ("dask-df", "datafusion-df"):
        assert (platform, "write_primitives") not in cell_pairs
        assert (platform, "tpch") in cell_pairs

    pruned = {(c.platform, c.benchmark): c for c in result.compatibility_pruned}
    for platform in matrix.DATAFRAME_PLATFORMS:
        assert pruned[(platform, "transaction_primitives")].rule_id == (
            f"uat.compat.{platform}.transaction_primitives.benchmark_gate"
        )
        assert pruned[(platform, "tpcdi")].rule_id == f"uat.compat.{platform}.tpcdi.benchmark_gate"
        assert "transaction" in pruned[(platform, "tpcdi")].reason.lower()
    for platform in ("dask-df", "datafusion-df"):
        assert pruned[(platform, "write_primitives")].rule_id == (
            f"uat.compat.{platform}.write_primitives.benchmark_gate"
        )
    assert result.candidate_count == len(result.cells) + len(result.compatibility_pruned)


def test_enumerate_keeps_release_gate_runtime_envelopes_for_diagnostic_sweeps():
    raw = {
        "platforms": {"include": ["pg-duckdb", "pg-mooncake", "timescaledb"]},
        "benchmarks": {
            "include": [
                "ai_primitives",
                "datavault",
                "joinorder",
                "read_primitives",
                "tpcds",
                "tpcds_obt",
                "vector_search",
                "tpch",
            ]
        },
        "scales": {"rungs": [0.01]},
    }

    result = enum_phase.enumerate_cells_with_pruning(_cfg(raw))

    cell_pairs = {(c.platform, c.benchmark) for c in result.cells}
    for platform in ("pg-duckdb", "pg-mooncake", "timescaledb"):
        assert (platform, "joinorder") in cell_pairs
        assert (platform, "tpcds_obt") in cell_pairs
    assert ("timescaledb", "datavault") in cell_pairs
    pruned_pairs = {(c.platform, c.benchmark) for c in result.compatibility_pruned}
    assert ("pg-duckdb", "ai_primitives") in pruned_pairs
    assert ("pg-mooncake", "tpcds") in pruned_pairs
    assert all(c.rule_id.endswith(".benchmark_gate") for c in result.compatibility_pruned)


def test_enumerate_records_pg_family_release_gate_compatibility_pruning():
    from benchbox.core.platform_registry import PlatformRegistry

    raw = {
        "platforms": {"include": ["pg-duckdb", "pg-mooncake", "timescaledb"]},
        "benchmarks": {
            "include": [
                "ai_primitives",
                "datavault",
                "joinorder",
                "read_primitives",
                "tpcds",
                "tpcds_obt",
                "vector_search",
                "tpch",
            ]
        },
        "compatibility": {"release_gate_runtime_envelopes": True},
        "scales": {"rungs": [0.01]},
    }

    result = enum_phase.enumerate_cells_with_pruning(_cfg(raw))

    assert {(c.platform, c.benchmark) for c in result.cells} == {
        ("pg-duckdb", "tpch"),
        ("pg-duckdb", "datavault"),
        ("pg-duckdb", "tpcds"),
        ("pg-mooncake", "tpch"),
        ("pg-mooncake", "datavault"),
        ("timescaledb", "tpch"),
        ("timescaledb", "tpcds"),
    }
    assert len(result.compatibility_pruned) == 17
    pruned = {(c.platform, c.benchmark): c for c in result.compatibility_pruned}
    assert pruned[("pg-mooncake", "tpcds")].rule_id == "uat.compat.pg-mooncake.tpcds.benchmark_gate"
    assert pruned[("timescaledb", "datavault")].rule_id == (
        "uat.compat.timescaledb.datavault.release_gate_runtime_envelope"
    )
    for platform in ("pg-duckdb", "pg-mooncake", "timescaledb"):
        caps = PlatformRegistry.get_platform_capabilities(platform)
        assert caps is not None
        assert "joinorder" not in caps.unsupported_benchmarks
        assert "tpcds_obt" not in caps.unsupported_benchmarks
        assert pruned[(platform, "ai_primitives")].rule_id == f"uat.compat.{platform}.ai_primitives.benchmark_gate"
        assert pruned[(platform, "joinorder")].rule_id == (
            f"uat.compat.{platform}.joinorder.release_gate_runtime_envelope"
        )
        assert pruned[(platform, "read_primitives")].rule_id == (
            f"uat.compat.{platform}.read_primitives.benchmark_gate"
        )
        assert pruned[(platform, "tpcds_obt")].rule_id == (
            f"uat.compat.{platform}.tpcds_obt.release_gate_runtime_envelope"
        )
        assert pruned[(platform, "vector_search")].rule_id == f"uat.compat.{platform}.vector_search.benchmark_gate"
    timescaledb_caps = PlatformRegistry.get_platform_capabilities("timescaledb")
    assert timescaledb_caps is not None
    assert "datavault" not in timescaledb_caps.unsupported_benchmarks


def test_enumerate_prunes_sqlite_tpcds_obt_release_gate_scale_ladder():
    raw = {
        "platforms": {"include": ["sqlite"]},
        "benchmarks": {"include": ["tpcds_obt"]},
        "compatibility": {"release_gate_runtime_envelopes": True},
        "scales": {"rungs": [0.01, 0.1, 1.0]},
    }

    result = enum_phase.enumerate_cells_with_pruning(_cfg(raw))

    assert result.cells == ()
    assert [(cell.platform, cell.benchmark, cell.scale) for cell in result.compatibility_pruned] == [
        ("sqlite", "tpcds_obt", 0.01),
        ("sqlite", "tpcds_obt", 0.1),
        ("sqlite", "tpcds_obt", 1.0),
    ]
    for cell in result.compatibility_pruned:
        assert cell.rule_id == "uat.compat.sqlite.tpcds_obt.release_gate_runtime_envelope"
        assert cell.status == "blocked"
        assert "1200s" in cell.reason
        assert "518-column" in cell.reason
        assert "PR #1904" in cell.evidence


def test_enumerate_prunes_sqlite_tpcds_release_gate_scale_ladder():
    raw = {
        "platforms": {"include": ["sqlite"]},
        "benchmarks": {"include": ["tpcds"]},
        "compatibility": {"release_gate_runtime_envelopes": True},
        "scales": {"rungs": [0.01, 0.1, 1.0]},
    }

    result = enum_phase.enumerate_cells_with_pruning(_cfg(raw))

    assert result.cells == ()
    assert [(cell.platform, cell.benchmark, cell.scale) for cell in result.compatibility_pruned] == [
        ("sqlite", "tpcds", 0.01),
        ("sqlite", "tpcds", 0.1),
        ("sqlite", "tpcds", 1.0),
    ]
    for cell in result.compatibility_pruned:
        assert cell.rule_id == "uat.compat.sqlite.tpcds.release_gate_runtime_envelope"
        assert cell.status == "blocked"
        assert "1200s" in cell.reason
        assert "query 13" in cell.reason
        assert "2026-08-25" in cell.evidence


def test_enumerate_keeps_sqlite_tpcds_without_release_gate_runtime_envelopes():
    raw = {
        "platforms": {"include": ["sqlite"]},
        "benchmarks": {"include": ["tpcds"]},
        "scales": {"rungs": [0.01, 0.1, 1.0]},
    }

    result = enum_phase.enumerate_cells_with_pruning(_cfg(raw))

    assert [(cell.platform, cell.benchmark, cell.scale) for cell in result.cells] == [
        ("sqlite", "tpcds", 0.01),
        ("sqlite", "tpcds", 0.1),
        ("sqlite", "tpcds", 1.0),
    ]
    assert result.compatibility_pruned == ()


def test_enumerate_prunes_datafusion_datavault_release_gate_scale_ladder():
    raw = {
        "platforms": {"include": ["datafusion"]},
        "benchmarks": {"include": ["datavault"]},
        "compatibility": {"release_gate_runtime_envelopes": True},
        "scales": {"rungs": [0.01, 0.1, 1.0]},
    }

    result = enum_phase.enumerate_cells_with_pruning(_cfg(raw))

    assert result.cells == ()
    assert [(cell.platform, cell.benchmark, cell.scale) for cell in result.compatibility_pruned] == [
        ("datafusion", "datavault", 0.01),
        ("datafusion", "datavault", 0.1),
        ("datafusion", "datavault", 1.0),
    ]
    assert {cell.rule_id for cell in result.compatibility_pruned} == {
        "uat.compat.datafusion.datavault.release_gate_runtime_envelope"
    }
    assert all("query 18" in cell.reason for cell in result.compatibility_pruned)
    assert all("2026-08-25" in cell.evidence for cell in result.compatibility_pruned)


def test_enumerate_keeps_datafusion_datavault_without_release_gate_runtime_envelopes():
    raw = {
        "platforms": {"include": ["datafusion"]},
        "benchmarks": {"include": ["datavault"]},
        "scales": {"rungs": [0.01, 0.1, 1.0]},
    }

    result = enum_phase.enumerate_cells_with_pruning(_cfg(raw))

    assert [(cell.platform, cell.benchmark, cell.scale) for cell in result.cells] == [
        ("datafusion", "datavault", 0.01),
        ("datafusion", "datavault", 0.1),
        ("datafusion", "datavault", 1.0),
    ]
    assert result.compatibility_pruned == ()


def test_enumerate_keeps_sqlite_tpcds_obt_without_release_gate_runtime_envelopes():
    raw = {
        "platforms": {"include": ["sqlite"]},
        "benchmarks": {"include": ["tpcds_obt"]},
        "scales": {"rungs": [0.01]},
    }

    result = enum_phase.enumerate_cells_with_pruning(_cfg(raw))

    assert [(cell.platform, cell.benchmark, cell.scale) for cell in result.cells] == [
        ("sqlite", "tpcds_obt", 1.0),
    ]
    assert result.compatibility_pruned == ()


def test_enumerate_preserves_explicit_empty_include_as_default_suppression():
    no_platforms = enum_phase.enumerate_cells(
        _cfg(
            {
                "platforms": {"include": []},
                "benchmarks": {"include": ["tpch"]},
                "scales": {"rungs": [0.01]},
            }
        )
    )
    no_benchmarks = enum_phase.enumerate_cells(
        _cfg(
            {
                "platforms": {"include": ["duckdb"]},
                "benchmarks": {"include": []},
                "scales": {"rungs": [0.01]},
            }
        )
    )

    assert no_platforms == []
    assert no_benchmarks == []


def test_enumerate_honours_scale_options():
    raw = {
        "platforms": {"include": ["duckdb"]},
        "benchmarks": {"include": ["tpch"]},
        "scales": {"rungs": [0.01, 0.1, 1.0, 50.0, 100.0]},
    }
    cells = enum_phase.enumerate_cells(_cfg(raw))
    scales = {c.scale for c in cells}
    assert 50.0 not in scales
    assert {0.01, 0.1, 1.0, 100.0}.issubset(scales)


def test_enumerate_override_replaces_rungs():
    from dataclasses import replace

    raw = {
        "platforms": {"include": ["duckdb"]},
        "benchmarks": {"include": ["tpch"]},
        "scales": {"rungs": [0.01, 0.1, 1.0]},
    }
    cfg = _cfg(raw)
    cfg = replace(cfg, scales=replace(cfg.scales, override=0.1))
    cells = enum_phase.enumerate_cells(cfg)
    assert {c.scale for c in cells} == {0.1}


def _stub_runner_factory(elapsed_map: dict[float, float], pass_map: dict[float, bool]):

    def fake_runner(platform, benchmark, scale, **kwargs):
        return CellResult(
            platform=platform,
            benchmark=benchmark,
            scale=scale,
            status="passed" if pass_map.get(scale, True) else "failed",
            exit_code=0 if pass_map.get(scale, True) else 1,
            elapsed_s=elapsed_map.get(scale, 1.0),
            log_path=Path("/tmp/uat-test.log"),
            result_path=None,
        )

    return fake_runner


def test_execute_walks_ladder_and_prunes_after_slow_rung(tmp_path):
    cfg = validate_config(
        {
            "name": "fake",
            "platforms": {"include": ["duckdb"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01, 0.1, 1.0]},
            "execute": {"early_stop_after_s": 5},
        }
    )
    runner = _stub_runner_factory(
        elapsed_map={0.01: 1.0, 0.1: 100.0, 1.0: 1.0},
        pass_map={0.01: True, 0.1: True, 1.0: True},
    )
    outcome = exec_phase.run_execute(
        cfg,
        log_dir=tmp_path,
        databases_root=tmp_path / "databases",
        runner=runner,
    )
    scales_run = {r.scale for r in outcome.results}
    pruned_scales = {c.scale for c in outcome.pruned}
    assert scales_run == {0.01, 0.1}
    assert 1.0 in pruned_scales


def test_execute_asserts_if_parallel_platforms_bypasses_yaml_validation(tmp_path):
    cfg = UATConfig(name="parallel-bypass", execute=ExecuteConfig(parallel_platforms=True))

    with pytest.raises(AssertionError, match="parallel_platforms must remain False"):
        exec_phase.run_execute(
            cfg,
            log_dir=tmp_path,
            databases_root=tmp_path / "databases",
            runner=_stub_runner_factory({}, {}),
        )


def test_execute_skips_unreachable_platform(tmp_path):
    cfg = validate_config(
        {
            "name": "fake",
            "platforms": {"include": ["postgresql"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
        }
    )
    with platform_reachability(False):
        runner = _stub_runner_factory({}, {})
        outcome = exec_phase.run_execute(
            cfg,
            log_dir=tmp_path,
            databases_root=tmp_path / "databases",
            runner=runner,
        )
    assert len(outcome.results) == 0
    assert len(outcome.skipped_unreachable) == 1
    assert outcome.exit_code() == 1


def _docker_platform_from_argv(argv: list[str]) -> str:
    if "stats" in argv:
        return "clickhouse-server"
    compose_file = argv[argv.index("-f") + 1]
    if compose_path_ends_with(compose_file, "docker", "clickhouse", "docker-compose.yml"):
        return "clickhouse-server"
    if compose_path_ends_with(compose_file, "docker", "postgresql", "docker-compose.yml"):
        return "postgresql"
    if "pg-duckdb" in compose_file:
        return "pg-duckdb"
    return compose_file


def _healthy_ps_result(argv: list[str]) -> docker_assets.DockerCommandResult:
    return docker_assets.DockerCommandResult(tuple(argv), 0, healthy_ps_stdout(), "")


def _healthy_stats_result(argv: list[str], *, limit: str = "8GiB") -> docker_assets.DockerCommandResult:
    return docker_assets.DockerCommandResult(
        tuple(argv),
        0,
        json.dumps({"Name": "clickhouse", "MemUsage": f"512MB / {limit}"}),
        "",
    )


def test_execute_managed_docker_tears_down_platform_before_next_starts(tmp_path):
    cfg = validate_config(
        {
            "name": "docker smoke",
            "platforms": {"include": ["clickhouse-server", "postgresql"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
            "cleanup": {"docker_manage_platforms": True, "docker_platform_switch": "volumes"},
        }
    )
    sequence: list[tuple[str, str, str]] = []

    def fake_docker(argv, **kwargs):
        action = _docker_verb(argv)
        sequence.append(("docker", action, _docker_platform_from_argv(argv)))
        if action == "ps":
            return _healthy_ps_result(argv)
        if action == "stats":
            return _healthy_stats_result(argv)
        return docker_assets.DockerCommandResult(tuple(argv), 0, "", "")

    def recording_runner(platform, benchmark, scale, **kwargs):
        sequence.append(("cell", "run", platform))
        return CellResult(
            platform=platform,
            benchmark=benchmark,
            scale=scale,
            status="passed",
            exit_code=0,
            elapsed_s=1.0,
            log_path=tmp_path / f"{platform}.log",
            result_path=None,
        )

    with platform_reachability(True):
        outcome = exec_phase.run_execute(
            cfg,
            log_dir=tmp_path,
            databases_root=tmp_path / "databases",
            runner=recording_runner,
            docker_runner=fake_docker,
            free_space_checks_enabled=True,
            free_space_reader=lambda _path: 100.0,
            sleep_fn=lambda _s: None,
        )

    assert outcome.aborted is False
    assert sequence == [
        ("docker", "up", "clickhouse-server"),
        ("docker", "ps", "clickhouse-server"),
        ("docker", "stats", "clickhouse-server"),
        ("cell", "run", "clickhouse-server"),
        ("docker", "down", "clickhouse-server"),
        ("docker", "up", "postgresql"),
        ("docker", "ps", "postgresql"),
        ("cell", "run", "postgresql"),
        ("docker", "down", "postgresql"),
    ]
    assert any(event.action == "down" and event.status == "ok" for event in outcome.docker_events)
    down_commands = [event.result.argv for event in outcome.docker_events if event.action == "down" and event.result]
    assert all("-v" in argv and "--remove-orphans" in argv for argv in down_commands)


def test_execute_teardown_sweeps_leaked_mocker_volumes_when_resolved_engine_is_mocker(tmp_path, monkeypatch):
    monkeypatch.setenv(docker_assets.CONTAINER_CLI_ENV_VAR, "mocker")
    monkeypatch.setattr(docker_assets, "_which_container_cli", lambda cli: f"/opt/homebrew/bin/{cli}")
    docker_assets.resolve_container_cli.cache_clear()
    try:
        cfg = validate_config(
            {
                "name": "mocker volume sweep",
                "platforms": {"include": ["postgresql"]},
                "benchmarks": {"include": ["tpch"]},
                "scales": {"rungs": [0.01]},
                "cleanup": {"docker_manage_platforms": True, "docker_platform_switch": "volumes"},
            }
        )
        volume_calls: list[tuple[str, ...]] = []

        leaked_volume = "benchbox-uat-mocker-volume-sweep-postgresql-postgresql18-data"

        def fake_docker(argv, **kwargs):
            argv_tuple = tuple(argv)
            if argv_tuple == ("mocker", "volume", "ls"):
                volume_calls.append(argv_tuple)
                return docker_assets.DockerCommandResult(argv_tuple, 0, f"local    {leaked_volume}\n", "")
            if argv_tuple[:3] == ("mocker", "volume", "rm"):
                volume_calls.append(argv_tuple)
                return docker_assets.DockerCommandResult(argv_tuple, 0, "", "")
            return docker_assets.DockerCommandResult(argv_tuple, 0, "", "")

        def recording_runner(platform, benchmark, scale, **kwargs):
            return CellResult(
                platform=platform,
                benchmark=benchmark,
                scale=scale,
                status="passed",
                exit_code=0,
                elapsed_s=1.0,
                log_path=tmp_path / f"{platform}.log",
                result_path=None,
            )

        with platform_reachability(True):
            outcome = exec_phase.run_execute(
                cfg,
                log_dir=tmp_path,
                databases_root=tmp_path / "databases",
                runner=recording_runner,
                docker_runner=fake_docker,
                sleep_fn=lambda _s: None,
            )

        assert outcome.aborted is False
        sweep_events = [e for e in outcome.docker_events if e.action == "volume-sweep"]
        assert len(sweep_events) == 1
        assert sweep_events[0].status == "ok"
        assert leaked_volume in sweep_events[0].message
        assert ("mocker", "volume", "ls") in volume_calls
        assert ("mocker", "volume", "rm", leaked_volume) in volume_calls
    finally:
        docker_assets.resolve_container_cli.cache_clear()


def test_execute_teardown_skips_mocker_volume_sweep_for_containers_mode(tmp_path, monkeypatch):
    monkeypatch.setenv(docker_assets.CONTAINER_CLI_ENV_VAR, "mocker")
    monkeypatch.setattr(docker_assets, "_which_container_cli", lambda cli: f"/opt/homebrew/bin/{cli}")
    docker_assets.resolve_container_cli.cache_clear()
    try:
        cfg = validate_config(
            {
                "name": "mocker containers mode",
                "platforms": {"include": ["postgresql"]},
                "benchmarks": {"include": ["tpch"]},
                "scales": {"rungs": [0.01]},
                "cleanup": {"docker_manage_platforms": True, "docker_platform_switch": "containers"},
            }
        )
        calls: list[tuple[str, ...]] = []

        def fake_docker(argv, **kwargs):
            calls.append(tuple(argv))
            return docker_assets.DockerCommandResult(tuple(argv), 0, "", "")

        def recording_runner(platform, benchmark, scale, **kwargs):
            return CellResult(
                platform=platform,
                benchmark=benchmark,
                scale=scale,
                status="passed",
                exit_code=0,
                elapsed_s=1.0,
                log_path=tmp_path / f"{platform}.log",
                result_path=None,
            )

        with platform_reachability(True):
            outcome = exec_phase.run_execute(
                cfg,
                log_dir=tmp_path,
                databases_root=tmp_path / "databases",
                runner=recording_runner,
                docker_runner=fake_docker,
                sleep_fn=lambda _s: None,
            )

        assert not any(e.action == "volume-sweep" for e in outcome.docker_events)
        assert not any(c[:2] == ("mocker", "volume") for c in calls)
    finally:
        docker_assets.resolve_container_cli.cache_clear()


def test_execute_docker_teardown_failure_aborts_before_next_platform(tmp_path):
    cfg = validate_config(
        {
            "name": "docker cleanup failure",
            "platforms": {"include": ["clickhouse-server", "postgresql"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
            "cleanup": {"docker_manage_platforms": True, "docker_platform_switch": "volumes"},
        }
    )
    commands: list[str] = []

    def fake_docker(argv, **kwargs):
        action = _docker_verb(argv)
        commands.append(f"{action}:{_docker_platform_from_argv(argv)}")
        if action == "down":
            return docker_assets.DockerCommandResult(tuple(argv), 1, "", "compose down failed")
        if action == "ps":
            return _healthy_ps_result(argv)
        if action == "stats":
            return _healthy_stats_result(argv)
        return docker_assets.DockerCommandResult(tuple(argv), 0, "", "")

    with platform_reachability(True):
        outcome = exec_phase.run_execute(
            cfg,
            log_dir=tmp_path,
            databases_root=tmp_path / "databases",
            runner=_stub_runner_factory({0.01: 1.0}, {0.01: True}),
            docker_runner=fake_docker,
            free_space_reader=lambda _path: 100.0,
            sleep_fn=lambda _s: None,
        )

    assert outcome.aborted is True
    assert "Docker cleanup failed" in (outcome.abort_reason or "")
    assert commands == [
        "up:clickhouse-server",
        "ps:clickhouse-server",
        "stats:clickhouse-server",
        "down:clickhouse-server",
    ]


def test_execute_aborts_before_compose_up_when_benchmark_runs_dir_is_relative_for_path_mirroring_platform(tmp_path):
    cfg = validate_config(
        {
            "name": "docker relative data dir",
            "platforms": {"include": ["lakesail"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
            "cleanup": {"docker_manage_platforms": True, "docker_platform_switch": "volumes"},
        }
    )
    calls: list[str] = []

    def fake_docker(argv, **kwargs):
        calls.append("up" if "up" in argv else "down")
        return docker_assets.DockerCommandResult(tuple(argv), 0, "", "")

    outcome = exec_phase.run_execute(
        cfg,
        log_dir=tmp_path,
        databases_root=tmp_path / "databases",
        benchmark_runs_dir=Path("relative_runs"),
        runner=_stub_runner_factory({0.01: 1.0}, {0.01: True}),
        docker_runner=fake_docker,
        free_space_reader=lambda _path: 100.0,
    )

    assert outcome.aborted is True
    assert "BENCHBOX_DATA_DIR" in (outcome.abort_reason or "")
    assert "absolute" in (outcome.abort_reason or "")
    assert calls == []


def test_run_docker_teardown_substitutes_absolute_placeholder_when_benchmark_runs_dir_is_relative():
    cfg = validate_config(
        {
            "name": "docker teardown relative data dir",
            "platforms": {"include": ["lakesail"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
            "cleanup": {"docker_manage_platforms": True, "docker_platform_switch": "volumes"},
        }
    )
    spec = docker_assets.docker_platform_spec("lakesail")
    docker_state = exec_phase._DockerPlatformState(
        spec=spec, project_name="benchbox-uat-test-lakesail", started=True, cleanup_status="started"
    )
    captured_env: dict[str, str] = {}

    def fake_docker(argv, **kwargs):
        captured_env.update(kwargs.get("env") or {})
        return docker_assets.DockerCommandResult(tuple(argv), 0, "", "")

    cleanup_status, abort_reason = exec_phase._run_docker_teardown(
        cfg,
        platform="lakesail",
        docker_state=docker_state,
        benchmark_runs_dir=Path("relative_runs"),
        docker_runner=fake_docker,
        docker_events=[],
        log_dir=None,
    )

    assert cleanup_status == "ok"
    assert abort_reason is None
    assert "BENCHBOX_DATA_DIR" in captured_env
    assert Path(captured_env["BENCHBOX_DATA_DIR"]).is_absolute()


def test_execute_docker_startup_failure_records_and_advances_to_next_platform(tmp_path):
    cfg = validate_config(
        {
            "name": "docker startup failure",
            "platforms": {"include": ["clickhouse-server", "postgresql"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
            "cleanup": {"docker_manage_platforms": True, "docker_platform_switch": "volumes"},
        }
    )
    sequence: list[tuple[str, str, str]] = []

    def fake_docker(argv, **kwargs):
        action = _docker_verb(argv)
        platform = _docker_platform_from_argv(argv)
        sequence.append(("docker", action, platform))
        if action == "up" and platform == "clickhouse-server":
            return docker_assets.DockerCommandResult(
                tuple(argv), 1, "", "docker command timed out after 300s", timed_out=True
            )
        if action == "ps":
            return _healthy_ps_result(argv)
        if action == "stats":
            return _healthy_stats_result(argv)
        return docker_assets.DockerCommandResult(tuple(argv), 0, "", "")

    def recording_runner(platform, benchmark, scale, **kwargs):
        sequence.append(("cell", "run", platform))
        return CellResult(
            platform=platform,
            benchmark=benchmark,
            scale=scale,
            status="passed",
            exit_code=0,
            elapsed_s=1.0,
            log_path=tmp_path / f"{platform}.log",
            result_path=None,
        )

    with platform_reachability(True):
        outcome = exec_phase.run_execute(
            cfg,
            log_dir=tmp_path,
            databases_root=tmp_path / "databases",
            runner=recording_runner,
            docker_runner=fake_docker,
            free_space_checks_enabled=True,
            free_space_reader=lambda _path: 100.0,
            sleep_fn=lambda _s: None,
        )

    assert outcome.aborted is False
    assert sequence == [
        ("docker", "up", "clickhouse-server"),
        ("docker", "logs", "clickhouse-server"),
        ("docker", "down", "clickhouse-server"),
        ("docker", "up", "postgresql"),
        ("docker", "ps", "postgresql"),
        ("cell", "run", "postgresql"),
        ("docker", "down", "postgresql"),
    ]
    assert any(cell.platform == "clickhouse-server" for cell in outcome.startup_failed)
    assert len(outcome.skipped_unreachable) == 0
    assert any(
        event.platform == "clickhouse-server" and event.action == "up" and event.status == "failed"
        for event in outcome.docker_events
    )


def test_execute_readiness_settle_reprobes_compose_ps_after_up_wait_reports_success(tmp_path):
    cfg = validate_config(
        {
            "name": "readiness settle",
            "platforms": {"include": ["clickhouse-server", "postgresql"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
            "cleanup": {"docker_manage_platforms": True, "docker_platform_switch": "volumes"},
        }
    )
    sequence: list[tuple[str, str, str]] = []
    sleep_calls: list[float] = []

    def fake_docker(argv, **kwargs):
        action = _docker_verb(argv)
        platform = _docker_platform_from_argv(argv)
        sequence.append(("docker", action, platform))
        if action == "ps" and platform == "clickhouse-server":
            return docker_assets.DockerCommandResult(
                tuple(argv),
                0,
                "NAME                STATUS\nclickhouse-server   Exited (137) 5 seconds ago\n",
                "",
            )
        if action == "ps":
            return _healthy_ps_result(argv)
        if action == "stats":
            return _healthy_stats_result(argv)
        return docker_assets.DockerCommandResult(tuple(argv), 0, "", "")

    def recording_runner(platform, benchmark, scale, **kwargs):
        sequence.append(("cell", "run", platform))
        return CellResult(
            platform=platform,
            benchmark=benchmark,
            scale=scale,
            status="passed",
            exit_code=0,
            elapsed_s=1.0,
            log_path=tmp_path / f"{platform}.log",
            result_path=None,
        )

    with platform_reachability(True):
        outcome = exec_phase.run_execute(
            cfg,
            log_dir=tmp_path,
            databases_root=tmp_path / "databases",
            runner=recording_runner,
            docker_runner=fake_docker,
            sleep_fn=sleep_calls.append,
        )

    assert not any(entry[:2] == ("cell", "run") and entry[2] == "clickhouse-server" for entry in sequence)
    assert any(cell.platform == "clickhouse-server" for cell in outcome.startup_failed)
    assert not any(r.platform == "clickhouse-server" for r in outcome.results)
    assert sleep_calls == [10, 10]
    assert outcome.aborted is False
    assert any(r.platform == "postgresql" and r.status == "passed" for r in outcome.results)
    assert any(
        event.platform == "clickhouse-server" and event.action == "readiness" and event.status == "failed"
        for event in outcome.docker_events
    )
    readiness_event = next(e for e in outcome.docker_events if e.action == "readiness")
    assert "clickhouse-server" in readiness_event.message
    assert "not ready" in readiness_event.message


def test_execute_readiness_check_fails_when_platform_unreachable_after_settle(tmp_path):
    cfg = validate_config(
        {
            "name": "readiness unreachable",
            "platforms": {"include": ["clickhouse-server"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
            "cleanup": {"docker_manage_platforms": True, "docker_platform_switch": "volumes"},
        }
    )

    def fake_docker(argv, **kwargs):
        action = _docker_verb(argv)
        if action == "ps":
            return _healthy_ps_result(argv)
        if action == "stats":
            return _healthy_stats_result(argv)
        return docker_assets.DockerCommandResult(tuple(argv), 0, "", "")

    def fail_runner(platform, benchmark, scale, **kwargs):
        raise AssertionError("no cell should run against an unreachable stack")

    with platform_reachability(False):
        outcome = exec_phase.run_execute(
            cfg,
            log_dir=tmp_path,
            databases_root=tmp_path / "databases",
            runner=fail_runner,
            docker_runner=fake_docker,
            sleep_fn=lambda _s: None,
        )

    assert outcome.aborted is False
    assert len(outcome.results) == 0
    assert any(cell.platform == "clickhouse-server" for cell in outcome.startup_failed)
    readiness_event = next(e for e in outcome.docker_events if e.action == "readiness")
    assert readiness_event.status == "failed"
    assert "reachability probe" in readiness_event.message


def test_execute_readiness_failure_captures_container_logs(tmp_path):
    cfg = validate_config(
        {
            "name": "readiness logs",
            "platforms": {"include": ["clickhouse-server"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
            "cleanup": {"docker_manage_platforms": True, "docker_platform_switch": "volumes"},
        }
    )

    def fake_docker(argv, **kwargs):
        action = _docker_verb(argv)
        if action == "ps":
            return _healthy_ps_result(argv)
        if action == "stats":
            return _healthy_stats_result(argv)
        if action == "logs":
            return docker_assets.DockerCommandResult(tuple(argv), 0, "server crashed: bad config\n", "")
        return docker_assets.DockerCommandResult(tuple(argv), 0, "", "")

    def fail_runner(platform, benchmark, scale, **kwargs):
        raise AssertionError("no cell should run against an unreachable stack")

    with platform_reachability(False):
        outcome = exec_phase.run_execute(
            cfg,
            log_dir=tmp_path,
            databases_root=tmp_path / "databases",
            runner=fail_runner,
            docker_runner=fake_docker,
            sleep_fn=lambda _s: None,
        )

    assert any(event.action == "logs" and event.status == "ok" for event in outcome.docker_events)
    assert "server crashed: bad config" in (tmp_path / "container-logs-clickhouse-server.log").read_text(
        encoding="utf-8"
    )


def test_execute_readiness_settle_uses_configured_docker_settle_s(tmp_path):
    cfg = validate_config(
        {
            "name": "custom settle",
            "platforms": {"include": ["clickhouse-server"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
            "cleanup": {
                "docker_manage_platforms": True,
                "docker_platform_switch": "volumes",
                "docker_settle_s": 3,
            },
        }
    )
    sleep_calls: list[float] = []

    def fake_docker(argv, **kwargs):
        action = _docker_verb(argv)
        if action == "ps":
            return _healthy_ps_result(argv)
        if action == "stats":
            return _healthy_stats_result(argv)
        return docker_assets.DockerCommandResult(tuple(argv), 0, "", "")

    with platform_reachability(True):
        outcome = exec_phase.run_execute(
            cfg,
            log_dir=tmp_path,
            databases_root=tmp_path / "databases",
            runner=_stub_runner_factory({0.01: 1.0}, {0.01: True}),
            docker_runner=fake_docker,
            sleep_fn=sleep_calls.append,
        )

    assert outcome.aborted is False
    assert sleep_calls == [3]


def test_execute_readiness_check_skipped_for_dry_run(tmp_path):
    cfg = validate_config(
        {
            "name": "dry run readiness",
            "dry_run": True,
            "platforms": {"include": ["clickhouse-server"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
            "cleanup": {"docker_manage_platforms": True, "docker_platform_switch": "volumes"},
        }
    )
    sleep_calls: list[float] = []

    def fake_docker(argv, **kwargs):
        return docker_assets.DockerCommandResult(tuple(argv), 0, "", "", dry_run=True)

    with platform_reachability(True):
        outcome = exec_phase.run_execute(
            cfg,
            log_dir=tmp_path,
            databases_root=tmp_path / "databases",
            runner=_stub_runner_factory({0.01: 1.0}, {0.01: True}),
            docker_runner=fake_docker,
            sleep_fn=sleep_calls.append,
        )

    assert outcome.aborted is False
    assert sleep_calls == []
    assert not any(event.action in {"ps", "readiness"} for event in outcome.docker_events)


def _memory_reader(free_gib, swap_used_percent=0.0):
    return lambda: MemorySnapshot(free_gib=free_gib, swap_used_percent=swap_used_percent)


def _managed_docker_cfg(name, **overrides):
    payload = {
        "name": name,
        "platforms": {"include": ["clickhouse-server"]},
        "benchmarks": {"include": ["tpch"]},
        "scales": {"rungs": [0.01]},
        "cleanup": {"docker_manage_platforms": True, "docker_platform_switch": "volumes"},
    }
    for section, values in overrides.items():
        payload.setdefault(section, {}).update(values)
    return validate_config(payload)


def _healthy_fake_docker(argv, **kwargs):
    action = _docker_verb(argv)
    if action == "ps":
        return _healthy_ps_result(argv)
    if action == "stats":
        return _healthy_stats_result(argv)
    return docker_assets.DockerCommandResult(tuple(argv), 0, "", "")


def test_execute_starrocks_requires_backend_liveness_and_runtime_memory(monkeypatch, tmp_path):
    monkeypatch.setenv(docker_assets.CONTAINER_CLI_ENV_VAR, "mocker")
    docker_assets.resolve_container_cli.cache_clear()
    calls: list[tuple[str, ...]] = []
    first_application_probe = True

    def fake_docker(argv, **kwargs):
        nonlocal first_application_probe
        call = tuple(argv)
        calls.append(call)
        if "ps" in call:
            return _healthy_ps_result(argv)
        if "exec" in call and first_application_probe:
            first_application_probe = False
            return docker_assets.DockerCommandResult(call, 1, "", "BE heartbeat pending")
        if "stats" in call:
            return _healthy_stats_result(argv, limit="8GiB")
        return docker_assets.DockerCommandResult(call, 0, "", "")

    cfg = _managed_docker_cfg(
        "starrocks readiness",
        platforms={"include": ["starrocks"]},
        preflight={"starrocks_memory_limit": "4g"},
    )
    with platform_reachability(True):
        outcome = exec_phase.run_execute(
            cfg,
            log_dir=tmp_path,
            databases_root=tmp_path / "databases",
            runner=_stub_runner_factory({0.01: 1.0}, {0.01: True}),
            docker_runner=fake_docker,
            memory_reader=_memory_reader(16.0),
            sleep_fn=lambda _s: None,
        )

    assert outcome.aborted is False
    assert not outcome.startup_failed
    assert not any("update" in call and "--memory" in call for call in calls)
    assert any(
        event.action == "resource-reconcile" and "already matches" in event.message for event in outcome.docker_events
    )
    readiness_call = next(call for call in calls if "exec" in call)
    assert "SHOW BACKENDS\\G" in readiness_call[-1]
    assert any(event.action == "application-readiness" and event.status == "ok" for event in outcome.docker_events)
    assert any(event.action == "memory-admission" and event.status == "ok" for event in outcome.docker_events)


def test_execute_starrocks_reconciles_mismatched_mocker_runtime(monkeypatch, tmp_path):
    monkeypatch.setenv(docker_assets.CONTAINER_CLI_ENV_VAR, "mocker")
    docker_assets.resolve_container_cli.cache_clear()
    stats_calls = 0
    calls: list[tuple[str, ...]] = []

    def fake_docker(argv, **kwargs):
        nonlocal stats_calls
        call = tuple(argv)
        calls.append(call)
        if "ps" in call or "exec" in call:
            return _healthy_ps_result(argv) if "ps" in call else docker_assets.DockerCommandResult(call, 0, "", "")
        if "stats" in call:
            stats_calls += 1
            return _healthy_stats_result(argv, limit="1GB" if stats_calls == 1 else "8GiB")
        return docker_assets.DockerCommandResult(call, 0, "", "")

    cfg = _managed_docker_cfg(
        "starrocks resource reconcile",
        platforms={"include": ["starrocks"]},
        preflight={"starrocks_memory_limit": "4g"},
    )
    with platform_reachability(True):
        outcome = exec_phase.run_execute(
            cfg,
            log_dir=tmp_path,
            databases_root=tmp_path / "databases",
            runner=_stub_runner_factory({0.01: 1.0}, {0.01: True}),
            docker_runner=fake_docker,
            memory_reader=_memory_reader(16.0),
            sleep_fn=lambda _s: None,
        )

    assert outcome.aborted is False
    assert any("update" in call and "--memory" in call for call in calls)
    assert any(event.action == "resource-reconcile" and event.status == "ok" for event in outcome.docker_events)


def test_execute_starrocks_readiness_failure_is_startup_failure(monkeypatch, tmp_path):
    monkeypatch.setenv(docker_assets.CONTAINER_CLI_ENV_VAR, "mocker")
    docker_assets.resolve_container_cli.cache_clear()

    def fake_docker(argv, **kwargs):
        call = tuple(argv)
        if "ps" in call:
            return _healthy_ps_result(argv)
        if "exec" in call:
            return docker_assets.DockerCommandResult(call, 1, "", "Current available backends: []")
        if "stats" in call:
            raise AssertionError(f"runtime stats must not run after failed application readiness: {call}")
        return docker_assets.DockerCommandResult(call, 0, "", "")

    cfg = _managed_docker_cfg("starrocks backend race", platforms={"include": ["starrocks"]})
    with platform_reachability(True):
        outcome = exec_phase.run_execute(
            cfg,
            log_dir=tmp_path,
            databases_root=tmp_path / "databases",
            runner=_stub_runner_factory({0.01: 1.0}, {0.01: True}),
            docker_runner=fake_docker,
            memory_reader=_memory_reader(16.0),
            sleep_fn=lambda _s: None,
        )

    assert outcome.aborted is False
    assert any(cell.platform == "starrocks" for cell in outcome.startup_failed)
    assert any(event.action == "application-readiness" and event.status == "failed" for event in outcome.docker_events)
    assert "Current available backends" in (outcome.abort_reason or "") or any(
        "Current available backends" in event.message for event in outcome.docker_events
    )


@pytest.mark.parametrize("platform", ["cedardb", "presto", "trino"])
def test_execute_query_readiness_failure_is_one_startup_failure_with_container_log(platform, tmp_path):
    def fake_docker(argv, **kwargs):
        action = "exec" if "exec" in argv else _docker_verb(argv)
        if action == "ps":
            return _healthy_ps_result(argv)
        if action == "exec":
            return docker_assets.DockerCommandResult(tuple(argv), 1, "", "server is still initializing")
        if action == "logs":
            return docker_assets.DockerCommandResult(tuple(argv), 0, "coordinator booting\n", "")
        return docker_assets.DockerCommandResult(tuple(argv), 0, "", "")

    def fail_runner(*args, **kwargs):
        raise AssertionError("no cell may run against a stack that cannot answer a query")

    cfg = _managed_docker_cfg("query readiness", platforms={"include": [platform]})
    with platform_reachability(True):
        outcome = exec_phase.run_execute(
            cfg,
            log_dir=tmp_path,
            databases_root=tmp_path / "databases",
            runner=fail_runner,
            docker_runner=fake_docker,
            memory_reader=_memory_reader(16.0),
            sleep_fn=lambda _s: None,
        )

    assert outcome.aborted is False
    assert outcome.startup_failed
    assert {cell.platform for cell in outcome.startup_failed} == {platform}
    assert any(event.action == "application-readiness" and event.status == "failed" for event in outcome.docker_events)
    assert "coordinator booting" in (tmp_path / f"container-logs-{platform}.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("platform", ["cedardb", "presto", "trino"])
def test_execute_missing_probe_tool_degrades_to_healthcheck_readiness(platform, tmp_path):
    def fake_docker(argv, **kwargs):
        action = "exec" if "exec" in argv else _docker_verb(argv)
        if action == "ps":
            return _healthy_ps_result(argv)
        if action == "exec":
            return docker_assets.DockerCommandResult(tuple(argv), 127, "", "sh: curl: not found")
        return docker_assets.DockerCommandResult(tuple(argv), 0, "", "")

    cfg = _managed_docker_cfg("probe tool missing", platforms={"include": [platform]})
    ran = []
    with platform_reachability(True):
        outcome = exec_phase.run_execute(
            cfg,
            log_dir=tmp_path,
            databases_root=tmp_path / "databases",
            runner=_stub_runner_factory({0.01: 1.0}, {0.01: True}),
            docker_runner=fake_docker,
            memory_reader=_memory_reader(16.0),
            sleep_fn=lambda _s: ran.append(_s),
        )

    assert not outcome.startup_failed
    skipped = [e for e in outcome.docker_events if e.action == "application-readiness" and e.status == "skipped"]
    assert skipped and "WARNING" in skipped[0].message


def test_application_readiness_retries_stay_within_one_deadline_and_cap_attempt_timeouts():
    cfg = validate_config({"name": "deadline", "platforms": {"include": ["presto"]}})
    spec = docker_assets.docker_platform_spec("presto")
    timeouts = []
    clock = {"t": 1000.0}

    def runner(argv, **kwargs):
        timeouts.append(kwargs["timeout_s"])
        clock["t"] += kwargs["timeout_s"]
        return docker_assets.DockerCommandResult(tuple(argv), 1, "", "pending")

    reason = managed_runtime.check_application_readiness(
        cfg,
        spec=spec,
        project_name="p",
        docker_runner=runner,
        docker_events=[],
        record_event=lambda *a, **k: None,
        log_dir=None,
        benchmark_runs_dir=Path("/tmp"),
        retry_window_s=290.0,
        retry_interval_s=10.0,
        sleep_fn=lambda s: clock.__setitem__("t", clock["t"] + s),
        clock=lambda: clock["t"],
    )

    assert reason is not None
    assert sum(timeouts) <= 290.0 + 1.0
    assert all(t <= 60.0 for t in timeouts)


def test_execute_starrocks_application_liveness_stops_after_runtime_wedge(monkeypatch, tmp_path):
    monkeypatch.setenv(docker_assets.CONTAINER_CLI_ENV_VAR, "mocker")
    docker_assets.resolve_container_cli.cache_clear()
    application_calls = 0

    def fake_docker(argv, **kwargs):
        nonlocal application_calls
        call = tuple(argv)
        if "ps" in call:
            return _healthy_ps_result(argv)
        if "exec" in call:
            application_calls += 1
            if application_calls >= 3:
                return docker_assets.DockerCommandResult(call, 1, "", "SQL listener wedged")
        if "stats" in call:
            return _healthy_stats_result(argv, limit="8GiB")
        return docker_assets.DockerCommandResult(call, 0, "", "")

    cfg = _managed_docker_cfg(
        "starrocks application liveness",
        platforms={"include": ["starrocks"]},
        scales={"rungs": [0.01, 0.1]},
    )
    with platform_reachability(True):
        outcome = exec_phase.run_execute(
            cfg,
            log_dir=tmp_path,
            databases_root=tmp_path / "databases",
            runner=_stub_runner_factory({0.01: 1.0, 0.1: 1.0}, {0.01: True, 0.1: True}),
            docker_runner=fake_docker,
            memory_reader=_memory_reader(16.0),
            sleep_fn=lambda _s: None,
        )

    assert outcome.results and outcome.results[0].scale == 0.01
    assert [(cell.platform, cell.scale) for cell in outcome.died_mid_platform] == [("starrocks", 0.1)]
    assert any(event.action == "application-liveness" and event.status == "failed" for event in outcome.docker_events)


def test_execute_starrocks_memory_admission_uses_measured_request(tmp_path):
    cfg = _managed_docker_cfg(
        "starrocks memory floor",
        platforms={"include": ["starrocks"]},
        preflight={"starrocks_memory_limit": "4g"},
    )
    outcome = exec_phase.run_execute(
        cfg,
        log_dir=tmp_path,
        databases_root=tmp_path / "databases",
        runner=_stub_runner_factory({0.01: 1.0}, {0.01: True}),
        docker_runner=_healthy_fake_docker,
        memory_reader=_memory_reader(3.5),
        sleep_fn=lambda _s: None,
    )

    assert outcome.aborted is True
    assert outcome.abort_kind == "memory_floor"
    assert "starrocks=4.000 GiB" in (outcome.abort_reason or "")


def test_execute_memory_floor_aborts_the_platform_before_starting_it(tmp_path):
    cfg = _managed_docker_cfg("memory floor abort")
    started: list[str] = []

    def fake_docker(argv, **kwargs):
        started.append(_docker_verb(argv))
        return docker_assets.DockerCommandResult(tuple(argv), 0, "", "")

    def fail_runner(platform, benchmark, scale, **kwargs):
        raise AssertionError("no cell may run once the memory floor has aborted")

    outcome = exec_phase.run_execute(
        cfg,
        log_dir=tmp_path,
        databases_root=tmp_path / "databases",
        runner=fail_runner,
        docker_runner=fake_docker,
        memory_reader=_memory_reader(0.07, swap_used_percent=88.0),
        sleep_fn=lambda _s: None,
    )

    assert outcome.aborted is True
    assert outcome.abort_kind == "memory_floor"
    assert outcome.results == ()
    assert "up" not in started


def test_execute_memory_floor_abort_reason_carries_the_shipped_failure_message(tmp_path):
    cfg = _managed_docker_cfg("memory floor message")

    outcome = exec_phase.run_execute(
        cfg,
        log_dir=tmp_path,
        databases_root=tmp_path / "databases",
        runner=_stub_runner_factory({0.01: 1.0}, {0.01: True}),
        docker_runner=_healthy_fake_docker,
        memory_reader=_memory_reader(0.07, swap_used_percent=88.0),
        sleep_fn=lambda _s: None,
    )

    reason = outcome.abort_reason
    assert reason is not None
    selected_bytes = docker_assets.parse_memory_bytes(cfg.preflight.clickhouse_memory_limit)
    required_gib = selected_bytes / (1024**3) + cfg.preflight.docker_memory_reserve_gib
    expected_core = format_memory_headroom_failure(
        check_memory_headroom(MemorySnapshot(free_gib=0.07, swap_used_percent=88.0), min_free_gib=required_gib)
    )
    assert reason.startswith(expected_core)
    assert "before starting platform clickhouse-server" in reason
    assert "engine=docker" in reason
    assert reason.count("swap 88.0% used") == 1
    lifecycle = (tmp_path / "uat_lifecycle.log").read_text(encoding="utf-8")
    assert "[free-memory]" in lifecycle
    assert "0.07 GiB available" in lifecycle


def test_execute_memory_floor_passes_when_host_has_headroom(tmp_path):
    cfg = _managed_docker_cfg("memory floor ok")

    with platform_reachability(True):
        outcome = exec_phase.run_execute(
            cfg,
            log_dir=tmp_path,
            databases_root=tmp_path / "databases",
            runner=_stub_runner_factory({0.01: 1.0}, {0.01: True}),
            docker_runner=_healthy_fake_docker,
            memory_reader=_memory_reader(16.0),
            sleep_fn=lambda _s: None,
        )

    assert outcome.aborted is False
    assert outcome.abort_kind is None
    assert any(r.platform == "clickhouse-server" and r.status == "passed" for r in outcome.results)


def test_execute_clickhouse_default_admission_uses_measured_request_without_unvalidated_reserve(tmp_path):
    cfg = _managed_docker_cfg("memory request only")
    assert cfg.preflight.docker_memory_reserve_gib == 0.0

    with platform_reachability(True):
        outcome = exec_phase.run_execute(
            cfg,
            log_dir=tmp_path,
            databases_root=tmp_path / "databases",
            runner=_stub_runner_factory({0.01: 1.0}, {0.01: True}),
            docker_runner=_healthy_fake_docker,
            memory_reader=_memory_reader(8.0),
            sleep_fn=lambda _s: None,
        )

    assert outcome.aborted is False
    assert any(r.platform == "clickhouse-server" and r.status == "passed" for r in outcome.results)


def test_execute_memory_floor_disabled_by_zero_never_aborts(tmp_path):
    cfg = _managed_docker_cfg("memory floor off", preflight={"free_memory_min_gib": 0})

    with platform_reachability(True):
        outcome = exec_phase.run_execute(
            cfg,
            log_dir=tmp_path,
            databases_root=tmp_path / "databases",
            runner=_stub_runner_factory({0.01: 1.0}, {0.01: True}),
            docker_runner=_healthy_fake_docker,
            memory_reader=_memory_reader(0.01),
            sleep_fn=lambda _s: None,
        )

    assert outcome.aborted is False
    assert any(cell.platform == "clickhouse-server" for cell in outcome.startup_failed)
    lifecycle_path = tmp_path / "uat_lifecycle.log"
    lifecycle = lifecycle_path.read_text(encoding="utf-8") if lifecycle_path.exists() else ""
    assert "[free-memory]" not in lifecycle


def test_execute_memory_floor_unmeasurable_clickhouse_host_fails_closed(tmp_path):
    cfg = _managed_docker_cfg("memory unmeasurable")

    with platform_reachability(True):
        outcome = exec_phase.run_execute(
            cfg,
            log_dir=tmp_path,
            databases_root=tmp_path / "databases",
            runner=_stub_runner_factory({0.01: 1.0}, {0.01: True}),
            docker_runner=_healthy_fake_docker,
            memory_reader=_memory_reader(None, swap_used_percent=None),
            sleep_fn=lambda _s: None,
        )

    assert outcome.aborted is True
    assert outcome.abort_kind == "memory_floor"
    lifecycle = (tmp_path / "uat_lifecycle.log").read_text(encoding="utf-8")
    assert "could not be measured" in lifecycle


def test_execute_clickhouse_runtime_memory_rejects_host_below_explicit_request_plus_reserve(tmp_path):
    cfg = _managed_docker_cfg("runtime memory shortfall", preflight={"docker_memory_reserve_gib": 2.0})
    readings = iter([16.0, 5.0])
    with platform_reachability(True):
        outcome = exec_phase.run_execute(
            cfg,
            log_dir=tmp_path,
            databases_root=tmp_path / "databases",
            runner=_stub_runner_factory({0.01: 1.0}, {0.01: True}),
            docker_runner=_healthy_fake_docker,
            memory_reader=lambda: MemorySnapshot(free_gib=next(readings), swap_used_percent=0.0),
            sleep_fn=lambda _s: None,
        )
    assert outcome.aborted is False
    assert any(cell.platform == "clickhouse-server" for cell in outcome.startup_failed)
    assert any(event.action == "memory-admission" and event.status == "ok" for event in outcome.docker_events)
    assert "required" in (next(event.message for event in outcome.docker_events if event.action == "readiness"))


def test_execute_memory_floor_ignores_non_docker_platforms(tmp_path):
    cfg = validate_config(
        {
            "name": "memory native",
            "platforms": {"include": ["duckdb"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
            "cleanup": {"docker_manage_platforms": True, "docker_platform_switch": "volumes"},
        }
    )

    with platform_reachability(True):
        outcome = exec_phase.run_execute(
            cfg,
            log_dir=tmp_path,
            databases_root=tmp_path / "databases",
            runner=_stub_runner_factory({0.01: 1.0}, {0.01: True}),
            docker_runner=_healthy_fake_docker,
            memory_reader=_memory_reader(0.01),
            sleep_fn=lambda _s: None,
        )

    assert outcome.aborted is False
    assert any(r.platform == "duckdb" and r.status == "passed" for r in outcome.results)


def test_execute_disk_floor_takes_precedence_over_memory_floor(tmp_path):
    cfg = _managed_docker_cfg("both floors")

    outcome = exec_phase.run_execute(
        cfg,
        log_dir=tmp_path,
        databases_root=tmp_path / "databases",
        runner=_stub_runner_factory({0.01: 1.0}, {0.01: True}),
        docker_runner=_healthy_fake_docker,
        free_space_checks_enabled=True,
        free_space_path=tmp_path,
        free_space_min_gib=100.0,
        free_space_reader=lambda _p: 1.0,
        memory_reader=_memory_reader(0.01),
        sleep_fn=lambda _s: None,
    )

    assert outcome.aborted is True
    assert outcome.abort_kind == "disk_floor"
    assert "free space" in (outcome.abort_reason or "")


def test_execute_teardown_failure_after_startup_failure_advances_instead_of_aborting(tmp_path):
    cfg = validate_config(
        {
            "name": "docker startup and teardown both fail",
            "platforms": {"include": ["clickhouse-server", "postgresql"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
            "cleanup": {"docker_manage_platforms": True, "docker_platform_switch": "volumes"},
        }
    )
    sequence: list[tuple[str, str, str]] = []

    def fake_docker(argv, **kwargs):
        action = _docker_verb(argv)
        platform = _docker_platform_from_argv(argv)
        sequence.append(("docker", action, platform))
        if platform == "clickhouse-server":
            return docker_assets.DockerCommandResult(tuple(argv), 1, "", f"{action} failed")
        if action == "ps":
            return _healthy_ps_result(argv)
        return docker_assets.DockerCommandResult(tuple(argv), 0, "", "")

    def recording_runner(platform, benchmark, scale, **kwargs):
        sequence.append(("cell", "run", platform))
        return CellResult(
            platform=platform,
            benchmark=benchmark,
            scale=scale,
            status="passed",
            exit_code=0,
            elapsed_s=1.0,
            log_path=tmp_path / f"{platform}.log",
            result_path=None,
        )

    with platform_reachability(True):
        outcome = exec_phase.run_execute(
            cfg,
            log_dir=tmp_path,
            databases_root=tmp_path / "databases",
            runner=recording_runner,
            docker_runner=fake_docker,
            free_space_checks_enabled=True,
            free_space_reader=lambda _path: 100.0,
            sleep_fn=lambda _s: None,
        )

    assert outcome.aborted is False
    assert outcome.abort_reason is None
    assert sequence == [
        ("docker", "up", "clickhouse-server"),
        ("docker", "logs", "clickhouse-server"),
        ("docker", "down", "clickhouse-server"),
        ("docker", "up", "postgresql"),
        ("docker", "ps", "postgresql"),
        ("cell", "run", "postgresql"),
        ("docker", "down", "postgresql"),
    ]
    assert any(cell.platform == "clickhouse-server" for cell in outcome.startup_failed)
    assert any(
        event.platform == "clickhouse-server" and event.action == "down" and event.status == "failed"
        for event in outcome.docker_events
    )
    assert any(
        event.platform == "clickhouse-server"
        and event.action == "down-policy"
        and event.status == "advance-after-startup-failed"
        for event in outcome.docker_events
    )


def test_execute_healthy_stack_teardown_failure_still_aborts_after_startup_failed_regression_guard(tmp_path):
    cfg = validate_config(
        {
            "name": "healthy stack teardown failure",
            "platforms": {"include": ["clickhouse-server", "postgresql"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
            "cleanup": {"docker_manage_platforms": True, "docker_platform_switch": "volumes"},
        }
    )

    def fake_docker(argv, **kwargs):
        action = _docker_verb(argv)
        if action == "down":
            return docker_assets.DockerCommandResult(tuple(argv), 1, "", "compose down failed")
        if action == "ps":
            return _healthy_ps_result(argv)
        if action == "stats":
            return _healthy_stats_result(argv)
        return docker_assets.DockerCommandResult(tuple(argv), 0, "", "")

    with platform_reachability(True):
        outcome = exec_phase.run_execute(
            cfg,
            log_dir=tmp_path,
            databases_root=tmp_path / "databases",
            runner=_stub_runner_factory({0.01: 1.0}, {0.01: True}),
            docker_runner=fake_docker,
            free_space_reader=lambda _path: 100.0,
            sleep_fn=lambda _s: None,
        )

    assert outcome.aborted is True
    assert "Docker cleanup failed" in (outcome.abort_reason or "")
    assert len(outcome.startup_failed) == 0


def test_execute_outcome_exit_code_nonzero_when_every_compose_up_fails(tmp_path):
    cfg = validate_config(
        {
            "name": "docker all compose-up failed",
            "platforms": {"include": ["clickhouse-server"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
            "cleanup": {"docker_manage_platforms": True, "docker_platform_switch": "volumes"},
        }
    )

    def fake_docker(argv, **kwargs):
        action = _docker_verb(argv)
        if action == "up":
            return docker_assets.DockerCommandResult(
                tuple(argv), 1, "", "docker command timed out after 300s", timed_out=True
            )
        return docker_assets.DockerCommandResult(tuple(argv), 0, "", "")

    def fail_runner(platform, benchmark, scale, **kwargs):
        raise AssertionError("no cell should run when the only compose-up failed")

    with platform_reachability(True):
        outcome = exec_phase.run_execute(
            cfg,
            log_dir=tmp_path,
            databases_root=tmp_path / "databases",
            runner=fail_runner,
            docker_runner=fake_docker,
            free_space_checks_enabled=True,
            free_space_reader=lambda _path: 100.0,
        )

    assert outcome.aborted is False
    assert len(outcome.results) == 0
    assert len(outcome.skipped_unreachable) == 0
    assert len(outcome.startup_failed) == 1
    assert outcome.exit_code() == 1


def test_execute_outcome_exit_code_zero_only_when_all_passed(tmp_path):
    all_passed = exec_phase.ExecuteOutcome(
        phase="execute",
        results=(
            CellResult(
                platform="duckdb",
                benchmark="tpch",
                scale=0.01,
                status="passed",
                exit_code=0,
                elapsed_s=1.0,
                log_path=tmp_path / "cell.log",
                result_path=None,
            ),
        ),
        pruned=(),
        skipped_unreachable=(),
    )
    assert all_passed.exit_code() == 0

    one_failed = exec_phase.ExecuteOutcome(
        phase="execute",
        results=(
            CellResult(
                platform="duckdb",
                benchmark="tpch",
                scale=0.01,
                status="failed",
                exit_code=1,
                elapsed_s=1.0,
                log_path=tmp_path / "cell.log",
                result_path=None,
            ),
        ),
        pruned=(),
        skipped_unreachable=(),
    )
    assert one_failed.exit_code() == 1

    no_results = exec_phase.ExecuteOutcome(
        phase="execute",
        results=(),
        pruned=(),
        skipped_unreachable=(),
    )
    assert no_results.exit_code() == 1


def test_execute_unmanaged_docker_keeps_skip_probe_without_commands(tmp_path):
    cfg = validate_config(
        {
            "name": "external",
            "platforms": {"include": ["postgresql"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
            "cleanup": {"docker_manage_platforms": False, "docker_platform_switch": "off"},
        }
    )

    def fail_docker(argv, **kwargs):
        raise AssertionError(f"unexpected Docker command: {argv}")

    with platform_reachability(False):
        outcome = exec_phase.run_execute(
            cfg,
            log_dir=tmp_path,
            databases_root=tmp_path / "databases",
            docker_runner=fail_docker,
            runner=_stub_runner_factory({}, {}),
        )

    assert len(outcome.results) == 0
    assert len(outcome.skipped_unreachable) == 1
    assert any(event.action == "manage" and event.status == "disabled" for event in outcome.docker_events)


@pytest.mark.parametrize(
    ("docker_manage_platforms", "expected_local_managed"),
    [(False, False), (True, True)],
)
def test_execute_scopes_local_managed_platform_options_to_managed_docker(
    docker_manage_platforms: bool,
    expected_local_managed: bool,
    tmp_path,
):
    cfg = validate_config(
        {
            "name": "docker scope",
            "platforms": {"include": ["postgresql"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
            "cleanup": {
                "docker_manage_platforms": docker_manage_platforms,
                "docker_platform_switch": "volumes" if docker_manage_platforms else "off",
            },
        }
    )
    seen: dict[str, bool] = {}

    def fake_docker(argv, **kwargs):
        if _docker_verb(argv) == "ps":
            return _healthy_ps_result(argv)
        return docker_assets.DockerCommandResult(tuple(argv), 0, "", "")

    def recording_runner(platform, benchmark, scale, **kwargs):
        seen["local_managed_platform"] = kwargs["local_managed_platform"]
        return CellResult(
            platform=platform,
            benchmark=benchmark,
            scale=scale,
            status="passed",
            exit_code=0,
            elapsed_s=1.0,
            log_path=tmp_path / "postgresql.log",
            result_path=None,
        )

    with platform_reachability(True):
        outcome = exec_phase.run_execute(
            cfg,
            log_dir=tmp_path,
            databases_root=tmp_path / "databases",
            runner=recording_runner,
            docker_runner=fake_docker,
            sleep_fn=lambda _s: None,
        )

    assert outcome.aborted is False
    assert seen["local_managed_platform"] is expected_local_managed


def test_execute_runner_exception_still_tears_down_managed_docker(tmp_path):
    cfg = validate_config(
        {
            "name": "docker failure",
            "platforms": {"include": ["clickhouse-server"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
            "cleanup": {"docker_manage_platforms": True, "docker_platform_switch": "volumes"},
        }
    )
    actions: list[str] = []

    def fake_docker(argv, **kwargs):
        action = _docker_verb(argv)
        actions.append(action)
        if action == "ps":
            return _healthy_ps_result(argv)
        if action == "stats":
            return _healthy_stats_result(argv)
        return docker_assets.DockerCommandResult(tuple(argv), 0, "", "")

    def raising_runner(platform, benchmark, scale, **kwargs):
        raise RuntimeError("cell exploded")

    with (
        platform_reachability(True),
        pytest.raises(RuntimeError, match="cell exploded"),
    ):
        exec_phase.run_execute(
            cfg,
            log_dir=tmp_path,
            databases_root=tmp_path / "databases",
            runner=raising_runner,
            docker_runner=fake_docker,
            free_space_reader=lambda _path: 100.0,
            sleep_fn=lambda _s: None,
        )

    assert actions == ["up", "ps", "stats", "down"]


def test_execute_fixed_container_name_platform_aborts_before_docker_command(tmp_path, monkeypatch):
    pg_duckdb_spec = docker_assets.docker_platform_spec("pg-duckdb")
    monkeypatch.setitem(
        docker_assets._DOCKER_PLATFORM_SPECS,
        "pg-duckdb",
        docker_assets.DockerPlatformSpec(
            platform=pg_duckdb_spec.platform,
            compose_files=pg_duckdb_spec.compose_files,
            fixed_container_names=("benchbox-pg-duckdb",),
            tcp_probe_label=pg_duckdb_spec.tcp_probe_label,
            notes=pg_duckdb_spec.notes,
        ),
    )
    cfg = validate_config(
        {
            "name": "fixed name",
            "platforms": {"include": ["pg-duckdb"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
            "cleanup": {"docker_manage_platforms": True, "docker_platform_switch": "volumes"},
        }
    )

    def fail_docker(argv, **kwargs):
        raise AssertionError(f"unexpected Docker command: {argv}")

    outcome = exec_phase.run_execute(
        cfg,
        log_dir=tmp_path,
        databases_root=tmp_path / "databases",
        docker_runner=fail_docker,
        runner=_stub_runner_factory({}, {}),
    )

    assert outcome.aborted is True
    assert "fixed container_name" in (outcome.abort_reason or "")
    assert outcome.docker_events == ()


def test_execute_free_space_abort_reports_context_after_docker_teardown(tmp_path):
    cfg = validate_config(
        {
            "name": "docker disk",
            "platforms": {"include": ["clickhouse-server"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
            "preflight": {"free_space_min_gib": 5, "free_space_path": str(tmp_path)},
            "cleanup": {"docker_manage_platforms": True, "docker_platform_switch": "volumes"},
        }
    )
    readings = iter([10.0, 1.0])

    def fake_docker(argv, **kwargs):
        if _docker_verb(argv) == "stats":
            return _healthy_stats_result(argv)
        return docker_assets.DockerCommandResult(tuple(argv), 0, "", "")

    with platform_reachability(True):
        outcome = exec_phase.run_execute(
            cfg,
            log_dir=tmp_path,
            databases_root=tmp_path / "databases",
            runner=_stub_runner_factory({0.01: 1.0}, {0.01: True}),
            docker_runner=fake_docker,
            free_space_checks_enabled=True,
            free_space_reader=lambda _path: next(readings),
            sleep_fn=lambda _s: None,
        )

    assert outcome.aborted is True
    assert "after Docker teardown" in (outcome.abort_reason or "")
    assert "last_completed_platform=clickhouse-server" in (outcome.abort_reason or "")
    assert "docker_cleanup_status=ok" in (outcome.abort_reason or "")


def test_execute_passes_config_extra_args_to_runner(tmp_path, monkeypatch):
    monkeypatch.delenv("BENCHBOX_OUTPUT_DIR", raising=False)
    cfg = validate_config(
        {
            "name": "fake",
            "platforms": {"include": ["duckdb"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
            "execute": {"extra_args": ["--tuning", "tuned"]},
        }
    )
    seen: dict[str, tuple[str, ...] | Path] = {}

    def recording_runner(platform, benchmark, scale, **kwargs):
        seen["extra_args"] = tuple(kwargs["extra_args"])
        seen["benchmark_runs_dir"] = kwargs["benchmark_runs_dir"]
        return CellResult(
            platform=platform,
            benchmark=benchmark,
            scale=scale,
            status="passed",
            exit_code=0,
            elapsed_s=1.0,
            log_path=Path("/tmp/x.log"),
            result_path=None,
        )

    exec_phase.run_execute(
        cfg,
        log_dir=tmp_path,
        databases_root=tmp_path / "databases",
        runner=recording_runner,
    )
    assert seen["extra_args"] == ("--tuning", "tuned")
    assert seen["benchmark_runs_dir"] == Path("~/Developer/benchmark_runs").expanduser()


def test_execute_outcome_carries_compatibility_pruned_cells(tmp_path):
    cfg = validate_config(
        {
            "name": "compat",
            "platforms": {"include": ["polars-df"]},
            "benchmarks": {"include": ["vector_search", "tpch"]},
            "scales": {"rungs": [0.01]},
        }
    )

    outcome = exec_phase.run_execute(
        cfg,
        log_dir=tmp_path,
        databases_root=tmp_path / "databases",
        runner=_stub_runner_factory({0.01: 1.0}, {0.01: True}),
    )

    assert len(outcome.results) == 1
    assert len(outcome.compatibility_pruned) == 1
    assert outcome.compatibility_pruned[0].rule_id == "uat.compat.dataframe.sql_only_benchmark"


def test_execute_downgrades_passed_cell_with_query_failure_result(tmp_path):
    result_path = tmp_path / "benchmark_runs" / "results" / "failed-query.json"
    _write_submit_result(result_path, failed=1)
    cfg = validate_config(
        {
            "name": "fake",
            "platforms": {"include": ["duckdb"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
        }
    )

    def fake_runner(platform, benchmark, scale, **kwargs):
        submit_state = classify_for_submit(result_path)
        is_failure = submit_state_is_cell_failure(submit_state)
        return CellResult(
            platform=platform,
            benchmark=benchmark,
            scale=scale,
            status="failed" if is_failure else "passed",
            exit_code=1 if is_failure else 0,
            elapsed_s=1.0,
            log_path=tmp_path / "cell.log",
            result_path=result_path,
            submit_terminal_state=submit_state.value,
        )

    outcome = exec_phase.run_execute(
        cfg,
        log_dir=tmp_path,
        databases_root=tmp_path / "databases",
        runner=fake_runner,
    )

    assert outcome.results[0].status == "failed"
    assert outcome.results[0].submit_terminal_state == "query_failure"


def test_package_classifier_agrees_with_query_failure_refusal(tmp_path):
    result_path = tmp_path / "benchmark_runs" / "results" / "failed-query.json"
    _write_submit_result(result_path, failed=1)
    cfg = validate_config({"name": "fake", "package": {"submit_terminal_state": "local-stage"}})
    warnings: list[str] = []
    calls: list[tuple[str, ...]] = []

    def fake_runner(argv, check=False):
        calls.append(tuple(argv))
        return type("Completed", (), {"returncode": 0})()

    result = package_phase.run_package(
        cfg,
        result_paths=[result_path],
        submissions_dir=tmp_path / "subs",
        runner=fake_runner,
        warn=warnings.append,
        classify_results=True,
    )

    assert result.failure_count == 1
    assert calls == []
    assert "query_failure" in warnings[0]


def test_default_log_dir_substitutes_date_and_name():
    cfg = validate_config({"name": "uat-2026-05-02"})
    out = exec_phase.default_log_dir(cfg, now=_dt.datetime(2026, 5, 5))
    assert "20260505" in str(out)
    assert "uat-2026-05-02" not in str(out)


def test_default_log_dir_substitutes_time_component():
    cfg = validate_config({"name": "uat-smoke"})
    out = exec_phase.default_log_dir(cfg, now=_dt.datetime(2026, 5, 5, 14, 30, 7))
    assert "143007" in str(out)


def test_default_log_dir_time_avoids_same_day_collision():
    cfg = validate_config({"name": "uat-smoke"})
    first = exec_phase.default_log_dir(cfg, now=_dt.datetime(2026, 5, 5, 9, 0, 0))
    second = exec_phase.default_log_dir(cfg, now=_dt.datetime(2026, 5, 5, 9, 0, 1))
    assert first != second


def test_default_log_dir_same_second_collision_gets_disambiguated(tmp_path: Path):
    cfg = validate_config(
        {
            "name": "collision-smoke",
            "output": {"logs_dir_template": str(tmp_path / "uat_{date}_{time}")},
        }
    )
    now = _dt.datetime(2026, 5, 5, 9, 0, 0)
    first = exec_phase.default_log_dir(cfg, now=now)
    first.mkdir(parents=True)
    second = exec_phase.default_log_dir(cfg, now=now)

    assert first != second
    assert second.name == f"{first.name}-2"


def test_reserve_default_log_dir_is_atomic_for_same_timestamp(tmp_path: Path):
    from concurrent.futures import ThreadPoolExecutor

    cfg = validate_config(
        {
            "name": "collision-smoke",
            "output": {"logs_dir_template": str(tmp_path / "uat_{date}_{time}")},
        }
    )
    now = _dt.datetime(2026, 5, 5, 9, 0, 0)

    with ThreadPoolExecutor(max_workers=2) as pool:
        reserved = list(pool.map(lambda _: exec_phase.reserve_default_log_dir(cfg, now=now), range(2)))

    assert reserved[0] != reserved[1]
    assert all(path.is_dir() for path in reserved)


def test_default_log_dir_explicit_date_only_template_still_works():
    cfg = validate_config(
        {
            "name": "uat-smoke",
            "output": {"logs_dir_template": "~/Developer/benchmark_runs/logs/uat_custom_{date}"},
        }
    )
    out = exec_phase.default_log_dir(cfg, now=_dt.datetime(2026, 5, 5, 9, 0, 0))
    assert str(out).endswith("uat_custom_20260505")


def test_atomic_write_text_writes_content_and_cleans_up_tmp(tmp_path: Path):
    target = tmp_path / "nested" / "cells.jsonl"
    report_phase.atomic_write_text(target, "line-one\nline-two\n")

    assert target.read_text(encoding="utf-8") == "line-one\nline-two\n"
    assert not target.with_name(target.name + ".tmp").exists()


def test_atomic_write_text_overwrites_existing_content(tmp_path: Path):
    target = tmp_path / "matrix_summary.tsv"
    report_phase.atomic_write_text(target, "first\n")
    report_phase.atomic_write_text(target, "second\n")

    assert target.read_text(encoding="utf-8") == "second\n"


def test_atomic_write_text_survives_a_failed_write_without_torn_output(tmp_path: Path):
    target = tmp_path / "validator_rollup.tsv"
    report_phase.atomic_write_text(target, "good-content\n")

    with patch("tests.uat.phases.report.os.replace", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            report_phase.atomic_write_text(target, "new-content-that-never-lands\n")

    assert target.read_text(encoding="utf-8") == "good-content\n"
    assert not target.with_name(target.name + ".tmp").exists()


def test_default_benchmark_runs_dir_substitutes_date_and_name(tmp_path):
    cfg = validate_config(
        {
            "name": "uat-smoke",
            "output": {"benchmark_runs_dir_template": str(tmp_path / "{name}" / "{date}")},
        }
    )
    out = exec_phase.default_benchmark_runs_dir(cfg, now=_dt.datetime(2026, 5, 5))
    assert out == tmp_path / "uat-smoke" / "20260505"


def test_default_benchmark_runs_dir_honors_env_var_when_template_is_default(monkeypatch, tmp_path):
    monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", str(tmp_path / "external-root"))
    cfg = validate_config({"name": "uat-smoke"})
    out = exec_phase.default_benchmark_runs_dir(cfg, now=_dt.datetime(2026, 5, 5))
    assert out == tmp_path / "external-root"


def test_default_log_dir_honors_env_var_when_template_is_default(monkeypatch, tmp_path):
    monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", str(tmp_path / "external-root"))
    cfg = validate_config({"name": "uat-smoke"})
    out = exec_phase.default_log_dir(cfg, now=_dt.datetime(2026, 5, 5, 9, 0, 0))
    assert out == tmp_path / "external-root" / "logs" / "uat_20260505_090000"


def test_default_submissions_dir_honors_env_var_when_template_is_default(monkeypatch, tmp_path):
    monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", str(tmp_path / "external-root"))
    cfg = validate_config({"name": "uat-smoke"})

    out = exec_phase.default_submissions_dir(cfg, now=_dt.datetime(2026, 5, 5))

    assert out == tmp_path / "external-root" / "submissions" / "uat-smoke"


def test_default_benchmark_runs_dir_explicit_template_wins_over_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", str(tmp_path / "external-root"))
    cfg = validate_config(
        {
            "name": "uat-smoke",
            "output": {"benchmark_runs_dir_template": str(tmp_path / "explicit-root")},
        }
    )
    out = exec_phase.default_benchmark_runs_dir(cfg, now=_dt.datetime(2026, 5, 5))
    assert out == tmp_path / "explicit-root"


def test_default_log_dir_explicit_template_wins_over_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", str(tmp_path / "external-root"))
    cfg = validate_config(
        {
            "name": "uat-smoke",
            "output": {"logs_dir_template": str(tmp_path / "explicit-logs" / "uat_{date}")},
        }
    )
    out = exec_phase.default_log_dir(cfg, now=_dt.datetime(2026, 5, 5, 9, 0, 0))
    assert out == tmp_path / "explicit-logs" / "uat_20260505"


def test_default_benchmark_runs_dir_default_template_without_env_var_unchanged(monkeypatch):
    monkeypatch.delenv("BENCHBOX_OUTPUT_DIR", raising=False)
    cfg = validate_config({"name": "uat-smoke"})
    out = exec_phase.default_benchmark_runs_dir(cfg, now=_dt.datetime(2026, 5, 5))
    assert out == Path("~/Developer/benchmark_runs").expanduser()


def test_default_benchmark_runs_dir_explicit_template_equal_to_default_wins_over_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", str(tmp_path / "external-root"))
    default_template = "~/Developer/benchmark_runs"
    cfg = validate_config(
        {
            "name": "uat-smoke",
            "output": {"benchmark_runs_dir_template": default_template},
        }
    )
    assert "benchmark_runs_dir_template" in cfg.output.explicitly_set
    out = exec_phase.default_benchmark_runs_dir(cfg, now=_dt.datetime(2026, 5, 5))
    assert out == Path(default_template).expanduser()


def test_topological_sort_moves_source_before_consumer():
    consumer_to_sources = {
        "read_primitives": ["tpch"],
        "write_primitives": ["tpch"],
    }
    out = exec_phase._topological_sort(
        ["read_primitives", "tpch", "write_primitives"],
        consumer_to_sources,
    )
    assert out.index("tpch") < out.index("read_primitives")
    assert out.index("tpch") < out.index("write_primitives")


def test_topological_sort_stable_when_no_constraint():
    out = exec_phase._topological_sort(["clickbench", "ssb", "h2odb"], {})
    assert out == ["clickbench", "ssb", "h2odb"]


def test_topological_sort_keeps_available_unrelated_benchmark_before_source():
    consumer_to_sources = {
        "read_primitives": ["tpch"],
        "write_primitives": ["tpch"],
        "transaction_primitives": ["tpch"],
        "ai_primitives": ["tpch"],
    }
    out = exec_phase._topological_sort(
        [
            "read_primitives",
            "clickbench",
            "tpch",
            "write_primitives",
            "transaction_primitives",
            "ai_primitives",
        ],
        consumer_to_sources,
    )
    assert out == [
        "clickbench",
        "tpch",
        "read_primitives",
        "write_primitives",
        "transaction_primitives",
        "ai_primitives",
    ]


def test_execute_reorders_consumer_before_source(tmp_path):
    cfg = validate_config(
        {
            "name": "fake",
            "platforms": {"include": ["duckdb"]},
            "benchmarks": {"include": ["read_primitives", "tpch"]},
            "scales": {"rungs": [0.01]},
        }
    )
    invocations: list[str] = []

    def recording_runner(platform, benchmark, scale, **kwargs):
        invocations.append(benchmark)
        return CellResult(
            platform=platform,
            benchmark=benchmark,
            scale=scale,
            status="passed",
            exit_code=0,
            elapsed_s=1.0,
            log_path=Path("/tmp/x.log"),
            result_path=None,
        )

    exec_phase.run_execute(
        cfg,
        log_dir=tmp_path,
        databases_root=tmp_path / "databases",
        runner=recording_runner,
    )
    assert "tpch" in invocations and "read_primitives" in invocations
    assert invocations.index("tpch") < invocations.index("read_primitives")


def test_execute_prunes_source_after_consumer_completes(tmp_path):
    cfg = validate_config(
        {
            "name": "fake",
            "platforms": {"include": ["duckdb"]},
            "benchmarks": {"include": ["tpch", "read_primitives"]},
            "scales": {"rungs": [0.01]},
        }
    )
    db_root = tmp_path / "databases"
    (db_root / "duckdb" / "tpch" / "0.01").mkdir(parents=True)
    (db_root / "duckdb" / "tpch" / "0.01" / "data.duckdb").write_text("stub")

    runner = _stub_runner_factory(
        elapsed_map={0.01: 1.0},
        pass_map={0.01: True},
    )
    exec_phase.run_execute(
        cfg,
        log_dir=tmp_path,
        databases_root=db_root,
        runner=runner,
    )
    assert not (db_root / "duckdb" / "tpch" / "0.01").exists()


def test_execute_does_not_prune_source_while_consumer_pending(tmp_path):
    cfg = validate_config(
        {
            "name": "fake",
            "platforms": {"include": ["duckdb"]},
            "benchmarks": {"include": ["tpch", "read_primitives"]},
            "scales": {"rungs": [0.01]},
        }
    )
    db_root = tmp_path / "databases"
    (db_root / "duckdb" / "tpch" / "0.01").mkdir(parents=True)
    (db_root / "duckdb" / "tpch" / "0.01" / "data.duckdb").write_text("stub")

    invocations: list[str] = []

    def stop_after_tpch(platform, benchmark, scale, **kwargs):
        invocations.append(benchmark)
        if benchmark == "read_primitives":
            raise RuntimeError("should be reachable but we want to inspect mid-state")
        return CellResult(
            platform=platform,
            benchmark=benchmark,
            scale=scale,
            status="passed",
            exit_code=0,
            elapsed_s=1.0,
            log_path=Path("/tmp/x.log"),
            result_path=None,
        )

    with pytest.raises(RuntimeError):
        exec_phase.run_execute(
            cfg,
            log_dir=tmp_path,
            databases_root=db_root,
            runner=stop_after_tpch,
        )
    assert (db_root / "duckdb" / "tpch" / "0.01").exists()


def _probe_dying_after(alive_calls: int):
    calls = {"n": 0}

    def probe(_platform, **_kwargs):
        calls["n"] += 1
        return calls["n"] <= alive_calls

    return probe


def test_execute_stack_dying_mid_platform_records_remaining_cells_as_died_not_failures(tmp_path):
    cfg = validate_config(
        {
            "name": "mid platform death",
            "platforms": {"include": ["clickhouse-server"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01, 0.1, 1.0]},
            "cleanup": {"docker_manage_platforms": True, "docker_platform_switch": "volumes"},
        }
    )
    ran: list[float] = []

    def recording_runner(platform, benchmark, scale, **kwargs):
        ran.append(scale)
        return CellResult(
            platform=platform,
            benchmark=benchmark,
            scale=scale,
            status="passed",
            exit_code=0,
            elapsed_s=1.0,
            log_path=tmp_path / f"{platform}-{scale}.log",
            result_path=None,
        )

    with platform_reachability(True, probe=_probe_dying_after(3)):
        outcome = exec_phase.run_execute(
            cfg,
            log_dir=tmp_path,
            databases_root=tmp_path / "databases",
            runner=recording_runner,
            docker_runner=_healthy_fake_docker,
            sleep_fn=lambda _s: None,
        )

    assert ran == [0.01]
    died = [(c.platform, c.scale) for c in outcome.died_mid_platform]
    assert died == [("clickhouse-server", 0.1), ("clickhouse-server", 1.0)]
    assert [r.status for r in outcome.results] == ["passed"]
    assert not any(r.status == "failed" for r in outcome.results)
    assert outcome.startup_failed == ()
    assert outcome.skipped_unreachable == ()
    assert outcome.exit_code() == 1
    lifecycle = (tmp_path / "uat_lifecycle.log").read_text(encoding="utf-8")
    assert "[liveness]" in lifecycle
    assert "died-mid-platform" in lifecycle


def test_execute_stack_dying_mid_platform_captures_container_state_and_log_tail(tmp_path):
    cfg = validate_config(
        {
            "name": "mid platform death diagnostics",
            "platforms": {"include": ["clickhouse-server"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01, 0.1]},
            "cleanup": {"docker_manage_platforms": True, "docker_platform_switch": "volumes"},
        }
    )
    seen: list[list[str]] = []

    def fake_docker(argv, **kwargs):
        seen.append(list(argv))
        if "-q" in argv:
            return docker_assets.DockerCommandResult(tuple(argv), 0, "abc123\n", "")
        if argv[1] == "inspect":
            return docker_assets.DockerCommandResult(
                tuple(argv), 0, "/ch Status=exited OOMKilled=true ExitCode=137 Error= \n", ""
            )
        if _docker_verb(argv) == "logs":
            return docker_assets.DockerCommandResult(tuple(argv), 0, "Code: 241 memory limit exceeded\n", "")
        return _healthy_fake_docker(argv, **kwargs)

    def passing_runner(platform, benchmark, scale, **kwargs):
        return CellResult(
            platform=platform,
            benchmark=benchmark,
            scale=scale,
            status="passed",
            exit_code=0,
            elapsed_s=1.0,
            log_path=tmp_path / f"{platform}-{scale}.log",
            result_path=None,
        )

    with platform_reachability(True, probe=_probe_dying_after(3)):
        outcome = exec_phase.run_execute(
            cfg,
            log_dir=tmp_path,
            databases_root=tmp_path / "databases",
            runner=passing_runner,
            docker_runner=fake_docker,
            sleep_fn=lambda _s: None,
        )

    assert [c.scale for c in outcome.died_mid_platform] == [0.1]
    artifact = (tmp_path / "container-logs-clickhouse-server.log").read_text(encoding="utf-8")
    assert "OOMKilled=true ExitCode=137" in artifact
    assert "Code: 241 memory limit exceeded" in artifact
    assert any(argv[1] == "inspect" for argv in seen)


def test_execute_stack_death_also_claims_the_platforms_later_benchmarks(tmp_path):
    cfg = validate_config(
        {
            "name": "death spans benchmarks",
            "platforms": {"include": ["clickhouse-server"]},
            "benchmarks": {"include": ["tpch", "tpcds"]},
            "scales": {"rungs": [0.01, 0.1]},
            "cleanup": {"docker_manage_platforms": True, "docker_platform_switch": "volumes"},
        }
    )

    def recording_runner(platform, benchmark, scale, **kwargs):
        return CellResult(
            platform=platform,
            benchmark=benchmark,
            scale=scale,
            status="passed",
            exit_code=0,
            elapsed_s=1.0,
            log_path=tmp_path / "cell.log",
            result_path=None,
        )

    with platform_reachability(True, probe=_probe_dying_after(3)):
        outcome = exec_phase.run_execute(
            cfg,
            log_dir=tmp_path,
            databases_root=tmp_path / "databases",
            runner=recording_runner,
            docker_runner=_healthy_fake_docker,
            sleep_fn=lambda _s: None,
        )

    assert len(outcome.results) == 1
    assert len(outcome.died_mid_platform) == 3
    assert {c.benchmark for c in outcome.died_mid_platform} == {"tpch", "tpcds"}
    assert len(outcome.results) + len(outcome.died_mid_platform) == 4


def test_execute_stack_death_does_not_stop_the_next_platform(tmp_path):
    cfg = validate_config(
        {
            "name": "death advances",
            "platforms": {"include": ["clickhouse-server", "duckdb"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01, 0.1]},
            "cleanup": {"docker_manage_platforms": True, "docker_platform_switch": "volumes"},
        }
    )

    def probe(platform, **_kwargs):
        if platform != "clickhouse-server":
            return True
        probe.calls += 1
        return probe.calls <= 2

    probe.calls = 0

    with platform_reachability(True, probe=probe):
        outcome = exec_phase.run_execute(
            cfg,
            log_dir=tmp_path,
            databases_root=tmp_path / "databases",
            runner=_stub_runner_factory({0.01: 1.0, 0.1: 1.0}, {0.01: True, 0.1: True}),
            docker_runner=_healthy_fake_docker,
            sleep_fn=lambda _s: None,
        )

    assert outcome.aborted is False
    assert {c.platform for c in outcome.died_mid_platform} == {"clickhouse-server"}
    assert any(r.platform == "duckdb" and r.status == "passed" for r in outcome.results)


def test_execute_liveness_probe_disabled_by_zero_timeout(tmp_path):
    cfg = validate_config(
        {
            "name": "liveness off",
            "platforms": {"include": ["clickhouse-server"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01, 0.1]},
            "execute": {"liveness_probe_timeout_s": 0},
            "cleanup": {"docker_manage_platforms": True, "docker_platform_switch": "volumes"},
        }
    )

    with platform_reachability(True, probe=_probe_dying_after(1)):
        outcome = exec_phase.run_execute(
            cfg,
            log_dir=tmp_path,
            databases_root=tmp_path / "databases",
            runner=_stub_runner_factory({0.01: 1.0, 0.1: 1.0}, {0.01: True, 0.1: True}),
            docker_runner=_healthy_fake_docker,
            sleep_fn=lambda _s: None,
        )

    assert outcome.died_mid_platform == ()
    assert len(outcome.results) == 2


def test_execute_liveness_probe_not_armed_for_a_platform_that_was_never_reachable(tmp_path):
    cfg = validate_config(
        {
            "name": "never reachable",
            "platforms": {"include": ["clickhouse-server"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01, 0.1]},
            "execute": {"skip_unreachable": False},
            "cleanup": {"docker_manage_platforms": True, "docker_platform_switch": "volumes"},
        }
    )

    with platform_reachability(True, probe=_probe_dying_after(1)):
        outcome = exec_phase.run_execute(
            cfg,
            log_dir=tmp_path,
            databases_root=tmp_path / "databases",
            runner=_stub_runner_factory({0.01: 1.0, 0.1: 1.0}, {0.01: True, 0.1: True}),
            docker_runner=_healthy_fake_docker,
            sleep_fn=lambda _s: None,
        )

    assert outcome.died_mid_platform == ()
    assert len(outcome.results) == 2


def test_execute_readiness_check_does_not_poison_the_reachability_cache(tmp_path):
    cfg = _managed_docker_cfg("no cache poisoning")
    cache_when_skip_check_ran: list[dict] = []
    real_is_reachable = matrix.platform_is_reachable

    def spy(platform, *args, **kwargs):
        cache_when_skip_check_ran.append(dict(matrix._REACHABILITY_CACHE))
        return real_is_reachable(platform, *args, **kwargs)

    with (
        patch.object(matrix, "tcp_probe", return_value=True),
        patch.object(exec_phase, "platform_is_reachable", side_effect=spy),
    ):
        outcome = exec_phase.run_execute(
            cfg,
            log_dir=tmp_path,
            databases_root=tmp_path / "databases",
            runner=_stub_runner_factory({0.01: 1.0}, {0.01: True}),
            docker_runner=_healthy_fake_docker,
            sleep_fn=lambda _s: None,
        )

    assert outcome.aborted is False
    assert cache_when_skip_check_ran == [{}]


def test_execute_readiness_check_fails_closed_on_an_empty_compose_ps_table(tmp_path):
    cfg = _managed_docker_cfg("empty ps table")

    def fake_docker(argv, **kwargs):
        if _docker_verb(argv) == "ps":
            return docker_assets.DockerCommandResult(tuple(argv), 0, "NAME   IMAGE   STATUS\n", "")
        return docker_assets.DockerCommandResult(tuple(argv), 0, "", "")

    def fail_runner(platform, benchmark, scale, **kwargs):
        raise AssertionError("no cell may run when compose ps -a lists no services")

    with platform_reachability(True):
        outcome = exec_phase.run_execute(
            cfg,
            log_dir=tmp_path,
            databases_root=tmp_path / "databases",
            runner=fail_runner,
            docker_runner=fake_docker,
            sleep_fn=lambda _s: None,
        )

    assert outcome.aborted is False
    assert any(cell.platform == "clickhouse-server" for cell in outcome.startup_failed)
    readiness_event = next(e for e in outcome.docker_events if e.action == "readiness")
    assert "listed no services" in readiness_event.message


def test_execute_readiness_check_requests_ps_all(tmp_path):
    cfg = _managed_docker_cfg("ps all argv")
    ps_argvs: list[tuple[str, ...]] = []

    def fake_docker(argv, **kwargs):
        if _docker_verb(argv) == "ps":
            ps_argvs.append(tuple(argv))
            return _healthy_ps_result(argv)
        if _docker_verb(argv) == "stats":
            return _healthy_stats_result(argv)
        return docker_assets.DockerCommandResult(tuple(argv), 0, "", "")

    with platform_reachability(True):
        exec_phase.run_execute(
            cfg,
            log_dir=tmp_path,
            databases_root=tmp_path / "databases",
            runner=_stub_runner_factory({0.01: 1.0}, {0.01: True}),
            docker_runner=fake_docker,
            sleep_fn=lambda _s: None,
        )

    assert ps_argvs and all(argv[-2:] == ("ps", "-a") for argv in ps_argvs)


def test_enumerate_prunes_ai_primitives_on_platforms_without_ai_functions():
    raw = {
        "platforms": {"include": ["duckdb", "sqlite", "spark"]},
        "benchmarks": {"include": ["ai_primitives", "tpch"]},
        "scales": {"rungs": [0.01]},
    }

    result = enum_phase.enumerate_cells_with_pruning(_cfg(raw))

    assert {c.benchmark for c in result.cells} == {"tpch"}
    assert {(c.platform, c.benchmark) for c in result.compatibility_pruned} == {
        ("duckdb", "ai_primitives"),
        ("sqlite", "ai_primitives"),
        ("spark", "ai_primitives"),
    }
    assert all(c.rule_id.endswith(".ai_primitives.benchmark_gate") for c in result.compatibility_pruned)
