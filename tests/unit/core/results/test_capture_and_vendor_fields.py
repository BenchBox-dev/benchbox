from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from benchbox.core.results.bundle_json_schema import DEFAULT_OUTPUT
from benchbox.core.results.comparison_modes import (
    CONFIG_CAPTURE_CURRENT,
    CONFIG_CAPTURE_MCP_BEFORE_PARITY,
    CONFIG_CAPTURE_VERSION,
    bundle_config_capture,
)
from benchbox.core.results.loader import reconstruct_benchmark_results
from benchbox.core.results.schema import SchemaV2Validator, build_result_payload
from benchbox.core.results.schema_policy import CURRENT_SCHEMA_VERSION, supports_capture_and_vendor_fields
from benchbox.validation.bundle import ValidationResult, _validate_capture_and_vendor_fields
from tests.fixtures.result_dict_fixtures import make_benchmark_results

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _payload(**result_fields: object) -> dict:
    result = make_benchmark_results(platform="snowflake")
    for name, value in result_fields.items():
        setattr(result, name, value)
    result.execution_context = {"entry_point": "mcp"}
    return build_result_payload(result)


def _errors(data: dict) -> list[str]:
    vr = ValidationResult("bundle.json")
    _validate_capture_and_vendor_fields(data, data["result_schema_version"], vr)
    return list(vr.errors)


def _schema_validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(Path(DEFAULT_OUTPUT).read_text(encoding="utf-8")))


class TestConfigCaptureMarker:
    def test_current_schema_is_the_version_that_introduced_the_fields(self) -> None:
        assert CURRENT_SCHEMA_VERSION == "2.5"
        assert supports_capture_and_vendor_fields("2.5")
        assert not supports_capture_and_vendor_fields("2.4")

    def test_exported_bundles_record_the_capture_version_for_every_entry_point(self) -> None:
        assert _payload()["execution"]["config_capture"] == CONFIG_CAPTURE_VERSION

    def test_current_mcp_bundle_without_compression_or_options_is_current(self) -> None:
        payload = _payload()
        assert "compression" not in payload.get("config", {})
        assert "platform_options" not in payload.get("config", {})

        assert bundle_config_capture(payload) == CONFIG_CAPTURE_CURRENT

    def test_mcp_bundle_without_marker_or_config_is_pre_parity(self) -> None:
        payload = _payload()
        del payload["execution"]["config_capture"]

        assert bundle_config_capture(payload) == CONFIG_CAPTURE_MCP_BEFORE_PARITY

    @pytest.mark.parametrize("marker", [0, True, "1", None, 1.0])
    def test_unreadable_marker_does_not_count_as_current(self, marker: object) -> None:
        payload = _payload()
        payload["execution"]["config_capture"] = marker

        assert bundle_config_capture(payload) == CONFIG_CAPTURE_MCP_BEFORE_PARITY

    def test_published_schema_accepts_the_marker_and_validator_accepts_the_payload(self) -> None:
        payload = _payload()
        SchemaV2Validator().validate(payload)
        assert not list(_schema_validator().iter_errors(payload))

    def test_marker_is_refused_on_an_older_schema_version(self) -> None:
        payload = _payload()
        payload["result_schema_version"] = payload["version"] = "2.4"

        assert any("requires schema version 2.5" in error for error in _errors(payload))
        assert list(_schema_validator().iter_errors(payload))

    @pytest.mark.parametrize("marker", [0, -1, True, "1", 1.5])
    def test_bad_marker_is_refused(self, marker: object) -> None:
        payload = _payload()
        payload["execution"]["config_capture"] = marker

        assert any("config_capture must be a positive integer" in error for error in _errors(payload))
        assert list(_schema_validator().iter_errors(payload))

    def test_bundle_from_before_the_marker_still_loads(self) -> None:
        payload = _payload()
        del payload["execution"]["config_capture"]
        payload["result_schema_version"] = payload["version"] = "2.4"

        assert _errors(payload) == []
        assert not list(_schema_validator().iter_errors(payload))
        assert reconstruct_benchmark_results(payload) is not None


