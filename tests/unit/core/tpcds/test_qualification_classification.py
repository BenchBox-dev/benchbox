from __future__ import annotations

import dataclasses
import json
from decimal import Decimal

import duckdb
import pytest

from benchbox.core.equivalence.cross_surface import _TPCDS_LEGITIMATELY_EMPTY
from benchbox.core.tpcds.qualification import classification, runner, summary
from benchbox.core.tpcds.qualification.classification import (
    CHAR_PADDING,
    DISPLAY_PRECISION,
    FLOAT_DETAIL,
    HALF_BOUNDARY_ROUNDING,
    KNOWN,
    MALFORMED_OFFICIAL_ANSWER,
    REQUIRED_NONEMPTY,
    RETURNS_DIFFERENCE,
    ROW_WIDTH_ERROR,
    TIED_ORDER,
    KnownDifference,
    gate,
    printed_classification,
    rows_digest,
)

pytestmark = [pytest.mark.unit, pytest.mark.medium, pytest.mark.tpcds]

_OWNER_EMPTY_AT_SF001 = {
    "3",
    "4",
    "8",
    "10",
    "23a",
    "23b",
    "24a",
    "24b",
    "31",
    "32",
    "37",
    "39a",
    "39b",
    "41",
    "54",
    "58",
    "61",
    "64",
    "65",
    "73",
    "82",
    "85",
    "90",
    "91",
    "92",
}

_DISPLAY_ONLY = {
    "7",
    "8",
    "9",
    "12",
    "13",
    "20",
    "22",
    "26",
    "27",
    "28",
    "31",
    "36",
    "49",
    "58",
    "59",
    "61",
    "63",
    "70",
    "83",
    "86",
    "90",
    "98",
}


def _answer(rows, null_order="unspecified", file="1.ans"):
    return {
        "file": file,
        "sha256": "0" * 64,
        "null_order": null_order,
        "null_tokens": [],
        "columns": [f"C{index}" for index in range(len(rows[0]))],
        "rows": rows,
    }


def _classify(entry, actual, answer, types, sql="SELECT 1"):
    expected = runner.convert_rows(answer, types)
    item = {
        "file": answer["file"],
        "null_order": answer["null_order"],
        "sql_to_printed": runner.compare_rows(expected, actual, "1", sql),
    }
    item["sql_to_printed_display"] = runner.compare_rows(
        expected, runner.display_rounded(actual, answer, types), "1", sql
    )
    return runner.side_classification(entry, item, "sql", actual, expected, answer, types, "1", sql, [])


def _item(sql, dataframe, file="1.ans", null_order="unspecified"):
    item = {"file": file, "null_order": null_order}
    for side, label in (("sql", sql), ("dataframe", dataframe)):
        item[f"{side}_to_printed"] = {"status": "match" if label == "strict" else "mismatch"}
        if label not in {"strict", None}:
            item[f"{side}_classification"] = {"class": label}
    return item


def test_baseline_records_every_known_difference_with_evidence():
    assert set(KNOWN) == _DISPLAY_ONLY | {"17", "39a", "39b", "66", "77", "78", "84", "85", "93"}
    assert set(KNOWN) <= set(runner.STATEMENTS)
    assert all(entry.evidence and entry.classes for entry in KNOWN.values())
    assert {name for name, entry in KNOWN.items() if entry.classes == (DISPLAY_PRECISION,)} == _DISPLAY_ONLY
    assert KNOWN["84"].classes == (CHAR_PADDING,)
    assert FLOAT_DETAIL in KNOWN["39a"].classes and FLOAT_DETAIL in KNOWN["39b"].classes
    assert all(HALF_BOUNDARY_ROUNDING in KNOWN[name].classes for name in ("66", "77", "78"))
    assert KNOWN["85"].classes == KNOWN["93"].classes == (RETURNS_DIFFERENCE,)
    assert KNOWN["85"].result_digest and KNOWN["93"].result_digest
    assert {name for name, entry in KNOWN.items() if entry.variant_files} == {"66", "77", "93"}


