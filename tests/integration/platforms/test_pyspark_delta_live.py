# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from pathlib import Path

import pytest

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

    warehouse = tmp_path_factory.mktemp("delta_warehouse")
    session = make_delta_spark_session(warehouse, app_name="benchbox-delta-runtime")
    yield session
    session.stop()


@pytest.fixture
def table_dir(tmp_path: Path) -> Path:
    target = tmp_path / "tables"
    target.mkdir()
    return target


class TestDeltaSessionBaseline:
    def test_delta_extension_configured(self, spark):
        assert "DeltaSparkSessionExtension" in spark.conf.get("spark.sql.extensions")
        assert spark.conf.get("spark.sql.catalog.spark_catalog") == "org.apache.spark.sql.delta.catalog.DeltaCatalog"

    def test_spark_version_supported(self, spark):
        major = int(spark.version.split(".")[0])
        assert major >= 3


class TestDeltaRoundTrip:
    def test_write_read_row_count_and_schema(self, spark, table_dir: Path):
        path = str(table_dir / "roundtrip")
        df = spark.createDataFrame([(1, "a"), (2, "b"), (3, "c")], ["id", "v"])
        df.write.format("delta").mode("overwrite").save(path)

        back = spark.read.format("delta").load(path)
        assert back.count() == 3
        assert [field.name for field in back.schema.fields] == ["id", "v"]
        assert sorted(row["id"] for row in back.collect()) == [1, 2, 3]

    def test_delta_log_present(self, spark, table_dir: Path):
        path = table_dir / "logcheck"
        spark.createDataFrame([(1,)], ["id"]).write.format("delta").mode("overwrite").save(str(path))
        assert (path / "_delta_log").is_dir()

    def test_history_records_initial_version(self, spark, table_dir: Path):
        from delta.tables import DeltaTable

        path = str(table_dir / "history")
        spark.createDataFrame([(1,)], ["id"]).write.format("delta").mode("overwrite").save(path)
        versions = [row["version"] for row in DeltaTable.forPath(spark, path).history().collect()]
        assert versions == [0]

    def test_overwrite_bumps_version(self, spark, table_dir: Path):
        from delta.tables import DeltaTable

        path = str(table_dir / "overwrite")
        spark.createDataFrame([(1,)], ["id"]).write.format("delta").mode("overwrite").save(path)
        spark.createDataFrame([(1,), (2,)], ["id"]).write.format("delta").mode("overwrite").save(path)
        versions = sorted(row["version"] for row in DeltaTable.forPath(spark, path).history().collect())
        assert versions == [0, 1]
        assert spark.read.format("delta").load(path).count() == 2


class TestDeltaSqlDdlDml:
    def test_create_insert_select_update_delete(self, spark, table_dir: Path):
        path = str(table_dir / "sqlops").replace("\\", "/")
        spark.sql(f"CREATE TABLE delta_sql (id INT, v STRING) USING DELTA LOCATION '{path}'")
        spark.sql("INSERT INTO delta_sql VALUES (1, 'a'), (2, 'b')")
        assert spark.sql("SELECT COUNT(*) AS cnt FROM delta_sql").collect()[0]["cnt"] == 2

        spark.sql("UPDATE delta_sql SET v = 'z' WHERE id = 1")
        assert spark.sql("SELECT v FROM delta_sql WHERE id = 1").collect()[0]["v"] == "z"

        spark.sql("DELETE FROM delta_sql WHERE id = 2")
        assert spark.sql("SELECT COUNT(*) AS cnt FROM delta_sql").collect()[0]["cnt"] == 1

    def test_merge_upsert(self, spark, table_dir: Path):
        path = str(table_dir / "sqlmerge").replace("\\", "/")
        spark.sql(f"CREATE TABLE delta_merge (id INT, v STRING) USING DELTA LOCATION '{path}'")
        spark.sql("INSERT INTO delta_merge VALUES (1, 'a')")
        spark.sql(
            """
            MERGE INTO delta_merge AS t
            USING (SELECT 1 AS id, 'b' AS v UNION ALL SELECT 2 AS id, 'c' AS v) AS s
            ON t.id = s.id
            WHEN MATCHED THEN UPDATE SET v = s.v
            WHEN NOT MATCHED THEN INSERT (id, v) VALUES (s.id, s.v)
            """
        )
        rows = {row["id"]: row["v"] for row in spark.sql("SELECT id, v FROM delta_merge").collect()}
        assert rows == {1: "b", 2: "c"}


