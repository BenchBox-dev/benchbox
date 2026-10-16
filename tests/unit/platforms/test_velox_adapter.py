from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestVeloxAdapterInit:
    @pytest.fixture
    def mock_pyspark(self):

        mock_spark_session = MagicMock()
        mock_builder = MagicMock()
        mock_builder.master.return_value = mock_builder
        mock_builder.remote.return_value = mock_builder
        mock_builder.config.return_value = mock_builder
        mock_builder.getOrCreate.return_value = mock_spark_session

        mock_spark_session.version = "3.5.0"
        mock_spark_session.catalog.listDatabases.return_value = []
        mock_spark_session.sql.return_value = MagicMock()

        mock_session_class = MagicMock()
        mock_session_class.builder = mock_builder

        with patch.dict(
            "sys.modules",
            {
                "pyspark": MagicMock(),
                "pyspark.sql": MagicMock(SparkSession=mock_session_class),
                "pyspark.sql.types": MagicMock(
                    StructType=MagicMock(),
                    StructField=MagicMock(),
                    StringType=MagicMock(),
                    IntegerType=MagicMock(),
                    LongType=MagicMock(),
                    DoubleType=MagicMock(),
                ),
            },
        ):
            yield mock_session_class, mock_spark_session

    def test_platform_name(self, mock_pyspark):
        from benchbox.platforms.velox import VeloxAdapter

        assert VeloxAdapter().platform_name == "Velox"

    def test_defaults(self, mock_pyspark):
        from benchbox.platforms.velox import VeloxAdapter

        adapter = VeloxAdapter()
        assert adapter.deployment == "local"
        assert adapter.endpoint == "sc://localhost:50051"
        assert adapter.gluten_jar_path == ""
        assert adapter.gluten_version == "1.6.0"
        assert adapter.offheap_size == "8g"
        assert adapter.app_name == "BenchBox-Velox"
        assert adapter.database == "default"
        assert adapter.driver_memory == "4g"
        assert adapter.shuffle_partitions is None
        assert adapter.adaptive_enabled is None
        assert adapter.table_format == "parquet"
        assert adapter.disable_cache is True

    def test_custom_config(self, mock_pyspark):
        from benchbox.platforms.velox import VeloxAdapter

        adapter = VeloxAdapter(
            deployment="remote",
            endpoint="sc://myserver:50051",
            gluten_jar_path="/opt/gluten.jar",
            gluten_version="1.4.1",
            offheap_size="16g",
            app_name="MyApp",
            database="bench",
            driver_memory="8g",
            shuffle_partitions=400,
            adaptive_enabled=False,
            table_format="orc",
            disable_cache=False,
        )
        assert adapter.deployment == "remote"
        assert adapter.endpoint == "sc://myserver:50051"
        assert adapter.gluten_jar_path == "/opt/gluten.jar"
        assert adapter.gluten_version == "1.4.1"
        assert adapter.offheap_size == "16g"
        assert adapter.app_name == "MyApp"
        assert adapter.database == "bench"
        assert adapter.driver_memory == "8g"
        assert adapter.shuffle_partitions == 400
        assert adapter.adaptive_enabled is False
        assert adapter.table_format == "orc"
        assert adapter.disable_cache is False

    def test_deployment_mode_alias(self, mock_pyspark):

        from benchbox.platforms.velox import VeloxAdapter

        adapter = VeloxAdapter(deployment_mode="remote")
        assert adapter.deployment == "remote"

    @pytest.mark.parametrize("key", ["deployment", "deployment_mode"])
    def test_constructor_rejects_docker_through_either_spelling(self, mock_pyspark, key):

        from benchbox.platforms.velox import VeloxAdapter

        with pytest.raises(ValueError, match="Unsupported Velox deployment 'docker'"):
            VeloxAdapter(**{key: "docker"})

    def test_supported_deployments_still_select_their_own_endpoints(self, mock_pyspark):

        from benchbox.platforms.velox import VeloxAdapter

        local = VeloxAdapter(deployment="local")
        assert local.deployment == "local"
        assert local._df_caching_supported is True

        remote = VeloxAdapter(deployment="remote", endpoint="sc://remote:50051")
        assert remote.deployment == "remote"
        assert remote.endpoint == "sc://remote:50051"
        assert remote._df_caching_supported is False

    def test_get_target_dialect(self, mock_pyspark):
        from benchbox.platforms.velox import VeloxAdapter

        assert VeloxAdapter().get_target_dialect() == "spark"


