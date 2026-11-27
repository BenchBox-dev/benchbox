from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from benchbox.core.runner.conversion import FormatConversionOrchestrator
from benchbox.core.tpcds.generator.manager import TPCDSDataGenerator
from benchbox.core.tpch.generator import TPCHDataGenerator
from benchbox.utils.datagen_backend import DatagenBackendError
from benchbox.utils.format_converters import ConversionOptions
from benchbox.utils.native_datagen import (
    bind_datagen_backend,
    load_benchbox_datagen,
    write_native_tables,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

TPCH_ROWS = {"nation": 25, "region": 5}
TPCDS_ROWS = {"item": 18_000, "store": 12}


def _fake_library(
    monkeypatch: pytest.MonkeyPatch, rows: dict[str, int], calls: list[dict[str, object]] | None = None
) -> ModuleType:
    module = ModuleType("benchbox_datagen")
    module.__version__ = "0.1.0"

    def write_tables(benchmark, scale_factor, output_dir, format="parquet", compression=None, threads=None, parts=1):
        if calls is not None:
            calls.append({"benchmark": benchmark, "format": format, "compression": compression, "threads": threads})
        stats = []
        for table, count in rows.items():
            path = Path(output_dir) / f"{table}.{format}"
            if format == "parquet":
                schema = pa.schema(
                    [pa.field("key", pa.int64())], metadata={b"benchbox.parquet_writer": b"benchbox-datagen-0"}
                )
                pq.write_table(pa.table({"key": list(range(count))}, schema=schema), path)
            else:
                path.write_text("".join(f"{index}|row\n" for index in range(count)), encoding="utf-8")
            stats.append(SimpleNamespace(table=table, path=str(path), rows=count, bytes=path.stat().st_size))
        return stats

    module.write_tables = write_tables
    monkeypatch.setitem(sys.modules, "benchbox_datagen", module)
    return module


def test_missing_library_raises_actionable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "benchbox_datagen", None)
    with pytest.raises(DatagenBackendError, match="native-datagen.*uv sync --extra native-datagen.*legacy"):
        load_benchbox_datagen()
    with pytest.raises(DatagenBackendError, match="native-datagen"):
        write_native_tables("tpch", 1.0, Path("unused"))


def test_unsupported_requests_raise_before_the_library_is_touched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, object]] = []
    _fake_library(monkeypatch, TPCDS_ROWS, calls)
    with pytest.raises(DatagenBackendError, match="scale_factor >= 1"):
        write_native_tables("tpcds", 0.1, tmp_path)
    with pytest.raises(DatagenBackendError, match="integer scale factor"):
        write_native_tables("tpcds", 1.5, tmp_path)
    with pytest.raises(DatagenBackendError, match="does not support --datagen-backend native"):
        write_native_tables("ssb", 1.0, tmp_path)
    assert calls == []


def test_tpcds_delimited_files_use_the_dat_extension(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_library(monkeypatch, TPCDS_ROWS)
    generation = write_native_tables("tpcds", 1.0, tmp_path)
    assert generation.version == "0.1.0"
    assert {table: file.path.name for table, file in generation.files.items()} == {
        "item": "item.dat",
        "store": "store.dat",
    }
    assert generation.files["item"].rows == 18_000
    assert sorted(path.name for path in tmp_path.iterdir()) == ["item.dat", "store.dat"]


def test_tpch_generator_records_backend_and_version_in_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, object]] = []
    _fake_library(monkeypatch, TPCH_ROWS, calls)
    generator = TPCHDataGenerator(scale_factor=0.01, output_dir=tmp_path, force_regenerate=True)
    bind_datagen_backend(
        SimpleNamespace(__dict__={"data_generator": generator}, data_generator=generator), "native", "0.1.0"
    )

    tables = generator.generate()

    assert calls == [{"benchmark": "tpch", "format": "tbl", "compression": None, "threads": None}]
    assert {table: Path(path).name for table, path in tables.items()} == {
        "nation": "nation.tbl",
        "region": "region.tbl",
    }
    manifest = json.loads((tmp_path / "_datagen_manifest.json").read_text(encoding="utf-8"))
    assert manifest["generator_backend"] == "benchbox-datagen"
    assert manifest["generator_backend_version"] == "0.1.0"
    assert "generator_binary_sha256" not in manifest
    assert manifest["row_counts_source"] == "measured"
    assert manifest["tables"]["nation"]["formats"]["tbl"][0]["row_count"] == 25


