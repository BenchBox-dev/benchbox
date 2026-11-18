from __future__ import annotations

import hashlib
import types
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

duckdb = pytest.importorskip("duckdb")
pa = pytest.importorskip("pyarrow")

from benchbox.platforms import duckdb as duckdb_module
from benchbox.platforms.base import data_loading
from benchbox.platforms.base.data_loading import (
    DeltaFileHandler,
    DuckDBDeltaHandler,
    DuckDBDuckLakeHandler,
    DuckDBNativeHandler,
    DuckDBParquetHandler,
    DuckDBVortexHandler,
    DuckLakeFileHandler,
    IcebergFileHandler,
    insert_arrow_table,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

DDL = "CREATE TABLE t (id INTEGER, name VARCHAR, d DATE, amt DECIMAL(12,2), note VARCHAR)"
COLUMNS = ["id", "name", "d", "amt", "note"]


def _arrow_table(rows: int = 2500) -> pa.Table:
    return pa.table(
        {
            "id": pa.array(range(rows), type=pa.int32()),
            "name": pa.array([None if i % 7 == 0 else f"name-{i}" for i in range(rows)], type=pa.string()),
            "d": pa.array([None if i % 11 == 0 else date(2020, 1, 1 + i % 28) for i in range(rows)], type=pa.date32()),
            "amt": pa.array(
                [None if i % 5 == 0 else Decimal(i) / Decimal(100) for i in range(rows)], type=pa.decimal128(12, 2)
            ),
            "note": pa.array([None] * rows, type=pa.string()),
        }
    )


class _NoPylistTable:
    def __init__(self, table):
        self._table = table
        self.num_rows = table.num_rows
        self.schema = table.schema

    def to_pylist(self):
        raise AssertionError("to_pylist must not be called")

    def __arrow_c_stream__(self, requested_schema=None):
        return self._table.__arrow_c_stream__(requested_schema)


def _digests(conn) -> dict[str, str]:
    result = {"count": str(conn.execute("SELECT COUNT(*) FROM t").fetchone()[0])}
    for col in COLUMNS:
        values = conn.execute(f"SELECT CAST({col} AS VARCHAR) FROM t ORDER BY id").fetchall()
        result[col] = hashlib.sha256(repr(values).encode()).hexdigest()
    return result


def _legacy_load(conn, table: pa.Table) -> None:
    names = table.schema.names
    sql = f"INSERT INTO t ({','.join(names)}) VALUES ({','.join('?' for _ in names)})"
    rows = [tuple(row[c] for c in names) for row in table.to_pylist()]
    for i in range(0, len(rows), 1000):
        conn.executemany(sql, rows[i : i + 1000])


def test_insert_arrow_table_matches_legacy_executemany_digests():
    table = _arrow_table()
    old = duckdb.connect(":memory:")
    new = duckdb.connect(":memory:")
    old.execute(DDL)
    new.execute(DDL)

    _legacy_load(old, table)
    assert insert_arrow_table(new, "t", table) == table.num_rows

    assert _digests(new) == _digests(old)
    assert _digests(new)["count"] == str(table.num_rows)


def test_insert_arrow_table_does_not_call_to_pylist(monkeypatch):
    conn = duckdb.connect(":memory:")
    conn.execute(DDL)
    table = _NoPylistTable(_arrow_table(50))

    assert insert_arrow_table(conn, "t", table) == 50
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 50
    assert (
        conn.execute("SELECT COUNT(*) FROM duckdb_views() WHERE view_name LIKE '_benchbox_arrow_%'").fetchone()[0] == 0
    )


def test_insert_arrow_table_respects_column_order_and_empty():
    conn = duckdb.connect(":memory:")
    conn.execute("CREATE TABLE t (a INTEGER, b VARCHAR)")
    assert insert_arrow_table(conn, "t", pa.table({"b": ["x"], "a": pa.array([1], type=pa.int32())})) == 1
    assert conn.execute("SELECT a, b FROM t").fetchall() == [(1, "x")]
    assert insert_arrow_table(conn, "t", pa.table({"a": pa.array([], type=pa.int32())})) == 0


def test_insert_arrow_table_executemany_fallback_for_non_arrow_connection():
    class Plain:
        def __init__(self):
            self.batches = []

        def executemany(self, sql, rows):
            self.batches.append((sql, rows))

    conn = Plain()
    assert insert_arrow_table(conn, "t", _arrow_table(2500)) == 2500
    assert [len(rows) for _, rows in conn.batches] == [1000, 1000, 500]
    assert conn.batches[0][1][0][:2] == (0, None)


def test_vortex_extension_fallback_uses_arrow_insert(monkeypatch, tmp_path):
    table = _arrow_table(30)

    class FakeArray:
        def to_arrow(self):
            return table

    fake_vortex = types.SimpleNamespace(io=types.SimpleNamespace(read=lambda path: FakeArray()))

    monkeypatch.setitem(__import__("sys").modules, "vortex", fake_vortex)

    inserted = []

    def fake_insert(connection, name, arrow_table):
        inserted.append((name, arrow_table))
        return arrow_table.num_rows

    monkeypatch.setattr(data_loading, "insert_arrow_table", fake_insert)
    conn = MagicMock()
    conn.execute.side_effect = RuntimeError("extension unavailable")

    handler = DuckDBVortexHandler(MagicMock(dry_run_mode=False))
    assert handler.load_table("t", tmp_path / "t.vortex", conn, MagicMock(), MagicMock()) == 30
    assert [name for name, _ in inserted] == ["t"]


def test_vortex_read_failure_falls_back_to_python_bindings(monkeypatch, tmp_path):
    table = _arrow_table(30)

    class FakeArray:
        def to_arrow(self):
            return table

    fake_vortex = types.SimpleNamespace(io=types.SimpleNamespace(read=lambda path: FakeArray()))
    monkeypatch.setitem(__import__("sys").modules, "vortex", fake_vortex)

    inserted = []

    def fake_insert(connection, name, arrow_table):
        inserted.append(name)
        return arrow_table.num_rows

    monkeypatch.setattr(data_loading, "insert_arrow_table", fake_insert)

    def execute(sql, *args):
        if "read_vortex" in sql:
            raise RuntimeError("cannot read file")
        result = MagicMock()
        result.fetchone.return_value = (0,)
        return result

    conn = MagicMock()
    conn.execute.side_effect = execute

    handler = DuckDBVortexHandler(MagicMock(dry_run_mode=False))
    assert handler.load_table("t", tmp_path / "t.vortex", conn, MagicMock(), MagicMock()) == 30
    assert inserted == ["t"]


@pytest.mark.parametrize(
    "handler_cls, patch_target",
    [
        (DeltaFileHandler, "delta"),
        (IcebergFileHandler, "iceberg"),
        (DuckLakeFileHandler, "ducklake"),
    ],
)
def test_generic_handlers_route_through_arrow_insert(monkeypatch, tmp_path, handler_cls, patch_target):
    calls = []

    def fake_insert(connection, table, arrow_table):
        calls.append((table, arrow_table.num_rows))
        return arrow_table.num_rows

    monkeypatch.setattr(data_loading, "insert_arrow_table", fake_insert)
    table = _arrow_table(10)
    path = tmp_path / "t"
    path.mkdir()

    if patch_target == "delta":
        fake = MagicMock()
        fake.DeltaTable.return_value.to_pyarrow_table.return_value = table
        monkeypatch.setitem(__import__("sys").modules, "deltalake", fake)
    elif patch_target == "iceberg":
        (path / "metadata").mkdir()
        (path / "metadata" / "v1.metadata.json").write_text("{}")
        fake_sql = MagicMock()
        catalog = fake_sql.SqlCatalog.return_value
        catalog.load_table.return_value.scan.return_value.to_arrow.return_value = table
        monkeypatch.setitem(__import__("sys").modules, "pyiceberg", MagicMock())
        monkeypatch.setitem(__import__("sys").modules, "pyiceberg.catalog", MagicMock())
        monkeypatch.setitem(__import__("sys").modules, "pyiceberg.catalog.sql", fake_sql)
    else:
        (path / "metadata.ducklake").write_text("")
        fake_duck = MagicMock()
        fake_duck.__version__ = "2.0.0"
        fake_duck.connect.return_value.sql.return_value.to_arrow_table.return_value = table
        monkeypatch.setitem(__import__("sys").modules, "duckdb", fake_duck)

    assert handler_cls().load_table("t", path, MagicMock(), MagicMock(), MagicMock()) == 10
    assert calls == [("t", 10)]


def _factory(path: Path):
    return duckdb_module._duckdb_handler_factory(path, MagicMock(), MagicMock(), table_name="t")


def test_factory_returns_native_handler_for_each_format(tmp_path):
    ducklake = tmp_path / "ducklake"
    ducklake.mkdir()
    (ducklake / "metadata.ducklake").write_text("")
    assert isinstance(_factory(ducklake), DuckDBDuckLakeHandler)

    delta = tmp_path / "delta"
    (delta / "_delta_log").mkdir(parents=True)
    assert isinstance(_factory(delta), DuckDBDeltaHandler)

    iceberg = tmp_path / "iceberg"
    (iceberg / "metadata").mkdir(parents=True)
    assert isinstance(_factory(iceberg), IcebergFileHandler)

    vortex = tmp_path / "t.vortex"
    vortex.write_text("")
    assert isinstance(_factory(vortex), DuckDBVortexHandler)

    parquet = tmp_path / "t.parquet"
    parquet.write_text("")
    assert isinstance(_factory(parquet), DuckDBParquetHandler)

    csv_file = tmp_path / "t.csv"
    csv_file.write_text("1,a\n")
    assert isinstance(_factory(csv_file), DuckDBNativeHandler)

    other = tmp_path / "t.unknown"
    other.write_text("")
    assert _factory(other) is None


def test_factory_detection_matches_registry(tmp_path):
    for name, marker in (("dl", "metadata.ducklake"), ("delta", "_delta_log"), ("ice", "metadata")):
        path = tmp_path / name
        path.mkdir()
        target = path / marker
        target.write_text("") if marker.endswith(".ducklake") else target.mkdir()
    registry_types = {
        "dl": DuckLakeFileHandler,
        "delta": DeltaFileHandler,
        "ice": IcebergFileHandler,
    }
    for name, expected in registry_types.items():
        assert isinstance(data_loading.FileFormatRegistry.get_handler(tmp_path / name), expected)


def test_duckdb_vortex_handler_returns_rows_added_not_table_total(tmp_path):
    conn = MagicMock()
    conn.execute.return_value.fetchone.side_effect = [(5,), (8,)]
    handler = DuckDBVortexHandler(MagicMock(dry_run_mode=False))

    assert handler.load_table("t", tmp_path / "t.vortex", conn, MagicMock(), MagicMock()) == 3


@pytest.mark.parametrize("marker", ["metadata.ducklake", "metadata"])
def test_data_loader_routes_ducklake_and_iceberg_dirs_to_factory(tmp_path, marker):
    table_dir = tmp_path / "t"
    table_dir.mkdir()
    if marker == "metadata":
        (table_dir / marker).mkdir()
    else:
        (table_dir / marker).write_text("")
    handler = MagicMock()
    handler.load_table.return_value = 7
    handler.load_table_bulk.return_value = 7
    loader = data_loading.DataLoader(
        adapter=MagicMock(),
        benchmark=MagicMock(),
        connection=MagicMock(),
        data_dir=tmp_path,
        handler_factory=lambda *args: handler,
    )

    assert loader._load_single_file("t", table_dir) == 7
    assert loader._load_sharded_table("t", [table_dir]) == 7


_ICEBERG_SCHEMA = {
    "columns": [
        {"name": "id", "type": "INTEGER"},
        {"name": "name", "type": "VARCHAR(20)"},
    ]
}


def _write_real_iceberg_table(root: Path, table_name: str, rows: int) -> Path:
    pytest.importorskip("pyiceberg.catalog.sql")
    from benchbox.utils.format_converters.base import ConversionOptions
    from benchbox.utils.format_converters.iceberg_converter import IcebergConverter

    source = root / f"{table_name}.tbl"
    source.write_text("".join(f"{i}|name-{i}|\n" for i in range(rows)))
    output_dir = root / "iceberg"
    output_dir.mkdir(exist_ok=True)
    IcebergConverter().convert([source], table_name, _ICEBERG_SCHEMA, ConversionOptions(output_dir=output_dir))
    return output_dir / table_name


def test_iceberg_handler_registers_real_converter_output(tmp_path):
    table_dir = _write_real_iceberg_table(tmp_path, "widgets", 25)
    conn = duckdb.connect()
    conn.execute("CREATE TABLE widgets (id INTEGER, name VARCHAR)")

    loaded = IcebergFileHandler().load_table("widgets", table_dir, conn, MagicMock(), MagicMock())

    assert loaded == 25
    assert conn.execute("SELECT COUNT(*), MIN(id), MAX(id) FROM widgets").fetchone() == (25, 0, 24)


class _Adapter:
    dry_run_mode = False
    platform_name = "DuckDB"
    table_mode = "native"
    platform_config: dict = {}
    requested_table_format = None
    verbose_enabled = False
    logger = MagicMock()

    def log_very_verbose(self, *args, **kwargs):
        pass


def _loader(connection, handler_factory=None):
    return data_loading.DataLoader(_Adapter(), MagicMock(), connection, Path("."), handler_factory=handler_factory)


def test_loader_raises_when_iceberg_handler_fails(tmp_path):
    table_dir = _write_real_iceberg_table(tmp_path, "widgets", 5)
    conn = duckdb.connect()

    with pytest.raises(data_loading.DataLoadingError, match="widgets"):
        _loader(conn)._load_single_file("widgets", table_dir)


def test_loader_loads_real_iceberg_directory(tmp_path):
    table_dir = _write_real_iceberg_table(tmp_path, "widgets", 7)
    conn = duckdb.connect()
    conn.execute("CREATE TABLE widgets (id INTEGER, name VARCHAR)")

    assert _loader(conn)._load_single_file("widgets", table_dir) == 7
    assert _loader(conn)._load_sharded_table("widgets", [table_dir]) == 7


class _ZeroRowHandler(data_loading.FileFormatHandler):
    def get_delimiter(self) -> str:
        return "|"

    def load_table(self, table_name, file_path, connection, benchmark, logger) -> int:
        return 0


def test_loader_raises_when_handler_loads_zero_rows_from_non_empty_source(tmp_path):
    source = tmp_path / "widgets.tbl"
    source.write_text("1|a|\n2|b|\n")
    loader = _loader(MagicMock(), handler_factory=lambda *args: _ZeroRowHandler())

    with pytest.raises(data_loading.DataLoadingError, match="loaded 0 rows but the source has 1"):
        loader._load_single_file("widgets", source)
    with pytest.raises(data_loading.DataLoadingError, match="loaded 0 rows but the source has 1"):
        loader._load_sharded_table("widgets", [source])


def test_loader_allows_zero_rows_from_empty_source(tmp_path):
    source = tmp_path / "widgets.tbl"
    source.write_text("")
    loader = _loader(MagicMock(), handler_factory=lambda *args: _ZeroRowHandler())

    assert loader._load_single_file("widgets", source) == 0


def test_loader_allows_zero_rows_from_header_only_csv(tmp_path):
    source = tmp_path / "widgets.csv"
    source.write_text("id,name\n")
    data_source = data_loading.DataSource(
        source_type="legacy_mapping", tables={"widgets": source}, table_metadata={"widgets": {"csv_has_header": True}}
    )
    loader = _loader(MagicMock(), handler_factory=lambda *args: _ZeroRowHandler())

    assert loader._load_single_file("widgets", source, data_source) == 0
    assert loader._load_sharded_table("widgets", [source], data_source) == 0


def test_loader_raises_when_header_csv_has_rows_but_none_load(tmp_path):
    source = tmp_path / "widgets.csv"
    source.write_text("id,name\n1,a\n")
    data_source = data_loading.DataSource(
        source_type="legacy_mapping", tables={"widgets": source}, table_metadata={"widgets": {"csv_has_header": True}}
    )
    loader = _loader(MagicMock(), handler_factory=lambda *args: _ZeroRowHandler())

    with pytest.raises(data_loading.DataLoadingError, match="loaded 0 rows but the source has 1"):
        loader._load_single_file("widgets", source, data_source)


def test_loader_skips_row_count_check_in_dry_run(tmp_path):
    class _PlaceholderHandler(_ZeroRowHandler):
        def load_table(self, table_name, file_path, connection, benchmark, logger) -> int:
            return 1000

    source = tmp_path / "widgets.tbl"
    source.write_text("1|a|\n2|b|\n")
    loader = _loader(MagicMock(), handler_factory=lambda *args: _PlaceholderHandler())

    with pytest.raises(data_loading.DataLoadingError):
        loader._load_single_file("widgets", tmp_path / "missing.tbl")
    loader.adapter.dry_run_mode = True
    assert loader._load_single_file("widgets", source) == 1000
    assert loader._load_sharded_table("widgets", [source]) == 1000


def test_loader_raises_on_decompression_or_file_errors(tmp_path):
    class _MissingToolHandler(_ZeroRowHandler):
        def load_table(self, table_name, file_path, connection, benchmark, logger) -> int:
            raise FileNotFoundError("zstd")

    source = tmp_path / "widgets.tbl"
    source.write_text("1|a|\n")
    loader = _loader(MagicMock(), handler_factory=lambda *args: _MissingToolHandler())

    with pytest.raises(data_loading.DataLoadingError, match="widgets"):
        loader._load_single_file("widgets", source)
