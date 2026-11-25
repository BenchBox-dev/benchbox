from __future__ import annotations

from typing import Any

import pytest

from benchbox.platforms.presto import PrestoAdapter
from benchbox.platforms.trino import TrinoAdapter
from benchbox.utils.dialect_utils import apply_rewrite

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

COLUMNS = [
    ("date_dim", "d_date", "date"),
    ("date_dim", "d_year", "integer"),
    ("sales", "sold_at", "timestamp(3)"),
    ("sales", "label", "varchar(10)"),
    ("sales", "referer", "varchar"),
]
TYPES = {"d_date": "date", "sold_at": "timestamp", "label": "varchar"}


class FakeCursor:
    def __init__(self, connection: FakeConnection):
        self.connection = connection
        self.sql = ""

    def execute(self, sql: str, *args: Any) -> None:
        self.sql = sql
        self.connection.statements.append(sql)
        if "information_schema" in sql and self.connection.lookup_fails:
            raise RuntimeError("lookup refused")

    def fetchall(self) -> list[tuple[Any, ...]]:
        if "information_schema" in self.sql:
            return [row for row in COLUMNS if f"'{row[0]}'" in self.sql]
        return [(1,)]

    def fetchone(self) -> tuple[Any, ...]:
        return (1,)

    def close(self) -> None:
        pass


class FakeConnection:
    def __init__(self, lookup_fails: bool = False):
        self.statements: list[str] = []
        self.lookup_fails = lookup_fails

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    @property
    def workload(self) -> list[str]:
        return [sql for sql in self.statements if "information_schema" not in sql and "system.runtime" not in sql]


@pytest.fixture(params=[TrinoAdapter, PrestoAdapter])
def adapter(request):
    try:
        return request.param(host="localhost", catalog="memory", schema="bench")
    except ImportError:
        pytest.skip("driver not installed")


def cast_literals(sql: str) -> str:
    return apply_rewrite("cast_temporal_literals", sql, "trino", column_types=TYPES)


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT 1 FROM t WHERE d_date <= '2024-01-02'", "d_date <= CAST('2024-01-02' AS DATE)"),
        ("SELECT 1 FROM t WHERE '2024-01-02' < x.d_date", "CAST('2024-01-02' AS DATE) < x.d_date"),
        ("SELECT 1 FROM t WHERE d_date IN ('1998-01-02', '1998-10-15')", "IN (CAST('1998-01-02' AS DATE), CAST("),
        (
            "SELECT 1 FROM t WHERE d_date BETWEEN '1999-2-01' AND CAST('1999-2-01' AS DATE) + INTERVAL '60' DAY",
            "BETWEEN CAST('1999-02-01' AS DATE) AND CAST('1999-02-01' AS DATE) + INTERVAL '60' DAY",
        ),
        ("SELECT 1 FROM t WHERE sold_at >= '2024-01-02 05:00:00'", "CAST('2024-01-02 05:00:00' AS TIMESTAMP)"),
        ("SELECT 1 FROM t WHERE sold_at >= '2024-01-02'", "CAST('2024-01-02' AS TIMESTAMP)"),
        ("SELECT 1 FROM t HAVING MAX(d_date) > '2024-01-02'", "MAX(d_date) > CAST('2024-01-02' AS DATE)"),
        ("SELECT 1 FROM t WHERE d_date < '2024-01-02 05:00:00'", "CAST('2024-01-02 05:00:00' AS TIMESTAMP)"),
    ],
)
def test_temporal_literals_take_the_column_type(sql, expected):
    assert expected in cast_literals(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1 FROM t WHERE label = '2024-01-02'",
        "SELECT 1 FROM t WHERE unknown = '2024-01-02'",
        "SELECT 1 FROM t WHERE d_date = 'yesterday'",
    ],
)
def test_text_comparisons_keep_their_literal(sql):
    assert "CAST" not in cast_literals(sql)


def inline(sql: str, columns: list[str]) -> str:
    return apply_rewrite("inline_group_by_aliases", sql, "trino", columns=columns)


