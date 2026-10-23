from __future__ import annotations

from benchbox.sql_compat.rules._registration import register_ddl_rewrite

register_ddl_rewrite(
    platform="presto",
    rule_name="optimize_table_definition",
    transformer_id="translate_ddl:strip_primary_keys,strip_not_null,default_layout",
    description="translate_ddl options for Presto CREATE TABLE: strip PRIMARY KEY constraints, lower-case "
    "table names, strip NOT NULL for the memory connector or catalog, add WITH (format = 'PARQUET') for the "
    "Hive connector",
    reason="Presto rejects PRIMARY KEY constraints in benchmark DDL; memory catalog rejects WITH properties "
    "and NOT NULL constraints; Hive connector requires explicit format declaration for table creation.",
)
