from __future__ import annotations

from typing import Any

from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import Phase
from benchbox.sql_compat.decision import (
    CompatibilityDecision,
    FailureMode,
    SelectVariantPayload,
    SupportLevel,
)
from benchbox.sql_compat.registry import REGISTRY

_B = "tpcdi"
_P = Phase.QUERY_SOURCE


def _clickhouse_metric_query(query_id: str, params: dict[str, Any] | None = None) -> str:
    from benchbox.core.tpcdi.queries import TPCDIQueryManager

    manager = TPCDIQueryManager().analytical_queries
    query = manager._queries[query_id]
    query = query.replace(
        "JULIANDAY(DATE('now')) - JULIANDAY(MIN(d.DateValue))",
        "dateDiff('day', MIN(d.DateValue), today())",
    )
    query = query.replace(
        "JULIANDAY(MAX(d.DateValue)) - JULIANDAY(MIN(d.DateValue))",
        "dateDiff('day', MIN(d.DateValue), MAX(d.DateValue))",
    )
    query = query.replace(
        "JULIANDAY(sell_date.DateValue) - JULIANDAY(buy_date.DateValue)",
        "dateDiff('day', buy_date.DateValue, sell_date.DateValue)",
    )
    query = query.replace(
        "JULIANDAY(t1_date.DateValue) - JULIANDAY(t2_date.DateValue)",
        "dateDiff('day', t2_date.DateValue, t1_date.DateValue)",
    )
    query = query.replace("JULIANDAY(DATE('now', '-90 days'))", "today() - INTERVAL 90 DAY")
    query = query.replace("DATE('now', '-90 days')", "today() - INTERVAL 90 DAY")
    query = query.replace("current_date", "today()")

    if query_id == "AQ10":
        query = query.replace("t1.TradeID,\n        CASE", "t1.TradeID AS trade_id,\n        CASE")
        query = query.replace(
            "wash_sales ON t.TradeID = wash_sales.TradeID", "wash_sales ON t.TradeID = wash_sales.trade_id"
        )
        query = query.replace(
            "COUNT(CASE WHEN t.Quantity * t.TradePrice > {large_trade_threshold} THEN 1 END)",
            "countIf(t.Quantity * t.TradePrice > {large_trade_threshold})",
        )
        query = query.replace(
            "COUNT(CASE WHEN d.DateValue = today() THEN 1 END)",
            "countIf(d.DateValue = today())",
        )
        query = query.replace(
            "COUNT(CASE WHEN wash_sales.wash_sale_flag = 1 THEN 1 END)",
            "countIf(wash_sales.wash_sale_flag = 1)",
        )

    if query_id == "AQ7":
        query = query.replace("c.SK_CustomerID,\n            MIN", "c.SK_CustomerID AS customer_id,\n            MIN")
        query = query.replace("customer_metrics.SK_CustomerID", "customer_metrics.customer_id")

    if query_id == "AQ8":
        query = query.replace(
            "GREATEST(dateDiff('day', MIN(d.DateValue), MAX(d.DateValue)), 1)",
            "greatest(dateDiff('day', MIN(d.DateValue), MAX(d.DateValue)), 1)",
        )
    query_params = manager._generate_default_params(query_id)
    if params:
        query_params.update(params)
    return query.format(**query_params)


def build_clickhouse_metric_query(query_id: str, params: dict[str, Any] | None = None) -> str:
    return _clickhouse_metric_query(query_id, params)


CLICKHOUSE_AQ7_SQL = _clickhouse_metric_query("AQ7")
CLICKHOUSE_AQ8_SQL = _clickhouse_metric_query("AQ8")
CLICKHOUSE_AQ10_SQL = _clickhouse_metric_query("AQ10")

