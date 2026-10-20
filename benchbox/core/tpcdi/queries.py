# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ DI (TPC-DI) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-DI specification.

# Licensed under the MIT License. See LICENSE file in the project root for details.

import json
from collections import Counter
from functools import lru_cache
from typing import Any, Optional

from benchbox.core.query_manager import ParameterizedQueryManager, copy_query_metadata
from benchbox.utils.dialect_utils import translate_sql_query

from .query_analytics import TPCDIAnalyticalQueries
from .query_etl import TPCDIETLQueries
from .query_validation import TPCDIValidationQueries

_BASE_QUERY_DATA_JSON = r"""{
"queries": {
"V1": "\nSELECT\n    COUNT(*) as total_customers,\n    COUNT(DISTINCT CustomerID) as unique_customers,\n    SUM(CASE WHEN IsCurrent = 1 THEN 1 ELSE 0 END) as current_customers\nFROM DimCustomer;\n",
"V2": "\nSELECT\n    Status,\n    COUNT(*) as account_count,\n    SUM(CASE WHEN IsCurrent = 1 THEN 1 ELSE 0 END) as current_accounts\nFROM DimAccount\nGROUP BY Status\nORDER BY Status;\n",
"V3": "\nSELECT\n    Status,\n    Type,\n    COUNT(*) as trade_count,\n    SUM(Quantity) as total_quantity,\n    AVG(TradePrice) as avg_trade_price,\n    SUM(Fee + Commission + Tax) as total_fees\nFROM FactTrade\nGROUP BY Status, Type\nORDER BY Status, Type;\n",
"A1": "\nSELECT\n    c.Tier,\n    COUNT(DISTINCT c.SK_CustomerID) as customer_count,\n    COUNT(t.TradeID) as trade_count,\n    SUM(t.Quantity * t.TradePrice) as total_value,\n    AVG(t.TradePrice) as avg_trade_price\nFROM DimCustomer c\nLEFT JOIN FactTrade t ON c.SK_CustomerID = t.SK_CustomerID\nWHERE c.IsCurrent = 1\nGROUP BY c.Tier\nORDER BY c.Tier;\n",
"A2": "\nSELECT\n    comp.Industry,\n    comp.SPrating,\n    COUNT(DISTINCT comp.SK_CompanyID) as company_count,\n    COUNT(t.TradeID) as trade_count,\n    AVG(t.TradePrice) as avg_trade_price,\n    SUM(t.Quantity * t.TradePrice) as total_trade_value\nFROM DimCompany comp\nLEFT JOIN FactTrade t ON comp.SK_CompanyID = t.SK_CompanyID\nWHERE comp.IsCurrent = 1\nGROUP BY comp.Industry, comp.SPrating\nHAVING COUNT(t.TradeID) > {min_trades}\nORDER BY total_trade_value DESC\nLIMIT {limit_rows};\n",
"A3": "\nSELECT\n    s.Symbol,\n    s.Name,\n    COUNT(t.TradeID) as trade_count,\n    SUM(t.Quantity) as total_quantity,\n    AVG(t.TradePrice) as avg_price,\n    MIN(t.TradePrice) as min_price,\n    MAX(t.TradePrice) as max_price,\n    SUM(t.Quantity * t.TradePrice) as total_value\nFROM DimSecurity s\nJOIN FactTrade t ON s.SK_SecurityID = t.SK_SecurityID\nWHERE s.IsCurrent = 1\n  AND t.Status = 'Completed'\nGROUP BY s.Symbol, s.Name\nHAVING COUNT(t.TradeID) > {min_trades}\nORDER BY total_value DESC\nLIMIT {limit_rows};\n",
"A4": "\nSELECT\n    d.CalendarYearID,\n    d.CalendarQtrID,\n    COUNT(t.TradeID) as trade_count,\n    SUM(t.Quantity * t.TradePrice) as total_value,\n    AVG(t.TradePrice) as avg_price,\n    SUM(t.Fee + t.Commission + t.Tax) as total_fees\nFROM DimDate d\nJOIN FactTrade t ON d.SK_DateID = t.SK_CreateDateID\nWHERE d.CalendarYearID >= {start_year}\n  AND d.CalendarYearID <= {end_year}\nGROUP BY d.CalendarYearID, d.CalendarQtrID\nORDER BY d.CalendarYearID, d.CalendarQtrID;\n",
"A5": "\nSELECT\n    c.Country,\n    c.StateProv,\n    COUNT(DISTINCT c.SK_CustomerID) as customer_count,\n    COUNT(t.TradeID) as trade_count,\n    SUM(t.Quantity * t.TradePrice) as total_trade_value,\n    AVG(c.NetWorth) as avg_net_worth\nFROM DimCustomer c\nLEFT JOIN FactTrade t ON c.SK_CustomerID = t.SK_CustomerID\nWHERE c.IsCurrent = 1\nGROUP BY c.Country, c.StateProv\nHAVING customer_count > {min_customers}\nORDER BY total_trade_value DESC\nLIMIT {limit_rows};\n"
},
"metadata": {
"V1": {"relies_on": ["DimCustomer"], "query_type": "validation", "description": "Validates customer dimension data integrity and current status"},
"V2": {"relies_on": ["DimAccount"], "query_type": "validation", "description": "Validates account dimension data with status breakdown"},
"V3": {"relies_on": ["FactTrade"], "query_type": "validation", "description": "Validates trade fact table data quality and completeness"},
"A1": {"relies_on": ["DimCustomer", "FactTrade", "V1", "V3"], "query_type": "analytical", "description": "Analysis of customer trading patterns by tier"},
"A2": {"relies_on": ["DimCompany", "FactTrade", "V3"], "query_type": "analytical", "description": "Company performance analysis by industry and rating"},
"A3": {"relies_on": ["DimSecurity", "FactTrade", "V3"], "query_type": "analytical", "description": "Security trading volume and price analysis"},
"A4": {"relies_on": ["DimDate", "FactTrade", "V3"], "query_type": "analytical", "description": "Time-based trading patterns by year and quarter"},
"A5": {"relies_on": ["DimCustomer", "FactTrade", "V1", "V3"], "query_type": "analytical", "description": "Geographic distribution of customers and trading activity"}
}
}"""


