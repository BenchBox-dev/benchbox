from __future__ import annotations

from benchbox.sql_compat.rules._registration import register_ddl_rewrite

register_ddl_rewrite(
    platform="starrocks",
    rule_name="optimize_table_definition",
    transformer_id="translate_ddl:key_model",
    description="Convert standard DDL to StarRocks dialect through translate_ddl: choose the DUPLICATE or "
    "PRIMARY KEY model, map types, strip FOREIGN KEY, and write the DISTRIBUTED BY HASH layout",
    reason="StarRocks DDL dialect differs from DuckDB: requires DUPLICATE KEY or PRIMARY KEY table model, "
    "rejects AUTO_INCREMENT, ENGINE=, FOREIGN KEY, and uses backtick identifiers.",
)
