# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import pytest

from benchbox.core.tuning.interface import (
    TableTuning,
    TuningColumn,
    TuningType,
    UnifiedTuningConfiguration,
)
from benchbox.core.tuning.metadata import TuningMetadataManager
from benchbox.platforms.base import PlatformAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


DISPLAY_NAMES = ["StarRocks", "ClickHouse (Local)", "Doris", "Apache Doris"]


class _StubAdapter(PlatformAdapter):
    def __init__(self, display_name: str = "StubPlatform", **config: Any):
        super().__init__(**config)
        self._display_name = display_name

    @staticmethod
    def add_cli_arguments(parser) -> None:  # pragma: no cover
        return None

    @classmethod
    def from_config(cls, config: dict[str, Any]):
        return cls(**config)

    @property
    def platform_name(self) -> str:
        return self._display_name

    def get_target_dialect(self) -> str | None:
        return None

    def create_connection(self, **connection_config):
        return Mock()

    def create_schema(self, benchmark, connection):
        return 0.0

    def load_data(self, benchmark, connection, data_dir):
        return {}, 0.0, None

    def configure_for_benchmark(self, connection, benchmark_type):
        return None

    def execute_query(self, connection, query, query_id, **kwargs):
        return {"query_id": query_id, "status": "SUCCESS"}

    def apply_platform_optimizations(self, platform_config, connection):
        return None

    def apply_constraint_configuration(self, primary_key_config, foreign_key_config, connection):
        return None


class TestDisplayNameValidationNoLongerErrors:
    @pytest.mark.parametrize("display_name", DISPLAY_NAMES)
    def test_default_config_produces_no_errors_for_display_names(self, display_name):
        config = UnifiedTuningConfiguration()
        assert config.validate_for_platform(display_name) == []

    @pytest.mark.parametrize("display_name", DISPLAY_NAMES)
    def test_display_name_mismatches_are_warnings_not_errors(self, display_name):
        config = UnifiedTuningConfiguration()
        errors, warnings = config.validate_for_platform_detailed(display_name)
        assert errors == []
        assert len(warnings) == 4
        assert all("is not supported by platform" in w for w in warnings)

    def test_table_layout_tuning_on_unknown_platform_warns_not_errors(self):
        config = UnifiedTuningConfiguration()
        config.disable_all_constraints()
        config.table_tunings["orders"] = TableTuning(
            table_name="orders",
            distribution=[TuningColumn(name="o_orderkey", type="INTEGER", order=1)],
        )

        errors, warnings = config.validate_for_platform_detailed("starrocks")
        assert errors == []
        assert any("'distribution'" in w for w in warnings)


class TestKnownPlatformSemanticsPreserved:
    def test_is_known_platform(self):
        assert TuningType.is_known_platform("duckdb") is True
        assert TuningType.is_known_platform("DuckDB") is True
        assert TuningType.is_known_platform("databricks") is True
        assert TuningType.is_known_platform("starrocks") is False
        assert TuningType.is_known_platform("doris") is False
        assert TuningType.is_known_platform("clickhouse-local") is False

    def test_known_platform_set_stays_in_sync_with_compatibility_map(self):
        from benchbox.core.tuning.interface import (
            _KNOWN_COMPATIBILITY_PLATFORMS,
            _PLATFORM_COMPATIBILITY_MAP,
        )

        assert frozenset(_PLATFORM_COMPATIBILITY_MAP) == _KNOWN_COMPATIBILITY_PLATFORMS
        assert (
            frozenset(
                {
                    "duckdb",
                    "snowflake",
                    "bigquery",
                    "redshift",
                    "clickhouse",
                    "databricks",
                    "sqlite",
                    "postgresql",
                    "mysql",
                }
            )
            == _KNOWN_COMPATIBILITY_PLATFORMS
        )
        for platform, tuning_types in _PLATFORM_COMPATIBILITY_MAP.items():
            for tuning_type in tuning_types:
                assert tuning_type.is_compatible_with_platform(platform) is True

    def test_duckdb_default_config_still_validates_clean(self):
        config = UnifiedTuningConfiguration()
        errors, warnings = config.validate_for_platform_detailed("duckdb")
        assert errors == []
        assert warnings == []

    def test_layout_incompatibility_on_known_platform_still_errors(self):
        config = UnifiedTuningConfiguration()
        config.disable_all_constraints()
        config.table_tunings["orders"] = TableTuning(
            table_name="orders",
            distribution=[TuningColumn(name="o_orderkey", type="INTEGER", order=1)],
        )

        errors = config.validate_for_platform("duckdb")
        assert any("'distribution'" in e for e in errors)

    def test_platform_specific_opt_on_known_platform_still_errors(self):
        config = UnifiedTuningConfiguration()
        config.disable_all_constraints()
        config.enable_platform_optimization(TuningType.Z_ORDERING, columns=["c1"])

        errors = config.validate_for_platform("duckdb")
        assert any("'z_ordering'" in e for e in errors)
        assert config.validate_for_platform("databricks") == []

    def test_constraint_mismatch_on_known_platform_is_warning(self):
        config = UnifiedTuningConfiguration()
        errors, warnings = config.validate_for_platform_detailed("clickhouse")
        assert errors == []
        assert any("'foreign_keys'" in w for w in warnings)
        assert any("'check_constraints'" in w for w in warnings)

    def test_databricks_liquid_layout_validation_still_errors(self):
        config = UnifiedTuningConfiguration()
        config.platform_optimizations.databricks_clustering_strategy = "liquid_clustering_auto"
        config.platform_optimizations.liquid_clustering_enabled = True
        config.table_tunings["orders"] = TableTuning(
            table_name="orders",
            partitioning=[TuningColumn(name="o_orderdate", type="DATE", order=1)],
        )

        errors = config.validate_for_platform("databricks")
        assert any("incompatible with per-table partitioning" in e for e in errors)


