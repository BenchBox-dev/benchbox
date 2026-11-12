from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

_HEX_LITERAL = r"0[xX][0-9A-Fa-f](?:_?[0-9A-Fa-f])*"
_DECIMAL_LITERAL = (
    r"(?:\d(?:_?\d)*(?:\.(?:\d(?:_?\d)*)?)?|\.\d(?:_?\d)*)"
    r"(?:[eE][+-]?\d(?:_?\d)*)?"
)
_NUMERIC_LITERAL_RE = re.compile(rf"(?<![A-Za-z0-9_.$#])[+-]?(?:{_HEX_LITERAL}|{_DECIMAL_LITERAL})(?![A-Za-z0-9_.$#])")
_STRING_LITERAL_RE = re.compile(r"'(?:[^']|'')*'")
_LITERAL_PLACEHOLDER = "?"

logger = logging.getLogger(__name__)


def _mask_literals(expression: str) -> str:
    return _STRING_LITERAL_RE.sub("<STR>", _NUMERIC_LITERAL_RE.sub("<NUM>", expression))


def _normalize_literal_text(text: str) -> str:
    if not text:
        return text
    normalized = _STRING_LITERAL_RE.sub(_LITERAL_PLACEHOLDER, text)
    normalized = _NUMERIC_LITERAL_RE.sub(_LITERAL_PLACEHOLDER, normalized)
    return normalized


_logged_unknown_operator_types: set[str] = set()
_logged_unknown_join_types: set[str] = set()


RAW_OUTPUT_FULL = "full"
RAW_OUTPUT_TRUNCATED = "truncated"
RAW_OUTPUT_NONE = "none"

RAW_OUTPUT_POLICIES = frozenset({RAW_OUTPUT_FULL, RAW_OUTPUT_TRUNCATED, RAW_OUTPUT_NONE})

DEFAULT_RAW_OUTPUT_POLICY = RAW_OUTPUT_TRUNCATED
DEFAULT_RAW_OUTPUT_MAX_BYTES = 16 * 1024

DEFAULT_PLAN_MAX_DEPTH = 50


def normalize_raw_output_policy(policy: str | None) -> str:
    if policy is None:
        return DEFAULT_RAW_OUTPUT_POLICY
    normalized = str(policy).strip().lower()
    if normalized in RAW_OUTPUT_POLICIES:
        return normalized
    logger.warning(
        "Unknown raw_explain_output policy %r; falling back to %r. Valid values: %s",
        policy,
        DEFAULT_RAW_OUTPUT_POLICY,
        ", ".join(sorted(RAW_OUTPUT_POLICIES)),
    )
    return DEFAULT_RAW_OUTPUT_POLICY


class LogicalOperatorType(str, Enum):
    SCAN = "Scan"
    FILTER = "Filter"
    JOIN = "Join"
    AGGREGATE = "Aggregate"
    SORT = "Sort"
    LIMIT = "Limit"
    PROJECT = "Project"
    UNION = "Union"
    INTERSECT = "Intersect"
    EXCEPT = "Except"
    WINDOW = "Window"
    CTE = "CTE"
    SUBQUERY = "Subquery"
    OTHER = "Other"


class JoinType(str, Enum):
    INNER = "inner"
    LEFT = "left"
    RIGHT = "right"
    FULL = "full"
    CROSS = "cross"
    SEMI = "semi"
    ANTI = "anti"
    MARK = "mark"
    SINGLE = "single"


class AggregateFunction(str, Enum):
    COUNT = "count"
    SUM = "sum"
    AVG = "avg"
    MIN = "min"
    MAX = "max"
    STDDEV = "stddev"
    VARIANCE = "variance"
    COUNT_DISTINCT = "count_distinct"
    ARRAY_AGG = "array_agg"
    STRING_AGG = "string_agg"
    OTHER = "other"


@dataclass
class PhysicalOperator:
    operator_type: str
    operator_id: str
    properties: dict[str, Any] = field(default_factory=dict)
    platform_metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "operator_type": self.operator_type,
            "operator_id": self.operator_id,
            "properties": self.properties,
            "platform_metadata": self.platform_metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PhysicalOperator:
        return cls(
            operator_type=data["operator_type"],
            operator_id=data["operator_id"],
            properties=data.get("properties", {}),
            platform_metadata=data.get("platform_metadata", {}),
        )


