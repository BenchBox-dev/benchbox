# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from benchbox.core.tuning.interface import TuningType

RenderedVia = Literal["ddl", "post_load", "session", "none"]


@dataclass(frozen=True)
class TuningCapability:
    rendered_via: RenderedVia
    mechanism_id: str
    notes: str = ""
    requires_sorted_ingestion: bool = False


def _ddl(mechanism_id: str, notes: str = "") -> TuningCapability:
    return TuningCapability(rendered_via="ddl", mechanism_id=mechanism_id, notes=notes)


def _post_load(mechanism_id: str, notes: str = "", requires_sorted_ingestion: bool = False) -> TuningCapability:
    return TuningCapability(
        rendered_via="post_load",
        mechanism_id=mechanism_id,
        notes=notes,
        requires_sorted_ingestion=requires_sorted_ingestion,
    )


def _session(mechanism_id: str, notes: str = "") -> TuningCapability:
    return TuningCapability(rendered_via="session", mechanism_id=mechanism_id, notes=notes)


def _none(mechanism_id: str, notes: str = "") -> TuningCapability:
    return TuningCapability(rendered_via="none", mechanism_id=mechanism_id, notes=notes)


_T = TuningType
_INLINE_CONSTRAINT = "inline_column_constraint"
_CONSTRAINT_NOTE = "Rendered as an inline column/table constraint clause at CREATE TABLE time."


def _pk_fk_entries() -> dict[TuningType, TuningCapability]:
    return {
        _T.PRIMARY_KEYS: _ddl(_INLINE_CONSTRAINT, _CONSTRAINT_NOTE),
        _T.FOREIGN_KEYS: _ddl(_INLINE_CONSTRAINT, _CONSTRAINT_NOTE),
    }


_UNRENDERED_UNIQUE_CHECK_NOTE = (
    "Benchmark DDL never carries UNIQUE/CHECK clauses from tuning flags: no in-tree benchmark "
    "consumes those flags (pinned tree-wide by "
    "tests/unit/core/tuning/test_capability_registry_parity.py "
    "test_unique_check_rendering_is_vacuous_in_tree), so requesting them renders nothing at execution."
)


def _no_unique_check_entries(mechanism_id: str, notes: str = "") -> dict[TuningType, TuningCapability]:
    detail = f"{notes} {_UNRENDERED_UNIQUE_CHECK_NOTE}".strip()
    return {
        _T.UNIQUE_CONSTRAINTS: _none(mechanism_id, detail),
        _T.CHECK_CONSTRAINTS: _none(mechanism_id, detail),
    }


PLATFORM_ALIASES: dict[str, str] = {
    "clickhouse-local": "clickhouse",
    "clickhouse-server": "clickhouse",
    "clickhouse-cloud": "clickhouse",
    "chdb": "clickhouse",
    "delta": "databricks",
    "synapse": "azure-synapse",
    "starburst": "trino",
}


