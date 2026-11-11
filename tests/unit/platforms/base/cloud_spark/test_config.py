# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.platforms.base.cloud_spark.config import (
    BenchmarkType,
    CloudPlatform,
    SparkAQEConfig,
    SparkConfig,
    SparkConfigOptimizer,
    SparkParallelismConfig,
    SparkResourceConfig,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestSparkResourceConfig:
    def test_default_values(self):

        config = SparkResourceConfig()

        assert config.driver_memory == "4g"
        assert config.driver_cores == 2
        assert config.executor_memory == "4g"
        assert config.executor_cores == 2
        assert config.num_executors is None
        assert config.memory_fraction == 0.6

    def test_custom_values(self):

        config = SparkResourceConfig(
            driver_memory="8g",
            executor_memory="16g",
            executor_cores=4,
            num_executors=10,
        )

        assert config.driver_memory == "8g"
        assert config.executor_memory == "16g"
        assert config.executor_cores == 4
        assert config.num_executors == 10


class TestSparkParallelismConfig:
    def test_default_values(self):

        config = SparkParallelismConfig()

        assert config.shuffle_partitions == 200
        assert config.adaptive_enabled is True
        assert config.coalesce_partitions is True

    def test_custom_values(self):

        config = SparkParallelismConfig(
            shuffle_partitions=500,
            default_parallelism=500,
            adaptive_enabled=False,
        )

        assert config.shuffle_partitions == 500
        assert config.adaptive_enabled is False


class TestSparkConfig:
    def test_default_config_to_dict(self):

        config = SparkConfig()
        result = config.to_dict()

        assert "spark.driver.memory" in result
        assert "spark.executor.memory" in result
        assert "spark.sql.shuffle.partitions" in result
        assert "spark.sql.adaptive.enabled" in result

    def test_to_dict_values(self):

        config = SparkConfig(
            resources=SparkResourceConfig(
                driver_memory="8g",
                executor_memory="16g",
            ),
            parallelism=SparkParallelismConfig(
                shuffle_partitions=1000,
            ),
            aqe=SparkAQEConfig(
                enabled=True,
                skew_join_enabled=True,
            ),
        )
        result = config.to_dict()

        assert result["spark.driver.memory"] == "8g"
        assert result["spark.executor.memory"] == "16g"
        assert result["spark.sql.shuffle.partitions"] == "1000"
        assert result["spark.sql.adaptive.enabled"] == "true"
        assert result["spark.sql.adaptive.skewJoin.enabled"] == "true"

    def test_to_dict_dynamic_allocation(self):

        config = SparkConfig(resources=SparkResourceConfig(num_executors=None))
        result = config.to_dict()

        assert result["spark.dynamicAllocation.enabled"] == "true"

    def test_to_dict_fixed_executors(self):

        config = SparkConfig(resources=SparkResourceConfig(num_executors=10))
        result = config.to_dict()

        assert result["spark.dynamicAllocation.enabled"] == "false"
        assert result["spark.executor.instances"] == "10"

    def test_to_dict_extra_configs(self):

        config = SparkConfig(
            extra={
                "spark.custom.setting": "value",
                "spark.another.setting": "123",
            }
        )
        result = config.to_dict()

        assert result["spark.custom.setting"] == "value"
        assert result["spark.another.setting"] == "123"


class TestSparkConfigOptimizerTPCH:
    def test_tpch_small_scale(self):

        config = SparkConfigOptimizer.for_tpch(scale_factor=0.01)

        assert config.resources.driver_memory == "2g"
        assert config.resources.executor_memory == "2g"
        assert config.aqe.enabled is True

    def test_tpch_medium_scale(self):

        config = SparkConfigOptimizer.for_tpch(scale_factor=1.0)

        assert config.resources.driver_memory == "2g"
        assert config.resources.executor_memory == "2g"
        assert config.parallelism.shuffle_partitions >= 50

    def test_tpch_large_scale(self):

        config = SparkConfigOptimizer.for_tpch(scale_factor=100)

        assert config.resources.driver_memory == "8g"
        assert config.resources.executor_memory == "8g"
        assert config.parallelism.shuffle_partitions >= 200

    def test_tpch_with_platform_string(self):

        config = SparkConfigOptimizer.for_tpch(
            scale_factor=1.0,
            platform="databricks",
        )

        result = config.to_dict()
        assert "spark.databricks.optimizer.dynamicFilePruning" in result

    def test_tpch_with_fixed_executors(self):

        config = SparkConfigOptimizer.for_tpch(
            scale_factor=1.0,
            num_executors=5,
        )

        assert config.resources.num_executors == 5


class TestSparkConfigOptimizerTPCDS:
    def test_tpcds_small_scale(self):

        config = SparkConfigOptimizer.for_tpcds(scale_factor=1.0)

        assert config.aqe.enabled is True
        assert config.aqe.skew_join_enabled is True

    def test_tpcds_large_scale(self):

        config = SparkConfigOptimizer.for_tpcds(scale_factor=100)

        assert config.resources.driver_memory != "2g"
        assert config.io.broadcast_timeout == 600

    def test_tpcds_more_partitions_than_tpch(self):

        tpch_config = SparkConfigOptimizer.for_tpch(scale_factor=10)
        tpcds_config = SparkConfigOptimizer.for_tpcds(scale_factor=10)

        assert tpcds_config.parallelism.shuffle_partitions >= tpch_config.parallelism.shuffle_partitions


