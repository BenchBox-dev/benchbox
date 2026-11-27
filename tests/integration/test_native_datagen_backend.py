from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

if os.environ.get("BENCHBOX_REQUIRE_NATIVE_DATAGEN") == "1":
    import benchbox_datagen
else:
    benchbox_datagen = pytest.importorskip("benchbox_datagen", reason="benchbox-datagen is not installed")

duckdb = pytest.importorskip("duckdb", reason="DuckDB not installed")

from benchbox import TPCDS, TPCH
from benchbox.core.runner.conversion import FormatConversionOrchestrator
from benchbox.core.runner.runner import get_table_schemas_from_benchmark
from benchbox.utils.datagen_backend import NATIVE_BACKEND_NAME
from benchbox.utils.format_converters import ConversionOptions
from benchbox.utils.native_datagen import bind_datagen_backend, write_native_tables

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.data_generation,
]

TPCH_PARITY_SCALES = (0.01, 0.1, 1.0)
CHUNK = 1 << 22


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest(directory: Path) -> dict:
    return json.loads((directory / "_datagen_manifest.json").read_text(encoding="utf-8"))


def _manifest_files(manifest: dict) -> dict[str, list[dict]]:
    files: dict[str, list[dict]] = {}
    for table, entry in manifest["tables"].items():
        files[table] = entry["formats"]["tbl"] if isinstance(entry, dict) else entry
    return files


def _generate(cls, scale: float, directory: Path, *, native: bool):
    benchmark = cls(scale_factor=scale, output_dir=directory)
    if native:
        bind_datagen_backend(benchmark, "native", None)
    benchmark.generate_data()
    return benchmark


def _table_hashes(directory: Path, manifest: dict) -> dict[str, list[tuple[str, str]]]:
    return {
        table: [(Path(entry["path"]).name, _sha256(directory / entry["path"])) for entry in entries]
        for table, entries in _manifest_files(manifest).items()
    }


def _row_counts(manifest: dict) -> dict[str, int]:
    return {table: sum(entry["row_count"] for entry in entries) for table, entries in _manifest_files(manifest).items()}


@pytest.mark.tpch
@pytest.mark.parametrize("scale", TPCH_PARITY_SCALES)
def test_tpch_native_backend_matches_legacy_dbgen_through_benchbox(tmp_path: Path, scale: float) -> None:
    legacy_dir = tmp_path / "legacy"
    native_dir = tmp_path / "native"
    _generate(TPCH, scale, legacy_dir, native=False)
    _generate(TPCH, scale, native_dir, native=True)

    legacy_manifest = _manifest(legacy_dir)
    native_manifest = _manifest(native_dir)
    assert "generator_backend" not in legacy_manifest
    assert native_manifest["generator_backend"] == NATIVE_BACKEND_NAME
    assert native_manifest["generator_backend_version"] == benchbox_datagen.__version__
    assert _row_counts(native_manifest) == _row_counts(legacy_manifest)
    assert _table_hashes(native_dir, native_manifest) == _table_hashes(legacy_dir, legacy_manifest)


@pytest.mark.tpch
@pytest.mark.parametrize("scale", TPCH_PARITY_SCALES)
def test_tpch_native_delimited_output_is_thread_invariant_and_matches_dbgen(tmp_path: Path, scale: float) -> None:
    legacy_dir = tmp_path / "legacy"
    _generate(TPCH, scale, legacy_dir, native=False)
    expected = {
        table: _sha256(legacy_dir / entries[0]["path"])
        for table, entries in _manifest_files(_manifest(legacy_dir)).items()
    }
    for threads in (1, None):
        generation = write_native_tables("tpch", scale, tmp_path / f"threads-{threads}", threads=threads)
        assert {table: _sha256(file.path) for table, file in generation.files.items()} == expected


@pytest.mark.tpch
@pytest.mark.parametrize("scale", TPCH_PARITY_SCALES)
def test_tpch_native_parquet_values_match_legacy_converter_both_directions(tmp_path: Path, scale: float) -> None:
    legacy = _generate(TPCH, scale, tmp_path / "legacy", native=False)
    _generate(TPCH, scale, tmp_path / "native", native=True)
    schemas = get_table_schemas_from_benchmark(legacy)
    orchestrator = FormatConversionOrchestrator()
    options = ConversionOptions(compression="zstd")
    legacy_results = orchestrator.convert_benchmark_tables(
        tmp_path / "legacy" / "_datagen_manifest.json", tmp_path / "legacy", "parquet", schemas, options
    )
    native_results = orchestrator.convert_benchmark_tables(
        tmp_path / "native" / "_datagen_manifest.json", tmp_path / "native", "parquet", schemas, options
    )

    native_manifest = _manifest(tmp_path / "native")
    assert native_manifest["parquet_writer"] == "benchbox-datagen-0"
    assert native_manifest["parquet_row_group_size"] == 122_880
    assert native_manifest["generator_backend"] == NATIVE_BACKEND_NAME
    assert sorted(native_results) == sorted(legacy_results)

    connection = duckdb.connect()
    try:
        for table, legacy_result in legacy_results.items():
            legacy_file = str(legacy_result.output_files[0])
            native_file = str(native_results[table].output_files[0])
            assert native_results[table].row_count == legacy_result.row_count
            for left, right in ((legacy_file, native_file), (native_file, legacy_file)):
                missing = connection.execute(
                    f"SELECT count(*) FROM (SELECT * FROM read_parquet('{left}') "
                    f"EXCEPT ALL SELECT * FROM read_parquet('{right}'))"
                ).fetchone()[0]
                assert missing == 0, f"{table}: {missing} rows differ"
    finally:
        connection.close()


@pytest.mark.tpcds
def test_tpcds_native_backend_matches_legacy_dsdgen_through_benchbox(tmp_path: Path) -> None:
    legacy_dir = tmp_path / "legacy"
    native_dir = tmp_path / "native"
    _generate(TPCDS, 1.0, legacy_dir, native=False)
    _generate(TPCDS, 1.0, native_dir, native=True)

    legacy_manifest = _manifest(legacy_dir)
    native_manifest = _manifest(native_dir)
    assert "generator_backend" not in legacy_manifest
    assert native_manifest["generator_backend"] == NATIVE_BACKEND_NAME
    assert native_manifest["generator_backend_version"] == benchbox_datagen.__version__

    native_tables = set(native_manifest["tables"])
    assert len(native_tables) == 24
    assert native_tables <= set(legacy_manifest["tables"])

    legacy_files = _manifest_files(legacy_manifest)
    native_files = _manifest_files(native_manifest)
    for table in sorted(native_tables):
        legacy_rows = sum(entry["row_count"] for entry in legacy_files[table])
        native_rows = sum(entry["row_count"] for entry in native_files[table])
        assert native_rows == legacy_rows, table
        legacy_hash = _sha256(legacy_dir / legacy_files[table][0]["path"])
        native_hash = _sha256(native_dir / native_files[table][0]["path"])
        assert native_hash == legacy_hash, table
