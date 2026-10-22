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

MEDIUM_EQUIVALENCE_SCALE = 0.01

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
    pytest.mark.duckdb,
]


@pytest.mark.timeout(600)
def test_coffeeshop_dataframe_surface_equivalent_to_sql(tmp_path):
    gate = GATES["coffeeshop"]
    data = gate.build(MEDIUM_EQUIVALENCE_SCALE, tmp_path)
    connection = data.connection
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
        )
        coverage = count_executed_cells(data.query_ids, data.dataframe_query, gate.backends)
    finally:
        connection.close()

    missing = sorted(backend for backend, count in coverage.items() if count == 0)
    assert not missing, f"gated CoffeeShop backend(s) implement no queries: {missing}"

    unexpected = {d.key for d in divergences} - set(gate.known_divergences)
    assert not unexpected, "CoffeeShop DataFrame surface diverges from SQL: " + ", ".join(
        f"{d.key} ({d.detail})" for d in divergences if d.key in unexpected
    )
