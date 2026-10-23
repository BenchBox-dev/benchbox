# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, call, patch

import pytest

from benchbox.platforms.redshift import RedshiftAdapter
from benchbox.utils.iceberg_layout import local_iceberg_location

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.cloud_import,
]


def _make_real_iceberg_table(table_dir: Path) -> None:
    pytest.importorskip("pyiceberg", reason="iceberg staging tests need pyiceberg")
    pa = pytest.importorskip("pyarrow", reason="iceberg staging tests need pyarrow")
    from pyiceberg.catalog.sql import SqlCatalog
    from pyiceberg.schema import Schema
    from pyiceberg.types import LongType, NestedField

    catalog = SqlCatalog("rs-test", uri=f"sqlite:///{table_dir.parent}/cat.db", warehouse=str(table_dir.parent))
    try:
        catalog.create_namespace_if_not_exists("ns")
        table = catalog.create_table(
            "ns.t",
            schema=Schema(NestedField(1, "o_orderkey", LongType())),
            location=local_iceberg_location(table_dir),
            properties={"format-version": "2"},
        )
        table.overwrite(pa.table({"o_orderkey": [1]}))
    finally:
        catalog.engine.dispose()


def test_iceberg_fixture_catalog_releases_its_database_handle(tmp_path: Path) -> None:
    from sqlalchemy.engine import Engine

    table_dir = tmp_path / "orders"
    with patch.object(Engine, "dispose", autospec=True, side_effect=Engine.dispose) as dispose:
        _make_real_iceberg_table(table_dir)

    dispose.assert_called_once()
    (tmp_path / "cat.db").unlink()


