from __future__ import annotations

import ast
import re
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
MCP_DOC = REPO_ROOT / "docs/reference/mcp.md"
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

MCP_TO_CLI_OPTIONS = {
    "platform": "--platform",
    "benchmark": "--benchmark",
    "scale_factor": "--scale",
    "queries": "--queries",
    "phases": "--phases",
    "mode": "--mode",
    "execution_engine": "--execution-engine",
    "capture_plans": "--capture-plans",
    "dry_run": "--dry-run",
    "validate_only": None,
    "link_probe": "--no-link-probe",
    "platform_options": "--platform-option",
}
PARTIAL_CLI_SURFACES = {"--platform-option"}


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


RATIFIED_OMISSION_TIERS = {
    "security-scoped",
    "interaction-scoped",
    "not-yet-demanded",
}

LEDGERED_CLI_SURFACES = _omitted_cli_surfaces()

EXPECTED_OMISSION_TIERS = {
    "--benchmark-option": "security-scoped",
    "--force": "security-scoped",
    "--global-cache": "security-scoped",
    "--output": "security-scoped",
    "--platform-option": "security-scoped",
    "--publish": "security-scoped",
    "--publish-label": "security-scoped",
    "--publish-target": "security-scoped",
    "--non-interactive": "interaction-scoped",
    "--no-progress": "interaction-scoped",
    "--quiet": "interaction-scoped",
    "--verbose": "interaction-scoped",
    "--compression": "not-yet-demanded",
    "--iterations": "not-yet-demanded",
    "--no-monitoring": "not-yet-demanded",
    "--show-plans": "interaction-scoped",
    "--normalize-plan-literals": "not-yet-demanded",
    "--official": "not-yet-demanded",
    "--plan-config": "not-yet-demanded",
    "--presort": "not-yet-demanded",
    "--seed": "not-yet-demanded",
    "--sorted-ingestion-*": "not-yet-demanded",
    "--table-format": "not-yet-demanded",
    "--table-mode": "not-yet-demanded",
    "--stats-per-table-timing": "not-yet-demanded",
    "--analyze-plans": "not-yet-demanded",
    "--stats-reset": "not-yet-demanded",
    "--concurrency": "security-scoped",
    "--streams": "security-scoped",
    "--strict-translation": "not-yet-demanded",
    "--ignore-memory-warnings": "security-scoped",
    "--funding": "not-yet-demanded",
    "--result-source": "not-yet-demanded",
    "--client-region": "not-yet-demanded",
    "--client-cloud": "not-yet-demanded",
    "--tuning": "not-yet-demanded",
    "--validation": "not-yet-demanded",
    "--polars-streaming": "not-yet-demanded",
    "--gateway": "security-scoped",
}

_TABLE_SEPARATOR_CELL = re.compile(r"^:?-{3,}:?$")
_FENCE_LINE = re.compile(r"^ {0,3}(?P<marker>`{3,}|~{3,})(?P<info>.*)$")


def _fence_marker(line: str) -> str | None:
    match = _FENCE_LINE.fullmatch(line)
    if match is None:
        return None
    marker = match.group("marker")
    info = match.group("info")
    if marker[0] == "`" and "`" in info:
        return None
    return marker


def _is_fence_closer(line: str, opening_marker: str) -> bool:
    match = _FENCE_LINE.fullmatch(line)
    if match is None or match.group("info").strip():
        return False
    marker = match.group("marker")
    return marker[0] == opening_marker[0] and len(marker) >= len(opening_marker)


def _is_indented_code(line: str) -> bool:
    return line.startswith("    ") or line.startswith("\t")


def _looks_like_table_separator(line: str) -> bool:
    return "|" in line and re.search(r"-{3,}", line) is not None


def _split_markdown_table_row(line: str, line_number: int) -> list[str]:
    stripped = line.rstrip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        raise AssertionError(f"Markdown table row {line_number} must have leading and trailing pipes")

    cells: list[str] = []
    cell: list[str] = []
    escaped = False
    for character in stripped[1:-1]:
        if character == "|" and not escaped:
            cells.append("".join(cell).strip())
            cell = []
        else:
            cell.append(character)
        if character == "\\":
            escaped = not escaped
        else:
            escaped = False
    cells.append("".join(cell).strip())
    return cells


