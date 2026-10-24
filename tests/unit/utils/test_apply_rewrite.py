import pytest

from benchbox._vendor import sqlglot
from benchbox.utils.dialect_utils import FORK_REWRITE_MODULES, _fork_rewrite, apply_rewrite

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_bound_fold_applies_to_any_table_and_column():
    for table, column in (("lineitem", "l_discount"), ("sat_lineitem", "l_discount"), ("orders", "o_totalprice")):
        sql = f"SELECT 1 FROM {table} WHERE {column} BETWEEN 0.06 - 0.01 AND 0.06 + 0.01"
        result = apply_rewrite("fold_between_literal_arithmetic", sql, "sqlite")
        assert result == f"SELECT 1 FROM {table} WHERE {column} BETWEEN 0.05 AND 0.07"


def test_bound_fold_leaves_exact_literals_alone():
    sql = "SELECT 1 FROM orders WHERE o_totalprice BETWEEN 0.05 AND 0.07"
    assert apply_rewrite("fold_between_literal_arithmetic", sql, "sqlite") == sql


def test_string_input_returns_sql_and_expression_input_returns_expression():
    sql = "SELECT a / b FROM t"
    assert apply_rewrite("safe_division", sql, "snowflake") == "SELECT a / NULLIF(b, 0) FROM t"
    expression = sqlglot.parse_one(sql, read="snowflake")
    rewritten = apply_rewrite("safe_division", expression, "snowflake")
    assert isinstance(rewritten, sqlglot.exp.Expr)
    assert rewritten.sql(dialect="snowflake") == "SELECT a / NULLIF(b, 0) FROM t"
    assert expression.sql(dialect="snowflake") == sql


@pytest.mark.parametrize(
    ("dialect", "expected"),
    [("bigquery", "SELECT SAFE_DIVIDE(a, b) FROM t"), ("databricks", "SELECT TRY_DIVIDE(a, b) FROM t")],
)
def test_dialect_is_forwarded_to_rewrites_that_take_one(dialect: str, expected: str):
    assert apply_rewrite("safe_division", "SELECT a / b FROM t", dialect) == expected


def test_rewrite_without_a_dialect_parameter_receives_only_its_arguments():
    result = apply_rewrite("flag_comparisons_to_boolean", "SELECT 1 FROM t WHERE f = 1", "duckdb", columns=["f"])
    assert result == "SELECT 1 FROM t WHERE f = TRUE"


def test_unsupported_dialect_for_safe_division_raises():
    with pytest.raises(ValueError):
        apply_rewrite("safe_division", "SELECT a / b FROM t", "duckdb")


@pytest.mark.parametrize("name", ["not_a_rewrite", "_deduplicate_select", "unqualified_tables", "transpile_ddl", ""])
def test_only_public_expression_rewrites_resolve(name: str):
    with pytest.raises(ValueError, match="Unknown fork rewrite"):
        apply_rewrite(name, "SELECT 1", "duckdb")


HAVOC_REWRITES = (
    "inline_select_aliases",
    "qualify_columns_by_name",
    "add_row_source_to_fromless_selects",
    "filtered_aggregates_to_conditional",
    "drop_literal_row_source",
    "flatten_count_of_count",
    "name_literal_subquery_columns",
    "wrap_grouped_scalar_subqueries",
    "order_list_aggregates",
)
SUBQUERY_REWRITES = ("not_in_subquery_to_not_exists", "in_aggregate_subquery_to_exists", "decorrelate_subqueries")
ENGINE_REWRITES = (
    "lowercase_identifiers",
    "decimal_zero_literals",
    "add_query_settings",
    "correlated_exists_to_in",
    "add_derived_table_aliases",
    "qualify_order_by_columns",
)
LIST_AND_RUNNER_REWRITES = (
    "reduce_list_aggregates",
    "nullif_zero_divisors",
    "inline_order_by_aliases",
    "alias_qualified_projections",
    "qualify_alias_shadowed_columns",
    "decimal_division_to_double",
    "use_nulls_for_rollups",
    "round_inputs_to_decimal",
)


