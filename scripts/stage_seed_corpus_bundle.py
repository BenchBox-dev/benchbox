from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

RESULTS_DATA = Path(__file__).resolve().parents[1] / "results-data"
sys.path.insert(0, str(RESULTS_DATA))
exclusion_reason = importlib.import_module("validate_corpus").exclusion_reason


def _write_output(eligible: bool) -> None:
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(f"eligible={str(eligible).lower()}\n")


def _write_status(reason: str | None) -> None:
    status_file = os.environ.get("SEED_STATUS_FILE")
    if status_file:
        path = Path(status_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{os.environ['SEED_CELL']}\t{reason or 'eligible'}\n", encoding="utf-8")


def stage_seed_bundle(result_file: Path, destination: Path) -> bool:
    payload: Any = json.loads(result_file.read_text(encoding="utf-8"))
    reason = exclusion_reason(payload)
    _write_status(reason)
    if reason is not None:
        print(f"Skipping ineligible bundle ({reason}): {result_file}")
        _write_output(False)
        return False

    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(result_file, destination / result_file.name)
    stem = result_file.with_suffix("")
    for suffix in (".plans.json", ".tuning.json"):
        companion = Path(f"{stem}{suffix}")
        if companion.is_file():
            shutil.copy2(companion, destination / companion.name)
    _write_output(True)
    print(f"Staged to: {destination / result_file.name}")
    return True


def main(arguments: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if arguments is None else arguments
    if len(arguments) != 2:
        raise SystemExit("usage: stage_seed_corpus_bundle.py RESULT_FILE DESTINATION")
    stage_seed_bundle(Path(arguments[0]), Path(arguments[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
