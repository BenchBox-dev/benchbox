from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

WORKFLOW_FILE = "release-canary.yml"
SUMMARY_ARTIFACT = "release-canary-summary"
SUMMARY_MEMBER = "release-canary-summary.json"
SUMMARY_RESULT_FIELDS = (
    "collection_result",
    "non_fast_result",
    "ruleset_drift_result",
    "pypi_latest_installability_result",
)
MAX_AGE_HOURS = 48
RUN_LIMIT = 30
NO_VERDICT_CONCLUSIONS = ("cancelled", "skipped")
_SHA = re.compile(r"[0-9a-f]{40}")


def _sibling(name: str):
    path = Path(__file__).with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"_release_canary_{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"{path.name} cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_reuse = _sibling("release_reuse_uat_evidence")


class Verdict(NamedTuple):
    ok: bool
    reason: str
    rows: tuple[str, ...]


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _run_started(run: dict[str, Any]) -> datetime | None:
    return _parse_time(run.get("run_started_at")) or _parse_time(run.get("created_at"))


def _canary_run_problem(run: dict[str, Any]) -> str | None:
    if run.get("event") not in _reuse.TRUNK_EVENTS:
        return f"event {run.get('event')!r} is not schedule or workflow_dispatch"
    if run.get("head_branch") != _reuse.TRUNK_BRANCH:
        return f"ran on {run.get('head_branch')!r}, not {_reuse.TRUNK_BRANCH}"
    return None


def tested_commit(repository: str, run: dict[str, Any]) -> tuple[str, str | None]:
    listing = _reuse._artifact_listing(repository, int(run["id"]))
    summaries = [a for a in listing if a.get("name") == SUMMARY_ARTIFACT and not a.get("expired")]
    if len(summaries) == 1:
        files = _reuse._download_artifact(repository, int(summaries[0]["id"]))
        payload = json.loads(files[SUMMARY_MEMBER]) if SUMMARY_MEMBER in files else {}
        sha = payload.get("commit_sha") if isinstance(payload, dict) else None
        if isinstance(sha, str) and _SHA.fullmatch(sha):
            failed = [field for field in SUMMARY_RESULT_FIELDS if payload.get(field) != "success"]
            return sha, ", ".join(f"{field}={payload.get(field)!r}" for field in failed) or None
    return str(run.get("head_sha")), None


def evaluate(
    repository: str, tag_sha: str, repo_dir: Path, now: datetime, *, max_age_hours: int = MAX_AGE_HOURS
) -> Verdict:
    payload = _reuse._gh_json(
        f"repos/{repository}/actions/workflows/{WORKFLOW_FILE}/runs?status=completed&per_page={RUN_LIMIT}"
    )
    runs = [run for run in payload.get("workflow_runs", []) if run.get("status") == "completed"]
    runs.sort(key=lambda run: (str(run.get("created_at")), int(run["id"])), reverse=True)
    rows: list[str] = []
    eligible: list[dict[str, Any]] = []
    for run in runs:
        problem = _canary_run_problem(run)
        if problem is None:
            eligible.append(run)
        else:
            rows.append(f"run {run['id']}: {problem}, not a canary run")
    runs = eligible
    if not runs:
        return Verdict(
            False,
            f"missing: no completed {WORKFLOW_FILE} run on {_reuse.TRUNK_BRANCH} from a scheduled or manual trigger was found",
            tuple(rows),
        )
    for run in runs[:RUN_LIMIT]:
        run_id = int(run["id"])
        if run.get("conclusion") in NO_VERDICT_CONCLUSIONS:
            rows.append(f"run {run_id}: {run.get('conclusion')}, carries no verdict")
            continue
        sha, failed_results = tested_commit(repository, run)
        if not _reuse._is_ancestor(repo_dir, sha, tag_sha):
            rows.append(f"run {run_id}: tested {sha[:10]}, not an ancestor of {tag_sha[:10]}")
            continue
        started = _run_started(run)
        if started is None:
            return Verdict(False, f"error: run {run_id} has no readable start time", tuple(rows))
        age_hours = (now - started).total_seconds() / 3600
        described = f"run {run_id} (tested {sha[:10]}, started {started.strftime('%Y-%m-%dT%H:%M:%SZ')})"
        if run.get("conclusion") != "success" or failed_results:
            detail = failed_results or f"concluded {run.get('conclusion')!r}"
            return Verdict(
                False, f"red: the newest canary on an ancestor, {described}, is not green: {detail}", tuple(rows)
            )
        if age_hours > max_age_hours:
            return Verdict(
                False,
                f"stale: the newest canary on an ancestor, {described}, is {age_hours:.1f} h old; "
                f"the limit is {max_age_hours} h",
                tuple(rows),
            )
        return Verdict(True, f"{described} is green and {age_hours:.1f} h old", tuple(rows))
    return Verdict(
        False,
        f"non-ancestor: none of the newest {min(len(runs), RUN_LIMIT)} canary runs tested an ancestor of {tag_sha[:10]}",
        tuple(rows),
    )


def _markdown(verdict: Verdict) -> str:
    lines = [
        "### Release canary freshness",
        "",
        f"Decision: **{'fresh green canary' if verdict.ok else 'release refused'}**",
        "",
        verdict.reason,
        "",
    ]
    lines.extend(f"- {row}" for row in verdict.rows)
    return "\n".join(lines) + "\n"


def _handle_check(args: argparse.Namespace) -> int:
    try:
        verdict = evaluate(args.github_repository, args.head_sha, Path(args.repo_dir), datetime.now(UTC))
    except Exception as exc:
        traceback.print_exc()
        verdict = Verdict(False, f"error: {type(exc).__name__}: {exc}", ())
    for row in verdict.rows:
        print(f"[canary] skipped {row}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as stream:
            stream.write(_markdown(verdict))
    if verdict.ok:
        print(f"[canary] PASS {verdict.reason}")
        return 0
    print(f"::error::Release canary check failed, {verdict.reason}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=f"Require a green {WORKFLOW_FILE} run on an ancestor of the tagged commit, at most "
        f"{MAX_AGE_HOURS} hours old."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check")
    check.add_argument("--head-sha", required=True)
    check.add_argument("--github-repository", required=True)
    check.add_argument("--repo-dir", default=".")
    check.set_defaults(handler=_handle_check)
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
