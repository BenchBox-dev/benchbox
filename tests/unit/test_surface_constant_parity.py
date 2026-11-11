from __future__ import annotations

import pytest

from benchbox.core.constants import (
    EXECUTION_TYPES,
    EXPORT_FORMATS,
    MCP_DATA_ONLY_ALIASES,
    MCP_MODE_CHOICES,
    MCP_RESULT_FORMATS,
    QUERY_PHASES,
    RUN_MODES,
    VALID_PHASES,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _click_option_choices(command, option_name: str) -> set[str]:
    for param in command.params:
        if option_name in param.opts:
            return set(param.type.choices)
    raise AssertionError(f"{command.name} has no option {option_name}")


class TestRunModeParity:
    def test_cli_mode_choice_matches_core(self):
        from benchbox.cli.commands.run import run

        assert _click_option_choices(run, "--mode") == set(RUN_MODES)

    def test_mcp_mode_choices_are_core_modes_plus_the_data_only_shortcut(self):
        from benchbox.mcp.schemas import MODE_CHOICES

        assert set(MODE_CHOICES) == set(MCP_MODE_CHOICES)
        assert set(MCP_MODE_CHOICES) - set(RUN_MODES) == {"data_only"}

    def test_data_only_is_an_execution_type_not_a_run_mode(self):
        assert "data_only" not in RUN_MODES
        assert "data_only" in EXECUTION_TYPES

    def test_every_run_mode_is_a_platform_capability(self):
        from benchbox.core.platform_registry import PlatformRegistry

        for mode in RUN_MODES:
            assert isinstance(PlatformRegistry.supports_mode("duckdb", mode), bool)

    @pytest.mark.parametrize("alias", MCP_DATA_ONLY_ALIASES)
    def test_mcp_still_accepts_its_data_only_aliases(self, alias: str):
        from benchbox.mcp.schemas import validate_mode

        assert validate_mode(alias) == "data_only"

    @pytest.mark.parametrize("mode", RUN_MODES)
    def test_mcp_accepts_every_core_run_mode(self, mode: str):
        from benchbox.mcp.schemas import validate_mode

        assert validate_mode(mode) == mode

    def test_mcp_rejects_an_unknown_mode(self):
        from benchbox.mcp.schemas import MCPValidationError, validate_mode

        with pytest.raises(MCPValidationError):
            validate_mode("not_a_mode")


class TestPhaseParity:
    def test_phase_list_is_defined_once(self):
        from benchbox.cli.benchmarks import VALID_PHASES as interactive_phases
        from benchbox.cli.commands.run import VALID_PHASES as run_phases

        assert tuple(run_phases) == tuple(interactive_phases) == VALID_PHASES

    def test_query_phases_are_a_subset_of_valid_phases(self):
        assert set(QUERY_PHASES) < set(VALID_PHASES)

    @pytest.mark.parametrize("phase", VALID_PHASES)
    def test_mcp_accepts_every_valid_phase(self, phase: str):
        from benchbox.mcp.schemas import validate_phases

        assert validate_phases(phase) == phase

    def test_mcp_accepts_a_phase_combination(self):
        from benchbox.mcp.schemas import validate_phases

        assert validate_phases("load,power") == "load,power"

    def test_mcp_rejects_an_unknown_phase_and_lists_the_valid_ones(self):
        from benchbox.mcp.schemas import MCPValidationError, validate_phases

        with pytest.raises(MCPValidationError) as exc_info:
            validate_phases("load,lodad")

        message = str(exc_info.value)
        assert "lodad" in message
        for phase in VALID_PHASES:
            assert phase in message

    def test_mcp_phase_validation_is_case_and_space_insensitive(self):
        from benchbox.mcp.schemas import validate_phases

        assert validate_phases(" LOAD , Power ") == "load,power"

    def test_mcp_treats_absent_and_empty_phases_alike(self):
        from benchbox.mcp.schemas import validate_phases

        assert validate_phases(None) is None
        assert validate_phases("") is None
        assert validate_phases(" , ") is None


class TestExportFormatParity:
    def test_cli_export_choice_matches_core(self):
        from benchbox.cli.commands.export import export

        assert _click_option_choices(export, "--format") == set(EXPORT_FORMATS)

    def test_mcp_result_formats_are_a_superset_of_export_formats(self):
        assert set(EXPORT_FORMATS) < set(MCP_RESULT_FORMATS)

    def test_mcp_extra_formats_are_read_modes_not_exports(self):
        assert set(MCP_RESULT_FORMATS) - set(EXPORT_FORMATS) == {"list", "details", "text", "markdown"}


