from __future__ import annotations

import gzip
import io
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from benchbox.platforms.base.data_loading import (
    BenchmarkImplTablesSource,
    BenchmarkTablesSource,
    ClickHouseNativeHandler,
    ClickHouseServerLoadError,
    DataLoader,
    DataLoadingError,
    DataSource,
    DataSourceResolver,
    FileFormatRegistry,
    GzipHandler,
    ManifestFileSource,
    NoCompressionHandler,
    RowBatchProcessor,
    SchemaInspector,
    ZstdHandler,
    escape_sql_string_literal,
    validate_sql_identifier,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class TestDataLoaderClickHouseFailurePropagation:
    @staticmethod
    def _loader(tmp_path: Path, handler: object) -> DataLoader:
        data_file = tmp_path / "events.tbl"
        data_file.write_text("1|alpha\n", encoding="utf-8")
        loader = DataLoader.__new__(DataLoader)
        loader.handler_factory = lambda file_path, adapter, benchmark, table_name=None, data_source=None: handler
        loader.connection = MagicMock()
        loader.benchmark = MagicMock()
        loader.adapter = MagicMock()
        loader.adapter.logger = MagicMock()
        return loader

    def test_sharded_load_rethrows_typed_server_failure(self, tmp_path):
        failure = ClickHouseServerLoadError("events", [], 0, RuntimeError("timeout"))
        loader = self._loader(tmp_path, MagicMock(load_table_bulk=MagicMock(side_effect=failure)))
        data_file = tmp_path / "events.tbl"

        with pytest.raises(ClickHouseServerLoadError) as exc_info:
            loader._load_sharded_table("events", [data_file])

        assert exc_info.value is failure

    def test_single_file_load_rethrows_typed_server_failure(self, tmp_path):
        failure = ClickHouseServerLoadError("events", [], 0, RuntimeError("timeout"))
        loader = self._loader(tmp_path, MagicMock(load_table=MagicMock(side_effect=failure)))
        data_file = tmp_path / "events.tbl"

        with pytest.raises(ClickHouseServerLoadError) as exc_info:
            loader._load_single_file("events", data_file)

        assert exc_info.value is failure


class TestDeltaTableDirDispatch:
    @staticmethod
    def _loader(tmp_path: Path, seen: list) -> DataLoader:
        loader = DataLoader.__new__(DataLoader)
        handler = MagicMock()
        handler.load_table_bulk = MagicMock(return_value=3)

        def factory(file_path, adapter, benchmark, table_name=None, data_source=None):
            seen.append(Path(file_path))
            return handler

        loader.handler_factory = factory
        loader.connection = MagicMock()
        loader.benchmark = MagicMock()
        loader.adapter = MagicMock()
        loader.adapter.logger = MagicMock()
        return loader

    def test_delta_dir_reaches_handler_whole(self, tmp_path):
        table_dir = tmp_path / "orders"
        table_dir.mkdir()
        (table_dir / "_delta_log").mkdir()
        (table_dir / "part-00000.parquet").write_bytes(b"PAR1")
        seen: list = []
        loader = self._loader(tmp_path, seen)

        assert loader._load_sharded_table("orders", [table_dir]) == 3

        assert seen == [table_dir]

    def test_plain_dir_still_expands_to_shards(self, tmp_path):
        table_dir = tmp_path / "events"
        table_dir.mkdir()
        (table_dir / "part-0.tbl").write_text("1|alpha\n", encoding="utf-8")
        seen: list = []
        loader = self._loader(tmp_path, seen)

        assert loader._load_sharded_table("events", [table_dir]) == 3
        assert seen == [table_dir / "part-0.tbl"]


class _ServerConnection:
    def __init__(self) -> None:
        self.rows: list[tuple] = []
        self.query: str | None = None

    def execute(self, query: str, rows=None, **_kwargs):
        self.query = query
        if rows is not None:
            self.rows.extend(rows)


class _ServerAdapter:
    deployment_mode = "server"
    insert_block_size = 16


def test_clickhouse_delimited_loader_skips_empty_first_shard(tmp_path: Path) -> None:
    empty = tmp_path / "part-0.tbl"
    populated = tmp_path / "part-1.tbl"
    empty.write_text("", encoding="utf-8")
    populated.write_text("1|alpha\n", encoding="utf-8")
    connection = _ServerConnection()
    handler = ClickHouseNativeHandler("|", _ServerAdapter(), object())

    assert (
        handler._load_delimited_via_client_insert(
            "events", [empty, populated], connection, object(), logging.getLogger()
        )
        == 1
    )
    assert connection.rows == [("1", "alpha")]


class _KeyedColumnsBenchmark:
    def get_schema(self):
        return {
            "zones": {
                "columns": {
                    "location_id": {"type": "INTEGER"},
                    "name": {"type": "VARCHAR(10)"},
                    "latitude": {"type": "DOUBLE"},
                    "seen_at": {"type": "TIMESTAMP"},
                }
            }
        }


def test_clickhouse_delimited_loader_converts_keyed_column_schemas(tmp_path: Path) -> None:
    path = tmp_path / "zones.tbl"
    path.write_text("7|north|1.5|2024-01-01 00:00:10\n8|||\n", encoding="utf-8")
    connection = _ServerConnection()
    benchmark = _KeyedColumnsBenchmark()
    handler = ClickHouseNativeHandler("|", _ServerAdapter(), benchmark)

    assert handler._load_delimited_via_client_insert("zones", [path], connection, benchmark, logging.getLogger()) == 2
    assert connection.rows == [
        (7, "north", 1.5, datetime(2024, 1, 1, 0, 0, 10)),
        (8, "", None, None),
    ]


def test_clickhouse_parquet_loader_uses_logical_nested_names(tmp_path: Path) -> None:
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    path = tmp_path / "events.parquet"
    pq.write_table(pa.table({"embedding": [[1, 2]], "event_id": [1]}), path)
    connection = _ServerConnection()
    handler = ClickHouseNativeHandler("|", _ServerAdapter(), object())

    assert handler._load_parquet_via_client_insert("events", [path], connection) == 1
    assert "embedding,event_id" in (connection.query or "")
    assert connection.rows == [([1, 2], 1)]


class TestValidateSqlIdentifier:
    def test_simple_valid_name(self):
        assert validate_sql_identifier("customer") == "customer"

    def test_underscore_prefix(self):
        assert validate_sql_identifier("_temp") == "_temp"

    def test_mixed_case_with_digits(self):
        assert validate_sql_identifier("LineItem2") == "LineItem2"

    def test_all_underscores(self):
        assert validate_sql_identifier("___") == "___"

    def test_single_letter(self):
        assert validate_sql_identifier("x") == "x"

    def test_max_length_exactly(self):
        name = "a" * 128
        assert validate_sql_identifier(name) == name

    def test_empty_string_raises(self):
        with pytest.raises(DataLoadingError, match="Empty"):
            validate_sql_identifier("")

    def test_none_like_empty_raises(self):

        with pytest.raises(DataLoadingError, match="Empty"):
            validate_sql_identifier("")

    def test_exceeds_max_length(self):
        name = "a" * 129
        with pytest.raises(DataLoadingError, match="exceeds maximum length"):
            validate_sql_identifier(name)

    def test_starts_with_digit(self):
        with pytest.raises(DataLoadingError, match="must contain only"):
            validate_sql_identifier("1table")

    def test_contains_space(self):
        with pytest.raises(DataLoadingError, match="must contain only"):
            validate_sql_identifier("my table")

    def test_contains_semicolon(self):
        with pytest.raises(DataLoadingError, match="must contain only"):
            validate_sql_identifier("tbl;DROP")

    def test_sql_injection_attempt(self):
        with pytest.raises(DataLoadingError):
            validate_sql_identifier("x; DROP TABLE users --")

    def test_contains_dash(self):
        with pytest.raises(DataLoadingError, match="must contain only"):
            validate_sql_identifier("my-table")

    def test_contains_dot(self):
        with pytest.raises(DataLoadingError, match="must contain only"):
            validate_sql_identifier("schema.table")

    def test_single_quote_injection(self):
        with pytest.raises(DataLoadingError):
            validate_sql_identifier("table'; DROP TABLE--")

    def test_custom_context_in_error(self):
        with pytest.raises(DataLoadingError, match="table name"):
            validate_sql_identifier("bad name", context="table name")

    def test_unicode_characters_rejected(self):
        with pytest.raises(DataLoadingError, match="must contain only"):
            validate_sql_identifier("tbl_\u00e9")

    def test_tab_in_name_rejected(self):
        with pytest.raises(DataLoadingError, match="must contain only"):
            validate_sql_identifier("tbl\ttbl")

    def test_returns_validated_value(self):

        result = validate_sql_identifier("orders", "table name")
        assert result == "orders"


class TestEscapeSqlStringLiteral:
    def test_no_special_characters(self):
        assert escape_sql_string_literal("hello") == "hello"

    def test_empty_string(self):
        assert escape_sql_string_literal("") == ""

    def test_single_quote_doubled(self):
        assert escape_sql_string_literal("it's") == "it''s"

    def test_multiple_single_quotes(self):
        assert escape_sql_string_literal("a'b'c") == "a''b''c"

    def test_consecutive_quotes(self):
        assert escape_sql_string_literal("''") == "''''"

    def test_double_quotes_unchanged(self):
        assert escape_sql_string_literal('say "hi"') == 'say "hi"'

    def test_backslash_unchanged(self):
        assert escape_sql_string_literal("path\\to\\file") == "path\\to\\file"

    def test_file_path_with_quotes(self):
        assert escape_sql_string_literal("/tmp/it's a file.csv") == "/tmp/it''s a file.csv"

    def test_newlines_preserved(self):
        assert escape_sql_string_literal("line1\nline2") == "line1\nline2"


class TestDataSource:
    def test_creation(self):
        ds = DataSource(source_type="benchmark_tables", tables={"t1": [Path("/a")]})
        assert ds.source_type == "benchmark_tables"
        assert "t1" in ds.tables

    def test_empty_tables(self):
        ds = DataSource(source_type="manifest", tables={})
        assert ds.tables == {}

    def test_multiple_tables(self):
        ds = DataSource(
            source_type="benchmark_impl_tables",
            tables={
                "customer": [Path("/data/customer.tbl")],
                "orders": [Path("/data/orders.tbl")],
            },
        )
        assert len(ds.tables) == 2

    def test_equality(self):
        a = DataSource(source_type="x", tables={"t": 1})
        b = DataSource(source_type="x", tables={"t": 1})
        assert a == b

    def test_table_formats_defaults_to_empty_dict_when_none(self):

        ds = DataSource(source_type="benchmark_tables", tables={})
        assert ds.table_formats == {}
        assert ds.table_formats is not None

    def test_table_formats_preserved_when_provided(self):
        ds = DataSource(source_type="manifest_v2", tables={}, table_formats={"orders": "tbl"})
        assert ds.table_formats == {"orders": "tbl"}

    def test_table_formats_empty_dict_preserved(self):
        ds = DataSource(source_type="benchmark_tables", tables={}, table_formats={})
        assert ds.table_formats == {}


class TestFileFormatRegistryExtensions:
    def test_csv_extension(self):
        assert FileFormatRegistry.get_base_data_extension(Path("/d/file.csv")) == ".csv"

    def test_dat_extension(self):
        assert FileFormatRegistry.get_base_data_extension(Path("/d/file.dat")) == ".dat"

    def test_parquet_extension(self):
        assert FileFormatRegistry.get_base_data_extension(Path("/d/file.parquet")) == ".parquet"

    def test_vortex_extension(self):
        assert FileFormatRegistry.get_base_data_extension(Path("/d/file.vortex")) == ".vortex"

    def test_csv_gz(self):
        assert FileFormatRegistry.get_base_data_extension(Path("/d/file.csv.gz")) == ".csv"

    def test_dat_zst(self):
        assert FileFormatRegistry.get_base_data_extension(Path("/d/file.dat.zst")) == ".dat"

    def test_unknown_extension_returns_none(self):
        assert FileFormatRegistry.get_base_data_extension(Path("/d/file.jsonl")) is None

    def test_no_extension_returns_none(self):
        assert FileFormatRegistry.get_base_data_extension(Path("/d/somefile")) is None

    def test_only_compression_ext_returns_none(self):
        assert FileFormatRegistry.get_base_data_extension(Path("/d/file.zst")) is None

    def test_double_compression_stripped(self):

        assert FileFormatRegistry.get_base_data_extension(Path("/d/foo.csv.gz.zst")) == ".csv"

    def test_numeric_shard_suffix_with_compression(self):

        assert FileFormatRegistry.get_base_data_extension(Path("/d/customer.tbl.5.gz")) == ".tbl"

    def test_parquet_zst(self):
        assert FileFormatRegistry.get_base_data_extension(Path("/d/data.parquet.zst")) == ".parquet"


class TestFileFormatRegistryHandlers:
    def test_csv_handler_returns_comma_delimiter(self):
        handler = FileFormatRegistry.get_handler(Path("/d/data.csv"))
        assert handler is not None
        assert handler.get_delimiter() == ","

    def test_dat_handler_returns_pipe_delimiter(self):
        handler = FileFormatRegistry.get_handler(Path("/d/data.dat"))
        assert handler is not None
        assert handler.get_delimiter() == "|"

    def test_unknown_extension_returns_none(self):
        handler = FileFormatRegistry.get_handler(Path("/d/data.jsonl"))
        assert handler is None

    def test_no_extension_returns_none(self):
        handler = FileFormatRegistry.get_handler(Path("/d/somefile"))
        assert handler is None

    def test_csv_gz_handler(self):

        handler = FileFormatRegistry.get_handler(Path("/d/data.csv.gz"))
        assert handler is not None
        assert handler.get_delimiter() == ","

    def test_dat_zst_handler(self):
        handler = FileFormatRegistry.get_handler(Path("/d/data.dat.zst"))
        assert handler is not None
        assert handler.get_delimiter() == "|"

    def test_ducklake_directory(self, tmp_path):

        from benchbox.platforms.base.data_loading import DuckLakeFileHandler

        tbl_dir = tmp_path / "orders"
        tbl_dir.mkdir()
        (tbl_dir / "metadata.ducklake").touch()
        handler = FileFormatRegistry.get_handler(tbl_dir)
        assert isinstance(handler, DuckLakeFileHandler)


class TestFileFormatRegistryCompression:
    def test_zst_returns_zstd_handler(self):
        handler = FileFormatRegistry.get_compression_handler(Path("/d/file.zst"))
        assert isinstance(handler, ZstdHandler)

    def test_gz_returns_gzip_handler(self):
        handler = FileFormatRegistry.get_compression_handler(Path("/d/file.gz"))
        assert isinstance(handler, GzipHandler)

    def test_uncompressed_returns_no_compression(self):
        handler = FileFormatRegistry.get_compression_handler(Path("/d/file.csv"))
        assert isinstance(handler, NoCompressionHandler)

    def test_unknown_ext_returns_no_compression(self):
        handler = FileFormatRegistry.get_compression_handler(Path("/d/file.xyz"))
        assert isinstance(handler, NoCompressionHandler)


class TestNoCompressionHandler:
    def test_reads_plain_file(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_text("a,b,c\n1,2,3\n")
        handler = NoCompressionHandler()
        with handler.open(f) as fh:
            content = fh.read()
        assert "a,b,c" in content


class TestGzipHandler:
    def test_reads_gzip_file(self, tmp_path):
        gz_file = tmp_path / "data.csv.gz"
        with gzip.open(gz_file, "wt") as f:
            f.write("x,y\n10,20\n")
        handler = GzipHandler()
        with handler.open(gz_file) as fh:
            content = fh.read()
        assert "x,y" in content
        assert "10,20" in content


class TestSchemaInspector:
    def test_from_schema(self):
        benchmark = MagicMock()
        benchmark.get_schema.return_value = {"t1": {"columns": ["a", "b", "c"]}}
        fh = io.StringIO("x|y|z\n")
        count = SchemaInspector.get_column_count(benchmark, "t1", fh, "|")
        assert count == 3

    def test_fallback_to_first_line(self):
        benchmark = MagicMock()
        benchmark.get_schema.return_value = {}
        fh = io.StringIO("a|b|c|d\n1|2|3|4\n")
        count = SchemaInspector.get_column_count(benchmark, "t1", fh, "|")
        assert count == 4

        assert fh.tell() == 0

    def test_no_schema_method(self):

        benchmark = MagicMock(spec=[])
        fh = io.StringIO("a,b\n1,2\n")
        count = SchemaInspector.get_column_count(benchmark, "t1", fh, ",")
        assert count == 2

    def test_empty_file_returns_none(self):
        benchmark = MagicMock()
        benchmark.get_schema.return_value = {}
        fh = io.StringIO("")
        count = SchemaInspector.get_column_count(benchmark, "t1", fh, "|")
        assert count is None


class TestRowBatchProcessor:
    def test_single_batch(self):
        proc = RowBatchProcessor(batch_size=100)
        fh = io.StringIO("a|b\n1|2\n3|4\n")
        batches = list(proc.process_file(fh, "|", 2))
        assert len(batches) == 1
        data, count = batches[0]
        assert count == 3
        assert len(data) == 3

    def test_multiple_batches(self):
        proc = RowBatchProcessor(batch_size=2)
        fh = io.StringIO("a|b\n1|2\n3|4\n5|6\n")
        batches = list(proc.process_file(fh, "|", 2))
        assert len(batches) == 2

        assert len(batches[0][0]) == 2

        assert len(batches[1][0]) == 2

    def test_empty_lines_skipped(self):
        proc = RowBatchProcessor(batch_size=100)
        fh = io.StringIO("a|b\n\n1|2\n\n")
        batches = list(proc.process_file(fh, "|", 2))
        assert len(batches) == 1
        data, count = batches[0]
        assert count == 2

    def test_pads_short_rows(self):
        proc = RowBatchProcessor(batch_size=100)
        fh = io.StringIO("a\n")
        batches = list(proc.process_file(fh, "|", 3))
        data, _ = batches[0]
        assert len(data[0]) == 3

    def test_truncates_long_rows(self):
        proc = RowBatchProcessor(batch_size=100)
        fh = io.StringIO("a|b|c|d|e\n")
        batches = list(proc.process_file(fh, "|", 2))
        data, _ = batches[0]
        assert len(data[0]) == 2

    def test_empty_file(self):
        proc = RowBatchProcessor(batch_size=100)
        fh = io.StringIO("")
        batches = list(proc.process_file(fh, "|", 2))
        assert batches == []


class TestBenchmarkTablesSource:
    def test_can_provide_with_tables(self):
        benchmark = MagicMock()
        benchmark.tables = {"t1": Path("/a")}
        src = BenchmarkTablesSource()
        assert src.can_provide(benchmark, Path("/data")) is True

    def test_cannot_provide_without_tables_attr(self):
        benchmark = MagicMock(spec=[])
        src = BenchmarkTablesSource()
        assert src.can_provide(benchmark, Path("/data")) is False

    def test_cannot_provide_with_empty_tables(self):
        benchmark = MagicMock()
        benchmark.tables = {}
        src = BenchmarkTablesSource()
        assert src.can_provide(benchmark, Path("/data")) is False

    def test_cannot_provide_with_none_tables(self):
        benchmark = MagicMock()
        benchmark.tables = None
        src = BenchmarkTablesSource()
        assert src.can_provide(benchmark, Path("/data")) is False

    def test_get_data_source_normalizes_single_path(self):
        benchmark = MagicMock()
        benchmark.tables = {"customer": Path("/data/customer.tbl")}
        src = BenchmarkTablesSource()
        ds = src.get_data_source(benchmark, Path("/data"))
        assert ds is not None
        assert ds.source_type == "benchmark_tables"

        assert ds.tables["customer"] == [Path("/data/customer.tbl")]

    def test_get_data_source_preserves_list(self):
        benchmark = MagicMock()
        benchmark.tables = {"customer": [Path("/a"), Path("/b")]}
        src = BenchmarkTablesSource()
        ds = src.get_data_source(benchmark, Path("/data"))
        assert ds.tables["customer"] == [Path("/a"), Path("/b")]

    def test_get_data_source_returns_none_when_cannot_provide(self):
        benchmark = MagicMock(spec=[])
        src = BenchmarkTablesSource()
        ds = src.get_data_source(benchmark, Path("/data"))
        assert ds is None

    def test_get_data_source_does_not_populate_table_formats(self, tmp_path):

        benchmark = MagicMock()
        benchmark.tables = {"lineitem": tmp_path / "lineitem.csv.zst"}

        manifest_data = {
            "version": 2,
            "benchmark": "clickbench",
            "scale_factor": 1,
            "format_preference": ["tbl"],
            "tables": {
                "lineitem": {
                    "formats": {
                        "tbl": [{"path": "lineitem.csv.zst", "size_bytes": 100, "row_count": 10}],
                    }
                }
            },
        }
        (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest_data))

        src = BenchmarkTablesSource()
        ds = src.get_data_source(benchmark, tmp_path)

        assert ds is not None

        assert ds.table_formats == {}


class TestBenchmarkImplTablesSource:
    def test_can_provide_with_impl_tables(self):
        impl = MagicMock()
        impl.tables = {"t1": Path("/a")}
        benchmark = MagicMock()
        benchmark._impl = impl
        src = BenchmarkImplTablesSource()
        assert src.can_provide(benchmark, Path("/data")) is True

    def test_cannot_provide_without_impl(self):
        benchmark = MagicMock(spec=[])
        src = BenchmarkImplTablesSource()
        assert src.can_provide(benchmark, Path("/data")) is False

    def test_cannot_provide_with_no_impl_tables(self):
        impl = MagicMock(spec=[])
        benchmark = MagicMock()
        benchmark._impl = impl
        src = BenchmarkImplTablesSource()
        assert src.can_provide(benchmark, Path("/data")) is False

    def test_get_data_source_normalizes(self):
        impl = MagicMock()
        impl.tables = {"orders": Path("/data/orders.tbl")}
        benchmark = MagicMock()
        benchmark._impl = impl
        src = BenchmarkImplTablesSource()
        ds = src.get_data_source(benchmark, Path("/data"))
        assert ds is not None
        assert ds.source_type == "benchmark_impl_tables"
        assert ds.tables["orders"] == [Path("/data/orders.tbl")]


class TestManifestFileSource:
    def test_can_provide_when_manifest_exists(self, tmp_path):
        manifest = tmp_path / "_datagen_manifest.json"
        manifest.write_text("{}")
        src = ManifestFileSource()
        assert src.can_provide(MagicMock(), tmp_path) is True

    def test_cannot_provide_when_manifest_missing(self, tmp_path):
        src = ManifestFileSource()
        assert src.can_provide(MagicMock(), tmp_path) is False

    def test_v1_manifest_loads_tables(self, tmp_path):
        manifest_data = {
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "tables": {
                "customer": [{"path": "customer.tbl", "size_bytes": 100, "row_count": 10}],
                "orders": [
                    {"path": "orders.tbl.1", "size_bytes": 200, "row_count": 20},
                    {"path": "orders.tbl.2", "size_bytes": 200, "row_count": 20},
                ],
            },
        }
        (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest_data))

        src = ManifestFileSource()
        ds = src.get_data_source(MagicMock(spec=[]), tmp_path)
        assert ds is not None
        assert ds.source_type == "manifest"
        assert len(ds.tables["customer"]) == 1
        assert len(ds.tables["orders"]) == 2
        assert ds.tables["customer"][0] == tmp_path / "customer.tbl"

    def test_v1_manifest_empty_tables(self, tmp_path):
        manifest_data = {"benchmark": "tpch", "scale_factor": 0.01, "tables": {}}
        (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest_data))
        src = ManifestFileSource()
        ds = src.get_data_source(MagicMock(spec=[]), tmp_path)
        assert ds is None

    def test_v1_manifest_skips_entries_without_path(self, tmp_path):

        manifest_data = {
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "tables": {
                "t1": [
                    {"path": "a.tbl", "size_bytes": 50, "row_count": 5},
                    {"path": "b.tbl", "size_bytes": 50, "row_count": 5},
                ],
            },
        }
        (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest_data))
        src = ManifestFileSource()
        ds = src.get_data_source(MagicMock(spec=[]), tmp_path)
        assert ds is not None
        assert len(ds.tables["t1"]) == 2

    def test_manifest_bad_json_returns_none(self, tmp_path):
        (tmp_path / "_datagen_manifest.json").write_text("NOT JSON")
        src = ManifestFileSource()
        ds = src.get_data_source(MagicMock(spec=[]), tmp_path)
        assert ds is None


