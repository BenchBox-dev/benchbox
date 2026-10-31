# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import importlib

from benchbox.core.query_catalog_base import QuerySkippedError, is_native_dialect
from benchbox.utils.dialect_utils import translate_sql_query

_QUERIES: dict[str, str] = {
    "Q1": """
SELECT v.id,
       array_cosine_similarity(v.embedding, q.query_vector) AS similarity
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q
ORDER BY similarity DESC
LIMIT 10
""".strip(),
    "Q2": """
SELECT v.id,
       array_distance(v.embedding, q.query_vector) AS distance
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q
ORDER BY distance ASC
LIMIT 10
""".strip(),
    "Q3": """
SELECT v.id,
       array_cosine_similarity(v.embedding, q.query_vector) AS similarity
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q
WHERE v.category = 'category_01'
ORDER BY similarity DESC
LIMIT 10
""".strip(),
    "Q4": """
SELECT v.id,
       array_cosine_similarity(v.embedding, q.query_vector) AS similarity
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q
ORDER BY similarity DESC
LIMIT 100
""".strip(),
    "Q5": """
SELECT v.id,
       array_cosine_similarity(v.embedding, q.query_vector) AS similarity
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q
ORDER BY similarity DESC
LIMIT 10
""".strip(),
    "Q6": """
SELECT v.id,
       array_cosine_similarity(v.embedding, q.query_vector) AS similarity
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 2) q
WHERE v.category IN ('category_01', 'category_02', 'category_03')
ORDER BY similarity DESC
LIMIT 20
""".strip(),
}


