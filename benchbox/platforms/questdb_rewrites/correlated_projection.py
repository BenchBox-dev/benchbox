from __future__ import annotations

from benchbox.platforms.questdb_rewrites import exp

ORDER = 345
_REJECTED = ("group", "having", "limit", "offset", "qualify")


def applies(sql: str) -> bool:
    return sql.upper().count("SELECT") > 1


def _flatten_and(node: exp.Expr) -> list[exp.Expr]:
    if isinstance(node, exp.Paren):
        return _flatten_and(node.this)
    if isinstance(node, exp.And):
        return _flatten_and(node.left) + _flatten_and(node.right)
    return [node]


def _defined_names(select: exp.Select) -> set[str]:
    return {source.alias_or_name.lower() for source in select.find_all(exp.Table, exp.Subquery) if source.alias_or_name}


def _own_sources(select: exp.Select) -> set[str]:
    sources = [select.args["from_"].this] if select.args.get("from_") is not None else []
    sources += [join.this for join in select.args.get("joins") or []]
    return {source.alias_or_name.lower() for source in sources if source.alias_or_name}


def _is_scalar_position(subquery: exp.Subquery, select: exp.Select) -> bool:
    node = subquery
    while node.parent is not None and node.parent is not select:
        if isinstance(node.parent, (exp.Exists, exp.In, exp.Subquery, exp.Select)):
            return False
        node = node.parent
    return node.parent is select and node.arg_key in ("expressions", "order")


def _aggregates(inner: exp.Select) -> list[exp.Expr]:
    return [node for node in inner.expressions[0].find_all(exp.AggFunc) if node.find_ancestor(exp.Window) is None]


def _split_predicates(
    inner: exp.Select, outer_sources: set[str]
) -> tuple[list[tuple[exp.Expr, exp.Expr]], list[exp.Expr]] | None:
    inner_names = _defined_names(inner)
    keys: list[tuple[exp.Expr, exp.Expr]] = []
    local: list[exp.Expr] = []
    for conjunct in _flatten_and(inner.args["where"].this):
        tables = {column.table.lower() for column in conjunct.find_all(exp.Column) if column.table}
        if tables <= inner_names:
            local.append(conjunct)
            continue
        if not isinstance(conjunct, exp.EQ):
            return None
        for inner_side, outer_side in ((conjunct.left, conjunct.right), (conjunct.right, conjunct.left)):
            inner_tables = {c.table.lower() for c in inner_side.find_all(exp.Column)}
            outer_tables = {c.table.lower() for c in outer_side.find_all(exp.Column)}
            if (
                inner_tables
                and inner_tables <= inner_names
                and outer_tables
                and outer_tables <= outer_sources
                and not outer_tables & inner_names
                and not inner_side.find(exp.Subquery)
                and not outer_side.find(exp.Subquery)
            ):
                keys.append((inner_side, outer_side))
                break
        else:
            return None
    return (keys, local) if keys else None


def _defaults_to_zero(projection: exp.Expr) -> bool | None:
    target = projection.this if isinstance(projection, exp.Alias) else projection
    if isinstance(target, exp.Count):
        return True
    return None if target.find(exp.Count) is not None else False


def _predicate_scope(inner: exp.Select) -> tuple[exp.Select, exp.Subquery | None] | None:
    if inner.args.get("where") is not None:
        return inner, None
    source = inner.args["from_"].this
    if (
        isinstance(source, exp.Subquery)
        and source.alias
        and isinstance(source.this, exp.Select)
        and not inner.args.get("joins")
        and source.this.args.get("where") is not None
        and source.this.args.get("from_") is not None
        and not any(source.this.args.get(key) for key in (*_REJECTED, "distinct"))
        and not source.this.find(exp.Window)
    ):
        return source.this, source
    return None


def _plan(
    subquery: exp.Subquery, select: exp.Select
) -> tuple[exp.Select, list[exp.Expr], bool, exp.Subquery | None] | None:
    inner = subquery.this
    if not isinstance(inner, exp.Select) or len(inner.expressions) != 1:
        return None
    if any(inner.args.get(key) for key in _REJECTED) or inner.args.get("distinct") or inner.find(exp.Window):
        return None
    if inner.args.get("from_") is None or not _aggregates(inner):
        return None
    scope = _predicate_scope(inner)
    if scope is None:
        return None
    split = _split_predicates(scope[0], _own_sources(select))
    zero = _defaults_to_zero(inner.expressions[0])
    if split is None or zero is None:
        return None
    keys, local = split
    grouped = select.args.get("group")
    if grouped is not None:
        group_sql = {key.sql() for key in grouped.expressions}
        if any(outer.sql() not in group_sql for _, outer in keys):
            return None
    elif any(
        isinstance(item.this if isinstance(item, exp.Alias) else item, exp.AggFunc) for item in select.expressions
    ):
        return None
    derived = inner.copy()
    value = derived.expressions[0]
    derived.set("expressions", [exp.alias_(value.this if isinstance(value, exp.Alias) else value, "_cv")])
    target = derived.args["from_"].this.this if scope[1] is not None else derived
    target.set("where", exp.Where(this=exp.and_(*[predicate.copy() for predicate in local])) if local else None)
    return derived, keys, zero, scope[1]


def _add_keys(derived: exp.Select, keys: list[exp.Expr], staged: exp.Subquery | None) -> None:
    for index, (inner_side, _) in enumerate(keys):
        name = f"_ck{index}"
        if staged is None:
            derived.expressions.append(exp.alias_(inner_side.copy(), name))
            continue
        derived.args["from_"].this.this.expressions.append(exp.alias_(inner_side.copy(), name))
        derived.expressions.append(exp.column(name, table=staged.alias))
    keyed = (
        [exp.column(f"_ck{index}", table=staged.alias) for index in range(len(keys))]
        if staged is not None
        else [inner_side.copy() for inner_side, _ in keys]
    )
    derived.set("group", exp.Group(expressions=keyed))


def _rewrite_select(select: exp.Select, counter: list[int]) -> None:
    joins = list(select.args.get("joins") or [])
    reuse: dict[str, str] = {}
    grouped = select.args.get("group") is not None
    candidates = [
        node
        for root in (*select.expressions, select.args.get("order"))
        if root is not None
        for node in root.find_all(exp.Subquery)
        if _is_scalar_position(node, select)
    ]
    for subquery in candidates:
        plan = _plan(subquery, select)
        if plan is None:
            continue
        derived, keys, zero, staged = plan
        signature = subquery.sql()
        alias = reuse.get(signature)
        if alias is None:
            counter[0] += 1
            alias = f"_cs{counter[0]}"
            reuse[signature] = alias
            _add_keys(derived, keys, staged)
            condition = exp.and_(
                *[
                    exp.EQ(this=exp.column(f"_ck{index}", table=alias), expression=outer_side.copy())
                    for index, (_, outer_side) in enumerate(keys)
                ]
            )
            source = exp.Subquery(this=derived, alias=exp.TableAlias(this=exp.to_identifier(alias)))
            joins.append(exp.Join(this=source, side="LEFT", on=condition))
        reference: exp.Expr = exp.column("_cv", table=alias)
        if grouped:
            reference = exp.Max(this=reference)
        if zero:
            reference = exp.Coalesce(this=reference, expressions=[exp.Literal.number(0)])
        subquery.replace(reference)
    select.set("joins", joins)


def transform(expression: exp.Expr) -> exp.Expr:
    counter = [0]
    for select in reversed(list(expression.find_all(exp.Select))):
        _rewrite_select(select, counter)
    return expression
