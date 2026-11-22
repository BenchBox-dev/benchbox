#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BUNDLES_DIR = Path("results-data/bundles")
SNAPSHOT_PATH = Path("results-data/compliance-classes.json")
SNAPSHOT_SCHEMA_VERSION = 1
REGENERATE_COMMAND = "uv run -- python scripts/corpus_compliance_snapshot.py --write"

CHECKOUT_ROOT = Path(__file__).resolve().parents[1]
if str(CHECKOUT_ROOT) not in sys.path:
    sys.path.insert(0, str(CHECKOUT_ROOT))

from benchbox.core.results.subscale_policy import RANKING_EXCLUDED_COMPLIANCE_CLASSES, effective_compliance_class
from benchbox.validation.bundle import discover_bundles

CLI_DESCRIPTION = (
    "Record or check the effective compliance class of every committed result bundle, "
    "so a code change that reclassifies a stored bundle fails visibly."
)


Classification = dict[str, object]


def compute_classes(bundles_dir: Path) -> dict[str, Classification]:
    classes: dict[str, Classification] = {}
    for bundle_path in discover_bundles(bundles_dir):
        benchmark = json.loads(bundle_path.read_text(encoding="utf-8")).get("benchmark")
        benchmark = benchmark if isinstance(benchmark, dict) else {}
        compliance_class = effective_compliance_class(
            benchmark.get("id"), benchmark.get("scale_factor"), benchmark.get("compliance_class")
        )
        classes[bundle_path.relative_to(bundles_dir).as_posix()] = {
            "compliance_class": compliance_class,
            "ranking_excluded": compliance_class in RANKING_EXCLUDED_COMPLIANCE_CLASSES,
        }
    return dict(sorted(classes.items()))


def compare(recorded: dict[str, Classification], computed: dict[str, Classification]) -> list[str]:
    problems: list[str] = []
    changed = sorted(name for name in recorded.keys() & computed.keys() if recorded[name] != computed[name])
    if changed:
        problems.append(
            f"{len(changed)} committed bundle(s) changed compliance class. A code change reclassified stored "
            "results. If that is intended, regenerate the snapshot in the same pull request so the change is "
            f"reviewed: {REGENERATE_COMMAND}"
        )
        problems.extend(f"  {name}: {recorded[name]} -> {computed[name]}" for name in changed)
    missing = sorted(computed.keys() - recorded.keys())
    stale = sorted(recorded.keys() - computed.keys())
    if missing:
        problems.append(f"{len(missing)} bundle(s) are not in the snapshot: {missing}")
    if stale:
        problems.append(f"{len(stale)} snapshot entr(ies) have no bundle: {stale}")
    if missing or stale:
        problems.append(f"Bundles were added or removed. Regenerate the snapshot: {REGENERATE_COMMAND}")
    return problems


def load_snapshot(path: Path) -> dict[str, Classification]:
    return json.loads(path.read_text(encoding="utf-8"))["classes"]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Fail if any committed bundle's class differs.")
    mode.add_argument("--write", action="store_true", help="Write the regenerated snapshot to disk.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not BUNDLES_DIR.is_dir():
        print(f"Error: {BUNDLES_DIR} not found", file=sys.stderr)
        return 1
    computed = compute_classes(BUNDLES_DIR)

    if args.write:
        SNAPSHOT_PATH.write_text(
            json.dumps({"schema_version": SNAPSHOT_SCHEMA_VERSION, "classes": computed}, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Wrote {SNAPSHOT_PATH} with {len(computed)} bundles")
        return 0

    if not SNAPSHOT_PATH.exists():
        print(f"FAIL: {SNAPSHOT_PATH} does not exist. Run: {REGENERATE_COMMAND}", file=sys.stderr)
        return 1
    problems = compare(load_snapshot(SNAPSHOT_PATH), computed)
    if problems:
        print("FAIL: " + "\n".join(problems), file=sys.stderr)
        return 1
    print(f"OK: compliance classes match the snapshot ({len(computed)} bundles)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