@pytest.mark.parametrize("name", [*HAVOC_REWRITES, *SUBQUERY_REWRITES, *ENGINE_REWRITES, *LIST_AND_RUNNER_REWRITES])
def test_named_registry_resolves_every_moved_rewrite(name: str):
    assert callable(_fork_rewrite(name))


def test_mark_date_columns_resolves_from_the_registry():
    assert callable(_fork_rewrite("mark_date_columns"))


def test_registry_exposes_no_unlisted_rewrites():
    import importlib
    import inspect

    exposed = set()
    for module_name in FORK_REWRITE_MODULES:
        module = importlib.import_module(f"benchbox._vendor.sqlglot.benchbox.{module_name}")
        for name, function in vars(module).items():
            if (
                not name.startswith("_")
                and inspect.isfunction(function)
                and function.__module__ == module.__name__
                and "copy" in inspect.signature(function).parameters
            ):
                exposed.add(name)
    listed = {
        "fold_between_literal_arithmetic",
        "date_integer_arithmetic_to_interval",
        "safe_division",
        "deduplicate_output_aliases",
        "flag_comparisons_to_boolean",
        "mark_date_columns",
        *HAVOC_REWRITES,
        *SUBQUERY_REWRITES,
        *ENGINE_REWRITES,
        *LIST_AND_RUNNER_REWRITES,
    }
    assert exposed == listed


@pytest.mark.parametrize(
    ("name", "sql", "dialect", "args", "expected"),
    [
        (
            "flatten_count_of_count",
            "SELECT COUNT(*) AS source_records FROM (SELECT COUNT(*) AS c FROM t WHERE p > 1)",
            "postgres",
            {},
            "SELECT COUNT(*) AS source_records FROM t WHERE p > 1",
        ),
        (
            "add_row_source_to_fromless_selects",
            "SELECT 1 WHERE 1 = 1",
            "bigquery",
            {"source": "UNNEST([1])"},
            "SELECT 1 FROM UNNEST([1]) WHERE 1 = 1",
        ),
        (
            "deduplicate_output_aliases",
            "SELECT a, a FROM t",
            "databricks",
            {},
            "SELECT a, a AS a_2 FROM t",
        ),
        (
            "date_integer_arithmetic_to_interval",
            "SELECT d + 30 FROM t",
            "datafusion",
            {"date_columns": ["d"]},
            "SELECT d + INTERVAL '30 DAY' FROM t",
        ),
        (
            "inline_select_aliases",
            "SELECT a + 1 AS x FROM t WHERE x > 2 ORDER BY x",
            "postgres",
            {},
            "SELECT a + 1 AS x FROM t WHERE a + 1 > 2 ORDER BY x",
        ),
        (
            "qualify_columns_by_name",
            "SELECT c FROM t GROUP BY c HAVING c > 1",
            "postgres",
            {"tables": {"c": "t"}},
            "SELECT t.c FROM t GROUP BY t.c HAVING t.c > 1",
        ),
        (
            "filtered_aggregates_to_conditional",
            "SELECT SUM(x) FILTER (WHERE y) FROM t",
            "bigquery",
            {},
            "SELECT SUM(IF(y, x, NULL)) FROM t",
        ),
        (
            "drop_literal_row_source",
            "SELECT 1 AS c FROM (SELECT 1 AS dual) AS dual",
            "postgres",
            {"alias": "dual"},
            "SELECT 1 AS c",
        ),
        (
            "name_literal_subquery_columns",
            "SELECT * FROM (SELECT 1) AS d",
            "databricks",
            {"column": "dual_col"},
            "SELECT * FROM (SELECT 1 AS dual_col) AS d",
        ),
        (
            "order_list_aggregates",
            "SELECT ARRAY_AGG(x) FROM t",
            "presto",
            {"order_by": ["a", "b"]},
            "SELECT ARRAY_AGG(x ORDER BY a, b) FROM t",
        ),
        (
            "not_in_subquery_to_not_exists",
            "SELECT 1 FROM t WHERE a NOT IN (SELECT b FROM u)",
            "datafusion",
            {"non_null_columns": ["a", "b"]},
            "SELECT 1 FROM t WHERE NOT EXISTS(SELECT 1 FROM u WHERE b = a)",
        ),
        (
            "in_aggregate_subquery_to_exists",
            "SELECT 1 FROM t WHERE a IN (SELECT k FROM u GROUP BY k HAVING SUM(v) > 1)",
            "datafusion",
            {"alias": "t"},
            "SELECT 1 FROM t WHERE EXISTS(SELECT 1 FROM (SELECT k FROM u GROUP BY k HAVING SUM(v) > 1) AS t "
            "WHERE t.k = a)",
        ),
        (
            "decorrelate_subqueries",
            "SELECT a FROM t WHERE b > (SELECT AVG(y) FROM u WHERE u.k = t.k)",
            "datafusion",
            {"schema": {"t": {"a": "INT", "b": "INT", "k": "INT"}, "u": {"y": "INT", "k": "INT"}}},
            "SELECT t.a AS a FROM t AS t LEFT JOIN (SELECT AVG(u.y) AS _col_0, u.k AS _u_1 FROM u AS u WHERE TRUE "
            "GROUP BY u.k) AS _u_0 ON _u_0._u_1 = t.k WHERE t.b > _u_0._col_0",
        ),
    ],
)
def test_moved_rewrites_produce_their_documented_output(name: str, sql: str, dialect: str, args: dict, expected: str):
    assert apply_rewrite(name, sql, dialect, **args) == expected


