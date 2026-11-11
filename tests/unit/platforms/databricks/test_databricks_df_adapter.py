# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import MagicMock, patch

import pytest

from benchbox.platforms.databricks.dataframe_adapter import (
    DATABRICKS_CONNECT_AVAILABLE,
    DatabricksDataFrameAdapter,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.cloud_import,
]


@pytest.fixture(autouse=True)
def databricks_dependencies():
    with patch(
        "benchbox.platforms.databricks.adapter.check_platform_dependencies",
        return_value=(True, []),
    ):
        yield


@pytest.fixture
def mock_databricks_sql():
    with patch("benchbox.platforms.databricks.adapter.databricks_sql") as mock:
        yield mock


@pytest.fixture
def mock_databricks_connect():
    mock_session = MagicMock()
    mock_session.version = "14.3.0"
    mock_session.catalog = MagicMock()

    mock_builder = MagicMock()
    mock_builder.host.return_value = mock_builder
    mock_builder.token.return_value = mock_builder
    mock_builder.clusterId.return_value = mock_builder
    mock_builder.getOrCreate.return_value = mock_session

    mock_db_session = MagicMock()
    mock_db_session.builder = mock_builder

    with (
        patch.dict(
            "sys.modules",
            {"databricks.connect": MagicMock(DatabricksSession=mock_db_session)},
        ),
        patch(
            "benchbox.platforms.databricks.dataframe_adapter.DATABRICKS_CONNECT_AVAILABLE",
            True,
        ),
        patch(
            "benchbox.platforms.databricks.dataframe_adapter.DatabricksSession",
            mock_db_session,
        ),
    ):
        yield mock_db_session, mock_session


class TestDatabricksDataFrameAdapterInitialization:
    def test_initialization_success(self, mock_databricks_sql):

        adapter = DatabricksDataFrameAdapter(
            server_hostname="test.cloud.databricks.com",
            http_path="/sql/1.0/warehouses/test",
            access_token="test_token",
            catalog="test_catalog",
            schema="test_schema",
        )

        assert "Databricks" in adapter.platform_name
        assert adapter.server_hostname == "test.cloud.databricks.com"
        assert adapter.catalog == "test_catalog"
        assert adapter.schema == "test_schema"

    def test_initialization_rejects_hudi_table_format(self, mock_databricks_sql):
        with pytest.raises(ValueError, match="does not support table_format"):
            DatabricksDataFrameAdapter(
                server_hostname="test.cloud.databricks.com",
                http_path="/sql/1.0/warehouses/test",
                access_token="test_token",
                table_format="hudi",
            )

    def test_hudi_rejection_precedes_parent_validation(self, mock_databricks_sql):
        with pytest.raises(ValueError, match="does not support table_format"):
            DatabricksDataFrameAdapter(
                server_hostname="test.cloud.databricks.com",
                http_path="/sql/1.0/warehouses/test",
                access_token="test_token",
                table_format="hudi",
                hudi_primary_key="not a valid identifier!",
            )

    def test_initialization_with_cluster_id(self, mock_databricks_sql):

        adapter = DatabricksDataFrameAdapter(
            server_hostname="test.cloud.databricks.com",
            http_path="/sql/1.0/warehouses/test",
            access_token="test_token",
            cluster_id="0101-123456-abc123",
        )

        assert adapter.cluster_id == "0101-123456-abc123"

    def test_initialization_with_execution_mode(self, mock_databricks_sql):

        adapter_sql = DatabricksDataFrameAdapter(
            server_hostname="test.cloud.databricks.com",
            http_path="/sql/1.0/warehouses/test",
            access_token="test_token",
            execution_mode="sql",
        )
        assert adapter_sql.execution_mode == "sql"

        adapter_df = DatabricksDataFrameAdapter(
            server_hostname="test.cloud.databricks.com",
            http_path="/sql/1.0/warehouses/test",
            access_token="test_token",
            execution_mode="dataframe",
        )
        if not DATABRICKS_CONNECT_AVAILABLE:
            assert adapter_df.execution_mode == "sql"

    def test_inheritance_from_databricks_adapter(self, mock_databricks_sql):

        from benchbox.platforms.databricks import DatabricksAdapter

        adapter = DatabricksDataFrameAdapter(
            server_hostname="test.cloud.databricks.com",
            http_path="/sql/1.0/warehouses/test",
            access_token="test_token",
        )

        assert isinstance(adapter, DatabricksAdapter)
        assert hasattr(adapter, "create_connection")
        assert hasattr(adapter, "create_schema")
        assert hasattr(adapter, "load_data")


