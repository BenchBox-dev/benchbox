import json

import pytest

from benchbox.core.results.query_plan_models import (
    FINGERPRINT_VERSION,
    LEGACY_FINGERPRINT_VERSION,
    AggregateFunction,
    FingerprintIntegrity,
    JoinType,
    LogicalOperator,
    LogicalOperatorType,
    PhysicalOperator,
    QueryPlanDAG,
    _normalize_literal_text,
    compute_plan_fingerprint,
    find_truncation_depth,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _scan(table: str, operator_id: str = "scan") -> LogicalOperator:
    return LogicalOperator(operator_type=LogicalOperatorType.SCAN, operator_id=operator_id, table_name=table)


class TestFingerprintV2Encoding:
    def test_sibling_children_vs_nested_chain_are_distinguished(self) -> None:
        siblings = LogicalOperator(
            operator_type=LogicalOperatorType.JOIN,
            operator_id="j",
            children=[_scan("t1", "s1"), _scan("t2", "s2")],
        )
        nested_inner = _scan("t1", "s1")
        nested_inner.children = [_scan("t2", "s2")]
        nested = LogicalOperator(operator_type=LogicalOperatorType.JOIN, operator_id="j", children=[nested_inner])

        assert siblings.get_structural_signature() != nested.get_structural_signature()
        assert compute_plan_fingerprint(siblings) != compute_plan_fingerprint(nested)

    def test_filter_list_separator_injection_is_distinguished(self) -> None:
        two = LogicalOperator(operator_type=LogicalOperatorType.FILTER, operator_id="f", filter_expressions=["a", "b"])
        one = LogicalOperator(operator_type=LogicalOperatorType.FILTER, operator_id="f", filter_expressions=["a,b"])

        assert two.get_structural_signature() != one.get_structural_signature()

    def test_table_name_field_injection_is_distinguished(self) -> None:
        crafted = LogicalOperator(operator_type=LogicalOperatorType.SCAN, operator_id="s", table_name="x|filters:y")
        genuine = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN, operator_id="s", table_name="x", filter_expressions=["y"]
        )

        assert crafted.get_structural_signature() != genuine.get_structural_signature()

    def test_signature_is_canonical_json(self) -> None:
        op = LogicalOperator(
            operator_type=LogicalOperatorType.JOIN,
            operator_id="j",
            join_type=JoinType.INNER,
            children=[_scan("t1"), _scan("t2")],
        )
        parsed = json.loads(op.get_structural_signature())
        assert parsed["op"] == "Join"
        assert parsed["join"] == "inner"
        assert [c["table"] for c in parsed["children"]] == ["t1", "t2"]

    def test_set_like_fields_stay_order_independent(self) -> None:
        a = LogicalOperator(
            operator_type=LogicalOperatorType.FILTER, operator_id="f", filter_expressions=["a", "b", "c"]
        )
        b = LogicalOperator(
            operator_type=LogicalOperatorType.FILTER, operator_id="f", filter_expressions=["c", "a", "b"]
        )
        assert a.get_structural_signature() == b.get_structural_signature()

    def test_ordered_fields_are_order_sensitive(self) -> None:
        a = LogicalOperator(
            operator_type=LogicalOperatorType.PROJECT, operator_id="p", projection_expressions=["x", "y"]
        )
        b = LogicalOperator(
            operator_type=LogicalOperatorType.PROJECT, operator_id="p", projection_expressions=["y", "x"]
        )
        assert a.get_structural_signature() != b.get_structural_signature()


class TestFingerprintVersioningAndIntegrity:
    def test_fresh_plan_is_current_version_and_verified(self) -> None:
        plan = QueryPlanDAG(query_id="q", platform="duckdb", logical_root=_scan("t"))
        assert plan.fingerprint_version == FINGERPRINT_VERSION
        assert plan.fingerprint_integrity == FingerprintIntegrity.VERIFIED
        assert plan.is_fingerprint_trusted()

    def test_to_dict_carries_fingerprint_version(self) -> None:
        plan = QueryPlanDAG(query_id="q", platform="duckdb", logical_root=_scan("t"))
        assert plan.to_dict()["fingerprint_version"] == FINGERPRINT_VERSION

    def test_from_dict_roundtrips_version_and_verifies(self) -> None:
        plan = QueryPlanDAG(query_id="q", platform="duckdb", logical_root=_scan("t"))
        restored = QueryPlanDAG.from_dict(plan.to_dict())
        assert restored.fingerprint_version == FINGERPRINT_VERSION
        assert restored.fingerprint_integrity == FingerprintIntegrity.VERIFIED

    def test_from_dict_absent_fingerprint_is_recomputed_not_verified(self) -> None:
        data = QueryPlanDAG(query_id="q", platform="duckdb", logical_root=_scan("t")).to_dict()
        data.pop("plan_fingerprint")

        restored = QueryPlanDAG.from_dict(data)

        assert restored.fingerprint_integrity == FingerprintIntegrity.RECOMPUTED
        assert restored.fingerprint_integrity != FingerprintIntegrity.VERIFIED

    def test_from_dict_tampered_fingerprint_is_stale(self) -> None:
        data = QueryPlanDAG(query_id="q", platform="duckdb", logical_root=_scan("t")).to_dict()
        data["plan_fingerprint"] = "deadbeef" * 8

        restored = QueryPlanDAG.from_dict(data)

        assert restored.fingerprint_integrity == FingerprintIntegrity.STALE
        assert not restored.is_fingerprint_trusted()

    def test_legacy_bundle_without_version_loads_as_v1_and_is_stale(self) -> None:
        legacy = {
            "query_id": "q",
            "platform": "duckdb",
            "logical_root": _scan("t").to_dict(),
            "plan_fingerprint": "Scan|table:t",
        }

        restored = QueryPlanDAG.from_dict(legacy)

        assert restored.fingerprint_version == LEGACY_FINGERPRINT_VERSION
        assert restored.fingerprint_integrity == FingerprintIntegrity.STALE
        assert not restored.is_fingerprint_trusted()


def _chain(leaf_table: str, depth: int = 3) -> QueryPlanDAG:
    root = None
    for level in reversed(range(depth)):
        leaf = root is None
        root = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id=f"op_{level}",
            table_name=leaf_table if leaf else "lineitem",
            children=[root] if root is not None else [],
        )
    assert root is not None
    return QueryPlanDAG(query_id="q", platform="duckdb", logical_root=root)


