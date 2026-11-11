from __future__ import annotations

import pytest

from benchbox.platforms.base.mysql_wire import split_sql_statements

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestSplitSqlStatements:
    def test_single_statement_without_semicolon(self):
        assert split_sql_statements("SELECT 1") == ["SELECT 1"]

    def test_single_statement_with_semicolon(self):
        assert split_sql_statements("SELECT 1;") == ["SELECT 1"]

    def test_multiple_statements(self):
        sql = "SELECT 1;\nSELECT 2;\nSELECT 3"
        assert split_sql_statements(sql) == ["SELECT 1", "SELECT 2", "SELECT 3"]

    def test_semicolon_inside_single_quotes(self):
        sql = "SELECT 'hello;world'; SELECT 2"
        assert split_sql_statements(sql) == ["SELECT 'hello;world'", "SELECT 2"]

    def test_escaped_quote_in_string_literal(self):
        sql = "SELECT 'it\\'s; fine'; SELECT 2"
        assert split_sql_statements(sql) == ["SELECT 'it\\'s; fine'", "SELECT 2"]

    def test_semicolon_inside_double_quotes(self):
        sql = 'SELECT "col;name" FROM t; SELECT 2'
        assert split_sql_statements(sql) == ['SELECT "col;name" FROM t', "SELECT 2"]

    def test_semicolon_inside_backticks(self):
        sql = "SELECT `tbl;col` FROM t; SELECT 2"
        assert split_sql_statements(sql) == ["SELECT `tbl;col` FROM t", "SELECT 2"]

    def test_semicolon_inside_line_comment(self):
        sql = "-- comment; with semicolon\nSELECT 1; -- trailing; comment\nSELECT 2"
        assert split_sql_statements(sql) == [
            "-- comment; with semicolon\nSELECT 1",
            "-- trailing; comment\nSELECT 2",
        ]

    def test_semicolon_inside_block_comment(self):
        sql = "/* block; comment */ SELECT 1; /* another; one */ SELECT 2"
        assert split_sql_statements(sql) == [
            "/* block; comment */ SELECT 1",
            "/* another; one */ SELECT 2",
        ]

    def test_empty_and_whitespace(self):
        assert split_sql_statements("") == []
        assert split_sql_statements("   \n\t  ") == []

    def test_unclosed_block_comment_ending_in_star_does_not_crash(self):

        assert split_sql_statements("/* banner *") == ["/* banner *"]


