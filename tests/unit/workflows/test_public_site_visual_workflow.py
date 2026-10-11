from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.utilities.posix_shell import run_posix_shell, skip_without_posix_shell

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCS_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "docs.yml"
CAPTURE_SPEC = REPO_ROOT / "results-explorer" / "e2e" / "captures" / "public-site-pages.spec.ts"


CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
CLASSIFY_STEP = "Classify site inputs and validate recovery source"
GATE_STEP = "Require comparison for affected develop trees"
RUNBOOK = REPO_ROOT / "docs" / "internal" / "public-site-visual-baseline.md"


def _run_sh(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    assert command[:2] == ["bash", "-c"] and len(command) == 3
    skip_without_posix_shell()
    kwargs.pop("check", None)
    return run_posix_shell(command[2], check=False, **kwargs)


def _workflow() -> dict[str, Any]:
    return yaml.safe_load(DOCS_WORKFLOW.read_text(encoding="utf-8"))


def _ci() -> dict[str, Any]:
    return yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))


def _step(job: dict[str, Any], name: str) -> dict[str, Any]:
    return next(step for step in job["steps"] if step.get("name") == name)


def _classifier(workflow: dict[str, Any]) -> str:
    return _step(workflow["jobs"]["visual-inputs"], CLASSIFY_STEP)["run"]


def test_develop_pushes_produce_a_public_site_visual_baseline() -> None:
    workflow = _workflow()
    assert "develop" in workflow[True]["push"]["branches"]
    assert "paths" not in workflow[True]["push"]
    assert workflow["permissions"]["actions"] == "read"

    build_steps = workflow["jobs"]["build"]["steps"]
    upload = next(step for step in build_steps if step.get("name") == "Upload assembled site for visual acceptance")
    assert upload["with"]["name"] == "public-site-assembled-${{ github.run_id }}"
    assert upload["with"]["retention-days"] == 3

    visual = workflow["jobs"]["public-site-visual-regression"]
    assert visual["needs"] == ["visual-inputs", "build"]
    assert "refs/heads/develop" in visual["if"]
    assert "workflow_dispatch" in visual["if"]
    assert "base_ref == 'develop'" in visual["if"]

    baseline_upload = next(
        step for step in visual["steps"] if step.get("name") == "Upload protected-develop visual baseline"
    )
    assert "refs/heads/develop" in baseline_upload["if"]
    assert "workflow_dispatch" in baseline_upload["if"]
    assert (
        baseline_upload["with"]["name"] == "public-site-visual-baseline-${{ needs.visual-inputs.outputs.source_sha }}"
    )
    assert baseline_upload["with"]["retention-days"] == 30


def test_pull_requests_require_exact_base_comparison() -> None:
    visual = _workflow()["jobs"]["public-site-visual-regression"]
    steps = visual["steps"]
    names = [step.get("name") for step in steps]
    download = next(
        step for step in steps if step.get("name") == "Download visual baseline for base or site-equivalent ancestor"
    )
    capture = next(step for step in steps if step.get("name") == "Capture public site")
    run = next(step for step in steps if step.get("name") == "Compare public site with exact base")

    assert names.index("Capture public site") < names.index(
        "Download visual baseline for base or site-equivalent ancestor"
    )
    assert names.index("Download visual baseline for base or site-equivalent ancestor") < names.index(
        "Compare public site with exact base"
    )
    assert capture["env"]["PUBLIC_SITE_VISUAL_PHASE"] == "capture"
    assert "if" not in capture
    assert run["env"]["PUBLIC_SITE_VISUAL_PHASE"] == "compare"
    assert run["if"] == "github.event_name == 'pull_request'"
    assert run["env"]["PUBLIC_SITE_VISUAL_REQUIRE_BASELINE"] == "1"
    assert run["env"]["PUBLIC_SITE_VISUAL_OUTPUT"] == capture["env"]["PUBLIC_SITE_VISUAL_OUTPUT"]

    assert download["env"]["PUBLIC_SITE_VISUAL_BASE_SHA"] == "${{ needs.visual-inputs.outputs.base_sha }}"
    assert download["if"] == "github.event_name == 'pull_request'"
    assert download["env"]["PUBLIC_SITE_VISUAL_BASELINE_WAIT_SECONDS"] == "4800"
    assert "continue-on-error" not in download
    assert "download-public-site-visual-baseline.mjs" in download["run"]
    assert run["env"]["PUBLIC_SITE_VISUAL_BASELINE"] == download["env"]["PUBLIC_SITE_VISUAL_BASELINE"]
    assert "merge_group" not in str(run["env"])
    assert "MERGE_GROUP" not in str(run["env"])
    assert run["env"]["E2E_PAGES_SHAPED"] == "1"
    assert "public-site-visual" in run["env"]["PUBLIC_SITE_VISUAL_OUTPUT"]

    assert not any(step.get("name") == "Determine baseline mode" for step in steps)


