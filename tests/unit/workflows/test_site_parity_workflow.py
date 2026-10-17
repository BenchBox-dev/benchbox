from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).parents[3]
JOB = "site-parity"


def _jobs() -> dict[str, Any]:
    return yaml.safe_load((ROOT / ".github/workflows/site-docs.yml").read_text(encoding="utf-8"))["jobs"]


def _needs(job: dict[str, Any]) -> list[str]:
    needs = job.get("needs", [])
    return [needs] if isinstance(needs, str) else list(needs)


def _recipe(target: str) -> str:
    text = (ROOT / "make/documentation.mk").read_text(encoding="utf-8")
    return text.split(f".PHONY: {target}\n{target}:", 1)[1].split("\n\n", 1)[0]


def test_parity_job_is_gated_on_its_own_path_filter_and_never_required() -> None:
    jobs = _jobs()

    assert jobs[JOB]["if"] == "${{ needs.site-paths.outputs.site-parity-needed == 'true' }}"
    assert "continue-on-error" not in jobs[JOB]
    assert "site-parity-needed" in jobs["site-paths"]["outputs"]
    assert [name for name, job in jobs.items() if JOB in _needs(job)] == []


def test_parity_job_builds_the_commit_it_records() -> None:
    steps = _jobs()[JOB]["steps"]
    expression = "${{ github.event.pull_request.head.sha || github.sha }}"
    checkout = next(step for step in steps if "actions/checkout" in step.get("uses", ""))
    run = next(step["run"] for step in steps if step.get("run", "").startswith("make site-parity"))

    assert checkout["with"]["ref"] == expression
    assert f"SITE_PARITY_SHA={expression}" in run


def test_parity_job_builds_explorer_first_and_runs_the_make_target_on_node_22() -> None:
    steps = _jobs()[JOB]["steps"]
    runs = [step.get("run", "") for step in steps]
    node = [str(step["with"]["node-version"]) for step in steps if "setup-node" in step.get("uses", "")]

    assert node == ["22"]
    parity = next(position for position, run in enumerate(runs) if run.startswith("make site-parity"))
    assert runs.index("npm run build") < runs.index("npm run test:e2e:fixtures") < parity
    assert "github.event.pull_request.head.sha" in runs[parity]
    assert any(step.get("if") == "always()" and "upload-artifact" in step.get("uses", "") for step in steps)


def test_parity_target_runs_every_required_check() -> None:
    aggregate = _recipe("site-parity")
    browser = _recipe("site-parity-browser")
    diff = _recipe("site-parity-diff")
    report = _recipe("site-parity-report")

    for target in (
        "site-parity-sphinx",
        "site-build",
        "site-parity-browser",
        "site-parity-privacy",
        "site-parity-diff",
        "site-parity-report",
    ):
        assert target in aggregate
    assert "verify:explorer" in browser and "verify:parity" in browser
    assert "check_artifact_privacy.py website/dist" in _recipe("site-parity-privacy")
    assert "site_inventory.py diff" in diff and "--allowed-differences" in diff
    assert "SITE_PARITY_REMOVALS" in diff and "--expected-removals" in (ROOT / "make/documentation.mk").read_text(
        encoding="utf-8"
    )
    assert "site_inventory.py check" in diff
    assert "site_parity.py" in report and "browser-report.json" in report
    assert "assemble_public_site.py" in _recipe("site-parity-sphinx")


def test_browser_checks_cover_every_page_template_and_the_search() -> None:
    templates = (ROOT / "website/e2e/templates.mjs").read_text(encoding="utf-8")
    pages = ROOT / "website/src/pages"
    sources = sorted(path.relative_to(pages).as_posix() for path in pages.rglob("*") if path.is_file())

    assert sources
    assert [source for source in sources if f'"{source}":' not in templates] == []
    for content_template in ("docs page", "docs index", "generated query page", "Explorer"):
        assert f'name: "{content_template}"' in templates
    script = (ROOT / "website/e2e/parity.mjs").read_text(encoding="utf-8")
    assert "templateRoutes(" in script and "pagefind-ui__result-link" in script
    explorer = (ROOT / "website/e2e/explorer.mjs").read_text(encoding="utf-8")
    assert "firstTagRoute(siteDir)" in explorer and '"/prompts/"' in explorer and '"/docs/"' in explorer


def test_gate_steps_leave_results_for_the_report_and_clear_stale_ones() -> None:
    for target, result in (
        ("site-parity-privacy", "privacy-result.json"),
        ("site-parity-browser", "explorer-result.json"),
        ("site-parity-browser", "browser-report.json"),
        ("site-parity-browser", "parity-result.json"),
        ("site-parity-browser", "shell-result.json"),
    ):
        recipe = _recipe(target)
        assert "rm -f" in recipe and result in recipe
    report = _recipe("site-parity-report")
    assert "rm -rf" in report
    for flag in (
        "--e2e-report",
        "--step-result",
        "parity-result.json",
        "shell-result.json",
        "explorer-result.json",
        "privacy-result.json",
    ):
        assert flag in report
    assert "wildcard" not in report
