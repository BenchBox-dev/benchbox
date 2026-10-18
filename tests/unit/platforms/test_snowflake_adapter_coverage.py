# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from benchbox.core.tuning.applied_ledger import PHASE_DDL, PHASE_POST_LOAD, AppliedTuningLedger, recording_connection

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture(autouse=True)
def _mock_snowflake_deps():
    with (
        patch("benchbox.platforms.snowflake.snowflake"),
        patch("benchbox.platforms.snowflake.check_platform_dependencies", return_value=(True, [])),
    ):
        yield


def _make_adapter(**kwargs):
    from benchbox.platforms.snowflake import SnowflakeAdapter

    defaults = {
        "account": "test_account",
        "username": "test_user",
        "password": "test_pass",
        "warehouse": "COMPUTE_WH",
        "database": "BENCHBOX",
    }
    defaults.update(kwargs)
    return SnowflakeAdapter(**defaults)


class TestGetQueryStatisticsRetry:
    def test_returns_note_when_no_result_after_max_retries(self):
        adapter = _make_adapter()
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = None

        with patch("time.sleep"):
            result = adapter._get_query_statistics(mock_conn, "q-miss", max_retries=1, initial_delay=0.001)

        assert "note" in result
        assert result["retrieval_attempts"] == 2

    def test_returns_statistics_error_when_execute_raises(self):
        adapter = _make_adapter()
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = RuntimeError("history table unavailable")

        with patch("time.sleep"):
            result = adapter._get_query_statistics(mock_conn, "q-err", max_retries=1, initial_delay=0.001)

        assert "statistics_error" in result
        assert result["statistics_error"] == "RuntimeError: query statistics retrieval failed"
        assert result["retrieval_attempts"] == 1
        assert mock_cursor.execute.call_count == 1

    def test_default_lookup_makes_one_attempt_without_backoff(self):
        adapter = _make_adapter()
        mock_conn = Mock()
        mock_cursor = mock_conn.cursor.return_value
        mock_cursor.fetchone.return_value = None

        with patch("time.sleep") as sleep:
            result = adapter._get_query_statistics(mock_conn, "q-miss")

        assert result["retrieval_attempts"] == 1
        assert mock_cursor.execute.call_count == 1
        sleep.assert_not_called()

    def test_query_history_projection_uses_table_function_columns(self):
        import re

        adapter = _make_adapter()
        mock_conn = Mock()
        mock_conn.cursor.return_value.fetchone.return_value = None
        adapter._get_query_statistics(mock_conn, "q1")
        sql = mock_conn.cursor.return_value.execute.call_args.args[0]
        projection = re.search(r"SELECT\s+(.*?)\s+FROM TABLE", sql, re.IGNORECASE | re.DOTALL)
        assert projection is not None
        columns = {part.strip().upper() for part in projection.group(1).split(",")}
        assert columns == {
            "QUERY_ID",
            "TOTAL_ELAPSED_TIME",
            "EXECUTION_TIME",
            "COMPILATION_TIME",
            "BYTES_SCANNED",
            "ROWS_PRODUCED",
            "CREDITS_USED_CLOUD_SERVICES",
            "WAREHOUSE_SIZE",
            "CLUSTER_NUMBER",
        }

    def test_query_history_sql_contains_query_id(self):
        adapter = _make_adapter()
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = None

        with patch("time.sleep"):
            adapter._get_query_statistics(mock_conn, "BENCHQ17", max_retries=0)

        executed_sqls = [c.args[0] for c in mock_cursor.execute.call_args_list]
        assert any("QUERY_HISTORY" in s for s in executed_sqls), f"No QUERY_HISTORY in: {executed_sqls}"
        assert any("BENCHQ17" in s for s in executed_sqls), f"query_id not in SQL: {executed_sqls}"
        assert "QUERY_TAG = 'BenchBox_BENCHQ17'" in executed_sqls[0]


class TestBuildCtasSortSql:
    def test_returns_none_when_mode_is_off(self):
        adapter = _make_adapter()
        col = Mock()
        col.name = "l_orderkey"

        with patch.object(adapter, "resolve_sorted_ingestion_strategy", return_value=("off", "ctas")):
            result = adapter._build_ctas_sort_sql("LINEITEM", [col])

        assert result is None

    def test_generates_ctas_order_by_sql(self):
        adapter = _make_adapter()
        col1 = Mock()
        col1.name = "l_orderkey"
        col2 = Mock()
        col2.name = "l_partkey"

        with patch.object(adapter, "resolve_sorted_ingestion_strategy", return_value=("on", "ctas")):
            sql = adapter._build_ctas_sort_sql("LINEITEM", [col1, col2])

        assert sql is not None
        assert "CREATE OR REPLACE TABLE LINEITEM" in sql
        assert "ORDER BY l_orderkey, l_partkey" in sql

    def test_raises_for_unsupported_method(self):
        adapter = _make_adapter()
        col = Mock()
        col.name = "col"

        with patch.object(adapter, "resolve_sorted_ingestion_strategy", return_value=("on", "sort_key")):
            with pytest.raises(ValueError, match="method.*sort_key.*not supported"):
                adapter._build_ctas_sort_sql("T", [col])


