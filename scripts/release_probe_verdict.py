from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from typing import Any

from scripts.release_probe_launch import CHECK_NAME, VARIANTS, WORKFLOW_FILE, gh, run_url

Gh = Callable[..., Any]
RELEASE_WORKFLOW_PATH = f".github/workflows/{WORKFLOW_FILE}"
CHECKS_PAGE = 100
MAX_PAGES = 30
IN_PROGRESS = "in_progress"
COMPLETED = "completed"


def parse_external_id(external_id: str | None) -> dict[str, int]:
    recorded: dict[str, int] = {}
    for part in (external_id or "").split(";"):
        variant, _, run_id = part.partition("=")
        if variant in VARIANTS and run_id.isdigit():
            recorded[variant] = int(run_id)
    distinct = len(set(recorded.values())) == len(VARIANTS)
    return recorded if set(recorded) == set(VARIANTS) and distinct else {}


def probe_checks(repository: str, head_sha: str, runner: Gh = gh) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        payload = runner(
            "api",
            f"repos/{repository}/commits/{head_sha}/check-runs"
            f"?filter=all&per_page={CHECKS_PAGE}&page={page}&check_name={CHECK_NAME.replace(' ', '%20')}",
        )
        rows = list(payload.get("check_runs", []))
        checks.extend(rows)
        if len(rows) < CHECKS_PAGE:
            break
    return checks


def find_check(checks: list[dict[str, Any]], run_id: int) -> tuple[dict[str, Any], dict[str, int]] | None:
    for check in checks:
        if check.get("name") != CHECK_NAME or check.get("status") != IN_PROGRESS:
            continue
        recorded = parse_external_id(check.get("external_id"))
        if run_id in recorded.values():
            return check, recorded
    return None


def complete_check(repository: str, check_id: int, conclusion: str, title: str, summary: str, runner: Gh = gh) -> None:
    runner(
        "api",
        "--method",
        "PATCH",
        f"repos/{repository}/check-runs/{check_id}",
        "-f",
        f"status={COMPLETED}",
        "-f",
        f"conclusion={conclusion}",
        "-f",
        f"output[title]={title}",
        "-f",
        f"output[summary]={summary}",
    )


def is_probe_run(run: dict[str, Any], head_sha: str) -> bool:
    return (
        str(run.get("path", "")).split("@", 1)[0] == RELEASE_WORKFLOW_PATH
        and run.get("event") == "workflow_dispatch"
        and run.get("head_sha") == head_sha
    )


def judge(repository: str, recorded: dict[str, int], head_sha: str, runner: Gh = gh) -> tuple[str, str, str] | None:
    runs = {variant: runner("api", f"repos/{repository}/actions/runs/{run_id}") for variant, run_id in recorded.items()}
    foreign = [
        f"- {variant}: {run_url(repository, recorded[variant])}"
        for variant, run in runs.items()
        if not is_probe_run(run, head_sha)
    ]
    if foreign:
        return (
            "failure",
            "Probe records foreign runs",
            f"These recorded runs are not release-v2.yml dispatches at {head_sha[:10]}:\n" + "\n".join(foreign),
        )
    if any(run.get("status") != COMPLETED for run in runs.values()):
        return None
    failed = [
        f"- {variant}: {run.get('conclusion')} ({run.get('html_url') or run_url(repository, recorded[variant])})"
        for variant, run in runs.items()
        if run.get("conclusion") != "success"
    ]
    if failed:
        return "failure", "Probe failed", "These release-v2 probe variants did not succeed:\n" + "\n".join(failed)
    links = "\n".join(f"- {variant}: {run_url(repository, run_id)}" for variant, run_id in recorded.items())
    return "success", "Probe passed", f"Every release-v2 probe variant succeeded.\n\n{links}"


def reconcile(repository: str, check_id: int, recorded: dict[str, int], head_sha: str, runner: Gh = gh) -> str:
    verdict = judge(repository, recorded, head_sha, runner)
    if verdict is None:
        return "waiting: probe variants still running"
    conclusion, title, summary = verdict
    complete_check(repository, check_id, conclusion, title, summary, runner)
    return f"completed the probe check as {conclusion}"


def reconcile_head(repository: str, head_sha: str, runner: Gh = gh) -> str | None:
    open_checks = [
        (check, recorded)
        for check in probe_checks(repository, head_sha, runner)
        if check.get("name") == CHECK_NAME
        and check.get("status") == IN_PROGRESS
        and (recorded := parse_external_id(check.get("external_id")))
    ]
    if not open_checks:
        return None
    return "; ".join(
        reconcile(repository, int(check["id"]), recorded, head_sha, runner) for check, recorded in open_checks
    )


def settle(repository: str, run_id: int, runner: Gh = gh) -> str:
    run = runner("api", f"repos/{repository}/actions/runs/{run_id}")
    if run.get("event") != "workflow_dispatch" or run.get("status") != COMPLETED:
        return "ignored: not a completed dispatched run"
    found = find_check(probe_checks(repository, str(run["head_sha"]), runner), run_id)
    if found is None:
        return "ignored: no in-progress probe check records this run"
    check, recorded = found
    return reconcile(repository, int(check["id"]), recorded, str(run["head_sha"]), runner)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Complete the release-v2 probe check when its variant runs finish.")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-id", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        print(settle(args.repository, args.run_id))
    except RuntimeError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
