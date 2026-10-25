# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import itertools
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tests.utilities.posix_shell import run_posix_shell, skip_without_posix_shell

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]

REPO_ROOT = Path(__file__).parent.parent.parent
CI_FAST_EXPRESSION = "fast and not (slow or stress or resource_heavy or live_integration)"
RELEASE_INTEGRATION_EXPRESSION = "integration and not (slow or stress or resource_heavy or live_integration)"
RELEASE_CANARY_NON_FAST_EXPRESSION = "(slow or resource_heavy) and not (stress or live_integration)"
RELEASE_PR_BRANCH_SKIP = (
    "(github.event_name != 'pull_request' || github.base_ref != 'release' || !startsWith(github.head_ref, 'v'))"
)
RELEASE_REQUIRED_CONTEXTS = ("validate-base", "release-required-result")


def _makefile_text() -> str:
    return (REPO_ROOT / "Makefile").read_text(encoding="utf-8")


def _make_target_recipe(target: str) -> str:
    lines = _makefile_text().splitlines()
    start = (
        next(
            index
            for index, line in enumerate(lines)
            if line.split(":", 1)[0] == target and index + 1 < len(lines) and lines[index + 1].startswith("\t")
        )
        + 1
    )
    recipe_lines: list[str] = []
    for line in lines[start:]:
        if line and not line.startswith("\t"):
            break
        recipe_lines.append(line)
    return "\n".join(recipe_lines)


def _workflow_job_run_text(workflow_name: str, job_name: str) -> str:
    workflow_path = REPO_ROOT / ".github" / "workflows" / workflow_name
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    return "\n".join(str(step.get("run", "")) for step in workflow["jobs"][job_name]["steps"])


def _workflow(workflow_name: str) -> dict:
    workflow_path = REPO_ROOT / ".github" / "workflows" / workflow_name
    return yaml.safe_load(workflow_path.read_text(encoding="utf-8"))


import tomllib


