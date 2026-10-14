# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for
# details.

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchbox.core.results import exporter as exporter_module
from benchbox.core.results.exporter import ResultExporter
from benchbox.core.results.loader import load_result_file
from benchbox.core.results.result_factory import build_enhanced_benchmark_result

pytestmark = [pytest.mark.unit, pytest.mark.fast]

_REQUESTED_TUNING = {
    "primary_keys": {"enabled": True, "enforce_uniqueness": True},
    "table_tunings": {
        "LINEITEM": {
            "table_name": "LINEITEM",
            "sorting": [{"name": "l_orderkey", "order": 1, "type": "INTEGER"}],
        }
    },
}

_APPLIED_LEDGER = {
    "status": "applied_unverified",
    "applied_ledger_hash": "f" * 64,
    "statements": [
        {
            "statement": "CREATE INDEX idx_l_orderkey ON LINEITEM (l_orderkey)",
            "phase": "ddl",
            "status": "executed",
            "mechanism": "sort_index",
        }
    ],
    "dropped": [{"intent": "partitioning:LINEITEM", "reason": "load-time only"}],
    "receipt": {
        "platform": "duckdb",
        "corroborated": False,
        "summary": {"mismatch": 1, "gate_relevant_total": 1},
        "entries": [
            {
                "kind": "index",
                "phase": "ddl",
                "verdict": "mismatch",
                "statement": "CREATE INDEX idx_l_orderkey ON LINEITEM (l_orderkey)",
                "table": "main.LINEITEM",
            }
        ],
    },
}


def _tuned_result() -> object:
    benchmark = SimpleNamespace(benchmark_name="tpch", scale_factor=0.01, compliance_class=None)
    return build_enhanced_benchmark_result(
        benchmark=benchmark,
        platform="duckdb",
        query_results=[],
        tunings_applied=dict(_REQUESTED_TUNING),
        tuning_config_hash="a" * 64,
        tuning_source="auto_discovered",
        tuning_source_file="examples/tunings/duckdb/tpch_tuned.yaml",
        tuning_validation_status="applied_unverified",
        applied_tuning_ledger=dict(_APPLIED_LEDGER),
        applied_ledger_hash=_APPLIED_LEDGER["applied_ledger_hash"],
    )


def _export(tmp_path: Path, *, anonymize: bool) -> tuple[dict, dict, dict]:
    exporter = ResultExporter(output_dir=tmp_path, anonymize=anonymize)
    result = _tuned_result()
    result.output_filename = "run.json"
    tuning = exporter._build_export_tuning_payload(result) or {}
    applied = exporter._build_export_applied_payload(result) or {}
    exporter.export_result(result, formats=["json"])

    bundle = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    return bundle, tuning, applied


def _write_legacy_companions(tmp_path: Path, tuning: dict | None, applied: dict | None) -> None:
    if tuning is not None:
        (tmp_path / "run.tuning.json").write_text(json.dumps(tuning), encoding="utf-8")
    if applied is not None:
        (tmp_path / "run.applied.json").write_text(json.dumps(applied), encoding="utf-8")


@pytest.mark.parametrize("anonymize", [True, False])
class TestInlinedBlockMatchesTheScrubbedPayloads:
    def test_requested_configuration_is_inlined(self, tmp_path: Path, anonymize: bool) -> None:
        bundle, tuning, _applied = _export(tmp_path, anonymize=anonymize)

        inlined = bundle["platform"]["tuning"]["requested"]
        assert inlined == tuning["requested"]
        assert bundle["platform"]["tuning"]["source_file"] == tuning["source_file"]

    def test_applied_ledger_is_inlined(self, tmp_path: Path, anonymize: bool) -> None:
        bundle, _tuning, applied = _export(tmp_path, anonymize=anonymize)

        inlined = bundle["platform"]["tuning"]["applied"]
        assert inlined == {k: v for k, v in applied.items() if k != "applied_ledger_hash"}
        assert bundle["platform"]["tuning"]["applied_ledger_hash"] == applied["applied_ledger_hash"]

    def test_hashes_are_not_duplicated_inside_the_sub_blocks(self, tmp_path: Path, anonymize: bool) -> None:
        bundle, _tuning, _applied = _export(tmp_path, anonymize=anonymize)

        tuning_block = bundle["platform"]["tuning"]
        assert "applied_ledger_hash" not in tuning_block["applied"]
        assert "requested_config_hash" not in tuning_block["requested"]
        assert "validation_status" not in tuning_block["requested"]
        assert "version" not in tuning_block["requested"]
        assert "run_id" not in tuning_block["requested"]


