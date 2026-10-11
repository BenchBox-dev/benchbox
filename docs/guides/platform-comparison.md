<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Platform Comparison Guide

```{tags} intermediate, guide, sql-platform
```

This guide covers how to compare benchmark performance across platforms using the unified `benchbox compare` command. Compare SQL platforms, DataFrame platforms, or both.

## Overview

BenchBox provides a unified comparison interface supporting:

- **SQL Platform Comparisons**: DuckDB vs SQLite vs PostgreSQL vs ClickHouse
- **DataFrame Platform Comparisons**: Polars vs Pandas vs DataFusion
- **Mixed Comparisons**: Compare SQL and DataFrame platforms together
- **File-Based Comparisons**: Compare previously saved result files
- **Performance Visualization**: Generate charts and reports automatically

## Same Data, Different Engines

A key BenchBox design principle: benchmark data is generated once as Parquet files and shared across all platforms. When you compare DuckDB, DataFusion, SQLite, and Polars at the same scale factor, every engine reads from the same Parquet dataset. This eliminates data variability and ensures performance differences reflect the engines themselves.

Every command reads the shared Parquet dataset. The comparisons use DuckDB as
the baseline for both alternatives.

```bash
duckdb_result=$(benchbox run --platform duckdb --benchmark tpch --scale 1 --output ./duckdb-run --quiet)
datafusion_result=$(benchbox run --platform datafusion --benchmark tpch --scale 1 --output ./datafusion-run --quiet)
polars_result=$(benchbox run --platform polars-df --benchmark tpch --scale 1 --output ./polars-run --quiet)

benchbox compare "$duckdb_result" "$datafusion_result"
benchbox compare "$duckdb_result" "$polars_result"
```

Data is generated on the first run and cached in `benchmark_runs/<benchmark>/sf<scale>/data/`. Subsequent runs at the same scale factor reuse the cached data automatically.

### External Table Mode (`--table-mode external`)

Use `--table-mode external` when you want engines to query staged files directly instead of materializing into native tables:

The first command uses the default native mode, which materializes data into platform-managed tables. The second uses
external mode, which registers views or external tables over the staged files.

```bash
benchbox run --platform duckdb --benchmark tpch --scale 1 --table-mode native

benchbox run --platform duckdb --benchmark tpch --scale 1 --table-mode external
```

Key rules:
- `native` remains the default.
- `external` is incompatible with `--tuning tuned`.
- Cloud platforms still upload/stage data in object storage during load; they skip COPY/CTAS materialization in external mode.

Current external-mode mechanisms:

| Platform | External Mechanism |
|----------|--------------------|
| DuckDB / MotherDuck | `CREATE VIEW` over `read_parquet(...)` / `read_csv(...)` / `delta_scan(...)` |
| DataFusion / Polars | Direct file scan path (`create_external_tables` delegates to existing scan loader) |
| Athena | External table registration over S3 Parquet (CTAS bypass) |
| Snowflake | External stage + `CREATE EXTERNAL TABLE` |
| BigQuery | `CREATE EXTERNAL TABLE ... OPTIONS(uris=[...])` over GCS |
| Redshift | Spectrum external schema + external table over S3 |
| Databricks | `CREATE TABLE ... USING PARQUET LOCATION ...` |
| ClickHouse Cloud | `CREATE VIEW ... AS SELECT * FROM s3()/gcs()` |
| Trino / Presto | `CREATE TABLE ... WITH (external_location, format='PARQUET')` |
| Azure Synapse | PolyBase external data source/file format + `CREATE EXTERNAL TABLE` |

Unsupported: SQLite and PostgreSQL.

## Quick Start

### List Available Platforms

```bash
benchbox compare --list-platforms
```

Output shows installed SQL and DataFrame platforms:

```
SQL Platforms:
  Installed:
    duckdb           (embedded, analytical)
    sqlite           (embedded, transactional)
    postgresql       (server, transactional)
    clickhouse-local (embedded, analytical)
    clickhouse-server (server, analytical)

  Cloud (requires configuration):
    snowflake        (cloud, analytical)
    bigquery         (cloud, analytical)
    databricks       (cloud, analytical)

DataFrame Platforms:
  Installed:
    polars-df        (expression, single_node) [lazy, streaming]
    pandas-df        (pandas, single_node) [standard]
    datafusion-df    (expression, single_node) [lazy]

  Not installed:
    pyspark-df       (expression, distributed)
    cudf-df          (pandas, gpu_accelerated)
```

### Compare SQL Platforms

This compares DuckDB with SQLite on TPC-H at scale factor 0.01.

```bash
benchbox compare -p duckdb -p sqlite --scale 0.01
```