PLATFORM_TUNING_CAPABILITIES: dict[str, dict[TuningType, TuningCapability]] = {
    "duckdb": {
        _T.SORTING: _post_load(
            "duckdb_ctas_sort",
            "CTAS reorder (CREATE OR REPLACE TABLE ... AS SELECT * FROM ... ORDER BY ...) run after data "
            "load; DuckDB has no inline CREATE TABLE ORDER BY syntax. Consumed via "
            "core.tuning.generators.duckdb.DuckDBDDLGenerator.generate_ctas_ddl by both dry-run preview "
            "and real execution since the DuckDB DDL migration.",
        ),
        _T.PARTITIONING: _none(
            "duckdb_copy_to_hint_only",
            "DuckDBDDLGenerator.generate_tuning_clauses logs a COPY TO Hive-partitioning hint for "
            "partition columns but no BenchBox code path applies it to the physical benchmark schema. "
            "Documented gap: compatible per the legacy map, but nothing renders it yet.",
        ),
        **_pk_fk_entries(),
        **_no_unique_check_entries(
            "duckdb_no_unique_check_rendering",
            "DuckDBAdapter.apply_constraint_configuration only logs UNIQUE/CHECK flag state (duckdb.py).",
        ),
    },
    "clickhouse": {
        _T.PARTITIONING: _ddl(
            "clickhouse_ddl_generator:PARTITION_BY",
            "core.tuning.generators.clickhouse.ClickHouseDDLGenerator renders PARTITION BY at CREATE "
            "TABLE time. Consumed by dry-run preview and (since the ClickHouse DDL migration) real "
            "execution in ClickHouseWorkloadMixin._optimize_table_definition.",
        ),
        _T.SORTING: _ddl(
            "clickhouse_ddl_generator:ORDER_BY",
            "Rendered as ORDER BY, combined with any clustering columns. See PARTITIONING entry for the "
            "shared migration note.",
        ),
        _T.CLUSTERING: _ddl(
            "clickhouse_ddl_generator:ORDER_BY",
            "ClickHouse has no separate clustering clause; clustering columns are folded into ORDER BY "
            "ahead of sorting columns by the generator.",
        ),
        _T.PRIMARY_KEYS: _ddl(
            "clickhouse_order_by_or_tuple_fallback",
            "MergeTree requires ORDER BY. When no tuned sort/cluster columns are configured, "
            "_optimize_table_definition falls back to primary-key-derived columns or ORDER BY tuple() -- "
            "this fallback is the engine-mandatory baseline (see ADR-3 baseline policy), not tuned "
            "rendering. ClickHouse enables primary keys even without tuning, so a change to a benchmark's "
            "declared keys changes the untuned MergeTree ORDER BY: the TPC-DS composite keys moved "
            "store_sales from ORDER BY (ss_ticket_number) to ORDER BY (ss_item_sk, ss_ticket_number) and "
            "inventory from ORDER BY tuple() to ORDER BY (inv_date_sk, inv_item_sk, inv_warehouse_sk), "
            "pinned by tests/unit/platforms/test_clickhouse_tpcds_untuned_layout.py.",
        ),
        _T.UNIQUE_CONSTRAINTS: _none(
            "unimplemented",
            "Compatible per the legacy map (downgraded to a warning regardless, as a constraint type); "
            "ClickHouse has no UNIQUE constraint syntax and no adapter code renders one.",
        ),
        _T.FOREIGN_KEYS: _none(
            "clickhouse_drops_foreign_keys",
            "ClickHouseWorkloadMixin._optimize_table_definition preserves FOREIGN KEY clauses it "
            "cannot execute (clickhouse/workload.py); the engine has no FOREIGN KEY syntax, so a "
            "requested foreign key fails at execution.",
        ),
        _T.CHECK_CONSTRAINTS: _none(
            "clickhouse_no_unique_check_rendering",
            "The ClickHouse workload column parser terminates column types at UNIQUE/CHECK keywords "
            "without rendering them (clickhouse/workload.py).",
        ),
        _T.MATERIALIZED_VIEWS: _none(
            "unimplemented",
            "Compatible per the legacy map; no adapter code creates a materialized view.",
        ),
    },
    "spark": {
        _T.PARTITIONING: _none(
            "spark_partitioning_log_only",
            "SparkAdapter.apply_table_tunings only logs partition columns, and SparkAdapter.generate_tuning_clause "
            "has no call site, so CREATE TABLE never carries PARTITIONED BY.",
        ),
        _T.SORTING: _post_load(
            "spark_delta_optimize_zorder",
            "Delta tables only: SparkAdapter.apply_table_tunings runs OPTIMIZE ... ZORDER BY on the sort columns "
            "after load. Parquet and ORC tables (the default) get no sort, and post-load reconciliation records "
            "the intent as dropped.",
        ),
        _T.PRIMARY_KEYS: _ddl(
            _INLINE_CONSTRAINT,
            "Delta tables keep the inline PRIMARY KEY clause. Parquet and ORC tables, the default formats, cannot "
            "carry constraints, so optimize_spark_table_definition removes the clause; Spark never enforces "
            "declared keys either way. Kept as DDL so tuning files that leave keys at their default still load.",
        ),
        _T.FOREIGN_KEYS: _ddl(
            _INLINE_CONSTRAINT,
            "Same as PRIMARY_KEYS: kept for Delta tables, removed from Parquet and ORC tables, never enforced.",
        ),
        **_no_unique_check_entries(
            "spark_no_unique_check_rendering",
            "optimize_spark_table_definition removes UNIQUE clauses from Parquet and ORC tables.",
        ),
    },
    "databricks": {
        _T.PARTITIONING: _none(
            "databricks_partitioning_preview_only",
            "DatabricksAdapter.generate_tuning_clause builds PARTITIONED BY from tuned columns "
            "(databricks/adapter.py:3005-3048) but has no production call site tree-wide, so no "
            "PARTITIONED BY is rendered at preview or execution. Real execution builds Delta DDL in "
            "_convert_to_delta_table (databricks/adapter.py:2672-2729), which never adds partitioning "
            "columns, and apply_table_tunings only logs them via _log_partitioning_and_sorting.",
        ),
        _T.CLUSTERING: _post_load(
            "databricks_z_order_or_liquid",
            "Rendered post-load as OPTIMIZE ... ZORDER BY (z_order strategy) or ALTER TABLE ... CLUSTER "
            "BY (Liquid Clustering strategies) by benchbox/platforms/databricks/adapter.py. Not migrated "
            "this round; see PARTITIONING entry.",
        ),
        _T.DISTRIBUTION: _none(
            "no_user_managed_distribution_key",
            "Databricks has no user-managed distribution key (see platform_capabilities.py's own "
            "reasoning strings). Distribution columns configured in shipped examples "
            "(examples/tunings/databricks/*_tuned.yaml) are folded into ZORDER locality by the "
            "workload-profile mapping upstream of the adapter, not rendered as a literal clause here.",
        ),
        _T.Z_ORDERING: _post_load("databricks_z_order_or_liquid", "See CLUSTERING entry."),
        _T.LIQUID_CLUSTERING: _post_load("databricks_z_order_or_liquid", "See CLUSTERING entry."),
        _T.AUTO_OPTIMIZE: _ddl(
            "delta_tblproperties",
            "Rendered as a Delta TBLPROPERTIES entry (delta.autoOptimize.optimizeWrite) at CREATE TABLE time.",
        ),
        _T.AUTO_COMPACT: _ddl(
            "delta_tblproperties",
            "Rendered as a Delta TBLPROPERTIES entry (delta.autoOptimize.autoCompact) at CREATE TABLE time.",
        ),
        _T.BLOOM_FILTERS: _none(
            "unimplemented",
            "Compatible per the legacy map; no adapter code creates a Bloom filter index.",
        ),
        _T.MATERIALIZED_VIEWS: _none(
            "unimplemented",
            "Compatible per the legacy map; no adapter code creates a materialized view.",
        ),
        **_pk_fk_entries(),
        **_no_unique_check_entries(
            "databricks_no_unique_check_rendering",
            "DatabricksAdapter executes tuning only through ZORDER, OPTIMIZE, Liquid and CLUSTER "
            "statements (databricks/adapter.py).",
        ),
        _T.PRIMARY_KEYS: _ddl(
            _INLINE_CONSTRAINT,
            "Declared keys are informational and unenforced on Databricks. The adapter never emits RELY "
            "(no RELY in benchbox/platforms/databricks/adapter.py), so BenchBox does not ask the optimizer to "
            "rely on declared keys. The effect of RELY on plans was not measured.",
        ),
    },
    "snowflake": {
        _T.CLUSTERING: _post_load(
            "adapter_mixin:SnowflakeAdapter.apply_table_tunings",
            "Real execution issues ALTER TABLE ... CLUSTER BY (...) from "
            "SnowflakeAdapter.apply_table_tunings (snowflake.py:1710), reached via apply_unified_tuning -> "
            "apply_standard_unified_tuning after create_schema (base/result_capture.py:1751-1753). This is "
            "NOT part of the CREATE TABLE ddl and does NOT go through the adapter's generate_tuning_clause "
            "mixin (snowflake.py:1605): that method builds a CLUSTER BY string but has no production call "
            "site anywhere, and get_create_tables_sql consumes tuning_config only for PK/FK constraint "
            "flags, never clustering columns. core.tuning.generators.snowflake.SnowflakeDDLGenerator emits "
            "CLUSTER BY for dry-run preview only.",
        ),
        _T.PARTITIONING: _post_load(
            "adapter_mixin:SnowflakeAdapter.apply_table_tunings",
            "apply_table_tunings folds partitioning columns into the same ALTER TABLE ... CLUSTER BY when "
            "no clustering columns are configured (snowflake.py:1690-1693); Snowflake has no separate "
            "partition clause. See CLUSTERING entry for the full mechanism/evidence.",
        ),
        _T.MATERIALIZED_VIEWS: _none("unimplemented", "Compatible per the legacy map; no adapter implementation."),
        **_pk_fk_entries(),
        **_no_unique_check_entries(
            "snowflake_no_unique_check_rendering",
            "SnowflakeAdapter tuning executes ALTER TABLE ... CLUSTER BY and constraint-bearing "
            "CREATE TABLE carrying PK/FK only (snowflake.py).",
        ),
        _T.PRIMARY_KEYS: _ddl(
            _INLINE_CONSTRAINT,
            "Primary keys on standard Snowflake tables are informational and unenforced (Snowflake table "
            "considerations documentation). The adapter never emits RELY (no RELY in "
            "benchbox/platforms/snowflake.py), so a declared key is not asserted as trustworthy to the "
            "optimizer.",
        ),
    },
    "bigquery": {
        _T.PARTITIONING: _none(
            "bigquery_ddl_generator:preview_only",
            "core.tuning.generators.bigquery.BigQueryDDLGenerator computes a real PARTITION BY clause from "
            "tuned columns for dry-run preview, but real execution never renders it. "
            "BigQueryAdapter.generate_tuning_clause builds PARTITION BY/CLUSTER BY (bigquery.py:1893) but "
            "has no production call site; get_create_tables_sql consumes tuning_config only for PK/FK "
            "flags, never partition/cluster columns; and apply_table_tunings only inspects the table and "
            "logs 'Consider recreating the table' (bigquery.py:2023-2027) -- it never re-creates it. "
            "Corrects the prior false _ddl(adapter_mixin:BigQueryAdapter.generate_tuning_clause) claim.",
        ),
        _T.CLUSTERING: _none(
            "bigquery_ddl_generator:preview_only",
            "Same execution gap as PARTITIONING: generate_tuning_clause emits CLUSTER BY "
            "(bigquery.py:1940-1949) and the generator previews it, but nothing renders it at execution "
            "(apply_table_tunings only logs a recreation hint, bigquery.py:2013-2027).",
        ),
        _T.MATERIALIZED_VIEWS: _none("unimplemented", "Compatible per the legacy map; no adapter implementation."),
        _T.PRIMARY_KEYS: _ddl(
            _INLINE_CONSTRAINT,
            "Rendered as a table-level 'PRIMARY KEY (...) NOT ENFORCED' constraint (bigquery.py:2146, 2157); "
            "foreign keys are likewise NOT ENFORCED (bigquery.py:2196). BigQuery never rejects a wrong key, "
            "so the key definitions, including the composite TPC-DS keys, are checked by "
            "tests/unit/benchmarks/test_schema_pk_conformance.py rather than by the engine. That test covers "
            "the generated key definitions only; the NOT ENFORCED rewrite is covered by "
            "tests/unit/platforms/test_bigquery_adapter.py.",
        ),
        _T.FOREIGN_KEYS: _ddl(_INLINE_CONSTRAINT, _CONSTRAINT_NOTE),
        **_no_unique_check_entries(
            "bigquery_no_unique_check_rendering",
            "BigQuery DDL emits PRIMARY KEY (...) NOT ENFORCED plus FOREIGN KEY clauses only (bigquery.py).",
        ),
    },
    "redshift": {
        _T.DISTRIBUTION: _none(
            "redshift_ddl_generator:preview_only",
            "core.tuning.generators.redshift.RedshiftDDLGenerator computes real DISTSTYLE/DISTKEY clauses "
            "from tuned columns for dry-run preview, but real execution never renders them. "
            "RedshiftAdapter.generate_tuning_clause builds DISTSTYLE KEY/DISTKEY (redshift.py:2464) but has "
            "no production call site; get_create_tables_sql consumes tuning_config only for PK/FK flags, "
            "never distribution columns; and apply_table_tunings only reads pg_table_def and logs "
            "'Redshift requires table recreation to change distribution/sort keys' (redshift.py:2607-2611) "
            "without recreating, then runs ANALYZE/VACUUM maintenance only (redshift.py:2616-2624). "
            "Corrects the prior false _ddl(adapter_mixin:RedshiftAdapter.generate_tuning_clause) claim.",
        ),
        _T.SORTING: _post_load(
            "redshift_ctas_sort",
            "Conditional post-load mechanism, gated on sorted ingestion being enabled "
            "(resolve_sorted_ingestion_strategy). After COPY/INSERT the loader calls apply_ctas_sort "
            "(redshift.py:1524, 1602), which resolves TuningType.SORTING columns and executes the SQL built "
            "by _build_ctas_sort_sql (redshift.py) -- 'VACUUM SORT ONLY'. The 'ctas' method renders nothing "
            "and records a drop, because CTAS + DROP + RENAME loses DISTSTYLE/DISTKEY, encodings and "
            "constraints. Inline SORTKEY remains preview-only: it is built by "
            "the uncalled generate_tuning_clause mixin (redshift.py:2500-2510) and the preview generator, and "
            "apply_table_tunings only logs the sort-key mismatch (redshift.py:2599-2605) because it cannot "
            "rewrite the key without table recreation. With sorted ingestion off, _build_ctas_sort_sql "
            "returns None and nothing is rendered.",
            requires_sorted_ingestion=True,
        ),
        _T.PARTITIONING: _none(
            "redshift_no_partition_rendering",
            "Redshift has no CREATE TABLE partition clause; generate_tuning_clause explicitly emits nothing "
            "for partitioning (redshift.py:2512-2517) and apply_table_tunings only logs the strategy "
            "(redshift.py:2629-2636). Nothing is rendered at execution.",
        ),
        _T.MATERIALIZED_VIEWS: _none("unimplemented", "Compatible per the legacy map; no adapter implementation."),
        **_pk_fk_entries(),
        **_no_unique_check_entries(
            "redshift_no_unique_check_rendering",
            "RedshiftAdapter tuning executes CTAS-sort, VACUUM and maintenance statements only (redshift.py).",
        ),
        _T.PRIMARY_KEYS: _ddl(
            _INLINE_CONSTRAINT,
            "Redshift documents primary and foreign keys as informational and unenforced, but states that "
            "the planner uses them for uniqueness inference, subquery decorrelation, join ordering and "
            "redundant-join removal and assumes they are valid as loaded, so a wrong key can return "
            "incorrect results (AWS Table constraints documentation). Whether the planner changes plans "
            "for the TPC-DS queries has not been measured; that needs a live cluster. Correct keys "
            "matter here because the planner trusts them.",
        ),
    },
    "sqlite": {
        **_pk_fk_entries(),
        **_no_unique_check_entries(
            "sqlite_no_unique_check_rendering",
            "The shared tuning-aware DDL path forwards tuning_config; SQLite execution builds no "
            "UNIQUE/CHECK clause from the flags.",
        ),
    },
    "postgresql": {
        _T.PARTITIONING: _ddl(
            "postgresql_partition_by_date_domain",
            "PostgreSQLAdapter.create_schema appends the PostgreSQLDDLGenerator PARTITION BY clause (RANGE for "
            "date columns, HASH for integers, LIST otherwise) and creates the child partitions: yearly RANGE "
            "children over the benchmark's date domain (TPC-H and SSB 1992-1998, TPC-DS 1998-2003) plus a "
            "DEFAULT partition for any other value. Skipped, and recorded as dropped, while primary or unique "
            "keys are requested, because PostgreSQL requires those keys to include the partition column.",
        ),
        _T.CLUSTERING: _post_load(
            "postgresql_index_cluster",
            "After each table loads, PostgreSQLAdapter.apply_post_load_tunings runs the PostgreSQLDDLGenerator "
            "statements: CREATE INDEX on the clustering columns followed by the sorting columns, then CLUSTER "
            "the table USING that index, which rewrites it in index order.",
        ),
        _T.SORTING: _post_load(
            "postgresql_index_cluster",
            "Same CREATE INDEX plus CLUSTER as CLUSTERING. When both are requested the index leads with the "
            "clustering columns and follows with the sorting columns, so the sort intent is realized as the "
            "secondary ordering of the combined index.",
        ),
        _T.BLOOM_FILTERS: _none("unimplemented", "Compatible per the legacy map; no adapter implementation."),
        _T.MATERIALIZED_VIEWS: _none("unimplemented", "Compatible per the legacy map; no adapter implementation."),
        **_pk_fk_entries(),
        **_no_unique_check_entries(
            "postgresql_no_unique_check_rendering",
            "PostgreSQLAdapter renders constraint-bearing CREATE TABLE from PK/FK flags only (postgresql.py).",
        ),
    },
    "mysql": {
        _T.PARTITIONING: _none(
            "mysql_no_generator_dead_mixin",
            "No MySQLAdapter class exists (the prior mechanism named a nonexistent class); MySQL-wire "
            "adapters mix in base.mysql_wire.NoOpTableTuningMixin, whose generate_tuning_clause returns '' "
            "(base/mysql_wire.py:475-476) and whose apply_table_tunings/apply_unified_tuning are no-ops "
            "(base/mysql_wire.py:472-473, 478-479). get_ddl_generator('mysql') has no real generator and "
            "falls through to NoOpDDLGenerator, so no MySQL PARTITION BY is rendered at preview or "
            "execution. Corrects the prior false "
            "_ddl(adapter_mixin:MySQLAdapter.generate_tuning_clause) claim.",
        ),
        **_pk_fk_entries(),
        **_no_unique_check_entries(
            "mysql_no_unique_check_rendering",
            "The mysql-wire tuning-aware DDL path renders PK/FK from flags only (base/mysql_wire.py).",
        ),
    },
    "singlestore": {
        _T.PARTITIONING: _none(
            "singlestore_no_partition_rendering",
            "SingleStoreAdapter inherits NoOpTableTuningMixin (singlestore.py:75): "
            "generate_tuning_clause returns '' and apply_table_tunings is a no-op. Tuned "
            "constraints flow through the shared mysql-wire tuning-aware DDL path, as on MySQL.",
        ),
        _T.PRIMARY_KEYS: _ddl(_INLINE_CONSTRAINT, _CONSTRAINT_NOTE),
        _T.FOREIGN_KEYS: _none(
            "singlestore_strips_foreign_keys",
            "SingleStore strips FOREIGN KEY clauses from every CREATE TABLE at execution "
            "(singlestore.py singlestore_strip_foreign_keys at 379-380, wired by the DDL_OPTIMIZE "
            "strip_foreign_keys rule in sql_compat/rules/ddl_optimize/singlestore_ddl_rewrites.py:6-16). "
            "Requested foreign keys never reach the engine (SingleStore error 2752).",
        ),
        **_no_unique_check_entries(
            "singlestore_no_unique_check_rendering",
            "SingleStoreAdapter inherits the mysql-wire tuning-aware DDL path rendering PK from "
            "flags only; FOREIGN KEY clauses are stripped (singlestore.py).",
        ),
    },
    "cedardb": {
        _T.PARTITIONING: _none(
            "postgresql_ddl_generator:preview_only",
            "CedarDBAdapter subclasses PostgreSQLAdapter with no DDL override: identical "
            "execution gap to postgresql (preview computes PARTITION BY, execution renders nothing).",
        ),
        _T.CLUSTERING: _none(
            "postgresql_ddl_generator:preview_only",
            "Same inherited execution gap as PARTITIONING.",
        ),
        _T.BLOOM_FILTERS: _none("unimplemented", "Compatible per the legacy map; no adapter implementation."),
        _T.MATERIALIZED_VIEWS: _none("unimplemented", "Compatible per the legacy map; no adapter implementation."),
        **_pk_fk_entries(),
        **_no_unique_check_entries(
            "cedardb_no_unique_check_rendering",
            "CedarDBAdapter inherits PostgreSQLAdapter execution rendering PK/FK from flags only (cedardb.py).",
        ),
    },
    "citus": {
        _T.PARTITIONING: _none(
            "postgresql_ddl_generator:preview_only",
            "CitusAdapter subclasses PostgreSQLAdapter with no DDL override: identical "
            "execution gap to postgresql (preview computes PARTITION BY, execution renders nothing).",
        ),
        _T.CLUSTERING: _none(
            "postgresql_ddl_generator:preview_only",
            "Same inherited execution gap as PARTITIONING.",
        ),
        _T.BLOOM_FILTERS: _none("unimplemented", "Compatible per the legacy map; no adapter implementation."),
        _T.MATERIALIZED_VIEWS: _none("unimplemented", "Compatible per the legacy map; no adapter implementation."),
        **_pk_fk_entries(),
        **_no_unique_check_entries(
            "citus_no_unique_check_rendering",
            "CitusAdapter inherits PostgreSQLAdapter execution rendering PK/FK from flags only (citus.py).",
        ),
    },
    "paradedb": {
        _T.PARTITIONING: _ddl(
            "postgresql_partition_by_date_domain",
            "ParadeDBAdapter inherits PostgreSQLAdapter heap-table partitioning: PARTITION BY (RANGE for "
            "date columns, HASH for integers, LIST otherwise) plus child partitions at CREATE TABLE time, "
            "skipped and recorded as dropped while primary or unique keys are requested. The strategy and "
            "columns are verified from pg_get_partkeydef after schema creation. Benchmark tables are "
            "ordinary heap tables; pg_search adds BM25 indexes only and is not involved.",
        ),
        _T.CLUSTERING: _post_load(
            "postgresql_index_cluster",
            "After each heap table loads, CREATE INDEX on the merged clustering and sorting columns, then "
            "CLUSTER USING that index (autocommit, since CLUSTER cannot run in a transaction block). Index "
            "columns are verified before CLUSTER commits and indisclustered per partition afterwards.",
        ),
        _T.SORTING: _post_load(
            "postgresql_index_cluster",
            "Same CREATE INDEX plus CLUSTER as CLUSTERING; sorting columns merge with clustering columns "
            "into one index ordered by tuning order.",
        ),
        _T.BLOOM_FILTERS: _none("unimplemented", "Compatible per the legacy map; no adapter implementation."),
        _T.MATERIALIZED_VIEWS: _none("unimplemented", "Compatible per the legacy map; no adapter implementation."),
        **_pk_fk_entries(),
        **_no_unique_check_entries(
            "paradedb_no_unique_check_rendering",
            "ParadeDBAdapter inherits PostgreSQLAdapter execution rendering PK/FK from flags only (paradedb.py).",
        ),
    },
    "fabric-warehouse": {
        _T.PARTITIONING: _none(
            "delta_ddl_generator:preview_only_tsql_ignores",
            "The Delta preview generator computes partitioning, but FabricWarehouseAdapter executes "
            "T-SQL (FABRIC_DIALECT='tsql'): the shared builder's tsql output ignores tuned partition "
            "columns, generate_tuning_clause returns '' and _optimize_table_definition only adds the "
            "schema (fabric_warehouse.py:857-883). Previously aliased to databricks (rendered _ddl) "
            "-- that claim was false for the warehouse execution path.",
        ),
        **_pk_fk_entries(),
        **_no_unique_check_entries(
            "fabric_warehouse_no_unique_check_rendering",
            "FabricWarehouseAdapter executes T-SQL carrying PK/FK only (fabric_warehouse.py).",
        ),
    },
    "databend": {
        _T.CLUSTERING: _post_load(
            "databend_alter_cluster_by",
            "DatabendAdapter.apply_table_tunings executes ALTER TABLE ... CLUSTER BY from tuned "
            "clustering columns (databend/adapter.py:632-651).",
        ),
        _T.PARTITIONING: _none(
            "unimplemented",
            "DatabendAdapter.generate_tuning_clause only handles CLUSTERING; nothing renders "
            "partitioning at execution.",
        ),
        _T.SORTING: _none(
            "unimplemented",
            "Same gap as PARTITIONING: only CLUSTERING is rendered at execution.",
        ),
        _T.DISTRIBUTION: _none(
            "unimplemented",
            "Same gap as PARTITIONING: only CLUSTERING is rendered at execution.",
        ),
        _T.MATERIALIZED_VIEWS: _none("unimplemented", "Compatible per the legacy map; no adapter implementation."),
        _T.PRIMARY_KEYS: _none(
            "databend_strips_primary_keys",
            "DatabendAdapter strips PRIMARY KEY clauses from every executed CREATE TABLE via "
            "inline regex (databend/adapter.py:678), alongside the foreign-key strip. Tuned keys "
            "reach the DDL builder but never reach the engine.",
        ),
        _T.FOREIGN_KEYS: _none(
            "databend_strips_foreign_keys",
            "DatabendAdapter strips foreign keys from every CREATE TABLE (databend/adapter.py:680); "
            "remaining constraint configuration is informational log-only.",
        ),
        _T.UNIQUE_CONSTRAINTS: _none(
            "databend_no_unique_check_rendering",
            "DatabendAdapter strips PK/FK and only renders CLUSTERING; UNIQUE/CHECK clauses are "
            "never produced or consumed from tuning.",
        ),
        _T.CHECK_CONSTRAINTS: _none(
            "databend_no_unique_check_rendering",
            "Same gap as UNIQUE_CONSTRAINTS: never produced or consumed from tuning.",
        ),
    },
    "datafusion": {
        _T.PRIMARY_KEYS: _none(
            "datafusion_unconstrained_schema",
            "DataFusionAdapter builds its schema explicitly without constraints even when enabled "
            "(datafusion.py:558-564).",
        ),
        _T.FOREIGN_KEYS: _none(
            "datafusion_unconstrained_schema",
            "Same as PRIMARY_KEYS: schema is created without constraints.",
        ),
        _T.UNIQUE_CONSTRAINTS: _none(
            "datafusion_unconstrained_schema",
            "Same as PRIMARY_KEYS: schema is created without constraints.",
        ),
        _T.CHECK_CONSTRAINTS: _none(
            "datafusion_unconstrained_schema",
            "Same as PRIMARY_KEYS: schema is created without constraints.",
        ),
    },
    "motherduck": {
        _T.SORTING: _post_load(
            "duckdb_ctas_sort",
            "MotherDuckAdapter runs the shared CTAS-sort path from tuned sorting columns during load "
            "(motherduck.py:337, via base/sorted_ingestion.py).",
        ),
        _T.PARTITIONING: _none(
            "motherduck_external_views_unpartitioned",
            "MotherDuckAdapter.create_schema executes benchmark DDL verbatim without tuning_config "
            "(motherduck.py:307-318) and loads through external views; tuned partitioning columns "
            "reshape nothing.",
        ),
        _T.CLUSTERING: _none(
            "motherduck_no_cluster_rendering",
            "Same execution gap as PARTITIONING: no post-create CLUSTER step consumes tuning.",
        ),
        _T.DISTRIBUTION: _none(
            "motherduck_not_applicable_single_writer",
            "MotherDuck is single-writer cloud DuckDB; nothing renders distribution columns.",
        ),
        _T.MATERIALIZED_VIEWS: _none("unimplemented", "Compatible per the legacy map; no adapter implementation."),
        _T.PRIMARY_KEYS: _none(
            "motherduck_drops_tuning_config",
            "create_schema drops tuning_config and apply_constraint_configuration is a pass-through "
            "no-op (motherduck.py:354-355).",
        ),
        _T.FOREIGN_KEYS: _none(
            "motherduck_drops_tuning_config",
            "Same as PRIMARY_KEYS: tuning never reaches DDL or the adapter.",
        ),
        _T.UNIQUE_CONSTRAINTS: _none(
            "motherduck_drops_tuning_config",
            "Same as PRIMARY_KEYS: tuning never reaches DDL or the adapter.",
        ),
        _T.CHECK_CONSTRAINTS: _none(
            "motherduck_drops_tuning_config",
            "Same as PRIMARY_KEYS: tuning never reaches DDL or the adapter.",
        ),
    },
    "starrocks": {
        _T.PARTITIONING: _ddl(
            "starrocks_ddl_generator",
            "core.tuning.generators.starrocks.StarRocksDDLGenerator renders PARTITION BY from tuned "
            "partitioning columns at CREATE TABLE time. Consumed by both dry-run preview "
            "(get_ddl_generator('starrocks')) and real execution -- benchbox/platforms/starrocks/"
            "workload.py's _optimize_table_definition resolves the same generator via "
            "_resolve_tuned_ddl_clauses and injects the tuned PARTITION BY clause. No drift between "
            "preview and execution.",
        ),
        _T.SORTING: _ddl(
            "starrocks_ddl_generator",
            "Rendered as StarRocks' ORDER BY sort-key clause from tuned sorting columns via the shared "
            "generator (see PARTITIONING entry for the shared preview/execution note). The table's key "
            "model (DUPLICATE KEY / PRIMARY KEY) is still chosen from the source DDL's primary key by the "
            "workload rewrite, independent of the ORDER BY sort key.",
        ),
        _T.DISTRIBUTION: _ddl(
            "starrocks_ddl_generator",
            "DISTRIBUTED BY HASH(...) BUCKETS N is engine-mandatory (StarRocks requires a distribution "
            "key). The generator renders it, and a tuned distribution column now overrides the "
            "first-column default: the workload picks the hash key from the tuned config when present, "
            "falling back to StarRocksDDLGenerator.render_distribution_clause(<first_column>) for untuned "
            "tables -- byte-identical to the previous bespoke injection (BUCKETS 8). Preview and "
            "execution render through the same generator.",
        ),
        _T.PRIMARY_KEYS: _ddl(
            _INLINE_CONSTRAINT,
            "Tuned primary keys choose the StarRocks key model: the workload rewrite reads the "
            "PK columns from the tuned DDL and emits PRIMARY KEY when they lead the table, else "
            "DUPLICATE KEY (starrocks/workload.py:122-152).",
        ),
        _T.FOREIGN_KEYS: _none(
            "starrocks_strips_foreign_keys",
            "StarRocksWorkloadMixin strips foreign keys from every CREATE TABLE (starrocks/workload.py:120).",
        ),
        _T.UNIQUE_CONSTRAINTS: _none(
            "starrocks_no_unique_check_rendering",
            "StarRocksWorkloadMixin._optimize_table_definition handles PARTITION/DISTRIBUTION/"
            "ORDER BY plus PK-model selection and foreign-key stripping only (starrocks/workload.py:"
            "109-198); nothing renders, strips, or validates UNIQUE/CHECK clauses.",
        ),
        _T.CHECK_CONSTRAINTS: _none(
            "starrocks_no_unique_check_rendering",
            "Same gap as UNIQUE_CONSTRAINTS: no UNIQUE/CHECK handling in the workload rewrite.",
        ),
    },
    "doris": {
        _T.PARTITIONING: _none(
            "doris_ddl_generator:preview_only",
            "core.tuning.generators.doris.DorisDDLGenerator computes PARTITION BY from tuned columns "
            "for dry-run preview, but DorisAdapter inherits NoOpTableTuningMixin (doris.py:187) and "
            "renders nothing at execution. Corrects the prior unaudited _ddl claim.",
        ),
        _T.SORTING: _none(
            "doris_ddl_generator:preview_only",
            "Same execution gap as PARTITIONING: preview only, NoOp at execution.",
        ),
        _T.DISTRIBUTION: _none(
            "doris_ddl_generator:preview_only",
            "Same execution gap as PARTITIONING: preview only, NoOp at execution.",
        ),
        _T.MATERIALIZED_VIEWS: _none(
            "unimplemented",
            "Doris supports materialized views, but DorisAdapter has no code that creates one from tuning.",
        ),
        _T.PRIMARY_KEYS: _none(
            "doris_strips_primary_keys",
            "The shared tuning-aware DDL path would emit tuned primary keys, but DorisAdapter "
            "strips PRIMARY KEY clauses from every CREATE TABLE (doris.py:803-805).",
        ),
        _T.FOREIGN_KEYS: _none(
            "doris_strips_foreign_keys",
            "DorisAdapter strips foreign keys from every CREATE TABLE (doris.py:802).",
        ),
        _T.UNIQUE_CONSTRAINTS: _none(
            "doris_no_unique_check_rendering",
            "DorisAdapter renders nothing at execution (NoOpTableTuningMixin) and strips PK/FK; "
            "UNIQUE/CHECK clauses are never produced or consumed from tuning.",
        ),
        _T.CHECK_CONSTRAINTS: _none(
            "doris_no_unique_check_rendering",
            "Same gap as UNIQUE_CONSTRAINTS: never produced or consumed from tuning.",
        ),
    },
    "trino": {
        _T.PARTITIONING: _none(
            "trino_ddl_generator:preview_only",
            "core.tuning.generators.trino.TrinoDDLGenerator computes a real partitioned_by/partitioning "
            "WITH-property clause from tuned columns for dry-run preview (get_ddl_generator('trino')), "
            "but benchbox/platforms/trino.py's TrinoAdapter never consumes it at execution time: "
            "create_schema builds DDL via _create_schema_with_tuning (primary/foreign-key toggle only) "
            "then _optimize_table_definition (trino.py:190-218, file-format/WITH-clause string tweaks "
            "only, no tuning). TrinoAdapter.generate_tuning_clause (trino.py:220-266) re-implements "
            "similar WITH-properties logic but has no call site anywhere in the codebase (a repo-wide "
            "search for '.generate_tuning_clause(' finds only its own definition and unit tests that "
            "call it directly). apply_table_tunings (trino.py:268-298) only logs partition/sort column "
            "names, emitting no SQL. Renderer-migration follow-up: wire generate_tuning_clause (or "
            "get_ddl_generator) into the real create_schema path.",
        ),
        _T.DISTRIBUTION: _none(
            "trino_ddl_generator:preview_only",
            "Same gap as PARTITIONING: the generator computes bucketed_by/bucket_count for the Hive "
            "connector (trino.py:178-187) from tuned distribution columns, but nothing in TrinoAdapter's "
            "execution path renders it.",
        ),
        _T.SORTING: _none(
            "trino_ddl_generator:preview_only",
            "Same gap as PARTITIONING: the generator computes sorted_by for Hive/Iceberg connectors "
            "(trino.py:201-215) from tuned sorting columns, but nothing in TrinoAdapter's execution path "
            "renders it.",
        ),
        _T.CLUSTERING: _none(
            "trino_ddl_generator:preview_only",
            "Trino has no separate clustering clause; the generator folds clustering columns into "
            "sorted_by (trino.py:202-210, see SORTING entry), and the same execution-path gap applies.",
        ),
        _T.PRIMARY_KEYS: _none(
            "trino_strips_primary_keys",
            "PrestoTrinoAdapterBase._optimize_table_definition strips PRIMARY KEY clauses from every "
            "CREATE TABLE via ddl_helpers strip_primary_keys (presto_trino_adapter_base.py).",
        ),
        _T.FOREIGN_KEYS: _ddl(_INLINE_CONSTRAINT, _CONSTRAINT_NOTE),
        **_no_unique_check_entries(
            "trino_no_unique_check_rendering",
            "TrinoAdapter renders no UNIQUE/CHECK clause at execution (trino.py).",
        ),
    },
    "presto": {
        _T.PARTITIONING: _none(
            "trino_ddl_generator:preview_only",
            "get_ddl_generator('presto') resolves to the same core.tuning.generators.trino."
            "TrinoDDLGenerator as trino (real partitioned_by/partitioning WITH-property clause computed "
            "from tuned columns for dry-run preview). benchbox/platforms/presto.py's PrestoAdapter never "
            "consumes it at execution time: _optimize_table_definition (presto.py:214-242) only does "
            "type/format string tweaks, PrestoAdapter.generate_tuning_clause (presto.py:244-272) has no "
            "call site anywhere in the codebase, and apply_table_tunings (presto.py:274-282) only logs "
            "via log_partition_tunings(...). See trino's PARTITIONING entry for the shared-generator "
            "note.",
        ),
        _T.DISTRIBUTION: _none(
            "trino_ddl_generator:preview_only",
            "Same gap as PARTITIONING: the shared generator computes bucketed_by/bucket_count from tuned "
            "distribution columns, but PrestoAdapter's execution path never renders it.",
        ),
        _T.SORTING: _none(
            "trino_ddl_generator:preview_only",
            "Same gap as PARTITIONING: the shared generator computes sorted_by from tuned sorting "
            "columns, but PrestoAdapter's execution path never renders it.",
        ),
        _T.CLUSTERING: _none(
            "trino_ddl_generator:preview_only",
            "Presto has no separate clustering clause; folded into sorted_by upstream (see SORTING "
            "entry), and the same execution-path gap applies.",
        ),
        _T.PRIMARY_KEYS: _none(
            "presto_strips_primary_keys",
            "PrestoTrinoAdapterBase._optimize_table_definition strips PRIMARY KEY clauses from every "
            "CREATE TABLE via ddl_helpers strip_primary_keys (presto_trino_adapter_base.py).",
        ),
        _T.FOREIGN_KEYS: _ddl(_INLINE_CONSTRAINT, _CONSTRAINT_NOTE),
        **_no_unique_check_entries(
            "presto_no_unique_check_rendering",
            "PrestoAdapter renders no UNIQUE/CHECK clause at execution (presto.py).",
        ),
    },
    "athena": {
        _T.PARTITIONING: _none(
            "athena_ddl_generator:preview_incomplete_and_unconsumed",
            "Even the dry-run generator (AthenaDDLGenerator(TrinoDDLGenerator), trino.py:343-444) does "
            "not emit a real PARTITIONED BY clause: generate_create_table_ddl only appends a comment "
            "placeholder ('-- Note: PARTITIONED BY clause should be added with column types', "
            "trino.py:409-413) when partitioned_by is present. Real execution "
            "(benchbox/platforms/athena.py's AthenaAdapter) never calls AthenaDDLGenerator at all: "
            "create_schema (athena.py:627-695) rewrites the statement via _convert_to_external_table "
            "(athena.py:697-801), an independent regex-based CREATE EXTERNAL TABLE builder that never "
            "adds PARTITIONED BY and strips any WITH clause. AthenaAdapter.generate_tuning_clause "
            "(athena.py:1380-1399) has no call site anywhere in the codebase. apply_table_tunings "
            "(athena.py:1401-1415) logs 'applied at table creation time', which is not accurate -- "
            "nothing is applied. Renderer-migration follow-up: fix the dry-run generator's placeholder "
            "comment and wire real rendering into _convert_to_external_table.",
        ),
        _T.DISTRIBUTION: _none(
            "athena_ddl_generator:preview_incomplete_and_unconsumed",
            "The inherited generator computes bucketed_by/bucket_count from tuned distribution columns, "
            "but AthenaDDLGenerator's own generate_create_table_ddl explicitly excludes them from the "
            "emitted TBLPROPERTIES (the remaining_props filter, trino.py:433-437) -- silently dropped "
            "even in dry-run preview. Real execution never renders it either; see PARTITIONING entry.",
        ),
        _T.SORTING: _none(
            "athena_ddl_generator:preview_incomplete_and_unconsumed",
            "Same silent-drop gap as DISTRIBUTION: sorted_by is computed by the inherited generator from "
            "tuned sorting columns but excluded from Athena's TBLPROPERTIES output, and never rendered "
            "at execution either.",
        ),
        _T.CLUSTERING: _none(
            "athena_ddl_generator:preview_incomplete_and_unconsumed",
            "Athena has no separate clustering clause; folded into sorted_by upstream, which is dropped "
            "(see SORTING entry).",
        ),
        _T.PRIMARY_KEYS: _none(
            "athena_strips_primary_keys",
            "Athena tables are Hive CREATE EXTERNAL TABLE statements, which have no constraint syntax, so "
            "AthenaAdapter._convert_to_external_table removes PRIMARY KEY clauses before execution.",
        ),
        _T.FOREIGN_KEYS: _none(
            "athena_strips_foreign_keys",
            "Athena CREATE EXTERNAL TABLE has no constraint syntax, so "
            "AthenaAdapter._convert_to_external_table removes FOREIGN KEY and REFERENCES clauses.",
        ),
        **_no_unique_check_entries(
            "athena_no_unique_check_rendering",
            "AthenaAdapter._convert_to_external_table builds CREATE EXTERNAL TABLE without UNIQUE/CHECK "
            "clauses (athena.py).",
        ),
    },
    "firebolt": {
        _T.DISTRIBUTION: _none(
            "firebolt_ddl_generator:preview_only",
            "core.tuning.generators.firebolt.FireboltDDLGenerator computes a real PRIMARY INDEX "
            "(col1, col2) clause from tuned distribution columns for dry-run preview "
            "(firebolt.py:144-149, 195-196), but benchbox/platforms/firebolt.py's FireboltAdapter never "
            "consumes it: create_schema uses _create_schema_with_tuning (primary/foreign-key toggle "
            "only) then _optimize_table_definition (firebolt.py:1267-1296, type-mapping/"
            "constraint-stripping only). FireboltAdapter.generate_tuning_clause (firebolt.py:1374-1411) "
            "has no call site anywhere in the codebase. apply_table_tunings (firebolt.py:1413-1421) only "
            "logs via log_partition_tunings(...).",
        ),
        _T.PARTITIONING: _none(
            "firebolt_ddl_generator:preview_only",
            "Same gap as DISTRIBUTION: the generator computes a real PARTITION BY clause from tuned "
            "partitioning columns (firebolt.py:151-156, 198-199) for dry-run preview only; execution "
            "never renders it.",
        ),
        _T.SORTING: _none(
            "firebolt_ddl_generator:log_only",
            "Firebolt sorts within segments automatically; the generator only info-logs sorting hints "
            "(firebolt.py:126-133) instead of computing a clause, even for dry-run preview.",
        ),
        _T.CLUSTERING: _none(
            "firebolt_ddl_generator:log_only",
            "Clustering is achieved through PRIMARY INDEX in Firebolt; the generator only info-logs "
            "clustering hints (firebolt.py:135-142) instead of computing a clause, even for dry-run "
            "preview.",
        ),
        _T.PRIMARY_KEYS: _none(
            "firebolt_strips_primary_keys",
            "FireboltAdapter._optimize_table_definition strips PRIMARY KEY clauses from every "
            "CREATE TABLE via ddl_helpers strip_primary_keys (firebolt.py).",
        ),
        _T.FOREIGN_KEYS: _none(
            "firebolt_strips_foreign_keys",
            "FireboltAdapter._optimize_table_definition strips FOREIGN KEY clauses from every "
            "CREATE TABLE via ddl_helpers strip_foreign_keys (firebolt.py).",
        ),
        **_no_unique_check_entries(
            "firebolt_no_unique_check_rendering",
            "FireboltAdapter renders no UNIQUE/CHECK clause at execution (firebolt.py).",
        ),
    },
    "azure-synapse": {
        _T.DISTRIBUTION: _none(
            "engine_default_not_tuned",
            "benchbox/platforms/azure_synapse.py's real _optimize_table_definition "
            "(azure_synapse.py:1046-1069, called at actual schema-creation time) does add a real "
            "WITH (DISTRIBUTION = ...) clause, but the value always comes from self.distribution_default "
            "(a platform/CLI option defaulted to ROUND_ROBIN, azure_synapse.py:117), never from "
            "TableTuning.distribution columns. The generator "
            "(core.tuning.generators.azure_synapse.AzureSynapseDDLGenerator) computes a real tuned "
            "HASH([col]) clause from those columns for dry-run preview only (azure_synapse.py:192-199, "
            "249-251); AzureSynapseAdapter.generate_tuning_clause (azure_synapse.py:1196-1228), which "
            "would correctly emit it, has no call site anywhere in the codebase. Same "
            "engine-mandatory-baseline-not-tuned pattern as StarRocks' DISTRIBUTION entry above: real "
            "DDL is emitted, but it is never derived from the tuned configuration.",
        ),
        _T.PARTITIONING: _none(
            "azure_synapse_ddl_generator:preview_only",
            "The generator computes a real (if structurally incomplete -- the RANGE RIGHT FOR VALUES () "
            "boundary list is always empty) PARTITION clause from tuned columns for dry-run preview "
            "(azure_synapse.py:201-209, 254-255), but real execution's _optimize_table_definition "
            "(azure_synapse.py:1046-1069) never adds a PARTITION clause at all.",
        ),
        _T.SORTING: _none(
            "azure_synapse_ddl_generator:log_only",
            "Azure Synapse sorting is handled by the index type; the generator only info-logs sorting "
            "hints (azure_synapse.py:173-181) instead of computing a clause, even for dry-run preview.",
        ),
        _T.CLUSTERING: _none(
            "azure_synapse_ddl_generator:log_only",
            "Clustering is achieved via DISTRIBUTION and CLUSTERED INDEX; the generator only info-logs "
            "clustering hints (azure_synapse.py:183-189) instead of computing a clause, even for dry-run "
            "preview.",
        ),
        **_pk_fk_entries(),
        **_no_unique_check_entries(
            "azure_synapse_no_unique_check_rendering",
            "AzureSynapseAdapter real DDL carries PK/FK with the engine-default DISTRIBUTION only (azure_synapse.py).",
        ),
    },
    "timescaledb": {
        _T.PARTITIONING: _ddl(
            "timescaledb_native_layout",
            "The configured date or timestamp partition column becomes the hypertable time dimension. "
            "Requests with multiple partition columns or incompatible column types are recorded as dropped. "
            "The dimensions catalog verifies the selected column.",
        ),
        _T.SORTING: _ddl(
            "timescaledb_native_layout",
            "Sorting columns configure compress_orderby and enable compression. "
            "The compression_settings catalog verifies the ordered columns and directions.",
        ),
        _T.DISTRIBUTION: _ddl(
            "timescaledb_native_layout",
            "Distribution columns configure native space partitioning and compression segmentation. "
            "Compression settings are verified from the TimescaleDB catalog.",
        ),
        _T.CLUSTERING: _ddl(
            "timescaledb_native_layout",
            "Clustering columns configure compress_segmentby and enable compression. "
            "Compression settings are verified from the TimescaleDB catalog.",
        ),
        **_pk_fk_entries(),
        **_no_unique_check_entries(
            "timescaledb_no_unique_check_rendering",
            "TimescaleDBAdapter hypertable and compression paths consume no UNIQUE/CHECK tuning (timescaledb.py).",
        ),
    },
    "questdb": {
        _T.PARTITIONING: _none(
            "questdb_ddl_generator:preview_only_separate_hardcoded_map",
            "core.tuning.generators.questdb.QuestDBDDLGenerator computes a real timestamp(col) + "
            "PARTITION BY suffix from the first tuned PARTITIONING column (questdb.py:154-170) for "
            "dry-run preview, falling back to its own module-level TPCH_DESIGNATED_TIMESTAMP/"
            "TPCH_PARTITION_BY dicts (questdb.py:49-64, 171-182) when no tuning is configured. Real "
            "execution (benchbox/platforms/questdb.py's QuestDBAdapter._apply_questdb_schema_"
            "enhancements, questdb.py:521-558) never reads TableTuning at all -- it applies timestamp/"
            "PARTITION BY using its own separate, adapter-local hardcoded TPCH_TIMESTAMP_COLUMNS/"
            "TPCH_PARTITION_DEFAULTS dicts (questdb.py:67-103), keyed by table name. For BenchBox's "
            "TPC-H defaults the two hardcoded maps happen to agree (lineitem/orders, MONTH), but any "
            "custom tuned PARTITIONING configuration is silently ignored by real execution. QuestDBAdapter "
            "defines no generate_tuning_clause/apply_table_tunings override at all.",
        ),
        _T.SORTING: _none(
            "questdb_ddl_generator:log_only",
            "QuestDB sorts by its designated timestamp automatically; the generator only info-logs "
            "sorting hints (questdb.py:185-191) instead of computing a clause, even for dry-run preview.",
        ),
        _T.DISTRIBUTION: _none(
            "questdb_ddl_generator:not_applicable_single_node",
            "QuestDB is single-node; the generator only warning-logs distribution hints and ignores the "
            "columns (questdb.py:193-199) instead of computing a clause, even for dry-run preview.",
        ),
        _T.PRIMARY_KEYS: _none(
            "questdb_strips_primary_keys",
            "QuestDBAdapter.create_schema strips PRIMARY KEY clauses via _strip_pk_constraints (questdb.py).",
        ),
        _T.FOREIGN_KEYS: _none(
            "questdb_strips_foreign_keys",
            "QuestDBAdapter.create_schema strips FOREIGN KEY clauses via ddl_helpers strip_foreign_keys (questdb.py).",
        ),
        **_no_unique_check_entries(
            "questdb_no_unique_check_rendering",
            "QuestDB schema enhancement maps hardcoded timestamp/partition columns only and builds no "
            "UNIQUE/CHECK clause (questdb.py).",
        ),
    },
    "pg-duckdb": {
        _T.PARTITIONING: _ddl(
            "postgresql_partition_by_date_domain",
            "PgDuckDBAdapter enables PostgreSQLAdapter.create_schema partitioning: the "
            "PostgreSQLDDLGenerator PARTITION BY clause (RANGE for date columns, HASH for integers, "
            "LIST otherwise) plus child partitions (yearly RANGE children and a DEFAULT partition "
            "for RANGE; fixed children without a DEFAULT partition for HASH). Skipped, and recorded "
            "as dropped, while primary or unique keys are requested. The adapter verifies the "
            "strategy and columns from pg_get_partkeydef after schema creation.",
        ),
        _T.CLUSTERING: _post_load(
            "postgresql_index_cluster",
            "After each heap table loads, PgDuckDBAdapter.apply_post_load_tunings runs CREATE INDEX "
            "on the merged clustering and sorting columns (ordered by tuning order across both "
            "lists), then CLUSTER USING that index. Index columns are verified from pg_index before "
            "CLUSTER commits, and indisclustered is verified per partition afterwards; CLUSTER itself "
            "runs in autocommit because it cannot run inside a transaction block.",
        ),
        _T.SORTING: _post_load(
            "postgresql_index_cluster",
            "Same CREATE INDEX plus CLUSTER as CLUSTERING. Sorting columns merge with clustering "
            "columns into one index ordered by tuning order, so sorting is realized by the same "
            "clustered index rather than a separate ordering.",
        ),
        **_pk_fk_entries(),
        **_no_unique_check_entries(
            "pg_duckdb_no_unique_check_rendering",
            "PgDuckDBAdapter inherits PostgreSQLAdapter execution carrying PK/FK only (pg_duckdb.py).",
        ),
    },
    "pg-mooncake": {
        _T.PARTITIONING: _none(
            "columnstore_manages_own_layout",
            "core.tuning.generators.pg_mooncake.PgMooncakeDDLGenerator.generate_tuning_clauses always "
            "returns an empty TuningClauses() regardless of input (pg_mooncake.py:61-81) -- by design, "
            "since Parquet/Iceberg columnstore tables manage their own storage layout. Consistent (not "
            "divergent) with the adapter: benchbox/platforms/pg_mooncake.py's PgMooncakeAdapter.supports_"
            "tuning_type unconditionally returns False for every tuning type (pg_mooncake.py:544-546), and "
            "_transform_create_statement is a deliberate passthrough that leaves the heap-table DDL "
            "unchanged for the bulk COPY load path (pg_mooncake.py:196-204) -- tuning is promoted away "
            "entirely once mooncake.create_table mirrors the loaded heap table.",
        ),
        _T.CLUSTERING: _none(
            "columnstore_manages_own_layout",
            "Same as PARTITIONING: the generator always returns empty clauses, and "
            "supports_tuning_type() is unconditionally False.",
        ),
        _T.SORTING: _none(
            "columnstore_manages_own_layout",
            "Same as PARTITIONING: the generator always returns empty clauses, and "
            "supports_tuning_type() is unconditionally False.",
        ),
        **_pk_fk_entries(),
        **_no_unique_check_entries(
            "pg_mooncake_no_unique_check_rendering",
            "PgMooncakeAdapter inherits PostgreSQLAdapter execution carrying PK/FK only (pg_mooncake.py).",
        ),
    },
}


