from __future__ import annotations

import pytest

from benchbox.core.run_service import translate_platform_options_for_adapter as _prepare_adapter_platform_options
from benchbox.mcp.schemas import MCPValidationError, validate_platform_options

pytestmark = [pytest.mark.integration, pytest.mark.medium]


def test_run_surface_normalizes_public_option_names_before_adapter_use() -> None:
    normalized = validate_platform_options("duckdb", {"threads": 4})

    assert normalized == {"threads": 4}
    assert _prepare_adapter_platform_options("duckdb", normalized) == {"thread_limit": 4}


def test_run_surface_rejects_an_option_without_a_contract() -> None:
    with pytest.raises(MCPValidationError, match="not authorized"):
        validate_platform_options("duckdb", {"thread_limit": 4})
