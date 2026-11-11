# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from benchbox.core.exceptions import ConfigurationError
from benchbox.platforms.base.adapter import PlatformAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture(autouse=True)
def mock_snowpark():
    mock_session = MagicMock()
    mock_session_builder = MagicMock()
    mock_session_builder.configs.return_value = mock_session_builder
    mock_session_builder.create.return_value = mock_session

    mock_snowpark_module = MagicMock()
    mock_snowpark_module.Session = MagicMock()
    mock_snowpark_module.Session.builder = mock_session_builder

    mock_exceptions = MagicMock()
    mock_exceptions.SnowparkSQLException = Exception

    with (
        patch.dict(
            "sys.modules",
            {
                "snowflake": MagicMock(),
                "snowflake.snowpark": mock_snowpark_module,
                "snowflake.snowpark.exceptions": mock_exceptions,
            },
        ),
        patch(
            "benchbox.platforms.snowpark_connect.SNOWPARK_AVAILABLE",
            True,
        ),
        patch(
            "benchbox.platforms.snowpark_connect.Session",
            mock_snowpark_module.Session,
        ),
    ):
        yield mock_session


class TestSnowparkConnectAdapterInitialization:
    def test_init_with_all_required_params(self, mock_snowpark):
        from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

        adapter = SnowparkConnectAdapter(
            account="xy12345.us-east-1",
            user="test_user",
            password="test_password",
            warehouse="COMPUTE_WH",
            database="BENCHBOX",
        )

        assert adapter.account == "xy12345.us-east-1"
        assert adapter.user == "test_user"
        assert adapter.password == "test_password"
        assert adapter.warehouse == "COMPUTE_WH"
        assert adapter.database == "BENCHBOX"

    def test_init_missing_account_raises_error(self, mock_snowpark):
        from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

        with pytest.raises(ConfigurationError, match="account is required"):
            SnowparkConnectAdapter(
                account=None,
                user="test_user",
                password="test_password",
            )

    def test_init_missing_user_raises_error(self, mock_snowpark):
        from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

        with pytest.raises(ConfigurationError, match="user is required"):
            SnowparkConnectAdapter(
                account="xy12345.us-east-1",
                user=None,
                password="test_password",
            )

    def test_init_missing_auth_raises_error(self, mock_snowpark):
        from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

        with pytest.raises(ConfigurationError, match="password or private_key_path"):
            SnowparkConnectAdapter(
                account="xy12345.us-east-1",
                user="test_user",
                password=None,
                private_key_path=None,
            )

    def test_init_with_key_auth(self, mock_snowpark):
        from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

        adapter = SnowparkConnectAdapter(
            account="xy12345.us-east-1",
            user="test_user",
            private_key_path="/path/to/key.pem",
        )

        assert adapter.private_key_path == "/path/to/key.pem"
        assert adapter.password is None

    def test_init_with_custom_warehouse_size(self, mock_snowpark):
        from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

        adapter = SnowparkConnectAdapter(
            account="xy12345.us-east-1",
            user="test_user",
            password="test_password",
            warehouse_size="XLARGE",
        )

        assert adapter.warehouse_size == "XLARGE"

    def test_init_with_role(self, mock_snowpark):
        from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

        adapter = SnowparkConnectAdapter(
            account="xy12345.us-east-1",
            user="test_user",
            password="test_password",
            role="ACCOUNTADMIN",
        )

        assert adapter.role == "ACCOUNTADMIN"


class TestSnowparkConnectAdapterPlatformInfo:
    def test_get_platform_info(self, mock_snowpark):
        from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

        adapter = SnowparkConnectAdapter(
            account="xy12345.us-east-1",
            user="test_user",
            password="test_password",
        )

        info = adapter.get_platform_info()

        assert info["platform"] == "snowpark-connect"
        assert info["display_name"] == "Snowpark Connect for Spark"
        assert info["vendor"] == "Snowflake"
        assert info["type"] == "pyspark_compatible"
        assert info["supports_sql"] is True
        assert info["supports_dataframe"] is True
        assert "RDD APIs not supported" in info["limitations"]

    def test_get_target_dialect(self, mock_snowpark):
        from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

        adapter = SnowparkConnectAdapter(
            account="xy12345.us-east-1",
            user="test_user",
            password="test_password",
        )

        assert adapter.get_target_dialect() == "snowflake"


