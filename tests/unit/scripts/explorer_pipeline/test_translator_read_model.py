from __future__ import annotations

import copy
import json
from pathlib import Path

import duckdb
import pytest
from click.testing import CliRunner

from _project.scripts.explorer_pipeline.contract import (
    EXPLORER_BUILD_CONTRACT,
    EXPLORER_READ_MODEL_VERSION,
    TRANSLATOR_RESULT_COLUMNS,
)
from _project.scripts.explorer_pipeline.pipeline import ExplorerPipeline
from _project.scripts.explorer_publish import explorer_publish
from benchbox.validation.bundle import SQL_TRANSLATOR_VERSION_KEY
from tests.unit.scripts.explorer_pipeline.conftest import MINIMAL_BUNDLE

pytestmark = [pytest.mark.unit, pytest.mark.medium]

IDENTITY = {
    "version": "30.24.0.17",
    "fork_sha": "b181afce8e9e9cd78b12ba78151d1cf7dbc9adf7",
    "upstream_sha": "5d588c0f32d17a08191898638f67ff27bb0a13ff",
    "wheel_sha256": "f91d099155ca4be92397b8ab2e86ef5e07b4bee0aefcec31859afced9f4fc526",
}
TRANSLATOR_COLUMNS = [name for name, _ in TRANSLATOR_RESULT_COLUMNS]
DESIGN_SCHEMA = Path(__file__).resolve().parents[4] / "_project" / "design" / "browser-duckdb-schema.sql"


def _bundle(name: str, *, runtime: dict | None = None, attested: dict | None = None, mode: str = "sql") -> dict:
    payload = copy.deepcopy(MINIMAL_BUNDLE)
    payload["run"]["id"] = name
    payload["config"] = {**payload.get("config", {}), "mode": mode}
    if runtime is not None:
        payload.setdefault("execution", {})["translation"] = {"status": "passed", "runtime": runtime}
    return payload


def _corpus(tmp_path: Path) -> Path:
    bundles = tmp_path / "data" / "bundles"
    bundles.mkdir(parents=True)
    runtime = {"distribution": "benchbox-sqlglot", **IDENTITY}
    attestation = {**IDENTITY, "evidence": "Reconciled wheel table."}
    cases = {
        "recorded": _bundle("recorded", runtime=runtime),
        "attested": _bundle("attested"),
        "unrecorded": _bundle("unrecorded"),
        "dataframe": _bundle("dataframe", mode="dataframe"),
    }
    for name, payload in cases.items():
        (bundles / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")
    sidecar = {"bundle_file": "attested.json", "bundle_hash": "h", SQL_TRANSLATOR_VERSION_KEY: attestation}
    (bundles / "attested.manifest.json").write_text(json.dumps(sidecar), encoding="utf-8")
    return tmp_path / "data"


def _build(tmp_path: Path, data: Path, **kwargs: int) -> Path:
    out = tmp_path / "out"
    ExplorerPipeline().run(data, out, **kwargs)
    return out / "results.duckdb"


def _columns(db: Path) -> list[str]:
    with duckdb.connect(str(db), read_only=True) as con:
        return [row[0] for row in con.execute("DESCRIBE results").fetchall()]


def _design_results_columns() -> list[str]:
    sql = DESIGN_SCHEMA.read_text(encoding="utf-8")
    block = sql.split("CREATE TABLE IF NOT EXISTS results (", 1)[1].split("\n);", 1)[0]
    return [line.split()[0] for line in block.splitlines() if line.strip()]


def test_default_build_stays_on_read_model_17_without_translator_columns(tmp_path: Path) -> None:
    db = _build(tmp_path, _corpus(tmp_path))

    with duckdb.connect(str(db), read_only=True) as con:
        assert con.execute("SELECT read_model_version FROM metadata").fetchone() == (17,)
    assert EXPLORER_READ_MODEL_VERSION == 17
    assert EXPLORER_BUILD_CONTRACT["read_model_version"] == 17
    assert _columns(db) == _design_results_columns()
    assert not set(TRANSLATOR_COLUMNS) & set(_columns(db))


def test_selecting_17_explicitly_matches_the_default(tmp_path: Path) -> None:
    data = _corpus(tmp_path)
    default = _columns(_build(tmp_path, data))
    explicit = _columns(_build(tmp_path, data, read_model_version=17))

    assert explicit == default


def test_read_model_18_appends_exactly_the_translator_columns(tmp_path: Path) -> None:
    db = _build(tmp_path, _corpus(tmp_path), read_model_version=18)

    with duckdb.connect(str(db), read_only=True) as con:
        assert con.execute("SELECT read_model_version FROM metadata").fetchone() == (18,)
        described = con.execute("DESCRIBE results").fetchall()
    assert [(row[0], row[1]) for row in described[-3:]] == [
        ("translator_version", "VARCHAR"),
        ("translator_fork_sha", "VARCHAR"),
        ("translator_version_source", "VARCHAR"),
    ]
    assert [row[0] for row in described[:-3]] == _design_results_columns()


def test_read_model_18_fills_translator_columns_by_source(tmp_path: Path) -> None:
    db = _build(tmp_path, _corpus(tmp_path), read_model_version=18)

    with duckdb.connect(str(db), read_only=True) as con:
        rows = con.execute(
            "SELECT translator_version_source, translator_version, translator_fork_sha FROM results"
        ).fetchall()
    by_source = {source: (version, fork) for source, version, fork in rows}
    assert sorted(by_source) == ["attested", "not_applicable", "not_recorded", "recorded"]
    assert by_source["recorded"] == (IDENTITY["version"], IDENTITY["fork_sha"])
    assert by_source["attested"] == (IDENTITY["version"], IDENTITY["fork_sha"])
    assert by_source["not_recorded"] == (None, None)
    assert by_source["not_applicable"] == (None, None)


def test_read_model_18_leaves_every_other_column_unchanged(tmp_path: Path) -> None:
    data = _corpus(tmp_path)
    base = tmp_path / "v17"
    new = tmp_path / "v18"
    ExplorerPipeline().run(data, base)
    ExplorerPipeline().run(data, new, read_model_version=18)
    shared = ", ".join(_columns(base / "results.duckdb"))
    query = f"SELECT {shared} FROM results ORDER BY result_id"

    with (
        duckdb.connect(str(base / "results.duckdb"), read_only=True) as old,
        duckdb.connect(str(new / "results.duckdb"), read_only=True) as upgraded,
    ):
        assert old.execute(query).fetchall() == upgraded.execute(query).fetchall()


def test_an_unsupported_read_model_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsupported read model version 19"):
        ExplorerPipeline().run(_corpus(tmp_path), tmp_path / "out", read_model_version=19)


def test_the_build_command_selects_the_read_model(tmp_path: Path) -> None:
    data = _corpus(tmp_path)
    runner = CliRunner()
    default = runner.invoke(explorer_publish, ["build", "--data-dir", str(data), "--output", str(tmp_path / "a")])
    selected = runner.invoke(
        explorer_publish,
        ["build", "--data-dir", str(data), "--output", str(tmp_path / "b"), "--read-model-version", "18"],
    )

    assert default.exit_code == 0, default.output
    assert selected.exit_code == 0, selected.output
    assert "translator_version" not in _columns(tmp_path / "a" / "results.duckdb")
    assert "translator_version" in _columns(tmp_path / "b" / "results.duckdb")
