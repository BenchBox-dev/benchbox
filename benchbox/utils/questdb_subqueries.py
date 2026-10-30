from __future__ import annotations

from collections.abc import Callable

from benchbox.utils.dialect_utils import apply_rewrite, sql_expressions
from benchbox.utils.questdb_subquery_schemas import registered_schema

exp = sql_expressions()

QUESTDB = "questdb"
_DERIVED_PARENTS = (exp.From, exp.Join, exp.SetOperation, exp.Subquery)
_UNPLAIN = ("joins", "group", "having", "qualify", "order", "limit", "offset", "with_")


def _has_subquery_predicate(expression: exp.Expr) -> bool:
    if expression.find(exp.Exists):
        return True
    if any(predicate.args.get("query") for predicate in expression.find_all(exp.In)):
        return True
    return any(
        isinstance(subquery.this, exp.Query) and not isinstance(subquery.parent, _DERIVED_PARENTS)
        for subquery in expression.find_all(exp.Subquery)
    )


def _has_comma_join(expression: exp.Expr) -> bool:
    return any(
        isinstance(join.this, (exp.Table, exp.Subquery))
        and not any(value for key, value in join.args.items() if key != "this")
        for select in expression.find_all(exp.Select)
        for join in select.args.get("joins") or []
    )


def _schema_for(expression: exp.Expr) -> tuple[dict[str, dict[str, str]], frozenset[str]] | None:
    ctes = {cte.alias_or_name.lower() for cte in expression.find_all(exp.CTE)}
    tables = {table.name.lower() for table in expression.find_all(exp.Table) if table.name.lower() not in ctes}
    columns, non_null = registered_schema()
    if not tables or not tables <= columns.keys():
        return None
    known = {column for table in tables for column in columns[table]}
    known |= {alias.alias.lower() for alias in expression.find_all(exp.Alias)}
    known |= {column.name.lower() for alias in expression.find_all(exp.TableAlias) for column in alias.columns}
    if any(column.name.lower() not in known for column in expression.find_all(exp.Column)):
        return None
    return {table: columns[table] for table in tables}, non_null


def _flatten_and(node: exp.Expr) -> list[exp.Expr]:
    if isinstance(node, exp.Paren):
        return _flatten_and(node.this)
    if isinstance(node, exp.And):
        return _flatten_and(node.left) + _flatten_and(node.right)
    return [node]


def _ref(node: exp.Expr) -> str:
    return node.alias_or_name.lower()


def _is_null(node: exp.Expr) -> exp.Expr:
    return exp.Is(this=node, expression=exp.Null())


def _is_not_null(node: exp.Expr) -> exp.Expr:
    return exp.Not(this=_is_null(node))


_ORDERING = {exp.LT: exp.GTE, exp.LTE: exp.GT, exp.GT: exp.LTE, exp.GTE: exp.LT}
_FLIP = {exp.LT: exp.GT, exp.LTE: exp.GTE, exp.GT: exp.LT, exp.GTE: exp.LTE, exp.NEQ: exp.NEQ}


def _comparison(
    predicate: exp.Expr, inner_refs: set[str], outer: set[str]
) -> tuple[exp.Column, exp.Column, type] | None:
    columns = list(predicate.find_all(exp.Column))
    kind = type(predicate)
    if kind not in (exp.EQ, *_FLIP) or len(columns) != 2:
        return None
    left, right = predicate.left, predicate.right
    if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
        return None
    if left.table.lower() in inner_refs and right.table.lower() in outer:
        return right, left, kind
    if right.table.lower() in inner_refs and left.table.lower() in outer:
        return left, right, _FLIP.get(kind, kind)
    return None


