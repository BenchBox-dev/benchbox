#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

LOCK_PATH = Path(".github") / "theseus-lock.json"
API = "https://api.github.com"
MAIN = "main"
REQUIRED_CHECKS = ("test (py3.11)", "test (py3.13)", "build and installed-wheel smoke")
REACHABLE = frozenset({"identical", "behind"})
ATTEMPTS = 6
BACKOFF_SECONDS = 2.0
BACKOFF_CAP_SECONDS = 30.0

Fetch = Callable[[str], Any]


class ReleaseCheckError(Exception):
    pass


def _transient(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code >= 500 or exc.code == 429
    return isinstance(exc, (urllib.error.URLError, TimeoutError))


def _server_error(exc: Exception) -> bool:
    return isinstance(exc, urllib.error.HTTPError) and exc.code >= 500


def _fetch(path: str, *, sleep: Callable[[float], None] = time.sleep, opener: Callable[..., Any] | None = None) -> Any:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "benchbox-theseus-release-check"}
    token = os.environ.get("GITHUB_TOKEN")
    authenticated = urllib.request.Request(
        f"{API}{path}", headers={**headers, "Authorization": f"Bearer {token}"} if token else headers
    )
    anonymous = urllib.request.Request(f"{API}{path}", headers=headers)
    open_url = opener or urllib.request.urlopen
    anonymous_tried = not token
    for attempt in range(1, ATTEMPTS + 1):
        try:
            try:
                with open_url(authenticated, timeout=30) as response:
                    return json.load(response)
            except urllib.error.HTTPError as exc:
                if anonymous_tried or not _server_error(exc):
                    raise
                anonymous_tried = True
                with open_url(anonymous, timeout=30) as response:
                    return json.load(response)
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == ATTEMPTS or not _transient(exc):
                raise ReleaseCheckError(f"GitHub API request {path} failed: {exc}") from exc
            sleep(min(BACKOFF_SECONDS * 2 ** (attempt - 1), BACKOFF_CAP_SECONDS))
        except json.JSONDecodeError as exc:
            raise ReleaseCheckError(f"GitHub API request {path} failed: {exc}") from exc
    raise ReleaseCheckError(f"GitHub API request {path} failed")


def _judge(fetch: Fetch, repo: str, sha: str) -> tuple[list[str], bool]:
    payload = fetch(f"/repos/{repo}/commits/{sha}/check-runs?per_page=100")
    latest: dict[str, Mapping[str, Any]] = {}
    for run in payload.get("check_runs", []):
        name = run.get("name")
        if name in REQUIRED_CHECKS and run.get("id", 0) >= latest.get(name, {}).get("id", -1):
            latest[name] = run
    problems = []
    for name in REQUIRED_CHECKS:
        run = latest.get(name)
        if run is None:
            problems.append(f"{name}: no check run on {sha}")
        elif run.get("conclusion") != "success":
            problems.append(f"{name}: concluded {run.get('conclusion')!r} on {sha}")
    return problems, bool(latest)


def _tree(fetch: Fetch, repo: str, sha: str) -> str | None:
    return (fetch(f"/repos/{repo}/git/commits/{sha}").get("tree") or {}).get("sha")


def verify(lock: Mapping[str, Any], fetch: Fetch) -> list[str]:
    repo = lock["repository"]
    sha = lock["source_commit"]
    status = fetch(f"/repos/{repo}/compare/{MAIN}...{sha}").get("status")
    if status not in REACHABLE:
        return [f"{sha} is not reachable from {repo} {MAIN} (compare status {status!r})"]
    direct, present = _judge(fetch, repo, sha)
    if present or not direct:
        return direct
    evidence = list(direct)
    for pull in fetch(f"/repos/{repo}/commits/{sha}/pulls?per_page=100"):
        if not pull.get("merged_at") or pull.get("base", {}).get("ref") != MAIN:
            continue
        head = pull["head"]["sha"]
        locked_tree = _tree(fetch, repo, sha)
        head_tree = _tree(fetch, repo, head)
        if not locked_tree or locked_tree != head_tree:
            evidence.append(
                f"pull {pull.get('number')}: head {head} has tree {head_tree!r}, not the locked commit's tree {locked_tree!r}"
            )
            continue
        problems, _ = _judge(fetch, repo, head)
        if not problems:
            return []
        evidence.extend(f"pull {pull.get('number')}: {item}" for item in problems)
    return evidence


def _release_tree(root: Path) -> bool:
    if (root / ".github" / "theseus-lock.json").exists() or (root / ".github" / "oracle-reviewers.yml").exists():
        return False
    print("release tree: no Theseus lock; skipping", file=sys.stderr)
    return True


def main(argv: list[str], fetch: Fetch = _fetch) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path.cwd()
    if _release_tree(root):
        return 0
    try:
        lock = json.loads((root / LOCK_PATH).read_text(encoding="utf-8"))
        problems = verify(lock, fetch)
    except (OSError, ValueError, KeyError, ReleaseCheckError) as exc:
        lock = {}
        problems = [str(exc)]
    if problems:
        print(f"theseus release check failed for {lock.get('source_commit', 'the lock')}:", file=sys.stderr)
        for item in problems:
            print(f"  {item}", file=sys.stderr)
        return 1
    print(f"theseus {lock['source_commit']} is on {lock['repository']} {MAIN} and passed {', '.join(REQUIRED_CHECKS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
