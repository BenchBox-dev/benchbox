# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ DI (TPC-DI) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-DI specification.

# Licensed under the MIT License. See LICENSE file in the project root for details.

import logging
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

from benchbox.utils.clock import elapsed_seconds, mono_time

logger = logging.getLogger(__name__)


@dataclass
class ChangeRecord:
    table_name: str
    operation: str
    primary_key: dict[str, Any]
    changed_data: dict[str, Any]
    change_timestamp: datetime
    batch_id: int
    sequence_number: Optional[int] = None
    source_system: Optional[str] = None
    change_reason: Optional[str] = None


@dataclass
class IncrementalBatch:
    batch_id: int
    batch_date: datetime
    source_system: str
    batch_type: str
    expected_record_count: Optional[int] = None
    actual_record_count: int = 0
    status: str = "PENDING"
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    error_message: Optional[str] = None

    changes_by_table: dict[str, int] = field(default_factory=dict)
    changes_by_operation: dict[str, int] = field(default_factory=dict)

    depends_on_batches: list[int] = field(default_factory=list)
    checkpoint_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class IncrementalLoadConfig:
    enable_change_data_capture: bool = True
    enable_cdc: Optional[bool] = None
    cdc_column_name: str = "LastModified"
    use_hash_based_detection: bool = True
    hash_columns: Optional[list[str]] = None

    batch_size: int = 10000
    max_batch_age_hours: int = 24
    enable_parallel_processing: bool = True
    max_parallel_tables: int = 4

    enable_watermarks: bool = True
    watermark_table: str = "ETL_Watermarks"
    watermark_table_name: Optional[str] = None
    watermark_lag_minutes: int = 5
    enable_deduplication: bool = True

    max_retry_attempts: int = 3
    retry_delay_minutes: int = 15
    enable_dead_letter_queue: bool = True

    enable_index_hints: bool = True
    optimize_for_bulk_operations: bool = True
    use_staging_tables: bool = True
    staging_table_prefix: str = "STG_"

    def __post_init__(self) -> None:
        if self.enable_cdc is not None:
            self.enable_change_data_capture = bool(self.enable_cdc)

        if self.watermark_table_name:
            self.watermark_table = self.watermark_table_name


class ChangeDetector(ABC):
    @abstractmethod
    def detect_changes(
        self, table_name: str, last_processed_timestamp: datetime, batch_id: int
    ) -> Iterator[ChangeRecord]:
        pass


class TimestampBasedChangeDetector(ChangeDetector):
    def __init__(self, connection: Any, config: IncrementalLoadConfig):
        self.connection = connection
        self.config = config

    def detect_changes(
        self, table_name: str, last_processed_timestamp: datetime, batch_id: int
    ) -> Iterator[ChangeRecord]:

        logger.debug(f"Detecting timestamp-based changes in {table_name} since {last_processed_timestamp}")

        query = f"""
        SELECT *, '{table_name}' as source_table
        FROM {table_name}
        WHERE {self.config.cdc_column_name} > ?
        ORDER BY {self.config.cdc_column_name}
        """

        try:
            cursor = self.connection.execute(query, (last_processed_timestamp,))
            columns = [desc[0] for desc in cursor.description]

            for row_data in cursor.fetchall():
                row = dict(zip(columns, row_data))

                primary_key = self._extract_primary_key(row, table_name)

                yield ChangeRecord(
                    table_name=table_name,
                    operation="INSERT",
                    primary_key=primary_key,
                    changed_data=row,
                    change_timestamp=row.get(self.config.cdc_column_name, datetime.now()),
                    batch_id=batch_id,
                    source_system="TPC-DI",
                )

        except Exception as e:
            logger.error(f"Error detecting changes in {table_name}: {str(e)}")
            raise

    def _extract_primary_key(self, row: dict[str, Any], table_name: str) -> dict[str, Any]:

        pk_patterns = {
            "DimCustomer": ["CustomerID"],
            "DimAccount": ["AccountID"],
            "DimSecurity": ["Symbol"],
            "DimBroker": ["BrokerID"],
            "FactTrade": ["TradeID"],
        }

        pk_columns = pk_patterns.get(table_name, ["ID"])

        primary_key = {}
        for col in pk_columns:
            if col in row:
                primary_key[col] = row[col]

        return primary_key