STARROCKS_EQ7_SQL = """\
SELECT
    'ETL Data Quality Score Calculation' AS validation_name,
    table_name,
    total_records,
    null_key_fields,
    invalid_values,
    constraint_violations,
    business_rule_violations,
    completeness_score,
    validity_score,
    consistency_score,
    (completeness_score * 0.4 + validity_score * 0.3 + consistency_score * 0.3) AS overall_quality_score,
    CASE
        WHEN (completeness_score * 0.4 + validity_score * 0.3 + consistency_score * 0.3) >= 95.0 THEN 'EXCELLENT'
        WHEN (completeness_score * 0.4 + validity_score * 0.3 + consistency_score * 0.3) >= 85.0 THEN 'GOOD'
        WHEN (completeness_score * 0.4 + validity_score * 0.3 + consistency_score * 0.3) >= 75.0 THEN 'ACCEPTABLE'
        ELSE 'POOR'
    END AS quality_rating
FROM (
    SELECT
        'DimCustomer' AS table_name,
        COUNT(*) AS total_records,
        SUM(CASE WHEN CustomerID IS NULL OR TaxID IS NULL OR Status IS NULL THEN 1 ELSE 0 END) AS null_key_fields,
        SUM(CASE WHEN Gender NOT IN ('M', 'F') OR Tier NOT IN (1,2,3) THEN 1 ELSE 0 END) AS invalid_values,
        SUM(CASE WHEN CreditRating < 300 OR CreditRating > 850 THEN 1 ELSE 0 END) AS constraint_violations,
        SUM(CASE WHEN (Tier = 1 AND NetWorth < 1000000) OR (Tier = 3 AND NetWorth > 250000) THEN 1 ELSE 0 END) AS business_rule_violations,
        (COUNT(*) - SUM(CASE WHEN CustomerID IS NULL OR TaxID IS NULL OR Status IS NULL THEN 1 ELSE 0 END)) / COUNT(*) * 100 AS completeness_score,
        (COUNT(*) - SUM(CASE WHEN Gender NOT IN ('M', 'F') OR Tier NOT IN (1,2,3) THEN 1 ELSE 0 END)) / COUNT(*) * 100 AS validity_score,
        (COUNT(*) - SUM(CASE WHEN CreditRating < 300 OR CreditRating > 850 THEN 1 ELSE 0 END)
         - SUM(CASE WHEN (Tier = 1 AND NetWorth < 1000000) OR (Tier = 3 AND NetWorth > 250000) THEN 1 ELSE 0 END)) / COUNT(*) * 100 AS consistency_score
    FROM DimCustomer
    WHERE IsCurrent = 1
    UNION ALL
    SELECT
        'DimAccount' AS table_name,
        COUNT(*) AS total_records,
        SUM(CASE WHEN AccountID IS NULL OR SK_CustomerID IS NULL OR Status IS NULL THEN 1 ELSE 0 END) AS null_key_fields,
        SUM(CASE WHEN Status NOT IN ('Active', 'Inactive', 'Closed') THEN 1 ELSE 0 END) AS invalid_values,
        SUM(CASE WHEN TaxStatus NOT IN (0, 1, 2) THEN 1 ELSE 0 END) AS constraint_violations,
        0 AS business_rule_violations,
        (COUNT(*) - SUM(CASE WHEN AccountID IS NULL OR SK_CustomerID IS NULL OR Status IS NULL THEN 1 ELSE 0 END)) / COUNT(*) * 100 AS completeness_score,
        (COUNT(*) - SUM(CASE WHEN Status NOT IN ('Active', 'Inactive', 'Closed') THEN 1 ELSE 0 END)) / COUNT(*) * 100 AS validity_score,
        (COUNT(*) - SUM(CASE WHEN TaxStatus NOT IN (0, 1, 2) THEN 1 ELSE 0 END)) / COUNT(*) * 100 AS consistency_score
    FROM DimAccount
    WHERE IsCurrent = 1
    UNION ALL
    SELECT
        'FactTrade' AS table_name,
        COUNT(*) AS total_records,
        SUM(CASE WHEN TradeID IS NULL OR SK_CustomerID IS NULL OR SK_SecurityID IS NULL THEN 1 ELSE 0 END) AS null_key_fields,
        SUM(CASE WHEN Status NOT IN ('Pending', 'Completed', 'Cancelled') OR Type NOT IN ('Buy', 'Sell') THEN 1 ELSE 0 END) AS invalid_values,
        SUM(CASE WHEN TradePrice <= 0 OR Quantity <= 0 OR Fee < 0 OR Commission < 0 THEN 1 ELSE 0 END) AS constraint_violations,
        SUM(CASE WHEN TradePrice > 10000 OR Quantity > 1000000 THEN 1 ELSE 0 END) AS business_rule_violations,
        (COUNT(*) - SUM(CASE WHEN TradeID IS NULL OR SK_CustomerID IS NULL OR SK_SecurityID IS NULL THEN 1 ELSE 0 END)) / COUNT(*) * 100 AS completeness_score,
        (COUNT(*) - SUM(CASE WHEN Status NOT IN ('Pending', 'Completed', 'Cancelled') OR Type NOT IN ('Buy', 'Sell') THEN 1 ELSE 0 END)) / COUNT(*) * 100 AS validity_score,
        (COUNT(*) - SUM(CASE WHEN TradePrice <= 0 OR Quantity <= 0 OR Fee < 0 OR Commission < 0 THEN 1 ELSE 0 END)
         - SUM(CASE WHEN TradePrice > 10000 OR Quantity > 1000000 THEN 1 ELSE 0 END)) / COUNT(*) * 100 AS consistency_score
    FROM FactTrade
) AS quality_metrics
ORDER BY (completeness_score * 0.4 + validity_score * 0.3 + consistency_score * 0.3) DESC"""

