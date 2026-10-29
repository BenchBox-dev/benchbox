from __future__ import annotations

import logging
import re
from typing import Any

from benchbox.core.query_plans.parsers.base import QueryPlanParser
from benchbox.core.results.query_plan_models import (
    JoinType,
    LogicalOperator,
    LogicalOperatorType,
    QueryPlanDAG,
)

logger = logging.getLogger(__name__)


class SingleStoreQueryPlanParser(QueryPlanParser):
    _OPERATOR_KEYWORDS: tuple[tuple[str, LogicalOperatorType], ...] = (
        ("orderedcolumnstorescan", LogicalOperatorType.SCAN),
        ("columnstorescan", LogicalOperatorType.SCAN),
        ("indexrangescan", LogicalOperatorType.SCAN),
        ("indexseek", LogicalOperatorType.SCAN),
        ("tablescan", LogicalOperatorType.SCAN),
        ("nestedloopjoin", LogicalOperatorType.JOIN),
        ("hashjoin", LogicalOperatorType.JOIN),
        ("mergejoin", LogicalOperatorType.JOIN),
        ("join", LogicalOperatorType.JOIN),
        ("hashgroupby", LogicalOperatorType.AGGREGATE),
        ("streaminggroupby", LogicalOperatorType.AGGREGATE),
        ("groupby", LogicalOperatorType.AGGREGATE),
        ("aggregate", LogicalOperatorType.AGGREGATE),
        ("topsort", LogicalOperatorType.SORT),
        ("sort", LogicalOperatorType.SORT),
        ("top", LogicalOperatorType.LIMIT),
        ("limit", LogicalOperatorType.LIMIT),
        ("filter", LogicalOperatorType.FILTER),
        ("project", LogicalOperatorType.PROJECT),
        ("window", LogicalOperatorType.WINDOW),
        ("union", LogicalOperatorType.UNION),
        ("gathermerge", LogicalOperatorType.OTHER),
        ("gather", LogicalOperatorType.OTHER),
        ("repartition", LogicalOperatorType.OTHER),
        ("broadcast", LogicalOperatorType.OTHER),
        ("shuffle", LogicalOperatorType.OTHER),
    )

    _PREFIX_RE = re.compile(r"^((?:\|---|\+---|\|   |\|--|    )*)(.*)$")
    _BLOCK_START_SUFFIXES = ("|---", "+---", "|--")
    _NOTICE_RE = re.compile(r"^(?:WARNING\b|See\s+https?://)")

    def __init__(self):
        super().__init__("singlestore")

    def _parse_impl(self, query_id: str, explain_output: str) -> QueryPlanDAG:
        if not explain_output or not explain_output.strip():
            raise ValueError("Empty EXPLAIN output")
        if explain_output.lstrip().startswith(("Failed to get query plan", "Could not get query plan")):
            raise ValueError("EXPLAIN returned an error message, not a plan")

        parsed_nodes = self._parse_lines(explain_output)
        if not parsed_nodes:
            raise ValueError("No operators found in EXPLAIN output")

        root = self._build_tree(parsed_nodes)
        if root is None:
            raise ValueError("Could not determine root operator")
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
            line = raw_line.rstrip()
            stripped = line.strip()
            if stripped.startswith("|") and stripped.endswith("|") and ("---" not in stripped):
                line = stripped[1:-1]
            elif stripped.startswith("+") and set(stripped) <= {"+", "-"}:
                continue
            if line.strip() in ("EXPLAIN", "QUERY PLAN") or self._NOTICE_RE.match(line.strip()):
                continue
            line = re.sub(r"\| (?!-)", "|   ", line)
            prefix, rest = self._PREFIX_RE.match(line).groups()
            rest = rest.strip()
            if not rest or not rest[0].isalpha():
                continue
            depth = len(prefix) // 4
            parsed.append({"depth": depth, "content": rest, "block_start": prefix.endswith(self._BLOCK_START_SUFFIXES)})
        return parsed

    def _build_tree(self, parsed_nodes: list[dict[str, Any]]) -> LogicalOperator | None:
        root: LogicalOperator | None = None
        last_at_depth: dict[int, LogicalOperator] = {}

        for node in parsed_nodes:
            depth = node["depth"]
            logical_op = self._convert_to_logical_operator(node["content"])

            if depth == 0 or not node["block_start"]:
                parent = last_at_depth.get(depth) or last_at_depth.get(depth - 1)
            else:
                parent = last_at_depth.get(depth - 1)
            if parent is None:
                if root is None:
                    root = logical_op
                else:
                    root.children.append(logical_op)
            else:
                parent.children.append(logical_op)

            last_at_depth[depth] = logical_op
            for deeper in [d for d in last_at_depth if d > depth]:
                del last_at_depth[deeper]

        return root

    def _convert_to_logical_operator(self, content: str) -> LogicalOperator:
        name_match = re.match(r"([A-Za-z][\w]*)", content)
        name = name_match.group(1) if name_match else content
        logical_type = self._map_operator_type(name)

        kwargs: dict[str, Any] = {}
        if logical_type == LogicalOperatorType.SCAN:
            table = self._extract_table(content)
            if table:
                kwargs["table_name"] = table
        elif logical_type == LogicalOperatorType.JOIN:
            kwargs["join_type"] = self._classify_join_type(content)

        physical_op = self._create_physical_operator(
            name,
            properties={},
            platform_metadata={"details": content},
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
    def _extract_table(content: str) -> str | None:
        match = re.match(r"[A-Za-z][\w]*\s+([A-Za-z_][\w.]*)", content)
        return match.group(1) if match else None

    def _classify_join_type(self, content: str) -> JoinType:
        match = re.search(r"\[([^\]]*)\]", content)
        if match:
            return self._harmonize_join_type(match.group(1))
        return JoinType.INNER


__all__ = ["SingleStoreQueryPlanParser"]
