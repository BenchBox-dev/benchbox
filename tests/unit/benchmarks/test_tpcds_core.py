# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ DS (TPC-DS) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-DS specification.

# Licensed under the MIT License. See LICENSE file in the project root for details.

from pathlib import Path
from unittest.mock import patch

import pytest

from benchbox.core.tpcds.benchmark import TPCDSBenchmark
from benchbox.core.tpcds.c_tools import TPCDSError
from benchbox.tpcds import TPCDS

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.mark.tpcds
class TestTPCDSBenchmarkMinimal:
    def test_benchmark_initialization_basic(self) -> None:

        benchmark = TPCDSBenchmark(scale_factor=1.0)

        assert benchmark.c_tools is not None
        assert benchmark.query_manager is not None
        assert benchmark.data_generator is not None
        assert benchmark.scale_factor == 1.0

    def test_benchmark_initialization_with_params(self) -> None:

        benchmark = TPCDSBenchmark(scale_factor=1.0, custom_param="test")

        assert benchmark.scale_factor == 1.0
        assert benchmark.c_tools is not None

    @patch("benchbox.core.tpcds.queries.TPCDSQueryManager.get_all_queries")
    def test_get_queries_interface(self, mock_get_all) -> None:

        mock_queries = {
            1: "SELECT * FROM customer WHERE c_customer_id = 1;",
            2: "SELECT * FROM store_sales WHERE ss_sold_date_sk IS NOT NULL;",
            3: "SELECT * FROM item WHERE i_manufact_id = 1;",
        }
        mock_get_all.return_value = mock_queries

        benchmark = TPCDSBenchmark()
        queries = benchmark.get_queries()

        assert isinstance(queries, dict)
        assert len(queries) > 0

        for query_id in queries:
            assert isinstance(query_id, str)
            assert 1 <= int(query_id) <= 99

        for template in queries.values():
            assert isinstance(template, str)
            assert len(template) > 0

    @patch("benchbox.core.tpcds.c_tools.DSQGenBinary.generate")
    def test_get_query_interface(self, mock_generate) -> None:

        def mock_query_gen(query_id, **kwargs):
            return f"SELECT * FROM customer WHERE c_customer_id = {query_id};"

        mock_generate.side_effect = mock_query_gen

        benchmark = TPCDSBenchmark()

        query = benchmark.get_query(1)
        assert isinstance(query, str)
        assert len(query) > 0

        query2 = benchmark.get_query(2)
        assert isinstance(query2, str)
        assert len(query2) > 0

        assert query != query2

    def test_get_query_error_handling(self) -> None:

        benchmark = TPCDSBenchmark()

        with pytest.raises(ValueError):
            benchmark.get_query(0)

        with pytest.raises(ValueError):
            benchmark.get_query(100)

    @patch("benchbox.core.tpcds.benchmark.TPCDSBenchmark.generate_table_data")
    def test_generate_data_interface(self, mock_generate_table_data) -> None:

        mock_generate_table_data.return_value = iter(
            [
                "1|Customer One|123 Main St|...",
                "2|Customer Two|456 Oak Ave|...",
                "3|Customer Three|789 Pine Rd|...",
            ]
        )

        benchmark = TPCDSBenchmark(scale_factor=1.0)

        data_gen = benchmark.generate_table_data("customer")
        data = list(data_gen)

        assert len(data) > 0

        if data:
            first_row = data[0]
            assert isinstance(first_row, str)
            assert len(first_row) > 0

    def test_generate_data_error_handling(self) -> None:
        benchmark = TPCDSBenchmark(scale_factor=1.0)

        try:
            data_gen = benchmark.generate_table_data("customer")
            list(data_gen)
        except (TPCDSError, RuntimeError, AttributeError):
            pass

    def test_get_available_tables_interface(self) -> None:

        benchmark = TPCDSBenchmark()
        tables = benchmark.get_available_tables()

        assert isinstance(tables, list)
        assert len(tables) > 0

        expected_tables = ["customer", "store_sales", "item", "date_dim"]
        for table in expected_tables:
            assert table in tables

    def test_get_available_queries_interface(self) -> None:

        benchmark = TPCDSBenchmark()
        queries = benchmark.get_available_queries()

        assert isinstance(queries, list)
        assert len(queries) == 99

        for i in range(1, 100):
            assert i in queries

    def test_get_schema_interface(self) -> None:

        benchmark = TPCDSBenchmark()
        schema = benchmark.get_schema()

        assert isinstance(schema, dict)
        assert len(schema) > 0

        expected_tables = ["customer", "store_sales", "item"]
        for table in expected_tables:
            assert table in schema, f"Table {table} not found in schema"
            assert schema[table]["name"] == table

    def test_get_benchmark_info_interface(self) -> None:

        benchmark = TPCDSBenchmark(scale_factor=2.0)
        info = benchmark.get_benchmark_info()

        assert isinstance(info, dict)
        assert info["name"] == "TPC-DS"
        assert info["scale_factor"] == 2.0
        assert "available_tables" in info
        assert "available_queries" in info
        assert "c_tools_info" in info

        assert isinstance(info["available_tables"], list)
        assert isinstance(info["available_queries"], list)
        assert isinstance(info["c_tools_info"], dict)

    def test_benchmark_consistency(self) -> None:

        benchmark = TPCDSBenchmark()

        tables1 = benchmark.get_available_tables()
        tables2 = benchmark.get_available_tables()
        assert tables1 == tables2

        queries1 = benchmark.get_available_queries()
        queries2 = benchmark.get_available_queries()
        assert queries1 == queries2

        with patch("benchbox.core.tpcds.c_tools.DSQGenBinary.generate") as mock_gen:
            mock_gen.return_value = "SELECT * FROM customer WHERE c_customer_id = 1;"
            query1_call1 = benchmark.get_query(1)
            query1_call2 = benchmark.get_query(1)
            assert query1_call1 == query1_call2

    def test_benchmark_independence(self) -> None:

        benchmark1 = TPCDSBenchmark(scale_factor=1.0)
        benchmark2 = TPCDSBenchmark(scale_factor=2.0)

        assert benchmark1.scale_factor != benchmark2.scale_factor

        assert benchmark1.get_available_tables() == benchmark2.get_available_tables()
        assert benchmark1.get_available_queries() == benchmark2.get_available_queries()

        with patch("benchbox.core.tpcds.c_tools.DSQGenBinary.generate") as mock_gen:
            mock_gen.return_value = "SELECT * FROM customer WHERE c_customer_id = 1;"
            assert benchmark1.get_query(1) == benchmark2.get_query(1)


