from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NamedTuple

from benchbox.core.results.metrics import TPCMetricsCalculator
from benchbox.core.throughput.result import throughput_stream_ids
from tests.uat import throughput_baseline as baseline

TPC_ALLOWED_SCALE_FACTORS = {1, 10, 30, 100, 300, 1000, 3000, 10000, 30000, 100000}


def resolve_official_result_path(results_dir: Path, *, emitted_path: str | None = None) -> Path | None:
    if not emitted_path:
        return None
    path = Path(emitted_path).expanduser()
    try:
        if path.is_absolute() or path.exists():
            return path
    except OSError:
        return None
    runs_dir = results_dir.parent
    if len(path.parts) >= 2 and path.parts[0] == "benchmark_runs":
        return runs_dir.parent / path
    return runs_dir / path


def _throughput_rows(result_json: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        query
        for query in result_json.get("queries") or []
        if isinstance(query, dict) and query.get("test_type") == "throughput" and query.get("stream") is not None
    ]


def _is_success(query: dict[str, Any]) -> bool:
    return str(query.get("status", "")).upper() == "SUCCESS"


def _token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def validate_result_identity(
    result_json: dict[str, Any], *, platform: str, benchmark: str, scale: float
) -> tuple[bool, str]:
    section = result_json.get("benchmark") or {}
    found = ((result_json.get("platform") or {}).get("name"), section.get("id"), section.get("scale_factor"))
    if _token(found[0]) != _token(platform) or found[0] is None:
        return False, f"result JSON platform {found[0]!r} does not match requested {platform!r}"
    if _token(found[1]) != _token(benchmark) or found[1] is None:
        return False, f"result JSON benchmark {found[1]!r} does not match requested {benchmark!r}"
    if not isinstance(found[2], (int, float)) or not math.isclose(float(found[2]), float(scale)):
        return False, f"result JSON scale factor {found[2]!r} does not match requested {scale!r}"
    return True, "ok"


def validate_stream_count(result_json: dict[str, Any], *, requested_streams: int) -> tuple[bool, str]:
    streams_seen = {row["stream"] for row in _throughput_rows(result_json)}
    executed = len(streams_seen)
    if executed != requested_streams:
        return (
            False,
            f"throughput stream count mismatch: requested {requested_streams}, executed {executed}",
        )
    expected_ids = set(throughput_stream_ids(requested_streams))
    if streams_seen != expected_ids:
        return (
            False,
            f"throughput stream ids {sorted(streams_seen)} do not match the spec numbering {sorted(expected_ids)}",
        )
    return True, "ok"


def validate_stream_success(result_json: dict[str, Any]) -> tuple[bool, str]:
    rows = _throughput_rows(result_json)
    if not rows:
        return False, "no throughput rows in result JSON"
    failed_streams = sorted({row["stream"] for row in rows} - {row["stream"] for row in rows if _is_success(row)})
    if failed_streams:
        return False, f"stream(s) {failed_streams} executed but had zero SUCCESSFUL queries"
    return True, "ok"


def validate_throughput_metric(result_json: dict[str, Any]) -> tuple[bool, str]:
    tpc_metrics = (result_json.get("summary") or {}).get("tpc_metrics") or {}
    throughput_at_size = tpc_metrics.get("throughput_at_size")
    if not isinstance(throughput_at_size, (int, float)) or throughput_at_size <= 0:
        return False, f"Throughput@Size not positive (got {throughput_at_size!r})"

    rows = _throughput_rows(result_json)
    total_queries = sum(1 for row in rows if _is_success(row))
    scale_factor = (result_json.get("benchmark") or {}).get("scale_factor")
    duration_ms = ((result_json.get("phases") or {}).get("throughput_test") or {}).get("duration_ms")
    if not all(isinstance(value, (int, float)) and value > 0 for value in (scale_factor, duration_ms)):
        return False, f"Throughput@Size plausibility unavailable: scale {scale_factor!r}, duration {duration_ms!r}"

    expected = TPCMetricsCalculator.calculate_throughput_at_size(
        total_queries=total_queries,
        total_time_seconds=duration_ms / 1000.0,
        scale_factor=float(scale_factor),
        num_streams=len({row["stream"] for row in rows}),
    )
    if not expected / 2.0 <= throughput_at_size <= expected * 2.0:
        return (
            False,
            f"Throughput@Size outside plausibility band: got {throughput_at_size:.2f}, expected {expected:.2f}",
        )
    return True, "ok"


