from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from benchbox.platforms.clickhouse.merge_settle import MergeSettleResult, wait_for_merges_to_settle
from benchbox.platforms.clickhouse.workload import ClickHouseWorkloadMixin

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class _ScriptedConnection:
    def __init__(self, snapshots):
        self._snapshots = list(snapshots)
        self.queries = []

    def execute(self, sql):
        self.queries.append(sql)
        return [self._snapshots.pop(0) if len(self._snapshots) > 1 else self._snapshots[0]]


def _wait(connection, **kwargs):
    sleeps = []
    result = wait_for_merges_to_settle(connection, sleep=sleeps.append, **kwargs)
    return result, sleeps


def test_returns_after_stable_quiet_polls() -> None:
    connection = _ScriptedConnection([(0, 6)])

    result, sleeps = _wait(connection, stable_polls=3)

    assert result.settled is True
    assert result.active_parts == 6
    assert len(connection.queries) == 3
    assert len(sleeps) == 2


def test_waits_while_merges_are_running() -> None:
    connection = _ScriptedConnection([(4, 90), (2, 60), (0, 40), (0, 40), (0, 40), (0, 40)])

    result, _ = _wait(connection, stable_polls=3)

    assert result.settled is True
    assert result.active_parts == 40
    assert len(connection.queries) == 6


def test_part_count_change_resets_the_quiet_window() -> None:
    connection = _ScriptedConnection([(0, 90), (0, 80), (0, 80), (0, 80)])

    result, _ = _wait(connection, stable_polls=3)

    assert result.settled is True
    assert result.active_parts == 80
    assert len(connection.queries) == 5


def test_gives_up_at_the_timeout() -> None:
    connection = _ScriptedConnection([(3, 100)])

    with patch("benchbox.platforms.clickhouse.merge_settle.elapsed_seconds", side_effect=[0.0, 1.0, 700.0, 700.0]):
        result = wait_for_merges_to_settle(connection, timeout_seconds=600.0, sleep=lambda _: None)

    assert result.settled is False
    assert result.active_parts == 100


class _Adapter(ClickHouseWorkloadMixin):
    def __init__(self, deployment_mode: str, tuning_enabled: bool = True) -> None:
        self.deployment_mode = deployment_mode
        self.tuning_enabled = tuning_enabled
        self.unified_tuning_configuration = None


def _load(adapter: _Adapter) -> tuple[dict[str, int], float, object]:
    loader = Mock()
    loader.load.return_value = ({"lineitem": 1}, 1.5)
    with patch("benchbox.platforms.base.data_loading.DataLoader", return_value=loader):
        return adapter.load_data(Mock(), Mock(), Path("/tmp"))


def test_server_load_waits_for_merges() -> None:
    adapter = _Adapter("server")

    with patch(
        "benchbox.platforms.clickhouse.workload.wait_for_merges_to_settle",
        return_value=MergeSettleResult(True, 0.0, 6),
    ) as wait:
        _, loading_time, _ = _load(adapter)

    wait.assert_called_once()
    assert loading_time == 1.5


def test_untuned_server_load_waits_for_merges() -> None:
    adapter = _Adapter("server", tuning_enabled=False)

    with patch(
        "benchbox.platforms.clickhouse.workload.wait_for_merges_to_settle",
        return_value=MergeSettleResult(True, 0.0, 6),
    ) as wait:
        _, loading_time, _ = _load(adapter)

    wait.assert_called_once()
    assert loading_time == 1.5


def test_local_load_does_not_wait_for_merges() -> None:
    adapter = _Adapter("local")

    with patch("benchbox.platforms.clickhouse.workload.wait_for_merges_to_settle") as wait:
        _load(adapter)

    wait.assert_not_called()


def test_unsettled_merges_warn(caplog: pytest.LogCaptureFixture) -> None:
    adapter = _Adapter("server")

    with (
        patch(
            "benchbox.platforms.clickhouse.workload.wait_for_merges_to_settle",
            return_value=MergeSettleResult(False, 600.0, 120),
        ),
        caplog.at_level("WARNING"),
    ):
        _load(adapter)

    assert "had not settled" in caplog.text


def test_merge_check_failure_does_not_fail_the_load(caplog: pytest.LogCaptureFixture) -> None:
    adapter = _Adapter("server")

    with (
        patch("benchbox.platforms.clickhouse.workload.wait_for_merges_to_settle", side_effect=RuntimeError("denied")),
        caplog.at_level("WARNING"),
    ):
        _load(adapter)

    assert "Could not check ClickHouse background merges" in caplog.text


def test_server_load_records_merge_settle_phase() -> None:
    from benchbox.platforms.clickhouse.merge_settle import MERGE_SETTLE_TIMEOUT_SECONDS

    adapter = _Adapter("server", tuning_enabled=False)

    with patch(
        "benchbox.platforms.clickhouse.workload.wait_for_merges_to_settle",
        return_value=MergeSettleResult(True, 2.5, 84),
    ):
        _, loading_time, _ = _load(adapter)

    assert loading_time == 1.5
    phase = adapter.build_merge_settle_phase()
    assert phase is not None
    assert (phase.settled, phase.waited_seconds, phase.active_parts) == (True, 2.5, 84)
    assert phase.timeout_seconds == MERGE_SETTLE_TIMEOUT_SECONDS