class TestAnonymizedInliningKeepsRedaction:
    def test_public_bundle_never_inlines_raw_ddl_or_identifiers(self, tmp_path: Path) -> None:
        bundle, _tuning, applied = _export(tmp_path, anonymize=True)
        raw_bundle_text = (tmp_path / "run.json").read_text(encoding="utf-8")

        applied_block = bundle["platform"]["tuning"]["applied"]
        assert applied_block["statements"][0]["statement_redacted"] is True
        assert "statement" not in applied_block["statements"][0]
        assert applied_block["receipt"]["entries"][0]["statement_redacted"] is True
        assert "table" not in applied_block["receipt"]["entries"][0]
        assert applied_block["dropped"] == [{"redacted": True}]

        assert "CREATE INDEX" not in raw_bundle_text
        assert "main.LINEITEM" not in raw_bundle_text
        assert "load-time only" not in raw_bundle_text
        assert "statement" not in applied["statements"][0]

    def test_private_bundle_keeps_the_statements_like_the_companion(self, tmp_path: Path) -> None:
        bundle, _tuning, applied = _export(tmp_path, anonymize=False)

        applied_block = bundle["platform"]["tuning"]["applied"]
        assert applied_block["statements"][0]["statement"] == applied["statements"][0]["statement"]
        assert bundle["export"]["anonymized"] is False


class TestAppliedLedgerWithoutRequestedTuning:
    def test_ledger_hash_survives_when_there_is_no_tuning_summary(self, tmp_path: Path) -> None:
        benchmark = SimpleNamespace(benchmark_name="tpch", scale_factor=0.01, compliance_class=None)
        result = build_enhanced_benchmark_result(
            benchmark=benchmark,
            platform="duckdb",
            query_results=[],
            applied_tuning_ledger=dict(_APPLIED_LEDGER),
            applied_ledger_hash=_APPLIED_LEDGER["applied_ledger_hash"],
        )
        result.output_filename = "run.json"
        ResultExporter(output_dir=tmp_path, anonymize=False).export_result(result, formats=["json"])

        bundle = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
        tuning_block = bundle["platform"]["tuning"]
        assert tuning_block["applied_ledger_hash"] == _APPLIED_LEDGER["applied_ledger_hash"]
        assert tuning_block["applied"]["status"] == "applied_unverified"

        assert not (tmp_path / "run.applied.json").exists()
        loaded, _raw = load_result_file(tmp_path / "run.json")
        assert loaded.applied_ledger_hash == _APPLIED_LEDGER["applied_ledger_hash"]
        assert loaded.applied_tuning_ledger is not None


