# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast, pytest.mark.usefixtures("spark_runtime_environment")]


@pytest.fixture()
def mock_pyspark():
    mock_session = MagicMock()
    mock_builder = MagicMock()
    mock_builder.master.return_value = mock_builder
    mock_builder.config.return_value = mock_builder
    mock_builder.enableHiveSupport.return_value = mock_builder
    mock_builder.getOrCreate.return_value = mock_session

    mock_session.version = "3.5.0"
    mock_session.catalog.listDatabases.return_value = []
    mock_session.sql.return_value = MagicMock()

    mock_class = MagicMock()
    mock_class.builder = mock_builder

    with patch.dict(
        "sys.modules",
        {
            "pyspark": MagicMock(__version__="3.5.0"),
            "pyspark.sql": MagicMock(SparkSession=mock_class),
            "pyspark.sql.types": MagicMock(
                StructType=MagicMock(),
                StructField=MagicMock(),
                StringType=MagicMock(),
                IntegerType=MagicMock(),
                LongType=MagicMock(),
                DoubleType=MagicMock(),
                DecimalType=MagicMock(),
                DateType=MagicMock(),
            ),
        },
    ):
        yield mock_class, mock_session


class TestSparkAdapterInit:
    def test_yarn_master(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(master="yarn")
        assert a.master == "yarn"

    def test_k8s_master(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(master="k8s://https://k8s-api:6443")
        assert a.master == "k8s://https://k8s-api:6443"

    def test_spark_connect_master(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(master="sc://host:15002")
        assert a.master == "sc://host:15002"

    def test_warehouse_dir_set(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(warehouse_dir="/tmp/warehouse")
        assert a.warehouse_dir == "/tmp/warehouse"

    def test_executor_cores_explicit_zero(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(executor_cores=0)
        assert a.executor_cores == 0

    def test_adaptive_disabled(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(adaptive_enabled=False)
        assert a.adaptive_enabled is False

    def test_enable_hive_true(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(enable_hive=True)
        assert a.enable_hive is True

    def test_disable_cache_false(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(disable_cache=False)
        assert a.disable_cache is False

    def test_num_executors_set(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(num_executors=8)
        assert a.num_executors == 8

    def test_spark_config_extra(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        extra = {"spark.custom.key": "value"}
        a = SparkAdapter(spark_config=extra)
        assert a.spark_config == extra

    def test_broadcast_threshold_set(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(broadcast_threshold=128)
        assert a.broadcast_threshold == 128

    def test_staging_root_set(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(staging_root="/tmp/staging")
        assert a.staging_root == "/tmp/staging"


class TestGetSparkConf:
    def test_aqe_disabled(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(adaptive_enabled=False)
        conf = a._get_spark_conf()
        assert conf["spark.sql.adaptive.enabled"] == "false"
        assert conf["spark.sql.adaptive.coalescePartitions.enabled"] == "false"
        assert conf["spark.sql.adaptive.skewJoin.enabled"] == "false"

    def test_aqe_disabled_spark_config_still_wins(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(adaptive_enabled=False, spark_config={"spark.sql.adaptive.enabled": "true"})
        conf = a._get_spark_conf()
        assert conf["spark.sql.adaptive.enabled"] == "true"

    def test_aqe_enabled(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(adaptive_enabled=True)
        conf = a._get_spark_conf()
        assert conf["spark.sql.adaptive.enabled"] == "true"
        assert conf["spark.sql.adaptive.coalescePartitions.enabled"] == "true"
        assert conf["spark.sql.adaptive.skewJoin.enabled"] == "true"

    def test_broadcast_threshold_in_conf(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(broadcast_threshold=64)
        conf = a._get_spark_conf()
        assert conf["spark.sql.autoBroadcastJoinThreshold"] == "64"

    def test_broadcast_threshold_none_absent(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter()
        conf = a._get_spark_conf()
        assert "spark.sql.autoBroadcastJoinThreshold" not in conf

    def test_num_executors_in_conf(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(num_executors=4)
        conf = a._get_spark_conf()
        assert conf["spark.executor.instances"] == "4"

    def test_num_executors_none_absent(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter()
        conf = a._get_spark_conf()
        assert "spark.executor.instances" not in conf

    def test_warehouse_dir_in_conf(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(warehouse_dir="/data/warehouse")
        conf = a._get_spark_conf()
        assert conf["spark.sql.warehouse.dir"] == "/data/warehouse"

    def test_warehouse_dir_none_absent(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter()
        conf = a._get_spark_conf()
        assert "spark.sql.warehouse.dir" not in conf

    def test_disable_cache_true(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(disable_cache=True)
        conf = a._get_spark_conf()
        assert conf["spark.sql.inMemoryColumnarStorage.enabled"] == "false"

    def test_disable_cache_false_absent(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(disable_cache=False)
        conf = a._get_spark_conf()
        assert "spark.sql.inMemoryColumnarStorage.enabled" not in conf

    def test_delta_extensions(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(table_format="delta")
        conf = a._get_spark_conf()
        assert "io.delta.sql.DeltaSparkSessionExtension" in conf["spark.sql.extensions"]
        assert "DeltaCatalog" in conf["spark.sql.catalog.spark_catalog"]

    def test_iceberg_extensions(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(table_format="iceberg")
        conf = a._get_spark_conf()
        assert "IcebergSparkSessionExtensions" in conf["spark.sql.extensions"]
        assert "SparkSessionCatalog" in conf["spark.sql.catalog.spark_catalog"]
        assert conf["spark.sql.catalog.spark_catalog.type"] == "hive"

    def test_parquet_no_extensions(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(table_format="parquet")
        conf = a._get_spark_conf()
        assert "spark.sql.extensions" not in conf

    def test_extra_spark_config_merged(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        extra = {"spark.custom.setting": "custom_value"}
        a = SparkAdapter(spark_config=extra)
        conf = a._get_spark_conf()
        assert conf["spark.custom.setting"] == "custom_value"


class TestExecuteQuery:
    def test_disable_cache_true_clears_catalog_before_query(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        _, mock_session = mock_pyspark
        mock_df = MagicMock()
        mock_df.collect.return_value = [(1,)]
        mock_session.sql.return_value = mock_df

        adapter = SparkAdapter(disable_cache=True)

        result = adapter.execute_query(mock_session, "SELECT 1", "q1")

        assert result["status"] == "SUCCESS"
        mock_session.catalog.clearCache.assert_called_once()

    def test_cache_clearing_is_outside_the_timed_window(self, mock_pyspark):
        import time

        from benchbox.platforms.spark import SparkAdapter

        _, mock_session = mock_pyspark
        mock_df = MagicMock()
        mock_df.collect.return_value = [(1,)]
        mock_session.sql.return_value = mock_df
        mock_session.catalog.clearCache.side_effect = lambda: time.sleep(0.3)

        adapter = SparkAdapter(disable_cache=True)

        result = adapter.execute_query(mock_session, "SELECT 1", "q1")

        assert result["status"] == "SUCCESS"
        assert result["query_statistics"]["execution_time_seconds"] < 0.2

    def test_cache_clear_failure_is_reported_as_query_failure(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        _, mock_session = mock_pyspark
        mock_session.catalog.clearCache.side_effect = RuntimeError("clear failed")

        adapter = SparkAdapter(disable_cache=True)

        result = adapter.execute_query(mock_session, "SELECT 1", "q1")

        assert result["status"] == "FAILED"
        assert "clear failed" in result["error"]
        mock_session.sql.assert_not_called()


class TestCreateConnection:
    def test_create_connection_basic(self, mock_pyspark):
        mock_class, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(database="bench_db")
        with (
            patch.object(a, "handle_existing_database"),
            patch("benchbox.platforms.spark.SparkSession", mock_class),
        ):
            a.database_was_reused = False
            mock_session.catalog.listDatabases.return_value = []
            conn = a.create_connection()

        assert conn is mock_session
        assert a._spark_session is mock_session

    def test_create_connection_with_hive_support(self, mock_pyspark):
        mock_class, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(enable_hive=True)
        with (
            patch.object(a, "handle_existing_database"),
            patch("benchbox.platforms.spark.SparkSession", mock_class),
        ):
            a.database_was_reused = False
            mock_session.catalog.listDatabases.return_value = []
            a.create_connection()

        mock_class.builder.enableHiveSupport.assert_called()

    def test_create_connection_database_reused(self, mock_pyspark):
        mock_class, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(database="existing_db")
        with (
            patch.object(a, "handle_existing_database"),
            patch("benchbox.platforms.spark.SparkSession", mock_class),
        ):
            a.database_was_reused = True
            a.create_connection()

        sql_calls = [str(c) for c in mock_session.sql.call_args_list]
        assert not any("CREATE DATABASE" in c.upper() for c in sql_calls)

    def test_create_connection_database_already_exists(self, mock_pyspark):
        mock_class, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(database="existing_db")
        db_mock = MagicMock()
        db_mock.name = "existing_db"
        mock_session.catalog.listDatabases.return_value = [db_mock]
        with (
            patch.object(a, "handle_existing_database"),
            patch("benchbox.platforms.spark.SparkSession", mock_class),
        ):
            a.database_was_reused = False
            a.create_connection()

        sql_calls = [str(c) for c in mock_session.sql.call_args_list]
        assert not any("CREATE DATABASE" in c.upper() for c in sql_calls)
        assert any("USE" in c.upper() for c in sql_calls)

    def test_create_connection_failure_propagates(self, mock_pyspark):
        mock_class, mock_session = mock_pyspark
        mock_class.builder.getOrCreate.side_effect = RuntimeError("spark down")
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter()
        with (
            patch.object(a, "handle_existing_database"),
            patch("benchbox.platforms.spark.SparkSession", mock_class),
        ):
            a.database_was_reused = False
            with pytest.raises(RuntimeError, match="spark down"):
                a.create_connection()


class TestCheckServerDatabaseExists:
    def test_no_session_returns_false(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter()
        assert a._spark_session is None
        assert a.check_server_database_exists() is False

    def test_database_found(self, mock_pyspark):
        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(database="mydb")
        a._spark_session = mock_session
        db = MagicMock()
        db.name = "mydb"
        mock_session.catalog.listDatabases.return_value = [db]
        assert a.check_server_database_exists() is True

    def test_database_not_found(self, mock_pyspark):
        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(database="missing")
        a._spark_session = mock_session
        db = MagicMock()
        db.name = "other"
        mock_session.catalog.listDatabases.return_value = [db]
        assert a.check_server_database_exists() is False

    def test_exception_returns_false(self, mock_pyspark):
        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(database="mydb")
        a._spark_session = mock_session
        mock_session.catalog.listDatabases.side_effect = Exception("catalog error")
        assert a.check_server_database_exists() is False

    def test_custom_database_in_kwargs(self, mock_pyspark):
        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(database="default")
        a._spark_session = mock_session
        db = MagicMock()
        db.name = "custom"
        mock_session.catalog.listDatabases.return_value = [db]
        assert a.check_server_database_exists(database="custom") is True


class TestDropDatabase:
    def test_drop_existing_database(self, mock_pyspark):
        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(database="mydb")
        a._spark_session = mock_session

        db = MagicMock()
        db.name = "mydb"
        mock_session.catalog.listDatabases.return_value = [db]

        a.drop_database()

        drop_calls = [str(c) for c in mock_session.sql.call_args_list]
        assert any("DROP DATABASE" in c.upper() and "mydb" in c for c in drop_calls)

    def test_drop_nonexistent_database_noop(self, mock_pyspark):
        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(database="gone")
        a._spark_session = None

        a.drop_database()
        mock_session.sql.assert_not_called()

    def test_drop_invalid_identifier_raises(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter()
        with pytest.raises(ValueError, match="Invalid database identifier"):
            a.drop_database(database="bad; DROP TABLE users")

    def test_drop_sql_error_raises_runtime(self, mock_pyspark):
        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(database="mydb")
        a._spark_session = mock_session
        db = MagicMock()
        db.name = "mydb"
        mock_session.catalog.listDatabases.return_value = [db]
        mock_session.sql.side_effect = Exception("drop failed")

        with pytest.raises(RuntimeError, match="Failed to drop Spark database"):
            a.drop_database()


class TestCreateSchema:
    def _make_benchmark(self):
        bench = MagicMock()
        bench.get_schema_sql.return_value = (
            "CREATE TABLE orders (id INT, amount DECIMAL);\nCREATE TABLE lineitem (id INT, qty INT);"
        )
        return bench

    def test_create_schema_basic(self, mock_pyspark):
        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(table_format="parquet")
        bench = self._make_benchmark()
        with patch.object(a, "_create_schema_with_tuning", return_value=bench.get_schema_sql()):
            elapsed = a.create_schema(bench, mock_session)

        assert elapsed >= 0
        assert mock_session.sql.call_count >= 2

    def test_create_schema_table_already_exists(self, mock_pyspark):
        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(table_format="parquet")
        bench = self._make_benchmark()

        already_raised = {"done": False}

        def side_effect(stmt):
            if "CREATE TABLE" in stmt.upper() and not already_raised["done"]:
                already_raised["done"] = True
                raise Exception("Table already exists")
            return MagicMock()

        mock_session.sql.side_effect = side_effect

        with patch.object(a, "_create_schema_with_tuning", return_value=bench.get_schema_sql()):
            a.create_schema(bench, mock_session)

        drop_calls = [c for c in mock_session.sql.call_args_list if "DROP TABLE" in str(c).upper()]
        assert len(drop_calls) >= 1

    def test_create_schema_non_exists_error_propagates(self, mock_pyspark):
        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter()
        bench = self._make_benchmark()
        mock_session.sql.side_effect = Exception("syntax error")

        with patch.object(a, "_create_schema_with_tuning", return_value="CREATE TABLE bad;"):
            with pytest.raises(Exception, match="syntax error"):
                a.create_schema(bench, mock_session)

    def test_create_schema_delta_format(self, mock_pyspark):
        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(table_format="delta")
        bench = self._make_benchmark()

        with patch.object(a, "_create_schema_with_tuning", return_value=bench.get_schema_sql()):
            a.create_schema(bench, mock_session)

        all_sqls = [str(c) for c in mock_session.sql.call_args_list]
        assert any("USING DELTA" in s.upper() for s in all_sqls)

    def test_create_schema_iceberg_format(self, mock_pyspark):
        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(table_format="iceberg")
        bench = self._make_benchmark()

        with patch.object(a, "_create_schema_with_tuning", return_value=bench.get_schema_sql()):
            a.create_schema(bench, mock_session)

        all_sqls = [str(c) for c in mock_session.sql.call_args_list]
        assert any("USING ICEBERG" in s.upper() for s in all_sqls)


class TestOptimizeTableDefinition:
    def test_non_create_returns_unchanged(self, mock_pyspark):
        from benchbox.platforms._spark_helpers import optimize_spark_table_definition

        stmt = "INSERT INTO orders VALUES (1, 2)"
        assert optimize_spark_table_definition(stmt, table_format="parquet") == stmt

    def test_removes_existing_using_clause(self, mock_pyspark):
        from benchbox.platforms._spark_helpers import optimize_spark_table_definition

        stmt = "CREATE TABLE t (id INT) USING ORC"
        result = optimize_spark_table_definition(stmt, table_format="parquet")
        assert "USING PARQUET" in result.upper()

    def test_orc_format(self, mock_pyspark):
        from benchbox.platforms._spark_helpers import optimize_spark_table_definition

        stmt = "CREATE TABLE t (id INT)"
        result = optimize_spark_table_definition(stmt, table_format="orc")
        assert "USING ORC" in result.upper()

    def test_no_closing_paren_no_using(self, mock_pyspark):
        from benchbox.platforms._spark_helpers import optimize_spark_table_definition

        stmt = "CREATE TABLE t AS SELECT 1"
        result = optimize_spark_table_definition(stmt, table_format="parquet")
        assert "USING" not in result.upper()


class TestGetQueryPlan:
    def test_plan_returned(self, mock_pyspark):
        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter()
        mock_df = MagicMock()
        row1 = MagicMock()
        row1.__getitem__ = lambda self, i: "== Physical Plan ==\nHashJoin"
        mock_df.collect.return_value = [row1]
        mock_session.sql.return_value = mock_df

        plan = a.get_query_plan(mock_session, "SELECT * FROM t")
        assert isinstance(plan, str)
        assert "EXPLAIN EXTENDED" in mock_session.sql.call_args[0][0].upper()

    def test_plan_error_returns_none(self, mock_pyspark):
        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter()
        mock_session.sql.side_effect = Exception("explain failed")

        plan = a.get_query_plan(mock_session, "SELECT * FROM t")
        assert plan is None


class TestTestConnection:
    def test_connection_success(self, mock_pyspark):
        mock_class, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        mock_df = MagicMock()
        mock_df.collect.return_value = [(1,)]
        mock_session.sql.return_value = mock_df

        a = SparkAdapter()
        with patch("benchbox.platforms.spark.SparkSession", mock_class):
            result = a.test_connection()

        assert result is True
        mock_session.stop.assert_called()

    def test_connection_failure(self, mock_pyspark):
        mock_class, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        mock_class.builder.getOrCreate.side_effect = Exception("cannot connect")

        a = SparkAdapter()
        with patch("benchbox.platforms.spark.SparkSession", mock_class):
            result = a.test_connection()

        assert result is False


class TestGetPlatformInfo:
    def test_platform_info_with_connection(self, mock_pyspark):
        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        mock_conf = MagicMock()
        mock_conf.get.side_effect = lambda k: f"val:{k}"
        mock_sc = MagicMock()
        mock_sc.getConf.return_value = mock_conf
        mock_sc.applicationId = "app-123"
        mock_session.sparkContext = mock_sc

        a = SparkAdapter(master="local[4]")
        info = a.get_platform_info(connection=mock_session)

        assert info["platform_version"] == mock_session.version
        assert info["connection_mode"] == "local"

    def test_platform_info_connection_error_handled(self, mock_pyspark):
        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        type(mock_session).version = PropertyMock(side_effect=AttributeError("no version"))

        a = SparkAdapter()
        info = a.get_platform_info(connection=mock_session)
        assert "platform_version" in info

    def test_platform_info_no_connection_returns_none_version(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(master="local[2]")
        info = a.get_platform_info(connection=None)
        assert info["platform_version"] is None
        assert info["connection_mode"] == "local"


class TestConfigureForBenchmark:
    def test_olap_sets_aqe(self, mock_pyspark):
        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter()
        a.configure_for_benchmark(mock_session, "tpch")

        conf_calls = [str(c) for c in mock_session.conf.set.call_args_list]
        assert any("adaptive.enabled" in c for c in conf_calls)

    def test_olap_respects_adaptive_disabled(self, mock_pyspark):
        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(adaptive_enabled=False)
        a.configure_for_benchmark(mock_session, "tpch")

        mock_session.conf.set.assert_any_call("spark.sql.adaptive.enabled", "false")
        mock_session.conf.set.assert_any_call("spark.sql.adaptive.coalescePartitions.enabled", "false")
        mock_session.conf.set.assert_any_call("spark.sql.adaptive.skewJoin.enabled", "false")

    def test_olap_respects_spark_config_aqe_override(self, mock_pyspark):
        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(spark_config={"spark.sql.adaptive.enabled": "false"})
        a.configure_for_benchmark(mock_session, "olap")

        mock_session.conf.set.assert_any_call("spark.sql.adaptive.enabled", "false")
        mock_session.conf.set.assert_any_call("spark.sql.adaptive.skewJoin.enabled", "true")

    def test_tpcds_sets_cbo(self, mock_pyspark):
        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter()
        a.configure_for_benchmark(mock_session, "tpcds")

        conf_calls = [str(c) for c in mock_session.conf.set.call_args_list]
        assert any("cbo.enabled" in c for c in conf_calls)

    def test_unknown_benchmark_noop(self, mock_pyspark):
        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter()
        a.configure_for_benchmark(mock_session, "unknown")
        mock_session.conf.set.assert_not_called()

    def test_conf_set_exception_warning(self, mock_pyspark):
        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter()
        mock_session.conf.set.side_effect = Exception("read-only conf")
        a.configure_for_benchmark(mock_session, "olap")
        mock_session.conf.set.assert_called()


class TestGenerateTuningClause:
    def test_sorting_with_delta_generates_cluster_by(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(table_format="delta")

        mock_col = MagicMock()
        mock_col.name = "l_shipdate"
        mock_col.order = 1

        mock_tuning = MagicMock()
        mock_tuning.has_any_tuning.return_value = True
        mock_tuning.table_name = "lineitem"

        from benchbox.core.tuning.interface import TuningType

        mock_tuning.get_columns_by_type.side_effect = lambda t: [mock_col] if t == TuningType.SORTING else []

        clause = a.generate_tuning_clause(mock_tuning)
        assert "CLUSTER BY" in clause
        assert "l_shipdate" in clause

    def test_sorting_without_delta_no_cluster_by(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(table_format="parquet")

        mock_col = MagicMock()
        mock_col.name = "col1"
        mock_col.order = 1

        mock_tuning = MagicMock()
        mock_tuning.has_any_tuning.return_value = True
        mock_tuning.table_name = "t"

        from benchbox.core.tuning.interface import TuningType

        mock_tuning.get_columns_by_type.side_effect = lambda t: [mock_col] if t == TuningType.SORTING else []

        clause = a.generate_tuning_clause(mock_tuning)
        assert "CLUSTER BY" not in clause

    def test_no_tuning_returns_empty(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter()
        mock_tuning = MagicMock()
        mock_tuning.has_any_tuning.return_value = False
        assert a.generate_tuning_clause(mock_tuning) == ""

    def test_none_tuning_returns_empty(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter()
        assert a.generate_tuning_clause(None) == ""


class TestApplyTableTunings:
    def test_no_tuning_noop(self, mock_pyspark):
        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter()
        a.apply_table_tunings(None, mock_session)
        mock_session.sql.assert_not_called()

    def test_delta_zorder_applied(self, mock_pyspark):
        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(table_format="delta")

        mock_col = MagicMock()
        mock_col.name = "l_shipdate"
        mock_col.order = 1

        from benchbox.core.tuning.interface import TuningType

        mock_tuning = MagicMock()
        mock_tuning.has_any_tuning.return_value = True
        mock_tuning.table_name = "lineitem"
        mock_tuning.get_columns_by_type.side_effect = lambda t: [mock_col] if t == TuningType.SORTING else []

        a.apply_table_tunings(mock_tuning, mock_session)

        sql_calls = [str(c) for c in mock_session.sql.call_args_list]
        assert any("OPTIMIZE" in c.upper() and "ZORDER" in c.upper() for c in sql_calls)

    def test_delta_zorder_error_continues(self, mock_pyspark):
        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(table_format="delta")
        mock_session.sql.side_effect = Exception("OPTIMIZE not available")

        mock_col = MagicMock()
        mock_col.name = "col1"
        mock_col.order = 1

        from benchbox.core.tuning.interface import TuningType

        mock_tuning = MagicMock()
        mock_tuning.has_any_tuning.return_value = True
        mock_tuning.table_name = "t"
        mock_tuning.get_columns_by_type.side_effect = lambda t: [mock_col] if t == TuningType.SORTING else []

        a.apply_table_tunings(mock_tuning, mock_session)
        mock_session.sql.assert_called()

    def test_parquet_partition_columns_logged(self, mock_pyspark):
        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter(table_format="parquet")

        mock_col = MagicMock()
        mock_col.name = "region"
        mock_col.order = 1

        from benchbox.core.tuning.interface import TuningType

        mock_tuning = MagicMock()
        mock_tuning.has_any_tuning.return_value = True
        mock_tuning.table_name = "nation"
        mock_tuning.get_columns_by_type.side_effect = lambda t: [mock_col] if t == TuningType.PARTITIONING else []

        a.apply_table_tunings(mock_tuning, mock_session)
        mock_session.sql.assert_not_called()


class TestApplyUnifiedTuning:
    def test_none_config_noop(self, mock_pyspark):
        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter()
        with (
            patch.object(a, "apply_platform_optimizations") as mock_plat,
            patch.object(a, "apply_constraint_configuration") as mock_constraint,
        ):
            a.apply_unified_tuning(None, mock_session)
            mock_plat.assert_not_called()
            mock_constraint.assert_not_called()
            mock_session.conf.set.assert_not_called()

    def test_applies_platform_optimizations(self, mock_pyspark):
        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter()

        mock_platform_cfg = MagicMock()
        mock_platform_cfg.spark = {"sql.shuffle.partitions": "100"}

        mock_config = MagicMock()
        mock_config.platform_optimizations = mock_platform_cfg
        mock_config.primary_keys = None
        mock_config.foreign_keys = None
        mock_config.table_tunings = {}

        with patch.object(a, "apply_constraint_configuration"):
            with patch.object(a, "apply_platform_optimizations") as mock_apply:
                a.apply_unified_tuning(mock_config, mock_session)
                mock_apply.assert_called_once_with(mock_platform_cfg, mock_session)

    def test_applies_table_tunings(self, mock_pyspark):
        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter()

        mock_table_tuning = MagicMock()
        mock_table_tuning.has_any_tuning.return_value = False

        mock_config = MagicMock()
        mock_config.platform_optimizations = None
        mock_config.primary_keys = None
        mock_config.foreign_keys = None
        mock_config.table_tunings = {"orders": mock_table_tuning}

        with patch.object(a, "apply_constraint_configuration"):
            a.apply_unified_tuning(mock_config, mock_session)

        mock_table_tuning.has_any_tuning.assert_called()


class TestApplyPlatformOptimizations:
    def test_none_config_noop(self, mock_pyspark):
        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter()
        a.apply_platform_optimizations(None, mock_session)
        mock_session.conf.set.assert_not_called()

    def test_spark_config_applied(self, mock_pyspark):
        from benchbox.core.tuning.interface import UnifiedTuningConfiguration
        from benchbox.platforms._spark_helpers import apply_tuned_spark_session_config

        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter()
        a.tuning_enabled = True
        a.unified_tuning_configuration = UnifiedTuningConfiguration.from_dict(
            {"platform_optimizations": {"spark": {"sql.shuffle.partitions": "400"}}}
        )
        store: dict[str, str] = {}
        mock_session.conf.set.side_effect = lambda key, value: store.__setitem__(key, str(value))
        mock_session.conf.get.side_effect = lambda key, default=None: store.get(key, default)

        apply_tuned_spark_session_config(a, mock_session)

        mock_session.conf.set.assert_called_with("spark.sql.shuffle.partitions", "400")

    def test_no_spark_attr_noop(self, mock_pyspark):
        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter()

        mock_platform_cfg = MagicMock(spec=[])
        a.apply_platform_optimizations(mock_platform_cfg, mock_session)
        mock_session.conf.set.assert_not_called()

    def test_conf_set_error_continues(self, mock_pyspark):
        from benchbox.core.tuning.applied_ledger import STATEMENT_FAILED, AppliedTuningLedger
        from benchbox.core.tuning.interface import UnifiedTuningConfiguration
        from benchbox.platforms._spark_helpers import apply_tuned_spark_session_config

        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter()
        a.tuning_enabled = True
        a.unified_tuning_configuration = UnifiedTuningConfiguration.from_dict(
            {"platform_optimizations": {"spark": {"bad.key": "value"}}}
        )
        a._applied_tuning_ledger = AppliedTuningLedger()
        mock_session.conf.set.side_effect = Exception("read-only")

        apply_tuned_spark_session_config(a, mock_session)

        mock_session.conf.set.assert_called_with("spark.bad.key", "value")
        failed = [s for s in a._applied_tuning_ledger.statements if s.status == STATEMENT_FAILED]
        assert [s.statement for s in failed] == ["SET spark.bad.key=value"]


class TestApplyConstraintConfiguration:
    def test_pk_enabled_logs(self, mock_pyspark):
        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter()

        pk = MagicMock()
        pk.enabled = True
        fk = MagicMock()
        fk.enabled = False

        with patch.object(a.logger, "info") as mock_info:
            a.apply_constraint_configuration(pk, fk, mock_session)
            mock_info.assert_called_once()
            assert "Primary key" in mock_info.call_args[0][0]

    def test_fk_enabled_logs(self, mock_pyspark):
        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter()

        pk = MagicMock()
        pk.enabled = False
        fk = MagicMock()
        fk.enabled = True

        with patch.object(a.logger, "info") as mock_info:
            a.apply_constraint_configuration(pk, fk, mock_session)
            mock_info.assert_called_once()
            assert "Foreign key" in mock_info.call_args[0][0]

    def test_both_none_noop(self, mock_pyspark):
        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter()
        with patch.object(a.logger, "info") as mock_info:
            a.apply_constraint_configuration(None, None, mock_session)
            mock_info.assert_not_called()


class TestCloseConnection:
    def test_stop_called_and_session_cleared(self, mock_pyspark):
        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter()
        a._spark_session = mock_session
        a.close_connection(mock_session)

        mock_session.stop.assert_called_once()
        assert a._spark_session is None

    def test_none_connection_noop(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter()
        a.close_connection(None)
        assert a._spark_session is None

    def test_stop_exception_swallowed(self, mock_pyspark):
        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter()
        mock_session.stop.side_effect = Exception("stop failed")
        a.close_connection(mock_session)
        mock_session.stop.assert_called_once()
        assert a._spark_session is None


class TestGetExistingTables:
    def test_returns_table_names(self, mock_pyspark):
        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter()
        t1 = MagicMock()
        t1.name = "Orders"
        t2 = MagicMock()
        t2.name = "LineItem"
        mock_session.catalog.listTables.return_value = [t1, t2]

        tables = a._get_existing_tables(mock_session)
        assert "orders" in tables
        assert "lineitem" in tables

    def test_exception_returns_empty(self, mock_pyspark):
        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter()
        mock_session.catalog.listTables.side_effect = Exception("no catalog")

        tables = a._get_existing_tables(mock_session)
        assert tables == []


class TestAnalyzeTable:
    def test_analyze_table_executes_sql(self, mock_pyspark):
        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter()
        a.analyze_table(mock_session, "Orders")

        sql_calls = [str(c) for c in mock_session.sql.call_args_list]
        assert any("ANALYZE TABLE" in c.upper() and "orders" in c.lower() for c in sql_calls)

    def test_analyze_table_exception_handled(self, mock_pyspark):
        _, mock_session = mock_pyspark
        from benchbox.platforms.spark import SparkAdapter

        a = SparkAdapter()
        mock_session.sql.side_effect = Exception("permission denied")
        a.analyze_table(mock_session, "orders")
        mock_session.sql.assert_called_once()


class TestFromConfig:
    def test_explicit_database_preserved(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        config = {
            "benchmark": "tpch",
            "scale_factor": 1.0,
            "database": "my_custom_db",
        }
        a = SparkAdapter.from_config(config)
        assert a.database == "my_custom_db"

    def test_database_generated_when_absent(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        config = {
            "benchmark": "tpch",
            "scale_factor": 1.0,
        }
        a = SparkAdapter.from_config(config)
        assert a.database

    def test_all_core_keys_passed(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        config = {
            "benchmark": "tpch",
            "scale_factor": 1.0,
            "master": "spark://host:7077",
            "app_name": "MyApp",
            "deploy_mode": "client",
            "driver_memory": "16g",
            "executor_memory": "32g",
            "executor_cores": 8,
            "num_executors": 20,
        }
        a = SparkAdapter.from_config(config)
        assert a.master == "spark://host:7077"
        assert a.app_name == "MyApp"
        assert a.driver_memory == "16g"
        assert a.num_executors == 20

    def test_optional_keys_passed(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        config = {
            "benchmark": "tpcds",
            "scale_factor": 10.0,
            "table_format": "delta",
            "enable_hive": True,
            "adaptive_enabled": False,
            "shuffle_partitions": 400,
            "broadcast_threshold": 128,
        }
        a = SparkAdapter.from_config(config)
        assert a.table_format == "delta"
        assert a.enable_hive is True
        assert a.adaptive_enabled is False
        assert a.shuffle_partitions == 400
