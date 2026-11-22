from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest
import yaml

from scripts.tpcdi import compare_warehouses as harness

pytestmark = [pytest.mark.unit, pytest.mark.fast]

CUSTOMERS = [
    (1, "2020-01-01", "Adams", "Gold"),
    (2, "2020-01-01", "Baker", "Silver"),
    (3, "2020-02-01", "Clark", "Bronze"),
]
YIELDS = [(10, 20200101, "2.50"), (11, 20200101, "1.25")]
MESSAGES = [
    (1, "Validation", "DimCustomer", "Row count", 3),
    (1, "Validation", "DimCustomer", "Row count", 3),
    (2, "Alert", "DimTrade", "Invalid commission", 1),
]


def build(
    path: Path,
    customers: list[tuple] = CUSTOMERS,
    yields: list[tuple] = YIELDS,
    yield_type: str = "DECIMAL(5,2)",
    messages: list[tuple] = MESSAGES,
    yield_scale: float = 1.0,
) -> Path:
    connection = duckdb.connect(str(path))
    connection.execute(
        "CREATE TABLE DimCustomer (CustomerID BIGINT, EffectiveDate DATE, LastName VARCHAR, Tier VARCHAR)"
    )
    connection.executemany("INSERT INTO DimCustomer VALUES (?, ?, ?, ?)", customers)
    connection.execute(f"CREATE TABLE FactMarketHistory (SK_SecurityID BIGINT, SK_DateID BIGINT, Yield {yield_type})")
    connection.executemany(
        "INSERT INTO FactMarketHistory VALUES (?, ?, ?)",
        [(security, date, float(value) * yield_scale) for security, date, value in yields],
    )
    connection.execute(
        "CREATE TABLE DImessages (MessageDateAndTime TIMESTAMP, BatchID INTEGER, MessageSource VARCHAR, "
        "MessageText VARCHAR, MessageType VARCHAR, MessageData VARCHAR)"
    )
    connection.executemany(
        "INSERT INTO DImessages VALUES (now(), ?, ?, ?, ?, NULL)",
        [(batch, source, text, kind) for batch, kind, source, text, _ in messages],
    )
    connection.close()
    return path


def write_expected(path: Path, entries: list[dict]) -> Path:
    path.write_text(yaml.safe_dump({"entries": entries}), encoding="utf-8")
    return path


YIELD_TYPE = {
    "id": "yield-type",
    "kind": "column_type",
    "clause": "3.2",
    "reason": "exact numerics",
    "table": "FactMarketHistory",
    "column": "Yield",
    "left_type": "DECIMAL(*)",
    "right_type": "DOUBLE",
}
YIELD_VALUE = {
    "id": "yield-value",
    "kind": "column_value",
    "clause": "4.5.11.2",
    "reason": "multiplying by 100",
    "table": "FactMarketHistory",
    "column": "Yield",
    "condition": "l.yield = round(r.yield * 10000, 2)",
}


@pytest.fixture
def left(tmp_path: Path) -> Path:
    return build(tmp_path / "left.duckdb")


def run(left: Path, right: Path, entries: list[dict] | None = None, audit=None, tmp: Path | None = None):
    expected = write_expected((tmp or right.parent) / "expected.yaml", entries or [])
    return harness.compare(left, right, harness.load_expected(expected, None), audit=audit)


def test_identical_warehouses_have_no_differences(tmp_path, left, capsys):
    right = build(tmp_path / "right.duckdb")
    expected = write_expected(tmp_path / "expected.yaml", [])
    code = harness.main([str(left), str(right), "--expected", str(expected), "--departures", str(tmp_path / "none")])
    assert code == 0
    assert "RESULT: 0 unaccepted differences" in capsys.readouterr().out


