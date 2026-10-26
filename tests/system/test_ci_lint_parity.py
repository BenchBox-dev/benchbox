from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.medium

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PR_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
PUBLICATION_DOCS_WORKFLOW = None
RELEASE_TEST_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "test.yml"
MAKEFILE = REPO_ROOT / "Makefile"
LINT_JOB_ID = "code-lint"

GUARD_ID_PREFIX = "guard-"
AGGREGATOR_STEP_NAME = "lint-guard-summary"

SETUP_STEP_NAMES = {"Install dependencies"}

EXCLUDED_STEPS: dict[str, str] = {
    "Run promoted regression reproducers": (
        "Promoted slow and medium regression reproducers; they are intentionally CI-gated, run here so they stay "
        "off the unit-test job's critical path, and are not silently represented by the fast local lane."
    ),
}

MERGE_GATE_WORKFLOWS = {
    "ci.yml": PR_WORKFLOW,
    "test.yml": RELEASE_TEST_WORKFLOW,
}
MERGE_GATE_SETUP_NAMES = {
    "Install Python dependencies",
    "Checkout code",
    "Checkout repository",
    "Install dependencies",
    "Install focused test dependencies",
    "Create virtual environment",
    "Fetch base branch",
    "Recreate path lists",
}
MERGE_GATE_NON_GUARD_JOBS = {
    "certification-identity",
    "ci-required-result",
    "release-required-result",
    "core",
    "explorer",
    "results-data",
    "docs",
    "tooling",
}
MERGE_GATE_GUARD_TOKENS = (
    "guard",
    "check",
    "verify",
    "validate",
    "drift",
    "parity",
    "hygiene",
    "isolation",
    "audit",
    "test",
    "lint",
)

MERGE_GATE_LOCAL_EQUIVALENTS: dict[tuple[str, str, str], str] = {
    ("ci.yml", "tuning-liveness", "Run real-CLI tuning liveness matrix"): "tuning-liveness-gate",
    ("ci.yml", "sql-execution-evidence", "Run the executed SQL comparison gate"): "sql-execution-gate",
    ("ci.yml", "package-smoke", "Build package"): "test-package",
    (
        "test.yml",
        "release-readiness",
        "Check bundled SQLGlot is on the newest upstream release",
    ): "sqlglot-upstream-freshness-check",
    (
        "test.yml",
        "release-readiness",
        "Check bundled SQLGlot carries no published advisory",
    ): "sqlglot-advisory-check",
    ("test.yml", "test-package", "Build package"): "test-package",
    ("ci.yml", "ci-paths", "Check release content"): "release-check",
    ("ci.yml", "ci-paths", "Check changelog fragments"): "changelog-check",
    (
        "ci.yml",
        "text-edit-verdict",
        "Re-check the changelog entry against the edited title",
    ): "changelog-check",
    ("ci.yml", "medium-test", "Run changed tests on the curated release tree"): "scripts/release_curation_dry_run.py",
    ("ci.yml", "comment-policy", "Enforce comment and docstring policy"): "comment-policy-check",
    ("ci.yml", "comment-policy", "Enforce pinned notice drift"): "comment-policy-check",
    ("ci.yml", "api-contract-check", "Verify public API symbols against the released wheel"): "api-contract-check",
    ("ci.yml", "content-guard", "Validate YAML hygiene"): "pr-content-guard",
    ("ci.yml", "content-guard", "Validate artifact hygiene"): "pr-content-guard",
    ("ci.yml", "content-guard", "Validate markdown hygiene"): "pr-content-guard",
    ("ci.yml", "skill-integrity", "Validate skill config, receipt, and tool pin"): "skill-integrity-check",
    ("ci.yml", "skill-integrity", "Validate tracked artifact hygiene"): "skill-integrity-check",
    ("ci.yml", "skill-integrity", "Enforce untracked mirror boundary"): "ci-lint",
    ("ci.yml", "ci-paths", "Enforce untracked handoff boundary"): "ci-lint",
    ("ci.yml", "code-lint", "Lint and format check with ruff"): "ci-lint",
    ("ci.yml", "code-lint", "Type check with ty"): "ci-lint",
    ("ci.yml", "code-lint", "Validate test marker annotations"): "ci-lint",
    ("ci.yml", "code-lint", "Validate import layering"): "ci-lint",
    ("ci.yml", "code-lint", "Validate Windows antipatterns"): "ci-lint",
    ("ci.yml", "code-lint", "Validate artifact hygiene"): "ci-lint",
    ("ci.yml", "code-lint", "Validate agent instruction authority and efficiency"): "ci-lint",
    ("ci.yml", "code-lint", "Validate no agent authorship or attribution in PR commits"): "ci-lint",
    ("ci.yml", "code-lint", "UAT spec LOC table drift check"): "ci-lint",
    ("ci.yml", "code-lint", "Compatibility governance drift check"): "ci-lint",
    ("ci.yml", "code-lint", "Platform manifest drift check"): "ci-lint",
    ("ci.yml", "code-lint", "Oracle coverage map drift check"): "ci-lint",
    ("ci.yml", "code-lint", "Public contract drift check"): "ci-lint",
    ("ci.yml", "code-lint", "Documentation contracts drift check"): "ci-lint",
    ("ci.yml", "code-lint", "Dependency inventory drift check"): "ci-lint",
    ("ci.yml", "code-lint", "Release curation list drift check"): "ci-lint",
    ("ci.yml", "code-lint", "Untracked skill-mirror drift guard (cloud parity)"): "ci-lint",
    ("ci.yml", "code-lint", "Check for unsafe patch() string paths in tests"): "ci-lint",
    ("ci.yml", "correctness-gate", "Run the required local-engine cases"): "test-required-local-cases",
    ("ci.yml", "correctness-gate", "Run the polars-df smoke and cross-surface gates on Polars 2.0.0"): (
        "polars-df-compat-check"
    ),
    ("test.yml", "test", "Run linting"): "ci-lint",
    ("test.yml", "test", "Run type checking"): "ci-lint",
    ("test.yml", "compat-test", "Run linting"): "ci-lint",
    ("test.yml", "compat-test", "Run type checking"): "ci-lint",
    ("test.yml", "parity", "Verify parity fixtures match Python source"): "parity-check",
    ("ci.yml", "parity-check", "Verify parity fixtures match Python source"): "parity-check",
    ("ci.yml", "example-validation", "Validate examples and docs references"): "docs-validate",
    ("ci.yml", "example-validation", "Generate documentation inputs"): "docs-generate",
}