### Compare DataFrame Platforms

This compares Polars with Pandas.

```bash
benchbox compare -p polars-df -p pandas-df --scale 0.01
```

### Compare Result Files

```bash
duckdb_result=$(benchbox run --platform duckdb --benchmark tpch --scale 0.1 --output ./duckdb-run --quiet)
sqlite_result=$(benchbox run --platform sqlite --benchmark tpch --scale 0.1 --output ./sqlite-run --quiet)
benchbox compare "$duckdb_result" "$sqlite_result"
```

### Interactive Mode

```bash
benchbox compare
```

The interactive wizard guides you through:
1. Choosing comparison type (platforms or files)
2. Selecting platforms from an interactive list
3. Configuring benchmark options
4. Running the comparison

## Command Reference

```bash
benchbox compare [OPTIONS] [RESULT_FILES]...
```

### Modes

Mode is automatically detected from arguments:

| Mode | Trigger | Description |
|------|---------|-------------|
| Run Mode | `-p/--platform` | Run benchmarks across platforms then compare |
| File Mode | `RESULT_FILES` | Compare existing result files |
| Interactive | No arguments | Launch interactive wizard |

### Platform Selection

| Option | Description |
|--------|-------------|
| `-p, --platform TEXT` | Platforms to compare (triggers run mode). Repeatable |
| `--type [sql\|dataframe\|auto]` | Platform type (default: auto-detect) |
| `--list-platforms` | Show available platforms and exit |

### Benchmark Configuration (run mode)

| Option | Default | Description |
|--------|---------|-------------|
| `-b, --benchmark` | `tpch` | Benchmark: tpch, tpcds, ssb, clickbench |
| `-s, --scale FLOAT` | `0.01` | Scale factor |
| `-q, --queries TEXT` | all | Comma-separated query IDs (e.g., Q1,Q6,Q10) |
| `--warmup INTEGER` | `1` | Warmup iterations |
| `--iterations INTEGER` | `3` | Benchmark iterations |

### Output Options

| Option | Default | Description |
|--------|---------|-------------|
| `-o, --output PATH` | none | Output directory for results |
| `--format [text\|json\|markdown\|html]` | `text` | Output format |
| `--generate-charts` | false | Generate ASCII visualization charts in output directory |
| `--theme [light\|dark]` | `light` | Chart color theme (with --generate-charts) |
| `--data-dir DIRECTORY` | auto | Directory containing benchmark data |

### Query Plan Options (file mode)

| Option | Default | Description |
|--------|---------|-------------|
| `--include-plans` | false | Include query plan comparison in output |
| `--plan-threshold FLOAT` | `0.0` | Only show plans with similarity below threshold (0.0-1.0) |

## Usage Examples

### SQL Platform Comparison

Compare embedded SQL databases:

```bash
benchbox compare \
  -p duckdb \
  -p sqlite \
  -p datafusion \
  --scale 0.1 \
  --iterations 5
```

Example output:

```
SQL Platform Comparison
Platforms: duckdb, sqlite, datafusion
Benchmark: tpch @ SF 0.1
Iterations: 5

Running benchmarks...

============================================================
RESULTS
============================================================

Fastest: duckdb
Slowest: sqlite

Platform        Geomean (ms)    Total (ms)    Success
------------------------------------------------------------
duckdb               45.23        1025.34       100%
datafusion           67.89        1538.67       100%
sqlite              234.56        5318.90       100%

Query Winners:
  Q1: duckdb
  Q2: datafusion
  Q3: duckdb
  ...
============================================================
```

### DataFrame Platform Comparison

Compare DataFrame libraries:

```bash
benchbox compare \
  -p polars-df \
  -p pandas-df \
  -p datafusion-df \
  --scale 0.1
```

### Mixed SQL and DataFrame Comparison

Compare across platform types:

```bash
benchbox compare \
  -p duckdb \
  -p polars-df \
  --scale 0.1
```

Note: Mixed comparisons use SQL execution on SQL platforms and DataFrame API on DataFrame platforms. Results compare execution times for equivalent queries.

### Cloud Platform Comparison

Compare cloud data warehouses (requires credentials):

```bash
benchbox compare \
  -p snowflake \
  -p bigquery \
  -p redshift \
  --benchmark tpch \
  --scale 1
```

### Generate Reports and Charts

Save results with visualization:

```bash
benchbox compare \
  -p duckdb \
  -p sqlite \
  --scale 1 \
  --output ./comparison_results \
  --format markdown \
  --generate-charts \
  --theme dark
```

Creates:

```
comparison_results/
├── comparison.md
├── comparison.json
└── charts/
    ├── performance_bar.txt
    ├── distribution_box.txt
    └── query_heatmap.txt
```

