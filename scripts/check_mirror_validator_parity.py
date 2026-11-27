#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

CHECKOUT_ROOT = Path(__file__).resolve().parents[1]
BUNDLES_DIR = CHECKOUT_ROOT / "results-data" / "bundles"
SYNC_WORKFLOW = CHECKOUT_ROOT / ".github" / "workflows" / "sync-results-data-to-published.yml"
PROBE = CHECKOUT_ROOT / "scripts" / "corpus_outcome_probe.py"
MIRRORED_SUFFIXES = (".py", ".json", ".txt")
NOT_RUNTIME = ("results-data/bundles/", "results-data/corpus-inventory.json")
COMPARED_FIELDS = ("admitted", "class", "labels", "ranked_lanes", "scores", "trusted", "strict")
MAX_REPORTED = 10

CLI_DESCRIPTION = (
    "Run the validator as published-results holds it (a checkout of only the mirrored files, standard library "
    "only, python -I -S) and the validator on develop over every stored bundle, and fail when their outcomes "
    "(admitted, lane, class, label, score, submission findings) differ for any bundle."
)


def mirrored_runtime_paths(workflow: Path = SYNC_WORKFLOW) -> list[str]:
    paths = re.findall(r'^\s+-\s+"([^"]+)"\s*$', workflow.read_text(encoding="utf-8"), re.MULTILINE)
    return [
        path
        for path in paths
        if path.endswith(MIRRORED_SUFFIXES) and not path.startswith(NOT_RUNTIME) and "*" not in path
    ]


def build_slim_checkout(destination: Path, root: Path = CHECKOUT_ROOT, paths: list[str] | None = None) -> None:
    for relative in paths if paths is not None else mirrored_runtime_paths():
        source = root / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def _run_probe(python: str, root: Path, bundles_dir: Path, *, isolated: bool) -> dict[str, Any]:
    interpreter_flags = ["-I", "-S"] if isolated else []
    argv = [python] + interpreter_flags + [str(PROBE), str(root), str(bundles_dir)]
    completed = subprocess.run(argv, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            f"probe failed ({' '.join(argv[:-2])}): {completed.stderr.strip() or completed.stdout[:400]}"
        )
    return json.loads(completed.stdout)


def mirror_report(slim_root: Path, bundles_dir: Path, python: str) -> dict[str, Any]:
    report = _run_probe(python, slim_root, bundles_dir, isolated=True)
    if report["installed_third_party"] or report["benchbox_package_loaded"]:
        raise RuntimeError(
            "the mirror validator is not standard-library only: "
            f"third-party modules importable={report['installed_third_party']}, "
            f"benchbox package loaded={report['benchbox_package_loaded']}"
        )
    return report


def develop_report(bundles_dir: Path, python: str) -> dict[str, Any]:
    report = _run_probe(python, CHECKOUT_ROOT, bundles_dir, isolated=False)
    if not report["benchbox_package_loaded"]:
        raise RuntimeError("the develop validator did not load the benchbox package")
    return report


def differences(mirror: dict[str, Any], develop: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    for name in sorted(set(mirror) | set(develop)):
        if name not in mirror or name not in develop:
            problems.append(f"{name}: present on only one side")
            continue
        for field in COMPARED_FIELDS:
            if mirror[name][field] != develop[name][field]:
                problems.append(f"{name}: {field} mirror={mirror[name][field]!r} develop={develop[name][field]!r}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--bundles-dir", type=Path, default=BUNDLES_DIR)
    parser.add_argument("--python", default=sys.executable, help="Interpreter that runs the develop validator.")
    parser.add_argument(
        "--mirror-python",
        default=None,
        help="Interpreter that runs the mirror validator (default: --python); published-results runs Python 3.11.",
    )
    args = parser.parse_args(argv)
    bundles_dir = args.bundles_dir.resolve()

    with tempfile.TemporaryDirectory(prefix="slim-checkout-") as scratch:
        slim_root = Path(scratch)
        build_slim_checkout(slim_root)
        try:
            mirror = mirror_report(slim_root, bundles_dir, args.mirror_python or args.python)
            develop = develop_report(bundles_dir, args.python)
        except RuntimeError as exc:
            print(f"::error::{exc}", file=sys.stderr)
            return 1

    problems = differences(mirror["outcomes"], develop["outcomes"])
    count = len(develop["outcomes"])
    if count == 0:
        print(f"::error::No stored bundles were found under {bundles_dir}; nothing was compared.", file=sys.stderr)
        return 1
    if not problems:
        print(f"Mirror and develop validators agree on all {count} stored bundles.")
        return 0
    print(
        f"::error::The mirror validator and the develop validator disagree on {len(problems)} outcome(s) "
        f"across {count} stored bundles:",
        file=sys.stderr,
    )
    for problem in problems[:MAX_REPORTED]:
        print(f"  {problem}", file=sys.stderr)
    if len(problems) > MAX_REPORTED:
        print(f"  ... {len(problems) - MAX_REPORTED} more", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
