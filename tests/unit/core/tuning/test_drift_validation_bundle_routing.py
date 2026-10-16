# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

from benchbox.core.results.anonymization import anonymize_applied_tuning_payload
from benchbox.core.tuning.applied_ledger import AppliedTuningLedger
from benchbox.core.tuning.metadata import MetadataValidationResult
from benchbox.platforms.duckdb import DuckDBAdapter

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _drifted_result() -> MetadataValidationResult:
    result = MetadataValidationResult()
    result.add_error("Failed to validate database tunings: /abs/path/db.duckdb locked")
    result.add_warning("Database contains tuning metadata but no tunings expected")
    result.configuration_mismatches["constraints"] = "hash a != b"
    result.drifted_sections.add("constraints")
    result.missing_tables.add("lineitem")
    return result


class TestMetadataValidationResultPayload:
    def test_clean_result_is_minimal(self):
        assert MetadataValidationResult().to_payload() == {"is_valid": True}

    def test_drift_serializes_deterministically(self):
        payload = _drifted_result().to_payload()
        assert payload["is_valid"] is False
        assert payload["drifted_sections"] == ["constraints"]
        assert payload["missing_tables"] == ["lineitem"]
        assert payload["configuration_mismatches"] == {"constraints": "hash a != b"}
        assert payload["errors"] and payload["warnings"]


class TestCompanionCarriesDriftCheck:
    def test_to_payload_includes_drift_check(self):
        ledger = AppliedTuningLedger()
        payload = ledger.to_payload(status="not_applicable", drift_check=_drifted_result().to_payload())
        assert payload["drift_check"]["is_valid"] is False

    def test_empty_ledger_with_drift_is_not_pruned(self):
        from benchbox.core.results.schema import build_applied_ledger_payload

        ledger = AppliedTuningLedger()

        class _Result:
            applied_tuning_ledger = ledger.to_payload(
                status="not_applicable", drift_check=_drifted_result().to_payload()
            )

        payload = build_applied_ledger_payload(_Result())
        assert payload is not None
        assert payload["drift_check"]["drifted_sections"] == ["constraints"]

    def test_empty_ledger_without_drift_is_pruned(self):
        from benchbox.core.results.schema import build_applied_ledger_payload

        class _Result:
            applied_tuning_ledger = AppliedTuningLedger().to_payload(status="noop")

        assert build_applied_ledger_payload(_Result()) is None


class TestAdapterDriftGating:
    def _adapter(self, *, tuning_enabled: bool, reused: bool, result) -> DuckDBAdapter:
        adapter = DuckDBAdapter()
        adapter.tuning_enabled = tuning_enabled
        adapter.database_was_reused = reused
        adapter._drift_validation_result = result
        return adapter

    def test_reused_tuned_run_builds_drift_check(self):
        adapter = self._adapter(tuning_enabled=True, reused=True, result=_drifted_result())
        payload = adapter._build_drift_check_payload()
        assert payload is not None and payload["is_valid"] is False

    def test_fresh_db_has_no_drift_check(self):
        adapter = self._adapter(tuning_enabled=True, reused=False, result=_drifted_result())
        assert adapter._build_drift_check_payload() is None

    def test_untuned_run_has_no_drift_check(self):
        adapter = self._adapter(tuning_enabled=False, reused=True, result=_drifted_result())
        assert adapter._build_drift_check_payload() is None

    def test_no_captured_result_returns_none(self):
        adapter = self._adapter(tuning_enabled=True, reused=True, result=None)
        assert adapter._build_drift_check_payload() is None


class TestDriftCheckAnonymization:
    def test_free_text_dropped_structural_kept(self):
        payload = {
            "status": "not_applicable",
            "applied_ledger_hash": None,
            "statements": [],
            "dropped": [],
            "drift_check": _drifted_result().to_payload(),
        }
        sanitized = anonymize_applied_tuning_payload(payload)
        drift = sanitized["drift_check"]
        assert drift["is_valid"] is False
        assert drift["drifted_sections"] == ["constraints"]
        assert drift["drift_redacted"] is True
        for dropped in ("errors", "warnings", "configuration_mismatches", "missing_tables", "extra_tables"):
            assert dropped not in drift
        assert "errors" in payload["drift_check"]
