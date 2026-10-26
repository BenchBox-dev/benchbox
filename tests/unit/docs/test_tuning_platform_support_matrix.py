from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from benchbox.core.tuning.capability_registry import (
    PLATFORM_TUNING_CAPABILITIES,
    SESSION_CONFIG_CAPABILITIES,
    is_not_rendered_at_execution,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]


def _introspector_probe():
    spec = importlib.util.spec_from_file_location("docs_data_sources", REPO_ROOT / "scripts" / "docs_data_sources.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module._adapter_has_introspector


def test_only_introspected_platforms_can_reach_applied_verified():
    probe = _introspector_probe()
    verifiable = set()
    for platform in PLATFORM_TUNING_CAPABILITIES.keys() | SESSION_CONFIG_CAPABILITIES.keys():
        applies = any(
            not is_not_rendered_at_execution(capability)
            for capability in PLATFORM_TUNING_CAPABILITIES.get(platform, {}).values()
        )
        if probe(platform) and applies:
            verifiable.add(platform)

    assert verifiable == {"clickhouse", "duckdb", "snowflake"}
