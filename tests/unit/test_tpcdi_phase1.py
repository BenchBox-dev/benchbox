import random
import tempfile
from pathlib import Path
from unittest.mock import Mock

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


pytest.importorskip("pandas")

from benchbox.core.tpcdi.financial_data import FinancialDataPatterns
from benchbox.core.tpcdi.generator import TPCDIDataGenerator
from benchbox.core.tpcdi.schema import (
    TABLES,
    TPCDISchemaManager,
    get_all_create_table_sql,
)
from benchbox.core.tpcdi.schema_extensions import (
    EXTENSION_TABLES,
    get_extended_create_table_sql,
    get_extended_table_order,
    get_foreign_key_constraints,
)


class TestSchemaExtensions:
    def test_extension_tables_defined(self):
        expected_tables = {
            "DimBroker",
            "FactCashBalances",
            "FactHoldings",
            "FactMarketHistory",
            "FactWatches",
            "Industry",
            "StatusType",
            "TaxRate",
            "TradeType",
        }

        assert set(EXTENSION_TABLES.keys()) == expected_tables

    def test_extension_table_structure(self):
        for _table_name, table_def in EXTENSION_TABLES.items():
            assert "name" in table_def
            assert "columns" in table_def
            assert len(table_def["columns"]) > 0

            for col in table_def["columns"]:
                assert "name" in col
                assert "type" in col

    def test_foreign_key_constraints(self):
        constraints = get_foreign_key_constraints()

        expected_fact_tables = {
            "FactCashBalances",
            "FactHoldings",
            "FactMarketHistory",
            "FactWatches",
        }

        for fact_table in expected_fact_tables:
            assert fact_table in constraints
            assert len(constraints[fact_table]) > 0

            for fk in constraints[fact_table]:
                assert "column" in fk
                assert "references_table" in fk
                assert "references_column" in fk
                assert "constraint_name" in fk

    def test_table_creation_order(self):
        order = get_extended_table_order()

        reference_tables = ["Industry", "StatusType", "TaxRate", "TradeType"]
        for ref_table in reference_tables:
            assert ref_table in order

        fact_tables = [
            "FactCashBalances",
            "FactHoldings",
            "FactMarketHistory",
            "FactWatches",
        ]
        for fact_table in fact_tables:
            assert fact_table in order

    def test_create_table_sql_generation(self):
        for table_name in EXTENSION_TABLES:
            sql = get_extended_create_table_sql(table_name)
            assert sql.startswith("CREATE TABLE IF NOT EXISTS")
            assert table_name in sql
            assert "(" in sql and ");" in sql

    def test_all_extended_create_table_sql(self):
        sql = get_extended_create_table_sql("DimBroker")
        assert "CREATE TABLE IF NOT EXISTS DimBroker" in sql
        assert "SK_BrokerID" in sql
        assert "PRIMARY KEY" in sql


class TestCompleteSchema:
    def test_core_and_extended_tables_included(self):
        core_tables = {
            "DimCustomer",
            "DimAccount",
            "DimSecurity",
            "DimCompany",
            "FactTrade",
            "DimDate",
            "DimTime",
        }

        extended_tables = {
            "DimBroker",
            "FactCashBalances",
            "FactHoldings",
            "FactMarketHistory",
            "FactWatches",
            "Industry",
            "StatusType",
            "TaxRate",
            "TradeType",
        }

        all_expected = core_tables | extended_tables

        for table in all_expected:
            assert table in TABLES, f"Missing table: {table}"

        assert set(TABLES.keys()) == all_expected

    def test_complete_schema_sql_generation(self):
        sql = get_all_create_table_sql()

        for table_name in TABLES:
            assert f"CREATE TABLE IF NOT EXISTS {table_name}" in sql


