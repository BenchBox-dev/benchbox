from __future__ import annotations

from pathlib import Path

import pytest

from benchbox.platforms.pyspark.sql_adapter import PySparkSQLAdapter
from tests.unit.platforms.pyspark.conftest import PYSPARK_SQL_SKIP_REASON, PYSPARK_SQL_TESTS_SKIPPED

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
    pytest.mark.usefixtures("spark_runtime_environment"),
    pytest.mark.xdist_group("pyspark"),
    pytest.mark.skipif(PYSPARK_SQL_TESTS_SKIPPED, reason=PYSPARK_SQL_SKIP_REASON),
]


class _MiniBenchmark:
    def __init__(self, table_dir: Path):
        self.tables = {"nation": [table_dir / "nation.tbl"]}

    def get_create_tables_sql(self, dialect: str = "duckdb", tuning_config=None) -> str:
        return """
        CREATE TABLE nation (
            n_nationkey INT,
            n_name STRING
        );
        """


def _write_sample_tbl(file_path: Path) -> None:
    file_path.write_text("0|ALGERIA|\n1|ARGENTINA|\n", encoding="utf-8")


def test_select_one(tmp_path, pyspark_test_environment):

    adapter = PySparkSQLAdapter(master="local[1]", database="pyspark_sql_tests", warehouse_dir=str(tmp_path))
    connection = adapter.create_connection()

    result = adapter.execute_query(connection, "SELECT 1 AS value", query_id="Q0", benchmark_type="smoke")
    assert result["status"] == "SUCCESS"
    assert result["rows_returned"] == 1
    adapter.close()


def test_schema_creation_and_loading(tmp_path, pyspark_test_environment):

    table_dir = tmp_path / "data"
    table_dir.mkdir()
    _write_sample_tbl(table_dir / "nation.tbl")

    benchmark = _MiniBenchmark(table_dir)
    adapter = PySparkSQLAdapter(master="local[1]", database="pyspark_sql_schema", warehouse_dir=str(tmp_path))
    conn = adapter.create_connection()

    adapter.create_schema(benchmark, conn)
    stats, _, _ = adapter.load_data(benchmark, conn, table_dir)
    assert stats.get("nation") == 2

    result = adapter.execute_query(conn, "SELECT COUNT(*) AS c FROM nation", query_id="Q1", benchmark_type="tpch")
    assert result["rows_returned"] == 1
    adapter.close()
