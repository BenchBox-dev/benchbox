#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

WORKFLOW_FILE = "trunk-uat.yml"
WORKFLOW_PATH = ".github/workflows/trunk-uat.yml"
RELEASE_WORKFLOW_FILE = "release-v2.yml"
RECORD_PREFIX = "trunk-uat-record-"
RECORD_NAME = re.compile(re.escape(RECORD_PREFIX) + r"([0-9a-f]{40})")
SHARD_DIRECTORY = re.compile(r"release-uat-trunk-[0-9a-f]{40}-stage([0-9]+)-(.+)")
SHARD_JOB = re.compile(r"UAT stage ([0-9]+) (.+)")
RECORD_FILE = "trunk-uat-record.json"
HISTORY_FILE = "cells-history.jsonl"
ADMISSION_RECEIPT = "admission-receipt.json"
HISTORY_SCHEMA = 1
BASELINE_RUN_LIMIT = 30
RUNNER_HOUR_GATE = 30.0
JOB_HOUR_LIMIT = 6.0
SHARD_TIMEOUT_HOURS = 5.9
STEP_TIMEOUT_MINUTES = 345
STEP_TIMEOUT_SLACK_SECONDS = 60
RUN_STEP = re.compile(r"Run the stage [0-9]+ shard against the trunk distribution")
LOG_TAIL_BYTES = 64 * 1024
HARNESS_FAILURE_FIELDS = (
    "failure_kind",
    "failure_signature",
    "retry_signature",
    "retry_outcome",
    "first_attempt_status",
    "first_attempt_elapsed_s",
)
_SHA = re.compile(r"[0-9a-f]{40}")
_PEAK_RSS = re.compile(r"^# UAT_PEAK_RSS_GIB=([0-9]+(?:\.[0-9]+)?)\s*$", re.MULTILINE)

Api = Callable[[str], dict[str, Any]]
Fetch = Callable[[int], bytes]


def _sibling(name: str):
    path = Path(__file__).with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"_trunk_uat_{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"{path.name} cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_consumer = _sibling("release_artifact_consumer")
_releasable = _sibling("release_releasable")
_reuse = _sibling("release_reuse_uat_evidence")

SKIP_PATHS = tuple(dict.fromkeys((*_reuse.GUARDED_TREES, "benchbox", "uv.lock", WORKFLOW_PATH, _reuse.WORKFLOW_PATH)))
GREEN = _reuse.VERDICT_GREEN


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    source_sha: str = ""
    baseline_sha: str = ""
    baseline_run_id: int = 0


def skip_path_changes(changed: list[str]) -> list[str]:
    return sorted(
        path for path in changed if any(path == guarded or path.startswith(guarded + "/") for guarded in SKIP_PATHS)
    )


