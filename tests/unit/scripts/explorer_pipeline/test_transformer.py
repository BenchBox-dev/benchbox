from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from _project.scripts.explorer_pipeline import transformer as transformer_module
from _project.scripts.explorer_pipeline.models import DetailResult, ManifestEntry
from _project.scripts.explorer_pipeline.transformer import BundleTransformer
from benchbox.core.cost.models import DeploymentMetadata, NormalizedCost
from benchbox.validation.bundle import is_primary_bundle_file
from tests.unit.scripts.explorer_pipeline.conftest import MINIMAL_BUNDLE

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class TestToManifestEntry:
    def test_offset_timestamp_preserves_id_date_and_normalizes_read_model_date(self, tmp_path: Path) -> None:
        data = copy.deepcopy(MINIMAL_BUNDLE)
        data["run"]["timestamp"] = "2026-09-05T00:15:00+14:00"
        bundle = tmp_path / "offset.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        transformer = BundleTransformer()
        result_id = transformer.result_id_from_bundle(bundle)
        entry = transformer.to_manifest_entry(bundle, result_id=result_id)
        detail = transformer.to_detail_result(bundle, result_id=result_id)

        assert result_id.startswith("tpch-duckdb-sf0.1-20260905-")
        assert entry.run_date == "2026-09-04"
        assert detail.run_date == "2026-09-04"

    @pytest.mark.parametrize(
        "timestamp",
        ["2026-09-05Tnot-a-time", "2026-09-05T12:00", "2026-09-05T12:00:00Z trailing"],
    )
    def test_malformed_timestamp_is_rejected_before_read_model_projection(self, tmp_path: Path, timestamp: str) -> None:
        data = copy.deepcopy(MINIMAL_BUNDLE)
        data["run"]["timestamp"] = timestamp
        bundle = tmp_path / "bad-timestamp.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        with pytest.raises(ValueError, match="invalid run.timestamp"):
            BundleTransformer().to_manifest_entry(bundle)

    def test_basic_fields(self, bundle_file: Path) -> None:
        transformer = BundleTransformer()
        entry = transformer.to_manifest_entry(bundle_file)

        assert isinstance(entry, ManifestEntry)
        assert entry.benchmark == "tpch"
        assert entry.platform == "duckdb"
        assert entry.scale_factor == pytest.approx(0.1)
        assert entry.run_date == "2026-03-15"
        assert entry.total_duration_s == pytest.approx(45.0)
        assert entry.query_count == 2

    def test_trust_label_and_visibility_defaults(self, bundle_file: Path) -> None:
        transformer = BundleTransformer()
        entry = transformer.to_manifest_entry(bundle_file)

        assert entry.trust_label == "maintainer-run"
        assert entry.visibility == "public-curated"

    def test_custom_trust_label_and_visibility(self, bundle_file: Path) -> None:
        transformer = BundleTransformer()
        entry = transformer.to_manifest_entry(
            bundle_file,
            trust_label="community-submission",
            visibility="public-self-reported",
        )

        assert entry.trust_label == "community-submission"
        assert entry.visibility == "public-self-reported"

    def test_power_score_extracted(self, bundle_file: Path) -> None:
        transformer = BundleTransformer()
        entry = transformer.to_manifest_entry(bundle_file)

        assert entry.power_score == pytest.approx(1234.56)

    def test_composite_metrics_are_not_used_as_the_power_score(self, tmp_path: Path) -> None:
        data = copy.deepcopy(MINIMAL_BUNDLE)
        data["summary"]["tpc_metrics"] = {"qphh_at_size": 777.0, "qphds_at_size": 888.0}
        bundle = tmp_path / "legacy-composite.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        entry = BundleTransformer().to_manifest_entry(bundle)

        assert entry.power_score is None

    def test_driver_version_extracted(self, bundle_file: Path) -> None:
        transformer = BundleTransformer()
        entry = transformer.to_manifest_entry(bundle_file)

        assert entry.driver_version == "1.2.0"

    def test_duckdb_dev_build_keeps_engine_and_package_versions_separate(self, tmp_path: Path) -> None:
        data = copy.deepcopy(MINIMAL_BUNDLE)
        data["platform"].update(
            {
                "version": "2.0.0-alpha38615",
                "client_version": "1.6.0.dev365",
            }
        )
        data["execution"].update(
            {
                "driver_actual_version": "2.0.0-alpha38615",
                "driver_resolved_version": "1.6.0.dev365",
            }
        )
        bundle = tmp_path / "duckdb-dev.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        entry = BundleTransformer().to_manifest_entry(bundle)

        assert entry.driver_version == "1.6.0.dev365"
        assert entry.platform_version == "2.0.0-alpha38615"

    def test_result_id_injected(self, bundle_file: Path) -> None:
        transformer = BundleTransformer()
        explicit_id = "my-explicit-id"
        entry = transformer.to_manifest_entry(bundle_file, result_id=explicit_id)

        assert entry.result_id == explicit_id

    def test_current_result_extension_blocks_are_ingested_without_projection_breakage(self, tmp_path: Path) -> None:
        data = copy.deepcopy(MINIMAL_BUNDLE)
        data["platform"].update(
            {
                "deployment": {"deployment_type": "local", "endpoint_class": "localhost_port"},
                "cloud": {"provider": "none"},
                "compute": {"instance_type": "developer-laptop"},
                "storage": {"storage_type": "local_disk"},
                "raw_config": {"host": "localhost", "port": 5432},
                "raw_metadata": {"server_version": "16.2"},
            }
        )
        data["phases"] = {
            "migration": {
                "status": "COMPLETED",
                "duration_ms": 2500,
                "tables_migrated": 8,
                "tables_failed": 0,
            }
        }
        data["comparisons"] = {
            "native_duckdb": {
                "generated_at": "2026-05-22T12:00:00",
                "scale_factor": 0.01,
                "total_queries": 1,
                "mean_delta_ms": 12.5,
                "max_delta_ms": 12.5,
                "queries": [{"id": "Q1", "pg_duckdb_ms": 42.5, "duckdb_ms": 30.0, "delta_ms": 12.5}],
            }
        }
        bundle = tmp_path / "extension_bundle.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        transformer = BundleTransformer()
        entry = transformer.to_manifest_entry(bundle)
        detail = transformer.to_detail_result(bundle, entry.result_id)

        assert entry.platform_version == "1.2.0"
        assert detail.phase_durations == {"migration": 2.5}
        assert detail.environment.os == "macOS 15.3.0"


class TestToDetailResult:
    def test_basic_fields(self, bundle_file: Path) -> None:
        transformer = BundleTransformer()
        rid = transformer.result_id_from_bundle(bundle_file)
        detail = transformer.to_detail_result(bundle_file, rid)

        assert isinstance(detail, DetailResult)
        assert detail.result_id == rid
        assert detail.benchmark == "tpch"
        assert detail.platform == "duckdb"
        assert detail.scale_factor == pytest.approx(0.1)

    def test_queries_extracted(self, bundle_file: Path) -> None:
        transformer = BundleTransformer()
        rid = transformer.result_id_from_bundle(bundle_file)
        detail = transformer.to_detail_result(bundle_file, rid)

        assert len(detail.queries) == 2
        q_ids = {q.query_id for q in detail.queries}
        assert q_ids == {"Q1", "Q6"}
        for q in detail.queries:
            assert q.status == "pass"

    def test_environment_extracted(self, bundle_file: Path) -> None:
        transformer = BundleTransformer()
        rid = transformer.result_id_from_bundle(bundle_file)
        detail = transformer.to_detail_result(bundle_file, rid)

        assert detail.environment.os == "macOS 15.3.0"
        assert detail.environment.arch == "arm64"

    def test_no_companion_files_by_default(self, bundle_file: Path) -> None:
        transformer = BundleTransformer()
        rid = transformer.result_id_from_bundle(bundle_file)
        detail = transformer.to_detail_result(bundle_file, rid)

        assert detail.has_plans is False
        assert detail.has_tuning is False

    def test_has_plans_detected(self, bundle_file: Path) -> None:
        plans_path = bundle_file.with_name(bundle_file.stem + ".plans.json")
        plans_path.write_text("{}", encoding="utf-8")

        transformer = BundleTransformer()
        rid = transformer.result_id_from_bundle(bundle_file)
        detail = transformer.to_detail_result(bundle_file, rid)

        assert detail.has_plans is True

    def test_bundle_download_url_set(self, bundle_file: Path) -> None:
        transformer = BundleTransformer()
        rid = transformer.result_id_from_bundle(bundle_file)
        url = f"/results/data/bundles/{rid}.json"
        detail = transformer.to_detail_result(bundle_file, rid, bundle_download_url=url)

        assert detail.bundle_download_url == url

    def test_detail_run_date_is_short_date(self, bundle_file: Path) -> None:
        transformer = BundleTransformer()
        rid = transformer.result_id_from_bundle(bundle_file)
        detail = transformer.to_detail_result(bundle_file, rid)

        assert detail.run_date == "2026-03-15"


