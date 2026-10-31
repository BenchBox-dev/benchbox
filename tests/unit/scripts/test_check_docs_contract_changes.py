from __future__ import annotations

import copy
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts import check_docs_contract_changes as check

pytestmark = [pytest.mark.unit, pytest.mark.fast]

BASE: dict[str, Any] = {
    "package": {"version": "1.0.0", "optional_extras": ["duckdb"]},
    "benchmarks": {"ids": ["tpch", "tpcds"], "registry_summary": {"total": 2}},
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
    "execution": {"deprecated_option_aliases": [{"platform": "duckdb", "key": "old", "target": "new"}]},
}


def _head() -> dict[str, Any]:
    return copy.deepcopy(BASE)


def _diff(head: dict[str, Any]) -> list[check.Change]:
    return check.diff_entries(check.contract_entries(BASE), check.contract_entries(head))


def test_unchanged_contract_reports_nothing() -> None:
    assert _diff(_head()) == []


def test_release_version_bump_is_not_a_change() -> None:
    head = _head()
    head["package"]["version"] = "1.1.0"
    assert _diff(head) == []


def test_added_mcp_parameter_is_reported() -> None:
    head = _head()
    head["mcp"]["tools"]["get_query_plan"]["params"]["stream"] = {"type": "integer", "required": False, "default": 0}
    assert _diff(head) == [
        check.Change("added", "contract.mcp.tools.get_query_plan.params.stream", "new in this pull request")
    ]


def test_removed_cli_option_is_reported() -> None:
    head = _head()
    head["cli"]["commands"][1]["options"].pop()
    assert _diff(head) == [
        check.Change("removed", "contract.cli.commands.benchbox run.options.--quiet", "no longer exported")
    ]


def test_changed_fields_are_reported_with_before_and_after() -> None:
    head = _head()
    head["mcp"]["tools"]["get_query_plan"]["params"]["limit"]["default"] = 20
    head["platform_options"]["platforms"]["duckdb"]["threads"]["choices"] = ["1", "2"]
    head["cli"]["commands"][1]["options"][0]["hidden"] = True
    assert _diff(head) == [
        check.Change("changed", "contract.cli.commands.benchbox run.options.--platform/-p", "hidden: false -> true"),
        check.Change("changed", "contract.mcp.tools.get_query_plan.params.limit", "default: 10 -> 20"),
        check.Change("changed", "contract.platform_options.platforms.duckdb.threads", 'choices: null -> ["1", "2"]'),
    ]


def test_list_fields_report_added_and_removed_members() -> None:
    head = _head()
    head["benchmarks"]["ids"] = ["tpch", "ssb"]
    head["benchmarks"]["registry_summary"]["total"] = 2
    head["mcp"]["remote_only_tools"] = ["remote_status", "remote_audit"]
    assert _diff(head) == [
        check.Change("changed", "contract.benchmarks", 'ids: +"ssb" -"tpcds"'),
        check.Change("changed", "contract.mcp", 'remote_only_tools: +"remote_audit"'),
    ]


def test_counts_and_composite_identities_are_covered() -> None:
    head = _head()
    head["benchmarks"]["registry_summary"]["total"] = 3
    head["execution"]["deprecated_option_aliases"].append({"platform": "duckdb", "key": "older", "target": "new"})
    assert _diff(head) == [
        check.Change("added", "contract.execution.deprecated_option_aliases.duckdb older", "new in this pull request"),
        check.Change("changed", "contract.benchmarks.registry_summary", "total: 2 -> 3"),
    ]


def test_added_and_removed_tools_and_commands() -> None:
    head = _head()
    del head["mcp"]["tools"]["list_platforms"]
    head["cli"]["commands"].append({"path": "benchbox export", "is_group": False, "options": []})
    assert _diff(head) == [
        check.Change("added", "contract.cli.commands.benchbox export", "new in this pull request"),
        check.Change("removed", "contract.mcp.tools.list_platforms.params", "no longer exported"),
    ]


def test_long_details_are_clipped() -> None:
    head = _head()
    head["benchmarks"]["ids"] = [f"b{n}" for n in range(200)]
    [change] = _diff(head)
    assert len(change.detail) == check.DETAIL_LIMIT
    assert change.detail.endswith("...")


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
    assert not check.is_relevant(["uv.lock", "tests/unit/test_x.py"])


def _git_repo(root: Path, exporter_body: str) -> str:
    def git(*args: str) -> str:
        done = subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.test", *args],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return done.stdout.strip()

    git("init", "-q")
    (root / "scripts").mkdir()
    (root / "scripts" / "doc_contracts.py").write_text(exporter_body, encoding="utf-8")
    git("add", "scripts/doc_contracts.py")
    git("commit", "-q", "-m", "base")
    return git("rev-parse", "HEAD")


def _exporter(tools: dict[str, Any]) -> str:
    return (
        "import argparse, json\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--output')\n"
        "args = parser.parse_args()\n"
        f"open(args.output, 'w', encoding='utf-8').write(json.dumps({{'mcp': {{'tools': {tools!r}}}}}))\n"
    )


def test_compare_exports_base_in_a_worktree_and_head_in_place(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    base_tools = {"t": {"params": {"a": {"type": "string", "required": True, "default": None}}}}
    base_sha = _git_repo(tmp_path, _exporter(base_tools))
    head_tools = {"t": {"params": {"b": {"type": "string", "required": True, "default": None}}}}
    (tmp_path / "scripts" / "doc_contracts.py").write_text(_exporter(head_tools), encoding="utf-8")
    monkeypatch.setattr(check, "REPO_ROOT", tmp_path)
    assert check.compare(base_sha) == [
        check.Change("added", "contract.mcp.tools.t.params.b", "new in this pull request"),
        check.Change("removed", "contract.mcp.tools.t.params.a", "no longer exported"),
    ]
    listing = subprocess.run(
        ["git", "worktree", "list"], cwd=tmp_path, check=True, capture_output=True, text=True, encoding="utf-8"
    )
    assert listing.stdout.count("\n") == 1


def test_compare_reports_missing_base_commit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _git_repo(tmp_path, _exporter({}))
    monkeypatch.setattr(check, "REPO_ROOT", tmp_path)
    with pytest.raises(RuntimeError, match="not present"):
        check.compare("0" * 40)


def test_main_turns_unexpected_errors_into_a_notice(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(check, "changed_paths", lambda _base: ["benchbox/x.py"])

    def drift(_base: str) -> list[check.Change]:
        raise KeyError("path")

    monkeypatch.setattr(check, "compare", drift)
    assert check.main(["--base-sha", "abc"]) == 0
    assert capsys.readouterr().out.startswith("::notice title=Docs contract comparison skipped::KeyError")


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
    assert "contract.mcp.tools.get_query_plan.params.stream" in summary.read_text(encoding="utf-8")


def test_main_is_silent_when_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub_compare(monkeypatch, _head())
    summary = tmp_path / "summary.md"
    assert check.main(["--base-sha", "abc", "--summary", str(summary)]) == 0
    assert "::warning" not in capsys.readouterr().out
    assert not summary.exists()


def test_main_skips_irrelevant_changes(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(check, "changed_paths", lambda _base: ["uv.lock"])

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
