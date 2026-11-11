# Copyright 2026 Joe Harris / BenchBox Project

import shutil
from pathlib import Path

import pytest

try:
    import polars as pl

    POLARS_AVAILABLE = True
except ImportError:
    POLARS_AVAILABLE = False
    pl = None

from benchbox.core.dataframe.maintenance_interface import (
    MaintenanceOperationType,
    get_maintenance_operations_for_platform,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.skipif(not POLARS_AVAILABLE, reason="Polars not installed"),
]


@pytest.fixture
def temp_table_dir(tmp_path: Path) -> Path:
    table_dir = tmp_path / "test_table"
    table_dir.mkdir()
    return table_dir


@pytest.fixture
def sample_df():
    return pl.DataFrame(
        {
            "id": [1, 2, 3, 4, 5],
            "name": ["Alice", "Bob", "Charlie", "Diana", "Eve"],
            "amount": [100.0, 200.0, 150.0, 300.0, 250.0],
            "region": ["US", "EU", "US", "EU", "APAC"],
        }
    )


@pytest.fixture
def existing_table(temp_table_dir: Path, sample_df):
    sample_df.write_parquet(temp_table_dir / "part-00000.parquet")
    return temp_table_dir


class TestPolarsMaintenanceAvailability:
    def test_get_maintenance_operations_returns_polars(self):

        ops = get_maintenance_operations_for_platform("polars-df")

        assert ops is not None
        from benchbox.platforms.dataframe.polars_maintenance import (
            PolarsMaintenanceOperations,
        )

        assert isinstance(ops, PolarsMaintenanceOperations)

    def test_get_maintenance_operations_polars_alias(self):

        ops = get_maintenance_operations_for_platform("polars")
        assert ops is not None

    def test_capabilities(self):

        ops = get_maintenance_operations_for_platform("polars-df")
        caps = ops.get_capabilities()

        assert caps.platform_name == "polars"
        assert caps.supports_insert is True
        assert caps.supports_delete is True
        assert caps.supports_update is True
        assert caps.supports_merge is True
        assert caps.supports_partitioned_delete is True
        assert caps.supports_transactions is False
        assert caps.supports_time_travel is False


class TestPolarsInsert:
    def test_insert_new_rows(self, temp_table_dir: Path, sample_df):

        ops = get_maintenance_operations_for_platform("polars-df")

        result = ops.insert_rows(temp_table_dir, sample_df, mode="append")

        assert result.success is True
        assert result.rows_affected == 5
        assert result.operation_type == MaintenanceOperationType.INSERT

        written = pl.read_parquet(temp_table_dir / "part-00000.parquet")
        assert written.height == 5

    def test_insert_append_mode(self, existing_table: Path, sample_df):

        ops = get_maintenance_operations_for_platform("polars-df")

        new_df = pl.DataFrame(
            {"id": [6, 7], "name": ["Frank", "Grace"], "amount": [400.0, 500.0], "region": ["US", "EU"]}
        )

        result = ops.insert_rows(existing_table, new_df, mode="append")

        assert result.success is True
        assert result.rows_affected == 2

        parquet_files = list(existing_table.glob("*.parquet"))
        assert len(parquet_files) == 2

        all_data = pl.read_parquet(existing_table / "*.parquet")
        assert all_data.height == 7

    def test_insert_overwrite_mode(self, existing_table: Path, sample_df):

        ops = get_maintenance_operations_for_platform("polars-df")

        new_df = pl.DataFrame(
            {"id": [10, 11], "name": ["New1", "New2"], "amount": [1000.0, 1100.0], "region": ["US", "US"]}
        )

        result = ops.insert_rows(existing_table, new_df, mode="overwrite")

        assert result.success is True
        assert result.rows_affected == 2

        all_data = pl.read_parquet(existing_table / "*.parquet")
        assert all_data.height == 2
        assert set(all_data["id"].to_list()) == {10, 11}

    def test_insert_lazy_frame(self, temp_table_dir: Path):

        ops = get_maintenance_operations_for_platform("polars-df")

        lazy_df = pl.LazyFrame({"id": [1, 2, 3], "value": ["a", "b", "c"]})

        result = ops.insert_rows(temp_table_dir, lazy_df, mode="append")

        assert result.success is True
        assert result.rows_affected == 3

    def test_insert_empty_dataframe(self, temp_table_dir: Path):
        ops = get_maintenance_operations_for_platform("polars-df")

        empty_df = pl.DataFrame({"id": [], "value": []})

        result = ops.insert_rows(temp_table_dir, empty_df, mode="append")

        assert result.success is True
        assert result.rows_affected == 0

    def test_insert_creates_directory(self, tmp_path: Path):

        ops = get_maintenance_operations_for_platform("polars-df")
        table_path = tmp_path / "new_table"

        df = pl.DataFrame({"id": [1], "value": ["test"]})
        result = ops.insert_rows(table_path, df, mode="append")

        assert result.success is True
        assert table_path.exists()

    def test_insert_with_partitioning(self, temp_table_dir: Path):

        ops = get_maintenance_operations_for_platform("polars-df")

        df = pl.DataFrame({"id": [1, 2, 3, 4], "region": ["US", "US", "EU", "EU"], "value": [10, 20, 30, 40]})

        result = ops.insert_rows(temp_table_dir, df, partition_columns=["region"], mode="append")

        assert result.success is True
        assert result.rows_affected == 4

        assert (temp_table_dir / "region=US").exists()
        assert (temp_table_dir / "region=EU").exists()


