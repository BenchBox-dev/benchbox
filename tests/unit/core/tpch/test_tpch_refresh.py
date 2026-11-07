from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchbox.core.tpch import maintenance_test, power_test, refresh, throughput_test
from benchbox.core.tpch.maintenance_test import TPCHMaintenanceTest
from benchbox.core.tpch.power_test import TPCHPowerTest
from benchbox.core.tpch.refresh import (
    TPCHRefreshUpdateSet,
    TPCHRF2Deletes,
    apply_rf1,
    apply_rf2,
    check_rf1_applied,
    check_rf2_applied,
)
from benchbox.core.tpch.schema import LINEITEM, ORDERS
from benchbox.core.tpch.throughput_test import TPCHThroughputTest
from benchbox.utils.clock import mono_time

pytestmark = [pytest.mark.unit, pytest.mark.fast, pytest.mark.tpch]


def _source_row(table, key: int) -> str:
    values = []
    for column in table.columns:
        if column.name.upper() in {"O_ORDERKEY", "L_ORDERKEY"}:
            values.append(str(key))
        elif column.data_type.name in {"INTEGER", "DECIMAL"}:
            values.append("1")
        elif column.data_type.name == "DATE":
            values.append("1995-01-01")
        else:
            values.append("x")
    return "|".join(values) + "|\n"


def _update_set(
    tmp_path: Path, set_id: int, *, order_keys: list[int], lineitem_keys: list[int], delete_keys: list[int]
):
    paths = {
        "orders_path": tmp_path / f"orders-{set_id}.tbl",
        "lineitem_path": tmp_path / f"lineitem-{set_id}.tbl",
        "delete_path": tmp_path / f"delete-{set_id}.tbl",
    }
    paths["orders_path"].write_text("".join(_source_row(ORDERS, key) for key in order_keys), encoding="utf-8")
    paths["lineitem_path"].write_text("".join(_source_row(LINEITEM, key) for key in lineitem_keys), encoding="utf-8")
    paths["delete_path"].write_text("".join(f"{key}|\n" for key in delete_keys), encoding="utf-8")
    return TPCHRefreshUpdateSet(set_id=set_id, **paths)


class RecordingConnection:
    def __init__(self, counts: list[int] | None = None) -> None:
        self.statements: list[str] = []
        self.counts = iter(counts or [])
        self.commits = 0
        self.rollbacks = 0

    def execute(self, statement: str):
        self.statements.append(statement)
        if statement.startswith("SELECT COUNT(*)"):
            return self
        return self

    def fetchone(self):
        count = next(self.counts, 0)
        return (count, count)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def test_rf1_uses_1000_row_insert_chunks_and_one_transaction(tmp_path: Path) -> None:
    update_set = _update_set(
        tmp_path,
        7,
        order_keys=list(range(1001, 2002)),
        lineitem_keys=list(range(1001, 2002)),
        delete_keys=[],
    )
    connection = RecordingConnection()

    inserted = apply_rf1(connection, update_set)

    inserts = [statement for statement in connection.statements if statement.startswith("INSERT INTO")]
    assert inserted == 2002
    assert len(inserts) == 4
    assert [statement.count("), (") + 1 for statement in inserts] == [1000, 1, 1000, 1]
    assert connection.statements[0] == "BEGIN"
    assert connection.commits == 1
    assert connection.rollbacks == 0


def test_rf1_check_rejects_keys_that_exist_more_than_once(tmp_path: Path) -> None:
    update_set = _update_set(tmp_path, 4, order_keys=[41], lineitem_keys=[41], delete_keys=[])
    connection = RecordingConnection([2])

    with pytest.raises(ValueError, match="refresh set 4 RF1 found 2 orders rows .*the database needs a reload"):
        check_rf1_applied(connection, update_set, 2)


def test_rf2_deletes_lineitems_before_orders_in_one_transaction(tmp_path: Path) -> None:
    update_set = _update_set(tmp_path, 12, order_keys=[], lineitem_keys=[], delete_keys=[121, 122])
    connection = RecordingConnection([3, 2])

    deleted = apply_rf2(connection, update_set)
    check_rf2_applied(connection, update_set, deleted.orders)

    assert deleted == TPCHRF2Deletes(orders=2, lineitems=3)
    assert deleted.rows == 5
    assert [statement for statement in connection.statements if statement.startswith("DELETE FROM")] == [
        "DELETE FROM lineitem WHERE L_ORDERKEY IN (121, 122)",
        "DELETE FROM orders WHERE O_ORDERKEY IN (121, 122)",
    ]
    assert "BEGIN" in connection.statements
    assert connection.commits == 1
    assert connection.rollbacks == 0


