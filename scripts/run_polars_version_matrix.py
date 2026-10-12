#!/usr/bin/env python3

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import run_version_matrix
from scripts.run_version_matrix import (
    CRITICAL_PRESSURE,
    CRITICAL_SAMPLES,
    OOM_EXIT_CODES,
    RUNTIME_DISTRIBUTIONS,
    Cell,
    _append_log,
    _find_optional_result_path as _find_result_path,
    _git,
    _kill_group,
    _run_logged,
    cell_command,
    cell_options,
    check_pins,
    classify_outcome,
    current_pins,
    directory_bytes,
    extract_result,
    generate_command,
    host_description,
    host_state,
    monitor,
    parse_freeze,
    plan_lines,
    prepare_cell_environment,
    pressure_level,
    prime_command,
    run_cell,
    run_polars_matrix as run_matrix,
    swap_out_bytes,
    verify_installation,
)
from scripts.version_matrix_specs import POLARS

__all__ = [
    "CRITICAL_PRESSURE",
    "CRITICAL_SAMPLES",
    "OOM_EXIT_CODES",
    "RUNTIME_DISTRIBUTIONS",
    "Cell",
    "REPO_ROOT",
    "_append_log",
    "_find_result_path",
    "_git",
    "_kill_group",
    "_run_logged",
    "check_pins",
    "cell_command",
    "cell_options",
    "classify_outcome",
    "current_pins",
    "directory_bytes",
    "extract_result",
    "generate_command",
    "host_description",
    "host_state",
    "monitor",
    "parse_freeze",
    "plan_lines",
    "prepare_cell_environment",
    "pressure_level",
    "prime_command",
    "run_cell",
    "run_matrix",
    "swap_out_bytes",
    "verify_installation",
]

CLI_DESCRIPTION = (
    "Run the Polars version matrix.\n"
    "\n"
    "Each round runs every (benchmark, version, engine setup) cell once, in an order\n"
    "shuffled from --shuffle-seed. A cell is one BenchBox invocation with one\n"
    "measurement iteration and the per-query warm-up. Before each cell the runner\n"
    "reinstalls the cell's Polars version and verifies it. Artifacts stay outside the\n"
    "checkout and are recorded in ``matrix-manifest.json``; analyze them with\n"
    "``scripts/analyze_polars_version_matrix.py``.\n"
    "\n"
    "Start it only on a quiet machine and run it from the BenchBox checkout with\n"
    "``uv run --no-sync`` so the installed Polars is not reverted between cells.\n"
)


def main(argv: list[str] | None = None) -> int:
    return run_version_matrix.main(argv, spec=POLARS, description=CLI_DESCRIPTION)


if __name__ == "__main__":
    raise SystemExit(main())
