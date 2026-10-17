from dataclasses import asdict
from datetime import datetime

import pytest

from benchbox.core.results.models import QueryExecution
from benchbox.core.results.query_plan_models import (
    JoinType,
    LogicalOperator,
    LogicalOperatorType,
    PhysicalOperator,
    QueryPlanDAG,
)
from benchbox.core.results.schema import build_plans_payload, build_result_payload
from tests.fixtures.result_dict_fixtures import make_benchmark_results

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestQueryPlanInQueryExecution:
    def test_query_execution_without_plan(self) -> None:
        qe = QueryExecution(
            query_id="q01",
            stream_id="power",
            execution_order=1,
            execution_time_ms=150,
            status="SUCCESS",
            rows_returned=4,
        )

        assert qe.query_plan is None
        assert qe.plan_fingerprint is None

    def test_query_execution_with_plan(self) -> None:

        root = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_1",
            table_name="lineitem",
        )
        plan = QueryPlanDAG(
            query_id="q01",
            platform="duckdb",
            logical_root=root,
            estimated_cost=100.0,
        )

        qe = QueryExecution(
            query_id="q01",
            stream_id="power",
            execution_order=1,
            execution_time_ms=150,
            status="SUCCESS",
            rows_returned=4,
            query_plan=plan,
            plan_fingerprint=plan.plan_fingerprint,
        )

        assert qe.query_plan is not None
        assert qe.query_plan.query_id == "q01"
        assert qe.query_plan.platform == "duckdb"
        assert qe.plan_fingerprint == plan.plan_fingerprint

    def test_query_execution_serialization_with_plan(self) -> None:
        root = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_1",
            table_name="orders",
        )
        plan = QueryPlanDAG(
            query_id="q01",
            platform="duckdb",
            logical_root=root,
        )

        qe = QueryExecution(
            query_id="q01",
            stream_id="power",
            execution_order=1,
            execution_time_ms=200,
            status="SUCCESS",
            query_plan=plan,
            plan_fingerprint=plan.plan_fingerprint,
        )

        result = asdict(qe)

        assert result["query_id"] == "q01"
        assert result["query_plan"]["query_id"] == "q01"
        assert result["query_plan"]["platform"] == "duckdb"
        assert result["query_plan"]["logical_root"]["table_name"] == "orders"
        assert result["plan_fingerprint"] == plan.plan_fingerprint


class TestQueryPlanInBenchmarkResults:
    def test_benchmark_results_without_plans(self) -> None:
        results = make_benchmark_results(
            benchmark_name="tpch",
            platform="duckdb",
            scale_factor=1.0,
            execution_id="test_001",
            duration_seconds=10.0,
            total_queries=1,
            successful_queries=1,
        )

        assert results.query_plans_captured == 0
        assert results.plan_comparison_summary is None

    def test_benchmark_results_with_plan_statistics(self) -> None:
        results = make_benchmark_results(
            benchmark_name="tpch",
            platform="duckdb",
            scale_factor=1.0,
            execution_id="test_002",
            duration_seconds=10.0,
            total_queries=22,
            successful_queries=22,
            query_plans_captured=15,
        )

        assert results.query_plans_captured == 15

    def test_benchmark_results_with_comparison_summary(self) -> None:

        results = make_benchmark_results(
            benchmark_name="tpch",
            platform="duckdb",
            scale_factor=1.0,
            execution_id="test_003",
            duration_seconds=10.0,
            total_queries=22,
            successful_queries=22,
            query_plans_captured=22,
            plan_comparison_summary={
                "baseline_run": "run_001",
                "plans_compared": 22,
                "plans_identical": 20,
                "plans_different": 2,
                "differences": [
                    {"query_id": "q01", "change_type": "operator_added"},
                    {"query_id": "q05", "change_type": "join_type_changed"},
                ],
            },
        )

        assert results.plan_comparison_summary is not None
        assert results.plan_comparison_summary["plans_identical"] == 20


