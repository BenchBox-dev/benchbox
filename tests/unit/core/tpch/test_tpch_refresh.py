from __future__ import annotations

import os
from pathlib import Path

import pytest

from benchbox.core.tpch import refresh
from benchbox.core.tpch.refresh import (
    TPCHRefreshUpdateSet,
    apply_rf1,
    apply_rf2,
    check_rf1_preconditions,
    check_rf2_applied,
    check_rf2_preconditions,
)
from benchbox.core.tpch.schema import LINEITEM, ORDERS

pytestmark = [pytest.mark.unit, pytest.mark.fast, pytest.mark.tpch]


def _source_row(table, key: int) -> str:
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
        return (next(self.counts, 0),)

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
    assert connection.statements[0] == "BEGIN TRANSACTION"
    assert connection.commits == 1
    assert connection.rollbacks == 0


def test_rf1_rejects_an_existing_order_key_before_transaction(tmp_path: Path) -> None:
    update_set = _update_set(tmp_path, 4, order_keys=[41], lineitem_keys=[41], delete_keys=[])
    connection = RecordingConnection([1])

    with pytest.raises(ValueError, match="refresh set 4 was already applied"):
        check_rf1_preconditions(connection, update_set)

    assert not any(statement.startswith("INSERT INTO") for statement in connection.statements)
    assert connection.commits == 0
    assert connection.rollbacks == 0


def test_rf2_deletes_lineitems_before_orders_in_one_transaction(tmp_path: Path) -> None:
    update_set = _update_set(tmp_path, 12, order_keys=[], lineitem_keys=[], delete_keys=[121, 122])
    connection = RecordingConnection([2, 3, 0, 0])

    deleted_lineitems = check_rf2_preconditions(connection, update_set)
    deleted = apply_rf2(connection, update_set, deleted_lineitems)
    check_rf2_applied(connection, update_set)

    assert deleted == 5
    assert [statement for statement in connection.statements if statement.startswith("DELETE FROM")] == [
        "DELETE FROM lineitem WHERE L_ORDERKEY IN (121, 122)",
        "DELETE FROM orders WHERE O_ORDERKEY IN (121, 122)",
    ]
    assert "BEGIN TRANSACTION" in connection.statements
    assert connection.commits == 1
    assert connection.rollbacks == 0


def test_rf2_uses_1000_key_delete_chunks(tmp_path: Path) -> None:
    keys = list(range(1, 1002))
    update_set = _update_set(tmp_path, 13, order_keys=[], lineitem_keys=[], delete_keys=keys)
    connection = RecordingConnection([1000, 0, 1, 0, 0, 0, 0, 0])

    deleted_lineitems = check_rf2_preconditions(connection, update_set)
    deleted = apply_rf2(connection, update_set, deleted_lineitems)
    check_rf2_applied(connection, update_set)

    deletes = [statement for statement in connection.statements if statement.startswith("DELETE FROM")]
    assert deleted == 1001
    assert len(deletes) == 4
    assert [statement.count(",") + 1 for statement in deletes] == [1000, 1000, 1, 1]
    assert connection.commits == 1
    assert connection.rollbacks == 0


def test_rf2_rejects_missing_orders_before_transaction(tmp_path: Path) -> None:
    update_set = _update_set(tmp_path, 9, order_keys=[], lineitem_keys=[], delete_keys=[91, 92])
    connection = RecordingConnection([1])

    with pytest.raises(ValueError, match="expected to delete 2 orders, found 1"):
        check_rf2_preconditions(connection, update_set)

    assert not any(statement.startswith("DELETE FROM") for statement in connection.statements)
    assert connection.commits == 0
    assert connection.rollbacks == 0


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

    def execute_refresh(update_sets, num_streams, refresh_stream):
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

    result = test.run()

    assert generated == [(0.01, 9, 2)]
    assert executed_sets == [9, 10]
    assert result.refresh_stream.first_set == 9


def test_throughput_runs_all_safety_checks_outside_the_refresh_measurement(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from benchbox.core.tpch import throughput_test
    from benchbox.core.tpch.throughput_test import TPCHRefreshStreamResult, TPCHThroughputTest

    events = []
    connection = SimpleNamespace(close=lambda: None)
    test = TPCHThroughputTest(
        benchmark=object(),
        connection_factory=lambda: None,
        scale_factor=0.01,
        num_streams=2,
        refresh_enabled=True,
        refresh_connection_factory=lambda: connection,
    )
    update_sets = {set_id: SimpleNamespace(set_id=set_id) for set_id in (3, 4)}
    monkeypatch.setattr(
        throughput_test,
        "check_rf1_preconditions",
        lambda _connection, update_set: events.append(("precheck-rf1", update_set.set_id)),
    )
    monkeypatch.setattr(
        throughput_test,
        "check_rf2_preconditions",
        lambda _connection, update_set: events.append(("precheck-rf2", update_set.set_id)) or 1,
    )
    monkeypatch.setattr(
        throughput_test,
        "apply_rf1",
        lambda _connection, update_set: events.append(("apply-rf1", update_set.set_id)) or 2,
    )
    monkeypatch.setattr(
        throughput_test,
        "apply_rf2",
        lambda _connection, update_set, _deleted: events.append(("apply-rf2", update_set.set_id)) or 3,
    )
    monkeypatch.setattr(
        throughput_test,
        "check_rf2_applied",
        lambda _connection, update_set: events.append(("postcheck-rf2", update_set.set_id)),
    )

    result = test._execute_refresh_stream(update_sets, 2, TPCHRefreshStreamResult(first_set=3))

    assert result.success is True
    assert events == [
        ("precheck-rf1", 3),
        ("precheck-rf2", 3),
        ("precheck-rf1", 4),
        ("precheck-rf2", 4),
        ("apply-rf1", 3),
        ("apply-rf2", 3),
        ("apply-rf1", 4),
        ("apply-rf2", 4),
        ("postcheck-rf2", 3),
        ("postcheck-rf2", 4),
    ]
    assert result.start_monotonic <= result.measurement_start_monotonic
    assert result.measurement_start_monotonic <= result.measurement_end_monotonic
    assert result.measurement_end_monotonic <= result.end_monotonic
