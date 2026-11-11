# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ H (TPC-H) - Copyright © Transaction Processing Performance Council
# This implementation is derived from TPC-H.

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest import mock

import duckdb
import pytest

from benchbox import TPCHavoc

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


@pytest.mark.integration
@pytest.mark.duckdb
@pytest.mark.tpchavoc
class TestTPCHavocDuckDBIntegration:
    @pytest.fixture
    def tpchavoc(self, small_scale_factor, temp_dir):
        with mock.patch("benchbox.core.tpch.generator.TPCHDataGenerator._find_or_build_dbgen") as mock_build:
            mock_build.return_value = temp_dir / "dbgen"
            return TPCHavoc(scale_factor=small_scale_factor, output_dir=temp_dir)

    @pytest.fixture
    def duckdb_conn(self):
        conn = duckdb.connect(":memory:")
        yield conn
        conn.close()

    @staticmethod
    def _create_duckdb_schema(tpchavoc, conn):
        for statement in tpchavoc.get_create_tables_sql(dialect="duckdb").strip().split(";"):
            if statement.strip():
                conn.execute(statement.strip())

    def test_variant_query_generation(self, tpchavoc):

        implemented = tpchavoc.get_implemented_queries()
        assert len(implemented) > 0, "Should have at least one implemented query"

        query_id = implemented[0]

        variants = tpchavoc.get_all_variants(query_id)
        assert len(variants) == 10, f"Should have 10 variants for query {query_id}"

        for variant_id, query_text in variants.items():
            assert isinstance(query_text, str), f"Variant {variant_id} should be a string"
            assert len(query_text.strip()) > 0, f"Variant {variant_id} should not be empty"
            assert "SELECT" in query_text.upper(), f"Variant {variant_id} should be a SELECT statement"

    def test_variant_descriptions(self, tpchavoc):

        implemented = tpchavoc.get_implemented_queries()
        query_id = implemented[0]

        variants_info = tpchavoc.get_all_variants_info(query_id)
        assert len(variants_info) == 10, "Should have 10 variant descriptions"

        for variant_id, info in variants_info.items():
            assert "description" in info, f"Variant {variant_id} should have description"
            assert "variant_id" in info, f"Variant {variant_id} should have variant_id"
            assert len(info["description"]) > 0, f"Variant {variant_id} description should not be empty"
            assert info["variant_id"] == variant_id, "Variant ID should match key"

    def test_get_query_supports_variant_format(self, tpchavoc):
        implemented = tpchavoc.get_implemented_queries()
        query_id = implemented[0]

        regular_query = tpchavoc.get_query(query_id)
        assert isinstance(regular_query, str)
        assert len(regular_query) > 0

        variant_query = tpchavoc.get_query(f"{query_id}_v1")
        assert isinstance(variant_query, str)
        assert len(variant_query) > 0

    def test_benchmark_info(self, tpchavoc):

        info = tpchavoc.get_benchmark_info()

        assert "benchmark_name" in info
        assert "base_benchmark" in info
        assert "scale_factor" in info
        assert "implemented_queries" in info
        assert "total_queries_with_variants" in info
        assert "variants_per_query" in info
        assert "total_query_variants" in info
        assert "variants_info" in info
        assert "validation_tolerance" in info
        assert "description" in info

        assert info["benchmark_name"] == "TPC-Havoc"
        assert info["base_benchmark"] == "TPC-H"
        assert info["variants_per_query"] == 10
        assert len(info["implemented_queries"]) > 0

        expected_total = len(info["implemented_queries"]) * 10
        assert info["total_query_variants"] == expected_total

    def test_export_variant_queries(self, tpchavoc, temp_dir):

        output_dir = temp_dir / "exported_queries"

        exported = tpchavoc.export_variant_queries(output_dir=output_dir, format="sql")

        assert len(exported) > 0, "Should export at least one file"

        for query_key, file_path in exported.items():
            assert file_path.exists(), f"Exported file {file_path} should exist"

            content = file_path.read_text()
            assert len(content) > 0, f"Exported file {file_path} should not be empty"
            assert "SELECT" in content.upper(), f"Exported file {file_path} should contain SELECT"
            assert "TPC-Havoc" in content, f"Exported file {file_path} should have TPC-Havoc header"

    def test_schema_compatibility_with_tpch(self, tpchavoc, duckdb_conn):

        sql = tpchavoc.get_create_tables_sql()

        for statement in sql.strip().split(";"):
            if statement.strip():
                duckdb_conn.execute(statement.strip())

        tables_result = duckdb_conn.execute("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'main'
            ORDER BY table_name
        """).fetchall()

        table_names = [row[0].lower() for row in tables_result]
        expected_tables = [
            "customer",
            "lineitem",
            "nation",
            "orders",
            "part",
            "partsupp",
            "region",
            "supplier",
        ]

        for expected_table in expected_tables:
            assert expected_table in table_names, f"TPC-H table {expected_table} not found"

    def test_variant_sql_syntax_validity(self, tpchavoc):

        implemented = tpchavoc.get_implemented_queries()

        for query_id in implemented[:3]:
            variants = tpchavoc.get_all_variants(query_id)

            for variant_id, query_text in variants.items():
                upper_sql = query_text.upper()
                assert "SELECT" in upper_sql, f"Q{query_id}.{variant_id} should have SELECT"
                assert "FROM" in upper_sql, f"Q{query_id}.{variant_id} should have FROM"

                assert query_text.count("(") == query_text.count(")"), (
                    f"Q{query_id}.{variant_id} should have balanced parentheses"
                )

    def test_duckdb_regression_variants_explain(self, tpchavoc, duckdb_conn):
        self._create_duckdb_schema(tpchavoc, duckdb_conn)

        regression_variants = [
            "2_v5",
            "2_v8",
            "5_v9",
            "7_v9",
            "9_v9",
            "10_v8",
            "10_v9",
            "11_v1",
            "11_v9",
            "13_v9",
            "17_v2",
            "17_v4",
            "17_v8",
        ]
        for query_id in regression_variants:
            duckdb_conn.execute(f"EXPLAIN {tpchavoc.get_query(query_id)}")

    def test_q10_v8_exists_correlates_to_outer_lineitem(self, tpchavoc, duckdb_conn):
        self._create_duckdb_schema(tpchavoc, duckdb_conn)

        sql = tpchavoc.get_query("10_v8")
        assert "l2.l_orderkey = lineitem.l_orderkey" in sql, "EXISTS must correlate to the outer lineitem"

        plan = duckdb_conn.execute(f"EXPLAIN {sql}").fetchall()[0][1]
        assert "SEMI" in plan.upper(), "EXISTS should decorrelate into a semi-join, not a self-referential scan"

    def test_correlated_subquery_variants_bind_to_outer_table(self, tpchavoc, duckdb_conn):
        self._create_duckdb_schema(tpchavoc, duckdb_conn)

        expected_correlations = {
            "11_v1": "ps2.ps_partkey = partsupp.ps_partkey",
            "17_v8": "avg_calc.l_partkey = lineitem.l_partkey",
            "2_v8": "ps2.ps_supplycost < partsupp.ps_supplycost",
        }
        plans = {}
        for query_id, correlation in expected_correlations.items():
            sql = tpchavoc.get_query(query_id)
            assert correlation in sql, (
                f"{query_id} must qualify the correlation to the outer table ({correlation}); "
                "an unqualified column silently self-binds to the inner alias"
            )
            plans[query_id] = duckdb_conn.execute(f"EXPLAIN {sql}").fetchall()[0][1]

        assert "DELIM" in plans["11_v1"].upper(), (
            "11_v1's correlated scalar subquery should decorrelate into a delimiter join"
        )


