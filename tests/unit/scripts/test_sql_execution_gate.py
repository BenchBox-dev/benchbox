from __future__ import annotations

import datetime
import json
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.sql_execution_gate import augmentation, gate
from scripts.sql_execution_gate.engines import Dataset, DuckdbEngine, SqliteEngine
from scripts.sql_execution_gate.rows import first_difference, is_vacuous, vacuity_detail

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


@pytest.mark.parametrize(
    ("rows", "vacuous"),
    [
        ([], True),
        ([(0, None, None)], True),
        ([(0,)], True),
        ([(None,), (Decimal("0.00"),), (0.0,)], True),
        ([(0, None, Decimal("0.01"))], False),
        ([(0, "x")], False),
        ([(2, Decimal("6022.84"), Decimal("-3046.96"))], False),
        ([(0, None, None), (1, None, None)], False),
    ],
)
def test_vacuous_classifier(rows, vacuous):
    assert is_vacuous(rows) is vacuous


def test_vacuity_detail_names_the_shape():
    assert vacuity_detail([]) == "0 rows"
    assert vacuity_detail([(0, None)]) == "1 rows, every cell NULL or 0"


VACUOUS_BOTH = {
    "empty": (
        "SELECT l_orderkey FROM lineitem WHERE l_orderkey > 99",
        "SELECT l_orderkey FROM lineitem WHERE l_orderkey > 99",
    )
}
ZERO_AGGREGATE = {
    "zero_sum": (
        "SELECT COUNT(*), SUM(l_orderkey) FROM lineitem WHERE l_orderkey > 99",
        "SELECT COUNT(*), SUM(l_orderkey) FROM lineitem WHERE l_orderkey > 99",
    )
}


def test_matching_vacuous_results_fail_closed(tiny, tmp_path):
    report = _evaluate(tiny, tmp_path, _side("candidate", {**VACUOUS_BOTH, **ZERO_AGGREGATE}))
    assert not report.passed
    assert {(f.engine, f.query, f.check) for f in report.unexplained} == {
        (engine, query, "vacuous") for engine in ("duckdb", "sqlite") for query in ("empty", "zero_sum")
    }
    assert "0 rows" in next(f.detail for f in report.unexplained if f.query == "empty")
    assert "every cell NULL or 0" in next(f.detail for f in report.unexplained if f.query == "zero_sum")


def test_vacuous_comparison_passes_only_with_a_reasoned_entry_that_goes_stale(tiny, tmp_path):
    entries = [
        {
            "benchmark": "tiny",
            "query": "empty",
            "engine": engine,
            "check": "vacuous",
            "match": "0 rows",
            "justification": "no data at this scale can satisfy the predicate",
        }
        for engine in ("duckdb", "sqlite")
    ]
    report = _evaluate(tiny, tmp_path, _side("candidate", VACUOUS_BOTH), expected=entries)
    assert report.passed
    assert {f.engine for f in report.explained} == {"duckdb", "sqlite"}
    assert set(report.reasons.values()) == {"no data at this scale can satisfy the predicate"}
    informative = {"empty": ("SELECT l_orderkey FROM lineitem", "SELECT l_orderkey FROM lineitem")}
    stale = _evaluate(tiny, tmp_path, _side("candidate", informative), expected=entries)
    assert not stale.passed
    assert len(stale.stale) == 2


def test_informative_results_are_not_vacuous(tiny, tmp_path):
    report = _evaluate(tiny, tmp_path, _side("fixed", FIXED))
    assert report.passed
    assert not report.proven


def test_vacuous_reference_difference_is_a_reference_finding_not_a_vacuous_one(tiny, tmp_path):
    mixed = {"mixed": ("SELECT l_orderkey FROM lineitem WHERE l_orderkey > 99", "SELECT l_orderkey FROM lineitem")}
    report = _evaluate(tiny, tmp_path, _side("candidate", mixed))
    assert {(f.engine, f.check) for f in report.unexplained if f.query == "mixed"} == {("sqlite", "reference")}


def test_expected_difference_entries_can_name_several_engines(tmp_path):
    entry = {
        "benchmark": "tpcds",
        "queries": [1, 2],
        "engine": ["sqlite", "duckdb"],
        "check": "vacuous",
        "match": "0 rows",
        "justification": "why",
    }
    expanded = gate.load_expected(_write(tmp_path / "multi.json", [entry]))
    assert {(e["engine"], e["query"]) for e in expanded} == {
        ("sqlite", "1"),
        ("duckdb", "1"),
        ("sqlite", "2"),
        ("duckdb", "2"),
    }


def _augmented_side(per_query: dict[str, tuple[str, str]], base: dict[str, tuple[str, str]]) -> dict:
    return {
        "ref": "candidate",
        "runtime_pin": {},
        "queries": {
            "tiny": {n: {"sqlite": s, "duckdb": d} for n, (s, d) in base.items()},
            augmentation.AUGMENTED: {n: {"sqlite": s, "duckdb": d} for n, (s, d) in per_query.items()},
        },
    }


@pytest.fixture
def tiny_with_augmented(tiny, tmp_path, monkeypatch):
    monkeypatch.setattr(augmentation, "BASE", "tiny")
    directory = tmp_path / "augmented"
    directory.mkdir()
    (directory / "lineitem.tbl").write_text("1|1998-08-12|\n2|1998-09-11|\n99|1998-10-11|\n")
    (directory / "inv.tbl").write_text("3|4|\n")
    return {
        **tiny,
        augmentation.AUGMENTED: Dataset(
            augmentation.AUGMENTED,
            DDL,
            {"lineitem": directory / "lineitem.tbl", "inv": directory / "inv.tbl"},
        ),
    }


