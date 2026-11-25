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


def test_a_non_ok_audit_result_on_the_left_is_a_finding_even_when_both_sides_agree(tmp_path, left):
    right = build(tmp_path / "right.duckdb")
    both = [("DimCustomer C_TIER_INV", 1, "Mismatch", "tier"), ("Audit table batches", None, "OK", "three")]
    results = {left: both, right: both}
    [difference] = run(left, right, audit=lambda path: results[path]).unaccepted()
    assert difference.subject == "DimCustomer C_TIER_INV batch 1: Mismatch vs Mismatch"


@pytest.mark.parametrize("right_result", ["Mismatch", "OK"])
def test_a_left_mismatch_of_the_tier_alert_test_is_refused_by_the_shipped_list(tmp_path, left, right_result):
    right = build(tmp_path / "right.duckdb")
    name = "DimCustomer customer tier alerts"
    results = {left: [(name, 1, "Mismatch", "tier")], right: [(name, 1, right_result, "tier")]}
    entries = harness.load_expected(harness.DEFAULT_EXPECTED, None)
    [difference] = harness.compare(left, right, entries, audit=lambda path: results[path]).unaccepted()
    assert difference.subject == f"{name} batch 1: Mismatch vs {right_result}"


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


def build_market(path: Path, decimal: bool, market: list[tuple], trades: list[tuple]) -> Path:
    price = "DECIMAL(8,2)" if decimal else "DOUBLE"
    yield_type = "DECIMAL(5,2)" if decimal else "DOUBLE"
    connection = duckdb.connect(str(path))
    connection.execute(
        f"CREATE TABLE FactMarketHistory (SK_SecurityID BIGINT, SK_DateID BIGINT, Yield {yield_type}, "
        f"FiftyTwoWeekHigh {price}, SK_FiftyTwoWeekHighDate BIGINT, FiftyTwoWeekLow {price}, "
        f"SK_FiftyTwoWeekLowDate BIGINT, DayHigh {price}, DayLow {price})"
    )
    connection.executemany("INSERT INTO FactMarketHistory VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", market)
    connection.execute(
        "CREATE TABLE DimTrade (TradeID BIGINT, SK_CreateDateID BIGINT, SK_CreateTimeID BIGINT, BatchID INTEGER)"
    )
    connection.executemany("INSERT INTO DimTrade VALUES (?, ?, 1, ?)", trades)
    connection.execute("CREATE TABLE BatchDate (BatchDate DATE, BatchID INTEGER)")
    connection.executemany(
        "INSERT INTO BatchDate VALUES (?, ?)", [("2020-03-01", 1), ("2020-03-02", 2), ("2020-03-03", 3)]
    )
    connection.close()
    return path


LEAP = 20200301
PLAIN = 20190301
LEAP_EXTRA = 20190302


def market(rows: list[tuple]) -> list[tuple]:
    return [(*row, 0.0, 0.0) for row in rows]


def replace(rows: list[tuple], row: tuple) -> list[tuple]:
    return [market([row])[0] if existing[0] == row[0] else existing for existing in rows]


BENCHBOX_MARKET = market(
    [
        (1, LEAP, 2.5, 12.0, LEAP_EXTRA, 5.0, 20200105),
        (2, LEAP, 1.25, 10.0, 20200105, 5.0, 20200106),
        (3, LEAP, 0.5, 9.0, 20200103, 2.0, LEAP_EXTRA),
        (4, PLAIN, 0.75, 8.0, 20190110, 4.0, 20190111),
        (5, LEAP, 0.25, 7.0, 20200107, 4.0, 20200108),
    ]
)
IMPLEMENTATION_MARKET = market(
    [
        (1, LEAP, 0.00025, 11.0, 20190501, 5.0, 20200105),
        (2, LEAP, 0.000125, 10.0, 20200110, 5.0, 20200106),
        (3, LEAP, 0.00005, 9.0, 20200103, 3.0, 20190601),
        (4, PLAIN, 0.000075, 8.0, 20190110, 4.0, 20190111),
        (5, LEAP, 0.000025, 7.0, 20200107, 4.0, 20200109),
    ]
)
TIE_ROWS = [
    (2, 20200105, 0.0, 10.0, 20200105, 5.0, 20200106, 10.0, 0.0),
    (2, 20200110, 0.0, 10.0, 20200110, 5.0, 20200106, 10.0, 0.0),
    (5, 20200108, 0.0, 7.0, 20200107, 4.0, 20200108, 0.0, 4.0),
    (5, 20200109, 0.0, 7.0, 20200107, 4.0, 20200109, 0.0, 4.0),
    (2, 20180101, 0.0, 10.0, 20180101, 5.0, 20200106, 10.0, 0.0),
    (5, 20180101, 0.0, 7.0, 20200107, 4.0, 20180101, 0.0, 4.0),
    (2, 20200302, 0.0, 10.0, 20200302, 5.0, 20200106, 10.0, 0.0),
]
TRADES_BENCHBOX = [(1, 20200301, 1), (2, 20200302, 2)]
TRADES_IMPLEMENTATION = [(1, 20200301, 2), (2, 20200302, 2)]


