from __future__ import annotations

from benchbox.platforms.questdb_rewrites import exp

ORDER = 330


def applies(sql: str) -> bool:
    return sql.upper().count("SELECT") > 1


def _own_aliases(select: exp.Select) -> set[str]:
    return {source.alias_or_name.lower() for source in select.find_all(exp.Table, exp.Subquery) if source.alias_or_name}


def _is_correlated(subquery: exp.Subquery) -> bool:
    own = _own_aliases(subquery.this)
    return any(column.table and column.table.lower() not in own for column in subquery.this.find_all(exp.Column))


def _own_aggregates(select: exp.Select, item: exp.Expr) -> list[exp.Expr]:
    return [
        node
        for node in item.find_all(exp.AggFunc)
        if node.find_ancestor(exp.Select) is select and node.find_ancestor(exp.Window) is None
    ]


def _returns_one_row(inner: exp.Select) -> bool:
    if any(inner.args.get(key) for key in ("group", "having", "limit", "offset")):
        return False
    if inner.args.get("from_") is None:
        return True
    return bool(_own_aggregates(inner, inner.expressions[0]))


def _is_projected_scalar(subquery: exp.Subquery, select: exp.Select) -> bool:
    inner = subquery.this
    if not isinstance(inner, exp.Select) or len(inner.expressions) != 1 or not _returns_one_row(inner):
        return False
    node = subquery
    while node.parent is not None and node.parent is not select:
        if isinstance(node.parent, (exp.Exists, exp.In, exp.Subquery, exp.Select, exp.AggFunc)):
            return False
        node = node.parent
    return node.parent is select and node.arg_key == "expressions"


def _is_keyless_aggregate(select: exp.Select) -> bool:
    if select.args.get("from_") is None or any(
        select.args.get(key) for key in ("group", "having", "order", "limit", "offset", "qualify", "distinct")
    ):
        return False
    return any(_own_aggregates(select, item) for item in select.expressions)


def _outermost_aggregates(select: exp.Select) -> list[exp.Expr]:
    return [
        node
        for item in select.expressions
        for node in _own_aggregates(select, item)
        if node.find_ancestor(exp.AggFunc) is None
    ]


def _rewrite_select(select: exp.Select, counter: list[int]) -> None:
    if not _is_keyless_aggregate(select):
        return
    found = [
        subquery
        for item in select.expressions
        for subquery in item.find_all(exp.Subquery)
        if _is_projected_scalar(subquery, select) and not _is_correlated(subquery)
    ]
    if not found:
        return
    select.set(
        "expressions",
        [
            item
            if isinstance(item, exp.Alias)
            else exp.Alias(this=item, alias=exp.to_identifier(item.sql(), quoted=True))
            for item in select.expressions
        ],
    )
    staged: dict[str, str] = {}
    stage_items: list[exp.Expr] = []
    for aggregate in _outermost_aggregates(select):
        name = staged.get(aggregate.sql())
        if name is None:
            name = f"_ag{len(staged) + 1}"
            staged[aggregate.sql()] = name
            stage_items.append(exp.alias_(aggregate.copy(), name))
        aggregate.replace(exp.column(name, table="_ag"))
    stage = exp.Select(expressions=stage_items).from_(select.args["from_"].this.copy())
    stage.set("joins", [join.copy() for join in select.args.get("joins") or []])
    if select.args.get("where") is not None:
        stage.set("where", select.args["where"].copy())
    select.set("from_", exp.From(this=exp.Subquery(this=stage, alias=exp.TableAlias(this=exp.to_identifier("_ag")))))
    select.set("where", None)
    joins = []
    for subquery in found:
        counter[0] += 1
        name = f"_ka{counter[0]}"
        value = f"{name}_v"
        inner = subquery.this.copy()
        inner.set("expressions", [exp.alias_(inner.expressions[0], value)])
        source = exp.Subquery(this=inner, alias=exp.TableAlias(this=exp.to_identifier(name)))
        subquery.replace(exp.column(value, table=name))
        joins.append(exp.Join(this=source, kind="CROSS"))
    select.set("joins", joins)


def transform(expression: exp.Expr) -> exp.Expr:
    counter = [0]
    for select in reversed(list(expression.find_all(exp.Select))):
        _rewrite_select(select, counter)
    return expression
