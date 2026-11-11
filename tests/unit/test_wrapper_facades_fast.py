from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from benchbox.amplab import AMPLab
from benchbox.clickbench import ClickBench
from benchbox.coffeeshop import CoffeeShop
from benchbox.datavault import DataVault
from benchbox.h2odb import H2ODB
from benchbox.read_primitives import ReadPrimitives
from benchbox.ssb import SSB
from benchbox.tpcdi import TPCDI
from benchbox.tpcds import TPCDS
from benchbox.tpcds_obt import TPCDSOBT
from benchbox.tpch import TPCH
from benchbox.transaction_primitives import TransactionPrimitives
from benchbox.write_primitives import WritePrimitives

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_tpch_wrapper_proxies_queries_streams_and_compatibility_runners(tmp_path: Path) -> None:
    impl = MagicMock()
    impl.generate_data.return_value = [tmp_path / "lineitem.tbl"]
    impl.get_queries.return_value = {"1": "SELECT 1"}
    impl.get_query.return_value = "SELECT 1"
    impl.get_schema.return_value = {"lineitem": {}}
    impl.get_create_tables_sql.return_value = "CREATE TABLE lineitem (id INT)"
    impl.generate_streams.return_value = [tmp_path / "stream1.sql"]
    impl.get_stream_info.return_value = {"stream_id": 1}
    impl.get_all_streams_info.return_value = [{"stream_id": 1}]
    impl.tables = {"lineitem": tmp_path / "lineitem.tbl"}

    with patch("benchbox.tpch.TPCHBenchmark", return_value=impl):
        tpch = TPCH(scale_factor=0.01, output_dir=tmp_path)

    assert tpch.generate_data() == [tmp_path / "lineitem.tbl"]
    assert tpch.get_queries(dialect="duckdb", base_dialect="netezza") == {"1": "SELECT 1"}
    assert tpch.get_query(1, seed=7, scale_factor=1.0, dialect="duckdb") == "SELECT 1"
    assert tpch.get_schema() == {"lineitem": {}}
    assert tpch.get_create_tables_sql(dialect="duckdb") == "CREATE TABLE lineitem (id INT)"
    assert tpch.generate_streams(num_streams=2, rng_seed=11) == [tmp_path / "stream1.sql"]
    assert tpch.get_stream_info(1) == {"stream_id": 1}
    assert tpch.get_all_streams_info() == [{"stream_id": 1}]
    assert tpch.tables == {"lineitem": tmp_path / "lineitem.tbl"}

    with pytest.raises(TypeError, match="query_id must be an integer"):
        tpch.get_query("1")

    with pytest.raises(ValueError, match="Query ID must be 1-22"):
        tpch.get_query(0)

    with pytest.raises(TypeError, match="seed must be an integer"):
        tpch.get_query(1, seed="bad")

    with patch("benchbox.core.tpch.official_benchmark.TPCHOfficialBenchmark") as official_cls:
        official_cls.return_value.run_official_benchmark.return_value = {"status": "official"}
        assert tpch.run_official_benchmark(lambda: object(), {"mode": "strict"}) == {"status": "official"}

    with patch("benchbox.core.tpch.power_test.TPCHPowerTest") as power_cls:
        power_cls.return_value.run.return_value = {"status": "power"}
        assert tpch.run_power_test(lambda: object(), {"seed": 7}) == {"status": "power"}

    with patch("benchbox.core.tpch.maintenance_test.TPCHMaintenanceTest") as maintenance_cls:
        maintenance_cls.return_value.run.return_value = {"status": "maintenance"}
        assert tpch.run_maintenance_test(lambda: object(), {"rf": 1}) == {"status": "maintenance"}