MERGE_GATE_EXEMPTIONS: dict[tuple[str, str, str], str] = {
    ("ci.yml", "code-test", "Run changed tests on the curated release tree"): (
        "The curated release-tree selector runs hosted against the PR base ref; no "
        "standalone Make target invokes scripts/release_curation_dry_run.py, and "
        "its logic is pinned by tests/unit/scripts/test_release_curation_dry_run.py."
    ),
    ("ci.yml", "ci-paths", "Validate TODO JSON/Git state contract"): (
        "Hosted classifier bootstrap; it runs before dependency installation and "
        "has no standalone Make target. The local preflight consumes the same "
        "classifier through pr-preflight."
    ),
    ("ci.yml", "ci-paths", "Flag changed paths read by later jobs"): (
        "Hosted job-output step; it turns the classifier's changed-path list into "
        "booleans so later jobs do not receive the full list, and has no local gate."
    ),
    ("ci.yml", "ci-paths", "Decide medium shards"): (
        "Hosted job-output step; it turns the heavy-tier decision and the coverage selection into the shard "
        "matrix for the medium-test job, and has no local gate."
    ),
    ("ci.yml", "ci-paths", "Fetch coverage test map"): (
        "Hosted artifact download; it fetches the coverage map through the Actions "
        "API with a GitHub token, which has no local equivalent."
    ),
    ("ci.yml", "ci-paths", "Select tests by coverage impact"): (
        "Hosted selection against the downloaded map for the PR base SHA; it needs "
        "the Actions artifact and the hosted path decision, so no Make target runs it."
    ),
    ("ci.yml", "ci-paths", "Select tests for the Windows leg"): (
        "Hosted reduction of the coverage selection to test files for the Windows runner; "
        "it reads the hosted selection artifact and path decision, so no Make target runs it."
    ),
    ("ci.yml", "windows-tests", "Run the selected and changed test files on Windows"): (
        "Windows-runner execution of the selected test files; the local macOS checkout has no Windows runner equivalent."
    ),
    ("ci.yml", "tpch-binary-framing", "Verify bundled dbgen binaries emit clean framing"): (
        "Cross-platform macOS/Windows binary smoke; the local macOS checkout has no Windows runner equivalent."
    ),
    ("ci.yml", "medium-collect", "Compile sources in parallel before collection"): (
        "Hosted parallel bytecode warm-up for the collection job; it asserts nothing and a local checkout keeps its own __pycache__."
    ),
    ("ci.yml", "medium-collect", "Collect the complete medium tier"): (
        "Hosted exact-SHA medium collection is consumed by both shards; local test-medium runs the same selector unsplit."
    ),
    ("ci.yml", "medium-test", "Partition the complete medium tier"): (
        "Hosted partitioning binds the shared collection artifact to this SHA; local test-medium runs the full set."
    ),
    ("ci.yml", "core", "Verify complete medium shard coverage"): (
        "Hosted evidence proves both medium shard assignments executed once; local test-medium runs the full set."
    ),
    ("ci.yml", "skill-integrity", "Clone skill sources to the configured checkout paths"): (
        "Hosted provenance step; it clones published skill sources into a fresh "
        "runner home, which a local checkout must not overwrite."
    ),
    ("ci.yml", "skill-integrity", "Preview, apply, verify, and re-check the skill mirrors"): (
        "Hosted source-materialization step; skill-integrity-check proves the "
        "local committed payload but does not clone remote source checkouts."
    ),
    ("ci.yml", "skill-integrity", "Audit instruction authority and PR commit identity"): (
        "The hosted command checks the immutable PR base range; ci-lint covers the "
        "current checkout and agent-commit-range-check covers local commits."
    ),
    ("ci.yml", "postgres-integration", "Wait for PostgreSQL service to be reachable"): (
        "Non-blocking service sample; the hosted container is deliberately not a "
        "local prerequisite for the required product lane."
    ),
    ("ci.yml", "postgres-integration", "Run PostgreSQL live integration tests"): (
        "Non-blocking live service sample; the hosted PostgreSQL container has no "
        "local equivalent in the required product lane."
    ),
    ("ci.yml", "publication-reconciliation", "Run publication plan reconciliation (live Git state)"): (
        "Hosted reconciliation reads the live tracker and publication refs; local "
        "preflight cannot safely substitute those mutable inputs."
    ),
    ("ci.yml", "audit-sha", "Validate changed audit develop SHA stamps"): (
        "Hosted audit lane compares the PR event base and merge-queue ancestry; "
        "those refs are not available as a stable local input."
    ),
    ("test.yml", "check-test-patterns", "Run patch safety check"): (
        "Release-branch-only patch audit; no local target exists and its absence "
        "is explicit rather than a silent CI-only guard."
    ),
    ("test.yml", "release-readiness", "Check dependency upper bounds"): (
        "Release-candidate-only artifact report; local develop preflight does not "
        "produce the release artifact it validates."
    ),
    ("test.yml", "release-readiness", "Check release branch curation"): (
        "Release-candidate-only curation of the curated branch; it is not a develop-tree contract."
    ),
    ("test.yml", "parity", "Detect results explorer"): (
        "Hosted Node-lane dependency sentinel; the local parity target assumes "
        "the checked-in application and does not manufacture its optional tree."
    ),
    ("test.yml", "parity", "Run Vitest parity suite"): (
        "Release parity's Node/npm suite is hosted in this workflow; it is not a "
        "silent omission because the exception names the exact runner surface."
    ),
    ("ci.yml", "ci-paths", "Check base branch"): (
        "Branch-identity guard with no local equivalent: the check reads the "
        "hosted PR base ref (`github.base_ref`), which has no meaning in a "
        "local checkout. The policy itself is pinned by "
        "tests/unit/workflows/test_stacked_pr_base_guard.py."
    ),
    ("ci.yml", "skill-integrity", "Run skill policy and instruction audit contracts"): (
        "The hosted contract bundle runs the complete policy/audit test set; the "
        "same tests run as part of the local full preflight rather than a separate "
        "lightweight Make target."
    ),
    ("ci.yml", "code-test", "Run fast tests"): "Covered by the local `test-fast` target.",
    ("ci.yml", "code-test", "Check fast-tier junit parses"): (
        "Non-blocking hosted parse of the fast-tier junit artifact; it cannot fail the job and has no local equivalent."
    ),
    ("ci.yml", "code-test", "Record fast-tier runner calibration"): (
        "Non-blocking hosted measurement of runner speed for the fast-tier junit artifact; it has no local equivalent."
    ),
    ("ci.yml", "medium-test", "Run medium tests for changed paths"): (
        "Runs the medium tests that `pr-preflight` maps from the changed paths; covered locally by `pr-preflight`."
    ),
    ("test.yml", "medium-collect", "Collect the complete medium tier"): (
        "Hosted exact-SHA medium collection is consumed by the release shards; local test-medium runs it unsplit."
    ),
    ("test.yml", "medium", "Partition the complete medium tier"): (
        "Hosted partitioning binds the shared collection artifact to this SHA; local test-medium runs the full set."
    ),
    ("test.yml", "medium", "Run medium tests"): "Covered by the local `test-medium` target.",
    ("test.yml", "medium-coverage", "Verify complete medium shard coverage"): (
        "Hosted evidence proves the four release shard assignments executed once; local test-medium runs the full set."
    ),
    ("ci.yml", "code-test", "Pre-build the assertion-rewrite cache"): (
        "Cache warm-up for the fast lane; it asserts nothing and only shortens collection in the step after it."
    ),
    ("ci.yml", "medium-test", "Run medium speed tier"): "Covered by the local `test-medium` target.",
    ("ci.yml", "correctness-gate", "Run bounded real-result correctness gate"): (
        "Covered by the local `test-correctness-gate` target."
    ),
    ("ci.yml", "postgres-integration", "Run TPC-Havoc PostgreSQL variant-equivalence sample"): (
        "Hosted PostgreSQL is an optional service sample; the local required lane "
        "does not start or substitute that service."
    ),
    (
        "ci.yml",
        "correctness-gate",
        "Run TPC-Havoc DataFusion variant-equivalence sample and .tbl load regression",
    ): ("Hosted integration sample with its own dependency/runtime; the local required lane does not substitute it."),
    ("ci.yml", "clickhouse-integration", "Run TPC-Havoc ClickHouse variant-equivalence sample"): (
        "Hosted ClickHouse is an optional service sample; the local required lane "
        "does not start or substitute that service."
    ),
    ("ci.yml", "package-smoke", "Test package installation"): "Covered by the local `test-package` target.",
    ("ci.yml", "dependency-audit", "Run security audit"): "Covered by the local `security-audit` target.",
    ("ci.yml", "audit-sha", "Fetch PR head for audit ancestry (merge queue only)"): (
        "Merge-queue-only ref preparation; those ephemeral refs do not exist in a stable local checkout."
    ),
    ("test.yml", "test", "Run linting"): "Covered by the local `ci-lint` target.",
    ("test.yml", "test", "Run type checking"): "Covered by the local `ci-lint` target.",
    ("test.yml", "test", "Run fast tests"): "Covered by the local `test-fast` target.",
    ("test.yml", "compat-test", "Run linting"): "Covered by the local `ci-lint` target.",
    ("test.yml", "compat-test", "Run type checking"): "Covered by the local `ci-lint` target.",
    ("test.yml", "compat-test", "Run fast tests"): "Covered by the local `test-fast` target.",
    ("test.yml", "security", "Run security audit"): "Covered by the local `security-audit` target.",
    ("test.yml", "integration-smoke", "Run integration smoke tests"): (
        "Covered by the local `test-integration-smoke` target."
    ),
    ("test.yml", "integration-table-formats", "Run table-format integration tests"): (
        "Covered by the local integration test target; the hosted job selects an optional table-format marker."
    ),
    ("test.yml", "integration", "Run integration tests"): "Covered by the local `test-integration` target.",
    ("test.yml", "correctness-gate", "Run bounded real-result correctness gate"): (
        "Covered by the local `test-correctness-gate` target."
    ),
    ("test.yml", "test-package", "Test package installation"): "Covered by the local `test-package` target.",
    ("test.yml", "pyspark-tests", "Run PySpark tests"): "Covered by the local `test-pyspark` target.",
    ("ci.yml", "ruleset-drift", "Compare live governance with the trusted runbook"): (
        "Hosted governance check reads the live ruleset via the API with a "
        "secret token; covered locally by tests/unit/test_ruleset_drift.py."
    ),
    ("ci.yml", "results-data-check", "Corpus inventory is current"): (
        "Covered by the corpus inventory check inside the results-data unit; no separate local target."
    ),
    ("ci.yml", "results-data-check", "Corpus validation"): (
        "Covered by results-data/validate_corpus.py run in the results-data unit; no separate local target."
    ),
    ("ci.yml", "results-data-check", "Submission validator stays in sync"): (
        "Hosted branch-copy comparison against published-results; the checker logic is covered by its unit tests."
    ),
    ("ci.yml", "results-data-check", "Corpus, submission, and explorer-pipeline contract tests"): (
        "Covered by the local unit test lane running the same test files."
    ),
}


