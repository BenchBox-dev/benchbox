import pytest

from benchbox.core.query_plans.parsers.duckdb import DuckDBQueryPlanParser
from benchbox.core.results.query_plan_models import LogicalOperatorType

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


JSON_SIMPLE_SCAN = """{
    "name": "SEQ_SCAN",
    "extra_info": "orders",
    "children": []
}"""

JSON_WITH_CHILDREN = """{
    "name": "PROJECTION",
    "extra_info": "o_orderkey\\no_custkey",
    "children": [
        {
            "name": "SEQ_SCAN",
            "extra_info": "orders",
            "children": []
        }
    ]
}"""

JSON_NESTED_PLAN = """{
    "children": [
        {
            "name": "QUERY_PLAN",
            "children": [
                {
                    "name": "PROJECTION",
                    "extra_info": "result",
                    "children": [
                        {
                            "name": "FILTER",
                            "extra_info": "o_orderdate >= '1995'",
                            "children": [
                                {
                                    "name": "SEQ_SCAN",
                                    "extra_info": "orders",
                                    "children": []
                                }
                            ]
                        }
                    ]
                }
            ]
        }
    ]
}"""

JSON_WITH_TIMING = """{
    "name": "HASH_GROUP_BY",
    "timing": 0.015,
    "cardinality": 100,
    "extra_info": "sum(amount)",
    "children": [
        {
            "name": "SEQ_SCAN",
            "timing": 0.005,
            "cardinality": 1000,
            "extra_info": "lineitem",
            "children": []
        }
    ]
}"""

JSON_JOIN = """{
    "name": "HASH_JOIN",
    "extra_info": "o_custkey = c_custkey",
    "children": [
        {
            "name": "SEQ_SCAN",
            "extra_info": "orders",
            "children": []
        },
        {
            "name": "SEQ_SCAN",
            "extra_info": "customer",
            "children": []
        }
    ]
}"""

JSON_ARRAY_FORMAT = """[
    {
        "name": "PROJECTION",
        "children": [
            {
                "name": "SEQ_SCAN",
                "extra_info": "orders",
                "children": []
            }
        ]
    }
]"""

TEXT_SIMPLE_SCAN = """
┌───────────────────────────┐
│         SEQ_SCAN          │
│   ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─   │
│          orders           │
└───────────────────────────┘
"""


class TestDuckDBParserFormatDetection:
    def test_detect_json_format_object(self) -> None:

        parser = DuckDBQueryPlanParser()
        assert parser._is_json_format(JSON_SIMPLE_SCAN)
        assert parser._is_json_format(JSON_WITH_CHILDREN)
        assert parser._is_json_format(JSON_NESTED_PLAN)

    def test_detect_json_format_array(self) -> None:

        parser = DuckDBQueryPlanParser()
        assert parser._is_json_format(JSON_ARRAY_FORMAT)

    def test_detect_text_format(self) -> None:

        parser = DuckDBQueryPlanParser()
        assert not parser._is_json_format(TEXT_SIMPLE_SCAN)

    def test_detect_text_format_with_whitespace(self) -> None:

        parser = DuckDBQueryPlanParser()
        assert not parser._is_json_format("   \n" + TEXT_SIMPLE_SCAN)

    def test_detect_json_with_whitespace(self) -> None:

        parser = DuckDBQueryPlanParser()
        assert parser._is_json_format("  \n  " + JSON_SIMPLE_SCAN)


class TestDuckDBJSONParser:
    def test_parse_simple_scan_json(self) -> None:

        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("q01", JSON_SIMPLE_SCAN)

        assert plan is not None
        assert plan.query_id == "q01"
        assert plan.platform == "duckdb"
        assert plan.logical_root is not None
        assert plan.logical_root.operator_type == LogicalOperatorType.SCAN
        assert plan.logical_root.table_name == "orders"

    def test_parse_projection_with_scan_json(self) -> None:

        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("q02", JSON_WITH_CHILDREN)

        assert plan is not None
        assert plan.logical_root.operator_type == LogicalOperatorType.PROJECT
        assert len(plan.logical_root.children) == 1
        assert plan.logical_root.children[0].operator_type == LogicalOperatorType.SCAN

    def test_parse_nested_plan_json(self) -> None:

        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("q03", JSON_NESTED_PLAN)

        assert plan is not None
        assert plan.logical_root.operator_type == LogicalOperatorType.PROJECT

    def test_parse_json_with_timing(self) -> None:

        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("q04", JSON_WITH_TIMING)

        assert plan is not None
        assert plan.logical_root.physical_operator is not None
        assert plan.logical_root.physical_operator.properties.get("timing") == 0.015
        assert plan.logical_root.physical_operator.properties.get("cardinality") == 100

    def test_parse_join_json(self) -> None:

        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("q05", JSON_JOIN)

        assert plan is not None
        assert plan.logical_root.operator_type == LogicalOperatorType.JOIN
        assert len(plan.logical_root.children) == 2

    def test_parse_array_format_json(self) -> None:

        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("q06", JSON_ARRAY_FORMAT)

        assert plan is not None
        assert plan.logical_root.operator_type == LogicalOperatorType.PROJECT

    def test_json_fingerprint_computation(self) -> None:

        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("q07", JSON_SIMPLE_SCAN)

        assert plan is not None
        assert plan.plan_fingerprint is not None
        assert len(plan.plan_fingerprint) == 64

    def test_json_raw_output_preserved(self) -> None:

        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("q08", JSON_SIMPLE_SCAN)

        assert plan is not None
        assert plan.raw_explain_output == JSON_SIMPLE_SCAN