class TestLoaderReadsTheBundleAlone:
    def test_bundle_without_companions_reconstructs_its_tuning(self, tmp_path: Path) -> None:
        _export(tmp_path, anonymize=False)
        assert not (tmp_path / "run.tuning.json").exists()
        assert not (tmp_path / "run.applied.json").exists()

        result, _raw = load_result_file(tmp_path / "run.json")

        assert result.tuning_config_hash == "a" * 64
        assert result.tuning_source == "auto_discovered"
        assert result.tuning_source_file == "examples/tunings/duckdb/tpch_tuned.yaml"
        assert result.tuning_validation_status == "applied_unverified"
        assert result.applied_ledger_hash == _APPLIED_LEDGER["applied_ledger_hash"]
        assert result.applied_tuning_ledger is not None
        assert result.applied_tuning_ledger["status"] == "applied_unverified"
        assert result.applied_tuning_ledger["applied_ledger_hash"] == _APPLIED_LEDGER["applied_ledger_hash"]
        assert result.tunings_applied is not None
        assert result.tunings_applied["table_tunings"]["LINEITEM"]["sorting"][0]["name"] == "l_orderkey"

    def test_companion_still_wins_for_a_legacy_bundle(self, tmp_path: Path) -> None:
        _bundle, tuning, applied = _export(tmp_path, anonymize=False)
        bundle_path = tmp_path / "run.json"
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))

        del bundle["platform"]["tuning"]["requested"]
        del bundle["platform"]["tuning"]["applied"]
        bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
        _write_legacy_companions(tmp_path, tuning=tuning, applied=applied)

        result, _raw = load_result_file(bundle_path)

        assert result.applied_tuning_ledger is not None
        assert result.applied_tuning_ledger["statements"][0]["phase"] == "ddl"
        assert result.tunings_applied is not None
        assert "table_tunings" in result.tunings_applied

    def test_round_trip_export_preserves_the_inlined_tuning(self, tmp_path: Path) -> None:
        _export(tmp_path, anonymize=False)
        result, _raw = load_result_file(tmp_path / "run.json")

        second = tmp_path / "again"
        second.mkdir()
        result.output_filename = "run.json"
        ResultExporter(output_dir=second, anonymize=False).export_result(result, formats=["json"])

        reexported = json.loads((second / "run.json").read_text(encoding="utf-8"))
        tuning_block = reexported["platform"]["tuning"]
        assert tuning_block["requested_config_hash"] == "a" * 64
        assert tuning_block["applied_ledger_hash"] == _APPLIED_LEDGER["applied_ledger_hash"]
        assert tuning_block["requested"]["table_tunings"]["LINEITEM"]["sorting"][0]["name"] == "l_orderkey"
        assert tuning_block["applied"]["statements"][0]["phase"] == "ddl"


class TestCompanionWinsFieldByField:
    def test_a_minimal_companion_does_not_wipe_inlined_fields(self, tmp_path: Path) -> None:
        _export(tmp_path, anonymize=False)
        _write_legacy_companions(
            tmp_path,
            tuning={"requested": {"table_tunings": {"LINEITEM": {"table_name": "LINEITEM"}}}},
            applied=None,
        )

        result, _raw = load_result_file(tmp_path / "run.json")

        assert result.tuning_source_file == "examples/tunings/duckdb/tpch_tuned.yaml"
        assert result.tuning_validation_status == "applied_unverified"
        assert result.tuning_config_hash == "a" * 64
        assert result.tuning_source == "auto_discovered"
        assert result.tunings_applied is not None
        assert "LINEITEM" in result.tunings_applied["table_tunings"]


class TestBareStringIdentifiersAreHashed:
    def test_string_clause_values_do_not_reach_a_public_bundle(self, tmp_path: Path) -> None:
        benchmark = SimpleNamespace(benchmark_name="tpch", scale_factor=0.01, compliance_class=None)
        result = build_enhanced_benchmark_result(
            benchmark=benchmark,
            platform="duckdb",
            query_results=[],
            tunings_applied={
                "table_tunings": {
                    "acme_orders": {
                        "table_name": "acme_orders",
                        "clustering": ["customer_ssn_column"],
                        "partitioning": "revenue_bucket_column",
                    }
                }
            },
            tuning_config_hash="c" * 64,
            tuning_source="explicit_file",
        )
        result.output_filename = "run.json"
        ResultExporter(output_dir=tmp_path, anonymize=True).export_result(result, formats=["json"])

        bundle_text = (tmp_path / "run.json").read_text(encoding="utf-8")

        for identifier in ("acme_orders", "customer_ssn_column", "revenue_bucket_column"):
            assert identifier not in bundle_text, f"{identifier} leaked into the public bundle"

        requested = json.loads(bundle_text)["platform"]["tuning"]["requested"]
        clause = next(iter(requested["table_tunings"].values()))
        assert clause["clustering"][0].startswith("column_")
        assert clause["partitioning"].startswith("column_")

    def test_non_identifier_clause_values_are_left_alone(self, tmp_path: Path) -> None:
        bundle, _tuning, _applied = _export(tmp_path, anonymize=True)

        sorting = next(iter(bundle["platform"]["tuning"]["requested"]["table_tunings"].values()))["sorting"][0]
        assert sorting["type"] == "INTEGER"
        assert sorting["order"] == 1


