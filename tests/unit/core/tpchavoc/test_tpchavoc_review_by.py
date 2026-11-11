# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from datetime import date

import pytest

from benchbox.core.tpchavoc.equivalence import CLICKHOUSE_KNOWN_DIVERGENCES, _report

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_review_by_past_due_warns_but_does_not_fail_the_report(capsys):
    exit_code = _report(
        [],
        total=10,
        known={"1_v1": "a documented, still-live engine-semantic difference"},
        engine_label="DuckDB",
        baseline_name="KNOWN_DIVERGENCES",
        review_by={"1_v1": date(2020, 1, 1)},
    )

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "WAIVER REVIEW DUE - 1_v1: review_by 2020-01-01 has passed" in out


def test_review_by_past_due_warns_on_a_still_reproducing_divergence(capsys):
    from benchbox.core.tpchavoc.equivalence import Divergence

    exit_code = _report(
        [Divergence(1, 1, "accepted, documented difference")],
        total=10,
        known={"1_v1": "a documented, still-live engine-semantic difference"},
        engine_label="DuckDB",
        baseline_name="KNOWN_DIVERGENCES",
        review_by={"1_v1": date(2020, 1, 1)},
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "WAIVER REVIEW DUE - 1_v1: review_by 2020-01-01 has passed - a documented, still-live" in out


def test_review_by_future_date_does_not_warn(capsys):
    from benchbox.core.tpchavoc.equivalence import Divergence

    exit_code = _report(
        [Divergence(1, 1, "accepted, documented difference")],
        total=10,
        known={"1_v1": "a documented, still-live engine-semantic difference"},
        engine_label="DuckDB",
        baseline_name="KNOWN_DIVERGENCES",
        review_by={"1_v1": date(2099, 1, 1)},
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "WAIVER REVIEW DUE" not in out


def test_review_by_absent_by_default_produces_no_warning(capsys):
    from benchbox.core.tpchavoc.equivalence import Divergence

    exit_code = _report(
        [Divergence(1, 1, "accepted, documented difference")],
        total=10,
        known={"1_v1": "a documented, still-live engine-semantic difference"},
        engine_label="DuckDB",
        baseline_name="KNOWN_DIVERGENCES",
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "WAIVER REVIEW DUE" not in out


def test_clickhouse_review_by_worked_example_is_not_yet_due():
    from benchbox.core.tpchavoc.equivalence import CLICKHOUSE_KNOWN_DIVERGENCES_REVIEW_BY

    for key, review_by in CLICKHOUSE_KNOWN_DIVERGENCES_REVIEW_BY.items():
        assert key in CLICKHOUSE_KNOWN_DIVERGENCES, f"{key} has a review_by but no matching baseline entry"
        assert review_by >= date.today(), f"{key}'s review_by {review_by} is already past-due"


