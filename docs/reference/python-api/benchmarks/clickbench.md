# ClickBench Benchmark API

```{tags} reference, python-api, clickbench
```

Python API reference for the ClickBench (ClickHouse Analytics Benchmark).

## Overview

ClickBench is a systematic analytics benchmark designed to test analytical database performance using real-world web analytics data patterns. It uses a single flat table (`hits`, 105 columns) and 43 query patterns representative of real-world analytics workloads.

**Key Features**:

- **43 analytical queries** - Diverse performance patterns
- **Single flat table design** - Emphasizes columnar storage
- **Real-world data patterns** - Based on web analytics
- **Comprehensive column coverage** - 105 columns, various data types
- **Performance-focused** - Precise timing comparisons
- **Cross-system compatibility** - Standard across databases
- **Any positive scale factor** - 1.0 generates 1,000,000 rows; 0.01 generates 10,000

## Quick Start

The example creates the benchmark, generates the data and runs it on a platform.

```python
from benchbox import ClickBench
from benchbox.platforms.duckdb import DuckDBAdapter

benchmark = ClickBench(scale_factor=0.01)

benchmark.generate_data()

adapter = DuckDBAdapter()
results = benchmark.run_with_platform(adapter)

print(f"Completed {results.total_queries} queries in {results.total_execution_time:.2f}s")
```

The DuckDB adapter needs the `duckdb` package. On 0.4.1 this prints `Completed 43 queries in 0.14s`; the time varies by machine.

## API Reference

### ClickBench Class

#### `benchbox.ClickBench`

<span id="benchbox.clickbench.ClickBench"></span>

Creates a ClickBench benchmark that generates synthetic web-analytics rows for one flat table and serves the 43 ClickBench queries.

**Import:** `from benchbox import ClickBench` · **Extras:** none

##### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `scale_factor` | `float` | `1.0` | Data size multiplier. Must be positive. Values of 1 or more must be whole numbers. 1.0 generates 1,000,000 rows. |
| `output_dir` | `str`, `Path` or `None` | `None` | Directory for generated data files. When `None`, the directory is `benchmark_runs/datagen/clickbench_<sf token>` under the current directory (for example `clickbench_sf001` for 0.01), or under `$BENCHBOX_OUTPUT_DIR/datagen` when that variable is set. |
| `**kwargs` | keyword arguments | none | `verbose` (`bool` or `int`) and `quiet` (`bool`) set the log level. `force_regenerate` is forwarded to the data generator. `compress_data=True` writes `hits.csv.zst`; add `compression_type="gzip"` for `hits.csv.gz`; `compression_level` sets the level; `uncompressed_output=True` overrides compression. `compression_type` alone, without `compress_data=True`, has no effect, and an unsupported type raises `ValueError`. Any other keyword is stored as an attribute on the instance and otherwise ignored. |

The constructor creates no files. Data is written by `generate_data()`.

##### Returns

A `ClickBench` instance. It subclasses `BaseBenchmark`; see {doc}`/reference/python-api/base` for the shared interface.

##### Raises

`ValueError` when `scale_factor` is zero or negative, when it is 1 or more and not a whole number, or when `compression_type` is not one of `none`, `gzip`, `zstd`.

##### Example

```python
from benchbox import ClickBench

benchmark = ClickBench(scale_factor=0.01, output_dir="clickbench_data")
files = benchmark.generate_data()
print(files)
print(len(benchmark.get_queries()), benchmark.get_query("Q1"))
```

Output on 0.4.1:

```text
['clickbench_data/hits.csv']
43 SELECT COUNT(*) FROM hits;
```

##### Compatibility

`benchbox.clickbench.ClickBench` is the same class.

### Constructor

<span id="benchbox.clickbench.ClickBench.__init__"></span>

`ClickBench(scale_factor=1.0, output_dir=None, **kwargs)`. The arguments are in the Parameters table above. Setting `date_range_days`, `user_count` or `enable_compression` has no effect on the data; they are only stored as attributes.

