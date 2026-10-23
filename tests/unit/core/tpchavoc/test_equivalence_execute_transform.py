from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from benchbox.core.tpchavoc.equivalence import _clickhouse_execute_transform, find_divergences
from benchbox.core.tpchavoc.validation import ResultValidator

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class _RecordingConnection:
    def __init__(self, fetchall_side_effect: dict[int, list[tuple]] | None = None):
        self.executed: list[str] = []
        self._fetchall_side_effect = fetchall_side_effect or {}
        self._fetches = 0

    def execute(self, sql: str):
        self.executed.append(sql)
        self._fetches += 1
        result = MagicMock()
        result.fetchall.return_value = self._fetchall_side_effect.get(self._fetches, [(1,)])
        return result


def _benchmark_with_one_query():
    benchmark = MagicMock()
    benchmark.get_implemented_queries.return_value = [1]
    benchmark.get_query.return_value = "SELECT v FROM t"
    benchmark.validate_variant_equivalence.return_value = None
    return benchmark


def _benchmark_validating_strictly():
    validator = ResultValidator()
    benchmark = MagicMock()
    benchmark.get_implemented_queries.return_value = [1]
    benchmark.get_query.return_value = "SELECT v FROM t"
    benchmark.validate_variant_equivalence.side_effect = lambda qid, vid, original, variant: (
        validator.validate_results_exact(original, variant, qid, vid)
    )
    return benchmark


def _padding_connection(canonical_rows: list[tuple], variant_rows: list[tuple]) -> _RecordingConnection:
    scripted = {1: canonical_rows, 2: variant_rows}
    scripted.update(dict.fromkeys(range(3, 12), canonical_rows))
    return _RecordingConnection(scripted)


def test_execute_transform_applied_to_canonical_and_every_variant():
    conn = _RecordingConnection()
    benchmark = _benchmark_with_one_query()

    divergences = find_divergences(
        conn,
        benchmark,
        lambda q: "SELECT c FROM t",
        translate_variant=lambda sql: sql,
        execute_transform=lambda sql: f"{sql} /*X*/",
    )

    assert divergences == []

    assert len(conn.executed) == 11
    assert all(sql.endswith(" /*X*/") for sql in conn.executed)
    assert conn.executed[0] == "SELECT c FROM t /*X*/"


def test_no_transform_leaves_sql_unchanged():
    conn = _RecordingConnection()
    benchmark = _benchmark_with_one_query()

    find_divergences(conn, benchmark, lambda q: "SELECT c FROM t", translate_variant=lambda sql: sql)

    assert conn.executed[0] == "SELECT c FROM t"
    assert all("/*X*/" not in sql for sql in conn.executed)


def test_clickhouse_execute_transform_mirrors_production_sequence():

    out = _clickhouse_execute_transform("SELECT revenue / SUM(quantity) FROM t")
    assert "CAST(revenue AS Nullable(Float64)) / nullIf(SUM(quantity), 0)" in out
    assert out.rstrip().endswith("SETTINGS joined_subquery_requires_alias = 0")


def test_clickhouse_execute_transform_keeps_windowed_divisor_intact():

    out = _clickhouse_execute_transform("SELECT mkt / SUM(volume) OVER (PARTITION BY o_year) FROM t")
    assert "nullIf(SUM(volume) OVER (PARTITION BY o_year), 0)" in out
    assert "nullIf(SUM(volume), 0) OVER" not in out


@pytest.mark.parametrize(
    ("canonical", "variant", "padding_columns", "expected_keys"),
    [
        ([("1-URGENT       ", 1)], [("1-URGENT", 1)], None, ["1_v1"]),
        ([("1-URGENT       ", 1)], [("1-URGENT", 1)], {1: (0,)}, []),
        ([("  1-URGENT", 1)], [("1-URGENT", 1)], {1: (0,)}, ["1_v1"]),
        ([("1-URGENT       ", 1)], [("2-HIGH", 1)], {1: (0,)}, ["1_v1"]),
        ([("1-URGENT       ", "comment ")], [("1-URGENT", "comment")], {1: (0,)}, ["1_v1"]),
        ([("1-URGENT\t", 1)], [("1-URGENT", 1)], {1: (0,)}, ["1_v1"]),
    ],
)
def test_char_padding_tolerance(canonical, variant, padding_columns, expected_keys):
    conn = _padding_connection(canonical, variant)
    divergences = find_divergences(
        conn, _benchmark_validating_strictly(), lambda q: "SELECT 1", char_padding_columns=padding_columns
    )
    assert [d.key for d in divergences] == expected_keys