class TestTruncationPreservation:
    def test_fresh_plan_has_no_truncation(self) -> None:
        assert _chain("lineitem").truncated_at_depth is None

    def test_from_dict_preserves_shallowest_cut(self) -> None:
        data = _chain("lineitem").to_dict(max_depth=1)

        restored = QueryPlanDAG.from_dict(data)

        assert restored.truncated_at_depth == 2
        assert restored.fingerprint_integrity == FingerprintIntegrity.TRUNCATED

    def test_from_dict_truncated_plan_is_not_stale(self) -> None:
        data = _chain("lineitem").to_dict(max_depth=1)

        restored = QueryPlanDAG.from_dict(data, verify_fingerprint=True)

        assert restored.truncated_at_depth is not None
        assert restored.fingerprint_integrity != FingerprintIntegrity.STALE
        assert restored.fingerprint_integrity == FingerprintIntegrity.TRUNCATED
        assert not restored.is_fingerprint_trusted()

    def test_from_dict_truncated_plan_survives_refresh_request(self) -> None:
        data = _chain("lineitem").to_dict(max_depth=1)
        stored = data["plan_fingerprint"]

        restored = QueryPlanDAG.from_dict(data, refresh_on_mismatch=True)

        assert restored.fingerprint_integrity == FingerprintIntegrity.TRUNCATED
        assert restored.plan_fingerprint == stored
        assert not restored.is_fingerprint_trusted()

    def test_verify_fingerprint_marks_truncated_not_stale(self) -> None:
        data = _chain("lineitem").to_dict(max_depth=1)

        restored = QueryPlanDAG.from_dict(data)
        assert restored.verify_fingerprint() is False

        assert restored.fingerprint_integrity == FingerprintIntegrity.TRUNCATED
        assert not restored.is_fingerprint_trusted()

    def test_from_dict_truncated_plan_without_fingerprint_is_not_trusted(self) -> None:
        data = _chain("lineitem").to_dict(max_depth=1)
        data.pop("plan_fingerprint")

        restored = QueryPlanDAG.from_dict(data)

        assert restored.truncated_at_depth is not None
        assert restored.fingerprint_integrity == FingerprintIntegrity.TRUNCATED
        assert not restored.is_fingerprint_trusted()

    def test_from_dict_full_depth_has_no_truncation(self) -> None:
        plan = _chain("lineitem")

        restored = QueryPlanDAG.from_dict(plan.to_dict())

        assert restored.truncated_at_depth is None
        assert restored.fingerprint_integrity == FingerprintIntegrity.VERIFIED

    def test_find_truncation_depth_ignores_non_markers(self) -> None:
        assert find_truncation_depth(None) is None
        assert find_truncation_depth({"a": [1, {"b": "x"}]}) is None
        assert find_truncation_depth({"truncated_at_depth": True}) is None


class TestPhysicalOperator:
    def test_basic_instantiation(self) -> None:

        op = PhysicalOperator(
            operator_type="SeqScan",
            operator_id="scan_1",
            properties={"cost": 100.0, "rows": 1000},
            platform_metadata={"table_oid": 12345},
        )

        assert op.operator_type == "SeqScan"
        assert op.operator_id == "scan_1"
        assert op.properties["cost"] == 100.0
        assert op.platform_metadata["table_oid"] == 12345

    def test_default_properties(self) -> None:

        op = PhysicalOperator(operator_type="HashJoin", operator_id="join_1")

        assert op.properties == {}
        assert op.platform_metadata == {}

    def test_to_dict(self) -> None:

        op = PhysicalOperator(
            operator_type="IndexScan",
            operator_id="idx_1",
            properties={"cost": 50.0},
            platform_metadata={"index_name": "idx_customer_pk"},
        )

        result = op.to_dict()

        assert result == {
            "operator_type": "IndexScan",
            "operator_id": "idx_1",
            "properties": {"cost": 50.0},
            "platform_metadata": {"index_name": "idx_customer_pk"},
        }

    def test_from_dict(self) -> None:

        data = {
            "operator_type": "HashAggregate",
            "operator_id": "agg_1",
            "properties": {"estimated_rows": 100},
            "platform_metadata": {"parallel_workers": 4},
        }

        op = PhysicalOperator.from_dict(data)

        assert op.operator_type == "HashAggregate"
        assert op.operator_id == "agg_1"
        assert op.properties == {"estimated_rows": 100}
        assert op.platform_metadata == {"parallel_workers": 4}

    def test_round_trip_serialization(self) -> None:

        original = PhysicalOperator(
            operator_type="MergeJoin",
            operator_id="join_2",
            properties={"cost": 250.0, "rows": 5000, "memory_mb": 128},
            platform_metadata={"join_algorithm": "sort-merge"},
        )

        serialized = original.to_dict()
        deserialized = PhysicalOperator.from_dict(serialized)

        assert deserialized.operator_type == original.operator_type
        assert deserialized.operator_id == original.operator_id
        assert deserialized.properties == original.properties
        assert deserialized.platform_metadata == original.platform_metadata


