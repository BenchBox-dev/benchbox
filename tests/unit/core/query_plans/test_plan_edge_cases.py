from __future__ import annotations

import concurrent.futures
import threading

import pytest

from benchbox.core.query_plans.parsers.duckdb import DuckDBQueryPlanParser
from benchbox.core.query_plans.parsers.postgresql import PostgreSQLQueryPlanParser
from benchbox.core.results.query_plan_models import (
    LogicalOperator,
    LogicalOperatorType,
    QueryPlanDAG,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestLargePlans:
    def _create_deep_duckdb_plan(self, depth: int) -> str:
        lines = []
        for i in range(depth):
            indent = i * 2
            lines.append(" " * indent + "┌" + "─" * 25 + "┐")
            if i < depth - 1:
                lines.append(" " * indent + "│         PROJECTION       │")
            else:
                lines.append(" " * indent + "│         SEQ_SCAN         │")
                lines.append(" " * indent + "│   ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─   │")
                lines.append(" " * indent + "│          lineitem        │")
            lines.append(" " * indent + "└" + "─" * 11 + "┬" + "─" * 11 + "┘")
        return "\n".join(lines)

    def _count_operators(self, root: LogicalOperator) -> int:
        count = 1
        for child in root.children:
            count += self._count_operators(child)
        return count

    def test_parse_plan_with_100_operators(self) -> None:
        explain = self._create_deep_duckdb_plan(100)
        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("test_large", explain)

        assert plan is not None
        assert self._count_operators(plan.logical_root) >= 100

    def test_parse_plan_with_50_operators(self) -> None:
        explain = self._create_deep_duckdb_plan(50)
        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("test_medium", explain)

        assert plan is not None
        assert self._count_operators(plan.logical_root) >= 50

    def test_serialize_large_plan(self) -> None:

        explain = self._create_deep_duckdb_plan(100)
        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("test_serial", explain)

        assert plan is not None
        json_str = plan.to_json(max_depth=150)
        assert len(json_str) > 0

        restored = QueryPlanDAG.from_json(json_str)
        assert restored.query_id == plan.query_id

    def test_large_plan_fingerprint_computed(self) -> None:

        explain = self._create_deep_duckdb_plan(100)
        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("test_fp", explain)

        assert plan is not None
        assert plan.plan_fingerprint is not None
        assert len(plan.plan_fingerprint) == 64


class TestWideUnionPlans:
    def _create_wide_union_plan(self, num_branches: int) -> str:
        lines = []
        lines.append("┌" + "─" * 25 + "┐")
        lines.append("│         UNION_ALL        │")
        lines.append("└" + "─" * 11 + "┬" + "─" * 11 + "┘")

        for i in range(num_branches):
            lines.append("┌" + "─" * 11 + "┴" + "─" * 11 + "┐")
            lines.append("│         SEQ_SCAN         │")
            lines.append("│   ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─   │")
            lines.append(f"│        table_{i:03d}         │")
            lines.append("└" + "─" * 25 + "┘")

        return "\n".join(lines)

    def test_union_with_10_branches(self) -> None:
        explain = self._create_wide_union_plan(10)
        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("test_wide", explain)

        assert plan is None, "Text UNION plans should be rejected (branching cannot be parsed)"

    def test_union_with_20_branches(self) -> None:

        explain = self._create_wide_union_plan(20)
        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("test_wide_20", explain)

        assert plan is None, "Text UNION plans should be rejected (branching cannot be parsed)"


class TestSpecialCharacters:
    def test_table_name_with_underscore(self) -> None:

        explain = """
┌───────────────────────────┐
│         SEQ_SCAN          │
│   ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─   │
│     my_table_name_v2      │
└───────────────────────────┘
"""
        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("test_underscore", explain)

        assert plan is not None
        assert plan.logical_root.operator_type == LogicalOperatorType.SCAN

    def test_postgresql_json_with_special_chars(self) -> None:

        explain = """[{
            "Plan": {
                "Node Type": "Seq Scan",
                "Relation Name": "table_with_special_chars_123",
                "Alias": "t",
                "Startup Cost": 0.00,
                "Total Cost": 15.50,
                "Plan Rows": 100,
                "Plan Width": 48,
                "Filter": "(column_name >= 'value with spaces')"
            }
        }]"""

        parser = PostgreSQLQueryPlanParser()
        plan = parser.parse_explain_output("test_special", explain)

        assert plan is not None
        assert plan.logical_root.table_name == "table_with_special_chars_123"


class TestParserThreadSafety:
    def test_duckdb_parser_thread_safety(self) -> None:

        explain = """
┌───────────────────────────┐
│         PROJECTION        │
└─────────────┬─────────────┘
┌─────────────┴─────────────┐
│         SEQ_SCAN          │
│   ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─   │
│          lineitem         │
└───────────────────────────┘
"""
        parser = DuckDBQueryPlanParser()
        results = []
        errors = []

        def parse_in_thread(idx: int) -> None:
            try:
                plan = parser.parse_explain_output(f"query_{idx}", explain)
                results.append((idx, plan))
            except Exception as e:
                errors.append((idx, e))

        threads = []
        for i in range(20):
            t = threading.Thread(target=parse_in_thread, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert len(errors) == 0, f"Errors occurred: {errors}"
        assert len(results) == 20

        query_ids = [r[1].query_id for r in results]
        assert len(set(query_ids)) == 20

        for idx, plan in results:
            assert plan.logical_root is not None

    def test_concurrent_futures_parser_safety(self) -> None:

        explain = """
┌───────────────────────────┐
│         FILTER            │
└─────────────┬─────────────┘
┌─────────────┴─────────────┐
│         SEQ_SCAN          │
│   ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─   │
│           orders          │
└───────────────────────────┘
"""
        parser = DuckDBQueryPlanParser()

        def parse_task(idx: int) -> QueryPlanDAG | None:
            return parser.parse_explain_output(f"concurrent_{idx}", explain)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(parse_task, i) for i in range(10)]
            plans = [f.result() for f in futures]

        assert all(p is not None for p in plans)

        query_ids = [p.query_id for p in plans]
        assert len(set(query_ids)) == 10

    def test_postgresql_parser_thread_safety(self) -> None:

        explain = """[{
            "Plan": {
                "Node Type": "Seq Scan",
                "Relation Name": "orders",
                "Startup Cost": 0.00,
                "Total Cost": 15.50,
                "Plan Rows": 100,
                "Plan Width": 48
            }
        }]"""

        parser = PostgreSQLQueryPlanParser()

        def parse_task(idx: int) -> QueryPlanDAG | None:
            return parser.parse_explain_output(f"pg_{idx}", explain)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(parse_task, i) for i in range(10)]
            plans = [f.result() for f in futures]

        assert all(p is not None for p in plans)


