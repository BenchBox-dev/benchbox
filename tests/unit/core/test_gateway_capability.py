from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from benchbox.cli.commands.compare import compare
from benchbox.core.gateway import (
    DEFAULT_GATEWAY,
    GATEWAY_CONNECTION_OPTIONS,
    GatewayConfigurationError,
    UnsupportedGatewayError,
    register_gateway_connection_specs,
    resolve_requested_gateway,
    supported_gateways,
    variants_comparable,
)
from benchbox.core.hooks.platform_hooks import PlatformHookRegistry
from benchbox.core.platform_manifest import (
    PLATFORM_MANIFEST,
    _validate_capabilities,
    _validate_gateways,
    _validate_manifest_set,
    get_platform_manifest_entry,
)
from benchbox.core.platform_registry import PlatformRegistry
from benchbox.core.results.analytics import _load_regression_runs
from benchbox.core.results.models import BenchmarkResults, QueryExecution
from benchbox.core.results.schema import SchemaV2Validator, build_result_payload

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_manifest_declares_snowflake_and_databricks_gateways() -> None:
    snowflake = get_platform_manifest_entry("snowflake")
    assert snowflake is not None
    sf_gateways = snowflake.capabilities.get("gateways", {})
    assert set(sf_gateways.keys()) == {"espresso", "greybeam", "custom"}
    assert sf_gateways["espresso"]["requires_host"] is False
    assert sf_gateways["espresso"]["routes_to"] == {"snowflake": {"class": "standard"}}
    assert sf_gateways["greybeam"]["requires_host"] is False
    assert sf_gateways["greybeam"]["routes_to"] == {
        "snowflake": {"class": "standard"},
        "duckdb": {"class": "delegated"},
    }
    assert sf_gateways["custom"]["requires_host"] is True
    assert sf_gateways["custom"]["routes_to"] == {"snowflake": {"class": "standard"}}

    databricks = get_platform_manifest_entry("databricks")
    assert databricks is not None
    dbx_gateways = databricks.capabilities.get("gateways", {})
    assert set(dbx_gateways.keys()) == {"espresso", "custom"}
    assert dbx_gateways["espresso"]["requires_host"] is False
    assert dbx_gateways["espresso"]["routes_to"] == {"databricks": {"class": "standard"}}
    assert dbx_gateways["custom"]["requires_host"] is True
    assert dbx_gateways["custom"]["routes_to"] == {"databricks": {"class": "standard"}}


def test_manifest_validation_rejects_non_dict_gateways() -> None:
    with pytest.raises(ValueError, match="gateways must be an object"):
        _validate_gateways("test", "not_a_dict")


def test_manifest_validation_rejects_native_gateway_declaration() -> None:
    with pytest.raises(ValueError, match="cannot declare gateway 'native'"):
        _validate_gateways("test", {"native": {"routes_to": {"test": {"class": "standard"}}, "requires_host": False}})


def test_manifest_validation_rejects_missing_requires_host() -> None:
    with pytest.raises(ValueError, match="must have routes_to and requires_host"):
        _validate_gateways("test", {"gw": {"routes_to": {"test": {"class": "standard"}}}})


def test_manifest_validation_rejects_custom_without_host() -> None:
    with pytest.raises(ValueError, match="gateway 'custom' must require a host"):
        _validate_gateways("test", {"custom": {"routes_to": {"test": {"class": "standard"}}, "requires_host": False}})


def test_manifest_validation_rejects_unknown_engine_class_in_routes() -> None:
    with pytest.raises(ValueError, match="has unknown class 'magic'"):
        _validate_gateways(
            "test",
            {"gw": {"routes_to": {"test": {"class": "magic"}}, "requires_host": False}},
        )


def test_manifest_validation_rejects_empty_routes_to() -> None:
    with pytest.raises(ValueError, match="routes_to must be a non-empty object"):
        _validate_gateways("test", {"gw": {"routes_to": {}, "requires_host": False}})