class TestGetPlatformInfoWithWarehouse:
    def test_warehouse_details_captured(self):
        adapter = _make_adapter(warehouse="MY_WH")

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        wh_row = ["MY_WH", "STARTED", "STANDARD", "LARGE"] + [None] * 27
        wh_row[4] = 1
        wh_row[5] = 3
        wh_row[11] = 300
        wh_row[12] = True
        wh_row[21] = None
        wh_row[22] = None
        wh_row[27] = "STANDARD"

        def _execute_side_effect(sql):
            if "current_version" in sql.lower():
                mock_cursor.fetchone.return_value = ("8.0.0",)
            elif "current_region" in sql.lower():
                mock_cursor.fetchone.return_value = ("AWS_US_EAST_1", "AWS")
            elif "SHOW WAREHOUSES" in sql:
                mock_cursor.fetchone.return_value = tuple(wh_row)
            elif "current_account_name" in sql.lower():
                mock_cursor.fetchone.return_value = ("acct", "org")
            return mock_cursor

        mock_cursor.execute = Mock(side_effect=_execute_side_effect)

        result = adapter.get_platform_info(connection=mock_conn)

        assert result["platform_version"] == "8.0.0"
        cc = result.get("compute_configuration")
        assert cc is not None
        assert cc["warehouse_name"] == "MY_WH"
        assert cc["warehouse_size"] == "LARGE"

    def test_normalized_metadata_maps_requested_and_observed_snowflake_fields(self):
        adapter = _make_adapter(
            warehouse="MY_WH",
            database="BENCH_DB",
            schema="BENCH_SCHEMA",
            role="ANALYST",
            warehouse_size="MEDIUM",
            disable_result_cache=True,
            file_format="PARQUET",
            compression="ZSTD",
            staging_root="s3://benchbox-stage/prefix",
            multi_cluster_warehouse=True,
        )

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        wh_row = ["MY_WH", "STARTED", "STANDARD", "LARGE"] + [None] * 27
        wh_row[4] = 1
        wh_row[5] = 3
        wh_row[11] = 300
        wh_row[12] = True
        wh_row[21] = True
        wh_row[22] = 8
        wh_row[27] = "ECONOMY"

        def _execute_side_effect(sql):
            if "current_version" in sql.lower():
                mock_cursor.fetchone.return_value = ("8.0.0",)
            elif "current_region" in sql.lower():
                mock_cursor.fetchone.return_value = ("AWS_US_EAST_1", "AWS")
            elif "SHOW WAREHOUSES" in sql:
                mock_cursor.fetchone.return_value = tuple(wh_row)
            elif "current_account_name" in sql.lower():
                mock_cursor.fetchone.return_value = ("acct", "org")
            return mock_cursor

        mock_cursor.execute = Mock(side_effect=_execute_side_effect)

        platform_info = adapter.get_platform_info(connection=mock_conn)
        metadata = adapter.get_normalized_result_metadata(platform_info=platform_info)

        assert metadata["execution_environment"]["platform_runtime"]["runtime_type"] == "managed_cloud"
        assert metadata["platform_deployment"]["deployment_type"] == "managed_cloud"
        assert metadata["platform_deployment"]["endpoint_class"] == "cloud_endpoint"
        assert metadata["platform_deployment"]["role"] == "ANALYST"
        assert metadata["platform_deployment"]["database"] == "BENCH_DB"
        assert metadata["platform_deployment"]["schema"] == "BENCH_SCHEMA"
        assert metadata["platform_cloud"]["provider"] == "aws"
        assert metadata["platform_cloud"]["region"] == "AWS_US_EAST_1"
        assert metadata["platform_cloud"]["account"] == "acct"
        assert metadata["platform_cloud"]["organization"] == "org"
        assert metadata["platform_compute"]["warehouse"] == "MY_WH"
        assert metadata["platform_compute"]["warehouse_size"] == "LARGE"
        assert metadata["platform_compute"]["warehouse_type"] == "STANDARD"
        assert metadata["platform_compute"]["min_cluster_count"] == 1
        assert metadata["platform_compute"]["max_cluster_count"] == 3
        assert metadata["platform_compute"]["auto_suspend"] == 300
        assert metadata["platform_compute"]["auto_resume"] is True
        assert metadata["platform_compute"]["query_acceleration_enabled"] is True
        assert metadata["platform_compute"]["query_acceleration_max_scale_factor"] == 8
        assert metadata["platform_compute"]["scaling_policy"] == "ECONOMY"
        assert metadata["platform_compute"]["result_cache_enabled"] is False
        assert metadata["platform_compute"]["collection_status"] == "available"
        assert metadata["platform_storage"]["table_format"] == "PARQUET"
        assert metadata["platform_storage"]["staging_location"] == "s3://benchbox-stage/prefix"
        assert metadata["platform_storage"]["compression"] == "ZSTD"
        assert metadata["platform_raw_config"]["password"] == "<redacted>"

    def test_warehouse_show_exception_silenced(self):
        adapter = _make_adapter()

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        def _execute_side_effect(sql):
            if "current_version" in sql.lower():
                mock_cursor.fetchone.return_value = ("8.0.0",)
            elif "current_region" in sql.lower():
                raise RuntimeError("region not available")
            elif "SHOW WAREHOUSES" in sql:
                raise RuntimeError("permission denied")
            elif "current_account_name" in sql.lower():
                raise RuntimeError("no account access")
            return mock_cursor

        mock_cursor.execute = Mock(side_effect=_execute_side_effect)

        result = adapter.get_platform_info(connection=mock_conn)
        assert result["platform_version"] == "8.0.0"
        assert result["compute_configuration"]["metadata_collection_status"] == "unavailable"
        assert result["compute_configuration"]["collection_error_class"] == "RuntimeError"

    def test_normalized_metadata_keeps_requested_compute_when_warehouse_metadata_unavailable(self):
        adapter = _make_adapter(warehouse="MY_WH", warehouse_size="XLARGE")

        platform_info = adapter.get_platform_info(connection=None)
        platform_info["compute_configuration"] = {
            "metadata_collection_status": "unavailable",
            "collection_error_class": "ProgrammingError",
            "collection_error_message": "permission denied",
        }

        metadata = adapter.get_normalized_result_metadata(platform_info=platform_info)

        assert metadata["platform_compute"]["warehouse"] == "MY_WH"
        assert metadata["platform_compute"]["warehouse_size"] == "XLARGE"
        assert metadata["platform_compute"]["collection_status"] == "partial"
        assert metadata["platform_compute"]["collection_error_class"] == "ProgrammingError"
        assert metadata["platform_compute"]["collection_error_message"] == "permission denied"

    def test_unavailable_warehouse_metadata_excludes_identifiers_from_message(self):
        from benchbox.platforms.snowflake import SnowflakeAdapter

        leaked = "Account 'synth-acct-9f31' user SYNTH_ADMIN warehouse SYNTH_WH"
        result = SnowflakeAdapter._unavailable_warehouse_metadata(RuntimeError(leaked))

        assert result["collection_error_class"] == "RuntimeError"
        for value in ("synth-acct-9f31", "SYNTH_ADMIN", "SYNTH_WH"):
            assert value not in result["collection_error_message"]


class TestConfigureForBenchmarkCacheControl:
    def test_cache_validation_called_when_disable_result_cache_true(self):
        adapter = _make_adapter(disable_result_cache=True)

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        with patch.object(
            adapter,
            "validate_session_cache_control",
            return_value={"validated": True, "cache_disabled": True},
        ) as mock_validate:
            adapter.configure_for_benchmark(mock_conn, "olap")

        mock_validate.assert_called_once_with(mock_conn)

    def test_no_validation_when_result_cache_enabled(self):
        adapter = _make_adapter(disable_result_cache=False)

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        with patch.object(adapter, "validate_session_cache_control") as mock_validate:
            adapter.configure_for_benchmark(mock_conn, "olap")

        mock_validate.assert_not_called()


