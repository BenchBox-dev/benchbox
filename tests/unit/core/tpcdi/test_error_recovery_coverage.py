from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

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

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.mark.parametrize(
    "member,value",
    [
        (ErrorSeverity.LOW, "LOW"),
        (ErrorSeverity.MEDIUM, "MEDIUM"),
        (ErrorSeverity.HIGH, "HIGH"),
        (ErrorSeverity.CRITICAL, "CRITICAL"),
    ],
)
def test_error_severity_values(member, value):
    assert member.value == value


@pytest.mark.parametrize(
    "member,value",
    [
        (ErrorCategory.TRANSIENT, "TRANSIENT"),
        (ErrorCategory.PERMANENT, "PERMANENT"),
        (ErrorCategory.DATA_QUALITY, "DATA_QUALITY"),
        (ErrorCategory.SYSTEM, "SYSTEM"),
        (ErrorCategory.CONFIGURATION, "CONFIGURATION"),
        (ErrorCategory.BUSINESS_RULE, "BUSINESS_RULE"),
    ],
)
def test_error_category_values(member, value):
    assert member.value == value


@pytest.mark.parametrize(
    "member,value",
    [
        (RetryStrategy.IMMEDIATE, "IMMEDIATE"),
        (RetryStrategy.EXPONENTIAL_BACKOFF, "EXPONENTIAL_BACKOFF"),
        (RetryStrategy.LINEAR_BACKOFF, "LINEAR_BACKOFF"),
        (RetryStrategy.FIXED_DELAY, "FIXED_DELAY"),
        (RetryStrategy.NO_RETRY, "NO_RETRY"),
    ],
)
def test_retry_strategy_values(member, value):
    assert member.value == value


def test_error_record_defaults():
    rec = ErrorRecord(
        error_id="ERR_001",
        timestamp=datetime.now(),
        error_message="something went wrong",
        error_type="ValueError",
        severity=ErrorSeverity.MEDIUM,
        category=ErrorCategory.TRANSIENT,
    )
    assert rec.retry_count == 0
    assert rec.max_retries == 3
    assert rec.retry_strategy == RetryStrategy.EXPONENTIAL_BACKOFF
    assert rec.can_retry is True
    assert rec.is_resolved is False
    assert rec.batch_id is None
    assert rec.stack_trace is None


def test_error_record_with_all_fields():
    now = datetime.now()
    rec = ErrorRecord(
        error_id="ERR_002",
        timestamp=now,
        error_message="disk full",
        error_type="OSError",
        severity=ErrorSeverity.CRITICAL,
        category=ErrorCategory.SYSTEM,
        batch_id=42,
        table_name="DimCustomer",
        operation_name="load",
        record_context={"key": "val"},
        exception_type="OSError",
        stack_trace="Traceback ...",
        error_code="ENOSPC",
        retry_count=1,
        max_retries=5,
        retry_strategy=RetryStrategy.LINEAR_BACKOFF,
        can_retry=False,
        recovery_action="alert ops",
        is_resolved=True,
        resolution_timestamp=now,
        resolution_notes="manual fix",
    )
    assert rec.batch_id == 42
    assert rec.table_name == "DimCustomer"
    assert rec.is_resolved is True
    assert rec.error_code == "ENOSPC"


def test_recovery_checkpoint_defaults():
    cp = RecoveryCheckpoint(
        checkpoint_id="CP_1",
        timestamp=datetime.now(),
        batch_id=1,
        operation_name="load_trades",
        checkpoint_type="SAVEPOINT",
    )
    assert cp.records_processed == 0
    assert cp.tables_completed == []
    assert cp.can_resume_from is True
    assert cp.dependencies == []


def test_retry_policy_defaults():
    policy = RetryPolicy()
    assert policy.max_attempts == 3
    assert policy.strategy == RetryStrategy.EXPONENTIAL_BACKOFF
    assert policy.base_delay_seconds == 1.0
    assert policy.max_delay_seconds == 300.0
    assert policy.jitter is True


