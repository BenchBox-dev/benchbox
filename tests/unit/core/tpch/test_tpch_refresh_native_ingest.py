from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from benchbox.core.contracts.platform_capabilities import resolve_refresh_ingester
from benchbox.core.tpch import maintenance_test, power_test, throughput_test
from benchbox.core.tpch.maintenance_test import TPCHMaintenanceTest
from benchbox.core.tpch.power_test import TPCHPowerTest
from benchbox.core.tpch.refresh import TPCHRefreshUpdateSet, apply_rf1
from benchbox.core.tpch.schema import LINEITEM, ORDERS
from benchbox.core.tpch.throughput_test import TPCHThroughputTest

pytestmark = [pytest.mark.unit, pytest.mark.fast, pytest.mark.tpch]


def _row(table, key: int) -> str:
    values = []
    for column in table.columns:
        if column.name in {"O_ORDERKEY", "L_ORDERKEY"}:
            values.append(str(key))
        elif column.data_type.name in {"INTEGER", "DECIMAL"}:
            values.append("1")
        elif column.data_type.name == "DATE":
            values.append("1995-01-01")
        else:
            values.append("x")
    return "|".join(values) + "|\n"


def _update_set(tmp_path: Path) -> TPCHRefreshUpdateSet:
    update_set = TPCHRefreshUpdateSet(
        set_id=1,
        orders_path=tmp_path / "orders.tbl.u1",
        lineitem_path=tmp_path / "lineitem.tbl.u1",
        delete_path=tmp_path / "delete.1",
    )
    update_set.orders_path.write_text(_row(ORDERS, 7), encoding="utf-8")
    update_set.lineitem_path.write_text(_row(LINEITEM, 7), encoding="utf-8")
    update_set.delete_path.write_text("7|\n", encoding="utf-8")
    return update_set


class _Events:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, watched: set[Path], modules: list) -> None:
        self.log: list[str] = []
        original_open = Path.open
        events = self.log

        def recording_open(path: Path, *args, **kwargs):
            if path in watched:
                events.append("read")
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr(Path, "open", recording_open)
        for module in modules:
            monkeypatch.setattr(module, "mono_time", lambda: events.append("timer_mark") or 1.0)
            monkeypatch.setattr(module, "elapsed_seconds", lambda _start: events.append("timer_stop") or 0.5)
            monkeypatch.setattr(module, "check_rf1_applied", lambda *_args: events.append("check"))


class _NativeAdapter:
    supports_native_refresh_ingest = True

    def __init__(self, log: list[str]) -> None:
        self.log = log

    def ingest_refresh_file(self, connection, table_name, columns, path):
        with path.open(encoding="utf-8") as source:
            fields = source.read().rstrip("\n").split("|")
        self.log.append("parse")
        connection.execute(f"INSERT INTO {table_name} NATIVE {len(fields)}")
        return 1


class _Connection:
    def __init__(self, log: list[str], adapter: object | None = None) -> None:
        self.log = log
        self.platform_adapter = adapter if adapter is not None else MagicMock()

    def execute(self, statement: str):
        self.log.append("BEGIN" if statement == "BEGIN" else "insert")
        return self

    def commit(self) -> None:
        self.log.append("COMMIT")

    def rollback(self) -> None:
        self.log.append("ROLLBACK")

    def close(self) -> None:
        return None


def _run_site(site: str, connection: _Connection, update_set: TPCHRefreshUpdateSet) -> None:
    if site == "power":
        evidence = TPCHPowerTest(
            benchmark=object(),
            connection=connection,
            refresh_update_set=update_set,
            refresh_connection_factory=lambda: connection,
        )._execute_refresh_function("RF1", connection)
        assert evidence["status"] == "SUCCESS", evidence
    elif site == "throughput":
        evidence = TPCHThroughputTest(
            benchmark=object(),
            connection_factory=lambda: connection,
            refresh_enabled=True,
            refresh_connection_factory=lambda: connection,
        )._execute_refresh_function("RF1", update_set, connection)
        assert evidence["status"] == "SUCCESS", evidence
    else:
        operation = TPCHMaintenanceTest(connection_factory=lambda: connection)._execute_rf1(0, update_set)
        assert operation.success, operation.error


