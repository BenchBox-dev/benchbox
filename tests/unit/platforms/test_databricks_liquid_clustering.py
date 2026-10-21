from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from benchbox.core.tuning.interface import UnifiedTuningConfiguration
from benchbox.platforms.databricks import DatabricksAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.cloud_import,
]


def _make_col(name: str, order: int):
    col = Mock()
    col.name = name
    col.order = order
    return col


def _make_table_tuning(cluster_cols=None, distribution_cols=None, sort_cols=None):
    from benchbox.core.tuning.interface import TuningType

    table_tuning = Mock()
    table_tuning.table_name = "test_table"
    table_tuning.has_any_tuning.return_value = True

    cluster_cols = cluster_cols or []
    distribution_cols = distribution_cols or []
    sort_cols = sort_cols or []

    def get_columns_by_type(tuning_type):
        if tuning_type == TuningType.CLUSTERING:
            return cluster_cols
        if tuning_type == TuningType.DISTRIBUTION:
            return distribution_cols
        if tuning_type == TuningType.SORTING:
            return sort_cols
        return []

    table_tuning.get_columns_by_type.side_effect = get_columns_by_type
    return table_tuning


@patch("benchbox.platforms.databricks.adapter.databricks_sql")
def test_apply_table_tunings_uses_liquid_clustering_when_configured(_mock_databricks_sql):
    adapter = DatabricksAdapter(
        server_hostname="test.cloud.databricks.com",
        http_path="/sql/1.0/warehouses/test",
        access_token="test_token",
    )

    unified = UnifiedTuningConfiguration()
    unified.platform_optimizations.databricks_clustering_strategy = "liquid_clustering"
    unified.platform_optimizations.liquid_clustering_enabled = True
    unified.platform_optimizations.liquid_clustering_columns = ["event_time", "customer_id"]
    adapter.unified_tuning_configuration = unified

    mock_cursor = Mock()
    mock_cursor.fetchall.return_value = [("Provider", "delta")]
    mock_connection = Mock()
    mock_connection.cursor.return_value = mock_cursor

    table_tuning = _make_table_tuning(cluster_cols=[_make_col("cluster_key", 1)])
    adapter.apply_table_tunings(table_tuning, mock_connection)

    execute_calls = [str(call) for call in mock_cursor.execute.call_args_list]
    assert any("ALTER TABLE test_table CLUSTER BY (event_time, customer_id)" in call for call in execute_calls)
    assert not any("ZORDER BY" in call for call in execute_calls)
    assert adapter._liquid_clustering_operations == [
        {
            "table": "test_table",
            "columns": ["event_time", "customer_id"],
            "statement": "ALTER TABLE test_table CLUSTER BY (event_time, customer_id)",
            "mode": "manual",
        }
    ]
    assert [operation["mechanism"] for operation in adapter._applied_layout_operations] == ["liquid_clustering"]


@patch("benchbox.platforms.databricks.adapter.databricks_sql")
def test_z_order_waits_for_loaded_data(_mock_databricks_sql):
    adapter = DatabricksAdapter(
        server_hostname="test.cloud.databricks.com",
        http_path="/sql/1.0/warehouses/test",
        access_token="test_token",
    )

    unified = UnifiedTuningConfiguration()
    unified.platform_optimizations.databricks_clustering_strategy = "z_order"
    unified.table_tunings["test_table"] = _make_table_tuning(
        cluster_cols=[_make_col("cluster_key", 1)],
        distribution_cols=[_make_col("dist_key", 2)],
    )
    adapter.unified_tuning_configuration = unified

    mock_cursor = Mock()
    mock_cursor.fetchall.return_value = [("Provider", "delta")]
    mock_connection = Mock()
    mock_connection.cursor.return_value = mock_cursor

    adapter.apply_table_tunings(unified.table_tunings["test_table"], mock_connection)

    assert not any("ZORDER" in str(call) for call in mock_cursor.execute.call_args_list)
    assert adapter._z_order_operations == []
    assert adapter._applied_layout_operations == []

    mock_cursor.execute.reset_mock()
    adapter.run_post_load_tunings("test_table", unified, mock_connection)

    statements = [str(call.args[0]) for call in mock_cursor.execute.call_args_list]
    assert "OPTIMIZE test_table ZORDER BY (cluster_key, dist_key)" in statements
    assert "OPTIMIZE test_table" not in statements
    assert adapter._z_order_operations == [
        {
            "table": "test_table",
            "columns": ["cluster_key", "dist_key"],
            "statement": "OPTIMIZE test_table ZORDER BY (cluster_key, dist_key)",
        }
    ]
    assert [(op["mechanism"], op["phase"]) for op in adapter._applied_layout_operations] == [
        ("z_order", "post_load"),
        ("analyze", "post_load"),
    ]


