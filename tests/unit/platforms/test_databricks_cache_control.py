import json
from datetime import datetime
from unittest.mock import Mock, patch

import pytest

from benchbox.core.exceptions import ConfigurationError
from benchbox.core.results.models import BenchmarkResults
from benchbox.core.results.schema import build_result_payload
from benchbox.core.tuning.applied_ledger import PHASE_SESSION, AppliedTuningLedger, recording_connection
from benchbox.platforms.databricks import DatabricksAdapter
from benchbox.validation.bundle import ValidationResult, _validate_cache_control_section

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture
def adapter():
    with patch("benchbox.platforms.databricks.adapter.check_platform_dependencies", return_value=(True, [])):
        return DatabricksAdapter(
            server_hostname="test.cloud.databricks.com", http_path="/sql/1.0/warehouses/test", access_token="test"
        )


class Session:
    def __init__(self, *, reject=None, value="false"):
        self.commands = []
        self.reject = reject
        self.value = value
        self.closed = False

    def cursor(self):
        return Cursor(self)

    def close(self):
        self.closed = True


class Cursor:
    __slots__ = ("connection", "closed")

    def __init__(self, connection):
        self.connection = connection
        self.closed = False

    def execute(self, sql):
        self.connection.commands.append(sql)
        if sql == self.connection.reject:
            raise RuntimeError("rejected")

    def fetchall(self):
        return [(1,)]

    def fetchone(self):
        value = self.connection.value
        return None if value is None else ("use_cached_result", value)

    def close(self):
        self.closed = True


def test_none_defaults_to_required_disable(adapter):
    with patch("benchbox.platforms.databricks.adapter.check_platform_dependencies", return_value=(True, [])):
        configured = DatabricksAdapter(
            server_hostname="test", http_path="test", access_token="test", disable_result_cache=None
        )
    assert configured.disable_result_cache is True
    session = Session()
    assert configured.execute_query(session, "SELECT 1", "q1")["status"] == "SUCCESS"
    assert session.commands == ["SET use_cached_result = false", "SET use_cached_result", "SELECT 1"]


@pytest.mark.parametrize(
    "reject,value",
    [("SET use_cached_result = false", "false"), ("SET use_cached_result", "false"), (None, "true"), (None, None)],
)
def test_failed_set_or_probe_never_executes_benchmark_sql(adapter, reject, value):
    session = Session(reject=reject, value=value)
    result = adapter.execute_query(session, "SELECT expensive_query", "q1")
    assert result["status"] == "FAILED"
    assert result["execution_time_seconds"] == 0.0
    assert "SELECT expensive_query" not in session.commands
    assert adapter._cache_control_receipt["validated"] is False
    assert adapter._cache_control_receipt["errors"]


def test_distinct_slotted_cursors_share_actual_session_and_single_ledger_capture(adapter):
    session = Session()
    ledger = AppliedTuningLedger()
    adapter._applied_tuning_ledger = ledger
    wrapped = recording_connection(session, ledger, PHASE_SESSION)
    adapter._ensure_session_cache_disabled(wrapped.cursor())
    adapter._ensure_session_cache_disabled(session.cursor())
    adapter.execute_query(wrapped, "SELECT 1", "q1")
    assert session.commands.count("SET use_cached_result = false") == 1
    assert len(ledger.statements) == 1
    assert ledger.statements[0].statement == "SET use_cached_result = false"


def test_dynamic_cursor_attributes_do_not_invent_session_keys(adapter):
    cursor = Mock(spec=["execute", "fetchall", "fetchone"])
    cursor.fetchone.return_value = ("use_cached_result", "false")
    adapter._ensure_session_cache_disabled(cursor)
    adapter._ensure_session_cache_disabled(cursor)
    assert cursor.execute.call_count == 2


@pytest.mark.parametrize("entry", ["create", "configure"])
def test_old_setup_paths_propagate_rejected_set(adapter, entry):
    session = Session(reject="SET use_cached_result = false")
    with (
        patch.object(adapter, "handle_existing_database"),
        patch.object(adapter, "_create_admin_connection", return_value=session),
    ):
        with pytest.raises(ConfigurationError):
            if entry == "create":
                adapter.create_connection()
            else:
                adapter.configure_for_benchmark(session, "tpch")
    assert adapter._cache_control_receipt["validated"] is False
    if entry == "create":
        assert session.closed


def test_setup_and_execute_have_one_capture_owner(adapter):
    session = Session()
    ledger = AppliedTuningLedger()
    adapter._applied_tuning_ledger = ledger
    with (
        patch.object(adapter, "handle_existing_database"),
        patch.object(adapter, "_create_admin_connection", return_value=session),
    ):
        connection = adapter.create_connection()
    adapter.spark_configs = {"use_cached_result": "false"}
    adapter.configure_for_benchmark(recording_connection(connection, ledger, PHASE_SESSION), "tpch")
    adapter.execute_query(connection, "SELECT 1", "q1")
    assert len(ledger.statements) == 1


