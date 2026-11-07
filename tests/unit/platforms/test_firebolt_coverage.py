# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from benchbox.platforms.base.data_loading import DataSource
from benchbox.platforms.firebolt import FireboltColumnCountError

pytestmark = [pytest.mark.unit, pytest.mark.fast]


_FIREBOLT_MOCKS = {
    "firebolt": MagicMock(),
    "firebolt.client": MagicMock(),
    "firebolt.client.auth": MagicMock(),
    "firebolt.client.auth.firebolt_core": MagicMock(),
    "firebolt.db": MagicMock(),
}


@pytest.fixture(autouse=True)
def _patch_firebolt_sdk():
    with patch.dict("sys.modules", _FIREBOLT_MOCKS):
        yield


def _make_core_adapter(**extra):
    from benchbox.platforms.firebolt import FireboltAdapter

    return FireboltAdapter(url="http://localhost:3473", **extra)


def _make_cloud_adapter(**extra):
    from benchbox.platforms.firebolt import FireboltAdapter

    return FireboltAdapter(
        client_id="cid",
        client_secret="csec",
        account_name="acct",
        engine_name="engine",
        **extra,
    )


class TestCheckServerDatabaseExistsCloud:
    def test_cloud_database_exists_returns_true(self):
        adapter = _make_cloud_adapter()

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ("my_db",)

        with patch("benchbox.platforms.firebolt.firebolt_connect", return_value=mock_conn):
            result = adapter.check_server_database_exists(database="my_db")

        assert result is True

    def test_cloud_database_missing_returns_false(self):
        adapter = _make_cloud_adapter()

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = None

        with patch("benchbox.platforms.firebolt.firebolt_connect", return_value=mock_conn):
            result = adapter.check_server_database_exists(database="missing_db")

        assert result is False

    def test_cloud_connection_error_returns_false(self):
        adapter = _make_cloud_adapter()

        with patch("benchbox.platforms.firebolt.firebolt_connect", side_effect=Exception("auth failed")):
            result = adapter.check_server_database_exists(database="db")

        assert result is False

    def test_cloud_query_error_returns_false(self):
        adapter = _make_cloud_adapter()

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = Exception("query error")

        with patch("benchbox.platforms.firebolt.firebolt_connect", return_value=mock_conn):
            result = adapter.check_server_database_exists(database="db")

        assert result is False


class TestCheckServerDatabaseExistsCore:
    def test_core_with_tables_returns_true(self):
        adapter = _make_core_adapter()

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [("table1",), ("table2",)]

        with patch("benchbox.platforms.firebolt.firebolt_connect", return_value=mock_conn):
            result = adapter.check_server_database_exists()

        assert result is True

    def test_core_empty_database_returns_false(self):
        adapter = _make_core_adapter()

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = []

        with patch("benchbox.platforms.firebolt.firebolt_connect", return_value=mock_conn):
            result = adapter.check_server_database_exists()

        assert result is False

    def test_core_connection_failure_returns_false(self):
        adapter = _make_core_adapter()

        with patch("benchbox.platforms.firebolt.firebolt_connect", side_effect=Exception("docker not running")):
            result = adapter.check_server_database_exists()

        assert result is False


class TestDropDatabase:
    def test_core_mode_drop_is_noop(self):
        adapter = _make_core_adapter()

        with patch("benchbox.platforms.firebolt.firebolt_connect") as mock_connect:
            adapter.drop_database(database="some_db")

        mock_connect.assert_not_called()

    def test_cloud_drop_skips_when_db_missing(self):
        adapter = _make_cloud_adapter()

        with patch.object(adapter, "check_server_database_exists", return_value=False):
            with patch("benchbox.platforms.firebolt.firebolt_connect") as mock_connect:
                adapter.drop_database(database="nonexistent")

        mock_connect.assert_not_called()

    def test_cloud_drop_executes_when_db_exists(self):
        adapter = _make_cloud_adapter()

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        with (
            patch.object(adapter, "check_server_database_exists", return_value=True),
            patch("benchbox.platforms.firebolt.firebolt_connect", return_value=mock_conn),
        ):
            adapter.drop_database(database="old_db")

        mock_cursor.execute.assert_called_once()
        sql_call = mock_cursor.execute.call_args[0][0]
        assert "DROP DATABASE" in sql_call

    def test_cloud_drop_raises_on_execute_error(self):
        adapter = _make_cloud_adapter()

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = Exception("permission denied")

        with (
            patch.object(adapter, "check_server_database_exists", return_value=True),
            patch("benchbox.platforms.firebolt.firebolt_connect", return_value=mock_conn),
            pytest.raises(RuntimeError, match="Failed to drop"),
        ):
            adapter.drop_database(database="db")