class TestVeloxSparkConf:
    @pytest.fixture
    def mock_pyspark(self):
        with patch.dict(
            "sys.modules",
            {
                "pyspark": MagicMock(),
                "pyspark.sql": MagicMock(SparkSession=MagicMock()),
                "pyspark.sql.types": MagicMock(),
            },
        ):
            yield

    def test_local_mode_includes_gluten_conf(self, mock_pyspark):
        from benchbox.platforms.velox import VeloxAdapter

        adapter = VeloxAdapter(
            deployment="local",
            gluten_jar_path="/opt/gluten.jar",
            offheap_size="12g",
        )
        conf = adapter._get_spark_conf()

        assert conf["spark.plugins"] == "org.apache.gluten.GlutenPlugin"
        assert conf["spark.memory.offHeap.enabled"] == "true"
        assert conf["spark.memory.offHeap.size"] == "12g"
        assert conf["spark.shuffle.manager"] == "org.apache.spark.shuffle.sort.ColumnarShuffleManager"
        assert conf["spark.jars"] == "/opt/gluten.jar"

        assert conf["spark.driver.extraClassPath"] == "/opt/gluten.jar"
        assert conf["spark.executor.extraClassPath"] == "/opt/gluten.jar"

    def test_remote_mode_excludes_gluten_conf(self, mock_pyspark):
        from benchbox.platforms.velox import VeloxAdapter

        adapter = VeloxAdapter(deployment="remote", endpoint="sc://remote:50051")
        conf = adapter._get_spark_conf()

        assert "spark.plugins" not in conf
        assert "spark.memory.offHeap.enabled" not in conf
        assert "spark.shuffle.manager" not in conf

    def test_aqe_uses_engine_default(self, mock_pyspark):
        from benchbox.platforms.velox import VeloxAdapter

        conf = VeloxAdapter()._get_spark_conf()
        assert "spark.sql.adaptive.enabled" not in conf
        assert "spark.sql.adaptive.coalescePartitions.enabled" not in conf

    def test_aqe_disabled(self, mock_pyspark):
        from benchbox.platforms.velox import VeloxAdapter

        conf = VeloxAdapter(adaptive_enabled=False)._get_spark_conf()

        assert conf["spark.sql.adaptive.enabled"] == "false"
        assert conf["spark.sql.adaptive.coalescePartitions.enabled"] == "false"
        assert conf["spark.sql.adaptive.skewJoin.enabled"] == "false"

    def test_aqe_disabled_spark_config_still_wins(self, mock_pyspark):
        from benchbox.platforms.velox import VeloxAdapter

        adapter = VeloxAdapter(adaptive_enabled=False, spark_config={"spark.sql.adaptive.enabled": "true"})
        conf = adapter._get_spark_conf()
        assert conf["spark.sql.adaptive.enabled"] == "true"

    def test_cache_disabled_in_every_mode(self, mock_pyspark):
        from benchbox.platforms.velox import VeloxAdapter

        conf = VeloxAdapter()._get_spark_conf()
        assert conf["spark.sql.inMemoryColumnarStorage.enabled"] == "false"

    def test_spark_config_overrides_merged(self, mock_pyspark):
        from benchbox.platforms.velox import VeloxAdapter

        adapter = VeloxAdapter(spark_config={"spark.foo.bar": "baz"})
        conf = adapter._get_spark_conf()
        assert conf["spark.foo.bar"] == "baz"

    def test_columnar_shuffle_override_rejected_in_local_mode(self, mock_pyspark):
        from benchbox.platforms.velox import VeloxAdapter

        adapter = VeloxAdapter(
            deployment="local",
            gluten_jar_path="/opt/gluten.jar",
            spark_config={"spark.shuffle.manager": "org.apache.spark.shuffle.sort.SortShuffleManager"},
        )
        with pytest.raises(ValueError, match="ColumnarShuffleManager"):
            adapter._get_spark_conf()