class TestResultIdFromBundle:
    def test_stable_id(self, bundle_file: Path) -> None:
        transformer = BundleTransformer()
        rid1 = transformer.result_id_from_bundle(bundle_file)
        rid2 = transformer.result_id_from_bundle(bundle_file)

        assert rid1 == rid2

    def test_id_format(self, bundle_file: Path) -> None:
        transformer = BundleTransformer()
        rid = transformer.result_id_from_bundle(bundle_file)

        assert rid.startswith("tpch-duckdb-sf0.1-")
        parts = rid.split("-")
        sha_part = parts[-1]
        assert len(sha_part) == 8
        assert all(c in "0123456789abcdef" for c in sha_part)

    def test_id_differs_for_different_content(self, bundle_file: Path, tmp_path: Path) -> None:
        other_data = json.loads(bundle_file.read_text(encoding="utf-8"))
        other_data["run"]["id"] = "different-exec"
        other_bundle = tmp_path / "other.json"
        other_bundle.write_text(json.dumps(other_data), encoding="utf-8")

        transformer = BundleTransformer()
        rid1 = transformer.result_id_from_bundle(bundle_file)
        rid2 = transformer.result_id_from_bundle(other_bundle)

        assert rid1 != rid2


class TestMalformedJson:
    def test_malformed_json_raises(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{not valid json", encoding="utf-8")

        transformer = BundleTransformer()
        with pytest.raises(Exception):
            transformer.result_id_from_bundle(bad_file)

    def test_malformed_json_to_manifest_raises(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("}}broken{{", encoding="utf-8")

        transformer = BundleTransformer()
        with pytest.raises(Exception):
            transformer.to_manifest_entry(bad_file)


class TestSchemaGate:
    @pytest.mark.parametrize("version", ["2.99", "1.0", "2.x"])
    def test_unsupported_schema_rejected_before_manifest_projection(self, tmp_path: Path, version: str) -> None:
        data = copy.deepcopy(MINIMAL_BUNDLE)
        data["version"] = version
        bundle = tmp_path / "unsupported_schema.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        transformer = BundleTransformer()
        with pytest.raises(ValueError) as exc_info:
            transformer.to_manifest_entry(bundle)

        message = str(exc_info.value)
        assert "explorer input schema policy" in message
        assert "Re-export or normalize" in message

    def test_missing_schema_rejected_before_detail_projection(self, tmp_path: Path) -> None:
        data = copy.deepcopy(MINIMAL_BUNDLE)
        del data["version"]
        bundle = tmp_path / "missing_schema.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        transformer = BundleTransformer()
        with pytest.raises(ValueError) as exc_info:
            transformer.to_detail_result(bundle, "missing-schema")

        assert "<missing>" in str(exc_info.value)
        assert "explorer input schema policy" in str(exc_info.value)

    def test_preloaded_data_uses_same_schema_gate(self, bundle_file: Path) -> None:
        data = copy.deepcopy(MINIMAL_BUNDLE)
        data["version"] = "2.99"

        transformer = BundleTransformer()
        with pytest.raises(ValueError, match="explorer input schema policy"):
            transformer.result_id_from_bundle(bundle_file, data=data, raw=b"{}")


class TestZeroDurationQuery:
    def test_zero_ms_preserved(self, tmp_path: Path) -> None:
        data = {**MINIMAL_BUNDLE}
        data["queries"] = [
            {"id": "Q1", "ms": 0.0, "rows": 0, "iter": 1, "stream": 0, "run_type": "measurement", "status": "SUCCESS"},
        ]
        bundle = tmp_path / "zero_ms.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        transformer = BundleTransformer()
        rid = transformer.result_id_from_bundle(bundle)
        detail = transformer.to_detail_result(bundle, rid)

        assert len(detail.queries) == 1
        assert detail.queries[0].duration_ms == pytest.approx(0.0)

    def test_warmup_queries_admitted_to_queries_but_excluded_from_display_timings(self, tmp_path: Path) -> None:
        data = {**MINIMAL_BUNDLE}
        data["queries"] = [
            {"id": "Q1", "ms": 100.0, "iter": 0, "stream": 0, "run_type": "warmup", "status": "SUCCESS"},
            {"id": "Q1", "ms": 200.0, "iter": 1, "stream": 0, "run_type": "measurement", "status": "SUCCESS"},
        ]
        bundle = tmp_path / "warmup.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        transformer = BundleTransformer()
        rid = transformer.result_id_from_bundle(bundle)
        detail = transformer.to_detail_result(bundle, rid)

        assert len(detail.queries) == 2
        run_types = {q.run_type for q in detail.queries}
        assert run_types == {"warmup", "measurement"}

        dt_q1 = next(dt for dt in detail.display_timings if dt.query_id == "Q1")
        assert dt_q1.display_ms == pytest.approx(200.0)
        assert dt_q1.sample_count == 1


class TestExtendedManifestFields:
    def test_geomean_ms_computed(self, bundle_file: Path) -> None:
        import math

        transformer = BundleTransformer()
        entry = transformer.to_manifest_entry(bundle_file)

        expected = math.exp((math.log(8000.0) + math.log(4000.0)) / 2)
        assert entry.geomean_ms == pytest.approx(expected)

    def test_geomean_ms_none_when_no_queries(self, tmp_path: Path) -> None:
        data = {**MINIMAL_BUNDLE, "queries": []}
        bundle = tmp_path / "no_queries.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        transformer = BundleTransformer()
        entry = transformer.to_manifest_entry(bundle)

        assert entry.geomean_ms is None

    def test_platform_version_extracted(self, bundle_file: Path) -> None:
        transformer = BundleTransformer()
        entry = transformer.to_manifest_entry(bundle_file)

        assert entry.platform_version == "1.2.0"

    def test_execution_mode_none_when_absent(self, bundle_file: Path) -> None:
        transformer = BundleTransformer()
        entry = transformer.to_manifest_entry(bundle_file)

        assert entry.execution_mode is None

    def test_execution_mode_extracted(self, tmp_path: Path) -> None:
        import copy

        data = copy.deepcopy(MINIMAL_BUNDLE)
        data["config"]["execution_mode"] = "sql"
        bundle = tmp_path / "exec_mode.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        transformer = BundleTransformer()
        entry = transformer.to_manifest_entry(bundle)

        assert entry.execution_mode == "sql"

    def test_tuning_mode_none_when_absent(self, bundle_file: Path) -> None:
        transformer = BundleTransformer()
        entry = transformer.to_manifest_entry(bundle_file)

        assert entry.tuning_mode is None

    def test_tuning_hash_none_when_no_tuning(self, bundle_file: Path) -> None:
        transformer = BundleTransformer()
        entry = transformer.to_manifest_entry(bundle_file)

        assert entry.tuning_hash is None

    def test_tuning_hash_computed_when_present(self, tmp_path: Path) -> None:
        import copy

        data = copy.deepcopy(MINIMAL_BUNDLE)
        data["config"]["tuning_mode"] = "tuned"
        bundle = tmp_path / "tuned.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        transformer = BundleTransformer()
        entry = transformer.to_manifest_entry(bundle)

        assert entry.tuning_hash is not None
        assert len(entry.tuning_hash) == 8
        assert all(c in "0123456789abcdef" for c in entry.tuning_hash)

    def test_tuning_mode_falls_back_to_execution_block(self, tmp_path: Path) -> None:
        import copy

        data = copy.deepcopy(MINIMAL_BUNDLE)
        data["execution"]["tuning_mode"] = "tuned"
        bundle = tmp_path / "exec_tuning_mode.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        transformer = BundleTransformer()
        entry = transformer.to_manifest_entry(bundle)
        detail = transformer.to_detail_result(bundle, result_id="exec-tuning-mode")

        assert entry.tuning_mode == "tuned"
        assert detail.tuning_mode == "tuned"

    def test_tuning_mode_prefers_config_over_execution(self, tmp_path: Path) -> None:
        import copy

        data = copy.deepcopy(MINIMAL_BUNDLE)
        data["config"]["tuning_mode"] = "custom"
        data["execution"]["tuning_mode"] = "tuned"
        bundle = tmp_path / "both_tuning_mode.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        transformer = BundleTransformer()
        entry = transformer.to_manifest_entry(bundle)

        assert entry.tuning_mode == "custom"

    def test_tuning_mode_stays_none_when_absent_everywhere(self, tmp_path: Path) -> None:
        import copy

        data = copy.deepcopy(MINIMAL_BUNDLE)
        bundle = tmp_path / "no_tuning_mode.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        transformer = BundleTransformer()
        entry = transformer.to_manifest_entry(bundle)

        assert entry.tuning_mode is None

    def test_legacy_raw_file_path_treated_as_not_recorded(self, tmp_path: Path) -> None:
        import copy

        data = copy.deepcopy(MINIMAL_BUNDLE)
        data["config"]["tuning_mode"] = "examples/tunings/duckdb/tpch_tuned.yaml"
        bundle = tmp_path / "legacy_path_tuning_mode.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        transformer = BundleTransformer()
        entry = transformer.to_manifest_entry(bundle)

        assert entry.tuning_mode is None

    def test_legacy_balanced_flavor_string_treated_as_not_recorded(self, tmp_path: Path) -> None:
        import copy

        data = copy.deepcopy(MINIMAL_BUNDLE)
        data["config"]["tuning_mode"] = "balanced"
        bundle = tmp_path / "legacy_balanced_tuning_mode.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        transformer = BundleTransformer()
        entry = transformer.to_manifest_entry(bundle)

        assert entry.tuning_mode is None

    def test_legacy_config_value_falls_back_to_valid_execution_value(self, tmp_path: Path) -> None:
        import copy

        data = copy.deepcopy(MINIMAL_BUNDLE)
        data["config"]["tuning_mode"] = "balanced"
        data["execution"]["tuning_mode"] = "tuned"
        bundle = tmp_path / "legacy_config_valid_execution.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        transformer = BundleTransformer()
        entry = transformer.to_manifest_entry(bundle)

        assert entry.tuning_mode == "tuned"

    def test_physical_mechanisms_and_rendering_id_extracted_from_logical_profile(self, tmp_path: Path) -> None:
        import copy

        data = copy.deepcopy(MINIMAL_BUNDLE)
        data["config"]["tuning_mode"] = "tuned"
        data["platform"]["tuning"] = {
            "source": "auto",
            "logical_profile": {
                "physical_rendering_id": "databricks_z_order",
                "physical_mechanisms": ["indexes", "clustering"],
            },
        }
        bundle = tmp_path / "logical_profile.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        transformer = BundleTransformer()
        detail = transformer.to_detail_result(bundle, result_id="logical-profile")

        assert detail.physical_rendering_id == "databricks_z_order"
        assert detail.physical_mechanisms == ["indexes", "clustering"]

    def test_physical_mechanisms_none_and_rendering_id_none_when_no_logical_profile_recorded(
        self, bundle_file: Path
    ) -> None:
        transformer = BundleTransformer()
        detail = transformer.to_detail_result(bundle_file, result_id="no-logical-profile")

        assert detail.physical_mechanisms is None
        assert detail.physical_rendering_id is None

    def test_physical_mechanisms_empty_list_when_logical_profile_recorded_with_zero_mechanisms(
        self, tmp_path: Path
    ) -> None:
        import copy

        data = copy.deepcopy(MINIMAL_BUNDLE)
        data["config"]["tuning_mode"] = "tuned"
        data["platform"]["tuning"] = {
            "source": "auto",
            "logical_profile": {"physical_mechanisms": []},
        }
        bundle = tmp_path / "logical_profile_empty_mechanisms.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        transformer = BundleTransformer()
        detail = transformer.to_detail_result(bundle, result_id="empty-mechanisms")

        assert detail.physical_mechanisms == []
        assert detail.physical_mechanisms is not None

    def test_tuned_fallback_and_custom_pass_through_verbatim(self, tmp_path: Path) -> None:
        import copy

        for mode in ("tuned-fallback", "custom"):
            data = copy.deepcopy(MINIMAL_BUNDLE)
            data["config"]["tuning_mode"] = mode
            bundle = tmp_path / f"mode_{mode}.json"
            bundle.write_text(json.dumps(data), encoding="utf-8")

            transformer = BundleTransformer()
            entry = transformer.to_manifest_entry(bundle)

            assert entry.tuning_mode == mode

    def test_tuning_hash_uses_execution_fallback_mode(self, tmp_path: Path) -> None:
        import copy

        data = copy.deepcopy(MINIMAL_BUNDLE)
        data["execution"]["tuning_mode"] = "tuned"
        bundle = tmp_path / "exec_tuning_hash.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        transformer = BundleTransformer()
        entry = transformer.to_manifest_entry(bundle)

        assert entry.tuning_hash is not None
        assert len(entry.tuning_hash) == 8

    def test_config_hashes_ingested_verbatim_from_tuning_summary(self, tmp_path: Path) -> None:
        import copy

        data = copy.deepcopy(MINIMAL_BUNDLE)
        data["platform"]["tuning"] = {
            "requested_config_hash": "a" * 64,
            "applied_ledger_hash": "b" * 64,
        }
        bundle = tmp_path / "hashes.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        entry = BundleTransformer().to_manifest_entry(bundle)
        assert entry.requested_config_hash == "a" * 64
        assert entry.applied_ledger_hash == "b" * 64

    def test_benchmark_support_status_resolved_from_registry(self, bundle_file: Path) -> None:
        entry = BundleTransformer().to_manifest_entry(bundle_file)
        assert entry.benchmark == "tpch"
        assert entry.benchmark_support_status == "stable"

    def test_benchmark_support_status_canonicalizes_legacy_slug(self, tmp_path: Path) -> None:
        import copy

        data = copy.deepcopy(MINIMAL_BUNDLE)
        data["benchmark"]["id"] = "star_schema"
        bundle = tmp_path / "legacy_slug.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        entry = BundleTransformer().to_manifest_entry(bundle)
        assert entry.benchmark_support_status == "stable"

    def test_benchmark_support_status_none_for_unknown_slug(self, tmp_path: Path) -> None:
        import copy

        data = copy.deepcopy(MINIMAL_BUNDLE)
        data["benchmark"]["id"] = "custom-internal-benchmark"
        bundle = tmp_path / "custom_slug.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        entry = BundleTransformer().to_manifest_entry(bundle)
        assert entry.benchmark_support_status is None

    def test_config_hashes_none_for_legacy_bundle(self, bundle_file: Path) -> None:
        entry = BundleTransformer().to_manifest_entry(bundle_file)
        assert entry.requested_config_hash is None
        assert entry.applied_ledger_hash is None

    def test_applied_ledger_hash_none_when_only_requested_present(self, tmp_path: Path) -> None:
        import copy

        data = copy.deepcopy(MINIMAL_BUNDLE)
        data["platform"]["tuning"] = {"requested_config_hash": "c" * 64}
        bundle = tmp_path / "req_only.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        entry = BundleTransformer().to_manifest_entry(bundle)
        assert entry.requested_config_hash == "c" * 64
        assert entry.applied_ledger_hash is None

    def test_tuning_policy_generation_ingested_verbatim_from_tuning_summary(self, tmp_path: Path) -> None:
        import copy

        data = copy.deepcopy(MINIMAL_BUNDLE)
        data["platform"]["tuning"] = {"tuning_policy_generation": "adr-003"}
        bundle = tmp_path / "generation.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        transformer = BundleTransformer()
        assert transformer.to_manifest_entry(bundle).tuning_policy_generation == "adr-003"
        assert transformer.to_detail_result(bundle, result_id="gen").tuning_policy_generation == "adr-003"

    def test_tuning_policy_generation_none_for_legacy_bundle(self, bundle_file: Path) -> None:
        transformer = BundleTransformer()
        assert transformer.to_manifest_entry(bundle_file).tuning_policy_generation is None
        assert transformer.to_detail_result(bundle_file, result_id="legacy").tuning_policy_generation is None

    def test_tuning_validation_status_ingested_verbatim_from_tuning_summary(self, tmp_path: Path) -> None:
        import copy

        data = copy.deepcopy(MINIMAL_BUNDLE)
        data["platform"]["tuning"] = {"validation_status": "applied_verified"}
        bundle = tmp_path / "verified.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        transformer = BundleTransformer()
        assert transformer.to_manifest_entry(bundle).tuning_validation_status == "applied_verified"
        assert transformer.to_detail_result(bundle, result_id="v").tuning_validation_status == "applied_verified"

    def test_untuned_bundle_not_applicable_status_ingested_verbatim(self, tmp_path: Path) -> None:
        import copy

        data = copy.deepcopy(MINIMAL_BUNDLE)
        data["platform"]["tuning"] = {"validation_status": "not_applicable"}
        bundle = tmp_path / "untuned.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        transformer = BundleTransformer()
        assert transformer.to_manifest_entry(bundle).tuning_validation_status == "not_applicable"
        detail = transformer.to_detail_result(bundle, result_id="untuned")
        assert detail.tuning_validation_status == "not_applicable"
        assert detail.has_tuning is False

    def test_tuning_validation_status_none_for_legacy_bundle(self, bundle_file: Path) -> None:
        transformer = BundleTransformer()
        assert transformer.to_manifest_entry(bundle_file).tuning_validation_status is None
        assert transformer.to_detail_result(bundle_file, result_id="legacy").tuning_validation_status is None

    def test_dataframe_bundle_applied_ledger_hash_ingests_end_to_end(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from types import SimpleNamespace

        from benchbox.core.dataframe.tuning.interface import DataFrameTuningConfiguration
        from benchbox.core.results.schema import build_result_payload
        from benchbox.core.schemas import BenchmarkConfig
        from benchbox.platforms.dataframe.benchmark_mixin import DataFramePhases, DataFrameRunOptions
        from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter
        from tests.utilities.session_isolation import own_environment

        own_environment(monkeypatch, ["POLARS_MAX_THREADS"])
        cfg = DataFrameTuningConfiguration()
        cfg.parallelism.thread_count = 4
        cfg.execution.streaming_mode = True
        adapter = PolarsDataFrameAdapter(tuning_config=cfg)
        result = adapter.run_benchmark(
            SimpleNamespace(name="tpch", display_name="TPC-H", scale_factor=1.0, tables={}),
            benchmark_config=BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=1.0),
            phases=DataFramePhases(load=False, execute=False),
            options=DataFrameRunOptions(ignore_memory_warnings=True, prefer_parquet=False),
        )
        assert result.applied_ledger_hash is not None

        bundle = tmp_path / "dataframe_run.json"
        bundle.write_text(json.dumps(build_result_payload(result)), encoding="utf-8")

        entry = BundleTransformer().to_manifest_entry(bundle)
        assert entry.platform == "Polars"
        assert entry.applied_ledger_hash == result.applied_ledger_hash

        detail = BundleTransformer().to_detail_result(bundle, result_id="df-ledger")
        assert detail.applied_ledger_hash == result.applied_ledger_hash

    def test_tuning_hash_dict_detail_is_hashed_canonically(self, tmp_path: Path) -> None:
        import copy

        data_a = copy.deepcopy(MINIMAL_BUNDLE)
        data_a["config"]["tuning_mode"] = "tuned"
        data_a["config"]["tuning_config"] = {"a": 1, "b": 2}
        bundle_a = tmp_path / "dict_a.json"
        bundle_a.write_text(json.dumps(data_a), encoding="utf-8")

        data_b = copy.deepcopy(MINIMAL_BUNDLE)
        data_b["config"]["tuning_mode"] = "tuned"
        data_b["config"]["tuning_config"] = {"b": 2, "a": 1}
        bundle_b = tmp_path / "dict_b.json"
        bundle_b.write_text(json.dumps(data_b), encoding="utf-8")

        transformer = BundleTransformer()
        entry_a = transformer.to_manifest_entry(bundle_a)
        entry_b = transformer.to_manifest_entry(bundle_b)

        assert entry_a.tuning_hash == entry_b.tuning_hash
        assert entry_a.tuning_hash is not None

    def test_tuning_hash_string_repr_detail_is_not_hashed(self, tmp_path: Path) -> None:
        import copy

        data_a = copy.deepcopy(MINIMAL_BUNDLE)
        data_a["config"]["tuning_mode"] = "tuned"
        data_a["config"]["tuning_config"] = (
            "UnifiedTuningConfiguration(primary_keys=PrimaryKeyConfiguration(enabled=True))"
        )
        bundle_a = tmp_path / "repr_a.json"
        bundle_a.write_text(json.dumps(data_a), encoding="utf-8")

        data_b = copy.deepcopy(MINIMAL_BUNDLE)
        data_b["config"]["tuning_mode"] = "tuned"
        data_b["config"]["tuning_config"] = (
            "UnifiedTuningConfiguration(primary_keys=PrimaryKeyConfiguration(enabled=False))"
        )
        bundle_b = tmp_path / "repr_b.json"
        bundle_b.write_text(json.dumps(data_b), encoding="utf-8")

        transformer = BundleTransformer()
        entry_a = transformer.to_manifest_entry(bundle_a)
        entry_b = transformer.to_manifest_entry(bundle_b)
        mode_only = transformer.to_manifest_entry(bundle_a, data={**data_a, "config": {"tuning_mode": "tuned"}})

        assert entry_a.tuning_hash is not None
        assert entry_a.tuning_hash == entry_b.tuning_hash
        assert entry_a.tuning_hash == mode_only.tuning_hash

    def test_tuning_hash_none_when_detail_is_repr_string_and_no_mode(self, tmp_path: Path) -> None:
        import copy

        data = copy.deepcopy(MINIMAL_BUNDLE)
        data["config"]["tuning_config"] = (
            "UnifiedTuningConfiguration(primary_keys=PrimaryKeyConfiguration(enabled=True))"
        )
        bundle = tmp_path / "repr_no_mode.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        transformer = BundleTransformer()
        entry = transformer.to_manifest_entry(bundle)

        assert entry.tuning_hash is None

    def test_test_type_from_benchmark_block(self, bundle_file: Path) -> None:
        transformer = BundleTransformer()
        entry = transformer.to_manifest_entry(bundle_file)

        assert entry.test_type == "power"

    def test_validation_status_extracted(self, bundle_file: Path) -> None:
        transformer = BundleTransformer()
        entry = transformer.to_manifest_entry(bundle_file)

        assert entry.validation_status == "passed"

    def test_validation_status_dict_form(self, tmp_path: Path) -> None:
        import copy

        data = copy.deepcopy(MINIMAL_BUNDLE)
        data["summary"]["validation"] = {"status": "passed", "mode": "exact"}
        bundle = tmp_path / "dict_validation.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        transformer = BundleTransformer()
        entry = transformer.to_manifest_entry(bundle)

        assert entry.validation_status == "passed"

    def test_translation_fallback_marks_explorer_validation_uncertain(self, tmp_path: Path) -> None:
        data = copy.deepcopy(MINIMAL_BUNDLE)
        data["summary"]["validation"] = "passed"
        data["execution"] = {
            "mode": "sql",
            "translation": {"status": "fallback", "strict_mode": False},
        }
        bundle = tmp_path / "translation_fallback.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        transformer = BundleTransformer()
        entry = transformer.to_manifest_entry(bundle)
        detail = transformer.to_detail_result(bundle, result_id="translation-fallback")

        assert entry.validation_status == "uncertain"
        assert entry.ranking_exclusion_reason == "validation_not_clean"
        assert detail.validation_status == "uncertain"
        assert detail.ranking_exclusion_reason == "validation_not_clean"

    def test_partial_query_failure_count_and_status_extracted(self, tmp_path: Path) -> None:
        data = copy.deepcopy(MINIMAL_BUNDLE)
        data["summary"]["queries"] = {"total": 2, "passed": 1, "failed": 1}
        data["summary"]["validation"] = "passed"
        data["queries"][1]["status"] = "ERROR"
        bundle = tmp_path / "partial_query_failure.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        transformer = BundleTransformer()
        entry = transformer.to_manifest_entry(bundle)
        detail = transformer.to_detail_result(bundle, result_id="partial-query-failure")

        assert entry.failed_query_count == 1
        assert detail.failed_query_count == 1
        assert entry.validation_status == "partial"
        assert detail.validation_status == "partial"

    def test_missing_status_does_not_create_partial_query_failure(self, tmp_path: Path) -> None:
        data = copy.deepcopy(MINIMAL_BUNDLE)
        for query in data["queries"]:
            del query["status"]
        bundle = tmp_path / "legacy_no_query_status.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        transformer = BundleTransformer()
        entry = transformer.to_manifest_entry(bundle)

        assert entry.failed_query_count == 0
        assert entry.validation_status == "passed"

    def test_cost_usd_none_when_absent(self, bundle_file: Path) -> None:
        transformer = BundleTransformer()
        entry = transformer.to_manifest_entry(bundle_file)

        assert entry.cost_usd is None
        assert entry.normalized_cost.cost_status == "unavailable"
        assert entry.normalized_cost.normalized_cost_usd is None

    def test_legacy_total_cost_does_not_populate_normalized_alias(self, tmp_path: Path) -> None:
        import copy

        data = copy.deepcopy(MINIMAL_BUNDLE)
        data["cost"] = {"total_usd": 1.23}
        bundle = tmp_path / "cost.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        transformer = BundleTransformer()
        entry = transformer.to_manifest_entry(bundle)

        assert entry.cost_usd is None
        assert entry.normalized_cost.cost_status == "unavailable"

    @pytest.mark.parametrize(
        ("normalized_cost", "expected_cost_usd"),
        [
            (
                NormalizedCost(
                    normalized_cost_usd="1.23",
                    cost_model_version="2026.05.0",
                    cost_model_source="benchbox.core.cost.pricing",
                    cost_scope="compute_only",
                    cost_status="normalized",
                    billing_unit="instance_hour",
                    pricing_region="us-east-1",
                    deployment=DeploymentMetadata(
                        cloud_provider="aws",
                        cloud_region="us-east-1",
                        instance_type="r7i.4xlarge",
                        node_count=2,
                        storage_format="parquet",
                    ),
                ),
                1.23,
            ),
            (
                NormalizedCost(
                    normalized_cost_usd="0",
                    cost_model_version="2026.05.0",
                    cost_model_source="benchbox.core.cost.pricing",
                    cost_scope="compute_only",
                    cost_status="not_applicable_local",
                    billing_unit="not_applicable",
                    pricing_region="not_applicable",
                ),
                None,
            ),
            (
                NormalizedCost(
                    normalized_cost_usd=None,
                    cost_model_version="2026.05.0",
                    cost_model_source="benchbox.core.cost.pricing",
                    cost_scope="compute_only",
                    cost_status="unavailable",
                    billing_unit="unknown",
                    pricing_region="unknown",
                ),
                None,
            ),
        ],
    )
    def test_normalized_cost_statuses_extracted(
        self,
        tmp_path: Path,
        normalized_cost: NormalizedCost,
        expected_cost_usd: float | None,
    ) -> None:
        data = copy.deepcopy(MINIMAL_BUNDLE)
        data["normalized_cost"] = normalized_cost.to_dict()
        bundle = tmp_path / f"{normalized_cost.cost_status}.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        transformer = BundleTransformer()
        entry = transformer.to_manifest_entry(bundle)
        rid = transformer.result_id_from_bundle(bundle)
        detail = transformer.to_detail_result(bundle, rid)

        assert entry.cost_usd == expected_cost_usd
        assert detail.cost_usd == expected_cost_usd
        assert entry.normalized_cost == normalized_cost
        assert detail.normalized_cost == normalized_cost

    def test_environment_facets_extracted_from_normalized_contract(self, tmp_path: Path) -> None:
        data = copy.deepcopy(MINIMAL_BUNDLE)
        data["environment"]["platform_runtime"] = {
            "runtime_type": "managed_cloud",
            "collection_status": "partial",
            "source": "requested",
        }
        data["platform"]["deployment"] = {
            "deployment_type": "managed_cloud",
            "endpoint_class": "cloud_endpoint",
            "collection_status": "partial",
        }
        data["platform"]["cloud"] = {
            "provider": "aws",
            "region": "us-east-1",
            "collection_status": "partial",
        }
        data["platform"]["compute"] = {
            "warehouse_size": "XSMALL",
            "warehouse": "warehouse_hash",
            "collection_status": "partial",
        }
        data["platform"]["storage"] = {
            "table_format": "parquet",
            "collection_status": "partial",
        }
        bundle = tmp_path / "normalized_environment.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        entry = BundleTransformer().to_manifest_entry(bundle)

        assert entry.deployment_class == "cloud"
        assert entry.cloud_provider == "aws"
        assert entry.cloud_region == "us-east-1"
        assert entry.instance_or_warehouse == "XSMALL"
        assert entry.storage_format == "parquet"

    def test_environment_facets_do_not_fall_back_to_normalized_cost_deployment(self, tmp_path: Path) -> None:
        data = copy.deepcopy(MINIMAL_BUNDLE)
        data["environment"]["platform_runtime"] = {
            "runtime_type": "unknown",
            "collection_status": "unavailable",
            "source": "unavailable",
        }
        data["normalized_cost"] = NormalizedCost(
            normalized_cost_usd="1.23",
            cost_model_version="2026.05.0",
            cost_model_source="benchbox.core.cost.pricing",
            cost_scope="compute_only",
            cost_status="normalized",
            billing_unit="instance_hour",
            pricing_region="us-east-1",
            deployment=DeploymentMetadata(
                cloud_provider="aws",
                cloud_region="us-east-1",
                instance_type="r7i.4xlarge",
                warehouse_size="LARGE",
                storage_format="parquet",
            ),
        ).to_dict()
        bundle = tmp_path / "cost_deployment_only.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        entry = BundleTransformer().to_manifest_entry(bundle)

        assert entry.deployment_class == "unavailable"
        assert entry.cloud_provider is None
        assert entry.cloud_region is None
        assert entry.instance_or_warehouse is None
        assert entry.storage_format is None

    def test_legacy_environment_facets_fall_back_to_normalized_cost_deployment(self, tmp_path: Path) -> None:
        data = copy.deepcopy(MINIMAL_BUNDLE)
        data["normalized_cost"] = NormalizedCost(
            normalized_cost_usd="1.23",
            cost_model_version="2026.05.0",
            cost_model_source="benchbox.core.cost.pricing",
            cost_scope="compute_only",
            cost_status="normalized",
            billing_unit="instance_hour",
            pricing_region="us-east-1",
            deployment=DeploymentMetadata(
                cloud_provider="aws",
                cloud_region="us-east-1",
                instance_type="r7i.4xlarge",
                warehouse_size="LARGE",
                storage_format="parquet",
            ),
        ).to_dict()
        bundle = tmp_path / "legacy_cost_deployment.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        entry = BundleTransformer().to_manifest_entry(bundle)

        assert entry.deployment_class == "cloud"
        assert entry.cloud_provider == "aws"
        assert entry.cloud_region == "us-east-1"
        assert entry.instance_or_warehouse == "r7i.4xlarge"
        assert entry.storage_format == "parquet"

    def test_partial_normalized_cost_rejected(self, tmp_path: Path) -> None:
        data = copy.deepcopy(MINIMAL_BUNDLE)
        data["normalized_cost"] = {
            "normalized_cost_usd": "1.23",
            "cost_status": "normalized",
        }
        bundle = tmp_path / "partial_normalized_cost.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        transformer = BundleTransformer()
        with pytest.raises(ValueError, match="cost_model_version"):
            transformer.to_manifest_entry(bundle)

    def test_explicitly_empty_normalized_cost_rejected(self, tmp_path: Path) -> None:
        data = copy.deepcopy(MINIMAL_BUNDLE)
        data["normalized_cost"] = {}
        bundle = tmp_path / "empty_normalized_cost.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        transformer = BundleTransformer()
        with pytest.raises(ValueError, match="cost_model_version"):
            transformer.to_manifest_entry(bundle)

    def test_absent_normalized_cost_stays_unavailable(self, bundle_file: Path) -> None:
        entry = BundleTransformer().to_manifest_entry(bundle_file)

        assert entry.normalized_cost.cost_status == "unavailable"
        assert entry.normalized_cost.normalized_cost_usd is None

    @pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
    def test_non_finite_normalized_cost_rejected(self, tmp_path: Path, value: str) -> None:
        data = copy.deepcopy(MINIMAL_BUNDLE)
        data["normalized_cost"] = NormalizedCost(
            normalized_cost_usd="1.23",
            cost_model_version="2026.05.0",
            cost_model_source="benchbox.core.cost.pricing",
            cost_scope="compute_only",
            cost_status="normalized",
            billing_unit="instance_hour",
            pricing_region="us-east-1",
        ).to_dict()
        data["normalized_cost"]["normalized_cost_usd"] = value
        bundle = tmp_path / f"non_finite_{value}.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        transformer = BundleTransformer()
        with pytest.raises(ValueError, match="Invalid normalized_cost_usd"):
            transformer.to_manifest_entry(bundle)

    @pytest.mark.parametrize(
        ("field", "value", "message"),
        [
            ("cost_status", "bogus", "must be one of"),
            ("cost_model_version", "", "must be a non-empty string"),
        ],
    )
    def test_malformed_normalized_cost_rejected_at_ingest(
        self,
        tmp_path: Path,
        field: str,
        value: str,
        message: str,
    ) -> None:
        data = copy.deepcopy(MINIMAL_BUNDLE)
        data["normalized_cost"] = NormalizedCost(
            normalized_cost_usd="0.42",
            cost_model_version="2026.05.0",
            cost_model_source="benchbox.core.cost.pricing",
            cost_scope="compute_only",
            cost_status="normalized",
            billing_unit="instance_hour",
            pricing_region="us-east-1",
        ).to_dict()
        data["normalized_cost"][field] = value
        bundle = tmp_path / f"malformed_{field}.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        transformer = BundleTransformer()
        with pytest.raises(ValueError, match=message):
            transformer.to_manifest_entry(bundle)

    def test_test_type_inferred_from_phases(self, tmp_path: Path) -> None:
        import copy

        data = copy.deepcopy(MINIMAL_BUNDLE)
        del data["benchmark"]["test_type"]
        data["phases"] = {"throughput_test": {"streams": 4}}
        bundle = tmp_path / "phases_throughput.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        transformer = BundleTransformer()
        entry = transformer.to_manifest_entry(bundle)

        assert entry.test_type == "throughput"

    def test_empty_phase_block_does_not_infer_test_type(self, tmp_path: Path) -> None:
        import copy

        data = copy.deepcopy(MINIMAL_BUNDLE)
        del data["benchmark"]["test_type"]
        data["phases"] = {"power_test": {}}
        bundle = tmp_path / "phases_empty.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        transformer = BundleTransformer()
        entry = transformer.to_manifest_entry(bundle)

        assert entry.test_type is None

    def test_not_run_phase_block_does_not_infer_test_type(self, tmp_path: Path) -> None:
        data = copy.deepcopy(MINIMAL_BUNDLE)
        del data["benchmark"]["test_type"]
        data["phases"] = {
            "power_test": {"status": "NOT_RUN"},
            "throughput_test": {"status": "COMPLETED", "streams": 3},
        }
        bundle = tmp_path / "phases_not_run.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        entry = BundleTransformer().to_manifest_entry(bundle)

        assert entry.test_type == "throughput"

    def test_extended_fields_in_detail_result(self, bundle_file: Path) -> None:
        import math

        transformer = BundleTransformer()
        rid = transformer.result_id_from_bundle(bundle_file)
        detail = transformer.to_detail_result(bundle_file, rid)

        expected_geomean = math.exp((math.log(8000.0) + math.log(4000.0)) / 2)
        assert detail.geomean_ms == pytest.approx(expected_geomean)
        assert detail.platform_version == "1.2.0"
        assert detail.test_type == "power"
        assert detail.validation_status == "passed"


class TestQueryTimingExtendedFields:
    def test_run_type_iter_stream_preserved(self, bundle_file: Path) -> None:
        transformer = BundleTransformer()
        rid = transformer.result_id_from_bundle(bundle_file)
        detail = transformer.to_detail_result(bundle_file, rid)

        q1 = next(q for q in detail.queries if q.query_id == "Q1")
        assert q1.run_type == "measurement"
        assert q1.iter == 1
        assert q1.stream == 0

    def test_run_type_none_when_absent(self, tmp_path: Path) -> None:
        data = {**MINIMAL_BUNDLE}
        data["queries"] = [{"id": "Q1", "ms": 500.0, "status": "SUCCESS"}]
        bundle = tmp_path / "no_run_type.json"
        bundle.write_text(json.dumps(data), encoding="utf-8")

        transformer = BundleTransformer()
        rid = transformer.result_id_from_bundle(bundle)
        detail = transformer.to_detail_result(bundle, rid)

        assert len(detail.queries) == 1
        assert detail.queries[0].run_type is None
        assert detail.queries[0].iter is None
        assert detail.queries[0].stream is None


class TestAppliedReceiptCompanion:
    RECEIPT: dict = {
        "platform": "duckdb",
        "corroborated": True,
        "summary": {"corroborated": 1, "total": 1},
        "entries": [
            {
                "statement": "CREATE INDEX idx_l_shipdate ON lineitem(l_shipdate)",
                "phase": "post_load",
                "verdict": "corroborated",
                "kind": "index",
                "table": "lineitem",
                "expected_columns": ["l_shipdate"],
                "observed_columns": ["l_shipdate"],
                "diff": None,
                "reason": None,
            }
        ],
        "observed": [{"name": "idx_l_shipdate"}],
        "error": None,
    }

    def _bundle_with_companion(self, tmp_path: Path, companion_text: str | None) -> Path:
        bundle = tmp_path / "receipted.json"
        bundle.write_text(json.dumps(MINIMAL_BUNDLE), encoding="utf-8")
        if companion_text is not None:
            bundle.with_name("receipted.applied.json").write_text(companion_text, encoding="utf-8")
        return bundle

    def test_receipt_ingested_verbatim_onto_entry_and_detail(self, tmp_path: Path) -> None:
        payload = {
            "status": "applied_verified",
            "applied_ledger_hash": "a" * 64,
            "statements": [{"statement": "CREATE INDEX ...", "status": "applied"}],
            "receipt": self.RECEIPT,
        }
        bundle = self._bundle_with_companion(tmp_path, json.dumps(payload))

        transformer = BundleTransformer()
        entry_receipt = transformer.to_manifest_entry(bundle).applied_receipt
        detail_receipt = transformer.to_detail_result(bundle, result_id="r").applied_receipt

        assert entry_receipt is not None
        assert entry_receipt == detail_receipt
        assert json.loads(entry_receipt) == self.RECEIPT

    def test_receipt_serialization_is_canonical_and_deterministic(self, tmp_path: Path) -> None:
        shuffled = {"entries": [], "corroborated": False, "platform": "duckdb"}
        ordered = {"corroborated": False, "entries": [], "platform": "duckdb"}
        first = self._bundle_with_companion(tmp_path, json.dumps({"receipt": shuffled}))
        transformer = BundleTransformer()
        stored_first = transformer.to_detail_result(first, result_id="r").applied_receipt

        second_dir = tmp_path / "second"
        second_dir.mkdir()
        second = self._bundle_with_companion(second_dir, json.dumps({"receipt": ordered}))
        stored_second = transformer.to_detail_result(second, result_id="r").applied_receipt

        assert stored_first == stored_second
        assert stored_first == '{"corroborated":false,"entries":[],"platform":"duckdb"}'

    def test_oversized_receipt_entries_are_explicitly_truncated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(transformer_module, "APPLIED_RECEIPT_MAX_ENTRIES", 1)
        bundle = self._bundle_with_companion(
            tmp_path,
            json.dumps({"receipt": {"entries": [{"statement": "one"}, {"statement": "two"}]}}),
        )

        stored = BundleTransformer().to_detail_result(bundle, result_id="r").applied_receipt

        assert json.loads(stored or "{}") == {
            "entries": [{"statement": "one"}],
            "original_entry_count": 2,
            "truncated": True,
            "truncation_reason": "entry_limit",
        }

    def test_oversized_companion_bytes_emit_marker_without_reading_payload(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(transformer_module, "APPLIED_COMPANION_MAX_BYTES", 32)
        bundle = self._bundle_with_companion(
            tmp_path,
            json.dumps({"receipt": {"entries": [], "padding": "x" * 64}}),
        )

        stored = BundleTransformer().to_detail_result(bundle, result_id="r").applied_receipt

        marker = json.loads(stored or "{}")
        assert marker["entries"] == []
        assert marker["truncated"] is True
        assert marker["truncation_reason"] == "byte_limit"
        assert marker["original_byte_count"] > 32

    def test_missing_companion_yields_none(self, tmp_path: Path) -> None:
        bundle = self._bundle_with_companion(tmp_path, None)

        transformer = BundleTransformer()
        assert transformer.to_manifest_entry(bundle).applied_receipt is None
        assert transformer.to_detail_result(bundle, result_id="r").applied_receipt is None

    def test_malformed_companion_json_degrades_to_none_without_raising(self, tmp_path: Path) -> None:
        bundle = self._bundle_with_companion(tmp_path, '{"receipt": {"entries": [')

        transformer = BundleTransformer()
        assert transformer.to_manifest_entry(bundle).applied_receipt is None
        assert transformer.to_detail_result(bundle, result_id="r").applied_receipt is None

    def test_companion_without_receipt_key_yields_none(self, tmp_path: Path) -> None:
        payload = {
            "status": "applied_unverified",
            "applied_ledger_hash": "b" * 64,
            "statements": [{"statement": "CREATE INDEX ...", "status": "applied"}],
            "dropped": [],
        }
        bundle = self._bundle_with_companion(tmp_path, json.dumps(payload))

        transformer = BundleTransformer()
        assert transformer.to_detail_result(bundle, result_id="r").applied_receipt is None

    def test_null_receipt_yields_none(self, tmp_path: Path) -> None:
        bundle = self._bundle_with_companion(tmp_path, json.dumps({"receipt": None}))

        transformer = BundleTransformer()
        assert transformer.to_detail_result(bundle, result_id="r").applied_receipt is None

    def test_non_object_companion_payload_yields_none(self, tmp_path: Path) -> None:
        bundle = self._bundle_with_companion(tmp_path, json.dumps(["not", "a", "payload"]))

        transformer = BundleTransformer()
        assert transformer.to_detail_result(bundle, result_id="r").applied_receipt is None

    def test_unreadable_companion_degrades_to_none(self, tmp_path: Path) -> None:
        bundle = self._bundle_with_companion(tmp_path, None)
        bundle.with_name("receipted.applied.json").mkdir()

        transformer = BundleTransformer()
        assert transformer.to_detail_result(bundle, result_id="r").applied_receipt is None


class TestExecutionModeExtraction:
    def test_reads_the_key_path_current_develop_writes(self) -> None:
        bundle = {"platform": {"config": {"execution_mode": "sql"}}}
        assert transformer_module._execution_mode(transformer_module._parse_bundle(bundle)) == "sql"

    def test_reads_legacy_config_mode(self) -> None:
        bundle = {"config": {"mode": "dataframe"}}
        assert transformer_module._execution_mode(transformer_module._parse_bundle(bundle)) == "dataframe"

    def test_documented_schema_location_wins(self) -> None:
        bundle = {
            "config": {"execution_mode": "dataframe", "mode": "sql"},
            "platform": {"config": {"execution_mode": "sql"}},
        }
        assert transformer_module._execution_mode(transformer_module._parse_bundle(bundle)) == "dataframe"

    def test_execution_mode_field_is_not_consulted(self) -> None:
        bundle = {
            "config": {"mode": "dataframe"},
            "execution": {"mode": "sql"},
            "platform": {"config": {"execution_mode": "dataframe"}},
        }
        assert transformer_module._execution_mode(transformer_module._parse_bundle(bundle)) == "dataframe"

    def test_unknown_vocabulary_stays_none(self) -> None:
        assert (
            transformer_module._execution_mode(transformer_module._parse_bundle({"config": {"mode": "balanced"}}))
            is None
        )

    def test_missing_everywhere_stays_none(self) -> None:
        assert transformer_module._execution_mode(transformer_module._parse_bundle({})) is None

    def test_case_is_normalized(self) -> None:
        assert (
            transformer_module._execution_mode(transformer_module._parse_bundle({"config": {"mode": "SQL"}})) == "sql"
        )

    @pytest.mark.parametrize("node", [None, "not-a-dict", 42, []])
    def test_non_dict_nodes_do_not_raise(self, node: object) -> None:
        assert (
            transformer_module._execution_mode(transformer_module._parse_bundle({"config": node, "platform": node}))
            is None
        )


class TestPublishedCorpusResolvesExecutionMode:
    def test_every_published_bundle_resolves(self) -> None:
        corpus = Path(__file__).resolve().parents[4] / "results-data" / "bundles"
        if not corpus.is_dir():
            pytest.skip("results-data/bundles not present in this checkout")

        unresolved = []
        total = 0
        for path in sorted(corpus.rglob("*.json")):
            if not is_primary_bundle_file(path):
                continue
            total += 1
            data = json.loads(path.read_text(encoding="utf-8"))
            if transformer_module._execution_mode(transformer_module._parse_bundle(data)) is None:
                unresolved.append(path.name)

        assert total > 0, "no bundles discovered"
        assert not unresolved, f"{len(unresolved)} of {total} bundles yield a NULL execution_mode: {unresolved[:5]}"

    def test_resolved_mode_agrees_with_the_filename_suffix(self) -> None:
        corpus = Path(__file__).resolve().parents[4] / "results-data" / "bundles"
        if not corpus.is_dir():
            pytest.skip("results-data/bundles not present in this checkout")

        mismatches = []
        for path in sorted(corpus.rglob("*.json")):
            if not is_primary_bundle_file(path):
                continue
            if "_df_" in path.name:
                expected = "dataframe"
            elif "_sql_" in path.name:
                expected = "sql"
            else:
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            actual = transformer_module._execution_mode(transformer_module._parse_bundle(data))
            if actual != expected:
                mismatches.append((path.name, expected, actual))

        assert not mismatches, f"filename suffix disagrees with resolved mode: {mismatches[:5]}"


class TestClientLinkProducerShape:
    def test_producer_shaped_client_link_projects(self, bundle_file: Path) -> None:
        transformer = BundleTransformer()
        rid = transformer.result_id_from_bundle(bundle_file)
        data = copy.deepcopy(MINIMAL_BUNDLE)
        data["environment"]["client_link"] = {
            "collection_status": "available",
            "source": "observed",
            "client_region": "us-east-1",
            "client_cloud": "aws",
            "statement_overhead_ms": {"samples": 5, "min": 1.42, "median": 1.68},
        }
        detail = transformer.to_detail_result(bundle_file, rid, data=data)

        assert detail.environment.client_region == "us-east-1"
        assert detail.environment.client_cloud == "aws"
        assert detail.environment.link_status == "available"
        assert detail.environment.statement_overhead_min_ms == pytest.approx(1.42)
        assert detail.environment.statement_overhead_median_ms == pytest.approx(1.68)

    def test_missing_client_link_projects_nulls(self, bundle_file: Path) -> None:
        transformer = BundleTransformer()
        rid = transformer.result_id_from_bundle(bundle_file)
        detail = transformer.to_detail_result(bundle_file, rid)

        assert detail.environment.client_region is None
        assert detail.environment.link_status is None
        assert detail.environment.statement_overhead_min_ms is None
        assert detail.environment.statement_overhead_median_ms is None

    def test_remote_host_endpoint_classifies_remote(self) -> None:
        data = copy.deepcopy(MINIMAL_BUNDLE)
        data["platform"]["deployment"] = {"endpoint_class": "remote_host"}
        assert transformer_module._deployment_class_from_contract(transformer_module._parse_bundle(data)) == "remote"

    def test_cloud_endpoint_still_classifies_cloud(self) -> None:
        data = copy.deepcopy(MINIMAL_BUNDLE)
        data["platform"]["deployment"] = {"endpoint_class": "cloud_endpoint"}
        assert transformer_module._deployment_class_from_contract(transformer_module._parse_bundle(data)) == "cloud"


class TestKnownDefectExclusion:
    def _write_sidecar(self, bundle_file: Path, payload: object) -> None:
        sidecar = bundle_file.parent / f"{bundle_file.stem}.manifest.json"
        if isinstance(payload, str):
            sidecar.write_text(payload, encoding="utf-8")
        else:
            sidecar.write_text(json.dumps(payload), encoding="utf-8")

    def test_known_defects_exclude_manifest_entry(self, bundle_file: Path) -> None:
        self._write_sidecar(bundle_file, {"known_defects": ["defective-macdbgen-addresses"]})
        entry = BundleTransformer().to_manifest_entry(bundle_file)

        assert entry.ranking_exclusion_reason == "known_defective_data"

    def test_known_defects_exclude_detail_result(self, bundle_file: Path) -> None:
        self._write_sidecar(bundle_file, {"known_defects": ["defective-macdbgen-addresses"]})
        transformer = BundleTransformer()
        rid = transformer.result_id_from_bundle(bundle_file)
        detail = transformer.to_detail_result(bundle_file, rid)

        assert detail.ranking_exclusion_reason == "known_defective_data"

    def test_unlisted_defect_id_still_excludes(self, bundle_file: Path) -> None:
        self._write_sidecar(bundle_file, {"known_defects": ["future-defect"]})
        entry = BundleTransformer().to_manifest_entry(bundle_file)

        assert entry.ranking_exclusion_reason == "known_defective_data"

    def test_absent_sidecar_preserves_baseline(self, bundle_file: Path) -> None:
        baseline = BundleTransformer().to_manifest_entry(bundle_file).ranking_exclusion_reason
        self._write_sidecar(bundle_file, {"result_source": "internal"})
        entry = BundleTransformer().to_manifest_entry(bundle_file)

        assert entry.ranking_exclusion_reason == baseline

    def test_empty_known_defects_preserves_baseline(self, bundle_file: Path) -> None:
        baseline = BundleTransformer().to_manifest_entry(bundle_file).ranking_exclusion_reason
        self._write_sidecar(bundle_file, {"known_defects": []})
        entry = BundleTransformer().to_manifest_entry(bundle_file)

        assert entry.ranking_exclusion_reason == baseline

    def test_malformed_sidecar_preserves_baseline(self, bundle_file: Path) -> None:
        baseline = BundleTransformer().to_manifest_entry(bundle_file).ranking_exclusion_reason
        self._write_sidecar(bundle_file, "{not json")
        entry = BundleTransformer().to_manifest_entry(bundle_file)

        assert entry.ranking_exclusion_reason == baseline

    def test_non_dict_sidecar_preserves_baseline(self, bundle_file: Path) -> None:
        baseline = BundleTransformer().to_manifest_entry(bundle_file).ranking_exclusion_reason
        self._write_sidecar(bundle_file, [1, 2, 3])
        entry = BundleTransformer().to_manifest_entry(bundle_file)

        assert entry.ranking_exclusion_reason == baseline

    def test_non_list_known_defects_preserves_baseline(self, bundle_file: Path) -> None:
        baseline = BundleTransformer().to_manifest_entry(bundle_file).ranking_exclusion_reason
        self._write_sidecar(bundle_file, {"known_defects": "defective-macdbgen-addresses"})
        entry = BundleTransformer().to_manifest_entry(bundle_file)

        assert entry.ranking_exclusion_reason == baseline

    def test_non_string_entries_are_ignored(self, bundle_file: Path) -> None:
        baseline = BundleTransformer().to_manifest_entry(bundle_file).ranking_exclusion_reason
        self._write_sidecar(bundle_file, {"known_defects": [123, None]})
        entry = BundleTransformer().to_manifest_entry(bundle_file)

        assert entry.ranking_exclusion_reason == baseline

    def test_known_defects_take_precedence_over_other_reasons(self, bundle_file: Path) -> None:
        data = copy.deepcopy(MINIMAL_BUNDLE)
        data["summary"]["queries"]["failed"] = 1
        bundle_file.write_text(json.dumps(data), encoding="utf-8")
        assert BundleTransformer().to_manifest_entry(bundle_file).ranking_exclusion_reason == "failed_queries"
        self._write_sidecar(bundle_file, {"known_defects": ["defective-macdbgen-addresses"]})
        entry = BundleTransformer().to_manifest_entry(bundle_file)

        assert entry.ranking_exclusion_reason == "known_defective_data"


class TestTemplateEvidence:
    def _write_bundle(self, bundle_file: Path, tuning: dict | None) -> None:
        data = copy.deepcopy(MINIMAL_BUNDLE)
        if tuning is None:
            data["platform"].pop("tuning", None)
        else:
            data["platform"]["tuning"] = tuning
        bundle_file.write_text(json.dumps(data), encoding="utf-8")

    def test_template_evidence_flows_to_manifest_and_detail(self, bundle_file: Path) -> None:
        self._write_bundle(bundle_file, {"template_evidence": "unmeasured"})
        transformer = BundleTransformer()
        rid = transformer.result_id_from_bundle(bundle_file)

        assert transformer.to_manifest_entry(bundle_file).template_evidence == "unmeasured"
        assert transformer.to_detail_result(bundle_file, rid).template_evidence == "unmeasured"

    def test_template_evidence_absent_without_tuning_block(self, bundle_file: Path) -> None:
        self._write_bundle(bundle_file, None)
        transformer = BundleTransformer()
        rid = transformer.result_id_from_bundle(bundle_file)

        assert transformer.to_manifest_entry(bundle_file).template_evidence is None
        assert transformer.to_detail_result(bundle_file, rid).template_evidence is None
