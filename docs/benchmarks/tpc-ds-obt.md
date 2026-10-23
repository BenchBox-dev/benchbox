<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# TPC-DS-OBT Benchmark

```{tags} advanced, concept, tpc-ds-obt, tpc-ds, experimental
```

> **CLI name:** `tpcds_obt` - use `benchbox run --benchmark tpcds_obt`
>
> **Query templates:** [every TPC-DS-OBT query](queries/tpcds_obt/index.md)

## Overview

The TPC-DS-OBT (One Big Table) benchmark adapts the standard TPC-DS benchmark to run against a single denormalized table instead of the traditional 25-table normalized schema. This experimental benchmark tests how databases handle wide tables with hundreds of columns, a pattern increasingly common in modern data warehouses and lakehouse architectures.

The benchmark is ideal for evaluating column pruning efficiency, wide table scan performance, and storage format effectiveness (Parquet, Delta Lake, Iceberg) on denormalized schemas.

## Key Features

- **Single wide table** - All TPC-DS data flattened into one denormalized table
- **90 of 99 queries** - TPC-DS queries rewritten for flat schema (9 queries are not OBT-convertible; see below)
- **No joins required** - Tests pure scan and aggregation performance
- **Column pruning focus** - Evaluates optimizer column projection efficiency
- **Modern lakehouse pattern** - Simulates real-world denormalized data models
- **Storage format comparison** - Ideal for Parquet vs Delta vs Iceberg testing

## Use Cases

### When to Use TPC-DS-OBT

- **Lakehouse performance testing** - Evaluate denormalized table performance
- **Column pruning benchmarks** - Test how efficiently engines skip unused columns
- **Wide table handling** - Stress test databases with a 715-column table
- **Storage format comparison** - Compare Parquet, Delta Lake, Iceberg on wide tables
- **Scan-heavy workloads** - Benchmark pure analytical scan performance without join overhead

### When to Use Standard TPC-DS

- **Join performance testing** - Evaluating multi-table join strategies
- **Normalized schema workloads** - Traditional data warehouse patterns
- **TPC compliance** - Official TPC-DS compliance requires normalized schema

## Data Model

### One Big Table Schema

The OBT schema denormalizes all 25 TPC-DS tables into a single wide table:

| Aspect | Value |
|--------|-------|
| **Tables** | 1 (denormalized) |
| **Columns** | 715 at `dimension_mode=full` |
| **Source Tables** | All 25 TPC-DS tables flattened |
| **Primary Grain** | store_sales fact table |

### Column Groups

The denormalized table contains columns from all TPC-DS dimensions. The [column lineage](tpc-ds-obt-lineage.md) page lists every fact source and dimension role:

| Source Table | Columns Added | Prefix |
|--------------|---------------|--------|
| store_sales | ~23 | `ss_` |
| customer | ~18 | `c_` |
| customer_address | ~13 | `ca_` |
| customer_demographics | ~9 | `cd_` |
| date_dim | ~28 | `d_` |
| item | ~22 | `i_` |
| store | ~29 | `s_` |
| promotion | ~19 | `p_` |
| household_demographics | ~5 | `hd_` |
| time_dim | ~10 | `t_` |
| ... | ... | ... |

### Scale Factors

| Scale Factor | Approximate Rows | Approximate Size |
|--------------|------------------|------------------|
| 1 | ~2.8 million | ~2 GB |
| 10 | ~28 million | ~20 GB |
| 100 | ~280 million | ~200 GB |
| 1000 | ~2.8 billion | ~2 TB |

## Quick Start

```bash
benchbox run --platform duckdb --benchmark tpcds_obt --scale 1.0

benchbox run --platform duckdb --benchmark tpcds_obt --scale 1.0 --queries Q1,Q3,Q7

benchbox run --platform duckdb --benchmark tpcds --scale 1.0
benchbox run --platform duckdb --benchmark tpcds_obt --scale 1.0
```

The first command runs TPC-DS-OBT on DuckDB and the second runs only queries [Q1](queries/tpcds_obt/q1.md), [Q3](queries/tpcds_obt/q3.md) and [Q7](queries/tpcds_obt/q7.md). The last two commands run standard TPC-DS and then TPC-DS-OBT at the same scale for comparison.

## CLI Options (`--benchmark-option`)

Configure TPC-DS-OBT via `--benchmark-option KEY=VALUE`:

| Option | Default | Description |
|--------|---------|-------------|
| `dimension_mode` | `full` | OBT dimension mode: `full`, `minimal` |
| `channels` | all | Sales channels: `store`, `web`, `catalog` (comma-separated) |
| `output_format` | `parquet` | Output format: `dat`, `parquet` |
| `tpcds_source_dir` | - | Directory containing TPC-DS source data |
| `force_regenerate` | - | Force data regeneration (`true`/`false`) |

Accepts hyphenated aliases (e.g. `dimension-mode`, `output-format`, `tpcds-source-dir`).

```bash
benchbox run --platform duckdb --benchmark tpcds_obt --scale 1 \
  --benchmark-option dimension_mode=minimal \
  --benchmark-option channels=store

benchbox run --platform duckdb --benchmark tpcds_obt --scale 1 \
  --benchmark-option output_format=dat
```

