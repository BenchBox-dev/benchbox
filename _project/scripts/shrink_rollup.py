from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import yaml

DEFAULT_TARGET_MIN = 12_000
DEFAULT_TARGET_MAX = 19_000
DEFAULT_REF = "origin/develop"
DEFAULT_LEDGER_DIR = "_project/shrink-ledger"


CLI_DESCRIPTION = "Roll up merged shrink-campaign ledger fragments from a Git ref."


@dataclass(frozen=True)
class LedgerRow:
    path: str
    surface: str
    credited_reduction: int
    raw_cloc_delta: int
    uncredited_relocation: int
    repair_only_delta: int
    generated_python_delta: int


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True, encoding="utf-8", stderr=subprocess.DEVNULL)


def _frontmatter(text: str, path: str) -> dict[str, Any] | None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"{path}: missing YAML frontmatter")
    try:
        end = next(i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration as exc:
        raise ValueError(f"{path}: unterminated YAML frontmatter") from exc
    data = yaml.safe_load("\n".join(lines[1:end])) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: frontmatter must be a mapping")
    if data.get("template") is True:
        return None
    return data


def _int_field(data: dict[str, Any], path: str, name: str) -> int:
    value = data.get(name, 0)
    if value in ("", None):
        value = 0
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{path}: {name} must be an integer, got {value!r}") from exc


def _ledger_paths(ref: str, ledger_dir: str) -> list[str]:
    output = _git("ls-tree", "-r", "--name-only", ref, ledger_dir)
    return [line for line in output.splitlines() if line.endswith(".md") and not line.endswith("/TEMPLATE.md")]


def _read_fragment(ref: str, path: str) -> str:
    return _git("show", f"{ref}:{path}")


def load_rows(ref: str, ledger_dir: str) -> list[LedgerRow]:
    rows: list[LedgerRow] = []
    for path in _ledger_paths(ref, ledger_dir):
        data = _frontmatter(_read_fragment(ref, path), path)
        if data is None:
            continue
        rows.append(
            LedgerRow(
                path=path,
                surface=str(data.get("surface") or Path(path).stem),
                credited_reduction=_int_field(data, path, "credited_reduction"),
                raw_cloc_delta=_int_field(data, path, "raw_cloc_delta"),
                uncredited_relocation=_int_field(data, path, "uncredited_relocation"),
                repair_only_delta=_int_field(data, path, "repair_only_delta"),
                generated_python_delta=_int_field(data, path, "generated_python_delta"),
            )
        )
    return rows


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--ref", default=DEFAULT_REF, help="Git ref containing merged ledger fragments")
    parser.add_argument("--ledger-dir", default=DEFAULT_LEDGER_DIR, help="Ledger fragment directory")
    parser.add_argument(
        "--target-min", type=int, default=DEFAULT_TARGET_MIN, help="Committed floor for credited reduction"
    )
    parser.add_argument(
        "--target-max", type=int, default=DEFAULT_TARGET_MAX, help="Stretch target for credited reduction"
    )
    args = parser.parse_args(argv)

    if args.target_min > args.target_max:
        print("shrink-rollup: --target-min cannot exceed --target-max", file=sys.stderr)
        return 2

    try:
        rows = load_rows(args.ref, args.ledger_dir)
    except (subprocess.CalledProcessError, ValueError) as exc:
        print(f"shrink-rollup: {exc}", file=sys.stderr)
        return 2

    credited = sum(row.credited_reduction for row in rows)
    raw_delta = sum(row.raw_cloc_delta for row in rows)
    uncredited = sum(row.uncredited_relocation for row in rows)
    repair = sum(row.repair_only_delta for row in rows)
    generated = sum(row.generated_python_delta for row in rows)
    remaining_min = max(args.target_min - credited, 0)
    remaining_max = max(args.target_max - credited, 0)

    print(f"Ledger ref: {args.ref}")
    print(f"Ledger fragments: {len(rows)}")
    print(f"Cumulative merged credited reduction: {credited}")
    print(f"Target credited reduction band: {args.target_min}-{args.target_max}")
    print(f"Remaining to committed floor: {remaining_min}")
    print(f"Remaining to stretch target: {remaining_max}")
    print(f"Raw cloc delta sanity check: {raw_delta}")
    print(f"Uncredited relocation: {uncredited}")
    print(f"Repair-only delta: {repair}")
    print(f"Generated Python delta: {generated}")

    if rows:
        print("Fragments:")
        for row in rows:
            print(f"- {row.path}: credited={row.credited_reduction} surface={row.surface}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
