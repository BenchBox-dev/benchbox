from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

WORKFLOW = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "ci.yml"
STEP_NAME = "verify-fast-duration"
DOWNLOAD_NAME = "Download fast-tier junit for duration verification"


def core() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["core"]


def named(name: str) -> dict[str, Any]:
    return next(item for item in core()["steps"] if item.get("name") == name)


def test_verify_step_blocks_core_on_regression() -> None:
    assert named(STEP_NAME).get("continue-on-error") is not True
    assert "code-tests-needed == 'true'" in named(STEP_NAME)["if"]
    assert named(DOWNLOAD_NAME)["continue-on-error"] is True


def test_verify_step_is_time_bounded_inside_the_core_timeout() -> None:
    assert named(STEP_NAME)["timeout-minutes"] < core()["timeout-minutes"]


def test_verify_step_reads_the_pr_artifact_and_writes_the_job_summary() -> None:
    run = named(STEP_NAME)["run"]
    assert "scripts.build_fast_duration_baseline verify" in run
    assert "--baseline tests/fixtures/fast_tier_baseline" in run
    assert "--run-dir fast-junit" in run
    assert '--summary "$GITHUB_STEP_SUMMARY"' in run
    download = named(DOWNLOAD_NAME)["with"]
    assert download["name"].startswith("fast-junit-${{ github.event.pull_request.number }}-")
    assert download["path"] == "fast-junit"


def test_verdict_artifact_is_bound_to_the_pr_head_and_uploads_on_failure() -> None:
    assert "--report-out fast-duration-verdict.json" in named(STEP_NAME)["run"]
    upload = named("Upload fast-duration verdict")
    assert upload["continue-on-error"] is True
    assert "always()" in upload["if"]
    assert upload["with"]["name"] == (
        "fast-duration-verdict-${{ github.event.pull_request.number }}-${{ github.event.pull_request.head.sha }}"
    )
    assert upload["with"]["path"] == "fast-duration-verdict.json"
    assert upload["with"]["overwrite"] is True


def test_a_verdict_is_uploaded_even_when_no_fast_tier_tests_ran() -> None:
    record = named("Record fast-duration verdict not applicable")
    assert record["continue-on-error"] is True
    assert "code-tests-needed == 'false'" in record["if"]
    assert '"verdict": "not-applicable"' in record["run"]
    assert "fast-duration-verdict.json" in record["run"]
    assert "code-tests-needed" not in named("Upload fast-duration verdict")["if"]
    names = [item.get("name") for item in core()["steps"]]
    assert names.index(STEP_NAME) < names.index(record["name"]) < names.index("Upload fast-duration verdict")


def test_verify_steps_run_after_the_aggregate_and_do_not_feed_it() -> None:
    names = [item.get("name") for item in core()["steps"]]
    assert names.index("Aggregate core result") < names.index(DOWNLOAD_NAME) < names.index(STEP_NAME)
    aggregate = named("Aggregate core result")
    assert "verify-fast-duration" not in aggregate["run"]
    assert "verify-fast-duration" not in str(core()["needs"])


def test_core_checkout_includes_the_verify_script_inputs() -> None:
    sparse = named("Checkout the result helper")["with"]["sparse-checkout"].split()
    for path in (
        "scripts/build_fast_duration_baseline.py",
        "tests/__init__.py",
        "tests/duration_policy.py",
        "tests/fixtures/fast_tier_baseline/",
    ):
        assert path in sparse