class TestLogicalOperator:
    def test_basic_scan_operator(self) -> None:

        op = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_1",
            table_name="lineitem",
        )

        assert op.operator_type == LogicalOperatorType.SCAN
        assert op.operator_id == "scan_1"
        assert op.table_name == "lineitem"
        assert op.children == []

    def test_join_operator(self) -> None:

        left_scan = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_1",
            table_name="orders",
        )
        right_scan = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_2",
            table_name="customer",
        )
        join = LogicalOperator(
            operator_type=LogicalOperatorType.JOIN,
            operator_id="join_1",
            join_type=JoinType.INNER,
            join_conditions=["o_custkey = c_custkey"],
            children=[left_scan, right_scan],
        )

        assert join.operator_type == LogicalOperatorType.JOIN
        assert join.join_type == JoinType.INNER
        assert len(join.children) == 2
        assert join.children[0].table_name == "orders"
        assert join.children[1].table_name == "customer"

    def test_aggregate_operator(self) -> None:

        scan = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_1",
            table_name="lineitem",
        )
        agg = LogicalOperator(
            operator_type=LogicalOperatorType.AGGREGATE,
            operator_id="agg_1",
            aggregation_functions=["sum(l_extendedprice)", "count(*)"],
            group_by_keys=["l_orderkey"],
            children=[scan],
        )

        assert agg.operator_type == LogicalOperatorType.AGGREGATE
        assert agg.aggregation_functions == ["sum(l_extendedprice)", "count(*)"]
        assert agg.group_by_keys == ["l_orderkey"]
        assert len(agg.children) == 1

    def test_sort_operator(self) -> None:

        scan = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_1",
            table_name="orders",
        )
        sort = LogicalOperator(
            operator_type=LogicalOperatorType.SORT,
            operator_id="sort_1",
            sort_keys=[
                {"expr": "o_orderdate", "direction": "DESC"},
                {"expr": "o_totalprice", "direction": "ASC"},
            ],
            children=[scan],
        )

        assert sort.operator_type == LogicalOperatorType.SORT
        assert len(sort.sort_keys) == 2
        assert sort.sort_keys[0]["expr"] == "o_orderdate"
        assert sort.sort_keys[0]["direction"] == "DESC"

    def test_filter_operator(self) -> None:

        scan = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_1",
            table_name="lineitem",
        )
        filter_op = LogicalOperator(
            operator_type=LogicalOperatorType.FILTER,
            operator_id="filter_1",
            filter_expressions=["l_shipdate >= '1994-01-01'", "l_quantity > 10"],
            children=[scan],
        )

        assert filter_op.operator_type == LogicalOperatorType.FILTER
        assert len(filter_op.filter_expressions) == 2
        assert "l_shipdate" in filter_op.filter_expressions[0]

    def test_limit_offset_operator(self) -> None:

        scan = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_1",
            table_name="orders",
        )
        limit = LogicalOperator(
            operator_type=LogicalOperatorType.LIMIT,
            operator_id="limit_1",
            limit_count=100,
            offset_count=50,
            children=[scan],
        )

        assert limit.operator_type == LogicalOperatorType.LIMIT
        assert limit.limit_count == 100
        assert limit.offset_count == 50

    def test_operator_with_physical_layer(self) -> None:

        physical = PhysicalOperator(
            operator_type="HashAggregate",
            operator_id="phys_agg_1",
            properties={"cost": 500.0, "memory_mb": 256},
        )
        logical = LogicalOperator(
            operator_type=LogicalOperatorType.AGGREGATE,
            operator_id="log_agg_1",
            physical_operator=physical,
        )

        assert logical.physical_operator is not None
        assert logical.physical_operator.operator_type == "HashAggregate"
        assert logical.physical_operator.properties["cost"] == 500.0

    def test_to_dict_simple(self) -> None:

        op = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_1",
            table_name="orders",
        )

        result = op.to_dict()

        assert result["operator_type"] == "Scan"
        assert result["operator_id"] == "scan_1"
        assert result["table_name"] == "orders"
        assert result["children"] == []

    def test_to_dict_with_tree(self) -> None:

        scan = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_1",
            table_name="lineitem",
        )
        filter_op = LogicalOperator(
            operator_type=LogicalOperatorType.FILTER,
            operator_id="filter_1",
            filter_expressions=["l_shipdate >= '1994-01-01'"],
            children=[scan],
        )

        result = filter_op.to_dict()

        assert result["operator_type"] == "Filter"
        assert result["filter_expressions"] == ["l_shipdate >= '1994-01-01'"]
        assert len(result["children"]) == 1
        assert result["children"][0]["operator_type"] == "Scan"
        assert result["children"][0]["table_name"] == "lineitem"

    def test_from_dict_simple(self) -> None:

        data = {
            "operator_type": "Scan",
            "operator_id": "scan_1",
            "table_name": "customer",
            "properties": {},
            "children": [],
            "physical_operator": None,
            "join_type": None,
            "join_conditions": None,
            "filter_expressions": None,
            "aggregation_functions": None,
            "group_by_keys": None,
            "sort_keys": None,
            "projection_expressions": None,
            "limit_count": None,
            "offset_count": None,
        }

        op = LogicalOperator.from_dict(data)

        assert op.operator_type == LogicalOperatorType.SCAN
        assert op.table_name == "customer"

    def test_from_dict_with_tree(self) -> None:

        data = {
            "operator_type": "Join",
            "operator_id": "join_1",
            "join_type": "inner",
            "join_conditions": ["o_custkey = c_custkey"],
            "properties": {},
            "children": [
                {
                    "operator_type": "Scan",
                    "operator_id": "scan_1",
                    "table_name": "orders",
                    "properties": {},
                    "children": [],
                    "physical_operator": None,
                    "join_type": None,
                    "join_conditions": None,
                    "filter_expressions": None,
                    "aggregation_functions": None,
                    "group_by_keys": None,
                    "sort_keys": None,
                    "projection_expressions": None,
                    "limit_count": None,
                    "offset_count": None,
                },
                {
                    "operator_type": "Scan",
                    "operator_id": "scan_2",
                    "table_name": "customer",
                    "properties": {},
                    "children": [],
                    "physical_operator": None,
                    "join_type": None,
                    "join_conditions": None,
                    "filter_expressions": None,
                    "aggregation_functions": None,
                    "group_by_keys": None,
                    "sort_keys": None,
                    "projection_expressions": None,
                    "limit_count": None,
                    "offset_count": None,
                },
            ],
            "physical_operator": None,
            "table_name": None,
            "filter_expressions": None,
            "aggregation_functions": None,
            "group_by_keys": None,
            "sort_keys": None,
            "projection_expressions": None,
            "limit_count": None,
            "offset_count": None,
        }

        op = LogicalOperator.from_dict(data)

        assert op.operator_type == LogicalOperatorType.JOIN
        assert op.join_type == JoinType.INNER
        assert len(op.children) == 2
        assert op.children[0].table_name == "orders"
        assert op.children[1].table_name == "customer"

    def test_round_trip_serialization_complex(self) -> None:

        scan_orders = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_1",
            table_name="orders",
        )
        scan_lineitem = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_2",
            table_name="lineitem",
        )
        join = LogicalOperator(
            operator_type=LogicalOperatorType.JOIN,
            operator_id="join_1",
            join_type=JoinType.INNER,
            join_conditions=["o_orderkey = l_orderkey"],
            children=[scan_orders, scan_lineitem],
        )
        filter_op = LogicalOperator(
            operator_type=LogicalOperatorType.FILTER,
            operator_id="filter_1",
            filter_expressions=["l_shipdate >= '1994-01-01'"],
            children=[join],
        )

        serialized = filter_op.to_dict()
        deserialized = LogicalOperator.from_dict(serialized)

        assert deserialized.operator_type == LogicalOperatorType.FILTER
        assert deserialized.filter_expressions == ["l_shipdate >= '1994-01-01'"]
        assert len(deserialized.children) == 1

        join_child = deserialized.children[0]
        assert join_child.operator_type == LogicalOperatorType.JOIN
        assert join_child.join_type == JoinType.INNER
        assert len(join_child.children) == 2

    def test_structural_signature_simple(self) -> None:

        op = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_1",
            table_name="orders",
        )

        signature = op.get_structural_signature()

        parsed = json.loads(signature)
        assert parsed["op"] == "Scan"
        assert parsed["table"] == "orders"

    def test_structural_signature_excludes_non_structural(self) -> None:

        op1 = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_1",
            table_name="orders",
            properties={"cost": 100.0},
        )
        op2 = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_999",
            table_name="orders",
            properties={"cost": 999.0},
        )

        assert op1.get_structural_signature() == op2.get_structural_signature()

    def test_structural_signature_includes_join_type(self) -> None:

        left_scan = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_1",
            table_name="orders",
        )
        right_scan = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_2",
            table_name="lineitem",
        )

        inner_join = LogicalOperator(
            operator_type=LogicalOperatorType.JOIN,
            operator_id="join_1",
            join_type=JoinType.INNER,
            children=[left_scan, right_scan],
        )
        left_join = LogicalOperator(
            operator_type=LogicalOperatorType.JOIN,
            operator_id="join_1",
            join_type=JoinType.LEFT,
            children=[left_scan, right_scan],
        )

        assert inner_join.get_structural_signature() != left_join.get_structural_signature()
        assert json.loads(inner_join.get_structural_signature())["join"] == "inner"
        assert json.loads(left_join.get_structural_signature())["join"] == "left"


