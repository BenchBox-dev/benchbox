from __future__ import annotations

from benchbox.sql_compat.rules._registration import register_ddl_rewrite

register_ddl_rewrite(
    platform="redshift",
    rule_name="optimize_table_definition",
    transformer_id="translate_ddl:default_layout_distribution+sort_auto",
    description="Append Redshift distribution and sort key clauses to CREATE TABLE through translate_ddl: "
    "DISTSTYLE AUTO and SORTKEY AUTO",
    reason="Redshift requires explicit DISTSTYLE and SORTKEY declarations for optimal "
    "analytical performance; DuckDB DDL output omits these Redshift-specific clauses.",
)
