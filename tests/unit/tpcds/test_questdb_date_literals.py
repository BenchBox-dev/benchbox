from __future__ import annotations

import re

import pytest

from benchbox.core.tpcds.benchmark.runner import TPCDSBenchmark
from benchbox.core.tpcds_obt.benchmark import TPCDSOBTBenchmark

pytestmark = [pytest.mark.unit, pytest.mark.medium]

DATE_LITERAL = re.compile(r"'(\d{4})-(\d{1,2})-(\d{1,2})'")
SEEDS = (None, 1001, 1, 2, 3, 4, 5)
QUERY_IDS = (16, 94, 95)


def _padded(sql: str) -> bool:
    literals = DATE_LITERAL.findall(sql)
    return bool(literals) and all(len(month) == 2 and len(day) == 2 for _, month, day in literals)


def _unpadded_source(sql: str) -> bool:
    return any(len(month) == 1 or len(day) == 1 for _, month, day in DATE_LITERAL.findall(sql))


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("query_id", QUERY_IDS)
def test_tpcds_questdb_date_bounds_are_zero_padded(tmp_path, query_id, seed):
    benchmark = TPCDSBenchmark(scale_factor=0.01, output_dir=tmp_path)
    source = benchmark.get_query(query_id, seed=seed, dialect="duckdb")
    translated = benchmark.get_query(query_id, seed=seed, dialect="questdb")
    assert _unpadded_source(source)
    assert _padded(translated)
    assert "DATEADD('d', 60, CAST('" in translated


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("query_id", QUERY_IDS)
def test_tpcds_obt_questdb_date_bounds_are_zero_padded(tmp_path, query_id, seed):
    benchmark = TPCDSOBTBenchmark(scale_factor=1.0, output_dir=tmp_path)
    source = benchmark.get_query(query_id, seed=seed, scale_factor=0.01, dialect="duckdb")
    translated = benchmark.get_query(query_id, seed=seed, scale_factor=0.01, dialect="questdb")
    assert _unpadded_source(source)
    assert _padded(translated)