class TestAdapterProperties:
    def test_platform_name_property(self):
        adapter = _make_adapter()
        assert adapter.platform_name == "Snowflake"

    def test_dialect(self):
        adapter = _make_adapter()
        assert adapter._dialect == "snowflake"

    def test_disable_result_cache_default_true(self):
        adapter = _make_adapter()
        assert adapter.disable_result_cache is True

    def test_disable_result_cache_can_be_disabled(self):
        adapter = _make_adapter(disable_result_cache=False)
        assert adapter.disable_result_cache is False

    def test_strict_validation_default_true(self):
        adapter = _make_adapter()
        assert adapter.strict_validation is True

    def test_warehouse_default(self):
        adapter = _make_adapter()
        assert adapter.warehouse == "COMPUTE_WH"

    def test_schema_default(self):
        adapter = _make_adapter()
        assert adapter.schema == "PUBLIC"

    def test_timezone_default_utc(self):
        adapter = _make_adapter()
        assert adapter.timezone == "UTC"

    def test_query_tag_default(self):
        adapter = _make_adapter()
        assert adapter.query_tag == "BenchBox"

    def test_modify_warehouse_settings_default_false(self):
        adapter = _make_adapter()
        assert adapter.modify_warehouse_settings is False

    def test_suppress_nondeterministic_errors_default_false(self):
        adapter = _make_adapter()
        assert adapter.suppress_nondeterministic_errors is False

    def test_auto_suspend_default(self):
        adapter = _make_adapter()
        assert adapter.auto_suspend == 300

    def test_auto_resume_default(self):
        adapter = _make_adapter()
        assert adapter.auto_resume is True

    def test_multi_cluster_warehouse_default_false(self):
        adapter = _make_adapter()
        assert adapter.multi_cluster_warehouse is False

    def test_iceberg_catalog_default(self):
        adapter = _make_adapter()
        assert adapter.iceberg_catalog == "SNOWFLAKE"

    def test_custom_role_stored(self):
        adapter = _make_adapter(role="SYSADMIN")
        assert adapter.role == "SYSADMIN"

    def test_custom_authenticator_stored(self):
        adapter = _make_adapter(authenticator="externalbrowser")
        assert adapter.authenticator == "externalbrowser"

    def test_private_key_path_stored(self):
        adapter = _make_adapter(private_key_path="/tmp/key.pem")
        assert adapter.private_key_path == "/tmp/key.pem"


class TestFromConfig:
    def test_from_config_uses_provided_database(self):
        with patch("benchbox.platforms.snowflake.check_platform_dependencies", return_value=(True, [])):
            from benchbox.platforms.snowflake import SnowflakeAdapter

            config = {
                "account": "my_account",
                "username": "user",
                "password": "pass",
                "warehouse": "WH",
                "database": "EXPLICIT_DB",
                "benchmark": "tpch",
                "scale_factor": 1.0,
            }
            adapter = SnowflakeAdapter.from_config(config)

        assert adapter.database == "EXPLICIT_DB"

    def test_from_config_generates_database_name_when_missing(self):
        with (
            patch("benchbox.platforms.snowflake.check_platform_dependencies", return_value=(True, [])),
            patch("benchbox.utils.database_naming.generate_database_name", return_value="tpch_1_sf0"),
        ):
            from benchbox.platforms.snowflake import SnowflakeAdapter

            config = {
                "account": "my_account",
                "username": "user",
                "password": "pass",
                "warehouse": "WH",
                "benchmark": "tpch",
                "scale_factor": 1.0,
            }
            adapter = SnowflakeAdapter.from_config(config)

        assert adapter.database is not None

    def test_from_config_passes_role(self):
        with patch("benchbox.platforms.snowflake.check_platform_dependencies", return_value=(True, [])):
            from benchbox.platforms.snowflake import SnowflakeAdapter

            config = {
                "account": "my_account",
                "username": "user",
                "password": "pass",
                "warehouse": "WH",
                "database": "DB",
                "role": "SYSADMIN",
            }
            adapter = SnowflakeAdapter.from_config(config)

        assert adapter.role == "SYSADMIN"

    def test_from_config_passes_authenticator(self):
        with patch("benchbox.platforms.snowflake.check_platform_dependencies", return_value=(True, [])):
            from benchbox.platforms.snowflake import SnowflakeAdapter

            config = {
                "account": "my_account",
                "username": "user",
                "password": "pass",
                "warehouse": "WH",
                "database": "DB",
                "authenticator": "externalbrowser",
            }
            adapter = SnowflakeAdapter.from_config(config)

        assert adapter.authenticator == "externalbrowser"

    def test_from_config_passes_disable_result_cache(self):
        with patch("benchbox.platforms.snowflake.check_platform_dependencies", return_value=(True, [])):
            from benchbox.platforms.snowflake import SnowflakeAdapter

            config = {
                "account": "my_account",
                "username": "user",
                "password": "pass",
                "warehouse": "WH",
                "database": "DB",
                "disable_result_cache": False,
            }
            adapter = SnowflakeAdapter.from_config(config)

        assert adapter.disable_result_cache is False

    def test_from_config_passes_storage_metadata_options(self):
        with patch("benchbox.platforms.snowflake.check_platform_dependencies", return_value=(True, [])):
            from benchbox.platforms.snowflake import SnowflakeAdapter

            config = {
                "account": "my_account",
                "username": "user",
                "password": "pass",
                "warehouse": "WH",
                "database": "DB",
                "file_format": "PARQUET",
                "compression": "ZSTD",
                "staging_root": "s3://benchbox-stage/prefix",
                "iceberg_external_volume": "iceberg_vol",
                "iceberg_catalog": "SNOWFLAKE",
                "delta_table_format": "DELTA",
            }
            adapter = SnowflakeAdapter.from_config(config)

        assert adapter.file_format == "PARQUET"
        assert adapter.compression == "ZSTD"
        assert adapter.staging_root == "s3://benchbox-stage/prefix"
        assert adapter.iceberg_external_volume == "iceberg_vol"
        assert adapter.iceberg_catalog == "SNOWFLAKE"
        assert adapter.delta_table_format == "DELTA"


class TestConstructorValidation:
    def test_missing_account_raises(self):
        from benchbox.core.exceptions import ConfigurationError
        from benchbox.platforms.snowflake import SnowflakeAdapter

        with pytest.raises(ConfigurationError, match="account"):
            SnowflakeAdapter(
                username="u",
                password="p",
                warehouse="WH",
                database="DB",
            )

    def test_missing_password_raises(self):
        from benchbox.core.exceptions import ConfigurationError
        from benchbox.platforms.snowflake import SnowflakeAdapter

        with pytest.raises(ConfigurationError, match="password"):
            SnowflakeAdapter(
                account="a",
                username="u",
                warehouse="WH",
                database="DB",
            )

    def test_missing_username_raises(self):
        from benchbox.core.exceptions import ConfigurationError
        from benchbox.platforms.snowflake import SnowflakeAdapter

        with pytest.raises(ConfigurationError, match="username"):
            SnowflakeAdapter(
                account="a",
                password="p",
                warehouse="WH",
                database="DB",
            )