QUERY_VARIANTS: dict[str, dict[str, str]] = {
    "spark": {
        "Q1": """
SELECT v.id,
       aggregate(zip_with(v.embedding, q.query_vector, (x, y) -> x * y), 0D, (acc, z) -> acc + z)
       / NULLIF(
           sqrt(aggregate(v.embedding, 0D, (acc, x) -> acc + x * x))
           * sqrt(aggregate(q.query_vector, 0D, (acc, x) -> acc + x * x)),
           0D
         ) AS similarity
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q
ORDER BY similarity DESC
LIMIT 10
""".strip(),
        "Q2": """
SELECT v.id,
       sqrt(aggregate(zip_with(v.embedding, q.query_vector, (x, y) -> (x - y) * (x - y)), 0D, (acc, z) -> acc + z)) AS distance
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q
ORDER BY distance ASC
LIMIT 10
""".strip(),
        "Q3": """
SELECT v.id,
       aggregate(zip_with(v.embedding, q.query_vector, (x, y) -> x * y), 0D, (acc, z) -> acc + z)
       / NULLIF(
           sqrt(aggregate(v.embedding, 0D, (acc, x) -> acc + x * x))
           * sqrt(aggregate(q.query_vector, 0D, (acc, x) -> acc + x * x)),
           0D
         ) AS similarity
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q
WHERE v.category = 'category_01'
ORDER BY similarity DESC
LIMIT 10
""".strip(),
        "Q4": """
SELECT v.id,
       aggregate(zip_with(v.embedding, q.query_vector, (x, y) -> x * y), 0D, (acc, z) -> acc + z)
       / NULLIF(
           sqrt(aggregate(v.embedding, 0D, (acc, x) -> acc + x * x))
           * sqrt(aggregate(q.query_vector, 0D, (acc, x) -> acc + x * x)),
           0D
         ) AS similarity
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q
ORDER BY similarity DESC
LIMIT 100
""".strip(),
        "Q5": """
SELECT v.id,
       aggregate(zip_with(v.embedding, q.query_vector, (x, y) -> x * y), 0D, (acc, z) -> acc + z)
       / NULLIF(
           sqrt(aggregate(v.embedding, 0D, (acc, x) -> acc + x * x))
           * sqrt(aggregate(q.query_vector, 0D, (acc, x) -> acc + x * x)),
           0D
         ) AS similarity
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q
ORDER BY similarity DESC
LIMIT 10
""".strip(),
        "Q6": """
SELECT v.id,
       aggregate(zip_with(v.embedding, q.query_vector, (x, y) -> x * y), 0D, (acc, z) -> acc + z)
       / NULLIF(
           sqrt(aggregate(v.embedding, 0D, (acc, x) -> acc + x * x))
           * sqrt(aggregate(q.query_vector, 0D, (acc, x) -> acc + x * x)),
           0D
         ) AS similarity
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 2) q
WHERE v.category IN ('category_01', 'category_02', 'category_03')
ORDER BY similarity DESC
LIMIT 20
""".strip(),
    },
    "starrocks": {
        "Q1": """
SELECT v.id,
       cosine_similarity(v.embedding, q.query_vector) AS similarity
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q
ORDER BY similarity DESC
LIMIT 10
""".strip(),
        "Q2": """
SELECT v.id,
       l2_distance(v.embedding, q.query_vector) AS distance
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q
ORDER BY distance ASC
LIMIT 10
""".strip(),
        "Q3": """
SELECT v.id,
       cosine_similarity(v.embedding, q.query_vector) AS similarity
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q
WHERE v.category = 'category_01'
ORDER BY similarity DESC
LIMIT 10
""".strip(),
        "Q4": """
SELECT v.id,
       cosine_similarity(v.embedding, q.query_vector) AS similarity
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q
ORDER BY similarity DESC
LIMIT 100
""".strip(),
        "Q5": """
SELECT v.id,
       cosine_similarity(v.embedding, q.query_vector) AS similarity
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q
ORDER BY similarity DESC
LIMIT 10
""".strip(),
        "Q6": """
SELECT v.id,
       cosine_similarity(v.embedding, q.query_vector) AS similarity
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 2) q
WHERE v.category IN ('category_01', 'category_02', 'category_03')
ORDER BY similarity DESC
LIMIT 20
""".strip(),
    },
    "doris": {
        "Q1": """
SELECT v.id,
       1 - cosine_distance(v.embedding, q.query_vector) AS similarity
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q
ORDER BY similarity DESC
LIMIT 10
""".strip(),
        "Q2": """
SELECT v.id,
       l2_distance(v.embedding, q.query_vector) AS distance
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q
ORDER BY distance ASC
LIMIT 10
""".strip(),
        "Q3": """
SELECT v.id,
       1 - cosine_distance(v.embedding, q.query_vector) AS similarity
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q
WHERE v.category = 'category_01'
ORDER BY similarity DESC
LIMIT 10
""".strip(),
        "Q4": """
SELECT v.id,
       1 - cosine_distance(v.embedding, q.query_vector) AS similarity
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q
ORDER BY similarity DESC
LIMIT 100
""".strip(),
        "Q5": """
SELECT v.id,
       1 - cosine_distance(v.embedding, q.query_vector) AS similarity
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q
ORDER BY similarity DESC
LIMIT 10
""".strip(),
        "Q6": """
SELECT v.id,
       1 - cosine_distance(v.embedding, q.query_vector) AS similarity
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 2) q
WHERE v.category IN ('category_01', 'category_02', 'category_03')
ORDER BY similarity DESC
LIMIT 20
""".strip(),
    },
    "postgresql": {
        "Q1": """
SELECT v.id,
       1 - (v.embedding <=> q.query_vector) AS similarity
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q
ORDER BY v.embedding <=> q.query_vector ASC
LIMIT 10
""".strip(),
        "Q2": """
SELECT v.id,
       v.embedding <-> q.query_vector AS distance
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q
ORDER BY distance ASC
LIMIT 10
""".strip(),
        "Q3": """
SELECT v.id,
       1 - (v.embedding <=> q.query_vector) AS similarity
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q
WHERE v.category = 'category_01'
ORDER BY v.embedding <=> q.query_vector ASC
LIMIT 10
""".strip(),
        "Q4": """
SELECT v.id,
       1 - (v.embedding <=> q.query_vector) AS similarity
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q
ORDER BY v.embedding <=> q.query_vector ASC
LIMIT 100
""".strip(),
        "Q5": """
SELECT v.id,
       1 - (v.embedding <=> q.query_vector) AS similarity
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q
ORDER BY v.embedding <=> q.query_vector ASC
LIMIT 10
""".strip(),
        "Q6": """
SELECT v.id,
       1 - (v.embedding <=> q.query_vector) AS similarity
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 2) q
WHERE v.category IN ('category_01', 'category_02', 'category_03')
ORDER BY v.embedding <=> q.query_vector ASC
LIMIT 20
""".strip(),
    },
    "clickhouse": {
        "Q1": """
SELECT v.id,
       1 - cosineDistance(v.embedding, q.query_vector) AS similarity
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q
ORDER BY cosineDistance(v.embedding, q.query_vector) ASC
LIMIT 10
""".strip(),
        "Q2": """
SELECT v.id,
       L2Distance(v.embedding, q.query_vector) AS distance
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q
ORDER BY distance ASC
LIMIT 10
""".strip(),
        "Q3": """
SELECT v.id,
       1 - cosineDistance(v.embedding, q.query_vector) AS similarity
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q
WHERE v.category = 'category_01'
ORDER BY cosineDistance(v.embedding, q.query_vector) ASC
LIMIT 10
""".strip(),
        "Q4": """
SELECT v.id,
       1 - cosineDistance(v.embedding, q.query_vector) AS similarity
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q
ORDER BY cosineDistance(v.embedding, q.query_vector) ASC
LIMIT 100
""".strip(),
        "Q5": """
SELECT v.id,
       1 - cosineDistance(v.embedding, q.query_vector) AS similarity
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q
ORDER BY cosineDistance(v.embedding, q.query_vector) ASC
LIMIT 10
""".strip(),
        "Q6": """
SELECT v.id,
       1 - cosineDistance(v.embedding, q.query_vector) AS similarity
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 2) q
WHERE v.category IN ('category_01', 'category_02', 'category_03')
ORDER BY cosineDistance(v.embedding, q.query_vector) ASC
LIMIT 20
""".strip(),
    },
    "snowflake": {
        "Q1": """
SELECT v.id,
       VECTOR_COSINE_SIMILARITY(v.embedding, q.query_vector) AS similarity
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q
ORDER BY similarity DESC
LIMIT 10
""".strip(),
        "Q2": """
SELECT v.id,
       VECTOR_L2_DISTANCE(v.embedding, q.query_vector) AS distance
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q
ORDER BY distance ASC
LIMIT 10
""".strip(),
        "Q3": """
SELECT v.id,
       VECTOR_COSINE_SIMILARITY(v.embedding, q.query_vector) AS similarity
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q
WHERE v.category = 'category_01'
ORDER BY similarity DESC
LIMIT 10
""".strip(),
        "Q4": """
SELECT v.id,
       VECTOR_COSINE_SIMILARITY(v.embedding, q.query_vector) AS similarity
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q
ORDER BY similarity DESC
LIMIT 100
""".strip(),
        "Q5": """
SELECT v.id,
       VECTOR_COSINE_SIMILARITY(v.embedding, q.query_vector) AS similarity
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q
ORDER BY similarity DESC
LIMIT 10
""".strip(),
        "Q6": """
SELECT v.id,
       VECTOR_COSINE_SIMILARITY(v.embedding, q.query_vector) AS similarity
FROM vectors v
CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 2) q
WHERE v.category IN ('category_01', 'category_02', 'category_03')
ORDER BY similarity DESC
LIMIT 20
""".strip(),
    },
}

