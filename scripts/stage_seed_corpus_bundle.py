from __future__ import annotations

import hashlib
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
    find_public_path_leaks,
    unanonymized_plans_findings,
    unanonymized_tuning_findings,
)

RESULTS_DATA = Path(__file__).resolve().parents[1] / "results-data"
sys.path.insert(0, str(RESULTS_DATA))
exclusion_reason = importlib.import_module("validate_corpus").exclusion_reason

PLANS_SUFFIX = ".plans.json"
TUNING_SUFFIX = ".tuning.json"
MANIFEST_SUFFIX = ".manifest.json"
MANIFEST_VERSION = "1.0"


class StagingError(Exception):
    pass


def _write_output(eligible: bool) -> None:
    output_file = os.environ.get("GITHUB_OUTPUT")
    if output_file:
        with Path(output_file).open("a", encoding="utf-8") as output:
            output.write(f"eligible={str(eligible).lower()}\n")


def _write_status(reason: str | None) -> None:
    status_file = os.environ.get("SEED_STATUS_FILE")
    if status_file:
        path = Path(status_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        cell = os.environ.get("SEED_CELL", "unknown cell")
        path.write_text(f"{cell}\t{reason or 'eligible'}\n", encoding="utf-8")


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
    else:
        payload = manager.anonymize_result_payload(payload)
    findings = [f"private path leak at {leak}" for leak in sorted(set(find_public_path_leaks(payload)))]
    findings += unanonymized_plans_findings(payload) + unanonymized_tuning_findings(payload)
    if findings:
        raise StagingError(f"{path.name}: " + "; ".join(findings))
    return payload


def stage_seed_bundle(result_file: Path, destination: Path, *, internal_manifest: bool = False) -> bool:
    payload: Any = json.loads(result_file.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise StagingError(f"{result_file.name} is not a JSON object")
    reason = exclusion_reason(payload)
    if reason is not None:
        _write_status(reason)
        _write_output(False)
        print(f"Skipping ineligible bundle ({reason}): {result_file}")
        return False

    manager = _anonymization_manager()
    staged = {result_file.name: anonymize_bundle(payload, manager)}
    stem = result_file.with_suffix("")
    for suffix in (PLANS_SUFFIX, TUNING_SUFFIX):
        companion = Path(f"{stem}{suffix}")
        if companion.is_file():
            staged[companion.name] = _anonymized_companion(companion, manager)

    destination.mkdir(parents=True, exist_ok=True)
    written = {}
    for name, content in staged.items():
        text = canonical_json_text(content)
        (destination / name).write_text(text, encoding="utf-8")
        written[name] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if internal_manifest:
        manifest = {
            "bundle_file": result_file.name,
            "bundle_hash": written.pop(result_file.name),
            "companion_hashes": written,
            "funding": "unspecified",
            "manifest_version": MANIFEST_VERSION,
            "result_source": "internal",
            "submitted_by": "maintainer",
        }
        manifest_path = destination / f"{result_file.stem}{MANIFEST_SUFFIX}"
        manifest_path.write_text(canonical_json_text(manifest), encoding="utf-8")
    _write_status(None)
    _write_output(True)
    print(f"Staged anonymized bundle to: {destination / result_file.name}")
    return True


def main(arguments: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if arguments is None else arguments
    internal_manifest = "--internal-manifest" in arguments
    positional = [argument for argument in arguments if argument != "--internal-manifest"]
    if len(positional) != 2:
        raise SystemExit("usage: stage_seed_corpus_bundle.py [--internal-manifest] RESULT_FILE DESTINATION")
    try:
        stage_seed_bundle(Path(positional[0]), Path(positional[1]), internal_manifest=internal_manifest)
    except (StagingError, OSError, ValueError) as exc:
        _write_status(f"staging_failed: {exc}".replace("\t", " ").replace("\n", " "))
        _write_output(False)
        print(f"ERROR cannot stage {positional[0]}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
