from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.utilities.posix_shell import run_posix_shell, skip_without_posix_shell

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
NIGHTLY = REPO_ROOT / ".github" / "workflows" / "nightly.yml"
CEDARDB_CONFIG = REPO_ROOT / "tests" / "uat" / "configs" / "uat-throughput-cedardb-nightly.yaml"
UPLOAD_ARTIFACT_PIN = "actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f"


def _jobs() -> dict[str, Any]:
    return yaml.safe_load(NIGHTLY.read_text(encoding="utf-8"))["jobs"]


def _steps(job: str = "throughput-uat") -> list[dict[str, Any]]:
    return _jobs()[job]["steps"]


def _step(prefix: str, job: str = "throughput-uat") -> dict[str, Any]:
    matches = [step for step in _steps(job) if str(step.get("name", "")).startswith(prefix)]
    assert len(matches) == 1, f"expected exactly one step starting with {prefix!r}, found {len(matches)}"
    return matches[0]


def _run_status_signal(tmp_path: Path, duckdb_result: str, cedardb_result: str) -> tuple[str, str]:
    skip_without_posix_shell()
    calls = tmp_path / "gh-calls.json"
    gh = tmp_path / "gh"
    gh.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['GH_CALLS']).write_text(json.dumps(sys.argv[1:]), encoding='utf-8')\n",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    script = _jobs()["throughput-uat-signal"]["steps"][0]["run"]
    result = run_posix_shell(
        'set -eo pipefail\nexport PATH="$PWD:$PATH"\n' + script,
        cwd=tmp_path,
        env={
            **os.environ,
            "GH_CALLS": str(calls),
            "GH_TOKEN": "token",
            "DUCKDB_RESULT": duckdb_result,
            "CEDARDB_RESULT": cedardb_result,
            "CEDARDB_SWEEP_OUTCOME": "success" if cedardb_result == "success" else "not run",
            "GITHUB_REPOSITORY": "example/repo",
            "GITHUB_SHA": "abc",
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_RUN_ID": "123",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    args = json.loads(calls.read_text(encoding="utf-8"))
    fields = {
        args[index + 1].split("=", 1)[0]: args[index + 1].split("=", 1)[1]
        for index, value in enumerate(args[:-1])
        if value == "-f"
    }
    return fields["state"], fields["description"]


def _is_quarantined(step: dict[str, Any]) -> bool:
    return step.get("continue-on-error") is True or "quarantine" in str(step.get("name", ""))


def _condition(step: dict[str, Any]) -> str:
    return "".join(str(step.get("if", "")).split())


def test_duckdb_sweep_step_gates_without_continue_on_error() -> None:
    step = _step("Run DuckDB throughput UAT cell")
    assert "continue-on-error" not in step
    assert "if" not in step
    assert "uat-throughput-duckdb-nightly.yaml" in step["run"]


def test_duckdb_assert_step_gates_and_runs_after_a_failed_sweep() -> None:
    step = _step("Assert the requested stream count reached the driver")
    assert "continue-on-error" not in step
    assert _condition(step) == "${{!cancelled()}}"
    run = step["run"]
    assert "tests.uat.throughput assert" in run
    assert "--platform duckdb" in run
    assert "--streams 3" in run
    assert "--scale 1" in run
    assert "--evaluate-floor" in run
    assert "uat_throughput_duckdb_nightly_*/cells.jsonl" in run
    assert "--baseline-out" in run


def test_duckdb_assert_resolves_through_cells_jsonl_not_a_results_glob() -> None:
    run = _step("Assert the requested stream count reached the driver")["run"]
    assert "glob(" not in run
    assert "st_mtime" not in run
    assert "THROUGHPUT_UAT_STARTED_AT" not in NIGHTLY.read_text(encoding="utf-8")


def test_throughput_jobs_fail_on_workload_errors() -> None:
    jobs = _jobs()
    for name in ("throughput-uat", "throughput-uat-cedardb"):
        assert "continue-on-error" not in jobs[name]
        assert "if" not in jobs[name]


def test_duckdb_throughput_does_not_depend_on_cedardb_mirror() -> None:
    jobs = _jobs()
    assert "needs" not in jobs["throughput-uat"]
    assert jobs["throughput-uat-cedardb"]["needs"] == "mirror-cedardb"


def test_no_throughput_step_is_quarantined() -> None:
    steps = _steps() + _steps("throughput-uat-cedardb")
    assert [step["name"] for step in steps if _is_quarantined(step)] == []
    assert "#2571" not in yaml.safe_dump(steps)


def test_cedardb_steps_gate_like_the_duckdb_steps() -> None:
    sweep = _step("Run CedarDB throughput UAT cell", "throughput-uat-cedardb")
    assert "continue-on-error" not in sweep
    assert "if" not in sweep
    assert "uat-throughput-cedardb-nightly.yaml" in sweep["run"]
    assert "continue-on-error" not in _step("Assert the CedarDB cell", "throughput-uat-cedardb")


def test_cedardb_assert_still_runs_after_a_failed_sweep() -> None:
    step = _step("Assert the CedarDB cell", "throughput-uat-cedardb")
    assert _condition(step) == "${{!cancelled()}}"
    assert "uat_throughput_cedardb_nightly_*/cells.jsonl" in step["run"]
    assert "--platform cedardb" in step["run"]
    assert "--evaluate-floor" not in step["run"]


@pytest.mark.parametrize(
    ("job", "upload_prefix", "log_path"),
    [
        (
            "throughput-uat",
            "Upload throughput UAT logs and results",
            "~/Developer/benchmark_runs/logs/uat_throughput_duckdb_*",
        ),
        (
            "throughput-uat-cedardb",
            "Upload CedarDB throughput UAT logs and results",
            "~/Developer/benchmark_runs/logs/uat_throughput_cedardb_*",
        ),
    ],
)
def test_each_throughput_job_uploads_its_logs_on_failure(job: str, upload_prefix: str, log_path: str) -> None:
    step = _step(upload_prefix, job)
    assert step["if"] == "always()"
    assert step["uses"].startswith(UPLOAD_ARTIFACT_PIN)
    assert log_path in step["with"]["path"]
    assert "~/Developer/benchmark_runs/results/tpch_sf1_*.json" in step["with"]["path"]
    assert "${{ github.run_attempt }}" in step["with"]["name"]


@pytest.mark.parametrize(
    ("job", "prefixes", "upload_prefix"),
    [
        (
            "throughput-uat",
            ("Run DuckDB throughput", "Assert the requested"),
            "Upload throughput UAT logs and results",
        ),
        (
            "throughput-uat-cedardb",
            ("Run CedarDB throughput", "Assert the CedarDB"),
            "Upload CedarDB throughput UAT logs and results",
        ),
    ],
)
def test_each_throughput_job_uploads_after_its_run_and_assertions(
    job: str, prefixes: tuple[str, str], upload_prefix: str
) -> None:
    names = [step.get("name", "") for step in _steps(job)]
    upload_index = names.index(_step(upload_prefix, job)["name"])
    for prefix in prefixes:
        assert next(index for index, name in enumerate(names) if name.startswith(prefix)) < upload_index


def test_baseline_record_is_uploaded_only_after_a_successful_duckdb_sweep() -> None:
    step = _step("Upload Throughput@Size baseline record")
    assert _step("Run DuckDB throughput UAT cell")["id"] == "duckdb-sweep"
    assert _condition(step) == "${{!cancelled()&&steps.duckdb-sweep.outcome=='success'}}"
    assert step["uses"].startswith(UPLOAD_ARTIFACT_PIN)
    assert step["with"]["path"].startswith("~/Developer/benchmark_runs/throughput-baseline")
    assert step["with"]["retention-days"] == 90
    assert "${{ github.run_id }}" in step["with"]["name"]


def test_failure_signal_waits_for_both_independent_jobs() -> None:
    job = _jobs()["throughput-uat-signal"]
    assert set(job["needs"]) == {"throughput-uat", "throughput-uat-cedardb"}
    assert "".join(str(job["if"]).split()) == "${{always()}}"
    assert job["permissions"] == {"statuses": "write"}
    assert "continue-on-error" not in job
    assert "needs.throughput-uat.result" in yaml.safe_dump(job)
    assert "needs.throughput-uat-cedardb.result" in yaml.safe_dump(job)


@pytest.mark.parametrize(
    ("duckdb_result", "cedardb_result", "expected_state"),
    [
        ("success", "success", "success"),
        ("success", "skipped", "failure"),
        ("success", "failure", "failure"),
        ("failure", "success", "failure"),
        ("cancelled", "success", "error"),
    ],
)
def test_only_both_successful_jobs_publish_success(
    tmp_path: Path, duckdb_result: str, cedardb_result: str, expected_state: str
) -> None:
    state, description = _run_status_signal(tmp_path, duckdb_result, cedardb_result)
    assert state == expected_state
    assert f"DuckDB {duckdb_result}" in description
    assert f"CedarDB job {cedardb_result}" in description


def test_status_description_reports_cedardb_sweep_outcome_without_a_quarantine_label() -> None:
    cedar = _jobs()["throughput-uat-cedardb"]
    assert cedar["outputs"] == {"cedardb_sweep_outcome": "${{ steps.cedardb-sweep.outcome }}"}
    assert _step("Run CedarDB throughput UAT cell", "throughput-uat-cedardb")["id"] == "cedardb-sweep"
    run = "\n".join(str(step.get("run", "")) for step in _jobs()["throughput-uat-signal"]["steps"])
    assert "CEDARDB_SWEEP_OUTCOME" in run
    assert "quarantine" not in run


def test_cedardb_cell_keeps_the_throughput_workload_and_a_bounded_timeout() -> None:
    config = yaml.safe_load(CEDARDB_CONFIG.read_text(encoding="utf-8"))
    execute = config["execute"]
    assert config["platforms"]["include"] == ["cedardb"]
    assert config["benchmarks"]["include"] == ["tpch"]
    assert config["scales"]["rungs"] == [1]
    assert (execute["official"], execute["streams"], execute["phases_arg"]) == (True, 3, "load,throughput")
    assert execute["extra_args"] == ["--platform-option", "port=5435"]
    assert 0 < execute["per_cell_timeout_s"] <= 900
    assert config["cleanup"]["docker_manage_platforms"] is True
    assert "uat_throughput_cedardb_nightly_" in config["output"]["logs_dir_template"]


def test_cedardb_image_is_pinned_by_digest() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker" / "cedardb" / "docker-compose.yml").read_text(encoding="utf-8"))
    digest = "@sha256:dbbacb16b24421a9a123cd1d065e2a049c60b7cc6060d7db2f3cccbf69f5ff62"
    default_image = compose["services"]["cedardb"]["image"]
    ci_image = _jobs()["throughput-uat-cedardb"]["env"]["CEDARDB_IMAGE"]
    assert re.findall(r"@sha256:[0-9a-f]{64}", default_image) == [digest]
    assert re.findall(r"@sha256:[0-9a-f]{64}", ci_image) == [digest]


def test_throughput_jobs_have_only_read_permissions() -> None:
    jobs = _jobs()
    assert jobs["throughput-uat"]["permissions"] == {"contents": "read", "actions": "read"}
    assert jobs["throughput-uat-cedardb"]["permissions"] == {"contents": "read", "packages": "read"}
    assert "actions" not in (yaml.safe_load(NIGHTLY.read_text(encoding="utf-8")).get("permissions") or {})


def test_baseline_history_is_downloaded_before_the_duckdb_assert() -> None:
    names = [step.get("name", "") for step in _steps()]
    download = _step("Download retained Throughput@Size baselines")
    assert names.index(download["name"]) < names.index(_step("Assert the requested stream count")["name"])
    assert names.index(download["name"]) > names.index(_step("Run DuckDB throughput UAT cell")["name"])
    assert "continue-on-error" not in download
    assert _condition(download) == "${{!cancelled()}}"
    assert download["env"] == {"GH_TOKEN": "${{ github.token }}"}
    run = download["run"]
    assert "gh run list" in run
    assert "--workflow nightly.yml" in run
    assert "--branch develop" in run
    assert "--event schedule" in run
    assert "--event workflow_dispatch" not in run
    assert '--dir "${history}/${run_id}"' in run
    assert "gh run download" in run
    assert "--pattern 'throughput-baseline-duckdb-tpch-sf1-*'" in run
    assert "${GITHUB_RUN_ID}" in run


def test_duckdb_assert_reads_the_downloaded_history_not_the_output_dir() -> None:
    run = _step("Assert the requested stream count reached the driver")["run"]
    history = '--baseline-history "$HOME/Developer/benchmark_runs/throughput-baseline-history"'
    assert history in run
    assert '--baseline-out "$HOME/Developer/benchmark_runs/throughput-baseline"' in run
    assert "--baseline-history" not in _step("Assert the CedarDB cell", "throughput-uat-cedardb")["run"]


def test_baseline_reset_variable_reaches_the_assert_environment() -> None:
    env = _jobs()["throughput-uat"]["env"]
    assert env["THROUGHPUT_BASELINE_MIN_RECORDED_AT"] == "${{ vars.THROUGHPUT_BASELINE_MIN_RECORDED_AT }}"
