from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from configparser import ConfigParser
from pathlib import Path

import pytest
import yaml

from tests.integration.test_local_platform_benchmark_matrix import (
    LOCAL_SQL_STABLE_MATRIX,
    _validate_against_expected_results,
)
from tests.utilities.paths import REPO_ROOT

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]

CI_FAST_EXPRESSION = "fast and not (slow or stress or resource_heavy or live_integration)"
CORRECTNESS_GATE_NODEID = (
    "tests/integration/test_local_platform_benchmark_matrix.py::test_local_platform_benchmark_matrix[tpch-duckdb]"
)
CORRECTNESS_GATE_QUERY_IDS = "1,2,3,4,5,6,7,8,9,10,12,13,14,15,17,19,21,22"


def _makefile_target_body(makefile_content: str, target_name: str) -> str:
    lines = makefile_content.splitlines(keepends=True)
    start = next(
        (index for index, line in enumerate(lines) if line.split(":", 1)[0] == target_name),
        None,
    )
    assert start is not None, f"Makefile target not found: {target_name}"
    body: list[str] = []
    for line in lines[start + 1 :]:
        if line.strip() and not line.startswith(("\t", " ")):
            break
        body.append(line)
    return "".join(body)


_SPLIT_CROSS_SURFACE_TARGETS = {"tpcds": ("tpcds-pandas-cross-surface-equivalence-report",)}


def _cross_surface_make_target(gate_name: str) -> str:
    return f"{gate_name.replace('_', '-')}-cross-surface-equivalence-report"


def _workflow_job_run_text(workflow_path: Path, job_name: str) -> str:
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    steps = workflow["jobs"][job_name]["steps"]
    return "\n".join(str(step.get("run", "")) for step in steps)


def _load_ini_section(path: Path, section: str) -> dict[str, str]:
    parser = ConfigParser()
    parser.read(path)
    return dict(parser[section])


def _marker_names(path: Path) -> set[str]:
    marker_lines = _load_ini_section(path, "pytest")["markers"].splitlines()
    return {
        line.split(":", 1)[0].strip()
        for line in marker_lines
        if line.strip() and ":" in line and not line.strip().startswith("#")
    }


class TestStandardizedTestCommands:
    def test_makefile_commands_exist(self):
        makefile_path = Path(__file__).resolve().parent.parent.parent / "Makefile"
        assert makefile_path.exists(), "Makefile should exist"

        makefile_content = makefile_path.read_text()
        assert "test-fast:" in makefile_content
        assert "test-unit:" in makefile_content
        assert "test-integration:" in makefile_content
        assert "test-all:" in makefile_content
        assert "python -m pytest" in makefile_content
        assert "run_tests.py" not in makefile_content

    def test_no_legacy_run_tests_references(self):
        run_tests_path = Path(__file__).resolve().parent.parent.parent / "run_tests.py"
        assert not run_tests_path.exists(), "run_tests.py should not exist in project root"

    def test_pytest_configuration_is_valid(self):
        pytest_ini_path = Path(__file__).resolve().parent.parent.parent / "pytest.ini"
        assert pytest_ini_path.exists(), "pytest.ini should exist"

        pytest_ini_content = pytest_ini_path.read_text()
        assert "fast:" in pytest_ini_content
        assert "unit:" in pytest_ini_content
        assert "integration:" in pytest_ini_content
        assert "markers =" in pytest_ini_content
        assert "not slow and not stress and not live_integration and not resource_heavy" in pytest_ini_content

    def test_coverage_commands_use_pytest(self):
        makefile_path = Path(__file__).resolve().parent.parent.parent / "Makefile"
        makefile_content = makefile_path.read_text()

        coverage_section = False
        for line in makefile_content.split("\n"):
            if line.startswith("coverage:"):
                coverage_section = True
                continue
            if coverage_section and line.startswith("\t"):
                assert "python -m pytest" in line
                assert "--cov=" in line
                break