DATAFUSION_AQ9_SQL = """\
SELECT
    'Market Maker and Liquidity Analysis' AS analysis_name,
    Symbol,
    security_name,
    market_makers,
    total_sell_orders,
    total_buy_orders,
    avg_sell_price,
    avg_buy_price,
    bid_ask_spread,
    bid_ask_spread / avg_trade_price * 100 AS bid_ask_spread_pct,
    total_volume,
    avg_daily_volume,
    market_share_of_volume,
    price_volatility,
    price_volatility / avg_trade_price * 100 AS coefficient_of_variation
FROM (
    SELECT
        Symbol,
        security_name,
        market_makers,
        total_sell_orders,
        total_buy_orders,
        avg_sell_price,
        avg_buy_price,
        avg_sell_price - avg_buy_price AS bid_ask_spread,
        avg_trade_price,
        total_volume,
        avg_daily_volume,
        total_volume / avg_daily_volume AS market_share_of_volume,
        price_volatility
    FROM (
        SELECT
            trade_rows.Symbol,
            trade_rows.Name AS security_name,
            COUNT(*) AS total_trades,
            COUNT(DISTINCT trade_rows.SK_BrokerID) AS market_makers,
            SUM(trade_rows.sell_order) AS total_sell_orders,
            SUM(trade_rows.buy_order) AS total_buy_orders,
            AVG(trade_rows.sell_price) AS avg_sell_price,
            AVG(trade_rows.buy_price) AS avg_buy_price,
            AVG(trade_rows.TradePrice) AS avg_trade_price,
            SUM(trade_rows.Quantity) AS total_volume,
            AVG(trade_rows.Volume) AS avg_daily_volume,
            STDDEV(trade_rows.TradePrice) AS price_volatility
        FROM (
            SELECT
                s.Symbol,
                s.Name,
                t.SK_BrokerID,
                t.TradePrice,
                t.Quantity,
                mh.Volume,
                CASE WHEN tt.TT_IS_SELL IS TRUE THEN 1 ELSE 0 END AS sell_order,
                CASE WHEN tt.TT_IS_SELL IS FALSE THEN 1 ELSE 0 END AS buy_order,
                CASE WHEN tt.TT_IS_SELL IS TRUE THEN t.TradePrice END AS sell_price,
                CASE WHEN tt.TT_IS_SELL IS FALSE THEN t.TradePrice END AS buy_price
            FROM DimSecurity s
            JOIN FactTrade t ON s.SK_SecurityID = t.SK_SecurityID
            JOIN TradeType tt ON t.Type = tt.TT_ID
            JOIN FactMarketHistory mh ON s.SK_SecurityID = mh.SK_SecurityID
            JOIN DimDate d ON t.SK_CreateDateID = d.SK_DateID
            WHERE s.IsCurrent IS TRUE
              AND t.Status = 'Completed'
              AND d.CalendarYearID >= 2015
              AND d.CalendarYearID <= 2019
        ) trade_rows
        GROUP BY trade_rows.Symbol, trade_rows.Name
    ) metrics
    WHERE total_trades > 10
      AND total_sell_orders > 0
      AND total_buy_orders > 0
) derived_metrics
ORDER BY market_share_of_volume DESC, bid_ask_spread_pct ASC
LIMIT 50;"""

