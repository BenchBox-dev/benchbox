# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License.

import contextlib
import tempfile
from pathlib import Path
from unittest.mock import Mock

import pytest

from benchbox.core.base_benchmark import BaseBenchmark
from benchbox.platforms.sqlite import SQLiteAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestBaseBenchmarkProperties:
    def test_concrete_benchmark_properties(self):

        class MockBenchmark(BaseBenchmark):
            def generate_data(self, tables=None, output_format="memory"):
                return {"table1": []}

            def get_query(self, query_id):
                return f"SELECT * FROM table WHERE id = {query_id}"

            def get_all_queries(self):
                return {1: "SELECT 1", 2: "SELECT 2"}

            def execute_query(self, query_id, connection, params=None):
                return [(1, "test")]

        benchmark = MockBenchmark(scale_factor=1.0)

        name = benchmark.name
        version = benchmark.version
        description = benchmark.description

        assert name == "mock"
        assert version == "1.0"
        assert "MOCK benchmark implementation" in description

    def test_benchmark_with_explicit_metadata(self):

        class ExplicitBenchmark(BaseBenchmark):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self._name = "custom_name"
                self._version = "2.1"
                self._description = "Custom description"

            def generate_data(self, tables=None, output_format="memory"):
                return {}

            def get_query(self, query_id):
                return "SELECT 1"

            def get_all_queries(self):
                return {}

            def execute_query(self, query_id, connection, params=None):
                return []

        benchmark = ExplicitBenchmark()

        assert benchmark.name == "custom_name"
        assert benchmark.version == "2.1"
        assert benchmark.description == "Custom description"


class TestPlatformDropDatabase:
    def test_sqlite_drop_database_documented_limitation(self):
        adapter = SQLiteAdapter()

        with pytest.raises(NotImplementedError, match="drop_database not implemented"):
            adapter.drop_database()

    def test_sqlite_tpc_tests_documented_limitations(self):
        adapter = SQLiteAdapter()
        mock_benchmark = Mock()

        with pytest.raises(NotImplementedError, match="Power test not implemented for SQLite"):
            adapter.run_power_test(mock_benchmark)

        with pytest.raises(NotImplementedError, match="Throughput test not implemented for SQLite"):
            adapter.run_throughput_test(mock_benchmark)

        with pytest.raises(NotImplementedError, match="Maintenance test not implemented for SQLite"):
            adapter.run_maintenance_test(mock_benchmark)


class TestDocumentedNotImplementedMethods:
    def test_stream_generation_apis_documented(self):
        from benchbox.core.tpcds.benchmark import TPCDSBenchmark

        tpcds = TPCDSBenchmark(scale_factor=1.0)

        assert callable(getattr(tpcds, "generate_streams", None))
        assert callable(getattr(tpcds, "get_stream_info", None))

    def test_non_executing_stream_stubs_now_raise_not_implemented(self):
        from benchbox.core.tpcds.benchmark import TPCDSBenchmark
        from benchbox.core.tpch.streams import TPCHStreamRunner

        runner = TPCHStreamRunner("test://connection", verbose=False)
        tpcds = TPCDSBenchmark(scale_factor=1.0)

        assert callable(getattr(runner, "run_stream", None))
        assert callable(getattr(runner, "run_concurrent_streams", None))
        assert callable(getattr(tpcds, "run_streams", None))

        with pytest.raises(NotImplementedError):
            runner.run_stream(Path("/fake/stream.sql"), stream_id=0)
        with pytest.raises(NotImplementedError):
            runner.run_concurrent_streams([])
        with pytest.raises(NotImplementedError):
            tpcds.run_streams(connection=Mock())


class TestNoPlatformNotImplementedErrors:
    def test_sqlite_adapter_basic_operations(self):
        adapter = SQLiteAdapter()

        platform_name = adapter.platform_name
        assert isinstance(platform_name, str)

        dialect = adapter.get_target_dialect()
        assert isinstance(dialect, str)

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
            connection_config = {"database": tmp_db.name}

            try:
                connection = adapter.create_connection(**connection_config)
                assert connection is not None

                adapter.close_connection(connection)

            finally:
                with contextlib.suppress(Exception):
                    Path(tmp_db.name).unlink()


class TestErrorMessagesQuality:
    def test_base_class_error_messages_helpful(self):
        from benchbox.base import BaseBenchmark

        class IncompleteBenchmark(BaseBenchmark):
            def generate_data(self):
                return []

            def get_queries(self):
                return {}

            def get_query(self, query_id, params=None):
                return "SELECT 1"

        benchmark = IncompleteBenchmark(scale_factor=1.0)
        mock_connection = Mock()

        with pytest.raises(NotImplementedError) as excinfo:
            benchmark._load_data(mock_connection)

        error_msg = str(excinfo.value)
        assert "IncompleteBenchmark" in error_msg
        assert "_load_data" in error_msg
        assert "database execution functionality" in error_msg


@pytest.mark.integration
class TestNotImplementedErrorResolution:
    def test_no_runtime_notimplementederrors_in_basic_usage(self):
        from benchbox import TPCH

        benchmark = TPCH(scale_factor=0.01)

        queries = benchmark.get_queries()
        assert isinstance(queries, dict)

        query1 = benchmark.get_query(1)
        assert isinstance(query1, str)

        data_files = benchmark.generate_data()
        assert isinstance(data_files, list)

    def test_platform_adapter_integration_no_notimplementederrors(self):
        from benchbox import TPCH
        from benchbox.platforms.sqlite import SQLiteAdapter

        TPCH(scale_factor=0.01)
        adapter = SQLiteAdapter()

        platform_name = adapter.platform_name
        assert isinstance(platform_name, str)

        dialect = adapter.get_target_dialect()
        assert isinstance(dialect, str)

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
            connection_config = {"database": tmp_db.name}

            try:
                connection = adapter.create_connection(**connection_config)
                assert connection is not None

                adapter.close_connection(connection)

            finally:
                with contextlib.suppress(Exception):
                    Path(tmp_db.name).unlink()


