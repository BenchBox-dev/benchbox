# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import importlib
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def delta_live_skip_reason() -> str | None:
    try:
        importlib.import_module("delta")
        importlib.import_module("pyspark")
    except ImportError as exc:
        return f"PySpark + Delta Lake runtime not installed: {exc}"

    from tests.utilities.optional_engines import pyspark_skip_reason

    reason = pyspark_skip_reason()
    if reason:
        return f"PySpark unusable: {reason}"

    return None


def make_delta_spark_session(warehouse_dir: Path | str, app_name: str = "benchbox-delta-live") -> Any:
    from benchbox.platforms.pyspark import ensure_compatible_java

    ensure_compatible_java()

    from delta import configure_spark_with_delta_pip
    from pyspark.sql import SparkSession

    builder = (
        SparkSession.builder.appName(app_name)
        .master("local[2]")
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")
        .config("spark.sql.warehouse.dir", str(warehouse_dir))
    )
    spark = configure_spark_with_delta_pip(builder).getOrCreate()
    spark.sparkContext.setLogLevel("ERROR")
    return spark
