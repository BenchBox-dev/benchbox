from __future__ import annotations

import importlib

import pytest

from benchbox.core.platform_manifest import get_adapter_imports

pytestmark = [pytest.mark.unit, pytest.mark.medium]


ADAPTER_DIALECTS: dict[str, str] = {
    "duckdb": "duckdb",
    "motherduck": "duckdb",
    "ducklake": "duckdb",
    "datafusion": "datafusion",
    "databricks": "databricks",
    "databricks-df": "databricks",
    "clickhouse": "clickhouse",
    "clickhouse-local": "clickhouse",
    "clickhouse-server": "clickhouse",
    "clickhouse-cloud": "clickhouse",
    "starrocks": "starrocks",
    "sqlite": "sqlite",
    "bigquery": "bigquery",
    "redshift": "redshift",
    "snowflake": "snowflake",
    "trino": "trino",
    "starburst": "trino",
    "presto": "presto",
    "postgresql": "postgres",
    "timescaledb": "postgres",
    "pg-duckdb": "postgres",
    "pg-mooncake": "postgres",
    "questdb": "questdb",
    "cedardb": "postgres",
    "synapse": "tsql",
    "pyspark": "spark",
    "firebolt": "postgres",
    "databend": "snowflake",
    "doris": "doris",
    "singlestore": "singlestore",
    "influxdb": "influxdb",
    "fabric_dw": "tsql",
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
    "lakesail": "spark",
    "velox": "spark",
    "polars": "dataframe",
    "snowpark-connect": "snowflake",
    "quanton": "spark",
    "paradedb": "postgres",
    "citus": "postgres",
}


NON_SQL_DIALECTS = {"dataframe": "expression/schema API"}

DDL_INPUT = "CREATE TABLE t (id INTEGER NOT NULL, name VARCHAR(20), amt DECIMAL(10,2), d DATE, PRIMARY KEY (id))"
QUERY_INPUT = "SELECT name, SUM(amt) AS s FROM t WHERE d >= DATE '2020-01-01' GROUP BY name ORDER BY s DESC LIMIT 5"

