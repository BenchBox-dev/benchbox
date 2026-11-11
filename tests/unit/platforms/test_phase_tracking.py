from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from benchbox.platforms.base.phase_tracking import (
    PhaseTrackingMixin,
    _extract_table_names,
    _resolve_benchmark_table_names,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _Adapter(PhaseTrackingMixin):
    def __init__(self):
        self.logger = MagicMock()


class _MappingWithBrokenKeys:
    def keys(self):
        raise TypeError("keys unavailable")


class _RowsThatFail:
    def __iter__(self):
        raise RuntimeError("cannot iterate rows")


class _TablesWithBadItems:
    def items(self):
        return None


class _TablesWithItemsThatRaise:
    def items(self):
        raise TypeError("items unavailable")


class _ItemsThatRejectIter:
    def items(self):
        return self

    def __iter__(self):
        raise TypeError("not iterable")


class _ItemsThatFailDuringIteration:
    def items(self):
        return self

    def __iter__(self):
        return self

    def __next__(self):
        raise TypeError("cannot consume table items")


class _CursorConnection:
    def __init__(self, rows=None, *, fail_cursor=False, fetchone=None):
        self.rows = rows or []
        self.fail_cursor = fail_cursor
        self.fetchone_result = fetchone
        self.cursor_obj = MagicMock()
        self.cursor_obj.fetchall.return_value = self.rows
        self.cursor_obj.fetchone.return_value = self.fetchone_result

    def cursor(self):
        if self.fail_cursor:
            raise RuntimeError("cursor unavailable")
        return self.cursor_obj


class _ExecuteConnection:
    def __init__(self, rows=None, *, fail=False):
        self.rows = rows or []
        self.fail = fail
        self.result = MagicMock()
        self.result.fetchall.return_value = self.rows

    def execute(self, query):
        if self.fail:
            raise RuntimeError("execute unavailable")
        return self.result


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, []),
        ({"Orders": object(), "LineItem": object()}, ["Orders", "LineItem"]),
        ("orders", ["orders"]),
        (("orders", 7), ["orders", "7"]),
        (_MappingWithBrokenKeys(), []),
        (object(), []),
    ],
)
def test_extract_table_names_coerces_supported_shapes(raw, expected):
    assert _extract_table_names(raw) == expected


def test_resolve_benchmark_table_names_prefers_api_then_tables():
    api_benchmark = SimpleNamespace(get_table_names=lambda: {"Orders": object()}, tables={"fallback": object()})
    fallback_benchmark = SimpleNamespace(get_table_names=list, tables={"LineItem": object()})
    empty_benchmark = SimpleNamespace(tables=[])

    assert _resolve_benchmark_table_names(api_benchmark) == ["Orders"]
    assert _resolve_benchmark_table_names(fallback_benchmark) == ["LineItem"]
    assert _resolve_benchmark_table_names(empty_benchmark) == []
    assert (
        _resolve_benchmark_table_names(SimpleNamespace(get_table_names=lambda: (_ for _ in ()).throw(RuntimeError())))
        == []
    )


def test_data_generation_phase_uses_tables_from_impl_when_public_tables_absent():
    adapter = _Adapter()
    benchmark = SimpleNamespace(_impl=SimpleNamespace(tables={"orders": [{"id": 1}, {"id": 2}]}))

    phase = adapter._create_enhanced_data_generation_phase(benchmark)

    assert phase is not None
    assert phase.status == "SUCCESS"
    assert phase.tables_generated == 1
    assert phase.total_rows_generated == 2
    assert phase.per_table_stats["orders"].rows_generated == 2
    assert phase.per_table_stats["orders"].data_size_bytes == 18


