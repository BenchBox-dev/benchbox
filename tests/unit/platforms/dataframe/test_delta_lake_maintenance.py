# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

try:
    import deltalake

    DELTA_AVAILABLE = True
except ImportError:
    DELTA_AVAILABLE = False

try:
    import pyarrow as pa

    PYARROW_AVAILABLE = True
except ImportError:
    pa = None
    PYARROW_AVAILABLE = False


pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.skipif(
        not DELTA_AVAILABLE or not PYARROW_AVAILABLE,
        reason="Delta Lake or PyArrow not installed",
    ),
]


class TestDeltaLakeMaintenanceAvailability:
    def test_get_maintenance_operations_returns_delta_lake(self):

        from benchbox.core.dataframe.maintenance_interface import (
            get_maintenance_operations_for_platform,
        )

        result = get_maintenance_operations_for_platform("delta-lake")
        assert result is not None

        from benchbox.platforms.dataframe.delta_lake_maintenance import (
            DeltaLakeMaintenanceOperations,
        )

        assert isinstance(result, DeltaLakeMaintenanceOperations)

    def test_capabilities(self):

        from benchbox.platforms.dataframe.delta_lake_maintenance import (
            DeltaLakeMaintenanceOperations,
        )

        ops = DeltaLakeMaintenanceOperations()
        caps = ops.get_capabilities()

        assert caps.platform_name == "delta-lake"
        assert caps.supports_insert is True
        assert caps.supports_delete is True
        assert caps.supports_update is True
        assert caps.supports_merge is True
        assert caps.supports_transactions is True
        assert caps.supports_time_travel is True

    def test_tpc_compliance(self):

        from benchbox.platforms.dataframe.delta_lake_maintenance import (
            DeltaLakeMaintenanceOperations,
        )

        ops = DeltaLakeMaintenanceOperations()
        caps = ops.get_capabilities()

        is_compliant, missing = caps.validate_tpc_compliance()
        assert is_compliant is True
        assert len(missing) == 0


class TestDeltaLakeInsert:
    def test_insert_new_rows(self, tmp_path):

        from benchbox.platforms.dataframe.delta_lake_maintenance import (
            DeltaLakeMaintenanceOperations,
        )

        ops = DeltaLakeMaintenanceOperations()
        table_path = tmp_path / "test_table"

        data = pa.table(
            {
                "id": [1, 2, 3],
                "name": ["Alice", "Bob", "Charlie"],
                "value": [100, 200, 300],
            }
        )

        result = ops.insert_rows(table_path=table_path, dataframe=data, mode="append")

        assert result.success is True
        assert result.rows_affected == 3
        assert result.operation_type.value == "insert"

    def test_insert_append_mode(self, tmp_path):

        from deltalake import DeltaTable, write_deltalake

        from benchbox.platforms.dataframe.delta_lake_maintenance import (
            DeltaLakeMaintenanceOperations,
        )

        ops = DeltaLakeMaintenanceOperations()
        table_path = tmp_path / "test_table"

        initial_data = pa.table({"id": [1, 2], "name": ["Alice", "Bob"]})
        write_deltalake(str(table_path), initial_data)

        new_data = pa.table({"id": [3, 4], "name": ["Charlie", "Diana"]})
        result = ops.insert_rows(table_path=table_path, dataframe=new_data, mode="append")

        assert result.success is True
        assert result.rows_affected == 2

        dt = DeltaTable(str(table_path))
        total_rows = dt.to_pyarrow_table().num_rows
        assert total_rows == 4

    def test_insert_overwrite_mode(self, tmp_path):

        from deltalake import DeltaTable, write_deltalake

        from benchbox.platforms.dataframe.delta_lake_maintenance import (
            DeltaLakeMaintenanceOperations,
        )

        ops = DeltaLakeMaintenanceOperations()
        table_path = tmp_path / "test_table"

        initial_data = pa.table({"id": [1, 2, 3], "name": ["Alice", "Bob", "Charlie"]})
        write_deltalake(str(table_path), initial_data)

        new_data = pa.table({"id": [10, 20], "name": ["Xavier", "Yolanda"]})
        result = ops.insert_rows(table_path=table_path, dataframe=new_data, mode="overwrite")

        assert result.success is True
        assert result.rows_affected == 2

        dt = DeltaTable(str(table_path))
        total_rows = dt.to_pyarrow_table().num_rows
        assert total_rows == 2

    def test_insert_empty_dataframe(self, tmp_path):

        from benchbox.platforms.dataframe.delta_lake_maintenance import (
            DeltaLakeMaintenanceOperations,
        )

        ops = DeltaLakeMaintenanceOperations()
        table_path = tmp_path / "test_table"

        empty_data = pa.table({"id": pa.array([], type=pa.int64()), "name": pa.array([], type=pa.string())})

        result = ops.insert_rows(table_path=table_path, dataframe=empty_data, mode="append")

        assert result.success is True
        assert result.rows_affected == 0