class TestSparkConfigOptimizerSSB:
    def test_ssb_smaller_than_tpch(self):

        ssb_config = SparkConfigOptimizer.for_ssb(scale_factor=10)

        assert ssb_config.parallelism.shuffle_partitions <= 200


class TestSparkConfigOptimizerPlatformOptimizations:
    def test_databricks_optimizations(self):

        config = SparkConfigOptimizer.for_tpch(
            scale_factor=1.0,
            platform=CloudPlatform.DATABRICKS,
        )
        result = config.to_dict()

        assert result.get("spark.databricks.optimizer.dynamicFilePruning") == "true"
        assert result.get("spark.databricks.delta.optimizeWrite.enabled") == "true"

    def test_emr_optimizations(self):

        config = SparkConfigOptimizer.for_tpch(
            scale_factor=1.0,
            platform=CloudPlatform.EMR,
        )
        result = config.to_dict()

        assert result.get("spark.sql.optimizer.dynamicPartitionPruning.enabled") == "true"
        assert result.get("spark.emr.optimized.parquet.io.enabled") == "true"

    def test_glue_optimizations(self):

        config = SparkConfigOptimizer.for_tpch(
            scale_factor=1.0,
            platform=CloudPlatform.GLUE,
        )

        assert config.resources.memory_overhead_factor == 0.2


class TestSparkConfigOptimizerHelpers:
    def test_calculate_shuffle_partitions_small(self):

        partitions = SparkConfigOptimizer._calculate_shuffle_partitions(0.1)

        assert partitions >= 50

    def test_calculate_shuffle_partitions_large(self):

        partitions = SparkConfigOptimizer._calculate_shuffle_partitions(100)

        assert partitions > 200
        assert partitions <= 2000

    def test_calculate_shuffle_partitions_with_complexity(self):

        base = SparkConfigOptimizer._calculate_shuffle_partitions(10)
        complex = SparkConfigOptimizer._calculate_shuffle_partitions(10, complexity_factor=2.0)

        assert complex >= base

    def test_increase_memory(self):

        result = SparkConfigOptimizer._increase_memory("4g", 1.5)
        assert result == "6g"

        result = SparkConfigOptimizer._increase_memory("8g", 2.0)
        assert result == "16g"

    def test_from_dict(self):

        input_dict = {
            "spark.driver.memory": "8g",
            "spark.executor.memory": "16g",
            "spark.sql.shuffle.partitions": "500",
        }

        config = SparkConfigOptimizer.from_dict(input_dict)

        assert config.extra["spark.driver.memory"] == "8g"
        assert config.extra["spark.sql.shuffle.partitions"] == "500"


class TestCloudPlatformEnum:
    def test_all_platforms_defined(self):

        platforms = [p.value for p in CloudPlatform]

        assert "databricks" in platforms
        assert "emr" in platforms
        assert "dataproc" in platforms
        assert "synapse" in platforms
        assert "glue" in platforms
        assert "fabric" in platforms
        assert "local" in platforms


class TestOptimizerAdaptiveToggle:
    AQE_KEYS = (
        "spark.sql.adaptive.enabled",
        "spark.sql.adaptive.coalescePartitions.enabled",
        "spark.sql.adaptive.skewJoin.enabled",
        "spark.sql.adaptive.localShuffleReader.enabled",
    )

    def test_tpch_disables_all_three_aqe_keys(self):
        config = SparkConfigOptimizer.for_tpch(scale_factor=1.0, adaptive_enabled=False)
        rendered = config.to_dict()
        assert [rendered[key] for key in self.AQE_KEYS] == ["false"] * 4
        assert config.parallelism.adaptive_enabled is False

    def test_tpch_defaults_to_aqe_on(self):
        rendered = SparkConfigOptimizer.for_tpch(scale_factor=1.0).to_dict()
        assert [rendered[key] for key in self.AQE_KEYS] == ["true"] * 4

    def test_tpcds_disables_all_three_aqe_keys(self):
        config = SparkConfigOptimizer.for_tpcds(scale_factor=1.0, adaptive_enabled=False)
        rendered = config.to_dict()
        assert [rendered[key] for key in self.AQE_KEYS] == ["false"] * 4

    def test_ssb_forwards_toggle_to_tpch(self):
        config = SparkConfigOptimizer.for_ssb(scale_factor=1.0, adaptive_enabled=False)
        rendered = config.to_dict()
        assert [rendered[key] for key in self.AQE_KEYS] == ["false"] * 4


class TestBenchmarkTypeEnum:
    def test_all_benchmarks_defined(self):

        benchmarks = [b.value for b in BenchmarkType]

        assert "tpch" in benchmarks
        assert "tpcds" in benchmarks
        assert "ssb" in benchmarks
        assert "clickbench" in benchmarks
        assert "custom" in benchmarks


