from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS_DIR = ROOT / ".github" / "workflows"
SITE_DEPLOY_PATH = WORKFLOWS_DIR / "site-deploy.yml"
DEPLOY_PAGES_SHA = "368f82528645a54fb793d4d04e342629a3f51346"
UPLOAD_PAGES_SHA = "fc324d3547104276b827a68afc52ff2a11cc49c9"


def _workflow() -> dict[str, Any]:
    return yaml.safe_load(SITE_DEPLOY_PATH.read_text(encoding="utf-8"))


def _jobs() -> dict[str, Any]:
    return _workflow()["jobs"]


def _steps(job: str) -> list[dict[str, Any]]:
    return _jobs()[job]["steps"]


def test_workflow_is_dispatch_only_with_the_decided_inputs() -> None:
    triggers = _workflow().get("on") or _workflow().get(True)
    assert set(triggers) == {"workflow_dispatch"}
    inputs = triggers["workflow_dispatch"]["inputs"]
    assert inputs["mode"]["type"] == "choice"
    assert inputs["mode"]["options"] == ["preview", "deploy", "rollback"]
    assert inputs["rollback_receipt_run_id"]["required"] is False
    assert inputs["rollback_phase"]["options"] == ["full", "ui-first"]
    assert inputs["bootstrap"]["default"] is False
    assert inputs["current_generation_unknown"]["type"] == "boolean"
    assert inputs["current_generation_unknown"]["default"] is False


def test_production_modes_share_the_legacy_group_and_preview_holds_a_private_one() -> None:
    concurrency = _workflow()["concurrency"]
    assert concurrency["cancel-in-progress"] is False
    group = concurrency["group"]
    assert group == "${{ inputs.mode == 'preview' && format('site-preview-{0}', github.run_id) || 'pages-deploy' }}"
    assert "'pages-deploy'" in group
    for name, job in _jobs().items():
        assert "concurrency" not in job, f"{name} must not open a second lock inside the workflow lock"


def test_artifact_names_are_unique_per_run_attempt_and_consumers_read_job_outputs() -> None:
    text = SITE_DEPLOY_PATH.read_text(encoding="utf-8")
    names = re.findall(r"^\s+name: (site-deploy-[^\n]+)$", text, flags=re.MULTILINE)
    assert names
    for name in names:
        assert "${{ github.run_attempt }}" in name, name
    outputs = _jobs()["build"]["outputs"]
    assert set(outputs) == {"build_artifact", "site_artifact", "legacy_artifact"}
    for step in _steps("probe") + _steps("preview") + _steps("deploy"):
        downloaded = str(step.get("with", {}).get("name", ""))
        if "download-artifact" in step.get("uses", ""):
            assert downloaded.startswith("${{ needs."), downloaded


def test_receipt_is_uploaded_before_the_deployment_status_is_posted() -> None:
    steps = _steps("probe")
    names = [step.get("name", "") for step in steps]
    finalize = names.index("Probe production and write the receipt")
    upload = names.index("Upload the receipt")
    record = names.index("Record the receipt on the deployment")
    assert finalize < upload < record
    assert "scripts.site_deploy finalize" in steps[finalize]["run"]
    assert steps[upload]["id"] == "receipt_upload"
    assert steps[upload]["with"]["if-no-files-found"] == "error"
    assert "steps.receipt_upload.outcome == 'success'" in steps[record]["if"]
    assert "steps.finalize.outcome == 'success'" in steps[record]["if"]
    assert "scripts.site_deploy record" in steps[record]["run"]
    assert "record" not in steps[finalize]["run"]


def test_rollback_downloads_go_through_the_digest_checked_fetch_command() -> None:
    restore = next(step for step in _steps("build") if step.get("name") == "Restore the retained generation")
    assert "gh run download" not in restore["run"]
    assert "scripts.site_deploy fetch-run" in restore["run"]
    assert "--receipt-sha256" in restore["run"]


def test_github_token_is_scoped_to_steps_that_call_the_api() -> None:
    build = _jobs()["build"]
    assert "GH_TOKEN" not in build["env"]
    holders = [step.get("name") for step in build["steps"] if "GH_TOKEN" in step.get("env", {})]
    assert holders == ["Restore the retained generation"]
    for name, job in _jobs().items():
        assert "GH_TOKEN" not in job.get("env", {}), name