def test_changed_row_fails_and_names_the_table(tmp_path, left, capsys):
    changed = [(1, "2020-01-01", "Adams", "Gold"), (2, "2020-01-01", "Baker", "Platinum"), CUSTOMERS[2]]
    right = build(tmp_path / "right.duckdb", customers=changed)
    expected = write_expected(tmp_path / "expected.yaml", [])
    code = harness.main([str(left), str(right), "--expected", str(expected), "--departures", str(tmp_path / "none")])
    out = capsys.readouterr().out
    assert code == 1
    assert "FINDING table=DimCustomer kind=column_value Tier unexplained=1 of 1" in out
    assert "RESULT: 1 unaccepted differences in DimCustomer" in out


def test_missing_row_fails_and_names_the_table(tmp_path, left):
    right = build(tmp_path / "right.duckdb", customers=CUSTOMERS[:2])
    report = run(left, right)
    [difference] = report.unaccepted()
    assert (difference.table, difference.kind, difference.count) == ("DimCustomer", "rows_only_left", 1)


def test_extra_row_on_the_other_side_is_reported_on_that_side(tmp_path, left):
    right = build(tmp_path / "right.duckdb", customers=[*CUSTOMERS, (4, "2020-03-01", "Davis", "Gold")])
    report = run(left, right)
    [difference] = report.unaccepted()
    assert (difference.kind, difference.count) == ("rows_only_right", 1)
    assert report.tables[0].left_rows == 3
    assert report.tables[0].right_rows == 4


def test_changed_message_count_fails_and_names_the_table(tmp_path, left, capsys):
    right = build(tmp_path / "right.duckdb", messages=[*MESSAGES, (2, "Alert", "DimTrade", "Invalid commission", 1)])
    expected = write_expected(tmp_path / "expected.yaml", [])
    code = harness.main([str(left), str(right), "--expected", str(expected), "--departures", str(tmp_path / "none")])
    out = capsys.readouterr().out
    assert code == 1
    assert "FINDING table=DImessages kind=messages 1 groups unexplained=1 of 1" in out
    assert "DImessages benchbox batch 2 Alert: 1" in out
    assert "DImessages duckdb-implementation batch 2 Alert: 2" in out


def test_missing_message_type_is_a_difference(tmp_path, left):
    right = build(tmp_path / "right.duckdb", messages=MESSAGES[:2])
    [difference] = run(left, right).unaccepted()
    assert (difference.table, difference.kind) == ("DImessages", "messages")


def test_listed_type_and_value_differences_are_accepted(tmp_path, left):
    right = build(tmp_path / "right.duckdb", yield_type="DOUBLE", yield_scale=0.0001)
    report = run(left, right, [YIELD_TYPE, YIELD_VALUE])
    assert report.unaccepted() == []
    assert report.unused_entries == []
    accepted = {difference.kind: difference.accepted_by for difference in report.all_differences()}
    assert accepted == {"column_type": ["yield-type"], "column_value": ["yield-value"]}


def test_unlisted_difference_is_not_accepted_beside_listed_ones(tmp_path, left):
    changed = [(1, "2020-01-01", "Adams", "Platinum"), *CUSTOMERS[1:]]
    right = build(tmp_path / "right.duckdb", customers=changed, yield_type="DOUBLE", yield_scale=0.0001)
    report = run(left, right, [YIELD_TYPE, YIELD_VALUE])
    [difference] = report.unaccepted()
    assert (difference.table, difference.kind, difference.subject) == ("DimCustomer", "column_value", "Tier")


def test_a_listed_value_difference_must_hold_for_every_differing_row(tmp_path, left):
    right = build(tmp_path / "right.duckdb", yield_type="DOUBLE", yield_scale=0.0001)
    connection = duckdb.connect(str(right))
    connection.execute("UPDATE FactMarketHistory SET Yield = 0.5 WHERE SK_SecurityID = 11")
    connection.close()
    report = run(left, right, [YIELD_TYPE, YIELD_VALUE])
    [difference] = report.unaccepted()
    assert (difference.kind, difference.count, difference.unexplained) == ("column_value", 2, 1)


