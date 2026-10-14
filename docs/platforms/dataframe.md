<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# DataFrame Platforms

```{tags} beginner, guide, dataframe-platform
```

BenchBox supports native DataFrame benchmarking alongside traditional SQL database benchmarking. DataFrame platforms execute benchmark queries using native DataFrame APIs rather than SQL, enabling direct performance comparison between different DataFrame libraries.

**Supported Benchmarks for DataFrame Execution**:

| Benchmark | DataFrame Support | Query Count |
|-----------|------------------|-------------|
| **TPC-H** | Full | 22 queries |
| **TPC-DS** | Full | 99 queries |
| **Read Primitives** | Full | 149 queries (both families) |
| **Write Primitives** | Full | INSERT, UPDATE, DELETE, MERGE, BULK_LOAD |

These counts describe registered query implementations. SQL/DataFrame parity must be checked for the same data, parameters, scale, and engine. See [TPC-DS DataFrame parity](../development/tpcds-dataframe-parity.md) for binding and coverage requirements.

## Why DataFrame Benchmarking Matters

### The Challenge

Modern data engineering teams face a choice between two paradigms:

1. **SQL-based platforms** (DuckDB, BigQuery, Snowflake) - Query using SQL strings
2. **DataFrame libraries** (Pandas, Polars, PySpark) - Query using native APIs

Traditionally, comparing these approaches required:
- Writing queries twice (SQL + DataFrame)
- Custom benchmarking infrastructure
- Non-standardized measurement methodologies

### The BenchBox Solution

BenchBox provides **unified TPC-H benchmarking** across both paradigms. The first command below runs the queries as SQL on DuckDB. The second runs them through the native Polars DataFrame API:

```bash
benchbox run --platform duckdb --benchmark tpch --scale 1

benchbox run --platform polars-df --benchmark tpch --scale 1
```

Same benchmark. Same scale factor. Same metrics. Different execution paradigm.

### Key Benefits

| Benefit | Description |
|---------|-------------|
| **Apples-to-apples comparison** | Compare SQL vs DataFrame on identical workloads |
| **Library evaluation** | Benchmark Polars vs Pandas vs PySpark on real analytics queries |
| **Migration assessment** | Measure performance impact of paradigm switches |
| **Optimization validation** | Test DataFrame API optimizations against SQL baselines |

## Supported Platforms

BenchBox supports 4 production-ready DataFrame platforms with 3 more in development.

### Expression Family (Production-Ready)

Libraries using expression objects and declarative, lazy-evaluation style:

| Platform | CLI Name | Status | Best For |
|----------|----------|--------|----------|
| **Polars** | `polars-df` | Production-ready | High-performance single-node analytics (recommended) |
| **DataFusion** | `datafusion-df` | Production-ready | Arrow-native workflows, Rust performance |
| **PySpark** | `pyspark-df` | Production-ready | Distributed big data processing |

### Pandas Family (Production-Ready)

Libraries using string-based column access and imperative style:

| Platform | CLI Name | Status | Best For |
|----------|----------|--------|----------|
| **Pandas** | `pandas-df` | Production-ready | Data science prototyping, ecosystem compatibility |

### Coming Soon

Infrastructure is in place for these platforms:

| Platform | CLI Name | Family | Status | Notes |
|----------|----------|--------|--------|-------|
| Dask | `dask-df` | Pandas | Infrastructure ready | Parallel computing |
| cuDF | `cudf-df` | Pandas | Infrastructure ready | NVIDIA GPU acceleration |

## Quick Start

### Installation

Polars DataFrame is recommended and is a core dependency, so the base BenchBox installation already includes it. Install the other DataFrame platforms as extras:

```bash
uv add benchbox --extra pandas

uv add benchbox --extra pyspark

uv add benchbox --extra dataframe-all
```

### Running Your First DataFrame Benchmark

The commands below run TPC-H on Polars, Pandas, and PySpark in turn. The PySpark run uses local mode.

