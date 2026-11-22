from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from benchbox.cli.output import ResultExporter
from benchbox.core import run_service
from benchbox.core.exceptions import RunTimeoutError
from benchbox.core.publishing.admission import publish_admission
from benchbox.core.results.loader import reconstruct_benchmark_results
from benchbox.core.results.schema import SchemaV2ValidationError, SchemaV2Validator, build_result_payload
from benchbox.core.results.status import (
    bundle_non_clean_reason,
    result_cli_failure_reason,
    validation_status_is_non_clean,
)
from benchbox.core.results.submit_classification import SubmitTerminalState, classify_loaded_result
from benchbox.core.schemas import BenchmarkConfig
from benchbox.utils.run_deadline import (
    RUN_TIMEOUT_OPTION_KEY,
    TIMED_OUT_VALIDATION_STATUS,
    RunDeadline,
    active_run_deadline,
    current_run_deadline,
    mark_timed_out,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

DATABASE = SimpleNamespace(type="duckdb")


def _config(**options: Any) -> BenchmarkConfig:
    return BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=0.01, options=options)


def _execute(config: BenchmarkConfig) -> Any:
    return run_service.execute_run(
        config=config,
        benchmark_instance=None,
        database_config=DATABASE,
        system_profile=None,
        platform_config=None,
        output_root=None,
        phases_to_run=["load", "power"],
        adapter_factory=None,
        verbosity=None,
    )


def _stop_in(phase: str, query_id: str | None = None):
    def stopped(**_kwargs: Any) -> Any:
        deadline = current_run_deadline()
        assert deadline is not None
        deadline.enter_phase(phase)
        if query_id is not None:
            deadline.enter_query(query_id)
        raise deadline.record_timeout()

    return stopped


def _export(result: Any, directory: Path) -> dict[str, Any]:
    exporter = ResultExporter(anonymize=False)
    exporter.output_dir = directory
    exported = exporter.export_result(result, ["json"])
    return json.loads(Path(str(exported["json"])).read_text(encoding="utf-8"))


@pytest.fixture
def timed_out_result(monkeypatch) -> Any:
    monkeypatch.setattr(run_service, "_execute_run", _stop_in("execute", "Q5"))
    return _execute(_config(**{RUN_TIMEOUT_OPTION_KEY: 5}))


def test_without_a_limit_no_deadline_exists(monkeypatch) -> None:
    seen: list[Any] = []
    monkeypatch.setattr(run_service, "_execute_run", lambda **_kwargs: seen.append(current_run_deadline()) or "done")

    assert _execute(_config()) == "done"
    assert _execute(_config(**{RUN_TIMEOUT_OPTION_KEY: 0})) == "done"
    assert seen == [None, None]


def test_the_configured_limit_is_active_for_the_whole_run(monkeypatch) -> None:
    seen: list[int] = []

    def finished(**_kwargs: Any) -> Any:
        deadline = current_run_deadline()
        assert deadline is not None
        seen.append(deadline.timeout_seconds)
        return SimpleNamespace(validation_status="PASSED", validation_details=None)

    monkeypatch.setattr(run_service, "_execute_run", finished)

    result = _execute(_config(**{RUN_TIMEOUT_OPTION_KEY: 600}))

    assert seen == [600]
    assert result.validation_status == "PASSED"
    assert current_run_deadline() is None


def test_an_already_active_deadline_is_used_instead_of_the_option(monkeypatch) -> None:
    seen: list[Any] = []
    monkeypatch.setattr(run_service, "_execute_run", lambda **_kwargs: seen.append(current_run_deadline()) or "done")
    outer = RunDeadline(900)

    with active_run_deadline(outer):
        _execute(_config(**{RUN_TIMEOUT_OPTION_KEY: 5}))

    assert seen == [outer]


def test_a_timeout_becomes_a_marked_result_not_an_error(timed_out_result) -> None:
    assert timed_out_result.validation_status == TIMED_OUT_VALIDATION_STATUS
    assert timed_out_result.validation_details == {
        "timeout": {"timeout_seconds": 5, "phase": "execute", "query_id": "Q5"}
    }
    assert timed_out_result.platform == "duckdb"


def test_a_result_returned_after_a_stop_request_is_marked_and_keeps_its_queries(monkeypatch) -> None:
    rows = [{"query_id": "Q1", "status": "SUCCESS"}]

    def stopped_between_queries(**_kwargs: Any) -> Any:
        deadline = current_run_deadline()
        assert deadline is not None
        deadline.enter_phase("execute")
        deadline.enter_query("Q2")
        deadline.record_timeout()
        return SimpleNamespace(
            validation_status="PASSED", validation_details=None, query_results=rows, compliance_class="official"
        )

    monkeypatch.setattr(run_service, "_execute_run", stopped_between_queries)

    result = _execute(_config(**{RUN_TIMEOUT_OPTION_KEY: 5}))

    assert result.validation_status == TIMED_OUT_VALIDATION_STATUS
    assert result.validation_details["timeout"]["query_id"] == "Q2"
    assert result.query_results == rows
    assert result.compliance_class is None


