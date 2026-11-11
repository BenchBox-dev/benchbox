# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark(TM) H (TPC-H) - Copyright (C) Transaction Processing Performance Council.
# This implementation is derived from TPC-H.

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

pytest.importorskip("polars", reason="Polars not installed")
pytest.importorskip("pandas", reason="Pandas not installed")

from benchbox.core.tpchavoc.dataframe_equivalence import (
    KNOWN_DIVERGENCES,
    build_dataframe_contexts,
    find_dataframe_divergences,
)
from benchbox.core.tpchavoc.equivalence import EQUIVALENCE_SCALE, build_duckdb_with_tpch

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
    pytest.mark.duckdb,
    pytest.mark.tpchavoc,
]


@pytest.fixture(scope="module")
def populated_environment(tmp_path_factory):
    output_dir = tmp_path_factory.mktemp("tpchavoc_dataframe_equivalence")
    connection, tpchavoc, tpch = build_duckdb_with_tpch(EQUIVALENCE_SCALE, output_dir)
    try:
        contexts = build_dataframe_contexts(connection)
        yield connection, tpchavoc, tpch, contexts
    finally:
        connection.close()


FIXED_DEFECT_QUERIES = (3, 5, 10, 15)


def test_fixed_dataframe_variant_families_equivalent_to_canonical(populated_environment):
    connection, tpchavoc, tpch, contexts = populated_environment

    divergences = find_dataframe_divergences(
        connection,
        tpchavoc,
        lambda query_id: tpch.get_query(query_id),
        contexts,
        query_ids=list(FIXED_DEFECT_QUERIES),
    )

    unexpected = {d.key for d in divergences} - set(KNOWN_DIVERGENCES)
    assert not unexpected, "DataFrame variant(s) diverge from canonical TPC-H: " + ", ".join(
        f"{d.key} ({d.detail})" for d in divergences if d.key in unexpected
    )