@pytest.mark.parametrize(
    "benchmark_obj",
    [
        SimpleNamespace(),
        SimpleNamespace(tables=[]),
        SimpleNamespace(tables=_TablesWithBadItems()),
        SimpleNamespace(tables=_TablesWithItemsThatRaise()),
        SimpleNamespace(tables=_ItemsThatRejectIter()),
        SimpleNamespace(tables=_ItemsThatFailDuringIteration()),
    ],
)
def test_data_generation_phase_returns_none_for_missing_or_malformed_tables(benchmark_obj):
    assert _Adapter()._create_enhanced_data_generation_phase(benchmark_obj) is None


def test_data_generation_phase_records_failed_tables_and_partial_status():
    adapter = _Adapter()
    benchmark = SimpleNamespace(tables={"orders": [{"id": 1}], "lineitem": _RowsThatFail()})

    phase = adapter._create_enhanced_data_generation_phase(benchmark)

    assert phase is not None
    assert phase.status == "PARTIAL_FAILURE"
    assert phase.tables_generated == 1
    assert phase.per_table_stats["lineitem"].status == "FAILED"
    assert phase.per_table_stats["lineitem"].error_type == "GENERATION_ERROR"


def test_data_generation_phase_handles_empty_and_non_iterable_table_payloads():
    adapter = _Adapter()
    benchmark = SimpleNamespace(tables={"empty": [], "metadata": "not row data"})

    phase = adapter._create_enhanced_data_generation_phase(benchmark)

    assert phase is not None
    assert phase.status == "SUCCESS"
    assert phase.tables_generated == 1
    assert phase.per_table_stats["empty"].rows_generated == 0
    assert phase.per_table_stats["empty"].data_size_bytes == 0
    assert "metadata" not in phase.per_table_stats


def test_data_generation_phase_reports_failed_when_all_tables_fail():
    adapter = _Adapter()
    benchmark = SimpleNamespace(tables={"orders": _RowsThatFail()})

    phase = adapter._create_enhanced_data_generation_phase(benchmark)

    assert phase is not None
    assert phase.status == "FAILED"
    assert phase.tables_generated == 0


def test_schema_creation_phase_builds_per_table_stats_and_empty_phase():
    adapter = _Adapter()
    benchmark = SimpleNamespace(get_table_names=lambda: ["orders", "lineitem"])

    phase = adapter._create_enhanced_schema_creation_phase(benchmark, connection=object(), schema_creation_time=0.005)
    empty_phase = adapter._create_enhanced_schema_creation_phase(SimpleNamespace(), object(), 0.0)

    assert phase.duration_ms == 5
    assert phase.tables_created == 2
    assert phase.constraints_applied == 2
    assert phase.indexes_created == 2
    assert phase.per_table_creation["orders"].creation_time_ms == 2
    assert empty_phase.tables_created == 0
    assert empty_phase.per_table_creation == {}


def test_data_loading_phase_distributes_time_by_rows_without_actual_timings():
    phase = _Adapter()._create_enhanced_data_loading_phase({"orders": 3, "lineitem": 1}, loading_time=0.4)

    assert phase.duration_ms == 400
    assert phase.total_rows_loaded == 4
    assert phase.tables_loaded == 2
    assert phase.per_table_stats["orders"].load_time_ms == 300
    assert phase.per_table_stats["lineitem"].load_time_ms == 100


def test_data_loading_phase_uses_actual_per_table_timings_and_handles_zero_rows():
    adapter = _Adapter()

    timed_phase = adapter._create_enhanced_data_loading_phase(
        {"orders": 3, "lineitem": 1},
        loading_time=0.4,
        per_table_timings={"orders": {"total_ms": 125.9}},
    )
    empty_phase = adapter._create_enhanced_data_loading_phase({"empty": 0}, loading_time=0.4)

    assert timed_phase.per_table_stats["orders"].load_time_ms == 125
    assert timed_phase.per_table_stats["lineitem"].load_time_ms == 0
    assert empty_phase.total_rows_loaded == 0
    assert empty_phase.per_table_stats["empty"].load_time_ms == 0