class TestDuckDBParserFallback:
    def test_fallback_on_invalid_json(self) -> None:

        parser = DuckDBQueryPlanParser()
        invalid_json_with_text = """{ invalid json here }
┌───────────────────────────┐
│         SEQ_SCAN          │
│   ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─   │
│          orders           │
└───────────────────────────┘
"""
        plan = parser.parse_explain_output("q09", invalid_json_with_text)
        assert plan is not None
        assert plan.logical_root.operator_type == LogicalOperatorType.SCAN

    def test_text_format_still_works(self) -> None:

        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("q10", TEXT_SIMPLE_SCAN)

        assert plan is not None
        assert plan.logical_root.operator_type == LogicalOperatorType.SCAN
        assert plan.logical_root.table_name == "orders"

    def test_empty_json_array_fallback(self) -> None:

        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("q11", "[]")
        assert plan is None

    def test_malformed_json_structure(self) -> None:

        parser = DuckDBQueryPlanParser()
        malformed = '{"no_name_field": true}'
        plan = parser.parse_explain_output("q12", malformed)
        assert plan is None


class TestDuckDBParserOperatorIDs:
    def test_json_operator_ids_unique(self) -> None:

        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("q13", JSON_WITH_CHILDREN)

        assert plan is not None
        operators = self._collect_operators(plan.logical_root)
        operator_ids = [op.operator_id for op in operators]

        assert len(operator_ids) == len(set(operator_ids))

    def test_json_operator_ids_reset_between_parses(self) -> None:

        parser = DuckDBQueryPlanParser()
        plan1 = parser.parse_explain_output("q14", JSON_SIMPLE_SCAN)
        plan2 = parser.parse_explain_output("q15", JSON_SIMPLE_SCAN)

        assert plan1 is not None
        assert plan2 is not None

        id1 = int(plan1.logical_root.operator_id.split("_")[-1])
        id2 = int(plan2.logical_root.operator_id.split("_")[-1])

        assert id1 < 10
        assert id2 < 10

    def test_mixed_format_operator_ids(self) -> None:

        parser = DuckDBQueryPlanParser()
        plan1 = parser.parse_explain_output("q16", JSON_SIMPLE_SCAN)
        plan2 = parser.parse_explain_output("q17", TEXT_SIMPLE_SCAN)

        assert plan1 is not None
        assert plan2 is not None

        assert plan1.logical_root.operator_id is not None
        assert plan2.logical_root.operator_id is not None

    def _collect_operators(self, root):
        operators = [root]
        for child in root.children:
            operators.extend(self._collect_operators(child))
        return operators


class TestDuckDBParserEdgeCasesJSON:
    def test_empty_children_array(self) -> None:

        parser = DuckDBQueryPlanParser()
        json_empty_children = '{"name": "SEQ_SCAN", "children": []}'
        plan = parser.parse_explain_output("q18", json_empty_children)

        assert plan is not None
        assert plan.logical_root.children == []

    def test_missing_children_field(self) -> None:

        parser = DuckDBQueryPlanParser()
        json_no_children = '{"name": "SEQ_SCAN"}'
        plan = parser.parse_explain_output("q19", json_no_children)

        assert plan is not None
        assert plan.logical_root.children == []

    def test_extra_info_multiline(self) -> None:

        parser = DuckDBQueryPlanParser()
        json_multiline = """{
            "name": "PROJECTION",
            "extra_info": "col1\\ncol2\\ncol3",
            "children": []
        }"""
        plan = parser.parse_explain_output("q20", json_multiline)

        assert plan is not None
        assert plan.logical_root.physical_operator.platform_metadata.get("extra_info") is not None

    def test_unicode_in_json(self) -> None:

        parser = DuckDBQueryPlanParser()
        json_unicode = """{
            "name": "SEQ_SCAN",
            "extra_info": "table_\u540d\u79f0",
            "children": []
        }"""
        plan = parser.parse_explain_output("q21", json_unicode)

        assert plan.query_id == "q21"

    def test_deeply_nested_json(self) -> None:

        parser = DuckDBQueryPlanParser()
        nested = {"name": "PROJECTION", "children": []}
        current = nested
        for i in range(20):
            child = {"name": "FILTER", "children": []}
            current["children"] = [child]
            current = child
        current["children"] = [{"name": "SEQ_SCAN", "children": []}]

        import json

        json_str = json.dumps(nested)
        plan = parser.parse_explain_output("q22", json_str)

        assert plan is not None
        node = plan.logical_root
        depth = 0
        while node.children:
            node = node.children[0]
            depth += 1
        assert depth > 10