def git_changed_paths(root: Path, base: str, head: str) -> list[str] | None:
    result = subprocess.run(
        ["git", "-C", str(root), "diff", "--no-renames", "--name-only", "-z", base, head],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return [name for name in result.stdout.decode("utf-8", "replace").split("\0") if name]


def _runs(api: Api, path: str) -> list[dict[str, Any]]:
    payload = api(path)
    runs = payload.get("workflow_runs")
    if not isinstance(runs, list):
        raise ValueError("malformed workflow run listing")
    return [run for run in runs if isinstance(run, dict) and isinstance(run.get("id"), int)]


def active_runs(api: Api, workflow: str) -> list[dict[str, Any]]:
    runs = _runs(api, f"actions/workflows/{workflow}/runs?per_page=50")
    return sorted((run for run in runs if run.get("status") != "completed"), key=lambda run: run["id"], reverse=True)


@dataclass(frozen=True)
class ExecutedRun:
    run_id: int
    sha: str
    artifacts: list[dict[str, Any]]


def executed_runs(
    api: Api, *, branch: str, run_limit: int = BASELINE_RUN_LIMIT, exclude_run_id: int | None = None
) -> Iterator[ExecutedRun]:
    runs = _runs(api, f"actions/workflows/{WORKFLOW_FILE}/runs?branch={branch}&per_page={run_limit}")
    runs.sort(key=lambda run: (str(run.get("created_at")), run["id"]), reverse=True)
    for run in runs[:run_limit]:
        if run["id"] == exclude_run_id or run.get("status") != "completed":
            continue
        artifacts = api(f"actions/runs/{run['id']}/artifacts?per_page=100").get("artifacts")
        if not isinstance(artifacts, list):
            raise ValueError("malformed artifact listing")
        live = [artifact for artifact in artifacts if artifact.get("expired") is False]
        for artifact in live:
            match = RECORD_NAME.fullmatch(str(artifact.get("name")))
            if match is not None:
                yield ExecutedRun(run["id"], match.group(1), live)
                break


def baseline_problem(executed: ExecutedRun, fetch: Fetch) -> str | None:
    artifact = next(item for item in executed.artifacts if RECORD_NAME.fullmatch(str(item.get("name"))))
    with zipfile.ZipFile(io.BytesIO(fetch(artifact["id"]))) as archive:
        record = json.loads(archive.read(RECORD_FILE))
    results = record.get("job_results")
    failed = (
        sorted(name for name, result in results.items() if result != "success") if isinstance(results, dict) else []
    )
    if record.get("verdict") != GREEN or failed:
        detail = f"verdict {record.get('verdict')}" + (f", jobs not successful: {', '.join(failed)}" if failed else "")
        return f"baseline run {executed.run_id} at {executed.sha} was not green ({detail})"
    return None


def find_baseline(
    api: Api, *, branch: str, current_run_id: int, run_limit: int = BASELINE_RUN_LIMIT
) -> ExecutedRun | None:
    return next(executed_runs(api, branch=branch, run_limit=run_limit, exclude_run_id=current_run_id), None)


def decide(
    *,
    event: str,
    branch: str,
    run_id: int,
    api: Api,
    changed: Callable[[str, str], list[str] | None],
    fetch: Fetch | None = None,
) -> Decision:
    active = active_runs(api, RELEASE_WORKFLOW_FILE)
    if active:
        return Decision("defer", f"deferred: release-v2 run {active[0]['id']} in progress")
    source = _releasable.newest_releasable(api)
    if source is None:
        raise ValueError(f"no releasable commit among the newest {_releasable.CANDIDATE_LIMIT} successful trunk runs")
    if event != "schedule":
        return Decision("run", f"run: {event} runs unconditionally", source)
    baseline = find_baseline(api, branch=branch, current_run_id=run_id)
    if baseline is None:
        return Decision("run", f"run: no earlier run on {branch} executed shards", source)
    known = Decision("run", "", source, baseline.sha, baseline.run_id)
    problem = baseline_problem(baseline, fetch or _fetch_artifact)
    if problem is not None:
        return _with_reason(known, f"run: {problem}")
    paths = changed(baseline.sha, source)
    if paths is None:
        return _with_reason(known, f"run: skip baseline {baseline.sha} is not in the checkout")
    hits = skip_path_changes(paths)
    if hits:
        listed = ", ".join(hits[:5]) + (", ..." if len(hits) > 5 else "")
        return _with_reason(known, f"run: {len(hits)} guarded path(s) changed since {baseline.sha}: {listed}")
    return Decision("skip", f"skipped: unchanged since {baseline.sha}", source, baseline.sha, baseline.run_id)


def _with_reason(decision: Decision, reason: str) -> Decision:
    return Decision(decision.action, reason, decision.source_sha, decision.baseline_sha, decision.baseline_run_id)


def admission_receipt(sha: str, version: str, producer: dict[str, Any], artifact: dict[str, Any]) -> dict[str, Any]:
    return {
        "tag": f"v{version}",
        "tag_object": sha,
        "head_sha": sha,
        "version": version,
        "producer": producer,
        "artifact_id": artifact["id"],
        "artifact_digest": artifact["digest"],
        "expires_at": artifact["expires_at"],
    }


def _check_distribution_versions(producer: dict[str, Any], version: str) -> None:
    from packaging.utils import parse_sdist_filename, parse_wheel_filename
    from packaging.version import Version

    for name in producer["files"]:
        package, found = parse_wheel_filename(name)[:2] if name.endswith(".whl") else parse_sdist_filename(name)
        _consumer._require(package == "benchbox" and found == Version(version), "distribution package/version mismatch")


def admit(root: Path, sha: str, output: Path, api: Api | None = None) -> dict[str, Any]:
    api = api or _consumer.github_json
    _consumer._require(_SHA.fullmatch(sha) is not None, "invalid commit SHA")
    manifest = _consumer._run_git(
        root, "show", f"{sha}:pyproject.toml", check=True, text=True, stdout=subprocess.PIPE, timeout=60
    ).stdout
    version = tomllib.loads(manifest)["project"]["version"]
    _consumer._require(re.fullmatch(_consumer.TAG_PATTERN, f"v{version}") is not None, "invalid package version")
    run, job, artifact = _consumer.select_producer(sha, api)
    _consumer._require(not output.exists(), "output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    output = output.parent.resolve(strict=True) / output.name
    _consumer._require_private_parent(output.parent)
    with tempfile.TemporaryDirectory(prefix="trunk-uat-admission-", dir=output.parent) as temporary:
        stage = Path(temporary)
        archive = stage / "download.zip"
        _consumer._download(artifact["id"], archive)
        payloads = _consumer.verify_archive(archive, artifact)
        archive.unlink()
        for name, payload in payloads.items():
            (stage / name).write_bytes(payload)
        producer = _consumer.verify_producer_receipt(stage, run, job)
        _check_distribution_versions(producer, version)
        _consumer._verify_committed_binaries(root, sha, [stage / name for name in producer["files"]])
        _consumer._require(
            _consumer.select_producer(sha, api) == (run, job, artifact), "producer changed during admission"
        )
        receipt = admission_receipt(sha, version, producer, artifact)
        (stage / ADMISSION_RECEIPT).write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8", newline="\n")
        _consumer._require(not output.exists(), "output appeared during admission")
        _consumer._publish_output(stage, output)
    return receipt


def peak_rss_gib(log: Path) -> float | None:
    try:
        with log.open("rb") as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - LOG_TAIL_BYTES))
            tail = stream.read().decode("utf-8", "replace")
    except OSError:
        return None
    matches = _PEAK_RSS.findall(tail)
    return float(matches[-1]) if matches else None


