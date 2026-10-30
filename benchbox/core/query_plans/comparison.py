from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from benchbox.core.results.loader import iter_query_results
from benchbox.core.results.query_plan_models import (
    LogicalOperator,
    LogicalOperatorType,
    QueryPlanDAG,
    get_join_type_str,
    get_operator_type_str,
    is_operator_type_match,
)

logger = logging.getLogger(__name__)


BACKBONE_OPERATOR_TYPES: frozenset[str] = frozenset(
    {
        LogicalOperatorType.SCAN.value,
        LogicalOperatorType.JOIN.value,
        LogicalOperatorType.AGGREGATE.value,
        LogicalOperatorType.SORT.value,
        LogicalOperatorType.LIMIT.value,
        LogicalOperatorType.UNION.value,
        LogicalOperatorType.INTERSECT.value,
        LogicalOperatorType.EXCEPT.value,
        LogicalOperatorType.WINDOW.value,
    }
)

COLLAPSIBLE_OPERATOR_TYPES: frozenset[str] = frozenset(
    {
        LogicalOperatorType.AGGREGATE.value,
        LogicalOperatorType.SORT.value,
    }
)

TRANSPARENT_WRAPPER_TYPES: frozenset[str] = frozenset(
    {
        LogicalOperatorType.OTHER.value,
    }
)


def _backbone_type(node: LogicalOperator) -> str | None:
    type_str = get_operator_type_str(node.operator_type, warn_unknown=False)
    return type_str if type_str in BACKBONE_OPERATOR_TYPES else None


def structural_backbone_counts(
    root: LogicalOperator,
    *,
    only: set[str] | frozenset[str] | None = None,
) -> dict[str, int]:
    if only is not None and not only:
        raise ValueError("`only` must be a non-empty set of operator types, or None to include all")

    counts: dict[str, int] = {}

    def walk(node: LogicalOperator, collapsing_type: str | None) -> None:
        backbone = _backbone_type(node)
        if backbone is None:
            wrapper_type = get_operator_type_str(node.operator_type, warn_unknown=False)
            child_collapsing = collapsing_type if wrapper_type in TRANSPARENT_WRAPPER_TYPES else None
            for child in node.children:
                walk(child, child_collapsing)
            return
        if not (backbone in COLLAPSIBLE_OPERATOR_TYPES and backbone == collapsing_type):
            counts[backbone] = counts.get(backbone, 0) + 1
        next_collapsing = backbone if backbone in COLLAPSIBLE_OPERATOR_TYPES else None
        for child in node.children:
            walk(child, next_collapsing)

    walk(root, None)

    if only is not None:
        return {key: counts.get(key, 0) for key in only}
    return counts


def comparable_subset_signature(
    root: LogicalOperator,
    *,
    only: set[str] | frozenset[str] | None = None,
) -> str:
    counts = structural_backbone_counts(root, only=only)
    return "|".join(f"{op_type}:{counts[op_type]}" for op_type in sorted(counts))


def structural_backbones_match(
    left: LogicalOperator,
    right: LogicalOperator,
    *,
    only: set[str] | frozenset[str] | None = None,
) -> bool:
    return structural_backbone_counts(left, only=only) == structural_backbone_counts(right, only=only)


@dataclass
class OperatorDiff:
    operator_id_left: str
    operator_id_right: str
    diff_type: str
    differences: dict[str, Any] = field(default_factory=dict)


@dataclass
class SimilarityScore:
    overall_similarity: float
    structural_similarity: float
    operator_similarity: float
    property_similarity: float

    total_operators_left: int
    total_operators_right: int
    matching_operators: int
    type_mismatches: int
    property_mismatches: int
    structure_mismatches: int


@dataclass
class PlanComparison:
    plan_left: QueryPlanDAG
    plan_right: QueryPlanDAG

    plans_identical: bool
    fingerprints_match: bool

    similarity: SimilarityScore

    operator_diffs: list[OperatorDiff] = field(default_factory=list)

    summary: str = ""


def _compare_scan(left: LogicalOperator, right: LogicalOperator, diffs: dict[str, Any]) -> None:
    if left.table_name != right.table_name:
        diffs["table_name"] = {"left": left.table_name, "right": right.table_name}


def _compare_join(left: LogicalOperator, right: LogicalOperator, diffs: dict[str, Any]) -> None:
    if left.join_type != right.join_type:
        diffs["join_type"] = {
            "left": get_join_type_str(left.join_type),
            "right": get_join_type_str(right.join_type),
        }


