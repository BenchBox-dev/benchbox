from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tests.duration_policy import collect_junit_durations, write_duration_file

CLI_DESCRIPTION = "Regenerate the committed per-test p95 artifact from T3 JUnit reports."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--input", type=Path, action="append", required=True, help="JUnit XML report to merge")
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "tests" / "fixtures" / "test_durations.json",
        help="Committed duration artifact to write",
    )
    parser.add_argument("--source", default="T3 nightly JUnit reports", help="Provenance label for the artifact")
    parser.add_argument(
        "--min-seconds",
        type=float,
        help="Keep only tests at least this slow and record the mean of the rest as default_seconds",
    )
    args = parser.parse_args(argv)

    durations = collect_junit_durations(args.input)
    default_seconds = None
    if args.min_seconds is not None:
        kept = {nodeid: value for nodeid, value in durations.items() if value >= args.min_seconds}
        rest = [value for nodeid, value in durations.items() if nodeid not in kept]
        default_seconds = sum(rest) / len(rest) if rest else 0.0
        durations = kept
    write_duration_file(args.output, durations, source=args.source, default_seconds=default_seconds)
    print(f"wrote {len(durations)} test durations to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
