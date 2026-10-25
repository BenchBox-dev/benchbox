# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark(TM) H (TPC-H) - Copyright (C) Transaction Processing Performance Council.
# This implementation is derived from TPC-H.

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from benchbox.core.equivalence.dataframe_surface import (
    DATAFRAME_BACKENDS,
    SurfaceDivergence,
    build_dataframe_contexts as _build_dataframe_contexts,
    fetch_reference_rows,
    find_surface_divergences,
    materialize_rows,
)
from benchbox.core.tpchavoc.benchmark import TPCHavocBenchmark
from benchbox.core.tpchavoc.equivalence import EQUIVALENCE_SCALE, build_duckdb_with_tpch
from benchbox.core.tpchavoc.validation import ValidationError

__all__ = [
    "DATAFRAME_BACKENDS",
    "KNOWN_DIVERGENCES",
    "build_dataframe_contexts",
    "fetch_canonical_rows",
    "find_dataframe_divergences",
    "main",
    "materialize_rows",
]

KNOWN_DIVERGENCES: dict[str, str] = {}

fetch_canonical_rows = fetch_reference_rows


def build_dataframe_contexts(connection: Any) -> dict[str, Any]:
    from benchbox.core.tpch.schema import TABLES

    return _build_dataframe_contexts(connection, TABLES)


def find_dataframe_divergences(
    connection: Any,
    benchmark: TPCHavocBenchmark,
    canonical_query: Callable[[int], str],
    contexts: dict[str, Any],
    *,
    query_ids: list[int] | None = None,
    backends: tuple[str, ...] = DATAFRAME_BACKENDS,
) -> list[SurfaceDivergence]:
    from benchbox.core.tpch.dataframe_queries import seeded_parameter_overrides

    ids = query_ids if query_ids is not None else benchmark.get_implemented_queries()
    registry = benchmark.get_dataframe_queries()

    def reference_rows(query_id: int) -> list[tuple[Any, ...]]:
        return fetch_canonical_rows(connection, canonical_query(query_id))

    def candidate_cells(
        query_id: int,
    ) -> Iterable[tuple[str, Callable[[list[tuple[Any, ...]]], None]]]:
        for variant_id in range(1, 11):
            query = registry.get_or_raise(f"Q{query_id}v{variant_id}")
            for backend in backends:
                impl = query.expression_impl if backend == "expression" else query.pandas_impl

                def check(
                    reference: list[tuple[Any, ...]],
                    *,
                    impl: Any = impl,
                    backend: str = backend,
                    variant_id: int = variant_id,
                ) -> None:
                    candidate = materialize_rows(impl(contexts[backend]))
                    benchmark.validate_variant_equivalence(query_id, variant_id, reference, candidate)

                yield f"v{variant_id}:{backend}", check

    with seeded_parameter_overrides(None, benchmark.scale_factor, 0):
        return find_surface_divergences(
            ids,
            reference_rows=reference_rows,
            candidate_cells=candidate_cells,
            validation_error=ValidationError,
            reference_failure_cell="v0:canonical",
        )


def main() -> int:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        connection, tpchavoc, tpch = build_duckdb_with_tpch(EQUIVALENCE_SCALE, tmp)
        try:
            contexts = build_dataframe_contexts(connection)
            divergences = find_dataframe_divergences(connection, tpchavoc, lambda q: tpch.get_query(q), contexts)
        finally:
            connection.close()

    total = len(tpchavoc.get_implemented_queries()) * 10 * len(DATAFRAME_BACKENDS)
    found = {d.key for d in divergences}
    new = sorted(found - set(KNOWN_DIVERGENCES), key=_sort_key)
    resolved = sorted(set(KNOWN_DIVERGENCES) - found, key=_sort_key)

    print(f"TPC-Havoc DataFrame variant equivalence vs canonical TPC-H @ SF={EQUIVALENCE_SCALE} (DuckDB-backed)")
    print(
        f"  checked {total} variant-backend cells - "
        f"{len(divergences)} divergent, {total - len(divergences)} equivalent\n"
    )

    by_class: dict[str, list[SurfaceDivergence]] = {}
    for divergence in sorted(divergences, key=lambda d: _sort_key(d.key)):
        klass = KNOWN_DIVERGENCES.get(divergence.key, "UNCLASSIFIED")
        by_class.setdefault(klass, []).append(divergence)
    for klass in sorted(by_class):
        print(f"  [{klass}]")
        for divergence in by_class[klass]:
            print(f"    {divergence.key}: {divergence.detail}")
        print()

    if new:
        print(f"GATE FAILURE - unclassified DataFrame divergences from canonical TPC-H: {new}")
    if resolved:
        print(f"Previously-known divergences now equivalent - update KNOWN_DIVERGENCES: {resolved}")
    if not new and not resolved:
        print("All DataFrame variants equivalent to canonical TPC-H (modulo KNOWN_DIVERGENCES).")
    return 1 if new else 0


def _sort_key(key: str) -> tuple[int, int, str]:
    cell, _, backend = key.partition(":")
    query, _, variant = cell.partition("_v")
    return int(query), int(variant or 0), backend


if __name__ == "__main__":
    raise SystemExit(main())
