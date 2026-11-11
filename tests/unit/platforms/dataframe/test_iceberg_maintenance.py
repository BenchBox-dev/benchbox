# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import sys

import pytest

try:
    import pyiceberg

    ICEBERG_AVAILABLE = True
except ImportError:
    ICEBERG_AVAILABLE = False

try:
    import pyarrow as pa

    PYARROW_AVAILABLE = True
except ImportError:
    pa = None
    PYARROW_AVAILABLE = False

IS_WINDOWS = sys.platform == "win32"


pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.skipif(
        not ICEBERG_AVAILABLE or not PYARROW_AVAILABLE,
        reason="PyIceberg or PyArrow not installed",
    ),
]


class TestIcebergMaintenanceAvailability:
    def test_get_maintenance_operations_returns_iceberg(self):

        from benchbox.core.dataframe.maintenance_interface import (
            get_maintenance_operations_for_platform,
        )

        result = get_maintenance_operations_for_platform("iceberg")
        assert result is not None

        from benchbox.platforms.dataframe.iceberg_maintenance import (
            IcebergMaintenanceOperations,
        )

        assert isinstance(result, IcebergMaintenanceOperations)

    def test_capabilities(self):

        from benchbox.platforms.dataframe.iceberg_maintenance import (
            IcebergMaintenanceOperations,
        )

        ops = IcebergMaintenanceOperations()
        caps = ops.get_capabilities()

        assert caps.platform_name == "iceberg"
        assert caps.supports_insert is True
        assert caps.supports_delete is True
        assert caps.supports_update is True
        assert caps.supports_merge is True
        assert caps.supports_transactions is True
        assert caps.supports_time_travel is True

    def test_tpc_compliance(self):

        from benchbox.platforms.dataframe.iceberg_maintenance import (
            IcebergMaintenanceOperations,
        )

        ops = IcebergMaintenanceOperations()
        caps = ops.get_capabilities()

        is_compliant, missing = caps.validate_tpc_compliance()
        assert is_compliant is True
        assert len(missing) == 0


@pytest.mark.skipif(IS_WINDOWS, reason="PyIceberg path handling incompatible with Windows")
class TestIcebergInsert:
    def test_insert_new_rows(self, tmp_path):

        from benchbox.platforms.dataframe.iceberg_maintenance import (
            IcebergMaintenanceOperations,
        )

        ops = IcebergMaintenanceOperations(working_dir=tmp_path)
        table_path = tmp_path / "test_table"

        data = pa.table(
            {
                "id": pa.array([1, 2, 3], type=pa.int64()),
                "name": ["Alice", "Bob", "Charlie"],
                "value": pa.array([100, 200, 300], type=pa.int64()),
            }
        )

        result = ops.insert_rows(table_path=table_path, dataframe=data, mode="append")

        assert result.success is True
        assert result.rows_affected == 3
        assert result.operation_type.value == "insert"

    def test_insert_empty_dataframe(self, tmp_path):

        from benchbox.platforms.dataframe.iceberg_maintenance import (
            IcebergMaintenanceOperations,
        )

        ops = IcebergMaintenanceOperations(working_dir=tmp_path)
        table_path = tmp_path / "test_table"

        empty_data = pa.table({"id": pa.array([], type=pa.int64()), "name": pa.array([], type=pa.string())})

        result = ops.insert_rows(table_path=table_path, dataframe=empty_data, mode="append")

        assert result.success is True
        assert result.rows_affected == 0


@pytest.mark.skipif(IS_WINDOWS, reason="PyIceberg path handling incompatible with Windows")
class TestIcebergDelete:
    def test_delete_nonexistent_table(self, tmp_path):
        from benchbox.platforms.dataframe.iceberg_maintenance import (
            IcebergMaintenanceOperations,
        )

        ops = IcebergMaintenanceOperations(working_dir=tmp_path)
        table_path = tmp_path / "nonexistent_table"

        result = ops.delete_rows(table_path=table_path, condition="id > 0")

        assert result.success is True
        assert result.rows_affected == 0


@pytest.mark.skipif(IS_WINDOWS, reason="PyIceberg path handling incompatible with Windows")
class TestIcebergMaintenanceResult:
    def test_result_timing(self, tmp_path):

        from benchbox.platforms.dataframe.iceberg_maintenance import (
            IcebergMaintenanceOperations,
        )

        ops = IcebergMaintenanceOperations(working_dir=tmp_path)
        table_path = tmp_path / "test_table"

        data = pa.table(
            {
                "id": pa.array([1, 2, 3], type=pa.int64()),
                "name": ["A", "B", "C"],
            }
        )
        result = ops.insert_rows(table_path=table_path, dataframe=data, mode="append")

        assert result.success is True
        assert result.duration is not None
        assert result.duration >= 0


