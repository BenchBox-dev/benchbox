from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

from benchbox.core.results.anonymization import AnonymizationManager, find_public_path_leaks
from benchbox.core.results.canonical_json import canonical_json_bytes
from benchbox.validation.bundle import ValidationResult, export_anonymized_errors, is_primary_bundle_file
from scripts.validate_submission import _append_corpus_anonymized_errors

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REPO_ROOT = Path(__file__).resolve().parents[3]
RESULTS_DATA = REPO_ROOT / "results-data"


def _corpus_json_files() -> list[Path]:
    return sorted(RESULTS_DATA.rglob("*.json"))


def test_corpus_directory_is_present() -> None:
    assert RESULTS_DATA.is_dir(), f"missing corpus directory: {RESULTS_DATA}"
    assert _corpus_json_files(), "corpus scan found no JSON files - the invariant would be vacuous"


def test_no_corpus_file_exposes_a_private_path() -> None:
    offenders: list[str] = []
    unreadable: list[str] = []

    for path in _corpus_json_files():
        rel = path.relative_to(REPO_ROOT)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            unreadable.append(f"{rel}: {type(exc).__name__}")
            continue
        leaks = find_public_path_leaks(payload)
        if leaks:
            offenders.append(f"{rel}: {', '.join(sorted(set(leaks))[:5])}")

    assert not unreadable, "corpus files could not be parsed (failing closed):\n" + "\n".join(unreadable)
    assert not offenders, f"{len(offenders)} corpus file(s) expose private paths:\n" + "\n".join(offenders[:20])


def test_corpus_only_change_routes_to_required_code_ci() -> None:
    from path_filter_decision import DEFAULT_RULES, classify_paths, load_rules

    rules = load_rules(REPO_ROOT / DEFAULT_RULES)
    for changed in (
        ["results-data/bundles/example.json"],
        ["results-data/bundles/example.manifest.json"],
        ["results-data/corpus-inventory.json"],
    ):
        decision = classify_paths(changed, rules)
        assert decision["needs_code_ci"] is True, f"{changed[0]} would skip the required code-test lane"


def test_code_test_is_gated_by_ci_required_result() -> None:
    import yaml

    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    needs = workflow["jobs"]["core"]["needs"]
    assert "code-test" in needs, "the core unit result no longer gates on code-test"


def _anonymize(payload: dict, path: Path, manager: AnonymizationManager) -> dict:
    if path.name.endswith(".tuning.json"):
        return manager.anonymize_tuning_payload(payload)
    return manager.anonymize_result_payload(payload)


def test_anonymizing_the_corpus_twice_matches_anonymizing_it_once() -> None:
    manager = AnonymizationManager()
    unstable: list[str] = []
    scanned = 0

    for path in _corpus_json_files():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            continue
        scanned += 1
        once = _anonymize(payload, path, manager)
        if _anonymize(once, path, manager) != once:
            unstable.append(str(path.relative_to(REPO_ROOT)))

    assert scanned, "no corpus mappings scanned - the fixed-point gate would be vacuous"
    assert not unstable, f"{len(unstable)} corpus file(s) change under a second anonymization pass:\n" + "\n".join(
        unstable[:20]
    )


def test_rederived_corpus_publishes_byte_identically_to_what_is_stored() -> None:
    manager = AnonymizationManager()
    drifted: list[str] = []
    scanned = 0

    for path in sorted((RESULTS_DATA / "bundles").rglob("*")):
        if not is_primary_bundle_file(path):
            continue
        stored = path.read_bytes()
        payload = json.loads(stored.decode("utf-8"))
        if not isinstance(payload, dict):
            continue
        scanned += 1
        if canonical_json_bytes(manager.anonymize_result_payload(payload)) != stored:
            drifted.append(str(path.relative_to(REPO_ROOT)))

    assert scanned, "no primary bundles scanned - this gate would be vacuous"
    assert not drifted, (
        f"{len(drifted)} bundle(s) are not stored at the publication fixed point; "
        "publishing would rewrite them:\n" + "\n".join(drifted[:20])
    )