@dataclass
class LogicalOperator:
    operator_type: LogicalOperatorType | str
    operator_id: str
    properties: dict[str, Any] = field(default_factory=dict)
    children: list[LogicalOperator] = field(default_factory=list)
    physical_operator: PhysicalOperator | None = None

    table_name: str | None = None
    join_type: JoinType | str | None = None
    join_conditions: list[str] | None = None
    filter_expressions: list[str] | None = None
    aggregation_functions: list[str] | None = None
    group_by_keys: list[str] | None = None
    sort_keys: list[dict[str, Any]] | None = None
    projection_expressions: list[str] | None = None
    limit_count: int | None = None
    offset_count: int | None = None

    def to_dict(self, max_depth: int | None = None, current_depth: int = 0) -> dict[str, Any]:
        operator_type_value = (
            self.operator_type.value if isinstance(self.operator_type, LogicalOperatorType) else self.operator_type
        )

        if max_depth is not None and current_depth > max_depth:
            return {
                "operator_type": operator_type_value,
                "operator_id": self.operator_id,
                "truncated_at_depth": current_depth,
                "children_omitted": len(self.children),
            }

        join_type_value = self.join_type.value if isinstance(self.join_type, JoinType) else self.join_type

        return {
            "operator_type": operator_type_value,
            "operator_id": self.operator_id,
            "properties": self.properties,
            "children": [
                child.to_dict(max_depth=max_depth, current_depth=current_depth + 1) for child in self.children
            ],
            "physical_operator": self.physical_operator.to_dict() if self.physical_operator else None,
            "table_name": self.table_name,
            "join_type": join_type_value,
            "join_conditions": self.join_conditions,
            "filter_expressions": self.filter_expressions,
            "aggregation_functions": self.aggregation_functions,
            "group_by_keys": self.group_by_keys,
            "sort_keys": self.sort_keys,
            "projection_expressions": self.projection_expressions,
            "limit_count": self.limit_count,
            "offset_count": self.offset_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LogicalOperator:
        operator_type_str = data["operator_type"]
        try:
            operator_type = LogicalOperatorType(operator_type_str)
        except ValueError:
            operator_type = operator_type_str

        join_type_str = data.get("join_type")
        join_type = None
        if join_type_str:
            try:
                join_type = JoinType(join_type_str)
            except ValueError:
                join_type = join_type_str

        return cls(
            operator_type=operator_type,
            operator_id=data["operator_id"],
            properties=data.get("properties", {}),
            children=[cls.from_dict(child) for child in data.get("children", [])],
            physical_operator=PhysicalOperator.from_dict(data["physical_operator"])
            if data.get("physical_operator")
            else None,
            table_name=data.get("table_name"),
            join_type=join_type,
            join_conditions=data.get("join_conditions"),
            filter_expressions=data.get("filter_expressions"),
            aggregation_functions=data.get("aggregation_functions"),
            group_by_keys=data.get("group_by_keys"),
            sort_keys=data.get("sort_keys"),
            projection_expressions=data.get("projection_expressions"),
            limit_count=data.get("limit_count"),
            offset_count=data.get("offset_count"),
        )

    def get_structural_signature(self, normalize_literals: bool = False) -> str:
        return json.dumps(
            self._structural_signature_obj(normalize_literals=normalize_literals),
            separators=(",", ":"),
            ensure_ascii=True,
            sort_keys=True,
        )

    def _structural_signature_obj(self, normalize_literals: bool = False) -> dict[str, Any]:
        norm = _normalize_literal_text if normalize_literals else (lambda value: value)

        node: dict[str, Any] = {"op": get_operator_type_str(self.operator_type)}

        if self.table_name:
            node["table"] = self.table_name

        if self.join_type:
            node["join"] = get_join_type_str(self.join_type)

        if self.join_conditions:
            conditions = (
                [_mask_literals(c) for c in self.join_conditions] if normalize_literals else list(self.join_conditions)
            )
            node["join_cond"] = sorted(conditions)

        if self.filter_expressions:
            filters = (
                [_mask_literals(f) for f in self.filter_expressions]
                if normalize_literals
                else list(self.filter_expressions)
            )
            node["filters"] = sorted(filters)

        if self.aggregation_functions:
            node["aggs"] = sorted(norm(a) for a in self.aggregation_functions)

        if self.group_by_keys:
            node["group"] = [norm(g) for g in self.group_by_keys]

        if self.sort_keys:
            node["sort"] = [norm(str(sorted(sk.items()))) for sk in self.sort_keys]

        if self.projection_expressions:
            node["proj"] = (
                [_mask_literals(p) for p in self.projection_expressions]
                if normalize_literals
                else list(self.projection_expressions)
            )

        if self.limit_count is not None:
            node["limit"] = _LITERAL_PLACEHOLDER if normalize_literals else self.limit_count
        if self.offset_count is not None:
            node["offset"] = _LITERAL_PLACEHOLDER if normalize_literals else self.offset_count

        if self.children:
            node["children"] = [
                child._structural_signature_obj(normalize_literals=normalize_literals) for child in self.children
            ]

        return node


FINGERPRINT_VERSION = 2
LEGACY_FINGERPRINT_VERSION = 1


class FingerprintIntegrity:
    VERIFIED = "verified"
    STALE = "stale"
    UNVERIFIED = "unverified"
    RECOMPUTED = "recomputed"
    TRUNCATED = "truncated"


def find_truncation_depth(node: Any) -> int | None:
    depths: list[int] = []
    stack: list[Any] = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            marker = current.get("truncated_at_depth")
            if isinstance(marker, int) and not isinstance(marker, bool):
                depths.append(marker)
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return min(depths) if depths else None


@dataclass
class QueryPlanDAG:
    query_id: str
    platform: str
    logical_root: LogicalOperator
    estimated_cost: float | None = None
    estimated_rows: int | None = None
    plan_fingerprint: str | None = None
    raw_explain_output: str | None = None
    fingerprint_integrity: str = field(default=FingerprintIntegrity.UNVERIFIED)
    fingerprint_version: int = field(default=FINGERPRINT_VERSION)

    def __post_init__(self) -> None:
        self._normalized_fingerprint: str | None = None
        self.truncated_at_depth: int | None = None
        if self.plan_fingerprint is None:
            self.plan_fingerprint = self.compute_plan_fingerprint()
            self.fingerprint_integrity = FingerprintIntegrity.VERIFIED
            self.fingerprint_version = FINGERPRINT_VERSION

    def compute_plan_fingerprint(self, normalize_literals: bool = False) -> str:
        if self.logical_root is None:
            return hashlib.sha256(b"EMPTY_PLAN").hexdigest()
        structural_signature = self.logical_root.get_structural_signature(normalize_literals=normalize_literals)
        return hashlib.sha256(structural_signature.encode("utf-8")).hexdigest()

    @property
    def normalized_fingerprint(self) -> str:
        if self._normalized_fingerprint is None:
            self._normalized_fingerprint = self.compute_plan_fingerprint(normalize_literals=True)
        return self._normalized_fingerprint

    def verify_fingerprint(self) -> bool:
        current = self.compute_plan_fingerprint()
        matches = current == self.plan_fingerprint
        if matches:
            self.fingerprint_integrity = FingerprintIntegrity.VERIFIED
        elif self.truncated_at_depth is not None:
            self.fingerprint_integrity = FingerprintIntegrity.TRUNCATED
        else:
            self.fingerprint_integrity = FingerprintIntegrity.STALE
        return matches

    def is_fingerprint_trusted(self) -> bool:
        return self.fingerprint_integrity in (
            FingerprintIntegrity.VERIFIED,
            FingerprintIntegrity.RECOMPUTED,
        )

    def refresh_fingerprint(self) -> None:
        self.plan_fingerprint = self.compute_plan_fingerprint()
        self.fingerprint_integrity = (
            FingerprintIntegrity.TRUNCATED if self.truncated_at_depth is not None else FingerprintIntegrity.VERIFIED
        )
        self.fingerprint_version = FINGERPRINT_VERSION
        self._normalized_fingerprint = None

    def apply_raw_output_policy(
        self,
        policy: str | None = DEFAULT_RAW_OUTPUT_POLICY,
        max_bytes: int = DEFAULT_RAW_OUTPUT_MAX_BYTES,
    ) -> None:
        resolved = normalize_raw_output_policy(policy)

        if resolved == RAW_OUTPUT_FULL or self.raw_explain_output is None:
            return

        if resolved == RAW_OUTPUT_NONE:
            self.raw_explain_output = None
            return

        if max_bytes <= 0:
            self.raw_explain_output = None
            return

        encoded = self.raw_explain_output.encode("utf-8")
        original_bytes = len(encoded)
        if original_bytes <= max_bytes:
            return

        kept = encoded[:max_bytes].decode("utf-8", errors="ignore")
        retained_bytes = len(kept.encode("utf-8"))
        self.raw_explain_output = (
            f"{kept}\n...[raw_explain_output truncated: retained {retained_bytes} "
            f"of {original_bytes} bytes under '{RAW_OUTPUT_TRUNCATED}' policy]"
        )

    def to_dict(self, max_depth: int | None = DEFAULT_PLAN_MAX_DEPTH) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "platform": self.platform,
            "logical_root": self.logical_root.to_dict(max_depth=max_depth, current_depth=0)
            if self.logical_root
            else None,
            "estimated_cost": self.estimated_cost,
            "estimated_rows": self.estimated_rows,
            "plan_fingerprint": self.plan_fingerprint,
            "fingerprint_version": self.fingerprint_version,
            "raw_explain_output": self.raw_explain_output,
        }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        *,
        verify_fingerprint: bool = True,
        refresh_on_mismatch: bool = False,
    ) -> QueryPlanDAG:
        logical_root_data = data.get("logical_root")
        stored_fingerprint = data.get("plan_fingerprint")
        stored_version = (
            data.get("fingerprint_version", LEGACY_FINGERPRINT_VERSION)
            if stored_fingerprint is not None
            else FINGERPRINT_VERSION
        )

        plan = cls(
            query_id=data["query_id"],
            platform=data["platform"],
            logical_root=LogicalOperator.from_dict(logical_root_data) if logical_root_data else None,
            estimated_cost=data.get("estimated_cost"),
            estimated_rows=data.get("estimated_rows"),
            plan_fingerprint=stored_fingerprint,
            raw_explain_output=data.get("raw_explain_output"),
            fingerprint_integrity=FingerprintIntegrity.UNVERIFIED,
            fingerprint_version=stored_version,
        )

        plan.truncated_at_depth = find_truncation_depth(logical_root_data)

        if plan.truncated_at_depth is not None:
            plan.fingerprint_integrity = FingerprintIntegrity.TRUNCATED
        elif stored_fingerprint is None:
            plan.fingerprint_integrity = FingerprintIntegrity.RECOMPUTED
        elif verify_fingerprint:
            computed = plan.compute_plan_fingerprint()
            if computed == stored_fingerprint:
                plan.fingerprint_integrity = FingerprintIntegrity.VERIFIED
            elif refresh_on_mismatch:
                plan.plan_fingerprint = computed
                plan.fingerprint_integrity = FingerprintIntegrity.RECOMPUTED
                plan.fingerprint_version = FINGERPRINT_VERSION
            else:
                plan.fingerprint_integrity = FingerprintIntegrity.STALE

        return plan

    def to_json(self, indent: int | None = 2, *, max_depth: int | None = DEFAULT_PLAN_MAX_DEPTH) -> str:
        return json.dumps(self.to_dict(max_depth=max_depth), indent=indent)

    def estimate_serialized_size(self, *, max_depth: int | None = DEFAULT_PLAN_MAX_DEPTH) -> int:
        return len(json.dumps(self.to_dict(max_depth=max_depth), indent=None))

    @classmethod
    def from_json(
        cls,
        json_str: str,
        *,
        verify_fingerprint: bool = True,
        refresh_on_mismatch: bool = False,
    ) -> QueryPlanDAG:
        return cls.from_dict(
            json.loads(json_str),
            verify_fingerprint=verify_fingerprint,
            refresh_on_mismatch=refresh_on_mismatch,
        )


