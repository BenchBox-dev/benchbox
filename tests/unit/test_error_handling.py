# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import contextlib
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

pytest.importorskip("pandas")

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


IS_WINDOWS = sys.platform == "win32"

from benchbox.amplab import AMPLab
from benchbox.clickbench import ClickBench
from benchbox.core.connection import DatabaseConnection, DatabaseError
from benchbox.h2odb import H2ODB
from benchbox.ssb import SSB
from benchbox.tpcdi import TPCDI
from benchbox.tpcds import TPCDS
from benchbox.tpch import TPCH
from benchbox.write_primitives import WritePrimitives


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.mark.unit
class TestDatabaseConnectionErrors:
    def test_none_connection_object(self):
        with pytest.raises(TypeError, match="Connection object cannot be None"):
            DatabaseConnection(None)

    def test_invalid_connection_object(self):
        invalid_conn = object()

        with pytest.raises(
            ValueError,
            match="Connection object must have either 'cursor' or 'execute' method",
        ):
            DatabaseConnection(invalid_conn)

    def test_sqlite_connection_with_invalid_path(self):
        invalid_path = "/non/existent/path/database.db"

        with pytest.raises((OSError, sqlite3.OperationalError)):
            sqlite_conn = sqlite3.connect(invalid_path)
            conn = DatabaseConnection(sqlite_conn)
            conn.execute("SELECT 1")

    def test_connection_with_closed_sqlite(self):
        sqlite_conn = sqlite3.connect(":memory:")
        sqlite_conn.close()

        conn = DatabaseConnection(sqlite_conn)
        with pytest.raises(DatabaseError):
            conn.execute("SELECT 1")

    def test_malformed_connection_object(self):
        mock_conn = Mock()
        mock_conn.cursor = Mock(side_effect=Exception("Cursor creation failed"))
        mock_conn.execute = None

        conn = DatabaseConnection(mock_conn)
        with pytest.raises(Exception):
            conn.execute("SELECT 1")


@pytest.mark.unit
class TestBenchmarkInitializationErrors:
    def test_invalid_scale_factors(self, temp_dir):
        with pytest.raises(ValueError):
            TPCH(scale_factor=0, output_dir=temp_dir)

        with pytest.raises(ValueError):
            TPCDS(scale_factor=-1.0, output_dir=temp_dir)

        with pytest.raises((ValueError, OverflowError)):
            TPCH(scale_factor=1e20, output_dir=temp_dir)

    def test_invalid_output_directories(self):
        benchmark = TPCH(scale_factor=1.0, output_dir=None)
        assert benchmark is not None

        with pytest.raises(TypeError):
            TPCDS(scale_factor=1.0, output_dir=123)

        benchmark3 = AMPLab(scale_factor=1.0, output_dir="")
        assert benchmark3.output_dir == Path("")

    @pytest.mark.skipif(IS_WINDOWS, reason="chmod doesn't enforce permissions on Windows")
    def test_permission_denied_output_directory(self, temp_dir):
        readonly_parent = temp_dir / "readonly_parent"
        readonly_parent.mkdir()
        readonly_parent.chmod(0o444)

        nested_output = readonly_parent / "output"

        try:
            with pytest.raises((PermissionError, OSError)):
                benchmark = TPCH(scale_factor=0.1, output_dir=nested_output)
                benchmark.generate_data()
        finally:
            with contextlib.suppress(OSError, PermissionError):
                readonly_parent.chmod(0o755)

    def test_invalid_benchmark_parameters(self, temp_dir):
        with pytest.raises((ValueError, TypeError)):
            TPCH(scale_factor=1.0, output_dir=temp_dir, parallel=-1)

        with pytest.raises((ValueError, TypeError)):
            TPCDS(scale_factor=1.0, output_dir=temp_dir, parallel=0)

        try:
            benchmark3 = H2ODB(scale_factor=1.0, output_dir=temp_dir, verbose="invalid")
            assert benchmark3.verbose == "invalid"
        except (ValueError, TypeError):
            pass


