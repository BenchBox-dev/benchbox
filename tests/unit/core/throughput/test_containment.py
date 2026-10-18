# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from benchbox.core.throughput.containment import (
    await_quiescence,
    check_phase_boundary,
)
from benchbox.core.throughput.result import ThroughputResult, ThroughputStreamResult
from benchbox.core.throughput.runner import StreamRunner
from benchbox.utils.clock import elapsed_seconds, mono_time

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]

TINY_TIMEOUT = 0.2


class _Config:
    def __init__(self, num_streams=2, max_workers=None, stream_timeout=0, cancel_on_timeout=False):
        self.num_streams = num_streams
        self.max_workers = max_workers
        self.base_seed = 42
        self.stream_timeout = stream_timeout
        self.scale_factor = 1.0
        self.verbose = False
        self.cancel_on_timeout = cancel_on_timeout


def _make_result() -> ThroughputResult:
    return ThroughputResult(
        start_time="2026-01-01T00:00:00",
        end_time="",
        total_time=0.0,
        throughput_at_size=0.0,
        streams_executed=0,
        streams_successful=0,
    )


def _make_stream_result(stream_id: int) -> ThroughputStreamResult:
    return ThroughputStreamResult(
        stream_id=stream_id,
        start_time=0.0,
        end_time=1.0,
        duration=1.0,
        queries_executed=22,
        queries_successful=22,
        queries_failed=0,
        success=True,
    )


def _blocking_stream_fn(release: threading.Event):

    def _fn(stream_id: int, seed: int, config: _Config) -> ThroughputStreamResult:
        if stream_id == 1:
            assert release.wait(timeout=10)
        return _make_stream_result(stream_id)

    return _fn


class TestOutstandingOwnershipState:
    def test_timeout_returns_bounded_with_running_stream_outstanding(self) -> None:
        release = threading.Event()
        config = _Config(num_streams=2, max_workers=2, stream_timeout=TINY_TIMEOUT)
        result = _make_result()
        logger = logging.getLogger("test-throughput-containment")

        start = mono_time()
        try:
            StreamRunner.execute(_blocking_stream_fn(release), config, result, logger)
        finally:
            release.set()

        assert elapsed_seconds(start) < 5.0
        assert result.streams_executed == 2
        assert result.streams_successful == 1
        assert result.outstanding_stream_ids == [1]
        assert result.has_outstanding_work is True
        assert result.cleanup_state == "outstanding"
        assert result.cancelled_stream_ids == []
        assert result.outstanding_notes != []
        assert len(result.errors) == 1
        assert "timed out" in result.errors[0].lower()
        assert "leaked" in result.errors[0].lower()

    def test_queued_stream_is_cancelled_never_outstanding(self) -> None:
        release = threading.Event()
        started = threading.Event()
        config = _Config(num_streams=2, max_workers=1, stream_timeout=TINY_TIMEOUT)
        result = _make_result()
        logger = logging.getLogger("test-throughput-containment")

        def _fn(stream_id: int, seed: int, config: _Config) -> ThroughputStreamResult:
            if stream_id == 1:
                assert release.wait(timeout=10)
            else:
                started.set()
            return _make_stream_result(stream_id)

        try:
            StreamRunner.execute(_fn, config, result, logger)
        finally:
            release.set()

        assert result.streams_executed == 1
        assert len(result.errors) == 2
        assert result.outstanding_stream_ids == [1]
        assert result.cancelled_stream_ids == [2]
        assert result.cleanup_state == "outstanding"
        assert not started.wait(timeout=1.0)

    def test_healthy_path_carries_no_outstanding_state(self) -> None:
        config = _Config(num_streams=2, max_workers=2, stream_timeout=5)
        result = _make_result()
        logger = logging.getLogger("test-throughput-containment")

        StreamRunner.execute(lambda sid, seed, cfg: _make_stream_result(sid), config, result, logger)

        assert result.streams_executed == 2
        assert result.streams_successful == 2
        assert result.outstanding_stream_ids == []
        assert result.cancelled_stream_ids == []
        assert result.has_outstanding_work is False
        assert result.cleanup_state == "complete"