def cell_record(row: dict[str, Any], *, run_dir: Path, stage: int, shard: str) -> dict[str, Any]:
    status = str(row.get("status"))
    attempt = row.get("attempt")
    log_path = row.get("log_path")
    record: dict[str, Any] = {
        "stage": stage,
        "shard": shard,
        "platform": row.get("platform"),
        "benchmark": row.get("benchmark"),
        "scale": row.get("scale"),
        "status": status,
        "terminal_state": str(row.get("terminal_state") or status),
        "elapsed_s": row.get("elapsed_s"),
        "peak_rss_gib": peak_rss_gib(run_dir / Path(str(log_path)).name) if log_path else None,
        "attempt": attempt if type(attempt) is int and attempt >= 1 else 1,
    }
    record.update({key: row.get(key) for key in HARNESS_FAILURE_FIELDS})
    return record


def read_shard_cells(evidence: Path) -> tuple[list[dict[str, Any]], list[str]]:
    cells: list[dict[str, Any]] = []
    shards: list[str] = []
    for directory in sorted(evidence.iterdir()) if evidence.is_dir() else []:
        match = SHARD_DIRECTORY.fullmatch(directory.name)
        if match is None:
            continue
        stage, shard = int(match.group(1)), match.group(2)
        shards.append(directory.name)
        run_dir = directory / "run"
        cells_file = run_dir / "cells.jsonl"
        if not cells_file.is_file():
            continue
        for line in cells_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                cells.append(cell_record(json.loads(line), run_dir=run_dir, stage=stage, shard=shard))
    return cells, shards


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    return _consumer._timestamp(value)