def test_q17_is_a_permanent_exception_recorded_with_its_shape():
    entry = KNOWN["17"]
    assert entry.classes == (MALFORMED_OFFICIAL_ANSWER,) and entry.permanent
    assert (entry.official_columns, entry.sql_columns) == (14, 15)
    assert "merged header" in entry.evidence and "wrapped continuation" in entry.evidence
    assert [name for name, other in KNOWN.items() if other.permanent] == ["17"]


def test_lane_requires_nonempty_comparison_for_the_statements_empty_at_sf001_and_q93():
    assert _OWNER_EMPTY_AT_SF001 | {"93"} == REQUIRED_NONEMPTY and len(REQUIRED_NONEMPTY) == 26
    assert set(runner.STATEMENTS) >= REQUIRED_NONEMPTY
    for statement in _OWNER_EMPTY_AT_SF001:
        key = statement[:-1] if statement.endswith("a") else statement
        assert key in _TPCDS_LEGITIMATELY_EMPTY, statement
    assert "93" not in _TPCDS_LEGITIMATELY_EMPTY


def test_a_required_statement_that_is_empty_at_sf1_fails_the_gate():
    classification_ = {"state": "strict"}
    parity = {"status": "match"}
    assert gate("3", "match", parity, classification_, True)["status"] == "pass"
    assert gate("3", "match", parity, classification_, False)["failures"] == ["empty_at_sf1"]
    assert gate("1", "match", parity, classification_, False)["status"] == "pass"


def test_dataframe_divergence_fails_even_when_the_printed_difference_is_classified():
    classified = {"state": "classified"}
    assert gate("7", "mismatch", {"status": "match"}, classified, True)["status"] == "pass"
    failed = gate("7", "mismatch", {"status": "mismatch"}, classified, True)
    assert failed["failures"] == ["dataframe_to_sql_divergence"]
    both = gate("99", "mismatch", {"status": "mismatch"}, {"state": "unclassified"}, True)
    assert both["failures"] == ["dataframe_to_sql_divergence", "unclassified_printed_mismatch"]


def test_a_new_statement_with_a_printed_mismatch_is_unclassified_and_fails():
    files = [_item(DISPLAY_PRECISION, DISPLAY_PRECISION)]
    assert "2" not in KNOWN
    result = printed_classification("2", "mismatch", files)
    assert result == {"state": "unclassified"}
    assert gate("2", "mismatch", {"status": "match"}, result, True)["status"] == "fail"


def test_known_statements_classify_only_with_a_label_from_their_own_classes():
    assert (
        printed_classification("13", "mismatch", [_item(DISPLAY_PRECISION, DISPLAY_PRECISION)])["state"] == "classified"
    )
    assert printed_classification("13", "mismatch", [_item(DISPLAY_PRECISION, None)]) == {"state": "unclassified"}
    assert printed_classification("13", "mismatch", [_item(DISPLAY_PRECISION, CHAR_PADDING)]) == {
        "state": "unclassified"
    }
    assert printed_classification("84", "mismatch", [_item(CHAR_PADDING, CHAR_PADDING)])["classes"] == [CHAR_PADDING]
    assert (
        printed_classification("84", "mismatch", [_item(DISPLAY_PRECISION, DISPLAY_PRECISION)])["state"]
        == "unclassified"
    )


def test_strict_sides_combine_with_classified_sides_on_the_same_file():
    result = printed_classification("86", "mismatch", [_item("strict", DISPLAY_PRECISION)])
    assert result["state"] == "classified" and result["classes"] == [DISPLAY_PRECISION]
    assert printed_classification("86", "match", [_item("strict", "strict")]) == {"state": "strict"}


def test_a_parity_only_failure_keeps_the_printed_comparison_strict():
    assert printed_classification("2", "mismatch", [_item("strict", "strict")]) == {"state": "strict"}
    assert printed_classification("2", "mismatch", [_item("strict", None)]) == {"state": "unclassified"}