class TestVeloxTableFormatConf:
    @pytest.fixture
    def mock_pyspark(self):
        with patch.dict(
            "sys.modules",
            {
                "pyspark": MagicMock(),
                "pyspark.sql": MagicMock(SparkSession=MagicMock()),
                "pyspark.sql.types": MagicMock(),
            },
        ):
            yield

    def test_parquet_default_emits_no_format_keys(self, mock_pyspark):
        from benchbox.platforms.velox import VeloxAdapter

        conf = VeloxAdapter()._get_spark_conf()
        assert "spark.sql.extensions" not in conf
        assert "spark.sql.catalog.spark_catalog" not in conf

    def test_orc_emits_no_format_keys(self, mock_pyspark):
        from benchbox.platforms.velox import VeloxAdapter

        conf = VeloxAdapter(table_format="orc")._get_spark_conf()
        assert "spark.sql.extensions" not in conf
        assert "spark.sql.catalog.spark_catalog" not in conf

    def test_delta_conf(self, mock_pyspark):
        from benchbox.platforms.velox import VeloxAdapter

        conf = VeloxAdapter(
            table_format="delta",
            lakehouse_jars="io.delta:delta-spark_2.12:3.2.0",
        )._get_spark_conf()
        assert conf["spark.sql.extensions"] == "io.delta.sql.DeltaSparkSessionExtension"
        assert conf["spark.sql.catalog.spark_catalog"] == "org.apache.spark.sql.delta.catalog.DeltaCatalog"
        assert "io.delta:delta-spark_2.12:3.2.0" in conf["spark.jars"]

    def test_iceberg_conf(self, mock_pyspark):
        from benchbox.platforms.velox import VeloxAdapter

        conf = VeloxAdapter(
            table_format="iceberg",
            lakehouse_jars="org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.5.0",
        )._get_spark_conf()
        assert conf["spark.sql.extensions"] == "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions"
        assert conf["spark.sql.catalog.spark_catalog"] == "org.apache.iceberg.spark.SparkSessionCatalog"
        assert conf["spark.sql.catalog.spark_catalog.type"] == "hive"

    def test_hudi_conf(self, mock_pyspark):
        from benchbox.platforms.velox import VeloxAdapter

        conf = VeloxAdapter(
            table_format="hudi",
            lakehouse_jars="org.apache.hudi:hudi-spark-bundle:1.0.0",
        )._get_spark_conf()
        assert conf["spark.sql.extensions"] == "org.apache.spark.sql.hudi.HoodieSparkSessionExtension"
        assert conf["spark.sql.catalog.spark_catalog"] == "org.apache.spark.sql.hudi.catalog.HoodieCatalog"

    def test_lakehouse_format_requires_jars_in_local_mode(self, mock_pyspark):
        from benchbox.platforms.velox import VeloxAdapter

        with pytest.raises(ValueError, match="requires connector jars"):
            VeloxAdapter(table_format="delta")._get_spark_conf()

    def test_missing_local_jar_path_rejected(self, mock_pyspark, tmp_path):
        from benchbox.platforms.velox import VeloxAdapter

        with pytest.raises(ValueError, match="not found"):
            VeloxAdapter(table_format="iceberg", lakehouse_jars=str(tmp_path / "missing.jar"))._get_spark_conf()

    @pytest.mark.parametrize(
        ("jar", "is_local"),
        [
            ("/opt/jars/delta.jar", True),
            ("relative/delta.jar", True),
            ("C:\\jars\\delta.jar", True),
            ("d:/jars/delta.jar", True),
            ("io.delta:delta-spark_2.12:3.2.0", False),
            ("s3://bucket/delta.jar", False),
            ("https://repo.example/delta.jar", False),
        ],
    )
    def test_only_filesystem_paths_are_checked_for_existence(self, jar, is_local):
        from benchbox.platforms.velox import _is_local_jar_path

        assert _is_local_jar_path(jar) is is_local

    def test_parquet_needs_no_jars(self, mock_pyspark):
        from benchbox.platforms.velox import VeloxAdapter

        conf = VeloxAdapter(table_format="parquet")._get_spark_conf()
        assert "spark.jars" not in conf

    def test_format_conf_applies_in_remote_mode(self, mock_pyspark):
        from benchbox.platforms.velox import VeloxAdapter

        conf = VeloxAdapter(deployment="remote", table_format="delta")._get_spark_conf()
        assert conf["spark.sql.extensions"] == "io.delta.sql.DeltaSparkSessionExtension"

    def test_spark_config_override_still_wins(self, mock_pyspark):
        from benchbox.platforms.velox import VeloxAdapter

        adapter = VeloxAdapter(
            table_format="delta",
            lakehouse_jars="io.delta:delta-spark_2.12:3.2.0",
            spark_config={"spark.sql.extensions": "com.example.CustomExtensions"},
        )
        assert adapter._get_spark_conf()["spark.sql.extensions"] == "com.example.CustomExtensions"

    def test_unsupported_format_rejected(self, mock_pyspark):
        from benchbox.platforms.velox import VeloxAdapter

        with pytest.raises(ValueError, match="Unsupported Velox table_format"):
            VeloxAdapter(table_format="clickhouse")

    def test_format_name_is_case_insensitive(self, mock_pyspark):
        from benchbox.platforms.velox import VeloxAdapter

        adapter = VeloxAdapter(table_format="Delta", lakehouse_jars="io.delta:delta-spark_2.12:3.2.0")
        assert adapter.table_format == "delta"
        assert (
            adapter._get_spark_conf()["spark.sql.catalog.spark_catalog"]
            == "org.apache.spark.sql.delta.catalog.DeltaCatalog"
        )


