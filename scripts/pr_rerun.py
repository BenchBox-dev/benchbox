from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import oracle_review_recover as recover

from _project.scripts import check_suites

CLI_DESCRIPTION = "Re-run the CI workflow for a pull request's current head after a failure not caused by the change."

WORKFLOW_PATH = ".github/workflows/ci.yml"
PATHS_JOB = "ci-paths"
BASE_BRANCH = "develop"
RUN_FILTER = (
    ".workflow_runs[] | {id, path, event, head_branch, head_sha,"
    " head_repository: .head_repository.full_name, status, conclusion, created_at}"
)
Runner = recover.Runner
RecoveryError = recover.RecoveryError


def _time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise RecoveryError("GitHub returned a record without a timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RecoveryError(f"GitHub returned an invalid timestamp {value!r}") from exc


def _lines(runner: Runner, arguments: list[str]) -> list[Any]:
    result = recover._invoke(runner, arguments)
    try:
        return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    except ValueError as exc:
        raise RecoveryError(f"gh {arguments[0]} returned invalid JSON") from exc


def _current_branch_pr(runner: Runner, repo: str) -> int:
    branch = runner(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    name = branch.stdout.strip()
    if branch.returncode != 0 or not name or name == "HEAD":
        raise RecoveryError("cannot resolve the current branch; pass PR=<number>")
    found = recover._json(
        runner,
        ["pr", "list", "--repo", repo, "--head", name, "--base", "develop", "--state", "open", "--json", "number"],
    )
    numbers = [item.get("number") for item in found if isinstance(item, dict)] if isinstance(found, list) else []
    if len(numbers) != 1 or not isinstance(numbers[0], int):
        raise RecoveryError(f"expected one open PR for {name!r}, found {len(numbers)}; pass PR=<number>")
    return numbers[0]


def _shared_head(runner: Runner, repo: str, pr: int, branch: str, head_repository: str) -> list[int]:
    found = recover._json(
        runner,
        ["pr", "list", "--repo", repo, "--head", branch, "--state", "open", "--json", "number,headRepositoryOwner"],
    )
    if not isinstance(found, list):
        raise RecoveryError("gh pr list returned an invalid list")
    owner = head_repository.split("/", 1)[0].casefold()
    return sorted(
        item["number"]
        for item in found
        if isinstance(item, dict)
        and item.get("number") != pr
        and str((item.get("headRepositoryOwner") or {}).get("login", "")).casefold() == owner
    )


def _latest_retarget(runner: Runner, repo: str, pr: int) -> datetime | None:
    dates = _lines(
        runner,
        [
            "api",
            "--paginate",
            "--jq",
            '.[] | select(.event == "base_ref_changed") | {created_at}',
            f"repos/{repo}/issues/{pr}/timeline?per_page=100",
        ],
    )
    times = [_time(item.get("created_at")) for item in dates if isinstance(item, dict)]
    return max(times) if times else None


def _runs(runner: Runner, repo: str, head: str, branch: str, head_repository: str) -> list[dict[str, Any]]:
    endpoint = f"repos/{repo}/actions/workflows/ci.yml/runs?event=pull_request&head_sha={head}&per_page=100"
    runs = [
        run
        for run in _lines(runner, ["api", "--paginate", "--jq", RUN_FILTER, endpoint])
        if isinstance(run, dict)
        and run.get("path") == WORKFLOW_PATH
        and run.get("event") == "pull_request"
        and run.get("head_sha") == head
        and run.get("head_branch") == branch
        and run.get("head_repository") == head_repository
    ]
    return sorted(runs, key=lambda run: _time(run.get("created_at")), reverse=True)


def _jobs(runner: Runner, repo: str, run_id: int) -> list[dict[str, Any]]:
    endpoint = f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100"
    jobs = _lines(runner, ["api", "--paginate", "--jq", ".jobs[] | {name, status, conclusion}", endpoint])
    return [job for job in jobs if isinstance(job, dict)]


def is_text_edit_run(jobs: Sequence[Mapping[str, Any]]) -> bool:
    return any(job.get("name") == PATHS_JOB and job.get("conclusion") == "skipped" for job in jobs)


def rerun_arguments(run: Mapping[str, Any], jobs: Sequence[Mapping[str, Any]], repo: str) -> list[str] | None:
    status = run.get("status")
    conclusion = run.get("conclusion")
    run_id = str(run.get("id"))
    if status != "completed":
        raise RecoveryError(f"CI is still running on {str(run.get('head_sha'))[:9]} (run {run_id}); wait for it")
    if conclusion == "success":
        return None
    if conclusion == "cancelled" or any(job.get("conclusion") == "cancelled" for job in jobs):
        return ["run", "rerun", run_id, "--repo", repo]
    if conclusion == "failure":
        return ["run", "rerun", run_id, "--repo", repo, "--failed"]
    raise RecoveryError(f"CI run {run_id} ended {conclusion!r}; re-running it is not supported here")


def _required_contexts(runner: Runner, repo: str, base: str) -> tuple[str, ...]:
    result = recover._invoke(
        runner,
        [
            "api",
            "--jq",
            check_suites.REQUIRED_CONTEXTS_JQ,
            check_suites.required_contexts_endpoint(repo, base),
        ],
    )
    try:
        return check_suites.parse_required_contexts(result.stdout)
    except ValueError as exc:
        raise RecoveryError(str(exc)) from exc


def _suite_reruns(runner: Runner, repo: str, head: str, base: str) -> list[check_suites.SuiteRerun]:
    result = recover._invoke(
        runner,
        [
            "api",
            "--paginate",
            "--jq",
            check_suites.LATEST_JQ,
            check_suites.latest_check_runs_endpoint(repo, head),
        ],
    )
    try:
        records = check_suites.parse_check_run_lines(result.stdout)
        evaluations = check_suites.evaluate(records, _required_contexts(runner, repo, base), check_suites.NON_BLOCKING)
        return check_suites.plan_reruns(evaluations)
    except (ValueError, check_suites.Unrerunnable) as exc:
        raise RecoveryError(str(exc)) from exc


def _check_identity(runner: Runner, repo: str, pr: int, created: datetime, branch: str, head_repository: str) -> None:
    shared = _shared_head(runner, repo, pr, branch, head_repository)
    if shared:
        listed = ", ".join(f"#{number}" for number in shared)
        raise RecoveryError(f"other open PRs share this head ({listed}); their CI runs cannot be told apart")
    retarget = _latest_retarget(runner, repo, pr)
    if retarget is not None and retarget >= created:
        raise RecoveryError(
            "the PR was retargeted at or after its latest CI run, so a re-run would test the old base;"
            " push a commit, or close and reopen the PR"
        )


def _newest_ci_run(
    runner: Runner, repo: str, head: str, branch: str, head_repository: str
) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    for run in _runs(runner, repo, head, branch, head_repository):
        jobs = _jobs(runner, repo, int(run["id"]))
        if not is_text_edit_run(jobs):
            return run, jobs
    return None


def rerun(repo: str, pr: int, runner: Runner = recover._subprocess_runner) -> str:
    if not recover.REPOSITORY_RE.fullmatch(repo):
        raise RecoveryError("repository must be a GitHub owner/name")
    head, branch, head_repository = recover._pull_request_snapshot(runner, repo, pr)
    chosen = _newest_ci_run(runner, repo, head, branch, head_repository)
    suite_reruns = _suite_reruns(runner, repo, head, BASE_BRANCH)
    commands: list[tuple[list[str], str]] = []
    created: list[datetime] = []
    if chosen is not None:
        run, jobs = chosen
        created.append(_time(run.get("created_at")))
        arguments = rerun_arguments(run, jobs, repo)
        if arguments is not None:
            scope = "failed jobs of" if "--failed" in arguments else "all jobs of"
            commands.append((arguments, f"{scope} CI run {run['id']}"))
    covered = {arguments[2] for arguments, _ in commands}
    for item in suite_reruns:
        if str(item.run_id) in covered:
            continue
        created.extend(_time(suite.started_at) for suite in item.suites)
        names = ", ".join(sorted({suite.name for suite in item.suites}))
        scope = "all jobs of" if item.scope == "all" else "failed jobs of"
        commands.append((item.arguments(repo), f"{scope} run {item.run_id} (stale suite: {names})"))
    if created:
        _check_identity(runner, repo, pr, max(created), branch, head_repository)
    if not commands:
        if chosen is None:
            raise RecoveryError(f"no CI run found for {head[:9]}; push a commit, or close and reopen the PR")
        return f"CI run {chosen[0]['id']} on {head[:9]} succeeded; nothing to re-run."
    latest = max(created)
    current = recover._read_pull_request(runner, repo, pr)
    recover._validate_pull_request(current, repo, pr, expected_head=head, expected_branch=branch)
    _check_identity(runner, repo, pr, latest, branch, head_repository)
    for arguments, _ in commands:
        recover._invoke(runner, arguments)
    return "; ".join(f"Re-running {label} on {head[:9]}" for _, label in commands) + "."


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] = os.environ) -> int:
    if argv:
        print("usage: make pr-rerun [PR=<number>] [REPO=<owner/name>]", file=sys.stderr)
        return 2
    repo = environ.get("PR_RERUN_REPO") or "BenchBox-dev/BenchBox"
    pr_text = environ.get("PR_RERUN_PR", "")
    try:
        if pr_text:
            if not re.fullmatch(r"[1-9][0-9]{0,8}", pr_text):
                print("PR must be a positive number", file=sys.stderr)
                return 2
            pr = int(pr_text)
        else:
            pr = _current_branch_pr(recover._subprocess_runner, repo)
        print(rerun(repo, pr))
        return 0
    except RecoveryError as exc:
        print(f"pr-rerun refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
