# Migrating to DataFrame Benchmarking

```{tags} intermediate, guide, dataframe-platform
```

This guide explains how to adopt DataFrame-based benchmarking alongside or instead of SQL-based execution.

## Overview

BenchBox supports two execution paradigms:

| Paradigm | When to Use | Example Platforms |
|----------|-------------|-------------------|
| **SQL Mode** | Database servers, cloud warehouses | Snowflake, BigQuery, DuckDB |
| **DataFrame Mode** | In-memory analytics, data science | Polars, Pandas, PySpark |

Both modes run the same benchmarks (TPC-H, TPC-DS) enabling direct paradigm comparison.

## Quick Migration

### From SQL to DataFrame

**SQL Mode:**
```bash
benchbox run --platform duckdb --benchmark tpch --scale 0.1
```

**DataFrame Mode:**
```bash
benchbox run --platform polars-df --benchmark tpch --scale 0.1
```

The only difference is the platform name with `-df` suffix.

### Platform Mapping

| SQL Platform | DataFrame Equivalent | Notes |
|--------------|---------------------|-------|
| DuckDB | `polars-df` | Similar performance profile |
| DataFusion | `datafusion-df` | Same engine, different API |
| PostgreSQL | `pandas-df` | Reference implementation |
| Spark SQL | `pyspark-df` | Same cluster, different API |

## Family Architecture

DataFrame platforms are organized into two families based on their API style:

### Expression Family (Declarative)

Uses `col()` and `lit()` functions with method chaining:

```python
df.filter(col("amount") > lit(100)).group_by("customer").agg(col("amount").sum())
```

**Platforms:** `polars-df`, `pyspark-df`, `datafusion-df`

### Pandas Family (Imperative)

Uses string column access and boolean indexing:

```python
df[df["amount"] > 100].groupby("customer")["amount"].sum()
```

**Platforms:** `pandas-df`, `dask-df`, `cudf-df`

## Data Compatibility

DataFrame and SQL modes share the same data files:

```
benchmark_runs/datagen/
├── tpch_sf0.1/          # Used by both modes
│   ├── lineitem.csv
│   ├── orders.csv
│   └── ...
```

**No data regeneration required** when switching between modes.

## Tuning Configuration

DataFrame platforms support performance tuning:

```bash
benchbox tuning defaults --platform polars

benchbox run --platform polars-df --benchmark tpch --tuning auto

benchbox run --platform polars-df --benchmark tpch --tuning ./tuning.yaml
```

The first command shows the platform defaults. The second auto-detects optimal settings. The third uses a custom configuration.

Example tuning file (`tuning.yaml`):

```yaml
platform: polars
settings:
  parallelism:
    n_threads: 8
  memory:
    streaming_enabled: true
```

## Programmatic Usage

### SQL Mode
```python
from benchbox import TPCH
from benchbox.platforms.duckdb import DuckDBAdapter

adapter = DuckDBAdapter()
benchmark = TPCH(scale_factor=0.1)
results = benchmark.run(adapter)
```

### DataFrame Mode
```python
from benchbox.platforms.dataframe import PolarsDataFrameAdapter
from benchbox import TPCH

adapter = PolarsDataFrameAdapter()
benchmark = TPCH(scale_factor=0.1)
results = benchmark.run_dataframe(adapter)
```

## Cross-Paradigm Comparison

Run the same benchmark on both paradigms:

```bash
sql_result=$(benchbox run --platform duckdb --benchmark tpch --scale 1 --output ./sql-run --quiet)
df_result=$(benchbox run --platform polars-df --benchmark tpch --scale 1 --output ./df-run --quiet)

benchbox compare "$sql_result" "$df_result"
```

The first command runs SQL execution. The second runs DataFrame execution. The third compares the results.

## Limitations

- **TPC-H and TPC-DS only**: DataFrame mode currently supports these two benchmarks
- **Result validation**: DataFrame results use approximate comparison (float tolerance)
- **No multi-stream**: DataFrame mode runs single-stream power tests only

## Next Steps

- [DataFrame Platform Documentation](../platforms/dataframe.md)
- [Tuning System Reference](../platforms/dataframe.md#tuning-system)
- [Platform Selection Guide](../platforms/platform-selection-guide.md)