class TestQueryPlanDAG:
    def test_basic_instantiation(self) -> None:

        root = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_1",
            table_name="orders",
        )
        plan = QueryPlanDAG(
            query_id="q01",
            platform="duckdb",
            logical_root=root,
            estimated_cost=100.0,
            estimated_rows=1000,
        )

        assert plan.query_id == "q01"
        assert plan.platform == "duckdb"
        assert plan.logical_root.table_name == "orders"
        assert plan.estimated_cost == 100.0
        assert plan.estimated_rows == 1000

    def test_automatic_fingerprint_computation(self) -> None:

        root = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_1",
            table_name="lineitem",
        )
        plan = QueryPlanDAG(query_id="q01", platform="duckdb", logical_root=root)

        assert plan.plan_fingerprint is not None
        assert isinstance(plan.plan_fingerprint, str)
        assert len(plan.plan_fingerprint) == 64

    def test_explicit_fingerprint_preserved(self) -> None:

        root = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_1",
            table_name="customer",
        )
        explicit_fp = "a" * 64
        plan = QueryPlanDAG(
            query_id="q01",
            platform="duckdb",
            logical_root=root,
            plan_fingerprint=explicit_fp,
        )

        assert plan.plan_fingerprint == explicit_fp

    def test_fingerprint_deterministic(self) -> None:

        root1 = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_1",
            table_name="orders",
        )
        root2 = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_1",
            table_name="orders",
        )

        plan1 = QueryPlanDAG(query_id="q01", platform="duckdb", logical_root=root1)
        plan2 = QueryPlanDAG(query_id="q01", platform="duckdb", logical_root=root2)

        assert plan1.plan_fingerprint == plan2.plan_fingerprint

    def test_fingerprint_different_for_different_plans(self) -> None:

        root1 = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_1",
            table_name="orders",
        )
        root2 = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_1",
            table_name="customer",
        )

        plan1 = QueryPlanDAG(query_id="q01", platform="duckdb", logical_root=root1)
        plan2 = QueryPlanDAG(query_id="q01", platform="duckdb", logical_root=root2)

        assert plan1.plan_fingerprint != plan2.plan_fingerprint

    def test_fingerprint_ignores_cost_changes(self) -> None:

        root1 = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_1",
            table_name="lineitem",
            properties={"cost": 100.0},
        )
        root2 = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_2",
            table_name="lineitem",
            properties={"cost": 999.0},
        )

        plan1 = QueryPlanDAG(
            query_id="q01",
            platform="duckdb",
            logical_root=root1,
            estimated_cost=100.0,
        )
        plan2 = QueryPlanDAG(
            query_id="q01",
            platform="duckdb",
            logical_root=root2,
            estimated_cost=999.0,
        )

        assert plan1.plan_fingerprint == plan2.plan_fingerprint

    def test_to_dict(self) -> None:

        root = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_1",
            table_name="orders",
        )
        plan = QueryPlanDAG(
            query_id="q01",
            platform="duckdb",
            logical_root=root,
            estimated_cost=100.0,
            raw_explain_output="EXPLAIN SELECT * FROM orders",
        )

        result = plan.to_dict()

        assert result["query_id"] == "q01"
        assert result["platform"] == "duckdb"
        assert result["estimated_cost"] == 100.0
        assert result["raw_explain_output"] == "EXPLAIN SELECT * FROM orders"
        assert "logical_root" in result
        assert result["logical_root"]["table_name"] == "orders"

    def test_from_dict(self) -> None:

        data = {
            "query_id": "q05",
            "platform": "postgres",
            "estimated_cost": 250.0,
            "estimated_rows": 5000,
            "plan_fingerprint": "abc123",
            "raw_explain_output": None,
            "logical_root": {
                "operator_type": "Scan",
                "operator_id": "scan_1",
                "table_name": "customer",
                "properties": {},
                "children": [],
                "physical_operator": None,
                "join_type": None,
                "join_conditions": None,
                "filter_expressions": None,
                "aggregation_functions": None,
                "group_by_keys": None,
                "sort_keys": None,
                "projection_expressions": None,
                "limit_count": None,
                "offset_count": None,
            },
        }

        plan = QueryPlanDAG.from_dict(data)

        assert plan.query_id == "q05"
        assert plan.platform == "postgres"
        assert plan.estimated_cost == 250.0
        assert plan.logical_root.table_name == "customer"

    def test_round_trip_serialization(self) -> None:

        scan_orders = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_1",
            table_name="orders",
        )
        scan_lineitem = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_2",
            table_name="lineitem",
        )
        join = LogicalOperator(
            operator_type=LogicalOperatorType.JOIN,
            operator_id="join_1",
            join_type=JoinType.INNER,
            join_conditions=["o_orderkey = l_orderkey"],
            children=[scan_orders, scan_lineitem],
        )

        original = QueryPlanDAG(
            query_id="q01",
            platform="duckdb",
            logical_root=join,
            estimated_cost=500.0,
            estimated_rows=10000,
            raw_explain_output="EXPLAIN ...",
        )

        serialized = original.to_dict()
        deserialized = QueryPlanDAG.from_dict(serialized)

        assert deserialized.query_id == original.query_id
        assert deserialized.platform == original.platform
        assert deserialized.estimated_cost == original.estimated_cost
        assert deserialized.estimated_rows == original.estimated_rows
        assert deserialized.raw_explain_output == original.raw_explain_output
        assert deserialized.logical_root.operator_type == LogicalOperatorType.JOIN
        assert len(deserialized.logical_root.children) == 2

    def test_to_json(self) -> None:

        root = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_1",
            table_name="lineitem",
        )
        plan = QueryPlanDAG(query_id="q01", platform="duckdb", logical_root=root)

        json_str = plan.to_json()

        parsed = json.loads(json_str)
        assert parsed["query_id"] == "q01"
        assert parsed["platform"] == "duckdb"
        assert parsed["logical_root"]["table_name"] == "lineitem"

    def test_from_json(self) -> None:

        json_str = """
        {
            "query_id": "q17",
            "platform": "snowflake",
            "estimated_cost": 1000.0,
            "estimated_rows": null,
            "plan_fingerprint": "xyz789",
            "raw_explain_output": null,
            "logical_root": {
                "operator_type": "Scan",
                "operator_id": "scan_1",
                "table_name": "part",
                "properties": {},
                "children": [],
                "physical_operator": null,
                "join_type": null,
                "join_conditions": null,
                "filter_expressions": null,
                "aggregation_functions": null,
                "group_by_keys": null,
                "sort_keys": null,
                "projection_expressions": null,
                "limit_count": null,
                "offset_count": null
            }
        }
        """

        plan = QueryPlanDAG.from_json(json_str)

        assert plan.query_id == "q17"
        assert plan.platform == "snowflake"
        assert plan.estimated_cost == 1000.0
        assert plan.logical_root.table_name == "part"

    def test_json_round_trip(self) -> None:

        scan = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_1",
            table_name="supplier",
        )
        filter_op = LogicalOperator(
            operator_type=LogicalOperatorType.FILTER,
            operator_id="filter_1",
            filter_expressions=["s_acctbal > 0"],
            children=[scan],
        )

        original = QueryPlanDAG(
            query_id="q22",
            platform="datafusion",
            logical_root=filter_op,
            estimated_cost=75.0,
        )

        json_str = original.to_json()
        deserialized = QueryPlanDAG.from_json(json_str)

        assert deserialized.query_id == original.query_id
        assert deserialized.platform == original.platform
        assert deserialized.estimated_cost == original.estimated_cost
        assert deserialized.logical_root.operator_type == LogicalOperatorType.FILTER


