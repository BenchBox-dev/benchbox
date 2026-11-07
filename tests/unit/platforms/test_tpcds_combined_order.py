from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from benchbox.core.power_harnesses import resolve_combined_harness
from benchbox.core.results.qphds import evaluate_qphds
from benchbox.core.throughput.result import ThroughputStreamResult
from benchbox.core.tpcds.throughput_test import TPCDSThroughputTestResult
from benchbox.platforms.base.execution import _combined_phase_plan, _refresh_window
from tests.unit.platforms.test_adapter_lifecycle import _LifecycleAdapter, _LifecycleBenchmark

pytestmark = [pytest.mark.unit, pytest.mark.medium, pytest.mark.tpcds]

STREAMS = 4
ORDER = ["load", "power", "throughput 1", "maintenance 1", "throughput 2", "maintenance 2"]


class _RecordingAdapter(_LifecycleAdapter):
    supports_refresh_functions = True
    power_query_results: list[dict[str, Any]] = [{"execution_time_seconds": 1.0}] * 100

    def __init__(self, order: list[str], **config: Any) -> None:
        super().__init__(**config)
        self.order = order
        self.throughput_windows: list[tuple[int, int]] = []
        self.refresh_windows: list[tuple[int, int]] = []

    def load_data(self, _benchmark: Any, _connection: Any, _data_dir: Path) -> tuple[dict[str, int], float, None]:
        self.order.append("load")
        return {"lineitem": 1}, 1000.0, None

    def _execute_tpcds_power_test(self, benchmark: Any, connection: Any, run_config: dict) -> list[dict[str, Any]]:
        self.order.append("power")
        self._last_power_test_result = SimpleNamespace(
            power_at_size=1.0,
            total_time=999.0,
            query_results=self.power_query_results,
            queries_executed=99,
            success=True,
            start_time="2026-01-01T00:00:00",
            end_time="2026-01-01T00:01:40",
        )
        return []


class _FakeThroughputTest:
    outstanding_first: list[int] = []
    windows: list[tuple[int, int]] = []
    order: list[str] = []

    def __init__(self, *, num_streams: int, **_kwargs: Any) -> None:
        self.num_streams = num_streams

    def run(self, config: Any) -> TPCDSThroughputTestResult:
        round_number = 1 if config.first_stream_id == 1 else 2
        self.order.append(f"throughput {round_number}")
        outstanding = list(self.outstanding_first) if round_number == 1 else []
        self.windows.append((config.first_stream_id, config.num_streams))
        streams = [
            ThroughputStreamResult(
                stream_id=stream_id,
                start_time=0.0,
                end_time=1.0,
                duration=1.0,
                queries_executed=99,
                queries_successful=99,
                queries_failed=0,
                success=True,
            )
            for stream_id in range(config.first_stream_id, config.first_stream_id + config.num_streams)
        ]
        return TPCDSThroughputTestResult(
            config=config,
            start_time="2026-01-01T00:00:00",
            end_time="2026-01-01T00:10:00",
            total_time=200.0 if round_number == 1 else 300.0,
            throughput_at_size=1.0,
            streams_executed=config.num_streams,
            streams_successful=config.num_streams,
            stream_results=streams,
            outstanding_stream_ids=outstanding,
            success=True,
            stream_numbering={"basis": "tpc_spec_throughput_streams_1_to_s", "first_stream_id": config.first_stream_id},
        )


