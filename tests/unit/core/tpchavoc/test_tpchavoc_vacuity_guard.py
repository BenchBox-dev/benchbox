# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import benchbox.core.tpchavoc.equivalence as equivalence_module
from benchbox.core.tpchavoc.equivalence import _report, find_divergences, find_q16_cardinality_divergences

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class _FakeDuckDBAdapterVacuousLoad:
    def __init__(self, *, database):
        self.database = database

    def load_data(self, benchmark, connection, data_dir):
        stats = {
            "lineitem": 100,
            "orders": 0,
            "partsupp": 10,
            "part": 10,
            "customer": 10,
            "supplier": 10,
            "nation": 25,
            "region": 5,
        }
        return stats, 0.0, None


def test_build_duckdb_with_tpch_vacuity_guard_raises_on_partial_load(monkeypatch, tmp_path):
    fake_tpchavoc = MagicMock()
    fake_tpchavoc.get_create_tables_sql.return_value = ""
    fake_tpch = MagicMock()

    monkeypatch.setattr(
        equivalence_module,
        "_generate_tpch",
        lambda scale_factor, output_dir: (tmp_path, fake_tpchavoc, fake_tpch),
    )
    fake_connection = MagicMock()
    monkeypatch.setattr("duckdb.connect", lambda *_a, **_kw: fake_connection)
    monkeypatch.setattr("benchbox.platforms.duckdb.DuckDBAdapter", _FakeDuckDBAdapterVacuousLoad)

    with pytest.raises(RuntimeError, match=r"DuckDB TPC-H load failed - no rows in \['orders'\]"):
        equivalence_module.build_duckdb_with_tpch(0.1, tmp_path)

    fake_connection.close.assert_called_once()


class _VacuousCanonicalConnection:
    def __init__(self):
        self.executed: list[str] = []

    def execute(self, sql: str):
        self.executed.append(sql)
        result = MagicMock()
        result.fetchall.return_value = []
        return result


def test_find_divergences_vacuity_guard_fails_on_empty_canonical_query():
    conn = _VacuousCanonicalConnection()
    benchmark = MagicMock()
    benchmark.get_implemented_queries.return_value = [1]

    divergences = find_divergences(conn, benchmark, lambda q: "SELECT c FROM t")

    assert len(divergences) == 1
    divergence = divergences[0]
    assert divergence.query_id == 1
    assert divergence.variant_id == 0
    assert "vacuous" in divergence.detail.lower()
    assert len(conn.executed) == 1
    benchmark.get_query.assert_not_called()


def test_find_divergences_vacuity_guard_does_not_flag_a_nonempty_canonical_query():

    class _NonEmptyConnection:
        def execute(self, sql: str):
            result = MagicMock()
            result.fetchall.return_value = [(1,)]
            return result

    benchmark = MagicMock()
    benchmark.get_implemented_queries.return_value = [1]
    benchmark.get_query.return_value = "SELECT v FROM t"
    benchmark.validate_variant_equivalence.return_value = None

    divergences = find_divergences(_NonEmptyConnection(), benchmark, lambda q: "SELECT c FROM t")

    assert divergences == []


def test_report_vacuity_guard_fails_on_stale_resolved_baseline(capsys):
    exit_code = _report(
        [],
        total=10,
        known={"1_v1": "a variant defect that has since been fixed"},
        engine_label="DuckDB",
        baseline_name="KNOWN_DIVERGENCES",
    )

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "GATE FAILURE - previously-known divergences now equivalent" in out
    assert "1_v1" in out
    assert "KNOWN_DIVERGENCES" in out


