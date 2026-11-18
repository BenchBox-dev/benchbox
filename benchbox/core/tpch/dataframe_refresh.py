from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from benchbox.core.tpch.refresh import (
    TPCHRefreshUpdateSet,
    TPCHRF2Deletes,
    applied_counts,
    read_rf1_source_keys,
    read_rf2_delete_keys,
)
from benchbox.utils.clock import elapsed_seconds, mono_time

ORDERS_TABLE = "orders"
LINEITEM_TABLE = "lineitem"
ORDERS_KEY = "o_orderkey"
LINEITEM_KEY = "l_orderkey"


class DataFrameRefreshBackend(Protocol):
    def parse_delimited(self, path: Path, like: Any) -> Any: ...

    def concat(self, current: Any, delta: Any) -> Any: ...

    def row_count(self, frame: Any) -> int: ...

    def count_keys(self, frame: Any, column: str, keys: list[int]) -> tuple[int, int]: ...

    def exclude_keys(self, frame: Any, column: str, keys: list[int]) -> Any: ...


@dataclass
class _AppliedState:
    previous: dict[str, Any]
    current: dict[str, Any]
    changed: dict[str, int] = field(default_factory=dict)


class DataFrameRefresher:
    def __init__(self, ctx: Any, backend: DataFrameRefreshBackend) -> None:
        self._ctx = ctx
        self._backend = backend
        self._applied: dict[tuple[str, int], _AppliedState] = {}

    def apply_rf1(self, update_set: TPCHRefreshUpdateSet) -> int:
        previous = self._native_tables()
        orders_delta = self._backend.parse_delimited(update_set.orders_path, previous[ORDERS_TABLE])
        lineitem_delta = self._backend.parse_delimited(update_set.lineitem_path, previous[LINEITEM_TABLE])
        orders_rows = self._backend.row_count(orders_delta)
        lineitem_rows = self._backend.row_count(lineitem_delta)
        current = {
            ORDERS_TABLE: self._backend.concat(previous[ORDERS_TABLE], orders_delta),
            LINEITEM_TABLE: self._backend.concat(previous[LINEITEM_TABLE], lineitem_delta),
        }
        self._ctx.swap_tables(current)
        self._applied[("RF1", update_set.set_id)] = _AppliedState(
            previous, current, {ORDERS_TABLE: orders_rows, LINEITEM_TABLE: lineitem_rows}
        )
        return orders_rows + lineitem_rows

    def apply_rf2(self, update_set: TPCHRefreshUpdateSet) -> TPCHRF2Deletes:
        keys = read_rf2_delete_keys(update_set)
        previous = self._native_tables()
        orders_found, _ = self._backend.count_keys(previous[ORDERS_TABLE], ORDERS_KEY, keys)
        lineitems_found, _ = self._backend.count_keys(previous[LINEITEM_TABLE], LINEITEM_KEY, keys)
        current = {
            ORDERS_TABLE: self._backend.exclude_keys(previous[ORDERS_TABLE], ORDERS_KEY, keys),
            LINEITEM_TABLE: self._backend.exclude_keys(previous[LINEITEM_TABLE], LINEITEM_KEY, keys),
        }
        self._ctx.swap_tables(current)
        self._applied[("RF2", update_set.set_id)] = _AppliedState(
            previous, current, {ORDERS_TABLE: orders_found, LINEITEM_TABLE: lineitems_found}
        )
        return TPCHRF2Deletes(orders=orders_found, lineitems=lineitems_found)

    def check_rf1_applied(self, update_set: TPCHRefreshUpdateSet, rows_inserted: int) -> None:
        state = self._state("RF1", update_set)
        order_keys, lineitem_rows = read_rf1_source_keys(update_set)
        found, distinct = self._backend.count_keys(state.current[ORDERS_TABLE], ORDERS_KEY, order_keys)
        if found != len(order_keys) or distinct != len(order_keys):
            raise ValueError(
                _reload_message(
                    update_set,
                    "RF1",
                    f"found {found} orders rows ({distinct} distinct keys) for {len(order_keys)} inserted order keys",
                )
            )
        found_lineitems, _ = self._backend.count_keys(state.current[LINEITEM_TABLE], LINEITEM_KEY, order_keys)
        if found_lineitems != lineitem_rows:
            raise ValueError(
                _reload_message(
                    update_set,
                    "RF1",
                    f"found {found_lineitems} lineitem rows for the inserted orders, expected {lineitem_rows}",
                )
            )
        for table, inserted in ((ORDERS_TABLE, len(order_keys)), (LINEITEM_TABLE, lineitem_rows)):
            delta = self._backend.row_count(state.current[table]) - self._backend.row_count(state.previous[table])
            if delta != inserted:
                raise ValueError(
                    _reload_message(update_set, "RF1", f"{table} grew by {delta} rows, expected {inserted}")
                )
        if rows_inserted != len(order_keys) + lineitem_rows:
            raise ValueError(
                _reload_message(
                    update_set, "RF1", f"inserted {rows_inserted} rows, expected {len(order_keys) + lineitem_rows}"
                )
            )

    def check_rf2_applied(self, update_set: TPCHRefreshUpdateSet, orders_deleted: int) -> None:
        state = self._state("RF2", update_set)
        keys = read_rf2_delete_keys(update_set)
        if not keys:
            raise ValueError(_reload_message(update_set, "RF2", "has no delete keys"))
        if self._backend.count_keys(state.current[ORDERS_TABLE], ORDERS_KEY, keys)[0]:
            raise ValueError(_reload_message(update_set, "RF2", "left orders for deleted keys"))
        if self._backend.count_keys(state.current[LINEITEM_TABLE], LINEITEM_KEY, keys)[0]:
            raise ValueError(_reload_message(update_set, "RF2", "left lineitems for deleted orders"))
        if orders_deleted != len(keys):
            raise ValueError(
                _reload_message(update_set, "RF2", f"deleted {orders_deleted} orders, expected {len(keys)}")
            )
        for table in (ORDERS_TABLE, LINEITEM_TABLE):
            delta = self._backend.row_count(state.previous[table]) - self._backend.row_count(state.current[table])
            if delta != state.changed[table]:
                raise ValueError(
                    _reload_message(
                        update_set, "RF2", f"{table} shrank by {delta} rows, expected {state.changed[table]}"
                    )
                )

    def discard(self) -> None:
        self._applied.clear()

    def _native_tables(self) -> dict[str, Any]:
        return {
            ORDERS_TABLE: self._ctx.get_native_table(ORDERS_TABLE),
            LINEITEM_TABLE: self._ctx.get_native_table(LINEITEM_TABLE),
        }

    def _state(self, function: str, update_set: TPCHRefreshUpdateSet) -> _AppliedState:
        try:
            return self._applied[(function, update_set.set_id)]
        except KeyError:
            raise RuntimeError(f"refresh set {update_set.set_id} {function} was not applied by this session") from None


