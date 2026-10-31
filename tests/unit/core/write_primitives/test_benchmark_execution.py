# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from benchbox.core.write_primitives.benchmark import OperationResult, WritePrimitivesBenchmark

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


class TestOperationResult:
    def test_operation_result_creation(self):

        result = OperationResult(
            operation_id="INSERT_001",
            success=True,
            write_duration_ms=50.5,
            rows_affected=100,
            validation_duration_ms=10.2,
            validation_passed=True,
            validation_results=[{"query_id": "V1", "passed": True}],
            cleanup_duration_ms=5.0,
            cleanup_success=True,
        )

        assert result.operation_id == "INSERT_001"
        assert result.success is True
        assert result.write_duration_ms == 50.5
        assert result.rows_affected == 100
        assert result.validation_passed is True
        assert result.cleanup_success is True
        assert result.error is None
        assert result.cleanup_warning is None

    def test_operation_result_with_error(self):

        result = OperationResult(
            operation_id="FAILED_OP",
            success=False,
            write_duration_ms=0.0,
            rows_affected=0,
            validation_duration_ms=0.0,
            validation_passed=False,
            validation_results=[],
            cleanup_duration_ms=0.0,
            cleanup_success=False,
            error="Database error: connection lost",
            cleanup_warning="Cleanup failed",
        )

        assert result.success is False
        assert result.error == "Database error: connection lost"
        assert result.cleanup_warning == "Cleanup failed"


class TestWritePrimitivesBenchmarkInit:
    def test_default_initialization(self, tmp_path):

        with patch("benchbox.core.write_primitives.benchmark.get_benchmark_runs_datagen_path") as mock_path:
            mock_path.return_value = tmp_path
            wp_benchmark = WritePrimitivesBenchmark()

            assert wp_benchmark._name == "Write Primitives Benchmark"
            assert wp_benchmark._version == "2.0"
            assert wp_benchmark.scale_factor == 1.0
            assert wp_benchmark.tables == {}

    def test_custom_scale_factor(self, tmp_path):

        with patch("benchbox.core.write_primitives.benchmark.get_benchmark_runs_datagen_path") as mock_path:
            mock_path.return_value = tmp_path
            wp_benchmark = WritePrimitivesBenchmark(scale_factor=0.1)

            assert wp_benchmark.scale_factor == 0.1

    def test_custom_output_dir(self, tmp_path):

        wp_benchmark = WritePrimitivesBenchmark(output_dir=tmp_path)

        assert str(wp_benchmark.output_dir) == str(tmp_path)

    def test_data_source_benchmark(self, tmp_path):

        wp_benchmark = WritePrimitivesBenchmark(output_dir=tmp_path)
        assert wp_benchmark.get_data_source_benchmark() == "tpch"


class TestQuoteIdentifier:
    @pytest.fixture
    def wp_benchmark(self, tmp_path):
        return WritePrimitivesBenchmark(output_dir=tmp_path)

    def test_valid_identifier(self, wp_benchmark):

        assert wp_benchmark._quote_identifier("table_name") == '"table_name"'
        assert wp_benchmark._quote_identifier("Orders") == '"Orders"'
        assert wp_benchmark._quote_identifier("_private") == '"_private"'

    def test_databricks_uses_backticks(self, wp_benchmark):
        from benchbox.core.primitives_benchmark_utils import quote_identifier_for_dialect

        assert quote_identifier_for_dialect("orders", "databricks") == "`orders`"
        assert quote_identifier_for_dialect("orders", "standard") == '"orders"'

    def test_identifier_with_numbers(self, wp_benchmark):

        assert wp_benchmark._quote_identifier("table123") == '"table123"'
        assert wp_benchmark._quote_identifier("t1") == '"t1"'

    def test_invalid_identifier_with_spaces(self, wp_benchmark):

        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            wp_benchmark._quote_identifier("table name")

    def test_invalid_identifier_with_semicolon(self, wp_benchmark):

        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            wp_benchmark._quote_identifier("table;DROP TABLE users")

    def test_invalid_identifier_starting_with_number(self, wp_benchmark):

        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            wp_benchmark._quote_identifier("123table")

    def test_invalid_identifier_with_special_chars(self, wp_benchmark):

        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            wp_benchmark._quote_identifier("table-name")
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            wp_benchmark._quote_identifier("table.name")

    def test_uppercase_dialects_quote_uppercase(self, wp_benchmark, tmp_path):
        from benchbox.core.primitives_benchmark_utils import quote_identifier_for_dialect

        assert quote_identifier_for_dialect("orders", "snowflake") == '"ORDERS"'
        assert quote_identifier_for_dialect("orders", "bigquery") == "`ORDERS`"
        assert quote_identifier_for_dialect("orders", "BigQuery") == "`ORDERS`"
        assert quote_identifier_for_dialect("orders", "Snowflake") == '"ORDERS"'
        assert quote_identifier_for_dialect("orders", "duckdb") == '"orders"'
        assert quote_identifier_for_dialect("orders", None) == '"orders"'

        wp_benchmark._setup_dialect = "snowflake"
        assert wp_benchmark._quote_identifier("orders") == '"ORDERS"'
        wp_benchmark._setup_dialect = "bigquery"
        assert wp_benchmark._quote_identifier("orders") == "`ORDERS`"
        wp_benchmark._setup_dialect = "standard"
        assert wp_benchmark._quote_identifier("orders") == '"orders"'

    def test_scd2_hash_cast_per_setup_dialect(self, wp_benchmark, tmp_path):
        wp_benchmark._setup_dialect = "standard"
        assert "AS VARCHAR" in wp_benchmark._scd2_row_hash_expr("c_acctbal")
        wp_benchmark._setup_dialect = "bigquery"
        expr = wp_benchmark._scd2_row_hash_expr("c_acctbal")
        assert "AS STRING" in expr
        assert "VARCHAR" not in expr

    def test_bigquery_quote_rejects_unsafe_identifier(self, wp_benchmark, tmp_path):
        from benchbox.core.primitives_benchmark_utils import quote_identifier_for_dialect

        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            quote_identifier_for_dialect("table-name", "bigquery")

    def test_fetch_count_probe_raises_on_failed_platform_result(self, wp_benchmark, tmp_path):
        from benchbox.core.primitives_benchmark_utils import fetch_count_probe

        class FailedCursor:
            platform_result = {"status": "FAILED", "error_type": "BadRequest", "error": "Syntax error"}

            def fetchone(self):
                return None

        class OkCursor:
            def fetchone(self):
                return (15000,)

        class RawCursor:
            def fetchone(self):
                return (42,)

        connection = Mock()
        connection.execute.return_value = OkCursor()
        assert fetch_count_probe(connection, "SELECT COUNT(*) FROM `ORDERS`") == 15000

        connection.execute.return_value = FailedCursor()
        with pytest.raises(RuntimeError, match="Count probe failed.*Syntax error"):
            fetch_count_probe(connection, "SELECT COUNT(*) FROM `ORDERS`")

        connection.execute.return_value = RawCursor()
        assert fetch_count_probe(connection, "SELECT COUNT(*) FROM orders") == 42


class TestFailedPlatformError:
    @pytest.fixture
    def wp_benchmark(self, tmp_path):
        return WritePrimitivesBenchmark(output_dir=tmp_path)

    def test_failed_result_returns_error(self):
        from benchbox.core.primitives_benchmark_utils import failed_platform_error

        class FailedCursor:
            platform_result = {"status": "FAILED", "error": "Syntax error"}

        assert failed_platform_error(FailedCursor()) == "Syntax error"

    def test_success_result_returns_none(self):
        from benchbox.core.primitives_benchmark_utils import failed_platform_error

        class OkCursor:
            platform_result = {"status": "SUCCESS", "rows_returned": 1}

            def fetchall(self):
                return [(1,)]

        assert failed_platform_error(OkCursor()) is None

    def test_plain_cursor_returns_none(self):
        from benchbox.core.primitives_benchmark_utils import failed_platform_error

        class RawCursor:
            def fetchall(self):
                return [(1,)]

        assert failed_platform_error(RawCursor()) is None


class TestSnowflakeCatalogCoverage:
    @pytest.fixture
    def wp_benchmark(self, tmp_path):
        return WritePrimitivesBenchmark(output_dir=tmp_path)

    @pytest.mark.parametrize(
        "op_id",
        [
            "insert_returning_clause",
            "update_returning",
            "delete_returning",
            "merge_returning_clause",
        ],
    )
    def test_returning_ops_skip_snowflake(self, wp_benchmark, op_id):
        operation = wp_benchmark.get_operation(op_id)
        assert operation.platform_overrides.get("snowflake") is None
        assert "snowflake" in operation.platform_overrides

    def test_info_schema_checks_compare_uppercase(self, wp_benchmark):
        operation = wp_benchmark.get_operation("ddl_create_table_simple")
        table_exists_sql = next(v.sql for v in operation.validation_queries if v.id == "table_exists")
        assert "UPPER(table_name) = UPPER('test_simple')" in table_exists_sql

    def test_batch_ops_use_generator_on_snowflake(self, wp_benchmark):
        for op_id, base, rows in (
            ("insert_batch_values_100", "9000100", 100),
            ("insert_batch_values_1000", "9001000", 1000),
        ):
            operation = wp_benchmark.get_operation(op_id)
            override = operation.platform_overrides.get("snowflake")
            assert override is not None
            assert f"GENERATOR(ROWCOUNT => {rows})" in override
            assert "ROW_NUMBER() OVER (ORDER BY SEQ4()) - 1 AS n" in override
            assert "generate_series" not in override
            assert base in override

    def test_conflict_op_uses_merge_on_snowflake(self, wp_benchmark):
        operation = wp_benchmark.get_operation("insert_on_conflict_ignore")
        override = operation.platform_overrides.get("snowflake")
        assert override is not None
        assert override.startswith("MERGE INTO insert_ops_orders")
        assert "WHEN NOT MATCHED THEN INSERT" in override
        assert "ON CONFLICT" not in override

    @pytest.mark.parametrize(
        "op_id",
        [
            "bulk_load_csv_small_gzip",
            "bulk_load_parquet_medium_snappy",
            "bulk_load_date_format_custom",
        ],
    )
    def test_bulk_ops_skip_snowflake(self, wp_benchmark, op_id):
        operation = wp_benchmark.get_operation(op_id)
        assert "snowflake" in operation.platform_overrides
        assert operation.platform_overrides.get("snowflake") is None


