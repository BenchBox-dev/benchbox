from __future__ import annotations

from types import SimpleNamespace

import pytest

from benchbox.core.dataframe.load_validation import (
    combine_dataframe_validation,
    validate_dataframe_loaded_tables,
    validate_managed_dataframe_tables,
)
from benchbox.core.dataframe.query_validation import DataFrameQueryValidationSummary

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class _Benchmark:
    def __init__(self, tables: list[str]) -> None:
        self._tables = tables

    def get_schema(self) -> dict[str, object]:
        return {name.upper(): {} for name in self._tables}


def _stats(**status_by_table: str) -> dict[str, SimpleNamespace]:
    return {name: SimpleNamespace(status=status) for name, status in status_by_table.items()}


def test_all_expected_tables_loaded_with_rows_passes() -> None:
    summary = validate_dataframe_loaded_tables(
        {"orders": 10, "lineitem": 40},
        _stats(orders="SUCCESS", lineitem="SUCCESS"),
        _Benchmark(["orders", "lineitem"]),
    )

    assert summary.status == "PASSED"
    assert summary.details == {
        "provider": "loaded_table_checks",
        "total_tables_validated": 2,
        "tables_with_data": 2,
    }


def test_empty_table_fails() -> None:
    summary = validate_dataframe_loaded_tables(
        {"orders": 10, "lineitem": 0},
        _stats(orders="SUCCESS", lineitem="SUCCESS"),
        _Benchmark(["orders", "lineitem"]),
    )

    assert summary.status == "FAILED"
    assert summary.details["empty_tables"] == ["lineitem"]


def test_missing_expected_table_fails() -> None:
    summary = validate_dataframe_loaded_tables(
        {"orders": 10},
        _stats(orders="SUCCESS"),
        _Benchmark(["orders", "lineitem"]),
    )

    assert summary.status == "FAILED"
    assert summary.details["missing_tables"] == ["lineitem"]


def test_failed_table_load_status_fails() -> None:
    summary = validate_dataframe_loaded_tables(
        {"orders": 10},
        _stats(orders="FAILED"),
        _Benchmark(["orders"]),
    )

    assert summary.status == "FAILED"
    assert summary.details["failed_tables"] == ["orders"]


def test_no_loaded_tables_fails() -> None:
    summary = validate_dataframe_loaded_tables({}, {}, _Benchmark(["orders"]))

    assert summary.status == "FAILED"
    assert summary.details["reason"] == "no tables were loaded"


def _query_summary(status: str, provider: str = "none") -> DataFrameQueryValidationSummary:
    return DataFrameQueryValidationSummary(status, {"provider": provider, "checked": 0})


def _load_summary(status: str) -> DataFrameQueryValidationSummary:
    return DataFrameQueryValidationSummary(status, {"provider": "loaded_table_checks"})


def test_no_oracle_benchmark_takes_load_validation_status() -> None:
    combined = combine_dataframe_validation(_query_summary("NOT_RUN"), _load_summary("PASSED"))

    assert combined.status == "PASSED"
    assert combined.details["load"] == {"provider": "loaded_table_checks"}
    assert combined.details["provider"] == "none"


def test_load_failure_overrides_passing_oracle() -> None:
    combined = combine_dataframe_validation(
        _query_summary("PASSED", provider="tpc_expected_row_counts"), _load_summary("FAILED")
    )

    assert combined.status == "FAILED"


def test_query_execution_failures_stay_partial() -> None:
    combined = combine_dataframe_validation(_query_summary("PARTIAL"), _load_summary("PASSED"))

    assert combined.status == "PARTIAL"


def test_oracle_not_run_is_not_promoted_by_load_validation() -> None:
    combined = combine_dataframe_validation(
        _query_summary("NOT_RUN", provider="tpc_expected_row_counts"), _load_summary("PASSED")
    )

    assert combined.status == "NOT_RUN"


def test_uncertain_oracle_evidence_is_not_promoted_by_load_validation() -> None:
    combined = combine_dataframe_validation(
        _query_summary("UNCERTAIN", provider="tpc_expected_row_counts"), _load_summary("PASSED")
    )

    assert combined.status == "UNCERTAIN"


def test_missing_load_validation_leaves_query_summary_unchanged() -> None:
    query_summary = _query_summary("NOT_RUN")

    assert combine_dataframe_validation(query_summary, None) is query_summary


class _ManagedBenchmark(_Benchmark):
    def __init__(self, loaded: tuple[dict[str, int], list[str]] | None) -> None:
        super().__init__(["orders", "lineitem", "staging"])
        self._loaded = loaded

    def dataframe_loaded_tables(self, *, ctx: object, adapter: object):
        return self._loaded


def test_managed_tables_validate_against_hook_expected_tables() -> None:
    benchmark = _ManagedBenchmark(({"orders": 3, "lineitem": 9}, ["orders", "lineitem"]))

    summary = validate_managed_dataframe_tables(benchmark, None, None)

    assert summary is not None
    assert summary.status == "PASSED"


def test_managed_tables_missing_expected_table_fails() -> None:
    benchmark = _ManagedBenchmark(({"orders": 3}, ["orders", "lineitem"]))

    summary = validate_managed_dataframe_tables(benchmark, None, None)

    assert summary is not None
    assert summary.status == "FAILED"
    assert summary.details["missing_tables"] == ["lineitem"]


def test_managed_tables_without_loaded_evidence_returns_none() -> None:
    assert validate_managed_dataframe_tables(_ManagedBenchmark(None), None, None) is None
    assert validate_managed_dataframe_tables(_Benchmark(["orders"]), None, None) is None
