from __future__ import annotations

import hashlib
import os
import signal
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from . import absence, credentials, evidence, omp_pin, processes, protocol
from .commands import Invocation, build
from .policy import Reviewer
from .verdict import (
    INCOMPLETE,
    SHIP,
    VerdictError,
    extract_omp_text,
    normalize_reviewer,
    omp_stream_without_answer,
    parse_output,
    shape_fingerprint,
    validate,
)


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
    data = source.read_bytes()
    if not data.decode("utf-8").strip():
        return None
    exclude_path = workspace / exclude
    try:
        exclude_path.parent.mkdir(parents=True, exist_ok=True)
        with exclude_path.open("a", encoding="utf-8") as handle:
            handle.write(f"/{STAGED_DIFF_NAME}\n")
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
    except OSError:
        return None
    return target


def _digest(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    except OSError:
        return "unreadable"


def execute(invocation: Invocation, timeout_seconds: int) -> RunResult:
    with (
        tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace", dir=invocation.cwd) as stdout_file,
        tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace", dir=invocation.cwd) as stderr_file,
    ):
        try:
            process = subprocess.Popen(
                list(invocation.argv),
                cwd=invocation.cwd,
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=True,
                env=dict(invocation.env) if invocation.env is not None else None,
            )
        except OSError as exc:
            return RunResult(None, "", str(exc), False)
        timed_out = False
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            timed_out = True
        survivors = processes.confirm_exit(process.pid)
        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout, stderr = stdout_file.read(), stderr_file.read()
    if survivors:
        raise processes.HarnessLeakError(survivors)
    return RunResult(process.returncode, stdout, stderr, timed_out)


def reset_workspace(workspace: Path) -> None:
    _git(workspace, "reset", "--hard", "--quiet", "HEAD")
    _git(workspace, "clean", "-ffdxq")


def _credentials() -> tuple[str, ...]:
    return credentials.known()


def _diagnostic(harness: str, result: RunResult) -> str:
    stdout = "" if harness == "omp" else result.stdout
    return absence.excerpt(stdout, result.stderr, _credentials())


def _unparsed(harness: str, output: str, fallback: str) -> str:
    text = extract_omp_text(output) if harness == "omp" else output
    return absence.unparsed(text, _credentials()) if text.strip() else fallback


def _with_trace(diagnostic: str, harness: str, stdout: str, plan: Mapping[str, Any], workspace: Path) -> str:
    if harness not in evidence.TRACED:
        return diagnostic
    summary = evidence.describe(evidence.trace(harness, stdout), plan.get("evidence_files", ()), workspace)
    return f"{diagnostic} | {summary}" if diagnostic else summary


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
    try:
        omp_pin.required_binary()
    except omp_pin.PinError as exc:
        return ReviewOutcome(None, absence.Absence(absence.ERROR, str(exc)), "")
    scratch.mkdir(parents=True, exist_ok=True)
    invocation = build(reviewer, workspace, prompt, scratch)
    staged_before = _digest(workspace / STAGED_DIFF_NAME)
    result = execute(invocation, reviewer.timeout_minutes * 60)
    output = result.stdout
    if invocation.output_file is not None:
        output = invocation.output_file.read_text(encoding="utf-8") if invocation.output_file.is_file() else ""
    diagnostic = _diagnostic(reviewer.harness, result)
    missing = absence.classify(
        reviewer.harness,
        result.exit_code,
        output,
        result.stderr,
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
    if reviewer.harness == "omp" and omp_stream_without_answer(output):
        missing = absence.Absence(absence.EMPTY, "the reviewer ended without a final answer")
        return ReviewOutcome(None, missing, _with_trace(diagnostic, reviewer.harness, result.stdout, plan, workspace))
    payload: Any = None
    parsed = False
    rewrites: tuple[str, ...] = ()
    try:
        payload = parse_output(reviewer.harness, output)
        parsed = True
        normalized, rewrites = normalize_reviewer(payload)
        verdict = validate(normalized)
    except VerdictError as exc:
        unparsed = _with_trace(
            _unparsed(reviewer.harness, output, diagnostic), reviewer.harness, result.stdout, plan, workspace
        )
        if parsed:
            fingerprint = shape_fingerprint(payload, _credentials())
            unparsed = f"shape={fingerprint}\n{unparsed}"
        if rewrites:
            unparsed = f"rewrites={','.join(rewrites)}\n{unparsed}"
        detail = absence.redact_detail(str(exc), _credentials())
        return ReviewOutcome(None, absence.Absence(absence.INVALID, detail), unparsed)
    traced = _with_trace(diagnostic, reviewer.harness, result.stdout, plan, workspace)
    if rewrites:
        traced = f"rewrites={','.join(rewrites)}\n{traced}"
    if verdict.status == INCOMPLETE:
        reason = verdict.incomplete_reason or "the reviewer reported its review incomplete"
        return ReviewOutcome(None, absence.Absence(absence.INCOMPLETE, reason), traced)
    verdict = evidence.mark_citations(verdict, workspace, protocol.disputed_claims(plan))
    problem = protocol.contradiction(verdict, plan)
    if problem is not None:
        return ReviewOutcome(None, absence.Absence(absence.INVALID, problem), traced)
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
        return ReviewOutcome(None, absence.Absence(absence.INCOMPLETE, problem), traced)
    return ReviewOutcome(verdict.to_json(), None, traced)
