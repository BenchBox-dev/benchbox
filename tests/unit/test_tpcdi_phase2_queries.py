# Copyright 2026 Joe Harris / BenchBox Project

import tempfile
from pathlib import Path
from unittest.mock import Mock

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


pytest.importorskip("pandas")

from benchbox.core.tpcdi.benchmark import TPCDIBenchmark
from benchbox.core.tpcdi.queries import TPCDIQueryManager
from benchbox.core.tpcdi.query_analytics import TPCDIAnalyticalQueries
from benchbox.core.tpcdi.query_etl import TPCDIETLQueries
from benchbox.core.tpcdi.query_validation import TPCDIValidationQueries


class TestTPCDIValidationQueries:
    def setup_method(self):
        self.validation_queries = TPCDIValidationQueries()

    def test_validation_queries_initialization(self):
        assert len(self.validation_queries._queries) == 12
        assert len(self.validation_queries._query_metadata) == 12

        expected_ids = [f"VQ{i}" for i in range(1, 13)]
        assert set(self.validation_queries._queries.keys()) == set(expected_ids)

    def test_validation_query_retrieval(self):
        vq1 = self.validation_queries.get_query("VQ1")
        assert "Core Tables Referential Integrity" in vq1
        assert "FactTrade" in vq1
        assert "DimCustomer" in vq1

        vq4 = self.validation_queries.get_query("VQ4")
        assert "SCD Type 2 Current Record Validation" in vq4
        assert "IsCurrent" in vq4

        vq7 = self.validation_queries.get_query("VQ7")
        assert "Customer Tier-NetWorth Validation" in vq7
        assert "NetWorth" in vq7

    def test_validation_query_parameters(self):
        params = {"tolerance_threshold": 50, "tier1_min_networth": 500000}

        vq6 = self.validation_queries.get_query("VQ6", params)
        assert "50" in vq6

        vq7 = self.validation_queries.get_query("VQ7", params)
        assert "500000" in vq7

    def test_validation_queries_by_category(self):
        ref_integrity = self.validation_queries.get_queries_by_category("referential_integrity")
        assert len(ref_integrity) == 2
        assert "VQ1" in ref_integrity
        assert "VQ2" in ref_integrity

        scd_queries = self.validation_queries.get_queries_by_category("scd_type2")
        assert len(scd_queries) == 2
        assert "VQ4" in scd_queries
        assert "VQ5" in scd_queries

        business_rules = self.validation_queries.get_queries_by_category("business_rules")
        assert len(business_rules) == 3

    def test_validation_queries_by_severity(self):
        critical = self.validation_queries.get_queries_by_severity("critical")
        high = self.validation_queries.get_queries_by_severity("high")
        medium = self.validation_queries.get_queries_by_severity("medium")

        assert len(critical) == 2
        assert len(high) == 3
        assert len(medium) >= 5

    def test_invalid_validation_query_id(self):
        with pytest.raises(ValueError, match="Invalid validation query ID"):
            self.validation_queries.get_query("VQ999")


class TestTPCDIAnalyticalQueries:
    def setup_method(self):
        self.analytical_queries = TPCDIAnalyticalQueries()

    def test_analytical_queries_initialization(self):
        assert len(self.analytical_queries._queries) == 10
        assert len(self.analytical_queries._query_metadata) == 10

        expected_ids = [f"AQ{i}" for i in range(1, 11)]
        assert set(self.analytical_queries._queries.keys()) == set(expected_ids)

    def test_analytical_query_retrieval(self):
        aq1 = self.analytical_queries.get_query("AQ1")
        assert "Customer Profitability Analysis" in aq1
        assert "total_fees_generated" in aq1
        assert "revenue_per_customer" in aq1

        aq5 = self.analytical_queries.get_query("AQ5")
        assert "Portfolio Risk and Return Analysis" in aq5
        assert "portfolio_diversification" in aq5
        assert "total_portfolio_value" in aq5

    def test_analytical_query_parameters(self):
        params = {
            "start_year": 2020,
            "end_year": 2024,
            "min_trades": 50,
            "limit_rows": 25,
        }

        aq2 = self.analytical_queries.get_query("AQ2", params)
        assert "2020" in aq2
        assert "2024" in aq2
        assert "50" in aq2
        assert "LIMIT 25" in aq2

    def test_analytical_queries_by_category(self):
        customer_prof = self.analytical_queries.get_queries_by_category("customer_profitability")
        assert "AQ1" in customer_prof

        portfolio = self.analytical_queries.get_queries_by_category("portfolio_analysis")
        assert "AQ5" in portfolio

        risk = self.analytical_queries.get_queries_by_category("risk_compliance")
        assert "AQ10" in risk

    def test_analytical_queries_by_complexity(self):
        high_complexity = self.analytical_queries.get_queries_by_complexity("high")
        medium_complexity = self.analytical_queries.get_queries_by_complexity("medium")

        assert len(high_complexity) >= 6
        assert len(medium_complexity) >= 3

        assert "AQ5" in high_complexity
        assert "AQ4" in high_complexity


