from __future__ import annotations

from typing import TYPE_CHECKING, Any

from benchbox.core.tuning.applied_ledger import (
    APPLIED_UNVERIFIED,
    APPLIED_VERIFIED,
    EXECUTED,
    PHASE_POST_LOAD,
    STATEMENT_FAILED,
)
from benchbox.core.tuning.introspection import corroborate
from benchbox.core.tuning.reconciliation import (
    RECONCILIATION_FAILED_INTENT,
    declared_constraint_types,
    reconcile_requested_intents,
)

if TYPE_CHECKING:
    from benchbox.platforms.base.adapter import PlatformAdapter


def corroborate_applied_ledger(
    adapter: PlatformAdapter, connection: Any, status: str
) -> tuple[str, dict[str, Any] | None]:
    if status != APPLIED_UNVERIFIED:
        return status, None
    introspector = None
    try:
        introspector = adapter.get_tuning_introspector()
    except Exception as exc:
        adapter.logger.debug("tuning introspector lookup degraded: %s", exc)
    if introspector is None:
        return status, None
    try:
        state = introspector.introspect(connection, adapter._applied_tuning_ledger)
        receipt = corroborate(adapter._applied_tuning_ledger, state)
        if receipt.corroborated:
            status = APPLIED_VERIFIED
        return status, receipt.to_payload()
    except Exception as exc:
        adapter.logger.debug("applied-ledger corroboration degraded: %s", exc)
        return status, None


def attach_applied_ledger_payload(adapter: PlatformAdapter, result: Any, status: str) -> None:
    ledger = getattr(adapter, "_applied_tuning_ledger", None)
    if ledger is None or result is None:
        return
    drift_check_payload = adapter._build_drift_check_payload()
    if ledger.is_empty() and drift_check_payload is None:
        return
    try:
        result.applied_tuning_ledger = ledger.to_payload(status=status, drift_check=drift_check_payload)
        result.applied_ledger_hash = ledger.applied_ledger_hash()
    except Exception as exc:
        adapter.logger.debug("applied-ledger attach degraded: %s", exc)


def build_drift_check_payload(adapter: PlatformAdapter) -> dict[str, Any] | None:
    try:
        if not (adapter.tuning_enabled and getattr(adapter, "database_was_reused", False)):
            return None
        result = getattr(adapter, "_drift_validation_result", None)
        if result is None:
            return None
        return result.to_payload()
    except Exception as exc:
        adapter.logger.debug("drift-check payload build degraded: %s", exc)
        return None


def _fold_one_layout_op(ledger: Any, op: dict[str, Any]) -> str:
    status = op.get("status")
    if status == "applied":
        ledger.record(
            op.get("statement", ""),
            op.get("phase") or PHASE_POST_LOAD,
            status=EXECUTED,
            mechanism=op.get("mechanism"),
            table=op.get("table"),
            error=op.get("error_message"),
        )
        return EXECUTED
    if status == "skipped" and not op.get("error_class") and not op.get("error_message"):
        mechanism = op.get("mechanism") or "layout"
        table = op.get("table")
        reason = f"skipped: {mechanism} not executed" + (f" for {table}" if table else "")
        ledger.record_dropped(str(op.get("statement", "")), reason)
        return "dropped"
    ledger.record(
        op.get("statement", ""),
        op.get("phase") or PHASE_POST_LOAD,
        status=STATEMENT_FAILED,
        mechanism=op.get("mechanism"),
        table=op.get("table"),
        error=op.get("error_message"),
    )
    return STATEMENT_FAILED


def fold_layout_operations_into_ledger(adapter: PlatformAdapter) -> None:
    ledger = getattr(adapter, "_applied_tuning_ledger", None)
    if ledger is None:
        return
    applied_ops = getattr(adapter, "_applied_layout_operations", None) or []
    skipped_ops = getattr(adapter, "_skipped_layout_operations", None) or []
    if not applied_ops and not skipped_ops:
        return
    for op in list(applied_ops) + list(skipped_ops):
        try:
            outcome = _fold_one_layout_op(ledger, op)
            if outcome == STATEMENT_FAILED and op.get("status") not in ("failed", "applied", "skipped"):
                adapter.logger.debug("applied-ledger unknown layout-op status %r; recorded as failed", op.get("status"))
        except Exception as exc:
            adapter.logger.debug("applied-ledger layout fold degraded: %s", exc)


def reconcile_requested_tuning(adapter: PlatformAdapter, config: Any) -> None:
    ledger = getattr(adapter, "_applied_tuning_ledger", None)
    if ledger is None or not config or not adapter.tuning_enabled:
        return
    if getattr(adapter, "database_was_reused", False) or adapter.is_dry_run:
        return
    dropped_before = len(ledger.dropped)
    try:
        reconcile_requested_intents(
            config,
            ledger,
            adapter.canonical_platform_type,
            sorted_tables=list(getattr(adapter, "_sorted_ingestion_applied_tables", None) or []),
            declared_constraints=declared_constraint_types(getattr(adapter, "benchmark", None)),
        )
    except Exception as exc:
        ledger.record_dropped(RECONCILIATION_FAILED_INTENT, f"reconciliation failed: {exc}")
    for dropped in ledger.dropped[dropped_before:]:
        adapter.logger.warning("Requested tuning intent not applied: %s (%s)", dropped.intent, dropped.reason)


def apply_phase_status(adapter: PlatformAdapter, has_config: bool) -> str:
    return adapter._applied_tuning_ledger.overall_status(
        tuning_enabled=adapter.tuning_enabled,
        has_config=has_config,
    )


def duckdb_tuning_introspector() -> Any:
    from benchbox.platforms.duckdb_introspection import DuckDBTuningIntrospector

    return DuckDBTuningIntrospector()


def clickhouse_tuning_introspector() -> Any:
    from benchbox.platforms.clickhouse.introspection import ClickHouseTuningIntrospector

    return ClickHouseTuningIntrospector()


def snowflake_tuning_introspector(schema: str | None) -> Any:
    from benchbox.platforms.snowflake_introspection import SnowflakeTuningIntrospector

    return SnowflakeTuningIntrospector(schema=schema)


def read_back_applied_ledger(
    adapter: PlatformAdapter, connection: Any, apply_status: str, has_config: bool
) -> tuple[str, dict[str, Any] | None, str | None, dict[str, Any] | None]:
    final_tuning_status = apply_status
    applied_ledger_payload = None
    applied_ledger_hash = None
    applied_receipt_payload = None
    try:
        final_tuning_status = adapter._applied_tuning_ledger.overall_status(
            tuning_enabled=adapter.tuning_enabled,
            has_config=has_config,
        )
        final_tuning_status, applied_receipt_payload = adapter._corroborate_applied_ledger(
            connection, final_tuning_status
        )
        drift_check_payload = adapter._build_drift_check_payload()
        if not adapter._applied_tuning_ledger.is_empty() or drift_check_payload is not None:
            applied_ledger_payload = adapter._applied_tuning_ledger.to_payload(
                status=final_tuning_status,
                receipt=applied_receipt_payload,
                drift_check=drift_check_payload,
            )
            applied_ledger_hash = adapter._applied_tuning_ledger.applied_ledger_hash()
    except Exception as exc:
        adapter.logger.debug("applied-ledger read-back degraded: %s", exc)
    return final_tuning_status, applied_ledger_payload, applied_ledger_hash, applied_receipt_payload
