from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from benchbox.core.analysis.comparison import PlatformComparison
from benchbox.core.results.analytics import compare_results, detect_regressions
from benchbox.core.results.comparison_modes import (
    IncomparableModesError,
    bundle_fetch_mode,
    bundle_mode_differences,
    modes_comparable,
    require_comparable_modes,
    results_fetch_mode,
)
from benchbox.core.results.database import ResultDatabase
from benchbox.core.results.fetch_mode import MIXED_FETCH_MODE, parse_fetch_mode, summarize_fetch_modes
from benchbox.core.results.loader import reconstruct_benchmark_results
from benchbox.core.results.models import QueryExecution
from benchbox.core.results.query_execution import (
    QueryExecutionContractError,
    query_execution_from_compact_v2,
    query_execution_from_legacy_dict,
    query_execution_to_compact_v2,
    query_execution_to_legacy_dict,
)
from benchbox.core.results.schema import SchemaV2Validator, build_result_payload
from benchbox.core.results.schema_policy import CURRENT_SCHEMA_VERSION
from benchbox.validation.bundle import ValidationResult, _validate_run_section, _validate_single_query
from tests.fixtures.result_dict_fixtures import make_benchmark_results

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _execution(query_id: str, mode: str | None, status: str = "SUCCESS", ms: float = 10.0) -> QueryExecution:
    return QueryExecution(
        query_id=query_id,
        execution_time_ms=ms,
        rows_returned=4,
        status=status,
        fetch_mode=mode,
        iteration=1,
        stream_id=0,
        run_type="measurement",
    )


def _bundle(modes: list[str | None], *, platform: str = "duckdb", execution_id: str = "run") -> dict:
    executions = [_execution(str(index + 1), mode) for index, mode in enumerate(modes)]
    results = make_benchmark_results(
        platform=platform,
        execution_id=execution_id,
        benchmark_name="TPC-H",
        benchmark_id="tpch",
        query_results=executions,
        total_queries=len(executions),
        successful_queries=len(executions),
        validation_status="PASSED",
    )
    return copy.deepcopy(build_result_payload(results))


class TestFetchModeValues:
    def test_parse_accepts_only_rows_and_arrow(self):
        assert parse_fetch_mode("Arrow") == "arrow"
        assert parse_fetch_mode(" rows ") == "rows"
        with pytest.raises(ValueError, match="fetch_mode must be one of"):
            parse_fetch_mode("mixed")

    def test_summarize_reports_mixed_and_treats_unrecorded_as_rows(self):
        assert summarize_fetch_modes(["arrow", "arrow"]) == "arrow"
        assert summarize_fetch_modes([None, "rows"]) == "rows"
        assert summarize_fetch_modes(["rows", "arrow"]) == MIXED_FETCH_MODE
        assert summarize_fetch_modes([]) is None


class TestQueryExecutionContract:
    def test_compact_and_legacy_round_trip(self):
        execution = _execution("1", "arrow")
        compact = query_execution_to_compact_v2(execution)
        assert compact["fetch_mode"] == "arrow"
        assert query_execution_from_compact_v2(compact).fetch_mode == "arrow"
        legacy = query_execution_to_legacy_dict(execution)
        assert legacy["fetch_mode"] == "arrow"
        assert query_execution_from_legacy_dict(legacy).fetch_mode == "arrow"

    def test_unrecorded_mode_is_omitted(self):
        assert "fetch_mode" not in query_execution_to_compact_v2(_execution("1", None))

    def test_invalid_mode_is_rejected(self):
        with pytest.raises(QueryExecutionContractError, match="fetch_mode must be one of"):
            QueryExecution(query_id="1", execution_time_ms=1.0, status="SUCCESS", fetch_mode="pandas")


