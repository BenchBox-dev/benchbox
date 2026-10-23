# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from benchbox.platforms.base import DriverIsolationCapability
from benchbox.platforms.base.data_loading import NO_BENCHMARK, DataSource
from benchbox.platforms.snowflake import SnowflakeAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
    pytest.mark.cloud_import,
]


@pytest.fixture(autouse=True)
def snowflake_dependencies():

    with patch("benchbox.platforms.snowflake.check_platform_dependencies", return_value=(True, [])):
        yield


class TestSnowflakeAdapter:
    def test_initialization_success(self):
        with patch("benchbox.platforms.snowflake.snowflake"):
            adapter = SnowflakeAdapter(
                account="test_account",
                username="test_user",
                password="test_pass",
                warehouse="TEST_WH",
                database="TEST_DB",
                schema="TEST_SCHEMA",
            )
            assert adapter.platform_name == "Snowflake"
            assert adapter.get_target_dialect() == "snowflake"
            assert adapter.account == "test_account"
            assert adapter.username == "test_user"
            assert adapter.warehouse == "TEST_WH"
            assert adapter.database == "TEST_DB"

    def test_initialization_with_defaults(self):
        with patch("benchbox.platforms.snowflake.snowflake"):
            adapter = SnowflakeAdapter(account="test_account", username="test_user", password="test_pass")
            assert adapter.warehouse == "COMPUTE_WH"
            assert adapter.database == "BENCHBOX"
            assert adapter.schema == "PUBLIC"
            assert adapter.authenticator == "snowflake"
            assert adapter.warehouse_size == "MEDIUM"
            assert adapter.auto_suspend == 300
            assert adapter.auto_resume is True

    def test_external_table_capability_declared(self):
        with patch("benchbox.platforms.snowflake.snowflake"):
            adapter = SnowflakeAdapter(account="test_account", username="test_user", password="test_pass")
            assert adapter.supports_external_tables is True

    def test_named_stage_is_rejected_as_native_staging_root(self):
        with patch("benchbox.platforms.snowflake.snowflake"):
            with pytest.raises(ValueError, match="named or table stage"):
                SnowflakeAdapter(
                    account="test_account",
                    username="test_user",
                    password="test_pass",
                    staging_root="@my_stage/data",
                )

    def test_stage_reference_is_rejected_for_external_table_mode(self):
        with patch("benchbox.platforms.snowflake.snowflake"):
            with pytest.raises(ValueError, match="requires a cloud URI"):
                SnowflakeAdapter(
                    account="test_account",
                    username="test_user",
                    password="test_pass",
                    staging_root="@~/benchbox",
                    table_mode="external",
                )

    def test_initialization_missing_driver(self):
        with (
            patch("benchbox.platforms.snowflake.snowflake", None),
            patch(
                "benchbox.platforms.snowflake.check_platform_dependencies",
                return_value=(False, ["snowflake-connector-python"]),
            ),
            pytest.raises(ImportError) as excinfo,
        ):
            SnowflakeAdapter()

        assert "Missing dependencies for snowflake platform" in str(excinfo.value)

    def test_initialization_missing_required_config(self):
        from benchbox.core.exceptions import ConfigurationError

        with patch("benchbox.platforms.snowflake.snowflake"):
            with pytest.raises(ConfigurationError, match="Snowflake configuration is incomplete"):
                SnowflakeAdapter()

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_connect_params(self, mock_snowflake):
        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
            role="TEST_ROLE",
        )

        params = adapter._connect_params({}, include_database=False)

        assert params["account"] == "test_account"
        assert params["user"] == "test_user"
        assert params["password"] == "test_pass"
        assert params["warehouse"] == "TEST_WH"
        assert params["role"] == "TEST_ROLE"

        override_params = adapter._connect_params(
            {"account": "override_account", "username": "override_user"},
            include_database=False,
        )
        assert override_params["account"] == "override_account"
        assert override_params["user"] == "override_user"
        assert override_params["password"] == "test_pass"

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_from_config_generates_database_name(self, mock_snowflake):
        config = {
            "benchmark": "tpch",
            "scale_factor": 1.0,
            "account": "test_account",
            "username": "test_user",
            "password": "test_pass",
            "warehouse": "TEST_WH",
            "tuning_config": None,
        }

        adapter = SnowflakeAdapter.from_config(config)

        assert adapter.database == "tpch_sf1_notuning_noconstraints"
        assert adapter.account == "test_account"
        assert adapter.warehouse == "TEST_WH"

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_from_config_respects_explicit_database(self, mock_snowflake):
        config = {
            "benchmark": "tpch",
            "scale_factor": 1.0,
            "account": "test_account",
            "username": "test_user",
            "password": "test_pass",
            "warehouse": "TEST_WH",
            "database": "CUSTOM_DATABASE",
        }

        adapter = SnowflakeAdapter.from_config(config)

        assert adapter.database == "CUSTOM_DATABASE"

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_from_config_with_scale_factor_formatting(self, mock_snowflake):
        config_01 = {
            "benchmark": "tpcds",
            "scale_factor": 0.1,
            "account": "test_account",
            "username": "test_user",
            "password": "test_pass",
        }
        adapter_01 = SnowflakeAdapter.from_config(config_01)
        assert adapter_01.database == "tpcds_sf01_notuning_noconstraints"

        config_10 = {
            "benchmark": "ssb",
            "scale_factor": 10.0,
            "account": "test_account",
            "username": "test_user",
            "password": "test_pass",
        }
        adapter_10 = SnowflakeAdapter.from_config(config_10)
        assert adapter_10.database == "ssb_sf10_notuning_noconstraints"

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_create_admin_connection(self, mock_snowflake):
        mock_connection = Mock()
        mock_snowflake.connector.connect.return_value = mock_connection

        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
        )

        admin_conn = adapter._create_admin_connection()

        assert admin_conn == mock_connection
        mock_snowflake.connector.connect.assert_called_once()
        call_kwargs = mock_snowflake.connector.connect.call_args[1]
        assert call_kwargs["user"] == "test_user"
        assert "username" not in call_kwargs

        snowflake_connection = pytest.importorskip("snowflake.connector.connection")
        DEFAULT_CONFIGURATION = snowflake_connection.DEFAULT_CONFIGURATION
        extra = {"client_session_keep_alive", "login_timeout", "network_timeout"}
        assert set(call_kwargs) - extra <= set(DEFAULT_CONFIGURATION)

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_create_admin_connection_with_key_pair(self, mock_snowflake, tmp_path):
        pytest.importorskip("cryptography.hazmat.primitives.serialization")
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        mock_connection = Mock()
        mock_snowflake.connector.connect.return_value = mock_connection

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        key_path = tmp_path / "key.p8"
        key_path.write_bytes(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )

        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
            private_key_path=str(key_path),
        )

        adapter._create_admin_connection()

        call_kwargs = mock_snowflake.connector.connect.call_args[1]
        assert "private_key" in call_kwargs
        assert "password" not in call_kwargs

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_handle_existing_database_reuses_compatible_database(self, mock_snowflake):
        from types import SimpleNamespace

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [["2026-10-12", "TPCH_SF1_X", "", "", ""]]
        mock_snowflake.connector.connect.return_value = mock_connection

        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
        )
        adapter._validate_database_compatibility = Mock(
            return_value=SimpleNamespace(is_valid=True, can_reuse=True, issues=[], warnings=[])
        )

        adapter.handle_existing_database(database="tpch_sf1_x")

        assert adapter.database_was_reused is True

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_check_server_database_exists_true(self, mock_snowflake):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [
            ["schema", "TEST_DB", "owner", "comment"],
            ["schema", "OTHER_DB", "owner", "comment"],
        ]
        mock_snowflake.connector.connect.return_value = mock_connection

        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
        )

        exists = adapter.check_server_database_exists()

        assert exists is True
        mock_cursor.execute.assert_called_once_with("SHOW DATABASES")
        mock_connection.close.assert_called_once()

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_check_server_database_exists_false(self, mock_snowflake):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        mock_cursor.fetchall.side_effect = [
            [["schema", "OTHER_DB", "owner", "comment"]],
            [],
            [],
        ]
        mock_snowflake.connector.connect.return_value = mock_connection

        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
        )

        exists = adapter.check_server_database_exists()

        assert exists is False
        mock_connection.close.assert_called_once()

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_check_server_database_exists_connection_error(self, mock_snowflake):
        mock_snowflake.connector.connect.side_effect = Exception("Connection failed")

        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
        )

        exists = adapter.check_server_database_exists()

        assert exists is False

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_drop_database(self, mock_snowflake):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_snowflake.connector.connect.return_value = mock_connection

        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
        )

        adapter.drop_database()

        mock_cursor.execute.assert_called_once_with("DROP DATABASE IF EXISTS TEST_DB")
        mock_connection.close.assert_called_once()

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_drop_database_connection_error_preserves_database_name(self, mock_snowflake):
        mock_snowflake.connector.connect.side_effect = RuntimeError("connection failed")
        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
        )

        with pytest.raises(RuntimeError, match="Failed to drop Snowflake database TEST_DB: connection failed"):
            adapter.drop_database()

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_create_connection_success(self, mock_snowflake):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_snowflake.connector.connect.return_value = mock_connection

        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
        )

        with patch.object(adapter, "handle_existing_database"):
            connection = adapter.create_connection()

        assert connection == mock_connection
        mock_snowflake.connector.connect.assert_called_once()

        mock_cursor.execute.assert_called_with("SELECT CURRENT_VERSION()")
        mock_cursor.fetchall.assert_called_once()
        mock_cursor.close.assert_called_once()

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_create_connection_with_key_pair_auth(self, mock_snowflake):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_snowflake.connector.connect.return_value = mock_connection

        with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False, encoding="utf-8") as f:
            f.write("""-----BEGIN TEST KEY-----
benchbox-fixture-key-material
-----END TEST KEY-----""")
            key_path = f.name

        try:
            adapter = SnowflakeAdapter(
                account="test_account",
                username="test_user",
                password="test_pass",
                warehouse="TEST_WH",
                database="TEST_DB",
                private_key_path=key_path,
            )

            mock_serialization = Mock()
            mock_load_key = Mock()
            mock_private_key = Mock()
            mock_load_key.return_value = mock_private_key
            mock_private_key.private_bytes.return_value = b"mock_key_bytes"

            with patch.dict(
                "sys.modules",
                {
                    "cryptography": Mock(),
                    "cryptography.hazmat": Mock(),
                    "cryptography.hazmat.primitives": Mock(),
                    "cryptography.hazmat.primitives.serialization": mock_serialization,
                },
            ):
                mock_serialization.load_pem_private_key = mock_load_key
                mock_serialization.Encoding = Mock()
                mock_serialization.PrivateFormat = Mock()
                mock_serialization.NoEncryption = Mock()

                with patch.object(adapter, "handle_existing_database"):
                    connection = adapter.create_connection()

            assert connection == mock_connection

            call_kwargs = mock_snowflake.connector.connect.call_args[1]
            assert "private_key" in call_kwargs
            assert "password" not in call_kwargs

        finally:
            Path(key_path).unlink()

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_create_connection_failure(self, mock_snowflake):
        mock_snowflake.connector.connect.side_effect = Exception("Connection failed")

        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
        )

        with patch.object(adapter, "handle_existing_database"):
            with pytest.raises(Exception, match="Connection failed"):
                adapter.create_connection()

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_create_schema(self, mock_snowflake):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_snowflake.connector.connect.return_value = mock_connection

        mock_benchmark = Mock()
        mock_benchmark.get_create_tables_sql.return_value = """
            CREATE TABLE table1 (id INTEGER, name VARCHAR(100));
            CREATE TABLE table2 (id INTEGER, data TEXT);
        """

        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
        )

        with patch.object(adapter, "translate_sql") as mock_translate:
            mock_translate.return_value = (
                "CREATE TABLE table1 (id INTEGER, name VARCHAR(100));\nCREATE TABLE table2 (id INTEGER, data TEXT);"
            )

            schema_time = adapter.create_schema(mock_benchmark, mock_connection)

        assert isinstance(schema_time, float)
        assert schema_time >= 0

        setup_calls = list(mock_cursor.execute.call_args_list)
        setup_sqls = [str(call) for call in setup_calls]

        assert any("CREATE DATABASE" in sql for sql in setup_sqls)
        assert any("CREATE SCHEMA" in sql for sql in setup_sqls)

        mock_cursor.close.assert_called_once()

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_load_data_with_file_upload(self, mock_snowflake):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        mock_cursor.fetchall.side_effect = [
            [(True, 100, 0, 0, "LOADED", None)],
            [(100,)],
        ]
        mock_cursor.fetchone.return_value = (100,)

        mock_benchmark = Mock()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".tbl", delete=False, encoding="utf-8") as f:
            f.write("1|test1|\n2|test2|\n")
            temp_path = Path(f.name)

        try:
            mock_benchmark.tables = {"test_table": str(temp_path)}

            adapter = SnowflakeAdapter(
                account="test_account",
                username="test_user",
                password="test_pass",
                warehouse="TEST_WH",
                database="TEST_DB",
            )

            table_stats, load_time, _ = adapter.load_data(mock_benchmark, mock_connection, Path("/tmp"))

            assert isinstance(table_stats, dict)
            assert isinstance(load_time, float)
            assert load_time >= 0
            assert "TEST_TABLE" in table_stats
            assert table_stats["TEST_TABLE"] == 100

            execute_calls = [str(call) for call in mock_cursor.execute.call_args_list]
            assert any("CREATE OR REPLACE FILE FORMAT" in call for call in execute_calls)
            assert any("PUT file://" in call for call in execute_calls)
            assert any("COPY INTO TEST_TABLE" in call for call in execute_calls)

        finally:
            temp_path.unlink()

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_load_data_returns_per_table_timings(self, mock_snowflake):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        mock_cursor.fetchall.side_effect = [
            [(True, 100, 0, 0, "LOADED", None)],
            [(100,)],
        ]
        mock_cursor.fetchone.return_value = (100,)

        mock_benchmark = Mock()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".tbl", delete=False, encoding="utf-8") as f:
            f.write("1|test1|\n2|test2|\n")
            temp_path = Path(f.name)

        try:
            mock_benchmark.tables = {"test_table": str(temp_path)}

            adapter = SnowflakeAdapter(
                account="test_account",
                username="test_user",
                password="test_pass",
                warehouse="TEST_WH",
                database="TEST_DB",
            )

            table_stats, _, per_table_timings = adapter.load_data(mock_benchmark, mock_connection, Path("/tmp"))

            assert set(per_table_timings) == set(table_stats)
            assert per_table_timings["TEST_TABLE"]["total_ms"] >= 0
        finally:
            temp_path.unlink()

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_load_data_skip_uses_uppercase_keys_with_zero_timings(self, mock_snowflake):
        mock_connection = Mock()
        mock_connection.cursor.return_value = Mock()

        mock_benchmark = Mock()
        mock_benchmark.tables = {"ghost_table": "/nonexistent/path/ghost.tbl"}

        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
        )

        table_stats, _, per_table_timings = adapter.load_data(mock_benchmark, mock_connection, Path("/tmp"))

        assert table_stats == {"GHOST_TABLE": 0}
        assert per_table_timings == {"GHOST_TABLE": {"total_ms": 0}}

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_validate_external_table_requirements_requires_staging_root(self, mock_snowflake):
        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
        )

        with pytest.raises(ValueError, match="requires --platform-option staging_root"):
            adapter.validate_external_table_requirements()

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_create_external_tables_generates_stage_and_external_table_sql(self, mock_snowflake):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (77,)

        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
            schema="PUBLIC",
            staging_root="s3://benchbox-stage-root",
        )

        ds = DataSource(source_type="test", tables={"lineitem": [Path("/tmp/lineitem.parquet")]})
        with patch.object(adapter, "_resolve_data_files", return_value=ds):
            table_stats, load_time, per_table_timings = adapter.create_external_tables(
                benchmark=Mock(),
                connection=mock_connection,
                data_dir=Path("/tmp"),
            )

        assert table_stats == {"LINEITEM": 77}
        assert isinstance(load_time, float)
        assert per_table_timings is None

        execute_calls = [str(call.args[0]) for call in mock_cursor.execute.call_args_list]
        assert any("CREATE STAGE IF NOT EXISTS PUBLIC.BENCHBOX_EXTERNAL_STAGE" in sql for sql in execute_calls)
        assert any("CREATE OR REPLACE EXTERNAL TABLE LINEITEM" in sql for sql in execute_calls)
        assert any("AUTO_REFRESH=FALSE" in sql for sql in execute_calls)
        assert any("FILE_FORMAT=(TYPE=PARQUET)" in sql for sql in execute_calls)
        assert any("ALTER EXTERNAL TABLE LINEITEM REFRESH" in sql for sql in execute_calls)
        assert any("SELECT COUNT(*) FROM LINEITEM" in sql for sql in execute_calls)

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_create_external_tables_generates_iceberg_sql(self, mock_snowflake):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (11,)

        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
            schema="PUBLIC",
            staging_root="s3://benchbox-stage-root/external-prefix",
            iceberg_external_volume="BENCHBOX_VOL",
        )

        ds = DataSource(source_type="test", tables={"lineitem": [Path("/tmp/lineitem")]})
        with (
            patch.object(adapter, "_resolve_data_files", return_value=ds),
            patch.object(adapter, "_detect_external_table_format", return_value="iceberg"),
        ):
            table_stats, _, _ = adapter.create_external_tables(
                benchmark=Mock(), connection=mock_connection, data_dir=Path("/tmp")
            )

        assert table_stats == {"LINEITEM": 11}
        execute_calls = [str(call.args[0]) for call in mock_cursor.execute.call_args_list]
        assert any("CREATE OR REPLACE ICEBERG TABLE LINEITEM" in sql for sql in execute_calls)
        assert any("EXTERNAL_VOLUME = 'BENCHBOX_VOL'" in sql for sql in execute_calls)
        assert any("CATALOG = 'SNOWFLAKE'" in sql for sql in execute_calls)
        assert any("BASE_LOCATION = 'external-prefix/test_db/lineitem'" in sql for sql in execute_calls)

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_create_external_tables_iceberg_requires_external_volume(self, mock_snowflake):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
            schema="PUBLIC",
            staging_root="s3://benchbox-stage-root/external-prefix",
        )

        ds = DataSource(source_type="test", tables={"lineitem": [Path("/tmp/lineitem")]})
        with (
            patch.object(adapter, "_resolve_data_files", return_value=ds),
            patch.object(adapter, "_detect_external_table_format", return_value="iceberg"),
            pytest.raises(ValueError, match="requires --platform-option iceberg_external_volume"),
        ):
            adapter.create_external_tables(benchmark=Mock(), connection=mock_connection, data_dir=Path("/tmp"))

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_create_external_tables_generates_delta_sql(self, mock_snowflake):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (12,)

        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
            schema="PUBLIC",
            staging_root="s3://benchbox-stage-root",
        )

        ds = DataSource(source_type="test", tables={"lineitem": [Path("/tmp/lineitem")]})
        with (
            patch.object(adapter, "_resolve_data_files", return_value=ds),
            patch.object(adapter, "_detect_external_table_format", return_value="delta"),
        ):
            table_stats, _, _ = adapter.create_external_tables(
                benchmark=Mock(), connection=mock_connection, data_dir=Path("/tmp")
            )

        assert table_stats == {"LINEITEM": 12}
        execute_calls = [str(call.args[0]) for call in mock_cursor.execute.call_args_list]
        assert any("TABLE_FORMAT=DELTA" in sql for sql in execute_calls)

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_create_external_tables_escapes_staging_root_quotes(self, mock_snowflake):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (10,)

        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
            schema="PUBLIC",
            staging_root="s3://bucket/it's-a-path",
        )

        ds = DataSource(source_type="test", tables={"orders": [Path("/tmp/orders.parquet")]})
        with patch.object(adapter, "_resolve_data_files", return_value=ds):
            adapter.create_external_tables(
                benchmark=Mock(),
                connection=mock_connection,
                data_dir=Path("/tmp"),
            )

        execute_calls = [str(call.args[0]) for call in mock_cursor.execute.call_args_list]
        stage_sql = [sql for sql in execute_calls if "CREATE STAGE" in sql]
        assert stage_sql, "Expected CREATE STAGE SQL"
        assert "it''s-a-path" in stage_sql[0]

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_detect_external_table_format(self, mock_snowflake):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            delta_dir = tmp_path / "delta_table"
            iceberg_dir = tmp_path / "iceberg_table"
            parquet_file = tmp_path / "orders.parquet"

            (delta_dir / "_delta_log").mkdir(parents=True)
            (iceberg_dir / "metadata").mkdir(parents=True)
            parquet_file.write_bytes(b"PAR1")

            assert SnowflakeAdapter._detect_external_table_format(delta_dir) == "delta"
            assert SnowflakeAdapter._detect_external_table_format(iceberg_dir) == "iceberg"
            assert SnowflakeAdapter._detect_external_table_format(parquet_file) == "parquet"

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_create_load_file_formats_uses_configured_compression(self, mock_snowflake):
        mock_cursor = Mock()
        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
            schema="PUBLIC",
            compression="GZIP",
        )

        adapter._create_load_file_formats(mock_cursor)

        execute_calls = [str(call.args[0]) for call in mock_cursor.execute.call_args_list]
        assert any("CREATE OR REPLACE FILE FORMAT PUBLIC.BENCHBOX_CSV_FORMAT" in sql for sql in execute_calls)
        assert any("CREATE OR REPLACE FILE FORMAT PUBLIC.BENCHBOX_TBL_FORMAT" in sql for sql in execute_calls)
        assert all("COMPRESSION = 'GZIP'" in sql for sql in execute_calls)
        assert any("FIELD_DELIMITER = '|'" in sql for sql in execute_calls)

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_get_file_format_for_table_uses_tbl_format_for_tpch_chunks(self, mock_snowflake):
        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
            schema="PUBLIC",
        )
        empty_ds = DataSource(source_type="test", tables={})

        assert (
            adapter._get_file_format_for_table("lineitem", Path("lineitem.tbl.1"), empty_ds, NO_BENCHMARK)
            == "PUBLIC.BENCHBOX_TBL_FORMAT"
        )
        assert (
            adapter._get_file_format_for_table("orders", Path("orders.csv"), empty_ds, NO_BENCHMARK)
            == "PUBLIC.BENCHBOX_CSV_FORMAT"
        )

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_get_file_format_for_table_manifest_csv_with_tpc_dialect_picks_tbl_format(self, mock_snowflake):
        from tests.unit.platforms.csv_dialect_test_helpers import resolver_data_source

        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
            schema="PUBLIC",
        )
        file_path = Path("lineitem.csv")
        ds = resolver_data_source("lineitem", file_path, {"csv_delimiter": "|", "csv_null_marker": ""})

        assert (
            adapter._get_file_format_for_table("lineitem", file_path, ds, NO_BENCHMARK) == "PUBLIC.BENCHBOX_TBL_FORMAT"
        )

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_get_file_format_for_table_manifest_csv_without_null_marker_picks_csv_format(self, mock_snowflake):
        from tests.unit.platforms.csv_dialect_test_helpers import resolver_data_source

        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
            schema="PUBLIC",
        )
        file_path = Path("orders.csv")
        ds = resolver_data_source("orders", file_path, {"csv_delimiter": ",", "csv_null_marker": None})

        assert adapter._get_file_format_for_table("orders", file_path, ds, NO_BENCHMARK) == "PUBLIC.BENCHBOX_CSV_FORMAT"

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_get_file_format_for_table_manifest_csv_with_empty_null_marker_picks_csv_format(self, mock_snowflake):
        from tests.unit.platforms.csv_dialect_test_helpers import resolver_data_source

        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
            schema="PUBLIC",
        )
        file_path = Path("trips.csv.gz")
        ds = resolver_data_source("trips", file_path, {"csv_delimiter": ",", "csv_null_marker": ""})

        assert adapter._get_file_format_for_table("trips", file_path, ds, NO_BENCHMARK) == "PUBLIC.BENCHBOX_CSV_FORMAT"

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_ensure_preserve_file_format_returns_none_without_sentinel(self, mock_snowflake):
        from tests.unit.platforms.csv_dialect_test_helpers import resolver_data_source

        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
            schema="PUBLIC",
        )
        mock_cursor = Mock()
        file_path = Path("lineitem.tbl.1")
        ds = resolver_data_source("lineitem", file_path, {"csv_delimiter": "|", "csv_null_marker": ""})

        assert adapter._ensure_preserve_file_format(mock_cursor, "lineitem", file_path, ds, NO_BENCHMARK) is None
        mock_cursor.execute.assert_not_called()

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_ensure_preserve_file_format_creates_per_dialect_format_for_sentinel(self, mock_snowflake):
        from tests.unit.platforms.csv_dialect_test_helpers import resolver_data_source

        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
            schema="PUBLIC",
        )
        mock_cursor = Mock()
        file_path = Path("hits.csv.gz")
        ds = resolver_data_source("hits", file_path, {"csv_delimiter": "|", "csv_null_marker": "__NULL__"})

        format_name = adapter._ensure_preserve_file_format(mock_cursor, "hits", file_path, ds, NO_BENCHMARK)

        assert format_name.startswith("PUBLIC.BENCHBOX_DYN_")
        create_sql = mock_cursor.execute.call_args_list[0].args[0]
        assert "CREATE FILE FORMAT IF NOT EXISTS" in create_sql
        assert "FIELD_DELIMITER = '|'" in create_sql
        assert "EMPTY_FIELD_AS_NULL = FALSE" in create_sql
        assert "NULL_IF = ('__NULL__')" in create_sql

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_ensure_preserve_file_format_creates_header_aware_format_without_sentinel(self, mock_snowflake):
        from tests.unit.platforms.csv_dialect_test_helpers import resolver_data_source

        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
            schema="PUBLIC",
        )
        mock_cursor = Mock()
        file_path = Path("trips.csv.gz")
        ds = resolver_data_source(
            "trips",
            file_path,
            {"csv_delimiter": ",", "csv_has_header": True, "csv_null_marker": ""},
        )

        format_name = adapter._ensure_preserve_file_format(mock_cursor, "trips", file_path, ds, NO_BENCHMARK)

        assert format_name.startswith("PUBLIC.BENCHBOX_DYN_")
        create_sql = mock_cursor.execute.call_args_list[0].args[0]
        assert "SKIP_HEADER = 1" in create_sql
        assert "EMPTY_FIELD_AS_NULL = TRUE" in create_sql
        assert "NULL_IF" not in create_sql

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_ensure_preserve_file_format_header_with_none_null_marker_skips(self, mock_snowflake):
        from tests.unit.platforms.csv_dialect_test_helpers import resolver_data_source

        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
            schema="PUBLIC",
        )
        mock_cursor = Mock()
        file_path = Path("devops.csv.gz")
        ds = resolver_data_source(
            "devops",
            file_path,
            {"csv_delimiter": ",", "csv_has_header": True, "csv_null_marker": None},
        )

        format_name = adapter._ensure_preserve_file_format(mock_cursor, "devops", file_path, ds, NO_BENCHMARK)

        assert format_name.startswith("PUBLIC.BENCHBOX_DYN_")
        create_sql = mock_cursor.execute.call_args_list[0].args[0]
        assert "SKIP_HEADER = 1" in create_sql
        assert "EMPTY_FIELD_AS_NULL = TRUE" in create_sql
        assert "NULL_IF" not in create_sql

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_parse_copy_results_logs_failed_and_unparseable_rows(self, mock_snowflake, caplog):
        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
        )

        caplog.set_level("WARNING")
        adapter._parse_copy_results(
            [
                ["orders.csv.gz", "FAILED", None, 3, None, "parse error"],
                ["lineitem.csv.gz", "FAILED", None, "not-an-int", None, "ignored"],
            ]
        )

        assert "orders.csv.gz status: FAILED, loaded 3 rows. Error: parse error" in caplog.text
        assert "Could not parse rows_loaded from COPY result" in caplog.text

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_load_table_from_stage_uploads_files_and_executes_copy_into(self, mock_snowflake):
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [["lineitem.tbl.1", "LOADED", None, 2, None, None]]
        mock_cursor.fetchone.return_value = (5,)

        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
            schema="PUBLIC",
        )

        with (
            tempfile.NamedTemporaryFile(mode="wb", suffix=".tbl.1", delete=False) as f1,
            tempfile.NamedTemporaryFile(mode="wb", suffix=".tbl.2", delete=False) as f2,
        ):
            f1.write(b"1|one|\n")
            f2.write(b"2|two|\n")
            path_one = Path(f1.name)
            path_two = Path(f2.name)

        try:
            row_count = adapter._load_table_from_stage(mock_cursor, "lineitem", "LINEITEM", [path_one, path_two])
        finally:
            path_one.unlink()
            path_two.unlink()

        assert row_count == 5
        execute_calls = [str(call.args[0]) for call in mock_cursor.execute.call_args_list]
        assert any(f"PUT file://{path_one.absolute()} @%LINEITEM" in sql for sql in execute_calls)
        assert any(f"PUT file://{path_two.absolute()} @%LINEITEM" in sql for sql in execute_calls)
        assert any("COPY INTO LINEITEM" in sql for sql in execute_calls)
        assert any("FORMAT_NAME = 'PUBLIC.BENCHBOX_TBL_FORMAT'" in sql for sql in execute_calls)
        assert any("PURGE = TRUE" in sql for sql in execute_calls)
        assert execute_calls[-1] == "SELECT COUNT(*) FROM LINEITEM"

    def test_load_table_from_stage_fallback_to_quoted_stage_when_not_authorized(self):
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [["lineitem.tbl.1", "LOADED", None, 2, None, None]]
        mock_cursor.fetchone.return_value = (10,)

        def mock_execute(sql):
            if "@%LINEITEM" in sql:
                raise Exception("Stage '@%LINEITEM' does not exist or not authorized")

        mock_cursor.execute.side_effect = mock_execute

        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
            schema="PUBLIC",
        )

        with tempfile.NamedTemporaryFile(mode="wb", suffix=".tbl", delete=False) as f:
            f.write(b"1|one|\n")
            path = Path(f.name)

        try:
            row_count = adapter._load_table_from_stage(mock_cursor, "lineitem", "LINEITEM", [path])
        finally:
            path.unlink()

        assert row_count == 10
        execute_calls = [str(call.args[0]) for call in mock_cursor.execute.call_args_list]
        assert any(f"PUT file://{path.absolute()} @%LINEITEM" in sql for sql in execute_calls)
        assert any(f'PUT file://{path.absolute()} @%"lineitem"' in sql for sql in execute_calls)
        assert any('COPY INTO "lineitem"' in sql for sql in execute_calls)

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_configure_for_benchmark_olap(self, mock_snowflake):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
            strict_validation=False,
        )

        adapter.configure_for_benchmark(mock_connection, "olap")

        execute_calls = [str(call) for call in mock_cursor.execute.call_args_list]
        assert any("QUERY_ACCELERATION_MAX_SCALE_FACTOR" in call for call in execute_calls)
        assert any("USE_CACHED_RESULT" in call for call in execute_calls)
        assert any("STATEMENT_TIMEOUT_IN_SECONDS" in call for call in execute_calls)
        assert any("USE WAREHOUSE" in call for call in execute_calls)

        assert mock_cursor.close.call_count == 2

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_configure_for_benchmark_with_multi_cluster(self, mock_snowflake):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
            multi_cluster_warehouse=True,
            modify_warehouse_settings=True,
            strict_validation=False,
        )

        adapter.configure_for_benchmark(mock_connection, "tpch")

        execute_calls = [str(call) for call in mock_cursor.execute.call_args_list]
        assert any("MIN_CLUSTER_COUNT" in call for call in execute_calls)
        assert any("MAX_CLUSTER_COUNT" in call for call in execute_calls)
        assert any("SCALING_POLICY" in call for call in execute_calls)

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_configure_for_benchmark_with_single_cluster_warehouse_settings(self, mock_snowflake):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
            warehouse_size="LARGE",
            auto_suspend=120,
            auto_resume=False,
            modify_warehouse_settings=True,
            strict_validation=False,
        )

        adapter.configure_for_benchmark(mock_connection, "tpch")

        execute_calls = [str(call.args[0]) for call in mock_cursor.execute.call_args_list]
        warehouse_sql = [sql for sql in execute_calls if "ALTER WAREHOUSE TEST_WH SET" in sql]
        assert warehouse_sql, "Expected single-cluster warehouse ALTER statement"
        assert "WAREHOUSE_SIZE = 'LARGE'" in warehouse_sql[0]
        assert "AUTO_SUSPEND = 120" in warehouse_sql[0]
        assert "AUTO_RESUME = False" in warehouse_sql[0]

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_execute_query_success(self, mock_snowflake):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [(1, "test"), (2, "test2")]

        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
        )

        with patch.object(adapter, "_get_query_statistics") as mock_stats:
            mock_stats.return_value = {"snowflake_query_id": "test_query_id"}

            result = adapter.execute_query(mock_connection, "SELECT * FROM test", "q1")

        assert result["query_id"] == "q1"
        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 2
        assert result["first_row"] == (1, "test")
        assert isinstance(result["execution_time_seconds"], float)
        assert result["query_statistics"] == {"snowflake_query_id": "test_query_id"}
        assert result["resource_usage"]["execution_time_seconds"] == result["execution_time_seconds"]

        mock_cursor.execute.assert_any_call("ALTER SESSION SET QUERY_TAG = 'BenchBox_q1'")
        mock_cursor.execute.assert_any_call("SELECT * FROM test")
        mock_cursor.close.assert_called_once()

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_execute_query_success_under_verbose_logging(self, mock_snowflake):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [(1,)]

        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
            verbose_level=1,
        )

        with patch.object(adapter, "_get_query_statistics") as mock_stats:
            mock_stats.return_value = {}
            result = adapter.execute_query(mock_connection, "SELECT 1", "q1")

        assert result["query_id"] == "q1"
        assert result["status"] == "SUCCESS"

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_execute_query_accepts_stream_cursor(self, mock_snowflake):
        mock_cursor = Mock(spec=["execute", "fetchall", "fetchone", "close"])
        mock_cursor.fetchall.return_value = [(1,)]

        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
        )

        with patch.object(adapter, "_get_query_statistics") as mock_stats:
            mock_stats.return_value = {}
            result = adapter.execute_query(mock_cursor, "SELECT 1", "q1")

        assert result["status"] == "SUCCESS"
        mock_cursor.execute.assert_any_call("SELECT 1")
        mock_cursor.close.assert_not_called()

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_execute_query_failure(self, mock_snowflake):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = [
            None,
            Exception("Query failed"),
        ]

        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
        )

        result = adapter.execute_query(mock_connection, "INVALID SQL", "q1")

        assert result["query_id"] == "q1"
        assert result["status"] == "FAILED"
        assert result["rows_returned"] == 0
        assert result["error"] == "Query failed"
        assert result["error_type"] == "Exception"
        assert isinstance(result["execution_time_seconds"], float)

        mock_cursor.close.assert_called_once()

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_execute_query_splits_multi_statement_batch(self, mock_snowflake):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchall.side_effect = [[], [], [(25,)]]

        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
        )

        batch = (
            "DELETE FROM t WHERE k BETWEEN 1 AND 10; "
            "INSERT INTO t SELECT * FROM s WHERE k BETWEEN 1 AND 10; "
            "SELECT COUNT(*) FROM t WHERE k BETWEEN 1 AND 10"
        )
        with patch.object(adapter, "_get_query_statistics") as mock_stats:
            mock_stats.return_value = {}
            result = adapter.execute_query(mock_connection, batch, "q_multi")

        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 1
        executed = [call.args[0] for call in mock_cursor.execute.call_args_list]
        assert executed[0] == "ALTER SESSION SET QUERY_TAG = 'BenchBox_q_multi'"
        assert executed[1:] == [
            "BEGIN",
            "DELETE FROM t WHERE k BETWEEN 1 AND 10",
            "INSERT INTO t SELECT * FROM s WHERE k BETWEEN 1 AND 10",
            "SELECT COUNT(*) FROM t WHERE k BETWEEN 1 AND 10",
            "COMMIT",
        ]

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_multi_statement_dml_failure_rolls_back_and_leaves_data_unchanged(self, mock_snowflake):
        table_data = {"rows": [{"id": 1, "is_current": True}]}

        class FakeCursor:
            def __init__(self):
                self.in_transaction = False
                self.uncommitted_data: list[dict] | None = None
                self.executed_statements = []

            def execute(self, stmt):
                self.executed_statements.append(stmt)
                if stmt == "BEGIN":
                    self.in_transaction = True
                    self.uncommitted_data = [dict(r) for r in table_data["rows"]]
                elif stmt == "COMMIT":
                    if self.uncommitted_data is not None:
                        table_data["rows"] = self.uncommitted_data
                    self.in_transaction = False
                elif stmt == "ROLLBACK":
                    self.uncommitted_data = None
                    self.in_transaction = False
                elif stmt.startswith("UPDATE"):
                    assert self.in_transaction
                    assert self.uncommitted_data is not None
                    for r in self.uncommitted_data:
                        r["is_current"] = False
                elif stmt.startswith("INSERT"):
                    raise RuntimeError("Syntax error or constraint failure on INSERT")

            def fetchall(self):
                return []

            def close(self):
                pass

        fake_cursor = FakeCursor()
        mock_connection = Mock()
        mock_connection.cursor.return_value = fake_cursor

        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
        )

        scd2_batch = (
            "UPDATE dim_customer SET is_current = FALSE WHERE id = 1;\n"
            "INSERT INTO dim_customer (id, is_current) VALUES (2, TRUE);\n"
        )
        with patch.object(adapter, "_get_query_statistics", return_value={}):
            result = adapter.execute_query(mock_connection, scd2_batch, "scd2_fail")

        assert result["status"] == "FAILED"
        assert "Syntax error or constraint failure on INSERT" in result["error"]

        assert fake_cursor.executed_statements[1:] == [
            "BEGIN",
            "UPDATE dim_customer SET is_current = FALSE WHERE id = 1",
            "INSERT INTO dim_customer (id, is_current) VALUES (2, TRUE)",
            "ROLLBACK",
        ]
        assert table_data["rows"] == [{"id": 1, "is_current": True}]

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_multi_statement_ddl_skips_explicit_transaction(self, mock_snowflake):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = []

        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
        )

        ddl_batch = "CREATE TABLE t1 (id INT); CREATE TABLE t2 (id INT);"
        with patch.object(adapter, "_get_query_statistics", return_value={}):
            result = adapter.execute_query(mock_connection, ddl_batch, "q_ddl")

        assert result["status"] == "SUCCESS"
        executed = [call.args[0] for call in mock_cursor.execute.call_args_list]
        assert "BEGIN" not in executed
        assert "COMMIT" not in executed
        assert executed[1:] == ["CREATE TABLE t1 (id INT)", "CREATE TABLE t2 (id INT)"]

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_execute_query_single_statement_executes_once(self, mock_snowflake):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [(1,)]

        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
        )

        with patch.object(adapter, "_get_query_statistics") as mock_stats:
            mock_stats.return_value = {}
            result = adapter.execute_query(mock_connection, "SELECT 1", "q_single")

        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 1
        statements = [call.args[0] for call in mock_cursor.execute.call_args_list]
        assert statements == ["ALTER SESSION SET QUERY_TAG = 'BenchBox_q_single'", "SELECT 1"]

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_get_query_statistics(self, mock_snowflake):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = [
            "query_id_123",
            1000,
            800,
            200,
            1024000,
            10,
            5.5,
            "MEDIUM",
            1,
        ]

        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
        )

        stats = adapter._get_query_statistics(mock_connection, "q1")

        assert stats["snowflake_query_id"] == "query_id_123"
        assert stats["total_elapsed_time_ms"] == 1000
        assert stats["execution_time_ms"] == 800
        assert stats["compilation_time_ms"] == 200
        assert stats["bytes_scanned"] == 1024000
        assert stats["rows_produced"] == 10
        assert stats["warehouse_size"] == "MEDIUM"
        assert stats["credits_used_cloud_services"] == 5.5
        assert "credits_used" not in stats

        mock_cursor.close.assert_called_once()

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_edition_flows_to_platform_info_for_cost_model(self, mock_snowflake):
        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
            edition="enterprise",
        )
        assert adapter.edition == "enterprise"
        assert adapter.get_platform_info(None)["configuration"]["edition"] == "enterprise"

        defaulted = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
        )
        assert defaulted.edition is None
        assert defaulted.get_platform_info(None)["configuration"]["edition"] is None

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_get_platform_metadata(self, mock_snowflake):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        mock_cursor.fetchone.side_effect = [
            ["6.21.0"],
            [
                "test_user",
                "test_role",
                "TEST_WH",
                "TEST_DB",
                "PUBLIC",
                "us-east-1",
                "test_account",
            ],
        ]

        from datetime import datetime

        mock_datetime = datetime(2024, 1, 1, 12, 0, 0)

        mock_cursor.fetchall.side_effect = [
            [
                [
                    "TEST_WH",
                    "STARTED",
                    "STANDARD",
                    "MEDIUM",
                    1,
                    1,
                    1,
                    1,
                    0,
                    0,
                    0,
                    0,
                    300,
                    True,
                    True,
                    False,
                    False,
                    False,
                    "2024-01-01",
                    "2024-01-01",
                    "2024-01-01",
                    "test_user",
                    "Test warehouse",
                    None,
                    None,
                    "STANDARD",
                ]
            ],
            [["TABLE1", 1000, 1024000, 7, mock_datetime, mock_datetime, None]],
        ]

        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
        )

        metadata = adapter._get_platform_metadata(mock_connection)

        assert metadata["platform"] == "Snowflake"
        assert metadata["account"] == "test_account"
        assert metadata["warehouse"] == "TEST_WH"
        assert metadata["snowflake_version"] == "6.21.0"
        assert "session_info" in metadata
        assert "warehouse_info" in metadata
        assert "tables" in metadata

        mock_cursor.close.assert_called()

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_analyze_table(self, mock_snowflake):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
        )

        adapter.analyze_table(mock_connection, "test_table")

        mock_cursor.execute.assert_called_once_with("ALTER TABLE TEST_TABLE RECLUSTER")
        mock_cursor.close.assert_called_once()

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_close_connection(self, mock_snowflake):
        mock_connection = Mock()

        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
        )

        adapter.close_connection(mock_connection)

        mock_connection.close.assert_called_once()

    def test_supports_tuning_type(self):
        with patch("benchbox.platforms.snowflake.snowflake"):
            adapter = SnowflakeAdapter(
                account="test_account",
                username="test_user",
                password="test_pass",
                warehouse="TEST_WH",
                database="TEST_DB",
            )

            with patch("benchbox.core.tuning.interface.TuningType") as mock_tuning_type:
                mock_tuning_type.CLUSTERING = "clustering"
                mock_tuning_type.PARTITIONING = "partitioning"
                mock_tuning_type.SORTING = "sorting"

                assert adapter.supports_tuning_type(mock_tuning_type.CLUSTERING) is True
                assert adapter.supports_tuning_type(mock_tuning_type.PARTITIONING) is True
                assert adapter.supports_tuning_type(mock_tuning_type.SORTING) is False

    def test_generate_tuning_clause_with_clustering(self):
        with patch("benchbox.platforms.snowflake.snowflake"):
            adapter = SnowflakeAdapter(
                account="test_account",
                username="test_user",
                password="test_pass",
                warehouse="TEST_WH",
                database="TEST_DB",
            )

            mock_tuning = Mock()
            mock_tuning.has_any_tuning.return_value = True

            mock_column = Mock()
            mock_column.name = "cluster_key"
            mock_column.order = 1

            with patch("benchbox.core.tuning.interface.TuningType") as mock_tuning_type:
                mock_tuning_type.CLUSTERING = "clustering"
                mock_tuning_type.PARTITIONING = "partitioning"

                def mock_get_columns_by_type(tuning_type):
                    if tuning_type == mock_tuning_type.CLUSTERING:
                        return [mock_column]
                    return []

                mock_tuning.get_columns_by_type.side_effect = mock_get_columns_by_type

                clause = adapter.generate_tuning_clause(mock_tuning)

                assert "CLUSTER BY (cluster_key)" in clause

    def test_generate_tuning_clause_none(self):
        with patch("benchbox.platforms.snowflake.snowflake"):
            adapter = SnowflakeAdapter(
                account="test_account",
                username="test_user",
                password="test_pass",
                warehouse="TEST_WH",
                database="TEST_DB",
            )

            clause = adapter.generate_tuning_clause(None)
            assert clause == ""

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_apply_table_tunings_with_clustering(self, mock_snowflake):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        mock_cursor.fetchone.return_value = [None]

        adapter = SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            warehouse="TEST_WH",
            database="TEST_DB",
        )

        mock_tuning = Mock()
        mock_tuning.table_name = "test_table"
        mock_tuning.has_any_tuning.return_value = True

        mock_column = Mock()
        mock_column.name = "cluster_key"
        mock_column.order = 1

        with patch("benchbox.core.tuning.interface.TuningType") as mock_tuning_type:
            mock_tuning_type.CLUSTERING = "clustering"
            mock_tuning_type.PARTITIONING = "partitioning"
            mock_tuning_type.SORTING = "sorting"
            mock_tuning_type.DISTRIBUTION = "distribution"

            def mock_get_columns_by_type(tuning_type):
                if tuning_type == mock_tuning_type.CLUSTERING:
                    return [mock_column]
                return []

            mock_tuning.get_columns_by_type.side_effect = mock_get_columns_by_type

            adapter.apply_table_tunings(mock_tuning, mock_connection)

            execute_calls = [str(call) for call in mock_cursor.execute.call_args_list]
            assert any("ALTER TABLE TEST_TABLE CLUSTER BY" in call for call in execute_calls)
            assert not any("RESUME RECLUSTER" in call for call in execute_calls)

            mock_cursor.execute.reset_mock()
            adapter.apply_post_load_tunings("TEST_TABLE", None, mock_connection)

            post_load_calls = [str(call) for call in mock_cursor.execute.call_args_list]
            assert any("ALTER TABLE TEST_TABLE RESUME RECLUSTER" in call for call in post_load_calls)

        mock_cursor.close.assert_called()

    def test_apply_unified_tuning(self):
        with patch("benchbox.platforms.snowflake.snowflake"):
            adapter = SnowflakeAdapter(
                account="test_account",
                username="test_user",
                password="test_pass",
                warehouse="TEST_WH",
                database="TEST_DB",
            )

            mock_connection = Mock()
            mock_unified_config = Mock()
            mock_unified_config.primary_keys = Mock()
            mock_unified_config.foreign_keys = Mock()
            mock_unified_config.platform_optimizations = Mock()
            mock_unified_config.table_tunings = {}

            with patch.object(adapter, "apply_constraint_configuration"):
                with patch.object(adapter, "apply_platform_optimizations"):
                    adapter.apply_unified_tuning(mock_unified_config, mock_connection)

    def test_file_format_detection_for_chunked_files(self):
        with patch("benchbox.platforms.snowflake.snowflake"):
            from pathlib import Path

            SnowflakeAdapter(
                account="test_account",
                username="test_user",
                password="test_pass",
                warehouse="TEST_WH",
                database="TEST_DB",
            )

            test_cases = [
                (Path("customer.tbl"), True, "Simple .tbl file"),
                (Path("customer.tbl.1"), True, "Chunked .tbl file"),
                (Path("lineitem.tbl.10"), True, "Chunked .tbl file (2 digits)"),
                (Path("orders.tbl.gz"), True, "Compressed .tbl file"),
                (Path("part.tbl.1.gz"), True, "Chunked compressed .tbl file"),
                (Path("nation.tbl.zst"), True, "zstd compressed .tbl file"),
                (Path("region.csv"), False, "CSV file"),
                (Path("supplier.csv.gz"), False, "Compressed CSV file"),
            ]

            for file_path, should_be_tbl, description in test_cases:
                file_str = str(file_path.name)
                is_tbl = ".tbl" in file_str
                assert is_tbl == should_be_tbl, f"Failed for {description}: {file_path.name}"

    def test_apply_constraint_configuration(self):
        with patch("benchbox.platforms.snowflake.snowflake"):
            adapter = SnowflakeAdapter(
                account="test_account",
                username="test_user",
                password="test_pass",
                warehouse="TEST_WH",
                database="TEST_DB",
            )

            mock_connection = Mock()
            mock_primary_key_config = Mock()
            mock_primary_key_config.enabled = True
            mock_foreign_key_config = Mock()
            mock_foreign_key_config.enabled = True

            adapter.apply_constraint_configuration(mock_primary_key_config, mock_foreign_key_config, mock_connection)


