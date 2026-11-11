# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox import TPCH
from benchbox.core.tpch.benchmark import TPCHBenchmark
from benchbox.core.tpch.queries import TPCHQueries

pytestmark = pytest.mark.medium



class TestTPCHParameterCompliance:

    def test_scale_factor_parameter_compliance(self):
        tpch = TPCH()

        valid_scale_factors = [0.001, 0.01, 0.1, 1.0, 10.0, 100.0, 1000.0]

        for sf in valid_scale_factors:
            sql = tpch.get_query(1, scale_factor=sf, seed=12345)
            assert len(sql) > 50
            assert "select" in sql.lower() or "with" in sql.lower()

    def test_seed_parameter_compliance(self):
        tpch = TPCH()

        valid_seeds = [1, 100, 10000, 2**31 - 1]

        for seed in valid_seeds:
            sql = tpch.get_query(1, seed=seed)
            assert len(sql) > 50
            assert "select" in sql.lower() or "with" in sql.lower()

    def test_query_id_parameter_compliance(self):
        tpch = TPCH()

        for query_id in range(1, 23):
            sql = tpch.get_query(query_id, seed=54321)
            assert len(sql) > 50
            assert "select" in sql.lower() or "with" in sql.lower()

        invalid_ids = [0, 23, 24, 25, -1, 100]
        for query_id in invalid_ids:
            with pytest.raises(ValueError) as exc_info:
                tpch.get_query(query_id)
            assert "Query ID must be 1-22" in str(exc_info.value)

    def test_parameter_type_validation(self):
        tpch = TPCH()

        sql = tpch.get_query(1, seed=12345, scale_factor=0.01)
        assert "select" in sql.lower() or "with" in sql.lower()

        sql = tpch.get_query(1, seed=12345, scale_factor=0.01)
        assert "select" in sql.lower() or "with" in sql.lower()

        sql = tpch.get_query(1, seed=12345)
        assert "select" in sql.lower() or "with" in sql.lower()

    def test_parameter_inheritance_compliance(self):
        benchmark = TPCHBenchmark(scale_factor=0.01)

        sql = benchmark.get_query(1, seed=99999)
        assert len(sql) > 50
        assert "select" in sql.lower() or "with" in sql.lower()

        sql_override = benchmark.get_query(1, seed=99999, scale_factor=0.5)
        assert len(sql_override) > 50
        assert "select" in sql_override.lower() or "with" in sql_override.lower()

    def test_parameter_boundary_conditions(self):
        tpch = TPCH()

        sql_min = tpch.get_query(1, scale_factor=0.01, seed=11111)
        assert "select" in sql_min.lower() or "with" in sql_min.lower()

        sql_max = tpch.get_query(1, scale_factor=100.0, seed=11111)
        assert "select" in sql_max.lower() or "with" in sql_max.lower()

        sql_seed_min = tpch.get_query(1, seed=1)
        assert "select" in sql_seed_min.lower() or "with" in sql_seed_min.lower()

        sql_seed_max = tpch.get_query(1, seed=2**31 - 1)
        assert "select" in sql_seed_max.lower() or "with" in sql_seed_max.lower()

    def test_parameter_combination_compliance(self):
        tpch = TPCH()

        combinations = [
            {},
            {"seed": 12345},
            {"scale_factor": 0.1},
            {"seed": 54321, "scale_factor": 2.0},
        ]

        for params in combinations:
            sql = tpch.get_query(1, **params)
            assert len(sql) > 50
            assert "select" in sql.lower() or "with" in sql.lower()

    def test_deprecated_parameter_handling(self):
        tpch = TPCH()

        sql = tpch.get_query(1, params=None, seed=33333)
        assert len(sql) > 50
        assert "select" in sql.lower() or "with" in sql.lower()

        sql_mixed = tpch.get_query(1, params=None, seed=44444, dialect="duckdb")
        assert len(sql_mixed) > 50
        assert "select" in sql_mixed.lower() or "with" in sql_mixed.lower()

    def test_parameter_validation_across_interfaces(self):
        seed = 77777
        scale_factor = 0.01

        tpch = TPCH()
        benchmark = TPCHBenchmark()
        queries = TPCHQueries()

        sql_tpch = tpch.get_query(1, seed=seed, scale_factor=scale_factor)
        sql_benchmark = benchmark.get_query(1, seed=seed, scale_factor=scale_factor)
        sql_queries = queries.get_query(1, seed=seed, scale_factor=scale_factor)

        assert "select" in sql_tpch.lower() or "with" in sql_tpch.lower()
        assert "select" in sql_benchmark.lower() or "with" in sql_benchmark.lower()
        assert "select" in sql_queries.lower() or "with" in sql_queries.lower()

    def test_parameter_persistence_across_calls(self):
        tpch = TPCH()

        sql1 = tpch.get_query(1, seed=11111, scale_factor=0.01)

        sql2 = tpch.get_query(1)

        sql3 = tpch.get_query(1, seed=22222, scale_factor=0.01)

        assert "select" in sql1.lower() or "with" in sql1.lower()
        assert "select" in sql2.lower() or "with" in sql2.lower()
        assert "select" in sql3.lower() or "with" in sql3.lower()

    def test_default_parameter_behavior(self):
        tpch = TPCH()

        sql_no_params = tpch.get_query(1)
        assert len(sql_no_params) > 50
        assert "select" in sql_no_params.lower() or "with" in sql_no_params.lower()

        sql_no_params2 = tpch.get_query(1)
        assert sql_no_params == sql_no_params2

    def test_parameter_validation_error_messages(self):
        tpch = TPCH()

        with pytest.raises(ValueError) as exc_info:
            tpch.get_query(0)
        assert "Query ID must be 1-22" in str(exc_info.value)

        with pytest.raises(ValueError) as exc_info:
            tpch.get_query(25)
        assert "Query ID must be 1-22" in str(exc_info.value)

    def test_scale_factor_precision_handling(self):
        tpch = TPCH()

        scale_factors = [0.001, 0.01, 0.1, 1.0, 1.5, 2.0, 10.0, 100.0]

        for sf in scale_factors:
            sql = tpch.get_query(1, scale_factor=sf, seed=88888)
            assert len(sql) > 50
            assert "select" in sql.lower() or "with" in sql.lower()

    def test_seed_range_compliance(self):
        tpch = TPCH()

        seed_ranges = [
            [1, 100],
            [1000, 10000],
            [100000, 1000000],
            [2**30, 2**31 - 1],
        ]

        for start, end in seed_ranges:
            for seed in [start, (start + end) // 2, end]:
                sql = tpch.get_query(1, seed=seed)
                assert "select" in sql.lower() or "with" in sql.lower()
                assert len(sql) > 50

    def test_parameter_immutability(self):
        tpch = TPCH(scale_factor=0.01)

        tpch.get_query(1, scale_factor=0.01)
        tpch.get_query(1, scale_factor=0.02)

        assert tpch.scale_factor == 0.01

        sql3 = tpch.get_query(1)
        assert "select" in sql3.lower() or "with" in sql3.lower()

    def test_parameter_forwarding_compliance(self):
        benchmark = TPCHBenchmark(scale_factor=0.01)

        sql1 = benchmark.get_query(1, seed=12345)
        sql2 = benchmark.get_query(1, seed=12345)

        assert sql1 == sql2

        sql3 = benchmark.get_query(1, seed=54321, scale_factor=0.01)
        assert len(sql3) > 50
        assert "select" in sql3.lower() or "with" in sql3.lower()

    def test_tpch_specification_compliance(self):
        tpch = TPCH()

        queries = tpch.get_queries()
        assert len(queries) == 22

        for query_id in range(1, 23):
            assert str(query_id) in queries

        for _query_id_str, sql in queries.items():
            assert len(sql) > 50
            assert "select" in sql.lower() or "with" in sql.lower()