class TestSchemaManager:
    def test_schema_manager_with_extensions(self):
        manager = TPCDISchemaManager(include_extensions=True)

        assert len(manager.table_order) == 16
        assert manager.get_table_count() == 16

    def test_schema_manager_without_extensions(self):
        manager = TPCDISchemaManager(include_extensions=False)

        assert len(manager.table_order) == 7
        assert manager.get_table_count() == 7

    def test_get_core_tables(self):
        manager = TPCDISchemaManager()
        core_tables = manager.get_core_tables()

        assert len(core_tables) == 7
        assert "DimCustomer" in core_tables
        assert "FactTrade" in core_tables

    def test_get_extended_tables(self):
        manager = TPCDISchemaManager()
        extended_tables = manager.get_extended_tables()

        assert len(extended_tables) == 9
        assert "DimBroker" in extended_tables
        assert "FactCashBalances" in extended_tables

    def test_create_schema_mock(self):
        manager = TPCDISchemaManager()
        mock_connection = Mock()

        manager.create_schema(mock_connection)

        assert mock_connection.execute.call_count == 16

    def test_foreign_key_creation_mock(self):
        manager = TPCDISchemaManager(include_extensions=True)
        mock_connection = Mock()

        manager.create_foreign_key_constraints(mock_connection)

        assert mock_connection.execute.call_count > 0


class TestFinancialDataPatterns:
    def test_financial_patterns_initialization(self):
        patterns = FinancialDataPatterns(seed=42)

        assert patterns.constants is not None
        assert len(patterns.industries) > 0
        assert len(patterns.sp_ratings) > 0
        assert len(patterns.trade_types) > 0

    def test_customer_tier_generation(self):
        patterns = FinancialDataPatterns(seed=42)

        tiers = [patterns.generate_customer_tier() for _ in range(1000)]

        assert all(tier in [1, 2, 3] for tier in tiers)
        assert len(set(tiers)) == 3

    def test_net_worth_by_tier(self):
        patterns = FinancialDataPatterns(seed=42)

        tier1_net_worth = patterns.generate_net_worth(1)
        tier3_net_worth = patterns.generate_net_worth(3)

        assert tier1_net_worth >= 1000000
        assert tier3_net_worth <= 99999

    def test_credit_rating_generation(self):
        patterns = FinancialDataPatterns(seed=42)

        rating = patterns.generate_credit_rating(tier=1, net_worth=5000000)

        assert 300 <= rating <= 850

    def test_security_price_generation(self):
        patterns = FinancialDataPatterns(seed=42)

        price = patterns.generate_security_price()
        assert price > 0

        new_price = patterns.generate_security_price(base_price=price)
        assert new_price > 0

    def test_market_hours_check(self):
        patterns = FinancialDataPatterns(seed=42)

        assert patterns.is_market_hours(10, 0)
        assert patterns.is_market_hours(15, 30)

        assert not patterns.is_market_hours(8, 0)
        assert not patterns.is_market_hours(17, 0)

    def test_industry_data(self):
        patterns = FinancialDataPatterns(seed=42)
        industries = patterns.get_industry_data()

        assert len(industries) >= 10
        for industry_id, industry_name, sector_code in industries:
            assert len(industry_id) > 0
            assert len(industry_name) > 0
            assert len(sector_code) > 0