def _inequality_exists(select: exp.Select, conjunct: exp.Expr, alias: str) -> tuple[exp.Join, exp.Expr] | None:
    negated = isinstance(conjunct, exp.Not) and isinstance(conjunct.this, exp.Exists)
    exists = conjunct.this if negated else conjunct
    if not isinstance(exists, exp.Exists):
        return None
    inner = exists.this
    while isinstance(inner, exp.Subquery) and not inner.args.get("alias"):
        inner = inner.this
    if not isinstance(inner, exp.Select) or not inner.args.get("where") or not inner.args.get("from_"):
        return None
    if (
        any(inner.args.get(key) for key in _UNPLAIN if key != "joins")
        or inner.args.get("distinct")
        or inner.find(exp.Window)
    ):
        return None
    source = inner.args["from_"].this
    inner_joins = inner.args.get("joins") or []
    if not (isinstance(source, exp.Table) or (isinstance(source, exp.Subquery) and source.alias)) or not all(
        isinstance(join.this, exp.Table)
        and join.args.get("on")
        and not join.args.get("side")
        and not join.args.get("kind")
        and not join.find(exp.Subquery)
        for join in inner_joins
    ):
        return None
    inner_refs = {_ref(source), *(_ref(join.this) for join in inner_joins)}
    outer = {_ref(select.args["from_"].this)} | {_ref(join.this) for join in select.args.get("joins") or []}
    if inner_refs & outer:
        return None

    equalities: list[tuple[exp.Column, exp.Column]] = []
    comparisons: list[tuple[exp.Column, exp.Column, type]] = []
    local: list[exp.Expr] = []
    for predicate in _flatten_and(inner.args["where"].this):
        columns = list(predicate.find_all(exp.Column))
        if not any(column.table.lower() in outer for column in columns):
            if predicate.find(exp.Subquery) or any(column.table.lower() not in inner_refs for column in columns):
                return None
            local.append(predicate)
            continue
        matched = _comparison(predicate, inner_refs, outer)
        if matched is None:
            return None
        if matched[2] is exp.EQ:
            equalities.append(matched[:2])
        else:
            comparisons.append(matched)
    if not equalities or len(comparisons) != 1:
        return None

    value_outer, value_inner, kind = comparisons[0]
    keys = [exp.alias_(inner_key.copy(), f"{alias}_k{n}") for n, (_, inner_key) in enumerate(equalities)]
    extremes = {
        "lo": exp.Min(this=value_inner.copy()) if kind in (exp.NEQ, exp.LT, exp.LTE) else None,
        "hi": exp.Max(this=value_inner.copy()) if kind in (exp.NEQ, exp.GT, exp.GTE) else None,
    }
    derived = exp.select(
        *keys, *[exp.alias_(aggregate, f"{alias}_{name}") for name, aggregate in extremes.items() if aggregate]
    ).from_(source.copy())
    derived.set("joins", [join.copy() for join in inner_joins])
    derived = derived.where(
        exp.and_(*[_is_not_null(inner_key.copy()) for _, inner_key in equalities], *[p.copy() for p in local]),
        copy=False,
    )
    derived.set("group", exp.Group(expressions=[inner_key.copy() for _, inner_key in equalities]))
    condition = exp.and_(
        *[
            exp.EQ(this=outer_key.copy(), expression=exp.column(f"{alias}_k{n}", table=alias))
            for n, (outer_key, _) in enumerate(equalities)
        ]
    )
    join = exp.Join(
        this=exp.Subquery(this=derived, alias=exp.TableAlias(this=exp.to_identifier(alias))),
        on=condition,
        side="LEFT" if negated else None,
    )
    low = exp.column(f"{alias}_lo", table=alias)
    high = exp.column(f"{alias}_hi", table=alias)
    value = value_outer.copy()
    if kind is exp.NEQ:
        if negated:
            return join, exp.Paren(
                this=exp.or_(
                    _is_null(low.copy()),
                    _is_null(value.copy()),
                    exp.Paren(
                        this=exp.and_(
                            exp.EQ(this=value.copy(), expression=low), exp.EQ(this=value.copy(), expression=high)
                        )
                    ),
                )
            )
        return join, exp.and_(
            _is_not_null(low.copy()),
            _is_not_null(value.copy()),
            exp.Paren(
                this=exp.or_(exp.NEQ(this=value.copy(), expression=low), exp.NEQ(this=value.copy(), expression=high))
            ),
        )
    extreme = low if kind in (exp.LT, exp.LTE) else high
    if negated:
        return join, exp.Paren(
            this=exp.or_(
                _is_null(extreme.copy()),
                _is_null(value.copy()),
                _ORDERING[kind](this=extreme.copy(), expression=value.copy()),
            )
        )
    return join, exp.and_(
        _is_not_null(extreme.copy()),
        _is_not_null(value.copy()),
        _FLIP[kind](this=value.copy(), expression=extreme.copy()),
    )


