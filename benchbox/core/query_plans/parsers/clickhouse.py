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


class ClickHouseQueryPlanParser(QueryPlanParser):
    _OPERATOR_KEYWORDS: tuple[tuple[str, LogicalOperatorType], ...] = (
        ("readfrommergetree", LogicalOperatorType.SCAN),
        ("readfrommemorystorage", LogicalOperatorType.SCAN),
        ("readfrompreparedsource", LogicalOperatorType.SCAN),
        ("readfromremote", LogicalOperatorType.SCAN),
        ("readfromstorage", LogicalOperatorType.SCAN),
        ("readfrom", LogicalOperatorType.SCAN),
        ("mergetreethread", LogicalOperatorType.SCAN),
        ("mergingaggregated", LogicalOperatorType.AGGREGATE),
        ("aggregating", LogicalOperatorType.AGGREGATE),
        ("aggregat", LogicalOperatorType.AGGREGATE),
        ("arrayjoin", LogicalOperatorType.OTHER),
        ("join", LogicalOperatorType.JOIN),
        ("filter", LogicalOperatorType.FILTER),
        ("mergingsorted", LogicalOperatorType.SORT),
        ("mergesorting", LogicalOperatorType.SORT),
        ("partialsorting", LogicalOperatorType.SORT),
        ("finishsorting", LogicalOperatorType.SORT),
        ("sorting", LogicalOperatorType.SORT),
        ("limitby", LogicalOperatorType.LIMIT),
        ("limit", LogicalOperatorType.LIMIT),
        ("offset", LogicalOperatorType.LIMIT),
        ("window", LogicalOperatorType.WINDOW),
        ("union", LogicalOperatorType.UNION),
        ("distinct", LogicalOperatorType.OTHER),
        ("expression", LogicalOperatorType.PROJECT),
    )

    _NODE_PATTERN = re.compile(r"^(?P<name>[A-Za-z][\w]*)\s*(?:\((?P<details>.*)\))?\s*$")

    _FILTER_STEP_LABEL = re.compile(r"\b(?:PREWHERE|WHERE|HAVING)\b")

    def __init__(self):
        super().__init__("clickhouse")

    def _parse_impl(self, query_id: str, explain_output: str) -> QueryPlanDAG:
        if not explain_output or not explain_output.strip():
            raise ValueError("Empty EXPLAIN output")

        parsed_nodes = self._parse_lines(explain_output)
        if not parsed_nodes:
            raise ValueError("No operators found in EXPLAIN output")

        root = self._build_tree(parsed_nodes)
        return QueryPlanDAG(
            query_id=query_id,
            platform=self.platform_name,
            logical_root=root,
            raw_explain_output=explain_output,
        )

    def _parse_lines(self, explain_output: str) -> list[dict[str, Any]]:
        parsed: list[dict[str, Any]] = []
        for raw_line in explain_output.splitlines():
            if not raw_line.strip():
                continue
            stripped = raw_line.lstrip()
            indent = len(raw_line) - len(stripped)

            content = stripped.strip()
            paren = re.match(r"^\((?P<inner>[A-Za-z][\w]*)\)$", content)
            if paren:
                name, details = paren.group("inner"), ""
            else:
                match = self._NODE_PATTERN.match(content)
                if match:
                    name = match.group("name")
                    details = (match.group("details") or "").strip()
                else:
                    name = content.split()[0] if content.split() else content
                    details = content[len(name) :].strip()
            if not name:
                continue
            parsed.append({"indent": indent, "name": name, "details": details})
        return parsed

    def _build_tree(self, parsed_nodes: list[dict[str, Any]]) -> LogicalOperator:
        stack: list[tuple[int, LogicalOperator]] = []
        root: LogicalOperator | None = None

        for node in parsed_nodes:
            logical_op = self._convert_to_logical_operator(node)
            while stack and stack[-1][0] >= node["indent"]:
                stack.pop()
            if not stack:
                if root is None:
                    root = logical_op
                else:
                    root.children.append(logical_op)
                    stack.append((node["indent"], logical_op))
                    continue
            else:
                stack[-1][1].children.append(logical_op)
            stack.append((node["indent"], logical_op))

        if root is None:
            raise ValueError("Could not determine root operator")
        return root

    def _convert_to_logical_operator(self, node: dict[str, Any]) -> LogicalOperator:
        name = node["name"]
        details = node["details"]
        logical_type = self._map_operator_type(name)
        if logical_type == LogicalOperatorType.PROJECT and self._FILTER_STEP_LABEL.search(details):
            logical_type = LogicalOperatorType.FILTER

        kwargs: dict[str, Any] = {}
        if logical_type == LogicalOperatorType.SCAN:
            table = self._extract_table(details)
            if table:
                kwargs["table_name"] = table

        physical_op = self._create_physical_operator(
            name,
            properties={},
            platform_metadata={"details": details or None},
        )
        return self._create_logical_operator(
            operator_type=logical_type,
            children=[],
            physical_operator=physical_op,
            **kwargs,
        )

    @classmethod
    def _map_operator_type(cls, name: str) -> LogicalOperatorType:
        normalized = name.lower().replace(" ", "").replace("_", "")
        for keyword, logical_type in cls._OPERATOR_KEYWORDS:
            if keyword in normalized:
                return logical_type
        return LogicalOperatorType.OTHER

    @staticmethod
    def _extract_table(details: str) -> str | None:
        if not details:
            return None
        dotted = re.search(r"\b([A-Za-z_]\w*\.[A-Za-z_][\w.]*)", details)
        if dotted:
            return dotted.group(1)
        bare = re.fullmatch(r"\s*([A-Za-z_]\w*)\s*", details)
        return bare.group(1) if bare else None