class TestVeloxConfigureForBenchmark:
    @pytest.fixture
    def mock_pyspark(self):
        with patch.dict(
            "sys.modules",
            {
                "pyspark": MagicMock(),
                "pyspark.sql": MagicMock(SparkSession=MagicMock()),
                "pyspark.sql.types": MagicMock(),
            },
        ):
            yield MagicMock()

    def test_olap_leaves_engine_cbo_defaults(self, mock_pyspark):
        from benchbox.platforms.velox import VeloxAdapter

        mock_session = MagicMock()
        VeloxAdapter().configure_for_benchmark(mock_session, "tpch")

        mock_session.conf.set.assert_not_called()

    def test_tuned_run_applies_olap_session_defaults(self, mock_pyspark):
        from benchbox.platforms.velox import VeloxAdapter

        mock_session = MagicMock()
        adapter = VeloxAdapter(tuning_enabled=True)

        conf = adapter._get_spark_conf()
        adapter.configure_for_benchmark(mock_session, "tpch")

        assert conf["spark.sql.shuffle.partitions"] == "200"
        assert conf["spark.sql.adaptive.enabled"] == "true"
        mock_session.conf.set.assert_any_call("spark.sql.cbo.enabled", "true")

    def test_explicit_spark_config_keeps_cbo_override(self, mock_pyspark):
        from benchbox.platforms.velox import VeloxAdapter

        adapter = VeloxAdapter(spark_config={"spark.sql.cbo.enabled": "false"})
        assert adapter._get_spark_conf()["spark.sql.cbo.enabled"] == "false"


class TestVeloxLocalModeValidation:
    @pytest.fixture
    def mock_pyspark(self):
        with patch.dict(
            "sys.modules",
            {
                "pyspark": MagicMock(),
                "pyspark.sql": MagicMock(SparkSession=MagicMock()),
                "pyspark.sql.types": MagicMock(),
            },
        ):
            yield

    def test_missing_jar_path_raises(self, mock_pyspark):
        from benchbox.platforms.velox import VeloxAdapter

        adapter = VeloxAdapter(deployment="local", gluten_jar_path="")
        with pytest.raises(ValueError, match="gluten_jar_path is required"):
            adapter._create_spark_session()

    def test_nonexistent_jar_raises(self, mock_pyspark, tmp_path):
        from benchbox.platforms.velox import VeloxAdapter

        adapter = VeloxAdapter(
            deployment="local",
            gluten_jar_path=str(tmp_path / "nonexistent.jar"),
        )
        with pytest.raises(ValueError, match="Gluten jar not found"):
            adapter._create_spark_session()

    def test_valid_jar_path_proceeds(self, mock_pyspark, tmp_path):
        import benchbox.platforms.velox as velox_module
        from benchbox.platforms.velox import VeloxAdapter

        jar = tmp_path / "gluten.jar"
        jar.write_bytes(b"fake-jar")

        mock_session_class = MagicMock()
        mock_builder = MagicMock()
        mock_builder.master.return_value = mock_builder
        mock_builder.config.return_value = mock_builder
        mock_builder.getOrCreate.return_value = MagicMock()
        mock_session_class.builder = mock_builder

        adapter = VeloxAdapter(deployment="local", gluten_jar_path=str(jar))
        with patch.object(velox_module, "SparkSession", mock_session_class):
            session = adapter._create_spark_session()
        assert session is not None
        mock_builder.master.assert_called_once_with("local[*]")