## Methods

### generate_data()

<span id="benchbox.clickbench.ClickBench.generate_data"></span>

`generate_data() -> list` writes one pipe-delimited file, `hits.csv`, plus `_datagen_manifest.json`, and returns the list of data file paths (one entry). At scale factor 0.01 the file has 10,000 rows (no header) and is 5.5 MB. The paths are also stored in `benchmark.tables` as `{"hits": path}`.

```python
data_files = benchmark.generate_data()
print(f"Generated {len(data_files)} data files")
```

The example prints `Generated 1 data files`.

### get_query(query_id, \*, params=None)

<span id="benchbox.clickbench.ClickBench.get_query"></span>

`get_query(query_id, *, params=None) -> str` returns the SQL text of one query.

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `query_id` | `str` | required | `"Q1"` to `"Q43"`. Ids are case-sensitive: `"1"`, `1` and `"q1"` are rejected. |
| `params` | `None` | `None` | ClickBench queries are static. Passing any dict, even `{}`, raises `ValueError`. |

Raises `ValueError` for an unknown id (`Invalid query ID '1'. Available queries: [...]`) and for any non-`None` `params` (`ClickBench queries are static and don't accept parameters`).

The three queries below are `SELECT COUNT(*) FROM hits;` (Q1), `SELECT COUNT(*) FROM hits WHERE AdvEngineID <> 0;` (Q2) and `SELECT COUNT(*) FROM hits WHERE URL LIKE '%google%';` (Q21).

```python
q1 = benchmark.get_query("Q1")
q2 = benchmark.get_query("Q2")
q21 = benchmark.get_query("Q21")
```

### get_queries(dialect=None)

<span id="benchbox.clickbench.ClickBench.get_queries"></span>

`get_queries(dialect=None) -> dict[str, str]` returns all 43 queries keyed `"Q1"` to `"Q43"`.

With a `dialect`, each query is translated with SQLGlot, reading ClickHouse SQL. Identifiers are not quoted (`SELECT COUNT(*) FROM hits` for DuckDB, with the trailing semicolon dropped). If translation fails, including for an unknown dialect name, the original query is returned without an error. Use `translate_query()` when you want failures to raise.

```python
queries = benchmark.get_queries()
print(f"Total queries: {len(queries)}")
queries_bq = benchmark.get_queries(dialect="bigquery")
```

The example prints `Total queries: 43`.

### get_query_categories()

<span id="benchbox.clickbench.ClickBench.get_query_categories"></span>

`get_query_categories() -> dict[str, list[str]]` groups the 43 query ids into eight categories. Every query appears in exactly one.

| Category | Queries |
| --- | --- |
| `basic_aggregation` | Q1-Q7 |
| `grouping_and_ordering` | Q8-Q15 |
| `user_analysis` | Q16-Q20 |
| `text_and_pattern_matching` | Q21-Q27 |
| `string_operations` | Q28-Q29 |
| `mathematical_operations` | Q30 |
| `complex_grouping` | Q31-Q36 |
| `time_based_analysis` | Q37-Q43 |

```python
categories = benchmark.get_query_categories()

for category, query_ids in categories.items():
    print(f"{category}: {len(query_ids)} queries")
```

### get_schema()

<span id="benchbox.clickbench.ClickBench.get_schema"></span>

`get_schema() -> list[dict]` returns a list with one table definition, `hits`, with 105 columns. Each column is a dict with `name`, `type` and `nullable` (always `False`).

```python
schema = benchmark.get_schema()
for table in schema:
    print(f"{table['name']}: {len(table['columns'])} columns")
```

The example prints `hits: 105 columns`.

### get_create_tables_sql(dialect="standard", tuning_config=None)

<span id="benchbox.clickbench.ClickBench.get_create_tables_sql"></span>

