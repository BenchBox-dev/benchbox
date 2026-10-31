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


class DorisQueryPlanParser(QueryPlanParser):
    _OPERATOR_KEYWORDS: tuple[tuple[str, LogicalOperatorType], ...] = (
        ("physicalolapscan", LogicalOperatorType.SCAN),
        ("physicalfilescan", LogicalOperatorType.SCAN),
        ("physicalschemascan", LogicalOperatorType.SCAN),
        ("physicaljdbcscan", LogicalOperatorType.SCAN),
        ("physicalesscan", LogicalOperatorType.SCAN),
        ("physicalhivescan", LogicalOperatorType.SCAN),
        ("scan", LogicalOperatorType.SCAN),
        ("physicalhashjoin", LogicalOperatorType.JOIN),
        ("physicalnestedloopjoin", LogicalOperatorType.JOIN),
        ("join", LogicalOperatorType.JOIN),
        ("physicalhashaggregate", LogicalOperatorType.AGGREGATE),
        ("hashagg", LogicalOperatorType.AGGREGATE),
        ("aggregate", LogicalOperatorType.AGGREGATE),
        ("physicalquicksort", LogicalOperatorType.SORT),
        ("physicaltopn", LogicalOperatorType.SORT),
        ("sort", LogicalOperatorType.SORT),
        ("physicallimit", LogicalOperatorType.LIMIT),
        ("limit", LogicalOperatorType.LIMIT),
        ("physicalproject", LogicalOperatorType.PROJECT),
        ("project", LogicalOperatorType.PROJECT),
        ("physicalwindow", LogicalOperatorType.WINDOW),
        ("window", LogicalOperatorType.WINDOW),
        ("physicalunion", LogicalOperatorType.UNION),
        ("union", LogicalOperatorType.UNION),
        ("physicalexcept", LogicalOperatorType.EXCEPT),
        ("physicalintersect", LogicalOperatorType.INTERSECT),
        ("physicalfilter", LogicalOperatorType.FILTER),
        ("vselect", LogicalOperatorType.FILTER),
        ("filter", LogicalOperatorType.FILTER),
        ("physicalcte", LogicalOperatorType.CTE),
        ("physicaldistribute", LogicalOperatorType.OTHER),
        ("physicalresultsink", LogicalOperatorType.OTHER),
        ("sink", LogicalOperatorType.OTHER),
    )

    _HASH_CONDITION_RE = re.compile(r"hashCondition=\((\(.*?\))\)(?=\s+otherCondition|\s*$)")
    _LINE_RE = re.compile(r"^(?P<dashes>-*)(?P<content>.*)$")
    _FRAGMENT_RE = re.compile(r"^PLAN FRAGMENT (\d+)\s*$", re.MULTILINE)
    _FRAGMENT_NODE_RE = re.compile(r"^(?P<prefix>[ |\-]*?)(?P<id>\d+):(?P<label>[A-Za-z].*)$")
    _SINK_EXCHANGE_RE = re.compile(r"EXCHANGE ID:\s*(\d+)")
    _TABLE_RE = re.compile(r"TABLE:\s*([\w.$-]+)")
    _VERBOSE_JOIN_RE = re.compile(r"join op:\s*([A-Z_ ]*?JOIN)")

    def __init__(self):
        super().__init__("doris")

    def _parse_impl(self, query_id: str, explain_output: str) -> QueryPlanDAG:
        if not explain_output or not explain_output.strip():
            raise ValueError("Empty EXPLAIN output")
        if explain_output.lstrip().startswith(("Failed to get query plan", "Could not get query plan")):
            raise ValueError("EXPLAIN returned an error message, not a plan")

        if self._FRAGMENT_RE.search(explain_output):
            root = self._parse_fragments(explain_output)
        else:
            parsed_nodes = self._parse_lines(explain_output)
            if not parsed_nodes:
                raise ValueError("No operators found in EXPLAIN SHAPE PLAN output")
            root = self._build_tree(parsed_nodes)
        if root is None:
            raise ValueError("Could not determine root operator")
        return QueryPlanDAG(
            query_id=query_id,
            platform=self.platform_name,
            logical_root=root,
            raw_explain_output=explain_output,
        )

    def _parse_fragments(self, explain_output: str) -> LogicalOperator | None:
        body = explain_output.split("\nTuples:", 1)[0]
        pieces = self._FRAGMENT_RE.split(body)
        roots: dict[int, LogicalOperator] = {}
        sink_exchange: dict[int, int] = {}
        exchanges: list[tuple[int, int, LogicalOperator]] = []
        for index in range(1, len(pieces) - 1, 2):
            fragment_id = int(pieces[index])
            root, exchange_nodes, sink_id = self._parse_fragment(pieces[index + 1])
            if root is None:
                continue
            roots[fragment_id] = root
            if sink_id is not None:
                sink_exchange[fragment_id] = sink_id
            for exchange_id, node in exchange_nodes:
                exchanges.append((exchange_id, fragment_id, node))
        for fragment_id, exchange_id in sink_exchange.items():
            target = next(
                (node for eid, owner, node in exchanges if eid == exchange_id and owner != fragment_id),
                None,
            )
            if target is not None:
                target.children.append(roots[fragment_id])
        return roots.get(min(roots)) if roots else None

    def _parse_fragment(
        self, text: str
    ) -> tuple[LogicalOperator | None, list[tuple[int, LogicalOperator]], int | None]:
        sink = self._SINK_EXCHANGE_RE.search(text)
        entries: list[dict[str, Any]] = []
        for line in text.splitlines():
            match = self._FRAGMENT_NODE_RE.match(line)
            if match:
                entries.append(
                    {
                        "id": int(match.group("id")),
                        "label": match.group("label").strip(),
                        "col": len(match.group("prefix")),
                        "build": "----" in match.group("prefix"),
                        "lines": [],
                    }
                )
            elif entries:
                entries[-1]["lines"].append(line.strip().lstrip("|").strip())
        stack: list[tuple[int, LogicalOperator]] = []
        root: LogicalOperator | None = None
        exchange_nodes: list[tuple[int, LogicalOperator]] = []
        for entry in entries:
            operator = self._convert_fragment_node(entry["label"], entry["lines"])
            if "exchange" in entry["label"].lower().split("(")[0]:
                exchange_nodes.append((entry["id"], operator))
            while stack and (stack[-1][0] > entry["col"] or (entry["build"] and stack[-1][0] >= entry["col"])):
                stack.pop()
            if stack:
                stack[-1][1].children.append(operator)
            elif root is None:
                root = operator
            stack.append((entry["col"], operator))
        return root, exchange_nodes, int(sink.group(1)) if sink else None

    def _convert_fragment_node(self, label: str, lines: list[str]) -> LogicalOperator:
        name = re.match(r"[A-Za-z][\w -]*?(?=\s*\(|\s*$)", label)
        name_text = name.group(0).strip() if name else label.split("(")[0].strip()
        logical_type = self._map_operator_type(name_text)
        kwargs: dict[str, Any] = {}
        if logical_type == LogicalOperatorType.SCAN:
            for line in lines:
                table = self._TABLE_RE.match(line)
                if table:
                    kwargs["table_name"] = table.group(1)
                elif line.startswith("PREDICATES:"):
                    kwargs["filter_expressions"] = [line.removeprefix("PREDICATES:").strip()]
        elif logical_type == LogicalOperatorType.JOIN:
            for line in lines:
                join_op = self._VERBOSE_JOIN_RE.match(line)
                if join_op:
                    kwargs["join_type"] = self._classify_join_type(join_op.group(1))
                elif line.startswith("equal join conjunct:"):
                    kwargs.setdefault("join_conditions", []).append(line.removeprefix("equal join conjunct:").strip())
            kwargs.setdefault("join_type", JoinType.INNER)
        elif logical_type == LogicalOperatorType.AGGREGATE:
            for line in lines:
                if line.startswith("group by:"):
                    kwargs["group_by_keys"] = [
                        k.strip() for k in line.removeprefix("group by:").split(",") if k.strip()
                    ]
        physical_op = self._create_physical_operator(
            name_text,
            properties={},
            platform_metadata={"details": " ".join(line for line in lines if line) or None},
        )
        return self._create_logical_operator(
            operator_type=logical_type,
            children=[],
            physical_operator=physical_op,
            **kwargs,
        )

    def _parse_lines(self, explain_output: str) -> list[dict[str, Any]]:
        parsed: list[dict[str, Any]] = []
        for raw_line in explain_output.splitlines():
            stripped = raw_line.strip()
            if not stripped:
                continue
            match = self._LINE_RE.match(stripped)
            content = match.group("content").strip()
            if not content or not content[0].isalpha():
                continue
            depth = len(match.group("dashes")) // 2
            parsed.append({"depth": depth, "content": content})
        return parsed

    def _build_tree(self, parsed_nodes: list[dict[str, Any]]) -> LogicalOperator | None:
        stack: list[tuple[int, LogicalOperator]] = []
        root: LogicalOperator | None = None

        for node in parsed_nodes:
            logical_op = self._convert_to_logical_operator(node["content"])
            while stack and stack[-1][0] >= node["depth"]:
                stack.pop()
            if not stack:
                if root is None:
                    root = logical_op
                else:
                    root.children.append(logical_op)
            else:
                stack[-1][1].children.append(logical_op)
            stack.append((node["depth"], logical_op))

        return root

    def _convert_to_logical_operator(self, content: str) -> LogicalOperator:
        name_match = re.match(r"([A-Za-z][\w]*)", content)
        name = name_match.group(1) if name_match else content
        qualifier = self._extract_qualifier(content)
        logical_type = self._map_operator_type(name)

        kwargs: dict[str, Any] = {}
        if logical_type == LogicalOperatorType.SCAN and qualifier:
            kwargs["table_name"] = qualifier
        elif logical_type == LogicalOperatorType.JOIN:
            kwargs["join_type"] = self._classify_join_type(qualifier)
            condition = self._HASH_CONDITION_RE.search(content)
            if condition:
                kwargs["join_conditions"] = [condition.group(1)]

        physical_op = self._create_physical_operator(
            name,
            properties={},
            platform_metadata={"details": qualifier or None},
        )
        return self._create_logical_operator(
            operator_type=logical_type,
            children=[],
            physical_operator=physical_op,
            **kwargs,
        )

    @staticmethod
    def _extract_qualifier(content: str) -> str | None:
        match = re.search(r"\[([^\]]*)\]", content)
        return match.group(1).strip() if match else None

    @classmethod
    def _map_operator_type(cls, name: str) -> LogicalOperatorType:
        normalized = name.lower().replace(" ", "").replace("_", "")
        for keyword, logical_type in cls._OPERATOR_KEYWORDS:
            if keyword in normalized:
                return logical_type
        return LogicalOperatorType.OTHER

    def _classify_join_type(self, qualifier: str | None) -> JoinType:
        if not qualifier:
            return JoinType.INNER
        return self._harmonize_join_type(qualifier)


__all__ = ["DorisQueryPlanParser"]