```bash
benchbox run --platform polars-df --benchmark tpch --scale 0.01

benchbox run --platform pandas-df --benchmark tpch --scale 0.01

benchbox run --platform pyspark-df --benchmark tpch --scale 0.01
```

### Comparing SQL vs DataFrame

The first command uses the Polars SQL interface (SQL mode). The second uses the Polars expression API (DataFrame mode).

```bash
benchbox run --platform polars --benchmark tpch --scale 0.1

benchbox run --platform polars-df --benchmark tpch --scale 0.1
```

## Architecture: Family-Based Design

BenchBox uses a **family-based architecture** that enables 95%+ code reuse across DataFrame libraries.

### The Two Families

Python DataFrame libraries cluster into two syntactic families based on API design:

#### Pandas Family

Libraries using string-based column access and imperative style:

```python
df = df[df['l_shipdate'] <= cutoff]
result = df.groupby(['l_returnflag', 'l_linestatus']).agg({
    'l_quantity': 'sum',
    'l_extendedprice': 'sum'
})
```

**Members:** Pandas, cuDF, Dask, Vaex

#### Expression Family

Libraries using expression objects and declarative style:

```python
result = (
    df.filter(col('l_shipdate') <= lit(cutoff))
    .group_by('l_returnflag', 'l_linestatus')
    .agg(
        col('l_quantity').sum().alias('sum_qty'),
        col('l_extendedprice').sum().alias('sum_base_price')
    )
)
```

**Members:** Polars, PySpark, DataFusion

### Why Family-Based Works

Libraries within each family are intentionally API-compatible:

- **cuDF and Dask** - designed around pandas-compatible APIs
- **PySpark, DataFusion** - share expression-based conceptual model with Polars

Result: Write query **once per family**, run on **multiple platforms**.

## Platform Configuration

### Polars DataFrame (`polars-df`)

```bash
benchbox run --platform polars-df --benchmark tpch --scale 1 \
  --execution-engine streaming \
  --platform-option rechunk=true
```

| Engine value | Meaning |
|--------------|---------|
| `default` | Each Polars version's own default |
| `auto` | Polars chooses the engine for each query |
| `in-memory` | Materialize each query in memory |
| `streaming` | Process each query in batches to bound memory |

| Option | Default | Description |
|--------|---------|-------------|
| `rechunk` | `true` | Rechunk data for better memory layout |
| `n_rows` | - | Limit rows to read (for testing) |

### Pandas DataFrame (`pandas-df`)

```bash
benchbox run --platform pandas-df --benchmark tpch --scale 1 \
  --platform-option dtype_backend=pyarrow
```

| Option | Default | Description |
|--------|---------|-------------|
| `dtype_backend` | `numpy_nullable` | Backend for nullable dtypes (`numpy`, `numpy_nullable`, `pyarrow`) |

### PySpark DataFrame (`pyspark-df`)

```bash
benchbox run --platform pyspark-df --benchmark tpch --scale 1 \
  --platform-option driver_memory=8g \
  --platform-option shuffle_partitions=8
```

| Option | Default | Description |
|--------|---------|-------------|
| `master` | `local[*]` | Spark master URL |
| `driver_memory` | `4g` | Memory for driver process |
| `shuffle_partitions` | CPU count | Partitions for shuffle operations |
| `enable_aqe` | `true` | Enable Adaptive Query Execution |

See [PySpark DataFrame Platform](pyspark-dataframe.md) for detailed configuration options.

## Scale Factor Guidelines

DataFrame platforms have memory constraints. Recommended scale factors:

| Platform | Max Recommended SF | Memory Required (SF=1) | Notes |
|----------|-------------------|------------------------|-------|
| Pandas | 10 | ~6 GB | Eager evaluation, high memory |
| Polars | 100 | ~4 GB | Lazy evaluation, efficient |
| Dask | 100+ | Configurable | Disk spillover supported |
| cuDF | 1-10 | GPU VRAM | Limited by GPU memory |
| PySpark | 1000+ | Cluster memory | Distributed processing |