def test_pinned_actions_carry_no_trailing_version_comments() -> None:
    text = SITE_DEPLOY_PATH.read_text(encoding="utf-8")
    assert not re.search(r"uses:\s+\S+@[0-9a-f]{40}\s+#", text)


def test_exactly_one_job_carries_the_github_pages_environment() -> None:
    with_environment = {name: job["environment"] for name, job in _jobs().items() if "environment" in job}
    assert list(with_environment) == ["deploy"]
    assert with_environment["deploy"]["name"] == "github-pages"


def test_probe_and_receipt_job_has_no_environment_and_can_write_deployment_statuses() -> None:
    probe = _jobs()["probe"]
    assert "environment" not in probe
    assert probe["permissions"] == {"contents": "read", "actions": "read", "deployments": "write"}
    assert probe["needs"] == ["resolve", "build", "deploy"]
    commands = " ".join(str(step.get("run", "")) for step in probe["steps"])
    assert "scripts.site_deploy finalize" in commands
    receipt_upload = next(
        step for step in probe["steps"] if step.get("with", {}).get("name", "").startswith("site-deploy-receipt")
    )
    assert receipt_upload["with"]["retention-days"] == 90


def test_permissions_are_least_privilege_per_job() -> None:
    workflow = _workflow()
    jobs = _jobs()
    assert workflow["permissions"] == {"contents": "read"}
    assert jobs["resolve"]["permissions"] == {"contents": "read", "actions": "read", "deployments": "read"}
    assert jobs["build"]["permissions"] == {"contents": "read", "actions": "read"}
    assert jobs["preview"]["permissions"] == {"contents": "read", "actions": "read"}
    assert jobs["deploy"]["permissions"] == {
        "contents": "read",
        "actions": "read",
        "deployments": "read",
        "pages": "write",
        "id-token": "write",
    }
    writers = {name for name, job in jobs.items() if any(value == "write" for value in job["permissions"].values())}
    assert writers == {"deploy", "probe"}
    assert jobs["deploy"]["permissions"].get("deployments") != "write"
    assert "pages" not in jobs["probe"]["permissions"]


def test_preview_job_has_no_environment_and_no_write_scope() -> None:
    preview = _jobs()["preview"]
    assert "environment" not in preview
    assert all(value == "read" for value in preview["permissions"].values())
    assert preview["if"] == "inputs.mode == 'preview'"
    run = " ".join(str(step.get("run", "")) for step in preview["steps"])
    assert "scripts.site_deploy preview" in run


def test_deploy_job_is_never_reached_by_preview_and_rechecks_the_generation_first() -> None:
    deploy = _jobs()["deploy"]
    assert "inputs.mode != 'preview'" in deploy["if"]
    names = [step.get("name") or step.get("uses", "") for step in deploy["steps"]]
    recheck = next(index for index, name in enumerate(names) if name == "Recheck the deployed generation")
    publish = next(index for index, step in enumerate(deploy["steps"]) if "deploy-pages" in step.get("uses", ""))
    assert recheck < publish
    assert "scripts.site_deploy recheck" in deploy["steps"][recheck]["run"]
    assert deploy["steps"][publish]["uses"].split("@")[1].split()[0] == DEPLOY_PAGES_SHA


def test_pages_artifact_is_uploaded_only_outside_preview_with_the_shared_pin() -> None:
    upload = next(step for step in _steps("build") if "upload-pages-artifact" in step.get("uses", ""))
    assert upload["uses"].split("@")[1].split()[0] == UPLOAD_PAGES_SHA
    assert upload["if"] == "inputs.mode != 'preview'"
    assert upload["with"]["include-hidden-files"] is True


def test_every_run_retains_the_exact_artifact_for_rollback() -> None:
    retained = next(
        step
        for step in _steps("build")
        if str(step.get("with", {}).get("name", "")).startswith("site-deploy-artifact-")
    )
    assert retained["with"]["retention-days"] == 90
    assert retained["with"]["include-hidden-files"] is True
    assert "if" not in retained


