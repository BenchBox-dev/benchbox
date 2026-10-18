from __future__ import annotations

import ast
import json
from datetime import datetime
from itertools import product
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from benchbox.core.results.builder import build_benchmark_results
from benchbox.core.results.loader import reconstruct_benchmark_results
from benchbox.core.results.models import BenchmarkResults, QueryExecution
from benchbox.core.results.query_execution import (
    LEGACY_IGNORED_EXTRA_FIELDS,
    LEGACY_QUERY_FIELDS,
    QueryExecutionContractError,
    query_duration_ms_from_legacy,
    query_execution_from_compact_v2,
    query_execution_from_legacy_dict,
    query_execution_to_compact_v2,
    query_execution_to_legacy_dict,
)
from benchbox.core.results.query_normalizer import QueryResultInput
from benchbox.core.results.schema import build_result_payload

pytestmark = [pytest.mark.unit, pytest.mark.medium]

_LEGACY_RESULT_SIGNAL_FIELDS = frozenset(
    {
        "duration",
        "execution_time",
        "execution_time_ms",
        "execution_time_seconds",
        "result_count",
        "rows_returned",
    }
)
_NON_PRODUCER_PATH_PREFIXES = (
    "benchbox/cli/",
    "benchbox/core/comparison/",
    "benchbox/mcp/",
)
_NON_PRODUCER_PATHS = frozenset(
    {
        "benchbox/core/results/exporter.py",
        "benchbox/core/tpc_validation.py",
    }
)


def _benchmark_result(query_result: dict[str, Any] | QueryExecution) -> BenchmarkResults:
    status = query_result.status if isinstance(query_result, QueryExecution) else str(query_result.get("status"))
    return BenchmarkResults(
        benchmark_name="TPC-H",
        platform="duckdb",
        scale_factor=0.01,
        execution_id="canonical-query-contract",
        timestamp=datetime(2026, 8, 8, 12, 0, 0),
        duration_seconds=1.0,
        total_queries=1,
        successful_queries=1 if status == "SUCCESS" else 0,
        failed_queries=0,
        query_results=[query_result],
    )


def test_query_result_input_is_a_fieldless_canonical_compatibility_constructor() -> None:
    execution = QueryResultInput(
        query_id="1",
        execution_time_seconds=1.25,
        rows_returned=0,
        status="SUCCESS",
    )

    assert isinstance(execution, QueryExecution)
    assert set(QueryResultInput.__dataclass_fields__) == set(QueryExecution.__dataclass_fields__)
    assert execution.execution_time_ms == 1250.0


def test_existing_query_execution_optional_defaults_remain_none() -> None:
    execution = QueryExecution(
        query_id="Q1",
        stream_id="power",
        execution_order=1,
        execution_time_ms=100,
        status="SUCCESS",
    )

    assert execution.iteration is None
    assert execution.run_type is None


def test_explicit_attribute_object_compatibility_is_preserved() -> None:
    execution = query_execution_from_legacy_dict(
        SimpleNamespace(
            query_id="Q1",
            execution_time_seconds=0.25,
            rows_returned=0,
            status="SUCCESS",
        )
    )

    assert execution.execution_time_ms == 250.0
    assert execution.rows_returned == 0


def test_field_specific_duration_normalization_ignores_invalid_row_count() -> None:
    legacy = {
        "query_id": "Q1",
        "execution_time_seconds": 0.5,
        "rows_returned": "not-a-row-count",
        "status": "SUCCESS",
    }

    assert query_duration_ms_from_legacy(legacy) == 500.0
    with pytest.raises(QueryExecutionContractError, match="rows_returned must be an integer"):
        query_execution_from_legacy_dict(legacy)


