from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from benchbox.mcp.deprecations import mcp_replacement
from benchbox.mcp.schemas import MCP_CLICKHOUSE_PROFILE_ENV
from tests.unit.mcp.public_api import call_tool

pytestmark = [pytest.mark.unit, pytest.mark.medium]

pytest.importorskip("mcp", reason="MCP SDK not installed. Install with: uv add benchbox --extra mcp")

DEPRECATION_KEYS = {"option", "replacement", "removed_in", "message"}


@pytest.fixture
def server(tmp_path: Path):
    from benchbox.mcp import create_server

    return create_server(results_dir=tmp_path / "results", charts_dir=tmp_path / "charts", log_level="ERROR")


def _dry_run(server, **arguments):
    return call_tool(server, "run_benchmark", benchmark="tpch", scale_factor=0.01, dry_run=True, **arguments)


def _validate_only(server, **arguments):
    return call_tool(server, "run_benchmark", benchmark="tpch", scale_factor=0.01, validate_only=True, **arguments)


class TestDeprecationsField:
    def test_dry_run_with_a_deprecated_alias_reports_the_mcp_replacement(self, server) -> None:
        response = _dry_run(server, platform="polars-df", platform_options={"streaming": True})

        assert response["status"] == "dry_run"
        assert response["deprecations"] == [
            {
                "option": "streaming",
                "replacement": "execution_engine='streaming'",
                "removed_in": "0.6.0",
                "message": (
                    "Platform option 'streaming' for 'polars' is deprecated and will be removed in "
                    "BenchBox 0.6.0; use execution_engine='streaming' instead."
                ),
            }
        ]
        assert "--" not in json.dumps(response["deprecations"])

    def test_validate_only_with_a_deprecated_alias_reports_it(self, server) -> None:
        response = _validate_only(server, platform="polars-df", platform_options={"streaming": True})

        assert response["valid"] is True
        assert [entry["option"] for entry in response["deprecations"]] == ["streaming"]
        assert set(response["deprecations"][0]) == DEPRECATION_KEYS

    @pytest.mark.parametrize("call", [_dry_run, _validate_only])
    def test_field_is_present_and_empty_when_nothing_is_deprecated(self, server, call) -> None:
        response = call(server, platform="duckdb")

        assert response["deprecations"] == []

    def test_failed_validation_still_carries_the_field(self, server) -> None:
        response = call_tool(
            server,
            "run_benchmark",
            platform="duckdb",
            benchmark="tpch",
            platform_options={"password": "x"},
        )

        assert response["status"] == "failed"
        assert response["deprecations"] == []

    def test_deprecations_are_scoped_to_one_call(self, server) -> None:
        first = _dry_run(server, platform="polars-df", platform_options={"streaming": True})
        second = _dry_run(server, platform="polars-df", platform_options={"streaming": True})
        clean = _dry_run(server, platform="duckdb")

        assert len(first["deprecations"]) == 1
        assert second["deprecations"] == first["deprecations"]
        assert clean["deprecations"] == []

    def test_real_run_path_returns_the_entry_for_a_deprecated_alias(self, server, tmp_path: Path) -> None:
        with patch("benchbox.core.run_service.execute_run", return_value=None):
            response = call_tool(
                server,
                "run_benchmark",
                platform="polars-df",
                benchmark="tpch",
                scale_factor=0.01,
                queries="6",
                platform_options={"streaming": True},
            )

        assert response["mcp_metadata"]["status"] == "no_results"
        assert [entry["replacement"] for entry in response["deprecations"]] == ["execution_engine='streaming'"]

    def test_deprecated_clickhouse_deployment_mode_names_the_platform_replacement(self, server) -> None:
        response = _dry_run(server, platform="clickhouse", platform_options={"deployment_mode": "server"})

        assert response["status"] == "dry_run"
        assert [(entry["option"], entry["replacement"]) for entry in response["deprecations"]] == [
            ("deployment_mode", "clickhouse-server")
        ]


class TestDryRunAppliesSettings:
    def test_dry_run_shows_the_requested_engine(self, server) -> None:
        response = _dry_run(server, platform="polars-df", execution_engine="streaming")

        assert response["status"] == "dry_run"
        assert response["execution_engine"] == "streaming"

    def test_dry_run_shows_the_engine_a_legacy_option_selects(self, server) -> None:
        response = _dry_run(server, platform="polars-df", platform_options={"streaming": True})

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

    @pytest.mark.parametrize("call", [_dry_run, _validate_only])
    def test_conflicting_engine_sources_are_rejected_like_a_real_run(self, server, call) -> None:
        response = call(
            server,
            platform="polars-df",
            execution_engine="in-memory",
            platform_options={"streaming": True},
        )

        assert response["status"] == "failed"
        assert "Conflicting execution engine sources" in response["message"]

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
        assert response["deprecations"] == []

    def test_clickhouse_server_is_case_insensitive(self, server) -> None:
        response = _dry_run(server, platform="ClickHouse:Server")

        assert response["platform"] == "clickhouse-server"

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

    def test_the_mode_alias_is_rejected(self, server) -> None:
        response = _validate_only(server, platform="clickhouse", platform_options={"mode": "server"})

        assert response["error"] is True
        assert "not authorized" in response["message"]

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


class TestReplacementWording:
    @pytest.mark.parametrize(
        ("cli_wording", "mcp_wording"),
        [
            ("'--execution-engine streaming' (execution_engine='streaming')", "execution_engine='streaming'"),
            ("'--platform clickhouse-server'", "clickhouse-server"),
            ("'--platform velox:remote'", "velox:remote"),
            ("'--execution-engine duckdb'", "execution_engine='duckdb'"),
            ("'compute_resource'", "'compute_resource'"),
        ],
    )
    def test_cli_flags_are_translated(self, cli_wording: str, mcp_wording: str) -> None:
        assert mcp_replacement(cli_wording) == mcp_wording
