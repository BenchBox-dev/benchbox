from __future__ import annotations

from benchbox.sql_compat.rules._registration import register_ddl_rewrite

register_ddl_rewrite(
    platform="postgresql",
    rule_name="strip_foreign_keys",
    transformer_id="translate_ddl:strip_foreign_keys",
    description="Remove FOREIGN KEY constraint clauses on retry when CREATE TABLE fails; "
    "some PostgreSQL-compatible engines (e.g., CedarDB) reject FK syntax.",
    reason="PostgreSQL itself supports FOREIGN KEY constraints, but PostgreSQL-compatible "
    "engines may reject them at CREATE TABLE time. The adapter strips FK clauses "
    "on retry so tables can be created without FK enforcement.",
)