class TestManifestFileSourceV2:
    def test_manifest_v2_native_mode_prefers_platform_defaults(self, tmp_path):

        manifest_data = {
            "version": 2,
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "format_preference": ["parquet", "tbl"],
            "tables": {
                "customer": {
                    "formats": {
                        "tbl": [{"path": "customer.tbl", "size_bytes": 100, "row_count": 10}],
                        "parquet": [{"path": "customer.parquet", "size_bytes": 50, "row_count": 10}],
                    }
                }
            },
        }
        (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest_data))

        src = ManifestFileSource()
        src._platform_name = "duckdb"

        ds = src.get_data_source(MagicMock(spec=[]), tmp_path)

        assert ds is not None
        assert ds.source_type == "manifest_v2"

        assert ds.tables["customer"] == [tmp_path / "customer.tbl"]

    def test_manifest_v2_redshift_native_prefers_platform_default(self, tmp_path):
        manifest_data = {
            "version": 2,
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "format_preference": ["parquet", "tbl"],
            "tables": {
                "customer": {
                    "formats": {
                        "tbl": [{"path": "customer.tbl", "size_bytes": 100, "row_count": 10}],
                        "parquet": [{"path": "customer.parquet", "size_bytes": 50, "row_count": 10}],
                    }
                }
            },
        }
        (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest_data))

        src = ManifestFileSource()
        src._platform_name = "redshift"
        src._table_mode = "native"

        ds = src.get_data_source(MagicMock(spec=[]), tmp_path)

        assert ds is not None
        assert ds.source_type == "manifest_v2"
        assert ds.tables["customer"] == [tmp_path / "customer.tbl"]

    def test_manifest_v2_bigquery_native_prefers_parquet(self, tmp_path):
        manifest_data = {
            "version": 2,
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "format_preference": [],
            "tables": {
                "customer": {
                    "formats": {
                        "tbl": [{"path": "customer.tbl", "size_bytes": 100, "row_count": 10}],
                        "parquet": [{"path": "customer.parquet", "size_bytes": 50, "row_count": 10}],
                    }
                }
            },
        }
        (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest_data))

        src = ManifestFileSource()
        src._platform_name = "bigquery"
        src._table_mode = "native"

        ds = src.get_data_source(MagicMock(spec=[]), tmp_path)

        assert ds is not None
        assert ds.source_type == "manifest_v2"
        assert ds.tables["customer"] == [tmp_path / "customer.parquet"]

    def test_manifest_v2_redshift_external_keeps_manifest_order(self, tmp_path):
        manifest_data = {
            "version": 2,
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "format_preference": ["parquet", "delta", "tbl"],
            "tables": {
                "lineitem": {
                    "formats": {
                        "tbl": [{"path": "lineitem.tbl", "size_bytes": 100, "row_count": 10}],
                        "parquet": [{"path": "lineitem.parquet", "size_bytes": 50, "row_count": 10}],
                        "delta": [{"path": "lineitem", "size_bytes": 75, "row_count": 10, "is_directory": True}],
                    }
                }
            },
        }
        (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest_data))

        src = ManifestFileSource()
        src._platform_name = "redshift"
        src._table_mode = "external"
        src._platform_config = {
            "staging_root": "s3://bucket/prefix",
            "iam_role": "arn:aws:iam::123456789012:role/benchbox",
        }

        ds = src.get_data_source(MagicMock(spec=[]), tmp_path)

        assert ds is not None
        assert ds.source_type == "manifest_v2"
        assert ds.tables["lineitem"] == [tmp_path / "lineitem.parquet"]

    def test_manifest_v2_lowercases_table_format_hints_for_mixed_case_tables(self, tmp_path):
        manifest_data = {
            "version": 2,
            "benchmark": "tpcdi",
            "scale_factor": 0.01,
            "format_preference": ["csv"],
            "tables": {
                "DimCustomer": {
                    "formats": {
                        "csv": [{"path": "DimCustomer.csv", "size_bytes": 100, "row_count": 10}],
                    }
                }
            },
        }
        (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest_data))

        src = ManifestFileSource()
        src._platform_name = "datafusion"

        ds = src.get_data_source(MagicMock(spec=[]), tmp_path)

        assert ds is not None
        assert ds.source_type == "manifest_v2"
        assert ds.tables["DimCustomer"] == [tmp_path / "DimCustomer.csv"]
        assert ds.table_formats == {"dimcustomer": "csv"}

    def test_manifest_v2_falls_back_to_first_available_format_when_no_preference_matches(self, tmp_path, monkeypatch):
        manifest_data = {
            "version": 2,
            "benchmark": "tpcdi",
            "scale_factor": 0.01,
            "format_preference": ["parquet"],
            "tables": {
                "DimCustomer": {
                    "formats": {
                        "csv": [{"path": "DimCustomer.csv", "size_bytes": 100, "row_count": 10}],
                        "tbl": [{"path": "DimCustomer.tbl", "size_bytes": 120, "row_count": 10}],
                    }
                }
            },
        }
        (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest_data))
        monkeypatch.setattr("benchbox.core.manifest.get_preferred_format", lambda *args, **kwargs: None)

        src = ManifestFileSource()
        ds = src.get_data_source(MagicMock(spec=[]), tmp_path)

        assert ds is not None
        assert ds.tables["DimCustomer"] == [tmp_path / "DimCustomer.csv"]
        assert ds.table_formats == {"dimcustomer": "csv"}

    def test_manifest_v2_returns_none_when_selected_format_has_no_files(self, tmp_path, monkeypatch):
        manifest_data = {
            "version": 2,
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "format_preference": ["parquet"],
            "tables": {
                "lineitem": {
                    "formats": {
                        "parquet": [{"path": "lineitem.parquet", "size_bytes": 50, "row_count": 10}],
                    }
                }
            },
        }
        (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest_data))
        monkeypatch.setattr("benchbox.core.manifest.get_preferred_format", lambda *args, **kwargs: "parquet")
        monkeypatch.setattr("benchbox.core.manifest.get_files_for_format", lambda *args, **kwargs: [])

        src = ManifestFileSource()
        ds = src.get_data_source(MagicMock(spec=[]), tmp_path)

        assert ds is None

    def test_manifest_v2_returns_none_when_no_fallback_formats_have_files(self, tmp_path, monkeypatch):
        manifest_data = {
            "version": 2,
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "format_preference": ["parquet"],
            "tables": {
                "lineitem": {
                    "formats": {
                        "csv": [],
                        "tbl": [],
                    }
                }
            },
        }
        (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest_data))
        monkeypatch.setattr("benchbox.core.manifest.get_preferred_format", lambda *args, **kwargs: None)

        src = ManifestFileSource()
        ds = src.get_data_source(MagicMock(spec=[]), tmp_path)

        assert ds is None

    def test_manifest_v2_defaults_to_duckdb_when_platform_name_unset(self, tmp_path):

        manifest_data = {
            "version": 2,
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "format_preference": ["parquet", "tbl"],
            "tables": {
                "customer": {
                    "formats": {
                        "tbl": [{"path": "customer.tbl", "size_bytes": 100, "row_count": 10}],
                        "parquet": [{"path": "customer.parquet", "size_bytes": 50, "row_count": 10}],
                    }
                }
            },
        }
        (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest_data))

        src = ManifestFileSource()

        ds = src.get_data_source(MagicMock(spec=[]), tmp_path)

        assert ds is not None
        assert ds.source_type == "manifest_v2"

        assert ds.tables["customer"] == [tmp_path / "customer.tbl"]

    def test_manifest_v2_non_import_error_falls_back_to_v1(self, tmp_path, monkeypatch):

        manifest_data = {
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "tables": {
                "customer": [{"path": "customer.tbl", "size_bytes": 100, "row_count": 10}],
            },
        }
        (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest_data))

        def bad_load(_path):
            raise RuntimeError("simulated non-import error in load_manifest")

        monkeypatch.setattr("benchbox.core.manifest.load_manifest", bad_load, raising=False)

        src = ManifestFileSource()
        ds = src.get_data_source(MagicMock(spec=[]), tmp_path)

        assert ds is not None
        assert ds.source_type == "manifest"
        assert "customer" in ds.tables