def test_tpch_wrapper_fallback_paths_return_compatibility_messages(tmp_path: Path) -> None:
    impl = MagicMock()

    with patch("benchbox.tpch.TPCHBenchmark", return_value=impl):
        tpch = TPCH(scale_factor=0.01, output_dir=tmp_path)

    import builtins

    real_import = builtins.__import__

    def _raise_for(target: str):
        def _import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == target:
                raise ImportError(f"missing {target}")
            return real_import(name, globals, locals, fromlist, level)

        return _import

    with patch("builtins.__import__", side_effect=_raise_for("benchbox.core.tpch.official_benchmark")):
        official = tpch.run_official_benchmark(lambda: object())
    assert official == {"status": "fallback", "message": "Use adapter.run_benchmark() instead"}

    with patch("builtins.__import__", side_effect=_raise_for("benchbox.core.tpch.power_test")):
        power = tpch.run_power_test(lambda: object())
    assert power == {"status": "fallback", "message": "Use adapter.run_benchmark() instead"}

    with patch("builtins.__import__", side_effect=_raise_for("benchbox.core.tpch.maintenance_test")):
        maintenance = tpch.run_maintenance_test(lambda: object())
    assert maintenance["status"] == "fallback"
    assert maintenance["message"] == "Use adapter.run_benchmark() instead"
    assert maintenance["refresh_functions"]["refresh_function_1"]["rows_inserted"] == 150


def test_tpcdi_wrapper_proxies_etl_and_public_properties(tmp_path: Path) -> None:
    impl = MagicMock()
    impl.generate_data.return_value = [tmp_path / "batch.csv"]
    impl.get_queries.return_value = {"V1": "SELECT 1"}
    impl.get_query.return_value = "SELECT 1"
    impl.get_schema.return_value = {"customer": {}}
    impl.get_create_tables_sql.return_value = "CREATE TABLE customer (id INT)"
    impl.generate_source_data.return_value = {"csv": ["batch.csv"]}
    impl.run_etl_pipeline.return_value = {"status": "etl"}
    impl.validate_etl_results.return_value = {"status": "valid"}
    impl.get_etl_status.return_value = {"phase": "ready"}
    impl.load_data_to_database.return_value = None
    impl.run_benchmark.return_value = {"status": "benchmark"}
    impl.execute_query.return_value = [("ok",)]
    impl.create_schema.return_value = None
    impl.run_full_benchmark.return_value = {"status": "full"}
    impl.run_etl_benchmark.return_value = {"status": "etl-benchmark"}
    impl.run_data_validation.return_value = {"status": "validation"}
    impl.calculate_official_metrics.return_value = {"score": 1.0}
    impl.optimize_database.return_value = {"optimized": True}
    impl.validator = object()
    impl.schema_manager = object()
    impl.metrics_calculator = object()

    with patch("benchbox.tpcdi.TPCDIBenchmark", return_value=impl):
        tpcdi = TPCDI(scale_factor=0.01, output_dir=tmp_path, verbose=True)

    assert tpcdi.generate_data() == [tmp_path / "batch.csv"]
    assert tpcdi.get_queries(dialect="duckdb") == {"V1": "SELECT 1"}
    assert tpcdi.get_query("V1") == "SELECT 1"
    assert tpcdi.get_schema("duckdb") == {"customer": {}}
    assert tpcdi.get_create_tables_sql(dialect="duckdb") == "CREATE TABLE customer (id INT)"
    assert tpcdi.generate_source_data(["csv"], ["historical"]) == {"csv": ["batch.csv"]}
    assert tpcdi.run_etl_pipeline("conn") == {"status": "etl"}
    assert tpcdi.validate_etl_results("conn") == {"status": "valid"}
    assert tpcdi.get_etl_status() == {"phase": "ready"}
    assert tpcdi.etl_mode is True
    assert tpcdi.load_data_to_database("conn", ["customer"]) is None
    assert tpcdi.run_benchmark("conn") == {"status": "benchmark"}
    assert tpcdi.execute_query("V1", "conn") == [("ok",)]
    assert tpcdi.create_schema("conn") is None
    assert tpcdi.run_full_benchmark("conn") == {"status": "full"}
    assert tpcdi.run_etl_benchmark("conn") == {"status": "etl-benchmark"}
    assert tpcdi.run_data_validation("conn") == {"status": "validation"}
    assert tpcdi.calculate_official_metrics("etl", "validation") == {"score": 1.0}
    assert tpcdi.optimize_database("conn") == {"optimized": True}
    assert tpcdi.validator is impl.validator
    assert tpcdi.schema_manager is impl.schema_manager
    assert tpcdi.metrics_calculator is impl.metrics_calculator


