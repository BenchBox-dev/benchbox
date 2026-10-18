# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


try:
    import polars as pl
    import pyarrow as pa

    POLARS_AVAILABLE = True
except ImportError:
    POLARS_AVAILABLE = False
    pl = None
    pa = None

try:
    import deltalake

    DELTA_LAKE_AVAILABLE = True
except ImportError:
    DELTA_LAKE_AVAILABLE = False
    deltalake = None

try:
    import pyiceberg

    ICEBERG_AVAILABLE = True
except ImportError:
    ICEBERG_AVAILABLE = False
    pyiceberg = None


@pytest.fixture
def sample_orders_data():
    if not POLARS_AVAILABLE:
        pytest.skip("Polars not installed")

    return pl.DataFrame(
        {
            "o_orderkey": [1, 2, 3, 4, 5],
            "o_custkey": [100, 100, 200, 200, 300],
            "o_orderstatus": ["F", "O", "F", "O", "P"],
            "o_totalprice": [1000.50, 2000.75, 1500.25, 2500.00, 3000.00],
            "o_orderdate": ["1995-01-01", "1995-02-15", "1995-03-20", "1995-04-10", "1995-05-01"],
            "o_orderpriority": ["1-URGENT", "2-HIGH", "3-MEDIUM", "4-NOT SPECIFIED", "5-LOW"],
        }
    )


@pytest.fixture
def sample_lineitem_data():
    if not POLARS_AVAILABLE:
        pytest.skip("Polars not installed")

    return pl.DataFrame(
        {
            "l_orderkey": [1, 1, 2, 3, 4, 5],
            "l_partkey": [101, 102, 103, 104, 105, 106],
            "l_suppkey": [1001, 1002, 1003, 1004, 1005, 1006],
            "l_quantity": [10.0, 20.0, 15.0, 25.0, 30.0, 5.0],
            "l_extendedprice": [1000.0, 2000.0, 1500.0, 2500.0, 3000.0, 500.0],
            "l_discount": [0.05, 0.10, 0.0, 0.05, 0.15, 0.0],
        }
    )


