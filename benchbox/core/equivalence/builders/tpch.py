from __future__ import annotations

import importlib
from pathlib import Path

from benchbox.core.equivalence.builders.base import CrossSurfaceData, _assemble_simple_duckdb_cell


def build_tpch_duckdb(scale_factor: float, output_dir: Path) -> CrossSurfaceData:
    from benchbox.core.tpch.schema import TABLES
    from benchbox.tpch import TPCH

    def _dataframe_query(query_id: str) -> object:
        importlib.import_module("benchbox.core.dataframe.benchmark_suite")
        from benchbox.core.tpch.dataframe_queries import TPCH_DATAFRAME_QUERIES, set_scale_factor_for_benchmark

        set_scale_factor_for_benchmark("tpch", scale_factor)
        return TPCH_DATAFRAME_QUERIES.get_or_raise(f"Q{query_id}")

    return _assemble_simple_duckdb_cell(
        TPCH(scale_factor=scale_factor, output_dir=Path(output_dir)),
        Path(output_dir),
        [table.name for table in TABLES],
        label="TPC-H",
        dataframe_query=_dataframe_query,
    )