class TestSnowflakeMergeAndIndexCoverage:
    @pytest.fixture
    def wp_benchmark(self, tmp_path):
        return WritePrimitivesBenchmark(output_dir=tmp_path)

    @pytest.mark.parametrize(
        "op_id",
        [
            "merge_simple_upsert_small",
            "merge_overlap_10pct",
            "merge_overlap_50pct",
            "merge_overlap_90pct",
            "merge_no_overlap_all_insert",
            "merge_conditional_update",
        ],
    )
    def test_merge_ops_use_explicit_values_on_snowflake(self, wp_benchmark, op_id):
        operation = wp_benchmark.get_operation(op_id)
        override = operation.platform_overrides.get("snowflake")
        assert override is not None
        assert "WHEN NOT MATCHED THEN INSERT VALUES (source.o_orderkey" in override
        assert "WHEN NOT MATCHED THEN INSERT\n" not in override

    def test_cte_merge_inlines_using_on_snowflake(self, wp_benchmark):
        operation = wp_benchmark.get_operation("merge_with_cte_source")
        override = operation.platform_overrides.get("snowflake")
        assert override is not None
        assert override.startswith("MERGE INTO merge_ops_summary_target")
        assert "WITH" not in override.split("MERGE")[0]
        assert "JOIN customer c ON" in override

    @pytest.mark.parametrize(
        "op_id",
        [
            "merge_upsert_with_delete",
            "merge_error_handling",
            "ddl_create_index_on_existing",
            "ddl_drop_index",
        ],
    )
    def test_unsupported_ops_skip_snowflake(self, wp_benchmark, op_id):
        operation = wp_benchmark.get_operation(op_id)
        assert "snowflake" in operation.platform_overrides
        assert operation.platform_overrides.get("snowflake") is None

    def test_table_with_index_measures_table_creation_on_snowflake(self, wp_benchmark):
        operation = wp_benchmark.get_operation("ddl_create_table_with_index")
        override = operation.platform_overrides.get("snowflake")
        assert override is not None
        assert override.startswith("CREATE OR REPLACE TABLE test_indexed")
        assert "CREATE INDEX" not in override


class TestDatabricksIndexAndBulkCoverage:
    @pytest.fixture
    def wp_benchmark(self, tmp_path):
        return WritePrimitivesBenchmark(output_dir=tmp_path)

    @pytest.mark.parametrize(
        "op_id",
        [
            "ddl_create_index_on_existing",
            "ddl_drop_index",
            "ddl_alter_table_drop_column",
            "ddl_alter_table_rename_column",
        ],
    )
    def test_unsupported_ddl_skips_databricks(self, wp_benchmark, op_id):
        operation = wp_benchmark.get_operation(op_id)
        assert "databricks" in operation.platform_overrides
        assert operation.platform_overrides.get("databricks") is None

    def test_table_with_index_measures_table_creation_on_databricks(self, wp_benchmark):
        operation = wp_benchmark.get_operation("ddl_create_table_with_index")
        override = operation.platform_overrides.get("databricks")
        assert override is not None
        assert override.startswith("CREATE OR REPLACE TABLE test_indexed")
        assert "CREATE INDEX" not in override

    def test_bulk_load_ops_skip_databricks(self, wp_benchmark):
        operations = wp_benchmark.get_all_operations()
        skips = [
            op_id
            for op_id, op in operations.items()
            if op_id.startswith("bulk_load_")
            and "databricks" in op.platform_overrides
            and op.platform_overrides.get("databricks") is None
        ]
        total = sum(1 for op_id in operations if op_id.startswith("bulk_load_"))
        assert total > 0
        assert len(skips) == total

    @pytest.mark.parametrize(
        "op_id,hi",
        [
            ("insert_batch_values_100", 99),
            ("insert_batch_values_1000", 999),
        ],
    )
    def test_batch_ops_use_explode_sequence_on_databricks(self, wp_benchmark, op_id, hi):
        operation = wp_benchmark.get_operation(op_id)
        override = operation.platform_overrides.get("databricks")
        assert override is not None
        assert f"EXPLODE(SEQUENCE(0, {hi}))" in override
        assert "generate_series" not in override

    def test_conflict_op_uses_insert_star_merge_on_databricks(self, wp_benchmark):
        operation = wp_benchmark.get_operation("insert_on_conflict_ignore")
        override = operation.platform_overrides.get("databricks")
        assert override is not None
        assert override.startswith("MERGE INTO insert_ops_orders")
        assert "WHEN NOT MATCHED THEN INSERT *" in override

    def test_update_join_uses_exists_on_databricks(self, wp_benchmark):
        operation = wp_benchmark.get_operation("update_with_join")
        override = operation.platform_overrides.get("databricks")
        assert override is not None
        assert "UPDATE update_ops_orders" in override
        assert "SET o_comment = 'joined_update'\nFROM" not in override
        assert "WHERE EXISTS" in override

    @pytest.mark.parametrize(
        "op_id",
        [
            "merge_simple_upsert_small",
            "merge_overlap_10pct",
            "merge_overlap_50pct",
            "merge_overlap_90pct",
            "merge_no_overlap_all_insert",
            "merge_conditional_update",
        ],
    )
    def test_select_star_merges_use_insert_star_on_databricks(self, wp_benchmark, op_id):
        operation = wp_benchmark.get_operation(op_id)
        override = operation.platform_overrides.get("databricks")
        assert override is not None
        assert "WHEN NOT MATCHED" in override
        assert "INSERT *" in override
        assert "INSERT VALUES" not in override

    @pytest.mark.parametrize(
        "op_id",
        [
            "merge_conditional_insert",
            "merge_from_subquery_aggregated",
            "merge_with_join_condition",
            "merge_computed_values",
            "merge_etl_aggregation_pattern",
            "merge_deduplication_window_function",
            "merge_with_cte_source",
        ],
    )
    def test_partial_source_merges_list_columns_on_databricks(self, wp_benchmark, op_id):
        operation = wp_benchmark.get_operation(op_id)
        override = operation.platform_overrides.get("databricks")
        assert override is not None
        assert "INSERT (" in override
        assert "VALUES" in override


class TestBigQueryBatchAndMergeCoverage:
    @pytest.fixture
    def wp_benchmark(self, tmp_path):
        return WritePrimitivesBenchmark(output_dir=tmp_path)

    @pytest.mark.parametrize(
        "op_id,base,hi",
        [
            ("insert_batch_values_100", "9000100", 99),
            ("insert_batch_values_1000", "9001000", 999),
        ],
    )
    def test_batch_ops_use_generate_array_on_bigquery(self, wp_benchmark, op_id, base, hi):
        operation = wp_benchmark.get_operation(op_id)
        override = operation.platform_overrides.get("bigquery")
        assert override is not None
        assert f"GENERATE_ARRAY(0, {hi})" in override
        assert "generate_series" not in override
        assert base in override

    def test_conflict_op_uses_merge_on_bigquery(self, wp_benchmark):
        operation = wp_benchmark.get_operation("insert_on_conflict_ignore")
        override = operation.platform_overrides.get("bigquery")
        assert override is not None
        assert override.startswith("MERGE INTO insert_ops_orders")
        assert "WHEN NOT MATCHED THEN INSERT ROW" in override

    @pytest.mark.parametrize(
        "op_id",
        [
            "merge_simple_upsert_small",
            "merge_overlap_10pct",
            "merge_overlap_50pct",
            "merge_overlap_90pct",
            "merge_no_overlap_all_insert",
            "merge_conditional_update",
            "merge_with_cte_source",
        ],
    )
    def test_merge_ops_have_bigquery_override(self, wp_benchmark, op_id):
        operation = wp_benchmark.get_operation(op_id)
        override = operation.platform_overrides.get("bigquery")
        assert override is not None
        assert "INSERT VALUES (source.o_orderkey" in override

    @pytest.mark.parametrize("op_id", ["update_date_arithmetic", "merge_date_arithmetic"])
    def test_date_arithmetic_ops_are_skipped_on_bigquery(self, wp_benchmark, op_id):
        operation = wp_benchmark.get_operation(op_id)
        assert "bigquery" in operation.platform_overrides
        assert operation.platform_overrides["bigquery"] is None
        assert "INTERVAL '" in (operation.cleanup_sql or "")

    @pytest.mark.parametrize(
        "op_id,val_id",
        [
            ("merge_scd_type2_basic", "basic_inserts_expected_new_version_count"),
            ("merge_scd_type2_basic", "basic_closes_expected_changed_count"),
            ("merge_scd_type2_new_keys_only", "new_keys_only_inserts_expected_count"),
        ],
    )
    def test_scd2_validations_avoid_bare_select_where_on_bigquery(self, wp_benchmark, op_id, val_id):
        operation = wp_benchmark.get_operation(op_id)
        val = next(v for v in operation.validation_queries if v.id == val_id)
        override = val.platform_overrides.get("bigquery")
        assert override is not None
        assert "SELECT 1 WHERE" not in override
        assert "FROM (SELECT 1 AS _one) AS _t WHERE" in override


class TestPortableDmlTargetCorrelation:
    @pytest.fixture
    def wp_benchmark(self, tmp_path):
        return WritePrimitivesBenchmark(output_dir=tmp_path)

    def test_no_update_or_delete_target_alias_anywhere(self, wp_benchmark):
        import re

        aliased_target = re.compile(r"\b(?:UPDATE|DELETE\s+FROM)\s+[\w.`\"]+\s+AS\s+\w+", re.IGNORECASE)
        offenders = []
        for op_id, operation in wp_benchmark.get_all_operations().items():
            statements = [(field, getattr(operation, field, None)) for field in ("write_sql", "cleanup_sql")]
            statements.extend(operation.platform_overrides.items())
            for field, sql in statements:
                if sql and aliased_target.search(sql):
                    offenders.append(f"{op_id}.{field}")
        assert offenders == []

    def test_update_and_delete_self_reference_by_table_name(self, wp_benchmark):
        expectations = {
            "delete_with_aggregation": "delete_ops_orders.o_orderkey",
            "delete_with_join": "delete_ops_orders.o_custkey",
            "delete_with_not_exists": "delete_ops_orders.o_orderkey",
            "update_from_select": "update_ops_orders.o_orderkey",
            "update_with_aggregate": "update_ops_orders.o_orderkey",
            "update_with_join": "update_ops_orders.o_custkey",
            "update_with_subquery": "update_ops_orders.o_orderkey",
        }
        for op_id, qualified_column in expectations.items():
            operation = wp_benchmark.get_operation(op_id)
            assert qualified_column in operation.write_sql

    def test_scd2_update_targets_self_reference_by_table_name(self, wp_benchmark):
        for op_id in ("merge_scd_type2_basic", "merge_scd_type2_no_change"):
            operation = wp_benchmark.get_operation(op_id)
            assert "UPDATE scd2_ops_dim_customer\n" in operation.write_sql
            assert "scd2_ops_dim_customer.c_custkey" in operation.write_sql
            assert "scd2_ops_dim_customer.row_hash" in operation.write_sql

    def test_cleanup_sql_matches_write_sql_correlation_style(self, wp_benchmark):
        for op_id in ("update_with_aggregate", "update_with_subquery"):
            operation = wp_benchmark.get_operation(op_id)
            cleanup = operation.cleanup_sql or ""
            assert cleanup.strip()
            assert "AS u" not in cleanup
            assert "update_ops_orders.o_orderkey" in cleanup


