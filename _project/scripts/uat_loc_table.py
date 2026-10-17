#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = REPO_ROOT / "_project" / "specs" / "uat-framework.md"
BUDGET_PATH = REPO_ROOT / "_project" / "specs" / "uat-loc-budget.json"
UAT_DIR = REPO_ROOT / "tests" / "uat"

BUCKETS: list[tuple[str, list[str]]] = [
    ("plumbing (orchestrator/config/`_cli`)", ["orchestrator.py", "config.py", "_cli.py"]),
    (
        "core exercise (execute/matrix/runner/enumerate/cleanup/ladder)",
        ["phases/execute.py", "matrix.py", "runner.py", "phases/enumerate.py", "cleanup.py", "ladder.py"],
    ),
    (
        "preflight/compat/timeouts",
        ["phases/preflight.py", "compatibility.py", "timeouts.py", "preflight_budget.py"],
    ),
    (
        "Docker lifecycle (default-OFF, incl. `container_cleanup.py`)",
        ["docker_assets.py", "docker_cleanup.py", "container_cleanup.py"],
    ),
    (
        "chartered evidence artifacts (validate/report/package/cells_io/gate_summary)",
        ["phases/validate.py", "phases/report.py", "phases/package.py", "cells_io.py", "gate_summary.py"],
    ),
    ("throughput", ["throughput.py", "throughput_baseline.py"]),
    ("artifact hygiene", ["artifact_hygiene.py"]),
    ("package init markers", ["__init__.py", "phases/__init__.py"]),
]

ALL_MODULES: list[str] = [name for _label, names in BUCKETS for name in names]


def loc(module: str) -> int:
    return len((UAT_DIR / module).read_text(encoding="utf-8").splitlines())


def _module_name_from_row(row: str) -> str | None:
    cells = row.split("|")
    if len(cells) < 4:
        return None
    first = cells[1].strip()
    if not (first.startswith("`") and first.endswith("`")):
        return None
    return first.strip("`")


def _validate_table_coverage(text: str) -> None:
    found = {name for row in text.splitlines() if (name := _module_name_from_row(row)) is not None}
    missing = set(ALL_MODULES) - (found & set(ALL_MODULES))
    if missing:
        raise SystemExit(f"error: bucket map lists modules absent from the spec table: {sorted(missing)}")
    missing_files = [m for m in ALL_MODULES if not (UAT_DIR / m).exists()]
    if missing_files:
        raise SystemExit(f"error: bucket map lists modules absent from tests/uat/: {missing_files}")


def load_budgets() -> tuple[dict[str, int], int]:
    try:
        raw = json.loads(BUDGET_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(
            f"error: {BUDGET_PATH.relative_to(REPO_ROOT)} is missing; it commits the UAT LOC "
            "ceilings this gate enforces."
        ) from None
    budgets = raw.get("budgets", {})
    buckets = budgets.get("buckets", {})
    total = budgets.get("total")
    if not buckets or total is None:
        raise SystemExit(f"error: {BUDGET_PATH.relative_to(REPO_ROOT)} is missing budgets.buckets / budgets.total")

    labels = {label for label, _names in BUCKETS}
    unknown = set(buckets) - labels
    unbudgeted = labels - set(buckets)
    if unknown or unbudgeted:
        raise SystemExit(
            "error: bucket labels in uat-loc-budget.json disagree with BUCKETS in uat_loc_table.py.\n"
            f"  budgeted but unknown: {sorted(unknown)}\n"
            f"  known but unbudgeted: {sorted(unbudgeted)}"
        )
    return buckets, total


def measure() -> tuple[dict[str, int], int]:
    measured = {label: sum(loc(n) for n in names) for label, names in BUCKETS}
    return measured, sum(measured.values())


def _render_report(measured: dict[str, int], total: int, buckets: dict[str, int], total_budget: int) -> str:
    width = max(len(label) for label in measured)
    lines = ["UAT production LOC vs committed ceilings:", ""]
    for label, _names in BUCKETS:
        used = measured[label]
        cap = buckets[label]
        pct = (used / cap * 100) if cap else 0.0
        flag = "OVER" if used > cap else "ok"
        lines.append(f"  {label:<{width}}  {used:>6,} / {cap:>6,}  ({pct:5.1f}%)  {flag}")
    pct = (total / total_budget * 100) if total_budget else 0.0
    lines.append("")
    lines.append(
        f"  {'TOTAL':<{width}}  {total:>6,} / {total_budget:>6,}  ({pct:5.1f}%)  "
        f"{'OVER' if total > total_budget else 'ok'}   across {len(ALL_MODULES)} modules"
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Report/enforce the UAT production-LOC ceilings.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if any bucket, or the total, exceeds its committed ceiling (CI mode).",
    )
    args = parser.parse_args()

    _validate_table_coverage(SPEC_PATH.read_text(encoding="utf-8"))
    buckets, total_budget = load_budgets()
    measured, total = measure()

    breaches = [(label, measured[label], buckets[label]) for label, _n in BUCKETS if measured[label] > buckets[label]]
    if total > total_budget:
        breaches.append(("TOTAL", total, total_budget))

    if not args.check:
        print(_render_report(measured, total, buckets, total_budget))
        return 0

    if breaches:
        sys.stderr.write(_render_report(measured, total, buckets, total_budget) + "\n\n")
        for label, used, cap in breaches:
            sys.stderr.write(f"OVER BUDGET: {label} is {used:,} lines, ceiling {cap:,} (+{used - cap:,})\n")
        sys.stderr.write(
            "\nUAT has grown past its committed charter ceiling. This gate is not a drift check --\n"
            "there is nothing to regenerate, and `make guards-fix` will NOT fix it. Either:\n"
            "  1. bring the LOC back under the ceiling (extract, delete, or move code out of tests/uat/); or\n"
            "  2. deliberately raise the ceiling in _project/specs/uat-loc-budget.json, in a PR of its own,\n"
            "     with the charter argument for the new scope in the PR body.\n"
            "See the LOC-budget section of _project/specs/uat-framework.md.\n"
        )
        return 1

    print(f"UAT LOC within ceilings ({total:,} / {total_budget:,} total).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
