from __future__ import annotations

from benchbox.sql_compat.rules._registration import register_ddl_rewrite

register_ddl_rewrite(
    platform="ducklake",
    rule_name="strip_primary_keys",
    transformer_id="translate_ddl:strip_primary_keys",
    description="Adjust CREATE TABLE DDL for DuckLake: strip PRIMARY KEY/UNIQUE constraints",
    reason="DuckLake rejects PRIMARY KEY/UNIQUE constraints in benchmark DDL; "
    "the constraint is metadata only for immutable benchmark data and is stripped before execution.",
)
