from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

CLI_DESCRIPTION = (
    "Summarize a release-canary run as an owned incident update.\n"
    "\n"
    "Release readiness blocks on a green, fresh canary, but a red canary used to\n"
    "alert nobody: the 2026-09-14 regression stayed red for ten days while further\n"
    "regressions piled on top of it. This script turns one canary run into the text\n"
    "of an incident issue, so a red run opens (or updates) one owned issue and the\n"
    "next green run closes it.\n"
    "\n"
    "It reads the shard pytest logs uploaded by the canary and the per-job results,\n"
    "and writes a JSON document with the state (``red`` or ``green``), the failing\n"
    "test IDs, and the issue title, body, and comment. Posting to GitHub stays in\n"
    "the workflow.\n"
)

INCIDENT_LABEL = "incident:release-canary-red"
INCIDENT_TITLE = "Release canary is red; releases are blocked"
SHARD_LOG_GLOB = "shard-*-pytest.log"

_FAILURE_LINE = re.compile(r"^(?:FAILED|ERROR) (tests/\S+)")
_MAXFAIL_LINE = re.compile(r"stopping after \d+ failures", re.IGNORECASE)
_SHARD_INDEX = re.compile(r"shard-(\d+)-pytest\.log$")


def parse_failures(log_text: str) -> list[str]:
    failures = []
    for line in log_text.splitlines():
        match = _FAILURE_LINE.match(line.strip())
        if match:
            failures.append(match.group(1))
    return sorted(set(failures))


def is_truncated(log_text: str) -> bool:
    return bool(_MAXFAIL_LINE.search(log_text))


def collect_failures(artifacts_dir: Path) -> tuple[list[str], list[int]]:
    failures: set[str] = set()
    truncated: set[int] = set()
    for log_path in sorted(artifacts_dir.rglob(SHARD_LOG_GLOB)):
        text = log_path.read_text(encoding="utf-8", errors="replace")
        failures.update(parse_failures(text))
        if is_truncated(text):
            match = _SHARD_INDEX.search(log_path.name)
            if match:
                truncated.add(int(match.group(1)))
    return sorted(failures), sorted(truncated)


def parse_job_results(pairs: Iterable[str]) -> dict[str, str]:
    results = {}
    for pair in pairs:
        name, sep, result = pair.partition("=")
        if not sep or not name:
            raise ValueError(f"job result must be name=result, got {pair!r}")
        results[name] = result or "unknown"
    if not results:
        raise ValueError("at least one job result is required")
    return results


def _changes_line(repo_url: str, last_green_sha: str, checked_sha: str) -> str:
    if not last_green_sha:
        return "Changes since the last green run: no green run found."
    if not checked_sha:
        return f"Changes since the last green run (`{last_green_sha[:9]}`): unknown; the checked SHA was not recorded."
    if last_green_sha == checked_sha:
        return f"Changes since the last green run: none; `{checked_sha[:9]}` was green before."
    compare = f"{repo_url}/compare/{last_green_sha}...{checked_sha}"
    return f"Changes since the last green run (`{last_green_sha[:9]}`): {compare}"


def render(
    *,
    job_results: dict[str, str],
    failures: Sequence[str],
    truncated_shards: Sequence[int],
    run_url: str,
    checked_sha: str,
    last_green_sha: str,
    repo_url: str,
) -> dict[str, Any]:
    failed_jobs = sorted(name for name, result in job_results.items() if result != "success")
    state = "red" if failed_jobs else "green"
    short_sha = checked_sha[:9] or "unknown"

    if state == "green":
        comment = f"Release canary is green again at `{short_sha}`: {run_url}\n\nClosing this incident."
        return {
            "state": state,
            "failed_jobs": [],
            "failures": [],
            "title": INCIDENT_TITLE,
            "body": "",
            "comment": comment,
        }

    failure_lines = "\n".join(f"- `{node_id}`" for node_id in failures) or "- none parsed from the shard logs"
    job_lines = "\n".join(f"- `{name}`: {job_results[name]}" for name in failed_jobs)
    truncation = ""
    if truncated_shards:
        shards = ", ".join(str(index) for index in truncated_shards)
        truncation = (
            f"\n\nShards {shards} stopped at `--maxfail`, so this list may be incomplete. "
            "Run the canary marker expression locally on those shards' files to see every failure."
        )
    details = (
        f"Run: {run_url}\n"
        f"Checked `develop` SHA: `{short_sha}`\n"
        f"{_changes_line(repo_url, last_green_sha, checked_sha)}\n\n"
        f"Failed jobs:\n{job_lines}\n\n"
        f"Failing tests:\n{failure_lines}{truncation}"
    )
    body = (
        "The daily release canary failed. `release-v2.yml` does not read the canary, so this does not block a "
        "tag by itself; investigate before tagging a release.\n\n"
        "Owner: fix forward or revert the change that broke each failing test, then confirm the next "
        "canary run is green. This issue closes on the next green run.\n\n"
        f"{details}"
    )
    comment = f"Release canary is still red.\n\n{details}"
    return {
        "state": state,
        "failed_jobs": failed_jobs,
        "failures": list(failures),
        "title": INCIDENT_TITLE,
        "body": body,
        "comment": comment,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--artifacts-dir", type=Path, required=True)
    parser.add_argument("--job-result", action="append", default=[], help="name=result; repeat per job")
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--checked-sha", default="")
    parser.add_argument("--last-green-sha", default="")
    parser.add_argument("--repo-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        job_results = parse_job_results(args.job_result)
        failures, truncated = ([], [])
        if args.artifacts_dir.is_dir():
            failures, truncated = collect_failures(args.artifacts_dir)
        update = render(
            job_results=job_results,
            failures=failures,
            truncated_shards=truncated,
            run_url=args.run_url,
            checked_sha=args.checked_sha,
            last_green_sha=args.last_green_sha,
            repo_url=args.repo_url.rstrip("/"),
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(update, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError) as exc:
        print(f"release-canary incident error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