def test_an_error_raised_after_the_limit_stays_an_error(monkeypatch) -> None:
    def fails_late(**_kwargs: Any) -> Any:
        monkeypatch.setattr(RunDeadline, "expired", property(lambda _self: True))
        raise RuntimeError("connection closed")

    monkeypatch.setattr(run_service, "_execute_run", fails_late)

    with pytest.raises(RuntimeError, match="connection closed"):
        _execute(_config(**{RUN_TIMEOUT_OPTION_KEY: 5}))


@pytest.mark.parametrize("verdict", ["FAILED", "ERROR", "INTERRUPTED"])
def test_a_failed_run_that_also_passed_the_limit_keeps_its_verdict_and_records_the_timeout(
    monkeypatch, verdict
) -> None:
    def fails_validation_late(**_kwargs: Any) -> Any:
        deadline = current_run_deadline()
        assert deadline is not None
        deadline.enter_phase("load")
        monkeypatch.setattr(RunDeadline, "expired", property(lambda _self: True))
        return SimpleNamespace(validation_status=verdict, validation_details={"error": "row counts differ"})

    monkeypatch.setattr(run_service, "_execute_run", fails_validation_late)

    result = _execute(_config(**{RUN_TIMEOUT_OPTION_KEY: 5}))

    assert result.validation_status == verdict
    assert result.validation_details == {
        "error": "row counts differ",
        "timeout": {"timeout_seconds": 5, "phase": "load"},
    }


def test_a_run_that_finished_its_work_after_the_limit_is_marked_without_a_query(monkeypatch) -> None:
    def finishes_late(**_kwargs: Any) -> Any:
        deadline = current_run_deadline()
        assert deadline is not None
        deadline.enter_phase("execute")
        deadline.enter_query("Q22")
        monkeypatch.setattr(RunDeadline, "expired", property(lambda _self: True))
        return SimpleNamespace(validation_status="PASSED", validation_details=None)

    monkeypatch.setattr(run_service, "_execute_run", finishes_late)

    result = _execute(_config(**{RUN_TIMEOUT_OPTION_KEY: 5}))

    assert result.validation_status == TIMED_OUT_VALIDATION_STATUS
    assert result.validation_details["timeout"] == {"timeout_seconds": 5, "phase": "execute"}


def test_an_error_before_the_limit_is_still_an_error(monkeypatch) -> None:
    def fails_early(**_kwargs: Any) -> Any:
        raise RuntimeError("bad config")

    monkeypatch.setattr(run_service, "_execute_run", fails_early)

    with pytest.raises(RuntimeError, match="bad config"):
        _execute(_config(**{RUN_TIMEOUT_OPTION_KEY: 600}))


def test_the_exported_bundle_carries_the_status_and_the_timeout_block(timed_out_result, tmp_path) -> None:
    bundle = _export(timed_out_result, tmp_path)

    assert bundle["version"] == "2.6"
    assert bundle["summary"]["validation"] == "timed_out"
    assert bundle["execution"]["timeout"] == {"timeout_seconds": 5, "phase": "execute", "query_id": "Q5"}
    SchemaV2Validator().validate(bundle)


def test_a_loaded_timed_out_bundle_keeps_its_timeout_block_when_exported_again(timed_out_result, tmp_path) -> None:
    bundle = _export(timed_out_result, tmp_path)

    again = build_result_payload(reconstruct_benchmark_results(bundle))

    assert again["summary"]["validation"] == "timed_out"
    assert again["execution"]["timeout"] == bundle["execution"]["timeout"]


def test_a_run_that_was_not_stopped_has_no_timeout_block(tmp_path) -> None:
    result = run_service.build_unfinished_result(
        _config(), platform="duckdb", validation_status="PASSED", validation_details={}
    )
    assert "timeout" not in _export(result, tmp_path)["execution"]


def test_the_timeout_block_is_refused_in_a_bundle_older_than_2_6(timed_out_result, tmp_path) -> None:
    bundle = _export(timed_out_result, tmp_path)
    bundle["version"] = "2.5"
    if "result_schema_version" in bundle:
        bundle["result_schema_version"] = "2.5"

    with pytest.raises(SchemaV2ValidationError, match="execution.timeout requires schema version 2.6"):
        SchemaV2Validator().validate(bundle)


def test_a_timed_out_result_is_refused_for_publishing_and_submission(timed_out_result, tmp_path) -> None:
    assert validation_status_is_non_clean("timed_out")
    assert result_cli_failure_reason(timed_out_result) == "validation_status=timed_out"

    decision = publish_admission(timed_out_result, "maintainer-run")
    assert (decision.allowed, decision.code, decision.reason) == (False, "non_clean", "validation_status=timed_out")
    assert classify_loaded_result(timed_out_result) is not SubmitTerminalState.submittable
    assert bundle_non_clean_reason(_export(timed_out_result, tmp_path)) == "validation_status=timed_out"


def test_marking_keeps_an_unofficial_class_and_drops_only_the_official_claim() -> None:
    deadline = RunDeadline(1)
    subscale = SimpleNamespace(
        validation_status="PASSED", validation_details=None, compliance_class="unofficial_subscale"
    )

    mark_timed_out(subscale, deadline)

    assert subscale.compliance_class == "unofficial_subscale"


def test_the_typed_error_carries_the_limit() -> None:
    error = RunTimeoutError(30, "generate", detail="BenchBox stopped dsdgen")
    assert (
        str(error) == "The run reached its 30-second limit (timeout_seconds) during generate: BenchBox stopped dsdgen"
    )
