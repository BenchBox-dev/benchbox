from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from benchbox.core.results.maintenance_method import MAINTENANCE_METHOD
from benchbox.core.tpcds.generator import TPCDSDataGenerator
from benchbox.core.tpcds.maintenance_functions import MAINTENANCE_FUNCTIONS, MAINTENANCE_FUNCTIONS_BY_NAME
from benchbox.core.tpcds.maintenance_test import TPCDSMaintenanceTest, TPCDSMaintenanceTestConfig
from benchbox.core.tpcds.refresh_data import generate_refresh_sets
from benchbox.core.tpcds.schema import get_create_all_tables_sql
from benchbox.core.tpcds.schema.registry import TABLES

duckdb = pytest.importorskip("duckdb")

pytestmark = [pytest.mark.integration, pytest.mark.slow, pytest.mark.tpcds]

SCALE_FACTOR = 0.01

EXPECTED_STAGED_ROWS = {
    "DF_CS": {"s_delete": 3},
    "DF_SS": {"s_delete": 3},
    "DF_WS": {"s_delete": 3},
    "DF_I": {"s_inventory_delete": 3},
    "LF_CR": {"s_catalog_returns": 8},
    "LF_CS": {"s_catalog_order": 8, "s_catalog_order_lineitem": 72},
    "LF_I": {"s_inventory": 540},
    "LF_SR": {"s_store_returns": 13},
    "LF_SS": {"s_purchase": 12, "s_purchase_lineitem": 144},
    "LF_WR": {"s_web_returns": 2},
    "LF_WS": {"s_web_order": 2, "s_web_order_lineitem": 24},
}
EXPECTED_ROWS_BY_TABLE = {
    "DF_CS": {"catalog_returns": -8, "catalog_sales": -72},
    "DF_SS": {"store_returns": -13, "store_sales": -144},
    "DF_WS": {"web_returns": -2, "web_sales": -24},
    "DF_I": {"inventory": -540},
    "LF_CR": {"catalog_returns": 8},
    "LF_CS": {"catalog_sales": 72},
    "LF_I": {"inventory": 540},
    "LF_SR": {"store_returns": 13},
    "LF_SS": {"store_sales": 144},
    "LF_WR": {"web_returns": 2},
    "LF_WS": {"web_sales": 24},
}
EXPECTED_STATEMENTS = {function.name: len(function.statements) for function in MAINTENANCE_FUNCTIONS}


def _build_database(root: Path, enable_primary_keys: bool) -> Path:
    data_dir = root / "data"
    generated = TPCDSDataGenerator(scale_factor=SCALE_FACTOR, output_dir=data_dir, quiet=True).generate()
    database = root / "base.duckdb"
    connection = duckdb.connect(str(database))
    try:
        connection.execute(
            get_create_all_tables_sql(enable_primary_keys=enable_primary_keys, enable_foreign_keys=False)
        )
        for table in TABLES:
            paths = generated.get(table.name)
            if not paths:
                continue
            columns = ", ".join(f"'{column.name}': '{column.get_sql_type()}'" for column in table.columns)
            column_names = ", ".join(column.name for column in table.columns)
            for path in paths:
                literal = str(path).replace("'", "''")
                connection.execute(
                    f"INSERT INTO {table.name} ({column_names}) SELECT {column_names} FROM read_csv('{literal}', "
                    f"delim='|', header=false, nullstr='', auto_detect=false, columns={{{columns}}})"
                )
        connection.execute("CHECKPOINT")
    finally:
        connection.close()
    return database


