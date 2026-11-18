from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from benchbox.core.contracts.platform_capabilities import refresh_is_transactional, refresh_reports_delete_counts
from benchbox.core.tpch.refresh import (
    TPCHRefreshUpdateSet,
    TPCHRF2Baseline,
    TPCHRF2Deletes,
    apply_rf1,
    apply_rf2,
    capture_rf2_baseline,
    check_rf1_applied,
    check_rf2_applied,
    refresh_transactional,
    rf2_precount,
)
from benchbox.core.tpch.schema import LINEITEM, ORDERS
from benchbox.platforms.base.adapter import PlatformAdapter
from benchbox.platforms.clickhouse_local import ClickHouseLocalAdapter

pytestmark = [pytest.mark.unit, pytest.mark.fast, pytest.mark.tpch]


def _source_row(table, key: int) -> str:
    values = []
    for index, column in enumerate(table.columns):
        if index == 0:
            values.append(str(key))
        elif column.data_type.name in {"INTEGER", "DECIMAL"}:
            values.append("1")
        elif column.data_type.name == "DATE":
            values.append("1995-01-01")
        else:
            values.append("x")
    return "|".join(values) + "|\n"


def _update_set(tmp_path: Path, keys: list[int]) -> TPCHRefreshUpdateSet:
    orders = tmp_path / "orders.tbl"
    lineitem = tmp_path / "lineitem.tbl"
    delete = tmp_path / "delete.tbl"
    orders.write_text("".join(_source_row(ORDERS, key) for key in keys), encoding="utf-8")
    lineitem.write_text("".join(_source_row(LINEITEM, key) for key in keys), encoding="utf-8")
    delete.write_text("".join(f"{key}|\n" for key in keys), encoding="utf-8")
    return TPCHRefreshUpdateSet(set_id=3, orders_path=orders, lineitem_path=lineitem, delete_path=delete)


class _Driver:
    def __init__(self, counts: list[int] | None = None, rowcount: int = -1) -> None:
        self.statements: list[str] = []
        self.counts = iter(counts or [])
        self.rowcount = rowcount
        self.commits = 0
        self.rollbacks = 0
        self._last_was_count = False

    def execute(self, statement: str):
        self.statements.append(statement)
        self._last_was_count = statement.startswith("SELECT COUNT(*)")
        return self

    def fetchone(self):
        if self._last_was_count:
            count = next(self.counts, 0)
            return (count, count)
        return None

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def _wrap(driver: _Driver, **capabilities) -> SimpleNamespace:
    adapter = SimpleNamespace(**capabilities)
    return SimpleNamespace(
        platform_adapter=adapter,
        execute=driver.execute,
        commit=driver.commit,
        rollback=driver.rollback,
        connection=driver,
    )


def test_adapter_base_defaults_are_transactional_and_count_reporting() -> None:
    assert PlatformAdapter.supports_refresh_transactions is True
    assert PlatformAdapter.reports_refresh_delete_counts is True
    assert refresh_is_transactional(None) is True
    assert refresh_reports_delete_counts(None) is True
    assert refresh_is_transactional(SimpleNamespace(supports_refresh_transactions=False)) is False
    assert refresh_reports_delete_counts(SimpleNamespace(reports_refresh_delete_counts=False)) is False


def test_clickhouse_local_declares_the_non_transactional_contract() -> None:
    assert ClickHouseLocalAdapter.supports_refresh_functions is True
    assert ClickHouseLocalAdapter.supports_native_refresh_ingest is True
    assert ClickHouseLocalAdapter.supports_refresh_transactions is False
    assert ClickHouseLocalAdapter.reports_refresh_delete_counts is False


def test_rf1_skips_begin_commit_and_rollback_without_transactions(tmp_path: Path) -> None:
    update_set = _update_set(tmp_path, [11, 12])
    driver = _Driver()
    connection = _wrap(driver, supports_refresh_transactions=False)

    assert refresh_transactional(connection) is False
    assert apply_rf1(connection, update_set) == 4

    assert "BEGIN" not in driver.statements
    assert driver.commits == 0
    assert driver.rollbacks == 0


def test_failed_rf1_does_not_roll_back_without_transactions(tmp_path: Path) -> None:
    update_set = _update_set(tmp_path, [11])
    driver = _Driver()

    def failing(statement: str):
        driver.statements.append(statement)
        raise RuntimeError("insert failed")

    connection = _wrap(driver, supports_refresh_transactions=False)
    connection.execute = failing

    with pytest.raises(RuntimeError, match="insert failed"):
        apply_rf1(connection, update_set)

    assert driver.rollbacks == 0


def test_transactional_adapter_still_begins_and_commits(tmp_path: Path) -> None:
    update_set = _update_set(tmp_path, [11])
    driver = _Driver()
    connection = _wrap(driver)
    connection.connection = SimpleNamespace()

    apply_rf1(connection, update_set)

    assert driver.statements[0] == "BEGIN"
    assert driver.commits == 1


def test_rf2_takes_its_counts_from_the_baseline_and_deletes_without_counting(tmp_path: Path) -> None:
    update_set = _update_set(tmp_path, [21, 22])
    driver = _Driver()
    connection = _wrap(driver, supports_refresh_transactions=False, reports_refresh_delete_counts=False)
    baseline = TPCHRF2Baseline(orders_total=9, lineitems_total=30, orders_for_keys=5, lineitems_for_keys=2)

    deleted = apply_rf2(connection, update_set, precounted=rf2_precount(connection, baseline))

    assert deleted == TPCHRF2Deletes(orders=5, lineitems=2)
    assert driver.statements == [
        "DELETE FROM lineitem WHERE l_orderkey IN (21, 22)",
        "DELETE FROM orders WHERE o_orderkey IN (21, 22)",
    ]


