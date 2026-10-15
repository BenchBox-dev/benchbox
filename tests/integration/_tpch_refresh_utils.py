from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from benchbox.core.tpch.refresh import (
    TPCHRefreshUpdateSet,
    apply_rf1,
    apply_rf2,
    check_rf1_preconditions,
    check_rf2_applied,
    check_rf2_preconditions,
)
from benchbox.core.tpch.schema import LINEITEM, ORDERS


class TPCHConnection(Protocol):
    def execute(self, statement: str) -> Any: ...

    def executemany(self, statement: str, parameters: Any) -> Any: ...

    def commit(self) -> Any: ...


def _make_update_set(tmp_path: Path) -> TPCHRefreshUpdateSet:
    from benchbox.core.tpch.refresh import generate_refresh_update_sets

    return generate_refresh_update_sets(0.01, 1, 1, tmp_path / "dbgen")[1]


def _load_sf001_base(connection: TPCHConnection) -> dict[str, int]:
    import csv
    import shutil
    import tempfile

    from benchbox.core.tpch.generator import TPCHDataGenerator

    staging = Path(tempfile.mkdtemp(prefix="tpch-base-"))
    try:
        TPCHDataGenerator(scale_factor=0.01, output_dir=staging, quiet=True).generate()
        counts = {}
        for table, filename in ((ORDERS, "orders.tbl"), (LINEITEM, "lineitem.tbl")):
            columns = ", ".join(f"{column.name} {column.get_sql_type()}" for column in table.columns)
            connection.execute(f"CREATE TABLE {table.name} ({columns})")
            with (staging / filename).open(encoding="utf-8", newline="") as handle:
                rows = []
                for fields in csv.reader(handle, delimiter="|"):
                    if fields and fields[-1] == "":
                        fields.pop()
                    rows.append(tuple(fields))
            assert all(len(row) == len(table.columns) for row in rows)
            names = ", ".join(column.name for column in table.columns)
            placeholders = ", ".join("?" * len(table.columns))
            for start in range(0, len(rows), 5000):
                connection.executemany(
                    f"INSERT INTO {table.name} ({names}) VALUES ({placeholders})",
                    rows[start : start + 5000],
                )
            counts[table.name] = len(rows)
        connection.commit()
        return counts
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _file_row_count(path: Path) -> int:
    with path.open(encoding="utf-8") as handle:
        return sum(1 for _ in handle)


def _delete_keys(update_set: TPCHRefreshUpdateSet) -> list[int]:
    return [
        int(line.split("|", 1)[0])
        for line in update_set.delete_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _table_count(connection: TPCHConnection, table_name: str, where: str = "") -> int:
    query = f"SELECT COUNT(*) FROM {table_name}" + (f" WHERE {where}" if where else "")
    return connection.execute(query).fetchone()[0]


def _check_rf1_rf2_with_expected_counts(connection: TPCHConnection, tmp_path: Path) -> None:
    base_counts = _load_sf001_base(connection)
    update_set = _make_update_set(tmp_path)
    delete_keys = _delete_keys(update_set)
    update_orders = _file_row_count(update_set.orders_path)
    update_lineitems = _file_row_count(update_set.lineitem_path)
    key_list = ", ".join(str(key) for key in delete_keys)

    check_rf1_preconditions(connection, update_set)
    inserted = apply_rf1(connection, update_set)
    assert inserted == update_orders + update_lineitems
    assert _table_count(connection, ORDERS.name) == base_counts[ORDERS.name] + update_orders
    assert _table_count(connection, LINEITEM.name) == base_counts[LINEITEM.name] + update_lineitems
    assert _table_count(connection, ORDERS.name, f"O_ORDERKEY = {delete_keys[0]}") == 1

    doomed_lineitems = _table_count(connection, LINEITEM.name, f"L_ORDERKEY IN ({key_list})")
    deleted_lineitems = check_rf2_preconditions(connection, update_set)
    removed = apply_rf2(connection, update_set, deleted_lineitems)
    check_rf2_applied(connection, update_set)
    assert removed == len(delete_keys) + doomed_lineitems
    assert _table_count(connection, ORDERS.name) == base_counts[ORDERS.name] + update_orders - len(delete_keys)
    assert _table_count(connection, ORDERS.name, f"O_ORDERKEY = {delete_keys[0]}") == 0
    assert _table_count(connection, LINEITEM.name, f"L_ORDERKEY = {delete_keys[0]}") == 0