def test_a_matching_file_does_not_hide_an_error_on_another_file():
    error = {"file": "1.ans", "null_order": "unspecified", "status": "error"}
    assert printed_classification("1", "error", [error, _item("strict", "strict")]) == {"state": "unclassified"}


def test_a_null_order_variant_file_never_classifies_a_statement_by_itself():
    variant = classification.NULL_ORDER_VARIANT
    only_variant = [
        _item(variant, variant, "66_NULLS_FIRST.ans", "first"),
        _item(None, None, "66_NULLS_LAST.ans", "last"),
    ]
    assert printed_classification("66", "mismatch", only_variant) == {"state": "unclassified"}
    both = [
        _item(variant, variant, "66_NULLS_FIRST.ans", "first"),
        _item(DISPLAY_PRECISION, HALF_BOUNDARY_ROUNDING, "66_NULLS_LAST.ans", "last"),
    ]
    assert printed_classification("66", "mismatch", both)["file"] == "66_NULLS_LAST.ans"


def _q17_file(**changes):
    item = {
        "file": "17.ans",
        "sha256": KNOWN["17"].answer_sha256,
        "null_order": "unspecified",
        "status": "error",
        "detail": ROW_WIDTH_ERROR,
        "official_columns": 14,
        "sql_columns": 15,
    }
    return {**item, **changes}


def test_q17_classifies_only_the_recorded_malformed_shape():
    assert printed_classification("17", "error", [_q17_file()])["permanent"] is True
    for change in ({"official_columns": 13}, {"sql_columns": 16}, {"sha256": "1" * 64}, {"detail": "other"}):
        assert printed_classification("17", "error", [_q17_file(**change)]) == {"state": "unclassified"}
    assert printed_classification("17", "error", []) == {"state": "unclassified"}
    assert gate("17", "error", {"status": "mismatch"}, printed_classification("17", "error", [_q17_file()]), True)[
        "failures"
    ] == ["dataframe_to_sql_divergence"]


def test_char_padding_labels_only_the_blank_padded_last_name():
    entry = KNOWN["84"]
    types = ["VARCHAR", "VARCHAR"]
    padded = _answer([["A", "Carter".ljust(30) + ", Rodney"], ["B", "Moore".ljust(30) + ","]])
    actual = [("A", "Carter, Rodney"), ("B", "Moore, ")]
    assert _classify(entry, actual, padded, types) == {"class": CHAR_PADDING}
    assert _classify(entry, [("A", "Carter, Rodnez"), actual[1]], padded, types) is None
    assert _classify(entry, [("A", "Carter, Rodney"), ("B", "Other, ")], padded, types) is None
    short = _answer([["A", "Carter".ljust(12) + ", Rodney"], ["B", "Moore".ljust(30) + ","]])
    assert _classify(entry, actual, short, types) is None


def test_half_boundary_labels_only_cells_at_the_rounding_boundary():
    entry = dataclasses.replace(KNOWN["66"], columns=frozenset({1}))
    types = ["VARCHAR", "DOUBLE"]
    printed = _answer([["a", "21346976.1"]])
    assert _classify(entry, [("a", 21346976.049999997)], printed, types) == {"class": HALF_BOUNDARY_ROUNDING}
    assert _classify(entry, [("a", 21346976.02)], printed, types) is None
    assert _classify(entry, [("a", 21346976.0)], printed, types) is None
    other_column = dataclasses.replace(entry, columns=frozenset({0}))
    assert _classify(other_column, [("a", 21346976.049999997)], printed, types) is None


def test_half_up_ratio_labels_only_the_exact_half_rounded_up():
    entry = KNOWN["78"]
    types = ["INTEGER", "DOUBLE", "INTEGER", "INTEGER"]
    entry = dataclasses.replace(entry, ratio=(1, 2, 3, 2), columns=frozenset())
    printed = _answer([["1", ".58", "23", "40"]])
    assert _classify(entry, [(1, 0.57, 23, 40)], printed, types) == {"class": HALF_BOUNDARY_ROUNDING}
    wrong = _answer([["1", ".59", "23", "40"]])
    assert _classify(entry, [(1, 0.57, 23, 40)], wrong, types) is None
    not_half = _answer([["1", ".58", "22", "40"]])
    assert _classify(entry, [(1, 0.57, 22, 40)], not_half, types) is None


