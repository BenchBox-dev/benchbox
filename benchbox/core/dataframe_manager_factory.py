from __future__ import annotations

from logging import Logger
from typing import Any, TypeVar

ManagerT = TypeVar("ManagerT")


def get_spark_maintenance_operations(spark_session: Any, logger: Logger) -> Any:
    if spark_session is None:
        logger.debug("No SparkSession provided for PySpark platform")
        return None
    try:
        from benchbox.platforms.dataframe.pyspark_maintenance import get_pyspark_maintenance_operations

        return get_pyspark_maintenance_operations(spark_session=spark_session, prefer_delta=True)
    except ImportError:
        logger.debug("PySpark maintenance module not available")
        return None


def get_dataframe_manager(
    platform_name: str,
    *,
    manager_class: type[ManagerT],
    supported_platforms: tuple[str, ...],
    logger: Logger,
    manager_label: str,
    spark_session: Any = None,
    execution_engine: str | None = None,
) -> ManagerT | None:
    platform_lower = platform_name.lower()
    if not any(platform in platform_lower for platform in supported_platforms):
        logger.debug(f"Platform {platform_name} is not a DataFrame platform")
        return None

    try:
        if execution_engine is None:
            return manager_class(platform_name, spark_session=spark_session)
        return manager_class(platform_name, spark_session=spark_session, execution_engine=execution_engine)
    except Exception as exc:
        logger.warning(f"Failed to create {manager_label} manager for {platform_name}: {exc}")
        return None
