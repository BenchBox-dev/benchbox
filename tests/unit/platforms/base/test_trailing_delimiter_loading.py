import sys
import sys as _sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from benchbox.platforms.base.data_loading import DuckDBNativeHandler

__import__("benchbox.cli.commands.run")
_run_module = _sys.modules["benchbox.cli.commands.run"]

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _make_pipe_file(tmp_path: Path, filename: str, lines: list[str]) -> Path:

    file_path = tmp_path / filename
    file_path.write_text("\n".join(lines) + "\n")
    return file_path


def _make_handler() -> DuckDBNativeHandler:

    adapter = Mock()
    adapter.dry_run_mode = False
    benchmark = Mock(spec=[])
    return DuckDBNativeHandler(delimiter="|", adapter=adapter, benchmark=benchmark)


def _make_connection(col_names: list[str], row_count: int = 3) -> Mock:

    connection = Mock()

    pragma_result = Mock()
    pragma_result.fetchall.return_value = [(name,) for name in col_names]

    before_result = Mock()
    before_result.fetchone.return_value = (0,)

    insert_result = Mock()

    after_result = Mock()
    after_result.fetchone.return_value = (row_count,)

    connection.execute.side_effect = [pragma_result, before_result, insert_result, after_result]
    return connection


class TestTrailingDelimiterWithPipe:
    def test_load_with_trailing_delimiter(self, tmp_path):

        col_names = ["r_regionkey", "r_name", "r_comment"]

        data_file = _make_pipe_file(
            tmp_path,
            "region.tbl",
            [
                "0|AFRICA|special deposits|",
                "1|AMERICA|furiously even|",
                "2|ASIA|carefully regular|",
            ],
        )

        handler = _make_handler()
        connection = _make_connection(col_names)

        result = handler.load_table("region", data_file, connection, Mock(), Mock())

        assert result == 3

        calls = connection.execute.call_args_list

        insert_call = calls[2][0][0]

        assert '"r_regionkey"' in insert_call
        assert '"r_name"' in insert_call
        assert '"r_comment"' in insert_call
        assert "SELECT *" not in insert_call

        assert "_trailing_delimiter_" in insert_call

        assert "null_padding=true" in insert_call

    def test_load_without_trailing_delimiter(self, tmp_path):

        col_names = ["t_time_sk", "t_time_id", "t_time"]

        data_file = _make_pipe_file(
            tmp_path,
            "time_dim.dat",
            [
                "0|AAAAAAAABAAAAAAA|0",
                "1|AAAAAAAACAAAAAAA|1",
                "2|AAAAAAAADAAAAAAA|2",
            ],
        )

        handler = _make_handler()
        connection = _make_connection(col_names)

        result = handler.load_table("time_dim", data_file, connection, Mock(), Mock())

        assert result == 3
        calls = connection.execute.call_args_list
        insert_call = calls[2][0][0]

        assert '"t_time_sk"' in insert_call
        assert '"t_time_id"' in insert_call
        assert '"t_time"' in insert_call
        assert "SELECT *" not in insert_call

        assert "_trailing_delimiter_" not in insert_call

        assert "null_padding=true" in insert_call

    def test_load_without_trailing_no_binder_error(self, tmp_path):

        col_names = ["ws_sold_date_sk", "ws_item_sk", "ws_order_number"]
        data_file = _make_pipe_file(
            tmp_path,
            "web_sales.dat",
            [
                "2450816|1234|5678",
                "2450817|2345|6789",
            ],
        )

        handler = _make_handler()
        connection = _make_connection(col_names, row_count=2)

        handler.load_table("web_sales", data_file, connection, Mock(), Mock())

        calls = connection.execute.call_args_list
        insert_call = calls[2][0][0]

        assert "EXCLUDE" not in insert_call

    def test_load_zst_compressed_without_trailing(self, tmp_path):

        col_names = ["ss_sold_date_sk", "ss_item_sk"]
        lines = "2450816|1234\n2450817|2345\n"

        zst_path = tmp_path / "store_sales_1_2.dat.zst"
        try:
            import zstandard as zstd

            cctx = zstd.ZstdCompressor()
            zst_path.write_bytes(cctx.compress(lines.encode()))
        except ImportError:
            pytest.skip("zstandard not installed")

        handler = _make_handler()
        connection = _make_connection(col_names, row_count=2)

        result = handler.load_table("store_sales", zst_path, connection, Mock(), Mock())

        assert result == 2
        calls = connection.execute.call_args_list
        insert_call = calls[2][0][0]
        assert "_trailing_delimiter_" not in insert_call
        assert '"ss_sold_date_sk"' in insert_call

    def test_fallback_when_no_columns_detected(self, tmp_path):

        data_file = _make_pipe_file(tmp_path, "unknown.dat", ["a|b|c"])

        handler = _make_handler()
        connection = Mock()

        pragma_result = Mock()
        pragma_result.fetchall.return_value = []

        before_result = Mock()
        before_result.fetchone.return_value = (0,)

        insert_result = Mock()

        after_result = Mock()
        after_result.fetchone.return_value = (1,)

        connection.execute.side_effect = [pragma_result, before_result, insert_result, after_result]

        handler.load_table("unknown_table", data_file, connection, Mock(), Mock())

        calls = connection.execute.call_args_list
        insert_call = calls[2][0][0]
        assert "auto_detect=true" in insert_call
        assert "SELECT *" in insert_call

    def test_multishard_row_count_not_inflated(self, tmp_path):

        col_names = ["o_orderkey", "o_custkey", "o_orderstatus"]
        handler = _make_handler()

        shard_rows = 500
        table_counts = [0, 500, 1000, 1500]

        total = 0
        for i, (before_count, after_count) in enumerate(zip(table_counts, table_counts[1:])):
            shard_file = _make_pipe_file(
                tmp_path,
                f"orders.tbl.{i + 1}",
                [f"{j}|{j}|O" for j in range(shard_rows)],
            )

            pragma_result = Mock()
            pragma_result.fetchall.return_value = [(name,) for name in col_names]

            before_result = Mock()
            before_result.fetchone.return_value = (before_count,)

            insert_result = Mock()

            after_result = Mock()
            after_result.fetchone.return_value = (after_count,)

            connection = Mock()
            connection.execute.side_effect = [pragma_result, before_result, insert_result, after_result]

            rows = handler.load_table("orders", shard_file, connection, Mock(), Mock())
            assert rows == shard_rows, f"Shard {i + 1}: expected {shard_rows}, got {rows}"
            total += rows

        assert total == 1500, f"Total rows should be 1500, got {total}"


