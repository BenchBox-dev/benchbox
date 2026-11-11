import pytest

from benchbox.core.results.normalizer import (
    NormalizedQuery,
    NormalizedResultDict,
    detect_schema_version,
    get_query_map,
    normalize_result_dict,
)
from tests.fixtures.result_dict_fixtures import make_v2_result_dict

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestDetectSchemaVersion:
    def test_detects_v2_0(self):
        data = {"version": "2.0"}
        assert detect_schema_version(data) == "2.0"

    def test_detects_v2_1(self):
        data = {"version": "2.1"}
        assert detect_schema_version(data) == "2.1"

    def test_detects_v2_2(self):
        data = {"version": "2.2"}
        assert detect_schema_version(data) == "2.2"

    def test_detects_legacy_schema_version_key(self):
        data = {"schema_version": "2.0"}
        assert detect_schema_version(data) == "2.0"

    def test_returns_1x_for_unknown(self):
        data = {}
        assert detect_schema_version(data) == "1.x"

    def test_returns_1x_for_old_version(self):
        data = {"version": "1.0"}
        assert detect_schema_version(data) == "1.x"

    def test_returns_1x_for_unknown_v2_version(self):
        data = {"version": "2.99"}
        assert detect_schema_version(data) == "1.x"

    def test_returns_1x_for_malformed_version(self):
        data = {"version": "2.x"}
        assert detect_schema_version(data) == "1.x"


class TestNormalizeResultDictV2:
    @pytest.fixture
    def v2_result(self):
        result = make_v2_result_dict(
            version="2.0",
            platform="DuckDB",
            platform_version="0.10.0",
            scale_factor=1.0,
            execution_id="test-run-123",
            timestamp="2026-01-28T12:00:00",
            execution_mode="sql",
            total_queries=20,
            passed_queries=18,
            failed_queries=2,
            total_ms=1000.0,
            queries=[
                {"id": "Q1", "ms": 100.5, "rows": 50, "status": "SUCCESS"},
                {"id": "Q2", "ms": 50.0, "rows": 25, "status": "SUCCESS"},
                {"id": "Q3", "ms": None, "rows": 0, "status": "FAILED"},
            ],
            cost={"total_usd": 0.05},
        )
        result["summary"]["timing"].update(avg_ms=50.0, min_ms=10.0, max_ms=200.0)
        return result

    def test_extracts_schema_version(self, v2_result):
        result = normalize_result_dict(v2_result)
        assert result.schema_version == "2.0"

    def test_extracts_benchmark_name(self, v2_result):
        result = normalize_result_dict(v2_result)
        assert result.benchmark == "TPC-H"

    def test_extracts_benchmark_id(self, v2_result):
        result = normalize_result_dict(v2_result)
        assert result.benchmark_id == "tpch"

    def test_extracts_platform(self, v2_result):
        result = normalize_result_dict(v2_result)
        assert result.platform == "DuckDB"

    def test_extracts_scale_factor(self, v2_result):
        result = normalize_result_dict(v2_result)
        assert result.scale_factor == 1.0

    def test_extracts_execution_id(self, v2_result):
        result = normalize_result_dict(v2_result)
        assert result.execution_id == "test-run-123"

    def test_parses_timestamp(self, v2_result):
        result = normalize_result_dict(v2_result)
        assert result.timestamp is not None
        assert result.timestamp.year == 2026
        assert result.timestamp.month == 1
        assert result.timestamp.day == 28

    def test_extracts_execution_mode(self, v2_result):
        result = normalize_result_dict(v2_result)
        assert result.execution_mode == "sql"

    def test_extracts_timing_metrics(self, v2_result):
        result = normalize_result_dict(v2_result)
        assert result.total_time_ms == 1000.0
        assert result.avg_time_ms == 50.0
        assert result.min_time_ms == 10.0
        assert result.max_time_ms == 200.0

    def test_extracts_query_counts(self, v2_result):
        result = normalize_result_dict(v2_result)
        assert result.total_queries == 20
        assert result.passed_queries == 18
        assert result.failed_queries == 2

    def test_calculates_success_rate(self, v2_result):
        result = normalize_result_dict(v2_result)
        assert result.success_rate == 0.9

    def test_extracts_cost(self, v2_result):
        result = normalize_result_dict(v2_result)
        assert result.cost_total == 0.05

    def test_extracts_queries(self, v2_result):
        result = normalize_result_dict(v2_result)
        assert len(result.queries) == 3

        q1 = result.queries[0]
        assert q1.query_id == "Q1"
        assert q1.execution_time_ms == 100.5
        assert q1.rows_returned == 50
        assert q1.status == "SUCCESS"

    def test_handles_null_query_time(self, v2_result):
        result = normalize_result_dict(v2_result)
        q3 = result.queries[2]
        assert q3.query_id == "Q3"
        assert q3.execution_time_ms is None

    def test_stores_raw_data(self, v2_result):
        result = normalize_result_dict(v2_result)
        assert result.raw == v2_result