class TestCreateConnectionResultCache:
    def test_cloud_disables_result_cache_when_configured(self):
        adapter = _make_cloud_adapter(disable_result_cache=True)

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (1,)

        with (
            patch.object(adapter, "handle_existing_database"),
            patch("benchbox.platforms.firebolt.firebolt_connect", return_value=mock_conn),
            patch.object(adapter, "_disable_result_cache") as mock_disable,
        ):
            connection = adapter.create_connection()

        mock_disable.assert_called_once_with(mock_conn)
        assert connection is mock_conn

    def test_core_does_not_disable_result_cache(self):
        adapter = _make_core_adapter(disable_result_cache=True)

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (1,)

        with (
            patch.object(adapter, "handle_existing_database"),
            patch("benchbox.platforms.firebolt.firebolt_connect", return_value=mock_conn),
            patch.object(adapter, "_disable_result_cache") as mock_disable,
        ):
            adapter.create_connection()

        mock_disable.assert_not_called()

    def test_create_connection_raises_on_connect_error(self):
        adapter = _make_core_adapter()

        with (
            patch.object(adapter, "handle_existing_database"),
            patch("benchbox.platforms.firebolt.firebolt_connect", side_effect=Exception("refused")),
            pytest.raises(Exception, match="refused"),
        ):
            adapter.create_connection()


class TestDisableResultCache:
    def test_cloud_disables_cache_success(self):
        adapter = _make_cloud_adapter()

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        adapter._disable_result_cache(mock_conn)

        mock_cursor.execute.assert_called_once_with("SET enable_result_cache = false")
        mock_cursor.close.assert_called_once()

    def test_core_mode_skips_disable(self):
        adapter = _make_core_adapter()

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        adapter._disable_result_cache(mock_conn)

        mock_cursor.execute.assert_not_called()

    def test_strict_validation_raises_on_failure(self):
        from benchbox.core.exceptions import ConfigurationError

        adapter = _make_cloud_adapter(strict_validation=True)

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = Exception("not supported")

        with pytest.raises(ConfigurationError, match="Failed to disable"):
            adapter._disable_result_cache(mock_conn)

    def test_non_strict_logs_warning_on_failure(self):
        adapter = _make_cloud_adapter(strict_validation=False)

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = Exception("not supported")

        adapter._disable_result_cache(mock_conn)

    def test_strict_validation_validates_cache_after_disable(self):
        adapter = _make_cloud_adapter(strict_validation=True)

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        with patch.object(adapter, "validate_session_cache_control") as mock_validate:
            adapter._disable_result_cache(mock_conn)

        mock_validate.assert_called_once_with(mock_conn)


class TestValidateSessionCacheControl:
    def test_core_mode_returns_true(self):
        adapter = _make_core_adapter()
        assert adapter.validate_session_cache_control(Mock()) is True

    def test_cloud_cache_disabled_returns_true(self):
        adapter = _make_cloud_adapter()

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ("false",)

        result = adapter.validate_session_cache_control(mock_conn)
        assert result is True

    def test_cloud_cache_enabled_returns_false_non_strict(self):
        adapter = _make_cloud_adapter(strict_validation=False)

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ("true",)

        result = adapter.validate_session_cache_control(mock_conn)
        assert result is False

    def test_cloud_cache_enabled_raises_strict(self):
        from benchbox.core.exceptions import ConfigurationError

        adapter = _make_cloud_adapter(strict_validation=True)

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ("1",)

        with pytest.raises(ConfigurationError, match="Result cache is still enabled"):
            adapter.validate_session_cache_control(mock_conn)

    def test_show_not_supported_returns_true(self):
        adapter = _make_cloud_adapter()

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = Exception("SHOW command not supported")

        result = adapter.validate_session_cache_control(mock_conn)
        assert result is True

    def test_non_show_error_non_strict_returns_false(self):
        adapter = _make_cloud_adapter(strict_validation=False)

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = Exception("network error")

        result = adapter.validate_session_cache_control(mock_conn)
        assert result is False

    def test_non_show_error_strict_raises(self):
        from benchbox.core.exceptions import ConfigurationError

        adapter = _make_cloud_adapter(strict_validation=True)

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = Exception("network error")

        with pytest.raises(ConfigurationError, match="Cache validation failed"):
            adapter.validate_session_cache_control(mock_conn)

    def test_no_result_from_show(self):
        adapter = _make_cloud_adapter()

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = None

        result = adapter.validate_session_cache_control(mock_conn)
        assert result is True