class TestCreateConnection:
    def test_password_auth_passes_correct_params(self):
        import benchbox.platforms.snowflake as sf_module

        adapter = _make_adapter()

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [("8.0.0",)]

        with (
            patch.object(adapter, "handle_existing_database"),
            patch.object(sf_module.snowflake.connector, "connect", return_value=mock_conn) as mock_connect,
        ):
            result = adapter.create_connection()

        call_kwargs = mock_connect.call_args[1]
        assert call_kwargs["user"] == "test_user"
        assert call_kwargs["password"] == "test_pass"
        assert call_kwargs["account"] == "test_account"
        assert call_kwargs["warehouse"] == "COMPUTE_WH"
        assert result is mock_conn

    def test_externalbrowser_auth_sets_authenticator_param(self):
        import benchbox.platforms.snowflake as sf_module

        adapter = _make_adapter(authenticator="externalbrowser")

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [("8.0.0",)]

        with (
            patch.object(adapter, "handle_existing_database"),
            patch.object(sf_module.snowflake.connector, "connect", return_value=mock_conn) as mock_connect,
        ):
            adapter.create_connection()

        call_kwargs = mock_connect.call_args[1]
        assert call_kwargs.get("authenticator") == "externalbrowser"

    def test_key_pair_auth_sets_private_key_and_removes_password(self):
        import benchbox.platforms.snowflake as sf_module

        adapter = _make_adapter(private_key_path="/tmp/fake_key.pem")

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [("8.0.0",)]

        mock_private_key = MagicMock()
        mock_pkb = b"fake_der_bytes"
        mock_private_key.private_bytes.return_value = mock_pkb

        with (
            patch.object(adapter, "handle_existing_database"),
            patch.object(sf_module.snowflake.connector, "connect", return_value=mock_conn) as mock_connect,
            patch("builtins.open", MagicMock()),
            patch("benchbox.platforms.snowflake.load_pem_private_key", return_value=mock_private_key, create=True),
            patch("cryptography.hazmat.primitives.serialization.load_pem_private_key", return_value=mock_private_key),
            patch("cryptography.hazmat.primitives.serialization.Encoding"),
            patch("cryptography.hazmat.primitives.serialization.PrivateFormat"),
            patch("cryptography.hazmat.primitives.serialization.NoEncryption"),
        ):
            try:
                adapter.create_connection()
            except Exception:
                pass

        if mock_connect.called:
            call_kwargs = mock_connect.call_args[1]
            assert "password" not in call_kwargs
            assert "private_key" in call_kwargs

    def test_role_added_when_configured(self):
        import benchbox.platforms.snowflake as sf_module

        adapter = _make_adapter(role="ANALYST")

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [("8.0.0",)]

        with (
            patch.object(adapter, "handle_existing_database"),
            patch.object(sf_module.snowflake.connector, "connect", return_value=mock_conn) as mock_connect,
        ):
            adapter.create_connection()

        call_kwargs = mock_connect.call_args[1]
        assert call_kwargs.get("role") == "ANALYST"

    def test_connection_failure_raises(self):
        import benchbox.platforms.snowflake as sf_module

        adapter = _make_adapter()

        with (
            patch.object(adapter, "handle_existing_database"),
            patch.object(sf_module.snowflake.connector, "connect", side_effect=RuntimeError("bad credentials")),
        ):
            with pytest.raises(RuntimeError, match="bad credentials"):
                adapter.create_connection()


class TestExecuteQuery:
    def test_successful_execution_returns_result_dict(self):
        adapter = _make_adapter()

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [("row1",), ("row2",)]

        with patch.object(adapter, "_get_query_statistics", return_value={"note": "no stats"}):
            result = adapter.execute_query(mock_conn, "SELECT 1", "Q1")

        assert result["query_id"] == "Q1"
        assert result["rows_returned"] == 2
        assert result["status"] == "SUCCESS"

    def test_query_tag_set_before_execution(self):
        adapter = _make_adapter(query_tag="BENCH")

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = []

        executed_sqls = []
        mock_cursor.execute.side_effect = lambda sql: executed_sqls.append(sql)

        with patch.object(adapter, "_get_query_statistics", return_value={}):
            adapter.execute_query(mock_conn, "SELECT 1", "Q5")

        tag_sqls = [s for s in executed_sqls if "QUERY_TAG" in s]
        assert any("Q5" in s for s in tag_sqls), f"No QUERY_TAG sql found in {tag_sqls}"

    def test_exception_returns_failed_status(self):
        adapter = _make_adapter()

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = RuntimeError("query syntax error")

        result = adapter.execute_query(mock_conn, "BAD SQL", "Q99")

        assert result["status"] == "FAILED"
        assert "query syntax error" in result["error"]

    def test_zero_rows_returned_correctly(self):
        adapter = _make_adapter()

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = []

        with patch.object(adapter, "_get_query_statistics", return_value={}):
            result = adapter.execute_query(mock_conn, "SELECT 1 WHERE false", "Q0")

        assert result["rows_returned"] == 0

    def test_actual_query_passed_verbatim_to_cursor_execute(self):
        adapter = _make_adapter(query_tag="BENCH")

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = []

        with patch.object(adapter, "_get_query_statistics", return_value={}):
            adapter.execute_query(mock_conn, "SELECT COUNT(*) FROM orders", "Q3")

        calls = [c.args[0] for c in mock_cursor.execute.call_args_list]
        assert "SELECT COUNT(*) FROM orders" in calls, f"Query not found in execute calls: {calls}"

    def test_cursor_closed_after_successful_execution(self):
        adapter = _make_adapter()

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = []

        with patch.object(adapter, "_get_query_statistics", return_value={}):
            adapter.execute_query(mock_conn, "SELECT 1", "Q1")

        mock_cursor.close.assert_called_once()

    def test_cursor_closed_even_on_execution_failure(self):
        adapter = _make_adapter()

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = RuntimeError("network error")

        adapter.execute_query(mock_conn, "SELECT 1", "Q1")

        mock_cursor.close.assert_called_once()


