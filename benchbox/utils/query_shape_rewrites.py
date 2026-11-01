from __future__ import annotations

from collections.abc import Sequence

_DERIVED_SOURCE_ALIAS = "hoisted_source"
_ORDERED_ROWS_ALIAS = "ordered_rows"


def drop_empty_group_by(expression, *, copy: bool = True):
    exp = sql_expressions()
    tree = expression.copy() if copy else expression
    for select in tree.find_all(exp.Select):
        group = select.args.get("group")
        if group is None:
            continue
        remaining = [item for item in group.expressions if not (isinstance(item, exp.Tuple) and not item.expressions)]
        if len(remaining) == len(group.expressions):
            continue
        if remaining or any(group.args.get(key) for key in ("grouping_sets", "cube", "rollup", "totals")):
            group.set("expressions", remaining)
        else:
            select.set("group", None)
    return tree


def unwrap_single_grouping_set(expression, *, copy: bool = True):
    exp = sql_expressions()
    tree = expression.copy() if copy else expression
    for group in list(tree.find_all(exp.Group)):
        if len(group.expressions) != 1 or not isinstance(group.expressions[0], exp.GroupingSets):
            continue
        sets = group.expressions[0].expressions
        if len(sets) != 1:
            continue
        only = sets[0]
        keys = only.expressions if isinstance(only, exp.Tuple) else [only]
        group.set("expressions", [key.copy() for key in keys])
    return tree


def add_dual_source_to_filtered_selects(expression, *, copy: bool = True):
    exp = sql_expressions()
    tree = expression.copy() if copy else expression
    for select in tree.find_all(exp.Select):
        if select.args.get("from_") is None and select.args.get("where") is not None:
            select.set("from_", exp.From(this=exp.Var(this="DUAL")))
    return tree


def inline_sourceless_exists(expression, *, copy: bool = True):
    exp = sql_expressions()
    tree = expression.copy() if copy else expression
    for node in list(tree.find_all(exp.Exists)):
        inner = node.this.this if isinstance(node.this, exp.Subquery) else node.this
        if not isinstance(inner, exp.Select) or inner.args.get("from_") is not None:
            continue
        where = inner.args.get("where")
        if where is None or any(inner.args.get(key) for key in ("group", "having", "limit", "joins")):
            continue
        node.replace(exp.paren(where.this.copy()))
    return tree


def drop_correlated_scalar_group_by(expression, *, columns: Sequence[str], copy: bool = True):
    exp = sql_expressions()
    tree = expression.copy() if copy else expression
    wanted = {name.lower() for name in columns}
    for subquery in list(tree.find_all(exp.Subquery)):
        inner = subquery.this
        if not isinstance(inner, exp.Select) or not isinstance(subquery.parent, exp.Binary):
            continue
        group = inner.args.get("group")
        where = inner.args.get("where")
        if group is None or where is None:
            continue
        keys = group.expressions
        if not keys or not all(isinstance(key, exp.Column) for key in keys):
            continue
        if {key.name.lower() for key in keys} != wanted:
            continue
        if all(_pinned_to_other_column(key, where) for key in keys):
            inner.set("group", None)
    return tree


def fold_scalar_having_into_case(expression, *, copy: bool = True):
    exp = sql_expressions()
    tree = expression.copy() if copy else expression
    for subquery in list(tree.find_all(exp.Subquery)):
        inner = subquery.this
        if not isinstance(inner, exp.Select) or not isinstance(subquery.parent, exp.Binary):
            continue
        having = inner.args.get("having")
        if having is None or inner.args.get("group") is not None or len(inner.expressions) != 1:
            continue
        projection = inner.expressions[0]
        value = projection.this if isinstance(projection, exp.Alias) else projection
        guarded = exp.Case(ifs=[exp.If(this=having.this.copy(), true=value.copy())])
        value.replace(guarded)
        inner.set("having", None)
    return tree


def _pinned_to_other_column(key, where) -> bool:
    exp = sql_expressions()
    for conjunct in where.this.flatten():
        if not isinstance(conjunct, exp.EQ):
            continue
        left, right = conjunct.this, conjunct.expression
        if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
            continue
        for mine, other in ((left, right), (right, left)):
            if mine == key and other.name.lower() != key.name.lower():
                return True
    return False


