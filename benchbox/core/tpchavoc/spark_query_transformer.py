# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging

from benchbox._vendor import sqlglot
from benchbox._vendor.sqlglot import exp

logger = logging.getLogger(__name__)

SCALAR_GROUP_BY_VARIANT_IDS: frozenset[str] = frozenset(
    {
        "1_v1",
        "3_v1",
        "7_v1",
        "8_v1",
        "9_v1",
        "10_v1",
        "12_v1",
        "16_v1",
    }
)

GROUP_BY_EMPTY_VARIANT_IDS: frozenset[str] = frozenset({"6_v2", "14_v2"})

DUAL_VARIANT_IDS: frozenset[str] = frozenset({"17_v4"})


class SparkTPCHavocQueryTransformer:
    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.transformations_applied: list[str] = []

    def transform(self, query: str, query_id: str | None = None) -> str:
        self.transformations_applied = []

        normalized_id = self.normalize_query_id(query_id)

        if normalized_id in SCALAR_GROUP_BY_VARIANT_IDS:
            query = self._rewrite_scalar_group_by(query)
        elif normalized_id in GROUP_BY_EMPTY_VARIANT_IDS:
            query = self._rewrite_group_by_empty(query)
        elif normalized_id in DUAL_VARIANT_IDS:
            query = self._rewrite_dual(query)

        if self.verbose and self.transformations_applied:
            logger.debug(f"Applied transformations: {', '.join(self.transformations_applied)}")

        return query

    def get_transformations_applied(self) -> list[str]:
        return self.transformations_applied

    @classmethod
    def known_variant_ids(cls) -> frozenset[str]:
        return SCALAR_GROUP_BY_VARIANT_IDS | GROUP_BY_EMPTY_VARIANT_IDS | DUAL_VARIANT_IDS

    def normalize_query_id(self, query_id: str | None) -> str | None:
        if query_id is None:
            return None
        qid = str(query_id).strip().lower()
        if qid.startswith("q"):
            qid = qid[1:]
        qid = qid.replace("-", "_")
        if "_v" in qid:
            q_num, _, v_num = qid.partition("_v")
            qid = f"{q_num.lstrip('0') or '0'}_v{v_num}"
        return qid

    _normalize_query_id = normalize_query_id

    def _rewrite_scalar_group_by(self, query: str) -> str:
        tree = sqlglot.parse_one(query, read="spark")
        applied = False
        for select in tree.find_all(exp.Select):
            if not select.args.get("group"):
                continue
            new_projections = []
            select_changed = False
            for projection in select.expressions:
                new_projection = self._wrap_bare_scalars(projection)
                new_projections.append(new_projection)
                select_changed |= new_projection is not projection
            if select_changed:
                select.set("expressions", new_projections)
                applied = True
        if not applied:
            return query
        self.transformations_applied.append("scalar_group_by_first")
        return tree.sql(dialect="spark")

    def _wrap_bare_scalars(self, node):
        from benchbox._vendor.sqlglot import exp

        if isinstance(node, exp.Subquery):
            return exp.First(this=node)
        if isinstance(node, (exp.AggFunc, exp.Exists, exp.In, exp.Any, exp.All, exp.Window)):
            return node
        if isinstance(node, exp.Select):
            return node
        args: dict = {}
        changed = False
        for key, value in node.args.items():
            if isinstance(value, exp.Expr):
                new_value = self._wrap_bare_scalars(value)
                args[key] = new_value
                changed |= new_value is not value
            elif isinstance(value, list):
                new_list = [self._wrap_bare_scalars(item) if isinstance(item, exp.Expr) else item for item in value]
                args[key] = new_list
                changed |= any(new is not old for new, old in zip(new_list, value))
            else:
                args[key] = value
        if not changed:
            return node
        new_node = node.__class__(**args)
        new_node.comments = node.comments
        return new_node

    def _rewrite_group_by_empty(self, query: str) -> str:
        tree = sqlglot.parse_one(query, read="spark")
        applied = False
        for select in tree.find_all(exp.Select):
            group = select.args.get("group")
            if group and len(group.expressions) == 1:
                only = group.expressions[0]
                if isinstance(only, exp.Tuple) and not only.expressions:
                    select.set("group", None)
                    applied = True
        if not applied:
            return query
        self.transformations_applied.append("group_by_empty_drop")
        return tree.sql(dialect="spark")

    def _rewrite_dual(self, query: str) -> str:
        tree = sqlglot.parse_one(query, read="spark")
        applied = False
        for subquery in tree.find_all(exp.Subquery):
            inner = subquery.this
            if not isinstance(inner, exp.Select) or len(inner.expressions) != 1:
                continue
            only = inner.expressions[0]
            if isinstance(only, exp.Alias):
                continue
            if isinstance(only, exp.Literal) and only.this == "1" and not only.args.get("is_string"):
                only.replace(exp.alias_(only.copy(), "dual_col"))
                applied = True
        if not applied:
            return query
        self.transformations_applied.append("dual_column_alias")
        return tree.sql(dialect="spark")


__all__ = [
    "SparkTPCHavocQueryTransformer",
    "SCALAR_GROUP_BY_VARIANT_IDS",
    "GROUP_BY_EMPTY_VARIANT_IDS",
    "DUAL_VARIANT_IDS",
]