def _workflow_steps(path: Path) -> list[tuple[str, dict]]:
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [
        (job_name, step)
        for job_name, job in workflow.get("jobs", {}).items()
        for step in job.get("steps", [])
        if isinstance(step, dict)
    ]


def _is_guard_shaped_step(job_name: str, step: dict) -> bool:
    name = str(step.get("name", ""))
    run = str(step.get("run", ""))
    if not run or name in MERGE_GATE_SETUP_NAMES or job_name in MERGE_GATE_NON_GUARD_JOBS:
        return False
    if name == AGGREGATOR_STEP_NAME:
        return False
    return job_name != LINT_JOB_ID and any(token in f"{name} {run}".lower() for token in MERGE_GATE_GUARD_TOKENS)


def _load_lint_job_steps() -> list[dict]:
    workflow = yaml.safe_load(PR_WORKFLOW.read_text(encoding="utf-8"))
    job = workflow["jobs"][LINT_JOB_ID]
    return job["steps"]


def _guard_commands() -> dict[str, list[str]]:
    commands: dict[str, list[str]] = {}
    for step in _load_lint_job_steps():
        name = step.get("name")
        run = step.get("run")
        if not name or not run:
            continue
        if name in SETUP_STEP_NAMES or name == AGGREGATOR_STEP_NAME:
            continue
        lines = [line.strip() for line in run.splitlines() if line.strip()]
        commands[name] = lines
    return commands


