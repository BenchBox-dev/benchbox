from __future__ import annotations

from benchbox.sql_compat.rules._registration import register_ddl_rewrite

register_ddl_rewrite(
    platform="synapse",
    rule_platform="azure_synapse",
    rule_name="optimize_table_definition",
    transformer_id="translate_ddl:default_layout_distribution",
    description="Append the Azure Synapse distribution clause to CREATE TABLE through translate_ddl: "
    "WITH (DISTRIBUTION=ROUND_ROBIN) or the configured default",
    reason="Azure Synapse Dedicated SQL Pool requires a WITH (DISTRIBUTION = ...) clause "
    "on every CREATE TABLE statement; DuckDB DDL output omits this clause.",
)
