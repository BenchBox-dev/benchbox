from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from benchbox.core.joinorder.queries import JoinOrderQueryManager

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

KNOWN_ZERO_UNDERLYING = {"2c", "5a", "5b", "10b", "32a"}
REPO_ROOT = Path(__file__).resolve().parents[4]
TINY_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "joinorder_canonical_tiny"
TINY_REFERENCE = REPO_ROOT / "_project" / "joinorder" / "tiny_reference_cardinalities.json"
DUCKDB_INTEGER_RANGES = {
    "TINYINT": (-(2**7), 2**7 - 1),
    "SMALLINT": (-(2**15), 2**15 - 1),
    "INTEGER": (-(2**31), 2**31 - 1),
    "BIGINT": (-(2**63), 2**63 - 1),
    "HUGEINT": (-(2**127), 2**127 - 1),
    "UTINYINT": (0, 2**8 - 1),
    "USMALLINT": (0, 2**16 - 1),
    "UINTEGER": (0, 2**32 - 1),
    "UBIGINT": (0, 2**64 - 1),
}
NULL_EMPTY_FIXTURE_CHECKS = {
    ("cast_info", "note"): (66, 0),
    ("movie_companies", "note"): (35, 0),
    ("title", "series_years"): (176, 0),
}


def _quote_ident(identifier: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", identifier):
        raise AssertionError(f"unsafe identifier: {identifier}")
    return f'"{identifier}"'


@pytest.fixture(scope="module")
def tiny_reference() -> dict[str, Any]:
    return json.loads(TINY_REFERENCE.read_text(encoding="utf-8"))["queries"]


@pytest.fixture(scope="module")
def duckdb_conn() -> Any:
    duckdb = pytest.importorskip("duckdb")

    conn = duckdb.connect(database=":memory:")
    for parquet_path in sorted(TINY_FIXTURE.glob("*.parquet")):
        table_name = parquet_path.stem
        conn.execute(f"CREATE TABLE {table_name} AS SELECT * FROM read_parquet(?)", [str(parquet_path)])
    try:
        yield conn
    finally:
        conn.close()


def _underlying_count_query(query: str) -> str:
    from_match = re.search(r"\bFROM\b", query, flags=re.IGNORECASE)
    if from_match is None:
        raise AssertionError(f"query has no FROM clause: {query}")
    return "SELECT COUNT(*) AS underlying_row_count\n" + query[from_match.start() :].rstrip(";\n ")


@pytest.mark.parametrize("query_id", JoinOrderQueryManager().get_query_ids())
def test_canonical_query_matches_tiny_fixture_oracle(
    query_id: str,
    duckdb_conn: Any,
    tiny_reference: dict[str, Any],
) -> None:
    query = JoinOrderQueryManager().get_query(query_id)

    rows = duckdb_conn.execute(query).fetchall()
    underlying_rows = duckdb_conn.execute(_underlying_count_query(query)).fetchone()[0]
    expected = tiny_reference[query_id]

    assert len(rows) == expected["row_count"] == 1
    assert underlying_rows == expected["underlying_row_count"]
    if query_id in KNOWN_ZERO_UNDERLYING:
        assert underlying_rows == 0
    else:
        assert underlying_rows >= 1


def test_tiny_fixture_preserves_nulls_and_integer_widths(duckdb_conn: Any) -> None:
    for (table, column), (expected_nulls, expected_empty) in NULL_EMPTY_FIXTURE_CHECKS.items():
        null_count, empty_count = duckdb_conn.execute(
            f"SELECT "
            f"count(*) FILTER (WHERE {_quote_ident(column)} IS NULL), "
            f"count(*) FILTER (WHERE {_quote_ident(column)} = '') "
            f"FROM {_quote_ident(table)}"
        ).fetchone()

        assert null_count == expected_nulls
        assert empty_count == expected_empty

    for table in sorted(path.stem for path in TINY_FIXTURE.glob("*.parquet")):
        schema_rows = duckdb_conn.execute(f"DESCRIBE {_quote_ident(table)}").fetchall()
        for column_name, column_type, *_rest in schema_rows:
            normalized_type = str(column_type).upper().split("(", 1)[0]
            if normalized_type not in DUCKDB_INTEGER_RANGES:
                continue

            min_value, max_value = duckdb_conn.execute(
                f"SELECT min({_quote_ident(column_name)}), max({_quote_ident(column_name)}) FROM {_quote_ident(table)}"
            ).fetchone()
            if min_value is None or max_value is None:
                continue

            type_range = DUCKDB_INTEGER_RANGES[normalized_type]
            assert type_range[0] <= int(min_value)
            assert int(max_value) <= type_range[1]