def _manifest_hash_mismatches(bundles_dir: Path) -> tuple[list[str], int]:
    mismatched: list[str] = []
    scanned = 0

    for manifest_path in sorted(bundles_dir.rglob("*.manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        bundle_file = manifest.get("bundle_file")
        if not isinstance(bundle_file, str) or not bundle_file:
            continue
        bundle_path = manifest_path.parent / bundle_file
        if not bundle_path.is_file():
            mismatched.append(f"{manifest_path.name}: declares missing bundle {bundle_file}")
            continue
        scanned += 1
        actual = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
        recorded = manifest.get("bundle_hash")
        if actual != recorded:
            mismatched.append(f"{manifest_path.name}: manifest {str(recorded)[:16]}..., bundle {actual[:16]}...")

    return mismatched, scanned


def test_every_manifest_bundle_hash_matches_its_bundle() -> None:
    mismatched, scanned = _manifest_hash_mismatches(RESULTS_DATA / "bundles")

    if not scanned:
        no_sidecars = not any(
            isinstance(json.loads(path.read_text(encoding="utf-8")).get("bundle_file"), str)
            for path in (RESULTS_DATA / "bundles").rglob("*.manifest.json")
        )
        assert no_sidecars, "sidecar manifests exist but none were scanned - this gate went vacuous"
        pytest.skip("corpus holds no bundle-declaring sidecar manifests - nothing for this gate to check")
    assert not mismatched, (
        f"{len(mismatched)} sidecar manifest(s) do not match their bundle, so the mirror to "
        "published-results will refuse to open. Rerun "
        "`_project/scripts/results_explorer_corpus_migrate.py --write --manifest <new-ledger>`:\n"
        + "\n".join(mismatched[:20])
    )


def test_the_manifest_hash_gate_still_catches_a_stale_hash(tmp_path: Path) -> None:
    bundle = tmp_path / "result.json"
    bundle.write_text('{"benchmark": {}}\n', encoding="utf-8")
    fresh = tmp_path / "fresh.manifest.json"
    fresh.write_text(
        json.dumps({"bundle_file": "result.json", "bundle_hash": hashlib.sha256(bundle.read_bytes()).hexdigest()}),
        encoding="utf-8",
    )

    mismatched, scanned = _manifest_hash_mismatches(tmp_path)
    assert (mismatched, scanned) == ([], 1), "a correct manifest must not be flagged"

    bundle.write_text('{"benchmark": {"edited": true}}\n', encoding="utf-8")

    mismatched, scanned = _manifest_hash_mismatches(tmp_path)
    assert scanned == 1
    assert len(mismatched) == 1 and fresh.name in mismatched[0]


def test_the_manifest_hash_gate_flags_a_manifest_whose_bundle_is_gone(tmp_path: Path) -> None:
    (tmp_path / "orphan.manifest.json").write_text(
        json.dumps({"bundle_file": "missing.json", "bundle_hash": "0" * 64}), encoding="utf-8"
    )

    mismatched, scanned = _manifest_hash_mismatches(tmp_path)

    assert scanned == 0
    assert len(mismatched) == 1 and "missing.json" in mismatched[0]


def test_primary_rederivation_discovery_uses_canonical_case_insensitive_rules(tmp_path: Path) -> None:
    primary = tmp_path / "RESULT.JSON"
    primary.write_text("{}\n", encoding="utf-8")
    companions = [
        tmp_path / "RESULT.PLANS.JSON",
        tmp_path / "RESULT.TUNING.JSON",
        tmp_path / "RESULT.MANIFEST.JSON",
        tmp_path / "SUBMISSION-MANIFEST.JSON",
    ]
    for companion in companions:
        companion.write_text("{}\n", encoding="utf-8")

    selected = [path.name for path in sorted(tmp_path.rglob("*")) if is_primary_bundle_file(path)]

    assert selected == ["RESULT.JSON"]


def test_corpus_checks_out_with_lf_on_every_platform() -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "-z", "--", "results-data"],
        capture_output=True,
        check=True,
        cwd=REPO_ROOT,
    ).stdout
    paths = [name for name in tracked.split(b"\0") if name]
    assert paths, "no tracked corpus files - this gate would be vacuous"

    attrs = subprocess.run(
        ["git", "check-attr", "-z", "--stdin", "eol"],
        input=b"\0".join(paths),
        capture_output=True,
        check=True,
        cwd=REPO_ROOT,
    ).stdout

    fields = attrs.split(b"\0")
    untranslated = [fields[index].decode() for index in range(0, len(fields) - 2, 3) if fields[index + 2] != b"lf"]
    assert not untranslated, (
        f"{len(untranslated)} corpus file(s) have no explicit `eol=lf` attribute, so their "
        "working-tree bytes depend on the platform that checked them out:\n" + "\n".join(untranslated[:20])
    )


def test_detector_still_flags_a_planted_leak() -> None:
    planted = {"metadata": {"working_dir": "/Users/someone/benchbox"}}
    assert find_public_path_leaks(planted), "detector no longer flags a private home path"


