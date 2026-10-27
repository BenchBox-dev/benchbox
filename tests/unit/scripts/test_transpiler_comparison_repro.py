from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture(scope="module")
def module():
    path = Path(__file__).resolve().parents[3] / "_project/sqlglot-upstream/repros/transpiler_comparison.py"
    if not path.is_file():
        pytest.skip("development-only comparison harness is absent from the release tree")
    spec = importlib.util.spec_from_file_location("transpiler_comparison_repro", path)
    assert spec and spec.loader
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def _case(module, case_id):
    return next(case for case in module.CASES if case["id"] == case_id)


def test_outcomes_are_classified_separately(module):
    connection = module._fixture_sqlite()
    case = _case(module, "good-limit-offset")

    def passthrough(sql, source, target):
        return sql

    def broken(sql, source, target):
        raise ValueError("unsupported")

    def wrong(sql, source, target):
        return "SELECT id FROM t ORDER BY id LIMIT 1"

    def bad_sql(sql, source, target):
        return "SELECT nope FROM missing"

    assert module._run_case(passthrough, case, "sqlite", connection)["outcome"] == "match"
    assert module._run_case(broken, case, "sqlite", connection)["outcome"] == "translate_error"
    assert module._run_case(wrong, case, "sqlite", connection)["outcome"] == "wrong_result"
    assert module._run_case(bad_sql, case, "sqlite", connection)["outcome"] == "exec_error"


def test_fixture_expectations_hold_in_sqlite(module):
    connection = module._fixture_sqlite()
    assert connection.execute("SELECT COUNT(*) FROM t WHERE d >= '2000-01-01'").fetchall() == [(3,)]
    assert isinstance(connection, sqlite3.Connection)


def test_summary_reports_repairs_and_regressions(module, tmp_path):
    def report(engine, outcomes):
        results = [
            {"case": f"c{index}", "target": "sqlite", "outcome": outcome} for index, outcome in enumerate(outcomes)
        ]
        counts = {}
        for outcome in outcomes:
            counts[outcome] = counts.get(outcome, 0) + 1
        return {"info": {"engine": engine, "version": "x"}, "counts": counts, "results": results}

    import json

    paths = []
    for engine, outcomes in (("sqlglot", ["match", "exec_error"]), ("polyglot", ["wrong_result", "match"])):
        path = tmp_path / f"{engine}.json"
        path.write_text(json.dumps(report(engine, outcomes)))
        paths.append(str(path))
    summary = module.summarize(paths)
    assert "repair: c1->sqlite (exec_error)" in summary
    assert "regression: c0->sqlite (wrong_result)" in summary
