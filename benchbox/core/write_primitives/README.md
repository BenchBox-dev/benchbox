# Write Primitives Benchmark

## Overview

The Write Primitives benchmark tests **fundamental write operations** for OLAP databases using the TPC-H schema as foundation. This benchmark provides comprehensive testing of insert, update, delete, bulk load, merge/upsert, DDL, and transaction operations.

**Purpose**: Replace the legacy `merge` benchmark with a comprehensive write operation testing suite that measures:
- Write throughput (rows/second)
- Operation latency and overhead
- Data format efficiency (CSV, Parquet, compressed variants)
- Transaction and isolation level performance
- DDL operation costs
- Data validation and consistency

## Design Philosophy

Following the **primitives benchmark pattern**, this benchmark:
- Uses YAML catalog for operation definitions (`catalog/operations.yaml`)
- Reuses TPC-H data via `get_data_source_benchmark() -> "tpch"`
- Pairs every write operation with validation read queries
- Provides single-sentence descriptions for each operation
- Supports platform-specific SQL variants via SQLGlot
- Measures end-to-end write-read cycle performance

## Benchmark Statistics

- **Total Operations**: 136 (112 baseline + 24 sketch — TRANSACTION ops moved to transaction_primitives)
- **Categories**: 7 (INSERT 12, UPDATE 15, DELETE 14, BULK_LOAD 36, MERGE 23, DDL 12, SKETCH 24)
- **Data Formats**: CSV, Parquet (uncompressed, gzip, zstd, snappy, bzip2)
- **Scale Factors**: Flexible (0.01 to 10.0+)
- **Platform Support**: All platforms via dialect translation + platform-specific overrides
- **Status**: ✅ All operations fully implemented and tested

## Operation Categories

### 1. INSERT Operations (12 operations)

Tests various insert patterns from single row to complex joins:
- Single row INSERT
- Batch INSERT (10, 100, 1000 rows)
- INSERT...SELECT (simple, with JOIN, aggregated, from multiple tables)
- INSERT...UNION
- INSERT with default values
- INSERT...ON CONFLICT (UPSERT)
- INSERT...RETURNING

### 2. UPDATE Operations (15 operations)

Tests selective and bulk updates with various predicates:
- Single row by primary key
- Selective (10%, 50%, 100% of rows)
- With subquery, JOIN, aggregate
- Multi-column updates (5+ columns)
- With CASE expression, computed columns
- String manipulation, date arithmetic
- Conditional updates
- UPDATE...RETURNING

### 3. DELETE Operations (14 operations)

Tests deletion patterns from single row to bulk deletes:
- Single row by primary key
- Selective (10%, 25%, 50%, 75% of rows)
- With subquery, JOIN, aggregation
- With NOT EXISTS (anti-join)
- DELETE...RETURNING
- Cascade simulation
- DELETE vs TRUNCATE comparison
- GDPR deletion by supplier list (1%, 5%)

### 4. BULK_LOAD Operations (36 operations)

Tests bulk loading from files with various formats and compression:
- CSV loads (12): uncompressed, gzip, zstd, bzip2 × (1K, 100K, 1M rows)
- Parquet loads (12): uncompressed, snappy, gzip, zstd × (1K, 100K, 1M rows)
- Special loads (12): column subset, transformations, error handling, parallel, upsert, append vs replace modes, custom delimiters, NULL handling, custom date formats

### 5. MERGE Operations (23 operations)

Tests MERGE/UPSERT patterns including INSERT, UPDATE, and DELETE:
- Simple UPSERT
- UPSERT with DELETE clause (tri-directional)
- Varying overlap scenarios (10%, 50%, 90%, none, all)
- Multi-column join conditions
- Aggregated source queries
- Conditional UPDATE and INSERT
- Multi-column updates
- Computed values, string operations, date arithmetic
- CTE sources
- MERGE...RETURNING
- Error handling (duplicate sources)
- SCD Type 2 dimension maintenance — three ops that exercise history-retaining
  merges via portable UPDATE-then-INSERT, keyed on a business key:
  - `merge_scd_type2_basic` — canonical close-old-plus-insert-new for changed
    and new keys
  - `merge_scd_type2_no_change` — idempotency check: an unchanged batch closes
    no rows and inserts no new versions
  - `merge_scd_type2_new_keys_only` — insert-only path where every staged key is
    brand-new, so no existing rows are closed

