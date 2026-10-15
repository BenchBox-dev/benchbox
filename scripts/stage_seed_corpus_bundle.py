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
bundle_validation_status = importlib.import_module("validate_corpus").bundle_validation_status


def _write_output(eligible: bool) -> None:
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(f"eligible={str(eligible).lower()}\n")


def stage_seed_bundle(result_file: Path, destination: Path) -> bool:
    payload: Any = json.loads(result_file.read_text(encoding="utf-8"))
    validation_status = bundle_validation_status(payload)
    if validation_status == "uncertain":
        print(f"Skipping uncertain bundle: {result_file}")
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
