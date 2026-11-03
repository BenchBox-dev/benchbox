from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from benchbox.core.write_primitives.benchmark import WritePrimitivesBenchmark
from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import CompatibilityContext, Phase
from benchbox.sql_compat.registry import REGISTRY

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture()
def wp(tmp_path):
    return WritePrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)


@pytest.mark.parametrize(
    "operation_id",
    [
        "delete_single_row_pk",
        "merge_simple_upsert_small",
        "bulk_load_csv_small_uncompressed",
        "insert_on_conflict_ignore",
        "update_returning",
        "ddl_drop_index",
    ],
)
def test_unsupported_operations_are_skipped_on_questdb(wp, operation_id):
    operation = wp.get_operation(operation_id)
    sql, reason = wp._get_effective_write_sql(operation, platform_key="questdb")
    assert sql is None
    assert reason.startswith(f"Operation '{operation_id}' is skipped on QuestDB: QuestDB 10.0.1")


def test_supported_operations_are_not_skipped_on_questdb(wp):
    for operation_id in ("insert_single_row", "update_single_row_pk", "ddl_create_table_as_select_simple"):
        sql, reason = wp._get_effective_write_sql(wp.get_operation(operation_id), platform_key="questdb")
        assert reason is None
        assert sql


def test_other_platforms_still_run_questdb_skipped_operations(wp):
    sql, reason = wp._get_effective_write_sql(wp.get_operation("delete_single_row_pk"), platform_key="duckdb")
    assert reason is None
    assert sql


def test_skips_are_registered_as_execution_filter_rules():
    from benchbox.sql_compat.rules.execution_filter import questdb_write_primitives

    ctx = CompatibilityContext(
        platform="questdb",
        platform_version=None,
        benchmark="write_primitives",
        query_id="merge_overlap_10pct",
        phase=Phase.EXECUTION_FILTER,
        mode="sql",
        dialect="questdb",
    )
    decision = REGISTRY.resolve(ctx)
    assert decision is not None
    assert decision.action is CompatAction.SKIP_QUERY
    assert "MERGE" in decision.payload.reason


@pytest.mark.parametrize("operation_id", ["insert_select_simple", "insert_select_union"])
def test_questdb_insert_select_override_has_no_leading_delete(wp, operation_id):
    operation = wp.get_operation(operation_id)
    sql, reason = wp._get_effective_write_sql(operation, platform_key="questdb")
    assert reason is None
    assert "DELETE" not in sql.upper()
    assert sql.lstrip().upper().startswith("INSERT INTO")


def test_questdb_date_arithmetic_uses_dateadd_for_write_and_cleanup(wp):
    operation = wp.get_operation("update_date_arithmetic")
    sql, _ = wp._get_effective_write_sql(operation, platform_key="questdb")
    assert "DATEADD('d', 7, o_orderdate)" in sql
    assert operation.cleanup_platform_overrides["questdb"].count("DATEADD('d', -7, o_orderdate)") == 1


def test_questdb_view_validation_reads_views_function(wp):
    operation = wp.get_operation("ddl_create_view_simple")
    assert "views()" in operation.validation_queries[0].platform_overrides["questdb"]


def test_questdb_new_key_population_avoids_scalar_subquery_arithmetic(wp):
    wp._setup_dialect = "questdb"
    sql = wp._get_population_sql("scd2_ops_stage_customer", "customer")
    assert "c_custkey + max_key.max_custkey" in sql
    assert "CROSS JOIN (SELECT MAX(c_custkey) AS max_custkey FROM" in sql
    assert "c_custkey + (SELECT" not in sql


def test_other_dialects_keep_scalar_subquery_arithmetic(wp):
    wp._setup_dialect = "duckdb"
    sql = wp._get_population_sql("scd2_ops_stage_customer", "customer")
    assert "c_custkey + (SELECT MAX(c_custkey)" in sql


def test_questdb_populates_one_statement_at_a_time(wp):
    wp._setup_dialect = "questdb"
    connection = Mock()
    connection.execute.return_value = SimpleNamespace(platform_result=None)
    wp._execute_population_sql(connection, "INSERT INTO a SELECT 1; INSERT INTO b SELECT 2")
    assert [call.args[0].strip() for call in connection.execute.call_args_list] == [
        "INSERT INTO a SELECT 1",
        "INSERT INTO b SELECT 2",
    ]


def test_questdb_staging_manifest_is_cleared_with_truncate(wp):
    wp._setup_dialect = "questdb"
    connection = Mock()
    wp._delete_staging_manifest_entry(connection, "Write Primitives")
    connection.execute.assert_called_once_with('TRUNCATE TABLE "benchbox_staging_manifest_v3"')


def test_other_dialects_delete_the_manifest_row(wp):
    wp._setup_dialect = "duckdb"
    connection = Mock()
    wp._delete_staging_manifest_entry(connection, "Write Primitives")
    assert connection.execute.call_args.args[0].startswith('DELETE FROM "benchbox_staging_manifest_v3" WHERE')


def test_scd2_skip_reason_does_not_claim_a_missing_merge(wp):
    _, reason = wp._get_effective_write_sql(wp.get_operation("merge_scd_type2_basic"), platform_key="questdb")
    assert "SCD2" in reason
    assert "no MERGE" not in reason
