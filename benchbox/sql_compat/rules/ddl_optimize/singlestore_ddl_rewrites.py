from __future__ import annotations

from benchbox.sql_compat.decision import FailureMode
from benchbox.sql_compat.rules._registration import register_ddl_rewrite

register_ddl_rewrite(
    platform="singlestore",
    rule_name="strip_foreign_keys",
    transformer_id="translate_ddl:strip_foreign_keys",
    description="Remove FOREIGN KEY clauses from CREATE TABLE (SingleStore error 2752).",
    reason="SingleStore raises error 2752 when a CREATE TABLE statement contains FOREIGN KEY "
    "clauses with referential actions. FK constraints are stripped so tables can be created "
    "without enforcement (acceptable for benchmarking workloads).",
    governance_only=True,
)

register_ddl_rewrite(
    platform="singlestore",
    rule_name="reference_table_for_dimensions",
    transformer_id="translate_ddl:singlestore.reference_tables",
    description="Rewrite small dimension tables (nation, region) to CREATE REFERENCE TABLE.",
    reason="Small TPC-H dimension tables (nation, region) benefit from REFERENCE TABLE replication "
    "in SingleStore, which eliminates broadcast joins at query time. "
    "Tables in _REFERENCE_TABLES are converted from CREATE TABLE to CREATE REFERENCE TABLE.",
    failure_mode=FailureMode.PERFORMANCE_REGRESSION,
    governance_only=True,
)

register_ddl_rewrite(
    platform="singlestore",
    rule_name="inject_shard_key",
    transformer_id="translate_ddl:singlestore.shard_keys",
    description="Inject SHARD KEY clause for columnstore tables (per-table or random distribution).",
    reason="SingleStore columnstore tables require an explicit SHARD KEY for data distribution. "
    "Without it, tables default to random sharding which degrades co-located join performance. "
    "Shard columns are mapped from _TPCH_SHARD_KEYS; tables without a known shard column "
    "receive SHARD KEY () (random distribution).",
    failure_mode=FailureMode.PERFORMANCE_REGRESSION,
    governance_only=True,
)

register_ddl_rewrite(
    platform="singlestore",
    rule_name="inject_sort_key",
    transformer_id="translate_ddl:singlestore.sort_keys",
    description="Inject SORT KEY clause for columnstore tables from _TPCH_SORT_KEYS mapping.",
    reason="SingleStore columnstore SORT KEY controls the on-disk ordering used by analytical scans. "
    "Without an appropriate sort key, range-scan and aggregation queries suffer. "
    "Sort columns are mapped from _TPCH_SORT_KEYS per table.",
    failure_mode=FailureMode.PERFORMANCE_REGRESSION,
    governance_only=True,
)
