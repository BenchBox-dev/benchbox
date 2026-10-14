from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from benchbox.core.execution_engine import ExecutionEngineHook
from benchbox.platforms.base.result_capture import ResultCaptureMixin

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _Host(ResultCaptureMixin):
    def __init__(self, capture_plans=True, plan=None, capture_time_ms=1.5):
        self.capture_plans = capture_plans
        self._plan = plan
        self._capture_time_ms = capture_time_ms
        self.capture_calls = []

    def capture_query_plan(self, connection, query, query_id):
        self.capture_calls.append((connection, query, query_id))
        return self._plan, self._capture_time_ms


def _make_plan(fingerprint="fp123"):
    plan = MagicMock()
    plan.plan_fingerprint = fingerprint
    return plan


class TestMergePlanCaptureIntoResult:
    def test_noop_when_capture_disabled(self):
        host = _Host(capture_plans=False, plan=_make_plan())
        result = {"status": "SUCCESS"}
        host._merge_plan_capture_into_result(result, "conn", "SELECT 1", "q1")
        assert result == {"status": "SUCCESS"}
        assert host.capture_calls == [], "capture_plans=False must not issue EXPLAIN"

    def test_noop_when_status_failed(self):
        host = _Host(capture_plans=True, plan=_make_plan())
        result = {"status": "FAILED"}
        host._merge_plan_capture_into_result(result, "conn", "SELECT 1", "q1")
        assert result == {"status": "FAILED"}
        assert host.capture_calls == [], "FAILED status must not trigger capture"

    def test_noop_when_status_missing(self):
        host = _Host(capture_plans=True, plan=_make_plan())
        result = {}
        host._merge_plan_capture_into_result(result, "conn", "SELECT 1", "q1")
        assert result == {}
        assert host.capture_calls == []

    def test_merges_plan_fields_on_success(self):
        plan = _make_plan("fpX")
        host = _Host(capture_plans=True, plan=plan, capture_time_ms=2.0)
        result = {"status": "SUCCESS"}
        host._merge_plan_capture_into_result(result, "conn", "SELECT 1", "q1")
        assert result["query_plan"] is plan
        assert result["plan_fingerprint"] == "fpX"
        assert result["plan_capture_time_ms"] == 2.0
        assert host.capture_calls == [("conn", "SELECT 1", "q1")]

    def test_records_capture_time_even_when_no_plan(self):
        host = _Host(capture_plans=True, plan=None, capture_time_ms=0.0)
        result = {"status": "SUCCESS"}
        host._merge_plan_capture_into_result(result, "conn", "SELECT 1", "q1")
        assert "query_plan" not in result
        assert "plan_fingerprint" not in result

        assert result["plan_capture_time_ms"] == 0.0

    def test_no_plan_and_none_capture_time_leaves_result_unchanged(self):
        host = _Host(capture_plans=True, plan=None, capture_time_ms=None)
        result = {"status": "SUCCESS"}
        host._merge_plan_capture_into_result(result, "conn", "SELECT 1", "q1")
        assert result == {"status": "SUCCESS"}

    def test_mutates_in_place_and_returns_none(self):
        host = _Host(capture_plans=True, plan=_make_plan())
        result = {"status": "SUCCESS"}
        ret = host._merge_plan_capture_into_result(result, "conn", "SELECT 1", "q1")
        assert ret is None, "helper mutates in place and must not return the dict"


class _SummaryHost(ResultCaptureMixin):
    def __init__(self, capture_plans=True):
        self.capture_plans = capture_plans
        self.plan_capture_errors = []
        self.logger = MagicMock()


def _qr(query_id, status="SUCCESS", run_type="measurement", query_plan=None, stream_id=0):
    row = {"query_id": query_id, "status": status, "run_type": run_type, "stream_id": stream_id}
    if query_plan is not None:
        row["query_plan"] = query_plan
    return row


class TestLogPlanCaptureSummary:
    def test_single_stream_all_captured_logs_info_with_matching_counts(self):
        host = _SummaryHost()
        query_results = [_qr("q1", query_plan=_make_plan()), _qr("q2", query_plan=_make_plan())]
        host._log_plan_capture_summary(query_results)
        host.logger.info.assert_called_once_with("Query plans: 2/2 captured")
        host.logger.warning.assert_not_called()

    def test_multi_stream_same_query_id_not_double_counted(self):

        host = _SummaryHost()
        plan = _make_plan()
        query_results = [
            _qr("q1", query_plan=plan, stream_id=0),
            _qr("q1", query_plan=plan, stream_id=1),
        ]
        host._log_plan_capture_summary(query_results)
        host.logger.info.assert_called_once_with("Query plans: 1/1 captured")

    def test_capture_failure_logs_warning_with_failed_suffix(self):
        host = _SummaryHost()
        query_results = [_qr("q1", query_plan=_make_plan()), _qr("q2", query_plan=None)]
        host._log_plan_capture_summary(query_results)
        host.logger.warning.assert_called_once_with("Query plans: 1/2 captured, 1 failed")
        host.logger.info.assert_not_called()