class TestManifestFileSourceReadFormatHints:
    def test_returns_empty_when_manifest_missing(self, tmp_path):
        src = ManifestFileSource()
        result = src.read_format_hints(tmp_path / "_datagen_manifest.json", MagicMock(spec=[]), ["t1"])
        assert result == {}

    def test_returns_empty_for_v1_manifest(self, tmp_path):

        manifest_data = {
            "benchmark": "tpch",
            "tables": {"lineitem": [{"path": "lineitem.tbl", "size_bytes": 100, "row_count": 10}]},
        }
        (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest_data))
        src = ManifestFileSource()
        result = src.read_format_hints(tmp_path / "_datagen_manifest.json", MagicMock(spec=[]), ["lineitem"])
        assert result == {}

    def test_returns_format_for_v2_manifest(self, tmp_path):

        manifest_data = {
            "version": 2,
            "benchmark": "clickbench",
            "scale_factor": 1,
            "format_preference": ["tbl", "csv"],
            "tables": {
                "lineitem": {
                    "formats": {
                        "tbl": [{"path": "lineitem.csv.zst", "size_bytes": 100, "row_count": 10}],
                    }
                }
            },
        }
        (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest_data))
        src = ManifestFileSource()
        result = src.read_format_hints(tmp_path / "_datagen_manifest.json", MagicMock(spec=[]), ["lineitem"])
        assert result == {"lineitem": "tbl"}

    def test_read_format_hints_defaults_platform_name_to_duckdb(self, tmp_path):

        manifest_data = {
            "version": 2,
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "format_preference": ["parquet", "tbl"],
            "tables": {
                "customer": {
                    "formats": {
                        "tbl": [{"path": "customer.tbl", "size_bytes": 100, "row_count": 10}],
                        "parquet": [{"path": "customer.parquet", "size_bytes": 50, "row_count": 10}],
                    }
                }
            },
        }
        (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest_data))
        src = ManifestFileSource()

        result = src.read_format_hints(tmp_path / "_datagen_manifest.json", MagicMock(spec=[]), ["customer"])
        assert result == {"customer": "tbl"}

    def test_uses_platform_name_for_format_selection(self, tmp_path):

        manifest_data = {
            "version": 2,
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "format_preference": ["parquet", "tbl"],
            "tables": {
                "customer": {
                    "formats": {
                        "parquet": [{"path": "customer.parquet", "size_bytes": 50, "row_count": 10}],
                        "tbl": [{"path": "customer.tbl", "size_bytes": 100, "row_count": 10}],
                    }
                }
            },
        }
        (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest_data))
        src = ManifestFileSource()
        src._platform_name = "bigquery"
        src._table_mode = "native"
        result = src.read_format_hints(tmp_path / "_datagen_manifest.json", MagicMock(spec=[]), ["customer"])

        assert result.get("customer") == "parquet"

    def test_only_returns_hints_for_requested_tables(self, tmp_path):

        manifest_data = {
            "version": 2,
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "format_preference": ["tbl"],
            "tables": {
                "customer": {"formats": {"tbl": [{"path": "customer.tbl", "size_bytes": 10, "row_count": 1}]}},
                "orders": {"formats": {"tbl": [{"path": "orders.tbl", "size_bytes": 10, "row_count": 1}]}},
            },
        }
        (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest_data))
        src = ManifestFileSource()
        result = src.read_format_hints(tmp_path / "_datagen_manifest.json", MagicMock(spec=[]), ["customer"])
        assert "customer" in result
        assert "orders" not in result

    def test_returns_empty_on_exception(self, tmp_path, monkeypatch):

        manifest_path = tmp_path / "_datagen_manifest.json"
        manifest_path.write_text("{}")

        def bad_load(_path):
            raise RuntimeError("simulated error")

        src = ManifestFileSource()
        monkeypatch.setattr("benchbox.core.manifest.load_manifest", bad_load, raising=False)
        result = src.read_format_hints(manifest_path, MagicMock(), ["t1"])
        assert result == {}

    def test_skips_requested_tables_without_resolved_format(self, tmp_path, monkeypatch):

        manifest_data = {
            "version": 2,
            "benchmark": "tpcdi",
            "scale_factor": 0.01,
            "format_preference": ["csv"],
            "tables": {
                "DimCustomer": {
                    "formats": {
                        "csv": [{"path": "DimCustomer.csv", "size_bytes": 100, "row_count": 10}],
                    }
                },
                "FactTrade": {
                    "formats": {
                        "csv": [{"path": "FactTrade.csv", "size_bytes": 120, "row_count": 10}],
                    }
                },
            },
        }
        manifest_path = tmp_path / "_datagen_manifest.json"
        manifest_path.write_text(json.dumps(manifest_data))

        def fake_get_preferred_format(_manifest, table_name, *_args, **_kwargs):
            return "csv" if table_name == "DimCustomer" else None

        src = ManifestFileSource()
        monkeypatch.setattr("benchbox.core.manifest.get_preferred_format", fake_get_preferred_format)
        result = src.read_format_hints(manifest_path, MagicMock(spec=[]), ["DimCustomer", "FactTrade"])

        assert result == {"dimcustomer": "csv"}