`get_create_tables_sql(dialect="standard", tuning_config=None) -> str` returns the `CREATE TABLE hits` script.

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `dialect` | `str` | `"standard"` | Accepted but has no effect. `standard`, `clickhouse`, `duckdb` and an unknown name all return the same script. |
| `tuning_config` | `UnifiedTuningConfiguration` or `None` | `None` | With `None`, no constraints are emitted. When `tuning_config.primary_keys.enabled` is true (the default of a new `UnifiedTuningConfiguration()`), the script ends with `PRIMARY KEY (CounterID, EventDate, UserID, EventTime, WatchID)`. |

```python
from benchbox.core.tuning.interface import UnifiedTuningConfiguration

create_sql = benchmark.get_create_tables_sql()
create_sql_pk = benchmark.get_create_tables_sql(tuning_config=UnifiedTuningConfiguration())
print("PRIMARY KEY" in create_sql, "PRIMARY KEY" in create_sql_pk)
```

The example prints `False True`.

### translate_query(query_id, dialect)

<span id="benchbox.clickbench.ClickBench.translate_query"></span>

`translate_query(query_id, dialect) -> str` returns one query translated to `dialect` with SQLGlot. Unlike `get_queries(dialect=...)`, it reads the query as PostgreSQL SQL and quotes every identifier: `SELECT COUNT(*) FROM "hits"` for DuckDB, PostgreSQL and Snowflake, and ``SELECT COUNT(*) FROM `hits` `` for BigQuery.

Raises `ValueError` for an unknown query id and for an unknown dialect (`Error translating to dialect 'bogus': Unknown dialect 'bogus'.`). Raises `ImportError` if the bundled `benchbox._vendor.sqlglot` runtime is unavailable; reinstall BenchBox rather than installing public SQLGlot.

```python
q1_bq = benchmark.translate_query("Q1", "bigquery")
q10_sf = benchmark.translate_query("Q10", "snowflake")
```

### Inherited members

Every other member comes from `BaseBenchmark`. See {doc}`/reference/python-api/base` for what they do. The ids on this page for the inherited members are kept in the table.

