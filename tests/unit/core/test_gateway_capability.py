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
    GatewayNotImplementedError,
    UnsupportedGatewayError,
    register_gateway_connection_specs,
    resolve_requested_gateway,
    supported_gateways,
)
from benchbox.core.hooks.platform_hooks import PlatformHookRegistry, PlatformOptionError
from benchbox.core.platform_manifest import (
    PLATFORM_MANIFEST,
    _validate_capabilities,
    _validate_gateways,
    _validate_manifest_set,
    get_platform_manifest_entry,
)
from benchbox.core.platform_registry import PlatformRegistry
from benchbox.core.results.analytics import _load_regression_runs
from benchbox.core.results.execution_variant import variants_comparable
from benchbox.core.results.models import BenchmarkResults, QueryExecution
from benchbox.core.results.schema import SchemaV2Validator, build_result_payload

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_manifest_declares_snowflake_and_databricks_gateways() -> None:
    snowflake = get_platform_manifest_entry("snowflake")
    assert snowflake is not None
    sf_gateways = snowflake.capabilities.get("gateways", {})
    assert set(sf_gateways.keys()) == {"espresso", "greybeam", "custom"}
    assert sf_gateways["espresso"]["requires_host"] is True
    assert sf_gateways["espresso"]["routes_to"] == {"snowflake": {"class": "standard"}}
    assert sf_gateways["greybeam"]["requires_host"] is True
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
    assert dbx_gateways["espresso"]["requires_host"] is True
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


@pytest.mark.parametrize(
    ("platform", "gateway"), [("snowflake", "espresso"), ("snowflake", "custom"), ("databricks", "espresso")]
)
def test_resolve_requested_gateway_fails_closed_for_declared_gateways(platform: str, gateway: str) -> None:
    with pytest.raises(GatewayNotImplementedError, match=f"gateway routing is not implemented for {platform} yet"):
        resolve_requested_gateway(platform, gateway)


def test_register_gateway_connection_specs() -> None:
    register_gateway_connection_specs()
    specs = PlatformHookRegistry.list_option_specs("snowflake")
    for opt in GATEWAY_CONNECTION_OPTIONS:
        assert opt in specs


def test_gateway_connection_options_are_typed() -> None:
    register_gateway_connection_specs()
    parsed = PlatformHookRegistry.parse_options(
        "snowflake",
        [("gateway_port", "8443"), ("allow_insecure_gateway", "false"), ("gateway_ocsp_fail_open", "yes")],
    )
    assert parsed["gateway_port"] == 8443
    assert parsed["allow_insecure_gateway"] is False
    assert parsed["gateway_ocsp_fail_open"] is True
    for pair in [("gateway_port", "abc"), ("gateway_port", "70000"), ("allow_insecure_gateway", "maybe")]:
        with pytest.raises(PlatformOptionError):
            PlatformHookRegistry.parse_options("snowflake", [pair])


def test_variants_comparable() -> None:
    def bundle(platform: dict, config: dict | None = None) -> dict:
        return {"platform": {"name": "Snowflake", **platform}, "config": config or {}}

    run_native = bundle({"execution_engine": {"requested": "default"}})
    run_espresso = bundle({"gateway": {"name": "espresso", "routed": True}})
    run_greybeam = bundle({"gateway": {"name": "greybeam", "routed": True}})
    run_diff_engine = bundle({"execution_engine": {"requested": "streaming"}})
    run_legacy_streaming = bundle({}, {"platform_options": {"streaming": True}})
    run_legacy_engine_requested = bundle({"config": {"engine_requested": "streaming"}})

    assert variants_comparable(run_native, run_native) is True
    assert variants_comparable(run_native, run_espresso) is False
    assert variants_comparable(run_espresso, run_greybeam) is False
    assert variants_comparable(run_native, run_diff_engine) is False
    assert variants_comparable(run_native, run_legacy_streaming) is False
    assert variants_comparable(run_native, run_legacy_engine_requested) is False
    assert variants_comparable(run_legacy_streaming, run_legacy_engine_requested) is True


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

    runs, other_variants = _load_regression_runs(
        [file1, file2], platform="snowflake", benchmark="tpch", lookback_runs=5
    )
    assert len(runs) == 1
    assert runs[0]["file"] == "run1.json"
    assert other_variants == ["run2.json"]


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
    assert "gateway differs (native vs espresso)" in " ".join(res_blocked.output.split())

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
    assert "tpc_compliant" not in payload["platform"]
    assert payload["tpc_compliant"] is False
    assert payload["platform"]["variant"] == "snowflake@greybeam"

    queries = {q["id"]: q.get("execution_engine") for q in payload["queries"]}
    assert queries["1"] == "snowflake"
    assert queries["2"] == "duckdb"
    assert queries["3"] == "unknown"

    assert payload["platform"]["execution_engine"]["observed"] == "mixed"


def test_unknown_gateway_does_not_preserve_query_engine() -> None:
    result = BenchmarkResults(
        benchmark_name="TPC-H",
        platform="snowflake",
        scale_factor=0.01,
        execution_id="run-unknown-gateway",
        timestamp=datetime.now(),
        duration_seconds=1.0,
        total_queries=1,
        successful_queries=1,
        failed_queries=0,
        platform_info={"gateway": {"name": "unregistered", "routed": True}},
        query_results=[
            QueryExecution(query_id="Q1", status="SUCCESS", execution_time_ms=1.0, execution_engine="rogue")
        ],
    )

    payload = build_result_payload(result)

    assert payload["queries"][0]["execution_engine"] == "unknown"


def test_platform_option_registration_includes_gateway_options_without_the_cli() -> None:
    import subprocess
    import sys

    script = (
        "import benchbox.platforms\n"
        "from benchbox.core.hooks.platform_hooks import PlatformHookRegistry\n"
        "print(sorted(name for name in PlatformHookRegistry.list_option_specs('snowflake') if 'gateway' in name))\n"
        "print(PlatformHookRegistry.parse_options('databricks', [('allow_insecure_gateway', 'false')]))\n"
    )
    completed = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)

    lines = completed.stdout.strip().splitlines()
    assert lines[0] == str(sorted(GATEWAY_CONNECTION_OPTIONS))
    assert "'allow_insecure_gateway': False" in lines[1]


def test_mcp_run_benchmark_rejects_gateway_connection_options(tmp_path: Path) -> None:
    from mcp.server.mcpserver import MCPServer

    from benchbox.mcp.tools.benchmark import register_benchmark_tools
    from tests.unit.mcp.public_api import get_tool

    register_gateway_connection_specs()
    server = MCPServer("test", version="test")
    register_benchmark_tools(server, results_dir=tmp_path)
    run_benchmark = get_tool(server, "run_benchmark")

    with patch("benchbox.mcp.tools.benchmark._get_platform_adapter") as get_adapter:
        response = run_benchmark.fn(
            platform="snowflake",
            benchmark="tpch",
            scale_factor=0.01,
            platform_options={"gateway_host": "gw.example.com"},
        )

    assert response["status"] == "failed"
    assert "Platform option 'gateway_host' is not authorized for MCP" in response["message"]
    get_adapter.assert_not_called()