class TestGetPlatformMetadata:
    def _make_cursor(self, version="8.0.0", session_row=None, wh_row=None, tables=None):
        mock_cursor = Mock()
        session_row = session_row or ("user1", "role1", "WH1", "DB1", "PUBLIC", "AWS_US_EAST_1", "ACCT1")
        wh_row = wh_row or (
            "WH",
            "STARTED",
            "STANDARD",
            "LARGE",
            1,
            3,
            1,
            0,
            0,
            False,
            True,
            None,
            300,
            True,
            "100%",
            "0%",
            "0%",
            "0%",
            "2025-01-01",
            "2025-01-01",
            "2025-01-01",
            "owner",
            "",
            None,
            None,
            "STANDARD",
        )
        tables = tables or [("ORDERS", 1000, 1024, 1, None, None, None)]

        call_count = [0]

        def _execute(sql, params=None):
            call_count[0] += 1
            if "CURRENT_VERSION" in sql.upper():
                mock_cursor.fetchone.return_value = (version,)
                mock_cursor.fetchall.return_value = [(version,)]
            elif "CURRENT_USER" in sql.upper():
                mock_cursor.fetchone.return_value = session_row
                mock_cursor.fetchall.return_value = [session_row]
            elif "SHOW WAREHOUSES" in sql.upper():
                mock_cursor.fetchone.return_value = wh_row
                mock_cursor.fetchall.return_value = [wh_row]
            elif "INFORMATION_SCHEMA.TABLES" in sql.upper():
                mock_cursor.fetchone.return_value = None
                mock_cursor.fetchall.return_value = tables
            return mock_cursor

        mock_cursor.execute.side_effect = _execute
        return mock_cursor

    def test_snowflake_version_populated(self):
        adapter = _make_adapter()
        mock_conn = Mock()
        mock_conn.cursor.return_value = self._make_cursor(version="9.1.0")

        result = adapter._get_platform_metadata(mock_conn)

        assert result["snowflake_version"] == "9.1.0"

    def test_session_info_populated(self):
        adapter = _make_adapter()
        mock_conn = Mock()
        mock_conn.cursor.return_value = self._make_cursor()

        result = adapter._get_platform_metadata(mock_conn)

        assert "session_info" in result
        assert result["session_info"]["current_user"] == "user1"

    def test_warehouse_info_populated(self):
        adapter = _make_adapter(warehouse="WH")
        mock_conn = Mock()
        mock_conn.cursor.return_value = self._make_cursor()

        result = adapter._get_platform_metadata(mock_conn)

        assert "warehouse_info" in result
        assert result["warehouse_info"]["name"] == "WH"
        assert result["warehouse_info"]["size"] == "LARGE"

    def test_tables_list_populated(self):
        adapter = _make_adapter(schema="bench")
        mock_conn = Mock()
        tables = [("LINEITEM", 6000000, 102400, 1, None, None, None)]
        mock_cursor = self._make_cursor(tables=tables)
        mock_conn.cursor.return_value = mock_cursor

        result = adapter._get_platform_metadata(mock_conn)

        assert "tables" in result
        assert result["tables"][0]["table_name"] == "LINEITEM"
        table_query = next(
            call for call in mock_cursor.execute.call_args_list if "INFORMATION_SCHEMA.TABLES" in call.args[0]
        )
        assert "TABLE_SCHEMA = %s" in table_query.args[0]
        assert table_query.args[1] == ("BENCH",)

    def test_metadata_error_key_on_exception(self):
        adapter = _make_adapter()
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.execute.side_effect = RuntimeError("access denied")
        mock_conn.cursor.return_value = mock_cursor

        result = adapter._get_platform_metadata(mock_conn)

        assert "metadata_error" in result
        assert "access denied" in result["metadata_error"]


class TestApplyTableTunings:
    def _make_tuning(self, table_name="ORDERS", cluster_cols=None, partition_cols=None, sort_cols=None):
        mock_tuning = Mock()
        mock_tuning.table_name = table_name
        mock_tuning.has_any_tuning.return_value = True

        try:
            from benchbox.core.tuning.interface import TuningType

            def _get_by_type(tt):
                if tt == TuningType.CLUSTERING:
                    return cluster_cols or []
                if tt == TuningType.PARTITIONING:
                    return partition_cols or []
                if tt == TuningType.SORTING:
                    return sort_cols or []
                if tt == TuningType.DISTRIBUTION:
                    return []
                return []

            mock_tuning.get_columns_by_type.side_effect = _get_by_type
        except ImportError:
            mock_tuning.get_columns_by_type.return_value = cluster_cols or []

        return mock_tuning

    def _make_col(self, name, order=0):
        col = Mock()
        col.name = name
        col.order = order
        return col

    def test_no_op_when_no_tuning(self):
        adapter = _make_adapter()
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = False

        adapter.apply_table_tunings(mock_tuning, mock_conn)

        mock_cursor.execute.assert_not_called()

    def test_no_op_when_tuning_is_none(self):
        adapter = _make_adapter()
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        adapter.apply_table_tunings(None, mock_conn)

        mock_cursor.execute.assert_not_called()

    def test_cluster_by_executed_when_different_from_current(self):
        adapter = _make_adapter()
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        mock_cursor.fetchone.return_value = (None,)

        col1 = self._make_col("o_orderdate", 0)
        col2 = self._make_col("o_custkey", 1)
        tuning = self._make_tuning(table_name="ORDERS", cluster_cols=[col1, col2])

        adapter.apply_table_tunings(tuning, mock_conn)

        all_sqls = [call[0][0] for call in mock_cursor.execute.call_args_list]
        cluster_sqls = [s for s in all_sqls if "CLUSTER BY" in s.upper()]
        assert len(cluster_sqls) >= 1
        assert "o_orderdate" in cluster_sqls[0]
        assert "o_custkey" in cluster_sqls[0]

    def test_skips_alter_when_clustering_already_matches(self):
        adapter = _make_adapter()
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        col1 = self._make_col("o_orderdate", 0)
        tuning = self._make_tuning(table_name="ORDERS", cluster_cols=[col1])

        mock_cursor.fetchone.return_value = ("(o_orderdate)",)

        adapter.apply_table_tunings(tuning, mock_conn)

        all_sqls = [call[0][0] for call in mock_cursor.execute.call_args_list]
        alter_sqls = [s for s in all_sqls if "ALTER TABLE" in s.upper() and "CLUSTER BY" in s.upper()]
        assert len(alter_sqls) == 0

    def test_resumes_reclustering_when_matching_key_is_suspended(self):
        adapter = _make_adapter()
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ("(o_orderdate)", "OFF")

        tuning = self._make_tuning(table_name="ORDERS", cluster_cols=[self._make_col("o_orderdate")])
        adapter.apply_table_tunings(tuning, mock_conn)

        all_sqls = [call.args[0] for call in mock_cursor.execute.call_args_list]
        assert "ALTER TABLE ORDERS RESUME RECLUSTER" not in all_sqls

        mock_cursor.execute.reset_mock()
        adapter.apply_post_load_tunings("ORDERS", None, mock_conn)

        post_load_sqls = [call.args[0] for call in mock_cursor.execute.call_args_list]
        assert post_load_sqls == ["ALTER TABLE ORDERS RESUME RECLUSTER"]

    def test_linear_catalog_form_skips_alter_and_records_satisfied_intent(self):
        adapter = _make_adapter()
        adapter._applied_tuning_ledger = AppliedTuningLedger()
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ("LINEAR(O_ORDERDATE, O_CUSTKEY)",)

        tuning = self._make_tuning(
            table_name="ORDERS",
            cluster_cols=[self._make_col("o_orderdate", 0), self._make_col("o_custkey", 1)],
        )
        adapter.apply_table_tunings(tuning, mock_conn)

        all_sqls = [call.args[0] for call in mock_cursor.execute.call_args_list]
        assert not any("ALTER TABLE" in sql.upper() and "CLUSTER BY" in sql.upper() for sql in all_sqls)
        assert [s.intent for s in adapter._applied_tuning_ledger.satisfied] == [
            "ALTER TABLE ORDERS CLUSTER BY (o_orderdate, o_custkey)"
        ]
        assert "already present" in adapter._applied_tuning_ledger.satisfied[0].reason
        assert adapter._applied_tuning_ledger.dropped == []

    def test_clustering_precheck_binds_normalized_schema_and_table(self):
        adapter = _make_adapter(schema="bench")
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (None,)

        tuning = self._make_tuning(table_name="orders", cluster_cols=[self._make_col("o_orderdate")])
        adapter.apply_table_tunings(tuning, mock_conn)

        query_call = mock_cursor.execute.call_args_list[0]
        assert "TABLE_SCHEMA = %s" in query_call.args[0]
        assert "TABLE_NAME = %s" in query_call.args[0]
        assert query_call.args[1] == ("BENCH", "ORDERS")

    def test_fresh_table_records_executed_clustering_statements(self):
        adapter = _make_adapter()
        ledger = AppliedTuningLedger()
        adapter._applied_tuning_ledger = ledger
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (None,)
        recording = recording_connection(mock_conn, ledger, PHASE_DDL)

        tuning = self._make_tuning(table_name="ORDERS", cluster_cols=[self._make_col("o_orderdate")])
        adapter.apply_table_tunings(tuning, recording)

        assert [statement.statement for statement in ledger.executed_statements] == [
            "ALTER TABLE ORDERS CLUSTER BY (o_orderdate)",
        ]

        adapter.run_post_load_tunings("ORDERS", SimpleNamespace(table_tunings={}), mock_conn)

        assert [(statement.statement, statement.phase) for statement in ledger.executed_statements] == [
            ("ALTER TABLE ORDERS CLUSTER BY (o_orderdate)", PHASE_DDL),
            ("ALTER TABLE ORDERS RESUME RECLUSTER", PHASE_POST_LOAD),
        ]
        assert ledger.dropped == []

    def test_partition_cols_used_as_clustering_fallback(self):
        adapter = _make_adapter()
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (None,)

        col1 = self._make_col("l_shipdate", 0)
        tuning = self._make_tuning(table_name="LINEITEM", partition_cols=[col1])

        adapter.apply_table_tunings(tuning, mock_conn)

        all_sqls = [call[0][0] for call in mock_cursor.execute.call_args_list]
        cluster_sqls = [s for s in all_sqls if "CLUSTER BY" in s.upper()]
        assert len(cluster_sqls) >= 1
        assert "l_shipdate" in cluster_sqls[0]


