# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.

from typing import Any, cast

import pytest

from benchbox.utils.dialect_utils import (
    SQLITE_DISCOUNT_BOUNDS,
    SQLTranslationError,
    UnknownSqlDialectError,
    apply_rewrite,
    resolve_sql_dialect,
    split_sql_statements,
    sql_translation_context,
    summarize_sql_translation_outcomes,
    translate_sql_query,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


def _fold_sqlite(query: str) -> str:
    return apply_rewrite("fold_between_literal_arithmetic", query, "sqlite", **SQLITE_DISCOUNT_BOUNDS)


def _roundtrip_sqlite(query: str) -> str:
    from benchbox._vendor import sqlglot

    return sqlglot.parse_one(query, read="sqlite").sql(dialect="sqlite")


class TestSQLiteDiscountBoundaries:
    def test_qualified_nested_bounds_and_translation_metadata(self):
        from benchbox._vendor import sqlglot

        query = (
            "SELECT 0.06 + 0.01 FROM lineitem AS l "
            "WHERE l.l_discount BETWEEN (0.06 - 0.02 + 0.01) AND (0.06 + 0.02 - 0.01)"
        )
        with sql_translation_context(strict=True) as outcomes:
            translated = translate_sql_query(query, target_dialect="sqlite")
        expected = "SELECT 0.06 + 0.01 FROM lineitem AS l WHERE l.l_discount BETWEEN 0.05 AND 0.07"
        expected_tree = sqlglot.parse_one(expected, read="sqlite")
        assert sqlglot.parse_one(translated, read="sqlite") == sqlglot.parse_one(
            expected_tree.sql(dialect="sqlite", identify=True), read="sqlite"
        )
        assert len(outcomes) == 1
        assert outcomes[0].status == "success"
        assert outcomes[0].normalized_target_dialect == "sqlite"
        assert outcomes[0].query_fingerprint

    @pytest.mark.parametrize("center, low, high", [("0.06", "0.05", "0.07"), ("0.07", "0.06", "0.08")])
    def test_only_discount_bounds_are_folded(self, center, low, high):
        from benchbox._vendor import sqlglot
        from benchbox._vendor.sqlglot import exp

        query = (
            f"SELECT 0.06 + 0.01 AS untouched FROM lineitem "
            f"WHERE l_discount BETWEEN {center} - 0.01 AND {center} + 0.01 AND l_quantity < 25"
        )
        actual = sqlglot.parse_one(translate_sql_query(query, target_dialect="sqlite"), read="sqlite")
        expected = sqlglot.parse_one(
            f"SELECT 0.06 + 0.01 AS untouched FROM lineitem "
            f"WHERE l_discount BETWEEN {low} AND {high} AND l_quantity < 25",
            read="sqlite",
        )
        assert actual == sqlglot.parse_one(expected.sql(dialect="sqlite", identify=True), read="sqlite")
        between = actual.find(exp.Between)
        assert isinstance(between.args["low"], exp.Literal)
        assert isinstance(between.args["high"], exp.Literal)

    @pytest.mark.parametrize(
        "predicate",
        [
            "l_discount BETWEEN center - 0.01 AND 0.06 + 0.01",
            "l_discount BETWEEN 0.06 - 0.01 AND center + 0.01",
            "other_discount BETWEEN 0.06 - 0.01 AND 0.06 + 0.01",
            "l_discount > 0.06 + 0.01",
            "l_discount BETWEEN '0.06' - 0.01 AND '0.06' + 0.01",
            "l_discount BETWEEN 0.06 * 0.01 AND 0.06 + 0.01",
            "l_discount BETWEEN 0.05 AND 0.06 + 0.01",
            "l_discount BETWEEN 0.06 - 0.01 AND 0.07",
            "l_discount BETWEEN 1 - 1 AND 1 + 1",
            "l_discount BETWEEN 0.06 - 1 AND 0.06 + 1",
            "l_discount BETWEEN (9223372036854775807 + 1 - 9223372036854775807) AND (1 + 1)",
            "l_discount BETWEEN 0.01 - 0.06 AND 0.06 + 0.01",
            "l_discount BETWEEN 6e-2 - 1e-2 AND 6e-2 + 1e-2",
            "l_discount BETWEEN (10000000000000000000000000000.0 + 9.0 - 10000000000000000000000000000.0) AND (20.0 + 0.0)",
            "l_discount BETWEEN (100000000.0 + 0.0000000001 - 100000000.0) AND (0.06 + 0.01)",
            f"l_discount BETWEEN (1{'0' * 400}.0 - 1{'0' * 400}.0) AND (0.06 + 0.01)",
            "l_discount BETWEEN 0.01 + 0.01 + 0.01 + 0.01 + 0.01 AND 0.06 + 0.01",
            "l_discount BETWEEN 0.0000001 + 0.0000001 AND 0.06 + 0.01",
            "l_discount BETWEEN 100.0 - 99.0 AND 0.06 + 0.01",
        ],
    )
    def test_unrelated_or_nonliteral_expressions_are_unchanged(self, predicate):
        query = f"SELECT 0.06 + 0.01 FROM lineitem WHERE {predicate}"
        assert _fold_sqlite(query) == _roundtrip_sqlite(query)

    def test_integer_overflow_bounds_keep_sqlite_semantics(self):
        import sqlite3

        query = (
            "WITH lineitem AS (SELECT 0 AS l_discount) SELECT * FROM lineitem "
            "WHERE l_discount BETWEEN (9223372036854775807 + 1 - 9223372036854775807) AND (1 + 1)"
        )
        connection = sqlite3.connect(":memory:")
        try:
            expected = connection.execute(query).fetchall()
            assert expected == [(0,)]
            translated = translate_sql_query(query, target_dialect="sqlite")
            assert connection.execute(translated).fetchall() == expected
        finally:
            connection.close()

    def test_bare_dot_decimal_bounds_are_folded(self):
        import sqlite3

        query = "SELECT l_discount FROM lineitem WHERE l_discount BETWEEN .06 - .01 AND .06 + .01 ORDER BY l_discount"
        connection = sqlite3.connect(":memory:")
        try:
            connection.execute("CREATE TABLE lineitem (l_discount DECIMAL(15,2))")
            connection.executemany("INSERT INTO lineitem VALUES (?)", [(v,) for v in ("0.04", "0.05", "0.06", "0.07")])
            assert connection.execute(query).fetchall() == [(0.05,), (0.06,)]
            translated = translate_sql_query(query, target_dialect="sqlite")
            assert connection.execute(translated).fetchall() == [(0.05,), (0.06,), (0.07,)]
        finally:
            connection.close()

    def test_two_decimal_discount_domain_matches_exact_decimal_semantics(self):
        import sqlite3
        from decimal import Decimal

        values = [f"{cents / 100:.2f}" for cents in range(101)]
        connection = sqlite3.connect(":memory:")
        try:
            connection.execute("CREATE TABLE lineitem (l_discount DECIMAL(15,2))")
            connection.executemany("INSERT INTO lineitem VALUES (?)", [(value,) for value in values])
            original_wrong = rewritten_wrong = checked = 0
            for cents in range(101):
                for delta in ("0.01", "0.02", "0.05"):
                    low, high = (
                        Decimal(f"{cents / 100:.2f}") - Decimal(delta),
                        Decimal(f"{cents / 100:.2f}") + Decimal(delta),
                    )
                    if low < 0:
                        continue
                    query = (
                        f"SELECT l_discount FROM lineitem WHERE l_discount BETWEEN {cents / 100:.2f} - {delta} "
                        f"AND {cents / 100:.2f} + {delta} ORDER BY l_discount"
                    )
                    expected = [float(value) for value in values if low <= Decimal(value) <= high]
                    checked += 1
                    original_wrong += [row[0] for row in connection.execute(query).fetchall()] != expected
                    rewritten = _fold_sqlite(query)
                    rewritten_wrong += [row[0] for row in connection.execute(rewritten).fetchall()] != expected
            assert checked > 250
            assert original_wrong > 0, "the sweep must exercise the SQLite boundary defect"
            assert rewritten_wrong == 0
        finally:
            connection.close()

    @pytest.mark.parametrize(
        "query",
        [
            "SELECT * FROM orders WHERE l_discount BETWEEN 0.06 - 0.01 AND 0.06 + 0.01",
            "SELECT * FROM t WHERE l_discount BETWEEN 0.06 - 0.01 AND 0.06 + 0.01",
            "SELECT 'lineitem' AS note FROM t WHERE l_discount BETWEEN 0.06 - 0.01 AND 0.06 + 0.01",
            "SELECT * FROM t /* lineitem */ WHERE l_discount BETWEEN 0.06 - 0.01 AND 0.06 + 0.01",
        ],
    )
    def test_queries_that_do_not_read_lineitem_are_not_folded(self, query):
        assert _fold_sqlite(query) == _roundtrip_sqlite(query)

    def test_a_query_that_reads_lineitem_is_folded_in_a_join_and_a_subquery(self):
        joined = (
            "SELECT 1 FROM orders o JOIN lineitem l ON l.l_orderkey = o.o_orderkey "
            "WHERE l.l_discount BETWEEN 0.06 - 0.01 AND 0.06 + 0.01"
        )
        assert "BETWEEN 0.05 AND 0.07" in _fold_sqlite(joined)
        nested = "SELECT * FROM (SELECT l_discount FROM lineitem) WHERE l_discount BETWEEN 0.06 - 0.01 AND 0.06 + 0.01"
        assert "BETWEEN 0.05 AND 0.07" in _fold_sqlite(nested)

    def test_value_inside_the_double_noise_band_follows_exact_decimal_semantics(self):
        import sqlite3

        query = (
            "WITH lineitem AS (SELECT 0.00000000000000001 AS l_discount) SELECT l_discount FROM lineitem "
            "WHERE l_discount BETWEEN (0.0 + 0.0) AND (0.1 + 0.2 - 0.3)"
        )
        connection = sqlite3.connect(":memory:")
        try:
            assert connection.execute(query).fetchall() == [(1e-17,)]
            assert connection.execute(_fold_sqlite(query)).fetchall() == []
        finally:
            connection.close()

    @pytest.mark.parametrize(
        "rows, low, high, expected",
        [
            (
                (8.0, 9.0),
                "(10000000000000000000000000000.0 + 9.0 - 10000000000000000000000000000.0)",
                "(20.0 + 0.0)",
                [8.0, 9.0],
            ),
            ((0.0, 0.05), "(100000000.0 + 0.0000000001 - 100000000.0)", "(0.06 + 0.01)", [0.0, 0.05]),
            ((0.05,), f"(1{'0' * 400}.0 - 1{'0' * 400}.0)", "(0.06 + 0.01)", []),
        ],
    )
    def test_cancellation_and_overflow_bounds_keep_sqlite_semantics(self, rows, low, high, expected):
        import sqlite3

        values = " UNION ALL ".join(f"SELECT {value} AS l_discount" for value in rows)
        query = (
            f"WITH lineitem AS ({values}) SELECT l_discount FROM lineitem "
            f"WHERE l_discount BETWEEN {low} AND {high} ORDER BY l_discount"
        )
        assert _fold_sqlite(query) == _roundtrip_sqlite(query)
        connection = sqlite3.connect(":memory:")
        try:
            assert [value for (value,) in connection.execute(query).fetchall()] == expected
            translated = translate_sql_query(query, target_dialect="sqlite")
            assert [value for (value,) in connection.execute(translated).fetchall()] == expected
        finally:
            connection.close()

    @pytest.mark.parametrize("dialect", ["duckdb", "postgres", "mysql"])
    def test_non_sqlite_bounds_are_unchanged(self, dialect):
        from benchbox._vendor import sqlglot
        from benchbox._vendor.sqlglot import exp

        query = "SELECT * FROM lineitem WHERE l_discount BETWEEN 0.06 - 0.01 AND 0.06 + 0.01"
        actual = sqlglot.parse_one(translate_sql_query(query, target_dialect=dialect), read=dialect)
        between = actual.find(exp.Between)
        assert isinstance(between.args["low"], exp.Sub)
        assert isinstance(between.args["high"], exp.Add)

    @pytest.mark.parametrize("surface", ["seed42", "bulk"])
    def test_public_q6_real_sqlite_duckdb_parity(self, surface):
        from decimal import Decimal

        import duckdb

        from benchbox.core.tpch.benchmark import TPCHBenchmark
        from benchbox.platforms.sqlite import SQLiteAdapter

        benchmark = TPCHBenchmark(scale_factor=0.01)
        if surface == "seed42":
            queries = {
                dialect: benchmark.get_query(6, seed=42, scale_factor=0.01, dialect=dialect)
                for dialect in ("sqlite", "duckdb")
            }
            shipdate = "1997-06-01"
        else:
            queries = {
                "sqlite": benchmark.get_queries(dialect="sqlite")["6"],
                "duckdb": benchmark.get_query(6, dialect="duckdb"),
            }
            assert queries["sqlite"] == benchmark.get_query(6, dialect="sqlite")
            shipdate = "1994-06-01"
        adapter = SQLiteAdapter(database_path=":memory:")
        sqlite = adapter.create_connection()
        reference = duckdb.connect(":memory:")
        rows = [("100.00", discount, shipdate, "1.00") for discount in ("0.04", "0.05", "0.06", "0.07", "0.08")]
        try:
            for connection in (sqlite, reference):
                connection.execute(
                    "CREATE TABLE lineitem (l_extendedprice DECIMAL(15,2), l_discount DECIMAL(15,2), "
                    "l_shipdate DATE, l_quantity DECIMAL(15,2))"
                )
                connection.executemany("INSERT INTO lineitem VALUES (?, ?, ?, ?)", rows)
            result = adapter.execute_query(sqlite, queries["sqlite"], "6", validate_row_count=False)
            expected = reference.execute(queries["duckdb"]).fetchall()
            assert expected == [(Decimal("18.0000"),)]
            assert result["results"] == [(18.0,)]
            assert [(Decimal(str(value)),) for (value,) in result["results"]] == expected
        finally:
            sqlite.close()
            reference.close()

    @pytest.mark.parametrize(
        "center, discounts, expected",
        [
            ("0.06", ("0.04", "0.05", "0.06", "0.07", "0.08"), 18),
            ("0.07", ("0.05", "0.06", "0.07", "0.08", "0.09"), 21),
        ],
    )
    def test_real_engine_boundary_membership(self, center, discounts, expected):
        import duckdb

        from benchbox.platforms.sqlite import SQLiteAdapter

        adapter = SQLiteAdapter(database_path=":memory:")
        sqlite = adapter.create_connection()
        reference = duckdb.connect(":memory:")
        source = f"SELECT l_discount BETWEEN {center} - 0.01 AND {center} + 0.01 FROM lineitem ORDER BY l_discount"
        aggregate = (
            f"SELECT SUM(100 * l_discount) FROM lineitem WHERE l_discount BETWEEN {center} - 0.01 AND {center} + 0.01"
        )
        try:
            for connection in (sqlite, reference):
                connection.execute("CREATE TABLE lineitem (l_discount DECIMAL(15,2))")
                connection.executemany("INSERT INTO lineitem VALUES (?)", [(value,) for value in discounts])
            for source_query, expected_rows in [(source, [(0,), (1,), (1,), (1,), (0,)]), (aggregate, [(expected,)])]:
                actual = adapter.execute_query(
                    sqlite, translate_sql_query(source_query, target_dialect="sqlite"), "6", validate_row_count=False
                )["results"]
                assert actual == reference.execute(source_query).fetchall() == expected_rows
        finally:
            sqlite.close()
            reference.close()


@pytest.mark.parametrize("surface", ["utility", "mixin", "benchmark"])
@pytest.mark.parametrize(
    "expression, expected",
    [
        ("DATE '1994-01-01'", "1994-01-01"),
        ("CAST('1994-01-01' AS DATE)", "1994-01-01"),
        ("DATE '1994-01-01' + INTERVAL '1' DAY", "1994-01-02"),
        ("DATE '1994-01-02' - INTERVAL '1' DAY", "1994-01-01"),
        ("CAST('1994-01-01' AS DATE) + INTERVAL '7' DAY", "1994-01-08"),
        ("EXTRACT(YEAR FROM DATE '1994-06-15')", 1994),
        ("EXTRACT(MONTH FROM DATE '1994-06-15')", 6),
        ("EXTRACT(DAY FROM DATE '1994-06-15')", 15),
        ("EXTRACT(YEAR FROM CAST('1994-06-15' AS DATE))", 1994),
        ("EXTRACT(MONTH FROM CAST('1994-06-15' AS DATE))", 6),
        ("EXTRACT(DAY FROM CAST('1994-06-15' AS DATE))", 15),
        ("CAST(DATE '1994-01-01' AS TEXT)", "1994-01-01"),
        ("DATE '2000-02-28' + INTERVAL '1' DAY", "2000-02-29"),
        ("DATE '2000-03-01' - INTERVAL '1' DAY", "2000-02-29"),
        ("EXTRACT(DAY FROM DATE '1994-01-01' + INTERVAL '1' DAY)", 2),
        ("EXTRACT(YEAR FROM DATE '2020-06-01') / 3 > 673", 1),
        ("CAST(EXTRACT(YEAR FROM DATE '2020-06-01') AS INTEGER) / 3 > 673", 0),
        ("2020 / 3 > 673", 0),
        ("-EXTRACT(YEAR FROM DATE '2020-06-01') / 3 < -673", 1),
        ("(SELECT y / 3 > 673 FROM (SELECT EXTRACT(YEAR FROM DATE '2020-06-01') AS y) AS t)", 1),
        ("CAST(NULL AS DATE) + INTERVAL '1' DAY", None),
        ("DATE '2024-02-29' + INTERVAL '1' HOUR", "2024-02-29 01:00:00"),
    ],
)
def test_bundled_sqlite_translation_execution(surface, expression, expected):
    import logging
    import sqlite3
    from types import SimpleNamespace

    from benchbox.base import BaseBenchmark
    from benchbox.platforms.base.dialect_translation import DialectTranslationMixin

    query = f"SELECT {expression} AS result"
    if surface == "utility":
        translated = translate_sql_query(query, "sqlite", source_dialect="netezza", identify=True, strict=True)
    elif surface == "mixin":
        adapter = SimpleNamespace(dialect="sqlite", logger=logging.getLogger(__name__))
        translated = DialectTranslationMixin.translate_sql(adapter, query, source_dialect="netezza", strict=True)
    else:
        benchmark = SimpleNamespace(get_query=lambda query_id: query)
        translated = BaseBenchmark.translate_query(benchmark, "date-case", "sqlite")
    with sqlite3.connect(":memory:") as connection:
        assert connection.execute(translated).fetchall() == [(expected,)]


class TestDialectResolution:
    @pytest.mark.parametrize("alias", ["ansi", "standard", "ANSI", "Standard"])
    def test_generic_names_resolve_to_postgres(self, alias):
        assert resolve_sql_dialect(alias) == "postgres"

    @pytest.mark.parametrize(
        "name", ["netezza", "NETEZZA", "Netezza", "greenplum", "vertica", "datafusion", "questdb", "duckdb", "exasol"]
    )
    def test_fork_dialects_resolve_to_themselves(self, name):
        assert resolve_sql_dialect(name) == name.lower()

    def test_influxdb_resolves_to_the_datafusion_engine(self):
        assert resolve_sql_dialect("influxdb") == "datafusion"

    def test_hyphen_and_underscore_are_interchangeable(self):
        assert resolve_sql_dialect("pg-duckdb") == resolve_sql_dialect("pg_duckdb") == "postgres"
        assert resolve_sql_dialect(" Clickhouse-Local ") == "clickhouse"

    @pytest.mark.parametrize("name", ["", None, "nonexistent_invalid_dialect_12345", "polars", "dataframe"])
    def test_unknown_platform_raises(self, name):
        with pytest.raises(UnknownSqlDialectError):
            resolve_sql_dialect(cast(Any, name))

    def test_every_sql_platform_in_the_manifest_resolves_to_a_fork_dialect(self):
        from benchbox.core.config_inheritance import resolve_dialect_for_query_translation
        from benchbox.core.platform_manifest import PLATFORM_MANIFEST

        sql_platforms = [
            entry.key for entry in PLATFORM_MANIFEST if entry.metadata.get("capabilities", {}).get("supports_sql")
        ]
        assert sql_platforms
        for key in sql_platforms:
            assert resolve_sql_dialect(resolve_dialect_for_query_translation(key))

    def test_every_adapter_target_dialect_resolves_to_a_fork_dialect(self):
        from benchbox.platforms.questdb import QUESTDB_DIALECT

        assert resolve_sql_dialect(QUESTDB_DIALECT) == "questdb"


class TestSQLTranslation:
    def test_translate_simple_query(self):
        query = "SELECT * FROM orders"
        result = translate_sql_query(query, target_dialect="duckdb")
        assert result
        assert "orders" in result.lower()

    def test_translate_to_date_netezza_to_duckdb(self):
        query = "SELECT TO_DATE('1995-03-15', 'yyyy-MM-dd')"
        result = translate_sql_query(query, target_dialect="duckdb", source_dialect="netezza")
        assert "STRPTIME" in result.upper() or "CAST" in result.upper()
        assert "1995-03-15" in result

    def test_translate_json_objectagg_postgres_to_duckdb(self):
        query = "SELECT JSON_OBJECTAGG(k, v) FROM t"
        result = translate_sql_query(query, target_dialect="duckdb", source_dialect="postgres")
        assert "JSON_GROUP_OBJECT" in result.upper()

    def test_translate_with_identify_quotes_identifiers(self):
        query = "SELECT * FROM orders"
        result = translate_sql_query(query, target_dialect="duckdb", identify=True)
        assert '"orders"' in result or "`orders`" in result or result == query

    def test_translate_without_identify(self):
        query = "SELECT * FROM orders"
        result = translate_sql_query(query, target_dialect="duckdb", identify=False)
        assert "orders" in result.lower()

    UNKNOWN_PLATFORM_KEYS = (
        "nonexistent_invalid_dialect_12345",
        "polars",
        "pandas",
        "dataframe",
        "",
        "   ",
        "clickhouse-nonexistent",
        "duckdb ; drop table x",
    )

    @pytest.mark.parametrize("strict", [True, False, None])
    @pytest.mark.parametrize("key", UNKNOWN_PLATFORM_KEYS)
    def test_unknown_target_always_raises(self, key, strict):
        with sql_translation_context(strict=False) as outcomes:
            with pytest.raises(UnknownSqlDialectError):
                translate_sql_query("SELECT * FROM orders", target_dialect=key, strict=strict)
        assert outcomes == []

    @pytest.mark.parametrize("strict", [True, False, None])
    @pytest.mark.parametrize("key", UNKNOWN_PLATFORM_KEYS)
    def test_unknown_source_always_raises(self, key, strict):
        with sql_translation_context(strict=False) as outcomes:
            with pytest.raises(UnknownSqlDialectError):
                translate_sql_query("SELECT * FROM orders", "duckdb", source_dialect=key, strict=strict)
        assert outcomes == []

    @pytest.mark.parametrize("strict_context", [True, False])
    def test_unknown_dialect_raises_under_strict_and_lenient_contexts(self, strict_context):
        with sql_translation_context(strict=strict_context):
            with pytest.raises(UnknownSqlDialectError):
                translate_sql_query("SELECT 1", "not_a_dialect")

    def test_unknown_dialect_raises_for_none(self):
        with pytest.raises(UnknownSqlDialectError):
            translate_sql_query("SELECT 1", cast(Any, None))

    @pytest.mark.parametrize("key", UNKNOWN_PLATFORM_KEYS)
    def test_split_sql_statements_raises_for_unknown_dialect(self, key):
        with pytest.raises(UnknownSqlDialectError):
            split_sql_statements("SELECT 1; SELECT 2", key)

    def test_text_processor_parameters_are_removed(self):
        import inspect

        parameters = inspect.signature(translate_sql_query).parameters
        assert "pre_processors" not in parameters
        assert "post_processors" not in parameters
        with pytest.raises(TypeError):
            translate_sql_query("SELECT 1", "duckdb", pre_processors=[str])

    def test_rewrites_apply_to_the_tree_before_generation(self):
        result = translate_sql_query(
            "SELECT a / b FROM t",
            target_dialect="databricks",
            source_dialect="databricks",
            identify=False,
            rewrites=[("safe_division", {})],
        )
        assert result == "SELECT TRY_DIVIDE(a, b) FROM t"

    def test_failing_rewrite_falls_back_and_records_outcome(self):
        query = "SELECT * FROM orders"
        with sql_translation_context(strict=False) as outcomes:
            result = translate_sql_query(query, "duckdb", "netezza", rewrites=[("no_such_rewrite", {})])
        assert result == query
        assert outcomes[0].status == "fallback"
        assert outcomes[0].warning_category == "translation_failed"

    def test_failing_rewrite_raises_in_strict_mode(self):
        with sql_translation_context(strict=True) as outcomes:
            with pytest.raises(SQLTranslationError):
                translate_sql_query("SELECT 1", "duckdb", "netezza", rewrites=[("no_such_rewrite", {})])
        assert outcomes[0].status == "failed"
        assert outcomes[0].error_category == "translation_failed"

    def test_empty_input_translates_to_empty_string(self):
        assert translate_sql_query("", "duckdb", "postgres") == ""
        assert translate_sql_query(";", "duckdb", "postgres") == ""

    def test_translate_default_source_dialect_is_netezza(self):
        query = "SELECT 1 AS test_col"
        result = translate_sql_query(query, target_dialect="duckdb")
        assert result
        assert "test_col" in result.lower() or "1" in result

    def test_translate_preserves_query_on_translation_error(self):
        query = "SELEC FROM FROM orders ((("
        assert translate_sql_query(query, target_dialect="duckdb") == query

    def test_translate_clickhouse_to_duckdb(self):
        query = "SELECT * FROM orders LIMIT 10"
        result = translate_sql_query(query, target_dialect="duckdb", source_dialect="clickhouse")
        assert "orders" in result.lower()
        assert "LIMIT" in result.upper() or "limit" in result.lower()

    def test_translate_preserves_complex_queries(self):
        query = """
            SELECT o.order_id, c.customer_name
            FROM orders o
            JOIN customers c ON o.customer_id = c.customer_id
            WHERE o.order_date >= '2024-01-01'
            ORDER BY o.order_id
            LIMIT 100
        """
        result = translate_sql_query(query, target_dialect="duckdb")
        assert "order_id" in result.lower()
        assert "customer_name" in result.lower()
        assert "join" in result.lower()
        assert "where" in result.lower()

    def test_translate_duckdb_preserves_order_by_all_keyword(self):
        query = """
            SELECT r_name, n_name, COUNT(*) AS supplier_count
            FROM region r
            JOIN nation n ON n.n_regionkey = r.r_regionkey
            GROUP BY r_name, n_name
            ORDER BY ALL
        """
        result = translate_sql_query(query, target_dialect="duckdb", source_dialect="netezza", identify=True)
        assert "ORDER BY ALL" in result.upper()
        assert 'ORDER BY "ALL"' not in result.upper()

    def test_translate_duckdb_preserves_group_by_all_keyword(self):
        query = """
            SELECT n_name, r_name, COUNT(*) AS nation_count
            FROM nation n
            JOIN region r ON n.n_regionkey = r.r_regionkey
            GROUP BY ALL
            ORDER BY n_name
        """
        result = translate_sql_query(query, target_dialect="duckdb", source_dialect="netezza", identify=True)
        assert "GROUP BY ALL" in result.upper()
        assert 'GROUP BY "ALL"' not in result.upper()

    def test_translate_sqlite_rewrites_interval_and_extract(self):
        query = """
            SELECT EXTRACT(YEAR FROM l_shipdate) AS ship_year
            FROM lineitem
            WHERE l_shipdate < DATE '1994-01-01' + INTERVAL '1' YEAR
        """
        result = translate_sql_query(query, target_dialect="sqlite", source_dialect="postgres")

        assert "INTERVAL" not in result.upper()
        assert "EXTRACT" not in result.upper()
        assert "DATE('1994-01-01', '+1 year')" in result
        assert "STRFTIME('%Y'," in result

    def test_translate_sqlite_rewrites_time_extract_and_date_trunc(self):
        query = """
            SELECT
                EXTRACT(HOUR FROM EventTime),
                EXTRACT(MINUTE FROM EventTime),
                DATE_TRUNC('MINUTE', EventTime)
            FROM hits
        """

        result = translate_sql_query(query, target_dialect="sqlite", source_dialect="clickhouse")

        assert "EXTRACT" not in result.upper()
        assert "DATE_TRUNC" not in result.upper()
        assert "STRFTIME('%H'," in result
        assert "STRFTIME('%M'," in result
        assert "STRFTIME('%Y-%m-%d %H:%M:00'," in result

    def test_translation_context_records_success_metadata(self):
        query = "SELECT * FROM orders"

        with sql_translation_context(strict=False) as outcomes:
            result = translate_sql_query(query, target_dialect="duckdb", source_dialect="netezza")

        assert result
        assert len(outcomes) == 1
        assert outcomes[0].status == "success"
        assert outcomes[0].source_dialect == "netezza"
        assert outcomes[0].target_dialect == "duckdb"
        assert outcomes[0].translator == "sqlglot"

    def test_translation_context_records_graceful_fallback_metadata(self):
        query = "SELECT * FROM orders"

        with sql_translation_context(strict=False) as outcomes:
            result = translate_sql_query(
                query,
                target_dialect="duckdb",
                source_dialect="netezza",
                rewrites=[("no_such_rewrite", {})],
            )

        assert result == query
        assert outcomes[0].status == "fallback"
        assert outcomes[0].warning_category == "translation_failed"

    def test_strict_translation_raises_and_records_failure_metadata(self):
        query = "SELECT * FROM orders"

        with sql_translation_context(strict=True) as outcomes:
            with pytest.raises(SQLTranslationError):
                translate_sql_query(
                    query,
                    target_dialect="duckdb",
                    source_dialect="netezza",
                    rewrites=[("no_such_rewrite", {})],
                )

        assert outcomes[0].status == "failed"
        assert outcomes[0].strict_mode is True
        assert outcomes[0].error_category == "translation_failed"

    def test_summarize_translation_outcomes_compacts_repeated_attempts(self):
        query = "SELECT * FROM orders"

        with sql_translation_context(strict=False) as outcomes:
            translate_sql_query(query, target_dialect="duckdb", source_dialect="netezza")
            translate_sql_query(query, target_dialect="duckdb", source_dialect="netezza")

        summary = summarize_sql_translation_outcomes(outcomes, strict_mode=False)

        assert summary is not None
        assert summary["status"] == "success"
        assert summary["attempt_count"] == 2
        assert summary["success_count"] == 2
        assert summary["source_dialects"] == ["netezza"]
        assert summary["target_dialects"] == ["duckdb"]
        assert summary["outcomes"][0]["count"] == 2


class TestTranslationScopeMetadata:
    def test_repeated_workload_queries_report_unique_count(self):
        with sql_translation_context(strict=False) as outcomes:
            for _ in range(3):
                translate_sql_query("SELECT * FROM orders", target_dialect="duckdb", source_dialect="netezza")
            translate_sql_query("SELECT * FROM lineitem", target_dialect="duckdb", source_dialect="netezza")

        summary = summarize_sql_translation_outcomes(outcomes, strict_mode=False)

        assert summary is not None
        assert summary["attempt_count"] == 4
        assert summary["total_transpilation_calls"] == 4
        assert summary["unique_queries_translated"] == 2
        assert "schema_statements_translated" not in summary
        entry = summary["outcomes"][0]
        assert entry["scope"] == "workload_query"
        assert entry["parser_grammar"] == "netezza"
        assert entry["normalized_source_dialect"] == "netezza"
        assert entry["count"] == 4
        assert entry["calls"] == 4
        assert entry["unique_queries"] == 2

    def test_schema_ddl_outcome_is_scoped_and_counted_separately(self):
        ddl = "CREATE TABLE region (r_regionkey INTEGER NOT NULL, r_name CHAR(25) NOT NULL)"
        with sql_translation_context(strict=False) as outcomes:
            translate_sql_query(ddl, target_dialect="bigquery", source_dialect="standard", scope="schema_ddl")
            translate_sql_query("SELECT * FROM region", target_dialect="bigquery", source_dialect="netezza")

        summary = summarize_sql_translation_outcomes(outcomes, strict_mode=False)

        assert summary is not None
        assert summary["attempt_count"] == 2
        assert summary["unique_queries_translated"] == 1
        assert summary["schema_statements_translated"] == 1
        assert summary["source_dialects"] == ["netezza", "standard"]
        outcome_entries = cast(list[dict[str, Any]], summary["outcomes"])
        scopes = {entry["scope"] for entry in outcome_entries}
        assert scopes == {"schema_ddl", "workload_query"}
        ddl_entry = next(entry for entry in outcome_entries if entry["scope"] == "schema_ddl")
        assert ddl_entry["source_dialect"] == "standard"
        assert ddl_entry["parser_grammar"] == "postgres"
        assert ddl_entry["count"] == 1

    def test_legacy_outcomes_without_scope_default_to_workload(self):
        with sql_translation_context(strict=False) as outcomes:
            translate_sql_query("SELECT 1", target_dialect="duckdb", source_dialect="netezza")

        assert outcomes[0].scope is None
        summary = summarize_sql_translation_outcomes(outcomes, strict_mode=False)

        assert summary is not None
        assert summary["outcomes"][0]["scope"] == "workload_query"
        assert summary["unique_queries_translated"] == 1

    def test_unknown_scope_is_rejected(self):
        with pytest.raises(ValueError, match="Unknown SQL translation scope"):
            translate_sql_query("SELECT 1", target_dialect="duckdb", scope="not_a_scope")


class TestStandardSourceDdlSnapshots:
    DDL = "CREATE TABLE region (r_regionkey INTEGER NOT NULL, r_name VARCHAR(25) NOT NULL)"

    def test_standard_source_preserves_lengths_bigquery(self):
        with sql_translation_context(strict=False) as outcomes:
            result = translate_sql_query(
                self.DDL, target_dialect="bigquery", source_dialect="standard", scope="schema_ddl"
            )
        assert "INT64" in result
        assert "STRING(25)" in result
        assert outcomes[0].scope == "schema_ddl"
        assert outcomes[0].normalized_source_dialect == "postgres"

    def test_standard_source_preserves_lengths_duckdb(self):
        with sql_translation_context(strict=False) as outcomes:
            result = translate_sql_query(
                self.DDL, target_dialect="duckdb", source_dialect="standard", scope="schema_ddl"
            )
        assert "TEXT(25)" in result
        assert outcomes[0].scope == "schema_ddl"

    def test_standard_source_preserves_lengths_snowflake(self):
        with sql_translation_context(strict=False) as outcomes:
            result = translate_sql_query(
                self.DDL, target_dialect="snowflake", source_dialect="standard", scope="schema_ddl"
            )
        assert "VARCHAR(25)" in result or "CHAR(25)" in result
        assert outcomes[0].scope == "schema_ddl"


class TestIntegrationScenarios:
    def test_read_primitives_translation_scenario(self):
        query = """
            SELECT COUNT(*) as orders_by_month
            FROM orders
            WHERE o_orderdate = TO_DATE('1995-03-15', 'yyyy-MM-dd')
        """
        result = translate_sql_query(query, target_dialect="duckdb", source_dialect="netezza")
        assert result
        assert "orders" in result.lower()
        assert "1995-03-15" in result


class TestTpcdsDateIntegerArithmetic:
    @staticmethod
    def _translate(query: str, target: str = "postgres") -> str:
        from types import SimpleNamespace

        from benchbox.core.tpcds.benchmark.runner import TPCDSBenchmark

        return TPCDSBenchmark.translate_query_text(SimpleNamespace(), query, "netezza", target)

    def test_convert_date_plus_integer(self):
        result = self._translate("SELECT * FROM t WHERE d_date + 5 > NOW()")
        assert "INTERVAL '5 DAY'" in result
        assert "+ 5" not in result

    def test_convert_qualified_date_column(self):
        result = self._translate("SELECT * FROM t WHERE d1.d_date + 30 > d2.d_date")
        assert "d1.d_date + INTERVAL '30 DAY'" in result
        assert "+ 30" not in result

    def test_convert_date_minus_integer(self):
        result = self._translate("SELECT * FROM t WHERE d_date - 7 < NOW()")
        assert "INTERVAL '7 DAY'" in result
        assert "- 7" not in result

    def test_does_not_affect_non_date_columns(self):
        result = self._translate("SELECT * FROM t WHERE order_id + 5 > 100")
        assert "order_id + 5" in result
        assert "INTERVAL" not in result

    def test_does_not_affect_surrogate_date_keys(self):
        result = self._translate("SELECT d_date_sk + 1 FROM date_dim WHERE d_date_sk - 7 > 0")
        assert "d_date_sk + 1" in result
        assert "d_date_sk - 7" in result
        assert "INTERVAL" not in result

    def test_tpcds_q72_pattern(self):
        result = self._translate("SELECT * FROM a, b d1, b d3 WHERE d3.d_date > d1.d_date + 5")
        assert "d3.d_date > d1.d_date + INTERVAL '5 DAY'" in result

    def test_multiple_date_arithmetic_expressions(self):
        result = self._translate("SELECT * FROM t WHERE d_date + 5 > NOW() AND d_date - 10 < NOW()")
        assert "INTERVAL '5 DAY'" in result
        assert "INTERVAL '10 DAY'" in result
        assert "+ 5" not in result
        assert "- 10" not in result

    def test_preserves_other_arithmetic(self):
        result = self._translate("SELECT price * 2 + tax FROM t WHERE d_date + 5 > NOW()")
        assert "price * 2 + tax" in result
        assert "INTERVAL '5 DAY'" in result

    def test_only_the_postgres_target_receives_the_rewrite(self):
        result = self._translate("SELECT * FROM t WHERE d_date + 5 > NOW()", target="duckdb")
        assert "+ 5" in result

    def test_date_columns_come_from_the_tpcds_schema(self):
        from benchbox.core.tpcds.benchmark.runner import _DATE_COLUMNS

        assert "d_date" in _DATE_COLUMNS
        assert "d_date_sk" not in _DATE_COLUMNS


class TestPostgresIdentifierQuoting:
    def test_postgres_no_identifier_quoting(self):
        query = "SELECT SR_RETURN_AMT FROM store_returns"
        result = translate_sql_query(query, target_dialect="postgres", identify=True)
        assert '"SR_RETURN_AMT"' not in result

    def test_clickhouse_no_identifier_quoting(self):
        query = "SELECT SR_RETURN_AMT FROM store_returns"
        result = translate_sql_query(query, target_dialect="clickhouse", identify=True)
        assert '"SR_RETURN_AMT"' not in result

    def test_snowflake_no_identifier_quoting(self):
        query = "SELECT note FROM company_type"
        result = translate_sql_query(query, target_dialect="snowflake", identify=True)
        assert '"company_type"' not in result
        assert '"note"' not in result
        assert "company_type" in result

    def test_duckdb_uses_identifier_quoting(self):
        query = "SELECT order FROM orders"
        result = translate_sql_query(query, target_dialect="duckdb", identify=True)
        assert result

    def test_postgres_case_folding_scenario(self):
        query = "SELECT SR_RETURN_AMT, SR_NET_LOSS FROM store_returns WHERE SR_RETURN_AMT > 100"
        result = translate_sql_query(query, target_dialect="postgres", source_dialect="netezza", identify=True)
        assert '"SR_RETURN_AMT"' not in result
        assert '"SR_NET_LOSS"' not in result


class TestMultiStatementInput:
    def test_every_statement_is_translated(self):
        result = translate_sql_query(
            "SELECT a FROM t1 LIMIT 1; SELECT b FROM t2 LIMIT 2;", "mysql", "postgres", identify=False
        )
        assert result == "SELECT a FROM t1 LIMIT 1;\nSELECT b FROM t2 LIMIT 2"
        assert split_sql_statements(result, "mysql") == ["SELECT a FROM t1 LIMIT 1", "SELECT b FROM t2 LIMIT 2"]

    def test_dsqgen_style_two_part_query_keeps_both_parts(self):
        result = translate_sql_query(
            "SELECT * FROM t WHERE a = 1; SELECT * FROM t WHERE a = 2", "postgres", source_dialect="netezza"
        )
        assert split_sql_statements(result, "postgres") == [
            "SELECT * FROM t WHERE a = 1",
            "SELECT * FROM t WHERE a = 2",
        ]

    def test_rewrites_apply_to_every_statement(self):
        result = translate_sql_query(
            "SELECT a / b FROM t1; SELECT c / d FROM t2",
            "databricks",
            "databricks",
            identify=False,
            rewrites=[("safe_division", {})],
        )
        assert split_sql_statements(result, "databricks") == [
            "SELECT TRY_DIVIDE(a, b) FROM t1",
            "SELECT TRY_DIVIDE(c, d) FROM t2",
        ]

    def test_single_statement_output_is_unchanged(self):
        assert translate_sql_query("SELECT 1 AS a;", "duckdb", "netezza", identify=False) == "SELECT 1 AS a"

    def test_empty_input_translates_to_empty_string(self):
        assert translate_sql_query("", "duckdb", "postgres") == ""
        assert translate_sql_query(";", "duckdb", "postgres") == ""

    def test_one_unparseable_statement_falls_back_to_the_whole_input(self):
        query = "SELECT 1; SELEC FROM FROM ((("
        with sql_translation_context(strict=False) as outcomes:
            assert translate_sql_query(query, "duckdb", "postgres") == query
        assert outcomes[0].status == "fallback"

    def test_unparseable_statement_raises_in_strict_mode(self):
        with pytest.raises(SQLTranslationError):
            translate_sql_query("SELECT 1; SELEC FROM FROM (((", "duckdb", "postgres", strict=True)

    def test_tpcds_two_part_queries_keep_both_parts(self):
        from types import SimpleNamespace

        from benchbox.core.tpcds.benchmark.runner import TPCDSBenchmark

        query = "with a as (select 1 x) select x from a; with b as (select 2 y) select y from b"
        translated = TPCDSBenchmark.translate_query_text(SimpleNamespace(), query, "netezza", "duckdb")
        assert len(split_sql_statements(translated, "duckdb")) == 2


class TestTpcdsQuestDbDateArithmetic:
    def test_date_plus_integer_becomes_dateadd(self):
        result = TestTpcdsDateIntegerArithmetic._translate(
            "SELECT * FROM a, b d1, b d3 WHERE d3.d_date > d1.d_date + 5", "questdb"
        )
        assert "DATEADD('d', 5, d1.d_date)" in result
