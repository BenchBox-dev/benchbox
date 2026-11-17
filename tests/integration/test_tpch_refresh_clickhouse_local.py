from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from benchbox.core.tpch.refresh import (
    TPCHRefreshUpdateSet,
    apply_rf1,
    apply_rf2,
    check_rf1_applied,
    check_rf2_applied,
    generate_refresh_update_sets,
    refresh_transactional,
)
from benchbox.core.tpch.schema import LINEITEM, ORDERS
from benchbox.platforms.base.connection_wrappers import PlatformAdapterConnection
from benchbox.platforms.clickhouse.client import ClickHouseLocalClient
from benchbox.platforms.clickhouse_local import ClickHouseLocalAdapter
from tests.integration._tpch_refresh_utils import (
    _check_native_rf1_matches_values,
    _check_rf1_rf2_with_expected_counts,
    _delete_keys,
    _table_count,
)
from tests.utilities.optional_engines import require_chdb

pytestmark = [pytest.mark.integration, pytest.mark.slow, pytest.mark.tpch]

require_chdb()


def _load_base(client) -> dict[str, int]:
    import shutil
    import tempfile

    from benchbox.core.tpch.generator import TPCHDataGenerator

    adapter = ClickHouseLocalAdapter()
    staging = Path(tempfile.mkdtemp(prefix="tpch-base-"))
    try:
        TPCHDataGenerator(scale_factor=0.01, output_dir=staging, quiet=True).generate()
        counts = {}
        for table, filename in ((ORDERS, "orders.tbl"), (LINEITEM, "lineitem.tbl")):
            columns = ", ".join(f"{column.name} {column.get_sql_type()}" for column in table.columns)
            client.execute(f"CREATE TABLE {table.name} ({columns}) ENGINE = MergeTree ORDER BY tuple()")
            counts[table.name] = adapter.ingest_refresh_file(
                client,
                table.name,
                [(column.name, column.data_type.value) for column in table.columns],
                staging / filename,
            )
        return counts
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _open_base(tmp_path: Path) -> ClickHouseLocalClient:
    tmp_path.mkdir(parents=True, exist_ok=True)
    return ClickHouseLocalClient(db_path=str(tmp_path / "refresh.chdb"))


def _refresh_connection(base: ClickHouseLocalClient, adapter=None, database: str | None = None):
    adapter = adapter or ClickHouseLocalAdapter()
    stream = adapter.new_stream_connection(base, benchmark_type="tpch")
    if database is not None:
        base.execute(f"CREATE DATABASE IF NOT EXISTS {database}")
        stream.execute(f"USE {database}")
    return PlatformAdapterConnection(stream, adapter, True)


def _load_base_via(wrapper: PlatformAdapterConnection):
    def load(_unused):
        return _load_base(wrapper.connection)

    return load


def test_chdb_sf001_applies_rf1_and_rf2_with_expected_counts_and_deleted_keys(tmp_path: Path) -> None:
    base = _open_base(tmp_path)
    connection = _refresh_connection(base)
    try:
        _check_rf1_rf2_with_expected_counts(connection, tmp_path / "dbgen", _load_base_via(connection))
    finally:
        connection.close()
        base.close()


def test_chdb_native_rf1_matches_the_values_path_for_every_column(tmp_path: Path) -> None:
    base = _open_base(tmp_path)
    native = _refresh_connection(base, ClickHouseLocalAdapter(), "native_db")
    values = _refresh_connection(base, ClickHouseLocalAdapter(), "values_db")
    values.platform_adapter = SimpleNamespace(supports_refresh_transactions=False, reports_refresh_delete_counts=False)
    try:
        _check_native_rf1_matches_values(native, values, tmp_path, _load_base)
    finally:
        native.close()
        values.close()
        base.close()


def test_chdb_refresh_is_recorded_as_non_transactional(tmp_path: Path) -> None:
    base = _open_base(tmp_path)
    connection = _refresh_connection(base)
    try:
        assert refresh_transactional(connection) is False
    finally:
        connection.close()
        base.close()


def test_chdb_rf2_reports_exact_counts_through_count_before_delete(tmp_path: Path) -> None:
    base = _open_base(tmp_path)
    connection = _refresh_connection(base)
    try:
        _load_base(connection.connection)
        update_set: TPCHRefreshUpdateSet = generate_refresh_update_sets(0.01, 1, 1, tmp_path / "dbgen")[1]
        inserted = apply_rf1(connection, update_set)
        check_rf1_applied(connection, update_set, inserted)
        keys = ", ".join(str(key) for key in _delete_keys(update_set))
        expected_lineitems = _table_count(connection, LINEITEM.name, f"l_orderkey IN ({keys})")
        deleted = apply_rf2(connection, update_set)
        check_rf2_applied(connection, update_set, deleted.orders)
        assert deleted.lineitems == expected_lineitems
        assert _table_count(connection, LINEITEM.name, f"l_orderkey IN ({keys})") == 0
    finally:
        connection.close()
        base.close()
