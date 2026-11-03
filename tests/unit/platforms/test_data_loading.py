from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pytest

from benchbox.platforms.base.data_loading import (
    ClickHouseNativeHandler,
    DataSource,
    DataSourceResolver,
    ParquetFileHandler,
    resolve_adapter_data_source,
)
from benchbox.platforms.sqlite import SQLiteAdapter

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class _FakeBenchmark:
    pass


@dataclass
class _BenchmarkWithTables:
    tables: dict


@dataclass
class _AdapterStub:
    platform_name: str = "Test Platform"
    table_mode: str = "external"
    platform_config: dict | None = None
    requested_table_format: str | None = "parquet"


def _write_manifest(directory: Path, tables_metadata: dict) -> None:

    tables_section = {}
    for table_name, meta in tables_metadata.items():
        tables_section[table_name] = {
            "formats": {
                "csv": [
                    {
                        "path": f"{table_name}.csv",
                        "size_bytes": 0,
                        "row_count": 0,
                        "metadata": meta,
                    }
                ]
            }
        }

    manifest = {
        "version": 2,
        "benchmark": "test",
        "scale_factor": 0.01,
        "formats": ["csv"],
        "format_preference": ["csv"],
        "compression": {"enabled": False, "type": None, "level": None},
        "parallel": 1,
        "tables": tables_section,
    }
    (directory / "_datagen_manifest.json").write_text(json.dumps(manifest))

    for table_name in tables_metadata:
        (directory / f"{table_name}.csv").write_text("")


def test_table_metadata_roundtrips_through_resolver(tmp_path: Path) -> None:

    meta = {
        "csv_has_header": True,
        "csv_delimiter": ",",
        "csv_null_marker": None,
        "csv_normalize_booleans": False,
        "csv_quote": '"',
    }
    _write_manifest(tmp_path, {"customer": meta})

    benchmark = _FakeBenchmark()
    resolver = DataSourceResolver(platform_name="duckdb")
    source = resolver.resolve(benchmark, tmp_path)

    assert source is not None
    assert "customer" in source.table_metadata
    resolved_meta = source.table_metadata["customer"]
    assert resolved_meta["csv_has_header"] is True
    assert resolved_meta["csv_delimiter"] == ","
    assert resolved_meta.get("csv_null_marker") is None


def test_table_metadata_empty_when_no_manifest(tmp_path: Path) -> None:

    stub_file = tmp_path / "customer.csv"
    stub_file.write_text("")
    benchmark = _BenchmarkWithTables(tables={"customer": stub_file})

    resolver = DataSourceResolver(platform_name="duckdb")
    source = resolver.resolve(benchmark, tmp_path)

    assert source is not None

    assert source.table_metadata == {}


def test_datasource_table_metadata_defaults_to_empty_dict() -> None:

    ds = DataSource(source_type="benchmark_tables", tables={"t": [Path("/x")]})
    assert ds.table_metadata == {}


def test_resolve_adapter_data_source_uses_standard_adapter_state(monkeypatch, tmp_path: Path) -> None:

    calls = {}
    data_source = DataSource(source_type="benchmark_tables", tables={"t": [tmp_path / "t.tbl"]})

    class FakeResolver:
        def __init__(self, **kwargs):
            calls["init"] = kwargs

        def resolve(self, benchmark, data_dir):
            calls["resolve"] = (benchmark, data_dir)
            return data_source

    monkeypatch.setattr("benchbox.platforms.base.data_loading.DataSourceResolver", FakeResolver)
    adapter = _AdapterStub(platform_config={"storage": "external"})
    benchmark = object()

    result = resolve_adapter_data_source(adapter, benchmark, tmp_path)

    assert result is data_source
    assert calls["init"] == {
        "platform_name": "Test Platform",
        "table_mode": "external",
        "platform_config": {"storage": "external"},
        "requested_format": "parquet",
    }
    assert calls["resolve"] == (benchmark, tmp_path)


def test_table_metadata_present_when_manifest_wins(tmp_path: Path) -> None:

    meta = {"csv_has_header": False, "csv_normalize_booleans": True}
    _write_manifest(tmp_path, {"orders": meta})

    benchmark = _FakeBenchmark()
    resolver = DataSourceResolver(platform_name="singlestore")
    source = resolver.resolve(benchmark, tmp_path)

    assert source is not None
    assert source.table_metadata.get("orders", {}).get("csv_normalize_booleans") is True


def test_table_metadata_injected_when_benchmark_tables_wins(tmp_path: Path) -> None:

    meta = {"csv_has_header": True, "csv_delimiter": ","}

    _write_manifest(tmp_path, {"customer": meta})

    stub_file = tmp_path / "customer.csv"
    benchmark = _BenchmarkWithTables(tables={"customer": stub_file})

    resolver = DataSourceResolver(platform_name="singlestore")
    source = resolver.resolve(benchmark, tmp_path)

    assert source is not None
    assert source.source_type == "benchmark_tables"
    assert source.table_metadata.get("customer", {}).get("csv_has_header") is True


