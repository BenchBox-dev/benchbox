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


def _ddl(mechanism_id: str, notes: str = "") -> TuningCapability:
    return TuningCapability(rendered_via="ddl", mechanism_id=mechanism_id, notes=notes)


def _post_load(mechanism_id: str, notes: str = "") -> TuningCapability:
    return TuningCapability(rendered_via="post_load", mechanism_id=mechanism_id, notes=notes)


def _session(mechanism_id: str, notes: str = "") -> TuningCapability:
    return TuningCapability(rendered_via="session", mechanism_id=mechanism_id, notes=notes)


def _none(mechanism_id: str, notes: str = "") -> TuningCapability:
    return TuningCapability(rendered_via="none", mechanism_id=mechanism_id, notes=notes)


_T = TuningType
_INLINE_CONSTRAINT = "inline_column_constraint"
_CONSTRAINT_NOTE = "Rendered as an inline column/table constraint clause at CREATE TABLE time."


def _constraint_entries() -> dict[TuningType, TuningCapability]:
    return {
        _T.PRIMARY_KEYS: _ddl(_INLINE_CONSTRAINT, _CONSTRAINT_NOTE),
        _T.FOREIGN_KEYS: _ddl(_INLINE_CONSTRAINT, _CONSTRAINT_NOTE),
        _T.UNIQUE_CONSTRAINTS: _ddl(_INLINE_CONSTRAINT, _CONSTRAINT_NOTE),
        _T.CHECK_CONSTRAINTS: _ddl(_INLINE_CONSTRAINT, _CONSTRAINT_NOTE),
    }


PLATFORM_ALIASES: dict[str, str] = {
    "clickhouse-local": "clickhouse",
    "clickhouse-server": "clickhouse",
    "clickhouse-cloud": "clickhouse",
    "chdb": "clickhouse",
    "spark": "databricks",
    "delta": "databricks",
    "fabric-warehouse": "databricks",
    "synapse": "azure-synapse",
}


