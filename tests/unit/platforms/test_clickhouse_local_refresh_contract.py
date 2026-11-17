from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from benchbox.core.contracts.platform_capabilities import resolve_refresh_ingester
from benchbox.platforms.base.connection_wrappers import (
    StreamConnectionCapability,
    require_throughput_stream_capability,
    resolve_stream_connection_capability,
)
from benchbox.platforms.clickhouse_local import ClickHouseLocalAdapter, _refresh_column_type

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class _Recorder:
    def __init__(self, counts: list[int]) -> None:
        self.statements: list[str] = []
        self._counts = iter(counts)
        self._last = ""

    def execute(self, statement: str):
        self.statements.append(statement)
        self._last = statement
        return self

    def fetchone(self):
        return (next(self._counts),) if self._last.startswith("SELECT count()") else None


@pytest.fixture()
def adapter() -> ClickHouseLocalAdapter:
    return ClickHouseLocalAdapter()


def test_manifest_declares_independent_streams_for_clickhouse_local_only() -> None:
    capability, declared = resolve_stream_connection_capability(ClickHouseLocalAdapter)

    assert declared is True
    assert capability is StreamConnectionCapability.INDEPENDENT_CONNECTION


def test_throughput_gate_passes_with_a_persistent_database(adapter: ClickHouseLocalAdapter) -> None:
    connection = SimpleNamespace(db_path="/tmp/run.chdb")

    assert (
        require_throughput_stream_capability(adapter, connection=connection)
        is StreamConnectionCapability.INDEPENDENT_CONNECTION
    )


def test_streams_are_refused_for_an_in_memory_database(adapter: ClickHouseLocalAdapter) -> None:
    connection = SimpleNamespace(db_path=None)

    with pytest.raises(RuntimeError, match="persistent database path"):
        adapter.ensure_stream_sessions_supported(connection)
    with pytest.raises(RuntimeError, match="persistent database path"):
        adapter.new_stream_connection(connection, benchmark_type="tpch")
    with pytest.raises(RuntimeError, match="persistent database path"):
        require_throughput_stream_capability(adapter, connection=connection)


