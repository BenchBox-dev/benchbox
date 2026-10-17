from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

WORKFLOWS = Path(__file__).resolve().parents[3] / ".github" / "workflows"
REQUIRED_CHECKS = ("core", "results-data", "tooling", "explorer", "docs", "landing")
LANE_JOBS = (
    "site-paths",
    "explorer-tokens",
    "explorer-vitest",
    "parity-check",
    "explorer-e2e",
    "site-theme-tokens",
    "docs-build",
    "public-site-visual-regression",
    "site-build",
    "site-parity",
    "explorer",
    "docs",
    "landing",
)
RETIRED_JOBS = ("visual-inputs", "public-site-visual-astro-dry-run", "example-validation", "spellcheck")


def _load(name: str) -> dict[str, Any]:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    triggers = workflow.get("on", workflow.get(True))
    if isinstance(triggers, dict):
        return triggers
    return dict.fromkeys([triggers] if isinstance(triggers, str) else triggers)


def _needs(job: dict[str, Any]) -> list[str]:
    needs = job.get("needs", [])
    return [needs] if isinstance(needs, str) else list(needs)


def _step(job: dict[str, Any], name: str) -> dict[str, Any]:
    return next(step for step in job["steps"] if step.get("name") == name)


def test_lane_runs_on_every_pull_request_that_ci_runs_on() -> None:
    lane = _triggers(_load("site-docs.yml"))["pull_request"]
    core = _triggers(_load("ci.yml"))["pull_request"]

    assert "paths" not in lane
    assert "paths-ignore" not in lane
    assert lane["branches-ignore"] == core["branches-ignore"]
    assert set(lane["types"]) == set(core["types"]) - {"converted_to_draft"}


def test_each_required_check_comes_from_exactly_one_pull_request_workflow() -> None:
    producers: dict[str, list[str]] = {name: [] for name in REQUIRED_CHECKS}
    for path in sorted(WORKFLOWS.glob("*.yml")):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        if "pull_request" not in _triggers(workflow):
            continue
        for job_id, job in workflow.get("jobs", {}).items():
            name = job.get("name", job_id)
            if name in producers:
                producers[name].append(path.name)

    assert producers == {
        "core": ["ci.yml"],
        "results-data": ["ci.yml"],
        "tooling": ["ci.yml"],
        "explorer": ["site-docs.yml"],
        "docs": ["site-docs.yml"],
        "landing": ["site-docs.yml"],
    }


def test_site_and_docs_jobs_live_only_in_the_lane() -> None:
    core_jobs = _load("ci.yml")["jobs"]
    lane_jobs = _load("site-docs.yml")["jobs"]

    assert tuple(lane_jobs) == LANE_JOBS
    assert not set(LANE_JOBS) & set(core_jobs)
    assert not set(RETIRED_JOBS) & (set(core_jobs) | set(lane_jobs))
    for name, job in lane_jobs.items():
        assert "ci-paths" not in _needs(job), name


def test_lane_summaries_classify_with_the_lane_classifier() -> None:
    jobs = _load("site-docs.yml")["jobs"]
    expected_needs = {
        "explorer": ["site-paths", "explorer-tokens", "explorer-vitest", "parity-check", "explorer-e2e"],
        "docs": ["site-paths", "docs-build"],
        "landing": ["site-paths", "site-theme-tokens", "site-build"],
    }
    for name, needs in expected_needs.items():
        run = _step(jobs[name], f"Aggregate {name} result")["run"]
        assert _needs(jobs[name]) == needs
        assert "--always site-paths" in run
        assert "ci-paths" not in run

    docs_run = _step(jobs["docs"], "Aggregate docs result")["run"]
    assert "--expect docs-build=${{ needs.site-paths.outputs.site-changed == 'true' }}" in docs_run


def test_docs_build_runs_the_example_and_docs_reference_checks_after_a_failure() -> None:
    job = _load("site-docs.yml")["jobs"]["docs-build"]
    examples = ("Validate example file references", "Check example file syntax", "Validate docs references")
    for name in examples:
        step = _step(job, name)
        assert step["if"].startswith("${{ !cancelled()"), name
        assert "continue-on-error" not in step, name
        assert step["timeout-minutes"] > 0, name

    references = _step(job, "Validate docs references")
    assert "needs.site-paths.outputs.docs-paths-changed == 'true'" in references["if"]
    assert references["run"] == "make docs-validate"


def test_docs_build_still_blocks_on_screenshot_sync_and_the_prompts_catalog() -> None:
    job = _load("site-docs.yml")["jobs"]["docs-build"]
    generate = _step(job, "Generate documentation inputs")
    assert generate["run"] == "make docs-generate"
    assert "if" not in generate
    assert "continue-on-error" not in generate

    makefile = (WORKFLOWS.parents[1] / "make" / "documentation.mk").read_text(encoding="utf-8")
    prerequisites = makefile.split("\ndocs-generate:", 1)[1].split("\n", 1)[0].split()
    assert {"prompt-quickstarts-check", "docs-images-check"} <= set(prerequisites)
    assert "\t@uv run -- python scripts/generate_landing_quickstarts.py --check" in makefile
    assert "docs-images-check:\n\t@uv run -- python scripts/validate_visualization_images.py" in makefile


def test_codespell_stays_advisory_and_scans_the_clean_checkout() -> None:
    job = _load("site-docs.yml")["jobs"]["docs-build"]
    names = [step.get("name") for step in job["steps"]]
    codespell = _step(job, "Run codespell")

    assert codespell["continue-on-error"] is True
    assert names.index("Run codespell") < names.index("Install dependencies")
    assert names.index("Run codespell") < names.index("Generate documentation inputs")


def test_docs_reference_check_left_the_core_content_guard() -> None:
    content_guard = _load("ci.yml")["jobs"]["content-guard"]
    assert "Validate docs references" not in [step.get("name") for step in content_guard["steps"]]


def test_patch_safety_check_runs_in_core_lint() -> None:
    lint = _load("ci.yml")["jobs"]["code-lint"]
    names = [step.get("id") for step in lint["steps"]]
    step = next(step for step in lint["steps"] if step.get("id") == "guard-patch-safety")

    assert step["run"] == "uv run -- python scripts/check_patch_safety.py"
    assert step["continue-on-error"] is True
    assert names.index("guard-patch-safety") < names.index("lint-guard-summary")
