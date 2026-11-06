# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from benchbox.core.deployment import deployment_class
from benchbox.platforms.base import DriverIsolationCapability, PlatformAdapter
from benchbox.utils.dependencies import (
    check_platform_dependencies,
    get_dependency_error_message,
    get_package_install_message,
)

from ._dependencies import INFLUXDB_AVAILABLE
from .metadata import InfluxDBMetadataMixin
from .setup import InfluxDBSetupMixin

if TYPE_CHECKING:
    from benchbox.core.tuning.interface import (
        ForeignKeyConfiguration,
        PlatformOptimizationConfiguration,
        PrimaryKeyConfiguration,
    )

logger = logging.getLogger(__name__)


class InfluxDBAdapter(
    InfluxDBMetadataMixin,
    InfluxDBSetupMixin,
    PlatformAdapter,
):
    plan_capture_phase_eligible = True
    default_service_port = 8181

    driver_isolation_capability = DriverIsolationCapability.FEASIBLE_CLIENT_ONLY

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> InfluxDBAdapter:
        nested = config.get("options")
        merged = {**nested, **config} if isinstance(nested, dict) else dict(config)
        return cls(**merged)

    def __init__(self, **config):
        super().__init__(**config)

        if not INFLUXDB_AVAILABLE:
            available, missing = check_platform_dependencies("influxdb")
            if not available:
                error_msg = get_dependency_error_message("influxdb", missing)
                raise ImportError(error_msg)

        self._dialect = "influxdb"

        token = config.get("token") or os.environ.get("INFLUXDB_TOKEN")
        config["token"] = token

        self._setup_connection_params(config)

        if self.mode not in ("core", "cloud"):
            raise ValueError(f"Invalid InfluxDB mode '{self.mode}'. Must be 'core' or 'cloud'.")
        self.deployment_selected = self.mode
        self.deployment_selected_class = deployment_class("influxdb", self.mode)

        if self.mode == "core":
            protocol = "http" if not self.ssl else "https"
            self.logger.info(f"InfluxDB Core mode: {protocol}://{self.host}:{self.port}")
        else:
            self.logger.info(f"InfluxDB Cloud mode: {self.host}")

    def get_table_row_count(self, connection: Any, table: str) -> int:
        query = f'SELECT COUNT(*) FROM "{table}"'
        result = connection.execute(query)
        if result and len(result) > 0:
            return result[0][0] if result[0][0] else 0
        return 0

    def get_tables(self, connection: Any = None) -> list[str]:
        if connection is None:
            connection = self.connection
        query = "SELECT table_name FROM information_schema.tables WHERE table_schema = 'iox'"
        try:
            result = connection.execute(query)
            return [row[0] for row in result if row[0]]
        except (RuntimeError, ConnectionError):
            return []

    def table_exists(self, table_name: str, connection: Any = None) -> bool:
        tables = self.get_tables(connection)
        return table_name in tables

    def drop_table(self, table_name: str, connection: Any = None) -> None:
        self.logger.warning(
            f"InfluxDB Core does not support DROP TABLE. Table '{table_name}' cannot be deleted via SQL."
        )

    def create_schema(self, benchmark, connection: Any) -> float:
        self.logger.info(
            "InfluxDB auto-creates tables from write operations. Explicit schema creation is not required."
        )
        return 0.0

    def load_data(
        self, benchmark, connection: Any, data_dir: Path
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        import time

        from ._dependencies import INFLUXDB3_AVAILABLE

        if not INFLUXDB3_AVAILABLE:
            self.logger.warning(
                "Line Protocol writes require influxdb3-python. Data loading skipped. "
                + get_package_install_message("influxdb3-python", "")
            )
            return {}, 0.0, {"skipped": True, "reason": "influxdb3-python not available"}

        if not hasattr(connection, "write_table"):
            self.logger.warning(
                "Connection does not support batch writes. "
                "Data loading requires InfluxDBConnection with influxdb3-python client."
            )
            return {}, 0.0, {"skipped": True, "reason": "write not supported"}

        tsbs_tables = self._tsbs_table_configs()

        row_counts: dict[str, int] = {}
        total_time = 0.0
        load_metadata: dict[str, Any] = {"tables_loaded": [], "batch_size": 10000}

        self.logger.info(f"Loading TSBS DevOps data from {data_dir}")

        for table_name, config in tsbs_tables.items():
            csv_path = data_dir / f"{table_name}.csv"
            if not csv_path.exists():
                self.logger.debug(f"No data file found for {table_name}, skipping")
                continue

            self.logger.info(f"Loading {table_name} from {csv_path}")
            table_start = time.perf_counter()

            try:
                count = 0
                for batch in self._iter_tsbs_batches(csv_path, config):
                    count += connection.write_table(
                        measurement=table_name,
                        table=batch,
                        tag_columns=config["tags"],
                        field_columns=config["fields"],
                        timestamp_column=config["timestamp"],
                        precision="ns",
                        batch_size=10000,
                        float_only_fields=self._FLOAT_ONLY_FIELDS,
                    )

                if count:
                    row_counts[table_name] = count
                    load_metadata["tables_loaded"].append(table_name)
                    self.logger.info(f"Loaded {count:,} rows into {table_name}")

            except Exception as e:
                self.logger.error(f"Failed to load {table_name}: {e}")
                row_counts[table_name] = 0

            table_time = time.perf_counter() - table_start
            total_time += table_time
            self.logger.debug(f"{table_name} load time: {table_time:.2f}s")

        load_metadata["total_rows"] = sum(row_counts.values())
        self.logger.info(f"Data loading complete: {load_metadata['total_rows']:,} total rows in {total_time:.2f}s")

        return row_counts, total_time, load_metadata

    @staticmethod
    def _tsbs_table_configs() -> dict[str, dict[str, Any]]:
        return {
            "tags": {
                "tags": ["hostname"],
                "fields": [
                    "region",
                    "datacenter",
                    "rack",
                    "os",
                    "arch",
                    "team",
                    "service",
                    "service_version",
                    "service_environment",
                ],
                "timestamp": None,
            },
            "cpu": {
                "tags": ["hostname"],
                "fields": [
                    "usage_user",
                    "usage_system",
                    "usage_idle",
                    "usage_nice",
                    "usage_iowait",
                    "usage_irq",
                    "usage_softirq",
                    "usage_steal",
                    "usage_guest",
                    "usage_guest_nice",
                ],
                "timestamp": "time",
            },
            "mem": {
                "tags": ["hostname"],
                "fields": [
                    "total",
                    "available",
                    "used",
                    "free",
                    "cached",
                    "buffered",
                    "used_percent",
                    "available_percent",
                ],
                "timestamp": "time",
            },
            "disk": {
                "tags": ["hostname", "device"],
                "fields": [
                    "reads_completed",
                    "reads_merged",
                    "sectors_read",
                    "read_time_ms",
                    "writes_completed",
                    "writes_merged",
                    "sectors_written",
                    "write_time_ms",
                    "io_in_progress",
                    "io_time_ms",
                    "weighted_io_time_ms",
                ],
                "timestamp": "time",
            },
            "net": {
                "tags": ["hostname", "interface"],
                "fields": [
                    "bytes_recv",
                    "bytes_sent",
                    "packets_recv",
                    "packets_sent",
                    "err_in",
                    "err_out",
                    "drop_in",
                    "drop_out",
                ],
                "timestamp": "time",
            },
        }

    _FLOAT_ONLY_FIELDS = frozenset(
        {
            "usage_user",
            "usage_system",
            "usage_idle",
            "usage_nice",
            "usage_iowait",
            "usage_irq",
            "usage_softirq",
            "usage_steal",
            "usage_guest",
            "usage_guest_nice",
            "used_percent",
            "available_percent",
        }
    )

    def _iter_tsbs_batches(self, csv_path: Path, config: dict[str, Any]) -> Iterator[Any]:
        import pyarrow as pa
        import pyarrow.csv as pacsv

        wanted = [config["timestamp"], *config["tags"], *config["fields"]]
        convert = pacsv.ConvertOptions(strings_can_be_null=False, null_values=[])
        with pacsv.open_csv(csv_path, convert_options=convert) as probe:
            present = [name for name in wanted if name and name in probe.schema.names]
        convert = pacsv.ConvertOptions(
            include_columns=present,
            column_types={name: pa.string() for name in present},
            strings_can_be_null=False,
            null_values=[],
        )
        with pacsv.open_csv(csv_path, convert_options=convert) as reader:
            yield from reader

    def configure_for_benchmark(self, connection: Any, benchmark_type: str) -> None:
        self.logger.debug(f"Configuring InfluxDB for benchmark type: {benchmark_type}")

    def apply_platform_optimizations(self, platform_config: PlatformOptimizationConfiguration, connection: Any) -> None:
        self.logger.debug("InfluxDB manages its own optimizations - skipping explicit optimization")

    def apply_constraint_configuration(
        self,
        primary_key_config: PrimaryKeyConfiguration,
        foreign_key_config: ForeignKeyConfiguration,
        connection: Any,
    ) -> None:
        self.logger.debug("InfluxDB is a time series database - relational constraints not applicable")

    def execute_query(
        self,
        connection: Any,
        query: str,
        query_id: str,
        iteration: int = 1,
        stream_id: int | None = None,
    ) -> tuple[float, int, dict[str, Any] | None]:
        import time

        start_time = time.perf_counter()

        try:
            result = connection.execute(query)
            row_count = len(result) if result else 0
        except Exception as e:
            self.logger.error(f"Query {query_id} failed: {e}")
            raise

        execution_time = time.perf_counter() - start_time

        return execution_time, row_count, None


__all__ = ["InfluxDBAdapter"]
