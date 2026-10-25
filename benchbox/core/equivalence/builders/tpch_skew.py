from __future__ import annotations

import importlib
from dataclasses import replace
from pathlib import Path

from benchbox.core.equivalence.builders.base import CrossSurfaceData, _assemble_simple_duckdb_cell

TPCH_SKEW_GATE_SEED = 42


def build_tpch_skew_duckdb(scale_factor: float, output_dir: Path) -> CrossSurfaceData:
    from benchbox.core.tpch.schema import TABLES
    from benchbox.core.tpch_skew.skew_config import SkewPreset, get_preset_config
    from benchbox.tpch_skew import TPCHSkew

    def _dataframe_query(query_id: str) -> object:
        importlib.import_module("benchbox.core.dataframe.benchmark_suite")
        from benchbox.core.tpch.dataframe_queries import set_scale_factor_for_benchmark
        from benchbox.core.tpch_skew.dataframe_queries import TPCH_SKEW_DATAFRAME_QUERIES

        set_scale_factor_for_benchmark("tpch_skew", scale_factor)
        return TPCH_SKEW_DATAFRAME_QUERIES.get_or_raise(f"Q{query_id}")

    skew_config = replace(get_preset_config(SkewPreset.MODERATE), seed=TPCH_SKEW_GATE_SEED)
    return _assemble_simple_duckdb_cell(
        TPCHSkew(scale_factor=scale_factor, output_dir=Path(output_dir), skew_config=skew_config),
        Path(output_dir),
        [table.name for table in TABLES],
        label="TPC-H Skew",
        dataframe_query=_dataframe_query,
    )