class TestReplacePlaceholders:
    @pytest.fixture
    def wp_benchmark(self, tmp_path):
        return WritePrimitivesBenchmark(output_dir=tmp_path)

    def test_file_path_placeholder(self, wp_benchmark):

        sql = "COPY table FROM '{file_path}/data.csv'"
        result = wp_benchmark._replace_placeholders(sql)

        assert "{file_path}" not in result
        assert "write_primitives_auxiliary" in result

    def test_no_placeholder(self, wp_benchmark):

        sql = "SELECT * FROM orders"
        result = wp_benchmark._replace_placeholders(sql)
        assert result == sql


class TestTableExists:
    @pytest.fixture
    def wp_benchmark(self, tmp_path):
        return WritePrimitivesBenchmark(output_dir=tmp_path)

    def test_table_exists_success(self, wp_benchmark):

        mock_conn = Mock()
        mock_conn.execute.return_value = None

        result = wp_benchmark._table_exists(mock_conn, "orders")
        assert result is True

    def test_table_does_not_exist(self, wp_benchmark):

        mock_conn = Mock()
        mock_conn.execute.side_effect = Exception("Table 'test' does not exist")

        result = wp_benchmark._table_exists(mock_conn, "test")
        assert result is False

    def test_table_exists_invalid_name(self, wp_benchmark):

        mock_conn = Mock()

        result = wp_benchmark._table_exists(mock_conn, "invalid;DROP TABLE")
        assert result is False
        mock_conn.execute.assert_not_called()

    def test_table_exists_unexpected_error(self, wp_benchmark):

        mock_conn = Mock()
        mock_conn.execute.side_effect = Exception("Connection timeout")

        result = wp_benchmark._table_exists(mock_conn, "orders")
        assert result is False

    def test_table_exists_uses_setup_dialect_quoting(self, wp_benchmark):
        mock_conn = Mock()
        mock_conn.execute.return_value = None

        wp_benchmark._setup_dialect = "bigquery"
        assert wp_benchmark._table_exists(mock_conn, "orders") is True
        sent_sql = mock_conn.execute.call_args[0][0]
        assert sent_sql == "SELECT 1 FROM `ORDERS` LIMIT 0"

        wp_benchmark._setup_dialect = "snowflake"
        assert wp_benchmark._table_exists(mock_conn, "orders") is True
        sent_sql = mock_conn.execute.call_args[0][0]
        assert sent_sql == 'SELECT 1 FROM "ORDERS" LIMIT 0'


class TestOperationManagement:
    @pytest.fixture
    def wp_benchmark(self, tmp_path):
        return WritePrimitivesBenchmark(output_dir=tmp_path)

    def test_get_operation_categories(self, wp_benchmark):

        categories = wp_benchmark.get_operation_categories()

        assert isinstance(categories, list)
        assert len(categories) > 0
        assert "insert" in categories or "INSERT" in [c.upper() for c in categories]

    def test_get_all_operations(self, wp_benchmark):

        operations = wp_benchmark.get_all_operations()

        assert isinstance(operations, dict)
        assert len(operations) > 0

    def test_get_operation(self, wp_benchmark):

        operations = wp_benchmark.get_all_operations()
        if operations:
            first_op_id = next(iter(operations.keys()))
            operation = wp_benchmark.get_operation(first_op_id)
            assert operation is not None
            assert hasattr(operation, "write_sql")

    def test_get_operation_invalid_id(self, wp_benchmark):

        with pytest.raises((ValueError, KeyError)):
            wp_benchmark.get_operation("NONEXISTENT_OPERATION_12345")

    def test_get_queries(self, wp_benchmark):

        queries = wp_benchmark.get_queries()

        assert isinstance(queries, dict)
        for sql in queries.values():
            assert isinstance(sql, str)

    def test_get_queries_by_category(self, wp_benchmark):

        categories = wp_benchmark.get_operation_categories()
        if categories:
            category = categories[0]
            queries = wp_benchmark.get_queries_by_category(category)
            assert isinstance(queries, dict)


class TestBenchmarkInfo:
    @pytest.fixture
    def wp_benchmark(self, tmp_path):
        return WritePrimitivesBenchmark(output_dir=tmp_path)

    def test_get_benchmark_info(self, wp_benchmark):

        info = wp_benchmark.get_benchmark_info()

        assert isinstance(info, dict)
        assert "name" in info
        assert "version" in info
        assert "scale_factor" in info
        assert "total_operations" in info
        assert "categories" in info
        assert "data_source" in info
        assert info["data_source"] == "tpch"

    def test_get_schema(self, wp_benchmark):

        schema = wp_benchmark.get_schema()

        assert isinstance(schema, dict)
        assert len(schema) > 0

    def test_get_create_tables_sql(self, wp_benchmark):

        sql = wp_benchmark.get_create_tables_sql()

        assert isinstance(sql, str)
        assert "CREATE TABLE" in sql
        assert "Write Primitives Staging Tables" in sql

    def test_get_create_tables_sql_with_tuning(self, wp_benchmark):

        mock_tuning = Mock()
        mock_tuning.primary_keys = Mock(enabled=True)
        mock_tuning.foreign_keys = Mock(enabled=False)

        sql = wp_benchmark.get_create_tables_sql(tuning_config=mock_tuning)

        assert isinstance(sql, str)
        assert "CREATE TABLE" in sql

    def test_get_query(self, wp_benchmark):

        operations = wp_benchmark.get_all_operations()
        if operations:
            first_op_id = next(iter(operations.keys()))
            query = wp_benchmark.get_query(first_op_id)
            assert isinstance(query, str)