def test_throughput_rf2_issues_no_count_query_inside_the_refresh_stream(tmp_path: Path) -> None:
    from benchbox.core.tpch.throughput_test import TPCHThroughputTest

    update_set = _update_set(tmp_path, [21, 22])
    driver = _Driver()
    connection = _wrap(driver, supports_refresh_transactions=False, reports_refresh_delete_counts=False)
    throughput = TPCHThroughputTest(benchmark=object(), connection_factory=lambda: None, num_streams=1)
    throughput._rf2_baselines = {
        update_set.set_id: TPCHRF2Baseline(orders_total=9, lineitems_total=30, orders_for_keys=5, lineitems_for_keys=2)
    }

    result = throughput._execute_refresh_function("RF2", update_set, connection)

    assert result["status"] == "SUCCESS", result["error"]
    assert result["orders_deleted"] == 5
    assert not any(statement.startswith("SELECT") for statement in driver.statements)


def test_rf2_without_reported_counts_refuses_to_run_without_a_baseline(tmp_path: Path) -> None:
    update_set = _update_set(tmp_path, [21])
    connection = _wrap(_Driver(), reports_refresh_delete_counts=False)

    with pytest.raises(RuntimeError, match="baseline"):
        rf2_precount(connection, None)
    with pytest.raises(RuntimeError, match="baseline"):
        apply_rf2(connection, update_set)


def test_rf2_uses_the_reported_count_when_the_driver_provides_one(tmp_path: Path) -> None:
    update_set = _update_set(tmp_path, [21, 22])
    driver = _Driver(rowcount=7)
    connection = _wrap(driver, supports_refresh_transactions=False)

    deleted = apply_rf2(connection, update_set)

    assert deleted == TPCHRF2Deletes(orders=7, lineitems=7)
    assert not any(statement.startswith("SELECT COUNT(*)") for statement in driver.statements)


def test_refresh_sql_uses_schema_column_names_for_case_sensitive_engines(tmp_path: Path) -> None:
    update_set = _update_set(tmp_path, [31])
    driver = _Driver(counts=[1, 1, 5, 5, 0, 0])
    connection = _wrap(driver, supports_refresh_transactions=False, reports_refresh_delete_counts=False)

    check_rf1_applied(_Driver(counts=[1, 1]), update_set, 2)
    apply_rf2(connection, update_set, precounted=rf2_precount(connection, capture_rf2_baseline(connection, update_set)))
    check_rf2_applied(driver, update_set, 1)

    statements = " ".join(driver.statements)
    assert "O_ORDERKEY" not in statements
    assert "L_ORDERKEY" not in statements
    assert "o_orderkey" in statements
    assert "l_orderkey" in statements


class _SqliteRefreshConnection:
    def __init__(self, adapter: Any, seeded_keys: list[int]) -> None:
        import sqlite3

        self.platform_adapter = adapter
        self.database = sqlite3.connect(":memory:", isolation_level=None)
        for table in (ORDERS, LINEITEM):
            self.database.execute(f"CREATE TABLE {table.name} ({', '.join(column.name for column in table.columns)})")
        for key in seeded_keys:
            self.database.execute(f"INSERT INTO orders (o_orderkey) VALUES ({key})")
            self.database.execute(f"INSERT INTO lineitem (l_orderkey) VALUES ({key})")

    def execute(self, statement: str):
        return self.database.execute(statement)

    def commit(self) -> None:
        self.database.commit()

    def rollback(self) -> None:
        self.database.rollback()

    def close(self) -> None:
        return None


@pytest.mark.parametrize("function", ["RF1", "RF2"])
@pytest.mark.parametrize(
    ("adapter", "expected"),
    [
        (
            SimpleNamespace(
                supports_refresh_transactions=False,
                reports_refresh_delete_counts=False,
                record_refresh_applied=lambda _connection: None,
            ),
            False,
        ),
        (SimpleNamespace(record_refresh_applied=lambda _connection: None), True),
    ],
)
def test_power_and_throughput_refresh_evidence_records_whether_the_step_was_transactional(
    tmp_path: Path, function: str, adapter: Any, expected: bool
) -> None:
    from benchbox.core.tpch.power_test import TPCHPowerTest
    from benchbox.core.tpch.throughput_test import TPCHThroughputTest

    update_set = _update_set(tmp_path, [81])
    connection = _SqliteRefreshConnection(adapter, [81] if function == "RF2" else [])

    power = TPCHPowerTest(
        benchmark=object(),
        connection=connection,
        refresh_update_set=update_set,
        refresh_connection_factory=lambda: connection,
    )._execute_refresh_function(function, connection)
    connection = _SqliteRefreshConnection(adapter, [81] if function == "RF2" else [])
    throughput_test = TPCHThroughputTest(
        benchmark=object(), connection_factory=lambda: None, scale_factor=0.01, num_streams=1
    )
    throughput_test._rf2_baselines = {update_set.set_id: capture_rf2_baseline(connection, update_set)}
    throughput = throughput_test._execute_refresh_function(function, update_set, connection)

    assert power["status"] == "SUCCESS", power["error"]
    assert throughput["status"] == "SUCCESS", throughput["error"]
    assert power["transactional"] is expected
    assert throughput["transactional"] is expected