class _FakeMaintenanceTest:
    windows: list[tuple[int, int]] = []
    order: list[str] = []
    output_dir = Path(".")

    def __init__(self, **_kwargs: Any) -> None:
        pass

    def run(self, config: Any) -> dict[str, Any]:
        round_number = 1 if config.first_refresh_set == 1 else 2
        self.order.append(f"maintenance {round_number}")
        self.windows.append((config.refresh_runs, config.first_refresh_set))
        operations = [
            SimpleNamespace(
                function="RF1",
                operation_type="insert",
                table_name="store_sales",
                duration=1.0,
                rows_affected=1,
                success=True,
                error=None,
                refresh_set=refresh_set,
                staged_rows={},
                rows_by_table={},
            )
            for refresh_set in range(config.first_refresh_set, config.first_refresh_set + config.refresh_runs)
        ]
        return {
            "success": True,
            "errors": [],
            "operations": operations,
            "insert_operations": len(operations),
            "delete_operations": 0,
            "total_operations": len(operations),
            "successful_operations": len(operations),
            "total_time": 10.0 if round_number == 1 else 20.0,
            "overall_throughput": 1.0,
            "start_time": "2026-01-01T00:00:00",
            "end_time": "2026-01-01T00:00:10",
            "seed": None,
            "maintenance_method": "dsdgen_refresh_sets",
        }


def _run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    outstanding_first: list[int] | None = None,
    power_query_results: list[dict[str, Any]] | None = None,
) -> tuple[Any, list[str], Any]:
    order: list[str] = []
    _FakeThroughputTest.outstanding_first = outstanding_first or []
    if power_query_results is not None:
        monkeypatch.setattr(_RecordingAdapter, "power_query_results", power_query_results)
    _FakeThroughputTest.windows = []
    _FakeThroughputTest.order = order
    _FakeMaintenanceTest.windows = []
    _FakeMaintenanceTest.order = order
    monkeypatch.setattr("benchbox.core.tpcds.throughput_test.TPCDSThroughputTest", _FakeThroughputTest)
    monkeypatch.setattr("benchbox.core.tpcds.maintenance_test.TPCDSMaintenanceTest", _FakeMaintenanceTest)
    monkeypatch.setattr("benchbox.platforms.base.execution.require_throughput_stream_capability", lambda *a, **k: None)

    adapter = _RecordingAdapter(order, existing_databases=[False])
    benchmark = _LifecycleBenchmark(tmp_path)
    result = adapter.run_benchmark(
        benchmark,
        benchmark_name="tpcds",
        test_execution_type="combined",
        scale_factor=1.0,
        num_streams=STREAMS,
        output_dir=tmp_path,
    )
    return result, order, adapter


@pytest.fixture
def recorded_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Any, list[str], Any]:
    return _run(monkeypatch, tmp_path)


