from __future__ import annotations

import json

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

pytest.importorskip("mcp", reason="MCP SDK not installed. Install with: uv add benchbox --extra mcp")


@pytest.fixture
def server(tmp_path):
    from benchbox.mcp import create_server

    return create_server(results_dir=tmp_path, charts_dir=tmp_path)


def _run(server, **kwargs):
    from tests.unit.mcp.public_api import call_tool

    return call_tool(server, "run_benchmark", platform="duckdb", **kwargs)


def _notes(response) -> list[str]:
    return [w for w in response.get("warnings", []) if "not a standard scale" in w]


def test_discovery_reports_range_and_inexact_list_for_tpch() -> None:
    from benchbox.mcp.tools.discovery import _get_benchmark_info_impl

    scale = _get_benchmark_info_impl("tpch")["scale_factors"]
    assert scale["minimum"] == 0.01
    assert scale["maximum"] == 100000
    assert scale["options_exact"] is False


def test_discovery_reports_exact_list_for_joinorder() -> None:
    from benchbox.mcp.tools.discovery import _get_benchmark_info_impl

    scale = _get_benchmark_info_impl("joinorder")["scale_factors"]
    assert scale["maximum"] == 1.0
    assert scale["options_exact"] is True


def test_dry_run_below_floor_is_rejected_naming_the_floor(server) -> None:
    response = _run(server, benchmark="tpcds", scale_factor=0.001, dry_run=True)

    assert response["error"] is True
    assert "0.01" in response["message"]


def test_validate_only_below_floor_is_invalid_naming_the_floor(server) -> None:
    response = _run(server, benchmark="tpcds", scale_factor=0.001, validate_only=True)

    assert response["valid"] is False
    assert any("0.01" in error for error in response["errors"])


@pytest.mark.parametrize("flag", ["dry_run", "validate_only"])
def test_nonstandard_value_succeeds_with_note(server, flag) -> None:
    response = _run(server, benchmark="tpch", scale_factor=2, **{flag: True})

    assert not response.get("error")
    assert response.get("valid", True) is True
    assert len(_notes(response)) == 1


@pytest.mark.parametrize("flag", ["dry_run", "validate_only"])
def test_standard_value_carries_no_note(server, flag) -> None:
    response = _run(server, benchmark="tpch", scale_factor=1, **{flag: True})

    assert not response.get("error")
    assert _notes(response) == []


def test_fixed_benchmark_rejects_other_values(server) -> None:
    response = _run(server, benchmark="joinorder", scale_factor=2, dry_run=True)

    assert response["error"] is True
    assert json.dumps(response).count("joinorder accepts scale_factor") >= 1