@pytest.mark.parametrize(
    "msg,expected_category,expected_severity",
    [
        ("connection reset by peer", ErrorCategory.TRANSIENT, ErrorSeverity.MEDIUM),
        ("lock timeout waiting for resource", ErrorCategory.TRANSIENT, ErrorSeverity.MEDIUM),
        ("syntax error near SELECT", ErrorCategory.PERMANENT, ErrorSeverity.HIGH),
        ("table not found: FactTrade", ErrorCategory.PERMANENT, ErrorSeverity.HIGH),
        ("duplicate key violates unique constraint", ErrorCategory.DATA_QUALITY, ErrorSeverity.MEDIUM),
        ("null value in column violates not-null", ErrorCategory.DATA_QUALITY, ErrorSeverity.MEDIUM),
        ("disk full, cannot write", ErrorCategory.SYSTEM, ErrorSeverity.CRITICAL),
        ("out of memory error", ErrorCategory.SYSTEM, ErrorSeverity.CRITICAL),
        ("totally unknown error xyz", ErrorCategory.SYSTEM, ErrorSeverity.MEDIUM),
    ],
)
def test_error_classifier_message_patterns(msg, expected_category, expected_severity):
    classifier = ErrorClassifier()
    category, severity = classifier.classify_error(msg)
    assert category == expected_category
    assert severity == expected_severity


@pytest.mark.parametrize(
    "exception_type,expected_category",
    [
        ("TimeoutError", ErrorCategory.TRANSIENT),
        ("ConnectionError", ErrorCategory.TRANSIENT),
        ("SQLAlchemyError", ErrorCategory.SYSTEM),
        ("DatabaseError", ErrorCategory.SYSTEM),
    ],
)
def test_error_classifier_exception_type_fallback(exception_type, expected_category):
    classifier = ErrorClassifier()

    category, _ = classifier.classify_error("generic failure", exception_type)
    assert category == expected_category


def _make_error_record(**kwargs) -> ErrorRecord:
    defaults = {
        "error_id": "E1",
        "timestamp": datetime.now(),
        "error_message": "timeout",
        "error_type": "TimeoutError",
        "severity": ErrorSeverity.MEDIUM,
        "category": ErrorCategory.TRANSIENT,
    }
    defaults.update(kwargs)
    return ErrorRecord(**defaults)


def test_retry_manager_allows_transient_retry():
    mgr = RetryManager()
    rec = _make_error_record(category=ErrorCategory.TRANSIENT, retry_count=0)
    assert mgr.should_retry(rec) is True


def test_retry_manager_blocks_when_max_attempts_reached():
    policy = RetryPolicy(max_attempts=3)
    mgr = RetryManager(policy)
    rec = _make_error_record(category=ErrorCategory.TRANSIENT, retry_count=3)
    assert mgr.should_retry(rec) is False


def test_retry_manager_blocks_permanent_errors():
    mgr = RetryManager()
    rec = _make_error_record(category=ErrorCategory.PERMANENT)
    assert mgr.should_retry(rec) is False


def test_retry_manager_blocks_configuration_errors():
    mgr = RetryManager()
    rec = _make_error_record(category=ErrorCategory.CONFIGURATION)
    assert mgr.should_retry(rec) is False


def test_retry_manager_blocks_when_can_retry_false():
    mgr = RetryManager()
    rec = _make_error_record(category=ErrorCategory.TRANSIENT, can_retry=False)
    assert mgr.should_retry(rec) is False


def test_retry_manager_non_retryable_error_pattern():
    policy = RetryPolicy(non_retryable_errors=["syntax error"])
    mgr = RetryManager(policy)
    rec = _make_error_record(
        category=ErrorCategory.SYSTEM,
        error_message="syntax error near token",
    )
    assert mgr.should_retry(rec) is False


def test_retry_manager_retryable_error_pattern():
    policy = RetryPolicy(retryable_errors=["custom transient"])
    mgr = RetryManager(policy)
    rec = _make_error_record(
        category=ErrorCategory.DATA_QUALITY,
        error_message="custom transient network issue",
        retry_count=0,
    )
    assert mgr.should_retry(rec) is True


