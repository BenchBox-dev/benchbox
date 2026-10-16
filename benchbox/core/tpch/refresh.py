from __future__ import annotations

import csv
import os
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from benchbox.core.tpch.generator import TPCHDataGenerator
from benchbox.core.tpch.schema import LINEITEM, ORDERS, DataType

REFRESH_CHUNK_SIZE = 1000


@dataclass(frozen=True)
class TPCHRefreshUpdateSet:
    set_id: int
    orders_path: Path
    lineitem_path: Path
    delete_path: Path


def refresh_unsupported_reason(adapter: Any) -> str | None:
    if getattr(adapter, "supports_refresh_functions", False) is True:
        return None
    platform_name = getattr(adapter, "platform_name", type(adapter).__name__)
    return f"TPC-H refresh functions are not supported by platform '{platform_name}'"


def require_refresh_support(adapter: Any) -> None:
    reason = refresh_unsupported_reason(adapter)
    if reason is not None:
        raise NotImplementedError(reason)


def generate_refresh_update_sets(
    scale_factor: float,
    first_set: int,
    count: int,
    output_dir: Path,
) -> dict[int, TPCHRefreshUpdateSet]:
    if isinstance(first_set, bool) or not isinstance(first_set, int):
        raise TypeError(f"first_set must be an integer, got {type(first_set).__name__}")
    if isinstance(count, bool) or not isinstance(count, int):
        raise TypeError(f"count must be an integer, got {type(count).__name__}")
    if first_set < 1:
        raise ValueError(f"first_set must be positive, got {first_set}")
    if count < 1:
        raise ValueError(f"count must be positive, got {count}")

    output_dir.mkdir(parents=True, exist_ok=True)
    if any(output_dir.iterdir()):
        raise ValueError(f"DBGen refresh output directory must be empty: {output_dir}")

    generator = TPCHDataGenerator(scale_factor=scale_factor, output_dir=output_dir, quiet=True)
    dbgen_path = Path(generator.dbgen_exe).resolve()
    distributions = dbgen_path.parent / "dists.dss"
    if not distributions.is_file():
        raise FileNotFoundError(f"DBGen distribution file was not found: {distributions}")
    shutil.copy2(dbgen_path, output_dir / dbgen_path.name)
    shutil.copy2(distributions, output_dir / "dists.dss")

    last_set = first_set + count - 1
    environment = os.environ.copy()
    environment["PATH"] = os.pathsep.join((str(output_dir), environment.get("PATH", "")))
    environment["DSS_PATH"] = "."
    environment["DSS_CONFIG"] = "."
    completed = subprocess.run(
        ["dbgen", "-s", format(scale_factor, "g"), "-U", str(last_set), "-f"],
        cwd=output_dir,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic output"
        raise RuntimeError(f"DBGen failed to generate TPC-H refresh sets (exit {completed.returncode}): {detail}")

    update_sets = {}
    for set_id in range(first_set, last_set + 1):
        update_set = TPCHRefreshUpdateSet(
            set_id=set_id,
            orders_path=output_dir / f"orders.tbl.u{set_id}",
            lineitem_path=output_dir / f"lineitem.tbl.u{set_id}",
            delete_path=output_dir / f"delete.{set_id}",
        )
        missing = [
            path.name
            for path in (update_set.orders_path, update_set.lineitem_path, update_set.delete_path)
            if not path.is_file()
        ]
        if missing:
            raise RuntimeError(f"DBGen did not produce refresh set {set_id}: {', '.join(missing)}")
        update_sets[set_id] = update_set
    return update_sets


def check_rf1_preconditions(
    connection: Any, update_set: TPCHRefreshUpdateSet, chunk_size: int = REFRESH_CHUNK_SIZE
) -> None:
    _require_chunk_size(chunk_size)
    for rows in _source_row_chunks(update_set.orders_path, ORDERS, chunk_size):
        order_keys = [int(row[0]) for row in rows]
        if _count_matching_keys(connection, ORDERS.name, "O_ORDERKEY", order_keys):
            raise ValueError(
                f"refresh set {update_set.set_id} was already applied; reload the data or set tpch_refresh_first_set"
            )


def check_rf2_preconditions(
    connection: Any, update_set: TPCHRefreshUpdateSet, chunk_size: int = REFRESH_CHUNK_SIZE
) -> int:
    _require_chunk_size(chunk_size)
    expected_orders = 0
    deleted_lineitems = 0
    for order_keys in _key_chunks(update_set.delete_path, chunk_size):
        matching_orders = _count_matching_keys(connection, ORDERS.name, "O_ORDERKEY", order_keys)
        if matching_orders != len(order_keys):
            raise ValueError(
                f"refresh set {update_set.set_id} expected to delete {len(order_keys)} orders, found {matching_orders}"
            )
        deleted_lineitems += _count_matching_keys(connection, LINEITEM.name, "L_ORDERKEY", order_keys)
        expected_orders += len(order_keys)
    if expected_orders == 0:
        raise ValueError(f"refresh set {update_set.set_id} has no delete keys")
    return deleted_lineitems


def check_rf2_applied(connection: Any, update_set: TPCHRefreshUpdateSet, chunk_size: int = REFRESH_CHUNK_SIZE) -> None:
    _require_chunk_size(chunk_size)
    for order_keys in _key_chunks(update_set.delete_path, chunk_size):
        if _count_matching_keys(connection, ORDERS.name, "O_ORDERKEY", order_keys):
            raise ValueError(f"refresh set {update_set.set_id} left orders for deleted keys")
        if _count_matching_keys(connection, LINEITEM.name, "L_ORDERKEY", order_keys):
            raise ValueError(f"refresh set {update_set.set_id} left lineitems for deleted orders")


def apply_rf1(connection: Any, update_set: TPCHRefreshUpdateSet, chunk_size: int = REFRESH_CHUNK_SIZE) -> int:
    _require_chunk_size(chunk_size)
    _begin_transaction(connection)
    try:
        rows_inserted = _insert_source_rows(connection, update_set.orders_path, ORDERS, chunk_size)
        rows_inserted += _insert_source_rows(connection, update_set.lineitem_path, LINEITEM, chunk_size)
        _commit(connection)
        return rows_inserted
    except BaseException:
        _rollback(connection)
        raise


def apply_rf2(
    connection: Any,
    update_set: TPCHRefreshUpdateSet,
    deleted_lineitems: int,
    chunk_size: int = REFRESH_CHUNK_SIZE,
) -> int:
    _require_chunk_size(chunk_size)
    _begin_transaction(connection)
    try:
        expected_orders = 0
        for order_keys in _key_chunks(update_set.delete_path, chunk_size):
            key_list = ", ".join(str(key) for key in order_keys)
            connection.execute(f"DELETE FROM {LINEITEM.name} WHERE L_ORDERKEY IN ({key_list})")
            connection.execute(f"DELETE FROM {ORDERS.name} WHERE O_ORDERKEY IN ({key_list})")
            expected_orders += len(order_keys)

        _commit(connection)
        return expected_orders + deleted_lineitems
    except BaseException:
        _rollback(connection)
        raise


def _require_chunk_size(chunk_size: int) -> None:
    if isinstance(chunk_size, bool) or not isinstance(chunk_size, int):
        raise TypeError(f"chunk_size must be an integer, got {type(chunk_size).__name__}")
    if chunk_size < 1:
        raise ValueError(f"chunk_size must be positive, got {chunk_size}")


def _begin_transaction(connection: Any) -> None:
    execute = getattr(connection, "execute", None)
    if not callable(execute):
        raise TypeError("TPC-H refresh functions require a connection with execute()")
    execute("BEGIN")


def _commit(connection: Any) -> None:
    commit = getattr(connection, "commit", None)
    if not callable(commit):
        raise TypeError("TPC-H refresh functions require transactional commit() support")
    commit()


def _rollback(connection: Any) -> None:
    rollback = getattr(connection, "rollback", None)
    if callable(rollback):
        try:
            rollback()
        except Exception:
            pass


def _count_matching_keys(connection: Any, table_name: str, column_name: str, keys: list[int]) -> int:
    if not keys:
        return 0
    key_list = ", ".join(str(key) for key in keys)
    cursor = connection.execute(f"SELECT COUNT(*) FROM {table_name} WHERE {column_name} IN ({key_list})")
    row = cursor.fetchone()
    if row is None:
        raise RuntimeError(f"Could not count rows for {table_name}.{column_name}")
    return int(row[0])


def _source_row_chunks(path: Path, table: Any, chunk_size: int) -> Iterator[list[list[str]]]:
    chunk: list[list[str]] = []
    with path.open(encoding="utf-8", newline="") as source:
        for line_number, fields in enumerate(csv.reader(source, delimiter="|"), start=1):
            if fields and fields[-1] == "":
                fields.pop()
            if len(fields) != len(table.columns):
                raise ValueError(f"{path.name}:{line_number} has {len(fields)} fields; expected {len(table.columns)}")
            chunk.append(fields)
            if len(chunk) == chunk_size:
                yield chunk
                chunk = []
    if chunk:
        yield chunk


def _key_chunks(path: Path, chunk_size: int) -> Iterator[list[int]]:
    chunk: list[int] = []
    with path.open(encoding="utf-8", newline="") as source:
        for line_number, fields in enumerate(csv.reader(source, delimiter="|"), start=1):
            if fields and fields[-1] == "":
                fields.pop()
            if len(fields) != 1:
                raise ValueError(f"{path.name}:{line_number} has {len(fields)} fields; expected 1")
            try:
                chunk.append(int(fields[0]))
            except ValueError:
                raise ValueError(f"{path.name}:{line_number} contains an invalid order key") from None
            if len(chunk) == chunk_size:
                yield chunk
                chunk = []
    if chunk:
        yield chunk


def _insert_source_rows(connection: Any, path: Path, table: Any, chunk_size: int) -> int:
    column_names = ", ".join(column.name for column in table.columns)
    inserted = 0
    for rows in _source_row_chunks(path, table, chunk_size):
        values = ", ".join(
            f"({', '.join(_sql_literal(value, column.data_type) for value, column in zip(row, table.columns, strict=True))})"
            for row in rows
        )
        connection.execute(f"INSERT INTO {table.name} ({column_names}) VALUES {values}")
        inserted += len(rows)
    return inserted


def _sql_literal(value: str, data_type: DataType) -> str:
    if data_type is DataType.INTEGER:
        try:
            return str(int(value))
        except ValueError:
            raise ValueError(f"invalid TPC-H integer value {value!r}") from None
    if data_type is DataType.DECIMAL:
        try:
            decimal_value = Decimal(value)
        except InvalidOperation:
            raise ValueError(f"invalid TPC-H decimal value {value!r}") from None
        if not decimal_value.is_finite():
            raise ValueError(f"invalid TPC-H decimal value {value!r}")
        return format(decimal_value, "f")
    return "'" + value.replace("'", "''") + "'"
