# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

pytest.importorskip("polars", reason="Polars not installed")
pytest.importorskip("pandas", reason="Pandas not installed")
pytest.importorskip("duckdb", reason="DuckDB not installed")

from benchbox.core.equivalence.cross_surface import (
    EQUIVALENCE_SCALE,
    GATES,
    build_production_contexts,
    count_executed_cells,
    find_cross_surface_divergences,
)
from benchbox.core.expected_results.ssb_results import SSB_EXPECTED_ROW_COUNTS, normalize_ssb_query_id
from benchbox.core.tpchavoc.validation import ResultValidator

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
    pytest.mark.duckdb,
]

_PREVIOUSLY_EMPTY = ("Q2.1", "Q2.2", "Q2.3", "Q3.3", "Q3.4", "Q4.3")


@pytest.fixture(scope="module")
def ssb_cell(tmp_path_factory):
    gate = GATES["ssb"]
    data = gate.build(EQUIVALENCE_SCALE, tmp_path_factory.mktemp("ssb_cell"))
    try:
        yield data
    finally:
        data.connection.close()


def test_ssb_dataframe_surface_equivalent_to_sql(ssb_cell):
    gate = GATES["ssb"]
    data = ssb_cell
    contexts = build_production_contexts(data.benchmark, data.data_dir, backends=gate.backends)
    divergences = find_cross_surface_divergences(
        data.connection,
        query_ids=data.query_ids,
        reference_sql=data.reference_sql,
        dataframe_query=data.dataframe_query,
        contexts=contexts,
        validator=ResultValidator(tolerance=gate.tolerance),
        backends=gate.backends,
    )
    coverage = count_executed_cells(data.query_ids, data.dataframe_query, gate.backends)

    missing = sorted(backend for backend, count in coverage.items() if count == 0)
    assert not missing, f"gated SSB backend(s) implement no queries: {missing}"

    unexpected = {d.key for d in divergences} - set(gate.known_divergences)
    assert not unexpected, "SSB DataFrame surface diverges from SQL: " + ", ".join(
        f"{d.key} ({d.detail})" for d in divergences if d.key in unexpected
    )


def test_ssb_previously_empty_queries_now_discriminate(ssb_cell):
    data = ssb_cell
    assert not GATES["ssb"].legitimately_empty, (
        "the SSB gate must carry no legitimately_empty classification once the data is fixed"
    )
    empty = [qid for qid in _PREVIOUSLY_EMPTY if not data.connection.execute(data.reference_sql(qid)).fetchall()]
    assert not empty, (
        f"SSB queries regressed to vacuous (0 reference rows) at SF={EQUIVALENCE_SCALE}: {empty} - "
        "the generator no longer emits values matching the canonical query literals (#870)."
    )


def test_ssb_sql_reference_row_counts_match_the_recorded_expectations(ssb_cell):
    data = ssb_cell
    actual = {
        normalize_ssb_query_id(qid): len(data.connection.execute(data.reference_sql(qid)).fetchall())
        for qid in data.query_ids
    }
    assert actual == SSB_EXPECTED_ROW_COUNTS[EQUIVALENCE_SCALE]
