# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.core.config_inheritance import (
    build_inherited_config,
    get_benchmark_compatibility,
    get_inherited_dialect,
    get_platform_family_dialect,
    resolve_dialect_for_query_translation,
)
from benchbox.utils.dialect_utils import UnknownSqlDialectError

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestGetInheritedDialect:
    def test_platform_without_inheritance_returns_self(self):
        dialect = get_inherited_dialect("duckdb")
        assert dialect == "duckdb"

    def test_clickhouse_returns_clickhouse(self):
        dialect = get_inherited_dialect("clickhouse")
        assert dialect == "clickhouse"

    def test_trino_returns_trino(self):
        dialect = get_inherited_dialect("trino")
        assert dialect == "trino"

    def test_case_insensitive(self):
        assert get_inherited_dialect("DuckDB") == "duckdb"
        assert get_inherited_dialect("CLICKHOUSE") == "clickhouse"


class TestGetPlatformFamilyDialect:
    def test_clickhouse_family(self):
        dialect = get_platform_family_dialect("clickhouse")
        assert dialect == "clickhouse"

    def test_duckdb_family(self):
        dialect = get_platform_family_dialect("duckdb")
        assert dialect == "duckdb"

    def test_firebolt_family(self):
        dialect = get_platform_family_dialect("firebolt")
        assert dialect == "firebolt"


class TestBuildInheritedConfig:
    def test_no_user_config_returns_empty_base(self):
        config = build_inherited_config("duckdb")
        assert isinstance(config, dict)

    def test_user_config_preserved(self):
        user_config = {"timeout": 30, "memory_limit": "1GB"}
        config = build_inherited_config("duckdb", user_config)

        assert config["timeout"] == 30
        assert config["memory_limit"] == "1GB"

    def test_platform_family_added(self):
        config = build_inherited_config("clickhouse")
        assert "_platform_family" in config
        assert config["_platform_family"] == "clickhouse"


class TestResolveDialectForQueryTranslation:
    def test_duckdb_returns_duckdb(self):
        dialect = resolve_dialect_for_query_translation("duckdb")
        assert dialect == "duckdb"

    def test_clickhouse_returns_clickhouse(self):
        dialect = resolve_dialect_for_query_translation("clickhouse")
        assert dialect == "clickhouse"

    def test_trino_returns_trino(self):
        dialect = resolve_dialect_for_query_translation("trino")
        assert dialect == "trino"

    def test_firebolt_resolves_to_its_postgres_dialect(self):
        dialect = resolve_dialect_for_query_translation("firebolt")
        assert dialect == "postgres"

    def test_unknown_platform_raises(self):
        with pytest.raises(UnknownSqlDialectError):
            resolve_dialect_for_query_translation("polars")

    def test_case_insensitive(self):
        assert resolve_dialect_for_query_translation("DuckDB") == "duckdb"


class TestGetBenchmarkCompatibility:
    def test_returns_dict(self):
        compat = get_benchmark_compatibility("duckdb")
        assert isinstance(compat, dict)

    def test_handles_unknown_platform(self):
        compat = get_benchmark_compatibility("clickhouse")
        assert isinstance(compat, dict)
