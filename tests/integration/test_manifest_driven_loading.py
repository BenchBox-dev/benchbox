from __future__ import annotations

import json
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


_TABLE = "lineitem"
_PIPE_DATA = "1|alpha\n2|beta\n"


@pytest.fixture
def manifest_data_dir(tmp_path: Path) -> Path:
    data_file = tmp_path / f"{_TABLE}.csv"
    data_file.write_text(_PIPE_DATA, encoding="utf-8")

    manifest = {
        "version": 2,
        "benchmark": "test",
        "tables": {
            _TABLE: {
                "formats": {
                    "csv": [
                        {
                            "path": f"{_TABLE}.csv",
                            "size_bytes": len(_PIPE_DATA),
                            "row_count": 2,
                            "metadata": {
                                "csv_delimiter": "|",
                                "csv_null_marker": "",
                            },
                        }
                    ]
                }
            }
        },
    }
    (tmp_path / "_datagen_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return tmp_path


@dataclass
class _Benchmark:
    tables: dict[str, Any] = field(default_factory=dict)
    name: str = "test"

    def get_create_tables_sql(self, **_: Any) -> str:
        return f"CREATE TABLE {_TABLE} (id INT, value VARCHAR(100))"


class _S2Cursor:
    def __init__(self, statements: list[str]) -> None:
        self._stmts = statements
        self._result: tuple | None = None

    def execute(self, sql: str) -> None:
        self._stmts.append(sql)
        self._result = (0,) if "count(*)" in sql.lower() else None

    def fetchone(self) -> tuple | None:
        return self._result

    def close(self) -> None:
        pass


class _S2Conn:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def cursor(self) -> _S2Cursor:
        return _S2Cursor(self.statements)


def _stub_singlestoredb(monkeypatch: Any) -> None:
    fake = types.ModuleType("singlestoredb")
    monkeypatch.setitem(sys.modules, "singlestoredb", fake)
    try:
        import benchbox.platforms.singlestore as m

        m._s2 = fake
    except ImportError:
        pass


@pytest.mark.platform_smoke
def test_singlestore_uses_manifest_delimiter(monkeypatch: Any, manifest_data_dir: Path) -> None:
    _stub_singlestoredb(monkeypatch)

    from benchbox.platforms.singlestore import SingleStoreAdapter

    adapter = SingleStoreAdapter(host="localhost", database="benchbox")
    benchmark = _Benchmark(tables={_TABLE: manifest_data_dir / f"{_TABLE}.csv"})
    conn = _S2Conn()

    adapter.load_data(benchmark, conn, manifest_data_dir)

    load_sql = next((s for s in conn.statements if "LOAD DATA" in s.upper()), None)
    assert load_sql is not None, f"No LOAD DATA statement executed; got: {conn.statements}"
    assert "FIELDS TERMINATED BY '|'" in load_sql, load_sql
    assert "NULL DEFINED BY ''" in load_sql, load_sql


class _PGCopyCtx:
    def __init__(self, sql: str, copies: list[str]) -> None:
        self._sql = sql
        self._copies = copies

    def __enter__(self) -> _PGCopyCtx:
        self._copies.append(self._sql)
        return self

    def __exit__(self, *_: Any) -> None:
        pass

    def write(self, chunk: Any) -> None:
        pass


class _PGCursor:
    def __init__(self, copies: list[str]) -> None:
        self._copies = copies
        self._result: tuple | None = None

    def copy(self, sql: str) -> _PGCopyCtx:
        return _PGCopyCtx(sql, self._copies)

    def execute(self, sql: str, params: Any = None) -> None:
        self._result = (2,) if "count(*)" in sql.lower() else None

    def fetchone(self) -> tuple | None:
        return self._result

    def close(self) -> None:
        pass


class _PGConn:
    def __init__(self) -> None:
        self.copies: list[str] = []
        self.autocommit = False
        self.closed = 0

    def cursor(self, cursor_factory: Any = None) -> _PGCursor:
        return _PGCursor(self.copies)

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        self.closed = 1


def _stub_psycopg(monkeypatch: Any) -> None:
    fake = types.ModuleType("psycopg")
    monkeypatch.setitem(sys.modules, "psycopg", fake)
    try:
        import benchbox.platforms.postgresql as m

        m.psycopg = fake
    except ImportError:
        pass


@pytest.mark.platform_smoke
def test_postgresql_uses_manifest_delimiter(monkeypatch: Any, manifest_data_dir: Path) -> None:
    _stub_psycopg(monkeypatch)

    from benchbox.platforms.postgresql import PostgreSQLAdapter

    adapter = PostgreSQLAdapter(host="localhost", username="postgres")
    benchmark = _Benchmark(tables={_TABLE: manifest_data_dir / f"{_TABLE}.csv"})
    conn = _PGConn()

    adapter.load_data(benchmark, conn, manifest_data_dir)

    copy_sql = next((s for s in conn.copies if "COPY" in s.upper()), None)
    assert copy_sql is not None, f"No COPY statement executed; copies: {conn.copies}"
    assert "DELIMITER '|'" in copy_sql, copy_sql
    assert "FORMAT text" in copy_sql, copy_sql


@pytest.mark.platform_smoke
def test_doris_uses_manifest_delimiter(monkeypatch: Any, manifest_data_dir: Path) -> None:
    from tests.integration.platforms.common import install_doris_stub

    state = install_doris_stub(monkeypatch)

    import benchbox.platforms.doris as doris_mod

    doris_mod._requests = None

    from benchbox.platforms.doris import DorisAdapter

    adapter = DorisAdapter(host="localhost")
    benchmark = _Benchmark(tables={_TABLE: manifest_data_dir / f"{_TABLE}.csv"})

    import pymysql

    conn = pymysql.connect()

    adapter.load_data(benchmark, conn, manifest_data_dir)

    assert state.inserts, "No INSERT statements executed"
    insert_sql, rows = state.inserts[0]
    assert "VALUES (%s, %s)" in insert_sql, f"Expected 2-column INSERT from pipe split; got: {insert_sql!r}"
    assert rows[0] == ["1", "alpha"], f"Unexpected row values: {rows[0]!r}"


class _FakeResponse:
    text = "| Rows imported | 2 |"
    status_code = 200

    def raise_for_status(self) -> None:
        pass


class _FakeRequests:
    def __init__(self) -> None:
        self.post_calls: list[dict[str, Any]] = []

    def post(self, url: str, *, params: Any = None, files: Any = None, timeout: Any = None) -> _FakeResponse:
        self.post_calls.append({"url": url, "params": params, "files": files})
        return _FakeResponse()

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        return _FakeResponse()


def _stub_psycopg_for_questdb(monkeypatch: Any) -> None:
    fake = types.ModuleType("psycopg")
    monkeypatch.setitem(sys.modules, "psycopg", fake)
    try:
        import benchbox.platforms.questdb as m

        m.psycopg = fake
    except ImportError:
        pass


@pytest.mark.platform_smoke
def test_questdb_uses_manifest_delimiter(monkeypatch: Any, manifest_data_dir: Path) -> None:
    _stub_psycopg_for_questdb(monkeypatch)

    fake_requests = _FakeRequests()
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    from benchbox.platforms.questdb import QuestDBAdapter

    adapter = QuestDBAdapter(host="localhost", loading_method="rest")
    benchmark = _Benchmark(tables={_TABLE: manifest_data_dir / f"{_TABLE}.csv"})

    adapter.load_data(benchmark, None, manifest_data_dir)

    assert fake_requests.post_calls, "No requests.post() call made"
    params = fake_requests.post_calls[0]["params"]
    assert params is not None, "requests.post() called without params"
    assert params.get("delimiter") == "|", f"Expected delimiter='|' from manifest; got: {params.get('delimiter')!r}"


def _assert_manifest_null_markers(tmp_path: Path, tables: dict[str, Path], benchmark: Any) -> None:
    from benchbox.platforms.base.data_loading import DataSourceResolver, resolve_csv_dialect

    resolver = DataSourceResolver()
    source = resolver.resolve(benchmark, tmp_path)
    assert source is not None, "DataSourceResolver returned None"
    assert source.table_metadata, f"No manifest metadata resolved; tables: {list(source.tables)}"
    for table, path in tables.items():
        dialect = resolve_csv_dialect(source, table, Path(path), benchmark)
        assert dialect.null_marker == "", (
            f"table {table!r}: expected null_marker == '' for SingleStore LOAD DATA, got {dialect.null_marker!r}"
        )


def test_clickbench_generator_manifest_null_marker(tmp_path: Path) -> None:
    from benchbox.core.clickbench.generator import ClickBenchDataGenerator
    from benchbox.platforms.base.data_loading import DataSourceResolver, resolve_csv_dialect

    generated = ClickBenchDataGenerator(scale_factor=0.00001, output_dir=tmp_path).generate_data()
    assert "hits" in generated
    tables = {name: Path(path) for name, path in generated.items()}
    benchmark = _Benchmark(tables=dict(tables))
    source = DataSourceResolver().resolve(benchmark, tmp_path)
    assert source is not None, "DataSourceResolver returned None"
    for table, path in tables.items():
        dialect = resolve_csv_dialect(source, table, Path(path), benchmark)
        assert dialect.null_marker == "__NULL__", (
            f"table {table!r}: expected null_marker '__NULL__' to preserve empty strings, got {dialect.null_marker!r}"
        )


def test_amplab_generator_manifest_null_marker(tmp_path: Path) -> None:
    from benchbox.core.amplab.generator import AMPLabDataGenerator

    generated = AMPLabDataGenerator(scale_factor=0.0001, output_dir=tmp_path).generate_data(tables=["rankings"])
    assert "rankings" in generated
    tables = {name: Path(path) for name, path in generated.items()}
    _assert_manifest_null_markers(tmp_path, tables, _Benchmark(tables=dict(tables)))


def test_nyctaxi_manifest_null_marker(tmp_path: Path) -> None:
    from benchbox.core.nyctaxi.benchmark import NYCTaxiBenchmark

    benchmark = NYCTaxiBenchmark(scale_factor=1.0, output_dir=tmp_path)
    data_file = tmp_path / "yellow_tripdata_2019-01.csv"
    data_file.write_text("VendorID,trip_distance\n1,2.5\n", encoding="utf-8")
    benchmark.tables = {"trips": data_file}
    benchmark.downloader._table_row_counts = {"trips": 1}
    benchmark._write_manifest()
    _assert_manifest_null_markers(tmp_path, {"trips": data_file}, benchmark)
