from __future__ import annotations

import types

import pytest

from benchbox.core.execution_engine import UnsupportedExecutionEngineError
from benchbox.core.schemas import DatabaseConfig
from benchbox.mcp.schemas import (
    MCP_CORE_PARAMETER_CONTRACT,
    MCP_PLATFORM_OPTION_ALLOWLIST,
    MCP_PLATFORM_OPTION_CONTRACT,
    MCPValidationError,
    validate_execution_engine,
    validate_platform_options,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class TestExecutionEngineConfig:
    def test_database_config_defaults_to_default_engine(self):
        assert DatabaseConfig(type="duckdb", name="test").execution_engine == "default"

    def test_builder_propagates_override_engine(self):
        from benchbox.core.hooks.platform_hooks import PlatformHookRegistry

        config = PlatformHookRegistry.build_database_config("duckdb", {}, {"execution_engine": "default"})
        assert config.execution_engine == "default"

    def test_builder_propagates_option_engine(self):
        from benchbox.core.hooks.platform_hooks import PlatformHookRegistry

        config = PlatformHookRegistry.build_database_config("duckdb", {"execution_engine": "default"}, {})
        assert config.execution_engine == "default"

    @pytest.mark.parametrize(
        ("options", "overrides", "expected"),
        [
            ({"execution_engine": "in-memory"}, {"execution_engine": "streaming"}, "streaming"),
            ({"execution_engine": "in-memory"}, {}, "in-memory"),
            ({}, {"execution_engine": "streaming"}, "streaming"),
            ({"execution_engine": "streaming"}, {"execution_engine": "in-memory"}, "in-memory"),
        ],
    )
    def test_builder_gives_overrides_precedence_over_options_for_non_default_engines(
        self, options: dict, overrides: dict, expected: str
    ):
        from benchbox.core.hooks.platform_hooks import PlatformHookRegistry

        config = PlatformHookRegistry.build_database_config("polars-df", options, overrides)

        assert config.execution_engine == expected

    def test_platform_config_carries_engine_to_sql_adapters(self):
        from benchbox.core.platform_config import get_platform_config

        platform_config = get_platform_config(DatabaseConfig(type="duckdb", name="test"), None)
        assert platform_config["execution_engine"] == "default"


class TestValidateExecutionEngine:
    def test_none_and_default_accepted_everywhere(self):
        assert validate_execution_engine("duckdb", None) == "default"
        assert validate_execution_engine("duckdb", "default") == "default"

    @pytest.mark.parametrize("value", ["auto", "in-memory", "streaming"])
    def test_polars_declared_values_accepted(self, value: str):
        assert validate_execution_engine("polars-df", value) == value

    def test_undeclared_value_rejected_with_allowed_values(self):
        with pytest.raises(MCPValidationError, match="Allowed: default$"):
            validate_execution_engine("duckdb", "streaming")

    def test_non_string_rejected(self):
        with pytest.raises(MCPValidationError):
            validate_execution_engine("polars-df", True)

    def test_matches_core_validator(self):
        from benchbox.core.execution_engine import resolve_requested

        assert validate_execution_engine("polars-df", "streaming") == resolve_requested("polars-df", "streaming")

    def test_polars_engine_platform_option_removed(self):
        assert "engine" not in MCP_PLATFORM_OPTION_ALLOWLIST["polars"]
        assert "engine" not in MCP_PLATFORM_OPTION_CONTRACT["polars"]
        with pytest.raises(MCPValidationError, match="not authorized"):
            validate_platform_options("polars-df", {"engine": "streaming"})

    def test_core_parameter_contract_is_execution_class(self):
        assert MCP_CORE_PARAMETER_CONTRACT["execution_engine"].security_class == "execution"


class TestConstructionTimeValidation:
    def test_sql_adapter_rejects_undeclared_engine(self):
        from benchbox.platforms import get_platform_adapter

        with pytest.raises(UnsupportedExecutionEngineError, match="Allowed: default$"):
            get_platform_adapter("duckdb", execution_engine="streaming")

    def test_sql_adapter_accepts_default_engine(self):
        from benchbox.platforms import get_platform_adapter

        adapter = get_platform_adapter("duckdb", execution_engine="default", benchmark="tpch", scale_factor=0.01)
        assert adapter is not None

    def test_dataframe_factory_rejects_undeclared_engine(self):
        from benchbox.platforms import get_adapter

        with pytest.raises(UnsupportedExecutionEngineError, match="Allowed: default$"):
            get_adapter("duckdb", mode="sql", execution_engine="duckdb")

    def test_dataframe_factory_rejects_unknown_polars_engine(self):
        from benchbox.platforms import get_adapter

        with pytest.raises(UnsupportedExecutionEngineError, match="Allowed: default, auto, in-memory, streaming$"):
            get_adapter("polars-df", mode="dataframe", execution_engine="gpu")


class TestCliEngineResolution:
    @staticmethod
    def _resolve(platform: str, execution_engine: str | None = None) -> types.SimpleNamespace:
        from unittest.mock import MagicMock

        from benchbox.cli.platform import normalize_platform_name
        from benchbox.cli.run_platform_resolution import _resolve_platform_mode

        s = types.SimpleNamespace(
            platform=platform,
            mode=None,
            execution_engine=execution_engine,
            dry_run=True,
            ctx=MagicMock(),
            logger=None,
            platform_manager=MagicMock(),
        )
        s.platform_key = normalize_platform_name(s.platform)
        _resolve_platform_mode(s)
        return s

    def test_default_engine_resolves(self):
        assert self._resolve("duckdb").resolved_execution_engine == "default"

    def test_polars_streaming_resolves(self):
        assert self._resolve("polars-df", "streaming").resolved_execution_engine == "streaming"

    def test_undeclared_engine_exits(self):
        s = self._resolve("duckdb", "streaming")
        assert s.ctx.exit.called

    def test_non_replayable_marks_explicit_engine(self):
        from benchbox.cli.run_resolution import _active_non_replayable_options

        assert "execution_engine" in _active_non_replayable_options(types.SimpleNamespace(execution_engine="streaming"))
        assert "execution_engine" not in _active_non_replayable_options(types.SimpleNamespace(execution_engine=None))
