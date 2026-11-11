# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ H (TPC-H) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-H specification.

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox import TPCH
from benchbox.core.tpch.benchmark import TPCHBenchmark

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


class TestTPCHBenchmarkIntegration:
    def test_benchmark_initialization(self):

        benchmark = TPCHBenchmark()

        assert benchmark.query_manager is not None
        assert hasattr(benchmark.query_manager, "get_query")
        assert hasattr(benchmark.query_manager, "get_all_queries")

    def test_benchmark_query_generation(self):

        benchmark = TPCHBenchmark()

        sql = benchmark.get_query(1)
        assert len(sql) > 50
        assert "select" in sql.lower() or "with" in sql.lower()

    def test_benchmark_query_generation_with_parameters(self):

        benchmark = TPCHBenchmark()

        sql1 = benchmark.get_query(1, seed=12345)
        sql2 = benchmark.get_query(1, seed=12345)
        assert sql1 == sql2

        sql3 = benchmark.get_query(1, seed=54321)
        assert sql1 != sql3

        sql_sf = benchmark.get_query(1, scale_factor=0.5)
        assert "select" in sql_sf.lower() or "with" in sql_sf.lower()
        assert len(sql_sf) > 50

    def test_benchmark_scale_factor_inheritance(self):

        benchmark = TPCHBenchmark(scale_factor=2.0)

        sql = benchmark.get_query(1)
        assert "select" in sql.lower() or "with" in sql.lower()
        assert len(sql) > 50

        sql_override = benchmark.get_query(1, scale_factor=0.1)
        assert "select" in sql_override.lower() or "with" in sql_override.lower()
        assert len(sql_override) > 50

    def test_benchmark_get_parameterized_query_compatibility(self):

        benchmark = TPCHBenchmark()

        sql_old = benchmark.get_query(1, params=None)
        assert "select" in sql_old.lower() or "with" in sql_old.lower()
        assert len(sql_old) > 50

        sql_new = benchmark.get_query(1, seed=12345)
        assert "select" in sql_new.lower() or "with" in sql_new.lower()
        assert len(sql_new) > 50

        sql_mixed = benchmark.get_query(1, params=None, seed=12345, dialect="duckdb")
        assert "select" in sql_mixed.lower() or "with" in sql_mixed.lower()
        assert len(sql_mixed) > 50

    def test_benchmark_get_queries_batch(self):

        benchmark = TPCHBenchmark()

        queries = benchmark.get_queries()

        assert len(queries) == 22

        for key in queries:
            assert int(key) in range(1, 23)

        for sql in queries.values():
            assert "select" in sql.lower() or "with" in sql.lower()
            assert len(sql) > 50

    def test_benchmark_all_queries_generation(self):
        benchmark = TPCHBenchmark()

        for query_id in range(1, 23):
            sql = benchmark.get_query(query_id)
            assert "select" in sql.lower() or "with" in sql.lower()
            assert len(sql) > 50
            assert f"Query {query_id} failed", f"Query {query_id} should generate successfully"

    def test_benchmark_error_handling(self):

        benchmark = TPCHBenchmark()

        with pytest.raises(ValueError) as exc_info:
            benchmark.get_query(23)
        assert "Query ID must be 1-22" in str(exc_info.value)

        with pytest.raises(ValueError):
            benchmark.get_query(0)

    def test_benchmark_with_different_scale_factors(self):

        scale_factors = [0.01, 0.1, 1.0, 2.0]

        for sf in scale_factors:
            benchmark = TPCHBenchmark(scale_factor=sf)
            sql = benchmark.get_query(1)
            assert "select" in sql.lower() or "with" in sql.lower()
            assert len(sql) > 50