### 6. DDL Operations (12 operations)

Tests schema evolution and table management:
- CREATE TABLE (simple, with constraints, with indexes)
- CREATE TABLE AS SELECT (simple, aggregated)
- ALTER TABLE (ADD COLUMN, DROP COLUMN, RENAME COLUMN)
- CREATE INDEX (on empty table, on existing data)
- DROP INDEX
- CREATE VIEW
- DROP TABLE
- TRUNCATE TABLE (small, large datasets)

### 7. SKETCH Operations (24 operations)

Tests the **persist + merge + requery** lifecycle for Apache DataSketches
sketch artifacts — the differentiated half of the modern approximate-
analytics story (vendors compete on millisecond-merge across partitioned
sketch columns, not on one-shot aggregate latency).

**Core lifecycle (8 ops)** — Theta / KLL / Top-K families at default parameters:

- `sketch_ddl_create_persistent_table` — CREATE/DROP overhead for a
  BINARY-column persistence table
- `sketch_insert_theta_per_partition` — build per-partition Theta sketches
- `sketch_insert_kll_per_partition` — build per-partition KLL quantile sketches
- `sketch_insert_topk_per_shard` — build per-shard frequent-items sketches
- ★ `sketch_query_theta_union_merge` — merge thetas across partitions and
  emit an approximate distinct count (cross-reference `aggregation_distinct`
  in read_primitives for the exact baseline)
- ★ `sketch_query_kll_quantiles_merge` — merge KLLs and extract a median
  (cross-reference `statistical_percentiles` in read_primitives)
- ★ `sketch_query_topk_combine` — combine frequent-items sketches across
  shards and count the merged frequent items (cross-reference
  `approx_top_k_lineitem` in read_primitives)
- `sketch_drop_persistent_table` — DROP overhead for a sketch-bearing table

**Accuracy/size parameter sweeps (6 ops)** — same merge lifecycle at smaller
and larger sketch sizes to expose the accuracy-vs-storage trade-off:

- `sketch_query_theta_union_merge_lgk10` / `_lgk14` — Theta at lg_k=10
  (~5KB, ~3.1% RSE) and lg_k=14 (~64KB, ~0.8% RSE)
- `sketch_query_kll_quantiles_merge_k100` / `_k1000` — KLL at k=100
  (~1.5KB, ~1.65% rank error) and k=1000 (~15KB, ~0.59% rank error)
- `sketch_query_topk_combine_lgmm8` / `_lgmm10` — Top-K at lg_max_map_size=8
  (256 buckets, ~600B) and lg_max_map_size=10 (1024 buckets, ~2KB)

**Extended DuckDB-only families (8 ops)** — CPC (distinct) and REQ (quantile)
sketches available through the `datasketches` community extension:

- `sketch_cpc_create_persistent_table` / `sketch_cpc_insert_per_partition` /
  ★ `sketch_cpc_query_union_merge` / `sketch_cpc_drop_persistent_table` —
  CPC distinct-count lifecycle (union-merge + approximate distinct)
- `sketch_req_create_persistent_table` / `sketch_req_insert_per_partition` /
  ★ `sketch_req_query_quantile_merge` / `sketch_req_drop_persistent_table` —
  REQ quantile lifecycle (merge + median extraction)

**PySpark DataFrame headlines (2 ops)** — exercise the persist+merge cycle
through the DataFrame API rather than SQL:

- ★ `sketch_df_hll_persist_merge` — per-group HLL sketches, persist + merge
- ★ `sketch_df_topk_persist_merge` — per-group Top-K, persist + merge
  (Spark 4.1+ only — uses `F.approx_top_k`)

The ★ ops are the **headline tests** that validate the "millisecond merge"
claim. Validation contracts use tolerance-based `expected_value_min/max`
because sketch outputs are non-deterministic across engines and runs.

