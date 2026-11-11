from __future__ import annotations

import pytest

from benchbox.core.tuning.interface import (
    PlatformOptimizationConfiguration,
    TuningType,
    UnifiedTuningConfiguration,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_platform_optimization_defaults_include_sorted_ingestion_and_databricks_strategy() -> None:
    config = PlatformOptimizationConfiguration()
    assert config.sorted_ingestion_mode == "off"
    assert config.sorted_ingestion_method == "auto"
    assert config.databricks_clustering_strategy == "none"
    assert config.liquid_clustering_enabled is False
    assert config.liquid_clustering_columns == []


def test_platform_optimization_from_dict_supports_deep_sort_aliases() -> None:
    config = PlatformOptimizationConfiguration.from_dict(
        {
            "deep_sort_mode": "force",
            "deep_sort_method": "ctas",
            "databricks_clustering_strategy": "liquid_clustering",
            "liquid_clustering_enabled": True,
            "liquid_clustering_columns": ["event_time"],
        }
    )
    assert config.sorted_ingestion_mode == "force"
    assert config.sorted_ingestion_method == "ctas"
    assert config.databricks_clustering_strategy == "liquid_clustering"
    assert config.liquid_clustering_enabled is True
    assert config.liquid_clustering_columns == ["event_time"]


def test_platform_optimization_accepts_liquid_auto_strategy() -> None:
    config = PlatformOptimizationConfiguration(
        databricks_clustering_strategy="liquid_clustering_auto",
        liquid_clustering_enabled=True,
        physical_rendering_id="databricks_liquid_auto",
    )

    assert config.databricks_clustering_strategy == "liquid_clustering_auto"
    assert config.physical_rendering_id == "databricks_liquid_auto"


def test_platform_optimization_accepts_hilbert_method() -> None:
    config = PlatformOptimizationConfiguration(sorted_ingestion_mode="force", sorted_ingestion_method="hilbert")
    assert config.sorted_ingestion_method == "hilbert"


def test_platform_optimization_rejects_invalid_sorted_ingestion_mode() -> None:
    with pytest.raises(ValueError, match="Invalid sorted_ingestion_mode"):
        PlatformOptimizationConfiguration(sorted_ingestion_mode="invalid")


def test_platform_optimization_rejects_method_when_mode_off() -> None:
    with pytest.raises(ValueError, match="sorted_ingestion_method must be 'auto'"):
        PlatformOptimizationConfiguration(sorted_ingestion_mode="off", sorted_ingestion_method="ctas")


def test_platform_optimization_rejects_liquid_columns_without_enablement() -> None:
    with pytest.raises(ValueError, match="liquid_clustering_columns requires liquid_clustering_enabled=true"):
        PlatformOptimizationConfiguration(liquid_clustering_columns=["event_time"])


def test_platform_optimization_rejects_liquid_and_zorder_conflict() -> None:
    with pytest.raises(ValueError, match="Liquid Clustering cannot be combined"):
        PlatformOptimizationConfiguration(
            z_ordering_enabled=True,
            liquid_clustering_enabled=True,
            databricks_clustering_strategy="liquid_clustering",
        )


def test_platform_optimization_rejects_auto_with_manual_columns() -> None:
    with pytest.raises(ValueError, match="liquid_clustering_columns cannot be set"):
        PlatformOptimizationConfiguration(
            databricks_clustering_strategy="liquid_clustering_auto",
            liquid_clustering_enabled=True,
            liquid_clustering_columns=["event_time"],
        )


def test_platform_optimization_rejects_none_with_layout_fields() -> None:
    with pytest.raises(ValueError, match="cannot be combined"):
        PlatformOptimizationConfiguration(
            databricks_clustering_strategy="none",
            z_ordering_enabled=True,
        )


def test_platform_optimization_rejects_rendering_strategy_conflict() -> None:
    with pytest.raises(ValueError, match="databricks_z_order cannot be combined"):
        PlatformOptimizationConfiguration(
            databricks_clustering_strategy="liquid_clustering",
            liquid_clustering_enabled=True,
            physical_rendering_id="databricks_z_order",
        )


def test_platform_optimization_rejects_zorder_strategy_with_liquid_flags() -> None:
    with pytest.raises(ValueError, match="databricks_clustering_strategy='z_order' cannot be combined"):
        PlatformOptimizationConfiguration(
            databricks_clustering_strategy="z_order",
            liquid_clustering_enabled=True,
        )


def test_platform_optimization_rejects_liquid_auto_rendering_without_auto_strategy() -> None:
    with pytest.raises(ValueError, match="databricks_liquid_auto requires"):
        PlatformOptimizationConfiguration(
            databricks_clustering_strategy="liquid_clustering",
            liquid_clustering_enabled=True,
            physical_rendering_id="databricks_liquid_auto",
        )


def test_platform_optimization_rejects_liquid_manual_rendering_with_auto_strategy() -> None:
    with pytest.raises(ValueError, match="databricks_liquid_manual requires manual"):
        PlatformOptimizationConfiguration(
            databricks_clustering_strategy="liquid_clustering_auto",
            liquid_clustering_enabled=True,
            physical_rendering_id="databricks_liquid_manual",
        )


def test_platform_optimization_rejects_liquid_manual_rendering_without_liquid_request() -> None:
    with pytest.raises(ValueError, match="databricks_liquid_manual requires manual"):
        PlatformOptimizationConfiguration(
            databricks_clustering_strategy="z_order",
            physical_rendering_id="databricks_liquid_manual",
        )


def test_platform_optimization_rejects_zorder_rendering_with_non_zorder_strategy() -> None:
    with pytest.raises(ValueError, match="databricks_z_order requires databricks_clustering_strategy='z_order'"):
        PlatformOptimizationConfiguration(
            databricks_clustering_strategy="none",
            physical_rendering_id="databricks_z_order",
        )


def test_platform_optimization_accepts_liquid_manual_rendering_with_manual_strategy() -> None:
    config = PlatformOptimizationConfiguration(
        databricks_clustering_strategy="liquid_clustering",
        liquid_clustering_enabled=True,
        liquid_clustering_columns=["event_time"],
        physical_rendering_id="databricks_liquid_manual",
    )
    assert config.physical_rendering_id == "databricks_liquid_manual"


def test_databricks_liquid_validation_rejects_partitioning_and_distribution_fields() -> None:
    from benchbox.core.tuning.interface import TableTuning, TuningColumn

    config = UnifiedTuningConfiguration()
    config.platform_optimizations.databricks_clustering_strategy = "liquid_clustering_auto"
    config.platform_optimizations.liquid_clustering_enabled = True
    config.table_tunings["orders"] = TableTuning(
        table_name="orders",
        partitioning=[TuningColumn(name="o_orderdate", type="DATE", order=1)],
    )
    config.table_tunings["lineitem"] = TableTuning(
        table_name="lineitem",
        distribution=[TuningColumn(name="l_orderkey", type="INTEGER", order=1)],
    )

    errors = config.validate_for_platform("databricks")
    assert any("incompatible with per-table partitioning" in error for error in errors)
    assert any("no user-managed distribution key" in error for error in errors)


def test_unified_tuning_enable_disable_liquid_clustering() -> None:
    config = UnifiedTuningConfiguration()
    config.enable_platform_optimization(TuningType.LIQUID_CLUSTERING, columns=["event_time"])

    assert config.platform_optimizations.liquid_clustering_enabled is True
    assert config.platform_optimizations.databricks_clustering_strategy == "liquid_clustering"
    assert config.platform_optimizations.liquid_clustering_columns == ["event_time"]
    assert TuningType.LIQUID_CLUSTERING in config.get_enabled_tuning_types()

    config.disable_platform_optimization(TuningType.LIQUID_CLUSTERING)
    assert config.platform_optimizations.liquid_clustering_enabled is False


def test_platform_optimization_from_dict_defaults_to_none_without_layout_flags() -> None:
    config = PlatformOptimizationConfiguration.from_dict({})

    assert config.databricks_clustering_strategy == "none"


def test_platform_optimization_from_dict_infers_z_order_from_z_order_flags() -> None:
    config = PlatformOptimizationConfiguration.from_dict({"z_ordering_enabled": True})

    assert config.databricks_clustering_strategy == "z_order"

    columns_config = PlatformOptimizationConfiguration.from_dict({"z_ordering_columns": ["event_time"]})

    assert columns_config.databricks_clustering_strategy == "z_order"


def test_unified_tuning_enable_z_ordering_sets_strategy() -> None:
    config = UnifiedTuningConfiguration()
    config.enable_platform_optimization(TuningType.Z_ORDERING, columns=["event_time"])

    assert config.platform_optimizations.z_ordering_enabled is True
    assert config.platform_optimizations.z_ordering_columns == ["event_time"]
    assert config.platform_optimizations.databricks_clustering_strategy == "z_order"


def test_unified_tuning_disable_z_ordering_resets_strategy_and_columns() -> None:
    config = UnifiedTuningConfiguration()
    config.enable_platform_optimization(TuningType.Z_ORDERING, columns=["event_time"])
    config.disable_platform_optimization(TuningType.Z_ORDERING)

    assert config.platform_optimizations.z_ordering_enabled is False
    assert config.platform_optimizations.z_ordering_columns == []
    assert config.platform_optimizations.databricks_clustering_strategy == "none"
    config.platform_optimizations.__post_init__()


def test_unified_tuning_from_dict_infers_z_order_from_table_clustering() -> None:
    config = UnifiedTuningConfiguration.from_dict(
        {
            "table_tunings": {
                "orders": {
                    "table_name": "orders",
                    "clustering": [{"name": "o_orderkey", "type": "INTEGER", "order": 1}],
                }
            }
        }
    )

    assert config.platform_optimizations.databricks_clustering_strategy == "z_order"


def test_unified_tuning_from_dict_keeps_none_for_sorting_only_tables() -> None:
    config = UnifiedTuningConfiguration.from_dict(
        {
            "table_tunings": {
                "orders": {
                    "table_name": "orders",
                    "sorting": [{"name": "o_orderkey", "type": "INTEGER", "order": 1}],
                }
            }
        }
    )

    assert config.platform_optimizations.databricks_clustering_strategy == "none"


def test_unified_tuning_from_dict_respects_explicit_none_with_table_clustering() -> None:
    config = UnifiedTuningConfiguration.from_dict(
        {
            "platform_optimizations": {"databricks_clustering_strategy": "none"},
            "table_tunings": {
                "orders": {
                    "table_name": "orders",
                    "clustering": [{"name": "o_orderkey", "type": "INTEGER", "order": 1}],
                }
            },
        }
    )

    assert config.platform_optimizations.databricks_clustering_strategy == "none"


