from __future__ import annotations

import inspect

import pytest

from benchbox.core.metadata_primitives.complexity import TypeComplexity
from benchbox.core.metadata_primitives.ddl import (
    ColumnDefinition,
    TableDefinition,
    ViewDefinition,
    generate_create_table_sql,
    generate_create_view_sql,
    generate_wide_table_columns,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


_DIALECT_TABLE_SNAPSHOTS = {
    "duckdb": (
        "CREATE TABLE IF NOT EXISTS sample (\n    id BIGINT NOT NULL,\n    name VARCHAR(255),\n    PRIMARY KEY (id)\n);"
    ),
    "snowflake": (
        "CREATE TABLE IF NOT EXISTS sample (\n    id BIGINT NOT NULL,\n    name VARCHAR(255),\n    PRIMARY KEY (id)\n);"
    ),
    "bigquery": (
        "CREATE TABLE IF NOT EXISTS sample (\n    id INT64 NOT NULL,\n    name STRING,\n    PRIMARY KEY (id)\n);"
    ),
    "clickhouse": (
        "CREATE TABLE IF NOT EXISTS sample (\n"
        "    id Int64 NOT NULL,\n"
        "    name String\n"
        ")\n"
        " ENGINE = MergeTree() ORDER BY (id);"
    ),
    "databricks": (
        "CREATE TABLE IF NOT EXISTS sample (\n    id BIGINT NOT NULL,\n    name STRING,\n    PRIMARY KEY (id)\n);"
    ),
    "postgres": (
        "CREATE TABLE IF NOT EXISTS sample (\n    id BIGINT NOT NULL,\n    name VARCHAR(255),\n    PRIMARY KEY (id)\n);"
    ),
    "doris": ("CREATE TABLE IF NOT EXISTS sample (\n    id BIGINT NOT NULL,\n    name VARCHAR(255)\n);"),
}


@pytest.mark.parametrize(("dialect", "expected"), _DIALECT_TABLE_SNAPSHOTS.items())
def test_create_table_rendering_snapshot_by_dialect(dialect: str, expected: str) -> None:
    table = TableDefinition(
        name="sample",
        columns=[
            ColumnDefinition("id", _id_type_for(dialect), nullable=False, primary_key=True),
            ColumnDefinition("name", _name_type_for(dialect)),
        ],
    )

    assert generate_create_table_sql(table, dialect) == expected


def test_clickhouse_no_primary_key_renders_bare_tuple_order_by() -> None:
    table = TableDefinition(
        name="sample",
        columns=[
            ColumnDefinition("id", "Int64", nullable=False),
            ColumnDefinition("name", "String"),
        ],
    )

    sql = generate_create_table_sql(table, "clickhouse")
    assert sql.endswith(" ENGINE = MergeTree() ORDER BY tuple();")
    assert "ORDER BY (tuple())" not in sql


@pytest.mark.parametrize(
    ("dialect", "expected_names"),
    [
        (
            "duckdb",
            [
                "id",
                "col_integer_0001",
                "col_varchar_0002",
                "col_varchar_0003",
                "col_varchar_0004",
                "col_array_int",
                "col_array_varchar",
                "col_struct_simple",
                "col_struct_nested",
                "col_map",
            ],
        ),
        (
            "snowflake",
            [
                "id",
                "col_integer_0001",
                "col_varchar_0002",
                "col_varchar_0003",
                "col_varchar_0004",
                "col_array_int",
                "col_array_varchar",
                "col_struct_simple",
                "col_struct_nested",
            ],
        ),
        (
            "bigquery",
            [
                "id",
                "col_integer_0001",
                "col_varchar_0002",
                "col_varchar_0003",
                "col_varchar_0004",
                "col_array_int",
                "col_array_varchar",
                "col_struct_simple",
                "col_struct_nested",
            ],
        ),
        (
            "clickhouse",
            [
                "id",
                "col_integer_0001",
                "col_varchar_0002",
                "col_varchar_0003",
                "col_varchar_0004",
                "col_array_int",
                "col_array_varchar",
                "col_struct_simple",
                "col_struct_nested",
                "col_map",
            ],
        ),
    ],
)
def test_nested_wide_table_map_column_snapshot(dialect: str, expected_names: list[str]) -> None:
    columns = generate_wide_table_columns(5, dialect, TypeComplexity.NESTED)

    assert [column.name for column in columns] == expected_names


@pytest.mark.parametrize("or_replace", [True, False])
def test_create_view_rendering_snapshot(or_replace: bool) -> None:
    view = ViewDefinition(name="sample_view", source_sql="SELECT * FROM sample")
    expected_prefix = "CREATE OR REPLACE VIEW" if or_replace else "CREATE VIEW IF NOT EXISTS"

    assert generate_create_view_sql(view, "bigquery", or_replace=or_replace) == (
        f"{expected_prefix} sample_view AS\nSELECT * FROM sample;"
    )


def test_rendering_functions_do_not_carry_literal_dialect_policy() -> None:
    rendering_functions = [
        generate_wide_table_columns,
        generate_create_table_sql,
        generate_create_view_sql,
    ]

    for func in rendering_functions:
        source = inspect.getsource(func)
        assert "dialect.lower()" not in source
        for dialect_literal in ("snowflake", "bigquery", "clickhouse", "doris"):
            assert repr(dialect_literal) not in source
            assert f'"{dialect_literal}"' not in source


def _id_type_for(dialect: str) -> str:
    return {
        "bigquery": "INT64",
        "clickhouse": "Int64",
    }.get(dialect, "BIGINT")


def _name_type_for(dialect: str) -> str:
    return {
        "bigquery": "STRING",
        "clickhouse": "String",
        "databricks": "STRING",
    }.get(dialect, "VARCHAR(255)")