class TestStandaloneFingerprintFunction:
    def test_standalone_fingerprint_matches_method(self) -> None:

        root = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_1",
            table_name="nation",
        )

        standalone_fp = compute_plan_fingerprint(root)

        plan = QueryPlanDAG(query_id="q01", platform="duckdb", logical_root=root)

        assert standalone_fp == plan.plan_fingerprint

    def test_standalone_fingerprint_deterministic(self) -> None:

        root = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_1",
            table_name="region",
        )

        fp1 = compute_plan_fingerprint(root)
        fp2 = compute_plan_fingerprint(root)

        assert fp1 == fp2
        assert len(fp1) == 64


class TestEnumTypes:
    def test_logical_operator_type_enum(self) -> None:

        assert LogicalOperatorType.SCAN.value == "Scan"
        assert LogicalOperatorType.JOIN.value == "Join"
        assert LogicalOperatorType.FILTER.value == "Filter"
        assert LogicalOperatorType.AGGREGATE.value == "Aggregate"

    def test_join_type_enum(self) -> None:

        assert JoinType.INNER.value == "inner"
        assert JoinType.LEFT.value == "left"
        assert JoinType.RIGHT.value == "right"
        assert JoinType.FULL.value == "full"

    def test_aggregate_function_enum(self) -> None:

        assert AggregateFunction.COUNT.value == "count"
        assert AggregateFunction.SUM.value == "sum"
        assert AggregateFunction.AVG.value == "avg"


class TestEdgeCases:
    def test_empty_operator_tree(self) -> None:

        op = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_1",
            table_name="empty",
            children=[],
        )

        assert len(op.children) == 0
        signature = op.get_structural_signature()
        assert "Scan" in signature

    def test_deep_operator_tree(self) -> None:

        scan1 = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_1",
            table_name="table1",
        )
        scan2 = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_2",
            table_name="table2",
        )
        join = LogicalOperator(
            operator_type=LogicalOperatorType.JOIN,
            operator_id="join_1",
            join_type=JoinType.INNER,
            children=[scan1, scan2],
        )
        filter_op = LogicalOperator(
            operator_type=LogicalOperatorType.FILTER,
            operator_id="filter_1",
            filter_expressions=["col > 0"],
            children=[join],
        )
        agg = LogicalOperator(
            operator_type=LogicalOperatorType.AGGREGATE,
            operator_id="agg_1",
            aggregation_functions=["count(*)"],
            children=[filter_op],
        )
        sort = LogicalOperator(
            operator_type=LogicalOperatorType.SORT,
            operator_id="sort_1",
            sort_keys=[{"expr": "col", "direction": "ASC"}],
            children=[agg],
        )
        limit = LogicalOperator(
            operator_type=LogicalOperatorType.LIMIT,
            operator_id="limit_1",
            limit_count=10,
            children=[sort],
        )

        serialized = limit.to_dict()
        deserialized = LogicalOperator.from_dict(serialized)

        assert deserialized.operator_type == LogicalOperatorType.LIMIT
        assert deserialized.limit_count == 10
        current = deserialized
        depth = 0
        while current.children:
            current = current.children[0]
            depth += 1
        assert depth == 5

    def test_plan_with_no_cost_estimates(self) -> None:

        root = LogicalOperator(
            operator_type=LogicalOperatorType.SCAN,
            operator_id="scan_1",
            table_name="test",
        )
        plan = QueryPlanDAG(
            query_id="q01",
            platform="sqlite",
            logical_root=root,
            estimated_cost=None,
            estimated_rows=None,
        )

        assert plan.estimated_cost is None
        assert plan.estimated_rows is None
        assert plan.plan_fingerprint is not None

    def test_operator_with_string_type_instead_of_enum(self) -> None:

        op = LogicalOperator(
            operator_type="CustomScan",
            operator_id="custom_1",
        )

        assert op.operator_type == "CustomScan"
        serialized = op.to_dict()
        assert serialized["operator_type"] == "CustomScan"


