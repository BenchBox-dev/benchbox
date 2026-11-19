from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from scripts import sqlglot_delivery as delivery
from scripts.sql_execution_gate import translation_report

CLI_DESCRIPTION = (
    "Pull request guard for release/X.Y.x maintenance lines.\n"
    "\n"
    "A maintenance line keeps the SQLGlot wheel of its release tag. A pull request that changes\n"
    "benchbox/_vendor/sqlglot.json must be a pure adoption (the file set the delivery automation\n"
    "commits) and its body must carry the translated-query report recomputed from the two wheels.\n"
)

REPO_ROOT = Path(__file__).resolve().parents[1]
MAINTENANCE_LINE = re.compile(r"release/[0-9]+\.[0-9]+\.x")


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, encoding="utf-8", errors="replace"
    ).stdout


def outcomes(root: Path, rev: str) -> dict[str, object]:
    baseline = json.loads(_git(root, "show", f"{rev}:{delivery.BASELINE}"))
    return {key: value for key, value in baseline.items() if key != "identity"}


def problems(
    root: Path,
    base_ref: str,
    base_sha: str,
    body: str,
    reporter: delivery.TranslationReporter = translation_report.translation_changes,
) -> list[str]:
    if MAINTENANCE_LINE.fullmatch(base_ref) is None:
        return []
    changed = [
        name for name in _git(root, "diff", "--name-only", "--no-renames", "-z", base_sha, "HEAD").split("\0") if name
    ]
    if delivery.PIN not in changed:
        return []
    version = json.loads(_git(root, "show", f"HEAD:{delivery.PIN}"))["version"]
    try:
        delivery.check_adoption_paths(changed, version)
    except delivery.DeliveryError as error:
        return [
            f"a pull request into {base_ref} changes {delivery.PIN} together with other changes ({error}); "
            "a maintenance line takes a newer SQLGlot wheel only through a separate adoption pull request"
        ]
    if outcomes(root, base_sha) != outcomes(root, "HEAD"):
        return [
            f"the adoption into {base_ref} changes expected outcomes in {delivery.BASELINE}, not only its identity; "
            "adopt this version by hand"
        ]
    try:
        listing = delivery.render_translation_changes(reporter(root, base_sha, "HEAD"))
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        return [f"the translation report could not be computed: {error}"]
    if listing.replace("\r\n", "\n") not in body.replace("\r\n", "\n"):
        return [
            f"the pull request body does not carry the translated-query report for the {version} wheel; "
            "generate it with `python -m scripts.sql_execution_gate.translation_report --render` and paste it into the body"
        ]
    return []


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--body-env", required=True, help="Environment variable holding the pull request body")
    arguments = parser.parse_args(argv)
    found = problems(arguments.root, arguments.base_ref, arguments.base_sha, os.environ.get(arguments.body_env, ""))
    if found:
        print("maintenance-line SQLGlot guard FAILED:", file=sys.stderr)
        for problem in found:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("maintenance-line SQLGlot guard OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
