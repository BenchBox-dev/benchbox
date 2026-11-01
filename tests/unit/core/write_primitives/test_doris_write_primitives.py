from __future__ import annotations

import pytest

from benchbox.core.write_primitives.benchmark import WritePrimitivesBenchmark
from benchbox.sql_compat.rules.execution_filter.doris_write_primitives import (
    DORIS_WRITE_PRIMITIVES_OPERATION_SKIPS,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture()
def write(tmp_path):
    return WritePrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)


@pytest.mark.parametrize("operation_id", sorted(DORIS_WRITE_PRIMITIVES_OPERATION_SKIPS))
def test_unsupported_operations_are_skipped_on_doris(write, operation_id):
    sql, reason = write._get_effective_write_sql(write.get_operation(operation_id), "doris")
    assert sql is None
    assert reason.startswith(f"Operation '{operation_id}' is skipped on Doris: Doris 4.0.3")


def test_every_bulk_load_operation_is_skipped_on_doris(write):
    bulk_load = write.get_operations_by_category("bulk_load")
    assert bulk_load
    for operation in bulk_load.values():
        sql, reason = write._get_effective_write_sql(operation, "doris")
        assert sql is None
        assert "Stream Load" in reason


@pytest.mark.parametrize(
    "operation_id",
    [
        "insert_on_conflict_ignore",
        "merge_simple_upsert_small",
        "merge_overlap_10pct",
        "merge_conditional_update",
        "ddl_alter_table_rename_column",
        "ddl_drop_index",
        "ddl_create_table_with_index",
    ],
)
def test_doris_overrides_replace_the_syntax_doris_cannot_parse(write, operation_id):
    sql, reason = write._get_effective_write_sql(write.get_operation(operation_id), "doris")
    assert reason is None
    assert "ON CONFLICT" not in sql
    assert "WHEN NOT MATCHED THEN INSERT\n" not in sql
    assert "RENAME COLUMN old_name TO" not in sql
    assert "CASE WHEN" not in sql