class TestFingerprintCoverage:
    def test_fingerprint_includes_join_conditions(self) -> None:

        from benchbox.core.results.query_plan_models import FingerprintIntegrity

        plan1 = QueryPlanDAG(
            query_id="q1",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="join_1",
                operator_type=LogicalOperatorType.JOIN,
                join_type=JoinType.INNER,
                join_conditions=["a.id = b.id"],
                children=[
                    LogicalOperator(operator_id="scan_1", operator_type=LogicalOperatorType.SCAN, table_name="a"),
                    LogicalOperator(operator_id="scan_2", operator_type=LogicalOperatorType.SCAN, table_name="b"),
                ],
            ),
        )

        plan2 = QueryPlanDAG(
            query_id="q2",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="join_1",
                operator_type=LogicalOperatorType.JOIN,
                join_type=JoinType.INNER,
                join_conditions=["a.id = b.foreign_id"],
                children=[
                    LogicalOperator(operator_id="scan_1", operator_type=LogicalOperatorType.SCAN, table_name="a"),
                    LogicalOperator(operator_id="scan_2", operator_type=LogicalOperatorType.SCAN, table_name="b"),
                ],
            ),
        )

        assert plan1.plan_fingerprint != plan2.plan_fingerprint
        assert plan1.fingerprint_integrity == FingerprintIntegrity.VERIFIED

    def test_fingerprint_includes_group_by_keys(self) -> None:

        plan1 = QueryPlanDAG(
            query_id="q1",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="agg_1",
                operator_type=LogicalOperatorType.AGGREGATE,
                group_by_keys=["region", "year"],
                children=[
                    LogicalOperator(operator_id="scan_1", operator_type=LogicalOperatorType.SCAN, table_name="orders"),
                ],
            ),
        )

        plan2 = QueryPlanDAG(
            query_id="q2",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="agg_1",
                operator_type=LogicalOperatorType.AGGREGATE,
                group_by_keys=["region", "month"],
                children=[
                    LogicalOperator(operator_id="scan_1", operator_type=LogicalOperatorType.SCAN, table_name="orders"),
                ],
            ),
        )

        assert plan1.plan_fingerprint != plan2.plan_fingerprint

    def test_fingerprint_includes_projection_expressions(self) -> None:

        plan1 = QueryPlanDAG(
            query_id="q1",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="proj_1",
                operator_type=LogicalOperatorType.PROJECT,
                projection_expressions=["id", "name", "total"],
                children=[
                    LogicalOperator(operator_id="scan_1", operator_type=LogicalOperatorType.SCAN, table_name="orders"),
                ],
            ),
        )

        plan2 = QueryPlanDAG(
            query_id="q2",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="proj_1",
                operator_type=LogicalOperatorType.PROJECT,
                projection_expressions=["id", "name"],
                children=[
                    LogicalOperator(operator_id="scan_1", operator_type=LogicalOperatorType.SCAN, table_name="orders"),
                ],
            ),
        )

        assert plan1.plan_fingerprint != plan2.plan_fingerprint

    def test_fingerprint_includes_limit_count(self) -> None:

        plan1 = QueryPlanDAG(
            query_id="q1",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="limit_1",
                operator_type=LogicalOperatorType.LIMIT,
                limit_count=100,
                children=[
                    LogicalOperator(operator_id="scan_1", operator_type=LogicalOperatorType.SCAN, table_name="orders"),
                ],
            ),
        )

        plan2 = QueryPlanDAG(
            query_id="q2",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="limit_1",
                operator_type=LogicalOperatorType.LIMIT,
                limit_count=50,
                children=[
                    LogicalOperator(operator_id="scan_1", operator_type=LogicalOperatorType.SCAN, table_name="orders"),
                ],
            ),
        )

        assert plan1.plan_fingerprint != plan2.plan_fingerprint

    def test_fingerprint_includes_offset_count(self) -> None:

        plan1 = QueryPlanDAG(
            query_id="q1",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="limit_1",
                operator_type=LogicalOperatorType.LIMIT,
                limit_count=100,
                offset_count=0,
                children=[
                    LogicalOperator(operator_id="scan_1", operator_type=LogicalOperatorType.SCAN, table_name="orders"),
                ],
            ),
        )

        plan2 = QueryPlanDAG(
            query_id="q2",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="limit_1",
                operator_type=LogicalOperatorType.LIMIT,
                limit_count=100,
                offset_count=10,
                children=[
                    LogicalOperator(operator_id="scan_1", operator_type=LogicalOperatorType.SCAN, table_name="orders"),
                ],
            ),
        )

        assert plan1.plan_fingerprint != plan2.plan_fingerprint

    def test_fingerprint_join_conditions_order_invariant(self) -> None:
        plan1 = QueryPlanDAG(
            query_id="q1",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="join_1",
                operator_type=LogicalOperatorType.JOIN,
                join_conditions=["a.id = b.id", "a.type = b.type"],
                children=[
                    LogicalOperator(operator_id="scan_1", operator_type=LogicalOperatorType.SCAN, table_name="a"),
                    LogicalOperator(operator_id="scan_2", operator_type=LogicalOperatorType.SCAN, table_name="b"),
                ],
            ),
        )

        plan2 = QueryPlanDAG(
            query_id="q2",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="join_1",
                operator_type=LogicalOperatorType.JOIN,
                join_conditions=["a.type = b.type", "a.id = b.id"],
                children=[
                    LogicalOperator(operator_id="scan_1", operator_type=LogicalOperatorType.SCAN, table_name="a"),
                    LogicalOperator(operator_id="scan_2", operator_type=LogicalOperatorType.SCAN, table_name="b"),
                ],
            ),
        )

        assert plan1.plan_fingerprint == plan2.plan_fingerprint

    def test_fingerprint_group_by_order_matters(self) -> None:
        plan1 = QueryPlanDAG(
            query_id="q1",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="agg_1",
                operator_type=LogicalOperatorType.AGGREGATE,
                group_by_keys=["region", "year"],
                children=[
                    LogicalOperator(operator_id="scan_1", operator_type=LogicalOperatorType.SCAN, table_name="orders"),
                ],
            ),
        )

        plan2 = QueryPlanDAG(
            query_id="q2",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="agg_1",
                operator_type=LogicalOperatorType.AGGREGATE,
                group_by_keys=["year", "region"],
                children=[
                    LogicalOperator(operator_id="scan_1", operator_type=LogicalOperatorType.SCAN, table_name="orders"),
                ],
            ),
        )

        assert plan1.plan_fingerprint != plan2.plan_fingerprint


class TestLiteralNormalization:
    def test_numeric_and_string_literals_are_masked(self) -> None:
        assert _normalize_literal_text("l_quantity > 24") == "l_quantity > ?"
        assert _normalize_literal_text("c_name = 'ALICE'") == "c_name = ?"

    def test_ordinal_column_references_are_preserved(self) -> None:
        assert _normalize_literal_text("#0") == "#0"
        assert _normalize_literal_text("#1") == "#1"
        assert _normalize_literal_text("group by #0, #1") == "group by #0, #1"
        assert _normalize_literal_text("#0 > 24") == "#0 > ?"

    def test_normalize_literals_distinguishes_different_ordinal_refs(self) -> None:
        plan_col0 = QueryPlanDAG(
            query_id="q1",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="agg_1",
                operator_type=LogicalOperatorType.AGGREGATE,
                group_by_keys=["#0"],
                children=[
                    LogicalOperator(operator_id="scan_1", operator_type=LogicalOperatorType.SCAN, table_name="orders"),
                ],
            ),
        )
        plan_col1 = QueryPlanDAG(
            query_id="q2",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="agg_1",
                operator_type=LogicalOperatorType.AGGREGATE,
                group_by_keys=["#1"],
                children=[
                    LogicalOperator(operator_id="scan_1", operator_type=LogicalOperatorType.SCAN, table_name="orders"),
                ],
            ),
        )

        assert plan_col0.compute_plan_fingerprint(normalize_literals=True) != plan_col1.compute_plan_fingerprint(
            normalize_literals=True
        )

    def test_normalize_literals_collapses_only_constant_differences(self) -> None:
        plan_a = QueryPlanDAG(
            query_id="q1",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="filter_1",
                operator_type=LogicalOperatorType.FILTER,
                filter_expressions=["l_quantity > 24"],
                children=[
                    LogicalOperator(
                        operator_id="scan_1", operator_type=LogicalOperatorType.SCAN, table_name="lineitem"
                    ),
                ],
            ),
        )
        plan_b = QueryPlanDAG(
            query_id="q2",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="filter_1",
                operator_type=LogicalOperatorType.FILTER,
                filter_expressions=["l_quantity > 30"],
                children=[
                    LogicalOperator(
                        operator_id="scan_1", operator_type=LogicalOperatorType.SCAN, table_name="lineitem"
                    ),
                ],
            ),
        )

        assert plan_a.plan_fingerprint != plan_b.plan_fingerprint
        assert plan_a.compute_plan_fingerprint(normalize_literals=True) == plan_b.compute_plan_fingerprint(
            normalize_literals=True
        )