class TestSnowparkConnectAdapterConnection:
    def test_create_connection(self, mock_snowpark):
        from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

        adapter = SnowparkConnectAdapter(
            account="xy12345.us-east-1",
            user="test_user",
            password="test_password",
        )

        mock_session = MagicMock()
        mock_session.sql.return_value.collect.return_value = [("8.0.0",)]

        with (
            patch.object(adapter, "_session", None),
            patch("benchbox.platforms.snowpark_connect.Session") as mock_session_class,
        ):
            mock_builder = MagicMock()
            mock_builder.configs.return_value = mock_builder
            mock_builder.create.return_value = mock_session
            mock_session_class.builder = mock_builder

            result = adapter.create_connection()

        assert result is not None

    def test_create_connection_reuses_existing_session(self, mock_snowpark):
        from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

        adapter = SnowparkConnectAdapter(
            account="xy12345.us-east-1",
            user="test_user",
            password="test_password",
        )

        mock_session = MagicMock()
        mock_session.sql.return_value.collect.return_value = [(1,)]
        adapter._session = mock_session

        result = adapter.create_connection()

        assert result is mock_session

    def test_close_session(self, mock_snowpark):
        from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

        adapter = SnowparkConnectAdapter(
            account="xy12345.us-east-1",
            user="test_user",
            password="test_password",
        )

        mock_session = MagicMock()
        adapter._session = mock_session

        adapter.close()

        mock_session.close.assert_called_once()
        assert adapter._session is None


class TestSnowparkConnectAdapterQueryExecution:
    def test_execute_query_without_session_raises_error(self, mock_snowpark):
        from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

        adapter = SnowparkConnectAdapter(
            account="xy12345.us-east-1",
            user="test_user",
            password="test_password",
        )
        adapter._session = None

        with pytest.raises(ConfigurationError, match="No active session"):
            adapter.execute_query(None, "SELECT 1", "Q1")

    def test_execute_query_success(self, mock_snowpark):
        from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

        adapter = SnowparkConnectAdapter(
            account="xy12345.us-east-1",
            user="test_user",
            password="test_password",
        )

        mock_row = MagicMock()
        mock_row.asDict.return_value = {"COUNT": 42}
        mock_session = MagicMock()
        mock_session.sql.return_value.collect.return_value = [mock_row]
        adapter._session = mock_session

        result = adapter.execute_query(mock_session, "SELECT COUNT(*) AS count FROM table1", "Q1")

        assert result["query_id"] == "Q1"
        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 1
        assert result["results"][0]["COUNT"] == 42
        mock_session.sql.assert_called_once_with("SELECT COUNT(*) AS count FROM table1")

    def test_execute_query_tracks_metrics(self, mock_snowpark):
        from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

        adapter = SnowparkConnectAdapter(
            account="xy12345.us-east-1",
            user="test_user",
            password="test_password",
        )

        mock_session = MagicMock()
        mock_session.sql.return_value.collect.return_value = []
        adapter._session = mock_session

        assert adapter._query_count == 0

        adapter.execute_query(mock_session, "SELECT 1", "Q1")

        assert adapter._query_count == 1
        assert adapter._total_execution_time_seconds >= 0


class TestSnowparkConnectAdapterDataFrame:
    def test_get_dataframe_without_session_raises_error(self, mock_snowpark):
        from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

        adapter = SnowparkConnectAdapter(
            account="xy12345.us-east-1",
            user="test_user",
            password="test_password",
        )
        adapter._session = None

        with pytest.raises(ConfigurationError, match="No active session"):
            adapter.get_dataframe("lineitem")

    def test_get_dataframe_success(self, mock_snowpark):
        from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

        adapter = SnowparkConnectAdapter(
            account="xy12345.us-east-1",
            user="test_user",
            password="test_password",
        )

        mock_df = MagicMock()
        mock_session = MagicMock()
        mock_session.table.return_value = mock_df
        adapter._session = mock_session

        result = adapter.get_dataframe("lineitem")

        assert result is mock_df
        mock_session.table.assert_called_once_with("lineitem")

    def test_execute_dataframe_without_session_raises_error(self, mock_snowpark):
        from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

        adapter = SnowparkConnectAdapter(
            account="xy12345.us-east-1",
            user="test_user",
            password="test_password",
        )
        adapter._session = None

        mock_df = MagicMock()

        with pytest.raises(ConfigurationError, match="No active session"):
            adapter.execute_dataframe(mock_df)

    def test_execute_dataframe_success(self, mock_snowpark):
        from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

        adapter = SnowparkConnectAdapter(
            account="xy12345.us-east-1",
            user="test_user",
            password="test_password",
        )

        mock_row = MagicMock()
        mock_row.asDict.return_value = {"value": 100}
        mock_df = MagicMock()
        mock_df.collect.return_value = [mock_row]

        mock_session = MagicMock()
        adapter._session = mock_session

        result = adapter.execute_dataframe(mock_df)

        assert len(result) == 1
        assert result[0]["value"] == 100