class HashBasedChangeDetector(ChangeDetector):
    def __init__(self, connection: Any, config: IncrementalLoadConfig):
        self.connection = connection
        self.config = config

    def detect_changes(
        self, table_name: str, last_processed_timestamp: datetime, batch_id: int
    ) -> Iterator[ChangeRecord]:

        logger.debug(f"Detecting hash-based changes in {table_name}")

        yield ChangeRecord(
            table_name=table_name,
            operation="UPDATE",
            primary_key={"ID": 1},
            changed_data={"column": "value"},
            change_timestamp=datetime.now(),
            batch_id=batch_id,
            source_system="TPC-DI",
        )


class IncrementalDataLoader:
    def __init__(
        self,
        connection: Any,
        dialect: str = "duckdb",
        config: Optional[IncrementalLoadConfig] = None,
    ):
        self.connection = connection
        self.dialect = dialect
        self.config = config or IncrementalLoadConfig()

        self.change_detectors: dict[str, ChangeDetector] = {}
        self._initialize_change_detectors()

        self.active_batches: dict[int, IncrementalBatch] = {}
        self.batch_history: list[IncrementalBatch] = []

        self.table_watermarks: dict[str, datetime] = {}
        self._load_existing_watermarks()

    def _initialize_change_detectors(self) -> None:

        if self.config.enable_change_data_capture:
            self.change_detectors["timestamp"] = TimestampBasedChangeDetector(self.connection, self.config)

        if self.config.use_hash_based_detection:
            self.change_detectors["hash"] = HashBasedChangeDetector(self.connection, self.config)

        logger.debug(f"Initialized {len(self.change_detectors)} change detectors")

    def _load_existing_watermarks(self) -> None:

        if not self.config.enable_watermarks:
            return

        try:
            query = f"""
            SELECT table_name, last_processed_timestamp
            FROM {self.config.watermark_table}
            WHERE is_active = 1
            """

            cursor = self.connection.execute(query)
            for table_name, timestamp in cursor.fetchall():
                self.table_watermarks[table_name] = timestamp

            logger.debug(f"Loaded watermarks for {len(self.table_watermarks)} tables")

        except Exception as e:
            logger.warning(f"Could not load existing watermarks: {str(e)}")
            self.table_watermarks = {}

    def create_incremental_batch(
        self,
        batch_date: datetime,
        source_system: str = "TPC-DI",
        batch_type: str = "INCREMENTAL",
        tables: Optional[list[str]] = None,
    ) -> IncrementalBatch:

        batch_id = len(self.batch_history) + 1000

        batch = IncrementalBatch(
            batch_id=batch_id,
            batch_date=batch_date,
            source_system=source_system,
            batch_type=batch_type,
        )

        self.active_batches[batch_id] = batch
        logger.info(f"Created incremental batch {batch_id} for {batch_date.date()}")

        return batch

    def process_incremental_batch(self, batch: IncrementalBatch, tables: Optional[list[str]] = None) -> dict[str, Any]:

        logger.info(f"Processing incremental batch {batch.batch_id}")
        batch.status = "PROCESSING"
        batch.start_time = datetime.now()

        processing_stats = {
            "batch_id": batch.batch_id,
            "tables_processed": 0,
            "total_changes_detected": 0,
            "changes_by_table": {},
            "changes_by_operation": {"INSERT": 0, "UPDATE": 0, "DELETE": 0},
            "processing_time": 0.0,
            "success": False,
            "error_message": None,
        }

        try:
            if tables is None:
                tables = self._get_incremental_tables()

            for table_name in tables:
                table_stats = self._process_table_incremental_changes(table_name, batch)

                processing_stats["tables_processed"] += 1
                processing_stats["changes_by_table"][table_name] = table_stats["changes_detected"]
                processing_stats["total_changes_detected"] += table_stats["changes_detected"]

                for operation, count in table_stats["operations"].items():
                    processing_stats["changes_by_operation"][operation] += count

                batch.changes_by_table[table_name] = table_stats["changes_detected"]
                batch.actual_record_count += table_stats["changes_detected"]

            if self.config.enable_watermarks:
                self._update_watermarks(tables, batch)

            batch.status = "COMPLETED"
            batch.end_time = datetime.now()
            processing_stats["processing_time"] = (batch.end_time - batch.start_time).total_seconds()
            processing_stats["success"] = True

            self.batch_history.append(batch)
            del self.active_batches[batch.batch_id]

            logger.info(
                f"Incremental batch {batch.batch_id} completed successfully: "
                f"{processing_stats['total_changes_detected']} changes processed "
                f"in {processing_stats['processing_time']:.2f}s"
            )

            return processing_stats

        except Exception as e:
            error_msg = f"Incremental batch {batch.batch_id} failed: {str(e)}"
            logger.error(error_msg, exc_info=True)

            batch.status = "FAILED"
            batch.end_time = datetime.now()
            batch.error_message = error_msg
            processing_stats["error_message"] = error_msg
            processing_stats["success"] = False

            return processing_stats

    def _get_incremental_tables(self) -> list[str]:

        return [
            "DimCustomer",
            "DimAccount",
            "DimSecurity",
            "FactTrade",
            "FactMarketHistory",
            "FactHoldings",
        ]

    def _process_table_incremental_changes(self, table_name: str, batch: IncrementalBatch) -> dict[str, Any]:

        logger.debug(f"Processing incremental changes for {table_name}")

        table_stats = {
            "table_name": table_name,
            "changes_detected": 0,
            "operations": {"INSERT": 0, "UPDATE": 0, "DELETE": 0},
            "processing_time": 0.0,
            "success": False,
        }

        start_time = mono_time()

        try:
            last_processed = self.table_watermarks.get(
                table_name,
                datetime.now() - timedelta(days=1),
            )

            last_processed -= timedelta(minutes=self.config.watermark_lag_minutes)

            detector = self.change_detectors.get("timestamp")
            if not detector:
                logger.warning(f"No change detector available for {table_name}")
                return table_stats

            changes_processed = 0
            for change_record in detector.detect_changes(table_name, last_processed, batch.batch_id):
                self._apply_change_record(change_record)

                changes_processed += 1
                table_stats["operations"][change_record.operation] += 1

                if changes_processed % self.config.batch_size == 0:
                    logger.debug(f"Processed {changes_processed} changes for {table_name}")

            table_stats["changes_detected"] = changes_processed
            table_stats["processing_time"] = elapsed_seconds(start_time)
            table_stats["success"] = True

            logger.debug(
                f"Completed processing {table_name}: {changes_processed} changes in {table_stats['processing_time']:.2f}s"
            )

            return table_stats

        except Exception as e:
            error_msg = f"Failed to process incremental changes for {table_name}: {str(e)}"
            logger.error(error_msg)
            table_stats["error_message"] = error_msg
            table_stats["success"] = False
            return table_stats

    def _apply_change_record(self, change_record: ChangeRecord) -> None:

        logger.debug(
            f"Applying {change_record.operation} to {change_record.table_name} for key {change_record.primary_key}"
        )

    def _update_watermarks(self, tables: list[str], batch: IncrementalBatch) -> None:

        current_timestamp = batch.end_time or datetime.now()

        for table_name in tables:
            self.table_watermarks[table_name] = current_timestamp

            try:
                upsert_query = f"""
                INSERT OR REPLACE INTO {self.config.watermark_table}
                (table_name, last_processed_timestamp, batch_id, updated_timestamp, is_active)
                VALUES (?, ?, ?, ?, 1)
                """

                self.connection.execute(
                    upsert_query,
                    (table_name, current_timestamp, batch.batch_id, datetime.now()),
                )

                logger.debug(f"Updated watermark for {table_name} to {current_timestamp}")

            except Exception as e:
                logger.warning(f"Failed to update watermark for {table_name}: {str(e)}")

    def get_incremental_statistics(self) -> dict[str, Any]:

        stats = {
            "total_batches_processed": len(self.batch_history),
            "active_batches": len(self.active_batches),
            "successful_batches": len([b for b in self.batch_history if b.status == "COMPLETED"]),
            "failed_batches": len([b for b in self.batch_history if b.status == "FAILED"]),
            "total_records_processed": sum(b.actual_record_count for b in self.batch_history),
            "watermarked_tables": len(self.table_watermarks),
            "change_detectors_available": len(self.change_detectors),
        }

        completed_batches = [b for b in self.batch_history if b.status == "COMPLETED" and b.start_time and b.end_time]
        if completed_batches:
            avg_processing_time = sum((b.end_time - b.start_time).total_seconds() for b in completed_batches) / len(
                completed_batches
            )
            stats["average_processing_time_seconds"] = avg_processing_time

        table_stats = {}
        for batch in self.batch_history:
            for table_name, change_count in batch.changes_by_table.items():
                if table_name not in table_stats:
                    table_stats[table_name] = {
                        "total_changes": 0,
                        "batches_processed": 0,
                    }
                table_stats[table_name]["total_changes"] += change_count
                table_stats[table_name]["batches_processed"] += 1

        stats["by_table"] = table_stats

        return stats

    def detect_changes_simple(self, table_name: str, last_watermark: Any, batch_id: int) -> list[dict[str, Any]]:
        if table_name in self.change_detectors:
            detector = self.change_detectors[table_name]
            try:
                return [asdict(record) for record in detector.detect_changes(table_name, last_watermark, batch_id)]
            except Exception:
                return []
        else:
            return []

    def get_batch_status(self, batch_id: int) -> Optional[dict[str, Any]]:

        if batch_id in self.active_batches:
            batch = self.active_batches[batch_id]
            return {
                "batch_id": batch.batch_id,
                "status": batch.status,
                "batch_date": batch.batch_date,
                "start_time": batch.start_time,
                "records_processed": batch.actual_record_count,
                "is_active": True,
            }

        for batch in self.batch_history:
            if batch.batch_id == batch_id:
                return {
                    "batch_id": batch.batch_id,
                    "status": batch.status,
                    "batch_date": batch.batch_date,
                    "start_time": batch.start_time,
                    "end_time": batch.end_time,
                    "processing_time": (batch.end_time - batch.start_time).total_seconds()
                    if batch.end_time and batch.start_time
                    else None,
                    "records_processed": batch.actual_record_count,
                    "changes_by_table": batch.changes_by_table,
                    "is_active": False,
                    "error_message": batch.error_message,
                }

        return None

    def cleanup_old_batches(self, retention_days: int = 30) -> int:

        cutoff_date = datetime.now() - timedelta(days=retention_days)

        initial_count = len(self.batch_history)
        self.batch_history = [batch for batch in self.batch_history if batch.batch_date > cutoff_date]

        cleaned_count = initial_count - len(self.batch_history)
        if cleaned_count > 0:
            logger.info(f"Cleaned up {cleaned_count} old batches (retention: {retention_days} days)")

        return cleaned_count

    def _get_last_watermark(self, table_name: str) -> datetime:
        return self.table_watermarks.get(table_name, datetime(1900, 1, 1))

    def _detect_table_changes(self, table_name: str, last_watermark: datetime, batch_id: int) -> list[ChangeRecord]:
        changes = []

        for detector_name, detector in self.change_detectors.items():
            try:
                table_changes = list(detector.detect_changes(table_name, last_watermark, batch_id=batch_id))
                changes.extend(table_changes)
                logger.debug(f"Detector {detector_name} found {len(table_changes)} changes for {table_name}")
            except Exception as e:
                logger.warning(f"Change detection failed for {table_name} with {detector_name}: {str(e)}")

        return changes

    def _load_data_batch(self, changes: list[ChangeRecord], table_name: str) -> dict[str, Any]:
        if not changes:
            return {
                "success": True,
                "records_loaded": 0,
                "insert_count": 0,
                "update_count": 0,
                "delete_count": 0,
            }

        insert_count = 0
        update_count = 0
        delete_count = 0

        try:
            for change in changes:
                if change.operation == "INSERT":
                    insert_count += 1
                elif change.operation == "UPDATE":
                    update_count += 1
                elif change.operation == "DELETE":
                    delete_count += 1

            logger.info(
                f"Loaded {len(changes)} changes for {table_name}: "
                f"{insert_count} inserts, {update_count} updates, {delete_count} deletes"
            )

            return {
                "success": True,
                "records_loaded": len(changes),
                "insert_count": insert_count,
                "update_count": update_count,
                "delete_count": delete_count,
            }

        except Exception as e:
            logger.error(f"Failed to load batch for {table_name}: {str(e)}")
            return {
                "success": False,
                "records_loaded": 0,
                "insert_count": 0,
                "update_count": 0,
                "delete_count": 0,
                "error": str(e),
            }

    def _deduplicate_data(self, data: Any, key_columns: list[str]) -> Any:
        if not key_columns:
            return data

        try:
            import pandas as pd

            if isinstance(data, pd.DataFrame):
                if data.empty:
                    return data
                deduplicated = data.drop_duplicates(subset=key_columns, keep="first")
                if len(deduplicated) < len(data):
                    logger.info(
                        f"Deduplicated {len(data)} records to {len(deduplicated)} (removed {len(data) - len(deduplicated)} duplicates)"
                    )
                return deduplicated
        except ImportError:
            pass

        if not data:
            return data

        seen_keys = set()
        deduplicated = []

        for record in data:
            key_values = tuple(record.get(col, None) for col in key_columns)

            if key_values not in seen_keys:
                seen_keys.add(key_values)
                deduplicated.append(record)

        if len(deduplicated) < len(data):
            logger.info(
                f"Deduplicated {len(data)} records to {len(deduplicated)} (removed {len(data) - len(deduplicated)} duplicates)"
            )

        return deduplicated

    def load_incremental_batch(self, table_name: str, data: Any, batch_id: int) -> dict[str, Any]:
        results = {
            "batch_id": batch_id,
            "table_name": table_name,
            "records_loaded": 0,
            "success": True,
            "errors": [],
        }

        try:
            normalized_changes: list[ChangeRecord]
            if isinstance(data, list) and all(isinstance(item, ChangeRecord) for item in data):
                normalized_changes = data
            elif hasattr(data, "to_dict"):
                records = data.to_dict(orient="records")
                normalized_changes = [
                    ChangeRecord(
                        table_name=table_name,
                        operation="INSERT",
                        primary_key={},
                        changed_data=record,
                        change_timestamp=datetime.now(),
                        batch_id=batch_id,
                    )
                    for record in records
                ]
            elif isinstance(data, list):
                normalized_changes = [
                    ChangeRecord(
                        table_name=table_name,
                        operation="INSERT",
                        primary_key={},
                        changed_data=record if isinstance(record, dict) else {"value": record},
                        change_timestamp=datetime.now(),
                        batch_id=batch_id,
                    )
                    for record in data
                ]
            elif isinstance(data, dict):
                normalized_changes = [
                    ChangeRecord(
                        table_name=table_name,
                        operation="INSERT",
                        primary_key={},
                        changed_data=data,
                        change_timestamp=datetime.now(),
                        batch_id=batch_id,
                    )
                ]
            else:
                normalized_changes = []

            batch_result = self._load_data_batch(normalized_changes, table_name)

            if isinstance(batch_result, dict) and "success" in batch_result:
                results["success"] = batch_result["success"]
                results["records_loaded"] = batch_result.get("records_loaded", 0)
            else:
                record_count = len(normalized_changes)
                results["records_loaded"] = record_count

                self.table_watermarks[table_name] = datetime.now()

        except Exception as e:
            results["success"] = False
            results["errors"].append(str(e))

        return results

    def get_watermark(self, table_name: str) -> Optional[datetime]:
        return self._get_last_watermark(table_name)

    def detect_changes(self, table_name: str, last_watermark: datetime, batch_id: int) -> list[ChangeRecord]:
        return list(self._detect_table_changes(table_name, last_watermark, batch_id))
