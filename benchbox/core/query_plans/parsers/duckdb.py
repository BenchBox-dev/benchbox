from __future__ import annotations

import json
import logging
import re
from typing import Any

from benchbox.core.query_plans.parsers.base import (
    QueryPlanParser,
    strip_estimate_keys,
    strip_estimates,
)
from benchbox.core.results.query_plan_models import (
    JoinType,
    LogicalOperator,
    LogicalOperatorType,
    QueryPlanDAG,
)

logger = logging.getLogger(__name__)

_DECLARED_JOIN_TYPES: dict[str, JoinType] = {
    "INNER": JoinType.INNER,
    "LEFT": JoinType.LEFT,
    "RIGHT": JoinType.RIGHT,
    "OUTER": JoinType.FULL,
    "FULL": JoinType.FULL,
    "SEMI": JoinType.SEMI,
    "RIGHT_SEMI": JoinType.SEMI,
    "ANTI": JoinType.ANTI,
    "RIGHT_ANTI": JoinType.ANTI,
    "MARK": JoinType.MARK,
    "SINGLE": JoinType.SINGLE,
}
_SORT_KEY_RE = re.compile(r"^(?P<expr>.*?)\s+(?P<direction>ASC|DESC)(?:\s+NULLS\s+(?:FIRST|LAST))?$", re.DOTALL)