| Sketch family   | DataSketches binary-portable engines              | Native-but-distinct engines                    | No support       |
|-----------------|---------------------------------------------------|-----------------------------------------------|------------------|
| Theta (distinct)| Databricks, Snowflake, BigQuery (HLL), DuckDB ext | ClickHouse (-State combinators)               | DataFusion       |
| KLL (quantile)  | Databricks, Snowflake, BigQuery, DuckDB ext       | ClickHouse (quantileTDigestState)             | Redshift, DataFusion |
| Top-K (frequent)| Databricks, Snowflake, DuckDB ext                 | ClickHouse (topKState), Redshift (HLL only)   | BigQuery, DataFusion |

Per-engine column-type mapping for sketch storage:

| Engine     | Type alias |
|------------|------------|
| Databricks | `BINARY`   |
| Snowflake  | `BINARY`   |
| BigQuery   | `BYTES`    |
| DuckDB     | `BLOB` (with `datasketches` community extension)|
| ClickHouse | `String` (or `AggregateFunction(...)` natively) |
| Redshift   | `HLLSKETCH` (HLL family only) |

DataSketches binary format is portable across Databricks / Snowflake /
BigQuery / DuckDB-with-extension (all share the C++/Java/WASM core).
ClickHouse uses its own `-State`/`-Merge` combinator serialization;
ClickHouse-native variants are deferred to a follow-up.

Full cross-platform reference:
<https://benchbox.dev/docs/benchmarks/write-primitives-sketch-functions.html>.

## Schema Design

### Base Tables (from TPC-H)
- **REGION**, **NATION**, **CUSTOMER**, **SUPPLIER**, **PART**, **PARTSUPP**, **ORDERS**, **LINEITEM**

### Staging Tables
- **orders_stage** - Copy of ORDERS for UPDATE/DELETE testing
- **lineitem_stage** - Copy of LINEITEM for write testing
- **orders_new** - Source for MERGE testing (50% overlap)
- **orders_summary** - Target for aggregated INSERT...SELECT
- **lineitem_enriched** - Target for joined INSERT...SELECT

### Metadata Tables
- **write_ops_log** - Audit log for all write operations
- **batch_metadata** - Tracks batch operations with file info

## Data Generation

The benchmark reuses TPC-H data through `get_data_source_benchmark() -> "tpch"` and generates:
- Staging tables (10% of ORDERS, 5% of LINEITEM)
- Bulk load files in `write_primitives_auxiliary/` under the benchmark output directory
- CSV files: uncompressed, gzip, zstd (1K, 100K, 1M rows)
- Parquet files: uncompressed, snappy, zstd (1K, 100K, 1M rows)

## Usage Examples

```python
from benchbox.write_primitives import WritePrimitivesBenchmark

bench = WritePrimitivesBenchmark(scale_factor=1.0)
bench.generate_data()

result = bench.execute_operation("insert_single_row", connection)

results = bench.run_category("insert", connection, iterations=3)

results = bench.run_benchmark(connection, iterations=3)
```

The example initializes the benchmark and generates data, runs a single operation, runs the `insert` category, and runs the full benchmark.

## Performance Metrics

Each operation captures:
- **Write Metrics**: duration, rows affected, throughput
- **Validation Metrics**: validation duration, passed/failed status
- **Combined Metrics**: end-to-end duration and throughput

## Platform Compatibility

Operations use SQLGlot dialect translation by default, with `platform_overrides` in
`catalog/operations.yaml` for platform-specific SQL or explicit skips (`null`). When
a platform override is `null`, `_get_effective_write_sql()` returns a skip reason and
the operation is recorded as `SKIPPED` in results.

### DataFusion (v51.0.0) - 62 Skipped Operations

DataFusion is an Arrow-native query engine that operates on **immutable record batches**.
This architecture provides excellent read/scan performance but means row-level mutation
(UPDATE, DELETE) is not implemented - there is no write path for existing data. MERGE
depends on UPDATE/DELETE and is therefore also unsupported.

All 62 skips fall into categories dictated by this architectural constraint. None have
viable alternative SQL syntax within DataFusion's current capability set. (Two further
BULK_LOAD operations carry a `datafusion: null` catalog override but are NOT part of
this skip total - see "BULK_LOAD Edge Cases" below.)