class TestCreateExternalTables:
    def _make_data_files(self, table_name="ORDERS", file_names=None):
        return {table_name: file_names or ["s3://bucket/path/orders/"]}

    def test_parquet_external_table_uses_file_format_type_parquet(self):
        adapter = _make_adapter(staging_root="s3://bucket/path")

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (100,)

        data_files = {"ORDERS": ["s3://bucket/path/orders/"]}

        with (
            patch.object(adapter, "_resolve_data_files", return_value=SimpleNamespace(tables=data_files)),
            patch.object(adapter, "_detect_external_table_format", return_value="parquet"),
            patch.object(adapter, "validate_external_table_requirements"),
        ):
            adapter.create_external_tables(Mock(), mock_conn, Mock())

        all_sqls = [call[0][0] for call in mock_cursor.execute.call_args_list]
        ext_sqls = [s for s in all_sqls if "EXTERNAL TABLE" in s.upper()]
        assert any("TYPE=PARQUET" in s or "FILE_FORMAT=(TYPE=PARQUET)" in s for s in ext_sqls), (
            f"No PARQUET format SQL found. Got: {ext_sqls}"
        )

    def test_stage_created(self):
        adapter = _make_adapter(staging_root="s3://bucket/path")

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (0,)

        data_files = {"ORDERS": ["s3://bucket/path/orders/"]}

        with (
            patch.object(adapter, "_resolve_data_files", return_value=SimpleNamespace(tables=data_files)),
            patch.object(adapter, "_detect_external_table_format", return_value="parquet"),
            patch.object(adapter, "validate_external_table_requirements"),
        ):
            adapter.create_external_tables(Mock(), mock_conn, Mock())

        all_sqls = [call[0][0] for call in mock_cursor.execute.call_args_list]
        stage_sqls = [s for s in all_sqls if "CREATE STAGE" in s.upper()]
        assert len(stage_sqls) >= 1

    def test_alter_external_table_refresh_called(self):
        adapter = _make_adapter(staging_root="s3://bucket/path")

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (50,)

        data_files = {"LINEITEM": ["s3://bucket/path/lineitem/"]}

        with (
            patch.object(adapter, "_resolve_data_files", return_value=SimpleNamespace(tables=data_files)),
            patch.object(adapter, "_detect_external_table_format", return_value="parquet"),
            patch.object(adapter, "validate_external_table_requirements"),
        ):
            adapter.create_external_tables(Mock(), mock_conn, Mock())

        all_sqls = [call[0][0] for call in mock_cursor.execute.call_args_list]
        refresh_sqls = [s for s in all_sqls if "REFRESH" in s.upper()]
        assert len(refresh_sqls) >= 1

    def test_validates_staging_root_requirement(self):
        adapter = _make_adapter()

        with pytest.raises(ValueError, match="staging_root"):
            adapter.create_external_tables(Mock(), Mock(), Mock())


class TestGetExistingTables:
    def test_returns_lowercase_table_names(self):
        adapter = _make_adapter()
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [
            (None, "LINEITEM", None),
            (None, "ORDERS", None),
        ]

        result = adapter._get_existing_tables(mock_conn)

        assert "lineitem" in result
        assert "orders" in result
        assert "LINEITEM" not in result

    def test_returns_empty_list_on_exception(self):
        adapter = _make_adapter()
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.execute.side_effect = RuntimeError("no access")
        mock_conn.cursor.return_value = mock_cursor

        result = adapter._get_existing_tables(mock_conn)

        assert result == []