class TestSnowflakeSchemaDdl:
    @staticmethod
    def _executed(ddl):
        with patch("benchbox.platforms.snowflake.snowflake"):
            adapter = SnowflakeAdapter(account="a", username="u", password="p", warehouse="WH", database="DB")
        cursor = Mock()
        connection = Mock()
        connection.cursor.return_value = cursor
        with (
            patch.object(adapter, "_should_skip_schema_creation", return_value=False),
            patch.object(adapter, "_create_schema_with_tuning", return_value=ddl),
        ):
            adapter.create_schema(Mock(), connection)
        return [str(call.args[0]) for call in cursor.execute.call_args_list][-1]

    def test_create_table_becomes_create_or_replace(self):
        assert self._executed("CREATE TABLE orders (id INT)") == "CREATE OR REPLACE TABLE orders (id INT)"

    def test_already_has_or_replace_is_unchanged(self):
        stmt = "CREATE OR REPLACE TABLE orders (id INT)"
        assert self._executed(stmt) == stmt

    def test_if_not_exists_is_kept_without_or_replace(self):
        stmt = "CREATE TABLE IF NOT EXISTS DimDate (SK_DateID BIGINT)"
        assert self._executed(stmt) == stmt

    def test_non_create_table_statement_is_passed_through(self):
        stmt = "CREATE INDEX idx ON ORDERS(o_orderkey)"
        assert self._executed(stmt) == stmt

    def test_comment_prefixed_create_still_gets_or_replace(self):
        result = self._executed("-- Generated staging load tables\nCREATE TABLE orders_stage (id INT)")
        assert "CREATE OR REPLACE TABLE orders_stage (id INT)" in result

    def test_lowercase_create_table_gets_or_replace(self):
        assert self._executed("create table orders (id INT)") == "CREATE OR REPLACE TABLE orders (id INT)"

    def test_quoted_identifiers_keep_their_case(self):
        result = self._executed(
            'CREATE TABLE "hits" ("WatchID" BIGINT NOT NULL, "note" VARCHAR DEFAULT \'keep "me" lower\')'
        )
        assert result == (
            'CREATE OR REPLACE TABLE "hits" ("WatchID" BIGINT NOT NULL, "note" VARCHAR DEFAULT \'keep "me" lower\')'
        )