class TestDatabricksDataFrameAdapterFromConfig:
    def test_from_config_basic(self, mock_databricks_sql):
        config = {
            "server_hostname": "test.cloud.databricks.com",
            "http_path": "/sql/1.0/warehouses/test",
            "access_token": "test_token",
            "catalog": "test_catalog",
            "schema": "test_schema",
        }

        adapter = DatabricksDataFrameAdapter.from_config(config)

        assert adapter.server_hostname == "test.cloud.databricks.com"
        assert adapter.catalog == "test_catalog"
        assert adapter.schema == "test_schema"

    def test_from_config_with_execution_mode(self, mock_databricks_sql):
        config = {
            "server_hostname": "test.cloud.databricks.com",
            "http_path": "/sql/1.0/warehouses/test",
            "access_token": "test_token",
            "execution_mode": "sql",
        }

        adapter = DatabricksDataFrameAdapter.from_config(config)

        assert adapter.execution_mode == "sql"

    def test_from_config_with_cluster_id(self, mock_databricks_sql):
        config = {
            "server_hostname": "test.cloud.databricks.com",
            "http_path": "/sql/1.0/warehouses/test",
            "access_token": "test_token",
            "cluster_id": "0101-123456-abc123",
        }

        adapter = DatabricksDataFrameAdapter.from_config(config)

        assert adapter.cluster_id == "0101-123456-abc123"


class TestDatabricksDataFrameAdapterPlatformInfo:
    def test_platform_name_sql_mode(self, mock_databricks_sql):

        adapter = DatabricksDataFrameAdapter(
            server_hostname="test.cloud.databricks.com",
            http_path="/sql/1.0/warehouses/test",
            access_token="test_token",
            execution_mode="sql",
        )

        assert adapter.platform_name == "Databricks"

    def test_get_platform_info_includes_execution_mode(self, mock_databricks_sql):
        adapter = DatabricksDataFrameAdapter(
            server_hostname="test.cloud.databricks.com",
            http_path="/sql/1.0/warehouses/test",
            access_token="test_token",
            cluster_id="test-cluster",
        )

        with patch.dict("sys.modules", {"databricks.sdk": MagicMock()}):
            info = adapter.get_platform_info()

        assert "execution_mode" in info
        assert "cluster_id" in info
        assert info["cluster_id"] == "test-cluster"
        assert "databricks_connect_available" in info


class TestDatabricksDataFrameAdapterExpressionHelpers:
    def test_col_expression(self, mock_databricks_sql):
        adapter = DatabricksDataFrameAdapter(
            server_hostname="test.cloud.databricks.com",
            http_path="/sql/1.0/warehouses/test",
            access_token="test_token",
        )

        with patch(
            "benchbox.platforms.databricks.dataframe_adapter.PYSPARK_AVAILABLE",
            True,
        ):
            mock_col = MagicMock()
            with patch("benchbox.platforms.databricks.dataframe_adapter.F") as mock_f:
                mock_f.col = mock_col
                adapter.col("test_column")
                mock_col.assert_called_once_with("test_column")

    def test_lit_expression(self, mock_databricks_sql):
        adapter = DatabricksDataFrameAdapter(
            server_hostname="test.cloud.databricks.com",
            http_path="/sql/1.0/warehouses/test",
            access_token="test_token",
        )

        with patch(
            "benchbox.platforms.databricks.dataframe_adapter.PYSPARK_AVAILABLE",
            True,
        ):
            mock_lit = MagicMock()
            with patch("benchbox.platforms.databricks.dataframe_adapter.F") as mock_f:
                mock_f.lit = mock_lit
                adapter.lit(42)
                mock_lit.assert_called_once_with(42)

    def test_aggregation_expressions(self, mock_databricks_sql):

        adapter = DatabricksDataFrameAdapter(
            server_hostname="test.cloud.databricks.com",
            http_path="/sql/1.0/warehouses/test",
            access_token="test_token",
        )

        with patch(
            "benchbox.platforms.databricks.dataframe_adapter.PYSPARK_AVAILABLE",
            True,
        ):
            mock_sum = MagicMock()
            mock_avg = MagicMock()
            mock_count = MagicMock()
            mock_min = MagicMock()
            mock_max = MagicMock()
            mock_col = MagicMock(return_value="col_expr")

            with patch("benchbox.platforms.databricks.dataframe_adapter.F") as mock_f:
                mock_f.col = mock_col
                mock_f.sum = mock_sum
                mock_f.avg = mock_avg
                mock_f.count = mock_count
                mock_f.min = mock_min
                mock_f.max = mock_max

                adapter.sum_col("amount")
                mock_sum.assert_called_once()

                adapter.avg_col("price")
                mock_avg.assert_called_once()

                adapter.count_col("id")
                mock_count.assert_called()

                adapter.min_col("date")
                mock_min.assert_called_once()

                adapter.max_col("value")
                mock_max.assert_called_once()