class TestCompanionsAreRetired:
    @pytest.mark.parametrize("anonymize", [True, False])
    def test_export_writes_no_tuning_or_applied_companion(self, tmp_path: Path, anonymize: bool) -> None:
        _export(tmp_path, anonymize=anonymize)

        written = sorted(path.name for path in tmp_path.iterdir())
        assert written == ["run.json"]

    def test_plans_companion_is_still_written(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(exporter_module, "build_plans_payload", lambda _result: {"queries": {"1": {}}})
        result = _tuned_result()
        result.output_filename = "run.json"
        ResultExporter(output_dir=tmp_path, anonymize=False).export_result(result, formats=["json"])

        assert (tmp_path / "run.plans.json").is_file()
        assert not (tmp_path / "run.tuning.json").exists()
        assert not (tmp_path / "run.applied.json").exists()


class TestInlinedSourceFileKeepsItsPublicForm:
    def test_repo_relative_reference_survives_public_export(self, tmp_path: Path) -> None:
        bundle, _tuning, _applied = _export(tmp_path, anonymize=True)

        assert bundle["platform"]["tuning"]["source_file"] == "examples/tunings/duckdb/tpch_tuned.yaml"

    def test_public_export_is_a_fixed_point_for_the_reference(self, tmp_path: Path) -> None:
        from benchbox.core.results.anonymization import AnonymizationConfig, AnonymizationManager

        bundle, _tuning, _applied = _export(tmp_path, anonymize=True)
        manager = AnonymizationManager(AnonymizationConfig.from_public_environ())

        assert manager.anonymize_result_payload(bundle) == bundle

    def test_an_absolute_path_is_still_hashed(self, tmp_path: Path) -> None:
        benchmark = SimpleNamespace(benchmark_name="tpch", scale_factor=0.01, compliance_class=None)
        result = build_enhanced_benchmark_result(
            benchmark=benchmark,
            platform="duckdb",
            query_results=[],
            tunings_applied={"primary_keys": {"enabled": True}},
            tuning_config_hash="a" * 64,
            tuning_source="explicit_file",
            tuning_source_file="/Users/alice/private/custom.yaml",
        )
        result.output_filename = "run.json"
        ResultExporter(output_dir=tmp_path, anonymize=True).export_result(result, formats=["json"])

        raw = (tmp_path / "run.json").read_text(encoding="utf-8")
        assert "alice" not in raw
        assert json.loads(raw)["platform"]["tuning"]["source_file"].startswith("path_")


_SESSION_ONLY_LEDGER = {
    "status": "not_applicable",
    "applied_ledger_hash": "e" * 64,
    "statements": [
        {
            "statement": "PRAGMA journal_mode = WAL",
            "phase": "session",
            "status": "executed",
            "mechanism": "session_setting",
        }
    ],
    "dropped": [],
}


def _export_untuned(tmp_path: Path, *, anonymize: bool, **overrides: object) -> dict:
    benchmark = SimpleNamespace(benchmark_name="tpch", scale_factor=0.01, compliance_class=None)
    result = build_enhanced_benchmark_result(
        benchmark=benchmark,
        platform=str(overrides.pop("platform", "duckdb")),
        query_results=[],
        **overrides,
    )
    result.output_filename = "run.json"
    ResultExporter(output_dir=tmp_path, anonymize=anonymize).export_result(result, formats=["json"])
    return json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("anonymize", [True, False])
class TestUntunedBundleStatesItsValidationStatus:
    def test_run_without_a_ledger_carries_only_not_applicable(self, tmp_path: Path, anonymize: bool) -> None:
        bundle = _export_untuned(tmp_path, anonymize=anonymize, tuning_validation_status="not_applicable")

        assert bundle["platform"]["tuning"] == {"validation_status": "not_applicable"}

    def test_unset_status_is_reported_as_not_applicable(self, tmp_path: Path, anonymize: bool) -> None:
        bundle = _export_untuned(tmp_path, anonymize=anonymize)

        assert bundle["platform"]["tuning"] == {"validation_status": "not_applicable"}

    def test_status_agrees_with_the_applied_ledger_of_a_session_only_run(self, tmp_path: Path, anonymize: bool) -> None:
        bundle = _export_untuned(
            tmp_path,
            anonymize=anonymize,
            platform="sqlite",
            tuning_validation_status="not_applicable",
            applied_tuning_ledger=dict(_SESSION_ONLY_LEDGER),
            applied_ledger_hash=_SESSION_ONLY_LEDGER["applied_ledger_hash"],
        )

        tuning_block = bundle["platform"]["tuning"]
        assert tuning_block["validation_status"] == "not_applicable"
        assert tuning_block["validation_status"] == tuning_block["applied"]["status"]

    def test_unset_status_takes_the_applied_ledgers_status(self, tmp_path: Path, anonymize: bool) -> None:
        bundle = _export_untuned(
            tmp_path,
            anonymize=anonymize,
            platform="sqlite",
            applied_tuning_ledger={**_SESSION_ONLY_LEDGER, "status": "noop"},
            applied_ledger_hash=_SESSION_ONLY_LEDGER["applied_ledger_hash"],
        )

        tuning_block = bundle["platform"]["tuning"]
        assert tuning_block["validation_status"] == "noop"
        assert tuning_block["applied"]["status"] == "noop"


class TestUntunedStatusRoundTrips:
    def test_loader_reads_the_untuned_status_back(self, tmp_path: Path) -> None:
        _export_untuned(tmp_path, anonymize=False, tuning_validation_status="not_applicable")

        result, _raw = load_result_file(tmp_path / "run.json")

        assert result.tuning_validation_status == "not_applicable"
        assert not result.tunings_applied

    def test_reexport_keeps_the_block_unchanged(self, tmp_path: Path) -> None:
        first = _export_untuned(tmp_path, anonymize=False, tuning_validation_status="not_applicable")
        result, _raw = load_result_file(tmp_path / "run.json")

        second_dir = tmp_path / "again"
        second_dir.mkdir()
        result.output_filename = "run.json"
        ResultExporter(output_dir=second_dir, anonymize=False).export_result(result, formats=["json"])

        again = json.loads((second_dir / "run.json").read_text(encoding="utf-8"))
        assert again["platform"]["tuning"] == first["platform"]["tuning"]

    def test_tuned_bundle_still_carries_its_full_block(self, tmp_path: Path) -> None:
        bundle, _tuning, _applied = _export(tmp_path, anonymize=False)

        tuning_block = bundle["platform"]["tuning"]
        assert tuning_block["validation_status"] == "applied_unverified"
        assert tuning_block["requested_config_hash"] == "a" * 64


def _reexport_legacy_bundle(tmp_path: Path, tuning_block: dict | None) -> dict:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    bundle = _export_untuned(source_dir, anonymize=False, tuning_validation_status="not_applicable")
    if tuning_block is None:
        bundle["platform"].pop("tuning", None)
    else:
        bundle["platform"]["tuning"] = tuning_block
    (source_dir / "run.json").write_text(json.dumps(bundle), encoding="utf-8")

    result, _raw = load_result_file(source_dir / "run.json")
    recorded = (tuning_block or {}).get("validation_status")
    assert result.tuning_validation_status == (None if recorded == "not_validated" else recorded)

    again_dir = tmp_path / "again"
    again_dir.mkdir()
    result.output_filename = "run.json"
    ResultExporter(output_dir=again_dir, anonymize=False).export_result(result, formats=["json"])
    return json.loads((again_dir / "run.json").read_text(encoding="utf-8"))


class TestReexportedLegacyBundleInventsNoStatus:
    def test_bundle_without_a_tuning_block_stays_without_one(self, tmp_path: Path) -> None:
        again = _reexport_legacy_bundle(tmp_path, None)

        assert "tuning" not in again["platform"]

    def test_bundle_with_only_source_and_hash_keeps_them_and_gains_no_status(self, tmp_path: Path) -> None:
        again = _reexport_legacy_bundle(tmp_path, {"source": "yaml", "hash": "a" * 64})

        tuning_block = again["platform"]["tuning"]
        assert "validation_status" not in tuning_block
        assert tuning_block["source"] == "yaml"
        assert tuning_block["hash"] == "a" * 64
        assert tuning_block["requested_config_hash"] == "a" * 64

    def test_bundle_with_only_auto_source_keeps_source_and_gains_no_hash(self, tmp_path: Path) -> None:
        again = _reexport_legacy_bundle(tmp_path, {"source": "auto"})

        tuning_block = again["platform"]["tuning"]
        assert tuning_block == {"source": "auto"}

    def test_bundle_with_an_empty_tuning_block_gains_no_status(self, tmp_path: Path) -> None:
        again = _reexport_legacy_bundle(tmp_path, {})

        assert "validation_status" not in again["platform"].get("tuning", {})

    def test_bundle_that_recorded_not_validated_keeps_its_evidence_and_gains_no_status(self, tmp_path: Path) -> None:
        again = _reexport_legacy_bundle(
            tmp_path,
            {"source": "yaml", "hash": "a" * 64, "validation_status": "not_validated", "counts": {"tables_tuned": 1}},
        )

        tuning_block = again["platform"]["tuning"]
        assert "validation_status" not in tuning_block
        assert tuning_block["source"] == "yaml"
        assert tuning_block["hash"] == "a" * 64
        assert tuning_block["requested_config_hash"] == "a" * 64

    def test_bundle_that_recorded_only_not_validated_gains_no_status(self, tmp_path: Path) -> None:
        again = _reexport_legacy_bundle(tmp_path, {"validation_status": "not_validated"})

        assert "validation_status" not in again["platform"].get("tuning", {})

    def test_status_recorded_by_the_original_run_survives(self, tmp_path: Path) -> None:
        again = _reexport_legacy_bundle(tmp_path, {"validation_status": "noop"})

        assert again["platform"]["tuning"] == {"validation_status": "noop"}


class TestTunedBundleDerivesStatusLikeUntuned:
    def _tuned(self, tmp_path: Path, **overrides: object) -> dict:
        benchmark = SimpleNamespace(benchmark_name="tpch", scale_factor=0.01, compliance_class=None)
        result = build_enhanced_benchmark_result(
            benchmark=benchmark,
            platform="duckdb",
            query_results=[],
            tunings_applied=dict(_REQUESTED_TUNING),
            tuning_config_hash="a" * 64,
            tuning_source="auto_discovered",
            **overrides,
        )
        result.output_filename = "run.json"
        ResultExporter(output_dir=tmp_path, anonymize=False).export_result(result, formats=["json"])
        return json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))["platform"]["tuning"]

    def test_unset_status_takes_the_applied_ledgers_status(self, tmp_path: Path) -> None:
        tuning_block = self._tuned(
            tmp_path,
            applied_tuning_ledger=dict(_APPLIED_LEDGER),
            applied_ledger_hash=_APPLIED_LEDGER["applied_ledger_hash"],
        )

        assert tuning_block["validation_status"] == "applied_unverified"

    def test_unset_status_without_a_ledger_is_omitted_rather_than_not_validated(self, tmp_path: Path) -> None:
        tuning_block = self._tuned(tmp_path)

        assert "validation_status" not in tuning_block


