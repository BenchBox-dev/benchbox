from __future__ import annotations

import logging
import re
from typing import Any

from benchbox.core.query_plans.parsers.base import QueryPlanParser
from benchbox.core.results.query_plan_models import (
    LogicalOperator,
    LogicalOperatorType,
    QueryPlanDAG,
)

logger = logging.getLogger(__name__)


class SQLiteQueryPlanParser(QueryPlanParser):
    def __init__(self):
        super().__init__("sqlite")

    def _parse_impl(self, query_id: str, explain_output: str) -> QueryPlanDAG:
        if not explain_output or not explain_output.strip():
            raise ValueError("Empty EXPLAIN output")

        lines = explain_output.strip().split("\n")

        if lines and lines[0].strip() == "QUERY PLAN":
            lines = lines[1:]

        if not lines:
            raise ValueError("No plan lines found")

        operators = self._parse_plan_rows(lines)
        root = self._build_operator_tree(operators)

        return QueryPlanDAG(
            query_id=query_id,
            platform=self.platform_name,
            logical_root=root,
            estimated_cost=None,
            estimated_rows=None,
            raw_explain_output=explain_output,
        )

    def _parse_plan_rows(self, lines: list[str]) -> list[dict[str, Any]]:
        operators: list[dict[str, Any]] = []
        seen: set[int] = set()

        for line in lines:
            if not line.strip():
                continue

            parts = line.split("|", 2)
            if len(parts) != 3:
                raise ValueError(f"Malformed SQLite plan row (expected 'id|parent|detail'): {line!r}")
            try:
                node_id, parent_id = int(parts[0]), int(parts[1])
            except ValueError as exc:
                raise ValueError(f"Malformed SQLite plan row ids: {line!r}") from exc

            detail = parts[2].strip()
            if not detail:
                raise ValueError(f"SQLite plan row has no detail: {line!r}")
            if node_id <= 0 or node_id in seen:
                raise ValueError(f"Invalid or duplicate SQLite plan node id {node_id}")
            if parent_id != 0 and parent_id not in seen:
                raise ValueError(f"SQLite plan node {node_id} references unknown parent {parent_id}")
            seen.add(node_id)

            op_dict = self._parse_operator_line(detail, 0)
            if op_dict:
                op_dict["id"] = node_id
                op_dict["parent"] = parent_id
                operators.append(op_dict)

        return operators

    def _parse_operator_line(self, line: str, level: int) -> dict[str, Any] | None:
        line = line.strip()
        if not line:
            return None

        return {
            "text": line,
            "level": level,
            "type": self._infer_operator_type(line),
            "details": self._extract_details(line),
        }

    def _infer_operator_type(self, line: str) -> str:
        upper = line.upper()

        if "SCAN TABLE" in upper or "SCAN SUBQUERY" in upper or upper.startswith("SCAN "):
            return "SCAN"
        elif "SEARCH TABLE" in upper or upper.startswith("SEARCH "):
            return "INDEX_SCAN"
        elif "ORDER BY" in upper:
            return "SORT"
        elif "GROUP BY" in upper:
            return "AGGREGATE"
        elif "TEMP B-TREE" in upper and "JOIN" not in upper:
            if "ORDER BY" in upper:
                return "SORT"
            elif "GROUP BY" in upper:
                return "AGGREGATE"
            else:
                return "OTHER"
        elif "COMPOUND" in upper or "UNION" in upper:
            return "UNION"
        elif "SUBQUERY" in upper:
            return "SUBQUERY"
        else:
            return "OTHER"

    def _extract_details(self, line: str) -> dict[str, Any]:
        details: dict[str, Any] = {}

        match = re.search(r"(?:SCAN|SEARCH) (?:TABLE |SUBQUERY )?(\w+)", line, re.IGNORECASE)
        if match:
            details["table_name"] = match.group(1)

        match = re.search(r"USING INDEX (\w+)", line, re.IGNORECASE)
        if match:
            details["index_name"] = match.group(1)

        if "LEFT" in line.upper():
            details["join_type"] = "left"
        elif "RIGHT" in line.upper():
            details["join_type"] = "right"
        elif "OUTER" in line.upper():
            details["join_type"] = "full"
        elif "JOIN" in line.upper():
            details["join_type"] = "inner"

        return details

    def _build_operator_tree(self, operators: list[dict[str, Any]]) -> LogicalOperator:
        if not operators:
            raise ValueError("No operators to build tree from")

        by_parent: dict[int, list[dict[str, Any]]] = {}
        for op_dict in operators:
            by_parent.setdefault(op_dict["parent"], []).append(op_dict)

        def build(op_dict: dict[str, Any], depth: int) -> LogicalOperator:
            op_dict["level"] = depth
            children = [build(child, depth + 1) for child in by_parent.get(op_dict["id"], [])]
            return self._convert_to_logical_operator(op_dict, children)

        roots = [build(op_dict, 0) for op_dict in by_parent.get(0, [])]
        if not roots:
            raise ValueError("SQLite plan has no root operator")
        if len(roots) == 1:
            return roots[0]

        return self._create_logical_operator(
            operator_type=LogicalOperatorType.OTHER,
            children=roots,
            physical_operator=self._create_physical_operator(
                "OTHER",
                properties={},
                platform_metadata={"text": "QUERY PLAN", "level": -1},
            ),
        )

    def _convert_to_logical_operator(
        self,
        op_dict: dict[str, Any],
        children: list[LogicalOperator],
    ) -> LogicalOperator:
        op_type_str = op_dict["type"]
        details = op_dict["details"]
        text = op_dict["text"]

        logical_type = self._map_sqlite_operator(op_type_str)

        kwargs: dict[str, Any] = {}

        if logical_type == LogicalOperatorType.SCAN:
            table_name = details.get("table_name")
            if table_name:
                kwargs["table_name"] = table_name

        elif logical_type == LogicalOperatorType.SORT or logical_type == LogicalOperatorType.AGGREGATE:
            kwargs["properties"] = {"temp_btree": "TEMP B-TREE" in text.upper()}

        physical_op = self._create_physical_operator(
            op_type_str,
            properties=details.copy(),
            platform_metadata={"text": text, "level": op_dict["level"]},
        )

        return self._create_logical_operator(
            operator_type=logical_type,
            children=children,
            physical_operator=physical_op,
            **kwargs,
        )

    def _map_sqlite_operator(self, sqlite_op: str) -> LogicalOperatorType:
        normalized = sqlite_op.upper()

        if normalized == "SCAN" or normalized == "INDEX_SCAN":
            return LogicalOperatorType.SCAN
        elif normalized == "SORT":
            return LogicalOperatorType.SORT
        elif normalized == "AGGREGATE":
            return LogicalOperatorType.AGGREGATE
        elif normalized == "UNION":
            return LogicalOperatorType.UNION
        elif normalized == "SUBQUERY":
            return LogicalOperatorType.SUBQUERY
        else:
            return LogicalOperatorType.OTHER

    def _create_fallback_operator(self) -> LogicalOperator:
        return self._create_logical_operator(
            operator_type=LogicalOperatorType.OTHER,
            properties={"note": "Fallback operator due to parsing failure"},
        )
