from __future__ import annotations

from benchbox.core.vector_search.queries import QUERY_VARIANTS
from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import Phase
from benchbox.sql_compat.decision import (
    CompatibilityDecision,
    FailureMode,
    SelectVariantPayload,
    SkipQueryPayload,
    SupportLevel,
)
from benchbox.sql_compat.registry import REGISTRY

_P = Phase.QUERY_SOURCE
_B = "vector_search"

_REASONS: dict[str, str] = {
    "spark": "Spark SQL uses higher-order array functions for exact vector search; DuckDB array_* functions are not available",
    "starrocks": "StarRocks uses cosine_similarity / l2_distance; DuckDB array_* functions are not available",
    "doris": "Doris uses cosine_distance / l2_distance on ARRAY<FLOAT>; DuckDB array_* functions are not available",
    "postgresql": "PostgreSQL + pgvector uses <=> and <-> operators; DuckDB array_* functions are not available",
    "clickhouse": "ClickHouse uses cosineDistance / L2Distance; DuckDB array_* functions are not available",
    "snowflake": "Snowflake native VECTOR type uses VECTOR_COSINE_SIMILARITY / VECTOR_L2_DISTANCE",
}


for _platform, _reason in _REASONS.items():
    for _qid, _sql in QUERY_VARIANTS.get(_platform, {}).items():
        REGISTRY.register(
            CompatibilityDecision(
                rule_id=f"query_source.{_platform}.vector_search.{_qid.lower()}_variant",
                action=CompatAction.SELECT_VARIANT,
                support_level=SupportLevel.REWRITTEN,
                failure_mode=FailureMode.UNSUPPORTED_FEATURE,
                payload=SelectVariantPayload(variant_key=_platform, variant_sql=_sql),
                reason=_reason,
            ),
            _P,
            _platform,
            benchmark=_B,
            query_id=_qid,
        )

for _qid in ("Q1", "Q2", "Q3", "Q4", "Q5", "Q6"):
    REGISTRY.register(
        CompatibilityDecision(
            rule_id=f"query_source.lakesail.vector_search.{_qid.lower()}_unsupported",
            action=CompatAction.SKIP_QUERY,
            support_level=SupportLevel.SKIPPED_QUERY,
            failure_mode=FailureMode.UNSUPPORTED_FEATURE,
            payload=SkipQueryPayload(
                reason=(
                    "LakeSail/Sail rejects DuckDB array_cosine_similarity/array_distance and the Spark "
                    "higher-order array lambda fallback."
                ),
                query_id=_qid,
            ),
            reason=(
                "LakeSail/Sail has no native vector-distance functions for this benchmark and rejects "
                "Spark SQL lambda fallback expressions."
            ),
        ),
        _P,
        "lakesail",
        benchmark=_B,
        query_id=_qid,
    )


REGISTRY.register(
    CompatibilityDecision(
        rule_id="query_source.starrocks.vector_search.q2_lt_32_skip",
        action=CompatAction.SKIP_QUERY,
        support_level=SupportLevel.SKIPPED_QUERY,
        failure_mode=FailureMode.SYNTAX_ERROR,
        payload=SkipQueryPayload(
            reason="l2_distance requires StarRocks ≥3.2; this build returns a parse error",
            query_id="Q2",
        ),
        reason="StarRocks <3.2 does not support l2_distance - skip Q2 rather than fail at runtime",
    ),
    _P,
    "starrocks",
    benchmark=_B,
    query_id="Q2",
    max_version="3.1",
)

REGISTRY.register(
    CompatibilityDecision(
        rule_id="query_source.starrocks.vector_search.q2_ge_32_variant",
        action=CompatAction.SELECT_VARIANT,
        support_level=SupportLevel.REWRITTEN,
        failure_mode=FailureMode.UNSUPPORTED_FEATURE,
        payload=SelectVariantPayload(
            variant_key="starrocks",
            variant_sql=QUERY_VARIANTS["starrocks"]["Q2"],
        ),
        reason="StarRocks ≥3.2 supports l2_distance - use native variant instead of DuckDB array_distance",
    ),
    _P,
    "starrocks",
    benchmark=_B,
    query_id="Q2",
    min_version="3.2",
)
