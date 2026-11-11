import json
from pathlib import Path

import duckdb
import pytest

from _project.scripts.explorer_pipeline.pipeline import ExplorerPipeline
from _project.scripts.results_explorer_snapshot_invariants import check_snapshot
from tests.unit.scripts.explorer_pipeline.conftest import MINIMAL_BUNDLE

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_rejects_wal_assisted_snapshot_before_opening_database(tmp_path: Path) -> None:
    db_path = tmp_path / "results.duckdb"
    db_path.write_bytes(b"not a complete database")
    db_path.with_name("results.duckdb.wal").write_bytes(b"sidecar")

    errors = check_snapshot(db_path)

    assert errors == ["snapshot is not self-contained: WAL sidecar exists at results.duckdb.wal"]


def test_promoted_snapshot_reopens_from_db_without_wal_sidecar(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    bundles_dir = data_dir / "bundles"
    bundles_dir.mkdir(parents=True)
    (bundles_dir / "result.json").write_text(json.dumps(MINIMAL_BUNDLE), encoding="utf-8")
    output_dir = tmp_path / "output"

    ExplorerPipeline().run(data_dir, output_dir)

    db_path = output_dir / "results.duckdb"
    assert not db_path.with_name("results.duckdb.wal").exists()
    assert check_snapshot(db_path) == []
    with duckdb.connect(str(db_path), read_only=True) as con:
        assert con.execute("SELECT COUNT(*) FROM results").fetchone()[0] == 1


