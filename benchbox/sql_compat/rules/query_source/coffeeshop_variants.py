from __future__ import annotations

from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import Phase
from benchbox.sql_compat.decision import (
    CompatibilityDecision,
    FailureMode,
    SelectVariantPayload,
    SupportLevel,
)
from benchbox.sql_compat.registry import REGISTRY

_B = "coffeeshop"
_P = Phase.QUERY_SOURCE

CLICKHOUSE_SA4_SQL = """\
SELECT
    dl.region,
    COUNT(DISTINCT ol.order_id) AS orders,
    SUM(ol.total_price) AS revenue,
    toFloat64(SUM(ol.total_price)) / toFloat64(totals.grand_total) AS revenue_share
FROM order_lines ol
JOIN dim_locations dl ON ol.location_record_id = dl.record_id
CROSS JOIN (
    SELECT SUM(total_price) AS grand_total
    FROM order_lines
    WHERE order_date BETWEEN toDate('{start_date}') AND toDate('{end_date}')
) totals
WHERE ol.order_date BETWEEN toDate('{start_date}') AND toDate('{end_date}')
GROUP BY dl.region, totals.grand_total
ORDER BY revenue DESC;"""

CLICKHOUSE_TM1_SQL = """\
SELECT
    CASE
        WHEN toHour(toDateTime(concat('2000-01-01 ', ol.order_time))) BETWEEN 5 AND 10 THEN 'Morning'
        WHEN toHour(toDateTime(concat('2000-01-01 ', ol.order_time))) BETWEEN 11 AND 14 THEN 'Midday'
        WHEN toHour(toDateTime(concat('2000-01-01 ', ol.order_time))) BETWEEN 15 AND 17 THEN 'Afternoon'
        ELSE 'Evening'
    END AS day_part,
    COUNT(DISTINCT ol.order_id) AS orders,
    SUM(ol.total_price) AS revenue,
    AVG(ol.total_price) AS avg_line_value
FROM order_lines ol
JOIN dim_locations dl ON ol.location_record_id = dl.record_id
WHERE dl.region = '{region}'
  AND ol.order_date BETWEEN toDate('{start_date}') AND toDate('{end_date}')
GROUP BY day_part
ORDER BY day_part;"""

REGISTRY.register(
    CompatibilityDecision(
        rule_id="query_source.clickhouse.coffeeshop.sa4_cross_join_variant",
        action=CompatAction.SELECT_VARIANT,
        support_level=SupportLevel.REWRITTEN,
        failure_mode=FailureMode.UNSUPPORTED_FEATURE,
        payload=SelectVariantPayload(
            variant_key="clickhouse",
            variant_sql=CLICKHOUSE_SA4_SQL,
        ),
        reason="ClickHouse does not support nested window aggregate SUM(SUM()) OVER (); replace with CROSS JOIN subquery",
    ),
    _P,
    "clickhouse",
    benchmark=_B,
    query_id="SA4",
)

REGISTRY.register(
    CompatibilityDecision(
        rule_id="query_source.clickhouse.coffeeshop.tm1_todatetime_variant",
        action=CompatAction.SELECT_VARIANT,
        support_level=SupportLevel.REWRITTEN,
        failure_mode=FailureMode.UNSUPPORTED_FEATURE,
        payload=SelectVariantPayload(
            variant_key="clickhouse",
            variant_sql=CLICKHOUSE_TM1_SQL,
        ),
        reason="ClickHouse stores TIME as String; toDateTime() of a bare time is NULL, so prefix a date before toHour(); the date is well after the epoch because pre-epoch DateTime values clamp on servers with a timezone east of UTC",
    ),
    _P,
    "clickhouse",
    benchmark=_B,
    query_id="TM1",
)

QUESTDB_TM1_SQL = """\
SELECT
    CASE
        WHEN hour(CAST('1970-01-01T' || ol.order_time AS TIMESTAMP)) BETWEEN 5 AND 10 THEN 'Morning'
        WHEN hour(CAST('1970-01-01T' || ol.order_time AS TIMESTAMP)) BETWEEN 11 AND 14 THEN 'Midday'
        WHEN hour(CAST('1970-01-01T' || ol.order_time AS TIMESTAMP)) BETWEEN 15 AND 17 THEN 'Afternoon'
        ELSE 'Evening'
    END AS day_part,
    COUNT(DISTINCT ol.order_id) AS orders,
    SUM(ol.total_price) AS revenue,
    AVG(ol.total_price) AS avg_line_value
FROM order_lines ol
JOIN dim_locations dl ON ol.location_record_id = dl.record_id
WHERE dl.region = '{region}'
  AND ol.order_date BETWEEN CAST('{start_date}' AS DATE) AND CAST('{end_date}' AS DATE)
GROUP BY day_part
ORDER BY day_part;"""

REGISTRY.register(
    CompatibilityDecision(
        rule_id="query_source.questdb.coffeeshop.tm1_varchar_time_variant",
        action=CompatAction.SELECT_VARIANT,
        support_level=SupportLevel.REWRITTEN,
        failure_mode=FailureMode.UNSUPPORTED_FEATURE,
        payload=SelectVariantPayload(
            variant_key="questdb",
            variant_sql=QUESTDB_TM1_SQL,
        ),
        reason="QuestDB has no TIME type, so order_time is stored as VARCHAR; prefix a date and cast to TIMESTAMP before extracting the hour",
    ),
    _P,
    "questdb",
    benchmark=_B,
    query_id="TM1",
)