class TestReleaseInfrastructure:
    def test_changelog_exists(self):
        changelog_path = REPO_ROOT / "CHANGELOG.md"
        assert changelog_path.exists(), "CHANGELOG.md file must exist"

    def test_pyproject_toml_release_config(self):
        pyproject_path = REPO_ROOT / "pyproject.toml"

        with open(pyproject_path, "rb") as f:
            config = tomllib.load(f)

        project = config["project"]
        assert "name" in project
        assert project["name"] == "benchbox"
        assert "version" in project
        assert "description" in project
        assert "readme" in project
        assert "license" in project
        assert "authors" in project

        urls = project["urls"]
        expected_repo = "https://github.com/BenchBox-dev/benchbox"
        assert urls["Homepage"] == expected_repo
        assert urls["Repository"] == f"{expected_repo}.git"
        assert urls["Bug Tracker"] == f"{expected_repo}/issues"
        assert urls["Changelog"] == f"{expected_repo}/blob/release/CHANGELOG.md"

        for url in urls.values():
            assert "anthropics/claude-code" not in url
            assert "anthropic" not in url

    def test_supported_python_range_matches_release_policy(self):
        with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
            project = tomllib.load(handle)["project"]

        assert project["requires-python"] == ">=3.11,<3.15"
        assert "Programming Language :: Python :: 3.10" not in project["classifiers"]
        for version in ("3.11", "3.12", "3.13", "3.14"):
            assert f"Programming Language :: Python :: {version}" in project["classifiers"]

    def test_import_benchbox_succeeds_without_pandas(self):
        code = "import sys; sys.modules['pandas'] = None; import benchbox; print(benchbox.__version__)"
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"import benchbox failed without pandas:\n{result.stderr}"

    def test_import_benchbox_does_not_pull_pandas(self):
        code = "import benchbox, sys; assert 'pandas' not in sys.modules, sorted(m for m in sys.modules if m == 'pandas' or m.startswith('pandas.'))"
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"import benchbox eagerly imported pandas:\n{result.stdout}{result.stderr}"

    def test_github_issue_url_fix(self):
        exceptions_path = REPO_ROOT / "benchbox" / "cli" / "exceptions.py"

        with open(exceptions_path, encoding="utf-8") as f:
            content = f.read()

        assert "https://github.com/BenchBox-dev/benchbox/issues" in content
        assert "anthropics/claude-code" not in content

    def test_github_workflows_exist(self):
        workflows_dir = REPO_ROOT / ".github" / "workflows"
        assert workflows_dir.exists(), "GitHub workflows directory must exist"

        required_workflows = ["test.yml", "lint.yml", "release.yml"]
        for workflow in required_workflows:
            workflow_path = workflows_dir / workflow
            assert workflow_path.exists(), f"{workflow} workflow must exist"

    def test_test_workflow_configuration(self):
        test_workflow_path = REPO_ROOT / ".github" / "workflows" / "test.yml"

        with open(test_workflow_path, encoding="utf-8") as f:
            workflow = yaml.safe_load(f)

        assert "name" in workflow
        assert True in workflow
        assert "jobs" in workflow

        on_events = workflow[True]
        assert "push" in on_events
        assert "pull_request" in on_events

        jobs = workflow["jobs"]
        assert "test" in jobs

        test_job = jobs["test"]
        assert test_job["name"] == "test (ubuntu-latest, 3.12)"
        assert test_job["runs-on"] == "ubuntu-latest"
        assert "strategy" not in test_job, "Required main test job is intentionally a single 3.12 lane"

        steps = test_job["steps"]
        uv_step_found = any("uv" in str(step).lower() for step in steps)
        assert uv_step_found, "Workflow should use uv for dependency management"

        workflow_text = test_workflow_path.read_text(encoding="utf-8")
        assert f'-m "{CI_FAST_EXPRESSION}"' in workflow_text
        assert "--cov-fail-under=70" in workflow_text

    def test_required_fast_marker_expression_is_consistent(self):
        makefile_content = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        develop_pr_run_text = _workflow_job_run_text("ci.yml", "code-test")
        main_pr_run_text = _workflow_job_run_text("test.yml", "test")

        expected_marker_flag = f'-m "{CI_FAST_EXPRESSION}"'
        assert expected_marker_flag in makefile_content
        assert "-m fast -q" not in makefile_content
        assert expected_marker_flag in develop_pr_run_text
        assert expected_marker_flag in main_pr_run_text
        assert "--cov-fail-under=70" in develop_pr_run_text
        assert "--cov-fail-under=70" in main_pr_run_text
        assert "coverage remains CI-only" in makefile_content

    def test_fast_suite_runs_on_python_314_per_pr_and_on_python_312_after_merge_and_nightly(self):
        expected_marker_flag = f'-m "{CI_FAST_EXPRESSION}"'

        def python_version(workflow_name: str, job_name: str) -> str:
            steps = _workflow(workflow_name)["jobs"][job_name]["steps"]
            setup = next(step for step in steps if step.get("uses", "").startswith("actions/setup-python@"))
            return setup["with"]["python-version"]

        pr_steps = _workflow("ci.yml")["jobs"]["code-test"]["steps"]
        pr_fast = next(step for step in pr_steps if step.get("name") == "Run fast tests")
        assert python_version("ci.yml", "code-test") == "3.14"
        assert pr_fast["env"]["COVERAGE_CORE"] == "sysmon"
        assert expected_marker_flag in pr_fast["run"]

        assert _workflow("trunk.yml")[True]["push"] == {"branches": ["develop"]}
        assert python_version("trunk.yml", "fast") == "3.12"
        assert expected_marker_flag in _workflow_job_run_text("trunk.yml", "fast")

        nightly = _workflow("nightly.yml")
        assert "schedule" in nightly[True]
        nightly_matrix = nightly["jobs"]["test"]["strategy"]["matrix"]
        assert "ubuntu-latest" in nightly_matrix["os"]
        assert "3.12" in nightly_matrix["python-version"]
        nightly_run_text = _workflow_job_run_text("nightly.yml", "test")
        assert expected_marker_flag in nightly_run_text
        assert "--cov-fail-under=70" in nightly_run_text

    def test_release_workflow_configuration(self):
        release_workflow_path = REPO_ROOT / ".github" / "workflows" / "release.yml"

        with open(release_workflow_path, encoding="utf-8") as f:
            workflow = yaml.safe_load(f)

        assert "name" in workflow
        assert workflow["name"] == "Release"
        assert True in workflow
        assert "jobs" in workflow

        on_events = workflow[True]
        assert set(on_events) == {"workflow_dispatch"}

        jobs = workflow["jobs"]
        required_jobs = ["dependency-bounds", "build", "publish", "github-release", "test-installation"]
        for job in required_jobs:
            assert job in jobs, f"Release workflow must have {job} job"
        assert "check-ci-passed" not in jobs

        release_workflow_text = release_workflow_path.read_text(encoding="utf-8")
        forbidden_pytest_invocations = ["python -m pytest", "uv run pytest", "uv run -- pytest"]
        for invocation in forbidden_pytest_invocations:
            assert invocation not in release_workflow_text, "release.yml publishes from tags and does not run pytest"

        github_release_steps = jobs["github-release"]["steps"]
        extract_steps = [step for step in github_release_steps if step.get("name") == "Extract curated release notes"]
        assert len(extract_steps) == 1
        assert "generate_changelog_entry.py" in extract_steps[0]["run"]
        assert "--print-section" in extract_steps[0]["run"]
        create_steps = [step for step in github_release_steps if step.get("name") == "Create or update GitHub Release"]
        assert len(create_steps) == 1
        release_step = create_steps[0]["run"]
        assert "gh release view" in release_step
        assert "gh release create" in release_step
        assert "gh release upload" in release_step
        assert "gh release edit" in release_step
        assert "--clobber" in release_step
        assert "--notes-file release-notes.md" in release_step
        assert "--generate-notes" not in release_step

        publish_job = jobs["publish"]
        assert "permissions" in publish_job
        assert "id-token" in publish_job["permissions"]
        assert publish_job["permissions"]["id-token"] == "write"

    def test_release_smoke_test_runs_before_publication_from_built_wheel(self):
        jobs = _workflow("release.yml")["jobs"]
        test_installation_job = jobs["test-installation"]

        needs = test_installation_job["needs"]
        needs = [needs] if isinstance(needs, str) else needs
        assert "build" in needs, "test-installation must depend on build to reach needs.build.outputs.version"
        assert "publish" not in needs, "pre-publication installation validation must not depend on publish"
        publish_needs = jobs["publish"]["needs"]
        publish_needs = [publish_needs] if isinstance(publish_needs, str) else publish_needs
        assert "test-installation" in publish_needs

        release_workflow_text = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        assert "pip install benchbox" not in release_workflow_text, "smoke test must not install an unpinned benchbox"
        assert "python -m pip install dist/*.whl" in release_workflow_text
        assert "Download build artifacts" in release_workflow_text

        steps = test_installation_job["steps"]
        install_steps = [s for s in steps if s.get("name", "").startswith("Test installation from")]
        assert len(install_steps) == 1, "expected exactly one pre-publication built-wheel install step"
        for step in install_steps:
            assert step.get("shell") == "bash", (
                f"{step['name']!r} must pin shell: bash - the matrix includes windows-latest"
            )

    def test_release_tag_must_match_package_version_before_build_or_publish(self):
        jobs = _workflow("release.yml")["jobs"]
        verify_steps = jobs["verify-tag-on-release"]["steps"]
        step_names = [step.get("name") for step in verify_steps]
        assertion_index = step_names.index("Assert tag matches package version")
        assertion = verify_steps[assertion_index]

        assert assertion["if"] == "startsWith(github.ref, 'refs/tags/v')"
        assert assertion["shell"] == "bash"
        assert assertion["env"]["RELEASE_TAG"] == "${{ github.ref_name }}"
        run = assertion["run"]
        assert "pyproject.toml" in run
        assert 'expected_tag="v${package_version}"' in run
        assert 'if [[ "$RELEASE_TAG" != "$expected_tag" ]]' in run
        assert "::error::Release tag" in run
        assert "exit 1" in run
        assert step_names.index("Assert release ref is release or an ancestor of release") < assertion_index

        build_needs = jobs["build"]["needs"]
        build_needs = [build_needs] if isinstance(build_needs, str) else build_needs
        assert "verify-tag-on-release" in build_needs

    def test_release_tag_version_assertion_accepts_match_and_rejects_mismatch(self):
        workflow = _workflow("release.yml")
        assertion = next(
            step
            for step in workflow["jobs"]["verify-tag-on-release"]["steps"]
            if step.get("name") == "Assert tag matches package version"
        )
        with open(REPO_ROOT / "pyproject.toml", "rb") as pyproject_file:
            package_version = tomllib.load(pyproject_file)["project"]["version"]

        skip_without_posix_shell()
        matching = run_posix_shell(
            assertion["run"],
            cwd=REPO_ROOT,
            env={**os.environ, "RELEASE_TAG": f"v{package_version}"},
            capture_output=True,
            text=True,
        )
        assert matching.returncode == 0, matching.stdout + matching.stderr

        mismatched = run_posix_shell(
            assertion["run"],
            cwd=REPO_ROOT,
            env={**os.environ, "RELEASE_TAG": f"v{package_version}-wrong"},
            capture_output=True,
            text=True,
        )
        assert mismatched.returncode != 0
        assert "::error::Release tag" in mismatched.stdout

    def test_test_pypi_workflow_dispatch_remains_available_from_branch_refs(self):
        jobs = _workflow("release.yml")["jobs"]
        verify_steps = jobs["verify-tag-on-release"]["steps"]
        assertion = next(step for step in verify_steps if step.get("name") == "Assert tag matches package version")
        publish_steps = jobs["publish"]["steps"]
        test_pypi = next(step for step in publish_steps if step.get("name") == "Publish to Test PyPI")
        pypi = next(step for step in publish_steps if step.get("name") == "Publish to PyPI")

        assert assertion["if"] == "startsWith(github.ref, 'refs/tags/v')"
        assert test_pypi["if"] == "github.event.inputs.test_pypi == 'true'"
        assert pypi["if"] == "github.event.inputs.test_pypi != 'true' && startsWith(github.ref, 'refs/tags/v')"

    def test_release_required_result_contract(self):
        jobs = _workflow("test.yml")["jobs"]

        assert jobs["integration"]["if"] == (
            "github.event_name == 'push' || (github.event_name == 'pull_request' && github.base_ref == 'release')"
        )
        integration_run_text = _workflow_job_run_text("test.yml", "integration")
        assert f'tests/integration -m "{RELEASE_INTEGRATION_EXPRESSION}"' in integration_run_text

        test_package_run_text = _workflow_job_run_text("test.yml", "test-package")
        assert "wheel_count=$(find dist -maxdepth 1 -name '*.whl'" in test_package_run_text
        assert 'uv run --isolated --no-project --with "$wheel"' in test_package_run_text
        assert "benchbox --help" in test_package_run_text

        for job_name in ["compat-test", "integration-smoke"]:
            condition = jobs[job_name]["if"]
            assert "github.event.pull_request.title" not in condition
            assert RELEASE_PR_BRANCH_SKIP in condition

        make_test_package = _make_target_recipe("test-package")
        assert "test-venv" not in make_test_package
        assert "Expected exactly one wheel" in make_test_package
        assert 'uv run --isolated --no-project --with "$$wheel"' in make_test_package

        release_readiness = jobs["release-readiness"]
        assert release_readiness["if"] == "${{ github.event_name == 'pull_request' && github.base_ref == 'release' }}"
        readiness_run_text = _workflow_job_run_text("test.yml", "release-readiness")
        assert "scripts/check_dependency_bounds.py" in readiness_run_text
        assert "--fail-on=cap-reached" in readiness_run_text
        assert "Check release branch curation" in str(jobs["release-readiness"]["steps"])
        assert "Release branch still contains curated path" in readiness_run_text
        assert "Curated Explorer publication path is missing" in readiness_run_text
        assert "non-curated _project path" in readiness_run_text
        assert "git ls-files -- _project" in readiness_run_text
        for retained_path in [
            "results-data",
            "_project/scripts/explorer_pipeline",
            "_project/scripts/explorer_publish.py",
            "_project/scripts/results_explorer_snapshot_invariants.py",
        ]:
            assert retained_path in readiness_run_text
        assert "_project" in readiness_run_text
        assert ".github/workflows/validate-submission.yml" in readiness_run_text

        result_job = jobs["release-required-result"]
        assert result_job["name"] == "release-required-result"
        assert set(result_job["needs"]) == {
            "test",
            "integration",
            "medium",
            "medium-coverage",
            "correctness-gate",
            "test-package",
            "release-readiness",
        }
        assert (
            result_job["if"] == "${{ always() && github.event_name == 'pull_request' && github.base_ref == 'release' }}"
        )
        aggregate_run_text = _workflow_job_run_text("test.yml", "release-required-result")
        for expected in [
            "test (ubuntu-latest, 3.12)",
            "integration",
            "correctness-gate",
            "test-package",
            "release-readiness",
            "Release-required checks passed.",
        ]:
            assert expected in aggregate_run_text

    def test_release_required_result_runs_the_complete_medium_tier(self):
        jobs = _workflow("test.yml")["jobs"]
        collect_text = _workflow_job_run_text("test.yml", "medium-collect")
        shard_text = _workflow_job_run_text("test.yml", "medium")

        assert "github.base_ref == 'release'" in jobs["medium-collect"]["if"]
        assert jobs["medium"]["needs"] == "medium-collect"
        assert jobs["medium"]["strategy"]["matrix"]["shard_index"] == [0, 1, 2, 3]
        assert "pytest --collect-only" in collect_text and "--ignore" not in collect_text
        assert '-m "medium and not (slow or stress or resource_heavy or live_integration)"' in shard_text
        assert '"${node_ids[@]}"' in shard_text
        assert "-p scripts.pytest_shard_evidence" in shard_text and "--assigned-nodeids" in shard_text
        assert '"medium=$MEDIUM_RESULT"' in _workflow_job_run_text("test.yml", "release-required-result")
        assert set(jobs["medium-coverage"]["needs"]) == {"medium-collect", "medium"}
        coverage_text = _workflow_job_run_text("test.yml", "medium-coverage")
        assert "verify-medium" in coverage_text
        assert "--workflow test.yml --artifact-prefix release-medium" in coverage_text
        assert '"medium-coverage=$MEDIUM_COVERAGE_RESULT"' in _workflow_job_run_text(
            "test.yml", "release-required-result"
        )

    def test_release_docs_name_required_contexts(self):
        docs_paths = [
            REPO_ROOT / "docs" / "internal" / "release-guide.md",
            REPO_ROOT / "docs" / "internal" / "repo-admin-settings.md",
            REPO_ROOT / ".github" / "RELEASE_PR_TEMPLATE.md",
        ]

        for path in docs_paths:
            content = path.read_text(encoding="utf-8")
            for context in RELEASE_REQUIRED_CONTEXTS:
                assert context in content

    def test_release_canary_workflow_contract(self):
        workflow = _workflow("release-canary.yml")
        on_events = workflow[True]
        assert "workflow_dispatch" in on_events
        assert on_events["schedule"] == [{"cron": "0 8 * * *"}]
        assert workflow["permissions"]["actions"] == "read"
        assert workflow["permissions"]["contents"] == "read"
        assert workflow["env"]["RELEASE_CANARY_REF"] == "develop"

        jobs = workflow["jobs"]
        assert set(jobs) == {
            "collect-credential-free-non-fast",
            "credential-free-non-fast",
            "ruleset-drift",
            "pypi-latest-installability",
            "release-canary-result",
            "release-canary-incident",
        }
        collection_job = jobs["collect-credential-free-non-fast"]
        assert collection_job["steps"][0]["with"]["ref"] == "${{ env.RELEASE_CANARY_REF }}"
        assert jobs["ruleset-drift"]["steps"][0]["with"]["ref"] == "${{ env.RELEASE_CANARY_REF }}"
        assert jobs["ruleset-drift"]["outputs"]["drift_sha"] == "${{ steps.drift-ref.outputs.drift_sha }}"
        non_fast_job = jobs["credential-free-non-fast"]
        assert non_fast_job["timeout-minutes"] == 75
        assert non_fast_job["strategy"]["fail-fast"] is False
        assert len(non_fast_job["strategy"]["matrix"]["include"]) == 6

        pypi_latest_job = jobs["pypi-latest-installability"]
        assert "needs" not in pypi_latest_job
        assert not any(step.get("uses", "").startswith("actions/checkout") for step in pypi_latest_job["steps"])
        pypi_latest_text = _workflow_job_run_text("release-canary.yml", "pypi-latest-installability")
        assert "uv run --isolated --no-project --with benchbox --" in pypi_latest_text
        assert "importlib.util.find_spec('pandas') is None" in pypi_latest_text
        assert "mktemp -d" in pypi_latest_text

        collection_text = _workflow_job_run_text("release-canary.yml", "collect-credential-free-non-fast")
        assert RELEASE_CANARY_NON_FAST_EXPRESSION in collection_text
        assert "--collect-only" in collection_text
        assert "release-canary-artifacts/non-fast-collection-summary.json" in collection_text
        assert "--checked-ref develop" in collection_text
        assert "--checked-sha" in collection_text
        assert "raw" not in collection_text.lower()
        assert "set +e" not in collection_text

        non_fast_text = _workflow_job_run_text("release-canary.yml", "credential-free-non-fast")
        assert RELEASE_CANARY_NON_FAST_EXPRESSION in non_fast_text
        assert "release-canary-artifacts/shard-${SHARD_INDEX}-summary.json" in non_fast_text
        assert "actions/download-artifact@v4" not in non_fast_text
        assert "mapfile -t node_ids" in non_fast_text
        assert "-n 0" in non_fast_text
        assert "--maxfail=5" in non_fast_text
        assert 'exit "$rc"' in non_fast_text
        assert "release-canary-artifacts/shard-${SHARD_INDEX}-junit.xml" in non_fast_text
        assert ".canary-setup-start-mono" in non_fast_text
        assert '"setup_seconds": "${setup_seconds}"' in non_fast_text
        assert '"test_seconds": "${test_seconds}"' in non_fast_text
        assert '"junit_present": ${junit_present}' in non_fast_text
        assert "/proc/uptime" in non_fast_text
        assert "date +%s" not in non_fast_text
        assert "set +e" in non_fast_text
        assert '"junit_captured": ${junit_captured}' in non_fast_text
        assert ".canary-junit-captured" in non_fast_text

        ruleset_text = _workflow_job_run_text("release-canary.yml", "ruleset-drift")
        assert "scripts/ruleset_drift_check.py" in ruleset_text
        assert "RULESET_DRIFT_TOKEN" in ruleset_text
        assert "--require-bypass-actor-visibility" in ruleset_text
        assert "release-canary-artifacts/ruleset-drift.json" in ruleset_text
        assert "set +e" not in ruleset_text
        assert "exit 0" not in ruleset_text

        result_job = jobs["release-canary-result"]
        assert result_job["name"] == "release-canary-result"
        assert set(result_job["needs"]) == {
            "collect-credential-free-non-fast",
            "credential-free-non-fast",
            "ruleset-drift",
            "pypi-latest-installability",
        }
        result_text = _workflow_job_run_text("release-canary.yml", "release-canary-result")
        assert '"checked_ref": "develop"' in result_text
        assert '"commit_sha": "${CHECKED_SHA}"' in result_text
        assert '"collection_result": "${COLLECTION_RESULT}"' in result_text
        assert '"ruleset_drift_sha": "${RULESET_DRIFT_SHA}"' in result_text
        assert '"freshness_contract_hours": 48' in result_text
        assert "Release canary passed." in result_text

        assert "issues" not in workflow["permissions"]
        incident_job = jobs["release-canary-incident"]
        assert incident_job["if"] == "${{ always() && !cancelled() }}"
        assert incident_job["permissions"]["issues"] == "write"
        assert incident_job["continue-on-error"] is True
        assert {
            "collect-credential-free-non-fast",
            "credential-free-non-fast",
            "release-canary-result",
        } <= set(incident_job["needs"])
        incident_text = _workflow_job_run_text("release-canary.yml", "release-canary-incident")
        assert "scripts/release_canary_incident.py" in incident_text
        assert '"release-canary-result=${AGGREGATE_RESULT}"' in incident_text
        assert "incident:release-canary-red" in incident_text
        assert "gh issue close" in incident_text

    def test_canary_collect_count_regex_matches_pytest_deselect_format(self):
        collect_text = _workflow_job_run_text("release-canary.yml", "collect-credential-free-non-fast")
        assert "'^[0-9]+/[0-9]+ tests collected'" in collect_text

        pattern = re.compile(r"^\d+/\d+ tests collected")
        assert pattern.match("92/24795 tests collected (24703 deselected) in 16.98s")
        assert pattern.match("1/100 tests collected")
        assert not pattern.match("24795 tests collected in 16.98s")
        assert not pattern.match("0 tests collected")

    def test_validate_main_pr_checks_release_canary_freshness(self):
        workflow = _workflow("validate-release-pr.yml")
        assert workflow["permissions"]["actions"] == "read"
        assert workflow["permissions"]["contents"] == "read"

        job = workflow["jobs"]["validate-base"]
        steps = job["steps"]
        checkout_step = next(
            step for step in steps if re.fullmatch(r"actions/checkout@[0-9a-f]{40}", str(step.get("uses") or ""))
        )
        assert checkout_step["name"] == "Checkout trusted release policy"
        assert checkout_step["with"]["ref"] == "${{ github.event.pull_request.base.sha }}"
        assert checkout_step["with"]["fetch-depth"] == 0
        assert "${{ github.event.pull_request.head.sha }}" not in str(checkout_step)

        run_text = _workflow_job_run_text("validate-release-pr.yml", "validate-base")
        assert "git fetch --prune origin develop" in run_text
        assert "refs/pull/${{ github.event.pull_request.number }}/head" in run_text
        assert "scripts/release_readiness_check.py" in run_text
        assert '--head-sha "${{ github.event.pull_request.head.sha }}"' in run_text
        assert "--bootstrap-on-missing-workflow" in run_text
        assert "bootstrap_required=true" in run_text
        canary_run_text = _workflow_job_run_text("release-canary.yml", "credential-free-non-fast")
        assert run_text.count(RELEASE_CANARY_NON_FAST_EXPRESSION) == 2
        assert RELEASE_CANARY_NON_FAST_EXPRESSION in canary_run_text
        assert " or e2e" not in run_text
        assert "TestFractionalScaleFactors" not in run_text
        assert "scripts/ruleset_drift_check.py" in run_text
        assert "--require-bypass-actor-visibility" in run_text

        readiness_step = next(step for step in steps if step["name"] == "Check release canary freshness")
        assert readiness_step["env"]["RELEASE_CANARY_WORKFLOW"] == "release-canary.yml"
        assert readiness_step["env"]["RELEASE_CANARY_BRANCH"] == "develop"
        assert readiness_step["env"]["RELEASE_CANARY_CHECKED_REF"] == "develop"
        assert readiness_step["env"]["RELEASE_CANARY_MAX_AGE_HOURS"] == "48"
        assert "RELEASE_READINESS_OVERRIDE_SHA" in readiness_step["env"]

    def test_validate_main_pr_restores_ruleset_helper_before_drift_check(self):
        job = _workflow("validate-release-pr.yml")["jobs"]["validate-base"]
        step_names = [step.get("name") for step in job["steps"]]
        restore_index = step_names.index("Restore ruleset review enforcement helper for bootstrap")
        drift_check_index = step_names.index("Bootstrap ruleset drift evidence")
        assert restore_index < drift_check_index

        restore_step = job["steps"][restore_index]
        assert restore_step["if"] == "steps.release-readiness.outputs.bootstrap_required == 'true'"
        assert "_project/scripts/ruleset_review_enforcement.py" in restore_step["run"]
        assert "origin/develop" in restore_step["run"]

    def test_release_docs_name_canary_and_ruleset_drift(self):
        docs_paths = [
            REPO_ROOT / "docs" / "internal" / "release-guide.md",
            REPO_ROOT / "docs" / "internal" / "repo-admin-settings.md",
            REPO_ROOT / ".github" / "RELEASE_PR_TEMPLATE.md",
        ]

        for path in docs_paths:
            content = path.read_text(encoding="utf-8")
            assert "canary" in content
            assert "ruleset drift" in content
            assert "48" in content
            assert "RELEASE_READINESS_OVERRIDE_SHA" in content

    def test_release_finalize_checks_required_context_before_merge_and_tag(self):
        makefile_content = _makefile_text()
        recipe = _make_target_recipe("release-finalize")
        finalize = (REPO_ROOT / "scripts" / "release_finalize.py").read_text(encoding="utf-8")

        assert "RELEASE_REQUIRED_CONTEXTS := validate-base release-required-result" in makefile_content
        assert '--required-contexts "$(RELEASE_REQUIRED_CONTEXTS)"' in recipe
        assert "scripts/release_finalize.py" in recipe
        assert '"--required"' in finalize
        assert '"name,bucket,state"' in finalize
        assert "pending_ok=True" in finalize
        assert 'f"sha={head}"' in finalize
        assert '"merge_method=squash"' in finalize
        assert 'command("git", "fetch", "origin", "--tags")' in finalize
        assert 'f"refs/tags/{tag}:refs/tags/{tag}"' in finalize
        assert '"git", "checkout", "release"' not in finalize

    def test_release_finalize_blocks_pending_required_check_exit_code(self):
        finalize = (REPO_ROOT / "scripts" / "release_finalize.py").read_text(encoding="utf-8")

        assert "result.returncode == 8" in finalize
        assert "Required PR checks are pending. Wait for GitHub Actions, then rerun" in finalize

    def test_release_finalize_failure_modes_are_explicit(self):
        finalize = (REPO_ROOT / "scripts" / "release_finalize.py").read_text(encoding="utf-8")

        for expected in [
            "Required release context",
            "missing or duplicated",
            "Required PR checks are pending",
            "Release PR state or head changed",
            "Local tag",
            "not reachable from fetched origin/release",
        ]:
            assert expected in finalize

        assert 'in ("OPEN", "MERGED")' in finalize
        assert "merge_method=squash" in finalize

    def test_release_finalize_docs_separate_premerge_and_postmerge_signals(self):
        makefile_content = _makefile_text()
        release_guide = (REPO_ROOT / "docs" / "internal" / "release-guide.md").read_text(encoding="utf-8")
        release_template = (REPO_ROOT / ".github" / "RELEASE_PR_TEMPLATE.md").read_text(encoding="utf-8")

        assert "Wait for CI green" not in makefile_content
        assert "CI is not green" not in makefile_content
        assert "required release contexts: $(RELEASE_REQUIRED_CONTEXTS)" in makefile_content
        assert "Push-to-release jobs are post-merge signals" in makefile_content

        for content in [release_guide, release_template]:
            normalized = content.lower()
            for context in RELEASE_REQUIRED_CONTEXTS:
                assert context in content
            assert "post-merge" in normalized
            assert "pre-merge" in normalized
            assert "patch release or incident" in content

        assert "curated Results Explorer inputs" in release_template
        assert "three curated Explorer" in release_template
        assert "non-curated portions of `_project/`" in release_template

    def test_release_cut_requires_the_prepared_changelog_instead_of_drafting_one(self):
        recipe = _make_target_recipe("release-cut")
        release_guide = (REPO_ROOT / "docs" / "internal" / "release-guide.md").read_text(encoding="utf-8")
        release_template = (REPO_ROOT / ".github" / "RELEASE_PR_TEMPLATE.md").read_text(encoding="utf-8")

        assert "scripts/generate_changelog_entry.py --version $(VERSION) --require-prepared" in recipe
        assert "--since-ref" not in recipe
        assert "`origin/release` patch delta" not in release_template
        assert "compiled from `changelog.d`" in release_template
        assert "--require-prepared" in release_guide
        assert "--compile-fragments" in release_guide

    def test_release_cut_gates_on_prepared_changelog_and_curation_before_git_rm(self):
        recipe = _make_target_recipe("release-cut")

        assert "scripts/generate_changelog_entry.py --check-curation --version $(VERSION)" in recipe
        assert "refusing to skip changelog curation" not in recipe
        prepared_idx = recipe.index("--require-prepared")
        check_idx = recipe.index("--check-curation")
        rm_idx = recipe.index("git rm")
        assert prepared_idx < check_idx < rm_idx, "the prepared and curation gates must run before `git rm`"

    def test_release_cut_takes_a_curated_section_in_one_pass_without_skipping_gates(self):
        recipe = _make_target_recipe("release-cut")

        assert '--section-file "$(CHANGELOG_SECTION)"' in recipe
        assert recipe.index('sh scripts/release_cut_start.sh "$(VERSION)"') < recipe.index("--section-file")
        assert recipe.index("--section-file") < recipe.index("--require-prepared")
        assert recipe.index("--require-prepared") < recipe.index("--check-curation --version $(VERSION)")
        assert 'if [ -n "$(CHANGELOG_SECTION)" ]' in recipe
        assert recipe.count("CHANGELOG_SECTION") == 3, "CHANGELOG_SECTION must not gate anything else"

    def test_release_cut_curation_accounts_for_the_changelog_fragment_directory(self):
        recipe = _make_target_recipe("release-cut")
        removal = next(line for line in recipe.splitlines() if "git rm -rf --ignore-unmatch changelog.d" in line)
        guard_line = next(line for line in recipe.splitlines() if "LEFTOVER=$$(git ls-files" in line)

        assert "changelog.d" in removal.split()
        assert "changelog.d" in guard_line.split()

    def test_release_cut_is_resumable_and_has_an_abort_target(self):
        recipe = _make_target_recipe("release-cut")
        assert "release-cut: .release-cut-tree-required" in _makefile_text()
        development_only = (
            _makefile_text()
            .split("DEVELOPMENT_TREE_ONLY_TARGETS :=", 1)[1]
            .split("\n\n.PHONY", 1)[0]
            .replace("\\", "")
            .split()
        )
        assert "release-cut" not in development_only
        assert 'sh scripts/release_cut_start.sh "$(VERSION)"' in recipe
        start = (REPO_ROOT / "scripts" / "release_cut_start.sh").read_text(encoding="utf-8")
        assert "Resuming interrupted cut" in start
        assert "already carries its release commit" in start
        assert 'git switch -c "$branch" "$develop"' in start
        assert 'git show-ref --verify --quiet "refs/heads/$branch"' in start

        assert "--first-parent" in start, (
            "the resume guard must walk first-parent: the `-s ours` alignment merge puts "
            "origin/release's release ledger (full of 'Release vX.Y.Z' subjects) on the second parent, "
            "and a plain `git log -1` misses a release commit sitting beneath that merge"
        )

        abort = _make_target_recipe("release-cut-abort")
        assert 'sh scripts/release_cut_abort.sh "$(VERSION)"' in abort
        abort_script = (REPO_ROOT / "scripts" / "release_cut_abort.sh").read_text(encoding="utf-8")
        assert "git switch --discard-changes" in abort_script
        assert 'git branch -d "$branch"' in abort_script
        assert "git ls-remote --heads origin" in abort_script
        phony_lines = {line.strip() for line in _makefile_text().splitlines() if line.startswith(".PHONY:")}
        for target in ("release-cut", "release-cut-abort", "release-finalize"):
            assert f".PHONY: {target}" in phony_lines

    def test_release_cut_curation_survives_untracked_paths(self):
        recipe = _make_target_recipe("release-cut")
        rm_lines = [line.strip() for line in recipe.splitlines() if re.search(r"git rm (?:-rf|-f) ", line)]
        assert rm_lines, "expected git rm curation lines in release-cut"
        for line in rm_lines:
            assert "--ignore-unmatch" in line, f"curation line missing --ignore-unmatch: {line}"
            assert not line.startswith("-"), f"real git rm failures must abort the cut (drop `-` prefix): {line}"

    def test_release_cut_post_curation_guard_covers_every_curated_path(self):
        recipe = _make_target_recipe("release-cut")
        rm_paths: set[str] = set()
        for line in recipe.splitlines():
            rm_match = re.search(r"git rm (?:-rf|-f) --ignore-unmatch (.+?)$", line.strip())
            if rm_match:
                rm_paths.update(shlex.split(rm_match.group(1)))
        assert rm_paths, "expected git rm curation paths in release-cut"
        guard_line = next(
            (line.strip() for line in recipe.splitlines() if "git ls-files _project" in line),
            None,
        )
        assert guard_line, "expected a `git ls-files <curated paths>` post-curation guard in release-cut"
        guard_match = re.search(r"git ls-files (.+?)\);", guard_line)
        assert guard_match, "expected a semicolon-terminated curation guard command"
        guard_paths = set(shlex.split(guard_match.group(1)))
        assert rm_paths == guard_paths, (
            f"post-curation guard out of sync with git rm lines; "
            f"missing from guard: {sorted(rm_paths - guard_paths)}, "
            f"extra in guard: {sorted(guard_paths - rm_paths)}"
        )

    def test_release_cut_covers_v040_release_pr_test_curation(self):
        recipe = _make_target_recipe("release-cut")
        curated_paths: set[str] = set()
        for line in recipe.splitlines():
            rm_match = re.search(r"git rm (?:-rf|-f) --ignore-unmatch (.+?)$", line.strip())
            if rm_match:
                curated_paths.update(shlex.split(rm_match.group(1)))

        v040_missed_paths = {
            "tests/integration/test_todo_db_standalone_compat_real.py",
            "tests/unit/core/equivalence/test_cross_surface_baseline_autodetect.py",
            "tests/unit/docs/test_architecture_decision_surfaces.py",
            "tests/unit/scripts/test_agent_instruction_audit.py",
            "tests/unit/scripts/test_audit_sha_check.py",
            "tests/unit/scripts/test_browser_gate_aggregate.py",
            "tests/unit/scripts/test_check_release_curation.py",
            "tests/unit/scripts/test_check_uv_lock_revision.py",
            "tests/unit/scripts/test_ci_lint_environment_boundary.py",
            "tests/unit/scripts/test_corpus_privacy_invariant.py",
            "tests/unit/scripts/test_dev_loop_pr_metrics.py",
            "tests/unit/scripts/test_green_unmerged_sweep.py",
            "tests/unit/scripts/test_guard_messages.py",
            "tests/unit/scripts/test_mirror_partial_validation_policy.py",
            "tests/unit/scripts/test_path_filter_decision.py",
            "tests/unit/scripts/test_results_explorer_corpus_migrate.py",
            "tests/unit/scripts/test_results_explorer_snapshot_invariants.py",
            "tests/unit/scripts/test_skill_sync_ci_policy.py",
            "tests/unit/scripts/test_soundness_drain_report.py",
            "tests/unit/scripts/test_fast_lane_ceiling_check.py",
            "tests/unit/scripts/test_timing_policy_check.py",
            "tests/unit/scripts/test_todo_db_shadow.py",
            "tests/unit/scripts/test_todo_db_standalone_compat.py",
            "tests/unit/scripts/test_todo_schema_migration_check.py",
            "tests/unit/scripts/test_todo_verification_lint.py",
            "tests/unit/scripts/test_todo_wrapper.py",
            "tests/unit/test_auto_merge_hold_is_durable.py",
            "tests/unit/test_release_infrastructure.py",
            "tests/unit/workflows/test_auto_merge_partial_stack_race.py",
            "tests/unit/workflows/test_develop_post_merge_gaps.py",
            "tests/unit/workflows/test_merge_group_triggers.py",
            "tests/unit/workflows/test_published_results_base_ci.py",
            "tests/unit/workflows/test_results_explorer_browser_gate.py",
            "tests/unit/workflows/test_results_explorer_dependency_audit.py",
            "tests/unit/workflows/test_seed_corpus_pr_base.py",
            "tests/unit/workflows/test_validate_submission_changed_bundles.py",
            "tests/unit/workflows/test_validate_submission_fail_open.py",
        }
        assert len(v040_missed_paths) == 37
        assert v040_missed_paths <= curated_paths, (
            "release-cut is missing tests that had to be curated manually in the v0.4.0 release PR: "
            f"{sorted(v040_missed_paths - curated_paths)}"
        )

    def test_release_cut_refreshes_and_stages_uv_lock(self):
        recipe = _make_target_recipe("release-cut")
        lock_match = re.search(r"^\tuv lock\s*$", recipe, re.MULTILINE)
        assert lock_match, "release-cut must run `uv lock` to refresh the lockfile"
        bump_idx = recipe.index("scripts/update_version.py")
        assert bump_idx < lock_match.start(), "`uv lock` must run after the pyproject version bump"
        add_match = re.search(r"^\tgit add (.+?)$", recipe, re.MULTILINE)
        assert add_match, "expected explicit git add line in release-cut"
        assert "uv.lock" in add_match.group(1).split(), "uv.lock must be staged with the release commit"

    def test_release_cut_commit_skips_hooks_after_curation_removes_them(self):
        recipe = _make_target_recipe("release-cut")
        commit_match = re.search(r"^\tgit commit (.+?)$", recipe, re.MULTILINE)
        assert commit_match, "expected a git commit line in release-cut"
        assert "--no-verify" in commit_match.group(1).split(), (
            f"release commit must skip hooks (curation already deleted the hook config): {commit_match.group(1)}"
        )

    def test_release_pushes_allow_the_intentionally_missing_precommit_config(self):
        recipe = _make_target_recipe("release-cut")
        push_lines = [
            line.strip()
            for line in recipe.splitlines()
            if "git push" in line and not line.lstrip().startswith("@#") and not line.strip().startswith("echo ")
        ]
        assert push_lines
        for line in push_lines:
            assert "PRE_COMMIT_ALLOW_NO_CONFIG=1 git push" in line

        finalize = (REPO_ROOT / "scripts" / "release_finalize.py").read_text(encoding="utf-8")
        assert 'push_env["PRE_COMMIT_ALLOW_NO_CONFIG"] = "1"' in finalize
        assert 'command("git", "push", "origin", f"refs/tags/{tag}:refs/tags/{tag}", env=push_env)' in finalize

    def test_release_cut_aligns_release_histories_before_pushing(self):
        recipe = _make_target_recipe("release-cut")
        merge_match = re.search(r"^\t@?git merge (.+?)$", recipe, re.MULTILINE)
        assert merge_match, "release-cut must merge origin/release to make the release PR mergeable"

        merge_args = merge_match.group(1).split()
        assert "-s" in merge_args and merge_args[merge_args.index("-s") + 1] == "ours", (
            f"alignment merge must use `-s ours` to preserve the curated tree: {merge_match.group(1)}"
        )
        assert "--allow-unrelated-histories" in merge_args, (
            "main and develop have unrelated roots; the merge needs --allow-unrelated-histories"
        )
        assert "origin/release" in merge_args, "the alignment merge must target the fetched origin/release"

        assert "alignment merge changed the curated release tree" in recipe, (
            "expected a post-merge guard asserting the curated release tree is unchanged"
        )

        commit_match = re.search(r"^\tgit commit .*Release v\$\(VERSION\)", recipe, re.MULTILINE)
        push_match = re.search(
            r"^\tPRE_COMMIT_ALLOW_NO_CONFIG=1 git push -u origin v\$\(VERSION\)", recipe, re.MULTILINE
        )
        assert commit_match, "expected the `Release v$(VERSION)` commit line in release-cut"
        assert push_match, "expected `git push -u origin v$(VERSION)` in release-cut"
        changelog_idx = recipe.index("--require-prepared")
        assert changelog_idx < commit_match.start() < merge_match.start() < push_match.start(), (
            "alignment merge must run after the `Release v$(VERSION)` commit and before the push"
        )

    def test_release_cut_branch_sweep_matches_only_release_branches(self):
        recipe = _make_target_recipe("release-cut")
        sweep_match = re.search(r"^\t@?for br in \$\$\((.+?)\); do", recipe, re.MULTILINE)
        assert sweep_match, "expected the stale release-branch sweep `for br in $$(...)` in release-cut"
        pipeline = sweep_match.group(1)
        assert "awk" in pipeline, "sweep must filter with awk against the full ref, not just `ls-remote 'v*'`"

        awk_match = re.search(r"awk '(.+?)'", pipeline)
        assert awk_match, "expected an awk filter in the sweep pipeline"
        awk_program = awk_match.group(1)
        assert re.search(r"\$2\s*~", awk_program), "awk filter must match against the full ref ($2), not $1/$NF"
        assert r"refs\/heads\/" in awk_program or "refs/heads/" in awk_program, (
            "awk filter must anchor to refs/heads/ to avoid matching bare tag-ish names"
        )

        sample_refs = [
            "refs/heads/v0.3.0",
            "refs/heads/v0.3.1",
            "refs/heads/v0.3.1-rc1",
            "refs/heads/fix/validate-submission-self-green-guard",
            "refs/heads/version-bump-tooling",
            "refs/heads/vendor/foo",
        ]
        sample_input = "\n".join(f"deadbeef\t{ref}" for ref in sample_refs)
        pipeline_without_ls_remote = re.sub(r"^git ls-remote --heads origin 'v\*'\s*\|\s*", "", pipeline)
        pipeline_without_version_grep = re.sub(
            r'\s*\|\s*grep -Fxv "v\$\(VERSION\)"\s*$', "", pipeline_without_ls_remote
        )
        assert pipeline_without_version_grep != pipeline_without_ls_remote, (
            "expected to isolate the awk/sed stages from the trailing version-exclusion grep"
        )
        shell_pipeline = pipeline_without_version_grep.replace("$$", "$")
        skip_without_posix_shell()
        result = run_posix_shell(
            shell_pipeline,
            input=sample_input,
            capture_output=True,
            text=True,
            check=True,
        )
        matched = set(result.stdout.split())
        assert matched == {"v0.3.0", "v0.3.1", "v0.3.1-rc1"}, (
            f"sweep must match only true release branches, got: {sorted(matched)}"
        )

    def test_worktree_and_pr_guards_still_block_stale_main(self):
        recipe = _make_target_recipe("pr-open")
        assert 'develop|main|release) echo "Refusing to open PR from $$CURRENT' in recipe

        recipe = _make_target_recipe("pr-fanout")
        assert 'develop|main|release|"") echo "(skip $$wt: branch=$$BR)"' in recipe

        recipe = _make_target_recipe("pr-refresh")
        assert 'develop|main|release|"") echo "Refusing to refresh $$CURRENT' in recipe

    def test_pr_open_publishes_behind_branches_without_a_queue_check(self):
        recipe = _make_target_recipe("pr-open")
        assert "git merge-base --is-ancestor origin/develop HEAD" in recipe
        assert "git merge-tree --write-tree origin/develop HEAD" in recipe
        assert "queue-policy" not in recipe
        assert "ruleset_drift_check" not in recipe
        assert "git merge --no-edit origin/develop" not in recipe
        fetch_at = recipe.find("git fetch origin develop --quiet")
        gate_at = recipe.find("git merge-base --is-ancestor origin/develop HEAD")
        conflict_at = recipe.find("git merge-tree --write-tree origin/develop HEAD")
        trunk_at = recipe.find("scripts/trunk_revert.py gate")
        push_at = recipe.find("git push -u origin")
        assert fetch_at != -1 and gate_at != -1 and conflict_at != -1 and trunk_at != -1 and push_at != -1
        assert fetch_at < gate_at < conflict_at < trunk_at < push_at

        refresh = _make_target_recipe("pr-refresh")
        assert "git merge --no-edit origin/develop" in refresh
        assert "$(MAKE) -s pr-open" in refresh

        fanout = _make_target_recipe("pr-fanout")
        assert "$(MAKE) -s pr-open" in fanout
        assert "pr-refresh" not in fanout

        makefile_content = _makefile_text()
        assert "worktree-release-locked" not in makefile_content

    def test_pr_open_has_no_stale_override(self):
        recipe = _make_target_recipe("pr-open")
        assert "STALE" not in recipe

        worktree_recipe = (REPO_ROOT / "make" / "worktrees.mk").read_text(encoding="utf-8")
        worktree_helper = (REPO_ROOT / "scripts" / "worktree_lifecycle.sh").read_text(encoding="utf-8")
        assert "worktree-remove:" in worktree_recipe
        assert "worktree_lifecycle.sh remove" in worktree_recipe
        assert "worktree is locked" in worktree_helper
        assert "worktree remove" in worktree_helper
        assert "git branch -D" not in worktree_recipe

    def test_preflight_cannot_refresh_or_bypass_pr_open_currency(self):
        preflight = _make_target_recipe("pr-preflight")

        assert "git fetch origin develop --quiet" in preflight
        assert "git merge --no-edit origin/develop" not in preflight
        assert "STALE" not in preflight
        assert "pr-refresh" not in preflight
        assert "pr-fanout" not in preflight
        assert "pr-open" not in preflight
        assert "pr-arm-auto-merge" not in preflight

    @pytest.mark.skipif(
        sys.platform == "win32", reason="the fake make is a POSIX shebang script and PATH is colon-joined"
    )
    def test_pre_push_hook_runs_the_targeted_preflight(self, tmp_path: Path):
        config = yaml.safe_load((REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
        hooks = [
            hook for repo in config["repos"] for hook in repo.get("hooks", []) if "pre-push" in hook.get("stages", [])
        ]
        preflight_hooks = [hook for hook in hooks if hook["id"] == "pr-preflight"]
        assert len(preflight_hooks) == 1
        assert all(hook["id"] != "pr-preflight-fast-tests" for hook in hooks)
        entry = preflight_hooks[0]["entry"]

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        trace = tmp_path / "make.trace"
        fake_make = bin_dir / "make"
        fake_make.write_text('#!/bin/sh\nprintf \'%s\\n\' "$*" > "$HOOK_TRACE"\n', encoding="utf-8")
        fake_make.chmod(0o755)
        env = os.environ.copy()
        env.pop("BENCHBOX_PREPUSH", None)
        env.update({"HOOK_TRACE": str(trace), "PATH": f"{bin_dir}:{env['PATH']}"})
        result = subprocess.run(shlex.split(entry), cwd=REPO_ROOT, capture_output=True, text=True, env=env)
        assert result.returncode == 0, result.stderr
        assert trace.read_text(encoding="utf-8").split() == ["pr-preflight"]

    def test_issue_templates_exist(self):
        templates_dir = REPO_ROOT / ".github" / "ISSUE_TEMPLATE"
        assert templates_dir.exists(), "Issue templates directory must exist"

        required_templates = ["bug_report.yml", "feature_request.yml", "platform_support.yml", "config.yml"]

        for template in required_templates:
            template_path = templates_dir / template
            assert template_path.exists(), f"{template} template must exist"

    def test_pr_template_exists(self):
        pr_template_path = REPO_ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md"
        assert pr_template_path.exists(), "Pull request template must exist"

        with open(pr_template_path, encoding="utf-8") as f:
            content = f.read()

        required_sections = [
            "## Description",
            "## Type of Change",
            "## Testing",
            "## Public Contract Check",
            "## Artifact Hygiene",
            "## Notes",
        ]

        for section in required_sections:
            assert section in content, f"PR template must have {section} section"

    @pytest.mark.slow
    def test_package_build_succeeds(self):
        import subprocess
        import tempfile
        from pathlib import Path

        project_root = REPO_ROOT

        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                ["uv", "build", "--out-dir", tmpdir], cwd=project_root, capture_output=True, text=True
            )

            if result.returncode != 0:
                pytest.fail(f"Package build failed: {result.stderr}")

            built_files = list(Path(tmpdir).glob("*"))
            wheel_files = [f for f in built_files if f.suffix == ".whl"]
            sdist_files = [f for f in built_files if f.suffix == ".gz"]

            assert len(wheel_files) > 0, "Build should create wheel file"
            assert len(sdist_files) > 0, "Build should create source distribution"

    def test_cli_entry_point_works(self):
        import subprocess
        from pathlib import Path

        project_root = REPO_ROOT

        result = subprocess.run(["uv", "run", "benchbox", "--help"], cwd=project_root, capture_output=True, text=True)

        assert result.returncode == 0, "CLI entry point should work"
        assert "BenchBox" in result.stdout
        assert "database benchmark" in result.stdout.lower()

    def test_no_incorrect_repository_references(self):
        from pathlib import Path

        project_root = REPO_ROOT

        search_dirs = [
            "benchbox",
            "tests",
            "docs",
            ".github",
        ]

        root_files = [
            "README.md",
            "CHANGELOG.md",
            "pyproject.toml",
            "setup.py",
            "setup.cfg",
        ]

        problematic_files = []

        for search_dir in search_dirs:
            dir_path = project_root / search_dir
            if not dir_path.exists():
                continue

            for file_path in dir_path.rglob("*"):
                if not file_path.is_file():
                    continue

                if (
                    any(skip in str(file_path) for skip in ["__pycache__", ".egg-info", ".pytest_cache", ".mypy_cache"])
                    or file_path.suffix == ".pyc"
                ):
                    continue

                if file_path.name == "test_release_infrastructure.py":
                    continue

                try:
                    content = file_path.read_text(encoding="utf-8", errors="ignore")
                    if "anthropics/claude-code" in content:
                        problematic_files.append(str(file_path.relative_to(project_root)))
                except (UnicodeDecodeError, OSError):
                    pass

        for root_file in root_files:
            file_path = project_root / root_file
            if not file_path.exists():
                continue

            try:
                content = file_path.read_text(encoding="utf-8")
                if "anthropics/claude-code" in content:
                    problematic_files.append(root_file)
            except (UnicodeDecodeError, OSError):
                pass

        problematic_files = [
            f for f in problematic_files if not any(acceptable in f for acceptable in ["_project/PROJECT_TODO.md"])
        ]

        if problematic_files:
            pytest.fail(f"Found incorrect repository references in: {problematic_files}")


class TestVersionConsistency:
    def test_version_in_pyproject_toml(self):
        pyproject_path = REPO_ROOT / "pyproject.toml"

        with open(pyproject_path, "rb") as f:
            config = tomllib.load(f)

        version = config["project"]["version"]
        assert version is not None
        assert isinstance(version, str)
        assert len(version) > 0

        import re

        semver_pattern = r"^\d+\.\d+\.\d+(-\w+|(a|b|rc)\d+)?$"
        assert re.match(semver_pattern, version), f"Version {version} should follow semantic versioning"

    def test_version_in_init_file(self):
        init_path = REPO_ROOT / "benchbox" / "__init__.py"

        with open(init_path, encoding="utf-8") as f:
            content = f.read()

        assert "__version__" in content, "__init__.py should define __version__"

    def test_version_consistency(self):
        pyproject_path = REPO_ROOT / "pyproject.toml"
        with open(pyproject_path, "rb") as f:
            config = tomllib.load(f)
        pyproject_version = config["project"]["version"]

        init_path = REPO_ROOT / "benchbox" / "__init__.py"
        with open(init_path, encoding="utf-8") as f:
            content = f.read()

        import re

        version_match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
        assert version_match, "Could not find __version__ in __init__.py"
        init_version = version_match.group(1)

        assert pyproject_version == init_version, (
            f"Version mismatch: pyproject.toml has {pyproject_version}, __init__.py has {init_version}"
        )


class TestReleaseWorkflowValidation:
    def test_workflows_are_valid_yaml(self):
        workflows_dir = REPO_ROOT / ".github" / "workflows"

        for workflow_file in workflows_dir.glob("*.yml"):
            with open(workflow_file, encoding="utf-8") as f:
                try:
                    yaml.safe_load(f)
                except yaml.YAMLError as e:
                    pytest.fail(f"Invalid YAML in {workflow_file}: {e}")

    def test_issue_templates_are_valid_yaml(self):
        templates_dir = REPO_ROOT / ".github" / "ISSUE_TEMPLATE"

        for template_file in templates_dir.glob("*.yml"):
            with open(template_file, encoding="utf-8") as f:
                try:
                    yaml.safe_load(f)
                except yaml.YAMLError as e:
                    pytest.fail(f"Invalid YAML in {template_file}: {e}")


class TestPackageMetadata:
    def test_license_file_exists(self):
        license_path = REPO_ROOT / "LICENSE"
        assert license_path.exists(), "LICENSE file must exist"

    def test_readme_file_exists(self):
        readme_path = REPO_ROOT / "README.md"
        assert readme_path.exists(), "README.md file must exist"

    def test_pyproject_toml_build_config(self):
        pyproject_path = REPO_ROOT / "pyproject.toml"

        with open(pyproject_path, "rb") as f:
            config = tomllib.load(f)

        assert "build-system" in config
        build_system = config["build-system"]
        assert "requires" in build_system
        assert "build-backend" in build_system

        assert "project" in config
        project = config["project"]
        assert "scripts" in project
        assert "benchbox" in project["scripts"]


class TestBetaReleaseSurface:
    REPO_ROOT = Path(__file__).parent.parent.parent

    STATUS_DOCS = [
        "README.md",
        "docs/usage/faq.md",
    ]

    INSTALL_DOCS = [
        "README.md",
        "docs/usage/installation.md",
        "docs/platforms/platform-selection-guide.md",
        "docs/platforms/duckdb.md",
        "docs/usage/faq.md",
    ]

    DUCKDB_DEFAULT_PHRASES = [
        "DuckDB is included with BenchBox by default",
        "included by default with BenchBox",
        "DuckDB is automatically installed as a dependency",
        "works with DuckDB out of the box",
        "DuckDB is embedded and ready to go",
    ]

    def test_pyproject_has_beta_classifier(self):
        pyproject_path = self.REPO_ROOT / "pyproject.toml"
        with open(pyproject_path, "rb") as f:
            config = tomllib.load(f)
        classifiers = config["project"].get("classifiers", [])
        beta_classifiers = [c for c in classifiers if "4 - Beta" in c]
        assert beta_classifiers, (
            "pyproject.toml must have a '4 - Beta' Development Status classifier. "
            "Found classifiers: " + str(classifiers)
        )

    def test_canonical_docs_say_beta_not_alpha(self):
        alpha_pattern = re.compile(
            r"\b(alpha software|is ALPHA software|Status-Alpha|## Alpha Software)\b",
            re.IGNORECASE,
        )
        for rel_path in self.STATUS_DOCS:
            path = self.REPO_ROOT / rel_path
            if not path.exists():
                continue
            content = path.read_text(encoding="utf-8")
            match = alpha_pattern.search(content)
            assert match is None, (
                f"{rel_path} still contains Alpha status language: {match.group()!r}. "
                "Update it to Beta before releasing."
            )

    def test_install_docs_do_not_claim_duckdb_is_default(self):
        for rel_path in self.INSTALL_DOCS:
            path = self.REPO_ROOT / rel_path
            if not path.exists():
                continue
            content = path.read_text(encoding="utf-8")
            for phrase in self.DUCKDB_DEFAULT_PHRASES:
                assert phrase not in content, (
                    f"{rel_path} claims DuckDB is a default dependency ({phrase!r}). "
                    "DuckDB is an optional extra ([duckdb]). "
                    "Fix the docs or update pyproject.toml dependencies to match."
                )

    def test_duckdb_is_optional_in_pyproject(self):
        pyproject_path = self.REPO_ROOT / "pyproject.toml"
        with open(pyproject_path, "rb") as f:
            config = tomllib.load(f)
        core_deps = config["project"].get("dependencies", [])
        duckdb_in_core = [d for d in core_deps if d.startswith("duckdb")]
        assert not duckdb_in_core, (
            "DuckDB must stay in [project.optional-dependencies], not [project.dependencies]. "
            "If you intentionally restored it to core deps, also update all install docs "
            "to remove the [duckdb] extra requirement."
        )
        optional_deps = config["project"].get("optional-dependencies", {})
        assert "duckdb" in optional_deps, (
            "Expected a 'duckdb' entry in [project.optional-dependencies]. "
            "If the extra was renamed or removed, update this test and all install docs."
        )

    def test_duckdb_runtime_floor_supports_constrained_sorted_ingestion(self):
        pyproject_path = self.REPO_ROOT / "pyproject.toml"
        with open(pyproject_path, "rb") as f:
            config = tomllib.load(f)

        optional_deps = config["project"]["optional-dependencies"]
        dependency_groups = config["dependency-groups"]
        expected = "duckdb>=1.5.0,<2.0.0"
        assert optional_deps["duckdb"] == [expected]
        assert expected in optional_deps["dev"]
        assert expected in optional_deps["mcp"]
        assert expected in dependency_groups["dev"]

        from benchbox.core.platform_manifest import get_platform_manifest_entry

        assert get_platform_manifest_entry("duckdb").metadata["requirements"] == (expected,)

    def test_experimental_not_in_benchbox_all(self):
        import benchbox

        public_exports = getattr(benchbox, "__all__", [])
        experimental_exports = [name for name in public_exports if "experimental" in name.lower()]
        assert not experimental_exports, "benchbox.__all__ must not expose the experimental namespace: " + str(
            experimental_exports
        )

    def test_experimental_namespace_support_boundary_is_documented(self):
        exp_init = self.REPO_ROOT / "benchbox" / "experimental" / "__init__.py"
        assert exp_init.exists(), "benchbox/experimental/__init__.py must exist"
        contracts = (self.REPO_ROOT / "docs" / "reference" / "public-contracts.md").read_text(encoding="utf-8")
        row = next((line for line in contracts.splitlines() if "`benchbox.experimental` namespace" in line), "")
        assert "| `experimental` |" in row
        assert "outside the supported beta product surface" in row, (
            "docs/reference/public-contracts.md must document benchbox.experimental as outside "
            "the supported beta product surface. Do not silently expand the supported product surface."
        )

    @pytest.mark.slow
    def test_ty_clean_on_beta_critical_entrypoints(self):
        import subprocess

        beta_critical = [
            "benchbox/cli/commands/run.py",
            "benchbox/base.py",
            "benchbox/__init__.py",
        ]
        result = subprocess.run(
            ["uv", "run", "ty", "check", *beta_critical],
            cwd=self.REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            "ty check reported diagnostics on Beta-critical entrypoints.\n"
            "Fix the diagnostics before releasing Beta:\n" + result.stdout + result.stderr
        )


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _init_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", ".", cwd=root)
    for key, value in (
        ("user.email", "test@benchbox.invalid"),
        ("user.name", "BenchBox Test"),
        ("commit.gpgsign", "false"),
        ("tag.gpgsign", "false"),
        ("core.hooksPath", str(root / ".no-hooks")),
    ):
        _git("config", key, value, cwd=root)


def _commit_all(root: Path, message: str) -> None:
    _git("add", "-A", cwd=root)
    result = _git("commit", "-q", "--no-verify", "-m", message, cwd=root)
    assert result.returncode == 0, f"scratch commit failed: {result.stdout}{result.stderr}"


def _default_branch(root: Path) -> str:
    return _git("symbolic-ref", "--short", "HEAD", cwd=root).stdout.strip()


def _probe(root: Path, theirs: str) -> subprocess.CompletedProcess:
    return _git("merge-tree", "--write-tree", "--name-only", "HEAD", theirs, cwd=root)


def _git_version() -> tuple[int, ...]:
    out = subprocess.run(["git", "--version"], capture_output=True, text=True, check=True).stdout
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
    assert match is not None, f"unparseable git version: {out!r}"
    return tuple(int(part) for part in match.groups())


requires_modern_merge_tree = pytest.mark.skipif(
    _git_version() < (2, 38, 0),
    reason="pr-conflict-scan requires git >= 2.38 for `merge-tree --write-tree`",
)


class TestPrConflictScan:
    def test_probe_uses_modern_write_tree_form(self):
        recipe = _make_target_recipe("pr-conflict-scan")

        assert 'git merge-tree --write-tree --name-only HEAD "origin/$$branch"' in recipe
        assert 'git rev-parse --verify --quiet "origin/$$branch^{commit}"' in recipe
        assert recipe.index("rev-parse --verify") < recipe.index("merge-tree")
        assert "[ $$? -eq 1 ] || continue" in recipe

    def test_probe_does_not_parse_legacy_informational_headers(self):
        recipe = _make_target_recipe("pr-conflict-scan")

        for header in ("changed in both", "added in both", "removed in local", "removed in remote"):
            assert header not in recipe, f"{header!r} is an informational header, not a conflict"
        assert 'git merge-tree "$$base"' not in recipe

    def test_scan_is_warn_only(self):
        recipe = _make_target_recipe("pr-conflict-scan")
        assert recipe.rstrip().endswith("done; true")

    @requires_modern_merge_tree
    def test_probe_is_silent_when_sides_touch_disjoint_files(self, tmp_path: Path):
        repo = tmp_path / "disjoint"
        _init_repo(repo)
        (repo / "ours.txt").write_text("a\n")
        (repo / "theirs.txt").write_text("b\n")
        _commit_all(repo, "base")
        base = _default_branch(repo)

        _git("checkout", "-q", "-b", "feature", cwd=repo)
        (repo / "theirs.txt").write_text("theirs edit\n")
        _commit_all(repo, "feature")

        _git("checkout", "-q", base, cwd=repo)
        (repo / "ours.txt").write_text("ours edit\n")
        _commit_all(repo, "ours")

        assert _probe(repo, "feature").returncode == 0

    @requires_modern_merge_tree
    def test_probe_is_silent_when_both_sides_edit_one_file_cleanly(self, tmp_path: Path):
        repo = tmp_path / "same_file_clean"
        _init_repo(repo)
        (repo / "CHANGELOG.md").write_text("".join(f"line{i}\n" for i in range(1, 21)))
        _commit_all(repo, "base")
        base = _default_branch(repo)

        _git("checkout", "-q", "-b", "feature", cwd=repo)
        lines = (repo / "CHANGELOG.md").read_text().splitlines(keepends=True)
        lines[-1] = "feature entry\n"
        (repo / "CHANGELOG.md").write_text("".join(lines))
        _commit_all(repo, "feature")

        _git("checkout", "-q", base, cwd=repo)
        lines = (repo / "CHANGELOG.md").read_text().splitlines(keepends=True)
        lines[0] = "ours entry\n"
        (repo / "CHANGELOG.md").write_text("".join(lines))
        _commit_all(repo, "ours")

        assert _probe(repo, "feature").returncode == 0, "clean auto-merge must not warn"

        merge_base = _git("merge-base", "HEAD", "feature", cwd=repo).stdout.strip()
        legacy = _git("merge-tree", merge_base, "HEAD", "feature", cwd=repo).stdout
        assert "changed in both" in legacy, "the legacy false-positive trigger is still reproducible"

    @requires_modern_merge_tree
    def test_probe_warns_on_a_true_conflict(self, tmp_path: Path):
        repo = tmp_path / "conflict"
        _init_repo(repo)
        (repo / "Makefile").write_text("target:\n\techo base\n")
        _commit_all(repo, "base")
        base = _default_branch(repo)

        _git("checkout", "-q", "-b", "feature", cwd=repo)
        (repo / "Makefile").write_text("target:\n\techo feature\n")
        _commit_all(repo, "feature")

        _git("checkout", "-q", base, cwd=repo)
        (repo / "Makefile").write_text("target:\n\techo ours\n")
        _commit_all(repo, "ours")

        result = _probe(repo, "feature")
        assert result.returncode == 1, "a same-line conflict must be detected"
        conflicted = list(itertools.takewhile(bool, result.stdout.splitlines()[1:]))
        assert conflicted == ["Makefile"]

    @requires_modern_merge_tree
    def test_legacy_conflict_marker_is_diff_prefixed(self, tmp_path: Path):
        repo = tmp_path / "legacy_marker"
        _init_repo(repo)
        (repo / "f.txt").write_text("base\nkeep\n")
        _commit_all(repo, "base")
        base = _default_branch(repo)

        _git("checkout", "-q", "-b", "feature", cwd=repo)
        (repo / "f.txt").write_text("feature\nkeep\n")
        _commit_all(repo, "feature")

        _git("checkout", "-q", base, cwd=repo)
        (repo / "f.txt").write_text("ours\nkeep\n")
        _commit_all(repo, "ours")

        merge_base = _git("merge-base", "HEAD", "feature", cwd=repo).stdout.strip()
        legacy = _git("merge-tree", merge_base, "HEAD", "feature", cwd=repo).stdout

        assert "<<<<<<<" in legacy
        assert not any(line.startswith("<<<<<<<") for line in legacy.splitlines())


class TestUATGateReleaseEvidence:
    @staticmethod
    def _payload(**overrides) -> dict:
        payload = {
            "version": 1,
            "verdict": "green",
            "source_commit_sha": "uat-tested-sha",
            "source_dirty": False,
            "completed_at": "2026-07-01T10:00:00+00:00",
            "generated_at": "2026-07-01T18:00:00+00:00",
            "stage_names": [
                "release-gate-01-native-dataframe",
                "release-gate-02-docker-nonoltp",
                "release-gate-03-docker-oltp",
            ],
            "stage_artifact_digests": {"release-gate-01-native-dataframe": "abc123"},
        }
        payload.update(overrides)
        return payload

    @staticmethod
    def _evaluate(payload, *, now=None, is_ancestor=lambda _a, _h: True, max_age_days=21.0):
        from datetime import datetime, timezone

        from scripts.release_readiness_check import evaluate_uat_gate_evidence

        return evaluate_uat_gate_evidence(
            payload,
            now=now or datetime(2026, 7, 10, 12, tzinfo=timezone.utc),
            max_age_days=max_age_days,
            head_sha="release-head",
            is_ancestor=is_ancestor,
        )

    def test_missing_evidence_marks_campaign_report_incomplete(self):
        result = self._evaluate(None)
        assert not result.ok
        assert "No committed UAT gate evidence" in result.message
        assert "- uat_gate: missing" in result.summary

    def test_red_verdict_marks_campaign_report_incomplete(self):
        result = self._evaluate(self._payload(verdict="red"))
        assert not result.ok
        assert "verdict is 'red'" in result.message

    def test_dry_run_verdict_marks_campaign_report_incomplete(self):
        result = self._evaluate(self._payload(verdict="dry_run"))
        assert not result.ok
        assert "verdict is 'dry_run'" in result.message

    def test_dirty_source_tree_marks_advisory_evidence_quality_failed(self):
        result = self._evaluate(self._payload(source_dirty=True))
        assert not result.ok
        assert "dirty source tree" in result.message

    def test_stale_evidence_marks_campaign_report_incomplete(self):
        from datetime import datetime, timezone

        result = self._evaluate(
            self._payload(completed_at="2026-06-01T10:00:00+00:00"),
            now=datetime(2026, 7, 10, 12, tzinfo=timezone.utc),
        )
        assert not result.ok
        assert "stale" in result.message

    def test_non_ancestor_evidence_marks_campaign_report_incomplete(self):
        result = self._evaluate(self._payload(), is_ancestor=lambda _a, _h: False)
        assert not result.ok
        assert "not an ancestor" in result.message

    def test_green_fresh_ancestor_evidence_passes(self):
        checked: list[tuple[str, str]] = []

        def _is_ancestor(ancestor: str, head: str) -> bool:
            checked.append((ancestor, head))
            return True

        result = self._evaluate(self._payload(), is_ancestor=_is_ancestor)
        assert result.ok
        assert "green, fresh, and applicable" in result.message
        assert checked == [("uat-tested-sha", "release-head")]

    def test_missing_completed_at_marks_campaign_report_incomplete(self):
        result = self._evaluate(self._payload(completed_at=""))
        assert not result.ok
        assert "no completed_at" in result.message

    @pytest.mark.skipif(not hasattr(__import__("time"), "tzset"), reason="time.tzset() is POSIX-only")
    def test_naive_completed_at_treated_as_utc_regardless_of_local_timezone(self, monkeypatch):
        import os
        import time
        from datetime import datetime, timezone

        payload = self._payload(completed_at="2026-07-09T10:00:00")
        now = datetime(2026, 7, 10, 12, tzinfo=timezone.utc)

        def _age_line(result):
            return next(line for line in result.summary if line.startswith("- uat_age:"))

        original_tz = os.environ.get("TZ")
        try:
            results = {}
            for tz in ("UTC", "Etc/GMT+12", "Etc/GMT-14"):
                monkeypatch.setenv("TZ", tz)
                time.tzset()
                results[tz] = self._evaluate(payload, now=now)
        finally:
            if original_tz is not None:
                os.environ["TZ"] = original_tz
            else:
                os.environ.pop("TZ", None)
            time.tzset()

        oks = {tz: r.ok for tz, r in results.items()}
        ages = {tz: _age_line(r) for tz, r in results.items()}
        assert len(set(oks.values())) == 1, oks
        assert len(set(ages.values())) == 1, ages

    def test_missing_uat_is_advisory_after_green_canary(self, monkeypatch, capsys):
        from datetime import datetime, timezone

        from scripts import release_readiness_check

        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        def _fake_api_json(url: str, _token: str) -> dict:
            return {
                "workflow_runs": [
                    {
                        "status": "completed",
                        "conclusion": "success",
                        "updated_at": now_iso,
                        "head_sha": "canary-sha",
                        "html_url": "https://example.test/run",
                        "display_title": "Release Canary",
                    }
                ]
            }

        monkeypatch.setattr(release_readiness_check, "_api_json", _fake_api_json)
        monkeypatch.setattr(release_readiness_check, "_is_ancestor_with_git", lambda _a, _h: True)
        monkeypatch.setattr(release_readiness_check, "_load_uat_gate_evidence", lambda _p, _r: None)
        monkeypatch.setenv("GITHUB_TOKEN", "token")
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
        monkeypatch.delenv("RELEASE_READINESS_OVERRIDE_SHA", raising=False)
        monkeypatch.delenv("RELEASE_READINESS_OVERRIDE_REASON", raising=False)

        rc = release_readiness_check.main(["--head-sha", "release-head", "--no-ancestor-check"])

        assert rc == 0
        assert "UAT evidence advisory: No committed UAT gate evidence" in capsys.readouterr().out

    def test_red_uat_is_advisory_after_green_canary(self, monkeypatch, capsys):
        from datetime import datetime, timedelta, timezone

        from scripts import release_readiness_check

        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        def _fake_api_json(url: str, _token: str) -> dict:
            return {
                "workflow_runs": [
                    {
                        "status": "completed",
                        "conclusion": "success",
                        "updated_at": now_iso,
                        "head_sha": "canary-sha",
                        "html_url": "https://example.test/run",
                        "display_title": "Release Canary",
                    }
                ]
            }

        payload = self._payload(
            verdict="red",
            completed_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
        )
        monkeypatch.setattr(release_readiness_check, "_api_json", _fake_api_json)
        monkeypatch.setattr(release_readiness_check, "_is_ancestor_with_git", lambda _a, _h: True)
        monkeypatch.setattr(release_readiness_check, "_load_uat_gate_evidence", lambda _p, _r: payload)
        monkeypatch.setenv("GITHUB_TOKEN", "token")
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
        monkeypatch.delenv("RELEASE_READINESS_OVERRIDE_SHA", raising=False)
        monkeypatch.delenv("RELEASE_READINESS_OVERRIDE_REASON", raising=False)

        rc = release_readiness_check.main(["--head-sha", "release-head", "--no-ancestor-check"])

        captured = capsys.readouterr()
        assert rc == 0
        assert "UAT evidence advisory:" in captured.out
        assert "verdict is 'red'" in captured.out

    def test_green_canary_plus_green_uat_prints_campaign_report(self, monkeypatch, capsys):
        from datetime import datetime, timedelta, timezone

        from scripts import release_readiness_check

        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        def _fake_api_json(url: str, _token: str) -> dict:
            return {
                "workflow_runs": [
                    {
                        "status": "completed",
                        "conclusion": "success",
                        "updated_at": now_iso,
                        "head_sha": "canary-sha",
                        "html_url": "https://example.test/run",
                        "display_title": "Release Canary",
                    }
                ]
            }

        payload = self._payload(completed_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
        monkeypatch.setattr(release_readiness_check, "_api_json", _fake_api_json)
        monkeypatch.setattr(release_readiness_check, "_is_ancestor_with_git", lambda _a, _h: True)
        monkeypatch.setattr(release_readiness_check, "_load_uat_gate_evidence", lambda _p, _r: payload)
        monkeypatch.setenv("GITHUB_TOKEN", "token")
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
        monkeypatch.delenv("RELEASE_READINESS_OVERRIDE_SHA", raising=False)
        monkeypatch.delenv("RELEASE_READINESS_OVERRIDE_REASON", raising=False)

        rc = release_readiness_check.main(["--head-sha", "release-head", "--no-ancestor-check"])

        assert rc == 0
        assert "UAT campaign report: UAT gate evidence is green" in capsys.readouterr().out

    def test_red_canary_blocks_even_with_green_uat(self, monkeypatch, capsys):
        from datetime import datetime, timedelta, timezone

        from scripts import release_readiness_check

        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        payload = self._payload(completed_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
        monkeypatch.setattr(
            release_readiness_check,
            "_api_json",
            lambda _url, _token: {
                "workflow_runs": [
                    {
                        "status": "completed",
                        "conclusion": "failure",
                        "updated_at": now_iso,
                        "head_sha": "canary-sha",
                        "html_url": "https://example.test/run",
                        "display_title": "Release Canary",
                    }
                ]
            },
        )
        monkeypatch.setattr(release_readiness_check, "_is_ancestor_with_git", lambda _a, _h: True)
        monkeypatch.setattr(release_readiness_check, "_load_uat_gate_evidence", lambda _p, _r: payload)
        monkeypatch.setenv("GITHUB_TOKEN", "token")
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
        monkeypatch.delenv("RELEASE_READINESS_OVERRIDE_SHA", raising=False)
        monkeypatch.delenv("RELEASE_READINESS_OVERRIDE_REASON", raising=False)

        rc = release_readiness_check.main(["--head-sha", "release-head", "--no-ancestor-check"])

        assert rc == 1
        assert "Latest release canary is failure" in capsys.readouterr().err

    def test_override_bypasses_uat_check_too(self, monkeypatch):
        from scripts import release_readiness_check

        monkeypatch.setenv("RELEASE_READINESS_OVERRIDE_SHA", "release-head")
        monkeypatch.setenv("RELEASE_READINESS_OVERRIDE_REASON", "INC-999 approved")
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

        def _boom(*_args, **_kwargs):
            raise AssertionError("override must short-circuit before any evidence lookup")

        monkeypatch.setattr(release_readiness_check, "_load_uat_gate_evidence", _boom)
        monkeypatch.setattr(release_readiness_check, "_api_json", _boom)

        rc = release_readiness_check.main(["--head-sha", "release-head"])

        assert rc == 0

    def test_gate_check_make_target_is_wired(self):
        recipe = _make_target_recipe("uat-gate-check")
        assert "tests.uat._cli gate-check" in recipe
        assert "--stage1" in recipe and "--stage2" in recipe and "--stage3" in recipe

    def test_release_evidence_dir_convention_documented(self):
        readme = (REPO_ROOT / "_project" / "release-evidence" / "README.md").read_text(encoding="utf-8")
        assert "uat-gate-summary.json" in readme
        assert "release_readiness_check.py" in readme