class TestAwaitQuiescence:
    def test_boundary_stays_contained_until_termination_then_releases(self) -> None:
        release = threading.Event()
        config = _Config(num_streams=2, max_workers=2, stream_timeout=TINY_TIMEOUT)
        result = _make_result()
        logger = logging.getLogger("test-throughput-containment")

        try:
            StreamRunner.execute(_blocking_stream_fn(release), config, result, logger)

            assert await_quiescence(result, timeout=0.1) is False
            assert result.has_outstanding_work is True
            assert check_phase_boundary(result).proceed is False

            release.set()
            assert await_quiescence(result, timeout=10.0) is True
            assert result.outstanding_stream_ids == []
            assert result.has_outstanding_work is False
            assert result.cleanup_state == "quiesced"
            assert check_phase_boundary(result).proceed is True
        finally:
            release.set()

    def test_unobservable_work_stays_contained(self) -> None:
        result = _make_result()
        result.outstanding_stream_ids = [3]
        result.cleanup_state = "outstanding"

        assert await_quiescence(result, timeout=0.1) is False
        assert check_phase_boundary(result).proceed is False


class TestPhaseBoundaryGate:
    def test_no_throughput_result_proceeds(self) -> None:
        assert check_phase_boundary(None).proceed is True

    def test_legacy_result_without_outstanding_evidence_proceeds(self) -> None:
        assert check_phase_boundary({"success": False}).proceed is True

    def test_healthy_result_proceeds(self) -> None:
        assert check_phase_boundary(_make_result()).proceed is True

    def test_outstanding_result_names_streams(self) -> None:
        result = _make_result()
        result.outstanding_stream_ids = [1, 3]

        decision = check_phase_boundary(result)

        assert decision.proceed is False
        assert decision.outstanding_stream_ids == [1, 3]
        assert "1" in decision.reason and "3" in decision.reason


class TestOfficialBenchmarkContainment:
    def _official(self, tmp_path):
        from benchbox.core.tpcds.official_benchmark import TPCDSOfficialBenchmark

        return TPCDSOfficialBenchmark(scale_factor=0.01, output_dir=tmp_path, verbose=False)

    def _outstanding_throughput_result(self) -> ThroughputResult:
        result = _make_result()
        result.streams_executed = 2
        result.streams_successful = 1
        result.success = False
        result.errors = ["Stream 1 timed out after 0.2s and has not completed."]
        result.outstanding_stream_ids = [1]
        result.cleanup_state = "outstanding"
        return result

    def test_maintenance_refused_while_throughput_outstanding(self, tmp_path) -> None:
        benchmark = self._official(tmp_path)
        with (
            patch("benchbox.core.tpcds.power_test.TPCDSPowerTest") as mock_power,
            patch("benchbox.core.tpcds.maintenance_test.TPCDSMaintenanceTest") as mock_maintenance,
        ):
            mock_power.return_value.run.return_value = {"power_at_size": 100.0}
            adapter = Mock()
            adapter._run_routed_throughput.return_value = self._outstanding_throughput_result()

            with pytest.warns(DeprecationWarning):
                result = benchmark.run_official_benchmark(lambda: Mock(), adapter=adapter)

        mock_maintenance.assert_not_called()
        assert result.success is False
        assert result.throughput_at_size == 0.0
        assert result.maintenance_test_result["status"] == "contained"
        assert result.maintenance_test_result["outstanding_stream_ids"] == [1]
        assert any("refused" in error.lower() for error in result.errors)

    def test_failed_throughput_withholds_metric_without_blocking_maintenance(self, tmp_path) -> None:
        benchmark = self._official(tmp_path)
        failed = self._outstanding_throughput_result()
        failed.outstanding_stream_ids = []
        failed.cleanup_state = "complete"
        with (
            patch("benchbox.core.tpcds.power_test.TPCDSPowerTest") as mock_power,
            patch("benchbox.core.tpcds.maintenance_test.TPCDSMaintenanceTest") as mock_maintenance,
        ):
            mock_power.return_value.run.return_value = {"power_at_size": 100.0}
            adapter = Mock()
            adapter._run_routed_throughput.return_value = failed
            mock_maintenance.return_value.run.return_value = {"success": True}

            with pytest.warns(DeprecationWarning):
                result = benchmark.run_official_benchmark(lambda: Mock(), adapter=adapter)

        mock_maintenance.assert_called_once()
        assert result.success is False
        assert result.throughput_at_size == 0.0

    def test_healthy_throughput_still_publishes_metric(self, tmp_path) -> None:
        benchmark = self._official(tmp_path)
        healthy = _make_result()
        healthy.success = True
        healthy.throughput_at_size = 200.0
        with (
            patch("benchbox.core.tpcds.power_test.TPCDSPowerTest") as mock_power,
            patch("benchbox.core.tpcds.maintenance_test.TPCDSMaintenanceTest") as mock_maintenance,
        ):
            mock_power.return_value.run.return_value = {"power_at_size": 100.0}
            adapter = Mock()
            adapter._run_routed_throughput.return_value = healthy
            mock_maintenance.return_value.run.return_value = {"success": True}

            with pytest.warns(DeprecationWarning):
                result = benchmark.run_official_benchmark(lambda: Mock(), adapter=adapter)

        assert result.success is True
        assert result.throughput_at_size == 200.0


