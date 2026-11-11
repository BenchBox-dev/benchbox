from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import pytest

from benchbox.core.benchmark_mixins import (
    CursorValidationQueryExecutionMixin,
    DataGenerationMixin,
    OperationCategoryFacadeMixin,
    QueryCategoryFacadeMixin,
    QueryFacadeMixin,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _FakeParent:
    def log_verbose(self, msg: str) -> None:
        pass

    def log_very_verbose(self, msg: str) -> None:
        pass

    def _build_query_result_with_validation(self, **kwargs: Any) -> dict[str, Any]:
        return {"status": "SUCCESS", **kwargs}


class _DataGenerationBenchmark(DataGenerationMixin):
    def __init__(self, *, batch_size: int | None = None) -> None:
        self._batch_size = batch_size
        self.tables: dict[str, Any] = {}
        self.data_generator = Mock()
        self._schema = {
            "customer": {"columns": [{"name": "id"}, {"name": "name"}]},
            "orders": {"columns": [{"name": "id"}, {"name": "amount"}]},
        }

    def _get_table_schema(self) -> dict[str, Any]:
        return self._schema

    def _get_data_loading_batch_size(self) -> int | None:
        return self._batch_size

    def get_create_tables_sql(self) -> str:
        return "CREATE TABLE customer (id INT, name TEXT);CREATE TABLE orders (id INT, amount INT);"


class _FacadeBenchmark(
    QueryCategoryFacadeMixin,
    OperationCategoryFacadeMixin,
    QueryFacadeMixin,
):
    def __init__(self) -> None:
        self._impl = Mock()


@pytest.mark.unit
class TestCursorValidationMixinMRO:
    def test_required_methods_resolve_to_parent_not_mixin(self):

        class ConcreteAdapter(CursorValidationQueryExecutionMixin, _FakeParent):
            pass

        adapter = ConcreteAdapter()
        adapter.log_verbose("test")
        adapter.log_very_verbose("test")
        result = adapter._build_query_result_with_validation(query_id="q1")
        assert result["status"] == "SUCCESS"

    def test_mixin_does_not_define_required_methods(self):
        mixin_own_methods = set(CursorValidationQueryExecutionMixin.__dict__.keys())
        for method_name in CursorValidationQueryExecutionMixin._REQUIRED_METHODS:
            assert method_name not in mixin_own_methods, (
                f"{method_name} must NOT be defined on CursorValidationQueryExecutionMixin "
                f"- it would shadow the real implementation via MRO"
            )

    def test_real_adapters_resolve_methods_correctly(self):
        from benchbox.platforms.base.adapter import PlatformAdapter

        for adapter_name in ("trino", "firebolt", "presto"):
            module = __import__(f"benchbox.platforms.{adapter_name}", fromlist=["_"])
            adapter_cls = next(v for k, v in vars(module).items() if k.endswith("Adapter") and k[0].isupper())
            mro = adapter_cls.__mro__
            for method_name in CursorValidationQueryExecutionMixin._REQUIRED_METHODS:
                provider = next(c for c in mro if method_name in c.__dict__)
                assert provider is not CursorValidationQueryExecutionMixin, (
                    f"{adapter_cls.__name__}.{method_name} resolves to the mixin - "
                    f"expected PlatformAdapter or a concrete parent"
                )


@pytest.mark.unit
class TestCursorValidationMixinSubclassEnforcement:
    def test_subclass_without_provider_raises_type_error(self):
        with pytest.raises(TypeError, match="does not inherit a concrete implementation"):

            class BrokenAdapter(CursorValidationQueryExecutionMixin):
                pass

    def test_intermediate_mixin_skips_enforcement(self):

        class IntermediateMixin(CursorValidationQueryExecutionMixin):
            pass

    def test_valid_subclass_passes_enforcement(self):

        class ValidAdapter(CursorValidationQueryExecutionMixin, _FakeParent):
            pass


@pytest.mark.unit
class TestCursorValidationExecuteQuery:
    def test_execute_query_returns_success_with_valid_parent(self):

        class TestAdapter(CursorValidationQueryExecutionMixin, _FakeParent):
            pass

        adapter = TestAdapter()
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [(1, "row")]

        result = adapter.execute_query(mock_conn, "SELECT 1", "q1")
        assert result["status"] == "SUCCESS"
        assert result["query_id"] == "q1"

    def test_build_query_stats_and_attach_query_stats_share_payload(self):

        class TestAdapter(CursorValidationQueryExecutionMixin, _FakeParent):
            pass

        adapter = TestAdapter()
        stats = adapter._build_query_stats(1.25)
        result = {"query_id": "q1"}

        adapter._attach_query_stats(result, stats)

        assert stats == {"execution_time_seconds": 1.25}
        assert result["query_statistics"] is stats
        assert result["resource_usage"] is stats

    def test_execute_query_logs_row_count_warning(self):

        class TestAdapter(CursorValidationQueryExecutionMixin, _FakeParent):
            def __init__(self) -> None:
                self.verbose_messages: list[str] = []

            def log_verbose(self, msg: str) -> None:
                self.verbose_messages.append(msg)

        adapter = TestAdapter()
        connection = Mock()
        cursor = Mock()
        connection.cursor.return_value = cursor
        cursor.fetchall.return_value = [(1,)]

        with patch(
            "benchbox.core.validation.query_validation.QueryValidator.validate_query_result",
            return_value=SimpleNamespace(
                warning_message="row count differs",
                is_valid=True,
                error_message=None,
                expected_row_count=1,
            ),
        ):
            result = adapter.execute_query(connection, "SELECT 1", "q1", benchmark_type="tpch", scale_factor=0.01)

        assert result["status"] == "SUCCESS"
        assert adapter.verbose_messages == ["Row count validation: row count differs"]
        cursor.close.assert_called_once()

    def test_execute_query_logs_validation_failure(self):

        class TestAdapter(CursorValidationQueryExecutionMixin, _FakeParent):
            def __init__(self) -> None:
                self.verbose_messages: list[str] = []

            def log_verbose(self, msg: str) -> None:
                self.verbose_messages.append(msg)

        adapter = TestAdapter()
        connection = Mock()
        cursor = Mock()
        connection.cursor.return_value = cursor
        cursor.fetchall.return_value = [(1,)]

        with patch(
            "benchbox.core.validation.query_validation.QueryValidator.validate_query_result",
            return_value=SimpleNamespace(
                warning_message=None,
                is_valid=False,
                error_message="wrong row count",
                expected_row_count=2,
            ),
        ):
            adapter.execute_query(connection, "SELECT 1", "q1", benchmark_type="tpch")

        assert adapter.verbose_messages == ["Row count validation FAILED: wrong row count"]

    def test_execute_query_logs_validation_success(self):

        class TestAdapter(CursorValidationQueryExecutionMixin, _FakeParent):
            def __init__(self) -> None:
                self.very_verbose_messages: list[str] = []

            def log_very_verbose(self, msg: str) -> None:
                self.very_verbose_messages.append(msg)

        adapter = TestAdapter()
        connection = Mock()
        cursor = Mock()
        connection.cursor.return_value = cursor
        cursor.fetchall.return_value = [(1,), (2,)]

        with patch(
            "benchbox.core.validation.query_validation.QueryValidator.validate_query_result",
            return_value=SimpleNamespace(
                warning_message=None,
                is_valid=True,
                error_message=None,
                expected_row_count=2,
            ),
        ):
            adapter.execute_query(connection, "SELECT 1", "q1", benchmark_type="tpch")

        assert adapter.very_verbose_messages == ["Row count validation PASSED: 2 rows (expected: 2)"]

    def test_execute_query_returns_failed_result_on_exception(self):

        class TestAdapter(CursorValidationQueryExecutionMixin, _FakeParent):
            pass

        adapter = TestAdapter()
        connection = Mock()
        cursor = Mock()
        connection.cursor.return_value = cursor
        cursor.execute.side_effect = RuntimeError("boom")

        with patch("benchbox.core.benchmark_mixins.elapsed_seconds", return_value=0.5):
            result = adapter.execute_query(connection, "SELECT 1", "q1")

        assert result == {
            "query_id": "q1",
            "status": "FAILED",
            "execution_time_seconds": 0.5,
            "rows_returned": 0,
            "error": "boom",
            "error_type": "RuntimeError",
        }
        cursor.close.assert_called_once()


class TestDataGenerationMixin:
    def test_generate_data_defaults_to_all_tables(self):
        benchmark = _DataGenerationBenchmark()
        expected = {"customer": Path("customer.csv")}
        benchmark.data_generator.generate_data.return_value = expected

        result = benchmark.generate_data()

        assert result is expected
        benchmark.data_generator.generate_data.assert_called_once_with(["customer", "orders"])

    def test_generate_data_rejects_invalid_output_format(self):
        benchmark = _DataGenerationBenchmark()

        with pytest.raises(ValueError, match="Unsupported output format"):
            benchmark.generate_data(output_format="parquet")

    def test_generate_data_rejects_unknown_tables(self):
        benchmark = _DataGenerationBenchmark()

        with pytest.raises(ValueError, match="Invalid table names"):
            benchmark.generate_data(tables=["missing"])

    def test_load_data_to_database_requires_generated_tables(self):
        benchmark = _DataGenerationBenchmark()

        with pytest.raises(ValueError, match="No data generated"):
            benchmark.load_data_to_database(Mock())

    def test_load_data_to_database_uses_executescript_and_executemany(self, tmp_path: Path):
        benchmark = _DataGenerationBenchmark()
        customer_csv = tmp_path / "customer.csv"
        customer_csv.write_text("1|alice\n2|bob\n")
        benchmark.tables = {"customer": customer_csv}

        connection = Mock(spec=["executescript", "executemany", "commit"])

        benchmark.load_data_to_database(connection)

        connection.executescript.assert_called_once()
        connection.executemany.assert_called_once_with(
            "INSERT INTO customer VALUES (?,?)",
            [["1", "alice"], ["2", "bob"]],
        )
        connection.commit.assert_called_once()

    def test_load_data_to_database_batches_rows_when_batch_size_is_set(self, tmp_path: Path):
        benchmark = _DataGenerationBenchmark(batch_size=2)
        orders_csv = tmp_path / "orders.csv"
        orders_csv.write_text("1|10\n2|20\n3|30\n")
        benchmark.tables = {"orders": orders_csv}

        connection = Mock(spec=["executescript", "executemany", "commit"])

        benchmark.load_data_to_database(connection)

        assert connection.executemany.call_args_list == [
            (("INSERT INTO orders VALUES (?,?)", [["1", "10"], ["2", "20"]]),),
            (("INSERT INTO orders VALUES (?,?)", [["3", "30"]]),),
        ]

    def test_load_data_to_database_uses_cursor_fallbacks(self, tmp_path: Path):
        benchmark = _DataGenerationBenchmark()
        customer_csv = tmp_path / "customer.csv"
        customer_csv.write_text("1|alice\n")
        benchmark.tables = {"customer": customer_csv}
        cursor = Mock()

        class CursorOnlyConnection:
            def __init__(self, inner_cursor: Mock) -> None:
                self._cursor = inner_cursor
                self.committed = False

            def cursor(self) -> Mock:
                return self._cursor

            def commit(self) -> None:
                self.committed = True

        connection = CursorOnlyConnection(cursor)

        benchmark.load_data_to_database(connection)

        assert cursor.execute.call_args_list == [
            (("CREATE TABLE customer (id INT, name TEXT)",),),
            (("CREATE TABLE orders (id INT, amount INT)",),),
            (("INSERT INTO customer VALUES (?,?)", ["1", "alice"]),),
        ]
        assert connection.committed is True


class TestFacadeMixins:
    def test_category_and_query_facades_delegate_to_impl(self):
        benchmark = _FacadeBenchmark()
        benchmark._impl.get_queries_by_category.return_value = {"Q1": "select 1"}
        benchmark._impl.get_query_categories.return_value = ["power"]
        benchmark._impl.get_operations_by_category.return_value = {"op1": {"kind": "write"}}
        benchmark._impl.get_operation_categories.return_value = ["writes"]
        benchmark._impl.get_queries.return_value = {"Q1": "select 1"}
        benchmark._impl.get_query.return_value = "select 1"

        assert benchmark.get_queries_by_category("power") == {"Q1": "select 1"}
        assert benchmark.get_query_categories() == ["power"]
        assert benchmark.get_operations_by_category("writes") == {"op1": {"kind": "write"}}
        assert benchmark.get_operation_categories() == ["writes"]
        assert benchmark.get_queries(dialect="duckdb") == {"Q1": "select 1"}
        assert benchmark.get_query("Q1", params={"x": 1}, dialect="duckdb") == "select 1"

        benchmark._impl.get_queries_by_category.assert_called_once_with("power")
        benchmark._impl.get_query_categories.assert_called_once_with()
        benchmark._impl.get_operations_by_category.assert_called_once_with("writes")
        benchmark._impl.get_operation_categories.assert_called_once_with()
        benchmark._impl.get_queries.assert_called_once_with(dialect="duckdb")
        benchmark._impl.get_query.assert_called_once_with("Q1", params={"x": 1}, dialect="duckdb")

    def test_query_facade_omits_params_when_none(self):
        benchmark = _FacadeBenchmark()
        benchmark._impl.get_query.return_value = "select 2"

        assert benchmark.get_query("Q2", dialect="sqlite") == "select 2"
        benchmark._impl.get_query.assert_called_once_with("Q2", dialect="sqlite")


def _reference_rows(
    path: Path, delimiter: str, num_columns: int, null_flags: list[bool], pad: bool, skip_header: bool
) -> list:
    import csv

    rows = []
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle, delimiter=delimiter)
        if skip_header:
            next(reader, None)
        for row in reader:
            if pad:
                row = (row + [None] * num_columns)[:num_columns]
            if len(row) != num_columns:
                continue
            rows.append([None if flag and value == "" else value for value, flag in zip(row, null_flags)])
    return rows