def test_read_primitives_wrapper_proxies_generation_and_benchmark_methods(tmp_path: Path) -> None:
    impl = MagicMock()
    impl.tables = {"lineitem": str(tmp_path / "lineitem.tbl")}
    impl.get_schema.return_value = {"lineitem": {}}
    impl.get_create_tables_sql.return_value = "CREATE TABLE lineitem (id INT)"
    impl.execute_query.return_value = [("row",)]
    impl.run_benchmark.return_value = {"status": "run"}
    impl.run_category_benchmark.return_value = {"status": "category"}
    impl.get_benchmark_info.return_value = {"benchmark": "read_primitives"}

    with patch("benchbox.read_primitives.ReadPrimitivesBenchmark", return_value=impl):
        benchmark = ReadPrimitives(scale_factor=0.01, output_dir=tmp_path, verbose=True)

    assert benchmark.generate_data() == {"lineitem": str(tmp_path / "lineitem.tbl")}
    impl.generate_data.assert_called_once_with(None)
    assert benchmark.get_schema() == {"lineitem": {}}
    assert benchmark.get_create_tables_sql(dialect="duckdb") == "CREATE TABLE lineitem (id INT)"
    assert benchmark.load_data_to_database("conn", ["lineitem"]) is impl.load_data_to_database.return_value
    assert benchmark.execute_query("aggregation_simple", "conn") == [("row",)]
    assert benchmark.run_benchmark("conn") == {"status": "run"}
    assert benchmark.run_category_benchmark("conn", "aggregation") == {"status": "category"}
    assert benchmark.get_benchmark_info() == {"benchmark": "read_primitives"}


def test_write_primitives_wrapper_proxies_operation_facade_methods(tmp_path: Path) -> None:
    impl = MagicMock()
    impl.output_dir = tmp_path / "tpch_data"
    impl.get_data_source_benchmark.return_value = "tpch"
    impl.tables = {"orders": tmp_path / "orders.tbl"}
    impl.generate_data.return_value = [tmp_path / "orders.tbl"]
    impl.get_operation.return_value = {"id": "insert_single_row"}
    impl.get_all_operations.return_value = {"insert_single_row": {"id": "insert_single_row"}}
    impl.get_schema.return_value = {"orders_staging": {}}
    impl.get_create_tables_sql.return_value = "CREATE TABLE orders_staging (id INT)"
    impl.get_benchmark_info.return_value = {"benchmark": "write_primitives"}
    impl.setup.return_value = {"status": "setup"}
    impl.load_data.return_value = {"status": "loaded"}
    impl.is_setup.return_value = True
    impl.execute_operation.return_value = {"status": "executed"}
    impl.run_benchmark.return_value = [{"status": "ok"}]

    with patch("benchbox.write_primitives.WritePrimitivesBenchmark", return_value=impl):
        benchmark = WritePrimitives(scale_factor=0.01, output_dir=tmp_path)

    assert benchmark.output_dir == impl.output_dir
    assert benchmark.get_data_source_benchmark() == "tpch"
    assert benchmark.tables == {"orders": tmp_path / "orders.tbl"}
    assert benchmark.generate_data() == [tmp_path / "orders.tbl"]
    assert benchmark.get_operation("insert_single_row") == {"id": "insert_single_row"}
    assert benchmark.get_all_operations() == {"insert_single_row": {"id": "insert_single_row"}}
    assert benchmark.get_schema(dialect="duckdb") == {"orders_staging": {}}
    assert benchmark.get_create_tables_sql(dialect="duckdb") == "CREATE TABLE orders_staging (id INT)"
    assert benchmark.get_benchmark_info() == {"benchmark": "write_primitives"}
    assert benchmark.setup("conn", force=True) == {"status": "setup"}
    assert benchmark.load_data("conn", force=True) == {"status": "loaded"}
    benchmark.teardown("conn")
    benchmark.reset("conn")
    assert benchmark.is_setup("conn") is True
    assert benchmark.execute_operation("insert_single_row", "conn", platform_key="duckdb") == {"status": "executed"}
    assert benchmark.run_benchmark("conn", operation_ids=["insert_single_row"]) == [{"status": "ok"}]


