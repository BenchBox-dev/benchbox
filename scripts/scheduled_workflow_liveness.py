#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

CLI_DESCRIPTION = "Fail when a workflow with a schedule trigger has no recent scheduled run."

ON_BLOCK = re.compile(r"(?ms)^(?:\"on\"|'on'|on):[^\n]*\n(.*?)(?=^\S|\Z)")
SCHEDULE_KEY = re.compile(r"(?m)^ {1,4}schedule:\s*(#.*)?$")
CRON_LINE = re.compile(r"(?m)^\s*-\s*cron:\s*['\"]?([^'\"#\n]+)")

RUNS_PER_PAGE = 30
RECENT_RUNS_SHOWN = 10
FETCH_ATTEMPTS = 3
FETCH_TIMEOUT_SECONDS = 30
USER_AGENT = "benchbox-scheduled-workflow-liveness"

RunFetcher = Callable[[str, datetime], list[dict]]
RegistrationFetcher = Callable[[str], datetime | None]
HistoryFetcher = Callable[[str], list[dict]]


class LivenessError(RuntimeError):
    pass


@dataclass(frozen=True)
class Verdict:
    workflow: str
    alive: bool
    message: str
    recent_runs: tuple[dict, ...] = ()


def cadence_window_days(cron_expressions: Iterable[str]) -> int:
    windows = []
    for expression in cron_expressions:
        fields = expression.split()
        if len(fields) != 5:
            continue
        _minute, _hour, day_of_month, month, day_of_week = fields
        if month != "*":
            windows.append(100)
        elif day_of_month != "*":
            windows.append(35)
        elif day_of_week != "*":
            windows.append(9)
        else:
            windows.append(3)
    return min(windows) if windows else 3


def scheduled_workflows(workflow_dir: Path) -> list[tuple[str, list[str]]]:
    found = []
    for path in sorted([*workflow_dir.glob("*.yml"), *workflow_dir.glob("*.yaml")]):
        on_block = ON_BLOCK.search(path.read_text(encoding="utf-8"))
        if on_block and SCHEDULE_KEY.search(on_block.group(1)):
            found.append((path.name, CRON_LINE.findall(on_block.group(1))))
    return found


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def newest_first(runs: Iterable[dict]) -> list[dict]:
    dated = [(parse_timestamp(run.get("created_at")), run) for run in runs]
    usable = [(created, run) for created, run in dated if created is not None]
    return [run for _, run in sorted(usable, key=lambda pair: pair[0], reverse=True)]


def describe_run(run: dict) -> str:
    return (
        f"id={run.get('id')} created_at={run.get('created_at')} run_started_at={run.get('run_started_at')} "
        f"status={run.get('status')} conclusion={run.get('conclusion')} head_branch={run.get('head_branch')}"
    )


def assess(
    workflow: str,
    runs: Sequence[dict],
    *,
    window_days: int,
    now: datetime,
    registered_at: datetime | None,
) -> Verdict:
    ordered = newest_first(runs)
    if not ordered:
        if registered_at is not None and now - registered_at <= timedelta(days=window_days):
            return Verdict(
                workflow,
                True,
                f"{workflow}: registered {registered_at.date()}, no scheduled run yet -- "
                f"within its {window_days}-day activation grace.",
            )
        return Verdict(
            workflow,
            False,
            f"{workflow}: no scheduled runs at all -- absent from the default branch, or its cron is "
            "disabled/never fires",
        )
    latest = ordered[0]
    created = parse_timestamp(latest["created_at"])
    assert created is not None
    if now - created > timedelta(days=window_days):
        return Verdict(
            workflow,
            False,
            f"{workflow}: latest scheduled run {latest['created_at']} is older than its {window_days}-day "
            "cadence window",
            tuple(ordered[:RECENT_RUNS_SHOWN]),
        )
    return Verdict(workflow, True, f"{workflow}: latest scheduled run {latest['created_at']}")


def check_workflows(
    workflows: Sequence[tuple[str, list[str]]],
    fetch_runs: RunFetcher,
    fetch_registration: RegistrationFetcher,
    fetch_history: HistoryFetcher,
    now: datetime,
) -> list[Verdict]:
    verdicts = []
    for name, crons in workflows:
        window_days = cadence_window_days(crons)
        runs = fetch_runs(name, now - timedelta(days=window_days + 1))
        history = None
        if not runs:
            history = fetch_history(name)
            runs = history
        registered_at = fetch_registration(name) if not runs else None
        verdict = assess(name, runs, window_days=window_days, now=now, registered_at=registered_at)
        if not verdict.alive:
            if history is None:
                history = fetch_history(name)
            verdict = replace(verdict, recent_runs=tuple(newest_first(history)[:RECENT_RUNS_SHOWN]))
        verdicts.append(verdict)
    return verdicts


