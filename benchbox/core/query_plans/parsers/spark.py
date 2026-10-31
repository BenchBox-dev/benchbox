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


class SparkQueryPlanParser(QueryPlanParser):
    _OPERATOR_KEYWORDS: tuple[tuple[str, LogicalOperatorType], ...] = (
        ("filescan", LogicalOperatorType.SCAN),
        ("inmemorytablescan", LogicalOperatorType.SCAN),
        ("hivetablescan", LogicalOperatorType.SCAN),
        ("batchscan", LogicalOperatorType.SCAN),
        ("rowdatasourcescan", LogicalOperatorType.SCAN),
        ("datasourcescan", LogicalOperatorType.SCAN),
        ("datasourceexec", LogicalOperatorType.SCAN),
        ("datasourcev2scan", LogicalOperatorType.SCAN),
        ("scan", LogicalOperatorType.SCAN),
        ("broadcasthashjoin", LogicalOperatorType.JOIN),
        ("sortmergejoin", LogicalOperatorType.JOIN),
        ("shuffledhashjoin", LogicalOperatorType.JOIN),
        ("broadcastnestedloopjoin", LogicalOperatorType.JOIN),
        ("cartesianproduct", LogicalOperatorType.JOIN),
        ("join", LogicalOperatorType.JOIN),
        ("hashaggregate", LogicalOperatorType.AGGREGATE),
        ("objecthashaggregate", LogicalOperatorType.AGGREGATE),
        ("sortaggregate", LogicalOperatorType.AGGREGATE),
        ("aggregate", LogicalOperatorType.AGGREGATE),
        ("takeorderedandproject", LogicalOperatorType.SORT),
        ("sort", LogicalOperatorType.SORT),
        ("globallimit", LogicalOperatorType.LIMIT),
        ("locallimit", LogicalOperatorType.LIMIT),
        ("collectlimit", LogicalOperatorType.LIMIT),
        ("window", LogicalOperatorType.WINDOW),
        ("union", LogicalOperatorType.UNION),
        ("filter", LogicalOperatorType.FILTER),
        ("project", LogicalOperatorType.PROJECT),
        ("broadcastexchange", LogicalOperatorType.OTHER),
        ("shuffleexchange", LogicalOperatorType.OTHER),
        ("exchange", LogicalOperatorType.OTHER),
    )

    _PHYSICAL_HEADER = "== Physical Plan =="
    _PREFIX_RE = re.compile(r"^((?:\+- |:- |:  |   )*)(.*)$")
    _CODEGEN_RE = re.compile(r"^\*(?:\(\d+\))?\s*")
    _TREE_MARKER_RE = re.compile(r"^(?:[+:]- |:  )", re.MULTILINE)
    _PART_FILE_RE = re.compile(r"^(?:part|chunk|data|file|\d+)(?:[-_.].*)?$|^[0-9a-f]{8}-[0-9a-f]{4}-", re.IGNORECASE)
    _FILE_GROUPS_RE = re.compile(r"file_groups=\{[^\[]*\[\[([^\],]+)")

    def __init__(self):
        super().__init__("spark")

    def _parse_impl(self, query_id: str, explain_output: str) -> QueryPlanDAG:
        if not explain_output or not explain_output.strip():
            raise ValueError("Empty EXPLAIN output")
        if explain_output.lstrip().startswith(("Failed to get query plan", "Could not get query plan")):
            raise ValueError("EXPLAIN returned an error message, not a plan")

        physical_text = self._extract_physical_section(explain_output)
        parsed_nodes = self._parse_lines(physical_text)
        if not parsed_nodes:
            raise ValueError("No operators found in Spark physical plan")

        root = self._build_tree(parsed_nodes)
        self._name_file_scans(root, set(re.findall(r"TableScan: ([A-Za-z_]\w*)", explain_output)))
        return QueryPlanDAG(
            query_id=query_id,
            platform=self.platform_name,
            logical_root=root,
            raw_explain_output=explain_output,
        )

    def _extract_physical_section(self, explain_output: str) -> str:
        idx = explain_output.rfind(self._PHYSICAL_HEADER)
        if idx == -1:
            return explain_output
        return explain_output[idx + len(self._PHYSICAL_HEADER) :]

    def _parse_lines(self, physical_text: str) -> list[dict[str, Any]]:
        parsed: list[dict[str, Any]] = []
        indented = not self._TREE_MARKER_RE.search(physical_text)
        for raw_line in physical_text.splitlines():
            if not raw_line.strip():
                continue
            prefix, rest = self._PREFIX_RE.match(raw_line).groups()
            rest = rest.strip()
            if rest.startswith("==") and rest.endswith("=="):
                continue
            depth = (len(raw_line) - len(raw_line.lstrip())) // 2 if indented else len(prefix) // 3
            node_text = self._CODEGEN_RE.sub("", rest).strip()
            name_match = re.match(r"([A-Za-z][\w]*)", node_text)
            if not name_match:
                continue
            parsed.append({"depth": depth, "name": name_match.group(1), "details": node_text})
        return parsed

    def _build_tree(self, parsed_nodes: list[dict[str, Any]]) -> LogicalOperator:
        stack: list[tuple[int, LogicalOperator]] = []
        root: LogicalOperator | None = None

        for node in parsed_nodes:
            logical_op = self._convert_to_logical_operator(node)
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

        if root is None:
            raise ValueError("Could not determine root operator")
        return root

    def _convert_to_logical_operator(self, node: dict[str, Any]) -> LogicalOperator:
        name = node["name"]
        details = node["details"]
        logical_type = self._map_operator_type(name)

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

    @classmethod
    def _name_file_scans(cls, root: LogicalOperator, logical_tables: set[str]) -> None:
        scans: list[tuple[LogicalOperator, str | None, str]] = []
        stack = [root]
        while stack:
            node = stack.pop()
            stack.extend(node.children)
            if node.operator_type != LogicalOperatorType.SCAN or node.table_name or node.physical_operator is None:
                continue
            files = cls._FILE_GROUPS_RE.search(node.physical_operator.platform_metadata.get("details") or "")
            if not files:
                continue
            *directories, filename = files.group(1).split("/")
            stem = re.sub(r"\.\d+$", "", filename.rsplit(".", 1)[0])
            directory = next((part for part in reversed(directories) if part and "=" not in part), None)
            scans.append((node, directory, stem))
        if not scans:
            return
        directories_seen = {directory for _, directory, _ in scans}
        per_table_directories = len(scans) > 1 and len(directories_seen) == len(scans)
        for node, directory, stem in scans:
            if logical_tables:
                node.table_name = next((name for name in (directory, stem) if name in logical_tables), None)
            elif directory and (per_table_directories or not stem or cls._PART_FILE_RE.match(stem)):
                node.table_name = directory
            else:
                node.table_name = stem or None

    @staticmethod
    def _extract_table(details: str) -> str | None:
        if SparkQueryPlanParser._FILE_GROUPS_RE.search(details):
            return None
        match = re.search(r"(?:FileScan|Scan)\s+\w+\s+([A-Za-z_][\w.]*)", details)
        if match:
            return match.group(1)
        dotted = re.search(r"\b([A-Za-z_]\w*\.[A-Za-z_][\w.]*)", details)
        return dotted.group(1) if dotted else None
