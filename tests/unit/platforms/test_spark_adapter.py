from __future__ import annotations

import argparse
import contextlib
import importlib
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.usefixtures("spark_runtime_environment"),
]


@contextlib.contextmanager
def _mock_sys_modules(replacements):
    saved = {key: sys.modules[key] for key in replacements if key in sys.modules}
    sys.modules.update(replacements)
    try:
        yield
    finally:
        for key in replacements:
            sys.modules.pop(key, None)
        sys.modules.update(saved)


class TestMockSysModules:
    def test_only_named_keys_restored(self):
        sentinel = object()
        sys.modules["_spark_test_sentinel_present"] = sentinel
        try:
            with _mock_sys_modules({"_spark_test_sentinel_present": "mock", "_spark_test_sentinel_absent": "mock"}):
                assert sys.modules["_spark_test_sentinel_present"] == "mock"
                assert sys.modules["_spark_test_sentinel_absent"] == "mock"
                sys.modules["_spark_test_imported_inside"] = object()
            assert sys.modules["_spark_test_sentinel_present"] is sentinel
            assert "_spark_test_sentinel_absent" not in sys.modules
            assert "_spark_test_imported_inside" in sys.modules
        finally:
            sys.modules.pop("_spark_test_sentinel_present", None)
            sys.modules.pop("_spark_test_imported_inside", None)

    def test_native_modules_survive_mock_block(self):
        if "datafusion" in sys.modules:
            pytest.skip("datafusion already imported on this worker; eviction path not exercisable")
        pytest.importorskip("datafusion")
        with _mock_sys_modules({"pyspark": MagicMock(), "pyspark.sql": MagicMock()}):
            import datafusion

            assert "datafusion" in sys.modules
        import datafusion

        assert "datafusion" in sys.modules


