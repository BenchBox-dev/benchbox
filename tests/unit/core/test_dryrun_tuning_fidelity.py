from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from benchbox.core.dryrun import DryRunExecutor, tuning_preview_notes
from benchbox.core.schemas import BenchmarkConfig, DatabaseConfig
from benchbox.core.system import SystemProfiler
from benchbox.core.tuning.ddl_generator import ColumnDefinition, get_ddl_generator
from benchbox.core.tuning.interface import TableTuning, TuningColumn, UnifiedTuningConfiguration

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]


def _template(platform: str) -> UnifiedTuningConfiguration:
    path = REPO_ROOT / "examples" / "tunings" / platform / "tpch_tuned.yaml"
    return UnifiedTuningConfiguration.from_dict(yaml.safe_load(path.read_text(encoding="utf-8")))


def _benchmark_config(unified: UnifiedTuningConfiguration) -> BenchmarkConfig:
    return BenchmarkConfig(
        name="tpch",
        display_name="TPC-H",
        scale_factor=0.01,
        options={"tuning_enabled": True, "unified_tuning_configuration": unified},
    )


def test_missing_database_config_still_previews_the_requested_platform(tmp_path: Path):
    executor = DryRunExecutor(output_dir=tmp_path)

    result = executor.execute_dry_run(
        _benchmark_config(_template("snowflake")),
        SystemProfiler().get_system_profile(),
        None,
        preview_platform="snowflake",
        preview_platform_note="Driver package 'snowflake-connector-python' is not installed.",
    )

    assert result.ddl_preview, result.warnings
    assert any("snowflake-connector-python" in warning for warning in result.warnings)
    assert any("generator only" in warning for warning in result.warnings)


def test_data_only_dry_run_has_no_tuning_preview(tmp_path: Path):
    result = DryRunExecutor(output_dir=tmp_path).execute_dry_run(
        _benchmark_config(_template("snowflake")), SystemProfiler().get_system_profile(), None
    )

    assert not result.ddl_preview


def test_generator_lookup_failure_is_reported(tmp_path: Path, monkeypatch):
    import benchbox.core.tuning.ddl_generator as ddl_generator

    def _fail(platform: str):
        raise KeyError(platform)

    monkeypatch.setattr(ddl_generator, "get_ddl_generator", _fail)
    result = DryRunExecutor(output_dir=tmp_path).execute_dry_run(
        _benchmark_config(_template("snowflake")),
        SystemProfiler().get_system_profile(),
        None,
        preview_platform="snowflake",
    )

    assert any("no tuning DDL generator for snowflake" in warning for warning in result.warnings)


def test_preview_only_platforms_get_a_banner_and_per_type_notes():
    config = UnifiedTuningConfiguration()
    config.disable_all_constraints()
    config.table_tunings["orders"] = TableTuning(
        table_name="orders", partitioning=[TuningColumn(name="o_orderdate", type="DATE", order=1)]
    )

    notes = tuning_preview_notes("postgresql", config)

    assert any("comes from the tuning generator" in note for note in notes)
    assert any(
        note.startswith("Preview only: partitioning is not applied at execution on postgresql") for note in notes
    )


def test_generator_driven_platforms_get_no_banner():
    notes = tuning_preview_notes("duckdb", _template("duckdb"))

    assert not any("comes from the tuning generator" in note for note in notes)


def test_duckdb_preview_lists_the_index_and_atomic_sort_statements(tmp_path: Path):
    result = DryRunExecutor(output_dir=tmp_path).execute_dry_run(
        _benchmark_config(_template("duckdb")),
        SystemProfiler().get_system_profile(),
        DatabaseConfig(type="duckdb", name="dry"),
    )

    statements = result.post_load_statements["lineitem"]
    assert any(statement.startswith("CREATE INDEX IF NOT EXISTS idx_lineitem_sort") for statement in statements)
    assert "BEGIN TRANSACTION;" in statements and "COMMIT;" in statements
    assert any(statement.startswith('CREATE TEMP TABLE "lineitem__ctas_sort"') for statement in statements)
    assert any(statement.startswith('INSERT INTO "lineitem"') for statement in statements)


def test_athena_preview_has_no_placeholder_comment():
    generator = get_ddl_generator("athena")
    tuning = TableTuning(table_name="orders", partitioning=[TuningColumn(name="o_orderdate", type="DATE", order=1)])
    clauses = generator.generate_tuning_clauses(tuning)
    ddl = generator.generate_create_table_ddl(
        "orders",
        [
            ColumnDefinition(name="o_orderkey", data_type="INTEGER"),
            ColumnDefinition(name="o_orderdate", data_type="DATE"),
        ],
        clauses,
    )

    assert "-- Note:" not in ddl
    assert "PARTITIONED BY" not in ddl