def test_download_accepts_site_equivalent_ancestors_and_compare_binds_the_used_sha() -> None:
    workflow = _workflow()
    assert workflow["jobs"]["visual-inputs"]["outputs"]["baseline_candidates"] == (
        "${{ steps.paths.outputs.baseline_candidates }}"
    )
    steps = workflow["jobs"]["public-site-visual-regression"]["steps"]
    download = next(
        step for step in steps if step.get("name") == "Download visual baseline for base or site-equivalent ancestor"
    )
    compare = next(step for step in steps if step.get("name") == "Compare public site with exact base")
    assert download["id"] == "baseline"
    assert download["env"]["PUBLIC_SITE_VISUAL_BASELINE_CANDIDATES"] == (
        "${{ needs.visual-inputs.outputs.baseline_candidates }}"
    )
    assert compare["env"]["PUBLIC_SITE_VISUAL_BASE_SHA"] == "${{ steps.baseline.outputs.baseline_sha }}"


def _commit(repo: Path, path: str, content: str, message: str) -> str:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    for args in (
        ["add", path],
        ["-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", message],
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_baseline_candidates_stop_at_the_first_site_input_change(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    _commit(tmp_path, "docs/index.md", "v1\n", "root")
    site_change = _commit(tmp_path, "docs/index.md", "v2\n", "site change")
    quiet_one = _commit(tmp_path, "tests/a.py", "a\n", "non-site change")
    base = _commit(tmp_path, "tests/b.py", "b\n", "another non-site change")
    head = _commit(tmp_path, "tests/c.py", "c\n", "pull request head")

    classifier = _classifier(_workflow())
    output = tmp_path / "github-output"
    env = dict(os.environ)
    env.update(
        EVENT_NAME="pull_request",
        PR_BASE_SHA=base,
        RECOVERY_SOURCE_SHA="",
        CURRENT_SHA=head,
        CURRENT_REF="refs/pull/1/merge",
        GITHUB_OUTPUT=str(output),
    )
    result = _run_sh(["bash", "-c", classifier], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    lines = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert lines["baseline_candidates"].split() == [base, quiet_one, site_change]


def test_baseline_candidates_narrow_scripts_and_reject_non_equivalent_ancestor(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    _commit(tmp_path, "docs/index.md", "v1\n", "root")
    site_change = _commit(tmp_path, "scripts/assemble_public_site.py", "# site script v1\n", "site script change")
    unrelated_script = _commit(tmp_path, "scripts/run_comment_policy.py", "# unrelated v1\n", "unrelated script")
    base = _commit(tmp_path, "tests/b.py", "b\n", "test change")
    head = _commit(tmp_path, "tests/c.py", "c\n", "pr head")

    classifier = _classifier(_workflow())
    output = tmp_path / "github-output"
    env = dict(os.environ)
    env.update(
        EVENT_NAME="pull_request",
        PR_BASE_SHA=base,
        RECOVERY_SOURCE_SHA="",
        CURRENT_SHA=head,
        CURRENT_REF="refs/pull/2/merge",
        GITHUB_OUTPUT=str(output),
    )
    result = _run_sh(["bash", "-c", classifier], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    lines = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert lines["baseline_candidates"].split() == [base, unrelated_script, site_change]


def test_both_workflows_configure_baseline_wait_and_coherent_timeout() -> None:
    for path in (DOCS_WORKFLOW, CI_WORKFLOW):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        visual_job = workflow["jobs"]["public-site-visual-regression"]
        assert visual_job["timeout-minutes"] == 90, f"expected 90m timeout in {path.name}"
        download_step = next(
            step
            for step in visual_job["steps"]
            if step.get("name") == "Download visual baseline for base or site-equivalent ancestor"
        )
        assert download_step["env"]["PUBLIC_SITE_VISUAL_BASELINE_WAIT_SECONDS"] == "4800", (
            f"expected 4800s wait in {path.name}"
        )
        classifier = _classifier(workflow)
        site_paths = classifier.split("SITE_PATHS=(")[1].split(")")[0]
        assert "scripts/" not in site_paths.split(), f"expected narrowed scripts, not broad scripts/ in {path.name}"
        assert "scripts/assemble_public_site.py" in site_paths, f"missing assemble_public_site.py in {path.name}"


def test_no_workflow_publishes_a_merge_queue_candidate_baseline() -> None:
    for path in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        assert "Upload merge-queue candidate visual baseline" not in text, path.name
    lookup = (REPO_ROOT / "results-explorer" / "scripts" / "public-site-visual-baseline-lookup.mjs").read_text(
        encoding="utf-8"
    )
    assert "merge_group" not in lookup
    assert "MERGE_QUEUE" not in lookup


def test_capture_spec_keeps_the_astro_capture_only_guard() -> None:
    source = CAPTURE_SPEC.read_text(encoding="utf-8")
    assert 'if (RENDERER === "astro" && PHASE !== "capture") {' in source
    guard = source.split('if (RENDERER === "astro" && PHASE !== "capture") {', 1)[1].split("}", 1)[0]
    assert "throw new Error(" in guard
    assert "PUBLIC_SITE_VISUAL_RENDERER=astro supports only PUBLIC_SITE_VISUAL_PHASE=capture" in guard


def test_visual_baseline_script_and_capture_command_are_tracked() -> None:
    script = REPO_ROOT / "results-explorer" / "scripts" / "download-public-site-visual-baseline.mjs"
    package = __import__("json").loads((REPO_ROOT / "results-explorer" / "package.json").read_text(encoding="utf-8"))
    script_source = script.read_text(encoding="utf-8")

    assert script.is_file()
    assert "bootstrap=true" not in script_source
    assert "manifest.source_sha !== baselineSha" in script_source
    assert "baseline_sha=${baselineSha}" in script_source
    assert "/^[0-9a-f]{40}$/" in script_source
    assert "waitForTrustedBaseline" in script_source
    lookup_source = (REPO_ROOT / "results-explorer" / "scripts" / "public-site-visual-baseline-lookup.mjs").read_text(
        encoding="utf-8"
    )
    assert "page=${page}" in lookup_source
    assert "actions/runs/${runId}" in lookup_source
    assert "lastLookupError = undefined" in lookup_source
    assert "test:e2e:public-site" in package["scripts"]
    assert "e2e/captures/public-site-pages.spec.ts" in package["scripts"]["test:e2e:public-site"]


def test_public_results_capture_waits_for_data_before_digesting() -> None:
    source = CAPTURE_SPEC.read_text(encoding="utf-8")

    assert "waitForDataLoaded" in source
    assert "Recent Results" in source
    assert "timeout: 240_000" in source
    assert "coldResultsLoad" not in source


def test_docs_workflow_keeps_baselines_and_ci_reports_the_comparison() -> None:
    workflow = _workflow()
    triggers = workflow.get("on", workflow.get(True))
    assert "pull_request" not in triggers
    assert "merge_group" not in triggers
    assert set(triggers) >= {"push", "workflow_dispatch"}
    gate = workflow["jobs"]["public-site-visual-required"]
    assert gate["name"] == "Public-site visual acceptance"
    assert gate["if"] == "always()"
    assert set(gate["needs"]) == {"visual-inputs", "build", "public-site-visual-regression"}

    ci = _ci()
    ci_triggers = ci.get("on", ci.get(True))
    assert "paths" not in ci_triggers["pull_request"]
    assert "merge_group" not in ci_triggers
    visual = ci["jobs"]["public-site-visual-regression"]
    assert visual["name"] == "Public-site visual regression"
    assert "render_changed" in visual["if"]
    assert "merge_group" not in visual["if"]


@pytest.mark.parametrize(
    ("event", "base_ref", "changed", "build", "visual", "expected"),
    [
        ("pull_request", "develop", "false", "skipped", "skipped", 0),
        ("pull_request", "develop", "true", "success", "success", 0),
        ("pull_request", "develop", "true", "success", "failure", 1),
        ("pull_request", "develop", "", "success", "success", 1),
    ],
)
def test_required_gate_fails_closed_for_changed_inputs(
    event: str, base_ref: str, changed: str, build: str, visual: str, expected: int
) -> None:
    step = _step(_workflow()["jobs"]["public-site-visual-required"], GATE_STEP)
    env = dict(os.environ)
    env.update(
        INPUT_RESULT="success",
        SITE_CHANGED=changed,
        BUILD_RESULT=build,
        VISUAL_RESULT=visual,
        EVENT_NAME=event,
        BASE_REF=base_ref,
    )
    result = _run_sh(["bash", "-c", step["run"]], env=env, capture_output=True, text=True, check=False)
    assert result.returncode == expected, result.stderr


def test_required_gate_rejects_failed_input_classification() -> None:
    step = _step(_workflow()["jobs"]["public-site-visual-required"], GATE_STEP)
    result = _run_sh(
        ["bash", "-c", step["run"]],
        env={
            **os.environ,
            "INPUT_RESULT": "failure",
            "SITE_CHANGED": "false",
            "EVENT_NAME": "pull_request",
            "BASE_REF": "develop",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1


def test_classification_retains_every_prior_public_site_input() -> None:
    workflow = _workflow()
    classifier = _classifier(workflow)
    former_inputs = (
        "benchbox/",
        "docs/",
        "examples/",
        "landing/",
        "_project/scripts/explorer_pipeline/",
        "_project/scripts/explorer_publish.py",
        "_project/scripts/results_explorer_snapshot_invariants.py",
        "results-data/",
        "results-explorer/",
        "scripts/",
        ".github/workflows/docs.yml",
        "Makefile",
        "README.md",
        "pyproject.toml",
    )
    assert all(path in classifier for path in former_inputs)
    assert "git merge-base --is-ancestor" in classifier
    assert "source_sha=$SOURCE_SHA" in classifier


@pytest.mark.parametrize(
    ("event", "changed_path", "expected"),
    [
        ("pull_request", "docs/changed.md", "true"),
        ("pull_request", "results-explorer/changed.ts", "true"),
        ("pull_request", "tests/changed.py", "false"),
    ],
)
def test_input_classifier_uses_exact_base_diff(tmp_path: Path, event: str, changed_path: str, expected: str) -> None:
    def git(*args: str) -> str:
        result = subprocess.run(["git", *args], cwd=tmp_path, text=True, capture_output=True, check=True)
        return result.stdout.strip()

    git("init", "-q")
    (tmp_path / "README.md").write_text("original\n")
    git("add", "README.md")
    git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "base")
    base_sha = git("rev-parse", "HEAD")
    changed = tmp_path / changed_path
    changed.parent.mkdir(parents=True, exist_ok=True)
    changed.write_text("changed\n")
    git("add", changed_path)
    git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "change")
    head_sha = git("rev-parse", "HEAD")

    classifier = _classifier(_workflow())
    output = tmp_path / "github-output"
    env = dict(os.environ)
    env.update(
        EVENT_NAME=event,
        PR_BASE_SHA=base_sha,
        RECOVERY_SOURCE_SHA="",
        CURRENT_SHA=head_sha,
        CURRENT_REF="refs/heads/develop",
        GITHUB_OUTPUT=str(output),
    )
    result = _run_sh(["bash", "-c", classifier], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert f"changed={expected}" in output.read_text()
    assert f"base_sha={base_sha}" in output.read_text()


def test_astro_dry_run_captures_without_comparing_or_gating() -> None:
    ci = _ci()
    job = ci["jobs"]["public-site-visual-astro-dry-run"]
    assert job["name"] == "Public-site visual Astro dry run"
    assert "site-needed" in job["if"]
    steps = {step["name"]: step for step in job["steps"] if "name" in step}
    assert steps["Build website"]["run"] == "make site-build"
    assert "make site-visual-capture" in steps["Capture public site from the Astro build"]["run"]
    upload = steps["Upload Astro visual captures"]
    assert upload["with"]["name"].startswith("public-site-visual-astro-")
    text = str(job)
    assert "download-public-site-visual-baseline" not in text
    assert "PUBLIC_SITE_VISUAL_BASELINE" not in text
    for name, other in ci["jobs"].items():
        needs = other.get("needs", [])
        needs = [needs] if isinstance(needs, str) else needs
        assert "public-site-visual-astro-dry-run" not in needs, name


def _capture_recipe(*overrides: str) -> str:
    result = subprocess.run(
        ["make", "-n", "-s", "site-visual-capture", *overrides],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_astro_capture_target_captures_dist_for_the_requested_source() -> None:
    recipe = _capture_recipe("SITE_VISUAL_SOURCE_SHA=" + "a" * 40, "SITE_VISUAL_DIR=out")
    assert "PUBLIC_SITE_VISUAL_RENDERER=astro" in recipe
    assert "PUBLIC_SITE_VISUAL_PHASE=capture" in recipe
    assert f'E2E_SITE_DIR="{REPO_ROOT.as_posix()}/website/dist"' in recipe
    assert f'PUBLIC_SITE_VISUAL_OUTPUT="{REPO_ROOT.as_posix()}/out"' in recipe
    assert 'PUBLIC_SITE_VISUAL_SOURCE_SHA="' + "a" * 40 + '"' in recipe
    assert "PUBLIC_SITE_VISUAL_BASELINE" not in recipe
    assert "PUBLIC_SITE_VISUAL_REQUIRE_BASELINE" not in recipe


def test_astro_capture_target_defaults_to_the_current_commit_and_honours_the_parity_sha() -> None:
    assert f'PUBLIC_SITE_VISUAL_SOURCE_SHA="{_head()}"' in _capture_recipe()
    assert 'PUBLIC_SITE_VISUAL_SOURCE_SHA="' + "b" * 40 + '"' in _capture_recipe("SITE_PARITY_SHA=" + "b" * 40)


def test_capture_spec_records_the_renderer_and_keeps_the_matrix() -> None:
    source = CAPTURE_SPEC.read_text(encoding="utf-8")
    assert "PUBLIC_SITE_VISUAL_RENDERER" in source
    assert "[390, 768, 1280, 1600]" in source
    for route in ('"/"', '"/docs/usage/getting-started.html"', '"/results/benchmarks/"', '"/results/platforms/"'):
        assert f"path: {route}" in source


def test_visual_inputs_classify_website_in_both_workflows() -> None:
    for path in (DOCS_WORKFLOW, CI_WORKFLOW):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        classifier = _classifier(workflow)
        site_paths = classifier.split("SITE_PATHS=(")[1].split(")")[0]
        assert "website/" in site_paths, path.name
        assert "landing/" in site_paths, path.name


def _step_names(workflow: dict[str, Any]) -> set[str]:
    return {step["name"] for job in workflow["jobs"].values() for step in job["steps"] if "name" in step}


def test_runbook_names_only_approval_controls_that_a_workflow_reads() -> None:
    runbook = RUNBOOK.read_text(encoding="utf-8")
    controls = set(re.findall(r"`((?:[A-Z_]*APPROVED[A-Z_]*|[A-Z_]*APPROVAL_REASON))`", runbook))
    assert {"APPROVED_HEAD_SHA", "APPROVAL_REASON"} <= controls
    workflow_text = CI_WORKFLOW.read_text(encoding="utf-8")
    assert [name for name in sorted(controls) if f"vars.{name}" not in workflow_text] == []
    assert "merge_group" not in _ci().get("on", _ci().get(True))


def test_runbook_renderer_switch_section_names_real_jobs_and_steps() -> None:
    runbook = RUNBOOK.read_text(encoding="utf-8")
    section = runbook.split("### Renderer-switch pull request")[1].split("## What CI gates")[0]
    docs_workflow, ci = _workflow(), _ci()
    named = set(re.findall(r"`([^`]+)`", section))
    for step in ("Assemble public site", "Upload assembled site for visual acceptance", "Capture public site"):
        assert step in named
        assert step in _step_names(docs_workflow) and step in _step_names(ci)
    assert "docs-build" in named and "docs-build" in ci["jobs"]
    assert "Public-site visual regression" in named
    assert ci["jobs"]["public-site-visual-regression"]["name"] == "Public-site visual regression"
    assert "build" in docs_workflow["jobs"]
    assert "Queue position" in section or "queue position" in section
    assert '"renderer": "astro"' in section