def test_coffeeshop_wrapper_proxies_core_methods(tmp_path: Path) -> None:
    impl = MagicMock()
    impl.generate_data.return_value = [tmp_path / "order_lines.tbl"]
    impl.get_queries.return_value = {"SA1": "SELECT 1"}
    impl.get_query.return_value = "SELECT 1"
    impl.get_schema.return_value = {"order_lines": {}}
    impl.get_create_tables_sql.return_value = "CREATE TABLE order_lines (id INT)"

    with patch("benchbox.coffeeshop.CoffeeShopBenchmark", return_value=impl):
        benchmark = CoffeeShop(scale_factor=0.01, output_dir=tmp_path)

    assert benchmark.generate_data() == [tmp_path / "order_lines.tbl"]
    assert benchmark.get_queries(dialect="duckdb") == {"SA1": "SELECT 1"}
    assert benchmark.get_query("SA1") == "SELECT 1"
    assert benchmark.get_schema() == {"order_lines": {}}
    assert benchmark.get_create_tables_sql(dialect="duckdb") == "CREATE TABLE order_lines (id INT)"


@pytest.mark.parametrize(
    ("benchmark_path", "wrapper_cls", "init_kwargs"),
    [
        ("benchbox.amplab.AMPLabBenchmark", AMPLab, {}),
        ("benchbox.h2odb.H2OBenchmark", H2ODB, {}),
        ("benchbox.ssb.SSBBenchmark", SSB, {"compress_data": False, "compression_type": "none"}),
    ],
)
def test_small_wrapper_facades_proxy_core_methods(
    benchmark_path: str,
    wrapper_cls: type[AMPLab | H2ODB | SSB],
    init_kwargs: dict[str, object],
    tmp_path: Path,
) -> None:
    impl = MagicMock()
    impl.generate_data.return_value = [tmp_path / "data.tbl"]
    impl.get_queries.return_value = {"Q1": "SELECT 1"}
    impl.get_query.return_value = "SELECT 1"
    impl.get_schema.return_value = [{"table": "sample"}]
    impl.get_create_tables_sql.return_value = "CREATE TABLE sample (id INT)"

    with patch(benchmark_path, return_value=impl):
        benchmark = wrapper_cls(scale_factor=1.0, output_dir=tmp_path, **init_kwargs)

    assert benchmark.generate_data() == [tmp_path / "data.tbl"]
    assert benchmark.get_queries(dialect="duckdb") == {"Q1": "SELECT 1"}
    assert benchmark.get_query("Q1") == "SELECT 1"
    assert benchmark.get_schema() == [{"table": "sample"}]
    assert benchmark.get_create_tables_sql(dialect="duckdb") == "CREATE TABLE sample (id INT)"


def test_clickbench_wrapper_handles_dict_outputs_and_category_facade(tmp_path: Path) -> None:
    impl = MagicMock()
    impl.generate_data.return_value = {"hits": tmp_path / "hits.csv"}
    impl.get_queries.return_value = {"Q1": "SELECT 1"}
    impl.get_query.return_value = "SELECT 1"
    impl.get_schema.return_value = {"hits": {"name": "hits"}}
    impl.get_create_tables_sql.return_value = "CREATE TABLE hits (id INT)"
    impl.get_query_categories.return_value = {"filters": ["Q1"]}

    with patch("benchbox.clickbench.ClickBenchBenchmark", return_value=impl):
        benchmark = ClickBench(scale_factor=1.0, output_dir=tmp_path)

    assert benchmark.generate_data() == [tmp_path / "hits.csv"]
    assert benchmark.get_queries(dialect="duckdb") == {"Q1": "SELECT 1"}
    assert benchmark.get_query("Q1") == "SELECT 1"
    assert benchmark.get_schema() == [{"name": "hits"}]
    assert benchmark.get_create_tables_sql(dialect="duckdb") == "CREATE TABLE hits (id INT)"
    assert benchmark.get_query_categories() == {"filters": ["Q1"]}

    with patch("benchbox.base.BaseBenchmark.translate_query", return_value="SELECT translated") as translate:
        assert benchmark.translate_query("Q1", "duckdb") == "SELECT translated"
    translate.assert_called_once_with("Q1", "duckdb")