def test_retry_manager_system_category_retried_by_default():
    mgr = RetryManager()
    rec = _make_error_record(category=ErrorCategory.SYSTEM, retry_count=0)
    assert mgr.should_retry(rec) is True


def test_calculate_delay_immediate():
    policy = RetryPolicy(strategy=RetryStrategy.IMMEDIATE, jitter=False)
    mgr = RetryManager(policy)
    assert mgr.calculate_delay(0) == 0.0


def test_calculate_delay_fixed():
    policy = RetryPolicy(strategy=RetryStrategy.FIXED_DELAY, base_delay_seconds=5.0, jitter=False)
    mgr = RetryManager(policy)
    assert mgr.calculate_delay(0) == pytest.approx(5.0)
    assert mgr.calculate_delay(3) == pytest.approx(5.0)


def test_calculate_delay_linear_backoff():
    policy = RetryPolicy(strategy=RetryStrategy.LINEAR_BACKOFF, base_delay_seconds=2.0, jitter=False)
    mgr = RetryManager(policy)

    assert mgr.calculate_delay(0) == pytest.approx(2.0)
    assert mgr.calculate_delay(1) == pytest.approx(4.0)
    assert mgr.calculate_delay(2) == pytest.approx(6.0)


def test_calculate_delay_exponential_backoff():
    policy = RetryPolicy(
        strategy=RetryStrategy.EXPONENTIAL_BACKOFF,
        base_delay_seconds=1.0,
        backoff_multiplier=2.0,
        jitter=False,
    )
    mgr = RetryManager(policy)

    assert mgr.calculate_delay(0) == pytest.approx(1.0)
    assert mgr.calculate_delay(1) == pytest.approx(2.0)
    assert mgr.calculate_delay(2) == pytest.approx(4.0)


def test_calculate_delay_respects_max_delay():
    policy = RetryPolicy(
        strategy=RetryStrategy.EXPONENTIAL_BACKOFF,
        base_delay_seconds=1.0,
        backoff_multiplier=2.0,
        max_delay_seconds=10.0,
        jitter=False,
    )
    mgr = RetryManager(policy)

    assert mgr.calculate_delay(10) == pytest.approx(10.0)


def test_calculate_delay_with_jitter_is_nonnegative():
    policy = RetryPolicy(strategy=RetryStrategy.FIXED_DELAY, base_delay_seconds=5.0, jitter=True)
    mgr = RetryManager(policy)
    for _ in range(20):
        assert mgr.calculate_delay(0) >= 0.0


def test_calculate_delay_no_retry_falls_back_to_base():
    policy = RetryPolicy(strategy=RetryStrategy.NO_RETRY, base_delay_seconds=3.0, jitter=False)
    mgr = RetryManager(policy)
    assert mgr.calculate_delay(0) == pytest.approx(3.0)


@pytest.fixture()
def manager():
    conn = MagicMock()
    return ErrorRecoveryManager(conn, dialect="duckdb")


def test_manager_initial_state(manager):
    assert manager.error_log == []
    assert manager.dead_letter_queue == []
    assert manager.checkpoints == {}


def test_manager_classify_error_delegates(manager):
    category, severity = manager.classify_error("timeout waiting", "TimeoutError")
    assert category == ErrorCategory.TRANSIENT


def test_manager_handle_error_transient_returns_retry(manager):
    err = TimeoutError("connection timeout")
    ctx = {"operation_name": "load", "batch_id": 1}
    should_retry, delay = manager.handle_error(err, ctx)
    assert should_retry is True
    assert delay is not None
    assert delay >= 0.0
    assert len(manager.error_log) == 1


def test_manager_handle_error_permanent_goes_to_dead_letter(manager):
    err = ValueError("syntax error in SQL")
    ctx = {"operation_name": "load", "batch_id": 1}
    should_retry, delay = manager.handle_error(err, ctx)
    assert should_retry is False
    assert delay is None
    assert len(manager.dead_letter_queue) == 1


def test_manager_handle_error_with_custom_policy(manager):
    policy = RetryPolicy(max_attempts=0)
    err = TimeoutError("timeout")
    ctx = {"operation_name": "op", "batch_id": 2}
    should_retry, _ = manager.handle_error(err, ctx, retry_policy=policy)
    assert should_retry is False