def test_all_actions_are_pinned_by_full_commit_sha() -> None:
    text = SITE_DEPLOY_PATH.read_text(encoding="utf-8")
    refs = re.findall(r"uses:\s+([^\s#]+)", text)
    assert refs
    for ref in refs:
        name, _, sha = ref.partition("@")
        assert re.fullmatch(r"[0-9a-f]{40}", sha), f"{name} must be pinned by a 40-character commit SHA"


def test_job_graph_orders_resolve_build_deploy_probe() -> None:
    jobs = _jobs()
    assert "needs" not in jobs["resolve"]
    assert jobs["build"]["needs"] == "resolve"
    assert jobs["deploy"]["needs"] == ["resolve", "build", "visual"]
    assert jobs["visual"]["needs"] == ["resolve", "build"]
    assert jobs["preview"]["needs"] == ["resolve", "build"]


def test_production_modes_are_restricted_to_develop() -> None:
    guard = _steps("resolve")[0]
    assert guard["if"] == "inputs.mode != 'preview' && github.ref != 'refs/heads/develop'"


def test_no_user_input_is_interpolated_into_shell_commands() -> None:
    for job_name, job in _jobs().items():
        for step in job["steps"]:
            run = str(step.get("run", ""))
            assert "${{ inputs." not in run, f"{job_name}: inputs must reach shell through env"


def test_gates_run_before_any_artifact_is_marked_for_deployment() -> None:
    steps = _steps("build")
    names = [step.get("name", step.get("uses", "")) for step in steps]
    gates = names.index("Run gates")
    pages = next(index for index, step in enumerate(steps) if "upload-pages-artifact" in step.get("uses", ""))
    assert gates < pages
    assert "scripts.site_deploy gates" in steps[gates]["run"]


def test_workflow_is_the_only_new_pages_writer_and_legacy_writers_keep_the_group() -> None:
    writers = {path.name for path in WORKFLOWS_DIR.glob("*.yml") if "deploy-pages" in path.read_text(encoding="utf-8")}
    assert "site-deploy.yml" in writers
    assert "docs.yml" not in writers
    for name in ("publication-deploy.yml", "publication-transaction.yml"):
        assert "group: pages-deploy" in (WORKFLOWS_DIR / name).read_text(encoding="utf-8")


def test_build_refuses_a_candidate_whose_control_plane_differs_from_the_dispatch() -> None:
    steps = _steps("build")
    names = [step.get("name", "") for step in steps]
    guard = steps[names.index("Refuse a control plane that differs from the dispatched revision")]
    assert guard["env"]["DISPATCH_SHA"] == "${{ github.sha }}"
    for path in ("deploy", "scripts/site_deploy", "scripts/publication", "scripts/assemble_public_site.py"):
        assert path in guard["run"]
    assert names.index("Refuse a control plane that differs from the dispatched revision") < names.index("Run gates")


def _step(job: str, name: str) -> dict[str, Any]:
    return next(step for step in _steps(job) if step.get("name") == name)


def test_resolve_exposes_the_renderer_and_whether_the_visual_comparison_runs() -> None:
    outputs = _jobs()["resolve"]["outputs"]
    assert outputs["renderer"] == "${{ steps.resolve.outputs.renderer }}"
    assert outputs["visual_required"] == "${{ steps.resolve.outputs.visual_required }}"
    assert outputs["release_in_use"] == "${{ steps.resolve.outputs.release_in_use }}"


def test_release_build_steps_run_only_while_a_route_uses_the_release_ref() -> None:
    for name in ("Build release documentation", "Build the release site with Astro"):
        assert "needs.resolve.outputs.release_in_use == 'true'" in _step("build", name)["if"], name
    assert "release_in_use" not in _step("build", "Build the trunk site with Astro")["if"]


def test_each_renderer_builds_only_its_own_site() -> None:
    build = _jobs()["build"]
    assert build["env"]["RENDERER"] == "${{ needs.resolve.outputs.renderer }}"
    for name in ("Build release documentation", "Generate trunk documentation inputs", "Build trunk documentation"):
        assert "needs.resolve.outputs.renderer == 'sphinx'" in _step("build", name)["if"], name
    for name in ("Build the release site with Astro", "Build the trunk site with Astro"):
        step = _step("build", name)
        assert "needs.resolve.outputs.renderer == 'astro'" in step["if"], name
        assert "make site-build" in step["run"]
    assert _step("build", "Build the release site with Astro")["working-directory"] == "release"
    nodes = [step for step in _steps("build") if "setup-node" in step.get("uses", "")]
    assert {step["with"]["node-version"] for step in nodes} == {"20", "22"}
    assemble = _step("build", "Assemble routes")
    assert '--renderer "$RENDERER"' in assemble["run"]