def _ci_lint_recipe_text() -> str:
    lines = MAKEFILE.read_text(encoding="utf-8").splitlines()
    start = None
    for i, line in enumerate(lines):
        if line == "ci-lint:":
            start = i + 1
            break
    assert start is not None, "Makefile has no top-level 'ci-lint:' target"

    recipe_lines = []
    for line in lines[start:]:
        if line.startswith("\t"):
            recipe_lines.append(line[1:])
        elif line.strip() == "":
            continue
        else:
            break
    return "\n".join(recipe_lines)


def _normalize_recipe_lines(recipe_text: str) -> list[str]:
    normalized = []
    for line in recipe_text.splitlines():
        stripped = line.strip()
        stripped = stripped.lstrip("@")
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.endswith("\\"):
            stripped = stripped[:-1].rstrip()
        if stripped.endswith(";"):
            stripped = stripped[:-1].rstrip()
        normalized.append(stripped)
    return normalized


def _command_satisfied(command: str, recipe_lines: list[str]) -> bool:
    make_target_match = re.match(r"^make\s+(\S+)$", command)
    if make_target_match:
        target = make_target_match.group(1)
        pattern = re.compile(rf"^(\$\(MAKE\)|make)\s+{re.escape(target)}$")
        return any(pattern.match(line) for line in recipe_lines)
    return command in recipe_lines