DATAFUSION_VQ6_SQL = """\
SELECT
    'Trade-Holdings Consistency Validation' AS validation_name,
    customer_id,
    security_id,
    total_buy_quantity,
    total_sell_quantity,
    net_position,
    current_holdings,
    position_discrepancy,
    CASE WHEN ABS(position_discrepancy) <= 100 THEN 'PASS' ELSE 'FAIL' END AS status
FROM (
    SELECT
        customer_id,
        security_id,
        total_buy_quantity,
        total_sell_quantity,
        net_position,
        current_holdings,
        net_position - current_holdings AS position_discrepancy
    FROM (
        SELECT
            trade_rows.SK_CustomerID AS customer_id,
            trade_rows.SK_SecurityID AS security_id,
            SUM(trade_rows.buy_quantity) AS total_buy_quantity,
            SUM(trade_rows.sell_quantity) AS total_sell_quantity,
            SUM(trade_rows.signed_quantity) AS net_position,
            COALESCE(fh.CurrentHolding, 0) AS current_holdings
        FROM (
            SELECT
                ft.SK_CustomerID,
                ft.SK_SecurityID,
                CASE WHEN tt.TT_IS_SELL IS FALSE THEN ft.Quantity ELSE 0 END AS buy_quantity,
                CASE WHEN tt.TT_IS_SELL IS TRUE THEN ft.Quantity ELSE 0 END AS sell_quantity,
                CASE WHEN tt.TT_IS_SELL IS FALSE THEN ft.Quantity ELSE -ft.Quantity END AS signed_quantity
            FROM FactTrade ft
            JOIN TradeType tt ON ft.Type = tt.TT_ID
            WHERE ft.Status = 'Completed'
        ) trade_rows
        LEFT JOIN FactHoldings fh ON trade_rows.SK_CustomerID = fh.SK_CustomerID
                                  AND trade_rows.SK_SecurityID = fh.SK_SecurityID
        GROUP BY trade_rows.SK_CustomerID, trade_rows.SK_SecurityID, fh.CurrentHolding
    ) grouped_positions
) position_check
WHERE ABS(position_discrepancy) > 0
ORDER BY ABS(position_discrepancy) DESC
LIMIT 100;"""

DATAFUSION_EQ7_SQL = STARROCKS_EQ7_SQL.replace("IsCurrent = 1", "IsCurrent IS TRUE").replace(
    "/ COUNT(*) * 100", "/ CAST(COUNT(*) AS DOUBLE) * 100"
)

DORIS_EQ7_SQL = STARROCKS_EQ7_SQL
CLICKHOUSE_EQ7_SQL = STARROCKS_EQ7_SQL

BIGQUERY_EQ7_SQL = STARROCKS_EQ7_SQL.replace("IsCurrent = 1", "IsCurrent IS TRUE")

DATABRICKS_EQ7_SQL = STARROCKS_EQ7_SQL.replace("IsCurrent = 1", "IsCurrent IS TRUE")

SNOWFLAKE_EQ7_SQL = STARROCKS_EQ7_SQL

for _query_id, _variant_sql, _reason in (
    (
        "AQ9",
        DATAFUSION_AQ9_SQL,
        "DataFusion physical planning fails when repeated conditional aggregates are commoned across AQ9 projections",
    ),
    (
        "VQ6",
        DATAFUSION_VQ6_SQL,
        "DataFusion physical planning fails when VQ6 repeats a grouped CASE expression across projections",
    ),
):
    REGISTRY.register(
        CompatibilityDecision(
            rule_id=f"query_source.datafusion.tpcdi.{_query_id.lower()}_projection_variant",
            action=CompatAction.SELECT_VARIANT,
            support_level=SupportLevel.REWRITTEN,
            failure_mode=FailureMode.UNSUPPORTED_FEATURE,
            payload=SelectVariantPayload(
                variant_key="datafusion",
                variant_sql=_variant_sql,
            ),
            reason=_reason,
        ),
        _P,
        "datafusion",
        benchmark=_B,
        query_id=_query_id,
    )

REGISTRY.register(
    CompatibilityDecision(
        rule_id="query_source.datafusion.tpcdi.eq7_derived_table_variant",
        action=CompatAction.SELECT_VARIANT,
        support_level=SupportLevel.REWRITTEN,
        failure_mode=FailureMode.UNSUPPORTED_FEATURE,
        payload=SelectVariantPayload(
            variant_key="datafusion",
            variant_sql=DATAFUSION_EQ7_SQL,
        ),
        reason="DataFusion cannot cross-reference sibling subquery aliases; wrap EQ7 metrics in a derived table",
    ),
    _P,
    "datafusion",
    benchmark=_B,
    query_id="EQ7",
)

