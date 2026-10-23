import pytest

from benchbox._vendor.sqlglot.errors import ParseError
from benchbox.platforms.clickhouse.query_rewrites import rewrite_query_for_clickhouse

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

SESSION_SETTING = "SETTINGS joined_subquery_requires_alias = 0"


class TestDecimalLiterals:
    def test_non_zero_decimal_literals_are_preserved(self):
        result = rewrite_query_for_clickhouse("SELECT col + 0.06, col - 0.01, col * 0.05, col / 0.07 FROM t")
        assert result == f"SELECT col + 0.06, col - 0.01, col * 0.05, col / 0.07 FROM t {SESSION_SETTING}"

    def test_pure_zero_decimal_is_cast(self):
        result = rewrite_query_for_clickhouse("SELECT col + 0.0 FROM t")
        assert result == f"SELECT col + CAST(0 AS Decimal(15, 2)) FROM t {SESSION_SETTING}"

    def test_branch_zero_decimals_are_cast(self):
        result = rewrite_query_for_clickhouse("SELECT CASE WHEN a THEN 0.0 ELSE b END FROM t")
        assert "THEN CAST(0 AS Decimal(15, 2)) ELSE b" in result

    def test_q6_between_bounds_are_preserved(self):
        sql = (
            "SELECT SUM(l_extendedprice * l_discount) AS revenue FROM lineitem "
            "WHERE l_discount BETWEEN 0.06 - 0.01 AND 0.06 + 0.01"
        )
        assert rewrite_query_for_clickhouse(sql) == f"{sql} {SESSION_SETTING}"

    def test_string_literals_are_not_rewritten(self):
        result = rewrite_query_for_clickhouse("SELECT 'THEN 0.0' AS s, 'a + 0.0' AS u FROM t")
        assert result.startswith("SELECT 'THEN 0.0' AS s, 'a + 0.0' AS u FROM t")


class TestSettings:
    def test_non_query_statements_are_not_given_a_settings_clause(self):
        ddl = "CREATE TABLE foo (id Int32) ENGINE = MergeTree() ORDER BY id"
        assert "SETTINGS joined" not in rewrite_query_for_clickhouse(ddl)

    def test_session_setting_is_appended_to_queries(self):
        assert rewrite_query_for_clickhouse("SELECT 1") == f"SELECT 1 {SESSION_SETTING}"
        assert rewrite_query_for_clickhouse("WITH x AS (SELECT 1) SELECT * FROM x").endswith(SESSION_SETTING)

    def test_query_after_a_leading_comment_is_rewritten(self):
        assert rewrite_query_for_clickhouse("-- header\nSELECT 1").endswith(SESSION_SETTING)

    def test_additional_settings_share_the_statement_settings_clause(self):
        result = rewrite_query_for_clickhouse(
            "SELECT 1", {"join_algorithm": "grace_hash", "grace_hash_join_initial_buckets": 8}
        )
        assert result == (
            "SELECT 1 SETTINGS joined_subquery_requires_alias = 0, "
            "join_algorithm = 'grace_hash', grace_hash_join_initial_buckets = 8"
        )

    def test_existing_session_setting_is_replaced(self):
        result = rewrite_query_for_clickhouse("SELECT 1 FROM t SETTINGS joined_subquery_requires_alias = 1")
        assert result == f"SELECT 1 FROM t {SESSION_SETTING}"

    def test_analyzer_opt_out_is_never_added(self):
        assert "enable_analyzer" not in rewrite_query_for_clickhouse("SELECT count(*) FROM store_sales")


class TestSafeDivision:
    def test_nullable_decimal_divisor_is_wrapped(self):
        result = rewrite_query_for_clickhouse("SELECT a / CAST(b AS Nullable(Decimal(15, 4))) FROM t")
        assert "nullIf(CAST(b AS Nullable(Decimal(15, 4))), 0)" in result

    def test_scaled_numerator_window_divisor_is_wrapped(self):
        result = rewrite_query_for_clickhouse(
            "SELECT SUM(x) * 100 / SUM(SUM(x)) OVER (PARTITION BY c) FROM t GROUP BY c"
        )
        assert "nullIf(SUM(SUM(x)) OVER (PARTITION BY c), 0)" in result

    def test_literal_divisor_is_untouched(self):
        assert "nullIf" not in rewrite_query_for_clickhouse("SELECT a / 12 FROM t")

    def test_division_inside_a_string_literal_is_untouched(self):
        result = rewrite_query_for_clickhouse("SELECT 'a/b' AS label, x / y FROM t")
        assert "'a/b'" in result
        assert "x / nullIf(y, 0)" in result


class TestCaseNormalization:
    @pytest.mark.parametrize(
        "column",
        [
            "SR_RETURN_AMT",
            "SR_FEE",
            "SR_REFUNDED_CASH",
            "SR_RETURN_AMT_INC_TAX",
            "SR_REVERSED_CHARGE",
            "SR_STORE_CREDIT",
            "SR_RETURN_TAX",
        ],
    )
    def test_return_columns_are_lowercased(self, column):
        result = rewrite_query_for_clickhouse(f"SELECT SUM({column}) FROM store_returns")
        assert f"SUM({column.lower()})" in result
        assert column not in result

    def test_string_literals_keep_their_case(self):
        result = rewrite_query_for_clickhouse("SELECT SR_FEE, 'SR_FEE' FROM store_returns")
        assert result.startswith("SELECT sr_fee, 'SR_FEE' FROM store_returns")


class TestUnparseableSql:
    def test_unparseable_query_fails_before_reaching_the_engine(self):
        with pytest.raises(ParseError):
            rewrite_query_for_clickhouse("SELECT SR_FEE / ((( FROM")
