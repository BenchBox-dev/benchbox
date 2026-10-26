from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from _project.scripts.tpchavoc_presto_variant_check import (
    PrestoClient,
    compare_results,
    has_top_level_order_by,
    parse_session,
    run_statements,
    variant_ids,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REFERENCE = [["A", 1, 10.0], ["B", 2, 20.0]]


def test_identical_results_pass_in_both_modes():
    assert compare_results(REFERENCE, [list(row) for row in REFERENCE], ordered=True)["status"] == "PASS"
    assert compare_results(REFERENCE, [list(row) for row in REFERENCE], ordered=False)["status"] == "PASS"


def test_integer_cells_are_compared_exactly():
    assert compare_results([["A", 150_000_000]], [["A", 149_999_999]], ordered=True)["status"] == "MISMATCH"


def test_float_cells_use_relative_tolerance():
    close = [["A", 1, 10.0 * (1 + 1e-12)], ["B", 2, 20.0]]
    far = [["A", 1, 10.0 * (1 + 1e-6)], ["B", 2, 20.0]]

    assert compare_results(REFERENCE, close, ordered=True)["status"] == "PASS"
    assert compare_results(REFERENCE, far, ordered=True)["status"] == "MISMATCH"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "NaN", "Infinity"])
def test_non_finite_cells_are_rejected(bad):
    outcome = compare_results([["A", 1.0]], [["A", bad]], ordered=True)

    assert outcome["status"] == "MISMATCH"
    assert outcome["max_relative_difference"] == math.inf


@pytest.mark.parametrize(
    "candidate",
    [[["A", 1, 10.0]], [["A", 1, 10.0], ["B", 2, 20.0], ["C", 3, 30.0]], [["A", 1], ["B", 2]]],
    ids=["missing-row", "extra-row", "missing-column"],
)
def test_structural_mismatch_is_reported(candidate):
    assert compare_results(REFERENCE, candidate, ordered=True)["status"] == "MISMATCH"


def test_row_order_is_ignored_only_when_the_query_has_no_order_by():
    shuffled = [list(REFERENCE[1]), list(REFERENCE[0])]

    assert compare_results(REFERENCE, shuffled, ordered=False)["status"] == "PASS"
    assert compare_results(REFERENCE, shuffled, ordered=True)["status"] == "ORDER_DIFFERS"


def test_top_level_order_by_detection_ignores_window_and_subquery_clauses():
    assert has_top_level_order_by("select a from t order by a")
    assert not has_top_level_order_by("select sum(a) over (order by b) from t")
    assert not has_top_level_order_by("select a from (select a from t order by a) x")
    assert not has_top_level_order_by("select 'order by' as s from t")


def test_variant_set_has_ten_variants_for_each_of_the_22_queries():
    identifiers = variant_ids()

    assert len(identifiers) == 220
    assert len(set(identifiers)) == 220
    assert "1_v7" in identifiers
    assert "16_v6" in identifiers


def test_parse_session_splits_name_and_value():
    assert parse_session(["a=1", "b=x=y"]) == {"a": "1", "b": "x=y"}
    assert parse_session(None) == {}


@pytest.mark.parametrize("bad", ["novalue", "=1"])
def test_parse_session_rejects_malformed_items(bad):
    with pytest.raises(ValueError):
        parse_session([bad])


def test_session_properties_are_sent_as_header(monkeypatch):
    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"{}"

    def fake_urlopen(request, timeout):
        seen.update(request.headers)
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    PrestoClient("http://x", "u", "tpch", "tiny", {"p": "1", "q": "2"}).info()

    assert seen["X-presto-session"] == "p=1,q=2"


def test_run_statements_reports_success_and_failure():
    class Stub:
        def execute(self, sql, schema=None):
            if "bad" in sql:
                return {"ok": False, "error_name": "NOT_SUPPORTED", "error_message": "nope", "wall_seconds": 0.0}
            return {"ok": True, "rows": [[1], [2]], "wall_seconds": 0.0}

    good, bad = run_statements(Stub(), ["SELECT 1;", "SELECT bad"])

    assert good["ok"] and good["row_count"] == 2 and good["rows_head"] == [[1], [2]]
    assert not bad["ok"] and bad["error_name"] == "NOT_SUPPORTED" and bad["error_message"] == "nope"
