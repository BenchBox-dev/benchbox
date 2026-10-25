from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from ci_units import UNITS, classify_units, load_unit_rules, write_github_output
from path_filter_decision import classify_paths, load_rules

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
RULES_PATH = REPO_ROOT / ".github" / "ci-units.yml"
FILTER_RULES_PATH = REPO_ROOT / ".github" / "path-filters.yml"
READERS_PATH = REPO_ROOT / "_project" / "inert-doc-readers.json"
SAFE_PROJECT_FOLDERS = ("notes", "blind-spots", "handoffs", "research", "planning", "decisions")


@pytest.fixture(scope="module")
def rules() -> dict[str, list[str]]:
    return load_unit_rules(RULES_PATH)


def needed(paths: list[str], rules: dict[str, list[str]]) -> set[str]:
    decision = classify_units(paths, rules)
    return {unit for unit, on in decision["units"].items() if on}


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        (["benchbox/core/tpch/generator.py"], {"core"}),
        (["tests/unit/core/test_x.py"], {"core"}),
        (["landing/prompts/catalog.yaml"], {"docs", "tooling"}),
        (["website/src/pages/index.astro"], {"core"}),
        (["results-explorer/src/App.tsx"], {"core"}),
        (["_project/scripts/explorer_pipeline/pipeline.py"], {"explorer", "tooling"}),
        (["results-data/bundles/x.json"], {"explorer", "results-data"}),
        (["docs/guides/intro.md"], {"docs"}),
        (["docs/conf.py"], {"core", "docs"}),
        (["scripts/check_windows_antipatterns.py"], {"tooling"}),
        (["scripts/site_inputs.py"], {"docs", "tooling"}),
        (["Makefile"], {"tooling"}),
        (["AGENTS.md"], {"docs", "tooling"}),
        (["_project/decisions/foo.md"], {"docs"}),
    ],
)
def test_single_unit_ownership(paths: list[str], expected: set[str], rules: dict[str, list[str]]) -> None:
    assert needed(paths, rules) == expected


@pytest.mark.parametrize(
    ("paths", "extra"),
    [
        (["benchbox/core/results/schema.py"], {"explorer", "results-data"}),
        (["benchbox/cli/main.py"], {"docs"}),
        (["benchbox/core/platform_registry.py"], {"docs"}),
        (["pyproject.toml"], set()),
    ],
)
def test_widened_triggers(paths: list[str], extra: set[str], rules: dict[str, list[str]]) -> None:
    result = needed(paths, rules)
    assert extra <= result
    assert "core" in result


def test_independent_units_do_not_pull_core(rules: dict[str, list[str]]) -> None:
    for path in ("docs/index.rst", "landing/prompts/catalog.yaml", "results-data/README.md"):
        assert "core" not in needed([path], rules), path


def test_unowned_path_fails_closed_to_core(rules: dict[str, list[str]]) -> None:
    decision = classify_units(["brand-new-top-level/thing.bin"], rules)
    assert decision["units"]["core"] is True
    assert decision["unowned_paths"] == ["brand-new-top-level/thing.bin"]


def test_empty_change_set_runs_every_unit(rules: dict[str, list[str]]) -> None:
    decision = classify_units([], rules)
    assert decision["run_all"] is True
    assert all(decision["units"][unit] for unit in UNITS)


@pytest.mark.parametrize("path", [".github/workflows/ci.yml", ".github/ci-units.yml", "scripts/ci_units.py"])
def test_self_protection_runs_every_unit(path: str, rules: dict[str, list[str]]) -> None:
    decision = classify_units([path], rules)
    assert decision["run_all"] is True
    assert all(decision["units"][unit] for unit in UNITS)


def test_code_tests_needed_covers_core_and_tooling(rules: dict[str, list[str]]) -> None:
    assert classify_units(["scripts/x.py"], rules)["code_tests_needed"] is True
    assert classify_units(["benchbox/x.py"], rules)["code_tests_needed"] is True
    assert classify_units(["docs/guides/intro.md"], rules)["code_tests_needed"] is False


def test_rules_file_declares_every_unit_and_nothing_else(rules: dict[str, list[str]]) -> None:
    assert set(rules) == {*UNITS, "all-units"}


