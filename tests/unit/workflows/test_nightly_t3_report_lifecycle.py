from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.utilities.posix_shell import run_posix_shell, skip_without_posix_shell

pytestmark = [pytest.mark.unit, pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "nightly-v2.yml"
RUNBOOK = REPO_ROOT / "docs" / "internal" / "nightly-t3.md"

DOMAIN_JOBS = [
    "docker",
    "cloud",
    "perf",
    "matrix",
    "extension",
    "install",
    "throughput",
    "throughput-cedardb",
    "drift",
    "quarantine",
    "linkcheck",
    "liveness",
    "ruleset-drift",
    "windows",
    "durations-refresh",
]
SUPPORT_JOBS = ["mirror-images", "mirror-cedardb"]

GH_STUB = (
    f"#!{sys.executable}\n"
    "import json, os, sys\n"
    "from pathlib import Path\n"
    "args = sys.argv[1:]\n"
    "with Path(os.environ['GH_CALLS']).open('a') as output:\n"
    "    output.write(json.dumps(args) + '\\n')\n"
    "state = json.loads(os.environ['GH_STATE'])\n"
    "label = args[args.index('--label') + 1] if '--label' in args else None\n"
    "if args[:2] == ['issue', 'list'] and label == state['label']:\n"
    "    if args[args.index('--state') + 1] == 'open':\n"
    "        if state.get('open'):\n"
    "            print(state['open'])\n"
    "    else:\n"
    "        print(json.dumps(state.get('closed', [])))\n"
    "elif args[:2] == ['issue', 'view']:\n"
    "    print(state.get('recorded_comment', ''))\n"
)


def _report_script() -> str:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return workflow["jobs"]["report"]["steps"][0]["run"]


def _closed_days_ago(number: int, days: float) -> dict[str, Any]:
    closed = datetime.now(timezone.utc) - timedelta(days=days)
    return {"number": number, "closedAt": closed.strftime("%Y-%m-%dT%H:%M:%SZ")}


def _signature_comment(signature: str) -> str:
    return f"Nightly T3 run 1 failed.\n<!-- t3-signature: {signature} -->"


def _run_report(
    tmp_path: Path,
    results: dict[str, str],
    *,
    label: str,
    open_issue: int | None = None,
    closed: list[dict[str, Any]] | None = None,
    recorded_comment: str = "",
) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
    skip_without_posix_shell()
    if shutil.which("jq") is None:
        pytest.skip("report execution requires jq")
    gh = tmp_path / "gh"
    gh.write_text(GH_STUB, encoding="utf-8")
    gh.chmod(0o755)
    needs = {job: {"result": "skipped"} for job in DOMAIN_JOBS + SUPPORT_JOBS}
    for job, result in results.items():
        needs[job] = {"result": result}
    calls_path = tmp_path / "gh-calls.jsonl"
    state = {"label": label, "open": open_issue, "closed": closed or [], "recorded_comment": recorded_comment}
    result = run_posix_shell(
        'set -eo pipefail\nexport PATH="$PWD:$PATH"\n' + _report_script(),
        cwd=tmp_path,
        env={
            **os.environ,
            "NEEDS_JSON": json.dumps(needs),
            "GH_CALLS": str(calls_path),
            "GH_STATE": json.dumps(state),
            "GITHUB_REPOSITORY": "example/repo",
            "GITHUB_RUN_ID": "123",
            "GITHUB_SHA": "abc",
            "RUN_URL": "https://example.test/run/123",
            "DOC_URL": "https://example.test/docs",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    calls = (
        [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()]
        if calls_path.exists()
        else []
    )
    return result, calls


MUTATING = {("issue", "create"), ("issue", "comment"), ("issue", "close"), ("issue", "reopen"), ("label", "create")}


def _mutations(calls: list[list[str]]) -> list[list[str]]:
    return [args for args in calls if tuple(args[:2]) in MUTATING and args[:2] != ["label", "create"]]


def _body(args: list[str]) -> str:
    return args[args.index("--body") + 1]


def test_report_permissions_are_unchanged_and_no_label_is_created_beyond_the_existing_call() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    report = workflow["jobs"]["report"]
    assert report["permissions"] == {"contents": "read", "issues": "write"}
    issue_writers = {name for name, job in workflow["jobs"].items() if (job.get("permissions") or {}).get("issues")}
    assert issue_writers == {"report"}
    assert set(report["env"]) == {"GH_TOKEN", "NEEDS_JSON", "RUN_URL", "DOC_URL"}
    assert _report_script().count("gh label create") == 1


def test_failure_without_an_issue_creates_one(tmp_path: Path) -> None:
    _, calls = _run_report(tmp_path, {"perf": "failure"}, label="t3:perf")
    creates = [args for args in calls if args[:2] == ["issue", "create"]]
    assert len(creates) == 1
    assert "t3:perf" in creates[0]
    assert "<!-- t3-signature: perf -->" in _body(creates[0])
    assert [args[:2] for args in _mutations(calls)] == [["issue", "create"]]


def test_failure_after_a_recent_close_reopens_and_comments(tmp_path: Path) -> None:
    _, calls = _run_report(
        tmp_path,
        {"perf": "failure"},
        label="t3:perf",
        closed=[_closed_days_ago(11, 30), _closed_days_ago(12, 3)],
    )
    mutations = _mutations(calls)
    assert [args[:3] for args in mutations] == [["issue", "reopen", "12"], ["issue", "comment", "12"]]
    assert "<!-- t3-signature: perf -->" in _body(mutations[1])
    assert not any(args[:2] == ["issue", "create"] for args in calls)


def test_failure_after_an_old_close_creates_a_new_issue(tmp_path: Path) -> None:
    _, calls = _run_report(tmp_path, {"perf": "failure"}, label="t3:perf", closed=[_closed_days_ago(12, 20)])
    assert [args[:2] for args in _mutations(calls)] == [["issue", "create"]]


def test_failure_on_open_issue_with_same_signature_adds_no_comment(tmp_path: Path) -> None:
    _, calls = _run_report(
        tmp_path,
        {"perf": "failure"},
        label="t3:perf",
        open_issue=42,
        recorded_comment=_signature_comment("perf"),
    )
    assert _mutations(calls) == []


def test_failure_on_open_issue_with_new_signature_comments(tmp_path: Path) -> None:
    _, calls = _run_report(
        tmp_path,
        {"docker": "failure", "mirror-images": "failure"},
        label="t3:docker",
        open_issue=42,
        recorded_comment=_signature_comment("docker"),
    )
    mutations = _mutations(calls)
    assert [args[:3] for args in mutations] == [["issue", "comment", "42"]]
    assert "<!-- t3-signature: docker,mirror-images -->" in _body(mutations[0])


def test_failure_on_open_issue_without_a_recorded_signature_comments(tmp_path: Path) -> None:
    _, calls = _run_report(tmp_path, {"perf": "failure"}, label="t3:perf", open_issue=42)
    assert [args[:3] for args in _mutations(calls)] == [["issue", "comment", "42"]]


def test_success_on_open_issue_comments_and_closes(tmp_path: Path) -> None:
    _, calls = _run_report(tmp_path, {"perf": "success"}, label="t3:perf", open_issue=42)
    mutations = _mutations(calls)
    assert [args[:3] for args in mutations] == [["issue", "comment", "42"], ["issue", "close", "42"]]
    assert "passed" in _body(mutations[0])


def test_success_without_an_issue_changes_nothing(tmp_path: Path) -> None:
    _, calls = _run_report(tmp_path, {"perf": "success"}, label="t3:perf")
    assert _mutations(calls) == []


def test_cancelled_on_open_issue_comments_without_closing(tmp_path: Path) -> None:
    _, calls = _run_report(tmp_path, {"windows": "cancelled"}, label="t3:windows", open_issue=42)
    mutations = _mutations(calls)
    assert [args[:3] for args in mutations] == [["issue", "comment", "42"]]
    assert "no verdict (cancelled or timed out)" in _body(mutations[0])


def test_cancelled_without_an_issue_changes_nothing(tmp_path: Path) -> None:
    _, calls = _run_report(tmp_path, {"windows": "cancelled"}, label="t3:windows", closed=[_closed_days_ago(5, 1)])
    assert _mutations(calls) == []


def test_skipped_changes_nothing_even_with_an_open_issue(tmp_path: Path) -> None:
    _, calls = _run_report(tmp_path, {"cloud": "skipped"}, label="t3:cloud", open_issue=42)
    assert _mutations(calls) == []


def test_cancelled_leg_never_masks_a_failure_or_closes(tmp_path: Path) -> None:
    _, calls = _run_report(
        tmp_path,
        {"docker": "success", "mirror-images": "cancelled"},
        label="t3:docker",
        open_issue=42,
    )
    assert [args[:3] for args in _mutations(calls)] == [["issue", "comment", "42"]]


def test_replay_of_the_durations_timeline_reopens_instead_of_creating(tmp_path: Path) -> None:
    label = "t3:durations"
    (tmp_path / "green").mkdir()
    _, green = _run_report(tmp_path / "green", {"durations-refresh": "success"}, label=label, open_issue=2827)
    assert [args[:3] for args in _mutations(green)] == [["issue", "comment", "2827"], ["issue", "close", "2827"]]

    (tmp_path / "red").mkdir()
    closed_eight_hours_ago = {
        "number": 2827,
        "closedAt": (datetime.now(timezone.utc) - timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    _, red = _run_report(
        tmp_path / "red", {"durations-refresh": "failure"}, label=label, closed=[closed_eight_hours_ago]
    )
    mutations = _mutations(red)
    assert [args[:3] for args in mutations] == [["issue", "reopen", "2827"], ["issue", "comment", "2827"]]
    assert not any(args[:2] == ["issue", "create"] for args in red)


@pytest.mark.parametrize(
    "domain_job, mirror_job, domain_label",
    [("docker", "mirror-images", "t3:docker"), ("throughput-cedardb", "mirror-cedardb", "t3:throughput-cedardb")],
)
def test_signature_separates_mirror_legs_from_database_legs(
    tmp_path: Path, domain_job: str, mirror_job: str, domain_label: str
) -> None:
    mirror_only = tmp_path / "mirror"
    database_only = tmp_path / "database"
    both = tmp_path / "both"
    for path in (mirror_only, database_only, both):
        path.mkdir()
    _, mirror_calls = _run_report(mirror_only, {mirror_job: "failure", domain_job: "success"}, label=domain_label)
    _, database_calls = _run_report(database_only, {mirror_job: "success", domain_job: "failure"}, label=domain_label)
    _, both_calls = _run_report(both, {mirror_job: "failure", domain_job: "failure"}, label=domain_label)

    def marker(calls: list[list[str]]) -> str:
        return next(_body(args) for args in calls if args[:2] == ["issue", "create"]).splitlines()[-1]

    assert marker(mirror_calls) == f"<!-- t3-signature: {mirror_job} -->"
    assert marker(database_calls) == f"<!-- t3-signature: {domain_job} -->"
    assert marker(both_calls) == f"<!-- t3-signature: {','.join(sorted([domain_job, mirror_job]))} -->"
    assert len({marker(mirror_calls), marker(database_calls), marker(both_calls)}) == 3


def test_mirror_leg_failure_after_database_leg_failure_comments_on_open_issue(tmp_path: Path) -> None:
    _, calls = _run_report(
        tmp_path,
        {"docker": "failure", "mirror-images": "failure"},
        label="t3:docker",
        open_issue=42,
        recorded_comment=_signature_comment("mirror-images"),
    )
    assert [args[:3] for args in _mutations(calls)] == [["issue", "comment", "42"]]


def test_runbook_documents_the_lifecycle() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    issues = text.split("## Issues", 1)[1].split("## Owner response policy", 1)[0]
    for needle in ("14 days", "reopen", "signature", "no verdict", "skipped", "mirror"):
        assert needle in issues
    assert "## Owner response policy" in text