for _spark_alias in ("pyspark", "velox", "databricks"):
    QUERY_VARIANTS[_spark_alias] = QUERY_VARIANTS["spark"]

_SINGLESTORE_COSINE = (
    "DOT_PRODUCT(v.embedding, q.query_vector)"
    " / NULLIF(SQRT(DOT_PRODUCT(v.embedding, v.embedding)) * SQRT(DOT_PRODUCT(q.query_vector, q.query_vector)), 0)"
)

QUERY_VARIANTS["singlestore"] = {
    _query_id: _sql.replace("array_cosine_similarity(v.embedding, q.query_vector)", _SINGLESTORE_COSINE).replace(
        "array_distance(v.embedding, q.query_vector)", "EUCLIDEAN_DISTANCE(v.embedding, q.query_vector)"
    )
    for _query_id, _sql in _QUERIES.items()
}


class VectorSearchQueryManager:
    ALL_QUERY_IDS = ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6"]

    DESCRIPTIONS: dict[str, str] = {
        "Q1": "kNN cosine similarity exact search (top-10)",
        "Q2": "kNN L2-distance exact search (top-10)",
        "Q3": "Filtered kNN cosine - single category (top-10)",
        "Q4": "Large-k cosine search for recall@10 ground truth (top-100)",
        "Q5": "ANN cosine search - uses HNSW index when available (top-10)",
        "Q6": "Multi-category filtered cosine search (top-20)",
    }

    def __init__(self) -> None:
        self._queries = dict(_QUERIES)

    def get_query(self, query_id: str, *, dialect: str | None = None, platform_version: str | None = None) -> str:
        key = str(query_id).upper()
        if key not in self._queries:
            available = ", ".join(sorted(self._queries.keys()))
            raise ValueError(f"Invalid query ID: {query_id!r}. Available: {available}")

        if dialect:
            dialect_lower = dialect.lower()
            legacy_sql = QUERY_VARIANTS.get(dialect_lower, {}).get(key)

            importlib.import_module("benchbox.sql_compat.rules.query_source.vector_search_variants")
            from benchbox.sql_compat.actions import CompatAction
            from benchbox.sql_compat.context import CompatibilityContext, Phase
            from benchbox.sql_compat.registry import REGISTRY

            ctx = CompatibilityContext(
                platform=dialect_lower,
                platform_version=platform_version,
                benchmark="vector_search",
                query_id=key,
                phase=Phase.QUERY_SOURCE,
                mode="sql",
                dialect=dialect,
            )
            registry_decision = REGISTRY.resolve(ctx)
            if registry_decision is not None:
                if registry_decision.action is CompatAction.SELECT_VARIANT:
                    return registry_decision.payload.variant_sql
                if registry_decision.action is CompatAction.SKIP_QUERY:
                    reason = getattr(registry_decision.payload, "reason", None) or registry_decision.reason
                    raise QuerySkippedError(
                        f"Query '{key}' is not supported on dialect '{dialect}' "
                        f"for platform version '{platform_version or 'unknown'}': {reason}"
                    )

            if legacy_sql is not None:
                return legacy_sql
            if is_native_dialect("duckdb", dialect):
                return self._queries[key]
            return translate_sql_query(self._queries[key], dialect, source_dialect="duckdb")

        return self._queries[key]

    def get_all_queries(self, *, dialect: str | None = None, platform_version: str | None = None) -> dict[str, str]:
        queries: dict[str, str] = {}
        for qid in self.ALL_QUERY_IDS:
            try:
                queries[qid] = self.get_query(qid, dialect=dialect, platform_version=platform_version)
            except QuerySkippedError:
                continue
        return queries

    def get_description(self, query_id: str) -> str:
        return self.DESCRIPTIONS.get(str(query_id).upper(), "Unknown query")

    def variant_dialects(self) -> list[str]:
        return list(QUERY_VARIANTS.keys())
