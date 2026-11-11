import pytest

from benchbox.core.dataframe.benchmark_suite import (
    BenchmarkConfig,
    DataFrameBenchmarkSuite,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _FakeLazy:
    def __init__(self, events, plan_text="PLAN"):
        self._events = events
        self._plan_text = plan_text

    def explain(self, optimized=True):
        del optimized
        self._events.append("explain")
        return self._plan_text

    def collect(self):
        self._events.append("collect")
        return [1, 2, 3]


class _FakeQuery:
    def __init__(self, frame):
        self._frame = frame
        self.execute_calls = 0

    def get_impl_for_family(self, family):
        del family
        return lambda ctx: self._frame

    def execute(self, context, family):
        del context, family
        self.execute_calls += 1
        return self._frame


class _FakeRegistry:
    def __init__(self, query):
        self._query = query

    def get(self, query_id):
        del query_id
        return self._query


def _make_suite(frame, **config_kwargs):
    config = BenchmarkConfig(
        scale_factor=0.01,
        warmup_iterations=0,
        benchmark_iterations=2,
        track_memory=False,
        **config_kwargs,
    )
    suite = DataFrameBenchmarkSuite(config=config)
    query = _FakeQuery(frame)
    suite._query_registry = _FakeRegistry(query)
    return suite, query


class TestSuitePlanCapture:
    def test_plan_captured_once_after_measured_collects(self):
        events: list = []
        suite, query = _make_suite(_FakeLazy(events, "MY-PLAN"))
        result = suite._benchmark_query("Q1", context=object(), family="expression", platform_name="polars-df")
        assert result.status == "SUCCESS"
        assert result.query_plan == "MY-PLAN"

        assert events == ["collect", "collect", "explain", "explain"]
        assert query.execute_calls == 3
        assert len(result.execution_times_ms) == 2

    def test_capture_disabled_leaves_plan_none(self):
        events: list = []
        suite, query = _make_suite(_FakeLazy(events, "MY-PLAN"), capture_plans=False)
        result = suite._benchmark_query("Q1", context=object(), family="expression", platform_name="polars-df")
        assert result.status == "SUCCESS"
        assert result.query_plan is None
        assert events == ["collect", "collect"]
        assert query.execute_calls == 2

    def test_eager_platform_skips_capture_without_extra_execute(self):

        events: list = []
        suite, query = _make_suite(_FakeLazy(events, "MY-PLAN"))
        result = suite._benchmark_query("Q1", context=object(), family="pandas", platform_name="pandas-df")
        assert result.status == "SUCCESS"
        assert result.query_plan is None
        assert events == ["collect", "collect"]
        assert query.execute_calls == 2

    def test_capture_failure_degrades_to_none(self, monkeypatch):
        import benchbox.core.dataframe.benchmark_suite as suite_module

        def boom(frame, platform):
            del frame, platform
            raise RuntimeError("no plan today")

        monkeypatch.setattr(suite_module, "capture_query_plan", boom)
        events: list = []
        suite, query = _make_suite(_FakeLazy(events, "MY-PLAN"))
        result = suite._benchmark_query("Q1", context=object(), family="expression", platform_name="polars-df")
        assert result.status == "SUCCESS"
        assert result.query_plan is None
        assert events == ["collect", "collect"]
        assert query.execute_calls == 3