_SITE_MODULES = {"power": power_test, "throughput": throughput_test, "maintenance": maintenance_test}


@pytest.mark.parametrize("native", [True, False], ids=["native", "values"])
@pytest.mark.parametrize("site", ["power", "throughput", "maintenance"])
def test_rf1_reads_parses_and_transacts_between_timer_start_and_stop(
    site: str, native: bool, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    update_set = _update_set(tmp_path)
    events = _Events(monkeypatch, {update_set.orders_path, update_set.lineitem_path}, [_SITE_MODULES[site]])
    adapter = _NativeAdapter(events.log) if native else None
    connection = _Connection(events.log, adapter)

    _run_site(site, connection, update_set)

    log = events.log
    marks = [index for index, event in enumerate(log) if event == "timer_mark"]
    start = marks[0]
    stop = log.index("timer_stop") if "timer_stop" in log else marks[1]
    assert start < log.index("BEGIN") < log.index("read") < log.index("insert") < log.index("COMMIT") < stop
    assert log.count("read") == 2
    assert all(start < index < stop for index, event in enumerate(log) if event in {"read", "parse"})
    assert ("parse" in log) is native
    assert log.count("check") == (0 if site == "throughput" else 1)
    if site != "throughput":
        assert log.index("check") > stop


def test_resolver_requires_per_class_declaration() -> None:
    from benchbox.platforms.cedardb import CedarDBAdapter
    from benchbox.platforms.duckdb import DuckDBAdapter
    from benchbox.platforms.ducklake import DuckLakeAdapter
    from benchbox.platforms.postgresql import PostgreSQLAdapter
    from benchbox.platforms.singlestore import SingleStoreAdapter
    from benchbox.platforms.sqlite import SQLiteAdapter

    def resolves(adapter_class: type) -> bool:
        return resolve_refresh_ingester(adapter_class.__new__(adapter_class)) is not None

    assert resolves(DuckDBAdapter)
    assert resolves(PostgreSQLAdapter)
    assert resolves(SingleStoreAdapter)
    assert not resolves(DuckLakeAdapter)
    assert not resolves(SQLiteAdapter)
    assert not resolves(CedarDBAdapter)
    assert resolve_refresh_ingester(MagicMock()) is None
    assert resolve_refresh_ingester(SimpleNamespace(supports_native_refresh_ingest=True)) is None
    assert resolve_refresh_ingester(None) is None


def test_adapter_without_capability_keeps_values_statements(tmp_path: Path) -> None:
    update_set = _update_set(tmp_path)
    connection = MagicMock()

    assert apply_rf1(connection, update_set) == 2

    statements = [call.args[0] for call in connection.execute.call_args_list]
    assert statements[0] == "BEGIN"
    assert statements[1].startswith("INSERT INTO orders (") and " VALUES (" in statements[1]
    assert statements[2].startswith("INSERT INTO lineitem (") and " VALUES (" in statements[2]
    connection.commit.assert_called_once_with()


class _LoadDataCursor:
    def __init__(self, log: list[str], statements: list[str], rowcount: int) -> None:
        self.log = log
        self.statements = statements
        self.rowcount = rowcount

    def execute(self, statement: str) -> None:
        self.statements.append(statement)
        self.log.append("load")

    def close(self) -> None:
        return None


class _SingleStoreConnection(_Connection):
    def __init__(self, log: list[str], adapter: object, rowcount: int = 1) -> None:
        super().__init__(log, adapter)
        self.statements: list[str] = []
        self.rowcount = rowcount

    def cursor(self) -> _LoadDataCursor:
        return _LoadDataCursor(self.log, self.statements, self.rowcount)


def _singlestore_adapter():
    from benchbox.platforms.singlestore import SingleStoreAdapter

    return SingleStoreAdapter.__new__(SingleStoreAdapter)


@pytest.mark.parametrize("site", ["power", "throughput", "maintenance"])
def test_singlestore_rf1_loads_inside_the_timer_without_pre_timer_reads(
    site: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    update_set = _update_set(tmp_path)
    events = _Events(monkeypatch, {update_set.orders_path, update_set.lineitem_path}, [_SITE_MODULES[site]])
    connection = _SingleStoreConnection(events.log, _singlestore_adapter())

    _run_site(site, connection, update_set)

    log = events.log
    marks = [index for index, event in enumerate(log) if event == "timer_mark"]
    start = marks[0]
    stop = log.index("timer_stop") if "timer_stop" in log else marks[1]
    assert start < log.index("BEGIN") < log.index("load") < log.index("COMMIT") < stop
    assert log.count("load") == 2
    assert "read" not in log
    assert "parse" not in log
    assert log.count("COMMIT") == 1


def test_singlestore_declares_and_resolves_native_ingest(tmp_path: Path) -> None:
    from benchbox.platforms.singlestore import SingleStoreAdapter

    adapter = _singlestore_adapter()
    assert SingleStoreAdapter.__dict__["supports_native_refresh_ingest"] is True
    assert resolve_refresh_ingester(adapter) is adapter

    connection = _SingleStoreConnection([], adapter, rowcount=1)
    assert apply_rf1(connection, _update_set(tmp_path)) == 2

    orders_statement, lineitem_statement = connection.statements
    assert orders_statement.startswith("LOAD DATA LOCAL INFILE '")
    assert "INTO TABLE `orders`" in orders_statement
    assert "INTO TABLE `lineitem`" in lineitem_statement
    for statement in connection.statements:
        assert "FIELDS TERMINATED BY '|' LINES TERMINATED BY '\\n'" in statement
        assert " SET " in statement and "REGEXP" in statement and "1e30" in statement
        assert "IGNORE" not in statement.upper() and "SKIP" not in statement.upper()
    assert "(`o_orderkey`" in orders_statement.lower()
    assert "@o_totalprice" in orders_statement.lower()
    assert "`o_totalprice` = if(@o_totalprice regexp" in orders_statement.lower()


def test_singlestore_native_ingest_rejects_unreported_row_count(tmp_path: Path) -> None:
    connection = _SingleStoreConnection([], _singlestore_adapter(), rowcount=-1)

    with pytest.raises(RuntimeError, match="did not report"):
        apply_rf1(connection, _update_set(tmp_path))

    assert connection.log == ["BEGIN", "load", "ROLLBACK"]


def test_native_rows_failure_rolls_back_and_raises(tmp_path: Path) -> None:
    update_set = _update_set(tmp_path)
    log: list[str] = []
    adapter = _NativeAdapter(log)

    def fail(*_args):
        raise RuntimeError("bad row")

    adapter.ingest_refresh_file = fail
    connection = _Connection(log, adapter)

    with pytest.raises(RuntimeError, match="bad row"):
        apply_rf1(connection, update_set)

    assert log == ["BEGIN", "ROLLBACK"]


def test_production_refresh_connections_carry_the_platform_adapter() -> None:
    from benchbox.platforms.base.connection_wrappers import PlatformAdapterConnection
    from benchbox.platforms.base.execution import TestDriversMixin

    class Host(TestDriversMixin):
        def get_target_dialect(self) -> str:
            return "duckdb"

        def new_stream_connection(self, connection, *, benchmark_type=None):
            return object()

    host = Host()
    wrapper = host._make_tpch_refresh_connection_adapter(object(), 1.0)

    assert isinstance(wrapper, PlatformAdapterConnection)
    assert wrapper.platform_adapter is host