def test_clickhouse_native_handler_uses_csv_with_names_for_headered_csv(tmp_path: Path) -> None:

    connection = _FakeClickHouseConnection()
    handler = ClickHouseNativeHandler(",", adapter=object(), benchmark=object(), has_header=True)

    handler.load_table("flights", tmp_path / "flights.csv", connection, object(), logging.getLogger(__name__))

    load_queries = [query for query in connection.queries if "file(" in query]
    assert load_queries
    assert "CSVWithNames" in load_queries[0]


def test_parquet_handler_streams_record_batches(monkeypatch, tmp_path: Path) -> None:

    pa = pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq

    source = tmp_path / "events.parquet"
    pq.write_table(
        pa.table(
            {
                "id": [1, 2, 3],
                "name": ["alpha", "beta", "gamma"],
                "amount": pa.array([Decimal("1.25"), None, Decimal("3.75")], type=pa.decimal128(15, 2)),
            }
        ),
        source,
        row_group_size=2,
    )

    def fail_if_materialized(*args, **kwargs):
        raise AssertionError("Parquet loading must use record batches, not read_table")

    monkeypatch.setattr(pq, "read_table", fail_if_materialized)
    connection = SQLiteAdapter(database_path=":memory:").create_connection()
    try:
        connection.execute("CREATE TABLE events (id INTEGER, name TEXT, amount DECIMAL(15, 2))")
        row_count = ParquetFileHandler().load_table("events", source, connection, object(), logging.getLogger(__name__))

        assert row_count == 3
        assert connection.execute("SELECT * FROM events ORDER BY id").fetchall() == [
            (1, "alpha", 1.25),
            (2, "beta", None),
            (3, "gamma", 3.75),
        ]
    finally:
        connection.close()


class _FailingParquetBatchConnection:
    def __init__(self, connection, failing_batch: int = 2) -> None:
        self._connection = connection
        self._failing_batch = failing_batch
        self._batch_calls = 0

    def executemany(self, sql, rows):
        self._batch_calls += 1
        if self._batch_calls == self._failing_batch:
            raise RuntimeError("simulated Parquet batch insert failure")
        return self._connection.executemany(sql, rows)

    def __getattr__(self, name):
        return getattr(self._connection, name)


def _write_batched_parquet(path: Path) -> None:
    pa = pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq

    pq.write_table(pa.table({"id": list(range(2001))}), path, row_group_size=1000)


def test_parquet_handler_rolls_back_prior_batches_when_insert_fails(tmp_path: Path) -> None:

    source = tmp_path / "events.parquet"
    _write_batched_parquet(source)
    raw_connection = SQLiteAdapter(database_path=":memory:").create_connection()
    connection = _FailingParquetBatchConnection(raw_connection)
    try:
        connection.execute("CREATE TABLE events (id INTEGER)")

        with pytest.raises(RuntimeError, match="simulated Parquet batch insert failure"):
            ParquetFileHandler().load_table("events", source, connection, object(), logging.getLogger(__name__))

        connection.commit()
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    finally:
        connection.close()


def test_parquet_handler_rolls_back_prior_batches_when_read_fails(monkeypatch, tmp_path: Path) -> None:

    pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq

    source = tmp_path / "events.parquet"
    _write_batched_parquet(source)
    real_parquet_file = pq.ParquetFile

    class _FailingParquetFile:
        def __init__(self, path):
            self._parquet_file = real_parquet_file(path)

        @property
        def schema_arrow(self):
            return self._parquet_file.schema_arrow

        def iter_batches(self, **kwargs):
            batches = self._parquet_file.iter_batches(**kwargs)
            yield next(batches)
            raise RuntimeError("simulated Parquet read failure")

    monkeypatch.setattr(pq, "ParquetFile", _FailingParquetFile)
    raw_connection = SQLiteAdapter(database_path=":memory:").create_connection()
    try:
        raw_connection.execute("CREATE TABLE events (id INTEGER)")

        with pytest.raises(RuntimeError, match="simulated Parquet read failure"):
            ParquetFileHandler().load_table("events", source, raw_connection, object(), logging.getLogger(__name__))

        raw_connection.commit()
        assert raw_connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    finally:
        raw_connection.close()


class _FakeClickHouseConnection:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def execute(self, query: str):
        self.queries.append(query)
        if "COUNT(*)" in query:
            return [(0,)]
        return []


def test_bzip2_handler_decodes_with_requested_encoding(tmp_path: Path) -> None:
    import bz2

    from benchbox.platforms.base.data_loading import Bzip2Handler

    path = tmp_path / "data.csv.bz2"
    path.write_bytes(bz2.compress("na\u00efve|1\n".encode("cp1252")))

    with Bzip2Handler().open(path, newline="", encoding="cp1252") as f:
        assert f.read() == "na\u00efve|1\n"
