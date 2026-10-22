from __future__ import annotations

import json
import os
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from scripts import coverage_test_selection as sel, heavy_tier_needed as heavy

pytestmark = [pytest.mark.unit, pytest.mark.medium]


def _git(repo: Path, *args: str) -> None:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)


def _write(repo: Path, rel: str, text: str = "x = 1\n") -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _commit_all(repo: Path, message: str = "change") -> None:
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", message)


def _head(repo: Path) -> str:
    proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True, capture_output=True)
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
    _write(root, "benchbox/base.py", "VALUE = 1\n\n\ndef translate(value):\n    return value + 1\n")
    _write(root, "benchbox/core/engine.py", "x = 1\n")
    _write(root, "benchbox/core/__init__.py", "")
    _write(root, "scripts/tool.py", "x = 1\n")
    _write(
        root,
        "tests/test_benchbox.py",
        "from benchbox.base import translate\n\n\ndef test_translate():\n    assert translate(1) == 2\n",
    )
    _write(root, "tests/unit/core/test_engine.py", "x = 1\n")
    _write(root, "tests/unit/test_tool.py", "x = 1\n")
    _write(root, "tests/unit/test_unrelated.py", "y = 1\n")
    _write(root, "docs/guide.md", "# guide\n")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "init")
    _git(root, "update-ref", "refs/remotes/origin/develop", "HEAD")
    _git(root, "checkout", "-b", "feature")
    return root


_DATA_COUNTER = [0]


def _coverage_data(tmp_path: Path, files: dict[str, dict[int, list[str]]]) -> Path:
    from coverage import CoverageData

    _DATA_COUNTER[0] += 1
    path = tmp_path / f".coverage-synth-{_DATA_COUNTER[0]}"
    data = CoverageData(str(path))
    for filename, by_line in files.items():
        for lineno, contexts in by_line.items():
            for context in contexts:
                data.set_context(context)
                data.add_lines({filename: {lineno}})
    data.set_context("")
    data.write()
    return path


def _mapping(repo: Path, sha: str) -> dict:
    del repo
    nodes = [
        "tests/test_benchbox.py::test_translate",
        "tests/unit/core/test_engine.py::test_x",
    ]
    return {
        "version": 2,
        "sha": sha,
        "nodes": nodes,
        "files": {
            "benchbox/core/engine.py": {
                "lines": {"1": [1]},
                "statements": [1],
            },
            "benchbox/base.py": {
                "lines": {"4": [0], "5": [0]},
                "statements": [4, 5],
            },
        },
        "failed_nodes": [],
    }


def test_normalize_context_strips_phase_suffixes() -> None:
    assert sel.normalize_context("tests/test_a.py::test_x|run") == "tests/test_a.py::test_x"
    assert sel.normalize_context("tests/test_a.py::test_x|setup") == "tests/test_a.py::test_x"
    assert sel.normalize_context("tests/test_a.py::test_x|teardown") == "tests/test_a.py::test_x"
    assert sel.normalize_context("tests/test_a.py::test_x") == "tests/test_a.py::test_x"
    assert sel.normalize_context("") is None
    assert sel.normalize_context("|run") is None


def test_probe_lines_covers_ranges_and_pure_additions() -> None:
    assert sel.probe_lines(10, 3) == [10, 11, 12]
    assert sel.probe_lines(7, 1) == [7]
    assert sel.probe_lines(5, 0) == [5, 6]
    assert sel.probe_lines(1, 0) == [1, 2]
    assert sel.probe_lines(0, 0) == [1]


def test_parse_diff_reads_modification_ranges() -> None:
    diff = (
        "diff --git a/benchbox/core/engine.py b/benchbox/core/engine.py\n"
        "index 1111111..2222222 100644\n"
        "--- a/benchbox/core/engine.py\n"
        "+++ b/benchbox/core/engine.py\n"
        "@@ -1,2 +1,3 @@\n"
        " x = 1\n"
        "+y = 2\n"
    )
    (entry,) = sel.parse_diff(diff)
    assert entry.path == "benchbox/core/engine.py"
    assert entry.old_path == "benchbox/core/engine.py"
    assert entry.status == "modified"
    assert entry.old_ranges == [(1, 2)]


def test_parse_diff_reads_additions_deletions_and_renames() -> None:
    diff = (
        "diff --git a/benchbox/core/old.py b/benchbox/core/new.py\n"
        "similarity index 90%\n"
        "rename from benchbox/core/old.py\n"
        "rename to benchbox/core/new.py\n"
        "--- a/benchbox/core/old.py\n"
        "+++ b/benchbox/core/new.py\n"
        "@@ -2 +2 @@\n"
        "-x = 1\n"
        "+x = 2\n"
        "diff --git a/benchbox/core/fresh.py b/benchbox/core/fresh.py\n"
        "new file mode 100644\n"
        "index 0000000..1111111\n"
        "--- /dev/null\n"
        "+++ b/benchbox/core/fresh.py\n"
        "@@ -0,0 +1 @@\n"
        "+x = 1\n"
        "diff --git a/benchbox/core/gone.py b/benchbox/core/gone.py\n"
        "deleted file mode 100644\n"
        "index 1111111..0000000\n"
        "--- a/benchbox/core/gone.py\n"
        "+++ /dev/null\n"
        "@@ -1 +0,0 @@\n"
        "-x = 1\n"
    )
    entries = {entry.path: entry for entry in sel.parse_diff(diff)}
    assert entries["benchbox/core/new.py"].status == "renamed"
    assert entries["benchbox/core/new.py"].old_path == "benchbox/core/old.py"
    assert entries["benchbox/core/new.py"].old_ranges == [(2, 1)]
    assert entries["benchbox/core/fresh.py"].status == "added"
    assert entries["benchbox/core/gone.py"].status == "deleted"
    assert entries["benchbox/core/gone.py"].old_ranges == [(1, 1)]


def test_parse_diff_reads_pure_renames_and_header_only_blocks() -> None:
    diff = (
        "diff --git a/benchbox/core/old.py b/benchbox/core/new.py\n"
        "similarity index 100%\n"
        "rename from benchbox/core/old.py\n"
        "rename to benchbox/core/new.py\n"
        "diff --git a/benchbox/core/empty.py b/benchbox/core/empty.py\n"
        "new file mode 100644\n"
        "index 0000000..e69de29\n"
    )
    entries = {entry.path: entry for entry in sel.parse_diff(diff)}
    assert entries["benchbox/core/new.py"].status == "renamed"
    assert entries["benchbox/core/new.py"].old_path == "benchbox/core/old.py"
    assert entries["benchbox/core/new.py"].old_ranges == []
    assert entries["benchbox/core/empty.py"].status == "added"


def test_parse_diff_rejects_blocks_without_paths() -> None:
    with pytest.raises(sel.SelectionError):
        sel.parse_diff("diff --git \nfoo\n")


def test_lookup_hits_separates_covered_and_missed_lines() -> None:
    nodes = ["tests/test_benchbox.py::test_translate"]
    entry = {"lines": {"4": [0], "5": []}, "statements": [4, 5, 6]}
    hits, missed, setup_hits = sel.lookup_hits(entry, nodes, [4, 5, 6])
    assert hits == {"tests/test_benchbox.py::test_translate"}
    assert missed == [5, 6]
    assert setup_hits == set()
    assert sel.lookup_hits({}, nodes, [4]) == (set(), [4], set())


def test_lookup_hits_treats_non_statement_lines_as_missed() -> None:
    nodes = ["tests/test_benchbox.py::test_translate"]
    entry = {"lines": {"4": [0]}, "statements": [4]}
    hits, missed, _ = sel.lookup_hits(entry, nodes, [2, 4])
    assert hits == {"tests/test_benchbox.py::test_translate"}
    assert missed == [2]


def test_lookup_hits_without_a_statement_table_checks_every_line() -> None:
    nodes = ["tests/test_benchbox.py::test_translate"]
    entry = {"lines": {"4": [0]}}
    hits, missed, _ = sel.lookup_hits(entry, nodes, [2, 4])
    assert hits == {"tests/test_benchbox.py::test_translate"}
    assert missed == [2]