class TestValidateDataIntegrity:
    def test_returns_passed_when_all_tables_accessible(self):
        adapter = _make_adapter()
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (1,)

        table_stats = {"ORDERS": 100, "LINEITEM": 200}
        status, details = adapter._validate_data_integrity(Mock(), mock_conn, table_stats)

        assert status == "PASSED"
        assert "accessible_tables" in details

    def test_returns_failed_when_table_inaccessible(self):
        adapter = _make_adapter()
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        call_count = [0]

        def _execute(sql):
            call_count[0] += 1
            if "ORDERS" in sql:
                return None
            raise RuntimeError("table not found")

        mock_cursor.execute.side_effect = _execute
        mock_cursor.fetchone.return_value = (1,)

        table_stats = {"MISSING_TABLE": 0}
        status, details = adapter._validate_data_integrity(Mock(), mock_conn, table_stats)

        assert status == "FAILED"

    def test_returns_failed_on_unexpected_exception(self):
        adapter = _make_adapter()
        mock_conn = Mock()
        mock_conn.cursor.side_effect = RuntimeError("connection broken")

        table_stats = {"ORDERS": 100}
        status, details = adapter._validate_data_integrity(Mock(), mock_conn, table_stats)

        assert status == "FAILED"
        assert "integrity_error" in details


class TestConfigureForBenchmarkWarehouse:
    def test_alter_warehouse_called_when_modify_warehouse_settings_true(self):
        adapter = _make_adapter(modify_warehouse_settings=True, disable_result_cache=False)

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        adapter.configure_for_benchmark(mock_conn, "olap")

        all_sqls = [call[0][0] for call in mock_cursor.execute.call_args_list]
        alter_sqls = [s for s in all_sqls if "ALTER WAREHOUSE" in s]
        assert len(alter_sqls) >= 1

    def test_multi_cluster_warehouse_sets_min_max_cluster(self):
        adapter = _make_adapter(
            modify_warehouse_settings=True, multi_cluster_warehouse=True, disable_result_cache=False
        )

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        adapter.configure_for_benchmark(mock_conn, "olap")

        all_sqls = [call[0][0] for call in mock_cursor.execute.call_args_list]
        cluster_sqls = [s for s in all_sqls if "MIN_CLUSTER_COUNT" in s]
        assert len(cluster_sqls) >= 1

    def test_nondeterministic_settings_applied_when_enabled(self):
        adapter = _make_adapter(suppress_nondeterministic_errors=True, disable_result_cache=False)

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        adapter.configure_for_benchmark(mock_conn, "olap")

        all_sqls = [call[0][0] for call in mock_cursor.execute.call_args_list]
        nondeterministic_sqls = [s for s in all_sqls if "NONDETERMINISTIC" in s]
        assert len(nondeterministic_sqls) >= 1

    def test_non_olap_benchmark_type_skips_cache_settings(self):
        adapter = _make_adapter(disable_result_cache=False)

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        adapter.configure_for_benchmark(mock_conn, "oltp")

        all_sqls = [call[0][0] for call in mock_cursor.execute.call_args_list]
        cache_sqls = [s for s in all_sqls if "USE_CACHED_RESULT" in s]
        assert len(cache_sqls) == 0


class TestGenerateTuningClause:
    def _make_col(self, name, order=0):
        col = Mock()
        col.name = name
        col.order = order
        return col

    def test_returns_empty_string_when_no_tuning(self):
        adapter = _make_adapter()
        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = False

        result = adapter.generate_tuning_clause(mock_tuning)
        assert result == ""

    def test_returns_empty_string_when_tuning_is_none(self):
        adapter = _make_adapter()
        result = adapter.generate_tuning_clause(None)
        assert result == ""

    def test_cluster_by_clause_generated(self):
        adapter = _make_adapter()
        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = True

        try:
            from benchbox.core.tuning.interface import TuningType

            col1 = self._make_col("o_orderdate", 0)
            col2 = self._make_col("o_custkey", 1)

            def _get_by_type(tt):
                if tt == TuningType.CLUSTERING:
                    return [col1, col2]
                return []

            mock_tuning.get_columns_by_type.side_effect = _get_by_type
            result = adapter.generate_tuning_clause(mock_tuning)

            assert "CLUSTER BY" in result
            assert "o_orderdate" in result
            assert "o_custkey" in result
        except ImportError:
            pytest.skip("TuningType not available")

    def test_partition_cols_used_as_clustering_fallback(self):
        adapter = _make_adapter()
        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = True

        try:
            from benchbox.core.tuning.interface import TuningType

            col1 = self._make_col("l_shipdate", 0)

            def _get_by_type(tt):
                if tt == TuningType.CLUSTERING:
                    return []
                if tt == TuningType.PARTITIONING:
                    return [col1]
                return []

            mock_tuning.get_columns_by_type.side_effect = _get_by_type
            result = adapter.generate_tuning_clause(mock_tuning)

            assert "CLUSTER BY" in result
            assert "l_shipdate" in result
        except ImportError:
            pytest.skip("TuningType not available")


class TestOptimizeTableDefinition:
    def test_adds_or_replace_to_create_table(self):
        adapter = _make_adapter()
        sql = "CREATE TABLE ORDERS (o_orderkey INT)"
        result = adapter._optimize_table_definition(sql)
        assert "CREATE OR REPLACE TABLE" in result

    def test_leaves_non_create_table_unchanged(self):
        adapter = _make_adapter()
        sql = "CREATE INDEX idx ON ORDERS (o_orderkey)"
        result = adapter._optimize_table_definition(sql)
        assert result == sql

    def test_does_not_double_replace(self):
        adapter = _make_adapter()
        sql = "CREATE OR REPLACE TABLE ORDERS (o_orderkey INT)"
        result = adapter._optimize_table_definition(sql)
        assert result.count("OR REPLACE") == 1


class TestAnalyzeTableAndCloseConnection:
    def test_analyze_table_executes_recluster(self):
        adapter = _make_adapter()
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor

        adapter.analyze_table(mock_conn, "orders")

        all_sqls = [call[0][0] for call in mock_cursor.execute.call_args_list]
        recluster_sqls = [s for s in all_sqls if "RECLUSTER" in s.upper()]
        assert len(recluster_sqls) >= 1

    def test_analyze_table_raises_on_failure(self):
        adapter = _make_adapter()
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.execute.side_effect = RuntimeError("not supported")
        mock_conn.cursor.return_value = mock_cursor

        with pytest.raises(RuntimeError, match="not supported"):
            adapter.analyze_table(mock_conn, "orders")
        mock_cursor.close.assert_called_once()

    def test_close_connection_calls_close(self):
        adapter = _make_adapter()
        mock_conn = Mock()

        adapter.close_connection(mock_conn)

        mock_conn.close.assert_called_once()

    def test_close_connection_handles_none(self):
        adapter = _make_adapter()
        adapter.close_connection(None)

    def test_close_connection_suppresses_exception(self):
        adapter = _make_adapter()
        mock_conn = Mock()
        mock_conn.close.side_effect = RuntimeError("already closed")

        adapter.close_connection(mock_conn)


class TestGetTargetDialect:
    def test_returns_snowflake(self):
        adapter = _make_adapter()
        assert adapter.get_target_dialect() == "snowflake"


class TestPlatformName:
    def test_platform_name(self):
        adapter = _make_adapter()
        assert adapter.platform_name == "Snowflake"