@pytest.mark.tpcds
class TestTPCDSInterfaceCompatibility:
    def test_tpcds_wrapper_initialization(self) -> None:

        tpcds = TPCDS()
        assert tpcds.scale_factor == 1.0
        assert isinstance(tpcds.output_dir, Path)

        custom_dir = Path("custom_dir")
        tpcds = TPCDS(scale_factor=1.0, output_dir=custom_dir, verbose=True, parallel=2)
        assert tpcds.scale_factor == 1.0
        assert tpcds.output_dir == custom_dir

    @patch("benchbox.core.tpcds.queries.TPCDSQueryManager.get_all_queries")
    def test_tpcds_wrapper_queries_interface(self, mock_get_all) -> None:

        mock_queries = {
            1: "SELECT * FROM customer WHERE c_customer_id = 1;",
            2: "SELECT * FROM store_sales WHERE ss_sold_date_sk IS NOT NULL;",
        }
        mock_get_all.return_value = mock_queries

        tpcds = TPCDS(scale_factor=1.0)

        assert hasattr(tpcds._impl, "query_manager")

        queries = tpcds.get_queries()
        assert isinstance(queries, dict)
        assert len(queries) > 0

    def test_tpcds_wrapper_data_generation(self) -> None:

        tpcds = TPCDS(scale_factor=1.0)

        assert hasattr(tpcds._impl, "data_generator")

        assert hasattr(tpcds._impl, "generate_table_data")
        assert callable(tpcds._impl.generate_table_data)

    def test_tpcds_wrapper_schema_access(self) -> None:

        tpcds = TPCDS()

        schema = tpcds.get_schema()
        assert isinstance(schema, dict)
        assert len(schema) > 0

    def test_tpcds_wrapper_benchmark_info(self) -> None:

        tpcds = TPCDS(scale_factor=1.0)

        info = tpcds.get_benchmark_info()
        assert isinstance(info, dict)
        assert info["name"] == "TPC-DS"
        assert info["scale_factor"] == 1.0


