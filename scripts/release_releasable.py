#!/usr/bin/env python3

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

CANDIDATE_LIMIT = 40
_SHA = re.compile(r"[0-9a-f]{40}")


def _load_consumer():
    spec = importlib.util.spec_from_file_location(
        "_release_releasable_consumer", Path(__file__).with_name("release_artifact_consumer.py")
    )
    if spec is None or spec.loader is None:
        raise ImportError("release_artifact_consumer.py cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_consumer = _load_consumer()
Api = Callable[[str], dict[str, Any]]


def unreleasable_reason(sha: str, api: Api | None = None) -> str | None:
    if _SHA.fullmatch(sha) is None:
        return "the commit must be a full 40-character SHA"
    try:
        _consumer.select_producer(sha, api or _consumer.github_json)
    except ValueError as exc:
        return str(exc)
    return None


def newest_releasable(
    api: Api | None = None, *, exclude: str | None = None, limit: int = CANDIDATE_LIMIT
) -> str | None:
    api = api or _consumer.github_json
    payload = api(
        f"actions/workflows/trunk.yml/runs?branch={_consumer.PRODUCER_BRANCH}"
        f"&event={_consumer.PRODUCER_EVENT}&status=success&per_page=100"
    )
    runs = payload.get("workflow_runs")
    if not isinstance(runs, list):
        raise ValueError("malformed API page")
    runs = [run for run in runs if isinstance(run, dict) and run.get("conclusion") == "success"]
    runs.sort(key=lambda run: (str(run.get("created_at")), int(run.get("id", 0))), reverse=True)
    seen: set[str] = set()
    for run in runs:
        sha = run.get("head_sha")
        if not isinstance(sha, str) or sha in seen or sha == exclude:
            continue
        seen.add(sha)
        if len(seen) > limit:
            break
        if unreleasable_reason(sha, api) is None:
            return sha
    return None


def releasable_problems(sha: str, api: Api | None = None) -> list[str]:
    reason = unreleasable_reason(sha, api)
    if reason is None:
        return []
    try:
        newest = newest_releasable(api, exclude=sha)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        newest_text = f"the newest releasable commit could not be determined ({exc})"
    else:
        newest_text = (
            f"newest releasable commit on develop: {newest}"
            if newest
            else f"no releasable commit among the newest {CANDIDATE_LIMIT} successful trunk runs"
        )
    return [f"commit {sha} is not releasable ({reason}); {newest_text}"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Find develop commits with a successful trunk run and dist artifact")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("newest", help="Print the newest releasable develop commit")
    check = sub.add_parser("check", help="Fail unless the commit is releasable, naming the newest releasable commit")
    check.add_argument("--sha", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "newest":
            newest = newest_releasable()
            if newest is None:
                print(f"no releasable commit among the newest {CANDIDATE_LIMIT} successful trunk runs", file=sys.stderr)
                return 1
            print(newest)
            return 0
        problems = releasable_problems(args.sha)
    except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        print(f"release_releasable FAILED: {exc}", file=sys.stderr)
        return 1
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        return 1
    print(f"commit {args.sha} is releasable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