### Compare Result Files

Compare previously saved benchmark results. The two `run` commands export results from individual runs, and the
`compare` command compares the result files:

```bash
duckdb_result=$(benchbox run --platform duckdb --benchmark tpch --scale 0.1 --output ./duckdb-run --quiet)
sqlite_result=$(benchbox run --platform sqlite --benchmark tpch --scale 0.1 --output ./sqlite-run --quiet)
benchbox compare "$duckdb_result" "$sqlite_result"
```

### Specific Query Subset

Run only specific queries for faster iteration:

```bash
benchbox compare \
  -p duckdb \
  -p sqlite \
  --queries Q1,Q6,Q14,Q17 \
  --iterations 10
```

### Parallel Execution

Run platforms in parallel for faster comparisons:

```bash
benchbox compare \
  -p duckdb \
  -p sqlite \
  -p clickhouse-local \
  --iterations 3
```

### JSON Output for Automation

Export results for programmatic analysis:

```bash
benchbox compare \
  -p duckdb \
  -p sqlite \
  --format json \
  --output ./results
```

The JSON output includes:

```text
{
  "benchmark_suite": "unified_comparison",
  "timestamp": "2026-01-01T00:00:00+00:00",
  "config": {
    "platform_type": "sql",
    "scale_factor": 0.01,
    "benchmark": "tpch",
    "query_ids": null,
    "warmup_iterations": 1,
    "benchmark_iterations": 3
  },
  "results": [
    {
      "platform": "duckdb",
      "platform_type": "sql",
      "query_results": [...],
      "geometric_mean_ms": 45.23,
      "total_time_ms": 1025.34,
      "success_rate": 100.0
    },
    ...
  ],
  "summary": {
    "platforms": ["duckdb", "sqlite"],
    "fastest_platform": "duckdb",
    "slowest_platform": "sqlite",
    "speedup_ratio": 5.18,
    "query_winners": {"Q1": "duckdb", ...}
  }
}
```

## Programmatic API

For custom analysis, use the Python API directly:

```python
from benchbox.core.comparison import (
    UnifiedBenchmarkSuite,
    UnifiedBenchmarkConfig,
    PlatformType,
    run_unified_comparison,
)
```

### Quick Comparison

```python
from benchbox.core.comparison import run_unified_comparison

results = run_unified_comparison(
    platforms=["duckdb", "sqlite"],
    scale_factor=0.01,
)

for result in results:
    print(f"{result.platform}: {result.geometric_mean_ms:.2f}ms")
```

### Full Suite with Configuration

```python
from benchbox.core.comparison import (
    UnifiedBenchmarkSuite,
    UnifiedBenchmarkConfig,
    PlatformType,
)

config = UnifiedBenchmarkConfig(
    platform_type=PlatformType.SQL,
    scale_factor=0.1,
    benchmark="tpch",
    query_ids=["Q1", "Q6", "Q10"],
    warmup_iterations=2,
    benchmark_iterations=5,
)

suite = UnifiedBenchmarkSuite(config=config)

results = suite.run_comparison(platforms=["duckdb", "sqlite"])

summary = suite.get_summary(results)
print(f"Fastest: {summary.fastest_platform}")
print(f"Speedup ratio: {summary.speedup_ratio:.2f}x")
```

### Generate Charts Programmatically

```python
from benchbox.core.visualization import ResultPlotter, export_ascii
from benchbox.core.visualization.ascii.base import ChartOptions

plotter = ResultPlotter.from_sources(["results/duckdb.json", "results/sqlite.json"])

from benchbox.core.visualization.ascii import BarChart
from benchbox.core.visualization.ascii.bar_chart import BarData

bar_data = [BarData(label=r.platform, value=r.total_time_ms or 0) for r in plotter.results]
chart = BarChart(data=bar_data, title="Performance Comparison")
print(chart.render())
```

You can also use the CLI for automatic chart generation: `benchbox visualize results/duckdb.json results/sqlite.json`.

### Platform Type Detection

`detect_platform_type("polars-df")` returns `PlatformType.DATAFRAME`. For the list of three platforms,
`detect_platform_types` returns `PlatformType.SQL` as `detected` (the majority) and `["polars-df"]` as `inconsistent`.

```python
from benchbox.core.comparison import detect_platform_type, detect_platform_types

platform_type = detect_platform_type("polars-df")

detected, inconsistent = detect_platform_types(["duckdb", "sqlite", "polars-df"])
```

## Platform Categories

### SQL Platforms

| Category | Platforms | Use Case |
|----------|-----------|----------|
| **Embedded** | DuckDB, SQLite, DataFusion | Local analysis, testing |
| **Server** | PostgreSQL, ClickHouse | Production workloads |
| **Cloud** | Snowflake, BigQuery, Redshift, Databricks | Enterprise, large scale |

