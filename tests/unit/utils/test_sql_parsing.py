from __future__ import annotations

import pytest

from benchbox.utils.sql_parsing import find_matching_parenthesis

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestFindMatchingParenthesis:
    def test_simple_pair(self):
        assert find_matching_parenthesis("(abc)", 0) == 4

    def test_nested_parentheses(self):
        text = "(a + (b * c))"
        assert find_matching_parenthesis(text, 0) == len(text) - 1

    def test_deeply_nested(self):
        text = "(((x)))"
        assert find_matching_parenthesis(text, 0) == 6
        assert find_matching_parenthesis(text, 1) == 5
        assert find_matching_parenthesis(text, 2) == 4

    def test_parenthesis_inside_single_quoted_string_ignored(self):
        text = "('hello (world)')"
        assert find_matching_parenthesis(text, 0) == len(text) - 1

    def test_escaped_single_quote_inside_string(self):

        text = "('it''s (a) test')"
        assert find_matching_parenthesis(text, 0) == len(text) - 1

    def test_open_index_not_at_start(self):
        text = "SELECT (a + b) FROM t"
        assert find_matching_parenthesis(text, 7) == 13

    def test_unbalanced_parentheses_raises_value_error(self):
        with pytest.raises(ValueError, match="Unbalanced parentheses"):
            find_matching_parenthesis("(abc", 0)

    def test_unbalanced_nested_raises_value_error(self):
        with pytest.raises(ValueError, match="Unbalanced parentheses"):
            find_matching_parenthesis("((abc)", 0)

    def test_sql_function_call(self):
        text = "SUM(CASE WHEN x = 1 THEN y ELSE 0 END)"
        assert find_matching_parenthesis(text, 3) == len(text) - 1

    def test_multiple_groups_finds_correct_match(self):
        text = "(a) + (b)"
        assert find_matching_parenthesis(text, 0) == 2
        assert find_matching_parenthesis(text, 6) == 8


