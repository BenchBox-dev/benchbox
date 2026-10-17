from __future__ import annotations

import importlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "seed-corpus.yml"

SEED_TEST_SALT = "seed-test-salt"

RELEASE_HEAD_RE = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.+-]+)?$")


def _create_pr_steps() -> dict[str, str]:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["create-pr"]["steps"]
    return {step["name"]: step.get("run", "") for step in steps if "name" in step}


def _rendered_head_branch(commit_script: str) -> str:
    match = re.search(r'BRANCH="([^"]+)"', commit_script)
    assert match is not None, "could not find the BRANCH= assignment in the commit step"
    branch = match.group(1)
    branch = branch.replace("${{ github.run_number }}", "123")
    return branch.replace("${DATE}", "20260710")


def _workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    on = workflow.get("on", workflow.get(True))
    assert on is not None, "seed-corpus.yml has no `on:` block"
    return on


def test_seed_corpus_is_scheduled_monthly_and_still_dispatchable() -> None:
    on = _triggers(_workflow())
    crons = [entry["cron"] for entry in on["schedule"]]
    assert "0 7 1 * *" in crons
    assert "workflow_dispatch" in on


def test_schedule_runs_the_full_matrix() -> None:
    selected = _workflow()["jobs"]["generate-seed-corpus"]["env"]["SELECTED"]
    assert "github.event_name == 'schedule'" in selected


def test_supported_matrix_has_three_local_identities_per_cohort() -> None:
    entries = _workflow()["jobs"]["generate-seed-corpus"]["strategy"]["matrix"]["include"]
    cohorts: dict[tuple[str, str], set[str]] = {}
    cells = set()
    for entry in entries:
        assert "cloud" not in entry["platform"]
        assert entry["extras"] in {"duckdb", "datafusion", "polars", "clickhouse-local"}
        cohorts.setdefault((entry["benchmark"], entry["scale_factor"]), set()).add(entry["platform"])
        cells.add((entry["benchmark"], entry["platform"], entry["scale_factor"]))

    assert cohorts == {
        ("tpch", "0.01"): {"duckdb", "datafusion", "polars-df"},
        ("tpch", "0.1"): {"duckdb", "datafusion", "polars-df"},
        ("tpch", "1.0"): {"duckdb", "datafusion", "clickhouse-local"},
        ("tpcds", "1"): {"duckdb", "datafusion", "clickhouse-local"},
        ("ssb", "0.01"): {"duckdb", "datafusion", "polars-df"},
        ("ssb", "0.1"): {"duckdb", "datafusion", "polars-df"},
    }
    assert all(len(platforms) == 3 for platforms in cohorts.values())
    assert len(entries) == len(cells) == 18


def _stage_bundle(
    tmp_path: Path,
    payload: dict[str, Any],
    companions: tuple[str, ...] = (),
    salt: str | None = SEED_TEST_SALT,
    companion_payloads: dict[str, Any] | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    result_file = tmp_path / "bundle.json"
    result_file.write_text(json.dumps(payload), encoding="utf-8")
    for suffix in companions:
        content = (companion_payloads or {}).get(suffix, {})
        Path(f"{result_file.with_suffix('')}{suffix}").write_text(json.dumps(content), encoding="utf-8")
    destination = tmp_path / "staged"
    output_file = tmp_path / "github-output"
    environment = {key: value for key, value in os.environ.items() if key != "BENCHBOX_MACHINE_ID_SALT"} | {
        "GITHUB_OUTPUT": str(output_file),
        "SEED_CELL": "ssb / duckdb / sf0.01",
        "SEED_STATUS_FILE": str(tmp_path / "seed-status.txt"),
    }
    if salt is not None:
        environment["BENCHBOX_MACHINE_ID_SALT"] = salt
    process = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "stage_seed_corpus_bundle.py"),
            str(result_file),
            str(destination),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    return process, destination


def test_seed_workflow_never_stages_uncertain_bundles(tmp_path: Path) -> None:
    uncertain_payloads = [
        {"summary": {"validation": "uncertain"}},
        {"summary": {"validation": "passed"}, "execution": {"translation": "fallback"}, "queries": []},
    ]
    for index, payload in enumerate(uncertain_payloads):
        process, destination = _stage_bundle(tmp_path / str(index), payload)
        assert process.returncode == 0, process.stderr
        assert "eligible=false" in (tmp_path / str(index) / "github-output").read_text(encoding="utf-8")
        assert "validation_not_clean" in (tmp_path / str(index) / "seed-status.txt").read_text(encoding="utf-8")
        assert not destination.exists()

    workflow = _workflow()
    steps = workflow["jobs"]["generate-seed-corpus"]["steps"]
    stage = next(step for step in steps if step.get("name") == "Stage bundle under results-data/")
    upload = next(step for step in steps if step.get("name") == "Upload bundle artifact")
    assert "scripts/stage_seed_corpus_bundle.py" in stage["run"]
    assert upload["if"] == "env.SELECTED == 'true' && steps.stage.outputs.eligible == 'true'"