@pytest.fixture(scope="module")
def base_database(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _build_database(tmp_path_factory.mktemp("tpcds_refresh_base"), enable_primary_keys=False)


@pytest.fixture(scope="module")
def primary_key_database(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _build_database(tmp_path_factory.mktemp("tpcds_refresh_pk_base"), enable_primary_keys=True)


def _copy_database(base_database: Path, tmp_path: Path) -> Path:
    target = tmp_path / "working.duckdb"
    shutil.copyfile(base_database, target)
    return target


def _raw_factory(database: Path):
    return lambda: duckdb.connect(str(database))


def _native_factory(database: Path):
    from benchbox.platforms.base.connection_wrappers import PlatformAdapterConnection
    from benchbox.platforms.duckdb import DuckDBAdapter

    return lambda: PlatformAdapterConnection(duckdb.connect(str(database)), DuckDBAdapter(), maintenance_mode=True)


def _table_counts(database: Path) -> dict[str, int]:
    connection = duckdb.connect(str(database), read_only=True)
    try:
        return {table.name: connection.execute(f"SELECT COUNT(*) FROM {table.name}").fetchone()[0] for table in TABLES}
    finally:
        connection.close()


def _staging_tables(database: Path) -> list[str]:
    connection = duckdb.connect(str(database), read_only=True)
    try:
        rows = connection.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name LIKE 's\\_%' ESCAPE '\\'"
        )
        return [row[0] for row in rows.fetchall()]
    finally:
        connection.close()


def _run(factory) -> dict:
    harness = TPCDSMaintenanceTest(benchmark=None, connection_factory=factory, scale_factor=SCALE_FACTOR)
    return harness.run(TPCDSMaintenanceTestConfig(scale_factor=SCALE_FACTOR, refresh_runs=1, first_refresh_set=1))


SALES_AND_RETURNS = {
    "DF_CS": ("catalog_sales", "catalog_returns", "cs_sold_date_sk", "cs_order_number", "cr_order_number"),
    "DF_SS": ("store_sales", "store_returns", "ss_sold_date_sk", "ss_ticket_number", "sr_ticket_number"),
    "DF_WS": ("web_sales", "web_returns", "ws_sold_date_sk", "ws_order_number", "wr_order_number"),
}


def _delete_ranges(path: Path) -> list[tuple[str, str]]:
    return [tuple(line.split("|")) for line in path.read_text(encoding="utf-8").splitlines()]


def _range_predicate(column: str, ranges: list[tuple[str, str]]) -> str:
    return " OR ".join(f"{column} BETWEEN DATE '{low}' AND DATE '{high}'" for low, high in ranges)


def _expected_base_deletes(database: Path, tmp_path: Path) -> dict[str, dict[str, int]]:
    refresh_set = generate_refresh_sets(SCALE_FACTOR, 1, 1, tmp_path / "ranges")[1]
    ranges = _delete_ranges(refresh_set.path_for("s_delete"))
    inventory_ranges = _delete_ranges(refresh_set.path_for("s_inventory_delete"))
    connection = duckdb.connect(str(database), read_only=True)
    try:
        expected: dict[str, dict[str, int]] = {}
        in_range = _range_predicate("d_date", ranges)
        for name, (sales, returns, date_sk, sales_key, returns_key) in SALES_AND_RETURNS.items():
            sales_count = connection.execute(
                f"SELECT COUNT(*) FROM {sales} WHERE {date_sk} IN (SELECT d_date_sk FROM date_dim WHERE {in_range})"
            ).fetchone()[0]
            returns_count = connection.execute(
                f"SELECT COUNT(*) FROM {returns} WHERE {returns_key} IN (SELECT {sales_key} FROM {sales}, date_dim "
                f"WHERE {date_sk} = d_date_sk AND ({in_range}))"
            ).fetchone()[0]
            expected[name] = {sales: -sales_count, returns: -returns_count}
        inventory_in_range = _range_predicate("d_date", inventory_ranges)
        expected["DF_I"] = {
            "inventory": -connection.execute(
                f"SELECT COUNT(*) FROM inventory WHERE inv_date_sk IN "
                f"(SELECT d_date_sk FROM date_dim WHERE {inventory_in_range})"
            ).fetchone()[0]
        }
        return expected
    finally:
        connection.close()


def _assert_function_evidence(
    result: dict, expected_rows: dict[str, dict[str, int]], returns_at_least: bool = False
) -> None:
    assert result["errors"] == []
    assert result["success"] is True
    assert result["maintenance_method"] == MAINTENANCE_METHOD
    assert result["refresh_sets"] == [1]
    assert result["total_operations"] == result["successful_operations"] == 11
    assert result["failed_operations"] == 0
    assert result["insert_operations"] == 7
    assert result["delete_operations"] == 4
    assert result["integrity_valid"] is True

    operations = {operation.function: operation for operation in result["operations"]}
    assert [operation.function for operation in result["operations"]] == [
        function.name for function in MAINTENANCE_FUNCTIONS
    ]
    for name, operation in operations.items():
        assert operation.success is True, name
        assert operation.error is None, name
        assert operation.refresh_set == 1, name
        assert operation.staged_rows == EXPECTED_STAGED_ROWS[name], name
        if returns_at_least and name.startswith("DF_"):
            for table, delta in expected_rows[name].items():
                if table.endswith("_returns"):
                    assert operation.rows_by_table[table] <= delta, (name, table)
                else:
                    assert operation.rows_by_table[table] == delta, (name, table)
        else:
            assert operation.rows_by_table == expected_rows[name], name
            assert operation.rows_affected == sum(abs(delta) for delta in expected_rows[name].values()), name
        assert operation.statements_executed == EXPECTED_STATEMENTS[name], name
        assert operation.duration >= 0.0, name


def _first_run_expectations(base_database: Path, tmp_path: Path) -> dict[str, dict[str, int]]:
    expected = {name: dict(rows) for name, rows in EXPECTED_ROWS_BY_TABLE.items() if name.startswith("LF_")}
    expected.update(_expected_base_deletes(base_database, tmp_path))
    return expected


def _net_change(expected: dict[str, dict[str, int]]) -> dict[str, int]:
    net: dict[str, int] = {}
    for rows in expected.values():
        for table, delta in rows.items():
            net[table] = net.get(table, 0) + delta
    return net


@pytest.mark.parametrize("make_factory", [_native_factory, _raw_factory], ids=["native-ingest", "fallback-insert"])
def test_all_eleven_functions_succeed_with_expected_row_evidence(
    base_database: Path, tmp_path: Path, make_factory
) -> None:
    database = _copy_database(base_database, tmp_path)
    expected = _first_run_expectations(base_database, tmp_path)
    assert all(delta < 0 for name in ("DF_CS", "DF_SS", "DF_WS", "DF_I") for delta in expected[name].values())

    result = _run(make_factory(database))

    _assert_function_evidence(result, expected)


@pytest.mark.parametrize("make_factory", [_native_factory, _raw_factory], ids=["native-ingest", "fallback-insert"])
def test_warehouse_row_counts_change_by_exactly_deletes_plus_inserts_and_staging_is_dropped(
    base_database: Path, tmp_path: Path, make_factory
) -> None:
    database = _copy_database(base_database, tmp_path)
    before = _table_counts(database)
    expected = _first_run_expectations(base_database, tmp_path)
    net = _net_change(expected)
    assert before["store_sales"] > 0
    assert before["inventory"] > 0

    result = _run(make_factory(database))

    assert result["success"] is True
    assert _table_counts(database) == {table: count + net.get(table, 0) for table, count in before.items()}
    assert _staging_tables(database) == []


@pytest.mark.parametrize("make_factory", [_native_factory, _raw_factory], ids=["native-ingest", "fallback-insert"])
def test_second_run_deletes_the_reinserted_rows_and_returns_to_same_totals(
    base_database: Path, tmp_path: Path, make_factory
) -> None:
    database = _copy_database(base_database, tmp_path)
    first_expected = _first_run_expectations(base_database, tmp_path)

    first = _run(make_factory(database))
    after_first = _table_counts(database)
    second = _run(make_factory(database))
    after_second = _table_counts(database)

    _assert_function_evidence(first, first_expected)
    _assert_function_evidence(second, EXPECTED_ROWS_BY_TABLE, returns_at_least=True)
    unchanged = [table for table in after_first if not table.endswith("_returns")]
    assert {table: after_second[table] for table in unchanged} == {table: after_first[table] for table in unchanged}
    assert all(after_second[table] <= after_first[table] for table in after_first if table.endswith("_returns"))


def test_native_and_fallback_loaders_leave_identical_warehouse_contents(base_database: Path, tmp_path: Path) -> None:
    native_dir = tmp_path / "native"
    fallback_dir = tmp_path / "fallback"
    native_dir.mkdir()
    fallback_dir.mkdir()
    native_db = _copy_database(base_database, native_dir)
    fallback_db = _copy_database(base_database, fallback_dir)

    assert _run(_native_factory(native_db))["success"] is True
    assert _run(_raw_factory(fallback_db))["success"] is True

    for table in (
        "store_sales",
        "store_returns",
        "catalog_sales",
        "catalog_returns",
        "web_sales",
        "web_returns",
        "inventory",
    ):
        query = f"SELECT * FROM {table} ORDER BY ALL"
        native = duckdb.connect(str(native_db), read_only=True)
        fallback = duckdb.connect(str(fallback_db), read_only=True)
        try:
            assert native.execute(query).fetchall() == fallback.execute(query).fetchall(), table
        finally:
            native.close()
            fallback.close()


def test_refresh_keys_overlap_base_keys_in_every_channel(base_database: Path, tmp_path: Path) -> None:
    refresh_sets = generate_refresh_sets(SCALE_FACTOR, 1, 3, tmp_path / "keys")
    connection = duckdb.connect(str(base_database), read_only=True)
    try:
        for staging, base_table, base_key in (
            ("s_purchase", "store_sales", "ss_ticket_number"),
            ("s_catalog_order", "catalog_sales", "cs_order_number"),
            ("s_web_order", "web_sales", "ws_order_number"),
        ):
            base_max = connection.execute(f"SELECT MAX({base_key}) FROM {base_table}").fetchone()[0]
            for refresh_set in refresh_sets.values():
                keys = [
                    int(line.split("|")[0])
                    for line in refresh_set.path_for(staging).read_text(encoding="utf-8").splitlines()
                ]
                assert min(keys) == 1
                assert min(keys) <= base_max
    finally:
        connection.close()


@pytest.mark.parametrize("make_factory", [_native_factory], ids=["native-ingest"])
def test_enforced_primary_keys_are_refused_before_any_refresh_work(
    primary_key_database: Path, tmp_path: Path, make_factory
) -> None:
    database = _copy_database(primary_key_database, tmp_path)
    before = _table_counts(database)

    result = _run(make_factory(database))

    assert result["success"] is False
    assert result["operations"] == []
    assert len(result["errors"]) == 1
    message = result["errors"][0]
    assert "primary keys are enforced" in message
    for table in ("catalog_sales", "store_sales", "web_sales", "inventory"):
        assert table in message
    assert "--tuning notuning" in message
    assert "duplicate" in message.lower()
    assert _table_counts(database) == before
    assert _staging_tables(database) == []


@pytest.mark.parametrize("name", ["DF_CS", "DF_SS", "DF_WS"])
def test_df_returns_of_out_of_range_orders_survive_a_key_collision(
    base_database: Path, tmp_path: Path, name: str
) -> None:
    database = _copy_database(base_database, tmp_path)
    assert _run(_native_factory(database))["success"] is True
    refresh_set = generate_refresh_sets(SCALE_FACTOR, 1, 1, tmp_path / "second")[1]
    in_range = _range_predicate("d.d_date", _delete_ranges(refresh_set.path_for("s_delete")))
    sales, returns, date_sk, sales_key, returns_key = SALES_AND_RETURNS[name]
    sales_item, returns_item = {
        "DF_CS": ("cs_item_sk", "cr_item_sk"),
        "DF_SS": ("ss_item_sk", "sr_item_sk"),
        "DF_WS": ("ws_item_sk", "wr_item_sk"),
    }[name]
    in_range_sales = (
        f"SELECT {sales_key} AS k, {sales_item} AS i FROM {sales} s JOIN date_dim d "
        f"ON s.{date_sk} = d.d_date_sk WHERE {in_range}"
    )

    probe_connection = duckdb.connect(str(database))
    try:
        order_number = probe_connection.execute(f"SELECT MIN(k) FROM ({in_range_sales})").fetchone()[0]
        assert order_number is not None
        probe_item = probe_connection.execute(f"SELECT MAX({sales_item}) + 1000000 FROM {sales}").fetchone()[0]
        template = probe_connection.execute(f"SELECT * FROM {returns} LIMIT 1").fetchone()
        assert template is not None
        columns = [column[1] for column in probe_connection.execute(f"PRAGMA table_info('{returns}')").fetchall()]
        values = dict(zip(columns, template, strict=True))
        values[returns_key] = order_number
        values[returns_item] = probe_item
        placeholders = ", ".join("?" for _ in columns)
        probe_connection.execute(
            f"INSERT INTO {returns} ({', '.join(columns)}) VALUES ({placeholders})", [values[c] for c in columns]
        )
        survivor_keys = probe_connection.execute(
            f"SELECT DISTINCT r.{returns_key}, r.{returns_item} FROM {returns} r "
            f"WHERE r.{returns_key} IN (SELECT k FROM ({in_range_sales})) "
            f"AND NOT EXISTS (SELECT 1 FROM ({in_range_sales}) m WHERE m.k = r.{returns_key} AND m.i = r.{returns_item})"
        ).fetchall()
        survivor_count_sql = f"SELECT COUNT(*) FROM {returns} WHERE {returns_key} = ? AND {returns_item} = ?"
        survivors_before = [
            probe_connection.execute(survivor_count_sql, list(key)).fetchone()[0] for key in survivor_keys
        ]
        expected_returns = probe_connection.execute(
            f"SELECT COUNT(*) FROM {returns} r WHERE EXISTS (SELECT 1 FROM ({in_range_sales}) m "
            f"WHERE m.k = r.{returns_key} AND m.i = r.{returns_item})"
        ).fetchone()[0]
        expected_sales = probe_connection.execute(f"SELECT COUNT(*) FROM ({in_range_sales})").fetchone()[0]
        returns_before = probe_connection.execute(f"SELECT COUNT(*) FROM {returns}").fetchone()[0]
    finally:
        probe_connection.close()
    assert survivor_keys
    assert all(count >= 1 for count in survivors_before)
    assert expected_sales > 0

    harness = TPCDSMaintenanceTest(
        benchmark=None, connection_factory=_native_factory(database), scale_factor=SCALE_FACTOR
    )
    operation = harness._execute_function(MAINTENANCE_FUNCTIONS_BY_NAME[name], refresh_set)

    assert operation.success is True, operation.error
    assert operation.rows_by_table == {returns: -expected_returns, sales: -expected_sales}
    after = duckdb.connect(str(database), read_only=True)
    try:
        assert [after.execute(survivor_count_sql, list(key)).fetchone()[0] for key in survivor_keys] == survivors_before
        assert after.execute(f"SELECT COUNT(*) FROM {returns}").fetchone()[0] == returns_before - expected_returns
    finally:
        after.close()
