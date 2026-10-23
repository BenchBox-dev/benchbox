from __future__ import annotations

import pytest

from benchbox.core.results.models import (
    QUERY_RUN_TYPE_MEASUREMENT,
    QUERY_RUN_TYPE_METADATA,
    QUERY_RUN_TYPE_SUMMARY,
    QUERY_RUN_TYPE_WARMUP,
    QUERY_RUN_TYPES,
)
from benchbox.core.results.query_execution import QueryExecutionContractError
from benchbox.core.results.query_normalizer import (
    QueryResultInput,
    format_query_id,
    normalize_query_id,
    normalize_query_result,
    normalize_query_results,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_canonical_query_run_type_constants_are_defined() -> None:
    assert QUERY_RUN_TYPE_WARMUP in QUERY_RUN_TYPES
    assert QUERY_RUN_TYPE_MEASUREMENT in QUERY_RUN_TYPES
    assert QUERY_RUN_TYPE_METADATA in QUERY_RUN_TYPES
    assert QUERY_RUN_TYPE_SUMMARY in QUERY_RUN_TYPES


class TestNormalizeQueryId:
    def test_normalize_with_q_prefix(self) -> None:

        assert normalize_query_id("Q1") == "1"
        assert normalize_query_id("Q21") == "21"
        assert normalize_query_id("Q99") == "99"

    def test_normalize_lowercase_q_prefix(self) -> None:

        assert normalize_query_id("q1") == "1"
        assert normalize_query_id("q21") == "21"

    def test_normalize_without_prefix(self) -> None:

        assert normalize_query_id("1") == "1"
        assert normalize_query_id("21") == "21"
        assert normalize_query_id("99") == "99"

    def test_normalize_query_prefix(self) -> None:

        assert normalize_query_id("QUERY_1") == "1"
        assert normalize_query_id("query_21") == "21"
        assert normalize_query_id("QUERY1") == "1"

    def test_normalize_integer_input(self) -> None:

        assert normalize_query_id(1) == "1"
        assert normalize_query_id(21) == "21"

    def test_normalize_with_whitespace(self) -> None:

        assert normalize_query_id("  Q1  ") == "1"
        assert normalize_query_id("Q21 ") == "21"

    def test_normalize_with_suffix(self) -> None:

        assert normalize_query_id("Q1.sql") == "1"
        assert normalize_query_id("query_12.txt") == "12"

    def test_normalize_preserves_ssb_dot_notation(self) -> None:
        assert normalize_query_id("Q1.1") == "1.1"
        assert normalize_query_id("Q1.2") == "1.2"
        assert normalize_query_id("Q1.3") == "1.3"
        assert normalize_query_id("Q2.1") == "2.1"
        assert normalize_query_id("Q3.1") == "3.1"
        assert normalize_query_id("Q3.2") == "3.2"
        assert normalize_query_id("Q3.3") == "3.3"
        assert normalize_query_id("Q3.4") == "3.4"
        assert normalize_query_id("Q4.1") == "4.1"
        assert normalize_query_id("Q4.2") == "4.2"
        assert normalize_query_id("Q4.3") == "4.3"
        assert normalize_query_id("Q1.sql") == "1"
        assert normalize_query_id("3.1.sql") == "3.1"

    def test_normalize_preserves_variant_suffix(self) -> None:
        assert normalize_query_id("14a") == "14a"
        assert normalize_query_id("Q14A") == "14a"
        assert normalize_query_id("query_39b") == "39b"

    def test_normalize_strips_legacy_uppercase_q_from_named_queries(self) -> None:
        assert normalize_query_id("Qfilter_decimal_selective") == "filter_decimal_selective"
        assert normalize_query_id("Qaggregation_groupby_large") == "aggregation_groupby_large"
        assert normalize_query_id("Qempty_build_join") == "empty_build_join"
        assert normalize_query_id("Qtopn") == "topn"
        assert normalize_query_id("Qqualify_cume_dist") == "qualify_cume_dist"

    def test_normalize_preserves_lowercase_q_named_queries(self) -> None:
        assert normalize_query_id("qualify_cume_dist") == "qualify_cume_dist"
        assert normalize_query_id("qualify_dense_rank") == "qualify_dense_rank"
        assert normalize_query_id("qualify_lag_lead") == "qualify_lag_lead"
        assert normalize_query_id("qualify_ntile") == "qualify_ntile"
        assert normalize_query_id("qualify_percentile") == "qualify_percentile"
        assert normalize_query_id("qualify_row_number") == "qualify_row_number"
        assert normalize_query_id("quantile_cont") == "quantile_cont"
        assert normalize_query_id("queue_depth") == "queue_depth"

    def test_normalize_preserves_named_ids_starting_with_query(self) -> None:
        assert normalize_query_id("query_cache_probe") == "query_cache_probe"
        assert normalize_query_id("QUERY_CACHE_PROBE") == "QUERY_CACHE_PROBE"
        assert normalize_query_id("query_") == "query_"

    def test_normalize_strips_query_and_q_before_digits_only(self) -> None:
        assert normalize_query_id("query22") == "22"
        assert normalize_query_id("QUERY18") == "18"
        assert normalize_query_id("QUERY_1") == "1"
        assert normalize_query_id("query_22") == "22"
        assert normalize_query_id("Q18.sql") == "18"
        assert normalize_query_id("q14a") == "14a"
        assert normalize_query_id("Q14A") == "14a"

    def test_normalize_preserves_bare_names_without_q(self) -> None:
        assert normalize_query_id("filter_decimal_selective") == "filter_decimal_selective"
        assert normalize_query_id("aggregation_groupby") == "aggregation_groupby"


class TestFormatQueryId:
    def test_format_with_prefix(self) -> None:

        assert format_query_id("1") == "Q1"
        assert format_query_id("21") == "Q21"
        assert format_query_id("Q1") == "Q1"

    def test_format_without_prefix(self) -> None:

        assert format_query_id("1", with_prefix=False) == "1"
        assert format_query_id("Q1", with_prefix=False) == "1"

    def test_format_integer_input(self) -> None:

        assert format_query_id(1) == "Q1"
        assert format_query_id(21) == "Q21"

    def test_format_numeric_variant_and_dotted_ids(self) -> None:
        assert format_query_id("14a") == "Q14a"
        assert format_query_id("Q1.1") == "Q1.1"

    def test_format_prefixes_named_ids_and_normalize_round_trips(self) -> None:
        assert format_query_id("qualify_cume_dist") == "Qqualify_cume_dist"
        assert format_query_id("qualify_cume_dist", with_prefix=False) == "qualify_cume_dist"

    @pytest.mark.parametrize(
        "query_id",
        ["qualify_cume_dist", "filter_decimal_selective", "obt_row_count", "obt_top_items_by_quantity", "14a", "1"],
    )
    def test_format_then_normalize_round_trips(self, query_id: str) -> None:
        assert normalize_query_id(format_query_id(query_id)) == normalize_query_id(query_id)


class TestNormalizeQueryResult:
    def test_normalize_basic_result(self) -> None:

        raw = {
            "query_id": "Q1",
            "execution_time_seconds": 1.5,
            "rows_returned": 100,
            "status": "SUCCESS",
        }
        result = normalize_query_result(raw)

        assert result.query_id == "1"
        assert result.execution_time_seconds == 1.5
        assert result.rows_returned == 100
        assert result.status == "SUCCESS"
        assert result.iteration == 1
        assert result.stream_id == 0
        assert result.run_type == "measurement"

    def test_normalize_with_iteration_and_stream(self) -> None:

        raw = {
            "query_id": "Q1",
            "execution_time_seconds": 1.5,
            "rows_returned": 100,
            "status": "SUCCESS",
            "iteration": 2,
            "stream_id": 3,
        }
        result = normalize_query_result(raw)

        assert result.iteration == 2
        assert result.stream_id == 3
        assert result.run_type == "measurement"

    def test_normalize_preserves_explicit_run_type_over_iteration_inference(self) -> None:
        raw = {
            "query_id": "Q1",
            "execution_time_seconds": 1.5,
            "rows_returned": 100,
            "status": "SUCCESS",
            "iteration": 1,
            "run_type": "warmup",
        }
        result = normalize_query_result(raw)

        assert result.iteration == 1
        assert result.run_type == "warmup"

    def test_normalize_preserves_zero_iteration(self) -> None:
        raw = {
            "query_id": "Q1",
            "execution_time_seconds": 1.5,
            "rows_returned": 100,
            "status": "SUCCESS",
            "iteration": 0,
            "stream_id": 0,
        }
        result = normalize_query_result(raw)

        assert result.iteration == 0
        assert result.stream_id == 0
        assert result.run_type == "warmup"

    def test_normalize_execution_time_from_ms(self) -> None:

        raw = {
            "query_id": "Q1",
            "execution_time_ms": 1500,
            "rows_returned": 100,
            "status": "SUCCESS",
        }
        result = normalize_query_result(raw)

        assert result.execution_time_seconds == 1.5

    def test_normalize_alternative_field_names(self) -> None:

        raw = {
            "id": "Q1",
            "execution_time_ms": 1500,
            "rows": 100,
            "status": "SUCCEEDED",
        }
        result = normalize_query_result(raw)

        assert result.query_id == "1"
        assert result.execution_time_seconds == 1.5
        assert result.rows_returned == 100
        assert result.status == "SUCCESS"

    def test_normalize_rejects_conflicting_duration_aliases(self) -> None:
        raw = {
            "query_id": "Q1",
            "execution_time_seconds": 1.25,
            "execution_time": 9.99,
            "execution_time_ms": 9999,
            "rows_returned": 100,
            "status": "SUCCESS",
        }
        with pytest.raises(QueryExecutionContractError, match="Conflicting query duration representations"):
            normalize_query_result(raw)

    def test_normalize_treats_documented_legacy_execution_time_as_seconds(self) -> None:
        raw = {
            "query_id": "Q1",
            "execution_time": 1.5,
            "rows_returned": 100,
            "status": "SUCCESS",
        }
        result = normalize_query_result(raw)

        assert result.execution_time_seconds == 1.5

    @pytest.mark.parametrize("field", ["rows_returned", "rows", "result_count"])
    def test_normalize_preserves_zero_row_aliases(self, field: str) -> None:
        result = normalize_query_result(
            {
                "query_id": "Q1",
                "execution_time_seconds": 0.0,
                field: 0,
                "status": "SUCCESS",
            }
        )

        assert result.rows_returned == 0

    def test_normalize_failed_result(self) -> None:

        raw = {
            "query_id": "Q1",
            "execution_time_seconds": 0.0,
            "rows_returned": 0,
            "status": "FAILED",
            "error_message": "Timeout occurred",
        }
        result = normalize_query_result(raw)

        assert result.status == "FAILED"
        assert result.error_message == "Timeout occurred"

    def test_normalize_with_cost(self) -> None:

        raw = {
            "query_id": "Q1",
            "execution_time_seconds": 1.5,
            "rows_returned": 100,
            "status": "SUCCESS",
            "cost": 0.05,
        }
        result = normalize_query_result(raw)

        assert result.cost == 0.05

    def test_normalize_with_row_count_validation(self) -> None:

        raw = {
            "query_id": "Q1",
            "execution_time_seconds": 1.5,
            "rows_returned": 100,
            "status": "SUCCESS",
            "row_count_validation": {"expected": 100, "actual": 100, "status": "PASSED"},
        }
        result = normalize_query_result(raw)

        assert result.row_count_validation == {"expected": 100, "actual": 100, "status": "PASSED"}

    def test_normalize_preserves_plan_capture_error(self) -> None:
        raw = {
            "query_id": "Q1",
            "execution_time_seconds": 1.5,
            "rows_returned": 100,
            "status": "SUCCESS",
            "plan_capture_error": "TypeError: unsupported operand",
        }
        result = normalize_query_result(raw)

        assert result.plan_capture_error == "TypeError: unsupported operand"

    def test_normalize_plan_capture_error_defaults_to_none(self) -> None:
        raw = {
            "query_id": "Q1",
            "execution_time_seconds": 1.5,
            "rows_returned": 100,
            "status": "SUCCESS",
        }
        result = normalize_query_result(raw)

        assert result.plan_capture_error is None

    def test_normalize_with_defaults(self) -> None:

        raw = {
            "query_id": "Q1",
            "execution_time_seconds": 1.5,
            "status": "SUCCESS",
        }
        result = normalize_query_result(raw, default_iteration=5, default_stream_id=2)

        assert result.iteration == 5
        assert result.stream_id == 2

    def test_normalize_preserves_test_type(self) -> None:
        raw = {
            "query_id": "Q1",
            "execution_time_seconds": 1.5,
            "rows_returned": 100,
            "status": "SUCCESS",
            "test_type": "throughput",
        }
        result = normalize_query_result(raw)

        assert result.test_type == "throughput"

    def test_normalize_test_type_defaults_to_none(self) -> None:
        raw = {
            "query_id": "Q1",
            "execution_time_seconds": 1.5,
            "rows_returned": 100,
            "status": "SUCCESS",
        }
        result = normalize_query_result(raw)

        assert result.test_type is None


class TestNormalizeQueryResults:
    def test_normalize_multiple_results(self) -> None:

        raw_results = [
            {"query_id": "Q1", "execution_time_seconds": 1.0, "status": "SUCCESS"},
            {"query_id": "Q2", "execution_time_seconds": 2.0, "status": "SUCCESS"},
            {"query_id": "Q3", "execution_time_seconds": 3.0, "status": "FAILED"},
        ]
        results = normalize_query_results(raw_results)

        assert len(results) == 3
        assert all(isinstance(r, QueryResultInput) for r in results)
        assert results[0].query_id == "1"
        assert results[1].query_id == "2"
        assert results[2].query_id == "3"

    def test_normalize_empty_list(self) -> None:

        results = normalize_query_results([])
        assert results == []

    def test_normalize_with_default_stream_id(self) -> None:

        raw_results = [
            {"query_id": "Q1", "execution_time_seconds": 1.0, "status": "SUCCESS"},
        ]
        results = normalize_query_results(raw_results, default_stream_id=5)

        assert results[0].stream_id == 5

    def test_normalize_preserves_explicit_iteration(self) -> None:

        raw_results = [
            {"query_id": "Q1", "execution_time_seconds": 1.0, "status": "SUCCESS", "iteration": 3},
        ]
        results = normalize_query_results(raw_results)

        assert results[0].iteration == 3


class TestQueryResultInput:
    def test_create_basic(self) -> None:

        result = QueryResultInput(
            query_id="1",
            execution_time_seconds=1.5,
            rows_returned=100,
            status="SUCCESS",
        )

        assert result.query_id == "1"
        assert result.execution_time_seconds == 1.5
        assert result.rows_returned == 100
        assert result.status == "SUCCESS"
        assert result.iteration == 1
        assert result.stream_id == 0
        assert result.error_message is None

    def test_create_with_all_fields(self) -> None:

        result = QueryResultInput(
            query_id="21",
            execution_time_seconds=2.5,
            rows_returned=500,
            status="FAILED",
            iteration=3,
            stream_id=2,
            error_message="Query timeout",
            cost=0.10,
            run_type="measurement",
            row_count_validation={"status": "PASSED"},
        )

        assert result.query_id == "21"
        assert result.execution_time_seconds == 2.5
        assert result.rows_returned == 500
        assert result.status == "FAILED"
        assert result.iteration == 3
        assert result.stream_id == 2
        assert result.error_message == "Query timeout"
        assert result.cost == 0.10
        assert result.run_type == "measurement"
        assert result.row_count_validation == {"status": "PASSED"}
