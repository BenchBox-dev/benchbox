# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from benchbox import TPCH
from benchbox.core.tpch.benchmark import TPCHBenchmark
from benchbox.core.tpch.queries import TPCHQueries
import pytest

pytestmark = pytest.mark.medium



class TestTPCHDeterminism:

    def test_same_seed_produces_identical_queries(self):
        tpch = TPCH()
        seed = 12345

        queries = []
        for _ in range(3):
            sql = tpch.get_query(1, seed=seed)
            queries.append(sql)

        assert len(set(queries)) == 1, "Same seed should produce identical queries"

        assert isinstance(queries[0], str)
        assert len(queries[0]) > 50

    def test_different_seeds_produce_different_queries(self):
        tpch = TPCH()

        queries = []
        for seed in [111, 222, 333, 444, 555]:
            sql = tpch.get_query(1, seed=seed)
            queries.append(sql)

        assert len(set(queries)) == 5, "Different seeds should produce different queries"

        for sql in queries:
            assert isinstance(sql, str)
            assert len(sql) > 50

    def test_determinism_across_query_manager_interfaces(self):
        seed = 98765
        query_id = 5

        tpch = TPCH()
        benchmark = TPCHBenchmark()
        queries = TPCHQueries()

        sql_tpch = tpch.get_query(query_id, seed=seed)
        sql_benchmark = benchmark.get_query(query_id, seed=seed)
        sql_queries = queries.get_query(query_id, seed=seed)

        def normalize_sql(sql):
            import re

            normalized = sql.strip()
            normalized = normalized.rstrip(";")
            normalized = normalized.replace('"', "")
            normalized = re.sub(r"date\s+'([^']+)'", r"cast('\1' as date)", normalized)
            normalized = re.sub(r"interval\s+'(\d+)'\s+(\w+)", r"interval '\1 \2'", normalized)
            return " ".join(normalized.split()).lower()

        assert normalize_sql(sql_tpch) == normalize_sql(sql_benchmark) == normalize_sql(sql_queries)

    def test_determinism_with_scale_factor(self):
        seed = 55555
        query_id = 10

        tpch = TPCH()

        results = []
        for _ in range(3):
            sql = tpch.get_query(query_id, seed=seed, scale_factor=0.5)
            results.append(sql)

        assert len(set(results)) == 1

        sql_sf1 = tpch.get_query(query_id, seed=seed, scale_factor=1.0)
        sql_sf2 = tpch.get_query(query_id, seed=seed, scale_factor=2.0)

        assert sql_sf1 == tpch.get_query(query_id, seed=seed, scale_factor=1.0)
        assert sql_sf2 == tpch.get_query(query_id, seed=seed, scale_factor=2.0)

    def test_determinism_across_all_queries(self):
        seed = 77777
        tpch = TPCH()

        queries_run1 = {}
        queries_run2 = {}

        for query_id in range(1, 23):
            queries_run1[query_id] = tpch.get_query(query_id, seed=seed)
            queries_run2[query_id] = tpch.get_query(query_id, seed=seed)

        for query_id in range(1, 23):
            assert queries_run1[query_id] == queries_run2[query_id], f"Query {query_id} not deterministic"

    def test_determinism_with_batch_generation(self):
        seed = 88888
        tpch = TPCH()


        tpch.get_queries()

        for query_id in [1, 5, 10, 15, 20]:
            sql1 = tpch.get_query(query_id, seed=seed)
            sql2 = tpch.get_query(query_id, seed=seed)
            assert sql1 == sql2, f"Query {query_id} not deterministic after batch operations"

    def test_determinism_with_parameterized_queries(self):
        seed = 33333
        tpch = TPCH()

        sql1 = tpch.get_query(1, params=None, seed=seed)
        sql2 = tpch.get_query(1, params=None, seed=seed)
        assert sql1 == sql2

        sql3 = tpch.get_query(1, seed=seed, dialect="duckdb")
        sql4 = tpch.get_query(1, seed=seed, dialect="duckdb")
        assert sql3 == sql4

    def test_determinism_independence_across_instances(self):
        seed = 11111
        query_id = 7

        tpch1 = TPCH()
        tpch2 = TPCH()
        tpch3 = TPCH(scale_factor=0.5)

        sql1 = tpch1.get_query(query_id, seed=seed)
        sql2 = tpch2.get_query(query_id, seed=seed)
        sql3 = tpch3.get_query(query_id, seed=seed, scale_factor=1.0)

        assert sql1 == sql2 == sql3

    def test_determinism_with_concurrent_generation(self):
        import threading

        seed = 99999
        query_id = 12
        results = []

        def generate_query():
            tpch = TPCH()
            sql = tpch.get_query(query_id, seed=seed)
            results.append(sql)

        threads = []
        for _ in range(5):
            thread = threading.Thread(target=generate_query)
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        assert len(set(results)) == 1, "Concurrent generation should be deterministic"

    def test_determinism_with_error_recovery(self):
        seed = 22222
        tpch = TPCH()

        sql_before = tpch.get_query(1, seed=seed)

        try:
            tpch.get_query(25, seed=seed)
        except ValueError:
            pass

        sql_after = tpch.get_query(1, seed=seed)

        assert sql_before == sql_after, "Determinism should survive error conditions"

    def test_random_seed_behavior(self):
        tpch = TPCH()

        queries = []
        for _ in range(5):
            sql = tpch.get_query(1)
            queries.append(sql)

        for sql in queries:
            assert isinstance(sql, str)
            assert len(sql) > 50

    def test_seed_boundary_conditions(self):
        tpch = TPCH()
        query_id = 3

        boundary_seeds = [0, 1, 2**31 - 1, 2**32 - 1]

        for seed in boundary_seeds:
            sql1 = tpch.get_query(query_id, seed=seed)
            sql2 = tpch.get_query(query_id, seed=seed)

            assert sql1 == sql2, f"Seed {seed} not deterministic"
            assert isinstance(sql1, str)
            assert len(sql1) > 50


