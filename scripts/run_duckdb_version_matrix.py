#!/usr/bin/env python3

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import run_version_matrix
from scripts.version_matrix_specs import DUCKDB

CLI_DESCRIPTION = (
    "Run the reproducible DuckDB version matrix used by Results Explorer.\n"
    "\n"
    "The matrix is intentionally operator-run: it creates four SF10 synthetic\n"
    "datasets once, then loads and measures each dataset with seven DuckDB package\n"
    "versions. Each power cell is a separate BenchBox invocation, repeated three times. Generated\n"
    "artifacts stay outside the checkout and are recorded in ``matrix-manifest.json``. Run the\n"
    "analyzer with ``--explorer-bundles-dir`` to materialize one median bundle per cell for\n"
    "Results Explorer; the raw repetitions remain external.\n"
    "\n"
    "Run from the BenchBox checkout with:\n"
    "\n"
    "    uv run --no-sync -- python scripts/run_duckdb_version_matrix.py       --output-dir /Users/example/Developer/benchmark_runs/duckdb-version-matrix-20260829\n"
    "\n"
    "``--no-sync`` is required because the runner changes the active DuckDB wheel\n"
    "between subprocesses while the BenchBox project lock intentionally remains\n"
    "unchanged.\n"
)


def main(argv: list[str] | None = None) -> int:
    return run_version_matrix.main(argv, spec=DUCKDB, description=CLI_DESCRIPTION)


if __name__ == "__main__":
    raise SystemExit(main())