class TestSchemaV2ExportWithPlans:
    def test_schema_v2_export_without_plans(self) -> None:
        results = make_benchmark_results(
            benchmark_id="tpch",
            benchmark_name="tpch",
            platform="duckdb",
            scale_factor=1.0,
            execution_id="test_004",
            timestamp=datetime(2025, 1, 1, 12, 0, 0),
            duration_seconds=10.0,
            total_queries=1,
            successful_queries=1,
            query_results=[
                {
                    "query_id": "q01",
                    "status": "SUCCESS",
                    "execution_time_ms": 150,
                    "rows_returned": 4,
                }
            ],
        )

        payload = build_result_payload(results)

        assert payload["result_schema_version"] == "2.3"
        assert payload["version"] == "2.3"
        assert len(payload["queries"]) == 1
        assert payload["queries"][0]["id"] == "01"

    def test_schema_v2_plans_companion_none_when_no_plans(self) -> None:

        results = make_benchmark_results(
            benchmark_id="tpch",
            benchmark_name="tpch",
            platform="duckdb",
            scale_factor=1.0,
            execution_id="test_005",
            timestamp=datetime(2025, 1, 1, 12, 0, 0),
            duration_seconds=10.0,
            total_queries=1,
            successful_queries=1,
        )

        plans_payload = build_plans_payload(results)

        assert plans_payload is None

    def test_schema_v2_plans_companion_with_plans(self) -> None:

        root = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_1",
            table_name="lineitem",
        )
        plan = QueryPlanDAG(
            query_id="q01",
            platform="duckdb",
            logical_root=root,
            estimated_cost=100.0,
        )

        results = make_benchmark_results(
            benchmark_id="tpch",
            benchmark_name="tpch",
            platform="duckdb",
            scale_factor=1.0,
            execution_id="test_006",
            timestamp=datetime(2025, 1, 1, 12, 0, 0),
            duration_seconds=10.0,
            total_queries=1,
            successful_queries=1,
            query_plans_captured=1,
            query_results=[
                {
                    "query_id": "q01",
                    "status": "SUCCESS",
                    "execution_time_ms": 150,
                    "rows_returned": 4,
                    "query_plan": plan,
                    "plan_fingerprint": plan.plan_fingerprint,
                }
            ],
        )

        plans_payload = build_plans_payload(results)

        assert plans_payload is not None
        assert plans_payload["version"] == "2.3"
        assert plans_payload["run_id"] == "test_006"
        assert plans_payload["plans_captured"] == 1
        assert "q01" in plans_payload["queries"]

    def test_schema_v2_plans_companion_uses_to_dict_not_asdict(self) -> None:
        root = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_1",
            table_name="lineitem",
        )
        plan = QueryPlanDAG(
            query_id="q01",
            platform="duckdb",
            logical_root=root,
            estimated_cost=100.0,
        )

        results = make_benchmark_results(
            benchmark_id="tpch",
            benchmark_name="tpch",
            platform="duckdb",
            scale_factor=1.0,
            execution_id="test_007",
            timestamp=datetime(2025, 1, 1, 12, 0, 0),
            duration_seconds=10.0,
            total_queries=1,
            successful_queries=1,
            query_plans_captured=1,
            query_results=[
                {
                    "query_id": "q01",
                    "status": "SUCCESS",
                    "execution_time_ms": 150,
                    "rows_returned": 4,
                    "query_plan": plan,
                    "plan_fingerprint": plan.plan_fingerprint,
                }
            ],
        )

        plans_payload = build_plans_payload(results)

        assert plans_payload is not None
        plan_dict = plans_payload["queries"]["q01"]["plan"]
        assert "fingerprint_integrity" not in plan_dict
        assert plan_dict == plan.to_dict()

    def test_companion_entry_plan_format_dag_for_structured_plan(self) -> None:
        root = LogicalOperator(operator_type=LogicalOperatorType.SCAN, operator_id="scan_1", table_name="lineitem")
        plan = QueryPlanDAG(query_id="q01", platform="duckdb", logical_root=root)
        results = make_benchmark_results(
            benchmark_id="tpch",
            benchmark_name="tpch",
            platform="duckdb",
            scale_factor=1.0,
            execution_id="fmt_dag",
            timestamp=datetime(2025, 1, 1, 12, 0, 0),
            duration_seconds=1.0,
            total_queries=1,
            successful_queries=1,
            query_plans_captured=1,
            query_results=[
                {
                    "query_id": "q01",
                    "status": "SUCCESS",
                    "execution_time_ms": 150,
                    "rows_returned": 4,
                    "query_plan": plan,
                    "plan_fingerprint": plan.plan_fingerprint,
                }
            ],
        )

        payload = build_plans_payload(results)

        assert payload is not None
        assert payload["queries"]["q01"]["plan_format"] == "dag"

    def test_companion_entry_plan_format_text_for_textonly_plan(self) -> None:
        from benchbox.core.dataframe.profiling import QueryPlan

        text_plan = QueryPlan(
            platform="polars",
            plan_type="logical",
            plan_text="FILTER [(col > 5)]\n  SCAN lineitem",
        )
        results = make_benchmark_results(
            benchmark_id="tpch",
            benchmark_name="tpch",
            platform="polars",
            scale_factor=1.0,
            execution_id="fmt_text",
            timestamp=datetime(2025, 1, 1, 12, 0, 0),
            duration_seconds=1.0,
            total_queries=1,
            successful_queries=1,
            query_plans_captured=1,
            query_results=[
                {
                    "query_id": "q01",
                    "status": "SUCCESS",
                    "execution_time_ms": 150,
                    "rows_returned": 4,
                    "query_plan": text_plan,
                }
            ],
        )

        payload = build_plans_payload(results)

        assert payload is not None
        entry = payload["queries"]["q01"]
        assert entry["plan_format"] == "text"
        assert "logical_root" not in entry["plan"]

    def test_schema_v2_plans_companion_multi_stream_keys_per_stream(self) -> None:

        def _plan(fingerprint: str) -> QueryPlanDAG:
            root = LogicalOperator(operator_type=LogicalOperatorType.SCAN, operator_id="scan_1", table_name="lineitem")
            return QueryPlanDAG(query_id="q06", platform="duckdb", logical_root=root, plan_fingerprint=fingerprint)

        plan_stream0 = _plan("a" * 64)
        plan_stream1 = _plan("b" * 64)

        results = make_benchmark_results(
            benchmark_id="tpch",
            benchmark_name="tpch",
            platform="duckdb",
            scale_factor=1.0,
            execution_id="test_multi_stream",
            timestamp=datetime(2025, 1, 1, 12, 0, 0),
            total_queries=2,
            successful_queries=2,
            query_plans_captured=2,
            query_results=[
                {
                    "query_id": "q06",
                    "status": "SUCCESS",
                    "stream_id": 0,
                    "query_plan": plan_stream0,
                    "plan_fingerprint": plan_stream0.plan_fingerprint,
                },
                {
                    "query_id": "q06",
                    "status": "SUCCESS",
                    "stream_id": 1,
                    "query_plan": plan_stream1,
                    "plan_fingerprint": plan_stream1.plan_fingerprint,
                },
            ],
        )

        plans_payload = build_plans_payload(results)

        assert plans_payload is not None
        assert plans_payload["plans_captured"] == 1
        queries = plans_payload["queries"]
        assert "q06" not in queries, "bare query_id key must not be used once ambiguous across streams"
        assert queries["q06#0"]["fingerprint"] == "a" * 64
        assert queries["q06#1"]["fingerprint"] == "b" * 64

    def test_schema_v2_plans_companion_cross_phase_same_stream_id_disambiguated(self) -> None:

        def _plan(fingerprint: str) -> QueryPlanDAG:
            root = LogicalOperator(operator_type=LogicalOperatorType.SCAN, operator_id="scan_1", table_name="lineitem")
            return QueryPlanDAG(query_id="q06", platform="duckdb", logical_root=root, plan_fingerprint=fingerprint)

        power_plan = _plan("a" * 64)
        throughput_plan = _plan("b" * 64)

        results = make_benchmark_results(
            benchmark_id="tpch",
            benchmark_name="tpch",
            platform="duckdb",
            scale_factor=1.0,
            execution_id="test_cross_phase",
            timestamp=datetime(2025, 1, 1, 12, 0, 0),
            total_queries=2,
            successful_queries=2,
            query_plans_captured=2,
            query_results=[
                {
                    "query_id": "q06",
                    "status": "SUCCESS",
                    "stream_id": 0,
                    "test_type": "power",
                    "query_plan": power_plan,
                    "plan_fingerprint": power_plan.plan_fingerprint,
                },
                {
                    "query_id": "q06",
                    "status": "SUCCESS",
                    "stream_id": 0,
                    "test_type": "throughput",
                    "query_plan": throughput_plan,
                    "plan_fingerprint": throughput_plan.plan_fingerprint,
                },
            ],
        )

        plans_payload = build_plans_payload(results)

        assert plans_payload is not None
        assert plans_payload["plans_captured"] == 1
        queries = plans_payload["queries"]
        assert "q06#0" not in queries, "bare stream_id key must not be used once ambiguous across phases too"
        assert queries["q06#0:power"]["fingerprint"] == "a" * 64
        assert queries["q06#0:throughput"]["fingerprint"] == "b" * 64

    def test_schema_v2_export_complex_plan(self) -> None:
        scan_orders = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_1",
            table_name="orders",
        )
        scan_lineitem = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_2",
            table_name="lineitem",
        )
        join = LogicalOperator(
            operator_type=LogicalOperatorType.JOIN,
            operator_id="join_1",
            join_type=JoinType.INNER,
            join_conditions=["o_orderkey = l_orderkey"],
            children=[scan_orders, scan_lineitem],
            physical_operator=PhysicalOperator(
                operator_type="HashJoin",
                operator_id="phys_join_1",
                properties={"cost": 500.0, "memory_mb": 128},
            ),
        )

        plan = QueryPlanDAG(
            query_id="q01",
            platform="duckdb",
            logical_root=join,
            estimated_cost=500.0,
            estimated_rows=10000,
        )

        results = make_benchmark_results(
            benchmark_id="tpch",
            benchmark_name="tpch",
            platform="duckdb",
            scale_factor=1.0,
            execution_id="test_008",
            timestamp=datetime(2025, 1, 1, 12, 0, 0),
            duration_seconds=10.0,
            total_queries=1,
            successful_queries=1,
            query_plans_captured=1,
            query_results=[
                {
                    "query_id": "q01",
                    "status": "SUCCESS",
                    "execution_time_ms": 350,
                    "rows_returned": 10000,
                    "query_plan": plan,
                    "plan_fingerprint": plan.plan_fingerprint,
                }
            ],
        )

        payload = build_result_payload(results)
        assert payload["result_schema_version"] == "2.3"
        assert payload["version"] == "2.3"
        assert len(payload["queries"]) == 1
        assert payload["queries"][0]["id"] == "01"
        assert payload["queries"][0]["ms"] == 350.0

        plans_payload = build_plans_payload(results)
        assert plans_payload is not None
        assert "q01" in plans_payload["queries"]

        plan_data = plans_payload["queries"]["q01"]["plan"]
        assert plan_data["estimated_cost"] == 500.0
        assert plan_data["estimated_rows"] == 10000


