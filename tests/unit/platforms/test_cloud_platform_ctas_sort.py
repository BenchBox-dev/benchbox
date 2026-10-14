from __future__ import annotations

import logging
from unittest.mock import Mock

import pytest

from benchbox.platforms.athena import AthenaAdapter
from benchbox.platforms.azure_synapse import AzureSynapseAdapter
from benchbox.platforms.bigquery import BigQueryAdapter
from benchbox.platforms.clickhouse_cloud import ClickHouseCloudAdapter
from benchbox.platforms.databricks.adapter import DatabricksAdapter
from benchbox.platforms.redshift import RedshiftAdapter
from benchbox.platforms.snowflake import SnowflakeAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.cloud_import,
]


def _make_sort_column(name: str, order: int) -> Mock:
    col = Mock()
    col.name = name
    col.order = order
    return col


def _make_tuning_config(table_name: str, sort_columns: list[Mock]) -> Mock:
    from benchbox.core.tuning.interface import TuningType

    table_tuning = Mock()

    def get_columns_by_type(tuning_type):
        if tuning_type == TuningType.SORTING:
            return sort_columns
        return []

    table_tuning.get_columns_by_type.side_effect = get_columns_by_type

    config = Mock()
    config.table_tunings = {table_name: table_tuning}
    return config


def _prepare_minimal_adapter(adapter):
    adapter.logger = logging.getLogger("test-cloud-ctas-sort")
    adapter.dry_run_mode = False
    adapter.capture_sql = Mock()
    adapter.log_verbose = Mock()
    return adapter


def _force_sorted_ingestion(adapter, method: str) -> None:
    from benchbox.core.tuning.interface import UnifiedTuningConfiguration

    config = UnifiedTuningConfiguration()
    config.platform_optimizations.sorted_ingestion_mode = "force"
    config.platform_optimizations.sorted_ingestion_method = method
    adapter.unified_tuning_configuration = config


@pytest.mark.parametrize(
    "adapter_cls",
    [
        SnowflakeAdapter,
        DatabricksAdapter,
        BigQueryAdapter,
        AthenaAdapter,
        AzureSynapseAdapter,
        ClickHouseCloudAdapter,
    ],
)
def test_unsupported_cloud_platforms_return_none(adapter_cls):
    adapter = adapter_cls.__new__(adapter_cls)
    sql = adapter._build_ctas_sort_sql("lineitem", [_make_sort_column("l_shipdate", 1)])
    assert sql is None


def test_redshift_returns_none_sql():
    adapter = RedshiftAdapter.__new__(RedshiftAdapter)
    sql = adapter._build_ctas_sort_sql("lineitem", [_make_sort_column("l_shipdate", 1)])
    assert sql is None


@pytest.mark.parametrize(
    "adapter_cls",
    [
        SnowflakeAdapter,
        DatabricksAdapter,
        BigQueryAdapter,
        AthenaAdapter,
        AzureSynapseAdapter,
        ClickHouseCloudAdapter,
    ],
)
def test_unsupported_cloud_apply_ctas_sort_has_no_sql_side_effects(adapter_cls):
    adapter = _prepare_minimal_adapter(adapter_cls.__new__(adapter_cls))
    connection = Mock()
    tuning_config = _make_tuning_config("lineitem", [_make_sort_column("l_shipdate", 1)])

    assert adapter.apply_ctas_sort("lineitem", tuning_config, connection) is False
    connection.execute.assert_not_called()


def test_redshift_apply_ctas_sort_has_no_sql_side_effects():
    adapter = _prepare_minimal_adapter(RedshiftAdapter.__new__(RedshiftAdapter))
    connection = Mock()
    tuning_config = _make_tuning_config("lineitem", [_make_sort_column("l_shipdate", 1)])

    assert adapter.apply_ctas_sort("lineitem", tuning_config, connection) is False
    connection.execute.assert_not_called()