class TestDatabricksDataFrameAdapterQueryExecution:
    def test_execute_query_with_sql_string(self, mock_databricks_sql):
        adapter = DatabricksDataFrameAdapter(
            server_hostname="test.cloud.databricks.com",
            http_path="/sql/1.0/warehouses/test",
            access_token="test_token",
        )

        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [(1, "test")]
        mock_cursor.fetchone.return_value = ("use_cached_result", "false")
        mock_connection = MagicMock()
        mock_connection.cursor.return_value = mock_cursor

        result = adapter.execute_query(
            connection=mock_connection,
            query="SELECT 1, 'test'",
            query_id="TEST1",
        )

        assert result["query_id"] == "TEST1"
        executed = [call.args[0] for call in mock_cursor.execute.call_args_list]
        assert executed.count("SELECT 1, 'test'") == 1

    def test_execute_query_with_callable_uses_dataframe_mode(self, mock_databricks_sql):
        adapter = DatabricksDataFrameAdapter(
            server_hostname="test.cloud.databricks.com",
            http_path="/sql/1.0/warehouses/test",
            access_token="test_token",
        )

        with patch.object(adapter, "execute_dataframe_query", return_value={"query_id": "Q1"}) as mock_df_exec:

            def query_builder(spark, tables):
                return spark.table("test")

            result = adapter.execute_query(
                connection=MagicMock(),
                query=query_builder,
                query_id="Q1",
            )

            mock_df_exec.assert_called_once()
            assert result["query_id"] == "Q1"


class TestDatabricksDataFrameAdapterSparkSession:
    def test_spark_session_created_lazily(self, mock_databricks_sql):

        adapter = DatabricksDataFrameAdapter(
            server_hostname="test.cloud.databricks.com",
            http_path="/sql/1.0/warehouses/test",
            access_token="test_token",
        )

        assert adapter._spark is None
        assert adapter._spark_initialized is False

    def test_close_connection_stops_spark_session(self, mock_databricks_sql):
        adapter = DatabricksDataFrameAdapter(
            server_hostname="test.cloud.databricks.com",
            http_path="/sql/1.0/warehouses/test",
            access_token="test_token",
        )

        mock_spark = MagicMock()
        adapter._spark = mock_spark
        adapter._spark_initialized = True

        mock_connection = MagicMock()
        adapter.close_connection(mock_connection)

        mock_spark.stop.assert_called_once()
        assert adapter._spark is None
        assert adapter._spark_initialized is False


class TestDatabricksDataFrameAdapterRegistry:
    def test_databricks_df_registered_in_platform_registry(self):

        from benchbox.core.platform_registry import PlatformRegistry

        available = PlatformRegistry.get_available_platforms()
        assert "databricks-df" in available

    def test_databricks_df_metadata_correct(self):

        from benchbox.core.platform_registry import PlatformRegistry

        info = PlatformRegistry.get_platform_info("databricks-df")

        assert info is not None
        assert info.display_name == "Databricks DataFrame"
        assert info.category == "cloud"
        assert "dataframe" in info.supports

    def test_databricks_df_capabilities_correct(self):

        from benchbox.core.platform_registry import PlatformRegistry

        caps = PlatformRegistry.get_platform_capabilities("databricks-df")

        assert caps is not None
        assert caps.supports_sql is True
        assert caps.supports_dataframe is True
        assert caps.default_mode == "dataframe"

    def test_databricks_original_now_supports_dataframe(self):
        from benchbox.core.platform_registry import PlatformRegistry

        caps = PlatformRegistry.get_platform_capabilities("databricks")

        assert caps is not None
        assert caps.supports_sql is True
        assert caps.supports_dataframe is True
        assert caps.default_mode == "sql"


class TestDatabricksDataFrameAdapterIntegration:
    def test_adapter_creates_with_from_config_factory(self, mock_databricks_sql):

        from benchbox.core.platform_registry import PlatformRegistry

        config = {
            "server_hostname": "test.cloud.databricks.com",
            "http_path": "/sql/1.0/warehouses/test",
            "access_token": "test_token",
            "catalog": "test_catalog",
            "schema": "test_schema",
        }

        adapter = PlatformRegistry.create_adapter("databricks-df", config)

        assert isinstance(adapter, DatabricksDataFrameAdapter)
        assert adapter.catalog == "test_catalog"