@pytest.mark.parametrize("factory", ["_make_direct_power_connection_adapter", "_make_power_connection_adapter"])
def test_zero_warmup_power_factory_initializes_before_harness_execution(adapter, factory):
    session = Session()
    wrapper = getattr(adapter, factory)(session, "tpch", 1.0)
    assert session.commands == ["SET use_cached_result = false", "SET use_cached_result"]

    session.commands.clear()
    wrapper.execute("SELECT 1")
    assert session.commands == ["SELECT 1"]


def test_new_throughput_stream_is_initialized_before_first_query(adapter):
    session = Session()
    stream = adapter.new_stream_connection(session)
    assert session.commands == ["SET use_cached_result = false", "SET use_cached_result"]
    session.commands.clear()
    adapter.execute_query(stream, "SELECT 1", "q1")
    assert session.commands == ["SELECT 1"]


@pytest.mark.parametrize(
    "value,enabled,reject",
    [
        ("false", False, None),
        ("true", False, None),
        (None, False, None),
        ("true", True, None),
        ("false", False, "SET use_cached_result = false"),
    ],
)
def test_producer_persistence_validator_seam(adapter, value, enabled, reject):
    adapter.disable_result_cache = not enabled
    session = Session(value=value, reject=reject)
    result = adapter.execute_query(session, "SELECT 1", "q1")
    metadata = adapter.get_normalized_result_metadata(platform_info={})
    receipt = metadata["platform_compute"]["cache_control"]
    benchmark = BenchmarkResults(
        benchmark_name="tpch",
        platform="Databricks",
        scale_factor=1.0,
        execution_id="cache-test",
        timestamp=datetime(2026, 1, 1),
        duration_seconds=1.0,
        total_queries=1,
        successful_queries=int(result["status"] == "SUCCESS"),
        failed_queries=int(result["status"] == "FAILED"),
        platform_compute=metadata["platform_compute"],
    )
    payload = json.loads(json.dumps(build_result_payload(benchmark)))
    from benchbox.platforms.cloud_shared import sanitize_cache_control_receipt

    assert sanitize_cache_control_receipt(payload["platform"]["compute"]["cache_control"]) == receipt
    validation = ValidationResult("cache-test")
    _validate_cache_control_section(payload["platform"], validation)
    assert validation.ok is (value == "false" and not enabled and reject is None)
    if value != "false" or enabled or reject is not None:
        assert validation.errors


def test_failed_session_cannot_be_hidden_by_later_success(adapter):
    adapter.execute_query(Session(value="true"), "SELECT 1", "q1")
    adapter.execute_query(Session(), "SELECT 1", "q2")
    assert adapter._cache_control_receipt["validated"] is False
    assert adapter._cache_control_receipt["errors"]


def test_unsupported_readback_shape_refuses_execution(adapter):
    cursor = Mock(spec=["execute", "fetchall", "fetchone"])
    cursor.fetchone.return_value = ("different_setting", "false")
    with pytest.raises(ConfigurationError):
        adapter._ensure_session_cache_disabled(cursor)
    assert "Unsupported cache readback" in adapter._cache_control_receipt["errors"][0]


def test_zero_warmup_real_power_runner_initializes_before_run_timer(adapter):
    from benchbox.core.tpch.power_test import TPCHPowerTest

    session = Session()
    benchmark = Mock()

    def timed_run(power):
        assert session.commands == ["SET use_cached_result = false", "SET use_cached_result"]
        session.commands.clear()
        power.connection.execute("SELECT 1")
        assert session.commands == ["SELECT 1"]
        return Mock(query_results=[], success=True)

    with patch.object(TPCHPowerTest, "run", timed_run):
        adapter._execute_tpch_power_test(
            benchmark, session, {"warm_up_iterations": 0, "iterations": 1, "seed": 42, "query_subset": ["1"]}
        )


def test_custom_spark_configuration_cannot_undo_cache_disable(adapter):
    adapter.spark_configs = {"use_cached_result": "true"}
    session = Session()
    with pytest.raises(ConfigurationError, match="conflicts"):
        adapter.configure_for_benchmark(session, "tpch")
    assert not session.commands
    assert adapter._cache_control_receipt["validated"] is False


def test_rejected_set_has_exactly_one_failed_ledger_entry(adapter):
    session = Session(reject="SET use_cached_result = false")
    ledger = AppliedTuningLedger()
    adapter._applied_tuning_ledger = ledger
    result = adapter.execute_query(recording_connection(session, ledger, PHASE_SESSION), "SELECT 1", "q1")
    assert result["status"] == "FAILED"
    assert len(ledger.statements) == 1
    assert ledger.statements[0].status == "failed"
    assert session.commands == ["SET use_cached_result = false"]


def test_unsupported_session_identity_refuses_before_measurement(adapter):
    class UntrackableCursor:
        __slots__ = ()

    with pytest.raises(ConfigurationError, match="Unsupported session identity"):
        adapter._ensure_session_cache_disabled(UntrackableCursor())
    assert adapter._cache_control_receipt["validated"] is False


def test_metadata_never_labels_intent_as_observed_disabled(adapter):
    metadata = adapter.get_normalized_result_metadata(platform_info={"configuration": {"result_cache_enabled": False}})
    assert "result_cache_enabled" not in metadata["platform_compute"]


