# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox import WritePrimitives
from benchbox.core.write_primitives.benchmark import OperationResult

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


@pytest.mark.integration
@pytest.mark.write_primitives
class TestWritePrimitivesBasic:
    def test_benchmark_initialization(self):

        bench = WritePrimitives(scale_factor=0.01, quiet=True)
        assert isinstance(bench, WritePrimitives)
        assert bench.scale_factor == 0.01

    def test_get_benchmark_info(self):

        bench = WritePrimitives(scale_factor=0.01, quiet=True)
        info = bench.get_benchmark_info()

        assert info["name"] == "Write Primitives Benchmark"
        assert info["version"] == "2.0"
        assert info["total_operations"] == 112
        assert "insert" in info["categories"]
        assert "update" in info["categories"]
        assert "delete" in info["categories"]
        assert "ddl" in info["categories"]

        assert "transaction" not in info["categories"]

    def test_get_operations(self):

        bench = WritePrimitives(scale_factor=0.01, quiet=True)

        all_ops = bench.get_all_operations()
        assert len(all_ops) == 112

        insert_ops = bench.get_operations_by_category("insert")
        assert len(insert_ops) == 12

        op = bench.get_operation("insert_single_row")
        assert op.id == "insert_single_row"
        assert op.category == "insert"

    def test_get_schema(self):

        bench = WritePrimitives(scale_factor=0.01, quiet=True)
        schema = bench.get_schema()

        assert "update_ops_orders" in schema
        assert "delete_ops_lineitem" in schema
        assert "write_ops_log" in schema

    def test_get_create_tables_sql(self):

        bench = WritePrimitives(scale_factor=0.01, quiet=True)
        sql = bench.get_create_tables_sql()

        assert "CREATE TABLE update_ops_orders" in sql
        assert "CREATE TABLE delete_ops_lineitem" in sql
        assert "CREATE TABLE write_ops_log" in sql

    def test_operation_result_structure(self):

        result = OperationResult(
            operation_id="test_op",
            success=True,
            write_duration_ms=10.5,
            rows_affected=1,
            validation_duration_ms=2.3,
            validation_passed=True,
            validation_results=[],
            cleanup_duration_ms=1.2,
            cleanup_success=True,
        )

        assert result.operation_id == "test_op"
        assert result.success is True
        assert result.write_duration_ms == 10.5
        assert result.error is None

    def test_data_source_sharing(self):

        bench = WritePrimitives(scale_factor=0.01, quiet=True)
        assert bench._impl.get_data_source_benchmark() == "tpch"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


