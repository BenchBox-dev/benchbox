from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from benchbox.core.coffeeshop.benchmark import CoffeeShopBenchmark
from benchbox.core.coffeeshop.queries import CoffeeShopQueryManager

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_get_queries_returns_raw_queries_without_translation(tmp_path: Path) -> None:
    benchmark = CoffeeShopBenchmark(scale_factor=0.000001, output_dir=tmp_path)

    with patch.object(benchmark.query_manager, "get_all_queries", return_value={"SA1": "SELECT 1"}) as get_all:
        assert benchmark.get_queries() == {"SA1": "SELECT 1"}

    get_all.assert_called_once()


def test_get_queries_translates_each_query_for_requested_dialect(tmp_path: Path) -> None:
    benchmark = CoffeeShopBenchmark(scale_factor=0.000001, output_dir=tmp_path)

    with (
        patch.object(benchmark.query_manager, "get_all_queries", return_value={"SA1": "SELECT 1", "PR1": "SELECT 2"}),
        patch.object(
            benchmark, "translate_query_text", side_effect=lambda sql, dialect: f"{dialect}:{sql}"
        ) as translate,
    ):
        queries = benchmark.get_queries(dialect="duckdb")

    assert queries == {"SA1": "duckdb:SELECT 1", "PR1": "duckdb:SELECT 2"}
    assert translate.call_count == 2


def test_execute_query_supports_execute_and_cursor_connections(tmp_path: Path) -> None:
    benchmark = CoffeeShopBenchmark(scale_factor=0.000001, output_dir=tmp_path)

    execute_cursor = Mock()
    execute_cursor.fetchall.return_value = [("execute",)]
    execute_connection = Mock()
    execute_connection.execute.return_value = execute_cursor

    cursor = Mock()
    cursor.fetchall.return_value = [("cursor",)]

    class _CursorConnection:
        def cursor(self):
            return cursor

    cursor_connection = _CursorConnection()

    with patch.object(benchmark, "get_query", return_value="SELECT 1"):
        assert benchmark.execute_query("SA1", execute_connection) == [("execute",)]
        assert benchmark.execute_query("SA1", cursor_connection) == [("cursor",)]


def test_execute_query_rejects_unsupported_connection_types(tmp_path: Path) -> None:
    benchmark = CoffeeShopBenchmark(scale_factor=0.000001, output_dir=tmp_path)

    with patch.object(benchmark, "get_query", return_value="SELECT 1"):
        with pytest.raises(ValueError, match="Unsupported connection type"):
            benchmark.execute_query("SA1", object())


def test_get_create_tables_sql_forwards_constraint_flags(tmp_path: Path) -> None:
    benchmark = CoffeeShopBenchmark(scale_factor=0.000001, output_dir=tmp_path)
    tuning_config = SimpleNamespace(
        primary_keys=SimpleNamespace(enabled=False),
        foreign_keys=SimpleNamespace(enabled=True),
    )

    with patch("benchbox.core.coffeeshop.benchmark.get_all_create_table_sql", return_value="DDL") as ddl:
        assert benchmark.get_create_tables_sql(dialect="sqlite", tuning_config=tuning_config) == "DDL"

    ddl.assert_called_once_with(dialect="sqlite", enable_primary_keys=False, enable_foreign_keys=True)


def test_load_data_to_database_uses_cursor_path_without_executescript_or_executemany(tmp_path: Path) -> None:
    benchmark = CoffeeShopBenchmark(scale_factor=0.000001, output_dir=tmp_path)
    data_file = tmp_path / "dim_locations.tbl"
    data_file.write_text("1||north\n", encoding="utf-8")
    benchmark.tables = {"dim_locations": str(data_file)}

    cursor = Mock()

    class _Connection:
        def __init__(self):
            self.commit = Mock()

        def cursor(self):
            return cursor

    connection = _Connection()

    with patch.object(benchmark, "get_create_tables_sql", return_value="CREATE TABLE dim_locations (id INT);"):
        benchmark.load_data_to_database(connection, tables=["dim_locations"])

    executed_sql = [call.args[0] for call in cursor.execute.call_args_list]
    assert any(sql.startswith("CREATE TABLE dim_locations") for sql in executed_sql)
    assert any(sql.startswith("INSERT INTO dim_locations") for sql in executed_sql)
    connection.commit.assert_called_once()


def test_normalize_row_replaces_empty_strings_with_none(tmp_path: Path) -> None:
    benchmark = CoffeeShopBenchmark(scale_factor=0.000001, output_dir=tmp_path)

    assert benchmark._normalize_row(["1", "", "abc", ""]) == ["1", None, "abc", None]


def test_run_benchmark_records_failed_iterations(tmp_path: Path) -> None:
    benchmark = CoffeeShopBenchmark(scale_factor=0.000001, output_dir=tmp_path)

    with (
        patch.object(benchmark, "execute_query", side_effect=[[(1, "ok")], RuntimeError("boom")]),
        patch("benchbox.core.coffeeshop.benchmark.mono_time", return_value=0.0),
        patch("benchbox.core.coffeeshop.benchmark.elapsed_seconds", return_value=0.1),
    ):
        result = benchmark.run_benchmark(connection=object(), queries=["SA1"], iterations=2)

    query_result = result["query_results"][0]
    assert query_result["query_id"] == "SA1"
    assert query_result["iterations"][0]["success"] is True
    assert query_result["iterations"][1]["success"] is False
    assert query_result["success"] is False
    assert query_result["error"] == "boom"
    assert query_result["avg_time"] == 0.1
    assert query_result["total_rows"] == 1


def test_query_manager_rejects_missing_format_parameter_and_accepts_explicit_override() -> None:
    manager = CoffeeShopQueryManager()
    manager._queries["SA1"]["defaults"].pop("start_date")

    with pytest.raises(ValueError, match="Missing parameter 'start_date'") as failure:
        manager.get_query("SA1")

    assert isinstance(failure.value.__cause__, KeyError)
    assert failure.value.__cause__.args == ("start_date",)
    query = manager.get_query("SA1", params={"start_date": "2023-02-01"})
    assert "DATE '2023-02-01'" in query
    assert "{start_date}" not in query