class TestVeloxRemoteMode:
    @pytest.fixture
    def mock_pyspark(self):
        with patch.dict(
            "sys.modules",
            {
                "pyspark": MagicMock(),
                "pyspark.sql": MagicMock(SparkSession=MagicMock()),
                "pyspark.sql.types": MagicMock(),
            },
        ):
            yield

    def test_parse_endpoint_default(self, mock_pyspark):
        from benchbox.platforms._spark_helpers import parse_spark_connect_endpoint
        from benchbox.platforms.velox import VeloxAdapter

        adapter = VeloxAdapter(deployment="remote")
        host, port = parse_spark_connect_endpoint(adapter.endpoint)
        assert host == "localhost"
        assert port == 50051

    def test_parse_endpoint_custom(self, mock_pyspark):
        from benchbox.platforms._spark_helpers import parse_spark_connect_endpoint
        from benchbox.platforms.velox import VeloxAdapter

        adapter = VeloxAdapter(deployment="remote", endpoint="sc://myhost:12345")
        host, port = parse_spark_connect_endpoint(adapter.endpoint)
        assert host == "myhost"
        assert port == 12345

    def test_ensure_server_ready_raises_when_unreachable(self, mock_pyspark):
        from benchbox.platforms.velox import VeloxAdapter

        adapter = VeloxAdapter(deployment="remote", endpoint="sc://unreachable-host:50051")
        with patch("benchbox.platforms.velox.is_spark_connect_reachable", return_value=False):
            with pytest.raises(RuntimeError, match="Cannot connect"):
                adapter._ensure_server_ready()

    def test_ensure_server_ready_passes_when_reachable(self, mock_pyspark):
        from benchbox.platforms.velox import VeloxAdapter

        adapter = VeloxAdapter(deployment="remote")
        with patch("benchbox.platforms.velox.is_spark_connect_reachable", return_value=True):
            adapter._ensure_server_ready()


class TestVeloxPlatformInfo:
    @pytest.fixture
    def mock_pyspark(self):
        with patch.dict(
            "sys.modules",
            {
                "pyspark": MagicMock(__version__="3.5.0"),
                "pyspark.sql": MagicMock(SparkSession=MagicMock()),
                "pyspark.sql.types": MagicMock(),
            },
        ):
            yield

    def test_platform_info_no_connection(self, mock_pyspark):
        from benchbox.platforms.velox import VeloxAdapter

        adapter = VeloxAdapter(gluten_version="1.6.0", offheap_size="8g")
        info = adapter.get_platform_info(connection=None)

        assert info["platform_type"] == "velox"
        assert info["platform_name"] == "Apache Gluten + Velox"
        assert info["gluten_version"] == "1.6.0"
        assert info["offheap_size"] == "8g"
        assert info["velox_active"] is None
        assert info["platform_version"] is None
        receipt = info["configuration"]["execution_engine"]
        assert receipt["observed"] == "not_captured"
        assert receipt["observed_source"] == "none"

    def test_platform_info_remote_has_endpoint(self, mock_pyspark):
        from benchbox.platforms.velox import VeloxAdapter

        adapter = VeloxAdapter(deployment="remote", endpoint="sc://host:50051")
        info = adapter.get_platform_info(connection=None)

        assert info["deployment"] == "remote"
        assert info["endpoint"] == "sc://host:50051"

    def test_platform_info_local_has_jar_name(self, mock_pyspark):
        from benchbox.platforms.velox import VeloxAdapter

        adapter = VeloxAdapter(deployment="local", gluten_jar_path="/opt/gluten-velox-1.6.0.jar")
        info = adapter.get_platform_info(connection=None)

        assert info["deployment"] == "local"
        assert "gluten-velox-1.6.0.jar" in info["gluten_jar"]
        assert "/opt/" not in info["gluten_jar"]

    def test_velox_active_probe_detects_velox_nodes(self, mock_pyspark):
        from benchbox.platforms.velox import VeloxAdapter

        mock_spark = MagicMock()
        mock_spark.version = "3.5.0"
        mock_df = MagicMock()
        mock_df.collect.return_value = [("VeloxColumnarToRow\nScan",)]
        mock_spark.sql.return_value = mock_df

        adapter = VeloxAdapter()
        info = adapter.get_platform_info(connection=mock_spark)

        assert info["velox_active"] is True
        receipt = info["configuration"]["execution_engine"]
        assert receipt["observed"] == "velox"
        assert receipt["observed_source"] == "explain"

    def test_velox_active_probe_false_without_velox_nodes(self, mock_pyspark):
        from benchbox.platforms.velox import VeloxAdapter

        mock_spark = MagicMock()
        mock_spark.version = "3.5.0"
        mock_df = MagicMock()
        mock_df.collect.return_value = [("HashAggregate\nFileScan",)]
        mock_spark.sql.return_value = mock_df

        adapter = VeloxAdapter()
        info = adapter.get_platform_info(connection=mock_spark)

        assert info["velox_active"] is False
        receipt = info["configuration"]["execution_engine"]
        assert receipt["observed"] == "standard"
        assert receipt["observed_source"] == "explain"