class TestSnowflakeBuildCtasSortSql:
    @patch("benchbox.platforms.snowflake.snowflake")
    def test_ctas_sort_generates_correct_sql(self, mock_snowflake):
        adapter = SnowflakeAdapter(account="a", username="u", password="p", warehouse="WH", database="DB")

        mock_col1 = Mock()
        mock_col1.name = "l_shipdate"
        mock_col2 = Mock()
        mock_col2.name = "l_orderkey"

        with patch.object(adapter, "resolve_sorted_ingestion_strategy", return_value=("on", "ctas")):
            sql = adapter._build_ctas_sort_sql("LINEITEM", [mock_col1, mock_col2])

        assert sql == "CREATE OR REPLACE TABLE LINEITEM AS SELECT * FROM LINEITEM ORDER BY l_shipdate, l_orderkey"

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_ctas_sort_returns_none_when_off(self, mock_snowflake):
        adapter = SnowflakeAdapter(account="a", username="u", password="p", warehouse="WH", database="DB")

        with patch.object(adapter, "resolve_sorted_ingestion_strategy", return_value=("off", "auto")):
            result = adapter._build_ctas_sort_sql("LINEITEM", [Mock(name="col1")])

        assert result is None

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_ctas_sort_rejects_non_ctas_method(self, mock_snowflake):
        adapter = SnowflakeAdapter(account="a", username="u", password="p", warehouse="WH", database="DB")

        with patch.object(adapter, "resolve_sorted_ingestion_strategy", return_value=("on", "presort")):
            with pytest.raises(ValueError, match="not supported for Snowflake"):
                adapter._build_ctas_sort_sql("LINEITEM", [Mock(name="col1")])


