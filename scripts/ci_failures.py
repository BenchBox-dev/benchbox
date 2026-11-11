#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci_text_edit_verdict

CLI_DESCRIPTION = "Print the failing lines of the failed CI jobs of a pull request head or of one Actions run."
USAGE = "usage: make pr-status PR=<number> | RUN=<run id> [REPO=<owner/name>] [LINES=<per job, 5-200>]"

DEFAULT_REPOSITORY = "BenchBox-dev/BenchBox"
DEFAULT_LINES = 40
MIN_LINES = 5
MAX_LINES = 200
CONTEXT_LINES = 8
DETAIL_LINES = 12
DETAIL_PREFIX = "E   "
FAILED_CONCLUSIONS = ("failure", "timed_out", "cancelled", "startup_failure")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$")
NUMBER_RE = re.compile(r"^[1-9][0-9]{0,18}$")
TIMESTAMP_RE = re.compile(r"^\ufeff?\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z ?")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
ERROR_MARKER = "##[error]"
INTERESTING_RE = re.compile(
    r"##\[error\]"
    r"|^(?:FAILED|ERROR) "
    r"|^E   "
    r"|FAILED: guard-"
    r"|GATE FAILURE"
    r"|\bis stale\b"
    r"|\berror\[[a-z0-9-]+\]"
    r"|^make(?:\[\d+\])?: \*\*\*"
    r"|^Traceback \(most recent call last\)"
    r"|Would reformat"
)
RUNS_JQ = '.workflow_runs[] | [.id, .workflow_id, .name, .display_title, .status, (.conclusion // "")] | @tsv'
RUN_JQ = '[.id, .workflow_id, .name, .display_title, .status, (.conclusion // "")] | @tsv'
JOBS_JQ = (
    '.jobs[] | {id, name, status, conclusion: (.conclusion // ""), '
    'steps: [(.steps // [])[] | select(.conclusion == "failure" or .conclusion == "timed_out" '
    'or .conclusion == "cancelled") | .name]}'
)

Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


class CiFailuresError(RuntimeError):
    pass


@dataclass(frozen=True)
class Run:
    run_id: int
    workflow_id: int
    name: str
    title: str
    status: str
    conclusion: str

    @property
    def placeholder(self) -> bool:
        return self.title == ci_text_edit_verdict.TEXT_EDIT_RUN_NAME


@dataclass(frozen=True)
class Job:
    job_id: int
    name: str
    conclusion: str
    steps: tuple[str, ...]


def _subprocess_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)


def _gh(runner: Runner, arguments: list[str]) -> str:
    try:
        result = runner(["gh", *arguments])
    except OSError as exc:
        raise CiFailuresError("could not execute the GitHub CLI") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        raise CiFailuresError(f"gh {arguments[0]} failed: {detail[-1][:200] if detail else 'no output'}")
    return result.stdout


def pr_head(runner: Runner, repo: str, pr: int) -> str:
    out = _gh(runner, ["pr", "view", str(pr), "--repo", repo, "--json", "headRefOid", "--jq", ".headRefOid"]).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", out):
        raise CiFailuresError(f"could not read the head of PR #{pr}")
    return out


def _parse_run(line: str) -> Run | None:
    fields = line.split("\t")
    if len(fields) != 6 or not fields[0].isdigit() or not fields[1].isdigit():
        return None
    return Run(int(fields[0]), int(fields[1]), fields[2], fields[3], fields[4], fields[5])


def runs_for_head(runner: Runner, repo: str, head: str) -> list[Run]:
    out = _gh(
        runner,
        ["api", "--paginate", "--jq", RUNS_JQ, f"repos/{repo}/actions/runs?head_sha={head}&per_page=100"],
    )
    return [run for run in map(_parse_run, out.splitlines()) if run is not None]


def one_run(runner: Runner, repo: str, run_id: int) -> Run:
    run = _parse_run(_gh(runner, ["api", "--jq", RUN_JQ, f"repos/{repo}/actions/runs/{run_id}"]).strip())
    if run is None:
        raise CiFailuresError(f"could not read run {run_id}")
    return run


def failed_jobs(runner: Runner, repo: str, run_id: int) -> list[Job]:
    out = _gh(
        runner,
        ["api", "--paginate", "--jq", JOBS_JQ, f"repos/{repo}/actions/runs/{run_id}/jobs?filter=latest&per_page=100"],
    )
    jobs: list[Job] = []
    for line in out.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict) or record.get("conclusion") not in FAILED_CONCLUSIONS:
            continue
        steps = tuple(str(step) for step in record.get("steps") or ())
        jobs.append(Job(int(record["id"]), str(record.get("name", "")), str(record["conclusion"]), steps))
    return jobs


def job_log(runner: Runner, repo: str, job_id: int) -> str:
    return _gh(runner, ["api", "--allow-escape-sequences", f"repos/{repo}/actions/jobs/{job_id}/logs"])


def plain(value: str) -> str:
    text = ANSI_RE.sub("", value)
    text = "".join(f"\\x{ord(char):02x}" if ord(char) < 32 or ord(char) == 127 else char for char in text)
    text = text.replace("##[", "#[")
    return f"\\{text}" if text.lstrip().startswith("::") else text


def clean_lines(log: str) -> list[str]:
    return [ANSI_RE.sub("", TIMESTAMP_RE.sub("", line)).rstrip() for line in log.splitlines()]


