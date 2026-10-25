from __future__ import annotations

import logging
import random
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import pytest

from benchbox.core.errors import PlanCaptureError, SerializationError
from benchbox.platforms.base import PlatformAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _TrackingAdapter(PlatformAdapter):
    def __init__(self, **config: Any):
        super().__init__(**config)
        self._database_path = config.get("database_path")
        self.server_exists = config.get("server_exists", False)
        self.drop_calls: list[dict[str, Any]] = []

    @staticmethod
    def add_cli_arguments(parser) -> None:
        return None

    @classmethod
    def from_config(cls, config: dict[str, Any]):
        return cls(**config)

    @property
    def platform_name(self) -> str:
        return "TrackingPlatform"

    def get_target_dialect(self) -> str | None:
        return None

    def create_connection(self, **connection_config):
        return Mock()

    def create_schema(self, benchmark, connection):
        return 0.0

    def load_data(self, benchmark, connection, data_dir):
        return {}, 0.0, None

    def configure_for_benchmark(self, connection, benchmark_type):
        return None

    def execute_query(self, connection, query, query_id, **kwargs):
        return {
            "query_id": query_id,
            "status": "SUCCESS",
            "execution_time_seconds": 0.0,
            "rows_returned": 0,
        }

    def apply_platform_optimizations(self, platform_config, connection):
        return None

    def apply_constraint_configuration(self, primary_key_config, foreign_key_config, connection):
        return None

    def get_database_path(self, **connection_config) -> str | None:
        return connection_config.get("database_path", self._database_path)

    def check_server_database_exists(self, **connection_config) -> bool:
        return connection_config.get("server_exists", self.server_exists)

    def drop_database(self, **connection_config) -> None:
        self.drop_calls.append(connection_config)


@pytest.fixture()
def adapter():
    return _TrackingAdapter()


def test_validate_tuning_configuration_for_platform_returns_empty_without_config(adapter):
    assert adapter.validate_tuning_configuration_for_platform() == []


def test_validate_tuning_configuration_for_platform_delegates_to_effective_config(adapter):
    adapter.unified_tuning_configuration = SimpleNamespace(
        validate_for_platform=lambda platform_name: [f"{platform_name} mismatch"]
    )

    assert adapter.validate_tuning_configuration_for_platform() == ["trackingplatform mismatch"]


def test_create_external_tables_raises_not_implemented(adapter, tmp_path):
    with pytest.raises(NotImplementedError, match="does not support external table mode"):
        adapter.create_external_tables(benchmark=object(), connection=object(), data_dir=tmp_path)


def test_upload_manifest_returns_false_by_default(adapter, tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}")

    assert adapter.upload_manifest(manifest, "s3://bucket/path") is False


def test_validate_loaded_data_returns_passthrough_when_disabled(adapter):
    result = adapter.validate_loaded_data(connection=object(), benchmark_type="tpch", scale_factor=1.0)

    assert result.is_valid is True
    assert result.warnings == ["Validation disabled"]
    assert result.details["validation_enabled"] is False


def test_remove_database_refuses_to_drop_a_database_holding_other_benchmarks_tables(adapter):
    adapter.drop_database = Mock()

    with pytest.raises(RuntimeError, match="refusing to drop the database.*orders_staging, txn_log"):
        adapter._remove_database(False, "", foreign_tables=("orders_staging", "txn_log"), database="shared")

    adapter.drop_database.assert_not_called()


def test_remove_database_names_only_the_first_foreign_tables(adapter):
    tables = tuple(f"t{index:02d}" for index in range(14))

    with pytest.raises(RuntimeError) as error:
        adapter._remove_database(False, "", foreign_tables=tables, database="shared")

    assert "t09" in str(error.value)
    assert "t10" not in str(error.value)
    assert "and 4 more" in str(error.value)


def test_an_in_place_reset_takes_precedence_over_the_foreign_table_refusal(adapter):
    adapter.reset_database_in_place = Mock(return_value=True)
    adapter.drop_database = Mock()

    adapter._remove_database(False, "", foreign_tables=("other",), database="shared")

    adapter.drop_database.assert_not_called()