class TestSnowflakeShouldSkipSchemaCreation:
    @patch("benchbox.platforms.snowflake.snowflake")
    def test_skips_when_all_tables_exist_with_data(self, mock_snowflake):
        adapter = SnowflakeAdapter(account="a", username="u", password="p", warehouse="WH", database="DB")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        mock_cursor.fetchone.side_effect = [
            ("LINEITEM",),
            (1000,),
            ("ORDERS",),
            (500,),
        ]

        with patch.object(adapter, "_get_expected_tables", return_value=["LINEITEM", "ORDERS"]):
            result = adapter._should_skip_schema_creation(Mock(), mock_connection)

        assert result is True

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_does_not_skip_when_table_is_missing(self, mock_snowflake):
        adapter = SnowflakeAdapter(account="a", username="u", password="p", warehouse="WH", database="DB")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        mock_cursor.fetchone.return_value = None

        with patch.object(adapter, "_get_expected_tables", return_value=["LINEITEM"]):
            result = adapter._should_skip_schema_creation(Mock(), mock_connection)

        assert result is False

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_does_not_skip_when_table_is_empty(self, mock_snowflake):
        adapter = SnowflakeAdapter(account="a", username="u", password="p", warehouse="WH", database="DB")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        mock_cursor.fetchone.side_effect = [
            ("LINEITEM",),
            (0,),
        ]

        with patch.object(adapter, "_get_expected_tables", return_value=["LINEITEM"]):
            result = adapter._should_skip_schema_creation(Mock(), mock_connection)

        assert result is False

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_does_not_skip_when_no_expected_tables(self, mock_snowflake):
        adapter = SnowflakeAdapter(account="a", username="u", password="p", warehouse="WH", database="DB")

        mock_connection = Mock()
        with patch.object(adapter, "_get_expected_tables", return_value=[]):
            result = adapter._should_skip_schema_creation(Mock(), mock_connection)

        assert result is False

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_returns_false_on_exception(self, mock_snowflake):
        adapter = SnowflakeAdapter(account="a", username="u", password="p", warehouse="WH", database="DB")

        mock_connection = Mock()
        mock_connection.cursor.side_effect = Exception("DB unavailable")

        with patch.object(adapter, "_get_expected_tables", return_value=["LINEITEM"]):
            result = adapter._should_skip_schema_creation(Mock(), mock_connection)

        assert result is False


