from __future__ import annotations

from types import SimpleNamespace

import pytest

from benchbox.cli.commands import run as run_command

pytestmark = [pytest.mark.unit, pytest.mark.fast]


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
