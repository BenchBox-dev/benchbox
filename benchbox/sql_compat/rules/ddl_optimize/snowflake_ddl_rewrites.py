from __future__ import annotations

from benchbox.sql_compat.rules._registration import register_ddl_rewrite

register_ddl_rewrite(
    platform="snowflake",
    rule_name="optimize_table_definition",
    transformer_id="translate_ddl:or_replace",
    description="Rewrite CREATE TABLE → CREATE OR REPLACE TABLE for Snowflake idempotency through translate_ddl",
    reason="Snowflake schema creation must be idempotent across benchmark runs; "
    "CREATE OR REPLACE TABLE avoids failures when a table already exists.",
)