class TestExportedBundle:
    def test_fetch_mode_is_additive_at_the_current_schema_version(self):
        bundle = _bundle(["arrow", "arrow"])
        assert bundle["result_schema_version"] == CURRENT_SCHEMA_VERSION
        assert CURRENT_SCHEMA_VERSION != "2.4"
        assert [query["fetch_mode"] for query in bundle["queries"]] == ["arrow", "arrow"]
        assert bundle["run"]["fetch_mode"] == "arrow"
        SchemaV2Validator().validate(bundle)

    def test_run_summary_is_mixed_when_queries_disagree(self):
        assert _bundle(["rows", "arrow"])["run"]["fetch_mode"] == "mixed"

    def test_run_summary_absent_when_no_query_records_a_mode(self):
        bundle = _bundle([None, None])
        assert "fetch_mode" not in bundle["run"]
        assert all("fetch_mode" not in query for query in bundle["queries"])

    def test_loader_preserves_per_query_mode(self):
        restored = reconstruct_benchmark_results(_bundle(["arrow", "rows"]))
        assert [query["fetch_mode"] for query in restored.query_results] == ["arrow", "rows"]
        assert results_fetch_mode(restored) == "mixed"

    def test_public_validator_rejects_unknown_modes(self):
        bad_run = ValidationResult("bundle.json")
        _validate_run_section(
            {"id": "r", "timestamp": "t", "total_duration_ms": 1, "query_time_ms": 1, "fetch_mode": "pandas"}, bad_run
        )
        assert any("run.fetch_mode" in error for error in bad_run.errors)
        bad_query = ValidationResult("bundle.json")
        _validate_single_query(
            0, {"id": "1", "ms": 1.0, "rows": 1, "status": "SUCCESS", "fetch_mode": "mixed"}, "2.3", bad_query
        )
        assert any("queries[0].fetch_mode" in error for error in bad_query.errors)
        good = ValidationResult("bundle.json")
        _validate_single_query(
            0, {"id": "1", "ms": 1.0, "rows": 1, "status": "SUCCESS", "fetch_mode": "arrow"}, "2.3", good
        )
        assert not any("fetch_mode" in error for error in good.errors)


class TestBundleComparability:
    def test_same_mode_is_comparable(self):
        assert modes_comparable(_bundle(["arrow"]), _bundle(["arrow"]))

    def test_different_modes_are_refused(self):
        differences = bundle_mode_differences(_bundle(["rows"]), _bundle(["arrow"]))
        assert differences == ["fetch mode differs (rows vs arrow)"]
        with pytest.raises(IncomparableModesError, match="compared and ranked within one mode"):
            require_comparable_modes(_bundle(["rows"]), _bundle(["arrow"]))

    def test_unrecorded_legacy_run_reads_as_rows(self):
        legacy = _bundle([None])
        assert bundle_fetch_mode(legacy) == "rows"
        assert modes_comparable(legacy, _bundle(["rows"]))
        assert not modes_comparable(legacy, _bundle(["arrow"]))

    def test_a_mixed_run_is_comparable_with_nothing(self):
        mixed = _bundle(["rows", "arrow"])
        assert not modes_comparable(mixed, _bundle(["rows"]))
        assert not modes_comparable(mixed, copy.deepcopy(mixed))

    def test_failed_queries_do_not_set_the_mode(self):
        executions = [_execution("1", "arrow"), _execution("2", None, status="FAILED")]
        results = make_benchmark_results(query_results=executions, total_queries=2, successful_queries=1)
        assert results_fetch_mode(results) == "arrow"


def _write(tmp_path: Path, name: str, bundle: dict) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(bundle), encoding="utf-8")
    return path