def test_float_detail_is_bounded_and_limited_to_its_columns():
    entry = KNOWN["39a"]
    types = ["INTEGER", "INTEGER", "INTEGER", "INTEGER", "DOUBLE"]
    printed = _answer([["1", "2", "3", "4", "1.0080922644484"]])
    assert _classify(entry, [(1, 2, 3, 4, 1.0080922635507177)], printed, types) == {"class": FLOAT_DETAIL}
    assert _classify(entry, [(1, 2, 3, 4, 1.00809236)], printed, types) is None
    assert _classify(entry, [(1, 2, 3, 5, 1.0080922635507177)], printed, types) is None


def test_tied_order_reorders_only_rows_tied_on_the_order_key():
    entry = dataclasses.replace(KNOWN["77"], columns=frozenset(), tie_key=(0,))
    types = ["VARCHAR", "DECIMAL(10,2)"]
    printed = _answer([["a", "10.5"], ["a", "2000"], ["b", "3"]])
    swapped = [("a", Decimal("2000.04")), ("a", Decimal("10.54")), ("b", Decimal("3.00"))]
    assert _classify(entry, swapped, printed, types, "SELECT x, y FROM t ORDER BY x") == {"class": TIED_ORDER}
    across = [("a", Decimal("10.54")), ("b", Decimal("3.00")), ("a", Decimal("2000.04"))]
    assert _classify(entry, across, printed, types, "SELECT x, y FROM t ORDER BY x") is None


def test_returns_difference_requires_the_pinned_result_digest():
    types = ["INTEGER", "DECIMAL(10,2)"]
    actual = [(1, Decimal("5.00")), (2, Decimal("7.10"))]
    printed = _answer([["1", "5"], ["2", "9"]])
    entry = KnownDifference("1", (RETURNS_DIFFERENCE,), "e", result_digest=rows_digest(actual))
    assert _classify(entry, actual, printed, types) == {"class": RETURNS_DIFFERENCE}
    changed = [(1, Decimal("5.00")), (2, Decimal("7.11"))]
    assert _classify(entry, changed, printed, types) is None
    assert rows_digest([(1, 2.5000000001)]) == rows_digest([(1, Decimal("2.5"))])
    assert rows_digest([(1, None)]) != rows_digest([(1, "NULL ")])


def test_result_digest_rounds_to_six_places():
    assert rows_digest([(0.1 + 0.2,)]) == rows_digest([(Decimal("0.3"),)])
    assert rows_digest([(0.31,)]) != rows_digest([(Decimal("0.3"),)])


def _results(**records):
    return {"polars": records, "pandas": dict(records), "datafusion": dict(records)}


def _record(status="match", parity="match", state="strict", nonempty=True, gate_status="pass", classes=None):
    return {
        "event": "query_result",
        "status": status,
        "dataframe_to_sql": {"status": parity},
        "printed_classification": {"state": state, "classes": classes or []},
        "sql_nonempty": nonempty,
        "gate": {"status": gate_status, "failures": [] if gate_status == "pass" else ["x"]},
    }


def test_summary_reports_parity_separately_from_printed_classification():
    results = _results(
        **{
            "1": _record(),
            "7": _record("mismatch", state="classified", classes=[DISPLAY_PRECISION]),
            "5": _record("mismatch", state="unclassified", gate_status="fail"),
            "3": _record(parity="mismatch", gate_status="fail"),
        }
    )
    report = summary.build_report(results)
    polars = report["engines"]["polars"]
    assert polars["parity_divergent"] == ["3"]
    assert polars["classified"] == ["7"] and polars["unclassified"] == ["5"]
    assert polars["classes"] == {DISPLAY_PRECISION: 1}
    assert polars["failed"] == ["3", "5"] and report["gate"] == "fail"
    text = summary.render(report)
    assert "### DataFrame-to-SQL parity" in text and "### Printed-answer comparison" in text
    assert text.index("DataFrame-to-SQL parity") < text.index("Printed-answer comparison")