SESSION_CONFIG_CAPABILITIES: dict[str, TuningCapability] = {
    "spark": _session(
        "spark_session_config",
        "Each key in platform_optimizations.spark is set with spark.conf.set after the session starts, read back, "
        "and recorded in the applied-tuning ledger.",
    ),
    "velox": _session("spark_session_config", "Applied the same way as on Spark; spark.gluten.* keys are accepted."),
}


_INTERFACE_KNOWN_PLATFORMS: frozenset[str] = frozenset(
    {
        "duckdb",
        "snowflake",
        "bigquery",
        "redshift",
        "clickhouse",
        "databricks",
        "sqlite",
        "postgresql",
        "mysql",
    }
)


def interface_compatibility_map() -> dict[str, frozenset[TuningType]]:
    return {platform: frozenset(PLATFORM_TUNING_CAPABILITIES[platform]) for platform in _INTERFACE_KNOWN_PLATFORMS}


def resolve_platform_key(platform: str) -> str:
    platform_key = platform.split(":", 1)[0].strip().lower().replace("_", "-")
    return PLATFORM_ALIASES.get(platform_key, platform_key)


def get_capability(platform: str, tuning_type: TuningType) -> TuningCapability | None:
    platform_key = resolve_platform_key(platform)
    entries = PLATFORM_TUNING_CAPABILITIES.get(platform_key)
    if entries is None:
        return None
    return entries.get(tuning_type)