def test_snowflake_force_mode_builds_ctas_sql():
    adapter = SnowflakeAdapter.__new__(SnowflakeAdapter)
    _force_sorted_ingestion(adapter, method="ctas")

    sql = adapter._build_ctas_sort_sql("lineitem", [_make_sort_column("l_shipdate", 1)])
    assert sql == "CREATE OR REPLACE TABLE lineitem AS SELECT * FROM lineitem ORDER BY l_shipdate"


def test_databricks_force_mode_supports_z_order_method():
    adapter = DatabricksAdapter.__new__(DatabricksAdapter)
    _force_sorted_ingestion(adapter, method="z_order")

    sql = adapter._build_ctas_sort_sql("lineitem", [_make_sort_column("l_shipdate", 1)])
    assert sql == "OPTIMIZE lineitem ZORDER BY (l_shipdate)"


def test_databricks_force_mode_apply_ctas_sort_executes_sql():
    adapter = _prepare_minimal_adapter(DatabricksAdapter.__new__(DatabricksAdapter))
    _force_sorted_ingestion(adapter, method="ctas")
    connection = Mock()
    tuning_config = _make_tuning_config("lineitem", [_make_sort_column("l_shipdate", 1)])

    assert adapter.apply_ctas_sort("lineitem", tuning_config, connection) is True
    connection.execute.assert_called_once_with(
        "CREATE OR REPLACE TABLE lineitem AS SELECT * FROM lineitem ORDER BY l_shipdate"
    )


def test_redshift_force_mode_supports_vacuum_sort():
    adapter = RedshiftAdapter.__new__(RedshiftAdapter)
    adapter.schema = "public"
    _force_sorted_ingestion(adapter, method="vacuum_sort")

    sql = adapter._build_ctas_sort_sql("lineitem", [_make_sort_column("l_shipdate", 1)])
    assert sql == "VACUUM SORT ONLY public.lineitem"


def test_redshift_force_mode_disables_ctas_rewrite():
    adapter = RedshiftAdapter.__new__(RedshiftAdapter)
    adapter.schema = "public"
    _force_sorted_ingestion(adapter, method="ctas")

    sql = adapter._build_ctas_sort_sql(
        "lineitem", [_make_sort_column("l_shipdate", 1), _make_sort_column("l_orderkey", 2)]
    )
    assert sql is None
    assert "SORTKEY" in adapter._ctas_sort_unsupported_reason("ctas")


def test_bigquery_force_mode_raises_actionable_error():
    adapter = BigQueryAdapter.__new__(BigQueryAdapter)
    _force_sorted_ingestion(adapter, method="ctas")

    with pytest.raises(ValueError, match="not yet executable through this CTAS hook"):
        adapter._build_ctas_sort_sql("lineitem", [_make_sort_column("l_shipdate", 1)])


def test_athena_force_mode_disables_ctas_sql():
    adapter = AthenaAdapter.__new__(AthenaAdapter)
    _force_sorted_ingestion(adapter, method="ctas")

    sql = adapter._build_ctas_sort_sql("lineitem", [_make_sort_column("l_shipdate", 1)])
    assert sql is None


def test_synapse_force_mode_disables_ctas_sql():
    adapter = AzureSynapseAdapter.__new__(AzureSynapseAdapter)
    adapter.schema = "dbo"
    _force_sorted_ingestion(adapter, method="ctas")

    sql = adapter._build_ctas_sort_sql("lineitem", [_make_sort_column("l_shipdate", 1)])
    assert sql is None


def test_clickhouse_cloud_force_mode_raises_actionable_error():
    adapter = ClickHouseCloudAdapter.__new__(ClickHouseCloudAdapter)
    _force_sorted_ingestion(adapter, method="ctas")

    with pytest.raises(ValueError, match="does not support post-load sorted ingestion"):
        adapter._build_ctas_sort_sql("lineitem", [_make_sort_column("l_shipdate", 1)])