PLATFORM_TUNING_CAPABILITIES: dict[str, dict[TuningType, TuningCapability]] = {
    "duckdb": {
        _T.SORTING: _post_load(
            "duckdb_ctas_sort",
            "CTAS reorder (CREATE OR REPLACE TABLE ... AS SELECT * FROM ... ORDER BY ...) run after data "
            "load; DuckDB has no inline CREATE TABLE ORDER BY syntax. Consumed via "
            "core.tuning.generators.duckdb.DuckDBDDLGenerator.generate_ctas_ddl by both dry-run preview "
            "and real execution as of the w2 duckdb migration.",
        ),
        _T.PARTITIONING: _none(
            "duckdb_copy_to_hint_only",
            "DuckDBDDLGenerator.generate_tuning_clauses logs a COPY TO Hive-partitioning hint for "
            "partition columns but no BenchBox code path applies it to the physical benchmark schema. "
            "Documented gap, not migrated this round (compatible per the legacy map; no rendering yet).",
        ),
        **_constraint_entries(),
    },
    "clickhouse": {
        _T.PARTITIONING: _ddl(
            "clickhouse_ddl_generator:PARTITION_BY",
            "core.tuning.generators.clickhouse.ClickHouseDDLGenerator renders PARTITION BY at CREATE "
            "TABLE time. Consumed by dry-run preview and (as of the w2 clickhouse migration) real "
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
        _T.MATERIALIZED_VIEWS: _none(
            "unimplemented",
            "Compatible per the legacy map; no adapter code creates a materialized view.",
        ),
    },
    "databricks": {
        _T.PARTITIONING: _ddl(
            "delta_partitioned_by",
            "Rendered as Delta PARTITIONED BY at CREATE TABLE time via the cloud_spark DDL mixin "
            "(third renderer per ADR-3; not migrated to core.tuning.generators this round -- Databricks "
            "migration is explicitly out of scope, owned by the blocked databricks-liquid-clustering "
            "TODO).",
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
        **_constraint_entries(),
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
        **_constraint_entries(),
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
        _T.CHECK_CONSTRAINTS: _ddl(_INLINE_CONSTRAINT, _CONSTRAINT_NOTE),
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
        ),
        _T.PARTITIONING: _none(
            "redshift_no_partition_rendering",
            "Redshift has no CREATE TABLE partition clause; generate_tuning_clause explicitly emits nothing "
            "for partitioning (redshift.py:2512-2517) and apply_table_tunings only logs the strategy "
            "(redshift.py:2629-2636). Nothing is rendered at execution.",
        ),
        _T.MATERIALIZED_VIEWS: _none("unimplemented", "Compatible per the legacy map; no adapter implementation."),
        **_constraint_entries(),
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
    "sqlite": _constraint_entries(),
    "postgresql": {
        _T.PARTITIONING: _none(
            "postgresql_ddl_generator:preview_only",
            "core.tuning.generators.postgresql.PostgreSQLDDLGenerator computes a real PARTITION BY RANGE/"
            "LIST/HASH clause from tuned columns (generators/postgresql.py:139) for dry-run preview, but "
            "real execution never renders it. PostgreSQLAdapter.generate_tuning_clause unconditionally "
            "returns '' (postgresql.py:972-977) and has no production call site; apply_table_tunings and "
            "apply_unified_tuning are both no-ops (postgresql.py:968-970, 979-981); and get_create_tables_sql "
            "consumes tuning_config only for PK/FK constraint flags. Corrects the prior false "
            "_ddl(adapter_mixin:PostgreSQLAdapter.generate_tuning_clause) claim (already flagged in the "
            "pg-duckdb entry below).",
        ),
        _T.CLUSTERING: _none(
            "postgresql_ddl_generator:preview_only",
            "Same execution gap as PARTITIONING: the preview generator emits a CREATE INDEX + CLUSTER pair "
            "(generators/postgresql.py:141-159) but the uncalled generate_tuning_clause mixin returns '' and "
            "the no-op apply path renders nothing at execution.",
        ),
        _T.BLOOM_FILTERS: _none("unimplemented", "Compatible per the legacy map; no adapter implementation."),
        _T.MATERIALIZED_VIEWS: _none("unimplemented", "Compatible per the legacy map; no adapter implementation."),
        **_constraint_entries(),
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
        **_constraint_entries(),
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
    },
    "doris": {
        _T.PARTITIONING: _ddl(
            "doris_ddl_generator",
            "core.tuning.generators.doris.DorisDDLGenerator exists and is reachable via "
            "get_ddl_generator('doris') for dry-run preview. Adapter execution-path parity was not "
            "audited this round (Doris is not in this TODO's migration order).",
        ),
        _T.SORTING: _ddl("doris_ddl_generator", "See PARTITIONING entry."),
        _T.DISTRIBUTION: _ddl("doris_ddl_generator", "See PARTITIONING entry."),
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
    },
    "timescaledb": {
        _T.PARTITIONING: _none(
            "timescaledb_hypertable_hardcoded_time_column",
            "core.tuning.generators.timescaledb.TimescaleDBDDLGenerator computes a real "
            "SELECT create_hypertable(...) post_create_statement using the first tuned PARTITIONING "
            "column as the time column (timescaledb.py:109-143) for dry-run preview only. Real execution "
            "(benchbox/platforms/timescaledb.py's TimescaleDBAdapter._convert_to_hypertables, "
            "timescaledb.py:378-447) ignores TableTuning entirely: it only converts tables that have a "
            "column literally named 'time' (information_schema lookup, timescaledb.py:459-467) and always "
            "passes the literal string 'time' to create_hypertable(), never the configured tuning column. "
            "TPC-H/TPC-DS benchmark schemas have no column literally named 'time', so this path does not "
            "fire for BenchBox's benchmarks today. TimescaleDBAdapter does not override "
            "apply_table_tunings/generate_tuning_clause, so it inherits PostgreSQLAdapter's unconditional "
            "no-ops (postgresql.py:894-903).",
        ),
        _T.SORTING: _none(
            "timescaledb_compression_hardcoded_segmentby",
            "The generator computes a real timescaledb.compress_orderby setting from tuned SORTING "
            "columns (timescaledb.py:189-196), gated on platform_opts.enable_compression, for dry-run "
            "preview only. Real execution's compression policy (TimescaleDBAdapter._add_compression_"
            "policy, timescaledb.py:476-521) never sets compress_orderby from any tuning source.",
        ),
        _T.DISTRIBUTION: _none(
            "timescaledb_compression_hardcoded_segmentby",
            "The generator computes real space-partitioning columns and a compress_segmentby setting "
            "from tuned DISTRIBUTION columns (timescaledb.py:131-141, 177-186) for dry-run preview only. "
            "Real execution's compression policy hardcodes compress_segmentby='' (empty, "
            "timescaledb.py:476-521), ignoring any configured distribution columns.",
        ),
        _T.CLUSTERING: _none(
            "timescaledb_compression_hardcoded_segmentby",
            "Merged with DISTRIBUTION into compress_segmentby by the generator (timescaledb.py:177-186); "
            "same real-execution gap -- compress_segmentby is hardcoded empty regardless of tuning.",
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
    },
    "pg-duckdb": {
        _T.PARTITIONING: _none(
            "pg_duckdb_inherited_postgresql_noop",
            "core.tuning.generators.pg_duckdb.PgDuckDBDDLGenerator delegates to "
            "PostgreSQLDDLGenerator.generate_tuning_clauses (pg_duckdb.py:81), which computes a real "
            "PARTITION BY RANGE/LIST/HASH clause from tuned columns (postgresql.py:130-139) for dry-run "
            "preview. benchbox/platforms/pg_duckdb.py's PgDuckDBAdapter defines no create_schema/"
            "apply_table_tunings/generate_tuning_clause override at all -- it inherits PostgreSQLAdapter's "
            'verbatim, and PostgreSQLAdapter.generate_tuning_clause unconditionally returns "" while '
            "apply_table_tunings is a no-op (postgresql.py:894-902) regardless of input, so neither "
            "PostgreSQL nor pg_duckdb renders PARTITION BY at execution time. NOTE: this appears to also "
            "affect this registry's existing 'postgresql' entry, which describes PARTITIONING/CLUSTERING "
            "as rendered via 'adapter_mixin:PostgreSQLAdapter.generate_tuning_clause' -- that claim could "
            "not be corroborated by a repo-wide call-site search either. Auditing/correcting the existing "
            "postgresql entry is out of this TODO's scope (scope_limit is the 9 newly-covered platforms); "
            "flagged here, not fixed.",
        ),
        _T.CLUSTERING: _none(
            "pg_duckdb_inherited_postgresql_noop",
            "PgDuckDBDDLGenerator deliberately filters CLUSTER out of the inherited post_create_statements "
            "(pg_duckdb.py:83-88, DuckDB's vectorized engine bypasses index-based scan ordering) for "
            "dry-run preview, but the point is moot at execution time: see PARTITIONING entry -- "
            "PgDuckDBAdapter never runs any post_create_statements via the inherited no-op "
            "apply_table_tunings/generate_tuning_clause path.",
        ),
        _T.SORTING: _none(
            "pg_duckdb_inherited_postgresql_noop",
            "PostgreSQLDDLGenerator treats SORTING as CLUSTERING -- a shared code path (postgresql.py:"
            "141-159) appends both a CREATE INDEX and a CLUSTER post_create_statement for either tuning "
            "type. PgDuckDBDDLGenerator's inherited generate_tuning_clauses runs that same path before its "
            "filter (pg_duckdb.py:83-88) strips only the CLUSTER statement by prefix match, so a tuned "
            "SORTING config's CREATE INDEX still surfaces in dry-run preview, same as CLUSTERING. Same "
            "execution-time no-op as CLUSTERING/PARTITIONING regardless: PgDuckDBAdapter never runs "
            "post_create_statements via the inherited no-op apply_table_tunings/generate_tuning_clause "
            "path.",
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
    },
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
    platform_key = platform.lower().replace("_", "-")
    return PLATFORM_ALIASES.get(platform_key, platform_key)


def get_capability(platform: str, tuning_type: TuningType) -> TuningCapability | None:
    platform_key = resolve_platform_key(platform)
    entries = PLATFORM_TUNING_CAPABILITIES.get(platform_key)
    if entries is None:
        return None
    return entries.get(tuning_type)


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
