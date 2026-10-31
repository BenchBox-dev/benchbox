from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from benchbox.core.query_plans.comparison import OperatorDiff, PlanComparison
from benchbox.core.results.query_plan_models import (
    LogicalOperator,
    QueryPlanDAG,
    get_join_type_str,
    get_operator_type_str,
)


@dataclass
class VisualizationOptions:
    show_properties: bool = True
    show_physical: bool = False
    show_costs: bool = True
    max_depth: int | None = None
    compact: bool = False


def _format_truncated_list(label: str, items: list[str], max_display: int = 3) -> list[str]:
    lines = [f"{label}: {len(items)}"]
    for item in items[:max_display]:
        lines.append(f"  - {item}")
    if len(items) > max_display:
        lines.append(f"  ... and {len(items) - max_display} more")
    return lines


_MAX_JOIN_CONDITIONS = 5
_MAX_CONDITION_CHARS = 120


def _unwrap_join_conditions(conditions: list[str]) -> list[str]:
    unwrapped: list[str] = []
    for condition in conditions:
        text = str(condition)
        if text.startswith("{"):
            try:
                payload = json.loads(text)
            except ValueError:
                payload = None
            inner = payload.get("Conditions") if isinstance(payload, dict) else None
            if isinstance(inner, str):
                unwrapped.append(inner)
                continue
            if isinstance(inner, list) and inner:
                unwrapped.extend(str(item) for item in inner)
                continue
        unwrapped.append(text)
    return unwrapped


def _format_join_conditions(conditions: list[str]) -> list[str]:
    conditions = _unwrap_join_conditions(conditions)
    lines = [f"Join Conditions: {len(conditions)}"]
    for condition in conditions[:_MAX_JOIN_CONDITIONS]:
        text = " ".join(str(condition).split())
        if len(text) > _MAX_CONDITION_CHARS:
            text = text[: _MAX_CONDITION_CHARS - 3] + "..."
        lines.append(f"  - {text}")
    if len(conditions) > _MAX_JOIN_CONDITIONS:
        lines.append(f"  +{len(conditions) - _MAX_JOIN_CONDITIONS} more")
    return lines


def _fmt_left_right(label: str, d: dict) -> str:
    return f"{label}: {d['left']} ≠ {d['right']}"


def _fmt_left_right_list(label: str, d: dict) -> str:
    left = d["left"] or []
    right = d["right"] or []
    return f"{label}: [{', '.join(left)}] ≠ [{', '.join(right)}]"


def _fmt_set_diff(label: str, item_noun: str, d: dict) -> str:
    parts = []
    left_only = d.get("left_only", [])
    right_only = d.get("right_only", [])
    if left_only:
        parts.append(f"{len(left_only)} {item_noun} only in left")
    if right_only:
        parts.append(f"{len(right_only)} {item_noun} only in right")
    return f"{label}: {', '.join(parts)}"


def _fmt_projections(d: dict) -> str:
    left = d["left"] or []
    right = d["right"] or []
    l_str = f"{', '.join(left[:3])}{'...' if len(left) > 3 else ''}"
    r_str = f"{', '.join(right[:3])}{'...' if len(right) > 3 else ''}"
    return f"Projections: [{l_str}] ≠ [{r_str}]"


_PROPERTY_DIFF_FORMATTERS: dict[str, Any] = {
    "table_name": lambda d: _fmt_left_right("Table", d),
    "join_type": lambda d: _fmt_left_right("Join type", d),
    "filter_expressions": lambda d: _fmt_set_diff("Filters", "filters", d),
    "aggregation_functions": lambda d: _fmt_set_diff("Aggregations", "aggs", d),
    "group_by_keys": lambda d: _fmt_left_right_list("Group by", d),
    "limit_count": lambda d: _fmt_left_right("Limit", d),
    "offset_count": lambda d: _fmt_left_right("Offset", d),
    "projection_expressions": _fmt_projections,
    "properties": lambda _d: "Property differences in operator details",
}