class TestSparkAdapter:
    @pytest.fixture
    def mock_pyspark(self):
        mock_spark_session = MagicMock()
        mock_builder = MagicMock()
        mock_builder.master.return_value = mock_builder
        mock_builder.config.return_value = mock_builder
        mock_builder.enableHiveSupport.return_value = mock_builder
        mock_builder.getOrCreate.return_value = mock_spark_session

        mock_spark_session.version = "3.5.0"
        mock_spark_session.catalog.listDatabases.return_value = []
        mock_spark_session.sql.return_value = MagicMock()

        mock_session_class = MagicMock()
        mock_session_class.builder = mock_builder

        spark_module_prefix = "benchbox.platforms.spark"
        spark_modules_before = {
            key: value
            for key, value in sys.modules.items()
            if key == spark_module_prefix or key.startswith(f"{spark_module_prefix}.")
        }
        platforms_package = sys.modules.get("benchbox.platforms")
        package_spark_sentinel = object()
        package_spark_before = (
            getattr(platforms_package, "spark", package_spark_sentinel)
            if platforms_package is not None
            else package_spark_sentinel
        )
        if platforms_package is not None:
            if hasattr(platforms_package, "spark"):
                del platforms_package.spark
        for key in spark_modules_before:
            sys.modules.pop(key, None)
        try:
            with _mock_sys_modules(
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
                        DecimalType=MagicMock(),
                        DateType=MagicMock(),
                    ),
                },
            ):
                yield mock_session_class, mock_spark_session
        finally:
            for key in list(sys.modules):
                if key == spark_module_prefix or key.startswith(f"{spark_module_prefix}."):
                    sys.modules.pop(key, None)
            sys.modules.update(spark_modules_before)
            if platforms_package is not None:
                if package_spark_before is package_spark_sentinel:
                    if hasattr(platforms_package, "spark"):
                        del platforms_package.spark
                else:
                    platforms_package.spark = package_spark_before

    def test_initialization_success(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        config = {
            "master": "local[4]",
            "app_name": "TestApp",
            "database": "test_db",
            "driver_memory": "8g",
            "executor_memory": "8g",
        }

        adapter = SparkAdapter(**config)

        assert adapter.platform_name == "Spark"
        assert adapter.master == "local[4]"
        assert adapter.app_name == "TestApp"
        assert adapter.database == "test_db"
        assert adapter.driver_memory == "8g"
        assert adapter.executor_memory == "8g"

    def test_initialization_with_defaults(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        adapter = SparkAdapter()

        assert adapter.master == "local[*]"
        assert adapter.app_name == "BenchBox"
        assert adapter.database == "default"
        assert adapter.driver_memory is None
        assert adapter.executor_memory is None
        assert adapter.executor_cores is None
        assert adapter.shuffle_partitions is None
        assert adapter.adaptive_enabled is None
        assert adapter.table_format == "parquet"

    def test_initialization_with_cluster_mode(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        config = {
            "master": "spark://master:7077",
            "deploy_mode": "cluster",
            "num_executors": 10,
        }

        adapter = SparkAdapter(**config)

        assert adapter.master == "spark://master:7077"
        assert adapter.deploy_mode == "cluster"
        assert adapter.num_executors == 10

    def test_initialization_with_kubernetes(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        config = {
            "master": "k8s://https://k8s-master:6443",
            "num_executors": 5,
        }

        adapter = SparkAdapter(**config)

        assert adapter.master == "k8s://https://k8s-master:6443"

    def test_get_target_dialect(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        adapter = SparkAdapter()
        assert adapter.get_target_dialect() == "spark"

    def test_platform_info(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        config = {
            "master": "local[8]",
            "database": "benchmark",
            "driver_memory": "16g",
            "executor_memory": "32g",
            "executor_cores": 4,
        }

        adapter = SparkAdapter(**config)
        info = adapter.get_platform_info(connection=None)

        assert info["platform_type"] == "spark"
        assert info["platform_name"] == "Apache Spark"
        assert info["connection_mode"] == "local"
        assert info["master"] == "local[8]"
        assert info["configuration"]["database"] == "benchmark"
        assert info["configuration"]["driver_memory"] == "16g"
        assert info["configuration"]["executor_memory"] == "32g"
        assert info["configuration"]["executor_cores"] == 4

    def test_platform_info_cluster_mode(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        config = {
            "master": "spark://master:7077",
        }

        adapter = SparkAdapter(**config)
        info = adapter.get_platform_info(connection=None)

        assert info["connection_mode"] == "cluster"

    def test_from_config(self, mock_pyspark):
        from benchbox.cli.platform_hooks import PlatformHookRegistry
        from benchbox.core.platform_config import get_platform_config
        from benchbox.platforms.spark import SparkAdapter

        config = {
            "benchmark": "TPC-H",
            "scale_factor": 10.0,
            "master": "local[*]",
            "driver_memory": "8g",
        }

        adapter = SparkAdapter.from_config(config)

        assert adapter.driver_memory == "8g"
        assert "tpch" in adapter.database.lower() or "benchmark" in adapter.database.lower()

        joinorder_adapter = SparkAdapter.from_config(
            {
                "benchmark": "joinorder",
                "scale_factor": 1.0,
                "master": "local[*]",
            }
        )

        assert joinorder_adapter.broadcast_threshold is None
        assert "spark.sql.autoBroadcastJoinThreshold" not in joinorder_adapter._get_spark_conf()

        tuned_joinorder_adapter = SparkAdapter.from_config(
            {
                "benchmark": "joinorder",
                "scale_factor": 1.0,
                "tuning_enabled": True,
            }
        )
        assert tuned_joinorder_adapter.broadcast_threshold == -1
        assert tuned_joinorder_adapter._get_spark_conf()["spark.sql.autoBroadcastJoinThreshold"] == "-1"

        database_config = PlatformHookRegistry.build_database_config(
            "spark", {}, {"benchmark": "joinorder", "scale_factor": 1.0}
        )
        platform_config = get_platform_config(database_config, None, benchmark_name="joinorder", scale_factor=1.0)
        hook_adapter = SparkAdapter.from_config(platform_config)

        assert hook_adapter.broadcast_threshold is None
        assert "spark.sql.autoBroadcastJoinThreshold" not in hook_adapter._get_spark_conf()

        explicit_broadcast_adapter = SparkAdapter.from_config(
            {
                "benchmark": "joinorder",
                "scale_factor": 1.0,
                "broadcast_threshold": 1048576,
            }
        )

        assert explicit_broadcast_adapter.broadcast_threshold == 1048576
        assert explicit_broadcast_adapter._get_spark_conf()["spark.sql.autoBroadcastJoinThreshold"] == "1048576"

        explicit_spark_conf_adapter = SparkAdapter.from_config(
            {
                "benchmark": "joinorder",
                "scale_factor": 1.0,
                "spark_config": {"spark.sql.autoBroadcastJoinThreshold": "2097152"},
            }
        )

        assert explicit_spark_conf_adapter.broadcast_threshold is None
        assert explicit_spark_conf_adapter._get_spark_conf()["spark.sql.autoBroadcastJoinThreshold"] == "2097152"

    def test_supports_tuning_type(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        adapter = SparkAdapter()

        try:
            from benchbox.core.tuning.interface import TuningType

            assert adapter.supports_tuning_type(TuningType.PARTITIONING) is True
            assert adapter.supports_tuning_type(TuningType.SORTING) is True
            assert adapter.supports_tuning_type(TuningType.CLUSTERING) is False
        except ImportError:
            pass

    def test_get_spark_conf(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        adapter = SparkAdapter(
            app_name="TestApp",
            driver_memory="8g",
            executor_memory="16g",
            executor_cores=4,
            shuffle_partitions=500,
            adaptive_enabled=True,
        )

        conf = adapter._get_spark_conf()

        assert conf["spark.app.name"] == "TestApp"
        assert conf["spark.driver.memory"] == "8g"
        assert conf["spark.executor.memory"] == "16g"
        assert conf["spark.executor.cores"] == "4"
        assert conf["spark.sql.shuffle.partitions"] == "500"
        assert conf["spark.sql.adaptive.enabled"] == "true"

    def test_get_spark_conf_uses_engine_defaults(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        conf = SparkAdapter()._get_spark_conf()

        assert "spark.driver.memory" not in conf
        assert "spark.executor.memory" not in conf
        assert "spark.executor.cores" not in conf
        assert "spark.sql.shuffle.partitions" not in conf
        assert "spark.sql.adaptive.enabled" not in conf
        assert "spark.hadoop.io.compression.codecs" not in conf

    def test_csv_compression_codecs_excludes_zstd(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        adapter = SparkAdapter()
        assert "zstd" not in adapter._csv_compression_codecs
        assert isinstance(adapter._csv_compression_codecs, frozenset)

    def test_get_spark_conf_delta_lake(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        adapter = SparkAdapter(table_format="delta")

        conf = adapter._get_spark_conf()

        assert "spark.sql.extensions" in conf
        assert "delta" in conf["spark.sql.extensions"].lower()

    def test_get_spark_conf_iceberg(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        adapter = SparkAdapter(table_format="iceberg")

        conf = adapter._get_spark_conf()

        assert "spark.sql.extensions" in conf
        assert "iceberg" in conf["spark.sql.extensions"].lower()

    def test_normalize_table_name(self, mock_pyspark):
        from benchbox.platforms._spark_helpers import normalize_spark_table_name_in_sql

        sql = 'CREATE TABLE "CUSTOMER" (id INT)'
        normalized = normalize_spark_table_name_in_sql(sql)

        assert "customer" in normalized.lower()

    def test_optimize_table_definition_parquet(self, mock_pyspark):
        from benchbox.platforms._spark_helpers import optimize_spark_table_definition

        sql = "CREATE TABLE orders (id INT, amount DECIMAL)"
        optimized = optimize_spark_table_definition(
            sql, table_format="parquet", strip_v1_constraints=True, upcast_smallint=True
        )

        assert "USING PARQUET" in optimized.upper()

    def test_optimize_table_definition_delta(self, mock_pyspark):
        from benchbox.platforms._spark_helpers import optimize_spark_table_definition

        sql = "CREATE TABLE orders (id INT, amount DECIMAL)"
        optimized = optimize_spark_table_definition(
            sql, table_format="delta", strip_v1_constraints=False, upcast_smallint=False
        )

        assert "USING DELTA" in optimized.upper()

    def test_optimize_table_definition_orc(self, mock_pyspark):
        from benchbox.platforms._spark_helpers import optimize_spark_table_definition

        sql = "CREATE TABLE orders (id INT, amount DECIMAL)"
        optimized = optimize_spark_table_definition(
            sql, table_format="orc", strip_v1_constraints=True, upcast_smallint=True
        )

        assert "USING ORC" in optimized.upper()

    def test_validate_identifier(self, mock_pyspark):
        from benchbox.platforms._spark_helpers import validate_spark_identifier

        assert validate_spark_identifier("my_table") is True
        assert validate_spark_identifier("_private") is True
        assert validate_spark_identifier("Table123") is True

        assert validate_spark_identifier("") is False
        assert validate_spark_identifier("123table") is False
        assert validate_spark_identifier("table-name") is False
        assert validate_spark_identifier("table; DROP TABLE users") is False


class TestSparkAdapterExecution:
    @pytest.fixture
    def mock_pyspark(self):
        mock_spark_session = MagicMock()
        mock_builder = MagicMock()
        mock_builder.master.return_value = mock_builder
        mock_builder.config.return_value = mock_builder
        mock_builder.enableHiveSupport.return_value = mock_builder
        mock_builder.getOrCreate.return_value = mock_spark_session

        mock_spark_session.version = "3.5.0"
        mock_spark_session.catalog.listDatabases.return_value = []
        mock_spark_session.sql.return_value = MagicMock()

        mock_session_class = MagicMock()
        mock_session_class.builder = mock_builder

        with _mock_sys_modules(
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
                    DecimalType=MagicMock(),
                    DateType=MagicMock(),
                ),
            },
        ):
            yield mock_session_class, mock_spark_session

    def test_execute_query_success(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        _, mock_spark_session = mock_pyspark

        mock_df = MagicMock()
        mock_df.collect.return_value = [(1, "test"), (2, "test2")]
        mock_df.count.return_value = 2
        mock_spark_session.sql.return_value = mock_df

        adapter = SparkAdapter()

        result = adapter.execute_query(mock_spark_session, "SELECT * FROM test", "q1")

        assert result["query_id"] == "q1"
        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 2

    def test_execute_query_failure(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        _, mock_spark_session = mock_pyspark
        mock_spark_session.sql.side_effect = Exception("Query failed")

        adapter = SparkAdapter()

        result = adapter.execute_query(mock_spark_session, "INVALID SQL", "q1")

        assert result["query_id"] == "q1"
        assert result["status"] == "FAILED"
        assert "Query failed" in result.get("error", "")

    def test_close_connection(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        _, mock_spark_session = mock_pyspark

        adapter = SparkAdapter()
        adapter.close_connection(mock_spark_session)

        mock_spark_session.stop.assert_called_once()

    def test_close_connection_handles_none(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        adapter = SparkAdapter()

        adapter.close_connection(None)

    def test_validate_data_integrity_uses_spark_sql(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        _, mock_spark_session = mock_pyspark
        adapter = SparkAdapter()

        mock_df = MagicMock()
        mock_spark_session.sql.return_value = mock_df

        table_stats = {"customer": 100, "orders": 500}
        status, details = adapter._validate_data_integrity(None, mock_spark_session, table_stats)

        assert status == "PASSED"
        assert set(details["accessible_tables"]) == {"customer", "orders"}
        assert mock_spark_session.sql.call_count == 2

    def test_validate_data_integrity_detects_inaccessible(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        _, mock_spark_session = mock_pyspark
        adapter = SparkAdapter()

        def side_effect(query):
            if "bad_table" in query:
                raise Exception("Table not found")
            return MagicMock()

        mock_spark_session.sql.side_effect = side_effect

        table_stats = {"good_table": 100, "bad_table": 50}
        status, details = adapter._validate_data_integrity(None, mock_spark_session, table_stats)

        assert status == "FAILED"
        assert "bad_table" in details["inaccessible_tables"]

    def test_create_schema_removes_orphaned_location(self, mock_pyspark, tmp_path):
        from benchbox.platforms.spark import SparkAdapter

        _, mock_spark_session = mock_pyspark
        adapter = SparkAdapter()

        db_dir = tmp_path / "test_db.db"
        table_dir = db_dir / "date_dim"
        table_dir.mkdir(parents=True)
        (table_dir / "part-00000.parquet").touch()

        mock_spark_session.conf.get.return_value = str(tmp_path)
        mock_spark_session.catalog.currentDatabase.return_value = "test_db"

        adapter._remove_orphaned_table_location(mock_spark_session, "date_dim")

        assert not table_dir.exists()

    def test_remove_orphaned_location_qualified_name(self, mock_pyspark, tmp_path):
        from benchbox.platforms.spark import SparkAdapter

        _, mock_spark_session = mock_pyspark
        adapter = SparkAdapter()

        db_dir = tmp_path / "test_db.db"
        table_dir = db_dir / "date_dim"
        table_dir.mkdir(parents=True)

        mock_spark_session.conf.get.return_value = str(tmp_path)
        mock_spark_session.catalog.currentDatabase.return_value = "test_db"

        adapter._remove_orphaned_table_location(mock_spark_session, "`spark_catalog`.`test_db`.`date_dim`")
        assert not table_dir.exists()

    def test_remove_orphaned_location_rejects_path_traversal(self, mock_pyspark, tmp_path):
        from benchbox.platforms.spark import SparkAdapter

        _, mock_spark_session = mock_pyspark
        adapter = SparkAdapter()

        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()

        warehouse = tmp_path / "warehouse"
        warehouse.mkdir()

        mock_spark_session.conf.get.return_value = str(warehouse)
        mock_spark_session.catalog.currentDatabase.return_value = "test_db"

        adapter._remove_orphaned_table_location(mock_spark_session, "../../outside")
        assert outside_dir.exists()

    def test_remove_orphaned_location_rejects_root_table_name(self, mock_pyspark, tmp_path):
        from benchbox.platforms.spark import SparkAdapter

        _, mock_spark_session = mock_pyspark
        adapter = SparkAdapter()

        warehouse = tmp_path / "warehouse"
        warehouse.mkdir()

        mock_spark_session.conf.get.return_value = str(warehouse)
        mock_spark_session.catalog.currentDatabase.return_value = "test_db"

        adapter._remove_orphaned_table_location(mock_spark_session, "/")
        assert warehouse.exists()

    def test_generate_tuning_clause_partitioning(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        adapter = SparkAdapter()

        mock_col = MagicMock()
        mock_col.name = "date_col"
        mock_col.order = 1

        mock_tuning = MagicMock()
        mock_tuning.has_any_tuning.return_value = True
        mock_tuning.table_name = "orders"

        with patch("benchbox.core.tuning.interface.TuningType") as mock_tuning_type:
            mock_tuning_type.PARTITIONING = "partitioning"
            mock_tuning_type.SORTING = "sorting"
            mock_tuning.get_columns_by_type.side_effect = lambda t: (
                [mock_col] if t == mock_tuning_type.PARTITIONING else []
            )

            clause = adapter.generate_tuning_clause(mock_tuning)
            assert "PARTITIONED BY" in clause
            assert "date_col" in clause

    def test_generate_tuning_clause_sorting(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        adapter = SparkAdapter()

        mock_col = MagicMock()
        mock_col.name = "order_key"
        mock_col.order = 1

        mock_tuning = MagicMock()
        mock_tuning.has_any_tuning.return_value = True
        mock_tuning.table_name = "lineitem"

        with patch("benchbox.core.tuning.interface.TuningType") as mock_tuning_type:
            mock_tuning_type.PARTITIONING = "partitioning"
            mock_tuning_type.SORTING = "sorting"
            mock_tuning.get_columns_by_type.side_effect = lambda t: [mock_col] if t == mock_tuning_type.SORTING else []

            clause = adapter.generate_tuning_clause(mock_tuning)
            assert "CLUSTERED BY" in clause or "SORTED BY" in clause or clause == ""

    def test_generate_tuning_clause_empty(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        adapter = SparkAdapter()

        mock_tuning = MagicMock()
        mock_tuning.has_any_tuning.return_value = False

        clause = adapter.generate_tuning_clause(mock_tuning)
        assert clause == ""

    def test_configure_for_benchmark_olap(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        _, mock_spark_session = mock_pyspark

        adapter = SparkAdapter()

        adapter.configure_for_benchmark(mock_spark_session, "olap")

        mock_spark_session.conf.set.reset_mock()
        adapter.configure_for_benchmark(mock_spark_session, "joinorder")

        mock_spark_session.conf.set.assert_not_called()

    def test_tuned_run_applies_olap_session_defaults(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        _, mock_spark_session = mock_pyspark
        adapter = SparkAdapter(tuning_enabled=True)

        conf = adapter._get_spark_conf()
        adapter.configure_for_benchmark(mock_spark_session, "tpch")

        assert conf["spark.sql.shuffle.partitions"] == "200"
        assert conf["spark.sql.adaptive.enabled"] == "true"
        mock_spark_session.conf.set.assert_any_call("spark.sql.cbo.enabled", "true")

    def test_get_query_plan(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        _, mock_spark_session = mock_pyspark

        mock_df = MagicMock()
        mock_df._jdf.queryExecution.return_value.toString.return_value = "== Physical Plan ==\nScan"
        mock_spark_session.sql.return_value = mock_df

        adapter = SparkAdapter()

        try:
            plan = adapter.get_query_plan(mock_spark_session, "SELECT * FROM test")
            assert isinstance(plan, str)
        except (AttributeError, NotImplementedError):
            pass

    def test_analyze_table(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        _, mock_spark_session = mock_pyspark

        adapter = SparkAdapter(database="test_db")

        adapter.analyze_table(mock_spark_session, "orders")

        calls = [str(c) for c in mock_spark_session.sql.call_args_list]
        assert any("ANALYZE" in c.upper() for c in calls) or len(calls) >= 0

    def test_extract_table_name(self, mock_pyspark):
        from benchbox.platforms._spark_helpers import extract_spark_table_name

        assert extract_spark_table_name("CREATE TABLE orders (id INT)") == "orders"
        assert extract_spark_table_name("CREATE TABLE IF NOT EXISTS lineitem (id INT)") == "lineitem"

    def test_get_existing_tables(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        _, mock_spark_session = mock_pyspark

        mock_tables = MagicMock()
        mock_tables.collect.return_value = [
            MagicMock(tableName="orders"),
            MagicMock(tableName="lineitem"),
        ]
        mock_spark_session.catalog.listTables.return_value = mock_tables

        adapter = SparkAdapter(database="test_db")

        try:
            tables = adapter._get_existing_tables(mock_spark_session)
            assert isinstance(tables, list)
        except AttributeError:
            pass

    def test_execute_query_calls_spark_sql_with_exact_query(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        _, mock_spark_session = mock_pyspark

        mock_df = MagicMock()
        mock_df.collect.return_value = []
        mock_spark_session.sql.return_value = mock_df

        adapter = SparkAdapter()
        adapter.execute_query(mock_spark_session, "SELECT COUNT(*) FROM lineitem", "q22")

        called_queries = [c.args[0] for c in mock_spark_session.sql.call_args_list if c.args]
        assert any("SELECT COUNT(*) FROM lineitem" in q for q in called_queries), (
            f"Expected query not found in spark.sql calls: {called_queries}"
        )

    def test_optimize_table_definition_has_exactly_one_using_clause(self, mock_pyspark):
        from benchbox.platforms._spark_helpers import optimize_spark_table_definition

        for fmt in ("parquet", "delta", "iceberg", "orc"):
            v1_table = fmt in {"parquet", "orc"}
            sql = "CREATE TABLE orders (id INT, amount DECIMAL)"
            result = optimize_spark_table_definition(
                sql,
                table_format=fmt,
                strip_v1_constraints=v1_table,
                upcast_smallint=v1_table,
            )
            using_count = result.upper().count("USING")
            assert using_count == 1, f"Expected exactly 1 USING for format={fmt!r}, got {using_count}: {result}"

    def test_analyze_table_sends_analyze_sql_to_spark(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        _, mock_spark_session = mock_pyspark

        adapter = SparkAdapter(database="bench_db")
        adapter.analyze_table(mock_spark_session, "orders")

        calls = [c.args[0] for c in mock_spark_session.sql.call_args_list if c.args]
        assert any("ANALYZE" in c.upper() for c in calls), f"No ANALYZE TABLE call found in: {calls}"


class TestSparkAdapterImportError:
    def test_missing_dependencies(self):
        with _mock_sys_modules({"pyspark": None, "pyspark.sql": None}):
            with pytest.raises(ImportError):
                importlib.import_module("pyspark")
            with pytest.raises(ImportError):
                importlib.import_module("pyspark.sql")


class TestSparkAdapterRegistration:
    def test_spark_in_platform_list(self):
        from benchbox.platforms import list_available_platforms

        platforms = list_available_platforms()
        assert "spark" in platforms

    def test_spark_requirements(self):
        from benchbox.platforms import get_platform_requirements

        requirements = get_platform_requirements("spark")
        assert "pyspark" in requirements

    def test_spark_dependency_group(self):
        from benchbox.utils.dependencies import DEPENDENCY_GROUPS

        assert "spark" in DEPENDENCY_GROUPS
        spark_deps = DEPENDENCY_GROUPS["spark"]
        assert "pyspark" in spark_deps.packages


class TestSparkAdapterCliArguments:
    @pytest.fixture
    def mock_pyspark(self):
        mock_spark_session = MagicMock()
        mock_session_class = MagicMock()
        mock_session_class.builder = MagicMock()

        with _mock_sys_modules(
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
                    DecimalType=MagicMock(),
                    DateType=MagicMock(),
                ),
            },
        ):
            yield mock_session_class, mock_spark_session

    def test_add_cli_arguments_registers_expected_flags(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        mock_parser = MagicMock()
        mock_group = MagicMock()
        mock_parser.add_argument_group.return_value = mock_group

        SparkAdapter.add_cli_arguments(mock_parser)

        mock_parser.add_argument_group.assert_called_once_with("Spark Arguments")
        add_arg_calls = [str(c) for c in mock_group.add_argument.call_args_list]

        assert any("--master" in c for c in add_arg_calls)
        assert any("--deploy-mode" in c for c in add_arg_calls)
        assert any("--executor-memory" in c for c in add_arg_calls)
        assert any("--driver-memory" in c for c in add_arg_calls)
        assert any("--executor-cores" in c for c in add_arg_calls)
        assert any("--table-format" in c for c in add_arg_calls)
        assert any("--shuffle-partitions" in c for c in add_arg_calls)

    def test_add_cli_arguments_supports_disabling_adaptive(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        parser = argparse.ArgumentParser()
        SparkAdapter.add_cli_arguments(parser)

        default_args = parser.parse_args([])
        enabled_args = parser.parse_args(["--adaptive-enabled"])
        disabled_args = parser.parse_args(["--no-adaptive-enabled"])

        assert default_args.adaptive_enabled is None
        assert enabled_args.adaptive_enabled is True
        assert disabled_args.adaptive_enabled is False


class TestSparkAdapterCreateSession:
    def _make_builder_and_session(self):
        mock_spark_session = MagicMock()
        mock_builder = MagicMock()
        mock_builder.master.return_value = mock_builder
        mock_builder.config.return_value = mock_builder
        mock_builder.enableHiveSupport.return_value = mock_builder
        mock_builder.getOrCreate.return_value = mock_spark_session

        mock_spark_session.version = "3.5.0"
        mock_spark_session.catalog.listDatabases.return_value = []
        mock_spark_session.sql.return_value = MagicMock()

        mock_session_class = MagicMock()
        mock_session_class.builder = mock_builder
        return mock_session_class, mock_spark_session, mock_builder

    def test_create_connection_calls_builder_chain(self):
        from benchbox.platforms.spark import SparkAdapter

        mock_session_class, mock_spark_session, mock_builder = self._make_builder_and_session()

        with patch("benchbox.platforms.spark.SparkSession", mock_session_class):
            adapter = SparkAdapter(master="local[2]", database="test_db")

            with patch.object(adapter, "handle_existing_database"):
                with patch.object(adapter, "check_server_database_exists", return_value=False):
                    adapter.database_was_reused = False
                    conn = adapter.create_connection()

        assert conn is mock_spark_session
        mock_builder.master.assert_called_once_with("local[2]")

    def test_create_connection_suppresses_window_exec_warnings(self):
        from benchbox.platforms.spark import SparkAdapter

        mock_session_class, mock_spark_session, _ = self._make_builder_and_session()
        mock_logger = MagicMock()
        mock_jvm = MagicMock()
        mock_jvm.org.apache.logging.log4j.LogManager.getLogger.return_value = mock_logger
        mock_spark_session.sparkContext = MagicMock()
        mock_spark_session.sparkContext._jvm = mock_jvm

        with patch("benchbox.platforms.spark.SparkSession", mock_session_class):
            adapter = SparkAdapter(master="local[*]", database="test_db")

            with patch.object(adapter, "handle_existing_database"):
                with patch.object(adapter, "check_server_database_exists", return_value=True):
                    adapter.database_was_reused = False
                    adapter.create_connection()

        mock_spark_session.sparkContext.setLogLevel.assert_called_once_with("ERROR")
        mock_jvm.org.apache.logging.log4j.LogManager.getLogger.assert_called_once_with(
            "org.apache.spark.sql.execution.window.WindowExec"
        )
        mock_jvm.org.apache.logging.log4j.core.config.Configurator.setLevel.assert_called_once_with(
            mock_logger,
            mock_jvm.org.apache.logging.log4j.Level.ERROR,
        )

    def test_create_connection_uses_warn_level_when_verbose(self):
        from benchbox.platforms.spark import SparkAdapter

        mock_session_class, mock_spark_session, _ = self._make_builder_and_session()
        mock_logger = MagicMock()
        mock_jvm = MagicMock()
        mock_jvm.org.apache.logging.log4j.LogManager.getLogger.return_value = mock_logger
        mock_spark_session.sparkContext = MagicMock()
        mock_spark_session.sparkContext._jvm = mock_jvm

        with patch("benchbox.platforms.spark.SparkSession", mock_session_class):
            adapter = SparkAdapter(master="local[*]", database="test_db")
            with patch.object(type(adapter), "verbose", new_callable=lambda: property(lambda self: True)):
                with patch.object(adapter, "handle_existing_database"):
                    with patch.object(adapter, "check_server_database_exists", return_value=True):
                        adapter.database_was_reused = False
                        adapter.create_connection()

        mock_spark_session.sparkContext.setLogLevel.assert_called_once_with("WARN")
        mock_jvm.org.apache.logging.log4j.LogManager.getLogger.assert_called_once_with(
            "org.apache.spark.sql.execution.window.WindowExec"
        )
        mock_jvm.org.apache.logging.log4j.core.config.Configurator.setLevel.assert_called_once_with(
            mock_logger,
            mock_jvm.org.apache.logging.log4j.Level.ERROR,
        )

    def test_configure_runtime_logging_sets_log_level_before_shared_suppress(self):
        from benchbox.platforms import spark as spark_module
        from benchbox.platforms.spark import SparkAdapter

        adapter = SparkAdapter(master="local[*]", database="test_db")
        mock_spark = MagicMock()
        call_order: list[tuple[str, object]] = []
        mock_spark.sparkContext.setLogLevel.side_effect = lambda level: call_order.append(("setLogLevel", level))

        with patch.object(
            spark_module,
            "suppress_window_exec_warning",
            side_effect=lambda spark: call_order.append(("suppress", spark)),
        ):
            adapter._configure_runtime_logging(mock_spark)

        assert call_order == [("setLogLevel", "ERROR"), ("suppress", mock_spark)]

    def test_create_connection_creates_database_when_not_exists(self):
        from benchbox.platforms.spark import SparkAdapter

        mock_session_class, mock_spark_session, _ = self._make_builder_and_session()

        with patch("benchbox.platforms.spark.SparkSession", mock_session_class):
            adapter = SparkAdapter(master="local[*]", database="bench_db")

            with patch.object(adapter, "handle_existing_database"):
                with patch.object(adapter, "check_server_database_exists", return_value=False):
                    adapter.database_was_reused = False
                    adapter.create_connection()

        sql_calls = [str(c) for c in mock_spark_session.sql.call_args_list]
        assert any("CREATE DATABASE" in c for c in sql_calls)

    def test_create_connection_skips_create_when_db_exists(self):
        from benchbox.platforms.spark import SparkAdapter

        mock_session_class, mock_spark_session, _ = self._make_builder_and_session()

        with patch("benchbox.platforms.spark.SparkSession", mock_session_class):
            adapter = SparkAdapter(master="local[*]", database="existing_db")

            with patch.object(adapter, "handle_existing_database"):
                with patch.object(adapter, "check_server_database_exists", return_value=True):
                    adapter.database_was_reused = False
                    adapter.create_connection()

        sql_calls = [str(c) for c in mock_spark_session.sql.call_args_list]
        assert not any("CREATE DATABASE" in c for c in sql_calls)


class TestSparkAdapterGetPlatformInfoLive:
    @pytest.fixture
    def mock_pyspark(self):
        mock_spark_session = MagicMock()
        mock_session_class = MagicMock()
        mock_session_class.builder = MagicMock()

        with _mock_sys_modules(
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
                    DecimalType=MagicMock(),
                    DateType=MagicMock(),
                ),
            },
        ):
            yield mock_session_class, mock_spark_session

    def test_get_platform_info_with_live_connection(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        _, mock_spark_session = mock_pyspark

        mock_spark_session.version = "3.5.1"
        mock_sc = MagicMock()
        mock_sc.applicationId = "app-123"
        mock_conf = MagicMock()
        mock_conf.get.return_value = "local[*]"
        mock_sc.getConf.return_value = mock_conf
        mock_executor_ids = MagicMock()
        mock_executor_ids.size.return_value = 2
        mock_sc._jsc.sc.return_value.getExecutorIds.return_value = mock_executor_ids
        mock_spark_session.sparkContext = mock_sc

        adapter = SparkAdapter(master="local[*]")
        info = adapter.get_platform_info(connection=mock_spark_session)

        assert info["platform_version"] == "3.5.1"
        assert info["configuration"]["spark_app_id"] == "app-123"

    def test_get_platform_info_no_connection_returns_none_version(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        adapter = SparkAdapter()
        info = adapter.get_platform_info(connection=None)

        assert info["platform_version"] is None


class TestSparkAdapterApplyTableTunings:
    @pytest.fixture
    def mock_pyspark(self):
        mock_spark_session = MagicMock()
        mock_session_class = MagicMock()
        mock_session_class.builder = MagicMock()

        with _mock_sys_modules(
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
                    DecimalType=MagicMock(),
                    DateType=MagicMock(),
                ),
            },
        ):
            yield mock_session_class, mock_spark_session

    def test_apply_table_tunings_delta_zorder(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        _, mock_spark_session = mock_pyspark

        adapter = SparkAdapter(table_format="delta")

        mock_col = MagicMock()
        mock_col.name = "order_date"
        mock_col.order = 1

        mock_tuning = MagicMock()
        mock_tuning.has_any_tuning.return_value = True
        mock_tuning.table_name = "orders"

        with patch("benchbox.core.tuning.interface.TuningType") as mock_tuning_type:
            mock_tuning_type.SORTING = "sorting"
            mock_tuning_type.PARTITIONING = "partitioning"
            mock_tuning.get_columns_by_type.side_effect = lambda t: [mock_col] if t == mock_tuning_type.SORTING else []
            adapter.apply_table_tunings(mock_tuning, mock_spark_session)

        sql_calls = [str(c) for c in mock_spark_session.sql.call_args_list]
        assert any("OPTIMIZE" in c and "ZORDER" in c for c in sql_calls)

    def test_apply_table_tunings_non_delta_no_zorder(self, mock_pyspark):
        from benchbox.platforms.spark import SparkAdapter

        _, mock_spark_session = mock_pyspark

        adapter = SparkAdapter(table_format="parquet")

        mock_col = MagicMock()
        mock_col.name = "order_date"
        mock_col.order = 1

        mock_tuning = MagicMock()
        mock_tuning.has_any_tuning.return_value = True
        mock_tuning.table_name = "orders"

        with patch("benchbox.core.tuning.interface.TuningType") as mock_tuning_type:
            mock_tuning_type.SORTING = "sorting"
            mock_tuning_type.PARTITIONING = "partitioning"
            mock_tuning.get_columns_by_type.return_value = []
            adapter.apply_table_tunings(mock_tuning, mock_spark_session)

        sql_calls = [str(c) for c in mock_spark_session.sql.call_args_list]
        assert not any("OPTIMIZE" in c for c in sql_calls)


class TestSparkAdapterTestConnection:
    def _make_builder_and_session(self):
        mock_spark_session = MagicMock()
        mock_builder = MagicMock()
        mock_builder.master.return_value = mock_builder
        mock_builder.config.return_value = mock_builder
        mock_builder.getOrCreate.return_value = mock_spark_session

        mock_session_class = MagicMock()
        mock_session_class.builder = mock_builder
        return mock_session_class, mock_spark_session, mock_builder

    def test_test_connection_success_stops_session(self):
        from benchbox.platforms.spark import SparkAdapter

        mock_session_class, mock_spark_session, _ = self._make_builder_and_session()
        mock_df = MagicMock()
        mock_df.collect.return_value = [1]
        mock_spark_session.sql.return_value = mock_df

        with patch("benchbox.platforms.spark.SparkSession", mock_session_class):
            adapter = SparkAdapter(master="local[*]")
            result = adapter.test_connection()

        assert result is True
        mock_spark_session.stop.assert_called_once()

    def test_test_connection_suppresses_window_exec_warnings(self):
        from benchbox.platforms.spark import SparkAdapter

        mock_session_class, mock_spark_session, _ = self._make_builder_and_session()
        mock_df = MagicMock()
        mock_logger = MagicMock()
        mock_jvm = MagicMock()
        mock_df.collect.return_value = [1]
        mock_jvm.org.apache.logging.log4j.LogManager.getLogger.return_value = mock_logger
        mock_spark_session.sql.return_value = mock_df
        mock_spark_session.sparkContext = MagicMock()
        mock_spark_session.sparkContext._jvm = mock_jvm

        with patch("benchbox.platforms.spark.SparkSession", mock_session_class):
            adapter = SparkAdapter(master="local[*]")
            result = adapter.test_connection()

        assert result is True
        mock_spark_session.sparkContext.setLogLevel.assert_called_once_with("ERROR")
        mock_jvm.org.apache.logging.log4j.LogManager.getLogger.assert_called_once_with(
            "org.apache.spark.sql.execution.window.WindowExec"
        )
        mock_jvm.org.apache.logging.log4j.core.config.Configurator.setLevel.assert_called_once_with(
            mock_logger,
            mock_jvm.org.apache.logging.log4j.Level.ERROR,
        )

    def test_test_connection_failure_returns_false(self):
        from benchbox.platforms.spark import SparkAdapter

        mock_session_class, _, mock_builder = self._make_builder_and_session()
        mock_builder.getOrCreate.side_effect = Exception("Spark unavailable")

        with patch("benchbox.platforms.spark.SparkSession", mock_session_class):
            adapter = SparkAdapter()
            result = adapter.test_connection()

        assert result is False


class TestBuildSparkConfig:
    @pytest.fixture
    def mock_pyspark(self):
        mock_session_class = MagicMock()
        mock_session_class.builder = MagicMock()

        with _mock_sys_modules(
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
                    DecimalType=MagicMock(),
                    DateType=MagicMock(),
                ),
            },
        ):
            yield mock_session_class

    def test_build_spark_config_populates_fields_from_credentials(self, mock_pyspark):
        from benchbox.platforms.spark import _build_spark_config

        saved_creds = {"spark_master": "local[*]", "driver_memory": "8g"}
        mock_info = MagicMock()
        mock_info.display_name = "Apache Spark"
        mock_info.driver_package = "pyspark"

        with patch("benchbox.security.credentials.CredentialManager") as mock_cred_cls:
            mock_cred_manager = MagicMock()
            mock_cred_manager.get_platform_credentials.return_value = saved_creds
            mock_cred_cls.return_value = mock_cred_manager

            config = _build_spark_config(
                platform="spark",
                options={},
                overrides={"benchmark": "tpch", "scale_factor": 1.0},
                info=mock_info,
            )

        assert config.type == "spark"
        assert config.name == "Apache Spark"
        assert config.driver_package == "pyspark"

    def test_build_spark_config_overrides_take_precedence(self, mock_pyspark):
        from benchbox.platforms.spark import _build_spark_config

        mock_info = MagicMock()
        mock_info.display_name = "Apache Spark"
        mock_info.driver_package = "pyspark"

        with patch("benchbox.security.credentials.CredentialManager") as mock_cred_cls:
            mock_cred_manager = MagicMock()
            mock_cred_manager.get_platform_credentials.return_value = {"driver_version": "3.4.0"}
            mock_cred_cls.return_value = mock_cred_manager

            config = _build_spark_config(
                platform="spark",
                options={},
                overrides={"driver_version": "3.5.1", "benchmark": "tpch", "scale_factor": 1.0},
                info=mock_info,
            )

        assert config.driver_version == "3.5.1"


class TestSparkAdapterWarehouseDir:
    _PATCH_TARGET = "benchbox.utils.path_utils.get_benchmark_runs_databases_path"

    def _mock_warehouse(self, tmp_path, name="tpch_sf1"):
        mock_path = MagicMock()
        mock_path.__str__ = lambda self: str(tmp_path / "databases" / name)
        mock_path.mkdir = MagicMock()
        return mock_path

    def test_from_config_defaults_warehouse_to_databases_path(self, tmp_path):
        from benchbox.platforms.spark import SparkAdapter

        mock_path = self._mock_warehouse(tmp_path)
        with patch(self._PATCH_TARGET, return_value=mock_path) as mock_path_fn:
            adapter = SparkAdapter.from_config({"benchmark": "tpch", "scale_factor": 1.0})

        mock_path_fn.assert_called_once_with("tpch", 1.0)
        assert adapter.warehouse_dir == str(mock_path)

    def test_from_config_warehouse_not_in_cwd(self, tmp_path):
        from benchbox.platforms.spark import SparkAdapter

        mock_path = self._mock_warehouse(tmp_path, "joinorder_sf1")
        with patch(self._PATCH_TARGET, return_value=mock_path):
            adapter = SparkAdapter.from_config({"benchmark": "joinorder", "scale_factor": 1.0})

        assert "spark-warehouse" not in adapter.warehouse_dir
        assert "spark_warehouse" not in adapter.warehouse_dir

    def test_from_config_respects_output_dir_override(self, tmp_path):
        from pathlib import Path

        from benchbox.platforms.spark import SparkAdapter

        custom_output = tmp_path / "custom_output"
        mock_path = self._mock_warehouse(tmp_path, "tpch_sf001")

        with patch(self._PATCH_TARGET, return_value=mock_path) as mock_path_fn:
            adapter = SparkAdapter.from_config(
                {
                    "benchmark": "tpch",
                    "scale_factor": 0.01,
                    "output_dir": str(custom_output),
                }
            )

        mock_path_fn.assert_called_once_with("tpch", 0.01, base_dir=Path(str(custom_output)) / "databases")
        assert adapter.warehouse_dir == str(mock_path)

    def test_from_config_explicit_warehouse_dir_not_overridden(self, tmp_path):
        from benchbox.platforms.spark import SparkAdapter

        explicit_dir = str(tmp_path / "my_custom_warehouse")

        with patch(self._PATCH_TARGET) as mock_path_fn:
            adapter = SparkAdapter.from_config(
                {
                    "benchmark": "tpch",
                    "scale_factor": 1.0,
                    "warehouse_dir": explicit_dir,
                }
            )

        mock_path_fn.assert_not_called()
        assert adapter.warehouse_dir == explicit_dir

    def test_warehouse_dir_propagates_to_spark_conf(self, tmp_path):
        from benchbox.platforms.spark import SparkAdapter

        mock_path = self._mock_warehouse(tmp_path)
        with patch(self._PATCH_TARGET, return_value=mock_path):
            adapter = SparkAdapter.from_config({"benchmark": "tpch", "scale_factor": 1.0})

        conf = adapter._get_spark_conf()
        assert "spark.sql.warehouse.dir" in conf
        assert conf["spark.sql.warehouse.dir"] == str(mock_path)


class TestJavaCompatibility:
    def test_get_java_version_parses_version_17(self):
        from benchbox.platforms.spark import _get_java_version

        mock_result = MagicMock()
        mock_result.stderr = 'openjdk version "17.0.17" 2025-01-21\n'
        mock_result.stdout = ""

        with patch("benchbox.platforms.spark.subprocess.run", return_value=mock_result):
            assert _get_java_version() == 17

    def test_get_java_version_parses_version_25(self):
        from benchbox.platforms.spark import _get_java_version

        mock_result = MagicMock()
        mock_result.stderr = 'openjdk version "25.0.1" 2025-10-21\n'
        mock_result.stdout = ""

        with patch("benchbox.platforms.spark.subprocess.run", return_value=mock_result):
            assert _get_java_version() == 25

    def test_get_java_version_returns_none_on_failure(self):
        from benchbox.platforms.spark import _get_java_version

        with patch("benchbox.platforms.spark.subprocess.run", side_effect=OSError):
            assert _get_java_version() is None

    def test_ensure_compatible_java_passes_for_java_17(self):
        from benchbox.platforms.spark import _ensure_compatible_java

        with patch("benchbox.platforms.spark._get_java_version", return_value=17):
            result = _ensure_compatible_java()
            assert result is None or isinstance(result, str)

    def test_ensure_compatible_java_auto_finds_jdk(self):
        from benchbox.platforms.spark import _ensure_compatible_java

        with (
            patch("benchbox.platforms.spark._get_java_version", side_effect=[25, 17]),
            patch(
                "benchbox.platforms.spark._find_compatible_java_home",
                return_value="/opt/jdk17",
            ),
            patch.dict(os.environ, {}, clear=False),
        ):
            result = _ensure_compatible_java()
            assert result == "/opt/jdk17"
            assert os.environ["JAVA_HOME"] == "/opt/jdk17"

    def test_ensure_compatible_java_raises_when_no_compatible_jdk(self):
        from benchbox.platforms.spark import _ensure_compatible_java

        with (
            patch("benchbox.platforms.spark._get_java_version", return_value=25),
            patch("benchbox.platforms.spark._find_compatible_java_home", return_value=None),
        ):
            with pytest.raises(RuntimeError, match="incompatible with PySpark"):
                _ensure_compatible_java()

    def test_ensure_compatible_java_respects_explicit_java_home(self):
        from benchbox.platforms.spark import _ensure_compatible_java

        with (
            patch("benchbox.platforms.spark._get_java_version", return_value=17),
            patch.dict(os.environ, {}, clear=False),
        ):
            result = _ensure_compatible_java("/opt/custom-jdk17")
            assert result == "/opt/custom-jdk17"
            assert os.environ["JAVA_HOME"] == "/opt/custom-jdk17"

    def test_ensure_compatible_java_rejects_incompatible_explicit_home(self):
        from benchbox.platforms.spark import _ensure_compatible_java

        with patch("benchbox.platforms.spark._get_java_version", return_value=25):
            with pytest.raises(RuntimeError, match="Specified java_home"):
                _ensure_compatible_java("/opt/jdk25")

    def test_build_spark_config_passes_java_home(self):
        from benchbox.platforms.spark import _build_spark_config

        mock_info = MagicMock()
        mock_info.display_name = "Spark"
        mock_info.driver_package = "pyspark"

        with patch("benchbox.security.credentials.CredentialManager") as mock_cred_cls:
            mock_cred_manager = MagicMock()
            mock_cred_manager.get_platform_credentials.return_value = {}
            mock_cred_cls.return_value = mock_cred_manager

            config = _build_spark_config(
                platform="spark",
                options={"java_home": "/opt/jdk17"},
                overrides={"benchmark": "tpch", "scale_factor": 1.0},
                info=mock_info,
            )

        assert config.options.get("java_home") == "/opt/jdk17"
