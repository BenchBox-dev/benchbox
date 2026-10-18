from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
import yaml

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


def _measurement(state):
    return {
        "state": state,
        "scale_factor": 0.01,
        "memory": {"basis": "host_total_gb", "value": 16},
        "engine_version": "v1",
        "measured_on": "2026-10-11",
        "evidence": "test evidence",
    }


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


@pytest.mark.parametrize("state", ("measured-benefit", "measured-neutral", "measured-regression"))
def test_template_evidence_reflects_matching_measured_state(state, monkeypatch):
    monkeypatch.setattr(template_evidence, "_REGISTRY", {"duckdb": {"tpch": {"measurements": [_measurement(state)]}}})
    config = _tuned_config()
    result = _make_result(
        tunings_applied=config.to_dict(),
        tuning_source_file="examples/tunings/duckdb/tpch_tuned.yaml",
        tuning_config_hash=config.get_configuration_hash(),
        execution_environment={"platform_runtime": {"runtime_type": "local_process"}},
        system_profile={"memory_gb": 16},
        engine_version="v1",
    )

    assert build_result_payload(result)["platform"]["tuning"]["template_evidence"] == state


def test_template_evidence_scope_mismatch_stays_unmeasured(monkeypatch):
    monkeypatch.setattr(
        template_evidence,
        "_REGISTRY",
        {
            "duckdb": {
                "tpch": {
                    "measurements": [
                        {
                            **_measurement("measured-benefit"),
                            "scale_factor": 1.0,
                        }
                    ]
                }
            }
        },
    )
    config = _tuned_config()
    result = _make_result(
        tunings_applied=config.to_dict(),
        tuning_source_file="examples/tunings/duckdb/tpch_tuned.yaml",
        tuning_config_hash=config.get_configuration_hash(),
        execution_environment={"platform_runtime": {"runtime_type": "local_process"}},
        system_profile={"memory_gb": 16},
        engine_version="v1",
    )

    assert build_result_payload(result)["platform"]["tuning"]["template_evidence"] == "unmeasured"


def test_requested_config_hash_is_independent_of_evidence_for_every_shipped_template(monkeypatch):
    repo_root = Path(__file__).resolve().parents[4]
    roots = (
        repo_root / "benchbox" / "core" / "tuning" / "templates",
        repo_root / "examples" / "tunings",
    )
    paths = sorted(path for root in roots for path in root.glob("*/*_tuned.yaml"))
    assert paths, "no shipped tuned templates found"

    for path in paths:
        source_file = path.relative_to(repo_root).as_posix()
        raw_config = yaml.safe_load(path.read_text(encoding="utf-8"))
        config = UnifiedTuningConfiguration.from_dict(raw_config)
        benchmark = path.stem.removesuffix("_liquid_tuned").removesuffix("_tuned")
        cell = template_evidence.template_cell(source_file, path.parent.name, benchmark)
        assert cell is not None, source_file
        assert config.to_dict(), source_file
        config_hash = config.get_configuration_hash()
        result = _make_result(
            benchmark_name=cell[1],
            platform=cell[0],
            scale_factor=0.01,
            tunings_applied=config.to_dict(),
            tuning_source_file=source_file,
            tuning_config_hash=config_hash,
            execution_environment={"platform_runtime": {"runtime_type": "local_process"}},
            system_profile={"memory_gb": 16},
            engine_version="v1",
        )
        hashes = []
        for state in ("measured-benefit", "measured-neutral"):
            monkeypatch.setattr(
                template_evidence,
                "_REGISTRY",
                {cell[0]: {cell[1]: {"measurements": [_measurement(state)]}}},
            )
            summary = build_result_payload(result)["platform"]["tuning"]
            assert summary["template_evidence"] == state, source_file
            hashes.append(summary["requested_config_hash"])

        assert hashes == [config_hash, config_hash], source_file


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


def test_template_evidence_uses_recorded_warehouse_size_for_managed_cloud_run():
    template_evidence.load_registry(refresh=True)
    config = _tuned_config()
    compute = {
        "warehouse_size": "X-Small",
        "warehouse_size_source": "observed",
        "multi_cluster_warehouse": False,
        "source": "observed",
    }
    fields = {
        "benchmark_name": "tpcds",
        "platform": "snowflake",
        "scale_factor": 100.0,
        "tunings_applied": config.to_dict(),
        "tuning_source_file": "examples/tunings/snowflake/tpcds_tuned.yaml",
        "tuning_config_hash": config.get_configuration_hash(),
        "execution_environment": {"platform_runtime": {"runtime_type": "managed_cloud"}},
        "platform_info": {"platform_version": "10.37.102", "engine_version": "10.37.102"},
    }

    measured = build_result_payload(_make_result(platform_compute=compute, **fields))
    multi_cluster = build_result_payload(
        _make_result(platform_compute={**compute, "multi_cluster_warehouse": True}, **fields)
    )

    assert measured["platform"]["tuning"]["template_evidence"] == "measured-benefit"
    assert multi_cluster["platform"]["tuning"]["template_evidence"] == "unmeasured"
