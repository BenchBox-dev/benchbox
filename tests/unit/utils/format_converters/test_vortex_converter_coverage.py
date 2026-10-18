from __future__ import annotations

import builtins
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchbox.utils.format_converters.base import ConversionError, ConversionOptions
from benchbox.utils.format_converters.vortex_converter import VortexConverter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


def test_get_vortex_module_raises_helpful_error(monkeypatch):
    converter = VortexConverter()

    real_import = builtins.__import__

    def _raise(name, *args, **kwargs):
        if name == "vortex":
            raise ImportError("missing vortex")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _raise)

    with pytest.raises(ConversionError, match="requires the 'vortex' package"):
        converter._get_vortex_module()


def test_convert_success_path_writes_output_and_metadata(monkeypatch, tmp_path):
    converter = VortexConverter()

    source_file = tmp_path / "lineitem.tbl"
    source_file.write_text("1|a\n2|b\n")
    schema = {"columns": [{"name": "id", "type": "INTEGER"}]}

    class _CombinedTable:
        num_rows = 2

    def _write(_array, path):
        assert isinstance(path, str)
        Path(path).write_bytes(b"vortex-data")

    fake_vortex = SimpleNamespace(array=lambda table: table, io=SimpleNamespace(write=_write))

    monkeypatch.setattr(converter, "_write_with_duckdb_vortex", lambda *_args, **_kwargs: (False, "no extension"))
    monkeypatch.setattr(converter, "_get_vortex_module", lambda: fake_vortex)
    monkeypatch.setattr(converter, "validate_source_files", lambda _files: None)
    monkeypatch.setattr(converter, "validate_schema", lambda _schema: True)
    monkeypatch.setattr(converter, "_build_arrow_schema", lambda _schema: None)
    monkeypatch.setattr(converter, "read_tbl_files", lambda *_args, **_kwargs: _CombinedTable())
    monkeypatch.setattr(converter, "calculate_file_size", lambda _files: 100)
    monkeypatch.setattr(converter, "_detect_source_format", lambda _files: "tbl")
    monkeypatch.setattr(converter, "get_current_timestamp", lambda: "2026-02-08T20:45:00Z")

    result = converter.convert(
        source_files=[source_file],
        table_name="lineitem",
        schema=schema,
        options=ConversionOptions(validate_row_count=False),
    )

    assert result.row_count == 2
    assert result.metadata["compression"] == "snappy"
    assert result.output_files[0].name == "lineitem.vortex"
    assert result.output_files[0].exists()
    assert result.conversion_options["vortex_writer"] == "python-bindings"


def test_convert_cleans_partial_file_on_write_error(monkeypatch, tmp_path):
    converter = VortexConverter()

    source_file = tmp_path / "orders.tbl"
    source_file.write_text("1|x\n")
    schema = {"columns": [{"name": "id", "type": "INTEGER"}]}

    class _CombinedTable:
        num_rows = 1

    def _write(_array, path):
        assert isinstance(path, str)
        Path(path).write_bytes(b"partial")
        raise RuntimeError("write failed")

    fake_vortex = SimpleNamespace(array=lambda table: table, io=SimpleNamespace(write=_write))

    monkeypatch.setattr(converter, "_write_with_duckdb_vortex", lambda *_args, **_kwargs: (False, "no extension"))
    monkeypatch.setattr(converter, "_get_vortex_module", lambda: fake_vortex)
    monkeypatch.setattr(converter, "validate_source_files", lambda _files: None)
    monkeypatch.setattr(converter, "validate_schema", lambda _schema: True)
    monkeypatch.setattr(converter, "_build_arrow_schema", lambda _schema: None)
    monkeypatch.setattr(converter, "read_tbl_files", lambda *_args, **_kwargs: _CombinedTable())

    with pytest.raises(ConversionError, match="Failed to write Vortex file"):
        converter.convert([source_file], "orders", schema, options=ConversionOptions(validate_row_count=False))

    assert not (tmp_path / "orders.vortex").exists()


def test_convert_supports_legacy_vortex_encoding_api(monkeypatch, tmp_path):
    converter = VortexConverter()

    source_file = tmp_path / "nation.tbl"
    source_file.write_text("1|n\n")
    schema = {"columns": [{"name": "id", "type": "INTEGER"}]}

    class _CombinedTable:
        num_rows = 1

    def _write(_array, path):
        assert isinstance(path, str)
        Path(path).write_bytes(b"ok")

    fake_vortex = SimpleNamespace(
        encoding=SimpleNamespace(array=lambda table: table),
        io=SimpleNamespace(write=_write),
    )

    monkeypatch.setattr(converter, "_write_with_duckdb_vortex", lambda *_args, **_kwargs: (False, "no extension"))
    monkeypatch.setattr(converter, "_get_vortex_module", lambda: fake_vortex)
    monkeypatch.setattr(converter, "validate_source_files", lambda _files: None)
    monkeypatch.setattr(converter, "validate_schema", lambda _schema: True)
    monkeypatch.setattr(converter, "_build_arrow_schema", lambda _schema: None)
    monkeypatch.setattr(converter, "read_tbl_files", lambda *_args, **_kwargs: _CombinedTable())
    monkeypatch.setattr(converter, "calculate_file_size", lambda _files: 10)
    monkeypatch.setattr(converter, "_detect_source_format", lambda _files: "tbl")
    monkeypatch.setattr(converter, "get_current_timestamp", lambda: "2026-03-04T15:30:00Z")

    result = converter.convert(
        source_files=[source_file],
        table_name="nation",
        schema=schema,
        options=ConversionOptions(validate_row_count=False),
    )

    assert result.row_count == 1
    assert result.output_files[0].exists()