| Group | Member | Kind | Notes |
| --- | --- | --- | --- |
| Run and results | <span id="benchbox.clickbench.ClickBench.cleanup"></span>`cleanup` | method | |
| Run and results | <span id="benchbox.clickbench.ClickBench.create_enhanced_benchmark_result"></span>`create_enhanced_benchmark_result` | method | |
| Run and results | <span id="benchbox.clickbench.ClickBench.create_minimal_benchmark_result"></span>`create_minimal_benchmark_result` | method | |
| Run and results | <span id="benchbox.clickbench.ClickBench.format_results"></span>`format_results` | method | |
| Run and results | <span id="benchbox.clickbench.ClickBench.run_benchmark"></span>`run_benchmark` | method | |
| Run and results | <span id="benchbox.clickbench.ClickBench.run_query"></span>`run_query` | method | |
| Run and results | <span id="benchbox.clickbench.ClickBench.run_with_platform"></span>`run_with_platform` | method | |
| Run and results | <span id="benchbox.clickbench.ClickBench.setup_database"></span>`setup_database` | method | |
| Validation | <span id="benchbox.clickbench.ClickBench.validate_loaded_data"></span>`validate_loaded_data` | method | |
| Validation | <span id="benchbox.clickbench.ClickBench.validate_manifest"></span>`validate_manifest` | method | |
| Validation | <span id="benchbox.clickbench.ClickBench.validate_preflight"></span>`validate_preflight` | method | |
| Verbosity and logging | <span id="benchbox.clickbench.ClickBench.apply_verbosity"></span>`apply_verbosity` | method | |
| Verbosity and logging | <span id="benchbox.clickbench.ClickBench.log_debug_info"></span>`log_debug_info` | method | |
| Verbosity and logging | <span id="benchbox.clickbench.ClickBench.log_error_with_debug_info"></span>`log_error_with_debug_info` | method | |
| Verbosity and logging | <span id="benchbox.clickbench.ClickBench.log_notice"></span>`log_notice` | method | |
| Verbosity and logging | <span id="benchbox.clickbench.ClickBench.log_operation_complete"></span>`log_operation_complete` | method | |
| Verbosity and logging | <span id="benchbox.clickbench.ClickBench.log_operation_start"></span>`log_operation_start` | method | |
| Verbosity and logging | <span id="benchbox.clickbench.ClickBench.log_verbose"></span>`log_verbose` | method | |
| Verbosity and logging | <span id="benchbox.clickbench.ClickBench.log_version_warning"></span>`log_version_warning` | method | |
| Verbosity and logging | <span id="benchbox.clickbench.ClickBench.log_very_verbose"></span>`log_very_verbose` | method | |
| Verbosity and logging | <span id="benchbox.clickbench.ClickBench.logger"></span>`logger` | property | |
| Verbosity and logging | <span id="benchbox.clickbench.ClickBench.quiet"></span>`quiet` | class attribute | |
| Verbosity and logging | <span id="benchbox.clickbench.ClickBench.verbose"></span>`verbose` | class attribute | |
| Verbosity and logging | <span id="benchbox.clickbench.ClickBench.verbose_enabled"></span>`verbose_enabled` | class attribute | |
| Verbosity and logging | <span id="benchbox.clickbench.ClickBench.verbose_level"></span>`verbose_level` | class attribute | |
| Verbosity and logging | <span id="benchbox.clickbench.ClickBench.verbosity_settings"></span>`verbosity_settings` | property | |
| Verbosity and logging | <span id="benchbox.clickbench.ClickBench.very_verbose"></span>`very_verbose` | class attribute | |
| Data and configuration | <span id="benchbox.clickbench.ClickBench.api_surface"></span>`api_surface` | class attribute | |
| Data and configuration | <span id="benchbox.clickbench.ClickBench.benchmark_name"></span>`benchmark_name` | property | |
| Data and configuration | <span id="benchbox.clickbench.ClickBench.csv_delimiter"></span>`csv_delimiter` | property | `\|`. |
| Data and configuration | <span id="benchbox.clickbench.ClickBench.csv_null_marker"></span>`csv_null_marker` | property | `__NULL__`. |
| Data and configuration | <span id="benchbox.clickbench.ClickBench.DATA_SOURCE_BENCHMARK"></span>`DATA_SOURCE_BENCHMARK` | class attribute | `None`: ClickBench generates its own data. |
| Data and configuration | <span id="benchbox.clickbench.ClickBench.get_csv_loading_config"></span>`get_csv_loading_config` | method | Returns `["delim='\|'", "header=false", "nullstr='__NULL__'", "ignore_errors=true", "auto_detect=true"]` for every table. |
| Data and configuration | <span id="benchbox.clickbench.ClickBench.get_data_source_benchmark"></span>`get_data_source_benchmark` | method | |
| Data and configuration | <span id="benchbox.clickbench.ClickBench.output_dir"></span>`output_dir` | property | The resolved directory from the constructor argument. |
| Data and configuration | <span id="benchbox.clickbench.ClickBench.run_with_platform_api_surface"></span>`run_with_platform_api_surface` | class attribute | |
| Data and configuration | <span id="benchbox.clickbench.ClickBench.scale_factor"></span>`scale_factor` | instance attribute | The constructor argument. |
| Data and configuration | <span id="benchbox.clickbench.ClickBench.SKIP_DATA_LOADING"></span>`SKIP_DATA_LOADING` | class attribute | Defined on `BaseBenchmark` from 0.4.2, default `False`. Set it to `True` for a benchmark that needs schema objects but no data files. |
| Data and configuration | <span id="benchbox.clickbench.ClickBench.tables"></span>`tables` | property | Empty until `generate_data()` has run, then the table-to-path mapping. |

## Query Categories