def excerpt(log: str, limit: int = DEFAULT_LINES) -> list[str]:
    lines = clean_lines(log)
    details = [index for index, line in enumerate(lines) if line.startswith(DETAIL_PREFIX)]
    dropped = set(details[DETAIL_LINES:])
    matched = [index for index, line in enumerate(lines) if INTERESTING_RE.search(line) and index not in dropped]
    first_error = next((index for index, line in enumerate(lines) if ERROR_MARKER in line), None)
    context: list[int] = []
    if first_error is not None:
        start = max(0, first_error - CONTEXT_LINES)
        context = [index for index in range(start, first_error) if lines[index].strip()]
    elif not matched:
        context = [index for index in range(max(0, len(lines) - CONTEXT_LINES), len(lines)) if lines[index].strip()]
    chosen = sorted((set(matched) | set(context)) - dropped)
    selected: list[str] = []
    seen: set[str] = set()
    for index in chosen:
        text = plain(lines[index])
        if text in seen:
            continue
        seen.add(text)
        selected.append(text)
    if len(selected) <= limit:
        return selected
    head = limit // 2
    tail = limit - head - 1
    omitted = len(selected) - head - tail
    return [*selected[:head], f"... {omitted} more matching line(s) omitted ...", *selected[len(selected) - tail :]]


def report_run(runner: Runner, repo: str, run: Run, limit: int) -> tuple[list[str], int]:
    header = f"run {run.run_id} {plain(run.name)} ({run.status}{', ' + run.conclusion if run.conclusion else ''})"
    if run.placeholder:
        return [f"{header}: placeholder for a title or body edit; the full CI run decides"], 0
    jobs = failed_jobs(runner, repo, run.run_id)
    if not jobs:
        if run.conclusion not in FAILED_CONCLUSIONS:
            return [], 0
        return [header, f"  the run ended {run.conclusion} with no failed job recorded; open the run to see why"], 1
    out = [header]
    for job in jobs:
        steps = ", ".join(plain(step) for step in job.steps) or "no failed step recorded"
        out.append(f"  job {plain(job.name)} [{job.conclusion}] failed step: {steps}")
        try:
            body = excerpt(job_log(runner, repo, job.job_id), limit)
        except CiFailuresError as exc:
            out.append(f"    log not available: {exc}")
            continue
        out.extend(f"    {line}" for line in body or ["no failing line recognised; read the job log"])
    return out, len(jobs)


def current_runs(runs: Sequence[Run]) -> list[Run]:
    newest: dict[int, Run] = {}
    for run in sorted(runs, key=lambda item: item.run_id):
        kept = newest.get(run.workflow_id)
        if kept is None or kept.placeholder or not run.placeholder:
            newest[run.workflow_id] = run
    return sorted(newest.values(), key=lambda item: item.run_id)


def report(runner: Runner, repo: str, *, pr: int | None, run_id: int | None, limit: int = DEFAULT_LINES) -> list[str]:
    if pr is not None:
        head = pr_head(runner, repo, pr)
        runs = current_runs(runs_for_head(runner, repo, head))
        lines = [f"ci-failures: PR #{pr} head {head[:9]}"]
        if not runs:
            return [*lines, "no Actions run found for this head"]
    else:
        assert run_id is not None
        runs = [one_run(runner, repo, run_id)]
        lines = [f"ci-failures: run {run_id}"]
    total = 0
    in_progress = 0
    for run in runs:
        body, count = report_run(runner, repo, run, limit)
        lines.extend(body)
        total += count
        in_progress += run.status != "completed"
    if total == 0:
        lines.append("no failed job found")
    if in_progress:
        lines.append(f"{in_progress} run(s) still in progress; later jobs may still fail")
    return lines


def _number(text: str, name: str) -> int:
    if not NUMBER_RE.fullmatch(text):
        raise ValueError(f"{name} must be a positive number")
    return int(text)


def main(
    argv: Sequence[str] | None = None, environ: Mapping[str, str] = os.environ, runner: Runner | None = None
) -> int:
    if argv and list(argv) in (["-h"], ["--help"]):
        print(f"{CLI_DESCRIPTION}\n{USAGE}")
        return 0
    if argv:
        print(USAGE, file=sys.stderr)
        return 2
    repo = environ.get("CI_FAILURES_REPO") or DEFAULT_REPOSITORY
    pr_text = environ.get("CI_FAILURES_PR", "")
    run_text = environ.get("CI_FAILURES_RUN", "")
    lines_text = environ.get("CI_FAILURES_LINES", "")
    try:
        if not REPOSITORY_RE.fullmatch(repo):
            raise ValueError("REPO must be owner/name")
        if bool(pr_text) == bool(run_text):
            raise ValueError("pass exactly one of PR or RUN")
        pr = _number(pr_text, "PR") if pr_text else None
        run_id = _number(run_text, "RUN") if run_text else None
        limit = _number(lines_text, "LINES") if lines_text else DEFAULT_LINES
        if not MIN_LINES <= limit <= MAX_LINES:
            raise ValueError(f"LINES must be from {MIN_LINES} to {MAX_LINES}")
    except ValueError as exc:
        print(f"{exc}\n{USAGE}", file=sys.stderr)
        return 2
    try:
        print("\n".join(report(runner or _subprocess_runner, repo, pr=pr, run_id=run_id, limit=limit)))
        return 0
    except CiFailuresError as exc:
        print(f"ci-failures: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
