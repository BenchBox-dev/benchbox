from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from scripts import check_docs_contract_changes as check

pytestmark = [pytest.mark.unit, pytest.mark.fast]

BASE: dict[str, Any] = {
    "mcp": {
        "tools": {
            "get_query_plan": {
                "params": {
                    "benchmark": {"type": "string", "required": True, "default": None},
                    "limit": {"type": "integer", "required": False, "default": 10},
                }
            },
            "list_platforms": {"params": {}},
        },
        "remote_only_tools": ["remote_status"],
    },
    "cli": {
        "commands": [
            {"path": "benchbox", "is_group": True, "options": []},
            {
                "path": "benchbox run",
                "is_group": False,
                "options": [
                    {"names": ["--platform", "-p"], "is_flag": False, "choices": None, "hidden": False},
                    {"names": ["--quiet"], "is_flag": True, "choices": None, "hidden": False},
                ],
            },
        ]
    },
    "platform_options": {
        "platforms": {
            "duckdb": {"threads": {"value_kind": "integer", "choices": None, "default": 4, "aliases": []}},
        }
    },
}


def _head() -> dict[str, Any]:
    return copy.deepcopy(BASE)


def _diff(head: dict[str, Any]) -> list[check.Change]:
    return check.diff_entries(check.contract_entries(BASE), check.contract_entries(head))


def test_unchanged_contract_reports_nothing() -> None:
    assert _diff(_head()) == []


def test_unchecked_fields_are_not_changes() -> None:
    head = _head()
    head["cli"]["commands"][1]["options"][0]["hidden"] = True
    head["mcp"]["run_surface"] = {"anything": 1}
    assert _diff(head) == []


def test_added_mcp_parameter_is_reported() -> None:
    head = _head()
    head["mcp"]["tools"]["get_query_plan"]["params"]["stream"] = {"type": "integer", "required": False, "default": 0}
    assert _diff(head) == [
        check.Change("added", "mcp tool get_query_plan parameter stream", "new in this pull request")
    ]


def test_removed_cli_option_is_reported() -> None:
    head = _head()
    head["cli"]["commands"][1]["options"].pop()
    assert _diff(head) == [check.Change("removed", "cli option benchbox run --quiet", "no longer exported")]


def test_changed_fields_are_reported_with_before_and_after() -> None:
    head = _head()
    head["mcp"]["tools"]["get_query_plan"]["params"]["limit"]["default"] = 20
    head["platform_options"]["platforms"]["duckdb"]["threads"]["choices"] = ["1", "2"]
    assert _diff(head) == [
        check.Change("changed", "mcp tool get_query_plan parameter limit", "default: 10 -> 20"),
        check.Change("changed", "platform option duckdb threads", 'choices: null -> ["1", "2"]'),
    ]


def test_added_and_removed_tools_and_commands() -> None:
    head = _head()
    del head["mcp"]["tools"]["list_platforms"]
    head["mcp"]["remote_only_tools"].append("remote_audit")
    head["cli"]["commands"].append({"path": "benchbox export", "is_group": False, "options": []})
    assert _diff(head) == [
        check.Change("added", "cli command benchbox export", "new in this pull request"),
        check.Change("added", "mcp remote-only tool remote_audit", "new in this pull request"),
        check.Change("removed", "mcp tool list_platforms", "no longer exported"),
    ]


def test_annotations_one_per_entry_and_escaped() -> None:
    lines = check.render_annotations([check.Change("added", "mcp tool x parameter y", "a,b\nc")])
    assert lines == [
        "::warning title=Docs contract added::mcp tool x parameter y (a,b%0Ac). The site documentation must cover it."
    ]


def test_summary_lists_every_entry_and_names_the_site() -> None:
    changes = [check.Change("added", "a", "d1"), check.Change("removed", "b", "d2")]
    summary = check.render_summary(changes)
    assert "- **added** `a`: d1" in summary
    assert "- **removed** `b`: d2" in summary
    assert "benchbox-site" in summary


def test_relevance_filter() -> None:
    assert check.is_relevant(["README.md", "benchbox/mcp/tools.py"])
    assert check.is_relevant(["scripts/doc_contracts.py"])
    assert not check.is_relevant(["docs/usage.md", "tests/unit/test_x.py"])


def _stub_compare(monkeypatch: pytest.MonkeyPatch, head: dict[str, Any]) -> None:
    monkeypatch.setattr(check, "changed_paths", lambda _base: ["benchbox/mcp/tools.py"])
    monkeypatch.setattr(
        check,
        "compare",
        lambda _base: check.diff_entries(check.contract_entries(BASE), check.contract_entries(head)),
    )


def test_main_warns_and_exits_zero_on_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    head = _head()
    head["mcp"]["tools"]["get_query_plan"]["params"]["stream"] = {"type": "integer", "required": False, "default": 0}
    _stub_compare(monkeypatch, head)
    summary = tmp_path / "summary.md"
    assert check.main(["--base-sha", "abc", "--summary", str(summary)]) == 0
    assert capsys.readouterr().out.count("::warning ") == 1
    assert "mcp tool get_query_plan parameter stream" in summary.read_text(encoding="utf-8")


def test_main_is_silent_when_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub_compare(monkeypatch, _head())
    summary = tmp_path / "summary.md"
    assert check.main(["--base-sha", "abc", "--summary", str(summary)]) == 0
    assert "::warning" not in capsys.readouterr().out
    assert not summary.exists()


def test_main_skips_irrelevant_changes(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(check, "changed_paths", lambda _base: ["docs/usage.md"])

    def boom(_base: str) -> list[check.Change]:
        raise AssertionError("export must not run")

    monkeypatch.setattr(check, "compare", boom)
    assert check.main(["--base-sha", "abc"]) == 0
    assert "skipped" in capsys.readouterr().out


def test_main_notices_and_exits_zero_when_base_export_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(check, "changed_paths", lambda _base: ["benchbox/x.py"])

    def fail(_base: str) -> list[check.Change]:
        raise RuntimeError("base commit abc is not present in the checkout")

    monkeypatch.setattr(check, "compare", fail)
    assert check.main(["--base-sha", "abc"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("::notice title=Docs contract comparison skipped::")
    assert "::warning" not in out


def test_main_without_base_sha_exits_zero(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("EVENT_BASE_SHA", raising=False)
    assert check.main([]) == 0
    assert "no base commit" in capsys.readouterr().out
