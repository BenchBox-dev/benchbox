from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

import pytest

from benchbox.core.tpch.refresh import (
    TPCHRefreshUpdateSet,
    apply_rf1,
    apply_rf2,
    check_rf1_applied,
    check_rf2_applied,
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


def _load_sf001_base_read_csv(connection: Any) -> dict[str, int]:
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
            names = ", ".join(column.name for column in table.columns)
            typed = ", ".join(f"'{column.name}': 'VARCHAR'" for column in table.columns)
            connection.execute(
                f"INSERT INTO {table.name} ({names}) SELECT {names} FROM read_csv('{staging / filename}', "
                f"delim='|', header=false, auto_detect=false, null_padding=true, columns={{{typed}, '_trailing': 'VARCHAR'}})"
            )
            counts[table.name] = connection.execute(f"SELECT COUNT(*) FROM {table.name}").fetchone()[0]
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


def _check_rf1_rf2_with_expected_counts(
    connection: TPCHConnection, tmp_path: Path, load_base: Any = _load_sf001_base
) -> None:
    base_counts = load_base(connection)
    update_set = _make_update_set(tmp_path)
    delete_keys = _delete_keys(update_set)
    update_orders = _file_row_count(update_set.orders_path)
    update_lineitems = _file_row_count(update_set.lineitem_path)
    key_list = ", ".join(str(key) for key in delete_keys)

    inserted = apply_rf1(connection, update_set)
    check_rf1_applied(connection, update_set, inserted)
    assert inserted == update_orders + update_lineitems
    assert _table_count(connection, ORDERS.name) == base_counts[ORDERS.name] + update_orders
    assert _table_count(connection, LINEITEM.name) == base_counts[LINEITEM.name] + update_lineitems
    assert _table_count(connection, ORDERS.name, f"O_ORDERKEY = {delete_keys[0]}") == 1

    doomed_lineitems = _table_count(connection, LINEITEM.name, f"L_ORDERKEY IN ({key_list})")
    deletes = apply_rf2(connection, update_set)
    check_rf2_applied(connection, update_set, deletes.orders)
    assert deletes.orders == len(delete_keys)
    assert deletes.lineitems == doomed_lineitems
    assert _table_count(connection, ORDERS.name) == base_counts[ORDERS.name] + update_orders - len(delete_keys)
    assert _table_count(connection, ORDERS.name, f"O_ORDERKEY = {delete_keys[0]}") == 0
    assert _table_count(connection, LINEITEM.name, f"L_ORDERKEY = {delete_keys[0]}") == 0


def _load_sf001_base_postgres(connection: Any) -> dict[str, int]:
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
            names = ", ".join(column.name for column in table.columns)
            rows = 0
            with connection.cursor() as cursor:
                with cursor.copy(f"COPY {table.name} ({names}) FROM STDIN WITH (FORMAT csv, DELIMITER '|')") as copy:
                    with (staging / filename).open(encoding="utf-8") as handle:
                        for line in handle:
                            copy.write(line.rstrip("\n").removesuffix("|") + "\n")
                            rows += 1
            counts[table.name] = rows
        connection.commit()
        return counts
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _load_sf001_base_singlestore(connection: Any) -> dict[str, int]:
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
            names = ", ".join(column.name for column in table.columns)
            cursor = connection.cursor()
            try:
                cursor.execute(
                    f"LOAD DATA LOCAL INFILE '{staging / filename}' INTO TABLE {table.name} "
                    f"FIELDS TERMINATED BY '|' LINES TERMINATED BY '\\n' ({names})"
                )
            finally:
                cursor.close()
            counts[table.name] = _table_count(connection, table.name)
        connection.commit()
        return counts
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _column_checksums(connection: Any, table: Any) -> dict[str, str]:
    import hashlib

    names = [column.name for column in table.columns]
    cursor = connection.execute(f"SELECT {', '.join(names)} FROM {table.name} ORDER BY {', '.join(names)}")
    digests = {name: hashlib.sha256() for name in names}
    for row in cursor.fetchall():
        for name, value in zip(names, row, strict=True):
            digests[name].update(f"{type(value).__name__}:{value}\n".encode())
    return {name: digest.hexdigest() for name, digest in digests.items()}


def _table_checksums(connection: Any) -> dict[str, Any]:
    return {
        table.name: {"rows": _table_count(connection, table.name), "columns": _column_checksums(connection, table)}
        for table in (ORDERS, LINEITEM)
    }


def _check_native_rf1_matches_values(
    native_connection: Any,
    values_connection: Any,
    tmp_path: Path,
    load_base: Any,
) -> None:
    from benchbox.core.contracts.platform_capabilities import resolve_refresh_ingester

    assert resolve_refresh_ingester(native_connection.platform_adapter) is not None
    assert resolve_refresh_ingester(values_connection.platform_adapter) is None

    load_base(native_connection.connection)
    load_base(values_connection.connection)
    assert _table_checksums(native_connection) == _table_checksums(values_connection)

    update_set = _make_update_set(tmp_path)
    native_rows = apply_rf1(native_connection, update_set)
    values_rows = apply_rf1(values_connection, update_set)

    assert (
        native_rows
        == values_rows
        == _file_row_count(update_set.orders_path) + _file_row_count(update_set.lineitem_path)
    )
    native_state = _table_checksums(native_connection)
    values_state = _table_checksums(values_connection)
    for table_name in (ORDERS.name, LINEITEM.name):
        assert native_state[table_name]["rows"] == values_state[table_name]["rows"]
        for column_name, digest in values_state[table_name]["columns"].items():
            assert native_state[table_name]["columns"][column_name] == digest, f"{table_name}.{column_name} differs"


def _check_rf1_bad_row_is_atomic(connection: Any, tmp_path: Path, load_base: Any) -> None:
    load_base(connection.connection)
    before = _table_checksums(connection)
    update_set = _make_update_set(tmp_path)
    lines = update_set.lineitem_path.read_text(encoding="utf-8").splitlines(keepends=True)
    fields = lines[len(lines) // 2].split("|")
    fields[5] = "not-a-decimal"
    lines[len(lines) // 2] = "|".join(fields)
    update_set.lineitem_path.write_text("".join(lines), encoding="utf-8")

    with pytest.raises(Exception):
        apply_rf1(connection, update_set)

    assert _table_checksums(connection) == before
    assert _table_count(connection, ORDERS.name) == before[ORDERS.name]["rows"]