def _table_fingerprint(connection: Any, table: str, num_columns: int) -> tuple:
    count = connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    sums = [
        connection.execute(
            f"SELECT coalesce(sum(hash(c{i})), 0), count(c{i}) FROM (SELECT c{i}::VARCHAR AS c{i} FROM {table})"
        ).fetchone()
        for i in range(num_columns)
    ]
    return count, sums


class TestDuckDBInsertFromCsv:
    @pytest.mark.parametrize(
        ("delimiter", "header", "null_flags", "trailing"),
        [
            ("|", False, [False] * 4, False),
            (",", True, [False, False, False, False], False),
            ("|", False, [True] * 4, True),
            ("|", False, [False, True, True, False], False),
        ],
    )
    def test_matches_python_row_loop(self, tmp_path: Path, delimiter, header, null_flags, trailing):
        duckdb = pytest.importorskip("duckdb")
        from benchbox.core.benchmark_mixins import duckdb_insert_from_csv

        end = delimiter if trailing else ""
        lines = ["a,b,c,d"] if header else []
        for i in range(500):
            quoted = f'"q{delimiter}{i}"' if i % 7 == 0 else f"v{i}"
            second = "" if i % 5 == 0 else str(i * 3)
            third = "" if i % 11 == 0 else f"{i}.25"
            lines.append(delimiter.join([str(i), quoted, second, third]) + end)
        lines.append(delimiter.join(["short", "row"]) + end)
        if not trailing:
            lines.append(delimiter.join(["1", "2", "3", "4", "5", "6"]))
        path = tmp_path / "data.dat"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        connection = duckdb.connect()
        connection.execute("CREATE TABLE fast (c0 VARCHAR, c1 VARCHAR, c2 VARCHAR, c3 VARCHAR)")
        connection.execute("CREATE TABLE ref (c0 VARCHAR, c1 VARCHAR, c2 VARCHAR, c3 VARCHAR)")

        loaded = duckdb_insert_from_csv(
            connection,
            "fast",
            path,
            4,
            delimiter=delimiter,
            skip_header=header,
            null_if_empty=null_flags,
            trailing_delimiter=trailing,
            skip_malformed=not trailing,
        )
        expected = _reference_rows(path, delimiter, 4, null_flags, trailing, header)
        connection.executemany("INSERT INTO ref VALUES (?, ?, ?, ?)", expected)

        assert loaded == len(expected) == (501 if trailing else 500)
        assert _table_fingerprint(connection, "fast", 4) == _table_fingerprint(connection, "ref", 4)

    def test_returns_none_for_non_duckdb_connection(self, tmp_path: Path):
        from benchbox.core.benchmark_mixins import duckdb_insert_from_csv

        assert duckdb_insert_from_csv(Mock(), "t", tmp_path / "x.csv", 1) is None

    def test_strict_mode_raises_on_malformed_rows(self, tmp_path: Path):
        duckdb = pytest.importorskip("duckdb")
        from benchbox.core.benchmark_mixins import duckdb_insert_from_csv

        path = tmp_path / "bad.dat"
        path.write_text("1|2\n3\n", encoding="utf-8")
        connection = duckdb.connect()
        connection.execute("CREATE TABLE t (a VARCHAR, b VARCHAR)")

        with pytest.raises(duckdb.Error):
            duckdb_insert_from_csv(connection, "t", path, 2, skip_malformed=False)

    def test_padding_mode_raises_instead_of_dropping_overlong_rows(self, tmp_path: Path):
        duckdb = pytest.importorskip("duckdb")
        from benchbox.core.benchmark_mixins import duckdb_insert_from_csv

        path = tmp_path / "long.dat"
        path.write_text("1|2|\n1|2|3|4|\n", encoding="utf-8")
        connection = duckdb.connect()
        connection.execute("CREATE TABLE t (a VARCHAR, b VARCHAR)")

        with pytest.raises(duckdb.Error):
            duckdb_insert_from_csv(connection, "t", path, 2, trailing_delimiter=True, skip_malformed=False)