def market_report(
    tmp_path: Path,
    benchbox: list[tuple] = BENCHBOX_MARKET,
    trades: list[tuple] = TRADES_BENCHBOX,
    implementation: list[tuple] = IMPLEMENTATION_MARKET,
) -> harness.Report:
    entries = harness.load_expected(harness.DEFAULT_EXPECTED, None)
    left = build_market(tmp_path / "left.duckdb", True, [*benchbox, *TIE_ROWS], trades)
    right = build_market(tmp_path / "right.duckdb", False, [*implementation, *TIE_ROWS], TRADES_IMPLEMENTATION)
    return harness.compare(left, right, entries)


def refused(report: harness.Report) -> set[tuple[str, str]]:
    return {(difference.table, difference.subject) for difference in report.unaccepted()}


def test_shipped_entries_accept_the_differences_they_describe(tmp_path):
    report = market_report(tmp_path)
    assert report.unaccepted() == []
    by_subject = {difference.subject: difference.accepted_by for difference in report.all_differences()}
    assert by_subject["FiftyTwoWeekHigh"] == ["fifty-two-week-high-window"]
    assert by_subject["SK_FiftyTwoWeekHighDate"] == ["fifty-two-week-high-date"]
    assert by_subject["FiftyTwoWeekLow"] == ["fifty-two-week-low-window"]
    assert by_subject["SK_FiftyTwoWeekLowDate"] == ["fifty-two-week-low-date"]
    assert by_subject["Yield"] == ["yield-multiplied-by-100"]
    assert by_subject["BatchID"] == ["dimtrade-batchid-fixed-at-creation"]


@pytest.mark.parametrize(
    ("row", "subject"),
    [
        ((4, PLAIN, 0.75, 13.0, 20190110, 4.0, 20190111), "FiftyTwoWeekHigh"),
        ((4, PLAIN, 0.75, 8.0, 20190110, 1.0, 20190111), "FiftyTwoWeekLow"),
        ((4, PLAIN, 0.75, 8.0, 20190120, 4.0, 20190111), "SK_FiftyTwoWeekHighDate"),
        ((4, PLAIN, 0.75, 8.0, 20190110, 4.0, 20190120), "SK_FiftyTwoWeekLowDate"),
        ((1, LEAP, 2.5, 12.0, 20190401, 5.0, 20200105), "FiftyTwoWeekHigh"),
        ((1, LEAP, 2.5, 12.0, 20190401, 5.0, 20200105), "SK_FiftyTwoWeekHighDate"),
        ((3, LEAP, 0.5, 9.0, 20200103, 2.0, 20190401), "FiftyTwoWeekLow"),
        ((3, LEAP, 0.5, 9.0, 20200103, 2.0, 20190401), "SK_FiftyTwoWeekLowDate"),
        ((2, LEAP, 1.25, 10.0, 20200104, 5.0, 20200106), "SK_FiftyTwoWeekHighDate"),
        ((5, LEAP, 0.25, 7.0, 20200107, 4.0, 20200107), "SK_FiftyTwoWeekLowDate"),
        ((2, LEAP, 1.25, 10.0, 20180101, 5.0, 20200106), "SK_FiftyTwoWeekHighDate"),
        ((5, LEAP, 0.25, 7.0, 20200107, 4.0, 20180101), "SK_FiftyTwoWeekLowDate"),
    ],
)
def test_shipped_entries_refuse_values_outside_the_rows_they_describe(tmp_path, row, subject):
    report = market_report(tmp_path, benchbox=replace(BENCHBOX_MARKET, row))
    assert ("FactMarketHistory", subject) in refused(report)


@pytest.mark.parametrize("trades", [[(1, 20200301, 1), (2, 20200302, 1)], [(1, 20200301, 0), (2, 20200302, 2)]])
def test_a_constant_batch_id_is_refused_where_it_is_not_the_creation_batch(tmp_path, trades):
    assert ("DimTrade", "BatchID") in refused(market_report(tmp_path, trades=trades))


def build_audit(path: Path, rows: list[tuple]) -> Path:
    connection = duckdb.connect(str(path))
    connection.execute("CREATE TABLE Audit (DataSet VARCHAR, BatchID INTEGER, Attribute VARCHAR, Value BIGINT)")
    connection.executemany("INSERT INTO Audit VALUES (?, ?, ?, ?)", rows)
    connection.close()
    return path


