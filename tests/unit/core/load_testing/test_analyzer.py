# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.experimental.load_testing.analyzer import (
    ContentionAnalysis,
    LoadAnalyzer,
    QueueAnalysis,
    ScalingAnalysis,
)
from benchbox.experimental.load_testing.executor import (
    ConcurrentLoadResult,
    QueryExecution,
    StreamResult,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture
def sample_result():
    executions = []
    for i in range(10):
        executions.append(
            QueryExecution(
                query_id=f"q{i}",
                stream_id=0,
                start_time=float(i),
                end_time=float(i) + 0.1 + (i * 0.01),
                success=i != 5,
                error="Query timeout" if i == 5 else None,
                queue_wait_time=0.05 if i < 3 else 0,
            )
        )

    stream = StreamResult(
        stream_id=0,
        queries_executed=10,
        queries_succeeded=9,
        queries_failed=1,
        total_time_seconds=2.0,
        query_executions=executions,
    )

    return ConcurrentLoadResult(
        start_time=0,
        end_time=2.0,
        total_duration_seconds=2.0,
        streams=[stream],
        total_streams_executed=1,
        total_streams_succeeded=1,
        total_queries_executed=10,
        total_queries_succeeded=9,
        total_queries_failed=1,
        overall_throughput=5.0,
        max_concurrency_reached=5,
    )


@pytest.fixture
def result_with_no_queuing():
    executions = [
        QueryExecution(
            query_id=f"q{i}",
            stream_id=0,
            start_time=float(i),
            end_time=float(i) + 0.1,
            success=True,
            queue_wait_time=0,
        )
        for i in range(5)
    ]

    stream = StreamResult(
        stream_id=0,
        queries_executed=5,
        queries_succeeded=5,
        queries_failed=0,
        total_time_seconds=1.0,
        query_executions=executions,
    )

    return ConcurrentLoadResult(
        start_time=0,
        end_time=1.0,
        total_duration_seconds=1.0,
        streams=[stream],
        total_streams_executed=1,
        total_streams_succeeded=1,
        total_queries_executed=5,
        total_queries_succeeded=5,
        total_queries_failed=0,
        overall_throughput=5.0,
        max_concurrency_reached=1,
    )


class TestQueueAnalysis:
    def test_analyze_queue_with_wait_times(self, sample_result):
        analyzer = LoadAnalyzer(sample_result)
        analysis = analyzer.analyze_queue()

        assert isinstance(analysis, QueueAnalysis)
        assert analysis.avg_wait_ms >= 0
        assert analysis.max_wait_ms >= analysis.min_wait_ms

    def test_analyze_queue_no_wait_times(self, result_with_no_queuing):
        analyzer = LoadAnalyzer(result_with_no_queuing)
        analysis = analyzer.analyze_queue()

        assert analysis.queueing_detected is False
        assert analysis.queueing_severity == "none"

    def test_queue_severity_none(self, result_with_no_queuing):
        analyzer = LoadAnalyzer(result_with_no_queuing)
        analysis = analyzer.analyze_queue()

        assert analysis.queueing_severity == "none"

    def test_queue_time_ratio(self, sample_result):
        analyzer = LoadAnalyzer(sample_result)
        analysis = analyzer.analyze_queue()

        assert 0 <= analysis.queue_time_ratio <= 1


class TestContentionAnalysis:
    def test_analyze_contention_basic(self, sample_result):
        analyzer = LoadAnalyzer(sample_result)
        analysis = analyzer.analyze_contention()

        assert isinstance(analysis, ContentionAnalysis)
        assert analysis.latency_stdev_ms >= 0
        assert analysis.failure_rate >= 0

    def test_contention_slow_query_detection(self, sample_result):
        analyzer = LoadAnalyzer(sample_result)
        analysis = analyzer.analyze_contention()

        assert isinstance(analysis.slow_query_count, int)
        assert 0 <= analysis.slow_query_ratio <= 1

    def test_contention_type_detection(self, sample_result):
        analyzer = LoadAnalyzer(sample_result)
        analysis = analyzer.analyze_contention()

        assert analysis.contention_type in ["none", "resource", "lock", "connection", "unknown"]

    def test_contention_recommendations(self, sample_result):
        analyzer = LoadAnalyzer(sample_result)
        analysis = analyzer.analyze_contention()

        assert isinstance(analysis.recommendations, list)

    def test_empty_result_contention(self):
        empty_result = ConcurrentLoadResult(
            start_time=0,
            end_time=1,
            total_duration_seconds=1,
            streams=[],
            total_streams_executed=0,
            total_streams_succeeded=0,
            total_queries_executed=0,
            total_queries_succeeded=0,
            total_queries_failed=0,
            overall_throughput=0,
            max_concurrency_reached=0,
        )
        analyzer = LoadAnalyzer(empty_result)
        analysis = analyzer.analyze_contention()

        assert analysis.contention_detected is False


class TestScalingAnalysis:
    def test_analyze_scaling_single_result(self, sample_result):
        analyzer = LoadAnalyzer(sample_result)
        analysis = analyzer.analyze_scaling()

        assert isinstance(analysis, ScalingAnalysis)
        assert len(analysis.concurrency_levels) == 1
        assert analysis.optimal_concurrency > 0

    def test_analyze_scaling_multiple_results(self, sample_result):
        results_by_concurrency = {}

        for concurrency in [1, 2, 4]:
            executions = [
                QueryExecution(
                    query_id=f"q{i}",
                    stream_id=0,
                    start_time=float(i),
                    end_time=float(i) + 0.1,
                    success=True,
                )
                for i in range(10)
            ]
            stream = StreamResult(
                stream_id=0,
                queries_executed=10,
                queries_succeeded=10,
                queries_failed=0,
                total_time_seconds=1.0,
                query_executions=executions,
            )
            results_by_concurrency[concurrency] = ConcurrentLoadResult(
                start_time=0,
                end_time=1.0,
                total_duration_seconds=1.0,
                streams=[stream],
                total_streams_executed=1,
                total_streams_succeeded=1,
                total_queries_executed=10,
                total_queries_succeeded=10,
                total_queries_failed=0,
                overall_throughput=10.0 * concurrency * 0.8,
                max_concurrency_reached=concurrency,
            )

        analyzer = LoadAnalyzer(sample_result)
        analysis = analyzer.analyze_scaling(results_by_concurrency)

        assert len(analysis.concurrency_levels) == 3
        assert analysis.scaling_efficiency > 0

    def test_scaling_type_detection(self, sample_result):
        analyzer = LoadAnalyzer(sample_result)
        analysis = analyzer.analyze_scaling()

        assert analysis.scaling_type in ["linear", "sublinear", "saturation", "degradation", "unknown"]

    def test_parallelizable_fraction(self, sample_result):
        analyzer = LoadAnalyzer(sample_result)
        analysis = analyzer.analyze_scaling()

        assert 0 <= analysis.parallelizable_fraction <= 1


class TestLoadAnalyzerSummary:
    def test_get_summary(self, sample_result):
        analyzer = LoadAnalyzer(sample_result)
        summary = analyzer.get_summary()

        assert "test_info" in summary
        assert "queue" in summary
        assert "contention" in summary
        assert "scaling" in summary

    def test_summary_test_info(self, sample_result):
        analyzer = LoadAnalyzer(sample_result)
        summary = analyzer.get_summary()

        assert "duration_seconds" in summary["test_info"]
        assert "total_queries" in summary["test_info"]
        assert "throughput_qps" in summary["test_info"]
        assert "success_rate" in summary["test_info"]

    def test_summary_queue_info(self, sample_result):
        analyzer = LoadAnalyzer(sample_result)
        summary = analyzer.get_summary()

        assert "detected" in summary["queue"]
        assert "severity" in summary["queue"]

    def test_summary_contention_info(self, sample_result):
        analyzer = LoadAnalyzer(sample_result)
        summary = analyzer.get_summary()

        assert "detected" in summary["contention"]
        assert "type" in summary["contention"]
        assert "recommendations" in summary["contention"]


class TestContentionAnalysisWithErrors:
    def test_timeout_error_detection(self):
        executions = [
            QueryExecution(
                query_id="q1",
                stream_id=0,
                start_time=0,
                end_time=30,
                success=False,
                error="Query timeout after 30s",
            )
        ]
        stream = StreamResult(
            stream_id=0,
            queries_executed=1,
            queries_succeeded=0,
            queries_failed=1,
            total_time_seconds=30,
            query_executions=executions,
        )
        result = ConcurrentLoadResult(
            start_time=0,
            end_time=30,
            total_duration_seconds=30,
            streams=[stream],
            total_streams_executed=1,
            total_streams_succeeded=0,
            total_queries_executed=1,
            total_queries_succeeded=0,
            total_queries_failed=1,
            overall_throughput=0.033,
            max_concurrency_reached=1,
        )

        analyzer = LoadAnalyzer(result)
        analysis = analyzer.analyze_contention()

        assert analysis.timeout_count == 1

    def test_connection_error_detection(self):
        executions = [
            QueryExecution(
                query_id="q1",
                stream_id=0,
                start_time=0,
                end_time=1,
                success=False,
                error="Connection refused",
            )
        ]
        stream = StreamResult(
            stream_id=0,
            queries_executed=1,
            queries_succeeded=0,
            queries_failed=1,
            total_time_seconds=1,
            query_executions=executions,
        )
        result = ConcurrentLoadResult(
            start_time=0,
            end_time=1,
            total_duration_seconds=1,
            streams=[stream],
            total_streams_executed=1,
            total_streams_succeeded=0,
            total_queries_executed=1,
            total_queries_succeeded=0,
            total_queries_failed=1,
            overall_throughput=1,
            max_concurrency_reached=1,
        )

        analyzer = LoadAnalyzer(result)
        analysis = analyzer.analyze_contention()

        assert analysis.connection_error_count == 1
        assert analysis.contention_type == "connection"