def is_not_rendered_at_execution(capability: TuningCapability | None) -> bool:
    if capability is None:
        return False
    return capability.rendered_via == "none" or ":preview_only" in capability.mechanism_id


def not_rendered_reason(capability: TuningCapability) -> str:
    mechanism_id = capability.mechanism_id
    if "strips" in mechanism_id:
        reason = "the platform adapter removes it from CREATE TABLE statements"
    elif "unconstrained" in mechanism_id:
        reason = "the schema is created without constraints"
    elif "no_unique_check_rendering" in mechanism_id:
        reason = "table DDL never carries this constraint"
    elif "preview" in mechanism_id or mechanism_id.endswith("hint_only"):
        reason = "it is generated only for dry-run previews and is not applied when tables are created"
    elif "log_only" in mechanism_id:
        reason = "the adapter only logs it and does not apply it"
    else:
        reason = "BenchBox does not apply it on this platform"
    return f"{reason} ({mechanism_id})"


def unapplied_reason(capability: TuningCapability, sorted_ingestion_mode: str | None) -> str | None:
    if is_not_rendered_at_execution(capability):
        return not_rendered_reason(capability)
    if capability.requires_sorted_ingestion and sorted_ingestion_mode in (None, "off"):
        return "it runs only when sorted ingestion is enabled; set sorted_ingestion_mode to auto or force"
    return None


