from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


class TestLakeSailAdapter:
    @pytest.fixture
    def mock_pyspark(self):

        mock_spark_session = MagicMock()
        mock_builder = MagicMock()
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
                    DecimalType=MagicMock(),
                    DateType=MagicMock(),
                ),
            },
        ):
            yield mock_session_class, mock_spark_session

    def test_initialization_success(self, mock_pyspark):

        from benchbox.platforms.lakesail import LakeSailAdapter

        config = {
            "endpoint": "sc://sail-server:50051",
            "app_name": "TestApp",
            "database": "test_db",
            "driver_memory": "8g",
        }

        adapter = LakeSailAdapter(**config)

        assert adapter.platform_name == "LakeSail"
        assert adapter.endpoint == "sc://sail-server:50051"
        assert adapter.app_name == "TestApp"
        assert adapter.database == "test_db"
        assert adapter.driver_memory == "8g"

    def test_csv_compression_codecs_includes_zstd(self, mock_pyspark):

        from benchbox.platforms.lakesail import LakeSailAdapter

        adapter = LakeSailAdapter()
        assert "zstd" in adapter._csv_compression_codecs
        assert isinstance(adapter._csv_compression_codecs, frozenset)

    def test_initialization_with_defaults(self, mock_pyspark):

        from benchbox.platforms.lakesail import LakeSailAdapter

        adapter = LakeSailAdapter()

        assert adapter.endpoint == "sc://localhost:50051"
        assert adapter.app_name == "BenchBox-LakeSail"
        assert adapter.database == "default"
        assert adapter.driver_memory == "4g"
        assert adapter.shuffle_partitions is None
        assert adapter.adaptive_enabled is None
        assert adapter.table_format == "parquet"
        assert adapter.sail_mode == "local"
        assert not hasattr(adapter, "sail_workers")
        assert adapter.disable_cache is True

    def test_initialization_with_distributed_mode(self, mock_pyspark):

        from benchbox.platforms.lakesail import LakeSailAdapter

        config = {
            "endpoint": "sc://cluster-master:50051",
            "sail_mode": "distributed",
            "sail_workers": 8,
        }

        adapter = LakeSailAdapter(**config)

        assert adapter.endpoint == "sc://cluster-master:50051"
        assert adapter.sail_mode == "distributed"
        assert not hasattr(adapter, "sail_workers")

    def test_get_target_dialect(self, mock_pyspark):

        from benchbox.platforms.lakesail import LakeSailAdapter

        adapter = LakeSailAdapter()
        assert adapter.get_target_dialect() == "spark"

    def test_vector_search_queries_use_lakesail_compat_key(self, mock_pyspark):

        from benchbox.platforms.lakesail import LakeSailAdapter

        adapter = LakeSailAdapter()
        benchmark = MagicMock()
        benchmark.get_queries.return_value = {}

        queries = adapter._get_dialect_queries(benchmark, "vector_search", connection=None)

        assert queries == {}
        benchmark.get_queries.assert_called_once_with(dialect="lakesail", platform_version=None)

    def test_unsupported_lakesail_benchmarks_are_gated(self, mock_pyspark):

        from benchbox.core.platform_registry import PlatformRegistry

        caps = PlatformRegistry.get_platform_capabilities("lakesail")

        assert caps is not None
        assert "ai_primitives" in caps.unsupported_benchmarks
        assert "get_create_tables_sql" in caps.unsupported_benchmarks["ai_primitives"]
        assert "metadata_primitives" in caps.unsupported_benchmarks
        assert "INFORMATION_SCHEMA" in caps.unsupported_benchmarks["metadata_primitives"]
        assert "transaction_primitives" in caps.unsupported_benchmarks
        assert "write_primitives" in caps.unsupported_benchmarks

    def test_platform_info(self, mock_pyspark):

        from benchbox.platforms.lakesail import LakeSailAdapter

        config = {
            "endpoint": "sc://sail:50051",
            "database": "benchmark",
            "driver_memory": "16g",
            "sail_mode": "distributed",
            "sail_workers": 4,
        }

        adapter = LakeSailAdapter(**config)
        info = adapter.get_platform_info(connection=None)

        assert info["platform_type"] == "lakesail"
        assert info["platform_name"] == "LakeSail Sail"
        assert info["connection_mode"] == "distributed"
        assert info["endpoint"] == "sc://sail:50051"
        assert info["configuration"]["database"] == "benchmark"
        assert info["configuration"]["driver_memory"] == "16g"
        assert info["configuration"]["sail_mode"] == "distributed"
        assert "sail_workers" not in info["configuration"]

    def test_platform_info_local_mode(self, mock_pyspark):

        from benchbox.platforms.lakesail import LakeSailAdapter

        adapter = LakeSailAdapter()
        info = adapter.get_platform_info(connection=None)

        assert info["connection_mode"] == "local"

    def test_from_config(self, mock_pyspark):

        from benchbox.platforms.lakesail import LakeSailAdapter

        config = {
            "benchmark": "TPC-H",
            "scale_factor": 10.0,
            "endpoint": "sc://sail:50051",
            "driver_memory": "8g",
        }

        adapter = LakeSailAdapter.from_config(config)

        assert adapter.driver_memory == "8g"
        assert adapter.endpoint == "sc://sail:50051"

        assert "tpch" in adapter.database.lower() or "benchmark" in adapter.database.lower()

    def test_from_config_adaptive_enabled_false_propagates(self, mock_pyspark):

        from benchbox.platforms.lakesail import LakeSailAdapter

        adapter = LakeSailAdapter.from_config({"benchmark": "TPC-H", "scale_factor": 1.0, "adaptive_enabled": False})

        assert adapter.adaptive_enabled is False

    def test_get_spark_conf(self, mock_pyspark):

        from benchbox.platforms.lakesail import LakeSailAdapter

        adapter = LakeSailAdapter(
            app_name="TestApp",
            shuffle_partitions=500,
            adaptive_enabled=True,
        )

        conf = adapter._get_spark_conf()

        assert conf["spark.app.name"] == "TestApp"
        assert conf["spark.sql.shuffle.partitions"] == "500"
        assert conf["spark.sql.adaptive.enabled"] == "true"
        assert conf["spark.sql.inMemoryColumnarStorage.enabled"] == "false"

    def test_get_spark_conf_no_adaptive(self, mock_pyspark):

        from benchbox.platforms.lakesail import LakeSailAdapter

        adapter = LakeSailAdapter(adaptive_enabled=False)

        conf = adapter._get_spark_conf()

        assert conf["spark.sql.adaptive.enabled"] == "false"
        assert conf["spark.sql.adaptive.coalescePartitions.enabled"] == "false"
        assert conf["spark.sql.adaptive.skewJoin.enabled"] == "false"

    def test_get_spark_conf_aqe_disabled_spark_config_still_wins(self, mock_pyspark):

        from benchbox.platforms.lakesail import LakeSailAdapter

        adapter = LakeSailAdapter(
            adaptive_enabled=False,
            spark_config={"spark.sql.adaptive.enabled": "true"},
        )

        conf = adapter._get_spark_conf()

        assert conf["spark.sql.adaptive.enabled"] == "true"

    def test_add_cli_arguments_supports_disabling_adaptive(self, mock_pyspark):

        from benchbox.platforms.lakesail import LakeSailAdapter

        parser = argparse.ArgumentParser()
        LakeSailAdapter.add_cli_arguments(parser)

        default_args = parser.parse_args([])
        disabled_args = parser.parse_args(["--no-adaptive-enabled"])

        assert default_args.adaptive_enabled is None
        assert disabled_args.adaptive_enabled is False

    def test_normalize_table_name(self, mock_pyspark):

        from benchbox.platforms._spark_helpers import normalize_spark_table_name_in_sql

        sql = 'CREATE TABLE "CUSTOMER" (id INT)'
        normalized = normalize_spark_table_name_in_sql(sql)

        assert "customer" in normalized.lower()

    def test_validate_identifier(self, mock_pyspark):

        from benchbox.platforms._spark_helpers import validate_spark_identifier

        assert validate_spark_identifier("my_table") is True
        assert validate_spark_identifier("_private") is True
        assert validate_spark_identifier("Table123") is True

        assert validate_spark_identifier("") is False
        assert validate_spark_identifier("123table") is False
        assert validate_spark_identifier("table-name") is False
        assert validate_spark_identifier("table; DROP TABLE users") is False

    def test_supports_tuning_type(self, mock_pyspark):

        from benchbox.platforms.lakesail import LakeSailAdapter

        adapter = LakeSailAdapter()

        try:
            from benchbox.core.tuning.interface import TuningType

            assert adapter.supports_tuning_type(TuningType.PARTITIONING) is True
            assert adapter.supports_tuning_type(TuningType.SORTING) is True
            assert adapter.supports_tuning_type(TuningType.CLUSTERING) is False
        except ImportError:
            pass

    def test_extract_table_name(self, mock_pyspark):

        from benchbox.platforms._spark_helpers import extract_spark_table_name

        assert extract_spark_table_name("CREATE TABLE orders (id INT)") == "orders"
        assert extract_spark_table_name("CREATE TABLE IF NOT EXISTS lineitem (id INT)") == "lineitem"

    def test_create_connection_initializes_session_before_existing_db_handling(self, mock_pyspark):

        import benchbox.platforms.lakesail as lakesail_module
        from benchbox.platforms.lakesail import LakeSailAdapter

        mock_session_class, mock_spark_session = mock_pyspark
        with patch.object(lakesail_module, "SparkSession", mock_session_class):
            adapter = LakeSailAdapter(database="test_db")

            def _assert_session_ready(**_kwargs):
                assert adapter._spark_session is mock_spark_session

            with (
                patch.object(adapter, "_ensure_server_ready"),
                patch.object(adapter, "handle_existing_database", side_effect=_assert_session_ready) as mocked_handle,
            ):
                connection = adapter.create_connection()

            assert connection is mock_spark_session
            mocked_handle.assert_called_once()


