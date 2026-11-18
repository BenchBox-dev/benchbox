from __future__ import annotations

import argparse
import fnmatch
import json
import os
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchbox.utils.clock import elapsed_seconds, mono_time
from scripts.measure_sf1_sizes import sum_directory_bytes

BASELINE_PATH = "tests/unit/generators/sf1_size_baseline.json"
SMOKE_PHASES = "generate,load,power"
SMOKE_TIMEOUT_SECONDS = 1500.0

PURE_PYTHON_GENERATORS: dict[str, tuple[str, ...]] = {
    "tpcdi": (
        "benchbox/core/tpcdi/generator/*",
        "benchbox/core/tpcdi/generator_specs.yaml",
        "benchbox/core/tpcdi/source_generators.py",
    ),
    "amplab": ("benchbox/core/amplab/generator.py", "benchbox/core/amplab/generator_specs.yaml"),
    "h2odb": ("benchbox/core/h2odb/generator.py", "benchbox/core/h2odb/generator_specs.yaml"),
    "coffeeshop": ("benchbox/core/coffeeshop/generator.py", "benchbox/core/coffeeshop/generator_specs.yaml"),
    "tsbs_devops": ("benchbox/core/tsbs_devops/generator.py", "benchbox/core/tsbs_devops/generator_specs.yaml"),
    "joinorder_synthetic": (
        "benchbox/core/joinorder_synthetic/generator.py",
        "benchbox/core/joinorder_synthetic/generator_specs.yaml",
    ),
    "vector_search": ("benchbox/core/vector_search/generator.py", "benchbox/core/vector_search/generator_specs.yaml"),
    "write_primitives": (
        "benchbox/core/write_primitives/generator.py",
        "benchbox/core/write_primitives/generator_specs.yaml",
    ),
    "transaction_primitives": (
        "benchbox/core/transaction_primitives/generator.py",
        "benchbox/core/transaction_primitives/generator_specs.yaml",
    ),
}


@dataclass(frozen=True)
class SmokeResult:
    dataset_bytes: int
    queries_passed: int
    queries_failed: int
    rows_loaded: int
    seconds: float
    error: str | None = None


Smoke = Callable[[str], SmokeResult]
CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def affected_benchmarks(changed_files: list[str]) -> list[str]:
    return [
        benchmark
        for benchmark, patterns in PURE_PYTHON_GENERATORS.items()
        if any(fnmatch.fnmatchcase(name, pattern) for name in changed_files for pattern in patterns)
    ]