class TestVeloxQueryPlan:
    @pytest.fixture
    def mock_pyspark(self):
        with patch.dict(
            "sys.modules",
            {
                "pyspark": MagicMock(),
                "pyspark.sql": MagicMock(SparkSession=MagicMock()),
                "pyspark.sql.types": MagicMock(),
            },
        ):
            yield

    def test_velox_native_annotation(self, mock_pyspark):
        from benchbox.platforms.velox import VeloxAdapter

        mock_spark = MagicMock()
        mock_df = MagicMock()
        mock_df.collect.return_value = [("VeloxColumnarToRow\nVeloxColumnarHashAggregate",)]
        mock_spark.sql.return_value = mock_df

        adapter = VeloxAdapter()
        plan = adapter.get_query_plan(mock_spark, "SELECT count(*) FROM t")

        assert "Velox native execution: YES" in plan

        assert "JVM fallback: DETECTED" not in plan

    def test_fallback_annotation(self, mock_pyspark):
        from benchbox.platforms.velox import VeloxAdapter

        mock_spark = MagicMock()
        mock_df = MagicMock()

        mock_df.collect.return_value = [("VeloxColumnarHashAggregate\nColumnarToRow\nHashAgg",)]
        mock_spark.sql.return_value = mock_df

        adapter = VeloxAdapter()
        plan = adapter.get_query_plan(mock_spark, "SELECT count(*) FROM t")

        assert "JVM fallback: DETECTED" in plan

    def test_row_to_columnar_is_fallback(self, mock_pyspark):
        from benchbox.platforms.velox import VeloxAdapter

        mock_spark = MagicMock()
        mock_df = MagicMock()
        mock_df.collect.return_value = [("VeloxColumnarHashAggregate\nRowToColumnar\nScan",)]
        mock_spark.sql.return_value = mock_df

        adapter = VeloxAdapter()
        plan = adapter.get_query_plan(mock_spark, "SELECT count(*) FROM t")

        assert "JVM fallback: DETECTED" in plan

    def test_no_velox_annotation(self, mock_pyspark):
        from benchbox.platforms.velox import VeloxAdapter

        mock_spark = MagicMock()
        mock_df = MagicMock()
        mock_df.collect.return_value = [("HashAggregate\nFileScan",)]
        mock_spark.sql.return_value = mock_df

        adapter = VeloxAdapter()
        plan = adapter.get_query_plan(mock_spark, "SELECT count(*) FROM t")

        assert "NOT DETECTED" in plan

    def test_explain_failure_returns_none(self, mock_pyspark):

        from benchbox.platforms.velox import VeloxAdapter

        mock_spark = MagicMock()
        mock_spark.sql.side_effect = RuntimeError("explain failed")

        adapter = VeloxAdapter()
        assert adapter.get_query_plan(mock_spark, "SELECT count(*) FROM t") is None

    def test_empty_plan_rows_return_none(self, mock_pyspark):

        from benchbox.platforms.velox import VeloxAdapter

        mock_df = MagicMock()
        mock_df.collect.return_value = []
        mock_spark = MagicMock()
        mock_spark.sql.return_value = mock_df

        adapter = VeloxAdapter()
        assert adapter.get_query_plan(mock_spark, "SELECT count(*) FROM t") is None


