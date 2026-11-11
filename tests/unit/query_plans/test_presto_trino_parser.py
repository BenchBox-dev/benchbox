from pathlib import Path

import pytest

from benchbox.core.query_plans.parsers.presto_trino import PrestoTrinoQueryPlanParser
from benchbox.core.query_plans.parsers.registry import get_parser_for_platform
from benchbox.core.results.query_plan_models import LogicalOperator, LogicalOperatorType

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "query_plans"


def _load(name: str) -> str:
    return (_FIXTURES / name).read_text()


def _collect_types(op: LogicalOperator) -> list[LogicalOperatorType]:
    types = [op.operator_type]
    for child in op.children:
        types.extend(_collect_types(child))
    return types


def _collect(op: LogicalOperator) -> list[LogicalOperator]:
    nodes = [op]
    for child in op.children:
        nodes.extend(_collect(child))
    return nodes


@pytest.fixture()
def parser():
    return PrestoTrinoQueryPlanParser()


class TestPrestoTrinoParserBasics:
    @pytest.mark.parametrize(
        "fixture", ["trino_explain_sample.json", "presto_explain_sample.json", "athena_explain_sample.json"]
    )
    def test_parses_fixture_to_valid_dag(self, parser, fixture):
        dag = parser.parse_explain_output("q1", _load(fixture))
        assert dag is not None, f"{fixture} should parse to a DAG"
        assert dag.logical_root is not None
        assert dag.plan_fingerprint is not None
        assert dag.platform == "presto_trino"

    def test_trino_tree_has_join_over_two_scans(self, parser):
        dag = parser.parse_explain_output("q_trino", _load("trino_explain_sample.json"))
        nodes = _collect(dag.logical_root)
        types = [n.operator_type for n in nodes]
        assert LogicalOperatorType.JOIN in types
        assert types.count(LogicalOperatorType.SCAN) == 2
        assert LogicalOperatorType.AGGREGATE in types
        assert LogicalOperatorType.FILTER in types
        tables = {n.table_name for n in nodes if n.table_name}
        assert tables == {"tpch:nation:sf1.0", "tpch:region:sf1.0"}

    def test_presto_tree_has_join_over_two_scans(self, parser):
        dag = parser.parse_explain_output("q_presto", _load("presto_explain_sample.json"))
        nodes = _collect(dag.logical_root)
        types = [n.operator_type for n in nodes]
        assert LogicalOperatorType.JOIN in types
        assert types.count(LogicalOperatorType.SCAN) == 2
        tables = {n.table_name for n in nodes if n.table_name}
        assert any("nation" in t for t in tables) and any("region" in t for t in tables)
        join = next(n for n in nodes if n.operator_type == LogicalOperatorType.JOIN)
        assert join.join_conditions and "regionkey" in join.join_conditions[0]
        assert "PARTITIONED" not in (join.join_conditions[0] if join.join_conditions else "")

    def test_distributed_fragments_are_stitched(self, parser):
        dag = parser.parse_explain_output("q_dist", _load("trino_distributed_explain_sample.json"))
        nodes = _collect(dag.logical_root)
        types = [n.operator_type for n in nodes]
        assert dag.logical_root.operator_type == LogicalOperatorType.PROJECT
        assert LogicalOperatorType.JOIN in types
        assert types.count(LogicalOperatorType.SCAN) == 2, "both fragments' scans must be present after stitching"
        tables = {n.table_name for n in nodes if n.table_name}
        assert any("nation" in t for t in tables) and any("region" in t for t in tables)

    def test_distributed_root_is_fragment_zero_regardless_of_order(self, parser):
        import json

        frag = json.loads(_load("trino_distributed_explain_sample.json"))
        reordered = json.dumps({"2": frag["2"], "1": frag["1"], "0": frag["0"]})
        dag = parser.parse_explain_output("q_reordered", reordered)
        assert dag.logical_root.operator_type == LogicalOperatorType.PROJECT

    def test_cross_dialect_shares_core_operator_shape(self, parser):
        trino = set(_collect_types(parser.parse_explain_output("t", _load("trino_explain_sample.json")).logical_root))
        presto = set(_collect_types(parser.parse_explain_output("p", _load("presto_explain_sample.json")).logical_root))
        core = {
            LogicalOperatorType.SCAN,
            LogicalOperatorType.JOIN,
            LogicalOperatorType.AGGREGATE,
            LogicalOperatorType.PROJECT,
        }
        assert core <= trino
        assert core <= presto

    def test_athena_scanfilterproject_maps_to_scan(self, parser):
        dag = parser.parse_explain_output("q_athena", _load("athena_explain_sample.json"))
        types = _collect_types(dag.logical_root)
        assert LogicalOperatorType.SCAN in types


class TestPrestoTrinoOperatorNormalization:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("TableScan", LogicalOperatorType.SCAN),
            ("ScanFilterProject", LogicalOperatorType.SCAN),
            ("ScanProject", LogicalOperatorType.SCAN),
            ("InnerJoin", LogicalOperatorType.JOIN),
            ("LeftJoin", LogicalOperatorType.JOIN),
            ("LookupJoin", LogicalOperatorType.JOIN),
            ("SemiJoin", LogicalOperatorType.JOIN),
            ("Aggregate(FINAL)", LogicalOperatorType.AGGREGATE),
            ("Aggregation", LogicalOperatorType.AGGREGATE),
            ("Filter", LogicalOperatorType.FILTER),
            ("TopN", LogicalOperatorType.SORT),
            ("Sort", LogicalOperatorType.SORT),
            ("Limit", LogicalOperatorType.LIMIT),
            ("DistinctLimit", LogicalOperatorType.LIMIT),
            ("Output", LogicalOperatorType.PROJECT),
            ("Project", LogicalOperatorType.PROJECT),
            ("Window", LogicalOperatorType.WINDOW),
            ("Union", LogicalOperatorType.UNION),
            ("RemoteExchange", LogicalOperatorType.OTHER),
            ("RemoteSource", LogicalOperatorType.OTHER),
            ("LocalExchange", LogicalOperatorType.OTHER),
        ],
    )
    def test_map_operator_type(self, name, expected):
        assert PrestoTrinoQueryPlanParser._map_operator_type(name) == expected


class TestPrestoTrinoErrorRecovery:
    def test_malformed_json_returns_none(self, parser):
        assert parser.parse_explain_output("q", "{not valid json") is None

    def test_empty_input_returns_none(self, parser):
        assert parser.parse_explain_output("q", "") is None

    def test_json_without_plan_node_returns_none(self, parser):
        assert parser.parse_explain_output("q", '{"unexpected": 1}') is None


class TestPrestoTrinoRegistration:
    @pytest.mark.parametrize("dialect", ["presto", "trino", "starburst", "athena"])
    def test_registered_for_dialect(self, dialect):
        parser = get_parser_for_platform(dialect)
        assert isinstance(parser, PrestoTrinoQueryPlanParser)


class TestPrestoTrinoConcretePlatform:
    def test_default_platform_is_family_name(self):
        assert PrestoTrinoQueryPlanParser().platform_name == "presto_trino"

    @pytest.mark.parametrize("platform", ["presto", "trino", "starburst", "athena"])
    def test_concrete_platform_threaded_into_dag(self, platform):
        parser = PrestoTrinoQueryPlanParser(platform_name=platform)
        assert parser.platform_name == platform
        dag = parser.parse_explain_output("q1", _load("trino_explain_sample.json"))
        assert dag is not None
        assert dag.platform == platform


