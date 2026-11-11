# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from benchbox.platforms.base.data_loading import run_staged_table_loads

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _adapter() -> MagicMock:
    adapter = MagicMock()
    adapter.logger = logging.getLogger("test-staged-load")
    return adapter


@contextmanager
def _frozen_clock(ticks: list[float]):

    clock = iter(ticks)
    with (
        patch("benchbox.platforms.base.data_loading.mono_time", side_effect=clock),
        patch("benchbox.utils.clock.mono_time", side_effect=clock),
    ):
        yield


class TestStagedLoadTemplate:
    def test_records_stats_with_caller_key_folding(self):

        adapter = _adapter()
        tables = {"LineItem": [Path("/tmp/a.tbl")], "Orders": [Path("/tmp/b.tbl")]}

        lower_stats, _, _ = run_staged_table_loads(
            adapter,
            tables=tables,
            stat_key=str.lower,
            filter_files=lambda paths: [Path(p) for p in paths],
            load_one=lambda table, files: 10,
            record_timings=False,
            fail_fast=False,
        )
        upper_stats, _, _ = run_staged_table_loads(
            adapter,
            tables=tables,
            stat_key=str.upper,
            filter_files=lambda paths: [Path(p) for p in paths],
            load_one=lambda table, files: 10,
            record_timings=False,
            fail_fast=False,
        )
        assert set(lower_stats) == {"lineitem", "orders"}
        assert set(upper_stats) == {"LINEITEM", "ORDERS"}

    def test_skipped_tables_record_zero_without_timings_by_default(self):

        adapter = _adapter()
        stats, total, timings = run_staged_table_loads(
            adapter,
            tables={"ghost": []},
            stat_key=str.lower,
            filter_files=lambda paths: [],
            load_one=lambda table, files: 99,
            record_timings=False,
            fail_fast=False,
        )
        assert stats == {"ghost": 0}
        assert timings is None
        assert total >= 0

    def test_skipped_tables_record_zero_timings_when_requested(self):

        adapter = _adapter()
        stats, _, timings = run_staged_table_loads(
            adapter,
            tables={"ghost": []},
            stat_key=str.upper,
            filter_files=lambda paths: [],
            load_one=lambda table, files: 99,
            record_timings=True,
            fail_fast=True,
        )
        assert stats == {"GHOST": 0}
        assert timings == {"GHOST": {"total_ms": 0}}

    def test_failure_logs_full_error_text(self):

        adapter = _adapter()
        adapter.logger = MagicMock()
        long_cause = "x" * 500
        stats, _, timings = run_staged_table_loads(
            adapter,
            tables={"orders": [Path("/tmp/o.tbl")]},
            stat_key=str.upper,
            filter_files=lambda paths: [Path(p) for p in paths],
            load_one=_fail_with(long_cause),
            record_timings=True,
            fail_fast=False,
        )
        assert stats == {"ORDERS": 0}
        assert timings == {"ORDERS": {"total_ms": 0}}
        (message,) = adapter.logger.error.call_args.args
        assert long_cause in message
        assert "..." not in message

    def test_continue_on_failure_by_default(self):

        adapter = _adapter()
        calls: list[str] = []

        def load_one(table: str, files: list[Path]) -> int:
            calls.append(table)
            if table == "bad":
                raise RuntimeError("boom")
            return 7

        stats, _, _ = run_staged_table_loads(
            adapter,
            tables={"bad": [Path("/tmp/b.tbl")], "good": [Path("/tmp/g.tbl")]},
            stat_key=str.lower,
            filter_files=lambda paths: [Path(p) for p in paths],
            load_one=load_one,
            record_timings=False,
            fail_fast=False,
        )
        assert calls == ["bad", "good"]
        assert stats == {"bad": 0, "good": 7}

    def test_fail_fast_reraises_after_recording_zeros(self):

        adapter = _adapter()
        with pytest.raises(RuntimeError, match="boom"):
            run_staged_table_loads(
                adapter,
                tables={"bad": [Path("/tmp/b.tbl")]},
                stat_key=str.upper,
                filter_files=lambda paths: [Path(p) for p in paths],
                load_one=_fail_with("boom"),
                record_timings=True,
                fail_fast=True,
            )

    def test_on_table_loaded_hook_runs_after_success(self):

        adapter = _adapter()
        seen: list[tuple[str, str, int]] = []
        stats, _, timings = run_staged_table_loads(
            adapter,
            tables={"orders": [Path("/tmp/o.tbl")]},
            stat_key=str.upper,
            filter_files=lambda paths: [Path(p) for p in paths],
            load_one=lambda table, files: 42,
            on_table_loaded=lambda table, key, count: seen.append((table, key, count)),
            record_timings=True,
            fail_fast=True,
        )
        assert seen == [("orders", "ORDERS", 42)]
        assert stats == {"ORDERS": 42}
        assert set(timings or {}) == {"ORDERS"}

    def test_custom_log_sinks_preserve_verbose_gating(self):

        adapter = _adapter()
        adapter.logger = MagicMock()
        success_lines: list[str] = []
        summary_lines: list[str] = []
        run_staged_table_loads(
            adapter,
            tables={"orders": [Path("/tmp/o.tbl")]},
            stat_key=str.upper,
            filter_files=lambda paths: [Path(p) for p in paths],
            load_one=lambda table, files: 5,
            record_timings=True,
            fail_fast=True,
            success_log=success_lines.append,
            summary_log=summary_lines.append,
        )
        adapter.logger.info.assert_not_called()
        assert len(success_lines) == 1
        assert len(summary_lines) == 1

    def test_custom_start_description(self):

        adapter = _adapter()
        adapter.log_verbose = MagicMock()
        run_staged_table_loads(
            adapter,
            tables={"orders": [Path("/tmp/o.tbl")]},
            stat_key=str.lower,
            filter_files=lambda paths: [Path(p) for p in paths],
            load_one=lambda table, files: 5,
            record_timings=False,
            fail_fast=False,
            describe_start=lambda table, chunk: f"Direct loading data for table: {table}",
        )
        (line,) = adapter.log_verbose.call_args.args
        assert line == "Direct loading data for table: orders"

    def test_post_load_hook_counts_inside_per_table_timing(self):

        adapter = _adapter()
        success_lines: list[str] = []

        def on_table_loaded(table: str, key: str, count: int) -> None:
            del table, key, count

        with _frozen_clock([1000.0, 1001.0, 1002.0, 1003.0]):
            _, _, timings = run_staged_table_loads(
                adapter,
                tables={"orders": [Path("/tmp/o.tbl")]},
                stat_key=str.upper,
                filter_files=lambda paths: [Path(p) for p in paths],
                load_one=lambda table, files: 5,
                on_table_loaded=on_table_loaded,
                record_timings=True,
                fail_fast=True,
                success_log=success_lines.append,
            )

        assert timings == {"ORDERS": {"total_ms": 1000.0}}
        assert "in 1.00s" in success_lines[0]

    def test_caller_phase_start_covers_setup_time(self):

        adapter = _adapter()
        summary_lines: list[str] = []

        with _frozen_clock([1001.0, 1001.5, 1010.0]):
            _, total, _ = run_staged_table_loads(
                adapter,
                tables={"orders": [Path("/tmp/o.tbl")]},
                stat_key=str.lower,
                filter_files=lambda paths: [Path(p) for p in paths],
                load_one=lambda table, files: 5,
                record_timings=False,
                fail_fast=False,
                summary_log=summary_lines.append,
                phase_start=990.0,
            )

        assert total == 20.0
        assert "in 20.00s" in summary_lines[0]

    def test_phase_total_defaults_to_loop_entry_without_caller_clock(self):

        adapter = _adapter()
        summary_lines: list[str] = []

        with _frozen_clock([1000.0, 1001.0, 1001.5, 1002.0]):
            _, total, _ = run_staged_table_loads(
                adapter,
                tables={"orders": [Path("/tmp/o.tbl")]},
                stat_key=str.lower,
                filter_files=lambda paths: [Path(p) for p in paths],
                load_one=lambda table, files: 5,
                record_timings=False,
                fail_fast=False,
                summary_log=summary_lines.append,
            )

        assert total == 2.0
        assert "in 2.00s" in summary_lines[0]


def _fail_with(message: str) -> Any:
    def load_one(table: str, files: list[Path]) -> int:
        raise RuntimeError(message)

    return load_one