class TestVeloxFromConfig:
    @pytest.fixture
    def mock_pyspark(self):
        with patch.dict(
            "sys.modules",
            {
                "pyspark": MagicMock(),
                "pyspark.sql": MagicMock(SparkSession=MagicMock()),
                "pyspark.sql.types": MagicMock(),
            },
        ):
            yield

    def test_from_config_basic(self, mock_pyspark):
        from benchbox.platforms.velox import VeloxAdapter

        config = {
            "benchmark": "tpch",
            "scale_factor": 1.0,
            "gluten_jar_path": "/opt/gluten.jar",
            "offheap_size": "16g",
        }
        adapter = VeloxAdapter.from_config(config)

        assert adapter.gluten_jar_path == "/opt/gluten.jar"
        assert adapter.offheap_size == "16g"
        assert "tpch" in adapter.database.lower() or adapter.database

    def test_from_config_explicit_database(self, mock_pyspark):
        from benchbox.platforms.velox import VeloxAdapter

        config = {
            "benchmark": "tpch",
            "scale_factor": 1.0,
            "database": "my_db",
        }
        adapter = VeloxAdapter.from_config(config)
        assert adapter.database == "my_db"

    def test_from_config_remote_deployment(self, mock_pyspark):
        from benchbox.platforms.velox import VeloxAdapter

        config = {
            "benchmark": "tpch",
            "scale_factor": 1.0,
            "deployment": "remote",
            "endpoint": "sc://velox-server:50051",
        }
        adapter = VeloxAdapter.from_config(config)
        assert adapter.deployment == "remote"
        assert adapter.endpoint == "sc://velox-server:50051"


class TestVeloxCLIArguments:
    def test_add_cli_arguments_is_noop(self):
        from benchbox.platforms.velox import VeloxAdapter

        parser = argparse.ArgumentParser()
        VeloxAdapter.add_cli_arguments(parser)

        actions = [a for a in parser._actions if a.dest != "help"]
        assert actions == []

    def test_option_specs_registered(self):
        from benchbox.cli.platform_hooks import PlatformHookRegistry

        specs = PlatformHookRegistry.list_option_specs("velox")

        for name in (
            "deployment",
            "endpoint",
            "gluten_jar_path",
            "offheap_size",
            "driver_memory",
            "shuffle_partitions",
            "adaptive_enabled",
        ):
            assert name in specs, f"Velox option-spec '{name}' is missing"
        assert specs["deployment"].choices == ("local", "remote")


class TestVeloxIdentifierValidation:
    def test_valid_identifiers(self):
        from benchbox.platforms._spark_helpers import validate_spark_identifier

        assert validate_spark_identifier("my_table") is True
        assert validate_spark_identifier("_priv") is True
        assert validate_spark_identifier("T123") is True

    def test_invalid_identifiers(self):
        from benchbox.platforms._spark_helpers import validate_spark_identifier

        assert validate_spark_identifier("") is False
        assert validate_spark_identifier("123bad") is False
        assert validate_spark_identifier("a-b") is False
        assert validate_spark_identifier("a; DROP TABLE t") is False


class TestVeloxTuning:
    @pytest.fixture
    def mock_pyspark(self):
        with patch.dict(
            "sys.modules",
            {
                "pyspark": MagicMock(),
                "pyspark.sql": MagicMock(SparkSession=MagicMock()),
                "pyspark.sql.types": MagicMock(),
            },
        ):
            yield

    def test_supports_partitioning_only(self, mock_pyspark):

        from benchbox.platforms.velox import VeloxAdapter

        adapter = VeloxAdapter()
        try:
            from benchbox.core.tuning.interface import TuningType

            assert adapter.supports_tuning_type(TuningType.PARTITIONING) is True
            assert adapter.supports_tuning_type(TuningType.SORTING) is False
            assert adapter.supports_tuning_type(TuningType.CLUSTERING) is False
        except ImportError:
            pytest.skip("TuningType not available")