@patch("benchbox.platforms.databricks.adapter.databricks_sql")
def test_z_order_runs_after_the_copy_into_that_fills_the_table(_mock_databricks_sql):
    from pathlib import Path

    from tests.unit.platforms.csv_dialect_test_helpers import resolver_data_source

    adapter = DatabricksAdapter(
        server_hostname="test.cloud.databricks.com",
        http_path="/sql/1.0/warehouses/test",
        access_token="test_token",
    )
    unified = UnifiedTuningConfiguration()
    unified.platform_optimizations.databricks_clustering_strategy = "z_order"
    unified.table_tunings["lineitem"] = _make_table_tuning(cluster_cols=[_make_col("l_orderkey", 1)])
    adapter.catalog = "main"
    adapter.schema = "bench"
    adapter.get_effective_tuning_configuration = Mock(return_value=unified)

    cursor = Mock()
    cursor.fetchone.return_value = (10,)
    cursor.fetchall.return_value = [("Provider", "delta")]
    connection = Mock()
    connection.cursor.return_value = cursor
    file_path = Path("lineitem.csv")

    adapter.apply_table_tunings(unified.table_tunings["lineitem"], connection)
    adapter._load_single_table(
        cursor=cursor,
        connection=connection,
        benchmark=None,
        table_name="lineitem",
        file_path=file_path,
        stage_root="dbfs:/Volumes/main/bench",
        existing_tables={"lineitem"},
        data_source=resolver_data_source(
            "lineitem", file_path, {"csv_delimiter": ",", "csv_has_header": False, "csv_null_marker": ""}
        ),
    )

    statements = [str(call.args[0]) for call in cursor.execute.call_args_list]
    copy_index = next(i for i, sql in enumerate(statements) if sql.startswith("COPY INTO"))
    zorder_indexes = [i for i, sql in enumerate(statements) if "ZORDER BY" in sql]
    assert len(zorder_indexes) == 1
    assert zorder_indexes[0] > copy_index
    assert statements[zorder_indexes[0]] == "OPTIMIZE lineitem ZORDER BY (l_orderkey)"
    assert [sql for sql in statements if sql.upper().startswith("OPTIMIZE")] == [statements[zorder_indexes[0]]]


@patch("benchbox.platforms.databricks.adapter.databricks_sql")
def test_z_order_runs_when_delta_optimization_is_disabled(_mock_databricks_sql):
    adapter = DatabricksAdapter(
        server_hostname="test.cloud.databricks.com",
        http_path="/sql/1.0/warehouses/test",
        access_token="test_token",
        enable_delta_optimization=False,
    )
    unified = UnifiedTuningConfiguration()
    unified.platform_optimizations.databricks_clustering_strategy = "z_order"
    unified.table_tunings["test_table"] = _make_table_tuning(cluster_cols=[_make_col("cluster_key", 1)])
    adapter.unified_tuning_configuration = unified

    mock_cursor = Mock()
    mock_cursor.fetchall.return_value = [("Provider", "delta")]
    mock_connection = Mock()
    mock_connection.cursor.return_value = mock_cursor

    adapter.run_post_load_tunings("test_table", unified, mock_connection)

    statements = [str(call.args[0]) for call in mock_cursor.execute.call_args_list]
    assert "OPTIMIZE test_table ZORDER BY (cluster_key)" in statements
    assert not any(sql.startswith("ANALYZE") for sql in statements)