def test_cross_platform_manifest_rejects_inconsistent_route_engine_class() -> None:
    from benchbox.core.platform_manifest import PlatformManifestEntry

    entry1 = PlatformManifestEntry(
        key="p1",
        aliases=(),
        metadata={
            "capabilities": {
                "gateways": {"gw1": {"requires_host": False, "routes_to": {"duckdb": {"class": "delegated"}}}}
            }
        },
        adapter=None,
    )
    entry2 = PlatformManifestEntry(
        key="p2",
        aliases=(),
        metadata={
            "capabilities": {
                "gateways": {"gw2": {"requires_host": False, "routes_to": {"duckdb": {"class": "standard"}}}}
            }
        },
        adapter=None,
    )
    with pytest.raises(ValueError, match="Execution engine 'duckdb'"):
        _validate_manifest_set([entry1, entry2])


def test_resolve_requested_gateway_native_and_defaults() -> None:
    assert resolve_requested_gateway("snowflake", None) == DEFAULT_GATEWAY
    assert resolve_requested_gateway("snowflake", "") == DEFAULT_GATEWAY
    assert resolve_requested_gateway("snowflake", "native") == DEFAULT_GATEWAY


def test_resolve_requested_gateway_unsupported_on_non_gateway_platform() -> None:
    with pytest.raises(
        UnsupportedGatewayError, match="Platform 'duckdb' does not support gateway 'espresso'. Allowed: native"
    ):
        resolve_requested_gateway("duckdb", "espresso")


def test_resolve_requested_gateway_unsupported_value_lists_allowed() -> None:
    with pytest.raises(UnsupportedGatewayError, match="Allowed: native, custom, espresso, greybeam"):
        resolve_requested_gateway("snowflake", "unknown_gw")


def test_resolve_requested_gateway_custom_requires_host() -> None:
    with pytest.raises(
        GatewayConfigurationError, match="Gateway 'custom' on platform 'snowflake' requires gateway_host"
    ):
        resolve_requested_gateway("snowflake", "custom")

    assert resolve_requested_gateway("snowflake", "custom", gateway_host="gw.example.com") == "custom"


def test_register_gateway_connection_specs() -> None:
    register_gateway_connection_specs()
    specs = PlatformHookRegistry.list_option_specs("snowflake")
    for opt in GATEWAY_CONNECTION_OPTIONS:
        assert opt in specs


def test_variants_comparable() -> None:
    run_native = {
        "platform": {
            "deployment": {"selected": "managed"},
            "execution_engine": {"requested": "default"},
        }
    }
    run_espresso = {
        "platform": {
            "deployment": {"selected": "managed"},
            "execution_engine": {"requested": "default"},
            "gateway": {"name": "espresso", "routed": True},
        }
    }
    run_greybeam = {
        "platform": {
            "deployment": {"selected": "managed"},
            "execution_engine": {"requested": "default"},
            "gateway": {"name": "greybeam", "routed": True},
        }
    }
    run_diff_engine = {
        "platform": {
            "deployment": {"selected": "managed"},
            "execution_engine": {"requested": "streaming"},
        }
    }
    run_legacy_streaming = {
        "config": {"platform_options": {"streaming": True}},
        "platform": {"deployment": {"selected": "managed"}},
    }
    run_legacy_engine_requested = {
        "platform": {
            "deployment": {"selected": "managed"},
            "config": {"engine_requested": "streaming"},
        }
    }
    loaded_legacy_result = BenchmarkResults(
        benchmark_name="TPC-H",
        platform="snowflake",
        scale_factor=0.01,
        execution_id="run-3",
        timestamp=datetime.now(),
        duration_seconds=1.0,
        total_queries=0,
        successful_queries=0,
        failed_queries=0,
        platform_info={"deployment": {"selected": "managed"}},
        execution_engine={"requested": "streaming"},
    )

    assert variants_comparable(run_native, run_native) is True
    assert variants_comparable(run_native, run_espresso) is False
    assert variants_comparable(run_espresso, run_greybeam) is False
    assert variants_comparable(run_native, run_diff_engine) is False
    assert variants_comparable(run_native, run_legacy_streaming) is False
    assert variants_comparable(run_native, run_legacy_engine_requested) is False
    assert variants_comparable(run_legacy_streaming, run_legacy_engine_requested) is True
    assert variants_comparable(run_native, loaded_legacy_result) is False