def _reload_message(update_set: TPCHRefreshUpdateSet, function: str, detail: str) -> str:
    return (
        f"refresh set {update_set.set_id} {function} {detail}; "
        f"the tables need a reload before refresh set {update_set.set_id} is applied again"
    )


def execute_refresh_function(
    refresher: DataFrameRefresher,
    function: str,
    update_set: TPCHRefreshUpdateSet,
    *,
    check: bool,
) -> dict[str, Any]:
    if function not in {"RF1", "RF2"}:
        raise ValueError(f"Unsupported TPC-H refresh function: {function}")

    counts: dict[str, int] = {"rows": 0}
    status = "FAILED"
    check_status = "NOT_RUN"
    error = None
    duration_ms = 0
    try:
        start_time = mono_time()
        applied = refresher.apply_rf1(update_set) if function == "RF1" else refresher.apply_rf2(update_set)
        duration_ms = max(1, round(elapsed_seconds(start_time) * 1000))
        counts = applied_counts(applied)
        if check:
            check_status = "FAILED"
            check_refresh_function(refresher, function, update_set, counts)
            check_status = "PASSED"
        status = "SUCCESS"
    except Exception as exc:
        error = str(exc)

    return {
        "function": function,
        "update_set": update_set.set_id,
        "duration_ms": duration_ms if status == "SUCCESS" else 0,
        **counts,
        "status": status,
        "check_status": check_status,
        "error": error,
    }


def check_refresh_function(
    refresher: DataFrameRefresher,
    function: str,
    update_set: TPCHRefreshUpdateSet,
    counts: dict[str, int],
) -> None:
    if function == "RF1":
        refresher.check_rf1_applied(update_set, counts["rows_inserted"])
    else:
        refresher.check_rf2_applied(update_set, counts["orders_deleted"])