def test_unlisted_type_difference_is_not_accepted(tmp_path, left):
    right = build(tmp_path / "right.duckdb", yield_type="DOUBLE", yield_scale=1.0)
    [difference] = run(left, right).unaccepted()
    assert difference.kind == "column_type"
    assert difference.subject == "Yield DECIMAL(5,2) vs DOUBLE"


def test_row_only_entry_accepts_only_the_rows_its_condition_selects(tmp_path, left):
    right = build(tmp_path / "right.duckdb", customers=CUSTOMERS[:1])
    entry = {
        "id": "late-rows",
        "kind": "rows_only",
        "clause": "4.6.1",
        "reason": "history",
        "table": "DimCustomer",
        "side": "left",
        "condition": "t.customerid = 2",
    }
    [difference] = run(left, right, [entry]).unaccepted()
    assert (difference.count, difference.unexplained, difference.accepted_by) == (2, 1, ["late-rows"])


def test_message_entry_accepts_listed_groups(tmp_path, left):
    right = build(tmp_path / "right.duckdb", messages=MESSAGES[:2])
    entry = {
        "id": "alerts",
        "kind": "messages",
        "clause": "4.5.8.3",
        "reason": "alerts",
        "condition": "g.messagetype = 'Alert' AND g.right_count IS NULL",
    }
    assert run(left, right, [entry]).unaccepted() == []


def test_missing_table_and_column_need_entries(tmp_path, left):
    right = build(tmp_path / "right.duckdb")
    connection = duckdb.connect(str(right))
    connection.execute("DROP TABLE FactMarketHistory")
    connection.execute("ALTER TABLE DimCustomer DROP COLUMN Tier")
    connection.close()
    report = run(left, right)
    kinds = sorted((difference.table, difference.kind) for difference in report.unaccepted())
    assert kinds == [("DimCustomer", "column_only_left"), ("FactMarketHistory", "table_only_left")]
    entries = [
        {
            "id": "no-table",
            "kind": "table_missing",
            "clause": "3.4",
            "reason": "r",
            "table": "FactMarketHistory",
            "side": "left",
        },
        {
            "id": "no-column",
            "kind": "column_missing",
            "clause": "3.4",
            "reason": "r",
            "table": "DimCustomer",
            "column": "Tier",
            "side": "left",
        },
    ]
    assert run(left, right, entries).unaccepted() == []


def test_duplicate_keys_are_a_finding(tmp_path, left):
    right = build(tmp_path / "right.duckdb", customers=[*CUSTOMERS, CUSTOMERS[0]])
    kinds = {difference.kind for difference in run(left, right).unaccepted()}
    assert "key_not_unique" in kinds


def test_audit_outcome_differences_need_an_entry(tmp_path, left):
    right = build(tmp_path / "right.duckdb")
    results = {
        left: [("DimCustomer C_TIER_INV", 1, "OK", "tier"), ("Audit table batches", None, "OK", "three")],
        right: [("DimCustomer C_TIER_INV", 1, "Mismatch", "tier"), ("Audit table batches", None, "OK", "three")],
    }
    report = run(left, right, audit=lambda path: results[path])
    [difference] = report.unaccepted()
    assert difference.table == "audit"
    assert difference.subject == "DimCustomer C_TIER_INV batch 1: OK vs Mismatch"
    entry = {
        "id": "tier-key",
        "kind": "audit",
        "clause": "2.4.5",
        "reason": "answer key",
        "test": "DimCustomer C_TIER_INV",
        "batch": "1",
        "left_result": "OK",
        "right_result": "Mismatch",
    }
    accepted = run(left, right, [entry], audit=lambda path: results[path])
    assert accepted.unaccepted() == []
    assert [row["right"] for row in accepted.audit] == ["OK", "Mismatch"]


