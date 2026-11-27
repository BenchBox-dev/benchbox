from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchbox.mcp.schemas import MCP_CLICKHOUSE_PROFILE_ENV
from tests.unit.mcp.public_api import call_tool

pytestmark = [pytest.mark.unit, pytest.mark.medium]

pytest.importorskip("mcp", reason="MCP SDK not installed. Install with: uv add benchbox --extra mcp")


@pytest.fixture
def server(tmp_path: Path):
    from benchbox.mcp import create_server

    return create_server(results_dir=tmp_path / "results", charts_dir=tmp_path / "charts")


def _dry_run(server, **arguments):
    return call_tool(server, "run_benchmark", benchmark="tpch", scale_factor=0.01, dry_run=True, **arguments)


def _validate_only(server, **arguments):
    return call_tool(server, "run_benchmark", benchmark="tpch", scale_factor=0.01, validate_only=True, **arguments)


class TestRemovedOptions:
    @pytest.mark.parametrize("call", [_dry_run, _validate_only])
    @pytest.mark.parametrize(
        ("platform", "option", "value", "replacement"),
        [
            ("polars-df", "streaming", True, "'--execution-engine streaming' (execution_engine='streaming')"),
            ("dask", "use_distributed", True, "'--execution-engine threaded' or '--execution-engine distributed'"),
            ("clickhouse", "deployment_mode", "server", "the platform selector '--platform clickhouse:"),
            ("clickhouse", "mode", "server", "the platform selector '--platform clickhouse:"),
            ("snowflake", "warehouse", "WH", "'compute_resource'"),
        ],
    )
    def test_removed_option_is_rejected_with_its_replacement(
        self, server, call, platform: str, option: str, value: object, replacement: str
    ) -> None:
        response = call(server, platform=platform, platform_options={option: value})

        assert response["error"] is True
        assert response["status"] == "failed"
        assert f"Platform option '{option}'" in response["message"]
        assert "was removed in BenchBox 0.6.0" in response["message"]
        assert replacement in response["message"]

    @pytest.mark.parametrize("call", [_dry_run, _validate_only])
    def test_responses_carry_no_deprecations_field(self, server, call) -> None:
        response = call(server, platform="duckdb")

        assert "deprecations" not in response

    def test_failed_validation_carries_no_deprecations_field(self, server) -> None:
        response = call_tool(
            server,
            "run_benchmark",
            platform="duckdb",
            benchmark="tpch",
            platform_options={"password": "x"},
        )

        assert response["status"] == "failed"
        assert "deprecations" not in response


class TestDryRunAppliesSettings:
    def test_dry_run_shows_the_requested_engine(self, server) -> None:
        response = _dry_run(server, platform="polars-df", execution_engine="streaming")

        assert response["status"] == "dry_run"
        assert response["execution_engine"] == "streaming"

    def test_dry_run_shows_the_platform_options(self, server) -> None:
        response = _dry_run(server, platform="duckdb", platform_options={"threads": 2})

        assert response["platform_options"] == {"threads": 2}
        assert response["execution_engine"] == "default"

    @pytest.mark.parametrize("call", [_dry_run, _validate_only])
    def test_an_unsupported_engine_is_rejected_like_a_real_run(self, server, call) -> None:
        response = call(server, platform="duckdb", execution_engine="streaming")

        assert response["error"] is True
        assert response["status"] == "failed"
        assert "does not support execution engine" in response["message"]

    def test_validate_only_echoes_the_resolved_selection(self, server) -> None:
        response = _validate_only(server, platform="polars-df", execution_engine="streaming")

        assert response["execution_engine"] == "streaming"
        assert response["platform_options"] == {}


class TestDeploymentSelector:
    @pytest.mark.parametrize("call", [_dry_run, _validate_only])
    def test_clickhouse_server_is_accepted(self, server, call) -> None:
        response = call(server, platform="clickhouse:server")

        assert response.get("error") is not True, response
        assert response["platform"] == "clickhouse-server"

    def test_clickhouse_server_is_case_insensitive(self, server) -> None:
        response = _dry_run(server, platform="ClickHouse:Server")

        assert response["platform"] == "clickhouse-server"

    def test_the_removed_embedded_synonym_is_rejected_with_the_selector(self, server) -> None:
        response = _validate_only(server, platform="clickhouse:embedded")

        assert response["error"] is True
        assert "'embedded'" in response["message"]
        assert "use 'local' instead" in response["message"]

    def test_a_manifest_declared_mode_on_another_platform_is_accepted(self, server) -> None:
        response = _validate_only(server, platform="timescaledb:cloud")

        assert response.get("error") is not True, response
        assert response["deployment"] == "cloud"
        assert response["platform"] == "timescaledb"

    @pytest.mark.parametrize("platform", ["duckdb:cluster", "snowflake:server", "clickhouse:mars", "clickhouse:"])
    def test_an_undeclared_mode_is_rejected(self, server, platform) -> None:
        response = _validate_only(server, platform=platform)

        assert response["error"] is True
        assert response["status"] == "failed"

    @pytest.mark.parametrize("platform", ["a:b:c", ":server", "clickhouse::server", "clickhouse:server/x"])
    def test_malformed_selectors_are_rejected(self, server, platform) -> None:
        response = _validate_only(server, platform=platform)

        assert response["error"] is True

    def test_velox_remote_is_not_reachable_through_the_selector(self, server) -> None:
        response = _validate_only(server, platform="velox:remote")

        assert response["error"] is True
        assert "not authorized for MCP" in response["message"]

    def test_connection_profile_is_accepted_on_the_selected_server_deployment(
        self, server, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(MCP_CLICKHOUSE_PROFILE_ENV, json.dumps({"analytics": {"port": 9440, "secure": True}}))

        for platform in ("clickhouse:server", "clickhouse-server"):
            response = _validate_only(server, platform=platform, platform_options={"connection_profile": "analytics"})
            assert response.get("error") is not True, response
            assert response["platform_options"] == {"connection_profile": "analytics"}

    def test_connection_profile_on_the_default_local_deployment_is_rejected(
        self, server, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(MCP_CLICKHOUSE_PROFILE_ENV, json.dumps({"analytics": {"port": 9440, "secure": True}}))

        response = _validate_only(server, platform="clickhouse", platform_options={"connection_profile": "analytics"})

        assert response["error"] is True
        assert "requires the server deployment" in response["message"]