def run_jobs(api: Api, run_id: int, attempt: int) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for page in range(1, 101):
        payload = api(f"actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100&page={page}")
        part = payload.get("jobs")
        if not isinstance(part, list):
            raise ValueError("malformed job listing")
        jobs.extend(job for job in part if isinstance(job, dict))
        if len(part) < 100:
            return jobs
    raise ValueError("job listing limit exceeded")


def _hit_step_timeout(job: dict[str, Any]) -> bool:
    for step in job.get("steps") or []:
        if not isinstance(step, dict) or not RUN_STEP.fullmatch(str(step.get("name"))):
            continue
        started, completed = _parse_time(step.get("started_at")), _parse_time(step.get("completed_at"))
        if started is None or completed is None or step.get("conclusion") not in ("failure", "cancelled"):
            continue
        if (completed - started).total_seconds() >= STEP_TIMEOUT_MINUTES * 60 - STEP_TIMEOUT_SLACK_SECONDS:
            return True
    return False


def job_costs(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    shards: list[dict[str, Any]] = []
    total = shard_total = 0.0
    starts: list[datetime] = []
    ends: list[datetime] = []
    for job in jobs:
        started, completed = _parse_time(job.get("started_at")), _parse_time(job.get("completed_at"))
        if started is None or completed is None:
            continue
        hours = max((completed - started).total_seconds(), 0.0) / 3600
        total += hours
        starts.append(started)
        ends.append(completed)
        match = SHARD_JOB.fullmatch(str(job.get("name")))
        if match is not None:
            shard_total += hours
            shards.append(
                {
                    "stage": int(match.group(1)),
                    "shard": match.group(2),
                    "state": job.get("conclusion"),
                    "hours": round(hours, 3),
                    "step_timeout": _hit_step_timeout(job),
                }
            )
    wall = (max(ends) - min(starts)).total_seconds() / 3600 if starts else 0.0
    shards.sort(key=lambda item: (item["stage"], item["shard"]))
    return {
        "runner_hours": round(total, 3),
        "shard_runner_hours": round(shard_total, 3),
        "wall_hours": round(wall, 3),
        "longest_shard_hours": max((item["hours"] for item in shards), default=0.0),
        "shards": shards,
    }


def _append(path: str | None, text: str) -> None:
    if path:
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(text)


def _output(path: str | None, **values: Any) -> None:
    _append(path, "".join(f"{key}={str(value).replace(chr(10), ' ')}\n" for key, value in values.items()))


def _gate_verdict(gate: Path) -> str | None:
    path = gate / "uat-gate-summary.json"
    if not path.is_file():
        return None
    verdict = json.loads(path.read_text(encoding="utf-8")).get("verdict")
    return verdict if isinstance(verdict, str) else None


def _dist_digest(gate: Path) -> str | None:
    path = gate / "dist-digest.txt"
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def render_summary(record: dict[str, Any]) -> str:
    lines = [
        "### Trunk UAT",
        "",
        f"Outcome: **{record['outcome']}**",
        f"Source commit: `{record['source_sha']}`",
        f"Distribution digest: `{record.get('dist_digest')}`",
        f"Gate verdict: `{record.get('verdict')}`",
    ]
    if record.get("reason"):
        lines.append(f"Reason: {record['reason']}")
    cells = record.get("cells")
    if cells:
        lines.append(f"Cells: {cells['total']} executed, {cells['passed']} passed, {cells['failed']} not passed")
    if "runner_hours" in record:
        lines.append(
            f"Cost: {record['runner_hours']:.2f} runner-hours, {record['wall_hours']:.2f} h wall, "
            f"longest shard {record['longest_shard_hours']:.2f} h"
        )
        if record["over_gate"]:
            lines.append(
                f"**DECISION GATE: more than {RUNNER_HOUR_GATE:g} runner-hours or a shard over "
                f"{JOB_HOUR_LIMIT:g} h; stop and ask the owner before this schedule continues.**"
            )
    shards = record.get("shards")
    if shards:
        lines += ["", "| Stage | Shard | State | Hours |", "| --- | --- | --- | --- |"]
        lines += [f"| {s['stage']} | {s['shard']} | {s['state']} | {s['hours']:.2f} |" for s in shards]
    return "\n".join(lines) + "\n"


def build_record(
    *,
    api: Api,
    evidence: Path,
    gate: Path,
    sha: str,
    run_id: int,
    attempt: int,
    event: str,
    job_results: dict[str, str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    record: dict[str, Any] = {
        "schema": HISTORY_SCHEMA,
        "run_id": run_id,
        "run_attempt": attempt,
        "event": event,
        "source_sha": sha,
        "dist_digest": _dist_digest(gate),
        "verdict": _gate_verdict(gate),
        "job_results": job_results,
    }
    costs = job_costs(run_jobs(api, run_id, attempt))
    timed_out = [
        f"stage {item['stage']} {item['shard']}"
        for item in costs["shards"]
        if item["step_timeout"] or (item["state"] == "cancelled" and item["hours"] >= SHARD_TIMEOUT_HOURS)
    ]
    if "cancelled" in job_results.values() and not timed_out:
        others = active_runs(api, RELEASE_WORKFLOW_FILE)
        record["outcome"] = "cancelled"
        record["reason"] = (
            f"cancelled: release-v2 run {others[0]['id']} in progress (full UAT fallback)"
            if others
            else "cancelled: no release-v2 run is in progress"
        )
        return record, []
    cells, shard_directories = read_shard_cells(evidence)
    record.update(costs)
    record["timed_out_shards"] = timed_out
    record["over_gate"] = (
        bool(timed_out) or costs["runner_hours"] > RUNNER_HOUR_GATE or costs["longest_shard_hours"] > JOB_HOUR_LIMIT
    )
    passed = sum(1 for cell in cells if cell["status"] == "passed")
    record["cells"] = {"total": len(cells), "passed": passed, "failed": len(cells) - passed}
    if not cells:
        states = ", ".join(f"{name}={result}" for name, result in sorted(job_results.items()))
        record["outcome"] = "zero-cells"
        record["reason"] = (
            f"zero cells executed: {len(shard_directories)} shard evidence directories held no cells.jsonl rows "
            f"({states})"
        )
        return record, []
    record["outcome"] = "executed"
    rows = [
        {
            "schema": HISTORY_SCHEMA,
            "run_id": run_id,
            "run_attempt": attempt,
            "event": event,
            "source_sha": sha,
            "dist_digest": record["dist_digest"],
            **cell,
        }
        for cell in cells
    ]
    return record, rows


def write_record(output: Path, record: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / RECORD_FILE).write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    (output / HISTORY_FILE).write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8", newline="\n"
    )


def _fetch_artifact(artifact_id: int) -> bytes:
    return _reuse._gh_bytes("api", f"repos/{_consumer.REPOSITORY}/actions/artifacts/{artifact_id}/zip")


def history_rows(
    api: Api | None = None, fetch: Fetch | None = None, *, branch: str = "develop", run_limit: int = BASELINE_RUN_LIMIT
) -> Iterator[dict[str, Any]]:
    api = api or _consumer.github_json
    fetch = fetch or _fetch_artifact
    for executed in executed_runs(api, branch=branch, run_limit=run_limit):
        record = next(item for item in executed.artifacts if RECORD_NAME.fullmatch(str(item.get("name"))))
        with zipfile.ZipFile(io.BytesIO(fetch(record["id"]))) as archive:
            text = archive.read(HISTORY_FILE).decode("utf-8")
        for line in text.splitlines():
            if line.strip():
                yield json.loads(line)


def extract_run_directory(blob: bytes, destination: Path) -> int:
    written = 0
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        for info in archive.infolist():
            name = PurePosixPath(info.filename)
            if info.is_dir() or name.parts[:1] != ("run",) or len(name.parts) < 2:
                continue
            relative = PurePosixPath(*name.parts[1:])
            if name.is_absolute() or ".." in name.parts or "\\" in info.filename:
                raise ValueError(f"unsafe evidence member {info.filename!r}")
            if info.file_size > _reuse.MAX_MEMBER_BYTES:
                raise ValueError(f"evidence member {info.filename!r} is too large")
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("xb") as stream:
                shutil.copyfileobj(source, stream)
            written += 1
    return written


def fetch_run_directories(
    output: Path,
    api: Api | None = None,
    fetch: Fetch | None = None,
    *,
    branch: str = "develop",
    run_limit: int = BASELINE_RUN_LIMIT,
) -> list[Path]:
    api = api or _consumer.github_json
    fetch = fetch or _fetch_artifact
    directories: list[Path] = []
    for executed in executed_runs(api, branch=branch, run_limit=run_limit):
        for artifact in executed.artifacts:
            match = SHARD_DIRECTORY.fullmatch(str(artifact.get("name")))
            if match is None:
                continue
            destination = output / f"{executed.run_id}-{executed.sha[:12]}" / f"stage{match.group(1)}-{match.group(2)}"
            if extract_run_directory(fetch(artifact["id"]), destination):
                directories.append(destination)
    return directories


def _post(path: str) -> None:
    subprocess.run(
        ["gh", "api", "--hostname", "github.com", "-X", "POST", f"repos/{_consumer.REPOSITORY}/{path}"],
        capture_output=True,
        check=True,
        timeout=60,
        env=_consumer._gh_environment(),
    )


def cancel_in_progress(api: Api, post: Callable[[str], None]) -> list[int]:
    cancelled: list[int] = []
    for run in active_runs(api, WORKFLOW_FILE):
        post(f"actions/runs/{run['id']}/cancel")
        cancelled.append(run["id"])
    return cancelled


def _parse_results(items: list[str]) -> dict[str, str]:
    results: dict[str, str] = {}
    for item in items:
        name, _, result = item.partition("=")
        if not name or not result:
            raise ValueError(f"job result must be name=result: {item!r}")
        results[name] = result
    return results


def _command_plan(args: argparse.Namespace) -> int:
    root = Path(args.root)
    decision = decide(
        event=args.event,
        branch=args.branch,
        run_id=args.run_id,
        api=_consumer.github_json,
        changed=lambda base, head: git_changed_paths(root, base, head),
    )
    print(decision.reason)
    _output(
        args.github_output,
        action=decision.action,
        reason=decision.reason,
        source_sha=decision.source_sha,
        baseline_sha=decision.baseline_sha,
        baseline_run_id=decision.baseline_run_id,
    )
    _append(
        args.summary,
        f"### Trunk UAT plan\n\nDecision: **{decision.reason}**\n\nSource commit: `{decision.source_sha or 'none'}`\n",
    )
    return 0


def _command_admit(args: argparse.Namespace) -> int:
    receipt = admit(Path(args.source).resolve(), args.sha, Path(args.output).absolute())
    _output(args.github_output, tag=receipt["tag"], version=receipt["version"])
    print(json.dumps(receipt, indent=2))
    return 0


def _command_record(args: argparse.Namespace) -> int:
    record, rows = build_record(
        api=_consumer.github_json,
        evidence=Path(args.evidence),
        gate=Path(args.gate),
        sha=args.sha,
        run_id=args.run_id,
        attempt=args.attempt,
        event=args.event,
        job_results=_parse_results(args.job_result),
    )
    _append(args.summary, render_summary(record))
    executed = record["outcome"] == "executed"
    if executed:
        write_record(Path(args.output), record, rows)
    _output(args.github_output, executed=str(executed).lower(), outcome=record["outcome"])
    if record["outcome"] == "zero-cells":
        print(f"::error::{record['reason']}")
        return 1
    return 0


def _command_cancel(args: argparse.Namespace) -> int:
    cancelled = cancel_in_progress(_consumer.github_json, _post)
    if cancelled:
        line = f"cancelled trunk-uat run {', '.join(str(run) for run in cancelled)} ({args.reason})"
    else:
        line = "no trunk-uat run was in progress"
    print(line)
    _append(args.summary, f"### Trunk UAT cancellation\n\n{line}\n")
    return 0


def _command_history(args: argparse.Namespace) -> int:
    for row in history_rows(branch=args.branch, run_limit=args.run_limit):
        print(json.dumps(row, sort_keys=True))
    return 0


def _command_fetch_runs(args: argparse.Namespace) -> int:
    directories = fetch_run_directories(args.output, branch=args.branch, run_limit=args.run_limit)
    print(f"wrote {len(directories)} shard run directories under {args.output}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan, admit, record and read the nightly trunk UAT")
    commands = parser.add_subparsers(dest="command", required=True)

    plan = commands.add_parser("plan", help="Decide whether this run executes, skips or defers")
    plan.add_argument("--event", required=True)
    plan.add_argument("--branch", required=True)
    plan.add_argument("--run-id", type=int, required=True)
    plan.add_argument("--root", default=".")
    plan.add_argument("--github-output")
    plan.add_argument("--summary")
    plan.set_defaults(handler=_command_plan)

    admission = commands.add_parser("admit", help="Download and verify the trunk distribution of a commit")
    admission.add_argument("--source", type=Path, required=True)
    admission.add_argument("--sha", required=True)
    admission.add_argument("--output", type=Path, required=True)
    admission.add_argument("--github-output")
    admission.set_defaults(handler=_command_admit)

    record = commands.add_parser("record", help="Build the run record and per-cell history from shard evidence")
    record.add_argument("--evidence", type=Path, required=True)
    record.add_argument("--gate", type=Path, required=True)
    record.add_argument("--sha", required=True)
    record.add_argument("--run-id", type=int, required=True)
    record.add_argument("--attempt", type=int, required=True)
    record.add_argument("--event", required=True)
    record.add_argument("--job-result", action="append", default=[])
    record.add_argument("--output", type=Path, required=True)
    record.add_argument("--github-output")
    record.add_argument("--summary")
    record.set_defaults(handler=_command_record)

    cancel = commands.add_parser("cancel", help="Cancel in-progress trunk-uat runs")
    cancel.add_argument("--reason", required=True)
    cancel.add_argument("--summary")
    cancel.set_defaults(handler=_command_cancel)

    history = commands.add_parser("history", help="Print per-cell history rows of recent executed runs")
    history.add_argument("--branch", default="develop")
    history.add_argument("--run-limit", type=int, default=BASELINE_RUN_LIMIT)
    history.set_defaults(handler=_command_history)

    fetch = commands.add_parser(
        "fetch-runs", help="Download shard run directories of recent executed runs for scripts/uat_rubric.py"
    )
    fetch.add_argument("--output", type=Path, required=True)
    fetch.add_argument("--branch", default="develop")
    fetch.add_argument("--run-limit", type=int, default=BASELINE_RUN_LIMIT)
    fetch.set_defaults(handler=_command_fetch_runs)

    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        print(f"trunk_uat FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
