from __future__ import annotations

import re

from benchbox._vendor import sqlglot
from benchbox._vendor.sqlglot import exp


def rewrite(query: str) -> str:
    query = _rewrite_comma_joins(query)
    query = _rewrite_interval_arithmetic(query)
    query = _rewrite_substring_from_for(query)
    query = _rewrite_cte_column_list(query)
    return query


def _rewrite_comma_joins(query: str) -> str:
    if not _has_comma_join(query):
        return query

    try:
        tree = sqlglot.parse_one(query, dialect="postgres")
    except Exception:
        return query

    changed = _rewrite_comma_joins_in_select(tree)
    if not changed:
        return query

    return tree.sql(dialect="postgres")


def _rewrite_nested_selects(root: exp.Expression) -> bool:
    changed = False
    for subquery in root.find_all(exp.Subquery):
        if subquery is not root:
            inner = subquery.this
            if isinstance(inner, exp.Select) and _rewrite_comma_joins_in_select(inner):
                changed = True
    for node in root.find_all(exp.Exists):
        if isinstance(node.this, exp.Select) and _rewrite_comma_joins_in_select(node.this):
            changed = True
    for cte in root.find_all(exp.CTE):
        if isinstance(cte.this, exp.Select) and _rewrite_comma_joins_in_select(cte.this):
            changed = True
    return changed


def _rewrite_comma_joins_in_select(select: exp.Expression) -> bool:
    changed = _rewrite_nested_selects(select)

    joins = select.args.get("joins") or []
    comma_joins = [j for j in joins if j.args.get("on") is None and j.args.get("using") is None]
    if not comma_joins:
        return changed

    where = select.args.get("where")
    if where is None:
        for join in comma_joins:
            join.set("kind", "CROSS")
        return True

    predicates = _flatten_and(where.this)
    remaining = list(predicates)

    from_clause = select.args.get("from_")
    joined_refs: set[str] = set()
    if from_clause:
        joined_refs.add(_table_ref(from_clause.this))

    for j in joins:
        if j not in comma_joins:
            joined_refs.add(_table_ref(j.this))

    for join in comma_joins:
        cj_ref = _table_ref(join.this)
        joined_refs_snapshot = set(joined_refs)

        join_pred = None
        for pred in remaining:
            if _is_join_pred_between(pred, cj_ref, joined_refs_snapshot):
                join_pred = pred
                break

        if join_pred is not None:
            join.set("on", join_pred.copy())
            remaining.remove(join_pred)
            changed = True
        else:
            join.set("kind", "CROSS")
            changed = True

        joined_refs.add(cj_ref)

    new_cond = _build_and(remaining)
    if new_cond is None:
        select.set("where", None)
    else:
        select.set("where", exp.Where(this=new_cond))

    return changed


def _table_ref(node: exp.Expression) -> str:
    alias = node.alias if hasattr(node, "alias") else ""
    if alias:
        return alias.lower()
    name = node.name if hasattr(node, "name") else str(node)
    return name.lower()


def _column_table_ref(col: exp.Column) -> str:
    table = col.args.get("table")
    if table is None:
        return ""
    return str(table).lower()


def _collect_table_refs(node: exp.Expression) -> set[str]:
    refs: set[str] = set()
    for col in node.find_all(exp.Column):
        ref = _column_table_ref(col)
        if ref:
            refs.add(ref)
    return refs


def _is_join_pred_between(pred: exp.Expression, cj_ref: str, joined_refs: set[str]) -> bool:
    if not isinstance(pred, exp.EQ):
        return False
    cols = list(pred.find_all(exp.Column))
    if len(cols) != 2:
        return False
    refs = _collect_table_refs(pred)
    if refs:
        if cj_ref in refs and bool(refs & joined_refs):
            return True
        if bool(refs & joined_refs) and any(_column_table_ref(c) == "" for c in cols):
            return True
        return False
    return all(_column_table_ref(c) == "" for c in cols)


def _flatten_and(node: exp.Expression) -> list[exp.Expression]:
    if isinstance(node, exp.And):
        return _flatten_and(node.left) + _flatten_and(node.right)
    return [node]


def _build_and(predicates: list[exp.Expression]) -> exp.Expression | None:
    if not predicates:
        return None
    result = predicates[0]
    for pred in predicates[1:]:
        result = exp.And(this=result, expression=pred)
    return result


def _has_comma_join(query: str) -> bool:
    stripped = re.sub(r"'[^']*'", "'?'", query)
    return bool(re.search(r"\b\w[\w.]*\s*,\s*\w", stripped, re.IGNORECASE))


_INTERVAL_RE = re.compile(
    r"(CAST\s*\([^)]+\)|DATE\s*'\d{4}-\d{2}-\d{2}'|[\w.]+)"
    r"\s*([+-])\s*"
    r"INTERVAL\s+(?:'(\d+)'\s*(DAY|MONTH|YEAR)|'(\d+)\s+(DAY|MONTH|YEAR)')",
    re.IGNORECASE,
)

_INTERVAL_UNIT_MAP = {"DAY": "d", "MONTH": "M", "YEAR": "y"}


def _rewrite_interval_arithmetic(query: str) -> str:

    def _replace(m: re.Match) -> str:
        date_expr = m.group(1).strip()
        sign = m.group(2)
        n = int(m.group(3) or m.group(5))
        unit_word = (m.group(4) or m.group(6) or "").upper()
        unit = _INTERVAL_UNIT_MAP[unit_word]
        offset = -n if sign == "-" else n
        return f"dateadd('{unit}', {offset}, {date_expr})"

    return _INTERVAL_RE.sub(_replace, query)


_SUBSTRING_FROM_FOR_RE = re.compile(
    r"SUBSTRING\s*\(\s*(.*?)\s+FROM\s+(\d+)\s+FOR\s+(\d+)\s*\)",
    re.IGNORECASE,
)

_SUBSTRING_FROM_RE = re.compile(
    r"SUBSTRING\s*\(\s*(.*?)\s+FROM\s+(\d+)\s*\)",
    re.IGNORECASE,
)


def _rewrite_substring_from_for(query: str) -> str:
    query = _SUBSTRING_FROM_FOR_RE.sub(r"substring(\1, \2, \3)", query)
    query = _SUBSTRING_FROM_RE.sub(r"substring(\1, \2)", query)
    return query


_CTE_COLUMN_LIST_RE = re.compile(
    r"\bWITH\s+(\w+)\s*\([^)]+\)\s+AS\s*\(",
    re.IGNORECASE,
)


def _rewrite_cte_column_list(query: str) -> str:
    return _CTE_COLUMN_LIST_RE.sub(r"WITH \1 AS (", query)