def test_new_stream_connection_opens_a_session_on_the_same_path_and_applies_settings(
    adapter: ClickHouseLocalAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[Any] = []

    class FakeClient:
        def __init__(self, db_path: str | None = None) -> None:
            self.db_path = db_path
            self.statements: list[str] = []
            self.closed = False
            opened.append(self)

        def execute(self, statement: str):
            self.statements.append(statement)
            return []

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr("benchbox.platforms.clickhouse_local.ClickHouseLocalClient", FakeClient)
    configured: list[tuple[Any, str]] = []
    monkeypatch.setattr(adapter, "configure_for_benchmark", lambda stream, kind: configured.append((stream, kind)))

    stream = adapter.new_stream_connection(SimpleNamespace(db_path="/tmp/run.chdb"), benchmark_type="tpcds")

    assert stream is opened[0]
    assert stream.db_path == "/tmp/run.chdb"
    assert configured == [(stream, "tpcds")]
    assert stream.statements == [
        "SET join_use_nulls = 1",
        "SET data_type_default_nullable = 1",
        "SET mutations_sync = 2",
        "SET lightweight_deletes_sync = 2",
        f"SET max_memory_usage = {3 * 1024**3}",
        f"SET max_bytes_before_external_group_by = {3 * 1024**3 // 2}",
        f"SET max_bytes_before_external_sort = {3 * 1024**3 // 2}",
    ]


def test_stream_memory_budget_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        def __init__(self, db_path=None) -> None:
            self.statements: list[str] = []

        def execute(self, statement: str):
            self.statements.append(statement)

        def close(self) -> None:
            pass

    monkeypatch.setattr("benchbox.platforms.clickhouse_local.ClickHouseLocalClient", FakeClient)
    adapter = ClickHouseLocalAdapter(stream_max_memory_usage="2GB")
    monkeypatch.setattr(adapter, "configure_for_benchmark", lambda stream, kind: None)

    stream = adapter.new_stream_connection(SimpleNamespace(db_path="/tmp/r.chdb"))

    assert f"SET max_memory_usage = {2 * 1024**3}" in stream.statements


def test_new_stream_connection_closes_the_session_when_a_setting_fails(
    adapter: ClickHouseLocalAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    created: list[Any] = []

    class FailingClient:
        def __init__(self, db_path: str | None = None) -> None:
            self.closed = False
            created.append(self)

        def execute(self, statement: str):
            raise RuntimeError("unknown setting")

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr("benchbox.platforms.clickhouse_local.ClickHouseLocalClient", FailingClient)
    monkeypatch.setattr(adapter, "configure_for_benchmark", lambda stream, kind: None)

    with pytest.raises(RuntimeError, match="unknown setting"):
        adapter.new_stream_connection(SimpleNamespace(db_path="/tmp/run.chdb"))

    assert created[0].closed is True


def test_adapter_resolves_as_a_native_refresh_ingester(adapter: ClickHouseLocalAdapter) -> None:
    assert resolve_refresh_ingester(adapter) is adapter


@pytest.mark.parametrize(
    ("sql_type", "nullable", "expected"),
    [
        ("INTEGER", False, "Int64"),
        ("INTEGER", True, "Nullable(Int64)"),
        ("DATE", False, "Date"),
        ("DECIMAL(15,2)", False, "Decimal(15, 2)"),
        ("DECIMAL(7, 2)", True, "Nullable(Decimal(7, 2))"),
        ("CHAR(16)", True, "Nullable(String)"),
        ("VARCHAR", False, "String"),
    ],
)
def test_refresh_column_types_map_to_clickhouse_structure(sql_type: str, nullable: bool, expected: str) -> None:
    assert _refresh_column_type(sql_type, nullable) == expected


def test_tpch_ingest_reads_the_pipe_file_with_a_trailing_delimiter_column(
    adapter: ClickHouseLocalAdapter, tmp_path: Path
) -> None:
    path = tmp_path / "orders.tbl.u1"
    path.write_text("1|2|x|\n", encoding="utf-8")
    connection = _Recorder([10, 11])

    rows = adapter.ingest_refresh_file(
        connection, "orders", [("o_orderkey", "INTEGER"), ("o_custkey", "INTEGER"), ("o_comment", "VARCHAR")], path
    )

    assert rows == 1
    insert = connection.statements[1]
    assert insert.startswith(
        "INSERT INTO orders (o_orderkey, o_custkey, o_comment) SELECT o_orderkey, o_custkey, o_comment FROM file("
    )
    assert "o_orderkey Int64, o_custkey Int64, o_comment String, _trailing_delimiter String" in insert
    assert "format_csv_delimiter = '|'" in insert
    assert "input_format_csv_trim_whitespaces = 0" in insert
    assert "format_csv_null_representation" not in insert


def test_tpcds_ingest_maps_the_empty_marker_to_null(adapter: ClickHouseLocalAdapter, tmp_path: Path) -> None:
    path = tmp_path / "s_inventory.dat"
    path.write_text("1|2|3\n", encoding="utf-8")
    connection = _Recorder([0, 1])

    rows = adapter.ingest_refresh_file(
        connection, "s_inventory", [("a", "INTEGER"), ("b", "DATE"), ("c", "CHAR(16)")], path, null_marker=""
    )

    assert rows == 1
    insert = connection.statements[1]
    assert "a Nullable(Int64), b Nullable(Date), c Nullable(String)" in insert
    assert "_trailing_delimiter" not in insert
    assert "format_csv_null_representation = ''" in insert


def test_ingest_reports_the_rows_the_table_gained(adapter: ClickHouseLocalAdapter, tmp_path: Path) -> None:
    path = tmp_path / "x.dat"
    path.write_text("1\n", encoding="utf-8")

    assert adapter.ingest_refresh_file(_Recorder([5, 9]), "t", [("a", "INTEGER")], path, null_marker="") == 4