## Supported Benchmarks

### TPC-H (22 queries)

All 22 TPC-H queries have DataFrame implementations for both expression and pandas families.

### Read Primitives (149 queries)

The Read Primitives benchmark provides comprehensive DataFrame support:

- **Expression Family**: 149 query implementations (Polars, PySpark, DataFusion)
- **Pandas Family**: 149 query implementations (Pandas, Dask, cuDF)
- **Categories**: aggregation, filter, groupby, orderby, window, qualify, broadcast, string, and more

```bash
benchbox run --platform polars-df --benchmark read-primitives --scale 0.01
```

See [Read Primitives](../benchmarks/read-primitives.md) for full documentation.

### Write Primitives (DataFrame Write Operations)

Write operations on DataFrame platforms are supported via the `DataFrameWriteOperationsManager`:

| Platform | INSERT | UPDATE | DELETE | MERGE | BULK_LOAD |
|----------|--------|--------|--------|-------|-----------|
| Polars | ✅ | ✅ | ✅ | ✅ | ✅ |
| Pandas | ✅ | ❌ | ❌ | ❌ | ✅ |
| PySpark | ✅ | ✅* | ✅* | ✅* | ✅ |

*PySpark requires Delta Lake table format for row-level operations.

See [Write Primitives](../benchmarks/write-primitives.md) for full documentation.

## Query Implementation Details

BenchBox implements all 22 TPC-H queries for DataFrame platforms:

### Expression Family Example (Q1)

```python
def q1_expression_impl(ctx: DataFrameContext) -> Any:
    lineitem = ctx.get_table("lineitem")
    col, lit = ctx.col, ctx.lit

    cutoff_date = date(1998, 9, 2)

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
    return result
```

### Pandas Family Example (Q1)

```python
def q1_pandas_impl(ctx: DataFrameContext) -> Any:
    lineitem = ctx.get_table("lineitem")

    cutoff = pd.to_datetime("1998-12-01") - pd.Timedelta(days=90)
    filtered = lineitem[lineitem["l_shipdate"] <= cutoff]

    result = (
        filtered
        .groupby(["l_returnflag", "l_linestatus"], as_index=False)
        .agg({
            "l_quantity": ["sum", "mean"],
            "l_extendedprice": ["sum", "mean"],
        })
        .sort_values(["l_returnflag", "l_linestatus"])
    )
    return result
```

## Execution Differences: SQL vs DataFrame

| Aspect | SQL Mode | DataFrame Mode |
|--------|----------|----------------|
| **Query representation** | SQL strings | Native API calls |
| **Optimization** | Database query planner | Library-specific (lazy if available) |
| **Type checking** | Runtime (query execution) | Some compile-time (IDE support) |
| **Composability** | CTEs, subqueries | Method chaining, intermediate variables |
| **Debugging** | EXPLAIN plans | Step-through execution |

## Performance Characteristics

### Polars DataFrame

**Strengths:**
- Lazy evaluation with query optimization
- Excellent memory efficiency
- Parallel execution by default
- Fast file scanning with predicate pushdown

**Best for:**
- Medium to large datasets (up to ~100GB)
- Complex analytical queries
- Memory-constrained environments

### Pandas DataFrame

**Strengths:**
- Familiar API for Python developers
- Extensive ecosystem
- Good for prototyping

**Best for:**
- Smaller datasets (up to ~10GB)
- Quick iteration
- Compatibility with existing codebases

## Available Databases

```bash
benchbox profile

python -c "from benchbox.platforms.dataframe import format_platform_status_table; print(format_platform_status_table())"
```

Example output:
```
DataFrame Platform Status
============================================================
Platform        Family       Available  Version
------------------------------------------------------------
Pandas          pandas       ✓          3.0.5
Polars          expression   ✓          1.15.0
Dask            pandas       ✗          N/A
PySpark         expression   ✗          N/A
DataFusion      expression   ✓          43.0.0
------------------------------------------------------------
Available: 3/6 platforms
```