def test_analytics_regression_baseline_excludes_heterogeneous_runs(tmp_path: Path) -> None:
    run1 = {
        "run": {"timestamp": "2026-10-10T10:00:00Z"},
        "benchmark": {"id": "tpch", "scale_factor": 0.01},
        "platform": {"name": "snowflake", "gateway": {"name": "espresso", "routed": True}},
        "queries": [{"run_type": "measurement", "id": "Q1", "ms": 100.0}],
    }
    run2 = {
        "run": {"timestamp": "2026-10-10T09:00:00Z"},
        "benchmark": {"id": "tpch", "scale_factor": 0.01},
        "platform": {"name": "snowflake"},
        "queries": [{"run_type": "measurement", "id": "Q1", "ms": 95.0}],
    }
    file1 = tmp_path / "run1.json"
    file2 = tmp_path / "run2.json"
    file1.write_text(json.dumps(run1))
    file2.write_text(json.dumps(run2))

    runs = _load_regression_runs([file1, file2], platform="snowflake", benchmark="tpch", lookback_runs=5)
    assert len(runs) == 1
    assert runs[0]["file"] == "run1.json"


def test_compare_cli_guards_heterogeneous_runs(tmp_path: Path) -> None:
    runner = CliRunner()
    file_native = tmp_path / "native.json"
    file_gateway = tmp_path / "gateway.json"

    native_bundle = {
        "result_schema_version": "2.3",
        "benchmark": {"name": "TPC-H", "id": "tpch", "scale_factor": 0.01, "test_type": "power"},
        "platform": {"name": "snowflake"},
        "run": {"timestamp": "2026-10-10T10:00:00Z", "id": "run-1", "total_duration_ms": 1000},
        "queries": [{"id": "Q1", "ms": 100.0, "status": "SUCCESS", "run_type": "measurement"}],
    }
    gateway_bundle = {
        "result_schema_version": "2.3",
        "benchmark": {"name": "TPC-H", "id": "tpch", "scale_factor": 0.01, "test_type": "power"},
        "platform": {"name": "snowflake", "gateway": {"name": "espresso", "routed": True}},
        "run": {"timestamp": "2026-10-10T11:00:00Z", "id": "run-2", "total_duration_ms": 1050},
        "queries": [{"id": "Q1", "ms": 105.0, "status": "SUCCESS", "run_type": "measurement"}],
    }

    file_native.write_text(json.dumps(native_bundle))
    file_gateway.write_text(json.dumps(gateway_bundle))

    res_blocked = runner.invoke(compare, [str(file_native), str(file_gateway)])
    assert res_blocked.exit_code != 0
    assert "Cannot compare runs with different execution variants" in res_blocked.output

    res_allowed = runner.invoke(compare, [str(file_native), str(file_gateway), "--allow-heterogeneous-comparison"])
    assert res_allowed.exit_code == 0


def test_build_result_payload_records_gateway_and_enforces_compliance() -> None:
    res = BenchmarkResults(
        benchmark_name="TPC-H",
        platform="snowflake",
        scale_factor=0.01,
        execution_id="run-1",
        timestamp=datetime.now(),
        duration_seconds=10.0,
        total_queries=3,
        successful_queries=3,
        failed_queries=0,
        platform_info={
            "gateway": {"name": "greybeam", "routed": True},
            "execution_engine": {"requested": "default", "applied": "standard", "resolution": "platform_default"},
        },
        query_results=[
            QueryExecution(query_id="Q1", status="SUCCESS", execution_time_ms=50.0, execution_engine="snowflake"),
            QueryExecution(query_id="Q2", status="SUCCESS", execution_time_ms=60.0, execution_engine="duckdb"),
            QueryExecution(
                query_id="Q3", status="SUCCESS", execution_time_ms=70.0, execution_engine="unregistered_engine"
            ),
        ],
    )
    payload = build_result_payload(res)
    SchemaV2Validator().validate(payload)

    assert payload["platform"]["gateway"] == {"name": "greybeam", "routed": True}
    assert payload["platform"]["tpc_compliant"] is False
    assert payload["tpc_compliant"] is False
    assert payload["platform"]["variant"] == "snowflake@greybeam"

    queries = {q["id"]: q.get("execution_engine") for q in payload["queries"]}
    assert queries["1"] == "snowflake"
    assert queries["2"] == "duckdb"
    assert queries["3"] == "unknown"

    assert payload["platform"]["execution_engine"]["observed"] == "mixed"
