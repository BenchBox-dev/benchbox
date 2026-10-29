import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from benchbox.core.query_plans.parsers.clickhouse import ClickHouseQueryPlanParser
from benchbox.core.query_plans.parsers.databend import DatabendQueryPlanParser
from benchbox.core.query_plans.parsers.doris import DorisQueryPlanParser
from benchbox.core.query_plans.parsers.presto_trino import PrestoTrinoQueryPlanParser
from benchbox.core.query_plans.parsers.questdb import QuestDBQueryPlanParser
from benchbox.core.query_plans.parsers.singlestore import SingleStoreQueryPlanParser
from benchbox.core.query_plans.parsers.spark import SparkQueryPlanParser
from benchbox.core.results.query_plan_models import (
    FingerprintIntegrity,
    JoinType,
    LogicalOperator,
    LogicalOperatorType,
    QueryPlanDAG,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "query_plans"
_SHAPES = ("scan_filter", "join", "aggregate", "union")


@dataclass(frozen=True)
class Engine:
    name: str
    parser_factory: type
    extension: str
    union_node: bool = True


_ENGINES = (
    Engine("spark", SparkQueryPlanParser, "txt"),
    Engine("clickhouse", ClickHouseQueryPlanParser, "txt"),
    Engine("trino", PrestoTrinoQueryPlanParser, "json", union_node=False),
    Engine("databend", DatabendQueryPlanParser, "txt"),
    Engine("questdb", QuestDBQueryPlanParser, "txt"),
    Engine("doris", DorisQueryPlanParser, "txt"),
    Engine("singlestore", SingleStoreQueryPlanParser, "txt"),
)


def _fixture_path(engine: Engine, shape: str) -> Path:
    return _FIXTURES / f"{engine.name}_captured_{shape}.{engine.extension}"


def _parse(engine: Engine, shape: str) -> QueryPlanDAG:
    dag = engine.parser_factory().parse_explain_output(
        f"{engine.name}_{shape}", _fixture_path(engine, shape).read_text(encoding="utf-8")
    )
    assert dag is not None
    return dag


def _collect(op: LogicalOperator) -> list[LogicalOperator]:
    nodes = [op]
    for child in op.children:
        nodes.extend(_collect(child))
    return nodes


def _scans(op: LogicalOperator) -> list[LogicalOperator]:
    return [n for n in _collect(op) if n.operator_type == LogicalOperatorType.SCAN]


def _scanned_tables(op: LogicalOperator) -> list[str]:
    return sorted(n.table_name.split(".")[-1].removeprefix("lpv_") for n in _scans(op) if n.table_name)


def _has_filter(op: LogicalOperator) -> bool:
    return any(n.operator_type == LogicalOperatorType.FILTER or n.filter_expressions for n in _collect(op))


def _of_type(op: LogicalOperator, operator_type: LogicalOperatorType) -> list[LogicalOperator]:
    return [n for n in _collect(op) if n.operator_type == operator_type]


_ids = [e.name for e in _ENGINES]


@pytest.mark.parametrize("engine", _ENGINES, ids=_ids)
@pytest.mark.parametrize("shape", _SHAPES)
class TestCapturedFixtureMetadata:
    def test_sidecar_marks_captured_and_live_validated(self, engine, shape):
        sidecar = json.loads(Path(f"{_fixture_path(engine, shape)}.meta.json").read_text(encoding="utf-8"))
        assert sidecar["provenance"] == "captured"
        assert sidecar["validated_against_live"] is True
        assert sidecar["models"].strip()

    def test_fingerprint_survives_dict_round_trip(self, engine, shape):
        dag = _parse(engine, shape)
        assert dag.verify_fingerprint()
        restored = QueryPlanDAG.from_dict(json.loads(json.dumps(dag.to_dict(max_depth=None))))
        assert restored.fingerprint_integrity == FingerprintIntegrity.VERIFIED
        assert restored.plan_fingerprint == dag.plan_fingerprint
        assert restored.compute_plan_fingerprint() == dag.plan_fingerprint


@pytest.mark.parametrize("engine", _ENGINES, ids=_ids)
class TestCapturedSemanticShapes:
    def test_scan_filter_has_filter_over_single_orders_scan(self, engine):
        root = _parse(engine, "scan_filter").logical_root
        assert _scanned_tables(root) == ["orders"]
        assert _has_filter(root)
        assert not _of_type(root, LogicalOperatorType.JOIN)
        assert not _of_type(root, LogicalOperatorType.AGGREGATE)

    def test_join_has_two_inputs_over_both_tables(self, engine):
        root = _parse(engine, "join").logical_root
        joins = _of_type(root, LogicalOperatorType.JOIN)
        assert len(joins) == 1
        assert len(joins[0].children) == 2
        assert _scanned_tables(joins[0]) == ["customers", "orders"]
        assert not _of_type(root, LogicalOperatorType.AGGREGATE)

    def test_aggregate_sits_above_single_orders_scan(self, engine):
        root = _parse(engine, "aggregate").logical_root
        aggregates = _of_type(root, LogicalOperatorType.AGGREGATE)
        assert aggregates
        assert _scanned_tables(aggregates[0]) == ["orders"]
        assert not _of_type(root, LogicalOperatorType.JOIN)

    def test_union_combines_both_tables(self, engine):
        root = _parse(engine, "union").logical_root
        assert _scanned_tables(root) == ["customers", "orders"]
        assert _has_filter(root)
        if engine.union_node:
            unions = _of_type(root, LogicalOperatorType.UNION)
            assert len(unions) == 1
            assert len(unions[0].children) == 2
            assert all(len(_scans(branch)) == 1 for branch in unions[0].children)
        else:
            assert not _of_type(root, LogicalOperatorType.UNION)
            fan_in = [n for n in _collect(root) if len([c for c in n.children if _scans(c)]) == 2]
            assert fan_in


class TestSparkCapturedPlans:
    def test_adaptive_wrapper_is_root_over_operator_tree(self):
        root = _parse(_ENGINES[0], "join").logical_root
        assert root.physical_operator.operator_type == "AdaptiveSparkPlan"
        assert root.children[0].operator_type == LogicalOperatorType.PROJECT

    def test_join_is_broadcast_hash_join_with_exchange_build_side(self):
        join = _of_type(_parse(_ENGINES[0], "join").logical_root, LogicalOperatorType.JOIN)[0]
        assert join.physical_operator.operator_type == "BroadcastHashJoin"
        assert [c.physical_operator.operator_type for c in join.children] == ["Filter", "BroadcastExchange"]

    def test_aggregate_is_partial_final_pair_around_exchange(self):
        root = _parse(_ENGINES[0], "aggregate").logical_root
        aggregates = _of_type(root, LogicalOperatorType.AGGREGATE)
        assert len(aggregates) == 2
        assert aggregates[0].children[0].physical_operator.operator_type == "Exchange"

    def test_scan_table_names_are_catalog_qualified(self):
        scans = _scans(_parse(_ENGINES[0], "scan_filter").logical_root)
        assert [s.table_name for s in scans] == ["spark_catalog.default.orders"]


class TestClickHouseCapturedPlans:
    def test_where_expression_step_is_classified_as_filter(self):
        root = _parse(_ENGINES[1], "scan_filter").logical_root
        filters = _of_type(root, LogicalOperatorType.FILTER)
        assert len(filters) == 1
        assert filters[0].children[0].operator_type == LogicalOperatorType.SCAN
        assert filters[0].children[0].table_name == "default.orders"

    def test_join_steps_are_wrapped_by_expression_projections(self):
        join = _of_type(_parse(_ENGINES[1], "join").logical_root, LogicalOperatorType.JOIN)[0]
        assert all(c.operator_type == LogicalOperatorType.PROJECT for c in join.children)

    def test_union_branches_carry_where_filters(self):
        unions = _of_type(_parse(_ENGINES[1], "union").logical_root, LogicalOperatorType.UNION)
        assert all(_of_type(branch, LogicalOperatorType.FILTER) for branch in unions[0].children)


class TestTrinoCapturedPlans:
    def test_scan_filter_predicate_is_retained_on_scan(self):
        scan = _scans(_parse(_ENGINES[2], "scan_filter").logical_root)[0]
        assert scan.physical_operator.operator_type == "ScanFilter"
        assert scan.table_name == "memory:default.orders"
        assert scan.filter_expressions and "o_total" in scan.filter_expressions[0]

    def test_scan_without_predicate_has_no_filter_expressions(self):
        scans = _scans(_parse(_ENGINES[2], "aggregate").logical_root)
        assert all(not s.filter_expressions for s in scans)

    def test_join_condition_and_type(self):
        join = _of_type(_parse(_ENGINES[2], "join").logical_root, LogicalOperatorType.JOIN)[0]
        assert join.join_type == JoinType.INNER
        assert join.join_conditions == ["(c_id = o_cust)"]

    def test_remote_source_fragment_is_expanded_into_child_scan(self):
        join = _of_type(_parse(_ENGINES[2], "join").logical_root, LogicalOperatorType.JOIN)[0]
        remote = [n for n in _collect(join) if n.physical_operator.operator_type == "RemoteSource"]
        assert len(remote) == 1
        assert _scanned_tables(remote[0]) == ["orders"]

    def test_group_by_keys_extracted(self):
        aggregates = _of_type(_parse(_ENGINES[2], "aggregate").logical_root, LogicalOperatorType.AGGREGATE)
        assert {tuple(a.group_by_keys) for a in aggregates} == {("o_cust",)}


class TestDatabendCapturedPlans:
    def test_pushed_down_scan_filter_is_extracted(self):
        scan = _scans(_parse(_ENGINES[3], "scan_filter").logical_root)[0]
        assert scan.table_name == "default.default.orders"
        assert scan.filter_expressions == ["is_true(orders.o_total (#2) > 100)"]

    def test_scan_without_pushed_filter_has_none(self):
        scans = _scans(_parse(_ENGINES[3], "aggregate").logical_root)
        assert all(not s.filter_expressions for s in scans)

    def test_hash_join_build_probe_keys_become_condition(self):
        join = _of_type(_parse(_ENGINES[3], "join").logical_root, LogicalOperatorType.JOIN)[0]
        assert join.join_type == JoinType.INNER
        assert join.join_conditions == ["c.c_id (#3) = o.o_cust (#1)"]
        assert [c.physical_operator.operator_type for c in join.children] == ["TableScan(Build)", "TableScan(Probe)"]

    def test_aggregate_is_final_over_partial(self):
        root = _parse(_ENGINES[3], "aggregate").logical_root
        assert root.physical_operator.operator_type == "AggregateFinal"
        assert root.children[0].physical_operator.operator_type == "AggregatePartial"


class TestQuestDBCapturedPlans:
    @pytest.mark.parametrize("shape", _SHAPES)
    def test_page_frame_stack_collapses_to_one_scan_per_table(self, shape):
        root = _parse(_ENGINES[4], shape).logical_root
        scans = _scans(root)
        assert all(s.table_name and not s.children for s in scans)
        assert len(scans) == len(set(_scanned_tables(root)))

    def test_join_build_side_hash_wrapper_is_kept(self):
        join = _of_type(_parse(_ENGINES[4], "join").logical_root, LogicalOperatorType.JOIN)[0]
        assert join.children[0].operator_type == LogicalOperatorType.SCAN
        assert join.children[1].operator_type == LogicalOperatorType.OTHER
        assert _scanned_tables(join.children[1]) == ["customers"]
        assert join.children[0].table_name == "lpv_orders"

    def test_aggregate_is_async_group_by(self):
        root = _parse(_ENGINES[4], "aggregate").logical_root
        assert root.operator_type == LogicalOperatorType.AGGREGATE
        assert root.children[0].operator_type == LogicalOperatorType.SCAN


class TestDorisCapturedPlans:
    def test_hash_agg_is_global_over_distributed_local(self):
        root = _parse(_ENGINES[5], "aggregate").logical_root
        aggregates = _of_type(root, LogicalOperatorType.AGGREGATE)
        assert [a.physical_operator.operator_type for a in aggregates] == ["hashAgg", "hashAgg"]
        assert aggregates[0].children[0].physical_operator.operator_type == "PhysicalDistribute"

    def test_hash_join_condition_and_type(self):
        join = _of_type(_parse(_ENGINES[5], "join").logical_root, LogicalOperatorType.JOIN)[0]
        assert join.join_type == JoinType.INNER
        assert join.join_conditions == ["(o.o_cust = c.c_id)"]

    def test_filter_sits_between_project_and_scan(self):
        root = _parse(_ENGINES[5], "scan_filter").logical_root
        filters = _of_type(root, LogicalOperatorType.FILTER)
        assert len(filters) == 1
        assert filters[0].children[0].operator_type == LogicalOperatorType.SCAN

    def test_result_sink_is_root(self):
        root = _parse(_ENGINES[5], "scan_filter").logical_root
        assert root.physical_operator.operator_type == "PhysicalResultSink"


class TestSingleStoreCapturedPlans:
    @pytest.mark.parametrize("shape", _SHAPES)
    def test_statistics_notices_are_not_operators(self, shape):
        names = {n.physical_operator.operator_type for n in _collect(_parse(_ENGINES[6], shape).logical_root)}
        assert not names & {"WARNING", "See"}

    def test_join_probe_block_and_gather_are_the_two_inputs(self):
        join = _of_type(_parse(_ENGINES[6], "join").logical_root, LogicalOperatorType.JOIN)[0]
        assert [c.physical_operator.operator_type for c in join.children] == ["HashTableProbe", "Gather"]
        assert _scanned_tables(join.children[0]) == ["customers"]
        assert _scanned_tables(join.children[1]) == ["orders"]

    def test_union_all_branches_each_scan_one_table(self):
        union = _of_type(_parse(_ENGINES[6], "union").logical_root, LogicalOperatorType.UNION)[0]
        assert [_scanned_tables(branch) for branch in union.children] == [["customers"], ["orders"]]

    def test_aggregate_is_partial_below_gather_and_final_above(self):
        root = _parse(_ENGINES[6], "aggregate").logical_root
        aggregates = _of_type(root, LogicalOperatorType.AGGREGATE)
        assert len(aggregates) == 2
        assert aggregates[0].children[0].physical_operator.operator_type == "Gather"
        assert _of_type(aggregates[1], LogicalOperatorType.AGGREGATE) == [aggregates[1]]