class TestBackwardCompatibility:
    def test_existing_code_without_plans_still_works(self) -> None:

        qe = QueryExecution(
            query_id="q01",
            stream_id="power",
            execution_order=1,
            execution_time_ms=150,
            status="SUCCESS",
            rows_returned=4,
            cost=0.00012,
        )

        result = asdict(qe)
        assert result["query_id"] == "q01"
        assert result["cost"] == 0.00012
        assert result["query_plan"] is None

    def test_schema_v2_with_and_without_plans(self) -> None:
        results_no_plans = make_benchmark_results(
            benchmark_id="tpch",
            benchmark_name="tpch",
            platform="duckdb",
            scale_factor=1.0,
            execution_id="test_009",
            timestamp=datetime(2025, 1, 1, 12, 0, 0),
            duration_seconds=10.0,
            total_queries=1,
            successful_queries=1,
            query_results=[
                {"query_id": "1", "execution_time_ms": 100, "status": "SUCCESS", "rows_returned": 4},
            ],
        )

        payload_no_plans = build_result_payload(results_no_plans)
        assert payload_no_plans["result_schema_version"] == "2.3"
        assert payload_no_plans["version"] == "2.3"
        plans_payload_none = build_plans_payload(results_no_plans)
        assert plans_payload_none is None

        root = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_1",
            table_name="test",
        )
        plan = QueryPlanDAG(query_id="q01", platform="duckdb", logical_root=root)

        results_with_plans = make_benchmark_results(
            benchmark_id="tpch",
            benchmark_name="tpch",
            platform="duckdb",
            scale_factor=1.0,
            execution_id="test_010",
            timestamp=datetime(2025, 1, 1, 12, 0, 0),
            duration_seconds=10.0,
            total_queries=1,
            successful_queries=1,
            query_plans_captured=1,
            query_results=[
                {
                    "query_id": "q01",
                    "status": "SUCCESS",
                    "execution_time_ms": 100,
                    "query_plan": plan,
                }
            ],
        )

        payload_with_plans = build_result_payload(results_with_plans)
        assert payload_with_plans["result_schema_version"] == "2.3"
        assert payload_with_plans["version"] == "2.3"
        plans_payload = build_plans_payload(results_with_plans)
        assert plans_payload is not None

        assert set(payload_no_plans.keys()) == set(payload_with_plans.keys())