def test_lint_job_guards_run_in_ci_lint() -> None:
    guard_commands = _guard_commands()
    recipe_lines = _normalize_recipe_lines(_ci_lint_recipe_text())

    missing: list[str] = []
    for step_name, commands in guard_commands.items():
        if step_name in EXCLUDED_STEPS:
            continue
        for command in commands:
            if not _command_satisfied(command, recipe_lines):
                missing.append(f"{step_name!r}: {command!r}")

    assert not missing, (
        "ci.yml `lint` job guard(s) not mirrored in `make ci-lint` -- these "
        "will fire for the FIRST time in CI instead of locally. Add the "
        "command to the ci-lint recipe in the Makefile (or, if genuinely "
        "CI-only, add it to EXCLUDED_STEPS with a reason):\n  " + "\n  ".join(missing)
    )


def test_timing_policy_strict_command_is_mirrored_in_ci_lint() -> None:
    timing = next(step for step in _load_lint_job_steps() if step.get("id") == "guard-timing-policy")
    workflow_run = str(timing["run"])
    recipe_lines = _normalize_recipe_lines(_ci_lint_recipe_text())
    strict_command = "uv run -- python _project/scripts/timing_policy_check.py --strict"

    assert strict_command in recipe_lines
    assert strict_command in workflow_run


