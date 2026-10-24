from __future__ import annotations

from benchbox.sql_compat.rules._registration import register_ddl_rewrite

register_ddl_rewrite(
    platform="velox",
    rule_name="optimize_table_definition",
    transformer_id="translate_ddl:default_layout",
    description="Write USING ORC or USING PARQUET into Velox CREATE TABLE based on adapter table_format "
    "config through the translate_ddl default_layout format",
    reason="Velox (Gluten+Spark) requires an explicit USING clause on CREATE TABLE; "
    "DuckDB DDL output omits this clause entirely.",
)