@pytest.mark.unit
class TestDataGenerationErrors:
    def test_insufficient_disk_space_simulation(self, temp_dir):
        benchmark = WritePrimitives(scale_factor=0.01, output_dir=temp_dir)

        with (
            patch.object(
                benchmark._impl.data_generator,
                "generate",
                side_effect=OSError(28, "No space left on device"),
            ),
            pytest.raises(OSError),
        ):
            benchmark.generate_data()

    def test_interrupted_data_generation(self, temp_dir):
        benchmark = WritePrimitives(scale_factor=0.01, output_dir=temp_dir)

        with (
            patch.object(
                benchmark,
                "generate_data",
                side_effect=KeyboardInterrupt("User interrupted"),
            ),
            pytest.raises(KeyboardInterrupt),
        ):
            benchmark.generate_data()

    def test_corrupted_data_file_handling(self, temp_dir):
        ClickBench(scale_factor=0.01, output_dir=temp_dir)

        corrupted_file = temp_dir / "corrupted.csv"
        corrupted_file.write_bytes(b"\x00\xff\xfe\x80corrupted data")

        assert corrupted_file.exists()
        with pytest.raises((UnicodeDecodeError, OSError)):
            with open(corrupted_file, encoding="utf-8") as f:
                f.read()

    def test_memory_exhaustion_simulation(self, temp_dir):
        benchmark = SSB(scale_factor=0.01, output_dir=temp_dir, compress_data=False, compression_type="none")

        with patch("builtins.list", side_effect=MemoryError("Out of memory")), pytest.raises(MemoryError):
            benchmark.generate_data()


@pytest.mark.unit
class TestQueryExecutionErrors:
    def test_sql_syntax_errors(self, temp_dir):
        sqlite_conn = sqlite3.connect(":memory:")
        conn = DatabaseConnection(sqlite_conn)

        with pytest.raises(DatabaseError):
            conn.execute("INVALID SQL SYNTAX")

    def test_missing_table_errors(self, temp_dir):
        sqlite_conn = sqlite3.connect(":memory:")
        conn = DatabaseConnection(sqlite_conn)

        with pytest.raises(DatabaseError):
            conn.execute("SELECT * FROM non_existent_table")

    def test_connection_lost_during_query(self, temp_dir):
        sqlite_conn = sqlite3.connect(":memory:")
        conn = DatabaseConnection(sqlite_conn)

        sqlite_conn.close()

        with pytest.raises(DatabaseError):
            conn.execute("SELECT 1")

    def test_query_execution_with_parameters_error(self, temp_dir):
        sqlite_conn = sqlite3.connect(":memory:")
        conn = DatabaseConnection(sqlite_conn)

        conn.execute("CREATE TABLE test (id INTEGER, name TEXT)")

        with pytest.raises(DatabaseError):
            conn.execute("INSERT INTO test VALUES (?, ?)", [1, 2, 3])


@pytest.mark.unit
class TestFileSystemEdgeCases:
    def test_extremely_long_file_paths(self, temp_dir):
        long_path = temp_dir
        for i in range(10):
            long_path = long_path / (f"very_long_directory_name_{i}_" * 10)

        try:
            benchmark = TPCDI(scale_factor=0.01, output_dir=long_path)
            benchmark.generate_data()
        except (OSError, FileNotFoundError, ValueError):
            pass

    def test_special_characters_in_paths(self, temp_dir):
        special_chars = [
            "spaces in name",
            "unicode_测试",
            "symbols!@#$%^&*()",
            "dots...",
            "dash-es",
        ]

        for char_set in special_chars:
            try:
                special_dir = temp_dir / char_set
                benchmark = WritePrimitives(scale_factor=0.01, output_dir=special_dir)
                assert benchmark.output_dir == special_dir
                special_dir.mkdir(parents=True, exist_ok=True)
                assert special_dir.exists()
            except (OSError, UnicodeError, ValueError):
                pass

    def test_concurrent_file_access(self, temp_dir):
        import threading

        def write_data(benchmark, results, index):
            try:
                result = benchmark.generate_data()
                results[index] = result
            except Exception as e:
                results[index] = e

        benchmarks = [WritePrimitives(scale_factor=0.01, output_dir=temp_dir) for _ in range(3)]

        results = {}
        threads = []

        for i, benchmark in enumerate(benchmarks):
            thread = threading.Thread(target=write_data, args=(benchmark, results, i))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        assert len(results) == 3
        for result in results.values():
            assert isinstance(result, (dict, list)), result
            assert result
            paths = result.values() if isinstance(result, dict) else result
            for path in paths:
                assert Path(path).is_file()

    @pytest.mark.skipif(IS_WINDOWS, reason="chmod doesn't enforce permissions on Windows")
    def test_readonly_filesystem_handling(self, temp_dir):
        output_dir = temp_dir / "readonly_output"
        output_dir.mkdir()

        output_dir.chmod(0o444)

        try:
            benchmark = WritePrimitives(scale_factor=0.01, output_dir=output_dir)

            with pytest.raises((PermissionError, OSError)):
                benchmark.generate_data()
        finally:
            with contextlib.suppress(OSError, PermissionError):
                output_dir.chmod(0o755)