class TestCreateAdminConnection:
    def test_cloud_connects_to_information_schema(self):
        adapter = _make_cloud_adapter()

        mock_conn = Mock()
        with patch("benchbox.platforms.firebolt.firebolt_connect", return_value=mock_conn) as mock_connect:
            result = adapter._create_admin_connection()

        assert result is mock_conn
        call_kwargs = mock_connect.call_args.kwargs
        assert call_kwargs["database"] == "information_schema"

    def test_core_connects_without_information_schema(self):
        adapter = _make_core_adapter()

        mock_conn = Mock()
        with patch("benchbox.platforms.firebolt.firebolt_connect", return_value=mock_conn) as mock_connect:
            result = adapter._create_admin_connection()

        assert result is mock_conn
        call_kwargs = mock_connect.call_args.kwargs
        assert call_kwargs.get("database") != "information_schema"

    def test_connection_error_propagates(self):
        adapter = _make_cloud_adapter()

        with (
            patch("benchbox.platforms.firebolt.firebolt_connect", side_effect=RuntimeError("auth failed")),
            pytest.raises(RuntimeError, match="auth failed"),
        ):
            adapter._create_admin_connection()


class TestGetPlatformMetadata:
    def test_cloud_includes_engine_details(self):
        adapter = _make_cloud_adapter()

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.side_effect = [
            ("Firebolt 1.2.3",),
            ("my-engine", "M", "RUNNING"),
        ]

        metadata = adapter._get_platform_metadata(mock_conn)

        assert metadata["mode"] == "cloud"
        assert metadata["account_name"] == "acct"
        assert metadata["engine_name"] == "engine"
        assert metadata["version"] == "Firebolt 1.2.3"

    def test_core_includes_url(self):
        adapter = _make_core_adapter()

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ("Firebolt 1.0.0",)

        metadata = adapter._get_platform_metadata(mock_conn)

        assert metadata["mode"] == "core"
        assert metadata["url"] == "http://localhost:3473"

    def test_version_query_error_skipped(self):
        adapter = _make_core_adapter()

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = Exception("version() not available")

        metadata = adapter._get_platform_metadata(mock_conn)

        assert metadata["mode"] == "core"
        assert "database" in metadata

    def test_engine_details_query_error_ignored(self):
        adapter = _make_cloud_adapter()

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.side_effect = [("v1",), Exception("engine query failed")]
        mock_cursor.execute.side_effect = [None, Exception("engine query failed")]

        metadata = adapter._get_platform_metadata(mock_conn)
        assert metadata["mode"] == "cloud"


class TestGetPlatformInfoBranches:
    def test_firebolt_version_from_sdk(self):
        adapter = _make_core_adapter()

        mock_firebolt = MagicMock()
        mock_firebolt.__version__ = "2.0.0"
        with patch.dict("sys.modules", {"firebolt": mock_firebolt}):
            info = adapter.get_platform_info(None)

        assert info["client_library_version"] == "2.0.0"

    def test_firebolt_version_none_on_attribute_error(self):
        adapter = _make_core_adapter()

        mock_firebolt = MagicMock(spec=[])
        with patch.dict("sys.modules", {"firebolt": mock_firebolt}):
            info = adapter.get_platform_info(None)

        assert info["client_library_version"] is None

    def test_version_query_error_is_swallowed(self):
        adapter = _make_core_adapter()

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = Exception("SELECT version() failed")

        info = adapter.get_platform_info(mock_conn)
        assert info["platform_version"] is None