X = ("DimCustomer", 1, "C_NEW", 5)
Y = ("DimCustomer", 1, "C_OLD", 6)
TIER = ("DimCustomer", 1, "C_TIER_INV", 9)
TIER_UNCORRECTED = ("DimCustomer", 1, "C_TIER_INV", 8)


def test_same_row_counts_with_different_multiplicities_are_a_difference(tmp_path, capsys):
    left = build_audit(tmp_path / "left.duckdb", [X, X, Y])
    right = build_audit(tmp_path / "right.duckdb", [X, Y, Y])
    expected = write_expected(tmp_path / "expected.yaml", [])
    code = harness.main([str(left), str(right), "--expected", str(expected), "--departures", str(tmp_path / "none")])
    out = capsys.readouterr().out
    assert code == 1
    assert "FINDING table=Audit kind=rows_only_left 1 rows" in out
    assert "FINDING table=Audit kind=rows_only_right 1 rows" in out


def test_an_accepted_rows_only_difference_does_not_hide_a_duplicated_row(tmp_path):
    entries = harness.load_expected(harness.DEFAULT_EXPECTED, None)
    left = build_audit(tmp_path / "left.duckdb", [X, X, Y, TIER])
    right = build_audit(tmp_path / "right.duckdb", [X, Y, TIER_UNCORRECTED])
    [difference] = harness.compare(left, right, entries).unaccepted()
    assert (difference.kind, difference.count, difference.unexplained) == ("rows_only_left", 2, 1)
    clean_left = build_audit(tmp_path / "left2.duckdb", [X, Y, TIER])
    clean_right = build_audit(tmp_path / "right2.duckdb", [X, Y, TIER_UNCORRECTED])
    assert harness.compare(clean_left, clean_right, entries).unaccepted() == []


@pytest.mark.parametrize(("value", "is_refused"), [(8, False), (9, False), (10, True), (7, True), (11, True)])
def test_the_answer_key_correction_must_be_the_documented_delta(tmp_path, value, is_refused):
    entries = harness.load_expected(harness.DEFAULT_EXPECTED, None)
    left = build_audit(tmp_path / "left.duckdb", [X, Y, TIER])
    right = build_audit(tmp_path / "right.duckdb", [X, Y, ("DimCustomer", 1, "C_TIER_INV", value)])
    kinds = {difference.kind for difference in harness.compare(left, right, entries).unaccepted()}
    assert kinds == ({"rows_only_left", "rows_only_right"} if is_refused else set())


def test_a_tie_date_of_the_implementation_outside_the_window_is_refused(tmp_path):
    implementation = replace(IMPLEMENTATION_MARKET, (2, LEAP, 0.000125, 10.0, 20200302, 5.0, 20200106))
    report = market_report(tmp_path, implementation=implementation)
    assert ("FactMarketHistory", "SK_FiftyTwoWeekHighDate") in refused(report)


SECURITIES = [("AAA", "2020-01-01"), ("BBB", "2020-01-01"), ("AAA", "2020-02-01")]


def build_securities(path: Path, numbering: list[int], watches: list[tuple]) -> Path:
    connection = duckdb.connect(str(path))
    connection.execute("CREATE TABLE DimSecurity (SK_SecurityID BIGINT, Symbol VARCHAR, EffectiveDate DATE)")
    connection.executemany(
        "INSERT INTO DimSecurity VALUES (?, ?, ?)",
        [(key, *security) for key, security in zip(numbering, SECURITIES, strict=True)],
    )
    connection.execute(
        "CREATE TABLE FactWatches (SK_CustomerID BIGINT, SK_SecurityID BIGINT, SK_DateID_DatePlaced BIGINT)"
    )
    connection.executemany("INSERT INTO FactWatches VALUES (?, ?, ?)", watches)
    connection.close()
    return path


def security_findings(tmp_path: Path, right_watches: list[tuple]) -> set[tuple[str, str]]:
    left = build_securities(tmp_path / "left.duckdb", [1, 2, 3], [(7, 1, 20200105), (7, 3, 20200205)])
    right = build_securities(tmp_path / "right.duckdb", [2, 1, 3], right_watches)
    return {(difference.table, difference.kind) for difference in run(left, right).unaccepted()}


def test_fact_rows_match_through_the_security_they_reference(tmp_path):
    assert security_findings(tmp_path, [(7, 2, 20200105), (7, 3, 20200205)]) == {("DimSecurity", "column_value")}


def test_a_fact_row_referencing_another_security_is_a_difference(tmp_path):
    findings = security_findings(tmp_path, [(7, 1, 20200105), (7, 3, 20200205)])
    assert {("FactWatches", "rows_only_left"), ("FactWatches", "rows_only_right")} <= findings
