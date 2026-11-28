from __future__ import annotations

from dataclasses import asdict

import pytest

from benchbox.core.execution_engine import (
    MIXED,
    NOT_CAPTURED,
    ExecutionEngineHook,
    ExecutionEngineReceipt,
    ObservationNotRecognizedError,
    summarize_observed_engines,
    unmeasured_statements,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _receipt(**kwargs) -> ExecutionEngineReceipt:
    return ExecutionEngineReceipt(
        requested="default", applied=None, applied_class=None, resolution="platform_default", **kwargs
    )


def test_default_receipt_is_not_captured_without_source_or_reason():
    receipt = _receipt()
    assert (receipt.observed, receipt.observed_source, receipt.observed_reason) == (NOT_CAPTURED, "none", None)


def test_not_captured_keeps_the_reason_it_was_given():
    assert _receipt(observed_reason="library cannot report").observed_reason == "library cannot report"


@pytest.mark.parametrize("observed", ["in-memory", MIXED])
def test_observed_engine_needs_a_source(observed):
    with pytest.raises(ValueError, match="needs the source"):
        _receipt(observed=observed)


def test_not_captured_cannot_name_a_source():
    with pytest.raises(ValueError, match="cannot name source"):
        _receipt(observed_source="explain")


def test_observed_engine_cannot_carry_a_not_captured_reason():
    with pytest.raises(ValueError, match="cannot carry"):
        _receipt(observed="in-memory", observed_source="explain", observed_reason="why")


def test_receipt_round_trips_through_its_dict_form():
    for receipt in (
        _receipt(observed=MIXED, observed_source="runtime_state"),
        _receipt(observed_reason="no query completed"),
    ):
        assert ExecutionEngineReceipt(**asdict(receipt)) == receipt


@pytest.mark.parametrize(
    ("observations", "expected"),
    [
        (["streaming", "streaming"], ("streaming", "explain", None)),
        (["streaming", "in-memory"], (MIXED, "explain", None)),
        (["streaming", "streaming", "mixed"], (MIXED, "explain", None)),
        ([], (NOT_CAPTURED, "none", "no query completed")),
        (["streaming", None], (NOT_CAPTURED, "none", "the library reported no engine for 1 of 2 queries")),
    ],
)
def test_run_observation_summarises_the_per_query_observations(observations, expected):
    summary = summarize_observed_engines(_receipt(), observations, "explain")
    assert (summary.observed, summary.observed_source, summary.observed_reason) == expected


def test_declared_gap_overrides_per_query_observations():
    summary = summarize_observed_engines(_receipt(), ["streaming"], "explain", unavailable="no API")
    assert (summary.observed, summary.observed_source, summary.observed_reason) == (NOT_CAPTURED, "none", "no API")


class _PerQueryObserver(ExecutionEngineHook):
    OBSERVED_ENGINE_SOURCE = "explain"

    def __init__(self, answers):
        self.answers = dict(answers)

    def observe_execution_engine(self, lazy):
        answer = self.answers[lazy.query_id]
        if isinstance(answer, Exception):
            raise answer
        return answer


class _RunEndObserver(_PerQueryObserver):
    OBSERVATION_AT_RUN_END = True

    def observe_run_end_targets(self, targets, connection=None):
        return [self.answers.get(target.statement_ids[0]) for target in targets]


def _recorded() -> dict:
    return asdict(_receipt())


def _finalize(observer, queries):
    for query_id in queries:
        observer.record_query_engine_observation(object(), f"select {query_id}", query_id, [query_id])
    return observer.finalize_query_engine_observation(_recorded())


def test_per_query_observations_are_summarised_into_the_run_receipt():
    done = _finalize(_PerQueryObserver({"q1": "fast", "q2": "fast"}), ["q1", "q2"])
    assert (done["observed"], done["observed_source"], done["observed_reason"]) == ("fast", "explain", None)


def test_disagreeing_per_query_observations_are_mixed():
    assert _finalize(_PerQueryObserver({"q1": "fast", "q2": "slow"}), ["q1", "q2"])["observed"] == MIXED


def test_a_query_without_evidence_makes_the_run_not_captured_rather_than_defaulting():
    done = _finalize(_PerQueryObserver({"q1": "fast", "q2": None}), ["q1", "q2"])
    assert (done["observed"], done["observed_source"]) == (NOT_CAPTURED, "none")
    assert "no engine for 1 of 2 queries" in done["observed_reason"]


def test_an_unrecognized_observation_is_counted_and_named_in_the_reason():
    done = _finalize(_PerQueryObserver({"q1": ObservationNotRecognizedError("x")}), ["q1"])
    assert done["observed"] == NOT_CAPTURED
    assert done["observed_reason"] == "query evidence not recognized for 1 of 1 queries"


def test_a_failing_observation_never_fails_the_query():
    done = _finalize(_PerQueryObserver({"q1": RuntimeError("boom")}), ["q1"])
    assert done["observed"] == NOT_CAPTURED


def test_run_end_observation_resolves_every_recorded_query_together():
    observer = _RunEndObserver({"q1": "fast", "q2": "fast"})
    assert _finalize(observer, ["q1", "q2"])["observed"] == "fast"


def test_run_end_observation_with_no_recorded_query_says_so():
    assert _finalize(_RunEndObserver({}), [])["observed_reason"] == "no query completed"


def test_an_adapter_without_a_source_records_nothing():
    observer = ExecutionEngineHook()
    observer.record_query_engine_observation(object(), "select 1", "q1")
    assert observer.finalize_query_engine_observation(_recorded()) == _recorded()


def test_a_declared_gap_overrides_recorded_observations():
    class _Gapped(_PerQueryObserver):
        def engine_observation_gap(self):
            return "no evidence available"

    done = _finalize(_Gapped({"q1": "fast"}), ["q1"])
    assert (done["observed"], done["observed_reason"]) == (NOT_CAPTURED, "no evidence available")


def test_warmup_and_setup_statements_are_not_counted():
    observer = _RunEndObserver({"q1": "fast", "q2": "slow", "q3": "fast"})
    observer.reset_query_engine_observation()
    observer.record_query_engine_observation(object(), "select 0", "setup", ["setup"])
    observer.open_engine_observation_window()
    with unmeasured_statements():
        observer.record_query_engine_observation(object(), "select 2", "q2", ["q2"])
    observer.record_query_engine_observation(object(), "select 1", "q1", ["q1"])
    observer.close_engine_observation_window()
    observer.record_query_engine_observation(object(), "select 3", "q3", ["q3"])

    done = observer.finalize_query_engine_observation(_recorded())

    assert (done["observed"], done["observed_reason"]) == ("fast", None)
    assert [target.query_id for target in observer._engine_targets] == ["q1"]


def test_observation_state_is_reset_for_each_run():
    observer = _PerQueryObserver({"q1": RuntimeError("boom"), "q2": ObservationNotRecognizedError("x")})
    observer.record_query_engine_observation(object(), "select 1", "q1")
    observer.record_query_engine_observation(object(), "select 2", "q2")

    observer.reset_query_engine_observation()
    done = observer.finalize_query_engine_observation(_recorded())

    assert done["observed_reason"] == "no query completed"
    assert observer._unrecognized_observations == 0
