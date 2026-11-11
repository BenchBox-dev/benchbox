# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ DS (TPC-DS) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-DS specification.

# Licensed under the MIT License. See LICENSE file in the project root for details.

import time
from pathlib import Path
from unittest.mock import patch

import pytest

from benchbox.core.tpcds.benchmark import TPCDSBenchmark

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
]


@pytest.mark.integration
@pytest.mark.tpcds
class TestTPCDSIntegrationMinimal:
    @pytest.fixture(scope="class")
    def tpcds_benchmark(self):
        return TPCDSBenchmark(scale_factor=1.0)

    @staticmethod
    def _assert_generated_files_exist(data_files: list[Path] | list[list[Path]]) -> None:
        for file_path_or_list in data_files:
            if isinstance(file_path_or_list, list):
                for file_path in file_path_or_list:
                    assert file_path.exists(), f"File {file_path} should exist"
                    assert file_path.stat().st_size > 0, f"File {file_path} should not be empty"
            else:
                file_path = Path(file_path_or_list)
                assert file_path.exists(), f"File {file_path} should exist"
                assert file_path.stat().st_size > 0, f"File {file_path} should not be empty"

    def test_end_to_end_workflow(self, tpcds_benchmark):

        info = tpcds_benchmark.get_benchmark_info()
        assert info["name"] == "TPC-DS"
        assert info["scale_factor"] == 1.0
        assert "c_tools_info" in info
        assert "available_tables" in info
        assert "available_queries" in info

        schema = tpcds_benchmark.get_schema()
        assert isinstance(schema, dict)
        assert len(schema) > 0

        tables = tpcds_benchmark.get_available_tables()
        assert len(tables) > 0

        available_queries = tpcds_benchmark.get_available_queries()
        assert len(available_queries) == 99

        queries = tpcds_benchmark.get_queries()
        assert len(queries) > 0
        assert isinstance(queries, dict)

        query1 = tpcds_benchmark.get_query(1)
        assert isinstance(query1, str)
        assert len(query1) > 0

    def test_c_tool_integration_workflow(self):

        benchmark = TPCDSBenchmark()

        assert benchmark.c_tools is not None

        c_tools_info = benchmark.c_tools.get_tools_info()
        assert isinstance(c_tools_info, dict)
        assert "tools_path" in c_tools_info
        assert "available_tools" in c_tools_info
        assert "templates_path" in c_tools_info

        tools = c_tools_info["available_tools"]
        dsdgen_exists = any(tool["name"] == "dsdgen" and tool["exists"] for tool in tools)
        assert dsdgen_exists

    def test_query_template_integration(self):

        benchmark = TPCDSBenchmark()

        queries = benchmark.get_queries()
        assert len(queries) > 0

        query1 = benchmark.get_query(1)
        query2 = benchmark.get_query(2)

        assert query1 != query2
        assert len(query1) > 0
        assert len(query2) > 0

        assert "select" in query1.lower() or "with" in query1.lower()
        assert "select" in query2.lower() or "with" in query2.lower()

    @pytest.mark.slow
    @pytest.mark.stress
    def test_data_generation_integration(self):
        benchmark = TPCDSBenchmark(scale_factor=1.0)
        data_files = benchmark.generate_data()
        assert len(data_files) > 0
        self._assert_generated_files_exist(data_files)

    def test_scale_factor_integration(self, tpcds_benchmark):

        assert tpcds_benchmark.scale_factor == 1.0
        info = tpcds_benchmark.get_benchmark_info()
        assert info["scale_factor"] == 1.0

    def test_error_handling_integration(self):

        benchmark = TPCDSBenchmark()

        with pytest.raises(ValueError):
            benchmark.get_query(999)

        with patch.object(benchmark, "generate_data") as mock_generate:
            mock_generate.return_value = ["/mock/table1.dat", "/mock/table2.dat"]
            data_files = benchmark.generate_data()
            assert len(data_files) > 0

        query1 = benchmark.get_query(1)
        assert isinstance(query1, str)
        assert len(query1) > 0

    def test_concurrent_access_integration(self):

        benchmark = TPCDSBenchmark()

        query1 = benchmark.get_query(1)
        query2 = benchmark.get_query(2)
        query3 = benchmark.get_query(3)

        assert isinstance(query1, str)
        assert isinstance(query2, str)
        assert isinstance(query3, str)

        assert query1 != query2
        assert query2 != query3
        assert query1 != query3

    def test_consistency_integration(self):

        benchmark = TPCDSBenchmark()

        query1_call1 = benchmark.get_query(1)
        query1_call2 = benchmark.get_query(1)
        assert query1_call1 == query1_call2

        tables1 = benchmark.get_available_tables()
        tables2 = benchmark.get_available_tables()
        assert tables1 == tables2

        queries1 = benchmark.get_available_queries()
        queries2 = benchmark.get_available_queries()
        assert queries1 == queries2

    def test_benchmark_independence_integration(self):
        benchmark1 = TPCDSBenchmark(scale_factor=1.0, seed=42)
        benchmark2 = TPCDSBenchmark(scale_factor=1.0, seed=123)

        assert benchmark1.scale_factor == benchmark2.scale_factor
        assert benchmark1.seed != benchmark2.seed

        query1_b1 = benchmark1.get_query(1, seed=42)
        query1_b2 = benchmark2.get_query(1, seed=123)

        assert isinstance(query1_b1, str)
        assert isinstance(query1_b2, str)
        assert len(query1_b1) > 0
        assert len(query1_b2) > 0

        info1 = benchmark1.get_benchmark_info()
        info2 = benchmark2.get_benchmark_info()

        assert info1 is not None
        assert info2 is not None
        assert isinstance(info1, dict)
        assert isinstance(info2, dict)

    def test_performance_integration(self):

        benchmark = TPCDSBenchmark()

        start_time = time.time()
        for i in range(1, 11):
            query = benchmark.get_query(i)
            assert len(query) > 0
        query_time = time.time() - start_time

        assert query_time < 5.0

        with patch.object(benchmark, "generate_data") as mock_generate:
            mock_generate.return_value = [f"/mock/table{i}.dat" for i in range(20)]
            start_time = time.time()
            data = benchmark.generate_data()
            data_time = time.time() - start_time

            assert data_time < 1.0
            assert len(data) > 0