def test_a_validation_mismatch_hands_the_foreign_tables_to_the_removal():
    adapter = _TrackingAdapter(server_exists=True)
    adapter._validate_database_compatibility = Mock(
        return_value=SimpleNamespace(
            is_valid=False, can_reuse=False, warnings=[], issues=["Missing tables: lineitem"], foreign_tables=("other",)
        )
    )

    with pytest.raises(RuntimeError, match="refusing to drop the database.*other"):
        adapter.handle_existing_database(database="shared")

    assert adapter.drop_calls == []


def test_force_recreate_still_drops_the_whole_database_on_request():
    adapter = _TrackingAdapter(server_exists=True)
    adapter.force_recreate = True
    adapter._validate_database_compatibility = Mock()

    adapter.handle_existing_database(database="shared")

    assert adapter.drop_calls == [{"database": "shared"}]
    adapter._validate_database_compatibility.assert_not_called()


def test_a_mismatch_without_foreign_tables_still_recreates_the_database():
    adapter = _TrackingAdapter(server_exists=True)
    adapter._validate_database_compatibility = Mock(
        return_value=SimpleNamespace(is_valid=False, can_reuse=False, warnings=[], issues=["Missing tables: lineitem"])
    )

    adapter.handle_existing_database(database="shared")

    assert adapter.drop_calls == [{"database": "shared"}]


def test_validate_loaded_data_uses_validation_service_when_enabled(adapter, monkeypatch):
    class FakeService:
        def run_database(self, connection, benchmark_type, scale_factor):
            return SimpleNamespace(
                is_valid=True,
                errors=[],
                warnings=[],
                details={"benchmark_type": benchmark_type, "scale_factor": scale_factor},
            )

    import benchbox.core.validation as validation_module

    adapter.enable_validation = True
    monkeypatch.setattr(validation_module, "ValidationService", FakeService)

    result = adapter.validate_loaded_data(connection="conn", benchmark_type="tpcds", scale_factor=3.0)

    assert result.is_valid is True
    assert result.details["platform"] == "TrackingPlatform"
    assert result.details["validation_enabled"] is True


def test_check_database_exists_uses_file_path_when_available(tmp_path):
    db_file = tmp_path / "example.duckdb"
    db_file.write_text("db")
    adapter = _TrackingAdapter(database_path=str(db_file))

    assert adapter.check_database_exists() is True


def test_check_database_exists_ignores_memory_path_and_uses_server_check():
    adapter = _TrackingAdapter(database_path=":memory:", server_exists=True)

    assert adapter.check_database_exists() is True


def test_check_database_exists_delegates_to_server_when_no_file_path():
    adapter = _TrackingAdapter(server_exists=True)

    assert adapter.check_database_exists() is True


def test_handle_existing_database_skips_in_dry_run(adapter):
    adapter.dry_run = True
    adapter.check_database_exists = Mock(return_value=True)

    adapter.handle_existing_database()

    adapter.check_database_exists.assert_not_called()


def test_handle_existing_database_skips_managed_cloud_database(adapter):
    adapter.skip_database_management = True
    adapter.check_benchmark_tables_exist = Mock(wraps=adapter.check_benchmark_tables_exist)
    adapter.check_database_exists = Mock(return_value=True)

    adapter.handle_existing_database()

    adapter.check_benchmark_tables_exist.assert_called_once()
    adapter.check_database_exists.assert_not_called()
    assert adapter.database_was_reused is True


def test_handle_existing_database_reuses_managed_database_when_tables_present(adapter):
    adapter.skip_database_management = True
    adapter.check_benchmark_tables_exist = Mock(return_value=True)
    adapter.check_database_exists = Mock(return_value=True)

    adapter.handle_existing_database()

    adapter.check_benchmark_tables_exist.assert_called_once()
    adapter.check_database_exists.assert_not_called()
    assert adapter.database_was_reused is True


def test_handle_existing_database_treats_managed_database_as_fresh_when_tables_missing(adapter):
    adapter.skip_database_management = True
    adapter.check_benchmark_tables_exist = Mock(return_value=False)
    adapter.check_database_exists = Mock(return_value=True)

    adapter.handle_existing_database()

    adapter.check_benchmark_tables_exist.assert_called_once()
    adapter.check_database_exists.assert_not_called()
    assert adapter.database_was_reused is False


def test_handle_existing_database_dry_run_skips_managed_table_hook(adapter):
    adapter.dry_run = True
    adapter.skip_database_management = True
    adapter.check_benchmark_tables_exist = Mock(return_value=False)

    adapter.handle_existing_database()

    adapter.check_benchmark_tables_exist.assert_not_called()
    assert adapter.database_was_reused is False