def _assert_markdown_table_topology(text: str) -> None:
    lines = text.splitlines()
    opening_fence: str | None = None
    index = 0

    while index < len(lines):
        line = lines[index]
        if opening_fence is not None:
            if _is_fence_closer(line, opening_fence):
                opening_fence = None
            index += 1
            continue
        fence_marker = _fence_marker(line)
        if fence_marker is not None:
            opening_fence = fence_marker
            index += 1
            continue
        if _is_indented_code(line) or not line.strip():
            index += 1
            continue
        if "|" not in line:
            index += 1
            continue
        if index + 1 >= len(lines) or not _looks_like_table_separator(lines[index + 1]):
            index += 1
            continue

        header = _split_markdown_table_row(line, index + 1)
        if not lines[index + 1].strip():
            raise AssertionError(f"Markdown table at row {index + 1} is missing its separator row")
        separator = _split_markdown_table_row(lines[index + 1], index + 2)
        if len(separator) != len(header) or not all(_TABLE_SEPARATOR_CELL.fullmatch(cell) for cell in separator):
            raise AssertionError(f"Markdown table at row {index + 1} has an invalid separator row")

        expected_columns = len(header)
        index += 2
        while index < len(lines):
            row = lines[index]
            if not row.strip() or row.startswith("#"):
                break
            if _fence_marker(row) is not None or _is_indented_code(row):
                break
            if "|" not in row:
                next_content = index + 1
                while next_content < len(lines) and not lines[next_content].strip():
                    next_content += 1
                if next_content < len(lines) and "|" in lines[next_content]:
                    raise AssertionError(f"Non-table content at row {index + 1} interrupts a Markdown table")
                break

            cells = _split_markdown_table_row(row, index + 1)
            if len(cells) != expected_columns:
                raise AssertionError(
                    f"Markdown table row {index + 1} has {len(cells)} columns; expected {expected_columns}"
                )
            index += 1


def _omission_ledger(text: str) -> dict[str, dict[str, str]]:
    section = _section(text, "**Scoped-surface omission ledger**", "### Discovery Tools")
    run_heading = "### Scoped-Surface Omission Ledger"
    if run_heading.lower() in section.lower():
        lower = section.lower()
        idx = lower.index(run_heading.lower())
        section = section[idx:]
    ledger: dict[str, dict[str, str]] = {}
    for line in section.splitlines():
        if not line.startswith("| `"):
            continue
        columns = [column.strip() for column in line.strip("|").split("|")]
        if len(columns) < 4:
            continue
        tier_candidate = columns[2].strip()
        if tier_candidate not in RATIFIED_OMISSION_TIERS:
            continue
        first = columns[0].strip("`")
        if not first.startswith("--"):
            continue
        ledger[first] = {
            "status": columns[1],
            "tier": tier_candidate,
            "reason": columns[3],
        }
    return ledger


_EXPECTED_TOOL_CLI_MAP: dict[str, str] = {
    "list_available": "benchbox platforms list",
    "get_benchmark_info": "benchbox benchmarks list",
    "system_profile": "benchbox profile",
    "check_dependencies": "benchbox check-deps",
    "run_benchmark": "benchbox run",
    "get_query_details": "none",
    "get_results": "benchbox results",
    "analyze_results": "benchbox compare",
    "get_query_plan": "benchbox show-plan",
    "validate_results": "validate_results",
    "suggest_charts": "benchbox visualize",
    "generate_chart": "benchbox visualize",
}

_EXPECTED_OMITTED_CLI_FAMILIES: dict[str, str] = {
    "benchbox auth": "security-scoped",
    "benchbox publish": "security-scoped",
    "benchbox submit": "security-scoped",
    "benchbox setup": "security-scoped",
    "benchbox shell": "interaction-scoped",
    "benchbox datagen": "not-yet-demanded",
    "benchbox convert": "not-yet-demanded",
    "benchbox tuning": "not-yet-demanded",
    "benchbox plan-history": "not-yet-demanded",
    "benchbox download-answers": "security-scoped",
    "benchbox metrics": "not-yet-demanded",
}


def _tool_mapping_ledger(text: str) -> dict[str, dict[str, str]]:
    section = _section(
        text,
        "### Per-Tool CLI",
        "**CLI command families with no MCP tool**",
    )
    ledger: dict[str, dict[str, str]] = {}
    for line in section.splitlines():
        if not line.startswith("| `"):
            continue
        columns = [column.strip() for column in line.strip("|").split("|")]
        if len(columns) < 4:
            continue
        tool = columns[0].strip("`")
        ledger[tool] = {
            "category": columns[1],
            "cli_counterparts": columns[2],
            "notes": columns[3],
        }
    return ledger