def test_group_by_alias_becomes_its_expression():
    assert "GROUP BY EXTRACT(MINUTE FROM t)" in inline("SELECT EXTRACT(MINUTE FROM t) AS m FROM s GROUP BY m", ["t"])


@pytest.mark.parametrize(
    ("sql", "columns"),
    [
        ("SELECT year + 1 AS year FROM s GROUP BY year", ["year"]),
        ("WITH c AS (SELECT 1 AS y) SELECT y + 1 AS y FROM c GROUP BY y", []),
        ("SELECT y + 1 AS y FROM (SELECT 1 AS y) d GROUP BY y", []),
        ("WITH c AS (SELECT * FROM s) SELECT y + 1 AS y FROM c GROUP BY y", []),
    ],
)
def test_group_by_prefers_a_source_column_over_an_alias(sql, columns):
    assert inline(sql, columns).endswith(sql[sql.index("GROUP BY") :])


def test_regexp_group_references_use_a_dollar():
    sql = apply_rewrite(
        "regexp_group_references_to_dollar", r"SELECT REGEXP_REPLACE(a, '^(x)(y)$', '\2-\1') FROM t", "trino"
    )
    assert "'$2-$1'" in sql


@pytest.mark.parametrize("dialect", ["trino", "presto"])
def test_approximate_top_k_casts_char_to_varchar(dialect):
    from benchbox.core.read_primitives.queries import ReadPrimitivesQueryManager

    assert "CAST(l_shipmode AS VARCHAR)" in ReadPrimitivesQueryManager().get_query(
        "approx_top_k_lineitem", dialect=dialect
    )


def test_vector_search_uses_the_trino_array_distance_functions():
    from benchbox.core.vector_search.queries import VectorSearchQueryManager

    sql = " ".join(VectorSearchQueryManager().get_all_queries(dialect="trino").values()).lower()
    assert "cosine_similarity(" in sql
    assert "euclidean_distance(" in sql
    assert "array_" not in sql


def test_adapter_types_literals_from_the_live_schema(adapter):
    connection = FakeConnection()
    sql = "SELECT d_year FROM date_dim WHERE d_date + 5 > d_date AND d_date = '2024-01-03'"
    result = adapter.execute_query(connection, sql, "q1")
    assert result["status"] == "SUCCESS"
    assert "CAST('2024-01-03' AS DATE)" in connection.workload[0]
    assert "INTERVAL '5' DAY" in connection.workload[0]


def test_adapter_reads_a_table_schema_once(adapter):
    connection = FakeConnection()
    for day in ("2024-01-02", "2024-01-03"):
        adapter.execute_query(connection, f"SELECT 1 FROM date_dim WHERE d_date = '{day}'", "q1")
    assert sum("information_schema" in sql for sql in connection.statements) == 1


def test_adapter_sends_unaffected_queries_verbatim(adapter):
    connection = FakeConnection()
    sql = "SELECT label  FROM sales"
    adapter.execute_query(connection, sql, "q1")
    assert connection.workload[0] == sql
    assert not any("information_schema" in statement for statement in connection.statements)


def test_adapter_keeps_the_query_when_the_schema_lookup_fails(adapter):
    connection = FakeConnection(lookup_fails=True)
    sql = "SELECT 1 FROM date_dim WHERE d_date = '2024-01-02'"
    result = adapter.execute_query(connection, sql, "q1")
    assert result["status"] == "SUCCESS"
    assert connection.workload[0] == sql


def test_adapter_drops_type_caches_when_the_schema_is_recreated(adapter, monkeypatch):
    connection = FakeConnection()
    adapter.execute_query(connection, "SELECT 1 FROM date_dim WHERE d_date = '2024-01-02'", "q1")
    monkeypatch.setattr(adapter, "_create_schema_with_tuning", lambda *args, **kwargs: "")
    adapter.create_schema(object(), connection)
    assert not adapter._table_column_types
    assert not adapter._strictly_typed_queries
