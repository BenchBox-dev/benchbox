from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "seed-corpus.yml"

RELEASE_HEAD_RE = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.+-]+)?$")


def _create_pr_steps() -> dict[str, str]:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["create-pr"]["steps"]
    return {step["name"]: step.get("run", "") for step in steps if "name" in step}


def _rendered_head_branch(commit_script: str) -> str:
    match = re.search(r'BRANCH="([^"]+)"', commit_script)
    assert match is not None, "could not find the BRANCH= assignment in the commit step"
    branch = match.group(1)
    branch = branch.replace("${{ github.run_number }}", "123")
    return branch.replace("${DATE}", "20260710")


def _workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    on = workflow.get("on", workflow.get(True))
    assert on is not None, "seed-corpus.yml has no `on:` block"
    return on


def test_seed_corpus_is_scheduled_monthly_and_still_dispatchable() -> None:
    on = _triggers(_workflow())
    crons = [entry["cron"] for entry in on["schedule"]]
    assert "0 7 1 * *" in crons
    assert "workflow_dispatch" in on


def test_schedule_runs_the_full_matrix() -> None:
    selected = _workflow()["jobs"]["generate-seed-corpus"]["env"]["SELECTED"]
    assert "github.event_name == 'schedule'" in selected


def test_supported_matrix_platforms_per_cohort() -> None:
    entries = _workflow()["jobs"]["generate-seed-corpus"]["strategy"]["matrix"]["include"]
    cohorts: dict[tuple[str, str], set[str]] = {}
    for entry in entries:
        assert "cloud" not in entry["platform"]
        assert entry["extras"] in {"duckdb", "datafusion", "polars", "clickhouse-local"}
        cohorts.setdefault((entry["benchmark"], entry["scale_factor"]), set()).add(entry["platform"])

    assert cohorts == {
        ("tpch", "0.01"): {"duckdb", "datafusion", "clickhouse-local"},
        ("tpch", "0.1"): {"duckdb", "datafusion", "clickhouse-local"},
        ("tpch", "1.0"): {"duckdb", "clickhouse-local", "polars-df"},
        ("tpcds", "1"): {"duckdb", "datafusion", "clickhouse-local"},
        ("ssb", "0.01"): {"duckdb", "datafusion", "polars-df"},
        ("ssb", "0.1"): {"duckdb", "datafusion", "polars-df"},
    }
    assert all(len(platforms) == 3 for platforms in cohorts.values())


def test_ineligible_seed_cells_are_absent_from_the_matrix() -> None:
    entries = _workflow()["jobs"]["generate-seed-corpus"]["strategy"]["matrix"]["include"]
    cells = {(entry["benchmark"], entry["platform"], entry["scale_factor"]) for entry in entries}

    assert ("tpch", "polars-df", "0.01") not in cells
    assert ("tpch", "polars-df", "0.1") not in cells
    assert ("tpch", "datafusion", "1.0") not in cells


def test_seed_corpus_local_jobs_do_not_reference_cloud_credentials() -> None:
    workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8").lower()
    assert "clickhouse-cloud" not in workflow_text
    assert "clickhouse_cloud" not in workflow_text
    assert "cloud_host" not in workflow_text
    assert "cloud_password" not in workflow_text
    assert "matrix.optional" not in workflow_text


def test_tpcds_matrix_jobs_run_with_official_seeded_command() -> None:
    workflow = _workflow()
    entries = workflow["jobs"]["generate-seed-corpus"]["strategy"]["matrix"]["include"]
    assert all(
        entry["benchmark"] == "tpcds" and entry["scale_factor"] == "1"
        for entry in entries
        if entry["benchmark"] == "tpcds"
    )

    run_step = next(
        step["run"] for step in workflow["jobs"]["generate-seed-corpus"]["steps"] if step.get("name") == "Run benchmark"
    )
    assert 'if [ "${{ matrix.benchmark }}" = "tpcds" ]; then' in run_step
    assert "command+=(--official --seed 42)" in run_step
    assert '"${command[@]}"' in run_step


def test_seed_corpus_pr_targets_develop() -> None:
    run = _create_pr_steps()["Open pull request"]

    assert "--base develop" in run
    assert "--base main" not in run
    assert "--base release" not in run


def test_seed_corpus_pr_regenerates_and_stages_inventory() -> None:
    steps = _workflow()["jobs"]["create-pr"]["steps"]
    names = [step.get("name") for step in steps]
    regenerate_index = names.index("Regenerate corpus inventory")
    stage_index = names.index("Check for new corpus changes")

    assert regenerate_index < stage_index
    assert "scripts/generate_corpus_inventory.py --write" in steps[regenerate_index]["run"]
    stage_script = steps[stage_index]["run"]
    assert "results-data/bundles/" in stage_script
    assert "results-data/corpus-inventory.json" in stage_script


def test_seed_corpus_head_branch_can_never_target_the_release_branch() -> None:
    steps = _create_pr_steps()
    head = _rendered_head_branch(steps["Create PR branch and commit"])

    assert head.startswith("chore/seed-corpus-")
    assert not RELEASE_HEAD_RE.match(head)


def test_seed_corpus_pr_body_does_not_escape_backticks() -> None:
    run = _create_pr_steps()["Open pull request"]
    body = run.split("<<'EOF'", 1)[1].split("\nEOF", 1)[0]

    assert "`" in body, "sanity: the body is expected to contain backticks"
    assert "\\`" not in body


def test_release_head_regex_matches_real_release_branches() -> None:
    assert RELEASE_HEAD_RE.match("v0.3.1")
    assert RELEASE_HEAD_RE.match("v1.2.3-rc.1")
    assert not RELEASE_HEAD_RE.match("chore/seed-corpus-20260710-123")
