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


class DatabendQueryPlanParser(QueryPlanParser):
    _OPERATOR_KEYWORDS: tuple[tuple[str, LogicalOperatorType], ...] = (
        ("constanttablescan", LogicalOperatorType.SCAN),
        ("ctescan", LogicalOperatorType.SCAN),
        ("tablescan", LogicalOperatorType.SCAN),
        ("readdatasource", LogicalOperatorType.SCAN),
        ("scan", LogicalOperatorType.SCAN),
        ("hashjoin", LogicalOperatorType.JOIN),
        ("rangejoin", LogicalOperatorType.JOIN),
        ("join", LogicalOperatorType.JOIN),
        ("finalaggregator", LogicalOperatorType.AGGREGATE),
        ("aggregatortransform", LogicalOperatorType.AGGREGATE),
        ("aggregatefinal", LogicalOperatorType.AGGREGATE),
        ("aggregatepartial", LogicalOperatorType.AGGREGATE),
        ("aggregateexpand", LogicalOperatorType.AGGREGATE),
        ("aggregat", LogicalOperatorType.AGGREGATE),
        ("filter", LogicalOperatorType.FILTER),
        ("sortingtransform", LogicalOperatorType.SORT),
        ("sort", LogicalOperatorType.SORT),
        ("limit", LogicalOperatorType.LIMIT),
        ("rowfetch", LogicalOperatorType.OTHER),
        ("evalscalar", LogicalOperatorType.PROJECT),
        ("projectset", LogicalOperatorType.PROJECT),
        ("projection", LogicalOperatorType.PROJECT),
        ("project", LogicalOperatorType.PROJECT),
        ("window", LogicalOperatorType.WINDOW),
        ("unionall", LogicalOperatorType.UNION),
        ("union", LogicalOperatorType.UNION),
        ("exchangesource", LogicalOperatorType.OTHER),
        ("exchangesink", LogicalOperatorType.OTHER),
        ("exchange", LogicalOperatorType.OTHER),
    )

    _MARKER_RE = re.compile(r"├──|└──")
    _PUSHED_FILTER_RE = re.compile(r"filters:\s*\[(.*)\],\s*limit:")
    _OPERATOR_RE = re.compile(r"^[A-Za-z][\w]*(?:\([^)]*\))?$")

    def __init__(self):
        super().__init__("databend")

    def _parse_impl(self, query_id: str, explain_output: str) -> QueryPlanDAG:
        if not explain_output or not explain_output.strip():
            raise ValueError("Empty EXPLAIN output")
        if explain_output.lstrip().startswith(("Failed to get query plan", "Could not get query plan")):
            raise ValueError("EXPLAIN returned an error message, not a plan")

        parsed_lines = self._parse_lines(explain_output)
        if not parsed_lines:
            raise ValueError("No operators found in EXPLAIN output")

        raw_root = self._build_raw_tree(parsed_lines)
        if raw_root is None:
            raise ValueError("Could not determine root operator")

        root = self._convert_raw(raw_root)
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
            marker = self._MARKER_RE.search(raw_line)
            if marker:
                prefix = raw_line[: marker.start()]
                depth = len(prefix) // 4 + 1
                content = raw_line[marker.end() :].strip()
            else:
                depth = 0
                content = raw_line.strip()
            if not content:
                continue
            parsed.append(
                {
                    "depth": depth,
                    "content": content,
                    "is_operator": bool(self._OPERATOR_RE.match(content)),
                }
            )
        return parsed

    def _build_raw_tree(self, parsed_lines: list[dict[str, Any]]) -> dict[str, Any] | None:
        root: dict[str, Any] | None = None
        stack: list[dict[str, Any]] = []

        for line in parsed_lines:
            depth = line["depth"]
            if line["is_operator"]:
                node = {"name": line["content"], "depth": depth, "details": {}, "children": []}
                while stack and stack[-1]["depth"] >= depth:
                    stack.pop()
                if not stack:
                    if root is None:
                        root = node
                    else:
                        root["children"].append(node)
                else:
                    stack[-1]["children"].append(node)
                stack.append(node)
            else:
                parent = next((op for op in reversed(stack) if op["depth"] < depth), None)
                if parent is None and stack:
                    parent = stack[-1]
                if parent is not None:
                    key, sep, value = line["content"].partition(":")
                    if sep:
                        parent["details"][key.strip()] = value.strip()
        return root

    def _convert_raw(self, raw: dict[str, Any]) -> LogicalOperator:
        name = raw["name"]
        details = raw["details"]
        logical_type = self._map_operator_type(name)
        children = [self._convert_raw(child) for child in raw["children"]]

        kwargs: dict[str, Any] = {}
        if logical_type == LogicalOperatorType.SCAN:
            table = details.get("table")
            if table:
                kwargs["table_name"] = table
            pushed_filter = self._PUSHED_FILTER_RE.search(details.get("push downs", ""))
            if pushed_filter and pushed_filter.group(1).strip():
                kwargs["filter_expressions"] = [pushed_filter.group(1).strip()]
        elif logical_type == LogicalOperatorType.JOIN:
            kwargs["join_type"] = self._classify_join_type(details)
            conditions = self._pair_join_keys(details.get("build keys", ""), details.get("probe keys", ""))
            if conditions:
                kwargs["join_conditions"] = conditions

        physical_op = self._create_physical_operator(
            name,
            properties={},
            platform_metadata={"details": details or None},
        )
        return self._create_logical_operator(
            operator_type=logical_type,
            children=children,
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
    def _split_top_level(keys: str) -> list[str]:
        text = keys.strip()
        if text.startswith("[") and text.endswith("]"):
            text = text[1:-1]
        parts: list[str] = []
        depth = 0
        current = ""
        for char in text:
            if char in "([":
                depth += 1
            elif char in ")]":
                depth -= 1
            if char == "," and depth == 0:
                parts.append(current.strip())
                current = ""
            else:
                current += char
        if current.strip():
            parts.append(current.strip())
        return parts

    @classmethod
    def _pair_join_keys(cls, build_keys: str, probe_keys: str) -> list[str]:
        build = cls._split_top_level(build_keys)
        probe = cls._split_top_level(probe_keys)
        if not build or len(build) != len(probe):
            return []
        return [f"{b} = {p}" for b, p in zip(build, probe, strict=True)]

    def _classify_join_type(self, details: dict[str, str]) -> JoinType:
        join_type = details.get("join type") or details.get("join")
        if join_type:
            return self._harmonize_join_type(join_type)
        return JoinType.INNER


__all__ = ["DatabendQueryPlanParser"]
