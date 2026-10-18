from __future__ import annotations

from pathlib import Path

import pytest

from tests.uat import cleanup
from tests.uat.config import validate_config
from tests.uat.phases import execute as exec_phase
from tests.uat.runner import CellResult

pytestmark = pytest.mark.medium


def test_source_reuse_graph_comes_from_registry_metadata():
    graph = cleanup.source_reuse_graph()

    assert graph["tpch"] == (
        "tpch",
        "read_primitives",
        "write_primitives",
        "transaction_primitives",
        "ai_primitives",
    )
    assert graph["tpcds"] == ("tpcds", "tpcds_obt")


def test_can_prune_blocks_when_consumer_pending():
    pending = [cleanup.CellKey("duckdb", "read_primitives", 0.01)]
    decision = cleanup.can_prune(
        "tpch",
        platform="duckdb",
        scale=0.01,
        pending_cells=pending,
        completed_cells=[],
    )
    assert decision.safe_to_prune is False
    assert "read_primitives" in decision.reason


def test_can_prune_allows_when_consumers_done():
    decision = cleanup.can_prune(
        "tpch",
        platform="duckdb",
        scale=0.01,
        pending_cells=[],
        completed_cells=[cleanup.CellKey("duckdb", "tpch", 0.01)],
    )
    assert decision.safe_to_prune is True


def test_can_prune_ignores_other_platform_consumers():
    pending = [cleanup.CellKey("sqlite", "read_primitives", 0.01)]
    decision = cleanup.can_prune(
        "tpch",
        platform="duckdb",
        scale=0.01,
        pending_cells=pending,
        completed_cells=[],
    )
    assert decision.safe_to_prune is True


def test_can_prune_unknown_source_only_blocks_on_self_consumers():
    pending = [cleanup.CellKey("duckdb", "ssb", 0.01)]
    decision = cleanup.can_prune(
        "ssb",
        platform="duckdb",
        scale=0.01,
        pending_cells=pending,
        completed_cells=[],
    )

    assert decision.safe_to_prune is False


def test_prune_database_dir_dry_run_returns_size_without_delete(tmp_path: Path):
    target = tmp_path / "duckdb" / "tpch" / "0.01"
    target.mkdir(parents=True)
    (target / "loaded.db").write_bytes(b"x" * 4096)
    bytes_freed = cleanup.prune_database_dir(tmp_path, platform="duckdb", benchmark="tpch", scale=0.01, dry_run=True)
    assert bytes_freed == 4096
    assert target.exists()


def test_prune_database_dir_actually_deletes(tmp_path: Path):
    target = tmp_path / "duckdb" / "tpch" / "0.01"
    target.mkdir(parents=True)
    (target / "loaded.db").write_bytes(b"x" * 1024)
    bytes_freed = cleanup.prune_database_dir(tmp_path, platform="duckdb", benchmark="tpch", scale=0.01)
    assert bytes_freed == 1024
    assert not target.exists()


def test_prune_database_dir_missing_target_returns_zero(tmp_path: Path):
    bytes_freed = cleanup.prune_database_dir(tmp_path, platform="duckdb", benchmark="tpch", scale=0.01)
    assert bytes_freed == 0


def test_execute_platform_chunking_walks_one_platform_at_a_time(tmp_path: Path):
    cfg = validate_config(
        {
            "name": "chunked",
            "platforms": {"include": ["duckdb", "sqlite"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
            "execute": {"platform_chunking": True},
        }
    )
    seen: list[str] = []

    def runner(platform, benchmark, scale, **kwargs):
        seen.append(platform)
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

    outcome = exec_phase.run_execute(
        cfg,
        log_dir=tmp_path,
        databases_root=tmp_path / "databases",
        runner=runner,
    )

    assert [result.platform for result in outcome.results] == ["duckdb", "sqlite"]
    assert seen == ["duckdb", "sqlite"]
    lifecycle = (tmp_path / "uat_lifecycle.log").read_text(encoding="utf-8")
    assert "[platform-chunk] start platform=duckdb" in lifecycle
    assert "[platform-chunk] start platform=sqlite" in lifecycle
    assert lifecycle.index("[platform-chunk] start platform=duckdb") < lifecycle.index(
        "[platform-chunk] start platform=sqlite"
    )


def test_execute_platform_chunking_prunes_between_chunks_via_reuse_aware_helpers(tmp_path: Path):
    cfg = validate_config(
        {
            "name": "chunk-prune",
            "platforms": {"include": ["duckdb", "sqlite"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
            "execute": {"platform_chunking": True},
        }
    )
    db_root = tmp_path / "databases"
    (db_root / "duckdb" / "tpch" / "0.01").mkdir(parents=True)
    (db_root / "duckdb" / "tpch" / "0.01" / "data.duckdb").write_text("stub")
    (db_root / "sqlite" / "tpch" / "0.01").mkdir(parents=True)
    (db_root / "sqlite" / "tpch" / "0.01" / "data.sqlite").write_text("stub")
    datagen = tmp_path / "datagen" / "tpch_sf001"
    datagen.mkdir(parents=True)
    (datagen / "keep.txt").write_text("reuse")

    seen: list[str] = []

    def runner(platform, benchmark, scale, **kwargs):
        seen.append(platform)
        if platform == "sqlite":
            assert not (db_root / "duckdb" / "tpch" / "0.01").exists()
            assert (db_root / "sqlite" / "tpch" / "0.01").exists()
            assert (datagen / "keep.txt").exists()
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

    exec_phase.run_execute(
        cfg,
        log_dir=tmp_path,
        databases_root=db_root,
        runner=runner,
    )

    assert seen == ["duckdb", "sqlite"]
    assert not (db_root / "duckdb" / "tpch" / "0.01").exists()
    assert not (db_root / "sqlite" / "tpch" / "0.01").exists()
    assert (datagen / "keep.txt").exists()
    lifecycle = (tmp_path / "uat_lifecycle.log").read_text(encoding="utf-8")
    assert "[platform-chunk] prune platform=duckdb via=reuse-aware helpers" in lifecycle
    assert "scope=per-platform-benchmark-scale" in lifecycle
