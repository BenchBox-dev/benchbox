from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

import benchbox.platforms.base.adapter as adapter_module
import benchbox.platforms.postgresql as postgresql_module
from benchbox.core.tuning.applied_ledger import PHASE_DDL, AppliedTuningLedger
from benchbox.core.tuning.interface import UnifiedTuningConfiguration
from benchbox.core.tuning.metadata import NO_TUNING_METADATA_ERROR
from benchbox.platforms.base import PlatformAdapter
from benchbox.platforms.base.models import DatabaseValidationResult
from benchbox.platforms.base.validation import DatabaseValidator, SchemaValidator
from benchbox.platforms.postgresql import PostgreSQLAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture()
def console(monkeypatch):
    fake = Mock()
    monkeypatch.setattr(adapter_module, "quiet_console", fake)
    return fake


def printed(console: Mock) -> list[str]:
    return [str(call.args[0]) for call in console.print.call_args_list]


@pytest.fixture()
def postgres_adapter(monkeypatch):
    stub = Mock()
    stub.__version__ = "3.1.0"
    monkeypatch.setattr(postgresql_module, "psycopg", stub)
    monkeypatch.setattr("benchbox.core.tuning.metadata.TuningMetadataManager", FakeMetadataManager)
    config = UnifiedTuningConfiguration()
    adapter = PostgreSQLAdapter(tuning_enabled=True, unified_tuning_configuration=config)
    adapter._applied_tuning_ledger = AppliedTuningLedger()
    adapter.create_schema = Mock(return_value=0.0)
    adapter.load_data = Mock(return_value=({"t": 1}, 0.0, {}))
    adapter._create_enhanced_schema_creation_phase = Mock()
    adapter._create_enhanced_data_loading_phase = Mock()
    return adapter, config


def run_fresh_setup(adapter, config, tmp_path):
    benchmark = Mock()
    benchmark.output_dir = tmp_path
    return adapter._setup_fresh_database_phases(benchmark, Mock(), config)


class FakeMetadataManager:
    marker_save_failed = False

    def __init__(self, adapter, **_kwargs):
        pass

    def write_tuned_run_marker(self):
        return True

    def save_unified_tunings(self, config):
        return True


def test_noop_apply_does_not_claim_configuration_was_applied(postgres_adapter, console, tmp_path):
    adapter, config = postgres_adapter
    adapter.save_tuning_metadata = Mock(return_value=True)

    run_fresh_setup(adapter, config, tmp_path)

    lines = printed(console)
    assert not any("Unified tuning configuration applied" in line for line in lines)
    assert "Tuning apply step complete (0 statements; 0 intents not rendered at apply time)" in lines


def test_apply_step_summary_counts_only_what_the_apply_step_recorded(postgres_adapter, console, tmp_path):
    adapter, config = postgres_adapter
    adapter._applied_tuning_ledger.record("CREATE TABLE before_apply (id INT)", PHASE_DDL)
    adapter.save_tuning_metadata = Mock(return_value=True)

    def apply(_config, _connection):
        adapter._applied_tuning_ledger.record("CREATE INDEX i1 ON t (c)", PHASE_DDL)
        adapter._applied_tuning_ledger.record_dropped("partitioning:T", "applied at load time")

    adapter.apply_unified_tuning = apply

    run_fresh_setup(adapter, config, tmp_path)

    assert "Tuning apply step complete (1 statement; 1 intents not rendered at apply time)" in printed(console)


def test_apply_step_summary_reports_failed_statements_only_when_there_are_some(postgres_adapter, console, tmp_path):
    adapter, config = postgres_adapter
    adapter.save_tuning_metadata = Mock(return_value=True)

    def apply(_config, _connection):
        adapter._applied_tuning_ledger.record("CREATE INDEX i1 ON t (c)", PHASE_DDL)
        adapter._applied_tuning_ledger.record("CREATE INDEX i2 ON t (d)", PHASE_DDL, status="failed", error="boom")
        adapter._applied_tuning_ledger.record("CREATE INDEX i3 ON t (e)", PHASE_DDL, status="failed", error="boom")

    adapter.apply_unified_tuning = apply

    run_fresh_setup(adapter, config, tmp_path)

    assert "Tuning apply step complete (1 statement, 2 failed; 0 intents not rendered at apply time)" in printed(
        console
    )