@pytest.mark.integration
@pytest.mark.skipif(not POLARS_AVAILABLE, reason="Polars not installed")
class TestPolarsMaintenanceIntegration:
    def test_insert_and_query_workflow(self, tmp_path, sample_orders_data):

        from benchbox.platforms.dataframe.polars_maintenance import PolarsMaintenanceOperations

        ops = PolarsMaintenanceOperations(working_dir=tmp_path)
        table_path = tmp_path / "orders"

        result = ops.insert_rows(table_path=table_path, dataframe=sample_orders_data, mode="append")
        assert result.success is True
        assert result.rows_affected == 5

        read_df = pl.read_parquet(table_path / "*.parquet")
        assert len(read_df) == 5

    def test_insert_append_preserves_existing_data(self, tmp_path, sample_orders_data):

        from benchbox.platforms.dataframe.polars_maintenance import PolarsMaintenanceOperations

        ops = PolarsMaintenanceOperations(working_dir=tmp_path)
        table_path = tmp_path / "orders"

        ops.insert_rows(table_path=table_path, dataframe=sample_orders_data, mode="append")

        new_orders = pl.DataFrame(
            {
                "o_orderkey": [6, 7],
                "o_custkey": [400, 400],
                "o_orderstatus": ["F", "O"],
                "o_totalprice": [4000.0, 5000.0],
                "o_orderdate": ["1995-06-01", "1995-07-01"],
                "o_orderpriority": ["1-URGENT", "2-HIGH"],
            }
        )
        result = ops.insert_rows(table_path=table_path, dataframe=new_orders, mode="append")

        assert result.success is True
        assert result.rows_affected == 2

        read_df = pl.read_parquet(table_path / "*.parquet")
        assert len(read_df) == 7

    def test_delete_reduces_row_count(self, tmp_path, sample_orders_data):

        from benchbox.platforms.dataframe.polars_maintenance import PolarsMaintenanceOperations

        ops = PolarsMaintenanceOperations(working_dir=tmp_path)
        table_path = tmp_path / "orders"

        ops.insert_rows(table_path=table_path, dataframe=sample_orders_data, mode="append")

        result = ops.delete_rows(table_path=table_path, condition="o_totalprice > 2500")

        assert result.success is True
        assert result.rows_affected == 1

        read_df = pl.read_parquet(table_path / "*.parquet")
        assert len(read_df) == 4

    def test_tpc_h_rf1_simulation(self, tmp_path, sample_lineitem_data):
        from benchbox.platforms.dataframe.polars_maintenance import PolarsMaintenanceOperations

        ops = PolarsMaintenanceOperations(working_dir=tmp_path)
        table_path = tmp_path / "lineitem"

        ops.insert_rows(table_path=table_path, dataframe=sample_lineitem_data, mode="append")

        new_lineitems = pl.DataFrame(
            {
                "l_orderkey": [6, 6, 7],
                "l_partkey": [107, 108, 109],
                "l_suppkey": [1007, 1008, 1009],
                "l_quantity": [12.0, 8.0, 15.0],
                "l_extendedprice": [1200.0, 800.0, 1500.0],
                "l_discount": [0.02, 0.08, 0.0],
            }
        )

        result = ops.insert_rows(table_path=table_path, dataframe=new_lineitems, mode="append")

        assert result.success is True
        assert result.rows_affected == 3

        assert result.duration >= 0
        assert result.start_time > 0
        assert result.end_time >= result.start_time

    def test_tpc_h_rf2_simulation(self, tmp_path, sample_orders_data):
        from benchbox.platforms.dataframe.polars_maintenance import PolarsMaintenanceOperations

        ops = PolarsMaintenanceOperations(working_dir=tmp_path)
        table_path = tmp_path / "orders"

        ops.insert_rows(table_path=table_path, dataframe=sample_orders_data, mode="append")

        result = ops.delete_rows(table_path=table_path, condition="o_orderkey IN (1, 2)")

        assert result.success is True
        assert result.rows_affected == 2

        read_df = pl.read_parquet(table_path / "*.parquet")
        remaining_keys = read_df["o_orderkey"].to_list()
        assert 1 not in remaining_keys
        assert 2 not in remaining_keys
        assert 3 in remaining_keys

    def test_tpc_ds_dm3_simulation(self, tmp_path, sample_orders_data):
        from benchbox.platforms.dataframe.polars_maintenance import PolarsMaintenanceOperations

        ops = PolarsMaintenanceOperations(working_dir=tmp_path)
        table_path = tmp_path / "orders"

        ops.insert_rows(table_path=table_path, dataframe=sample_orders_data, mode="append")

        result = ops.update_rows(
            table_path=table_path,
            condition="o_orderstatus = 'P'",
            updates={"o_orderstatus": "'F'"},
        )

        assert result.success is True
        assert result.rows_affected == 1

        read_df = pl.read_parquet(table_path / "*.parquet")
        pending_count = len(read_df.filter(pl.col("o_orderstatus") == "P"))
        assert pending_count == 0

    def test_merge_upsert_workflow(self, tmp_path, sample_orders_data):

        from benchbox.platforms.dataframe.polars_maintenance import PolarsMaintenanceOperations

        ops = PolarsMaintenanceOperations(working_dir=tmp_path)
        table_path = tmp_path / "orders"

        ops.insert_rows(table_path=table_path, dataframe=sample_orders_data, mode="append")

        source_df = pl.DataFrame(
            {
                "o_orderkey": [1, 10],
                "o_custkey": [100, 999],
                "o_orderstatus": ["X", "N"],
                "o_totalprice": [9999.0, 1000.0],
                "o_orderdate": ["1995-01-01", "1996-01-01"],
                "o_orderpriority": ["1-URGENT", "5-LOW"],
            }
        )

        result = ops.merge_rows(
            table_path=table_path,
            source_dataframe=source_df,
            merge_condition="o_orderkey",
            when_matched={"o_orderstatus": "source.o_orderstatus"},
            when_not_matched={"o_orderkey": "source.o_orderkey", "o_orderstatus": "source.o_orderstatus"},
        )

        assert result.success is True
        assert result.rows_affected == 2

        read_df = pl.read_parquet(table_path / "*.parquet")
        assert len(read_df) == 6

        order1 = read_df.filter(pl.col("o_orderkey") == 1)
        assert order1["o_orderstatus"][0] == "X"

        order10 = read_df.filter(pl.col("o_orderkey") == 10)
        assert len(order10) == 1