#### UPDATE - 15 operations (queries 13-27)

`NotImplemented("Unsupported logical plan: Dml(Update)")`

Row-level mutation is architecturally impossible on immutable Arrow record batches. A
CTAS-based workaround would measure fundamentally different performance (full table
rewrite vs. in-place update) and is therefore not substituted.

#### DELETE - 14 operations (queries 28-39, 94-95)

`NotImplemented("Unsupported logical plan: Dml(Delete)")`

Same architectural constraint as UPDATE. Includes the 2 GDPR-pattern deletes
(queries 94-95) which also require DELETE.

#### MERGE - 23 operations (queries 76-93, 96-100)

`NotImplemented("Unsupported SQL statement: MERGE INTO...")`

MERGE requires UPDATE and/or DELETE capabilities, neither of which DataFusion supports.
Covers all upsert patterns, conditional update/insert, ETL aggregation, and deduplication.
Includes the 3 SCD Type 2 ops (queries 98-100: `merge_scd_type2_basic`,
`merge_scd_type2_no_change`, `merge_scd_type2_new_keys_only`), which depend on
UPDATE/INSERT against existing rows and carry explicit `datafusion: null` overrides.

#### DDL Mutations - 8 operations

| Operation | Reason |
|-----------|--------|
| `ddl_truncate_table_small` | `NotImplemented("Unsupported SQL statement: TRUNCATE TABLE")` |
| `ddl_create_table_with_constraints` | PK/FK constraints parse but are not enforced; benchmark would test no-op behavior |
| `ddl_create_table_with_index` | `NotImplemented("Unsupported logical plan: CreateIndex")` |
| `ddl_alter_table_add_column` | `NotImplemented("Unsupported SQL statement: ALTER TABLE")` |
| `ddl_alter_table_drop_column` | No ALTER TABLE support |
| `ddl_alter_table_rename_column` | No ALTER TABLE support |
| `ddl_create_index_on_existing` | No indexing support |
| `ddl_drop_index` | No indexing support |

#### INSERT Edge Cases - 2 operations

| Operation | Reason |
|-----------|--------|
| `insert_on_conflict_ignore` | `Plan("Insert-on clause not supported")` - no constraint enforcement makes ON CONFLICT meaningless |
| `insert_returning_clause` | `Plan("Insert-returning clause not supported")` |

#### BULK_LOAD Edge Cases - 2 operations (NOT counted in the 62-skip total)

These carry a `datafusion: null` `platform_overrides` entry in the catalog, but that
entry is never consulted for DataFusion: `DataFusionAdapter.preprocess_operation_sql()`
rewrites every `bulk_load`-category operation and passes the result as `sql_override`,
and `_get_effective_write_sql()` returns `sql_override` before it ever checks
`platform_overrides`. So on an actual DataFusion run these two are ATTEMPTED via the
rewritten SQL, not surfaced as `SKIPPED` - the `datafusion: null` override is
vestigial for this pair. Listed here for the catalog-metadata record, not as part of
the 62-skip count above.

| Operation | Catalog override reason (not applied at runtime) |
|-----------|--------|
| `bulk_load_error_handling_skip_bad_rows` | DataFusion's CSV reader has no `IGNORE_ERRORS` equivalent; all rows must be valid |
| `bulk_load_upsert_mode` | Requires MERGE INTO, which is unsupported |

#### Upstream Tracking

If DataFusion adds DML support in a future version
([apache/datafusion#1885](https://github.com/apache/datafusion/issues/1885)), these
skips should be revisited and the `datafusion: null` overrides removed for any newly
supported operations.

## File Structure

```
benchbox/core/write_primitives/
├── __init__.py                  # Public exports
├── README.md                    # This file
├── benchmark.py                 # WritePrimitivesBenchmark class
├── generator.py                 # Data and file generation
├── operations.py                # WriteOperationManager
├── schema.py                    # Schema definitions
└── catalog/
    ├── __init__.py
    ├── loader.py                # YAML catalog loader
    └── operations.yaml          # 136 operation definitions
```

## License

Copyright 2026 Joe Harris / BenchBox Project

Licensed under the MIT License. See LICENSE file in the project root for details.
