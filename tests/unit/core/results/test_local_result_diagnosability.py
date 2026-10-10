from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from benchbox.core.results.exporter import ResultExporter
from benchbox.core.results.tuning_summary import (
    TuningVerificationSummary,
    format_tuning_verification,
    summarize_tuning_verification,
)
from benchbox.validation.bundle import unanonymized_tuning_findings, validate_bundles
from tests.fixtures.result_dict_fixtures import make_benchmark_results

pytestmark = [pytest.mark.unit, pytest.mark.fast]

_PROBE_STATEMENT = "CREATE INDEX idx_probe ON LINEITEM (l_orderkey)"
_PROBE_REASON = "no catalog footprint for sort-only rewrite"
_PROBE_ENTRY_ERROR = "dial tcp db.internal:5432: connect: connection refused"
_PROBE_USER = "diagnosability-probe-user"
_PROBE_DROP_INTENT = "partitioning:LINEITEM"
_PROBE_DROP_REASON = "platform renders no partitioning clause"


def _applied_ledger() -> dict:
    return {
        "status": "applied_unverified",
        "applied_ledger_hash": "a" * 64,
        "statements": [
            {"statement": _PROBE_STATEMENT, "phase": "ddl", "status": "executed"},
        ],
        "dropped": [{"intent": _PROBE_DROP_INTENT, "reason": _PROBE_DROP_REASON}],
        "receipt": {
            "platform": "duckdb",
            "corroborated": False,
            "summary": {"unverifiable": 1, "verifiable_total": 1},
            "entries": [
                {
                    "phase": "ddl",
                    "verdict": "unverifiable",
                    "reason": _PROBE_REASON,
                    "statement": _PROBE_STATEMENT,
                    "error": _PROBE_ENTRY_ERROR,
                },
            ],
        },
    }


def _tuned_result():
    return make_benchmark_results(
        benchmark_name="TPC-H",
        platform="duckdb",
        execution_id="diag-probe-exec",
        timestamp=datetime(2026, 10, 9, 12, 0, 0),
        duration_seconds=3.0,
        total_queries=1,
        successful_queries=1,
        query_results=[
            {"query_id": "Q1", "execution_time": 1.0, "status": "SUCCESS", "rows_returned": 4},
        ],
        validation_status="PASSED",
        validation_details={},
        applied_tuning_ledger=_applied_ledger(),
        platform_raw_config={"username": _PROBE_USER, "host": "db.internal"},
    )


def _exported_payload(tmp_path: Path, *, anonymize: bool) -> dict:
    result = _tuned_result()
    exported = ResultExporter(output_dir=tmp_path, anonymize=anonymize).export_result(result, ["json"])
    return json.loads(exported["json"].read_text(encoding="utf-8"))


def _applied_block(payload: dict) -> dict:
    return payload["platform"]["tuning"]["applied"]


class TestLocalResultKeepsDiagnostics:
    def test_default_export_is_unanonymized(self, tmp_path):
        assert ResultExporter(output_dir=tmp_path).anonymize is False

    def test_local_result_contains_receipt_statements_and_reasons(self, tmp_path):
        payload = _exported_payload(tmp_path, anonymize=False)

        assert payload["export"]["anonymized"] is False
        applied = _applied_block(payload)
        assert applied["statements"][0]["statement"] == _PROBE_STATEMENT
        assert applied["receipt"]["entries"][0]["reason"] == _PROBE_REASON
        assert applied["receipt"]["entries"][0]["statement"] == _PROBE_STATEMENT
        assert applied["receipt"]["entries"][0]["error"] == _PROBE_ENTRY_ERROR
        assert applied["dropped"] == [{"intent": _PROBE_DROP_INTENT, "reason": _PROBE_DROP_REASON}]

    def test_local_result_keeps_username_redaction(self, tmp_path):
        payload = _exported_payload(tmp_path, anonymize=False)

        serialized = json.dumps(payload)
        assert _PROBE_USER not in serialized