def test_get_expected_tables_normalizes_schema_dict(adapter):
    benchmark = SimpleNamespace(get_schema=lambda: {"Orders": {}, "LINEITEM": {}})

    assert adapter._get_expected_tables(benchmark) == ["orders", "lineitem"]


def test_get_expected_tables_normalizes_schema_list_of_dicts(adapter):
    benchmark = SimpleNamespace(get_schema=lambda: [{"name": "Orders"}, {"name": "LINEITEM"}])

    assert adapter._get_expected_tables(benchmark) == ["orders", "lineitem"]


def test_get_expected_tables_normalizes_schema_list_of_names(adapter):
    benchmark = SimpleNamespace(get_schema=lambda: ["Orders", "LINEITEM"])

    assert adapter._get_expected_tables(benchmark) == ["orders", "lineitem"]


def test_get_expected_tables_normalizes_table_name_api(adapter):
    benchmark = SimpleNamespace(get_table_names=lambda: ["Orders", "LINEITEM"])

    assert adapter._get_expected_tables(benchmark) == ["orders", "lineitem"]


def test_handle_existing_database_skips_reuse_logic_while_validating(adapter):
    adapter._validating_database = True
    adapter.check_database_exists = Mock(return_value=True)

    adapter.handle_existing_database()

    adapter.check_database_exists.assert_not_called()


def test_handle_existing_database_returns_when_database_missing(adapter):
    adapter.check_database_exists = Mock(return_value=False)

    adapter.handle_existing_database()

    adapter.check_database_exists.assert_called_once()
    assert adapter.database_was_reused is False


def test_handle_existing_database_force_recreates_existing_database(adapter, tmp_path):
    db_file = tmp_path / "example.db"
    db_file.write_text("db")
    adapter.force_recreate = True
    adapter.check_database_exists = Mock(return_value=True)
    adapter._remove_database = Mock()

    adapter.handle_existing_database(database_path=str(db_file))

    adapter._remove_database.assert_called_once_with(True, str(db_file), database_path=str(db_file))


def test_handle_existing_database_reuses_compatible_database(adapter, tmp_path):
    db_file = tmp_path / "example-reuse.db"
    db_file.write_text("db")
    adapter.check_database_exists = Mock(return_value=True)
    adapter._validate_database_compatibility = Mock(
        return_value=SimpleNamespace(
            warnings=[],
            issues=[],
            is_valid=True,
            can_reuse=True,
        )
    )
    adapter._remove_database = Mock()

    adapter.handle_existing_database(database_path=str(db_file))

    assert adapter.database_was_reused is True
    adapter._remove_database.assert_not_called()


def test_handle_existing_database_recreates_incompatible_database(adapter, tmp_path):
    db_file = tmp_path / "example-recreate.db"
    db_file.write_text("db")
    adapter.check_database_exists = Mock(return_value=True)
    adapter._validate_database_compatibility = Mock(
        return_value=SimpleNamespace(
            warnings=["stale stats"],
            issues=["wrong schema"],
            is_valid=False,
            can_reuse=False,
        )
    )
    adapter._remove_database = Mock()

    adapter.handle_existing_database(database_path=str(db_file))

    assert adapter.database_was_reused is False
    adapter._remove_database.assert_called_once()


def test_handle_existing_database_recreates_for_extra_tuning_drift(adapter, tmp_path):
    db_file = tmp_path / "extra-tuning.db"
    db_file.write_text("db")
    adapter.check_database_exists = Mock(return_value=True)
    adapter._validate_database_compatibility = Mock(
        return_value=SimpleNamespace(
            warnings=[],
            issues=["Tuning: Unexpected tuning found for table 'lineitem' in database"],
            is_valid=False,
            can_reuse=False,
        )
    )
    adapter._remove_database = Mock()

    adapter.handle_existing_database(database_path=str(db_file))

    assert adapter.database_was_reused is False
    adapter._remove_database.assert_called_once()


def test_remove_database_deletes_file(tmp_path):
    db_file = tmp_path / "sample.db"
    db_file.write_text("db")
    adapter = _TrackingAdapter()

    adapter._remove_database(True, str(db_file))

    assert not db_file.exists()


