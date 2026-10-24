import importlib

import pytest

from benchbox.core.platform_manifest import PLATFORM_MANIFEST, get_platform_query_dialect
from benchbox.utils.dialect_utils import GENERIC_DIALECT_ALIASES, UnknownSqlDialectError, resolve_sql_dialect

pytestmark = [pytest.mark.unit, pytest.mark.fast]

INTENDED_DIALECTS: dict[str, str] = {
    "duckdb": "duckdb",
    "datafusion": "datafusion",
    "sqlite": "sqlite",
    "motherduck": "duckdb",
    "ducklake": "duckdb",
    "gizmosql": "duckdb",
    "clickhouse": "clickhouse",
    "clickhouse-local": "clickhouse",
    "clickhouse-server": "clickhouse",
    "clickhouse-cloud": "clickhouse",
    "bigquery": "bigquery",
    "databricks": "databricks",
    "databricks-df": "databricks",
    "snowflake": "snowflake",
    "redshift": "redshift",
    "trino": "trino",
    "starburst": "trino",
    "presto": "presto",
    "postgresql": "postgres",
    "timescaledb": "postgres",
    "pg-mooncake": "postgres",
    "cedardb": "postgres",
    "pg-duckdb": "postgres",
    "paradedb": "postgres",
    "citus": "postgres",
    "synapse": "tsql",
    "fabric_dw": "tsql",
    "firebolt": "postgres",
    "starrocks": "starrocks",
    "databend": "snowflake",
    "doris": "doris",
    "singlestore": "singlestore",
    "influxdb": "datafusion",
    "questdb": "questdb",
    "athena": "athena",
    "glue": "spark",
    "emr-serverless": "spark",
    "athena-spark": "spark",
    "dataproc": "spark",
    "dataproc-serverless": "spark",
    "fabric-spark": "spark",
    "fabric-lakehouse": "tsql",
    "synapse-spark": "spark",
    "spark": "spark",
    "velox": "spark",
    "lakesail": "spark",
    "snowpark-connect": "snowflake",
    "quanton": "spark",
    "pyspark": "spark",
}

INTENDED_QUERY_DIALECTS: dict[str, str] = {"singlestore": "mysql", "athena": "trino"}

SQL_ENTRIES = [entry for entry in PLATFORM_MANIFEST if entry.capabilities["supports_sql"]]
DATAFRAME_ONLY_ENTRIES = [entry for entry in PLATFORM_MANIFEST if not entry.capabilities["supports_sql"]]


def test_intended_table_covers_exactly_the_sql_platforms_of_the_manifest():
    assert {entry.key for entry in SQL_ENTRIES} == set(INTENDED_DIALECTS)


@pytest.mark.parametrize("key", sorted(INTENDED_DIALECTS))
def test_platform_resolves_to_its_intended_dialect(key: str):
    assert resolve_sql_dialect(key) == INTENDED_DIALECTS[key]
    assert resolve_sql_dialect(key.replace("-", "_")) == INTENDED_DIALECTS[key]
    assert resolve_sql_dialect(key.upper()) == INTENDED_DIALECTS[key]


@pytest.mark.parametrize("entry", DATAFRAME_ONLY_ENTRIES, ids=lambda entry: entry.key)
def test_dataframe_only_platform_has_no_sql_dialect(entry):
    assert "sql_dialect" not in entry.capabilities
    with pytest.raises(UnknownSqlDialectError):
        resolve_sql_dialect(entry.key)


@pytest.mark.parametrize("alias", sorted(GENERIC_DIALECT_ALIASES))
def test_generic_aliases_resolve_to_postgres(alias: str):
    assert resolve_sql_dialect(alias) == "postgres"


@pytest.mark.parametrize("entry", [e for e in SQL_ENTRIES if e.adapter is not None], ids=lambda entry: entry.key)
def test_adapter_target_dialect_matches_the_registry(entry):
    try:
        adapter_class = getattr(importlib.import_module(entry.adapter.module), entry.adapter.class_name)
    except ModuleNotFoundError as error:
        if error.name and error.name.startswith("benchbox"):
            raise
        pytest.skip(f"optional dependency {error.name} is not installed")
    adapter = object.__new__(adapter_class)
    expected = INTENDED_QUERY_DIALECTS.get(entry.key, INTENDED_DIALECTS[entry.key])
    assert resolve_sql_dialect(adapter.get_target_dialect()) == resolve_sql_dialect(expected)
    assert get_platform_query_dialect(entry.key) == expected


def test_manifest_rejects_sql_platform_without_dialect():
    from benchbox.core.platform_manifest import _validate_capabilities

    base = {"supports_sql": True, "supports_dataframe": False, "default_mode": "sql"}
    with pytest.raises(ValueError, match="sql_dialect"):
        _validate_capabilities("x", base)
    with pytest.raises(ValueError, match="sql_dialect"):
        _validate_capabilities("x", {**base, "sql_dialect": "Not Lower"})
    _validate_capabilities("x", {**base, "sql_dialect": "duckdb"})
    with pytest.raises(ValueError, match="must not declare sql_dialect"):
        _validate_capabilities(
            "x",
            {"supports_sql": False, "supports_dataframe": True, "default_mode": "dataframe", "sql_dialect": "duckdb"},
        )


def test_query_dialect_overrides_are_exactly_the_intended_table():
    declared = {
        entry.key: entry.capabilities["query_dialect"]
        for entry in PLATFORM_MANIFEST
        if "query_dialect" in entry.capabilities
    }
    assert declared == INTENDED_QUERY_DIALECTS


def test_manifest_rejects_a_query_dialect_equal_to_the_sql_dialect_or_on_a_dataframe_platform():
    from benchbox.core.platform_manifest import _validate_capabilities

    base = {"supports_sql": True, "supports_dataframe": False, "default_mode": "sql", "sql_dialect": "duckdb"}
    _validate_capabilities("x", {**base, "query_dialect": "postgres"})
    with pytest.raises(ValueError, match="must differ"):
        _validate_capabilities("x", {**base, "query_dialect": "duckdb"})
    with pytest.raises(ValueError, match="query_dialect"):
        _validate_capabilities(
            "x",
            {"supports_sql": False, "supports_dataframe": True, "default_mode": "dataframe", "query_dialect": "duckdb"},
        )


def test_query_dialect_lookup_rejects_unknown_and_dataframe_platforms():
    with pytest.raises(KeyError):
        get_platform_query_dialect("polars")
    with pytest.raises(KeyError):
        get_platform_query_dialect("not_a_platform")