@pytest.mark.integration
@pytest.mark.skipif(not DELTA_LAKE_AVAILABLE or not POLARS_AVAILABLE, reason="Delta Lake or Polars not installed")
class TestDeltaLakeMaintenanceIntegration:
    def test_insert_and_query_workflow(self, tmp_path, sample_orders_data):

        from deltalake import DeltaTable

        from benchbox.platforms.dataframe.delta_lake_maintenance import DeltaLakeMaintenanceOperations

        ops = DeltaLakeMaintenanceOperations()
        table_path = tmp_path / "delta_orders"

        result = ops.insert_rows(table_path=table_path, dataframe=sample_orders_data, mode="append")
        assert result.success is True
        assert result.rows_affected == 5

        dt = DeltaTable(str(table_path))
        read_df = dt.to_pandas()
        assert len(read_df) == 5

    def test_delete_with_acid_guarantees(self, tmp_path, sample_orders_data):

        from deltalake import DeltaTable

        from benchbox.platforms.dataframe.delta_lake_maintenance import DeltaLakeMaintenanceOperations

        ops = DeltaLakeMaintenanceOperations()
        table_path = tmp_path / "delta_orders"

        ops.insert_rows(table_path=table_path, dataframe=sample_orders_data, mode="append")

        result = ops.delete_rows(table_path=table_path, condition="o_custkey = 200")

        assert result.success is True
        assert result.rows_affected == 2

        dt = DeltaTable(str(table_path))
        read_df = dt.to_pandas()
        assert len(read_df) == 3
        assert 200 not in read_df["o_custkey"].values

    def test_update_rows_acid(self, tmp_path, sample_orders_data):

        from deltalake import DeltaTable

        from benchbox.platforms.dataframe.delta_lake_maintenance import DeltaLakeMaintenanceOperations

        ops = DeltaLakeMaintenanceOperations()
        table_path = tmp_path / "delta_orders"

        ops.insert_rows(table_path=table_path, dataframe=sample_orders_data, mode="append")

        result = ops.update_rows(
            table_path=table_path,
            condition="o_orderstatus = 'P'",
            updates={"o_orderstatus": "'F'"},
        )

        assert result.success is True

        dt = DeltaTable(str(table_path))
        read_df = dt.to_pandas()
        pending_count = len(read_df[read_df["o_orderstatus"] == "P"])
        assert pending_count == 0

    def test_merge_upsert_operation(self, tmp_path, sample_orders_data):

        from deltalake import DeltaTable

        from benchbox.platforms.dataframe.delta_lake_maintenance import DeltaLakeMaintenanceOperations

        ops = DeltaLakeMaintenanceOperations()
        table_path = tmp_path / "delta_orders"

        ops.insert_rows(table_path=table_path, dataframe=sample_orders_data, mode="append")

        source_data = pa.table(
            {
                "o_orderkey": [1, 6],
                "o_custkey": [100, 500],
                "o_orderstatus": ["F", "O"],
                "o_totalprice": [1500.0, 6000.0],
                "o_orderdate": ["1995-01-01", "1995-08-01"],
                "o_orderpriority": ["1-URGENT", "1-URGENT"],
            }
        )

        result = ops.merge_rows(
            table_path=table_path,
            source_dataframe=source_data,
            merge_condition="target.o_orderkey = source.o_orderkey",
            when_matched={"o_totalprice": "source.o_totalprice"},
            when_not_matched={
                "o_orderkey": "source.o_orderkey",
                "o_custkey": "source.o_custkey",
                "o_orderstatus": "source.o_orderstatus",
                "o_totalprice": "source.o_totalprice",
                "o_orderdate": "source.o_orderdate",
                "o_orderpriority": "source.o_orderpriority",
            },
        )

        assert result.success is True

        dt = DeltaTable(str(table_path))
        read_df = dt.to_pandas()
        assert len(read_df) == 6

        order1 = read_df[read_df["o_orderkey"] == 1]
        assert order1["o_totalprice"].iloc[0] == 1500.0

        order6 = read_df[read_df["o_orderkey"] == 6]
        assert len(order6) == 1

    def test_time_travel_capability(self, tmp_path, sample_orders_data):

        from deltalake import DeltaTable

        from benchbox.platforms.dataframe.delta_lake_maintenance import DeltaLakeMaintenanceOperations

        ops = DeltaLakeMaintenanceOperations()
        table_path = tmp_path / "delta_orders"

        ops.insert_rows(table_path=table_path, dataframe=sample_orders_data, mode="append")

        ops.delete_rows(table_path=table_path, condition="o_custkey = 300")

        dt = DeltaTable(str(table_path))
        current_count = dt.to_pyarrow_table().num_rows
        assert current_count == 4

        dt_v0 = DeltaTable(str(table_path), version=0)
        v0_count = dt_v0.to_pyarrow_table().num_rows
        assert v0_count == 5