def test_visual_comparison_runs_only_when_resolve_requires_it_and_reuses_the_capture_spec() -> None:
    visual = _jobs()["visual"]
    assert visual["if"] == "needs.resolve.outputs.visual_required == 'true'"
    assert visual["permissions"] == {"contents": "read", "actions": "read"}
    assert "environment" not in visual
    fetch = _step("visual", "Fetch the last production artifact")
    assert "scripts.site_deploy fetch-run" in fetch["run"]
    assert "--verify-tree" in fetch["run"]
    assert "--receipt-sha256" in fetch["run"]
    runs = [step for step in visual["steps"] if step.get("run") == "npm run test:e2e:public-site"]
    assert [step["name"] for step in runs] == [
        "Capture the last production artifact",
        "Capture the candidate artifact",
        "Compare the candidate with the last production artifact",
    ]
    production, candidate, compare = (step["env"] for step in runs)
    assert production["PUBLIC_SITE_VISUAL_PHASE"] == candidate["PUBLIC_SITE_VISUAL_PHASE"] == "capture"
    assert production["E2E_SITE_DIR"].endswith("/production/tree")
    assert production["PUBLIC_SITE_VISUAL_RENDERER"] == "${{ steps.production.outputs.renderer }}"
    assert candidate["PUBLIC_SITE_VISUAL_RENDERER"] == "${{ needs.resolve.outputs.renderer }}"
    assert compare["PUBLIC_SITE_VISUAL_PHASE"] == "compare"
    assert "PUBLIC_SITE_VISUAL_RENDERER" not in compare
    assert compare["PUBLIC_SITE_VISUAL_REQUIRE_BASELINE"] == "1"
    assert compare["PUBLIC_SITE_VISUAL_BASELINE"] == production["PUBLIC_SITE_VISUAL_OUTPUT"]
    assert compare["PUBLIC_SITE_VISUAL_OUTPUT"] == candidate["PUBLIC_SITE_VISUAL_OUTPUT"]
    assert compare["PUBLIC_SITE_VISUAL_BASE_SHA"] == production["PUBLIC_SITE_VISUAL_SOURCE_SHA"]
    assert compare["PUBLIC_SITE_VISUAL_SOURCE_SHA"] == "${{ needs.resolve.outputs.trunk_sha }}"
    assert compare["PR_HEAD_SHA"] == "${{ steps.binding.outputs.binding }}"
    assert compare["APPROVED_HEAD_SHA"] == "${{ vars.SITE_DEPLOY_VISUAL_APPROVED_BINDING }}"
    binding = _step("visual", "Bind the approval to the release, candidate and baseline")
    assert binding["id"] == "binding"
    assert "scripts.site_deploy visual-binding" in binding["run"]
    assert "--candidate candidate --baseline production/tree" in binding["run"]
    names = [step.get("name") for step in visual["steps"]]
    assert (
        names.index("Fetch the last production artifact") < names.index(binding["name"]) < names.index(runs[0]["name"])
    )
    assert compare["APPROVAL_REASON"] == "${{ vars.SITE_DEPLOY_VISUAL_APPROVAL_REASON }}"
    assert "vars.APPROVED_HEAD_SHA" not in SITE_DEPLOY_PATH.read_text(encoding="utf-8")
    package = (ROOT / "results-explorer" / "package.json").read_text(encoding="utf-8")
    assert "e2e/captures/public-site-pages.spec.ts" in package


def test_deploy_waits_for_a_required_visual_comparison_and_probe_survives_its_skip() -> None:
    condition = _jobs()["deploy"]["if"]
    assert condition.startswith("${{ !cancelled()")
    assert condition.endswith("}}")
    assert "needs.build.result == 'success'" in condition
    assert "needs.visual.result == 'success'" in condition
    assert "needs.visual.result == 'skipped' && needs.resolve.outputs.visual_required != 'true'" in condition
    assert _jobs()["probe"]["if"] == "${{ !cancelled() && needs.deploy.result == 'success' }}"


