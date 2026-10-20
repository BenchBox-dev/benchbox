from __future__ import annotations

import json
import os
import subprocess
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
    return {
        "version": sel.MAP_VERSION,
        "sha": sha,
        "files": {
            "benchbox/core/engine.py": {
                "1": ["tests/unit/core/test_engine.py::test_x"],
            },
            "benchbox/base.py": {
                "4": ["tests/test_benchbox.py::test_translate"],
                "5": ["tests/test_benchbox.py::test_translate"],
            },
        },
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


def test_lookup_hits_separates_covered_and_missed_lines() -> None:
    index = {"benchbox/base.py": {"4": ["tests/test_benchbox.py::test_translate"], "5": []}}
    hits, missed = sel.lookup_hits(index, "benchbox/base.py", [4, 5, 6])
    assert hits == {"tests/test_benchbox.py::test_translate"}
    assert missed == [5, 6]
    assert sel.lookup_hits(index, "benchbox/other.py", [1]) == (set(), [1])
    assert sel.lookup_hits({}, "benchbox/base.py", [4]) == (set(), [4])


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
    assert mapping["files"]["benchbox/base.py"] == {
        "4": ["tests/test_benchbox.py::test_translate"],
        "5": ["tests/test_benchbox.py::test_translate"],
    }
    assert "benchbox/core/engine.py" not in mapping["files"]


def test_build_map_skips_files_outside_the_repo(tmp_path: Path, repo: Path) -> None:
    outside = _coverage_data(tmp_path, {"/elsewhere/other.py": {1: ["tests/test_x.py::test_y|run"]}})
    assert sel.build_map([outside], repo, _head(repo))["files"] == {}


def test_read_map_rejects_bad_envelopes(tmp_path: Path, repo: Path) -> None:
    good = _mapping(repo, _head(repo))
    path = tmp_path / "map.json"
    path.write_text(json.dumps(good), encoding="utf-8")
    assert sel.read_map(path) == good
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(sel.SelectionError):
        sel.read_map(path)
    path.write_text(json.dumps({**good, "version": 999}), encoding="utf-8")
    with pytest.raises(sel.SelectionError):
        sel.read_map(path)
    path.write_text(json.dumps({**good, "sha": "xyz"}), encoding="utf-8")
    with pytest.raises(sel.SelectionError):
        sel.read_map(path)
    with pytest.raises(sel.SelectionError):
        sel.read_map(tmp_path / "absent.json")


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


def test_selection_unites_coverage_changed_tests_and_fallback(repo: Path) -> None:
    map_sha = _head(repo)
    _write(repo, "benchbox/core/engine.py", "x = 2\n")
    _write(repo, "tests/unit/test_unrelated.py", "y = 2\n")
    _commit_all(repo)
    base_sha = _head(repo)
    files = sel.changed_files(repo, map_sha)
    result = sel.select_tests(repo, _mapping(repo, map_sha), files, base_sha)
    assert result["escalate"] is False
    assert "tests/unit/core/test_engine.py::test_x" in result["selected"]
    assert "tests/unit/test_unrelated.py" in result["selected"]
    assert "tests/unit/core/test_engine.py" in result["fallback_tests"]
    assert result["map_sha"] == map_sha
    assert result["behind_commits"] == 1


def test_pure_addition_probes_neighbouring_old_lines(repo: Path) -> None:
    map_sha = _head(repo)
    _write(repo, "benchbox/base.py", "VALUE = 1\n\n\ndef translate(value):\n    return value + 1\n# trailing note\n")
    _commit_all(repo)
    files = sel.changed_files(repo, map_sha)
    (entry,) = [entry for entry in files if entry.path == "benchbox/base.py"]
    assert entry.old_ranges == [(5, 0)]
    result = sel.select_tests(repo, _mapping(repo, map_sha), files, _head(repo))
    assert "tests/test_benchbox.py::test_translate" in result["selected"]


def test_uncovered_lines_add_importing_tests(repo: Path) -> None:
    map_sha = _head(repo)
    _write(repo, "benchbox/core/engine.py", "x = 1\ny = 2\n")
    _write(repo, "tests/unit/core/test_engine.py", "from benchbox.core.engine import x\n")
    _commit_all(repo)
    files = sel.changed_files(repo, map_sha)
    result = sel.select_tests(repo, _mapping(repo, map_sha), files, _head(repo))
    assert result["escalate"] is False
    assert "tests/unit/core/test_engine.py" in result["importer_tests"]
    assert "tests/unit/core/test_engine.py" in result["selected"]
    assert result["uncovered_sources"] == ["benchbox/core/engine.py"]


def test_new_source_file_selects_fallback_and_importers(repo: Path) -> None:
    map_sha = _head(repo)
    _write(repo, "benchbox/core/fresh.py", "x = 1\n")
    _write(repo, "tests/unit/core/test_fresh.py", "from benchbox.core.fresh import x\n")
    _commit_all(repo)
    files = sel.changed_files(repo, map_sha)
    result = sel.select_tests(repo, _mapping(repo, map_sha), files, _head(repo))
    assert result["escalate"] is False
    assert "tests/unit/core/test_fresh.py" in result["selected"]


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


def test_stale_map_escalates_past_the_named_limit(repo: Path) -> None:
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


def test_selection_over_the_named_limit_escalates(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    map_sha = _head(repo)
    _write(repo, "benchbox/core/engine.py", "x = 2\n")
    _commit_all(repo)
    files = sel.changed_files(repo, map_sha)
    monkeypatch.setattr(sel, "MAX_SELECTED_NODEIDS", 1)
    result = sel.select_tests(repo, _mapping(repo, map_sha), files, _head(repo))
    assert result["escalate"] is True
    assert "exceeds" in result["reason"]


def test_non_python_change_escalates_only_for_code_routed_trees(repo: Path) -> None:
    map_sha = _head(repo)
    _write(repo, "docs/guide.md", "# changed\n")
    _commit_all(repo)
    files = sel.changed_files(repo, map_sha)
    base_sha = _head(repo)
    coded = sel.select_tests(repo, _mapping(repo, map_sha), files, base_sha, decision={"needs_code_ci": True})
    assert coded["escalate"] is True
    assert "docs/guide.md" in coded["reason"]
    routed_away = sel.select_tests(repo, _mapping(repo, map_sha), files, base_sha, decision={"needs_code_ci": False})
    assert routed_away["escalate"] is False
    unknown = sel.select_tests(repo, _mapping(repo, map_sha), files, base_sha)
    assert unknown["escalate"] is True


def test_deleted_source_still_matches_coverage_by_old_path(repo: Path) -> None:
    map_sha = _head(repo)
    (repo / "benchbox/core/engine.py").unlink()
    _commit_all(repo)
    files = sel.changed_files(repo, map_sha)
    result = sel.select_tests(repo, _mapping(repo, map_sha), files, _head(repo))
    assert result["escalate"] is False
    assert "tests/unit/core/test_engine.py::test_x" in result["selected"]


def test_select_cli_writes_nodeids_and_json(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    map_sha = _head(repo)
    _write(repo, "benchbox/core/engine.py", "x = 2\n")
    _commit_all(repo)
    map_path = tmp_path / "map.json"
    map_path.write_text(json.dumps(_mapping(repo, map_sha)), encoding="utf-8")
    nodeids = tmp_path / "nodeids.txt"
    payload = tmp_path / "selection.json"
    monkeypatch.chdir(repo)
    assert (
        sel.main(
            [
                "select",
                "--map",
                str(map_path),
                "--base-sha",
                _head(repo),
                "--nodeids-out",
                str(nodeids),
                "--json-out",
                str(payload),
                "--repo-root",
                str(repo),
            ]
        )
        == 0
    )
    assert "tests/unit/core/test_engine.py::test_x" in nodeids.read_text(encoding="utf-8").splitlines()
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
    assert result["status"] == "missing"
    assert result["sha"] is None
    assert "gh is not installed" in result["reason"]


def test_fetch_map_skips_runs_that_are_not_base_ancestors(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sel, "_github_slug", lambda _repo: "o/r")
    monkeypatch.setattr(
        sel, "_list_map_artifacts", lambda _repo, _slug, _prefix: [("t2-coverage-map-abc", 7, "2026-10-20T00:00:00Z")]
    )
    monkeypatch.setattr(sel, "_run_head_sha", lambda _repo, _slug, _run: "a" * 40)
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