class TestMakefileCommands:
    def test_makefile_test_targets_defined(self):
        makefile_path = Path(__file__).resolve().parent.parent.parent / "Makefile"
        makefile_content = makefile_path.read_text()

        expected_targets = [
            "test:",
            "test-all:",
            "test-unit:",
            "test-integration:",
            "test-fast:",
            "test-medium:",
            "test-slow:",
            "coverage:",
            "coverage-html:",
        ]

        for target in expected_targets:
            assert target in makefile_content, f"Makefile should contain target: {target}"

    def test_makefile_test_unlock_expands_user_lock_dir_without_uv(self, tmp_path):
        home = tmp_path / "home"
        lock_dir = home / "tmp" / "benchbox-lock-probe"
        lock_dir.mkdir(parents=True)
        lock_path = lock_dir / "test.lock"
        lock_path.write_text("stale lock\n", encoding="utf-8")
        env = {
            **subprocess.os.environ,
            "BENCHBOX_TEST_LOCK_DIR": "~/tmp/benchbox-lock-probe",
            "HOME": str(home),
            "PATH": "/usr/bin:/bin",
        }

        result = subprocess.run(
            ["make", "--no-print-directory", "test-unlock"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )

        expected_path = str(lock_path)
        assert result.returncode == 0, result.stderr
        assert expected_path in result.stdout
        assert "~/tmp/benchbox-lock-probe/test.lock" not in result.stdout
        assert "uv:" not in result.stdout + result.stderr
        assert lock_path.exists()
        assert lock_path.read_text(encoding="utf-8") == ""

    def test_makefile_test_all_splits_parallel_and_serial_lanes_explicitly(self):
        makefile_content = (Path(__file__).resolve().parent.parent.parent / "Makefile").read_text()

        assert "test-all:" in makefile_content
        assert '-m "not (slow or stress or resource_heavy or live_integration)"' in makefile_content
        assert '-m "(slow or resource_heavy) and not (stress or live_integration)" -n 0' in makefile_content

    def test_makefile_test_fast_excludes_heavy_and_opt_in_lanes(self):
        makefile_content = (Path(__file__).resolve().parent.parent.parent / "Makefile").read_text()

        assert "test-fast:" in makefile_content
        assert '-m "fast and not (slow or stress or resource_heavy or live_integration)" --tb=short' in makefile_content

    def test_default_test_lanes_arm_explicit_timeouts(self):
        makefile_content = (Path(__file__).resolve().parent.parent.parent / "Makefile").read_text()
        expected = {
            "test-fast": "--timeout=120",
            "test-medium": "--timeout=60",
            "test-slow": "--timeout=1200",
            "test-stress": "--timeout=1800",
            "test-ci": "--timeout=300",
            "ci-test": "--timeout=120",
            "pr-preflight-fast-tests": "--timeout=120",
        }
        for target, timeout in expected.items():
            assert timeout in _makefile_target_body(makefile_content, target)

        test_all = _makefile_target_body(makefile_content, "test-all")
        assert "--timeout=300" in test_all
        assert "--timeout=1200" in test_all

    def test_pytest_ini_declares_baseline_timeout(self):
        repo_root = Path(__file__).resolve().parent.parent.parent
        pytest_ini = (repo_root / "pytest.ini").read_text(encoding="utf-8")
        pytest_ci_ini = (repo_root / "pytest-ci.ini").read_text(encoding="utf-8")

        assert "timeout = 300" in pytest_ini
        assert "timeout = 300" in pytest_ci_ini

    def test_pytest_timeout_kills_hanging_test(self, tmp_path: Path):
        ini_file = tmp_path / "pytest.ini"
        ini_file.write_text("[pytest]\ntimeout = 1\n", encoding="utf-8")

        test_file = tmp_path / "test_hang.py"
        test_file.write_text(
            "import time\ndef test_hang():\n    time.sleep(5)\n",
            encoding="utf-8",
        )
        env = {**os.environ, "BENCHBOX_SKIP_TEST_LOCK": "1"}
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_file), "-q", "--tb=short", "-p", "no:cov"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
        assert result.returncode != 0
        assert (
            "Timeout (>1.0s) from pytest-timeout" in result.stdout
            or "Timeout" in result.stdout
            or "Timeout" in result.stderr
        )

    def test_skill_integrity_preflight_missing_trusted_tool_fails_instead_of_skipping(self, tmp_path: Path):
        tool_bin = tmp_path / "bin"
        tool_bin.mkdir()
        for command in ("uv", "mktemp", "rm", "mkdir", "env"):
            executable = shutil.which(command)
            assert executable is not None
            (tool_bin / command).symlink_to(executable)
        make = shutil.which("make")
        assert make is not None

        result = subprocess.run(
            [make, "--no-print-directory", "skill-integrity-check"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "PATH": str(tool_bin)},
        )

        assert result.returncode != 0
        assert "git" in result.stderr.lower()
        assert "skipping" not in (result.stdout + result.stderr).lower()

    def test_pr_preflight_fast_tests_creates_classifier_artifacts_when_absent(self, tmp_path: Path):
        tool_bin = tmp_path / "bin"
        tool_bin.mkdir()
        for command in ("mktemp", "rm", "mkdir"):
            executable = shutil.which(command)
            assert executable is not None
            (tool_bin / command).symlink_to(executable)
        (tool_bin / "git").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (tool_bin / "git").chmod(0o755)
        (tool_bin / "uv").write_text(
            "#!/bin/sh\n"
            "json_out=\n"
            "lists_dir=\n"
            "check=\n"
            "while [ $# -gt 0 ]; do\n"
            '  case "$1" in\n'
            "    --json-out) json_out=$2; shift 2 ;;\n"
            "    --lists-dir) lists_dir=$2; shift 2 ;;\n"
            "    --check) check=$2; shift 2 ;;\n"
            "    *) shift ;;\n"
            "  esac\n"
            "done\n"
            'if [ -n "$json_out" ]; then\n'
            '  printf "%s\n" "{"needs_code_ci": false}" > "$json_out"\n'
            '  mkdir -p "$lists_dir"\n'
            '  : > "$lists_dir/yaml.txt"\n'
            '  : > "$lists_dir/markdown.txt"\n'
            '  : > "$lists_dir/docs.txt"\n'
            "  exit 0\n"
            "fi\n"
            'if [ "$check" = "needs-code-ci" ]; then exit 1; fi\n'
            "exit 0\n",
            encoding="utf-8",
        )
        (tool_bin / "uv").chmod(0o755)
        trace = tmp_path / "trace.txt"
        fake_make = tmp_path / "fake-make"
        fake_make.write_text('#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$PREFLIGHT_TRACE"\n', encoding="utf-8")
        fake_make.chmod(0o755)
        make = shutil.which("make")
        assert make is not None

        result = subprocess.run(
            [make, "--no-print-directory", "pr-preflight-fast-tests", f"MAKE={fake_make}"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "PATH": str(tool_bin), "PREFLIGHT_TRACE": str(trace)},
        )
        calls = trace.read_text(encoding="utf-8").splitlines() if trace.exists() else []

        assert result.returncode == 0, result.stderr
        assert "PATH_DECISION is required" not in result.stderr
        assert any("pr-content-guard" in call for call in calls)
        assert "No code changes detected; skipping fast tests." in result.stdout

    def test_pr_preflight_fast_tests_consumes_one_classifier_artifact(self):
        makefile_content = (Path(__file__).resolve().parent.parent.parent / "Makefile").read_text(encoding="utf-8")
        fast_tests_body = _makefile_target_body(makefile_content, "pr-preflight-fast-tests")

        assert 'if [ -n "$(PATH_DECISION)" ] || [ -n "$(PATH_LISTS)" ]; then' in fast_tests_body
        assert "scripts/path_filter_decision.py --base-ref origin/develop" in fast_tests_body

    def test_pr_preflight_runs_only_changed_file_lint_and_serial_tests(self):
        makefile_content = (Path(__file__).resolve().parent.parent.parent / "Makefile").read_text(encoding="utf-8")
        preflight_body = _makefile_target_body(makefile_content, "pr-preflight")

        assert "_project/scripts/preflight_targets.py python" in preflight_body
        assert "_project/scripts/preflight_targets.py tests" in preflight_body
        assert "ruff check --force-exclude" in preflight_body
        assert "ruff format --check --force-exclude" in preflight_body
        assert "-q -n 0" in preflight_body
        assert "--ff" in preflight_body
        assert "-u \\1" in preflight_body
        assert "--lf" not in preflight_body
        assert "CI runs the fast lane" in preflight_body
        assert "local_validation" not in preflight_body
        assert "pr-preflight-fast-tests" not in preflight_body
        assert "ci-lint" not in preflight_body

    def test_pr_preflight_fast_tests_uses_required_ci_marker_expression(self):
        repo_root = Path(__file__).resolve().parent.parent.parent
        makefile_content = (repo_root / "Makefile").read_text()
        preflight_body = _makefile_target_body(makefile_content, "pr-preflight-fast-tests")
        develop_pr_run_text = _workflow_job_run_text(repo_root / ".github" / "workflows" / "ci.yml", "code-test")
        main_pr_run_text = _workflow_job_run_text(repo_root / ".github" / "workflows" / "test.yml", "test")

        assert f'-m "{CI_FAST_EXPRESSION}"' in preflight_body
        assert "-m fast -q" not in preflight_body
        assert f'-m "{CI_FAST_EXPRESSION}"' in develop_pr_run_text
        assert f'-m "{CI_FAST_EXPRESSION}"' in main_pr_run_text
        assert "--timeout=120" in develop_pr_run_text
        assert "--timeout=120" in main_pr_run_text
        assert "--cov-fail-under=70" in develop_pr_run_text
        assert "--cov-fail-under=70" in main_pr_run_text
        assert "coverage remains CI-only" in makefile_content

    def test_test_ci_is_maintained_broad_local_profile(self):
        repo_root = Path(__file__).resolve().parent.parent.parent
        makefile_content = (repo_root / "Makefile").read_text()
        pytest_ci_config = ConfigParser()
        pytest_ci_config.read(repo_root / "pytest-ci.ini")
        pytest_ci_addopts = _load_ini_section(repo_root / "pytest-ci.ini", "pytest")["addopts"]
        coverage_run = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["coverage"][
            "run"
        ]

        test_ci_body = _makefile_target_body(makefile_content, "test-ci")
        assert "-c pytest-ci.ini" in test_ci_body
        assert '-m "not (slow or stress or resource_heavy or live_integration)"' in test_ci_body
        assert "--cov=benchbox" in test_ci_body
        assert "--cov-fail-under=0" in test_ci_body
        assert pytest_ci_config.sections() == ["pytest"]
        assert "--cov-config=pyproject.toml" in pytest_ci_addopts
        assert coverage_run["source"] == ["benchbox"]
        assert coverage_run["branch"] is True
        assert "benchbox/core/tpcdi/etl/scd_processor.py" in coverage_run["omit"]
        assert _marker_names(repo_root / "pytest.ini") == _marker_names(repo_root / "pytest-ci.ini")
        for target in ("coverage-fast", "coverage-all", "coverage-opt-in-all", "coverage-html", "coverage-report"):
            assert "--cov-fail-under=0" in _makefile_target_body(makefile_content, target)

        coverage_filter = '-m "not (stress or resource_heavy or live_integration)"'
        for target in ("coverage-all", "coverage-html", "coverage-report"):
            assert coverage_filter in _makefile_target_body(makefile_content, target)
        assert '-m "not (stress or resource_heavy or live_integration)"' not in _makefile_target_body(
            makefile_content, "coverage-opt-in-all"
        )

    def test_coverage_threshold_policy_distinguishes_blocking_and_advisory_thresholds(self):
        repo_root = Path(__file__).resolve().parent.parent.parent
        makefile_content = (repo_root / "Makefile").read_text()
        pr_workflow = (repo_root / ".github" / "workflows" / "ci.yml").read_text()
        test_workflow = (repo_root / ".github" / "workflows" / "test.yml").read_text()
        conftest_content = (repo_root / "tests" / "conftest.py").read_text()
        policy_content = (repo_root / "docs" / "internal" / "quality-gate-policy.md").read_text()

        assert "--cov-fail-under=70" in makefile_content
        assert "--cov-fail-under=70" in pr_workflow
        assert "--cov-fail-under=70" in test_workflow
        assert "70 is the blocking CI floor" in policy_content
        assert "threshold = 80.0" in conftest_content
        assert "intentionally advisory" in policy_content
        assert "pytest.fail" not in conftest_content.split("def pytest_terminal_summary", maxsplit=1)[1]

    def test_makefile_test_medium_uses_five_workers(self):
        makefile_content = (Path(__file__).resolve().parent.parent.parent / "Makefile").read_text()

        assert "test-medium:" in makefile_content
        assert (
            '-m "medium and not (slow or stress or resource_heavy or live_integration)" --tb=short --timeout=60 -n 5'
            in makefile_content
        )

    def test_medium_marker_policy_is_documented_with_pr_routing(self):
        repo_root = Path(__file__).resolve().parent.parent.parent
        readme_content = (repo_root / "tests" / "README.md").read_text()
        makefile_content = (repo_root / "Makefile").read_text()

        assert "The `medium-test` job in `.github/workflows/ci.yml` runs `make test-medium`" in readme_content
        assert "only when the heavy tier is needed" in readme_content
        assert "Ordinary\ncode-change PRs skip it" in readme_content
        assert "Product-critical tests that need a different" in readme_content
        assert "test-medium:" in makefile_content

    def test_every_speed_marker_tier_has_a_ci_consumer(self):
        from tests.unit.test_marker_strategy import _E2E_QUICK_INCOMPATIBLE, _SPEED_MARKERS

        known_markers = sorted(_SPEED_MARKERS | _E2E_QUICK_INCOMPATIBLE)
        repo_root = Path(__file__).resolve().parent.parent.parent
        workflow_paths = [
            repo_root / ".github" / "workflows" / "ci.yml",
            repo_root / ".github" / "workflows" / "test.yml",
            repo_root / ".github" / "workflows" / "nightly.yml",
        ]
        combined_text = "\n".join(path.read_text(encoding="utf-8") for path in workflow_paths)

        unreferenced = [marker for marker in known_markers if not re.search(rf"\b{re.escape(marker)}\b", combined_text)]
        assert not unreferenced, (
            f"Speed marker(s) {unreferenced} have zero references across "
            f"{[p.name for p in workflow_paths]}; give the tier a CI home "
            "(path-filtered PR job, develop-post-merge job, or nightly job) "
            "per medium-tier-ci-home.yaml."
        )

    def test_makefile_correctness_gate_runs_expected_results_backed_matrix_slice(self):
        makefile_content = (Path(__file__).resolve().parent.parent.parent / "Makefile").read_text()
        gate_body = _makefile_target_body(makefile_content, "test-correctness-gate")

        assert "BENCHBOX_STRICT_EXPECTED_RESULTS=1" in gate_body
        assert "BENCHBOX_EMIT_RESULT_DIGEST=1" in gate_body
        assert f"CORRECTNESS_GATE_QUERY_IDS := {CORRECTNESS_GATE_QUERY_IDS}" in makefile_content
        assert "BENCHBOX_CORRECTNESS_GATE_QUERY_IDS=$(CORRECTNESS_GATE_QUERY_IDS)" in gate_body
        assert CORRECTNESS_GATE_NODEID in gate_body
        assert "-m stress" in gate_body
        assert "-n 0" in gate_body
        assert "--timeout=1200" in gate_body

    def test_makefile_digest_regen_shares_gate_query_id_source(self):
        makefile_content = (Path(__file__).resolve().parent.parent.parent / "Makefile").read_text()
        regen_body = _makefile_target_body(makefile_content, "correctness-gate-digests-regen")
        assert "BENCHBOX_CORRECTNESS_GATE_QUERY_IDS=$(CORRECTNESS_GATE_QUERY_IDS)" in regen_body
        assert "regenerate_correctness_gate_digests.py" in regen_body

    def test_correctness_gate_nodeid_collects_under_stress(self):
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "--collect-only",
                "-q",
                "-p",
                "no:cacheprovider",
                "-n",
                "0",
                "-m",
                "stress",
                CORRECTNESS_GATE_NODEID,
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=300,
            env={**os.environ, "BENCHBOX_SKIP_TEST_LOCK": "1"},
        )
        assert proc.returncode == 0, (
            f"Gate node-id failed to collect under -m stress (rc={proc.returncode}).\n"
            f"stdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"
        )
        assert CORRECTNESS_GATE_NODEID in proc.stdout, f"Gate node-id not present in collection output:\n{proc.stdout}"

    def test_develop_pr_invokes_bounded_correctness_gate(self):
        repo_root = Path(__file__).resolve().parent.parent.parent
        workflow = yaml.safe_load((repo_root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
        job = workflow["jobs"]["correctness-gate"]
        aggregate = workflow["jobs"]["core"]

        assert job["needs"] == "ci-paths"
        assert "heavy-needed == 'true'" in job["if"]
        assert "make test-correctness-gate" in _workflow_job_run_text(
            repo_root / ".github" / "workflows" / "ci.yml",
            "correctness-gate",
        )
        assert "correctness-gate" in aggregate["needs"]
        assert "needs" in _workflow_job_run_text(
            repo_root / ".github" / "workflows" / "ci.yml",
            "core",
        )

    def test_correctness_gate_job_runs_value_level_equivalence_steps(self):
        repo_root = Path(__file__).resolve().parent.parent.parent
        run_text = _workflow_job_run_text(repo_root / ".github" / "workflows" / "ci.yml", "correctness-gate")

        assert "make test-correctness-gate" in run_text
        assert "make tpchavoc-equivalence-report" in run_text
        assert "make tpchavoc-dataframe-equivalence-report" in run_text
        for target in (
            "make ssb-cross-surface-equivalence-report",
            "make amplab-cross-surface-equivalence-report",
            "make coffeeshop-cross-surface-equivalence-report",
        ):
            assert target in run_text, f"required correctness-gate job missing value-level step: {target}"

    def test_cross_surface_gates_are_pinned_in_make_and_ci(self):
        from benchbox.core.equivalence.cross_surface import GATES

        repo_root = Path(__file__).resolve().parent.parent.parent
        makefile_content = (repo_root / "Makefile").read_text()
        correctness_gate_run_text = _workflow_job_run_text(
            repo_root / ".github" / "workflows" / "ci.yml",
            "correctness-gate",
        )
        missing_make_targets: list[str] = []
        missing_ci_steps: list[str] = []

        for gate_name in sorted(GATES):
            target = _cross_surface_make_target(gate_name)
            try:
                body = _makefile_target_body(makefile_content, target)
            except AssertionError:
                missing_make_targets.append(target)
            else:
                assert f"--benchmark {gate_name}" in body, (
                    f"Make target {target} must invoke the matching cross_surface benchmark {gate_name!r}"
                )
            if f"make {target}" not in correctness_gate_run_text:
                missing_ci_steps.append(f"make {target}")

        assert not missing_make_targets, f"GATES entries missing Make targets: {missing_make_targets}"
        assert not missing_ci_steps, f"GATES entries missing correctness-gate CI steps: {missing_ci_steps}"

    def test_split_cross_surface_targets_cover_every_gate_backend(self):
        from benchbox.core.equivalence.cross_surface import GATES

        repo_root = Path(__file__).resolve().parent.parent.parent
        makefile_content = (repo_root / "Makefile").read_text()
        run_text = _workflow_job_run_text(repo_root / ".github" / "workflows" / "ci.yml", "correctness-gate")
        for gate_name, extra_targets in _SPLIT_CROSS_SURFACE_TARGETS.items():
            covered: set[str] = set()
            for target in (_cross_surface_make_target(gate_name), *extra_targets):
                body = _makefile_target_body(makefile_content, target)
                assert f"--benchmark {gate_name}" in body, f"{target} must run --benchmark {gate_name}"
                assert f"make {target}" in run_text, f"correctness-gate CI is missing make {target}"
                covered.update(re.findall(r"--backend (\S+)", body))
            assert covered == set(GATES[gate_name].backends), (
                f"{gate_name} Make targets run {sorted(covered)}, gate backends are {sorted(GATES[gate_name].backends)}"
            )

    def test_main_release_required_includes_bounded_correctness_gate(self):
        repo_root = Path(__file__).resolve().parent.parent.parent
        workflow = yaml.safe_load((repo_root / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8"))
        job = workflow["jobs"]["correctness-gate"]
        aggregate = workflow["jobs"]["release-required-result"]

        assert "github.base_ref == 'release'" in job["if"]
        assert "make test-correctness-gate" in _workflow_job_run_text(
            repo_root / ".github" / "workflows" / "test.yml",
            "correctness-gate",
        )
        assert "correctness-gate" in aggregate["needs"]

    def test_lint_markers_runs_marker_strategy_policy_explicitly(self):
        makefile_content = (Path(__file__).resolve().parent.parent.parent / "Makefile").read_text()
        lint_body = _makefile_target_body(makefile_content, "lint-markers")

        assert "--collect-only" in lint_body
        assert "tests/unit/test_marker_strategy.py -q" in lint_body

    def test_makefile_test_slow_runs_serially(self):
        makefile_content = (Path(__file__).resolve().parent.parent.parent / "Makefile").read_text()

        assert "test-slow:" in makefile_content
        assert '-m "slow and not (stress or live_integration)" -n 0 --tb=short -v' in makefile_content

    def test_runtime_no_longer_depends_on_generated_bucket_files(self):
        assert not (
            Path(__file__).resolve().parent.parent.parent / "_project" / "config" / "test_speed_buckets.json"
        ).exists()
        assert not (
            Path(__file__).resolve().parent.parent.parent / "_project" / "scripts" / "generate_test_speed_buckets.py"
        ).exists()

    def test_makefile_uses_pytest_consistently(self):
        makefile_content = (Path(__file__).resolve().parent.parent.parent / "Makefile").read_text()

        test_lines = [line for line in makefile_content.split("\n") if line.startswith("\tuv run -- python -m pytest")]

        assert len(test_lines) > 5, "Should have multiple pytest-based commands"
        for line in test_lines:
            assert line.startswith("\tuv run -- python -m pytest"), f"Line should use pytest: {line}"


def _gate_query_ids() -> list[str]:
    return [q for q in CORRECTNESS_GATE_QUERY_IDS.split(",") if q]


class TestCorrectnessGateOracle:
    DBGEN_UNSTABLE_QUERY_IDS = {"11", "16", "18", "20"}

    def test_gate_query_set_is_answer_backed_and_discriminating(self):
        from benchbox.core.expected_results.tpch_results import load_tpch_expected_results

        ids = _gate_query_ids()
        row_counts = load_tpch_expected_results(scale_factor=1.0)

        missing = [q for q in ids if q not in row_counts]
        assert not missing, f"gate queries lack stored SF=1 answer cardinalities: {missing}"

        one_row = [q for q in ids if row_counts[q] == 1]
        multi_row = [q for q in ids if row_counts[q] > 1]
        high_card = [q for q in ids if row_counts[q] >= 100]

        assert len(multi_row) > len(one_row), (
            f"correctness gate is dominated by one-row queries "
            f"({len(one_row)} one-row vs {len(multi_row)} multi-row); add answer-backed "
            f"high/mid-cardinality queries so wrong single-row answers are detectable"
        )
        assert len(high_card) >= 2, (
            f"correctness gate needs at least two answer-backed queries with SF=1 cardinality "
            f">=100 for discrimination; have {sorted(high_card, key=int)}"
        )

    def test_gate_excludes_dbgen_unstable_threshold_queries(self):
        included = self.DBGEN_UNSTABLE_QUERY_IDS.intersection(_gate_query_ids())
        assert not included, (
            f"correctness gate must exclude dbgen-build-unstable threshold queries "
            f"{sorted(included, key=int)}; their HAVING/threshold cardinality varies across "
            f"dbgen builds (see #744 and the test-correctness-gate Makefile note)"
        )

    def test_gate_target_enforces_no_skip(self):
        gate_body = _makefile_target_body(
            (Path(__file__).resolve().parent.parent.parent / "Makefile").read_text(), "test-correctness-gate"
        )
        assert "--junitxml=" in gate_body, "gate must emit a JUnit report to verify the node actually ran"
        assert "tests == 1" in gate_body, "gate guard must require exactly one node to run"
        assert "skipped == 0" in gate_body, "gate guard must require zero selected skips"
        assert "PYTEST_STATUS" in gate_body, "gate must also propagate the pytest exit status"

    def test_gate_node_is_in_stable_matrix(self):
        assert "tpch" in LOCAL_SQL_STABLE_MATRIX.get("duckdb", set()), (
            "gate node tpch-duckdb is no longer in LOCAL_SQL_STABLE_MATRIX; the gate would skip"
        )

    def test_strict_mode_fails_on_scale_drift(self, monkeypatch):
        monkeypatch.setenv("BENCHBOX_STRICT_EXPECTED_RESULTS", "1")
        payload = {"queries": [{"id": "9", "stream": 0, "status": "SUCCESS", "rows": 175}]}
        with pytest.raises(AssertionError, match="strict expected-results"):
            _validate_against_expected_results(payload, "tpch", 0.1, expected_query_ids={"9"})

    def test_strict_mode_fails_on_benchmark_drift(self, monkeypatch):
        monkeypatch.setenv("BENCHBOX_STRICT_EXPECTED_RESULTS", "1")
        payload = {"queries": [{"id": "1", "stream": 0, "status": "SUCCESS", "rows": 5}]}
        with pytest.raises(AssertionError, match="strict expected-results"):
            _validate_against_expected_results(payload, "tpch_future_variant", 1.0, expected_query_ids={"1"})

    def test_strict_mode_passes_when_all_configured_queries_evaluate(self, monkeypatch):
        from benchbox.core.expected_results import register_all_providers
        from benchbox.core.expected_results.registry import get_registry

        monkeypatch.setenv("BENCHBOX_STRICT_EXPECTED_RESULTS", "1")
        register_all_providers()
        q9_digest = get_registry().get_expected_result("tpch", "9", 1.0, 0).value_digest
        payload = {"queries": [{"id": "9", "stream": 0, "status": "SUCCESS", "rows": 175, "digest": q9_digest}]}
        _validate_against_expected_results(payload, "tpch", 1.0, expected_query_ids={"9"})

    def test_strict_mode_fails_on_value_digest_mismatch(self, monkeypatch):
        monkeypatch.setenv("BENCHBOX_STRICT_EXPECTED_RESULTS", "1")
        payload = {
            "queries": [{"id": "9", "stream": 0, "status": "SUCCESS", "rows": 175, "digest": "deadbeefwrongdigest"}]
        }
        with pytest.raises(AssertionError, match="VALUE DIGEST mismatch"):
            _validate_against_expected_results(payload, "tpch", 1.0, expected_query_ids={"9"})

    def test_strict_mode_fails_when_value_digest_missing(self, monkeypatch):
        monkeypatch.setenv("BENCHBOX_STRICT_EXPECTED_RESULTS", "1")
        payload = {"queries": [{"id": "9", "stream": 0, "status": "SUCCESS", "rows": 175}]}
        with pytest.raises(AssertionError, match="strict value-digest"):
            _validate_against_expected_results(payload, "tpch", 1.0, expected_query_ids={"9"})

    def test_non_strict_mode_tolerates_unsupported_validation(self, monkeypatch):
        monkeypatch.delenv("BENCHBOX_STRICT_EXPECTED_RESULTS", raising=False)
        payload = {"queries": [{"id": "9", "stream": 0, "status": "SUCCESS", "rows": 175}]}
        _validate_against_expected_results(payload, "tpch", 0.1, expected_query_ids={"9"})
