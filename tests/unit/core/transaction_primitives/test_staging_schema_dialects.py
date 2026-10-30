# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

from benchbox.core.transaction_primitives.schema import (
    STAGING_TABLES,
    _requires_catalog_managed_staging,
    get_create_table_sql,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.mark.parametrize("table_name", sorted(STAGING_TABLES))
def test_databricks_staging_tables_enable_catalog_managed(table_name: str):

    sql = get_create_table_sql(table_name, dialect="databricks", if_not_exists=True)

    assert sql.startswith(f"CREATE TABLE IF NOT EXISTS {table_name} (")
    assert sql.rstrip().endswith(") USING DELTA TBLPROPERTIES ('delta.feature.catalogManaged' = 'supported');")


@pytest.mark.parametrize("dialect", ["standard", "duckdb", "snowflake", "bigquery", "postgres"])
def test_other_dialects_keep_plain_create_table(dialect: str):
    sql = get_create_table_sql("txn_orders", dialect=dialect)

    assert "TBLPROPERTIES" not in sql
    assert "USING DELTA" not in sql
    assert sql.rstrip().endswith(");")


def test_catalog_managed_gate_follows_registry_rule():

    assert _requires_catalog_managed_staging("databricks") is True
    assert _requires_catalog_managed_staging("DATABRICKS") is True
    for dialect in ("standard", "duckdb", "snowflake", "bigquery", "postgres"):
        assert _requires_catalog_managed_staging(dialect) is False


def test_databricks_skips_savepoint_and_isolation_operations(tmp_path):

    from unittest.mock import MagicMock

    from benchbox.core.transaction_primitives.benchmark import TransactionPrimitivesBenchmark

    bench = TransactionPrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
    bench._prepare_operation = MagicMock(return_value=(MagicMock(), "databricks", None, None))

    result = bench.execute_operation("transaction_savepoint_nested", MagicMock(), platform_name="databricks")

    assert result.status == "SKIPPED"
    assert "SAVEPOINT" in (result.skip_reason or "")


@pytest.mark.parametrize(("dialect", "expects_sql_rollback"), [("databricks", True), ("duckdb", False)])
def test_rollback_after_error_issues_sql_rollback_only_where_needed(tmp_path, dialect, expects_sql_rollback):

    from unittest.mock import MagicMock

    from benchbox.core.transaction_primitives.benchmark import TransactionPrimitivesBenchmark

    bench = TransactionPrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
    bench._setup_dialect = dialect
    connection = MagicMock()

    bench._rollback_connection_after_error(connection)

    connection.rollback.assert_called_once()
    if expects_sql_rollback:
        connection.execute.assert_called_once_with("ROLLBACK")
    else:
        connection.execute.assert_not_called()


@pytest.mark.parametrize("table_name", sorted(STAGING_TABLES))
def test_questdb_staging_tables_use_parameterless_types(table_name: str):
    sql = get_create_table_sql(table_name, dialect="questdb", if_not_exists=True)

    assert "VARCHAR(" not in sql
    assert "DECIMAL" not in sql
    assert "PRIMARY KEY" not in sql
    assert "VARCHAR" in sql


def test_other_dialects_keep_parameterized_types():
    sql = get_create_table_sql("txn_orders", dialect="postgres")

    assert "VARCHAR(" in sql
    assert "DECIMAL(15,2)" in sql