class TestSchemaV2Validation:
    def test_validator_rejects_missing_version(self) -> None:

        from benchbox.core.results.schema import SchemaV2ValidationError, SchemaV2Validator

        validator = SchemaV2Validator()
        payload = {
            "run": {"id": "test", "timestamp": "2025-01-01T00:00:00", "total_duration_ms": 1000, "query_time_ms": 500},
            "benchmark": {"id": "test", "name": "Test", "scale_factor": 1.0},
            "platform": {"name": "Test"},
            "summary": {
                "queries": {"total": 0, "passed": 0, "failed": 0},
                "timing": {"total_ms": 0, "avg_ms": 0, "min_ms": 0, "max_ms": 0},
            },
            "queries": [],
        }

        with pytest.raises(SchemaV2ValidationError) as exc:
            validator.validate(payload)
        assert "missing keys" in str(exc.value)

    def test_validator_rejects_wrong_version(self) -> None:

        from benchbox.core.results.schema import SchemaV2ValidationError, SchemaV2Validator

        validator = SchemaV2Validator()
        payload = {
            "version": "1.1",
            "run": {"id": "test", "timestamp": "2025-01-01T00:00:00", "total_duration_ms": 1000, "query_time_ms": 500},
            "benchmark": {"id": "test", "name": "Test", "scale_factor": 1.0},
            "platform": {"name": "Test"},
            "summary": {
                "queries": {"total": 0, "passed": 0, "failed": 0},
                "timing": {"total_ms": 0, "avg_ms": 0, "min_ms": 0, "max_ms": 0},
            },
            "queries": [],
        }

        with pytest.raises(SchemaV2ValidationError) as exc:
            validator.validate(payload)
        assert "runtime result schema policy" in str(exc.value)
        assert "schema versions 2.0, 2.1, 2.2, 2.3, and 2.4" in str(exc.value)

    def test_validator_rejects_missing_run_fields(self) -> None:

        from benchbox.core.results.schema import SchemaV2ValidationError, SchemaV2Validator

        validator = SchemaV2Validator()
        payload = {
            "version": "2.0",
            "run": {"id": "test"},
            "benchmark": {"id": "test", "name": "Test", "scale_factor": 1.0},
            "platform": {"name": "Test"},
            "summary": {
                "queries": {"total": 0, "passed": 0, "failed": 0},
                "timing": {"total_ms": 0, "avg_ms": 0, "min_ms": 0, "max_ms": 0},
            },
            "queries": [],
        }

        with pytest.raises(SchemaV2ValidationError) as exc:
            validator.validate(payload)
        assert "run block missing keys" in str(exc.value)

    def test_validator_rejects_unexpected_keys(self) -> None:

        from benchbox.core.results.schema import SchemaV2ValidationError, SchemaV2Validator

        validator = SchemaV2Validator()
        payload = {
            "version": "2.0",
            "run": {"id": "test", "timestamp": "2025-01-01T00:00:00", "total_duration_ms": 1000, "query_time_ms": 500},
            "benchmark": {"id": "test", "name": "Test", "scale_factor": 1.0},
            "platform": {"name": "Test"},
            "summary": {
                "queries": {"total": 0, "passed": 0, "failed": 0},
                "timing": {"total_ms": 0, "avg_ms": 0, "min_ms": 0, "max_ms": 0},
            },
            "queries": [],
            "unexpected_key": {},
        }

        with pytest.raises(SchemaV2ValidationError) as exc:
            validator.validate(payload)
        assert "unexpected keys" in str(exc.value)

    def test_validator_accepts_valid_payload(self) -> None:
        from benchbox.core.results.schema import SchemaV2Validator

        validator = SchemaV2Validator()
        payload = {
            "version": "2.0",
            "run": {"id": "test", "timestamp": "2025-01-01T00:00:00", "total_duration_ms": 1000, "query_time_ms": 500},
            "benchmark": {"id": "test", "name": "Test", "scale_factor": 1.0},
            "platform": {"name": "Test"},
            "summary": {
                "queries": {"total": 1, "passed": 1, "failed": 0},
                "timing": {"total_ms": 500, "avg_ms": 500, "min_ms": 500, "max_ms": 500},
            },
            "queries": [{"id": "1", "ms": 500.0, "rows": 10}],
        }

        validator.validate(payload)

    def test_empty_query_list_handled(self) -> None:

        results = make_benchmark_results(
            benchmark_id="empty_test",
            benchmark_name="Empty Test",
            platform="duckdb",
            scale_factor=1.0,
            execution_id="empty-001",
            timestamp=datetime(2025, 1, 1, 12, 0, 0),
            duration_seconds=1.0,
        )

        payload = build_result_payload(results)

        assert payload["result_schema_version"] == "2.3"
        assert payload["version"] == "2.3"
        assert payload["summary"]["queries"]["total"] == 0
        assert payload["summary"]["queries"]["passed"] == 0
        assert payload["summary"]["queries"]["failed"] == 0
        assert payload["summary"]["timing"]["total_ms"] == 0
        assert payload["summary"]["timing"]["avg_ms"] == 0
        assert payload["queries"] == []

    def test_normalized_cost_exported_and_validated(self) -> None:
        results = make_benchmark_results(
            benchmark_id="cost_test",
            benchmark_name="Cost Test",
            platform="snowflake",
            scale_factor=1.0,
            execution_id="cost-001",
            timestamp=datetime(2025, 1, 1, 12, 0, 0),
            duration_seconds=1.0,
            total_queries=1,
            successful_queries=1,
            query_results=[{"query_id": "1", "execution_time_ms": 100, "rows_returned": 4, "status": "SUCCESS"}],
            cost_summary={
                "total_cost": 1.25,
                "cost_model": "actual",
                "normalized_cost": {
                    "normalized_cost_usd": "1.25",
                    "cost_usd": "1.25",
                    "cost_model_version": "2026.05.0",
                    "cost_model_source": "benchbox.core.cost.pricing",
                    "cost_scope": "compute_only",
                    "cost_status": "normalized",
                    "billing_unit": "credit",
                    "pricing_region": "us-east-1",
                    "deployment": {
                        "cloud_provider": "aws",
                        "cloud_region": "us-east-1",
                        "instance_type": None,
                        "warehouse_size": "MEDIUM",
                        "node_count": None,
                        "cluster_size": None,
                        "storage_format": None,
                        "storage_tier": None,
                    },
                },
            },
        )

        payload = build_result_payload(results)

        assert payload["cost"]["total_usd"] == 1.25
        assert payload["normalized_cost"]["cost_status"] == "normalized"
        assert payload["normalized_cost"]["deployment"]["warehouse_size"] == "MEDIUM"

        from benchbox.core.results.schema import SchemaV2Validator

        SchemaV2Validator().validate(payload)

    def test_driver_metadata_exported(self) -> None:

        results = make_benchmark_results(
            benchmark_id="driver_test",
            benchmark_name="Driver Test",
            platform="duckdb",
            scale_factor=1.0,
            execution_id="driver-001",
            timestamp=datetime(2025, 1, 1, 12, 0, 0),
            duration_seconds=1.0,
            total_queries=1,
            successful_queries=1,
            query_results=[{"query_id": "1", "execution_time_ms": 100, "rows_returned": 4, "status": "SUCCESS"}],
            driver_package="duckdb",
            driver_version_requested="1.0.0",
            driver_version_resolved="1.0.1",
            driver_version_actual="1.0.1",
            driver_runtime_strategy="current-process",
            driver_auto_install=True,
            platform_info={"name": "duckdb", "version": "1.0.1"},
        )

        payload = build_result_payload(results)

        assert payload["result_schema_version"] == "2.3"
        assert payload["version"] == "2.3"
        assert payload["platform"]["name"] == "duckdb"
        assert payload["platform"]["version"] == "1.0.1"
        assert payload["platform"]["driver_package"] == "duckdb"
        assert payload["platform"]["driver_requested_version"] == "1.0.0"
        assert payload["platform"]["driver_resolved_version"] == "1.0.1"
        assert payload["platform"]["driver_actual_version"] == "1.0.1"
        assert payload["platform"]["driver_runtime_strategy"] == "current-process"
        assert payload["execution"]["driver_version_requested"] == "1.0.0"
        assert payload["execution"]["driver_version_resolved"] == "1.0.1"
        assert payload["execution"]["driver_version_actual"] == "1.0.1"

    def test_timing_computed_from_queries(self) -> None:
        results = make_benchmark_results(
            benchmark_id="timing_test",
            benchmark_name="Timing Test",
            platform="duckdb",
            scale_factor=1.0,
            execution_id="timing-001",
            timestamp=datetime(2025, 1, 1, 12, 0, 0),
            duration_seconds=10.0,
            total_queries=3,
            successful_queries=3,
            total_execution_time=999.0,
            average_query_time=333.0,
            query_results=[
                {"query_id": "1", "execution_time_ms": 100, "rows_returned": 4, "status": "SUCCESS"},
                {"query_id": "2", "execution_time_ms": 200, "rows_returned": 8, "status": "SUCCESS"},
                {"query_id": "3", "execution_time_ms": 300, "rows_returned": 12, "status": "SUCCESS"},
            ],
        )

        payload = build_result_payload(results)

        assert payload["summary"]["timing"]["total_ms"] == 600.0
        assert payload["summary"]["timing"]["avg_ms"] == 200.0
        assert payload["summary"]["timing"]["min_ms"] == 100.0
        assert payload["summary"]["timing"]["max_ms"] == 300.0


