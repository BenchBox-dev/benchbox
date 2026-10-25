from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts import ci_units, coverage_test_selection as sel, heavy_tier_needed as heavy, path_filter_decision as pfd

pytestmark = [pytest.mark.unit, pytest.mark.medium]

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "selection_incidents"
COVERED_INCIDENTS = (3066, 3122, 3185)


def _git(repo: Path, *args: str) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    proc = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True, env=env)
    return proc.stdout.strip()


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _incident(pr: int) -> dict[str, Any]:
    return json.loads((FIXTURES / f"pr{pr}.json").read_text(encoding="utf-8"))


def _init(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    for args in (
        ["init", "-b", "main"],
        ["config", "user.email", "test@example.com"],
        ["config", "user.name", "Test"],
        ["config", "commit.gpgsign", "false"],
    ):
        _git(root, *args)
    return root


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _map_for(incident: dict[str, Any], sha: str) -> dict[str, Any]:
    mapping = sel.read_map(FIXTURES / f"pr{incident['pr']}.map.json")
    assert mapping["sha"] == incident["provenance"]["base_sha"]
    assert incident["failed_test"] in mapping["nodes"]
    return {**mapping, "sha": sha}


def _incident_repo(tmp_path: Path, incident: dict[str, Any]) -> tuple[Path, str]:
    repo = _init(tmp_path)
    _write(repo, incident["test_file"], "def test_placeholder():\n    assert True\n")
    for path in sel.read_map(FIXTURES / f"pr{incident['pr']}.map.json")["files"]:
        _write(repo, path, "VALUE = 1\n")
    return repo, _commit(repo, "base")


def _decision(files: list[str]) -> dict[str, Any]:
    decision = pfd.classify_paths(files, pfd.load_rules(pfd.DEFAULT_RULES))
    decision["packaging_needed"] = bool(decision.get("packaging_needed"))
    return decision


def _write_junit(path: Path, failed_node: str) -> None:
    module, _, name = failed_node.partition("::")
    classname = module[: -len(".py")].replace("/", ".")
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest">'
        f'<testcase classname="{classname}" name="{name}" file="{module}">'
        '<failure message="regressed"/></testcase></testsuite></testsuites>',
        encoding="utf-8",
    )


def _miss_report(tmp_path: Path, repo: Path, result: dict[str, Any], failed_node: str) -> dict[str, Any]:
    selection = tmp_path / "coverage-selection.json"
    selection.write_text(json.dumps(result), encoding="utf-8")
    junit = tmp_path / "shard-0-junit.xml"
    _write_junit(junit, failed_node)
    out = tmp_path / "misses.json"
    argv = ["--repo-root", str(repo), "misses", "--selection-in", str(selection), "--junit", str(junit)]
    assert sel.main([*argv, "--json-out", str(out)]) == 0
    return json.loads(out.read_text(encoding="utf-8"))


@pytest.mark.parametrize("pr", COVERED_INCIDENTS)
def test_coverage_selection_picks_the_test_that_failed_on_trunk(
    pr: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sel, "SELECTION_SHADOW", False)
    incident = _incident(pr)
    repo, base = _incident_repo(tmp_path, incident)
    files = sel.parse_diff(incident["diff"])

    result = sel.select_tests(repo, _map_for(incident, base), files, base, decision=_decision([f.path for f in files]))

    assert incident["failed_test"] in result["coverage_tests"]
    assert incident["failed_test"] in result["selected"]


@pytest.mark.parametrize("pr", COVERED_INCIDENTS)
def test_shadow_miss_report_lists_the_trunk_failure_as_caught(
    pr: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sel, "SELECTION_SHADOW", False)
    incident = _incident(pr)
    repo, base = _incident_repo(tmp_path, incident)
    files = sel.parse_diff(incident["diff"])
    result = sel.select_tests(repo, _map_for(incident, base), files, base, decision=_decision([f.path for f in files]))

    report = _miss_report(tmp_path, repo, result, incident["failed_test"])

    assert report["available"] is True
    assert report["failed"] == [incident["failed_test"]]
    assert report["missed"] == []
    assert report["would_escalate"] is False


def test_stale_map_sends_the_dryrun_change_to_the_full_tier(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sel, "SELECTION_SHADOW", False)
    incident = _incident(3122)
    repo, map_sha = _incident_repo(tmp_path, incident)
    _write(repo, "benchbox/core/dryrun.py", "VALUE = 2\n")
    base = _commit(repo, "develop moved dryrun after the map")
    files = sel.parse_diff(incident["diff"])
    decision = _decision([f.path for f in files])

    result = sel.select_tests(repo, _map_for(incident, map_sha), files, base, decision=decision)

    assert result["escalate"] is True
    assert "benchbox/core/dryrun.py" in result["uncovered_sources"]
    verdict = heavy.heavy_needed(decision, base, repo, selection=result)
    assert verdict["heavy_needed"] is True
    assert "coverage selection escalated" in verdict["reason"]


@pytest.mark.parametrize("pr", COVERED_INCIDENTS)
def test_missing_map_sends_the_incident_to_the_full_tier(
    pr: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sel, "SELECTION_SHADOW", False)
    incident = _incident(pr)
    repo, base = _incident_repo(tmp_path, incident)
    files = sel.parse_diff(incident["diff"])
    decision = _decision([f.path for f in files])

    result = sel.select_tests(repo, None, files, base, decision=decision)

    assert result["escalate"] is True
    assert heavy.heavy_needed(decision, base, repo, selection=result)["heavy_needed"] is True


def test_site_asset_change_runs_the_fast_tier_and_escalates_the_medium_tier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sel, "SELECTION_SHADOW", False)
    incident = _incident(3018)
    repo = _init(tmp_path)
    _write(repo, incident["test_file"], "def test_placeholder():\n    assert True\n")
    base = _commit(repo, "base")
    files = sel.parse_diff(incident["diff"])
    paths = [f.path for f in files]
    decision = _decision(paths)

    assert decision["needs_code_ci"] is True
    units = ci_units.classify_units(paths, ci_units.load_unit_rules(ci_units.DEFAULT_RULES))
    assert units["code_tests_needed"] is True

    mapping = {"version": 2, "sha": base, "nodes": [], "files": {}, "failed_nodes": []}
    result = sel.select_tests(repo, mapping, files, base, decision=decision)

    assert result["escalate"] is True
    assert "non-Python changes need the full tier" in result["reason"]
    verdict = heavy.heavy_needed(decision, base, repo, selection=result)
    assert verdict["heavy_needed"] is True
    assert "coverage selection escalated" in verdict["reason"]


@pytest.mark.parametrize("pr", COVERED_INCIDENTS)
def test_recorded_map_hits_the_changed_base_lines_for_the_failing_test(pr: int) -> None:
    incident = _incident(pr)
    mapping = sel.read_map(FIXTURES / f"pr{pr}.map.json")
    index = mapping["nodes"].index(incident["failed_test"])
    hit = False
    for entry in sel.parse_diff(incident["diff"]):
        recorded = mapping["files"].get(entry.old_path)
        if recorded is None:
            continue
        probes = {line for start, count in entry.old_ranges for line in sel.probe_lines(start, count)}
        hit = hit or any(index in recorded["lines"].get(str(line), []) for line in probes)
    assert hit
    assert incident["provenance"]["full_map_sha256"]
    assert incident["provenance"]["merge_sha"]