ClickBench organizes its 43 queries into the eight categories that `get_query_categories()` returns (see above). The five groupings below are a coarser reading by query number, used by this guide; they do not match the API categories.

### Scan Queries (Q1, Q2, Q7)

Tests basic table scanning and filtering performance. Execute each query on your platform in the loop.

```python
scan_queries = ["Q1", "Q2", "Q7"]

for query_id in scan_queries:
    query = benchmark.get_query(query_id)
```

**Query Characteristics**:

- **Q1**: `COUNT(*)` over the whole table
- **Q2**: `COUNT(*)` with `AdvEngineID <> 0`
- **Q7**: `MIN(EventDate)` and `MAX(EventDate)`

### Aggregation Queries (Q3-Q6)

Tests aggregation function performance. Execute each query on your platform in the loop.

```python
agg_queries = ["Q3", "Q4", "Q5", "Q6"]

for query_id in agg_queries:
    query = benchmark.get_query(query_id)
```

**Query Characteristics**:

- **Q3**: `SUM`, `COUNT` and `AVG` in one query
- **Q4**: `AVG(UserID)`
- **Q5**: `COUNT(DISTINCT UserID)`
- **Q6**: `COUNT(DISTINCT SearchPhrase)`

### Grouping Queries (Q8-Q19)

Tests GROUP BY and ORDER BY performance. Execute each query on your platform in the loop.

```python
grouping_queries = [f"Q{i}" for i in range(8, 20)]

for query_id in grouping_queries:
    query = benchmark.get_query(query_id)
```

**Query Characteristics**:

- **Q8-Q10**: Group by `AdvEngineID` or `RegionID`, ordered by a count, with `COUNT(DISTINCT UserID)` in Q9 and Q10
- **Q11-Q15**: Group by phone model, `SearchPhrase` or search engine, filtered on non-empty values, top 10
- **Q16-Q19**: Group by `UserID` and `SearchPhrase`, top 10; Q19 adds the minute of `EventTime`

### String Operations Queries (Q20-Q29)

Tests string processing and pattern matching. Execute each query on your platform in the loop.

```python
string_queries = [f"Q{i}" for i in range(20, 30)]

for query_id in string_queries:
    query = benchmark.get_query(query_id)
```

**Query Characteristics**:

- **Q20**: Point lookup on one `UserID`
- **Q21-Q24**: `LIKE` pattern matching on `URL` and `Title`
- **Q25-Q27**: Sort on `EventTime` and `SearchPhrase`, top 10
- **Q28-Q29**: `LENGTH` and, in Q29, `REGEXP_REPLACE` over `URL` and `Referer`, grouped, with `HAVING COUNT(*) > 100000`

### Complex Analytics Queries (Q30-Q43)

Tests complex analytical operations. Execute each query on your platform in the loop.

```python
complex_queries = [f"Q{i}" for i in range(30, 44)]

for query_id in complex_queries:
    query = benchmark.get_query(query_id)
```

**Query Characteristics**:

- **Q30**: 90 `SUM(ResolutionWidth + n)` expressions
- **Q31-Q36**: Group by high-cardinality keys (`ClientIP`, `WatchID`, `URL`) and by derived expressions
- **Q37-Q43**: Page-view counts for `CounterID = 62` in July 2013, grouped by `URL`, `Title`, traffic source, `URLHash` or window size; Q43 groups by minute

## Usage Examples

### Basic Benchmark Run

Scale factor 0.01 creates 10,000 rows. The example generates the data, runs the benchmark on DuckDB and prints the results.

```python
from benchbox import ClickBench
from benchbox.platforms.duckdb import DuckDBAdapter

benchmark = ClickBench(scale_factor=0.01)

benchmark.generate_data()

adapter = DuckDBAdapter()
results = benchmark.run_with_platform(adapter)

print(f"Benchmark: {results.benchmark_name}")
print(f"Total time: {results.total_execution_time:.2f}s")
print(f"Queries: {results.successful_queries}/{results.total_queries}")
print(f"Avg query time: {results.average_query_time:.3f}s")
```

