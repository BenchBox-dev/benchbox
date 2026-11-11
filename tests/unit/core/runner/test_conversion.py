from __future__ import annotations

from pathlib import Path

import pytest

from benchbox.core.manifest import ConvertedFileEntry, ManifestV2, TableFormats
from benchbox.core.runner import conversion as conv_module
from benchbox.core.runner.conversion import FormatConversionOrchestrator
from benchbox.utils.format_converters import ConversionOptions, ConversionResult

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _FakeConverter:
    def __init__(self, output_file: Path):
        self._output_file = output_file

    def convert(self, source_files, table_name, schema, options):
        return ConversionResult(
            output_files=[self._output_file],
            row_count=3,
            source_size_bytes=300,
            output_size_bytes=100,
            source_format="tbl",
            converted_at="2026-02-09T00:00:00Z",
            conversion_options={"compression": options.compression if options else "snappy"},
            metadata={"row_groups": 1},
        )


def _build_manifest_with_tbl() -> ManifestV2:
    return ManifestV2(
        benchmark="tpch",
        scale_factor=0.01,
        tables={
            "customer": TableFormats(
                formats={
                    "tbl": [
                        ConvertedFileEntry(path="customer.tbl", size_bytes=10, row_count=1),
                    ]
                }
            )
        },
        format_preference=["tbl"],
    )


def test_get_converter_and_unknown_format_error():
    orchestrator = FormatConversionOrchestrator()

    assert orchestrator._get_converter("parquet") is not None
    with pytest.raises(ValueError, match="Unknown format"):
        orchestrator._get_converter("bogus")


def test_get_source_files_prefers_tbl_and_fallback(tmp_path):
    orchestrator = FormatConversionOrchestrator()
    manifest = _build_manifest_with_tbl()

    files = orchestrator._get_source_files(manifest, "customer", tmp_path)
    assert files == [tmp_path / "customer.tbl"]

    manifest.tables["customer"] = TableFormats(
        formats={"parquet": [ConvertedFileEntry(path="customer.parquet", size_bytes=10, row_count=1)]}
    )
    files = orchestrator._get_source_files(manifest, "customer", tmp_path)
    assert files == [tmp_path / "customer.parquet"]


def test_update_manifest_with_results_adds_target_format_and_preference(tmp_path):
    orchestrator = FormatConversionOrchestrator()
    manifest = _build_manifest_with_tbl()
    output_file = tmp_path / "customer.parquet"
    output_file.touch()

    results = {
        "customer": ConversionResult(
            output_files=[output_file],
            row_count=1,
            source_size_bytes=10,
            output_size_bytes=5,
            source_format="tbl",
            converted_at="2026-02-09T00:00:00Z",
            conversion_options={"compression": "snappy"},
            metadata={"row_groups": 1},
        )
    }

    orchestrator._update_manifest_with_results(manifest, results, target_format="parquet", output_dir=tmp_path)
    assert "parquet" in manifest.tables["customer"].formats
    assert manifest.format_preference[0] == "parquet"


def test_convert_benchmark_tables_happy_path(monkeypatch, tmp_path):
    manifest = _build_manifest_with_tbl()
    source_file = tmp_path / "customer.tbl"
    source_file.write_text("1|a|\n")
    output_file = tmp_path / "customer.parquet"
    output_file.write_text("dummy")

    orchestrator = FormatConversionOrchestrator()
    orchestrator._converters = {"parquet": _FakeConverter(output_file)}

    monkeypatch.setattr(conv_module, "load_manifest", lambda _: manifest)
    captured = {"written": False}
    monkeypatch.setattr(conv_module, "write_manifest", lambda _manifest, _path: captured.__setitem__("written", True))

    results = orchestrator.convert_benchmark_tables(
        manifest_path=tmp_path / "_datagen_manifest.json",
        output_dir=tmp_path,
        target_format="parquet",
        schemas={"customer": {"columns": [{"name": "c_custkey", "type": "INTEGER"}]}},
        options=ConversionOptions(compression="snappy"),
    )

    assert "customer" in results
    assert captured["written"] is True


def test_format_preference_moves_existing_format_to_position_zero(tmp_path):
    orchestrator = FormatConversionOrchestrator()
    manifest = _build_manifest_with_tbl()

    manifest.format_preference = ["vortex", "delta", "tbl"]

    output_file = tmp_path / "customer.delta"
    output_file.touch()

    results = {
        "customer": ConversionResult(
            output_files=[output_file],
            row_count=1,
            source_size_bytes=10,
            output_size_bytes=5,
            source_format="tbl",
            converted_at="2026-03-04T00:00:00Z",
            conversion_options={"compression": "snappy"},
            metadata={},
        )
    }

    orchestrator._update_manifest_with_results(manifest, results, target_format="delta", output_dir=tmp_path)

    assert manifest.format_preference[0] == "delta"
    assert manifest.format_preference.count("delta") == 1


def test_format_preference_inserts_new_format_at_position_zero(tmp_path):
    orchestrator = FormatConversionOrchestrator()
    manifest = _build_manifest_with_tbl()

    output_file = tmp_path / "customer.iceberg"
    output_file.touch()

    results = {
        "customer": ConversionResult(
            output_files=[output_file],
            row_count=1,
            source_size_bytes=10,
            output_size_bytes=5,
            source_format="tbl",
            converted_at="2026-03-04T00:00:00Z",
            conversion_options={"compression": "snappy"},
            metadata={},
        )
    }

    orchestrator._update_manifest_with_results(manifest, results, target_format="iceberg", output_dir=tmp_path)

    assert manifest.format_preference[0] == "iceberg"