class TestSnowparkConnectAdapterResolveTableNames:
    class _FakeBenchmarkWithOrdering:
        def get_available_tables(self):
            return ["lineitem", "orders", "customer"]

        def get_table_loading_order(self, available_tables):
            full_order = ["customer", "orders", "lineitem"]
            return [t for t in full_order if t in available_tables]

    class _FakeBenchmarkWithoutOrdering:
        def get_table_names(self):
            return ["region", "nation"]

    class _FakeBenchmarkWithSchema:
        def get_schema(self):
            return {"lineitem": object(), "orders": object(), "customer": object()}

        def get_table_loading_order(self, available_tables):
            return [name for name in ("customer", "orders", "lineitem") if name in available_tables]

    def test_resolves_and_orders_via_required_arg_loading_order(self, mock_snowpark):
        from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

        result = SnowparkConnectAdapter._resolve_table_names(self._FakeBenchmarkWithOrdering())

        assert result == ["customer", "orders", "lineitem"]

    def test_falls_back_to_unordered_list_without_loading_order(self, mock_snowpark):
        from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

        result = SnowparkConnectAdapter._resolve_table_names(self._FakeBenchmarkWithoutOrdering())

        assert result == ["region", "nation"]

    def test_falls_back_to_schema_keys_and_applies_loading_order(self, mock_snowpark):
        from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

        result = SnowparkConnectAdapter._resolve_table_names(self._FakeBenchmarkWithSchema())

        assert result == ["customer", "orders", "lineitem"]

    def test_raises_when_benchmark_exposes_no_table_names(self, mock_snowpark):
        from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

        with pytest.raises(ConfigurationError):
            SnowparkConnectAdapter._resolve_table_names(object())


class TestSnowparkConnectAdapterSchema:
    def test_create_schema_without_session_raises_error(self, mock_snowpark):
        from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

        adapter = SnowparkConnectAdapter(
            account="xy12345.us-east-1",
            user="test_user",
            password="test_password",
        )
        adapter._session = None

        with pytest.raises(ConfigurationError, match="No active session"):
            adapter.create_schema(MagicMock(), None)

    def test_create_schema_success(self, mock_snowpark):
        from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

        adapter = SnowparkConnectAdapter(
            account="xy12345.us-east-1",
            user="test_user",
            password="test_password",
            database="BENCHBOX",
            schema="PUBLIC",
        )

        mock_session = MagicMock()
        mock_session.sql.return_value.collect.return_value = None
        adapter._session = mock_session

        elapsed = adapter.create_schema(MagicMock(), mock_session)

        assert elapsed >= 0
        calls = mock_session.sql.call_args_list
        assert any("CREATE DATABASE IF NOT EXISTS" in str(call) for call in calls)
        assert any("CREATE SCHEMA IF NOT EXISTS" in str(call) for call in calls)

    def test_abc_method_signatures_match_platform_adapter(self, mock_snowpark):
        from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

        for method_name in ("create_schema", "load_data", "execute_query"):
            assert inspect.signature(getattr(SnowparkConnectAdapter, method_name)) == inspect.signature(
                getattr(PlatformAdapter, method_name)
            )

    def test_load_data_uses_standard_contract(self, mock_snowpark, tmp_path):
        from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

        adapter = SnowparkConnectAdapter(
            account="xy12345.us-east-1",
            user="test_user",
            password="test_password",
        )

        table_file = tmp_path / "lineitem.parquet"
        table_file.write_bytes(b"PAR1")

        benchmark = MagicMock()
        benchmark.get_table_loading_order.return_value = ["lineitem"]
        mock_session = MagicMock()
        mock_session.sql.return_value.collect.return_value = [(3,)]

        table_stats, elapsed, per_table = adapter.load_data(benchmark, mock_session, Path(tmp_path))

        assert table_stats == {"lineitem": 3}
        assert elapsed >= 0
        assert per_table is not None
        assert "lineitem" in per_table
        assert per_table["lineitem"]["total_ms"] >= 0
        mock_session.read.parquet.assert_called_once_with("@~/lineitem/")