def test_wrap_grouped_scalar_subqueries_takes_the_aggregate_class():
    result = apply_rewrite(
        "wrap_grouped_scalar_subqueries",
        "SELECT (SELECT 1) FROM t GROUP BY a",
        "databricks",
        aggregate=sqlglot.exp.First,
    )
    assert result == "SELECT FIRST((SELECT 1)) FROM t GROUP BY a"


def test_apply_rewrites_returns_the_input_text_when_no_rewrite_changes_the_tree():
    from benchbox.utils.dialect_utils import apply_rewrites

    sql = "select   a  ,b from   t where c = 1"
    rewrites = [("deduplicate_output_aliases", {"nested": False}), ("safe_division", {})]
    assert apply_rewrites(sql, "databricks", rewrites) is sql


def test_apply_rewrites_parses_once_and_renders_the_combined_result():
    from benchbox.utils.dialect_utils import apply_rewrites

    rewrites = [("deduplicate_output_aliases", {"nested": False}), ("safe_division", {})]
    result = apply_rewrites("SELECT a.k, b.k, x / y FROM a, b", "databricks", rewrites)
    assert result == "SELECT a.k, b.k AS k_2, TRY_DIVIDE(x, y) FROM a CROSS JOIN b"


def test_string_rewrite_preserves_the_identifier_quoting_of_its_input():
    from benchbox.utils.dialect_utils import apply_rewrites

    quoted = apply_rewrites("SELECT `a` / `b` FROM `t`", "databricks", [("safe_division", {})])
    assert quoted == "SELECT TRY_DIVIDE(`a`, `b`) FROM `t`"
    mixed = apply_rewrites("SELECT `a` / b FROM t", "databricks", [("safe_division", {})])
    assert mixed == "SELECT TRY_DIVIDE(`a`, b) FROM t"


def test_string_rewrite_applies_to_every_statement():
    from benchbox.utils.dialect_utils import apply_rewrites, split_sql_statements

    result = apply_rewrites("SELECT a / b FROM t; SELECT c / d FROM u", "databricks", [("safe_division", {})])
    assert split_sql_statements(result, "databricks") == [
        "SELECT TRY_DIVIDE(a, b) FROM t",
        "SELECT TRY_DIVIDE(c, d) FROM u",
    ]


