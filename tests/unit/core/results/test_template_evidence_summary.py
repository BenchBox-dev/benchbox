from __future__ import annotations

from datetime import datetime

import pytest

from benchbox.core.results.models import BenchmarkResults
from benchbox.core.results.schema import build_result_payload
from benchbox.core.tuning import template_evidence
from benchbox.core.tuning.interface import TableTuning, TuningColumn, UnifiedTuningConfiguration

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _tuned_config() -> UnifiedTuningConfiguration:
    config = UnifiedTuningConfiguration()
    config.table_tunings["lineitem"] = TableTuning(
        table_name="lineitem",
        sorting=[TuningColumn(name="l_orderkey", type="INTEGER", order=1)],
    )
    return config


def _make_result(**overrides) -> BenchmarkResults:
    defaults = {
        "benchmark_name": "tpch",
        "platform": "duckdb",
        "scale_factor": 0.01,
        "execution_id": "run-1",
        "timestamp": datetime(2026, 10, 8),
        "duration_seconds": 1.0,
        "total_queries": 1,
        "successful_queries": 1,
        "failed_queries": 0,
        "query_results": [],
    }
    defaults.update(overrides)
    return BenchmarkResults(**defaults)


def test_tuned_run_records_unmeasured_template_evidence_with_hash_unchanged():
    config = _tuned_config()
    config_hash = config.get_configuration_hash()
    result = _make_result(
        tunings_applied=config.to_dict(),
        tuning_source="auto_discovered",
        tuning_source_file="examples/tunings/duckdb/tpch_tuned.yaml",
        tuning_config_hash=config_hash,
        tuning_validation_status="applied_unverified",
    )

    summary = build_result_payload(result)["platform"]["tuning"]

    assert summary["template_evidence"] == "unmeasured"
    assert summary["requested_config_hash"] == config_hash
    assert summary["hash"] == config_hash


def test_template_evidence_absent_without_tuned_template():
    config = _tuned_config()
    result = _make_result(
        tunings_applied=config.to_dict(),
        tuning_source="explicit_file",
        tuning_source_file="my_custom.yaml",
        tuning_config_hash=config.get_configuration_hash(),
    )

    assert "template_evidence" not in build_result_payload(result)["platform"]["tuning"]


def test_template_evidence_absent_for_untuned_run():
    result = _make_result(tuning_validation_status="not_applicable")

    assert "template_evidence" not in build_result_payload(result)["platform"].get("tuning", {})


def test_template_evidence_absent_off_registry():
    config = _tuned_config()
    result = _make_result(
        benchmark_name="nosuchbench",
        tunings_applied=config.to_dict(),
        tuning_source_file="nosuchbench_tuned.yaml",
        tuning_config_hash=config.get_configuration_hash(),
    )

    assert "template_evidence" not in build_result_payload(result)["platform"]["tuning"]


def test_template_evidence_reflects_measured_state(monkeypatch):
    monkeypatch.setattr(template_evidence, "_REGISTRY", {"duckdb": {"tpch": {"state": "measured"}}})
    config = _tuned_config()
    result = _make_result(
        tunings_applied=config.to_dict(),
        tuning_source_file="examples/tunings/duckdb/tpch_tuned.yaml",
        tuning_config_hash=config.get_configuration_hash(),
    )

    assert build_result_payload(result)["platform"]["tuning"]["template_evidence"] == "measured"


def test_tuned_run_with_display_names_records_evidence():
    config = _tuned_config()
    result = _make_result(
        benchmark_name="TPC-H",
        platform="DuckDB",
        tunings_applied=config.to_dict(),
        tuning_source="auto_discovered",
        tuning_source_file="examples/tunings/duckdb/tpch_tuned.yaml",
        tuning_config_hash=config.get_configuration_hash(),
        tuning_validation_status="applied_unverified",
    )

    assert build_result_payload(result)["platform"]["tuning"]["template_evidence"] == "unmeasured"