## DataFrame Tuning

BenchBox provides a comprehensive tuning system for DataFrame platforms that allows you to optimize runtime performance based on your system profile and workload characteristics.

### Quick Start with Tuning

```bash
benchbox run --platform polars-df --benchmark tpch --scale 1 --tuning auto

benchbox run --platform polars-df --benchmark tpch --tuning tuned

benchbox run --platform polars-df --benchmark tpch --tuning ./my_tuning.yaml
```

`--tuning tuned` loads `examples/tunings/dataframe/<platform>_optimized.yaml`
for Polars, pandas and cuDF, relative to the current directory, and records
the run as mode `tuned`. Dask and DataFusion ship no such file, so `tuned` falls
back to the system-profile defaults that `--tuning auto` uses, prints a warning
and a line that name what the fallback applies ("engine runtime defaults"), and
records mode `tuned-fallback`.

### CLI Commands

```bash
benchbox tuning defaults --platform polars

benchbox tuning init --platform polars --output polars_tuning.yaml

benchbox tuning validate polars_tuning.yaml --platform polars

benchbox tuning platforms
```

### Tuning Configuration Options

DataFrame tuning is organized into configuration categories:

#### Parallelism Settings

| Setting | Type | Default | Applicable Platforms | Description |
|---------|------|---------|---------------------|-------------|
| `thread_count` | int | auto | Polars | Number of threads (`POLARS_MAX_THREADS`) |
| `worker_count` | int | auto | Dask | Number of worker processes |
| `threads_per_worker` | int | auto | Dask | Threads per worker process |

#### Memory Settings

| Setting | Type | Default | Applicable Platforms | Description |
|---------|------|---------|---------------------|-------------|
| `memory_limit` | str | None | Dask | Memory limit per worker (e.g., `"4GB"`, `"2GiB"`) |
| `chunk_size` | int | None | Polars, Pandas, Dask | Size of chunks for streaming/batched operations |
| `spill_to_disk` | bool | false | Dask, cuDF | Enable spilling to disk when memory is exhausted |
| `spill_directory` | str | None | Dask | Directory for spill files (None = temp directory) |
| `rechunk_after_filter` | bool | true | Polars | Rechunk data after filter operations for better memory layout |

#### Execution Settings

| Setting | Type | Default | Applicable Platforms | Description |
|---------|------|---------|---------------------|-------------|
| `streaming_mode` | bool | false | Polars | Enable streaming execution for memory efficiency |
| `engine_affinity` | str | None | Polars | Preferred execution engine: `"streaming"` or `"in-memory"`. To let Polars choose the engine for each query, use `--execution-engine auto` instead |
| `lazy_evaluation` | bool | true | Polars, Dask | Enable lazy evaluation where supported |
| `collect_timeout` | int | None | All lazy platforms | Maximum seconds for collect/compute operations |

#### Data Type Settings

| Setting | Type | Default | Applicable Platforms | Description |
|---------|------|---------|---------------------|-------------|
| `dtype_backend` | str | `"numpy_nullable"` | Pandas and Dask | Backend for nullable dtypes: `"numpy"`, `"numpy_nullable"`, or `"pyarrow"` |
| `enable_string_cache` | bool | false | Polars, Pandas | Enable global string caching for categoricals |
| `auto_categorize_strings` | bool | false | Pandas | Auto-convert low-cardinality strings to categoricals |
| `categorical_threshold` | float | 0.5 | Pandas | Unique ratio threshold for auto-categorization (0.0-1.0) |

#### I/O Settings

| Setting | Type | Default | Applicable Platforms | Description |
|---------|------|---------|---------------------|-------------|
| `memory_pool` | str | `"default"` | All | Memory allocator for Arrow: `"default"`, `"jemalloc"`, `"mimalloc"`, `"system"` |
| `memory_map` | bool | false | Pandas and Dask | Use memory-mapped files for reading |
| `pre_buffer` | bool | true | Pandas, Dask | Pre-buffer data during file reads |
| `row_group_size` | int | None | Polars, Pandas, cuDF | Row group size for Parquet writing |