def test_remove_database_deletes_directory(tmp_path):
    db_dir = tmp_path / "datafusion_db"
    db_dir.mkdir()
    (db_dir / "part.parquet").write_text("data")
    adapter = _TrackingAdapter()

    adapter._remove_database(True, str(db_dir))

    assert not db_dir.exists()


def test_remove_database_calls_drop_database_for_server_platform():
    adapter = _TrackingAdapter()

    adapter._remove_database(False, None, database="benchbox")

    assert adapter.drop_calls == [{"database": "benchbox"}]


def test_remove_database_wraps_errors(tmp_path):
    missing = tmp_path / "missing.db"
    adapter = _TrackingAdapter()
    adapter.drop_database = Mock(side_effect=RuntimeError("boom"))

    with pytest.raises(RuntimeError, match="Could not remove existing database: boom"):
        adapter._remove_database(False, str(missing))


def test_format_execution_time_uses_expected_units(adapter):
    assert adapter._format_execution_time(0.0005).endswith("μs")
    assert adapter._format_execution_time(0.5).endswith("ms")
    assert adapter._format_execution_time(5.0).endswith("s")
    assert adapter._format_execution_time(65.0).startswith("1:")


def test_normalize_and_validate_file_paths_filters_missing_and_empty(adapter, tmp_path):
    good = tmp_path / "good.tbl"
    empty = tmp_path / "empty.tbl"
    missing = tmp_path / "missing.tbl"
    good.write_text("row\n")
    empty.write_text("")

    assert adapter._normalize_and_validate_file_paths([good, empty, missing]) == [good]
    assert adapter._normalize_and_validate_file_paths(good) == [good]


def test_display_query_plan_if_enabled_renders_output(adapter, monkeypatch):
    adapter.show_query_plans = True
    adapter.get_query_plan = Mock(return_value="SEQ SCAN test")
    fake_console = Mock()

    monkeypatch.setattr("benchbox.platforms.base.result_capture.quiet_console", fake_console)

    adapter.display_query_plan_if_enabled(connection=object(), query="SELECT 1", query_id="Q1")

    assert fake_console.print.call_count >= 3


def test_display_query_plan_if_enabled_swallows_plan_errors(adapter):
    adapter.show_query_plans = True
    adapter.get_query_plan = Mock(side_effect=RuntimeError("plan failed"))
    adapter.logger = logging.getLogger("tests.base_adapter_database_management")
    adapter.logger.debug = Mock()

    adapter.display_query_plan_if_enabled(connection=object(), query="SELECT 1", query_id="Q1")

    adapter.logger.debug.assert_called_once()


