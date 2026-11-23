#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

CLI_DESCRIPTION = (
    "Print the outcome of every result bundle under a directory as the validator checked out at ROOT computes it. "
    "Runs under the standard library only: usage: corpus_outcome_probe.py ROOT BUNDLES_DIR"
)

THIRD_PARTY_MODULES = ("pydantic", "yaml")


def _load_validator(root: Path):
    path = root / "results-data" / "validate_corpus.py"
    spec = importlib.util.spec_from_file_location("_probe_validate_corpus", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _lane_score(validator, payload: dict, lane: str) -> float | None:
    rule = validator.CORPUS_RULE
    if lane == rule.LANE_COMBINED:
        return validator._combined_score(payload)
    if lane == rule.LANE_THROUGHPUT:
        return validator._throughput_score(payload)
    if lane in (rule.LANE_POWER_ONLY, rule.LANE_SUBSCALE):
        return validator._power_score(payload)
    return validator._primary_metric_value(payload, validator._display_timings(payload))


def _outcome(validator, bundle: Path) -> dict:
    payload = json.loads(bundle.read_text(encoding="utf-8"))
    outcome = validator.rule_outcome(payload, bundle)
    reason = validator.admission_reason(payload, bundle)
    return {
        "admitted": reason is None,
        "reason": reason,
        "class": outcome.class_name,
        "labels": list(outcome.labels),
        "lanes": list(outcome.lanes),
        "ranked_lanes": list(validator.bundle_lanes(payload, bundle)),
        "scores": {lane: _lane_score(validator, payload, lane) for lane in outcome.lanes},
    }


def _submission_findings(validator, bundles: list[Path]) -> dict:
    module = validator._load_bundle_validation()
    findings = {}
    for bundle in bundles:
        (result,) = module.validate_bundles([bundle], allow_partial_validation=True)
        findings[str(bundle)] = {"errors": list(result.errors), "warnings": list(result.warnings)}
    return findings


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(CLI_DESCRIPTION, file=sys.stderr)
        return 2
    root, bundles_dir = (Path(argument).resolve() for argument in argv)
    validator = _load_validator(root)
    bundles = validator.discover_bundles(bundles_dir)
    submission = _submission_findings(validator, bundles)
    outcomes = {}
    for bundle in bundles:
        key = bundle.relative_to(bundles_dir).as_posix()
        outcomes[key] = {**_outcome(validator, bundle), **submission[str(bundle)]}
    report = {
        "installed_third_party": [name for name in THIRD_PARTY_MODULES if importlib.util.find_spec(name) is not None],
        "benchbox_package_loaded": any(name == "benchbox" or name.startswith("benchbox.") for name in sys.modules),
        "outcomes": outcomes,
    }
    json.dump(report, sys.stdout, sort_keys=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
