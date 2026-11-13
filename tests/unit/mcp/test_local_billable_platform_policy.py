from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("mcp", reason="MCP SDK not installed")

from benchbox.core.billable_platforms import ACKNOWLEDGEMENT_ENV_VAR, assess_platform, billable_platform_names
from benchbox.mcp import create_server
from benchbox.mcp.security import LocalPlatformPolicy
from tests.unit.mcp.public_api import call_tool

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    monkeypatch.delenv(ACKNOWLEDGEMENT_ENV_VAR, raising=False)


@pytest.fixture
def executed(monkeypatch):
    calls: list[dict] = []

    def fake(*args, **kwargs):
        calls.append(kwargs)
        return {"status": "completed"}

    monkeypatch.setattr("benchbox.mcp.tools.benchmark._run_benchmark_impl", fake)
    return calls


def _server(tmp_path: Path):
    return create_server(results_dir=tmp_path, charts_dir=tmp_path)


def test_default_policy_allows_no_billable_platform():
    policy = LocalPlatformPolicy.from_environment({})

    assert policy.allowed_platforms == ()
    assert policy.refusal("snowflake") is not None
    assert policy.refusal("duckdb") is None


def test_operator_environment_opts_billable_platforms_in():
    policy = LocalPlatformPolicy.from_environment({ACKNOWLEDGEMENT_ENV_VAR: "1"})

    assert set(policy.allowed_platforms) == set(billable_platform_names())
    assert policy.refusal("snowflake") is None
    assert policy.acknowledgement_record("snowflake") == {"acknowledged": True, "source": "mcp_server_environment"}


@pytest.mark.parametrize(
    "platform", [*sorted(n for n in billable_platform_names() if assess_platform(n).billable), "timescaledb:cloud"]
)
def test_billable_platform_is_refused_by_default(tmp_path: Path, executed, platform: str):
    response = call_tool(_server(tmp_path), "run_benchmark", platform=platform, benchmark="tpch", scale_factor=0.01)

    assert response["error"] is True
    assert response["status"] == "failed"
    assert executed == []


def test_local_platform_still_runs(tmp_path: Path, executed):
    response = call_tool(_server(tmp_path), "run_benchmark", platform="duckdb", benchmark="tpch", scale_factor=0.01)

    assert response.get("status") == "completed"
    assert executed[0]["billable_acknowledgement"] is None


@pytest.mark.parametrize(
    "extra",
    [
        {"platform_options": {"acknowledge_billable_platform": True}},
        {"platform_options": {ACKNOWLEDGEMENT_ENV_VAR: "1"}},
        {"platform_options": {"allowed_platforms": ["snowflake"]}},
        {"mode": "sql", "phases": "load,power"},
    ],
)
def test_no_tool_argument_grants_the_acknowledgement(tmp_path: Path, executed, extra: dict):
    response = call_tool(
        _server(tmp_path), "run_benchmark", platform="snowflake", benchmark="tpch", scale_factor=0.01, **extra
    )

    assert response.get("error") is True
    assert executed == []


def test_environment_set_after_server_start_does_not_grant(tmp_path: Path, executed, monkeypatch):
    server = _server(tmp_path)
    monkeypatch.setenv(ACKNOWLEDGEMENT_ENV_VAR, "1")

    response = call_tool(server, "run_benchmark", platform="snowflake", benchmark="tpch", scale_factor=0.01)

    assert response["error"] is True
    assert executed == []


def test_environment_at_server_start_grants_and_is_recorded(tmp_path: Path, executed, monkeypatch):
    monkeypatch.setenv(ACKNOWLEDGEMENT_ENV_VAR, "1")
    server = _server(tmp_path)

    response = call_tool(server, "run_benchmark", platform="snowflake", benchmark="tpch", scale_factor=0.01)

    assert response.get("status") == "completed"
    assert executed[0]["billable_acknowledgement"] == {"acknowledged": True, "source": "mcp_server_environment"}