def load_baseline(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_base_baseline(path: Path | None) -> dict[str, object] | None:
    if path is None or not path.is_file() or not path.read_text(encoding="utf-8").strip():
        return None
    return load_baseline(path)


def baseline_size(baseline: dict[str, object], benchmark: str) -> int | None:
    sizes = baseline.get("sizes")
    value = sizes.get(benchmark) if isinstance(sizes, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def baseline_tolerance(baseline: dict[str, object]) -> float:
    value = baseline.get("tolerance")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value < 1:
        raise ValueError("size baseline has no usable tolerance")
    return float(value)


def evaluate_size(
    benchmark: str, measured: int, baseline: dict[str, object], base_baseline: dict[str, object] | None
) -> dict[str, Any]:
    expected = baseline_size(baseline, benchmark)
    tolerance = baseline_tolerance(baseline)
    if expected is None:
        return {
            "benchmark": benchmark,
            "measured_bytes": measured,
            "status": "fail",
            "reason": f"{BASELINE_PATH} has no SF 1 size for {benchmark}",
        }
    drift = (measured - expected) / expected
    record: dict[str, Any] = {
        "benchmark": benchmark,
        "measured_bytes": measured,
        "baseline_bytes": expected,
        "drift": drift,
        "tolerance": tolerance,
        "status": "pass" if abs(drift) <= tolerance else "fail",
    }
    if record["status"] == "pass":
        return record
    updated = base_baseline is not None and baseline_size(base_baseline, benchmark) != expected
    record["reason"] = (
        f"the SF 1 output is {measured} bytes, {drift:+.1%} from the baseline {expected} (tolerance "
        f"\u00b1{tolerance:.0%}); the baseline entry was changed in this diff but still does not match the generator"
        if updated
        else f"the SF 1 output is {measured} bytes, {drift:+.1%} from the baseline {expected} (tolerance "
        f"\u00b1{tolerance:.0%}); update {BASELINE_PATH} and the calibrated-size page in this change"
    )
    return record


def evaluate_smoke(smoke: SmokeResult) -> str | None:
    if smoke.error:
        return smoke.error
    if smoke.queries_failed:
        return f"{smoke.queries_failed} power-phase queries failed on DuckDB at SF 1"
    if smoke.queries_passed == 0:
        return "no power-phase query passed on DuckDB at SF 1"
    if smoke.rows_loaded <= 0:
        return "no rows were loaded into DuckDB at SF 1"
    return None


def run_smoke(benchmark: str, *, runner: CommandRunner = subprocess.run) -> SmokeResult:
    start = mono_time()
    with tempfile.TemporaryDirectory(prefix="generator-volume-") as tmp:
        output = Path(tmp)
        result = runner(
            [
                "uv",
                "run",
                "--no-sync",
                "--",
                "benchbox",
                "run",
                "--platform",
                "duckdb",
                "--benchmark",
                benchmark,
                "--scale",
                "1",
                "--phases",
                SMOKE_PHASES,
                "--compression",
                "none",
                "--non-interactive",
                "--quiet",
            ],
            env={**os.environ, "BENCHBOX_OUTPUT_DIR": str(output)},
            capture_output=True,
            text=True,
            timeout=SMOKE_TIMEOUT_SECONDS,
            check=False,
        )
        seconds = elapsed_seconds(start)
        if result.returncode != 0:
            tail = (result.stderr or result.stdout).strip().splitlines()[-5:]
            return SmokeResult(0, 0, 0, 0, seconds, f"benchbox run exited {result.returncode}: {' | '.join(tail)}")
        bundles = sorted((output / "results").glob("*.json"))
        if not bundles:
            return SmokeResult(0, 0, 0, 0, seconds, "benchbox run wrote no result bundle")
        summary = json.loads(bundles[-1].read_text(encoding="utf-8")).get("summary", {})
        queries = summary.get("queries", {})
        data = summary.get("data", {})
        dataset_bytes, _count = sum_directory_bytes(output / "datagen")
        return SmokeResult(
            dataset_bytes,
            int(queries.get("passed", 0)),
            int(queries.get("failed", 0)),
            int(data.get("rows_loaded", 0)),
            seconds,
        )


def run_gate(
    changed_files: list[str],
    baseline: dict[str, object],
    base_baseline: dict[str, object] | None,
    *,
    smoke: Smoke | None = None,
) -> dict[str, Any]:
    measure = run_smoke if smoke is None else smoke
    benchmarks = affected_benchmarks(changed_files)
    if not benchmarks:
        return {"status": "skipped", "reason": "no pure-Python generator or generator spec changed", "benchmarks": []}
    start = mono_time()
    records: list[dict[str, object]] = []
    for benchmark in benchmarks:
        result = measure(benchmark)
        failure = evaluate_smoke(result)
        if failure:
            records.append({"benchmark": benchmark, "status": "fail", "reason": failure, "seconds": result.seconds})
            continue
        record = evaluate_size(benchmark, result.dataset_bytes, baseline, base_baseline)
        record |= {
            "seconds": result.seconds,
            "queries_passed": result.queries_passed,
            "rows_loaded": result.rows_loaded,
        }
        records.append(record)
    failed = any(record["status"] == "fail" for record in records)
    return {
        "status": "fail" if failed else "pass",
        "benchmarks": records,
        "total_seconds": elapsed_seconds(start),
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = ["### generator-volume", "", f"Result: **{report['status']}**", ""]
    if report["status"] == "skipped":
        lines.append(str(report["reason"]))
        return "\n".join(lines) + "\n"
    lines += [
        "| Benchmark | Status | Measured bytes | Baseline bytes | Drift | Seconds |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for record in report["benchmarks"]:
        drift = record.get("drift")
        lines.append(
            f"| {record['benchmark']} | {record['status']} | {record.get('measured_bytes', 'n/a')} | "
            f"{record.get('baseline_bytes', 'n/a')} | {'n/a' if drift is None else format(drift, '+.2%')} | "
            f"{record.get('seconds', 0.0):.0f} |"
        )
    for record in report["benchmarks"]:
        if record.get("reason"):
            lines.append(f"\n{record['benchmark']}: {record['reason']}")
    lines.append(f"\nTotal runtime: {report.get('total_seconds', 0.0):.0f} s")
    return "\n".join(lines) + "\n"


def read_changed(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def git_output(*args: str) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", check=False)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])} failed: {result.stderr.strip()}")
    return result.stdout


def changed_since(base_ref: str) -> list[str]:
    return [
        line for line in git_output("diff", "--name-only", "--no-renames", f"{base_ref}...HEAD").splitlines() if line
    ]


def baseline_at(base_ref: str) -> dict[str, object] | None:
    try:
        text = git_output("show", f"{base_ref}:{BASELINE_PATH}")
    except RuntimeError:
        return None
    return json.loads(text) if text.strip() else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Re-measure SF 1 output volume for a changed pure-Python generator.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--changed-files", type=Path)
    source.add_argument("--base-ref")
    parser.add_argument("--baseline", type=Path, default=Path(BASELINE_PATH))
    parser.add_argument("--base-baseline", type=Path, default=None)
    parser.add_argument("--summary", type=Path, default=None)
    parser.add_argument("--list-affected", action="store_true")
    args = parser.parse_args(argv)
    changed = changed_since(args.base_ref) if args.base_ref else read_changed(args.changed_files)
    if args.list_affected:
        print(json.dumps(affected_benchmarks(changed)))
        return 0
    base_baseline = baseline_at(args.base_ref) if args.base_ref else load_base_baseline(args.base_baseline)
    report = run_gate(changed, load_baseline(args.baseline), base_baseline)
    text = render_markdown(report)
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as handle:
            handle.write(text)
    print(text)
    for record in report["benchmarks"]:
        if record["status"] == "fail":
            print(f"::error title=generator-volume::{record['benchmark']}: {record['reason']}")
    return 1 if report["status"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
