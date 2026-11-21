from __future__ import annotations

import csv
import os
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from benchbox.core.contracts.platform_capabilities import (
    TPCHRefreshIngester,
    refresh_is_transactional,
    refresh_reports_delete_counts,
    resolve_refresh_ingester,
)
from benchbox.core.tpch.generator import TPCHDataGenerator
from benchbox.core.tpch.schema import LINEITEM, ORDERS, DataType
from benchbox.utils.run_deadline import current_run_deadline, run_child

REFRESH_CHUNK_SIZE = 1000
ORDERS_KEY_COLUMN = ORDERS.columns[0].name
LINEITEM_KEY_COLUMN = LINEITEM.columns[0].name


@dataclass(frozen=True)
class TPCHRefreshUpdateSet:
    set_id: int
    orders_path: Path
    lineitem_path: Path
    delete_path: Path


@dataclass(frozen=True)
class TPCHRF2Deletes:
    orders: int
    lineitems: int

    @property
    def rows(self) -> int:
        return self.orders + self.lineitems


@dataclass(frozen=True)
class TPCHRF2Baseline:
    orders_total: int
    lineitems_total: int
    orders_for_keys: int
    lineitems_for_keys: int


def _greybeam_routed(adapter: Any) -> bool:
    from benchbox.core.gateway import is_greybeam_gateway

    return is_greybeam_gateway(getattr(getattr(adapter, "gateway_endpoint", None), "name", None))


def refresh_unsupported_reason(adapter: Any) -> str | None:
    if _greybeam_routed(adapter):
        return "TPC-H refresh functions are not supported through the greybeam gateway"
    if getattr(adapter, "supports_refresh_functions", False) is True:
        return None
    platform_name = getattr(adapter, "platform_name", type(adapter).__name__)
    return f"TPC-H refresh functions are not supported by platform '{platform_name}'"