class TestCombinedSequencerContainment:
    def _sequence(self, monkeypatch, throughput_result, calls):
        from benchbox.platforms.base.execution import TestDriversMixin

        harness = SimpleNamespace(
            label="TPC-DS",
            power_method="fake_power",
            throughput_method="fake_throughput",
            maintenance_method="fake_maintenance",
        )
        monkeypatch.setattr(
            "benchbox.platforms.base.execution.resolve_combined_harness",
            lambda benchmark_id: harness,
        )
        monkeypatch.setattr(
            "benchbox.platforms.base.execution.quiet_console",
            Mock(),
        )

        def fake_power(benchmark, connection, run_config):
            calls.append("power")
            return []

        def fake_throughput(benchmark, connection, run_config):
            calls.append("throughput")
            fake_self._last_throughput_test_result = throughput_result
            return []

        def fake_maintenance(benchmark, connection, run_config):
            calls.append("maintenance")
            return []

        fake_self = SimpleNamespace(
            _resolve_benchmark_slug=lambda benchmark, run_config: "tpcds",
            _last_throughput_test_result=None,
            fake_power=fake_power,
            fake_throughput=fake_throughput,
            fake_maintenance=fake_maintenance,
        )
        run_config = {"options": {"requested_phases": {"power", "throughput", "maintenance"}}}
        return TestDriversMixin._execute_combined_test(fake_self, Mock(), Mock(), run_config)

    def test_maintenance_skipped_while_throughput_outstanding(self, monkeypatch) -> None:
        outstanding = _make_result()
        outstanding.success = False
        outstanding.outstanding_stream_ids = [0]
        calls: list[str] = []

        all_results = self._sequence(monkeypatch, outstanding, calls)

        assert calls == ["power", "throughput"]
        assert len(all_results) == 1
        record = all_results[0]
        assert record["test_type"] == "maintenance"
        assert record["status"] == "FAILED"
        assert record["contained"] is True
        assert record["outstanding_stream_ids"] == [0]
        assert "refused" in record["error"].lower()

    def test_all_phases_run_when_boundary_clear(self, monkeypatch) -> None:
        calls: list[str] = []

        all_results = self._sequence(monkeypatch, _make_result(), calls)

        assert calls == ["power", "throughput", "maintenance"]
        assert all_results == []

    def test_maintenance_only_run_proceeds_without_throughput(self, monkeypatch) -> None:
        from benchbox.platforms.base.execution import TestDriversMixin

        harness = SimpleNamespace(
            label="TPC-DS",
            power_method="fake_power",
            throughput_method="fake_throughput",
            maintenance_method="fake_maintenance",
        )
        monkeypatch.setattr(
            "benchbox.platforms.base.execution.resolve_combined_harness",
            lambda benchmark_id: harness,
        )
        monkeypatch.setattr(
            "benchbox.platforms.base.execution.quiet_console",
            Mock(),
        )
        calls: list[str] = []

        def fake_maintenance(benchmark, connection, run_config):
            calls.append("maintenance")
            return [{"test_type": "maintenance"}]

        fake_self = SimpleNamespace(
            _resolve_benchmark_slug=lambda benchmark, run_config: "tpcds",
            _last_throughput_test_result=None,
            fake_maintenance=fake_maintenance,
        )
        run_config = {"options": {"requested_phases": {"maintenance"}}}

        all_results = TestDriversMixin._execute_combined_test(fake_self, Mock(), Mock(), run_config)

        assert calls == ["maintenance"]
        assert all_results == [{"test_type": "maintenance"}]
