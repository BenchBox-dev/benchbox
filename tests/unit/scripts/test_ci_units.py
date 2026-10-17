from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from ci_units import UNITS, classify_units, load_unit_rules, write_github_output

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
RULES_PATH = REPO_ROOT / ".github" / "ci-units.yml"


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
        (["landing/prompts/catalog.yaml"], {"docs", "landing"}),
        (["website/src/pages/index.astro"], {"core"}),
        (["results-explorer/src/App.tsx"], {"core"}),
        (["_project/scripts/explorer_pipeline/pipeline.py"], {"explorer", "tooling"}),
        (["results-data/bundles/x.json"], {"explorer", "results-data"}),
        (["docs/guides/intro.md"], {"docs"}),
        (["docs/conf.py"], {"core", "docs"}),
        (["scripts/check_windows_antipatterns.py"], {"tooling"}),
        (["scripts/site_inputs.py"], {"docs", "tooling"}),
        (["Makefile"], {"tooling"}),
        (["AGENTS.md"], {"tooling"}),
        (["_project/decisions/foo.md"], {"tooling"}),
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
    assert classify_units(["landing/prompts/catalog.yaml"], rules)["code_tests_needed"] is False


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
    write_github_output(out, classify_units(["landing/prompts/catalog.yaml"], rules))
    lines = dict(line.split("=", 1) for line in out.read_text(encoding="utf-8").splitlines())
    assert lines["unit-landing"] == "true"
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