class TestExecuteOperation:
    @pytest.fixture
    def wp_benchmark(self, tmp_path):
        return WritePrimitivesBenchmark(output_dir=tmp_path)

    def test_execute_operation_no_connection(self, wp_benchmark):

        with pytest.raises(ValueError, match="Connection is None"):
            wp_benchmark.execute_operation("INSERT_001", None)

    def test_execute_operation_invalid_connection(self, wp_benchmark):

        invalid_conn = "not a connection"
        with pytest.raises(ValueError, match="Invalid connection type"):
            wp_benchmark.execute_operation("INSERT_001", invalid_conn)

    def test_execute_operation_skips_unsupported_datafusion_category(self, wp_benchmark, monkeypatch):
        mock_conn = Mock()
        mock_conn.execute = Mock()

        monkeypatch.setattr(wp_benchmark, "is_setup", lambda conn: True)
        result = wp_benchmark.execute_operation("update_single_row_pk", mock_conn, platform_key="datafusion")

        assert result.status == "SKIPPED"
        assert result.success is True
        assert result.validation_passed is True
        assert result.error is None
        assert "unsupported on platform 'datafusion'" in (result.skip_reason or "")
        mock_conn.execute.assert_not_called()

    def test_execute_operation_skips_postgres_bulk_load_operation(self, wp_benchmark, monkeypatch):
        mock_conn = Mock()
        mock_conn.execute = Mock()
        monkeypatch.setattr(wp_benchmark, "is_setup", lambda conn: True)

        result = wp_benchmark.execute_operation("bulk_load_csv_small_uncompressed", mock_conn, platform_key="postgres")

        assert result.status == "SKIPPED"
        assert result.success is True
        assert result.error is None
        assert "server-side file COPY" in (result.skip_reason or "")
        mock_conn.execute.assert_not_called()

    def test_execute_operation_skips_postgres_sketch_operation(self, wp_benchmark, monkeypatch):
        mock_conn = Mock()
        mock_conn.execute = Mock()
        monkeypatch.setattr(wp_benchmark, "is_setup", lambda conn: True)

        result = wp_benchmark.execute_operation(
            "sketch_ddl_create_persistent_table", mock_conn, platform_key="postgres"
        )

        assert result.status == "SKIPPED"
        assert result.success is True
        assert result.error is None
        assert "DataSketches" in (result.skip_reason or "")
        mock_conn.execute.assert_not_called()

    def test_execute_operation_skips_postgres_merge_shorthand(self, wp_benchmark, monkeypatch):
        mock_conn = Mock()
        mock_conn.execute = Mock()
        monkeypatch.setattr(wp_benchmark, "is_setup", lambda conn: True)

        result = wp_benchmark.execute_operation("merge_simple_upsert_small", mock_conn, platform_key="postgres")

        assert result.status == "SKIPPED"
        assert result.success is True
        assert result.error is None
        assert "MERGE" in (result.skip_reason or "")
        mock_conn.execute.assert_not_called()

    def test_execute_operation_skips_duckdb_merge_gap(self, wp_benchmark, monkeypatch):
        mock_conn = Mock()
        mock_conn.execute = Mock()
        monkeypatch.setattr(wp_benchmark, "is_setup", lambda conn: True)

        result = wp_benchmark.execute_operation("merge_simple_upsert_small", mock_conn, platform_key="duckdb")

        assert result.status == "SKIPPED"
        assert result.success is True
        assert result.error is None
        assert "MERGE" in (result.skip_reason or "")
        mock_conn.execute.assert_not_called()

    def test_scd2_merge_category_ops_are_not_skipped_on_duckdb(self, wp_benchmark):
        for op_id in (
            "merge_scd_type2_basic",
            "merge_scd_type2_no_change",
            "merge_scd_type2_new_keys_only",
        ):
            operation = wp_benchmark.get_operation(op_id)
            assert operation.category == "merge"
            effective_sql, skip_reason = wp_benchmark._get_effective_write_sql(operation, platform_key="duckdb")
            assert skip_reason is None, f"{op_id} was wrongly skipped on DuckDB: {skip_reason}"
            assert effective_sql, f"{op_id} returned no effective SQL"

        merge_op = wp_benchmark.get_operation("merge_simple_upsert_small")
        _, merge_skip = wp_benchmark._get_effective_write_sql(merge_op, platform_key="duckdb")
        assert merge_skip is not None and "MERGE" in merge_skip

    def test_execute_operation_uses_platform_override_when_available(self, wp_benchmark, monkeypatch):
        operation = wp_benchmark.get_operation("insert_on_conflict_ignore")

        assert operation.platform_overrides.get("duckdb")

        class _Result:
            rowcount = 1

            def fetchall(self):
                return [(1,)]

        sql_calls: list[str] = []

        def _execute(sql):
            sql_calls.append(sql)
            return _Result()

        mock_conn = Mock()
        mock_conn.execute = _execute
        monkeypatch.setattr(wp_benchmark, "is_setup", lambda conn: True)

        result = wp_benchmark.execute_operation("insert_on_conflict_ignore", mock_conn, platform_key="duckdb")

        assert result.status == "SUCCESS"
        assert any("INSERT OR IGNORE" in sql for sql in sql_calls)

    def test_execute_operation_rolls_back_after_failed_write(self, wp_benchmark, monkeypatch):
        mock_conn = Mock()
        mock_conn.execute.side_effect = RuntimeError("COPY failed")
        mock_conn.rollback = Mock()
        monkeypatch.setattr(wp_benchmark, "is_setup", lambda conn: True)

        result = wp_benchmark.execute_operation("insert_single_row", mock_conn)

        assert result.status == "FAILED"
        mock_conn.rollback.assert_called_once()

    def test_execute_operation_rewrites_generate_series_for_postgres(self, wp_benchmark, monkeypatch):

        class _Result:
            rowcount = 100

            def fetchall(self):
                return [(100,)]

        sql_calls: list[str] = []

        def _execute(sql):
            sql_calls.append(sql)
            return _Result()

        mock_conn = Mock()
        mock_conn.execute = _execute
        monkeypatch.setattr(wp_benchmark, "is_setup", lambda conn: True)

        result = wp_benchmark.execute_operation("insert_batch_values_100", mock_conn, platform_key="postgres")

        assert result.status == "SUCCESS"
        assert "FROM (SELECT generate_series(0, 99) AS n) t" in sql_calls[0]
        assert "unnest(generate_series" not in sql_calls[0]

    def test_execute_operation_fails_loud_on_failed_platform_write(self, wp_benchmark, monkeypatch):

        class FailedCursor:
            platform_result = {"status": "FAILED", "error": "Actual statement count 2 did not match"}

        mock_conn = Mock()
        mock_conn.execute.return_value = FailedCursor()
        mock_conn.rollback = Mock()
        monkeypatch.setattr(wp_benchmark, "is_setup", lambda conn: True)

        result = wp_benchmark.execute_operation("insert_select_simple", mock_conn)

        assert result.status == "FAILED"
        assert result.success is False
        assert "Actual statement count 2" in (result.error or "")

    def test_execute_operation_fails_validation_on_failed_platform_select(self, wp_benchmark, monkeypatch):

        class OkCursor:
            rowcount = 1

            def fetchall(self):
                return [(1,)]

        class FailedCursor:
            platform_result = {"status": "FAILED", "error": "Object does not exist"}

            def fetchall(self):
                return []

        mock_conn = Mock()
        mock_conn.execute.side_effect = [OkCursor(), FailedCursor(), OkCursor()]
        monkeypatch.setattr(wp_benchmark, "is_setup", lambda conn: True)

        result = wp_benchmark.execute_operation("insert_single_row", mock_conn)

        assert result.status == "VALIDATION_FAILED"
        assert result.validation_passed is False


class TestRunBenchmark:
    @pytest.fixture
    def wp_benchmark(self, tmp_path):
        return WritePrimitivesBenchmark(output_dir=tmp_path)

    def test_run_benchmark_with_mock(self, wp_benchmark):
        mock_conn = Mock()
        mock_conn.execute.return_value = Mock(rowcount=10)

        operations = wp_benchmark.get_all_operations()
        real_op_id = next(iter(operations.keys()))

        with patch.object(wp_benchmark, "is_setup", return_value=True):
            with patch.object(wp_benchmark, "execute_operation") as mock_execute:
                mock_execute.return_value = OperationResult(
                    operation_id=real_op_id,
                    success=True,
                    write_duration_ms=10.0,
                    rows_affected=10,
                    validation_duration_ms=5.0,
                    validation_passed=True,
                    validation_results=[],
                    cleanup_duration_ms=2.0,
                    cleanup_success=True,
                )

                results = wp_benchmark.run_benchmark(mock_conn, operation_ids=[real_op_id])

                assert len(results) == 1
                assert results[0].success is True

    def test_run_benchmark_by_category(self, wp_benchmark):

        mock_conn = Mock()
        mock_conn.execute.return_value = Mock(rowcount=10)

        with patch.object(wp_benchmark, "is_setup", return_value=True):
            with patch.object(wp_benchmark, "execute_operation") as mock_execute:
                mock_execute.return_value = OperationResult(
                    operation_id="cat_op",
                    success=True,
                    write_duration_ms=10.0,
                    rows_affected=10,
                    validation_duration_ms=5.0,
                    validation_passed=True,
                    validation_results=[],
                    cleanup_duration_ms=2.0,
                    cleanup_success=True,
                )

                categories = wp_benchmark.get_operation_categories()
                if categories:
                    results = wp_benchmark.run_benchmark(mock_conn, categories=[categories[0]])
                    assert isinstance(results, list)


class TestSetupAndTeardown:
    @pytest.fixture
    def wp_benchmark(self, tmp_path):
        return WritePrimitivesBenchmark(output_dir=tmp_path)

    def test_teardown_drops_tables(self, wp_benchmark):

        mock_conn = Mock()

        wp_benchmark.teardown(mock_conn)

        assert mock_conn.execute.call_count > 0
        drop_calls = [call for call in mock_conn.execute.call_args_list if "DROP TABLE" in str(call)]
        assert len(drop_calls) > 0

    def test_is_setup_false_when_tables_missing(self, wp_benchmark):

        mock_conn = Mock()
        mock_conn.execute.side_effect = Exception("Table not found")

        result = wp_benchmark.is_setup(mock_conn)
        assert result is False

    def test_is_setup_false_when_tables_empty(self, wp_benchmark):

        mock_conn = Mock()
        mock_conn.execute.return_value.fetchone.return_value = (0,)

        result = wp_benchmark.is_setup(mock_conn)
        assert result is False

    def test_acquire_setup_lock_skips_lock_table_for_datafusion(self, wp_benchmark):
        mock_conn = Mock()

        assert wp_benchmark._acquire_setup_lock(mock_conn, dialect="datafusion") is True
        mock_conn.execute.assert_not_called()

    def test_setup_uses_datafusion_dialect_for_staging_tables(self, wp_benchmark, monkeypatch):

        class _Result:
            def __init__(self, row=(1,)):
                self._row = row

            def fetchone(self):
                return self._row

        mock_conn = Mock()
        mock_conn.execute.return_value = _Result((1,))
        monkeypatch.setattr(wp_benchmark, "_table_exists", lambda conn, table_name: False)

        requested_dialects: list[str] = []

        def _fake_create_sql(table_name, dialect="standard", if_not_exists=False):
            requested_dialects.append(dialect)
            if_not_exists_clause = " IF NOT EXISTS" if if_not_exists else ""
            return f"CREATE TABLE{if_not_exists_clause} {table_name} (id INTEGER)"

        monkeypatch.setattr("benchbox.core.write_primitives.benchmark.get_create_table_sql", _fake_create_sql)

        result = wp_benchmark.setup(mock_conn, force=False, dialect="datafusion")

        assert result["success"] is True
        assert requested_dialects
        assert set(requested_dialects) == {"datafusion"}


class TestCleanupAuxiliaryFiles:
    @pytest.fixture
    def wp_benchmark(self, tmp_path):
        return WritePrimitivesBenchmark(output_dir=tmp_path)

    def test_cleanup_removes_directory(self, wp_benchmark, tmp_path):

        aux_dir = tmp_path / "write_primitives_auxiliary"
        aux_dir.mkdir()
        (aux_dir / "test.csv").write_text("data")

        wp_benchmark.data_generator.files_dir = aux_dir

        wp_benchmark.cleanup_auxiliary_files()

        assert not aux_dir.exists()

    def test_cleanup_handles_nonexistent_dir(self, wp_benchmark, tmp_path):
        aux_dir = tmp_path / "nonexistent"
        wp_benchmark.data_generator.files_dir = aux_dir

        wp_benchmark.cleanup_auxiliary_files()


class _CountingConnection:
    def execute(self, _sql):
        return SimpleNamespace(fetchone=lambda: (1,))