def test_marker_save_failure_does_not_print_metadata_saved(postgres_adapter, console, monkeypatch, tmp_path):
    adapter, config = postgres_adapter

    class MarkerFailureManager(FakeMetadataManager):
        marker_save_failed = True

    monkeypatch.setattr("benchbox.core.tuning.metadata.TuningMetadataManager", MarkerFailureManager)

    run_fresh_setup(adapter, config, tmp_path)

    lines = printed(console)
    assert not any("✅ Tuning metadata saved" in line for line in lines)
    assert any("saved without section markers" in line for line in lines)


def test_successful_metadata_save_still_prints_saved(postgres_adapter, console, monkeypatch, tmp_path):
    adapter, config = postgres_adapter
    monkeypatch.setattr("benchbox.core.tuning.metadata.TuningMetadataManager", FakeMetadataManager)

    run_fresh_setup(adapter, config, tmp_path)

    assert "✅ Tuning metadata saved" in printed(console)


def test_failed_metadata_save_prints_warning_only(postgres_adapter, console, tmp_path):
    adapter, config = postgres_adapter
    adapter.save_tuning_metadata = Mock(return_value=False)

    run_fresh_setup(adapter, config, tmp_path)

    lines = printed(console)
    assert "⚠️ Failed to save tuning metadata" in lines
    assert not any("✅ Tuning metadata saved" in line for line in lines)


def test_marker_flag_resets_between_saves(postgres_adapter, monkeypatch):
    adapter, _config = postgres_adapter

    class MarkerFailureManager(FakeMetadataManager):
        marker_save_failed = True

    monkeypatch.setattr("benchbox.core.tuning.metadata.TuningMetadataManager", MarkerFailureManager)
    adapter.save_tuning_metadata(Mock())
    assert adapter._tuning_marker_save_failed is True

    monkeypatch.setattr("benchbox.core.tuning.metadata.TuningMetadataManager", FakeMetadataManager)
    adapter.save_tuning_metadata(Mock())
    assert adapter._tuning_marker_save_failed is False


def test_outcome_summary_reports_executed_failed_and_dropped_counts():
    ledger = AppliedTuningLedger()
    ledger.record("CREATE INDEX a ON t (c)", PHASE_DDL)
    ledger.record("CREATE INDEX b ON t (d)", PHASE_DDL)
    ledger.record("CREATE INDEX c ON t (e)", PHASE_DDL, status="failed", error="boom")
    ledger.record_dropped("partitioning:T", "applied at load time")

    assert ledger.describe_outcome() == "Tuning outcome after load: 2 executed, 1 failed, 1 dropped"


def test_outcome_summary_for_an_empty_ledger_reports_zero_everywhere():
    assert AppliedTuningLedger().describe_outcome() == "Tuning outcome after load: 0 executed, 0 failed, 0 dropped"


class _StopAfterOutcome(Exception):
    pass


def run_until_session_configuration(adapter, config, tmp_path, *, reused: bool):
    benchmark = Mock()
    benchmark.output_dir = tmp_path

    def connect(**_kwargs):
        adapter.database_was_reused = reused
        return Mock()

    adapter.get_effective_tuning_configuration = Mock(return_value=config)
    adapter.create_connection = connect
    adapter._setup_reused_database_phases = Mock(return_value=(0.0, Mock(), 0.0, {"t": 1}, Mock(), True))
    adapter._create_enhanced_validation_phase = Mock()
    adapter._check_validation_failure = Mock(return_value=False)
    adapter.configure_for_benchmark = Mock(side_effect=_StopAfterOutcome)
    adapter.save_tuning_metadata = Mock(return_value=True)
    adapter._create_enhanced_data_generation_phase = Mock()
    config.validate_for_platform_detailed = Mock(return_value=([], []))
    adapter._fold_layout_operations_into_ledger = Mock()
    try:
        adapter.run_enhanced_benchmark(benchmark)
    except _StopAfterOutcome:
        return
    raise AssertionError("run did not reach session configuration")


def test_post_load_outcome_is_printed_for_a_freshly_loaded_database(postgres_adapter, console, tmp_path):
    adapter, config = postgres_adapter

    run_until_session_configuration(adapter, config, tmp_path, reused=False)

    assert "Tuning outcome after load: 0 executed, 0 failed, 4 dropped" in printed(console)
    assert [item.intent for item in adapter._applied_tuning_ledger.dropped] == [
        "primary_keys",
        "foreign_keys",
        "unique_constraints",
        "check_constraints",
    ]


def test_post_load_outcome_is_not_printed_for_a_reused_database(postgres_adapter, console, tmp_path):
    adapter, config = postgres_adapter

    run_until_session_configuration(adapter, config, tmp_path, reused=True)

    assert not any("Tuning outcome after load" in line for line in printed(console))


