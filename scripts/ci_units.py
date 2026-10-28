#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Iterable

from path_filter_decision import matches_any, normalize_path, unquote_yaml_scalar

CLI_DESCRIPTION = (
    "Decide which merge units a change touches.\n"
    "\n"
    "Reads ``.github/ci-units.yml`` and a newline-delimited list of changed paths\n"
    "(or a diff against a base ref) and reports which of the five units\n"
    "(``core``, ``explorer``, ``results-data``, ``docs``, ``tooling``)\n"
    "have work to do.\n"
    "\n"
    "Rules:\n"
    "\n"
    "* A path belongs to every unit whose patterns match it.\n"
    "* A path that matches no unit belongs to ``core`` (fail closed).\n"
    "* A path matching ``all-units`` runs every unit.\n"
    "* An empty change set runs every unit (fail closed).\n"
    "* A changed Markdown file, or a deleted or renamed path of any kind, also\n"
    "  runs ``docs``, whose link check can fail on the missing target.\n"
    "\n"
    "The script is stdlib-only so the classifier job needs no dependency sync.\n"
)

UNITS: tuple[str, ...] = ("core", "explorer", "results-data", "docs", "tooling")
ALL_UNITS_KEY = "all-units"
DEFAULT_RULES = Path(__file__).resolve().parents[1] / ".github" / "ci-units.yml"


def load_unit_rules(path: Path) -> dict[str, list[str]]:
    rules: dict[str, list[str]] = {}
    current: str | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not line.startswith(" ") and line.endswith(":"):
            current = line[:-1].strip()
            rules[current] = []
            continue
        if current and line.lstrip().startswith("- "):
            rules[current].append(unquote_yaml_scalar(line.lstrip()[2:]))
    missing = [unit for unit in (*UNITS, ALL_UNITS_KEY) if unit not in rules]
    if missing:
        raise ValueError(f"{path} is missing unit keys: {', '.join(missing)}")
    unknown = sorted(set(rules) - {*UNITS, ALL_UNITS_KEY})
    if unknown:
        raise ValueError(f"{path} has unknown unit keys: {', '.join(unknown)}")
    return rules


def classify_units(
    changed_paths: Iterable[str], rules: dict[str, list[str]], removed_paths: Iterable[str] = ()
) -> dict[str, object]:
    paths = [normalize_path(p) for p in changed_paths if normalize_path(p)]
    removed = {normalize_path(p) for p in removed_paths}
    unit_paths: dict[str, list[str]] = {unit: [] for unit in UNITS}
    unowned: list[str] = []
    all_units_paths = [p for p in paths if matches_any(p, rules[ALL_UNITS_KEY])]

    for path in paths:
        owners = [unit for unit in UNITS if matches_any(path, rules[unit])]
        if not owners:
            unowned.append(path)
            owners = ["core"]
        if (path.endswith(".md") or path in removed) and "docs" not in owners:
            owners.append("docs")
        for unit in owners:
            unit_paths[unit].append(path)

    run_all = not paths or bool(all_units_paths)
    needed = {unit: run_all or bool(unit_paths[unit]) for unit in UNITS}
    return {
        "changed_paths": paths,
        "run_all": run_all,
        "unowned_paths": unowned,
        "units": needed,
        "unit_paths": unit_paths,
        "code_tests_needed": needed["core"] or needed["tooling"],
    }


def git_changed_paths(base_ref: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", "--diff-filter=ACDMRT", f"{base_ref}...HEAD"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    return [normalize_path(line) for line in result.stdout.splitlines() if normalize_path(line)]


def git_removed_paths(base_ref: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", "--diff-filter=D", f"{base_ref}...HEAD"],
        check=True,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
    )
    return [normalize_path(line) for line in result.stdout.splitlines() if normalize_path(line)]


def write_github_output(path: Path, decision: dict[str, object]) -> None:
    units: dict[str, bool] = decision["units"]
    lines = [f"unit-{unit}={'true' if units[unit] else 'false'}" for unit in UNITS]
    lines.append(f"code-tests-needed={'true' if decision['code_tests_needed'] else 'false'}")
    lines.append(f"run-all-units={'true' if decision['run_all'] else 'false'}")
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def write_summary(path: Path, decision: dict[str, object]) -> None:
    units: dict[str, bool] = decision["units"]
    unit_paths: dict[str, list[str]] = decision["unit_paths"]
    rows = ["### CI units", "", "| unit | runs | changed paths |", "| --- | --- | ---: |"]
    for unit in UNITS:
        rows.append(f"| {unit} | {'yes' if units[unit] else 'no'} | {len(unit_paths[unit])} |")
    if decision["run_all"]:
        rows.append("")
        rows.append("All units run (self-protection path or empty change set).")
    if decision["unowned_paths"]:
        rows.append("")
        rows.append("Unowned paths routed to core: " + ", ".join(f"`{p}`" for p in decision["unowned_paths"][:20]))
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(rows) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    parser.add_argument("--base-ref", help="Git ref to diff against, for example a base SHA")
    parser.add_argument("--changed-file", type=Path, help="Read changed paths from a newline-delimited file")
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args(argv)

    rules = load_unit_rules(args.rules)
    removed: list[str] = []
    if args.changed_file:
        paths = [normalize_path(line) for line in args.changed_file.read_text(encoding="utf-8").splitlines()]
    elif args.base_ref:
        try:
            paths = git_changed_paths(args.base_ref)
            removed = git_removed_paths(args.base_ref)
        except (subprocess.CalledProcessError, OSError) as exc:
            print(f"ci_units: diff failed ({exc}); running every unit", file=sys.stderr)
            paths = []
    else:
        paths = []

    decision = classify_units(paths, rules, removed)
    if args.json_out:
        args.json_out.write_text(json.dumps(decision, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.github_output:
        write_github_output(args.github_output, decision)
    if args.summary:
        write_summary(args.summary, decision)
    if not (args.github_output or args.json_out):
        print(json.dumps(decision["units"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
