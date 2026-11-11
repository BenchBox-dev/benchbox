from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from benchbox.core.results.loader import load_result_file
from benchbox.core.results.submit_classification import (
    SubmitTerminalState,
    classify_loaded_result,
    classify_result_path,
)
from tests.uat import runner

sub = importlib.import_module("benchbox.cli.commands.submit")

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _write_result_json(
    path: Path,
    *,
    failed: int = 0,
    validation: str = "passed",
    compliance_class: str | None = None,
    translation_status: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    passed = 1 if failed == 0 else 0
    payload: dict = {
        "version": "2.1",
        "run": {"id": "contract", "timestamp": "2026-01-01T00:00:00", "total_duration_ms": 1},
        "benchmark": {"id": "tpch", "name": "TPC-H", "scale_factor": 0.01, "test_type": "power"},
        "platform": {"name": "DuckDB"},
        "summary": {
            "queries": {"total": 1, "passed": passed, "failed": failed},
            "validation": {"status": validation},
        },
        "queries": [{"id": "Q1", "status": "SUCCESS" if failed == 0 else "ERROR", "ms": 1}],
        "phases": {},
    }
    if compliance_class is not None:
        payload["benchmark"]["compliance_class"] = compliance_class
    if translation_status is not None:
        payload["execution"] = {"translation": {"status": translation_status}}
    path.write_text(json.dumps(payload), encoding="utf-8")


def _loadable_cases(tmp_path: Path) -> list[tuple[str, Path, SubmitTerminalState]]:
    clean = tmp_path / "clean.json"
    _write_result_json(clean)

    query_failure = tmp_path / "query-failure.json"
    _write_result_json(query_failure, failed=1)

    schema_violation = tmp_path / "schema-violation.json"
    _write_result_json(schema_violation, failed=0, validation="failed")

    unvalidated_not_validated = tmp_path / "unvalidated-not-validated.json"
    _write_result_json(unvalidated_not_validated, failed=0, validation="not_validated")

    unvalidated_not_run = tmp_path / "unvalidated-not-run.json"
    _write_result_json(unvalidated_not_run, failed=0, validation="not_run")

    unvalidated_uncertain = tmp_path / "unvalidated-uncertain.json"
    _write_result_json(unvalidated_uncertain, failed=0, validation="uncertain")

    unvalidated_unknown = tmp_path / "unvalidated-unknown.json"
    _write_result_json(unvalidated_unknown, failed=0, validation="unknown")

    translation_fallback = tmp_path / "translation-fallback.json"
    _write_result_json(translation_fallback, failed=0, validation="passed", translation_status="fallback")

    uncertain_with_translation_fallback = tmp_path / "uncertain-with-translation-fallback.json"
    _write_result_json(
        uncertain_with_translation_fallback,
        failed=0,
        validation="uncertain",
        translation_status="fallback",
    )

    unofficial = tmp_path / "unofficial.json"
    _write_result_json(unofficial, compliance_class="unofficial_subscale")

    unofficial_and_unvalidated = tmp_path / "unofficial-and-unvalidated.json"
    _write_result_json(
        unofficial_and_unvalidated,
        failed=0,
        validation="not_validated",
        compliance_class="unofficial_subscale",
    )

    return [
        ("clean", clean, SubmitTerminalState.submittable),
        ("query_failure", query_failure, SubmitTerminalState.query_failure),
        ("schema_violation", schema_violation, SubmitTerminalState.schema_violation),
        ("translation_fallback", translation_fallback, SubmitTerminalState.schema_violation),
        ("unvalidated_not_validated", unvalidated_not_validated, SubmitTerminalState.unvalidated),
        ("unvalidated_not_run", unvalidated_not_run, SubmitTerminalState.unvalidated),
        ("unvalidated_uncertain", unvalidated_uncertain, SubmitTerminalState.unvalidated),
        ("unvalidated_unknown", unvalidated_unknown, SubmitTerminalState.unvalidated),
        (
            "uncertain_with_translation_fallback",
            uncertain_with_translation_fallback,
            SubmitTerminalState.unvalidated,
        ),
        ("unofficial", unofficial, SubmitTerminalState.unofficial),
        ("unofficial_and_unvalidated", unofficial_and_unvalidated, SubmitTerminalState.unofficial),
    ]


def test_submit_classifier_contract_loaded_and_path_agree(tmp_path: Path):
    for label, path, expected in _loadable_cases(tmp_path):
        assert classify_result_path(path) is expected, label

        assert runner.classify_for_submit(path) is runner.SubmitTerminalState.__members__[expected.name], label

        loaded, _raw = load_result_file(path)
        assert classify_loaded_result(loaded) is expected, label


def test_submit_classifier_contract_path_only_states(tmp_path: Path):
    missing = tmp_path / "does-not-exist.json"
    assert classify_result_path(missing) is SubmitTerminalState.missing_manifest
    assert classify_result_path(None) is SubmitTerminalState.missing_manifest
    assert runner.classify_for_submit(missing) is runner.SubmitTerminalState.missing_manifest

    malformed = tmp_path / "malformed.json"
    malformed.write_text("{not valid json", encoding="utf-8")

    assert classify_result_path(malformed) is SubmitTerminalState.bundle_load_error
    assert runner.classify_for_submit(malformed) is runner.SubmitTerminalState.bundle_load_error


def test_submit_classifier_contract_load_error_distinct_from_integrity(tmp_path: Path):
    integrity = tmp_path / "integrity.json"
    _write_result_json(integrity, failed=0, validation="failed")
    assert classify_result_path(integrity) is SubmitTerminalState.schema_violation

    unreadable = tmp_path / "unreadable.json"
    unreadable.write_bytes(b"\xff\xfe not json")
    assert classify_result_path(unreadable) is SubmitTerminalState.bundle_load_error


def test_submit_classifier_contract_cli_refusal_tracks_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):

    monkeypatch.setenv("BENCHBOX_MACHINE_ID_SALT", "unit-test-community-publish-salt")
    out_dir = tmp_path / "out"
    for label, path, expected in _loadable_cases(tmp_path):
        loaded, _raw = load_result_file(path)
        monkeypatch.setattr(sub, "load_result_file", lambda *_a, _r=loaded, **_k: (_r, {}))
        result = CliRunner().invoke(sub.submit, [str(path), "--dry-run", "--output", str(out_dir)])

        if expected is SubmitTerminalState.submittable:
            assert "Submission refused" not in result.output, f"{label}: {result.output}"
        else:
            assert result.exit_code == 1, f"{label}: {result.output}"
            assert "Submission refused" in result.output, label
            if expected is SubmitTerminalState.unofficial:
                assert "compliance_class" in result.output, label
            elif expected is SubmitTerminalState.unvalidated:
                assert "unvalidated" in result.output, label
                assert "validation_status=" in result.output, label
                assert "schema violation" not in result.output.lower(), label
                if "uncertain" in label:
                    assert "uncertain correctness claim" in result.output, label
                    assert "never executed" not in result.output, label
                elif "not_validated" in label or "not_run" in label or "unknown" in label:
                    assert "never executed" in result.output or "was skipped" in result.output, label
            else:
                assert "not a clean pass" in result.output, label