def test_legacy_adapter_preserves_typed_metadata_without_truthiness_loss() -> None:
    legacy = {
        "query_id": "Q1",
        "execution_time_seconds": 0.0,
        "execution_time_ms": 0,
        "execution_time": 0.0,
        "rows_returned": 0,
        "status": "SUCCESS",
        "iteration": 0,
        "stream_id": 0,
        "dataframe_skip_summary": {},
        "result_digest": "",
        "plan_capture_error": "",
        "row_count_validation": {"matched": False},
        "resource_usage": {},
        "cost": 0.0,
        "test_type": "",
        "run_type": "",
    }

    execution = query_execution_from_legacy_dict(legacy)
    restored = query_execution_to_legacy_dict(
        execution,
        include_seconds=True,
        include_legacy_seconds_alias=True,
    )

    assert restored == legacy


def test_stream_position_is_operational_and_does_not_conflict_with_execution_order() -> None:
    execution = query_execution_from_legacy_dict(
        {
            "query_id": "Q1",
            "execution_order": 19,
            "position": 1,
            "execution_time_seconds": 0.25,
            "status": "SUCCESS",
        }
    )

    assert execution.execution_order == 19
    assert execution.execution_time_ms == 250.0


def test_position_only_input_does_not_promote_stream_slot_to_execution_order() -> None:
    execution = query_execution_from_legacy_dict(
        {"query_id": "Q1", "position": 0, "execution_time_seconds": 0.25, "status": "SUCCESS"}
    )

    assert execution.execution_order is None


def test_missing_and_null_optional_values_do_not_become_zero() -> None:
    for legacy in ({"query_id": "Q1", "status": "SUCCESS"}, {"query_id": "Q1", "status": "SUCCESS", "rows": None}):
        execution = query_execution_from_legacy_dict(legacy)
        compact = query_execution_to_compact_v2(execution)
        restored = query_execution_to_legacy_dict(execution)

        assert execution.execution_time_ms is None
        assert execution.rows_returned is None
        assert "ms" not in compact
        assert "rows" not in compact
        assert "execution_time_ms" not in restored
        assert "rows_returned" not in restored


def test_compact_property_round_trip_preserves_values_and_units() -> None:
    durations = [None, 0.0, 0.001, 1.0, 1234.5]
    row_counts = [None, 0, 1, 2**31]
    statuses = ["SUCCESS", "FAILED", "SKIPPED", "UNKNOWN"]
    digests = [None, "", "sha256:abc"]

    for duration_ms, rows, status, digest in product(durations, row_counts, statuses, digests):
        execution = QueryExecution(
            query_id="Q1",
            execution_time_ms=duration_ms,
            rows_returned=rows,
            status=status,
            iteration=0,
            stream_id=0,
            run_type="warmup",
            result_digest=digest,
            dataframe_skip_summary={},
        )
        compact = query_execution_to_compact_v2(execution)
        restored = query_execution_from_compact_v2(compact)

        assert restored.execution_time_ms == duration_ms
        assert restored.rows_returned == rows
        assert restored.status == status
        assert restored.result_digest == digest
        assert restored.dataframe_skip_summary == {}


def test_compact_round_trip_preserves_row_count_validation_evidence() -> None:
    evidence = {"status": "PASSED", "expected": 4, "actual": 4}
    execution = QueryExecution(
        query_id="Q1",
        execution_time_ms=100.0,
        rows_returned=4,
        status="SUCCESS",
        row_count_validation=evidence,
    )

    compact = query_execution_to_compact_v2(execution)
    restored = query_execution_from_compact_v2(compact)

    assert compact["row_count_validation"] == evidence
    assert restored.row_count_validation == evidence


@pytest.mark.parametrize(
    ("evidence", "message"),
    [
        ([], "must be an object"),
        ({"status": "PASSED", "expected": 4}, "missing fields"),
        ({"status": "UNKNOWN", "expected": 4, "actual": 4}, "Unknown row_count_validation.status"),
        ({"status": "PASSED", "expected": -1, "actual": 4}, "must be non-negative"),
        ({"status": "PASSED", "expected": 4.5, "actual": 4}, "must be an integer"),
        ({"status": "PASSED", "expected": True, "actual": 4}, "must be an integer"),
        ({"status": "PASSED", "expected": 4, "actual": 3}, "must match rows_returned"),
        ({"status": "PASSED", "expected": 5, "actual": 4}, "requires equal integer expected and actual"),
        (
            {"status": "PASSED", "expected": 4, "actual": 4, "warning": "x" * 501},
            "exceeds 500 characters",
        ),
    ],
)
def test_compact_serializer_rejects_malformed_row_count_validation(evidence: Any, message: str) -> None:
    execution = QueryExecution(
        query_id="Q1",
        execution_time_ms=100.0,
        rows_returned=4,
        status="SUCCESS",
        row_count_validation=evidence,
    )

    with pytest.raises(QueryExecutionContractError, match=message):
        query_execution_to_compact_v2(execution)