def test_resolve_pins_the_locked_yaml_reader_without_the_project() -> None:
    run = _step("resolve", "Resolve candidate and generation")["run"]
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    locked = re.search(r'name = "pyyaml"\nversion = "([^"]+)"', lock)
    assert locked
    assert f'uv run --no-project --with "pyyaml=={locked.group(1)}" -- python -m scripts.site_deploy resolve' in run
    assert "uv sync" not in run


def test_plain_python_jobs_can_import_the_cli_without_third_party_packages() -> None:
    plain = [
        step["run"]
        for job in ("deploy", "probe", "visual")
        for step in _steps(job)
        if "python -m scripts.site_deploy" in str(step.get("run", ""))
    ]
    assert plain and not any("uv run" in run for run in plain)
    code = "import sys; sys.modules.update(yaml=None, duckdb=None); import scripts.site_deploy.cli"
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_visual_comparison_covers_the_trunk_served_landing_and_getting_started() -> None:
    from scripts.site_deploy import routes
    from scripts.site_deploy.renderer import RENDERERS

    spec = (ROOT / "results-explorer" / "e2e" / "captures" / "public-site-pages.spec.ts").read_text(encoding="utf-8")
    captured = dict(re.findall(r'slug:\s*"([^"]+)",\s*path:\s*"([^"]+)"', spec))
    assert len(captured) >= 6
    selected = _jobs()["visual"]["env"]["PUBLIC_SITE_VISUAL_ROUTES"].split(",")
    assert sorted(selected) == ["getting-started", "landing"]
    manifest = routes.load_manifest(ROOT / "deploy" / "routes.yml")
    for renderer in RENDERERS:
        assert all(
            manifest.refs[routes.owner_ref(manifest, path, renderer)] == "trunk" for path in captured.values()
        ), renderer
    assert "PUBLIC_SITE_VISUAL_ROUTES" in spec


def test_visual_comparison_is_required_on_a_release_or_renderer_change() -> None:
    from scripts.site_deploy import cli
    from scripts.site_deploy.generation import Deployed

    deployed = Deployed(
        generation=1,
        trunk_sha="a" * 40,
        release_tag="v0.4.1",
        corpus_sha="1" * 40,
        run_id=1,
        receipt_sha256="0" * 64,
        artifact_sha256="f" * 64,
        ui_version=11,
        snapshot_version=11,
        renderer="sphinx",
        release_sha="9" * 40,
    )
    assert cli.visual_comparison_required(deployed, "sphinx", "9" * 40) is False
    assert cli.visual_comparison_required(deployed, "sphinx", "8" * 40) is True
    assert cli.visual_comparison_required(deployed, "astro", "9" * 40) is True


def test_a_first_astro_deploy_without_a_receipted_generation_names_the_sphinx_precondition() -> None:
    fetch = _step("visual", "Fetch the last production artifact")["run"]
    assert "deploy and receipt a Sphinx generation before switching deploy/routes.yml to renderer: auto" in fetch
    runbook = (ROOT / "docs" / "internal" / "site-deploy.md").read_text(encoding="utf-8")
    cutover = runbook.split("### Cutover")[1].split("### Astro layout")[0]
    assert "a receipted Sphinx generation must be live before the switch" in " ".join(cutover.split())


def test_runbook_states_the_pre_deploy_visual_scope_the_workflow_uses() -> None:
    runbook = (ROOT / "docs" / "internal" / "site-deploy.md").read_text(encoding="utf-8")
    section = runbook.split("## Visual comparison before deploy")[1].split("## Candidate and generation")[0]
    spec = (ROOT / "results-explorer" / "e2e" / "captures" / "public-site-pages.spec.ts").read_text(encoding="utf-8")
    captured = set(re.findall(r'slug:\s*"([^"]+)"', spec))
    selected = set(_jobs()["visual"]["env"]["PUBLIC_SITE_VISUAL_ROUTES"].split(","))
    named = set(re.findall(r"`([a-z-]+)`", section))
    assert selected <= named
    assert captured - selected <= named
    assert "`Public-site visual regression` job in `site-docs.yml`" in " ".join(section.split())
