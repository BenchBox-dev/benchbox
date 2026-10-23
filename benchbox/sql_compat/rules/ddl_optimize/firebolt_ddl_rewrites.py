from __future__ import annotations

from benchbox.sql_compat.rules._registration import register_ddl_rewrite

register_ddl_rewrite(
    platform="firebolt",
    rule_name="optimize_table_definition",
    transformer_id="translate_ddl:strip_primary_keys+strip_foreign_keys+type_map",
    description="Convert DuckDB-style DDL to Firebolt dialect: VARCHAR(n)/CHAR(n) → TEXT, "
    "DECIMAL → NUMERIC, strip PRIMARY KEY and FOREIGN KEY constraint clauses",
    reason="Firebolt uses TEXT for all string types (not VARCHAR/CHAR), NUMERIC for exact "
    "decimals (not DECIMAL), and does not enforce PRIMARY KEY or FOREIGN KEY constraints.",
)
