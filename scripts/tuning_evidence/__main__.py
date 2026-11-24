from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from benchbox.utils.clock import utc_now
from scripts.tuning_evidence import harness, stats
from scripts.tuning_evidence.platforms import (
    DEFAULT_MAX_CHECKSUM_ROWS,
    LAYOUTS,
    PACKS,
    SEAMS,
    ChDBSeam,
    ClickHouseSeam,
    ServerSeam,
    StarRocksSeam,
)

JSON_NAME = "harness.json"
SUMMARY_NAME = "summary.md"
EXIT_CODES = {"completed": 0, "aborted": 2}

ARM_HELP = (
    "NAME=SPEC, repeatable. SPEC is notuning, tuned, a tuning YAML path, or comma-separated "
    "load=..., session=notuning|tuned, layout=NAME and from=ARM (ClickHouse server), pack=NAME (ClickHouse server). "
    f"Layouts: {', '.join(sorted(LAYOUTS))}. Packs: {', '.join(sorted(PACKS))}. "
    "Default: N=notuning and T=tuned."
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m scripts.tuning_evidence")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="load each arm once, then time shuffled, interleaved rounds")
    run.add_argument("--platform", required=True, choices=sorted(SEAMS))
    run.add_argument("--benchmark", default="tpch")
    run.add_argument("--scale", type=float, required=True)
    run.add_argument("--arm", action="append", default=[], help=ARM_HELP)
    run.add_argument("--aa", metavar="SPEC", help="A/A run: arms A and B share SPEC and B calibrates the run")
    run.add_argument("--baseline", help="reference arm (default: the first arm)")
    run.add_argument("--calibration-arm", help="an arm configured like the baseline, used as the in-run A/A")
    run.add_argument("--calibration-file", help="harness JSON from a passing A/A run on the same cell")
    run.add_argument("--queries", help="comma-separated query ids to run (default: all)")
    run.add_argument("--rounds", type=int, default=9)
    run.add_argument("--warmup-rounds", type=int, default=1)
    run.add_argument("--seed", type=int, default=42)
    run.add_argument("--timeout", type=float, default=300.0, help="per-query timeout and failure penalty, seconds")
    run.add_argument("--load-ceiling", type=float, default=8.0)
    run.add_argument("--round-retries", type=int, default=3)
    run.add_argument("--retry-wait", type=float, default=60.0)
    run.add_argument("--cold", action="store_true", help="drop engine caches before each query where supported")
    run.add_argument("--streams", type=int, default=0, help="also time N concurrent streams per arm and round")
    run.add_argument("--resamples", type=int, default=stats.DEFAULT_RESAMPLES)
    run.add_argument("--max-checksum-rows", type=int, default=DEFAULT_MAX_CHECKSUM_ROWS)
    run.add_argument("--data-dir", type=Path)
    run.add_argument("--work-dir", type=Path, help="where file databases go (default: OUT/databases)")
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--keep-databases", action="store_true")
    run.add_argument("--min-rounds", type=int, default=stats.RuleThresholds.min_rounds)
    run.add_argument("--aa-point-low", type=float, default=stats.RuleThresholds.aa_point_low)
    run.add_argument("--aa-point-high", type=float, default=stats.RuleThresholds.aa_point_high)
    run.add_argument("--load-settle-ratio", type=float, default=stats.RuleThresholds.load_settle_ratio)
    run.add_argument("--slow-query-ratio", type=float, default=stats.RuleThresholds.slow_query_ratio)
    run.add_argument("--host", default="localhost")
    run.add_argument("--port", type=int, help="server port (default: the platform's)")
    run.add_argument("--http-port", type=int, default=8040, help="StarRocks backend HTTP port for Stream Load")
    run.add_argument("--user", help="server user (default: the platform's)")
    run.add_argument("--password-env", help="environment variable holding the password (default: the platform's)")
    run.add_argument("--container", help="server container name, for memory evidence")
    run.add_argument("--container-cli", help="container CLI (default: mocker on macOS, docker elsewhere)")
    run.add_argument("--settle-timeout", type=float, default=600.0)

    report = commands.add_parser("report", help="render the markdown summary from a harness JSON file")
    report.add_argument("json", type=Path)
    report.add_argument("--out", type=Path)
    return parser


def arms_from(args: argparse.Namespace) -> tuple[tuple[harness.ArmSpec, ...], str | None]:
    if args.aa:
        if args.arm:
            raise ValueError("--aa and --arm cannot be combined")
        first = harness.parse_arm(f"A={args.aa}")
        return (first, harness.parse_arm(f"B={args.aa}")), "B"
    specs = args.arm or ["N=notuning", "T=tuned"]
    return tuple(harness.parse_arm(spec) for spec in specs), args.calibration_arm


def config_from(args: argparse.Namespace) -> harness.HarnessConfig:
    arms, calibration_arm = arms_from(args)
    if args.streams and args.platform == ChDBSeam.platform_name:
        raise ValueError("throughput streams are not supported on chDB")
    config = harness.HarnessConfig(
        platform=args.platform,
        benchmark=args.benchmark,
        scale_factor=args.scale,
        arms=arms,
        baseline=args.baseline or arms[0].name,
        calibration_arm=calibration_arm,
        calibration_file=args.calibration_file,
        rounds=args.rounds,
        warmup_rounds=args.warmup_rounds,
        seed=args.seed,
        timeout_seconds=args.timeout,
        load_ceiling=args.load_ceiling,
        round_retries=args.round_retries,
        retry_wait_seconds=args.retry_wait,
        cold=args.cold,
        streams=args.streams,
        resamples=args.resamples,
        queries=tuple(query.strip() for query in args.queries.split(",")) if args.queries else None,
        keep_databases=args.keep_databases,
        thresholds=stats.RuleThresholds(
            min_rounds=args.min_rounds,
            aa_point_low=args.aa_point_low,
            aa_point_high=args.aa_point_high,
            load_settle_ratio=args.load_settle_ratio,
            slow_query_ratio=args.slow_query_ratio,
        ),
    )
    harness.validate_config(config)
    return config


def seam_from(args: argparse.Namespace, run_tag: str) -> Any:
    seam_class = SEAMS[args.platform]
    kwargs: dict[str, Any] = {
        "benchmark": args.benchmark,
        "scale_factor": args.scale,
        "work_dir": args.work_dir or args.out / "databases",
        "run_tag": run_tag,
        "data_dir": args.data_dir,
        "max_checksum_rows": args.max_checksum_rows,
    }
    if issubclass(seam_class, (ClickHouseSeam, StarRocksSeam)):
        kwargs["timeout_seconds"] = args.timeout
    if issubclass(seam_class, StarRocksSeam):
        kwargs["http_port"] = args.http_port
    if issubclass(seam_class, ServerSeam):
        kwargs.update(
            host=args.host,
            port=args.port,
            user=args.user,
            password=os.environ.get(args.password_env or seam_class.password_env, ""),
            container=args.container,
            container_cli=args.container_cli,
            settle_timeout_seconds=args.settle_timeout,
        )
    return seam_class(**kwargs)


def run(args: argparse.Namespace, argv: Sequence[str]) -> int:
    config = config_from(args)
    json_path = args.out / JSON_NAME
    if json_path.exists():
        raise ValueError(f"{json_path} already exists; choose a new --out directory")
    args.out.mkdir(parents=True, exist_ok=True)
    (args.work_dir or args.out / "databases").mkdir(parents=True, exist_ok=True)
    run_tag = utc_now().strftime("%Y%m%d%H%M%S")
    payload = harness.Harness(config, seam_from(args, run_tag)).run()
    payload["command"] = ["python", "-m", "scripts.tuning_evidence", *argv]
    json_path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    summary_path = args.out / SUMMARY_NAME
    summary_path.write_text(harness.render_markdown(payload), encoding="utf-8")
    print(f"status: {payload['status']}\njson: {json_path}\nsummary: {summary_path}")
    return EXIT_CODES.get(payload["status"], 1)


def report(args: argparse.Namespace) -> int:
    payload = json.loads(args.json.read_text(encoding="utf-8"))
    text = harness.render_markdown(payload)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(arguments)
    try:
        if args.command == "report":
            return report(args)
        return run(args, arguments)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