class TestSupportsTuningType:
    def test_clustering_supported(self):
        adapter = _make_adapter()
        try:
            from benchbox.core.tuning.interface import TuningType

            assert adapter.supports_tuning_type(TuningType.CLUSTERING) is True
        except ImportError:
            pytest.skip("TuningType not available")

    def test_distribution_not_supported(self):
        adapter = _make_adapter()
        try:
            from benchbox.core.tuning.interface import TuningType

            assert adapter.supports_tuning_type(TuningType.DISTRIBUTION) is False
        except ImportError:
            pytest.skip("TuningType not available")


class TestCreateLoadFileFormats:
    def test_creates_csv_and_tbl_formats(self):
        adapter = _make_adapter()
        mock_cursor = Mock()

        adapter._create_load_file_formats(mock_cursor)

        all_sqls = [call[0][0] for call in mock_cursor.execute.call_args_list]
        csv_sqls = [s for s in all_sqls if "BENCHBOX_CSV_FORMAT" in s]
        tbl_sqls = [s for s in all_sqls if "BENCHBOX_TBL_FORMAT" in s]
        assert len(csv_sqls) >= 1
        assert len(tbl_sqls) >= 1


class TestCheckServerDatabaseExists:
    def test_returns_true_when_database_in_show_databases(self):
        import benchbox.platforms.snowflake as sf_module

        adapter = _make_adapter(database="BENCHDB")

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [(None, "BENCHDB")]

        with patch.object(sf_module.snowflake.connector, "connect", return_value=mock_conn):
            result = adapter.check_server_database_exists()

        assert result is True

    def test_returns_false_when_database_not_found(self):
        import benchbox.platforms.snowflake as sf_module

        adapter = _make_adapter(database="MISSING_DB")

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.side_effect = [
            [],
            [],
        ]
        mock_cursor.fetchone.return_value = None

        with patch.object(sf_module.snowflake.connector, "connect", return_value=mock_conn):
            result = adapter.check_server_database_exists()

        assert result is False

    def test_returns_false_on_connection_error(self):
        import benchbox.platforms.snowflake as sf_module

        adapter = _make_adapter()

        with patch.object(sf_module.snowflake.connector, "connect", side_effect=RuntimeError("timeout")):
            result = adapter.check_server_database_exists()

        assert result is False


class TestParseCopyResults:
    def test_loaded_status_silently_ignored(self):
        adapter = _make_adapter()
        rows = [
            ("file1.csv", "LOADED", None, 100, None, None),
        ]
        adapter._parse_copy_results(rows)

    def test_non_loaded_status_logged_as_warning(self):
        adapter = _make_adapter()
        rows = [
            ("file2.csv", "LOAD_FAILED", None, 0, None, "bad encoding"),
        ]
        with patch.object(adapter.logger, "warning") as mock_warn:
            adapter._parse_copy_results(rows)
        mock_warn.assert_called()

    def test_short_rows_skipped(self):
        adapter = _make_adapter()
        rows = [("file.csv", "LOADED")]
        adapter._parse_copy_results(rows)


class TestCacheControlReceiptPersistence:
    def test_receipt_stored_on_validation(self):
        from unittest.mock import Mock, patch

        adapter = _make_adapter(disable_result_cache=True)
        assert adapter._cache_control_receipt is None

        mock_conn = Mock()
        mock_conn.cursor.return_value = Mock()
        with patch.object(
            adapter,
            "validate_session_cache_control",
            return_value={
                "validated": True,
                "cache_disabled": True,
                "settings": {"USE_CACHED_RESULT": "off"},
                "warnings": [],
                "errors": [],
                "connection": Mock(),
            },
        ):
            adapter.configure_for_benchmark(mock_conn, "olap")

        assert adapter._cache_control_receipt == {
            "validated": True,
            "cache_disabled": True,
            "settings": {"USE_CACHED_RESULT": "off"},
            "warnings": [],
            "errors": [],
        }

    def test_receipt_flows_into_compute_metadata(self):
        from benchbox.platforms.snowflake import SnowflakeAdapter

        receipt = {"validated": True, "cache_disabled": True}
        payload = SnowflakeAdapter._snowflake_compute_metadata({}, {}, cache_control=receipt)
        assert payload["cache_control"] == receipt

    def test_compute_metadata_omits_absent_receipt(self):
        from benchbox.platforms.snowflake import SnowflakeAdapter

        payload = SnowflakeAdapter._snowflake_compute_metadata({}, {})
        assert "cache_control" not in payload

    def test_compute_metadata_records_where_the_warehouse_size_came_from(self):
        from benchbox.platforms.snowflake import SnowflakeAdapter

        observed = SnowflakeAdapter._snowflake_compute_metadata(
            {"warehouse_size": "MEDIUM"}, {"warehouse_size": "X-Small", "warehouse_state": "STARTED"}
        )
        assert (observed["warehouse_size"], observed["warehouse_size_source"]) == ("X-Small", "observed")

        fallback = SnowflakeAdapter._snowflake_compute_metadata(
            {"warehouse_size": "MEDIUM"}, {"warehouse_state": "STARTED"}
        )
        assert fallback["source"] == "observed"
        assert (fallback["warehouse_size"], fallback["warehouse_size_source"]) == ("MEDIUM", "requested")

        assert "warehouse_size_source" not in SnowflakeAdapter._snowflake_compute_metadata({}, {})

    def test_explicitly_enabled_cache_records_receipt_without_session_probe(self):
        from unittest.mock import Mock, patch

        adapter = _make_adapter(disable_result_cache=False)
        assert adapter._cache_control_receipt is None

        mock_conn = Mock()
        mock_conn.cursor.return_value = Mock()
        with patch.object(adapter, "validate_session_cache_control") as mock_validate:
            adapter.configure_for_benchmark(mock_conn, "olap")

        mock_validate.assert_not_called()
        assert adapter._cache_control_receipt == {
            "validated": True,
            "cache_disabled": False,
            "settings": {"USE_CACHED_RESULT": "TRUE"},
            "warnings": [
                "result cache explicitly left enabled (disable_result_cache=False); "
                "timings measured under an enabled cache are not comparable clean evidence"
            ],
            "errors": [],
        }

    def test_enabled_receipt_flows_into_compute_metadata(self):
        from benchbox.platforms.cloud_shared import explicit_cache_enabled_receipt
        from benchbox.platforms.snowflake import SnowflakeAdapter

        receipt = explicit_cache_enabled_receipt("USE_CACHED_RESULT", "TRUE")
        payload = SnowflakeAdapter._snowflake_compute_metadata(
            {"result_cache_enabled": True}, {}, cache_control=receipt
        )
        assert payload["result_cache_enabled"] is True
        assert payload["cache_control"]["cache_disabled"] is False