class TestVeloxTableOptimization:
    def test_parquet_format(self):
        from benchbox.platforms._spark_helpers import optimize_spark_table_definition

        result = optimize_spark_table_definition("CREATE TABLE orders (id INT)", table_format="parquet")
        assert "USING PARQUET" in result.upper()

    def test_orc_format(self):
        from benchbox.platforms._spark_helpers import optimize_spark_table_definition

        result = optimize_spark_table_definition("CREATE TABLE orders (id INT)", table_format="orc")
        assert "USING ORC" in result.upper()

    def test_non_create_table_unchanged(self):
        from benchbox.platforms._spark_helpers import optimize_spark_table_definition

        sql = "SELECT * FROM orders"
        assert optimize_spark_table_definition(sql, table_format="parquet") == sql

    def test_delta_preserves_v2_schema(self):

        from unittest.mock import MagicMock, patch

        from benchbox.platforms.velox import VeloxAdapter

        adapter = VeloxAdapter(
            table_format="delta",
            lakehouse_jars="io.delta:delta-spark_2.12:3.2.0",
        )
        ddl = "CREATE TABLE orders (id INT PRIMARY KEY, qty SMALLINT)"
        adapter._create_schema_with_tuning = MagicMock(return_value=ddl)
        captured = {}
        with patch(
            "benchbox.platforms.velox.run_spark_schema_creation_loop",
            side_effect=lambda spark, statements, optimize, **kwargs: captured.update(optimize=optimize),
        ):
            adapter.create_schema(MagicMock(), MagicMock())

        result = captured["optimize"](ddl)
        assert "PRIMARY KEY" in result
        assert "SMALLINT" in result

    def test_parquet_strips_v1_schema(self):

        from unittest.mock import MagicMock, patch

        from benchbox.platforms.velox import VeloxAdapter

        adapter = VeloxAdapter(table_format="parquet")
        ddl = "CREATE TABLE orders (id INT PRIMARY KEY, qty SMALLINT)"
        adapter._create_schema_with_tuning = MagicMock(return_value=ddl)
        captured = {}
        with patch(
            "benchbox.platforms.velox.run_spark_schema_creation_loop",
            side_effect=lambda spark, statements, optimize, **kwargs: captured.update(optimize=optimize),
        ):
            adapter.create_schema(MagicMock(), MagicMock())

        result = captured["optimize"](ddl)
        assert "PRIMARY KEY" not in result
        assert "SMALLINT" not in result


class TestVeloxRegistration:
    def test_module_imports_without_pyspark(self):

        with patch.dict("sys.modules", {"pyspark": None, "pyspark.sql": None, "pyspark.sql.types": None}):
            import importlib

            import benchbox.platforms.velox as velox_mod

            importlib.reload(velox_mod)
            assert hasattr(velox_mod, "VeloxAdapter")

    def test_dependency_info_registered(self):
        from benchbox.utils.dependencies import DEPENDENCY_GROUPS

        assert "velox" in DEPENDENCY_GROUPS
        dep = DEPENDENCY_GROUPS["velox"]
        assert "pyspark" in dep.packages

    def test_platform_to_extra_registered(self):
        from benchbox.utils.dependencies import PLATFORM_TO_EXTRA

        assert "velox" in PLATFORM_TO_EXTRA
        assert PLATFORM_TO_EXTRA["velox"] == "velox"
        assert PLATFORM_TO_EXTRA.get("gluten-velox") == "velox"


class TestDeploymentContractIsEnumerated:
    def test_docker_is_rejected_rather_than_silently_becoming_remote(self):
        from benchbox.platforms.velox import VeloxAdapter

        with pytest.raises(ValueError, match="Unsupported Velox deployment 'docker'"):
            VeloxAdapter._validate_deployment("docker")

    def test_the_docker_rejection_points_at_the_real_workflow(self):

        from benchbox.platforms.velox import VeloxAdapter

        with pytest.raises(ValueError, match="packaging infrastructure"):
            VeloxAdapter._validate_deployment("docker")

    @pytest.mark.parametrize("deployment", ["kubernetes", "k8s", "cluster", "", "sc://evil:50051", "Remote-ish"])
    def test_unknown_values_are_rejected_not_routed_to_an_endpoint(self, deployment):
        from benchbox.platforms.velox import VeloxAdapter

        with pytest.raises(ValueError, match="Unsupported Velox deployment"):
            VeloxAdapter._validate_deployment(deployment)

    @pytest.mark.parametrize(
        ("deployment", "expected"),
        [("local", "local"), ("remote", "remote"), ("LOCAL", "local"), (" remote ", "remote")],
    )
    def test_supported_deployments_are_accepted_and_normalized(self, deployment, expected):
        from benchbox.platforms.velox import VeloxAdapter

        assert VeloxAdapter._validate_deployment(deployment) == expected

    def test_the_supported_set_matches_the_mcp_contract(self):

        import pytest

        from benchbox.mcp.schemas import MCP_PLATFORM_OPTION_ALLOWLIST, MCPValidationError, validate_platform_options
        from benchbox.platforms.velox import SUPPORTED_VELOX_DEPLOYMENTS, VeloxAdapter

        assert VeloxAdapter._validate_deployment("local") == "local"
        assert VeloxAdapter._validate_deployment("remote") == "remote"
        assert set(SUPPORTED_VELOX_DEPLOYMENTS) == {"local", "remote"}

        assert "deployment" not in MCP_PLATFORM_OPTION_ALLOWLIST.get("velox", {})

        with pytest.raises(MCPValidationError, match="not authorized"):
            validate_platform_options("velox", {"deployment": "remote"})