class TestDataSourceResolver:
    def test_prefers_benchmark_tables_first(self, tmp_path):

        benchmark = MagicMock()
        benchmark.tables = {"t1": Path("/x")}

        manifest_data = {
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "tables": {"t1": [{"path": "t1.csv", "size_bytes": 10, "row_count": 1}]},
        }
        (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest_data))

        resolver = DataSourceResolver()
        ds = resolver.resolve(benchmark, tmp_path)
        assert ds is not None
        assert ds.source_type == "benchmark_tables"

    def test_falls_through_to_manifest(self, tmp_path):

        benchmark = MagicMock(spec=[])
        manifest_data = {
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "tables": {"t1": [{"path": "t1.csv", "size_bytes": 10, "row_count": 1}]},
        }
        (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest_data))

        resolver = DataSourceResolver()
        ds = resolver.resolve(benchmark, tmp_path)
        assert ds is not None
        assert ds.source_type == "manifest"

    def test_returns_none_when_no_source(self, tmp_path):
        benchmark = MagicMock(spec=[])
        resolver = DataSourceResolver()
        ds = resolver.resolve(benchmark, tmp_path)
        assert ds is None

    def test_platform_name_propagated_to_manifest_source(self):
        resolver = DataSourceResolver(platform_name="snowflake")
        assert getattr(resolver._manifest_source, "_platform_name", None) == "snowflake"

    def test_table_mode_propagated(self):
        resolver = DataSourceResolver(table_mode="external")
        assert getattr(resolver._manifest_source, "_table_mode", None) == "external"

    def test_platform_config_propagated(self):
        cfg = {"key": "value"}
        resolver = DataSourceResolver(platform_config=cfg)
        assert getattr(resolver._manifest_source, "_platform_config", None) is cfg

    def test_format_hints_injected_for_benchmark_tables_source(self, tmp_path):

        benchmark = MagicMock()
        benchmark.tables = {"lineitem": tmp_path / "lineitem.csv.zst"}

        manifest_data = {
            "version": 2,
            "benchmark": "clickbench",
            "scale_factor": 1,
            "format_preference": ["tbl", "csv"],
            "tables": {
                "lineitem": {
                    "formats": {
                        "tbl": [{"path": "lineitem.csv.zst", "size_bytes": 100, "row_count": 10}],
                    }
                }
            },
        }
        (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest_data))

        resolver = DataSourceResolver(platform_name="datafusion")
        ds = resolver.resolve(benchmark, tmp_path)

        assert ds is not None
        assert ds.source_type == "benchmark_tables"
        assert ds.table_formats.get("lineitem") == "tbl"

    def test_bigquery_native_uses_all_manifest_tbl_shards(self, tmp_path):

        first = tmp_path / "lineitem_000.tbl.gz"
        second = tmp_path / "lineitem_001.tbl.gz"
        first.write_bytes(b"a")
        second.write_bytes(b"b")
        benchmark = MagicMock()
        benchmark.tables = {"lineitem": first}
        manifest_data = {
            "version": 2,
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "format_preference": ["tbl"],
            "tables": {
                "lineitem": {
                    "formats": {
                        "tbl": [
                            {"path": first.name, "size_bytes": 1, "row_count": 1},
                            {"path": second.name, "size_bytes": 1, "row_count": 1},
                        ]
                    }
                }
            },
        }
        (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest_data))

        source = DataSourceResolver(platform_name="bigquery", table_mode="native").resolve(benchmark, tmp_path)
        assert source is not None
        assert source.tables["lineitem"] == [first, second]
        assert source.table_formats["lineitem"] == "tbl"

        external = tmp_path / "external.tbl.gz"
        external.write_bytes(b"c")
        benchmark.tables = {"lineitem": external}
        source = DataSourceResolver(platform_name="bigquery", table_mode="native").resolve(benchmark, tmp_path)
        assert source is not None
        assert source.tables["lineitem"] == [external]

    def test_bigquery_native_infers_tbl_format_for_v1_manifest(self, tmp_path):

        first = tmp_path / "lineitem_000.tbl.gz"
        second = tmp_path / "lineitem_001.tbl.gz"
        first.write_bytes(b"a")
        second.write_bytes(b"b")
        benchmark = MagicMock()
        benchmark.tables = {"lineitem": first}
        manifest_data = {
            "tables": {
                "lineitem": [
                    {"path": first.name},
                    {"path": second.name},
                ]
            }
        }
        (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest_data))

        source = DataSourceResolver(platform_name="bigquery", table_mode="native").resolve(benchmark, tmp_path)
        assert source is not None
        assert source.tables["lineitem"] == [first, second]
        assert source.table_formats["lineitem"] == "tbl"

    def test_format_hints_injected_for_mixed_case_benchmark_tables_source(self, tmp_path):

        benchmark = MagicMock()
        benchmark.tables = {"DimCustomer": tmp_path / "DimCustomer.csv"}

        manifest_data = {
            "version": 2,
            "benchmark": "tpcdi",
            "scale_factor": 0.01,
            "format_preference": ["csv"],
            "tables": {
                "DimCustomer": {
                    "formats": {
                        "csv": [{"path": "DimCustomer.csv", "size_bytes": 100, "row_count": 10}],
                    }
                }
            },
        }
        (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest_data))

        resolver = DataSourceResolver(platform_name="datafusion")
        ds = resolver.resolve(benchmark, tmp_path)

        assert ds is not None
        assert ds.source_type == "benchmark_tables"
        assert ds.table_formats == {"dimcustomer": "csv"}

    def test_format_hints_injected_for_impl_tables_source(self, tmp_path):

        impl = MagicMock()
        impl.tables = {"hits": tmp_path / "hits.csv.zst"}
        benchmark = MagicMock(spec=["_impl"])
        benchmark._impl = impl

        manifest_data = {
            "version": 2,
            "benchmark": "clickbench",
            "scale_factor": 1,
            "format_preference": ["tbl"],
            "tables": {
                "hits": {
                    "formats": {
                        "tbl": [{"path": "hits.csv.zst", "size_bytes": 100, "row_count": 10}],
                    }
                }
            },
        }
        (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest_data))

        resolver = DataSourceResolver(platform_name="datafusion")
        ds = resolver.resolve(benchmark, tmp_path)

        assert ds is not None
        assert ds.source_type == "benchmark_impl_tables"
        assert ds.table_formats.get("hits") == "tbl"

    def test_manifest_source_table_formats_not_overwritten(self, tmp_path):

        benchmark = MagicMock(spec=[])

        manifest_data = {
            "version": 2,
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "format_preference": ["parquet", "tbl"],
            "tables": {
                "customer": {
                    "formats": {
                        "parquet": [{"path": "customer.parquet", "size_bytes": 50, "row_count": 5}],
                    }
                }
            },
        }
        (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest_data))
        (tmp_path / "customer.parquet").write_bytes(b"")

        resolver = DataSourceResolver(platform_name="datafusion")
        ds = resolver.resolve(benchmark, tmp_path)

        assert ds is not None
        assert ds.source_type == "manifest_v2"

        assert ds.table_formats.get("customer") == "parquet"

    def test_format_hints_empty_when_no_manifest(self, tmp_path):

        benchmark = MagicMock()
        benchmark.tables = {"orders": tmp_path / "orders.tbl"}

        resolver = DataSourceResolver(platform_name="datafusion")
        ds = resolver.resolve(benchmark, tmp_path)

        assert ds is not None
        assert ds.source_type == "benchmark_tables"
        assert ds.table_formats == {}

    def test_format_hints_empty_for_v1_manifest(self, tmp_path):

        benchmark = MagicMock()
        benchmark.tables = {"lineitem": tmp_path / "lineitem.tbl"}
        manifest_data = {
            "benchmark": "tpch",
            "tables": {"lineitem": [{"path": "lineitem.tbl", "size_bytes": 100, "row_count": 10}]},
        }
        (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest_data))

        resolver = DataSourceResolver(platform_name="datafusion")
        ds = resolver.resolve(benchmark, tmp_path)

        assert ds is not None
        assert ds.source_type == "benchmark_tables"

        assert ds.table_formats == {}

    def test_manifest_source_is_shared_with_providers(self):

        resolver = DataSourceResolver(platform_name="snowflake")
        assert resolver._manifest_source is resolver.providers[2]

    def test_athena_external_replaces_non_parquet_benchmark_tables_with_manifest_selection(self, tmp_path):

        benchmark = MagicMock()
        tbl_file = tmp_path / "lineitem.tbl"
        parquet_file = tmp_path / "lineitem.parquet"
        tbl_file.write_text("1|x|\n")
        parquet_file.write_bytes(b"PAR1")
        benchmark.tables = {"lineitem": tbl_file}

        manifest_data = {
            "version": 2,
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "format_preference": ["tbl", "parquet"],
            "tables": {
                "lineitem": {
                    "formats": {
                        "tbl": [{"path": "lineitem.tbl", "size_bytes": 5, "row_count": 1}],
                        "parquet": [{"path": "lineitem.parquet", "size_bytes": 4, "row_count": 1}],
                    }
                }
            },
        }
        (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest_data))

        resolver = DataSourceResolver(platform_name="athena", table_mode="external")
        ds = resolver.resolve(benchmark, tmp_path)

        assert ds is not None
        assert ds.source_type == "benchmark_tables"
        assert ds.tables == {"lineitem": [parquet_file]}
        assert ds.table_formats.get("lineitem") == "parquet"

    def test_redshift_native_replaces_directory_benchmark_tables_with_manifest_selection(self, tmp_path):

        benchmark = MagicMock()
        table_dir = tmp_path / "orders"
        table_dir.mkdir()
        (table_dir / "_delta_log").mkdir()
        (table_dir / "part-00000.parquet").write_bytes(b"PAR1")
        tbl_file = tmp_path / "orders.tbl"
        tbl_file.write_text("1|2024-01-01|O|1.00|\n")
        benchmark.tables = {"orders": [table_dir]}

        manifest_data = {
            "version": 2,
            "benchmark": "tpch",
            "scale_factor": 1.0,
            "format_preference": ["delta", "tbl"],
            "tables": {
                "orders": {
                    "formats": {
                        "delta": [
                            {
                                "path": "orders",
                                "size_bytes": 4,
                                "row_count": 1,
                                "is_directory": True,
                            }
                        ],
                        "tbl": [
                            {
                                "path": "orders.tbl",
                                "size_bytes": tbl_file.stat().st_size,
                                "row_count": 1,
                            }
                        ],
                    }
                }
            },
        }
        (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest_data))

        resolver = DataSourceResolver(platform_name="redshift", table_mode="native")
        ds = resolver.resolve(benchmark, tmp_path)

        assert ds is not None
        assert ds.source_type == "benchmark_tables"
        assert ds.tables == {"orders": [tbl_file]}
        assert ds.table_formats.get("orders") == "tbl"

    def test_impl_tables_source_format_hints_empty_when_no_manifest(self, tmp_path):

        impl = MagicMock()
        impl.tables = {"hits": tmp_path / "hits.csv.zst"}
        benchmark = MagicMock(spec=["_impl"])
        benchmark._impl = impl

        resolver = DataSourceResolver(platform_name="datafusion")
        ds = resolver.resolve(benchmark, tmp_path)

        assert ds is not None
        assert ds.source_type == "benchmark_impl_tables"
        assert ds.table_formats == {}


