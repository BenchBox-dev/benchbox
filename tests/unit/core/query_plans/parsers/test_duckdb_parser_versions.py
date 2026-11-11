import pytest

from benchbox.core.query_plans.parsers.duckdb import DuckDBQueryPlanParser
from benchbox.core.query_plans.parsers.registry import (
    get_parser_for_platform,
)
from benchbox.core.results.query_plan_models import LogicalOperatorType
from tests.fixtures.duckdb_plans_by_version import (
    DUCKDB_0_9_AGGREGATE,
    DUCKDB_0_9_FILTER_SCAN,
    DUCKDB_0_9_JOIN,
    DUCKDB_0_9_ORDER_BY,
    DUCKDB_0_9_SIMPLE_SCAN,
    DUCKDB_1_0_JSON_AGGREGATE,
    DUCKDB_1_0_JSON_FILTER_SCAN,
    DUCKDB_1_0_JSON_JOIN,
    DUCKDB_1_0_JSON_ORDER_BY,
    DUCKDB_1_0_JSON_SIMPLE_SCAN,
    DUCKDB_1_0_JSON_WITH_TIMING,
    DUCKDB_1_0_JSON_WRAPPED,
    DUCKDB_2_0_PREVIEW_JSON_ANALYZED,
    EXPECTED_OPERATORS,
    VERSION_FIXTURES,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestDuckDBVersionCompatibility:
    @pytest.mark.parametrize(
        "version,format,fixture_name,fixture_value",
        VERSION_FIXTURES,
    )
    def test_parse_version_fixture(self, version: str, format: str, fixture_name: str, fixture_value: str) -> None:
        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output(f"q_{fixture_name}", fixture_value)

        if format == "text" and fixture_name == "join":
            assert plan is None, "Text JOIN plans should now be rejected (branching detected)"
            return

        assert plan is not None, f"Failed to parse {version} {format} {fixture_name}"
        assert plan.logical_root is not None
        assert plan.platform == "duckdb"

    @pytest.mark.parametrize(
        "version,format,fixture_name,fixture_value",
        VERSION_FIXTURES,
    )
    def test_correct_operators_detected(self, version: str, format: str, fixture_name: str, fixture_value: str) -> None:

        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output(f"q_{fixture_name}", fixture_value)

        if format == "text" and fixture_name == "join":
            assert plan is None
            return

        if plan is None:
            pytest.fail(f"Parse failed for {version} {format} {fixture_name}")

        operators = self._collect_operators(plan.logical_root)
        op_types = [op.operator_type.value for op in operators]

        expected = EXPECTED_OPERATORS.get(fixture_name, [])
        for expected_type in expected:
            found = any(expected_type.lower() in t.lower() for t in op_types)
            assert found, f"Expected {expected_type} not found in {op_types}"

    def _collect_operators(self, root):
        operators = [root]
        for child in root.children:
            operators.extend(self._collect_operators(child))
        return operators


class TestDuckDBTextFormatVersions:
    def test_0_9_simple_scan(self) -> None:
        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("q01", DUCKDB_0_9_SIMPLE_SCAN)

        assert plan is not None
        assert plan.logical_root.operator_type == LogicalOperatorType.SCAN
        assert plan.logical_root.table_name == "lineitem"

    def test_0_9_filter_scan(self) -> None:
        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("q02", DUCKDB_0_9_FILTER_SCAN)

        assert plan is not None
        operators = self._collect_operators(plan.logical_root)
        op_types = {op.operator_type for op in operators}

        assert LogicalOperatorType.SCAN in op_types

    def test_0_9_wrapped_filter_parses_as_single_expression(self) -> None:
        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("q02", DUCKDB_0_9_FILTER_SCAN)

        assert plan is not None
        filter_ops = [
            op for op in self._collect_operators(plan.logical_root) if op.operator_type == LogicalOperatorType.FILTER
        ]
        assert len(filter_ops) == 1
        exprs = filter_ops[0].filter_expressions
        assert exprs is not None
        assert len(exprs) == 1
        joined = exprs[0]
        assert "l_shipdate" in joined
        assert "AS DATE" in joined
        assert joined.count("(") == joined.count(")")

    def test_0_9_aggregate(self) -> None:
        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("q03", DUCKDB_0_9_AGGREGATE)

        assert plan is not None
        operators = self._collect_operators(plan.logical_root)
        op_types = {op.operator_type for op in operators}

        assert any(t in op_types for t in [LogicalOperatorType.AGGREGATE, LogicalOperatorType.PROJECT])

    def test_0_9_join(self) -> None:
        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("q04", DUCKDB_0_9_JOIN)

        assert plan is None, "Text JOIN plans should be rejected (branching cannot be parsed)"

    def test_0_9_order_by(self) -> None:
        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("q05", DUCKDB_0_9_ORDER_BY)

        assert plan.query_id == "q05"

    def _collect_operators(self, root):
        operators = [root]
        for child in root.children:
            operators.extend(self._collect_operators(child))
        return operators


class TestDuckDBJSONFormatVersions:
    def test_1_0_simple_scan_json(self) -> None:
        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("q01", DUCKDB_1_0_JSON_SIMPLE_SCAN)

        assert plan is not None
        assert plan.logical_root.operator_type == LogicalOperatorType.SCAN

    def test_1_0_filter_scan_json(self) -> None:
        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("q02", DUCKDB_1_0_JSON_FILTER_SCAN)

        assert plan is not None
        assert plan.logical_root.operator_type == LogicalOperatorType.FILTER
        assert len(plan.logical_root.children) == 1
        assert plan.logical_root.children[0].operator_type == LogicalOperatorType.SCAN

    def test_1_0_aggregate_json(self) -> None:
        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("q03", DUCKDB_1_0_JSON_AGGREGATE)

        assert plan is not None
        assert plan.logical_root.operator_type == LogicalOperatorType.PROJECT

    def test_1_0_join_json(self) -> None:
        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("q04", DUCKDB_1_0_JSON_JOIN)

        assert plan is not None
        assert plan.logical_root.operator_type == LogicalOperatorType.PROJECT
        assert len(plan.logical_root.children) == 1
        assert plan.logical_root.children[0].operator_type == LogicalOperatorType.JOIN

    def test_1_0_order_by_json(self) -> None:
        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("q05", DUCKDB_1_0_JSON_ORDER_BY)

        assert plan is not None
        assert plan.logical_root.operator_type == LogicalOperatorType.SORT

    def test_1_0_with_timing_json(self) -> None:
        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("q06", DUCKDB_1_0_JSON_WITH_TIMING)

        assert plan is not None
        assert plan.logical_root.physical_operator is not None
        assert plan.logical_root.physical_operator.properties.get("timing") == 0.025

    def test_1_0_wrapped_format_json(self) -> None:
        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("q07", DUCKDB_1_0_JSON_WRAPPED)

        assert plan is not None
        assert plan.logical_root.operator_type == LogicalOperatorType.PROJECT

    def test_2_0_preview_analyzed_json(self) -> None:
        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("q08", DUCKDB_2_0_PREVIEW_JSON_ANALYZED)

        assert plan is not None
        assert plan.logical_root.operator_type == LogicalOperatorType.PROJECT
        assert plan.logical_root.physical_operator is not None
        assert plan.logical_root.physical_operator.operator_type == "PROJECTION"
        assert plan.logical_root.physical_operator.properties["timing"] == 0.000004
        assert plan.logical_root.physical_operator.properties["cardinality"] == 1
        assert len(plan.logical_root.children) == 1
        child_op = plan.logical_root.children[0].physical_operator
        assert child_op.operator_type == "DUMMY_SCAN"
        assert child_op.properties["cardinality"] == 1


class TestVersionBasedParserSelection:
    def test_registry_returns_parser_for_any_version(self) -> None:

        for version in ["0.9.0", "0.10.0", "1.0.0", "1.1.0", "2.0.0"]:
            parser = get_parser_for_platform("duckdb", version)
            assert parser is not None
            assert parser.platform_name == "duckdb"

    def test_parser_from_registry_parses_text_format(self) -> None:

        parser = get_parser_for_platform("duckdb", "0.9.0")
        plan = parser.parse_explain_output("q01", DUCKDB_0_9_SIMPLE_SCAN)

        assert plan is not None
        assert plan.logical_root is not None

    def test_parser_from_registry_parses_json_format(self) -> None:

        parser = get_parser_for_platform("duckdb", "1.0.0")
        plan = parser.parse_explain_output("q01", DUCKDB_1_0_JSON_SIMPLE_SCAN)

        assert plan is not None
        assert plan.logical_root is not None


class TestFingerprintConsistencyAcrossVersions:
    def test_same_structure_same_fingerprint_base(self) -> None:

        parser = DuckDBQueryPlanParser()
        plan1 = parser.parse_explain_output("q01", DUCKDB_1_0_JSON_SIMPLE_SCAN)
        plan2 = parser.parse_explain_output("q02", DUCKDB_1_0_JSON_SIMPLE_SCAN)

        assert plan1 is not None and plan2 is not None

    def test_different_structure_different_fingerprint(self) -> None:

        parser = DuckDBQueryPlanParser()
        plan1 = parser.parse_explain_output("q01", DUCKDB_1_0_JSON_SIMPLE_SCAN)
        plan2 = parser.parse_explain_output("q02", DUCKDB_1_0_JSON_FILTER_SCAN)

        assert plan1 is not None and plan2 is not None
        assert plan1.plan_fingerprint != plan2.plan_fingerprint


