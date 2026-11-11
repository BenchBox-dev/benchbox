# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ H (TPC-H) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-H specification.

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import patch

import pytest

from benchbox.core.tpch.queries import TPCHQueries

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


class TestTPCHQueries:
    def test_initialization_requires_qgen(self):
        with patch("benchbox.core.tpch.queries.QGenBinary") as mock_qgen:
            mock_qgen.side_effect = RuntimeError("qgen binary required but not found")

            with pytest.raises(RuntimeError) as exc_info:
                TPCHQueries()

            assert "qgen binary required but not found" in str(exc_info.value)

    def test_successful_initialization(self):
        queries = TPCHQueries()
        assert queries.qgen is not None

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

    def test_get_query_invalid_id_low(self):
        queries = TPCHQueries()

        with pytest.raises(ValueError) as exc_info:
            queries.get_query(0)

        assert "Query ID must be 1-22, got 0" in str(exc_info.value)

    def test_get_query_invalid_id_high(self):
        queries = TPCHQueries()

        with pytest.raises(ValueError) as exc_info:
            queries.get_query(23)

        assert "Query ID must be 1-22, got 23" in str(exc_info.value)

    def test_get_query_invalid_id_negative(self):
        queries = TPCHQueries()

        with pytest.raises(ValueError) as exc_info:
            queries.get_query(-1)

        assert "Query ID must be 1-22, got -1" in str(exc_info.value)

    def test_get_all_queries_batch(self):
        queries = TPCHQueries()

        all_queries = queries.get_all_queries()

        assert isinstance(all_queries, dict)
        assert len(all_queries) == 22

        for query_id in range(1, 23):
            assert query_id in all_queries
            assert isinstance(all_queries[query_id], str)
            assert len(all_queries[query_id]) > 50

    def test_get_all_queries_with_parameters(self):
        queries = TPCHQueries()

        all_queries_seed = queries.get_all_queries(seed=777)
        assert len(all_queries_seed) == 22

        all_queries_sf = queries.get_all_queries(scale_factor=0.5)
        assert len(all_queries_sf) == 22

        all_queries_both = queries.get_all_queries(seed=888, scale_factor=2.0)
        assert len(all_queries_both) == 22

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

    def test_extract_rowcount_oracle_syntax(self):
        from benchbox.core.tpch.queries import QGenBinary

        qgen = QGenBinary()

        assert qgen._extract_rowcount("SELECT * FROM orders WHERE ROWNUM <= 20;") == 20
        assert qgen._extract_rowcount("SELECT * FROM orders WHERE ROWNUM <= 100;") == 100
        assert qgen._extract_rowcount("select * from orders where rownum <= 10;") == 10

        assert qgen._extract_rowcount("SELECT * FROM orders;") is None

    def test_extract_rowcount_sqlserver_syntax(self):
        from benchbox.core.tpch.queries import QGenBinary

        qgen = QGenBinary()

        assert qgen._extract_rowcount("SET ROWCOUNT 100\nGO\nSELECT * FROM orders;") == 100
        assert qgen._extract_rowcount("set rowcount 20\ngo\nselect * from orders;") == 20

    def test_extract_rowcount_informix_syntax(self):
        from benchbox.core.tpch.queries import QGenBinary

        qgen = QGenBinary()

        assert qgen._extract_rowcount("SELECT FIRST 100 * FROM orders;") == 100
        assert qgen._extract_rowcount("select first 20 * from orders;") == 20

    def test_queries_with_limits_have_limit_clause(self):
        queries = TPCHQueries()

        test_cases = [
            (2, 100),
            (3, 10),
            (10, 20),
            (18, 100),
            (21, 100),
        ]

        for query_id, expected_limit in test_cases:
            sql = queries.get_query(query_id, seed=42)
            sql_upper = sql.upper()

            assert "LIMIT" in sql_upper, f"Query {query_id} should have LIMIT clause"

            assert f"LIMIT {expected_limit}" in sql_upper, (
                f"Query {query_id} should have LIMIT {expected_limit}, got:\n{sql}"
            )

            assert "ROWNUM" not in sql_upper, f"Query {query_id} should not have ROWNUM"
            assert "SET ROWCOUNT" not in sql_upper, f"Query {query_id} should not have SET ROWCOUNT"

    def test_queries_without_limits_no_limit_clause(self):
        queries = TPCHQueries()

        test_cases = [1, 4, 6, 8, 11, 12, 14, 15, 16, 17, 19, 22]

        for query_id in test_cases:
            sql = queries.get_query(query_id, seed=42)
            sql_upper = sql.upper()

            assert "LIMIT" not in sql_upper, f"Query {query_id} should not have LIMIT clause, got:\n{sql}"

    def test_limit_clause_determinism(self):
        queries = TPCHQueries()

        for seed in [1, 42, 999, 12345]:
            sql = queries.get_query(10, seed=seed)
            assert "LIMIT 20" in sql.upper(), f"Query 10 with seed {seed} should have LIMIT 20"

        for seed in [1, 42, 999, 12345]:
            sql = queries.get_query(3, seed=seed)
            assert "LIMIT 10" in sql.upper(), f"Query 3 with seed {seed} should have LIMIT 10"

    def test_limit_clause_with_scale_factor(self):
        queries = TPCHQueries()

        for sf in [0.1, 1.0, 10.0]:
            sql = queries.get_query(10, scale_factor=sf)
            assert "LIMIT 20" in sql.upper(), f"Query 10 at SF={sf} should have LIMIT 20"

    def test_limit_clause_format(self):
        queries = TPCHQueries()

        sql = queries.get_query(10, seed=42)

        assert sql.rstrip().endswith("LIMIT 20;") or sql.rstrip().endswith("limit 20;"), (
            f"Query 10 should end with LIMIT 20; Got:\n{sql[-100:]}"
        )


