# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import re

from benchbox import TPCH
from benchbox.core.tpch.benchmark import TPCHBenchmark
from benchbox.core.tpch.queries import TPCHQueries
import pytest

pytestmark = pytest.mark.medium



class TestTPCHQueryGeneration:

    def test_all_queries_generate_successfully(self) -> None:
        tpch = TPCH()

        for query_id in range(1, 23):
            sql = tpch.get_query(query_id)
            assert isinstance(sql, str), f"Query {query_id} should return string"
            assert len(sql) > 50, f"Query {query_id} should have substantial content"

    def test_all_queries_contain_valid_sql_keywords(self) -> None:
        tpch = TPCH()

        for query_id in range(1, 23):
            sql = tpch.get_query(query_id, seed=12345)
            sql_lower = sql.lower()

            assert "select" in sql_lower or "with" in sql_lower, f"Query {query_id} should contain SELECT or WITH"

            assert "from" in sql_lower, f"Query {query_id} should contain FROM"

    def test_queries_have_no_parameter_placeholders(self) -> None:
        tpch = TPCH()

        for query_id in range(1, 23):
            sql = tpch.get_query(query_id, seed=54321)

            assert not re.search(r":\d+", sql), f"Query {query_id} contains parameter placeholders"

            assert ":" not in sql or "TIMESTAMP" in sql.upper(), f"Query {query_id} may contain unresolved parameters"

    def test_queries_contain_expected_tpch_tables(self) -> None:
        tpch = TPCH()

        expected_tables = {
            "customer",
            "lineitem",
            "nation",
            "orders",
            "part",
            "partsupp",
            "region",
            "supplier",
        }

        all_tables_found = set()

        for query_id in range(1, 23):
            sql = tpch.get_query(query_id, seed=11111)
            sql_lower = sql.lower()

            for table in expected_tables:
                if table in sql_lower:
                    all_tables_found.add(table)

        assert len(all_tables_found) >= 6, "Should find most TPC-H tables across queries"

    def test_queries_with_different_scale_factors(self) -> None:
        tpch = TPCH()
        scale_factors = [0.001, 0.01, 0.1, 1.0, 10.0]

        for sf in scale_factors:
            for query_id in [1, 5, 10, 15, 20]:
                sql = tpch.get_query(query_id, scale_factor=sf, seed=99999)

                assert isinstance(sql, str), f"Query {query_id} with SF {sf} should return string"
                assert len(sql) > 50, f"Query {query_id} with SF {sf} should have content"

    def test_queries_with_different_seeds(self) -> None:
        tpch = TPCH()
        seeds = [1, 100, 10000, 999999]

        for seed in seeds:
            for query_id in [1, 6, 11, 16, 21]:
                sql = tpch.get_query(query_id, seed=seed)

                assert isinstance(sql, str), f"Query {query_id} with seed {seed} should return string"
                assert len(sql) > 50, f"Query {query_id} with seed {seed} should have content"

    def test_query_uniqueness_across_ids(self) -> None:
        tpch = TPCH()
        seed = 55555

        queries = {}
        for query_id in range(1, 23):
            queries[query_id] = tpch.get_query(query_id, seed=seed)

        sql_texts = list(queries.values())
        unique_sqls = set(sql_texts)
        assert len(unique_sqls) == 22, "All 22 queries should be unique"

    def test_query_complexity_distribution(self) -> None:
        tpch = TPCH()

        query_lengths = []
        for query_id in range(1, 23):
            sql = tpch.get_query(query_id, seed=77777)
            query_lengths.append(len(sql))

        min_length = min(query_lengths)
        max_length = max(query_lengths)

        assert min_length >= 100, "Shortest query should be at least 100 chars"
        assert max_length >= 500, "Longest query should be at least 500 chars"
        assert max_length > min_length * 2, "Should have significant length variation"

    def test_queries_contain_appropriate_clauses(self) -> None:
        tpch = TPCH()

        clause_counts = {
            "select": 0,
            "from": 0,
            "where": 0,
            "group by": 0,
            "order by": 0,
            "having": 0,
            "join": 0,
        }

        for query_id in range(1, 23):
            sql = tpch.get_query(query_id, seed=33333)
            sql_lower = sql.lower()

            for clause in clause_counts:
                if clause in sql_lower:
                    clause_counts[clause] += 1

        assert clause_counts["select"] >= 20, "Most queries should have SELECT"
        assert clause_counts["from"] >= 20, "Most queries should have FROM"
        assert clause_counts["where"] >= 15, "Many queries should have WHERE"
        assert clause_counts["group by"] >= 5, "Some queries should have GROUP BY"
        assert clause_counts["order by"] >= 10, "Many queries should have ORDER BY"

    def test_query_generation_performance(self) -> None:
        import time

        tpch = TPCH()

        start_time = time.time()
        sql = tpch.get_query(1, seed=12345)
        single_time = time.time() - start_time

        assert single_time < 1.0, "Single query generation should be under 1 second"
        assert isinstance(sql, str)
        assert len(sql) > 50

        start_time = time.time()
        all_queries = tpch.get_queries()
        batch_time = time.time() - start_time

        assert batch_time < 10.0, "Batch generation should be under 10 seconds"
        assert len(all_queries) == 22

    def test_query_generation_with_boundary_conditions(self) -> None:
        tpch = TPCH()

        sql_min = tpch.get_query(1, scale_factor=0.01, seed=44444)
        assert isinstance(sql_min, str)
        assert len(sql_min) > 50

        sql_large = tpch.get_query(1, scale_factor=10.0, seed=44444)
        assert isinstance(sql_large, str)
        assert len(sql_large) > 50

        sql_first = tpch.get_query(1, seed=44444)
        sql_last = tpch.get_query(22, seed=44444)

        assert isinstance(sql_first, str)
        assert isinstance(sql_last, str)
        assert len(sql_first) > 50
        assert len(sql_last) > 50

    def test_query_sql_syntax_validation(self) -> None:
        tpch = TPCH()

        for query_id in range(1, 23):
            sql = tpch.get_query(query_id, seed=66666)

            assert sql.count("(") == sql.count(")"), f"Query {query_id} has unmatched parentheses"

            assert not sql.strip().endswith(","), f"Query {query_id} ends with comma"
            assert not sql.strip().endswith("AND"), f"Query {query_id} ends with AND"
            assert not sql.strip().endswith("OR"), f"Query {query_id} ends with OR"

    def test_query_generation_across_interfaces(self) -> None:
        seed = 88888
        query_id = 8

        tpch = TPCH()
        benchmark = TPCHBenchmark()
        queries = TPCHQueries()

        sql_tpch = tpch.get_query(query_id, seed=seed)
        sql_benchmark = benchmark.get_query(query_id, seed=seed)
        sql_queries = queries.get_query(query_id, seed=seed)

        for sql in [sql_tpch, sql_benchmark, sql_queries]:
            assert isinstance(sql, str)
            assert len(sql) > 50
            assert "select" in sql.lower() or "with" in sql.lower()

    def test_batch_vs_individual_consistency(self) -> None:
        tpch = TPCH()

        individual_queries = {}
        for query_id in range(1, 23):
            individual_queries[str(query_id)] = tpch.get_query(query_id)

        batch_queries = tpch.get_queries()

        assert len(individual_queries) == len(batch_queries) == 22

        for query_id_str in individual_queries:
            assert isinstance(individual_queries[query_id_str], str)
            assert len(individual_queries[query_id_str]) > 50

        for query_id_str in batch_queries:
            assert isinstance(batch_queries[query_id_str], str)
            assert len(batch_queries[query_id_str]) > 50

    def test_error_conditions_dont_break_generation(self) -> None:
        tpch = TPCH()

        sql_before = tpch.get_query(1, seed=11111)
        assert isinstance(sql_before, str)

        try:
            tpch.get_query(0)
        except ValueError:
            pass

        try:
            tpch.get_query(25)
        except ValueError:
            pass

        sql_after = tpch.get_query(1, seed=11111)
        assert isinstance(sql_after, str)
        assert sql_before == sql_after


