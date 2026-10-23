# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

from benchbox.platforms.starrocks.workload import (
    StarRocksWorkloadMixin,
    _split_sql_literals,
    _stream_load_treats_empty_as_null,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture
def mixin() -> StarRocksWorkloadMixin:

    return StarRocksWorkloadMixin.__new__(StarRocksWorkloadMixin)


class TestSplitSqlLiterals:
    def test_plain_code_has_no_literal_segments(self):
        assert _split_sql_literals("SELECT 1") == [("SELECT 1", False)]

    def test_single_literal(self):
        parts = _split_sql_literals("WHERE x = 'a'")
        assert parts == [("WHERE x = ", False), ("'a'", True)]

    def test_doubled_quote_is_escape(self):
        parts = _split_sql_literals("SELECT 'it''s' FROM t")
        assert parts[0] == ("SELECT ", False)
        assert parts[1] == ("'it''s'", True)
        assert parts[2] == (" FROM t", False)

    def test_backslash_escape_inside_literal(self):
        parts = _split_sql_literals("SELECT 'a\\'b' FROM t")
        assert parts[1] == ("'a\\'b'", True)


class TestTranslateAnsiIdentifiers:
    def test_identifier_rewrite(self, mixin):
        assert mixin._translate_ansi_identifiers('SELECT "col" FROM t') == "SELECT `col` FROM t"

    def test_preserves_string_literal_contents(self, mixin):
        q = "SELECT 'has \"active\" word' FROM t"
        assert mixin._translate_ansi_identifiers(q) == q

    def test_preserves_json_shaped_literal(self, mixin):
        q = 'WHERE j = \'{"k": "v"}\''
        assert mixin._translate_ansi_identifiers(q) == q

    def test_mixed_identifier_and_literal(self, mixin):
        q = "SELECT \"col\" FROM t WHERE s = 'x'"
        assert mixin._translate_ansi_identifiers(q) == "SELECT `col` FROM t WHERE s = 'x'"


class TestTranslateAnsiSubstring:
    def test_basic_digit_arguments(self, mixin):
        assert mixin._translate_ansi_substring("SUBSTRING(c_phone FROM 1 FOR 2)") == "SUBSTRING(c_phone, 1, 2)"

    def test_expression_arguments(self, mixin):
        assert (
            mixin._translate_ansi_substring("SUBSTRING(col FROM offset + 1 FOR n - 1)")
            == "SUBSTRING(col, offset + 1, n - 1)"
        )

    def test_literal_is_preserved(self, mixin):
        q = "WHERE msg = 'SUBSTRING(x FROM 1 FOR 2)' AND t = SUBSTRING(y FROM 3 FOR 4)"
        assert mixin._translate_ansi_substring(q) == (
            "WHERE msg = 'SUBSTRING(x FROM 1 FOR 2)' AND t = SUBSTRING(y, 3, 4)"
        )

    def test_nested_function_emits_warning(self, mixin, caplog):

        with caplog.at_level("WARNING", logger="benchbox.platforms.starrocks.workload"):
            out = mixin._translate_ansi_substring("SUBSTRING(LOWER(col) FROM 1 FOR 3)")

        assert "LOWER(col)" in out
        assert any("ANSI 'FROM" in rec.message for rec in caplog.records)


class TestQuoteReservedAliases:
    @pytest.mark.parametrize(
        "alias",
        ["character", "rank", "order", "group", "key", "value", "row_number", "dense_rank"],
    )
    def test_seeded_reserved_words_are_quoted(self, mixin, alias):
        q = f"SELECT x AS {alias} FROM t"
        assert mixin._quote_reserved_aliases(q) == f"SELECT x AS `{alias}` FROM t"

    def test_non_reserved_alias_left_alone(self, mixin):
        q = "SELECT x AS total FROM t"
        assert mixin._quote_reserved_aliases(q) == q

    def test_literal_contents_not_quoted(self, mixin):

        q = "SELECT 'prefix AS rank suffix' AS total FROM t"
        assert mixin._quote_reserved_aliases(q) == q


class TestInjectMissingSubqueryAliases:
    def test_from_subquery_gets_alias(self, mixin):
        q = "SELECT * FROM (SELECT 1 AS x)"
        out = mixin._inject_missing_subquery_aliases(q)
        assert "AS `_sq0`" in out

    def test_already_aliased_subquery_unchanged(self, mixin):
        q = "SELECT * FROM (SELECT 1 AS x) AS sq"
        assert mixin._inject_missing_subquery_aliases(q) == q

    def test_implicit_alias_is_respected(self, mixin):

        q = "SELECT * FROM (SELECT 1 AS x) sq"
        assert mixin._inject_missing_subquery_aliases(q) == q

    def test_in_subquery_not_aliased(self, mixin):
        q = "SELECT * FROM t WHERE id IN (SELECT id FROM s)"
        assert mixin._inject_missing_subquery_aliases(q) == q

    def test_comma_joined_subquery(self, mixin):
        q = "SELECT * FROM t1, (SELECT id FROM t2)"
        out = mixin._inject_missing_subquery_aliases(q)
        assert "AS `_sq0`" in out

    def test_with_cte_subquery(self, mixin):
        q = "SELECT * FROM (WITH cte AS (SELECT 1) SELECT * FROM cte)"
        out = mixin._inject_missing_subquery_aliases(q)
        assert "AS `_sq0`" in out

    def test_union_follows_subquery(self, mixin):

        q = "SELECT * FROM (SELECT 1) UNION SELECT 2"
        out = mixin._inject_missing_subquery_aliases(q)
        assert "AS `_sq0`" in out

    def test_multiple_subqueries_get_distinct_aliases(self, mixin):

        q = "SELECT * FROM (SELECT 1), (SELECT 2)"
        out = mixin._inject_missing_subquery_aliases(q)
        assert "AS `_sq0`" in out
        assert "AS `_sq1`" in out


class TestOptimizeTableDefinition:
    def test_primary_key_preserved_when_leading(self, mixin):
        ddl = "CREATE TABLE t (\n  id INT NOT NULL,\n  name VARCHAR(50),\n  PRIMARY KEY (id)\n)"
        out = mixin._translate_schema_statement(ddl)
        assert "PRIMARY KEY (`id`)" in out
        assert "DUPLICATE KEY" not in out

    def test_falls_back_to_duplicate_key_when_pk_not_leading(self, mixin):

        ddl = "CREATE TABLE t (\n  name VARCHAR(50),\n  id INT NOT NULL,\n  PRIMARY KEY (id)\n)"
        out = mixin._translate_schema_statement(ddl)
        assert "DUPLICATE KEY" in out
        assert "PRIMARY KEY" not in out

    def test_timestamp_column_name_preserved(self, mixin):

        ddl = "CREATE TABLE t (id INT, timestamp VARCHAR(20))"
        out = mixin._translate_schema_statement(ddl)
        assert "`timestamp` VARCHAR(20)" in out
        assert "DATETIME" not in out

    def test_uppercase_timestamp_type_converted(self, mixin):
        ddl = "CREATE TABLE t (id INT, ts TIMESTAMP)"
        out = mixin._translate_schema_statement(ddl)
        assert "`ts` DATETIME" in out


class TestStreamLoadEmptyAsNull:
    def test_pipe_delimited_tpc_keeps_empty_literal(self):
        assert _stream_load_treats_empty_as_null("|") is False

    def test_unannotated_comma_csv_still_treats_empty_as_null(self):

        assert _stream_load_treats_empty_as_null(",") is True

    def test_tab_delimited_keeps_empty_literal(self):
        assert _stream_load_treats_empty_as_null("\t") is False