def _clean_payload() -> dict[str, Any]:
    return {
        "benchmark": {"id": "ssb"},
        "summary": {"validation": "passed", "queries": {"total": 2}},
        "execution": {"translation": "success"},
        "environment": {"hostname": "seed-runner.internal"},
        "queries": [{"id": "1", "ms": 12.0}, {"id": "2", "ms": 15.0}],
        "export": {"tool": "benchbox", "anonymized": False},
    }


def test_seed_workflow_stages_clean_bundles_anonymized(tmp_path: Path) -> None:
    process, destination = _stage_bundle(tmp_path, _clean_payload(), (".plans.json", ".tuning.json"))

    assert process.returncode == 0, process.stderr
    assert "eligible=true" in (tmp_path / "github-output").read_text(encoding="utf-8")
    assert "eligible" in (tmp_path / "seed-status.txt").read_text(encoding="utf-8")
    staged = json.loads((destination / "bundle.json").read_text(encoding="utf-8"))
    assert staged["export"] == {"tool": "benchbox", "anonymized": True}
    assert staged["environment"]["hostname"] != "seed-runner.internal"
    assert staged["queries"] == _clean_payload()["queries"]
    assert (destination / "bundle.plans.json").is_file()
    assert (destination / "bundle.tuning.json").is_file()

    sys.path.insert(0, str(REPO_ROOT / "results-data"))
    try:
        validate_corpus = importlib.import_module("validate_corpus")
    finally:
        sys.path.remove(str(REPO_ROOT / "results-data"))
    assert validate_corpus.unanonymized_bundle_errors([destination / "bundle.json"]) == []


def _tuned_clickhouse_local_payload() -> dict[str, Any]:
    from benchbox.core.tuning.applied_ledger import PHASE_DDL, AppliedTuningLedger

    ledger = AppliedTuningLedger()
    for table in ("store_sales", "web_sales"):
        ledger.record(
            f"CREATE TABLE {table} (ss_item_sk Int64, ss_sold_date_sk Int64) "
            "ENGINE = MergeTree() ORDER BY (ss_sold_date_sk, ss_item_sk)",
            PHASE_DDL,
        )
    payload = _clean_payload()
    payload["platform"] = {
        "name": "clickhouse-local",
        "tuning": {"applied": ledger.to_payload(status="applied_unverified")},
    }
    return payload


def test_seed_staging_redacts_applied_tuning_statements(tmp_path: Path) -> None:
    payload = _tuned_clickhouse_local_payload()
    assert all("statement" in entry for entry in payload["platform"]["tuning"]["applied"]["statements"])

    process, destination = _stage_bundle(tmp_path, payload)

    assert process.returncode == 0, process.stderr
    staged_text = (destination / "bundle.json").read_text(encoding="utf-8")
    assert "MergeTree" not in staged_text
    statements = json.loads(staged_text)["platform"]["tuning"]["applied"]["statements"]
    assert len(statements) == 2
    assert all(entry["statement_redacted"] is True and "statement" not in entry for entry in statements)

    sys.path.insert(0, str(REPO_ROOT / "results-data"))
    try:
        validate_corpus = importlib.import_module("validate_corpus")
    finally:
        sys.path.remove(str(REPO_ROOT / "results-data"))
    assert validate_corpus.unanonymized_bundle_errors([destination / "bundle.json"]) == []


def test_seed_pseudonyms_follow_the_salt(tmp_path: Path) -> None:
    first, first_destination = _stage_bundle(tmp_path / "first", _clean_payload())
    again, again_destination = _stage_bundle(tmp_path / "again", _clean_payload())
    other, other_destination = _stage_bundle(tmp_path / "other", _clean_payload(), salt="another-salt")
    assert first.returncode == again.returncode == other.returncode == 0

    def hostname(destination: Path) -> str:
        return json.loads((destination / "bundle.json").read_text(encoding="utf-8"))["environment"]["hostname"]

    assert hostname(first_destination) == hostname(again_destination)
    assert hostname(first_destination) != hostname(other_destination)