class TestLoadDataInsertBranches:
    def test_manifest_fallback_loads_data(self):
        adapter = _make_core_adapter()

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        with tempfile.TemporaryDirectory() as tmp_dir:
            data_file = Path(tmp_dir) / "orders.csv"
            data_file.write_text("1,foo\n2,bar\n")

            manifest = {
                "tables": {
                    "orders": [{"path": "orders.csv"}],
                }
            }
            (Path(tmp_dir) / "_datagen_manifest.json").write_text(json.dumps(manifest))

            mock_benchmark = Mock(spec=[])

            table_stats, load_time, _ = adapter._load_data_via_insert(mock_benchmark, mock_conn, Path(tmp_dir))

        assert "orders" in table_stats
        assert table_stats["orders"] == 2

    def test_missing_data_files_raises_value_error(self):
        adapter = _make_core_adapter()

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        with tempfile.TemporaryDirectory() as tmp_dir:
            mock_benchmark = Mock(spec=[])

            with pytest.raises(ValueError, match="No data files found"):
                adapter._load_data_via_insert(mock_benchmark, mock_conn, Path(tmp_dir))

    def test_large_batch_is_flushed_mid_file(self):
        adapter = _make_core_adapter()

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        rows = "\n".join(f"{i},value{i}" for i in range(600))

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
            f.write(rows)
            tmp_path = Path(f.name)

        try:
            mock_benchmark = Mock()
            mock_benchmark.tables = {"big_table": str(tmp_path)}

            table_stats, _, _ = adapter._load_data_via_insert(mock_benchmark, mock_conn, Path("/tmp"))
        finally:
            tmp_path.unlink()

        assert table_stats["big_table"] == 600
        assert mock_cursor.executemany.call_count >= 2

    def test_empty_file_is_skipped(self):
        adapter = _make_core_adapter()

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
            f.write("")
            tmp_path = Path(f.name)

        try:
            mock_benchmark = Mock()
            mock_benchmark.tables = {"empty_table": str(tmp_path)}

            table_stats, _, _ = adapter._load_data_via_insert(mock_benchmark, mock_conn, Path("/tmp"))
        finally:
            tmp_path.unlink()

        assert table_stats["empty_table"] == 0
        mock_cursor.executemany.assert_not_called()

    def test_column_count_mismatch_raises(self):
        adapter = _make_core_adapter()

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
            f.write("1,a\n2,b,extra\n")
            tmp_path = Path(f.name)

        try:
            mock_benchmark = Mock()
            mock_benchmark.tables = {"bad_table": str(tmp_path)}

            with pytest.raises(FireboltColumnCountError, match="Inconsistent column count"):
                adapter._load_data_via_insert(mock_benchmark, mock_conn, Path("/tmp"))
        finally:
            tmp_path.unlink()

    def test_null_values_converted(self):
        adapter = _make_core_adapter()

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
            f.write("1,empty_col\n2,NULL\n")
            tmp_path = Path(f.name)

        try:
            mock_benchmark = Mock()
            mock_benchmark.tables = {"nullable_table": str(tmp_path)}

            source = DataSource(
                source_type="manifest_v2",
                tables={"nullable_table": [tmp_path]},
                table_metadata={"nullable_table": {"csv_delimiter": ",", "csv_null_marker": "NULL"}},
            )

            with patch.object(adapter, "_resolve_data_files", return_value=source):
                adapter._load_data_via_insert(mock_benchmark, mock_conn, Path("/tmp"))

            _, rows = mock_cursor.executemany.call_args[0]
            assert rows[0] == ("1", "empty_col")
            assert rows[1] == ("2", None)
        finally:
            tmp_path.unlink()


class TestExecuteBatchInsert:
    def test_uses_executemany_when_available(self):
        adapter = _make_core_adapter()

        cursor = Mock()
        cursor.executemany = Mock()
        rows = [("a",), ("b",)]
        adapter._execute_batch_insert(cursor, "INSERT INTO t VALUES (?)", rows)

        cursor.executemany.assert_called_once_with("INSERT INTO t VALUES (?)", rows)

    def test_fallback_to_execute_when_no_executemany(self):
        adapter = _make_core_adapter()

        cursor = Mock(spec=["execute"])
        rows = [("a",), ("b",)]
        adapter._execute_batch_insert(cursor, "INSERT INTO t VALUES (?)", rows)

        assert cursor.execute.call_count == 2

    def test_empty_rows_does_nothing(self):
        adapter = _make_core_adapter()

        cursor = Mock()
        adapter._execute_batch_insert(cursor, "INSERT INTO t VALUES (?)", [])

        cursor.executemany.assert_not_called()
        cursor.execute.assert_not_called()


