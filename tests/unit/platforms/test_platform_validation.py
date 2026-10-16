from unittest.mock import Mock, patch

import pytest

from benchbox.platforms.base.models import DatabaseValidationResult
from benchbox.platforms.base.validation import (
    ConnectionValidator,
    DatabaseValidator,
    GenericRowCountStrategy,
    JoinOrderRowCountStrategy,
    RowCountValidator,
    SchemaValidator,
    SSBRowCountStrategy,
    TPCDSRowCountStrategy,
    TPCHRowCountStrategy,
    TuningValidator,
    ValidationResult,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestValidationResult:
    def test_initialization(self):

        result = ValidationResult(is_valid=True)
        assert result.is_valid is True
        assert result.errors == []
        assert result.warnings == []

    def test_add_error(self):

        result = ValidationResult(is_valid=True)
        result.add_error("Test error")
        assert result.is_valid is False
        assert "Test error" in result.errors

    def test_add_warning(self):

        result = ValidationResult(is_valid=True)
        result.add_warning("Test warning")
        assert result.is_valid is True
        assert "Test warning" in result.warnings


class TestConnectionValidator:
    def test_create_temporary_connection_with_direct_connection(self):

        mock_adapter = Mock()
        mock_adapter._create_direct_connection = Mock(return_value="mock_connection")
        mock_adapter.get_database_path = Mock(return_value="/path/to/db")
        mock_adapter.close_connection = Mock()

        validator = ConnectionValidator(mock_adapter, {"database": "test"})

        with validator.create_temporary_connection() as conn:
            assert conn == "mock_connection"

        mock_adapter._create_direct_connection.assert_called_once_with(database="test")
        mock_adapter.close_connection.assert_called_once_with("mock_connection")

    def test_create_temporary_connection_with_flag(self):

        mock_adapter = Mock(spec=["get_database_path", "create_connection", "close_connection"])
        mock_adapter.create_connection = Mock(return_value="mock_connection")
        mock_adapter.get_database_path = Mock(return_value="/path/to/db")
        mock_adapter.close_connection = Mock()

        validator = ConnectionValidator(mock_adapter, {"database": "test"})

        flag_states = []

        def record_flag_state(*args, **kwargs):
            flag_states.append(mock_adapter._validating_database)
            return "mock_connection"

        mock_adapter.create_connection = Mock(side_effect=record_flag_state)

        with validator.create_temporary_connection() as conn:
            assert conn == "mock_connection"

        assert True in flag_states

        assert mock_adapter._validating_database is False
        mock_adapter.close_connection.assert_called_once_with("mock_connection")

    def test_validate_success(self):

        mock_adapter = Mock()
        mock_adapter._create_direct_connection = Mock(return_value="mock_connection")
        mock_adapter.get_database_path = Mock(return_value="/path/to/db")
        mock_adapter.close_connection = Mock()

        validator = ConnectionValidator(mock_adapter, {"database": "test"})
        result = validator.validate()

        assert result.is_valid is True
        assert len(result.errors) == 0

    def test_validate_failure(self):

        mock_adapter = Mock()
        mock_adapter.get_database_path = Mock(side_effect=Exception("Connection failed"))

        validator = ConnectionValidator(mock_adapter, {"database": "test"})
        result = validator.validate()

        assert result.is_valid is False
        assert "Failed to establish connection" in result.errors[0]


class TestTuningValidator:
    def test_validate_no_tuning_enabled(self):

        mock_adapter = Mock()
        mock_adapter.tuning_enabled = False
        mock_adapter.get_effective_tuning_configuration = Mock(return_value=None)
        mock_adapter._validate_database_tunings = Mock(return_value=Mock(is_valid=True, errors=[], warnings=[]))

        validator = TuningValidator(mock_adapter, {})
        result = validator.validate()

        assert result.is_valid is True
        assert len(result.errors) == 0
        assert len(result.warnings) == 0

    def test_validate_no_tuning_enabled_warns_when_database_has_tuning_metadata(self):
        mock_adapter = Mock()
        mock_adapter.tuning_enabled = False
        mock_adapter.get_effective_tuning_configuration = Mock(return_value=None)
        mock_adapter._validate_database_tunings = Mock(
            return_value=Mock(
                is_valid=True,
                errors=[],
                warnings=["Database contains tuning metadata but no tunings expected for this run"],
            )
        )

        result = TuningValidator(mock_adapter, {"database": "test"}).validate()

        assert result.is_valid is True
        assert result.warnings == ["Database contains tuning metadata but no tunings expected for this run"]

    def test_validate_with_valid_tuning(self):

        mock_adapter = Mock()
        mock_adapter.tuning_enabled = True
        mock_adapter.get_effective_tuning_configuration = Mock(return_value={"some": "config"})

        mock_tuning_result = Mock()
        mock_tuning_result.is_valid = True
        mock_tuning_result.errors = []
        mock_tuning_result.warnings = []
        mock_adapter._validate_database_tunings = Mock(return_value=mock_tuning_result)

        validator = TuningValidator(mock_adapter, {})
        result = validator.validate()

        assert result.is_valid is True
        assert len(result.errors) == 0

    def test_validate_with_invalid_tuning(self):

        mock_adapter = Mock()
        mock_adapter.tuning_enabled = True
        mock_adapter.get_effective_tuning_configuration = Mock(return_value={"some": "config"})

        mock_tuning_result = Mock()
        mock_tuning_result.is_valid = False
        mock_tuning_result.errors = ["Error 1", "Error 2"]
        mock_tuning_result.warnings = []
        mock_adapter._validate_database_tunings = Mock(return_value=mock_tuning_result)

        validator = TuningValidator(mock_adapter, {})
        result = validator.validate()

        assert result.is_valid is False
        assert len(result.errors) == 2
        assert "Tuning: Error 1" in result.errors

    def test_validate_with_many_tuning_errors(self):

        mock_adapter = Mock()
        mock_adapter.tuning_enabled = True
        mock_adapter.get_effective_tuning_configuration = Mock(return_value={"some": "config"})

        mock_tuning_result = Mock()
        mock_tuning_result.is_valid = False
        mock_tuning_result.errors = [f"Error {i}" for i in range(10)]
        mock_tuning_result.warnings = []
        mock_adapter._validate_database_tunings = Mock(return_value=mock_tuning_result)

        validator = TuningValidator(mock_adapter, {})
        result = validator.validate()

        assert result.is_valid is False
        assert len(result.errors) == 4
        assert "... and 7 more tuning errors" in result.errors[-1]

    def test_validate_unexpected_metadata(self):

        mock_adapter = Mock()
        mock_adapter.tuning_enabled = True
        mock_adapter.get_effective_tuning_configuration = Mock(return_value=None)

        mock_adapter._validate_database_tunings = Mock(
            return_value=Mock(
                is_valid=True,
                errors=[],
                warnings=["Database contains tuning metadata but no tunings expected for this run"],
            )
        )

        validator = TuningValidator(mock_adapter, {"database": "test"})
        result = validator.validate()

        assert result.is_valid is True
        assert len(result.warnings) == 1
        assert "contains tuning metadata" in result.warnings[0]


class TestSchemaValidator:
    def test_validate_no_benchmark_instance(self):

        mock_adapter = Mock(spec=["benchmark_instance"])
        mock_adapter.benchmark_instance = None

        validator = SchemaValidator(mock_adapter, {})
        result = validator.validate(Mock())

        assert result.is_valid is True
        assert len(result.errors) == 0
        assert len(result.warnings) == 1
        assert "benchmark_instance not set" in result.warnings[0]

    def test_validate_all_tables_present(self):

        mock_adapter = Mock()
        mock_benchmark = Mock()
        mock_benchmark.get_schema = Mock(return_value={"table1": {}, "table2": {}})
        mock_adapter.benchmark_instance = mock_benchmark
        mock_adapter._get_existing_tables = Mock(return_value=["table1", "table2"])

        validator = SchemaValidator(mock_adapter, {})
        result = validator.validate(Mock())

        assert result.is_valid is True
        assert len(result.errors) == 0

    def test_validate_missing_tables(self):

        mock_adapter = Mock()
        mock_benchmark = Mock()
        mock_benchmark.get_schema = Mock(return_value={"table1": {}, "table2": {}, "table3": {}})
        mock_adapter.benchmark_instance = mock_benchmark
        mock_adapter._get_existing_tables = Mock(return_value=["table1"])

        validator = SchemaValidator(mock_adapter, {})
        result = validator.validate(Mock())

        assert result.is_valid is False
        assert len(result.errors) == 1
        assert "Missing tables" in result.errors[0]
        assert "table2" in result.errors[0]
        assert "table3" in result.errors[0]

    def test_validate_extra_tables(self):

        mock_adapter = Mock()
        mock_benchmark = Mock()
        mock_benchmark.get_schema = Mock(return_value={"table1": {}})
        mock_adapter.benchmark_instance = mock_benchmark
        mock_adapter._get_existing_tables = Mock(return_value=["table1", "table2", "extra_table"])

        validator = SchemaValidator(mock_adapter, {})
        result = validator.validate(Mock())

        assert result.is_valid is True
        assert len(result.warnings) == 1
        assert "Extra tables found" in result.warnings[0]

    def test_validate_filters_system_tables(self):

        mock_adapter = Mock()
        mock_benchmark = Mock()
        mock_benchmark.get_schema = Mock(return_value={"table1": {}})
        mock_adapter.benchmark_instance = mock_benchmark
        mock_adapter._get_existing_tables = Mock(return_value=["table1", "benchbox_tuning_metadata", "sqlite_sequence"])

        validator = SchemaValidator(mock_adapter, {})
        result = validator.validate(Mock())

        assert result.is_valid is True
        assert len(result.warnings) == 0


class TestRowCountStrategies:
    def test_tpch_strategy_sample_tables(self):

        strategy = TPCHRowCountStrategy(scale_factor=1.0)
        available = {"lineitem", "orders", "customer", "nation", "region"}
        samples = strategy.get_sample_tables(available)

        assert len(samples) == len(available)
        assert set(samples) == available

    def test_tpch_strategy_expected_ranges(self):

        strategy = TPCHRowCountStrategy(scale_factor=1.0)

        lineitem_range = strategy.get_expected_range("lineitem")
        assert lineitem_range is not None
        assert lineitem_range[0] < lineitem_range[1]
        assert lineitem_range[0] == 6000000 * 0.8

    def test_tpcds_strategy_sample_tables(self):

        strategy = TPCDSRowCountStrategy(scale_factor=1.0)
        available = {"store_sales", "catalog_sales", "customer", "item"}
        samples = strategy.get_sample_tables(available)

        assert len(samples) == len(available)
        assert set(samples) == available

    @pytest.mark.parametrize(
        ("scale", "table", "actual_rows"),
        [
            (100, "customer", 2_000_000),
            (10, "customer", 500_000),
            (100, "customer_address", 1_000_000),
            (100, "inventory", 399_330_000),
            (100, "store", 402),
            (10, "item", 102_000),
            (1, "reason", 75),
            (10, "reason", 75),
            (100, "reason", 75),
            (1, "web_site", 30),
            (10, "web_site", 42),
            (100, "web_site", 24),
            (100, "store_sales", 287_997_024),
            (100, "CUSTOMER", 2_000_000),
        ],
    )
    def test_tpcds_strategy_accepts_generated_row_counts(self, scale, table, actual_rows):
        expected = TPCDSRowCountStrategy(scale_factor=float(scale)).get_expected_range(table)
        assert expected is not None
        low, high = expected

        assert low <= actual_rows <= high

    @pytest.mark.parametrize("scale", [1.0, 10.0, 100.0])
    def test_tpcds_strategy_covers_every_table_at_spec_scales(self, scale):
        strategy = TPCDSRowCountStrategy(scale_factor=scale)
        tables = {
            "call_center",
            "catalog_page",
            "catalog_returns",
            "catalog_sales",
            "customer",
            "customer_address",
            "customer_demographics",
            "date_dim",
            "household_demographics",
            "income_band",
            "inventory",
            "item",
            "promotion",
            "reason",
            "ship_mode",
            "store",
            "store_returns",
            "store_sales",
            "time_dim",
            "warehouse",
            "web_page",
            "web_returns",
            "web_sales",
            "web_site",
        }

        assert all(strategy.get_expected_range(table) is not None for table in tables)

    def test_tpcds_strategy_rejects_linear_customer_count_at_scale_100(self):
        expected = TPCDSRowCountStrategy(scale_factor=100.0).get_expected_range("customer")
        assert expected is not None
        low, high = expected

        assert not low <= 10_000_000 <= high

    def test_tpcds_strategy_scales_fact_tables_and_skips_sublinear_tables_off_spec_scales(self):
        strategy = TPCDSRowCountStrategy(scale_factor=1000.0)

        expected = strategy.get_expected_range("store_sales")

        assert expected is not None

        low, high = expected
        assert low <= 2_880_404 * 1000 <= high
        assert strategy.get_expected_range("customer") is None
        assert strategy.get_expected_range("inventory") is None

    def test_tpcds_strategy_scales_customer_linearly_below_scale_one(self):
        expected = TPCDSRowCountStrategy(scale_factor=0.01).get_expected_range("customer")
        assert expected is not None
        low, high = expected

        assert low <= 1_000 <= high

    def test_ssb_strategy_sample_tables(self):

        strategy = SSBRowCountStrategy(scale_factor=1.0)
        available = {"lineorder", "customer", "supplier"}
        samples = strategy.get_sample_tables(available)

        assert len(samples) == len(available)
        assert set(samples) == available

    def test_ssb_strategy_expected_ranges(self):

        strategy = SSBRowCountStrategy(scale_factor=1.0)

        customer_range = strategy.get_expected_range("customer")
        assert customer_range is not None
        assert customer_range[0] == 30000 * 0.8
        assert customer_range[1] == 30000 * 1.2

        lineorder_range = strategy.get_expected_range("lineorder")
        assert lineorder_range is not None
        assert lineorder_range[0] == 6000000 * 0.8
        assert lineorder_range[1] == 6000000 * 1.2

        assert strategy.get_expected_range("supplier") is None

    def test_ssb_strategy_expected_ranges_sf10(self):

        strategy = SSBRowCountStrategy(scale_factor=10.0)

        customer_range = strategy.get_expected_range("customer")
        assert customer_range[0] <= 300_000 <= customer_range[1], (
            f"300,000 customer rows at SF=10 should be in range {customer_range}"
        )

        lineorder_range = strategy.get_expected_range("lineorder")
        assert lineorder_range[0] <= 60_000_000 <= lineorder_range[1]

    def test_generic_strategy_sample_tables(self):

        strategy = GenericRowCountStrategy(scale_factor=1.0)
        available = {"table1", "table2", "table3"}
        samples = strategy.get_sample_tables(available)

        assert len(samples) == len(available)
        assert set(samples) == available

    def test_generic_strategy_no_expected_ranges(self):

        strategy = GenericRowCountStrategy(scale_factor=1.0)
        assert strategy.get_expected_range("any_table") is None

    def test_joinorder_strategy_exact_manifest_ranges(self):

        mock_benchmark = Mock()
        mock_benchmark.get_table_row_count = Mock(return_value=36_244_344)
        strategy = JoinOrderRowCountStrategy(scale_factor=1.0, benchmark_instance=mock_benchmark)

        assert strategy.get_sample_tables({"cast_info"}) == ["cast_info"]
        assert strategy.get_expected_range("cast_info") == (36_244_344.0, 36_244_344.0)
        mock_benchmark.get_table_row_count.assert_called_once_with("cast_info")

    def test_scale_factor_affects_ranges(self):

        strategy1 = TPCHRowCountStrategy(scale_factor=1.0)
        strategy2 = TPCHRowCountStrategy(scale_factor=10.0)

        range1 = strategy1.get_expected_range("lineitem")
        range2 = strategy2.get_expected_range("lineitem")

        assert range2[0] == range1[0] * 10
        assert range2[1] == range1[1] * 10


class TestRowCountValidator:
    def test_validate_no_scale_factor(self):

        mock_adapter = Mock(spec=["benchmark_instance"])
        mock_adapter.benchmark_instance = Mock()

        validator = RowCountValidator(mock_adapter, {})
        result = validator.validate(Mock(), {"table1"})

        assert result.is_valid is True
        assert len(result.errors) == 0

    def test_validate_no_tables(self):

        mock_adapter = Mock()
        mock_adapter.scale_factor = 1.0
        mock_adapter.benchmark_instance = Mock()

        validator = RowCountValidator(mock_adapter, {})
        result = validator.validate(Mock(), set())

        assert result.is_valid is True
        assert len(result.errors) == 0

    def test_get_strategy_tpch(self):

        mock_adapter = Mock()
        mock_adapter.scale_factor = 1.0
        mock_benchmark = Mock()
        mock_benchmark.__class__.__name__ = "TPCHBenchmark"
        mock_adapter.benchmark_instance = mock_benchmark

        validator = RowCountValidator(mock_adapter, {})
        strategy = validator._get_strategy()

        assert isinstance(strategy, TPCHRowCountStrategy)

    def test_get_strategy_tpcds(self):

        mock_adapter = Mock()
        mock_adapter.scale_factor = 1.0
        mock_benchmark = Mock()
        mock_benchmark.__class__.__name__ = "TPCDSBenchmark"
        mock_adapter.benchmark_instance = mock_benchmark

        validator = RowCountValidator(mock_adapter, {})
        strategy = validator._get_strategy()

        assert isinstance(strategy, TPCDSRowCountStrategy)

    def test_get_strategy_ssb(self):

        mock_adapter = Mock()
        mock_adapter.scale_factor = 1.0
        mock_benchmark = Mock()
        mock_benchmark.__class__.__name__ = "SSBBenchmark"
        mock_adapter.benchmark_instance = mock_benchmark

        validator = RowCountValidator(mock_adapter, {})
        strategy = validator._get_strategy()

        assert isinstance(strategy, SSBRowCountStrategy)

    def test_get_strategy_generic(self):

        mock_adapter = Mock()
        mock_adapter.scale_factor = 1.0
        mock_benchmark = Mock()
        mock_benchmark.__class__.__name__ = "UnknownBenchmark"
        mock_adapter.benchmark_instance = mock_benchmark

        validator = RowCountValidator(mock_adapter, {})
        strategy = validator._get_strategy()

        assert isinstance(strategy, GenericRowCountStrategy)

    def test_get_strategy_joinorder(self):

        mock_adapter = Mock()
        mock_adapter.scale_factor = 1.0
        mock_benchmark = Mock()
        mock_benchmark.__class__.__name__ = "JoinOrderBenchmark"
        mock_benchmark.get_table_row_count = Mock(return_value=36_244_344)
        mock_adapter.benchmark_instance = mock_benchmark

        validator = RowCountValidator(mock_adapter, {})
        strategy = validator._get_strategy()

        assert isinstance(strategy, JoinOrderRowCountStrategy)

    def test_get_strategy_joinorder_synthetic_falls_back_to_generic(self):

        mock_adapter = Mock()
        mock_adapter.scale_factor = 1.0
        mock_benchmark = Mock()
        mock_benchmark.__class__.__name__ = "JoinOrderSyntheticBenchmark"
        mock_adapter.benchmark_instance = mock_benchmark

        validator = RowCountValidator(mock_adapter, {})
        strategy = validator._get_strategy()

        assert isinstance(strategy, GenericRowCountStrategy)

    def test_validate_joinorder_exact_row_count_passes(self):

        mock_adapter = Mock()
        mock_adapter.scale_factor = 1.0
        mock_benchmark = Mock()
        mock_benchmark.__class__.__name__ = "JoinOrderBenchmark"
        mock_benchmark.get_table_row_count = Mock(return_value=36_244_344)
        mock_adapter.benchmark_instance = mock_benchmark
        mock_adapter.get_table_row_count = Mock(return_value=36_244_344)

        validator = RowCountValidator(mock_adapter, {})
        result = validator.validate(Mock(), {"cast_info"})

        assert result.is_valid is True
        assert result.errors == []

    def test_validate_joinorder_drifted_row_count_fails(self):

        mock_adapter = Mock()
        mock_adapter.scale_factor = 1.0
        mock_benchmark = Mock()
        mock_benchmark.__class__.__name__ = "JoinOrderBenchmark"
        mock_benchmark.get_table_row_count = Mock(return_value=36_244_344)
        mock_adapter.benchmark_instance = mock_benchmark
        mock_adapter.get_table_row_count = Mock(return_value=36_244_300)

        validator = RowCountValidator(mock_adapter, {})
        result = validator.validate(Mock(), {"cast_info"})

        assert result.is_valid is False
        assert len(result.errors) == 1
        assert "expected" in result.errors[0].lower()
        assert "36,244,344" in result.errors[0]
        assert "empty" not in result.errors[0].lower()

    def test_validate_table_row_count_within_range(self):

        mock_adapter = Mock()
        mock_adapter.scale_factor = 1.0
        mock_benchmark = Mock()
        mock_benchmark.__class__.__name__ = "TPCHBenchmark"
        mock_adapter.benchmark_instance = mock_benchmark

        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = (6000000,)
        mock_connection = Mock()
        mock_connection.cursor.return_value = mock_cursor

        validator = RowCountValidator(mock_adapter, {})
        result = validator.validate(mock_connection, {"lineitem"})

        assert result.is_valid is True
        assert len(result.errors) == 0

    def test_validate_table_row_count_outside_range(self):

        mock_adapter = Mock()
        mock_adapter.scale_factor = 1.0
        mock_benchmark = Mock()
        mock_benchmark.__class__.__name__ = "TPCHBenchmark"
        mock_adapter.benchmark_instance = mock_benchmark

        mock_adapter.get_table_row_count = Mock(return_value=100)

        mock_connection = Mock()

        validator = RowCountValidator(mock_adapter, {})
        result = validator.validate(mock_connection, {"lineitem"})

        assert result.is_valid is False
        assert len(result.errors) == 1
        assert "expected" in result.errors[0].lower()

    def test_validate_table_empty(self):

        mock_adapter = Mock()
        mock_adapter.scale_factor = 1.0
        mock_benchmark = Mock()
        mock_benchmark.__class__.__name__ = "UnknownBenchmark"
        mock_adapter.benchmark_instance = mock_benchmark

        mock_adapter.get_table_row_count = Mock(return_value=0)

        mock_connection = Mock()

        validator = RowCountValidator(mock_adapter, {})
        result = validator.validate(mock_connection, {"some_table"})

        assert result.is_valid is False
        assert len(result.errors) == 1
        assert "empty" in result.errors[0].lower()


class TestDatabaseValidator:
    def test_validate_success(self):

        mock_adapter = Mock()
        mock_adapter.tuning_enabled = False

        mock_benchmark = Mock()
        mock_benchmark.get_schema = Mock(return_value={"table1": {}, "table2": {}})
        mock_adapter.benchmark_instance = mock_benchmark
        mock_adapter.get_effective_tuning_configuration = Mock(return_value=None)
        mock_adapter.log_operation_start = Mock()
        mock_adapter.log_operation_complete = Mock()
        mock_adapter.log_very_verbose = Mock()

        mock_connection = Mock()
        with patch.object(ConnectionValidator, "create_temporary_connection") as mock_conn_mgr:
            mock_conn_mgr.return_value.__enter__ = Mock(return_value=mock_connection)
            mock_conn_mgr.return_value.__exit__ = Mock(return_value=False)

            with (
                patch.object(TuningValidator, "validate") as mock_tuning,
                patch.object(SchemaValidator, "validate") as mock_schema,
                patch.object(RowCountValidator, "validate") as mock_rows,
            ):
                mock_tuning.return_value = ValidationResult(is_valid=True)
                mock_schema.return_value = ValidationResult(is_valid=True)
                mock_rows.return_value = ValidationResult(is_valid=True)

                validator = DatabaseValidator(mock_adapter, {})
                result = validator.validate()

                assert isinstance(result, DatabaseValidationResult)
                assert result.is_valid is True
                assert result.can_reuse is True

    def test_validate_with_errors(self):

        mock_adapter = Mock()
        mock_adapter.tuning_enabled = False
        mock_adapter.benchmark_instance = Mock()
        mock_adapter.get_effective_tuning_configuration = Mock(return_value=None)
        mock_adapter.log_operation_start = Mock()
        mock_adapter.log_operation_complete = Mock()

        mock_connection = Mock()
        with patch.object(ConnectionValidator, "create_temporary_connection") as mock_conn_mgr:
            mock_conn_mgr.return_value.__enter__ = Mock(return_value=mock_connection)
            mock_conn_mgr.return_value.__exit__ = Mock(return_value=False)

            with (
                patch.object(TuningValidator, "validate") as mock_tuning,
                patch.object(SchemaValidator, "validate") as mock_schema,
                patch.object(RowCountValidator, "validate") as mock_rows,
            ):
                mock_tuning.return_value = ValidationResult(is_valid=True)

                schema_result = ValidationResult(is_valid=False)
                schema_result.add_error("Missing tables: table1, table2")
                mock_schema.return_value = schema_result

                mock_rows.return_value = ValidationResult(is_valid=True)

                validator = DatabaseValidator(mock_adapter, {})
                result = validator.validate()

                assert result.is_valid is False
                assert len(result.issues) > 0
                assert "Missing tables" in result.issues[0]

    def test_validate_connection_exception(self):

        mock_adapter = Mock()
        mock_adapter.log_operation_start = Mock()

        with patch.object(ConnectionValidator, "create_temporary_connection") as mock_conn_mgr:
            mock_conn_mgr.side_effect = Exception("Connection failed")

            validator = DatabaseValidator(mock_adapter, {})
            result = validator.validate()

            assert result.is_valid is False
            assert result.can_reuse is False
            assert "Database validation failed" in result.issues[0]

    def test_determine_validity_all_valid(self):

        mock_adapter = Mock()
        validator = DatabaseValidator(mock_adapter, {})

        is_valid, can_reuse = validator._determine_validity(tuning_valid=True, tables_valid=True, row_counts_valid=True)

        assert is_valid is True
        assert can_reuse is True

    def test_determine_validity_row_count_invalid(self):

        mock_adapter = Mock()
        validator = DatabaseValidator(mock_adapter, {})

        is_valid, can_reuse = validator._determine_validity(
            tuning_valid=True, tables_valid=True, row_counts_valid=False
        )

        assert is_valid is False
        assert can_reuse is True

    def test_determine_validity_tables_invalid(self):

        mock_adapter = Mock()
        validator = DatabaseValidator(mock_adapter, {})

        is_valid, can_reuse = validator._determine_validity(
            tuning_valid=True, tables_valid=False, row_counts_valid=None
        )

        assert is_valid is False
        assert can_reuse is False

    def test_determine_validity_no_benchmark(self):

        mock_adapter = Mock(spec=[])
        validator = DatabaseValidator(mock_adapter, {})

        is_valid, can_reuse = validator._determine_validity(tuning_valid=None, tables_valid=None, row_counts_valid=None)

        assert is_valid is False
        assert can_reuse is False


@pytest.mark.unit
@pytest.mark.unit
class TestValidationCoverageGaps:
    def test_schema_validator_exception_handling(self):

        adapter = Mock()
        adapter._get_existing_tables.side_effect = Exception("DB error")
        validator = SchemaValidator(adapter, {})

        result = validator.validate(Mock())

        assert result is not None
        assert isinstance(result.errors, list)

    def test_row_count_validator_exception_handling(self):

        adapter = Mock()
        adapter.get_table_row_count.side_effect = RuntimeError("Query failed")
        validator = RowCountValidator(adapter, {})

        result = validator.validate(Mock(), {"table1"})

        assert result is not None
        assert len(result.warnings) > 0

    def test_database_validator_initialization(self):

        adapter = Mock()
        config = {"type": "duckdb"}
        validator = DatabaseValidator(adapter, config)

        assert validator is not None
        assert hasattr(validator, "validate")
        assert hasattr(validator, "connection_validator")
        assert hasattr(validator, "schema_validator")