def test_merge_settle_phase_is_exported_next_to_data_loading() -> None:
    from datetime import datetime

    from benchbox.core.results.models import (
        BenchmarkResults,
        DataLoadingPhase,
        ExecutionPhases,
        MergeSettlePhase,
        SetupPhase,
    )
    from benchbox.core.results.schema import SchemaV2Validator, build_result_payload

    result = BenchmarkResults(
        benchmark_name="TPC-H",
        platform="clickhouse",
        scale_factor=0.01,
        execution_id="merge-settle",
        timestamp=datetime(2026, 10, 9, 12, 0, 0),
        duration_seconds=12.5,
        total_queries=1,
        successful_queries=1,
        failed_queries=0,
        query_results=[
            {
                "query_id": "Q1",
                "status": "SUCCESS",
                "execution_time_seconds": 1.25,
                "rows_returned": 1,
                "iteration": 1,
                "stream_id": 0,
                "run_type": "measurement",
            }
        ],
        execution_phases=ExecutionPhases(
            setup=SetupPhase(
                data_loading=DataLoadingPhase(
                    duration_ms=3500, status="SUCCESS", total_rows_loaded=10, tables_loaded=8, per_table_stats={}
                ),
                merge_settle=MergeSettlePhase(settled=True, waited_seconds=2.5, active_parts=84, timeout_seconds=600.0),
            )
        ),
    )

    payload = build_result_payload(result)

    SchemaV2Validator().validate(payload)
    assert payload["phases"]["merge_settle"] == {
        "settled": True,
        "waited_seconds": 2.5,
        "active_parts": 84,
        "timeout_seconds": 600.0,
    }
    assert payload["phases"]["data_loading"]["duration_ms"] == 3500


def test_merge_settle_phase_is_omitted_when_no_settle_ran() -> None:
    assert _Adapter("local").build_merge_settle_phase() is None


def test_failed_settle_check_is_recorded_not_silent(caplog: pytest.LogCaptureFixture) -> None:
    from benchbox.platforms.clickhouse.merge_settle import MERGE_SETTLE_TIMEOUT_SECONDS

    adapter = _Adapter("server")

    with (
        patch("benchbox.platforms.clickhouse.workload.wait_for_merges_to_settle", side_effect=RuntimeError("denied")),
        caplog.at_level("WARNING"),
    ):
        _load(adapter)

    assert "Could not check ClickHouse background merges" in caplog.text
    phase = adapter.build_merge_settle_phase()
    assert phase is not None
    assert phase.settled is False
    assert phase.active_parts is None
    assert phase.timeout_seconds == MERGE_SETTLE_TIMEOUT_SECONDS


def test_second_run_does_not_republish_stale_settle() -> None:
    adapter = _Adapter("server")

    with patch(
        "benchbox.platforms.clickhouse.workload.wait_for_merges_to_settle",
        return_value=MergeSettleResult(True, 2.5, 84),
    ):
        _load(adapter)
    assert adapter.build_merge_settle_phase() is not None

    with patch(
        "benchbox.platforms.clickhouse.workload.wait_for_merges_to_settle",
        side_effect=RuntimeError("denied"),
    ):
        _load(adapter)

    phase = adapter.build_merge_settle_phase()
    assert phase is not None
    assert (phase.settled, phase.active_parts) == (False, None)


def test_failed_settle_phase_exports_null_parts() -> None:
    from datetime import datetime

    from benchbox.core.results.models import (
        BenchmarkResults,
        DataLoadingPhase,
        ExecutionPhases,
        MergeSettlePhase,
        SetupPhase,
    )
    from benchbox.core.results.schema import SchemaV2Validator, build_result_payload

    result = BenchmarkResults(
        benchmark_name="TPC-H",
        platform="clickhouse",
        scale_factor=0.01,
        execution_id="merge-settle-failed",
        timestamp=datetime(2026, 10, 9, 12, 0, 0),
        duration_seconds=12.5,
        total_queries=1,
        successful_queries=1,
        failed_queries=0,
        query_results=[
            {
                "query_id": "Q1",
                "status": "SUCCESS",
                "execution_time_seconds": 1.25,
                "rows_returned": 1,
                "iteration": 1,
                "stream_id": 0,
                "run_type": "measurement",
            }
        ],
        execution_phases=ExecutionPhases(
            setup=SetupPhase(
                data_loading=DataLoadingPhase(
                    duration_ms=3500, status="SUCCESS", total_rows_loaded=10, tables_loaded=8, per_table_stats={}
                ),
                merge_settle=MergeSettlePhase(
                    settled=False, waited_seconds=0.4, active_parts=None, timeout_seconds=600.0
                ),
            )
        ),
    )

    payload = build_result_payload(result)

    SchemaV2Validator().validate(payload)
    assert payload["phases"]["merge_settle"] == {
        "settled": False,
        "waited_seconds": 0.4,
        "active_parts": None,
        "timeout_seconds": 600.0,
    }
