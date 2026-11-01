from __future__ import annotations

import pytest

from benchbox.core.write_primitives.benchmark import WritePrimitivesBenchmark
from benchbox.core.write_primitives.schema import get_create_table_sql

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture()
def wp(tmp_path):
    return WritePrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)


@pytest.mark.parametrize(
    "operation_id",
    [
        "merge_simple_upsert_small",
        "bulk_load_csv_small_uncompressed",
        "bulk_load_upsert_mode",
        "insert_on_conflict_ignore",
        "update_returning",
        "update_with_join",
        "delete_with_not_exists",
        "ddl_drop_index",
    ],
)
def test_unsupported_operations_are_skipped_with_the_engine_error(wp, operation_id):
    sql, reason = wp._get_effective_write_sql(wp.get_operation(operation_id), platform_key="clickhouse")
    assert sql is None
    assert reason.startswith(f"Operation '{operation_id}' is skipped on ClickHouse: ClickHouse 25.8.33.6")
    assert "Code" in reason


def test_supported_operations_are_not_skipped(wp):
    for operation_id in (
        "insert_single_row",
        "update_single_row_pk",
        "delete_single_row_pk",
        "ddl_alter_table_add_column",
    ):
        sql, reason = wp._get_effective_write_sql(wp.get_operation(operation_id), platform_key="clickhouse")
        assert reason is None
        assert sql


def test_staging_ddl_declares_a_merge_tree_and_nullable_columns():
    sql = get_create_table_sql("batch_metadata", "clickhouse", if_not_exists=True)
    assert sql.rstrip(";").endswith("ENGINE = MergeTree ORDER BY (batch_id)")
    assert "file_path Nullable(VARCHAR(500))" in sql
    assert "operation_id VARCHAR(100) NOT NULL" in sql
    assert "ENGINE" not in get_create_table_sql("batch_metadata", "duckdb")