class TestLakeSailAdapterExecution:
    @pytest.fixture
    def mock_pyspark(self):

        mock_spark_session = MagicMock()
        mock_builder = MagicMock()
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
                    DecimalType=MagicMock(),
                    DateType=MagicMock(),
                ),
            },
        ):
            yield mock_session_class, mock_spark_session

    def test_execute_query_success(self, mock_pyspark):

        from benchbox.platforms.lakesail import LakeSailAdapter

        _, mock_spark_session = mock_pyspark

        mock_df = MagicMock()
        mock_df.collect.return_value = [(1, "test"), (2, "test2")]
        mock_df.count.return_value = 2
        mock_spark_session.sql.return_value = mock_df

        adapter = LakeSailAdapter()

        result = adapter.execute_query(mock_spark_session, "SELECT * FROM test", "q1")

        assert result["query_id"] == "q1"
        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 2

    def test_execute_query_disable_cache_skips_unsupported_clear_cache(self, mock_pyspark):

        from benchbox.platforms.lakesail import LakeSailAdapter

        _, mock_spark_session = mock_pyspark

        mock_df = MagicMock()
        mock_df.collect.return_value = [(1,)]
        mock_spark_session.sql.return_value = mock_df

        adapter = LakeSailAdapter(disable_cache=True)

        result = adapter.execute_query(mock_spark_session, "SELECT 1", "q1")

        assert result["status"] == "SUCCESS"
        mock_spark_session.catalog.clearCache.assert_not_called()

    def test_execute_query_failure(self, mock_pyspark):

        from benchbox.platforms.lakesail import LakeSailAdapter

        _, mock_spark_session = mock_pyspark
        mock_spark_session.sql.side_effect = Exception("Query failed")

        adapter = LakeSailAdapter()

        result = adapter.execute_query(mock_spark_session, "INVALID SQL", "q1")

        assert result["query_id"] == "q1"
        assert result["status"] == "FAILED"
        assert "Query failed" in result.get("error", "")

    def test_close_connection(self, mock_pyspark):

        from benchbox.platforms.lakesail import LakeSailAdapter

        _, mock_spark_session = mock_pyspark

        adapter = LakeSailAdapter()
        adapter.close_connection(mock_spark_session)

        mock_spark_session.stop.assert_called_once()

    def test_close_connection_handles_none(self, mock_pyspark):

        from benchbox.platforms.lakesail import LakeSailAdapter

        adapter = LakeSailAdapter()

        adapter.close_connection(None)

    def test_configure_for_benchmark_olap(self, mock_pyspark):

        from benchbox.platforms.lakesail import LakeSailAdapter

        _, mock_spark_session = mock_pyspark

        adapter = LakeSailAdapter()

        adapter.configure_for_benchmark(mock_spark_session, "olap")

    def test_configure_for_benchmark_leaves_engine_defaults(self, mock_pyspark):

        from benchbox.platforms.lakesail import LakeSailAdapter

        _, mock_spark_session = mock_pyspark

        adapter = LakeSailAdapter(adaptive_enabled=False)
        adapter.configure_for_benchmark(mock_spark_session, "tpch")

        mock_spark_session.conf.set.assert_not_called()

    def test_tuned_run_applies_olap_session_defaults(self, mock_pyspark):
        from benchbox.platforms.lakesail import LakeSailAdapter

        _, mock_spark_session = mock_pyspark
        adapter = LakeSailAdapter(tuning_enabled=True)

        conf = adapter._get_spark_conf()
        adapter.configure_for_benchmark(mock_spark_session, "tpch")

        assert conf["spark.sql.shuffle.partitions"] == "200"
        assert conf["spark.sql.adaptive.enabled"] == "true"
        mock_spark_session.conf.set.assert_any_call("spark.sql.cbo.enabled", "true")

    def test_tuned_spark_map_is_dropped_not_set(self, mock_pyspark):
        from benchbox.core.tuning.applied_ledger import AppliedTuningLedger
        from benchbox.core.tuning.interface import UnifiedTuningConfiguration
        from benchbox.platforms.lakesail import LakeSailAdapter

        _, mock_spark_session = mock_pyspark
        adapter = LakeSailAdapter(tuning_enabled=True)
        adapter._applied_tuning_ledger = AppliedTuningLedger()
        adapter.unified_tuning_configuration = UnifiedTuningConfiguration.from_dict(
            {"platform_optimizations": {"spark": {"spark.sql.files.maxPartitionBytes": "64m"}}}
        )

        adapter.configure_for_benchmark(mock_spark_session, "tpch")

        set_keys = [call.args[0] for call in mock_spark_session.conf.set.call_args_list]
        assert "spark.sql.files.maxPartitionBytes" not in set_keys
        assert [item.intent for item in adapter._applied_tuning_ledger.dropped] == [
            "spark_session_config:spark.sql.files.maxPartitionBytes"
        ]

    def test_spark_config_keeps_explicit_session_override(self, mock_pyspark):

        from benchbox.platforms.lakesail import LakeSailAdapter

        _, mock_spark_session = mock_pyspark

        adapter = LakeSailAdapter(spark_config={"spark.sql.adaptive.enabled": "false"})
        assert adapter._get_spark_conf()["spark.sql.adaptive.enabled"] == "false"

    def test_get_query_plan(self, mock_pyspark):

        from benchbox.platforms.lakesail import LakeSailAdapter

        _, mock_spark_session = mock_pyspark

        mock_df = MagicMock()
        mock_df.collect.return_value = [("== Physical Plan ==\nScan",)]
        mock_spark_session.sql.return_value = mock_df

        adapter = LakeSailAdapter()

        plan = adapter.get_query_plan(mock_spark_session, "SELECT * FROM test")
        assert isinstance(plan, str)

    def test_get_query_plan_failure_returns_none(self, mock_pyspark):

        from benchbox.platforms.lakesail import LakeSailAdapter

        _, mock_spark_session = mock_pyspark
        mock_spark_session.sql.side_effect = RuntimeError("explain failed")

        adapter = LakeSailAdapter()

        assert adapter.get_query_plan(mock_spark_session, "SELECT * FROM test") is None

    def test_analyze_table(self, mock_pyspark):

        from benchbox.platforms.lakesail import LakeSailAdapter

        _, mock_spark_session = mock_pyspark

        adapter = LakeSailAdapter(database="test_db")

        adapter.analyze_table(mock_spark_session, "orders")

    def test_generate_tuning_clause_partitioning(self, mock_pyspark):

        from benchbox.platforms.lakesail import LakeSailAdapter

        adapter = LakeSailAdapter()

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

    def test_generate_tuning_clause_empty(self, mock_pyspark):

        from benchbox.platforms.lakesail import LakeSailAdapter

        adapter = LakeSailAdapter()

        mock_tuning = MagicMock()
        mock_tuning.has_any_tuning.return_value = False

        clause = adapter.generate_tuning_clause(mock_tuning)
        assert clause == ""