class TestDuckDBNativeHandlerBulk:
    def _make_bulk_connection(self, col_names: list[str], total_rows: int) -> Mock:

        connection = Mock()

        pragma_result = Mock()
        pragma_result.fetchall.return_value = [(name,) for name in col_names]

        before_result = Mock()
        before_result.fetchone.return_value = (0,)

        insert_result = Mock()

        after_result = Mock()
        after_result.fetchone.return_value = (total_rows,)

        connection.execute.side_effect = [pragma_result, before_result, insert_result, after_result]
        return connection

    def test_bulk_load_trailing_delimiter_uses_array_syntax(self, tmp_path):

        col_names = ["l_orderkey", "l_partkey", "l_suppkey"]
        shards = [_make_pipe_file(tmp_path, f"lineitem.tbl.{i}", [f"{i}|{i}|{i}|"]) for i in range(1, 4)]

        handler = _make_handler()
        connection = self._make_bulk_connection(col_names, total_rows=3000)

        result = handler.load_table_bulk("lineitem", shards, connection, Mock(), Mock())

        assert result == 3000

        calls = connection.execute.call_args_list
        assert connection.execute.call_count == 4
        insert_sql = calls[2][0][0]

        assert "read_csv([" in insert_sql

        for shard in shards:
            assert str(shard) in insert_sql

        for col in col_names:
            assert f'"{col}"' in insert_sql

        assert "_trailing_delimiter_" in insert_sql

    def test_bulk_load_no_trailing_delimiter_uses_array_syntax(self, tmp_path):

        col_names = ["t_time_sk", "t_time_id", "t_time"]
        shards = [_make_pipe_file(tmp_path, f"time_dim.dat.{i}", [f"{i}|AAAA|{i}"]) for i in range(1, 4)]

        handler = _make_handler()
        connection = self._make_bulk_connection(col_names, total_rows=1500)

        result = handler.load_table_bulk("time_dim", shards, connection, Mock(), Mock())

        assert result == 1500
        calls = connection.execute.call_args_list
        insert_sql = calls[2][0][0]
        assert "read_csv([" in insert_sql
        assert "_trailing_delimiter_" not in insert_sql
        for col in col_names:
            assert f'"{col}"' in insert_sql

    def test_bulk_load_dry_run_captures_sql_and_returns_placeholder(self, tmp_path):

        col_names = ["o_orderkey", "o_custkey"]
        shards = [_make_pipe_file(tmp_path, f"orders.tbl.{i}", [f"{i}|{i}|"]) for i in range(1, 4)]

        adapter = Mock()
        adapter.dry_run_mode = True
        adapter.capture_sql = Mock()
        benchmark = Mock(spec=[])
        handler = DuckDBNativeHandler(delimiter="|", adapter=adapter, benchmark=benchmark)

        connection = Mock()
        pragma_result = Mock()
        pragma_result.fetchall.return_value = [(c,) for c in col_names]
        connection.execute.return_value = pragma_result

        result = handler.load_table_bulk("orders", shards, connection, Mock(), Mock())

        assert result == 3000
        adapter.capture_sql.assert_called_once()

        assert connection.execute.call_count == 1

    def test_bulk_load_single_shard_delegates_to_load_table(self, tmp_path):

        col_names = ["r_regionkey", "r_name", "r_comment"]
        shard = _make_pipe_file(tmp_path, "region.tbl", ["0|AFRICA|desc|"])

        handler = _make_handler()
        connection = self._make_bulk_connection(col_names, total_rows=5)

        result = handler.load_table_bulk("region", [shard], connection, Mock(), Mock())

        assert result == 5


class TestCLIExitCodeOnFailure:
    def test_run_command_exits_nonzero_on_bad_platform(self):

        from click.testing import CliRunner

        from benchbox.cli.commands.run import run

        runner = CliRunner()
        result = runner.invoke(run, ["--platform", "nonexistent_platform", "--benchmark", "tpch", "--non-interactive"])
        assert result.exit_code != 0

    def test_run_command_exits_nonzero_on_failed_validation_status(self):

        from click.testing import CliRunner

        from benchbox.cli.app import cli

        mock_result = Mock()
        mock_result.validation_status = "FAILED"
        mock_result.validation_details = "Simulated failure"

        runner = CliRunner()
        with patch.object(_run_module, "_execute_orchestrated_run", return_value=mock_result):
            result = runner.invoke(
                cli,
                ["run", "--platform", "duckdb", "--benchmark", "tpch", "--non-interactive", "--phases", "power"],
            )

        assert result.exit_code != 0, f"Expected non-zero exit but got {result.exit_code}. Output:\n{result.output}"

    def test_run_command_exits_zero_on_help(self):

        from click.testing import CliRunner

        from benchbox.cli.commands.run import run

        runner = CliRunner()
        result = runner.invoke(run, ["--help"])
        assert result.exit_code == 0


