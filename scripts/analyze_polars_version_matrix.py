#!/usr/bin/env python3

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import analyze_version_matrix
from scripts.analyze_version_matrix import (
    ACCEPTED_STATUSES,
    EVIDENCE_REQUIRED,
    PINNED_KEYS,
    CellKey,
    _stats,
    _totals,
    analyze_polars as analyze,
    assess_cells,
    assess_record,
    compare_cells as compare,
    group_records,
    load_manifests,
    load_qualification,
    timing_stats,
    write_polars_outputs as write_outputs,
)
from scripts.version_matrix_specs import POLARS

__all__ = [
    "ACCEPTED_STATUSES",
    "EVIDENCE_REQUIRED",
    "PINNED_KEYS",
    "CellKey",
    "REPO_ROOT",
    "_stats",
    "_totals",
    "analyze",
    "assess_cells",
    "assess_record",
    "compare",
    "group_records",
    "load_manifests",
    "load_qualification",
    "timing_stats",
    "write_outputs",
]

CLI_DESCRIPTION = "Analyze Polars version-matrix manifests against the qualification file."


def main(argv: list[str] | None = None) -> int:
    return analyze_version_matrix.main(argv, spec=POLARS, description=CLI_DESCRIPTION)


if __name__ == "__main__":
    raise SystemExit(main())
