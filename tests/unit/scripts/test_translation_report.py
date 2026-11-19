from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.sql_execution_gate import emit, translation_report

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.test", *args],
        cwd=root,
        check=True,
        capture_output=True,
        encoding="utf-8",
    ).stdout.strip()


def test_a_tree_compared_with_itself_reports_nothing() -> None:
    emitted = {"questdb": {"tpchavoc": {"18_v10": "SELECT 1"}}, "sqlite": {"tpch": {"1": "SELECT 2"}}}
    assert translation_report.diff_translations(emitted, emitted) == []


def test_changed_added_and_removed_entries_are_reported_in_a_stable_order() -> None:
    before = {
        "questdb": {"tpchavoc": {"2_v1": "a", "10_v1": "b", "18_v10": "x"}},
        "duckdb": {"tpch": {"1": "same"}},
        "gone": {"tpch": {"1": "z"}},
    }
    after = {
        "questdb": {"tpchavoc": {"2_v1": "a2", "10_v1": "b", "18_v10": "y", "19_v1": "new"}},
        "duckdb": {"tpch": {"1": "same"}},
        "fresh": {"tpch": {"1": "z"}},
    }
    changes = translation_report.diff_translations(before, after)
    assert [(c["platform"], c["benchmark"], c["query"]) for c in changes] == [
        ("fresh", "tpch", "1"),
        ("gone", "tpch", "1"),
        ("questdb", "tpchavoc", "2_v1"),
        ("questdb", "tpchavoc", "18_v10"),
        ("questdb", "tpchavoc", "19_v1"),
    ]
    by_query = {(c["platform"], c["query"]): c for c in changes}
    assert by_query[("fresh", "1")]["before"] is None and by_query[("fresh", "1")]["after"] == "z"
    assert by_query[("gone", "1")]["after"] is None
    assert by_query[("questdb", "18_v10")]["before"] == "x" and by_query[("questdb", "18_v10")]["after"] == "y"


def test_a_benchmark_error_entry_is_a_reportable_change() -> None:
    before = {"spark": {"tpch": {"*": "<RuntimeError: old>"}}}
    after = {"spark": {"tpch": {"*": "<RuntimeError: new>"}}}
    (change,) = translation_report.diff_translations(before, after)
    assert change["query"] == "*"


def test_checkout_swaps_in_the_wheel_and_pin_of_another_revision(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    vendor = repo / "benchbox" / "_vendor"
    vendor.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    (vendor / "sqlglot.json").write_text('{"version": "1.0.0.1"}')
    (vendor / "benchbox_sqlglot-1.0.0.1-py3-none-any.whl.zip").write_text("one")
    (repo / "marker.txt").write_text("old")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "one")
    old = git(repo, "rev-parse", "HEAD")
    (vendor / "sqlglot.json").write_text('{"version": "2.0.0.1"}')
    (vendor / "benchbox_sqlglot-1.0.0.1-py3-none-any.whl.zip").unlink()
    (vendor / "benchbox_sqlglot-2.0.0.1-py3-none-any.whl.zip").write_text("two")
    (repo / "marker.txt").write_text("new")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "two")
    new = git(repo, "rev-parse", "HEAD")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    with translation_report.checkout(repo, new, scratch, "swapped", vendor_from=old) as path:
        names = sorted(p.name for p in (path / "benchbox" / "_vendor").iterdir())
        assert names == ["benchbox_sqlglot-1.0.0.1-py3-none-any.whl.zip", "sqlglot.json"]
        assert '"1.0.0.1"' in (path / "benchbox" / "_vendor" / "sqlglot.json").read_text(encoding="utf-8")
        assert (path / "marker.txt").read_text(encoding="utf-8") == "new"
    assert not path.exists()
    with translation_report.checkout(repo, new, scratch, "plain") as path:
        assert (path / "benchbox" / "_vendor" / "benchbox_sqlglot-2.0.0.1-py3-none-any.whl.zip").is_file()


def test_every_sql_platform_is_in_exactly_one_group_and_a_dedicated_query_path_stays_separate() -> None:
    groups = emit._platform_groups()
    members = [platform for label in groups for platform in label.split(",")]
    assert len(members) == len(set(members))
    assert "questdb" in groups and groups["questdb"].dialect == "questdb"
    assert "lakesail" in groups and groups["lakesail"].dialect == "spark"
    assert groups["lakesail"].platforms == ["lakesail"]
    assert list(groups) == sorted(groups)


def test_seeded_benchmarks_emit_identical_queries_every_time(tmp_path: Path) -> None:
    first = emit._benchmark_instance("nyctaxi", 0.01, tmp_path / "a")
    second = emit._benchmark_instance("nyctaxi", 0.01, tmp_path / "b")
    assert first.get_queries(dialect="duckdb") == second.get_queries(dialect="duckdb")