def test_fast_lane_guard_command_is_mirrored_in_ci_lint() -> None:
    guard = next(step for step in _load_lint_job_steps() if step.get("id") == "guard-fast-lane-markers")
    workflow_run = str(guard["run"])
    recipe_lines = _normalize_recipe_lines(_ci_lint_recipe_text())
    strict_command = "uv run -- python _project/scripts/fast_lane_ceiling_check.py --strict --manifest-from-env"

    assert strict_command in recipe_lines
    assert strict_command in workflow_run


def test_non_lint_merge_gate_guards_have_local_equivalent_or_documented_exemption() -> None:
    missing: list[str] = []
    for workflow_name, workflow_path in MERGE_GATE_WORKFLOWS.items():
        for job_name, step in _workflow_steps(workflow_path):
            if not _is_guard_shaped_step(job_name, step):
                continue
            key = (workflow_name, job_name, str(step.get("name", "")))
            if key in MERGE_GATE_LOCAL_EQUIVALENTS:
                continue
            if key in MERGE_GATE_EXEMPTIONS:
                continue
            missing.append(f"{workflow_name}:{job_name}:{key[2]}")

    assert not missing, (
        "merge-gate guard/check step(s) have neither a local equivalent nor a "
        "documented exemption; add one to MERGE_GATE_LOCAL_EQUIVALENTS or "
        f"MERGE_GATE_EXEMPTIONS: {missing}"
    )


def test_merge_gate_local_equivalents_and_exemptions_are_documented() -> None:
    makefile = "\n".join(
        path.read_text(encoding="utf-8") for path in [MAKEFILE, *sorted((REPO_ROOT / "make").glob("*.mk"))]
    )

    for key, target in MERGE_GATE_LOCAL_EQUIVALENTS.items():
        assert target in makefile, f"{key} local equivalent {target!r} is not discoverable in a Make target"

    assert all(reason.strip() for reason in MERGE_GATE_EXEMPTIONS.values())


