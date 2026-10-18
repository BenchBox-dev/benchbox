# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark(TM) H (TPC-H) - Copyright (C) Transaction Processing Performance Council.
# This implementation is derived from TPC-H.

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

from benchbox.core.tpchavoc.benchmark import TPCHavocBenchmark
from benchbox.core.tpchavoc.equivalence import find_divergences

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
    pytest.mark.tpchavoc,
]


class _FakeResult:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple]:
        return self._rows


class _RecordingConnection:
    def __init__(self, rows: list[tuple]) -> None:
        self.rows = rows
        self.executed: list[str] = []

    def execute(self, sql: str) -> _FakeResult:
        self.executed.append(sql)
        return _FakeResult(self.rows)


@pytest.fixture
def havoc_benchmark() -> TPCHavocBenchmark:
    return TPCHavocBenchmark(scale_factor=0.1)


def test_default_path_is_identity_and_runs_every_variant(havoc_benchmark):
    connection = _RecordingConnection(rows=[(1,)])

    divergences = find_divergences(
        connection,
        havoc_benchmark,
        lambda _q: "SELECT 1 AS canonical",
        query_ids=[3],
    )

    assert divergences == []
    assert connection.executed[0] == "SELECT 1 AS canonical"
    assert sum(1 for s in connection.executed if "canonical" in s) == 1
    assert not any("/*translated*/" in s for s in connection.executed)


def test_translate_variant_applies_only_to_variants(havoc_benchmark):
    connection = _RecordingConnection(rows=[(1,)])

    divergences = find_divergences(
        connection,
        havoc_benchmark,
        lambda _q: "SELECT 1 AS canonical",
        query_ids=[3],
        translate_variant=lambda sql: sql + " /*translated*/",
    )

    assert divergences == []
    canonical_sql = [s for s in connection.executed if "canonical" in s]
    variant_sql = [s for s in connection.executed if "canonical" not in s]
    assert len(canonical_sql) == 1
    assert "/*translated*/" not in canonical_sql[0], "canonical must not be re-translated"
    assert variant_sql, "variants should have been executed"
    assert all(s.endswith("/*translated*/") for s in variant_sql), "every variant must be translated"


def test_skip_variants_are_excluded_not_executed_not_counted(havoc_benchmark):
    connection = _RecordingConnection(rows=[(1,)])

    skipped = {"3_v2", "3_v5"}
    divergences = find_divergences(
        connection,
        havoc_benchmark,
        lambda _q: "SELECT 1 AS canonical",
        query_ids=[3],
        skip_variants=skipped,
    )

    assert divergences == []
    assert len(connection.executed) == 1 + (10 - len(skipped))
    keys = {d.key for d in divergences}
    assert keys.isdisjoint(skipped)


def test_char_padding_normalization_is_opt_in(havoc_benchmark):
    connection = _RecordingConnection(rows=[("1-URGENT       ",)])

    divergences = find_divergences(
        connection,
        havoc_benchmark,
        lambda _q: "SELECT priority",
        query_ids=[3],
        char_padding_columns={3: (0,)},
    )

    assert divergences == []