def test_rf2_uses_1000_key_delete_chunks(tmp_path: Path) -> None:
    keys = list(range(1, 1002))
    update_set = _update_set(tmp_path, 13, order_keys=[], lineitem_keys=[], delete_keys=keys)
    connection = RecordingConnection([0, 1000, 0, 1])

    deleted = apply_rf2(connection, update_set)
    check_rf2_applied(connection, update_set, deleted.orders)

    deletes = [statement for statement in connection.statements if statement.startswith("DELETE FROM")]
    assert deleted.rows == 1001
    assert len(deletes) == 4
    assert [statement.count(",") + 1 for statement in deletes] == [1000, 1000, 1, 1]
    assert connection.commits == 1
    assert connection.rollbacks == 0


def test_rf2_check_rejects_a_short_delete_count_and_names_the_reload(tmp_path: Path) -> None:
    update_set = _update_set(tmp_path, 9, order_keys=[], lineitem_keys=[], delete_keys=[91, 92])
    connection = RecordingConnection([0, 1])

    deletes = apply_rf2(connection, update_set)

    assert deletes == TPCHRF2Deletes(orders=1, lineitems=0)
    with pytest.raises(ValueError, match="refresh set 9 RF2 deleted 1 orders, expected 2; the database needs a reload"):
        check_rf2_applied(connection, update_set, deletes.orders)


def test_update_sets_are_backed_by_distinct_dbgen_output_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output_dir = tmp_path / "dbgen"
    executable = tmp_path / "bin" / "dbgen.exe"
    distributions = executable.parent / "dists.dss"
    distributions.parent.mkdir()
    executable.touch()
    distributions.write_text("test distribution", encoding="utf-8")

    def fake_generator(*, scale_factor: float, output_dir: Path, quiet: bool):
        from types import SimpleNamespace

        return SimpleNamespace(dbgen_exe=executable)

    def run(args, *, cwd, env, capture_output, text, check):
        assert args == ["dbgen", "-s", "0.01", "-U", "6", "-f"]
        assert cwd == output_dir
        assert (output_dir / executable.name).is_file()
        assert str(output_dir) in env["PATH"].split(os.pathsep)
        assert env["DSS_PATH"] == "."
        assert env["DSS_CONFIG"] == "."
        for set_id in (5, 6):
            for name in (f"orders.tbl.u{set_id}", f"lineitem.tbl.u{set_id}", f"delete.{set_id}"):
                (output_dir / name).write_text(f"{set_id}|\n", encoding="utf-8")
        return type("Completed", (), {"returncode": 0, "stderr": "", "stdout": ""})()

    monkeypatch.setattr(refresh, "TPCHDataGenerator", fake_generator)
    monkeypatch.setattr(refresh.subprocess, "run", run)
    sets = refresh.generate_refresh_update_sets(0.01, 5, 2, output_dir)

    assert list(sets) == [5, 6]
    assert [sets[set_id].set_id for set_id in (5, 6)] == [5, 6]
    assert sets[5].orders_path.name == "orders.tbl.u5"
    assert sets[5].lineitem_path.name == "lineitem.tbl.u5"
    assert sets[5].delete_path.name == "delete.5"
    assert sets[6].orders_path.name == "orders.tbl.u6"
    assert sets[6].lineitem_path.name == "lineitem.tbl.u6"
    assert sets[6].delete_path.name == "delete.6"
    assert sets[5].orders_path.read_text(encoding="utf-8") == "5|\n"
    assert sets[6].orders_path.read_text(encoding="utf-8") == "6|\n"