class TestAdapterPlanCaptureGaps:
    def test_capture_query_plan_disabled(self, adapter):
        adapter.capture_plans = False
        plan, timing = adapter.capture_query_plan(Mock(), "SELECT 1", "Q1")
        assert plan is None
        assert timing == 0.0

    def test_capture_query_plan_filtered_out(self, adapter):
        adapter.capture_plans = True
        adapter.plan_query_filter = {"Q2"}
        plan, timing = adapter.capture_query_plan(Mock(), "SELECT 1", "Q1")
        assert plan is None
        assert timing == 0.0

    def test_capture_query_plan_query_filter(self, adapter):
        adapter.capture_plans = True
        adapter.plan_query_filter = {"Q1"}

        mock_plan = Mock()
        mock_plan.estimate_serialized_size.return_value = 1024
        mock_parser = Mock()
        mock_parser.parse_explain_output.return_value = mock_plan

        with patch.object(adapter, "get_query_plan", return_value="PLAN"):
            with patch.object(adapter, "get_query_plan_parser", return_value=mock_parser):
                plan, _ = adapter.capture_query_plan(Mock(), "SELECT 1", "Q1")
                assert plan == mock_plan
                plan2, timing2 = adapter.capture_query_plan(Mock(), "SELECT 1", "Q2")
                assert plan2 is None
                assert timing2 == 0.0

    def test_capture_query_plan_timeout(self, adapter):
        adapter.capture_plans = True
        adapter.plan_capture_timeout_seconds = 0.01
        adapter.logger = logging.getLogger("tests.base_adapter")

        def slow_plan(*args, **kwargs):
            time.sleep(0.05)
            return "SLOW PLAN"

        adapter.get_query_plan = slow_plan

        plan, timing = adapter.capture_query_plan(Mock(), "SELECT 1", "Q1")
        assert plan is None
        assert adapter.plan_capture_failures == 1
        assert any("timed out" in err["message"] for err in adapter.plan_capture_errors)

    def test_capture_query_plan_exception(self, adapter):
        adapter.capture_plans = True
        adapter.get_query_plan = Mock(side_effect=RuntimeError("explain failed"))

        plan, timing = adapter.capture_query_plan(Mock(), "SELECT 1", "Q1")
        assert plan is None
        assert adapter.plan_capture_failures == 1
        assert any("explain failed" in err["message"] for err in adapter.plan_capture_errors)

    def test_capture_query_plan_no_output(self, adapter):
        adapter.capture_plans = True
        adapter.get_query_plan = Mock(return_value=None)

        plan, timing = adapter.capture_query_plan(Mock(), "SELECT 1", "Q1")
        assert plan is None
        assert any("No EXPLAIN output" in err["message"] for err in adapter.plan_capture_errors)

    def test_capture_query_plan_no_parser(self, adapter):
        adapter.capture_plans = True
        adapter.logger = logging.getLogger("tests.base_adapter")
        adapter.get_query_plan = Mock(return_value="SOME PLAN")
        adapter.get_query_plan_parser = Mock(return_value=None)

        plan, timing = adapter.capture_query_plan(Mock(), "SELECT 1", "Q1")
        assert plan is None
        assert any("No parser available" in err["message"] for err in adapter.plan_capture_errors)

    def test_capture_query_plan_parse_exception(self, adapter):
        adapter.capture_plans = True
        adapter.get_query_plan = Mock(return_value="SOME PLAN")
        mock_parser = Mock()
        mock_parser.parse_explain_output.side_effect = ValueError("parse boom")
        adapter.get_query_plan_parser = Mock(return_value=mock_parser)

        plan, timing = adapter.capture_query_plan(Mock(), "SELECT 1", "Q1")
        assert plan is None
        assert any("parse boom" in err["message"] for err in adapter.plan_capture_errors)

    def test_capture_query_plan_parse_none(self, adapter):
        adapter.capture_plans = True
        adapter.get_query_plan = Mock(return_value="SOME PLAN")
        mock_parser = Mock()
        mock_parser.parse_explain_output.return_value = None
        adapter.get_query_plan_parser = Mock(return_value=mock_parser)

        plan, timing = adapter.capture_query_plan(Mock(), "SELECT 1", "Q1")
        assert plan is None
        assert any("Parser returned no plan" in err["message"] for err in adapter.plan_capture_errors)

    def test_capture_query_plan_serialization_error(self, adapter):
        adapter.capture_plans = True
        adapter.get_query_plan = Mock(return_value="SOME PLAN")
        mock_parser = Mock()
        mock_plan = Mock()
        mock_plan.estimate_serialized_size.side_effect = SerializationError("serial boom")
        mock_parser.parse_explain_output.return_value = mock_plan
        adapter.get_query_plan_parser = Mock(return_value=mock_parser)

        plan, timing = adapter.capture_query_plan(Mock(), "SELECT 1", "Q1")
        assert plan is None
        assert any("serial boom" in err["message"] for err in adapter.plan_capture_errors)

    def test_capture_query_plan_large_plan_warning(self, adapter, caplog):
        adapter.capture_plans = True
        adapter.logger = logging.getLogger("tests.base_adapter")
        adapter.get_query_plan = Mock(return_value="SOME PLAN")
        mock_parser = Mock()
        mock_plan = Mock()
        mock_plan.estimate_serialized_size.return_value = 200 * 1024
        mock_parser.parse_explain_output.return_value = mock_plan
        adapter.get_query_plan_parser = Mock(return_value=mock_parser)

        with caplog.at_level(logging.WARNING, logger="tests.base_adapter"):
            plan, timing = adapter.capture_query_plan(Mock(), "SELECT 1", "Q1")

        assert plan == mock_plan
        assert "Large query plan" in caplog.text
        assert "external plan storage" not in caplog.text

    def test_plan_max_depth_defaults_and_is_configurable(self):
        from benchbox.core.results.query_plan_models import DEFAULT_PLAN_MAX_DEPTH

        assert _TrackingAdapter().plan_max_depth == DEFAULT_PLAN_MAX_DEPTH
        assert _TrackingAdapter(plan_max_depth=12).plan_max_depth == 12

    def test_capture_query_plan_sizes_with_configured_plan_max_depth(self):
        adapter = _TrackingAdapter(plan_max_depth=7)
        adapter.capture_plans = True
        adapter.get_query_plan = Mock(return_value="SOME PLAN")
        mock_parser = Mock()
        mock_plan = Mock()
        mock_plan.estimate_serialized_size.return_value = 1024
        mock_parser.parse_explain_output.return_value = mock_plan
        adapter.get_query_plan_parser = Mock(return_value=mock_parser)

        adapter.capture_query_plan(Mock(), "SELECT 1", "Q1")

        mock_plan.estimate_serialized_size.assert_called_once_with(max_depth=7)