def test_compact_parser_rejects_row_count_validation_that_disagrees_with_rows() -> None:
    compact = {
        "id": "Q1",
        "ms": 100.0,
        "rows": 4,
        "status": "SUCCESS",
        "row_count_validation": {"status": "PASSED", "expected": 3, "actual": 3},
    }

    with pytest.raises(QueryExecutionContractError, match="must match rows_returned"):
        query_execution_from_compact_v2(compact)


@pytest.mark.parametrize(
    "legacy",
    [
        {"query_id": "Q1", "id": "Q2"},
        {"query_id": "Q1", "execution_time_ms": 1, "execution_time_seconds": 1},
        {"query_id": "Q1", "rows_returned": 0, "rows": 1},
        {"query_id": "Q1", "iteration": 0, "iter": 1},
        {"query_id": "Q1", "stream_id": 0, "stream": 1},
        {"query_id": "Q1", "error": "first", "error_message": "second"},
        {"query_id": "Q1", "digest": "a", "result_digest": "b"},
    ],
)
def test_legacy_adapter_rejects_every_conflicting_alias_family(legacy: dict[str, Any]) -> None:
    with pytest.raises(QueryExecutionContractError, match="Conflicting"):
        query_execution_from_legacy_dict(legacy)


@pytest.mark.parametrize("legacy_duration_field", ["execution_time_ms", "execution_time_seconds", "execution_time"])
def test_compact_adapter_rejects_legacy_unit_fields(legacy_duration_field: str) -> None:
    with pytest.raises(QueryExecutionContractError, match="Unknown compact schema-v2 query fields"):
        query_execution_from_compact_v2({"id": "Q1", "ms": 1.0, legacy_duration_field: 0.001, "status": "SUCCESS"})


def test_compact_adapter_rejects_unknown_schema_fields() -> None:
    with pytest.raises(QueryExecutionContractError, match="unexpected_correctness_field"):
        query_execution_from_compact_v2({"id": "Q1", "status": "SUCCESS", "unexpected_correctness_field": 1})


@pytest.mark.parametrize(
    "extras",
    [
        {"first_row": (1,), "sql_text": "SELECT 1"},
        {
            "job_id": "bquxjob_123",
            "job_statistics": {"bytes_processed": 100, "slot_ms": 5},
            "bytes_billed": 100,
        },
        {
            "query_statistics": {"elapsed_ms": 12, "rows_produced": 3},
        },
        {
            "duration_microsecs": 900,
            "cpu_time_microsecs": 100,
            "bytes_returned": 8,
            "slots": 1,
            "wlm_slots": 1,
            "aborted": False,
        },
        {"translated_query": "SELECT 1", "validation_time": 0.1, "validation_passed": True},
        {"job_id": "bdf8296d", "job_statistics": {"bytes_processed": 10}},
        {"query_statistics": {"rows_produced": 5}},
        {"results": [(1,)], "columns": ["one"], "execution_mode": "dataframe"},
        {"cleanup_time": 0.01, "table_name": "orders", "platform": "spark", "reason": "unsupported"},
    ],
)
def test_legacy_unknown_policy_allows_inventory_operational_extras(extras: dict[str, Any]) -> None:
    execution = query_execution_from_legacy_dict({"query_id": "Q1", "status": "SUCCESS", **extras})
    assert execution.query_id == "Q1"
    assert query_execution_to_compact_v2(execution) == {"id": "Q1", "status": "SUCCESS"}