class TestGetParameterPlaceholder:
    def test_format_style_returns_percent_s(self):
        adapter = _make_core_adapter()

        cursor = Mock()
        cursor.paramstyle = "format"
        assert adapter._get_parameter_placeholder(cursor) == "%s"

    def test_pyformat_style_returns_percent_s(self):
        adapter = _make_core_adapter()

        cursor = Mock()
        cursor.paramstyle = "pyformat"
        assert adapter._get_parameter_placeholder(cursor) == "%s"

    def test_unknown_style_returns_question_mark(self):
        adapter = _make_core_adapter()

        cursor = Mock()
        cursor.paramstyle = "qmark"
        assert adapter._get_parameter_placeholder(cursor) == "?"

    def test_no_paramstyle_returns_question_mark(self):
        adapter = _make_core_adapter()

        cursor = Mock(spec=["execute"])
        assert adapter._get_parameter_placeholder(cursor) == "?"


class TestCoerceBool:
    def test_none_returns_default(self):
        adapter = _make_core_adapter()
        assert adapter._coerce_bool(None, True) is True
        assert adapter._coerce_bool(None, False) is False

    def test_true_bool(self):
        adapter = _make_core_adapter()
        assert adapter._coerce_bool(True, False) is True

    def test_false_bool(self):
        adapter = _make_core_adapter()
        assert adapter._coerce_bool(False, True) is False

    def test_string_false_values(self):
        adapter = _make_core_adapter()
        for falsy in ("false", "0", "no", "off", "False", "FALSE", "  0  "):
            assert adapter._coerce_bool(falsy, True) is False, f"Expected False for {falsy!r}"

    def test_string_truthy_values(self):
        adapter = _make_core_adapter()
        for truthy in ("true", "yes", "1", "on", "True"):
            assert adapter._coerce_bool(truthy, False) is True, f"Expected True for {truthy!r}"


class TestS3UrlValidation:
    def test_valid_s3_url_accepted(self):
        adapter = _make_cloud_adapter(s3_staging_url="s3://my-bucket/prefix/")
        assert adapter.s3_staging_url == "s3://my-bucket/prefix/"

    def test_s3_url_without_trailing_slash_gets_one(self):
        adapter = _make_cloud_adapter(s3_staging_url="s3://my-bucket/prefix")
        assert adapter.s3_staging_url == "s3://my-bucket/prefix/"

    def test_invalid_s3_url_raises(self):
        from benchbox.core.exceptions import ConfigurationError

        with pytest.raises(ConfigurationError, match="Invalid S3 staging URL"):
            _make_cloud_adapter(s3_staging_url="https://my-bucket/prefix")

    def test_env_var_s3_url(self):
        with patch.dict(os.environ, {"FIREBOLT_S3_STAGING_URL": "s3://env-bucket/path/"}):
            adapter = _make_cloud_adapter()
        assert adapter.s3_staging_url == "s3://env-bucket/path/"


class TestParseS3Url:
    def test_parses_bucket_and_prefix(self):
        from benchbox.platforms.firebolt import FireboltAdapter

        bucket, prefix = FireboltAdapter._parse_s3_url("s3://my-bucket/some/prefix/")
        assert bucket == "my-bucket"
        assert prefix == "some/prefix/"

    def test_parses_bucket_only(self):
        from benchbox.platforms.firebolt import FireboltAdapter

        bucket, prefix = FireboltAdapter._parse_s3_url("s3://my-bucket/")
        assert bucket == "my-bucket"
        assert prefix == ""


class TestGenerateTuningClauseDistribution:
    def test_distribution_generates_primary_index(self):
        adapter = _make_core_adapter()

        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = True

        mock_col1 = Mock()
        mock_col1.name = "customer_id"
        mock_col1.order = 1
        mock_col2 = Mock()
        mock_col2.name = "order_date"
        mock_col2.order = 2

        with patch("benchbox.core.tuning.interface.TuningType") as mock_tt:
            mock_tt.DISTRIBUTION = "distribution"
            mock_tt.PARTITIONING = "partitioning"

            def _get_cols(tuning_type):
                if tuning_type == mock_tt.DISTRIBUTION:
                    return [mock_col1, mock_col2]
                return []

            mock_tuning.get_columns_by_type.side_effect = _get_cols
            clause = adapter.generate_tuning_clause(mock_tuning)

        assert "PRIMARY INDEX" in clause
        assert "customer_id" in clause
        assert "order_date" in clause

    def test_both_distribution_and_partition(self):
        adapter = _make_core_adapter()

        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = True

        mock_dist_col = Mock()
        mock_dist_col.name = "user_id"
        mock_dist_col.order = 1

        mock_part_col = Mock()
        mock_part_col.name = "event_date"
        mock_part_col.order = 1

        with patch("benchbox.core.tuning.interface.TuningType") as mock_tt:
            mock_tt.DISTRIBUTION = "distribution"
            mock_tt.PARTITIONING = "partitioning"

            def _get_cols(tuning_type):
                if tuning_type == mock_tt.DISTRIBUTION:
                    return [mock_dist_col]
                if tuning_type == mock_tt.PARTITIONING:
                    return [mock_part_col]
                return []

            mock_tuning.get_columns_by_type.side_effect = _get_cols
            clause = adapter.generate_tuning_clause(mock_tuning)

        assert "PRIMARY INDEX" in clause
        assert "PARTITION BY" in clause