class TestDataGenerator:
    def test_generator_initialization(self):
        generator = TPCDIDataGenerator(scale_factor=0.1)

        assert generator.financial_patterns is not None
        assert generator.scale_factor == 0.1

    def test_extended_table_generators_exist(self):
        generator = TPCDIDataGenerator(scale_factor=0.1)

        extended_methods = [
            "_generate_industry_data",
            "_generate_statustype_data",
            "_generate_taxrate_data",
            "_generate_tradetype_data",
            "_generate_dimbroker_data",
            "_generate_factcashbalances_data",
            "_generate_factholdings_data",
            "_generate_factmarkethistory_data",
            "_generate_factwatches_data",
        ]

        for method_name in extended_methods:
            assert hasattr(generator, method_name)

    def test_reference_data_generation(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            generator = TPCDIDataGenerator(scale_factor=0.1, output_dir=Path(tmp_dir))

            industry_file = generator._generate_industry_data()
            assert Path(industry_file).exists()

            with open(industry_file, encoding="utf-8") as f:
                lines = f.readlines()
                assert len(lines) >= 10

            status_file = generator._generate_statustype_data()
            assert Path(status_file).exists()

    def test_broker_data_generation(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            generator = TPCDIDataGenerator(scale_factor=0.1, output_dir=Path(tmp_dir))

            broker_file = generator._generate_dimbroker_data()
            assert Path(broker_file).exists()

            with open(broker_file, encoding="utf-8") as f:
                lines = f.readlines()
                assert len(lines) >= 100

    def test_fact_table_generation(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            generator = TPCDIDataGenerator(
                scale_factor=0.01,
                output_dir=Path(tmp_dir),
            )

            cash_file = generator._generate_factcashbalances_data()
            assert Path(cash_file).exists()

            holdings_file = generator._generate_factholdings_data()
            assert Path(holdings_file).exists()

            with open(cash_file, encoding="utf-8") as f:
                assert len(f.readlines()) > 0

    @pytest.mark.slow
    def test_complete_data_generation(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            generator = TPCDIDataGenerator(
                scale_factor=0.01,
                output_dir=Path(tmp_dir),
            )

            file_paths = generator.generate_data()

            assert len(file_paths) >= 16

            extended_tables = [
                "Industry",
                "StatusType",
                "TaxRate",
                "TradeType",
                "DimBroker",
                "FactCashBalances",
                "FactHoldings",
            ]

            for table in extended_tables:
                if table in file_paths:
                    file_path = Path(file_paths[table])
                    assert file_path.exists()
                    assert file_path.stat().st_size > 0


class TestIntegration:
    def test_repeated_generation_resets_all_private_rngs(self, tmp_path):
        generator = TPCDIDataGenerator(scale_factor=0.001, output_dir=tmp_path, generation_seed=73)
        global_state = random.getstate()

        first_paths = generator.generate_data(tables=["FactCashBalances", "FactHoldings"])
        first_contents = {table: Path(path).read_bytes() for table, path in first_paths.items()}
        second_paths = generator.generate_data(tables=["FactCashBalances", "FactHoldings"])
        second_contents = {table: Path(path).read_bytes() for table, path in second_paths.items()}

        assert second_contents == first_contents
        assert random.getstate() == global_state

    def test_schema_and_generator_compatibility(self):
        schema_manager = TPCDISchemaManager(include_extensions=True)
        generator = TPCDIDataGenerator(scale_factor=0.01)

        set(schema_manager.table_order)

        with tempfile.TemporaryDirectory() as tmp_dir:
            generator.output_dir = Path(tmp_dir)

            test_tables = ["Industry", "DimBroker", "FactCashBalances"]
            file_paths = generator.generate_data(tables=test_tables)

            for table in test_tables:
                assert table in file_paths
                assert Path(file_paths[table]).exists()

    def test_realistic_data_patterns(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            generator = TPCDIDataGenerator(scale_factor=0.01, output_dir=Path(tmp_dir))

            broker_file = generator._generate_dimbroker_data()

            with open(broker_file, encoding="utf-8") as f:
                lines = f.readlines()

            assert len(lines) >= 100

            if lines:
                sample_line = lines[0].strip().split("|")
                assert len(sample_line) >= 10

    def test_foreign_key_referential_integrity(self):
        constraints = get_foreign_key_constraints()
        schema_tables = set(TABLES.keys())

        for table_name, fk_list in constraints.items():
            assert table_name in schema_tables

            for fk in fk_list:
                referenced_table = fk["references_table"]
                assert referenced_table in schema_tables, f"FK references unknown table: {referenced_table}"


@pytest.mark.integration
class TestTPCDIPhase1Complete:
    def test_phase1_complete_implementation(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            schema_manager = TPCDISchemaManager(include_extensions=True)

            generator = TPCDIDataGenerator(
                scale_factor=0.01,
                output_dir=Path(tmp_dir),
            )

            file_paths = generator.generate_data()

            expected_tables = 16
            assert len(file_paths) >= expected_tables

            total_size = 0
            for _table, file_path in file_paths.items():
                path = Path(file_path)
                assert path.exists()
                size = path.stat().st_size
                assert size > 0
                total_size += size

            assert total_size > 10000

            core_coverage = len(schema_manager.get_core_tables())
            extended_coverage = len(schema_manager.get_extended_tables())

            assert core_coverage == 7
            assert extended_coverage == 9

            patterns = generator.financial_patterns
            assert patterns is not None

            tier = patterns.generate_customer_tier()
            assert tier in [1, 2, 3]

            net_worth = patterns.generate_net_worth(tier)
            assert net_worth > 0

            print("✅ TPC-DI Phase 1 implementation complete:")
            print(f"  - Schema tables: {len(schema_manager.table_order)}")
            print(f"  - Generated files: {len(file_paths)}")
            print(f"  - Total data size: {total_size:,} bytes")
            print(f"  - Coverage: {(len(file_paths) / 16) * 100:.1f}%")