def test_legacy_unknown_policy_rejects_misspelled_correctness_field() -> None:
    with pytest.raises(QueryExecutionContractError, match="row_returnd"):
        query_execution_from_legacy_dict({"query_id": "Q1", "status": "SUCCESS", "row_returnd": 1})


def test_legacy_query_result_producer_literals_have_an_explicit_field_policy() -> None:
    repository_root = Path(__file__).resolve().parents[4]
    classified_fields = LEGACY_QUERY_FIELDS | LEGACY_IGNORED_EXTRA_FIELDS
    producer_sites: list[str] = []
    unclassified_by_site: dict[str, list[str]] = {}

    for path in sorted((repository_root / "benchbox").rglob("*.py")):
        relative_path = path.relative_to(repository_root).as_posix()
        if relative_path in _NON_PRODUCER_PATHS or relative_path.startswith(_NON_PRODUCER_PATH_PREFIXES):
            continue

        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative_path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            literal_fields = {
                key.value for key in node.keys if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
            if "query_id" not in literal_fields or not literal_fields.intersection(_LEGACY_RESULT_SIGNAL_FIELDS):
                continue

            site = f"{relative_path}:{node.lineno}"
            producer_sites.append(site)
            unclassified = sorted(literal_fields - classified_fields)
            if unclassified:
                unclassified_by_site[site] = unclassified

    assert producer_sites, "The focused producer scan found no query-result literals"
    assert unclassified_by_site == {}


def test_legacy_ignored_field_allowlist_has_no_duplicate_literals() -> None:
    module_path = Path(__file__).resolve().parents[4] / "benchbox/core/results/query_execution.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=module_path.as_posix())

    ignored_field_literals: list[str] | None = None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "LEGACY_IGNORED_EXTRA_FIELDS" for target in node.targets
        ):
            continue
        if not isinstance(node.value, ast.Call) or not node.value.args or not isinstance(node.value.args[0], ast.Set):
            pytest.fail("LEGACY_IGNORED_EXTRA_FIELDS must remain an auditable frozenset literal")
        ignored_field_literals = [
            element.value
            for element in node.value.args[0].elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        ]
        break

    assert ignored_field_literals is not None
    duplicates = sorted({field for field in ignored_field_literals if ignored_field_literals.count(field) > 1})
    assert duplicates == []


def test_builder_precise_seconds_survive_integer_ms_compatibility_alias() -> None:
    result = build_benchmark_results(
        benchmark_name="TPC-H",
        platform_name="DuckDB",
        scale_factor=0.01,
        query_results=[
            QueryResultInput(
                query_id="1",
                execution_time_seconds=0.0009,
                rows_returned=0,
                status="SUCCESS",
            )
        ],
    )

    assert result.query_results[0]["execution_time_ms"] == 0
    assert result.query_results[0]["execution_time_seconds"] == 0.0009
    assert build_result_payload(result)["queries"][0]["ms"] == pytest.approx(0.9)


def test_direct_and_legacy_status_normalization_are_identical() -> None:
    direct = QueryExecution(query_id="Q1", execution_time_ms=1, status="ok")
    legacy = query_execution_from_legacy_dict({"query_id": "Q1", "execution_time_ms": 1, "status": "ok"})

    assert direct.status == legacy.status == "SUCCESS"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("execution_time_ms", float("nan")),
        ("execution_time_ms", -1),
        ("execution_time_ms", True),
        ("rows_returned", -1),
        ("rows_returned", 1.5),
        ("rows_returned", False),
        ("iteration", -1),
        ("iteration", True),
        ("execution_order", -1),
        ("execution_order", False),
    ],
)
def test_direct_typed_construction_rejects_invalid_correctness_values(field: str, value: Any) -> None:
    kwargs: dict[str, Any] = {"query_id": "Q1", "execution_time_ms": 1, "status": "SUCCESS", field: value}
    with pytest.raises(QueryExecutionContractError):
        QueryExecution(**kwargs)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("execution_time_ms", float("nan")),
        ("execution_time_ms", -1),
        ("rows_returned", -1),
        ("iteration", -1),
        ("execution_order", -1),
    ],
)
def test_serialization_revalidates_mutated_typed_executions(field: str, value: Any) -> None:
    execution = QueryExecution(
        query_id="Q1",
        execution_time_ms=1,
        rows_returned=1,
        iteration=1,
        execution_order=1,
        status="SUCCESS",
    )
    setattr(execution, field, value)

    with pytest.raises(QueryExecutionContractError):
        query_execution_to_compact_v2(execution)


