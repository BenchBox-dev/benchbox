from __future__ import annotations

import datetime
import json
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.sql_execution_gate import gate
from scripts.sql_execution_gate.engines import Dataset, DuckdbEngine, SqliteEngine
from scripts.sql_execution_gate.rows import first_difference

pytestmark = [pytest.mark.unit, pytest.mark.medium]

DDL = "CREATE TABLE lineitem (l_orderkey INTEGER, l_shipdate DATE); CREATE TABLE inv (inv_before INTEGER, inv_after INTEGER);"

DUCKDB_DATE_WINDOW = (
    "SELECT l_orderkey FROM lineitem WHERE l_shipdate BETWEEN CAST('1998-09-11' AS DATE) - INTERVAL '30' DAY "
    "AND CAST('1998-09-11' AS DATE) + INTERVAL '30' DAY"
)
SQLITE_DATE_WINDOW_DATETIME = (
    "SELECT l_orderkey FROM lineitem WHERE l_shipdate BETWEEN DATETIME(DATE('1998-09-11'), '-30 DAY') "
    "AND DATETIME(DATE('1998-09-11'), '30 DAY')"
)
SQLITE_DATE_WINDOW_DATE = (
    "SELECT l_orderkey FROM lineitem WHERE l_shipdate BETWEEN DATE(DATE('1998-09-11'), '-30 DAY') "
    "AND DATE(DATE('1998-09-11'), '30 DAY')"
)
DUCKDB_RATIO = "SELECT inv_before FROM inv WHERE inv_after / inv_before > 1.2"
SQLITE_RATIO_TRUNCATING = "SELECT inv_before FROM inv WHERE inv_after / inv_before > 1.2"
SQLITE_RATIO_EXACT = "SELECT inv_before FROM inv WHERE CAST(inv_after AS REAL) / inv_before > 1.2"


@pytest.fixture
def tiny(tmp_path: Path) -> dict[str, Dataset]:
    (tmp_path / "lineitem.tbl").write_text("1|1998-08-12|\n2|1998-09-11|\n3|1998-10-11|\n4|1998-12-24|\n")
    (tmp_path / "inv.tbl").write_text("3|4|\n2|3|\n5|1|\n")
    return {
        "tiny": Dataset("tiny", DDL, {"lineitem": tmp_path / "lineitem.tbl", "inv": tmp_path / "inv.tbl"}),
    }


def _side(ref: str, queries: dict[str, tuple[str, str]]) -> dict:
    return {
        "ref": ref,
        "runtime_pin": {},
        "queries": {"tiny": {name: {"sqlite": sqlite, "duckdb": duck} for name, (sqlite, duck) in queries.items()}},
    }


def _evaluate(tiny, tmp_path: Path, candidate: dict, baseline: dict | None = None, expected=()) -> gate.Report:
    sides = {"candidate": candidate, **({"baseline": baseline} if baseline else {})}
    results, _, _ = gate.run_engines([DuckdbEngine, SqliteEngine], tiny, sides, 30.0, tmp_path)
    return gate.evaluate(results, sides, {}, list(expected), ["duckdb", "sqlite"])


OLD_AND_FIXED = {
    "date_window": (SQLITE_DATE_WINDOW_DATETIME, DUCKDB_DATE_WINDOW),
    "ratio": (SQLITE_RATIO_TRUNCATING, DUCKDB_RATIO),
}
FIXED = {
    "date_window": (SQLITE_DATE_WINDOW_DATE, DUCKDB_DATE_WINDOW),
    "ratio": (SQLITE_RATIO_EXACT, DUCKDB_RATIO),
}


def test_gate_catches_the_datetime_day_offset_regression(tiny, tmp_path):
    report = _evaluate(tiny, tmp_path, _side("30.24.0.1", {"date_window": OLD_AND_FIXED["date_window"]}))
    assert not report.passed
    [finding] = report.unexplained
    assert (finding.engine, finding.query, finding.check) == ("sqlite", "date_window", "reference")
    assert "row count 2 != 3" in finding.detail


def test_gate_catches_the_integer_division_truncation(tiny, tmp_path):
    report = _evaluate(tiny, tmp_path, _side("30.24.0.3", {"ratio": OLD_AND_FIXED["ratio"]}))
    assert not report.passed
    [finding] = report.unexplained
    assert (finding.engine, finding.query, finding.check) == ("sqlite", "ratio", "reference")
    assert "row count 0 != 2" in finding.detail