def test_string_rewrite_records_a_success_outcome():
    from benchbox.utils.dialect_utils import sql_translation_context, summarize_sql_translation_outcomes

    with sql_translation_context() as outcomes:
        apply_rewrite("safe_division", "SELECT a / b FROM t", "databricks")
        apply_rewrite("safe_division", "SELECT 1 FROM t", "databricks")
    assert [(o.translator, o.status, o.normalized_target_dialect) for o in outcomes] == [
        ("sqlglot_rewrite", "success", "databricks")
    ] * 2
    summary = summarize_sql_translation_outcomes(outcomes)
    assert summary is not None
    assert summary["translators"] == ["sqlglot_rewrite"]
    assert "unique_queries_translated" not in summary


@pytest.mark.parametrize(("strict", "status"), [(False, "fallback"), (True, "failed")])
def test_string_rewrite_failure_records_an_outcome_and_raises(strict: bool, status: str):
    from benchbox.utils.dialect_utils import sql_translation_context

    with sql_translation_context(strict=strict) as outcomes:
        with pytest.raises(sqlglot.errors.ParseError):
            apply_rewrite("safe_division", "SELECT FROM WHERE (", "databricks")
    assert [(o.translator, o.status) for o in outcomes] == [("sqlglot_rewrite", status)]
    assert outcomes[0].message


def test_expression_rewrite_records_no_outcome():
    from benchbox.utils.dialect_utils import sql_translation_context

    expression = sqlglot.parse_one("SELECT a / b FROM t", read="snowflake")
    with sql_translation_context() as outcomes:
        apply_rewrite("safe_division", expression, "snowflake")
    assert outcomes == []


def test_unknown_dialect_raises_for_string_and_expression_rewrites():
    from benchbox.utils.dialect_utils import UnknownSqlDialectError, apply_rewrites

    with pytest.raises(UnknownSqlDialectError):
        apply_rewrite("safe_division", "SELECT a / b FROM t", "not_a_dialect")
    with pytest.raises(UnknownSqlDialectError):
        apply_rewrite("safe_division", sqlglot.parse_one("SELECT a / b FROM t"), "not_a_dialect")
    with pytest.raises(UnknownSqlDialectError):
        apply_rewrites("SELECT 1", "not_a_dialect", [])


def test_translate_sql_query_applies_rewrites_to_the_source_tree_before_generation():
    from benchbox.utils.dialect_utils import translate_sql_query

    result = translate_sql_query(
        "SELECT 1 FROM t WHERE IsCurrent = 1",
        "postgres",
        rewrites=[("flag_comparisons_to_boolean", {"columns": ["IsCurrent"], "use_is": True})],
    )
    assert "IsCurrent IS TRUE" in result


def test_translate_sql_query_runs_a_rewrite_under_its_own_dialect_when_the_rewrite_names_one():
    from benchbox.utils.dialect_utils import translate_sql_query

    source = "SELECT LIST(x) AS xs FROM t"
    rewrite = ("order_list_aggregates", {"order_by": ["k"], "dialect": "databricks"})

    result = translate_sql_query(source, "databricks", source_dialect="duckdb", identify=False, rewrites=[rewrite])
    assert "SORT_ARRAY(COLLECT_LIST(STRUCT(k AS _k0, x AS _v)))" in result

    default = translate_sql_query(
        source,
        "databricks",
        source_dialect="duckdb",
        identify=False,
        rewrites=[("order_list_aggregates", {"order_by": ["k"]})],
    )
    assert "SORT_ARRAY" not in default


def test_unqualified_table_names_skips_ctes_schema_qualified_tables_and_deduplicates():
    from benchbox.utils.dialect_utils import unqualified_table_names

    sql = "WITH c AS (SELECT 1 FROM base) SELECT * FROM c JOIN ds.qualified q ON TRUE JOIN base b ON TRUE"
    assert unqualified_table_names(sql, "bigquery") == ["BASE"]


def test_unqualified_table_names_returns_none_for_unparseable_sql():
    from benchbox.utils.dialect_utils import unqualified_table_names

    assert unqualified_table_names("SELECT FROM WHERE (", "bigquery") is None