def _lower_conjuncts(
    expression: exp.Expr,
    prefix: str,
    lower: Callable[[exp.Select, exp.Expr, str], tuple[list[exp.Join], exp.Expr] | None],
) -> exp.Expr:
    occupied = {identifier.name.lower() for identifier in expression.find_all(exp.Identifier)}
    counter = 0
    for select in list(expression.find_all(exp.Select)):
        where = select.args.get("where")
        if not where or not select.args.get("from_"):
            continue
        sources = [select.args["from_"].this, *(join.this for join in select.args.get("joins") or [])]
        if not all(isinstance(source, (exp.Table, exp.Subquery)) for source in sources):
            continue
        kept: list[exp.Expr] = []
        joins: list[exp.Join] = []
        for conjunct in _flatten_and(where.this):
            alias = f"{prefix}{counter + 1}"
            while alias in occupied:
                counter += 1
                alias = f"{prefix}{counter + 1}"
            lowered = lower(select, conjunct, alias)
            if lowered is None:
                kept.append(conjunct)
                continue
            counter += 1
            occupied.add(alias)
            joins.extend(lowered[0])
            kept.append(lowered[1])
        if joins:
            select.set("joins", [*(select.args.get("joins") or []), *joins])
            where.set("this", exp.and_(*kept))
    return expression


def _inequality_exists_parts(
    select: exp.Select, conjunct: exp.Expr, alias: str
) -> tuple[list[exp.Join], exp.Expr] | None:
    lowered = _inequality_exists(select, conjunct, alias)
    return None if lowered is None else ([lowered[0]], lowered[1])


def _not_in_parts(select: exp.Select, conjunct: exp.Expr, alias: str) -> tuple[list[exp.Join], exp.Expr] | None:
    if not isinstance(conjunct, exp.Not) or not isinstance(conjunct.this, exp.In):
        return None
    predicate = conjunct.this
    query = predicate.args.get("query")
    if query is None or predicate.args.get("expressions") or predicate.args.get("unnest"):
        return None
    inner = query.this if isinstance(query, exp.Subquery) else query
    while isinstance(inner, exp.Subquery):
        inner = inner.this
    if not isinstance(inner, exp.Select) or len(inner.expressions) != 1:
        return None
    output = inner.expressions[0].alias_or_name
    if not output or isinstance(inner.expressions[0].unalias(), exp.Star):
        return None
    defined = {_ref(table) for table in inner.find_all(exp.Table)}
    defined |= {subquery.alias.lower() for subquery in inner.find_all(exp.Subquery) if subquery.alias}
    if any(column.table.lower() not in defined for column in inner.find_all(exp.Column)):
        return None
    value = predicate.this

    def derived(projections: list[exp.Expr], suffix: str) -> exp.Subquery:
        source = exp.Subquery(this=inner.copy(), alias=exp.TableAlias(this=exp.to_identifier(f"{alias}_{suffix}")))
        return exp.select(*projections).from_(source)

    keys = derived([exp.alias_(exp.column(output), f"{alias}_k")], "s")
    keys.set("group", exp.Group(expressions=[exp.column(output)]))
    counts = derived(
        [
            exp.alias_(exp.Count(this=exp.Star()), f"{alias}_n"),
            exp.alias_(exp.Count(this=exp.column(output)), f"{alias}_nn"),
        ],
        "t",
    )
    members = exp.Join(
        this=exp.Subquery(this=keys, alias=exp.TableAlias(this=exp.to_identifier(alias))),
        on=exp.EQ(this=value.copy(), expression=exp.column(f"{alias}_k", table=alias)),
        side="LEFT",
    )
    totals = exp.Join(
        this=exp.Subquery(this=counts, alias=exp.TableAlias(this=exp.to_identifier(f"{alias}_c"))),
        kind="CROSS",
    )
    total = exp.column(f"{alias}_n", table=f"{alias}_c")
    non_null = exp.column(f"{alias}_nn", table=f"{alias}_c")
    return [members, totals], exp.Paren(
        this=exp.or_(
            exp.EQ(this=total.copy(), expression=exp.Literal.number(0)),
            exp.Paren(
                this=exp.and_(
                    _is_not_null(value.copy()),
                    _is_null(exp.column(f"{alias}_k", table=alias)),
                    exp.EQ(this=total.copy(), expression=non_null),
                )
            ),
        )
    )