def _evaluate_augmented(datasets, tmp_path, side) -> gate.Report:
    sides = {"candidate": side}
    results, _, _ = gate.run_engines([DuckdbEngine, SqliteEngine], datasets, sides, 30.0, tmp_path)
    return gate.evaluate(results, sides, {}, [], ["duckdb", "sqlite"])


def test_non_vacuous_augmented_data_proves_a_vacuous_base_query(tiny_with_augmented, tmp_path):
    probe = "SELECT COUNT(*), SUM(l_orderkey) FROM lineitem WHERE l_orderkey > 98"
    report = _evaluate_augmented(
        tiny_with_augmented,
        tmp_path,
        _augmented_side({"zero.default": (probe, probe), "zero.1001": (probe, probe)}, {"zero": (probe, probe)}),
    )
    assert report.passed
    assert {(f.engine, f.query) for f in report.proven} == {("duckdb", "zero"), ("sqlite", "zero")}


def test_vacuous_augmented_data_does_not_prove_anything(tiny_with_augmented, tmp_path):
    probe = "SELECT COUNT(*), SUM(l_orderkey) FROM lineitem WHERE l_orderkey > 99"
    report = _evaluate_augmented(
        tiny_with_augmented,
        tmp_path,
        _augmented_side({"zero.default": (probe, probe)}, {"zero": (probe, probe)}),
    )
    assert not report.passed
    assert not report.proven
    assert {f.benchmark for f in report.unexplained} == {"tiny", augmentation.AUGMENTED}


def test_augmented_translation_defect_leaves_the_base_query_unproven(tiny_with_augmented, tmp_path):
    base = "SELECT COUNT(*), SUM(l_orderkey) FROM lineitem WHERE l_orderkey > 99"
    sqlite_wrong = "SELECT COUNT(*), SUM(l_orderkey) FROM lineitem WHERE l_orderkey > 98 AND l_orderkey < 2"
    duck_right = "SELECT COUNT(*), SUM(l_orderkey) FROM lineitem WHERE l_orderkey > 98"
    report = _evaluate_augmented(
        tiny_with_augmented,
        tmp_path,
        _augmented_side({"zero.default": (sqlite_wrong, duck_right)}, {"zero": (base, base)}),
    )
    assert not report.passed
    assert ("sqlite", "zero.default", "reference") in {(f.engine, f.query, f.check) for f in report.unexplained}
    assert {f.engine for f in report.proven} == {"duckdb"}


def test_render_lists_proven_and_not_proven_sections(tiny, tmp_path):
    report = _evaluate(tiny, tmp_path, _side("candidate", VACUOUS_BOTH))
    text = gate.render(report, {"duckdb": "1", "sqlite": "3"}, {}, {"candidate": _side("candidate", VACUOUS_BOTH)})
    assert "NOT PROVEN, vacuous comparisons: 2" in text
    assert "tiny sqlite: 1 not proven" in text
    assert "NO ALLOWLIST ENTRY" in text
    assert "UNEXPLAINED VACUOUS COMPARISONS (fail closed): 2" in text
    assert text.endswith("RESULT: FAIL")


def test_augmentation_gives_a_second_warehouse_and_the_query_parameters(tmp_path):
    from benchbox.core.tpcds.schema import TABLES

    def row(table: str, **values: str) -> str:
        names = [c.name for c in next(t for t in TABLES if t.name == table).columns]
        return "|".join(values.get(name, "") for name in names) + "|"

    source = tmp_path / "source"
    source.mkdir()
    files = {
        "catalog_sales": [row("catalog_sales", cs_item_sk=str(i), cs_warehouse_sk="1") for i in (1, 2, 3, 4)],
        "web_sales": [row("web_sales", ws_item_sk=str(i), ws_warehouse_sk="1") for i in (1, 2, 3, 4)],
        "customer_address": [row("customer_address", ca_address_sk=str(i), ca_state="AZ") for i in (1, 2, 3, 4)],
        "web_site": [row("web_site", web_site_sk=str(i), web_company_name="cally") for i in (1, 2)],
        "warehouse": [row("warehouse", w_warehouse_sk="1", w_warehouse_id="AAAAAAAABAAAAAAA", w_state="TN")],
    }
    tables = {}
    for table, lines in files.items():
        tables[table] = source / f"{table}.dat"
        tables[table].write_text("\n".join(lines) + "\n", encoding="utf-8")
    queries = {
        "16.default": {"duckdb": "WHERE \"ca_state\" = 'IL' AND \"web_company_name\" = 'pri'"},
        "16.1001": {"duckdb": "WHERE \"ca_state\" = 'IA'"},
    }
    augmented = augmentation.augment(Dataset("tpcds", "", tables), queries, tmp_path)

    def column(table: str, name: str) -> list[str]:
        names = [c.name for c in next(t for t in TABLES if t.name == table).columns]
        index = names.index(name)
        return [line.split("|")[index] for line in augmented.tables[table].read_text(encoding="utf-8").splitlines()]

    assert augmented.benchmark == augmentation.AUGMENTED
    assert column("catalog_sales", "cs_warehouse_sk") == ["2", "1", "2", "1"]
    assert column("web_sales", "ws_warehouse_sk") == ["2", "1", "2", "1"]
    assert set(column("customer_address", "ca_state")) == {"IA", "IL"}
    assert column("web_site", "web_company_name") == ["pri", "pri"]
    assert column("warehouse", "w_warehouse_sk") == ["1", "2"]


def test_augmentation_refuses_queries_that_name_no_parameters(tmp_path):
    with pytest.raises(ValueError, match="state or web_site company"):
        augmentation.augment(Dataset("tpcds", "", {}), {"16.default": {"duckdb": "SELECT 1"}}, tmp_path)