def test_lookup_hits_reports_setup_phase_hits() -> None:
    nodes = ["tests/test_benchbox.py::test_translate"]
    entry = {"lines": {"4": [0]}, "statements": [4], "setup": {"4": [0]}}
    hits, missed, setup_hits = sel.lookup_hits(entry, nodes, [4])
    assert hits == {"tests/test_benchbox.py::test_translate"}
    assert missed == []
    assert setup_hits == {"tests/test_benchbox.py::test_translate"}


def test_build_map_merges_shards_and_strips_contexts(tmp_path: Path, repo: Path) -> None:
    base = str(repo / "benchbox/base.py")
    engine = str(repo / "benchbox/core/engine.py")
    first = _coverage_data(
        tmp_path,
        {base: {4: ["tests/test_benchbox.py::test_translate|run", ""]}, engine: {1: [""]}},
    )
    second = _coverage_data(tmp_path, {base: {5: ["tests/test_benchbox.py::test_translate|setup"]}})
    mapping = sel.build_map([first, second], repo, _head(repo))
    assert mapping["version"] == sel.MAP_VERSION
    assert mapping["sha"] == _head(repo)
    assert mapping["nodes"] == ["tests/test_benchbox.py::test_translate"]
    entry = mapping["files"]["benchbox/base.py"]
    assert entry["lines"] == {"4": [0], "5": [0]}
    assert 4 in entry["statements"]
    assert 5 in entry["statements"]
    assert entry["setup"] == {"5": [0]}
    assert "benchbox/core/engine.py" not in mapping["files"]
    assert mapping["failed_nodes"] == []


def test_build_map_setup_phase_expands_selection_to_module(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sel, "SELECTION_SHADOW", False)
    base = str(repo / "benchbox/base.py")
    first_node = "tests/test_benchbox.py::test_translate"
    second_node = "tests/test_benchbox.py::test_other"
    first = _coverage_data(tmp_path, {base: {4: [f"{first_node}|setup"]}})
    second = _coverage_data(tmp_path, {base: {5: [f"{second_node}|run"]}})
    head = _head(repo)
    mapping = sel.build_map([first, second], repo, head)
    assert mapping["nodes"] == [second_node, first_node]
    entry = mapping["files"]["benchbox/base.py"]
    assert entry["lines"] == {"4": [1], "5": [0]}
    assert all(isinstance(index, int) for indices in entry["lines"].values() for index in indices)
    assert set(entry["statements"]) >= {4, 5}
    assert entry["setup"] == {"4": [1]}
    files = [
        sel.ChangedFile(
            path="benchbox/base.py",
            old_path="benchbox/base.py",
            status="modified",
            old_ranges=[(4, 1)],
        )
    ]
    result = sel.select_tests(repo, mapping, files, head)
    assert first_node in result["coverage_tests"]
    assert second_node in result["selected"]
    assert result["escalate"] is False


def test_build_map_skips_files_outside_the_repo(tmp_path: Path, repo: Path) -> None:
    outside = _coverage_data(tmp_path, {"/elsewhere/other.py": {1: ["tests/test_x.py::test_y|run"]}})
    assert sel.build_map([outside], repo, _head(repo))["files"] == {}


def test_build_map_records_failed_nodes_from_junit(tmp_path: Path, repo: Path) -> None:
    base = str(repo / "benchbox/base.py")
    data = _coverage_data(tmp_path, {base: {4: ["tests/test_benchbox.py::test_translate|run"]}})
    junit = tmp_path / "junit.xml"
    junit.write_text(
        '<?xml version="1.0"?><testsuite>'
        '<testcase file="tests/unit/core/test_engine.py" name="test_x">'
        "<failure>boom</failure></testcase>"
        '<testcase file="tests/unit/core/test_engine.py" name="test_ok"/>'
        "</testsuite>",
        encoding="utf-8",
    )
    mapping = sel.build_map([data], repo, _head(repo), junit_files=[junit])
    assert mapping["failed_nodes"] == ["tests/unit/core/test_engine.py::test_x"]


def test_build_map_derives_failed_nodes_without_file_attributes(tmp_path: Path, repo: Path) -> None:
    base = str(repo / "benchbox/base.py")
    data = _coverage_data(tmp_path, {base: {4: ["tests/test_benchbox.py::test_translate|run"]}})
    junit = tmp_path / "junit-xunit2.xml"
    junit.write_text(
        '<?xml version="1.0"?><testsuite>'
        '<testcase classname="tests.unit.core.test_engine" name="test_x">'
        "<failure>boom</failure></testcase>"
        '<testcase classname="tests.unit.core.test_engine" name="test_ok"/>'
        "</testsuite>",
        encoding="utf-8",
    )
    mapping = sel.build_map([data], repo, _head(repo), junit_files=[junit])
    assert mapping["failed_nodes"] == ["tests/unit/core/test_engine.py::test_x"]


def test_build_map_derives_failed_nodes_from_classname_on_file_mismatch(tmp_path: Path, repo: Path) -> None:
    base = str(repo / "benchbox/base.py")
    data = _coverage_data(tmp_path, {base: {4: ["tests/test_benchbox.py::test_translate|run"]}})
    junit = tmp_path / "junit.xml"
    junit.write_text(
        '<?xml version="1.0"?><testsuite>'
        '<testcase file="tests/unit/a/test_base.py" classname="tests.unit.core.test_engine.TestSub"'
        ' name="test_inherited"><failure>boom</failure></testcase>'
        "</testsuite>",
        encoding="utf-8",
    )
    mapping = sel.build_map([data], repo, _head(repo), junit_files=[junit])
    assert mapping["failed_nodes"] == ["tests/unit/core/test_engine.py::TestSub::test_inherited"]


def test_build_map_rejects_a_junit_shortfall_against_the_data_files(tmp_path: Path, repo: Path) -> None:
    data = _coverage_data(tmp_path, {str(repo / "benchbox/base.py"): {4: ["tests/test_benchbox.py::test_translate"]}})
    out = tmp_path / "map.json"
    assert (
        sel.main(
            [
                "build-map",
                "--data",
                str(data),
                "--junit",
                str(tmp_path / "missing-*.xml"),
                "--expected-shards",
                "1",
                "--sha",
                _head(repo),
                "--out",
                str(out),
                "--repo-root",
                str(repo),
            ]
        )
        == 1
    )
    assert not out.is_file()


def test_read_map_rejects_bad_envelopes(tmp_path: Path, repo: Path) -> None:
    good = _mapping(repo, _head(repo))
    path = tmp_path / "map.json"
    path.write_text(json.dumps(good), encoding="utf-8")
    assert sel.read_map(path) == good
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(sel.SelectionError):
        sel.read_map(path)
    path.write_text(json.dumps({**good, "version": 999}), encoding="utf-8")
    with pytest.raises(sel.SelectionError, match="rebuild"):
        sel.read_map(path)
    path.write_text(json.dumps({**good, "sha": "xyz"}), encoding="utf-8")
    with pytest.raises(sel.SelectionError):
        sel.read_map(path)
    with pytest.raises(sel.SelectionError):
        sel.read_map(tmp_path / "absent.json")


def test_read_map_rejects_v1_maps_with_rebuild(repo: Path, tmp_path: Path) -> None:
    path = tmp_path / "map.json"
    path.write_text(json.dumps({"version": 1, "sha": _head(repo), "files": {}}), encoding="utf-8")
    with pytest.raises(sel.SelectionError, match="rebuild"):
        sel.read_map(path)


def test_changed_files_detects_rename_with_old_ranges(repo: Path) -> None:
    _write(repo, "benchbox/core/engine.py", "".join(f"line_{index} = {index}\n" for index in range(20)))
    _commit_all(repo, "grow")
    grown = _head(repo)
    _git(repo, "mv", "benchbox/core/engine.py", "benchbox/core/motor.py")
    _write(
        repo,
        "benchbox/core/motor.py",
        "".join(f"line_{index} = {index}\n" for index in range(19)) + "line_19 = 99\n",
    )
    _commit_all(repo)
    entries = {entry.path: entry for entry in sel.changed_files(repo, grown)}
    assert entries["benchbox/core/motor.py"].status == "renamed"
    assert entries["benchbox/core/motor.py"].old_path == "benchbox/core/engine.py"
    assert entries["benchbox/core/motor.py"].old_ranges == [(20, 1)]


