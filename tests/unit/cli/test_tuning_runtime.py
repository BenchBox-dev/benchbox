from __future__ import annotations

import pytest

from benchbox.cli.tuning_runtime import (
    build_baseline_unified_config,
    build_default_unified_config,
    infer_runtime_tuning_mode,
)
from benchbox.core.tuning.interface import TuningType, UnifiedTuningConfiguration

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_infer_runtime_tuning_mode_reports_baseline_as_notuning() -> None:
    config = build_baseline_unified_config()

    enabled, mode = infer_runtime_tuning_mode(config)

    assert enabled is False
    assert mode == "notuning"


def test_infer_runtime_tuning_mode_reports_none_as_notuning() -> None:
    enabled, mode = infer_runtime_tuning_mode(None)

    assert enabled is False
    assert mode == "notuning"


def test_infer_runtime_tuning_mode_reports_enabled_config_as_tuned() -> None:
    config = UnifiedTuningConfiguration()
    config.enable_primary_keys()

    enabled, mode = infer_runtime_tuning_mode(config)

    assert enabled is True
    assert mode == "tuned"


def test_infer_runtime_tuning_mode_reports_platform_optimization_as_tuned() -> None:
    config = UnifiedTuningConfiguration()
    config.enable_platform_optimization(TuningType.Z_ORDERING)

    enabled, mode = infer_runtime_tuning_mode(config)

    assert enabled is True
    assert mode == "tuned"


def test_build_baseline_unified_config_reports_no_clustering_strategy() -> None:
    config = build_baseline_unified_config()

    assert config.platform_optimizations.databricks_clustering_strategy == "none"
    assert config.platform_optimizations.z_ordering_enabled is False
    assert config.platform_optimizations.liquid_clustering_enabled is False


@pytest.mark.parametrize("platform", ["duckdb", "DuckDB"])
def test_default_unified_config_disables_unique_and_check_constraints_on_duckdb(platform: str) -> None:
    config = build_default_unified_config(platform)

    assert config.check_constraints.enabled is False
    assert config.primary_keys.enabled is True
    assert config.foreign_keys.enabled is True
    assert config.unique_constraints.enabled is False


def test_default_unified_config_shares_base_rules_on_duckdb_memory_variant() -> None:
    config = build_default_unified_config("duckdb:memory")

    assert config.check_constraints.enabled is False
    assert config.unique_constraints.enabled is False


def test_default_unified_config_without_platform_keeps_all_constraints() -> None:
    assert build_default_unified_config(None).to_dict() == UnifiedTuningConfiguration().to_dict()


@pytest.mark.parametrize("platform", ["postgresql", "snowflake"])
def test_default_unified_config_disables_unrendered_unique_and_check(platform: str) -> None:
    config = build_default_unified_config(platform)

    assert config.primary_keys.enabled is True
    assert config.foreign_keys.enabled is True
    assert config.unique_constraints.enabled is False
    assert config.check_constraints.enabled is False


def test_default_unified_config_disables_unrendered_constraints_on_motherduck() -> None:
    config = build_default_unified_config("motherduck")

    assert config.primary_keys.enabled is False
    assert config.foreign_keys.enabled is False
    assert config.unique_constraints.enabled is False
    assert config.check_constraints.enabled is False