class TestDeltaLakeDelete:
    def test_delete_with_condition(self, tmp_path):

        from deltalake import DeltaTable, write_deltalake

        from benchbox.platforms.dataframe.delta_lake_maintenance import (
            DeltaLakeMaintenanceOperations,
        )

        ops = DeltaLakeMaintenanceOperations()
        table_path = tmp_path / "test_table"

        data = pa.table(
            {
                "id": [1, 2, 3, 4, 5],
                "name": ["Alice", "Bob", "Charlie", "Diana", "Eve"],
                "age": [25, 30, 35, 40, 45],
            }
        )
        write_deltalake(str(table_path), data)

        result = ops.delete_rows(table_path=table_path, condition="age > 35")

        assert result.success is True
        assert result.rows_affected == 2

        dt = DeltaTable(str(table_path))
        remaining = dt.to_pyarrow_table().num_rows
        assert remaining == 3

    def test_delete_no_matches(self, tmp_path):

        from deltalake import DeltaTable, write_deltalake

        from benchbox.platforms.dataframe.delta_lake_maintenance import (
            DeltaLakeMaintenanceOperations,
        )

        ops = DeltaLakeMaintenanceOperations()
        table_path = tmp_path / "test_table"

        data = pa.table({"id": [1, 2, 3], "value": [10, 20, 30]})
        write_deltalake(str(table_path), data)

        result = ops.delete_rows(table_path=table_path, condition="value > 100")

        assert result.success is True
        assert result.rows_affected == 0

        dt = DeltaTable(str(table_path))
        remaining = dt.to_pyarrow_table().num_rows
        assert remaining == 3

    def test_delete_nonexistent_table(self, tmp_path):
        from benchbox.platforms.dataframe.delta_lake_maintenance import (
            DeltaLakeMaintenanceOperations,
        )

        ops = DeltaLakeMaintenanceOperations()
        table_path = tmp_path / "nonexistent_table"

        result = ops.delete_rows(table_path=table_path, condition="id > 0")

        assert result.success is True
        assert result.rows_affected == 0


class TestDeltaLakeUpdate:
    def test_update_rows(self, tmp_path):

        from deltalake import DeltaTable, write_deltalake

        from benchbox.platforms.dataframe.delta_lake_maintenance import (
            DeltaLakeMaintenanceOperations,
        )

        ops = DeltaLakeMaintenanceOperations()
        table_path = tmp_path / "test_table"

        data = pa.table(
            {
                "id": [1, 2, 3],
                "status": ["active", "active", "inactive"],
                "value": [100, 200, 300],
            }
        )
        write_deltalake(str(table_path), data)

        result = ops.update_rows(
            table_path=table_path,
            condition="value > 150",
            updates={"status": "'updated'"},
        )

        assert result.success is True

        dt = DeltaTable(str(table_path))
        table = dt.to_pyarrow_table()
        statuses = table.column("status").to_pylist()
        assert statuses.count("updated") == 2


class TestDeltaLakeMerge:
    def test_merge_upsert(self, tmp_path):

        from deltalake import DeltaTable, write_deltalake

        from benchbox.platforms.dataframe.delta_lake_maintenance import (
            DeltaLakeMaintenanceOperations,
        )

        ops = DeltaLakeMaintenanceOperations()
        table_path = tmp_path / "test_table"

        data = pa.table({"id": [1, 2, 3], "name": ["Alice", "Bob", "Charlie"], "value": [100, 200, 300]})
        write_deltalake(str(table_path), data)

        source = pa.table({"id": [2, 4], "name": ["Bobby", "Diana"], "value": [250, 400]})

        result = ops.merge_rows(
            table_path=table_path,
            source_dataframe=source,
            merge_condition="target.id = source.id",
            when_matched={"name": "source.name", "value": "source.value"},
            when_not_matched={"id": "source.id", "name": "source.name", "value": "source.value"},
        )

        assert result.success is True

        dt = DeltaTable(str(table_path))
        table = dt.to_pyarrow_table()
        assert table.num_rows == 4

        names = table.column("name").to_pylist()
        assert "Bobby" in names
        assert "Diana" in names


class TestDeltaLakeMaintenanceResult:
    def test_result_timing(self, tmp_path):

        from benchbox.platforms.dataframe.delta_lake_maintenance import (
            DeltaLakeMaintenanceOperations,
        )

        ops = DeltaLakeMaintenanceOperations()
        table_path = tmp_path / "test_table"

        data = pa.table({"id": [1, 2, 3], "name": ["A", "B", "C"]})
        result = ops.insert_rows(table_path=table_path, dataframe=data, mode="append")

        assert result.success is True
        assert result.duration is not None
        assert result.duration >= 0


class TestDeltaLakeDataFrameConversion:
    def test_convert_polars_dataframe(self, tmp_path):

        pytest.importorskip("polars")
        import polars as pl

        from benchbox.platforms.dataframe.delta_lake_maintenance import (
            DeltaLakeMaintenanceOperations,
        )

        ops = DeltaLakeMaintenanceOperations()
        table_path = tmp_path / "test_table"

        df = pl.DataFrame({"id": [1, 2, 3], "name": ["Alice", "Bob", "Charlie"]})

        result = ops.insert_rows(table_path=table_path, dataframe=df, mode="append")

        assert result.success is True
        assert result.rows_affected == 3

    def test_convert_pandas_dataframe(self, tmp_path):

        pytest.importorskip("pandas")
        import pandas as pd

        from benchbox.platforms.dataframe.delta_lake_maintenance import (
            DeltaLakeMaintenanceOperations,
        )

        ops = DeltaLakeMaintenanceOperations()
        table_path = tmp_path / "test_table"

        df = pd.DataFrame({"id": [1, 2, 3], "name": ["Alice", "Bob", "Charlie"]})

        result = ops.insert_rows(table_path=table_path, dataframe=df, mode="append")

        assert result.success is True
        assert result.rows_affected == 3