def test_outstanding_first_round_streams_stop_every_later_phase(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _result, order, _adapter = _run(monkeypatch, tmp_path, outstanding_first=[2])

    assert order == ["load", "power", "throughput 1"]


def test_power_time_is_the_sum_of_query_times_not_the_test_wall_time(recorded_run: tuple[Any, list[str], Any]) -> None:
    result, _order, _adapter = recorded_run

    assert result.phases.power_test.duration_ms == 100_000


def test_power_time_without_query_durations_withholds_qphds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    result, _order, _adapter = _run(monkeypatch, tmp_path, power_query_results=[{}] * 99)

    evaluation = evaluate_qphds(
        result.phases, scale_factor=1.0, compliance_class="official", failed_query_count=0, query_subset=None
    )

    assert evaluation.qphds_at_size is None
    assert any("Power Test elapsed time" in item for item in evaluation.unavailable)


def test_phases_run_in_spec_order(recorded_run: tuple[Any, list[str], Any]) -> None:
    _result, order, _adapter = recorded_run

    assert order == ORDER


def test_second_throughput_test_uses_streams_s_plus_one_to_two_s(recorded_run: tuple[Any, list[str], Any]) -> None:
    _result, _order, _adapter = recorded_run

    assert _FakeThroughputTest.windows == [(1, STREAMS), (STREAMS + 1, STREAMS)]


def test_each_maintenance_test_runs_half_the_streams_in_consecutive_refresh_sets(
    recorded_run: tuple[Any, list[str], Any],
) -> None:
    _result, _order, _adapter = recorded_run

    assert _FakeMaintenanceTest.windows == [(STREAMS // 2, 1), (STREAMS // 2, STREAMS // 2 + 1)]


def test_load_time_and_both_rounds_reach_the_result_and_feed_the_metric(
    recorded_run: tuple[Any, list[str], Any],
) -> None:
    result, _order, _adapter = recorded_run
    phases = result.phases

    assert phases.setup.data_loading.duration_ms == 1_000_000
    assert phases.power_test.duration_ms == 100_000
    assert phases.throughput_test.duration_ms == 200_000
    assert phases.throughput_test_2.duration_ms == 300_000
    assert phases.maintenance_test.duration_ms == 10_000
    assert phases.maintenance_test_2.duration_ms == 20_000
    assert [stream.stream_id for stream in phases.throughput_test_2.streams] == [5, 6, 7, 8]

    evaluation = evaluate_qphds(
        phases, scale_factor=1.0, compliance_class="official", failed_query_count=0, query_subset=None
    )

    assert evaluation.unavailable == []
    assert evaluation.qphds_at_size == 11453.0
    assert evaluation.terms["t_ld_hours"] == pytest.approx(0.01 * STREAMS * 1000.0 / 3600.0)


def test_second_round_state_does_not_leak_into_the_next_run(recorded_run: tuple[Any, list[str], Any]) -> None:
    _result, _order, adapter = recorded_run

    assert adapter._second_throughput_test_result is None
    assert adapter._second_maintenance_test_result is None


class TestCombinedPhasePlan:
    harness = resolve_combined_harness("tpcds")

    def test_all_phases_follow_the_spec_sequence(self) -> None:
        plan = _combined_phase_plan(self.harness, {"power", "throughput", "maintenance"}, spec_order=True)

        assert plan == [
            ("power", 1),
            ("throughput", 1),
            ("maintenance", 1),
            ("throughput", 2),
            ("maintenance", 2),
        ]

    @pytest.mark.parametrize(
        "requested",
        [{"power", "throughput"}, {"throughput", "maintenance"}, {"power", "maintenance"}],
    )
    def test_a_subset_of_phases_runs_each_once(self, requested: set[str]) -> None:
        plan = _combined_phase_plan(self.harness, requested, spec_order=True)

        assert all(phase_round is None for _phase, phase_round in plan)
        assert [phase for phase, _round in plan] == [
            phase for phase in ("power", "throughput", "maintenance") if phase in requested
        ]

    def test_spec_order_can_be_declined(self) -> None:
        plan = _combined_phase_plan(self.harness, {"power", "throughput", "maintenance"}, spec_order=False)

        assert plan == [("power", None), ("throughput", None), ("maintenance", None)]

    def test_tpch_keeps_one_pass(self) -> None:
        plan = _combined_phase_plan(
            resolve_combined_harness("tpch"), {"power", "throughput", "maintenance"}, spec_order=True
        )

        assert plan == [("power", None), ("throughput", None), ("maintenance", None)]


class TestRefreshWindow:
    def test_standalone_maintenance_keeps_the_configured_window(self) -> None:
        benchmark = SimpleNamespace(tpcds_refresh_first_set=3)

        assert _refresh_window({"tpcds_refresh_runs": 2}, benchmark) == (2, 3)

    @pytest.mark.parametrize(("phase_round", "expected"), [(1, (3, 7)), (2, (3, 10))])
    def test_spec_rounds_split_the_refresh_sets_in_half(self, phase_round: int, expected: tuple[int, int]) -> None:
        benchmark = SimpleNamespace(tpcds_refresh_first_set=7)

        assert _refresh_window({"num_streams": 6, "_phase_round": phase_round}, benchmark) == expected


def test_user_refresh_runs_override_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    benchmark = SimpleNamespace(tpcds_refresh_first_set=1)

    with caplog.at_level("WARNING"):
        runs, _first = _refresh_window({"num_streams": 4, "tpcds_refresh_runs": 1, "_phase_round": 1}, benchmark)

    assert runs == 2
    assert "tpcds_refresh_runs=1 ignored" in caplog.text