class TestPolarsDelete:
    def test_delete_with_condition(self, existing_table: Path):

        ops = get_maintenance_operations_for_platform("polars-df")

        result = ops.delete_rows(existing_table, "amount > 200")

        assert result.success is True
        assert result.rows_affected == 2

        remaining = pl.read_parquet(existing_table / "*.parquet")
        assert remaining.height == 3
        assert all(amt <= 200 for amt in remaining["amount"].to_list())

    def test_delete_string_condition(self, existing_table: Path):

        ops = get_maintenance_operations_for_platform("polars-df")

        result = ops.delete_rows(existing_table, "region = 'US'")

        assert result.success is True
        assert result.rows_affected == 2

        remaining = pl.read_parquet(existing_table / "*.parquet")
        assert remaining.height == 3
        assert "US" not in remaining["region"].to_list()

    def test_delete_no_matches(self, existing_table: Path):

        ops = get_maintenance_operations_for_platform("polars-df")

        result = ops.delete_rows(existing_table, "amount > 1000")

        assert result.success is True
        assert result.rows_affected == 0

        remaining = pl.read_parquet(existing_table / "*.parquet")
        assert remaining.height == 5

    def test_delete_all_rows(self, existing_table: Path):

        ops = get_maintenance_operations_for_platform("polars-df")

        result = ops.delete_rows(existing_table, "id > 0")

        assert result.success is True
        assert result.rows_affected == 5

        assert existing_table.exists()
        parquet_files = list(existing_table.glob("*.parquet"))
        assert len(parquet_files) == 0

    def test_delete_nonexistent_table(self, tmp_path: Path):

        ops = get_maintenance_operations_for_platform("polars-df")

        result = ops.delete_rows(tmp_path / "nonexistent", "id > 0")

        assert result.success is True
        assert result.rows_affected == 0

    def test_delete_compound_condition(self, existing_table: Path):

        ops = get_maintenance_operations_for_platform("polars-df")

        result = ops.delete_rows(existing_table, "amount > 150 AND region = 'EU'")

        assert result.success is True
        assert result.rows_affected == 2


class TestPolarsUpdate:
    def test_update_single_column(self, existing_table: Path):

        ops = get_maintenance_operations_for_platform("polars-df")

        result = ops.update_rows(existing_table, "id = 1", {"name": "'Updated'"})

        assert result.success is True
        assert result.rows_affected == 1

        df = pl.read_parquet(existing_table / "*.parquet")
        updated_row = df.filter(pl.col("id") == 1)
        assert updated_row["name"][0] == "Updated"

    def test_update_multiple_rows(self, existing_table: Path):

        ops = get_maintenance_operations_for_platform("polars-df")

        result = ops.update_rows(existing_table, "region = 'EU'", {"amount": "0"})

        assert result.success is True
        assert result.rows_affected == 2

        df = pl.read_parquet(existing_table / "*.parquet")
        eu_rows = df.filter(pl.col("region") == "EU")
        assert eu_rows["amount"].to_list() == [0, 0]

    def test_update_no_matching_rows(self, existing_table: Path):

        ops = get_maintenance_operations_for_platform("polars-df")

        result = ops.update_rows(existing_table, "id = 999", {"name": "'NoMatch'"})

        assert result.success is True
        assert result.rows_affected == 0

    def test_update_with_expression(self, existing_table: Path):

        ops = get_maintenance_operations_for_platform("polars-df")

        result = ops.update_rows(existing_table, "amount > 200", {"amount": "amount * 2"})

        assert result.success is True
        assert result.rows_affected == 2

        df = pl.read_parquet(existing_table / "*.parquet")
        diana = df.filter(pl.col("name") == "Diana")
        eve = df.filter(pl.col("name") == "Eve")
        assert diana["amount"][0] == 600.0
        assert eve["amount"][0] == 500.0


