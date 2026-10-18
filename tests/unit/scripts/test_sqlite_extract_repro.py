from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from benchbox._vendor import sqlglot as bundled_sqlglot

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.mark.parametrize(
    ("translated", "expected", "exit_code", "has_error"),
    [
        ("SELECT 2020", [(2020,)], 0, False),
        ("SELECT 2019", [(2020,)], 1, False),
        ("SELECT NULL", [(0,)], 1, False),
        ("SELECT ' x'", [("x",)], 1, False),
        ("SELECT 1 UNION ALL SELECT 1", [(1,)], 1, False),
        ("SELECT 2 UNION ALL SELECT 1", [(1,), (2,)], 1, False),
        ("SELECT missing_function()", [(2020,)], 1, True),
    ],
)
def test_execution_report(monkeypatch, capsys, translated, expected, exit_code, has_error):
    path = Path(__file__).resolve().parents[3] / "_project/sqlglot-upstream/repros/sqlite_extract.py"
    spec = importlib.util.spec_from_file_location("sqlite_extract_repro", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "sqlglot", bundled_sqlglot)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "cases", lambda: [("witness", "postgres", "SELECT source", expected)])
    monkeypatch.setattr(module, "translated_trap_cases", list)

    def transpile(sql, *, read, write):
        assert (sql, read, write) == ("SELECT source", "postgres", "sqlite")
        return [translated]

    monkeypatch.setattr(module.sqlglot, "transpile", transpile)
    assert module.main() == exit_code
    report = json.loads(capsys.readouterr().out)
    assert report["total"] == 1
    assert report["passed"] == int(exit_code == 0)
    assert ("error" in report["cases"][0]) == has_error
    assert len(report["naive_lowering_counterexamples"]) == 2
    assert report["translated_counterexamples"] == []


@pytest.mark.parametrize(
    ("trap_translated", "trap_expected", "exit_code"),
    [
        ("SELECT 2020", [(2020,)], 0),
        ("SELECT 2019", [(2020,)], 1),
    ],
)
def test_translated_counterexamples_gate_verdict(monkeypatch, capsys, trap_translated, trap_expected, exit_code):

    path = Path(__file__).resolve().parents[3] / "_project/sqlglot-upstream/repros/sqlite_extract.py"
    spec = importlib.util.spec_from_file_location("sqlite_extract_repro_traps", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "sqlglot", bundled_sqlglot)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "cases", lambda: [("witness", "postgres", "SELECT source", [(2020,)])])
    monkeypatch.setattr(module, "translated_trap_cases", lambda: [("trap", "postgres", "SELECT trap", trap_expected)])

    def transpile(sql, *, read, write):
        if (sql, read, write) == ("SELECT source", "postgres", "sqlite"):
            return ["SELECT 2020"]
        assert (sql, read, write) == ("SELECT trap", "postgres", "sqlite")
        return [trap_translated]

    monkeypatch.setattr(module.sqlglot, "transpile", transpile)
    assert module.main() == exit_code
    report = json.loads(capsys.readouterr().out)
    assert len(report["translated_counterexamples"]) == 1
    assert bool(report["translated_counterexamples"][0]["matched"]) == (exit_code == 0)
