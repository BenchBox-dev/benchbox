from __future__ import annotations

from types import SimpleNamespace

import pytest

from benchbox.core.publishing.admission import publish_admission
from benchbox.core.results.models import QueryExecution
from benchbox.core.results.submit_classification import (
    SubmitTerminalState,
    classify_loaded_result,
    result_refused_at_submission,
)
from tests.fixtures.result_dict_fixtures import make_power_only_phases
from tests.unit.core.results.test_tpch_refresh_metrics import _result_payload

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _loaded(scale_factor: float, *, stamp: str | None = None, benchmark_name: str = "tpch") -> SimpleNamespace:
    query = QueryExecution(query_id="1", execution_time_ms=10.0, status="SUCCESS", run_type="measurement")
    result = SimpleNamespace(
        benchmark_name=benchmark_name,
        platform="platform-a",
        scale_factor=scale_factor,
        total_queries=22,
        duration_seconds=1.0,
        validation_status="PASSED",
        execution_phases=make_power_only_phases(query_executions=[query]),
        query_results=[query],
        execution_metadata={},
        failed_queries=0,
    )
    if stamp is not None:
        result.compliance_class = stamp
    return result


def test_incomplete_result_is_undetermined_without_crashing() -> None:
    result = SimpleNamespace(benchmark_name="tpch", scale_factor=1.0, total_queries=22, duration_seconds=1.0)
    assert classify_loaded_result(result) is SubmitTerminalState.schema_violation
    decision = publish_admission(result, "community")
    assert (decision.allowed, decision.reason) == (False, "undetermined")


@pytest.mark.parametrize("benchmark_name", ["tpch", "tpcds"])
def test_a_standard_scale_run_without_the_official_flag_is_admitted_publicly(benchmark_name: str) -> None:
    result = _loaded(1.0, benchmark_name=benchmark_name)

    assert publish_admission(result, "community").allowed
    assert classify_loaded_result(result) is SubmitTerminalState.submittable


@pytest.mark.parametrize("benchmark_name", ["tpch", "tpcds"])
@pytest.mark.parametrize("stamp", [None, "official"])
def test_a_sub_sf1_tpc_run_is_refused_publicly_whatever_it_is_stamped(benchmark_name: str, stamp: str | None) -> None:
    result = _loaded(0.01, stamp=stamp, benchmark_name=benchmark_name)

    decision = publish_admission(result, "community")
    assert (decision.allowed, decision.code) == (False, "unofficial_compliance")
    assert classify_loaded_result(result) is SubmitTerminalState.unofficial


@pytest.mark.parametrize("stamp", [None, "official", "unofficial_subscale", "unofficial_nonstandard"])
def test_the_stamp_never_decides_the_public_gate(stamp: str | None) -> None:
    assert result_refused_at_submission(_loaded(1.0, stamp=stamp)) is False
    assert result_refused_at_submission(_loaded(2.0, stamp=stamp)) is False
    assert result_refused_at_submission(_loaded(0.5, stamp=stamp)) is True


def test_a_non_standard_scale_run_is_admitted_unranked() -> None:
    assert publish_admission(_loaded(2.0), "community").allowed


def test_a_gateway_routed_run_is_refused_publicly() -> None:
    result = _loaded(1.0)
    result.platform_info = {"gateway": {"name": "proxy", "routed": True}}

    assert not publish_admission(result, "community").allowed


def test_a_written_power_only_standard_scale_result_is_admitted_publicly() -> None:
    from benchbox.core.results.loader import reconstruct_benchmark_results

    payload = _result_payload(1.0, None, power_refresh=False, evidenced=True)
    loaded = reconstruct_benchmark_results(payload)

    assert "compliance_class" not in payload["benchmark"]
    assert publish_admission(loaded, "community").allowed


def test_no_reference_is_refused_when_a_reference_exists() -> None:
    result = _loaded(1.0)
    result.validation_status = "no_reference"
    assert not publish_admission(result, "community").allowed
    assert classify_loaded_result(result) is not SubmitTerminalState.submittable


def test_tpcdi_direct_audited_result_preserves_public_admission() -> None:
    from tests.fixtures.result_dict_fixtures import make_benchmark_results

    query = QueryExecution(query_id="1", execution_time_ms=10.0, status="SUCCESS", run_type="measurement")
    result = make_benchmark_results(
        benchmark_id="tpcdi",
        benchmark_name="TPC-DI",
        execution_phases=make_power_only_phases(query_executions=[query]),
        query_results=[query],
        validation_status="PASSED",
        validation_details={"tpcdi_audit": {"status": "recorded"}},
    )
    assert classify_loaded_result(result) is SubmitTerminalState.submittable
    assert publish_admission(result, "community").allowed


def test_tpcdi_file_paths_remain_refused(tmp_path) -> None:
    import json

    from benchbox.core.results.loader import load_result_file
    from benchbox.validation.bundle import validate_bundles
    from scripts.stage_seed_corpus_bundle import admission_reason, stage_seed_bundle

    payload = _result_payload(1.0, None, evidenced=True)
    payload["benchmark"].update(id="tpcdi", name="TPC-DI")
    path = tmp_path / "result.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded, _ = load_result_file(path)
    assert classify_loaded_result(loaded) is SubmitTerminalState.unvalidated
    assert not publish_admission(loaded, "community").allowed
    assert any("TPC-DI bundles are not accepted" in error for error in validate_bundles([path])[0].errors)
    assert admission_reason(payload, path) == "not_admitted_undetermined"
    assert not stage_seed_bundle(path, tmp_path / "staged")


def test_in_memory_timed_queries_share_submit_and_outcome_facts() -> None:
    from benchbox.core.results import corpus_rule
    from benchbox.core.results.result_outcome import facts_for_result

    result = _loaded(1.0)
    result.execution_phases = None
    facts = facts_for_result(result)
    assert facts.phases == frozenset({corpus_rule.PHASE_OTHER_TIMED})
    assert corpus_rule.public_refusal_for_facts(facts) is None
    assert classify_loaded_result(result) is SubmitTerminalState.submittable


def test_query_counts_without_measured_rows_do_not_establish_timed_work() -> None:
    from benchbox.core.results import corpus_rule
    from benchbox.core.results.result_outcome import facts_for_result

    result = _loaded(1.0)
    result.execution_phases = None
    result.query_results = []
    facts = facts_for_result(result)
    assert facts.phases == frozenset()
    assert corpus_rule.public_refusal_for_facts(facts) == corpus_rule.HELD_NO_TIMED_PHASE


def test_loaded_explicit_null_phases_remain_undetermined() -> None:
    from benchbox.core.results import corpus_rule
    from benchbox.core.results.loader import reconstruct_benchmark_results
    from benchbox.core.results.result_outcome import facts_for_result, outcome_for_result

    payload = _result_payload(1.0, None, power_refresh=False, evidenced=True)
    payload["phases"] = None
    result = reconstruct_benchmark_results(payload)
    assert facts_for_result(result).phases is None
    assert outcome_for_result(result).class_name == corpus_rule.CLASS_UNDETERMINED
    assert classify_loaded_result(result) is SubmitTerminalState.schema_violation


def test_underscore_benchmark_name_is_not_squashed() -> None:
    from benchbox.core.results.result_outcome import facts_for_result

    result = _loaded(1.0, benchmark_name="read_primitives")
    assert facts_for_result(result).benchmark == "read_primitives"