class TestAdapterValidationGaps:
    def test_validate_tuning_metadata_exception(self, adapter):
        adapter.create_connection = Mock(side_effect=RuntimeError("conn boom"))

        result = adapter._validate_database_tunings(database="test")
        assert result.errors
        assert any("Failed to validate database tunings: conn boom" in err for err in result.errors)

    def test_validate_tuning_metadata_no_tuning_expected_with_existing(self, adapter, monkeypatch):
        class FakeMetadataManager:
            def __init__(self, adapter, **_kwargs):
                self.last_load_error = None

            def load_unified_tunings(self):
                return {"some": "tuning"}

            def validate_unified_tunings(self, config):
                return Mock()

        monkeypatch.setattr("benchbox.core.tuning.metadata.TuningMetadataManager", FakeMetadataManager)
        adapter.get_effective_tuning_configuration = Mock(return_value=None)
        adapter.tuning_enabled = False

        result = adapter._validate_database_tunings(database="test")
        assert any("Database contains tuning metadata but no tunings expected" in warn for warn in result.warnings)
        assert result.is_valid is False
        assert any("notuning" in error for error in result.errors)

    def test_save_tuning_metadata_disabled_or_no_config(self, adapter):
        adapter.tuning_enabled = False
        assert adapter.save_tuning_metadata(Mock()) is True

        adapter.tuning_enabled = True
        adapter.get_effective_tuning_configuration = Mock(return_value=None)
        assert adapter.save_tuning_metadata(Mock()) is True

    def test_save_tuning_metadata_exception(self, adapter, caplog, monkeypatch):
        adapter.tuning_enabled = True
        adapter.get_effective_tuning_configuration = Mock(return_value={"some": "config"})
        adapter.logger = logging.getLogger("tests.base_adapter")

        class FakeMetadataManager:
            def __init__(self, adapter, **_kwargs):
                pass

            def save_unified_tunings(self, config):
                raise RuntimeError("save boom")

        monkeypatch.setattr("benchbox.core.tuning.metadata.TuningMetadataManager", FakeMetadataManager)

        with caplog.at_level(logging.ERROR, logger="tests.base_adapter"):
            result = adapter.save_tuning_metadata(Mock())

        assert result is False
        assert "Failed to save tuning metadata: save boom" in caplog.text

    def test_save_tuning_metadata_marker_failure_is_nonfatal_and_stashed(self, adapter, monkeypatch):
        adapter.tuning_enabled = True
        adapter.get_effective_tuning_configuration = Mock(return_value={"some": "config"})

        class FakeMetadataManager:
            marker_save_failed = True

            def __init__(self, adapter, **_kwargs):
                pass

            def save_unified_tunings(self, config):
                return True

        monkeypatch.setattr("benchbox.core.tuning.metadata.TuningMetadataManager", FakeMetadataManager)

        assert adapter.save_tuning_metadata(Mock()) is True
        assert any("section markers were not saved" in warning for warning in adapter._drift_validation_result.warnings)

    def test_validate_row_counts_success(self, adapter, monkeypatch):
        class FakeValidator:
            def __init__(self, adapter):
                pass

            def validate_row_counts(self, expected):
                res = Mock()
                res.is_valid = True
                return res

        monkeypatch.setattr("benchbox.core.validation.data.DataValidator", FakeValidator)
        adapter.log_operation_start = Mock()
        adapter.log_operation_complete = Mock()

        result = adapter.validate_row_counts(Mock(), {"t1": 100})
        assert result.is_valid is True
        adapter.log_operation_complete.assert_called_once()

    def test_validate_row_counts_failure_log(self, adapter, monkeypatch):
        class FakeValidator:
            def __init__(self, adapter):
                pass

            def validate_row_counts(self, expected):
                res = Mock()
                res.is_valid = False
                res.issues = ["mismatch"]
                return res

        monkeypatch.setattr("benchbox.core.validation.data.DataValidator", FakeValidator)
        adapter.log_verbose = Mock()

        result = adapter.validate_row_counts(Mock(), {"t1": 100})
        assert not result.is_valid
        assert any("row count validation failed" in str(call).lower() for call in adapter.log_verbose.call_args_list)

    def test_validate_row_counts_exception(self, adapter, caplog, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("validate boom")

        monkeypatch.setattr("benchbox.core.validation.data.DataValidator", boom)
        adapter.logger = logging.getLogger("tests.base_adapter")

        with caplog.at_level(logging.ERROR, logger="tests.base_adapter"):
            result = adapter.validate_row_counts(Mock(), {"t1": 100})

        assert any("Row count validation failed: validate boom" in err for err in result.errors)
        assert "Failed to validate row counts: validate boom" in caplog.text


class TestAdapterSchemaGaps:
    def test_execute_schema_statements_partial_failure(self, adapter, caplog):
        adapter.logger = logging.getLogger("tests.base_adapter")
        adapter.log_verbose = Mock()
        adapter.log_very_verbose = Mock()

        cursor = Mock()
        cursor.execute.side_effect = [None, RuntimeError("schema boom")]

        statements = [
            "CREATE TABLE t1 (a int)",
            "CREATE TABLE t2 (b int)",
        ]

        with caplog.at_level(logging.ERROR, logger="tests.base_adapter"):
            with pytest.raises(RuntimeError, match="Failed to create 1 table"):
                adapter._execute_schema_statements(statements, cursor)

        assert "Failed to create table t2" in caplog.text
        adapter.log_verbose.assert_called_with("Schema creation: 1 tables created, 1 failed")

    def test_execute_schema_statements_empty_statements(self, adapter):
        cursor = Mock()
        statements = ["", "  ", "\n"]

        created, failed = adapter._execute_schema_statements(statements, cursor)
        assert created == 0
        assert not failed
        cursor.execute.assert_not_called()


class TestAdapterConnectionGaps:
    def test_adapter_connection_maintenance_mode(self, adapter):
        from benchbox.platforms.base.adapter import PlatformAdapterConnection

        mock_conn = Mock()
        conn = PlatformAdapterConnection(mock_conn, adapter)
        conn._maintenance_mode = True

        conn.execute("SELECT 1")
        mock_conn.execute.assert_called_with("SELECT 1")

        conn.execute("SELECT ?", (1,))
        mock_conn.execute.assert_called_with("SELECT ?", (1,))

    def test_adapter_connection_validation_mode_parameterized(self, adapter):
        from benchbox.platforms.base.adapter import PlatformAdapterConnection

        mock_conn = Mock()
        conn = PlatformAdapterConnection(mock_conn, adapter)

        conn.execute("SELECT ?", (1,))
        mock_conn.execute.assert_called_with("SELECT ?", (1,))

    def test_adapter_connection_debug_log_swallows_error(self, adapter, monkeypatch):
        from benchbox.platforms.base.adapter import PlatformAdapterConnection

        mock_conn = Mock()
        conn = PlatformAdapterConnection(mock_conn, adapter)

        def boom(*args, **kwargs):
            raise RuntimeError("io boom")

        monkeypatch.setattr("builtins.open", boom)

        adapter.execute_query = Mock(return_value={"rows": []})

        conn.execute("SELECT 1")
        adapter.execute_query.assert_called_once()

    def test_adapter_cursor_extract_rows_first_row(self):
        from benchbox.platforms.base.adapter import PlatformAdapterCursor

        res = {"first_row": (1, "a")}
        cursor = PlatformAdapterCursor(res)
        assert cursor.fetchall() == [(1, "a")]
        assert cursor.fetchone() == (1, "a")

    def test_adapter_cursor_extract_rows_row_count_only(self):
        from benchbox.platforms.base.adapter import PlatformAdapterCursor

        res = {"rows_returned": 2}
        cursor = PlatformAdapterCursor(res)
        assert cursor.fetchall() == [(None,), (None,)]

    def test_adapter_connection_proxy_methods(self, adapter):
        from benchbox.platforms.base.adapter import PlatformAdapterConnection

        mock_conn = Mock()
        conn = PlatformAdapterConnection(mock_conn, adapter)

        conn.commit()
        mock_conn.commit.assert_called_once()

        conn.rollback()
        mock_conn.rollback.assert_called_once()

        conn.close()
        mock_conn.close.assert_called_once()


class TestAdapterSummarizePerformance:
    def test_empty_results_returns_zeroed_summary(self, adapter):
        summary = adapter._summarize_performance_characteristics([], 1.0, 0)
        assert summary["total_queries"] == 0
        assert summary["successful_queries"] == 0
        assert summary["rows_returned_total"] == 0

    def test_none_results_returns_zeroed_summary(self, adapter):
        summary = adapter._summarize_performance_characteristics(None, 1.0, 0)
        assert summary["total_queries"] == 0

    def test_duration_field_fallback(self, adapter):
        results = [{"status": "SUCCESS", "duration": 1.5, "rows_returned": 5}]
        summary = adapter._summarize_performance_characteristics(results, 2.0, 0)
        assert summary["successful_queries"] == 1
        assert summary["average_success_query_time_ms"] is not None

    def test_non_convertible_rows_returned_defaults_to_zero(self, adapter):
        results = [{"status": "SUCCESS", "execution_time_seconds": 0.5, "rows_returned": "bad"}]
        summary = adapter._summarize_performance_characteristics(results, 1.0, 0)
        assert summary["rows_returned_total"] == 0

    def test_null_rows_returned_defaults_to_zero(self, adapter):
        results = [{"status": "SUCCESS", "execution_time_seconds": 0.5, "rows_returned": None}]
        summary = adapter._summarize_performance_characteristics(results, 1.0, 0)
        assert summary["rows_returned_total"] == 0

    def test_non_dict_result_uses_attribute_extraction(self, adapter):
        class BareResult:
            status = "SUCCESS"

        results = [BareResult()]
        summary = adapter._summarize_performance_characteristics(results, 1.0, 0)
        assert summary["total_queries"] == 1
        assert summary["average_query_time_ms"] is None


def test_remove_database_prefers_in_place_reset_over_drop(adapter):
    adapter.reset_database_in_place = Mock(return_value=True)
    adapter.drop_database = Mock()

    adapter._remove_database(False, "", schema="s")

    adapter.reset_database_in_place.assert_called_once_with(schema="s")
    adapter.drop_database.assert_not_called()


def test_remove_database_drops_when_in_place_reset_declines(adapter):
    adapter.reset_database_in_place = Mock(return_value=False)
    adapter.drop_database = Mock()

    adapter._remove_database(False, "", schema="s")

    adapter.drop_database.assert_called_once_with(schema="s")


class TestOncePerRunExistingDatabaseDecision:
    def _invalid_adapter(self):
        adapter = _TrackingAdapter(server_exists=True)
        adapter._validate_database_compatibility = Mock(
            return_value=SimpleNamespace(is_valid=False, can_reuse=False, warnings=[], issues=[])
        )
        return adapter

    def test_second_call_skips_recreate_decision(self):
        adapter = self._invalid_adapter()

        adapter.handle_existing_database()
        assert len(adapter.drop_calls) == 1

        adapter.handle_existing_database()
        assert len(adapter.drop_calls) == 1

    def test_run_reset_reenables_decision(self):
        adapter = self._invalid_adapter()

        adapter.handle_existing_database()
        adapter._reset_run_scoped_state()
        adapter.handle_existing_database()

        assert len(adapter.drop_calls) == 2

    def test_validation_context_calls_do_not_consume_the_decision(self):
        adapter = self._invalid_adapter()

        with adapter.non_destructive_connection_context():
            adapter.handle_existing_database()
        assert adapter.drop_calls == []

        adapter.handle_existing_database()
        assert len(adapter.drop_calls) == 1

    def test_non_destructive_context_guards_and_restores(self):
        adapter = _TrackingAdapter()
        adapter._validating_database = False

        with adapter.non_destructive_connection_context():
            assert adapter._validating_database is True
            with adapter.non_destructive_connection_context():
                pass
            assert adapter._validating_database is True

        assert adapter._validating_database is False