#### Write Settings

| Setting | Type | Default | Applicable Platforms | Description |
|---------|------|---------|---------------------|-------------|
| `sort_by` | list[object\|str] | `[]` | All DataFrame platforms | Sort columns before write for better compression and scan locality |
| `partition_by` | list[object\|str] | `[]` | Dask, PySpark | Hive-style partitioned output directories |
| `row_group_size` | int | None | Polars, Pandas, cuDF | Parquet row group sizing for write path |
| `target_file_size_mb` | int | None | Platform-dependent | Preferred output file size target |
| `repartition_count` | int | None | Dask, PySpark | Number of output files/partitions |
| `compression` | str | `"zstd"` | All DataFrame platforms | Parquet compression codec (`none`, `snappy`, `gzip`, `zstd`, `lz4`, `brotli`) |
| `compression_level` | int | None | Codec-dependent | Compression level for codecs that support levels |
| `dictionary_columns` | list[str] | `[]` | All DataFrame platforms | Force dictionary encoding on selected columns |
| `skip_dictionary_columns` | list[str] | `[]` | All DataFrame platforms | Disable dictionary encoding for selected columns |
| `data_page_version` | `"1.0"`\|`"2.0"` | `null` (PyArrow default `1.0`) | All Parquet write paths | Parquet data page serialization version |

#### GPU Settings (cuDF)

| Setting | Type | Default | Applicable Platforms | Description |
|---------|------|---------|---------------------|-------------|
| `enabled` | bool | false | cuDF | Enable GPU acceleration |
| `device_id` | int | 0 | cuDF | CUDA device ID to use (0-indexed) |
| `spill_to_host` | bool | true | cuDF | Spill GPU memory to host RAM when exhausted |
| `pool_type` | str | `"default"` | cuDF | RMM memory pool type: `"default"`, `"managed"`, `"pool"`, `"cuda"` |

### Example Tuning Configurations

#### Polars - SF10+ Dataset Optimization

```yaml
_metadata:
  version: "1.0"
  platform: polars
  description: "Optimized settings for large TPC-H workloads"

parallelism:
  thread_count: 8

execution:
  streaming_mode: true
  engine_affinity: streaming
  lazy_evaluation: true

memory:
  chunk_size: 100000
  rechunk_after_filter: true

io:
  memory_pool: jemalloc
```

#### Pandas - Memory-Efficient Processing

```yaml
_metadata:
  version: "1.0"
  platform: pandas
  description: "Memory-efficient Pandas configuration"

data_types:
  dtype_backend: pyarrow
  auto_categorize_strings: true
  categorical_threshold: 0.3

io:
  memory_map: true
  pre_buffer: false
```

#### Dask - Distributed Workload

```yaml
_metadata:
  version: "1.0"
  platform: dask
  description: "Distributed Dask configuration"

parallelism:
  worker_count: 4
  threads_per_worker: 2

memory:
  memory_limit: "4GB"
  spill_to_disk: true
  spill_directory: /tmp/dask-spill

execution:
  lazy_evaluation: true
```

#### cuDF - GPU Acceleration

```yaml
_metadata:
  version: "1.0"
  platform: cudf
  description: "GPU-accelerated cuDF configuration"

gpu:
  enabled: true
  device_id: 0
  spill_to_host: true
  pool_type: managed
```

### Smart Defaults

When you use `--tuning auto`, BenchBox detects your system profile and applies appropriate settings:

| System Profile | Memory | Base Memory Settings (Before Platform Overrides) |
|----------------|--------|-----------------------------------------------|
| Very Low | <4GB | `streaming_mode=true`; `chunk_size=50K` |
| Low | 4GB to <8GB | `streaming_mode=true`; `chunk_size=100K` |
| Medium | 8GB to <32GB | `chunk_size=500K` |
| High | 32GB or more | `streaming_mode=false` |
| GPU Available | N/A | GPU enabled with managed pool (cuDF) |