class TestSnowparkConnectAdapterCLI:
    def test_add_cli_arguments(self, mock_snowpark):
        import argparse

        from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

        parser = argparse.ArgumentParser()
        SnowparkConnectAdapter.add_cli_arguments(parser)

        args = parser.parse_args(["--account", "xy12345", "--user", "testuser"])

        assert args.account == "xy12345"
        assert args.user == "testuser"

    def test_from_config(self, mock_snowpark):
        from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

        config = {
            "account": "xy12345.us-east-1",
            "user": "test_user",
            "password": "test_password",
            "warehouse": "MY_WH",
            "database": "MY_DB",
            "schema": "MY_SCHEMA",
            "role": "MY_ROLE",
        }

        adapter = SnowparkConnectAdapter.from_config(config)

        assert adapter.account == "xy12345.us-east-1"
        assert adapter.user == "test_user"
        assert adapter.warehouse == "MY_WH"
        assert adapter.database == "MY_DB"
        assert adapter.schema == "MY_SCHEMA"
        assert adapter.role == "MY_ROLE"

    def test_from_config_forwards_force_recreate(self, mock_snowpark):
        from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

        base = {
            "account": "xy12345.us-east-1",
            "user": "test_user",
            "password": "test_password",
        }
        assert SnowparkConnectAdapter.from_config({**base, "force_recreate": True}).force_recreate is True
        assert SnowparkConnectAdapter.from_config({**base, "force": True}).force_recreate is True
        assert SnowparkConnectAdapter.from_config(dict(base)).force_recreate is False

    def test_load_data_csv_truncates_before_copy_when_forced(self, mock_snowpark, tmp_path):
        from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

        adapter = SnowparkConnectAdapter(
            account="xy12345.us-east-1",
            user="test_user",
            password="test_password",
            force_recreate=True,
        )
        table_file = tmp_path / "lineitem.tbl"
        table_file.write_bytes(b"1|one|\n")
        benchmark = MagicMock()
        benchmark.get_table_loading_order.return_value = ["lineitem"]
        mock_session = MagicMock()
        mock_session.sql.return_value.collect.return_value = [(3,)]

        table_stats, _, _ = adapter.load_data(benchmark, mock_session, Path(tmp_path))

        assert table_stats == {"lineitem": 3}
        statements = [str(call.args[0]) for call in mock_session.sql.call_args_list]
        assert any("TRUNCATE TABLE lineitem" in sql for sql in statements)
        assert any("COPY INTO" in sql and "FORCE = TRUE" in sql for sql in statements)
        truncate_idx = next(i for sql in statements for i in [statements.index(sql)] if "TRUNCATE TABLE" in sql)
        copy_idx = next(i for i, sql in enumerate(statements) if "COPY INTO" in sql)
        assert truncate_idx < copy_idx

    def test_load_data_csv_full_refresh_by_default(self, mock_snowpark, tmp_path):
        from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

        adapter = SnowparkConnectAdapter(
            account="xy12345.us-east-1",
            user="test_user",
            password="test_password",
        )
        table_file = tmp_path / "lineitem.tbl"
        table_file.write_bytes(b"1|one|\n")
        benchmark = MagicMock()
        benchmark.get_table_loading_order.return_value = ["lineitem"]
        mock_session = MagicMock()
        mock_session.sql.return_value.collect.return_value = [(3,)]

        adapter.load_data(benchmark, mock_session, Path(tmp_path))

        statements = [str(call.args[0]) for call in mock_session.sql.call_args_list]
        assert any(sql.startswith("REMOVE ") for sql in statements)
        assert any("PUT " in sql and "OVERWRITE = TRUE" in sql for sql in statements)
        assert any("TRUNCATE TABLE lineitem" in sql for sql in statements)
        assert any("COPY INTO" in sql and "FORCE = TRUE" in sql for sql in statements)


