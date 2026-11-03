from __future__ import annotations

import pytest

from benchbox.core.write_primitives.benchmark import WritePrimitivesBenchmark
from benchbox.sql_compat.rules.execution_filter.starrocks_write_primitives import (
    STARROCKS_WRITE_PRIMITIVES_OPERATION_SKIPS,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture()
def write(tmp_path):
    benchmark = WritePrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
    benchmark._setup_dialect = "starrocks"
    return benchmark


@pytest.mark.parametrize("operation_id", sorted(STARROCKS_WRITE_PRIMITIVES_OPERATION_SKIPS))
def test_returning_operations_are_skipped_on_starrocks(write, operation_id):
    sql, reason = write._get_effective_write_sql(write.get_operation(operation_id), "starrocks")
    assert sql is None
    assert reason.startswith(f"Operation '{operation_id}' is skipped on StarRocks: StarRocks 3.5")


def test_every_merge_into_operation_is_skipped_on_starrocks(write):
    merges = [op for op in write.get_all_operations().values() if "MERGE INTO" in op.write_sql.upper()]
    assert len(merges) > 10
    for operation in merges:
        sql, reason = write._get_effective_write_sql(operation, "starrocks")
        assert sql is None
        assert "MERGE" in reason or "RETURNING" in reason or "Stream Load" in reason


def test_every_bulk_load_operation_is_skipped_on_starrocks(write):
    bulk_load = write.get_operations_by_category("bulk_load")
    assert bulk_load
    for operation in bulk_load.values():
        sql, reason = write._get_effective_write_sql(operation, "starrocks")
        assert sql is None
        assert "Stream Load" in reason


@pytest.mark.parametrize(
    "operation_id",
    [
        "insert_select_simple",
        "insert_batch_values_100",
        "insert_batch_values_1000",
        "delete_gdpr_suppliers_1pct",
        "delete_cascade_simulation",
        "merge_scd_type2_basic",
    ],
)
def test_operations_starrocks_can_run_are_not_skipped(write, operation_id):
    sql, reason = write._get_effective_write_sql(write.get_operation(operation_id), "starrocks")
    assert reason is None
    assert sql


def test_batch_values_use_the_starrocks_series_table_function(write):
    sql = write._rewrite_transactional_sql_for_platform(
        write.get_operation("insert_batch_values_100").write_sql, "starrocks"
    )
    assert "TABLE(generate_series(0, 99))" in sql
    assert "unnest" not in sql.lower()


@pytest.mark.parametrize(
    "operation_id",
    ["insert_on_conflict_ignore", "delete_all_truncate_comparison", "ddl_drop_index", "ddl_create_table_with_index"],
)
def test_starrocks_overrides_replace_the_syntax_starrocks_cannot_parse(write, operation_id):
    sql, reason = write._get_effective_write_sql(write.get_operation(operation_id), "starrocks")
    assert reason is None
    assert "ON CONFLICT" not in sql
    assert "CASE WHEN" not in sql
    if operation_id == "delete_all_truncate_comparison":
        assert "WHERE 1 = 1" in sql