def _compare_set_diff(left: LogicalOperator, right: LogicalOperator, diffs: dict[str, Any], attr: str) -> None:
    left_vals = set(getattr(left, attr) or [])
    right_vals = set(getattr(right, attr) or [])
    if left_vals != right_vals:
        diffs[attr] = {
            "left_only": sorted(left_vals - right_vals),
            "right_only": sorted(right_vals - left_vals),
        }


def _compare_sort(left: LogicalOperator, right: LogicalOperator, diffs: dict[str, Any]) -> None:
    if left.sort_keys != right.sort_keys:
        diffs["sort_keys"] = {"left": left.sort_keys, "right": right.sort_keys}


def _compare_aggregate(left: LogicalOperator, right: LogicalOperator, diffs: dict[str, Any]) -> None:
    _compare_set_diff(left, right, diffs, "aggregation_functions")
    if (left.group_by_keys or []) != (right.group_by_keys or []):
        diffs["group_by_keys"] = {"left": left.group_by_keys, "right": right.group_by_keys}


def _compare_limit(left: LogicalOperator, right: LogicalOperator, diffs: dict[str, Any]) -> None:
    if left.limit_count != right.limit_count:
        diffs["limit_count"] = {"left": left.limit_count, "right": right.limit_count}
    if left.offset_count != right.offset_count:
        diffs["offset_count"] = {"left": left.offset_count, "right": right.offset_count}


def _compare_project(left: LogicalOperator, right: LogicalOperator, diffs: dict[str, Any]) -> None:
    if (left.projection_expressions or []) != (right.projection_expressions or []):
        diffs["projection_expressions"] = {
            "left": left.projection_expressions,
            "right": right.projection_expressions,
        }


_OPERATOR_COMPARATORS: dict[str, Callable[[LogicalOperator, LogicalOperator, dict[str, Any]], None]] = {
    "Scan": _compare_scan,
    "Join": _compare_join,
    "Filter": lambda l, r, d: _compare_set_diff(l, r, d, "filter_expressions"),
    "Aggregate": _compare_aggregate,
    "Sort": _compare_sort,
    "Limit": _compare_limit,
    "Project": _compare_project,
}


def _truncation_caveat(plan_left: QueryPlanDAG, plan_right: QueryPlanDAG) -> OperatorDiff | None:
    cuts = [
        depth
        for depth in (
            getattr(plan_left, "truncated_at_depth", None),
            getattr(plan_right, "truncated_at_depth", None),
        )
        if depth is not None
    ]
    if not cuts:
        return None
    left_fp = plan_left.plan_fingerprint
    right_fp = plan_right.plan_fingerprint
    if not left_fp or not right_fp or left_fp == right_fp:
        return None
    return OperatorDiff(
        operator_id_left=_root_operator_id(plan_left),
        operator_id_right=_root_operator_id(plan_right),
        diff_type="structure_mismatch",
        differences={
            "reason": (
                f"persisted plans depth-truncated at depth {min(cuts)} with "
                "differing full-tree fingerprints: the full plans differed "
                "below the truncation cut"
            ),
        },
    )


def _root_operator_id(plan: QueryPlanDAG) -> str:
    root = plan.logical_root
    return root.operator_id if root is not None else "missing"