class TestSnowflakeGetExistingTables:
    @patch("benchbox.platforms.snowflake.snowflake")
    def test_returns_lowercase_table_names(self, mock_snowflake):
        adapter = SnowflakeAdapter(account="a", username="u", password="p", warehouse="WH", database="DB")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [
            ("2024-01-01", "LINEITEM", "DB", "PUBLIC", "TABLE"),
            ("2024-01-01", "ORDERS", "DB", "PUBLIC", "TABLE"),
            ("2024-01-01", "CUSTOMER", "DB", "PUBLIC", "TABLE"),
        ]

        result = adapter._get_existing_tables(mock_connection)

        assert result == ["lineitem", "orders", "customer"]
        mock_cursor.execute.assert_called_once_with("SHOW TABLES")

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_returns_empty_list_on_failure(self, mock_snowflake):
        adapter = SnowflakeAdapter(account="a", username="u", password="p", warehouse="WH", database="DB")

        mock_connection = Mock()
        mock_connection.cursor.side_effect = Exception("Permission denied")

        result = adapter._get_existing_tables(mock_connection)

        assert result == []


class TestSnowflakeValidateDataIntegrity:
    @patch("benchbox.platforms.snowflake.snowflake")
    def test_passed_when_all_tables_accessible(self, mock_snowflake):
        adapter = SnowflakeAdapter(account="a", username="u", password="p", warehouse="WH", database="DB")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [
            ("2024-01-01", "LINEITEM", "DB", "PUBLIC"),
            ("2024-01-01", "ORDERS", "DB", "PUBLIC"),
        ]
        mock_cursor.fetchone.return_value = (1,)

        status, details = adapter._validate_data_integrity(Mock(), mock_connection, {"LINEITEM": 1000, "ORDERS": 500})

        assert status == "PASSED"
        assert "LINEITEM" in details["accessible_tables"]
        assert "ORDERS" in details["accessible_tables"]
        assert details["constraints_enabled"] is True

        execute_calls = [str(call.args[0]) for call in mock_cursor.execute.call_args_list]
        assert 'SELECT 1 FROM "lineitem" LIMIT 1' in execute_calls
        assert 'SELECT 1 FROM "orders" LIMIT 1' in execute_calls

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_quoted_lowercase_tables_accessible(self, mock_snowflake):
        adapter = SnowflakeAdapter(account="a", username="u", password="p", warehouse="WH", database="DB")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [("2024-01-01", "call_center", "DB", "PUBLIC")]
        mock_cursor.fetchone.return_value = (1,)

        status, details = adapter._validate_data_integrity(Mock(), mock_connection, {"CALL_CENTER": 100})

        assert status == "PASSED"
        assert "CALL_CENTER" in details["accessible_tables"]
        execute_calls = [str(call.args[0]) for call in mock_cursor.execute.call_args_list]
        assert 'SELECT 1 FROM "call_center" LIMIT 1' in execute_calls

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_failed_when_table_inaccessible(self, mock_snowflake):
        adapter = SnowflakeAdapter(account="a", username="u", password="p", warehouse="WH", database="DB")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = []
        mock_cursor.execute.side_effect = Exception("Table not found")
        mock_cursor.fetchone.return_value = (1,)

        status, details = adapter._validate_data_integrity(Mock(), mock_connection, {"LINEITEM": 1000, "ORDERS": 500})

        assert status == "FAILED"
        assert "inaccessible_tables" in details
        assert details["constraints_enabled"] is False