class TestDataFrameSqlParity:
    @pytest.fixture
    def wp_benchmark(self, tmp_path):
        return WritePrimitivesBenchmark(output_dir=tmp_path)

    def test_dataframe_workload_matches_sql_status_mapping_for_same_operations(self, wp_benchmark, monkeypatch):
        operation_ids = ["insert_single_row", "update_single_row_pk", "ddl_create_table_simple"]

        monkeypatch.setattr(
            wp_benchmark,
            "_select_dataframe_operation_ids",
            lambda query_filter=None: operation_ids,
        )

        monkeypatch.setattr(
            "benchbox.platforms.duckdb.DuckDBAdapter.create_connection", lambda self, **_: _CountingConnection()
        )
        monkeypatch.setattr(
            "benchbox.platforms.duckdb.DuckDBAdapter.create_schema",
            lambda self, benchmark, connection: 0.0,
        )
        monkeypatch.setattr(
            "benchbox.platforms.duckdb.DuckDBAdapter.load_data",
            lambda self, benchmark, connection, data_dir: ({}, 0.0, None),
        )
        monkeypatch.setattr(
            "benchbox.platforms.duckdb.DuckDBAdapter.close_connection",
            lambda self, connection: None,
        )

        results_by_id = {
            "insert_single_row": OperationResult(
                operation_id="insert_single_row",
                success=True,
                write_duration_ms=1.0,
                rows_affected=1,
                validation_duration_ms=0.1,
                validation_passed=True,
                validation_results=[],
                cleanup_duration_ms=0.1,
                cleanup_success=True,
            ),
            "update_single_row_pk": OperationResult(
                operation_id="update_single_row_pk",
                success=False,
                write_duration_ms=2.0,
                rows_affected=0,
                validation_duration_ms=0.1,
                validation_passed=False,
                validation_results=[],
                cleanup_duration_ms=0.0,
                cleanup_success=False,
                error="forced update failure",
            ),
            "ddl_create_table_simple": OperationResult(
                operation_id="ddl_create_table_simple",
                success=True,
                write_duration_ms=0.5,
                rows_affected=-1,
                validation_duration_ms=0.1,
                validation_passed=False,
                validation_results=[],
                cleanup_duration_ms=0.0,
                cleanup_success=True,
            ),
        }

        monkeypatch.setattr(
            wp_benchmark,
            "execute_operation",
            lambda op_id, connection, **_kwargs: results_by_id[op_id],
        )

        sql_results = wp_benchmark.run_benchmark(connection=object(), operation_ids=operation_ids)
        expected_status = {
            result.operation_id: ("SUCCESS" if result.success and result.validation_passed else "FAILED")
            for result in sql_results
        }

        class DummyAdapter:
            platform_name = "polars-df"

        dataframe_rows = wp_benchmark.execute_dataframe_workload(
            ctx=None,
            adapter=DummyAdapter(),
            benchmark_config=SimpleNamespace(options={}),
            query_filter=None,
            monitor=None,
            run_options=None,
        )

        assert len(dataframe_rows) == len(operation_ids) * 4

        seen_status: dict[str, set[str]] = {op_id: set() for op_id in operation_ids}
        for row in dataframe_rows:
            query_id = row["query_id"]
            assert query_id in expected_status
            seen_status[query_id].add(row["status"])
            assert row["run_type"] in {"warmup", "measurement"}
            assert row["iteration"] in {0, 1, 2, 3}

        for op_id in operation_ids:
            assert seen_status[op_id] == {expected_status[op_id]}

        ddl_rows = [row for row in dataframe_rows if row["query_id"] == "ddl_create_table_simple"]
        assert ddl_rows
        assert all(row["rows_returned"] == 0 for row in ddl_rows)

    def test_dataframe_workload_query_filter_keeps_sql_subset(self, wp_benchmark, monkeypatch):
        monkeypatch.setattr(
            "benchbox.platforms.duckdb.DuckDBAdapter.create_connection", lambda self, **_: _CountingConnection()
        )
        monkeypatch.setattr(
            "benchbox.platforms.duckdb.DuckDBAdapter.create_schema",
            lambda self, benchmark, connection: 0.0,
        )
        monkeypatch.setattr(
            "benchbox.platforms.duckdb.DuckDBAdapter.load_data",
            lambda self, benchmark, connection, data_dir: ({}, 0.0, None),
        )
        monkeypatch.setattr(
            "benchbox.platforms.duckdb.DuckDBAdapter.close_connection",
            lambda self, connection: None,
        )

        monkeypatch.setattr(
            wp_benchmark,
            "execute_operation",
            lambda op_id, connection, **_kwargs: OperationResult(
                operation_id=op_id,
                success=True,
                write_duration_ms=1.0,
                rows_affected=1,
                validation_duration_ms=0.1,
                validation_passed=True,
                validation_results=[],
                cleanup_duration_ms=0.1,
                cleanup_success=True,
            ),
        )

        class DummyAdapter:
            platform_name = "polars-df"

        rows = wp_benchmark.execute_dataframe_workload(
            ctx=None,
            adapter=DummyAdapter(),
            benchmark_config=SimpleNamespace(options={}),
            query_filter={"INSERT_SINGLE_ROW"},
            monitor=None,
            run_options=None,
        )

        assert rows
        assert {row["query_id"] for row in rows} == {"insert_single_row"}


class TestCheckValidationQuery:
    pytestmark = [pytest.mark.unit, pytest.mark.medium]

    def _make_query(
        self,
        expected_rows=None,
        expected_rows_min=None,
        expected_rows_max=None,
        expected_value_min=None,
        expected_value_max=None,
    ):
        return SimpleNamespace(
            expected_rows=expected_rows,
            expected_rows_min=expected_rows_min,
            expected_rows_max=expected_rows_max,
            expected_value_min=expected_value_min,
            expected_value_max=expected_value_max,
        )

    def test_exact_match_passes(self):
        from benchbox.core.write_primitives.benchmark import _check_validation_query

        assert _check_validation_query(self._make_query(expected_rows=500), actual_rows=500) is True

    def test_exact_mismatch_fails(self):
        from benchbox.core.write_primitives.benchmark import _check_validation_query

        assert _check_validation_query(self._make_query(expected_rows=500), actual_rows=499) is False

    def test_min_max_within_range_passes(self):
        from benchbox.core.write_primitives.benchmark import _check_validation_query

        q = self._make_query(expected_rows_min=10, expected_rows_max=20)
        assert _check_validation_query(q, actual_rows=15) is True

    def test_min_max_below_range_fails(self):
        from benchbox.core.write_primitives.benchmark import _check_validation_query

        q = self._make_query(expected_rows_min=10, expected_rows_max=20)
        assert _check_validation_query(q, actual_rows=9) is False

    def test_min_max_above_range_fails(self):
        from benchbox.core.write_primitives.benchmark import _check_validation_query

        q = self._make_query(expected_rows_min=10, expected_rows_max=20)
        assert _check_validation_query(q, actual_rows=21) is False

    def test_only_min_no_max_passes_above_min(self):
        from benchbox.core.write_primitives.benchmark import _check_validation_query

        q = self._make_query(expected_rows_min=5)
        assert _check_validation_query(q, actual_rows=100) is True

    def test_only_max_no_min_passes_below_max(self):
        from benchbox.core.write_primitives.benchmark import _check_validation_query

        q = self._make_query(expected_rows_max=100)
        assert _check_validation_query(q, actual_rows=50) is True

    def test_no_constraints_always_passes(self):
        from benchbox.core.write_primitives.benchmark import _check_validation_query

        q = self._make_query()
        assert _check_validation_query(q, actual_rows=999) is True

    def test_value_bounds_within_range_passes(self):
        from benchbox.core.write_primitives.benchmark import _check_validation_query

        q = self._make_query(expected_value_min=14000, expected_value_max=16000)
        assert _check_validation_query(q, actual_rows=1, val_result=[(14836.89,)]) is True

    def test_value_bounds_below_range_fails(self):
        from benchbox.core.write_primitives.benchmark import _check_validation_query

        q = self._make_query(expected_value_min=14000, expected_value_max=16000)
        assert _check_validation_query(q, actual_rows=1, val_result=[(0.0,)]) is False

    def test_value_bounds_above_range_fails(self):
        from benchbox.core.write_primitives.benchmark import _check_validation_query

        q = self._make_query(expected_value_min=14000, expected_value_max=16000)
        assert _check_validation_query(q, actual_rows=1, val_result=[(50000.0,)]) is False

    def test_value_bounds_no_rows_fails(self):
        from benchbox.core.write_primitives.benchmark import _check_validation_query

        q = self._make_query(expected_value_min=14000, expected_value_max=16000)
        assert _check_validation_query(q, actual_rows=0, val_result=[]) is False

    def test_value_bounds_non_numeric_fails(self):
        from benchbox.core.write_primitives.benchmark import _check_validation_query

        q = self._make_query(expected_value_min=14000, expected_value_max=16000)
        assert _check_validation_query(q, actual_rows=1, val_result=[("not a number",)]) is False

    def test_value_bounds_integer_scalar_passes(self):
        from benchbox.core.write_primitives.benchmark import _check_validation_query

        q = self._make_query(expected_value_min=6, expected_value_max=8)
        assert _check_validation_query(q, actual_rows=1, val_result=[(7,)]) is True

    def test_value_bounds_multi_row_all_in_range_passes(self):
        from benchbox.core.write_primitives.benchmark import _check_validation_query

        q = self._make_query(expected_value_min=10, expected_value_max=20)
        assert _check_validation_query(q, actual_rows=3, val_result=[(11,), (15,), (19,)]) is True

    def test_value_bounds_multi_row_first_out_of_range_fails(self):
        from benchbox.core.write_primitives.benchmark import _check_validation_query

        q = self._make_query(expected_value_min=10, expected_value_max=20)
        assert _check_validation_query(q, actual_rows=3, val_result=[(0,), (15,), (19,)]) is False

    def test_value_bounds_multi_row_middle_out_of_range_fails(self):
        from benchbox.core.write_primitives.benchmark import _check_validation_query

        q = self._make_query(expected_value_min=10, expected_value_max=20)
        assert _check_validation_query(q, actual_rows=3, val_result=[(11,), (999,), (19,)]) is False

    def test_value_bounds_multi_row_last_out_of_range_fails(self):
        from benchbox.core.write_primitives.benchmark import _check_validation_query

        q = self._make_query(expected_value_min=10, expected_value_max=20)
        assert _check_validation_query(q, actual_rows=3, val_result=[(11,), (15,), (0,)]) is False

    def test_value_bounds_empty_result_fails(self):
        from benchbox.core.write_primitives.benchmark import _check_validation_query

        q = self._make_query(expected_value_min=10, expected_value_max=20)
        assert _check_validation_query(q, actual_rows=0, val_result=None) is False

    def test_value_bounds_empty_row_fails(self):
        from benchbox.core.write_primitives.benchmark import _check_validation_query

        q = self._make_query(expected_value_min=10, expected_value_max=20)
        assert _check_validation_query(q, actual_rows=2, val_result=[(15,), ()]) is False


@pytest.fixture()
def fast_bench(tmp_path):
    with patch("benchbox.core.write_primitives.benchmark.WritePrimitivesDataGenerator"):
        bench = WritePrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
    return bench


@pytest.fixture()
def fast_conn():
    conn = MagicMock()
    conn.execute.return_value = MagicMock()
    conn.execute.return_value.fetchone.return_value = (100,)
    return conn


