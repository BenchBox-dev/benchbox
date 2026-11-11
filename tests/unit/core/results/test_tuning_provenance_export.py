# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import json
from datetime import datetime

import pytest

from benchbox.core.results.exporter import ResultExporter
from benchbox.core.results.models import BenchmarkResults
from benchbox.core.results.schema import (
    build_applied_ledger_payload,
    build_result_payload,
    build_tuning_payload,
)
from benchbox.core.tuning.interface import TableTuning, TuningColumn, UnifiedTuningConfiguration
from benchbox.core.tuning.policy_generation import TUNING_POLICY_GENERATION

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
        "timestamp": datetime(2026, 7, 16),
        "duration_seconds": 1.0,
        "total_queries": 1,
        "successful_queries": 1,
        "failed_queries": 0,
        "query_results": [
            {
                "query_id": "Q1",
                "status": "SUCCESS",
                "execution_time_seconds": 0.1,
                "rows_returned": 1,
                "run_type": "measurement",
            }
        ],
    }
    defaults.update(overrides)
    return BenchmarkResults(**defaults)


def _applied_ledger_payload(status: str = "applied_unverified") -> dict:
    return {
        "status": status,
        "applied_ledger_hash": "f" * 64,
        "statements": [
            {
                "statement": "CREATE INDEX IF NOT EXISTS idx_lineitem_sort ON LINEITEM (l_orderkey)",
                "phase": "ddl",
                "status": "executed",
            }
        ],
        "dropped": [],
    }


def test_template_run_bundle_carries_source_enum_template_ref_and_hash():
    config = _tuned_config()
    applied = _applied_ledger_payload()
    result = _make_result(
        tunings_applied=config.to_dict(),
        tuning_source="auto_discovered",
        tuning_source_file="examples/tunings/duckdb/tpch_tuned.yaml",
        tuning_config_hash=config.get_configuration_hash(),
        tuning_validation_status="applied_unverified",
        applied_tuning_ledger=applied,
        applied_ledger_hash=applied["applied_ledger_hash"],
    )

    payload = build_result_payload(result)
    summary = payload["platform"]["tuning"]

    assert summary["tuning_source"] == "auto_discovered"
    assert summary["requested_config_hash"] == config.get_configuration_hash()
    assert summary["source"] == "yaml"
    assert summary["hash"] == config.get_configuration_hash()
    assert summary["counts"]["tables_tuned"] == 1
    assert "sorting" in summary["counts"]["tuning_types"]
    assert summary["applied_ledger_hash"] == applied["applied_ledger_hash"]
    assert summary["validation_status"] == "applied_unverified"

    companion = build_tuning_payload(result)
    assert companion["tuning_source"] == "auto_discovered"
    assert companion["source_file"] == "examples/tunings/duckdb/tpch_tuned.yaml"
    assert companion["requested_config_hash"] == config.get_configuration_hash()
    assert companion["hash"] == config.get_configuration_hash()
    assert companion["applied_ledger_hash"] == applied["applied_ledger_hash"]
    assert companion["validation_status"] == "applied_unverified"
    assert companion["requested"]["table_tunings"]["lineitem"]["sorting"][0]["name"] == "l_orderkey"


def test_applied_ledger_companion_payload_is_the_execution_record():
    config = _tuned_config()
    applied = _applied_ledger_payload()
    result = _make_result(
        tunings_applied=config.to_dict(),
        tuning_source="auto_discovered",
        tuning_config_hash=config.get_configuration_hash(),
        tuning_validation_status="applied_unverified",
        applied_tuning_ledger=applied,
        applied_ledger_hash=applied["applied_ledger_hash"],
    )

    companion = build_applied_ledger_payload(result)

    assert companion is not None
    assert companion["status"] == "applied_unverified"
    assert companion["applied_ledger_hash"] == applied["applied_ledger_hash"]
    assert companion["statements"][0]["statement"].startswith("CREATE INDEX IF NOT EXISTS")
    assert companion["statements"][0]["status"] == "executed"


def test_no_applied_ledger_returns_no_applied_companion():
    result = _make_result(
        tunings_applied=_tuned_config().to_dict(),
        tuning_source="auto_discovered",
        tuning_config_hash=_tuned_config().get_configuration_hash(),
    )
    assert build_applied_ledger_payload(result) is None
    assert "applied_ledger_hash" not in build_tuning_payload(result)


def test_explicit_file_source_also_bridges_to_legacy_yaml():
    config = _tuned_config()
    result = _make_result(
        tunings_applied=config.to_dict(),
        tuning_source="explicit_file",
        tuning_source_file="my_custom_tuning.yaml:0123456789abcdef",
        tuning_config_hash=config.get_configuration_hash(),
    )

    summary = build_result_payload(result)["platform"]["tuning"]

    assert summary["source"] == "yaml"


def test_fallback_run_carries_source_fallback_and_auto_bridge():
    config = UnifiedTuningConfiguration()
    result = _make_result(
        tunings_applied=config.to_dict(),
        tuning_source="fallback",
        tuning_source_file=None,
        tuning_config_hash=config.get_configuration_hash(),
    )

    summary = build_result_payload(result)["platform"]["tuning"]
    companion = build_tuning_payload(result)

    assert summary["tuning_source"] == "fallback"
    assert summary["source"] == "auto"
    assert companion["tuning_source"] == "fallback"
    assert companion["source"] == "auto"
    assert "source_file" not in companion