class TestNormalizeResultDictV1:
    @pytest.fixture
    def v1_result(self):
        return {
            "schema_version": "1.0",
            "benchmark": {"name": "TPC-H", "id": "tpch", "scale_factor": 0.1},
            "execution": {
                "platform": "SQLite",
                "id": "legacy-run-456",
                "timestamp": "2025-06-15T10:30:00",
            },
            "config": {"execution_mode": "sql"},
            "results": {
                "timing": {
                    "total_ms": 5000.0,
                    "avg_ms": 250.0,
                },
                "queries": {
                    "total": 20,
                    "successful": 20,
                    "failed": 0,
                    "success_rate": 1.0,
                    "details": [
                        {
                            "query_id": "Q1",
                            "execution_time_ms": 300.0,
                            "rows_returned": 100,
                            "status": "SUCCESS",
                        },
                        {
                            "id": "Q2",
                            "execution_time_ms": 200.0,
                            "rows_returned": 50,
                            "status": "SUCCESS",
                        },
                    ],
                },
            },
            "cost_summary": {"total_cost": 0.10},
        }

    def test_detects_v1_schema(self, v1_result):
        result = normalize_result_dict(v1_result)
        assert result.schema_version == "1.x"

    def test_extracts_platform_from_execution(self, v1_result):
        result = normalize_result_dict(v1_result)
        assert result.platform == "SQLite"

    def test_extracts_execution_id_from_execution(self, v1_result):
        result = normalize_result_dict(v1_result)
        assert result.execution_id == "legacy-run-456"

    def test_extracts_timing_from_results(self, v1_result):
        result = normalize_result_dict(v1_result)
        assert result.total_time_ms == 5000.0
        assert result.avg_time_ms == 250.0

    def test_extracts_query_counts_from_results(self, v1_result):
        result = normalize_result_dict(v1_result)
        assert result.total_queries == 20
        assert result.passed_queries == 20
        assert result.failed_queries == 0

    def test_extracts_success_rate_directly(self, v1_result):
        result = normalize_result_dict(v1_result)
        assert result.success_rate == 1.0

    def test_extracts_cost_from_cost_summary(self, v1_result):
        result = normalize_result_dict(v1_result)
        assert result.cost_total == 0.10

    def test_extracts_queries_from_details(self, v1_result):
        result = normalize_result_dict(v1_result)
        assert len(result.queries) == 2

        q1 = result.queries[0]
        assert q1.query_id == "Q1"
        assert q1.execution_time_ms == 300.0

    def test_handles_alternative_query_id_key(self, v1_result):
        result = normalize_result_dict(v1_result)
        q2 = result.queries[1]
        assert q2.query_id == "Q2"


class TestNormalizeResultDictEdgeCases:
    def test_handles_empty_data(self):
        result = normalize_result_dict({})
        assert result.benchmark == "unknown"
        assert result.platform == "unknown"
        assert result.total_time_ms is None
        assert result.queries == []

    def test_handles_missing_timing(self):
        data = {"version": "2.0", "summary": {}}
        result = normalize_result_dict(data)
        assert result.total_time_ms is None
        assert result.avg_time_ms is None

    def test_handles_missing_queries_list(self):
        data = {"version": "2.0"}
        result = normalize_result_dict(data)
        assert result.queries == []

    def test_handles_invalid_timestamp(self):
        data = {
            "version": "2.0",
            "run": {"timestamp": "not-a-timestamp"},
        }
        result = normalize_result_dict(data)
        assert result.timestamp is None

    def test_handles_zero_total_queries(self):
        data = {
            "version": "2.0",
            "summary": {"queries": {"total": 0, "passed": 0, "failed": 0}},
        }
        result = normalize_result_dict(data)
        assert result.success_rate is None

    def test_uses_benchmark_id_as_fallback_name(self):
        data = {"version": "2.0", "benchmark": {"id": "tpch"}}
        result = normalize_result_dict(data)
        assert result.benchmark == "tpch"


class TestGetQueryMap:
    def test_creates_map_from_queries(self):
        normalized = NormalizedResultDict(
            schema_version="2.0",
            benchmark="TPC-H",
            benchmark_id="tpch",
            platform="DuckDB",
            scale_factor=1.0,
            execution_id=None,
            timestamp=None,
            execution_mode=None,
            total_time_ms=None,
            avg_time_ms=None,
            queries=[
                NormalizedQuery(query_id="Q1", execution_time_ms=100.0),
                NormalizedQuery(query_id="Q2", execution_time_ms=200.0),
            ],
        )
        query_map = get_query_map(normalized)

        assert len(query_map) == 2
        assert "Q1" in query_map
        assert query_map["Q1"].execution_time_ms == 100.0

    def test_handles_empty_queries(self):
        normalized = NormalizedResultDict(
            schema_version="2.0",
            benchmark="TPC-H",
            benchmark_id="tpch",
            platform="DuckDB",
            scale_factor=1.0,
            execution_id=None,
            timestamp=None,
            execution_mode=None,
            total_time_ms=None,
            avg_time_ms=None,
            queries=[],
        )
        query_map = get_query_map(normalized)
        assert query_map == {}


