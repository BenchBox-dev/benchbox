# DataFrame Performance Optimization Guide

```{tags} advanced, guide, dataframe-platform, performance
```

This guide covers performance optimization techniques for BenchBox DataFrame adapters.

## Overview

DataFrame benchmarking performance depends on:
1. **Platform selection** - Different platforms excel at different workloads
2. **Configuration options** - Platform-specific tuning settings
3. **Query patterns** - Writing efficient DataFrame operations
4. **Memory management** - Managing data size and memory pressure

## Platform-Specific Optimizations

### Polars

Polars is the default expression-family adapter and excels at single-node performance.

**Key Optimizations:**
- **Lazy evaluation**: Use `LazyFrame` for query planning optimization
- **Streaming mode**: Enable for large datasets that don't fit in memory
- **Predicate pushdown**: Filters are automatically pushed to scan operations

```python
from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

adapter = PolarsDataFrameAdapter(
    execution_engine="streaming",
    rechunk=True,
)
```

`execution_engine="streaming"` selects the streaming engine for large datasets (`--execution-engine streaming` on the command line). `rechunk=True` rechunks data for a better memory layout.

**Best Practices:**
- Use `ctx.scalar()` instead of `.collect()[0, 0]` for single-value extraction
- Chain operations before calling `.collect()`
- Use `select()` to limit columns early in the query

### Pandas

Pandas is the reference Pandas-family implementation.

**Key Optimizations:**
- **Copy-on-write (2.0+)**: Reduces memory copies for read-heavy workloads
- **PyArrow backend**: Uses Apache Arrow for better memory efficiency
- **Categorical dtypes**: Reduces memory for low-cardinality string columns

```python
from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter

adapter = PandasDataFrameAdapter(
    copy_on_write=True,
    dtype_backend="pyarrow",
)
```

`copy_on_write=True` enables copy-on-write for Pandas 2.0+. `dtype_backend="pyarrow"` uses the PyArrow backend.

**Best Practices:**
- Enable CoW for read-heavy workloads with many intermediate operations
- Use `ctx.scalar()` for efficient single-value extraction
- Avoid chained indexing (`df['a']['b']`) - use `.loc[row, col]` instead

### PySpark

PySpark excels at distributed processing and large-scale data.

**Key Optimizations:**
- **Adaptive Query Execution (AQE)**: Auto-optimizes joins and aggregations
- **Shuffle partitions**: Tune based on data size
- **Broadcast joins**: Use for small dimension tables

```python
from benchbox.platforms.dataframe.pyspark_df import PySparkDataFrameAdapter

adapter = PySparkDataFrameAdapter(
    master="local[*]",
    driver_memory="8g",
    shuffle_partitions=200,
    enable_aqe=True,
)
```

`master="local[*]"` uses all cores locally. `driver_memory` increases driver memory. `shuffle_partitions` should be tuned for your data size. `enable_aqe=True` enables Adaptive Query Execution.

**Best Practices:**
- Use `ctx.scalar()` instead of `.collect()[0][0]` for single values
- Cache frequently-accessed DataFrames with `.cache()`
- Avoid collecting large datasets to the driver

### DataFusion

DataFusion provides SQL-like optimization with expression-family syntax.

**Key Optimizations:**
- **Parquet pushdown**: Filters and projections pushed to file scan
- **Repartition joins**: Parallel hash joins
- **Batch size tuning**: Adjust for memory/throughput tradeoff

```python
from benchbox.platforms.dataframe.datafusion_df import DataFusionDataFrameAdapter

adapter = DataFusionDataFrameAdapter(
    repartition_joins=True,
    parquet_pushdown=True,
    batch_size=8192,
)
```

`repartition_joins=True` enables parallel hash joins. `parquet_pushdown=True` pushes predicates into the Parquet scan. `batch_size` sets the row batch size.

## Common Optimization Patterns

### Scalar Extraction

When extracting a single value (e.g., for use in a subsequent filter), use the
optimized `scalar()` method. The first line is the inefficient form, and the second is the optimized form:

```python
total = df.select(col("value").sum()).collect()[0, 0]

total = ctx.scalar(df.select(col("value").sum()))
```

This uses platform-native methods for efficient scalar extraction:
- Polars: `.item()`
- PySpark: `.first()[0]`
- DataFusion: PyArrow column access
- Pandas: `.iloc[0, 0]`

### Filter Push-down

Write queries that allow predicate push-down to file scans. The first example filters early, before the join (good). The second filters after the expensive operation (avoid):

```python
filtered = lineitem.filter(col("l_shipdate") >= lit(start_date))
result = filtered.join(orders, ...)

result = lineitem.join(orders, ...).filter(col("l_shipdate") >= lit(start_date))
```

### Column Pruning

Select only needed columns early in the query. The first example selects columns early (good). The second carries unnecessary columns through the joins (avoid):

```python
subset = lineitem.select("l_orderkey", "l_quantity", "l_extendedprice")
result = subset.join(orders.select("o_orderkey", "o_orderdate"), ...)

result = lineitem.join(orders, ...).select("l_orderkey", "l_quantity", ...)
```

## Memory Management

### Scale Factor Guidelines

| Scale Factor | Memory Required | Recommended Platform |
|-------------|-----------------|---------------------|
| SF 0.01     | <1 GB           | Any                 |
| SF 0.1      | <4 GB           | Any                 |
| SF 1        | 4-8 GB          | Polars, Pandas      |
| SF 10       | 32-64 GB        | Polars (streaming)  |
| SF 100+     | 256+ GB         | PySpark, Dask       |

### Memory-Exceeding Dataset Strategies

For datasets larger than available memory:

1. **Polars streaming**: `execution_engine="streaming"` processes data in chunks
2. **Dask**: Automatically partitions data across workers
3. **PySpark**: Distributed processing with memory spill to disk

## Profiling Queries

BenchBox provides built-in profiling for DataFrame queries. The profile gives a timing breakdown, and a query plan on lazy platforms only:

```python
result, profile = adapter.execute_query_profiled(ctx, query)

print(f"Planning time: {profile.planning_time_ms}ms")
print(f"Execution time: {profile.execution_time_ms}ms")
print(f"Peak memory: {profile.peak_memory_mb}MB")

if profile.query_plan:
    print(profile.query_plan.plan_text)
```

## Tuning Configuration

Use YAML tuning configurations for reproducible optimization. This example is saved as `tuning/performance.yaml`:

```yaml
platform: polars-df
settings:
  streaming:
    enabled: true
  memory:
    batch_size: 16384
  data_types:
    auto_categorize_strings: true
```

```bash
benchbox run --platform polars-df --tuning tuning/performance.yaml ...
```

## Related Documentation

- [DataFrame Platform Documentation](../platforms/dataframe.md)
- [Profiling Guide](dataframe-benchmarks.md)
- [Tuning Configuration](../advanced/performance-tuning.md)