def test_unused_entries_are_reported(tmp_path, left):
    right = build(tmp_path / "right.duckdb")
    assert run(left, right, [YIELD_TYPE]).unused_entries == ["yield-type"]


def test_entries_must_cite_a_departure_row_when_the_catalogue_exists(tmp_path):
    catalogue = tmp_path / "DEPARTURES.yaml"
    catalogue.write_text(yaml.safe_dump({"departures": [{"id": "D1"}]}), encoding="utf-8")
    entry = {**YIELD_TYPE}
    path = write_expected(tmp_path / "expected.yaml", [entry])
    with pytest.raises(harness.ConfigError, match="yield-type: departure None is not a row"):
        harness.load_expected(path, catalogue)
    path = write_expected(tmp_path / "expected.yaml", [{**entry, "departure": "D1"}])
    [loaded] = harness.load_expected(path, catalogue)
    assert loaded.departure == "D1"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"clause": ""}, "yield-type: missing clause"),
        ({"kind": "other"}, "yield-type: unknown kind 'other'"),
        ({"surprise": 1}, "yield-type: unknown fields surprise"),
    ],
)
def test_invalid_entries_are_refused(tmp_path, change, message):
    path = write_expected(tmp_path / "expected.yaml", [{**YIELD_TYPE, **change}])
    with pytest.raises(harness.ConfigError, match=message):
        harness.load_expected(path, None)


def test_invalid_expected_file_exits_two(tmp_path, left, capsys):
    path = write_expected(tmp_path / "expected.yaml", [{**YIELD_TYPE, "clause": ""}])
    assert harness.main([str(left), str(left), "--expected", str(path)]) == 2
    assert "missing clause" in capsys.readouterr().err


def test_shipped_expected_differences_load_and_cite_clauses():
    entries = harness.load_expected(harness.DEFAULT_EXPECTED, None)
    assert entries
    assert all(entry.clause.strip() and entry.reason.strip() for entry in entries)


def test_audit_only_lists_every_outcome_and_fails_on_a_non_ok_result(tmp_path, left, monkeypatch, capsys):
    rows = [("Audit table batches", None, "OK", "three"), ("DimCustomer C_TIER_INV", 1, "Mismatch", "tier")]
    monkeypatch.setattr(harness, "run_official_audit", lambda path, memory_limit: rows)
    report = tmp_path / "audit.json"
    assert harness.main([str(left), "--audit-only", "--json", str(report)]) == 1
    out = capsys.readouterr().out
    assert "audit DimCustomer C_TIER_INV batch 1: Mismatch (tier)" in out
    assert "RESULT: 2 audit rows, 1 not OK" in out
    assert [row["results"] for row in json.loads(report.read_text(encoding="utf-8"))] == [["OK"], ["Mismatch"]]
    monkeypatch.setattr(harness, "run_official_audit", lambda path, memory_limit: rows[:1])
    assert harness.main([str(left), "--audit-only"]) == 0


def test_two_databases_or_audit_only_but_not_both(tmp_path, left):
    with pytest.raises(SystemExit):
        harness.main([str(left), "--audit-only", str(left)])
    with pytest.raises(SystemExit):
        harness.main([str(left)])


def test_integer_widths_and_decimal_precision_are_not_type_differences(tmp_path, left):
    right = build(tmp_path / "right.duckdb", yield_type="DECIMAL(9,2)")
    connection = duckdb.connect(str(right))
    connection.execute("ALTER TABLE FactMarketHistory ALTER SK_SecurityID TYPE DECIMAL(38,0)")
    connection.execute("ALTER TABLE DimCustomer ALTER CustomerID TYPE SMALLINT")
    connection.close()
    assert run(left, right).all_differences() == []


MARKET_ROWS = [
    (1, 20200101, 2.5, 10.0, 20200105, 5.0, 20200301),
    (2, 20200101, 1.25, 12.0, 20200102, 4.0, 20200302),
    (3, 20200101, 0.5, 9.0, 20200103, 3.0, 20200303),
]


