# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from benchbox.core.tuning.ddl_generator import ColumnDefinition
from benchbox.core.tuning.interface import (
    ForeignKeyConfiguration,
    PrimaryKeyConfiguration,
    TableTuning,
    TuningColumn,
    TuningType,
    UnifiedTuningConfiguration,
)
from benchbox.platforms.base.cloud_spark.config import CloudPlatform
from benchbox.platforms.base.cloud_spark.mixins import (
    CloudSparkConfigMixin,
    SparkDDLGeneratorMixin,
    SparkTableFormat,
    SparkTuningMixin,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class StubSparkTuning(SparkTuningMixin):
    pass


class StubCloudSparkConfig(CloudSparkConfigMixin):
    cloud_platform = CloudPlatform.DATAPROC_SERVERLESS
    _benchmark_type: str | None = None
    _scale_factor: float = 1.0
    _spark_config: dict = {}


class StubSparkDDL(SparkDDLGeneratorMixin):
    table_format = SparkTableFormat.PARQUET


class StubDeltaDDL(SparkDDLGeneratorMixin):
    table_format = SparkTableFormat.DELTA


class StubIcebergDDL(SparkDDLGeneratorMixin):
    table_format = SparkTableFormat.ICEBERG


class StubHudiDDL(SparkDDLGeneratorMixin):
    table_format = SparkTableFormat.HUDI


class StubHiveDDL(SparkDDLGeneratorMixin):
    table_format = SparkTableFormat.HIVE


def _col(name: str, col_type: str = "INTEGER", order: int = 1) -> TuningColumn:
    return TuningColumn(name=name, type=col_type, order=order)


def _table_tuning(
    *,
    partitioning=None,
    clustering=None,
    distribution=None,
    sorting=None,
) -> TableTuning:
    return TableTuning(
        table_name="test_table",
        partitioning=partitioning,
        clustering=clustering,
        distribution=distribution,
        sorting=sorting,
    )


class TestSparkTuningMixin:
    def test_apply_primary_keys_disabled_returns_empty(self):
        mixin = StubSparkTuning()
        cfg = PrimaryKeyConfiguration(enabled=False)
        result = mixin.apply_primary_keys(cfg)
        assert result == []

    def test_apply_primary_keys_enabled_returns_empty_logs(self, caplog):
        mixin = StubSparkTuning()
        cfg = PrimaryKeyConfiguration(enabled=True)
        with caplog.at_level("INFO"):
            result = mixin.apply_primary_keys(cfg)
        assert result == []
        assert "Primary keys noted" in caplog.text

    def test_apply_primary_keys_none_returns_empty(self):
        mixin = StubSparkTuning()
        result = mixin.apply_primary_keys(None)
        assert result == []

    def test_apply_foreign_keys_disabled_returns_empty(self):
        mixin = StubSparkTuning()
        cfg = ForeignKeyConfiguration(enabled=False)
        result = mixin.apply_foreign_keys(cfg)
        assert result == []

    def test_apply_foreign_keys_enabled_returns_empty_logs(self, caplog):
        mixin = StubSparkTuning()
        cfg = ForeignKeyConfiguration(enabled=True)
        with caplog.at_level("INFO"):
            result = mixin.apply_foreign_keys(cfg)
        assert result == []
        assert "Foreign keys noted" in caplog.text

    def test_apply_foreign_keys_none_returns_empty(self):
        mixin = StubSparkTuning()
        result = mixin.apply_foreign_keys(None)
        assert result == []

    def test_apply_platform_optimizations_always_empty(self):
        mixin = StubSparkTuning()
        result = mixin.apply_platform_optimizations(MagicMock())
        assert result == []

    def test_apply_tuning_configuration_uses_unified_platform_optimization_field(self):
        mixin = StubSparkTuning()
        mixin.apply_primary_keys = MagicMock(return_value=[])
        mixin.apply_foreign_keys = MagicMock(return_value=[])
        mixin.apply_platform_optimizations = MagicMock(return_value=["spark.conf.example=true"])
        config = UnifiedTuningConfiguration()
        config.platform_optimizations.auto_optimize_enabled = True

        result = mixin.apply_tuning_configuration(config)

        mixin.apply_platform_optimizations.assert_called_once_with(config.platform_optimizations)
        assert result["platform_optimizations"] == ["spark.conf.example=true"]

    def test_apply_constraint_configuration_both_enabled(self, caplog):
        mixin = StubSparkTuning()
        pk_cfg = PrimaryKeyConfiguration(enabled=True)
        fk_cfg = ForeignKeyConfiguration(enabled=True)
        with caplog.at_level("INFO"):
            mixin.apply_constraint_configuration(pk_cfg, fk_cfg, connection=None)
        assert "Primary key constraints noted" in caplog.text
        assert "Foreign key constraints noted" in caplog.text

    def test_apply_constraint_configuration_both_disabled(self, caplog):
        mixin = StubSparkTuning()
        pk_cfg = PrimaryKeyConfiguration(enabled=False)
        fk_cfg = ForeignKeyConfiguration(enabled=False)
        with caplog.at_level("INFO"):
            mixin.apply_constraint_configuration(pk_cfg, fk_cfg, connection=None)
        assert "Primary key constraints noted" not in caplog.text
        assert "Foreign key constraints noted" not in caplog.text

    def test_apply_constraint_configuration_none_configs(self):
        mixin = StubSparkTuning()
        mixin.apply_constraint_configuration(None, None, connection=None)


class TestCloudSparkConfigMixin:
    @pytest.fixture(autouse=True)
    def _mock_optimizer(self):
        mock_config = MagicMock()
        mock_config.to_dict.return_value = {"spark.executor.memory": "4g"}
        with patch("benchbox.platforms.base.cloud_spark.mixins.SparkConfigOptimizer") as mock_cls:
            mock_cls.for_tpch.return_value = mock_config
            mock_cls.for_tpcds.return_value = mock_config
            mock_cls.for_ssb.return_value = mock_config
            self.mock_optimizer = mock_cls
            self.mock_config = mock_config
            yield

    def test_configure_for_tpch(self):
        mixin = StubCloudSparkConfig()
        mixin.configure_for_benchmark(connection=None, benchmark_type="tpch")
        self.mock_optimizer.for_tpch.assert_called_once()
        assert mixin._benchmark_type == "tpch"
        assert mixin._spark_config == {"spark.executor.memory": "4g"}

    def test_configure_for_tpcds(self):
        mixin = StubCloudSparkConfig()
        mixin.configure_for_benchmark(connection=None, benchmark_type="tpcds")
        self.mock_optimizer.for_tpcds.assert_called_once()
        assert mixin._benchmark_type == "tpcds"

    def test_configure_for_ssb(self):
        mixin = StubCloudSparkConfig()
        mixin.configure_for_benchmark(connection=None, benchmark_type="ssb")
        self.mock_optimizer.for_ssb.assert_called_once()
        assert mixin._benchmark_type == "ssb"

    def test_configure_for_unknown_falls_back_to_tpch(self):
        mixin = StubCloudSparkConfig()
        mixin.configure_for_benchmark(connection=None, benchmark_type="UNKNOWN_BENCH")
        self.mock_optimizer.for_tpch.assert_called_once()
        assert mixin._benchmark_type == "unknown_bench"

    def test_benchmark_type_lowercased(self):
        mixin = StubCloudSparkConfig()
        mixin.configure_for_benchmark(connection=None, benchmark_type="TPC-H")
        assert mixin._benchmark_type == "tpc-h"

    def test_toggle_flows_into_optimizer(self):
        mixin = StubCloudSparkConfig()
        mixin.adaptive_enabled = False
        mixin.configure_for_benchmark(connection=None, benchmark_type="tpch")
        self.mock_optimizer.for_tpch.assert_called_once_with(
            scale_factor=mixin._scale_factor,
            platform=mixin.cloud_platform,
            adaptive_enabled=False,
        )

    def test_missing_toggle_defaults_to_aqe_on(self):
        mixin = StubCloudSparkConfig()
        mixin.configure_for_benchmark(connection=None, benchmark_type="tpch")
        self.mock_optimizer.for_tpch.assert_called_once_with(
            scale_factor=mixin._scale_factor,
            platform=mixin.cloud_platform,
            adaptive_enabled=True,
        )

    def test_explicit_user_entries_win_over_optimizer(self):
        mixin = StubCloudSparkConfig()
        mixin._spark_config = {"spark.sql.adaptive.enabled": "false", "custom.key": "keep"}
        self.mock_config.to_dict.return_value = {
            "spark.sql.adaptive.enabled": "true",
            "other.key": "opt",
        }
        mixin.configure_for_benchmark(connection=None, benchmark_type="tpch")
        assert mixin._spark_config == {
            "spark.sql.adaptive.enabled": "false",
            "other.key": "opt",
            "custom.key": "keep",
        }

    def test_reconfigure_does_not_resurrect_stale_output(self):
        mixin = StubCloudSparkConfig()
        self.mock_config.to_dict.return_value = {"k": "v1"}
        mixin.configure_for_benchmark(connection=None, benchmark_type="tpch")
        assert mixin._spark_config == {"k": "v1"}
        self.mock_config.to_dict.return_value = {"k": "v2"}
        mixin.configure_for_benchmark(connection=None, benchmark_type="tpcds")
        assert mixin._spark_config == {"k": "v2"}


class TestSparkTableFormat:
    def test_enum_values(self):
        assert SparkTableFormat.DELTA.value == "delta"
        assert SparkTableFormat.ICEBERG.value == "iceberg"
        assert SparkTableFormat.HUDI.value == "hudi"
        assert SparkTableFormat.PARQUET.value == "parquet"
        assert SparkTableFormat.HIVE.value == "hive"

    def test_is_str_enum(self):
        assert isinstance(SparkTableFormat.DELTA, str)


class TestGetTableFormat:
    def test_default_returns_class_format(self):
        mixin = StubSparkDDL()
        assert mixin.get_table_format() == SparkTableFormat.PARQUET

    def test_platform_opts_override(self):
        mixin = StubSparkDDL()
        opts = MagicMock()
        opts.table_format = "delta"
        result = mixin.get_table_format(opts)
        assert result == SparkTableFormat.DELTA

    def test_platform_opts_invalid_format_falls_back(self):
        mixin = StubSparkDDL()
        opts = MagicMock()
        opts.table_format = "nonexistent_format"
        result = mixin.get_table_format(opts)
        assert result == SparkTableFormat.PARQUET

    def test_platform_opts_none_table_format_attr(self):
        mixin = StubSparkDDL()
        opts = MagicMock()
        opts.table_format = None
        result = mixin.get_table_format(opts)
        assert result == SparkTableFormat.PARQUET


class TestGenerateTableLayoutRouting:
    def test_routes_to_delta(self):
        assert StubDeltaDDL().generate_table_layout(None)["format"] == "delta"

    def test_routes_to_iceberg(self):
        assert StubIcebergDDL().generate_table_layout(None) == {"format": "iceberg"}

    def test_routes_to_hudi(self):
        assert StubHudiDDL().generate_table_layout(None)["format"] == "hudi"

    def test_routes_to_parquet(self):
        assert StubSparkDDL().generate_table_layout(None) == {"format": "parquet"}

    def test_routes_to_hive(self):
        assert StubHiveDDL().generate_table_layout(None) == {"stored_as": "PARQUET"}

    def test_unknown_format_returns_empty_layout(self):
        mixin = StubSparkDDL()

        def _fake_get_table_format(platform_opts=None):
            return object()

        mixin.get_table_format = _fake_get_table_format
        assert mixin.generate_table_layout(None) == {}


class TestDeltaLayout:
    def test_no_tuning_adds_auto_optimize_props(self):
        layout = StubDeltaDDL()._delta_layout(None)
        assert layout["properties"]["delta.autoOptimize.optimizeWrite"] == "true"
        assert layout["properties"]["delta.autoOptimize.autoCompact"] == "true"

    def test_no_tuning_auto_optimize_disabled(self):
        opts = MagicMock()
        opts.enable_auto_optimize = False
        assert "properties" not in StubDeltaDDL()._delta_layout(None, opts)

    def test_partition_columns(self):
        tt = _table_tuning(partitioning=[_col("l_shipdate", "DATE", order=1)])
        assert StubDeltaDDL()._delta_layout(tt)["partition_by"] == ["l_shipdate"]

    def test_cluster_by_liquid_clustering(self):
        tt = _table_tuning(clustering=[_col("l_orderkey", "BIGINT", order=1)])
        assert StubDeltaDDL()._delta_layout(tt)["cluster_by"] == ["l_orderkey"]

    def test_cluster_by_zorder_when_liquid_disabled(self):
        mixin = StubDeltaDDL()
        opts = MagicMock()
        opts.use_liquid_clustering = False
        opts.enable_auto_optimize = True
        tt = _table_tuning(clustering=[_col("l_orderkey", "BIGINT", order=1)])
        assert "cluster_by" not in mixin._delta_layout(tt, opts)
        assert mixin.get_post_load_statements("lineitem", tt, opts) == ["OPTIMIZE lineitem ZORDER BY (l_orderkey)"]

    def test_distribution_zorder_when_no_cluster_cols(self):
        mixin = StubDeltaDDL()
        tt = _table_tuning(distribution=[_col("l_orderkey", "BIGINT", order=1)])
        assert "cluster_by" not in mixin._delta_layout(tt)
        assert mixin.get_post_load_statements("lineitem", tt) == ["OPTIMIZE lineitem ZORDER BY (l_orderkey)"]

    def test_sort_columns_added_to_cluster(self):
        tt = _table_tuning(sorting=[_col("l_shipdate", "DATE", order=1)])
        assert StubDeltaDDL()._delta_layout(tt)["cluster_by"] == ["l_shipdate"]

    def test_auto_optimize_false_via_platform_opts(self):
        opts = MagicMock()
        opts.enable_auto_optimize = False
        opts.use_liquid_clustering = True
        tt = _table_tuning(partitioning=[_col("l_shipdate", "DATE", order=1)])
        assert "properties" not in StubDeltaDDL()._delta_layout(tt, opts)


class TestIcebergLayout:
    def test_no_tuning_returns_format_only(self):
        assert StubIcebergDDL()._iceberg_layout(None) == {"format": "iceberg"}

    def test_date_column_gets_months_transform(self):
        tt = _table_tuning(partitioning=[_col("o_orderdate", "DATE", order=1)])
        assert StubIcebergDDL()._iceberg_layout(tt)["partition_by"] == ["months(o_orderdate)"]

    def test_timestamp_column_gets_days_transform(self):
        tt = _table_tuning(partitioning=[_col("ts_col", "TIMESTAMP", order=1)])
        assert StubIcebergDDL()._iceberg_layout(tt)["partition_by"] == ["days(ts_col)"]

    def test_int_column_gets_bucket_transform(self):
        mixin = StubIcebergDDL()
        tt = _table_tuning(partitioning=[_col("o_orderkey", "INTEGER", order=1)])
        assert mixin._iceberg_layout(tt)["partition_by"] == [f"bucket({mixin.default_bucket_count}, o_orderkey)"]

    def test_string_column_gets_identity_transform(self):
        tt = _table_tuning(partitioning=[_col("nation_name", "VARCHAR", order=1)])
        assert StubIcebergDDL()._iceberg_layout(tt)["partition_by"] == ["nation_name"]

    def test_explicit_partition_transform_via_platform_opts(self):
        opts = MagicMock()
        opts.partition_transforms = {"o_orderdate": "years(o_orderdate)"}
        tt = _table_tuning(partitioning=[_col("o_orderdate", "DATE", order=1)])
        assert StubIcebergDDL()._iceberg_layout(tt, opts)["partition_by"] == ["years(o_orderdate)"]

    def test_distribution_bucket_when_no_partition(self):
        mixin = StubIcebergDDL()
        tt = _table_tuning(distribution=[_col("o_orderkey", "INTEGER", order=1)])
        assert mixin._iceberg_layout(tt)["partition_by"] == [f"bucket({mixin.default_bucket_count}, o_orderkey)"]

    def test_distribution_bucket_count_from_opts(self):
        opts = MagicMock()
        opts.bucket_count = 16
        opts.partition_transforms = {}
        tt = _table_tuning(distribution=[_col("o_orderkey", "INTEGER", order=1)])
        assert StubIcebergDDL()._iceberg_layout(tt, opts)["partition_by"] == ["bucket(16, o_orderkey)"]

    def test_sort_columns_added_to_write_sort_order(self):
        tt = _table_tuning(sorting=[_col("l_shipdate", "DATE", order=1)])
        assert StubIcebergDDL()._iceberg_layout(tt)["properties"] == {"write.sort-order": "l_shipdate ASC"}

    def test_clustering_columns_added_to_sort_order(self):
        tt = _table_tuning(clustering=[_col("l_orderkey", "BIGINT", order=1)])
        assert "write.sort-order" in StubIcebergDDL()._iceberg_layout(tt)["properties"]

    def test_partition_transforms_dict_missing_key_falls_to_auto_detect(self):
        opts = MagicMock()
        opts.partition_transforms = {"other_col": "years(other_col)"}
        tt = _table_tuning(partitioning=[_col("o_orderdate", "DATE", order=1)])
        assert StubIcebergDDL()._iceberg_layout(tt, opts)["partition_by"] == ["months(o_orderdate)"]


class TestHudiLayout:
    def test_no_tuning_sets_required_properties(self):
        layout = StubHudiDDL()._hudi_layout(None)
        assert layout["format"] == "hudi"
        assert layout["properties"]["hoodie.table.type"] == "COPY_ON_WRITE"
        assert layout["properties"]["hoodie.datasource.write.operation"] == "upsert"

    def test_record_key_from_platform_opts(self):
        opts = MagicMock()
        opts.record_key = "id"
        opts.precombine_field = "ts"
        opts.hudi_table_type = "MERGE_ON_READ"
        opts.hudi_write_operation = "insert"
        properties = StubHudiDDL()._hudi_layout(None, opts)["properties"]
        assert properties["hoodie.datasource.write.recordkey.field"] == "id"
        assert properties["hoodie.datasource.write.precombine.field"] == "ts"
        assert properties["hoodie.table.type"] == "MERGE_ON_READ"

    def test_partition_columns_enable_hive_style(self):
        tt = _table_tuning(partitioning=[_col("o_orderdate", "DATE", order=1)])
        layout = StubHudiDDL()._hudi_layout(tt)
        assert layout["partition_by"] == ["o_orderdate"]
        assert layout["properties"]["hoodie.datasource.write.hive_style_partitioning"] == "true"

    def test_distribution_col_becomes_record_key_when_no_explicit_key(self):
        tt = _table_tuning(distribution=[_col("o_orderkey", "INTEGER", order=1)])
        properties = StubHudiDDL()._hudi_layout(tt)["properties"]
        assert properties.get("hoodie.datasource.write.recordkey.field") == "o_orderkey"

    def test_sorting_enables_inline_clustering(self):
        tt = _table_tuning(sorting=[_col("l_shipdate", "DATE", order=1)])
        properties = StubHudiDDL()._hudi_layout(tt)["properties"]
        assert properties["hoodie.clustering.inline"] == "true"
        assert "l_shipdate" in properties["hoodie.clustering.plan.strategy.sort.columns"]

    def test_clustering_columns_enable_inline_clustering(self):
        tt = _table_tuning(clustering=[_col("l_orderkey", "BIGINT", order=1)])
        assert StubHudiDDL()._hudi_layout(tt)["properties"]["hoodie.clustering.inline"] == "true"


class TestParquetLayout:
    def test_no_tuning_returns_format_only(self):
        assert StubSparkDDL()._parquet_layout(None) == {"format": "parquet"}

    def test_partition_columns(self):
        tt = _table_tuning(partitioning=[_col("l_shipdate", "DATE", order=1)])
        assert StubSparkDDL()._parquet_layout(tt)["partition_by"] == ["l_shipdate"]

    def test_distribution_columns_bucketed(self):
        mixin = StubSparkDDL()
        tt = _table_tuning(distribution=[_col("o_orderkey", "INTEGER", order=1)])
        layout = mixin._parquet_layout(tt)
        assert layout["distribute_by"] == ["o_orderkey"]
        assert layout["buckets"] == mixin.default_bucket_count

    def test_distribution_bucket_count_override(self):
        opts = MagicMock()
        opts.bucket_count = 64
        tt = _table_tuning(distribution=[_col("o_orderkey", "INTEGER", order=1)])
        assert StubSparkDDL()._parquet_layout(tt, opts)["buckets"] == 64

    def test_sort_columns_basic(self):
        tt = _table_tuning(sorting=[_col("l_shipdate", "DATE", order=1)])
        assert StubSparkDDL()._parquet_layout(tt)["sort_by"] == ["l_shipdate"]

    def test_sort_column_with_desc(self):
        col = TuningColumn(name="l_shipdate", type="DATE", order=1, sort_order="DESC")
        assert StubSparkDDL()._parquet_layout(_table_tuning(sorting=[col]))["sort_by"] == ["l_shipdate DESC"]

    def test_sort_column_with_nulls_position(self):
        col = TuningColumn(name="l_shipdate", type="DATE", order=1, nulls_position="FIRST")
        assert StubSparkDDL()._parquet_layout(_table_tuning(sorting=[col]))["sort_by"] == ["l_shipdate NULLS FIRST"]

    def test_clustering_columns_merged_into_sort(self):
        tt = _table_tuning(clustering=[_col("l_orderkey", "BIGINT", order=1)])
        assert StubSparkDDL()._parquet_layout(tt)["sort_by"] == ["l_orderkey"]


class TestHiveLayout:
    def test_no_tuning_defaults_stored_as_parquet(self):
        assert StubHiveDDL()._hive_layout(None) == {"stored_as": "PARQUET"}

    def test_storage_format_override(self):
        opts = MagicMock()
        opts.storage_format = "ORC"
        assert StubHiveDDL()._hive_layout(None, opts) == {"stored_as": "ORC"}

    def test_partition_columns(self):
        tt = _table_tuning(partitioning=[_col("l_shipdate", "DATE", order=1)])
        assert StubHiveDDL()._hive_layout(tt)["partition_by"] == ["l_shipdate"]

    def test_distribution_columns_bucketed(self):
        mixin = StubHiveDDL()
        tt = _table_tuning(distribution=[_col("o_orderkey", "INTEGER", order=1)])
        layout = mixin._hive_layout(tt)
        assert layout["distribute_by"] == ["o_orderkey"]
        assert layout["buckets"] == mixin.default_bucket_count

    def test_distribution_with_sorting_adds_sort_by(self):
        tt = _table_tuning(
            distribution=[_col("o_orderkey", "INTEGER", order=1)],
            sorting=[_col("l_shipdate", "DATE", order=2)],
        )
        assert StubHiveDDL()._hive_layout(tt)["sort_by"] == ["l_shipdate"]

    def test_bucket_count_override(self):
        opts = MagicMock()
        opts.bucket_count = 8
        opts.storage_format = "PARQUET"
        tt = _table_tuning(distribution=[_col("o_orderkey", "INTEGER", order=1)])
        assert StubHiveDDL()._hive_layout(tt, opts)["buckets"] == 8


class TestGenerateCreateTableDDL:
    def _columns(self):
        return [
            ColumnDefinition(name="id", data_type="BIGINT"),
            ColumnDefinition(name="name", data_type="VARCHAR(255)"),
        ]

    def test_basic_ddl_no_layout(self):
        ddl = StubSparkDDL().generate_create_table_ddl("my_table", self._columns())
        assert ddl == "CREATE TABLE my_table (id BIGINT, name VARCHAR(255))"

    def test_if_not_exists(self):
        ddl = StubSparkDDL().generate_create_table_ddl("my_table", self._columns(), if_not_exists=True)
        assert "IF NOT EXISTS" in ddl

    def test_schema_prefix(self):
        ddl = StubSparkDDL().generate_create_table_ddl("my_table", self._columns(), schema="my_schema")
        assert "my_schema.my_table" in ddl

    def test_delta_layout_with_partitioning(self):
        mixin = StubDeltaDDL()
        tt = _table_tuning(partitioning=[_col("l_shipdate", "DATE", order=1)])
        ddl = mixin.generate_create_table_ddl("lineitem", self._columns(), layout=mixin.generate_table_layout(tt))
        assert "USING DELTA" in ddl
        assert "PARTITIONED BY (l_shipdate)" in ddl

    def test_tblproperties_rendered(self):
        mixin = StubDeltaDDL()
        ddl = mixin.generate_create_table_ddl("lineitem", self._columns(), layout=mixin.generate_table_layout(None))
        assert "TBLPROPERTIES" in ddl

    def test_parquet_distribution_renders_clustered_by(self):
        mixin = StubSparkDDL()
        tt = _table_tuning(distribution=[_col("id", "INTEGER", order=1)], sorting=[_col("name", "VARCHAR", order=1)])
        ddl = mixin.generate_create_table_ddl("orders", self._columns(), layout=mixin.generate_table_layout(tt))
        assert "USING PARQUET" in ddl
        assert "CLUSTERED BY (id)" in ddl
        assert "SORTED BY (name)" in ddl

    def test_cluster_by_rendered(self):
        mixin = StubDeltaDDL()
        tt = _table_tuning(clustering=[_col("id", "BIGINT", order=1)])
        ddl = mixin.generate_create_table_ddl("lineitem", self._columns(), layout=mixin.generate_table_layout(tt))
        assert "CLUSTER BY (id)" in ddl

    def test_hive_layout_renders_stored_as(self):
        mixin = StubHiveDDL()
        ddl = mixin.generate_create_table_ddl("orders", self._columns(), layout=mixin.generate_table_layout(None))
        assert ddl.endswith("STORED AS PARQUET")


class TestGetPostLoadStatements:
    def test_no_zorder_returns_empty(self):
        assert StubDeltaDDL().get_post_load_statements("lineitem", _table_tuning()) == []

    def test_none_tuning_returns_empty(self):
        assert StubDeltaDDL().get_post_load_statements("lineitem", None) == []

    def test_non_delta_format_returns_empty(self):
        tt = _table_tuning(distribution=[_col("id", "INTEGER", order=1)])
        assert StubSparkDDL().get_post_load_statements("lineitem", tt) == []

    def test_zorder_statement_uses_table_name(self):
        tt = _table_tuning(distribution=[_col("id", "INTEGER", order=1)])
        assert StubDeltaDDL().get_post_load_statements("lineitem", tt) == ["OPTIMIZE lineitem ZORDER BY (id)"]

    def test_schema_prefix_in_post_load(self):
        tt = _table_tuning(distribution=[_col("id", "INTEGER", order=1)])
        result = StubDeltaDDL().get_post_load_statements("lineitem", tt, schema="tpch")
        assert result == ["OPTIMIZE tpch.lineitem ZORDER BY (id)"]


class TestSupportsTuningType:
    def test_supported_types(self):
        mixin = StubSparkDDL()
        for t in ("partitioning", "clustering", "sorting", "distribution"):
            assert mixin.supports_tuning_type(t) is True

    def test_unsupported_type(self):
        mixin = StubSparkDDL()
        assert mixin.supports_tuning_type("primary_keys") is False

    def test_case_insensitive(self):
        mixin = StubSparkDDL()
        assert mixin.supports_tuning_type("PARTITIONING") is True
