from __future__ import annotations

from types import SimpleNamespace

import pytest

from benchbox.cli.commands import run as run_command
from benchbox.cli.output import ConsoleResultFormatter

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _results(compliance_class: str | None, power_at_size: float | None) -> SimpleNamespace:
    return SimpleNamespace(
        benchmark_name="TPC-H",
        platform="DuckDB",
        scale_factor=0.01,
        execution_id="run",
        timestamp=None,
        duration_seconds=1.0,
        total_queries=1,
        successful_queries=1,
        total_execution_time=1.0,
        average_query_time=1.0,
        validation_status=None,
        compliance_class=compliance_class,
        power_at_size=power_at_size,
    )


@pytest.mark.parametrize(
    ("compliance_class", "power_at_size", "expected"),
    [
        ("unofficial_subscale", 1234.5, "1234.50 (subscale, unofficial)"),
        (None, 1234.5, "1234.50"),
        ("unofficial_nonstandard", 1234.5, None),
        ("unofficial_subscale", None, None),
    ],
)
def test_power_at_size_is_labelled_for_subscale_runs(compliance_class, power_at_size, expected) -> None:
    stats = ConsoleResultFormatter.format_execution_statistics(_results(compliance_class, power_at_size))

    assert stats.get("power_at_size") == expected


@pytest.mark.parametrize(
    ("benchmark_id", "scale", "expected"),
    [
        ("tpch", 0.01, "TPC-H UNOFFICIAL SUBSCALE RUN"),
        ("tpcds", 0.1, "TPC-DS UNOFFICIAL SUBSCALE RUN"),
        ("tpch", 1.0, None),
        ("ssb", 0.01, None),
    ],
)
def test_subscale_warning_describes_unofficial_power(monkeypatch, benchmark_id, scale, expected) -> None:
    printed: list[str] = []
    monkeypatch.setattr(run_command.console, "print", lambda message: printed.append(message))

    run_command._warn_unofficial_subscale(SimpleNamespace(benchmark=benchmark_id, scale=scale, quiet=False))

    if expected is None:
        assert printed == []
    else:
        assert expected in printed[0]
        assert "Power@Size is recorded for" in printed[0]
        assert "marked unofficial" in printed[0]
