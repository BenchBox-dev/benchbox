# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ DI (TPC-DI) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-DI specification.

# Licensed under the MIT License. See LICENSE file in the project root for details.

import logging
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class ErrorSeverity(Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ErrorCategory(Enum):
    TRANSIENT = "TRANSIENT"
    PERMANENT = "PERMANENT"
    DATA_QUALITY = "DATA_QUALITY"
    SYSTEM = "SYSTEM"
    CONFIGURATION = "CONFIGURATION"
    BUSINESS_RULE = "BUSINESS_RULE"


class RetryStrategy(Enum):
    IMMEDIATE = "IMMEDIATE"
    EXPONENTIAL_BACKOFF = "EXPONENTIAL_BACKOFF"
    LINEAR_BACKOFF = "LINEAR_BACKOFF"
    FIXED_DELAY = "FIXED_DELAY"
    NO_RETRY = "NO_RETRY"


@dataclass
class ErrorRecord:
    error_id: str
    timestamp: datetime
    error_message: str
    error_type: str
    severity: ErrorSeverity
    category: ErrorCategory

    batch_id: Optional[int] = None
    table_name: Optional[str] = None
    operation_name: Optional[str] = None
    record_context: Optional[dict[str, Any]] = None

    exception_type: Optional[str] = None
    stack_trace: Optional[str] = None
    error_code: Optional[str] = None

    retry_count: int = 0
    max_retries: int = 3
    retry_strategy: RetryStrategy = RetryStrategy.EXPONENTIAL_BACKOFF
    can_retry: bool = True
    recovery_action: Optional[str] = None

    is_resolved: bool = False
    resolution_timestamp: Optional[datetime] = None
    resolution_notes: Optional[str] = None


@dataclass
class RecoveryCheckpoint:
    checkpoint_id: str
    timestamp: datetime
    batch_id: int
    operation_name: str
    checkpoint_type: str

    records_processed: int = 0
    tables_completed: list[str] = field(default_factory=list)
    current_table: Optional[str] = None
    current_operation: Optional[str] = None

    recovery_data: dict[str, Any] = field(default_factory=dict)
    can_resume_from: bool = True
    dependencies: list[str] = field(default_factory=list)


@dataclass
class RetryPolicy:
    max_attempts: int = 3
    strategy: RetryStrategy = RetryStrategy.EXPONENTIAL_BACKOFF
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 300.0
    backoff_multiplier: float = 2.0
    jitter: bool = True

    retryable_errors: list[str] = field(default_factory=list)
    non_retryable_errors: list[str] = field(default_factory=list)
    retry_on_timeout: bool = True
    retry_on_connection_error: bool = True


class ErrorClassifier:
    def __init__(self):
        self.transient_patterns = [
            "timeout",
            "connection reset",
            "temporary unavailable",
            "lock timeout",
            "deadlock",
            "resource busy",
        ]

        self.permanent_patterns = [
            "syntax error",
            "invalid column",
            "table not found",
            "constraint violation",
            "data type mismatch",
            "invalid format",
        ]

        self.data_quality_patterns = [
            "duplicate key",
            "foreign key violation",
            "check constraint",
            "null value",
            "invalid range",
            "business rule",
        ]

        self.system_patterns = [
            "disk full",
            "out of memory",
            "permission denied",
            "service unavailable",
            "database error",
        ]

    def classify_error(self, error_message: str, exception_type: str = "") -> tuple[ErrorCategory, ErrorSeverity]:

        error_lower = error_message.lower()
        exception_lower = exception_type.lower()

        if any(pattern in error_lower for pattern in self.transient_patterns):
            return ErrorCategory.TRANSIENT, ErrorSeverity.MEDIUM

        if any(pattern in error_lower for pattern in self.permanent_patterns):
            return ErrorCategory.PERMANENT, ErrorSeverity.HIGH

        if any(pattern in error_lower for pattern in self.data_quality_patterns):
            return ErrorCategory.DATA_QUALITY, ErrorSeverity.MEDIUM

        if any(pattern in error_lower for pattern in self.system_patterns):
            return ErrorCategory.SYSTEM, ErrorSeverity.CRITICAL

        if "timeout" in exception_lower:
            return ErrorCategory.TRANSIENT, ErrorSeverity.MEDIUM
        elif "connection" in exception_lower:
            return ErrorCategory.TRANSIENT, ErrorSeverity.HIGH
        elif "sql" in exception_lower or "database" in exception_lower:
            return ErrorCategory.SYSTEM, ErrorSeverity.HIGH

        return ErrorCategory.SYSTEM, ErrorSeverity.MEDIUM


class RetryManager:
    def __init__(self, policy: Optional[RetryPolicy] = None):
        self.policy = policy or RetryPolicy()
        self.classifier = ErrorClassifier()

    def should_retry(self, error_record: ErrorRecord) -> bool:

        if error_record.retry_count >= self.policy.max_attempts:
            return False

        if not error_record.can_retry:
            return False

        if error_record.category == ErrorCategory.PERMANENT:
            return False
        elif error_record.category == ErrorCategory.TRANSIENT:
            return True
        elif error_record.category == ErrorCategory.CONFIGURATION:
            return False

        error_lower = error_record.error_message.lower()

        for non_retryable in self.policy.non_retryable_errors:
            if non_retryable.lower() in error_lower:
                return False

        for retryable in self.policy.retryable_errors:
            if retryable.lower() in error_lower:
                return True

        return error_record.category in [ErrorCategory.TRANSIENT, ErrorCategory.SYSTEM]

    def calculate_delay(self, retry_count: int) -> float:

        if self.policy.strategy == RetryStrategy.IMMEDIATE:
            delay = 0.0
        elif self.policy.strategy == RetryStrategy.FIXED_DELAY:
            delay = self.policy.base_delay_seconds
        elif self.policy.strategy == RetryStrategy.LINEAR_BACKOFF:
            delay = self.policy.base_delay_seconds * (retry_count + 1)
        elif self.policy.strategy == RetryStrategy.EXPONENTIAL_BACKOFF:
            delay = self.policy.base_delay_seconds * (self.policy.backoff_multiplier**retry_count)
        else:
            delay = self.policy.base_delay_seconds

        delay = min(delay, self.policy.max_delay_seconds)

        if self.policy.jitter:
            import random

            jitter_amount = delay * 0.1
            delay += random.uniform(-jitter_amount, jitter_amount)

        return max(0.0, delay)


class ErrorRecoveryManager:
    def __init__(self, connection: Any, dialect: str = "duckdb"):
        self.connection = connection
        self.dialect = dialect

        self.error_log: list[ErrorRecord] = []
        self.error_counts_by_category: dict[ErrorCategory, int] = {}
        self.error_lock = threading.RLock()

        self.checkpoints: dict[str, RecoveryCheckpoint] = {}
        self.active_operations: dict[str, dict[str, Any]] = {}

        self.classifier = ErrorClassifier()
        self.retry_manager = RetryManager()

        self.dead_letter_queue: list[ErrorRecord] = []

    def classify_error(self, error_message: str, exception_type: str = "") -> tuple[ErrorCategory, ErrorSeverity]:
        return self.classifier.classify_error(error_message, exception_type)

    def handle_error(
        self,
        error: Exception,
        operation_context: dict[str, Any],
        retry_policy: Optional[RetryPolicy] = None,
    ) -> tuple[bool, Optional[float]]:

        error_record = self._create_error_record(error, operation_context)

        self._log_error(error_record)

        retry_manager = RetryManager(retry_policy) if retry_policy else self.retry_manager

        should_retry = retry_manager.should_retry(error_record)

        if should_retry:
            delay = retry_manager.calculate_delay(error_record.retry_count)
            error_record.retry_count += 1

            logger.warning(f"Error will be retried (attempt {error_record.retry_count}): {error_record.error_message}")
            return True, delay
        else:
            self.dead_letter_queue.append(error_record)
            error_record.can_retry = False

            logger.error(f"Error cannot be retried, moved to dead letter queue: {error_record.error_message}")
            return False, None

    def _create_error_record(self, error: Exception, context: dict[str, Any]) -> ErrorRecord:

        error_message = str(error)
        exception_type = type(error).__name__
        category, severity = self.classifier.classify_error(error_message, exception_type)

        error_record = ErrorRecord(
            error_id=f"ERR_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{id(error)}",
            timestamp=datetime.now(),
            error_message=error_message,
            error_type=exception_type,
            severity=severity,
            category=category,
            batch_id=context.get("batch_id"),
            table_name=context.get("table_name"),
            operation_name=context.get("operation_name"),
            record_context=context.get("record_context"),
            exception_type=exception_type,
            stack_trace=traceback.format_exc(),
            error_code=getattr(error, "code", None),
        )

        return error_record

    def _log_error(self, error_record: ErrorRecord) -> None:

        with self.error_lock:
            self.error_log.append(error_record)

            category = error_record.category
            self.error_counts_by_category[category] = self.error_counts_by_category.get(category, 0) + 1

        log_message = (
            f"Error logged: {error_record.error_id} - "
            f"{error_record.category.value}/{error_record.severity.value} - "
            f"{error_record.error_message}"
        )

        if error_record.severity == ErrorSeverity.CRITICAL:
            logger.critical(log_message)
        elif error_record.severity == ErrorSeverity.HIGH:
            logger.error(log_message)
        elif error_record.severity == ErrorSeverity.MEDIUM:
            logger.warning(log_message)
        else:
            logger.info(log_message)

    def create_checkpoint(
        self,
        operation_name: str,
        batch_id: int,
        checkpoint_type: str = "SAVEPOINT",
        recovery_data: Optional[dict[str, Any]] = None,
    ) -> str:

        checkpoint_id = f"CP_{batch_id}_{operation_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        checkpoint = RecoveryCheckpoint(
            checkpoint_id=checkpoint_id,
            timestamp=datetime.now(),
            batch_id=batch_id,
            operation_name=operation_name,
            checkpoint_type=checkpoint_type,
            recovery_data=recovery_data or {},
        )

        self.checkpoints[checkpoint_id] = checkpoint

        logger.debug(f"Created checkpoint: {checkpoint_id} for batch {batch_id}")
        return checkpoint_id

    def restore_from_checkpoint(self, checkpoint_id: str) -> dict[str, Any]:

        if checkpoint_id not in self.checkpoints:
            raise ValueError(f"Checkpoint not found: {checkpoint_id}")

        checkpoint = self.checkpoints[checkpoint_id]

        if not checkpoint.can_resume_from:
            raise ValueError(f"Checkpoint cannot be resumed from: {checkpoint_id}")

        logger.info(f"Restoring from checkpoint: {checkpoint_id}")

        recovery_state = {
            "batch_id": checkpoint.batch_id,
            "operation_name": checkpoint.operation_name,
            "records_processed": checkpoint.records_processed,
            "tables_completed": checkpoint.tables_completed.copy(),
            "current_table": checkpoint.current_table,
            "current_operation": checkpoint.current_operation,
            "recovery_data": checkpoint.recovery_data.copy(),
            "checkpoint_timestamp": checkpoint.timestamp,
        }

        return recovery_state

    def execute_with_recovery(
        self,
        operation_func: Callable,
        operation_context: dict[str, Any],
        retry_policy: Optional[RetryPolicy] = None,
        max_attempts: Optional[int] = None,
    ) -> Any:

        operation_name = operation_context.get("operation_name", "unknown_operation")
        batch_id = operation_context.get("batch_id", 0)

        checkpoint_id = self.create_checkpoint(operation_name, batch_id, "SAVEPOINT")

        attempts = 0
        max_attempts = max_attempts or (retry_policy.max_attempts if retry_policy else 3)

        while attempts < max_attempts:
            try:
                logger.debug(f"Executing operation {operation_name} (attempt {attempts + 1})")

                result = operation_func()

                self.create_checkpoint(operation_name, batch_id, "COMMIT")

                logger.debug(f"Operation {operation_name} completed successfully")
                return result

            except Exception as e:
                attempts += 1

                should_retry, delay = self.handle_error(e, operation_context, retry_policy)

                if should_retry and attempts < max_attempts:
                    if delay and delay > 0:
                        logger.info(f"Waiting {delay:.1f}s before retry...")
                        time.sleep(delay)

                    try:
                        recovery_state = self.restore_from_checkpoint(checkpoint_id)
                        operation_context.update(recovery_state)
                    except Exception as recovery_error:
                        logger.warning(f"Could not restore from checkpoint: {recovery_error}")

                    continue
                else:
                    self.create_checkpoint(
                        operation_name,
                        batch_id,
                        "ROLLBACK",
                        {"final_error": str(e), "attempts_made": attempts},
                    )

                    logger.error(f"Operation {operation_name} failed after {attempts} attempts")
                    raise

        raise RuntimeError(f"Operation {operation_name} exceeded maximum attempts")

    def get_error_statistics(self) -> dict[str, Any]:

        with self.error_lock:
            total_errors = len(self.error_log)
            if total_errors == 0:
                return {"message": "No errors recorded"}

            stats = {
                "total_errors": total_errors,
                "errors_in_dead_letter_queue": len(self.dead_letter_queue),
                "errors_by_category": dict(self.error_counts_by_category),
                "errors_by_severity": {},
                "recent_error_rate": 0.0,
                "most_common_errors": [],
                "error_trend": "STABLE",
            }

            for error in self.error_log:
                severity = error.severity.value
                stats["errors_by_severity"][severity] = stats["errors_by_severity"].get(severity, 0) + 1

            recent_threshold = datetime.now() - timedelta(hours=1)
            recent_errors = len([e for e in self.error_log if e.timestamp > recent_threshold])
            stats["recent_error_rate"] = recent_errors

            error_message_counts = {}
            for error in self.error_log[-100:]:
                msg = error.error_message[:100]
                error_message_counts[msg] = error_message_counts.get(msg, 0) + 1

            stats["most_common_errors"] = sorted(error_message_counts.items(), key=lambda x: x[1], reverse=True)[:5]

            if len(self.error_log) >= 10:
                recent_errors = self.error_log[-10:]
                older_errors = self.error_log[-20:-10] if len(self.error_log) >= 20 else []

                if older_errors:
                    recent_rate = len(recent_errors) / 10.0
                    older_rate = len(older_errors) / 10.0

                    if recent_rate > older_rate * 1.2:
                        stats["error_trend"] = "INCREASING"
                    elif recent_rate < older_rate * 0.8:
                        stats["error_trend"] = "DECREASING"

            return stats

    def get_recovery_statistics(self) -> dict[str, Any]:

        stats = {
            "total_checkpoints": len(self.checkpoints),
            "checkpoints_by_type": {},
            "checkpoints_by_operation": {},
            "active_operations": len(self.active_operations),
            "successful_recoveries": 0,
            "failed_recoveries": 0,
        }

        for checkpoint in self.checkpoints.values():
            cp_type = checkpoint.checkpoint_type
            operation = checkpoint.operation_name

            stats["checkpoints_by_type"][cp_type] = stats["checkpoints_by_type"].get(cp_type, 0) + 1
            stats["checkpoints_by_operation"][operation] = stats["checkpoints_by_operation"].get(operation, 0) + 1

        return stats

    def cleanup_old_errors(self, retention_hours: int = 24) -> int:

        cutoff_time = datetime.now() - timedelta(hours=retention_hours)

        with self.error_lock:
            initial_count = len(self.error_log)
            self.error_log = [error for error in self.error_log if error.timestamp > cutoff_time]

            self.dead_letter_queue = [error for error in self.dead_letter_queue if error.timestamp > cutoff_time]

            cleaned_count = initial_count - len(self.error_log)

        if cleaned_count > 0:
            logger.info(f"Cleaned up {cleaned_count} old error records (retention: {retention_hours}h)")

        return cleaned_count

    def export_error_report(self, output_path: str, include_stack_traces: bool = False) -> bool:

        try:
            import json

            with self.error_lock:
                error_data = []
                for error in self.error_log:
                    error_dict = {
                        "error_id": error.error_id,
                        "timestamp": error.timestamp.isoformat(),
                        "error_message": error.error_message,
                        "error_type": error.error_type,
                        "severity": error.severity.value,
                        "category": error.category.value,
                        "batch_id": error.batch_id,
                        "table_name": error.table_name,
                        "operation_name": error.operation_name,
                        "retry_count": error.retry_count,
                        "is_resolved": error.is_resolved,
                        "resolution_timestamp": error.resolution_timestamp.isoformat()
                        if error.resolution_timestamp
                        else None,
                        "resolution_notes": error.resolution_notes,
                    }

                    if include_stack_traces:
                        error_dict["stack_trace"] = error.stack_trace

                    error_data.append(error_dict)

                report = {
                    "export_timestamp": datetime.now().isoformat(),
                    "error_statistics": self.get_error_statistics(),
                    "recovery_statistics": self.get_recovery_statistics(),
                    "error_records": error_data,
                    "dead_letter_queue_size": len(self.dead_letter_queue),
                }

                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(report, f, indent=2)

                logger.info(f"Error report exported to {output_path} ({len(error_data)} records)")
                return True

        except Exception as e:
            logger.error(f"Failed to export error report: {str(e)}")
            return False
