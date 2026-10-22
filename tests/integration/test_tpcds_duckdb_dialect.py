# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ DS (TPC-DS) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-DS specification.

# Licensed under the MIT License. See LICENSE file in the project root for details.

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


try:
    import duckdb

    DUCKDB_AVAILABLE = True
except ImportError:
    DUCKDB_AVAILABLE = False

from benchbox import TPCDS
from benchbox.core.tpcds.benchmark import TPCDSBenchmark
from benchbox.platforms.duckdb import DuckDBAdapter
from benchbox.utils.dialect_utils import SQLTranslationError, sql_translation_context


@pytest.mark.integration
@pytest.mark.tpcds
@pytest.mark.skipif(not DUCKDB_AVAILABLE, reason="DuckDB not available")
class TestTPCDSDuckDBDialectIntegration:
    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            yield Path(temp_dir)

    @pytest.fixture
    def duckdb_adapter(self, temp_dir):
        db_path = temp_dir / "test.duckdb"
        return DuckDBAdapter(database_path=str(db_path))

    @pytest.fixture
    def tpcds_benchmark(self, temp_dir):
        return TPCDSBenchmark(scale_factor=1.0, output_dir=temp_dir)

    def test_tpcds_dialect_translation_end_to_end(self, duckdb_adapter, tpcds_benchmark):

        assert duckdb_adapter.get_target_dialect() == "duckdb"

        mock_sql_server_query = """
        SELECT TOP 100 c_customer_id, c_first_name, c_last_name
        FROM customer
        WHERE c_customer_sk > 1000
        ORDER BY c_customer_id
        """

        translated = tpcds_benchmark.translate_query_text(
            mock_sql_server_query, source_dialect="tsql", target_dialect="duckdb"
        )

        assert isinstance(translated, str)
        assert len(translated) > 0
        assert "LIMIT 100" in translated, "Query should contain LIMIT 100 after translation"
        assert "TOP 100" not in translated, "Query should not contain TOP 100 after translation"

    def test_duckdb_query_syntax_validation(self, duckdb_adapter):

        benchmark = TPCDSBenchmark(scale_factor=1.0)

        mock_queries = {
            "1": """
            SELECT TOP 100 c_customer_id, c_first_name, c_last_name
            FROM customer
            WHERE c_customer_sk > 1000
            ORDER BY c_customer_id
            """,
            "2": """
            SELECT TOP 50 item_sk, item_id, item_desc
            FROM item
            WHERE item_sk > 500
            ORDER BY item_id
            """,
            "3": """
            SELECT TOP 25 store_sk, store_name
            FROM store
            ORDER BY store_name
            """,
        }

        translated_queries = {}
        for query_id, query_sql in mock_queries.items():
            translated = benchmark.translate_query_text(query_sql, source_dialect="tsql", target_dialect="duckdb")
            translated_queries[query_id] = translated

        conn = duckdb.connect(":memory:")

        try:
            for query_id, query in translated_queries.items():
                assert "LIMIT" in query, f"Query {query_id} should have LIMIT after translation"
                assert "TOP" not in query, f"Query {query_id} should not have TOP after translation"

                try:
                    explain_result = conn.execute(f"EXPLAIN {query}")
                    assert explain_result is not None, f"Query {query_id}: EXPLAIN returned no result"
                except Exception as e:
                    error_msg = str(e).lower()
                    if "syntax error" in error_msg and ("top" in error_msg or "100" in error_msg):
                        pytest.fail(f"Query {query_id} still has TOP syntax error: {e}")
                    elif "table" in error_msg and "does not exist" in error_msg:
                        pass
                    else:
                        pass
        finally:
            conn.close()

    def test_top_level_tpcds_class_dialect_support(self):

        benchmark = TPCDS(scale_factor=1.0, verbose=False)

        queries_no_dialect = benchmark.get_queries()
        queries_with_dialect = benchmark.get_queries(dialect="duckdb")

        assert isinstance(queries_no_dialect, dict)
        assert isinstance(queries_with_dialect, dict)
        assert len(queries_no_dialect) == len(queries_with_dialect)

    def test_dialect_translation_preserves_query_structure(self):

        benchmark = TPCDSBenchmark(scale_factor=1.0)

        complex_query = """
        WITH customer_totals AS (
            SELECT TOP 50 c_customer_sk, SUM(c_acctbal) as total
            FROM customer
            GROUP BY c_customer_sk
        ),
        high_value_customers AS (
            SELECT TOP 25 customer_sk, total
            FROM customer_totals
            WHERE total > 1000
            ORDER BY total DESC
        )
        SELECT TOP 10 *
        FROM high_value_customers
        ORDER BY total DESC
        """

        translated = benchmark.translate_query_text(complex_query, source_dialect="tsql", target_dialect="duckdb")

        assert "TOP 50" in complex_query
        assert "TOP 25" in complex_query
        assert "TOP 10" in complex_query

        assert "LIMIT 50" in translated, "Should translate TOP 50 to LIMIT 50"
        assert "LIMIT 25" in translated, "Should translate TOP 25 to LIMIT 25"
        assert "LIMIT 10" in translated, "Should translate TOP 10 to LIMIT 10"

        assert "TOP 50" not in translated, "Should not have TOP 50 after translation"
        assert "TOP 25" not in translated, "Should not have TOP 25 after translation"
        assert "TOP 10" not in translated, "Should not have TOP 10 after translation"

        assert (
            "WITH customer_totals AS" in translated
            or 'WITH "customer_totals" AS' in translated
            or "WITH customer_totals(" in translated.replace(" ", "")
        )
        assert "high_value_customers" in translated or '"high_value_customers"' in translated
        assert (
            "GROUP BY c_customer_sk" in translated
            or 'GROUP BY "c_customer_sk"' in translated
            or "GROUP BY" in translated
        )

    def test_dialect_error_handling_in_integration(self):

        benchmark = TPCDSBenchmark(scale_factor=1.0, verbose=False)

        malformed_sql = "SELECT * FROM WHERE invalid syntax"

        try:
            result = benchmark.translate_query_text(malformed_sql, source_dialect="tsql", target_dialect="duckdb")
            assert isinstance(result, str)
            assert len(result) > 0
        except Exception as e:
            pytest.fail(f"Should handle translation errors gracefully: {e}")

        valid_sql = "SELECT TOP 100 * FROM customer"
        try:
            result = benchmark.translate_query_text(valid_sql, source_dialect="tsql", target_dialect="tsql")
            assert isinstance(result, str)
            assert len(result) > 0
        except Exception as e:
            pytest.fail(f"Should handle same-dialect translation gracefully: {e}")

    def test_strict_dialect_translation_fails_closed(self):
        benchmark = TPCDSBenchmark(scale_factor=1.0, verbose=False)

        with patch("benchbox._vendor.sqlglot.parse", side_effect=RuntimeError("synthetic translation failure")):
            with sql_translation_context(strict=True) as outcomes:
                with pytest.raises(SQLTranslationError):
                    benchmark.translate_query_text(
                        "SELECT * FROM customer",
                        source_dialect="netezza",
                        target_dialect="duckdb",
                    )

        assert outcomes[0].status == "failed"
        assert outcomes[0].error_category == "translation_failed"

    def test_interval_syntax_normalization(self):

        benchmark = TPCDSBenchmark(scale_factor=1.0, verbose=False)

        query_add = "SELECT * FROM t WHERE d BETWEEN '2000-01-01' AND cast('2000-01-01' as date) + 60 days"
        normalized_add = benchmark._normalize_interval_syntax(query_add)
        assert "+ INTERVAL 60 DAY" in normalized_add
        assert "+ 60 days" not in normalized_add.lower()

        query_sub = "SELECT * FROM t WHERE d BETWEEN (cast('2000-01-01' as date) - 30 days) AND '2000-02-01'"
        normalized_sub = benchmark._normalize_interval_syntax(query_sub)
        assert "- INTERVAL 30 DAY" in normalized_sub
        assert "- 30 days" not in normalized_sub.lower()

        query_upper = "SELECT * FROM t WHERE d = cast('2000-01-01' as date) + 14 DAYS"
        normalized_upper = benchmark._normalize_interval_syntax(query_upper)
        assert "+ INTERVAL 14 DAY" in normalized_upper

    def test_interval_syntax_with_duckdb_execution(self, temp_dir):

        import duckdb

        benchmark = TPCDSBenchmark(scale_factor=1.0, verbose=False)

        conn = duckdb.connect(":memory:")

        conn.execute("CREATE TABLE test_dates (d DATE)")
        conn.execute("INSERT INTO test_dates VALUES ('2000-01-15'), ('2000-02-28')")

        netezza_query = "SELECT * FROM test_dates WHERE d BETWEEN '2000-01-01' AND cast('2000-01-01' as date) + 60 days"

        translated = benchmark.translate_query_text(netezza_query, "postgres", "duckdb")

        try:
            result = conn.execute(translated).fetchall()
            assert len(result) == 2
            assert "INTERVAL" in translated
        except Exception as e:
            pytest.fail(f"DuckDB execution failed with translated query: {e}")
        finally:
            conn.close()

    def test_interval_normalization_preserves_other_syntax(self):

        benchmark = TPCDSBenchmark(scale_factor=1.0, verbose=False)

        query_with_days_column = "SELECT days_since_order FROM orders WHERE notes = 'shipped in 30 days'"
        normalized = benchmark._normalize_interval_syntax(query_with_days_column)
        assert "days_since_order" in normalized
        assert "'shipped in 30 days'" in normalized