def test_loader_preserves_exact_optional_key_shape_while_reexport_omits_nulls() -> None:
    payload = build_result_payload(_benchmark_result({"query_id": "Q1", "status": "SUCCESS"}))
    payload["queries"][0].pop("ms", None)
    payload["queries"][0].pop("rows", None)

    reconstructed = reconstruct_benchmark_results(payload)
    query = reconstructed.query_results[0]

    assert query == {
        "query_id": "1",
        "status": "SUCCESS",
        "iteration": 1,
        "stream_id": 0,
        "run_type": "measurement",
        "execution_time_ms": None,
        "rows_returned": None,
    }
    reexported = build_result_payload(reconstructed)["queries"][0]
    assert "ms" not in reexported
    assert "rows" not in reexported


def test_valid_v2_query_rows_are_byte_stable_across_load_reexport() -> None:
    original = build_result_payload(
        _benchmark_result(
            {
                "query_id": "Q1",
                "execution_time_seconds": 0.125,
                "execution_time_ms": 125,
                "execution_time": 0.125,
                "rows_returned": 0,
                "status": "SUCCESS",
                "iteration": 0,
                "stream_id": 0,
                "run_type": "warmup",
                "result_digest": "digest-1",
                "dataframe_skip_summary": {},
                "plan_capture_error": "",
            }
        )
    )

    round_trip = build_result_payload(reconstruct_benchmark_results(original))

    assert json.dumps(round_trip["queries"], separators=(",", ":")) == json.dumps(
        original["queries"], separators=(",", ":")
    )


def test_digest_survives_export_load_reexport() -> None:
    original = build_result_payload(
        _benchmark_result(
            {
                "query_id": "Q1",
                "execution_time_ms": 1.0,
                "rows_returned": 1,
                "status": "SUCCESS",
                "result_digest": "sha256:result",
            }
        )
    )
    reconstructed = reconstruct_benchmark_results(original)
    round_trip = build_result_payload(reconstructed)

    assert reconstructed.query_results[0]["result_digest"] == "sha256:result"
    assert round_trip["queries"][0]["digest"] == "sha256:result"


def test_failed_query_error_round_trip_remains_attached_to_same_execution() -> None:
    result = _benchmark_result(
        {
            "query_id": "Q1",
            "execution_time_seconds": 0.25,
            "rows_returned": 0,
            "status": "FAILED",
            "error_type": "TimeoutError",
            "error_message": "deadline exceeded",
        }
    )
    result.successful_queries = 0
    result.failed_queries = 1
    original = build_result_payload(result)
    reconstructed = reconstruct_benchmark_results(original)

    assert reconstructed.query_results == [
        {
            "query_id": "1",
            "status": "FAILED",
            "execution_time_ms": 250.0,
            "rows_returned": 0,
            "iteration": 1,
            "stream_id": 0,
            "run_type": "measurement",
            "error_type": "TimeoutError",
            "error_message": "deadline exceeded",
        }
    ]


def test_warning_only_row_count_validation_without_expected_count_normalizes_to_skipped() -> None:
    result = _benchmark_result(
        {
            "query_id": "Q1",
            "execution_time_seconds": 0.25,
            "rows_returned": 42,
            "status": "SUCCESS",
            "row_count_validation": {
                "expected": None,
                "actual": 42,
                "status": "PASSED",
                "warning": "Verification skipped: reference results unavailable",
            },
        }
    )
    payload = build_result_payload(result)
    assert payload["queries"][0]["row_count_validation"] == {
        "status": "SKIPPED",
        "expected": None,
        "actual": 42,
        "warning": "Verification skipped: reference results unavailable",
    }
