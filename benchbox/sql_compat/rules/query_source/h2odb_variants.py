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

_B = "h2odb"
_P = Phase.QUERY_SOURCE

CLICKHOUSE_Q9_SQL = """
SELECT
    passenger_count,
    quantile(0.5)(fare_amount) as median_fare_amount,
    quantile(0.9)(fare_amount) as p90_fare_amount
FROM trips
GROUP BY passenger_count
ORDER BY passenger_count;
"""

STARROCKS_Q9_SQL = """
SELECT
    passenger_count,
    PERCENTILE_APPROX(fare_amount, 0.5) as median_fare_amount,
    PERCENTILE_APPROX(fare_amount, 0.9) as p90_fare_amount
FROM trips
GROUP BY passenger_count
ORDER BY passenger_count;
"""

SQLITE_Q9_SQL = """
SELECT
    "passenger_count",
    PERCENTILE_CONT(0.5, "fare_amount") AS "median_fare_amount",
    PERCENTILE_CONT(0.9, "fare_amount") AS "p90_fare_amount"
FROM "trips"
GROUP BY "passenger_count"
ORDER BY "passenger_count";
"""

BIGQUERY_Q9_SQL = """
SELECT DISTINCT
    passenger_count,
    PERCENTILE_CONT(fare_amount, 0.5) OVER (PARTITION BY passenger_count) as median_fare_amount,
    PERCENTILE_CONT(fare_amount, 0.9) OVER (PARTITION BY passenger_count) as p90_fare_amount
FROM trips
ORDER BY passenger_count;
"""

REGISTRY.register(
    CompatibilityDecision(
        rule_id="query_source.clickhouse.h2odb.q9_quantile_variant",
        action=CompatAction.SELECT_VARIANT,
        support_level=SupportLevel.REWRITTEN,
        failure_mode=FailureMode.SYNTAX_ERROR,
        payload=SelectVariantPayload(
            variant_key="clickhouse",
            variant_sql=CLICKHOUSE_Q9_SQL,
        ),
        reason="ClickHouse cannot parse PERCENTILE_CONT … WITHIN GROUP; replace with quantile()",
    ),
    _P,
    "clickhouse",
    benchmark=_B,
    query_id="Q9",
)

REGISTRY.register(
    CompatibilityDecision(
        rule_id="query_source.sqlite.h2odb.q9_percentile_cont_variant",
        action=CompatAction.SELECT_VARIANT,
        support_level=SupportLevel.REWRITTEN,
        failure_mode=FailureMode.SYNTAX_ERROR,
        payload=SelectVariantPayload(variant_key="sqlite", variant_sql=SQLITE_Q9_SQL),
        reason="SQLite does not parse ANSI PERCENTILE_CONT WITHIN GROUP syntax; use the registered two-argument aggregate.",
    ),
    _P,
    "sqlite",
    benchmark=_B,
    query_id="Q9",
)

REGISTRY.register(
    CompatibilityDecision(
        rule_id="query_source.starrocks.h2odb.q9_percentile_approx_variant",
        action=CompatAction.SELECT_VARIANT,
        support_level=SupportLevel.REWRITTEN,
        failure_mode=FailureMode.SYNTAX_ERROR,
        payload=SelectVariantPayload(
            variant_key="starrocks",
            variant_sql=STARROCKS_Q9_SQL,
        ),
        reason="StarRocks uses PERCENTILE_APPROX instead of ANSI WITHIN GROUP syntax",
    ),
    _P,
    "starrocks",
    benchmark=_B,
    query_id="Q9",
)

REGISTRY.register(
    CompatibilityDecision(
        rule_id="query_source.bigquery.h2odb.q9_percentile_window_variant",
        action=CompatAction.SELECT_VARIANT,
        support_level=SupportLevel.REWRITTEN,
        failure_mode=FailureMode.SYNTAX_ERROR,
        payload=SelectVariantPayload(
            variant_key="bigquery",
            variant_sql=BIGQUERY_Q9_SQL,
        ),
        reason="BigQuery does not parse ANSI PERCENTILE_CONT WITHIN GROUP syntax; use the native "
        "PERCENTILE_CONT(value, percentile) OVER (PARTITION BY …) window form",
    ),
    _P,
    "bigquery",
    benchmark=_B,
    query_id="Q9",
)
