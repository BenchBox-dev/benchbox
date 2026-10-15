from __future__ import annotations

from types import SimpleNamespace

import pytest

from benchbox.core.throughput.entrypoints import (
    finalize_throughput_metrics,
    require_adapter,
    require_stream_minimum,
    warn_legacy_throughput_api,
)
from benchbox.core.throughput.result import ThroughputResult, ThroughputStreamResult

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _stream(stream_id: int, failed: int = 0) -> ThroughputStreamResult:
    return ThroughputStreamResult(
        stream_id=stream_id,
        start_time=0.0,
        end_time=1.0,
        duration=1.0,
        queries_executed=22,
        queries_successful=22 - failed,
        queries_failed=failed,
        success=failed == 0,
    )


def _result(streams: list[ThroughputStreamResult], metric: float | None = 10.0) -> ThroughputResult:
    return ThroughputResult(
        start_time="s",
        end_time="e",
        total_time=1.0,
        throughput_at_size=metric,
        streams_executed=len(streams),
        streams_successful=sum(1 for stream in streams if stream.success),
        stream_results=streams,
        query_throughput=5.0,
    )


class TestStreamMinimum:
    @pytest.mark.parametrize("count", [-1, 0, 1])
    def test_counts_below_two_are_rejected_with_the_source(self, count):
        with pytest.raises(ValueError, match=r"at least 2.*'num_streams'"):
            require_stream_minimum(count, "num_streams")

    def test_two_or_more_pass_through(self):
        assert require_stream_minimum(2, "num_streams") == 2
        assert require_stream_minimum(7, "num_streams") == 7


class TestFinalizeMetrics:
    def test_failed_refresh_stream_withholds_the_metric(self):
        result = _result([_stream(0), _stream(1)])
        result.refresh_stream = SimpleNamespace(success=False)

        finalize_throughput_metrics(result, 2, None)

        assert result.success is False
        assert result.throughput_at_size is None

    def test_all_streams_successful_keeps_the_metric(self):
        result = _result([_stream(0), _stream(1)])

        finalize_throughput_metrics(result, 2, None)

        assert result.success is True
        assert result.throughput_at_size == 10.0

    def test_any_failed_stream_withholds_the_metric(self):
        result = _result([_stream(0), _stream(1, failed=1)])

        finalize_throughput_metrics(result, 2, None)

        assert result.success is False
        assert result.throughput_at_size is None
        assert result.query_throughput == 0.0

    def test_missing_stream_withholds_the_metric(self):
        result = _result([_stream(0)])

        finalize_throughput_metrics(result, 2, None)

        assert result.success is False
        assert result.throughput_at_size is None

    def test_query_subset_runs_report_no_metric(self):
        result = _result([_stream(0), _stream(1)])

        finalize_throughput_metrics(result, 2, ["1", "6"])

        assert result.success is True
        assert result.throughput_at_size is None


class TestDeprecationNotice:
    def test_warning_names_the_api_and_the_supported_driver(self):
        with pytest.warns(DeprecationWarning, match=r"Some.api is deprecated.*TPCDSThroughputTest"):
            warn_legacy_throughput_api("Some.api", "tpcds")

    def test_warning_points_at_the_caller(self):
        with pytest.warns(DeprecationWarning) as caught:
            warn_legacy_throughput_api("Some.api", "tpch", stacklevel=2)

        assert caught[0].filename == __file__


class TestRequireAdapter:
    def test_missing_adapter_is_refused_with_the_supported_path(self):
        with pytest.raises(TypeError, match=r"Some.api needs adapter=.*benchbox run --phases throughput"):
            require_adapter("Some.api", None)

    def test_adapter_passes_through(self):
        adapter = object()

        assert require_adapter("Some.api", adapter) is adapter