class TestLakeSailAdapterRegistration:
    def test_lakesail_in_platform_list(self):

        from benchbox.platforms import list_available_platforms

        platforms = list_available_platforms()
        assert "lakesail" in platforms

    def test_lakesail_requirements(self):

        from benchbox.platforms import get_platform_requirements

        requirements = get_platform_requirements("lakesail")
        assert "pyspark" in requirements

    def test_lakesail_dependency_group(self):

        from benchbox.utils.dependencies import DEPENDENCY_GROUPS

        assert "lakesail" in DEPENDENCY_GROUPS
        lakesail_deps = DEPENDENCY_GROUPS["lakesail"]
        assert "pyspark" in lakesail_deps.packages

    def test_lakesail_df_is_dataframe_platform(self):

        from benchbox.platforms import is_dataframe_platform

        assert is_dataframe_platform("lakesail-df") is True

    def test_lakesail_platform_info_from_registry(self):

        from benchbox.core.platform_registry import PlatformRegistry

        info = PlatformRegistry.get_platform_info("lakesail")
        assert info is not None
        assert info.display_name == "LakeSail Sail"


class TestLakeSailIdentifierValidation:
    @pytest.fixture
    def mock_pyspark(self):

        mock_spark_session = MagicMock()
        mock_builder = MagicMock()
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
                "pyspark.sql.types": MagicMock(),
            },
        ):
            yield mock_session_class, mock_spark_session

    def test_create_connection_rejects_invalid_database(self, mock_pyspark):

        import benchbox.platforms.lakesail as lakesail_module
        from benchbox.platforms.lakesail import LakeSailAdapter

        mock_session_class, _mock_spark_session = mock_pyspark
        with patch.object(lakesail_module, "SparkSession", mock_session_class):
            adapter = LakeSailAdapter(database="valid_db")

            with (
                patch.object(adapter, "_ensure_server_ready"),
                patch.object(adapter, "handle_existing_database"),
                pytest.raises(ValueError, match="Invalid database identifier"),
            ):
                adapter.create_connection(database="DROP TABLE; --")

    def test_normalize_table_name_preserves_if_not_exists(self, mock_pyspark):

        from benchbox.platforms._spark_helpers import normalize_spark_table_name_in_sql
        from benchbox.platforms.lakesail import LakeSailAdapter

        result = normalize_spark_table_name_in_sql("CREATE TABLE IF NOT EXISTS LINEITEM (id INT)")

        assert "IF NOT EXISTS" in result
        assert "lineitem" in result

    def test_normalize_table_name_works_without_if_not_exists(self, mock_pyspark):

        from benchbox.platforms._spark_helpers import normalize_spark_table_name_in_sql

        result = normalize_spark_table_name_in_sql("CREATE TABLE ORDERS (id INT)")

        assert "orders" in result
        assert "IF NOT EXISTS" not in result


