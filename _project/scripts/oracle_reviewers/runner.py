from __future__ import annotations

import hashlib
import os
import shutil
import signal
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from . import absence, evidence, protocol
from .commands import Invocation, build
from .policy import Reviewer
from .verdict import INCOMPLETE, SHIP, VerdictError, parse_output, validate


@dataclass(frozen=True)
class RunResult:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool


@dataclass(frozen=True)
class ReviewOutcome:
    verdict: dict[str, Any] | None
    missing: absence.Absence | None
    diagnostic: str


def _git(workspace: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(workspace), *args], capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


STAGED_DIFF_NAME = ".oracle-pull-request.diff"


def stage_pull_request_diff(source: Path, workspace: Path) -> Path | None:
    target = workspace / STAGED_DIFF_NAME
    exclude = _git(workspace, "rev-parse", "--git-path", "info/exclude")
    if not source.is_file() or not exclude or os.path.lexists(target):
        return None
    text = source.read_text(encoding="utf-8")
    if not text.strip():
        return None
    exclude_path = workspace / exclude
    try:
        exclude_path.parent.mkdir(parents=True, exist_ok=True)
        with exclude_path.open("a", encoding="utf-8") as handle:
            handle.write(f"/{STAGED_DIFF_NAME}\n")
        target.write_text(text, encoding="utf-8")
    except OSError:
        return None
    return target


def _digest(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    except OSError:
        return "unreadable"


def execute(invocation: Invocation, timeout_seconds: int) -> RunResult:
    try:
        process = subprocess.Popen(
            list(invocation.argv),
            cwd=invocation.cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except OSError as exc:
        return RunResult(None, "", str(exc), False)
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate()
        return RunResult(process.returncode, stdout or "", stderr or "", True)
    return RunResult(process.returncode, stdout or "", stderr or "", False)


def _diagnostic(result: RunResult, events: str) -> str:
    credentials = [os.environ.get(name, "") for name in absence.CREDENTIAL_ENV]
    return absence.excerpt(result.stdout, f"{result.stderr}\n{events}", credentials)


def review(
    reviewer: Reviewer,
    *,
    workspace: Path,
    head_sha: str,
    prompt: str,
    scratch: Path,
    now: datetime,
    plan: Mapping[str, Any],
) -> ReviewOutcome:
    if _git(workspace, "rev-parse", "HEAD") != head_sha:
        return ReviewOutcome(None, absence.Absence(absence.ERROR, "the workspace is not at the reviewed head"), "")
    if not prompt:
        return ReviewOutcome(None, absence.Absence(absence.INVALID, "the brief is empty or truncated"), "")
    if shutil.which(reviewer.harness) is None:
        return ReviewOutcome(None, absence.Absence(absence.ERROR, f"{reviewer.harness} is not installed"), "")
    credential = absence.REQUIRED_CREDENTIAL.get(reviewer.harness)
    if credential is not None and not os.environ.get(credential):
        return ReviewOutcome(None, absence.Absence(absence.AUTH, f"{credential} is not set"), "")
    scratch.mkdir(parents=True, exist_ok=True)
    invocation = build(reviewer, workspace, prompt, scratch)
    staged_before = _digest(workspace / STAGED_DIFF_NAME)
    result = execute(invocation, reviewer.timeout_minutes * 60)
    output = result.stdout
    if invocation.output_file is not None:
        output = invocation.output_file.read_text(encoding="utf-8") if invocation.output_file.is_file() else ""
    events = evidence.codex_errors(result.stdout) if reviewer.harness == "codex" else ""
    diagnostic = _diagnostic(result, events)
    missing = absence.classify(
        reviewer.harness,
        result.exit_code,
        output,
        f"{result.stderr}\n{result.stdout}\n{events}",
        timed_out=result.timed_out,
        now=now,
    )
    if _git(workspace, "status", "--porcelain", "--untracked-files=all"):
        return ReviewOutcome(None, absence.Absence(absence.INVALID, "the reviewer changed the workspace"), diagnostic)
    if _digest(workspace / STAGED_DIFF_NAME) != staged_before:
        reason = "the reviewer changed the staged pull request diff"
        return ReviewOutcome(None, absence.Absence(absence.INVALID, reason), diagnostic)
    if missing.absent:
        return ReviewOutcome(None, missing, diagnostic)
    try:
        verdict = validate(parse_output(reviewer.harness, output))
    except VerdictError as exc:
        return ReviewOutcome(None, absence.Absence(absence.INVALID, str(exc)), diagnostic)
    if verdict.status == INCOMPLETE:
        reason = verdict.incomplete_reason or "the reviewer reported its review incomplete"
        return ReviewOutcome(None, absence.Absence(absence.INCOMPLETE, reason), diagnostic)
    verdict = evidence.mark_citations(verdict, workspace)
    problem = evidence.check(
        verdict,
        ships=protocol.judge_planned(verdict, plan).decision == SHIP,
        harness=reviewer.harness,
        brief_mode=plan["brief_mode"],
        workspace=workspace,
        required=tuple(plan.get("evidence_files", ())),
        run=evidence.trace(reviewer.harness, result.stdout),
    )
    if problem is not None:
        return ReviewOutcome(None, absence.Absence(absence.INCOMPLETE, problem), diagnostic)
    return ReviewOutcome(verdict.to_json(), None, diagnostic)