The first command uses minimal dimensions and the store channel only. Parquet is the default output format, so it needs no flag. The second forces the legacy pipe-delimited `.dat` output, which is rarely needed.

### Storage format

Parquet is the default because the OBT is a very wide table (715 columns at `dimension_mode=full`)
with high null density - store rows carry NULL for all `web_site_*`, `web_page_*`, and
`call_center_*` columns, and so on per channel. Columnar storage with null suppression
reduces the SF1 artifact by an order of magnitude vs the 11 GB pipe-delimited `.dat`,
and makes the DataFrame platforms (DataFusion, Polars, Pandas) viable for SF1+.

Use `--benchmark-option output_format=dat` when you need to reproduce results from runs
that pre-date this default change, or when feeding a tool that can only read pipe-delimited
text. Existing `.dat` files on disk are not deleted automatically; BenchBox will regenerate
as parquet and log the path of the stale `.dat` so you can reclaim disk space manually.

## Query Adaptations

TPC-DS-OBT provides 90 of the standard 99 TPC-DS queries rewritten to run against the flat table and return the
TPC-DS result. 9 queries are excluded because they cannot be expressed against a single denormalized fact; the
catalog records each one with its reason, and requesting one raises `QuerySkippedError`:

| Category | Queries | Reason for exclusion |
|----------|---------|----------------------|
| Inventory fact | Q21, Q22, Q37, Q39, Q72, Q82 | Use the inventory fact table, a separate fact domain not included in the OBT denormalization |
| Dimension rows with no sales | Q8, Q54, Q84 | Need customers, zip codes or stores that have no sales rows, and the OBT only carries dimension rows reachable from a sale line |

The OBT queries are a hand-owned catalog, `benchbox/core/tpcds_obt/catalog/queries.yaml`. Each entry records the
SHA-256 of the TPC-DS template it derives from, and tests fail when a template or its parameters change.

### Example: Query 1

**Standard TPC-DS [Q1](queries/tpcds/q1.md)** (with joins):
```sql
SELECT c_customer_id, c_first_name, c_last_name, ...
FROM customer, store_sales, date_dim, store
WHERE c_customer_sk = ss_customer_sk
  AND ss_sold_date_sk = d_date_sk
  AND ss_store_sk = s_store_sk
  ...
```

**TPC-DS-OBT [Q1](queries/tpcds_obt/q1.md)** (flat table):
```sql
SELECT c_customer_id, c_first_name, c_last_name, ...
FROM tpcds_obt
WHERE d_year = 2000
  AND s_state = 'TN'
  ...
```

### Result Equivalence

`tests/integration/test_tpcds_obt_equivalence.py` runs every served OBT query and its TPC-DS counterpart on DuckDB over the same generated data, with identical dsqgen parameters, and compares the result multisets (integers and decimals exactly, floating-point values within a relative tolerance of 1e-9, NULL-aware, row order ignored). Where the default parameter draw makes the TPC-DS query return no rows, the test searches seeds 1 to 12 for one that does. Queries whose results differ, and queries with no rows to compare at any seed, are recorded per scale factor with a cause class in the test module. A listed query that starts matching fails the test, as does an unlisted query that diverges, so the lists can only shrink on purpose.

Run it with `make tpcds-obt-equivalence` (SF0.01 by default) or `make tpcds-obt-equivalence TPCDS_OBT_EQUIVALENCE_SF=1`. The PR tier runs SF0.01 and the nightly `t3:tpcds-obt` domain runs SF1.

## Performance Considerations

### Advantages of OBT

- **No join overhead** - Eliminates multi-table join costs
- **Simplified query plans** - Single table scan with filters
- **Columnar format efficiency** - Modern formats excel at column pruning
- **Predictable performance** - Less optimizer variability

### Challenges of OBT

- **Storage overhead** - Denormalization increases data redundancy
- **Column count** - Wide tables stress metadata handling
- **Update complexity** - Changes require full table rewrites
- **Memory pressure** - Wide rows can stress memory buffers

## Platform Support

| Platform | Status | Notes |
|----------|--------|-------|
| DuckDB | ✅ Full | Excellent wide table handling |
| ClickHouse | ✅ Full | Strong columnar performance |
| Databricks | ✅ Full | Native Delta Lake support |
| Snowflake | ✅ Full | Automatic micro-partitioning |
| BigQuery | ✅ Full | Columnar storage optimized |
| Polars | ✅ Full | Efficient Arrow-based scans |
| PostgreSQL | ⚠️ Limited | Row-store less efficient for wide tables |

## Comparison with Related Benchmarks

| Benchmark | Schema | Tables | Focus |
|-----------|--------|--------|-------|
| **TPC-DS** | Normalized | 25 | Join performance, complex queries |
| **TPC-DS-OBT** | Denormalized | 1 | Wide table scans, column pruning |
| **TPC-H** | Normalized | 8 | Simpler decision support |
| **ClickBench** | Single table | 1 | Real-world analytics patterns |

## See Also

- [TPC-DS Benchmark](tpc-ds.md) - Standard normalized TPC-DS
- [ClickBench](clickbench.md) - Another single-table analytics benchmark
- [Data Vault](datavault.md) - Alternative schema modeling approach
- [Platform Comparison](../platforms/comparison-matrix.md) - Platform capabilities