def test_generate_data_calls_generator_and_returns_list(fast_bench):
    fast_bench.data_generator.generate.return_value = {
        "orders": Path("/tmp/orders.parquet"),
        "lineitem": Path("/tmp/lineitem.parquet"),
    }
    result = fast_bench.generate_data()
    fast_bench.data_generator.generate.assert_called_once()
    assert len(result) == 2


def test_setup_success_creates_and_populates_tables(fast_bench, fast_conn):
    with (
        patch.object(fast_bench, "_acquire_setup_lock", return_value=True),
        patch.object(fast_bench, "_release_setup_lock"),
        patch.object(fast_bench, "_table_exists", return_value=False),
        patch.object(fast_bench, "_populate_staging_tables", return_value={}),
    ):
        result = fast_bench.setup(fast_conn)

    assert result["success"] is True
    assert "tables_created" in result


def test_setup_lock_timeout_raises_runtime_error(fast_bench, fast_conn):
    with patch.object(fast_bench, "_acquire_setup_lock", return_value=False):
        with pytest.raises(RuntimeError, match="Could not acquire setup lock"):
            fast_bench.setup(fast_conn)


def test_setup_force_drops_existing_tables(fast_bench, fast_conn):
    with (
        patch.object(fast_bench, "_acquire_setup_lock", return_value=True),
        patch.object(fast_bench, "_release_setup_lock"),
        patch.object(fast_bench, "_table_exists", return_value=True),
        patch.object(fast_bench, "_populate_staging_tables", return_value={}),
    ):
        result = fast_bench.setup(fast_conn, force=True)

    assert result["success"] is True
    drop_calls = [str(c) for c in fast_conn.execute.call_args_list if "DROP" in str(c)]
    assert len(drop_calls) > 0


def test_setup_missing_required_table_raises(fast_bench):
    conn = MagicMock()
    conn.execute.side_effect = RuntimeError("table not found")
    with pytest.raises(RuntimeError, match="Required TPC-H table"):
        fast_bench.setup(conn)


def test_setup_datafusion_dialect_skips_lock(fast_bench, fast_conn):
    with (
        patch.object(fast_bench, "_table_exists", return_value=True),
        patch.object(fast_bench, "_populate_staging_tables", return_value={}),
    ):
        result = fast_bench.setup(fast_conn, dialect="datafusion")

    assert result["success"] is True


def test_teardown_drops_all_tables(fast_bench, fast_conn):
    fast_bench.teardown(fast_conn)
    drop_calls = [str(c) for c in fast_conn.execute.call_args_list if "DROP" in str(c)]
    assert len(drop_calls) > 0


def test_teardown_ignores_drop_errors(fast_bench):
    conn = MagicMock()
    conn.execute.side_effect = Exception("table does not exist")
    fast_bench.teardown(conn)


def test_reset_staging_tables_truncates_and_repopulates(fast_bench, fast_conn):
    with patch.object(fast_bench, "_populate_staging_tables") as mock_pop:
        fast_bench.reset(fast_conn)
    mock_pop.assert_called_once()
    truncate_calls = [str(c) for c in fast_conn.execute.call_args_list if "TRUNCATE" in str(c)]
    assert len(truncate_calls) > 0


def test_execute_operation_success_path(fast_bench, fast_conn):
    with (
        patch.object(fast_bench, "is_setup", return_value=True),
        patch.object(fast_bench, "_get_effective_write_sql", return_value=("SELECT 1", None)),
        patch.object(fast_bench, "_replace_placeholders", side_effect=lambda s: s),
        patch.object(fast_bench, "_extract_rows_affected", return_value=3),
        patch.object(fast_bench, "_run_operation_validation", return_value=(True, [], 0.0)),
        patch.object(fast_bench, "_run_operation_cleanup", return_value=(True, None, 0.0)),
    ):
        result = fast_bench.execute_operation("insert_single_row", fast_conn)

    assert result.success is True
    assert result.status == "SUCCESS"
    assert result.rows_affected == 3


def test_execute_operation_skipped_when_skip_reason(fast_bench, fast_conn):
    with (
        patch.object(fast_bench, "is_setup", return_value=True),
        patch.object(fast_bench, "_get_effective_write_sql", return_value=(None, "platform not supported")),
    ):
        result = fast_bench.execute_operation("insert_single_row", fast_conn)

    assert result.success is True
    assert result.status == "SKIPPED"


def test_execute_operation_error_returns_failed(fast_bench, fast_conn):
    with (
        patch.object(fast_bench, "is_setup", return_value=True),
        patch.object(fast_bench, "_get_effective_write_sql", return_value=("SELECT 1", None)),
        patch.object(fast_bench, "_replace_placeholders", side_effect=RuntimeError("db error")),
    ):
        result = fast_bench.execute_operation("insert_single_row", fast_conn)

    assert result.success is False
    assert "db error" in (result.error or "")


def test_execute_operation_invalid_connection_raises(fast_bench):
    with pytest.raises(ValueError, match="Invalid connection type"):
        fast_bench.execute_operation("insert_single_row", object())


def test_execute_operation_none_connection_raises(fast_bench):
    with pytest.raises(ValueError, match="Connection is None"):
        fast_bench.execute_operation("insert_single_row", None)


def test_execute_operation_auto_setup_when_not_initialized(fast_bench, fast_conn):
    with (
        patch.object(fast_bench, "is_setup", return_value=False),
        patch.object(fast_bench, "setup") as mock_setup,
        patch.object(fast_bench, "_get_effective_write_sql", return_value=("SELECT 1", None)),
        patch.object(fast_bench, "_replace_placeholders", side_effect=lambda s: s),
        patch.object(fast_bench, "_extract_rows_affected", return_value=0),
        patch.object(fast_bench, "_run_operation_validation", return_value=(True, [], 0.0)),
        patch.object(fast_bench, "_run_operation_cleanup", return_value=(True, None, 0.0)),
    ):
        fast_bench.execute_operation("insert_single_row", fast_conn)

    mock_setup.assert_called_once()


def test_run_benchmark_by_operation_ids(fast_bench, fast_conn):
    mock_result = OperationResult(
        operation_id="insert_single_row",
        success=True,
        write_duration_ms=10.0,
        rows_affected=1,
        validation_duration_ms=5.0,
        validation_passed=True,
        validation_results=[],
        cleanup_duration_ms=0.0,
        cleanup_success=True,
    )
    with patch.object(fast_bench, "execute_operation", return_value=mock_result):
        results = fast_bench.run_benchmark(fast_conn, operation_ids=["insert_single_row"])

    assert len(results) == 1
    assert results[0].success is True


def test_run_benchmark_by_category(fast_bench, fast_conn):
    mock_result = OperationResult(
        operation_id="op1",
        success=True,
        write_duration_ms=1.0,
        rows_affected=0,
        validation_duration_ms=0.0,
        validation_passed=True,
        validation_results=[],
        cleanup_duration_ms=0.0,
        cleanup_success=True,
    )
    with patch.object(fast_bench, "execute_operation", return_value=mock_result):
        results = fast_bench.run_benchmark(fast_conn, categories=["insert"])

    assert len(results) > 0


def test_run_benchmark_all_operations(fast_bench, fast_conn):
    mock_result = OperationResult(
        operation_id="op1",
        success=False,
        write_duration_ms=0.0,
        rows_affected=0,
        validation_duration_ms=0.0,
        validation_passed=False,
        validation_results=[],
        cleanup_duration_ms=0.0,
        cleanup_success=False,
        error="test error",
    )
    with patch.object(fast_bench, "execute_operation", return_value=mock_result):
        results = fast_bench.run_benchmark(fast_conn)

    assert len(results) > 0


def test_populate_staging_tables_already_populated(fast_bench):
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = (500,)

    reset_map = {"update_ops_orders": "orders"}
    result = fast_bench._populate_staging_tables(conn, reset_map)

    assert result["update_ops_orders"] == 500


def test_populate_staging_tables_empty_populates(fast_bench):
    fetch_results = [
        (0,),
        (1000,),
        (1000,),
    ]
    conn = MagicMock()
    conn.execute.return_value.fetchone.side_effect = fetch_results

    with patch.object(fast_bench, "_get_population_sql", return_value="INSERT INTO ..."):
        result = fast_bench._populate_staging_tables(conn, {"update_ops_orders": "orders"})

    assert result["update_ops_orders"] == 1000


def test_populate_staging_tables_optional_missing_source(fast_bench):
    conn = MagicMock()

    def execute_side(sql):
        result = MagicMock()
        if "delete_ops_supplier" in sql and "COUNT" in sql:
            result.fetchone.return_value = (0,)
        elif "COUNT" in sql:
            raise RuntimeError("table not found")
        else:
            result.fetchone.return_value = (0,)
        return result

    conn.execute.side_effect = execute_side

    result = fast_bench._populate_staging_tables(conn, {"delete_ops_supplier": "supplier"})
    assert result["delete_ops_supplier"] == 0


def test_populate_staging_tables_required_empty_source_raises(fast_bench):
    fetch_results = [
        (0,),
        (0,),
    ]
    conn = MagicMock()
    conn.execute.return_value.fetchone.side_effect = fetch_results

    with pytest.raises(RuntimeError, match="Source table .* is empty"):
        fast_bench._populate_staging_tables(conn, {"update_ops_orders": "orders"})


def test_reset_truncate_exception_is_silenced(fast_bench):
    conn = MagicMock()
    conn.execute.side_effect = Exception("TRUNCATE not supported")

    with patch.object(fast_bench, "_populate_staging_tables"):
        fast_bench.reset(conn)


def test_is_setup_returns_true_when_all_tables_populated(fast_bench):
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = (100,)

    assert fast_bench.is_setup(conn) is True


def test_is_setup_returns_false_when_table_empty(fast_bench):
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = (0,)

    assert fast_bench.is_setup(conn) is False


def test_is_setup_returns_false_on_exception(fast_bench):
    conn = MagicMock()
    conn.execute.side_effect = Exception("table not found")

    assert fast_bench.is_setup(conn) is False


def test_replace_placeholders_substitutes_file_path(fast_bench, tmp_path):
    sql = "COPY INTO table FROM '{file_path}/data.parquet'"
    result = fast_bench._replace_placeholders(sql)
    assert "{file_path}" not in result
    assert "write_primitives_auxiliary" in result


