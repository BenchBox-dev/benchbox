import pytest

from benchbox.utils.dialect_utils import apply_rewrite

pytestmark = [pytest.mark.unit, pytest.mark.fast]

CORRELATED_EXISTS = "SELECT * FROM t WHERE EXISTS (SELECT 1 FROM s WHERE s.id = t.id)"


def test_lowercase_identifiers_lowers_matching_columns_and_keeps_literals():
    result = apply_rewrite(
        "lowercase_identifiers", "SELECT SR_FEE, 'SR_FEE' FROM store_returns", "clickhouse", patterns=[r"SR_FEE"]
    )
    assert result == "SELECT sr_fee, 'SR_FEE' FROM store_returns"


def test_decimal_zero_literals_casts_zero_branches_to_the_decimal_type():
    result = apply_rewrite(
        "decimal_zero_literals",
        "SELECT CASE WHEN a THEN 0.0 ELSE b END FROM t",
        "clickhouse",
        precision=15,
        scale=2,
    )
    assert result == "SELECT CASE WHEN a THEN CAST(0 AS Decimal(15, 2)) ELSE b END FROM t"


def test_add_query_settings_appends_a_settings_clause():
    result = apply_rewrite(
        "add_query_settings", "SELECT 1", "clickhouse", settings={"joined_subquery_requires_alias": 0}
    )
    assert result == "SELECT 1 SETTINGS joined_subquery_requires_alias = 0"


def test_correlated_exists_to_in_rewrites_the_exists_predicate():
    result = apply_rewrite("correlated_exists_to_in", CORRELATED_EXISTS, "starrocks")
    assert result == "SELECT * FROM t WHERE t.id IN (SELECT s.id FROM s)"


def test_correlated_exists_to_in_fails_closed_when_the_expected_count_is_not_met():
    with pytest.raises(ValueError, match="expected 3 correlated EXISTS rewrites, applied 1"):
        apply_rewrite("correlated_exists_to_in", CORRELATED_EXISTS, "clickhouse", expected=3)


def test_add_derived_table_aliases_names_unaliased_subqueries():
    result = apply_rewrite(
        "add_derived_table_aliases", "SELECT * FROM (SELECT 1 AS x)", "starrocks", prefix="_sq", quoted=True
    )
    assert result == "SELECT * FROM (SELECT 1 AS x) AS `_sq0`"


def test_qualify_order_by_columns_qualifies_the_named_columns():
    result = apply_rewrite(
        "qualify_order_by_columns", "SELECT i_id FROM a ORDER BY i_id", "netezza", qualifiers={"i_id": "a"}
    )
    assert result == "SELECT i_id FROM a ORDER BY a.i_id"


def test_qualify_order_by_columns_requires_its_qualifiers():
    with pytest.raises(TypeError):
        apply_rewrite("qualify_order_by_columns", "SELECT i_id FROM a ORDER BY i_id", "netezza")


@pytest.mark.parametrize(
    ("dialect", "expected"),
    [
        (
            "trino",
            "SELECT REDUCE(xs, CAST(NULL AS DOUBLE), (_acc, _v) -> CASE WHEN _v IS NULL THEN _acc "
            "WHEN _acc IS NULL THEN CAST(_v AS DOUBLE) ELSE _acc + CAST(_v AS DOUBLE) END, _acc -> _acc) FROM t",
        ),
        (
            "spark",
            "SELECT AGGREGATE(xs, CAST(NULL AS DOUBLE), (_acc, _v) -> CASE WHEN _v IS NULL THEN _acc "
            "WHEN _acc IS NULL THEN CAST(_v AS DOUBLE) ELSE _acc + CAST(_v AS DOUBLE) END) FROM t",
        ),
        ("bigquery", "SELECT (SELECT SUM(_item) FROM UNNEST(xs) AS _item) FROM t"),
        ("duckdb", "SELECT LIST_SUM(xs) FROM t"),
    ],
)
def test_reduce_list_aggregates_rewrites_list_sum_for_engines_without_it(dialect: str, expected: str):
    assert apply_rewrite("reduce_list_aggregates", "SELECT LIST_SUM(xs) FROM t", dialect) == expected


def test_nullif_zero_divisors_guards_only_the_named_divisor_columns():
    result = apply_rewrite(
        "nullif_zero_divisors", "SELECT a / CAST(pmc AS DECIMAL(15, 4)), a / b FROM t", "postgres", columns=["PMC"]
    )
    assert result == "SELECT a / NULLIF(CAST(pmc AS DECIMAL(15, 4)), 0), a / b FROM t"