@pytest.mark.skipif(IS_WINDOWS, reason="PyIceberg path handling incompatible with Windows")
class TestIcebergDataFrameConversion:
    def test_convert_polars_dataframe(self, tmp_path):

        pytest.importorskip("polars")
        import polars as pl

        from benchbox.platforms.dataframe.iceberg_maintenance import (
            IcebergMaintenanceOperations,
        )

        ops = IcebergMaintenanceOperations(working_dir=tmp_path)
        table_path = tmp_path / "test_table"

        df = pl.DataFrame(
            {
                "id": pl.Series([1, 2, 3], dtype=pl.Int64),
                "name": ["Alice", "Bob", "Charlie"],
            }
        )

        result = ops.insert_rows(table_path=table_path, dataframe=df, mode="append")

        assert result.success is True
        assert result.rows_affected == 3

    def test_convert_pandas_dataframe(self, tmp_path):

        pytest.importorskip("pandas")
        import pandas as pd

        from benchbox.platforms.dataframe.iceberg_maintenance import (
            IcebergMaintenanceOperations,
        )

        ops = IcebergMaintenanceOperations(working_dir=tmp_path)
        table_path = tmp_path / "test_table"

        df = pd.DataFrame({"id": pd.array([1, 2, 3], dtype="int64"), "name": ["Alice", "Bob", "Charlie"]})

        result = ops.insert_rows(table_path=table_path, dataframe=df, mode="append")

        assert result.success is True
        assert result.rows_affected == 3


@pytest.mark.skipif(IS_WINDOWS, reason="PyIceberg path handling incompatible with Windows")
class TestIcebergDictCondition:
    def test_dict_to_expression_builds_conjunction(self):
        from pyiceberg.expressions import And, EqualTo

        from benchbox.platforms.dataframe.iceberg_maintenance import (
            IcebergMaintenanceOperations,
        )

        ops = IcebergMaintenanceOperations()
        predicate = ops._dict_to_expression({"IsCurrent": True, "CustomerID": 101})

        assert isinstance(predicate, And)
        assert isinstance(predicate.left, EqualTo)
        assert isinstance(predicate.right, EqualTo)
        assert predicate.left.term.name == "IsCurrent"
        assert predicate.right.term.name == "CustomerID"

    def test_dict_to_expression_maps_none_to_is_null(self):
        from pyiceberg.expressions import IsNull

        from benchbox.platforms.dataframe.iceberg_maintenance import (
            IcebergMaintenanceOperations,
        )

        ops = IcebergMaintenanceOperations()
        assert isinstance(ops._dict_to_expression({"EndDate": None}), IsNull)

    def test_dict_to_expression_rejects_empty_and_unsupported(self):
        from benchbox.platforms.dataframe.iceberg_maintenance import (
            IcebergMaintenanceOperations,
        )

        ops = IcebergMaintenanceOperations()
        with pytest.raises(ValueError, match="cannot be empty"):
            ops._dict_to_expression({})
        with pytest.raises(TypeError, match="Unsupported Iceberg condition value"):
            ops._dict_to_expression({"tags": ["a"]})

    def test_update_with_dict_condition_expires_one_row(self, tmp_path):
        import pandas as pd

        from benchbox.platforms.dataframe.iceberg_maintenance import (
            IcebergMaintenanceOperations,
        )

        ops = IcebergMaintenanceOperations(working_dir=tmp_path)
        frame = pd.DataFrame(
            {
                "SK_CustomerID": [1, 2],
                "CustomerID": [101, 102],
                "IsCurrent": [True, True],
            }
        )
        insert = ops.insert_rows(table_path="DimCustomer", dataframe=frame, mode="append")
        assert insert.success is True

        update = ops.update_rows(
            table_path="DimCustomer",
            condition={"IsCurrent": True, "CustomerID": 101},
            updates={"IsCurrent": False},
        )
        assert update.success is True
        assert update.rows_affected == 1

        table = ops.catalog.load_table("default.DimCustomer")
        out = table.scan().to_arrow().to_pandas().sort_values("SK_CustomerID").reset_index(drop=True)
        assert out["IsCurrent"].tolist() == [False, True]
        assert str(out["IsCurrent"].dtype) == "bool"


