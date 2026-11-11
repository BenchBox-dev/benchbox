# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
from pathlib import Path

import pytest

pytest.importorskip("polars", reason="Polars not installed")
pytest.importorskip("pandas", reason="Pandas not installed")
pytest.importorskip("duckdb", reason="DuckDB not installed")

from benchbox.core.equivalence.cross_surface import get_gate
from benchbox.utils.datagen_manifest import MANIFEST_FILENAME, load_manifest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.duckdb,
]


@pytest.mark.timeout(1200)
def test_enforced_gate_forces_regeneration_records_manifest_and_runs_all_cells(tmp_path, caplog):
    gate = get_gate("datavault")

    with caplog.at_level(logging.INFO, logger="benchbox.core.equivalence.builders.datavault"):
        data = gate.build(gate.scale_factor, tmp_path)
    try:
        assert data.benchmark.force_regenerate is True

        manifest_path = Path(data.data_dir) / MANIFEST_FILENAME
        assert manifest_path.exists(), "builder must leave the probe manifest behind"
        manifest = load_manifest(manifest_path)
        assert manifest.get("benchmark") == "datavault"
        assert float(manifest.get("scale_factor", 0)) == pytest.approx(gate.scale_factor)
        assert len(manifest.get("tables", {})) == 21
        assert "probe manifest" in caplog.text, "every run must record which manifest its cell came from"

        table_files = list(Path(data.data_dir).glob("*.tbl"))
        assert len(table_files) == 21, f"expected 21 table files, found {len(table_files)}"
        for path in table_files:
            path.write_text("")
        before = manifest_path.read_bytes()

        data.connection.close()
        data = gate.build(gate.scale_factor, tmp_path)
        assert manifest_path.read_bytes() != before, "rebuild must rewrite the probe manifest"
        from benchbox.core.datavault.schema import LOADING_ORDER

        counts = {
            table: data.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in LOADING_ORDER
        }
        assert len(counts) == 21
        assert all(count > 0 for count in counts.values()), f"cell not fully regenerated: {counts}"
    finally:
        data.connection.close()


