from __future__ import annotations

from pathlib import Path

import pytest

from benchbox.cli.config import ConfigManager
from benchbox.core.tpch.benchmark import TPCHBenchmark
from benchbox.core.tuning.applied_ledger import APPLIED_VERIFIED
from benchbox.core.tuning.interface import TuningType
from benchbox.core.tuning.introspection import (
    CONSTRAINT_FOREIGN_KEY,
    CONSTRAINT_PRIMARY_KEY,
    CORROBORATED,
    KIND_CONSTRAINT,
    UNVERIFIABLE,
)
from benchbox.platforms.duckdb import DuckDBAdapter

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
]

SHIPPED_TPCH_TEMPLATE = (
    Path(__file__).resolve().parents[3] / "benchbox" / "core" / "tuning" / "templates" / "duckdb" / "tpch_tuned.yaml"
)


@pytest.fixture(scope="module")
def tuned_tpch_result(tmp_path_factory: pytest.TempPathFactory):
    base = tmp_path_factory.mktemp("constraint_corroboration_tpch")
    benchmark = TPCHBenchmark(scale_factor=0.01, output_dir=str(base / "data"))
    benchmark.generate_data()
    config = ConfigManager().load_unified_tuning_config(SHIPPED_TPCH_TEMPLATE, platform="duckdb")
    adapter = DuckDBAdapter(
        type="duckdb", database_path=str(base / "tpch.duckdb"), tuning_enabled=True, tuning_config=config
    )
    return config, adapter.run_benchmark(benchmark, benchmark_type="olap", query_subset=["1"])


def test_shipped_template_requests_constraints_without_partitioning_or_check(tuned_tpch_result):
    config, _result = tuned_tpch_result
    assert config.primary_keys.enabled and config.foreign_keys.enabled
    assert config.check_constraints.enabled is False
    assert not any(tuning.get_columns_by_type(TuningType.PARTITIONING) for tuning in config.table_tunings.values())


def test_shipped_tpch_template_has_no_unverifiable_constraint_entries(tuned_tpch_result):
    _config, result = tuned_tpch_result
    entries = result.applied_tuning_ledger["receipt"]["entries"]
    constraint_entries = [entry for entry in entries if entry.get("kind") == KIND_CONSTRAINT]

    assert {entry["constraint_type"] for entry in constraint_entries} == {
        CONSTRAINT_PRIMARY_KEY,
        CONSTRAINT_FOREIGN_KEY,
    }
    assert len(constraint_entries) == 17
    assert all(entry["verdict"] == CORROBORATED for entry in constraint_entries)
    assert not [entry for entry in entries if entry["verdict"] == UNVERIFIABLE]


def test_shipped_tpch_template_reaches_applied_verified(tuned_tpch_result):
    _config, result = tuned_tpch_result
    assert result.applied_tuning_ledger["receipt"]["corroborated"] is True
    assert result.tuning_validation_status == APPLIED_VERIFIED