def test_tpch_generator_without_binding_never_uses_the_library(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []
    _fake_library(monkeypatch, TPCH_ROWS, calls)
    generator = TPCHDataGenerator(scale_factor=0.01, output_dir=tmp_path)
    assert generator.uses_native_datagen is False
    bind_datagen_backend(
        SimpleNamespace(__dict__={"data_generator": generator}, data_generator=generator), "legacy", None
    )
    assert generator.uses_native_datagen is False
    assert calls == []


def test_tpch_native_rejects_compression_and_data_organization(tmp_path: Path) -> None:
    compressed = TPCHDataGenerator(scale_factor=0.01, output_dir=tmp_path, compress_data=True, compression_type="gzip")
    compressed.datagen_backend = "native"
    with pytest.raises(DatagenBackendError, match="uncompressed delimited files"):
        compressed.generate()

    organized = TPCHDataGenerator(scale_factor=0.01, output_dir=tmp_path / "organized")
    organized.datagen_backend = "native"
    organized._data_organization_config = object()
    with pytest.raises(DatagenBackendError, match="data organization"):
        organized.generate()


def test_tpcds_generator_records_backend_and_row_counts_in_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, object]] = []
    _fake_library(monkeypatch, TPCDS_ROWS, calls)
    generator = TPCDSDataGenerator(scale_factor=1.0, output_dir=tmp_path, force_regenerate=True)
    generator.datagen_backend = "native"

    tables = generator.generate()

    assert calls == [{"benchmark": "tpcds", "format": "tbl", "compression": None, "threads": None}]
    assert {table: [path.name for path in paths] for table, paths in tables.items()} == {
        "item": ["item.dat"],
        "store": ["store.dat"],
    }
    manifest = json.loads((tmp_path / "_datagen_manifest.json").read_text(encoding="utf-8"))
    assert manifest["generator_backend"] == "benchbox-datagen"
    assert manifest["generator_backend_version"] == "0.1.0"
    assert "generator_binary_sha256" not in manifest
    assert manifest["tables"]["item"]["formats"]["tbl"][0]["row_count"] == 18_000


def test_tpcds_native_subscale_is_an_error_not_a_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []
    _fake_library(monkeypatch, TPCDS_ROWS, calls)
    generator = TPCDSDataGenerator(scale_factor=0.1, output_dir=tmp_path, force_regenerate=True)
    generator.datagen_backend = "native"
    with pytest.raises(DatagenBackendError, match="scale_factor >= 1"):
        generator.generate()
    assert calls == []


def test_bind_datagen_backend_reaches_wrapper_implementation_and_generator() -> None:
    generator = SimpleNamespace()
    implementation = SimpleNamespace(data_generator=generator)
    benchmark = SimpleNamespace(_impl=implementation)
    benchmark.__dict__["_impl"] = implementation
    bind_datagen_backend(benchmark, "native", "0.1.0")
    for target in (benchmark, implementation, generator):
        assert target.datagen_backend == "native"
        assert target.datagen_backend_version == "0.1.0"


def test_native_parquet_conversion_stamps_library_writer_and_layout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, object]] = []
    _fake_library(monkeypatch, TPCH_ROWS, calls)
    generator = TPCHDataGenerator(scale_factor=0.01, output_dir=tmp_path, force_regenerate=True)
    generator.datagen_backend = "native"
    generator.generate()

    orchestrator = FormatConversionOrchestrator()
    manifest_path = tmp_path / "_datagen_manifest.json"
    results = orchestrator.convert_benchmark_tables(
        manifest_path, tmp_path, "parquet", schemas={}, options=ConversionOptions(compression="zstd")
    )

    assert calls[-1] == {"benchmark": "tpch", "format": "parquet", "compression": "zstd", "threads": None}
    assert {table: result.row_count for table, result in results.items()} == TPCH_ROWS
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["generator_backend"] == "benchbox-datagen"
    assert manifest["parquet_writer"] == "benchbox-datagen-0"
    assert manifest["parquet_row_group_size"] == 122_880
    assert "parquet" in manifest["formats"]
    assert manifest["tables"]["nation"]["formats"]["parquet"][0]["row_count"] == 25
    assert (tmp_path / "nation.parquet").is_file()


