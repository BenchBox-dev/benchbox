from __future__ import annotations

from benchbox.sql_compat.rules._registration import register_ddl_rewrite

register_ddl_rewrite(
    platform="bigquery",
    rule_name="convert_to_bigquery_table",
    transformer_id="translate_ddl:or_replace+schema+table_case",
    description="Convert DuckDB-style CREATE TABLE to BigQuery DDL through translate_ddl: CREATE OR REPLACE TABLE, "
    "project.dataset table qualification, DECIMAL/NUMERIC(p,s) exceeding NUMERIC limits rewritten "
    "to BIGNUMERIC, inline PRIMARY KEY converted to a NOT ENFORCED table constraint, "
    "and optional PARTITION BY / CLUSTER BY clauses.",
    reason="BigQuery DDL runs in a project/dataset namespace and benchmark schema creation must be "
    "idempotent across repeated runs. DECIMAL is only an alias for NUMERIC (scale capped at 9), so "
    "columns such as DECIMAL(18,14) latitude/longitude must become BIGNUMERIC. BigQuery rejects "
    "enforced PRIMARY KEY, so inline column keys (e.g. JoinOrder) become PRIMARY KEY NOT ENFORCED "
    "table constraints. Optional partitioning and clustering clauses are appended from adapter "
    "configuration because DuckDB DDL does not emit BigQuery storage layout hints.",
)