def test_detector_still_flags_a_leak_encoded_as_an_object_key() -> None:
    planted = {"metadata": {"/Users/someone/benchbox": True}}
    leaks = find_public_path_leaks(planted)
    assert leaks, "detector no longer flags a private path encoded as an object key"
    assert "someone" not in " ".join(leaks), "key leaks must stay redacted in the report"


def test_anonymized_bundle_passes_the_export_flag() -> None:
    assert export_anonymized_errors({"export": {"anonymized": True}}) == []


def test_unanonymized_bundle_fails_the_export_flag() -> None:
    assert export_anonymized_errors({"export": {"anonymized": False}}), "raw bundle passed the flag"


def test_bundle_without_export_section_fails_the_flag() -> None:
    assert export_anonymized_errors({}), "bundle without an export section passed the flag"


def test_spoofed_anonymized_flag_fails_on_private_path_leak() -> None:
    payload = {
        "export": {"anonymized": True},
        "system": {"user": "/Users/someone/secret"},
    }
    assert export_anonymized_errors(payload), "spoofed flag with private path leak passed validation"


def test_spoofed_anonymized_flag_fails_on_raw_explain_in_plan() -> None:
    payload = {
        "export": {"anonymized": True},
        "plans": {
            "queries": {
                "q1": {
                    "plan": {
                        "raw_explain_output": "Seq Scan on table",
                    }
                }
            }
        },
    }
    assert export_anonymized_errors(payload), "spoofed flag with raw explain output passed validation"


def test_spoofed_anonymized_flag_fails_on_unanonymized_tuning_diagnostics() -> None:
    payload = {
        "export": {"anonymized": True},
        "platform": {
            "tuning": {
                "applied": {
                    "statements": [{"statement": "ALTER TABLE x", "action": "applied"}],
                }
            }
        },
    }
    assert export_anonymized_errors(payload), "spoofed flag with tuning diagnostics passed validation"


def test_mirror_path_flags_a_raw_corpus_bundle(tmp_path: Path) -> None:
    root = tmp_path / "results-data" / "bundles"
    root.mkdir(parents=True)
    raw = root / "seed_raw.json"
    raw.write_text(json.dumps({"export": {"anonymized": False}}), encoding="utf-8")
    results = [ValidationResult(str(raw))]
    _append_corpus_anonymized_errors([raw], results)
    assert results[0].errors, "mirror path passed a raw corpus bundle"


def test_mirror_path_flags_a_nested_raw_corpus_bundle(tmp_path: Path) -> None:
    root = tmp_path / "results-data" / "bundles" / "nested" / "deep"
    root.mkdir(parents=True)
    raw = root / "seed_nested_raw.json"
    raw.write_text(json.dumps({"export": {"anonymized": False}}), encoding="utf-8")
    results = [ValidationResult(str(raw))]
    _append_corpus_anonymized_errors([raw], results)
    assert results[0].errors, "mirror path passed a nested raw corpus bundle"


def test_mirror_path_passes_an_anonymized_corpus_bundle(tmp_path: Path) -> None:
    root = tmp_path / "results-data" / "bundles"
    root.mkdir(parents=True)
    clean = root / "seed_clean.json"
    clean.write_text(json.dumps({"export": {"anonymized": True}}), encoding="utf-8")
    results = [ValidationResult(str(clean))]
    _append_corpus_anonymized_errors([clean], results)
    assert results[0].errors == []


def test_mirror_path_passes_a_nested_anonymized_corpus_bundle(tmp_path: Path) -> None:
    root = tmp_path / "results-data" / "bundles" / "nested" / "deep"
    root.mkdir(parents=True)
    clean = root / "seed_nested_clean.json"
    clean.write_text(json.dumps({"export": {"anonymized": True}}), encoding="utf-8")
    results = [ValidationResult(str(clean))]
    _append_corpus_anonymized_errors([clean], results)
    assert results[0].errors == []


def _load_corpus_validator() -> ModuleType:
    import importlib.util

    path = REPO_ROOT / "results-data" / "validate_corpus.py"
    spec = importlib.util.spec_from_file_location("benchbox_corpus_validator", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_corpus_gate_rejects_a_raw_bundle(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    validator = _load_corpus_validator()
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "seed_raw.json").write_text(json.dumps({"export": {"anonymized": False}}), encoding="utf-8")
    assert validator.main(bundles_dir=bundles) == 1
    assert "not anonymized" in capsys.readouterr().out
