# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import json
import tempfile
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from benchbox.core.tpcdi.etl.error_recovery import (
    ErrorCategory,
    ErrorClassifier,
    ErrorRecord,
    ErrorRecoveryManager,
    ErrorSeverity,
    RecoveryCheckpoint,
    RetryManager,
    RetryPolicy,
    RetryStrategy,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestErrorClassifier:
    @pytest.fixture
    def classifier(self) -> ErrorClassifier:
        return ErrorClassifier()

    def test_classify_transient_timeout(self, classifier: ErrorClassifier) -> None:
        category, severity = classifier.classify_error("connection timeout occurred")
        assert category == ErrorCategory.TRANSIENT
        assert severity == ErrorSeverity.MEDIUM

    def test_classify_transient_deadlock(self, classifier: ErrorClassifier) -> None:
        category, _ = classifier.classify_error("deadlock detected between transactions")
        assert category == ErrorCategory.TRANSIENT

    def test_classify_permanent_syntax_error(self, classifier: ErrorClassifier) -> None:
        category, severity = classifier.classify_error("syntax error near SELECT")
        assert category == ErrorCategory.PERMANENT
        assert severity == ErrorSeverity.HIGH

    def test_classify_permanent_invalid_column(self, classifier: ErrorClassifier) -> None:
        category, _ = classifier.classify_error("invalid column reference: foo")
        assert category == ErrorCategory.PERMANENT

    def test_classify_data_quality_duplicate_key(self, classifier: ErrorClassifier) -> None:
        category, severity = classifier.classify_error("duplicate key violation on table t")
        assert category == ErrorCategory.DATA_QUALITY
        assert severity == ErrorSeverity.MEDIUM

    def test_classify_data_quality_null_value(self, classifier: ErrorClassifier) -> None:
        category, _ = classifier.classify_error("null value in column violates constraint")
        assert category == ErrorCategory.DATA_QUALITY

    def test_classify_system_disk_full(self, classifier: ErrorClassifier) -> None:
        category, severity = classifier.classify_error("disk full error")
        assert category == ErrorCategory.SYSTEM
        assert severity == ErrorSeverity.CRITICAL

    def test_classify_system_out_of_memory(self, classifier: ErrorClassifier) -> None:
        category, severity = classifier.classify_error("out of memory")
        assert category == ErrorCategory.SYSTEM
        assert severity == ErrorSeverity.CRITICAL

    def test_classify_by_exception_type_timeout(self, classifier: ErrorClassifier) -> None:
        category, _ = classifier.classify_error("operation failed", exception_type="TimeoutError")
        assert category == ErrorCategory.TRANSIENT

    def test_classify_by_exception_type_connection(self, classifier: ErrorClassifier) -> None:
        category, severity = classifier.classify_error("failed", exception_type="ConnectionError")
        assert category == ErrorCategory.TRANSIENT
        assert severity == ErrorSeverity.HIGH

    def test_classify_by_exception_type_sql(self, classifier: ErrorClassifier) -> None:
        category, severity = classifier.classify_error("failed", exception_type="SQLError")
        assert category == ErrorCategory.SYSTEM
        assert severity == ErrorSeverity.HIGH

    def test_classify_default_unknown(self, classifier: ErrorClassifier) -> None:
        category, severity = classifier.classify_error("some unknown error", exception_type="")
        assert category == ErrorCategory.SYSTEM
        assert severity == ErrorSeverity.MEDIUM


class TestRetryManager:
    @pytest.fixture
    def default_manager(self) -> RetryManager:
        return RetryManager()

    def _make_error_record(
        self,
        category: ErrorCategory,
        retry_count: int = 0,
        can_retry: bool = True,
    ) -> ErrorRecord:
        return ErrorRecord(
            error_id="ERR_001",
            timestamp=datetime.now(),
            error_message="test error",
            error_type="Exception",
            severity=ErrorSeverity.MEDIUM,
            category=category,
            retry_count=retry_count,
            can_retry=can_retry,
        )

    def test_should_retry_transient_below_max(self, default_manager: RetryManager) -> None:
        record = self._make_error_record(ErrorCategory.TRANSIENT, retry_count=0)
        assert default_manager.should_retry(record) is True

    def test_should_not_retry_permanent(self, default_manager: RetryManager) -> None:
        record = self._make_error_record(ErrorCategory.PERMANENT)
        assert default_manager.should_retry(record) is False

    def test_should_not_retry_configuration(self, default_manager: RetryManager) -> None:
        record = self._make_error_record(ErrorCategory.CONFIGURATION)
        assert default_manager.should_retry(record) is False

    def test_should_not_retry_when_max_reached(self, default_manager: RetryManager) -> None:
        record = self._make_error_record(ErrorCategory.TRANSIENT, retry_count=3)
        assert default_manager.should_retry(record) is False

    def test_should_not_retry_when_can_retry_false(self, default_manager: RetryManager) -> None:
        record = self._make_error_record(ErrorCategory.TRANSIENT, can_retry=False)
        assert default_manager.should_retry(record) is False

    def test_should_retry_system_errors(self, default_manager: RetryManager) -> None:
        record = self._make_error_record(ErrorCategory.SYSTEM, retry_count=0)
        assert default_manager.should_retry(record) is True

    def test_calculate_delay_immediate(self) -> None:
        policy = RetryPolicy(strategy=RetryStrategy.IMMEDIATE, jitter=False)
        manager = RetryManager(policy)
        delay = manager.calculate_delay(0)
        assert delay == 0.0

    def test_calculate_delay_fixed(self) -> None:
        policy = RetryPolicy(strategy=RetryStrategy.FIXED_DELAY, base_delay_seconds=5.0, jitter=False)
        manager = RetryManager(policy)
        delay = manager.calculate_delay(0)
        assert delay == 5.0
        delay2 = manager.calculate_delay(2)
        assert delay2 == 5.0

    def test_calculate_delay_linear_backoff(self) -> None:
        policy = RetryPolicy(strategy=RetryStrategy.LINEAR_BACKOFF, base_delay_seconds=2.0, jitter=False)
        manager = RetryManager(policy)
        assert manager.calculate_delay(0) == 2.0
        assert manager.calculate_delay(1) == 4.0
        assert manager.calculate_delay(2) == 6.0

    def test_calculate_delay_exponential_backoff(self) -> None:
        policy = RetryPolicy(
            strategy=RetryStrategy.EXPONENTIAL_BACKOFF,
            base_delay_seconds=1.0,
            backoff_multiplier=2.0,
            jitter=False,
        )
        manager = RetryManager(policy)
        assert manager.calculate_delay(0) == 1.0
        assert manager.calculate_delay(1) == 2.0
        assert manager.calculate_delay(2) == 4.0

    def test_calculate_delay_respects_max(self) -> None:
        policy = RetryPolicy(
            strategy=RetryStrategy.EXPONENTIAL_BACKOFF,
            base_delay_seconds=1.0,
            backoff_multiplier=100.0,
            max_delay_seconds=10.0,
            jitter=False,
        )
        manager = RetryManager(policy)
        delay = manager.calculate_delay(5)
        assert delay <= 10.0

    def test_calculate_delay_with_jitter(self) -> None:
        policy = RetryPolicy(strategy=RetryStrategy.FIXED_DELAY, base_delay_seconds=10.0, jitter=True)
        manager = RetryManager(policy)
        delay = manager.calculate_delay(0)
        assert 8.0 <= delay <= 12.0

    def test_should_retry_checks_non_retryable_patterns(self) -> None:
        policy = RetryPolicy(non_retryable_errors=["fatal", "corrupt"])
        manager = RetryManager(policy)
        record = ErrorRecord(
            error_id="ERR_001",
            timestamp=datetime.now(),
            error_message="fatal disk error",
            error_type="DiskError",
            severity=ErrorSeverity.HIGH,
            category=ErrorCategory.DATA_QUALITY,
        )
        assert manager.should_retry(record) is False

    def test_should_retry_checks_retryable_patterns(self) -> None:
        policy = RetryPolicy(retryable_errors=["lock"])
        manager = RetryManager(policy)
        record = ErrorRecord(
            error_id="ERR_001",
            timestamp=datetime.now(),
            error_message="lock wait timeout exceeded",
            error_type="LockError",
            severity=ErrorSeverity.MEDIUM,
            category=ErrorCategory.DATA_QUALITY,
        )
        assert manager.should_retry(record) is True


class TestErrorRecoveryManager:
    @pytest.fixture
    def manager(self) -> ErrorRecoveryManager:
        connection = MagicMock()
        return ErrorRecoveryManager(connection=connection, dialect="duckdb")

    def test_instantiation(self, manager: ErrorRecoveryManager) -> None:
        assert manager.dialect == "duckdb"
        assert manager.error_log == []
        assert manager.dead_letter_queue == []

    def test_classify_error_delegates_to_classifier(self, manager: ErrorRecoveryManager) -> None:
        category, severity = manager.classify_error("timeout occurred")
        assert category == ErrorCategory.TRANSIENT

    def test_handle_error_transient_returns_should_retry(self, manager: ErrorRecoveryManager) -> None:
        error = TimeoutError("timeout")
        context = {"operation_name": "load_table", "batch_id": 1}
        should_retry, delay = manager.handle_error(error, context)
        assert isinstance(should_retry, bool)
        assert delay is None or isinstance(delay, float)

    def test_handle_error_logs_error(self, manager: ErrorRecoveryManager) -> None:
        error = ValueError("syntax error near foo")
        context = {"operation_name": "transform", "batch_id": 2}
        manager.handle_error(error, context)
        assert len(manager.error_log) == 1

    def test_create_checkpoint_returns_id(self, manager: ErrorRecoveryManager) -> None:
        cp_id = manager.create_checkpoint("load_batch", batch_id=1)
        assert cp_id.startswith("CP_1_load_batch_")
        assert cp_id in manager.checkpoints

    def test_create_checkpoint_stores_data(self, manager: ErrorRecoveryManager) -> None:
        cp_id = manager.create_checkpoint("test_op", batch_id=42, checkpoint_type="COMMIT", recovery_data={"k": "v"})
        cp = manager.checkpoints[cp_id]
        assert cp.batch_id == 42
        assert cp.operation_name == "test_op"
        assert cp.checkpoint_type == "COMMIT"
        assert cp.recovery_data == {"k": "v"}

    def test_restore_from_checkpoint_returns_state(self, manager: ErrorRecoveryManager) -> None:
        cp_id = manager.create_checkpoint("my_op", batch_id=5, recovery_data={"offset": 100})
        state = manager.restore_from_checkpoint(cp_id)
        assert state["batch_id"] == 5
        assert state["operation_name"] == "my_op"
        assert state["recovery_data"] == {"offset": 100}

    def test_restore_from_checkpoint_raises_on_unknown_id(self, manager: ErrorRecoveryManager) -> None:
        with pytest.raises(ValueError, match="Checkpoint not found"):
            manager.restore_from_checkpoint("nonexistent_id")

    def test_restore_from_checkpoint_raises_when_cannot_resume(self, manager: ErrorRecoveryManager) -> None:
        cp_id = manager.create_checkpoint("op", batch_id=1)
        manager.checkpoints[cp_id].can_resume_from = False
        with pytest.raises(ValueError, match="cannot be resumed"):
            manager.restore_from_checkpoint(cp_id)

    def test_get_error_statistics_empty(self, manager: ErrorRecoveryManager) -> None:
        stats = manager.get_error_statistics()
        assert "message" in stats
        assert stats["message"] == "No errors recorded"

    def test_get_error_statistics_with_errors(self, manager: ErrorRecoveryManager) -> None:
        error = TimeoutError("timeout")
        ctx = {"operation_name": "op", "batch_id": 1}
        manager.handle_error(error, ctx)
        stats = manager.get_error_statistics()
        assert stats["total_errors"] == 1
        assert "errors_by_category" in stats
        assert "errors_by_severity" in stats

    def test_get_recovery_statistics(self, manager: ErrorRecoveryManager) -> None:
        manager.create_checkpoint("op1", batch_id=1, checkpoint_type="SAVEPOINT")
        manager.create_checkpoint("op1", batch_id=2, checkpoint_type="COMMIT")
        stats = manager.get_recovery_statistics()
        assert stats["total_checkpoints"] >= 1
        assert "SAVEPOINT" in stats["checkpoints_by_type"] or "COMMIT" in stats["checkpoints_by_type"]

    def test_cleanup_old_errors_removes_nothing_when_fresh(self, manager: ErrorRecoveryManager) -> None:
        error = ValueError("syntax error")
        ctx = {"operation_name": "op", "batch_id": 1}
        manager.handle_error(error, ctx)
        cleaned = manager.cleanup_old_errors(retention_hours=24)
        assert cleaned == 0
        assert len(manager.error_log) == 1

    def test_cleanup_old_errors_removes_stale_records(self, manager: ErrorRecoveryManager) -> None:
        from datetime import timedelta

        error = ValueError("syntax error")
        ctx = {"operation_name": "op", "batch_id": 1}
        manager.handle_error(error, ctx)
        manager.error_log[0].timestamp = datetime.now() - timedelta(hours=48)
        cleaned = manager.cleanup_old_errors(retention_hours=24)
        assert cleaned == 1
        assert len(manager.error_log) == 0

    def test_execute_with_recovery_success(self, manager: ErrorRecoveryManager) -> None:
        result = manager.execute_with_recovery(
            lambda: 42,
            {"operation_name": "add", "batch_id": 0},
        )
        assert result == 42

    def test_execute_with_recovery_retries_on_failure(self, manager: ErrorRecoveryManager) -> None:
        call_count = 0

        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise TimeoutError("timeout")
            return "ok"

        policy = RetryPolicy(max_attempts=3, strategy=RetryStrategy.IMMEDIATE, jitter=False)
        result = manager.execute_with_recovery(
            flaky,
            {"operation_name": "flaky_op", "batch_id": 1},
            retry_policy=policy,
        )
        assert result == "ok"
        assert call_count == 2

    def test_execute_with_recovery_raises_after_max_attempts(self, manager: ErrorRecoveryManager) -> None:
        policy = RetryPolicy(max_attempts=2, strategy=RetryStrategy.IMMEDIATE, jitter=False)
        with pytest.raises(ValueError):
            manager.execute_with_recovery(
                lambda: (_ for _ in ()).throw(ValueError("syntax error")),
                {"operation_name": "always_fails", "batch_id": 1},
                retry_policy=policy,
            )

    def test_export_error_report(self, manager: ErrorRecoveryManager, tmp_path) -> None:
        from unittest.mock import patch

        error = TimeoutError("timeout")
        ctx = {"operation_name": "export_test", "batch_id": 99}
        manager.error_log.append(manager._create_error_record(error, ctx))

        output = tmp_path / "report.json"
        with patch.object(manager, "get_error_statistics", return_value={"total_errors": 1}):
            success = manager.export_error_report(str(output))
        assert success is True
        assert output.exists()
        data = json.loads(output.read_text())
        assert "error_records" in data
        assert len(data["error_records"]) == 1

    def test_export_error_report_with_stack_traces(self, manager: ErrorRecoveryManager, tmp_path) -> None:
        from unittest.mock import patch

        error = RuntimeError("runtime fail")
        ctx = {"operation_name": "trace_test", "batch_id": 1}
        manager.error_log.append(manager._create_error_record(error, ctx))

        output = tmp_path / "report_traces.json"
        with patch.object(manager, "get_error_statistics", return_value={"total_errors": 1}):
            success = manager.export_error_report(str(output), include_stack_traces=True)
        assert success is True
        data = json.loads(output.read_text())
        assert "stack_trace" in data["error_records"][0]

    def test_export_error_report_returns_false_on_bad_path(self, manager: ErrorRecoveryManager) -> None:
        from unittest.mock import patch

        with patch.object(manager, "get_error_statistics", return_value={"message": "No errors recorded"}):
            success = manager.export_error_report("/nonexistent/path/report.json")
        assert success is False


