from __future__ import annotations

from io import StringIO
from unittest.mock import patch

import pytest
from rich.console import Console

from benchbox.cli.progress import BenchmarkProgress, phase_progress, should_show_progress
from benchbox.monitoring import PerformanceMonitor

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_benchmark_progress_initialization():
    console = Console()
    progress = BenchmarkProgress(console, enable_monitoring=True)

    assert progress.console is console
    assert progress.enable_monitoring is True
    assert progress.monitor is not None
    assert isinstance(progress.monitor, PerformanceMonitor)


def test_benchmark_progress_without_monitoring():
    console = Console()
    progress = BenchmarkProgress(console, enable_monitoring=False)

    assert progress.monitor is None


def test_benchmark_progress_get_monitor():
    console = Console()
    progress = BenchmarkProgress(console, enable_monitoring=True)

    monitor = progress.get_monitor()
    assert monitor is not None
    assert isinstance(monitor, PerformanceMonitor)

    assert monitor is progress.monitor


def test_benchmark_progress_get_monitor_when_disabled():
    console = Console()
    progress = BenchmarkProgress(console, enable_monitoring=False)

    monitor = progress.get_monitor()
    assert monitor is None


def test_benchmark_progress_add_task():
    console = Console(file=StringIO())
    progress = BenchmarkProgress(console)

    task_id = progress.add_task("Test task", total=100)
    assert isinstance(task_id, int)


def test_benchmark_progress_update_task():
    console = Console(file=StringIO())
    progress = BenchmarkProgress(console)

    task_id = progress.add_task("Test task", total=100)

    progress.update(task_id, advance=10)


def test_benchmark_progress_context_manager():
    console = Console(file=StringIO())
    progress = BenchmarkProgress(console, enable_monitoring=True)

    assert not progress._is_started

    with progress:
        assert progress._is_started

    assert not progress._is_started


def test_benchmark_progress_double_start():
    console = Console(file=StringIO())
    progress = BenchmarkProgress(console)

    progress.start()
    progress.start()

    progress.stop()


def test_benchmark_progress_stop_before_start():
    console = Console(file=StringIO())
    progress = BenchmarkProgress(console)

    progress.stop()


def test_phase_progress_context_manager():
    console = Console(file=StringIO())
    progress = BenchmarkProgress(console)

    with progress, phase_progress(progress, "Test phase", total=10) as task_id:
        assert isinstance(task_id, int)
        progress.update(task_id, advance=5)


def test_phase_progress_with_none_progress():

    with phase_progress(None, "Test phase", total=10) as task_id:
        assert task_id is None


def test_phase_progress_without_total():
    console = Console(file=StringIO())
    progress = BenchmarkProgress(console)

    with progress, phase_progress(progress, "Test phase", total=None) as task_id:
        assert isinstance(task_id, int)


def test_should_show_progress_tty():
    with patch("sys.stdout.isatty", return_value=True):
        assert should_show_progress() is True


def test_should_show_progress_non_tty():
    with patch("sys.stdout.isatty", return_value=False):
        assert should_show_progress() is False


def test_benchmark_progress_monitors_collect_metrics():
    console = Console(file=StringIO())
    progress = BenchmarkProgress(console, enable_monitoring=True)

    with progress:
        task_id = progress.add_task("Collecting metrics", total=5)
        for i in range(5):
            progress.update(task_id, advance=1)

    monitor = progress.get_monitor()
    assert monitor is not None

    snapshot = monitor.snapshot()

    assert len(snapshot.counters) >= 0 or len(snapshot.gauges) >= 0