class TestSupportsTuningTypeImportError:
    def test_import_error_returns_false(self):
        adapter = _make_core_adapter()

        with patch("benchbox.core.tuning.interface.TuningType", side_effect=ImportError("no tuning")):
            with patch.dict("sys.modules", {"benchbox.core.tuning.interface": None}):
                result = adapter.supports_tuning_type("anything")

        assert result is False


class TestGetExistingTables:
    def test_returns_lowercase_table_names(self):
        adapter = _make_core_adapter()

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [("LINEITEM",), ("ORDERS",), ("Customer",)]

        result = adapter._get_existing_tables(mock_conn)

        assert result == ["lineitem", "orders", "customer"]

    def test_returns_empty_on_error(self):
        adapter = _make_core_adapter()

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = Exception("SHOW TABLES failed")

        result = adapter._get_existing_tables(mock_conn)

        assert result == []


class TestAnalyzeTable:
    def test_analyze_is_noop(self):
        adapter = _make_core_adapter()

        mock_conn = Mock()

        adapter.analyze_table(mock_conn, "lineitem")

        mock_conn.cursor.assert_not_called()


class TestApplyPlatformOptimizations:
    def test_none_config_is_noop(self):
        adapter = _make_core_adapter()
        adapter.apply_platform_optimizations(None, Mock())

    def test_valid_config_logs_info(self):
        adapter = _make_core_adapter()

        mock_config = Mock()
        with patch.object(adapter.logger, "info") as mock_log:
            adapter.apply_platform_optimizations(mock_config, Mock())

        mock_log.assert_called_once()
        assert "optimized" in mock_log.call_args[0][0].lower()


class TestApplyUnifiedTuning:
    def test_none_config_returns_early(self):
        adapter = _make_core_adapter()
        adapter.apply_unified_tuning(None, Mock())

    def test_delegates_to_sub_methods(self):
        adapter = _make_core_adapter()

        mock_config = Mock()
        mock_config.primary_keys = Mock()
        mock_config.foreign_keys = Mock()
        mock_config.platform_optimizations = Mock()
        mock_config.table_tunings = {"tbl1": Mock()}

        with (
            patch.object(adapter, "apply_constraint_configuration") as mock_constraint,
            patch.object(adapter, "apply_platform_optimizations") as mock_opt,
            patch.object(adapter, "apply_table_tunings") as mock_table,
        ):
            adapter.apply_unified_tuning(mock_config, Mock())

        mock_constraint.assert_called_once()
        mock_opt.assert_called_once()
        mock_table.assert_called_once()


class TestFromConfigCoreOverride:
    def test_core_mode_override_sets_default_url(self):
        from benchbox.platforms.firebolt import FireboltAdapter

        config = {
            "deployment_mode": "core",
            "benchmark": "tpch",
            "scale_factor": 1.0,
        }
        adapter = FireboltAdapter.from_config(config)

        assert adapter.deployment_mode == "core"
        assert adapter.url == "http://localhost:3473"

    def test_cloud_from_config_with_s3(self):
        from benchbox.platforms.firebolt import FireboltAdapter

        config = {
            "client_id": "cid",
            "client_secret": "csec",
            "account_name": "acct",
            "engine_name": "eng",
            "s3_staging_url": "s3://bucket/prefix/",
            "s3_region": "us-east-1",
            "benchmark": "tpch",
            "scale_factor": 1.0,
        }
        adapter = FireboltAdapter.from_config(config)

        assert adapter.s3_staging_url == "s3://bucket/prefix/"
        assert adapter.s3_region == "us-east-1"