class TestInMemoryDataHandler:
    def test_load_dict_rows(self):
        from benchbox.platforms.base.data_loading import InMemoryDataHandler

        conn = MagicMock()
        rows = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
        count = InMemoryDataHandler.load_table("mytable", rows, conn)
        assert count == 2
        conn.executemany.assert_called_once()

    def test_load_tuple_rows(self):
        from benchbox.platforms.base.data_loading import InMemoryDataHandler

        conn = MagicMock()
        rows = [(1, 2), (3, 4)]
        count = InMemoryDataHandler.load_table("mytable", rows, conn)
        assert count == 2

    def test_load_empty_iterable(self):
        from benchbox.platforms.base.data_loading import InMemoryDataHandler

        conn = MagicMock()
        count = InMemoryDataHandler.load_table("mytable", [], conn)
        assert count == 0

    def test_load_string_returns_zero(self):

        from benchbox.platforms.base.data_loading import InMemoryDataHandler

        conn = MagicMock()
        count = InMemoryDataHandler.load_table("mytable", "not_iterable_data", conn)
        assert count == 0


class TestPrepareLocalLoadFile:
    def _dialect(self, delimiter=",", null_marker=None, normalize_booleans=False):
        from benchbox.platforms.base.data_loading import CsvDialect

        return CsvDialect(
            delimiter=delimiter,
            has_header=False,
            null_marker=null_marker,
            normalize_booleans=normalize_booleans,
            quote=None,
        )

    def test_no_transform_yields_original_path(self, tmp_path):

        from benchbox.platforms.base.data_loading import prepare_local_load_file

        data_file = tmp_path / "data.csv"
        data_file.write_text("1,foo\n2,bar\n", encoding="utf-8")

        dialect = self._dialect(delimiter=",")
        with prepare_local_load_file(data_file, dialect=dialect, strip_trailing_delim=False) as load_path:
            assert load_path == data_file

    def test_strip_trailing_delim_removes_trailing_pipe(self, tmp_path):

        from benchbox.platforms.base.data_loading import prepare_local_load_file

        data_file = tmp_path / "lineitem.tbl"
        data_file.write_text("1|hello|world|\n2|foo|bar|\n", encoding="utf-8")

        dialect = self._dialect(delimiter="|", null_marker="")
        with prepare_local_load_file(data_file, dialect=dialect, strip_trailing_delim=True) as load_path:
            content = load_path.read_text(encoding="utf-8")

        assert content == "1|hello|world\n2|foo|bar\n"

    def test_no_strip_preserves_trailing_delimiter(self, tmp_path):

        from benchbox.platforms.base.data_loading import prepare_local_load_file

        data_file = tmp_path / "title.csv"
        data_file.write_text("1,Comedy Adventure,,4,1957,,,,,,,\n", encoding="utf-8")

        dialect = self._dialect(delimiter=",", null_marker="")
        with prepare_local_load_file(data_file, dialect=dialect, strip_trailing_delim=False) as load_path:
            content = load_path.read_text(encoding="utf-8")

        assert content == "1,Comedy Adventure,,4,1957,,,,,,,\n"

    def test_strip_trailing_delim_also_works_for_dat_files(self, tmp_path):

        from benchbox.platforms.base.data_loading import prepare_local_load_file

        data_file = tmp_path / "customer.dat"
        data_file.write_text("1|Smith|Jane|24525083|\n2|Jones|Bob|99887766|\n", encoding="utf-8")

        dialect = self._dialect(delimiter="|", null_marker="")
        with prepare_local_load_file(data_file, dialect=dialect, strip_trailing_delim=True) as load_path:
            content = load_path.read_text(encoding="utf-8")

        assert content == "1|Smith|Jane|24525083\n2|Jones|Bob|99887766\n"

    def test_strip_only_strips_final_delimiter_not_embedded(self, tmp_path):

        from benchbox.platforms.base.data_loading import prepare_local_load_file

        data_file = tmp_path / "mixed.tbl"

        data_file.write_text("a|b|c|\nd|e|f\n", encoding="utf-8")

        dialect = self._dialect(delimiter="|", null_marker="")
        with prepare_local_load_file(data_file, dialect=dialect, strip_trailing_delim=True) as load_path:
            content = load_path.read_text(encoding="utf-8")

        assert content == "a|b|c\nd|e|f\n"

    def test_boolean_normalisation_rewrites_true_false(self, tmp_path):

        from benchbox.platforms.base.data_loading import prepare_local_load_file

        data_file = tmp_path / "data.csv"
        data_file.write_text("True,hello,False\nFalse,world,True\n", encoding="utf-8")

        dialect = self._dialect(delimiter=",", normalize_booleans=True)
        with prepare_local_load_file(data_file, dialect=dialect, strip_trailing_delim=False) as load_path:
            content = load_path.read_text(encoding="utf-8")

        assert content == "1,hello,0\n0,world,1\n"

    def test_temp_file_cleaned_up_after_context(self, tmp_path):

        from benchbox.platforms.base.data_loading import prepare_local_load_file

        data_file = tmp_path / "lineitem.tbl"
        data_file.write_text("1|2|3|\n", encoding="utf-8")

        dialect = self._dialect(delimiter="|", null_marker="")
        captured: list[Path] = []
        with prepare_local_load_file(data_file, dialect=dialect, strip_trailing_delim=True) as load_path:
            assert load_path != data_file
            captured.append(load_path)

        assert not captured[0].exists()

    def test_load_none_returns_zero(self):
        from benchbox.platforms.base.data_loading import InMemoryDataHandler

        conn = MagicMock()
        count = InMemoryDataHandler.load_table("mytable", None, conn)
        assert count == 0