class TestOutwardPathsStayRedacted:
    def test_anonymized_export_scrubs_statements_and_reasons(self, tmp_path):
        payload = _exported_payload(tmp_path, anonymize=True)

        assert payload["export"]["anonymized"] is True
        applied = _applied_block(payload)
        assert all("statement" not in entry for entry in applied["statements"])
        assert all(entry.get("statement_redacted") is True for entry in applied["statements"])
        assert all("reason" not in entry for entry in applied["receipt"]["entries"])
        assert all(entry.get("reason_redacted") is True for entry in applied["receipt"]["entries"])
        assert all("error" not in entry for entry in applied["receipt"]["entries"])
        assert all(entry.get("error_redacted") is True for entry in applied["receipt"]["entries"])
        assert _PROBE_ENTRY_ERROR not in json.dumps(payload)
        assert applied["dropped"] == [{"redacted": True}]
        assert unanonymized_tuning_findings(payload) == []

    def test_export_command_redacts_unanonymized_source(self, tmp_path):
        from benchbox.cli.main import cli

        source = ResultExporter(output_dir=tmp_path / "local", anonymize=False).export_result(
            _tuned_result(), ["json"]
        )["json"]
        out_dir = tmp_path / "public"

        result = CliRunner().invoke(
            cli, ["export", str(source), "--format", "json", "--output-dir", str(out_dir), "--force"]
        )

        assert result.exit_code == 0, result.output
        exported = [json.loads(path.read_text(encoding="utf-8")) for path in out_dir.glob("*.json")]
        assert len(exported) == 1
        assert exported[0]["export"]["anonymized"] is True
        assert unanonymized_tuning_findings(exported[0]) == []

    def test_publish_redacts_unanonymized_source(self, tmp_path, monkeypatch):
        import benchbox.cli.commands.publish as publish_mod
        from benchbox.cli.commands.publish import publish_bundle

        source = ResultExporter(output_dir=tmp_path / "local", anonymize=False).export_result(
            _tuned_result(), ["json"]
        )["json"]

        created: list = []
        real_temporary_directory = publish_mod.TemporaryDirectory

        def _tracking_temporary_directory(*args, **kwargs):
            scratch = real_temporary_directory(*args, **kwargs)
            created.append(scratch)
            return scratch

        monkeypatch.setattr(publish_mod, "TemporaryDirectory", _tracking_temporary_directory)

        reference = publish_bundle(source, target=str(tmp_path / "published"), label="local", quiet=True)

        assert reference is not None
        published = [json.loads(path.read_text(encoding="utf-8")) for path in (tmp_path / "published").glob("*.json")]
        assert len(published) == 1
        assert published[0]["export"]["anonymized"] is True
        assert unanonymized_tuning_findings(published[0]) == []
        assert len(created) == 1
        assert not Path(created[0].name).exists()

    def test_redacted_publish_carries_plans_companion(self, tmp_path):
        from benchbox.cli.commands.publish import _count_companions, _redacted_publish_source, publish_bundle
        from benchbox.core.results.query_plan_models import (
            LogicalOperator,
            LogicalOperatorType,
            QueryPlanDAG,
        )

        result = _tuned_result()
        root = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_1",
            table_name="lineitem",
        )
        result.query_results = [
            {
                "query_id": "Q1",
                "execution_time": 1.0,
                "status": "SUCCESS",
                "rows_returned": 4,
                "query_plan": QueryPlanDAG(query_id="Q1", platform="duckdb", logical_root=root),
            }
        ]
        result.query_plans_captured = 1
        source = ResultExporter(output_dir=tmp_path / "local", anonymize=False).export_result(result, ["json"])["json"]

        assert _count_companions(source) == 1

        redacted, scratch = _redacted_publish_source(source)
        try:
            assert redacted is not None
            assert _count_companions(redacted) == 1
            assert unanonymized_tuning_findings(json.loads(redacted.read_text(encoding="utf-8"))) == []
        finally:
            if scratch is not None:
                scratch.cleanup()

        reference = publish_bundle(source, target=str(tmp_path / "published"), label="local", quiet=True)

        assert reference is not None
        published_names = sorted(path.name for path in (tmp_path / "published").glob("*.json"))
        assert published_names == sorted([source.name, source.stem + ".plans.json"])
        for path in (tmp_path / "published").glob("*.json"):
            assert unanonymized_tuning_findings(json.loads(path.read_text(encoding="utf-8"))) == []

    def test_redacted_publish_refuses_unredactable_override_companion(self, tmp_path):
        from benchbox.cli.commands.publish import _redacted_publish_source, publish_bundle

        source = ResultExporter(output_dir=tmp_path / "local", anonymize=False).export_result(
            _tuned_result(), ["json"]
        )["json"]
        (source.parent / (source.stem + ".override.json")).write_text('{"rules": []}', encoding="utf-8")

        redacted, scratch = _redacted_publish_source(source)

        assert redacted is None
        assert scratch is None
        assert publish_bundle(source, target=str(tmp_path / "published"), label="local", quiet=True) is None
        assert list((tmp_path / "published").glob("*.json")) == []

    def test_clean_primary_with_dirty_applied_companion_never_publishes_verbatim(self, tmp_path):
        import json as _json

        from benchbox.cli.commands.publish import _redacted_publish_source, publish_bundle
        from benchbox.validation.bundle import unanonymized_tuning_findings

        source = ResultExporter(output_dir=tmp_path / "local", anonymize=True).export_result(_tuned_result(), ["json"])[
            "json"
        ]
        assert unanonymized_tuning_findings(_json.loads(source.read_text(encoding="utf-8"))) == []
        (source.parent / (source.stem + ".applied.json")).write_text(
            _json.dumps(
                {
                    "statements": [{"statement": _PROBE_STATEMENT, "phase": "ddl", "status": "executed"}],
                    "receipt": {"entries": [{"reason": _PROBE_REASON}]},
                }
            ),
            encoding="utf-8",
        )

        redacted, scratch = _redacted_publish_source(source)
        try:
            assert redacted is not None
            assert redacted != source
            assert unanonymized_tuning_findings(_json.loads(redacted.read_text(encoding="utf-8"))) == []
        finally:
            if scratch is not None:
                scratch.cleanup()

        reference = publish_bundle(source, target=str(tmp_path / "published"), label="local", quiet=True)

        assert reference is not None
        published = list((tmp_path / "published").glob("*.json"))
        assert [path.name for path in published] == [source.name]
        for path in published:
            assert unanonymized_tuning_findings(_json.loads(path.read_text(encoding="utf-8"))) == []

    def test_clean_primary_with_clean_companions_publishes_verbatim(self, tmp_path):
        from benchbox.cli.commands.publish import _redacted_publish_source

        source = ResultExporter(output_dir=tmp_path / "local", anonymize=True).export_result(_tuned_result(), ["json"])[
            "json"
        ]
        (source.parent / (source.stem + ".applied.json")).write_text(
            '{"statements": [], "receipt": {"entries": []}}', encoding="utf-8"
        )

        redacted, scratch = _redacted_publish_source(source)

        assert redacted == source
        assert scratch is None

    def test_publish_run_reports_actually_published_companions(self, tmp_path):
        from benchbox.cli.commands.publish import publish_run

        source = ResultExporter(output_dir=tmp_path / "local", anonymize=True).export_result(_tuned_result(), ["json"])[
            "json"
        ]
        (source.parent / (source.stem + ".plans.json")).write_text('{"queries": {}}', encoding="utf-8")

        result = CliRunner().invoke(
            publish_run,
            [str(source), "--target", str(tmp_path / "published"), "--label", "local"],
        )

        assert result.exit_code == 0, result.output
        assert (tmp_path / "published" / source.name).exists()
        assert (tmp_path / "published" / (source.stem + ".plans.json")).exists()
        assert "+ 1 companion file(s) also published" in result.output

    def test_redacted_publish_records_original_source_and_dedups(self, tmp_path):
        from benchbox.cli.commands.publish import _redacted_publish_source
        from benchbox.core.publishing.bundle_publisher import BundlePublisher
        from benchbox.core.publishing.store import PublicationStore

        source = ResultExporter(output_dir=tmp_path / "local", anonymize=False).export_result(
            _tuned_result(), ["json"]
        )["json"]
        store = PublicationStore(store_path=tmp_path / "store.json")
        first_redacted, first_scratch = _redacted_publish_source(source)
        try:
            assert first_redacted is not None
            assert first_redacted != source
            first = BundlePublisher(destination=tmp_path / "published", store=store, label="local").publish(
                first_redacted, record_source=source
            )
            assert first.success
            assert first.record is not None
            assert Path(first.record.source_path) == source.resolve()
        finally:
            if first_scratch is not None:
                first_scratch.cleanup()
        assert Path(first.record.source_path).exists()
        second_redacted, second_scratch = _redacted_publish_source(source)
        try:
            assert second_redacted is not None
            second = BundlePublisher(destination=tmp_path / "published", store=store, label="local").publish(
                second_redacted, record_source=source
            )
            assert second.success
            assert second.record is not None
            assert second.record.pub_id == first.record.pub_id
        finally:
            if second_scratch is not None:
                second_scratch.cleanup()
        assert len(store.list_all()) == 1

    def test_publish_bundle_passes_original_source_for_record_keeping(self, tmp_path, monkeypatch):
        import benchbox.cli.commands.publish as publish_mod
        from benchbox.cli.commands.publish import publish_bundle
        from benchbox.core.publishing.store import PublicationStore

        source = ResultExporter(output_dir=tmp_path / "local", anonymize=False).export_result(
            _tuned_result(), ["json"]
        )["json"]
        store = PublicationStore(store_path=tmp_path / "store.json")
        monkeypatch.setattr(publish_mod, "PublicationStore", lambda *args, **kwargs: store)
        seen: dict = {}
        real_publisher = publish_mod.BundlePublisher

        class _SpyPublisher:
            def __init__(self, *args, **kwargs):
                self._inner = real_publisher(*args, **kwargs)

            def publish(self, source_bundle, *args, **kwargs):
                seen["record_source"] = kwargs.get("record_source", args[0] if args else None)
                return self._inner.publish(source_bundle, *args, **kwargs)

        monkeypatch.setattr(publish_mod, "BundlePublisher", _SpyPublisher)
        assert publish_bundle(source, target=str(tmp_path / "published"), label="local", quiet=True) is not None
        assert seen.get("record_source") is not None
        assert Path(seen["record_source"]).resolve() == source.resolve()
        records = store.list_all()
        assert len(records) == 1
        assert Path(records[0].source_path) == source.resolve()
        assert Path(records[0].source_path).exists()

    def test_clean_primary_with_dirty_plans_companion_routes_redacted(self, tmp_path, monkeypatch):
        import copy as _copy
        import json as _json

        import benchbox.cli.commands.publish as publish_mod
        from benchbox.cli.commands.publish import _redacted_publish_source, publish_bundle
        from benchbox.core.publishing.store import PublicationStore
        from benchbox.core.results.query_plan_models import (
            LogicalOperator,
            LogicalOperatorType,
            QueryPlanDAG,
        )
        from benchbox.validation.bundle import unanonymized_plans_findings, unanonymized_tuning_findings

        result = _tuned_result()
        root = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_1",
            table_name="lineitem",
        )
        result.query_results = [
            {
                "query_id": "Q1",
                "execution_time": 1.0,
                "status": "SUCCESS",
                "rows_returned": 4,
                "query_plan": QueryPlanDAG(query_id="Q1", platform="duckdb", logical_root=root),
            }
        ]
        result.query_plans_captured = 1
        source = ResultExporter(output_dir=tmp_path / "local", anonymize=True).export_result(result, ["json"])["json"]
        assert unanonymized_tuning_findings(_json.loads(source.read_text(encoding="utf-8"))) == []
        plans_path = source.parent / (source.stem + ".plans.json")
        assert unanonymized_plans_findings(_json.loads(plans_path.read_text(encoding="utf-8"))) == []
        dirty = _copy.deepcopy(_json.loads(plans_path.read_text(encoding="utf-8")))
        for entry in dirty["queries"].values():
            plan = entry["plan"]
            plan["raw_explain_output"] = "EXPLAIN SELECT * FROM lineitem"
            logical = plan.get("logical_root")
            if isinstance(logical, dict):
                logical["physical_operator"] = {
                    "operator_type": "Seq Scan",
                    "operator_id": "scan_1",
                    "properties": {},
                    "platform_metadata": {"sql": "SELECT * FROM lineitem"},
                }
            break
        plans_path.write_text(_json.dumps(dirty), encoding="utf-8")
        assert unanonymized_plans_findings(_json.loads(plans_path.read_text(encoding="utf-8"))) != []
        redacted, scratch = _redacted_publish_source(source)
        try:
            assert redacted is not None
            assert redacted != source
            assert unanonymized_tuning_findings(_json.loads(redacted.read_text(encoding="utf-8"))) == []
            redacted_plans = redacted.parent / (redacted.stem + ".plans.json")
            assert redacted_plans.exists()
            assert unanonymized_plans_findings(_json.loads(redacted_plans.read_text(encoding="utf-8"))) == []
        finally:
            if scratch is not None:
                scratch.cleanup()
        store = PublicationStore(store_path=tmp_path / "store.json")
        monkeypatch.setattr(publish_mod, "PublicationStore", lambda *args, **kwargs: store)
        assert publish_bundle(source, target=str(tmp_path / "published"), label="local", quiet=True) is not None
        published_plans = tmp_path / "published" / (source.stem + ".plans.json")
        assert published_plans.exists()
        assert unanonymized_plans_findings(_json.loads(published_plans.read_text(encoding="utf-8"))) == []


