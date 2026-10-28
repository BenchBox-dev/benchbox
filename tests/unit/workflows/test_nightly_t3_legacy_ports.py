from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
NIGHTLY_V2 = WORKFLOWS_DIR / "nightly-v2.yml"
SWEEP = WORKFLOWS_DIR / "green-unmerged-sweep.yml"
RUNBOOK = REPO_ROOT / "docs" / "internal" / "nightly-t3.md"
MAKEFILE = REPO_ROOT / "Makefile"
SMOKE_MARKERS = "platform_smoke or (integration and fast)"
LEGACY_WORKFLOWS = ("nightly.yml", "cross-platform-validation.yml")


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _jobs() -> dict[str, Any]:
    return _load(NIGHTLY_V2)["jobs"]


def _run_text(job: dict[str, Any]) -> str:
    return "\n".join(str(step.get("run", "")) for step in job["steps"])


def _make_targets() -> set[str]:
    return set(re.findall(r"(?m)^([A-Za-z0-9_.-]+):", MAKEFILE.read_text(encoding="utf-8")))


def _diff_module() -> Any:
    path = REPO_ROOT / "scripts" / "cross_engine_diff_artifact.py"
    spec = importlib.util.spec_from_file_location("cross_engine_diff_artifact", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_security_domain_runs_an_existing_make_target() -> None:
    job = _jobs()["security"]
    assert job["name"].startswith("t3:security")
    assert "make security-audit" in _run_text(job)
    assert "security-audit" in _make_targets()


def test_integration_smoke_runs_the_make_target_marker_on_ubuntu_and_windows() -> None:
    job = _jobs()["integration-smoke"]
    legs = {entry["os"] for entry in job["strategy"]["matrix"]["include"]}
    assert legs == {"ubuntu-latest", "windows-latest"}
    assert job["strategy"]["fail-fast"] is False
    assert job["env"]["BENCHBOX_TEST_TIER"] == "t2"
    makefile = MAKEFILE.read_text(encoding="utf-8")
    assert f'-m "{SMOKE_MARKERS}"' in makefile.split("test-integration-smoke:", 1)[1].split(".PHONY", 1)[0]
    by_condition = {str(step.get("if")): str(step["run"]) for step in job["steps"] if "if" in step and "run" in step}
    assert by_condition["runner.os != 'Windows'"] == "make test-integration-smoke"
    assert f'-m "{SMOKE_MARKERS}"' in by_condition["runner.os == 'Windows'"]


def test_cross_engine_matrix_selects_existing_comparison_classes() -> None:
    job = _jobs()["cross-engine"]
    include = job["strategy"]["matrix"]["include"]
    assert {entry["comparison"] for entry in include} == {"datafusion", "polars-df", "clickhouse-local"}
    assert job["strategy"]["fail-fast"] is False
    source = (REPO_ROOT / "tests" / "integration" / "validation" / "test_cross_platform.py").read_text(encoding="utf-8")
    for entry in include:
        assert re.search(rf"(?m)^class {entry['test-class']}\b", source), entry


def test_cross_engine_failure_is_recorded_then_surfaced_after_the_artifact_upload() -> None:
    steps = _jobs()["cross-engine"]["steps"]
    names = [step.get("name", "") for step in steps]
    compare = next(step for step in steps if step.get("id") == "compare")
    assert compare["continue-on-error"] is True
    diff = next(step for step in steps if "diff artifact" in step.get("name", ""))
    upload = next(step for step in steps if str(step.get("uses", "")).startswith("actions/upload-artifact@"))
    fail = steps[-1]
    assert diff["if"] == fail["if"] == "steps.compare.outcome == 'failure'"
    assert upload["if"] == "always()"
    assert names.index(diff["name"]) < steps.index(upload) < steps.index(fail)
    assert upload["with"]["name"].startswith("t3-cross-engine-")


def test_diff_artifact_lists_only_failed_queries(tmp_path: Path) -> None:
    module = _diff_module()
    report = {
        "tests": [
            {"nodeid": "t.py::TestDuckDBDataFusion::test_query[Q1]", "outcome": "passed"},
            {
                "nodeid": "t.py::TestDuckDBDataFusion::test_query[Q6]",
                "outcome": "failed",
                "call": {"longrepr": "mismatch"},
            },
            {"nodeid": "t.py::TestDuckDBDataFusion::test_query[Q9]", "outcome": "failed"},
        ]
    }
    report_path = tmp_path / "report.json"
    output_path = tmp_path / "diff.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    status = module.main(["--report", str(report_path), "--output", str(output_path), "--comparison", "datafusion"])

    artifact = json.loads(output_path.read_text(encoding="utf-8"))
    assert status == 0
    assert artifact["comparison_platform"] == "datafusion"
    assert artifact["reference_platform"] == "duckdb"
    assert artifact["failed_queries"] == [
        {"query_id": "Q6", "longrepr": "mismatch"},
        {"query_id": "Q9", "longrepr": ""},
    ]


def test_diff_artifact_tolerates_a_missing_report(tmp_path: Path) -> None:
    module = _diff_module()
    output_path = tmp_path / "diff.json"

    status = module.main(
        ["--report", str(tmp_path / "absent.json"), "--output", str(output_path), "--comparison", "polars-df"]
    )

    assert status == 0
    assert not output_path.exists()


@pytest.mark.parametrize(
    "job", ["security", "cross-engine", "integration-smoke", "integration", "parity", "sqlglot-generator"]
)
def test_ported_domains_upload_their_artifacts_even_when_the_check_fails(job: str) -> None:
    uploads = [
        step for step in _jobs()[job]["steps"] if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    ]
    assert uploads
    assert all(step["with"]["name"].startswith(f"t3-{job}") for step in uploads)
    assert all("always()" in str(step["if"]) or "cancelled()" in str(step["if"]) for step in uploads)


def test_questdb_mirror_is_a_reported_domain_with_package_write() -> None:
    job = _jobs()["mirror-questdb"]
    assert job["uses"] == "./.github/workflows/mirror-ci-images.yml"
    assert json.loads(job["with"]["images"]) == ["questdb"]
    assert job["permissions"] == {"contents": "read", "packages": "write"}
    assert "mirror-questdb" in _jobs()["report"]["needs"]


def test_every_ported_domain_is_in_the_report_loop_and_runbook_table() -> None:
    report = _jobs()["report"]["steps"][0]["run"]
    loop = re.search(r"for job in ([^;]+); do", report)
    assert loop
    looped = set(loop.group(1).split())
    runbook = RUNBOOK.read_text(encoding="utf-8")
    for job in (
        "security",
        "cross-engine",
        "integration-smoke",
        "integration",
        "parity",
        "sqlglot-generator",
        "mirror-questdb",
    ):
        assert job in looped
        assert f"| `t3:{job}` | `{job}` |" in runbook


def test_sweep_workflow_runs_the_sweep_script_on_its_own_schedule() -> None:
    workflow = _load(SWEEP)
    triggers = workflow.get("on", workflow.get(True))
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    cron = triggers["schedule"][0]["cron"]
    others = set()
    for path in WORKFLOWS_DIR.glob("*.yml"):
        document = _load(path)
        other_triggers = document.get("on", document.get(True))
        if path != SWEEP and isinstance(other_triggers, dict):
            others.update(entry["cron"] for entry in other_triggers.get("schedule") or [])
    assert cron not in others
    job = workflow["jobs"]["sweep"]
    command = _run_text(job)
    assert "_project/scripts/green_unmerged_sweep.py --apply" in command
    assert job["permissions"] == {
        "contents": "read",
        "pull-requests": "read",
        "issues": "write",
        "checks": "read",
        "actions": "read",
    }
    env = next(step for step in job["steps"] if "green_unmerged_sweep" in str(step.get("run", "")))["env"]
    assert env == {"GITHUB_TOKEN": "${{ secrets.GITHUB_TOKEN }}"}


def test_runbook_records_a_disposition_for_every_legacy_job() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    section = text.split("## Legacy workflow retirement", 1)[1].split("\n## ", 1)[0]
    rows = {
        row.split("|")[1].strip(): row.split("|")[-2].strip() for row in section.splitlines() if row.startswith("| `")
    }
    for name in LEGACY_WORKFLOWS:
        for job in _load(WORKFLOWS_DIR / name)["jobs"]:
            matching = [disposition for label, disposition in rows.items() if f"`{job}`" in label]
            assert matching, f"{name} job {job} has no disposition"
            assert re.match(r"\*\*(Port|Keep|Drop)\*\*", matching[0]), (job, matching[0])
    for job in ("slow-tier-observation", "duckdb-preview", "pyspark-tests"):
        assert any(
            f"`{job}`" in label and "owner decision 2026-10-27" in disposition for label, disposition in rows.items()
        )