class QueryPlanVisualizer:
    def __init__(self, options: VisualizationOptions | None = None):
        self.options = options or VisualizationOptions()

    def render_plan(self, plan: QueryPlanDAG) -> str:
        lines = []

        lines.append(f"Query Plan: {plan.query_id}")
        lines.append(f"Platform: {plan.platform}")

        if self.options.show_costs and (plan.estimated_cost or plan.estimated_rows):
            cost_str = f"Cost: {plan.estimated_cost:.2f}" if plan.estimated_cost else ""
            rows_str = f"Rows: {plan.estimated_rows:,}" if plan.estimated_rows else ""
            info = " | ".join(filter(None, [cost_str, rows_str]))
            if info:
                lines.append(info)

        if plan.plan_fingerprint:
            lines.append(f"Fingerprint: {plan.plan_fingerprint[:16]}...")

        lines.append("")

        tree_lines = self._render_operator_tree(plan.logical_root, prefix="", is_last=True)
        lines.extend(tree_lines)

        return "\n".join(lines)

    def render_summary(self, plan: QueryPlanDAG) -> str:
        operator_counts: dict[str, int] = {}
        max_depth = 0

        def count_operators(op: LogicalOperator, depth: int = 0) -> None:
            nonlocal max_depth
            max_depth = max(max_depth, depth)

            op_type_str = get_operator_type_str(op.operator_type)
            operator_counts[op_type_str] = operator_counts.get(op_type_str, 0) + 1

            if op.children:
                for child in op.children:
                    count_operators(child, depth + 1)

        count_operators(plan.logical_root)

        lines = []
        lines.append(f"Query: {plan.query_id} ({plan.platform})")
        lines.append(f"Total Operators: {sum(operator_counts.values())}")
        lines.append(f"Max Depth: {max_depth}")

        if plan.estimated_cost:
            lines.append(f"Estimated Cost: {plan.estimated_cost:.2f}")
        if plan.estimated_rows:
            lines.append(f"Estimated Rows: {plan.estimated_rows:,}")

        lines.append("\nOperator Breakdown:")
        for op_type, count in sorted(operator_counts.items(), key=lambda x: -x[1]):
            lines.append(f"  {op_type}: {count}")

        return "\n".join(lines)

    def render_comparison(self, comparison: PlanComparison) -> str:
        lines = []

        lines.append("=" * 80)
        lines.append("QUERY PLAN COMPARISON")
        lines.append("=" * 80)
        lines.append("")

        lines.append(f"Left:  {comparison.plan_left.query_id} ({comparison.plan_left.platform})")
        lines.append(f"Right: {comparison.plan_right.query_id} ({comparison.plan_right.platform})")
        lines.append("")

        lines.append(comparison.summary)
        lines.append("")
        self._render_similarity_metrics(comparison.similarity, lines)
        self._render_operator_counts(comparison.similarity, lines)

        if comparison.operator_diffs:
            self._render_operator_diffs(comparison.operator_diffs, lines)

        lines.append("=" * 80)

        return "\n".join(lines)

    def _render_similarity_metrics(self, sim: Any, lines: list[str]) -> None:
        lines.append("Similarity Metrics:")
        lines.append(f"  Overall:    {sim.overall_similarity:6.1%}")
        lines.append(f"  Structural: {sim.structural_similarity:6.1%}")
        lines.append(f"  Operator:   {sim.operator_similarity:6.1%}")
        lines.append(f"  Property:   {sim.property_similarity:6.1%}")
        lines.append("")

    def _render_operator_counts(self, sim: Any, lines: list[str]) -> None:
        lines.append(f"Operators: {sim.total_operators_left} (left) vs {sim.total_operators_right} (right)")
        lines.append(f"  Matching:   {sim.matching_operators}")
        if sim.type_mismatches > 0:
            lines.append(f"  Type mismatches:     {sim.type_mismatches}")
        if sim.property_mismatches > 0:
            lines.append(f"  Property mismatches: {sim.property_mismatches}")
        if sim.structure_mismatches > 0:
            lines.append(f"  Structure mismatches: {sim.structure_mismatches}")
        lines.append("")

    def _render_operator_diffs(self, operator_diffs: list[OperatorDiff], lines: list[str]) -> None:
        type_diffs = [d for d in operator_diffs if d.diff_type == "type_mismatch"]
        prop_diffs = [d for d in operator_diffs if d.diff_type == "property_mismatch"]
        struct_diffs = [d for d in operator_diffs if d.diff_type == "structure_mismatch"]

        if type_diffs:
            lines.append(f"Type Mismatches ({len(type_diffs)}):")
            for diff in type_diffs[:10]:
                left_type = diff.differences.get("left_type", "?")
                right_type = diff.differences.get("right_type", "?")
                lines.append(f"  • {left_type} ≠ {right_type}")
            if len(type_diffs) > 10:
                lines.append(f"  ... and {len(type_diffs) - 10} more")
            lines.append("")

        if prop_diffs:
            lines.append(f"Property Differences ({len(prop_diffs)}):")
            for diff in prop_diffs[:10]:
                details = self._format_property_diff(diff)
                if details:
                    lines.append(f"  • {details}")
            if len(prop_diffs) > 10:
                lines.append(f"  ... and {len(prop_diffs) - 10} more")
            lines.append("")

        if struct_diffs:
            lines.append(f"Structure Mismatches ({len(struct_diffs)}):")
            for diff in struct_diffs[:10]:
                reason = diff.differences.get("reason", "unknown")
                lines.append(f"  • {reason}")
            if len(struct_diffs) > 10:
                lines.append(f"  ... and {len(struct_diffs) - 10} more")
            lines.append("")

    def _render_operator_tree(
        self,
        operator: LogicalOperator,
        prefix: str = "",
        is_last: bool = True,
        depth: int = 0,
    ) -> list[str]:
        if self.options.max_depth and depth >= self.options.max_depth:
            return [f"{prefix}..."]

        lines = []

        connector = "└── " if is_last else "├── "
        line = f"{prefix}{connector}{self._format_operator(operator)}"
        lines.append(line)

        if self.options.show_properties and not self.options.compact:
            extension = "    " if is_last else "│   "
            props = self._format_operator_properties(operator)
            for prop in props:
                lines.append(f"{prefix}{extension}  {prop}")

        if operator.children:
            extension = "    " if is_last else "│   "
            for i, child in enumerate(operator.children):
                child_is_last = i == len(operator.children) - 1
                child_lines = self._render_operator_tree(
                    child,
                    prefix=f"{prefix}{extension}",
                    is_last=child_is_last,
                    depth=depth + 1,
                )
                lines.extend(child_lines)

        return lines

    def _format_operator(self, operator: LogicalOperator) -> str:
        op_type = get_operator_type_str(operator.operator_type)

        details = []

        if operator.table_name:
            details.append(f"table={operator.table_name}")

        if operator.join_type:
            details.append(f"type={get_join_type_str(operator.join_type)}")

        if operator.filter_expressions and len(operator.filter_expressions) == 1:
            expr = operator.filter_expressions[0]
            if len(expr) < 40:
                details.append(f"filter='{expr}'")

        if operator.aggregation_functions and len(operator.aggregation_functions) <= 2:
            aggs = ", ".join(operator.aggregation_functions)
            if len(aggs) < 40:
                details.append(f"aggs=[{aggs}]")

        detail_str = f" ({', '.join(details)})" if details else ""
        return f"{op_type}{detail_str}"

    def _format_operator_properties(self, operator: LogicalOperator) -> list[str]:
        props: list[str] = []

        if operator.join_conditions:
            props.extend(_format_join_conditions(operator.join_conditions))
        if operator.filter_expressions and len(operator.filter_expressions) > 1:
            props.extend(_format_truncated_list("Filters", operator.filter_expressions))
        if operator.aggregation_functions and len(operator.aggregation_functions) > 2:
            props.extend(_format_truncated_list("Aggregations", operator.aggregation_functions))
        if operator.sort_keys:
            props.extend(_format_truncated_list("Sort Keys", operator.sort_keys))

        if self.options.show_physical and operator.physical_operator:
            props.extend(self._format_physical_properties(operator.physical_operator))

        return props

    def _format_physical_properties(self, phys: Any) -> list[str]:
        lines = [f"Physical: {phys.operator_type}"]
        props = phys.properties if phys.properties is not None else {}
        if self.options.show_costs:
            if props.get("estimated_cost"):
                lines.append(f"  Cost: {props['estimated_cost']}")
            if props.get("estimated_rows"):
                lines.append(f"  Rows: {props['estimated_rows']:,}")
        return lines

    def _format_property_diff(self, diff: OperatorDiff) -> str:
        diffs = diff.differences
        for key, formatter in _PROPERTY_DIFF_FORMATTERS.items():
            if key in diffs:
                return formatter(diffs[key])
        return "Property mismatch"


def render_plan(plan: QueryPlanDAG, options: VisualizationOptions | None = None) -> str:
    visualizer = QueryPlanVisualizer(options)
    return visualizer.render_plan(plan)


def render_summary(plan: QueryPlanDAG) -> str:
    visualizer = QueryPlanVisualizer()
    return visualizer.render_summary(plan)


def render_comparison(comparison: PlanComparison) -> str:
    visualizer = QueryPlanVisualizer()
    return visualizer.render_comparison(comparison)