EXPECTED_SQL: dict[str, tuple[str, str]] = {
    "duckdb": (
        'CREATE TABLE "t" ("id" INT NOT NULL, "name" TEXT(20), "amt" DECIMAL(10, 2), "d" DATE, PRIMARY KEY ("id"));',
        'SELECT "name", SUM("amt") AS "s" FROM "t" WHERE "d" >= CAST(\'2020-01-01\' AS DATE) '
        'GROUP BY "name" ORDER BY "s" DESC NULLS FIRST LIMIT 5;',
    ),
    "datafusion": (
        "CREATE TABLE t (id INT NOT NULL, name VARCHAR(20), amt DECIMAL(10, 2), d DATE, PRIMARY KEY (id));",
        "SELECT name, SUM(amt) AS s FROM t WHERE d >= CAST('2020-01-01' AS DATE) GROUP BY name ORDER BY s DESC LIMIT 5;",
    ),
    "databricks": (
        "CREATE TABLE `t` (`id` INT NOT NULL, `name` VARCHAR(20), `amt` DECIMAL(10, 2), `d` DATE, PRIMARY KEY (`id`));",
        "SELECT `name`, SUM(`amt`) AS `s` FROM `t` WHERE `d` >= CAST('2020-01-01' AS DATE) "
        "GROUP BY `name` ORDER BY `s` DESC NULLS FIRST LIMIT 5;",
    ),
    "clickhouse": (
        "CREATE TABLE t (id Nullable(Int32) NOT NULL, name Nullable(String), amt Nullable(Decimal(10, 2)), "
        "d Nullable(DATE), PRIMARY KEY (id));",
        "SELECT name, SUM(amt) AS s FROM t WHERE d >= CAST('2020-01-01' AS Nullable(DATE)) "
        "GROUP BY name ORDER BY s DESC NULLS FIRST LIMIT 5;",
    ),
    "starrocks": (
        "CREATE TABLE `t` (`id` INT NOT NULL, `name` VARCHAR(20), `amt` DECIMAL(10, 2), `d` DATE);",
        "SELECT `name`, SUM(`amt`) AS `s` FROM `t` WHERE `d` >= CAST('2020-01-01' AS DATE) GROUP BY `name` "
        "ORDER BY CASE WHEN SUM(`amt`) IS NULL THEN 1 ELSE 0 END DESC, SUM(`amt`) DESC LIMIT 5;",
    ),
    "sqlite": (
        'CREATE TABLE "t" ("id" INTEGER NOT NULL PRIMARY KEY, "name" TEXT(20), "amt" REAL(10, 2), "d" DATE);',
        'SELECT "name", SUM("amt") AS "s" FROM "t" WHERE "d" >= DATE(\'2020-01-01\') '
        'GROUP BY "name" ORDER BY "s" DESC NULLS FIRST LIMIT 5;',
    ),
    "bigquery": (
        "CREATE TABLE `t` (`id` INT64 NOT NULL, `name` STRING(20), `amt` NUMERIC(10, 2), `d` DATE, "
        "PRIMARY KEY (`id`));",
        "SELECT `name`, SUM(`amt`) AS `s` FROM `t` WHERE `d` >= CAST('2020-01-01' AS DATE) "
        "GROUP BY `name` ORDER BY `s` DESC NULLS FIRST LIMIT 5;",
    ),
    "redshift": (
        'CREATE TABLE "t" ("id" INTEGER NOT NULL, "name" VARCHAR(20), "amt" DECIMAL(10, 2), "d" DATE, '
        'PRIMARY KEY ("id"));',
        'SELECT "name", SUM("amt") AS "s" FROM "t" WHERE "d" >= CAST(\'2020-01-01\' AS DATE) '
        'GROUP BY "name" ORDER BY "s" DESC LIMIT 5;',
    ),
    "snowflake": (
        "CREATE TABLE t (id INT NOT NULL, name VARCHAR(20), amt DECIMAL(10, 2), d DATE, PRIMARY KEY (id));",
        "SELECT name, SUM(amt) AS s FROM t WHERE d >= CAST('2020-01-01' AS DATE) GROUP BY name ORDER BY s DESC LIMIT 5;",
    ),
    "trino": (
        'CREATE TABLE "t" ("id" INTEGER NOT NULL, "name" VARCHAR(20), "amt" DECIMAL(10, 2), "d" DATE, '
        'PRIMARY KEY ("id"));',
        'SELECT "name", SUM("amt") AS "s" FROM "t" WHERE "d" >= CAST(\'2020-01-01\' AS DATE) '
        'GROUP BY "name" ORDER BY "s" DESC NULLS FIRST LIMIT 5;',
    ),
    "presto": (
        'CREATE TABLE "t" ("id" INTEGER NOT NULL, "name" VARCHAR(20), "amt" DECIMAL(10, 2), "d" DATE, '
        'PRIMARY KEY ("id"));',
        'SELECT "name", SUM("amt") AS "s" FROM "t" WHERE "d" >= CAST(\'2020-01-01\' AS DATE) '
        'GROUP BY "name" ORDER BY "s" DESC NULLS FIRST LIMIT 5;',
    ),
    "postgres": (
        "CREATE TABLE t (id INT NOT NULL, name VARCHAR(20), amt DECIMAL(10, 2), d DATE, PRIMARY KEY (id));",
        "SELECT name, SUM(amt) AS s FROM t WHERE d >= CAST('2020-01-01' AS DATE) GROUP BY name ORDER BY s DESC LIMIT 5;",
    ),
    "tsql": (
        "CREATE TABLE [t] ([id] INTEGER NOT NULL, [name] VARCHAR(20), [amt] NUMERIC(10, 2), [d] DATE, "
        "PRIMARY KEY ([id]));",
        "SELECT TOP 5 [name], SUM([amt]) AS [s] FROM [t] WHERE [d] >= CAST('2020-01-01' AS DATE) "
        "GROUP BY [name] ORDER BY CASE WHEN SUM([amt]) IS NULL THEN 1 ELSE 0 END DESC, SUM([amt]) DESC;",
    ),
    "spark": (
        "CREATE TABLE `t` (`id` INT NOT NULL, `name` VARCHAR(20), `amt` DECIMAL(10, 2), `d` DATE, PRIMARY KEY (`id`));",
        "SELECT `name`, SUM(`amt`) AS `s` FROM `t` WHERE `d` >= CAST('2020-01-01' AS DATE) "
        "GROUP BY `name` ORDER BY `s` DESC NULLS FIRST LIMIT 5;",
    ),
    "doris": (
        "CREATE TABLE `t` (`id` INT NOT NULL, `name` VARCHAR(20), `amt` DECIMAL(10, 2), `d` DATE, PRIMARY KEY (`id`));",
        "SELECT `name`, SUM(`amt`) AS `s` FROM `t` WHERE `d` >= CAST('2020-01-01' AS DATE) GROUP BY `name` "
        "ORDER BY CASE WHEN SUM(`amt`) IS NULL THEN 1 ELSE 0 END DESC, SUM(`amt`) DESC LIMIT 5;",
    ),
    "athena": (
        "CREATE TABLE `t` (`id` INT NOT NULL, `name` VARCHAR(20), `amt` DECIMAL(10, 2), `d` DATE, PRIMARY KEY (`id`));",
        'SELECT "name", SUM("amt") AS "s" FROM "t" WHERE "d" >= CAST(\'2020-01-01\' AS DATE) '
        'GROUP BY "name" ORDER BY "s" DESC NULLS FIRST LIMIT 5;',
    ),
    "singlestore": (
        "CREATE TABLE `t` (`id` INT NOT NULL, `name` VARCHAR(20), `amt` DECIMAL(10, 2), `d` DATE, PRIMARY KEY (`id`));",
        "SELECT `name`, SUM(`amt`) AS `s` FROM `t` WHERE `d` >= '2020-01-01' :> DATE GROUP BY `name` "
        "ORDER BY `s` DESC NULLS FIRST LIMIT 5;",
    ),
    "questdb": (
        "CREATE TABLE t (id INT NOT NULL, name VARCHAR(20), amt DECIMAL(10, 2), d DATE, PRIMARY KEY (id));",
        "SELECT name, SUM(amt) AS s FROM t WHERE d >= CAST('2020-01-01' AS DATE) GROUP BY name ORDER BY s DESC LIMIT 5;",
    ),
    "influxdb": (
        "CREATE TABLE t (id INT NOT NULL, name VARCHAR(20), amt DECIMAL(10, 2), d DATE, PRIMARY KEY (id));",
        "SELECT name, SUM(amt) AS s FROM t WHERE d >= CAST('2020-01-01' AS DATE) GROUP BY name ORDER BY s DESC LIMIT 5;",
    ),
}