def known_registry_platforms() -> frozenset[str]:
    return frozenset(PLATFORM_TUNING_CAPABILITIES)


DEFAULT_FALLBACK_DESCRIPTION = "basic constraints"

PLATFORM_FALLBACK_DESCRIPTIONS: dict[str, str] = {
    "clickhouse": "OLAP session pack",
    "polars": "engine runtime defaults (streaming)",
    "pandas": "engine runtime defaults",
    "dask": "engine runtime defaults",
    "cudf": "engine runtime defaults",
    "datafusion": "engine runtime defaults",
}


def get_fallback_description(platform: str | None) -> str:
    if not platform:
        return DEFAULT_FALLBACK_DESCRIPTION
    base = platform.lower().split(":", 1)[0]
    if base.endswith("-df"):
        base = base[: -len("-df")]
    return PLATFORM_FALLBACK_DESCRIPTIONS.get(resolve_platform_key(base), DEFAULT_FALLBACK_DESCRIPTION)


WORKLOAD_PROFILE_MAPPED_PLATFORMS: frozenset[str] = frozenset(
    {"databricks", "duckdb", "bigquery", "redshift", "snowflake"}
)


__all__ = [
    "DEFAULT_FALLBACK_DESCRIPTION",
    "PLATFORM_FALLBACK_DESCRIPTIONS",
    "RenderedVia",
    "TuningCapability",
    "PLATFORM_ALIASES",
    "PLATFORM_TUNING_CAPABILITIES",
    "WORKLOAD_PROFILE_MAPPED_PLATFORMS",
    "get_capability",
    "get_fallback_description",
    "interface_compatibility_map",
    "known_registry_platforms",
    "resolve_platform_key",
]
