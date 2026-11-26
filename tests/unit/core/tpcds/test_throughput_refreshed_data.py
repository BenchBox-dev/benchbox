from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from benchbox.core.tpcds.throughput_test import TPCDSThroughputTest
from benchbox.core.validation.query_validation import get_data_refreshed_context
from benchbox.core.validation.stream_checks import QueryGeneration
from benchbox.platforms.base.execution import TestDriversMixin

pytestmark = [pytest.mark.unit, pytest.mark.fast, pytest.mark.tpcds]


class _ContextRecordingConnection:
    def __init__(self) -> None:
        self.seen: list[bool] = []

    def execute(self, _query: str) -> SimpleNamespace:
        self.seen.append(get_data_refreshed_context())
        return SimpleNamespace(platform_result={"status": "SUCCESS"}, fetchall=lambda: [(1,)])


@pytest.mark.parametrize("data_refreshed", [False, True])
def test_stream_query_runs_under_the_data_state_it_was_given(data_refreshed: bool) -> None:
    connection = _ContextRecordingConnection()
    throughput = TPCDSThroughputTest.__new__(TPCDSThroughputTest)

    throughput._run_single_stream_query(
        connection, "SELECT 1", "1", 5, {}, QueryGeneration(seed=42, streams_index=5), data_refreshed
    )

    assert connection.seen == [data_refreshed]
    assert get_data_refreshed_context() is False


def _run_combined(monkeypatch, maintenance_rows: list[dict]) -> list[tuple[str, bool]]:
    harness = SimpleNamespace(
        label="TPC-DS",
        spec_sequence=("power", "throughput", "maintenance", "throughput", "maintenance"),
        power_method="fake_power",
        throughput_method="fake_throughput",
        maintenance_method="fake_maintenance",
    )
    monkeypatch.setattr("benchbox.platforms.base.execution.resolve_combined_harness", lambda benchmark_id: harness)
    monkeypatch.setattr("benchbox.platforms.base.execution.quiet_console", Mock())
    seen: list[tuple[str, bool]] = []

    def recorder(name: str, rows: list[dict]):
        def run(benchmark, connection, run_config):
            seen.append((name, bool(run_config.get("_data_refreshed"))))
            return rows

        return run

    fake_self = SimpleNamespace(
        _resolve_benchmark_slug=lambda benchmark, run_config: "tpcds",
        _last_throughput_test_result=None,
        fake_power=recorder("power", []),
        fake_throughput=recorder("throughput", []),
        fake_maintenance=recorder("maintenance", maintenance_rows),
    )
    run_config = {"options": {"requested_phases": {"power", "throughput", "maintenance"}}}
    TestDriversMixin._execute_combined_test(fake_self, Mock(), Mock(), run_config)
    return seen


def test_throughput_after_a_maintenance_test_is_marked_as_running_on_refreshed_data(monkeypatch) -> None:
    seen = _run_combined(monkeypatch, [{"test_type": "maintenance", "query_id": "DF_SS"}])

    assert seen == [
        ("power", False),
        ("throughput", False),
        ("maintenance", False),
        ("throughput", True),
        ("maintenance", True),
    ]


def test_throughput_after_an_unsupported_maintenance_test_keeps_the_base_data_state(monkeypatch) -> None:
    seen = _run_combined(monkeypatch, [{"test_type": "maintenance", "query_id": "maintenance_not_supported"}])

    assert [refreshed for name, refreshed in seen if name == "throughput"] == [False, False]
