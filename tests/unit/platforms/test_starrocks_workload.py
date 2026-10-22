# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

from benchbox.platforms.starrocks.workload import (
    StarRocksWorkloadMixin,
    _alias_derived_tables,
    _stream_load_treats_empty_as_null,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture
def mixin() -> StarRocksWorkloadMixin:

    return StarRocksWorkloadMixin.__new__(StarRocksWorkloadMixin)


class TestAliasDerivedTables:
    def test_from_subquery_gets_alias(self):
        assert _alias_derived_tables("SELECT * FROM (SELECT 1 AS x)") == "SELECT * FROM (SELECT 1 AS x) AS `_sq0`"

    def test_already_aliased_subquery_unchanged(self):
        q = "SELECT * FROM (SELECT 1 AS x) AS sq"
        assert _alias_derived_tables(q) == q

    def test_implicit_alias_is_respected(self):
        assert _alias_derived_tables("SELECT * FROM (SELECT 1 AS x) sq") == "SELECT * FROM (SELECT 1 AS x) AS sq"

    def test_in_subquery_not_aliased(self):
        q = "SELECT * FROM t WHERE id IN (SELECT id FROM s)"
        assert _alias_derived_tables(q) == q

    def test_comma_joined_subquery(self):
        assert _alias_derived_tables("SELECT * FROM t1, (SELECT id FROM t2)") == (
            "SELECT * FROM t1, (SELECT id FROM t2) AS `_sq0`"
        )

    def test_joined_subquery(self):
        assert _alias_derived_tables("SELECT * FROM t JOIN (SELECT id FROM s) ON t.id = id") == (
            "SELECT * FROM t JOIN (SELECT id FROM s) AS `_sq0` ON t.id = id"
        )

    def test_with_cte_subquery(self):
        out = _alias_derived_tables("SELECT * FROM (WITH cte AS (SELECT 1) SELECT * FROM cte)")
        assert out.endswith("AS `_sq0`")

    def test_union_follows_subquery(self):
        out = _alias_derived_tables("SELECT * FROM (SELECT 1) UNION SELECT 2")
        assert "(SELECT 1) AS `_sq0` UNION" in out

    def test_multiple_subqueries_get_distinct_aliases(self):
        assert _alias_derived_tables("SELECT * FROM (SELECT 1), (SELECT 2)") == (
            "SELECT * FROM (SELECT 1) AS `_sq0`, (SELECT 2) AS `_sq1`"
        )

    def test_nested_subqueries_are_each_aliased(self):
        assert _alias_derived_tables("SELECT * FROM (SELECT * FROM (SELECT 1 AS x))") == (
            "SELECT * FROM (SELECT * FROM (SELECT 1 AS x) AS `_sq1`) AS `_sq0`"
        )

    def test_quoted_identifiers_stay_quoted(self):
        q = "SELECT `a` AS `rank` FROM `t`"
        assert _alias_derived_tables(q) == q

    def test_unparseable_query_passes_through_with_a_warning(self, caplog):
        broken = "SELECT ((( FROM"
        with caplog.at_level("WARNING", logger="benchbox.platforms.starrocks.workload"):
            assert _alias_derived_tables(broken) == broken
        assert "unparseable StarRocks SQL" in caplog.text


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
