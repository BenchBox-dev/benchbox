import json
from pathlib import Path

import pytest

from benchbox.core.query_plans.parsers.bigquery import BigQueryQueryPlanParser
from benchbox.core.query_plans.parsers.registry import get_parser_for_platform
from benchbox.core.results.query_plan_models import JoinType, LogicalOperator, LogicalOperatorType

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "query_plans"


def _load(name: str) -> str:
    return (_FIXTURES / name).read_text()


def _collect(op: LogicalOperator) -> list[LogicalOperator]:
    nodes = [op]
    for child in op.children:
        nodes.extend(_collect(child))
    return nodes


@pytest.fixture()
def parser():
    return BigQueryQueryPlanParser()


class TestBigQueryParserBasics:
    def test_parses_fixture_to_valid_dag(self, parser):
        dag = parser.parse_explain_output("q1", _load("bigquery_query_plan_sample.json"))
        assert dag is not None
        assert dag.logical_root is not None
        assert dag.plan_fingerprint is not None
        assert dag.platform == "bigquery"

    def test_output_stage_is_root_over_two_inputs(self, parser):
        dag = parser.parse_explain_output("q1", _load("bigquery_query_plan_sample.json"))
        assert dag.logical_root.operator_type == LogicalOperatorType.PROJECT
        nodes = _collect(dag.logical_root)
        types = [n.operator_type for n in nodes]
        assert types.count(LogicalOperatorType.SCAN) == 2
        assert LogicalOperatorType.JOIN in types
        assert LogicalOperatorType.SORT in types

    def test_scan_tables_extracted_from_substeps(self, parser):
        dag = parser.parse_explain_output("q1", _load("bigquery_query_plan_sample.json"))
        tables = {n.table_name for n in _collect(dag.logical_root) if n.table_name}
        assert tables == {"tpch.orders", "tpch.lineitem"}

    def test_records_read_recorded_in_properties(self, parser):
        dag = parser.parse_explain_output("q1", _load("bigquery_query_plan_sample.json"))
        scans = [n for n in _collect(dag.logical_root) if n.operator_type == LogicalOperatorType.SCAN]
        assert any(n.physical_operator.properties.get("records_read") for n in scans)


class TestBigQueryParserMapping:
    def test_falls_back_to_step_kind_when_name_generic(self, parser):
        stages = [
            {"name": "S00: Stage", "id": "0", "inputStages": [], "steps": [{"kind": "READ", "substeps": ["FROM t"]}]},
            {"name": "S01: Stage", "id": "1", "inputStages": ["0"], "steps": [{"kind": "JOIN", "substeps": ["x=y"]}]},
        ]
        dag = parser.parse_explain_output("q", json.dumps(stages))
        types = [n.operator_type for n in _collect(dag.logical_root)]
        assert LogicalOperatorType.JOIN in types
        assert LogicalOperatorType.SCAN in types

    def test_left_join_detected_from_substeps(self, parser):
        stages = [
            {"name": "S00: Input", "id": "0", "inputStages": [], "steps": [{"kind": "READ", "substeps": ["FROM a"]}]},
            {
                "name": "S01: Join+",
                "id": "1",
                "inputStages": ["0"],
                "steps": [{"kind": "JOIN", "substeps": ["LEFT OUTER JOIN ON a=b"]}],
            },
        ]
        dag = parser.parse_explain_output("q", json.dumps(stages))
        joins = [n for n in _collect(dag.logical_root) if n.operator_type == LogicalOperatorType.JOIN]
        assert joins and joins[0].join_type == JoinType.LEFT


class TestBigQueryParserMultipleRoots:
    def test_dangling_stage_does_not_become_root(self, parser):
        stages = [
            {"name": "S00: Input", "id": "0", "inputStages": [], "steps": [{"kind": "READ", "substeps": ["FROM a"]}]},
            {"name": "S01: Input", "id": "1", "inputStages": [], "steps": [{"kind": "READ", "substeps": ["FROM b"]}]},
            {
                "name": "S02: Join+",
                "id": "2",
                "inputStages": ["0", "1"],
                "steps": [{"kind": "JOIN", "substeps": ["x=y"]}],
            },
            {
                "name": "S03: Output",
                "id": "3",
                "inputStages": ["2"],
                "steps": [{"kind": "WRITE", "substeps": ["TO o"]}],
            },
            {"name": "S99: Noop", "id": "99", "inputStages": [], "steps": []},
        ]
        dag = parser.parse_explain_output("q", json.dumps(stages))
        types = [n.operator_type for n in _collect(dag.logical_root)]
        assert types.count(LogicalOperatorType.SCAN) == 2
        assert LogicalOperatorType.JOIN in types
        assert dag.logical_root.operator_type == LogicalOperatorType.PROJECT


class TestBigQueryParserErrorHandling:
    def test_empty_returns_none(self, parser):
        assert parser.parse_explain_output("q", "") is None

    def test_invalid_json_returns_none(self, parser):
        assert parser.parse_explain_output("q", "{not json") is None

    def test_empty_stage_list_returns_none(self, parser):
        assert parser.parse_explain_output("q", "[]") is None


class TestBigQueryRegistry:
    def test_registry_returns_bigquery_parser(self):
        assert isinstance(get_parser_for_platform("bigquery"), BigQueryQueryPlanParser)