class TestEmptySourceFailsClosed:
    @staticmethod
    def _loader(tmp_path: Path, benchmark: object, resolved: object) -> DataLoader:
        loader = DataLoader.__new__(DataLoader)
        loader.adapter = MagicMock()
        loader.benchmark = benchmark
        loader.connection = MagicMock()
        loader.data_dir = tmp_path
        loader.tuning_config = None
        loader.resolver = MagicMock()
        loader.resolver.resolve = MagicMock(return_value=resolved)
        return loader

    def test_load_raises_when_no_source_resolves(self, tmp_path: Path) -> None:
        from types import SimpleNamespace

        loader = self._loader(tmp_path, SimpleNamespace(), None)
        with pytest.raises(ValueError, match="No data files found"):
            loader.load()

    def test_load_raises_when_source_names_zero_tables(self, tmp_path: Path) -> None:
        from types import SimpleNamespace

        empty = DataSource(source_type="manifest", tables={})
        loader = self._loader(tmp_path, SimpleNamespace(), empty)
        with pytest.raises(ValueError, match="No data files found"):
            loader.load()

    def test_load_stays_empty_when_benchmark_skips_loading(self, tmp_path: Path) -> None:
        from types import SimpleNamespace

        skipped = SimpleNamespace(SKIP_DATA_LOADING=True)
        loader = self._loader(tmp_path, skipped, None)
        stats, _ = loader.load()
        assert stats == {}

    def test_load_raises_when_every_table_maps_to_an_empty_file_list(self, tmp_path: Path) -> None:
        from types import SimpleNamespace

        source = DataSource(source_type="benchmark_tables", tables={"customer": [], "orders": []})
        loader = self._loader(tmp_path, SimpleNamespace(), source)
        with pytest.raises(ValueError, match="No data files found"):
            loader.load()

    def test_load_stays_empty_for_skip_benchmark_with_empty_file_lists(self, tmp_path: Path) -> None:
        from types import SimpleNamespace

        skipped = SimpleNamespace(SKIP_DATA_LOADING=True)
        source = DataSource(source_type="benchmark_tables", tables={"customer": []})
        loader = self._loader(tmp_path, skipped, source)
        stats, _ = loader.load()
        assert stats == {}

    def test_load_with_empty_file_lists_does_not_silently_skip_for_mock_benchmark(self, tmp_path: Path) -> None:
        source = DataSource(source_type="benchmark_tables", tables={"customer": []})
        loader = self._loader(tmp_path, MagicMock(), source)
        with pytest.raises(ValueError, match="No data files found"):
            loader.load()