#### System Detection

BenchBox automatically detects:

- **CPU cores**: Used to set thread/worker counts
- **Available RAM**: Determines memory category and chunk sizes
- **GPU presence**: Enables CUDA-based acceleration for cuDF
- **GPU memory**: Sets spill thresholds and pool types

#### Platform-Specific Smart Defaults

**Polars:**
- Smart defaults no longer select an execution engine: `--tuning auto` and
  `--tuning tuned` leave the engine at `default` on every host. Pass
  `--execution-engine streaming` to stream explicitly.
- Low memory: `chunk_size=50000`; high memory: `lazy_evaluation=true`

**Pandas:**
- Low memory: `dtype_backend="numpy_nullable"`, `memory_map=true`
- Medium+ memory: `dtype_backend="pyarrow"` (2-5x faster for aggregations)

**Dask:**
- Workers set to CPU cores / 2
- Threads per worker: 2
- Memory limit per worker: available RAM / workers

**cuDF:**
- Small GPU (<8GB): `pool_type="managed"`, `spill_to_host=true`
- Large GPU (≥8GB): `pool_type="pool"`, `spill_to_host=false`

### Tuning Validation

BenchBox validates your tuning configuration and reports issues at three levels:

| Level | Description | Example |
|-------|-------------|---------|
| **ERROR** | Invalid configuration that will fail | Invalid `engine_affinity` value |
| **WARNING** | Suboptimal or conflicting settings | `streaming_mode=true` with `engine_affinity="in-memory"` |
| **INFO** | Suggestions for improvement | Streaming mode without `chunk_size` set |

Validate your configuration before running. The output lists any issues and suggestions:

```bash
benchbox tuning validate my_config.yaml --platform polars

✓ Configuration valid
⚠ WARNING: streaming_mode enabled without chunk_size - consider setting chunk_size
ℹ INFO: Consider enabling lazy_evaluation for improved efficiency
```

## Troubleshooting

### Platform Not Available

```
ValueError: Unknown DataFrame platform: polars-df
```

**Solution:** Ensure you're using a supported platform name with the `-df` suffix.

### Memory Errors with Pandas

```
MemoryError: Unable to allocate array
```

**Solutions:**
1. Reduce scale factor: `--scale 0.1`
2. Switch to Polars: `--platform polars-df`
3. Use PyArrow backend: `--platform-option dtype_backend=pyarrow`

### Slow Performance with Large Datasets

For scale factors > 10:

1. **Polars:** Run with the streaming engine
   ```bash
   --execution-engine streaming
   ```

2. **Pandas:** Consider switching to Polars or Dask

## API Reference

### DataFrameContext Protocol

The context provides table access and expression helpers. `ctx.col` builds column references and `ctx.lit` builds literal values (the expression builders for the expression family):

```python
from benchbox.core.dataframe import DataFrameContext

df = ctx.get_table("lineitem")

col = ctx.col
lit = ctx.lit
```

### DataFrameQuery Class

Query definition with dual-family implementations:

```python
from benchbox.core.dataframe import DataFrameQuery, QueryCategory

query = DataFrameQuery(
    query_id="Q1",
    query_name="Pricing Summary Report",
    category=QueryCategory.TPCH,
    expression_impl=q1_expression_impl,
    pandas_impl=q1_pandas_impl,
)
```

## Related Documentation

- [DataFrame Optimization Guide](../performance/dataframe-optimization.md) - Performance optimization techniques
- [Polars Platform](polars.md) - Polars SQL mode documentation
- [Platform Selection Guide](platform-selection-guide.md) - Choosing between platforms
- [Getting Started](../usage/getting-started.md) - BenchBox quick start
- [TPC-H Guide](../guides/tpc/tpc-h-official-guide.md) - TPC-H benchmark details
