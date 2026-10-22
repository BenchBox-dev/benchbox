from __future__ import annotations

from benchbox.sql_compat.rules._registration import register_ddl_rewrite

register_ddl_rewrite(
    platform="fabric_dw",
    rule_name="optimize_table_definition",
    transformer_id="translate_ddl:schema",
    description="Qualify CREATE TABLE names with [schema] for Fabric Warehouse through translate_ddl; "
    "DuckDB DDL output omits the schema qualifier.",
    reason="Fabric Warehouse requires schema-qualified table names in CREATE TABLE. "
    "Without the schema prefix, tables are created in the wrong schema or the "
    "statement references an unqualified name that doesn't resolve correctly.",
)
