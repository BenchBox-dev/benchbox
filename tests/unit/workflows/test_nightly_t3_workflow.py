from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.utilities.posix_shell import posix_shell, run_posix_shell, skip_without_posix_shell

pytestmark = [pytest.mark.unit, pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
WORKFLOW = WORKFLOWS_DIR / "nightly-v2.yml"

DOMAIN_JOBS = {
    "docker": "t3:docker",
    "cloud": "t3:cloud",
    "perf": "t3:perf",
    "matrix": "t3:matrix",
    "extension": "t3:extension",
    "install": "t3:install",
    "throughput": "t3:throughput",
    "throughput-cedardb": "t3:throughput-cedardb",
    "drift": "t3:drift",
    "quarantine": "t3:quarantine",
    "linkcheck": "t3:linkcheck",
    "liveness": "t3:liveness",
    "ruleset-drift": "t3:ruleset-drift",
    "windows": "t3:windows",
    "tpcds-obt": "t3:tpcds-obt",
    "sql-execution-gate-obt": "t3:sql-execution-gate-obt",
    "durations-refresh": "t3:durations",
}

_PINNED_ACTION = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")


def _load(path: Path = WORKFLOW) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    triggers = workflow.get("on", workflow.get(True))
    assert isinstance(triggers, dict), "workflow has no `on:` mapping"
    return triggers


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return job.get("steps", [])


def _run_text(job: dict[str, Any]) -> str:
    return "\n".join(str(step.get("run", "")) for step in _steps(job))


def _mirrored_images(job: dict[str, Any]) -> list[str]:
    return json.loads(job["with"]["images"])


def _compose_default_image(compose: str) -> str:
    match = re.search(r"image: \$\{[A-Z_]+:-([^}]+)\}", (REPO_ROOT / compose).read_text(encoding="utf-8"))
    assert match, compose
    return match.group(1)


def _run_workflow_script(script: str, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    skip_without_posix_shell()
    return run_posix_shell(
        'set -eo pipefail\nexport PATH="$PWD:$PATH"\n' + script,
        cwd=cwd,
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
        check=False,
    )


def test_triggers_are_schedule_and_dispatch_without_branch_filters() -> None:
    triggers = _triggers(_load())
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    assert not any("branches" in (value or {}) for value in triggers.values() if isinstance(value, dict))


def test_schedule_does_not_collide_with_other_workflows() -> None:
    crons = _triggers(_load())["schedule"]
    assert len(crons) == 1
    mine = crons[0]["cron"]
    others: dict[str, str] = {}
    for path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        if path == WORKFLOW:
            continue
        document = _load(path)
        triggers = document.get("on", document.get(True)) if isinstance(document, dict) else None
        if not isinstance(triggers, dict):
            continue
        for entry in triggers.get("schedule") or []:
            others[entry["cron"]] = path.name
    assert mine not in others, f"cron {mine!r} already used by {others.get(mine)}"


def test_every_domain_has_a_job() -> None:
    jobs = _load()["jobs"]
    missing = sorted(set(DOMAIN_JOBS) - set(jobs))
    assert not missing, f"missing domain jobs: {missing}"


def test_docker_covers_all_three_services() -> None:
    docker = _load()["jobs"]["docker"]
    services = {entry["service"] for entry in docker["strategy"]["matrix"]["include"]}
    assert services == {"postgres", "clickhouse", "trino"}
    assert set(_mirrored_images(_load()["jobs"]["mirror-images"])) >= services
    assert docker["strategy"]["fail-fast"] is False


def test_docker_attempts_pulls_after_mirror_failure() -> None:
    jobs = _load()["jobs"]
    docker = jobs["docker"]
    assert str(docker["if"]).replace(" ", "") == "${{!cancelled()}}"
    needs = docker["needs"]
    assert "mirror-images" in ([needs] if isinstance(needs, str) else needs)
    login = next(step for step in _steps(docker) if "docker login ghcr.io" in str(step.get("run", "")))
    assert "if" not in login
    assert "mirror-images" in jobs["report"]["needs"]


def _run_mirror_step(
    tmp_path: Path,
    *,
    image: str,
    source_image: str,
    source_manifest: str,
    mirror_manifest: str | None = None,
    mirror_error: str | None = None,
    copied_manifest: str | None = None,
    pin_source: bool = True,
    copy_plan: dict[str, list[str | None]] | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
    if pin_source and "@sha256:" not in source_image:
        digest = hashlib.sha256(source_manifest.encode("utf-8")).hexdigest()
        source_image = f"{source_image}@sha256:{digest}"
    mirror = _load(WORKFLOWS_DIR / "mirror-ci-images.yml")
    step = next(
        step for step in _steps(mirror["jobs"]["mirror"]) if step.get("name") == "Verify pinned digest and mirror image"
    )
    (tmp_path / "docker").write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "compose = sys.argv[sys.argv.index('-f') + 1]\n"
        "service = {'docker/clickhouse/docker-compose.yml': 'clickhouse', "
        "'docker/trino/docker-compose.yml': 'trino', "
        "'docker/cedardb/docker-compose.yml': 'cedardb', "
        "'docker/postgresql/docker-compose.yml': 'postgresql'}[compose]\n"
        "print(json.dumps({'services': {service: {'image': os.environ['SOURCE_IMAGE']}}}))\n",
        encoding="utf-8",
    )
    (tmp_path / "jq").write_text(
        f"#!{sys.executable}\nimport json, sys\nprint(json.load(sys.stdin)['services'][sys.argv[4]]['image'])\n",
        encoding="utf-8",
    )
    (tmp_path / "skopeo").write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "calls = Path(os.environ['SKOPEO_CALLS'])\n"
        "with calls.open('a', encoding='utf-8') as output:\n"
        "    output.write(json.dumps(args) + '\\n')\n"
        "if args[0] == 'inspect':\n"
        "    mirror = Path(os.environ['MIRROR_STATE'])\n"
        "    if mirror.exists():\n"
        "        sys.stdout.write(mirror.read_text(encoding='utf-8'))\n"
        "    elif 'MIRROR_MANIFEST' in os.environ:\n"
        "        sys.stdout.write(os.environ['MIRROR_MANIFEST'])\n"
        "    else:\n"
        "        sys.stderr.write(os.environ.get('MIRROR_ERROR', "
        "'Error reading manifest: manifest unknown'))\n"
        "        raise SystemExit(1)\n"
        "elif args[0] == 'copy':\n"
        "    plan = json.loads(os.environ.get('COPY_PLAN', '{}'))\n"
        "    state = Path(os.environ['MIRROR_STATE'] + '.copy-count')\n"
        "    counts = json.loads(state.read_text(encoding='utf-8')) if state.exists() else {}\n"
        "    index = counts.get(args[-2], 0)\n"
        "    counts[args[-2]] = index + 1\n"
        "    state.write_text(json.dumps(counts), encoding='utf-8')\n"
        "    outcomes = plan.get(args[-2], [])\n"
        "    if index < len(outcomes) and outcomes[index] is not None:\n"
        "        sys.stderr.write(outcomes[index])\n"
        "        raise SystemExit(1)\n"
        "    manifest = os.environ.get('COPIED_MANIFEST', os.environ['SOURCE_MANIFEST'])\n"
        "    Path(os.environ['MIRROR_STATE']).write_text(manifest, encoding='utf-8')\n"
        "else:\n"
        "    raise SystemExit(2)\n",
        encoding="utf-8",
    )
    (tmp_path / "sha256sum").write_text(
        f"#!{sys.executable}\n"
        "import hashlib, sys\n"
        "from pathlib import Path\n"
        "path = Path(sys.argv[-1])\n"
        "print(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + sys.argv[-1])\n",
        encoding="utf-8",
    )
    for executable in ("docker", "jq", "skopeo", "sha256sum"):
        (tmp_path / executable).chmod(0o755)
    calls_path = tmp_path / "skopeo-calls.jsonl"
    summary_path = tmp_path / "step-summary.md"
    env = {
        "IMAGE": image,
        "SOURCE_IMAGE": source_image,
        "SOURCE_MANIFEST": source_manifest,
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_STEP_SUMMARY": str(summary_path),
        "SKOPEO_CALLS": str(calls_path),
        "MIRROR_STATE": str(tmp_path / "mirror-manifest.json"),
        "MIRROR_RETRY_BASE_SECONDS": "0",
    }
    if copy_plan is not None:
        env["COPY_PLAN"] = json.dumps(copy_plan)
    if mirror_manifest is not None:
        env["MIRROR_MANIFEST"] = mirror_manifest
    if mirror_error is not None:
        env["MIRROR_ERROR"] = mirror_error
    if copied_manifest is not None:
        env["COPIED_MANIFEST"] = copied_manifest
    result = _run_workflow_script(step["run"], tmp_path, env)
    calls = (
        [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()]
        if calls_path.exists()
        else []
    )
    return result, calls


@pytest.mark.parametrize(
    ("image", "source_image"),
    [
        ("clickhouse", "clickhouse/clickhouse-server:25.8"),
        ("trino", "trinodb/trino:480"),
    ],
)
def test_mirror_reuses_an_existing_digest_without_reading_docker_hub(
    tmp_path: Path, image: str, source_image: str
) -> None:
    manifest = '{"schemaVersion":2,"annotations":{"revision":"same"}}'
    result, calls = _run_mirror_step(
        tmp_path,
        image=image,
        source_image=source_image,
        source_manifest=manifest,
        mirror_manifest=manifest,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not any(call[0] == "copy" for call in calls)
    assert all("docker://docker.io/" not in call[-1] for call in calls if call[0] == "inspect")
    digest = hashlib.sha256(manifest.encode("utf-8")).hexdigest()
    assert f"sha256:{digest}" in (tmp_path / "step-summary.md").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("image", "source_image"),
    [
        ("clickhouse", "clickhouse/clickhouse-server:25.8"),
        ("trino", "trinodb/trino:480"),
    ],
)
@pytest.mark.parametrize(
    "inspect_error",
    [
        "unauthorized: access denied",
        "Get https://ghcr.io/v2/: dial tcp: i/o timeout",
        "unexpected status code 503 Service Unavailable",
    ],
)
def test_mirror_fails_closed_on_non_missing_registry_errors(
    tmp_path: Path, image: str, source_image: str, inspect_error: str
) -> None:
    result, calls = _run_mirror_step(
        tmp_path,
        image=image,
        source_image=source_image,
        source_manifest='{"schemaVersion":2}',
        mirror_error=inspect_error,
    )
    assert result.returncode != 0
    assert inspect_error in result.stdout + result.stderr
    assert not any(call[0] == "copy" for call in calls)


@pytest.mark.parametrize(
    ("image", "source_image"),
    [
        ("clickhouse", "clickhouse/clickhouse-server:25.8"),
        ("trino", "trinodb/trino:480"),
    ],
)
def test_mirror_refuses_an_unpinned_compose_source(tmp_path: Path, image: str, source_image: str) -> None:
    result, calls = _run_mirror_step(
        tmp_path,
        image=image,
        source_image=source_image,
        source_manifest='{"schemaVersion":2}',
        pin_source=False,
    )
    assert result.returncode != 0
    assert "must be pinned to a SHA-256 digest" in result.stdout + result.stderr
    assert not calls


@pytest.mark.parametrize(
    ("image", "source_image"),
    [
        ("clickhouse", "clickhouse/clickhouse-server:25.8"),
        ("trino", "trinodb/trino:480"),
    ],
)
def test_mirror_fails_if_existing_digest_reference_returns_other_content(
    tmp_path: Path, image: str, source_image: str
) -> None:
    result, calls = _run_mirror_step(
        tmp_path,
        image=image,
        source_image=source_image,
        source_manifest='{"schemaVersion":2,"annotations":{"revision":"pinned"}}',
        mirror_manifest='{"schemaVersion":2,"annotations":{"revision":"other"}}',
    )
    assert result.returncode != 0
    assert "GHCR mirror digest mismatch" in result.stdout + result.stderr
    assert not any(call[0] == "copy" for call in calls)


@pytest.mark.parametrize(
    ("image", "source_image"),
    [
        ("clickhouse", "clickhouse/clickhouse-server:25.8"),
        ("trino", "trinodb/trino:480"),
    ],
)
def test_mirror_copies_only_a_missing_digest_and_checks_the_result(
    tmp_path: Path, image: str, source_image: str
) -> None:
    manifest = '{"schemaVersion":2,"annotations":{"revision":"pinned"}}'
    digest = hashlib.sha256(manifest.encode("utf-8")).hexdigest()
    tagless_source = f"{source_image.split(':')[0]}@sha256:{digest}"
    result, calls = _run_mirror_step(
        tmp_path,
        image=image,
        source_image=source_image,
        source_manifest=manifest,
        mirror_error="Error reading manifest: MANIFEST_UNKNOWN: manifest unknown",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    copy = next(call for call in calls if call[0] == "copy")
    assert "--all" in copy and "--preserve-digests" in copy
    assert copy[-2] == f"docker://docker.io/{tagless_source}"
    assert copy[-1] == f"docker://ghcr.io/benchbox-dev/{image}:digest-{digest}"
    assert any(
        call[-1] == f"docker://ghcr.io/benchbox-dev/{image}@sha256:{digest}" for call in calls if call[0] == "inspect"
    )


@pytest.mark.parametrize(
    ("image", "source_image"),
    [
        ("clickhouse", "clickhouse/clickhouse-server:25.8"),
        ("trino", "trinodb/trino:480"),
    ],
)
def test_mirror_checks_the_digest_after_copy(tmp_path: Path, image: str, source_image: str) -> None:
    result, calls = _run_mirror_step(
        tmp_path,
        image=image,
        source_image=source_image,
        source_manifest='{"schemaVersion":2,"annotations":{"revision":"pinned"}}',
        mirror_error="manifest unknown",
        copied_manifest='{"schemaVersion":2,"annotations":{"revision":"different"}}',
    )
    assert result.returncode != 0
    assert "GHCR mirror digest mismatch" in result.stdout + result.stderr
    assert any(call[0] == "copy" for call in calls)


def test_mirror_keeps_the_cedardb_digest_reference(tmp_path: Path) -> None:
    manifest = '{"schemaVersion":2,"mediaType":"application/vnd.oci.image.index.v1+json"}'
    digest = hashlib.sha256(manifest.encode("utf-8")).hexdigest()
    result, calls = _run_mirror_step(
        tmp_path,
        image="cedardb",
        source_image=f"cedardb/cedardb@sha256:{digest}",
        source_manifest=manifest,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    copy = next(call for call in calls if call[0] == "copy")
    assert copy[-1] == f"docker://ghcr.io/benchbox-dev/cedardb:digest-{digest}"
    assert any(
        call[-1] == f"docker://ghcr.io/benchbox-dev/cedardb@sha256:{digest}" for call in calls if call[0] == "inspect"
    )


def test_wheel_matrix_covers_python_versions_and_operating_systems() -> None:
    job = _load()["jobs"]["matrix"]
    matrix = job["strategy"]["matrix"]
    assert matrix["python-version"] == ["3.11", "3.12", "3.13", "3.14"]
    assert set(matrix["os"]) == {"ubuntu-latest", "macos-latest", "windows-latest"}
    text = _run_text(job)
    assert "generate_data" in text and "TPCH" in text and "TPCDS" in text, "dbgen and dsdgen must run per OS"


def test_cloud_job_is_gated_by_repository_variable() -> None:
    condition = str(_load()["jobs"]["cloud"]["if"])
    assert "vars.BENCHBOX_T3_CLOUD_ENABLED == 'true'" in condition
    assert "||" not in condition


def test_drift_job_is_weekly_gated_and_covers_all_checks() -> None:
    jobs = _load()["jobs"]
    assert jobs["drift"]["needs"] == ["drift-gate"]
    assert "needs.drift-gate.outputs.run == 'true'" in str(jobs["drift"]["if"])
    gate_text = _run_text(jobs["drift-gate"])
    assert "date -u +%u" in gate_text and "workflow_dispatch" in gate_text
    text = _run_text(jobs["drift"])
    for needle in (
        "results-data/bundles",
        "find_public_path_leaks",
        "check_submission_validator_sync.py",
        "cross_surface_baseline_autodetect.py",
        "generate_pricing_data.py --refresh",
    ):
        assert needle in text, f"drift job is missing {needle!r}"


def test_quarantine_job_tolerates_unregistered_marker() -> None:
    text = _run_text(_load()["jobs"]["quarantine"])
    assert "-m quarantine" in text
    assert "pytest.mark.quarantine" in text, "job must check marker registration before selecting it"


@pytest.mark.parametrize("pytest_exit, expected", [(0, 0), (1, 1), (2, 2), (5, 0)])
def test_quarantine_exit_codes_under_runner_shell(tmp_path: Path, pytest_exit: int, expected: int) -> None:
    stub = tmp_path / "uv"
    stub.write_text(
        '#!/usr/bin/env bash\nif [[ "$*" == *--markers* ]]; then\n'
        '  echo "@pytest.mark.quarantine: quarantined tests"\nelse\n'
        f"  exit {pytest_exit}\nfi\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    step = next(step for step in _steps(_load()["jobs"]["quarantine"]) if step.get("name") == "Run quarantined tests")
    result = _run_workflow_script(step["run"], tmp_path)
    assert result.returncode == expected, result.stdout + result.stderr


@pytest.mark.parametrize("result_count", [0, 1, 2])
def test_perf_comparison_uses_only_the_current_job_result(tmp_path: Path, result_count: int) -> None:
    runner_temp = tmp_path / "job output"
    output_root = runner_temp / "perf-smoke"
    results = output_root / "results"
    results.mkdir(parents=True)
    for index in range(result_count):
        (results / f"tpch_sf001_duckdb_sql_{index}.json").write_text("{}", encoding="utf-8")
    job = _load()["jobs"]["perf"]
    setup = next(step for step in _steps(job) if step.get("name") == "Set isolated benchmark output")
    github_env = tmp_path / "github-env"
    setup_result = _run_workflow_script(
        setup["run"], tmp_path, {"RUNNER_TEMP": str(runner_temp), "GITHUB_ENV": str(github_env)}
    )
    assert setup_result.returncode == 0, setup_result.stdout + setup_result.stderr
    key, _, configured_root = github_env.read_text(encoding="utf-8").strip().partition("=")
    assert key == "BENCHBOX_OUTPUT_DIR" and Path(configured_root) == output_root
    step = next(step for step in _steps(job) if step.get("id") == "current")
    github_output = tmp_path / "github-output"
    result = _run_workflow_script(step["run"], tmp_path, {key: configured_root, "GITHUB_OUTPUT": str(github_output)})
    assert (result.returncode == 0) == (result_count == 1), result.stdout + result.stderr
    if result_count == 1:
        published = github_output.read_text(encoding="utf-8").strip().removeprefix("path=")
        assert Path(published) == results / "tpch_sf001_duckdb_sql_0.json"


@pytest.mark.parametrize("tier", ["fast", "slow"])
@pytest.mark.parametrize("pytest_exit, tee_exit, expected", [(0, 0, 0), (1, 0, 0), (2, 0, 2), (5, 0, 5), (0, 1, 1)])
def test_duration_measurement_keeps_assertion_reports_and_rejects_runner_failures(
    tmp_path: Path, tier: str, pytest_exit: int, tee_exit: int, expected: int
) -> None:
    (tmp_path / "t3-durations").mkdir()
    stub = tmp_path / "uv"
    stub.write_text(
        '#!/usr/bin/env bash\nfor arg in "$@"; do\n'
        '  if [[ "$arg" == --junitxml=* ]]; then\n'
        '    printf "<testsuite tests=\\"1\\"/>" > "${arg#--junitxml=}"\n'
        "  fi\ndone\necho 'measurement output'\n"
        f"exit {pytest_exit}\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    if tee_exit:
        tee = tmp_path / "tee"
        tee.write_text(f"#!/usr/bin/env bash\ncat >/dev/null\nexit {tee_exit}\n", encoding="utf-8")
        tee.chmod(0o755)
    step = next(
        step
        for step in _steps(_load()["jobs"]["durations-refresh"])
        if step.get("name") == f"Measure {tier} tier durations"
    )
    result = _run_workflow_script(step["run"], tmp_path)
    assert result.returncode == expected, result.stdout + result.stderr
    assert (tmp_path / "t3-durations" / f"junit-{tier}.xml").read_text(encoding="utf-8") == '<testsuite tests="1"/>'
    if tee_exit == 0:
        assert (tmp_path / "t3-durations" / f"pytest-{tier}.exit").read_text(encoding="utf-8").strip() == str(
            pytest_exit
        )


@pytest.mark.parametrize("compare_exit", [0, 1])
def test_perf_comparison_preserves_threshold_and_failure(tmp_path: Path, compare_exit: int) -> None:
    current = tmp_path / "perf-smoke" / "results" / "tpch_sf001_duckdb_sql_current.json"
    arguments = tmp_path / "compare-arguments"
    step = next(
        step
        for step in _steps(_load()["jobs"]["perf"])
        if step.get("name") == "Compare against baseline (fail on >10% regression)"
    )
    script = step["run"].replace("${{ steps.current.outputs.path }}", str(current))
    script = 'uv() { printf "%s\\n" "$@" > "$ARGUMENTS"; return "$COMPARE_EXIT"; }\n' + script
    result = _run_workflow_script(script, tmp_path, {"ARGUMENTS": str(arguments), "COMPARE_EXIT": str(compare_exit)})
    assert result.returncode == compare_exit, result.stdout + result.stderr
    assert arguments.read_text(encoding="utf-8").splitlines() == [
        "run",
        "benchbox",
        "compare",
        "_project/baselines/perf_smoke_duckdb_tpch_001.json",
        str(current),
        "--fail-on-regression",
        "10%",
        "--min-regression-delta",
        "7ms",
        "--min-aggregate-regression-delta",
        "82ms",
    ]


@pytest.mark.parametrize("fast_exit, slow_exit", [(0, 0), (1, 0), (0, 1)])
def test_failed_duration_samples_keep_both_reports_and_regenerated_artifact(
    tmp_path: Path, fast_exit: int, slow_exit: int
) -> None:
    tool = tmp_path / "_project" / "scripts" / "update_test_durations.py"
    tool.parent.mkdir(parents=True)
    shutil.copyfile(REPO_ROOT / "_project/scripts/update_test_durations.py", tool)
    (tmp_path / "tests").mkdir()
    shutil.copyfile(REPO_ROOT / "tests/duration_policy.py", tmp_path / "tests/duration_policy.py")
    (tmp_path / "tests/__init__.py").touch()
    stub = tmp_path / "uv"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$*" != *"-m pytest"* ]]; then shift; exec "$@"; fi\n'
        'for arg in "$@"; do\n'
        '  if [[ "$arg" == --junitxml=* ]]; then\n'
        '    report="${arg#--junitxml=}"\n'
        '    case "$report" in *fast*) tier=fast; rc="$FAST_EXIT" ;; *slow*) tier=slow; rc="$SLOW_EXIT" ;; esac\n'
        '    body="/>"; if [ "$rc" = "1" ]; then body="><failure message=\\"boom\\"/></testcase>"; fi\n'
        '    printf \'<testsuite tests="1"><testcase classname="tests.unit.test_sample" '
        'name="test_%s" time="1.2"%s</testsuite>\' "$tier" "$body" > "$report"\n'
        '  fi\ndone\necho "measurement $tier"\nexit "$rc"\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    steps = _steps(_load()["jobs"]["durations-refresh"])
    env = {"FAST_EXIT": str(fast_exit), "SLOW_EXIT": str(slow_exit)}
    for name in ["Measure fast tier durations", "Measure slow tier durations", "Regenerate duration file"]:
        step = next(step for step in steps if step.get("name") == name)
        script = step["run"].replace("uv run -- python", f"uv run -- {shlex.quote(Path(sys.executable).as_posix())}")
        result = _run_workflow_script(script, tmp_path, env)
        assert result.returncode == 0, result.stdout + result.stderr
    output = tmp_path / "t3-durations"
    for tier in ["fast", "slow"]:
        assert (output / f"junit-{tier}.xml").is_file()
        assert (output / f"durations-{tier}.txt").read_text(encoding="utf-8").strip() == f"measurement {tier}"
    artifact = json.loads((output / "test_durations.json").read_text(encoding="utf-8"))
    assert len(artifact["tests"]) == 2
    final = next(step for step in steps if step.get("name") == "Require successful sampled tests")
    result = _run_workflow_script(final["run"], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert ("::warning::" in result.stdout) == (fast_exit == 1 or slow_exit == 1)


def test_durations_job_emits_pytest_durations_artifact() -> None:
    job = _load()["jobs"]["durations-refresh"]
    assert "--durations=0" in _run_text(job)
    uploads = [step for step in _steps(job) if str(step.get("uses", "")).startswith("actions/upload-artifact@")]
    assert uploads and uploads[-1]["with"]["name"] == "t3-durations"


def test_every_domain_job_uploads_artifacts() -> None:
    jobs = _load()["jobs"]
    without = [
        name
        for name in DOMAIN_JOBS
        if not any(str(step.get("uses", "")).startswith("actions/upload-artifact@") for step in _steps(jobs[name]))
    ]
    assert not without, f"domain jobs without an artifact upload: {without}"


def test_default_permissions_are_read_only() -> None:
    assert _load()["permissions"] == {"contents": "read"}


def test_write_permissions_are_scoped_to_reporting_and_image_publication() -> None:
    jobs = _load()["jobs"]
    issue_writers = {name for name, job in jobs.items() if (job.get("permissions") or {}).get("issues") == "write"}
    package_writers = {name for name, job in jobs.items() if (job.get("permissions") or {}).get("packages") == "write"}
    assert issue_writers == {"report"}
    assert package_writers == {"mirror-images", "mirror-cedardb"}
    assert jobs["report"]["permissions"] == {"contents": "read", "issues": "write"}
    assert jobs["mirror-images"]["permissions"] == {"contents": "read", "packages": "write"}
    assert jobs["mirror-cedardb"]["permissions"] == {"contents": "read", "packages": "write"}
    assert jobs["docker"]["permissions"] == {"contents": "read", "packages": "read"}
    for name in DOMAIN_JOBS:
        assert "write" not in (jobs[name].get("permissions") or {}).values()


@pytest.mark.parametrize(
    "mirror_result, docker_result, existing, expected_action",
    [
        ("failure", "skipped", False, "create"),
        ("failure", "success", False, "create"),
        ("failure", "success", True, "comment"),
        ("success", "failure", False, "create"),
        ("success", "success", True, "close"),
        ("success", "skipped", True, None),
    ],
)
def test_report_keeps_mirror_failures_visible_and_optional_skips_unchanged(
    tmp_path: Path, mirror_result: str, docker_result: str, existing: bool, expected_action: str | None
) -> None:
    if shutil.which("jq") is None:
        pytest.skip("report execution requires jq")
    gh = tmp_path / "gh"
    gh.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "with Path(os.environ['GH_CALLS']).open('a') as output:\n"
        "    output.write(json.dumps(args) + '\\n')\n"
        "if args[:2] == ['issue', 'list'] and 't3:docker' in args and os.environ['EXISTING'] == 'true':\n"
        "    print('42')\n",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    needs = {job: {"result": "skipped"} for job in DOMAIN_JOBS}
    needs["docker"]["result"] = docker_result
    needs["mirror-images"] = {"result": mirror_result}
    call_path = tmp_path / "gh-calls.jsonl"
    result = _run_workflow_script(
        _load()["jobs"]["report"]["steps"][0]["run"],
        tmp_path,
        {
            "NEEDS_JSON": json.dumps(needs),
            "GH_CALLS": str(call_path),
            "EXISTING": str(existing).lower(),
            "GITHUB_REPOSITORY": "example/repo",
            "GITHUB_RUN_ID": "123",
            "GITHUB_SHA": "abc",
            "RUN_URL": "https://example.test/run/123",
            "DOC_URL": "https://example.test/docs",
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    calls = [json.loads(line) for line in call_path.read_text(encoding="utf-8").splitlines()]
    mutations = [args for args in calls if args[:2] in [["issue", "create"], ["issue", "comment"], ["issue", "close"]]]
    if expected_action is None:
        assert mutations == []
    else:
        assert any(args[:2] == ["issue", expected_action] for args in mutations)
        assert all("t3:docker" in args or "42" in args for args in mutations)
    if mirror_result == "failure":
        assert not any(args[:2] == ["issue", "close"] for args in mutations)
        body = next(args[args.index("--body") + 1] for args in mutations if "--body" in args)
        assert "Image mirror infrastructure failed" in body
        assert f"database test job result: {docker_result}" in body


def test_actions_are_pinned_by_sha() -> None:
    unpinned: list[str] = []
    for name, job in _load()["jobs"].items():
        for step in _steps(job):
            uses = step.get("uses")
            if uses and not _PINNED_ACTION.match(str(uses)):
                unpinned.append(f"{name}: {uses}")
    assert not unpinned, f"actions must be pinned to a full commit SHA: {unpinned}"


def test_perf_smoke_workflow_uses_the_same_regression_gate_options() -> None:
    workflow = _load(WORKFLOWS_DIR / "perf-smoke.yml")
    compare = next(
        step for step in _steps(workflow["jobs"]["perf-smoke"]) if str(step.get("name", "")).startswith("Compare")
    )
    nightly = next(
        step
        for step in _steps(_load()["jobs"]["perf"])
        if step.get("name") == "Compare against baseline (fail on >10% regression)"
    )

    for step in (compare, nightly):
        assert "--fail-on-regression 10%" in step["run"]
        assert "--min-regression-delta 7ms" in step["run"]
        assert "--min-aggregate-regression-delta 82ms" in step["run"]


def test_liveness_domain_runs_the_shared_script_with_read_only_actions_access() -> None:
    job = _load()["jobs"]["liveness"]

    assert job["permissions"] == {"actions": "read", "contents": "read"}
    assert "python3 scripts/scheduled_workflow_liveness.py" in _run_text(job)
    assert "GITHUB_TOKEN" in str([step.get("env") for step in _steps(job)])


def test_liveness_domain_checks_out_the_default_branch_that_scheduled_runs_come_from() -> None:
    checkout = next(
        step for step in _steps(_load()["jobs"]["liveness"]) if "actions/checkout@" in str(step.get("uses"))
    )

    assert checkout["with"]["ref"] == "develop"


def test_windows_domain_runs_the_fast_tier_without_continue_on_error() -> None:
    job = _load()["jobs"]["windows"]
    text = _run_text(job)

    assert job["runs-on"] == "windows-latest"
    assert "continue-on-error" not in job
    assert "fast and not (slow or stress or resource_heavy or live_integration)" in text
    assert "python-version" in str([step.get("with") for step in _steps(job)])
    assert isinstance(job["timeout-minutes"], int) and job["timeout-minutes"] <= 60


def test_windows_domain_timeout_leaves_margin_over_the_measured_run() -> None:
    measured_run_minutes = 38.13

    assert _load()["jobs"]["windows"]["timeout-minutes"] >= measured_run_minutes * 1.25


RETIRING_WORKFLOWS = {"nightly.yml", "release-canary.yml", "validate-release-pr.yml"}


def test_a_kept_scheduled_workflow_runs_the_ruleset_drift_check() -> None:
    runners = [
        path.name
        for path in sorted(WORKFLOWS_DIR.glob("*.yml"))
        if path.name not in RETIRING_WORKFLOWS
        and "scripts/ruleset_drift_check.py" in path.read_text(encoding="utf-8")
        and "schedule" in _triggers(_load(path))
    ]

    assert runners, "no workflow outside the retiring set runs scripts/ruleset_drift_check.py on a schedule"


def test_ruleset_drift_domain_reports_through_the_token_and_uploads_evidence() -> None:
    job = _load()["jobs"]["ruleset-drift"]
    text = _run_text(job)

    assert "scripts/ruleset_drift_check.py" in text
    assert "--require-bypass-actor-visibility" in text
    assert "continue-on-error" not in job
    uploads = [step for step in _steps(job) if "upload-artifact" in str(step.get("uses", ""))]
    assert [step["with"]["name"] for step in uploads] == ["t3-ruleset-drift"]


def test_throughput_domain_runs_the_duckdb_cell_and_reads_history_from_both_nightlies() -> None:
    jobs = _load()["jobs"]
    text = _run_text(jobs["throughput"])

    assert "tests/uat/configs/uat-throughput-duckdb-nightly.yaml" in text
    assert "--platform duckdb --benchmark tpch --scale 1 --streams 3" in text
    assert "--evaluate-floor" in text
    assert "for workflow in nightly-v2.yml nightly.yml; do" in text
    assert '--workflow "$workflow" --branch develop --event schedule' in text
    assert "throughput-baseline-duckdb-tpch-sf1-*" in text
    uploads = {
        step["with"]["name"].split("-${{", 1)[0]
        for step in _steps(jobs["throughput"])
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    }
    assert "throughput-baseline-duckdb-tpch-sf1" in uploads


def test_throughput_cedardb_domain_runs_the_cell_from_the_mirrored_image() -> None:
    jobs = _load()["jobs"]
    job = jobs["throughput-cedardb"]
    text = _run_text(job)

    assert job["needs"] == "mirror-cedardb"
    assert "if" not in job
    assert jobs["mirror-images"]["with"]["images"] == '["clickhouse", "trino", "postgres"]'
    assert jobs["mirror-cedardb"]["with"]["images"] == '["cedardb"]'
    assert "mirror-cedardb" in jobs["report"]["needs"]
    assert job["permissions"] == {"contents": "read", "packages": "read"}
    assert job["env"]["CEDARDB_IMAGE"].startswith("ghcr.io/benchbox-dev/cedardb@sha256:")
    assert "tests/uat/configs/uat-throughput-cedardb-nightly.yaml" in text
    assert "--platform cedardb --benchmark tpch --scale 1 --streams 3" in text


def test_report_attributes_cedardb_mirror_failure_to_the_cedardb_domain(tmp_path: Path) -> None:
    if shutil.which("jq") is None:
        pytest.skip("report execution requires jq")
    gh = tmp_path / "gh"
    gh.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "with Path(os.environ['GH_CALLS']).open('a') as output:\n"
        "    output.write(json.dumps(args) + '\\n')\n",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    needs = {job: {"result": "skipped"} for job in DOMAIN_JOBS}
    needs["mirror-images"] = {"result": "success"}
    needs["mirror-cedardb"] = {"result": "failure"}
    call_path = tmp_path / "gh-calls.jsonl"
    result = _run_workflow_script(
        _load()["jobs"]["report"]["steps"][0]["run"],
        tmp_path,
        {
            "NEEDS_JSON": json.dumps(needs),
            "GH_CALLS": str(call_path),
            "GITHUB_REPOSITORY": "example/repo",
            "GITHUB_RUN_ID": "123",
            "GITHUB_SHA": "abc",
            "RUN_URL": "https://example.test/run/123",
            "DOC_URL": "https://example.test/docs",
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    calls = [json.loads(line) for line in call_path.read_text(encoding="utf-8").splitlines()]
    issue_creates = [args for args in calls if args[:2] == ["issue", "create"]]
    assert len(issue_creates) == 1
    assert "t3:throughput-cedardb" in issue_creates[0]
    body = issue_creates[0][issue_creates[0].index("--body") + 1]
    assert "CedarDB image mirror infrastructure failed" in body
    assert "throughput cell result: skipped" in body
    assert not any("t3:docker" in args for args in issue_creates)


def test_throughput_signal_publishes_the_commit_status_the_retiring_workflow_published() -> None:
    job = _load()["jobs"]["throughput-signal"]
    text = _run_text(job)

    assert set(job["needs"]) == {"throughput", "throughput-cedardb"}
    assert str(job["if"]).replace(" ", "") == "${{always()}}"
    assert job["permissions"] == {"statuses": "write"}
    assert 'context="nightly/throughput-uat"' in text
    assert "success:success) state=success" in text
    assert "cancelled:*|*:cancelled) state=error" in text
    assert "throughput-signal" not in _load()["jobs"]["report"]["needs"]


@pytest.mark.parametrize(
    ("image", "compose", "expected_source"),
    [
        (
            "clickhouse",
            "docker/clickhouse/docker-compose.yml",
            "docker://docker.io/clickhouse/clickhouse-server@sha256:0152dd511befe6a2c2ef53e930726179669b08116da78500b37c51c96ff5ee77",
        ),
        (
            "trino",
            "docker/trino/docker-compose.yml",
            "docker://docker.io/trinodb/trino@sha256:1565e8cac299a32dd9177a4da2d748da4ceb9f1560a9c409d1d18fd72ea5253e",
        ),
        (
            "cedardb",
            "docker/cedardb/docker-compose.yml",
            "docker://docker.io/cedardb/cedardb@sha256:dbbacb16b24421a9a123cd1d065e2a049c60b7cc6060d7db2f3cccbf69f5ff62",
        ),
        (
            "postgres",
            "docker/postgresql/docker-compose.yml",
            "docker://public.ecr.aws/docker/library/postgres@sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336",
        ),
    ],
)
def test_mirror_source_reference_for_the_committed_compose_pins(
    tmp_path: Path, image: str, compose: str, expected_source: str
) -> None:
    source_image = _compose_default_image(compose)
    digest = source_image.rsplit("@", 1)[1]
    assert expected_source.endswith(f"@{digest}")
    _, calls = _run_mirror_step(
        tmp_path,
        image=image,
        source_image=source_image,
        source_manifest="{}",
        mirror_error="Error reading manifest: manifest unknown",
        pin_source=False,
    )
    copy = next(call for call in calls if call[0] == "copy")
    assert copy[-2] == expected_source
    assert copy[-1] == f"docker://ghcr.io/benchbox-dev/{image}:digest-{digest.removeprefix('sha256:')}"


def test_mirror_leaves_a_digest_only_source_unchanged(tmp_path: Path) -> None:
    manifest = '{"schemaVersion":2,"annotations":{"revision":"pinned"}}'
    digest = hashlib.sha256(manifest.encode("utf-8")).hexdigest()
    result, calls = _run_mirror_step(
        tmp_path,
        image="cedardb",
        source_image=f"cedardb/cedardb@sha256:{digest}",
        source_manifest=manifest,
        mirror_error="Error reading manifest: manifest unknown",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    copy = next(call for call in calls if call[0] == "copy")
    assert copy[-2] == f"docker://docker.io/cedardb/cedardb@sha256:{digest}"


@pytest.mark.parametrize("image", ["clickhouse", "trino", "postgres"])
def test_mirror_rejects_a_source_without_a_digest(tmp_path: Path, image: str) -> None:
    result, calls = _run_mirror_step(
        tmp_path,
        image=image,
        source_image="example/image:1",
        source_manifest="{}",
        pin_source=False,
    )
    assert result.returncode != 0
    assert "must be pinned to a SHA-256 digest" in result.stdout + result.stderr
    assert not calls


def test_t3_docker_postgres_pulls_from_the_ghcr_mirror() -> None:
    docker = _load()["jobs"]["docker"]
    image = docker["env"]["POSTGRES_IMAGE"]
    compose_image = _compose_default_image("docker/postgresql/docker-compose.yml")
    assert image.startswith("ghcr.io/benchbox-dev/postgres:")
    assert image.rsplit("@", 1)[1] == compose_image.rsplit("@", 1)[1]
    assert "POSTGRES_IMAGE" in (REPO_ROOT / "docker/postgresql/docker-compose.yml").read_text(encoding="utf-8")


POSTGRES17_DIGEST = "sha256:2d2b8998d31037bf721cfdf764d76ba74171b4fab3431b7f72c27c56ddbdf9e3"


def test_mirror_copies_the_pinned_postgres17_digest_into_the_shared_postgres_repository(tmp_path: Path) -> None:
    result, calls = _run_mirror_step(
        tmp_path,
        image="postgres17",
        source_image="unused",
        source_manifest="{}",
        mirror_error="Error reading manifest: manifest unknown",
        pin_source=False,
    )
    copy = next(call for call in calls if call[0] == "copy")
    assert copy[-2] == f"docker://public.ecr.aws/docker/library/postgres@{POSTGRES17_DIGEST}"
    assert copy[-1] == f"docker://ghcr.io/benchbox-dev/postgres:digest-{POSTGRES17_DIGEST.removeprefix('sha256:')}"
    assert "digest mismatch" in result.stdout + result.stderr


def test_mirror_skips_the_copy_when_postgres17_is_already_mirrored(tmp_path: Path) -> None:
    result, calls = _run_mirror_step(
        tmp_path,
        image="postgres17",
        source_image="unused",
        source_manifest="{}",
        mirror_manifest="{}",
        pin_source=False,
    )
    assert not any(call[0] == "copy" for call in calls)
    assert [call[-1] for call in calls] == [f"docker://ghcr.io/benchbox-dev/postgres@{POSTGRES17_DIGEST}"]
    assert "digest mismatch" in result.stdout + result.stderr


POSTGRES17_ECR = f"docker://public.ecr.aws/docker/library/postgres@{POSTGRES17_DIGEST}"
POSTGRES17_HUB = f"docker://docker.io/library/postgres@{POSTGRES17_DIGEST}"
RATE_LIMITED = "reading manifest: toomanyrequests: Data limit exceeded"


def _run_postgres17_copy(
    tmp_path: Path, copy_plan: dict[str, list[str | None]], **kwargs: Any
) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
    return _run_mirror_step(
        tmp_path,
        image="postgres17",
        source_image="unused",
        source_manifest="{}",
        mirror_error="Error reading manifest: manifest unknown",
        pin_source=False,
        copy_plan=copy_plan,
        **kwargs,
    )


def _copy_sources(calls: list[list[str]]) -> list[str]:
    return [call[-2] for call in calls if call[0] == "copy"]


def test_mirror_falls_back_to_docker_hub_when_the_first_source_stays_rate_limited(tmp_path: Path) -> None:
    result, calls = _run_postgres17_copy(tmp_path, {POSTGRES17_ECR: [RATE_LIMITED] * 3})
    assert _copy_sources(calls) == [POSTGRES17_ECR] * 3 + [POSTGRES17_HUB]
    assert "digest mismatch" in result.stdout + result.stderr


def test_mirror_retries_a_transient_error_on_the_same_source_before_falling_back(tmp_path: Path) -> None:
    result, calls = _run_postgres17_copy(
        tmp_path, {POSTGRES17_ECR: [RATE_LIMITED, "received unexpected HTTP status: 503", None]}
    )
    assert _copy_sources(calls) == [POSTGRES17_ECR] * 3
    assert "Transient error copying" in result.stdout + result.stderr


def test_mirror_does_not_retry_or_fall_back_on_authentication_errors(tmp_path: Path) -> None:
    result, calls = _run_postgres17_copy(tmp_path, {POSTGRES17_ECR: ["unauthorized: authentication required"]})
    assert result.returncode != 0
    assert _copy_sources(calls) == [POSTGRES17_ECR]
    assert "Authentication failure" in result.stdout + result.stderr


def test_mirror_moves_on_without_retrying_when_a_source_does_not_have_the_digest(tmp_path: Path) -> None:
    _, calls = _run_postgres17_copy(tmp_path, {POSTGRES17_ECR: ["manifest unknown: manifest unknown"]})
    assert _copy_sources(calls) == [POSTGRES17_ECR, POSTGRES17_HUB]


def test_mirror_reports_every_source_error_when_all_sources_fail(tmp_path: Path) -> None:
    result, calls = _run_postgres17_copy(
        tmp_path,
        {POSTGRES17_ECR: [RATE_LIMITED] * 3, POSTGRES17_HUB: ["manifest unknown: manifest unknown"]},
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert _copy_sources(calls) == [POSTGRES17_ECR] * 3 + [POSTGRES17_HUB]
    assert "Could not copy postgres17" in output
    assert f"{POSTGRES17_ECR} (attempt 3): {RATE_LIMITED}" in output
    assert f"{POSTGRES17_HUB} (attempt 1): manifest unknown" in output


def test_mirror_checks_the_digest_of_a_fallback_copy(tmp_path: Path) -> None:
    result, calls = _run_postgres17_copy(
        tmp_path, {POSTGRES17_ECR: [RATE_LIMITED] * 3}, copied_manifest='{"other":true}'
    )
    assert result.returncode != 0
    assert _copy_sources(calls)[-1] == POSTGRES17_HUB
    assert "GHCR mirror digest mismatch" in result.stdout + result.stderr


def test_mirror_postgres_pin_falls_back_to_the_docker_hub_official_image(tmp_path: Path) -> None:
    source_image = _compose_default_image("docker/postgresql/docker-compose.yml")
    digest = source_image.rsplit("@", 1)[1]
    primary = f"docker://public.ecr.aws/docker/library/postgres@{digest}"
    _, calls = _run_mirror_step(
        tmp_path,
        image="postgres",
        source_image=source_image,
        source_manifest="{}",
        mirror_error="manifest unknown",
        pin_source=False,
        copy_plan={primary: [RATE_LIMITED] * 3},
    )
    assert _copy_sources(calls) == [primary] * 3 + [f"docker://docker.io/library/postgres@{digest}"]


@pytest.mark.parametrize("image", ["clickhouse", "trino", "cedardb"])
def test_mirror_keeps_single_source_images_single_source(tmp_path: Path, image: str) -> None:
    source_image = _compose_default_image(f"docker/{image}/docker-compose.yml")
    primary = f"docker://docker.io/{source_image.split(':')[0].split('@')[0]}@{source_image.rsplit('@', 1)[1]}"
    result, calls = _run_mirror_step(
        tmp_path,
        image=image,
        source_image=source_image,
        source_manifest="{}",
        mirror_error="manifest unknown",
        pin_source=False,
        copy_plan={primary: [RATE_LIMITED] * 3},
    )
    assert result.returncode != 0
    assert _copy_sources(calls) == [primary] * 3


def test_nightly_postgres_integration_pulls_the_authenticated_ghcr_mirror_after_it_is_published() -> None:
    document = _load(WORKFLOWS_DIR / "nightly.yml")
    consumer = document["jobs"]["postgres-integration"]
    service = consumer["services"]["postgres"]
    assert service["image"] == f"ghcr.io/benchbox-dev/postgres:17@{POSTGRES17_DIGEST}"
    assert service["credentials"] == {
        "username": "${{ github.actor }}",
        "password": "${{ github.token }}",
    }
    assert consumer["permissions"]["packages"] == "read"
    needs = consumer["needs"]
    mirror = document["jobs"]["mirror-postgres17"]
    assert "mirror-postgres17" in ([needs] if isinstance(needs, str) else needs)
    assert mirror["uses"] == "./.github/workflows/mirror-ci-images.yml"
    assert mirror["permissions"]["packages"] == "write"
    assert _mirrored_images(mirror) == ["postgres17"]
    assert "public.ecr.aws/docker/library/postgres" not in (WORKFLOWS_DIR / "nightly.yml").read_text(encoding="utf-8")


_POSTGRES_17_GHCR_IMAGE = (
    "ghcr.io/benchbox-dev/postgres:17@sha256:2d2b8998d31037bf721cfdf764d76ba74171b4fab3431b7f72c27c56ddbdf9e3"
)


def _service_images(workflow: dict[str, Any]) -> list[str]:
    return [
        service["image"]
        for job in workflow["jobs"].values()
        for service in (job.get("services") or {}).values()
        if isinstance(service, dict) and "image" in service
    ]


def test_no_workflow_service_uses_a_public_ecr_postgres_image() -> None:
    offenders = [
        f"{path.name}: {image}"
        for path in sorted(WORKFLOWS_DIR.glob("*.yml"))
        for image in _service_images(_load(path))
        if "public.ecr.aws" in image and "postgres" in image
    ]
    assert offenders == []


def test_pr_sql_evidence_and_nightly_postgres_share_one_ghcr_digest() -> None:
    ci_job = _load(WORKFLOWS_DIR / "ci.yml")["jobs"]["sql-execution-evidence"]
    nightly_job = _load(WORKFLOWS_DIR / "nightly.yml")["jobs"]["postgres-integration"]
    for job in (ci_job, nightly_job):
        service = job["services"]["postgres"]
        assert service["image"] == _POSTGRES_17_GHCR_IMAGE
        assert service["credentials"] == {
            "username": "${{ github.actor }}",
            "password": "${{ github.token }}",
        }
        assert job["permissions"]["packages"] == "read"


def test_sql_execution_gate_obt_runs_the_obt_gate_against_ghcr_postgres() -> None:
    job = _load()["jobs"]["sql-execution-gate-obt"]
    assert job["name"].startswith("t3:sql-execution-gate-obt")
    assert job["services"]["postgres"]["image"] == _POSTGRES_17_GHCR_IMAGE
    assert job["services"]["postgres"]["credentials"] == {
        "username": "${{ github.actor }}",
        "password": "${{ github.token }}",
    }
    assert job["permissions"]["packages"] == "read"
    assert "--benchmarks tpcds_obt" in _run_text(job)
    assert "BENCHBOX_POSTGRES_DSN" in _run_text(job)


QUESTDB_DIGEST = "sha256:67eaed863ebb2383227919ea9a5499a4a39ceca3862c20b974c84a10e11cdf89"


def _run_questdb_copy(
    tmp_path: Path, copy_plan: dict[str, list[str | None]] | None = None
) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
    return _run_mirror_step(
        tmp_path,
        image="questdb",
        source_image="unused",
        source_manifest="{}",
        mirror_error="Error reading manifest: manifest unknown",
        pin_source=False,
        copy_plan=copy_plan,
    )


def test_mirror_copies_the_pinned_questdb_digest_into_the_questdb_repository(tmp_path: Path) -> None:
    result, calls = _run_questdb_copy(tmp_path)
    copy = next(call for call in calls if call[0] == "copy")
    assert copy[-2] == f"docker://docker.io/questdb/questdb@{QUESTDB_DIGEST}"
    assert copy[-1] == f"docker://ghcr.io/benchbox-dev/questdb:digest-{QUESTDB_DIGEST.removeprefix('sha256:')}"
    assert calls[0][-1] == f"docker://ghcr.io/benchbox-dev/questdb@{QUESTDB_DIGEST}"
    assert "digest mismatch" in result.stdout + result.stderr


def test_mirror_falls_back_to_the_gcr_mirror_when_docker_hub_stays_rate_limited(tmp_path: Path) -> None:
    primary = f"docker://docker.io/questdb/questdb@{QUESTDB_DIGEST}"
    _, calls = _run_questdb_copy(tmp_path, {primary: [RATE_LIMITED] * 3})
    assert _copy_sources(calls) == [primary] * 3 + [f"docker://mirror.gcr.io/questdb/questdb@{QUESTDB_DIGEST}"]


def test_nightly_seeds_the_questdb_mirror_with_package_write_permission() -> None:
    mirror = _load(WORKFLOWS_DIR / "nightly.yml")["jobs"]["mirror-questdb"]
    assert mirror["uses"] == "./.github/workflows/mirror-ci-images.yml"
    assert mirror["permissions"] == {"contents": "read", "packages": "write"}
    assert _mirrored_images(mirror) == ["questdb"]


_DIGEST_PINNED_GHCR = re.compile(r"^ghcr\.io/benchbox-dev/[a-z0-9._-]+(:[\w.-]+)?@sha256:[0-9a-f]{64}$")


def test_ci_service_images_come_from_the_ghcr_mirror_pinned_by_digest() -> None:
    services = [
        (job_name, name, service)
        for job_name, job in _load(WORKFLOWS_DIR / "ci.yml")["jobs"].items()
        for name, service in (job.get("services") or {}).items()
    ]
    assert services
    offenders = [
        f"{job}.{name}: {svc['image']}" for job, name, svc in services if not _DIGEST_PINNED_GHCR.match(svc["image"])
    ]
    assert offenders == []


def test_lint_job_pulls_the_questdb_mirror_with_job_token_credentials() -> None:
    job = _load(WORKFLOWS_DIR / "ci.yml")["jobs"]["code-lint"]
    service = job["services"]["questdb"]
    assert service["image"] == f"ghcr.io/benchbox-dev/questdb@{QUESTDB_DIGEST}"
    assert service["credentials"] == {
        "username": "${{ github.actor }}",
        "password": "${{ github.token }}",
    }
    assert job["permissions"] == {"contents": "read", "pull-requests": "read", "packages": "read"}