for _query_id, _variant_sql, _description in (
    ("AQ7", CLICKHOUSE_AQ7_SQL, "replace SQLite Julian-day date arithmetic with ClickHouse dateDiff"),
    ("AQ8", CLICKHOUSE_AQ8_SQL, "replace SQLite Julian-day date arithmetic with ClickHouse dateDiff"),
    ("AQ10", CLICKHOUSE_AQ10_SQL, "project the ClickHouse wash-sales join key explicitly"),
    ("EQ7", CLICKHOUSE_EQ7_SQL, "compute the quality score from the UNION-derived columns"),
):
    REGISTRY.register(
        CompatibilityDecision(
            rule_id=f"query_source.clickhouse.tpcdi.{_query_id.lower()}_variant",
            action=CompatAction.SELECT_VARIANT,
            support_level=SupportLevel.REWRITTEN,
            failure_mode=FailureMode.UNSUPPORTED_FEATURE,
            payload=SelectVariantPayload(variant_key="clickhouse", variant_sql=_variant_sql),
            reason=f"ClickHouse TPC-DI compatibility rewrite: {_description}",
        ),
        _P,
        "clickhouse",
        benchmark=_B,
        query_id=_query_id,
    )

REGISTRY.register(
    CompatibilityDecision(
        rule_id="query_source.starrocks.tpcdi.eq7_derived_table_variant",
        action=CompatAction.SELECT_VARIANT,
        support_level=SupportLevel.REWRITTEN,
        failure_mode=FailureMode.UNSUPPORTED_FEATURE,
        payload=SelectVariantPayload(
            variant_key="starrocks",
            variant_sql=STARROCKS_EQ7_SQL,
        ),
        reason="StarRocks (MySQL-compatible) rejects cross-referencing sibling subquery aliases; wrap UNION ALL in derived table",
    ),
    _P,
    "starrocks",
    benchmark=_B,
    query_id="EQ7",
)

REGISTRY.register(
    CompatibilityDecision(
        rule_id="query_source.bigquery.tpcdi.eq7_derived_table_variant",
        action=CompatAction.SELECT_VARIANT,
        support_level=SupportLevel.REWRITTEN,
        failure_mode=FailureMode.UNSUPPORTED_FEATURE,
        payload=SelectVariantPayload(
            variant_key="bigquery",
            variant_sql=BIGQUERY_EQ7_SQL,
        ),
        reason="BigQuery rejects cross-referencing sibling subquery aliases; wrap UNION ALL in derived table",
    ),
    _P,
    "bigquery",
    benchmark=_B,
    query_id="EQ7",
)

REGISTRY.register(
    CompatibilityDecision(
        rule_id="query_source.doris.tpcdi.eq7_derived_table_variant",
        action=CompatAction.SELECT_VARIANT,
        support_level=SupportLevel.REWRITTEN,
        failure_mode=FailureMode.UNSUPPORTED_FEATURE,
        payload=SelectVariantPayload(
            variant_key="doris",
            variant_sql=DORIS_EQ7_SQL,
        ),
        reason="Doris (MySQL-compatible) rejects cross-referencing sibling subquery aliases; wrap UNION ALL in derived table",
    ),
    _P,
    "doris",
    benchmark=_B,
    query_id="EQ7",
)

REGISTRY.register(
    CompatibilityDecision(
        rule_id="query_source.databricks.tpcdi.eq7_derived_table_variant",
        action=CompatAction.SELECT_VARIANT,
        support_level=SupportLevel.REWRITTEN,
        failure_mode=FailureMode.UNSUPPORTED_FEATURE,
        payload=SelectVariantPayload(
            variant_key="databricks",
            variant_sql=DATABRICKS_EQ7_SQL,
        ),
        reason="Databricks rejects cross-referencing sibling subquery aliases; wrap UNION ALL in derived table",
    ),
    _P,
    "databricks",
    benchmark=_B,
    query_id="EQ7",
)

REGISTRY.register(
    CompatibilityDecision(
        rule_id="query_source.snowflake.tpcdi.eq7_derived_table_variant",
        action=CompatAction.SELECT_VARIANT,
        support_level=SupportLevel.REWRITTEN,
        failure_mode=FailureMode.UNSUPPORTED_FEATURE,
        payload=SelectVariantPayload(
            variant_key="snowflake",
            variant_sql=SNOWFLAKE_EQ7_SQL,
        ),
        reason="Snowflake rejects cross-referencing sibling subquery aliases; wrap UNION ALL in derived table",
    ),
    _P,
    "snowflake",
    benchmark=_B,
    query_id="EQ7",
)