class TestOperatorIdReset:
    def test_operator_id_resets_between_parses_duckdb(self) -> None:

        explain = """
┌───────────────────────────┐
│         SEQ_SCAN          │
│   ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─   │
│          lineitem         │
└───────────────────────────┘
"""
        parser = DuckDBQueryPlanParser()

        plan1 = parser.parse_explain_output("q1", explain)
        plan2 = parser.parse_explain_output("q2", explain)
        plan3 = parser.parse_explain_output("q3", explain)

        assert plan1 is not None
        assert plan2 is not None
        assert plan3 is not None

        assert plan1.logical_root.operator_id == plan2.logical_root.operator_id
        assert plan2.logical_root.operator_id == plan3.logical_root.operator_id

    def test_operator_id_resets_between_parses_postgresql(self) -> None:

        explain = """[{
            "Plan": {
                "Node Type": "Seq Scan",
                "Relation Name": "orders",
                "Startup Cost": 0.00,
                "Total Cost": 15.50,
                "Plan Rows": 100,
                "Plan Width": 48
            }
        }]"""

        parser = PostgreSQLQueryPlanParser()

        plan1 = parser.parse_explain_output("q1", explain)
        plan2 = parser.parse_explain_output("q2", explain)

        assert plan1 is not None
        assert plan2 is not None
        assert plan1.logical_root.operator_id == plan2.logical_root.operator_id


class TestEmptyAndInvalidInput:
    def test_empty_string_returns_none(self) -> None:

        parser = DuckDBQueryPlanParser()
        result = parser.parse_explain_output("empty", "")
        assert result is None

    def test_whitespace_only_returns_none(self) -> None:

        parser = DuckDBQueryPlanParser()
        result = parser.parse_explain_output("whitespace", "   \n\n   ")
        assert result is None

    def test_invalid_json_postgresql_returns_none(self) -> None:

        parser = PostgreSQLQueryPlanParser()
        result = parser.parse_explain_output("invalid", "not valid json {}")
        assert result is None

    def test_no_plan_key_postgresql_returns_none(self) -> None:

        parser = PostgreSQLQueryPlanParser()
        result = parser.parse_explain_output("no_plan", '[{"NotPlan": {}}]')
        assert result is None


class TestPlanValidation:
    def test_plan_has_required_fields(self) -> None:
        explain = """
┌───────────────────────────┐
│         SEQ_SCAN          │
│   ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─   │
│          lineitem         │
└───────────────────────────┘
"""
        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("test", explain)

        assert plan is not None
        assert plan.query_id == "test"
        assert plan.platform == "duckdb"
        assert plan.logical_root is not None
        assert plan.plan_fingerprint is not None
        assert plan.raw_explain_output is not None

    def test_operator_has_required_fields(self) -> None:
        explain = """
┌───────────────────────────┐
│         SEQ_SCAN          │
│   ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─   │
│          lineitem         │
└───────────────────────────┘
"""
        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("test", explain)

        assert plan is not None
        root = plan.logical_root
        assert root.operator_type is not None
        assert root.operator_id is not None
        assert root.children is not None