ADAPTER_COORDINATES = {key: (module, class_name) for key, module, class_name in get_adapter_imports()}


def _bare_adapter(key: str):

    module, class_name = ADAPTER_COORDINATES[key]
    adapter = object.__new__(getattr(importlib.import_module(module), class_name))
    adapter._dialect = adapter.get_target_dialect()
    return adapter


def test_inventory_conserves_all_concrete_adapters() -> None:
    assert len(ADAPTER_COORDINATES) == 49
    assert set(ADAPTER_DIALECTS) == set(ADAPTER_COORDINATES)


def test_every_dialect_is_covered_by_translation_or_exemption() -> None:
    dialects = set(ADAPTER_DIALECTS.values())
    assert dialects == set(EXPECTED_SQL) | set(NON_SQL_DIALECTS)
    assert not set(EXPECTED_SQL) & set(NON_SQL_DIALECTS)


@pytest.mark.parametrize("key", sorted(ADAPTER_DIALECTS))
def test_adapter_class_loads_and_reports_expected_dialect(key: str) -> None:
    assert _bare_adapter(key).dialect == ADAPTER_DIALECTS[key]


@pytest.mark.parametrize("key", sorted(k for k, d in ADAPTER_DIALECTS.items() if d in EXPECTED_SQL))
@pytest.mark.parametrize("statement", ["ddl", "query"])
def test_real_translation_of_ddl_and_query(key: str, statement: str) -> None:
    adapter = _bare_adapter(key)
    index = 0 if statement == "ddl" else 1
    sql = DDL_INPUT if statement == "ddl" else QUERY_INPUT
    assert adapter.translate_sql(sql, strict=True) == EXPECTED_SQL[ADAPTER_DIALECTS[key]][index]


@pytest.mark.parametrize("key", sorted(k for k, d in ADAPTER_DIALECTS.items() if d in NON_SQL_DIALECTS))
def test_non_sql_platforms_reject_strict_sql_translation(key: str) -> None:
    from benchbox.utils.dialect_utils import SQLTranslationError

    adapter = _bare_adapter(key)
    with pytest.raises(SQLTranslationError, match="No fork SQL dialect"):
        adapter.translate_sql(DDL_INPUT, strict=True)


def test_polars_structured_schema_without_sql_or_sdk() -> None:

    adapter = _bare_adapter("polars")
    adapter.quiet = True

    class Benchmark:
        def get_schema(self):
            return {"T": {"columns": [{"name": "id", "type": "INTEGER"}, {"name": "name", "type": "VARCHAR"}]}}

    assert adapter._get_benchmark_schema(Benchmark()) == {
        "t": {"columns": [{"name": "id", "type": "INTEGER"}, {"name": "name", "type": "VARCHAR"}]}
    }


def test_influx_measurement_schema_and_typed_line_protocol() -> None:

    import logging

    from benchbox.platforms.influxdb import to_line_protocol

    adapter = _bare_adapter("influxdb")
    adapter.logger = logging.getLogger(__name__)
    assert adapter.create_schema(None, None) == 0.0
    assert (
        to_line_protocol(
            "cpu", {"host": "local host"}, {"count": 3, "load": 1.5, "ok": True, "label": 'a"b', "missing": None}, 42
        )
        == 'cpu,host=local\\ host count=3i,load=1.5,ok=true,label="a\\"b" 42'
    )


def test_snowflake_fixture_stub_does_not_hide_real_cli_options() -> None:

    import argparse

    from benchbox.platforms.snowflake import SnowflakeAdapter

    stub_parser = argparse.ArgumentParser()
    SnowflakeAdapter.add_cli_arguments(stub_parser)
    assert not stub_parser._actions[1:]

    real_parser = argparse.ArgumentParser()
    SnowflakeAdapter.add_cli_arguments.real(real_parser)
    options = {option for action in real_parser._actions for option in action.option_strings}
    assert {"--account", "--warehouse", "--schema", "--private-key-path", "--modify-warehouse-settings"} <= options