def _tables_of(node: exp.Expr) -> set[str]:
    return {column.table.lower() for column in node.find_all(exp.Column)}


def _is_cross(join: exp.Join) -> bool:
    return (
        isinstance(join.this, (exp.Table, exp.Subquery))
        and not any(join.args.get(key) for key in ("on", "using", "side", "method"))
        and join.args.get("kind") in (None, "", "CROSS")
    )


def _attachable(remaining: list[exp.Expr], current: str, available: set[str]) -> list[exp.Expr]:
    attached = []
    for predicate in remaining:
        if not isinstance(predicate, exp.EQ) or predicate.find(exp.Subquery):
            continue
        left, right = _tables_of(predicate.left), _tables_of(predicate.right)
        if (left == {current} and right and right <= available) or (right == {current} and left and left <= available):
            attached.append(predicate)
    return attached


def _cross_joins_to_inner(expression: exp.Expr) -> exp.Expr:
    for select in expression.find_all(exp.Select):
        joins = select.args.get("joins") or []
        where = select.args.get("where")
        from_ = select.args.get("from_")
        if not where or not from_ or not any(_is_cross(join) for join in joins):
            continue
        leading = 0
        while leading < len(joins) and _is_cross(joins[leading]):
            leading += 1
        pending, trailing = list(joins[:leading]), list(joins[leading:])
        remaining = _flatten_and(where.this)
        available = {_ref(from_.this)}
        ordered: list[exp.Join] = []
        while pending:
            join = next(
                (candidate for candidate in pending if _attachable(remaining, _ref(candidate.this), available)),
                pending[0],
            )
            pending.remove(join)
            current = _ref(join.this)
            attached = _attachable(remaining, current, available)
            if attached:
                remaining = [predicate for predicate in remaining if predicate not in attached]
                join.set("on", exp.and_(*[predicate.copy() for predicate in attached]))
                join.set("kind", None)
            available.add(current)
            ordered.append(join)
        select.set("joins", [*ordered, *trailing])
        if remaining:
            where.set("this", exp.and_(*remaining))
        else:
            select.set("where", None)
    return expression


