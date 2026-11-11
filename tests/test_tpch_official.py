# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ H (TPC-H) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-H specification.

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import patch

import pytest

from benchbox.core.tpch.benchmark import TPCHBenchmark
from benchbox.core.tpch.queries import TPCHQueries

pytestmark = [
    pytest.mark.medium,
    pytest.mark.tpch,
]


class TestTPCHQueryGeneration:
    def test_initialization_requires_qgen(self):
        with patch("benchbox.core.tpch.queries.QGenBinary") as mock_qgen:
            mock_qgen.side_effect = RuntimeError("qgen binary required but not found")

            with pytest.raises(RuntimeError) as exc_info:
                TPCHQueries()

            assert "qgen binary required but not found" in str(exc_info.value)

    def test_successful_initialization(self):
        queries = TPCHQueries()
        assert hasattr(queries.qgen, "generate")

    def test_get_query_all_valid_ids(self):
        queries = TPCHQueries()

        for query_id in range(1, 23):
            sql = queries.get_query(query_id)

            assert isinstance(sql, str)
            assert len(sql) > 50
            assert "select" in sql.lower() or "with" in sql.lower()
            assert ":1" not in sql

    def test_get_query_with_seed_determinism(self):
        queries = TPCHQueries()

        sql1 = queries.get_query(1, seed=12345)
        sql2 = queries.get_query(1, seed=12345)

        assert sql1 == sql2

        results1 = [queries.get_query(i, seed=42) for i in range(1, 6)]
        results2 = [queries.get_query(i, seed=42) for i in range(1, 6)]

        assert results1 == results2

    def test_get_query_different_seeds(self):
        queries = TPCHQueries()

        sql_seed1 = queries.get_query(1, seed=111)
        sql_seed2 = queries.get_query(1, seed=222)

        assert sql_seed1 != sql_seed2

    def test_get_query_with_scale_factor(self):
        queries = TPCHQueries()

        sql_sf1 = queries.get_query(1, scale_factor=1.0)
        sql_sf01 = queries.get_query(1, scale_factor=0.1)

        assert isinstance(sql_sf1, str)
        assert isinstance(sql_sf01, str)
        assert len(sql_sf1) > 0
        assert len(sql_sf01) > 0

    def test_get_query_invalid_id(self):
        queries = TPCHQueries()

        invalid_ids = [0, -1, 23, 100]
        for invalid_id in invalid_ids:
            with pytest.raises(ValueError) as exc_info:
                queries.get_query(invalid_id)
            assert f"Query ID must be 1-22, got {invalid_id}" in str(exc_info.value)

    def test_get_all_queries_batch(self):
        queries = TPCHQueries()

        all_queries = queries.get_all_queries()

        assert isinstance(all_queries, dict)
        assert len(all_queries) == 22

        for query_id in range(1, 23):
            assert query_id in all_queries
            assert isinstance(all_queries[query_id], str)
            assert len(all_queries[query_id]) > 50

    def test_tpchqueries_instantiation(self):
        manager = TPCHQueries()
        sql = manager.get_query(1)
        assert isinstance(sql, str)
        assert len(sql) > 50

    def test_parameter_combinations(self):
        queries = TPCHQueries()

        combinations = [
            {},
            {"seed": 123},
            {"scale_factor": 0.5},
            {"seed": 456, "scale_factor": 2.0},
        ]

        for params in combinations:
            sql = queries.get_query(1, **params)
            assert isinstance(sql, str)
            assert len(sql) > 50

    def test_query_content_validation(self):
        queries = TPCHQueries()

        query1 = queries.get_query(1)
        assert "lineitem" in query1.lower()
        assert "l_shipdate" in query1.lower()

        query2 = queries.get_query(2)
        assert "part" in query2.lower()
        assert "supplier" in query2.lower()

        query3 = queries.get_query(3)
        assert "customer" in query3.lower()
        assert "orders" in query3.lower()

    def test_no_parameter_placeholders_in_output(self):
        queries = TPCHQueries()

        for query_id in [1, 5, 10, 15, 20]:
            sql = queries.get_query(query_id, seed=99)

            assert ":1" not in sql
            assert ":2" not in sql
            assert ":3" not in sql
            assert ":4" not in sql
            assert ":5" not in sql

    def test_scale_factor_edge_cases(self):
        queries = TPCHQueries()

        sql_tiny = queries.get_query(1, scale_factor=0.01)
        assert isinstance(sql_tiny, str)
        assert len(sql_tiny) > 0

        sql_large = queries.get_query(1, scale_factor=10.0)
        assert isinstance(sql_large, str)
        assert len(sql_large) > 0

    def test_seed_edge_cases(self):
        queries = TPCHQueries()

        seeds = [0, 1, 999999, 2147483647]

        for seed in seeds:
            sql = queries.get_query(1, seed=seed)
            assert isinstance(sql, str)
            assert len(sql) > 0

    def test_error_propagation_from_qgen(self):
        queries = TPCHQueries()

        with patch.object(queries.qgen, "generate") as mock_generate:
            mock_generate.side_effect = RuntimeError("Mock qgen error")

            with pytest.raises(RuntimeError):
                queries.get_query(1)


class TestTPCHBenchmarkCore:
    def test_benchmark_initialization(self):
        benchmark = TPCHBenchmark()

        assert benchmark.query_manager is not None
        assert hasattr(benchmark.query_manager, "get_query")
        assert hasattr(benchmark.query_manager, "get_all_queries")

    def test_benchmark_query_generation(self):
        pass