def test_native_parquet_conversion_rejects_unsupported_options(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_library(monkeypatch, TPCH_ROWS)
    generator = TPCHDataGenerator(scale_factor=0.01, output_dir=tmp_path, force_regenerate=True)
    generator.datagen_backend = "native"
    generator.generate()
    orchestrator = FormatConversionOrchestrator()
    manifest_path = tmp_path / "_datagen_manifest.json"
    with pytest.raises(DatagenBackendError, match="partition columns"):
        orchestrator.convert_benchmark_tables(
            manifest_path, tmp_path, "parquet", schemas={}, options=ConversionOptions(partition_cols=["n_regionkey"])
        )
    with pytest.raises(DatagenBackendError, match="row-group size"):
        orchestrator.convert_benchmark_tables(
            manifest_path, tmp_path, "parquet", schemas={}, options=ConversionOptions(row_group_size=1000)
        )


def test_pinned_library_version_is_registered_as_verified_and_sourced_from_a_commit() -> None:
    import tomllib

    from benchbox.utils.datagen_version import VERIFIED_BYTE_IDENTICAL_BACKENDS

    root = Path(__file__).resolve().parents[3]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    requirement = project["project"]["optional-dependencies"]["native-datagen"]
    assert len(requirement) == 1 and requirement[0].startswith("benchbox-datagen==")
    version = requirement[0].removeprefix("benchbox-datagen==")
    assert {benchmark for benchmark, pinned in VERIFIED_BYTE_IDENTICAL_BACKENDS if pinned == version} == {
        "tpch",
        "tpcds",
    }
    source = project["tool"]["uv"]["sources"]["benchbox-datagen"]
    assert len(source["rev"]) == 40


def test_native_parquet_runs_need_native_written_data_while_delimited_data_is_shared() -> None:
    from benchbox.utils.datagen_version import current_datagen_stamp, manifest_datagen_is_current

    legacy = {"benchmark": "tpch", "scale_factor": 1.0, "formats": ["tbl"], **current_datagen_stamp("tpch")}
    legacy["generator_binary_sha256"] = "0" * 64
    native = {
        "benchmark": "tpch",
        "scale_factor": 1.0,
        "formats": ["tbl"],
        **current_datagen_stamp("tpch", backend="benchbox-datagen", backend_version="0.1.0"),
    }

    def current(manifest: dict, backend: str, table_format: str) -> bool:
        return manifest_datagen_is_current(
            manifest,
            benchmark="tpch",
            expected_backend=backend,
            expected_backend_version="0.1.0",
            expected_table_format=table_format,
        )

    assert current(native, "native", "tbl") is True
    assert current(native, "native", "parquet") is True
    assert current(native, "legacy", "tbl") is True
    assert current(legacy, "native", "parquet") is False
    assert current(native, "legacy", "parquet") is True

    native_parquet = {**native, "formats": ["tbl", "parquet"], "parquet_writer": "benchbox-datagen-0"}
    native_parquet["parquet_row_group_size"] = 122_880
    assert current(native_parquet, "native", "parquet") is True
    assert current(native_parquet, "legacy", "parquet") is False


def test_parquet_writer_identity_is_read_from_file_metadata_and_required(tmp_path: Path) -> None:
    from benchbox.utils.native_datagen import read_parquet_writer

    anonymous = tmp_path / "anonymous.parquet"
    pq.write_table(pa.table({"key": [1]}), anonymous)
    with pytest.raises(DatagenBackendError, match="does not record its Parquet writer"):
        read_parquet_writer(anonymous)

    named = tmp_path / "named.parquet"
    schema = pa.schema([pa.field("key", pa.int64())], metadata={b"benchbox.parquet_writer": b"benchbox-datagen-0"})
    pq.write_table(pa.table({"key": [1]}, schema=schema), named)
    assert read_parquet_writer(named) == "benchbox-datagen-0"


def test_tpcds_native_parquet_conversion_uses_the_library_writer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, object]] = []
    _fake_library(monkeypatch, TPCDS_ROWS, calls)
    generator = TPCDSDataGenerator(scale_factor=1.0, output_dir=tmp_path, force_regenerate=True)
    generator.datagen_backend = "native"
    generator.generate()
    manifest_path = tmp_path / "_datagen_manifest.json"
    FormatConversionOrchestrator().convert_benchmark_tables(
        manifest_path, tmp_path, "parquet", schemas={}, options=ConversionOptions(compression="snappy")
    )
    assert calls[-1]["format"] == "parquet"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["parquet_writer"] == "benchbox-datagen-0"
    assert manifest["generator_backend"] == "benchbox-datagen"