def test_data_loading_phase_ignores_non_dict_per_table_timings():

    phase = _Adapter()._create_enhanced_data_loading_phase(
        {"orders": 3},
        loading_time=0.4,
        per_table_timings={"orders": 1.5},
    )

    assert phase.per_table_stats["orders"].load_time_ms == 0


def test_data_loading_phase_malformed_total_ms_degrades_to_zero():

    phase = _Adapter()._create_enhanced_data_loading_phase(
        {"a": 1, "b": 2},
        loading_time=0.4,
        per_table_timings={"a": {"total_ms": None}, "b": {"total_ms": "n/a"}},
    )
    assert phase.per_table_stats["a"].load_time_ms == 0
    assert phase.per_table_stats["b"].load_time_ms == 0

    container_phase = _Adapter()._create_enhanced_data_loading_phase(
        {"a": 1},
        loading_time=0.4,
        per_table_timings="error",
    )
    fallback_phase = _Adapter()._create_enhanced_data_loading_phase({"a": 1}, loading_time=0.4)
    assert container_phase.per_table_stats["a"].load_time_ms == fallback_phase.per_table_stats["a"].load_time_ms


def test_validation_phase_defaults_without_runtime_inputs():
    phase = _Adapter()._create_enhanced_validation_phase()

    assert phase.duration_ms >= 50
    assert phase.row_count_validation == "PASSED"
    assert phase.schema_validation == "PASSED"
    assert phase.data_integrity_checks == "PASSED"
    assert phase.validation_details["row_count_matches"] is True


def test_validation_phase_aggregates_row_schema_and_integrity_details():
    adapter = _Adapter()
    benchmark = SimpleNamespace(expected_row_counts={"orders": 10}, tables={"orders": object()})
    connection = MagicMock()
    adapter._get_existing_tables = MagicMock(return_value=["orders"])

    phase = adapter._create_enhanced_validation_phase(benchmark, connection, {"orders": 10})

    assert phase.row_count_validation == "PASSED"
    assert phase.schema_validation == "PASSED"
    assert phase.data_integrity_checks == "PASSED"
    assert phase.validation_details["verified_tables"] == ["orders"]
    assert phase.validation_details["accessible_tables"] == ["orders"]


def test_validate_table_row_counts_reports_empty_and_insufficient_tables():
    adapter = _Adapter()
    benchmark = SimpleNamespace(expected_row_counts={"orders": 10, "lineitem": 3})

    empty_status, empty_details = adapter._validate_table_row_counts(benchmark, {"orders": 0})
    partial_status, partial_details = adapter._validate_table_row_counts(benchmark, {"orders": 5, "lineitem": 3})
    passed_status, passed_details = adapter._validate_table_row_counts(SimpleNamespace(), {"orders": 1})

    assert empty_status == "FAILED"
    assert empty_details["empty_tables"] == ["orders"]
    assert partial_status == "PARTIAL"
    assert partial_details["insufficient_data_tables"] == [{"table": "orders", "actual": 5, "expected_minimum": 10}]
    assert passed_status == "PASSED"
    assert passed_details["tables_with_data"] == 1


def test_validate_schema_integrity_reports_pass_missing_and_errors():
    adapter = _Adapter()
    benchmark = SimpleNamespace(tables={"orders": object(), "lineitem": object()})

    pass_status, pass_details = adapter._validate_schema_integrity(
        benchmark, _CursorConnection(rows=[("orders",), ("lineitem",)])
    )
    missing_status, missing_details = adapter._validate_schema_integrity(
        benchmark, _CursorConnection(rows=[("orders",)])
    )

    adapter._get_expected_tables = MagicMock(side_effect=RuntimeError("bad schema"))
    error_status, error_details = adapter._validate_schema_integrity(benchmark, object())

    assert pass_status == "PASSED"
    assert pass_details["verified_tables"] == ["orders", "lineitem"]
    assert missing_status == "FAILED"
    assert missing_details["missing_tables"] == ["lineitem"]
    assert error_status == "FAILED"
    assert error_details["validation_error"] == "bad schema"


