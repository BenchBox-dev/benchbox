from __future__ import annotations

import pytest

from benchbox.core.results.query_normalizer import normalize_query_id
from benchbox.validation.bundle import _normalize_coverage_query_id

pytestmark = [pytest.mark.unit, pytest.mark.fast]

QUERY_IDS = [
    "Q1",
    "q14a",
    "Q14A",
    "18",
    "QUERY_1",
    "query22",
    "Q18.sql",
    "Qtopn",
    "Qqualify_cume_dist",
    "qualify_cume_dist",
    "quantile_cont",
    "query_cache_probe",
    "QC1",
    "QPR1",
    "aggregation_distinct_groupby",
]


@pytest.mark.parametrize("query_id", QUERY_IDS)
def test_coverage_normaliser_matches_core_normaliser(query_id: str) -> None:
    assert _normalize_coverage_query_id(query_id) == normalize_query_id(query_id)


def test_coverage_normaliser_keeps_named_queries_that_start_with_q() -> None:
    assert _normalize_coverage_query_id("qualify_row_number") == "qualify_row_number"


@pytest.mark.parametrize("raw", [None, 7, "", "   "])
def test_coverage_normaliser_rejects_non_ids(raw: object) -> None:
    assert _normalize_coverage_query_id(raw) is None