@lru_cache(maxsize=1)
def _base_query_data() -> dict[str, Any]:
    return json.loads(_BASE_QUERY_DATA_JSON)


class TPCDIQueryManager(ParameterizedQueryManager):
    def __init__(self) -> None:
        self.validation_queries = TPCDIValidationQueries()
        self.analytical_queries = TPCDIAnalyticalQueries()
        self.etl_queries = TPCDIETLQueries()
        self._base_queries = self._load_base_queries()
        self._queries = self._base_queries
        self._base_query_metadata = self._load_base_query_metadata()
        self._all_queries = self._build_comprehensive_catalog()
        self._all_metadata = self._build_comprehensive_metadata()

    def _load_base_queries(self) -> dict[str, str]:
        return dict(_base_query_data()["queries"])

    def _load_base_query_metadata(self) -> dict[str, dict[str, Any]]:
        return copy_query_metadata(_base_query_data()["metadata"])

    def _build_comprehensive_catalog(self) -> dict[str, str]:
        return {
            **self._base_queries,
            **self.validation_queries.get_all_queries(),
            **self.analytical_queries.get_all_queries(),
            **self.etl_queries.get_all_queries(),
        }

    def _build_comprehensive_metadata(self) -> dict[str, dict[str, Any]]:
        metadata = dict(self._base_query_metadata)
        metadata.update(copy_query_metadata(self.validation_queries._query_metadata))
        metadata.update(copy_query_metadata(self.analytical_queries._query_metadata))
        metadata.update(copy_query_metadata(self.etl_queries._query_metadata))
        return metadata

    def get_query(
        self,
        query_id: str,
        params: Optional[dict[str, Any]] = None,
        dialect: Optional[str] = None,
    ) -> str:
        if query_id in self._base_queries:
            query = super().get_query(query_id, params)
        elif query_id.startswith("VQ"):
            query = self.validation_queries.get_query(query_id, params)
        elif query_id.startswith("AQ"):
            query = self.analytical_queries.get_query(query_id, params)
        elif query_id.startswith("EQ"):
            query = self.etl_queries.get_query(query_id, params)
        else:
            available = ", ".join(sorted(self._all_queries.keys()))
            raise ValueError(f"Invalid query ID: {query_id}. Available: {available}")

        return self.translate_query_text(query, dialect) if dialect and dialect != "standard" else query

    def get_all_queries(self) -> dict[str, str]:
        return self._all_queries.copy()

    def _generate_default_params(self, query_id: str) -> dict[str, Any]:
        return {"min_trades": 100, "min_customers": 10, "start_year": 2015, "end_year": 2019, "limit_rows": 50}

    def get_query_dependencies(self, query_id: str) -> list[str]:
        if query_id not in self._all_metadata:
            available = ", ".join(sorted(self._all_metadata.keys()))
            raise ValueError(f"Invalid query ID: {query_id}. Available: {available}")
        return self._all_metadata[query_id]["relies_on"].copy()

    def get_query_metadata(self, query_id: str) -> dict[str, Any]:
        return self._get_query_metadata_copy(self._all_metadata, query_id, "query")

    def get_queries_by_type(self, query_type: str) -> list[str]:
        return self._query_ids_for_valid_metadata(self._all_metadata, "query_type", query_type, "query type")

    def resolve_query_order(self, query_ids: Optional[list[str]] = None) -> list[str]:
        query_ids = list(self._all_queries.keys()) if query_ids is None else query_ids
        for query_id in query_ids:
            if query_id not in self._all_metadata:
                available = ", ".join(sorted(self._all_metadata.keys()))
                raise ValueError(f"Invalid query ID: {query_id}. Available: {available}")

        query_dependencies = {
            query_id: [dep for dep in self._all_metadata[query_id]["relies_on"] if dep in self._all_metadata]
            for query_id in query_ids
        }
        in_degree = dict.fromkeys(query_ids, 0)
        for query_id in query_ids:
            for dep in query_dependencies[query_id]:
                if dep in in_degree:
                    in_degree[query_id] += 1

        queue = [query_id for query_id in query_ids if in_degree[query_id] == 0]
        result = []
        while queue:
            queue.sort()
            current = queue.pop(0)
            result.append(current)
            for query_id in query_ids:
                if current in query_dependencies[query_id]:
                    in_degree[query_id] -= 1
                    if in_degree[query_id] == 0:
                        queue.append(query_id)

        if len(result) != len(query_ids):
            remaining = [q for q in query_ids if q not in result]
            raise ValueError(f"Circular dependency detected involving queries: {', '.join(remaining)}")
        return result

    def get_execution_plan(self) -> list[tuple[str, str, list[str]]]:
        return [
            (query_id, self._all_metadata[query_id]["query_type"], self._all_metadata[query_id]["relies_on"].copy())
            for query_id in self.resolve_query_order()
        ]

    def get_validation_queries(self) -> dict[str, str]:
        return self.validation_queries.get_all_queries()

    def get_analytical_queries(self) -> dict[str, str]:
        return self.analytical_queries.get_all_queries()

    def get_etl_queries(self) -> dict[str, str]:
        return self.etl_queries.get_all_queries()

    def get_queries_by_category(self, category: str) -> list[str]:
        result = []
        for query_group in (self.validation_queries, self.analytical_queries, self.etl_queries):
            try:
                result.extend(query_group.get_queries_by_category(category))
            except ValueError:
                pass
        return result

    def get_query_statistics(self) -> dict[str, Any]:
        type_counts = Counter(metadata.get("query_type", "unknown") for metadata in self._all_metadata.values())
        category_counts = Counter(metadata.get("category", "uncategorized") for metadata in self._all_metadata.values())
        complexity_counts = Counter(
            metadata["complexity"] for metadata in self._all_metadata.values() if "complexity" in metadata
        )
        severity_counts = Counter(
            metadata["severity"] for metadata in self._all_metadata.values() if "severity" in metadata
        )
        return {
            "total_queries": len(self._all_queries),
            "base_queries": len(self._base_queries),
            "extended_validation_queries": len(self.validation_queries._queries),
            "extended_analytical_queries": len(self.analytical_queries._queries),
            "etl_validation_queries": len(self.etl_queries._queries),
            "queries_by_type": dict(type_counts),
            "queries_by_category": dict(category_counts),
            "queries_by_complexity": dict(complexity_counts),
            "queries_by_severity": dict(severity_counts),
            "query_expansion_factor": len(self._all_queries) / len(self._base_queries),
        }

    def translate_query_text(self, query: str, dialect: str) -> str:
        return translate_sql_query(query, dialect, source_dialect="ansi", identify=False)