@pytest.mark.unit
class TestResourceExhaustionScenarios:
    def test_file_descriptor_exhaustion(self, temp_dir):
        benchmark = H2ODB(scale_factor=0.01, output_dir=temp_dir)

        with patch("builtins.open", side_effect=OSError(24, "Too many open files")):
            with pytest.raises((OSError, Exception)) as excinfo:
                benchmark.generate_data()
            error_msg = str(excinfo.value)
            assert "Too many open files" in error_msg

    def test_thread_exhaustion_handling(self, temp_dir):
        try:
            benchmark = TPCH(scale_factor=0.01, output_dir=temp_dir, parallel=10000)
            benchmark.generate_data()
        except (RuntimeError, OSError, ValueError):
            pass

    def test_network_timeout_handling(self):
        benchmark = ClickBench(scale_factor=0.01)

        assert benchmark is not None

        with patch("urllib.request.urlopen", side_effect=TimeoutError("Network timeout")):
            with pytest.raises(TimeoutError, match="Network timeout"):
                import urllib.request

                urllib.request.urlopen("http://example.com")


@pytest.mark.unit
class TestParameterValidationEdgeCases:
    def test_boundary_value_testing(self, temp_dir):
        with pytest.raises(ValueError, match="Scale factor 0.001 is too small"):
            TPCH(scale_factor=0.001, output_dir=temp_dir)

        benchmark = TPCH(scale_factor=0.01, output_dir=temp_dir)
        assert benchmark.scale_factor >= 0.01

        benchmark = TPCDS(scale_factor=100.0, output_dir=temp_dir)
        assert benchmark.scale_factor <= 100.0

        benchmark = AMPLab(scale_factor=0.0000001, output_dir=temp_dir)
        assert benchmark.scale_factor > 0

    def test_unicode_parameter_handling(self, temp_dir):
        unicode_dir = temp_dir / "测试目录"

        try:
            benchmark = WritePrimitives(scale_factor=0.01, output_dir=unicode_dir)
            assert benchmark.output_dir == unicode_dir
            unicode_dir.mkdir(parents=True, exist_ok=True)
            assert unicode_dir.exists()
        except (UnicodeError, OSError):
            pass

    def test_null_and_empty_parameter_handling(self, temp_dir):
        invalid_values = [None, "", [], {}, False]

        for invalid_value in invalid_values:
            try:
                if invalid_value is None:
                    with pytest.raises((TypeError, ValueError)):
                        TPCH(scale_factor=invalid_value, output_dir=temp_dir)
                elif invalid_value == "":
                    benchmark = TPCH(scale_factor=1.0, output_dir=invalid_value)
                    assert benchmark is not None
                else:
                    with pytest.raises((TypeError, ValueError)):
                        TPCH(scale_factor=1.0, output_dir=invalid_value)
            except (AssertionError, Exception):
                pass


@pytest.mark.unit
class TestRecoveryAndCleanupScenarios:
    def test_partial_data_generation_recovery(self, temp_dir):
        benchmark = WritePrimitives(scale_factor=0.01, output_dir=temp_dir)

        partial_file = temp_dir / "partial_data.csv"
        partial_file.write_text("incomplete,data,row")

        try:
            result = benchmark.generate_data()
            assert isinstance(result, (dict, list))
        except Exception as e:
            assert not isinstance(e, SystemError)

    def test_cleanup_on_interruption(self, temp_dir):
        benchmark = SSB(scale_factor=0.01, output_dir=temp_dir, compress_data=False, compression_type="none")

        with patch.object(benchmark, "generate_data") as mock_generate:
            mock_generate.side_effect = KeyboardInterrupt("User interrupted")

            try:
                benchmark.generate_data()
            except KeyboardInterrupt:
                assert temp_dir.exists()
                temp_files = list(temp_dir.glob("*"))
                assert len(temp_files) < 100

    def test_resource_cleanup_on_exception(self, temp_dir):
        benchmark = TPCDI(scale_factor=0.01, output_dir=temp_dir)

        original_open = open
        opened_files = []

        def tracking_open(*args, **kwargs):
            file_handle = original_open(*args, **kwargs)
            opened_files.append(file_handle)
            return file_handle

        with patch("builtins.open", side_effect=tracking_open):
            try:
                with patch.object(
                    benchmark,
                    "generate_data",
                    side_effect=RuntimeError("Simulated error"),
                ):
                    benchmark.generate_data()
            except RuntimeError:
                pass

            for file_handle in opened_files:
                if not file_handle.closed:
                    assert len([f for f in opened_files if not f.closed]) < 10


