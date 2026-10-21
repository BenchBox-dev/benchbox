# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import logging

import pytest

from benchbox.platforms.base.dialect_translation import DialectTranslationMixin
from benchbox.utils.dialect_utils import (
    NO_IDENTIFY_DIALECTS,
    resolve_sql_dialect,
    translate_sql_query,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _ExasolHost(DialectTranslationMixin):
    _dialect = "exasol"
    logger = logging.getLogger("test-exasol-identify")


class TestExasolIdentifierPolicy:
    def test_exasol_is_a_no_identify_target(self):

        assert (
            frozenset(
                {
                    "clickhouse",
                    "postgres",
                    "snowflake",
                    "exasol",
                    "questdb",
                    "netezza",
                    "greenplum",
                    "vertica",
                    "datafusion",
                }
            )
            == NO_IDENTIFY_DIALECTS
        )

    def test_resolve_passes_exasol_through(self):

        assert resolve_sql_dialect("exasol") == "exasol"
        assert resolve_sql_dialect("EXASOL") == "exasol"

    def test_tpch_ddl_emits_unquoted_identifiers(self):

        ddl = (
            "CREATE TABLE lineitem (l_orderkey INTEGER NOT NULL, l_partkey INTEGER NOT NULL, PRIMARY KEY (l_orderkey))"
        )
        result = translate_sql_query(ddl, target_dialect="exasol", source_dialect="standard")
        assert '"l_orderkey"' not in result
        assert '"lineitem"' not in result
        assert "l_orderkey" in result

    def test_tpch_query_emits_unquoted_identifiers(self):

        result = translate_sql_query(
            "SELECT l_orderkey, l_partkey FROM lineitem WHERE l_orderkey > 10",
            target_dialect="exasol",
        )
        assert '"' not in result
        assert "l_orderkey" in result

    def test_tpcds_reserved_aliases_stay_quoted(self):

        result = translate_sql_query(
            "SELECT d_year AS year, c_id AS catalog FROM dates",
            target_dialect="exasol",
        )
        assert '"year"' in result
        assert '"catalog"' in result

    def test_reserved_ddl_column_stays_quoted(self):

        result = translate_sql_query(
            "CREATE TABLE t (year INTEGER NOT NULL, id INTEGER NOT NULL)",
            target_dialect="exasol",
            source_dialect="standard",
        )
        assert '"year"' in result
        assert '"id"' not in result

    def test_platforms_path_matches_utils_path(self):

        host = _ExasolHost()
        assert '"' not in host.translate_sql("SELECT l_orderkey FROM lineitem", source_dialect="postgres")
        assert '"year"' in host.translate_sql("SELECT d_year AS year FROM dates", source_dialect="postgres")

    _UNCHANGED_TARGET_EXPECTATIONS = {
        "duckdb": 'SELECT "l_orderkey" FROM "lineitem"',
        "postgres": "SELECT l_orderkey FROM lineitem",
        "snowflake": "SELECT l_orderkey FROM lineitem",
        "clickhouse": "SELECT l_orderkey FROM lineitem",
        "sqlite": 'SELECT "l_orderkey" FROM "lineitem"',
        "trino": 'SELECT "l_orderkey" FROM "lineitem"',
    }

    @pytest.mark.parametrize("target", ["duckdb", "postgres", "snowflake", "clickhouse", "sqlite", "trino"])
    def test_other_targets_are_unchanged(self, target):

        query = "SELECT l_orderkey FROM lineitem"
        assert translate_sql_query(query, target_dialect=target) == self._UNCHANGED_TARGET_EXPECTATIONS[target]