class TestSnowflakeValidateSessionCacheControl:
    @patch("benchbox.platforms.snowflake.snowflake")
    def test_calls_shared_validate_with_correct_params(self, mock_snowflake):
        adapter = SnowflakeAdapter(
            account="a",
            username="u",
            password="p",
            warehouse="WH",
            database="DB",
            disable_result_cache=True,
            strict_validation=False,
        )

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ("FALSE",)

        with patch("benchbox.platforms.cloud_shared.validate_session_cache_control") as mock_validate:
            mock_validate.return_value = {"validated": True, "cache_disabled": True}
            result = adapter.validate_session_cache_control(mock_connection)

        assert result["validated"] is True
        assert result["cache_disabled"] is True

        call_kwargs = mock_validate.call_args[1]
        assert call_kwargs["query"] == "SHOW PARAMETERS LIKE 'USE_CACHED_RESULT' IN SESSION"
        assert call_kwargs["setting_key"] == "USE_CACHED_RESULT"
        assert call_kwargs["disabled_value"] == "FALSE"
        assert call_kwargs["enabled_value"] == "TRUE"
        assert call_kwargs["normalize"] == "upper"
        assert call_kwargs["platform_name"] == "Snowflake"
        assert call_kwargs["disable_result_cache"] is True
        assert call_kwargs["strict_validation"] is False
        assert call_kwargs["value_column_index"] == 1


class TestSnowflakeGetPlatformInfo:
    @patch("benchbox.platforms.snowflake.snowflake")
    def test_platform_info_without_connection(self, mock_snowflake):
        adapter = SnowflakeAdapter(
            account="test_account",
            username="u",
            password="p",
            warehouse="TEST_WH",
            database="TEST_DB",
            schema="PUBLIC",
            role="SYSADMIN",
        )

        result = adapter.get_platform_info(connection=None)

        assert result["platform_type"] == "snowflake"
        assert result["platform_name"] == "Snowflake"
        assert result["connection_mode"] == "remote"
        assert result["configuration"]["account"] == "test_account"
        assert result["configuration"]["warehouse"] == "TEST_WH"
        assert result["configuration"]["database"] == "TEST_DB"
        assert result["configuration"]["schema"] == "PUBLIC"
        assert result["configuration"]["role"] == "SYSADMIN"
        assert result["configuration"]["result_cache_enabled"] is False
        assert result["platform_version"] is None

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_platform_info_with_connection_queries_version(self, mock_snowflake):
        adapter = SnowflakeAdapter(
            account="a",
            username="u",
            password="p",
            warehouse="WH",
            database="DB",
        )

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        mock_cursor.execute.return_value = mock_cursor
        mock_cursor.fetchone.side_effect = [
            ("8.12.3",),
            ("AWS_US_EAST_1",),
            Exception("skip"),
        ]

        call_count = [0]
        executed_statements = []

        def side_effect_execute(sql):
            call_count[0] += 1
            executed_statements.append(sql)
            if "SHOW WAREHOUSES" in sql:
                raise Exception("skip warehouses")
            if "current_account_name" in sql:
                raise Exception("skip account")
            return mock_cursor

        mock_cursor.execute = Mock(side_effect=side_effect_execute)

        result = adapter.get_platform_info(connection=mock_connection)

        assert result["platform_version"] == "8.12.3"
        assert result["engine_version"] == "8.12.3"
        assert result["engine_version_source"] == "sql_query"
        assert result["cloud_region"] == "AWS_US_EAST_1"
        assert result["cloud_provider"] == "AWS"
        assert executed_statements, "expected the adapter to issue SQL queries"
        assert all("current_cloud" not in sql.lower() for sql in executed_statements)


class TestSnowflakeConfigValidation:
    @patch("benchbox.platforms.snowflake.snowflake")
    def test_missing_account_error_message(self, mock_snowflake):
        from benchbox.core.exceptions import ConfigurationError

        with pytest.raises(ConfigurationError, match="account"):
            SnowflakeAdapter(
                username="u",
                password="p",
                warehouse="WH",
                database="DB",
            )

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_missing_username_error_message(self, mock_snowflake):
        from benchbox.core.exceptions import ConfigurationError

        with pytest.raises(ConfigurationError, match="username"):
            SnowflakeAdapter(
                account="a",
                password="p",
                warehouse="WH",
                database="DB",
            )

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_missing_password_error_message(self, mock_snowflake):
        from benchbox.core.exceptions import ConfigurationError

        with pytest.raises(ConfigurationError, match="password"):
            SnowflakeAdapter(
                account="a",
                username="u",
                warehouse="WH",
                database="DB",
            )

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_missing_multiple_fields_error_message(self, mock_snowflake):
        from benchbox.core.exceptions import ConfigurationError

        with pytest.raises(ConfigurationError) as exc_info:
            SnowflakeAdapter()

        error_msg = str(exc_info.value)
        assert "account" in error_msg
        assert "username" in error_msg
        assert "password" in error_msg

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_authenticator_default(self, mock_snowflake):
        adapter = SnowflakeAdapter(account="a", username="u", password="p", warehouse="WH", database="DB")
        assert adapter.authenticator == "snowflake"

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_authenticator_override(self, mock_snowflake):
        adapter = SnowflakeAdapter(
            account="a",
            username="u",
            password="p",
            warehouse="WH",
            database="DB",
            authenticator="oauth",
        )
        assert adapter.authenticator == "oauth"

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_private_key_path_is_stored(self, mock_snowflake):
        adapter = SnowflakeAdapter(
            account="a",
            username="u",
            password="p",
            warehouse="WH",
            database="DB",
            private_key_path="/path/to/key.pem",
            private_key_passphrase="secret",
        )
        assert adapter.private_key_path == "/path/to/key.pem"
        assert adapter.private_key_passphrase == "secret"

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_disable_result_cache_default_true(self, mock_snowflake):
        adapter = SnowflakeAdapter(account="a", username="u", password="p", warehouse="WH", database="DB")
        assert adapter.disable_result_cache is True

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_disable_result_cache_explicit_false(self, mock_snowflake):
        adapter = SnowflakeAdapter(
            account="a",
            username="u",
            password="p",
            warehouse="WH",
            database="DB",
            disable_result_cache=False,
        )
        assert adapter.disable_result_cache is False

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_modify_warehouse_settings_default_false(self, mock_snowflake):
        adapter = SnowflakeAdapter(account="a", username="u", password="p", warehouse="WH", database="DB")
        assert adapter.modify_warehouse_settings is False

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_suppress_nondeterministic_errors_default_false(self, mock_snowflake):
        adapter = SnowflakeAdapter(account="a", username="u", password="p", warehouse="WH", database="DB")
        assert adapter.suppress_nondeterministic_errors is False

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_auto_suspend_zero_is_preserved(self, mock_snowflake):
        adapter = SnowflakeAdapter(
            account="a", username="u", password="p", warehouse="WH", database="DB", auto_suspend=0
        )
        assert adapter.auto_suspend == 0

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_auto_resume_false_is_preserved(self, mock_snowflake):
        adapter = SnowflakeAdapter(
            account="a", username="u", password="p", warehouse="WH", database="DB", auto_resume=False
        )
        assert adapter.auto_resume is False

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_multi_cluster_warehouse_false_by_default(self, mock_snowflake):
        adapter = SnowflakeAdapter(account="a", username="u", password="p", warehouse="WH", database="DB")
        assert adapter.multi_cluster_warehouse is False

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_file_format_default_csv(self, mock_snowflake):
        adapter = SnowflakeAdapter(account="a", username="u", password="p", warehouse="WH", database="DB")
        assert adapter.file_format == "CSV"

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_compression_default_auto(self, mock_snowflake):
        adapter = SnowflakeAdapter(account="a", username="u", password="p", warehouse="WH", database="DB")
        assert adapter.compression == "AUTO"

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_query_tag_default(self, mock_snowflake):
        adapter = SnowflakeAdapter(account="a", username="u", password="p", warehouse="WH", database="DB")
        assert adapter.query_tag == "BenchBox"

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_timezone_default_utc(self, mock_snowflake):
        adapter = SnowflakeAdapter(account="a", username="u", password="p", warehouse="WH", database="DB")
        assert adapter.timezone == "UTC"


class TestSnowflakeConfigureForBenchmarkEdgeCases:
    @patch("benchbox.platforms.snowflake.snowflake")
    def test_suppress_nondeterministic_errors_generates_sql(self, mock_snowflake):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        adapter = SnowflakeAdapter(
            account="a",
            username="u",
            password="p",
            warehouse="WH",
            database="DB",
            suppress_nondeterministic_errors=True,
            strict_validation=False,
        )

        adapter.configure_for_benchmark(mock_connection, "tpch")

        execute_calls = [str(call.args[0]) for call in mock_cursor.execute.call_args_list]
        assert any("ERROR_ON_NONDETERMINISTIC_MERGE = FALSE" in sql for sql in execute_calls)
        assert any("ERROR_ON_NONDETERMINISTIC_UPDATE = FALSE" in sql for sql in execute_calls)

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_modify_warehouse_settings_false_skips_alter_warehouse(self, mock_snowflake):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        adapter = SnowflakeAdapter(
            account="a",
            username="u",
            password="p",
            warehouse="WH",
            database="DB",
            modify_warehouse_settings=False,
            strict_validation=False,
        )

        adapter.configure_for_benchmark(mock_connection, "tpch")

        execute_calls = [str(call.args[0]) for call in mock_cursor.execute.call_args_list]
        assert not any("ALTER WAREHOUSE" in sql for sql in execute_calls)

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_result_cache_enabled_generates_true(self, mock_snowflake):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        adapter = SnowflakeAdapter(
            account="a",
            username="u",
            password="p",
            warehouse="WH",
            database="DB",
            disable_result_cache=False,
            strict_validation=False,
        )

        adapter.configure_for_benchmark(mock_connection, "olap")

        execute_calls = [str(call.args[0]) for call in mock_cursor.execute.call_args_list]
        assert any("USE_CACHED_RESULT = TRUE" in sql for sql in execute_calls)

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_multi_cluster_warehouse_generates_scaling_sql(self, mock_snowflake):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        adapter = SnowflakeAdapter(
            account="a",
            username="u",
            password="p",
            warehouse="WH",
            database="DB",
            multi_cluster_warehouse=True,
            modify_warehouse_settings=True,
            auto_suspend=60,
            auto_resume=True,
            strict_validation=False,
        )

        adapter.configure_for_benchmark(mock_connection, "olap")

        execute_calls = [str(call.args[0]) for call in mock_cursor.execute.call_args_list]
        alter_wh = [sql for sql in execute_calls if "ALTER WAREHOUSE WH SET" in sql]
        assert len(alter_wh) == 1
        assert "MIN_CLUSTER_COUNT = 1" in alter_wh[0]
        assert "MAX_CLUSTER_COUNT = 3" in alter_wh[0]
        assert "AUTO_SUSPEND = 60" in alter_wh[0]
        assert "AUTO_RESUME = True" in alter_wh[0]
        assert "SCALING_POLICY = 'STANDARD'" in alter_wh[0]

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_non_olap_benchmark_type_skips_olap_settings(self, mock_snowflake):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        adapter = SnowflakeAdapter(
            account="a",
            username="u",
            password="p",
            warehouse="WH",
            database="DB",
            modify_warehouse_settings=True,
            strict_validation=False,
        )

        adapter.configure_for_benchmark(mock_connection, "custom_benchmark")

        execute_calls = [str(call.args[0]) for call in mock_cursor.execute.call_args_list]
        assert not any("QUERY_ACCELERATION_MAX_SCALE_FACTOR" in sql for sql in execute_calls)
        assert not any("ALTER WAREHOUSE" in sql for sql in execute_calls)

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_session_base_settings_always_applied(self, mock_snowflake):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        adapter = SnowflakeAdapter(
            account="a",
            username="u",
            password="p",
            warehouse="WH",
            database="DB",
            timezone="America/New_York",
            strict_validation=False,
        )

        adapter.configure_for_benchmark(mock_connection, "custom")

        execute_calls = [str(call.args[0]) for call in mock_cursor.execute.call_args_list]
        assert any("TIMEZONE = 'America/New_York'" in sql for sql in execute_calls)
        assert any("AUTOCOMMIT = TRUE" in sql for sql in execute_calls)