def test_summary_passes_with_only_classified_differences_and_reports_required_coverage():
    records = {query: _record() for query in REQUIRED_NONEMPTY}
    records["7"] = _record("mismatch", state="classified", classes=[DISPLAY_PRECISION])
    report = summary.build_report(_results(**records))
    assert report["gate"] == "pass"
    assert report["engines"]["pandas"]["required_compared"] == 26
    records["3"] = _record(nonempty=False, gate_status="fail")
    report = summary.build_report(_results(**records))
    assert report["engines"]["pandas"]["required_empty"] == ["3"] and report["gate"] == "fail"


def test_summary_fails_when_an_engine_produced_no_results(tmp_path):
    (tmp_path / "polars.jsonl").write_text(json.dumps({"event": "query_result", "query": "1", **_record()}) + "\n")
    report = summary.build_report(summary.load_results(tmp_path))
    assert report["gate"] == "fail"
    assert report["engines"]["pandas"]["statements"] == 0


def _worker_job(tmp_path, monkeypatch, official_rows, store_returns):
    import polars as pl

    from benchbox.core.equivalence.builders import base
    from benchbox.core.tpcds.c_tools import DSQGenBinary
    from benchbox.core.tpcds.schema.registry import TABLES
    from benchbox.core.tpcds.schema.tables import DBGEN_VERSION
    from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter
    from benchbox.tpcds import TPCDS

    values = runner.qualification_parameters()["values"]["1"]
    benchmark = TPCDS(scale_factor=1.0)
    raw = DSQGenBinary().generate_with_parameters(1, values)
    sql = benchmark._impl.translate_query_text(raw, "netezza", "duckdb")
    sql = benchmark._impl._apply_target_dialect_overrides(1, sql, "duckdb")
    tables = {
        "date_dim": pl.DataFrame({"d_date_sk": [1], "d_year": [2000]}),
        "customer": pl.DataFrame({"c_customer_sk": [1, 2], "c_customer_id": ["C1", "C2"]}),
        "store": pl.DataFrame({"s_store_sk": [1], "s_state": ["TN"]}),
        "store_returns": pl.DataFrame(
            store_returns,
            schema={
                "sr_customer_sk": pl.Int64,
                "sr_store_sk": pl.Int64,
                "sr_returned_date_sk": pl.Int64,
                "sr_return_amt": pl.Float64,
            },
        ),
    }
    context = PolarsDataFrameAdapter().create_context()
    for name, frame in tables.items():
        context.register_table(name, frame.lazy())
    binary = tmp_path / "fixture-input"
    binary.write_text("fixture", encoding="utf-8")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    path = data_dir / "customer.dat"
    path.write_text("customer", encoding="utf-8")
    job = tmp_path / "inputs.json"
    answer = {
        "file": "1.ans",
        "sha256": runner.sha256(binary),
        "null_order": "unspecified",
        "null_tokens": [],
        "columns": ["C_CUSTOMER_ID"],
        "rows": official_rows,
    }
    job.write_text(
        json.dumps(
            {
                "binaries": {"dsqgen": {"path": str(binary), "sha256": runner.sha256(binary)}},
                "parameters": {"values": {"1": values}},
                "sql": {"1": sql},
                "answers": {"1": [answer]},
            }
        ),
        encoding="utf-8",
    )

    class FixtureBenchmark:
        def __init__(self, **kwargs):
            self.tables = dict.fromkeys((table.name for table in TABLES if table.name != DBGEN_VERSION.name), [path])

        def generate_data(self):
            return [path]

    def loaded(*args, **kwargs):
        connection = duckdb.connect()
        for name, frame in tables.items():
            connection.register(name, frame.to_arrow())
        return connection

    monkeypatch.setattr("benchbox.tpcds.TPCDS", FixtureBenchmark)
    monkeypatch.setattr(base, "_load_duckdb_cell", loaded)
    monkeypatch.setattr(PolarsDataFrameAdapter, "load_benchmark_into_context", lambda *args, **kwargs: context)
    monkeypatch.setattr(runner, "STATEMENTS", ("1",))
    return job, data_dir