@pytest.mark.integration
@pytest.mark.tpcds
class TestTPCDSWorkflowIntegration:
    def test_typical_benchmark_workflow(self):

        from unittest.mock import patch

        benchmark = TPCDSBenchmark(scale_factor=1.0)

        info = benchmark.get_benchmark_info()
        assert info["name"] == "TPC-DS"

        available_queries = benchmark.get_available_queries()
        assert len(available_queries) > 0

        test_queries = available_queries[:5]

        generated_queries = {}
        for query_id in test_queries:
            query = benchmark.get_query(query_id)
            generated_queries[query_id] = query
            assert len(query) > 0

        tables = benchmark.get_available_tables()
        test_tables = tables[:3]

        with patch.object(benchmark, "generate_data") as mock_generate:
            mock_generate.return_value = [f"/mock/table{i}.dat" for i in range(5)]
            generated_data = {}
            for table in test_tables:
                data = benchmark.generate_data()
                generated_data[table] = data
                assert len(data) > 0

        assert len(generated_queries) == len(test_queries)
        assert len(generated_data) == len(test_tables)

    def test_batch_query_generation_workflow(self):

        benchmark = TPCDSBenchmark()

        all_queries = benchmark.get_queries()
        assert len(all_queries) > 0

        for query_id, query in all_queries.items():
            assert isinstance(query_id, (int, str))
            if isinstance(query_id, str):
                assert query_id.isdigit()
                assert 1 <= int(query_id) <= 99
            else:
                assert 1 <= query_id <= 99
            assert isinstance(query, str)
            assert len(query) > 0

    def test_data_pipeline_workflow(self):

        from unittest.mock import patch

        benchmark = TPCDSBenchmark(scale_factor=1.0)

        tables = benchmark.get_available_tables()
        assert len(tables) > 0

        with patch.object(benchmark, "generate_data") as mock_generate:
            mock_generate.return_value = [f"/mock/table{i}.dat" for i in range(5)]
            data_pipeline = {}
            for table in tables[:5]:
                data = benchmark.generate_data()
                data_pipeline[table] = data
                assert len(data) > 0

        assert len(data_pipeline) == 5
        for table, data in data_pipeline.items():
            assert len(data) > 0
            assert all(isinstance(row, str) for row in data)

    def test_mixed_operations_workflow(self):

        from unittest.mock import patch

        benchmark = TPCDSBenchmark(scale_factor=1.0)

        operations = []

        query1 = benchmark.get_query(1)
        operations.append(("query", 1, len(query1)))

        with patch.object(benchmark, "generate_data") as mock_generate:
            mock_generate.return_value = [f"/mock/table{i}.dat" for i in range(5)]
            data = benchmark.generate_data()
            operations.append(("data", "customer", len(data)))

        query2 = benchmark.get_query(2)
        operations.append(("query", 2, len(query2)))

        info = benchmark.get_benchmark_info()
        operations.append(("info", "benchmark", len(info)))

        assert len(operations) == 4
        for _op_type, _op_target, op_result in operations:
            assert op_result > 0