def _cte_extremum_in_subquery(select: exp.Select, conjunct: exp.Expr, name: str) -> bool:
    if not isinstance(conjunct, exp.EQ):
        return False
    column, scalar = conjunct.left, conjunct.right
    if not isinstance(column, exp.Column):
        column, scalar = scalar, column
    if not isinstance(column, exp.Column) or not isinstance(scalar, exp.Subquery):
        return False
    inner = scalar.this
    if not isinstance(inner, exp.Select) or len(inner.expressions) != 1:
        return False
    aggregate = inner.expressions[0].unalias()
    source = inner.args.get("from_")
    if (
        not isinstance(aggregate, (exp.Max, exp.Min))
        or not isinstance(aggregate.this, exp.Column)
        or aggregate.this.name.lower() != column.name.lower()
        or not source
        or not isinstance(source.this, exp.Table)
        or any(inner.args.get(key) for key in (*_UNPLAIN, "where", "distinct"))
    ):
        return False
    cte = source.this.name.lower()
    definition = next((c for c in select.root().find_all(exp.CTE) if c.alias_or_name.lower() == cte), None)
    if definition is None or not isinstance(definition.this, exp.Select):
        return False
    declared = definition.args["alias"].columns if definition.args.get("alias") else []
    outputs = [column.name for column in declared] or [
        projection.alias_or_name for projection in definition.this.expressions
    ]
    if not all(outputs):
        return False
    sources = [select.args["from_"].this, *(join.this for join in select.args.get("joins") or [])]
    target = next((src for src in sources if isinstance(src, exp.Table) and src.name.lower() == cte), None)
    if target is None or (column.table and column.table.lower() != _ref(target)):
        return False
    window = type(aggregate)(this=aggregate.this.copy())
    derived = exp.select(*[exp.column(output) for output in outputs], exp.alias_(exp.Window(this=window), name)).from_(
        exp.table_(source.this.name)
    )
    alias = target.alias or target.name
    target.replace(exp.Subquery(this=derived, alias=exp.TableAlias(this=exp.to_identifier(alias))))
    conjunct.replace(exp.EQ(this=column.copy(), expression=exp.column(name)))
    return True


def _single_evaluation_extremum(expression: exp.Expr) -> exp.Expr:
    occupied = {identifier.name.lower() for identifier in expression.find_all(exp.Identifier)}
    counter = 0
    for select in list(expression.find_all(exp.Select)):
        where = select.args.get("where")
        if not where or not select.args.get("from_") or not expression.find(exp.CTE):
            continue
        for conjunct in _flatten_and(where.this):
            name = f"_qx_agg_{counter + 1}"
            while name in occupied:
                counter += 1
                name = f"_qx_agg_{counter + 1}"
            if _cte_extremum_in_subquery(select, conjunct, name):
                counter += 1
                occupied.add(name)
    return expression


def _unwrap_where_parentheses(expression: exp.Expr) -> exp.Expr:
    for where in expression.find_all(exp.Where):
        while isinstance(where.this, exp.Paren):
            where.set("this", where.this.this)
    return expression


def _flatten_or(node: exp.Expr) -> list[exp.Expr]:
    if isinstance(node, exp.Paren):
        return _flatten_or(node.this)
    if isinstance(node, exp.Or):
        return _flatten_or(node.left) + _flatten_or(node.right)
    return [node]


def _factor_common_conjuncts(expression: exp.Expr) -> exp.Expr:
    for where in list(expression.find_all(exp.Where)):
        if where.parent_select is None or where.parent_select.parent is None:
            continue
        branches = [_flatten_and(branch) for branch in _flatten_or(where.this)]
        if len(branches) < 2:
            continue
        common = [conjunct for conjunct in branches[0] if all(conjunct in branch for branch in branches[1:])]
        if not common:
            continue
        rests = [[conjunct for conjunct in branch if conjunct not in common] for branch in branches]
        parts = [conjunct.copy() for conjunct in common]
        if all(rests):
            parts.append(exp.Paren(this=exp.or_(*[exp.and_(*[c.copy() for c in rest]) for rest in rests])))
        where.set("this", exp.and_(*parts))
    return expression


_COUNT_TESTS = {
    (exp.GT, 0): False,
    (exp.GTE, 1): False,
    (exp.NEQ, 0): False,
    (exp.EQ, 0): True,
    (exp.LT, 1): True,
}
_FLIPPED = {exp.GT: exp.LT, exp.GTE: exp.LTE, exp.LT: exp.GT, exp.LTE: exp.GTE, exp.EQ: exp.EQ, exp.NEQ: exp.NEQ}


