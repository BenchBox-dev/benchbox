# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ H (TPC-H) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-H specification.

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox import TPCH
from benchbox.core.tpch.benchmark import TPCHBenchmark
from benchbox.core.tpch.queries import TPCHQueries

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


class TestAPICompatibility:
    def test_tpchqueries_import_compatibility(self):
        manager = TPCHQueries()
        assert isinstance(manager, TPCHQueries)

    def test_get_query_original_signature(self):
        queries = TPCHQueries()

        sql = queries.get_query(1)
        assert len(sql) > 50
        assert "select" in sql.lower() or "with" in sql.lower()

    def test_get_query_enhanced_signature(self):
        queries = TPCHQueries()

        sql = queries.get_query(1, seed=12345, scale_factor=1.0)
        assert len(sql) > 50
        assert "select" in sql.lower() or "with" in sql.lower()

    def test_benchmark_integration_compatibility(self):
        benchmark = TPCHBenchmark()

        sql = benchmark.get_query(1)
        assert len(sql) > 50
        assert "select" in sql.lower() or "with" in sql.lower()

        sql_enhanced = benchmark.get_query(1, seed=12345)
        assert len(sql_enhanced) > 50
        assert "select" in sql_enhanced.lower() or "with" in sql_enhanced.lower()

    def test_benchmark_get_parameterized_query_compatibility(self):
        benchmark = TPCHBenchmark()

        sql_old = benchmark.get_query(1, params=None, dialect="standard")
        assert len(sql_old) > 50
        assert "select" in sql_old.lower() or "with" in sql_old.lower()

        sql_new = benchmark.get_query(1, seed=12345, scale_factor=1.0)
        assert len(sql_new) > 50
        assert "select" in sql_new.lower() or "with" in sql_new.lower()

        sql_mixed = benchmark.get_query(1, params=None, seed=12345)
        assert len(sql_mixed) > 50
        assert "select" in sql_mixed.lower() or "with" in sql_mixed.lower()

    def test_top_level_tpch_compatibility(self):
        tpch = TPCH()

        sql = tpch.get_query(1)
        assert len(sql) > 50
        assert "select" in sql.lower() or "with" in sql.lower()

        sql_enhanced = tpch.get_query(1, seed=12345, scale_factor=1.0)
        assert len(sql_enhanced) > 50
        assert "select" in sql_enhanced.lower() or "with" in sql_enhanced.lower()

    def test_top_level_tpch_parameterized_query_compatibility(self):
        tpch = TPCH()

        sql_old = tpch.get_query(1, params=None, dialect="duckdb")
        assert len(sql_old) > 50
        assert "select" in sql_old.lower() or "with" in sql_old.lower()

        sql_new = tpch.get_query(1, seed=12345, dialect="duckdb")
        assert len(sql_new) > 50
        assert "select" in sql_new.lower() or "with" in sql_new.lower()

    def test_deprecated_params_parameter_ignored(self):
        tpch = TPCH()

        sql1 = tpch.get_query(1, params=None, seed=42)
        sql2 = tpch.get_query(1, params={"1": "ignored"}, seed=42)
        sql3 = tpch.get_query(1, seed=42)

        assert sql1 == sql2 == sql3

    def test_method_signature_parameter_ordering(self):
        tpch = TPCH()

        sql1 = tpch.get_query(1)
        assert "select" in sql1.lower() or "with" in sql1.lower()

        sql2 = tpch.get_query(query_id=1, seed=123)
        assert "select" in sql2.lower() or "with" in sql2.lower()

        sql3 = tpch.get_query(1, seed=123, scale_factor=1.0)
        assert "select" in sql3.lower() or "with" in sql3.lower()

    def test_scale_factor_inheritance_from_benchmark(self):
        benchmark = TPCHBenchmark(scale_factor=2.0)

        sql = benchmark.get_query(1)
        assert len(sql) > 50
        assert "select" in sql.lower() or "with" in sql.lower()

        sql_override = benchmark.get_query(1, scale_factor=0.5)
        assert len(sql_override) > 50
        assert "select" in sql_override.lower() or "with" in sql_override.lower()

    def test_benchmark_get_queries_compatibility(self):
        benchmark = TPCHBenchmark()

        queries = benchmark.get_queries()

        assert len(queries) == 22

        for key in queries:
            assert int(key) in range(1, 23)

        for sql in queries.values():
            assert len(sql) > 50
            assert "select" in sql.lower() or "with" in sql.lower()

    def test_import_paths_compatibility(self):
        from benchbox.core.tpch.queries import TPCHQueries as Manager1

        assert Manager1 is TPCHQueries

        from benchbox.core.tpch.benchmark import TPCHBenchmark as Benchmark1

        benchmark = Benchmark1()
        assert hasattr(benchmark, "get_query")

        from benchbox import TPCH as TPCH1

        tpch = TPCH1()
        assert hasattr(tpch, "get_query")

    def test_error_handling_compatibility(self):
        queries = TPCHQueries()

        with pytest.raises(ValueError) as exc_info:
            queries.get_query(23)
        assert "Query ID must be 1-22" in str(exc_info.value)

        benchmark = TPCHBenchmark()
        with pytest.raises(ValueError):
            benchmark.get_query(0)

        tpch = TPCH()
        with pytest.raises(ValueError):
            tpch.get_query(-1)

    def test_return_type_compatibility(self):
        tpch = TPCH()

        sql = tpch.get_query(1)
        assert "select" in sql.lower() or "with" in sql.lower()

        sql_param = tpch.get_query(1)
        assert "select" in sql_param.lower() or "with" in sql_param.lower()

        queries = tpch.get_queries()
        assert len(queries) == 22

    def test_dialect_parameter_handling(self):
        tpch = TPCH()

        sql_duckdb = tpch.get_query(1, dialect="duckdb")
        sql_postgres = tpch.get_query(1, dialect="postgres")
        sql_standard = tpch.get_query(1, dialect="standard")

        assert len(sql_duckdb) > 50
        assert "select" in sql_duckdb.lower() or "with" in sql_duckdb.lower()
        assert len(sql_postgres) > 50
        assert "select" in sql_postgres.lower() or "with" in sql_postgres.lower()
        assert len(sql_standard) > 50
        assert "select" in sql_standard.lower() or "with" in sql_standard.lower()