def test_validate_schema_integrity_passes_when_no_expected_tables_declared():
    adapter = _Adapter()
    adapter._get_expected_tables = MagicMock(return_value=None)
    adapter._get_existing_tables = MagicMock(return_value=["orders"])

    status, details = adapter._validate_schema_integrity(SimpleNamespace(), object())

    assert status == "PASSED"
    assert details["schema_valid"] is True
    assert details["verified_tables"] == ["orders"]


def test_validate_data_integrity_reports_accessible_and_inaccessible_tables():
    adapter = _Adapter()
    good_connection = MagicMock()
    bad_connection = MagicMock()
    bad_connection.cursor.side_effect = [MagicMock(), RuntimeError("missing table")]

    passed_status, passed_details = adapter._validate_data_integrity(SimpleNamespace(), good_connection, {"orders": 1})
    failed_status, failed_details = adapter._validate_data_integrity(
        SimpleNamespace(), bad_connection, {"orders": 1, "lineitem": 1}
    )

    assert passed_status == "PASSED"
    assert passed_details["accessible_tables"] == ["orders"]
    assert failed_status == "FAILED"
    assert failed_details["inaccessible_tables"] == ["lineitem"]


def test_validate_data_integrity_reports_outer_errors():
    adapter = _Adapter()
    table_stats = MagicMock()
    table_stats.__iter__.side_effect = RuntimeError("cannot iterate stats")

    status, details = adapter._validate_data_integrity(SimpleNamespace(), MagicMock(), table_stats)

    assert status == "FAILED"
    assert details["integrity_error"] == "cannot iterate stats"


def test_expected_row_counts_are_optional():
    adapter = _Adapter()

    assert adapter._get_expected_row_counts(SimpleNamespace(expected_row_counts={"orders": 1})) == {"orders": 1}
    assert adapter._get_expected_row_counts(SimpleNamespace()) is None


def test_get_table_row_count_uses_cursor_and_returns_zero_on_empty_or_error():
    adapter = _Adapter()

    assert adapter.get_table_row_count(_CursorConnection(fetchone=(12,)), "orders") == 12
    assert adapter.get_table_row_count(_CursorConnection(fetchone=None), "orders") == 0
    assert adapter.get_table_row_count(_CursorConnection(fail_cursor=True), "orders") == 0


def test_get_expected_tables_fallbacks_and_exception_paths():
    adapter = _Adapter()

    assert adapter._get_expected_tables(SimpleNamespace(get_available_tables=lambda: ["Orders", 3])) == ["orders", "3"]
    assert (
        adapter._get_expected_tables(SimpleNamespace(get_schema=lambda: (_ for _ in ()).throw(RuntimeError()))) is None
    )
    assert adapter._get_expected_tables(
        SimpleNamespace(
            get_schema=list,
            get_available_tables=lambda: (_ for _ in ()).throw(RuntimeError()),
            tables={"Fallback": object()},
        )
    ) == ["fallback"]
    assert adapter._get_expected_tables(SimpleNamespace(tables=_MappingWithBrokenKeys())) is None


def test_get_existing_tables_uses_cursor_then_execute_fallback_then_empty_on_failure():
    adapter = _Adapter()
    cursor_connection = _CursorConnection(rows=[("Orders",), ("LINEITEM",)])
    execute_connection = _ExecuteConnection(rows=[("Part",), ("Supplier",)])
    failing_connection = _ExecuteConnection(fail=True)
    failing_connection.cursor = MagicMock(side_effect=RuntimeError("cursor unavailable"))

    assert adapter._get_existing_tables(cursor_connection) == ["orders", "lineitem"]
    cursor_connection.cursor_obj.close.assert_called_once_with()
    assert adapter._get_existing_tables(execute_connection) == ["part", "supplier"]
    assert adapter._get_existing_tables(failing_connection) == []