def test_seed_staging_fails_closed_without_the_salt(tmp_path: Path) -> None:
    process, destination = _stage_bundle(tmp_path, _clean_payload(), salt=None)

    assert process.returncode != 0
    assert "BENCHBOX_MACHINE_ID_SALT" in process.stderr
    assert (tmp_path / "github-output").read_text(encoding="utf-8") == "eligible=false\n"
    assert "\tstaging_failed: " in (tmp_path / "seed-status.txt").read_text(encoding="utf-8")
    assert not destination.exists()


def test_seed_staging_anonymizes_plans_companions(tmp_path: Path) -> None:
    plans = {"queries": {"1": {"plan": {"source": "/home/runner/work/data/lineitem.parquet"}}}}
    process, destination = _stage_bundle(
        tmp_path, _clean_payload(), (".plans.json",), companion_payloads={".plans.json": plans}
    )

    assert process.returncode == 0, process.stderr
    staged = (destination / "bundle.plans.json").read_text(encoding="utf-8")
    assert "/home/runner" not in staged
    assert "lineitem.parquet" not in staged


def test_seed_staging_refuses_plans_with_raw_explain_output(tmp_path: Path) -> None:
    plans = {"queries": {"1": {"plan": {"raw_explain_output": "EXPLAIN text"}}}}
    process, destination = _stage_bundle(
        tmp_path, _clean_payload(), (".plans.json",), companion_payloads={".plans.json": plans}
    )

    assert process.returncode != 0
    assert "raw_explain_output" in process.stderr
    assert "eligible=true" not in (tmp_path / "github-output").read_text(encoding="utf-8")
    assert "\tstaging_failed: " in (tmp_path / "seed-status.txt").read_text(encoding="utf-8")
    assert not destination.exists()


def test_seed_workflow_reports_every_failed_cell() -> None:
    job = _workflow()["jobs"]["generate-seed-corpus"]
    steps = {step.get("name"): step for step in job["steps"]}
    record = steps["Record a failed cell"]
    upload = steps["Upload cell status"]

    assert record["if"] == "failure() && env.SELECTED == 'true'"
    assert "job_failed" in record["run"]
    assert '[ ! -f "$SEED_STATUS_FILE" ]' in record["run"]
    assert upload["if"] == "${{ !cancelled() && env.SELECTED == 'true' }}"
    assert "SEED_STATUS_FILE" in job["env"]
    assert "SEED_CELL" in job["env"]


def test_seed_staging_runs_in_the_project_environment_with_the_salt_secret() -> None:
    steps = _workflow()["jobs"]["generate-seed-corpus"]["steps"]
    stage = next(step for step in steps if step.get("name") == "Stage bundle under results-data/")

    assert "uv run -- python scripts/stage_seed_corpus_bundle.py" in stage["run"]
    assert stage["env"]["BENCHBOX_MACHINE_ID_SALT"] == "${{ secrets.BENCHBOX_MACHINE_ID_SALT }}"


@pytest.mark.parametrize(
    "payload, reason",
    [
        ({"summary": {"validation": "failed"}}, "validation_not_clean"),
        ({"summary": {"validation": "passed"}, "queries": []}, "no_queries"),
        ({"summary": {"validation": "passed"}, "queries": [{"id": "1", "ms": 1.0}]}, "insufficient_valid_queries"),
    ],
)
def test_seed_workflow_skips_unrankable_bundles(tmp_path: Path, payload: dict[str, Any], reason: str) -> None:
    process, destination = _stage_bundle(tmp_path, payload)

    assert process.returncode == 0, process.stderr
    assert "eligible=false" in (tmp_path / "github-output").read_text(encoding="utf-8")
    assert reason in (tmp_path / "seed-status.txt").read_text(encoding="utf-8")
    assert not destination.exists()


def test_seed_corpus_local_jobs_do_not_reference_cloud_credentials() -> None:
    workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8").lower()
    assert "clickhouse-cloud" not in workflow_text
    assert "clickhouse_cloud" not in workflow_text
    assert "cloud_host" not in workflow_text
    assert "cloud_password" not in workflow_text
    assert "matrix.optional" not in workflow_text