def test_manager_create_checkpoint(manager):
    cp_id = manager.create_checkpoint("load_trades", batch_id=5, checkpoint_type="SAVEPOINT")
    assert cp_id in manager.checkpoints
    cp = manager.checkpoints[cp_id]
    assert cp.batch_id == 5
    assert cp.operation_name == "load_trades"
    assert cp.checkpoint_type == "SAVEPOINT"


def test_manager_create_checkpoint_with_recovery_data(manager):
    cp_id = manager.create_checkpoint(
        "etl_op",
        batch_id=7,
        checkpoint_type="COMMIT",
        recovery_data={"offset": 100},
    )
    cp = manager.checkpoints[cp_id]
    assert cp.recovery_data == {"offset": 100}


def test_manager_restore_from_checkpoint(manager):
    cp_id = manager.create_checkpoint("op", batch_id=3)
    state = manager.restore_from_checkpoint(cp_id)
    assert state["batch_id"] == 3
    assert state["operation_name"] == "op"
    assert "records_processed" in state


def test_manager_restore_missing_checkpoint_raises(manager):
    with pytest.raises(ValueError, match="not found"):
        manager.restore_from_checkpoint("nonexistent_id")


def test_manager_restore_non_resumable_checkpoint_raises(manager):
    cp_id = manager.create_checkpoint("op", batch_id=1)
    manager.checkpoints[cp_id].can_resume_from = False
    with pytest.raises(ValueError, match="cannot be resumed"):
        manager.restore_from_checkpoint(cp_id)


def test_manager_get_error_statistics_no_errors(manager):
    stats = manager.get_error_statistics()
    assert stats == {"message": "No errors recorded"}


def test_manager_get_error_statistics_with_errors(manager):
    for msg in ["timeout", "connection reset", "disk full"]:
        err = Exception(msg)
        manager.handle_error(err, {"operation_name": "op", "batch_id": 1})
    stats = manager.get_error_statistics()
    assert stats["total_errors"] == 3
    assert "errors_by_category" in stats
    assert "errors_by_severity" in stats
    assert "recent_error_rate" in stats
    assert "most_common_errors" in stats


def test_manager_get_recovery_statistics(manager):

    conn = MagicMock()
    mgr = ErrorRecoveryManager(conn)
    mgr.create_checkpoint("op1", 1, "SAVEPOINT")
    mgr.create_checkpoint("op2", 2, "COMMIT")
    mgr.create_checkpoint("op3", 3, "ROLLBACK")
    stats = mgr.get_recovery_statistics()
    assert stats["total_checkpoints"] == 3
    assert stats["checkpoints_by_type"]["SAVEPOINT"] == 1
    assert stats["checkpoints_by_type"]["COMMIT"] == 1
    assert stats["checkpoints_by_type"]["ROLLBACK"] == 1


def test_manager_cleanup_old_errors_removes_nothing_recent(manager):
    err = TimeoutError("timeout")
    manager.handle_error(err, {"operation_name": "op", "batch_id": 1})
    removed = manager.cleanup_old_errors(retention_hours=24)
    assert removed == 0
    assert len(manager.error_log) == 1


def test_manager_cleanup_old_errors_removes_old_records(manager):
    err = TimeoutError("old error")
    manager.handle_error(err, {"operation_name": "op", "batch_id": 1})

    manager.error_log[0].timestamp = datetime.now() - timedelta(hours=48)
    removed = manager.cleanup_old_errors(retention_hours=24)
    assert removed == 1
    assert len(manager.error_log) == 0


def test_manager_execute_with_recovery_success(manager):
    func = MagicMock(return_value="ok")
    ctx = {"operation_name": "load", "batch_id": 1}
    result = manager.execute_with_recovery(func, ctx)
    assert result == "ok"
    func.assert_called_once()


