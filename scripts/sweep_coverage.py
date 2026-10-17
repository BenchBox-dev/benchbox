#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

import queue_certification

CLI_DESCRIPTION = (
    "Decide whether the develop tip is already covered before an hourly sweep.\n"
    "\n"
    "The ``schedule`` trigger in ``develop-post-merge.yml`` exists to cover\n"
    "dropped push events, but most sweeps re-run the slim gates on a tip SHA\n"
    "that a per-push run already gated. A tip is *covered* when EITHER of the\n"
    "following holds:\n"
    "\n"
    "1. A ``develop-post-merge.yml`` run with event ``push`` and ``head_sha``\n"
    "   exactly equal to the tip reached a terminal conclusion of ``success``.\n"
    "   Failed, cancelled, startup-failed, missing, or still-running runs are NOT\n"
    "   coverage; the sweep runs the gates again.\n"
    "2. The tip is queue-certified under the exact rule in ci-dedupe-01\n"
    "   (:func:`queue_certification.find_certifying_run` with a push-to-develop\n"
    "   event/ref): the merge queue already passed this exact SHA.\n"
    "\n"
    "Nothing else counts: no tree matching, no ``schedule``-run evidence, no\n"
    "parent inheritance.\n"
    "\n"
    "Fail-open by design: any API error, pagination gap, missing permission,\n"
    "ambiguous match, or timeout reports ``covered=false`` with a reason, and\n"
    "the process still exits 0 so the lookup can never red a workflow on its\n"
    "own. Callers gate the sweep gates on ``covered == 'true'`` and run the\n"
    "full gates otherwise.\n"
    "\n"
    "The event gate lives HERE, not in a workflow ``if:``: downstream jobs\n"
    "read this lookup's outputs on every event, and references to a skipped\n"
    "job's outputs do not evaluate reliably. The sweep-coverage job therefore\n"
    "runs unconditionally and this script returns ``covered=false`` without\n"
    "any API call when the event is not ``schedule``.\n"
    "\n"
    "Stdlib-only (urllib, no ``gh`` dependency) so the lookup step needs no\n"
    "dependency sync; unit tests inject a fake ``urlopen``.\n"
)

API_BASE = "https://api.github.com"
POST_MERGE_WORKFLOW_FILE = ".github/workflows/develop-post-merge.yml"
REQUEST_TIMEOUT_SECONDS = 20

COVERING_CONCLUSIONS = ("success",)


class CoverageError(RuntimeError):
    pass


def _api_get(
    path: str,
    repo: str,
    token: str,
    urlopen: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    opener = urlopen or urllib.request.urlopen
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
        with opener(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        raise CoverageError(f"API read failed for {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise CoverageError(f"API read returned a non-object for {path}")
    return payload


def _push_runs_for_sha(
    sha: str,
    repo: str,
    token: str,
    urlopen: Callable[..., Any],
) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({"head_sha": sha, "event": "push", "per_page": "30"})
    workflow_quoted = urllib.parse.quote(POST_MERGE_WORKFLOW_FILE, safe="")
    payload = _api_get(f"actions/workflows/{workflow_quoted}/runs?{query}", repo, token, urlopen)
    runs = payload.get("workflow_runs")
    if not isinstance(runs, list):
        raise CoverageError(f"run listing for {POST_MERGE_WORKFLOW_FILE} has no workflow_runs list")
    return [run for run in runs if isinstance(run, dict)]


def _find_covering_push_run(
    sha: str,
    repo: str,
    token: str,
    urlopen: Callable[..., Any],
) -> dict[str, Any]:
    candidates = sorted(
        _push_runs_for_sha(sha, repo, token, urlopen),
        key=lambda run: str(run.get("created_at") or ""),
        reverse=True,
    )
    for run in candidates:
        if str(run.get("event")) != "push":
            continue
        if str(run.get("head_sha") or "") != sha:
            continue
        if not str(run.get("path") or "").endswith("develop-post-merge.yml"):
            continue
        conclusion = run.get("conclusion")
        if conclusion not in COVERING_CONCLUSIONS:
            continue
        run_id = run.get("id")
        if not isinstance(run_id, int):
            continue
        return {
            "covered": True,
            "covering_run_id": run_id,
            "reason": f"push run {run_id} of develop-post-merge.yml concluded {conclusion} on this exact SHA",
        }
    return {
        "covered": False,
        "covering_run_id": None,
        "reason": "no successful push run of develop-post-merge.yml on this exact SHA",
    }


def find_coverage(
    sha: str,
    repo: str,
    token: str,
    urlopen: Callable[..., Any] | None = None,
    event: str = "schedule",
) -> dict[str, Any]:
    if event != "schedule":
        return {
            "covered": False,
            "covering_run_id": None,
            "reason": f"event {event!r} never exits early; running the full gates",
        }
    if not sha or not repo or not token:
        return {
            "covered": False,
            "covering_run_id": None,
            "reason": "lookup failed open: sha, repo, and token are all required",
        }
    opener = urlopen or urllib.request.urlopen
    try:
        push = _find_covering_push_run(sha, repo, token, opener)
        if push["covered"]:
            return push
        certified = queue_certification.find_certifying_run(
            sha=sha,
            repo=repo,
            token=token,
            urlopen=opener,
            event="push",
            ref="refs/heads/develop",
        )
        if certified.get("certified"):
            return {
                "covered": True,
                "covering_run_id": certified.get("run_id"),
                "reason": f"tip is queue-certified by merge_group run {certified.get('run_id')}",
            }
        return {
            "covered": False,
            "covering_run_id": None,
            "reason": f"{push['reason']}; {certified.get('reason')}",
        }
    except (CoverageError, queue_certification.CertificationError) as exc:
        return {"covered": False, "covering_run_id": None, "reason": f"lookup failed open: {exc}"}


def _write_github_output(path: Path, covered: bool, covering_run_id: int | None) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"covered={'true' if covered else 'false'}\n")
        handle.write(f"covering_run_id={covering_run_id if covering_run_id is not None else ''}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--sha", default=os.environ.get("GITHUB_SHA", ""))
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--token", default=os.environ.get("GH_TOKEN", ""))
    parser.add_argument("--event", default=os.environ.get("GITHUB_EVENT_NAME", ""))
    parser.add_argument("--github-output", type=Path, default=None)
    parser.add_argument("--summary", type=Path, default=None)
    args = parser.parse_args(argv)

    result = find_coverage(
        sha=args.sha,
        repo=args.repo,
        token=args.token,
        event=args.event,
    )
    covered = bool(result["covered"])
    covering_run_id = result["covering_run_id"]
    reason = str(result["reason"])
    print(
        f"covered={'true' if covered else 'false'} covering_run_id={covering_run_id if covering_run_id is not None else ''}"
    )
    print(reason)
    if args.github_output is not None:
        _write_github_output(args.github_output, covered, covering_run_id if isinstance(covering_run_id, int) else None)
    if args.summary is not None:
        with args.summary.open("a", encoding="utf-8") as handle:
            if covered:
                handle.write(
                    f"Sweep early exit: tip already covered (covering run {covering_run_id}); slim gates skip.\n"
                )
            else:
                handle.write(f"Sweep runs the full slim gates ({reason}).\n")
            handle.write(f"{reason}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
