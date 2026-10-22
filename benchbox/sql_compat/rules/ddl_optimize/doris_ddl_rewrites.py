from __future__ import annotations

from benchbox.sql_compat.rules._registration import register_ddl_rewrite

register_ddl_rewrite(
    platform="doris",
    rule_name="inject_ddl_clauses",
    transformer_id="translate_ddl:key_model",
    description="Transform standard DDL to valid Doris OLAP DDL through translate_ddl: strip FOREIGN KEY "
    "and PRIMARY KEY constraints, map TIME/STRING/TEXT/SMALLINT types, write the DUPLICATE KEY and "
    "DISTRIBUTED BY HASH layout, and set PROPERTIES(replication_num) for single-node deployments.",
    reason="Doris OLAP rejects standard SQL DDL in multiple ways: it does not support "
    "FOREIGN KEY or PRIMARY KEY constraint syntax, lacks TIME as a column type, "
    "and requires explicit DUPLICATE KEY and DISTRIBUTED BY HASH clauses — "
    "both mandatory for CREATE TABLE to succeed in OLAP mode.",
)
