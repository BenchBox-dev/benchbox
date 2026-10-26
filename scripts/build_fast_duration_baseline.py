from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tests.duration_policy import _junit_nodeid, _percentile

SCHEMA = 1
MARKER_EXPRESSION = "fast and not (slow or stress or resource_heavy or live_integration)"
COVERAGE_ENABLED = True
XDIST_MODE = "auto"
GATE_SUM_TOLERANCE = 0.10
GATE_COVERAGE_MINIMUM = 0.95
MEDIUM_SKEW_THRESHOLD = 0.20
TEST_SHARD_COUNT = 8


class BaselineError(RuntimeError):
    pass


def read_calibration_best(run_dir: Path) -> tuple[float, dict[str, Any]]:
    try:
        payload = json.loads((run_dir / "calibration.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BaselineError(f"missing or unreadable calibration.json: {exc}") from exc
    workload = payload.get("workload") if isinstance(payload, dict) else None
    best = workload.get("best_seconds") if isinstance(workload, dict) else None
    if isinstance(best, bool) or not isinstance(best, (int, float)):
        raise BaselineError("calibration workload.best_seconds is not numeric")
    if not math.isfinite(best) or best <= 0:
        raise BaselineError("calibration workload.best_seconds is not finite and positive")
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
        raise BaselineError(f"missing or unreadable junit.xml: {exc}") from exc
    samples: dict[str, list[float]] = {}
    for testcase in root.iter("testcase"):
        raw_time = testcase.get("time")
        if raw_time is None:
            continue
        try:
            duration = float(raw_time)
        except ValueError as exc:
            raise BaselineError(f"invalid junit time {raw_time!r} in {junit_path}") from exc
        if not math.isfinite(duration) or duration < 0:
            raise BaselineError(f"invalid junit time {raw_time!r} in {junit_path}")
        nodeid = _junit_nodeid(testcase)
        if nodeid is None:
            continue
        samples.setdefault(nodeid, []).append(duration)
    if not samples:
        raise BaselineError(f"no usable testcase samples in {junit_path}")
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
        except BaselineError as exc:
            rejected.append(
                {
                    "run_id": entry["run_id"],
                    "pr": entry["pr"],
                    "sha": entry["sha"],
                    "artifact_id": entry["artifact_id"],
                    "reason": str(exc),
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
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    buckets: dict[int, list[dict[str, Any]]] = {index: [] for index in range(TEST_SHARD_COUNT)}
    for record in baseline["tests"]:
        buckets[test_shard_index(record["id"])].append(record)
    written = [manifest_path]
    for index in range(TEST_SHARD_COUNT):
        shard_path = output_dir / f"tests-{index:02d}.json"
        shard_path.write_text(json.dumps(buckets[index], indent=2) + "\n", encoding="utf-8")
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
            }
        )
    overall_skew = float(statistics.median(all_ratios)) - 1 if all_ratios else None
    recommends = overall_skew is not None and abs(overall_skew) > MEDIUM_SKEW_THRESHOLD
    recommends = recommends or any(
        shard["sum_skew"] is not None and abs(float(shard["sum_skew"])) > MEDIUM_SKEW_THRESHOLD for shard in shards
    )
    measured_total = sum(len(shard_tests) for shard_tests in measured.values())
    return {
        "matched_test_count": len(all_ratios),
        "overall_measured_test_count": measured_total,
        "overall_median_ratio_skew": overall_skew,
        "recommends_refresh": recommends,
        "skew_threshold": MEDIUM_SKEW_THRESHOLD,
        "shards": shards,
    }


def build_medium_durations(runs: list[dict[str, dict[str, float]]], generated_at: str, source: str) -> dict[str, Any]:
    samples: dict[str, list[float]] = {}
    for run in runs:
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


def medium_refresh_command(args: argparse.Namespace) -> int:
    runs = [read_shard_measured(sorted(Path(run_dir).glob("*/shard-*-junit.xml"))) for run_dir in args.run_dir]
    generated_at = args.generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = build_medium_durations(runs, generated_at, args.source)
    Path(args.output).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return 0


def drift_markdown(report: dict[str, Any]) -> str:
    lines = [
        "### Medium-shard drift",
        "",
        f"Matched tests: {report['matched_test_count']} of {report['overall_measured_test_count']} measured",
        f"Overall median-ratio skew: {report['overall_median_ratio_skew']}",
        f"Refresh recommended: {report['recommends_refresh']}",
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


VERIFY_RATIO_THRESHOLD = 0.15
VERIFY_DELTA_SECONDS = 60.0
VERIFY_NEW_TEST_ALLOWANCE_SECONDS = 0.5


def normalized_pr_samples(run_dir: Path, baseline: dict[str, object]) -> tuple[dict[str, float], float, float]:
    best, _metadata = read_calibration_best(run_dir)
    reference = baseline.get("reference_calibration_best_seconds")
    if isinstance(reference, bool) or not isinstance(reference, (int, float)) or reference <= 0:
        raise BaselineError("baseline has no usable reference_calibration_best_seconds")
    factor = float(reference) / best
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
    return {
        "verdict": "would-block" if slower else ("pass" if ratio is not None else "neutral"),
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
    }


def verify_run(baseline_dir: Path, run_dir: Path) -> dict[str, object]:
    try:
        baseline = read_sharded_baseline(baseline_dir)
        baseline_tests = baseline_tests_map(baseline)
    except BaselineError as exc:
        return {"verdict": "neutral", "reason": f"no usable committed baseline: {exc}"}
    try:
        pr_tests, factor, best = normalized_pr_samples(run_dir, baseline)
    except BaselineError as exc:
        return {"verdict": "rejected", "reason": str(exc)}
    report = verify_durations(baseline_tests, pr_tests)
    report["speed_factor"] = factor
    report["calibration_best_seconds"] = best
    report["collection_max_seconds"] = read_collection_max(run_dir)
    return report


def verify_markdown(report: dict[str, object]) -> str:
    verdict = str(report["verdict"])
    lines = ["### verify-fast-duration (report only)", "", f"Verdict: **{verdict}**", ""]
    if "reason" in report:
        lines.append(f"Reason: {report['reason']}")
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
        f"| Summed-time ratio | {fmt(report['sum_ratio'], '.3f')} |",
        f"| Median per-test ratio | {fmt(report['median_test_ratio'], '.3f')} |",
        f"| Coverage of baseline ids | {fmt(report['coverage'], '.1%')} |",
        f"| Runner speed factor | {fmt(report['speed_factor'], '.3f')} |",
        f"| Pytest collection time (max) | {fmt(report['collection_max_seconds'], '.1f')} s |",
        "",
        f"Would block when the ratio exceeds {1 + float(report['ratio_threshold']):.2f} and the delta exceeds "
        f"{float(report['delta_threshold_seconds']):.0f} s (provisional thresholds; job wall time is not a metric).",
    ]
    if report["coverage_warning"]:
        lines.append(f"Warning: coverage is under {GATE_COVERAGE_MINIMUM:.0%} of baseline ids.")
    return "\n".join(lines) + "\n"


def verify_command(args: argparse.Namespace) -> int:
    report = verify_run(Path(args.baseline), Path(args.run_dir))
    text = verify_markdown(report)
    if args.summary:
        with Path(args.summary).open("a", encoding="utf-8") as handle:
            handle.write(text)
    if args.report_out:
        Path(args.report_out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(text)
    verdict = report["verdict"]
    if verdict == "would-block":
        print(
            "::warning title=verify-fast-duration::fast-tier time would be blocked by the duration gate (report only)"
        )
    elif verdict == "rejected":
        print(f"::warning title=verify-fast-duration::run rejected: {report['reason']}")
    return 0


def build_command(args: argparse.Namespace) -> int:
    generated_at = args.generated_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    baseline, _rejected = build_baseline(load_manifest(Path(args.manifest)), generated_at)
    written = write_sharded_baseline(Path(args.output), baseline)
    print(f"baseline: {len(baseline['tests'])} tests from {len(baseline['runs'])} runs")
    print(f"rejected runs: {len(baseline['rejected_runs'])}")
    for rejected in baseline["rejected_runs"]:
        print(f"rejected run {rejected['run_id']}: {rejected['reason']}")
    for path in written:
        print(f"wrote {path}")
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
    drift_parser.set_defaults(handler=medium_drift_command)
    refresh_parser = subparsers.add_parser(
        "medium-refresh", help="Rebuild the medium shard durations from shard junit."
    )
    refresh_parser.add_argument("--run-dir", action="append", required=True)
    refresh_parser.add_argument("--output", required=True)
    refresh_parser.add_argument("--source", required=True)
    refresh_parser.add_argument("--generated-at", default=None)
    refresh_parser.set_defaults(handler=medium_refresh_command)
    verify_parser = subparsers.add_parser("verify", help="Report a PR fast-tier run against the committed baseline.")
    verify_parser.add_argument("--baseline", required=True)
    verify_parser.add_argument("--run-dir", required=True)
    verify_parser.add_argument("--summary", default=None)
    verify_parser.add_argument("--report-out", default=None)
    verify_parser.set_defaults(handler=verify_command)
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except BaselineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