def test_tpcds_wrapper_proxies_helper_methods_and_validates_inputs(tmp_path: Path) -> None:
    impl = MagicMock()
    impl.generate_data.return_value = [tmp_path / "store_sales.dat"]
    impl.get_queries.return_value = {"1": "SELECT 1"}
    impl.get_query.return_value = "SELECT 1"
    impl.query_manager = object()
    impl.data_generator = object()
    impl.get_available_tables.return_value = ["store_sales"]
    impl.get_available_queries.return_value = [1, 2]
    impl.generate_table_data.return_value = "store_sales.dat"
    impl.get_schema.return_value = {"store_sales": {}}
    impl.get_create_tables_sql.return_value = "CREATE TABLE store_sales (id INT)"
    impl.generate_streams.return_value = [tmp_path / "stream_1.sql"]
    impl.get_stream_info.return_value = {"stream_id": 1}
    impl.get_all_streams_info.return_value = [{"stream_id": 1}]
    impl.get_benchmark_info.return_value = {"benchmark": "tpcds"}

    with patch("benchbox.tpcds.TPCDSBenchmark", return_value=impl):
        benchmark = TPCDS(scale_factor=1.0, output_dir=tmp_path)

    assert benchmark.generate_data() == [tmp_path / "store_sales.dat"]
    assert benchmark.get_queries(dialect="duckdb", base_dialect="netezza") == {"1": "SELECT 1"}
    assert benchmark.get_query(1, params={"k": "v"}, seed=7, scale_factor=1.0, dialect="duckdb") == "SELECT 1"
    assert benchmark.queries is impl.query_manager
    assert benchmark.generator is impl.data_generator
    assert benchmark.get_available_tables() == ["store_sales"]
    assert benchmark.get_available_queries() == [1, 2]
    assert benchmark.generate_table_data("store_sales", output_dir=str(tmp_path)) == "store_sales.dat"
    assert benchmark.get_schema() == {"store_sales": {}}
    assert benchmark.get_create_tables_sql(dialect="duckdb") == "CREATE TABLE store_sales (id INT)"
    assert benchmark.generate_streams(num_streams=2, rng_seed=9, streams_output_dir=tmp_path) == [
        tmp_path / "stream_1.sql"
    ]
    assert benchmark.get_stream_info(1) == {"stream_id": 1}
    assert benchmark.get_all_streams_info() == [{"stream_id": 1}]
    assert benchmark.get_benchmark_info() == {"benchmark": "tpcds"}

    with pytest.raises(TypeError, match="query_id must be an integer"):
        benchmark.get_query("1")

    with pytest.raises(ValueError, match="Query ID must be 1-99"):
        benchmark.get_query(100)

    with pytest.raises(TypeError, match="scale_factor must be a number"):
        benchmark.get_query(1, scale_factor="bad")

    with pytest.raises(ValueError, match="scale_factor must be positive"):
        benchmark.get_query(1, scale_factor=0)

    with pytest.raises(TypeError, match="seed must be an integer"):
        benchmark.get_query(1, seed="bad")


def test_tpcds_obt_wrapper_proxies_public_methods(tmp_path: Path) -> None:
    impl = MagicMock()
    impl.generate_data.return_value = {"store_sales_obt": tmp_path / "store_sales_obt.parquet"}
    impl.get_queries.return_value = {"Q1": "SELECT 1"}
    impl.get_query.return_value = "SELECT 1"
    impl.get_schema.return_value = {"store_sales_obt": {}}
    impl.get_create_tables_sql.return_value = "CREATE TABLE store_sales_obt (id INT)"

    with patch("benchbox.tpcds_obt.TPCDSOBTBenchmark", return_value=impl):
        benchmark = TPCDSOBT(scale_factor=1.0, output_dir=tmp_path)

    assert benchmark.generate_data() == {"store_sales_obt": tmp_path / "store_sales_obt.parquet"}
    assert benchmark.get_queries(dialect="duckdb", base_dialect="spark") == {"Q1": "SELECT 1"}
    impl.get_queries.assert_called_once_with(dialect="duckdb")
    assert benchmark.get_query("Q1", ignored=True) == "SELECT 1"
    impl.get_query.assert_called_once_with("Q1", ignored=True)
    assert benchmark.get_schema() == {"store_sales_obt": {}}
    assert benchmark.get_create_tables_sql() == "CREATE TABLE store_sales_obt (id INT)"