class TestTopLevelTPCHIntegration:
    def test_tpch_initialization(self):

        tpch = TPCH()

        assert tpch._impl is not None
        assert hasattr(tpch._impl, "query_manager")

    def test_tpch_query_generation(self):

        tpch = TPCH()

        sql = tpch.get_query(1)
        assert "select" in sql.lower() or "with" in sql.lower()
        assert len(sql) > 50

        sql_with_seed = tpch.get_query(1, seed=12345)
        assert "select" in sql_with_seed.lower() or "with" in sql_with_seed.lower()
        assert len(sql_with_seed) > 50

    def test_tpch_parameterized_query_compatibility(self):

        tpch = TPCH()

        sql_old = tpch.get_query(1, params=None, dialect="duckdb")
        assert "select" in sql_old.lower() or "with" in sql_old.lower()
        assert len(sql_old) > 50

        sql_new = tpch.get_query(1, seed=12345, dialect="duckdb")
        assert "select" in sql_new.lower() or "with" in sql_new.lower()
        assert len(sql_new) > 50

    def test_tpch_get_queries_batch(self):

        tpch = TPCH()

        queries = tpch.get_queries()

        assert len(queries) == 22

        for key, sql in queries.items():
            assert int(key) in range(1, 23)
            assert "select" in sql.lower() or "with" in sql.lower()
            assert len(sql) > 50

    def test_tpch_scale_factor_inheritance(self):

        tpch = TPCH(scale_factor=0.5)

        sql = tpch.get_query(1)
        assert "select" in sql.lower() or "with" in sql.lower()
        assert len(sql) > 50

        sql_override = tpch.get_query(1, scale_factor=2.0)
        assert "select" in sql_override.lower() or "with" in sql_override.lower()
        assert len(sql_override) > 50

    def test_tpch_all_queries_integration(self):

        tpch = TPCH()

        for query_id in [1, 5, 10, 15, 20]:
            sql = tpch.get_query(query_id, seed=42)
            assert "select" in sql.lower() or "with" in sql.lower()
            assert len(sql) > 50
            assert ":1" not in sql

    def test_tpch_deterministic_behavior(self):

        tpch = TPCH()

        sql1 = tpch.get_query(1, seed=999)
        sql2 = tpch.get_query(1, seed=999)
        assert sql1 == sql2

        sql3 = tpch.get_query(1, seed=111)
        assert sql1 != sql3

    def test_tpch_error_propagation(self):

        tpch = TPCH()

        with pytest.raises(ValueError) as exc_info:
            tpch.get_query(25)
        assert "Query ID must be 1-22" in str(exc_info.value)


class TestCrossComponentIntegration:
    def test_query_consistency_across_interfaces(self):

        TPCH().get_queries()
        benchmark = TPCHBenchmark()
        tpch = TPCH()

        seed = 12345
        query_id = 1

        sql_benchmark = benchmark.get_query(query_id, seed=seed)
        sql_tpch = tpch.get_query(query_id, seed=seed)

        assert sql_benchmark == sql_tpch

    def test_parameter_inheritance_consistency(self):

        sf = 0.75
        benchmark = TPCHBenchmark(scale_factor=sf)
        tpch = TPCH(scale_factor=sf)

        sql_benchmark = benchmark.get_query(1)
        sql_tpch = tpch.get_query(1)

        assert "select" in sql_benchmark.lower() or "with" in sql_benchmark.lower()
        assert "select" in sql_tpch.lower() or "with" in sql_tpch.lower()
        assert len(sql_benchmark) > 50
        assert len(sql_tpch) > 50

    def test_error_handling_consistency(self):

        benchmark = TPCHBenchmark()
        tpch = TPCH()

        with pytest.raises(ValueError):
            benchmark.get_query(0)

        with pytest.raises(ValueError):
            tpch.get_query(0)

    def test_api_signature_consistency(self):

        benchmark = TPCHBenchmark()
        tpch = TPCH()

        test_params = [
            {"seed": 123},
            {"scale_factor": 0.5},
            {"seed": 456, "scale_factor": 1.5},
        ]

        for params in test_params:
            sql_benchmark = benchmark.get_query(1, **params)
            sql_tpch = tpch.get_query(1, **params)

            assert "select" in sql_benchmark.lower() or "with" in sql_benchmark.lower()
            assert "select" in sql_tpch.lower() or "with" in sql_tpch.lower()
            assert len(sql_benchmark) > 50
            assert len(sql_tpch) > 50