def _count_test_to_exists(expression: exp.Expr) -> exp.Expr:
    for comparison in list(expression.find_all(exp.Binary)):
        comparison_type = type(comparison)
        if comparison_type not in _FLIPPED:
            continue
        scalar, number = comparison.left, comparison.right
        if isinstance(scalar, exp.Literal):
            scalar, number = number, scalar
            comparison_type = _FLIPPED[comparison_type]
        if (
            not isinstance(scalar, exp.Subquery)
            or not isinstance(number, exp.Literal)
            or number.is_string
            or not number.is_int
            or (comparison_type, int(number.name)) not in _COUNT_TESTS
        ):
            continue
        inner = scalar.this
        if (
            not isinstance(inner, exp.Select)
            or len(inner.expressions) != 1
            or not isinstance(inner.expressions[0].unalias(), exp.Count)
            or not isinstance(inner.expressions[0].unalias().this, exp.Star)
            or any(
                inner.args.get(key) for key in ("group", "having", "qualify", "limit", "offset", "with_", "distinct")
            )
            or not inner.args.get("from_")
        ):
            continue
        probe = inner.copy()
        probe.set("expressions", [exp.Literal.number(1)])
        probe.set("order", None)
        replacement: exp.Expr = exp.Exists(this=probe)
        if _COUNT_TESTS[(comparison_type, int(number.name))]:
            replacement = exp.Not(this=replacement)
        comparison.replace(replacement)
    return expression


def _add_single_row_source(expression: exp.Expr) -> exp.Expr:
    occupied = {identifier.name.lower() for identifier in expression.find_all(exp.Identifier)}
    for select in list(expression.find_all(exp.Select)):
        if select.args.get("from_") or not any(
            subquery.parent_select is select and not isinstance(subquery.parent, _DERIVED_PARENTS)
            for subquery in select.find_all(exp.Subquery)
        ):
            continue
        name = "_qx_dual"
        while name in occupied:
            name += "_"
        occupied.add(name)
        select.set(
            "from_",
            exp.From(
                this=exp.Subquery(
                    this=exp.select(exp.alias_(exp.Literal.number(1), "_qx_one")),
                    alias=exp.TableAlias(this=exp.to_identifier(name)),
                )
            ),
        )
    return expression


def _reject_null_correlation_keys(expression: exp.Expr) -> exp.Expr:
    for join in expression.find_all(exp.Join):
        derived = join.this
        on = join.args.get("on")
        if not isinstance(derived, exp.Subquery) or not isinstance(derived.this, exp.Select) or on is None:
            continue
        alias = derived.alias.lower()
        if not alias.startswith("_u_"):
            continue
        keys = {
            column.name.lower()
            for column in on.find_all(exp.Column)
            if column.table.lower() == alias and column.name.lower().startswith("_u_")
        }
        select = derived.this
        for projection in select.expressions:
            if isinstance(projection, exp.Alias) and projection.alias.lower() in keys:
                guard = _is_not_null(projection.this.copy())
                where = select.args.get("where")
                select.set("where", exp.Where(this=exp.and_(where.this, guard) if where else guard))
    return expression


def rewrite_subqueries(expression: exp.Expr) -> exp.Expr:
    if not isinstance(expression, exp.Query) or not (
        _has_subquery_predicate(expression) or _has_comma_join(expression)
    ):
        return expression
    matched = _schema_for(expression)
    if matched is None:
        return expression
    schema, non_null = matched
    expression = _add_single_row_source(expression)
    expression = _count_test_to_exists(expression)
    expression = _unwrap_where_parentheses(expression)
    expression = _factor_common_conjuncts(expression)
    expression = _single_evaluation_extremum(expression)
    expression = apply_rewrite("not_in_subquery_to_not_exists", expression, QUESTDB, non_null_columns=non_null)
    expression = apply_rewrite("decorrelate_subqueries", expression, QUESTDB, schema=schema)
    expression = _reject_null_correlation_keys(expression)
    expression = _cross_joins_to_inner(expression)
    expression = _lower_conjuncts(expression, "_qx_ne_", _inequality_exists_parts)
    return _lower_conjuncts(expression, "_qx_ni_", _not_in_parts)