class TestManifestMissingFiles:
    @staticmethod
    def _write_manifest(data_dir: Path, tables: dict) -> None:
        (data_dir / "_datagen_manifest.json").write_text(json.dumps({"tables": tables}), encoding="utf-8")

    def test_empty_tables_manifest_resolves_to_no_source(self, tmp_path: Path) -> None:
        from types import SimpleNamespace

        (tmp_path / "call_center.dat").write_text("1|\n", encoding="utf-8")
        self._write_manifest(tmp_path, {})
        assert ManifestFileSource().get_data_source(SimpleNamespace(), tmp_path) is None


class TestZstdToolProbe:
    def test_missing_zstd_cli_raises_actionable_error_on_open(self, tmp_path: Path, monkeypatch) -> None:
        from benchbox.platforms.base import data_loading

        monkeypatch.setattr(data_loading.shutil, "which", lambda _cmd: None)
        handler = FileFormatRegistry.get_compression_handler(tmp_path / "x.dat.zst")
        with pytest.raises(DataLoadingError, match="zstd.*command was not found"), handler.open(tmp_path / "x.dat.zst"):
            pass

    def test_handler_selection_does_not_require_the_zstd_cli(self, tmp_path: Path, monkeypatch) -> None:
        from benchbox.platforms.base import data_loading

        monkeypatch.setattr(data_loading.shutil, "which", lambda _cmd: None)
        handler = FileFormatRegistry.get_compression_handler(tmp_path / "x.dat.zst")
        assert isinstance(handler, ZstdHandler)