@patch("benchbox.platforms.databricks.adapter.databricks_sql")
def test_liquid_clustering_leaves_the_post_load_step_to_plain_optimize(_mock_databricks_sql):
    adapter = DatabricksAdapter(
        server_hostname="test.cloud.databricks.com",
        http_path="/sql/1.0/warehouses/test",
        access_token="test_token",
    )
    unified = UnifiedTuningConfiguration()
    unified.platform_optimizations.databricks_clustering_strategy = "liquid_clustering"
    unified.platform_optimizations.liquid_clustering_enabled = True
    unified.platform_optimizations.liquid_clustering_columns = ["event_time"]
    unified.table_tunings["test_table"] = _make_table_tuning(cluster_cols=[_make_col("cluster_key", 1)])
    adapter.unified_tuning_configuration = unified

    mock_cursor = Mock()
    mock_cursor.fetchall.return_value = [("Provider", "delta")]
    mock_connection = Mock()
    mock_connection.cursor.return_value = mock_cursor

    adapter.run_post_load_tunings("test_table", unified, mock_connection)

    statements = [str(call.args[0]) for call in mock_cursor.execute.call_args_list]
    assert "OPTIMIZE test_table" in statements
    assert not any("ZORDER" in sql for sql in statements)


@patch("benchbox.platforms.databricks.adapter.databricks_sql")
def test_strategy_conflict_rejects_liquid_when_legacy_z_order_is_also_enabled(_mock_databricks_sql):
    adapter = DatabricksAdapter(
        server_hostname="test.cloud.databricks.com",
        http_path="/sql/1.0/warehouses/test",
        access_token="test_token",
    )

    unified = UnifiedTuningConfiguration()
    unified.platform_optimizations.z_ordering_enabled = True
    unified.platform_optimizations.databricks_clustering_strategy = "z_order"
    unified.platform_optimizations.liquid_clustering_enabled = True
    unified.platform_optimizations.liquid_clustering_columns = ["event_time"]
    adapter.unified_tuning_configuration = unified

    mock_cursor = Mock()
    mock_cursor.fetchall.return_value = [("Provider", "delta")]
    mock_connection = Mock()
    mock_connection.cursor.return_value = mock_cursor

    table_tuning = _make_table_tuning(cluster_cols=[_make_col("cluster_key", 1)])
    with pytest.raises(ValueError, match="Liquid Clustering cannot be combined"):
        adapter.apply_table_tunings(table_tuning, mock_connection)


@patch("benchbox.platforms.databricks.adapter.databricks_sql")
def test_apply_table_tunings_uses_liquid_auto_when_configured(_mock_databricks_sql):
    adapter = DatabricksAdapter(
        server_hostname="test.cloud.databricks.com",
        http_path="/sql/1.0/warehouses/test",
        access_token="test_token",
    )

    unified = UnifiedTuningConfiguration()
    unified.platform_optimizations.databricks_clustering_strategy = "liquid_clustering_auto"
    unified.platform_optimizations.liquid_clustering_enabled = True
    adapter.unified_tuning_configuration = unified

    mock_cursor = Mock()
    mock_cursor.fetchall.return_value = [("Provider", "delta")]
    mock_connection = Mock()
    mock_connection.cursor.return_value = mock_cursor

    table_tuning = _make_table_tuning(cluster_cols=[_make_col("cluster_key", 1)])
    adapter.apply_table_tunings(table_tuning, mock_connection)

    execute_calls = [str(call) for call in mock_cursor.execute.call_args_list]
    assert any("ALTER TABLE test_table CLUSTER BY AUTO" in call for call in execute_calls)
    assert adapter._liquid_clustering_operations == [
        {
            "table": "test_table",
            "columns": [],
            "statement": "ALTER TABLE test_table CLUSTER BY AUTO",
            "mode": "auto",
        }
    ]
    assert [operation["mechanism"] for operation in adapter._applied_layout_operations] == ["liquid_clustering_auto"]