def test_wizard_run_carries_source_wizard():
    config = _tuned_config()
    result = _make_result(
        tunings_applied=config.to_dict(),
        tuning_source="wizard",
        tuning_source_file=None,
        tuning_config_hash=config.get_configuration_hash(),
    )

    summary = build_result_payload(result)["platform"]["tuning"]

    assert summary["tuning_source"] == "wizard"
    assert summary["source"] == "auto"


def test_requested_config_hash_distinct_across_differing_configs():
    baseline_result = _make_result(
        tunings_applied=UnifiedTuningConfiguration().to_dict(),
        tuning_source="auto_discovered",
        tuning_config_hash=UnifiedTuningConfiguration().get_configuration_hash(),
    )
    tuned_config = _tuned_config()
    tuned_result = _make_result(
        tunings_applied=tuned_config.to_dict(),
        tuning_source="auto_discovered",
        tuning_config_hash=tuned_config.get_configuration_hash(),
    )

    baseline_hash = build_result_payload(baseline_result)["platform"]["tuning"]["requested_config_hash"]
    tuned_hash = build_result_payload(tuned_result)["platform"]["tuning"]["requested_config_hash"]

    assert baseline_hash != tuned_hash


def test_no_absolute_paths_anywhere_in_emitted_bundle_or_companion():
    config = _tuned_config()
    result = _make_result(
        tunings_applied=config.to_dict(),
        tuning_source="explicit_file",
        tuning_source_file="custom_tuning.yaml:ab12cd34ef56ab12",
        tuning_config_hash=config.get_configuration_hash(),
    )

    bundle_payload = build_result_payload(result)
    companion_payload = build_tuning_payload(result)

    bundle_text = json.dumps(bundle_payload)
    companion_text = json.dumps(companion_payload)

    for text in (bundle_text, companion_text):
        assert "/home/" not in text
        assert "/Users/" not in text
        assert "/root/" not in text
        assert "C:\\\\" not in text


def test_anonymized_requested_tuning_hides_identifiers_and_preserves_source(tmp_path):
    config = _tuned_config()
    result = _make_result(
        tunings_applied=config.to_dict(),
        tuning_source="explicit_file",
        tuning_source_file="examples/tunings/custom.yaml:0123456789abcdef",
        tuning_config_hash=config.get_configuration_hash(),
    )

    exporter = ResultExporter(output_dir=tmp_path, anonymize=True)
    payload = exporter._build_export_tuning_payload(result)

    table_tunings = payload["requested"]["table_tunings"]
    table_key = next(iter(table_tunings))
    assert table_key.startswith("table_")
    assert table_tunings[table_key]["table_name"].startswith("table_")
    assert table_tunings[table_key]["sorting"][0]["name"].startswith("column_")
    assert payload["source_file"] == "examples/tunings/custom.yaml:0123456789abcdef"


def test_anonymized_requested_tuning_hashes_legacy_absolute_source(tmp_path):
    config = _tuned_config()
    result = _make_result(
        tunings_applied=config.to_dict(),
        tuning_source="explicit_file",
        tuning_source_file="/Users/alice/private/custom.yaml",
        tuning_config_hash=config.get_configuration_hash(),
    )

    payload = ResultExporter(output_dir=tmp_path, anonymize=True)._build_export_tuning_payload(result)

    assert payload["source_file"].startswith("path_")
    assert "alice" not in json.dumps(payload)


def test_requested_block_reports_platform_optimizations_non_defaults_only():
    config = UnifiedTuningConfiguration()
    config.platform_optimizations.z_ordering_enabled = True
    config.platform_optimizations.z_ordering_columns = ["l_shipdate"]
    result = _make_result(
        tunings_applied=config.to_dict(),
        tuning_source="auto_discovered",
        tuning_config_hash=config.get_configuration_hash(),
    )

    companion = build_tuning_payload(result)

    platform_optimizations = companion["requested"]["platform_optimizations"]
    assert platform_optimizations["z_ordering_enabled"] is True
    assert platform_optimizations["z_ordering_columns"] == ["l_shipdate"]
    assert "auto_optimize_enabled" not in platform_optimizations
    assert "sorted_ingestion_mode" not in platform_optimizations


def test_no_tuning_returns_only_the_validation_status_and_no_companion():
    result = _make_result(tunings_applied=None)

    assert build_tuning_payload(result) is None
    assert build_result_payload(result)["platform"]["tuning"] == {"validation_status": "not_applicable"}


def test_tuned_bundle_carries_explicit_tuning_policy_generation_marker():
    config = _tuned_config()
    result = _make_result(
        tunings_applied=config.to_dict(),
        tuning_source="auto_discovered",
        tuning_config_hash=config.get_configuration_hash(),
    )

    summary = build_result_payload(result)["platform"]["tuning"]
    companion = build_tuning_payload(result)

    assert summary["tuning_policy_generation"] == TUNING_POLICY_GENERATION
    assert companion["tuning_policy_generation"] == TUNING_POLICY_GENERATION


def test_no_tuning_omits_tuning_policy_generation_marker():
    result = _make_result(tunings_applied=None)

    assert "tuning_policy_generation" not in build_result_payload(result)["platform"]["tuning"]
    assert build_tuning_payload(result) is None


def test_packaged_resource_source_maps_to_yaml_legacy_bridge():
    from benchbox.core.results.schema import _legacy_tuning_source_bridge

    assert _legacy_tuning_source_bridge("packaged_resource", "tpch_tuned.yaml:abc123") == "yaml"
    assert _legacy_tuning_source_bridge("packaged_resource", None) == "yaml"
    assert _legacy_tuning_source_bridge("smart_defaults", None) == "auto"


