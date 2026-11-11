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
from benchbox.core.tpchavoc.validation import ResultValidator

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
    pytest.mark.duckdb,
]


@pytest.mark.timeout(600)
def test_datavault_dataframe_surface_equivalent_to_sql(tmp_path):
    gate = GATES["datavault"]
    data = gate.build(gate.scale_factor, tmp_path)
    connection = data.connection
    reference_row_counts: dict = {}
    try:
        contexts = build_production_contexts(
            data.benchmark, data.data_dir, backends=gate.backends, scale_factor=gate.scale_factor
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

    expected_coverage = {backend: len(data.query_ids) for backend in gate.backends}
    assert coverage == expected_coverage, f"Data Vault backend coverage shrank: {coverage}"

    vacuous = sorted(qid for qid, count in reference_row_counts.items() if count == 0)
    unclassified = [qid for qid in vacuous if qid not in gate.legitimately_empty]
    assert not unclassified, f"Data Vault reference queries returned zero rows: {unclassified}"

    assert not divergences, "Data Vault DataFrame surface diverges from SQL: " + ", ".join(
        f"{d.key} ({d.detail})" for d in divergences
    )