@pytest.mark.integration
@pytest.mark.skipif(not ICEBERG_AVAILABLE or not POLARS_AVAILABLE, reason="Iceberg or Polars not installed")
@pytest.mark.skipif(__import__("sys").platform == "win32", reason="pyiceberg does not support Windows paths")
class TestIcebergMaintenanceIntegration:
    def test_insert_and_query_workflow(self, tmp_path, sample_orders_data):

        from benchbox.platforms.dataframe.iceberg_maintenance import IcebergMaintenanceOperations

        ops = IcebergMaintenanceOperations(working_dir=tmp_path)

        result = ops.insert_rows(table_path="default.orders", dataframe=sample_orders_data, mode="append")
        assert result.success is True
        assert result.rows_affected == 5

        table = ops.catalog.load_table("default.orders")
        read_table = table.scan().to_arrow()
        assert read_table.num_rows == 5

    def test_insert_append_mode(self, tmp_path, sample_orders_data):

        from benchbox.platforms.dataframe.iceberg_maintenance import IcebergMaintenanceOperations

        ops = IcebergMaintenanceOperations(working_dir=tmp_path)

        ops.insert_rows(table_path="default.orders", dataframe=sample_orders_data, mode="append")

        new_orders = pl.DataFrame(
            {
                "o_orderkey": [6, 7],
                "o_custkey": [400, 400],
                "o_orderstatus": ["F", "O"],
                "o_totalprice": [4000.0, 5000.0],
                "o_orderdate": ["1995-06-01", "1995-07-01"],
                "o_orderpriority": ["1-URGENT", "2-HIGH"],
            }
        )
        result = ops.insert_rows(table_path="default.orders", dataframe=new_orders, mode="append")

        assert result.success is True
        assert result.rows_affected == 2

        table = ops.catalog.load_table("default.orders")
        read_table = table.scan().to_arrow()
        assert read_table.num_rows == 7


