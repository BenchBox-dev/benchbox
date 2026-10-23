from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from typing import Any, NamedTuple

CHECK_NAME = "oracle-review"
WORKFLOW_PATH = ".github/workflows/oracle-review.yml"
CHECK_RUN_FILTER = ".check_runs[] | {name, head_sha, status, conclusion, details_url}"
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$")
RUN_ID_RE = re.compile(r"/actions/runs/([1-9][0-9]*)(?:/|$)")
ALLOWED_EVENTS = {"pull_request", "pull_request_review", "workflow_dispatch"}
Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


class RecoveryError(RuntimeError):
    pass


class Recovery(NamedTuple):
    reruns: int
    reopened: bool


def _subprocess_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=False)


def _invoke(runner: Runner, arguments: list[str], *, allow_failure: bool = False) -> subprocess.CompletedProcess[str]:
    try:
        result = runner(["gh", *arguments])
    except OSError as exc:
        raise RecoveryError("could not execute the GitHub CLI") from exc
    if result.returncode != 0 and not allow_failure:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RecoveryError(f"gh {arguments[0]} failed: {detail}")
    return result


def _json(runner: Runner, arguments: list[str]) -> Any:
    result = _invoke(runner, arguments)
    try:
        return json.loads(result.stdout)
    except (TypeError, ValueError) as exc:
        raise RecoveryError(f"gh {arguments[0]} returned invalid JSON") from exc


def _read_pull_request(runner: Runner, repo: str, pr: int) -> dict[str, Any]:
    payload = _json(
        runner,
        [
            "pr",
            "view",
            str(pr),
            "--repo",
            repo,
            "--json",
            "number,state,baseRefName,headRefName,headRefOid,headRepository,isCrossRepository,autoMergeRequest",
        ],
    )
    if not isinstance(payload, dict):
        raise RecoveryError(f"PR #{pr} returned an invalid record")
    return payload


def _validate_pull_request(
    payload: dict[str, Any],
    repo: str,
    pr: int,
    *,
    expected_head: str | None = None,
    expected_branch: str | None = None,
    expected_state: str = "OPEN",
) -> tuple[str, str, str]:
    if payload.get("number") != pr or str(payload.get("state", "")).upper() != expected_state:
        raise RecoveryError(f"PR #{pr} is not {expected_state.lower()}")
    if payload.get("baseRefName") != "develop":
        raise RecoveryError(f"PR #{pr} does not target develop")
    head = payload.get("headRefOid")
    branch = payload.get("headRefName")
    head_repository = payload.get("headRepository")
    head_repository_name = head_repository.get("nameWithOwner") if isinstance(head_repository, dict) else None
    if not isinstance(head, str) or not FULL_SHA_RE.fullmatch(head):
        raise RecoveryError(f"PR #{pr} has an invalid head SHA")
    if not isinstance(branch, str) or not branch:
        raise RecoveryError(f"PR #{pr} has no head branch")
    if not isinstance(head_repository_name, str) or not head_repository_name:
        raise RecoveryError(f"PR #{pr} has no head repository")
    if expected_head is not None and head != expected_head:
        raise RecoveryError(f"PR #{pr} head changed during recovery")
    if expected_branch is not None and branch != expected_branch:
        raise RecoveryError(f"PR #{pr} head branch changed during recovery")
    cross_repository = payload.get("isCrossRepository")
    expected_cross_repository = head_repository_name.casefold() != repo.casefold()
    if not isinstance(cross_repository, bool) or cross_repository != expected_cross_repository:
        raise RecoveryError(f"PR #{pr} has inconsistent head repository metadata")
    return head, branch, head_repository_name


def _pull_request_snapshot(runner: Runner, repo: str, pr: int) -> tuple[str, str, str]:
    payload = _read_pull_request(runner, repo, pr)
    return _validate_pull_request(payload, repo, pr)


def _check_runs(runner: Runner, repo: str, head: str) -> list[dict[str, Any]]:
    endpoint = f"repos/{repo}/commits/{head}/check-runs?check_name={CHECK_NAME}&per_page=100"
    result = _invoke(runner, ["api", "--paginate", "--jq", CHECK_RUN_FILTER, endpoint])
    try:
        payload = [json.loads(line) for line in result.stdout.splitlines() if line]
    except (TypeError, ValueError) as exc:
        raise RecoveryError("GitHub returned invalid Oracle check-run data") from exc
    if any(not isinstance(item, dict) for item in payload):
        raise RecoveryError("GitHub returned an invalid Oracle check-run list")
    return payload


def _run_id(check_run: dict[str, Any]) -> int:
    details_url = check_run.get("details_url")
    match = RUN_ID_RE.search(details_url) if isinstance(details_url, str) else None
    if match is None:
        raise RecoveryError("an unsuccessful oracle-review check has no Actions run URL")
    return int(match.group(1))


def _read_run(runner: Runner, repo: str, run_id: int) -> dict[str, Any]:
    endpoint = f"repos/{repo}/actions/runs/{run_id}"
    payload = _json(
        runner,
        [
            "api",
            endpoint,
            "--jq",
            "{id, path, event, head_branch, head_sha, head_repository: .head_repository.full_name, status, conclusion}",
        ],
    )
    if not isinstance(payload, dict):
        raise RecoveryError(f"Actions run {run_id} returned an invalid record")
    return payload


