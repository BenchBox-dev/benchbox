from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from . import reset
from .retry import State

STATE_ARTIFACT_PREFIX = "oracle-verdict-state-"
LEGACY_STATE_ARTIFACT_PREFIX = "oracle-review-shadow-state-"
STATE_FILE = "state.json"


class GitHubError(RuntimeError):
    pass


def _gh_bytes(*args: str, accept: str | None = None) -> bytes:
    argv = ["gh", "api", *(("-H", f"Accept: {accept}") if accept else ()), *args]
    result = subprocess.run(argv, capture_output=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()[:500]
        raise GitHubError(f"gh api {' '.join(args)} failed: {detail}")
    return result.stdout


def _gh(*args: str, accept: str | None = None) -> str:
    return _gh_bytes(*args, accept=accept).decode("utf-8", errors="replace")


def get_json(path: str) -> Any:
    return json.loads(_gh(path))


def get_paginated(path: str) -> list[Any]:
    pages = json.loads(_gh("--paginate", "--slurp", path))
    return [item for page in pages for item in page]


def get_diff(repo: str, pr: int) -> bytes | None:
    try:
        return _gh_bytes(f"repos/{repo}/pulls/{pr}", accept="application/vnd.github.v3.diff")
    except GitHubError:
        return None


def merge_base(repo: str, base: str, head: str) -> str | None:
    try:
        sha = (get_json(f"repos/{repo}/compare/{base}...{head}?per_page=1").get("merge_base_commit") or {}).get("sha")
    except (GitHubError, ValueError):
        return None
    return sha if isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40}", sha) else None


def tree_blobs(repo: str, sha: str) -> dict[str, tuple[str, str]] | None:
    try:
        data = get_json(f"repos/{repo}/git/trees/{sha}?recursive=1")
    except (GitHubError, ValueError):
        return None
    entries = data.get("tree")
    if data.get("truncated") or not isinstance(entries, list):
        return None
    return {
        entry["path"]: (entry["mode"], entry["sha"]) for entry in entries if entry.get("type") in ("blob", "commit")
    }


_THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          isResolved
          path
          comments(first: 50) { nodes { author { __typename login } authorAssociation body } }
        }
      }
    }
  }
}
"""


def review_threads(repo: str, pr: int) -> list[dict[str, Any]]:
    owner, name = repo.split("/", 1)
    threads: list[dict[str, Any]] = []
    after: str | None = None
    while True:
        args = ["graphql", "-f", f"query={_THREADS_QUERY}", "-f", f"owner={owner}", "-f", f"name={name}"]
        args += ["-F", f"number={pr}", *(("-f", f"after={after}") if after else ())]
        payload = json.loads(_gh(*args))
        if payload.get("errors"):
            raise GitHubError(f"GitHub GraphQL returned errors: {payload['errors']}")
        connection = payload["data"]["repository"]["pullRequest"]["reviewThreads"]
        for node in connection["nodes"]:
            comments = node["comments"]["nodes"] or [{}]
            first = comments[0]
            threads.append(
                {
                    "resolved": node["isResolved"],
                    "path": node.get("path"),
                    "author": (first.get("author") or {}).get("login"),
                    "author_type": (first.get("author") or {}).get("__typename"),
                    "body": first.get("body") or "",
                    "replies": [
                        {
                            "author": (item.get("author") or {}).get("login"),
                            "author_type": (item.get("author") or {}).get("__typename"),
                            "association": item.get("authorAssociation"),
                            "body": item.get("body") or "",
                        }
                        for item in comments[1:]
                    ],
                }
            )
        if not connection["pageInfo"]["hasNextPage"]:
            return threads
        after = connection["pageInfo"]["endCursor"]


def oracle_reviews(repo: str, pr: int) -> list[dict[str, Any]]:
    return [
        {
            "id": item.get("id"),
            "login": (item.get("user") or {}).get("login"),
            "user_type": (item.get("user") or {}).get("type"),
            "state": item.get("state"),
            "body": item.get("body") or "",
            "submitted_at": item.get("submitted_at"),
            "commit_id": item.get("commit_id"),
        }
        for item in get_paginated(f"repos/{repo}/pulls/{pr}/reviews?per_page=100")
    ]


def state_artifact_name(pr: int, prefix: str = STATE_ARTIFACT_PREFIX) -> str:
    return f"{prefix}{pr}"


WORKFLOW_PATH = ".github/workflows/oracle-verdict.yml"
LEGACY_WORKFLOW_PATH = ".github/workflows/oracle-review-shadow.yml"
STATE_SOURCES = ((STATE_ARTIFACT_PREFIX, WORKFLOW_PATH), (LEGACY_STATE_ARTIFACT_PREFIX, LEGACY_WORKFLOW_PATH))
TRUSTED_EVENTS = frozenset({"pull_request_target", "issue_comment", "schedule", "workflow_dispatch"})
TRUSTED_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})


def on_develop(repo: str, sha: str) -> bool:
    try:
        comparison = get_json(f"repos/{repo}/compare/{sha}...develop")
    except GitHubError:
        return False
    return comparison.get("status") in ("ahead", "identical")


def _targets_develop(run: dict[str, Any], pr: int) -> bool:
    pulls = [pull for pull in run.get("pull_requests") or [] if isinstance(pull, dict)]
    return (
        bool(pulls)
        and all((pull.get("base") or {}).get("ref") == "develop" for pull in pulls)
        and any(pull.get("number") == pr for pull in pulls)
    )


def _parse_time(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def base_changed_at(repo: str, pr: int) -> datetime | None:
    changes = [
        _parse_time(event.get("created_at"))
        for event in get_paginated(f"repos/{repo}/issues/{pr}/timeline?per_page=100")
        if event.get("event") == "base_ref_changed"
    ]
    if any(moment is None for moment in changes):
        raise GitHubError(f"a base change on #{pr} has no readable time")
    return max(filter(None, changes), default=None)


@dataclass(frozen=True)
class HeadPull:
    number: int
    created_at: datetime
    base_ref: str
    base_changed_at: datetime | None


def head_pulls(repo: str, run: dict[str, Any]) -> list[HeadPull]:
    owner = (((run.get("head_repository") or {}).get("owner")) or {}).get("login")
    branch = run.get("head_branch")
    if not owner or not branch:
        raise GitHubError("the run does not name its head branch")
    found: list[HeadPull] = []
    for pull in get_paginated(f"repos/{repo}/pulls?state=all&head={owner}:{quote(branch, safe='/')}&per_page=100"):
        created_at = _parse_time(pull.get("created_at"))
        if created_at is None or not isinstance(pull.get("number"), int):
            raise GitHubError("a pull request for the head branch has no readable creation time")
        changes = [
            _parse_time(event.get("created_at"))
            for event in get_paginated(f"repos/{repo}/issues/{pull['number']}/timeline?per_page=100")
            if event.get("event") == "base_ref_changed"
        ]
        if any(moment is None for moment in changes):
            raise GitHubError(f"a base change on #{pull['number']} has no readable time")
        found.append(
            HeadPull(
                pull["number"],
                created_at,
                str((pull.get("base") or {}).get("ref", "")),
                max(filter(None, changes), default=None),
            )
        )
    return found


def _pull_request_target_trusted(run: dict[str, Any], pr: int, pulls: Sequence[HeadPull] | None) -> bool:
    started = _parse_time(run.get("created_at"))
    if started is None or pulls is None or not _targets_develop(run, pr):
        return False
    open_then = [pull for pull in pulls if pull.created_at <= started]
    return any(pull.number == pr for pull in open_then) and all(
        pull.base_ref == "develop" and (pull.base_changed_at is None or pull.base_changed_at < started)
        for pull in open_then
    )


def trusted_run(
    run: dict[str, Any],
    repo: str,
    pr: int,
    is_on_develop: Callable[[str, str], bool] = on_develop,
    pulls: Sequence[HeadPull] | None = None,
    workflow_path: str = WORKFLOW_PATH,
) -> bool:
    head_sha = str(run.get("head_sha", ""))
    event = run.get("event")
    if not (
        str(run.get("path", "")).split("@", 1)[0] == workflow_path
        and (run.get("repository") or {}).get("full_name") == repo
        and event in TRUSTED_EVENTS
        and re.fullmatch(r"[0-9a-f]{40}", head_sha) is not None
    ):
        return False
    if event == "pull_request_target":
        return _pull_request_target_trusted(run, pr, pulls)
    return is_on_develop(repo, head_sha)


def latest_state(repo: str, pr: int) -> State | None:
    for prefix, workflow_path in STATE_SOURCES:
        state = _latest_state(repo, pr, state_artifact_name(pr, prefix), workflow_path)
        if state is not None:
            return state
    return None


def _latest_state(repo: str, pr: int, name: str, workflow_path: str) -> State | None:
    listing = get_json(f"repos/{repo}/actions/artifacts?name={name}&per_page=30")
    candidates = sorted(
        (item for item in listing.get("artifacts", []) if not item.get("expired")),
        key=lambda item: item.get("created_at", ""),
        reverse=True,
    )
    for candidate in candidates:
        run_id = str(candidate["workflow_run"]["id"])
        run = get_json(f"repos/{repo}/actions/runs/{run_id}")
        pulls: list[HeadPull] | None = None
        if run.get("event") == "pull_request_target":
            try:
                pulls = head_pulls(repo, run)
            except GitHubError:
                continue
        if not trusted_run(run, repo, pr, pulls=pulls, workflow_path=workflow_path):
            continue
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                ["gh", "run", "download", run_id, "-n", name, "-D", directory, "-R", repo],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                raise GitHubError(f"could not download {name} from run {run_id}: {result.stderr.strip()[:500]}")
            return State.from_json(json.loads((Path(directory) / STATE_FILE).read_text(encoding="utf-8")))
    return None


def verdict_runs(repo: str, workflow: str, since: datetime) -> list[dict[str, Any]]:
    created = quote(f">={since.strftime('%Y-%m-%dT%H:%M:%SZ')}", safe="")
    pages = json.loads(
        _gh("--paginate", "--slurp", f"repos/{repo}/actions/workflows/{workflow}/runs?per_page=100&created={created}")
    )
    return [run for page in pages for run in page.get("workflow_runs", [])]


def committed_at(repo: str, sha: str) -> datetime:
    moment = _parse_time(get_json(f"repos/{repo}/commits/{sha}")["commit"]["committer"]["date"])
    if moment is None:
        raise GitHubError(f"commit {sha} has no readable commit time")
    return moment


FAILED_STEP_CONCLUSIONS = frozenset({"failure", "timed_out", "cancelled"})


def failed_steps(repo: str, run_id: int) -> list[str]:
    listing = get_json(f"repos/{repo}/actions/runs/{run_id}/jobs?filter=latest&per_page=100")
    return [
        str(step["name"])
        for job in listing.get("jobs", [])
        for step in job.get("steps", [])
        if step.get("conclusion") in FAILED_STEP_CONCLUSIONS
    ]


def commit_statuses(repo: str, sha: str) -> list[dict[str, Any]]:
    combined = get_json(f"repos/{repo}/commits/{sha}/status")
    statuses = combined.get("statuses", [])
    return [status for status in statuses if isinstance(status, dict)]


def is_app_status(status: Mapping[str, Any], bot_login: str) -> bool:
    creator = status.get("creator") or {}
    if not isinstance(creator, Mapping):
        return False
    return (creator.get("login") or "").removesuffix("[bot]") == bot_login and creator.get("type") == "Bot"


def post_status(repo: str, head_sha: str, payload: Mapping[str, Any], token: str) -> None:
    env = dict(os.environ)
    env["GH_TOKEN"] = token
    result = subprocess.run(
        [
            "gh",
            "api",
            "--method",
            "POST",
            f"repos/{repo}/statuses/{head_sha}",
            "-f",
            f"state={payload['state']}",
            "-f",
            f"context={payload['context']}",
            "-f",
            f"description={payload['description']}",
            "-f",
            f"target_url={payload['target_url']}",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if result.returncode != 0:
        raise GitHubError(f"could not post a status on {head_sha}: {result.stderr.strip()[:500]}")


def backoff_reset_at(repo: str, pr: int, head_sha: str) -> datetime | None:
    return reset.reset_moment(get_paginated(f"repos/{repo}/issues/{pr}/comments?per_page=100"), head_sha)


def current_login() -> str:
    return _gh("user", "--jq", ".login").strip()


def current_repository() -> str:
    result = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise GitHubError(f"could not resolve the repository: {result.stderr.strip()[:500]}")
    return result.stdout.strip()


def post_comment(repo: str, pr: int, body: str) -> None:
    _gh("--method", "POST", f"repos/{repo}/issues/{pr}/comments", "-f", f"body={body}")


def dispatch(repo: str, workflow: str, pr: int) -> None:
    result = subprocess.run(
        ["gh", "workflow", "run", workflow, "--ref", "develop", "-f", f"pr={pr}", "-R", repo],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise GitHubError(f"could not dispatch {workflow} for #{pr}: {result.stderr.strip()[:500]}")
