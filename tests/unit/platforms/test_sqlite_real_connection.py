from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture()
def adapter(tmp_path):

    from benchbox.platforms.sqlite import SQLiteAdapter

    return SQLiteAdapter(database_path=":memory:")


@pytest.fixture()
def connection(adapter):

    conn = adapter.create_connection()
    yield conn
    try:
        conn.close()
    except Exception:
        pass


class TestSQLiteRealConnection:
    def test_create_connection_returns_usable_connection(self, adapter):
        conn = adapter.create_connection()
        cursor = conn.execute("SELECT 1")
        assert cursor.fetchone() == (1,)
        conn.close()

    def test_execute_query_returns_result_dict(self, adapter, connection):
        result = adapter.execute_query(
            connection=connection,
            query="SELECT 42 AS answer",
            query_id="test_q1",
            validate_row_count=False,
        )
        assert result["query_id"] == "test_q1"
        assert result["execution_time_seconds"] >= 0
        assert result["rows_returned"] == 1

    def test_execute_query_with_table_data(self, adapter, connection):
        connection.execute("CREATE TABLE items (id INTEGER, name TEXT)")
        connection.execute("INSERT INTO items VALUES (1, 'alpha'), (2, 'beta')")
        connection.commit()

        result = adapter.execute_query(
            connection=connection,
            query="SELECT * FROM items ORDER BY id",
            query_id="q_items",
            validate_row_count=False,
        )
        assert result["rows_returned"] == 2
        assert result.get("error") is None

    def test_execute_query_with_bad_sql_returns_error(self, adapter, connection):
        result = adapter.execute_query(
            connection=connection,
            query="SELECTX GARBAGE",
            query_id="bad_q",
            validate_row_count=False,
        )
        assert result["error"] is not None

    def test_platform_info_returns_sqlite_version(self, adapter, connection):
        info = adapter.get_platform_info(connection)
        assert "platform_version" in info

        assert "." in info["platform_version"]
        assert info["engine_version"] == info["platform_version"]
        assert info["engine_version_source"] == "sql_query"

    def test_wal_mode_applied_for_file_database(self, adapter, tmp_path):

        from benchbox.platforms.sqlite import SQLiteAdapter

        db_path = str(tmp_path / "test.db")
        file_adapter = SQLiteAdapter(database_path=db_path)
        conn = file_adapter.create_connection()

        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()

        assert journal_mode is not None
        conn.close()

    def test_close_connection_makes_connection_unusable(self, adapter):
        conn = adapter.create_connection()
        conn.execute("SELECT 1")
        conn.close()
        with pytest.raises(Exception):
            conn.execute("SELECT 1")


class _TpchBenchmarkStub:
    _name = "TPC-H Benchmark"


class _OtherBenchmarkStub:
    _name = "Other Benchmark"


def _create_minimal_tpch_tables(connection):
    connection.execute("CREATE TABLE customer (c_custkey INTEGER)")
    connection.execute(
        "CREATE TABLE lineitem (l_orderkey INTEGER, l_suppkey INTEGER, l_receiptdate TEXT, l_commitdate TEXT)"
    )
    connection.execute("CREATE TABLE nation (n_name TEXT, n_nationkey INTEGER)")
    connection.execute("CREATE TABLE orders (o_orderstatus TEXT, o_orderkey INTEGER)")
    connection.execute("CREATE TABLE part (p_partkey INTEGER)")
    connection.execute("CREATE TABLE partsupp (ps_partkey INTEGER)")
    connection.execute("CREATE TABLE region (r_regionkey INTEGER)")
    connection.execute("CREATE TABLE supplier (s_nationkey INTEGER, s_suppkey INTEGER)")
    connection.commit()


def _index_names(connection) -> set[str]:
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    return {row[0] for row in rows}


def test_tpch_helper_indexes_are_applied_after_load(adapter, connection):
    _create_minimal_tpch_tables(connection)

    elapsed = adapter._apply_benchmark_helper_indexes(_TpchBenchmarkStub(), connection)

    assert elapsed >= 0
    assert {
        "idx_bb_tpch_lineitem_order_supp_dates",
        "idx_bb_tpch_orders_status_order",
        "idx_bb_tpch_supplier_nation_supp",
        "idx_bb_tpch_nation_name_key",
    }.issubset(_index_names(connection))


def test_tpch_helper_indexes_do_not_apply_to_other_benchmarks(adapter, connection):
    _create_minimal_tpch_tables(connection)

    elapsed = adapter._apply_benchmark_helper_indexes(_OtherBenchmarkStub(), connection)

    assert elapsed == 0.0
    assert _index_names(connection) == set()