class TestVendorFieldsInBundle:
    def test_exporter_records_plan_tier_preview_features_and_vendor_fields(self) -> None:
        payload = _payload(
            plan_tier="paid",
            preview_features=["x"],
            vendor_permission="email 2026-11-01",
            vendor_notice={"sent_on": "2026-11-01", "reference": "ticket-1"},
            self_run=True,
        )

        assert payload["provenance"] == {
            "plan_tier": "paid",
            "preview_features": ["x"],
            "vendor_permission": "email 2026-11-01",
            "vendor_notice": {"sent_on": "2026-11-01", "reference": "ticket-1"},
            "self_run": True,
        }
        assert not list(_schema_validator().iter_errors(payload))

    def test_bundle_without_vendor_fields_has_no_provenance_block(self) -> None:
        assert "provenance" not in _payload()

    def test_recorded_fields_round_trip_through_the_loader(self) -> None:
        payload = _payload(plan_tier="trial", preview_features=["x"], vendor_permission="p")

        loaded = reconstruct_benchmark_results(payload)

        assert loaded.plan_tier == "trial"
        assert loaded.preview_features == ["x"]
        assert loaded.vendor_permission == "p"

    @pytest.mark.parametrize("tier", ["free", "trial"])
    def test_free_or_trial_bundle_is_refused_without_permission(self, tier: str) -> None:
        errors = _errors(_payload(plan_tier=tier))

        assert any("written permission" in e and tier in e for e in errors)

    def test_free_tier_with_permission_is_accepted(self) -> None:
        assert _errors(_payload(plan_tier="free", vendor_permission="email 2026-11-01")) == []

    def test_preview_feature_is_refused_without_permission(self) -> None:
        errors = _errors(_payload(plan_tier="paid", preview_features=["new thing"]))

        assert any("preview features (new thing)" in e for e in errors)

    def test_databricks_preview_is_refused_even_with_permission(self) -> None:
        payload = _payload(plan_tier="paid", preview_features=["x"], vendor_permission="email")
        payload["platform"]["name"] = "databricks"

        assert any("does not lift the exclusion" in e for e in _errors(payload))

    def test_paid_tier_is_accepted(self) -> None:
        assert _errors(_payload(plan_tier="paid")) == []

    def test_unknown_tier_is_refused(self) -> None:
        payload = _payload(plan_tier="paid")
        payload["provenance"]["plan_tier"] = "enterprise"

        assert any("plan_tier" in e for e in _errors(payload))
        assert list(_schema_validator().iter_errors(payload))

    def test_vendor_fields_are_refused_on_an_older_schema_version(self) -> None:
        payload = _payload(plan_tier="paid")
        payload["result_schema_version"] = payload["version"] = "2.4"

        assert any("provenance.plan_tier requires schema version 2.5" in e for e in _errors(payload))
        assert list(_schema_validator().iter_errors(payload))

    def test_bigquery_self_run_false_is_refused(self) -> None:
        payload = _payload(self_run=False)
        payload["platform"]["name"] = "bigquery"

        assert any("self_run is false" in e for e in _errors(payload))

    def test_conditions_are_left_to_the_manifest_when_one_accompanies_the_bundle(self) -> None:
        payload = _payload(plan_tier="free", preview_features=["new thing"])
        vr = ValidationResult("bundle.json")

        _validate_capture_and_vendor_fields(payload, payload["result_schema_version"], vr, manifest_present=True)

        assert vr.errors == []

    def test_malformed_vendor_fields_are_still_refused_when_a_manifest_accompanies_the_bundle(self) -> None:
        payload = _payload(plan_tier="paid")
        payload["provenance"]["plan_tier"] = "enterprise"
        payload["provenance"]["preview_features"] = "not-a-list"
        vr = ValidationResult("bundle.json")

        _validate_capture_and_vendor_fields(payload, payload["result_schema_version"], vr, manifest_present=True)

        assert any("plan_tier" in e for e in vr.errors)
        assert any("preview_features" in e for e in vr.errors)

    def test_platform_rules_are_left_to_the_manifest_when_one_accompanies_the_bundle(self) -> None:
        payload = _payload(plan_tier="paid", preview_features=["x"])
        payload["platform"]["name"] = "databricks"
        vr = ValidationResult("bundle.json")

        _validate_capture_and_vendor_fields(payload, payload["result_schema_version"], vr, manifest_present=True)

        assert vr.errors == []

    def test_the_validator_does_not_change_its_input(self) -> None:
        payload = _payload(plan_tier="free")
        before = copy.deepcopy(payload)

        _errors(payload)

        assert payload == before