_RETURNS = {
    "sr_customer_sk": [1, 2],
    "sr_store_sk": [1, 1],
    "sr_returned_date_sk": [1, 1],
    "sr_return_amt": [100.0, 10.0],
}


def _last_result(events):
    return json.loads(events.read_text(encoding="utf-8").splitlines()[-1])


def test_worker_fails_a_new_printed_mismatch_and_passes_the_same_mismatch_once_classified(tmp_path, monkeypatch):
    job, data_dir = _worker_job(tmp_path, monkeypatch, [["wrong-customer"]], _RETURNS)
    events = tmp_path / "new.jsonl"
    assert runner.worker(job, "polars", events, data_dir) == 1
    result = _last_result(events)
    assert result["status"] == "mismatch"
    assert result["printed_classification"] == {"state": "unclassified"}
    assert result["gate"]["failures"] == ["unclassified_printed_mismatch"]

    entry = KnownDifference("1", (RETURNS_DIFFERENCE,), "e", result_digest=rows_digest([("C1",)]))
    monkeypatch.setitem(runner.KNOWN, "1", entry)
    classified = tmp_path / "classified.jsonl"
    assert runner.worker(job, "polars", classified, data_dir) == 0
    result = _last_result(classified)
    assert result["status"] == "mismatch" and result["gate"] == {"status": "pass", "failures": []}
    assert result["printed_classification"]["state"] == "classified"
    assert result["official_files"][0]["sql_classification"] == {"class": RETURNS_DIFFERENCE}

    stale = dataclasses.replace(entry, result_digest=rows_digest([("C2",)]))
    monkeypatch.setitem(runner.KNOWN, "1", stale)
    assert runner.worker(job, "polars", tmp_path / "stale.jsonl", data_dir) == 1


def test_worker_keeps_the_strict_status_when_a_difference_is_classified(tmp_path, monkeypatch):
    job, data_dir = _worker_job(tmp_path, monkeypatch, [["wrong-customer"]], _RETURNS)
    entry = KnownDifference("1", (RETURNS_DIFFERENCE,), "e", result_digest=rows_digest([("C1",)]))
    monkeypatch.setitem(runner.KNOWN, "1", entry)
    events = tmp_path / "events.jsonl"
    runner.worker(job, "polars", events, data_dir)
    result = _last_result(events)
    assert result["official_files"][0]["sql_to_printed"]["status"] == "mismatch"
    assert result["official_files"][0]["dataframe_to_printed"]["status"] == "mismatch"
    assert result["status"] == "mismatch"


def test_worker_fails_a_required_statement_with_an_empty_result(tmp_path, monkeypatch):
    empty = {key: [] for key in _RETURNS}
    job, data_dir = _worker_job(tmp_path, monkeypatch, [], empty)
    prepared = json.loads(job.read_text(encoding="utf-8"))
    prepared["answers"]["1"][0]["rows"] = []
    job.write_text(json.dumps(prepared), encoding="utf-8")
    events = tmp_path / "events.jsonl"
    assert runner.worker(job, "polars", events, data_dir) == 0
    assert _last_result(events)["sql_nonempty"] is False
    monkeypatch.setattr(classification, "REQUIRED_NONEMPTY", frozenset({"1"}))
    required = tmp_path / "required.jsonl"
    assert runner.worker(job, "polars", required, data_dir) == 1
    assert _last_result(required)["gate"]["failures"] == ["empty_at_sf1"]