class TestLakeSailServerLifecycle:
    @pytest.fixture
    def mock_pyspark(self):

        mock_spark_session = MagicMock()
        mock_builder = MagicMock()
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
                "pyspark.sql.types": MagicMock(),
            },
        ):
            yield mock_session_class, mock_spark_session

    def test_parse_endpoint_standard(self, mock_pyspark):

        from benchbox.platforms._spark_helpers import parse_spark_connect_endpoint

        assert parse_spark_connect_endpoint("sc://myhost:9999") == ("myhost", 9999)

    def test_parse_endpoint_default_port(self, mock_pyspark):

        from benchbox.platforms._spark_helpers import parse_spark_connect_endpoint

        assert parse_spark_connect_endpoint("sc://myhost") == ("myhost", 50051)

    def test_parse_endpoint_no_scheme(self, mock_pyspark):

        from benchbox.platforms._spark_helpers import parse_spark_connect_endpoint

        assert parse_spark_connect_endpoint("myhost:8080") == ("myhost", 8080)

    def test_parse_endpoint_invalid_port(self, mock_pyspark):

        from benchbox.platforms._spark_helpers import parse_spark_connect_endpoint

        assert parse_spark_connect_endpoint("sc://myhost:notaport") == ("myhost", 50051)

    def test_parse_endpoint_empty_host(self, mock_pyspark):

        from benchbox.platforms._spark_helpers import parse_spark_connect_endpoint

        assert parse_spark_connect_endpoint("sc://:50051") == ("localhost", 50051)

    def test_is_server_reachable_true(self, mock_pyspark):

        from benchbox.platforms._spark_helpers import is_spark_connect_reachable

        with patch("benchbox.platforms._spark_helpers.socket.create_connection"):
            assert is_spark_connect_reachable("sc://localhost:50051") is True

    def test_is_server_reachable_false(self, mock_pyspark):

        from benchbox.platforms._spark_helpers import is_spark_connect_reachable

        with patch(
            "benchbox.platforms._spark_helpers.socket.create_connection",
            side_effect=OSError("refused"),
        ):
            assert is_spark_connect_reachable("sc://localhost:50051") is False

    def test_ensure_server_ready_already_reachable(self, mock_pyspark):

        from benchbox.platforms.lakesail import LakeSailAdapter

        adapter = LakeSailAdapter()
        with patch("benchbox.platforms.lakesail.is_spark_connect_reachable", return_value=True):
            adapter._ensure_server_ready()

    def test_ensure_server_ready_distributed_unreachable(self, mock_pyspark):

        from benchbox.platforms.lakesail import LakeSailAdapter

        adapter = LakeSailAdapter(sail_mode="distributed", endpoint="sc://cluster:50051")
        with patch("benchbox.platforms.lakesail.is_spark_connect_reachable", return_value=False):
            with pytest.raises(RuntimeError, match="Cannot connect to LakeSail Sail server"):
                adapter._ensure_server_ready()

    def test_ensure_server_ready_local_no_pysail(self, mock_pyspark):

        from benchbox.platforms.lakesail import LakeSailAdapter

        adapter = LakeSailAdapter(sail_mode="local")
        with (
            patch("benchbox.platforms.lakesail.is_spark_connect_reachable", return_value=False),
            patch.dict("sys.modules", {"pysail": None}),
            pytest.raises(RuntimeError, match="pysail.*not installed"),
        ):
            adapter._ensure_server_ready()

    def test_ensure_server_ready_local_auto_starts(self, mock_pyspark):

        from benchbox.platforms.lakesail import LakeSailAdapter

        adapter = LakeSailAdapter(sail_mode="local")
        with (
            patch("benchbox.platforms.lakesail.is_spark_connect_reachable", return_value=False),
            patch.dict("sys.modules", {"pysail": MagicMock()}),
            patch.object(adapter, "_auto_start_local_server") as mock_auto,
        ):
            adapter._ensure_server_ready()
            mock_auto.assert_called_once()

    def test_auto_start_server_success(self, mock_pyspark):

        from benchbox.platforms.lakesail import LakeSailAdapter

        adapter = LakeSailAdapter()

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None

        reachable_calls = iter([False, True])

        with (
            patch("subprocess.Popen", return_value=mock_proc),
            patch(
                "benchbox.platforms.lakesail.is_spark_connect_reachable",
                side_effect=reachable_calls,
            ),
            patch("atexit.register"),
        ):
            adapter._auto_start_local_server()

        assert adapter._managed_server_process is mock_proc

    def test_auto_start_server_process_crashes(self, mock_pyspark):

        from benchbox.platforms.lakesail import LakeSailAdapter

        adapter = LakeSailAdapter()

        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1
        mock_proc.returncode = 1
        mock_proc.stderr.read.return_value = b"Address already in use"

        with (
            patch("subprocess.Popen", return_value=mock_proc),
            patch("atexit.register"),
            pytest.raises(RuntimeError, match="exited immediately.*Address already in use"),
        ):
            adapter._auto_start_local_server()

    def test_auto_start_server_timeout(self, mock_pyspark):

        import time

        from benchbox.platforms.lakesail import LakeSailAdapter

        adapter = LakeSailAdapter()

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stderr.read.return_value = b"some debug output"

        start = 1000.0
        with (
            patch("subprocess.Popen", return_value=mock_proc),
            patch("benchbox.platforms.lakesail.is_spark_connect_reachable", return_value=False),
            patch("time.monotonic", side_effect=[start, start, start + 11]),
            patch("time.sleep"),
            patch("atexit.register"),
            pytest.raises(RuntimeError, match=r"(?s)did not become ready.*some debug output"),
        ):
            adapter._auto_start_local_server()

        mock_proc.terminate.assert_called_once()
        mock_proc.wait.assert_called_once_with(timeout=5)

    def test_close_connection_stops_managed_server(self, mock_pyspark):

        from benchbox.platforms.lakesail import LakeSailAdapter

        _, mock_spark_session = mock_pyspark
        adapter = LakeSailAdapter()

        mock_proc = MagicMock()
        adapter._managed_server_process = mock_proc
        cleanup_fn = MagicMock()
        adapter._managed_server_atexit = cleanup_fn

        with patch("atexit.unregister") as mock_unregister:
            adapter.close_connection(mock_spark_session)

        mock_proc.terminate.assert_called_once()
        mock_proc.wait.assert_called_once_with(timeout=5)
        assert adapter._managed_server_process is None
        mock_unregister.assert_called_once_with(cleanup_fn)

    def test_close_connection_kills_on_timeout(self, mock_pyspark):

        import subprocess

        from benchbox.platforms.lakesail import LakeSailAdapter

        _, mock_spark_session = mock_pyspark
        adapter = LakeSailAdapter()

        mock_proc = MagicMock()
        mock_proc.wait.side_effect = subprocess.TimeoutExpired(cmd="pysail", timeout=5)
        adapter._managed_server_process = mock_proc

        adapter.close_connection(mock_spark_session)

        mock_proc.terminate.assert_called_once()
        mock_proc.kill.assert_called_once()
        assert adapter._managed_server_process is None
