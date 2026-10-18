# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.core.write_primitives import WritePrimitivesBenchmark
from benchbox.core.write_primitives.catalog import (
    ValidationQuery,
    WriteOperation,
    load_write_primitives_catalog,
)
from benchbox.core.write_primitives.operations import WriteOperationsManager
from benchbox.core.write_primitives.schema import (
    STAGING_TABLES,
    get_all_staging_tables_sql,
    get_create_table_sql,
    get_table_schema,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


class TestWritePrimitivesSchema:
    def test_staging_tables_defined(self):
        required_tables = [
            "insert_ops_lineitem",
            "insert_ops_orders",
            "update_ops_orders",
            "delete_ops_lineitem",
            "merge_ops_target",
            "bulk_load_ops_target",
            "write_ops_log",
            "batch_metadata",
        ]

        for table_name in required_tables:
            assert table_name in STAGING_TABLES, f"Missing staging table: {table_name}"

    def test_get_table_schema(self):

        schema = get_table_schema("insert_ops_lineitem")
        assert schema is not None
        assert schema["name"] == "insert_ops_lineitem"
        assert "columns" in schema
        assert len(schema["columns"]) > 0

    def test_get_table_schema_invalid(self):

        with pytest.raises(ValueError, match="Unknown table"):
            get_table_schema("nonexistent_table")

    def test_get_create_table_sql(self):

        sql = get_create_table_sql("insert_ops_lineitem")
        assert "CREATE TABLE insert_ops_lineitem" in sql
        assert "l_orderkey" in sql
        assert "l_quantity" in sql

    def test_get_create_table_sql_datafusion_omits_primary_key_constraints(self):
        sql = get_create_table_sql("insert_ops_lineitem", dialect="datafusion")
        assert "CREATE TABLE insert_ops_lineitem" in sql
        assert "PRIMARY KEY" not in sql

    def test_get_create_table_sql_invalid(self):

        with pytest.raises(ValueError, match="Unknown staging table"):
            get_create_table_sql("invalid_table")

    def test_get_all_staging_tables_sql(self):

        sql = get_all_staging_tables_sql()
        assert "CREATE TABLE" in sql
        assert "insert_ops_lineitem" in sql
        assert "write_ops_log" in sql

    def test_benchmark_get_schema_includes_tpch_base_tables(self, tmp_path):
        benchmark = WritePrimitivesBenchmark(output_dir=tmp_path)
        schema = benchmark.get_schema()
        assert "orders" in schema
        assert "lineitem" in schema
        assert schema["orders"]["columns"][0]["name"] == "o_orderkey"


class TestWritePrimitivesCatalog:
    def test_load_catalog(self):

        catalog = load_write_primitives_catalog()
        assert catalog is not None
        assert catalog.version == 2
        assert len(catalog.operations) > 0

    def test_catalog_operations_have_required_fields(self):
        catalog = load_write_primitives_catalog()

        for op_id, operation in catalog.operations.items():
            assert isinstance(operation, WriteOperation)
            assert operation.id == op_id
            assert len(operation.category) > 0
            assert len(operation.description) > 0
            if operation.aggregate_state is None:
                assert len(operation.write_sql) > 0, f"SQL op '{op_id}' missing write_sql"

    def test_catalog_validation_queries(self):

        catalog = load_write_primitives_catalog()

        for operation in catalog.operations.values():
            for val_query in operation.validation_queries:
                assert isinstance(val_query, ValidationQuery)
                assert len(val_query.id) > 0
                assert len(val_query.sql) > 0

    def test_catalog_categories(self):

        catalog = load_write_primitives_catalog()

        categories = set()
        for operation in catalog.operations.values():
            categories.add(operation.category)

        expected_categories = {"insert", "update", "delete", "bulk_load", "merge", "ddl", "sketch"}
        assert categories == expected_categories

    def test_catalog_operation_count(self):

        catalog = load_write_primitives_catalog()
        assert len(catalog.operations) > 100, f"Expected >100 operations, got {len(catalog.operations)}"

    def test_batch_insert_key_ranges_match_cleanup_ranges(self):
        catalog = load_write_primitives_catalog()

        batch_100 = catalog.operations["insert_batch_values_100"]
        assert "generate_series(0, 99)" in batch_100.write_sql
        assert "BETWEEN 9000100 AND 9000199" in (batch_100.cleanup_sql or "")

        batch_1000 = catalog.operations["insert_batch_values_1000"]
        assert "generate_series(0, 999)" in batch_1000.write_sql
        assert "BETWEEN 9001000 AND 9001999" in (batch_1000.cleanup_sql or "")

    def test_all_operations_have_validation_or_cleanup(self):

        catalog = load_write_primitives_catalog()

        for op_id, operation in catalog.operations.items():
            has_validation = len(operation.validation_queries) > 0
            has_cleanup = operation.cleanup_sql is not None and len(operation.cleanup_sql.strip()) > 0

            assert has_validation or has_cleanup, f"Operation '{op_id}' has neither validation queries nor cleanup SQL"

    def test_bulk_load_operations_have_file_dependencies(self):

        catalog = load_write_primitives_catalog()

        for op_id, operation in catalog.operations.items():
            if operation.category == "bulk_load":
                if "special" not in op_id.lower() and "parallel" not in op_id.lower():
                    assert len(operation.file_dependencies) > 0, (
                        f"Bulk load operation '{op_id}' has no file dependencies"
                    )


class TestWriteOperationsManager:
    def test_manager_initialization(self):

        manager = WriteOperationsManager()
        assert manager.catalog_version == 2
        assert manager.get_operation_count() > 0

    def test_get_operation(self):

        manager = WriteOperationsManager()
        operation = manager.get_operation("insert_single_row")

        assert operation is not None
        assert operation.id == "insert_single_row"
        assert operation.category == "insert"
        assert len(operation.description) > 0

    def test_get_operation_invalid(self):

        manager = WriteOperationsManager()

        with pytest.raises(ValueError, match="Invalid operation ID"):
            manager.get_operation("nonexistent_operation")

    def test_get_all_operations(self):

        manager = WriteOperationsManager()
        operations = manager.get_all_operations()

        assert len(operations) > 0
        assert "insert_single_row" in operations

    def test_get_operations_by_category(self):

        manager = WriteOperationsManager()
        insert_ops = manager.get_operations_by_category("insert")

        assert len(insert_ops) > 0
        for operation in insert_ops.values():
            assert operation.category == "insert"

    def test_get_operation_categories(self):

        manager = WriteOperationsManager()
        categories = manager.get_operation_categories()

        assert len(categories) > 0
        assert "insert" in categories
        assert isinstance(categories, list)
        assert categories == sorted(categories)

    def test_get_category_count(self):

        manager = WriteOperationsManager()
        insert_count = manager.get_category_count("insert")

        assert insert_count > 0
        assert isinstance(insert_count, int)


class TestWritePrimitivesBenchmark:
    def test_benchmark_initialization(self):

        benchmark = WritePrimitivesBenchmark(scale_factor=1.0)

        assert benchmark.scale_factor == 1.0
        assert benchmark._name == "Write Primitives Benchmark"
        assert benchmark._version == "2.0"

    def test_get_data_source_benchmark(self):

        benchmark = WritePrimitivesBenchmark(scale_factor=1.0)
        assert benchmark.get_data_source_benchmark() == "tpch"

    def test_get_operation(self):

        benchmark = WritePrimitivesBenchmark(scale_factor=1.0)
        operation = benchmark.get_operation("insert_single_row")

        assert operation is not None
        assert operation.id == "insert_single_row"

    def test_get_all_operations(self):

        benchmark = WritePrimitivesBenchmark(scale_factor=1.0)
        operations = benchmark.get_all_operations()

        assert len(operations) > 0

    def test_get_operations_by_category(self):

        benchmark = WritePrimitivesBenchmark(scale_factor=1.0)
        insert_ops = benchmark.get_operations_by_category("insert")

        assert len(insert_ops) > 0

    def test_get_operation_categories(self):

        benchmark = WritePrimitivesBenchmark(scale_factor=1.0)
        categories = benchmark.get_operation_categories()

        assert len(categories) > 0
        assert "insert" in categories

    def test_get_schema(self):

        benchmark = WritePrimitivesBenchmark(scale_factor=1.0)
        schema = benchmark.get_schema()

        assert len(schema) > 0
        assert "insert_ops_lineitem" in schema

    def test_get_create_tables_sql(self):

        benchmark = WritePrimitivesBenchmark(scale_factor=1.0)
        sql = benchmark.get_create_tables_sql()

        assert "CREATE TABLE" in sql
        assert len(sql) > 0

    def test_get_benchmark_info(self):

        benchmark = WritePrimitivesBenchmark(scale_factor=1.0)
        info = benchmark.get_benchmark_info()

        assert info["name"] == "Write Primitives Benchmark"
        assert info["version"] == "2.0"
        assert info["scale_factor"] == 1.0
        assert info["total_operations"] > 0
        assert len(info["categories"]) > 0
        assert info["data_source"] == "tpch"


class TestConsolidatedOperations:
    def test_gdpr_deletion_1pct_exists_in_delete_category(self):
        manager = WriteOperationsManager()
        operation = manager.get_operation("delete_gdpr_suppliers_1pct")

        assert operation is not None
        assert operation.id == "delete_gdpr_suppliers_1pct"
        assert operation.category == "delete", "Should be categorized as DELETE, not MERGE"
        assert "GDPR" in operation.description
        assert "1%" in operation.description

    def test_gdpr_deletion_5pct_exists_in_delete_category(self):
        manager = WriteOperationsManager()
        operation = manager.get_operation("delete_gdpr_suppliers_5pct")

        assert operation is not None
        assert operation.id == "delete_gdpr_suppliers_5pct"
        assert operation.category == "delete", "Should be categorized as DELETE, not MERGE"
        assert "GDPR" in operation.description
        assert "5%" in operation.description

    def test_gdpr_deletions_use_delete_statements(self):

        manager = WriteOperationsManager()

        op_1pct = manager.get_operation("delete_gdpr_suppliers_1pct")
        op_5pct = manager.get_operation("delete_gdpr_suppliers_5pct")

        assert "DELETE FROM" in op_1pct.write_sql.upper()
        assert "MERGE" not in op_1pct.write_sql.upper()

        assert "DELETE FROM" in op_5pct.write_sql.upper()
        assert "MERGE" not in op_5pct.write_sql.upper()

    def test_gdpr_deletions_are_data_dependent(self):

        manager = WriteOperationsManager()

        op_1pct = manager.get_operation("delete_gdpr_suppliers_1pct")
        op_5pct = manager.get_operation("delete_gdpr_suppliers_5pct")

        assert op_1pct.expected_rows_affected is None, "1% GDPR deletion should be data-dependent"
        assert op_5pct.expected_rows_affected is None, "5% GDPR deletion should be data-dependent"

    def test_gdpr_deletions_have_strengthened_validation(self):

        manager = WriteOperationsManager()

        op_1pct = manager.get_operation("delete_gdpr_suppliers_1pct")
        op_5pct = manager.get_operation("delete_gdpr_suppliers_5pct")

        assert len(op_1pct.validation_queries) > 0
        assert len(op_5pct.validation_queries) > 0

        for val_query in op_1pct.validation_queries:
            assert "CASE" in val_query.sql.upper(), "Validation should use CASE expression"

        for val_query in op_5pct.validation_queries:
            assert "CASE" in val_query.sql.upper(), "Validation should use CASE expression"

    def test_etl_aggregation_exists_in_merge_category(self):

        manager = WriteOperationsManager()
        operation = manager.get_operation("merge_etl_aggregation_pattern")

        assert operation is not None
        assert operation.id == "merge_etl_aggregation_pattern"
        assert operation.category == "merge"
        assert "ETL" in operation.description or "aggregation" in operation.description.lower()

    def test_etl_aggregation_uses_merge_statement(self):

        manager = WriteOperationsManager()
        operation = manager.get_operation("merge_etl_aggregation_pattern")

        assert "MERGE INTO" in operation.write_sql.upper() or "MERGE" in operation.write_sql.upper()
        assert "WHEN MATCHED" in operation.write_sql.upper()

    def test_etl_aggregation_has_multiple_validations(self):

        manager = WriteOperationsManager()
        operation = manager.get_operation("merge_etl_aggregation_pattern")

        assert len(operation.validation_queries) >= 2, "Should have at least 2 validation queries"

        has_case_validation = any("CASE" in vq.sql.upper() for vq in operation.validation_queries)
        assert has_case_validation, "Should have CASE expression in at least one validation query"

    def test_etl_aggregation_is_data_dependent(self):

        manager = WriteOperationsManager()
        operation = manager.get_operation("merge_etl_aggregation_pattern")

        assert operation.expected_rows_affected is None, "ETL aggregation should be data-dependent"

    def test_deduplication_exists_in_merge_category(self):

        manager = WriteOperationsManager()
        operation = manager.get_operation("merge_deduplication_window_function")

        assert operation is not None
        assert operation.id == "merge_deduplication_window_function"
        assert operation.category == "merge"
        assert "deduplication" in operation.description.lower() or "window" in operation.description.lower()

    def test_deduplication_uses_window_function(self):

        manager = WriteOperationsManager()
        operation = manager.get_operation("merge_deduplication_window_function")

        assert "ROW_NUMBER()" in operation.write_sql.upper()
        assert "MERGE INTO" in operation.write_sql.upper() or "MERGE" in operation.write_sql.upper()

    def test_deduplication_has_explicit_column_list(self):

        manager = WriteOperationsManager()
        operation = manager.get_operation("merge_deduplication_window_function")

        assert "INSERT VALUES" in operation.write_sql, "Should have INSERT VALUES clause"
        assert "source.o_orderkey" in operation.write_sql, "Should specify source column names explicitly"

    def test_deduplication_has_multiple_validations(self):

        manager = WriteOperationsManager()
        operation = manager.get_operation("merge_deduplication_window_function")

        assert len(operation.validation_queries) >= 2, "Should have at least 2 validation queries"

        val_ids = [vq.id for vq in operation.validation_queries]
        assert "verify_deduplication" in val_ids
        assert "verify_no_duplicates" in val_ids

    def test_deduplication_validates_no_duplicates(self):

        manager = WriteOperationsManager()
        operation = manager.get_operation("merge_deduplication_window_function")

        verify_no_dups = None
        for vq in operation.validation_queries:
            if vq.id == "verify_no_duplicates":
                verify_no_dups = vq
                break

        assert verify_no_dups is not None, "Should have verify_no_duplicates validation query"
        assert "CASE" in verify_no_dups.sql.upper()
        assert "MAX(" in verify_no_dups.sql.upper() or "COUNT(*)" in verify_no_dups.sql.upper()

    def test_deduplication_is_data_dependent(self):

        manager = WriteOperationsManager()
        operation = manager.get_operation("merge_deduplication_window_function")

        assert operation.expected_rows_affected is None, "Deduplication should be data-dependent"

    def test_consolidated_operations_not_in_wrong_categories(self):

        manager = WriteOperationsManager()

        merge_ops = manager.get_operations_by_category("merge")
        merge_op_ids = list(merge_ops.keys())

        assert "delete_gdpr_suppliers_1pct" not in merge_op_ids
        assert "delete_gdpr_suppliers_5pct" not in merge_op_ids

        assert "merge_etl_aggregation_pattern" in merge_op_ids
        assert "merge_deduplication_window_function" in merge_op_ids

    def test_all_consolidated_operations_have_cleanup(self):

        manager = WriteOperationsManager()

        op_ids = [
            "delete_gdpr_suppliers_1pct",
            "delete_gdpr_suppliers_5pct",
            "merge_etl_aggregation_pattern",
            "merge_deduplication_window_function",
        ]

        for op_id in op_ids:
            operation = manager.get_operation(op_id)
            assert operation.cleanup_sql is not None, f"{op_id} should have cleanup SQL"
            assert len(operation.cleanup_sql.strip()) > 0, f"{op_id} cleanup SQL should not be empty"


class TestSCD2Operations:
    SCD2_OP_IDS = (
        "merge_scd_type2_basic",
        "merge_scd_type2_no_change",
        "merge_scd_type2_new_keys_only",
    )

    PREEXISTING_MERGE_OP_IDS = (
        "merge_conditional_update",
        "merge_conditional_insert",
        "merge_etl_aggregation_pattern",
        "merge_deduplication_window_function",
    )

    def test_scd2_ops_registered_in_merge_category(self):
        catalog = load_write_primitives_catalog()
        for op_id in self.SCD2_OP_IDS:
            assert op_id in catalog.operations, f"missing SCD2 op {op_id}"
            assert catalog.operations[op_id].category == "merge"

    def test_scd2_ops_exposed_via_get_all_operations(self):
        bench = WritePrimitivesBenchmark()
        ops = bench.get_all_operations()
        for op_id in self.SCD2_OP_IDS:
            assert op_id in ops

    def test_merge_category_count_increased_by_three(self):
        bench = WritePrimitivesBenchmark()
        merge_ops = bench.get_operations_by_category("merge")
        assert len(merge_ops) == 23
        for op_id in self.PREEXISTING_MERGE_OP_IDS:
            assert op_id in merge_ops

    def test_user_facing_operation_count_is_112(self):
        bench = WritePrimitivesBenchmark()
        assert len(bench.get_all_operations()) == 112

    def test_raw_catalog_count_is_136(self):
        catalog = load_write_primitives_catalog()
        assert len(catalog.operations) == 136

    def test_scd2_staging_tables_defined(self):
        assert "scd2_ops_dim_customer" in STAGING_TABLES
        assert "scd2_ops_stage_customer" in STAGING_TABLES

        dim_cols = {c["name"] for c in get_table_schema("scd2_ops_dim_customer")["columns"]}
        for col in ("sk", "c_custkey", "row_hash", "is_current", "valid_from", "valid_to"):
            assert col in dim_cols, f"dimension missing versioning column {col}"

        stage_cols = {c["name"] for c in get_table_schema("scd2_ops_stage_customer")["columns"]}
        for col in ("c_custkey", "row_hash", "effective_ts", "change_type"):
            assert col in stage_cols, f"staging missing column {col}"

    def test_scd2_ops_do_not_touch_merge_ops_target(self):
        catalog = load_write_primitives_catalog()
        for op_id in self.SCD2_OP_IDS:
            op = catalog.operations[op_id]
            combined = (op.write_sql or "") + (op.cleanup_sql or "")
            assert "merge_ops_target" not in combined
            assert "scd2_ops_dim_customer" in op.write_sql

    def test_scd2_ops_have_validation_and_cleanup(self):
        catalog = load_write_primitives_catalog()
        for op_id in self.SCD2_OP_IDS:
            op = catalog.operations[op_id]
            assert len(op.validation_queries) >= 1
            val_ids = {v.id for v in op.validation_queries}
            assert "at_most_one_current_per_business_key" in val_ids
            assert op.cleanup_sql is not None and op.cleanup_sql.strip()

    def test_scd2_ops_are_scale_independent(self):
        catalog = load_write_primitives_catalog()
        basic = catalog.operations["merge_scd_type2_basic"]
        assert "MAX(sk)" in basic.write_sql
        assert "ROW_NUMBER()" in basic.write_sql

    def test_scd2_insert_projection_shared_across_ops(self):
        catalog = load_write_primitives_catalog()
        canonical_projection = (
            "SELECT (SELECT MAX(sk) FROM scd2_ops_dim_customer) + ROW_NUMBER() OVER (ORDER BY s.c_custkey),\n"
            "       s.c_custkey, s.c_name, s.c_address, s.c_acctbal, s.c_mktsegment, s.row_hash,\n"
            "       true, s.effective_ts, DATE '9999-12-31'\n"
        )
        for op_id in ("merge_scd_type2_basic", "merge_scd_type2_no_change"):
            assert canonical_projection in catalog.operations[op_id].write_sql, (
                f"{op_id} INSERT projection drifted from the shared SCD2 form"
            )
        new_keys_only = catalog.operations["merge_scd_type2_new_keys_only"].write_sql
        assert canonical_projection in new_keys_only, (
            "new_keys_only must preserve the staged effective timestamp and shared insert projection"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
