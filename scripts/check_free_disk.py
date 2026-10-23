#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from benchbox.utils.disk_guard import (
    DEFAULT_MIN_FREE_PERCENT,
    MIN_FREE_PERCENT_ENV,
    LowDiskSpaceError,
    ensure_free_space,
)
from benchbox.utils.path_utils import resolve_benchmark_runs_dir

CLI_DESCRIPTION = (
    "Fail when free space on the benchmark output volume is below a threshold.\n"
    "\n"
    "Exit status: 0 enough free space; 1 below the threshold (message names free and total\n"
    "bytes and the largest top-level consumers); 2 invalid arguments.\n"
    "This check never deletes anything."
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--path", type=Path, default=None, help="Path to check (default: benchmark runs directory).")
    parser.add_argument(
        "--min-free-percent",
        type=float,
        default=None,
        help=f"Minimum free percent (default: ${MIN_FREE_PERCENT_ENV} or {DEFAULT_MIN_FREE_PERCENT:g}).",
    )
    args = parser.parse_args(argv)
    path = args.path if args.path is not None else resolve_benchmark_runs_dir()
    try:
        status = ensure_free_space(path, args.min_free_percent)
    except LowDiskSpaceError as exc:
        print(exc, file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Disk OK on {status.path}: {status.free_percent:.2f}% free (minimum {status.min_free_percent:g}%).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
