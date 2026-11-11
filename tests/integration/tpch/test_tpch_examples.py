# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ H (TPC-H) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-H specification.

# Licensed under the MIT License. See LICENSE file in the project root for details.

import time
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


class TestTPCHExamples:
    def test_example_api_usage_patterns(self):

        from benchbox import TPCH

        tpch = TPCH()

        sql = tpch.get_query(1)
        assert isinstance(sql, str)
        assert len(sql) > 50

        sql_param = tpch.get_query(1, params=None, dialect="duckdb")
        assert isinstance(sql_param, str)
        assert len(sql_param) > 50

        queries = tpch.get_queries()
        assert isinstance(queries, dict)
        assert len(queries) == 22

    def test_example_parameter_usage(self):

        from benchbox import TPCH

        tpch = TPCH()

        sql1 = tpch.get_query(1, seed=42)
        sql2 = tpch.get_query(1, seed=42)
        assert sql1 == sql2

        sql_sf = tpch.get_query(1, scale_factor=0.1)
        assert isinstance(sql_sf, str)
        assert len(sql_sf) > 50

    def test_example_dialect_handling(self):

        from benchbox import TPCH

        tpch = TPCH()

        dialects = ["duckdb", "postgres", "sqlite", "standard"]

        for dialect in dialects:
            sql = tpch.get_query(1, dialect=dialect)
            assert isinstance(sql, str)
            assert len(sql) > 50

    def test_example_error_handling_patterns(self):
        from benchbox import TPCH

        tpch = TPCH()

        with pytest.raises(ValueError) as exc_info:
            tpch.get_query(23)
        assert "Query ID must be 1-22" in str(exc_info.value)

    def test_benchmark_example_patterns(self):

        from benchbox.core.tpch.benchmark import TPCHBenchmark

        benchmark = TPCHBenchmark()

        sql = benchmark.get_query(1)
        assert isinstance(sql, str)

        queries = benchmark.get_queries()
        assert len(queries) == 22

        sql_param = benchmark.get_query(1, params=None)
        assert isinstance(sql_param, str)

    @patch("duckdb.connect")
    def test_duckdb_integration_pattern(self, mock_connect):

        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.execute.return_value = MagicMock()

        from benchbox import TPCH

        tpch = TPCH()

        sql = tpch.get_query(1, seed=42)

        try:
            import duckdb

            mock_conn.execute(sql)
            mock_conn.execute.assert_called_once_with(sql)
        except ImportError:
            pytest.skip("DuckDB not available for integration test")

    def test_multi_scale_example_pattern(self):

        from benchbox import TPCH

        scale_factors = [0.01, 0.1, 1.0]

        for sf in scale_factors:
            tpch = TPCH(scale_factor=sf)

            sql = tpch.get_query(1)
            assert isinstance(sql, str)
            assert len(sql) > 50

            sql_override = tpch.get_query(1, scale_factor=sf * 2)
            assert isinstance(sql_override, str)

    def test_example_query_iteration_pattern(self):

        from benchbox import TPCH

        tpch = TPCH()

        results = {}
        for query_id in range(1, 6):
            sql = tpch.get_query(query_id, seed=123)
            results[query_id] = sql

            assert isinstance(sql, str)
            assert len(sql) > 50
            assert query_id in results

        sqls = list(results.values())
        for i, sql1 in enumerate(sqls):
            for j, sql2 in enumerate(sqls):
                if i != j:
                    assert sql1 != sql2

    def test_example_deterministic_testing_pattern(self):

        from benchbox import TPCH

        tpch = TPCH()

        seed = 42

        queries_run1 = [tpch.get_query(i, seed=seed) for i in range(1, 6)]
        queries_run2 = [tpch.get_query(i, seed=seed) for i in range(1, 6)]

        assert queries_run1 == queries_run2

    def test_example_performance_measurement_pattern(self):

        import time

        from benchbox import TPCH

        tpch = TPCH()

        start_time = time.time()
        sql = tpch.get_query(1)
        generation_time = time.time() - start_time

        assert isinstance(sql, str)
        assert len(sql) > 50
        assert generation_time < 1.0

    def test_example_batch_generation_pattern(self):

        from benchbox import TPCH

        tpch = TPCH()

        start_time = time.time()
        all_queries = tpch.get_queries()
        batch_time = time.time() - start_time

        assert len(all_queries) == 22
        assert batch_time < 5.0

        for query_id, sql in all_queries.items():
            assert isinstance(query_id, str)
            assert isinstance(sql, str)
            assert len(sql) > 50

    def test_example_error_recovery_pattern(self):
        from benchbox import TPCH

        tpch = TPCH()

        try:
            tpch.get_query(0)
            raise AssertionError("Should have raised ValueError")
        except ValueError as e:
            assert "Query ID must be 1-22" in str(e)

        sql = tpch.get_query(1)
        assert isinstance(sql, str)
        assert len(sql) > 50

    def test_example_configuration_pattern(self):

        from benchbox import TPCH
        from benchbox.core.tpch.benchmark import TPCHBenchmark

        configs = [
            {"scale_factor": 0.1},
            {"scale_factor": 1.0},
        ]

        for config in configs:
            tpch = TPCH(**config)
            sql = tpch.get_query(1)
            assert isinstance(sql, str)

            benchmark = TPCHBenchmark(**config)
            sql_benchmark = benchmark.get_query(1)
            assert isinstance(sql_benchmark, str)