def test_datavault_wrapper_proxies_metadata_and_validates_query_ids(tmp_path: Path) -> None:
    impl = MagicMock()
    impl.generate_data.return_value = {"hub_customer": tmp_path / "hub_customer.parquet"}
    impl.get_all_queries.return_value = {"1": "SELECT 1"}
    impl.get_query.return_value = "SELECT 1"
    impl.get_schema.return_value = {"hub_customer": {}}
    impl.get_create_tables_sql.return_value = "CREATE TABLE hub_customer (id INT)"
    impl.get_table_loading_order.return_value = ["hub_customer"]
    impl.tables = {"hub_customer": tmp_path / "hub_customer.parquet"}
    impl.get_table_count.return_value = 21

    with patch("benchbox.datavault.DataVaultBenchmark", return_value=impl):
        benchmark = DataVault(scale_factor=1.0, output_dir=tmp_path)

    assert benchmark.generate_data() == {"hub_customer": tmp_path / "hub_customer.parquet"}
    assert benchmark.get_queries() == {"1": "SELECT 1"}
    assert benchmark.get_query(1) == "SELECT 1"
    assert benchmark.get_schema() == {"hub_customer": {}}
    assert benchmark.get_create_tables_sql(dialect="duckdb") == "CREATE TABLE hub_customer (id INT)"
    assert benchmark.get_table_loading_order(["hub_customer"]) == ["hub_customer"]
    assert benchmark.tables == {"hub_customer": tmp_path / "hub_customer.parquet"}
    assert benchmark.table_count == 21
    assert benchmark.query_count == 22

    with pytest.raises(TypeError, match="query_id must be an integer"):
        benchmark.get_query("1")

    with pytest.raises(ValueError, match="Query ID must be 1-22"):
        benchmark.get_query(23)


def test_transaction_primitives_wrapper_proxies_setup_and_execution(tmp_path: Path) -> None:
    impl = MagicMock()
    impl.output_dir = tmp_path / "tpch_sf1"
    impl.get_data_source_benchmark.return_value = "tpch"
    impl.tables = {"orders": tmp_path / "orders.tbl"}
    impl.generate_data.return_value = [tmp_path / "orders.tbl"]
    impl.get_operation.return_value = {"id": "txn_commit_small"}
    impl.get_all_operations.return_value = {"txn_commit_small": {"id": "txn_commit_small"}}
    impl.get_schema.return_value = {"txn_staging": {}}
    impl.get_create_tables_sql.return_value = "CREATE TABLE txn_staging (id INT)"
    impl.get_benchmark_info.return_value = {"benchmark": "transaction_primitives"}
    impl.setup.return_value = {"status": "setup"}
    impl.load_data.return_value = {"status": "loaded"}
    impl.is_setup.return_value = True
    impl.execute_operation.return_value = {"status": "executed"}
    impl.run_benchmark.return_value = [{"status": "ok"}]

    with patch("benchbox.transaction_primitives.TransactionPrimitivesBenchmark", return_value=impl):
        benchmark = TransactionPrimitives(scale_factor=1.0, output_dir=tmp_path)

    assert benchmark.output_dir == impl.output_dir
    assert benchmark.get_data_source_benchmark() == "tpch"
    assert benchmark.tables == {"orders": tmp_path / "orders.tbl"}
    assert benchmark.generate_data(["orders"]) == [tmp_path / "orders.tbl"]
    assert benchmark.get_operation("txn_commit_small") == {"id": "txn_commit_small"}
    assert benchmark.get_all_operations() == {"txn_commit_small": {"id": "txn_commit_small"}}
    assert benchmark.get_schema(dialect="duckdb") == {"txn_staging": {}}
    assert benchmark.get_create_tables_sql(dialect="duckdb") == "CREATE TABLE txn_staging (id INT)"
    assert benchmark.get_benchmark_info() == {"benchmark": "transaction_primitives"}
    assert benchmark.setup("conn", force=True) == {"status": "setup"}
    assert benchmark.load_data("conn", force=True) == {"status": "loaded"}
    benchmark.teardown("conn")
    benchmark.reset("conn")
    assert benchmark.is_setup("conn") is True
    assert benchmark.execute_operation("txn_commit_small", "conn") == {"status": "executed"}
    assert benchmark.run_benchmark("conn", ["txn_commit_small"], ["commit"]) == [{"status": "ok"}]