class TestSnowflakeNormalizeExistingFiles:
    def test_filters_nonexistent_files(self, tmp_path):
        existing = tmp_path / "exists.tbl"
        existing.write_text("data")

        result = SnowflakeAdapter._normalize_existing_files([existing, tmp_path / "nonexistent.tbl"])

        assert result == [existing]

    def test_filters_empty_files(self, tmp_path):
        empty = tmp_path / "empty.tbl"
        empty.write_text("")
        nonempty = tmp_path / "data.tbl"
        nonempty.write_text("1|test|\n")

        result = SnowflakeAdapter._normalize_existing_files([empty, nonempty])

        assert result == [nonempty]

    def test_accepts_single_path_not_list(self, tmp_path):
        single = tmp_path / "single.tbl"
        single.write_text("1|data|\n")

        result = SnowflakeAdapter._normalize_existing_files(single)

        assert result == [single]

    def test_converts_strings_to_paths(self, tmp_path):
        f = tmp_path / "file.csv"
        f.write_text("data")

        result = SnowflakeAdapter._normalize_existing_files([str(f)])

        assert result == [f]


class TestSnowflakeBuildSnowflakeConfig:
    @patch("benchbox.platforms.snowflake.snowflake")
    def test_merges_saved_creds_and_options(self, mock_snowflake):
        from benchbox.platforms.snowflake import _build_snowflake_config

        mock_info = Mock()
        mock_info.display_name = "Snowflake"
        mock_info.driver_package = "snowflake-connector-python"

        with patch("benchbox.security.credentials.CredentialManager") as mock_cm:
            mock_cm.return_value.get_platform_credentials.return_value = {
                "account": "saved_acct",
                "username": "saved_user",
                "password": "saved_pass",
            }

            config = _build_snowflake_config(
                platform="snowflake",
                options={"warehouse": "CLI_WH"},
                overrides={"account": "override_acct"},
                info=mock_info,
            )

        assert config.account == "override_acct"
        assert config.warehouse == "CLI_WH"
        assert config.username == "saved_user"
        assert config.password == "saved_pass"

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_database_override_is_included(self, mock_snowflake):
        from benchbox.platforms.snowflake import _build_snowflake_config

        mock_info = Mock()
        mock_info.display_name = "Snowflake"
        mock_info.driver_package = "snowflake-connector-python"

        with patch("benchbox.security.credentials.CredentialManager") as mock_cm:
            mock_cm.return_value.get_platform_credentials.return_value = {
                "account": "a",
                "username": "u",
                "password": "p",
            }

            config = _build_snowflake_config(
                platform="snowflake",
                options={},
                overrides={"database": "MY_DB"},
                info=mock_info,
            )

        assert config.database == "MY_DB"

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_no_database_override_omits_database(self, mock_snowflake):
        from benchbox.platforms.snowflake import _build_snowflake_config

        mock_info = Mock()
        mock_info.display_name = "Snowflake"
        mock_info.driver_package = "snowflake-connector-python"

        with patch("benchbox.security.credentials.CredentialManager") as mock_cm:
            mock_cm.return_value.get_platform_credentials.return_value = {
                "account": "a",
                "username": "u",
                "password": "p",
            }

            config = _build_snowflake_config(
                platform="snowflake",
                options={},
                overrides={},
                info=mock_info,
            )

        assert not hasattr(config, "database") or config.database is None


class TestSnowflakeFromConfigEdgeCases:
    @patch("benchbox.platforms.snowflake.snowflake")
    def test_from_config_passes_behavior_options(self, mock_snowflake):
        config = {
            "benchmark": "tpch",
            "scale_factor": 1.0,
            "account": "a",
            "username": "u",
            "password": "p",
            "warehouse": "WH",
            "disable_result_cache": False,
            "strict_validation": False,
            "suppress_nondeterministic_errors": True,
            "modify_warehouse_settings": True,
        }

        adapter = SnowflakeAdapter.from_config(config)

        assert adapter.disable_result_cache is False
        assert adapter.strict_validation is False
        assert adapter.suppress_nondeterministic_errors is True
        assert adapter.modify_warehouse_settings is True

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_from_config_passes_file_format_and_compression(self, mock_snowflake):
        config = {
            "benchmark": "tpch",
            "scale_factor": 1.0,
            "account": "a",
            "username": "u",
            "password": "p",
            "file_format": "PARQUET",
            "compression": "GZIP",
        }

        adapter = SnowflakeAdapter.from_config(config)

        assert adapter.file_format == "PARQUET"
        assert adapter.compression == "GZIP"

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_from_config_passes_warehouse_settings(self, mock_snowflake):
        config = {
            "benchmark": "tpch",
            "scale_factor": 1.0,
            "account": "a",
            "username": "u",
            "password": "p",
            "warehouse_size": "XLARGE",
            "auto_suspend": 0,
            "auto_resume": False,
            "multi_cluster_warehouse": True,
        }

        adapter = SnowflakeAdapter.from_config(config)

        assert adapter.warehouse_size == "XLARGE"
        assert adapter.auto_suspend == 0
        assert adapter.auto_resume is False
        assert adapter.multi_cluster_warehouse is True


class TestSnowflakeCreateLoadFileFormats:
    @patch("benchbox.platforms.snowflake.snowflake")
    def test_default_compression_auto(self, mock_snowflake):
        mock_cursor = Mock()
        adapter = SnowflakeAdapter(
            account="a", username="u", password="p", warehouse="WH", database="DB", schema="MY_SCHEMA"
        )

        adapter._create_load_file_formats(mock_cursor)

        execute_calls = [str(call.args[0]) for call in mock_cursor.execute.call_args_list]
        assert len(execute_calls) == 2

        csv_sql = execute_calls[0]
        assert "MY_SCHEMA.BENCHBOX_CSV_FORMAT" in csv_sql
        assert "FIELD_DELIMITER = ','" in csv_sql
        assert "COMPRESSION = 'AUTO'" in csv_sql
        assert "ERROR_ON_COLUMN_COUNT_MISMATCH = FALSE" in csv_sql
        assert "REPLACE_INVALID_CHARACTERS = TRUE" in csv_sql
        assert "EMPTY_FIELD_AS_NULL = TRUE" in csv_sql

        tbl_sql = execute_calls[1]
        assert "MY_SCHEMA.BENCHBOX_TBL_FORMAT" in tbl_sql
        assert "FIELD_DELIMITER = '|'" in tbl_sql
        assert "COMPRESSION = 'AUTO'" in tbl_sql


class TestSnowflakeDriverIsolation:
    @patch("benchbox.platforms.snowflake.snowflake")
    def test_driver_isolation_is_feasible_client_only(self, mock_snowflake):
        adapter = SnowflakeAdapter(account="a", username="u", password="p", warehouse="WH", database="DB")
        assert adapter.driver_isolation_capability == DriverIsolationCapability.FEASIBLE_CLIENT_ONLY


class TestSnowflakeGenerateTuningClauseEdgeCases:
    @patch("benchbox.platforms.snowflake.snowflake")
    def test_partition_columns_used_as_clustering_when_no_explicit_clustering(self, mock_snowflake):
        adapter = SnowflakeAdapter(account="a", username="u", password="p", warehouse="WH", database="DB")

        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = True

        mock_col = Mock()
        mock_col.name = "order_date"
        mock_col.order = 1

        with patch("benchbox.core.tuning.interface.TuningType") as mock_tuning_type:
            mock_tuning_type.CLUSTERING = "clustering"
            mock_tuning_type.PARTITIONING = "partitioning"

            def mock_get_columns_by_type(tuning_type):
                if tuning_type == mock_tuning_type.PARTITIONING:
                    return [mock_col]
                return []

            mock_tuning.get_columns_by_type.side_effect = mock_get_columns_by_type

            clause = adapter.generate_tuning_clause(mock_tuning)

        assert clause == "CLUSTER BY (order_date)"

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_multiple_clustering_columns_ordered(self, mock_snowflake):
        adapter = SnowflakeAdapter(account="a", username="u", password="p", warehouse="WH", database="DB")

        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = True

        col2 = Mock()
        col2.name = "b_col"
        col2.order = 2
        col1 = Mock()
        col1.name = "a_col"
        col1.order = 1

        with patch("benchbox.core.tuning.interface.TuningType") as mock_tuning_type:
            mock_tuning_type.CLUSTERING = "clustering"
            mock_tuning_type.PARTITIONING = "partitioning"

            def mock_get_columns_by_type(tuning_type):
                if tuning_type == mock_tuning_type.CLUSTERING:
                    return [col2, col1]
                return []

            mock_tuning.get_columns_by_type.side_effect = mock_get_columns_by_type

            clause = adapter.generate_tuning_clause(mock_tuning)

        assert clause == "CLUSTER BY (a_col, b_col)"

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_no_tuning_returns_empty_string(self, mock_snowflake):
        adapter = SnowflakeAdapter(account="a", username="u", password="p", warehouse="WH", database="DB")

        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = False

        clause = adapter.generate_tuning_clause(mock_tuning)
        assert clause == ""


class TestSnowflakeCreateSchemaSQL:
    @patch("benchbox.platforms.snowflake.snowflake")
    def test_create_schema_executes_database_and_schema_ddl(self, mock_snowflake):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        adapter = SnowflakeAdapter(
            account="a",
            username="u",
            password="p",
            warehouse="WH",
            database="MY_DB",
            schema="MY_SCHEMA",
        )

        mock_benchmark = Mock()

        with (
            patch.object(adapter, "_should_skip_schema_creation", return_value=False),
            patch.object(adapter, "_create_schema_with_tuning", return_value="CREATE TABLE t (id INT)"),
        ):
            elapsed = adapter.create_schema(mock_benchmark, mock_connection)

        assert isinstance(elapsed, float)
        execute_calls = [str(call.args[0]) for call in mock_cursor.execute.call_args_list]
        assert "CREATE DATABASE IF NOT EXISTS MY_DB" in execute_calls
        assert "USE DATABASE MY_DB" in execute_calls
        assert "CREATE SCHEMA IF NOT EXISTS MY_SCHEMA" in execute_calls
        assert "USE SCHEMA MY_SCHEMA" in execute_calls
        assert any("BenchBox_schema_creation" in sql for sql in execute_calls)
        assert "CREATE OR REPLACE TABLE t (id INT)" in execute_calls

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_create_schema_skips_when_tables_exist(self, mock_snowflake):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        adapter = SnowflakeAdapter(
            account="a",
            username="u",
            password="p",
            warehouse="WH",
            database="MY_DB",
            schema="MY_SCHEMA",
        )

        with patch.object(adapter, "_should_skip_schema_creation", return_value=True):
            elapsed = adapter.create_schema(Mock(), mock_connection)

        assert isinstance(elapsed, float)
        execute_calls = [str(call.args[0]) for call in mock_cursor.execute.call_args_list]
        assert "CREATE DATABASE IF NOT EXISTS MY_DB" in execute_calls
        assert not any("QUERY_TAG" in sql for sql in execute_calls)