def test_fixed_translations_pass(tiny, tmp_path):
    report = _evaluate(tiny, tmp_path, _side("fixed", FIXED))
    assert report.passed
    assert not report.findings


def test_regression_against_a_fixed_baseline_is_unexplained(tiny, tmp_path):
    report = _evaluate(tiny, tmp_path, _side("regressed", OLD_AND_FIXED), _side("fixed", FIXED))
    assert not report.passed
    assert {(f.query, f.check) for f in report.unexplained} >= {("date_window", "reference"), ("ratio", "reference")}


def test_verified_improvement_over_an_old_baseline_is_reported_not_failed(tiny, tmp_path):
    report = _evaluate(tiny, tmp_path, _side("fixed", FIXED), _side("old", OLD_AND_FIXED))
    assert report.passed
    assert {f.query for f in report.improvements} == {"date_window", "ratio"}


def test_duckdb_change_against_baseline_needs_an_entry(tiny, tmp_path):
    changed = {"date_window": (SQLITE_DATE_WINDOW_DATE, "SELECT l_orderkey FROM lineitem WHERE l_orderkey < 3")}
    report = _evaluate(
        tiny, tmp_path, _side("candidate", changed), _side("baseline", {"date_window": FIXED["date_window"]})
    )
    assert ("duckdb", "date_window", "baseline") in {(f.engine, f.query, f.check) for f in report.unexplained}


def test_expected_entry_explains_a_finding_and_a_vanished_entry_is_stale(tiny, tmp_path):
    entry = {
        "benchmark": "tiny",
        "query": "date_window",
        "engine": "sqlite",
        "check": "reference",
        "match": "row count 2 != 3",
        "justification": "documented",
    }
    old = _side("30.24.0.1", {"date_window": OLD_AND_FIXED["date_window"]})
    assert _evaluate(tiny, tmp_path, old, expected=[entry]).passed
    stale = _evaluate(tiny, tmp_path, _side("fixed", {"date_window": FIXED["date_window"]}), expected=[entry])
    assert not stale.passed
    assert stale.stale == [entry]
    assert not _evaluate(tiny, tmp_path, old, expected=[{**entry, "match": "something else"}]).passed


def test_optional_entry_is_never_stale(tiny, tmp_path):
    entry = {
        "benchmark": "tiny",
        "query": "ratio",
        "engine": "sqlite",
        "check": "error",
        "match": "timeout",
        "justification": "wall clock",
        "optional": True,
    }
    assert _evaluate(tiny, tmp_path, _side("fixed", {"ratio": FIXED["ratio"]}), expected=[entry]).passed


def test_row_comparison_semantics():
    assert first_difference([(Decimal("1.50"), datetime.date(2000, 1, 1))], [(1.5, "2000-01-01")]) is None
    assert first_difference([("2000-01-01 00:00:00",)], [("2000-01-01",)]) is None
    assert first_difference([("2000-01-01 00:00:00",)], [("2000-01-01",)], strict=True)
    assert first_difference([(1,), (2,)], [(2,), (1,)]) is None
    assert first_difference([(1,)], [(1,), (1,)])
    assert first_difference([(2.0,)], [(2.32,)])


def _write(path: Path, differences: list[dict]) -> Path:
    path.write_text(json.dumps({"schema": 1, "differences": differences}), encoding="utf-8")
    return path


def test_expected_differences_require_justification_and_match(tmp_path):
    entry = {
        "benchmark": "tpcds",
        "queries": [1],
        "engine": "sqlite",
        "check": "error",
        "match": "timeout",
        "justification": "why",
    }
    assert gate.load_expected(_write(tmp_path / "ok.json", [entry]))[0]["query"] == "1"
    for broken in ({"justification": " "}, {"match": ""}, {"check": "nope"}, {"queries": []}):
        with pytest.raises(ValueError):
            gate.load_expected(_write(tmp_path / "bad.json", [{**entry, **broken}]))
    with pytest.raises(ValueError, match="duplicate"):
        gate.load_expected(_write(tmp_path / "dup.json", [entry, entry]))


def test_committed_expected_differences_are_valid_and_name_trackers_for_defects():
    entries = gate.load_expected()
    assert entries
    for entry in entries:
        assert entry["justification"].strip()
        if "defect" in entry["justification"]:
            assert entry.get("tracker")
