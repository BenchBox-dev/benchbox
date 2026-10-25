from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts import ci_units, coverage_test_selection as sel, heavy_tier_needed as heavy, path_filter_decision

pytestmark = [pytest.mark.unit, pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parents[3]
REPLAYS = REPO_ROOT / "tests" / "fixtures" / "ci" / "selection_replays"
CASES = (3018, 3066, 3122, 3185)
COVERED_BY_MAP = (3066, 3122, 3185)


def _load(case: int) -> tuple[dict[str, Any], list[sel.ChangedFile]]:
    meta = json.loads((REPLAYS / f"{case}.json").read_text(encoding="utf-8"))
    files = sel.parse_diff("\n".join(meta["diff"]))
    return meta, files


def _git(repo: Path, *args: str) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    proc = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True, env=env)
    return proc.stdout.strip()


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
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


def _commit_stubs(repo: Path, nodes: list[str]) -> str:
    for node in nodes:
        path = repo / node.split("::", 1)[0]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n", encoding="utf-8")
    (repo / "README.txt").write_text("stub\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "stubs")
    return _git(repo, "rev-parse", "HEAD")


def _map_from_replay(meta: dict[str, Any], sha: str) -> dict[str, Any]:
    nodes = sorted(
        {
            *meta["failed_in_base_map"],
            *(node for entry in meta["hits"].values() for hit in entry["lines"].values() for node in hit),
        }
    )
    position = {node: index for index, node in enumerate(nodes)}
    files: dict[str, Any] = {
        path: {
            "lines": {line: sorted(position[node] for node in hit) for line, hit in entry["lines"].items()},
            "statements": entry["statements"],
        }
        for path, entry in meta["hits"].items()
    }
    return {"version": sel.MAP_VERSION, "sha": sha, "nodes": nodes, "files": files, "failed_nodes": []}


def _decision(files: list[sel.ChangedFile]) -> dict[str, Any]:
    rules = path_filter_decision.load_rules(REPO_ROOT / ".github" / "path-filters.yml")
    return path_filter_decision.classify_paths([entry.path for entry in files], rules)


def _select(repo: Path, case: int, monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, Any], dict[str, Any]]:
    meta, files = _load(case)
    sha = _commit_stubs(repo, meta["failed"])
    monkeypatch.setattr(sel, "SELECTION_SHADOW", False)
    mapping = _map_from_replay(meta, sha) if meta["hits"] else None
    result = sel.select_tests(repo, mapping, files, sha, decision=_decision(files))
    return meta, result


@pytest.mark.parametrize("case", CASES)
def test_replayed_pull_request_runs_the_fast_tier_and_the_code_lane(case: int) -> None:
    _, files = _load(case)
    paths = [entry.path for entry in files]
    units = ci_units.classify_units(paths, ci_units.load_unit_rules(ci_units.DEFAULT_RULES))
    assert units["units"]["core"] is True
    assert units["code_tests_needed"] is True
    assert _decision(files)["needs_code_ci"] is True


@pytest.mark.parametrize("case", COVERED_BY_MAP)
def test_replay_fixture_names_the_coverage_run_it_came_from(case: int) -> None:
    meta, _ = _load(case)
    provenance = meta["provenance"]
    assert len(provenance["full_map_sha256"]) == 64
    assert provenance["test_file"] == meta["failed"][0].split("::", 1)[0]
    assert provenance["merge_sha"] in {
        "b7e364ad8f6cfd7e0147c1783422f92bba763351",
        "dc5a628847c41ddc7289796f76139c21de71e2f3",
        "ef67c6c118894659748ab425ad4e9ce0453cb882",
    }
    assert "--cov-context=test" in provenance["command"]


@pytest.mark.parametrize("case", COVERED_BY_MAP)
def test_replayed_pull_request_selects_the_test_that_failed_on_trunk(
    case: int, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    meta, result = _select(repo, case, monkeypatch)
    assert meta["failed"] == meta["failed_in_base_map"]
    assert set(meta["failed"]) <= set(result["coverage_tests"])
    assert sel.shadow_misses(result["selected"], meta["failed"]) == []


def test_replayed_dryrun_change_is_selected_without_escalating(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, result = _select(repo, 3122, monkeypatch)
    assert result["escalate"] is False
    assert result["uncovered_sources"] == []


def test_replayed_stylesheet_change_forces_the_full_tier(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    meta, result = _select(repo, 3018, monkeypatch)
    assert result["escalate"] is True
    assert "non-Python changes need the full tier" in result["reason"]
    assert "results-explorer/src/index.css" in result["reason"]
    assert result["selected"] == []
    assert sel.shadow_misses(result["selected"], meta["failed"]) == meta["failed"]


@pytest.mark.parametrize("case", (3018, 3066, 3185))
def test_replayed_escalation_sends_the_pull_request_to_the_heavy_tier(
    case: int, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, result = _select(repo, case, monkeypatch)
    _, files = _load(case)
    verdict = heavy.heavy_needed(_decision(files), "HEAD", repo, selection=result)
    assert result["escalate"] is True
    assert verdict["heavy_needed"] is True
    assert "coverage selection escalated" in verdict["reason"]


def _junit_for(path: Path, nodes: list[str]) -> None:
    rows = ['<?xml version="1.0" encoding="utf-8"?>', '<testsuites><testsuite name="pytest">']
    for node in nodes:
        filename, *groups, name = node.split("::")
        classname = ".".join([filename[:-3].replace("/", "."), *groups])
        rows.append(
            f'<testcase classname="{classname}" name="{name}" file="{filename}" time="0.1">'
            '<failure message="boom">trace</failure></testcase>'
        )
    rows.append("</testsuite></testsuites>")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


@pytest.mark.parametrize("shadow", (False, True))
@pytest.mark.parametrize("case", CASES)
def test_miss_report_lists_the_replayed_failures_as_caught(
    case: int, shadow: bool, repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    meta, _ = _load(case)
    _, files = _load(case)
    sha = _commit_stubs(repo, meta["failed"])
    monkeypatch.setattr(sel, "SELECTION_SHADOW", shadow)
    mapping = _map_from_replay(meta, sha) if meta["hits"] else None
    result = sel.select_tests(repo, mapping, files, sha, decision=_decision(files))
    selection = tmp_path / "coverage-selection.json"
    selection.write_text(json.dumps(result), encoding="utf-8")
    junit = tmp_path / "shard-0-junit.xml"
    _junit_for(junit, meta["failed"])
    payload = tmp_path / "misses.json"
    summary = tmp_path / "summary.md"
    args = ["misses", "--selection-in", str(selection), "--junit", str(junit), "--expected-shards", "1"]
    args += ["--json-out", str(payload), "--summary", str(summary)]
    assert sel.main(args) == 0
    report = json.loads(payload.read_text(encoding="utf-8"))
    assert report["available"] is True
    assert report["failed"] == sorted(meta["failed"])
    assert report["missed"] == []
    assert report["would_escalate"] is False
    assert "No misses" in summary.read_text(encoding="utf-8")