@pytest.mark.parametrize("cache_state", ["disabled", "enabled", "rejected", "invalid", "malformed", "legacy"])
@pytest.mark.parametrize("has_client_link", [False, True])
def test_timeout_preserves_receipt_through_actual_adapter_export(adapter, tmp_path, cache_state, has_client_link):
    from contextlib import ExitStack

    from benchbox.core.benchmark_result_validation import BenchmarkResultValidationMixin
    from benchbox.core.results.models import DataGenerationPhase, DataLoadingPhase, SchemaCreationPhase, ValidationPhase
    from benchbox.platforms.cloud_shared import sanitize_cache_control_receipt
    from benchbox.validation.bundle import _validate_bundle

    class Benchmark(BenchmarkResultValidationMixin):
        scale_factor = 1.0
        output_dir = tmp_path
        _name = "tpch"

    class PoisonableSession(Session):
        poisoned = False

        def __getattribute__(self, name):
            if object.__getattribute__(self, "poisoned"):
                raise AssertionError(f"tainted connection accessed: {name}")
            return super().__getattribute__(name)

    session = PoisonableSession(reject="SET use_cached_result = false" if cache_state == "rejected" else None)
    adapter.disable_result_cache = cache_state != "enabled"
    collected_receipt = []

    def execute_workload(benchmark, connection, run_config):
        result = adapter.execute_query(connection, "SELECT 1", "1")
        if cache_state == "invalid":
            adapter._cache_control_receipt = {"validated": "true", "cache_disabled": "false"}
        elif cache_state == "malformed":
            adapter._cache_control_receipt = "invalid"
        elif cache_state == "legacy":
            adapter._cache_control_receipt = None
        collected_receipt.append(adapter._cache_control_receipt)
        return [result]

    def timeout_after_measurement(connection, run_config):
        adapter._link_probe_timed_out = True
        adapter._client_link_metadata = (
            {"collection_status": "unavailable", "source": "unavailable"} if has_client_link else None
        )
        object.__setattr__(connection, "poisoned", True)
        return 0.0

    generation = DataGenerationPhase(0, "SKIPPED", 0, 0, 0, {})
    schema = SchemaCreationPhase(0, "SKIPPED", 0, 0, 0, {})
    loading = DataLoadingPhase(0, "SKIPPED", 1, 1, {})
    validation = ValidationPhase(0, "PASSED", "PASSED", "PASSED", {})
    with ExitStack() as stack:
        patches = {
            "create_connection": {"return_value": session},
            "_create_enhanced_data_generation_phase": {"return_value": generation},
            "get_effective_tuning_configuration": {"return_value": None},
            "_setup_fresh_database_phases": {"return_value": (0.0, schema, 0.0, {"t": 1}, loading, False)},
            "_create_enhanced_validation_phase": {"return_value": validation},
            "configure_for_benchmark": {"return_value": None},
            "_execute_queries_by_type": {"side_effect": execute_workload},
            "_collect_post_measurement_metadata": {"side_effect": timeout_after_measurement},
            "_get_dialect_queries": {"return_value": {"1": "SELECT 1"}},
            "_build_execution_metadata": {"return_value": ({}, {}, None)},
            "_collect_resource_utilization": {"return_value": {}},
            "_close_run_connection": {"return_value": None},
            "get_platform_info": {"side_effect": AssertionError("live platform metadata must not be queried")},
            "get_normalized_result_metadata": {"side_effect": AssertionError("normal metadata path must not run")},
        }
        for name, kwargs in patches.items():
            stack.enter_context(patch.object(adapter, name, **kwargs))
        exported = adapter.run_enhanced_benchmark(Benchmark(), benchmark_name="tpch", link_probe=True)

    assert adapter._link_probe_timed_out is True
    payload = json.loads(json.dumps(build_result_payload(exported)))
    compute = payload["platform"].get("compute", {})
    gate = ValidationResult("timeout-receipt")
    _validate_cache_control_section(payload["platform"], gate)
    full_validation = ValidationResult("timeout-bundle")
    _validate_bundle(payload, full_validation)
    cache_errors = [error for error in full_validation.errors if "cache_control" in error or "result cache" in error]
    assert bool(cache_errors) is (cache_state not in {"disabled", "legacy"})
    if cache_state == "invalid":
        assert collected_receipt[0] == {"validated": "true", "cache_disabled": "false"}
    if cache_state == "legacy":
        assert "cache_control" not in compute
        assert gate.ok
    else:
        receipt = compute["cache_control"]
        assert receipt["validated"] is (cache_state in {"disabled", "enabled"})
        assert receipt["cache_disabled"] is (cache_state == "disabled")
        assert gate.ok is (cache_state == "disabled")
        if cache_state not in {"disabled", "enabled", "malformed"}:
            assert sanitize_cache_control_receipt(receipt) == sanitize_cache_control_receipt(collected_receipt[0])
        if cache_state == "malformed":
            assert "unsupported shape" in receipt["errors"][0]
    if has_client_link:
        assert payload["environment"]["client_link"]["collection_status"] == "unavailable"