class TestTuningVerificationSummary:
    def test_none_without_ledger(self):
        assert summarize_tuning_verification(None) is None
        assert summarize_tuning_verification({}) is None
        assert summarize_tuning_verification("applied_unverified") is None

    def test_status_verdicts_reasons_and_dropped(self):
        summary = summarize_tuning_verification(_applied_ledger())

        assert isinstance(summary, TuningVerificationSummary)
        assert summary.status == "applied_unverified"
        assert summary.verdict_counts == (("unverifiable", 1),)
        assert summary.top_reasons == ((_PROBE_REASON, 1),)
        assert summary.dropped == ((_PROBE_DROP_INTENT, _PROBE_DROP_REASON),)

    def test_top_reasons_capped_at_three_by_count_then_name(self):
        ledger = _applied_ledger()
        ledger["receipt"]["entries"] = [
            {"verdict": "absent", "reason": "b-cause"},
            {"verdict": "absent", "reason": "a-cause"},
            {"verdict": "mismatch", "reason": "b-cause"},
            {"verdict": "absent", "reason": "c-cause"},
            {"verdict": "absent", "reason": "d-cause"},
        ]

        summary = summarize_tuning_verification(ledger)

        assert summary is not None
        assert summary.top_reasons == (("b-cause", 2), ("a-cause", 1), ("c-cause", 1))
        assert summary.verdict_counts == (("absent", 4), ("mismatch", 1))

    def test_format_renders_required_sections(self):
        lines = format_tuning_verification(summarize_tuning_verification(_applied_ledger()))
        text = "\n".join(lines)

        assert "Tuning verification: applied_unverified" in text
        assert "unverifiable=1" in text
        assert _PROBE_REASON in text
        assert _PROBE_DROP_INTENT in text


