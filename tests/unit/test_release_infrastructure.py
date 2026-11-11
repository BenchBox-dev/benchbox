# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import itertools
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import scripts.check_make_targets as make_targets

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]

REPO_ROOT = Path(__file__).parent.parent.parent
CI_FAST_EXPRESSION = "fast and not (slow or stress or resource_heavy or live_integration)"
RELEASE_CANARY_NON_FAST_EXPRESSION = "(slow or resource_heavy) and not (stress or live_integration)"


def _makefile_text() -> str:
    return make_targets.makefile_sources(REPO_ROOT)


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
        assert urls["Changelog"] == f"{expected_repo}/blob/develop/CHANGELOG.md"

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

        required_workflows = ["ci.yml", "release-v2.yml", "release-canary.yml"]
        for workflow in required_workflows:
            workflow_path = workflows_dir / workflow
            assert workflow_path.exists(), f"{workflow} workflow must exist"

    def test_required_fast_marker_expression_is_consistent(self):
        makefile_content = make_targets.makefile_sources(REPO_ROOT)
        develop_pr_run_text = _workflow_job_run_text("ci.yml", "code-test")

        expected_marker_flag = f'-m "{CI_FAST_EXPRESSION}"'
        assert expected_marker_flag in makefile_content
        assert "-m fast -q" not in makefile_content
        assert expected_marker_flag in develop_pr_run_text
        assert "--cov-fail-under=70" in develop_pr_run_text
        assert "coverage remains CI-only" in makefile_content

    def test_fast_suite_runs_on_python_314_per_pr_and_on_python_312_after_merge(self):
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

        assert _workflow("trunk.yml")[True]["push"] == {"branches": ["develop", "release/**"]}
        assert python_version("trunk.yml", "fast") == "3.12"
        assert expected_marker_flag in _workflow_job_run_text("trunk.yml", "fast")

    def test_release_canary_workflow_contract(self):
        workflow = _workflow("release-canary.yml")
        on_events = workflow[True]
        assert "workflow_dispatch" in on_events
        assert on_events["schedule"] == [{"cron": "0 8 * * *"}]
        assert workflow["permissions"]["actions"] == "read"
        assert workflow["permissions"]["contents"] == "read"
        assert workflow["env"]["RELEASE_CANARY_REF"] == (
            "${{ startsWith(github.ref, 'refs/heads/release/') && github.ref_name || 'develop' }}"
        )

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
        assert '--checked-ref "${RELEASE_CANARY_REF}"' in collection_text
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
        assert '"checked_ref": "${RELEASE_CANARY_REF}"' in result_text
        assert '"commit_sha": "${CHECKED_SHA}"' in result_text
        assert '"collection_result": "${COLLECTION_RESULT}"' in result_text
        assert '"ruleset_drift_sha": "${RULESET_DRIFT_SHA}"' in result_text
        assert '"freshness_contract_hours": 48' in result_text
        assert "Release canary passed." in result_text

        assert "issues" not in workflow["permissions"]
        incident_job = jobs["release-canary-incident"]
        assert incident_job["if"] == "${{ always() && !cancelled() && !startsWith(github.ref, 'refs/heads/release/') }}"
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
        push_at = recipe.find('git push origin "$$REVIEWED:refs/heads/')
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
    ]

    INSTALL_DOCS = [
        "README.md",
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
    def test_gate_check_make_target_is_wired(self):
        recipe = _make_target_recipe("uat-gate-check")
        assert "tests.uat._cli gate-check" in recipe
        assert "--stage1" in recipe and "--stage2" in recipe and "--stage3" in recipe

    def test_release_evidence_dir_convention_documented(self):
        readme = (REPO_ROOT / "_project" / "release-evidence" / "README.md").read_text(encoding="utf-8")
        assert "uat-gate-summary.json" in readme


