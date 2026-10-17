from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]


def _generator():
    spec = importlib.util.spec_from_file_location(
        "generate_tuning_support_matrix", REPO_ROOT / "scripts" / "generate_tuning_support_matrix.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_committed_matrix_matches_the_registry():
    generator = _generator()

    assert generator.TARGET.read_text(encoding="utf-8") == generator.render(), (
        "docs/reference/tuning-platform-support.md is out of date; run make tuning-support-matrix"
    )


def test_matrix_covers_every_registry_platform():
    from benchbox.core.tuning.capability_registry import PLATFORM_TUNING_CAPABILITIES, SESSION_CONFIG_CAPABILITIES

    text = _generator().render()

    for platform in PLATFORM_TUNING_CAPABILITIES.keys() | SESSION_CONFIG_CAPABILITIES.keys():
        assert f"| `{platform}` |" in text


def test_only_introspected_platforms_can_reach_applied_verified():
    rows = [line for line in _generator().render().splitlines() if line.startswith("| `")]
    verifiable = {row.split("|")[1].strip().strip("`") for row in rows if row.rstrip(" |").endswith("yes")}

    assert verifiable == {"clickhouse", "duckdb", "snowflake"}
