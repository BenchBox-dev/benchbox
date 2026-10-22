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