def build_market(path: Path, decimal: bool, rows: list[tuple]) -> Path:
    price = "DECIMAL(8,2)" if decimal else "DOUBLE"
    yield_type = "DECIMAL(5,2)" if decimal else "DOUBLE"
    connection = duckdb.connect(str(path))
    connection.execute(
        f"CREATE TABLE FactMarketHistory (SK_SecurityID BIGINT, SK_DateID BIGINT, Yield {yield_type}, "
        f"FiftyTwoWeekHigh {price}, SK_FiftyTwoWeekHighDate BIGINT, FiftyTwoWeekLow {price}, "
        "SK_FiftyTwoWeekLowDate BIGINT)"
    )
    connection.executemany("INSERT INTO FactMarketHistory VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    connection.execute(
        "CREATE TABLE DimTrade (TradeID BIGINT, SK_CreateDateID BIGINT, SK_CreateTimeID BIGINT, BatchID INTEGER)"
    )
    connection.executemany(
        "INSERT INTO DimTrade VALUES (?, 20200101, 1, ?)", [(1, 1), (2, 1)] if decimal else [(1, 2), (2, 1)]
    )
    connection.close()
    return path


def test_shipped_entries_accept_the_differences_they_describe_and_nothing_else(tmp_path):
    entries = harness.load_expected(harness.DEFAULT_EXPECTED, None)
    benchbox_rows = [
        (1, 20200101, 2.5, 10.0, 20200105, 5.0, 20200301),
        (2, 20200101, 1.25, 12.0, 20200102, 3.5, 20200201),
        (3, 20200101, 0.5, 9.0, 20200103, 3.0, 20200303),
    ]
    other_rows = [
        (1, 20200101, 0.00025, 10.0, 20200110, 5.0, 20200305),
        (2, 20200101, 0.000125, 11.0, 20200104, 4.0, 20200302),
        (3, 20200101, 0.00005, 9.0, 20200103, 3.0, 20200303),
    ]
    left = build_market(tmp_path / "left.duckdb", True, benchbox_rows)
    right = build_market(tmp_path / "right.duckdb", False, other_rows)
    report = harness.compare(left, right, entries)
    assert report.unaccepted() == []
    by_subject = {difference.subject: difference.accepted_by for difference in report.all_differences()}
    assert by_subject["FiftyTwoWeekHigh"] == ["fifty-two-week-high-window"]
    assert by_subject["SK_FiftyTwoWeekHighDate"] == ["fifty-two-week-high-date"]
    assert by_subject["FiftyTwoWeekLow"] == ["fifty-two-week-low-window"]
    assert by_subject["SK_FiftyTwoWeekLowDate"] == ["fifty-two-week-low-date"]
    assert by_subject["Yield"] == ["yield-multiplied-by-100"]
    assert by_subject["BatchID"] == ["dimtrade-batchid-fixed-at-creation"]


def test_shipped_entries_refuse_a_high_below_the_implementations_and_a_later_batch(tmp_path):
    entries = harness.load_expected(harness.DEFAULT_EXPECTED, None)
    other_rows = [(s, d, y / 10000, *rest) for s, d, y, *rest in MARKET_ROWS]
    other_rows[0] = (1, 20200101, 0.00025, 10.5, 20200105, 5.0, 20200301)
    left = build_market(tmp_path / "left.duckdb", True, MARKET_ROWS)
    right = build_market(tmp_path / "right.duckdb", False, other_rows)
    connection = duckdb.connect(str(left))
    connection.execute("UPDATE DimTrade SET BatchID = 3 WHERE TradeID = 1")
    connection.close()
    report = harness.compare(left, right, entries)
    refused = {(difference.table, difference.subject) for difference in report.unaccepted()}
    assert refused == {
        ("FactMarketHistory", "FiftyTwoWeekHigh"),
        ("DimTrade", "BatchID"),
    }