class TestPolarsMerge:
    def test_merge_update_existing(self, existing_table: Path):

        ops = get_maintenance_operations_for_platform("polars-df")

        source_df = pl.DataFrame(
            {
                "id": [1, 2],
                "name": ["Alice Updated", "Bob Updated"],
                "amount": [999.0, 888.0],
                "region": ["US", "EU"],
            }
        )

        result = ops.merge_rows(
            existing_table,
            source_df,
            merge_condition="target.id = source.id",
            when_matched={"name": "source.name", "amount": "source.amount"},
            when_not_matched=None,
        )

        assert result.success is True
        assert result.rows_affected == 2

        df = pl.read_parquet(existing_table / "*.parquet")
        alice = df.filter(pl.col("id") == 1)
        assert alice["name"][0] == "Alice Updated"
        assert alice["amount"][0] == 999.0

    def test_merge_insert_new(self, existing_table: Path):

        ops = get_maintenance_operations_for_platform("polars-df")

        source_df = pl.DataFrame(
            {
                "id": [10, 11],
                "name": ["New1", "New2"],
                "amount": [1000.0, 1100.0],
                "region": ["APAC", "APAC"],
            }
        )

        result = ops.merge_rows(
            existing_table,
            source_df,
            merge_condition="id",
            when_matched=None,
            when_not_matched={"id": "source.id", "name": "source.name"},
        )

        assert result.success is True
        assert result.rows_affected == 2

        df = pl.read_parquet(existing_table / "*.parquet")
        assert df.height == 7
        new_rows = df.filter(pl.col("id").is_in([10, 11]))
        assert new_rows.height == 2

    def test_merge_upsert(self, existing_table: Path):

        ops = get_maintenance_operations_for_platform("polars-df")

        source_df = pl.DataFrame(
            {
                "id": [1, 100],
                "name": ["Alice Upserted", "NewPerson"],
                "amount": [111.0, 100.0],
                "region": ["US", "EU"],
            }
        )

        result = ops.merge_rows(
            existing_table,
            source_df,
            merge_condition="id",
            when_matched={"name": "source.name"},
            when_not_matched={"id": "source.id", "name": "source.name"},
        )

        assert result.success is True
        assert result.rows_affected == 2

        df = pl.read_parquet(existing_table / "*.parquet")
        assert df.height == 6

        alice = df.filter(pl.col("id") == 1)
        assert alice["name"][0] == "Alice Upserted"

        new_person = df.filter(pl.col("id") == 100)
        assert new_person.height == 1

    def test_merge_to_empty_table(self, temp_table_dir: Path):
        ops = get_maintenance_operations_for_platform("polars-df")

        source_df = pl.DataFrame(
            {
                "id": [1, 2, 3],
                "name": ["A", "B", "C"],
                "amount": [100.0, 200.0, 300.0],
                "region": ["US", "EU", "APAC"],
            }
        )

        result = ops.merge_rows(
            temp_table_dir / "new_table",
            source_df,
            merge_condition="id",
            when_matched={"name": "source.name"},
            when_not_matched={"id": "source.id", "name": "source.name"},
        )

        assert result.success is True
        assert result.rows_affected == 3


class TestPolarsMaintenanceResult:
    def test_result_timing(self, temp_table_dir: Path):

        ops = get_maintenance_operations_for_platform("polars-df")

        df = pl.DataFrame({"id": list(range(1000)), "value": list(range(1000))})

        result = ops.insert_rows(temp_table_dir, df, mode="append")

        assert result.start_time > 0
        assert result.end_time >= result.start_time
        assert result.duration >= 0
        assert result.duration == pytest.approx(result.end_time - result.start_time, abs=0.001)