class TestSnowflakeParseResultsEdgeCases:
    @patch("benchbox.platforms.snowflake.snowflake")
    def test_skips_rows_with_3_or_fewer_columns(self, mock_snowflake):
        adapter = SnowflakeAdapter(account="a", username="u", password="p", warehouse="WH", database="DB")
        adapter._parse_copy_results([["a"], ["a", "b"], ["a", "b", "c"]])

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_loaded_status_is_silently_skipped(self, mock_snowflake):
        import logging

        adapter = SnowflakeAdapter(account="a", username="u", password="p", warehouse="WH", database="DB")

        with patch.object(adapter.logger, "warning") as mock_warn:
            adapter._parse_copy_results([["orders.csv.gz", "LOADED", None, 100, None, None]])
        mock_warn.assert_not_called()


class _StatefulSnowflakeCursor:
    ROWS_PER_FILE = 2

    def __init__(self, existing_rows=25):
        self.tables = {"LINEITEM": [1] * existing_rows}
        self.staged: dict[str, list[int]] = {}
        self.load_history: set[str] = set()
        self.statements: list[str] = []
        self._last_count = 0
        self._last_copy_rows: list[tuple[str, int]] = []

    @staticmethod
    def _file_key(statement):
        token = str(statement).split()[1]
        return token.split("file://", 1)[1].split()[0].rsplit("/", 1)[-1]

    def execute(self, sql):
        statement = str(sql)
        self.statements.append(statement)
        upper = statement.strip().upper()
        if upper.startswith("REMOVE "):
            self.staged = {}
            return
        if upper.startswith("PUT "):
            self.staged[self._file_key(statement)] = [1] * self.ROWS_PER_FILE
            return
        if upper.startswith("TRUNCATE TABLE"):
            target = statement.strip().split()[-1].strip().strip('"')
            self.tables[target.upper()] = []
            return
        if "COPY INTO" in upper:
            idx = upper.index("COPY INTO") + len("COPY INTO")
            target = upper[idx:].strip().split()[0].strip().strip('"')
            force = "FORCE = TRUE" in upper or "FORCE=TRUE" in upper
            fresh = [name for name in self.staged if force or name not in self.load_history]
            self.tables.setdefault(target.upper(), []).extend([1] * (len(fresh) * self.ROWS_PER_FILE))
            self._last_copy_rows = [(name, self.ROWS_PER_FILE if name in fresh else 0) for name in self.staged]
            self.load_history.update(self.staged)
            self.staged = {}
            return
        if upper.startswith("SELECT COUNT"):
            target = statement.strip().split()[-1].strip().strip('"').strip(";")
            self._last_count = len(self.tables.get(target.upper(), []))
            return
        if upper.startswith("SHOW TABLES LIKE"):
            return

    def fetchall(self):
        return [[name, "LOADED", None, rows, None, None] for name, rows in self._last_copy_rows]

    def fetchone(self):
        return (self._last_count,)


class TestSnowflakeIdempotentLoad:
    def _adapter(self, **kwargs):
        from benchbox.platforms.snowflake import SnowflakeAdapter

        with patch("benchbox.platforms.snowflake.snowflake"):
            return SnowflakeAdapter(
                account="test_account",
                username="test_user",
                password="test_pass",
                warehouse="TEST_WH",
                database="TEST_DB",
                schema="PUBLIC",
                **kwargs,
            )

    def _tbl_files(self, tmp_path):
        paths = []
        for i in (1, 2):
            path = tmp_path / f"lineitem.tbl.{i}"
            path.write_bytes(b"1|one|\n")
            paths.append(path)
        return paths

    def test_double_load_stable_count_with_truncate_before_copy(self, tmp_path):
        adapter = self._adapter(force_recreate=True)
        cursor = _StatefulSnowflakeCursor(existing_rows=25)
        files = self._tbl_files(tmp_path)
        try:
            first = adapter._load_table_from_stage(cursor, "lineitem", "LINEITEM", files)
            second = adapter._load_table_from_stage(cursor, "lineitem", "LINEITEM", files)
        finally:
            for path in files:
                path.unlink()

        assert first == 2 * len(files)
        assert second == first
        truncates = [s for s in cursor.statements if s.strip().upper().startswith("TRUNCATE TABLE")]
        assert len(truncates) == 2
        first_truncate = cursor.statements.index(truncates[0])
        first_copy = next(i for i, s in enumerate(cursor.statements) if "COPY INTO" in s.upper())
        assert first_truncate < first_copy
        assert "TRUNCATE TABLE LINEITEM" in truncates[0]

    def test_default_load_is_full_refresh_not_append(self, tmp_path):
        adapter = self._adapter()
        assert adapter.force_recreate is False
        cursor = _StatefulSnowflakeCursor(existing_rows=25)
        files = self._tbl_files(tmp_path)
        try:
            first = adapter._load_table_from_stage(cursor, "lineitem", "LINEITEM", files)
            second = adapter._load_table_from_stage(cursor, "lineitem", "LINEITEM", files)
        finally:
            for path in files:
                path.unlink()

        assert first == 2 * len(files)
        assert second == first
        uppers = [s.strip().upper() for s in cursor.statements]
        assert any(s.startswith("REMOVE ") for s in uppers)
        assert any(s.startswith("PUT ") and "OVERWRITE = TRUE" in s for s in uppers)
        assert any("COPY INTO" in s and "FORCE = TRUE" in s for s in uppers)
        assert sum(s.startswith("TRUNCATE TABLE") for s in uppers) == 2

    def test_truncate_uses_quoted_fallback_target(self, tmp_path):
        adapter = self._adapter(force_recreate=True)
        cursor = _StatefulSnowflakeCursor(existing_rows=0)

        def _execute(sql):
            if "@%LINEITEM" in str(sql):
                raise Exception("Stage '@%LINEITEM' does not exist or not authorized")
            _StatefulSnowflakeCursor.execute(cursor, sql)

        cursor.execute = _execute

        path = tmp_path / "lineitem.tbl"
        path.write_bytes(b"1|one|\n")
        try:
            adapter._load_table_from_stage(cursor, "lineitem", "LINEITEM", [path])
        finally:
            path.unlink()

        assert any('TRUNCATE TABLE "lineitem"' in s for s in cursor.statements)
        truncate_idx = next(i for i, s in enumerate(cursor.statements) if "TRUNCATE TABLE" in s)
        copy_idx = next(i for i, s in enumerate(cursor.statements) if "COPY INTO" in s.upper())
        assert truncate_idx < copy_idx

    def test_should_skip_schema_creation_returns_false_when_forced(self):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchone.side_effect = [(None, "LINEITEM"), (25,)]

        adapter = self._adapter(force_recreate=True)
        with patch.object(adapter, "_get_expected_tables", return_value=["lineitem"]):
            assert adapter._should_skip_schema_creation(Mock(), mock_connection) is False
        mock_cursor.execute.assert_not_called()

    def test_should_skip_schema_creation_unchanged_when_not_forced(self):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchone.side_effect = [(None, "LINEITEM"), (25,)]

        adapter = self._adapter()
        with patch.object(adapter, "_get_expected_tables", return_value=["lineitem"]):
            assert adapter._should_skip_schema_creation(Mock(), mock_connection) is True

    def test_from_config_forwards_force_recreate(self):
        from benchbox.platforms.snowflake import SnowflakeAdapter

        base = {
            "account": "a",
            "username": "u",
            "password": "p",
            "warehouse": "WH",
            "database": "DB",
            "schema": "PUBLIC",
            "benchmark": "tpch",
            "scale_factor": 0.01,
        }
        with patch("benchbox.platforms.snowflake.snowflake"):
            assert SnowflakeAdapter.from_config({**base, "force_recreate": True}).force_recreate is True
            assert SnowflakeAdapter.from_config({**base, "force": True}).force_recreate is True
            assert SnowflakeAdapter.from_config(dict(base)).force_recreate is False

    def test_dirty_stage_leftovers_not_reloaded(self, tmp_path):
        adapter = self._adapter()
        cursor = _StatefulSnowflakeCursor(existing_rows=25)
        cursor.staged = {"stale.tbl": [1] * 99}
        files = self._tbl_files(tmp_path)
        try:
            count = adapter._load_table_from_stage(cursor, "lineitem", "LINEITEM", files)
        finally:
            for path in files:
                path.unlink()

        assert count == 2 * len(files)
        assert "stale.tbl" not in cursor.staged

    def test_load_history_skips_seen_files_without_force(self):
        cursor = _StatefulSnowflakeCursor(existing_rows=0)
        cursor.staged = {"lineitem.tbl.1": [1, 1]}
        cursor.load_history = {"lineitem.tbl.1"}
        cursor.execute(
            "COPY INTO LINEITEM FROM @%LINEITEM FILE_FORMAT = (FORMAT_NAME = 'X') ON_ERROR = 'CONTINUE' PURGE = TRUE"
        )
        cursor.execute("SELECT COUNT(*) FROM LINEITEM")
        assert cursor.fetchone() == (0,)

        cursor.staged = {"lineitem.tbl.1": [1, 1]}
        cursor.execute(
            "COPY INTO LINEITEM FROM @%LINEITEM FILE_FORMAT = (FORMAT_NAME = 'X')"
            " ON_ERROR = 'CONTINUE' PURGE = TRUE FORCE = TRUE"
        )
        cursor.execute("SELECT COUNT(*) FROM LINEITEM")
        assert cursor.fetchone() == (2,)

    def test_failed_put_leaves_previous_data_and_raises(self, tmp_path):
        adapter = self._adapter()
        cursor = _StatefulSnowflakeCursor(existing_rows=25)

        real_execute = cursor.execute

        def _execute(sql):
            if str(sql).strip().upper().startswith("PUT "):
                raise RuntimeError("network down")
            real_execute(sql)

        cursor.execute = _execute
        path = tmp_path / "lineitem.tbl"
        path.write_bytes(b"1|one|\n")
        try:
            with pytest.raises(RuntimeError, match="network down"):
                adapter._load_table_from_stage(cursor, "lineitem", "LINEITEM", [path])
        finally:
            path.unlink()

        assert cursor.tables["LINEITEM"] == [1] * 25
        assert not any(s.strip().upper().startswith("TRUNCATE TABLE") for s in cursor.statements)

    def test_failed_copy_raises(self, tmp_path):
        adapter = self._adapter()
        cursor = _StatefulSnowflakeCursor(existing_rows=25)

        real_execute = cursor.execute

        def _execute(sql):
            if "COPY INTO" in str(sql).upper():
                raise RuntimeError("warehouse suspended")
            real_execute(sql)

        cursor.execute = _execute
        path = tmp_path / "lineitem.tbl"
        path.write_bytes(b"1|one|\n")
        try:
            with pytest.raises(RuntimeError, match="warehouse suspended"):
                adapter._load_table_from_stage(cursor, "lineitem", "LINEITEM", [path])
        finally:
            path.unlink()

    def test_truncate_missing_table_tolerated(self, tmp_path):
        adapter = self._adapter()
        cursor = _StatefulSnowflakeCursor(existing_rows=0)
        del cursor.tables["LINEITEM"]

        real_execute = cursor.execute

        def _execute(sql):
            if str(sql).strip().upper().startswith("TRUNCATE TABLE"):
                raise Exception("Table 'LINEITEM' does not exist or not authorized")
            real_execute(sql)

        cursor.execute = _execute
        path = tmp_path / "lineitem.tbl"
        path.write_bytes(b"1|one|\n")
        try:
            count = adapter._load_table_from_stage(cursor, "lineitem", "LINEITEM", [path])
        finally:
            path.unlink()

        assert count == 2

    def test_load_data_fails_fast_on_table_error(self, tmp_path):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        path = tmp_path / "lineitem.tbl"
        path.write_bytes(b"1|one|\n")
        mock_benchmark = Mock()
        mock_benchmark.tables = {"lineitem": str(path)}

        adapter = self._adapter()
        with patch.object(adapter, "_load_table_from_stage", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError, match="boom"):
                adapter.load_data(mock_benchmark, mock_connection, tmp_path)