@patch("benchbox.platforms.databricks.adapter.databricks_sql")
def test_apply_table_tunings_liquid_clustering_falls_back_to_sort_columns(_mock_databricks_sql):
    adapter = DatabricksAdapter(
        server_hostname="test.cloud.databricks.com",
        http_path="/sql/1.0/warehouses/test",
        access_token="test_token",
    )

    unified = UnifiedTuningConfiguration()
    unified.platform_optimizations.databricks_clustering_strategy = "liquid_clustering"
    unified.platform_optimizations.liquid_clustering_enabled = True
    adapter.unified_tuning_configuration = unified

    mock_cursor = Mock()
    mock_cursor.fetchall.return_value = [("Provider", "delta")]
    mock_connection = Mock()
    mock_connection.cursor.return_value = mock_cursor

    table_tuning = _make_table_tuning(sort_cols=[_make_col("ship_date", 1), _make_col("order_key", 2)])
    adapter.apply_table_tunings(table_tuning, mock_connection)

    execute_calls = [str(call) for call in mock_cursor.execute.call_args_list]
    assert any("ALTER TABLE test_table CLUSTER BY (ship_date, order_key)" in call for call in execute_calls)


@patch("benchbox.platforms.databricks.adapter.databricks_sql")
@patch("databricks.sdk.WorkspaceClient")
def test_platform_metadata_includes_clustering_strategy_and_operations(_mock_workspace_client, _mock_databricks_sql):
    adapter = DatabricksAdapter(
        server_hostname="test.cloud.databricks.com",
        http_path="/sql/1.0/warehouses/test",
        access_token="test_token",
        catalog="main",
        schema="benchbox",
    )

    unified = UnifiedTuningConfiguration()
    unified.platform_optimizations.databricks_clustering_strategy = "liquid_clustering"
    unified.platform_optimizations.liquid_clustering_enabled = True
    unified.platform_optimizations.liquid_clustering_columns = ["event_time"]
    adapter.unified_tuning_configuration = unified

    adapter._liquid_clustering_operations.append(
        {
            "table": "test_table",
            "columns": ["event_time"],
            "statement": "ALTER TABLE test_table CLUSTER BY (event_time)",
        }
    )

    mock_cursor = Mock()
    mock_cursor.fetchone.side_effect = [["Spark 3.5.0"], ["main", "benchbox"]]
    mock_cursor.fetchall.side_effect = [[["current_catalog"]], []]
    mock_connection = Mock()
    mock_connection.cursor.return_value = mock_cursor

    metadata = adapter._get_platform_metadata(mock_connection)
    assert metadata["databricks_clustering_strategy"] == "liquid_clustering"
    assert metadata["liquid_clustering_enabled"] is True
    assert metadata["liquid_clustering_columns_config"] == ["event_time"]
    assert metadata["liquid_clustering_operations"] == [
        {
            "table": "test_table",
            "columns": ["event_time"],
            "statement": "ALTER TABLE test_table CLUSTER BY (event_time)",
        }
    ]
    assert metadata["z_order_operations"] == []
    assert metadata["requested_databricks_clustering_strategy"] == "liquid_clustering"
    assert metadata["resolved_databricks_clustering_strategy"] == "liquid_clustering"
    assert metadata["applied_layout_operations"] == []
    assert metadata["skipped_layout_operations"] == []

    platform_info = adapter.get_platform_info(mock_connection)
    assert platform_info["configuration"]["requested_databricks_clustering_strategy"] == "liquid_clustering"
    assert platform_info["configuration"]["resolved_databricks_clustering_strategy"] == "liquid_clustering"
    assert platform_info["configuration"]["databricks_clustering_strategy"] == "liquid_clustering"
    assert platform_info["configuration"]["liquid_clustering_enabled"] is True
    assert platform_info["configuration"]["liquid_clustering_columns_config"] == ["event_time"]
    assert platform_info["configuration"]["liquid_clustering_operations"] == [
        {
            "table": "test_table",
            "columns": ["event_time"],
            "statement": "ALTER TABLE test_table CLUSTER BY (event_time)",
        }
    ]
    assert platform_info["configuration"]["z_order_operations"] == []
    assert platform_info["configuration"]["applied_layout_operations"] == []
    assert platform_info["configuration"]["skipped_layout_operations"] == []