def qualify_aggregated_columns(expression, *, tables: dict[str, str], copy: bool = True):
    exp = sql_expressions()
    tree = expression.copy() if copy else expression
    wanted = {name.lower(): table for name, table in tables.items()}
    for aggregate in list(tree.find_all(exp.AggFunc)):
        for column in aggregate.find_all(exp.Column):
            table = wanted.get(column.name.lower())
            if table is not None and not column.table:
                column.set("table", exp.to_identifier(table))
    return tree


def wrap_order_by_subquery_aliases(expression, *, copy: bool = True):
    exp = sql_expressions()
    tree = expression.copy() if copy else expression
    if not isinstance(tree, exp.Select):
        return tree
    order = tree.args.get("order")
    if order is None:
        return tree
    projected = {projection.alias_or_name for projection in tree.expressions}
    subquery_aliases = {
        projection.alias
        for projection in tree.expressions
        if isinstance(projection, exp.Alias) and projection.find(exp.Subquery) is not None
    }
    ordered = {column.name for column in order.find_all(exp.Column) if not column.table}
    if not ordered & subquery_aliases:
        return tree
    if not ordered <= projected:
        raise ValueError("ORDER BY references a column that is not projected")
    limit = tree.args.get("limit")
    offset = tree.args.get("offset")
    tree.set("order", None)
    tree.set("limit", None)
    tree.set("offset", None)
    outer = exp.select("*").from_(tree.subquery(_ORDERED_ROWS_ALIAS))
    outer.set("order", order)
    if limit is not None:
        outer.set("limit", limit)
    if offset is not None:
        outer.set("offset", offset)
    return outer


def inline_named_windows(expression, *, copy: bool = True):
    exp = sql_expressions()
    tree = expression.copy() if copy else expression
    for select in tree.find_all(exp.Select):
        definitions = select.args.get("windows")
        if not definitions:
            continue
        named = {definition.this.name.lower(): definition for definition in definitions}
        for use in list(select.find_all(exp.Window)):
            alias = use.args.get("alias")
            if alias is None or use.find_ancestor(exp.Select) is not select:
                continue
            definition = named.get(alias.name.lower())
            if definition is None:
                continue
            use.set("alias", None)
            for key in ("partition_by", "order", "spec"):
                value = definition.args.get(key)
                if value is not None:
                    use.set(key, [item.copy() for item in value] if isinstance(value, list) else value.copy())
        select.set("windows", None)
    return tree


def alias_order_by_projections(expression, *, copy: bool = True):
    exp = sql_expressions()
    tree = expression.copy() if copy else expression
    for select in tree.find_all(exp.Select):
        order = select.args.get("order")
        if order is None:
            continue
        projected = {
            projection.this.sql(): projection.args["alias"]
            for projection in select.expressions
            if isinstance(projection, exp.Alias) and projection.this.find(exp.Subquery, exp.Window) is not None
        }
        if not projected:
            continue
        for node in list(order.walk()):
            alias = projected.get(node.sql())
            if alias is not None:
                node.replace(exp.Column(this=alias.copy()))
    return tree


def date_column_integer_arithmetic(expression, *, copy: bool = True):
    exp = sql_expressions()
    tree = expression.copy() if copy else expression
    for node in list(tree.find_all(exp.Add, exp.Sub)):
        column, amount = node.this, node.expression
        if not (isinstance(column, exp.Column) and column.name.lower().endswith("_date")):
            continue
        if not (isinstance(amount, exp.Literal) and not amount.is_string and amount.name.isdigit()):
            continue
        builder = exp.DateAdd if isinstance(node, exp.Add) else exp.DateSub
        node.replace(builder(this=column.copy(), expression=amount.copy(), unit=exp.var("DAY")))
    return tree


