<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# DataFrame Performance Benchmarks

```{tags} intermediate, guide, dataframe-platform, performance
```

This guide covers DataFrame performance profiling, optimization, and benchmarking across platforms.

## Overview

BenchBox provides tools for:
- Profiling DataFrame query execution
- Comparing DataFrame vs SQL performance
- Capturing and analyzing query plans
- Measuring lazy evaluation overhead

## Profiling Queries

### Basic Profiling

```python
from benchbox.core.dataframe.profiling import DataFrameProfiler

profiler = DataFrameProfiler(platform="polars")

with profiler.profile_query("q1") as ctx:
    result = df.filter(col("l_shipdate") <= lit(cutoff)).collect()
    ctx.set_rows(len(result))

profile = ctx.get_profile()
print(f"Query: {profile.query_id}")
print(f"Execution time: {profile.execution_time_ms:.2f}ms")
print(f"Rows processed: {profile.rows_processed}")
```

### Lazy Evaluation Profiling

For platforms with lazy evaluation (Polars, PySpark, DataFusion):

```python
with profiler.profile_query("q1") as ctx:
    ctx.start_planning()
    lazy_result = df.filter(...).group_by(...).agg(...)
    ctx.end_planning()

    ctx.start_collect()
    result = lazy_result.collect()
    ctx.end_collect()

    ctx.set_rows(len(result))

profile = ctx.get_profile()
print(f"Planning: {profile.planning_time_ms:.2f}ms")
print(f"Collect: {profile.collect_time_ms:.2f}ms")
print(f"Lazy overhead: {profile.lazy_overhead_percent:.1f}%")
```

### Aggregate Statistics

```python
for query_id in ["q1", "q2", "q3"]:
    with profiler.profile_query(query_id) as ctx:
        execute_query(query_id)

stats = profiler.get_statistics()
print(f"Queries: {stats['query_count']}")
print(f"Total time: {stats['total_execution_time_ms']:.0f}ms")
print(f"Average time: {stats['avg_execution_time_ms']:.2f}ms")
print(f"Lazy evaluation queries: {stats['lazy_evaluation_queries']}")
print(f"Avg lazy overhead: {stats['avg_lazy_overhead_percent']:.1f}%")
```

## Query Plan Capture

### Polars

Capture the plan before calling `collect()`:

```python
from benchbox.core.dataframe.profiling import capture_polars_plan

lazy_df = (
    df.lazy()
    .filter(col("l_shipdate") <= lit(cutoff))
    .group_by("l_returnflag")
    .agg(col("l_quantity").sum())
)

plan = capture_polars_plan(lazy_df)
print(plan.plan_text)
print("Optimization hints:", plan.optimization_hints)

result = lazy_df.collect()
```

### DataFusion

```python
from benchbox.core.dataframe.profiling import capture_datafusion_plan

plan = capture_datafusion_plan(df)
print(plan.plan_text)
```

### PySpark

```python
from benchbox.core.dataframe.profiling import capture_pyspark_plan

plan = capture_pyspark_plan(spark_df)
print(plan.plan_text)
```

### Generic Capture

The generic function selects the capture method for the platform automatically:

```python
from benchbox.core.dataframe.profiling import capture_query_plan

plan = capture_query_plan(df, platform="polars")
if plan:
    print(plan.plan_text)
    for hint in plan.optimization_hints:
        print(f"  - {hint}")
```

## SQL vs DataFrame Comparison

Compare execution modes for platforms supporting both. The `sql_times` dictionary holds SQL execution times in milliseconds from a previous benchmark run:

```python
from benchbox.core.dataframe.profiling import (
    DataFrameProfiler,
    compare_execution_modes,
)

df_profiler = DataFrameProfiler(platform="polars-df")
for query_id in benchmark.query_ids:
    with df_profiler.profile_query(query_id):
        benchmark.execute_dataframe_query(query_id)

sql_times = {"q1": 45.2, "q2": 123.4, "q3": 89.1}

comparisons = compare_execution_modes(
    df_profiler.get_profiles(),
    sql_times
)

for comp in comparisons:
    print(f"{comp.query_id}: DataFrame={comp.dataframe_time_ms:.1f}ms, "
          f"SQL={comp.sql_time_ms:.1f}ms, Winner={comp.winner}")
    for note in comp.notes:
        print(f"  - {note}")
```

## Platform Performance Characteristics

### Polars

| Characteristic | Value | Notes |
|---------------|-------|-------|
| **Execution Model** | Lazy with streaming | Query plan optimized before execution |
| **Memory Efficiency** | High | Columnar, zero-copy operations |
| **Parallelism** | Automatic | Thread pool based on CPU cores |
| **Best For** | Medium-large datasets | Up to ~100GB on single node |

**Optimization Tips:**
- Use `.lazy()` for query optimization
- Enable streaming for very large datasets: `--execution-engine streaming`
- Minimize `.collect()` calls
- Filter early to reduce data volume

### Pandas

| Characteristic | Value | Notes |
|---------------|-------|-------|
| **Execution Model** | Eager | Each operation executes immediately |
| **Memory Efficiency** | Moderate | Higher memory footprint |
| **Parallelism** | Limited | Single-threaded by default |
| **Best For** | Small-medium datasets | Up to ~10GB |

**Optimization Tips:**
- Use PyArrow backend: `--platform-option dtype_backend=pyarrow`
- Minimize DataFrame copies
- Use vectorized operations
- Filter data early

