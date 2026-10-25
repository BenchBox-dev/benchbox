from __future__ import annotations

import logging
from typing import Any

from benchbox.platforms.spark import SparkAdapter
from benchbox.utils.dependencies import get_package_install_message

from .session import PYSPARK_AVAILABLE, SparkSessionManager, SparkUnavailableError

logger = logging.getLogger(__name__)


class PySparkSQLAdapter(SparkAdapter):
    plan_capture_phase_eligible = True

    def __init__(self, **config: Any) -> None:
        if not PYSPARK_AVAILABLE:
            raise SparkUnavailableError(get_package_install_message("pyspark pyarrow", "PySpark is not installed."))

        super().__init__(**config)
        self._session_claimed = False

    @property
    def platform_name(self) -> str:
        return "PySpark SQL"

    def create_connection(self, **connection_config: Any) -> Any:
        self.log_operation_start("PySpark SQL session")

        extra_configs = self._get_spark_conf()

        try:
            spark = SparkSessionManager.get_or_create(
                master=self.master,
                app_name=self.app_name,
                driver_memory=self.driver_memory,
                executor_memory=self.executor_memory,
                shuffle_partitions=self.shuffle_partitions,
                enable_aqe=self.adaptive_enabled,
                extra_configs=extra_configs,
                verbose=self.verbose,
            )
            self._session_claimed = True
            self._spark_session = spark

            self.handle_existing_database(**connection_config)

            target_database = connection_config.get("database", self.database)

            if not self.database_was_reused:
                spark.sql(f"CREATE DATABASE IF NOT EXISTS {target_database}")
                self.logger.info(f"Created PySpark database {target_database}")

            spark.sql(f"USE {target_database}")

            self.log_operation_complete(
                "PySpark SQL session",
                details=f"master={self.master}, database={target_database}",
            )

            return spark
        except Exception as exc:
            logger.error("Failed to create PySpark SQL session: %s", exc)
            raise

    def close_connection(self, connection: Any) -> None:
        try:
            if self._session_claimed:
                SparkSessionManager.release()
                self._session_claimed = False
        finally:
            self._spark_session = None

    def close(self) -> None:
        self.close_connection(self._spark_session)