class TestRedshiftAdapter:
    @pytest.fixture(autouse=True)
    def _skip_cluster_state_check(self):
        with patch.object(RedshiftAdapter, "_resolve_connect_timeout", return_value=10):
            yield

    def test_initialization_success(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                port=5439,
                database="test_db",
                username="test_user",
                password="test_pass",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        assert adapter.platform_name == "Redshift"
        assert adapter.get_target_dialect() == "redshift"
        assert adapter.host == "test-cluster.redshift.amazonaws.com"
        assert adapter.port == 5439
        assert adapter.database == "test_db"
        assert adapter.username == "test_user"

    def test_initialization_with_defaults(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        assert adapter.port == 5439
        assert adapter.connect_timeout == 10
        assert adapter.statement_timeout == 0
        assert adapter.sslmode == "require"
        assert adapter.wlm_query_slot_count == 1

    def test_initialization_missing_driver(self):
        import benchbox.platforms.redshift as redshift_module

        with (
            patch.object(redshift_module, "redshift_connector", None),
            patch.object(redshift_module, "psycopg", None, create=True),
            patch(
                "benchbox.platforms.redshift.check_platform_dependencies", return_value=(False, ["redshift-connector"])
            ),
            patch(
                "benchbox.platforms.redshift.get_dependency_error_message",
                return_value="Missing Redshift dependencies",
            ),
        ):
            with pytest.raises(ImportError, match="Missing Redshift dependencies"):
                RedshiftAdapter(
                    host="test-cluster.redshift.amazonaws.com",
                    database="test_db",
                    username="test_user",
                    password="test_pass",
                )

    def test_initialization_missing_required_config(self):
        from benchbox.core.exceptions import ConfigurationError

        try:
            with pytest.raises(ConfigurationError, match="Redshift configuration is incomplete"):
                RedshiftAdapter()
        except ImportError:
            pytest.skip("Redshift drivers not installed")

    def test_get_connection_params(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                port=5439,
                database="test_db",
                username="test_user",
                password="test_pass",
                sslmode="require",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        params = adapter._get_connection_params()

        assert params["host"] == "test-cluster.redshift.amazonaws.com"
        assert params["port"] == 5439
        assert params["database"] == "test_db"
        assert params["user"] == "test_user"
        assert params["password"] == "test_pass"
        assert params["sslmode"] == "require"

    def test_create_s3_client_uses_retry_safe_checksum_mode(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
                aws_region="us-east-2",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        mock_session = Mock()
        mock_session.get_credentials.return_value = object()
        mock_client = Mock()
        mock_session.client.return_value = mock_client
        with patch("benchbox.platforms.redshift.boto3.Session", return_value=mock_session) as mock_session_ctor:
            created_client = adapter._create_s3_client()

        assert created_client is mock_client
        session_kwargs = mock_session_ctor.call_args.kwargs
        assert session_kwargs["region_name"] == "us-east-2"
        client_args = mock_session.client.call_args.args
        client_kwargs = mock_session.client.call_args.kwargs
        assert client_args == ("s3",)
        assert client_kwargs["config"].request_checksum_calculation == "when_required"

        override_params = adapter._get_connection_params(
            host="override-cluster.redshift.amazonaws.com", database="override_db"
        )
        assert override_params["host"] == "override-cluster.redshift.amazonaws.com"
        assert override_params["database"] == "override_db"
        assert override_params["user"] == "test_user"
        assert "database" in override_params

    def test_create_s3_client_raises_when_credentials_missing(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        mock_session = Mock()
        mock_session.get_credentials.return_value = None
        with patch("benchbox.platforms.redshift.boto3.Session", return_value=mock_session):
            with pytest.raises(ValueError, match="AWS credentials not found"):
                adapter._create_s3_client()

    @pytest.mark.parametrize(
        "key_id, secret",
        [("AKID_ONLY", None), (None, "SECRET_ONLY")],
        ids=["key_without_secret", "secret_without_key"],
    )
    def test_create_s3_client_rejects_asymmetric_credentials(self, key_id, secret):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
                aws_access_key_id=key_id,
                aws_secret_access_key=secret,
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        with pytest.raises(ValueError, match="both aws_access_key_id and aws_secret_access_key"):
            adapter._create_s3_client()

    def test_create_s3_client_passes_session_token_to_boto3(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
                aws_access_key_id="AKID",
                aws_secret_access_key="SECRET",
                aws_session_token="TOK123",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        mock_session = Mock()
        mock_session.get_credentials.return_value = object()
        mock_session.client.return_value = Mock()
        with patch("benchbox.platforms.redshift.boto3.Session", return_value=mock_session) as mock_ctor:
            adapter._create_s3_client()

        session_kwargs = mock_ctor.call_args.kwargs
        assert session_kwargs["aws_access_key_id"] == "AKID"
        assert session_kwargs["aws_secret_access_key"] == "SECRET"
        assert session_kwargs["aws_session_token"] == "TOK123"

    def test_create_admin_connection(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
                admin_database="admin_db",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        mock_connection = Mock()

        import benchbox.platforms.redshift as redshift_module

        if redshift_module.redshift_connector:
            with patch.object(redshift_module.redshift_connector, "connect", return_value=mock_connection):
                connection = adapter._create_admin_connection()

                redshift_module.redshift_connector.connect.assert_called_once()
                call_kwargs = redshift_module.redshift_connector.connect.call_args[1]
                assert call_kwargs["database"] == "admin_db"
                assert call_kwargs["host"] == "test-cluster.redshift.amazonaws.com"
                assert call_kwargs["user"] == "test_user"
        elif redshift_module.psycopg:
            with patch.object(redshift_module.psycopg, "connect", return_value=mock_connection):
                connection = adapter._create_admin_connection()

                redshift_module.psycopg.connect.assert_called_once()
                call_kwargs = redshift_module.psycopg.connect.call_args[1]
                assert call_kwargs["database"] == "admin_db"
                assert call_kwargs["host"] == "test-cluster.redshift.amazonaws.com"
                assert call_kwargs["user"] == "test_user"
        else:
            pytest.skip("Neither redshift_connector nor psycopg available")
            return

        assert connection == mock_connection

    def test_create_admin_connection_uses_shared_driver_helper(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
                admin_database="admin_db",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        mock_connection = Mock()

        with patch.object(adapter, "_connect_with_driver", return_value=mock_connection) as mock_connect:
            connection = adapter._create_admin_connection(connect_timeout=27)

        assert connection == mock_connection
        mock_connect.assert_called_once_with(
            application_name="BenchBox-Admin",
            connect_timeout=27,
            database="admin_db",
        )

    def test_create_admin_connection_uses_adaptive_timeout_by_default(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
                admin_database="admin_db",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        mock_connection = Mock()

        with (
            patch.object(adapter, "_resolve_connect_timeout", return_value=42) as mock_timeout,
            patch.object(adapter, "_connect_with_driver", return_value=mock_connection) as mock_connect,
        ):
            connection = adapter._create_admin_connection()

        assert connection == mock_connection
        mock_timeout.assert_called_once_with()
        mock_connect.assert_called_once_with(
            application_name="BenchBox-Admin",
            connect_timeout=42,
            database="admin_db",
        )

    def test_create_direct_connection(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
                wlm_query_queue_name="validation_queue",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        import benchbox.platforms.redshift as redshift_module

        if redshift_module.redshift_connector:
            with patch.object(redshift_module.redshift_connector, "connect", return_value=mock_connection):
                connection = adapter._create_direct_connection(database="target_db")
        elif redshift_module.psycopg:
            with patch.object(redshift_module.psycopg, "connect", return_value=mock_connection):
                connection = adapter._create_direct_connection(database="target_db")
        else:
            pytest.skip("Neither redshift_connector nor psycopg available")
            return

        assert connection == mock_connection

        mock_cursor.execute.assert_called_with("SET query_group TO 'validation_queue'")
        mock_cursor.close.assert_called_once()

    def test_create_direct_connection_uses_shared_driver_helper(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
                wlm_query_queue_name="validation_queue",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        with patch.object(adapter, "_connect_with_driver", return_value=mock_connection) as mock_connect:
            connection = adapter._create_direct_connection(database="target_db", connect_timeout=17)

        assert connection == mock_connection
        mock_connect.assert_called_once_with(
            application_name="BenchBox-Validation",
            connect_timeout=17,
            database="target_db",
        )
        mock_cursor.execute.assert_called_with("SET query_group TO 'validation_queue'")
        mock_cursor.close.assert_called_once()

    def test_create_direct_connection_adaptive_timeout_no_queue(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        mock_connection = Mock()
        mock_connection.cursor.return_value = Mock()

        with patch.object(adapter, "_connect_with_driver", return_value=mock_connection) as mock_connect:
            adapter._create_direct_connection(database="target_db")

        mock_connect.assert_called_once_with(
            application_name="BenchBox-Validation",
            connect_timeout=10,
            database="target_db",
        )

    def test_create_direct_connection_uses_adaptive_timeout_by_default(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
                wlm_query_queue_name="validation_queue",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        with (
            patch.object(adapter, "_resolve_connect_timeout", return_value=42) as mock_timeout,
            patch.object(adapter, "_connect_with_driver", return_value=mock_connection) as mock_connect,
        ):
            connection = adapter._create_direct_connection(database="target_db")

        assert connection == mock_connection
        mock_timeout.assert_called_once_with()
        mock_connect.assert_called_once_with(
            application_name="BenchBox-Validation",
            connect_timeout=42,
            database="target_db",
        )
        mock_cursor.execute.assert_called_with("SET query_group TO 'validation_queue'")
        mock_cursor.close.assert_called_once()

    def test_check_server_database_exists_true(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ("test_db",)

        import benchbox.platforms.redshift as redshift_module

        if redshift_module.redshift_connector:
            with patch.object(redshift_module.redshift_connector, "connect", return_value=mock_connection):
                result = adapter.check_server_database_exists(database="test_db")
        elif redshift_module.psycopg:
            with patch.object(redshift_module.psycopg, "connect", return_value=mock_connection):
                result = adapter.check_server_database_exists(database="test_db")
        else:
            pytest.skip("Neither redshift_connector nor psycopg available")
            return

        assert result is True

        mock_cursor.execute.assert_called_once()
        call_args = mock_cursor.execute.call_args[0][0]
        assert "SELECT datname FROM pg_database WHERE datname" in call_args

    def test_check_server_database_exists_false(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = None

        import benchbox.platforms.redshift as redshift_module

        if redshift_module.redshift_connector:
            with patch.object(redshift_module.redshift_connector, "connect", return_value=mock_connection):
                result = adapter.check_server_database_exists(database="nonexistent_db")
        elif redshift_module.psycopg:
            with patch.object(redshift_module.psycopg, "connect", return_value=mock_connection):
                result = adapter.check_server_database_exists(database="nonexistent_db")
        else:
            pytest.skip("Neither redshift_connector nor psycopg available")
            return

        assert result is False

        mock_cursor.execute.assert_called_once()
        call_args = mock_cursor.execute.call_args[0][0]
        assert "SELECT datname FROM pg_database WHERE datname" in call_args

    def test_check_server_database_exists_connection_error(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        import benchbox.platforms.redshift as redshift_module

        if redshift_module.redshift_connector:
            with patch.object(
                redshift_module.redshift_connector, "connect", side_effect=Exception("Connection failed")
            ):
                result = adapter.check_server_database_exists(database="test_db")
        elif redshift_module.psycopg:
            with patch.object(redshift_module.psycopg, "connect", side_effect=Exception("Connection failed")):
                result = adapter.check_server_database_exists(database="test_db")
        else:
            pytest.skip("Neither redshift_connector nor psycopg available")
            return

        assert result is False

    def test_drop_database(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ("test_db",)

        import benchbox.platforms.redshift as redshift_module

        if redshift_module.redshift_connector:
            with patch.object(redshift_module.redshift_connector, "connect", return_value=mock_connection):
                adapter.drop_database(database="test_db")

                drop_calls = [call for call in mock_cursor.execute.call_args_list if "DROP DATABASE" in str(call)]
                assert len(drop_calls) > 0, "DROP DATABASE should have been executed"
        elif redshift_module.psycopg:
            with patch.object(redshift_module.psycopg, "connect", return_value=mock_connection):
                adapter.drop_database(database="test_db")

                drop_calls = [call for call in mock_cursor.execute.call_args_list if "DROP DATABASE" in str(call)]
                assert len(drop_calls) > 0, "DROP DATABASE should have been executed"
        else:
            pytest.skip("Neither redshift_connector nor psycopg available")
            return

        assert mock_connection.autocommit is True

    def test_drop_database_uses_long_running_timeout(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        mock_connection = Mock()
        mock_connection.cursor.return_value = Mock()

        with (
            patch.object(adapter, "check_server_database_exists", return_value=True),
            patch.object(adapter, "_long_running_timeout", return_value=321) as mock_timeout,
            patch.object(adapter, "_create_admin_connection", return_value=mock_connection) as mock_admin_conn,
        ):
            adapter.drop_database(database="test_db")

        mock_timeout.assert_called_once_with()
        mock_admin_conn.assert_called_once_with(connect_timeout=321)

    def test_drop_database_retry_on_active_connection(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        first_cursor = Mock()
        second_cursor = Mock()
        first_connection = Mock()
        second_connection = Mock()
        first_connection.cursor.return_value = first_cursor
        second_connection.cursor.return_value = second_cursor

        def drop_side_effect(sql, *args):
            if "DROP DATABASE" in sql:
                raise Exception({"C": "55006", "M": "database is being accessed by other users"})

        first_cursor.execute.side_effect = drop_side_effect

        with (
            patch.object(adapter, "check_server_database_exists", return_value=True),
            patch.object(adapter, "_long_running_timeout", return_value=60),
            patch.object(
                adapter,
                "_create_admin_connection",
                side_effect=[first_connection, second_connection],
            ) as mock_admin_conn,
        ):
            adapter.drop_database(database="test_db")

        assert mock_admin_conn.call_count == 2, "Expected two admin connections: initial + retry"
        assert second_connection.autocommit is True
        first_cursor.close.assert_called_once()
        first_connection.close.assert_called_once()
        drop_calls = [c for c in second_cursor.execute.call_args_list if "DROP DATABASE" in str(c)]
        assert len(drop_calls) == 1, "DROP DATABASE should be executed exactly once on the retry cursor"

    def test_drop_database_non_retryable_sqlstate_re_raises(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        mock_cursor.execute.side_effect = [
            None,
            Exception({"C": "42501", "M": "permission denied to drop database"}),
        ]

        with (
            patch.object(adapter, "check_server_database_exists", return_value=True),
            patch.object(adapter, "_long_running_timeout", return_value=60),
            patch.object(adapter, "_create_admin_connection", return_value=mock_connection) as mock_admin_conn,
        ):
            with pytest.raises(RuntimeError, match="Failed to drop Redshift database"):
                adapter.drop_database(database="test_db")

        mock_admin_conn.assert_called_once()

    def test_drop_database_retry_drop_failure_propagates(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        first_cursor = Mock()
        second_cursor = Mock()
        first_connection = Mock()
        second_connection = Mock()
        first_connection.cursor.return_value = first_cursor
        second_connection.cursor.return_value = second_cursor

        def first_drop_side_effect(sql, *args):
            if "DROP DATABASE" in sql:
                raise Exception({"C": "55006", "M": "database is being accessed by other users"})

        def second_drop_side_effect(sql, *args):
            if "DROP DATABASE" in sql:
                raise Exception({"C": "55006", "M": "database is still being accessed"})

        first_cursor.execute.side_effect = first_drop_side_effect
        second_cursor.execute.side_effect = second_drop_side_effect

        with (
            patch.object(adapter, "check_server_database_exists", return_value=True),
            patch.object(adapter, "_long_running_timeout", return_value=60),
            patch.object(
                adapter,
                "_create_admin_connection",
                side_effect=[first_connection, second_connection],
            ),
        ):
            with pytest.raises(RuntimeError, match="Failed to drop Redshift database"):
                adapter.drop_database(database="test_db")

    def test_create_connection_success(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        with (
            patch.object(adapter, "handle_existing_database"),
            patch.object(adapter, "check_server_database_exists", return_value=True),
        ):
            import benchbox.platforms.redshift as redshift_module

            if redshift_module.redshift_connector:
                with patch.object(redshift_module.redshift_connector, "connect", return_value=mock_connection):
                    connection = adapter.create_connection()
            elif redshift_module.psycopg:
                with patch.object(redshift_module.psycopg, "connect", return_value=mock_connection):
                    connection = adapter.create_connection()
            else:
                pytest.skip("Neither redshift_connector nor psycopg available")
                return

        assert connection == mock_connection

        mock_cursor.execute.assert_called_with("SELECT version()")
        mock_cursor.fetchone.assert_called_once()
        mock_cursor.close.assert_called_once()

    def test_create_connection_with_wlm_settings(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
                wlm_query_slot_count=4,
                statement_timeout=300000,
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        with patch.object(adapter, "handle_existing_database"):
            import benchbox.platforms.redshift as redshift_module

            if redshift_module.redshift_connector:
                with patch.object(redshift_module.redshift_connector, "connect", return_value=mock_connection):
                    adapter.create_connection()
            elif redshift_module.psycopg:
                with patch.object(redshift_module.psycopg, "connect", return_value=mock_connection):
                    adapter.create_connection()
            else:
                pytest.skip("Neither redshift_connector nor psycopg available")
                return

        expected_calls = [
            call("SET wlm_query_slot_count = 4"),
            call("SET statement_timeout = 300000"),
            call('SET search_path TO "public"'),
            call("SELECT version()"),
        ]
        mock_cursor.execute.assert_has_calls(expected_calls)

    def test_create_connection_failure(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        with patch.object(adapter, "handle_existing_database"):
            import benchbox.platforms.redshift as redshift_module

            if redshift_module.redshift_connector:
                with (
                    patch.object(
                        redshift_module.redshift_connector, "connect", side_effect=Exception("Connection failed")
                    ),
                    pytest.raises(Exception, match="Connection failed"),
                ):
                    adapter.create_connection()
            elif redshift_module.psycopg:
                with patch.object(redshift_module.psycopg, "connect", side_effect=Exception("Connection failed")):
                    with pytest.raises(Exception, match="Connection failed"):
                        adapter.create_connection()
            else:
                pytest.skip("Neither redshift_connector nor psycopg available")

    def test_create_schema(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        mock_benchmark = Mock()
        mock_benchmark.get_create_tables_sql.return_value = """
            CREATE TABLE table1 (id INTEGER, name VARCHAR(100)) DISTKEY(id) SORTKEY(id);
            CREATE TABLE table2 (id INTEGER, data TEXT) DISTSTYLE EVEN;
        """

        with patch.object(adapter, "translate_sql") as mock_translate:
            mock_translate.return_value = "CREATE TABLE table1 (id INTEGER, name VARCHAR(100)) DISTKEY(id) SORTKEY(id);\nCREATE TABLE table2 (id INTEGER, data TEXT) DISTSTYLE EVEN;"

            schema_time = adapter.create_schema(mock_benchmark, mock_connection)

        assert isinstance(schema_time, float)
        assert schema_time >= 0

        assert mock_cursor.execute.call_count >= 2
        mock_cursor.close.assert_called_once()

    def test_load_data_with_copy_command(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        mock_cursor.fetchone.return_value = (100,)

        mock_benchmark = Mock()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
            f.write("1,test1\n2,test2\n")
            temp_path = Path(f.name)

        try:
            mock_benchmark.tables = {"test_table": str(temp_path)}

            adapter.s3_bucket = None

            table_stats, load_time, _ = adapter.load_data(mock_benchmark, mock_connection, Path("/tmp"))

            assert isinstance(table_stats, dict)
            assert isinstance(load_time, float)
            assert load_time >= 0
            assert "test_table" in table_stats
            assert table_stats["test_table"] == 2

            execute_calls = [str(call) for call in mock_cursor.execute.call_args_list]
            assert any("INSERT INTO test_table" in call for call in execute_calls)

        finally:
            temp_path.unlink()

    def test_external_table_mode_generates_spectrum_sql(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
                s3_bucket="benchbox-test-bucket",
                s3_prefix="benchbox",
                iam_role="arn:aws:iam::123456789012:role/benchbox-redshift",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        assert adapter.supports_external_tables is True

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (42,)

        mock_benchmark = Mock()
        mock_benchmark.get_schema.return_value = {
            "orders": {
                "columns": [
                    {"name": "o_orderkey", "type": "BIGINT"},
                    {"name": "o_totalprice", "type": "DECIMAL(15,2)"},
                ]
            }
        }

        with tempfile.NamedTemporaryFile(mode="wb", suffix=".parquet", delete=False) as f:
            f.write(b"PAR1")
            parquet_path = Path(f.name)

        mock_benchmark.tables = {"orders": [parquet_path]}

        mock_s3_client = Mock()
        try:
            with patch.object(adapter, "_create_s3_client", return_value=mock_s3_client):
                table_stats, load_time, _ = adapter.create_external_tables(
                    mock_benchmark, mock_connection, Path("/tmp")
                )

            assert table_stats["orders"] == 42
            assert load_time >= 0
            mock_s3_client.upload_file.assert_called_once()

            execute_calls = [str(call) for call in mock_cursor.execute.call_args_list]
            assert any("CREATE EXTERNAL SCHEMA IF NOT EXISTS" in call for call in execute_calls)
            assert any("CREATE EXTERNAL TABLE" in call for call in execute_calls)
            assert any("STORED AS PARQUET" in call for call in execute_calls)
            assert any(
                "LOCATION 's3://benchbox-test-bucket/benchbox/test_db_external/orders/" in call
                for call in execute_calls
            )
        finally:
            parquet_path.unlink()

    def test_external_table_mode_generates_delta_spectrum_sql(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
                s3_bucket="benchbox-test-bucket",
                s3_prefix="benchbox",
                iam_role="arn:aws:iam::123456789012:role/benchbox-redshift",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (9,)

        mock_benchmark = Mock()
        mock_benchmark.get_schema.return_value = {"orders": {"columns": [{"name": "o_orderkey", "type": "BIGINT"}]}}

        with tempfile.TemporaryDirectory() as tmpdir:
            delta_dir = Path(tmpdir) / "orders"
            (delta_dir / "_delta_log").mkdir(parents=True)
            (delta_dir / "_delta_log" / "00000000000000000000.json").write_text("{}")
            (delta_dir / "part-000.parquet").write_bytes(b"PAR1")
            mock_benchmark.tables = {"orders": [delta_dir]}

            with patch.object(adapter, "_create_s3_client", return_value=Mock()):
                table_stats, _, _ = adapter.create_external_tables(mock_benchmark, mock_connection, Path(tmpdir))

        assert table_stats["orders"] == 9
        execute_calls = [str(call) for call in mock_cursor.execute.call_args_list]
        assert any("TABLE PROPERTIES ('table_type'='DELTA')" in call for call in execute_calls)

    def test_external_table_mode_registers_iceberg_in_glue(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
                s3_bucket="benchbox-test-bucket",
                s3_prefix="benchbox",
                iam_role="arn:aws:iam::123456789012:role/benchbox-redshift",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (7,)

        mock_benchmark = Mock()
        mock_benchmark.get_schema.return_value = {"orders": {"columns": [{"name": "o_orderkey", "type": "BIGINT"}]}}

        mock_glue = Mock()

        with tempfile.TemporaryDirectory() as tmpdir:
            iceberg_dir = Path(tmpdir) / "orders"
            _make_real_iceberg_table(iceberg_dir)
            mock_benchmark.tables = {"orders": [iceberg_dir]}

            mock_s3 = Mock()
            with (
                patch.object(adapter, "_create_s3_client", return_value=mock_s3),
                patch.object(adapter, "_create_glue_client", return_value=mock_glue),
            ):
                table_stats, _, _ = adapter.create_external_tables(mock_benchmark, mock_connection, Path(tmpdir))

        assert table_stats["orders"] == 7
        execute_calls = [str(call) for call in mock_cursor.execute.call_args_list]
        assert not any("CREATE EXTERNAL TABLE" in call for call in execute_calls)
        assert any("SELECT COUNT(*)" in call for call in execute_calls)
        mock_glue.create_table.assert_called_once()
        table_input = mock_glue.create_table.call_args[1]["TableInput"]
        assert mock_glue.create_table.call_args[1]["DatabaseName"] == "test_db_external"
        assert table_input["Parameters"]["table_type"] == "iceberg"
        assert table_input["StorageDescriptor"]["Location"].startswith("s3://benchbox-test-bucket/")
        assert table_input["StorageDescriptor"]["Location"].endswith("/orders/")
        assert table_input["StorageDescriptor"]["Columns"] == [{"Name": "o_orderkey", "Type": "BIGINT"}]
        metadata_location = table_input["Parameters"]["metadata_location"]
        assert metadata_location.startswith(table_input["StorageDescriptor"]["Location"] + "metadata/")
        assert metadata_location.endswith(".metadata.json")
        uploaded_keys = {call.args[2] for call in mock_s3.upload_file.call_args_list}
        assert any(key.endswith(".parquet") for key in uploaded_keys)
        assert any(key.endswith(".avro") for key in uploaded_keys)
        assert metadata_location[len("s3://benchbox-test-bucket/") :] in uploaded_keys

    def test_iceberg_glue_registration_replaces_existing_table(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
                s3_bucket="benchbox-test-bucket",
                s3_prefix="benchbox",
                iam_role="arn:aws:iam::123456789012:role/benchbox-redshift",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        class FakeGlueError(Exception):
            def __init__(self, code):
                super().__init__(code)
                self.response = {"Error": {"Code": code}}

        mock_glue = Mock()
        mock_glue.delete_table.side_effect = FakeGlueError("EntityNotFoundException")
        adapter._register_iceberg_table_in_glue(
            mock_glue, "test_db_external", "orders", "s3://bucket/orders/", [("o_orderkey", "BIGINT")], None
        )
        mock_glue.create_table.assert_called_once()

        mock_glue = Mock()
        mock_glue.delete_table.side_effect = FakeGlueError("AccessDeniedException")
        with pytest.raises(FakeGlueError):
            adapter._register_iceberg_table_in_glue(
                mock_glue, "test_db_external", "orders", "s3://bucket/orders/", [("o_orderkey", "BIGINT")], None
            )
        mock_glue.create_table.assert_not_called()

    def test_glue_column_type_aliases(self):
        assert RedshiftAdapter._map_external_column_type_to_glue("INTEGER") == "INT"
        assert RedshiftAdapter._map_external_column_type_to_glue("DOUBLE PRECISION") == "DOUBLE"
        assert RedshiftAdapter._map_external_column_type_to_glue("REAL") == "FLOAT"
        assert RedshiftAdapter._map_external_column_type_to_glue("BIGINT") == "BIGINT"
        assert RedshiftAdapter._map_external_column_type_to_glue("DECIMAL(15,2)") == "DECIMAL(15,2)"
        assert RedshiftAdapter._map_external_column_type_to_glue("NUMERIC(10,2)") == "DECIMAL(10,2)"
        assert RedshiftAdapter._map_external_column_type_to_glue("TIMESTAMP WITH TIME ZONE") == "TIMESTAMP"
        assert RedshiftAdapter._map_external_column_type_to_glue("TIMESTAMP(6)") == "TIMESTAMP"

    def test_external_table_mode_requires_iam_role(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
                s3_bucket="benchbox-test-bucket",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        with pytest.raises(ValueError, match="requires IAM role credentials"):
            adapter.validate_external_table_requirements()

    def test_load_data_native_path_still_uses_copy_loader(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
                s3_bucket="benchbox-test-bucket",
                iam_role="arn:aws:iam::123456789012:role/benchbox-redshift",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
            f.write("1,test\n")
            csv_path = Path(f.name)

        mock_benchmark = Mock()
        mock_benchmark.tables = {"orders": [csv_path]}

        try:
            with (
                patch.object(adapter, "_create_s3_client", return_value=Mock()),
                patch.object(adapter, "_load_table_via_s3", return_value=7) as mock_copy_loader,
            ):
                table_stats, _, _ = adapter.load_data(mock_benchmark, mock_connection, Path("/tmp"))

            assert table_stats["orders"] == 7
            mock_copy_loader.assert_called_once()
        finally:
            csv_path.unlink()

    def test_load_data_native_path_uses_manifest_files_when_benchmark_tables_are_directories(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
                s3_bucket="benchbox-test-bucket",
                iam_role="arn:aws:iam::123456789012:role/benchbox-redshift",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            table_dir = data_dir / "orders"
            delta_log_dir = table_dir / "_delta_log"
            delta_log_dir.mkdir(parents=True)
            (delta_log_dir / "00000000000000000000.json").write_text("{}")
            (table_dir / "part-00000.parquet").write_bytes(b"PAR1")
            delta_dir_size = sum(path.stat().st_size for path in table_dir.rglob("*") if path.is_file())

            tbl_path = data_dir / "orders.tbl"
            tbl_path.write_text("1|2024-01-01|O|1.00|\n")

            manifest = {
                "benchmark": "tpch",
                "scale_factor": 1.0,
                "format_preference": ["delta", "tbl"],
                "tables": {
                    "orders": {
                        "formats": {
                            "delta": [
                                {
                                    "path": "orders",
                                    "size_bytes": delta_dir_size,
                                    "row_count": 1,
                                    "is_directory": True,
                                }
                            ],
                            "tbl": [
                                {
                                    "path": "orders.tbl",
                                    "size_bytes": tbl_path.stat().st_size,
                                    "row_count": 1,
                                }
                            ],
                        }
                    }
                },
            }
            (data_dir / "_datagen_manifest.json").write_text(json.dumps(manifest))

            mock_benchmark = Mock()
            mock_benchmark.tables = {"orders": [table_dir]}

            with (
                patch.object(adapter, "_create_s3_client", return_value=Mock()),
                patch.object(adapter, "_load_table_via_s3", return_value=7) as mock_copy_loader,
            ):
                table_stats, _, _ = adapter.load_data(mock_benchmark, mock_connection, data_dir)

        assert table_stats["orders"] == 7
        assert mock_copy_loader.call_args.args[2] == "orders"
        assert mock_copy_loader.call_args.args[3] == [tbl_path]

    def test_build_s3_copy_source_uses_manifest_for_multiple_files(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
                s3_bucket="benchbox-test-bucket",
                s3_prefix="benchbox",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        mock_s3_client = Mock()
        s3_uris = [
            "s3://benchbox-test-bucket/benchbox/orders_0.csv",
            "s3://benchbox-test-bucket/benchbox/orders_1.csv",
        ]

        copy_from_path, manifest_option = adapter._build_s3_copy_source(mock_s3_client, s3_uris, "orders")

        assert copy_from_path == "s3://benchbox-test-bucket/benchbox/orders_manifest.json"
        assert manifest_option == "manifest"
        mock_s3_client.put_object.assert_called_once()
        put_kwargs = mock_s3_client.put_object.call_args.kwargs
        assert put_kwargs["Bucket"] == "benchbox-test-bucket"
        assert put_kwargs["Key"] == "benchbox/orders_manifest.json"
        assert json.loads(put_kwargs["Body"]) == {"entries": [{"url": uri, "mandatory": True} for uri in s3_uris]}

    def test_get_copy_credentials_clause_prefers_role_then_keys_then_default(self):
        try:
            role_adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
                iam_role="arn:aws:iam::123456789012:role/benchbox-redshift",
                aws_access_key_id="AKIA123",
                aws_secret_access_key="secret123",
            )
            key_adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
                aws_access_key_id="AKIA123",
                aws_secret_access_key="secret123",
            )
            default_adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        assert (
            role_adapter._get_copy_credentials_clause() == "IAM_ROLE 'arn:aws:iam::123456789012:role/benchbox-redshift'"
        )
        assert key_adapter._get_copy_credentials_clause() == "ACCESS_KEY_ID 'AKIA123' SECRET_ACCESS_KEY 'secret123'"
        assert default_adapter._get_copy_credentials_clause() == ""

    @pytest.mark.parametrize(
        "kwargs, match_name",
        [
            ({"iam_role": "arn:role/legit'; DROP TABLE users--"}, "iam_role"),
            (
                {"aws_access_key_id": "AKID'BAD", "aws_secret_access_key": "good"},
                "aws_access_key_id",
            ),
            (
                {"aws_access_key_id": "AKID", "aws_secret_access_key": "sec'ret"},
                "aws_secret_access_key",
            ),
            (
                {
                    "aws_access_key_id": "AKID",
                    "aws_secret_access_key": "secret",
                    "aws_session_token": "tok'en",
                },
                "aws_session_token",
            ),
        ],
        ids=["iam_role", "access_key_id", "secret_access_key", "session_token"],
    )
    def test_rejects_credential_with_single_quote(self, kwargs, match_name):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
                **kwargs,
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        with pytest.raises(ValueError, match="single-quote"):
            adapter._get_copy_credentials_clause()

    def test_load_table_via_s3_with_tbl_zst_from_preference_chain(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
                schema="public",
                s3_bucket="benchbox-bucket",
                s3_prefix="data",
                iam_role="arn:aws:iam::123456789012:role/redshift-role",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = (150,)
        mock_connection = Mock()
        mock_s3_client = Mock()

        with tempfile.NamedTemporaryFile(mode="wb", suffix=".tbl.zst", delete=False) as f:
            f.write(b"\x28\xb5\x2f\xfd")
            tbl_zst_file = Path(f.name)

        try:
            with patch.object(
                adapter,
                "_upload_file_to_s3",
                return_value="s3://benchbox-bucket/data/customer_0.tbl.zst",
            ):
                row_count = adapter._load_table_via_s3(
                    mock_cursor,
                    mock_s3_client,
                    "customer",
                    [tbl_zst_file],
                    mock_connection,
                )
        finally:
            tbl_zst_file.unlink()

        assert row_count == 150
        copy_sql = str(mock_cursor.execute.call_args_list[0].args[0])
        assert "COPY public.customer" in copy_sql
        assert "ZSTD" in copy_sql
        assert "DELIMITER '|'" in copy_sql
        assert "FORMAT AS PARQUET" not in copy_sql

    def test_load_table_via_s3_builds_copy_sql_with_manifest_and_gzip(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
                schema="analytics",
                s3_bucket="benchbox-test-bucket",
                s3_prefix="benchbox",
                iam_role="arn:aws:iam::123456789012:role/benchbox-redshift",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = (17,)
        mock_connection = Mock()
        mock_s3_client = Mock()

        with (
            tempfile.NamedTemporaryFile(mode="wb", suffix=".csv.gz", delete=False) as f1,
            tempfile.NamedTemporaryFile(mode="wb", suffix=".csv.gz", delete=False) as f2,
        ):
            f1.write(b"id,name\n1,one\n")
            f2.write(b"id,name\n2,two\n")
            file_one = Path(f1.name)
            file_two = Path(f2.name)

        try:
            with patch.object(
                adapter,
                "_upload_file_to_s3",
                side_effect=[
                    "s3://benchbox-test-bucket/benchbox/orders_0.csv.gz",
                    "s3://benchbox-test-bucket/benchbox/orders_1.csv.gz",
                ],
            ):
                row_count = adapter._load_table_via_s3(
                    mock_cursor,
                    mock_s3_client,
                    "orders",
                    [file_one, file_two],
                    mock_connection,
                )
        finally:
            file_one.unlink()
            file_two.unlink()

        assert row_count == 17
        copy_sql = str(mock_cursor.execute.call_args_list[0].args[0])
        assert "COPY analytics.orders" in copy_sql
        assert "FROM 's3://benchbox-test-bucket/benchbox/orders_manifest.json'" in copy_sql
        assert "IAM_ROLE 'arn:aws:iam::123456789012:role/benchbox-redshift'" in copy_sql
        assert "manifest" in copy_sql
        assert "GZIP" in copy_sql
        assert "DELIMITER ','" in copy_sql
        assert "COMPUPDATE PRESET" in copy_sql
        assert mock_cursor.execute.call_args_list[1].args[0] == "SELECT COUNT(*) FROM analytics.orders"
        assert mock_cursor.execute.call_args_list[2].args[0] == "ANALYZE analytics.orders"
        mock_s3_client.put_object.assert_called_once()

    def test_configure_for_benchmark_olap(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
                strict_validation=False,
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [("public", "table1"), ("public", "table2")]
        mock_cursor.fetchone.return_value = ("off",)

        maint_conn = Mock()
        maint_conn.autocommit = True
        maint_conn.cursor.return_value = Mock()
        with patch.object(adapter, "_connect_with_driver", return_value=maint_conn):
            adapter.configure_for_benchmark(mock_connection, "olap")

        execute_calls = [str(call) for call in mock_cursor.execute.call_args_list]
        assert any("enable_result_cache_for_session" in call for call in execute_calls)
        assert any("query_group" in call for call in execute_calls)
        assert any("enable_case_sensitive_identifier" in call for call in execute_calls)

    def test_configure_for_benchmark_with_wlm(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
                wlm_query_slot_count=8,
                strict_validation=False,
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [("public", "table1"), ("public", "table2")]
        mock_cursor.fetchone.return_value = ("off",)

        maint_conn = Mock()
        maint_conn.autocommit = True
        maint_conn.cursor.return_value = Mock()
        with patch.object(adapter, "_connect_with_driver", return_value=maint_conn):
            adapter.configure_for_benchmark(mock_connection, "tpch")

        execute_calls = [str(call) for call in mock_cursor.execute.call_args_list]
        assert any("query_group" in call for call in execute_calls)
        assert any("statement_timeout" in call for call in execute_calls)

    def test_execute_query_uses_core_cursor_mixin(self):
        from benchbox.core.benchmark_mixins import CursorValidationQueryExecutionMixin

        assert issubclass(RedshiftAdapter, CursorValidationQueryExecutionMixin)

    def test_execute_query_success(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [(1, "test"), (2, "test2")]

        with patch.object(adapter, "_get_query_statistics") as mock_stats:
            mock_stats.return_value = {"query_id": "redshift_query_123"}

            result = adapter.execute_query(mock_connection, "SELECT * FROM test", "q1")

        assert result["query_id"] == "q1"
        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 2
        assert result["first_row"] == (1, "test")
        assert isinstance(result["execution_time_seconds"], float)
        assert result["query_statistics"]["query_id"] == "redshift_query_123"
        assert "execution_time_seconds" in result["query_statistics"]
        assert isinstance(result["query_statistics"]["execution_time_seconds"], float)

        mock_cursor.execute.assert_called_with("SELECT * FROM test")
        mock_cursor.close.assert_called_once()

    def test_execute_query_failure(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = Exception("Query failed")

        result = adapter.execute_query(mock_connection, "INVALID SQL", "q1")

        assert result["query_id"] == "q1"
        assert result["status"] == "FAILED"
        assert result["rows_returned"] == 0
        assert result["error"] == "Query failed"
        assert result["error_type"] == "Exception"
        assert isinstance(result["execution_time_seconds"], float)

        mock_cursor.close.assert_called_once()

    def test_get_query_statistics(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = [
            "query_123",
            1000,
            2000,
            1024000,
            512000,
            4,
            8,
            True,
        ]

        stats = adapter._get_query_statistics(mock_connection, "q1")

        assert stats["query_id"] == "query_123"
        assert stats["duration_microsecs"] == 1000
        assert stats["cpu_time_microsecs"] == 2000
        assert stats["bytes_scanned"] == 1024000
        assert stats["bytes_returned"] == 512000
        assert stats["slots"] == 4
        assert stats["wlm_slots"] == 8
        assert stats["aborted"] is True

        mock_cursor.close.assert_called_once()

    def test_get_platform_metadata(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        mock_cursor.fetchone.side_effect = [
            ["PostgreSQL 8.0.2 on i686-pc-linux-gnu, compiled by GCC gcc (GCC) 3.4.2 20041017, Redshift 1.0.0"],
            ["dc2.large", 2, "1.0", True],
            ["test_user", "test_db", "public", "192.168.1.1", 5432],
        ]

        mock_cursor.fetchall.return_value = [
            ("public", "table1", "test_user", None, False, False, False),
            ("public", "table2", "test_user", None, False, False, False),
        ]

        metadata = adapter._get_platform_metadata(mock_connection)

        assert metadata["platform"] == "Redshift"
        assert metadata["host"] == "test-cluster.redshift.amazonaws.com"
        assert metadata["database"] == "test_db"
        assert "redshift_version" in metadata
        assert "cluster_info" in metadata
        assert "session_info" in metadata
        assert "tables" in metadata

        mock_cursor.close.assert_called()

    def test_analyze_table(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        adapter.analyze_table(mock_connection, "test_table")

        mock_cursor.execute.assert_called_once_with("ANALYZE test_table")
        mock_cursor.close.assert_called_once()

    def test_vacuum_table(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        adapter.vacuum_table(mock_connection, "test_table")

        mock_cursor.execute.assert_called_once_with("VACUUM test_table")
        mock_cursor.close.assert_called_once()

    def test_close_connection(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        mock_connection = Mock()

        adapter.close_connection(mock_connection)

        mock_connection.close.assert_called_once()

    def test_supports_tuning_type(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        with patch("benchbox.core.tuning.interface.TuningType") as mock_tuning_type:
            mock_tuning_type.DISTRIBUTION = "distribution"
            mock_tuning_type.SORTING = "sorting"
            mock_tuning_type.CLUSTERING = "clustering"

            assert adapter.supports_tuning_type(mock_tuning_type.DISTRIBUTION) is True
            assert adapter.supports_tuning_type(mock_tuning_type.SORTING) is True
            assert adapter.supports_tuning_type(mock_tuning_type.CLUSTERING) is False

    def test_generate_tuning_clause_with_distribution(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = True

        mock_column = Mock()
        mock_column.name = "dist_key"
        mock_column.order = 1

        with patch("benchbox.core.tuning.interface.TuningType") as mock_tuning_type:
            mock_tuning_type.DISTRIBUTION = "distribution"
            mock_tuning_type.SORTING = "sorting"

            def mock_get_columns_by_type(tuning_type):
                if tuning_type == mock_tuning_type.DISTRIBUTION:
                    return [mock_column]
                return []

            mock_tuning.get_columns_by_type.side_effect = mock_get_columns_by_type

            clause = adapter.generate_tuning_clause(mock_tuning)

            assert "DISTKEY (dist_key)" in clause

    def test_generate_tuning_clause_with_sorting(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = True

        mock_column1 = Mock()
        mock_column1.name = "sort_key1"
        mock_column1.order = 1
        mock_column2 = Mock()
        mock_column2.name = "sort_key2"
        mock_column2.order = 2

        with patch("benchbox.core.tuning.interface.TuningType") as mock_tuning_type:
            mock_tuning_type.DISTRIBUTION = "distribution"
            mock_tuning_type.SORTING = "sorting"

            def mock_get_columns_by_type(tuning_type):
                if tuning_type == mock_tuning_type.SORTING:
                    return [mock_column1, mock_column2]
                return []

            mock_tuning.get_columns_by_type.side_effect = mock_get_columns_by_type

            clause = adapter.generate_tuning_clause(mock_tuning)

            assert "SORTKEY (sort_key1, sort_key2)" in clause

    def test_generate_tuning_clause_with_diststyle(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = True

        with patch("benchbox.core.tuning.interface.TuningType") as mock_tuning_type:
            mock_tuning_type.DISTRIBUTION = "distribution"
            mock_tuning_type.SORTING = "sorting"

            def mock_get_columns_by_type(tuning_type):
                return []

            mock_tuning.get_columns_by_type.side_effect = mock_get_columns_by_type

            clause = adapter.generate_tuning_clause(mock_tuning)

            assert "DISTSTYLE EVEN" in clause

    @pytest.mark.parametrize(
        ("column_type", "expected"),
        [
            ("DECIMAL(15,2)", "DECIMAL(15,2)"),
            ("varchar(128)", "VARCHAR(128)"),
            ("bigint", "BIGINT"),
            ("smallint", "SMALLINT"),
            ("int", "INTEGER"),
            ("double", "DOUBLE PRECISION"),
            ("float", "REAL"),
            ("date", "DATE"),
            ("bool", "BOOLEAN"),
            ("GEOMETRY", "VARCHAR(65535)"),
            ("", "VARCHAR(65535)"),
        ],
    )
    def test_map_external_column_type(self, column_type, expected):
        assert RedshiftAdapter._map_external_column_type(column_type) == expected

    def test_generate_tuning_clause_none(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        clause = adapter.generate_tuning_clause(None)
        assert clause == ""

    def test_apply_table_tunings_with_distribution_and_sorting(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor

        mock_cursor.fetchone.return_value = (
            "public",
            "test_table",
            "KEY",
            "dist_key",
            "sort_key",
            None,
            None,
            None,
        )

        mock_tuning = Mock()
        mock_tuning.table_name = "test_table"
        mock_tuning.has_any_tuning.return_value = True

        mock_dist_column = Mock()
        mock_dist_column.name = "dist_key"
        mock_dist_column.order = 1

        mock_sort_column = Mock()
        mock_sort_column.name = "sort_key"
        mock_sort_column.order = 1

        with patch("benchbox.core.tuning.interface.TuningType") as mock_tuning_type:
            mock_tuning_type.DISTRIBUTION = "distribution"
            mock_tuning_type.SORTING = "sorting"
            mock_tuning_type.CLUSTERING = "clustering"
            mock_tuning_type.PARTITIONING = "partitioning"

            def mock_get_columns_by_type(tuning_type):
                if tuning_type == mock_tuning_type.DISTRIBUTION:
                    return [mock_dist_column]
                elif tuning_type == mock_tuning_type.SORTING:
                    return [mock_sort_column]
                return []

            mock_tuning.get_columns_by_type.side_effect = mock_get_columns_by_type

            adapter.apply_table_tunings(mock_tuning, mock_connection)

    def test_apply_unified_tuning(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        mock_connection = Mock()
        mock_unified_config = Mock()
        mock_unified_config.primary_keys = Mock()
        mock_unified_config.foreign_keys = Mock()
        mock_unified_config.platform_optimizations = Mock()
        mock_unified_config.table_tunings = {}

        with patch.object(adapter, "apply_constraint_configuration"):
            with patch.object(adapter, "apply_platform_optimizations"):
                adapter.apply_unified_tuning(mock_unified_config, mock_connection)

    def test_apply_constraint_configuration(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        mock_connection = Mock()
        mock_primary_key_config = Mock()
        mock_primary_key_config.enabled = True
        mock_foreign_key_config = Mock()
        mock_foreign_key_config.enabled = False

        adapter.apply_constraint_configuration(mock_primary_key_config, mock_foreign_key_config, mock_connection)

    def test_wlm_configuration_options(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
                wlm_query_slot_count=8,
                wlm_query_queue_name="benchmark_queue",
                statement_timeout=600000,
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        assert adapter.wlm_query_slot_count == 8
        assert adapter.wlm_query_queue_name == "benchmark_queue"
        assert adapter.statement_timeout == 600000

    def test_ssl_configuration_options(self):
        try:
            adapter_require = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
                sslmode="require",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        assert adapter_require.sslmode == "require"

        try:
            adapter_verify = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
                sslmode="verify-full",
                sslrootcert="/path/to/cert.pem",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        assert adapter_verify.sslmode == "verify-full"
        assert adapter_verify.sslrootcert == "/path/to/cert.pem"

    def test_shared_connector_helper_omits_prefer_sslmode_for_redshift_connector(self):
        import benchbox.platforms.redshift as redshift_module

        mock_connector = Mock()
        mock_connect = Mock()
        mock_connector.connect = mock_connect

        adapter = RedshiftAdapter(
            host="test-cluster.redshift.amazonaws.com",
            database="test_db",
            username="test_user",
            password="test_pass",
            sslmode="prefer",
        )

        with patch.object(redshift_module, "redshift_connector", mock_connector):
            adapter._connect_with_driver(application_name="BenchBox-Validation", connect_timeout=15)

        call_kwargs = mock_connect.call_args.kwargs
        assert call_kwargs["ssl"] is True
        assert call_kwargs["timeout"] == 15
        assert "sslmode" not in call_kwargs

    def test_shared_connector_helper_passes_supported_sslmode_for_redshift_connector(self):
        import benchbox.platforms.redshift as redshift_module

        mock_connector = Mock()
        mock_connect = Mock()
        mock_connector.connect = mock_connect

        adapter = RedshiftAdapter(
            host="test-cluster.redshift.amazonaws.com",
            database="test_db",
            username="test_user",
            password="test_pass",
            sslmode="verify-full",
        )

        with patch.object(redshift_module, "redshift_connector", mock_connector):
            adapter._connect_with_driver(application_name="BenchBox-Validation", connect_timeout=15)

        call_kwargs = mock_connect.call_args.kwargs
        assert call_kwargs["sslmode"] == "verify-full"

    def test_file_format_detection_for_chunked_files(self):
        from pathlib import Path

        test_cases = [
            (Path("customer.tbl"), True, "Simple .tbl file"),
            (Path("customer.tbl.1"), True, "Chunked .tbl file"),
            (Path("lineitem.tbl.10"), True, "Chunked .tbl file (2 digits)"),
            (Path("orders.tbl.gz"), True, "Compressed .tbl file"),
            (Path("part.tbl.1.gz"), True, "Chunked compressed .tbl file"),
            (Path("nation.tbl.zst"), True, "zstd compressed .tbl file"),
            (Path("partsupp.dat"), True, "TPC-DS .dat file"),
            (Path("partsupp.dat.1"), True, "Chunked TPC-DS .dat file"),
            (Path("region.csv"), False, "CSV file"),
            (Path("supplier.csv.gz"), False, "Compressed CSV file"),
        ]

        for file_path, should_be_tbl, description in test_cases:
            file_str = str(file_path.name)
            is_tbl = ".tbl" in file_str or ".dat" in file_str
            assert is_tbl == should_be_tbl, f"Failed for {description}: {file_path.name}"

    def test_compupdate_validation_valid_values(self):
        valid_values = ["ON", "OFF", "PRESET", "on", "off", "preset", "On", "Off", "Preset"]

        for value in valid_values:
            try:
                adapter = RedshiftAdapter(
                    host="test-cluster.redshift.amazonaws.com",
                    database="test_db",
                    username="test_user",
                    password="test_pass",
                    compupdate=value,
                )
            except ImportError:
                pytest.skip("Redshift drivers not installed")

            assert adapter.compupdate == value.upper(), f"Failed for value: {value}"

    def test_compupdate_validation_invalid_values(self):
        invalid_values = ["ALWAYS", "NEVER", "AUTO", "TRUE", "FALSE", "1", "0", "INVALID"]

        for value in invalid_values:
            with pytest.raises(ValueError, match=r"Invalid COMPUPDATE value"):
                try:
                    RedshiftAdapter(
                        host="test-cluster.redshift.amazonaws.com",
                        database="test_db",
                        username="test_user",
                        password="test_pass",
                        compupdate=value,
                    )
                except ImportError:
                    pytest.skip("Redshift drivers not installed")

    def test_compupdate_default_value(self):
        try:
            adapter = RedshiftAdapter(
                host="test-cluster.redshift.amazonaws.com",
                database="test_db",
                username="test_user",
                password="test_pass",
            )
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        assert adapter.compupdate == "PRESET"

    def test_from_config_passes_all_parameters(self):
        config = {
            "host": "test-cluster.redshift.amazonaws.com",
            "port": 5439,
            "database": "test_db",
            "username": "test_user",
            "password": "test_pass",
            "schema": "test_schema",
            "benchmark": "tpch",
            "scale_factor": 1.0,
            "iam_role": "arn:aws:iam::123456789012:role/RedshiftCopyRole",
            "s3_bucket": "test-bucket",
            "s3_prefix": "test-prefix",
            "aws_access_key_id": "AKIAIOSFODNN7EXAMPLE",
            "aws_secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "aws_region": "us-west-2",
            "cluster_identifier": "test-cluster",
            "admin_database": "admin_db",
            "connect_timeout": 30,
            "statement_timeout": 60000,
            "sslmode": "require",
            "ssl_enabled": True,
            "ssl_insecure": False,
            "sslrootcert": "/path/to/cert.pem",
            "wlm_query_slot_count": 4,
            "wlm_query_queue_name": "test_queue",
            "wlm_config": {"queue": "test"},
            "compupdate": "ON",
            "auto_vacuum": False,
            "auto_analyze": False,
        }

        try:
            adapter = RedshiftAdapter.from_config(config)
        except ImportError:
            pytest.skip("Redshift drivers not installed")

        assert adapter.host == "test-cluster.redshift.amazonaws.com"
        assert adapter.port == 5439
        assert adapter.username == "test_user"
        assert adapter.password == "test_pass"
        assert adapter.schema == "test_schema"

        assert adapter.iam_role == "arn:aws:iam::123456789012:role/RedshiftCopyRole"
        assert adapter.s3_bucket == "test-bucket"
        assert adapter.s3_prefix == "test-prefix"
        assert adapter.aws_access_key_id == "AKIAIOSFODNN7EXAMPLE"
        assert adapter.aws_secret_access_key == "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
        assert adapter.aws_region == "us-west-2"

        assert adapter.cluster_identifier == "test-cluster"
        assert adapter.admin_database == "admin_db"
        assert adapter.connect_timeout == 30
        assert adapter.statement_timeout == 60000
        assert adapter.sslmode == "require"

        assert adapter.ssl_enabled is True
        assert adapter.ssl_insecure is False
        assert adapter.sslrootcert == "/path/to/cert.pem"

        assert adapter.wlm_query_slot_count == 4
        assert adapter.wlm_query_queue_name == "test_queue"
        assert adapter.workload_management_config == {"queue": "test"}

        assert adapter.compupdate == "ON"
        assert adapter.auto_vacuum is False
        assert adapter.auto_analyze is False


def _make_adapter(**kwargs):
    try:
        from benchbox.platforms.redshift import RedshiftAdapter
    except ImportError:
        pytest.skip("Redshift drivers not installed")

    defaults = {
        "host": "test-cluster.redshift.amazonaws.com",
        "database": "test_db",
        "username": "test_user",
        "password": "test_pass",
        "strict_validation": False,
    }
    defaults.update(kwargs)
    return RedshiftAdapter(**defaults)


class TestDeploymentTypeDetection:
    def test_detect_serverless_hostname(self):
        adapter = _make_adapter()
        result = adapter._detect_deployment_type("my-workgroup.123456789.us-east-1.redshift-serverless.amazonaws.com")
        assert result == "serverless"

    def test_detect_provisioned_hostname(self):
        adapter = _make_adapter()
        result = adapter._detect_deployment_type("my-cluster.us-west-2.redshift.amazonaws.com")
        assert result == "provisioned"

    def test_detect_unknown_hostname(self):
        adapter = _make_adapter()
        result = adapter._detect_deployment_type("some-database.example.com")
        assert result == "unknown"

    def test_detect_empty_hostname(self):
        adapter = _make_adapter()
        result = adapter._detect_deployment_type("")
        assert result == "unknown"

    def test_detect_none_hostname(self):
        adapter = _make_adapter()
        result = adapter._detect_deployment_type(None)
        assert result == "unknown"

    def test_extract_region_serverless(self):
        adapter = _make_adapter()
        region = adapter._extract_region_from_hostname(
            "my-workgroup.123456789.us-west-2.redshift-serverless.amazonaws.com", "serverless"
        )
        assert region == "us-west-2"

    def test_extract_region_provisioned(self):
        adapter = _make_adapter()
        region = adapter._extract_region_from_hostname("my-cluster.eu-central-1.redshift.amazonaws.com", "provisioned")
        assert region == "eu-central-1"

    def test_extract_region_unknown_returns_none(self):
        adapter = _make_adapter()
        region = adapter._extract_region_from_hostname("some-host.example.com", "unknown")
        assert region is None

    def test_extract_region_empty_hostname_returns_none(self):
        adapter = _make_adapter()
        region = adapter._extract_region_from_hostname("", "serverless")
        assert region is None

    def test_extract_region_none_hostname_returns_none(self):
        adapter = _make_adapter()
        region = adapter._extract_region_from_hostname(None, "provisioned")
        assert region is None

    def test_extract_identifier_serverless(self):
        adapter = _make_adapter()
        identifier = adapter._extract_identifier_from_hostname(
            "my-workgroup.123456789.us-east-1.redshift-serverless.amazonaws.com", "serverless"
        )
        assert identifier == "my-workgroup"

    def test_extract_identifier_provisioned(self):
        adapter = _make_adapter()
        identifier = adapter._extract_identifier_from_hostname(
            "my-cluster.us-west-2.redshift.amazonaws.com", "provisioned"
        )
        assert identifier == "my-cluster"

    def test_extract_identifier_unknown_returns_none(self):
        adapter = _make_adapter()
        identifier = adapter._extract_identifier_from_hostname("some-host.example.com", "unknown")
        assert identifier is None

    def test_extract_identifier_empty_hostname_returns_none(self):
        adapter = _make_adapter()
        identifier = adapter._extract_identifier_from_hostname("", "serverless")
        assert identifier is None

    def test_extract_identifier_none_hostname_returns_none(self):
        adapter = _make_adapter()
        identifier = adapter._extract_identifier_from_hostname(None, "provisioned")
        assert identifier is None


class TestCopySqlGeneration:
    def test_copy_sql_single_file_no_compression(self):
        import tempfile

        adapter = _make_adapter(
            schema="analytics",
            s3_bucket="my-bucket",
            s3_prefix="data",
            iam_role="arn:aws:iam::111111111111:role/RedshiftCopy",
            compupdate="OFF",
            auto_analyze=False,
        )

        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = (50,)
        mock_connection = Mock()
        mock_s3_client = Mock()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
            f.write("1,alpha\n2,beta\n")
            csv_path = Path(f.name)

        try:
            with patch.object(
                adapter,
                "_upload_file_to_s3",
                return_value="s3://my-bucket/data/orders_0.csv",
            ):
                row_count = adapter._load_table_via_s3(
                    mock_cursor, mock_s3_client, "orders", [csv_path], mock_connection
                )
        finally:
            csv_path.unlink()

        assert row_count == 50
        copy_sql = mock_cursor.execute.call_args_list[0].args[0]
        assert "COPY analytics.orders" in copy_sql
        assert "FROM 's3://my-bucket/data/orders_0.csv'" in copy_sql
        assert "IAM_ROLE 'arn:aws:iam::111111111111:role/RedshiftCopy'" in copy_sql
        assert "COMPUPDATE OFF" in copy_sql
        assert "DELIMITER ','" in copy_sql
        assert "manifest" not in copy_sql.lower().split("from")[0]
        assert "GZIP" not in copy_sql
        assert "ZSTD" not in copy_sql
        analyze_calls = [str(c) for c in mock_cursor.execute.call_args_list if "ANALYZE" in str(c)]
        assert len(analyze_calls) == 0

    def test_copy_sql_zstd_compression(self):
        import tempfile

        adapter = _make_adapter(
            schema="public",
            s3_bucket="my-bucket",
            s3_prefix="staging",
            iam_role="arn:aws:iam::222222222222:role/Role",
            auto_analyze=True,
        )

        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = (99,)
        mock_connection = Mock()
        mock_s3_client = Mock()

        with tempfile.NamedTemporaryFile(mode="wb", suffix=".csv.zst", delete=False) as f:
            f.write(b"dummy")
            zst_path = Path(f.name)

        try:
            with patch.object(
                adapter,
                "_upload_file_to_s3",
                return_value="s3://my-bucket/staging/lineitem_0.csv.zst",
            ):
                row_count = adapter._load_table_via_s3(
                    mock_cursor, mock_s3_client, "lineitem", [zst_path], mock_connection
                )
        finally:
            zst_path.unlink()

        assert row_count == 99
        copy_sql = mock_cursor.execute.call_args_list[0].args[0]
        assert "COPY public.lineitem" in copy_sql
        assert "ZSTD" in copy_sql
        analyze_sql = mock_cursor.execute.call_args_list[2].args[0]
        assert analyze_sql == "ANALYZE public.lineitem"

    def test_copy_sql_parquet_omits_delimiter_and_compupdate(self):
        import tempfile

        adapter = _make_adapter(
            schema="analytics",
            s3_bucket="my-bucket",
            s3_prefix="data",
            iam_role="arn:aws:iam::111111111111:role/RedshiftCopy",
            compupdate="OFF",
            auto_analyze=False,
        )

        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = (12,)
        mock_connection = Mock()
        mock_s3_client = Mock()

        with tempfile.NamedTemporaryFile(mode="wb", suffix=".parquet", delete=False) as f:
            f.write(b"PAR1")
            parquet_path = Path(f.name)

        try:
            with patch.object(
                adapter,
                "_upload_file_to_s3",
                return_value="s3://my-bucket/data/orders_0.parquet",
            ):
                row_count = adapter._load_table_via_s3(
                    mock_cursor, mock_s3_client, "orders", [parquet_path], mock_connection
                )
        finally:
            parquet_path.unlink()

        assert row_count == 12
        copy_sql = mock_cursor.execute.call_args_list[0].args[0]
        assert "COPY analytics.orders" in copy_sql
        assert "FORMAT AS PARQUET" in copy_sql
        assert "COMPUPDATE" not in copy_sql
        assert "DELIMITER" not in copy_sql
        assert "GZIP" not in copy_sql
        assert "ZSTD" not in copy_sql

    def test_copy_sql_rejects_mixed_file_formats(self):
        import tempfile

        adapter = _make_adapter(
            schema="analytics",
            s3_bucket="my-bucket",
            s3_prefix="data",
            iam_role="arn:aws:iam::111111111111:role/RedshiftCopy",
        )

        mock_cursor = Mock()
        mock_connection = Mock()
        mock_s3_client = Mock()

        with (
            tempfile.NamedTemporaryFile(mode="wb", suffix=".parquet", delete=False) as parquet_file,
            tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as csv_file,
        ):
            parquet_file.write(b"PAR1")
            csv_file.write("1,alpha\n")
            parquet_path = Path(parquet_file.name)
            csv_path = Path(csv_file.name)

        try:
            with patch.object(adapter, "_upload_file_to_s3") as mock_upload:
                with pytest.raises(ValueError, match="uniform source format"):
                    adapter._load_table_via_s3(
                        mock_cursor,
                        mock_s3_client,
                        "orders",
                        [parquet_path, csv_path],
                        mock_connection,
                    )
        finally:
            parquet_path.unlink()
            csv_path.unlink()

        mock_upload.assert_not_called()

    def test_copy_sql_with_access_key_credentials(self):
        import tempfile

        adapter = _make_adapter(
            schema="public",
            s3_bucket="my-bucket",
            s3_prefix="data",
            aws_access_key_id="AKIAEXAMPLE",
            aws_secret_access_key="secretEXAMPLE",
            auto_analyze=False,
        )

        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = (10,)
        mock_connection = Mock()
        mock_s3_client = Mock()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
            f.write("1,x\n")
            csv_path = Path(f.name)

        try:
            with patch.object(
                adapter,
                "_upload_file_to_s3",
                return_value="s3://my-bucket/data/nation_0.csv",
            ):
                adapter._load_table_via_s3(mock_cursor, mock_s3_client, "nation", [csv_path], mock_connection)
        finally:
            csv_path.unlink()

        copy_sql = mock_cursor.execute.call_args_list[0].args[0]
        assert "ACCESS_KEY_ID 'AKIAEXAMPLE'" in copy_sql
        assert "SECRET_ACCESS_KEY 'secretEXAMPLE'" in copy_sql


class TestS3CopySourceBuilding:
    def test_single_file_returns_direct_uri(self):
        adapter = _make_adapter(s3_bucket="bkt", s3_prefix="pfx")
        mock_s3 = Mock()
        path, option = adapter._build_s3_copy_source(mock_s3, ["s3://bkt/pfx/orders_0.csv"], "orders")
        assert path == "s3://bkt/pfx/orders_0.csv"
        assert option == ""
        mock_s3.put_object.assert_not_called()

    def test_multiple_files_creates_manifest(self):
        adapter = _make_adapter(s3_bucket="bkt", s3_prefix="pfx")
        mock_s3 = Mock()
        uris = ["s3://bkt/pfx/t_0.csv", "s3://bkt/pfx/t_1.csv", "s3://bkt/pfx/t_2.csv"]
        path, option = adapter._build_s3_copy_source(mock_s3, uris, "t")
        assert path == "s3://bkt/pfx/t_manifest.json"
        assert option == "manifest"
        put_kwargs = mock_s3.put_object.call_args.kwargs
        manifest = json.loads(put_kwargs["Body"])
        assert len(manifest["entries"]) == 3
        for i, entry in enumerate(manifest["entries"]):
            assert entry["url"] == uris[i]
            assert entry["mandatory"] is True


class TestCopyCredentialsClause:
    def test_iam_role_takes_precedence_over_keys(self):
        adapter = _make_adapter(
            iam_role="arn:aws:iam::999:role/R",
            aws_access_key_id="AK",
            aws_secret_access_key="SK",
        )
        clause = adapter._get_copy_credentials_clause()
        assert clause == "IAM_ROLE 'arn:aws:iam::999:role/R'"

    def test_access_keys_when_no_iam_role(self):
        adapter = _make_adapter(aws_access_key_id="AK123", aws_secret_access_key="SK456")
        clause = adapter._get_copy_credentials_clause()
        assert clause == "ACCESS_KEY_ID 'AK123' SECRET_ACCESS_KEY 'SK456'"

    def test_temporary_access_keys_include_session_token(self):
        adapter = _make_adapter(
            aws_access_key_id="AK123",
            aws_secret_access_key="SK456",
            aws_session_token="TOKEN789",
        )
        clause = adapter._get_copy_credentials_clause()
        assert clause == "ACCESS_KEY_ID 'AK123' SECRET_ACCESS_KEY 'SK456' SESSION_TOKEN 'TOKEN789'"

    def test_empty_when_no_credentials(self):
        adapter = _make_adapter()
        clause = adapter._get_copy_credentials_clause()
        assert clause == ""


class TestCtasSortSql:
    def test_vacuum_sort_method(self):
        adapter = _make_adapter(schema="analytics")
        mock_col = Mock()
        mock_col.name = "o_orderdate"

        with patch.object(adapter, "resolve_sorted_ingestion_strategy", return_value=("on", "vacuum_sort")):
            result = adapter._build_ctas_sort_sql("orders", [mock_col])

        assert result == "VACUUM SORT ONLY analytics.orders"

    def test_ctas_method_is_disabled(self):
        adapter = _make_adapter(schema="public")
        col1 = Mock()
        col1.name = "l_orderkey"
        col2 = Mock()
        col2.name = "l_linenumber"

        with patch.object(adapter, "resolve_sorted_ingestion_strategy", return_value=("on", "ctas")):
            result = adapter._build_ctas_sort_sql("lineitem", [col1, col2])

        assert result is None

    def test_off_mode_returns_none(self):
        adapter = _make_adapter()
        with patch.object(adapter, "resolve_sorted_ingestion_strategy", return_value=("off", "auto")):
            result = adapter._build_ctas_sort_sql("orders", [Mock()])
        assert result is None

    def test_unsupported_method_raises(self):
        adapter = _make_adapter()
        with patch.object(adapter, "resolve_sorted_ingestion_strategy", return_value=("on", "cluster_by")):
            with pytest.raises(ValueError, match="not supported for Redshift"):
                adapter._build_ctas_sort_sql("orders", [Mock()])


class TestSchemaDdl:
    def _executed(self, ddl):
        adapter = _make_adapter()
        cursor = MagicMock()
        connection = MagicMock()
        connection.cursor.return_value = cursor
        with patch.object(adapter, "_create_schema_with_tuning", return_value=ddl):
            adapter.create_schema(MagicMock(), connection)
        return [call.args[0] for call in cursor.execute.call_args_list]

    def test_adds_diststyle_auto_and_sortkey_auto(self):
        executed = self._executed("CREATE TABLE t (id INT)")
        assert executed[-1] == 'CREATE TABLE "t" ("id" INTEGER) DISTSTYLE AUTO SORTKEY AUTO'

    def test_preserves_existing_diststyle(self):
        statement = self._executed("CREATE TABLE t (id INT) DISTSTYLE ALL")[-1]
        assert "DISTSTYLE ALL" in statement
        assert "DISTSTYLE AUTO" not in statement
        assert "SORTKEY AUTO" in statement

    def test_preserves_existing_sortkey(self):
        statement = self._executed("CREATE TABLE t (id INT) SORTKEY (id)")[-1]
        assert "SORTKEY(id)" in statement.replace('"', "")
        assert "SORTKEY AUTO" not in statement
        assert "DISTSTYLE AUTO" in statement

    def test_non_create_statement_unchanged(self):
        assert self._executed("ALTER TABLE t ADD COLUMN x INT")[-1].startswith("ALTER TABLE")

    def test_drop_table_unchanged(self):
        assert "DROP TABLE" in self._executed("DROP TABLE t")[-1]

    def test_create_table_drops_existing_table_first(self):
        executed = self._executed("CREATE TABLE Orders (id INT)")
        assert "DROP TABLE IF EXISTS" in executed[-2]
        assert "orders" in executed[-2]


class TestExternalColumnTypeMapping:
    @pytest.mark.parametrize(
        ("input_type", "expected"),
        [
            ("SUPER", "VARCHAR(65535)"),
            ("GEOMETRY", "VARCHAR(65535)"),
            ("TIMESTAMPTZ", "TIMESTAMPTZ"),
            ("TIMESTAMP WITH TIME ZONE", "TIMESTAMP WITH TIME ZONE"),
            ("NUMERIC(10,4)", "NUMERIC(10,4)"),
            ("CHAR(1)", "CHAR(1)"),
            ("REAL", "REAL"),
            ("FLOAT4", "REAL"),
            ("DOUBLE PRECISION", "DOUBLE PRECISION"),
            ("BOOLEAN", "BOOLEAN"),
            ("BOOL", "BOOLEAN"),
            ("INTEGER", "INTEGER"),
            ("  bigint  ", "BIGINT"),
            ("smallint", "SMALLINT"),
            ("DATE", "DATE"),
            ("   ", "VARCHAR(65535)"),
        ],
    )
    def test_type_mapping(self, input_type, expected):
        assert RedshiftAdapter._map_external_column_type(input_type) == expected


class TestBuildExternalColumnDefinitions:
    def test_generates_column_defs_from_schema(self):
        adapter = _make_adapter()
        mock_benchmark = Mock()
        mock_benchmark.get_schema.return_value = {
            "orders": {
                "columns": [
                    {"name": "O_ORDERKEY", "type": "BIGINT"},
                    {"name": "O_TOTALPRICE", "type": "DECIMAL(15,2)"},
                    {"name": "O_ORDERDATE", "type": "DATE"},
                ]
            }
        }
        result = adapter._build_external_column_definitions(mock_benchmark, "orders")
        assert "o_orderkey BIGINT" in result
        assert "o_totalprice DECIMAL(15,2)" in result
        assert "o_orderdate DATE" in result

    def test_raises_for_missing_schema(self):
        adapter = _make_adapter()
        mock_benchmark = Mock()
        mock_benchmark.get_schema.return_value = {}
        with pytest.raises(ValueError, match="No schema definition found"):
            adapter._build_external_column_definitions(mock_benchmark, "missing_table")

    def test_raises_for_empty_columns(self):
        adapter = _make_adapter()
        mock_benchmark = Mock()
        mock_benchmark.get_schema.return_value = {"orders": {"columns": []}}
        with pytest.raises(ValueError, match="No columns found"):
            adapter._build_external_column_definitions(mock_benchmark, "orders")

    def test_raises_when_benchmark_has_no_get_schema(self):
        adapter = _make_adapter()
        mock_benchmark = Mock(spec=[])
        with pytest.raises(ValueError, match="schema metadata unavailable"):
            adapter._build_external_column_definitions(mock_benchmark, "orders")


class TestValidateExternalTableRequirements:
    def test_missing_s3_bucket_raises(self):
        adapter = _make_adapter()
        adapter.s3_bucket = None
        with pytest.raises(ValueError, match="requires S3 staging"):
            adapter.validate_external_table_requirements()

    def test_missing_iam_role_raises(self):
        adapter = _make_adapter(s3_bucket="my-bucket")
        adapter.iam_role = None
        with pytest.raises(ValueError, match="requires IAM role credentials"):
            adapter.validate_external_table_requirements()

    def test_passes_with_both_configured(self):
        adapter = _make_adapter(
            s3_bucket="my-bucket",
            iam_role="arn:aws:iam::123:role/R",
        )
        adapter.validate_external_table_requirements()


class TestConfigureForBenchmarkSql:
    def _run_configure(self, adapter, benchmark_type):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = []
        mock_cursor.fetchone.return_value = ("off",)

        adapter.configure_for_benchmark(mock_connection, benchmark_type)

        return [call_obj.args[0] for call_obj in mock_cursor.execute.call_args_list]

    def test_olap_settings_include_case_sensitivity_and_datestyle(self):
        adapter = _make_adapter(disable_result_cache=True)
        sqls = self._run_configure(adapter, "tpch")

        assert "SET enable_result_cache_for_session TO OFF" in sqls
        assert "SET query_group TO 'benchbox'" in sqls
        assert "SET enable_case_sensitive_identifier TO OFF" in sqls
        assert "SET datestyle TO 'ISO, MDY'" in sqls
        assert "SET extra_float_digits TO 0" in sqls
        assert "SET statement_timeout TO '1800000'" in sqls

    def test_non_olap_skips_case_sensitivity(self):
        adapter = _make_adapter(disable_result_cache=False)
        sqls = self._run_configure(adapter, "custom")

        assert "SET enable_result_cache_for_session TO ON" in sqls
        assert "SET query_group TO 'benchbox'" in sqls
        assert not any("enable_case_sensitive_identifier" in s for s in sqls)

    def test_olap_variant_types_recognized(self):
        adapter = _make_adapter()
        for btype in ["olap", "analytics", "tpcds"]:
            sqls = self._run_configure(adapter, btype)
            assert any("enable_case_sensitive_identifier" in s for s in sqls), f"Failed for {btype}"


class TestConnectionConfigValidation:
    def test_default_port_is_5439(self):
        adapter = _make_adapter()
        assert adapter.port == 5439

    def test_custom_port(self):
        adapter = _make_adapter(port=5440)
        assert adapter.port == 5440

    def test_default_database_is_dev(self):
        adapter = _make_adapter(database=None)
        assert adapter.database == "dev"

    def test_default_schema_is_public(self):
        adapter = _make_adapter(schema=None)
        assert adapter.schema == "public"

    def test_default_ssl_settings(self):
        adapter = _make_adapter()
        assert adapter.ssl_enabled is True
        assert adapter.ssl_insecure is False
        assert adapter.sslmode == "require"

    def test_ssl_insecure_mode(self):
        adapter = _make_adapter(ssl_insecure=True)
        assert adapter.ssl_insecure is True

    def test_default_aws_region(self):
        adapter = _make_adapter()
        assert adapter.aws_region == "us-east-1"

    def test_custom_aws_region(self):
        adapter = _make_adapter(aws_region="ap-southeast-1")
        assert adapter.aws_region == "ap-southeast-1"

    def test_default_wlm_slot_count(self):
        adapter = _make_adapter()
        assert adapter.wlm_query_slot_count == 1

    def test_default_connect_timeout(self):
        adapter = _make_adapter()
        assert adapter.connect_timeout == 10

    def test_default_statement_timeout(self):
        adapter = _make_adapter()
        assert adapter.statement_timeout == 0

    def test_missing_host_raises(self):
        from benchbox.core.exceptions import ConfigurationError

        with pytest.raises(ConfigurationError, match="host"):
            _make_adapter(host=None)

    def test_missing_username_raises(self):
        from benchbox.core.exceptions import ConfigurationError

        with pytest.raises(ConfigurationError, match="username"):
            _make_adapter(username=None)

    def test_missing_password_raises(self):
        from benchbox.core.exceptions import ConfigurationError

        with pytest.raises(ConfigurationError, match="password"):
            _make_adapter(password=None)

    def test_compupdate_default_preset(self):
        adapter = _make_adapter()
        assert adapter.compupdate == "PRESET"

    def test_compupdate_case_normalization(self):
        adapter = _make_adapter(compupdate="on")
        assert adapter.compupdate == "ON"

    def test_compupdate_invalid_value_raises(self):
        with pytest.raises(ValueError, match="Invalid COMPUPDATE value"):
            _make_adapter(compupdate="ALWAYS")

    def test_result_cache_default_disabled(self):
        adapter = _make_adapter()
        assert adapter.disable_result_cache is True

    def test_admin_database_default(self):
        adapter = _make_adapter()
        assert adapter.admin_database == "dev"

    def test_admin_database_custom(self):
        adapter = _make_adapter(admin_database="shared_admin")
        assert adapter.admin_database == "shared_admin"


class TestLongRunningTimeout:
    @pytest.mark.parametrize(
        "adaptive, floor, expected",
        [
            (10, 300, 300),
            (300, 300, 300),
            (600, 300, 600),
            (10, 60, 60),
        ],
    )
    def test_returns_max_of_adaptive_and_floor(self, adaptive, floor, expected):
        adapter = _make_adapter()
        with patch.object(RedshiftAdapter, "_resolve_connect_timeout", return_value=adaptive):
            assert adapter._long_running_timeout(floor=floor) == expected

    def test_default_floor_is_300(self):
        adapter = _make_adapter()
        with patch.object(RedshiftAdapter, "_resolve_connect_timeout", return_value=10):
            assert adapter._long_running_timeout() == 300


class TestGetConnectionParams:
    def test_default_params(self):
        adapter = _make_adapter()
        params = adapter._get_connection_params()
        assert params["host"] == "test-cluster.redshift.amazonaws.com"
        assert params["port"] == 5439
        assert params["database"] == "test_db"
        assert params["user"] == "test_user"
        assert params["password"] == "test_pass"
        assert params["sslmode"] == "require"

    def test_override_params(self):
        adapter = _make_adapter()
        params = adapter._get_connection_params(host="override.com", database="other_db")
        assert params["host"] == "override.com"
        assert params["database"] == "other_db"
        assert params["user"] == "test_user"
        assert params["port"] == 5439


class TestQueryPlanGeneration:
    def test_explain_sql(self):
        adapter = _make_adapter()
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [
            ("XN Seq Scan on orders  (cost=0.00..15.00 rows=1500 width=36)",),
            ("  Output: o_orderkey, o_custkey",),
        ]

        plan = adapter.get_query_plan(mock_conn, "SELECT * FROM orders")
        mock_cursor.execute.assert_called_once_with("EXPLAIN SELECT * FROM orders")
        assert "XN Seq Scan on orders" in plan
        assert "Output: o_orderkey, o_custkey" in plan
        mock_cursor.close.assert_called_once()

    def test_explain_failure_returns_none(self):
        adapter = _make_adapter()
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = Exception("permission denied")

        plan = adapter.get_query_plan(mock_conn, "SELECT 1")
        assert plan is None
        mock_cursor.close.assert_called_once()


class TestTuningClauseGeneration:
    def test_distribution_and_sorting_combined(self):
        adapter = _make_adapter()
        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = True

        dist_col = Mock()
        dist_col.name = "l_orderkey"
        dist_col.order = 1
        sort_col1 = Mock()
        sort_col1.name = "l_shipdate"
        sort_col1.order = 1
        sort_col2 = Mock()
        sort_col2.name = "l_returnflag"
        sort_col2.order = 2

        with patch("benchbox.core.tuning.interface.TuningType") as TT:
            TT.DISTRIBUTION = "distribution"
            TT.SORTING = "sorting"
            TT.PARTITIONING = "partitioning"
            TT.CLUSTERING = "clustering"

            def get_cols(tt):
                if tt == TT.DISTRIBUTION:
                    return [dist_col]
                if tt == TT.SORTING:
                    return [sort_col2, sort_col1]
                return []

            mock_tuning.get_columns_by_type.side_effect = get_cols
            clause = adapter.generate_tuning_clause(mock_tuning)

        assert "DISTSTYLE KEY" in clause
        assert "DISTKEY (l_orderkey)" in clause
        assert "SORTKEY (l_shipdate, l_returnflag)" in clause

    def test_no_distribution_uses_even(self):
        adapter = _make_adapter()
        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = True

        with patch("benchbox.core.tuning.interface.TuningType") as TT:
            TT.DISTRIBUTION = "distribution"
            TT.SORTING = "sorting"
            TT.PARTITIONING = "partitioning"
            TT.CLUSTERING = "clustering"

            mock_tuning.get_columns_by_type.return_value = []
            clause = adapter.generate_tuning_clause(mock_tuning)

        assert "DISTSTYLE EVEN" in clause
        assert "DISTKEY" not in clause

    def test_none_tuning_returns_empty(self):
        adapter = _make_adapter()
        assert adapter.generate_tuning_clause(None) == ""

    def test_no_tuning_returns_empty(self):
        adapter = _make_adapter()
        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = False
        assert adapter.generate_tuning_clause(mock_tuning) == ""


class TestGetPlatformInfo:
    def test_basic_info_without_connection(self):
        adapter = _make_adapter(
            s3_bucket="mybkt",
            iam_role="arn:aws:iam::999:role/R",
        )
        info = adapter.get_platform_info(connection=None)
        assert info["platform_type"] == "redshift"
        assert info["platform_name"] == "Redshift"
        assert info["connection_mode"] == "remote"
        assert info["cloud_provider"] == "AWS"
        assert info["host"] == "test-cluster.redshift.amazonaws.com"
        assert info["port"] == 5439
        assert info["configuration"]["database"] == "test_db"
        assert info["configuration"]["s3_bucket"] == "mybkt"
        assert info["configuration"]["iam_role"] == "arn:aws:iam::999:role/R"
        assert info["configuration"]["result_cache_enabled"] is False
        assert info["configuration"]["deployment_type"] == "provisioned"
        assert info["platform_version"] is None


class TestUploadFileToS3:
    def test_simple_csv_key(self):
        import tempfile

        adapter = _make_adapter(s3_bucket="bkt", s3_prefix="pfx")
        mock_s3 = Mock()

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            f.write(b"data")
            fpath = Path(f.name)

        try:
            uri = adapter._upload_file_to_s3(mock_s3, fpath, "orders", 0)
        finally:
            fpath.unlink()

        assert uri == "s3://bkt/pfx/orders_0.csv"
        mock_s3.upload_file.assert_called_once_with(str(fpath), "bkt", "pfx/orders_0.csv")

    def test_chunked_tbl_file_preserves_suffix(self):
        import tempfile

        adapter = _make_adapter(s3_bucket="bkt", s3_prefix="pfx")
        mock_s3 = Mock()

        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "lineitem.tbl.1.zst"
            fpath.write_bytes(b"data")

            uri = adapter._upload_file_to_s3(mock_s3, fpath, "lineitem", 2)

        assert uri == "s3://bkt/pfx/lineitem_2.tbl.1.zst"


class TestFromConfigDatabaseGeneration:
    def test_explicit_database_preserved(self):
        config = {
            "host": "test-cluster.redshift.amazonaws.com",
            "username": "u",
            "password": "p",
            "database": "my_explicit_db",
            "benchmark": "tpch",
            "scale_factor": 1.0,
        }
        adapter = RedshiftAdapter.from_config(config)
        assert adapter.database == "my_explicit_db"

    def test_generated_database_name_when_not_provided(self):
        config = {
            "host": "test-cluster.redshift.amazonaws.com",
            "username": "u",
            "password": "p",
            "benchmark": "tpch",
            "scale_factor": 0.01,
        }
        adapter = RedshiftAdapter.from_config(config)
        assert "tpch" in adapter.database.lower()

    def test_staging_root_s3_parsing(self):
        adapter = _make_adapter(staging_root="s3://my-staging-bucket/some/prefix")
        assert adapter.s3_bucket == "my-staging-bucket"
        assert adapter.s3_prefix == "some/prefix"

    def test_staging_root_non_s3_raises(self):
        with pytest.raises(ValueError, match="requires S3"):
            _make_adapter(staging_root="gs://gcs-bucket/path")


class TestGatherStatistics:
    @pytest.fixture(autouse=True)
    def _skip_cluster_state_check(self):
        with patch.object(RedshiftAdapter, "_resolve_connect_timeout", return_value=10):
            yield

    def test_auto_analyze_reports_auto_on_load_without_rebuilding(self):
        adapter = _make_adapter(auto_analyze=True)
        connection = Mock()

        assert adapter.gather_statistics(connection, ["title"]) == ("auto-on-load", 0)
        connection.cursor.assert_not_called()

    def test_disabled_auto_analyze_falls_back_to_explicit_analyze(self):
        adapter = _make_adapter(auto_analyze=False)
        connection = Mock()

        stats_mode, tables_analyzed = adapter.gather_statistics(connection, ["title", "name"])

        assert (stats_mode, tables_analyzed) == ("explicit", 2)
        executed = [c.args[0] for c in connection.cursor.return_value.execute.call_args_list]
        assert executed == ["ANALYZE title", "ANALYZE name"]