class TestExecuteQueryWithPlanCapture:
    def test_delegates_then_merges(self):
        host = _Host(capture_plans=True, plan=_make_plan())
        calls = []

        def execute(connection, query, query_id, **kwargs):
            calls.append((connection, query, query_id, kwargs))
            return {"status": "SUCCESS"}

        result = host.execute_query_with_plan_capture(
            execute,
            "conn",
            "SELECT 1",
            "q1",
            benchmark_type="tpch",
            scale_factor=0.01,
            validate_row_count=False,
            stream_id=3,
        )
        assert calls == [
            (
                "conn",
                "SELECT 1",
                "q1",
                {
                    "benchmark_type": "tpch",
                    "scale_factor": 0.01,
                    "validate_row_count": False,
                    "stream_id": 3,
                },
            )
        ]
        assert result["status"] == "SUCCESS"
        assert result["plan_fingerprint"] == "fp123"
        assert host.capture_calls == [("conn", "SELECT 1", "q1")]

    def test_merge_noop_without_capture_plans(self):
        host = _Host(capture_plans=False, plan=_make_plan())
        result = host.execute_query_with_plan_capture(
            lambda connection, query, query_id, **kwargs: {"status": "SUCCESS"},
            "conn",
            "SELECT 1",
            "q1",
        )
        assert result == {"status": "SUCCESS"}
        assert host.capture_calls == []


class _MetadataHost(ExecutionEngineHook, ResultCaptureMixin):
    table_mode = "native"
    external_format = None

    def _hash_connection_config(self, connection_config):
        return "connection-hash"

    def get_sorted_ingestion_metadata(self):
        return None

    def get_post_load_maintenance_metadata(self):
        return None

    def build_post_load_maintenance_phase(self):
        return None

    def _build_tuning_profile_metadata(self, run_config):
        return None


@pytest.mark.parametrize(
    "name,seed,expected",
    [
        ("tpch", None, "qgen -d (TPC-H default substitution parameters)"),
        ("TPC-H", 17039360, "qgen -r (17039360 + 1000 * stream_id)"),
        ("tpch", 42, "qgen -r (42 + 1000 * stream_id)"),
    ],
)
def test_sql_metadata_records_the_bound_tpch_parameter_set(name, seed, expected):
    from benchbox.core.results.result_factory import build_enhanced_benchmark_result
    from benchbox.core.results.schema import build_result_payload

    metadata, _, _ = _MetadataHost()._build_execution_metadata(
        {"benchmark_name": name, "seed": seed, "test_execution_type": "power"}
    )
    assert metadata["run_config"]["query_parameters"] == expected
    result = build_enhanced_benchmark_result(
        benchmark=SimpleNamespace(benchmark_name="TPC-H", scale_factor=1.0),
        platform="duckdb",
        query_results=[],
        execution_metadata=metadata,
    )
    assert build_result_payload(result)["config"]["query_parameters"] == expected


def test_other_sql_benchmarks_keep_their_existing_metadata_shape():
    metadata, _, _ = _MetadataHost()._build_execution_metadata({"benchmark_name": "tpcds", "seed": 42})
    assert "query_parameters" not in metadata["run_config"]


@pytest.mark.parametrize("seed", [None, 42])
def test_standard_sql_metadata_records_defaults_even_with_a_requested_seed(seed):
    metadata, _, _ = _MetadataHost()._build_execution_metadata({"benchmark_name": "tpch", "seed": seed})
    assert metadata["run_config"]["query_parameters"] == "qgen -d (TPC-H default substitution parameters)"


@pytest.mark.parametrize("seed", [None, 42, 7])
def test_throughput_sql_metadata_matches_the_production_generation_convention(seed):
    from benchbox.core.tpch.throughput_test import TPCHThroughputTest, TPCHThroughputTestConfig

    calls = []
    benchmark = SimpleNamespace(
        get_query=lambda query_id, **kwargs: calls.append(kwargs) or "SELECT 1",
    )
    throughput = TPCHThroughputTest(benchmark, lambda: None)
    config = TPCHThroughputTestConfig(num_streams=2) if seed is None else TPCHThroughputTestConfig(base_seed=seed)
    throughput._pregenerate_stream_queries(config)
    base_seed = config.base_seed
    for stream in (1, 2):
        emitted = [call["seed"] for call in calls if call["params"]["stream_id"] == stream]
        assert emitted == [base_seed + 1001 * stream + position for position in range(22)]
    metadata, _, _ = _MetadataHost()._build_execution_metadata(
        {"benchmark_name": "tpch", "seed": seed, "test_execution_type": "throughput"}
    )
    assert metadata["run_config"]["query_parameters"] == (f"qgen -r ({base_seed} + 1001 * stream_id + query_position)")


def test_fallback_sql_metadata_records_the_standard_generation_convention():
    metadata, _, _ = _MetadataHost()._build_execution_metadata(
        {
            "benchmark_name": "tpch",
            "seed": 7,
            "test_execution_type": "throughput",
            "_effective_execution_type": "power",
        }
    )
    assert metadata["run_config"]["query_parameters"] == "qgen -d (TPC-H default substitution parameters)"


def test_combined_sql_metadata_describes_only_the_requested_query_phases():
    metadata, _, _ = _MetadataHost()._build_execution_metadata(
        {
            "benchmark_name": "tpch",
            "seed": 7,
            "test_execution_type": "combined",
            "options": {"requested_phases": ["throughput", "maintenance"]},
        }
    )
    assert metadata["run_config"]["query_parameters"] == (
        "throughput: qgen -r (7 + 1001 * stream_id + query_position); "
        "maintenance: not applicable (TPC-H refresh functions)"
    )


def test_maintenance_sql_metadata_does_not_claim_qgen_substitution_parameters():
    metadata, _, _ = _MetadataHost()._build_execution_metadata(
        {"benchmark_name": "tpch", "seed": 7, "test_execution_type": "maintenance"}
    )
    assert metadata["run_config"]["query_parameters"] == "not applicable (TPC-H refresh functions)"
