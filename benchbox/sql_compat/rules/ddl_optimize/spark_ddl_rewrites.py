from __future__ import annotations

from benchbox.sql_compat.rules._registration import register_ddl_rewrite

register_ddl_rewrite(
    platform="spark",
    rule_name="optimize_table_definition",
    transformer_id="translate_ddl:default_layout",
    description="Inject USING <format> clause into Spark CREATE TABLE: "
    "DELTA, ICEBERG, ORC, or PARQUET based on adapter table_format config through the translate_ddl "
    "default_layout format",
    reason="Spark SQL requires an explicit USING clause on CREATE TABLE to declare the "
    "storage format; DuckDB DDL output omits this clause entirely.",
)
