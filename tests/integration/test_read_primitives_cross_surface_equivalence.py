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
    find_cross_surface_dtype_divergences,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
    pytest.mark.duckdb,
]


@pytest.mark.timeout(600)
def test_read_primitives_dataframe_surface_equivalent_to_sql(tmp_path):
    gate = GATES["read_primitives"]
    data = gate.build(gate.scale_factor, tmp_path)
    connection = data.connection
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
            validator=gate.build_validator(),
            backends=gate.backends,
        )
        coverage = count_executed_cells(data.query_ids, data.dataframe_query, gate.backends)
    finally:
        connection.close()

    missing = sorted(backend for backend, count in coverage.items() if count == 0)
    assert not missing, f"gated Read Primitives backend(s) implement no queries: {missing}"

    unexpected = {d.key for d in divergences} - set(gate.known_divergences)
    assert not unexpected, "Read Primitives DataFrame surface diverges from SQL: " + ", ".join(
        f"{d.key} ({d.detail})" for d in divergences if d.key in unexpected
    )


@pytest.mark.timeout(600)
def test_read_primitives_expression_frame_dtypes_match_sql(tmp_path):
    gate = GATES["read_primitives"]
    data = gate.build(gate.scale_factor, tmp_path)
    connection = data.connection
    try:
        contexts = build_production_contexts(
            data.benchmark, data.data_dir, backends=gate.backends, scale_factor=gate.scale_factor
        )
        divergences, compared = find_cross_surface_dtype_divergences(
            connection,
            query_ids=data.query_ids,
            reference_sql=data.reference_sql,
            dataframe_query=data.dataframe_query,
            contexts=contexts,
            backends=("expression",),
            skip_keys=gate.dtype_skip_keys,
            skip_query_ids=frozenset({"json_extract_nested"}),
        )
    finally:
        connection.close()

    assert compared.get("expression", 0) > 100, (
        f"dtypes compared on too few cells ({compared}): the cell must not go green by comparing nothing"
    )
    assert not divergences, "Read Primitives expression frame dtypes diverge from SQL: " + ", ".join(
        f"{d.key} ({d.detail})" for d in divergences
    )