def compute_plan_fingerprint(logical_root: LogicalOperator, normalize_literals: bool = False) -> str:
    structural_signature = logical_root.get_structural_signature(normalize_literals=normalize_literals)
    return hashlib.sha256(structural_signature.encode("utf-8")).hexdigest()


def validate_plan_tree(root: LogicalOperator) -> list[str]:
    errors: list[str] = []
    visited: set[int] = set()
    operator_ids: set[str] = set()

    def validate_node(node: LogicalOperator, path: list[str]) -> None:
        node_id = id(node)
        if node_id in visited:
            errors.append(f"Cycle detected at {node.operator_id} (path: {' -> '.join(path)})")
            return
        visited.add(node_id)

        if node.operator_id in operator_ids:
            errors.append(f"Duplicate operator_id: {node.operator_id}")
        operator_ids.add(node.operator_id)

        if not node.operator_type:
            errors.append(f"Missing operator_type for {node.operator_id}")

        if not node.operator_id:
            errors.append("Found operator with empty operator_id")

        for child in node.children:
            validate_node(child, path + [node.operator_id])

    validate_node(root, [])
    return errors


def validate_root_operator(root: LogicalOperator) -> list[str]:
    warnings: list[str] = []

    op_type = (
        root.operator_type.value if isinstance(root.operator_type, LogicalOperatorType) else str(root.operator_type)
    )

    unusual_roots = {"Scan", "Filter", "SCAN", "FILTER"}
    if op_type in unusual_roots:
        warnings.append(
            f"Root operator is {op_type}, which is unusual. Plan may be incomplete. "
            "Expected: Project, Sort, Limit, or similar result-producing operator."
        )

    return warnings


