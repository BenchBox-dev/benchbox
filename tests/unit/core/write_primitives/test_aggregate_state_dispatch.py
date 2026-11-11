from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from benchbox.core.write_primitives.benchmark import WritePrimitivesBenchmark
from benchbox.core.write_primitives.catalog.loader import (
    AggregateStateSpec,
    ValidationQuery,
    WriteOperation,
)
from benchbox.core.write_primitives.dataframe_operations import (
    WriteOperationType,
)

from ._mock_manager import MockDataFrameWriteOperationsManager, make_result

pytestmark = [pytest.mark.fast, pytest.mark.unit]


def _make_op(
    *,
    op_id: str = "sketch_df_hll_persist_merge",
    sketch_type: str = "hll",
    supported_platforms: tuple[str, ...] = ("pyspark",),
    bound: tuple[float, float] | None = (0.0, 1000.0),
) -> WriteOperation:
    spec = AggregateStateSpec(
        sketch_type=sketch_type,
        source_table="lineitem",
        target_subdir=f"{op_id}/state",
        group_cols=["l_shipdate"],
        value_col="l_orderkey",
        sketch_alias="sketch",
        supported_platforms=list(supported_platforms),
    )
    if bound is None:
        vqs: list[ValidationQuery] = []
    else:
        vqs = [
            ValidationQuery(
                id=f"{op_id}_bound",
                sql="SELECT 1",
                expected_value_min=bound[0],
                expected_value_max=bound[1],
            )
        ]
    return WriteOperation(
        id=op_id,
        category="sketch",
        description="aggregate-state op for dispatch tests",
        write_sql="-- aggregate-state placeholder",
        validation_queries=vqs,
        aggregate_state=spec,
    )


@pytest.fixture()
def adapter_with_spark() -> SimpleNamespace:
    spark = SimpleNamespace(version="4.1.0")
    return SimpleNamespace(platform_name="pyspark", spark=spark)


@pytest.fixture()
def wp_benchmark(tmp_path: Path) -> WritePrimitivesBenchmark:
    return WritePrimitivesBenchmark(output_dir=tmp_path)


def _patch_dispatch_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    *,
    manager: MockDataFrameWriteOperationsManager | None,
    pyspark_topk_supported: bool = True,
    persist_builder_returns: Any = "stub_persist_builder",
    merge_extract_returns: Any = "stub_merge_extract",
) -> None:
    import benchbox.core.write_primitives.dataframe_operations as dfo

    monkeypatch.setattr(dfo, "get_dataframe_write_manager", lambda *a, **kw: manager)
    monkeypatch.setattr(dfo, "pyspark_supports_approx_top_k", lambda spark: pyspark_topk_supported)
    monkeypatch.setattr(dfo, "make_pyspark_hll_persist_builder", lambda **kw: persist_builder_returns)
    monkeypatch.setattr(dfo, "make_pyspark_hll_merge_extract", lambda **kw: merge_extract_returns)
    monkeypatch.setattr(dfo, "make_pyspark_topk_persist_builder", lambda **kw: persist_builder_returns)
    monkeypatch.setattr(dfo, "make_pyspark_topk_merge_extract", lambda **kw: merge_extract_returns)


def _stub_get_operation(
    monkeypatch: pytest.MonkeyPatch, wp_benchmark: WritePrimitivesBenchmark, op: WriteOperation
) -> None:
    monkeypatch.setattr(wp_benchmark.operations_manager, "get_operation", lambda _id: op)


def _stub_source_path(monkeypatch: pytest.MonkeyPatch, wp_benchmark: WritePrimitivesBenchmark, tmp_path: Path) -> Path:
    stub = tmp_path / "stub_source"
    stub.mkdir(exist_ok=True)
    monkeypatch.setattr(
        wp_benchmark,
        "_resolve_aggregate_source_path",
        lambda *args, **kwargs: stub,
    )
    return stub


def test_dispatch_success_with_bound_passing_aggregate_value(
    monkeypatch: pytest.MonkeyPatch,
    wp_benchmark: WritePrimitivesBenchmark,
    adapter_with_spark: SimpleNamespace,
    tmp_path: Path,
) -> None:
    op = _make_op(bound=(0.0, 100.0))
    _stub_get_operation(monkeypatch, wp_benchmark, op)
    _stub_source_path(monkeypatch, wp_benchmark, tmp_path)
    manager = MockDataFrameWriteOperationsManager(
        persist_result=make_result(operation_type=WriteOperationType.AGGREGATE_PERSIST, rows_affected=42),
        merge_result=make_result(
            operation_type=WriteOperationType.AGGREGATE_MERGE,
            metrics={"aggregate_value": 50.0},
        ),
    )
    _patch_dispatch_dependencies(monkeypatch, manager=manager)

    result = wp_benchmark._execute_aggregate_state_op(op.id, adapter=adapter_with_spark)

    assert result["status"] == "SUCCESS", result
    assert result["query_id"] == op.id
    assert result["rows_returned"] == 42
    assert result["aggregate_value"] == 50.0
    assert result["execution_time_seconds"] >= 0.0


