#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

CLI_DESCRIPTION = (
    "Decide whether a develop push SHA was already tested by the merge queue.\n"
    "\n"
    "A pushed SHA is *queue-certified* when ALL of the following hold:\n"
    "\n"
    "1. A workflow run with event ``merge_group``, workflow file\n"
    "   ``.github/workflows/pr.yml``, conclusion ``success``, and ``head_sha``\n"
    "   exactly equal to the pushed SHA exists.\n"
    "2. That run's ``ci-required-result`` job concluded ``success``.\n"
    "\n"
    "Nothing else counts: no tree matching, no ``pull_request``-run evidence, no\n"
    "parent inheritance.\n"
    "\n"
    "Fail-open by design: any API error, pagination gap, missing permission,\n"
    "ambiguous match, or timeout reports ``certified=false`` with a reason, and\n"
    "the process still exits 0 so the lookup can never red a workflow on its\n"
    "own. Callers gate expensive jobs on ``certified == 'true'`` and run the\n"
    "full gates otherwise.\n"
    "\n"
    "The event/ref gate lives HERE, not in a workflow ``if:``: downstream jobs\n"
    "read this lookup's outputs on every event, and references to a skipped\n"
    "job's outputs do not evaluate reliably. The certify job therefore runs\n"
    "unconditionally and this script returns ``certified=false`` without any\n"
    "API call when the event or ref is ineligible.\n"
    "\n"
    "Stdlib-only (urllib, no ``gh`` dependency) so the lookup step needs no\n"
    "dependency sync; unit tests inject a fake ``urlopen``.\n"
)

API_BASE = "https://api.github.com"
PR_WORKFLOW_FILE = ".github/workflows/pr.yml"
REQUIRED_JOB = "ci-required-result"
REQUEST_TIMEOUT_SECONDS = 20


class CertificationError(RuntimeError):
    pass


def _api_get(
    path: str,
    repo: str,
    token: str,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    url = f"{API_BASE}/repos/{repo}/{path.lstrip('/')}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        raise CertificationError(f"API read failed for {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise CertificationError(f"API read returned a non-object for {path}")
    return payload


def _workflow_runs(
    repo: str,
    token: str,
    workflow_file: str,
    sha: str,
    urlopen: Callable[..., Any],
) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({"head_sha": sha, "event": "merge_group", "per_page": "30"})
    workflow_quoted = urllib.parse.quote(workflow_file, safe="")
    payload = _api_get(f"actions/workflows/{workflow_quoted}/runs?{query}", repo, token, urlopen)
    runs = payload.get("workflow_runs")
    if not isinstance(runs, list):
        raise CertificationError(f"run listing for {workflow_file} has no workflow_runs list")
    return [run for run in runs if isinstance(run, dict)]


def _run_jobs(repo: str, token: str, run_id: int, urlopen: Callable[..., Any]) -> list[dict[str, Any]]:
    payload = _api_get(f"actions/runs/{run_id}/jobs?per_page=100&filter=all", repo, token, urlopen)
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        raise CertificationError(f"jobs listing for run {run_id} has no jobs list")
    return [job for job in jobs if isinstance(job, dict)]


def _job_conclusion(jobs: list[dict[str, Any]], name: str) -> str | None:
    matches = [job for job in jobs if job.get("name") == name]
    if len(matches) != 1:
        return None
    conclusion = matches[0].get("conclusion")
    return conclusion if isinstance(conclusion, str) else None


def check_event_eligibility(event: str, ref: str, allow_ref: str) -> str | None:
    if event != "push":
        return f"event {event!r} is never queue-certified; running the full gates"
    if ref != allow_ref:
        return f"ref {ref!r} is never queue-certified; running the full gates"
    return None


def find_certifying_run(
    sha: str,
    repo: str,
    token: str,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
    event: str = "push",
    ref: str = "",
    allow_ref: str = "refs/heads/develop",
) -> dict[str, Any]:
    ineligible = check_event_eligibility(event, ref, allow_ref)
    if ineligible is not None:
        return {"certified": False, "run_id": None, "reason": ineligible}
    try:
        return _find_certifying_run(sha, repo, token, urlopen)
    except CertificationError as exc:
        return {"certified": False, "run_id": None, "reason": f"lookup failed open: {exc}"}


def _find_certifying_run(
    sha: str,
    repo: str,
    token: str,
    urlopen: Callable[..., Any],
) -> dict[str, Any]:
    if not sha or not repo or not token:
        raise CertificationError("sha, repo, and token are all required")
    candidates = sorted(
        _workflow_runs(repo, token, PR_WORKFLOW_FILE, sha, urlopen),
        key=lambda run: str(run.get("created_at") or ""),
        reverse=True,
    )
    for run in candidates:
        if str(run.get("event")) != "merge_group":
            continue
        if str(run.get("head_sha") or "") != sha:
            continue
        if not str(run.get("path") or "").endswith("pr.yml"):
            continue
        if str(run.get("conclusion") or "") != "success":
            continue
        run_id = run.get("id")
        if not isinstance(run_id, int):
            continue
        jobs = _run_jobs(repo, token, run_id, urlopen)
        if _job_conclusion(jobs, REQUIRED_JOB) != "success":
            continue
        return {
            "certified": True,
            "run_id": run_id,
            "reason": f"merge_group run {run_id} passed {PR_WORKFLOW_FILE} on this exact SHA",
        }
    return {"certified": False, "run_id": None, "reason": "no successful merge_group run of pr.yml on this exact SHA"}


def _write_github_output(path: Path, certified: bool, run_id: int | None) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"certified={'true' if certified else 'false'}\n")
        handle.write(f"certifying_run_id={run_id if run_id is not None else ''}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--sha", default=os.environ.get("GITHUB_SHA", ""))
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--token", default=os.environ.get("GH_TOKEN", ""))
    parser.add_argument("--event", default=os.environ.get("GITHUB_EVENT_NAME", ""))
    parser.add_argument("--ref", default=os.environ.get("GITHUB_REF", ""))
    parser.add_argument("--allow-ref", default="refs/heads/develop")
    parser.add_argument("--github-output", type=Path, default=None)
    parser.add_argument("--summary", type=Path, default=None)
    args = parser.parse_args(argv)

    result = find_certifying_run(
        sha=args.sha,
        repo=args.repo,
        token=args.token,
        event=args.event,
        ref=args.ref,
        allow_ref=args.allow_ref,
    )
    certified = bool(result["certified"])
    run_id = result["run_id"]
    reason = str(result["reason"])
    print(f"certified={'true' if certified else 'false'} certifying_run_id={run_id if run_id is not None else ''}")
    print(reason)
    if args.github_output is not None:
        _write_github_output(args.github_output, certified, run_id)
    if args.summary is not None:
        with args.summary.open("a", encoding="utf-8") as handle:
            if certified:
                handle.write(f"Queue-certified by merge_group run {run_id}; fast/medium gates skip.\n")
            else:
                handle.write(f"Not queue-certified ({reason}); running the full gates.\n")
            handle.write(f"{reason}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
