from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from benchbox.core.dataframe.tuning import DataFrameTuningConfiguration
from benchbox.core.deployment import deployment_class, select_adapter_deployment
from benchbox.platforms._spark_helpers import spark_aqe_conf_entries
from benchbox.platforms.dataframe.expression_family import ExpressionFamilyAdapter
from benchbox.platforms.dataframe.pyspark_df import (
    PYSPARK_AVAILABLE,
    PYSPARK_VERSION,
    Column,
    DataFrame,
    F,
    PySparkDataFrameAdapter,
    SparkSession,
    Window,
)

if PYSPARK_AVAILABLE:
    LakeSailDF = DataFrame
    LakeSailLazyDF = DataFrame
    LakeSailExpr = Column
else:
    LakeSailDF = Any
    LakeSailLazyDF = Any
    LakeSailExpr = Any


class LakeSailDataFrameAdapter(PySparkDataFrameAdapter):
    def __init__(
        self,
        working_dir: str | Path | None = None,
        verbose: bool = False,
        very_verbose: bool = False,
        tuning_config: DataFrameTuningConfiguration | None = None,
        endpoint: str = "sc://localhost:50051",
        app_name: str = "BenchBox-LakeSail-DF",
        driver_memory: str = "4g",
        shuffle_partitions: int | None = None,
        enable_aqe: bool = True,
        deployment_mode: str | None = None,
        **spark_config: Any,
    ) -> None:
        if not PYSPARK_AVAILABLE:
            raise ImportError(
                "PySpark not installed. Install with: pip install pyspark pyarrow\n"
                "LakeSail Sail uses the standard PySpark client via Spark Connect."
            )

        ExpressionFamilyAdapter.__init__(
            self,
            working_dir=working_dir,
            verbose=verbose,
            very_verbose=very_verbose,
            tuning_config=tuning_config,
        )
        self._endpoint = endpoint
        self._app_name = app_name
        self._driver_memory = driver_memory
        self._shuffle_partitions = shuffle_partitions or os.cpu_count() or 8
        self._enable_aqe = enable_aqe
        self.deployment_selected = select_adapter_deployment("lakesail", {"deployment_mode": deployment_mode}, "local")
        self.deployment_selected_class = deployment_class("lakesail", self.deployment_selected)
        self._spark_config = spark_config
        self._spark: SparkSession | None = None
        self._validate_and_apply_tuning()

    def _apply_tuning(self) -> None:
        config = self._tuning_config
        if config.parallelism.thread_count is not None:
            self._shuffle_partitions = config.parallelism.thread_count
            self._log_verbose(f"Set shuffle_partitions={self._shuffle_partitions}")
        if config.memory.memory_limit is not None:
            self._driver_memory = config.memory.memory_limit
            self._log_verbose(f"Set driver_memory={self._driver_memory}")
        if config.execution.streaming_mode:
            self._log_verbose("Note: streaming_mode not applicable to LakeSail batch DataFrames")

    @property
    def platform_name(self) -> str:
        return "LakeSail"

    def _get_or_create_session(self) -> SparkSession:
        if self._spark is None:
            builder = SparkSession.builder.remote(self._endpoint)
            builder = builder.config("spark.app.name", self._app_name)
            builder = builder.config("spark.sql.shuffle.partitions", str(self._shuffle_partitions))
            for aqe_key, aqe_value in spark_aqe_conf_entries(self._enable_aqe).items():
                builder = builder.config(aqe_key, aqe_value)
            for key, value in self._spark_config.items():
                builder = builder.config(key, str(value))
            self._spark = builder.getOrCreate()
            if self.verbose and self._spark is not None:
                self._log_verbose(f"LakeSail Spark Connect session acquired: endpoint={self._endpoint}")
        return self._spark

    def close(self) -> None:
        if self._spark is not None:
            try:
                self._spark.stop()
            except Exception:
                pass
            if self.verbose:
                self._log_verbose("LakeSail Spark Connect session stopped")
        self._spark = None

    def __enter__(self) -> LakeSailDataFrameAdapter:
        return self

    def get_platform_info(self) -> dict[str, Any]:
        info = {
            "platform": self.platform_name,
            "family": self.family,
            "endpoint": self._endpoint,
            "deployment_mode": self.deployment_selected,
            "driver_memory": self._driver_memory,
            "shuffle_partitions": self._shuffle_partitions,
            "aqe_enabled": self._enable_aqe,
            "working_dir": str(self.working_dir),
        }
        if PYSPARK_AVAILABLE:
            info["pyspark_version"] = PYSPARK_VERSION
        return info

    def get_tuning_summary(self) -> dict[str, Any]:
        summary = super(PySparkDataFrameAdapter, self).get_tuning_summary()
        summary.update(
            {
                "endpoint": self._endpoint,
                "driver_memory": self._driver_memory,
                "shuffle_partitions": self._shuffle_partitions,
                "aqe_enabled": self._enable_aqe,
                "pyspark_version": PYSPARK_VERSION,
            }
        )
        return summary


__all__ = [
    "F",
    "PYSPARK_AVAILABLE",
    "PYSPARK_VERSION",
    "LakeSailDF",
    "LakeSailDataFrameAdapter",
    "LakeSailExpr",
    "LakeSailLazyDF",
    "SparkSession",
    "Window",
]
