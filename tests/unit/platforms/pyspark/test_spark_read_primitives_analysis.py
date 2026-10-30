from __future__ import annotations

from decimal import Decimal

import pytest

from benchbox.core.read_primitives.catalog import load_primitives_catalog
from benchbox.platforms.pyspark.sql_adapter import PySparkSQLAdapter
from tests.unit.platforms.pyspark.conftest import PYSPARK_SQL_SKIP_REASON, PYSPARK_SQL_TESTS_SKIPPED

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
    pytest.mark.usefixtures("spark_runtime_environment"),
    pytest.mark.xdist_group("pyspark"),
    pytest.mark.skipif(PYSPARK_SQL_TESTS_SKIPPED, reason=PYSPARK_SQL_SKIP_REASON),
]


def test_list_reduce_variant_analyzes_against_decimal_quantity(tmp_path, pyspark_test_environment):
    adapter = PySparkSQLAdapter(master="local[1]", database="pyspark_list_reduce", warehouse_dir=str(tmp_path))
    spark = adapter.create_connection()
    spark.sql(
        "CREATE OR REPLACE TEMP VIEW lineitem AS "
        "SELECT CAST(1 AS BIGINT) AS l_orderkey, CAST(12.50 AS DECIMAL(15, 2)) AS l_quantity "
        "UNION ALL SELECT CAST(1 AS BIGINT), CAST(3.25 AS DECIMAL(15, 2))"
    )

    variant = load_primitives_catalog().queries["list_reduce"].variants["spark"]
    rows = spark.sql(variant).collect()

    assert [(row["l_orderkey"], row["total_qty"]) for row in rows] == [(1, Decimal("15.75"))]
    adapter.close()
