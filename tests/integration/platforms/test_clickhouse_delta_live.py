# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from pathlib import Path

import pyarrow.parquet as pq
import pytest

from benchbox.utils.delta_export import export_delta_to_parquet

from .delta_live_helpers import delta_live_skip_reason, make_delta_spark_session

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live_integration,
    pytest.mark.live_delta,
    pytest.mark.skipif(
        delta_live_skip_reason() is not None,
        reason=delta_live_skip_reason() or "PySpark + Delta Lake runtime unavailable",
    ),
]


@pytest.fixture(scope="module")
def spark(tmp_path_factory, pyspark_test_environment):
    warehouse = tmp_path_factory.mktemp("delta_clickhouse_warehouse")
    session = make_delta_spark_session(warehouse, app_name="benchbox-delta-clickhouse")
    yield session
    session.stop()


@pytest.fixture
def table_dir(tmp_path: Path) -> Path:
    target = tmp_path / "tables"
    target.mkdir()
    return target


class TestSparkDeltaToParquetChain:
    def test_export_matches_spark_table(self, spark, table_dir: Path):
        delta_path = table_dir / "orders"
        spark.createDataFrame([(1, "a"), (2, "b"), (3, "c")], ["id", "v"]).write.format("delta").mode("overwrite").save(
            str(delta_path)
        )
        spark.createDataFrame([(4, "d")], ["id", "v"]).write.format("delta").mode("append").save(str(delta_path))
        expected_latest = spark.read.format("delta").load(str(delta_path)).count()

        out = table_dir / "parquet"
        result = export_delta_to_parquet(delta_path, out)

        assert result.row_count == expected_latest == 4
        exported = pq.read_table(result.parquet_files[0])
        assert exported.num_rows == 4
        assert exported.schema.names == ["id", "v"]

    def test_export_pinned_version_matches_history(self, spark, table_dir: Path):
        delta_path = table_dir / "versioned"
        spark.createDataFrame([(1, "a")], ["id", "v"]).write.format("delta").mode("overwrite").save(str(delta_path))
        spark.createDataFrame([(2, "b")], ["id", "v"]).write.format("delta").mode("append").save(str(delta_path))
        expected_v0 = spark.read.format("delta").option("versionAsOf", 0).load(str(delta_path)).count()

        out = table_dir / "parquet_v0"
        result = export_delta_to_parquet(delta_path, out, version=0)

        assert result.version == 0
        assert result.row_count == expected_v0 == 1


class TestClickHouseIngestionContract:
    def test_single_parquet_directory_glob(self, spark, table_dir: Path):
        delta_path = table_dir / "clickhouse_src"
        spark.createDataFrame([(1, "a"), (2, "b")], ["id", "v"]).write.format("delta").mode("overwrite").save(
            str(delta_path)
        )

        out = table_dir / "clickhouse_parquet"
        result = export_delta_to_parquet(delta_path, out)

        assert result.output_dir.is_dir()
        assert result.parquet_files, "ClickHouse s3 glob needs at least one .parquet file"
        assert all(path.suffix == ".parquet" and path.parent == result.output_dir for path in result.parquet_files)
        total = sum(pq.read_table(path).num_rows for path in result.parquet_files)
        assert total == result.row_count == 2


