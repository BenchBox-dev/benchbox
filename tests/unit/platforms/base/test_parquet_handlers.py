import builtins
from pathlib import Path
from unittest.mock import Mock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from benchbox.platforms.base.data_loading import (
    DuckDBParquetHandler,
    FileFormatRegistry,
    ParquetFileHandler,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestParquetFileHandler:
    @pytest.fixture
    def handler(self):
        return ParquetFileHandler()

    @pytest.fixture
    def temp_parquet_file(self, tmp_path):

        data = {
            "id": [1, 2, 3],
            "name": ["Alice", "Bob", "Charlie"],
            "amount": [100.50, 200.75, 300.00],
        }
        table = pa.table(data)

        parquet_file = tmp_path / "test.parquet"
        pq.write_table(table, parquet_file)

        return parquet_file

    def test_get_delimiter(self, handler):

        assert handler.get_delimiter() == ""

    def test_load_table_basic(self, handler, temp_parquet_file):

        connection = Mock()
        connection.executemany = Mock()

        logger = Mock()
        benchmark = Mock()

        row_count = handler.load_table(
            "test_table",
            temp_parquet_file,
            connection,
            benchmark,
            logger,
        )

        assert row_count == 3

        assert connection.executemany.called

        call_args = connection.executemany.call_args[0]
        insert_sql = call_args[0]
        assert "INSERT INTO test_table" in insert_sql
        assert "(id,name,amount)" in insert_sql
        assert "VALUES (?,?,?)" in insert_sql

        data_tuples = call_args[1]
        assert len(data_tuples) == 3
        assert data_tuples[0] == (1, "Alice", 100.50)
        assert data_tuples[1] == (2, "Bob", 200.75)
        assert data_tuples[2] == (3, "Charlie", 300.00)

    def test_load_table_empty_file(self, handler, tmp_path):

        empty_data = {"id": [], "name": []}
        table = pa.table(empty_data)
        parquet_file = tmp_path / "empty.parquet"
        pq.write_table(table, parquet_file)

        connection = Mock()
        logger = Mock()
        benchmark = Mock()

        row_count = handler.load_table(
            "test_table",
            parquet_file,
            connection,
            benchmark,
            logger,
        )

        assert row_count == 0
        assert not connection.executemany.called

    def test_load_table_large_file(self, handler, tmp_path):

        data = {
            "id": list(range(2500)),
            "value": [f"value_{i}" for i in range(2500)],
        }
        table = pa.table(data)
        parquet_file = tmp_path / "large.parquet"
        pq.write_table(table, parquet_file)

        connection = Mock()
        connection.executemany = Mock()
        logger = Mock()
        benchmark = Mock()

        row_count = handler.load_table(
            "test_table",
            parquet_file,
            connection,
            benchmark,
            logger,
        )

        assert row_count == 2500

        assert connection.executemany.call_count == 3

        calls = connection.executemany.call_args_list
        assert len(calls[0][0][1]) == 1000
        assert len(calls[1][0][1]) == 1000
        assert len(calls[2][0][1]) == 500

    def test_load_table_pyarrow_not_installed(self, handler, tmp_path, monkeypatch):

        real_import = builtins.__import__

        def mock_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name.startswith("pyarrow"):
                raise ImportError("pyarrow not found")
            return real_import(name, globals, locals, fromlist, level)

        monkeypatch.setattr("builtins.__import__", mock_import)

        connection = Mock()
        logger = Mock()
        benchmark = Mock()
        parquet_file = tmp_path / "test.parquet"

        with pytest.raises(RuntimeError, match="pyarrow is required"):
            handler_new = ParquetFileHandler()
            handler_new.load_table("test_table", parquet_file, connection, benchmark, logger)


class TestDuckDBParquetHandler:
    @pytest.fixture
    def adapter(self):
        adapter = Mock()
        adapter.dry_run_mode = False
        return adapter

    @pytest.fixture
    def handler(self, adapter):
        return DuckDBParquetHandler(adapter)

    def test_get_delimiter(self, handler):

        assert handler.get_delimiter() == ""

    def test_load_table_normal_mode(self, handler, tmp_path):

        parquet_file = tmp_path / "test.parquet"
        parquet_file.touch()

        connection = Mock()
        before_result = Mock()
        before_result.fetchone.return_value = (0,)
        insert_result = Mock()
        after_result = Mock()
        after_result.fetchone.return_value = (1000,)
        connection.execute.side_effect = [before_result, insert_result, after_result]

        logger = Mock()
        benchmark = Mock()

        row_count = handler.load_table(
            "customer",
            parquet_file,
            connection,
            benchmark,
            logger,
        )

        assert row_count == 1000

        assert connection.execute.call_count == 3

        insert_call = connection.execute.call_args_list[1]
        insert_sql = insert_call[0][0]
        assert "INSERT INTO customer" in insert_sql
        assert f"read_parquet('{parquet_file}')" in insert_sql

    def test_load_table_dry_run_mode(self, handler, adapter, tmp_path):

        adapter.dry_run_mode = True
        adapter.capture_sql = Mock()

        parquet_file = tmp_path / "test.parquet"
        parquet_file.touch()

        connection = Mock()
        logger = Mock()
        benchmark = Mock()

        row_count = handler.load_table(
            "customer",
            parquet_file,
            connection,
            benchmark,
            logger,
        )

        assert row_count == 1000

        assert adapter.capture_sql.called
        call_args = adapter.capture_sql.call_args
        assert "INSERT INTO customer" in call_args[0][0]
        assert f"read_parquet('{parquet_file}')" in call_args[0][0]
        assert call_args[0][1] == "load_data"
        assert call_args[0][2] == "customer"

        assert not connection.execute.called


class TestDuckDBParquetHandlerBulk:
    @pytest.fixture
    def adapter(self):
        adapter = Mock()
        adapter.dry_run_mode = False
        return adapter

    @pytest.fixture
    def handler(self, adapter):
        return DuckDBParquetHandler(adapter)

    def _make_bulk_connection(self, total_rows: int) -> Mock:
        connection = Mock()
        before_result = Mock()
        before_result.fetchone.return_value = (0,)
        insert_result = Mock()
        after_result = Mock()
        after_result.fetchone.return_value = (total_rows,)
        connection.execute.side_effect = [before_result, insert_result, after_result]
        return connection

    def test_bulk_load_two_shards_uses_array_syntax(self, handler, tmp_path):
        shards = [tmp_path / f"customer.parquet.{i}" for i in range(1, 3)]
        for shard in shards:
            shard.touch()

        connection = self._make_bulk_connection(total_rows=2000)

        result = handler.load_table_bulk("customer", shards, connection, Mock(), Mock())

        assert result == 2000
        assert connection.execute.call_count == 3
        insert_sql = connection.execute.call_args_list[1][0][0]
        assert "read_parquet([" in insert_sql
        for shard in shards:
            assert str(shard) in insert_sql

    def test_bulk_load_dry_run_returns_placeholder(self, adapter, tmp_path):
        adapter.dry_run_mode = True
        adapter.capture_sql = Mock()
        handler = DuckDBParquetHandler(adapter)

        shards = [tmp_path / f"orders.parquet.{i}" for i in range(1, 4)]
        for shard in shards:
            shard.touch()

        connection = Mock()
        result = handler.load_table_bulk("orders", shards, connection, Mock(), Mock())

        assert result == 3000
        adapter.capture_sql.assert_called_once()
        assert not connection.execute.called

    def test_bulk_load_single_shard_delegates_to_load_table(self, handler, tmp_path):
        shard = tmp_path / "lineitem.parquet"
        shard.touch()

        connection = self._make_bulk_connection(total_rows=6001215)

        result = handler.load_table_bulk("lineitem", [shard], connection, Mock(), Mock())

        assert result == 6001215


class TestFileFormatRegistry:
    def test_parquet_format_registered(self):

        handler = FileFormatRegistry.get_handler(Path("test.parquet"))
        assert handler is not None
        assert isinstance(handler, ParquetFileHandler)

    def test_get_base_data_extension_parquet(self):

        assert FileFormatRegistry.get_base_data_extension(Path("customer.parquet")) == ".parquet"

        assert FileFormatRegistry.get_base_data_extension(Path("customer.parquet")) == ".parquet"

    def test_parquet_handler_returned_for_parquet_files(self):

        handler = FileFormatRegistry.get_handler(Path("/data/customer.parquet"))
        assert isinstance(handler, ParquetFileHandler)
        assert handler.get_delimiter() == ""


class TestParquetIntegration:
    def test_end_to_end_parquet_loading(self, tmp_path):

        data = {
            "c_custkey": [1, 2, 3, 4, 5],
            "c_name": ["Customer#1", "Customer#2", "Customer#3", "Customer#4", "Customer#5"],
            "c_acctbal": [100.50, 200.75, 300.00, 400.25, 500.50],
        }
        table = pa.table(data)
        parquet_file = tmp_path / "customer.parquet"
        pq.write_table(table, parquet_file)

        handler = FileFormatRegistry.get_handler(parquet_file)
        assert isinstance(handler, ParquetFileHandler)

        connection = Mock()
        connection.executemany = Mock()
        logger = Mock()
        benchmark = Mock()

        row_count = handler.load_table(
            "customer",
            parquet_file,
            connection,
            benchmark,
            logger,
        )

        assert row_count == 5
        assert connection.executemany.called

        call_args = connection.executemany.call_args[0]
        data_tuples = call_args[1]
        assert len(data_tuples) == 5
        assert data_tuples[0] == (1, "Customer#1", 100.50)
        assert data_tuples[4] == (5, "Customer#5", 500.50)