def report(verdicts: Sequence[Verdict], emit: Callable[[str], None]) -> int:
    dead = [verdict for verdict in verdicts if not verdict.alive]
    if not verdicts:
        emit("::error::No scheduled workflows found in the checked-out tree; the liveness scan itself is broken.")
        return 1
    if dead:
        emit(
            "::error::Scheduled workflow(s) with no recent scheduled run detected "
            "(see docs/internal/repo-admin-settings.md 'Scheduled activation'):"
        )
        for verdict in dead:
            emit(f"::error::  {verdict.message}")
            for run in verdict.recent_runs:
                emit(f"  {verdict.workflow}: {describe_run(run)}")
        return 1
    emit(
        f"All {len(verdicts)} scheduled workflow(s) alive within their cadence windows (run conclusions are not assessed)."
    )
    return 0


class GitHubApi:
    def __init__(self, repository: str, token: str, api_url: str, sleep: Callable[[float], None] = time.sleep):
        self.repository = repository
        self.token = token
        self.api_url = api_url
        self.sleep = sleep

    def _request(self, url: str) -> urllib.request.Request:
        headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return urllib.request.Request(url, headers=headers)

    def _get_json(self, url: str):
        last_error: Exception | None = None
        for attempt in range(FETCH_ATTEMPTS):
            try:
                with urllib.request.urlopen(self._request(url), timeout=FETCH_TIMEOUT_SECONDS) as response:
                    return json.load(response)
            except urllib.error.HTTPError as error:
                if error.code == 404:
                    return None
                if error.code in (401, 403):
                    raise LivenessError(
                        f"GitHub API auth/rate-limit failure ({error.code}) for {url}; cannot assess liveness."
                    ) from error
                last_error = error
            except (urllib.error.URLError, TimeoutError, ValueError) as error:
                last_error = error
            self.sleep(5 * (attempt + 1))
        raise LivenessError(f"GitHub API unreachable after retries for {url}: {last_error}")

    def scheduled_runs(self, workflow_file: str, since: datetime) -> list[dict]:
        query = urllib.parse.urlencode(
            {"event": "schedule", "per_page": RUNS_PER_PAGE, "created": f">={since.strftime('%Y-%m-%d')}"}
        )
        payload = self._get_json(
            f"{self.api_url}/repos/{self.repository}/actions/workflows/{workflow_file}/runs?{query}"
        )
        return list((payload or {}).get("workflow_runs", []))

    def recent_scheduled_runs(self, workflow_file: str) -> list[dict]:
        query = urllib.parse.urlencode({"event": "schedule", "per_page": RECENT_RUNS_SHOWN})
        payload = self._get_json(
            f"{self.api_url}/repos/{self.repository}/actions/workflows/{workflow_file}/runs?{query}"
        )
        return list((payload or {}).get("workflow_runs", []))

    def registered_at(self, workflow_file: str) -> datetime | None:
        anchors = []
        try:
            payload = self._get_json(f"{self.api_url}/repos/{self.repository}/actions/workflows/{workflow_file}")
            if isinstance(payload, dict):
                anchors.append(parse_timestamp(payload.get("created_at")))
        except LivenessError:
            pass
        try:
            path = urllib.parse.quote(f".github/workflows/{workflow_file}")
            commits = self._get_json(f"{self.api_url}/repos/{self.repository}/commits?path={path}&per_page=1")
            if isinstance(commits, list) and commits:
                anchors.append(parse_timestamp(commits[0].get("commit", {}).get("committer", {}).get("date")))
        except LivenessError:
            pass
        known = [anchor for anchor in anchors if anchor is not None]
        return max(known) if known else None


def main(environ: dict[str, str] | None = None, emit: Callable[[str], None] = print) -> int:
    env = os.environ if environ is None else environ
    workflow_dir = Path(env.get("WORKFLOWS_DIR", ".github/workflows"))
    api = GitHubApi(
        env["GITHUB_REPOSITORY"],
        env.get("GITHUB_TOKEN", ""),
        env.get("GITHUB_API_URL", "https://api.github.com"),
    )
    now = datetime.now(timezone.utc)
    try:
        verdicts = check_workflows(
            scheduled_workflows(workflow_dir),
            api.scheduled_runs,
            api.registered_at,
            api.recent_scheduled_runs,
            now,
        )
    except LivenessError as error:
        emit(f"::error::{error}")
        return 1
    for verdict in verdicts:
        if verdict.alive:
            emit(verdict.message)
    return report(verdicts, emit)


if __name__ == "__main__":
    sys.exit(main())