class TestFingerprintVerification:
    def test_from_dict_verifies_fingerprint(self) -> None:

        from benchbox.core.results.query_plan_models import FingerprintIntegrity

        original = QueryPlanDAG(
            query_id="q1",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="scan_1",
                operator_type=LogicalOperatorType.SCAN,
                table_name="orders",
            ),
        )
        data = original.to_dict()

        loaded = QueryPlanDAG.from_dict(data)
        assert loaded.fingerprint_integrity == FingerprintIntegrity.VERIFIED
        assert loaded.is_fingerprint_trusted()

    def test_from_dict_detects_stale_fingerprint(self) -> None:

        from benchbox.core.results.query_plan_models import FingerprintIntegrity

        original = QueryPlanDAG(
            query_id="q1",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="scan_1",
                operator_type=LogicalOperatorType.SCAN,
                table_name="orders",
            ),
        )
        data = original.to_dict()

        data["plan_fingerprint"] = "invalid_fingerprint"

        loaded = QueryPlanDAG.from_dict(data)
        assert loaded.fingerprint_integrity == FingerprintIntegrity.STALE
        assert not loaded.is_fingerprint_trusted()

    def test_from_dict_refresh_on_mismatch(self) -> None:

        from benchbox.core.results.query_plan_models import FingerprintIntegrity

        original = QueryPlanDAG(
            query_id="q1",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="scan_1",
                operator_type=LogicalOperatorType.SCAN,
                table_name="orders",
            ),
        )
        data = original.to_dict()
        correct_fingerprint = original.plan_fingerprint

        data["plan_fingerprint"] = "invalid_fingerprint"

        loaded = QueryPlanDAG.from_dict(data, refresh_on_mismatch=True)
        assert loaded.fingerprint_integrity == FingerprintIntegrity.RECOMPUTED
        assert loaded.is_fingerprint_trusted()
        assert loaded.plan_fingerprint == correct_fingerprint

    def test_from_dict_skip_verification(self) -> None:

        from benchbox.core.results.query_plan_models import FingerprintIntegrity

        original = QueryPlanDAG(
            query_id="q1",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="scan_1",
                operator_type=LogicalOperatorType.SCAN,
                table_name="orders",
            ),
        )
        data = original.to_dict()

        data["plan_fingerprint"] = "invalid_fingerprint"

        loaded = QueryPlanDAG.from_dict(data, verify_fingerprint=False)
        assert loaded.fingerprint_integrity == FingerprintIntegrity.UNVERIFIED
        assert not loaded.is_fingerprint_trusted()
        assert loaded.plan_fingerprint == "invalid_fingerprint"

    def test_verify_fingerprint_method(self) -> None:

        from benchbox.core.results.query_plan_models import FingerprintIntegrity

        plan = QueryPlanDAG(
            query_id="q1",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="scan_1",
                operator_type=LogicalOperatorType.SCAN,
                table_name="orders",
            ),
        )

        assert plan.verify_fingerprint() is True
        assert plan.fingerprint_integrity == FingerprintIntegrity.VERIFIED

        plan.plan_fingerprint = "invalid"
        assert plan.verify_fingerprint() is False
        assert plan.fingerprint_integrity == FingerprintIntegrity.STALE

    def test_refresh_fingerprint_method(self) -> None:

        from benchbox.core.results.query_plan_models import FingerprintIntegrity

        plan = QueryPlanDAG(
            query_id="q1",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="scan_1",
                operator_type=LogicalOperatorType.SCAN,
                table_name="orders",
            ),
        )

        plan.plan_fingerprint = "invalid"
        plan.fingerprint_integrity = FingerprintIntegrity.STALE

        plan.refresh_fingerprint()
        assert plan.fingerprint_integrity == FingerprintIntegrity.VERIFIED
        assert plan.verify_fingerprint() is True


class TestHelperFunctions:
    def test_get_operator_type_str_with_enum(self) -> None:

        from benchbox.core.results.query_plan_models import get_operator_type_str

        result = get_operator_type_str(LogicalOperatorType.SCAN)
        assert result == "Scan"

        result = get_operator_type_str(LogicalOperatorType.JOIN)
        assert result == "Join"

    def test_get_operator_type_str_with_string(self) -> None:

        from benchbox.core.results.query_plan_models import get_operator_type_str

        result = get_operator_type_str("CustomScan")
        assert result == "CustomScan"

        result = get_operator_type_str("IndexSeek")
        assert result == "IndexSeek"

    def test_get_join_type_str_with_enum(self) -> None:

        from benchbox.core.results.query_plan_models import get_join_type_str

        result = get_join_type_str(JoinType.INNER)
        assert result == "inner"

        result = get_join_type_str(JoinType.LEFT)
        assert result == "left"

    def test_get_join_type_str_with_string(self) -> None:

        from benchbox.core.results.query_plan_models import get_join_type_str

        result = get_join_type_str("parallel_hash")
        assert result == "parallel_hash"

    def test_get_join_type_str_with_none(self) -> None:

        from benchbox.core.results.query_plan_models import get_join_type_str

        result = get_join_type_str(None)
        assert result is None

    def test_normalize_operator_type_with_enum(self) -> None:

        from benchbox.core.results.query_plan_models import normalize_operator_type

        result = normalize_operator_type(LogicalOperatorType.SCAN)
        assert result == LogicalOperatorType.SCAN

    def test_normalize_operator_type_with_matching_string(self) -> None:

        from benchbox.core.results.query_plan_models import normalize_operator_type

        result = normalize_operator_type("Scan")
        assert result == LogicalOperatorType.SCAN

        result = normalize_operator_type("Join")
        assert result == LogicalOperatorType.JOIN

    def test_normalize_operator_type_with_unknown_string(self) -> None:

        from benchbox.core.results.query_plan_models import normalize_operator_type

        result = normalize_operator_type("CustomScan")
        assert result == "CustomScan"

    def test_is_operator_type_match_same_enum(self) -> None:

        from benchbox.core.results.query_plan_models import is_operator_type_match

        result = is_operator_type_match(LogicalOperatorType.SCAN, LogicalOperatorType.SCAN)
        assert result is True

    def test_is_operator_type_match_different_enum(self) -> None:

        from benchbox.core.results.query_plan_models import is_operator_type_match

        result = is_operator_type_match(LogicalOperatorType.SCAN, LogicalOperatorType.JOIN)
        assert result is False

    def test_is_operator_type_match_enum_and_matching_string(self) -> None:

        from benchbox.core.results.query_plan_models import is_operator_type_match

        result = is_operator_type_match(LogicalOperatorType.SCAN, "Scan")
        assert result is True

        result = is_operator_type_match("Scan", LogicalOperatorType.SCAN)
        assert result is True

    def test_is_operator_type_match_same_string(self) -> None:

        from benchbox.core.results.query_plan_models import is_operator_type_match

        result = is_operator_type_match("CustomScan", "CustomScan")
        assert result is True

    def test_is_operator_type_match_different_string(self) -> None:

        from benchbox.core.results.query_plan_models import is_operator_type_match

        result = is_operator_type_match("CustomScan", "IndexScan")
        assert result is False