def _validate_run(
    run: dict[str, Any],
    head: str,
    branch: str,
    head_repository: str,
    run_id: int,
) -> None:
    if run.get("id") != run_id or run.get("path") != WORKFLOW_PATH:
        raise RecoveryError(f"Actions run {run_id} is not from {WORKFLOW_PATH}")
    if run.get("head_sha") != head or run.get("head_branch") != branch:
        raise RecoveryError(f"Actions run {run_id} does not match the current PR head")
    if run.get("head_repository") != head_repository:
        raise RecoveryError(f"Actions run {run_id} does not match the current PR head repository")
    if str(run.get("event", "")) not in ALLOWED_EVENTS:
        raise RecoveryError(f"Actions run {run_id} has unexpected event {run.get('event')!r}")


def _watch_run(runner: Runner, repo: str, run_id: int) -> None:
    _invoke(runner, ["run", "watch", str(run_id), "--repo", repo, "--exit-status"], allow_failure=True)


def _process_run(
    runner: Runner,
    repo: str,
    pr: int,
    head: str,
    branch: str,
    head_repository: str,
    run_id: int,
) -> bool:
    run = _read_run(runner, repo, run_id)
    _validate_run(run, head, branch, head_repository, run_id)
    if run.get("status") != "completed":
        _watch_run(runner, repo, run_id)
        run = _read_run(runner, repo, run_id)
        _validate_run(run, head, branch, head_repository, run_id)
        if run.get("status") != "completed":
            raise RecoveryError(f"Actions run {run_id} is still incomplete")
    if run.get("conclusion") == "success":
        return False
    current = _read_pull_request(runner, repo, pr)
    _validate_pull_request(current, repo, pr, expected_head=head, expected_branch=branch)
    _invoke(runner, ["run", "rerun", str(run_id), "--repo", repo])
    _watch_run(runner, repo, run_id)
    run = _read_run(runner, repo, run_id)
    _validate_run(run, head, branch, head_repository, run_id)
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        raise RecoveryError(f"Actions run {run_id} did not pass after its serial rerun")
    return True


def _failed_run_ids(runner: Runner, repo: str, head: str) -> list[int]:
    run_ids: set[int] = set()
    for check_run in _check_runs(runner, repo, head):
        if check_run.get("name") != CHECK_NAME:
            continue
        if check_run.get("head_sha") != head:
            raise RecoveryError("GitHub returned an oracle-review check for a different head")
        if check_run.get("status") == "completed" and check_run.get("conclusion") == "success":
            continue
        run_ids.add(_run_id(check_run))
    return sorted(run_ids)


def _close_and_reopen(runner: Runner, repo: str, pr: int, head: str, branch: str) -> None:
    current = _read_pull_request(runner, repo, pr)
    _validate_pull_request(current, repo, pr, expected_head=head, expected_branch=branch)
    closed = True
    try:
        _invoke(runner, ["pr", "close", str(pr), "--repo", repo])
        current = _read_pull_request(runner, repo, pr)
        _validate_pull_request(
            current,
            repo,
            pr,
            expected_head=head,
            expected_branch=branch,
            expected_state="CLOSED",
        )
        _invoke(runner, ["pr", "reopen", str(pr), "--repo", repo])
        current = _read_pull_request(runner, repo, pr)
        _validate_pull_request(current, repo, pr, expected_head=head, expected_branch=branch)
        closed = False
    except RecoveryError:
        if closed:
            _invoke(runner, ["pr", "reopen", str(pr), "--repo", repo], allow_failure=True)
        raise


def recover(repo: str, pr: int, runner: Runner = _subprocess_runner) -> Recovery:
    if not REPOSITORY_RE.fullmatch(repo):
        raise RecoveryError("repository must be a GitHub owner/name")
    head, branch, head_repository = _pull_request_snapshot(runner, repo, pr)
    processed: set[int] = set()
    reruns = 0
    while True:
        pending = [run_id for run_id in _failed_run_ids(runner, repo, head) if run_id not in processed]
        if not pending:
            remaining = [run_id for run_id in _failed_run_ids(runner, repo, head) if run_id not in processed]
            if remaining:
                continue
            break
        for run_id in pending:
            current = _read_pull_request(runner, repo, pr)
            _validate_pull_request(current, repo, pr, expected_head=head, expected_branch=branch)
            if _process_run(runner, repo, pr, head, branch, head_repository, run_id):
                reruns += 1
            processed.add(run_id)
    current = _read_pull_request(runner, repo, pr)
    _validate_pull_request(current, repo, pr, expected_head=head, expected_branch=branch)
    if current.get("autoMergeRequest"):
        return Recovery(reruns, False)
    _close_and_reopen(runner, repo, pr, head, branch)
    return Recovery(reruns, True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=os.environ.get("ORACLE_REVIEW_REPO", "BenchBox-dev/BenchBox"))
    parser.add_argument("--pr", default=os.environ.get("ORACLE_REVIEW_PR", ""))
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[1-9][0-9]{0,8}", args.pr):
        print("oracle-review recovery requires PR=<positive number>", file=sys.stderr)
        return 2
    try:
        result = recover(args.repo, int(args.pr))
    except RecoveryError as exc:
        print(f"oracle-review recovery: {exc}", file=sys.stderr)
        return 1
    print(f"Reran {result.reruns} unsuccessful oracle-review run(s) serially for PR #{args.pr}.")
    if result.reopened:
        print(f"Closed and reopened PR #{args.pr}; the fresh pull_request event requeues its checks.")
    else:
        print(
            f"PR #{args.pr} has auto-merge enabled, so it was not closed and reopened: "
            "closing a pull request turns auto-merge off. Auto-merge is still armed."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