def require_refresh_support(adapter: Any) -> None:
    if _greybeam_routed(adapter):
        from benchbox.core.gateway import raise_greybeam_maintenance_unsupported

        raise_greybeam_maintenance_unsupported()
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
    completed = run_child(
        ["dbgen", "-s", format(scale_factor, "g"), "-U", str(last_set), "-f"],
        tool=f"dbgen (refresh sets {first_set} to {last_set}, scale factor {scale_factor:g})",
        deadline=current_run_deadline(),
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


def check_rf1_applied(
    connection: Any,
    update_set: TPCHRefreshUpdateSet,
    rows_inserted: int,
    chunk_size: int = REFRESH_CHUNK_SIZE,
) -> None:
    _require_chunk_size(chunk_size)
    order_rows = 0
    found_lineitems = 0
    for rows in _source_row_chunks(update_set.orders_path, ORDERS, chunk_size):
        order_keys = [int(row[0]) for row in rows]
        order_rows += len(order_keys)
        found, distinct = _count_matching_keys_distinct(connection, ORDERS.name, ORDERS_KEY_COLUMN, order_keys)
        if found != len(order_keys) or distinct != len(order_keys):
            raise ValueError(
                _reload_message(
                    update_set,
                    "RF1",
                    f"found {found} orders rows ({distinct} distinct keys) for {len(order_keys)} inserted order keys",
                )
            )
        found_lineitems += _count_matching_keys(connection, LINEITEM.name, LINEITEM_KEY_COLUMN, order_keys)
    lineitem_rows = sum(len(rows) for rows in _source_row_chunks(update_set.lineitem_path, LINEITEM, chunk_size))
    if found_lineitems != lineitem_rows:
        raise ValueError(
            _reload_message(
                update_set,
                "RF1",
                f"found {found_lineitems} lineitem rows for the inserted orders, expected {lineitem_rows}",
            )
        )
    if rows_inserted != order_rows + lineitem_rows:
        raise ValueError(
            _reload_message(
                update_set,
                "RF1",
                f"inserted {rows_inserted} rows, expected {order_rows + lineitem_rows}",
            )
        )


def check_rf2_applied(
    connection: Any,
    update_set: TPCHRefreshUpdateSet,
    orders_deleted: int,
    chunk_size: int = REFRESH_CHUNK_SIZE,
) -> None:
    _require_chunk_size(chunk_size)
    expected_orders = 0
    for order_keys in _key_chunks(update_set.delete_path, chunk_size):
        expected_orders += len(order_keys)
        if _count_matching_keys(connection, ORDERS.name, ORDERS_KEY_COLUMN, order_keys):
            raise ValueError(_reload_message(update_set, "RF2", "left orders for deleted keys"))
        if _count_matching_keys(connection, LINEITEM.name, LINEITEM_KEY_COLUMN, order_keys):
            raise ValueError(_reload_message(update_set, "RF2", "left lineitems for deleted orders"))
    if expected_orders == 0:
        raise ValueError(_reload_message(update_set, "RF2", "has no delete keys"))
    if orders_deleted != expected_orders:
        raise ValueError(
            _reload_message(update_set, "RF2", f"deleted {orders_deleted} orders, expected {expected_orders}")
        )


def apply_rf1(connection: Any, update_set: TPCHRefreshUpdateSet, chunk_size: int = REFRESH_CHUNK_SIZE) -> int:
    _require_chunk_size(chunk_size)
    ingester = resolve_refresh_ingester(getattr(connection, "platform_adapter", None))
    _begin_transaction(connection)
    try:
        if ingester is None:
            rows_inserted = _insert_source_rows(connection, update_set.orders_path, ORDERS, chunk_size)
            rows_inserted += _insert_source_rows(connection, update_set.lineitem_path, LINEITEM, chunk_size)
        else:
            rows_inserted = _ingest_source_file(ingester, connection, update_set.orders_path, ORDERS)
            rows_inserted += _ingest_source_file(ingester, connection, update_set.lineitem_path, LINEITEM)
        _commit(connection)
        return rows_inserted
    except BaseException:
        _rollback(connection)
        raise


def rf2_precount(connection: Any, baseline: TPCHRF2Baseline | None) -> TPCHRF2Deletes | None:
    if refresh_reports_delete_counts(getattr(connection, "platform_adapter", None)):
        return None
    if baseline is None:
        raise RuntimeError("RF2 needs a baseline captured before the timer starts on a driver without delete counts")
    return TPCHRF2Deletes(orders=baseline.orders_for_keys, lineitems=baseline.lineitems_for_keys)


def apply_rf2(
    connection: Any,
    update_set: TPCHRefreshUpdateSet,
    chunk_size: int = REFRESH_CHUNK_SIZE,
    precounted: TPCHRF2Deletes | None = None,
) -> TPCHRF2Deletes:
    _require_chunk_size(chunk_size)
    reports = refresh_reports_delete_counts(getattr(connection, "platform_adapter", None))
    if not reports and precounted is None:
        raise RuntimeError(
            f"refresh set {update_set.set_id} RF2: the driver reports no deleted row count; "
            "derive the count from the RF2 baseline before the timer starts"
        )
    _begin_transaction(connection)
    try:
        lineitems_deleted = 0
        orders_deleted = 0
        for order_keys in _key_chunks(update_set.delete_path, chunk_size):
            lineitems_deleted += _delete_keys(connection, LINEITEM.name, LINEITEM_KEY_COLUMN, order_keys, update_set)
            orders_deleted += _delete_keys(connection, ORDERS.name, ORDERS_KEY_COLUMN, order_keys, update_set)

        _commit(connection)
        return TPCHRF2Deletes(orders=orders_deleted, lineitems=lineitems_deleted) if reports else precounted
    except BaseException:
        _rollback(connection)
        raise


def _delete_keys(
    connection: Any, table_name: str, column_name: str, keys: list[int], update_set: TPCHRefreshUpdateSet
) -> int:
    key_list = ", ".join(str(key) for key in keys)
    cursor = connection.execute(f"DELETE FROM {table_name} WHERE {column_name} IN ({key_list})")
    if refresh_reports_delete_counts(getattr(connection, "platform_adapter", None)):
        return _reported_row_count(cursor, update_set)
    return 0


def read_rf2_delete_keys(update_set: TPCHRefreshUpdateSet) -> list[int]:
    return [key for chunk in _key_chunks(update_set.delete_path, REFRESH_CHUNK_SIZE) for key in chunk]


def read_rf1_source_keys(update_set: TPCHRefreshUpdateSet) -> tuple[list[int], int]:
    order_keys = [
        int(row[0]) for rows in _source_row_chunks(update_set.orders_path, ORDERS, REFRESH_CHUNK_SIZE) for row in rows
    ]
    lineitem_rows = sum(
        len(rows) for rows in _source_row_chunks(update_set.lineitem_path, LINEITEM, REFRESH_CHUNK_SIZE)
    )
    return order_keys, lineitem_rows


def record_refresh_applied(connection: Any) -> None:
    recorder = getattr(getattr(connection, "platform_adapter", None), "record_refresh_applied", None)
    if not callable(recorder):
        raise RuntimeError(
            "TPC-H refresh functions need a connection that carries its platform adapter so the refresh marker can "
            "be recorded; the database would otherwise be reused after it was changed"
        )
    recorder(getattr(connection, "connection", connection))


def _reload_message(update_set: TPCHRefreshUpdateSet, function: str, detail: str) -> str:
    return (
        f"refresh set {update_set.set_id} {function} {detail}; "
        f"the database needs a reload before refresh set {update_set.set_id} is applied again"
    )


def _reported_row_count(cursor: Any, update_set: TPCHRefreshUpdateSet) -> int:
    rowcount = getattr(cursor, "rowcount", None)
    if isinstance(rowcount, int) and not isinstance(rowcount, bool) and rowcount >= 0:
        return rowcount
    fetchone = getattr(cursor, "fetchone", None)
    row = fetchone() if callable(fetchone) else None
    if row and isinstance(row[0], int) and not isinstance(row[0], bool) and row[0] >= 0:
        return row[0]
    raise RuntimeError(f"refresh set {update_set.set_id} RF2: the driver did not report a deleted row count")


def _require_chunk_size(chunk_size: int) -> None:
    if isinstance(chunk_size, bool) or not isinstance(chunk_size, int):
        raise TypeError(f"chunk_size must be an integer, got {type(chunk_size).__name__}")
    if chunk_size < 1:
        raise ValueError(f"chunk_size must be positive, got {chunk_size}")


def refresh_transactional(connection: Any) -> bool:
    return refresh_is_transactional(getattr(connection, "platform_adapter", None))


def _begin_transaction(connection: Any) -> None:
    if not refresh_transactional(connection):
        return
    if getattr(getattr(connection, "connection", connection), "autocommit", None) is False:
        return
    execute = getattr(connection, "execute", None)
    if not callable(execute):
        raise TypeError("TPC-H refresh functions require a connection with execute()")
    execute("BEGIN")


def _commit(connection: Any) -> None:
    if not refresh_transactional(connection):
        return
    commit = getattr(connection, "commit", None)
    if not callable(commit):
        raise TypeError("TPC-H refresh functions require transactional commit() support")
    commit()


def _rollback(connection: Any) -> None:
    if not refresh_transactional(connection):
        return
    rollback = getattr(connection, "rollback", None)
    if callable(rollback):
        try:
            rollback()
        except Exception:
            pass


def _count_matching_keys_distinct(
    connection: Any, table_name: str, column_name: str, keys: list[int]
) -> tuple[int, int]:
    key_list = ", ".join(str(key) for key in keys)
    cursor = connection.execute(
        f"SELECT COUNT(*), COUNT(DISTINCT {column_name}) FROM {table_name} WHERE {column_name} IN ({key_list})"
    )
    row = cursor.fetchone()
    if row is None:
        raise RuntimeError(f"Could not count rows for {table_name}.{column_name}")
    return int(row[0]), int(row[1])


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


def _ingest_source_file(ingester: TPCHRefreshIngester, connection: Any, path: Path, table: Any) -> int:
    columns = [(column.name, column.data_type.value) for column in table.columns]
    return ingester.ingest_refresh_file(connection, table.name, columns, path)


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


def applied_counts(applied: int | TPCHRF2Deletes) -> dict[str, int]:
    if isinstance(applied, TPCHRF2Deletes):
        return {"rows": applied.rows, "orders_deleted": applied.orders, "lineitems_deleted": applied.lineitems}
    return {"rows": applied, "rows_inserted": applied}


def capture_rf2_baseline(
    connection: Any,
    update_set: TPCHRefreshUpdateSet,
    chunk_size: int = REFRESH_CHUNK_SIZE,
) -> TPCHRF2Baseline:
    _require_chunk_size(chunk_size)
    orders_for_keys = 0
    lineitems_for_keys = 0
    for order_keys in _key_chunks(update_set.delete_path, chunk_size):
        orders_for_keys += _count_matching_keys(connection, ORDERS.name, ORDERS_KEY_COLUMN, order_keys)
        lineitems_for_keys += _count_matching_keys(connection, LINEITEM.name, LINEITEM_KEY_COLUMN, order_keys)
    return TPCHRF2Baseline(
        orders_total=_count_table_rows(connection, ORDERS.name),
        lineitems_total=_count_table_rows(connection, LINEITEM.name),
        orders_for_keys=orders_for_keys,
        lineitems_for_keys=lineitems_for_keys,
    )


def verify_rf2_deletes(
    connection: Any,
    update_set: TPCHRefreshUpdateSet,
    baseline: TPCHRF2Baseline,
    deletes: TPCHRF2Deletes,
) -> None:
    for table_name, total_before, expected in (
        (ORDERS.name, baseline.orders_total, baseline.orders_for_keys),
        (LINEITEM.name, baseline.lineitems_total, baseline.lineitems_for_keys),
    ):
        removed = total_before - _count_table_rows(connection, table_name)
        if removed != expected:
            raise ValueError(
                _reload_message(
                    update_set,
                    "RF2",
                    f"removed {removed} rows from {table_name}, expected {expected} rows matching the delete keys",
                )
            )
    verify_rf2_reported(update_set, baseline, deletes)


def verify_rf2_reported(update_set: TPCHRefreshUpdateSet, baseline: TPCHRF2Baseline, deletes: TPCHRF2Deletes) -> None:
    for table_name, expected, reported in (
        (ORDERS.name, baseline.orders_for_keys, deletes.orders),
        (LINEITEM.name, baseline.lineitems_for_keys, deletes.lineitems),
    ):
        if reported != expected:
            raise ValueError(
                _reload_message(
                    update_set,
                    "RF2",
                    f"the driver reported {reported} rows deleted from {table_name}, expected {expected}",
                )
            )


def verify_refresh_stream_totals(
    connection: Any,
    initial: TPCHRF2Baseline,
    inserted_sets: list[TPCHRefreshUpdateSet],
    deleted_baselines: list[TPCHRF2Baseline],
    chunk_size: int = REFRESH_CHUNK_SIZE,
) -> None:
    _require_chunk_size(chunk_size)
    inserted_orders = 0
    inserted_lineitems = 0
    for update_set in inserted_sets:
        inserted_orders += sum(len(rows) for rows in _source_row_chunks(update_set.orders_path, ORDERS, chunk_size))
        inserted_lineitems += sum(
            len(rows) for rows in _source_row_chunks(update_set.lineitem_path, LINEITEM, chunk_size)
        )
    for table_name, total_before, inserted, expected in (
        (ORDERS.name, initial.orders_total, inserted_orders, sum(b.orders_for_keys for b in deleted_baselines)),
        (
            LINEITEM.name,
            initial.lineitems_total,
            inserted_lineitems,
            sum(b.lineitems_for_keys for b in deleted_baselines),
        ),
    ):
        removed = total_before + inserted - _count_table_rows(connection, table_name)
        if removed != expected:
            raise ValueError(
                f"the refresh stream removed {removed} rows from {table_name}, expected {expected} rows matching "
                "the delete keys; the database needs a reload before the update sets are applied again"
            )


def _count_table_rows(connection: Any, table_name: str) -> int:
    row = connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
    if row is None or row[0] is None:
        raise RuntimeError(f"Could not count rows for {table_name}")
    return int(row[0])