def test_tpc_matrix_jobs_run_official_seeded_at_every_official_scale_point() -> None:
    from benchbox.core.tpcds.compliance import OFFICIAL_SCALE_POINTS as TPCDS_OFFICIAL_SCALE_POINTS
    from benchbox.core.tpch.compliance import OFFICIAL_SCALE_POINTS as TPCH_OFFICIAL_SCALE_POINTS

    official_points = {"tpch": TPCH_OFFICIAL_SCALE_POINTS, "tpcds": TPCDS_OFFICIAL_SCALE_POINTS}
    workflow = _workflow()
    entries = workflow["jobs"]["generate-seed-corpus"]["strategy"]["matrix"]["include"]
    official_cells = set()
    for entry in entries:
        at_official_point = float(entry["scale_factor"]) in official_points.get(entry["benchmark"], frozenset())
        assert entry.get("official", False) is at_official_point, entry
        if at_official_point:
            official_cells.add((entry["benchmark"], entry["scale_factor"]))
    assert official_cells == {("tpch", "1.0"), ("tpcds", "1")}

    run_step = next(
        step["run"] for step in workflow["jobs"]["generate-seed-corpus"]["steps"] if step.get("name") == "Run benchmark"
    )
    assert 'if [ "${{ matrix.official }}" = "true" ]; then' in run_step
    assert "command+=(--official --seed 42)" in run_step
    assert '"${command[@]}"' in run_step


def test_seed_corpus_pr_targets_develop() -> None:
    run = _create_pr_steps()["Open pull request"]

    assert "--base develop" in run
    assert "--base main" not in run
    assert "--base release" not in run


def test_seed_corpus_pr_regenerates_and_stages_inventory() -> None:
    steps = _workflow()["jobs"]["create-pr"]["steps"]
    names = [step.get("name") for step in steps]
    regenerate_index = names.index("Regenerate corpus inventory")
    stage_index = names.index("Check for new corpus changes")

    assert regenerate_index < stage_index
    assert "scripts/generate_corpus_inventory.py --write" in steps[regenerate_index]["run"]
    stage_script = steps[stage_index]["run"]
    assert "results-data/bundles/" in stage_script
    assert "results-data/corpus-inventory.json" in stage_script


def test_seed_corpus_head_branch_can_never_target_the_release_branch() -> None:
    steps = _create_pr_steps()
    head = _rendered_head_branch(steps["Create PR branch and commit"])

    assert head.startswith("chore/seed-corpus-")
    assert not RELEASE_HEAD_RE.match(head)


def test_seed_corpus_pr_body_does_not_escape_backticks() -> None:
    run = _create_pr_steps()["Open pull request"]
    body = run.split("<<'EOF'", 1)[1].split("\nEOF", 1)[0]

    assert "`" in body, "sanity: the body is expected to contain backticks"
    assert "\\`" not in body
    assert "--body-file" in run
    assert "seed-status" in run


def test_release_head_regex_matches_real_release_branches() -> None:
    assert RELEASE_HEAD_RE.match("v0.3.1")
    assert RELEASE_HEAD_RE.match("v1.2.3-rc.1")
    assert not RELEASE_HEAD_RE.match("chore/seed-corpus-20260710-123")


def _create_pr_step_names() -> list[str]:
    steps = _workflow()["jobs"]["create-pr"]["steps"]
    return [step["name"] for step in steps if "name" in step]


def test_ledger_seed_is_recut_and_gated_after_corpus_validation() -> None:
    names = _create_pr_step_names()
    steps = _create_pr_steps()

    validate = names.index("Validate corpus before opening a PR")
    recut = names.index("Re-cut the publication ledger seed")
    gates = names.index("Run the publication gate suites")
    check = names.index("Check for new corpus changes")
    assert validate < recut < gates < check
    assert "git fetch --no-tags origin published-results:refs/remotes/origin/published-results" in steps[names[recut]]
    assert "scripts/publication/create_ledger_seed.py --accepted-ref origin/published-results" in steps[names[recut]]
    assert steps[names[gates]].strip() == "make ledger-seed-gates"


def test_ledger_seed_is_committed_only_with_corpus_changes() -> None:
    check = _create_pr_steps()["Check for new corpus changes"]

    before, _, after = check.partition("else")
    assert "publication/ledger-seed.json" not in before
    assert "git add publication/ledger-seed.json" in after


def test_ledger_seed_gate_target_runs_the_publication_suites() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

    for suite in (
        "tests/unit/scripts/publication/test_ledger_seed.py",
        "tests/unit/scripts/publication/test_check_corpus_bijection.py",
        "tests/unit/scripts/test_site_inputs.py",
    ):
        assert suite in makefile
        assert (REPO_ROOT / suite).is_file()
    assert re.search(r"^ledger-seed-recut:\n(?:\t.*\n)*\t\$\(MAKE\) ledger-seed-gates$", makefile, re.MULTILINE)


def test_seed_pr_body_tells_the_owner_to_merge_the_mirror_and_recut() -> None:
    body = _create_pr_steps()["Open pull request"]

    assert "The owner merges that mirror PR in the same cycle." in body
    assert "make ledger-seed-recut" in body