class TestUnknownTypeWarnings:
    def test_unknown_operator_type_logs_warning(self, caplog) -> None:

        import logging

        from benchbox.core.results.query_plan_models import (
            clear_unknown_type_warnings,
            get_operator_type_str,
        )

        clear_unknown_type_warnings()

        with caplog.at_level(logging.WARNING):
            result = get_operator_type_str("UnknownCustomOperator")

        assert result == "UnknownCustomOperator"
        assert "Unknown operator type 'UnknownCustomOperator'" in caplog.text
        assert "Consider adding a mapping" in caplog.text

    def test_known_string_operator_type_no_warning(self, caplog) -> None:

        import logging

        from benchbox.core.results.query_plan_models import (
            clear_unknown_type_warnings,
            get_operator_type_str,
        )

        clear_unknown_type_warnings()

        with caplog.at_level(logging.WARNING):
            result = get_operator_type_str("Scan")

        assert result == "Scan"
        assert "Unknown operator type" not in caplog.text

    def test_unknown_operator_type_warning_logged_once(self, caplog) -> None:

        import logging

        from benchbox.core.results.query_plan_models import (
            clear_unknown_type_warnings,
            get_operator_type_str,
        )

        clear_unknown_type_warnings()

        with caplog.at_level(logging.WARNING):
            get_operator_type_str("RepeatedUnknownType")
            get_operator_type_str("RepeatedUnknownType")
            get_operator_type_str("RepeatedUnknownType")

        assert caplog.text.count("Unknown operator type 'RepeatedUnknownType'") == 1

    def test_multiple_unknown_operator_types_each_warned(self, caplog) -> None:

        import logging

        from benchbox.core.results.query_plan_models import (
            clear_unknown_type_warnings,
            get_operator_type_str,
        )

        clear_unknown_type_warnings()

        with caplog.at_level(logging.WARNING):
            get_operator_type_str("UnknownTypeA")
            get_operator_type_str("UnknownTypeB")

        assert "Unknown operator type 'UnknownTypeA'" in caplog.text
        assert "Unknown operator type 'UnknownTypeB'" in caplog.text

    def test_warn_unknown_parameter(self, caplog) -> None:

        import logging

        from benchbox.core.results.query_plan_models import (
            clear_unknown_type_warnings,
            get_operator_type_str,
        )

        clear_unknown_type_warnings()

        with caplog.at_level(logging.WARNING):
            result = get_operator_type_str("SilentUnknownType", warn_unknown=False)

        assert result == "SilentUnknownType"
        assert "Unknown operator type 'SilentUnknownType'" not in caplog.text

    def test_unknown_join_type_logs_warning(self, caplog) -> None:

        import logging

        from benchbox.core.results.query_plan_models import (
            clear_unknown_type_warnings,
            get_join_type_str,
        )

        clear_unknown_type_warnings()

        with caplog.at_level(logging.WARNING):
            result = get_join_type_str("parallel_hash_join")

        assert result == "parallel_hash_join"
        assert "Unknown join type 'parallel_hash_join'" in caplog.text
        assert "Consider adding a mapping" in caplog.text

    def test_known_string_join_type_no_warning(self, caplog) -> None:

        import logging

        from benchbox.core.results.query_plan_models import (
            clear_unknown_type_warnings,
            get_join_type_str,
        )

        clear_unknown_type_warnings()

        with caplog.at_level(logging.WARNING):
            result = get_join_type_str("inner")

        assert result == "inner"
        assert "Unknown join type" not in caplog.text

    def test_unknown_join_type_warning_logged_once(self, caplog) -> None:

        import logging

        from benchbox.core.results.query_plan_models import (
            clear_unknown_type_warnings,
            get_join_type_str,
        )

        clear_unknown_type_warnings()

        with caplog.at_level(logging.WARNING):
            get_join_type_str("repeated_custom_join")
            get_join_type_str("repeated_custom_join")

        assert caplog.text.count("Unknown join type 'repeated_custom_join'") == 1

    def test_clear_unknown_type_warnings_resets_state(self, caplog) -> None:

        import logging

        from benchbox.core.results.query_plan_models import (
            clear_unknown_type_warnings,
            get_operator_type_str,
        )

        clear_unknown_type_warnings()
        with caplog.at_level(logging.WARNING):
            get_operator_type_str("ClearTestType")
        assert "Unknown operator type 'ClearTestType'" in caplog.text

        caplog.clear()
        clear_unknown_type_warnings()
        with caplog.at_level(logging.WARNING):
            get_operator_type_str("ClearTestType")
        assert "Unknown operator type 'ClearTestType'" in caplog.text

    def test_is_operator_type_match_does_not_warn(self, caplog) -> None:

        import logging

        from benchbox.core.results.query_plan_models import (
            clear_unknown_type_warnings,
            is_operator_type_match,
        )

        clear_unknown_type_warnings()

        with caplog.at_level(logging.WARNING):
            result = is_operator_type_match("UnknownMatch1", "UnknownMatch2")

        assert result is False
        assert "Unknown operator type" not in caplog.text


class TestPlanDepthTruncation:
    @staticmethod
    def _linear_chain(depth: int) -> LogicalOperator:
        node = LogicalOperator(operator_type=LogicalOperatorType.SCAN, operator_id="scan_leaf", table_name="t")
        for level in range(depth):
            node = LogicalOperator(
                operator_type=LogicalOperatorType.FILTER,
                operator_id=f"filter_{level}",
                filter_expressions=[f"c > {level}"],
                children=[node],
            )
        return node

    def test_to_dict_emits_truncation_marker_beyond_max_depth(self) -> None:
        root = self._linear_chain(6)

        serialized = root.to_dict(max_depth=3)

        node = serialized
        depth = 0
        while "truncated_at_depth" not in node:
            assert node["children"], f"reached a leaf at depth {depth} before truncation"
            node = node["children"][0]
            depth += 1
        assert node["truncated_at_depth"] == 4
        assert node["children_omitted"] >= 1
        assert node["operator_id"].startswith(("filter_", "scan_"))
        assert "children" not in node

    def test_to_dict_no_truncation_when_within_max_depth(self) -> None:
        root = self._linear_chain(3)
        serialized = root.to_dict(max_depth=50)

        node = serialized
        while node.get("children"):
            assert "truncated_at_depth" not in node
            node = node["children"][0]
        assert node["operator_id"] == "scan_leaf"

    def test_deep_plan_serializes_instead_of_raising(self) -> None:
        root = self._linear_chain(60)
        plan = QueryPlanDAG(query_id="deep_q", platform="duckdb", logical_root=root)

        size = plan.estimate_serialized_size()
        assert size > 0
        payload = plan.to_json()
        assert "truncated_at_depth" in payload

    def test_truncation_marker_round_trips_as_leaf(self) -> None:
        root = self._linear_chain(5)
        serialized = root.to_dict(max_depth=2)
        restored = LogicalOperator.from_dict(serialized)

        node = restored
        while node.children:
            node = node.children[0]
        assert node.children == []

    def test_fingerprint_unaffected_by_serialization_truncation(self) -> None:
        root = self._linear_chain(60)
        plan = QueryPlanDAG(query_id="fp_q", platform="duckdb", logical_root=root)

        fp = plan.plan_fingerprint
        plan.estimate_serialized_size(max_depth=3)
        assert plan.plan_fingerprint == fp
        assert plan.compute_plan_fingerprint() == fp

    def test_configurable_max_depth_changes_truncation_point(self) -> None:
        root = self._linear_chain(10)

        def _first_truncated_depth(md: int) -> int:
            node = root.to_dict(max_depth=md)
            depth = 0
            while "truncated_at_depth" not in node:
                node = node["children"][0]
                depth += 1
            return depth

        assert _first_truncated_depth(2) == 3
        assert _first_truncated_depth(5) == 6


