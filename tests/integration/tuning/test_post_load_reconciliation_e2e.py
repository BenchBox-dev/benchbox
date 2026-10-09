from __future__ import annotations

from pathlib import Path

import pytest

from benchbox.cli.config import ConfigManager
from benchbox.core.tpch.benchmark import TPCHBenchmark
from benchbox.core.tuning.applied_ledger import APPLIED_UNVERIFIED, APPLIED_VERIFIED
from benchbox.core.tuning.interface import UnifiedTuningConfiguration
from benchbox.platforms.duckdb import DuckDBAdapter

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
]

ROOT = Path(__file__).resolve().parents[3]
SORT_ONLY_FIXTURE = ROOT / "tests" / "fixtures" / "tuning" / "duckdb_sort_only.yaml"


@pytest.fixture(scope="module")
def tpch_data(tmp_path_factory: pytest.TempPathFactory) -> TPCHBenchmark:
    data_dir = tmp_path_factory.mktemp("reconciliation_tpch")
    benchmark = TPCHBenchmark(scale_factor=0.01, output_dir=str(data_dir))
    benchmark.generate_data()
    return benchmark


def _run(benchmark: TPCHBenchmark, config: UnifiedTuningConfiguration, database: Path):
    adapter = DuckDBAdapter(type="duckdb", database_path=str(database), tuning_enabled=True, tuning_config=config)
    return adapter.run_benchmark(benchmark, benchmark_type="olap", query_subset=["1"])


def test_duckdb_sort_only_custom_config_reaches_applied_verified_end_to_end(tpch_data, tmp_path):
    config = ConfigManager().load_unified_tuning_config(SORT_ONLY_FIXTURE, platform="duckdb")
    result = _run(tpch_data, config, tmp_path / "sort_only.duckdb")

    assert result.tuning_validation_status == APPLIED_VERIFIED
    assert result.applied_tuning_ledger["dropped"] == []
    assert "satisfied" not in result.applied_tuning_ledger
    assert result.applied_tuning_ledger["receipt"]["corroborated"] is True


def test_duckdb_inline_keys_are_satisfied_and_undeclared_unique_constraints_not_dropped(tpch_data, tmp_path):
    config = UnifiedTuningConfiguration.from_dict(
        {
            "primary_keys": {"enabled": True},
            "foreign_keys": {"enabled": True},
            "unique_constraints": {"enabled": True},
            "check_constraints": {"enabled": False},
        }
    )
    result = _run(tpch_data, config, tmp_path / "constraints.duckdb")

    ledger = result.applied_tuning_ledger
    assert result.tuning_validation_status == APPLIED_UNVERIFIED
    assert {item["intent"] for item in ledger["satisfied"]} == {"primary_keys", "foreign_keys"}
    assert ledger["dropped"] == []