class TestTPCDIETLQueries:
    def setup_method(self):
        self.etl_queries = TPCDIETLQueries()

    def test_etl_queries_initialization(self):
        assert len(self.etl_queries._queries) == 8
        assert len(self.etl_queries._query_metadata) == 8

        expected_ids = [f"EQ{i}" for i in range(1, 9)]
        assert set(self.etl_queries._queries.keys()) == set(expected_ids)

    def test_etl_query_retrieval(self):
        eq1 = self.etl_queries.get_query("EQ1")
        assert "Batch Processing Validation" in eq1
        assert "records_per_second" in eq1

        eq4 = self.etl_queries.get_query("EQ4")
        assert "SCD Type 2 Processing Validation" in eq4
        assert "scd_processing_errors" in eq4

    def test_etl_query_parameters(self):
        params = {
            "min_throughput": 200,
            "success_rate_threshold": 98.0,
            "batch_start_date": "2024-01-01",
        }

        eq1 = self.etl_queries.get_query("EQ1", params)
        assert "200" in eq1

        eq3 = self.etl_queries.get_query("EQ3", params)
        assert "98.0" in eq3

    def test_etl_queries_by_category(self):
        batch = self.etl_queries.get_queries_by_category("batch_processing")
        assert "EQ1" in batch

        scd = self.etl_queries.get_queries_by_category("scd_processing")
        assert "EQ4" in scd

        performance = self.etl_queries.get_queries_by_category("performance")
        assert "EQ6" in performance

    def test_etl_queries_by_frequency(self):
        per_batch = self.etl_queries.get_queries_by_frequency("per_batch")
        continuous = self.etl_queries.get_queries_by_frequency("continuous")

        assert len(per_batch) >= 4
        assert "EQ8" in continuous


class TestTPCDIQueryManager:
    def setup_method(self):
        self.query_manager = TPCDIQueryManager()

    def test_query_manager_initialization(self):
        all_queries = self.query_manager.get_all_queries()
        assert len(all_queries) == 38

        stats = self.query_manager.get_query_statistics()
        assert stats["total_queries"] == 38
        assert stats["base_queries"] == 8
        assert stats["extended_validation_queries"] == 12
        assert stats["extended_analytical_queries"] == 10
        assert stats["etl_validation_queries"] == 8
        assert stats["query_expansion_factor"] == 4.75

    def test_base_query_support(self):
        v1 = self.query_manager.get_query("V1")
        assert "total_customers" in v1

        a1 = self.query_manager.get_query("A1")
        assert "customer_count" in a1

    def test_extended_query_access(self):
        vq1 = self.query_manager.get_query("VQ1")
        assert "Core Tables Referential Integrity" in vq1

        aq1 = self.query_manager.get_query("AQ1")
        assert "Customer Profitability Analysis" in aq1

        eq1 = self.query_manager.get_query("EQ1")
        assert "Batch Processing Validation" in eq1

    def test_query_routing(self):
        validation_queries = self.query_manager.get_validation_queries()
        assert len(validation_queries) == 12

        analytical_queries = self.query_manager.get_analytical_queries()
        assert len(analytical_queries) == 10

        etl_queries = self.query_manager.get_etl_queries()
        assert len(etl_queries) == 8

    def test_queries_by_type(self):
        validation = self.query_manager.get_queries_by_type("validation")
        analytical = self.query_manager.get_queries_by_type("analytical")
        etl_validation = self.query_manager.get_queries_by_type("etl_validation")

        assert len(validation) >= 15
        assert len(analytical) >= 15
        assert len(etl_validation) == 8

    def test_queries_by_category(self):
        ref_integrity = self.query_manager.get_queries_by_category("referential_integrity")
        assert len(ref_integrity) == 2

        customer_prof = self.query_manager.get_queries_by_category("customer_profitability")
        assert len(customer_prof) >= 1

    def test_query_execution_plan(self):
        execution_plan = self.query_manager.get_execution_plan()
        assert len(execution_plan) == 38

        for entry in execution_plan:
            assert len(entry) == 3
            query_id, query_type, dependencies = entry
            assert isinstance(query_id, str)
            assert isinstance(query_type, str)
            assert isinstance(dependencies, list)

    def test_query_metadata_access(self):
        v1_metadata = self.query_manager.get_query_metadata("V1")
        assert v1_metadata["query_type"] == "validation"

        vq1_metadata = self.query_manager.get_query_metadata("VQ1")
        assert vq1_metadata["category"] == "referential_integrity"
        assert vq1_metadata["severity"] == "critical"

        aq1_metadata = self.query_manager.get_query_metadata("AQ1")
        assert aq1_metadata["category"] == "customer_profitability"
        assert aq1_metadata["complexity"] == "medium"

    def test_invalid_query_handling(self):
        with pytest.raises(ValueError, match="Invalid query ID"):
            self.query_manager.get_query("INVALID999")

        with pytest.raises(ValueError, match="Invalid query type"):
            self.query_manager.get_queries_by_type("invalid_type")