def _omitted_cli_families(text: str) -> dict[str, dict[str, str]]:
    section = _section(
        text,
        "**CLI command families with no MCP tool**",
        "### Scoped-Surface Omission Ledger",
    )
    ledger: dict[str, dict[str, str]] = {}
    for line in section.splitlines():
        if not line.startswith("| `"):
            continue
        columns = [column.strip() for column in line.strip("|").split("|")]
        if len(columns) < 3:
            continue
        family = columns[0].strip("`")
        ledger[family] = {
            "tier": columns[1],
            "reason": columns[2],
        }
    return ledger


def _registered_tools() -> dict[str, Any]:
    from benchbox.mcp import create_server
    from tests.unit.mcp.public_api import list_tools_by_name

    server = create_server(log_level="ERROR")
    return list_tools_by_name(server)


def _doc_text() -> str:
    return MCP_DOC.read_text(encoding="utf-8")


def _section(text: str, start_heading: str, end_heading: str) -> str:
    start = text.index(start_heading)
    end = text.index(end_heading, start)
    return text[start:end]


def _tool_section(text: str, tool_name: str) -> str:
    match = re.search(rf"^#### `{re.escape(tool_name)}`\s*$", text, flags=re.MULTILINE)
    if match is None:
        raise AssertionError(f"Missing documented tool heading for {tool_name}")
    next_heading = re.search(r"^(##|###|####) ", text[match.end() :], flags=re.MULTILINE)
    if next_heading is None:
        return text[match.end() :]
    return text[match.end() : match.end() + next_heading.start()]


def _documented_inventory_tools(text: str) -> set[str]:
    inventory = _section(text, "### Actual Tool Inventory", "### Run Surface Contract")
    inventory = inventory.split("Authenticated remote mode additionally registers:", maxsplit=1)[0]
    return set(re.findall(r"^\| `([^`]+)` \|", inventory, flags=re.MULTILINE))


def _documented_remote_inventory_tools(text: str) -> set[str]:
    inventory = _section(text, "Authenticated remote mode additionally registers:", "### Run Surface Contract")
    return set(re.findall(r"^\| `([^`]+)` \|", inventory, flags=re.MULTILINE))


def _normalize_doc_default(value: str) -> Any:
    cleaned = value.strip()
    if cleaned == "-":
        return None
    cleaned = cleaned.strip("`")
    if cleaned == "null":
        return None
    if cleaned == '""':
        return ""
    if cleaned == "false":
        return False
    if cleaned == "true":
        return True
    try:
        return int(cleaned)
    except ValueError:
        pass
    try:
        return float(cleaned)
    except ValueError:
        return cleaned


def _documented_run_params(text: str) -> dict[str, dict[str, Any]]:
    run_params_section = _section(text, "**MCP run parameter schema**", "**Behavior**")
    return _parse_params_table(run_params_section)


def _parse_params_table(markdown: str) -> dict[str, dict[str, Any]]:
    params: dict[str, dict[str, Any]] = {}
    for line in markdown.splitlines():
        if not line.startswith("| `"):
            continue
        columns = [column.strip() for column in line.strip("|").split("|")]
        name = columns[0].strip("`")
        params[name] = {
            "type": columns[1],
            "required": columns[2].startswith("Yes"),
            "default": _normalize_doc_default(columns[3]),
        }
    return params


def _documented_tool_params(text: str, tool_name: str) -> dict[str, dict[str, Any]]:
    if tool_name == "run_benchmark":
        return _documented_run_params(text)
    return _parse_params_table(_tool_section(text, tool_name))


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


