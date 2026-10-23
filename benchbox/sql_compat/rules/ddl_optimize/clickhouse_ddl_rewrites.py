from __future__ import annotations

from benchbox.sql_compat.rules._registration import register_ddl_rewrite

register_ddl_rewrite(
    platform="clickhouse",
    rule_name="optimize_table_definition",
    transformer_id="translate_ddl:clickhouse_layout",
    description="Fork translate_ddl writes the ClickHouse table DDL: Nullable only for the listed columns, "
    "ENGINE = MergeTree(), PARTITION BY / ORDER BY / PRIMARY KEY from the tuned layout, ORDER BY tuple() or "
    "the primary-key columns otherwise",
    reason="ClickHouse rejects Nullable(Type) NOT NULL combinations, requires an explicit "
    "ENGINE clause, and MergeTree tables must declare ORDER BY.",
)