class TestStatusDerivationIgnoresInternalSentinelsAndSparseResults:
    def test_ledger_status_of_not_validated_is_not_exported(self) -> None:
        from benchbox.core.results.schema import _derived_validation_status

        result = SimpleNamespace(tuning_validation_status=None, applied_tuning_ledger={"status": "not_validated"})

        assert _derived_validation_status(result) is None

    def test_result_object_without_source_or_hash_attributes_does_not_raise(self) -> None:
        from benchbox.core.results.schema import _untuned_tuning_summary

        result = SimpleNamespace(tuning_validation_status=None, tuning_source=None, applied_tuning_ledger=None)

        assert _untuned_tuning_summary(result) == {}

    def test_baseline_source_with_derived_status_yields_both_keys(self) -> None:
        from benchbox.core.results.schema import _untuned_tuning_summary

        result = SimpleNamespace(
            tuning_validation_status="not_applicable",
            tuning_source="baseline",
            applied_tuning_ledger=None,
        )

        summary = _untuned_tuning_summary(result)

        assert summary["tuning_source"] == "baseline"
        assert summary["validation_status"] == "not_applicable"

    def test_tuning_companion_payload_omits_the_sentinel_status(self) -> None:
        from benchbox.core.results.schema import build_tuning_payload

        result = build_enhanced_benchmark_result(
            benchmark=SimpleNamespace(benchmark_name="tpch", scale_factor=0.01, compliance_class=None),
            platform="duckdb",
            query_results=[],
            tunings_applied=dict(_REQUESTED_TUNING),
            tuning_config_hash="a" * 64,
        )

        payload = build_tuning_payload(result)

        assert payload is not None
        assert "validation_status" not in payload