@pytest.mark.integration
class TestMaintenanceCapabilitiesConsistency:
    @pytest.mark.skipif(not POLARS_AVAILABLE, reason="Polars not installed")
    def test_polars_capabilities_match_spec(self):

        from benchbox.platforms.dataframe.polars_maintenance import PolarsMaintenanceOperations

        ops = PolarsMaintenanceOperations()
        caps = ops.get_capabilities()

        assert caps.supports_insert is True
        assert caps.supports_delete is True
        assert caps.supports_update is True
        assert caps.supports_merge is True
        assert caps.supports_partitioned_delete is True

        assert caps.supports_transactions is False
        assert caps.supports_time_travel is False

    @pytest.mark.skipif(not DELTA_LAKE_AVAILABLE, reason="Delta Lake not installed")
    def test_delta_lake_capabilities_match_spec(self):

        from benchbox.platforms.dataframe.delta_lake_maintenance import DeltaLakeMaintenanceOperations

        ops = DeltaLakeMaintenanceOperations()
        caps = ops.get_capabilities()

        assert caps.supports_insert is True
        assert caps.supports_delete is True
        assert caps.supports_update is True
        assert caps.supports_merge is True
        assert caps.supports_transactions is True
        assert caps.supports_time_travel is True

    @pytest.mark.skipif(not ICEBERG_AVAILABLE, reason="Iceberg not installed")
    def test_iceberg_capabilities_match_spec(self):

        from benchbox.platforms.dataframe.iceberg_maintenance import IcebergMaintenanceOperations

        ops = IcebergMaintenanceOperations()
        caps = ops.get_capabilities()

        assert caps.supports_insert is True
        assert caps.supports_delete is True
        assert caps.supports_update is True
        assert caps.supports_merge is True
        assert caps.supports_transactions is True
        assert caps.supports_time_travel is True


@pytest.mark.integration
class TestTPCComplianceValidation:
    @pytest.mark.skipif(not DELTA_LAKE_AVAILABLE, reason="Delta Lake not installed")
    def test_delta_lake_tpc_h_compliance(self):

        from benchbox.platforms.dataframe.delta_lake_maintenance import DeltaLakeMaintenanceOperations

        ops = DeltaLakeMaintenanceOperations()
        caps = ops.get_capabilities()

        is_compliant, missing = caps.validate_tpc_compliance()

        assert is_compliant is True, f"Delta Lake should be TPC compliant. Missing: {missing}"
        assert len(missing) == 0

    @pytest.mark.skipif(not ICEBERG_AVAILABLE, reason="Iceberg not installed")
    def test_iceberg_tpc_h_compliance(self):

        from benchbox.platforms.dataframe.iceberg_maintenance import IcebergMaintenanceOperations

        ops = IcebergMaintenanceOperations()
        caps = ops.get_capabilities()

        is_compliant, missing = caps.validate_tpc_compliance()

        assert is_compliant is True, f"Iceberg should be TPC compliant. Missing: {missing}"
        assert len(missing) == 0

    @pytest.mark.skipif(not POLARS_AVAILABLE, reason="Polars not installed")
    def test_polars_tpc_compliance(self):

        from benchbox.platforms.dataframe.polars_maintenance import PolarsMaintenanceOperations

        ops = PolarsMaintenanceOperations()
        caps = ops.get_capabilities()

        is_compliant, missing = caps.validate_tpc_compliance()

        assert is_compliant is True, f"Polars should be TPC compliant. Missing: {missing}"
        assert len(missing) == 0


@pytest.mark.integration
@pytest.mark.skipif(not POLARS_AVAILABLE, reason="Polars not installed")
class TestMaintenanceMetricsCapture:
    def test_timing_metrics_captured(self, tmp_path, sample_orders_data):

        from benchbox.platforms.dataframe.polars_maintenance import PolarsMaintenanceOperations

        ops = PolarsMaintenanceOperations(working_dir=tmp_path)
        table_path = tmp_path / "orders"

        result = ops.insert_rows(table_path=table_path, dataframe=sample_orders_data, mode="append")

        assert result.start_time > 0
        assert result.end_time >= result.start_time
        assert result.duration >= 0
        assert result.duration == result.end_time - result.start_time

    def test_row_count_metrics_accurate(self, tmp_path, sample_orders_data):

        from benchbox.platforms.dataframe.polars_maintenance import PolarsMaintenanceOperations

        ops = PolarsMaintenanceOperations(working_dir=tmp_path)
        table_path = tmp_path / "orders"

        insert_result = ops.insert_rows(table_path=table_path, dataframe=sample_orders_data, mode="append")
        assert insert_result.rows_affected == 5

        delete_result = ops.delete_rows(table_path=table_path, condition="o_custkey = 100")
        assert delete_result.rows_affected == 2