def test_convert_raises_helpful_error_for_incompatible_vortex_module(monkeypatch, tmp_path):
    converter = VortexConverter()

    source_file = tmp_path / "region.tbl"
    source_file.write_text("1|r\n")
    schema = {"columns": [{"name": "id", "type": "INTEGER"}]}

    class _CombinedTable:
        num_rows = 1

    monkeypatch.setattr(converter, "_write_with_duckdb_vortex", lambda *_args, **_kwargs: (False, "no extension"))
    monkeypatch.setattr(converter, "_get_vortex_module", lambda: SimpleNamespace())
    monkeypatch.setattr(converter, "validate_source_files", lambda _files: None)
    monkeypatch.setattr(converter, "validate_schema", lambda _schema: True)
    monkeypatch.setattr(converter, "_build_arrow_schema", lambda _schema: None)
    monkeypatch.setattr(converter, "read_tbl_files", lambda *_args, **_kwargs: _CombinedTable())
    monkeypatch.setattr(
        "benchbox.utils.format_converters.vortex_converter.importlib_metadata.packages_distributions",
        lambda: {"vortex": ["vortex"]},
    )

    with pytest.raises(ConversionError, match="incompatible with BenchBox Vortex conversion"):
        converter.convert([source_file], "region", schema, options=ConversionOptions(validate_row_count=False))


def test_convert_prefers_duckdb_writer_when_available(monkeypatch, tmp_path):
    converter = VortexConverter()

    source_file = tmp_path / "customer.tbl"
    source_file.write_text("1|x\n")
    schema = {"columns": [{"name": "id", "type": "INTEGER"}]}

    class _CombinedTable:
        num_rows = 1

    output_path = tmp_path / "customer.vortex"

    def _duckdb_write(_table, path):
        path.write_bytes(b"duckdb-vortex")
        return True, None

    monkeypatch.setattr(converter, "_write_with_duckdb_vortex", _duckdb_write)
    monkeypatch.setattr(
        converter,
        "_get_vortex_module",
        lambda: (_ for _ in ()).throw(AssertionError("python vortex fallback should not be used")),
    )
    monkeypatch.setattr(converter, "validate_source_files", lambda _files: None)
    monkeypatch.setattr(converter, "validate_schema", lambda _schema: True)
    monkeypatch.setattr(converter, "_build_arrow_schema", lambda _schema: None)
    monkeypatch.setattr(converter, "read_tbl_files", lambda *_args, **_kwargs: _CombinedTable())
    monkeypatch.setattr(converter, "calculate_file_size", lambda _files: 10)
    monkeypatch.setattr(converter, "_detect_source_format", lambda _files: "tbl")
    monkeypatch.setattr(converter, "get_current_timestamp", lambda: "2026-03-04T15:30:00Z")

    result = converter.convert(
        source_files=[source_file],
        table_name="customer",
        schema=schema,
        options=ConversionOptions(validate_row_count=False),
    )

    assert result.row_count == 1
    assert output_path.exists()
    assert result.conversion_options["vortex_writer"] == "duckdb-extension"


def test_require_duckdb_writer_raises_when_extension_unavailable(monkeypatch, tmp_path):

    converter = VortexConverter()

    source_file = tmp_path / "part.tbl"
    source_file.write_text("1|x\n")
    schema = {"columns": [{"name": "id", "type": "INTEGER"}]}

    class _CombinedTable:
        num_rows = 1

    monkeypatch.setattr(
        converter, "_write_with_duckdb_vortex", lambda *_args, **_kwargs: (False, "extension not found")
    )
    monkeypatch.setattr(converter, "validate_source_files", lambda _files: None)
    monkeypatch.setattr(converter, "validate_schema", lambda _schema: True)
    monkeypatch.setattr(converter, "_build_arrow_schema", lambda _schema: None)
    monkeypatch.setattr(converter, "read_tbl_files", lambda *_args, **_kwargs: _CombinedTable())

    opts = ConversionOptions(validate_row_count=False, metadata={"require_duckdb_writer": True})
    with pytest.raises(ConversionError, match="DuckDB vortex extension is required"):
        converter.convert([source_file], "part", schema, options=opts)


def test_duckdb_write_failure_logs_warning(monkeypatch, tmp_path, caplog):

    converter = VortexConverter()

    import logging

    with caplog.at_level(logging.WARNING, logger="benchbox.utils.format_converters.vortex_converter"):
        ok, err = converter._write_with_duckdb_vortex(None, tmp_path / "test.vortex")

    assert isinstance(ok, bool)
    if not ok:
        assert err is not None