class QueryPlanComparator:
    def compare_plans(
        self,
        plan_left: QueryPlanDAG,
        plan_right: QueryPlanDAG,
    ) -> PlanComparison:
        left_trusted = plan_left.is_fingerprint_trusted()
        right_trusted = plan_right.is_fingerprint_trusted()
        same_version = getattr(plan_left, "fingerprint_version", None) == getattr(
            plan_right, "fingerprint_version", None
        )

        fingerprints_match = False
        if left_trusted and right_trusted and same_version:
            fingerprints_match = (
                plan_left.plan_fingerprint == plan_right.plan_fingerprint
                if plan_left.plan_fingerprint and plan_right.plan_fingerprint
                else False
            )

            if fingerprints_match:
                return self._create_identical_comparison(plan_left, plan_right)
        else:
            if not same_version:
                logger.debug(
                    f"Plan {plan_left.query_id} fingerprint_version "
                    f"{getattr(plan_left, 'fingerprint_version', None)} != "
                    f"{getattr(plan_right, 'fingerprint_version', None)}; using full comparison"
                )
            if not left_trusted:
                logger.debug(
                    f"Plan {plan_left.query_id} has untrusted fingerprint "
                    f"(integrity={plan_left.fingerprint_integrity}), using full comparison"
                )
            if not right_trusted:
                logger.debug(
                    f"Plan {plan_right.query_id} has untrusted fingerprint "
                    f"(integrity={plan_right.fingerprint_integrity}), using full comparison"
                )

        operator_diffs = self._compare_operator_trees(
            plan_left.logical_root,
            plan_right.logical_root,
        )

        truncation_diff = _truncation_caveat(plan_left, plan_right)
        if truncation_diff is not None:
            operator_diffs.append(truncation_diff)

        similarity = self._calculate_similarity(
            plan_left.logical_root,
            plan_right.logical_root,
            operator_diffs,
        )

        summary = self._generate_summary(similarity, operator_diffs)

        return PlanComparison(
            plan_left=plan_left,
            plan_right=plan_right,
            plans_identical=False,
            fingerprints_match=fingerprints_match,
            similarity=similarity,
            operator_diffs=operator_diffs,
            summary=summary,
        )

    def _create_identical_comparison(
        self,
        plan_left: QueryPlanDAG,
        plan_right: QueryPlanDAG,
    ) -> PlanComparison:
        total_ops = self._count_operators(plan_left.logical_root)

        similarity = SimilarityScore(
            overall_similarity=1.0,
            structural_similarity=1.0,
            operator_similarity=1.0,
            property_similarity=1.0,
            total_operators_left=total_ops,
            total_operators_right=total_ops,
            matching_operators=total_ops,
            type_mismatches=0,
            property_mismatches=0,
            structure_mismatches=0,
        )

        return PlanComparison(
            plan_left=plan_left,
            plan_right=plan_right,
            plans_identical=True,
            fingerprints_match=True,
            similarity=similarity,
            operator_diffs=[],
            summary="Plans are identical (fingerprints match)",
        )

    def _compare_operator_trees(
        self,
        left: LogicalOperator,
        right: LogicalOperator,
    ) -> list[OperatorDiff]:
        diffs: list[OperatorDiff] = []

        queue: deque[tuple[LogicalOperator | None, LogicalOperator | None]] = deque([(left, right)])

        while queue:
            op_left, op_right = queue.popleft()

            if op_left is None and op_right is None:
                continue

            if op_left is None or op_right is None:
                diffs.append(
                    OperatorDiff(
                        operator_id_left=op_left.operator_id if op_left else "MISSING",
                        operator_id_right=op_right.operator_id if op_right else "MISSING",
                        diff_type="structure_mismatch",
                        differences={"reason": "One tree has operator, other doesn't"},
                    )
                )
                continue

            if not is_operator_type_match(op_left.operator_type, op_right.operator_type):
                diffs.append(
                    OperatorDiff(
                        operator_id_left=op_left.operator_id,
                        operator_id_right=op_right.operator_id,
                        diff_type="type_mismatch",
                        differences={
                            "left_type": get_operator_type_str(op_left.operator_type),
                            "right_type": get_operator_type_str(op_right.operator_type),
                        },
                    )
                )
            else:
                property_diffs = self._compare_operator_properties(op_left, op_right)
                if property_diffs:
                    diffs.append(
                        OperatorDiff(
                            operator_id_left=op_left.operator_id,
                            operator_id_right=op_right.operator_id,
                            diff_type="property_mismatch",
                            differences=property_diffs,
                        )
                    )
                else:
                    diffs.append(
                        OperatorDiff(
                            operator_id_left=op_left.operator_id,
                            operator_id_right=op_right.operator_id,
                            diff_type="match",
                            differences={},
                        )
                    )

            left_children = op_left.children or []
            right_children = op_right.children or []

            if len(left_children) != len(right_children):
                diffs.append(
                    OperatorDiff(
                        operator_id_left=op_left.operator_id,
                        operator_id_right=op_right.operator_id,
                        diff_type="structure_mismatch",
                        differences={
                            "left_children": len(left_children),
                            "right_children": len(right_children),
                        },
                    )
                )

            max_children = max(len(left_children), len(right_children))
            for i in range(max_children):
                left_child = left_children[i] if i < len(left_children) else None
                right_child = right_children[i] if i < len(right_children) else None
                queue.append((left_child, right_child))

        return diffs

    def _compare_operator_properties(
        self,
        left: LogicalOperator,
        right: LogicalOperator,
    ) -> dict[str, Any]:
        diffs: dict[str, Any] = {}

        op_type_str = get_operator_type_str(left.operator_type)
        comparator = _OPERATOR_COMPARATORS.get(op_type_str)
        if comparator:
            comparator(left, right, diffs)

        left_props = left.properties or {}
        right_props = right.properties or {}

        for key in set(left_props.keys()) | set(right_props.keys()):
            if left_props.get(key) != right_props.get(key):
                if "properties" not in diffs:
                    diffs["properties"] = {}
                diffs["properties"][key] = {
                    "left": left_props.get(key),
                    "right": right_props.get(key),
                }

        return diffs

    def _calculate_similarity(
        self,
        left: LogicalOperator,
        right: LogicalOperator,
        diffs: list[OperatorDiff],
    ) -> SimilarityScore:
        total_left = self._count_operators(left)
        total_right = self._count_operators(right)

        matches = sum(1 for d in diffs if d.diff_type == "match")
        type_mismatches = sum(1 for d in diffs if d.diff_type == "type_mismatch")
        property_mismatches = sum(1 for d in diffs if d.diff_type == "property_mismatch")
        structure_mismatches = sum(1 for d in diffs if d.diff_type == "structure_mismatch")

        total_comparisons = max(total_left, total_right)

        structural_similarity = 1.0 - (structure_mismatches / total_comparisons) if total_comparisons > 0 else 1.0

        operator_similarity = (matches + property_mismatches) / total_comparisons if total_comparisons > 0 else 1.0

        operators_with_same_type = matches + property_mismatches
        property_similarity = matches / operators_with_same_type if operators_with_same_type > 0 else 1.0

        overall_similarity = 0.4 * structural_similarity + 0.4 * operator_similarity + 0.2 * property_similarity

        return SimilarityScore(
            overall_similarity=overall_similarity,
            structural_similarity=structural_similarity,
            operator_similarity=operator_similarity,
            property_similarity=property_similarity,
            total_operators_left=total_left,
            total_operators_right=total_right,
            matching_operators=matches,
            type_mismatches=type_mismatches,
            property_mismatches=property_mismatches,
            structure_mismatches=structure_mismatches,
        )

    def _count_operators(self, operator: LogicalOperator) -> int:
        count = 1
        if operator.children:
            for child in operator.children:
                count += self._count_operators(child)
        return count

    def _generate_summary(
        self,
        similarity: SimilarityScore,
        diffs: list[OperatorDiff],
    ) -> str:
        if similarity.overall_similarity >= 0.95:
            level = "nearly identical"
        elif similarity.overall_similarity >= 0.75:
            level = "very similar"
        elif similarity.overall_similarity >= 0.50:
            level = "somewhat similar"
        else:
            level = "significantly different"

        summary_parts = [
            f"Plans are {level} ({similarity.overall_similarity:.1%} similarity)",
        ]

        if similarity.type_mismatches > 0:
            summary_parts.append(f"{similarity.type_mismatches} operator type mismatches")

        if similarity.property_mismatches > 0:
            summary_parts.append(f"{similarity.property_mismatches} property differences")

        if similarity.structure_mismatches > 0:
            summary_parts.append(f"{similarity.structure_mismatches} structural differences")

        return "; ".join(summary_parts)