class TestBuildFireboltConfig:
    def test_builds_config_with_merged_options(self):
        from benchbox.platforms.firebolt import _build_firebolt_config

        mock_info = Mock()
        mock_info.display_name = "Firebolt"
        mock_info.driver_package = "firebolt-sdk"

        mock_cred_mgr = Mock()
        mock_cred_mgr.get_platform_credentials.return_value = {
            "client_id": "saved-id",
            "client_secret": "saved-secret",
        }

        options = {"account_name": "options-acct"}
        overrides = {
            "engine_name": "override-engine",
            "benchmark": "tpch",
            "scale_factor": 1.0,
        }

        with patch("benchbox.security.credentials.CredentialManager", return_value=mock_cred_mgr):
            config = _build_firebolt_config("firebolt", options, overrides, mock_info)

        assert config.client_id == "saved-id"
        assert config.account_name == "options-acct"
        assert config.engine_name == "override-engine"

    def test_builds_config_without_platform_info(self):
        from benchbox.platforms.firebolt import _build_firebolt_config

        mock_cred_mgr = Mock()
        mock_cred_mgr.get_platform_credentials.return_value = {}

        overrides = {"benchmark": "tpch", "scale_factor": 1.0}

        with patch("benchbox.security.credentials.CredentialManager", return_value=mock_cred_mgr):
            config = _build_firebolt_config("firebolt", {}, overrides, None)

        assert config is not None

    def test_explicit_database_override_is_used(self):
        from benchbox.platforms.firebolt import _build_firebolt_config

        mock_info = Mock()
        mock_info.display_name = "Firebolt"
        mock_info.driver_package = "firebolt-sdk"

        mock_cred_mgr = Mock()
        mock_cred_mgr.get_platform_credentials.return_value = {}

        overrides = {
            "database": "explicit_db",
            "benchmark": "tpch",
            "scale_factor": 1.0,
        }

        with patch("benchbox.security.credentials.CredentialManager", return_value=mock_cred_mgr):
            config = _build_firebolt_config("firebolt", {}, overrides, mock_info)

        assert config.database == "explicit_db"

    def test_builds_config_with_runtime_metadata_fields(self):
        from benchbox.platforms.firebolt import _build_firebolt_config

        mock_info = Mock()
        mock_info.display_name = "Firebolt"
        mock_info.driver_package = "firebolt-sdk"
        mock_cred_mgr = Mock()
        mock_cred_mgr.get_platform_credentials.return_value = {}

        options = {
            "region": "us-east-1",
            "cloud_provider": "aws",
            "engine_type": "GENERAL_PURPOSE",
            "engine_size": "large",
            "s3_staging_url": "s3://bench-bucket/stage/",
            "s3_region": "us-east-1",
        }
        overrides = {"benchmark": "tpch", "scale_factor": 1.0}

        with patch("benchbox.security.credentials.CredentialManager", return_value=mock_cred_mgr):
            config = _build_firebolt_config("firebolt", options, overrides, mock_info)

        assert config.options["region"] == "us-east-1"
        assert config.options["cloud_provider"] == "aws"
        assert config.options["engine_type"] == "GENERAL_PURPOSE"
        assert config.options["engine_size"] == "large"
        assert config.options["s3_staging_url"] == "s3://bench-bucket/stage/"

    def test_firebolt_platform_options_parse_metadata_aliases(self):
        from benchbox.cli.platform_hooks import PlatformHookRegistry

        parsed = PlatformHookRegistry.parse_options(
            "firebolt",
            [
                ("firebolt_mode", "cloud"),
                ("cloud_region", "us-east-1"),
                ("disable_result_cache", "false"),
            ],
        )

        assert parsed["deployment_mode"] == "cloud"
        assert parsed["region"] == "us-east-1"
        assert parsed["disable_result_cache"] is False


