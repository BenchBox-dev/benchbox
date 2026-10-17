# Tuning Platform Support

This page lists, for each platform in the tuning capability registry, what a
tuning file can change at execution and whether a tuned run can be verified
against the live catalog. It is generated from
`benchbox/core/tuning/capability_registry.py`, the packaged tuning templates,
and each adapter's catalog introspector by
`scripts/generate_tuning_support_matrix.py` (`make tuning-support-matrix`);
a test fails when the page drifts from those sources, so do not edit it by
hand.

- **Shipped templates**: benchmarks with a packaged `*_tuned.yaml` template,
  used by `--tuning tuned`. Platforms without one use the generic fallback.
- **Applied at execution**: tuning types the platform renders when tables are
  created or loaded. Requesting any other type fails validation.
- **Not applied**: tuning types the registry lists but the platform does not
  apply; requesting one fails validation with the registry's reason.
- **Catalog introspector**: whether the adapter reads the catalog after load
  to corroborate the applied-tuning ledger.
- **Can reach `applied_verified`**: yes only when the platform has a catalog
  introspector and applies at least one tuning type. Other platforms report
  at most `applied_unverified`.

Aliases resolve to another platform's entry: `chdb` -> `clickhouse`, `clickhouse-cloud` -> `clickhouse`, `clickhouse-local` -> `clickhouse`, `clickhouse-server` -> `clickhouse`, `delta` -> `databricks`, `starburst` -> `trino`, `synapse` -> `azure-synapse`.

| Platform | Shipped templates | Applied at execution | Not applied | Spark session map | Catalog introspector | Can reach `applied_verified` |
| --- | --- | --- | --- | --- | --- | --- |
| `athena` | none | none | check_constraints, clustering, distribution, foreign_keys, partitioning, primary_keys, sorting, unique_constraints | - | no | no |
| `azure-synapse` | none | foreign_keys, primary_keys | check_constraints, clustering, distribution, partitioning, sorting, unique_constraints | - | no | no |
| `bigquery` | none | foreign_keys, primary_keys | check_constraints, clustering, materialized_views, partitioning, unique_constraints | - | no | no |
| `cedardb` | none | foreign_keys, primary_keys | bloom_filters, check_constraints, clustering, materialized_views, partitioning, unique_constraints | - | no | no |
| `citus` | none | foreign_keys, primary_keys | bloom_filters, check_constraints, clustering, materialized_views, partitioning, unique_constraints | - | no | no |
| `clickhouse` | ssb, tpcds, tpch | clustering, partitioning, primary_keys, sorting | check_constraints, foreign_keys, materialized_views, unique_constraints | - | yes | yes |
| `databend` | none | clustering | check_constraints, distribution, foreign_keys, materialized_views, partitioning, primary_keys, sorting, unique_constraints | - | no | no |
| `databricks` | read_primitives, ssb, tpcds, tpch, tpchavoc | auto_compact, auto_optimize, clustering, foreign_keys, liquid_clustering, primary_keys, z_ordering | bloom_filters, check_constraints, distribution, materialized_views, partitioning, unique_constraints | - | no | no |
| `datafusion` | none | none | check_constraints, foreign_keys, primary_keys, unique_constraints | - | no | no |
| `doris` | none | none | check_constraints, distribution, foreign_keys, materialized_views, partitioning, primary_keys, sorting, unique_constraints | - | no | no |
| `duckdb` | amplab, clickbench, h2odb, joinorder, read_primitives, ssb, tpcds, tpch, tpchavoc | foreign_keys, primary_keys, sorting | check_constraints, partitioning, unique_constraints | - | yes | yes |
| `fabric-warehouse` | none | foreign_keys, primary_keys | check_constraints, partitioning, unique_constraints | - | no | no |
| `firebolt` | none | none | check_constraints, clustering, distribution, foreign_keys, partitioning, primary_keys, sorting, unique_constraints | - | no | no |
| `motherduck` | none | sorting | check_constraints, clustering, distribution, foreign_keys, materialized_views, partitioning, primary_keys, unique_constraints | - | no | no |
| `mysql` | none | foreign_keys, primary_keys | check_constraints, partitioning, unique_constraints | - | no adapter | no |
| `paradedb` | none | foreign_keys, primary_keys | bloom_filters, check_constraints, clustering, materialized_views, partitioning, unique_constraints | - | no | no |
| `pg-duckdb` | none | foreign_keys, primary_keys | check_constraints, clustering, partitioning, sorting, unique_constraints | - | no | no |
| `pg-mooncake` | none | foreign_keys, primary_keys | check_constraints, clustering, partitioning, sorting, unique_constraints | - | no | no |
| `postgresql` | none | foreign_keys, partitioning, primary_keys | bloom_filters, check_constraints, clustering, materialized_views, unique_constraints | - | no | no |
| `presto` | none | foreign_keys | check_constraints, clustering, distribution, partitioning, primary_keys, sorting, unique_constraints | - | no | no |
| `questdb` | none | none | check_constraints, distribution, foreign_keys, partitioning, primary_keys, sorting, unique_constraints | - | no | no |
| `redshift` | none | foreign_keys, primary_keys, sorting (with sorted ingestion) | check_constraints, distribution, materialized_views, partitioning, unique_constraints | - | no | no |
| `singlestore` | none | primary_keys | check_constraints, foreign_keys, partitioning, unique_constraints | - | no | no |
| `snowflake` | tpcds, tpch | clustering, foreign_keys, partitioning, primary_keys | check_constraints, materialized_views, unique_constraints | - | yes | yes |
| `spark` | none | foreign_keys, primary_keys, sorting | check_constraints, partitioning, unique_constraints | applied | no | no |
| `sqlite` | none | foreign_keys, primary_keys | check_constraints, unique_constraints | - | no | no |
| `starrocks` | none | distribution, partitioning, primary_keys, sorting | check_constraints, foreign_keys, unique_constraints | - | no | no |
| `timescaledb` | none | foreign_keys, primary_keys | check_constraints, clustering, distribution, partitioning, sorting, unique_constraints | - | no | no |
| `trino` | none | foreign_keys | check_constraints, clustering, distribution, partitioning, primary_keys, sorting, unique_constraints | - | no | no |
| `velox` | none | none | none | applied | no | no |