def test_report_vacuity_guard_stays_green_with_no_new_or_resolved_entries(capsys):
    exit_code = _report(
        [],
        total=10,
        known={},
        engine_label="DuckDB",
        baseline_name="KNOWN_DIVERGENCES",
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "All variants equivalent to canonical TPC-H" in out


def _build_q16_duckdb_fixture():
    import duckdb

    from benchbox.core.tpchavoc.benchmark import TPCHavocBenchmark

    conn = duckdb.connect(":memory:")
    benchmark = TPCHavocBenchmark(scale_factor=0.1)
    for statement in benchmark.get_create_tables_sql(dialect="duckdb").strip().split(";"):
        if statement.strip():
            conn.execute(statement.strip())

    conn.execute(
        """
        INSERT INTO part VALUES
            (1, 'part1', 'mfgr1', 'Brand#11', 'STANDARD ANODIZED TIN', 3, 'SM BOX', 100.0, 'ok'),
            (2, 'part2', 'mfgr1', 'Brand#22', 'SMALL PLATED BRASS', 14, 'LG CASE', 200.0, 'ok');
        INSERT INTO supplier VALUES
            (10, 'supp10', 'addr', 1, '11-111', 100.0, 'Customer Complaints initial'),
            (20, 'supp20', 'addr', 1, '22-222', 200.0, 'reliable supplier'),
            (30, 'supp30', 'addr', 1, '33-333', 300.0, 'prompt delivery'),
            (40, 'supp40', 'addr', 1, '44-444', 400.0, 'trusted vendor');
        INSERT INTO partsupp VALUES
            (1, 10, 5, 10.0, 'ps10'),
            (1, 20, 5, 10.0, 'ps20'),
            (1, 30, 5, 10.0, 'ps30'),
            (2, 20, 5, 10.0, 'ps20b'),
            (2, 40, 5, 10.0, 'ps40');
        """
    )
    return conn, benchmark


def test_find_q16_cardinality_divergences_verifies_zero_and_multi_complaints_and_restores_comments():
    conn, benchmark = _build_q16_duckdb_fixture()
    try:
        canonical_q16 = benchmark.get_query("16_v3")
        before = conn.execute("SELECT s_suppkey, s_comment FROM supplier ORDER BY s_suppkey").fetchall()

        divergences = find_q16_cardinality_divergences(conn, benchmark, lambda _q: canonical_q16)
        after = conn.execute("SELECT s_suppkey, s_comment FROM supplier ORDER BY s_suppkey").fetchall()

        assert divergences == []
        assert after == before

        defective_cross_join_16_v6 = """
            SELECT p.p_brand, p.p_type, p.p_size, count(distinct ps.ps_suppkey) as supplier_cnt
            FROM partsupp ps, part p, (
                SELECT s_suppkey as excluded_suppkey
                FROM supplier
                WHERE s_comment like '%Customer%Complaints%'
            ) excluded
            WHERE p.p_partkey = ps.ps_partkey
              AND p.p_brand <> 'Brand#45'
              AND p.p_type not like 'MEDIUM POLISHED%'
              AND p.p_size in (49, 14, 23, 45, 19, 3, 36, 9)
              AND ps.ps_suppkey <> excluded.excluded_suppkey
            GROUP BY p.p_brand, p.p_type, p.p_size
            ORDER BY supplier_cnt desc, p.p_brand, p.p_type, p.p_size
        """
        single_complaint_divs = find_divergences(
            conn,
            benchmark,
            lambda _q: canonical_q16,
            query_ids=[16],
            variant_query=lambda _q, v: defective_cross_join_16_v6 if v == 6 else benchmark.get_query(f"16_v{v}"),
        )
        assert single_complaint_divs == []

        multi_cardinality_divs = find_q16_cardinality_divergences(
            conn,
            benchmark,
            lambda _q: canonical_q16,
            variant_query=lambda _q, v: defective_cross_join_16_v6 if v == 6 else benchmark.get_query(f"16_v{v}"),
        )
        assert [d.key for d in multi_cardinality_divs] == ["16_v6"]
        assert "|excluded|=0" in multi_cardinality_divs[0].detail
    finally:
        conn.close()


def test_q8_v7_and_q14_v7_coalesce_empty_filtered_numerator_groups():
    import duckdb

    from benchbox.core.tpchavoc.benchmark import TPCHavocBenchmark

    conn = duckdb.connect(":memory:")
    benchmark = TPCHavocBenchmark(scale_factor=0.01)
    try:
        for statement in benchmark.get_create_tables_sql(dialect="duckdb").strip().split(";"):
            if statement.strip():
                conn.execute(statement.strip())

        conn.execute(
            """
            INSERT INTO region VALUES (1, 'AMERICA', 'ok');
            INSERT INTO nation VALUES (1, 'CANADA', 1, 'ok'), (2, 'BRAZIL', 1, 'ok');
            INSERT INTO part VALUES (1, 'p1', 'm1', 'Brand#11', 'ECONOMY ANODIZED STEEL', 1, 'SM BOX', 10.0, 'ok');
            INSERT INTO supplier VALUES (1, 's1', 'a', 1, '11', 10.0, 'ok');
            INSERT INTO customer VALUES (1, 'c1', 'a', 1, '11', 10.0, 'AUTOMOBILE', 'ok');
            INSERT INTO orders VALUES (1, 1, 'O', 100.0, DATE '1995-06-15', '1-URGENT', 'Clerk#1', 0, 'ok');
            INSERT INTO lineitem VALUES
                (1, 1, 1, 1, 10.0, 100.0, 0.05, 0.02, 'N', 'O', DATE '1995-09-15', DATE '1995-09-15', DATE '1995-09-20', 'NONE', 'AIR', 'ok');
            """
        )

        divergences = find_divergences(
            conn,
            benchmark,
            lambda q: benchmark.get_query(f"{q}_v3"),
            query_ids=[8, 14],
        )
        assert divergences == []
    finally:
        conn.close()


def _build_q18_tie_fixture():
    import duckdb

    from benchbox.core.tpchavoc.benchmark import TPCHavocBenchmark

    conn = duckdb.connect(":memory:")
    benchmark = TPCHavocBenchmark(scale_factor=0.01)
    conn.execute(
        """
        CREATE TABLE customer AS SELECT 1 AS c_custkey, 'customer' AS c_name;
        CREATE TABLE orders AS
            SELECT i AS o_orderkey, 1 AS o_custkey,
                DATE '1995-01-01' AS o_orderdate, 100000.00 AS o_totalprice
            FROM range(1, 102) AS t(i);
        CREATE TABLE lineitem AS
            SELECT i AS l_orderkey, 50 AS l_quantity
            FROM range(1, 102) AS t(i) CROSS JOIN range(7);
        """
    )
    return conn, benchmark


def test_q18_variants_return_bounded_top_n_on_rank_ties():
    conn, benchmark = _build_q18_tie_fixture()
    try:
        assert len(conn.execute(benchmark.get_query(18)).fetchall()) == 100
        for variant_id in range(1, 11):
            rows = conn.execute(benchmark.get_query(f"18_v{variant_id}")).fetchall()
            assert len(rows) <= 100, f"18_v{variant_id}"
        assert len(conn.execute(benchmark.get_query("18_v9")).fetchall()) == 100

        legacy_rank_top_n = (
            "WITH order_quantities AS (SELECT 1 AS x, rank() OVER (ORDER BY 1) AS r "
            "FROM range(101) AS t(i)) SELECT x FROM order_quantities WHERE r <= 100"
        )
        assert len(conn.execute(legacy_rank_top_n).fetchall()) == 101
    finally:
        conn.close()