class _LifecycleAdapter(PlatformAdapter):
    @property
    def platform_name(self) -> str:
        return "LifecyclePlatform"

    def get_target_dialect(self):
        return None

    @staticmethod
    def add_cli_arguments(parser) -> None:
        return None

    @classmethod
    def from_config(cls, config):
        return cls(**config)

    def create_connection(self, **connection_config):
        return Mock()

    def create_schema(self, benchmark, connection):
        return 0.0

    def load_data(self, benchmark, connection, data_dir):
        return {}, 0.0, None

    def configure_for_benchmark(self, connection, benchmark_type):
        return None

    def execute_query(self, connection, query, query_id, **kwargs):
        return {}

    def apply_platform_optimizations(self, platform_config, connection):
        return None

    def apply_constraint_configuration(self, primary_key_config, foreign_key_config, connection):
        return None


def validation_outcome(issues: list[str], *, database_empty: bool):
    return SimpleNamespace(
        warnings=[],
        issues=issues,
        is_valid=False,
        can_reuse=False,
        database_empty=database_empty,
    )


def handle_existing(tmp_path, outcome, caplog):
    adapter = _LifecycleAdapter()
    adapter.logger = logging.getLogger("tests.tuning_console_claims")
    db_file = tmp_path / "existing.db"
    db_file.write_text("db")
    adapter.check_database_exists = Mock(return_value=True)
    adapter._validate_database_compatibility = Mock(return_value=outcome)
    adapter._remove_database = Mock()
    with caplog.at_level(logging.ERROR, logger="tests.tuning_console_claims"):
        adapter.handle_existing_database(database_path=str(db_file))
    return adapter, [record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR]


def test_missing_tuning_metadata_on_an_empty_database_is_not_an_error(tmp_path, caplog):
    outcome = validation_outcome(
        [f"Tuning: {NO_TUNING_METADATA_ERROR}", "Missing tables: LINEITEM, ORDERS"], database_empty=True
    )

    adapter, errors = handle_existing(tmp_path, outcome, caplog)

    assert not any(NO_TUNING_METADATA_ERROR in message for message in errors)
    assert not any("Missing tables" in message for message in errors)
    adapter._remove_database.assert_called_once()


def test_missing_tables_on_a_populated_database_is_still_an_error(tmp_path, caplog):
    outcome = validation_outcome(["Missing tables: ORDERS"], database_empty=False)

    _adapter, errors = handle_existing(tmp_path, outcome, caplog)

    assert any("Missing tables" in message for message in errors)


def test_missing_tuning_metadata_on_a_populated_database_is_still_an_error(tmp_path, caplog):
    outcome = validation_outcome([f"Tuning: {NO_TUNING_METADATA_ERROR}"], database_empty=False)

    _adapter, errors = handle_existing(tmp_path, outcome, caplog)

    assert any(NO_TUNING_METADATA_ERROR in message for message in errors)


def test_other_tuning_errors_on_an_empty_database_are_still_errors(tmp_path, caplog):
    outcome = validation_outcome(["Tuning: Failed to load tuning metadata: boom"], database_empty=True)

    _adapter, errors = handle_existing(tmp_path, outcome, caplog)

    assert any("Failed to load tuning metadata: boom" in message for message in errors)


class _SchemaValidator(SchemaValidator):
    def __init__(self, expected: set[str], existing: set[str]):
        super().__init__(Mock(), {})
        self._expected = expected
        self._existing = existing

    def _get_expected_tables(self):
        return self._expected

    def _get_existing_tables(self, connection):
        return self._existing


def test_schema_validator_marks_database_empty_only_when_every_table_is_missing():
    assert _SchemaValidator({"a", "b"}, set()).validate(Mock()).database_empty is True
    assert _SchemaValidator({"a", "b"}, {"a"}).validate(Mock()).database_empty is False
    assert _SchemaValidator({"a", "b"}, {"a", "b"}).validate(Mock()).database_empty is False


def test_database_validator_carries_database_empty_into_its_result():
    adapter = Mock()
    adapter._validate_database_tunings.return_value = SimpleNamespace(
        warnings=[], errors=[NO_TUNING_METADATA_ERROR], is_valid=False
    )
    validator = DatabaseValidator(adapter, {})
    validator.connection_validator.create_temporary_connection = MagicMock()
    validator.schema_validator = _SchemaValidator({"a"}, set())

    result = validator.validate()

    assert isinstance(result, DatabaseValidationResult)
    assert result.database_empty is True
    assert f"Tuning: {NO_TUNING_METADATA_ERROR}" in result.issues