def test_selection_unites_coverage_changed_tests_and_fallback(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sel, "SELECTION_SHADOW", False)
    map_sha = _head(repo)
    _write(repo, "benchbox/core/engine.py", "x = 2\n")
    _write(repo, "tests/unit/test_unrelated.py", "y = 2\n")
    _commit_all(repo)
    files = sel.changed_files(repo, map_sha)
    result = sel.select_tests(repo, _mapping(repo, map_sha), files, map_sha)
    assert result["escalate"] is False
    assert "tests/unit/core/test_engine.py::test_x" in result["selected"]
    assert "tests/unit/test_unrelated.py" in result["selected"]
    assert "tests/unit/core/test_engine.py" in result["fallback_tests"]
    assert result["map_sha"] == map_sha
    assert result["behind_commits"] == 0
    assert result["failed_nodes"] == []
    assert result["estimated_runtime_seconds"] >= 0


def test_shadow_mode_escalates_while_reporting_a_clean_selection(repo: Path) -> None:
    assert sel.SELECTION_SHADOW is True
    map_sha = _head(repo)
    _write(repo, "benchbox/core/engine.py", "x = 2\n")
    _write(repo, "tests/unit/test_unrelated.py", "y = 2\n")
    _commit_all(repo)
    files = sel.changed_files(repo, map_sha)
    result = sel.select_tests(repo, _mapping(repo, map_sha), files, map_sha)
    assert result["escalate"] is True
    assert result["shadow"] is True
    assert result["would_escalate"] is False
    assert result["would_escalate_reasons"] == []
    assert result["reason"].startswith("shadow mode:")
    assert result["elapsed_seconds"] >= 0
    assert result["peak_rss_mb"] > 0
    assert "tests/unit/core/test_engine.py::test_x" in result["selected"]


def test_shadow_mode_records_would_escalate_reasons(repo: Path) -> None:
    assert sel.SELECTION_SHADOW is True
    map_sha = _head(repo)
    _write(repo, "benchbox/core/engine.py", "x = 2\n")
    _write(repo, "benchbox/core/fresh.py", "y = 3\n")
    _commit_all(repo)
    files = sel.changed_files(repo, map_sha)
    result = sel.select_tests(repo, _mapping(repo, map_sha), files, map_sha)
    assert result["escalate"] is True
    assert result["would_escalate"] is True
    assert result["would_escalate_reasons"] == ["changed Python code no recorded test executed: benchbox/core/fresh.py"]
    assert result["reason"].startswith("shadow mode:")


def test_shadow_off_restores_the_computed_selection(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sel, "SELECTION_SHADOW", False)
    map_sha = _head(repo)
    _write(repo, "benchbox/core/engine.py", "x = 2\n")
    _write(repo, "tests/unit/test_unrelated.py", "y = 2\n")
    _commit_all(repo)
    files = sel.changed_files(repo, map_sha)
    result = sel.select_tests(repo, _mapping(repo, map_sha), files, map_sha)
    assert result["escalate"] is False
    assert "shadow" not in result
    assert "would_escalate" not in result
    assert "tests/unit/core/test_engine.py::test_x" in result["selected"]


def test_pure_addition_probes_neighbouring_old_lines(repo: Path) -> None:
    map_sha = _head(repo)
    _write(repo, "benchbox/base.py", "VALUE = 1\n\n\ndef translate(value):\n    return value + 1\n# trailing note\n")
    _commit_all(repo)
    files = sel.changed_files(repo, map_sha)
    (entry,) = [entry for entry in files if entry.path == "benchbox/base.py"]
    assert entry.old_ranges == [(5, 0)]
    result = sel.select_tests(repo, _mapping(repo, map_sha), files, map_sha)
    assert "tests/test_benchbox.py::test_translate" in result["selected"]


def test_non_statement_lines_escalate_instead_of_narrowing(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sel, "SELECTION_SHADOW", False)
    head = _head(repo)
    files = [
        sel.ChangedFile(
            path="benchbox/base.py",
            old_path="benchbox/base.py",
            status="modified",
            old_ranges=[(2, 1), (4, 1)],
        )
    ]
    result = sel.select_tests(repo, _mapping(repo, head), files, head)
    assert result["escalate"] is True
    assert result["uncovered_sources"] == ["benchbox/base.py"]
    assert "tests/test_benchbox.py::test_translate" in result["selected"]


