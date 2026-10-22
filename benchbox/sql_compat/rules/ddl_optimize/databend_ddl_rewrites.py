from __future__ import annotations

from benchbox.sql_compat.rules._registration import register_ddl_rewrite

register_ddl_rewrite(
    platform="databend",
    rule_name="optimize_table_definition",
    transformer_id="translate_ddl:strip_primary_keys+strip_foreign_keys+type_map",
    description="Convert DuckDB-style DDL to Databend dialect: CHAR(n) → VARCHAR(n), "
    "strip PRIMARY KEY and FOREIGN KEY constraint clauses",
    reason="Databend uses VARCHAR for all string types (not CHAR), and does not enforce "
    "PRIMARY KEY or FOREIGN KEY constraints - both must be removed from DDL.",
)
