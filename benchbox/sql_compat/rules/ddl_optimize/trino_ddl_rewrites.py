from __future__ import annotations

from benchbox.sql_compat.rules._registration import register_ddl_rewrite

register_ddl_rewrite(
    platform="trino",
    rule_name="optimize_table_definition",
    transformer_id="translate_ddl:strip_primary_keys,strip_not_null,default_layout",
    description="translate_ddl options for Trino CREATE TABLE: strip PRIMARY KEY constraints, lower-case "
    "table names, strip NOT NULL for the memory connector, add WITH (format = 'PARQUET') for the Hive/Iceberg "
    "connector",
    reason="Trino memory catalog rejects NOT NULL and WITH clause properties; Hive and Iceberg connectors "
    "require explicit format declaration for table creation.",
)
