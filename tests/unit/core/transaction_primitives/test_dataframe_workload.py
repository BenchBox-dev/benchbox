# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

from benchbox.core.transaction_primitives.benchmark import TransactionPrimitivesBenchmark
from benchbox.core.transaction_primitives.dataframe_operations import (
    DataFrameTransactionResult,
    TransactionOperationType,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


_OP_TYPES_IN_PHASE_ORDER = [
    TransactionOperationType.ATOMIC_INSERT,
    TransactionOperationType.ATOMIC_UPDATE,
    TransactionOperationType.ATOMIC_DELETE,
    TransactionOperationType.ATOMIC_MERGE,
    TransactionOperationType.ROLLBACK_TO_VERSION,
    TransactionOperationType.ROLLBACK_TO_TIMESTAMP,
    TransactionOperationType.TIME_TRAVEL_QUERY,
    TransactionOperationType.VERSION_COMPARE,
    TransactionOperationType.CONCURRENT_WRITE,
    TransactionOperationType.CONFLICT_RESOLUTION,
    TransactionOperationType.SNAPSHOT_ISOLATION,
    TransactionOperationType.READ_YOUR_WRITES,
]


def _make_result(
    op_type: TransactionOperationType,
    duration_ms: float = 42.0,
    rows: int = 10,
    success: bool = True,
    error_message: str | None = None,
) -> DataFrameTransactionResult:
    now = time.time()
    return DataFrameTransactionResult(
        operation_type=op_type,
        success=success,
        start_time=now,
        end_time=now + duration_ms / 1000.0,
        duration_ms=duration_ms,
        rows_affected=rows,
        error_message=error_message,
    )


def _make_full_mock_manager(supports_all: bool = True) -> MagicMock:
    manager = MagicMock()
    manager.supports_operation.return_value = supports_all

    for op in TransactionOperationType:
        method_name = f"execute_{op.value}"
        getattr(manager, method_name).return_value = _make_result(op)

    return manager


def _make_ctx_adapter_config(
    platform_name: str = "pyspark-df",
    options: dict[str, Any] | None = None,
) -> tuple[MagicMock, MagicMock, MagicMock]:
    adapter = MagicMock()
    adapter.platform_name = platform_name

    ctx = MagicMock()
    ctx.spark_session = MagicMock()

    benchmark_config = MagicMock()
    benchmark_config.options = options or {}

    return ctx, adapter, benchmark_config


def _run_workload(
    benchmark: TransactionPrimitivesBenchmark,
    mock_manager: MagicMock,
    platform_name: str = "pyspark-df",
    options: dict[str, Any] | None = None,
    query_filter: set[str] | None = None,
) -> list[dict[str, Any]]:
    ctx, adapter, benchmark_config = _make_ctx_adapter_config(platform_name, options)

    with (
        patch.object(benchmark, "get_dataframe_operations", return_value=mock_manager),
        patch.object(benchmark, "_setup_transaction_table"),
        patch.object(benchmark, "_create_test_orders_df", return_value=MagicMock()),
    ):
        return benchmark.execute_dataframe_workload(
            ctx=ctx,
            adapter=adapter,
            benchmark_config=benchmark_config,
            query_filter=query_filter,
        )


class TestSkipDataframeDataLoading:
    def test_returns_true(self):
        b = TransactionPrimitivesBenchmark()
        assert b.skip_dataframe_data_loading() is True

    def test_is_a_callable_method(self):
        b = TransactionPrimitivesBenchmark()
        assert callable(b.skip_dataframe_data_loading)


class TestResultDictFormat:
    REQUIRED_KEYS = {"query_id", "status", "execution_time_seconds", "rows_returned", "iteration", "run_type"}

    def test_all_required_keys_present(self):
        b = TransactionPrimitivesBenchmark()
        results = _run_workload(b, _make_full_mock_manager())
        for row in results:
            missing = self.REQUIRED_KEYS - row.keys()
            assert not missing, f"Result for {row.get('query_id')} missing keys: {missing}"

    def test_execution_time_is_float_seconds_not_ms(self):
        b = TransactionPrimitivesBenchmark()
        results = _run_workload(b, _make_full_mock_manager())
        for row in results:
            val = row["execution_time_seconds"]
            assert isinstance(val, float), f"execution_time_seconds is {type(val)} not float"
            assert val < 1.0 or row["status"] != "SUCCESS", (
                f"execution_time_seconds={val} looks like ms not seconds (op={row['query_id']})"
            )

    def test_run_type_is_measurement(self):
        b = TransactionPrimitivesBenchmark()
        results = _run_workload(b, _make_full_mock_manager())
        for row in results:
            assert row["run_type"] == "measurement"

    def test_status_values_are_valid(self):
        b = TransactionPrimitivesBenchmark()
        results = _run_workload(b, _make_full_mock_manager())
        valid = {"SUCCESS", "FAILED", "SKIPPED"}
        for row in results:
            assert row["status"] in valid, f"Unexpected status: {row['status']}"

    def test_iteration_starts_at_one(self):
        b = TransactionPrimitivesBenchmark()
        results = _run_workload(b, _make_full_mock_manager())
        for row in results:
            assert row["iteration"] >= 1


class TestNoPhantomData:
    def test_success_rows_have_positive_execution_time(self):
        b = TransactionPrimitivesBenchmark()
        results = _run_workload(b, _make_full_mock_manager())
        success_rows = [r for r in results if r["status"] == "SUCCESS"]
        assert success_rows, "Expected at least one SUCCESS row"
        for row in success_rows:
            assert row["execution_time_seconds"] > 0.0, f"Phantom data: SUCCESS with 0.0 seconds for {row['query_id']}"

    def test_no_row_has_zero_time_with_success_status(self):
        b = TransactionPrimitivesBenchmark()
        results = _run_workload(b, _make_full_mock_manager())
        phantom = [r for r in results if r["status"] == "SUCCESS" and r["execution_time_seconds"] == 0.0]
        assert not phantom, f"Phantom data rows: {[r['query_id'] for r in phantom]}"


class TestManagerDelegation:
    def test_all_supported_operations_are_delegated(self):
        b = TransactionPrimitivesBenchmark()
        manager = _make_full_mock_manager(supports_all=True)
        results = _run_workload(b, manager)

        result_ids = {r["query_id"] for r in results}
        for op in TransactionOperationType:
            assert op.value in result_ids, f"Operation {op.value} missing from results"

    def test_atomic_insert_called_with_table_path_and_dataframe(self):
        b = TransactionPrimitivesBenchmark()
        manager = _make_full_mock_manager()
        _run_workload(b, manager)
        assert manager.execute_atomic_insert.call_count >= 1
        args, kwargs = manager.execute_atomic_insert.call_args
        assert len(args) >= 1 or "table_path" in kwargs, "table_path not passed"

    def test_atomic_update_called(self):
        b = TransactionPrimitivesBenchmark()
        manager = _make_full_mock_manager()
        _run_workload(b, manager)
        assert manager.execute_atomic_update.call_count >= 1

    def test_rollback_to_version_called_with_version_0(self):
        b = TransactionPrimitivesBenchmark()
        manager = _make_full_mock_manager()
        _run_workload(b, manager)
        manager.execute_rollback_to_version.assert_called()
        _, kwargs = manager.execute_rollback_to_version.call_args
        assert kwargs.get("version") == 0

    def test_time_travel_query_called_with_version(self):
        b = TransactionPrimitivesBenchmark()
        manager = _make_full_mock_manager()
        _run_workload(b, manager)
        manager.execute_time_travel_query.assert_called()
        _, kwargs = manager.execute_time_travel_query.call_args
        assert "version" in kwargs

    def test_concurrent_write_receives_list_of_dataframes(self):
        b = TransactionPrimitivesBenchmark()
        manager = _make_full_mock_manager()
        _run_workload(b, manager)
        manager.execute_concurrent_write.assert_called()
        args, _ = manager.execute_concurrent_write.call_args
        assert len(args) >= 2
        assert isinstance(args[1], list), "concurrent_write must receive a list of DataFrames"
        assert len(args[1]) == 3, "Expected 3 concurrent DataFrames"

    def test_conflict_resolution_called_with_retry_strategy(self):
        b = TransactionPrimitivesBenchmark()
        manager = _make_full_mock_manager()
        _run_workload(b, manager)
        manager.execute_conflict_resolution.assert_called()
        _, kwargs = manager.execute_conflict_resolution.call_args
        assert kwargs.get("resolution_strategy") == "retry"

    def test_atomic_delete_targets_staging_data_range(self):
        b = TransactionPrimitivesBenchmark()
        manager = _make_full_mock_manager()
        _run_workload(b, manager)
        manager.execute_atomic_delete.assert_called()
        args, _ = manager.execute_atomic_delete.call_args
        condition = args[1]
        assert "8000001" in condition, (
            f"Delete condition {condition!r} does not target staging rows - "
            "use a range within 8000001-8000020 so the delete is non-trivial"
        )

    def test_supports_operation_checked_for_each_op(self):
        b = TransactionPrimitivesBenchmark()
        manager = _make_full_mock_manager()
        _run_workload(b, manager)
        assert manager.supports_operation.call_count >= len(TransactionOperationType)


class TestUnsupportedOperationsSkipped:
    def test_unsupported_operation_emits_skipped_status(self):
        b = TransactionPrimitivesBenchmark()
        manager = _make_full_mock_manager()
        manager.supports_operation.return_value = False

        results = _run_workload(b, manager)
        assert results, "Expected SKIPPED rows, got empty list"
        for row in results:
            assert row["status"] == "SKIPPED", f"Expected SKIPPED, got {row['status']} for {row['query_id']}"

    def test_skipped_rows_have_zero_execution_time(self):
        b = TransactionPrimitivesBenchmark()
        manager = _make_full_mock_manager()
        manager.supports_operation.return_value = False

        results = _run_workload(b, manager)
        for row in results:
            assert row["execution_time_seconds"] == 0.0

    def test_skipped_operations_do_not_call_execute_methods(self):
        b = TransactionPrimitivesBenchmark()
        manager = _make_full_mock_manager()
        manager.supports_operation.return_value = False

        _run_workload(b, manager)

        for op in TransactionOperationType:
            method = getattr(manager, f"execute_{op.value}")
            assert method.call_count == 0, f"execute_{op.value} was called for an unsupported op"

    def test_partial_support_mixes_success_and_skipped(self):
        write_ops = {
            TransactionOperationType.ATOMIC_INSERT,
            TransactionOperationType.ATOMIC_UPDATE,
            TransactionOperationType.ATOMIC_DELETE,
            TransactionOperationType.ATOMIC_MERGE,
        }

        b = TransactionPrimitivesBenchmark()
        manager = _make_full_mock_manager()
        manager.supports_operation.side_effect = lambda op: op in write_ops

        results = _run_workload(b, manager)
        result_by_id = {r["query_id"]: r for r in results}

        for op in write_ops:
            assert result_by_id[op.value]["status"] == "SUCCESS"
        for op in TransactionOperationType:
            if op not in write_ops:
                assert result_by_id[op.value]["status"] == "SKIPPED"


class TestQueryFilter:
    def test_filter_limits_results_to_matching_ops(self):
        b = TransactionPrimitivesBenchmark()
        manager = _make_full_mock_manager()
        filt = {"ATOMIC_INSERT", "SNAPSHOT_ISOLATION"}

        results = _run_workload(b, manager, query_filter=filt)
        returned_ids = {r["query_id"] for r in results}
        assert returned_ids == {"atomic_insert", "snapshot_isolation"}

    def test_no_filter_returns_all_12_operations(self):
        b = TransactionPrimitivesBenchmark()
        manager = _make_full_mock_manager()

        results = _run_workload(b, manager)
        returned_ids = {r["query_id"] for r in results}
        assert len(returned_ids) == 12

    def test_filtered_out_operations_not_executed(self):
        b = TransactionPrimitivesBenchmark()
        manager = _make_full_mock_manager()

        _run_workload(b, manager, query_filter={"ATOMIC_INSERT"})

        for op in TransactionOperationType:
            if op != TransactionOperationType.ATOMIC_INSERT:
                method = getattr(manager, f"execute_{op.value}")
                assert method.call_count == 0, f"execute_{op.value} was called despite being filtered out"


class TestOperationOrdering:
    def test_phase_a_ops_appear_before_phase_b_in_results(self):
        b = TransactionPrimitivesBenchmark()
        manager = _make_full_mock_manager()

        results = _run_workload(b, manager)
        ids = [r["query_id"] for r in results]

        phase_a = [
            TransactionOperationType.ATOMIC_INSERT.value,
            TransactionOperationType.ATOMIC_UPDATE.value,
            TransactionOperationType.ATOMIC_DELETE.value,
            TransactionOperationType.ATOMIC_MERGE.value,
        ]
        phase_b = [
            TransactionOperationType.ROLLBACK_TO_VERSION.value,
            TransactionOperationType.ROLLBACK_TO_TIMESTAMP.value,
            TransactionOperationType.TIME_TRAVEL_QUERY.value,
            TransactionOperationType.VERSION_COMPARE.value,
        ]

        last_phase_a = max(ids.index(op) for op in phase_a)
        first_phase_b = min(ids.index(op) for op in phase_b)
        assert last_phase_a < first_phase_b, (
            "All Phase A (write) operations must complete before any Phase B (version) operations"
        )

    def test_rollback_to_version_runs_after_writes(self):
        b = TransactionPrimitivesBenchmark()
        manager = _make_full_mock_manager()

        results = _run_workload(b, manager)
        ids = [r["query_id"] for r in results]

        insert_idx = ids.index(TransactionOperationType.ATOMIC_INSERT.value)
        rollback_idx = ids.index(TransactionOperationType.ROLLBACK_TO_VERSION.value)
        assert insert_idx < rollback_idx, "ATOMIC_INSERT must appear before ROLLBACK_TO_VERSION"


class TestTableLifecycle:
    def test_setup_transaction_table_called_once_per_iteration(self):
        b = TransactionPrimitivesBenchmark()
        manager = _make_full_mock_manager()

        ctx, adapter, benchmark_config = _make_ctx_adapter_config(options={"power_iterations": 2})
        with (
            patch.object(b, "get_dataframe_operations", return_value=manager),
            patch.object(b, "_setup_transaction_table") as mock_setup,
            patch.object(b, "_create_test_orders_df", return_value=MagicMock()),
        ):
            b.execute_dataframe_workload(
                ctx=ctx,
                adapter=adapter,
                benchmark_config=benchmark_config,
            )
        assert mock_setup.call_count == 2, (
            f"_setup_transaction_table should be called once per iteration, got {mock_setup.call_count}"
        )

    def test_temp_dir_cleaned_up_on_completion(self):
        b = TransactionPrimitivesBenchmark()
        manager = _make_full_mock_manager()

        ctx, adapter, benchmark_config = _make_ctx_adapter_config()
        with (
            patch.object(b, "get_dataframe_operations", return_value=manager),
            patch.object(b, "_setup_transaction_table"),
            patch.object(b, "_create_test_orders_df", return_value=MagicMock()),
            patch("benchbox.core.transaction_primitives.benchmark.shutil.rmtree") as mock_rmtree,
            patch(
                "benchbox.core.transaction_primitives.benchmark.tempfile.mkdtemp", return_value="/tmp/benchbox_txn_test"
            ),
        ):
            b.execute_dataframe_workload(
                ctx=ctx,
                adapter=adapter,
                benchmark_config=benchmark_config,
            )
        mock_rmtree.assert_called_once()
        call_args = mock_rmtree.call_args
        assert "/tmp/benchbox_txn_test" in call_args[0][0]

    def test_temp_dir_cleaned_up_on_exception(self):
        b = TransactionPrimitivesBenchmark()
        manager = _make_full_mock_manager()
        manager.supports_operation.side_effect = RuntimeError("simulated failure")

        ctx, adapter, benchmark_config = _make_ctx_adapter_config()
        with (
            patch.object(b, "get_dataframe_operations", return_value=manager),
            patch.object(b, "_setup_transaction_table"),
            patch.object(b, "_create_test_orders_df", return_value=MagicMock()),
            patch("benchbox.core.transaction_primitives.benchmark.shutil.rmtree") as mock_rmtree,
            patch(
                "benchbox.core.transaction_primitives.benchmark.tempfile.mkdtemp", return_value="/tmp/benchbox_txn_err"
            ),
        ):
            with pytest.raises(RuntimeError, match="simulated failure"):
                b.execute_dataframe_workload(
                    ctx=ctx,
                    adapter=adapter,
                    benchmark_config=benchmark_config,
                )
        mock_rmtree.assert_called_once()


class TestIterations:
    def test_default_iteration_is_one(self):
        b = TransactionPrimitivesBenchmark()
        manager = _make_full_mock_manager()

        results = _run_workload(b, manager)
        iterations_seen = {r["iteration"] for r in results}
        assert max(iterations_seen) == 1

    def test_two_iterations_yields_two_results_per_op(self):
        b = TransactionPrimitivesBenchmark()
        manager = _make_full_mock_manager()

        results = _run_workload(b, manager, options={"power_iterations": 2})
        by_query: dict[str, list[dict]] = {}
        for r in results:
            by_query.setdefault(r["query_id"], []).append(r)

        for op_id, rows in by_query.items():
            assert len(rows) == 2, f"Expected 2 results for {op_id}, got {len(rows)}"
            assert rows[0]["iteration"] == 1
            assert rows[1]["iteration"] == 2

    def test_iteration_count_increments_per_op(self):
        b = TransactionPrimitivesBenchmark()
        manager = _make_full_mock_manager()

        results = _run_workload(b, manager, options={"power_iterations": 3})
        atomic_insert_iters = [r["iteration"] for r in results if r["query_id"] == "atomic_insert"]
        assert atomic_insert_iters == [1, 2, 3]