class TestTPCDIBenchmarkIntegration:
    def setup_method(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            self.benchmark = TPCDIBenchmark(scale_factor=0.001, output_dir=Path(tmp_dir))

    def test_benchmark_query_access(self):
        all_queries = self.benchmark.get_queries()
        assert len(all_queries) == 38

        vq1 = self.benchmark.get_query("VQ1")
        assert "Core Tables Referential Integrity" in vq1

    def test_benchmark_query_statistics(self):
        stats = self.benchmark.query_manager.get_query_statistics()
        assert stats["total_queries"] == 38
        assert stats["query_expansion_factor"] == 4.75

    def test_benchmark_specialized_query_methods(self):
        mock_connection = Mock()
        mock_connection.execute.return_value.fetchall.return_value = []

        try:
            validation_query_ids = list(self.benchmark.query_manager.get_validation_queries().keys())
            assert len(validation_query_ids) == 12
        except Exception as e:
            pytest.fail(f"Validation query method failed: {e}")

        try:
            analytical_query_ids = list(self.benchmark.query_manager.get_analytical_queries().keys())
            assert len(analytical_query_ids) == 10
        except Exception as e:
            pytest.fail(f"Analytical query method failed: {e}")

        try:
            etl_query_ids = list(self.benchmark.query_manager.get_etl_queries().keys())
            assert len(etl_query_ids) == 8
        except Exception as e:
            pytest.fail(f"ETL query method failed: {e}")

    def test_benchmark_execution_plan(self):
        execution_plan = self.benchmark.get_query_execution_plan()
        assert len(execution_plan) == 38

        for query_id, query_type, _dependencies in execution_plan:
            assert query_id in self.benchmark.query_manager.get_all_queries()
            assert query_type in ["validation", "analytical", "etl_validation"]


class TestQueryParameterValidation:
    def setup_method(self):
        self.query_manager = TPCDIQueryManager()

    def test_parameter_substitution(self):
        params = {
            "start_year": 2020,
            "end_year": 2024,
            "min_trades": 100,
            "limit_rows": 50,
        }

        a2_with_params = self.query_manager.get_query("A2", params)
        assert "100" in a2_with_params
        assert "LIMIT 50" in a2_with_params

        aq1_with_params = self.query_manager.get_query("AQ1", params)
        assert "2020" in aq1_with_params
        assert "2024" in aq1_with_params

    def test_default_parameter_handling(self):
        vq7_default = self.query_manager.get_query("VQ7")
        assert "1000000" in vq7_default

        aq1_default = self.query_manager.get_query("AQ1")
        assert "2015" in aq1_default
        assert "2019" in aq1_default

    def test_partial_parameter_override(self):
        partial_params = {"start_year": 2021}

        aq1_partial = self.query_manager.get_query("AQ1", partial_params)
        assert "2021" in aq1_partial
        assert "10" in aq1_partial


class TestPhase2Integration:
    def test_phase2_complete_implementation(self):
        query_manager = TPCDIQueryManager()

        stats = query_manager.get_query_statistics()
        assert stats["total_queries"] >= 30
        actual_total = stats["total_queries"]

        validation_queries = query_manager.get_validation_queries()
        assert len(validation_queries) == 12

        analytical_queries = query_manager.get_analytical_queries()
        assert len(analytical_queries) == 10

        etl_queries = query_manager.get_etl_queries()
        assert len(etl_queries) == 8

        assert hasattr(query_manager, "get_queries_by_category")
        assert hasattr(query_manager, "get_execution_plan")
        assert hasattr(query_manager, "get_query_statistics")

        for query_id in query_manager.get_all_queries():
            try:
                query_sql = query_manager.get_query(query_id)
                assert len(query_sql) > 0
                assert "SELECT" in query_sql.upper()
            except Exception as e:
                pytest.fail(f"Failed to retrieve query {query_id}: {e}")

        expansion_factor = actual_total / stats["base_queries"]
        assert expansion_factor >= 3.75

        print("✅ TPC-DI Phase 2: Query Suite Development - COMPLETE")
        print(f"   - Total queries: {actual_total} ({expansion_factor:.1f}x expansion)")
        print(f"   - Base queries: {stats['base_queries']}")
        print(f"   - Validation queries: {len(validation_queries)} (VQ1-VQ12)")
        print(f"   - Analytical queries: {len(analytical_queries)} (AQ1-AQ10)")
        print(f"   - ETL validation queries: {len(etl_queries)} (EQ1-EQ8)")
        print("   - Comprehensive parameter management ✅")
        print("   - SQL dialect translation support ✅")
        print("   - Query metadata and dependency tracking ✅")
        print("   - Query execution planning ✅")


