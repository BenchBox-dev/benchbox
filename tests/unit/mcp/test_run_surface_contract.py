from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest
from click import Option

from benchbox.cli.commands.run import run as cli_run

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

pytest.importorskip("mcp", reason="MCP SDK not installed. Install with: uv add benchbox --extra mcp")

REPO_ROOT = Path(__file__).resolve().parents[3]
MCP_SOURCE_ROOT = REPO_ROOT / "benchbox/mcp"

EXPECTED_TOOLS = {
    "analyze_results",
    "check_dependencies",
    "generate_chart",
    "get_benchmark_info",
    "get_query_details",
    "get_query_plan",
    "get_results",
    "list_available",
    "run_benchmark",
    "suggest_charts",
    "system_profile",
    "validate_results",
}
EXPECTED_REMOTE_TOOLS = {
    "cancel_benchmark",
    "get_benchmark_capacity",
    "get_benchmark_result",
    "get_benchmark_status",
    "start_benchmark",
}

STALE_TOOL_NAMES = {
    "aggregate_results",
    "compare_results",
    "detect_regressions",
    "dry_run",
    "export_results",
    "export_summary",
    "generate_data",
    "get_performance_trends",
    "list_benchmarks",
    "list_platforms",
    "list_recent_runs",
    "validate_config",
}

EXPECTED_RUN_PARAMS = {
    "platform": {"type": "string", "required": True, "default": None},
    "benchmark": {"type": "string", "required": True, "default": None},
    "scale_factor": {"type": "number", "required": False, "default": 0.01},
    "queries": {"type": "string or null", "required": False, "default": None},
    "phases": {"type": "string or null", "required": False, "default": None},
    "mode": {"type": "string or null", "required": False, "default": None},
    "execution_engine": {"type": "string", "required": False, "default": "default"},
    "capture_plans": {"type": "boolean", "required": False, "default": False},
    "dry_run": {"type": "boolean", "required": False, "default": False},
    "validate_only": {"type": "boolean", "required": False, "default": False},
    "link_probe": {"type": "boolean", "required": False, "default": True},
    "platform_options": {"type": "object or null", "required": False, "default": None},
}

RUN_SURFACE = json.loads((REPO_ROOT / "quality/mcp-run-surface.json").read_text(encoding="utf-8"))
MCP_TO_CLI_OPTIONS = RUN_SURFACE["run_parameter_cli_options"]
PARTIAL_CLI_SURFACES = set(RUN_SURFACE["partial_cli_surfaces"])


def _actual_cli_options() -> set[str]:
    return {
        option
        for param in cli_run.params
        if isinstance(param, Option)
        for option in param.opts
        if option.startswith("--")
    }


def _omitted_cli_surfaces() -> set[str]:
    omitted = _actual_cli_options() - {option for option in MCP_TO_CLI_OPTIONS.values() if option is not None}
    omitted.update(PARTIAL_CLI_SURFACES)
    omitted.difference_update({"--help", "--help-topic"})
    sorted_ingestion = {"--sorted-ingestion-mode", "--sorted-ingestion-method"}
    if sorted_ingestion <= omitted:
        omitted.difference_update(sorted_ingestion)
        omitted.add("--sorted-ingestion-*")
    return omitted


RATIFIED_OMISSION_TIERS = set(RUN_SURFACE["ratified_tiers"])

LEDGERED_CLI_SURFACES = _omitted_cli_surfaces()

EXPECTED_OMISSION_TIERS = RUN_SURFACE["omission_tiers"]

EXPECTED_OMITTED_CLI_FAMILIES: dict[str, str] = RUN_SURFACE["omitted_cli_families"]


def _registered_tools() -> dict[str, Any]:
    from benchbox.mcp import create_server
    from tests.unit.mcp.public_api import list_tools_by_name

    server = create_server()
    return list_tools_by_name(server)


def _schema_type(property_schema: dict[str, Any]) -> str:
    if "type" in property_schema:
        return property_schema["type"]
    if "anyOf" in property_schema:
        return " or ".join(option["type"] for option in property_schema["anyOf"])
    return "unknown"


def _schema_params(schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    required = set(schema.get("required", []))
    params: dict[str, dict[str, Any]] = {}
    for name, property_schema in schema["properties"].items():
        params[name] = {
            "type": _schema_type(property_schema),
            "required": name in required,
            "default": None if name in required else property_schema.get("default"),
        }
    return params


class TestMCPRunSurface:
    def test_registered_tool_inventory_matches_expected_tools(self):
        tools = _registered_tools()

        assert set(tools) == EXPECTED_TOOLS
        assert not (STALE_TOOL_NAMES & set(tools))

    def test_remote_server_registers_expected_remote_tools(self, tmp_path: Path):
        from benchbox.mcp import create_server
        from benchbox.mcp.security import RemoteSecurityRuntime
        from tests.integration.mcp._security import write_security_config
        from tests.unit.mcp.public_api import list_tools_by_name

        config = write_security_config(
            tmp_path,
            tokens={"token": ("tenant", ("benchbox:read", "benchbox:execute"))},
        )
        server = create_server(remote_security=RemoteSecurityRuntime.from_file(config))

        assert set(list_tools_by_name(server)) == EXPECTED_TOOLS | EXPECTED_REMOTE_TOOLS

    def test_run_benchmark_schema_matches_expected_params(self):
        schema = _registered_tools()["run_benchmark"].input_schema

        assert list(schema["properties"]) == list(EXPECTED_RUN_PARAMS)
        assert _schema_params(schema) == EXPECTED_RUN_PARAMS

    def test_run_schema_properties_map_to_cli_options(self):
        schema = _registered_tools()["run_benchmark"].input_schema

        assert set(schema["properties"]) == set(MCP_TO_CLI_OPTIONS)

    def test_omission_tiers_cover_every_omitted_cli_surface(self):
        assert set(EXPECTED_OMISSION_TIERS) == LEDGERED_CLI_SURFACES

    def test_omission_tiers_use_only_ratified_tiers(self):
        assert set(EXPECTED_OMISSION_TIERS.values()) <= RATIFIED_OMISSION_TIERS

    def test_omitted_cli_families_carry_ratified_tiers(self):
        for family, tier in sorted(EXPECTED_OMITTED_CLI_FAMILIES.items()):
            assert tier in RATIFIED_OMISSION_TIERS, family

    def test_security_scoped_omissions_are_never_promotable(self):
        security_scoped = {surface for surface, tier in EXPECTED_OMISSION_TIERS.items() if tier == "security-scoped"}

        assert set(RUN_SURFACE["security_scoped_required"]) <= security_scoped


class TestMCPImplementationBoundary:
    def test_mcp_package_does_not_import_cli_command_internals(self):
        offenders: list[str] = []

        for path in sorted(MCP_SOURCE_ROOT.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "benchbox.cli" or alias.name.startswith("benchbox.cli."):
                            offenders.append(f"{path.relative_to(REPO_ROOT)} imports {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if module == "benchbox.cli" or module.startswith("benchbox.cli."):
                        offenders.append(f"{path.relative_to(REPO_ROOT)} imports from {module}")

        assert offenders == []