class TestSnowparkConnectAdapterTuning:
    def test_configure_for_benchmark(self, mock_snowpark):
        from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

        adapter = SnowparkConnectAdapter(
            account="xy12345.us-east-1",
            user="test_user",
            password="test_password",
        )

        mock_session = MagicMock()
        mock_session.sql.return_value.collect.return_value = None
        adapter._session = mock_session

        adapter.configure_for_benchmark(mock_session, "tpch")

        assert adapter._benchmark_type == "tpch"
        mock_session.sql.assert_called_with("ALTER SESSION SET USE_CACHED_RESULT = FALSE")

    def test_configure_for_benchmark_hygiene_never_reaches_ledger(self, mock_snowpark):
        from benchbox.core.tuning.applied_ledger import AppliedTuningLedger
        from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

        adapter = SnowparkConnectAdapter(
            account="xy12345.us-east-1",
            user="test_user",
            password="test_password",
        )
        adapter._applied_tuning_ledger = AppliedTuningLedger()

        mock_session = MagicMock()
        mock_session.sql.return_value.collect.return_value = None
        adapter._session = mock_session

        adapter.configure_for_benchmark(mock_session, "tpch")

        mock_session.sql.assert_called_with("ALTER SESSION SET USE_CACHED_RESULT = FALSE")
        assert adapter._applied_tuning_ledger.is_empty()

    def test_apply_platform_optimizations_records_nothing(self, mock_snowpark):
        from benchbox.core.tuning.applied_ledger import AppliedTuningLedger
        from benchbox.core.tuning.interface import UnifiedTuningConfiguration
        from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

        adapter = SnowparkConnectAdapter(
            account="xy12345.us-east-1",
            user="test_user",
            password="test_password",
        )
        adapter._applied_tuning_ledger = AppliedTuningLedger()

        config = UnifiedTuningConfiguration()
        config.enable_all_constraints()
        assert adapter.apply_platform_optimizations(config.platform_optimizations) == []
        assert adapter._applied_tuning_ledger.is_empty()

    def test_apply_platform_optimizations_records_dropped_intents(self, mock_snowpark):
        from benchbox.core.tuning.applied_ledger import AppliedTuningLedger
        from benchbox.core.tuning.interface import TuningType, UnifiedTuningConfiguration
        from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

        adapter = SnowparkConnectAdapter(
            account="xy12345.us-east-1",
            user="test_user",
            password="test_password",
        )
        adapter._applied_tuning_ledger = AppliedTuningLedger()

        config = UnifiedTuningConfiguration()
        config.enable_platform_optimization(TuningType.Z_ORDERING, columns=["l_orderkey"])
        assert adapter.apply_platform_optimizations(config.platform_optimizations) == []
        assert adapter._applied_tuning_ledger.statements == []
        dropped = adapter._applied_tuning_ledger.dropped
        assert any("z_ordering_enabled" in intent.intent for intent in dropped)


class TestSnowparkConnectAdapterConnectionParams:
    def test_build_connection_params_basic(self, mock_snowpark):
        from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

        adapter = SnowparkConnectAdapter(
            account="xy12345.us-east-1",
            user="test_user",
            password="test_password",
            warehouse="COMPUTE_WH",
            database="BENCHBOX",
            schema="PUBLIC",
        )

        params = adapter._build_connection_parameters()

        assert params["account"] == "xy12345.us-east-1"
        assert params["user"] == "test_user"
        assert params["password"] == "test_password"
        assert params["warehouse"] == "COMPUTE_WH"
        assert params["database"] == "BENCHBOX"
        assert params["schema"] == "PUBLIC"

    def test_build_connection_params_with_role(self, mock_snowpark):
        from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

        adapter = SnowparkConnectAdapter(
            account="xy12345.us-east-1",
            user="test_user",
            password="test_password",
            role="SYSADMIN",
        )

        params = adapter._build_connection_parameters()

        assert params["role"] == "SYSADMIN"

    def test_build_connection_params_with_authenticator(self, mock_snowpark):
        from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

        adapter = SnowparkConnectAdapter(
            account="xy12345.us-east-1",
            user="test_user",
            password="test_password",
            authenticator="externalbrowser",
        )

        params = adapter._build_connection_parameters()

        assert params["authenticator"] == "externalbrowser"