def test_load_rejects_missing_or_unknown_keys(tmp_path: Path) -> None:
    bad = tmp_path / "units.yml"
    bad.write_text('core:\n  - "a/**"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="missing unit keys"):
        load_unit_rules(bad)
    good = RULES_PATH.read_text(encoding="utf-8") + '\nextra-unit:\n  - "x/**"\n'
    bad.write_text(good, encoding="utf-8")
    with pytest.raises(ValueError, match="unknown unit keys"):
        load_unit_rules(bad)


def test_github_output_lines(tmp_path: Path, rules: dict[str, list[str]]) -> None:
    out = tmp_path / "out"
    write_github_output(out, classify_units(["docs/guides/intro.md"], rules))
    lines = dict(line.split("=", 1) for line in out.read_text(encoding="utf-8").splitlines())
    assert lines["unit-docs"] == "true"
    assert lines["unit-core"] == "false"
    assert lines["code-tests-needed"] == "false"
    assert lines["run-all-units"] == "false"


def test_cli_reads_changed_file_and_writes_json(tmp_path: Path) -> None:
    changed = tmp_path / "changed.txt"
    changed.write_text("docs/index.rst\n", encoding="utf-8")
    json_out = tmp_path / "decision.json"
    subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "ci_units.py"),
            "--changed-file",
            str(changed),
            "--json-out",
            str(json_out),
        ],
        check=True,
        cwd=REPO_ROOT,
    )
    decision = json.loads(json_out.read_text(encoding="utf-8"))
    assert decision["units"]["docs"] is True
    assert decision["units"]["core"] is False


def test_cli_diff_failure_runs_every_unit(tmp_path: Path) -> None:
    out = tmp_path / "out"
    subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "ci_units.py"),
            "--base-ref",
            "definitely-not-a-ref-0000",
            "--github-output",
            str(out),
        ],
        check=True,
        cwd=REPO_ROOT,
    )
    lines = dict(line.split("=", 1) for line in out.read_text(encoding="utf-8").splitlines())
    assert all(lines[f"unit-{unit}"] == "true" for unit in UNITS)


DOCS_ONLY_MARKDOWN = [
    "DISCLAIMER.md",
    "docs/internal/comment-policy.md",
    "docs/guides/intro.md",
    "_project/todo-db-export/index.md",
    *(f"_project/{folder}/unread.md" for folder in SAFE_PROJECT_FOLDERS),
]

TEST_READ_MARKDOWN = [
    "README.md",
    "CHANGELOG.md",
    "AGENTS.md",
    "CLAUDE.md",
    "docs/agent/review-protocol.md",
    "docs/agent/identity-instruction-boundary.md",
    "docs/agent/attribution-surfaces.md",
    "docs/internal/release-guide.md",
    "docs/publish-exclusions.txt",
    "_project/decisions/single-repo-migration.md",
    "_project/notes/phase-2-requests.md",
]


@pytest.mark.parametrize("path", DOCS_ONLY_MARKDOWN)
def test_unread_markdown_routes_to_the_docs_unit_only(path: str, rules: dict[str, list[str]]) -> None:
    decision = classify_units([path], rules)
    assert needed([path], rules) == {"docs"}
    assert decision["code_tests_needed"] is False
    assert decision["unowned_paths"] == []


@pytest.mark.parametrize("path", TEST_READ_MARKDOWN)
def test_markdown_read_by_tests_still_needs_code_tests(path: str, rules: dict[str, list[str]]) -> None:
    assert classify_units([path], rules)["code_tests_needed"] is True


def test_markdown_only_change_set_skips_code_tests(rules: dict[str, list[str]]) -> None:
    decision = classify_units(["DISCLAIMER.md", "_project/todo-db-export/index.md", "docs/guides/intro.md"], rules)
    assert decision["code_tests_needed"] is False
    assert decision["run_all"] is False


def test_markdown_mixed_with_code_still_needs_code_tests(rules: dict[str, list[str]]) -> None:
    assert classify_units(["DISCLAIMER.md", "scripts/x.py"], rules)["code_tests_needed"] is True


def _tracked_documents() -> list[str]:
    if not FILTER_RULES_PATH.is_file():
        pytest.skip("path filters are absent from this tree")
    proc = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z"],
        text=True,
        capture_output=True,
        timeout=120,
    )
    if proc.returncode != 0:
        pytest.skip("not a git checkout")
    return sorted(
        path
        for path in proc.stdout.split("\0")
        if path.endswith((".md", ".txt")) and not path.startswith(("results-data/", "tests/", "benchbox/"))
    )


def test_every_test_read_document_needs_code_tests(rules: dict[str, list[str]]) -> None:
    if not READERS_PATH.is_file():
        pytest.skip("the reader index is absent from this tree")
    readers = json.loads(READERS_PATH.read_text(encoding="utf-8"))
    assert readers
    unrouted = sorted(
        path
        for path in readers
        if not path.startswith("results-data/") and not classify_units([path], rules)["code_tests_needed"]
    )
    assert not unrouted, f"documents read by tests but routed away from code tests: {unrouted}"


def test_ci_units_and_path_filters_agree_on_which_documents_are_code(rules: dict[str, list[str]]) -> None:
    filter_rules = load_rules(FILTER_RULES_PATH)
    disagreements = []
    for path in _tracked_documents():
        by_units = classify_units([path], rules)["code_tests_needed"]
        decision = classify_paths([path], filter_rules)
        by_filters = bool(decision["needs_code_ci"] or decision["skill_integrity_needed"])
        if by_units != by_filters:
            disagreements.append((path, by_units, by_filters))
    assert not disagreements, f"(path, ci-units code, path-filters code): {disagreements[:10]}"
