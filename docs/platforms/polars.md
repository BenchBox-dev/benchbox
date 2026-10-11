<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Polars Platform

```{tags} intermediate, guide, polars, dataframe-platform
```

Polars is a high-performance DataFrame library implemented in Rust with Python bindings. It provides both lazy and eager execution modes, with automatic query optimization and parallelization.

BenchBox supports **two modes** for benchmarking Polars:

| Mode | CLI Name | Query Type | Best For |
|------|----------|------------|----------|
| **SQL Mode** | `polars` | SQL strings via SQLContext | SQL-oriented workflows |
| **DataFrame Mode** | `polars-df` | Native expression API | DataFrame-native workflows |

## Features

- **Fast execution** - Rust-based implementation with SIMD optimizations
- **Lazy execution** - Build query plans and optimize before execution
- **Memory efficient** - Zero-copy operations and out-of-core processing
- **SQL support** - Query DataFrames using SQL via `pl.SQLContext`
- **Parallel execution** - Automatic parallelization across CPU cores
- **Native file formats** - Parquet, CSV, JSON, Arrow IPC

## Installation

```bash
pip install polars

pip install "polars[all]"
```

## Configuration

### CLI Options

SQL mode (`polars`) executes queries via Polars `SQLContext`. DataFrame mode (`polars-df`) executes queries via the native expression API:

```bash
benchbox run --platform polars --benchmark tpch --scale 0.1

benchbox run --platform polars-df --benchmark tpch --scale 0.1
```

### Platform Options (SQL Mode: `polars`)

| Option | Default | Description |
|--------|---------|-------------|
| `execution_mode` | lazy | Execution mode (lazy, eager) |
| `n_threads` | auto | Number of threads for parallel execution |
| `driver_version` | (latest) | Pin the polars package version (e.g. `1.36.1`) |
| `driver_auto_install` | false | Auto-install the requested driver version via uv if missing |

The SQL module accepts only the default execution engine. Engine selection and
the deprecated streaming aliases apply only to DataFrame execution.

### Execution Engine (DataFrame Mode: `polars-df`)

Select the engine with the core `--execution-engine` option:

```bash
benchbox run --platform polars-df --benchmark tpch --scale 0.1 --execution-engine streaming
```

| Value | Meaning |
|-------|---------|
| `default` | Each Polars version's own default (`auto` on current releases) |
| `auto` | Polars chooses the engine for each query |
| `in-memory` | Materialize each query in memory |
| `streaming` | Process each query in batches to bound memory |

A version that cannot honour the value fails the run. The `streaming`
platform option is a deprecated alias for `--execution-engine streaming`.

Write-primitive bulk loads and DataFrame maintenance operations honor the
  resolved `execution_engine` when materializing lazy frames. The separate
  DataFrame comparison suite, profiling, validation and dataframe-surface
  equivalence paths remain on Polars' default engine; they do not receive
  `benchbox run`'s execution-engine setting. The comparison suite times its own
  queries, but its separate configuration has no engine-selection input.

### Platform Options (DataFrame Mode: `polars-df`)

| Option | Default | Description |
|--------|---------|-------------|
| `rechunk` | true | Rechunk data for better memory layout |
| `n_rows` | - | Limit rows to read (for testing) |
| `driver_version` | (latest) | Pin the polars package version (e.g. `1.36.1`) |
| `driver_auto_install` | false | Auto-install the requested driver version via uv if missing |

### Testing a Specific Polars Version

```bash
benchbox run --platform polars-df --benchmark tpch \
  --platform-option driver_version=1.36.1 \
  --platform-option driver_auto_install=true
```

See {ref}`driver-version-management` for the full guide, including why `uv run` may
revert a manually-installed version and how to work around it.

## Usage Examples

### Basic Benchmark Run

Run TPC-H using SQL queries (`polars`) or using native expressions (`polars-df`):

```bash
benchbox run --platform polars --benchmark tpch --scale 0.1

benchbox run --platform polars-df --benchmark tpch --scale 0.1
```

### Compare SQL vs DataFrame Performance

Run the same benchmark through both execution paradigms, then compare the results:

```bash
benchbox run --platform polars --benchmark tpch --scale 1 --output ./polars-sql
benchbox run --platform polars-df --benchmark tpch --scale 1 --output ./polars-df

benchbox compare ./polars-sql ./polars-df
```

### Python API

```python
from benchbox import TPCH
from benchbox.platforms.polars_platform import PolarsAdapter

adapter = PolarsAdapter()

benchmark = TPCH(scale_factor=0.1)
benchmark.generate_data()
adapter.load_benchmark(benchmark)
results = adapter.run_benchmark(benchmark)
```

### Direct Polars Usage