def _chain_plan(depth: int) -> QueryPlanDAG:
    root = None
    for level in reversed(range(depth)):
        root = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id=f"op_{level}",
            table_name="lineitem",
            children=[root] if root is not None else [],
        )
    assert root is not None
    return QueryPlanDAG(query_id="q01", platform="duckdb", logical_root=root)


def _results_with_plan(plan: QueryPlanDAG, platform_options: dict | None) -> object:
    run_config = {"platform_options": platform_options} if platform_options is not None else {}
    return make_benchmark_results(
        benchmark_id="tpch",
        benchmark_name="tpch",
        platform="duckdb",
        scale_factor=1.0,
        execution_id="test-depth",
        timestamp=datetime(2025, 1, 1, 12, 0, 0),
        duration_seconds=10.0,
        total_queries=1,
        successful_queries=1,
        query_plans_captured=1,
        execution_metadata={"run_config": run_config},
        query_results=[
            {
                "query_id": "q01",
                "status": "SUCCESS",
                "execution_time_ms": 150,
                "rows_returned": 4,
                "query_plan": plan,
                "plan_fingerprint": plan.plan_fingerprint,
            }
        ],
    )


class TestCompanionMaxDepth:
    def test_companion_truncates_at_configured_depth(self) -> None:
        import json

        plan = _chain_plan(3)
        payload = build_plans_payload(_results_with_plan(plan, {"plan_max_depth": 1}))

        assert payload is not None
        entry = payload["queries"]["q01"]
        assert entry["plan_format"] == "dag"
        assert "truncated_at_depth" in json.dumps(entry["plan"])

    def test_companion_full_depth_when_unset(self) -> None:
        import json

        plan = _chain_plan(3)
        payload = build_plans_payload(_results_with_plan(plan, None))

        assert payload is not None
        entry = payload["queries"]["q01"]
        assert "truncated_at_depth" not in json.dumps(entry["plan"])
        leaf = entry["plan"]["logical_root"]["children"][0]["children"][0]
        assert leaf["operator_id"] == "op_2"

    def test_companion_marks_truncation_at_payload_level(self) -> None:
        plan = _chain_plan(3)
        payload = build_plans_payload(_results_with_plan(plan, {"plan_max_depth": 1}))

        assert payload is not None
        assert payload["max_depth"] == 1
        assert payload["truncated"] is True

    def test_companion_untruncated_payload_level_marker(self) -> None:
        from benchbox.core.results.query_plan_models import DEFAULT_PLAN_MAX_DEPTH

        plan = _chain_plan(3)
        payload = build_plans_payload(_results_with_plan(plan, None))

        assert payload is not None
        assert payload["max_depth"] == DEFAULT_PLAN_MAX_DEPTH
        assert payload["truncated"] is False

    def test_resolve_companion_max_depth_fallbacks(self) -> None:
        from benchbox.core.results.query_plan_models import DEFAULT_PLAN_MAX_DEPTH
        from benchbox.core.results.schema import _resolve_companion_max_depth

        assert _resolve_companion_max_depth(object()) == DEFAULT_PLAN_MAX_DEPTH
        assert (
            _resolve_companion_max_depth(
                make_benchmark_results(execution_metadata={"run_config": {"platform_options": {"plan_max_depth": 4}}})
            )
            == 4
        )
        assert (
            _resolve_companion_max_depth(
                make_benchmark_results(
                    execution_metadata={"run_config": {"platform_options": {"plan_max_depth": "deep"}}}
                )
            )
            == DEFAULT_PLAN_MAX_DEPTH
        )
