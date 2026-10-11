# Advanced Performance Optimization Guide

```{tags} advanced, guide, performance
```

Comprehensive guide to optimizing BenchBox benchmarks for maximum performance across different platforms and configurations.

## Overview

This guide covers advanced optimization techniques for achieving optimal benchmark performance. For basic performance monitoring, see the [Performance Monitoring](performance.md) guide.

## Table of Contents

- [Platform-Specific Optimizations](#platform-specific-optimizations)
- [Tuning Configuration](#tuning-configuration)
- [Query Optimization](#query-optimization)
- [Data Generation Optimization](#data-generation-optimization)
- [Cloud Platform Optimization](#cloud-platform-optimization)
- [Resource Management](#resource-management)
- [Performance Profiling](#performance-profiling)

## Platform-Specific Optimizations

### DuckDB Optimizations

DuckDB is optimized for analytical workloads by default, but additional tunings can improve performance:

```python
from benchbox.tpch import TPCH
from benchbox.platforms.duckdb import DuckDBAdapter
from benchbox.core.tuning.interface import UnifiedTuningConfiguration, TuningType

tuning = UnifiedTuningConfiguration()

tuning.enable_platform_optimization(
    TuningType.PARTITIONING,
    table_name="lineitem",
    columns=["l_shipdate"],
)

tuning.enable_platform_optimization(
    TuningType.CLUSTERING,
    table_name="lineitem",
    columns=["l_orderkey"],
)

tuning.enable_platform_optimization(
    TuningType.CLUSTERING,
    table_name="orders",
    columns=["o_orderkey"],
)

benchmark = TPCH(scale_factor=1.0)
adapter = DuckDBAdapter(memory_limit="8GB", thread_limit=8, tuning_config=tuning)
results = adapter.run_benchmark(benchmark)
```

This configuration partitions `lineitem` on the ship date and adds clustering on frequently joined columns.

### ClickHouse Local Optimizations

```python
from benchbox.tpch import TPCH
from benchbox.platforms.clickhouse import ClickHouseAdapter
from benchbox.core.tuning.interface import UnifiedTuningConfiguration, TuningType

tuning = UnifiedTuningConfiguration()

tuning.enable_platform_optimization(
    TuningType.PARTITIONING,
    table_name="lineitem",
    columns=["l_shipdate"],
)

tuning.enable_platform_optimization(
    TuningType.SORTING,
    table_name="lineitem",
    columns=["l_orderkey", "l_partkey"],
)

benchmark = TPCH(scale_factor=1.0)
adapter = ClickHouseAdapter(local_mode=True, tuning_config=tuning)
results = adapter.run_benchmark(benchmark)
```

This configuration partitions `lineitem` by ship date and sorts it by common filter columns.

### Databricks Delta Lake Optimizations

```python
from benchbox.tpch import TPCH
from benchbox.platforms.databricks import DatabricksAdapter
from benchbox.core.tuning.interface import UnifiedTuningConfiguration, TuningType

tuning = UnifiedTuningConfiguration()

tuning.enable_platform_optimization(
    TuningType.Z_ORDERING,
    table_name="lineitem",
    columns=["l_orderkey", "l_partkey", "l_shipdate"]
)

tuning.enable_platform_optimization(
    TuningType.AUTO_OPTIMIZE,
    enabled=True
)

tuning.enable_platform_optimization(
    TuningType.AUTO_COMPACT,
    enabled=True
)

tuning.enable_platform_optimization(
    TuningType.BLOOM_FILTERS,
    table_name="lineitem",
    columns=["l_orderkey"],
    expected_items=1000000,
    false_positive_rate=0.01,
)

benchmark = TPCH(scale_factor=10.0)
adapter = DatabricksAdapter(
    warehouse_id="your_warehouse_id",
    catalog="main",
    schema="benchmarks",
    tuning_config=tuning,
)

results = adapter.run_benchmark(benchmark)
```

Z-ordering gives multi-dimensional clustering. Auto-optimize handles background compaction, and auto-compact merges small files. Bloom filters help on high-cardinality columns.

### Snowflake Clustering Optimizations

```python
from benchbox.tpch import TPCH
from benchbox.platforms.snowflake import SnowflakeAdapter
from benchbox.core.tuning.interface import UnifiedTuningConfiguration, TuningType

tuning = UnifiedTuningConfiguration()

tuning.enable_platform_optimization(
    TuningType.CLUSTERING,
    table_name="lineitem",
    columns=["l_shipdate", "l_orderkey"],
)

tuning.enable_platform_optimization(
    TuningType.CLUSTERING,
    table_name="orders",
    columns=["o_orderdate", "o_custkey"],
)

tuning.enable_platform_optimization(
    TuningType.PARTITIONING,
    table_name="lineitem",
    columns=["l_shipdate"],
)

benchmark = TPCH(scale_factor=100.0)
adapter = SnowflakeAdapter(
    warehouse="LARGE_WH",
    database="BENCHMARKS",
    schema="TPCH",
    tuning_config=tuning,
)

results = adapter.run_benchmark(benchmark)
```

Clustering keys cover frequently filtered columns, large tables are partitioned, and `LARGE_WH` is a larger warehouse.

### BigQuery Optimizations

```python
from benchbox.tpch import TPCH
from benchbox.platforms.bigquery import BigQueryAdapter
from benchbox.core.tuning.interface import UnifiedTuningConfiguration, TuningType

tuning = UnifiedTuningConfiguration()

tuning.enable_platform_optimization(
    TuningType.PARTITIONING,
    table_name="lineitem",
    columns=["l_shipdate"],
)

tuning.enable_platform_optimization(
    TuningType.CLUSTERING,
    table_name="lineitem",
    columns=["l_orderkey", "l_partkey", "l_suppkey", "l_linenumber"],
)

benchmark = TPCH(scale_factor=1000.0)
adapter = BigQueryAdapter(
    project_id="your_project",
    dataset_id="benchmarks",
    location="US",
    tuning_config=tuning,
)

results = adapter.run_benchmark(benchmark)
```

This uses native BigQuery partitioning on a date column and clustering for multi-column optimization.

## Tuning Configuration

### Comprehensive Tuning Strategy

```python
from benchbox.tpcds import TPCDS
from benchbox.platforms.duckdb import DuckDBAdapter
from benchbox.core.tuning.interface import UnifiedTuningConfiguration, TuningType

def create_comprehensive_tuning(benchmark_name: str, scale_factor: float):
    tuning = UnifiedTuningConfiguration()

    if benchmark_name == "tpcds":
        fact_tables = ["store_sales", "web_sales", "catalog_sales"]

        for table in fact_tables:
            date_column = {
                "store_sales": "ss_sold_date_sk",
                "web_sales": "ws_sold_date_sk",
                "catalog_sales": "cs_sold_date_sk",
            }[table]
            tuning.enable_platform_optimization(
                TuningType.PARTITIONING,
                table_name=table,
                columns=[date_column],
            )

            key_columns = {
                "store_sales": ["ss_item_sk", "ss_ticket_number"],
                "web_sales": ["ws_item_sk", "ws_order_number"],
                "catalog_sales": ["cs_item_sk", "cs_order_number"],
            }

            tuning.enable_platform_optimization(
                TuningType.CLUSTERING,
                table_name=table,
                columns=key_columns[table],
            )

        tuning.enable_primary_keys()
        tuning.enable_foreign_keys()

    return tuning

tuning = create_comprehensive_tuning("tpcds", scale_factor=10.0)
benchmark = TPCDS(scale_factor=10.0)
adapter = DuckDBAdapter(memory_limit="16GB", thread_limit=16, tuning_config=tuning)
results = adapter.run_benchmark(benchmark)
```

For TPC-DS, the function partitions each fact table by date, clusters it by primary key, and enables primary-key and foreign-key constraints.

### Constraint-Based Optimization

```python
from benchbox.tpch import TPCH
from benchbox.platforms.duckdb import DuckDBAdapter
from benchbox.core.tuning.interface import UnifiedTuningConfiguration

tuning = UnifiedTuningConfiguration()

tuning.enable_primary_keys()
tuning.enable_foreign_keys()

benchmark = TPCH(scale_factor=1.0)
adapter = DuckDBAdapter(tuning_config=tuning)
results = adapter.run_benchmark(benchmark)
```

Primary keys are defined for referential integrity. Foreign keys enable join optimizations.

## Query Optimization

### Query Subset Selection

Run only performance-critical queries:

```python
from benchbox.tpch import TPCH
from benchbox.platforms.duckdb import DuckDBAdapter

slow_queries = [1, 6, 12, 17, 21]

benchmark = TPCH(scale_factor=1.0)
adapter = DuckDBAdapter()

benchmark.generate_data()

for query_id in slow_queries:
    query = benchmark.get_query(query_id)

    import time
    start = time.time()
    conn = adapter.create_connection()
    result = conn.execute(query).fetchall()
    elapsed = time.time() - start

    print(f"Query {query_id}: {elapsed:.3f}s ({len(result)} rows)")
```

The `slow_queries` list holds queries identified as slow in a previous run. Data is generated once, then only those queries run.

### Query Caching

Cache frequently used query results:

```python
from benchbox.tpch import TPCH
from benchbox.platforms.duckdb import DuckDBAdapter
import hashlib

class CachedBenchmark:
    def __init__(self, benchmark, adapter, conn):
        self.benchmark = benchmark
        self.adapter = adapter
        self.conn = conn
        self.cache = {}

    def run_query_cached(self, query_id):
        query_sql = self.benchmark.get_query(query_id)

        cache_key = hashlib.md5(query_sql.encode()).hexdigest()

        if cache_key in self.cache:
            print(f"Query {query_id}: Cache hit")
            return self.cache[cache_key]

        print(f"Query {query_id}: Cache miss, executing...")
        result = self.conn.execute(query_sql).fetchall()

        self.cache[cache_key] = result

        return result

benchmark = TPCH(scale_factor=0.01, output_dir="./tpch_data")
adapter = DuckDBAdapter()
benchmark.generate_data()
conn = adapter.create_connection()
conn.execute(benchmark.get_create_tables_sql())
for table, path in benchmark.tables.items():
    conn.execute(f"INSERT INTO {table} SELECT * FROM read_csv('{path}', delim='|', header=false)")

cached = CachedBenchmark(benchmark, adapter, conn)

result1 = cached.run_query_cached(1)

result2 = cached.run_query_cached(1)
```

The first call is a cache miss and executes the query. The second call is a cache hit.

## Data Generation Optimization

### Timing Data Generation

Time generation and measure the output so the environment can be sized:

```python
from pathlib import Path
from benchbox.tpch import TPCH
import time

def generate_with_timing(scale_factor: float, output_dir: str = "./tpch_data"):
    start_time = time.time()

    benchmark = TPCH(scale_factor=scale_factor, output_dir=output_dir)

    benchmark.generate_data()

    generation_time = time.time() - start_time

    total_size = sum(Path(p).stat().st_size for p in benchmark.tables.values()) / 1024 / 1024

    print(f"\nData generation completed:")
    print(f"  Time: {generation_time:.2f}s")
    print(f"  Tables: {len(benchmark.tables)}")
    print(f"  Total size: {total_size:.1f} MB")

generate_with_timing(scale_factor=1.0)
```

The TPC tools parallelize generation internally for most benchmarks. Time the run and measure the output size to size the environment.

### Data Reuse Strategy

Reuse generated data across runs:

```python
from benchbox.tpch import TPCH
from pathlib import Path

def get_or_generate_data(benchmark_name: str, scale_factor: float, cache_dir: str = "data_cache"):
    cache_path = Path(cache_dir) / benchmark_name / f"sf{scale_factor}"

    if cache_path.exists() and list(cache_path.glob("*.tbl")):
        print(f"Using cached data from {cache_path}")
        return cache_path

    print(f"Generating new data to {cache_path}")
    cache_path.mkdir(parents=True, exist_ok=True)

    benchmark = TPCH(scale_factor=scale_factor, output_dir=str(cache_path))
    benchmark.generate_data()

    return cache_path

data_dir = get_or_generate_data("tpch", scale_factor=1.0)

benchmark = TPCH(scale_factor=1.0, output_dir=str(data_dir))
```

## Cloud Platform Optimization

### S3 Data Staging

Optimize data loading from S3:

```python
from benchbox.tpch import TPCH
from benchbox.platforms.duckdb import DuckDBAdapter

benchmark = TPCH(
    scale_factor=10.0,
    output_dir="s3://my-benchbox-bucket/tpch/sf10"
)

benchmark.generate_data()

adapter = DuckDBAdapter()
conn = adapter.create_connection()

adapter.create_schema(benchmark, conn)
adapter.load_data(benchmark, conn, "s3://my-benchbox-bucket/tpch/sf10")

results = adapter.run_benchmark(benchmark)
```

Data is generated directly to S3 once and reused many times. DuckDB reads from S3 natively, so `load_data` pulls from the bucket without a local copy.

### Regional Optimization

Use cloud storage in the same region as compute:

```python
from benchbox.tpch import TPCH
from benchbox.platforms.databricks import DatabricksAdapter

benchmark = TPCH(
    scale_factor=100.0,
    output_dir="dbfs:/Volumes/main/benchmarks/tpch_sf100"
)

benchmark.generate_data()

adapter = DatabricksAdapter(
    warehouse_id="your_warehouse_id",
    catalog="main",
    schema="benchmarks"
)

results = adapter.run_benchmark(benchmark)
```

The Unity Catalog volume and the SQL warehouse should be in the same region as the workspace, so data is generated to regional storage.

## Resource Management

### Memory Management

Optimize memory usage for large benchmarks:

```python
import gc
from benchbox.tpcds import TPCDS
from benchbox.platforms.duckdb import DuckDBAdapter

def run_memory_optimized_benchmark(scale_factor: float):
    adapter = DuckDBAdapter(
        memory_limit="8GB",
        thread_limit=4
    )

    benchmark = TPCDS(scale_factor=scale_factor)

    print("Generating data...")
    benchmark.generate_data()

    gc.collect()

    print("Running benchmark...")
    results = adapter.run_benchmark(benchmark)

    gc.collect()

    return results

results = run_memory_optimized_benchmark(scale_factor=10.0)
```

The explicit `memory_limit` sets a hard cap, and `thread_limit=4` limits parallelism to control memory use. Garbage collection runs before loading and after the benchmark.

### Disk Space Management

Manage disk space for large benchmarks:

```python
from benchbox.tpch import TPCH
from pathlib import Path
import shutil

def run_with_cleanup(scale_factor: float, temp_dir: str = "/tmp/benchbox"):
    temp_path = Path(temp_dir)
    temp_path.mkdir(parents=True, exist_ok=True)

    try:
        benchmark = TPCH(scale_factor=scale_factor, output_dir=str(temp_path))
        benchmark.generate_data()

        adapter = DuckDBAdapter(database_path=str(temp_path / "benchmark.db"))
        results = adapter.run_benchmark(benchmark)

        return results

    finally:
        print(f"Cleaning up {temp_path}")
        shutil.rmtree(temp_path, ignore_errors=True)

results = run_with_cleanup(scale_factor=1.0)
```

The `finally` block removes the temporary files even if the benchmark fails.

## Performance Profiling

### Detailed Query Profiling

Profile query execution with detailed breakdowns:

```python
from benchbox.tpch import TPCH
from benchbox.platforms.duckdb import DuckDBAdapter
from benchbox.core.results.timing import TimingCollector, TimingAnalyzer

def profile_queries(benchmark, conn, query_ids):
    collector = TimingCollector(enable_detailed_timing=True)

    for query_id in query_ids:
        query_sql = benchmark.get_query(query_id)

        with collector.time_query(query_id, f"Query {query_id}") as timing:
            with collector.time_phase(query_id, "compile"):
                pass

            with collector.time_phase(query_id, "execute"):
                result = conn.execute(query_sql).fetchall()

            collector.record_metric(query_id, "rows_returned", len(result))

    timings = collector.get_completed_timings()
    analyzer = TimingAnalyzer(timings)

    analysis = analyzer.analyze_query_performance()

    print("\nPerformance Analysis:")
    print(f"Queries: {analysis['basic_stats']['count']}")
    print(f"Mean: {analysis['basic_stats']['mean']:.3f}s")
    print(f"Median: {analysis['basic_stats']['median']:.3f}s")
    print(f"P95: {analysis['percentiles'][95]:.3f}s")

    if analysis['timing_phases']:
        print("\nPhase Breakdown:")
        for phase, stats in analysis['timing_phases'].items():
            print(f"  {phase}: {stats['mean']:.3f}s avg")

    return analysis

benchmark = TPCH(scale_factor=0.1, output_dir="./tpch_data")
adapter = DuckDBAdapter()
benchmark.generate_data()
conn = adapter.create_connection()
conn.execute(benchmark.get_create_tables_sql())
for table, path in benchmark.tables.items():
    conn.execute(f"INSERT INTO {table} SELECT * FROM read_csv('{path}', delim='|', header=false)")

analysis = profile_queries(benchmark, conn, [1, 3, 6, 12, 17])
```

The `compile` phase is an empty placeholder because DuckDB compiles on first execute. The `execute` phase holds the real work.

### Performance Regression Testing

Automated regression detection:

```python
from benchbox.tpch import TPCH
from benchbox.platforms.duckdb import DuckDBAdapter
from benchbox.core.results.exporter import ResultExporter
from pathlib import Path

def run_regression_test(baseline_file: Path, threshold: float = 10.0):
    benchmark = TPCH(scale_factor=0.01)
    adapter = DuckDBAdapter()
    current_results = adapter.run_benchmark(benchmark)

    exporter = ResultExporter(output_dir="regression_tests")
    current_file = exporter.export_result(current_results, formats=["json"])["json"]

    comparison = exporter.compare_results(baseline_file, current_file)

    perf_changes = comparison.get("performance_changes", {})
    avg_change = perf_changes.get("average_query_time", {})

    if avg_change.get("change_percent", 0) > threshold:
        print(f"❌ REGRESSION DETECTED: {avg_change['change_percent']:.2f}% slower")
        return False
    else:
        print(f"✅ No regression: {avg_change.get('change_percent', 0):.2f}% change")
        return True

baseline = Path("baselines/tpch_sf001_duckdb.json")
passed = run_regression_test(baseline, threshold=10.0)
exit(0 if passed else 1)
```

The final lines show use in CI/CD: the script exits non-zero when a regression exceeds the threshold.

## Best Practices

### 1. Start Small, Scale Up

Always test with small scale factors first:

```python
benchmark = TPCH(scale_factor=0.01)

benchmark = TPCH(scale_factor=0.1)

benchmark = TPCH(scale_factor=1.0)
```

Use 0.01 for development (fast iteration), 0.1 for testing, and 1.0 for production-scale runs.

### 2. Use Appropriate Tunings

Match tunings to your workload:

```python
from benchbox.core.tuning.interface import UnifiedTuningConfiguration, TuningType

tuning = UnifiedTuningConfiguration()
tuning.enable_platform_optimization(TuningType.CLUSTERING, table_name="fact_table", columns=["date", "id"])

tuning.enable_primary_keys()
```

The clustering tuning suits OLAP-focused workloads (analytical queries). The primary key tuning suits OLTP-focused workloads (point lookups).

### 3. Monitor Resource Usage

Track resource consumption:

```python
import psutil
import os

process = psutil.Process(os.getpid())

print(f"Memory: {process.memory_info().rss / 1024 / 1024:.1f} MB")
print(f"CPU: {process.cpu_percent()}%")
```

### 4. Cache and Reuse

Reuse generated data and connections:

```python
benchmark.generate_data()

conn = adapter.create_connection()

for config in configurations:
    results = run_with_config(conn, benchmark, config)
```

### 5. Profile Before Optimizing

Always profile to identify bottlenecks:

```python
from benchbox.core.results.timing import TimingAnalyzer

analyzer = TimingAnalyzer(timings)
outliers = analyzer.identify_outliers(method="iqr")

print("Optimization targets:")
for outlier in outliers:
    print(f"  Query {outlier.query_id}: {outlier.execution_time:.3f}s")
```

## See Also

- [Performance Monitoring](performance.md) - Basic performance monitoring
- [Tuning Configuration API](../reference/python-api/tuning.md) - Tuning API reference
- [Result Analysis API](../reference/python-api/result-analysis.md) - Analysis utilities
- [CI/CD Integration](ci-cd-integration.md) - Automated performance testing
- [Platform Adapters](../reference/python-api/platforms/) - Platform-specific optimizations