def test_dispatch_persist_failure_returns_failed_envelope(
    monkeypatch: pytest.MonkeyPatch,
    wp_benchmark: WritePrimitivesBenchmark,
    adapter_with_spark: SimpleNamespace,
    tmp_path: Path,
) -> None:
    op = _make_op()
    _stub_get_operation(monkeypatch, wp_benchmark, op)
    _stub_source_path(monkeypatch, wp_benchmark, tmp_path)
    manager = MockDataFrameWriteOperationsManager(
        persist_result=make_result(
            operation_type=WriteOperationType.AGGREGATE_PERSIST,
            success=False,
            rows_affected=0,
            error_message="mock persist boom",
        ),
    )
    _patch_dispatch_dependencies(monkeypatch, manager=manager)

    result = wp_benchmark._execute_aggregate_state_op(op.id, adapter=adapter_with_spark)

    assert result["status"] == "FAILED", result

    assert result["rows_returned"] == 0
    assert manager.last_merge_path is None, "merge should not run after persist failure"
    assert result["error"] == "AGGREGATE_PERSIST failed: mock persist boom"


def test_dispatch_merge_failure_returns_failed_envelope_with_persist_rows(
    monkeypatch: pytest.MonkeyPatch,
    wp_benchmark: WritePrimitivesBenchmark,
    adapter_with_spark: SimpleNamespace,
    tmp_path: Path,
) -> None:
    op = _make_op()
    _stub_get_operation(monkeypatch, wp_benchmark, op)
    _stub_source_path(monkeypatch, wp_benchmark, tmp_path)
    manager = MockDataFrameWriteOperationsManager(
        persist_result=make_result(operation_type=WriteOperationType.AGGREGATE_PERSIST, rows_affected=99),
        merge_result=make_result(
            operation_type=WriteOperationType.AGGREGATE_MERGE,
            success=False,
            rows_affected=0,
            error_message="mock merge boom",
        ),
    )
    _patch_dispatch_dependencies(monkeypatch, manager=manager)

    result = wp_benchmark._execute_aggregate_state_op(op.id, adapter=adapter_with_spark)

    assert result["status"] == "FAILED", result
    assert result["rows_returned"] == 99
    assert manager.last_merge_path is not None, "dispatch should reach merge before failing"
    assert result["error"] == "AGGREGATE_MERGE failed: mock merge boom"


def test_dispatch_bound_violation_returns_failed_envelope_with_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
    wp_benchmark: WritePrimitivesBenchmark,
    adapter_with_spark: SimpleNamespace,
    tmp_path: Path,
) -> None:
    op = _make_op(bound=(0.0, 10.0))
    _stub_get_operation(monkeypatch, wp_benchmark, op)
    _stub_source_path(monkeypatch, wp_benchmark, tmp_path)
    manager = MockDataFrameWriteOperationsManager(
        persist_result=make_result(operation_type=WriteOperationType.AGGREGATE_PERSIST, rows_affected=33),
        merge_result=make_result(
            operation_type=WriteOperationType.AGGREGATE_MERGE,
            metrics={"aggregate_value": 999.0},
        ),
    )
    _patch_dispatch_dependencies(monkeypatch, manager=manager)

    result = wp_benchmark._execute_aggregate_state_op(op.id, adapter=adapter_with_spark)

    assert result["status"] == "FAILED", result
    assert result["rows_returned"] == 33
    assert "outside bounds" in result["error"], result["error"]
    assert "999" in result["error"]


def test_dispatch_skipped_on_unsupported_platform(
    monkeypatch: pytest.MonkeyPatch,
    wp_benchmark: WritePrimitivesBenchmark,
) -> None:
    op = _make_op(supported_platforms=("pyspark",))
    _stub_get_operation(monkeypatch, wp_benchmark, op)

    adapter = SimpleNamespace(platform_name="duckdb", spark=None)

    result = wp_benchmark._execute_aggregate_state_op(op.id, adapter=adapter)

    assert result["status"] == "SKIPPED", result

    assert "duckdb" in result["error"]
    assert "supports" in result["error"]


def test_dispatch_skipped_on_spark_version_for_topk(
    monkeypatch: pytest.MonkeyPatch,
    wp_benchmark: WritePrimitivesBenchmark,
) -> None:
    op = _make_op(op_id="sketch_df_topk_persist_merge", sketch_type="topk")
    _stub_get_operation(monkeypatch, wp_benchmark, op)
    spark = SimpleNamespace(version="3.4.1")
    adapter = SimpleNamespace(platform_name="pyspark", spark=spark)
    manager = MockDataFrameWriteOperationsManager()
    _patch_dispatch_dependencies(monkeypatch, manager=manager, pyspark_topk_supported=False)

    result = wp_benchmark._execute_aggregate_state_op(op.id, adapter=adapter)

    assert result["status"] == "SKIPPED", result

    assert "Spark 4.1" in result["error"]
    assert "3.4.1" in result["error"]

    assert manager.last_persist_path is None
    assert manager.last_merge_path is None


def test_resolve_aggregate_source_path_short_circuits_on_cached_dir(
    wp_benchmark: WritePrimitivesBenchmark,
    tmp_path: Path,
) -> None:
    cache_dir = tmp_path / "_aggregate_state_sources" / "lineitem"
    cache_dir.mkdir(parents=True)
    (cache_dir / "data.parquet").write_bytes(b"PAR1\x00\x00")

    resolved = wp_benchmark._resolve_aggregate_source_path(
        source_table="lineitem",
        adapter=SimpleNamespace(),
        output_dir=tmp_path,
        spark=SimpleNamespace(),
    )

    assert resolved == cache_dir

    assert (cache_dir / "data.parquet").read_bytes() == b"PAR1\x00\x00"