def compare_query_plans(
    plan_left: QueryPlanDAG,
    plan_right: QueryPlanDAG,
) -> PlanComparison:
    comparator = QueryPlanComparator()
    return comparator.compare_plans(plan_left, plan_right)


@dataclass
class QueryPlanChange:
    query_id: str
    change_type: str
    similarity: float
    details: str


@dataclass
class PerformanceCorrelation:
    query_id: str
    plan_changed: bool
    baseline_time_ms: float
    current_time_ms: float
    perf_change_pct: float
    is_regression: bool


@dataclass
class PlanComparisonSummary:
    baseline_run_id: str
    current_run_id: str
    plans_compared: int
    plans_unchanged: int
    plans_changed: int
    structural_differences: list[QueryPlanChange]
    performance_correlations: list[PerformanceCorrelation]

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_run_id": self.baseline_run_id,
            "current_run_id": self.current_run_id,
            "plans_compared": self.plans_compared,
            "plans_unchanged": self.plans_unchanged,
            "plans_changed": self.plans_changed,
            "structural_differences": [
                {
                    "query_id": d.query_id,
                    "change_type": d.change_type,
                    "similarity": d.similarity,
                    "details": d.details,
                }
                for d in self.structural_differences
            ],
            "performance_correlations": [
                {
                    "query_id": c.query_id,
                    "plan_changed": c.plan_changed,
                    "baseline_time_ms": c.baseline_time_ms,
                    "current_time_ms": c.current_time_ms,
                    "perf_change_pct": c.perf_change_pct,
                    "is_regression": c.is_regression,
                }
                for c in self.performance_correlations
            ],
        }