```python
import polars as pl
from benchbox import TPCH

tpch = TPCH(scale_factor=0.1)
tpch.generate_data()

lineitem = pl.scan_csv(tpch.tables["lineitem"], separator="|")
orders = pl.scan_csv(tpch.tables["orders"], separator="|")

ctx = pl.SQLContext()
ctx.register("lineitem", lineitem)
ctx.register("orders", orders)

result = ctx.execute("""
    SELECT l_returnflag, SUM(l_quantity) as total_qty
    FROM lineitem
    GROUP BY l_returnflag
    ORDER BY l_returnflag
""").collect()

print(result)
```

## Execution Modes

### Lazy Execution (Default)

Lazy execution builds a query plan and optimizes before execution:

```python
df = (
    pl.scan_csv("lineitem.csv")
    .filter(pl.col("l_quantity") > 10)
    .group_by("l_returnflag")
    .agg(pl.sum("l_quantity"))
    .collect()
)
```

Polars optimizes the entire query plan, and `.collect()` executes the optimized plan.

Optimizations include:
- Predicate pushdown
- Projection pushdown
- Common subexpression elimination
- Join reordering

### Eager Execution

For immediate results without optimization:

```python
adapter = PolarsAdapter(execution_mode="eager")
```

### Streaming Engine

For datasets larger than memory, run DataFrame mode with the streaming engine:

```bash
benchbox run --platform polars-df --benchmark tpch --scale 100 --execution-engine streaming
```

## Performance Characteristics

### Strengths

- **CPU-bound queries**: Excellent vectorized execution
- **In-memory analytics**: Optimized for RAM-resident data
- **File scanning**: Efficient Parquet/CSV reading with pushdown
- **Parallel aggregations**: Automatic multi-core utilization

### Considerations

- **Single-node only**: No distributed execution
- **Memory bound**: Large datasets require streaming mode
- **SQL subset**: Some advanced SQL features not yet supported

## Benchmark Recommendations

### Best For

- Local development and testing
- Small to medium datasets (up to ~100GB)
- Quick iteration and prototyping
- Comparison baseline against DuckDB

### Scale Factor Guidelines

| Scale Factor | Memory Required | Use Case |
|--------------|-----------------|----------|
| 0.01 | ~100 MB | Unit testing, CI/CD |
| 0.1 | ~500 MB | Integration testing |
| 1.0 | ~4 GB | Standard benchmarking |
| 10.0 | ~40 GB (streaming) | Large-scale testing |

**Note:** Execution times vary based on query complexity, hardware, and configuration. Run benchmarks to establish baselines for your environment.

## Comparison with DuckDB

| Aspect | Polars | DuckDB |
|--------|--------|--------|
| Language | Rust/Python | C++/Python |
| Execution | DataFrame API + SQL | SQL-first |
| Persistence | External files | Embedded database |
| Best for | DataFrame workflows | SQL queries |

## Troubleshooting

### Memory Errors

Run DataFrame mode with the streaming engine for large datasets:

```bash
benchbox run --platform polars-df --benchmark tpch --scale 100 --execution-engine streaming
```

### SQL Syntax Errors

Polars SQL supports a subset of SQL. For unsupported features, use the DataFrame API:

```python
result = (
    df.filter(pl.col("column") > 10)
    .group_by("category")
    .agg(pl.sum("value"))
)
```

### Thread Configuration

```python
import polars as pl
pl.Config.set_global_string_cache()
```

## DataFrame Mode Deep Dive <!-- content-ok: cliche -->

The `polars-df` platform executes TPC-H queries using Polars' native expression API instead of SQL. This provides:

- **Full lazy optimization** - Polars query planner optimizes the entire expression chain
- **Type safety** - Expression errors caught at construction time
- **Composability** - Build complex queries from reusable expression components

### Expression API Example

TPC-H Q1 implemented with Polars expressions:

```python
result = (
    lineitem.filter(col("l_shipdate") <= lit(cutoff_date))
    .group_by("l_returnflag", "l_linestatus")
    .agg(
        col("l_quantity").sum().alias("sum_qty"),
        col("l_extendedprice").sum().alias("sum_base_price"),
        (col("l_extendedprice") * (lit(1) - col("l_discount")))
            .sum().alias("sum_disc_price"),
    )
    .sort("l_returnflag", "l_linestatus")
)
```

### When to Use DataFrame Mode

| Use Case | Recommended Mode |
|----------|------------------|
| SQL-based workflows | `polars` (SQL mode) |
| DataFrame-native development | `polars-df` (DataFrame mode) |
| Comparing with Pandas | `polars-df` (same paradigm) |
| Maximum optimization control | `polars-df` (expression API) |

### Streaming Engine for Large Data

Run with the streaming engine for datasets larger than memory:

```bash
benchbox run --platform polars-df --benchmark tpch --scale 100 --execution-engine streaming
```

## Related Documentation

- [DataFrame Platforms Overview](dataframe.md) - DataFrame architecture and concepts
- [Pandas DataFrame](pandas-dataframe.md) - Pandas DataFrame mode
- [DuckDB](../usage/getting-started.md) - SQL-first embedded database
- [DataFusion](datafusion.md) - Rust-based SQL engine
- [CUDF](cudf.md) - GPU-accelerated DataFrames