class TestDeclarationMerge:
    def test_a_bundle_recording_self_run_false_is_not_overridden_by_a_manifest_attesting_true(self) -> None:
        from benchbox.validation.bundle import evaluate_vendor_conditions, merge_bundle_vendor_declarations

        declared = {"platform": "bigquery", "self_run": True}
        merge_bundle_vendor_declarations(declared, {"self_run": False})

        errors, _ = evaluate_vendor_conditions(declared)

        assert declared["self_run"] is False
        assert any("self_run is false" in e for e in errors)

    def test_a_bundle_recording_self_run_true_does_not_override_a_manifest_saying_false(self) -> None:
        from benchbox.validation.bundle import merge_bundle_vendor_declarations

        declared = {"platform": "bigquery", "self_run": False}
        merge_bundle_vendor_declarations(declared, {"self_run": True})

        assert declared["self_run"] is False


class TestSubmissionFlow:
    @staticmethod
    def _recorded_flags(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, manifest: bool, declared: bool) -> bool:
        from benchbox.validation import bundle as bundle_module

        seen: list[bool] = []
        monkeypatch.setattr(
            bundle_module,
            "_validate_bundle",
            lambda data, vr, **kwargs: seen.append(kwargs["manifest_present"]),
        )
        path = tmp_path / "result.json"
        path.write_text("{}", encoding="utf-8")
        if manifest:
            (tmp_path / "result.manifest.json").write_text(json.dumps({"bundle_file": "result.json"}), encoding="utf-8")
        bundle_module.validate_bundles([path], vendor_conditions_declared=declared)
        return seen[0]

    def test_a_bundle_on_its_own_carries_its_own_vendor_conditions(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        assert self._recorded_flags(monkeypatch, tmp_path, manifest=False, declared=False) is False

    def test_a_manifest_next_to_the_bundle_takes_over_the_vendor_conditions(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        assert self._recorded_flags(monkeypatch, tmp_path, manifest=True, declared=False) is True

    def test_a_per_bundle_manifest_naming_another_bundle_does_not_take_over(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from benchbox.validation import bundle as bundle_module

        seen: list[bool] = []
        monkeypatch.setattr(
            bundle_module,
            "_validate_bundle",
            lambda data, vr, **kwargs: seen.append(kwargs["manifest_present"]),
        )
        path = tmp_path / "result.json"
        path.write_text("{}", encoding="utf-8")
        manifest = tmp_path / "result.manifest.json"
        manifest.write_text(json.dumps({"bundle_file": "other.json"}), encoding="utf-8")

        bundle_module.validate_bundles([path])
        assert seen == [False]

        manifest.write_text(json.dumps({"bundle_file": "result.json"}), encoding="utf-8")
        bundle_module.validate_bundles([path])
        assert seen == [False, True]

    def test_a_shared_legacy_manifest_naming_another_bundle_does_not_take_over(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from benchbox.validation import bundle as bundle_module

        seen: list[bool] = []
        monkeypatch.setattr(
            bundle_module,
            "_validate_bundle",
            lambda data, vr, **kwargs: seen.append(kwargs["manifest_present"]),
        )
        path = tmp_path / "result.json"
        path.write_text("{}", encoding="utf-8")
        legacy = tmp_path / bundle_module.SUBMISSION_MANIFEST_FILENAME
        legacy.write_text(json.dumps({"bundle_file": "other.json"}), encoding="utf-8")

        bundle_module.validate_bundles([path])
        assert seen == [False]

        legacy.write_text(json.dumps({"bundle_file": "result.json"}), encoding="utf-8")
        bundle_module.validate_bundles([path])
        assert seen == [False, True]

    def test_submit_declares_the_conditions_it_enforces_before_staging(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        assert self._recorded_flags(monkeypatch, tmp_path, manifest=False, declared=True) is True
