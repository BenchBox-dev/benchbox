from __future__ import annotations

import pytest

from benchbox.core.tuning.type_mapping import map_sql_type_with_fallback

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


_SAMPLE_MAPPING = {
    "INTEGER": "INT",
    "VARCHAR": "STRING",
    "DECIMAL": "NUMERIC",
    "TIMESTAMP": "DATETIME",
}


class TestMapSqlTypeWithFallback:
    def test_exact_match(self):
        assert map_sql_type_with_fallback("INTEGER", _SAMPLE_MAPPING) == "INT"

    def test_case_insensitive_lookup(self):
        assert map_sql_type_with_fallback("integer", _SAMPLE_MAPPING) == "INT"
        assert map_sql_type_with_fallback("Integer", _SAMPLE_MAPPING) == "INT"

    def test_strips_parenthesized_precision(self):
        assert map_sql_type_with_fallback("DECIMAL(10,2)", _SAMPLE_MAPPING) == "NUMERIC"
        assert map_sql_type_with_fallback("VARCHAR(255)", _SAMPLE_MAPPING) == "STRING"

    def test_case_insensitive_with_precision(self):
        assert map_sql_type_with_fallback("decimal(18,4)", _SAMPLE_MAPPING) == "NUMERIC"

    def test_unknown_type_returns_original(self):
        assert map_sql_type_with_fallback("BLOB", _SAMPLE_MAPPING) == "BLOB"

    def test_unknown_type_with_precision_returns_original(self):
        assert map_sql_type_with_fallback("FLOAT(8)", _SAMPLE_MAPPING) == "FLOAT(8)"

    def test_empty_mapping_returns_original(self):
        assert map_sql_type_with_fallback("INTEGER", {}) == "INTEGER"

    def test_whitespace_around_base_type(self):
        assert map_sql_type_with_fallback("  INTEGER  ", _SAMPLE_MAPPING) == "INT"
        assert map_sql_type_with_fallback("  DECIMAL (10,2)", _SAMPLE_MAPPING) == "NUMERIC"

    def test_empty_string(self):
        assert map_sql_type_with_fallback("", _SAMPLE_MAPPING) == ""

    def test_whitespace_only(self):
        assert map_sql_type_with_fallback("   ", _SAMPLE_MAPPING) == "   "