### DataFusion

| Characteristic | Value | Notes |
|---------------|-------|-------|
| **Execution Model** | Lazy | Similar to Polars |
| **Memory Efficiency** | High | Arrow-native |
| **Parallelism** | Automatic | Configurable thread pool |
| **Best For** | Analytical queries | Arrow ecosystem integration |

### PySpark

| Characteristic | Value | Notes |
|---------------|-------|-------|
| **Execution Model** | Lazy | Catalyst optimizer |
| **Memory Efficiency** | Variable | Depends on configuration |
| **Parallelism** | Distributed | Cluster-wide execution |
| **Best For** | Very large datasets | Terabyte+ scale |

## Benchmark Results Analysis

### Running and Comparing Results

```bash
polars_result=$(benchbox run --platform polars-df --benchmark tpch --scale 1 --output ./polars-run --quiet)
pandas_result=$(benchbox run --platform pandas-df --benchmark tpch --scale 1 --output ./pandas-run --quiet)
duckdb_result=$(benchbox run --platform duckdb-df --benchmark tpch --scale 1 --output ./duckdb-run --quiet)

benchbox compare "$polars_result" "$pandas_result"
benchbox compare "$polars_result" "$duckdb_result"
```

### Programmatic Analysis

```python
from pathlib import Path
import json

def load_results(path: str) -> dict:
    with open(path) as f:
        return json.load(f)

def compare_platforms(result_paths: list[str]) -> dict:
    results = {Path(p).stem: load_results(p) for p in result_paths}

    comparison = {}
    for platform, data in results.items():
        query_times = {
            q["query_id"]: q["execution_time_ms"]
            for q in data.get("query_results", [])
        }
        comparison[platform] = query_times

    return comparison

comparison = compare_platforms([
    "results/polars_sf1.json",
    "results/pandas_sf1.json",
    "results/duckdb_sf1.json"
])

for query_id in comparison["polars_sf1"]:
    times = {p: comparison[p].get(query_id) for p in comparison}
    fastest = min(times, key=lambda x: times[x] or float('inf'))
    print(f"{query_id}: {fastest} ({times[fastest]:.0f}ms)")
```

## Memory Profiling

### Tracking Memory Usage

```python
import tracemalloc

tracemalloc.start()

result = df.filter(...).collect()

current, peak = tracemalloc.get_traced_memory()
tracemalloc.stop()

print(f"Current memory: {current / 1024 / 1024:.1f} MB")
print(f"Peak memory: {peak / 1024 / 1024:.1f} MB")
```

### With Profiler Context

The peak memory from `tracemalloc` is converted to MB before it is recorded:

```python
import tracemalloc

with profiler.profile_query("q1") as ctx:
    tracemalloc.start()

    result = df.filter(...).collect()
    ctx.set_rows(len(result))

    _, peak = tracemalloc.get_traced_memory()
    ctx.set_peak_memory(peak / 1024 / 1024)

    tracemalloc.stop()

profile = ctx.get_profile()
print(f"Peak memory: {profile.peak_memory_mb:.1f} MB")
```

## Best Practices

### 1. Profile Before Optimizing

Always measure before making changes:

```python
baseline_profiles = []
for i in range(3):
    with profiler.profile_query(f"q1_run{i}"):
        execute_query()

stats = profiler.get_statistics()
print(f"Baseline avg: {stats['avg_execution_time_ms']:.2f}ms")
```

### 2. Capture Query Plans

Understand what the optimizer is doing:

```python
plan = capture_query_plan(lazy_df, "polars")
print(plan.plan_text)
```

### 3. Compare Lazy vs Eager

For platforms supporting both. The eager run applies only where eager execution is available:

```python
with profiler.profile_query("q1_lazy") as ctx:
    ctx.start_planning()
    lazy_result = df.lazy().filter(...).group_by(...).agg(...)
    ctx.end_planning()

    ctx.start_collect()
    result = lazy_result.collect()
    ctx.end_collect()

with profiler.profile_query("q1_eager") as ctx:
    result = df.filter(...).group_by(...).agg(...)

lazy_profile = profiler.get_profile("q1_lazy")
eager_profile = profiler.get_profile("q1_eager")

print(f"Lazy: {lazy_profile.execution_time_ms:.2f}ms "
      f"(overhead: {lazy_profile.lazy_overhead_percent:.1f}%)")
print(f"Eager: {eager_profile.execution_time_ms:.2f}ms")
```

### 4. Scale Factor Selection

| Platform | Recommended Max SF | Memory Needed |
|----------|-------------------|---------------|
| Pandas | 1-10 | ~6GB per SF |
| Polars | 10-100 | ~4GB per SF |
| DataFusion | 10-100 | ~4GB per SF |
| PySpark | 100+ | Cluster dependent |

### 5. Monitor Optimization Hints

```python
plan = capture_polars_plan(lazy_df)
if plan.optimization_hints:
    print("Optimization opportunities:")
    for hint in plan.optimization_hints:
        print(f"  - {hint}")
```

## Related Documentation

- [DataFrame Platforms](../platforms/dataframe.md) - Platform overview
- [DataFrame Tuning](../platforms/dataframe.md#dataframe-tuning) - Configuration options
- [Polars Platform](../platforms/polars.md) - Polars-specific details
- [Result Analysis](../tutorials/understanding-results.md) - Analyzing benchmark results
