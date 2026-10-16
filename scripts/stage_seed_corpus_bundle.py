from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from benchbox.core.results.anonymization import (
    AnonymizationConfig,
    AnonymizationManager,
    MissingPublicPseudonymSaltError,
    require_public_pseudonym_salt,
)
from benchbox.core.results.canonical_json import canonical_json_text
from benchbox.validation.bundle import (
    export_anonymized_errors,
    unanonymized_plans_findings,
    unanonymized_tuning_findings,
)

RESULTS_DATA = Path(__file__).resolve().parents[1] / "results-data"
sys.path.insert(0, str(RESULTS_DATA))
exclusion_reason = importlib.import_module("validate_corpus").exclusion_reason

PLANS_SUFFIX = ".plans.json"
TUNING_SUFFIX = ".tuning.json"


class StagingError(Exception):
    pass


def _write_output(eligible: bool) -> None:
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(f"eligible={str(eligible).lower()}\n")


def _write_status(reason: str | None) -> None:
    status_file = os.environ.get("SEED_STATUS_FILE")
    if status_file:
        path = Path(status_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{os.environ['SEED_CELL']}\t{reason or 'eligible'}\n", encoding="utf-8")


def _anonymization_manager() -> AnonymizationManager:
    try:
        salt = require_public_pseudonym_salt()
    except MissingPublicPseudonymSaltError as exc:
        raise StagingError(str(exc)) from exc
    return AnonymizationManager(AnonymizationConfig(machine_id_salt=salt))


def anonymize_bundle(payload: dict[str, Any], manager: AnonymizationManager) -> dict[str, Any]:
    working = dict(payload)
    export = working.pop("export", None)
    anonymized = manager.anonymize_result_payload(working)
    anonymized["export"] = {**(export if isinstance(export, dict) else {}), "anonymized": True}
    errors = export_anonymized_errors(anonymized)
    if errors:
        raise StagingError("; ".join(errors))
    return anonymized


def _anonymized_companion(path: Path, manager: AnonymizationManager) -> dict[str, Any]:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise StagingError(f"{path.name} is not a JSON object")
    if path.name.endswith(TUNING_SUFFIX):
        payload = manager.anonymize_tuning_payload(payload)
    findings = unanonymized_plans_findings(payload) + unanonymized_tuning_findings(payload)
    if findings:
        raise StagingError(f"{path.name}: " + "; ".join(findings))
    return payload


def stage_seed_bundle(result_file: Path, destination: Path) -> bool:
    payload: Any = json.loads(result_file.read_text(encoding="utf-8"))
    reason = exclusion_reason(payload)
    _write_status(reason)
    if reason is not None:
        print(f"Skipping ineligible bundle ({reason}): {result_file}")
        _write_output(False)
        return False

    manager = _anonymization_manager()
    staged = {result_file.name: anonymize_bundle(payload, manager)}
    stem = result_file.with_suffix("")
    for suffix in (PLANS_SUFFIX, TUNING_SUFFIX):
        companion = Path(f"{stem}{suffix}")
        if companion.is_file():
            staged[companion.name] = _anonymized_companion(companion, manager)

    destination.mkdir(parents=True, exist_ok=True)
    for name, content in staged.items():
        (destination / name).write_text(canonical_json_text(content), encoding="utf-8")
    _write_output(True)
    print(f"Staged anonymized bundle to: {destination / result_file.name}")
    return True


def main(arguments: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if arguments is None else arguments
    if len(arguments) != 2:
        raise SystemExit("usage: stage_seed_corpus_bundle.py RESULT_FILE DESTINATION")
    try:
        stage_seed_bundle(Path(arguments[0]), Path(arguments[1]))
    except StagingError as exc:
        print(f"ERROR cannot stage {arguments[0]}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