def validate_throughput_result(
    result_json: dict[str, Any],
    *,
    requested_streams: int,
    platform: str | None = None,
    benchmark: str | None = None,
    scale: float | None = None,
) -> tuple[bool, str]:
    checks = [
        lambda: validate_stream_count(result_json, requested_streams=requested_streams),
        lambda: validate_stream_success(result_json),
        lambda: validate_throughput_metric(result_json),
    ]
    if platform is not None and benchmark is not None and scale is not None:
        checks.insert(
            0, lambda: validate_result_identity(result_json, platform=platform, benchmark=benchmark, scale=scale)
        )
    for check in checks:
        ok, reason = check()
        if not ok:
            return ok, reason
    return True, "ok"


ThroughputGateError = ValueError


class CellResultFile(NamedTuple):
    result_path: Path
    payload: dict[str, Any]
    passed: bool


def load_cell_result(cells_glob: str, *, platform: str, benchmark: str, scale: float) -> CellResultFile:
    matches = sorted(glob.glob(os.path.expanduser(cells_glob)))
    if len(matches) != 1:
        raise ThroughputGateError(f"expected exactly one cells.jsonl matching {cells_glob!r}, found {len(matches)}")
    rows = [
        row
        for row in map(json.loads, filter(str.strip, Path(matches[0]).read_text(encoding="utf-8").splitlines()))
        if _token(row.get("platform")) == _token(platform)
        and _token(row.get("benchmark")) == _token(benchmark)
        and math.isclose(float(row.get("scale", math.nan)), float(scale))
    ]
    if len(rows) != 1:
        raise ThroughputGateError(
            f"expected exactly one {platform}/{benchmark}/sf{scale:g} cell in {matches[0]}, found {len(rows)}"
        )
    if not rows[0].get("result_path"):
        raise ThroughputGateError(f"cell {platform}/{benchmark}/sf{scale:g} recorded no result_path")
    result_path = Path(rows[0]["result_path"]).expanduser()
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ThroughputGateError(f"could not read result JSON {result_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ThroughputGateError(f"result JSON {result_path} is not an object")
    return CellResultFile(result_path, payload, rows[0].get("status") == "passed")


def evaluate_floor(
    observed: float | None, *, median: str | float | None, max_drop: str | float | None = None
) -> tuple[bool, str]:
    if median is None or median == "":
        return True, "no Throughput@Size floor median configured; floor check in observe-only mode"
    try:
        median_value, drop = float(median), float(max_drop or 0.2)
    except ValueError:
        median_value = drop = math.nan
    values = (median_value, drop, observed or math.nan)
    if not (all(map(math.isfinite, values)) and median_value > 0 and 0 < drop < 1 and observed > 0):
        return False, "Throughput@Size floor misconfigured or unobserved; refusing to gate"
    floor = median_value * (1 - drop)
    if observed < floor:
        return (
            False,
            f"Throughput@Size regression: observed {observed:.2f} below floor {floor:.2f} (median {median_value:.2f})",
        )
    return True, f"Throughput@Size {observed:.2f} clears floor {floor:.2f}"


def resolve_floor_median(history_dir: str | None, **where: Any) -> str | float | None:
    override = os.environ.get("THROUGHPUT_FLOOR_MEDIAN")
    if override or not history_dir:
        return override
    directory = Path(history_dir).expanduser()
    runner_class = baseline.runner_class_for(baseline.detect_cpu_model(), os.cpu_count() or 0)
    not_before = os.environ.get("THROUGHPUT_BASELINE_MIN_RECORDED_AT", "")
    records = baseline.load_baseline_records(directory, not_before=not_before)
    found = baseline.rolling_median(records, runner_class=runner_class, **where)
    count = found.count if found else 0
    verdict = f"rolling median {found.median:.2f}" if found and count >= baseline.MIN_FLOOR_SAMPLES else None
    level = "warning" if verdict is None and any(directory.rglob("throughput-baseline-*.json")) else "notice"
    print(
        f"::{level}::{count} of {baseline.MIN_FLOOR_SAMPLES} required baseline samples for runner class "
        f"{runner_class!r}; {verdict or 'floor stays observe-only'}"
    )
    return found.median if verdict else None


def _error(message: str) -> int:
    print(f"::error::{message}")
    return 1


def _run_assert(args: argparse.Namespace) -> int:
    where = {"platform": args.platform, "benchmark": args.benchmark, "scale": args.scale}
    cell = load_cell_result(args.cells_glob, **where)
    ok, reason = validate_throughput_result(cell.payload, requested_streams=args.streams, **where)
    if not ok:
        return _error(reason)
    observed = baseline.observed_throughput_at_size(cell.payload)
    print(f"OK: verified {cell.result_path}")
    print(f"::notice::Throughput@Size observed: {observed!r}")
    if args.evaluate_floor:
        median = resolve_floor_median(args.baseline_history, streams=args.streams, **where)
        ok, message = evaluate_floor(
            observed, median=median, max_drop=os.environ.get("THROUGHPUT_FLOOR_MAX_DROP_FRACTION")
        )
        if not ok:
            return _error(message)
        print(f"::notice::{message}" if "observe-only" in message else f"OK: {message}")
    if args.baseline_out and cell.passed:
        path = baseline.record_baseline(
            Path(args.baseline_out).expanduser(), cell.payload, streams=args.streams, **where
        )
        print(f"OK: recorded baseline {path}")
    return 0


def _run_rolling_median(args: argparse.Namespace) -> int:
    runner_class = args.runner_class or baseline.runner_class_for(baseline.detect_cpu_model(), os.cpu_count() or 0)
    records = baseline.load_baseline_records(Path(args.baseline_dir).expanduser())
    where = {key: getattr(args, key) for key in ("platform", "benchmark", "scale", "streams")}
    result = baseline.rolling_median(records, runner_class=runner_class, window=args.window, **where)
    if result is None:
        return _error(f"no retained baselines for runner class {runner_class!r}")
    print(json.dumps({"runner_class": runner_class, **result._asdict()}, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tests.uat.throughput")
    subparsers = parser.add_subparsers(dest="command", required=True)
    assert_parser = subparsers.add_parser("assert")
    assert_parser.add_argument("--cells-glob", required=True)
    assert_parser.add_argument("--evaluate-floor", action="store_true")
    assert_parser.add_argument("--baseline-out")
    assert_parser.add_argument("--baseline-history")
    assert_parser.set_defaults(handler=_run_assert)
    median_parser = subparsers.add_parser("rolling-median")
    median_parser.add_argument("--baseline-dir", required=True)
    median_parser.add_argument("--runner-class")
    median_parser.add_argument("--window", type=int, default=baseline.DEFAULT_ROLLING_WINDOW)
    median_parser.set_defaults(handler=_run_rolling_median)
    for subparser in (assert_parser, median_parser):
        subparser.add_argument("--platform", required=True)
        subparser.add_argument("--streams", type=int, required=True)
        subparser.add_argument("--benchmark", required=True)
        subparser.add_argument("--scale", type=float, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except ThroughputGateError as exc:
        return _error(str(exc))


if __name__ == "__main__":
    sys.exit(main())