@pytest.mark.parametrize("base_checker_present", [True, False], ids=["unsupported", "missing"])
def test_pinned_notice_gate_fails_closed_without_running_candidate_checker(
    tmp_path: Path, base_checker_present: bool
) -> None:
    step = next(
        step
        for job_name, step in _workflow_steps(PR_WORKFLOW)
        if job_name == "comment-policy" and step.get("name") == "Enforce pinned notice drift"
    )

    def git(*args: str) -> str:
        result = subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True, text=True)
        return result.stdout.strip()

    git("init")
    git("config", "user.name", "Workflow smoke")
    git("config", "user.email", "workflow-smoke@example.invalid")
    (tmp_path / "LICENSE").write_text("pinned\n", encoding="utf-8")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    checker = scripts / "check_comment_cleanup_scope.py"
    if base_checker_present:
        checker.write_text("import argparse\nargparse.ArgumentParser().parse_args()\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "base")
    base = git("rev-parse", "HEAD")

    marker = tmp_path / "candidate-checker-executed"
    checker.write_text("from pathlib import Path\nPath('candidate-checker-executed').touch()\n", encoding="utf-8")
    (tmp_path / "LICENSE").write_text("changed\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "candidate")

    environment = os.environ.copy()
    environment.update({"BASE_REF": base, "RUNNER_TEMP": str(tmp_path)})
    script_path = tmp_path / "workflow-step.sh"
    script_path.write_text(step["run"], encoding="utf-8")
    result = subprocess.run(
        ["bash", "workflow-step.sh"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    if base_checker_present:
        assert "--check-drift" in result.stderr
    else:
        assert "Trusted pinned-notice checker is missing" in result.stderr
    assert not marker.exists()


def test_ci_lint_identity_check_is_gated_rather_than_fed_a_synthetic_identity() -> None:
    recipe = _ci_lint_recipe_text()
    assert "ci_lint_environment_gate.py agent-identity" in recipe, (
        "ci-lint no longer consults the environment gate before agent-identity-check"
    )
    assert "GIT_CONFIG_COUNT" not in recipe, (
        "the synthetic runner identity is back; the audit it fed is skipped on CI, so it feeds nothing"
    )
    assert "ci_lint_environment_gate.py agent-commit-range" not in recipe, (
        "agent-commit-range-check is gated; it is the merge-time authorship control and must always run"
    )
    assert "$(MAKE) agent-commit-range-check" in recipe


def test_excluded_steps_still_exist() -> None:
    current_step_names = {step.get("name") for step in _load_lint_job_steps()}
    stale = sorted(name for name in EXCLUDED_STEPS if name not in current_step_names)
    assert not stale, (
        "EXCLUDED_STEPS references ci.yml `lint`-job step name(s) that no "
        f"longer exist (renamed or removed) -- update the exclusion: {stale}"
    )


def test_guard_steps_follow_naming_convention() -> None:
    violations: list[str] = []
    for step in _load_lint_job_steps():
        name = step.get("name")
        step_id = step.get("id", "")
        has_run = "run" in step
        is_setup = name in SETUP_STEP_NAMES or "run" not in step
        is_aggregator = name == AGGREGATOR_STEP_NAME
        is_guard = has_run and not is_setup and not is_aggregator

        has_guard_id = step_id.startswith(GUARD_ID_PREFIX)
        has_continue_on_error = step.get("continue-on-error") is True

        if is_guard:
            if not has_guard_id:
                violations.append(f"{name!r}: guard step missing id prefix {GUARD_ID_PREFIX!r}")
            if not has_continue_on_error:
                violations.append(f"{name!r}: guard step missing `continue-on-error: true`")
        else:
            if has_guard_id:
                violations.append(f"{name!r}: non-guard step must not carry a {GUARD_ID_PREFIX!r} id")
            if has_continue_on_error:
                violations.append(f"{name!r}: non-guard step must not carry `continue-on-error: true`")

    assert not violations, (
        "code-lint step(s) violate the guard naming convention (id prefix "
        f"{GUARD_ID_PREFIX!r} + continue-on-error: true for guards, neither "
        "for setup/aggregator steps):\n  " + "\n  ".join(violations)
    )


def test_lint_guard_summary_step_exists() -> None:
    steps = _load_lint_job_steps()
    matches = [step for step in steps if step.get("name") == AGGREGATOR_STEP_NAME]
    assert matches, f"code-lint job has no {AGGREGATOR_STEP_NAME!r} step"
    assert len(matches) == 1, f"code-lint job has multiple {AGGREGATOR_STEP_NAME!r} steps"

    aggregator = matches[0]
    assert aggregator.get("id") == AGGREGATOR_STEP_NAME, (
        f"{AGGREGATOR_STEP_NAME!r} step must use a matching `id:` (found {aggregator.get('id')!r})"
    )
    assert aggregator.get("if") == "always()", (
        f"{AGGREGATOR_STEP_NAME!r} step must run with `if: always()` so it still reports after an earlier guard fails"
    )
    assert steps[-1] is aggregator, f"{AGGREGATOR_STEP_NAME!r} must be the last step in the code-lint job"


def test_lint_guard_summary_accepts_only_success() -> None:
    aggregator = next(step for step in _load_lint_job_steps() if step.get("name") == AGGREGATOR_STEP_NAME)
    run = aggregator["run"]

    assert 'if [ "$outcome" = "success" ]; then' in run
    assert "merge_group" not in run
    assert "skipped" not in run
    assert 'echo "FAILED: $id ($outcome)"' in run
    assert "All lint guards passed." in run