### Category-Based Execution

The example loads the data, gets the query categories and runs each category. It then prints a category summary.

```python
from benchbox import ClickBench
from benchbox.platforms.duckdb import DuckDBAdapter
import time

benchmark = ClickBench(scale_factor=0.01)
benchmark.generate_data()

adapter = DuckDBAdapter()
conn = adapter.create_connection()

adapter.create_schema(benchmark, conn)
adapter.load_data(benchmark, conn, benchmark.output_dir)

categories = benchmark.get_query_categories()

category_results = {}

for category_name, query_ids in categories.items():
    print(f"\n{category_name.upper()} queries:")
    category_times = []

    for query_id in query_ids:
        query = benchmark.get_query(query_id)

        start = time.time()
        result = adapter.execute_query(conn, query, query_id)
        duration = time.time() - start

        category_times.append(duration)
        print(f"  {query_id}: {duration:.3f}s")

    category_results[category_name] = {
        "total_time": sum(category_times),
        "avg_time": sum(category_times) / len(category_times),
        "query_count": len(query_ids)
    }

print("\nCategory Summary:")
for category, stats in category_results.items():
    print(f"{category}: {stats['avg_time']:.3f}s avg ({stats['query_count']} queries)")
```

### Performance Analysis

The example sets up the data and runs each query for several iterations. It then prints a performance summary and the geometric mean of the median times.

```python
from benchbox import ClickBench
from benchbox.platforms.duckdb import DuckDBAdapter
import time
from statistics import geometric_mean, mean, median

benchmark = ClickBench(scale_factor=0.01)
adapter = DuckDBAdapter()

benchmark.generate_data()
conn = adapter.create_connection()
adapter.create_schema(benchmark, conn)
adapter.load_data(benchmark, conn, benchmark.output_dir)

iterations = 3
all_results = {}

for query_id in [f"Q{i}" for i in range(1, 44)]:
    times = []

    for iteration in range(iterations):
        query = benchmark.get_query(query_id)

        start = time.time()
        try:
            result = adapter.execute_query(conn, query, query_id)
            duration = time.time() - start
            times.append(duration)
        except Exception as e:
            print(f"{query_id} iteration {iteration}: ERROR - {e}")
            break

    if times:
        all_results[query_id] = {
            "mean": mean(times),
            "median": median(times),
            "min": min(times),
            "max": max(times),
            "times": times
        }

print("Performance Summary (3 iterations):")
print(f"{'Query':<8} {'Mean':<10} {'Median':<10} {'Min':<10} {'Max':<10}")
print("-" * 50)

for query_id, stats in all_results.items():
    print(f"{query_id:<8} {stats['mean']:<10.4f} {stats['median']:<10.4f} "
          f"{stats['min']:<10.4f} {stats['max']:<10.4f}")

all_times = [stats["median"] for stats in all_results.values()]
print(f"\nGeometric mean query time: {geometric_mean(all_times):.4f}s")
```

### Multi-Platform Comparison

Run the same generated data through several adapters and compare the result objects. This example uses two DuckDB configurations; add other adapters to the dictionary the same way.

```python
from benchbox import ClickBench
from benchbox.platforms.duckdb import DuckDBAdapter
import pandas as pd

benchmark = ClickBench(scale_factor=0.01, output_dir="./data/clickbench")
benchmark.generate_data()

platforms = {
    "DuckDB 1GB": DuckDBAdapter(memory_limit="1GB"),
    "DuckDB 4GB": DuckDBAdapter(memory_limit="4GB"),
}

results_data = []

for name, adapter in platforms.items():
    print(f"\nBenchmarking {name}...")
    results = benchmark.run_with_platform(adapter)

    results_data.append({
        "platform": name,
        "total_time": results.total_execution_time,
        "avg_query_time": results.average_query_time,
        "successful": results.successful_queries,
        "failed": results.failed_queries,
        "queries_per_sec": results.total_queries / results.total_execution_time
    })

df = pd.DataFrame(results_data)
print("\nBenchmark Results:")
print(df)
```

