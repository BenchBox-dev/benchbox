from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium, pytest.mark.tpcds]


def test_gate_sql_and_bindings_use_the_generated_data_scale(monkeypatch, tmp_path):
    import benchbox.core.equivalence.builders.base as base
    from benchbox.core.equivalence.builders.tpcds import build_tpcds_duckdb
    from benchbox.core.tpcds.benchmark import TPCDSBenchmark
    from benchbox.core.tpcds.dataframe_queries.production_binding import clear_binding_cache
    from benchbox.tpcds import TPCDS

    monkeypatch.setenv("BENCHBOX_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(TPCDS, "generate_data", lambda self: [])
    monkeypatch.setattr(base, "_load_duckdb_cell", lambda *args, **kwargs: object())
    clear_binding_cache()
    data = build_tpcds_duckdb(0.01, tmp_path / "built")
    scale_one = TPCDSBenchmark(scale_factor=1.0, output_dir=tmp_path / "one")

    assert data.benchmark.scale_factor == 0.01
    assert data.reference_sql("44") == data.benchmark.get_query(44, dialect="duckdb", seed=1001)
    assert data.reference_sql("44") != scale_one.get_query(44, dialect="duckdb", seed=1001)
    binding = data.dataframe_query.bindings[44]
    assert binding.scale_factor == data.query_parameters["scale_factor"] == 0.01
    assert (
        str(binding.parameters["store_sk"])
        != scale_one.query_manager.dsqgen.generate_parameter_log(44, scale_factor=1.0, seed=1001).substitutions[
            "STORE.01"
        ]
    )