def test_replace_placeholders_no_placeholder_unchanged(fast_bench):
    sql = "SELECT 1"
    result = fast_bench._replace_placeholders(sql)
    assert result == sql


def test_replace_placeholders_no_output_dir_uses_empty(fast_bench):
    fast_bench._output_dir = None
    sql = "COPY INTO t FROM '{file_path}/x'"
    result = fast_bench._replace_placeholders(sql)
    assert "{file_path}" not in result


def test_get_schema_returns_normalized_dict(fast_bench):
    schema = fast_bench.get_schema()
    assert isinstance(schema, dict)
    assert len(schema) > 0
    for table_name, table_def in schema.items():
        assert "columns" in table_def or isinstance(table_def, dict)


def test_get_schema_excludes_operation_created_sketch_tables(fast_bench):
    schema = fast_bench.get_schema()

    assert "sketch_ops_daily_users" not in schema
    assert "sketch_ops_topk" not in schema


def test_get_benchmark_info_returns_metadata(fast_bench):
    info = fast_bench.get_benchmark_info()
    assert info["name"] == "Write Primitives Benchmark"
    assert "scale_factor" in info


def test_get_query_returns_sql_string(fast_bench):
    result = fast_bench.get_query("insert_single_row")
    assert isinstance(result, str)
    assert len(result) > 0


def test_get_queries_returns_all(fast_bench):
    queries = fast_bench.get_queries()
    assert isinstance(queries, dict)
    assert len(queries) > 0


def test_default_sql_operations_exclude_sketch_category(fast_bench):
    operations = fast_bench.get_all_operations()

    assert "sketch_query_theta_union_merge" not in operations
    assert all(operation.category != "sketch" for operation in operations.values())


def test_get_query_allows_explicit_sql_sketch_operation(fast_bench):
    result = fast_bench.get_query("sketch_query_theta_union_merge")

    assert isinstance(result, str)
    assert "sketch_ops_daily_users" in result


def test_get_queries_by_category_returns_subset(fast_bench):
    queries = fast_bench.get_queries_by_category("insert")
    assert isinstance(queries, dict)
    assert len(queries) > 0


def test_execute_operation_auto_setup_failure_raises_runtime(fast_bench, fast_conn):
    with (
        patch.object(fast_bench, "is_setup", return_value=False),
        patch.object(fast_bench, "setup", side_effect=RuntimeError("setup failed")),
    ):
        with pytest.raises(RuntimeError, match="Failed to initialize staging tables"):
            fast_bench.execute_operation("insert_single_row", fast_conn)


def test_extract_rows_affected_from_rowcount(fast_bench):
    result = MagicMock()
    result.rowcount = 10
    assert fast_bench._extract_rows_affected(result, "test_op") == 10


def test_extract_rows_affected_no_rowcount_returns_minus_one(fast_bench):
    result = MagicMock(spec=[])
    assert fast_bench._extract_rows_affected(result, "test_op") == -1


def test_extract_rows_affected_minus_one_passthrough(fast_bench):
    result = MagicMock()
    result.rowcount = -1
    assert fast_bench._extract_rows_affected(result, "test_op") == -1


def test_run_operation_validation_passes(fast_bench, fast_conn):
    from benchbox.core.write_primitives.catalog.loader import ValidationQuery

    val_q = ValidationQuery(id="v1", sql="SELECT 1", expected_rows=1)
    operation = SimpleNamespace(validation_queries=[val_q])
    fast_conn.execute.return_value.fetchall.return_value = [("1",)]

    passed, results, duration_ms = fast_bench._run_operation_validation(operation, fast_conn, "test_op")

    assert passed is True
    assert len(results) == 1
    assert results[0]["query_id"] == "v1"


def test_run_operation_validation_fails_wrong_count(fast_bench, fast_conn):
    from benchbox.core.write_primitives.catalog.loader import ValidationQuery

    val_q = ValidationQuery(id="v1", sql="SELECT 1", expected_rows=5)
    operation = SimpleNamespace(validation_queries=[val_q])
    fast_conn.execute.return_value.fetchall.return_value = [("1",)]

    passed, results, _ = fast_bench._run_operation_validation(operation, fast_conn, "test_op")

    assert passed is False


def test_run_operation_validation_no_queries(fast_bench, fast_conn):
    operation = SimpleNamespace(validation_queries=[])

    passed, results, _ = fast_bench._run_operation_validation(operation, fast_conn, "test_op")

    assert passed is True
    assert results == []


def test_run_operation_cleanup_executes_sql(fast_bench, fast_conn):
    operation = SimpleNamespace(cleanup_sql="DELETE FROM t WHERE 1=0")

    success, warning, _ = fast_bench._run_operation_cleanup(operation, fast_conn, "test_op")

    assert success is True
    assert warning is None
    fast_conn.execute.assert_called_with("DELETE FROM t WHERE 1=0")


def test_run_operation_cleanup_no_sql(fast_bench, fast_conn):
    operation = SimpleNamespace(cleanup_sql=None)

    success, warning, _ = fast_bench._run_operation_cleanup(operation, fast_conn, "test_op")

    assert success is True
    assert warning is None


def test_run_operation_cleanup_error_sets_warning(fast_bench):
    conn = MagicMock()
    conn.execute.side_effect = Exception("cleanup failed")
    operation = SimpleNamespace(cleanup_sql="DELETE FROM t")

    success, warning, _ = fast_bench._run_operation_cleanup(operation, conn, "test_op")

    assert success is False
    assert warning is not None
    assert "cleanup failed" in warning


def test_get_effective_write_sql_uses_sql_override(fast_bench):
    operation = SimpleNamespace(write_sql="SELECT 1", platform_overrides={}, id="op1")
    sql, skip = fast_bench._get_effective_write_sql(operation, sql_override="OVERRIDE SQL")
    assert sql == "OVERRIDE SQL"
    assert skip is None


def test_get_effective_write_sql_platform_override_none_skips(fast_bench):
    operation = SimpleNamespace(write_sql="SELECT 1", platform_overrides={"duckdb": None}, id="op1")
    sql, skip = fast_bench._get_effective_write_sql(operation, platform_key="duckdb")
    assert sql is None
    assert skip is not None
    assert "unsupported" in skip.lower()


def test_get_effective_write_sql_platform_override_sql(fast_bench):
    operation = SimpleNamespace(write_sql="SELECT 1", platform_overrides={"duckdb": "DUCKDB SQL"}, id="op1")
    sql, skip = fast_bench._get_effective_write_sql(operation, platform_key="duckdb")
    assert sql == "DUCKDB SQL"
    assert skip is None


def test_get_effective_write_sql_no_overrides(fast_bench):
    operation = SimpleNamespace(write_sql="INSERT INTO t VALUES (1)", platform_overrides={}, id="op1")
    sql, skip = fast_bench._get_effective_write_sql(operation)
    assert sql == "INSERT INTO t VALUES (1)"
    assert skip is None


def test_get_effective_write_sql_duckdb_skips_merge_into(fast_bench):
    operation = SimpleNamespace(
        id="merge_simple_upsert_small",
        write_sql="MERGE INTO t AS target USING s ON t.k = s.k WHEN MATCHED THEN UPDATE SET v = s.v",
        platform_overrides={},
        category="merge",
    )
    sql, skip = fast_bench._get_effective_write_sql(operation, platform_key="duckdb")
    assert sql is None
    assert skip is not None
    assert "MERGE INTO" in skip


def test_get_effective_write_sql_duckdb_runs_portable_merge(fast_bench):
    operation = SimpleNamespace(
        id="merge_scd_type2_basic",
        write_sql="UPDATE dim SET is_current = false WHERE k IN (SELECT k FROM stage);\nINSERT INTO dim SELECT * FROM stage",
        platform_overrides={},
        category="merge",
    )
    sql, skip = fast_bench._get_effective_write_sql(operation, platform_key="duckdb")
    assert skip is None
    assert sql == operation.write_sql


class _FakeCountConnection:
    def __init__(self, counts_by_table: dict[str, int]):
        self._counts_by_table = counts_by_table

    def execute(self, sql: str):
        for table, count in self._counts_by_table.items():
            if table in sql:
                return SimpleNamespace(fetchone=lambda count=count: (count,))
        raise RuntimeError(f"unexpected query in fake connection: {sql}")


def test_get_effective_write_sql_skips_scd2_when_customer_staging_is_empty(fast_bench):
    operation = fast_bench.get_operation("merge_scd_type2_basic")
    empty_connection = _FakeCountConnection({"scd2_ops_dim_customer": 0, "scd2_ops_stage_customer": 0})

    sql, skip = fast_bench._get_effective_write_sql(operation, platform_key="duckdb", connection=empty_connection)

    assert sql is None
    assert skip is not None
    assert "empty" in skip.lower()
    assert "scd2_ops_dim_customer" in skip or "scd2_ops_stage_customer" in skip


def test_get_effective_write_sql_runs_scd2_when_customer_staging_is_populated(fast_bench):
    operation = fast_bench.get_operation("merge_scd_type2_basic")
    populated_connection = _FakeCountConnection({"scd2_ops_dim_customer": 40, "scd2_ops_stage_customer": 10})

    sql, skip = fast_bench._get_effective_write_sql(operation, platform_key="duckdb", connection=populated_connection)

    assert skip is None
    assert sql == operation.write_sql


def test_get_effective_write_sql_without_connection_does_not_check_staging_population(fast_bench):
    operation = fast_bench.get_operation("merge_scd_type2_basic")

    sql, skip = fast_bench._get_effective_write_sql(operation, platform_key="duckdb")

    assert skip is None
    assert sql == operation.write_sql


def test_get_effective_write_sql_empty_supplier_staging_skips_gdpr_delete(fast_bench):
    operation = SimpleNamespace(
        id="delete_gdpr_suppliers_5pct",
        write_sql="DELETE FROM delete_ops_supplier WHERE s_suppkey <= 5",
        platform_overrides={},
        category="delete",
    )
    empty_connection = _FakeCountConnection({"delete_ops_supplier": 0})

    sql, skip = fast_bench._get_effective_write_sql(operation, connection=empty_connection)

    assert sql is None
    assert skip is not None
    assert "delete_ops_supplier" in skip