class TestComparisonSurfaces:
    def test_compare_results_refuses_mixed_modes(self, tmp_path):
        rows = _write(tmp_path, "rows.json", _bundle(["rows"], execution_id="a"))
        arrow = _write(tmp_path, "arrow.json", _bundle(["arrow"], execution_id="b"))
        outcome = compare_results(rows, arrow)
        assert outcome["error_code"] == "VALIDATION_ERROR"
        assert "fetch mode differs (rows vs arrow)" in outcome["error"]
        assert "summary" not in outcome

    def test_compare_results_accepts_one_mode(self, tmp_path):
        left = _write(tmp_path, "a.json", _bundle(["arrow", "arrow"], execution_id="a"))
        right = _write(tmp_path, "b.json", _bundle(["arrow", "arrow"], execution_id="b"))
        outcome = compare_results(left, right)
        assert "error" not in outcome
        assert outcome["summary"]["total_queries_compared"] == 2

    def test_platform_comparison_refuses_mixed_modes(self):
        rows = reconstruct_benchmark_results(_bundle(["rows"], platform="duckdb", execution_id="a"))
        arrow = reconstruct_benchmark_results(_bundle(["arrow"], platform="datafusion", execution_id="b"))
        validation = PlatformComparison([rows, arrow]).validate()
        assert not validation.is_valid
        assert any("compared and ranked within one mode" in error for error in validation.errors)
        with pytest.raises(ValueError, match="Cannot compare invalid results"):
            PlatformComparison([rows, arrow]).compare()

    def test_platform_comparison_accepts_one_mode(self):
        first = reconstruct_benchmark_results(_bundle(["arrow"], platform="duckdb", execution_id="a"))
        second = reconstruct_benchmark_results(_bundle(["arrow"], platform="datafusion", execution_id="b"))
        validation = PlatformComparison([first, second]).validate()
        assert not any("one mode" in error for error in validation.errors)

    def test_regression_detection_skips_runs_of_other_modes(self, tmp_path):
        older = _bundle(["rows"], execution_id="older")
        older["run"]["timestamp"] = "2026-01-01T00:00:00"
        newer = _bundle(["arrow"], execution_id="newer")
        newer["run"]["timestamp"] = "2026-01-02T00:00:00"
        _write(tmp_path, "older.json", older)
        _write(tmp_path, "newer.json", newer)
        outcome = detect_regressions(tmp_path)
        assert outcome["status"] == "insufficient_data"
        assert outcome["excluded_other_variant_runs"]


def _stored(execution_id: str, platform: str, mode: str | None, geometric_mean_ms: float, when: datetime):
    from tests.unit.core.results.test_database import create_test_result

    result = create_test_result(
        execution_id=execution_id,
        platform=platform,
        geometric_mean_ms=geometric_mean_ms,
        timestamp=when,
    )
    result.query_results = [{"query_id": "1", "status": "SUCCESS", "fetch_mode": mode}] if mode else []
    return result


class TestResultDatabase:
    def test_rankings_refuse_stored_results_that_mix_modes(self, tmp_path):
        db = ResultDatabase(tmp_path / "r.db")
        now = datetime.now(timezone.utc)
        db.store_result(_stored("a", "DuckDB", "arrow", 100.0, now))
        db.store_result(_stored("b", "Snowflake", "rows", 200.0, now))
        with pytest.raises(IncomparableModesError, match="arrow, rows"):
            db.calculate_rankings("TPC-H", 1.0)

    def test_rankings_rank_one_selected_mode(self, tmp_path):
        db = ResultDatabase(tmp_path / "r.db")
        now = datetime.now(timezone.utc)
        db.store_result(_stored("a", "DuckDB", "arrow", 100.0, now))
        db.store_result(_stored("b", "DataFusion", "arrow", 150.0, now))
        db.store_result(_stored("c", "Snowflake", "rows", 50.0, now))
        arrow = db.calculate_rankings("TPC-H", 1.0, fetch_mode="arrow")
        assert [entry.platform for entry in arrow] == ["DuckDB", "DataFusion"]
        rows = db.calculate_rankings("TPC-H", 1.0, fetch_mode="rows")
        assert [entry.platform for entry in rows] == ["Snowflake"]

    def test_legacy_results_rank_as_rows(self, tmp_path):
        db = ResultDatabase(tmp_path / "r.db")
        now = datetime.now(timezone.utc)
        db.store_result(_stored("a", "DuckDB", None, 100.0, now))
        db.store_result(_stored("b", "Snowflake", "rows", 200.0, now))
        assert [entry.platform for entry in db.calculate_rankings("TPC-H", 1.0)] == ["DuckDB", "Snowflake"]

    def test_trends_use_only_runs_in_the_newest_mode(self, tmp_path):
        db = ResultDatabase(tmp_path / "r.db")
        now = datetime.now(timezone.utc)
        db.store_result(_stored("old", "DuckDB", "rows", 400.0, now - timedelta(days=40)))
        db.store_result(_stored("new", "DuckDB", "arrow", 100.0, now - timedelta(days=1)))
        trends = db.get_performance_trends("DuckDB", "TPC-H", 1.0, periods=3, period_days=30)
        assert sum(trend.sample_count for trend in trends) == 1
        assert trends[0].avg_geometric_mean_ms == 100.0