class DuckDBQueryPlanParser(QueryPlanParser):
    def __init__(self):
        super().__init__("duckdb")

    def _parse_impl(self, query_id: str, explain_output: str) -> QueryPlanDAG:
        if not explain_output or not explain_output.strip():
            raise ValueError("Empty EXPLAIN output")

        stripped = explain_output.strip()

        if self._is_json_format(stripped):
            try:
                return self._parse_json_format(query_id, explain_output)
            except Exception as e:
                logger.warning(
                    "JSON format parse failed for %s: %s, falling back to text parser",
                    query_id,
                    e,
                )

        return self._parse_text_format(query_id, explain_output)

    def _is_json_format(self, explain_output: str) -> bool:
        stripped = explain_output.strip()
        return stripped.startswith("{") or stripped.startswith("[")

    def _parse_json_format(self, query_id: str, explain_output: str) -> QueryPlanDAG:
        try:
            data = json.loads(explain_output)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON format: {e}") from e

        if isinstance(data, list):
            if not data:
                raise ValueError("Empty JSON array")
            data = data[0] if len(data) == 1 else {"children": data}

        root_node = self._find_plan_root_in_json(data)
        if not root_node:
            raise ValueError("Could not find plan root in JSON structure")

        logical_root = self._parse_json_node(root_node)

        estimated_cost = None
        estimated_rows = None
        if "timing" in data:
            estimated_cost = data["timing"]
        elif data.get("latency"):
            estimated_cost = data["latency"]
        if "cardinality" in data:
            try:
                estimated_rows = int(data["cardinality"])
            except (ValueError, TypeError):
                pass
        elif "rows_returned" in data:
            try:
                estimated_rows = int(data["rows_returned"])
            except (ValueError, TypeError):
                pass

        return QueryPlanDAG(
            query_id=query_id,
            platform=self.platform_name,
            logical_root=logical_root,
            estimated_cost=estimated_cost,
            estimated_rows=estimated_rows,
            raw_explain_output=explain_output,
        )

    def _find_plan_root_in_json(self, data: dict[str, Any]) -> dict[str, Any] | None:
        wrapper_names = ("QUERY_PLAN", "RESULT", "RESULT_COLLECTOR", "EXPLAIN", "QUERY", "EXPLAIN_ANALYZE")

        if "operator" in data and data["operator"]:
            return self._find_plan_root_in_json(data["operator"][0])

        node_name = data.get("name") or data.get("operator_name") or data.get("operator_type") or data.get("type", "")

        if node_name:
            name = node_name.upper().strip()
            if name not in wrapper_names:
                return data
            if "children" in data and data["children"]:
                return self._find_plan_root_in_json(data["children"][0])

        if "children" in data and data["children"]:
            return self._find_plan_root_in_json(data["children"][0])

        return None

    def _parse_json_node(self, node: dict[str, Any]) -> LogicalOperator:
        raw_name = (
            node.get("operator_name") or node.get("name") or node.get("operator_type") or node.get("type", "UNKNOWN")
        )
        operator_name = raw_name.strip()
        operator_type = self._harmonize_duckdb_operator(operator_name)

        children = []
        for child_node in node.get("children", []):
            children.append(self._parse_json_node(child_node))

        kwargs: dict[str, Any] = {}
        raw_extra = node.get("extra_info", "")
        if isinstance(raw_extra, dict):
            extra_info = json.dumps(raw_extra)
            stripped_extra = strip_estimate_keys(raw_extra)
            logical_extra = json.dumps(stripped_extra) if stripped_extra else ""
        else:
            extra_info = raw_extra
            logical_extra = strip_estimates(raw_extra)

        if operator_type == LogicalOperatorType.SCAN:
            table_name = self._extract_table_from_extra_info(extra_info)
            if table_name:
                kwargs["table_name"] = table_name

        elif operator_type == LogicalOperatorType.FILTER:
            if logical_extra:
                kwargs["filter_expressions"] = [logical_extra]

        elif operator_type == LogicalOperatorType.JOIN:
            kwargs["join_type"] = self._declared_join_type(raw_extra) or self._extract_join_type_from_operator(
                operator_name
            )
            if logical_extra:
                kwargs["join_conditions"] = [logical_extra]

        elif operator_type == LogicalOperatorType.AGGREGATE:
            if logical_extra:
                kwargs["aggregation_functions"] = [logical_extra]

        elif operator_type == LogicalOperatorType.SORT:
            sort_keys = self._declared_sort_keys(raw_extra)
            if sort_keys:
                kwargs["sort_keys"] = sort_keys
                limit_count = self._declared_top(raw_extra)
                if limit_count is not None:
                    kwargs["limit_count"] = limit_count
            elif logical_extra:
                kwargs["sort_keys"] = [{"expr": logical_extra, "direction": "ASC"}]

        _timing = node.get("timing")
        _cardinality = node.get("cardinality")
        if _cardinality is None:
            _cardinality = node.get("operator_cardinality")
        if _cardinality is None:
            _cardinality = node.get("intermediate_rows")
        physical_op = self._create_physical_operator(
            operator_name,
            properties={
                "timing": _timing if _timing is not None else node.get("operator_timing"),
                "cardinality": _cardinality,
            },
            platform_metadata={"extra_info": extra_info} if extra_info else {},
        )

        return self._create_logical_operator(
            operator_type=operator_type,
            children=children,
            physical_operator=physical_op,
            **kwargs,
        )

    @staticmethod
    def _declared_join_type(raw_extra: Any) -> JoinType | str | None:
        if not isinstance(raw_extra, dict):
            return None
        declared = raw_extra.get("Join Type")
        if not isinstance(declared, str) or not declared.strip():
            return None
        normalized = declared.strip().upper().replace(" ", "_")
        return _DECLARED_JOIN_TYPES.get(normalized, normalized.lower())

    @staticmethod
    def _declared_sort_keys(raw_extra: Any) -> list[dict[str, str]]:
        if not isinstance(raw_extra, dict):
            return []
        declared = raw_extra.get("Order By")
        entries = [declared] if isinstance(declared, str) else declared
        if not isinstance(entries, list):
            return []
        sort_keys = []
        for entry in entries:
            if not isinstance(entry, str) or not entry.strip():
                continue
            match = _SORT_KEY_RE.match(entry.strip())
            if match:
                sort_keys.append({"expr": match.group("expr").strip(), "direction": match.group("direction")})
            else:
                sort_keys.append({"expr": entry.strip(), "direction": "ASC"})
        return sort_keys

    @staticmethod
    def _declared_top(raw_extra: Any) -> int | None:
        declared = raw_extra.get("Top") if isinstance(raw_extra, dict) else None
        try:
            return int(declared) if declared is not None else None
        except (TypeError, ValueError):
            return None

    def _extract_table_from_extra_info(self, extra_info: str) -> str | None:
        if not extra_info:
            return None
        if extra_info.startswith("{"):
            try:
                parsed = json.loads(extra_info)
                if isinstance(parsed, dict) and "Table" in parsed:
                    return str(parsed["Table"])
            except (json.JSONDecodeError, TypeError):
                pass
        lines = extra_info.strip().split("\n")
        for line in lines:
            line = line.strip()
            if line and re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", line):
                return line
        return None

    def _parse_text_format(self, query_id: str, explain_output: str) -> QueryPlanDAG:
        branching_detected = self._detect_branching_structure(explain_output)
        if branching_detected:
            raise ValueError(
                f"DuckDB text plan for '{query_id}' contains branching structure (joins/unions) "
                "that cannot be accurately parsed from box-drawing format. The text parser "
                "would flatten the tree into a linear chain, producing incorrect fingerprints "
                "and misleading comparison results. "
                "Use EXPLAIN (FORMAT JSON) for accurate plan capture. "
                "See: https://duckdb.org/docs/sql/query_syntax/explain "
                f"Branching indicators found: {branching_detected}"
            )

        operators = self._parse_text_operators(explain_output)

        if not operators:
            raise ValueError("No operators found in EXPLAIN output")

        root = self._build_operator_tree(operators)

        logger.warning(
            "Query %s: Parsed from text format (limited fidelity). Use EXPLAIN (FORMAT JSON) for full plan structure.",
            query_id,
        )

        estimated_cost, estimated_rows = self._extract_estimates_from_output(explain_output)

        return QueryPlanDAG(
            query_id=query_id,
            platform=self.platform_name,
            logical_root=root,
            estimated_cost=estimated_cost,
            estimated_rows=estimated_rows,
            raw_explain_output=explain_output,
        )

    def _detect_branching_structure(self, explain_output: str) -> str | None:
        indicators = []

        join_patterns = ["HASH_JOIN", "NESTED_LOOP_JOIN", "PIECEWISE_MERGE_JOIN", "MERGE_JOIN", "CROSS_JOIN", "JOIN"]
        for pattern in join_patterns:
            if pattern in explain_output.upper():
                indicators.append(f"JOIN operator: {pattern}")

        set_patterns = ["UNION", "INTERSECT", "EXCEPT"]
        for pattern in set_patterns:
            if pattern in explain_output.upper():
                indicators.append(f"SET operator: {pattern}")

        if "┬" in explain_output:
            fork_count = explain_output.count("┬")
            if fork_count > 0:
                pass

        lines = explain_output.split("\n")
        box_starts_by_line = []
        for i, line in enumerate(lines):
            if "┌" in line:
                starts = line.count("┌")
                if starts > 1:
                    indicators.append(f"Multiple boxes on line {i + 1} (parallel branches)")
                box_starts_by_line.append((i, starts))

        if indicators:
            return "; ".join(indicators)
        return None

    def _parse_text_operators(self, explain_output: str) -> list[dict[str, Any]]:
        operators = []
        lines = explain_output.strip().split("\n")

        current_operator = None
        collecting_details = False

        for line in lines:
            if "┌" in line and "┐" in line:
                collecting_details = False
                current_operator = None
            elif "│" in line and not collecting_details:
                content = line.strip("│ \t")
                content = content.strip()

                if content and content.replace("_", "").replace(" ", "").isalnum():
                    if content.isupper() or any(
                        op in content.upper()
                        for op in [
                            "SCAN",
                            "JOIN",
                            "FILTER",
                            "PROJECTION",
                            "AGGREGATE",
                            "GROUP",
                            "SORT",
                            "ORDER",
                            "LIMIT",
                            "HASH",
                            "NESTED",
                            "MERGE",
                        ]
                    ):
                        current_operator = {
                            "operator_type": content,
                            "details": [],
                            "properties": {},
                        }
                        operators.append(current_operator)
                        collecting_details = True
            elif "│" in line and collecting_details and current_operator:
                content = line.strip("│ \t")
                content = content.strip()

                if content and "─" not in content:
                    current_operator["details"].append(content)

        return operators

    def _build_operator_tree(self, operators: list[dict[str, Any]]) -> LogicalOperator:
        if not operators:
            raise ValueError("No operators to build tree from")

        logical_operators = []

        for op_dict in reversed(operators):
            logical_op = self._convert_to_logical_operator(op_dict, logical_operators[-1:] if logical_operators else [])
            logical_operators.append(logical_op)

        return logical_operators[-1] if logical_operators else self._create_fallback_operator()

    def _convert_to_logical_operator(
        self,
        op_dict: dict[str, Any],
        children: list[LogicalOperator],
    ) -> LogicalOperator:
        operator_type_str = op_dict["operator_type"]
        details = op_dict["details"]

        logical_type = self._harmonize_duckdb_operator(operator_type_str)

        kwargs: dict[str, Any] = {}

        if logical_type == LogicalOperatorType.SCAN:
            table_name = self._extract_table_name_from_details(details)
            if table_name:
                kwargs["table_name"] = table_name

        elif logical_type == LogicalOperatorType.FILTER:
            raw_exprs = [d for d in details if d and not d.startswith("Filters:")]
            filter_exprs = self._join_wrapped_expressions(raw_exprs)
            if filter_exprs:
                kwargs["filter_expressions"] = filter_exprs

        elif logical_type == LogicalOperatorType.AGGREGATE:
            agg_funcs = [
                d for d in details if any(func in d.lower() for func in ["sum(", "count(", "avg(", "min(", "max("])
            ]
            if agg_funcs:
                kwargs["aggregation_functions"] = agg_funcs

        elif logical_type == LogicalOperatorType.SORT:
            sort_keys = []
            for detail in details:
                if "ASC" in detail or "DESC" in detail:
                    direction = "ASC" if "ASC" in detail else "DESC"
                    expr = (
                        detail.replace("ASC", "")
                        .replace("DESC", "")
                        .replace("NULLS LAST", "")
                        .replace("NULLS FIRST", "")
                        .strip()
                    )
                    sort_keys.append({"expr": expr, "direction": direction})
            if sort_keys:
                kwargs["sort_keys"] = sort_keys

        elif logical_type == LogicalOperatorType.JOIN:
            join_type = self._extract_join_type_from_operator(operator_type_str)
            kwargs["join_type"] = join_type

            join_conds = [d for d in details if "=" in d or "ON" in d]
            if join_conds:
                kwargs["join_conditions"] = join_conds

        physical_op = self._create_physical_operator(
            operator_type_str,
            properties={},
            platform_metadata={"details": details},
        )

        return self._create_logical_operator(
            operator_type=logical_type,
            children=children,
            physical_operator=physical_op,
            **kwargs,
        )

    @staticmethod
    def _join_wrapped_expressions(fragments: list[str]) -> list[str]:
        joined: list[str] = []
        buffer = ""
        depth = 0
        for fragment in fragments:
            buffer += fragment
            depth += fragment.count("(") - fragment.count(")")
            if depth <= 0:
                joined.append(buffer)
                buffer = ""
                depth = 0
        if buffer:
            joined.append(buffer)
        return joined

    def _harmonize_duckdb_operator(self, duckdb_operator: str) -> LogicalOperatorType:
        normalized = duckdb_operator.upper().strip()

        if normalized in ["SEQ_SCAN", "INDEX_SCAN", "TABLE_SCAN"]:
            return LogicalOperatorType.SCAN

        if normalized == "FILTER":
            return LogicalOperatorType.FILTER

        if any(
            join_type in normalized for join_type in ["HASH_JOIN", "NESTED_LOOP_JOIN", "PIECEWISE_MERGE_JOIN", "JOIN"]
        ):
            return LogicalOperatorType.JOIN

        if normalized in ["HASH_GROUP_BY", "PERFECT_HASH_GROUP_BY", "AGGREGATE"]:
            return LogicalOperatorType.AGGREGATE

        if normalized in ["ORDER_BY", "TOP_N", "SORT"]:
            return LogicalOperatorType.SORT

        if normalized == "LIMIT":
            return LogicalOperatorType.LIMIT

        if normalized in ["PROJECTION", "RESULT_COLLECTOR"]:
            return LogicalOperatorType.PROJECT

        if "UNION" in normalized:
            return LogicalOperatorType.UNION
        if "INTERSECT" in normalized:
            return LogicalOperatorType.INTERSECT
        if "EXCEPT" in normalized:
            return LogicalOperatorType.EXCEPT

        if "WINDOW" in normalized:
            return LogicalOperatorType.WINDOW

        if "CTE" in normalized or "MATERIALIZED" in normalized:
            return LogicalOperatorType.CTE

        return self._harmonize_operator_type(duckdb_operator)

    def _extract_table_name_from_details(self, details: list[str]) -> str | None:
        for detail in details:
            if detail and re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", detail):
                return detail
        return None

    def _extract_join_type_from_operator(self, operator_type: str) -> str:
        normalized = operator_type.upper()

        if "INNER" in normalized:
            return "inner"
        elif "LEFT" in normalized:
            return "left"
        elif "RIGHT" in normalized:
            return "right"
        elif "FULL" in normalized:
            return "full"
        elif "CROSS" in normalized:
            return "cross"
        else:
            return "inner"

    def _extract_estimates_from_output(self, explain_output: str) -> tuple[float | None, int | None]:
        return None, None

    def _create_fallback_operator(self) -> LogicalOperator:
        return self._create_logical_operator(
            operator_type=LogicalOperatorType.OTHER,
            properties={"note": "Fallback operator due to parsing failure"},
        )