class TestNormalizedResultMetadata:
    def test_cloud_metadata_maps_observed_engine_and_s3_staging(self):
        adapter = _make_cloud_adapter(
            database="bench",
            region="us-east-1",
            cloud_provider="aws",
            engine_size="large",
            s3_staging_url="s3://bench-bucket/stage/",
            s3_region="us-east-1",
            disable_result_cache=False,
        )
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.side_effect = [
            ("3.0.0",),
            ("engine", "GENERAL_PURPOSE", "RUNNING"),
        ]

        info = adapter.get_platform_info(mock_conn)
        metadata = adapter.get_normalized_result_metadata(platform_info=info)

        assert metadata["execution_environment"]["platform_runtime"]["runtime_type"] == "managed_cloud"
        assert metadata["platform_deployment"]["deployment_type"] == "managed_cloud"
        assert metadata["platform_deployment"]["account"] == "acct"
        assert metadata["platform_deployment"]["engine"] == "engine"
        assert metadata["platform_cloud"]["provider"] == "aws"
        assert metadata["platform_cloud"]["region"] == "us-east-1"
        assert metadata["platform_cloud"]["region_collection_status"] == "available"
        assert metadata["platform_compute"]["engine"] == "engine"
        assert metadata["platform_compute"]["engine_type"] == "GENERAL_PURPOSE"
        assert metadata["platform_compute"]["engine_size"] == "large"
        assert metadata["platform_compute"]["engine_status"] == "RUNNING"
        assert metadata["platform_compute"]["result_cache_enabled"] is True
        assert metadata["platform_compute"]["collection_status"] == "available"
        assert metadata["platform_storage"]["staging_url_type"] == "s3"
        assert metadata["platform_storage"]["bucket"] == "bench-bucket"
        assert metadata["platform_storage"]["prefix"] == "stage/"
        assert metadata["platform_storage"]["region"] == "us-east-1"

    def test_cloud_malformed_engine_metadata_remains_requested(self):
        adapter = _make_cloud_adapter(engine_type="GENERAL_PURPOSE")
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.side_effect = [
            ("3.0.0",),
            ("engine",),
        ]

        info = adapter.get_platform_info(mock_conn)
        metadata = adapter.get_normalized_result_metadata(platform_info=info)

        assert metadata["platform_compute"]["engine"] == "engine"
        assert metadata["platform_compute"]["engine_type"] == "GENERAL_PURPOSE"
        assert metadata["platform_compute"]["engine_metadata_collection_status"] == "unavailable"
        assert metadata["platform_compute"]["source"] == "requested"
        assert metadata["platform_compute"]["collection_status"] == "partial"

    def test_cloud_sparse_metadata_marks_region_and_engine_metadata_unavailable(self):
        adapter = _make_cloud_adapter()

        info = adapter.get_platform_info(connection=None)
        metadata = adapter.get_normalized_result_metadata(platform_info=info)

        assert metadata["execution_environment"]["platform_runtime"]["runtime_type"] == "managed_cloud"
        assert metadata["platform_cloud"]["region_collection_status"] == "unavailable"
        assert "region" not in metadata["platform_cloud"]
        assert metadata["platform_compute"]["engine"] == "engine"
        assert metadata["platform_compute"]["collection_status"] == "partial"
        assert metadata["platform_storage"]["table_format"] == "firebolt_engine_table"
        assert metadata["platform_storage"]["staging_url_type_status"] == "unavailable"

    def test_core_metadata_maps_localhost_self_hosted_runtime(self):
        adapter = _make_core_adapter(database="core_db")

        metadata = adapter.get_normalized_result_metadata(platform_info=adapter.get_platform_info(connection=None))

        assert metadata["execution_environment"]["platform_runtime"]["runtime_type"] == "remote_server"
        assert metadata["platform_deployment"]["deployment_type"] == "self_hosted"
        assert metadata["platform_deployment"]["endpoint_class"] == "localhost_port"
        assert metadata["platform_deployment"]["url"] == "http://localhost:3473"
        assert metadata["platform_cloud"]["collection_status"] == "unavailable"
        assert metadata["platform_compute"]["service_model"] == "core"


class TestQuoteIdentifier:
    def test_quotes_simple_name(self):
        adapter = _make_core_adapter()
        assert adapter._quote_identifier("lineitem") == '"lineitem"'

    def test_escapes_embedded_quotes(self):
        adapter = _make_core_adapter()
        assert adapter._quote_identifier('weird"name') == '"weird""name"'

    def test_empty_name_raises(self):
        adapter = _make_core_adapter()
        with pytest.raises(ValueError, match="non-empty"):
            adapter._quote_identifier("")

    def test_non_string_raises(self):
        adapter = _make_core_adapter()
        with pytest.raises(ValueError, match="non-empty"):
            adapter._quote_identifier(None)


class TestConfigureForBenchmark:
    def test_tpch_is_accepted(self):
        adapter = _make_core_adapter()
        adapter.configure_for_benchmark(Mock(), "tpch")

    def test_tpcds_is_accepted(self):
        adapter = _make_core_adapter()
        adapter.configure_for_benchmark(Mock(), "tpcds")

    def test_generic_type_accepted(self):
        adapter = _make_core_adapter()
        adapter.configure_for_benchmark(Mock(), "custom_benchmark")