class TestMCPDocsContract:
    def test_authoritative_public_contract_tables_have_valid_topology(self):
        _assert_markdown_table_topology((REPO_ROOT / "docs/reference/public-contracts.md").read_text(encoding="utf-8"))

    @pytest.mark.parametrize(
        "malformed",
        [
            "Header | Value |\n|---|---|\n| good | row |",
            "| Header | Value\n|---|---|\n| good | row |",
            "| Header | Value |\n|---|---|\n| good |",
            "| Header | Value |\n|---|not-a-separator|\n| good | row |",
            "| Header | Value |\n|---|---|\n| good | row |\nprose inserted here\n| continued | row |",
        ],
        ids=[
            "missing-leading-delimiter",
            "missing-trailing-delimiter",
            "wrong-column-count",
            "invalid-separator",
            "prose-interruption",
        ],
    )
    def test_public_contract_table_topology_rejects_malformed_rows(self, malformed: str):
        with pytest.raises(AssertionError):
            _assert_markdown_table_topology(malformed)

    @pytest.mark.parametrize(
        "non_table_markdown",
        [
            "The value is `alpha | beta`.\n",
            "```text\nvalue = alpha | beta\n```\n",
            "   ~~~text\nvalue = alpha | beta\n   ~~~\n",
            "    value = alpha | beta\n",
        ],
        ids=["prose", "backtick-fence", "tilde-fence", "indented-code"],
    )
    def test_public_contract_table_topology_ignores_non_table_pipes(self, non_table_markdown: str):
        _assert_markdown_table_topology(non_table_markdown)

    def test_docs_identify_mcp_as_scoped_surface_over_shared_core(self):
        text = _doc_text()
        normalized = " ".join(text.split())

        assert "beta-public scoped surface over the shared BenchBox engine" in normalized
        assert "all benchmark business logic lives in `benchbox.core` below both CLI and MCP" in normalized
        assert "Differences between the CLI and MCP surfaces are deliberate" in normalized
        assert "schema-level comparable to CLI result bundles" in normalized

    def test_docs_retire_the_superseded_anti_parity_framing(self):
        normalized = " ".join(_doc_text().split())

        assert "smoke/control-plane" not in normalized
        assert "not a CLI-equivalent execution surface" not in normalized
        assert "shared non-CLI execution service" not in normalized

    def test_docs_tool_inventory_matches_registered_tools(self):
        tools = _registered_tools()
        text = _doc_text()

        assert set(tools) == EXPECTED_TOOLS
        assert _documented_inventory_tools(text) == EXPECTED_TOOLS
        assert not (STALE_TOOL_NAMES & _documented_inventory_tools(text))

    def test_docs_remote_inventory_matches_authenticated_server(self, tmp_path: Path):
        from benchbox.mcp import create_server
        from benchbox.mcp.security import RemoteSecurityRuntime
        from tests.integration.mcp._security import write_security_config
        from tests.unit.mcp.public_api import list_tools_by_name

        config = write_security_config(
            tmp_path,
            tokens={"token": ("tenant", ("benchbox:read", "benchbox:execute"))},
        )
        server = create_server(log_level="ERROR", remote_security=RemoteSecurityRuntime.from_file(config))

        assert set(list_tools_by_name(server)) == EXPECTED_TOOLS | EXPECTED_REMOTE_TOOLS
        assert _documented_remote_inventory_tools(_doc_text()) == EXPECTED_REMOTE_TOOLS

    def test_docs_run_benchmark_table_matches_registered_schema(self):
        schema = _registered_tools()["run_benchmark"].input_schema
        documented_params = _documented_run_params(_doc_text())

        assert list(schema["properties"]) == list(EXPECTED_RUN_PARAMS)
        assert documented_params == EXPECTED_RUN_PARAMS
        assert _schema_params(schema) == EXPECTED_RUN_PARAMS

    def test_docs_tool_parameter_tables_match_registered_schemas(self):
        tools = _registered_tools()
        text = _doc_text()

        for tool_name, tool in tools.items():
            assert _documented_tool_params(text, tool_name) == _schema_params(tool.input_schema), tool_name

    def test_docs_record_cli_only_controls_as_intentional_omissions(self):
        schema = _registered_tools()["run_benchmark"].input_schema
        text = _doc_text()

        assert set(schema["properties"]) == set(MCP_TO_CLI_OPTIONS)
        for option in LEDGERED_CLI_SURFACES:
            assert option in text

    def test_omission_ledger_covers_every_omitted_cli_surface(self):
        ledger = _omission_ledger(_doc_text())

        assert set(ledger) == LEDGERED_CLI_SURFACES

    def test_every_ledgered_omission_carries_one_ratified_tier(self):
        ledger = _omission_ledger(_doc_text())

        for surface, entry in sorted(ledger.items()):
            assert entry["tier"] in RATIFIED_OMISSION_TIERS, surface
            assert entry["reason"], surface

    def test_ledgered_omission_tiers_match_the_ratified_classification(self):
        ledger = _omission_ledger(_doc_text())

        assert {surface: entry["tier"] for surface, entry in ledger.items()} == EXPECTED_OMISSION_TIERS

    def test_ledger_documents_the_three_ratified_tier_definitions(self):
        normalized = " ".join(_doc_text().split())

        for tier in sorted(RATIFIED_OMISSION_TIERS):
            assert f"**{tier}**" in normalized
        assert (
            "Any CLI control not listed in these tables is omitted unintentionally rather than by design." in normalized
        )

    def test_security_scoped_omissions_are_never_promotable(self):
        ledger = _omission_ledger(_doc_text())
        security_scoped = {surface for surface, entry in ledger.items() if entry["tier"] == "security-scoped"}

        assert {
            "--output",
            "--platform-option",
            "--benchmark-option",
            "--force",
            "--global-cache",
            "--publish",
            "--publish-target",
            "--publish-label",
            "--concurrency",
            "--streams",
            "--ignore-memory-warnings",
        } <= security_scoped

    def test_docs_do_not_keep_stale_standalone_tool_sections(self):
        text = _doc_text()
        documented_headings = set(re.findall(r"^#### `([^`]+)`", text, flags=re.MULTILINE))

        assert not (STALE_TOOL_NAMES & documented_headings)

    def test_per_tool_mapping_ledger_covers_all_local_tools(self):
        text = _doc_text()
        ledger = _tool_mapping_ledger(text)
        live = set(_registered_tools())

        assert set(ledger) == EXPECTED_TOOLS == live, f"tool-mapping ledger {sorted(ledger)} vs live {sorted(live)}"
        assert not (STALE_TOOL_NAMES & set(ledger))

    def test_per_tool_mapping_cli_counterparts_match_expectations(self):
        ledger = _tool_mapping_ledger(_doc_text())

        for tool, expected_cli_substr in _EXPECTED_TOOL_CLI_MAP.items():
            cli_cell = ledger[tool]["cli_counterparts"]
            if expected_cli_substr == "none":
                assert cli_cell.strip("` ") == "none", f"{tool}: expected none, got {cli_cell!r}"
            else:
                assert expected_cli_substr in cli_cell, (
                    f"{tool}: expected CLI counterpart {expected_cli_substr!r} in {cli_cell!r}"
                )

    def test_per_tool_mapping_none_is_only_for_mcp_only_conveniences(self):
        ledger = _tool_mapping_ledger(_doc_text())

        none_tools = {tool for tool, row in ledger.items() if row["cli_counterparts"].strip("` ") == "none"}
        assert none_tools == {"get_query_details"}

    def test_per_tool_mapping_rows_carry_category_and_notes(self):
        ledger = _tool_mapping_ledger(_doc_text())

        for tool, row in ledger.items():
            assert row["category"], f"{tool}: empty category"
            assert row["notes"], f"{tool}: empty notes"

    def test_omitted_cli_families_carry_exactly_one_ratified_tier(self):
        ledger = _omitted_cli_families(_doc_text())

        for family, entry in sorted(ledger.items()):
            assert entry["tier"] in RATIFIED_OMISSION_TIERS, family
            assert entry["reason"], family

    def test_omitted_cli_families_match_expected_classification(self):
        ledger = _omitted_cli_families(_doc_text())

        assert set(ledger) >= set(_EXPECTED_OMITTED_CLI_FAMILIES)
        for family, expected_tier in _EXPECTED_OMITTED_CLI_FAMILIES.items():
            assert ledger[family]["tier"] == expected_tier, family

    def test_omitted_cli_families_are_not_registered_mcp_tools(self):
        families = _omitted_cli_families(_doc_text())
        live_tools = set(_registered_tools())

        for family in families:
            tool_like = family.replace("benchbox ", "").replace("-", "_")
            assert tool_like not in live_tools, family


class TestScopedSurfaceADR:
    def test_adr_supersedes_the_smoke_control_plane_decision(self):
        adr = (REPO_ROOT / "docs/development/adr/adr-one-engine-scoped-surfaces.md").read_text(encoding="utf-8")
        normalized = " ".join(adr.split())

        assert "smoke/control-plane surface, not a CLI-equivalent execution surface" in normalized
        assert "Supersedes" in normalized
        for tier in sorted(RATIFIED_OMISSION_TIERS):
            assert tier in normalized

    def test_adr_keeps_the_import_boundaries_that_make_one_engine_possible(self):
        adr = (REPO_ROOT / "docs/development/adr/adr-one-engine-scoped-surfaces.md").read_text(encoding="utf-8")
        normalized = " ".join(adr.split())

        assert "`benchbox.core` must not import `benchbox.platforms` or `benchbox.cli`" in normalized
        assert "`benchbox.mcp` must not import `benchbox.cli`" in normalized


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