### Columnar Database Optimization

ClickBench is one wide table (`hits`), which suits columnar engines. To inspect the table definition that a platform receives:

```python
from benchbox import ClickBench

benchmark = ClickBench(scale_factor=0.01)
ddl = benchmark.get_create_tables_sql()
print(ddl.splitlines()[:3])
```

The example prints `['CREATE TABLE hits (', '  WatchID BIGINT NOT NULL,', '  JavaEnable SMALLINT NOT NULL,']`.

The script is the same for every dialect, so platform-specific column codecs or sort keys have to be applied by the platform adapter or by your own statements after `create_schema()`.

### Query Translation Example

The original ClickBench queries are in the ClickHouse dialect. The example translates one query to other dialects.

```python
from benchbox import ClickBench

benchmark = ClickBench(scale_factor=0.01)

q1_original = benchmark.get_query("Q1")
print("Original:")
print(q1_original)

q1_duckdb = benchmark.translate_query("Q1", "duckdb")
print("\nDuckDB:")
print(q1_duckdb)

q1_postgres = benchmark.translate_query("Q1", "postgres")
print("\nPostgreSQL:")
print(q1_postgres)

q1_bigquery = benchmark.translate_query("Q1", "bigquery")
print("\nBigQuery:")
print(q1_bigquery)
```

Output on 0.4.1:

```text
Original:
SELECT COUNT(*) FROM hits;

DuckDB:
SELECT COUNT(*) FROM "hits"

PostgreSQL:
SELECT COUNT(*) FROM "hits"

BigQuery:
SELECT COUNT(*) FROM `hits`
```

### Selective Query Execution

The example runs only the fast queries (scan and simple aggregation), and then only the string operations queries.

```python
from benchbox import ClickBench
from benchbox.platforms.duckdb import DuckDBAdapter

benchmark = ClickBench(scale_factor=0.01)
adapter = DuckDBAdapter()

benchmark.generate_data()
conn = adapter.create_connection()
adapter.create_schema(benchmark, conn)
adapter.load_data(benchmark, conn, benchmark.output_dir)

fast_queries = ["Q1", "Q2", "Q3", "Q4", "Q7"]

print("Running fast queries:")
for query_id in fast_queries:
    query = benchmark.get_query(query_id)
    result = adapter.execute_query(conn, query, query_id)
    print(f"{query_id}: {result['execution_time_seconds']:.3f}s ({result['status']})")

string_queries = [f"Q{i}" for i in range(20, 30)]

print("\nRunning string operations queries:")
for query_id in string_queries:
    query = benchmark.get_query(query_id)
    result = adapter.execute_query(conn, query, query_id)
    print(f"{query_id}: {result['execution_time_seconds']:.3f}s ({result['status']})")
```

## See Also

- {doc}`index` - Benchmark API overview
- {doc}`tpch` - TPC-H benchmark API
- {doc}`tpcds` - TPC-DS benchmark API
- {doc}`ssb` - Star Schema Benchmark API
- {doc}`../base` - Base benchmark interface
- {doc}`../results` - Results API
- {doc}`/benchmarks/clickbench` - ClickBench guide
- {doc}`/benchmarks/index` - Benchmark catalog

### External Resources

- [ClickBench GitHub](https://github.com/ClickHouse/ClickBench) - Official benchmark repository
- [ClickBench Results](https://benchmark.clickhouse.com/) - Cross-database performance comparisons
- [ClickBench Methodology](https://github.com/ClickHouse/ClickBench) - Detailed methodology
- [DuckDB Labs Benchmark](https://duckdblabs.github.io/db-benchmark/) - Updated benchmark results