@pytest.mark.tpcds
class TestTPCDSErrorHandling:
    def test_c_tools_initialization_error(self) -> None:

        with patch("benchbox.core.tpcds.c_tools.DSQGenBinary._find_dsqgen_or_fail") as mock_find:
            mock_find.side_effect = RuntimeError("dsqgen binary not found")

            with pytest.raises(RuntimeError):
                TPCDSBenchmark()

    def test_query_error_handling(self) -> None:

        benchmark = TPCDSBenchmark()

        with pytest.raises(ValueError):
            benchmark.get_query(0)

        with pytest.raises(ValueError):
            benchmark.get_query(999)

    def test_data_generation_error_handling(self) -> None:

        benchmark = TPCDSBenchmark()

        assert hasattr(benchmark, "generate_table_data")
        assert callable(benchmark.generate_table_data)

    @patch("benchbox.core.tpcds.queries.TPCDSQueryManager.get_all_queries")
    def test_graceful_degradation(self, mock_get_all) -> None:

        mock_get_all.return_value = {
            1: "SELECT * FROM customer WHERE c_customer_id = 1;",
            2: "SELECT * FROM store_sales WHERE ss_sold_date_sk IS NOT NULL;",
        }

        benchmark = TPCDSBenchmark()

        queries = benchmark.get_queries()
        assert len(queries) > 0

        tables = benchmark.get_available_tables()
        assert len(tables) > 0


@pytest.mark.tpcds
class TestTPCDSIntegration:
    @patch("benchbox.core.tpcds.queries.TPCDSQueryManager.get_all_queries")
    def test_end_to_end_workflow(self, mock_get_all) -> None:

        mock_get_all.return_value = {
            1: "SELECT * FROM customer WHERE c_customer_id = 1;",
            2: "SELECT * FROM store_sales WHERE ss_sold_date_sk IS NOT NULL;",
        }

        benchmark = TPCDSBenchmark(scale_factor=1.0)

        queries = benchmark.get_queries()
        assert len(queries) > 0

        with patch("benchbox.core.tpcds.c_tools.DSQGenBinary.generate") as mock_gen:
            mock_gen.return_value = "SELECT * FROM customer WHERE c_customer_id = 1;"
            query1 = benchmark.get_query(1)
            assert isinstance(query1, str)
            assert len(query1) > 0

        assert hasattr(benchmark, "generate_table_data")
        assert callable(benchmark.generate_table_data)

        info = benchmark.get_benchmark_info()
        assert info["name"] == "TPC-DS"
        assert "c_tools_info" in info

    def test_c_tools_integration(self) -> None:

        benchmark = TPCDSBenchmark()

        assert benchmark.c_tools is not None

        info = benchmark.c_tools.get_tools_info()
        assert isinstance(info, dict)
        assert "tools_path" in info

        tables = benchmark.c_tools.get_available_tables()
        assert len(tables) > 0

    def test_queries_c_tools_integration(self) -> None:

        benchmark = TPCDSBenchmark()

        assert benchmark.query_manager.dsqgen is not None

        available = benchmark.get_available_queries()
        assert len(available) == 99

        with patch("benchbox.core.tpcds.c_tools.DSQGenBinary.generate") as mock_gen:
            mock_gen.return_value = "SELECT * FROM customer WHERE c_customer_id = 1;"
            query = benchmark.get_query(1)
            assert isinstance(query, str)
            assert len(query) > 0

    def test_generator_c_tools_integration(self) -> None:

        benchmark = TPCDSBenchmark()

        assert benchmark.data_generator is not None

        tables = benchmark.get_available_tables()
        assert len(tables) > 0

        assert hasattr(benchmark, "generate_table_data")
        assert callable(benchmark.generate_table_data)