def test_throughput_assigns_consecutive_update_sets_after_the_power_set(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from benchbox.core.tpch import throughput_test
    from benchbox.core.tpch.throughput_test import TPCHThroughputTest

    generated = []
    executed_sets = []

    def generate(scale_factor, first_set, count, output_dir):
        generated.append((scale_factor, first_set, count))
        return {set_id: SimpleNamespace(set_id=set_id) for set_id in range(first_set, first_set + count)}

    test = TPCHThroughputTest(
        benchmark=object(),
        connection_factory=lambda: None,
        scale_factor=0.01,
        num_streams=2,
        refresh_enabled=True,
        refresh_first_set=8,
        refresh_connection_factory=lambda: None,
    )
    test._pregenerate_stream_queries = lambda config: {}

    def execute(stream_fn, config, result, logger, auxiliary_fn=None):
        auxiliary_fn()

    def execute_refresh(update_sets, num_streams, refresh_stream, cancel_event=None):
        executed_sets.extend(sorted(update_sets))
        refresh_stream.start_time = "refresh-start"
        refresh_stream.end_time = "refresh-end"
        refresh_stream.start_monotonic = 1.0
        refresh_stream.end_monotonic = 2.0
        refresh_stream.status = "COMPLETED"
        return refresh_stream

    monkeypatch.setattr(throughput_test, "generate_refresh_update_sets", generate)
    monkeypatch.setattr(throughput_test.StreamRunner, "execute", staticmethod(execute))
    monkeypatch.setattr(test, "_execute_refresh_stream", execute_refresh)
    monkeypatch.setattr(test, "_check_refresh_postconditions", lambda *_args: None)

    result = test.run()

    assert generated == [(0.01, 9, 2)]
    assert executed_sets == [9, 10]
    assert result.refresh_stream.first_set == 9


def test_throughput_reports_refresh_worker_that_never_started(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from benchbox.core.tpch import throughput_test
    from benchbox.core.tpch.throughput_test import TPCHThroughputTest

    test = TPCHThroughputTest(
        benchmark=object(),
        connection_factory=lambda: None,
        refresh_enabled=True,
        refresh_connection_factory=lambda: None,
    )
    test._pregenerate_stream_queries = lambda config: {}
    monkeypatch.setattr(
        throughput_test,
        "generate_refresh_update_sets",
        lambda scale_factor, first_set, count, output_dir: {
            set_id: SimpleNamespace(set_id=set_id) for set_id in range(first_set, first_set + count)
        },
    )
    monkeypatch.setattr(throughput_test.StreamRunner, "execute", staticmethod(lambda *_args, **_kwargs: None))

    result = test.run()

    assert result.success is False
    assert result.refresh_stream.status == "FAILED"
    assert "did not start" in result.errors[0]


class _RecordedConnection:
    def __init__(self, seeded_order_keys: list[int]) -> None:
        self.database = sqlite3.connect(":memory:", isolation_level=None)
        for table in (ORDERS, LINEITEM):
            self.database.execute(f"CREATE TABLE {table.name} ({', '.join(column.name for column in table.columns)})")
        for key in seeded_order_keys:
            self.database.execute(f"INSERT INTO orders (O_ORDERKEY) VALUES ({key})")
            self.database.execute(f"INSERT INTO lineitem (L_ORDERKEY) VALUES ({key})")
        self.events: list[tuple[str, float]] = []

    def execute(self, statement: str):
        self.events.append((statement.split(None, 1)[0].upper(), mono_time()))
        return self.database.execute(statement)

    def commit(self) -> None:
        self.events.append(("COMMIT", mono_time()))
        self.database.commit()

    def rollback(self) -> None:
        self.database.rollback()

    def close(self) -> None:
        return None

    def names(self) -> list[str]:
        return [name for name, _ in self.events]


def _record_update_set_reads(monkeypatch: pytest.MonkeyPatch, update_set: TPCHRefreshUpdateSet, connection) -> None:
    watched = {update_set.orders_path, update_set.lineitem_path, update_set.delete_path}
    original_open = Path.open

    def recording_open(path: Path, *args, **kwargs):
        if path in watched:
            connection.events.append(("READ", mono_time()))
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", recording_open)


def _refresh_site(site: str, connection: _RecordedConnection, update_set: TPCHRefreshUpdateSet, function: str):
    if site == "power":
        return TPCHPowerTest(
            benchmark=object(),
            connection=connection,
            refresh_update_set=update_set,
            refresh_connection_factory=lambda: connection,
        )._execute_refresh_function(function, connection)
    runner = TPCHMaintenanceTest(connection_factory=lambda: connection)
    return (runner._execute_rf1 if function == "RF1" else runner._execute_rf2)(0, update_set)


@pytest.mark.parametrize("function", ["RF1", "RF2"])
@pytest.mark.parametrize("site", ["power", "maintenance"])
def test_refresh_checks_run_after_the_timer_stops(
    site: str, function: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    update_set = _update_set(tmp_path, 3, order_keys=[41, 42], lineitem_keys=[41, 41, 42], delete_keys=[91, 92])
    connection = _RecordedConnection([91, 92])
    _record_update_set_reads(monkeypatch, update_set, connection)
    module = {"power": power_test, "maintenance": maintenance_test}[site]
    monkeypatch.setattr(module, "mono_time", lambda: connection.events.append(("TIMER_START", 0.0)) or 1.0)
    monkeypatch.setattr(module, "elapsed_seconds", lambda _start: connection.events.append(("TIMER_STOP", 0.0)) or 0.5)

    outcome = _refresh_site(site, connection, update_set, function)

    assert (outcome["status"] == "SUCCESS") if site == "power" else outcome.success
    names = connection.names()
    start = names.index("TIMER_START")
    stop = names.index("TIMER_STOP")
    assert start == 0
    assert "SELECT" not in names[:stop]
    assert "SELECT" in names[stop:]
    assert {"BEGIN", "COMMIT"} <= set(names[start:stop])


def test_rf1_check_fails_the_phase_for_a_duplicated_update_set_key(tmp_path: Path) -> None:
    update_set = _update_set(tmp_path, 6, order_keys=[61, 61], lineitem_keys=[61, 61], delete_keys=[])
    connection = _RecordedConnection([])

    evidence = _refresh_site("power", connection, update_set, "RF1")
    operation = _refresh_site("maintenance", _RecordedConnection([]), update_set, "RF1")

    assert evidence["status"] == "FAILED"
    assert evidence["check_status"] == "FAILED"
    assert evidence["duration_ms"] == 0
    assert "refresh set 6 RF1" in evidence["error"]
    assert "the database needs a reload" in evidence["error"]
    assert operation.success is False
    assert operation.error == evidence["error"]


def test_rf2_check_fails_the_phase_for_a_short_delete_count(tmp_path: Path) -> None:
    update_set = _update_set(tmp_path, 7, order_keys=[], lineitem_keys=[], delete_keys=[71, 72])

    evidence = _refresh_site("power", _RecordedConnection([71]), update_set, "RF2")
    operation = _refresh_site("maintenance", _RecordedConnection([71]), update_set, "RF2")

    assert evidence["status"] == "FAILED"
    assert evidence["check_status"] == "FAILED"
    assert "refresh set 7 RF2 deleted 1 orders, expected 2" in evidence["error"]
    assert "the database needs a reload" in evidence["error"]
    assert operation.success is False
    assert operation.error == evidence["error"]


def test_refresh_evidence_names_row_counts_and_check_outcome(tmp_path: Path) -> None:
    update_set = _update_set(tmp_path, 8, order_keys=[81], lineitem_keys=[81, 81], delete_keys=[91])
    connection = _RecordedConnection([91])

    rf1 = _refresh_site("power", connection, update_set, "RF1")
    rf2 = _refresh_site("power", connection, update_set, "RF2")

    assert (rf1["status"], rf1["check_status"], rf1["rows_inserted"], rf1["rows"]) == ("SUCCESS", "PASSED", 3, 3)
    assert (rf2["status"], rf2["check_status"]) == ("SUCCESS", "PASSED")
    assert (rf2["orders_deleted"], rf2["lineitems_deleted"], rf2["rows"]) == (1, 1, 2)


def _throughput_refresh_test(connection, update_set, monkeypatch: pytest.MonkeyPatch) -> TPCHThroughputTest:
    test = TPCHThroughputTest(
        benchmark=object(),
        connection_factory=lambda: None,
        scale_factor=0.01,
        num_streams=1,
        refresh_enabled=True,
        refresh_first_set=update_set.set_id - 1,
        refresh_connection_factory=lambda: connection,
    )
    test._pregenerate_stream_queries = lambda config: {}
    monkeypatch.setattr(
        throughput_test,
        "generate_refresh_update_sets",
        lambda scale_factor, first_set, count, output_dir: {update_set.set_id: update_set},
    )
    monkeypatch.setattr(
        throughput_test.StreamRunner,
        "execute",
        staticmethod(lambda _stream_fn, _config, _result, _logger, auxiliary_fn=None: auxiliary_fn()),
    )
    return test


def test_throughput_refresh_checks_run_after_the_refresh_stream_ends(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    update_set = _update_set(tmp_path, 3, order_keys=[41, 42], lineitem_keys=[41, 41, 42], delete_keys=[91, 92])
    connection = _RecordedConnection([91, 92])
    _record_update_set_reads(monkeypatch, update_set, connection)
    test = _throughput_refresh_test(connection, update_set, monkeypatch)

    result = test.run()

    stream = result.refresh_stream
    assert stream.status == "COMPLETED"
    stamps = [(name, at) for name, at in connection.events]
    assert all(at >= stream.start_monotonic for _, at in stamps)
    selects = [(index, at) for index, (name, at) in enumerate(stamps) if name == "SELECT"]
    applies = [(index, at) for index, (name, at) in enumerate(stamps) if name in {"INSERT", "DELETE", "COMMIT"}]
    assert selects and applies
    assert all(at <= stream.end_monotonic for _, at in applies)
    assert all(at >= stream.end_monotonic for _, at in selects)
    assert min(index for index, _ in selects) > max(index for index, _ in applies)
    functions = stream.pairs[0]["functions"]
    assert [function["check_status"] for function in functions] == ["PASSED", "PASSED"]
    assert functions[0]["rows_inserted"] == 5
    assert (functions[1]["orders_deleted"], functions[1]["lineitems_deleted"]) == (2, 2)


def test_throughput_total_time_excludes_check_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    update_set = SimpleNamespace(set_id=3)
    connection = SimpleNamespace(close=lambda: None)
    test = _throughput_refresh_test(connection, update_set, monkeypatch)
    monkeypatch.setattr(throughput_test, "apply_rf1", lambda *_args: 1)
    monkeypatch.setattr(throughput_test, "apply_rf2", lambda *_args: TPCHRF2Deletes(orders=1, lineitems=0))
    monkeypatch.setattr(throughput_test, "check_rf1_applied", lambda *_args: time.sleep(0.2))
    monkeypatch.setattr(throughput_test, "check_rf2_applied", lambda *_args: time.sleep(0.2))

    result = test.run()

    assert [function["check_status"] for function in result.refresh_stream.pairs[0]["functions"]] == ["PASSED"] * 2
    assert 0 < result.total_time < 0.1


def test_throughput_failed_refresh_check_withholds_throughput(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    update_set = _update_set(tmp_path, 3, order_keys=[41, 41], lineitem_keys=[41], delete_keys=[91])
    test = _throughput_refresh_test(_RecordedConnection([91]), update_set, monkeypatch)

    result = test.run()

    assert result.success is False
    assert result.throughput_at_size == 0.0
    assert result.refresh_stream.status == "FAILED"
    assert "refresh set 3 RF1" in result.refresh_stream.error
    assert "the database needs a reload" in result.refresh_stream.error
    rf1 = result.refresh_stream.pairs[0]["functions"][0]
    assert (rf1["status"], rf1["check_status"], rf1["duration_ms"]) == ("FAILED", "FAILED", 0)
    assert result.errors == [f"Refresh stream failed: {result.refresh_stream.error}"]


def test_refresh_stream_stops_before_next_update_set_after_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading
    from types import SimpleNamespace

    from benchbox.core.tpch import throughput_test
    from benchbox.core.tpch.throughput_test import TPCHRefreshStreamResult, TPCHThroughputTest

    cancel_event = threading.Event()
    applied = []
    connection = SimpleNamespace(close=lambda: None)
    test = TPCHThroughputTest(
        benchmark=object(),
        connection_factory=lambda: None,
        refresh_enabled=True,
        refresh_connection_factory=lambda: connection,
    )
    update_sets = {set_id: SimpleNamespace(set_id=set_id) for set_id in (3, 4)}

    def apply_rf1(_connection, update_set):
        applied.append(("RF1", update_set.set_id))
        cancel_event.set()
        return 1

    def apply_rf2(_connection, update_set):
        applied.append(("RF2", update_set.set_id))
        return TPCHRF2Deletes(orders=1, lineitems=0)

    monkeypatch.setattr(throughput_test, "apply_rf1", apply_rf1)
    monkeypatch.setattr(throughput_test, "apply_rf2", apply_rf2)

    result = test._execute_refresh_stream(update_sets, 2, TPCHRefreshStreamResult(first_set=3), cancel_event)

    assert applied == [("RF1", 3), ("RF2", 3)]
    assert result.status == "FAILED"
    assert "cancelled" in result.error