def test_manager_execute_with_recovery_retries_then_succeeds(manager):
    call_count = {"n": 0}

    def flaky():
        call_count["n"] += 1
        if call_count["n"] < 2:
            raise TimeoutError("timeout")
        return "success"

    policy = RetryPolicy(max_attempts=3, strategy=RetryStrategy.IMMEDIATE, jitter=False)
    ctx = {"operation_name": "load", "batch_id": 1}
    with patch("benchbox.core.tpcdi.etl.error_recovery.time.sleep"):
        result = manager.execute_with_recovery(flaky, ctx, retry_policy=policy)
    assert result == "success"
    assert call_count["n"] == 2


def test_manager_execute_with_recovery_raises_after_all_attempts(manager):
    def always_fail():
        raise ValueError("syntax error always")

    policy = RetryPolicy(max_attempts=2, strategy=RetryStrategy.IMMEDIATE, jitter=False)
    ctx = {"operation_name": "load", "batch_id": 1}
    with patch("benchbox.core.tpcdi.etl.error_recovery.time.sleep"):
        with pytest.raises(ValueError):
            manager.execute_with_recovery(always_fail, ctx, retry_policy=policy)


def test_manager_export_error_report(manager, tmp_path):

    rec = ErrorRecord(
        error_id="E_export",
        timestamp=datetime.now(),
        error_message="timeout",
        error_type="TimeoutError",
        severity=ErrorSeverity.MEDIUM,
        category=ErrorCategory.TRANSIENT,
    )
    manager.error_log.append(rec)

    out = tmp_path / "report.json"

    with patch.object(manager, "get_error_statistics", return_value={"total_errors": 1}):
        success = manager.export_error_report(str(out))
    assert success is True
    data = json.loads(out.read_text())
    assert "error_records" in data
    assert len(data["error_records"]) == 1


def test_manager_export_error_report_with_stack_traces(manager, tmp_path):
    rec = ErrorRecord(
        error_id="E_export2",
        timestamp=datetime.now(),
        error_message="timeout",
        error_type="TimeoutError",
        severity=ErrorSeverity.MEDIUM,
        category=ErrorCategory.TRANSIENT,
        stack_trace="Traceback (most recent call last): ...",
    )
    manager.error_log.append(rec)

    out = tmp_path / "report_with_traces.json"
    with patch.object(manager, "get_error_statistics", return_value={"total_errors": 1}):
        success = manager.export_error_report(str(out), include_stack_traces=True)
    assert success is True
    data = json.loads(out.read_text())
    assert "stack_trace" in data["error_records"][0]


def test_manager_export_error_report_failure(manager):
    with patch.object(manager, "get_error_statistics", return_value={}):
        success = manager.export_error_report("/nonexistent/path/report.json")
    assert success is False


def test_manager_error_trend_with_enough_errors(manager):
    for i in range(20):
        rec = ErrorRecord(
            error_id=f"E{i}",
            timestamp=datetime.now(),
            error_message="timeout",
            error_type="TimeoutError",
            severity=ErrorSeverity.MEDIUM,
            category=ErrorCategory.TRANSIENT,
        )
        manager._log_error(rec)
    stats = manager.get_error_statistics()
    assert stats["error_trend"] in ("STABLE", "INCREASING", "DECREASING")


def test_manager_log_error_severity_routing(manager):
    for severity in (ErrorSeverity.LOW, ErrorSeverity.MEDIUM, ErrorSeverity.HIGH, ErrorSeverity.CRITICAL):
        rec = ErrorRecord(
            error_id=f"E_{severity.value}",
            timestamp=datetime.now(),
            error_message="msg",
            error_type="Exception",
            severity=severity,
            category=ErrorCategory.SYSTEM,
        )
        manager._log_error(rec)
    assert len(manager.error_log) == 4


def test_manager_create_error_record_captures_context(manager):
    err = RuntimeError("disk full on /data")
    ctx = {
        "batch_id": 99,
        "table_name": "FactTrade",
        "operation_name": "bulk_load",
        "record_context": {"row": 42},
    }
    rec = manager._create_error_record(err, ctx)
    assert rec.batch_id == 99
    assert rec.table_name == "FactTrade"
    assert rec.operation_name == "bulk_load"
    assert rec.record_context == {"row": 42}
    assert rec.error_message == "disk full on /data"
    assert rec.stack_trace is not None


