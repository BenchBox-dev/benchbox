# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.

import builtins
import logging
from unittest.mock import patch

import pytest

from benchbox.platforms.base.dialect_translation import DialectTranslationMixin
from benchbox.utils.dialect_utils import (
    sql_translation_context,
    summarize_sql_translation_outcomes,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _Adapter(DialectTranslationMixin):
    def __init__(self, dialect: str | None = None):
        self._dialect = dialect
        self.logger = logging.getLogger("test")


class TestTranslateSqlIdentityPolicy:
    def test_no_dialect_returns_unchanged(self):
        a = _Adapter(dialect=None)
        sql = "SELECT 1"
        assert a.translate_sql(sql) is sql

    def test_same_dialect_returns_unchanged(self):
        a = _Adapter(dialect="duckdb")
        sql = "SELECT 1"
        assert a.translate_sql(sql, source_dialect="duckdb") is sql

    def test_different_dialect_triggers_translation(self):
        a = _Adapter(dialect="snowflake")
        result = a.translate_sql("SELECT 1", source_dialect="duckdb")
        assert result != ""


class TestTranslateSqlDialectNormalization:
    @patch("benchbox._vendor.sqlglot.transpile")
    def test_datafusion_normalized_to_postgres(self, mock_transpile):
        mock_transpile.return_value = ["SELECT col FROM t"]
        a = _Adapter(dialect="datafusion")
        a.translate_sql("SELECT col FROM t", source_dialect="duckdb")
        _, kwargs = mock_transpile.call_args
        assert kwargs["write"] == "postgres"

    @patch("benchbox._vendor.sqlglot.transpile")
    def test_netezza_source_normalized_to_postgres(self, mock_transpile):
        mock_transpile.return_value = ["SELECT col FROM t"]
        a = _Adapter(dialect="snowflake")
        a.translate_sql("SELECT col FROM t", source_dialect="netezza")
        _, kwargs = mock_transpile.call_args
        assert kwargs["read"] == "postgres"

    @patch("benchbox._vendor.sqlglot.transpile")
    def test_duckdb_source_passes_through_unchanged(self, mock_transpile):
        mock_transpile.return_value = ["SELECT col FROM t"]
        a = _Adapter(dialect="snowflake")
        a.translate_sql("SELECT col FROM t", source_dialect="duckdb")
        _, kwargs = mock_transpile.call_args
        assert kwargs["read"] == "duckdb"


class TestTranslateSqlDuckDBPostfix:
    def test_group_by_all_preserved_duckdb_target(self):
        sql = "SELECT a, b, SUM(c) FROM t GROUP BY ALL"
        a = _Adapter(dialect="duckdb")
        result = a.translate_sql(sql, source_dialect="duckdb")
        assert "GROUP BY ALL" in result

    def test_group_by_all_not_corrupted_to_snowflake(self):
        sql = "SELECT a, SUM(c) FROM t GROUP BY ALL"
        a = _Adapter(dialect="snowflake")
        result = a.translate_sql(sql, source_dialect="duckdb")
        assert "GROUP BY ALL" in result.upper()
        assert result.endswith(";")


class TestTranslateSqlSQLitePostfix:
    def test_date_interval_rewritten_for_sqlite(self):
        sql = "SELECT DATE('1998-09-01') + INTERVAL '10' DAY FROM t"
        a = _Adapter(dialect="sqlite")
        result = a.translate_sql(sql, source_dialect="duckdb")
        assert "INTERVAL" not in result
        assert "DATETIME(DATE('1998-09-01'), '10 DAY')" in result


class TestTranslateSqlMultiStatement:
    def test_single_statement_gets_trailing_semicolon(self):
        sql = "CREATE TABLE t (id INTEGER)"
        a = _Adapter(dialect="snowflake")
        result = a.translate_sql(sql, source_dialect="duckdb")
        assert result.endswith(";")

    def test_multi_statement_rejoined(self):
        sql = "CREATE TABLE a (id INTEGER);\n\nCREATE TABLE b (name TEXT)"
        a = _Adapter(dialect="snowflake")
        result = a.translate_sql(sql, source_dialect="duckdb")
        assert "a" in result.lower()
        assert "b" in result.lower()
        assert result.endswith(";")

    def test_error_returns_original_sql(self):
        sql = "CREATE TABLE t (id INTEGER)"
        a = _Adapter(dialect="snowflake")
        with patch("benchbox._vendor.sqlglot.transpile", side_effect=Exception("boom")):
            result = a.translate_sql(sql, source_dialect="duckdb")
        assert result == sql

    def test_import_error_returns_original_sql(self):
        sql = "CREATE TABLE t (id INTEGER)"
        a = _Adapter(dialect="snowflake")

        real_import = builtins.__import__

        def raising_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "benchbox._vendor" and "sqlglot" in fromlist:
                raise ImportError("boom")
            return real_import(name, globals, locals, fromlist, level)

        with patch("builtins.__import__", side_effect=raising_import):
            result = a.translate_sql(sql, source_dialect="duckdb")
        assert result == sql


class TestTranslateSqlScopeMetadata:
    def test_mixin_translation_records_schema_scope_by_default(self):
        a = _Adapter(dialect="snowflake")
        with sql_translation_context(strict=False) as outcomes:
            a.translate_sql("CREATE TABLE t (id INTEGER)", source_dialect="standard")
            a.translate_sql("CREATE TABLE t (id INTEGER)", source_dialect="standard")

        assert len(outcomes) == 2
        assert {o.scope for o in outcomes} == {"schema_ddl"}
        summary = summarize_sql_translation_outcomes(outcomes, strict_mode=False)
        assert summary is not None
        assert summary["schema_statements_translated"] == 1
        assert "unique_queries_translated" not in summary
        assert summary["outcomes"][0]["scope"] == "schema_ddl"


class TestTranslateSqlAdapterSnapshots:
    CREATE_TABLE_SQL = "CREATE TABLE lineitem (l_orderkey BIGINT, l_partkey BIGINT)"

    @pytest.mark.parametrize(
        "dialect,expect_quoted",
        [
            ("snowflake", False),
            ("bigquery", True),
            ("duckdb", False),
            ("clickhouse", False),
            ("postgres", False),
            ("redshift", True),
            ("spark", True),
            ("trino", True),
        ],
    )
    def test_identify_snapshot_by_dialect(self, dialect, expect_quoted):
        a = _Adapter(dialect=dialect)
        result = a.translate_sql(self.CREATE_TABLE_SQL, source_dialect="duckdb")
        has_quoted = '"lineitem"' in result or "`lineitem`" in result or "[lineitem]" in result
        assert has_quoted == expect_quoted, f"dialect={dialect!r}: expected quoted={expect_quoted}, result={result!r}"


class TestTranslateSqlDelegatesToWrapper:
    def test_each_statement_goes_through_translate_sql_query_with_explicit_identify(self):
        a = _Adapter(dialect="snowflake")
        with patch(
            "benchbox.utils.dialect_utils.translate_sql_query",
            side_effect=lambda statement, *args, **kwargs: statement.upper(),
        ) as wrapper:
            result = a.translate_sql("select 1;\n\nselect 2", source_dialect="duckdb")

        assert result == "SELECT 1;\n\nSELECT 2;"
        assert wrapper.call_count == 2
        for call in wrapper.call_args_list:
            assert call.kwargs["identify"] is True
            assert call.kwargs["source_dialect"] == "duckdb"

    def test_semicolon_inside_string_literal_does_not_split_statement(self):
        a = _Adapter(dialect="duckdb")
        result = a.translate_sql("SELECT 'a;b' AS c", source_dialect="snowflake")
        assert result.count(";") == 2
        assert "'a;b'" in result