def rollup_empty_input_row(expression, *, copy: bool = True):
    exp = sql_expressions()
    tree = expression.copy() if copy else expression
    for select in reversed(list(tree.find_all(exp.Select))):
        group = select.args.get("group")
        if group is None or len(group.expressions) != 1 or not isinstance(group.expressions[0], exp.Rollup):
            continue
        order = select.args.get("order")
        if select.args.get("having") is not None or select.find(exp.Grouping) is not None:
            continue
        if order is not None and any(column.table for column in order.find_all(exp.Column)):
            continue
        keys = {key.sql() for key in group.expressions[0].expressions}
        branch = select.copy()
        for clause in ("group", "order", "limit", "offset"):
            branch.set(clause, None)
        for index, projection in enumerate(list(branch.expressions)):
            if isinstance(projection, exp.Column) and projection.sql() in keys:
                branch.expressions[index] = exp.alias_(exp.Null(), projection.name)
                continue
            for column in list(projection.find_all(exp.Column)):
                if column.sql() in keys and column.find_ancestor(exp.AggFunc) is None:
                    column.replace(exp.Null())
        branch.set("having", exp.Having(this=exp.EQ(this=exp.Count(this=exp.Star()), expression=exp.Literal.number(0))))
        limit, offset = select.args.get("limit"), select.args.get("offset")
        with_clause = select.args.get("with_")
        for clause in ("order", "limit", "offset", "with_"):
            select.set(clause, None)
        branch.set("with_", None)
        union = exp.Union(this=select.copy(), expression=branch, distinct=False)
        wrapper = exp.select("*").from_(union.subquery("rollup_rows"))
        for clause, value in (("with_", with_clause), ("order", order), ("limit", limit), ("offset", offset)):
            if value is not None:
                wrapper.set(clause, value)
        if select is tree:
            return wrapper
        select.replace(wrapper)
    return tree


def _is_hoistable(node) -> bool:
    exp = sql_expressions()
    if isinstance(node, (exp.Exists, exp.Subquery)):
        return True
    return isinstance(node, exp.In) and node.args.get("query") is not None


def _outermost_subqueries(aggregate) -> list:
    found = []
    stack = [aggregate]
    while stack:
        node = stack.pop()
        for child in node.iter_expressions():
            if _is_hoistable(child):
                found.append(child)
            else:
                stack.append(child)
    return found


def _hoist_aggregate_subqueries(select) -> None:
    exp = sql_expressions()
    aggregates = [agg for agg in select.find_all(exp.AggFunc) if agg.find_ancestor(exp.Select) is select]
    hoisted = [node for agg in aggregates for node in _outermost_subqueries(agg)]
    source = select.args.get("from_")
    if not hoisted or source is None:
        return
    joins = select.args.get("joins") or []
    single_table = not joins and isinstance(source.this, exp.Table)
    source_alias = source.this.alias_or_name if single_table else _DERIVED_SOURCE_ALIAS
    columns: dict[str, str] = {}
    projections = [exp.Star()]
    for node in hoisted:
        key = node.sql()
        if key not in columns:
            columns[key] = f"hoisted_subquery_{len(columns) + 1}"
            projections.append(exp.alias_(node.copy(), columns[key]))
        node.replace(exp.column(columns[key]))
    if not single_table and any(
        column.table
        for column in select.find_all(exp.Column)
        if column.find_ancestor(exp.Select) is select and column.find_ancestor(exp.Where, exp.Join) is None
    ):
        raise ValueError("Cannot hoist subqueries from a multi-source select with qualified column references")
    inner = exp.Select(expressions=projections)
    inner.set("from_", source)
    if joins:
        inner.set("joins", joins)
    if select.args.get("where") is not None:
        inner.set("where", select.args["where"])
    select.set("joins", None)
    select.set("where", None)
    select.set("from_", exp.From(this=inner.subquery(source_alias)))


def hoist_aggregate_subqueries(expression, *, copy: bool = True):
    exp = sql_expressions()
    tree = expression.copy() if copy else expression
    for select in list(tree.find_all(exp.Select)):
        _hoist_aggregate_subqueries(select)
    return tree


def sql_expressions():
    from benchbox.utils.dialect_utils import sql_expressions as _sql_expressions

    return _sql_expressions()