def test_build_map_omits_statements_when_analysis_fails(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = str(repo / "benchbox/base.py")
    data = _coverage_data(tmp_path, {base: {4: ["tests/test_benchbox.py::test_translate|run"]}})
    monkeypatch.setattr(sel, "_statement_lines", lambda _repo, _data, _files: {})
    mapping = sel.build_map([data], repo, _head(repo))
    entry = mapping["files"]["benchbox/base.py"]
    assert "statements" not in entry
    hits, missed, _ = sel.lookup_hits(entry, mapping["nodes"], [4, 6])
    assert hits == {"tests/test_benchbox.py::test_translate"}
    assert missed == [6]


def test_uncovered_lines_escalate_and_keep_importing_tests(repo: Path) -> None:
    map_sha = _head(repo)
    _write(repo, "benchbox/core/engine.py", "x = 1\ny = 2\n")
    _write(repo, "tests/unit/core/test_engine.py", "from benchbox.core.engine import x\n")
    _commit_all(repo)
    files = sel.changed_files(repo, map_sha)
    result = sel.select_tests(repo, _mapping(repo, map_sha), files, _head(repo))
    assert result["escalate"] is True
    assert "no recorded test executed: benchbox/core/engine.py" in result["reason"]
    assert "tests/unit/core/test_engine.py" in result["importer_tests"]
    assert "tests/unit/core/test_engine.py" in result["selected"]
    assert result["uncovered_sources"] == ["benchbox/core/engine.py"]


def test_new_source_file_escalates_and_keeps_importers(repo: Path) -> None:
    map_sha = _head(repo)
    _write(repo, "benchbox/core/fresh.py", "x = 1\n")
    _write(repo, "tests/unit/core/test_fresh.py", "from benchbox.core.fresh import x\n")
    _commit_all(repo)
    files = sel.changed_files(repo, map_sha)
    result = sel.select_tests(repo, _mapping(repo, map_sha), files, _head(repo))
    assert result["escalate"] is True
    assert "benchbox/core/fresh.py" in result["reason"]
    assert "tests/unit/core/test_fresh.py" in result["selected"]


def test_shared_test_plugin_change_escalates(repo: Path) -> None:
    map_sha = _head(repo)
    _write(repo, "tests/conftest.py", "FIXTURE = 1\n")
    _write(repo, "tests/fixtures/database_fixtures.py", "DB = 1\n")
    _commit_all(repo)
    files = sel.changed_files(repo, map_sha)
    result = sel.select_tests(repo, _mapping(repo, map_sha), files, _head(repo))
    assert result["escalate"] is True
    assert "tests/conftest.py" in result["reason"]
    assert "tests/fixtures/database_fixtures.py" in result["reason"]


def test_failed_nodes_join_selection_without_code_change(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sel, "SELECTION_SHADOW", False)
    head = _head(repo)
    mapping = _mapping(repo, head)
    mapping["failed_nodes"] = ["tests/unit/core/test_engine.py::test_x"]
    result = sel.select_tests(repo, mapping, [], head)
    assert result["failed_nodes"] == ["tests/unit/core/test_engine.py::test_x"]
    assert "tests/unit/core/test_engine.py::test_x" in result["selected"]
    assert result["escalate"] is False


def test_failed_nodes_missing_from_checkout_are_dropped(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sel, "SELECTION_SHADOW", False)
    head = _head(repo)
    mapping = _mapping(repo, head)
    mapping["failed_nodes"] = ["tests/unit/core/test_absent.py::test_x"]
    result = sel.select_tests(repo, mapping, [], head)
    assert result["failed_nodes"] == []
    assert result["escalate"] is False


def test_missing_map_escalates_with_fallback(repo: Path) -> None:
    map_sha = _head(repo)
    _write(repo, "benchbox/core/engine.py", "x = 2\n")
    _commit_all(repo)
    files = sel.changed_files(repo, map_sha)
    result = sel.select_tests(repo, None, files, _head(repo))
    assert result["escalate"] is True
    assert "missing" in result["reason"]
    assert result["selected"] == result["fallback_tests"]
    assert result["fallback_tests"] == ["tests/unit/core/test_engine.py"]


def test_unreadable_map_note_escalates(repo: Path) -> None:
    result = sel.select_tests(repo, None, [], _head(repo), map_note="coverage map x is unreadable: boom")
    assert result["escalate"] is True
    assert "boom" in result["reason"]


def test_stale_map_escalates_past_the_named_limit(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sel, "SELECTION_SHADOW", False)
    assert sel.MAX_MAP_BEHIND_COMMITS == 50
    map_sha = _head(repo)
    for _ in range(sel.MAX_MAP_BEHIND_COMMITS + 1):
        _git(repo, "commit", "--allow-empty", "-m", "gap")
    base_sha = _head(repo)
    assert sel.behind_commits(repo, map_sha, base_sha) == sel.MAX_MAP_BEHIND_COMMITS + 1
    result = sel.select_tests(repo, _mapping(repo, map_sha), [], base_sha)
    assert result["escalate"] is True
    assert "behind" in result["reason"]
    fresh = sel.select_tests(repo, _mapping(repo, base_sha), [], base_sha)
    assert fresh["escalate"] is False
    assert fresh["behind_commits"] == 0


def test_selection_over_the_runtime_limit_escalates(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sel, "SELECTION_SHADOW", False)
    assert sel.MAX_SELECTION_RUNTIME_SECONDS == 900
    map_sha = _head(repo)
    _write(repo, "benchbox/core/engine.py", "x = 2\n")
    _commit_all(repo)
    files = sel.changed_files(repo, map_sha)
    node = "tests/unit/core/test_engine.py::test_x"
    result = sel.select_tests(
        repo, _mapping(repo, map_sha), files, map_sha, durations={node: 1000.0}, durations_default=0.0
    )
    assert result["escalate"] is True
    assert "estimated runtime" in result["reason"]
    assert set(result["selected"]) == (
        set(result["fallback_tests"])
        | set(result["importer_tests"])
        | set(result["develop_changed_tests"])
        | set(result["failed_nodes"])
    )


def test_selection_under_the_runtime_limit_does_not_escalate(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sel, "SELECTION_SHADOW", False)
    map_sha = _head(repo)
    _write(repo, "benchbox/core/engine.py", "x = 2\n")
    _commit_all(repo)
    files = sel.changed_files(repo, map_sha)
    node = "tests/unit/core/test_engine.py::test_x"
    result = sel.select_tests(
        repo, _mapping(repo, map_sha), files, map_sha, durations={node: 1.0}, durations_default=1.0
    )
    assert result["escalate"] is False
    assert node in result["selected"]
    assert result["estimated_runtime_seconds"] <= sel.MAX_SELECTION_RUNTIME_SECONDS


def test_selection_over_the_argv_limit_escalates(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sel, "SELECTION_SHADOW", False)
    assert sel.MAX_ARGV_NODEIDS == 10000
    monkeypatch.setattr(sel, "MAX_ARGV_NODEIDS", 1)
    map_sha = _head(repo)
    _write(repo, "benchbox/core/engine.py", "x = 2\n")
    _commit_all(repo)
    files = sel.changed_files(repo, map_sha)
    result = sel.select_tests(repo, _mapping(repo, map_sha), files, map_sha, durations={}, durations_default=0.0)
    assert result["escalate"] is True
    assert "argv limit" in result["reason"]
    assert set(result["selected"]) == (
        set(result["fallback_tests"])
        | set(result["importer_tests"])
        | set(result["develop_changed_tests"])
        | set(result["failed_nodes"])
    )


def test_inert_paths_never_escalate(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sel, "SELECTION_SHADOW", False)
    map_sha = _head(repo)
    _write(repo, "docs/guide.md", "# changed\n")
    _write(repo, "notes.md", "# changed\n")
    _write(repo, "_project/todo-db-export/index.md", "{}\n")
    _commit_all(repo)
    files = sel.changed_files(repo, map_sha)
    base_sha = _head(repo)
    assert files
    assert all(sel._is_inert(entry.path) for entry in files)
    for decision in ({"needs_code_ci": True}, {"needs_code_ci": False}, None):
        assert sel.select_tests(repo, _mapping(repo, map_sha), files, base_sha, decision=decision)["escalate"] is False


def test_non_python_change_escalates_only_for_code_routed_trees(repo: Path) -> None:
    map_sha = _head(repo)
    _write(repo, "assets/logo.png", "fake-png\n")
    _commit_all(repo)
    files = sel.changed_files(repo, map_sha)
    base_sha = _head(repo)
    coded = sel.select_tests(repo, _mapping(repo, map_sha), files, base_sha, decision={"needs_code_ci": True})
    assert coded["escalate"] is True
    assert "assets/logo.png" in coded["reason"]
    routed_away = sel.select_tests(repo, _mapping(repo, map_sha), files, base_sha, decision={"needs_code_ci": False})
    assert routed_away["escalate"] is True
    assert "no tests selected" in routed_away["reason"]
    unknown = sel.select_tests(repo, _mapping(repo, map_sha), files, base_sha)
    assert unknown["escalate"] is True


def test_non_exempt_exception_still_escalates_as_non_python(repo: Path) -> None:
    map_sha = _head(repo)
    _write(repo, ".claude/settings.json", "{}\n")
    _commit_all(repo)
    files = sel.changed_files(repo, map_sha)
    base_sha = _head(repo)
    assert files and all(entry.path == ".claude/settings.json" for entry in files)
    result = sel.select_tests(repo, _mapping(repo, map_sha), files, base_sha, decision={"needs_code_ci": True})
    assert result["escalate"] is True
    assert ".claude/settings.json" in result["reason"]


def test_deleted_source_still_matches_coverage_by_old_path(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sel, "SELECTION_SHADOW", False)
    map_sha = _head(repo)
    (repo / "benchbox/core/engine.py").unlink()
    _commit_all(repo)
    files = sel.changed_files(repo, map_sha)
    result = sel.select_tests(repo, _mapping(repo, map_sha), files, map_sha)
    assert result["escalate"] is False
    assert "tests/unit/core/test_engine.py::test_x" in result["selected"]


def test_empty_selection_with_code_changes_escalates(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    map_sha = _head(repo)
    monkeypatch.setattr(sel.preflight_targets, "map_tests", lambda _repo, _inputs: [])
    files = [
        sel.ChangedFile(
            path="benchbox/core/engine.py",
            old_path="benchbox/core/engine.py",
            status="modified",
            old_ranges=[(99, 1)],
        )
    ]
    result = sel.select_tests(repo, _mapping(repo, map_sha), files, _head(repo))
    assert result["escalate"] is True
    assert "no tests selected" in result["reason"]


def test_stale_coverage_node_ids_are_dropped(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    map_sha = _head(repo)
    (repo / "tests/unit/core/test_engine.py").unlink()
    monkeypatch.setattr(sel.preflight_targets, "map_tests", lambda _repo, _inputs: [])
    files = [
        sel.ChangedFile(
            path="benchbox/core/engine.py",
            old_path="benchbox/core/engine.py",
            status="modified",
            old_ranges=[(1, 1)],
        )
    ]
    result = sel.select_tests(repo, _mapping(repo, map_sha), files, _head(repo))
    assert result["coverage_tests"] == []
    assert "tests/unit/core/test_engine.py::test_x" not in result["selected"]
    assert result["escalate"] is True
    assert "no tests selected" in result["reason"]


def test_zero_hunk_modified_source_escalates(repo: Path) -> None:
    map_sha = _head(repo)
    _write(repo, "tests/unit/core/test_engine.py", "from benchbox.core.engine import x\n")
    _commit_all(repo)
    files = [
        sel.ChangedFile(
            path="benchbox/core/engine.py",
            old_path="benchbox/core/engine.py",
            status="modified",
            old_ranges=[],
        )
    ]
    result = sel.select_tests(repo, _mapping(repo, map_sha), files, _head(repo))
    assert result["escalate"] is True
    assert "no recorded test executed: benchbox/core/engine.py" in result["reason"]
    assert result["uncovered_sources"] == ["benchbox/core/engine.py"]
    assert "tests/unit/core/test_engine.py" in result["importer_tests"]
    assert "tests/unit/core/test_engine.py" in result["selected"]


def test_escalation_runs_the_full_union(repo: Path) -> None:
    map_sha = _head(repo)
    _write(repo, "benchbox/core/engine.py", "x = 2\n")
    _write(repo, "tests/unit/test_unrelated.py", "y = 2\n")
    _commit_all(repo)
    files = sel.changed_files(repo, map_sha)
    result = sel.select_tests(repo, None, files, _head(repo))
    assert result["escalate"] is True
    assert "tests/unit/test_unrelated.py" in result["selected"]
    assert set(result["fallback_tests"]) <= set(result["selected"])
    assert set(result["importer_tests"]) <= set(result["selected"])


def test_map_sha_outside_base_ancestry_escalates(repo: Path) -> None:
    map_sha = _head(repo)
    _git(repo, "checkout", "-b", "side")
    _write(repo, "benchbox/core/engine.py", "x = 2\n")
    _commit_all(repo)
    side_sha = _head(repo)
    _git(repo, "checkout", "feature")
    result = sel.select_tests(repo, _mapping(repo, side_sha), [], _head(repo))
    assert map_sha != side_sha
    assert result["escalate"] is True
    assert "not an ancestor" in result["reason"]
    assert result["behind_commits"] is None


def test_pure_rename_escalates_and_keeps_tests_on_the_old_path(repo: Path) -> None:
    map_sha = _head(repo)
    base_sha = map_sha
    _git(repo, "mv", "benchbox/core/engine.py", "benchbox/core/motor.py")
    _commit_all(repo)
    files = sel.changed_files(repo, base_sha)
    assert [(entry.path, entry.status) for entry in files] == [("benchbox/core/motor.py", "renamed")]
    result = sel.select_tests(repo, _mapping(repo, map_sha), files, base_sha)
    assert result["escalate"] is True
    assert "benchbox/core/motor.py" in result["reason"]
    assert "tests/unit/core/test_engine.py::test_x" in result["selected"]


def test_pure_rename_of_non_python_resource_escalates(repo: Path) -> None:
    map_sha = _head(repo)
    _write(repo, "assets/logo.png", "fake-png\n")
    _commit_all(repo)
    base_sha = _head(repo)
    _git(repo, "mv", "assets/logo.png", "assets/banner.png")
    _commit_all(repo)
    files = sel.changed_files(repo, base_sha)
    assert [(entry.path, entry.status) for entry in files] == [("assets/banner.png", "renamed")]
    result = sel.select_tests(repo, _mapping(repo, map_sha), files, _head(repo), decision={"needs_code_ci": True})
    assert result["escalate"] is True
    assert "assets/banner.png" in result["reason"]


def test_file_changed_on_develop_since_the_map_escalates(repo: Path) -> None:
    map_sha = _head(repo)
    _write(repo, "benchbox/core/engine.py", "x = 2\n")
    _commit_all(repo)
    base_sha = _head(repo)
    _write(repo, "benchbox/core/engine.py", "x = 1\n")
    _commit_all(repo)
    assert sel.changed_files(repo, map_sha) == []
    files = sel.changed_files(repo, base_sha)
    assert [entry.path for entry in files] == ["benchbox/core/engine.py"]
    result = sel.select_tests(repo, _mapping(repo, map_sha), files, base_sha)
    assert result["escalate"] is True
    assert "no recorded test executed: benchbox/core/engine.py" in result["reason"]
    assert "tests/unit/core/test_engine.py" in result["selected"]


def test_build_map_rejects_unexpected_shard_count(repo: Path, tmp_path: Path) -> None:
    data = _coverage_data(tmp_path, {str(repo / "benchbox/base.py"): {4: ["tests/test_benchbox.py::test_translate"]}})
    out = tmp_path / "map.json"
    base = ["build-map", "--data", str(data), "--sha", _head(repo), "--out", str(out), "--repo-root", str(repo)]
    assert sel.main([*base, "--expected-shards", "2"]) == 1
    assert not out.is_file()
    assert sel.main([*base, "--expected-shards", "1"]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["sha"] == _head(repo)


def test_trusted_run_head_accepts_trunk_push_runs(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sha = "a" * 40
    stdout = "\n".join([sha, ".github/workflows/trunk-coverage.yml", "push", "develop", "o/r"]) + "\n"

    def _run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, stdout, "")

    monkeypatch.setattr(sel.subprocess, "run", _run)
    assert sel._trusted_run_head(repo, "o/r", 7) == sha


def test_trusted_run_head_accepts_dispatched_runs_on_develop(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sha = "b" * 40
    stdout = "\n".join([sha, ".github/workflows/trunk-coverage.yml", "workflow_dispatch", "develop", "o/r"]) + "\n"
    monkeypatch.setattr(sel.subprocess, "run", _fake_run(stdout))
    assert sel._trusted_run_head(repo, "o/r", 9) == sha


def test_trusted_run_head_rejects_untrusted_runs(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sha = "a" * 40
    good = [sha, ".github/workflows/trunk-coverage.yml", "push", "develop", "o/r"]
    cases = [
        ["not-a-sha", good[1], good[2], good[3], good[4]],
        [good[0], ".github/workflows/ci.yml", good[2], good[3], good[4]],
        [good[0], good[1], "pull_request", good[3], good[4]],
        [good[0], good[1], good[2], "feature", good[4]],
        [good[0], good[1], good[2], good[3], "other/r"],
    ]
    for fields in cases:
        monkeypatch.setattr(sel.subprocess, "run", _fake_run("\n".join(fields) + "\n"))
        assert sel._trusted_run_head(repo, "o/r", 7) is None


def test_trusted_run_head_reports_api_failures(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, "", "HTTP 500")

    monkeypatch.setattr(sel.subprocess, "run", _run)
    with pytest.raises(sel.SelectionError, match="HTTP 500"):
        sel._trusted_run_head(repo, "o/r", 7)


def _fake_run(stdout: str) -> object:
    def _run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, stdout, "")

    return _run


def test_list_map_artifacts_reads_trunk_runs_with_get_requests(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first, second = "a" * 40, "b" * 40
    commands: list[list[str]] = []

    def _run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        endpoint = command[4]
        if endpoint.endswith("/workflows/trunk-coverage.yml/runs"):
            runs = f"11\t{first}\tpush\n12\t{second}\tworkflow_dispatch\n13\tnot-a-sha\tpush\n14\t{first}\tschedule\n"
            return subprocess.CompletedProcess(command, 0, runs, "")
        if endpoint.endswith("/runs/11/artifacts"):
            return subprocess.CompletedProcess(command, 0, "", "")
        rows = f"t2-coverage-map-{second}\t2026-10-20T00:00:00Z\nt2-coverage-map-{first}\t2026-10-19T00:00:00Z\n"
        return subprocess.CompletedProcess(command, 0, rows, "")

    monkeypatch.setattr(sel.subprocess, "run", _run)
    found = list(sel._list_map_artifacts(repo, "o/r", "t2-coverage-map-"))
    assert found == [(f"t2-coverage-map-{second}", 12, "2026-10-20T00:00:00Z")]
    assert [command[2:4] for command in commands] == [["--method", "GET"]] * 3
    assert commands[0][7:9] == ["-f", "branch=develop"]
    assert f"name=t2-coverage-map-{second}" in commands[2]


def test_list_map_artifacts_reports_api_failures(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, "", "HTTP 500")

    monkeypatch.setattr(sel.subprocess, "run", _run)
    with pytest.raises(sel.SelectionError, match="HTTP 500"):
        list(sel._list_map_artifacts(repo, "o/r", "t2-coverage-map-"))


def test_fetch_map_skips_runs_failing_provenance(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sel, "_github_slug", lambda _repo: "o/r")
    monkeypatch.setattr(
        sel, "_list_map_artifacts", lambda _repo, _slug, _prefix: [("t2-coverage-map-abc", 7, "2026-10-20T00:00:00Z")]
    )
    monkeypatch.setattr(sel, "_trusted_run_head", lambda _repo, _slug, _run: None)
    result = sel.fetch_map(repo, _head(repo), tmp_path / "maps")
    assert result["status"] == "missing"


def test_fetch_map_lookup_failed_on_artifact_error(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sel, "_github_slug", lambda _repo: "o/r")

    def _boom(_repo: Path, _slug: str, _prefix: str) -> object:
        raise sel.SelectionError("boom")

    monkeypatch.setattr(sel, "_list_map_artifacts", _boom)
    result = sel.fetch_map(repo, _head(repo), tmp_path / "maps")
    assert result["status"] == "lookup-failed"
    assert result["sha"] is None
    assert "boom" in result["reason"]
    assert "::warning::" not in capsys.readouterr().out


def test_read_downloaded_map_prefers_matching_map_json(repo: Path, tmp_path: Path) -> None:
    head = _head(repo)
    target = tmp_path / "artifact"
    target.mkdir()
    (target / "a.json").write_text(json.dumps(_mapping(repo, "0" * 40)), encoding="utf-8")
    (target / "map.json").write_text(json.dumps(_mapping(repo, head)), encoding="utf-8")
    found = sel._read_downloaded_map(target, head)
    assert found is not None
    assert found[1].name == "map.json"
    assert sel._read_downloaded_map(target, "1" * 40) is None


def test_read_downloaded_map_scans_past_mismatched_candidates(repo: Path, tmp_path: Path) -> None:
    head = _head(repo)
    target = tmp_path / "artifact"
    target.mkdir()
    (target / "map.json").write_text(json.dumps(_mapping(repo, "0" * 40)), encoding="utf-8")
    (target / "b.json").write_text(json.dumps(_mapping(repo, head)), encoding="utf-8")
    found = sel._read_downloaded_map(target, head)
    assert found is not None
    assert found[1].name == "b.json"


def test_select_cli_writes_nodeids_and_json(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sel, "SELECTION_SHADOW", False)
    map_sha = _head(repo)
    _write(repo, "benchbox/core/engine.py", "x = 2\n")
    _commit_all(repo)
    map_path = tmp_path / "map.json"
    map_path.write_text(json.dumps(_mapping(repo, map_sha)), encoding="utf-8")
    payload = tmp_path / "selection.json"
    monkeypatch.chdir(repo)
    assert (
        sel.main(
            [
                "select",
                "--map",
                str(map_path),
                "--base-sha",
                map_sha,
                "--json-out",
                str(payload),
                "--repo-root",
                str(repo),
            ]
        )
        == 0
    )
    selected = json.loads(payload.read_text(encoding="utf-8"))["selected"]
    assert "tests/unit/core/test_engine.py::test_x" in selected
    assert json.loads(payload.read_text(encoding="utf-8"))["escalate"] is False


def test_select_cli_without_a_map_escalates(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(repo)
    payload = tmp_path / "selection.json"
    assert sel.main(["select", "--base-sha", _head(repo), "--json-out", str(payload), "--repo-root", str(repo)]) == 0
    assert json.loads(payload.read_text(encoding="utf-8"))["escalate"] is True


def test_unreadable_decision_raises(tmp_path: Path) -> None:
    with pytest.raises(sel.SelectionError):
        sel.read_decision(tmp_path / "decision.json")


def test_fetch_map_reports_missing_without_hiding_the_reason(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(_repo: Path) -> str:
        raise sel.SelectionError("gh is not installed")

    monkeypatch.setattr(sel, "_github_slug", _boom)
    result = sel.fetch_map(repo, _head(repo), tmp_path / "maps")
    assert result["status"] == "lookup-failed"
    assert result["sha"] is None
    assert "gh is not installed" in result["reason"]


def test_fetch_map_skips_runs_that_are_not_base_ancestors(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sel, "_github_slug", lambda _repo: "o/r")
    monkeypatch.setattr(
        sel, "_list_map_artifacts", lambda _repo, _slug, _prefix: [("t2-coverage-map-abc", 7, "2026-10-20T00:00:00Z")]
    )
    monkeypatch.setattr(sel, "_trusted_run_head", lambda _repo, _slug, _run: "a" * 40)
    monkeypatch.setattr(sel, "_is_ancestor", lambda _repo, _sha, _base: False)
    result = sel.fetch_map(repo, _head(repo), tmp_path / "maps")
    assert result["status"] == "missing"
    assert "ancestor" in result["reason"]


def _policy_sources() -> dict[str, str]:
    root = Path(__file__).resolve().parents[3]
    return {rel: (root / rel).read_text(encoding="utf-8") for rel in heavy.POLICY_PATHS}


def _soundness_kwargs() -> dict:
    sources = _policy_sources()
    return {"read_base": lambda _ref, _root: dict(sources), "read_pr": lambda _root: dict(sources)}


def _decision(paths: list[str]) -> dict:
    return {"needs_code_ci": True, "packaging_needed": False, "changed_paths": paths}


def test_heavy_bridge_escalates_on_selection(repo: Path) -> None:
    result = heavy.heavy_needed(
        _decision(["benchbox/base.py"]),
        "origin/develop",
        repo,
        selection={"escalate": True, "reason": "coverage map is missing"},
    )
    assert result["heavy_needed"] is True
    assert "coverage fail-closed" in result["reason"]
    assert "coverage map is missing" in result["reason"]


def test_heavy_bridge_passes_through_a_clean_selection(repo: Path) -> None:
    result = heavy.heavy_needed(
        _decision(["benchbox/base.py"]),
        "origin/develop",
        repo,
        selection={"escalate": False, "reason": "ok"},
        **_soundness_kwargs(),
    )
    assert result["heavy_needed"] is False


def test_heavy_bridge_fails_closed_on_an_unreadable_selection(repo: Path) -> None:
    assert (
        heavy.heavy_needed(_decision(["benchbox/base.py"]), "origin/develop", repo, selection={"escalate": "yes"})[
            "heavy_needed"
        ]
        is True
    )
    assert (
        heavy.heavy_needed(
            _decision(["benchbox/base.py"]), "origin/develop", repo, selection=None, **_soundness_kwargs()
        )["heavy_needed"]
        is False
    )


def test_heavy_bridge_keeps_the_light_lane_without_code(repo: Path) -> None:
    decision = {"needs_code_ci": False, "packaging_needed": False, "changed_paths": ["docs/guide.md"]}
    result = heavy.heavy_needed(decision, "origin/develop", repo, selection={"escalate": True, "reason": "stale"})
    assert result["heavy_needed"] is False


def test_heavy_cli_accepts_a_selection_file(repo: Path, tmp_path: Path) -> None:
    selection = tmp_path / "selection.json"
    selection.write_text(json.dumps({"escalate": True, "reason": "map missing"}), encoding="utf-8")
    decision = tmp_path / "decision.json"
    decision.write_text(json.dumps(_decision(["benchbox/base.py"])), encoding="utf-8")
    assert heavy.main(["--decision-in", str(decision), "--selection-in", str(selection), "--repo-root", str(repo)]) == 0
    selection.write_text("{bad", encoding="utf-8")
    assert heavy.main(["--decision-in", str(decision), "--selection-in", str(selection), "--repo-root", str(repo)]) == 0


def test_select_parse_failure_falls_back_to_changed_paths(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    map_sha = _head(repo)
    _write(repo, "scripts/tool.py", "x = 2\n")
    _commit_all(repo)
    map_path = tmp_path / "map.json"
    map_path.write_text(json.dumps(_mapping(repo, map_sha)), encoding="utf-8")
    payload = tmp_path / "selection.json"

    def _boom(header: str, block: list[str]) -> sel.ChangedFile:
        raise sel.SelectionError("cannot parse diff block")

    monkeypatch.setattr(sel, "parse_diff", _boom)
    monkeypatch.chdir(repo)
    assert (
        sel.main(
            [
                "select",
                "--map",
                str(map_path),
                "--base-sha",
                map_sha,
                "--json-out",
                str(payload),
                "--repo-root",
                str(repo),
            ]
        )
        == 0
    )
    result = json.loads(payload.read_text(encoding="utf-8"))
    assert result["escalate"] is True
    assert "coverage selection failed" in result["reason"]
    assert "tests/unit/test_tool.py" in result["selected"]


def test_select_total_failure_exits_nonzero(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    map_sha = _head(repo)
    _write(repo, "scripts/tool.py", "x = 2\n")
    _commit_all(repo)
    map_path = tmp_path / "map.json"
    map_path.write_text(json.dumps(_mapping(repo, map_sha)), encoding="utf-8")
    payload = tmp_path / "selection.json"

    def _boom(*args: object, **kwargs: object) -> object:
        raise sel.SelectionError("boom")

    monkeypatch.setattr(sel, "parse_diff", _boom)
    monkeypatch.setattr(sel.preflight_targets, "map_tests", _boom)
    monkeypatch.chdir(repo)
    assert (
        sel.main(
            [
                "select",
                "--map",
                str(map_path),
                "--base-sha",
                map_sha,
                "--json-out",
                str(payload),
                "--repo-root",
                str(repo),
            ]
        )
        == 2
    )
    assert not payload.is_file()


def test_prune_missing_nodeids_keeps_collectable_and_file_targets(repo: Path) -> None:
    node_ids = [
        "tests/unit/core/test_engine.py::test_x",
        "tests/unit/core/test_engine.py::test_x",
        "tests/unit/core/test_engine.py::test_gone",
        "tests/unit/core/test_engine.py",
        "tests/unit/core/test_missing.py::test_y",
        "tests/unit/core/absent.py",
    ]
    collected = ["tests/unit/core/test_engine.py::test_x"]
    assert sel.prune_missing_nodeids(repo, node_ids, collected) == [
        "tests/unit/core/test_engine.py::test_x",
        "tests/unit/core/test_engine.py",
    ]


def test_prune_cli_rewrites_nodeids(repo: Path, tmp_path: Path) -> None:
    nodeids = tmp_path / "nodeids.txt"
    nodeids.write_text(
        "tests/unit/core/test_engine.py::test_x\ntests/unit/core/test_engine.py::test_gone\n", encoding="utf-8"
    )
    collected = tmp_path / "collected.txt"
    collected.write_text("tests/unit/core/test_engine.py::test_x\n", encoding="utf-8")
    out = tmp_path / "pruned.txt"
    assert (
        sel.main(
            [
                "prune",
                "--nodeids-in",
                str(nodeids),
                "--collected-in",
                str(collected),
                "--nodeids-out",
                str(out),
                "--repo-root",
                str(repo),
            ]
        )
        == 0
    )
    assert out.read_text(encoding="utf-8").splitlines() == [
        "tests/unit/core/test_engine.py::test_x",
        "tests/unit/core/test_engine.py",
    ]


def _write_junit(path: Path, cases: list[tuple[str, str, str | None]]) -> None:
    lines = ['<?xml version="1.0" encoding="utf-8"?>', '<testsuites><testsuite name="pytest">']
    for classname, name, outcome in cases:
        source = classname.replace(".", "/") + ".py"
        if outcome is None:
            lines.append(f'<testcase classname="{classname}" name="{name}" file="{source}" time="0.1" />')
        else:
            lines.append(
                f'<testcase classname="{classname}" name="{name}" file="{source}" time="0.1">'
                f'<{outcome} message="boom">trace</{outcome}></testcase>'
            )
    lines.append("</testsuite></testsuites>")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _case(classname: str | None, name: str, file: str | None = None) -> ET.Element:
    case = ET.Element("testcase", {"name": name})
    if classname is not None:
        case.set("classname", classname)
    if file is not None:
        case.set("file", file)
    return case


def test_junit_case_node_id_needs_no_test_tree(tmp_path: Path) -> None:
    node = sel.junit_case_node_id
    assert (
        node(tmp_path, _case("tests.unit.scripts.test_heavy_tier_needed", "test_param[True-False-True]"))
        == "tests/unit/scripts/test_heavy_tier_needed.py::test_param[True-False-True]"
    )
    assert (
        node(tmp_path, _case("tests.unit.scripts.test_heavy_tier_needed.TestGroup", "test_x"))
        == "tests/unit/scripts/test_heavy_tier_needed.py::TestGroup::test_x"
    )
    assert node(tmp_path, _case("", "test_x", "tests/unit/core/test_engine.py")) == (
        "tests/unit/core/test_engine.py::test_x"
    )
    assert node(tmp_path, _case("", "test_x")) is None
    assert node(tmp_path, _case("tests.unit.b.test_sub.TestSub", "test_inherited", "tests/unit/a/test_base.py")) == (
        "tests/unit/b/test_sub.py::TestSub::test_inherited"
    )
    assert node(tmp_path, _case("tests.unit.b.test_sub.TestSub", "test_x", "")) == (
        "tests/unit/b/test_sub.py::TestSub::test_x"
    )


def test_junit_case_node_id_prefers_existing_module_file(tmp_path: Path) -> None:
    (tmp_path / "tests/unit/a").mkdir(parents=True)
    (tmp_path / "tests/unit/a/checks.py").write_text("", encoding="utf-8")
    assert sel.junit_case_node_id(tmp_path, _case("tests.unit.a.checks.TestG", "test_a")) == (
        "tests/unit/a/checks.py::TestG::test_a"
    )


def test_miss_and_build_map_derive_identical_node_ids(tmp_path: Path) -> None:
    report = tmp_path / "shard-0-junit.xml"
    report.write_text(
        "<testsuites><testsuite>"
        '<testcase classname="tests.unit.a.test_one.TestG" name="test_a"><failure/></testcase>'
        '<testcase classname="" name="test_orphan"><failure/></testcase>'
        '<testcase classname="tests.unit.zz.test_gone.TestG" name="test_b" file="tests/unit/a/test_one.py">'
        "<error/></testcase>"
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    failed, _, _ = sel.failed_node_ids([report], tmp_path)
    assert failed == sel._junit_failed_nodeids([report], tmp_path)
    assert failed == [
        "tests/unit/a/test_one.py::TestG::test_a",
        "tests/unit/zz/test_gone.py::TestG::test_b",
    ]


def test_failed_node_ids_counts_failures_and_errors(tmp_path: Path) -> None:
    report = tmp_path / "shard-0-junit.xml"
    _write_junit(
        report,
        [
            ("tests.unit.a.test_one", "test_ok", None),
            ("tests.unit.a.test_one", "test_broke", "failure"),
            ("tests.unit.b.test_two", "TestGroup::test_blew_up[param]", "error"),
            ("tests.unit.c.test_three", "test_skip", None),
        ],
    )
    failed, parsed, unparsed = sel.failed_node_ids([report], tmp_path)
    assert parsed == 1
    assert unparsed == []
    assert failed == [
        "tests/unit/a/test_one.py::test_broke",
        "tests/unit/b/test_two.py::TestGroup::test_blew_up[param]",
    ]


def test_shadow_misses_flags_failure_outside_selection() -> None:
    selected = ["tests/unit/a/test_one.py::test_keep"]
    failed = [
        "tests/unit/a/test_one.py::test_keep",
        "tests/unit/z/test_nine.py::test_broke",
    ]
    assert sel.shadow_misses(selected, failed) == ["tests/unit/z/test_nine.py::test_broke"]
    report = sel.miss_report(selected, failed)
    assert report == {
        "selected_count": 1,
        "failed": failed,
        "missed": ["tests/unit/z/test_nine.py::test_broke"],
        "would_escalate": True,
    }


def test_shadow_misses_covers_failure_by_node_or_file() -> None:
    failed = [
        "tests/unit/a/test_one.py::test_broke",
        "tests/unit/b/test_two.py::TestGroup::test_bad",
    ]
    assert sel.shadow_misses(["tests/unit/a/test_one.py::test_broke"], failed[:1]) == []
    assert sel.shadow_misses(["tests/unit/a/test_one.py"], failed[:1]) == []
    assert sel.shadow_misses(["tests/unit/b/test_two.py"], failed[1:]) == []
    assert sel.miss_report(failed, failed)["would_escalate"] is False


def test_misses_cli_reports_not_available_on_missing_inputs(tmp_path: Path) -> None:
    summary = tmp_path / "summary.md"
    payload = tmp_path / "misses.json"
    assert sel.main(["misses", "--json-out", str(payload), "--summary", str(summary)]) == 0
    body = summary.read_text(encoding="utf-8")
    assert "## Shadow selection misses" in body
    assert "not available" in body
    loaded = json.loads(payload.read_text(encoding="utf-8"))
    assert loaded["selected_count"] == 0
    assert loaded["failed"] == []
    assert loaded["missed"] == []
    assert loaded["would_escalate"] is None
    assert loaded["available"] is False
    assert "Would escalate on misses:" not in body


def test_misses_cli_lists_only_failures_outside_selection(tmp_path: Path) -> None:
    selection = tmp_path / "coverage-selection.json"
    selection.write_text(
        json.dumps({"selected": ["tests/unit/a/test_one.py::test_keep", "tests/unit/b/test_two.py"]}),
        encoding="utf-8",
    )
    report = tmp_path / "shard-1-junit.xml"
    _write_junit(
        report,
        [
            ("tests.unit.a.test_one", "test_keep", None),
            ("tests.unit.a.test_one", "test_flaky", "failure"),
            ("tests.unit.b.test_two", "TestGroup::test_bad", "failure"),
            ("tests.unit.z.test_nine", "test_broke", "failure"),
        ],
    )
    summary = tmp_path / "summary.md"
    payload = tmp_path / "misses.json"
    assert (
        sel.main(
            [
                "misses",
                "--selection-in",
                str(selection),
                "--junit",
                str(report),
                "--json-out",
                str(payload),
                "--summary",
                str(summary),
            ]
        )
        == 0
    )
    body = summary.read_text(encoding="utf-8")
    assert "## Shadow selection misses" in body
    assert "tests/unit/z/test_nine.py::test_broke" in body
    assert "tests/unit/a/test_one.py::test_flaky" in body
    assert "tests/unit/b/test_two.py::TestGroup::test_bad" not in body
    loaded = json.loads(payload.read_text(encoding="utf-8"))
    assert loaded["selected_count"] == 2
    assert loaded["missed"] == [
        "tests/unit/a/test_one.py::test_flaky",
        "tests/unit/z/test_nine.py::test_broke",
    ]
    assert loaded["would_escalate"] is True


def test_misses_cli_reports_clean_run_when_no_failures(tmp_path: Path) -> None:
    selection = tmp_path / "coverage-selection.json"
    selection.write_text(json.dumps({"selected": ["tests/unit/a/test_one.py::test_keep"]}), encoding="utf-8")
    report = tmp_path / "shard-0-junit.xml"
    _write_junit(report, [("tests.unit.a.test_one", "test_keep", None)])
    summary = tmp_path / "summary.md"
    payload = tmp_path / "misses.json"
    assert (
        sel.main(
            [
                "misses",
                "--selection-in",
                str(selection),
                "--junit",
                str(report),
                "--json-out",
                str(payload),
                "--summary",
                str(summary),
            ]
        )
        == 0
    )
    body = summary.read_text(encoding="utf-8")
    assert "## Shadow selection misses" in body
    assert "shadow selection of 1 test has nothing to miss" in body
    loaded = json.loads(payload.read_text(encoding="utf-8"))
    assert loaded["available"] is True
    assert loaded["missed"] == []
    assert loaded["would_escalate"] is False


def test_misses_cli_reports_not_available_when_shards_are_missing(tmp_path: Path) -> None:
    selection = tmp_path / "coverage-selection.json"
    selection.write_text(json.dumps({"selected": ["tests/unit/a/test_one.py::test_keep"]}), encoding="utf-8")
    reports = tmp_path / "junit"
    reports.mkdir()
    for shard in range(3):
        _write_junit(reports / f"shard-{shard}-junit.xml", [("tests.unit.a.test_one", "test_keep", None)])
    payload = tmp_path / "misses.json"
    base = ["misses", "--selection-in", str(selection), "--junit", str(reports), "--json-out", str(payload)]
    assert sel.main([*base, "--expected-shards", "4"]) == 0
    loaded = json.loads(payload.read_text(encoding="utf-8"))
    assert loaded["available"] is False
    assert "only 3 of 4 shard junit reports parsed" in loaded["reason"]
    assert sel.main([*base, "--expected-shards", "3"]) == 0
    assert json.loads(payload.read_text(encoding="utf-8"))["available"] is True


def test_shadow_misses_flags_unselected_sibling_node() -> None:
    selected = ["tests/unit/a/test_one.py::test_keep", "tests/unit/b/test_two.py"]
    assert sel.shadow_misses(selected, ["tests/unit/a/test_one.py::test_flaky"]) == [
        "tests/unit/a/test_one.py::test_flaky"
    ]
    assert sel.shadow_misses(selected, ["tests/unit/b/test_two.py::TestGroup::test_bad"]) == []


def test_misses_cli_reports_unavailable_on_corrupt_junit(tmp_path: Path) -> None:
    selection = tmp_path / "coverage-selection.json"
    selection.write_text(json.dumps({"selected": ["tests/unit/a/test_one.py::test_keep"]}), encoding="utf-8")
    valid = tmp_path / "shard-0-junit.xml"
    _write_junit(valid, [("tests.unit.a.test_one", "test_broke", "failure")])
    corrupt = tmp_path / "shard-1-junit.xml"
    corrupt.write_text("<testsuites><testsuite><testcase", encoding="utf-8")
    summary = tmp_path / "summary.md"
    payload = tmp_path / "misses.json"
    assert (
        sel.main(
            [
                "misses",
                "--selection-in",
                str(selection),
                "--junit",
                str(valid),
                "--junit",
                str(corrupt),
                "--json-out",
                str(payload),
                "--summary",
                str(summary),
            ]
        )
        == 0
    )
    body = summary.read_text(encoding="utf-8")
    assert "## Shadow selection misses" in body
    assert "not available" in body
    assert "Would escalate on misses:" not in body
    loaded = json.loads(payload.read_text(encoding="utf-8"))
    assert loaded["available"] is False
    assert loaded["missed"] == []
    assert loaded["would_escalate"] is None


def test_develop_added_tests_join_the_selection(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sel, "SELECTION_SHADOW", False)
    map_sha = _head(repo)
    _write(repo, "tests/unit/test_develop_added.py", "def test_added():\n    assert True\n")
    _commit_all(repo)
    base_sha = _head(repo)
    _write(repo, "benchbox/core/engine.py", "x = 2\n")
    _commit_all(repo)
    files = sel.changed_files(repo, base_sha)
    assert [entry.path for entry in files] == ["benchbox/core/engine.py"]
    result = sel.select_tests(repo, _mapping(repo, map_sha), files, base_sha)
    assert result["develop_changed_tests"] == ["tests/unit/test_develop_added.py"]
    assert "tests/unit/test_develop_added.py" in result["selected"]
    assert "tests/unit/core/test_engine.py::test_x" in result["selected"]
    assert result["escalate"] is False


def test_develop_changed_tests_exclude_files_missing_from_the_checkout(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sel, "SELECTION_SHADOW", False)
    map_sha = _head(repo)
    _write(repo, "tests/unit/test_develop_added.py", "def test_added():\n    assert True\n")
    _commit_all(repo)
    base_sha = _head(repo)
    (repo / "tests/unit/test_develop_added.py").unlink()
    _write(repo, "benchbox/core/engine.py", "x = 2\n")
    _commit_all(repo)
    files = sel.changed_files(repo, base_sha)
    result = sel.select_tests(repo, _mapping(repo, map_sha), files, base_sha)
    assert result["develop_changed_tests"] == []