### DataFrame Platforms

| Category | Platforms | Use Case |
|----------|-----------|----------|
| **Single Node** | Polars, Pandas, DataFusion | In-memory, medium datasets |
| **Distributed** | PySpark and Dask | Large datasets, cluster |
| **GPU Accelerated** | cuDF | CUDA GPU acceleration |

## Best Practices

### 1. Start Small

Begin with SF 0.01 to verify everything works:

```bash
benchbox compare -p duckdb -p sqlite --scale 0.01
```

### 2. Use Multiple Iterations

Reduce variance with multiple iterations:

```bash
benchbox compare \
  -p duckdb \
  -p sqlite \
  --iterations 5 \
  --warmup 2
```

### 3. Match Your Production Scale

| Data Size | Recommended SF |
|-----------|----------------|
| < 1 GB | 0.01 - 0.1 |
| 1-10 GB | 0.1 - 1 |
| 10-100 GB | 1 - 10 |
| > 100 GB | 10+ |

### 4. Compare Similar Platforms

For meaningful comparisons:
- Compare platforms with similar deployment models
- Use same hardware/resources
- Run at the same scale factor

### 5. Document Your Results

Use markdown output for documentation:

```bash
benchbox compare \
  -p duckdb \
  -p sqlite \
  --scale 1 \
  --format markdown \
  --output ./docs/benchmark_results
```

## Interpreting Results

### Geometric Mean

The geometric mean summarizes performance across all queries. Lower is better. It's less sensitive to outliers than arithmetic mean.

### Success Rate

Percentage of queries that completed successfully. 100% expected for production-ready platforms.

### Query Winners

Shows which platform was fastest for each query:
- Different platforms excel at different query types
- Aggregation vs joins vs filtering
- Use for workload-specific decisions

### Speedup Ratio

Ratio between fastest and slowest platform:
- `> 1.0`: Faster platform completed in less time
- `~1.0`: Platforms are approximately equal
- Large ratios (5x+) indicate major performance differences

## Troubleshooting

### Data Not Found

```
Error: Data directory not found: benchmark_runs/tpch/sf001/data
```

Generate data first:

```bash
benchbox run --platform duckdb --benchmark tpch --scale 0.01
```

### Platform Not Available

```
Warning: Platform clickhouse not available
```

Install the required extra:

```bash
uv add benchbox --extra clickhouse
```

### Mixed Platform Types

```
Error: Cannot mix SQL and DataFrame platforms. Use --type to specify.
```

Either:
1. Compare only same-type platforms
2. Explicitly set `--type sql` or `--type dataframe`

### Memory Issues at Large Scale

For large scale factors:
- Use streaming-capable platforms (Polars, DuckDB)
- Reduce concurrent platforms compared
- Compare fewer platforms per invocation

## Query Plan Comparison

When comparing result files, you can include query plan analysis to understand *why* performance changed, not just that it changed.

### Include Plan Analysis

The first command compares files with plan analysis. The second shows only plans that changed significantly, meaning
less than 90% similar (`--plan-threshold 0.9`).

```bash
benchbox compare baseline.json current.json --include-plans

benchbox compare baseline.json current.json --include-plans --plan-threshold 0.9
```

### Plan Comparison Output

The plan comparison section shows:

| Column | Description |
|--------|-------------|
| Similarity | Overall plan similarity (0-100%) |
| Type Δ | Number of operator type mismatches |
| Prop Δ | Number of property mismatches |
| Perf Δ | Performance change (positive = slower) |
| Status | Plan status (Identical, Similar, Different, REGRESSION) |

### Plan-Correlated Regressions

The most valuable insight is **plan-correlated regressions**: queries where:
1. The query plan changed (< 100% similarity)
2. Performance degraded > 20%

These are highlighted specially because optimizer changes likely caused the regression.

### Requirements

Plan comparison requires benchmark results captured with `--capture-plans`. The two `run` commands capture plans, and
the `compare` command then compares with plan analysis:

```bash
baseline=$(benchbox run --platform duckdb --benchmark tpch --capture-plans --output ./baseline --quiet)
current=$(benchbox run --platform duckdb --benchmark tpch --capture-plans --output ./current --quiet)

benchbox compare "$baseline" "$current" --include-plans
```

## Related Documentation

- [CLI Reference](../reference/cli/index.md) - Full command reference
- [DataFrame Benchmarking Guide](dataframe-migration.md) - Adopting DataFrame benchmarking
- [Visualization Guide](../visualization/chart-generation-guide.md) - Chart customization
