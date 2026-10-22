# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

pytest.importorskip("polars", reason="Polars not installed")
pytest.importorskip("pandas", reason="Pandas not installed")
pytest.importorskip("duckdb", reason="DuckDB not installed")

from benchbox.core.equivalence.cross_surface import (
    GATES,
    build_production_contexts,
    count_executed_cells,
    find_cross_surface_divergences,
)
from benchbox.core.joinorder.queries import CANONICAL_JOINORDER_QUERIES
from benchbox.core.tpchavoc.validation import ResultValidator

MEDIUM_EQUIVALENCE_SCALE = 0.01
MEDIUM_NON_VACUOUS_QUERY_FLOOR = 31

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
    pytest.mark.duckdb,
]


def test_joinorder_synthetic_dataframe_surface_equivalent_to_sql(tmp_path):
    gate = GATES["joinorder_synthetic"]
    data = gate.build(MEDIUM_EQUIVALENCE_SCALE, tmp_path)
    connection = data.connection
    reference_row_counts: dict[str, int] = {}
    try:
        contexts = build_production_contexts(
            data.benchmark, data.data_dir, backends=gate.backends, scale_factor=MEDIUM_EQUIVALENCE_SCALE
        )
        divergences = find_cross_surface_divergences(
            connection,
            query_ids=data.query_ids,
            reference_sql=data.reference_sql,
            dataframe_query=data.dataframe_query,
            contexts=contexts,
            validator=ResultValidator(tolerance=gate.tolerance),
            backends=gate.backends,
            reference_row_counts=reference_row_counts,
        )
        coverage = count_executed_cells(data.query_ids, data.dataframe_query, gate.backends)
    finally:
        connection.close()

    assert set(data.query_ids) == set(CANONICAL_JOINORDER_QUERIES)
    expected_coverage = {backend: len(CANONICAL_JOINORDER_QUERIES) for backend in gate.backends}
    assert coverage == expected_coverage, f"joinorder_synthetic backend coverage shrank: {coverage}"

    non_vacuous = sum(1 for count in reference_row_counts.values() if count > 0)
    assert non_vacuous >= MEDIUM_NON_VACUOUS_QUERY_FLOOR, (
        f"only {non_vacuous} queries returned reference rows at SF {MEDIUM_EQUIVALENCE_SCALE}; "
        f"need at least {MEDIUM_NON_VACUOUS_QUERY_FLOOR}"
    )

    unexpected = {d.key for d in divergences} - set(gate.known_divergences)
    assert not unexpected, "joinorder_synthetic DataFrame surface diverges from SQL: " + ", ".join(
        f"{d.key} ({d.detail})" for d in divergences if d.key in unexpected
    )