@patch("benchbox.platforms.databricks.adapter.databricks_sql")
def test_mcp_clustering_options_reach_the_effective_resolver(_mock_databricks_sql):

    from benchbox.core.run_service import translate_platform_options_for_adapter as _prepare_adapter_platform_options
    from benchbox.mcp.schemas import validate_platform_options

    normalized = validate_platform_options(
        "databricks",
        {"databricks_clustering_strategy": "liquid_clustering", "liquid_clustering_columns": "event_time,customer_id"},
    )
    prepared = _prepare_adapter_platform_options("databricks", normalized)

    adapter = DatabricksAdapter.from_config(
        {
            "benchmark": "tpch",
            "scale_factor": 1.0,
            "server_hostname": "test.cloud.databricks.com",
            "http_path": "/sql/1.0/warehouses/test",
            "access_token": "test_token",
            **prepared,
        }
    )

    effective = adapter.get_effective_tuning_configuration()
    assert effective is not None
    platform_opts = effective.platform_optimizations
    assert platform_opts.databricks_clustering_strategy == "liquid_clustering"
    assert platform_opts.liquid_clustering_columns == ["event_time", "customer_id"]
    assert platform_opts.liquid_clustering_enabled is True
    assert adapter._resolve_databricks_clustering_strategy() == "liquid_clustering"


@patch("benchbox.platforms.databricks.adapter.databricks_sql")
def test_mcp_columns_alone_infer_liquid_clustering_at_the_resolver(_mock_databricks_sql):
    from benchbox.core.run_service import translate_platform_options_for_adapter as _prepare_adapter_platform_options
    from benchbox.mcp.schemas import validate_platform_options

    normalized = validate_platform_options("databricks", {"liquid_clustering_columns": "event_time"})
    prepared = _prepare_adapter_platform_options("databricks", normalized)

    adapter = DatabricksAdapter.from_config(
        {
            "benchmark": "tpch",
            "scale_factor": 1.0,
            "server_hostname": "test.cloud.databricks.com",
            "http_path": "/sql/1.0/warehouses/test",
            "access_token": "test_token",
            **prepared,
        }
    )

    assert adapter._resolve_databricks_clustering_strategy() == "liquid_clustering"
    assert adapter.get_effective_tuning_configuration().platform_optimizations.liquid_clustering_columns == [
        "event_time"
    ]


@patch("benchbox.platforms.databricks.adapter.databricks_sql")
def test_absent_mcp_clustering_options_resolve_to_none(_mock_databricks_sql):

    adapter = DatabricksAdapter.from_config(
        {
            "benchmark": "tpch",
            "scale_factor": 1.0,
            "server_hostname": "test.cloud.databricks.com",
            "http_path": "/sql/1.0/warehouses/test",
            "access_token": "test_token",
        }
    )

    assert adapter._resolve_databricks_clustering_strategy() == "none"
    assert adapter.get_effective_tuning_configuration() is None

    from benchbox.cli.tuning_runtime import build_baseline_unified_config

    adapter.unified_tuning_configuration = build_baseline_unified_config()

    assert adapter._resolve_databricks_clustering_strategy() == "none"
    assert adapter.get_effective_tuning_configuration().platform_optimizations.databricks_clustering_strategy == "none"