def describe_tree(root: LogicalOperator, max_depth: int = 5) -> str:
    lines: list[str] = []

    def describe_node(node: LogicalOperator, depth: int, prefix: str) -> None:
        if depth > max_depth:
            lines.append(f"{prefix}... (truncated)")
            return

        op_type = get_operator_type_str(node.operator_type)
        lines.append(f"{prefix}{op_type}[{node.operator_id}]")

        for i, child in enumerate(node.children):
            is_last = i == len(node.children) - 1
            child_prefix = prefix + ("  " if is_last else "  ")
            describe_node(child, depth + 1, child_prefix)

    describe_node(root, 0, "")
    return "\n".join(lines)


def get_operator_type_str(operator_type: LogicalOperatorType | str, *, warn_unknown: bool = True) -> str:
    if isinstance(operator_type, LogicalOperatorType):
        return operator_type.value

    op_str = str(operator_type)
    try:
        LogicalOperatorType(op_str)
    except ValueError:
        if warn_unknown and op_str not in _logged_unknown_operator_types:
            _logged_unknown_operator_types.add(op_str)
            logger.warning(
                f"Unknown operator type '{op_str}' encountered. "
                "Consider adding a mapping in the parser or extending LogicalOperatorType enum."
            )
    return op_str


def get_join_type_str(join_type: JoinType | str | None, *, warn_unknown: bool = True) -> str | None:
    if join_type is None:
        return None
    if isinstance(join_type, JoinType):
        return join_type.value

    jt_str = str(join_type)
    try:
        JoinType(jt_str)
    except ValueError:
        if warn_unknown and jt_str not in _logged_unknown_join_types:
            _logged_unknown_join_types.add(jt_str)
            logger.warning(
                f"Unknown join type '{jt_str}' encountered. "
                "Consider adding a mapping in the parser or extending JoinType enum."
            )
    return jt_str


def normalize_operator_type(operator_type: LogicalOperatorType | str) -> LogicalOperatorType | str:
    if isinstance(operator_type, LogicalOperatorType):
        return operator_type
    try:
        return LogicalOperatorType(operator_type)
    except ValueError:
        return operator_type


def is_operator_type_match(left: LogicalOperatorType | str, right: LogicalOperatorType | str) -> bool:
    return get_operator_type_str(left, warn_unknown=False) == get_operator_type_str(right, warn_unknown=False)


def clear_unknown_type_warnings() -> None:
    _logged_unknown_operator_types.clear()
    _logged_unknown_join_types.clear()