class TestUnanonymizedBundleRejection:
    def test_detector_catches_local_shapes(self, tmp_path):
        findings = unanonymized_tuning_findings(_exported_payload(tmp_path, anonymize=False))

        assert any("receipt.entries[0]" in finding for finding in findings)
        assert any("statements[0]" in finding for finding in findings)

    def test_detector_passes_redacted_and_empty_payloads(self, tmp_path):
        assert unanonymized_tuning_findings(_exported_payload(tmp_path, anonymize=True)) == []
        assert unanonymized_tuning_findings({}) == []
        assert unanonymized_tuning_findings({"platform": {"tuning": {}}}) == []

    def test_submission_validation_rejects_unanonymized_bundle(self, tmp_path):
        local = ResultExporter(output_dir=tmp_path, anonymize=False).export_result(_tuned_result(), ["json"])["json"]

        results = validate_bundles([local])

        assert len(results) == 1
        assert any("unanonymized tuning" in error for error in results[0].errors), results[0].errors

    def test_privacy_script_rejects_unanonymized_bundle(self, tmp_path):
        from scripts.publication.check_artifact_privacy import scan_directory_for_privacy

        local = ResultExporter(output_dir=tmp_path, anonymize=False).export_result(_tuned_result(), ["json"])["json"]

        findings = scan_directory_for_privacy(local.parent)

        assert any("nanonymized tuning" in finding for finding in findings), findings

    def test_privacy_script_passes_redacted_bundle(self, tmp_path):
        from scripts.publication.check_artifact_privacy import scan_directory_for_privacy

        public = ResultExporter(output_dir=tmp_path, anonymize=True).export_result(_tuned_result(), ["json"])["json"]

        assert scan_directory_for_privacy(public.parent) == []
