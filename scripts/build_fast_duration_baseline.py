from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import re
import stat
import statistics
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from scripts.release_canary_sharding import load_durations, partition_node_ids
from tests.duration_policy import _junit_nodeid, _percentile

SCHEMA = 1
MARKER_EXPRESSION = "fast and not (slow or stress or resource_heavy or live_integration)"
COVERAGE_ENABLED = True
XDIST_MODE = "auto"
GATE_SUM_TOLERANCE = 0.10
GATE_COVERAGE_MINIMUM = 0.95
MEDIUM_SKEW_THRESHOLD = 0.20
MEDIUM_STALE_LIST_LIMIT = 50
MEDIUM_MIN_DELTA_SECONDS = 0.1
MEDIUM_MIN_RELATIVE_DELTA = 0.20
MEDIUM_SHARD_COUNT = 4
TEST_SHARD_COUNT = 8
SUMMARY_NAME = "SUMMARY.md"
SUMMARY_LIST_LIMIT = 15
REFRESH_RUN_DIR = "fast-refresh/runs/run-{run_id}"
REJECTION_CODES = (
    "calibration-unreadable",
    "calibration-invalid",
    "junit-unreadable",
    "junit-time-invalid",
    "junit-no-samples",
)
MAX_SOURCE_RUNS = 20
MIN_SOURCE_RUNS = 10
CI_WORKFLOW_PATH = ".github/workflows/ci.yml"
COMMITTED_FILE_MODE = "100644"
GITHUB_API_TIMEOUT_SECONDS = 60
GITHUB_ARTIFACT_TIMEOUT_SECONDS = 300
ARTIFACT_MEMBERS = frozenset({"junit.xml", "calibration.json", "collection.jsonl"})
ARTIFACT_MAX_BYTES = 256 * 1024 * 1024
GENERATED_AT_WINDOW = timedelta(days=30)
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
BACKTICK_RUNS = re.compile(r"`+")
Api = Callable[[str], Any]
Fetch = Callable[[str], bytes]


NEW_TEST_NOTE = (
    "Tests counted as new are those absent from the committed baseline, which includes tests added by other merged "
    "pull requests since it was last refreshed; they keep being charged until a refreshed baseline merges into develop."
)


class BaselineError(RuntimeError):
    pass


