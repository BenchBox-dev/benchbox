from __future__ import annotations

import json
import re
from typing import Any

import pytest

from benchbox.core.joinorder.benchmark import JoinOrderBenchmark
from benchbox.core.joinorder.queries import JoinOrderQueryManager
from benchbox.sql_compat.rules.query_source.joinorder_variants import QUESTDB_VARIANTS

_canonical = pytest.importorskip("tests.unit.core.joinorder.test_canonical_queries")
TINY_FIXTURE = _canonical.TINY_FIXTURE
TINY_REFERENCE = _canonical.TINY_REFERENCE
_underlying_count_query = _canonical._underlying_count_query

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture(scope="module")
def tiny_reference() -> dict[str, Any]:
    return json.loads(TINY_REFERENCE.read_text(encoding="utf-8"))["queries"]


@pytest.fixture(scope="module")
def duckdb_conn() -> Any:
    duckdb = pytest.importorskip("duckdb")

    conn = duckdb.connect(database=":memory:")
    for parquet_path in sorted(TINY_FIXTURE.glob("*.parquet")):
        conn.execute(f"CREATE TABLE {parquet_path.stem} AS SELECT * FROM read_parquet(?)", [str(parquet_path)])
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture(scope="module")
def joinorder_benchmark(tmp_path_factory: pytest.TempPathFactory) -> JoinOrderBenchmark:
    return JoinOrderBenchmark(scale_factor=1.0, output_dir=tmp_path_factory.mktemp("joinorder"))


def _leading_table(sql: str) -> str:
    match = re.search(r"\bFROM\s+(\w+)", sql, flags=re.IGNORECASE)
    assert match is not None
    return match.group(1).lower()


@pytest.mark.parametrize("query_id", sorted(QUESTDB_VARIANTS))
def test_questdb_variant_leads_with_the_selective_table_not_company_name(query_id: str) -> None:
    canonical = JoinOrderQueryManager().get_query(query_id)
    variant = QUESTDB_VARIANTS[query_id]

    assert _leading_table(variant) in {"keyword", "role_type"}
    assert "company_name" in canonical.split("WHERE")[0].lower()
    assert "\nJOIN " in variant
    assert "," not in variant.split("WHERE")[0].split("FROM", 1)[1]


@pytest.mark.parametrize("query_id", sorted(QUESTDB_VARIANTS))
def test_questdb_variant_returns_the_canonical_result(
    query_id: str,
    duckdb_conn: Any,
    tiny_reference: dict[str, Any],
) -> None:
    canonical = JoinOrderQueryManager().get_query(query_id)
    variant = QUESTDB_VARIANTS[query_id]

    assert duckdb_conn.execute(variant).fetchall() == duckdb_conn.execute(canonical).fetchall()
    underlying = duckdb_conn.execute(_underlying_count_query(variant)).fetchone()[0]
    assert underlying == tiny_reference[query_id]["underlying_row_count"]


def test_questdb_dialect_selects_the_variant_and_other_dialects_keep_the_canonical_text(
    joinorder_benchmark: JoinOrderBenchmark,
) -> None:
    assert joinorder_benchmark.get_query("16b", dialect="questdb") == QUESTDB_VARIANTS["16b"]
    assert joinorder_benchmark.get_queries(dialect="questdb")["17e"] == QUESTDB_VARIANTS["17e"]
    assert joinorder_benchmark.get_query("16b", dialect="duckdb") != QUESTDB_VARIANTS["16b"]
    assert joinorder_benchmark.get_query("16b") == JoinOrderQueryManager().get_query("16b")
    assert joinorder_benchmark.get_query("1a", dialect="questdb") == joinorder_benchmark.translate_query_text(
        JoinOrderQueryManager().get_query("1a"), "questdb"
    )