def test_nullif_zero_divisors_requires_its_columns():
    with pytest.raises(TypeError):
        apply_rewrite("nullif_zero_divisors", "SELECT a / b FROM t", "postgres")


def test_inline_order_by_aliases_inlines_an_alias_used_inside_a_sort_expression():
    sql = "SELECT GROUPING(a) AS g, a FROM t GROUP BY ROLLUP (a) ORDER BY {key}"
    inlined = apply_rewrite("inline_order_by_aliases", sql.format(key="CASE WHEN g = 0 THEN a END"), "postgres")
    assert inlined == sql.format(key="CASE WHEN GROUPING(a) = 0 THEN a END")
    bare = sql.format(key="g")
    assert apply_rewrite("inline_order_by_aliases", bare, "postgres") == bare


def test_inline_order_by_aliases_limits_inlining_to_the_named_aliases():
    sql = "SELECT a + 1 AS x, b + 1 AS y FROM t ORDER BY x * 2, y * 2"
    result = apply_rewrite("inline_order_by_aliases", sql, "postgres", names=["x"])
    assert result == "SELECT a + 1 AS x, b + 1 AS y FROM t ORDER BY (a + 1) * 2, y * 2"


def test_alias_qualified_projections_names_a_nested_qualified_column_after_the_column():
    sql = "WITH v1 AS (SELECT v1.d_year FROM v1, v1 AS v1_lag) SELECT * FROM v1"
    result = apply_rewrite("alias_qualified_projections", sql, "clickhouse")
    assert result == "WITH v1 AS (SELECT v1.d_year AS d_year FROM v1 CROSS JOIN v1 AS v1_lag) SELECT * FROM v1"


def test_alias_qualified_projections_touches_the_outermost_query_only_on_request():
    assert apply_rewrite("alias_qualified_projections", "SELECT x.a FROM x", "clickhouse") == "SELECT x.a FROM x"
    result = apply_rewrite("alias_qualified_projections", "SELECT x.a FROM x", "clickhouse", top_level=True)
    assert result == "SELECT x.a AS a FROM x"


def test_qualify_alias_shadowed_columns_qualifies_a_column_hidden_by_a_same_named_alias():
    sql = "SELECT SUM(jan_sales) AS jan_sales, SUM(jan_sales / sq_ft) AS per FROM (SELECT 1 AS jan_sales, 2 AS sq_ft) AS x"
    result = apply_rewrite("qualify_alias_shadowed_columns", sql, "clickhouse")
    assert result == (
        "SELECT SUM(x.jan_sales) AS jan_sales, SUM(x.jan_sales / sq_ft) AS per "
        "FROM (SELECT 1 AS jan_sales, 2 AS sq_ft) AS x"
    )


def test_decimal_division_to_double_casts_the_dividend_once():
    result = apply_rewrite("decimal_division_to_double", "SELECT a / b FROM t", "clickhouse")
    assert result == "SELECT CAST(a AS Nullable(Float64)) / b FROM t"
    assert apply_rewrite("decimal_division_to_double", "SELECT 1.5 / b FROM t", "clickhouse") == "SELECT 1.5 / b FROM t"
    assert apply_rewrite("decimal_division_to_double", "SELECT a / b FROM t", "duckdb") == (
        "SELECT CAST(a AS DOUBLE) / b FROM t"
    )


def test_use_nulls_for_rollups_adds_the_setting_only_to_a_rollup_query():
    rolled = apply_rewrite("use_nulls_for_rollups", "SELECT a, SUM(b) FROM t GROUP BY ROLLUP (a)", "clickhouse")
    assert rolled == "SELECT a, SUM(b) FROM t GROUP BY ROLLUP (a) SETTINGS group_by_use_nulls = 1"
    plain = "SELECT a, SUM(b) FROM t GROUP BY a"
    assert apply_rewrite("use_nulls_for_rollups", plain, "clickhouse") == plain


def test_round_inputs_to_decimal_casts_every_round_input_once():
    result = apply_rewrite("round_inputs_to_decimal", "SELECT ROUND(AVG(x), 2), ROUND(y) FROM t", "postgres")
    assert result == "SELECT ROUND(CAST(AVG(x) AS DECIMAL), 2), ROUND(CAST(y AS DECIMAL)) FROM t"
    assert apply_rewrite("round_inputs_to_decimal", result, "postgres") == result


def test_round_inputs_to_decimal_leaves_a_query_without_round_unchanged():
    assert apply_rewrite("round_inputs_to_decimal", "SELECT a FROM t", "postgres") == "SELECT a FROM t"