class SourceRunError(BaselineError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def read_calibration_best(run_dir: Path) -> tuple[float, dict[str, Any]]:
    try:
        payload = json.loads((run_dir / "calibration.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SourceRunError("calibration-unreadable", f"missing or unreadable calibration.json: {exc}") from exc
    workload = payload.get("workload") if isinstance(payload, dict) else None
    best = workload.get("best_seconds") if isinstance(workload, dict) else None
    if isinstance(best, bool) or not isinstance(best, (int, float)):
        raise SourceRunError("calibration-invalid", "calibration workload.best_seconds is not numeric")
    if not math.isfinite(best) or best <= 0:
        raise SourceRunError("calibration-invalid", "calibration workload.best_seconds is not finite and positive")
    metadata: dict[str, Any] = {}
    if isinstance(payload, dict):
        for key in ("python", "cpu_count"):
            if payload.get(key) is not None:
                metadata[key] = payload[key]
    return float(best), metadata


def read_run_samples(run_dir: Path) -> dict[str, list[float]]:
    junit_path = run_dir / "junit.xml"
    try:
        root = ET.parse(junit_path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise SourceRunError("junit-unreadable", f"missing or unreadable junit.xml: {exc}") from exc
    samples: dict[str, list[float]] = {}
    for testcase in root.iter("testcase"):
        raw_time = testcase.get("time")
        if raw_time is None:
            continue
        try:
            duration = float(raw_time)
        except ValueError as exc:
            raise SourceRunError("junit-time-invalid", f"invalid junit time {raw_time!r} in {junit_path}") from exc
        if not math.isfinite(duration) or duration < 0:
            raise SourceRunError("junit-time-invalid", f"invalid junit time {raw_time!r} in {junit_path}")
        nodeid = _junit_nodeid(testcase)
        if nodeid is None:
            continue
        samples.setdefault(nodeid, []).append(duration)
    if not samples:
        raise SourceRunError("junit-no-samples", f"no usable testcase samples in {junit_path}")
    return samples


def load_manifest(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise BaselineError("manifest must be a non-empty list of run entries")
    entries: list[dict[str, Any]] = []
    for entry in payload:
        if not isinstance(entry, dict):
            raise BaselineError("manifest entries must be objects")
        for key in ("run_id", "pr", "sha", "artifact_id", "dir"):
            if entry.get(key) is None:
                raise BaselineError(f"manifest entry is missing {key!r}")
        entries.append(entry)
    return entries


def build_baseline(entries: list[dict[str, Any]], generated_at: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ordered = sorted(entries, key=lambda entry: (int(entry["run_id"]), int(entry["artifact_id"])))
    usable: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for entry in ordered:
        run_dir = Path(str(entry["dir"]))
        try:
            best, metadata = read_calibration_best(run_dir)
            samples = read_run_samples(run_dir)
        except SourceRunError as exc:
            rejected.append(
                {
                    "run_id": entry["run_id"],
                    "pr": entry["pr"],
                    "sha": entry["sha"],
                    "artifact_id": entry["artifact_id"],
                    "reason": exc.code,
                }
            )
            continue
        usable.append(
            {
                "run_id": entry["run_id"],
                "pr": entry["pr"],
                "sha": entry["sha"],
                "artifact_id": entry["artifact_id"],
                "best_seconds": best,
                "metadata": metadata,
                "samples": samples,
            }
        )
    if not usable:
        raise BaselineError("no usable source runs: every run is missing a calibration value or junit samples")
    reference = float(statistics.median([run["best_seconds"] for run in usable]))
    normalized: dict[str, list[float]] = {}
    run_records: list[dict[str, Any]] = []
    for run in usable:
        best = float(run["best_seconds"])
        factor = reference / best
        record: dict[str, Any] = {
            "run_id": run["run_id"],
            "pr": run["pr"],
            "sha": run["sha"],
            "artifact_id": run["artifact_id"],
            "calibration_best_seconds": best,
            "speed_factor": factor,
            "test_count": sum(len(values) for values in run["samples"].values()),
        }
        record.update(run["metadata"])
        run_records.append(record)
        for nodeid in sorted(run["samples"]):
            for raw in run["samples"][nodeid]:
                normalized.setdefault(nodeid, []).append(raw * factor)
    tests = [
        {"id": nodeid, "p50_seconds": round(_percentile(values, 50), 6)}
        for nodeid, values in sorted(normalized.items())
    ]
    baseline = {
        "schema": SCHEMA,
        "generated_at": generated_at,
        "generator": "scripts/build_fast_duration_baseline.py",
        "config": {
            "coverage": COVERAGE_ENABLED,
            "xdist": XDIST_MODE,
            "marker_expression": MARKER_EXPRESSION,
        },
        "reference_calibration_best_seconds": reference,
        "run_count": len(run_records),
        "rejected_count": len(rejected),
        "runs": run_records,
        "rejected_runs": rejected,
        "tests": tests,
    }
    return baseline, rejected


def test_shard_index(nodeid: str) -> int:
    digest = hashlib.sha1(nodeid.encode("utf-8")).hexdigest()
    return int(digest, 16) % TEST_SHARD_COUNT


def write_sharded_baseline(output_dir: Path, baseline: dict[str, Any]) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {key: baseline[key] for key in baseline if key != "tests"}
    manifest["test_shard_count"] = TEST_SHARD_COUNT
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    buckets: dict[int, list[dict[str, Any]]] = {index: [] for index in range(TEST_SHARD_COUNT)}
    for record in baseline["tests"]:
        buckets[test_shard_index(record["id"])].append(record)
    written = [manifest_path]
    for index in range(TEST_SHARD_COUNT):
        shard_path = output_dir / f"tests-{index:02d}.json"
        shard_path.write_text(json.dumps(buckets[index], indent=2) + "\n", encoding="utf-8", newline="\n")
        written.append(shard_path)
    return written


def read_sharded_baseline(input_dir: Path) -> dict[str, Any]:
    try:
        manifest = json.loads((input_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BaselineError(f"missing or unreadable baseline manifest: {exc}") from exc
    if manifest.get("schema") != SCHEMA:
        raise BaselineError(f"unsupported baseline schema: {manifest.get('schema')!r}")
    shard_count = manifest.get("test_shard_count")
    if shard_count != TEST_SHARD_COUNT:
        raise BaselineError(f"unsupported test shard count: {shard_count!r}")
    tests: list[dict[str, Any]] = []
    for index in range(TEST_SHARD_COUNT):
        try:
            records = json.loads((input_dir / f"tests-{index:02d}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise BaselineError(f"missing or unreadable baseline shard {index:02d}: {exc}") from exc
        if not isinstance(records, list):
            raise BaselineError(f"baseline shard {index:02d} must be a list of records")
        tests.extend(records)
    return {**manifest, "tests": tests}


def run_sums(baseline: dict[str, Any], entries: list[dict[str, Any]]) -> list[float]:
    factors = {int(run["run_id"]): float(run["speed_factor"]) for run in baseline["runs"]}
    totals: list[float] = []
    for entry in entries:
        run_id = int(entry["run_id"])
        if run_id not in factors:
            continue
        samples = read_run_samples(Path(str(entry["dir"])))
        totals.append(sum(raw * factors[run_id] for values in samples.values() for raw in values))
    return totals


def baseline_tests_map(baseline: dict[str, Any]) -> dict[str, float]:
    records = baseline.get("tests")
    if not isinstance(records, list):
        raise BaselineError("baseline tests must be a list of id/p50_seconds records")
    tests: dict[str, float] = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("id"), str):
            raise BaselineError("baseline test records must carry a string id")
        value = record.get("p50_seconds")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise BaselineError(f"baseline record for {record.get('id')!r} must contain numeric p50_seconds")
        amount = float(value)
        if not math.isfinite(amount) or amount < 0:
            raise BaselineError(f"baseline record for {record.get('id')!r} must be finite and non-negative")
        tests[record["id"]] = amount
    return tests


def _cell(text: object) -> str:
    flat = CONTROL_CHARACTERS.sub(" ", str(text))
    longest = max((len(run) for run in BACKTICK_RUNS.findall(flat)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if flat.startswith("`") or flat.endswith("`") else ""
    return f"{fence}{pad}{flat.replace('|', chr(92) + '|')}{pad}{fence}"


def _count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BaselineError("baseline manifest run records must carry integer ids")
    return value


def _area(nodeid: str) -> str:
    parts = nodeid.split("::", 1)[0].split("/")
    return "/".join(parts[:3]) if len(parts) > 3 else "/".join(parts[:-1]) or parts[0]


def _seconds(value: float) -> str:
    return f"{value:.3f}"


def _change(old: float, new: float) -> str:
    if old <= 0:
        return "new" if new > 0 else "0.0%"
    return f"{(new - old) / old * 100:+.1f}%"


def _reference_seconds(baseline: dict[str, Any]) -> str:
    value = baseline.get("reference_calibration_best_seconds")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise BaselineError("baseline manifest must carry a finite reference_calibration_best_seconds")
    return f"{value:.6f}"


def _rejection_code(reason: object) -> str:
    return reason if isinstance(reason, str) and reason in REJECTION_CODES else "unrecognized"


def summary_markdown(previous: dict[str, Any], current: dict[str, Any]) -> str:
    old = baseline_tests_map(previous)
    new = baseline_tests_map(current)
    kept = sorted(old.keys() & new.keys())
    added = sorted(new.keys() - old.keys(), key=lambda nodeid: (-new[nodeid], nodeid))
    removed = sorted(old.keys() - new.keys(), key=lambda nodeid: (-old[nodeid], nodeid))
    old_sum, new_sum = sum(old.values()), sum(new.values())
    kept_old, kept_new = sum(old[nodeid] for nodeid in kept), sum(new[nodeid] for nodeid in kept)
    lines = [
        "# Fast-tier duration baseline refresh",
        "",
        "Generated by `scripts/build_fast_duration_baseline.py summary` from the previous and the refreshed baseline.",
        "CI rebuilds the baseline and this summary from the source runs in `manifest.json` and fails unless both",
        "match the committed files byte for byte.",
        "",
        "## Totals",
        "",
        "| measure | previous | refreshed | change |",
        "| --- | ---: | ---: | ---: |",
        f"| sum of p50 seconds | {_seconds(old_sum)} | {_seconds(new_sum)} | {_change(old_sum, new_sum)} |",
        f"| sum over tests in both | {_seconds(kept_old)} | {_seconds(kept_new)} | {_change(kept_old, kept_new)} |",
        f"| tests | {len(old)} | {len(new)} | +{len(added)} / -{len(removed)} |",
        f"| reference calibration seconds | {_reference_seconds(previous)} | {_reference_seconds(current)} | |",
        "",
        "## Change by area (tests in both)",
        "",
        "| area | tests | previous | refreshed | change |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    areas: dict[str, list[float]] = {}
    for nodeid in kept:
        totals = areas.setdefault(_area(nodeid), [0, 0.0, 0.0])
        totals[0] += 1
        totals[1] += old[nodeid]
        totals[2] += new[nodeid]
    for area, (count, before, after) in sorted(
        areas.items(), key=lambda item: (-abs(item[1][2] - item[1][1]), item[0])
    )[:SUMMARY_LIST_LIMIT]:
        lines.append(
            f"| {_cell(area)} | {int(count)} | {_seconds(before)} | {_seconds(after)} | {_change(before, after)} |"
        )
    moved = sorted(kept, key=lambda nodeid: (-(new[nodeid] - old[nodeid]), nodeid))
    for title, chosen in (
        ("Largest increases", [nodeid for nodeid in moved if new[nodeid] > old[nodeid]][:SUMMARY_LIST_LIMIT]),
        (
            "Largest decreases",
            [nodeid for nodeid in reversed(moved) if new[nodeid] < old[nodeid]][:SUMMARY_LIST_LIMIT],
        ),
    ):
        lines += ["", f"## {title}", "", "| test | previous | refreshed | change |", "| --- | ---: | ---: | ---: |"]
        lines += [
            f"| {_cell(nodeid)} | {_seconds(old[nodeid])} | {_seconds(new[nodeid])} | "
            f"{_change(old[nodeid], new[nodeid])} |"
            for nodeid in chosen
        ]
    for title, chosen, values in (
        ("Slowest added tests", added[:SUMMARY_LIST_LIMIT], new),
        ("Slowest removed tests", removed[:SUMMARY_LIST_LIMIT], old),
    ):
        lines += ["", f"## {title}", "", "| test | p50 seconds |", "| --- | ---: |"]
        lines += [f"| {_cell(nodeid)} | {_seconds(values[nodeid])} |" for nodeid in chosen]
    lines += [
        "",
        "## Source runs",
        "",
        "| run | pr | sha | speed factor | tests |",
        "| ---: | ---: | --- | ---: | ---: |",
    ]
    for run in current.get("runs", []):
        lines.append(
            f"| {_count(run['run_id'])} | {_count(run['pr'])} | {_cell(run['sha'])} | "
            f"{float(run['speed_factor']):.3f} | {_count(run['test_count'])} |"
        )
    rejected = current.get("rejected_runs", [])
    lines += ["", f"Rejected source runs: {len(rejected)}."]
    if rejected:
        lines.append("")
    lines += [
        f"- run {_count(item['run_id'])} (pr {_count(item['pr'])}): {_rejection_code(item.get('reason'))}"
        for item in rejected
    ]
    return "\n".join(lines) + "\n"


def write_summary(previous_dir: Path, baseline_dir: Path) -> Path:
    path = baseline_dir / SUMMARY_NAME
    path.write_text(
        summary_markdown(read_sharded_baseline(previous_dir), read_sharded_baseline(baseline_dir)),
        encoding="utf-8",
        newline="\n",
    )
    return path


def reproduce_entries(manifest: dict[str, Any], runs_root: Path) -> list[dict[str, Any]]:
    entries = []
    for group in ("runs", "rejected_runs"):
        records = manifest.get(group)
        if not isinstance(records, list):
            raise BaselineError(f"baseline manifest {group} must be a list")
        for run in records:
            if not isinstance(run, dict):
                raise BaselineError(f"baseline manifest {group} must hold objects")
            entry = {key: run.get(key) for key in ("run_id", "pr", "sha", "artifact_id")}
            for key in ("run_id", "pr", "artifact_id"):
                if _count(entry[key]) <= 0:
                    raise BaselineError("baseline manifest run records must carry positive ids")
            if not isinstance(entry["sha"], str) or not SHA_PATTERN.fullmatch(entry["sha"]):
                raise BaselineError("baseline manifest run records must carry a full lowercase commit sha")
            entries.append({**entry, "dir": str(runs_root / REFRESH_RUN_DIR.format(run_id=entry["run_id"]))})
    if not entries:
        raise BaselineError("the baseline manifest names no source runs")
    return entries


def github_api(path: str) -> Any:
    try:
        completed = subprocess.run(
            ["gh", "api", path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
            timeout=GITHUB_API_TIMEOUT_SECONDS,
        )
        return json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise BaselineError("a GitHub API request failed") from exc


def github_artifact_zip(path: str) -> bytes:
    try:
        return subprocess.run(
            ["gh", "api", path], capture_output=True, check=True, timeout=GITHUB_ARTIFACT_TIMEOUT_SECONDS
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise BaselineError("a GitHub artifact download failed") from exc


def _fetch(api: Api, path: str) -> dict[str, Any] | None:
    try:
        payload = api(path)
    except BaselineError:
        return None
    return payload if isinstance(payload, dict) else None


def _utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        return datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError:
        return None


def _same_repo(record: object, repo: str) -> bool:
    name = record.get("full_name") if isinstance(record, dict) else None
    return isinstance(name, str) and name.lower() == repo.lower()


def _run_codes(run: dict[str, Any], entry: dict[str, Any], repo: str, generated_at: datetime) -> list[str]:
    codes: list[str] = []
    if run.get("id") != entry["run_id"]:
        codes.append("run-id")
    if str(run.get("path", "")).split("@", 1)[0] != CI_WORKFLOW_PATH:
        codes.append("run-workflow")
    if run.get("event") != "pull_request":
        codes.append("run-event")
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        codes.append("run-conclusion")
    if run.get("head_sha") != entry["sha"]:
        codes.append("run-sha")
    if not _same_repo(run.get("head_repository"), repo):
        codes.append("run-fork")
    created = _utc(run.get("created_at"))
    if created is None:
        codes.append("run-created")
    elif generated_at < created:
        codes.append("generated-before-run")
    elif generated_at - created > GENERATED_AT_WINDOW:
        codes.append("generated-too-late")
    return codes


def _artifact_codes(artifact: dict[str, Any], entry: dict[str, Any]) -> list[str]:
    codes: list[str] = []
    if artifact.get("name") != f"fast-junit-{entry['pr']}-{entry['sha']}":
        codes.append("artifact-name")
    origin = artifact.get("workflow_run")
    if not isinstance(origin, dict) or origin.get("id") != entry["run_id"]:
        codes.append("artifact-run")
    elif origin.get("head_sha") != entry["sha"]:
        codes.append("artifact-sha")
    return codes


def _pull_codes(pull: dict[str, Any], entry: dict[str, Any], repo: str, run_created: datetime | None) -> list[str]:
    codes: list[str] = []
    if pull.get("merged") is not True:
        codes.append("pr-not-merged")
    head = pull.get("head")
    if not isinstance(head, dict) or head.get("sha") != entry["sha"]:
        codes.append("pr-head")
    elif not _same_repo(head.get("repo"), repo):
        codes.append("pr-fork")
    merged_at = _utc(pull.get("merged_at"))
    if merged_at is None:
        codes.append("pr-merged-at")
    elif run_created is not None and run_created > merged_at:
        codes.append("run-after-merge")
    return codes


def provenance_codes(entry: dict[str, Any], repo: str, api: Api, generated_at: datetime) -> list[str]:
    codes: list[str] = []
    run = _fetch(api, f"repos/{repo}/actions/runs/{entry['run_id']}")
    codes += ["run-unavailable"] if run is None else _run_codes(run, entry, repo, generated_at)
    artifact = _fetch(api, f"repos/{repo}/actions/artifacts/{entry['artifact_id']}")
    codes += ["artifact-unavailable"] if artifact is None else _artifact_codes(artifact, entry)
    pull = _fetch(api, f"repos/{repo}/pulls/{entry['pr']}")
    created = _utc(run.get("created_at")) if run is not None else None
    codes += ["pr-unavailable"] if pull is None else _pull_codes(pull, entry, repo, created)
    return codes


def source_run_problems(entries: list[dict[str, Any]], repo: str, api: Api, generated_at: datetime) -> list[str]:
    problems: list[str] = []
    if len(entries) > MAX_SOURCE_RUNS:
        problems.append(f"source runs: more than {MAX_SOURCE_RUNS}")
    if len(entries) < MIN_SOURCE_RUNS:
        problems.append(f"source runs: fewer than {MIN_SOURCE_RUNS}")
    for key, label in (("run_id", "run"), ("pr", "pr"), ("artifact_id", "artifact")):
        counts = Counter(entry[key] for entry in entries)
        problems += [
            f"source runs: {label} {value} appears more than once" for value, n in sorted(counts.items()) if n > 1
        ]
    if problems:
        return problems
    for entry in entries:
        problems += [f"run {entry['run_id']}: {code}" for code in provenance_codes(entry, repo, api, generated_at)]
    return problems


def extract_artifact(archive: bytes, destination: Path) -> None:
    try:
        bundle = zipfile.ZipFile(io.BytesIO(archive))
    except zipfile.BadZipFile as exc:
        raise SourceRunError("artifact-unreadable", "the artifact is not a zip archive") from exc
    with bundle:
        infos = bundle.infolist()
        names = [info.filename for info in infos]
        if len(set(names)) != len(names) or any(name not in ARTIFACT_MEMBERS for name in names):
            raise SourceRunError("artifact-members", "the artifact holds a member outside the allowed set")
        for info in infos:
            if info.is_dir() or stat.S_IFMT(info.external_attr >> 16) not in (0, stat.S_IFREG):
                raise SourceRunError("artifact-members", "the artifact holds a member that is not a regular file")
        if sum(info.file_size for info in infos) > ARTIFACT_MAX_BYTES:
            raise SourceRunError("artifact-members", "the artifact is larger than the limit")
        destination.mkdir(parents=True)
        for info in infos:
            try:
                with bundle.open(info) as member:
                    data = member.read(info.file_size + 1)
            except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
                raise SourceRunError("artifact-unreadable", "the artifact could not be read") from exc
            if len(data) != info.file_size:
                raise SourceRunError("artifact-members", "an artifact member does not match its declared size")
            (destination / info.filename).write_bytes(data)


def _git(directory: Path, *args: str) -> bytes:
    try:
        return subprocess.run(["git", "-C", str(directory), *args], capture_output=True, check=True).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise BaselineError("a git command failed while reading the committed baseline") from exc


def committed_tree(baseline_dir: Path, rev: str) -> dict[str, tuple[str, str]]:
    listing = _git(baseline_dir, "ls-tree", "-r", "-z", rev, "--", ".").decode("utf-8", errors="surrogateescape")
    tree: dict[str, tuple[str, str]] = {}
    for record in listing.split("\0"):
        if not record:
            continue
        meta, _, name = record.partition("\t")
        mode, _kind, blob = meta.split(" ")
        tree[name] = (mode, blob)
    return tree


def tree_differences(baseline_dir: Path, tree: dict[str, tuple[str, str]], generated: dict[str, bytes]) -> list[str]:
    quoted = {name: json.dumps(name) for name in {*tree, *generated}}
    problems = [f"extra file {quoted[name]}" for name in sorted(tree.keys() - generated.keys())]
    problems += [f"missing file {quoted[name]}" for name in sorted(generated.keys() - tree.keys())]
    for name in sorted(tree.keys() & generated.keys()):
        mode, blob = tree[name]
        if mode != COMMITTED_FILE_MODE:
            problems.append(f"file mode {mode} on {quoted[name]}")
        elif _git(baseline_dir, "cat-file", "blob", blob) != generated[name]:
            problems.append(f"changed bytes in {quoted[name]}")
    return problems


def reproduce_problems(
    baseline_dir: Path,
    previous_dir: Path,
    workdir: Path,
    *,
    rev: str,
    repo: str,
    api: Api,
    fetch: Fetch,
    collected_ids: set[str],
    ceiling_path: Path,
    not_after: datetime,
) -> list[str]:
    tree = committed_tree(baseline_dir, rev)
    if "manifest.json" not in tree:
        return ["the committed baseline has no manifest.json"]
    try:
        manifest = json.loads(_git(baseline_dir, "cat-file", "blob", tree["manifest.json"][1]))
    except ValueError as exc:
        raise BaselineError("the committed baseline manifest is not valid JSON") from exc
    if not isinstance(manifest, dict):
        raise BaselineError("the committed baseline manifest must be an object")
    generated_at = _utc(manifest.get("generated_at"))
    if generated_at is None:
        return ["manifest: generated_at is not a UTC timestamp"]
    if generated_at > not_after:
        return ["manifest: generated_at is after the pull request event"]
    resolved = workdir.resolve()
    if resolved == Path.cwd().resolve() or Path.cwd().resolve() in resolved.parents:
        raise BaselineError("the working directory must lie outside the current directory")
    if workdir.exists() and any(workdir.iterdir()):
        raise BaselineError("the working directory must be empty")
    entries = reproduce_entries(manifest, workdir)
    problems = source_run_problems(entries, repo, api, generated_at)
    if problems:
        return problems
    for entry in entries:
        try:
            extract_artifact(fetch(f"repos/{repo}/actions/artifacts/{entry['artifact_id']}/zip"), Path(entry["dir"]))
        except SourceRunError as exc:
            problems.append(f"run {entry['run_id']}: {exc.code}")
        except BaselineError:
            problems.append(f"run {entry['run_id']}: artifact-download")
    if problems:
        return problems
    scratch_dir = workdir / "reproduced"
    rebuilt, _rejected = build_baseline(entries, manifest["generated_at"])
    write_sharded_baseline(scratch_dir, rebuilt)
    write_summary(previous_dir, scratch_dir)
    generated = {
        path.relative_to(scratch_dir).as_posix(): path.read_bytes()
        for path in sorted(scratch_dir.rglob("*"))
        if path.is_file()
    }
    problems = tree_differences(baseline_dir, tree, generated)
    if len(rebuilt["runs"]) < MIN_SOURCE_RUNS:
        problems.append(f"source runs: fewer than {MIN_SOURCE_RUNS} usable")
    report = check_baseline(rebuilt, entries, collected_ids)
    if not report["sum_within_tolerance"]:
        problems.append("gate check: the baseline sum is outside tolerance of the source runs")
    if not report["coverage_within_minimum"]:
        problems.append("gate check: coverage of the collected fast tier is below the minimum")
    if ceiling_report(rebuilt, collected_ids, ceiling_path, read_sharded_baseline(previous_dir))["over"]:
        problems.append("ceiling check: the baseline is over the fast-duration ceiling")
    return problems


def check_baseline(baseline: dict[str, Any], entries: list[dict[str, Any]], collected_ids: set[str]) -> dict[str, Any]:
    baseline_tests = baseline_tests_map(baseline)
    baseline_sum = float(sum(baseline_tests.values()))
    totals = run_sums(baseline, entries)
    if not totals:
        raise BaselineError("gate check found no usable source runs")
    median_sum = float(statistics.median(totals))
    sum_ratio = baseline_sum / median_sum if median_sum > 0 else None
    covered = len(set(baseline_tests) & collected_ids)
    coverage = covered / len(collected_ids) if collected_ids else None
    return {
        "baseline_sum_seconds": baseline_sum,
        "median_run_sum_seconds": median_sum,
        "sum_ratio": sum_ratio,
        "sum_within_tolerance": sum_ratio is not None and abs(sum_ratio - 1) <= GATE_SUM_TOLERANCE,
        "baseline_test_count": len(baseline_tests),
        "collected_test_count": len(collected_ids),
        "covered_test_count": covered,
        "coverage": coverage,
        "coverage_within_minimum": coverage is not None and coverage >= GATE_COVERAGE_MINIMUM,
        "source_run_count": len(totals),
    }


def read_medium_recorded(path: Path) -> dict[str, float]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("tests")
    if not isinstance(records, dict):
        raise BaselineError("medium durations file tests must be an object")
    recorded: dict[str, float] = {}
    for nodeid, record in records.items():
        value = record.get("p95_seconds") if isinstance(record, dict) else None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        amount = float(value)
        if math.isfinite(amount) and amount >= 0:
            recorded[nodeid] = amount
    if not recorded:
        raise BaselineError("medium durations file has no usable records")
    return recorded


def read_shard_measured(paths: list[Path]) -> dict[str, dict[str, float]]:
    measured: dict[str, dict[str, float]] = {}
    for index, path in enumerate(paths):
        try:
            root = ET.parse(path).getroot()
        except (OSError, ET.ParseError) as exc:
            raise BaselineError(f"missing or unreadable shard junit {path}: {exc}") from exc
        shard = f"shard {index}"
        shard_tests: dict[str, float] = {}
        for testcase in root.iter("testcase"):
            raw_time = testcase.get("time")
            if raw_time is None:
                continue
            try:
                duration = float(raw_time)
            except ValueError as exc:
                raise BaselineError(f"invalid junit time {raw_time!r} in {path}") from exc
            if not math.isfinite(duration) or duration < 0:
                raise BaselineError(f"invalid junit time {raw_time!r} in {path}")
            nodeid = _junit_nodeid(testcase)
            if nodeid is None:
                continue
            shard_tests[nodeid] = shard_tests.get(nodeid, 0.0) + duration
        measured[shard] = shard_tests
    return measured


def _max_to_mean(totals: list[float]) -> float | None:
    mean = sum(totals) / len(totals) if totals else 0.0
    return max(totals) / mean if mean > 0 else None


def projected_max_to_mean(
    durations_path: Path, collected: set[str], measured: dict[str, dict[str, float]]
) -> float | None:
    durations = load_durations(durations_path)
    seconds: dict[str, float] = {}
    for shard_tests in measured.values():
        for nodeid, amount in shard_tests.items():
            seconds[nodeid] = seconds.get(nodeid, 0.0) + amount
    ordered = sorted(collected)
    totals = [
        sum(seconds.get(nodeid, 0.0) for nodeid in partition_node_ids(ordered, index, MEDIUM_SHARD_COUNT, durations))
        for index in range(MEDIUM_SHARD_COUNT)
    ]
    return _max_to_mean(totals)


def medium_drift_report(recorded: dict[str, float], measured: dict[str, dict[str, float]]) -> dict[str, Any]:
    shards: list[dict[str, Any]] = []
    all_ratios: list[float] = []
    for shard in sorted(measured):
        shard_tests = measured[shard]
        matched = {nodeid: shard_tests[nodeid] for nodeid in shard_tests if nodeid in recorded}
        ratios = [amount / recorded[nodeid] for nodeid, amount in matched.items() if recorded[nodeid] > 0]
        measured_sum = float(sum(matched.values()))
        recorded_sum = float(sum(recorded[nodeid] for nodeid in matched))
        shard_skew = measured_sum / recorded_sum - 1 if recorded_sum > 0 else None
        all_ratios.extend(ratios)
        shards.append(
            {
                "shard": shard,
                "measured_test_count": len(shard_tests),
                "matched_test_count": len(matched),
                "measured_sum_seconds": measured_sum,
                "recorded_sum_seconds": recorded_sum,
                "sum_skew": shard_skew,
                "measured_total_seconds": float(sum(shard_tests.values())),
            }
        )
    overall_skew = float(statistics.median(all_ratios)) - 1 if all_ratios else None
    recommends = overall_skew is not None and abs(overall_skew) > MEDIUM_SKEW_THRESHOLD
    recommends = recommends or any(
        shard["sum_skew"] is not None and abs(float(shard["sum_skew"])) > MEDIUM_SKEW_THRESHOLD for shard in shards
    )
    measured_ids = {nodeid for shard_tests in measured.values() for nodeid in shard_tests}
    unrecorded_seconds = sum(
        amount for shard_tests in measured.values() for nodeid, amount in shard_tests.items() if nodeid not in recorded
    )
    measured_total = sum(len(shard_tests) for shard_tests in measured.values())
    return {
        "matched_test_count": len(all_ratios),
        "overall_measured_test_count": measured_total,
        "overall_median_ratio_skew": overall_skew,
        "recommends_refresh": recommends,
        "skew_threshold": MEDIUM_SKEW_THRESHOLD,
        "stale_ids": sorted(set(recorded) - measured_ids),
        "unrecorded_test_count": len(measured_ids - set(recorded)),
        "unrecorded_measured_seconds": float(unrecorded_seconds),
        "max_to_mean_shard_seconds": _max_to_mean([float(shard["measured_total_seconds"]) for shard in shards]),
        "shards": shards,
    }


def build_medium_durations(runs: list[dict[str, dict[str, float]]], generated_at: str, source: str) -> dict[str, Any]:
    samples: dict[str, list[float]] = {}
    for index, run in enumerate(runs):
        if not any(run.values()):
            raise BaselineError(f"run {index} has no usable testcase samples in medium shard junit")
        for shard_tests in run.values():
            for nodeid, amount in shard_tests.items():
                samples.setdefault(nodeid, []).append(amount)
    if not samples:
        raise BaselineError("no usable testcase samples in medium shard junit")
    p95 = {nodeid: round(_percentile(values, 95), 5) for nodeid, values in samples.items()}
    return {
        "schema_version": 1,
        "generated_at": generated_at,
        "source": source,
        "default_seconds": round(float(statistics.median(p95.values())), 5),
        "tests": {nodeid: {"p95_seconds": p95[nodeid]} for nodeid in sorted(p95)},
    }


def _moved_enough(old: float, new: float) -> bool:
    delta = round(abs(new - old), 9)
    return delta >= MEDIUM_MIN_DELTA_SECONDS and delta >= round(old * MEDIUM_MIN_RELATIVE_DELTA, 9)


def apply_medium_hysteresis(previous: dict[str, Any] | None, fresh: dict[str, Any]) -> dict[str, Any]:
    records = previous.get("tests") if isinstance(previous, dict) else None
    if not isinstance(records, dict):
        return fresh
    tests: dict[str, Any] = {}
    for nodeid, record in fresh["tests"].items():
        old_record = records.get(nodeid)
        old = old_record.get("p95_seconds") if isinstance(old_record, dict) else None
        usable = not isinstance(old, bool) and isinstance(old, (int, float)) and math.isfinite(old) and old >= 0
        if usable and not _moved_enough(float(old), record["p95_seconds"]):
            tests[nodeid] = {"p95_seconds": old}
        else:
            tests[nodeid] = record
    default = fresh["default_seconds"]
    old_default = previous.get("default_seconds")
    if (
        not isinstance(old_default, bool)
        and isinstance(old_default, (int, float))
        and math.isfinite(old_default)
        and old_default > 0
        and round(abs(default - old_default), 9) < round(old_default * MEDIUM_MIN_RELATIVE_DELTA, 9)
    ):
        default = old_default
    return {**fresh, "default_seconds": default, "tests": tests}


def render_medium_durations(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2) + "\n"


def read_previous_medium(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def read_collected_ids(path: Path) -> set[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise BaselineError(f"missing or unreadable collected node ids {path}: {exc}") from exc
    return {line.strip() for line in lines if line.strip()}


def medium_refresh_command(args: argparse.Namespace) -> int:
    runs = [read_shard_measured(sorted(Path(run_dir).glob("*/shard-*-junit.xml"))) for run_dir in args.run_dir]
    if args.collected:
        collected = read_collected_ids(Path(args.collected))
        runs = [
            {
                shard: {nodeid: amount for nodeid, amount in tests.items() if nodeid in collected}
                for shard, tests in run.items()
            }
            for run in runs
        ]
        if not any(any(run.values()) for run in runs):
            raise BaselineError("no measured test is listed in the collected node ids")
    generated_at = args.generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    output = Path(args.output)
    previous = read_previous_medium(output)
    payload = apply_medium_hysteresis(previous, build_medium_durations(runs, generated_at, args.source))
    if (
        previous is not None
        and previous.get("tests") == payload["tests"]
        and previous.get("default_seconds") == payload["default_seconds"]
    ):
        return 0
    output.write_text(render_medium_durations(payload), encoding="utf-8")
    return 0


def drift_markdown(report: dict[str, Any]) -> str:
    lines = [
        "### Medium-shard drift",
        "",
        f"Matched tests: {report['matched_test_count']} of {report['overall_measured_test_count']} measured",
        f"Overall median-ratio skew: {report['overall_median_ratio_skew']}",
        f"Refresh recommended: {report['recommends_refresh']}",
        f"Unrecorded measured tests: {report['unrecorded_test_count']} "
        f"({report['unrecorded_measured_seconds']:.2f} s measured)",
        f"Busiest shard measured time / average: {_format_ratio(report['max_to_mean_shard_seconds'])}",
    ]
    if "projected_max_to_mean_shard_seconds" in report:
        lines.append(
            "Busiest shard / average if repartitioned with the refreshed durations: "
            f"{_format_ratio(report['projected_max_to_mean_shard_seconds'])}"
        )
    stale = report["stale_ids"]
    lines += ["", f"Stale recorded ids (recorded, not measured): {len(stale)}"]
    if stale:
        lines += ["", *(f"- `{nodeid}`" for nodeid in stale[:MEDIUM_STALE_LIST_LIMIT])]
        if len(stale) > MEDIUM_STALE_LIST_LIMIT:
            lines.append(f"- ... and {len(stale) - MEDIUM_STALE_LIST_LIMIT} more")
    lines += [
        "",
        "| shard | measured tests | matched | measured sum (s) | recorded sum (s) | skew |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for shard in report["shards"]:
        lines.append(
            f"| {shard['shard']} | {shard['measured_test_count']} | {shard['matched_test_count']} | "
            f"{shard['measured_sum_seconds']:.2f} | {shard['recorded_sum_seconds']:.2f} | {shard['sum_skew']} |"
        )
    return "\n".join(lines) + "\n"


def _format_ratio(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


VERIFY_RATIO_THRESHOLD = 0.30
VERIFY_DELTA_SECONDS = 360.0
VERIFY_NEW_TEST_ALLOWANCE_SECONDS = 0.5
VERIFY_MODE = "block"


def normalized_pr_samples(run_dir: Path, baseline: dict[str, object]) -> tuple[dict[str, float], float, float]:
    best, _metadata = read_calibration_best(run_dir)
    factor = baseline_reference(baseline) / best
    samples = read_run_samples(run_dir)
    tests = {nodeid: _percentile([raw * factor for raw in values], 50) for nodeid, values in samples.items()}
    return tests, factor, best


def read_collection_max(run_dir: Path) -> float | None:
    try:
        payload = json.loads((run_dir / "calibration.json").read_text(encoding="utf-8"))
        value = payload["collection"]["max_seconds"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def verify_durations(
    baseline_tests: dict[str, float],
    pr_tests: dict[str, float],
    *,
    ratio_threshold: float = VERIFY_RATIO_THRESHOLD,
    delta_seconds: float = VERIFY_DELTA_SECONDS,
    new_test_allowance: float = VERIFY_NEW_TEST_ALLOWANCE_SECONDS,
    ceiling_seconds: float | None = None,
) -> dict[str, object]:
    shared = sorted(set(baseline_tests) & set(pr_tests))
    new_ids = sorted(set(pr_tests) - set(baseline_tests))
    missing_ids = sorted(set(baseline_tests) - set(pr_tests))
    baseline_sum = float(sum(baseline_tests[nodeid] for nodeid in shared))
    pr_sum = float(sum(pr_tests[nodeid] for nodeid in shared))
    new_sum = float(sum(pr_tests[nodeid] for nodeid in new_ids))
    new_excess = float(sum(max(0.0, pr_tests[nodeid] - new_test_allowance) for nodeid in new_ids))
    effective_sum = pr_sum + new_excess
    delta = effective_sum - baseline_sum
    ratio = effective_sum / baseline_sum if baseline_sum > 0 else None
    per_test = [pr_tests[nodeid] / baseline_tests[nodeid] for nodeid in shared if baseline_tests[nodeid] > 0]
    median_ratio = float(statistics.median(per_test)) if per_test else None
    coverage = len(shared) / len(baseline_tests) if baseline_tests else None
    slower = ratio is not None and ratio > 1 + ratio_threshold and delta > delta_seconds
    projected_sum = baseline_sum + new_sum
    over_ceiling = ceiling_seconds is not None and projected_sum > ceiling_seconds
    return {
        "verdict": "would-block" if slower or over_ceiling else ("pass" if ratio is not None else "neutral"),
        **(
            {}
            if ratio is not None
            else {"reason": "no baseline tests with a positive summed time were present in the run"}
        ),
        "baseline_sum_seconds": baseline_sum,
        "pr_sum_seconds": pr_sum,
        "new_test_count": len(new_ids),
        "new_test_seconds": new_sum,
        "new_test_excess_seconds": new_excess,
        "effective_sum_seconds": effective_sum,
        "delta_seconds": delta,
        "sum_ratio": ratio,
        "median_test_ratio": median_ratio,
        "shared_test_count": len(shared),
        "baseline_test_count": len(baseline_tests),
        "missing_test_count": len(missing_ids),
        "coverage": coverage,
        "coverage_warning": coverage is not None and coverage < GATE_COVERAGE_MINIMUM,
        "ratio_threshold": ratio_threshold,
        "delta_threshold_seconds": delta_seconds,
        "ratio_rule_fired": slower,
        "projected_sum_seconds": projected_sum,
        "ceiling_seconds": ceiling_seconds,
        "over_ceiling": over_ceiling,
    }


def read_measurement_source(run_dir: Path) -> dict[str, str | None]:
    try:
        payload = json.loads((run_dir / "calibration.json").read_text(encoding="utf-8"))
        source = payload["measured_in"]
    except (OSError, ValueError, KeyError, TypeError):
        return {"run_id": None, "run_attempt": None}
    if not isinstance(source, dict):
        return {"run_id": None, "run_attempt": None}
    return {key: None if source.get(key) is None else str(source[key]) for key in ("run_id", "run_attempt")}


def describe_measurement(
    source: dict[str, str | None], current_run_id: str | None, current_attempt: str | None
) -> dict[str, object]:
    measured_attempt = source["run_attempt"]
    measured_run = source["run_id"]
    reused = (
        measured_attempt is not None
        and current_attempt is not None
        and (measured_attempt != current_attempt or (measured_run is not None and measured_run != current_run_id))
    )
    return {
        "measured_run_id": measured_run,
        "measured_run_attempt": measured_attempt,
        "verifying_run_attempt": current_attempt,
        "timings_reused": reused,
    }


def measurement_note(report: dict[str, object]) -> str | None:
    measured_attempt = report.get("measured_run_attempt")
    if measured_attempt is None:
        return None
    where = f"attempt {measured_attempt} of run {report.get('measured_run_id')}"
    if report.get("timings_reused"):
        return (
            f"These timings were measured by {where}; this is attempt {report.get('verifying_run_attempt')}, which "
            "re-read them without running any test. Re-running only this job or the failed jobs cannot change the "
            "numbers; a full workflow re-run (`gh run rerun <run-id>` without `--failed`) re-measures."
        )
    return f"Timings were measured in this attempt ({where})."


def _positive_number(payload: dict[str, object], key: str, path: Path) -> float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BaselineError(f"ceiling file {path} must contain a numeric {key}")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise BaselineError(f"ceiling file {path} must contain a finite positive {key}")
    return number


def read_ceiling(path: Path) -> tuple[float, float]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BaselineError(f"missing or unreadable ceiling file {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != 1 or isinstance(payload.get("schema"), bool):
        raise BaselineError(f"ceiling file {path} must be an object with schema 1")
    ceiling = _positive_number(payload, "ceiling_seconds", path)
    reference = _positive_number(payload, "reference_calibration_best_seconds", path)
    reason = payload.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise BaselineError(f"ceiling file {path} must contain a non-empty reason")
    return ceiling, reference


def baseline_reference(baseline: dict[str, object]) -> float:
    reference = baseline.get("reference_calibration_best_seconds")
    if isinstance(reference, bool) or not isinstance(reference, (int, float)) or reference <= 0:
        raise BaselineError("baseline has no usable reference_calibration_best_seconds")
    return float(reference)


def ceiling_in_baseline_scale(path: Path, baseline: dict[str, object]) -> float:
    ceiling, ceiling_reference = read_ceiling(path)
    return ceiling * baseline_reference(baseline) / ceiling_reference


def verify_run(baseline_dir: Path, run_dir: Path, ceiling_path: Path) -> dict[str, object]:
    try:
        baseline = read_sharded_baseline(baseline_dir)
        baseline_tests = baseline_tests_map(baseline)
    except BaselineError as exc:
        return {"verdict": "rejected", "reason": f"no usable committed baseline: {exc}"}
    try:
        ceiling = ceiling_in_baseline_scale(ceiling_path, baseline)
    except BaselineError as exc:
        return {"verdict": "rejected", "reason": f"no usable fast-duration ceiling: {exc}"}
    try:
        pr_tests, factor, best = normalized_pr_samples(run_dir, baseline)
    except BaselineError as exc:
        return {"verdict": "rejected", "reason": str(exc)}
    report = verify_durations(baseline_tests, pr_tests, ceiling_seconds=ceiling)
    report["speed_factor"] = factor
    report["calibration_best_seconds"] = best
    report["collection_max_seconds"] = read_collection_max(run_dir)
    return report


def attach_measurement(report: dict[str, object], run_dir: Path, run_id: str | None, run_attempt: str | None) -> None:
    report.update(describe_measurement(read_measurement_source(run_dir), run_id, run_attempt))


def verify_markdown(report: dict[str, object], *, mode: str | None = None) -> str:
    effective_mode = str(mode or report.get("mode") or VERIFY_MODE)
    verdict = str(report["verdict"])
    heading = "### verify-fast-duration" if effective_mode == "block" else "### verify-fast-duration (report only)"
    lines = [heading, "", f"Verdict: **{verdict}**", ""]
    note = measurement_note(report)
    if "reason" in report:
        lines.append(f"Reason: {report['reason']}")
        if note:
            lines += ["", note]
        return "\n".join(lines) + "\n"

    def fmt(value: object, spec: str) -> str:
        return "n/a" if value is None else format(value, spec)

    lines += [
        "| Metric | Value |",
        "| --- | --- |",
        f"| Baseline summed time over shared tests (normalized) | {fmt(report['baseline_sum_seconds'], '.1f')} s |",
        f"| PR summed time over shared tests (normalized) | {fmt(report['pr_sum_seconds'], '.1f')} s |",
        f"| New tests | {report['new_test_count']} ({fmt(report['new_test_seconds'], '.1f')} s, "
        f"{fmt(report['new_test_excess_seconds'], '.1f')} s over allowance) |",
        f"| Delta | {fmt(report['delta_seconds'], '+.1f')} s |",
        f"| Projected fast-tier total (baseline for shared tests plus new tests) | "
        f"{fmt(report['projected_sum_seconds'], '.1f')} s |",
        f"| Absolute ceiling | {fmt(report['ceiling_seconds'], '.1f')} s |",
        f"| Summed-time ratio | {fmt(report['sum_ratio'], '.3f')} |",
        f"| Median per-test ratio | {fmt(report['median_test_ratio'], '.3f')} |",
        f"| Coverage of baseline ids | {fmt(report['coverage'], '.1%')} |",
        f"| Runner speed factor | {fmt(report['speed_factor'], '.3f')} |",
        f"| Pytest collection time (max) | {fmt(report['collection_max_seconds'], '.1f')} s |",
        "",
    ]
    verb = "Blocks" if effective_mode == "block" else "Would block"
    lines.append(
        f"{verb} when the ratio exceeds {1 + float(report['ratio_threshold']):.2f} and the delta exceeds "
        f"{float(report['delta_threshold_seconds']):.0f} s (provisional thresholds; job wall time is not a metric), "
        f"or when the projected total exceeds the ceiling of {fmt(report['ceiling_seconds'], '.1f')} s."
    )
    if report["coverage_warning"]:
        lines.append(f"Warning: coverage is under {GATE_COVERAGE_MINIMUM:.0%} of baseline ids.")
    if report["new_test_count"]:
        lines += ["", NEW_TEST_NOTE]
    if note:
        lines += ["", note]
    return "\n".join(lines) + "\n"


def verify_command(args: argparse.Namespace) -> int:
    mode = getattr(args, "mode", VERIFY_MODE)
    baseline_path = Path(args.baseline)
    run_path = Path(args.run_dir)
    report = verify_run(baseline_path, run_path, Path(args.ceiling))
    attach_measurement(report, run_path, getattr(args, "run_id", None), getattr(args, "run_attempt", None))
    report["mode"] = mode
    text = verify_markdown(report, mode=mode)
    if args.summary:
        with Path(args.summary).open("a", encoding="utf-8") as handle:
            handle.write(text)
    if args.report_out:
        Path(args.report_out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(text)
    verdict = report["verdict"]
    if verdict == "would-block":
        if mode == "block":
            reasons = []
            if report.get("over_ceiling"):
                reasons.append(
                    f"projected total {float(report['projected_sum_seconds']):.1f} s > "
                    f"ceiling {float(report['ceiling_seconds']):.1f} s"
                )
            if report.get("ratio_rule_fired"):
                reasons.append(
                    f"summed ratio {float(report['sum_ratio']):.3f} > {1 + float(report['ratio_threshold']):.2f} and "
                    f"delta {float(report['delta_seconds']):+.1f} s > {float(report['delta_threshold_seconds']):.0f} s"
                )
            rerun_hint = (
                " The timings came from an earlier attempt and were re-read, not re-measured: re-run the whole "
                "workflow (not only this job or the failed jobs) to re-measure."
                if report.get("timings_reused")
                else ""
            )
            print(
                f"::error title=verify-fast-duration::fast-tier time exceeded the duration gate "
                f"({'; '.join(reasons)}).{rerun_hint}"
            )
            return 1
        print(
            "::warning title=verify-fast-duration::fast-tier time would be blocked by the duration gate (report only)"
        )
        return 0
    incomplete = {
        "rejected": f"run rejected: {report.get('reason')}",
        "neutral": f"nothing comparable: {report.get('reason')}",
    }.get(str(verdict))
    if incomplete is None and report.get("coverage_warning"):
        incomplete = f"coverage is under {GATE_COVERAGE_MINIMUM:.0%} of baseline ids"
    if incomplete is None:
        return 0
    if mode == "block":
        print(f"::error title=verify-fast-duration::{incomplete}")
        return 1
    print(f"::warning title=verify-fast-duration::{incomplete}")
    return 0


def file_sums(tests: dict[str, float], ids: set[str]) -> dict[str, float]:
    sums: dict[str, float] = {}
    for nodeid in ids:
        if nodeid in tests:
            path = nodeid.split("::", 1)[0]
            sums[path] = sums.get(path, 0.0) + tests[nodeid]
    return sums


def ceiling_growth(
    current: dict[str, float], previous: dict[str, float], ids: set[str], limit: int = 15
) -> list[dict[str, object]]:
    now = file_sums(current, ids)
    before = file_sums(previous, ids)
    rows = [
        {
            "file": path,
            "previous_seconds": before.get(path, 0.0),
            "current_seconds": total,
            "growth_seconds": total - before.get(path, 0.0),
        }
        for path, total in now.items()
    ]
    rows.sort(key=lambda row: (-float(row["growth_seconds"]), str(row["file"])))
    return rows[:limit]


def ceiling_markdown(report: dict[str, object]) -> str:
    state = "over" if report["over"] else "within"
    lines = [
        f"The refreshed fast-tier baseline sums to {float(report['total']):.1f} s over the currently collected "
        f"tests, {state} the ceiling of {float(report['ceiling']):.1f} s.",
        "",
    ]
    growth = report.get("top_growth_files")
    if isinstance(growth, list):
        lines += [
            "Top test files by growth against the previous baseline:",
            "",
            "| File | Previous | Refreshed | Growth |",
            "| --- | --- | --- | --- |",
        ]
        for row in growth:
            lines.append(
                f"| `{row['file']}` | {row['previous_seconds']:.1f} s | {row['current_seconds']:.1f} s | "
                f"{row['growth_seconds']:+.1f} s |"
            )
    return "\n".join(lines) + "\n"


def ceiling_report(
    current_baseline: dict[str, Any],
    collected: set[str],
    ceiling_path: Path,
    previous_baseline: dict[str, Any] | None = None,
) -> dict[str, object]:
    current = baseline_tests_map(current_baseline)
    ceiling = ceiling_in_baseline_scale(ceiling_path, current_baseline)
    total = float(sum(current[nodeid] for nodeid in collected if nodeid in current))
    report: dict[str, object] = {"total": total, "ceiling": ceiling, "over": total > ceiling}
    if previous_baseline is not None:
        scale = baseline_reference(current_baseline) / baseline_reference(previous_baseline)
        previous = {nodeid: value * scale for nodeid, value in baseline_tests_map(previous_baseline).items()}
        report["top_growth_files"] = ceiling_growth(current, previous, collected)
    return report


def ceiling_check_command(args: argparse.Namespace) -> int:
    report = ceiling_report(
        read_sharded_baseline(Path(args.baseline)),
        read_collected_ids(Path(args.collected_ids)),
        Path(args.ceiling),
        read_sharded_baseline(Path(args.previous)) if args.previous else None,
    )
    if args.report_out:
        Path(args.report_out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    text = ceiling_markdown(report)
    if args.markdown_out:
        Path(args.markdown_out).write_text(text, encoding="utf-8")
    print(text)
    return 3 if report["over"] else 0


def build_command(args: argparse.Namespace) -> int:
    generated_at = args.generated_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    baseline, _rejected = build_baseline(load_manifest(Path(args.manifest)), generated_at)
    if len(baseline["runs"]) < MIN_SOURCE_RUNS:
        raise BaselineError(f"only {len(baseline['runs'])} source runs are usable; a baseline needs {MIN_SOURCE_RUNS}")
    written = write_sharded_baseline(Path(args.output), baseline)
    print(f"baseline: {len(baseline['tests'])} tests from {len(baseline['runs'])} runs")
    print(f"rejected runs: {len(baseline['rejected_runs'])}")
    for rejected in baseline["rejected_runs"]:
        print(f"rejected run {rejected['run_id']}: {rejected['reason']}")
    for path in written:
        print(f"wrote {path}")
    return 0


def summary_command(args: argparse.Namespace) -> int:
    print(f"wrote {write_summary(Path(args.previous), Path(args.baseline))}")
    return 0


def reproduce_command(args: argparse.Namespace) -> int:
    if not args.repo:
        raise BaselineError("--repo or GITHUB_REPOSITORY is required")
    not_after = _utc(args.not_after) if args.not_after else datetime.now(timezone.utc)
    if not_after is None:
        raise BaselineError("--not-after must be a UTC timestamp ending in Z")
    problems = reproduce_problems(
        Path(args.baseline),
        Path(args.previous),
        Path(args.workdir),
        rev=args.rev,
        repo=str(args.repo),
        api=github_api,
        fetch=github_artifact_zip,
        collected_ids=read_collected_ids(Path(args.collected_ids)),
        ceiling_path=Path(args.ceiling),
        not_after=not_after,
    )
    if problems:
        print("error: the committed baseline is not reproduced from its source runs")
        for problem in problems:
            print(f"  - {problem}")
        return 2
    print(f"reproduced {args.baseline} byte for byte from its source runs")
    return 0


def check_command(args: argparse.Namespace) -> int:
    baseline = read_sharded_baseline(Path(args.baseline))
    entries = load_manifest(Path(args.manifest))
    collected = {
        line.strip() for line in Path(args.collected_ids).read_text(encoding="utf-8").splitlines() if line.strip()
    }
    report = check_baseline(baseline, entries, collected)
    if args.report_out:
        Path(args.report_out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["sum_within_tolerance"] and report["coverage_within_minimum"] else 2


def medium_drift_command(args: argparse.Namespace) -> int:
    recorded = read_medium_recorded(Path(args.recorded))
    measured = read_shard_measured([Path(item) for item in args.shard])
    report = medium_drift_report(recorded, measured)
    if args.projected_durations:
        if not args.collected:
            raise BaselineError("--projected-durations requires --collected")
        report["projected_max_to_mean_shard_seconds"] = projected_max_to_mean(
            Path(args.projected_durations), read_collected_ids(Path(args.collected)), measured
        )
    if args.report_out:
        Path(args.report_out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(drift_markdown(report))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build and check the fast-tier duration baseline.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_parser = subparsers.add_parser("build", help="Build a baseline JSON from a run manifest.")
    build_parser.add_argument("--manifest", required=True)
    build_parser.add_argument("--output", required=True)
    build_parser.add_argument("--generated-at", default=None)
    build_parser.set_defaults(handler=build_command)
    summary_parser = subparsers.add_parser("summary", help="Write the refresh summary next to a baseline.")
    summary_parser.add_argument("--previous", required=True)
    summary_parser.add_argument("--baseline", required=True)
    summary_parser.set_defaults(handler=summary_command)
    reproduce_parser = subparsers.add_parser(
        "reproduce",
        help="Rebuild a committed baseline and its summary from its source runs and compare the committed tree.",
    )
    reproduce_parser.add_argument("--baseline", required=True)
    reproduce_parser.add_argument("--previous", required=True)
    reproduce_parser.add_argument("--workdir", required=True)
    reproduce_parser.add_argument("--not-after", default=None)
    reproduce_parser.add_argument("--collected-ids", required=True)
    reproduce_parser.add_argument("--ceiling", required=True)
    reproduce_parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    reproduce_parser.add_argument("--rev", default="HEAD")
    reproduce_parser.set_defaults(handler=reproduce_command)
    check_parser = subparsers.add_parser("check", help="Gate-check a baseline against its source runs.")
    check_parser.add_argument("--baseline", required=True)
    check_parser.add_argument("--manifest", required=True)
    check_parser.add_argument("--collected-ids", required=True)
    check_parser.add_argument("--report-out", default=None)
    check_parser.set_defaults(handler=check_command)
    drift_parser = subparsers.add_parser("medium-drift", help="Compare medium shard durations with shard junit.")
    drift_parser.add_argument("--recorded", required=True)
    drift_parser.add_argument("--shard", action="append", default=[])
    drift_parser.add_argument("--report-out", default=None)
    drift_parser.add_argument("--collected", default=None)
    drift_parser.add_argument("--projected-durations", default=None)
    drift_parser.set_defaults(handler=medium_drift_command)
    refresh_parser = subparsers.add_parser(
        "medium-refresh", help="Rebuild the medium shard durations from shard junit."
    )
    refresh_parser.add_argument("--run-dir", action="append", required=True)
    refresh_parser.add_argument("--output", required=True)
    refresh_parser.add_argument("--source", required=True)
    refresh_parser.add_argument("--generated-at", default=None)
    refresh_parser.add_argument("--collected", default=None)
    refresh_parser.set_defaults(handler=medium_refresh_command)
    verify_parser = subparsers.add_parser("verify", help="Report a PR fast-tier run against the committed baseline.")
    verify_parser.add_argument("--baseline", required=True)
    verify_parser.add_argument("--run-dir", required=True)
    verify_parser.add_argument("--ceiling", default="quality/fast-duration-ceiling.json")
    verify_parser.add_argument("--summary", default=None)
    verify_parser.add_argument("--report-out", default=None)
    verify_parser.add_argument("--run-id", default=None)
    verify_parser.add_argument("--run-attempt", default=None)
    verify_parser.add_argument("--mode", choices=["report", "block"], default=VERIFY_MODE)
    verify_parser.set_defaults(handler=verify_command)
    ceiling_parser = subparsers.add_parser("ceiling-check", help="Check a baseline against the absolute ceiling.")
    ceiling_parser.add_argument("--baseline", required=True)
    ceiling_parser.add_argument("--collected-ids", required=True)
    ceiling_parser.add_argument("--ceiling", required=True)
    ceiling_parser.add_argument("--previous", default=None)
    ceiling_parser.add_argument("--report-out", default=None)
    ceiling_parser.add_argument("--markdown-out", default=None)
    ceiling_parser.set_defaults(handler=ceiling_check_command)
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except BaselineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