def build_execution_map(results: Any) -> dict[str, Any]:
    execution_map: dict[str, Any] = {}
    for execution in iter_query_results(results):
        query_id = execution.get("query_id")
        if query_id is None:
            continue
        existing = execution_map.get(query_id)
        if existing is None or (existing.get("query_plan") is None and execution.get("query_plan") is not None):
            execution_map[query_id] = execution
    return execution_map


def generate_plan_comparison_summary(
    baseline_results: Any,
    current_results: Any,
    *,
    regression_threshold_pct: float = 20.0,
) -> PlanComparisonSummary:
    baseline_map = build_execution_map(baseline_results)
    current_map = build_execution_map(current_results)

    common_queries = set(baseline_map.keys()) & set(current_map.keys())

    plans_compared = 0
    plans_unchanged = 0
    plans_changed = 0
    structural_differences: list[QueryPlanChange] = []
    performance_correlations: list[PerformanceCorrelation] = []

    comparator = QueryPlanComparator()

    for query_id in sorted(common_queries):
        baseline_exec = baseline_map[query_id]
        current_exec = current_map[query_id]

        baseline_plan = baseline_exec.get("query_plan")
        current_plan = current_exec.get("query_plan")

        if not baseline_plan or not current_plan:
            continue

        plans_compared += 1

        baseline_fp = getattr(baseline_plan, "plan_fingerprint", None)
        current_fp = getattr(current_plan, "plan_fingerprint", None)
        same_version = getattr(baseline_plan, "fingerprint_version", None) == getattr(
            current_plan, "fingerprint_version", None
        )
        fingerprints_comparable = (
            bool(baseline_fp)
            and bool(current_fp)
            and same_version
            and baseline_plan.is_fingerprint_trusted()
            and current_plan.is_fingerprint_trusted()
        )

        plan_changed = not (fingerprints_comparable and baseline_fp == current_fp)

        if not plan_changed:
            plans_unchanged += 1
            change = QueryPlanChange(
                query_id=query_id,
                change_type="unchanged",
                similarity=1.0,
                details="Plans are identical",
            )
        else:
            comparison = comparator.compare_plans(baseline_plan, current_plan)

            if not fingerprints_comparable and (
                comparison.similarity.type_mismatches == 0
                and comparison.similarity.property_mismatches == 0
                and comparison.similarity.structure_mismatches == 0
            ):
                plan_changed = False

            if not plan_changed:
                plans_unchanged += 1
                change = QueryPlanChange(
                    query_id=query_id,
                    change_type="unchanged",
                    similarity=1.0,
                    details="Plans are identical",
                )
            else:
                plans_changed += 1

                if comparison.similarity.structure_mismatches > 0:
                    change_type = "structure_change"
                elif comparison.similarity.type_mismatches > 0:
                    change_type = "type_change"
                else:
                    change_type = "property_change"

                change = QueryPlanChange(
                    query_id=query_id,
                    change_type=change_type,
                    similarity=comparison.similarity.overall_similarity,
                    details=comparison.summary,
                )

        structural_differences.append(change)

        baseline_time = baseline_exec.get("execution_time_ms", 0.0) or 0.0
        current_time = current_exec.get("execution_time_ms", 0.0) or 0.0

        if baseline_time > 0:
            perf_change_pct = ((current_time - baseline_time) / baseline_time) * 100
        else:
            perf_change_pct = 0.0

        is_regression = plan_changed and perf_change_pct > regression_threshold_pct

        correlation = PerformanceCorrelation(
            query_id=query_id,
            plan_changed=plan_changed,
            baseline_time_ms=baseline_time,
            current_time_ms=current_time,
            perf_change_pct=perf_change_pct,
            is_regression=is_regression,
        )
        performance_correlations.append(correlation)

    return PlanComparisonSummary(
        baseline_run_id=getattr(baseline_results, "run_id", "unknown"),
        current_run_id=getattr(current_results, "run_id", "unknown"),
        plans_compared=plans_compared,
        plans_unchanged=plans_unchanged,
        plans_changed=plans_changed,
        structural_differences=structural_differences,
        performance_correlations=performance_correlations,
    )