def test_get_effective_write_sql_duckdb_gate_matches_catalog(fast_bench):
    portable_ops = (
        "merge_scd_type2_basic",
        "merge_scd_type2_no_change",
        "merge_scd_type2_new_keys_only",
    )
    for op_id in portable_ops:
        operation = fast_bench.get_operation(op_id)
        assert operation.category == "merge"
        assert "MERGE INTO" not in operation.write_sql.upper()
        _, skip = fast_bench._get_effective_write_sql(operation, platform_key="duckdb")
        assert skip is None, f"{op_id} should run on DuckDB, got skip: {skip}"

    legacy = fast_bench.get_operation("merge_simple_upsert_small")
    assert "MERGE INTO" in legacy.write_sql.upper()
    _, legacy_skip = fast_bench._get_effective_write_sql(legacy, platform_key="duckdb")
    assert legacy_skip is not None
    assert "MERGE INTO" in legacy_skip


def test_get_effective_write_sql_skips_when_file_dependencies_missing(fast_bench, tmp_path):
    fast_bench.data_generator.files_dir = tmp_path
    operation = SimpleNamespace(
        id="bulk_load_csv_small_uncompressed",
        write_sql="COPY bulk_load_ops_target FROM 'unused';",
        platform_overrides={},
        category="bulk_load",
        file_dependencies=["csv_small_1k.csv", "csv_missing.csv"],
    )

    sql, skip = fast_bench._get_effective_write_sql(operation)

    assert sql is None
    assert skip is not None
    assert "csv_small_1k.csv" in skip
    assert "csv_missing.csv" in skip


def test_get_population_sql_merge_ops_target(fast_bench):
    sql = fast_bench._get_population_sql("merge_ops_target", "orders")
    assert "0.5" in sql or "50%" in sql or "CAST" in sql
    assert "merge_ops_target" in sql or '"merge_ops_target"' in sql


def test_get_population_sql_merge_ops_source(fast_bench):
    sql = fast_bench._get_population_sql("merge_ops_source", "orders")
    assert "orders" in sql or '"orders"' in sql


def test_get_population_sql_merge_ops_lineitem_target(fast_bench):
    sql = fast_bench._get_population_sql("merge_ops_lineitem_target", "lineitem")
    assert "lineitem" in sql or '"lineitem"' in sql


def test_get_population_sql_ddl_truncate_target(fast_bench):
    sql = fast_bench._get_population_sql("ddl_truncate_target", "orders")
    assert "o_orderkey" in sql


def test_get_population_sql_default_full_copy(fast_bench):
    sql = fast_bench._get_population_sql("update_ops_orders", "orders")
    assert "SELECT *" in sql


def test_get_population_sql_scd2_uses_sqlite_date_functions(fast_bench):
    fast_bench._setup_dialect = "sqlite"

    dimension_sql = fast_bench._get_population_sql("scd2_ops_dim_customer", "customer")
    stage_sql = fast_bench._get_population_sql("scd2_ops_stage_customer", "customer")

    assert "DATE '" not in dimension_sql
    assert "DATE '" not in stage_sql
    assert "DATE('1990-01-01')" in dimension_sql
    assert "DATE('9999-12-31')" in dimension_sql
    assert "DATE('2026-01-01')" in stage_sql
    assert "DATE('2026-01-02')" in stage_sql
    assert "DATE('2026-01-03')" in stage_sql


def test_populate_scd2_staging_table_succeeds_on_sqlite(fast_bench):
    import sqlite3

    from benchbox.core.write_primitives.schema import get_create_table_sql

    fast_bench._setup_dialect = "sqlite"
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute(
            "CREATE TABLE customer (c_custkey INTEGER, c_name TEXT, c_address TEXT, c_acctbal REAL, c_mktsegment TEXT)"
        )
        connection.execute("INSERT INTO customer VALUES (1, 'Customer#1', 'Address', 100.0, 'BUILDING')")
        connection.execute(get_create_table_sql("scd2_ops_dim_customer", dialect="sqlite"))
        connection.execute(get_create_table_sql("scd2_ops_stage_customer", dialect="sqlite"))

        status = fast_bench._populate_staging_tables(
            connection,
            {
                "scd2_ops_dim_customer": "customer",
                "scd2_ops_stage_customer": "customer",
            },
        )

        assert status == {"scd2_ops_dim_customer": 1, "scd2_ops_stage_customer": 2}
        assert connection.execute("SELECT valid_from, valid_to FROM scd2_ops_dim_customer").fetchone() == (
            "1990-01-01",
            "9999-12-31",
        )
        assert connection.execute(
            "SELECT effective_ts, change_type FROM scd2_ops_stage_customer ORDER BY c_custkey"
        ).fetchall() == [("2026-01-01", "changed"), ("2026-01-03", "new")]
    finally:
        connection.close()


def test_get_create_tables_sql_returns_sql_string(fast_bench):
    sql = fast_bench.get_create_tables_sql()
    assert isinstance(sql, str)
    assert "CREATE TABLE" in sql.upper()
    assert "CREATE TABLE orders_stage" in sql
    assert "CREATE TABLE lineitem_stage" in sql


def test_ensure_auxiliary_data_files_bulk_files_exist(fast_bench):
    fast_bench.data_generator.check_bulk_load_files_exist.return_value = True
    fast_bench.ensure_auxiliary_data_files()
    fast_bench.data_generator.generate_bulk_load_files.assert_not_called()


def test_ensure_auxiliary_data_files_acquires_lock_and_generates(fast_bench):
    fast_bench.data_generator.check_bulk_load_files_exist.return_value = False
    fast_bench.data_generator._acquire_bulk_load_lock.return_value = True
    fast_bench.data_generator.generate_bulk_load_files.return_value = ["/tmp/f1.parquet"]

    fast_bench.ensure_auxiliary_data_files()

    fast_bench.data_generator.generate_bulk_load_files.assert_called_once()


def test_ensure_auxiliary_data_files_lock_timeout_silenced(fast_bench):
    fast_bench.data_generator.check_bulk_load_files_exist.return_value = False
    fast_bench.data_generator._acquire_bulk_load_lock.return_value = False

    fast_bench.ensure_auxiliary_data_files()


def test_ensure_auxiliary_data_files_already_generated_during_lock(fast_bench):
    call_count = [0]

    def check_side_effect():
        call_count[0] += 1
        return call_count[0] > 1

    fast_bench.data_generator.check_bulk_load_files_exist.side_effect = check_side_effect
    fast_bench.data_generator._acquire_bulk_load_lock.return_value = True
    fast_bench.data_generator._release_bulk_load_lock = MagicMock()

    fast_bench.ensure_auxiliary_data_files()


def test_cleanup_auxiliary_files_removes_directory(fast_bench, tmp_path):
    aux_dir = tmp_path / "write_primitives_auxiliary"
    aux_dir.mkdir()
    fast_bench.data_generator.files_dir = aux_dir

    fast_bench.cleanup_auxiliary_files()

    assert not aux_dir.exists()


def test_cleanup_auxiliary_files_missing_dir_is_noop(fast_bench, tmp_path):
    fast_bench.data_generator.files_dir = tmp_path / "nonexistent_dir"
    fast_bench.cleanup_auxiliary_files()


def test_replace_placeholders_unusual_chars_in_path(fast_bench, tmp_path):
    fast_bench._output_dir = Path("/tmp/test<path>")
    sql = "COPY INTO t FROM '{file_path}/data'"
    result = fast_bench._replace_placeholders(sql)
    assert "{file_path}" not in result


def test_execute_operation_none_sql_raises_via_exception_handler(fast_bench, fast_conn):
    with (
        patch.object(fast_bench, "is_setup", return_value=True),
        patch.object(fast_bench, "_get_effective_write_sql", return_value=(None, None)),
    ):
        result = fast_bench.execute_operation("insert_single_row", fast_conn)

    assert result.success is False


def test_populate_staging_tables_count_exception_treats_as_zero(fast_bench):
    call_count = [0]

    def execute_side(sql):
        result = MagicMock()
        call_count[0] += 1
        if call_count[0] == 1:
            raise Exception("table not found")
        elif call_count[0] == 2:
            result.fetchone.return_value = (1000,)
        else:
            result.fetchone.return_value = (1000,)
        return result

    conn = MagicMock()
    conn.execute.side_effect = execute_side

    with patch.object(fast_bench, "_get_population_sql", return_value="INSERT INTO ..."):
        result = fast_bench._populate_staging_tables(conn, {"update_ops_orders": "orders"})

    assert result.get("update_ops_orders", 0) >= 0


def test_population_sql_runs_one_statement_per_call_on_databricks(fast_bench):

    class _OneStatementConnection:
        def __init__(self):
            self.statements = []

        def execute(self, sql):
            if ";" in sql.strip().rstrip(";"):
                raise AssertionError(f"multi-statement batch sent: {sql!r}")
            self.statements.append(sql.strip())
            return None

    fast_bench._setup_dialect = "databricks"
    connection = _OneStatementConnection()
    sql = fast_bench._get_population_sql("scd2_ops_stage_customer", "customer")

    fast_bench._execute_population_sql(connection, sql)

    assert len(connection.statements) == 3
    assert all(stmt.upper().startswith("INSERT INTO") for stmt in connection.statements)
    assert all("AS STRING)" in stmt and "AS VARCHAR)" not in stmt for stmt in connection.statements)


def test_setup_force_replaces_tables_in_place_on_databricks(fast_bench, fast_conn):
    with (
        patch.object(fast_bench, "_acquire_setup_lock", return_value=True),
        patch.object(fast_bench, "_release_setup_lock"),
        patch.object(fast_bench, "_table_exists", return_value=True),
        patch.object(fast_bench, "_populate_staging_tables", return_value={}),
    ):
        result = fast_bench.setup(fast_conn, force=True, dialect="databricks")

    assert result["success"] is True
    executed = [str(c.args[0]) for c in fast_conn.execute.call_args_list if c.args]
    staging_drops = [s for s in executed if s.startswith("DROP TABLE") and "manifest" not in s.lower()]
    assert staging_drops == []
    assert any(s.startswith("CREATE OR REPLACE TABLE") for s in executed)
    manifest_clears = [s for s in executed if s.startswith("DELETE FROM") and "staging_manifest" in s]
    assert manifest_clears and executed.index(manifest_clears[0]) < next(
        i for i, s in enumerate(executed) if s.startswith("CREATE OR REPLACE TABLE")
    )
