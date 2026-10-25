from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from benchbox.core.publishing.admission import publish_admission
from benchbox.core.results.database import ResultDatabase
from benchbox.core.results.loader import load_result_file
from benchbox.core.results.metrics import TPCMetricsCalculator
from benchbox.core.results.models import BenchmarkResults
from benchbox.core.results.schema import build_result_payload
from benchbox.core.results.submit_classification import (
    SubmitTerminalState,
    classify_loaded_result,
    classify_result_path,
)
from benchbox.core.results.subscale_policy import (
    NONSTANDARD_COMPLIANCE_CLASS,
    bundle_compliance_class,
    result_compliance_class,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROUTED = {"name": "espresso", "routed": True}


def _result(gateway: dict | None, compliance_class: str | None = "official") -> BenchmarkResults:
    platform_info: dict = {"platform_name": "Snowflake"}
    if gateway is not None:
        platform_info["gateway"] = gateway
    return BenchmarkResults(
        benchmark_name="TPC-H",
        platform="Snowflake",
        scale_factor=1.0,
        execution_id="routed-compliance",
        timestamp=datetime(2026, 1, 1),
        duration_seconds=1.0,
        total_queries=1,
        successful_queries=1,
        failed_queries=0,
        query_results=[],
        total_execution_time=1.0,
        average_query_time=1.0,
        validation_status="PASSED",
        platform_info=platform_info,
        compliance_class=compliance_class,
    )


def _write_bundle(path: Path, gateway: dict | None, compliance_class: str | None = "official") -> Path:
    payload: dict = {
        "version": "2.1",
        "run": {"id": "routed", "timestamp": "2026-01-01T00:00:00", "total_duration_ms": 1},
        "benchmark": {"id": "tpch", "name": "TPC-H", "scale_factor": 1.0, "test_type": "power"},
        "platform": {"name": "Snowflake"},
        "summary": {"queries": {"total": 1, "passed": 1, "failed": 0}, "validation": {"status": "passed"}},
        "queries": [{"id": "Q1", "status": "SUCCESS", "ms": 1}],
        "phases": {},
    }
    if compliance_class is not None:
        payload["benchmark"]["compliance_class"] = compliance_class
    if gateway is not None:
        payload["platform"]["gateway"] = gateway
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_routed_result_is_never_official() -> None:
    assert result_compliance_class(_result(ROUTED)) == NONSTANDARD_COMPLIANCE_CLASS
    assert result_compliance_class(_result(ROUTED, compliance_class=None)) == NONSTANDARD_COMPLIANCE_CLASS


def test_native_result_is_unaffected() -> None:
    assert result_compliance_class(_result(None)) == "official"
    assert result_compliance_class(_result({"name": "espresso", "routed": False})) == "official"
    assert result_compliance_class(_result(None, compliance_class=None)) is None


def test_routed_bundle_payload_is_not_official_and_has_no_tpc_metrics() -> None:
    result = _result(ROUTED)
    result.power_at_size = 1234.0
    payload = build_result_payload(result)
    assert payload["benchmark"]["compliance_class"] == NONSTANDARD_COMPLIANCE_CLASS
    assert payload["tpc_compliant"] is False
    assert payload["summary"]["tpc_metrics"]["suppressed"] is True


def test_native_bundle_payload_keeps_official() -> None:
    payload = build_result_payload(_result(None))
    assert payload["benchmark"]["compliance_class"] == "official"


def test_publish_admission_rejects_routed_result_claiming_official() -> None:
    decision = publish_admission(_result(ROUTED), "official")
    assert decision.allowed is False
    assert decision.code == "unofficial_compliance"
    assert publish_admission(_result(ROUTED), "unofficial-research").allowed is True
    assert publish_admission(_result(None), "official").allowed is True


def test_submit_classification_rejects_routed_result_claiming_official(tmp_path: Path) -> None:
    assert classify_loaded_result(_result(ROUTED)) is SubmitTerminalState.unofficial
    assert classify_loaded_result(_result(None)) is SubmitTerminalState.submittable

    routed = _write_bundle(tmp_path / "routed.json", ROUTED)
    native = _write_bundle(tmp_path / "native.json", None)
    assert classify_result_path(routed) is SubmitTerminalState.unofficial
    assert classify_result_path(native) is SubmitTerminalState.submittable
    loaded, _ = load_result_file(routed)
    assert result_compliance_class(loaded) == NONSTANDARD_COMPLIANCE_CLASS


def test_bundle_helper_and_metrics_readiness_refuse_routed_bundle() -> None:
    routed = {
        "benchmark": {"id": "tpch", "compliance_class": "official"},
        "platform": {"gateway": ROUTED},
        "summary": {"queries": {"failed": 0}},
    }
    native = {"benchmark": {"id": "tpch", "compliance_class": "official"}, "platform": {}}
    assert bundle_compliance_class(routed) == NONSTANDARD_COMPLIANCE_CLASS
    assert bundle_compliance_class(native) == "official"
    assert TPCMetricsCalculator.metrics_refusal_reason(routed, "Power") is not None
    assert any(
        "not official compliance" in reason for reason in TPCMetricsCalculator.qphh_unavailable_reasons(routed, routed)
    )
    assert not any(
        "not official compliance" in reason for reason in TPCMetricsCalculator.qphh_unavailable_reasons(native, native)
    )


def test_results_database_stores_routed_result_as_non_official(tmp_path: Path) -> None:
    database = ResultDatabase(tmp_path / "results.db")
    routed = _result(ROUTED)
    routed.execution_id = "routed-run"
    native = _result(None)
    native.execution_id = "native-run"
    database.store_result(routed)
    database.store_result(native)

    import sqlite3

    with sqlite3.connect(tmp_path / "results.db") as connection:
        rows = dict(connection.execute("SELECT execution_id, compliance_class FROM results"))
    assert rows == {"routed-run": NONSTANDARD_COMPLIANCE_CLASS, "native-run": "official"}


def _built_like_a_real_run(gateway: dict | None):
    from benchbox.core.results.builder import BenchmarkInfoInput, PlatformInfoInput, ResultBuilder
    from benchbox.core.results.query_normalizer import normalize_query_result

    adapter_info: dict = {"platform_type": "snowflake", "account": "acct"}
    if gateway is not None:
        adapter_info["gateway"] = gateway
    builder = ResultBuilder(
        benchmark=BenchmarkInfoInput(
            name="tpch",
            display_name="TPC-H",
            scale_factor=1.0,
            test_type="power",
            benchmark_id="tpch",
            compliance_class="official",
        ),
        platform=PlatformInfoInput(name="Snowflake", config=adapter_info),
    )
    builder.add_query_result(
        normalize_query_result(
            {
                "query_id": "1",
                "execution_time_seconds": 0.5,
                "rows_returned": 1,
                "status": "SUCCESS",
                "iteration": 1,
                "stream_id": 0,
                "run_type": "measurement",
            }
        )
    )
    return builder.build()


def test_routed_result_built_by_result_builder_is_non_official_everywhere(tmp_path: Path) -> None:
    result = _built_like_a_real_run(ROUTED)
    assert "gateway" not in result.platform_info
    assert result.platform_info["configuration"]["gateway"] == ROUTED
    assert result_compliance_class(result) == NONSTANDARD_COMPLIANCE_CLASS

    payload = build_result_payload(result)
    assert payload["benchmark"]["compliance_class"] == NONSTANDARD_COMPLIANCE_CLASS
    assert payload["tpc_compliant"] is False
    assert payload["summary"]["tpc_metrics"]["suppressed"] is True
    assert payload["platform"]["gateway"] == ROUTED

    assert publish_admission(result, "official").allowed is False
    assert classify_loaded_result(result) is SubmitTerminalState.unofficial
    written = tmp_path / "routed.json"
    written.write_text(json.dumps(payload), encoding="utf-8")
    assert classify_result_path(written) is SubmitTerminalState.unofficial


def test_native_result_built_by_result_builder_stays_official() -> None:
    result = _built_like_a_real_run(None)
    assert result_compliance_class(result) == "official"
    assert build_result_payload(result)["benchmark"]["compliance_class"] == "official"


def test_routed_result_built_by_result_builder_carries_no_tpc_metrics() -> None:
    routed = _built_like_a_real_run(ROUTED)
    assert routed.power_at_size is None
    assert routed.throughput_at_size is None
    assert routed.qph_at_size is None
    assert routed.compliance_class == NONSTANDARD_COMPLIANCE_CLASS
    payload = build_result_payload(routed)
    assert "power_at_size" not in payload["summary"]["tpc_metrics"]
    assert "qphh_at_size" not in payload["summary"]["tpc_metrics"]


def test_native_result_built_by_result_builder_keeps_tpc_metrics() -> None:
    native = _built_like_a_real_run(None)
    assert native.power_at_size is not None
    assert native.compliance_class == "official"