class TestCanonicalPlatformType:
    def test_prefers_config_type_key(self):
        adapter = _StubAdapter(display_name="ClickHouse (Local)", type="clickhouse-local")
        assert adapter.canonical_platform_type == "clickhouse-local"

    def test_config_type_is_normalized_to_lowercase(self):
        adapter = _StubAdapter(display_name="Doris", type="Doris")
        assert adapter.canonical_platform_type == "doris"

    def test_falls_back_to_normalized_platform_name(self):
        adapter = _StubAdapter(display_name="ClickHouse Local")
        assert adapter.canonical_platform_type == "clickhouse-local"

    def test_fallback_lowercases_single_word_display_names(self):
        adapter = _StubAdapter(display_name="StarRocks")
        assert adapter.canonical_platform_type == "starrocks"

    def test_fallback_on_parenthesized_display_name_is_not_a_canonical_key(self):
        adapter = _StubAdapter(display_name="ClickHouse (Local)")
        assert adapter.canonical_platform_type == "clickhouse-(local)"
        assert TuningType.is_known_platform(adapter.canonical_platform_type) is False

    def test_injected_config_type_overrides_parenthesized_display_name(self):
        adapter = _StubAdapter(display_name="ClickHouse (Local)", type="clickhouse")
        assert adapter.canonical_platform_type == "clickhouse"
        assert TuningType.is_known_platform(adapter.canonical_platform_type) is True

    def test_registry_constructed_adapter_reports_canonical_type(self, tmp_path):
        from benchbox.platforms import get_platform_adapter

        adapter = get_platform_adapter("duckdb", database_path=str(tmp_path / "t.duckdb"))
        assert adapter.canonical_platform_type == "duckdb"
        assert adapter.platform_config["type"] == "duckdb"

    def test_registry_constructed_adapter_resolves_aliases(self, tmp_path):
        from benchbox.platforms import get_platform_adapter

        adapter = get_platform_adapter("sqlite3", database_path=str(tmp_path / "t.db"))
        assert adapter.canonical_platform_type == "sqlite"

    def test_validate_tuning_configuration_uses_canonical_key(self):
        adapter = _StubAdapter(display_name="StarRocks", type="starrocks")
        adapter.unified_tuning_configuration = UnifiedTuningConfiguration()
        assert adapter.validate_tuning_configuration_for_platform() == []
        assert adapter.validate_tuning_configuration(UnifiedTuningConfiguration()) == []


class _MetadataStubAdapter:
    def __init__(self, platform_name: str, canonical_platform_type: str | None = None):
        self.platform_name = platform_name
        if canonical_platform_type is not None:
            self.canonical_platform_type = canonical_platform_type
        self.platform_config = {}

    def create_connection(self, **_kwargs):  # pragma: no cover
        return Mock()

    def close_connection(self, _conn):  # pragma: no cover
        return None


class TestMetadataPersistsCanonicalKey:
    def _saved_platforms(self, manager: TuningMetadataManager) -> set[str]:
        from benchbox.core.tuning.interface import BenchmarkTunings

        tunings = BenchmarkTunings("tpch")
        tunings.add_table_tuning(
            TableTuning(
                table_name="orders",
                sorting=[TuningColumn(name="o_orderkey", type="INTEGER", order=1)],
            )
        )

        captured: dict[str, Any] = {"records": None}
        manager.create_metadata_table = lambda: True
        manager.clear_tunings = lambda _bn=None: True
        manager._batch_insert_records = lambda records: captured.__setitem__("records", records)

        assert manager.save_tunings(tunings) is True
        assert captured["records"]
        return {record.platform for record in captured["records"]}

    def test_save_tunings_persists_canonical_platform_type(self):
        adapter = _MetadataStubAdapter("ClickHouse (Local)", canonical_platform_type="clickhouse-local")
        assert self._saved_platforms(TuningMetadataManager(adapter)) == {"clickhouse_local"}

    def test_save_tunings_falls_back_to_normalized_display_name(self):
        adapter = _MetadataStubAdapter("StarRocks")
        assert self._saved_platforms(TuningMetadataManager(adapter)) == {"starrocks"}

    def test_create_table_sql_dispatches_on_canonical_key(self):
        adapter = _MetadataStubAdapter("ClickHouse (Local)", canonical_platform_type="clickhouse-local")
        manager = TuningMetadataManager(adapter)
        assert "ENGINE = MergeTree()" in manager._get_create_table_sql()
        assert manager._get_create_index_sql() is None
