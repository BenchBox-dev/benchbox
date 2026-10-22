# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ DI (TPC-DI) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-DI specification.

# Licensed under the MIT License. See LICENSE file in the project root for details.

from pathlib import Path

import duckdb
import pytest

pytest.importorskip("pandas")

from benchbox import TPCDI
from benchbox.core.tpcdi.benchmark import TPCDIBenchmark

pytestmark = [
    pytest.mark.medium,
]

TPCDI_PARALLEL_FACT_TRADE_SCALE_FACTOR = 0.11


class TestTPCDI:
    @pytest.fixture
    def tpcdi(self, small_scale_factor: float, temp_dir: Path) -> TPCDI:
        return TPCDI(scale_factor=small_scale_factor, output_dir=temp_dir)

    def test_generate_data(self, temp_dir: Path) -> None:
        tpcdi = TPCDI(scale_factor=TPCDI_PARALLEL_FACT_TRADE_SCALE_FACTOR, output_dir=temp_dir)
        data_paths = tpcdi.generate_data()

        expected_total_tables = 16
        expected_row_counts = {
            "DimCompany": int(1_000 * TPCDI_PARALLEL_FACT_TRADE_SCALE_FACTOR),
            "DimSecurity": int(10_000 * TPCDI_PARALLEL_FACT_TRADE_SCALE_FACTOR),
            "DimCustomer": int(50_000 * TPCDI_PARALLEL_FACT_TRADE_SCALE_FACTOR),
            "DimAccount": int(100_000 * TPCDI_PARALLEL_FACT_TRADE_SCALE_FACTOR),
            "FactTrade": int(1_000_000 * TPCDI_PARALLEL_FACT_TRADE_SCALE_FACTOR),
        }

        assert isinstance(data_paths, list), "generate_data should return a list per BaseBenchmark interface"
        assert len(data_paths) == expected_total_tables, (
            f"Expected {expected_total_tables} files (7 core + 9 extended), got {len(data_paths)}"
        )

        for path in data_paths:
            assert Path(path).exists(), f"Generated file {path} does not exist"

        row_counts = {}
        for path in data_paths:
            with open(path, encoding="utf-8") as data_file:
                row_counts[Path(path).stem] = sum(1 for _ in data_file)
        assert {table: row_counts.get(table) for table in expected_row_counts} == expected_row_counts

    def test_get_queries(self, tpcdi: TPCDI) -> None:
        queries = tpcdi.get_queries()

        assert isinstance(queries, dict), "get_queries should return a dictionary"
        assert len(queries) >= 4, "Should have multiple queries"

        for query_id, query_text in queries.items():
            assert isinstance(query_id, str), "Query IDs should be strings for TPC-DI"
            assert isinstance(query_text, str), "Query text should be a string"
            assert len(query_text.strip()) > 0, "Query text should not be empty"

        if len(queries) > 0:
            first_query_id = next(iter(queries.keys()))
            individual_query = tpcdi.get_query(first_query_id)
            assert isinstance(individual_query, str), "Individual query should be a string"
            assert len(individual_query.strip()) > 0, "Individual query should not be empty"

        for query_id, query_sql in queries.items():
            assert isinstance(query_sql, str)
            assert query_sql.strip()

            query_upper = query_sql.upper()
            assert "SELECT" in query_upper
            assert "FROM" in query_upper

            if query_id.startswith("V"):
                validation_keywords = ["COUNT", "WHERE", "NULL", "DISTINCT"]
                has_validation = any(kw in query_upper for kw in validation_keywords)
                assert has_validation, f"Validation query {query_id} should contain validation patterns"

            elif query_id.startswith("A"):
                analytical_keywords = ["GROUP BY", "ORDER BY", "SUM", "COUNT", "AVG"]
                has_analytics = any(kw in query_upper for kw in analytical_keywords)
                assert has_analytics, f"Analytical query {query_id} should contain analytical patterns"

    def test_get_query(self, tpcdi: TPCDI) -> None:
        queries = tpcdi.get_queries()
        first_query_id = list(queries.keys())[0]

        query = tpcdi.get_query(first_query_id)
        assert isinstance(query, str)
        assert "SELECT" in query.upper()
        assert "FROM" in query.upper()

        validation_queries = [q for q in queries if q.startswith("V")]
        if validation_queries:
            val_query = tpcdi.get_query(validation_queries[0])
            assert isinstance(val_query, str)
            assert "SELECT" in val_query.upper()

    def test_translate_query(self, tpcdi: TPCDI, sql_dialect: str) -> None:
        queries = tpcdi.get_queries()
        first_query_id = list(queries.keys())[0]

        tpcdi.get_query(first_query_id)
        translated_query = tpcdi.translate_query(first_query_id, dialect=sql_dialect)

        assert isinstance(translated_query, str)
        assert "SELECT" in translated_query.upper()

    def test_invalid_query_id(self, tpcdi: TPCDI) -> None:
        with pytest.raises(ValueError):
            tpcdi.get_query("X999")

    def test_get_query_with_params(self, tpcdi: TPCDI) -> None:
        queries = tpcdi.get_queries()
        first_query_id = list(queries.keys())[0]

        param_query = tpcdi.get_query(first_query_id)
        assert isinstance(param_query, str)
        assert "SELECT" in param_query.upper()

        analytical_queries = [q for q in queries if q.startswith("A")]
        if analytical_queries:
            custom_params = {"min_trades": 100, "start_date": "2023-01-01"}
            param_query = tpcdi.get_query(analytical_queries[0], params=custom_params)
            assert isinstance(param_query, str)
            assert "SELECT" in param_query.upper()

    def test_get_schema(self, tpcdi: TPCDI) -> None:
        schema = tpcdi.get_schema()

        assert isinstance(schema, dict), "Schema should be a dictionary"
        expected_tables = [
            "DimCustomer",
            "DimAccount",
            "DimSecurity",
            "DimCompany",
            "FactTrade",
            "DimDate",
            "DimTime",
        ]

        for table in expected_tables:
            assert table in schema, f"Table {table} not found in schema"

        dimcustomer_table = schema["DimCustomer"]
        customer_columns = [col["name"] for col in dimcustomer_table["columns"]]
        expected_customer_columns = [
            "SK_CustomerID",
            "CustomerID",
            "TaxID",
            "Status",
            "LastName",
            "FirstName",
            "MiddleInitial",
            "Gender",
            "Tier",
            "DOB",
            "AddressLine1",
            "AddressLine2",
            "PostalCode",
            "City",
            "StateProv",
            "Country",
            "Phone1",
            "Phone2",
            "Phone3",
            "Email1",
            "Email2",
            "EffectiveDate",
            "EndDate",
            "IsCurrent",
        ]

        for column in expected_customer_columns:
            assert column in customer_columns

        facttrade_table = schema["FactTrade"]
        trade_columns = [col["name"] for col in facttrade_table["columns"]]
        expected_trade_columns = [
            "TradeID",
            "SK_BrokerID",
            "SK_CreateDateID",
            "SK_CreateTimeID",
            "SK_CloseDateID",
            "SK_CloseTimeID",
            "Status",
            "Type",
            "CashFlag",
            "SK_SecurityID",
            "SK_CompanyID",
            "Quantity",
            "BidPrice",
            "SK_CustomerID",
            "SK_AccountID",
            "ExecutedBy",
            "TradePrice",
            "Fee",
            "Commission",
            "Tax",
        ]

        for column in expected_trade_columns:
            assert column in trade_columns

    def test_get_create_tables_sql(self, tpcdi: TPCDI) -> None:
        sql = tpcdi.get_create_tables_sql()

        assert isinstance(sql, str)
        assert "CREATE TABLE" in sql

        expected_core_tables = [
            "DimCustomer",
            "DimAccount",
            "DimSecurity",
            "DimCompany",
            "FactTrade",
            "DimDate",
            "DimTime",
        ]

        expected_extended_tables = [
            "DimBroker",
            "FactCashBalances",
            "FactHoldings",
            "FactMarketHistory",
            "FactWatches",
            "Industry",
            "StatusType",
            "TaxRate",
            "TradeType",
        ]

        all_expected_tables = expected_core_tables + expected_extended_tables

        for table in all_expected_tables:
            assert f"CREATE TABLE IF NOT EXISTS {table}" in sql

    def test_tpcdi_properties(self, tpcdi: TPCDI) -> None:
        schema = tpcdi.get_schema()
        assert len(schema) == 16, f"Expected 16 tables (Phase 1), got {len(schema)}"

        table_names = list(schema.keys())
        dimension_tables = [t for t in table_names if t.startswith("Dim")]
        fact_tables = [t for t in table_names if t.startswith("Fact")]
        reference_tables = [t for t in table_names if not (t.startswith(("Dim", "Fact")))]

        assert len(dimension_tables) == 7, f"Should have 7 dimension tables, got {len(dimension_tables)}"
        assert len(fact_tables) == 5, f"Should have 5 fact tables, got {len(fact_tables)}"
        assert len(reference_tables) == 4, f"Should have 4 reference tables, got {len(reference_tables)}"

        queries = tpcdi.get_queries()
        validation_queries = [q for q in queries if q.startswith("V")]
        analytical_queries = [q for q in queries if q.startswith("A")]

        assert len(validation_queries) >= 1, "Should have validation queries"
        assert len(analytical_queries) >= 1, "Should have analytical queries"

    def test_data_warehouse_focus(self, tpcdi: TPCDI) -> None:
        schema = tpcdi.get_schema()

        table_names = list(schema.keys())
        dimension_tables = [t for t in table_names if t.startswith("Dim")]
        fact_tables = [t for t in table_names if t.startswith("Fact")]

        assert len(dimension_tables) >= 4, "Should have multiple dimension tables"
        assert len(fact_tables) >= 1, "Should have fact tables"

        dimcustomer_table = schema["DimCustomer"]
        customer_columns = [col["name"] for col in dimcustomer_table["columns"]]

        scd_columns = ["EffectiveDate", "EndDate", "IsCurrent"]
        for col in scd_columns:
            assert col in customer_columns, f"SCD column {col} not found in DimCustomer"

    def test_financial_services_domain(self, tpcdi: TPCDI) -> None:
        schema = tpcdi.get_schema()

        table_names = list(schema.keys())
        financial_tables = ["DimAccount", "DimSecurity", "DimCompany", "FactTrade"]

        for table in financial_tables:
            assert table in table_names, f"Financial table {table} not found"

        facttrade_table = schema["FactTrade"]
        trade_columns = [col["name"] for col in facttrade_table["columns"]]

        financial_columns = ["TradePrice", "Fee", "Commission", "Tax", "BidPrice"]
        for col in financial_columns:
            assert col in trade_columns, f"Financial column {col} not found"

    def test_etl_validation_queries(self, tpcdi: TPCDI) -> None:
        queries = tpcdi.get_queries()
        validation_queries = {k: v for k, v in queries.items() if k.startswith("V")}

        assert len(validation_queries) >= 1, "Should have validation queries"

        for query_id, query_sql in validation_queries.items():
            query_upper = query_sql.upper()

            quality_keywords = ["COUNT", "NULL", "DISTINCT", "WHERE"]
            has_quality_check = any(kw in query_upper for kw in quality_keywords)
            assert has_quality_check, f"Validation query {query_id} should check data quality"

    def test_business_intelligence_queries(self, tpcdi: TPCDI) -> None:
        queries = tpcdi.get_queries()
        analytical_queries = {k: v for k, v in queries.items() if k.startswith("A")}

        assert len(analytical_queries) >= 1, "Should have analytical queries"

        for query_id, query_sql in analytical_queries.items():
            query_upper = query_sql.upper()

            analysis_keywords = ["GROUP BY", "SUM", "COUNT", "AVG", "ORDER BY"]
            has_analysis = any(kw in query_upper for kw in analysis_keywords)
            assert has_analysis, f"Analytical query {query_id} should perform business analysis"


class TestTPCDIBenchmarkCoverage:
    @pytest.fixture
    def tpcdi_benchmark(self, temp_dir: Path) -> TPCDIBenchmark:
        return TPCDIBenchmark(scale_factor=0.01, output_dir=temp_dir)

    def test_execute_query_coverage(self, tpcdi_benchmark: TPCDIBenchmark) -> None:
        connection = duckdb.connect(":memory:")
        connection.execute("""
            CREATE TABLE DimCustomer (
                CustomerID INTEGER,
                EffectiveDate DATE,
                CustomerName VARCHAR(100),
                Status VARCHAR(10),
                IsCurrent INTEGER DEFAULT 1
            )
        """)
        connection.execute("""
            INSERT INTO DimCustomer VALUES
            (1, '2023-01-01', 'Customer1', 'Active', 1),
            (2, '2023-01-02', 'Customer2', 'Active', 1)
        """)

        result = tpcdi_benchmark.execute_query("V1", connection)

        assert isinstance(result, list)
        assert len(result) > 0

        connection.close()

    @pytest.mark.slow
    def test_load_data_to_database_coverage(self, tpcdi_benchmark: TPCDIBenchmark) -> None:
        tpcdi_benchmark.generate_data()

        connection = duckdb.connect(":memory:")

        try:
            tpcdi_benchmark.load_data_to_database(connection)

            tables_result = connection.execute("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'main'
            """).fetchall()

            table_names = [row[0] for row in tables_result]
            assert "DimCustomer" in table_names or "dimcustomer" in table_names

            if "DimCustomer" in table_names:
                count = connection.execute("SELECT COUNT(*) FROM DimCustomer").fetchone()[0]
            else:
                count = connection.execute("SELECT COUNT(*) FROM dimcustomer").fetchone()[0]
            assert count > 0

        finally:
            connection.close()

    @pytest.mark.slow
    def test_run_benchmark_coverage(self, tpcdi_benchmark: TPCDIBenchmark) -> None:
        tpcdi_benchmark.generate_data()

        connection = duckdb.connect(":memory:")

        try:
            tpcdi_benchmark.load_data_to_database(connection)

            result = tpcdi_benchmark.run_benchmark(connection, iterations=1)

            assert result["benchmark"] == "TPC-DI"
            assert "queries" in result
            assert isinstance(result["queries"], dict)

        finally:
            connection.close()

    def test_error_handling_paths(self, tpcdi_benchmark: TPCDIBenchmark) -> None:
        connection = duckdb.connect(":memory:")

        try:
            with pytest.raises(ValueError, match="No data generated"):
                tpcdi_benchmark.load_data_to_database(connection)
        finally:
            connection.close()

        connection = duckdb.connect(":memory:")

        try:
            with pytest.raises(Exception):
                tpcdi_benchmark.execute_query("V1", connection)
        finally:
            connection.close()
