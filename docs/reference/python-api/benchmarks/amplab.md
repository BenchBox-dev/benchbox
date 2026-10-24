# AMPLab Big Data Benchmark API

```{tags} reference, python-api, custom-benchmark
```

Python API reference for the AMPLab Big Data Benchmark.

## Overview

The AMPLab Big Data Benchmark tests the performance of big data processing systems using realistic web analytics workloads. Developed by UC Berkeley's AMPLab, this benchmark focuses on three core patterns: scanning, joining, and analytics operations on web-scale data.

**Key Features**:

- **Web Analytics Workload**: Models internet-scale data processing
- **Three Query Types**: Scan, Join, and Analytics patterns
- **Simple Schema**: 3 tables (rankings, uservisits, documents)
- **Scalable**: Any positive scale factor; 0.01 generates about 6.6 MB of data
- **Big Data Focus**: Designed for distributed processing systems

**Reference**: <https://amplab.cs.berkeley.edu/benchmark/>

## Quick Start

```python
from benchbox import AMPLab
from benchbox.platforms.duckdb import DuckDBAdapter

benchmark = AMPLab(scale_factor=0.1)
benchmark.generate_data()

adapter = DuckDBAdapter()
results = adapter.run_benchmark(benchmark)
print(results.total_queries)
```

The DuckDB adapter needs the `duckdb` package. On 0.4.1 the run executes all 8 queries and prints `8`.

## API Reference

### AMPLab Class

#### `benchbox.AMPLab`

<span id="benchbox.amplab.AMPLab"></span>

Creates an AMPLab benchmark that generates the three web-analytics tables and serves eight parameterised queries.

**Import:** `from benchbox import AMPLab` · **Extras:** none

##### Parameters

<span id="benchbox.amplab.AMPLab.__init__"></span>

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `scale_factor` | `float` | `1.0` | Data size multiplier. Must be positive. Values of 1 or more must be whole numbers. |
| `output_dir` | `str`, `Path` or `None` | `None` | Directory for generated data files. When `None`, the directory is `benchmark_runs/datagen/amplab_<sf token>` under the current directory (for example `amplab_sf001` for 0.01), or under `$BENCHBOX_OUTPUT_DIR/datagen` when that variable is set. |
| `**kwargs` | keyword arguments | none | `verbose` (`bool` or `int`) and `quiet` (`bool`) set the log level. `force_regenerate` is forwarded to the data generator. Any other keyword is stored as an attribute on the instance. |

The constructor creates no files. Data is written by `generate_data()`.

##### Returns

An `AMPLab` instance. It subclasses `BaseBenchmark`; see {doc}`/reference/python-api/base` for the shared interface.

##### Raises

`ValueError` when `scale_factor` is zero or negative, or when it is 1 or more and not a whole number (`1.5` is rejected, `2` and `0.5` are accepted).

##### Example

```python
from benchbox import AMPLab

benchmark = AMPLab(scale_factor=0.01, output_dir="amplab_data")
files = benchmark.generate_data()
print(sorted(files))
print(list(benchmark.get_queries()))
print(benchmark.get_query("1", params={"pagerank_threshold": 1500}).strip())
try:
    AMPLab(scale_factor=1.5)
except ValueError as error:
    print(error)
```

Output on 0.4.1:

```text
['documents', 'rankings', 'uservisits']
['1', '1a', '2', '2a', '3', '3a', '4', '5']
SELECT pageURL, pageRank
FROM rankings
WHERE pageRank > 1500;
Scale factors >= 1 must be whole integers. Got: 1.5. Use values like 1, 2, 10, etc. for large scale factors. Use values like 0.1, 0.01, 0.001, etc. for small scale factors.
```

##### Compatibility

`benchbox.amplab.AMPLab` is the same class. The data methods below return different types than their annotations state; the sections say which.

#### `AMPLab.generate_data`

<span id="benchbox.amplab.AMPLab.generate_data"></span>

`generate_data()` writes the data files and returns a `dict` that maps table name to file path. The signature is annotated `list[str | Path]`, but the value is a dict on 0.4.1.

At scale factor 0.01 it writes three pipe-delimited files with no header row:

| Table | File | Rows | Size |
| --- | --- | --- | --- |
| `rankings` | `rankings.tbl` | 2,500 | 87 KB |
| `documents` | `documents.tbl` | 1,250 | 3.1 MB |
| `uservisits` | `uservisits.tbl` | 25,000 | 3.4 MB |

The directory also receives `_datagen_manifest.json`. The paths are also available afterwards in `benchmark.tables`, which is empty before `generate_data()` runs. `get_csv_loading_config(table)` returns `["delim='|'"]` for every table.

### Schema Methods

#### `AMPLab.get_create_tables_sql`

<span id="benchbox.amplab.AMPLab.get_create_tables_sql"></span>

`get_create_tables_sql(dialect="standard", tuning_config=None) -> str` returns the `CREATE TABLE` statements for `rankings`, `documents` and `uservisits` as one script.

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `dialect` | `str` | `"standard"` | Accepted but has no effect. `standard`, `duckdb`, `postgres` and an unknown name all return the same script. |
| `tuning_config` | `UnifiedTuningConfiguration` or `None` | `None` | With `None`, no constraints are emitted. When `tuning_config.primary_keys.enabled` is true, `PRIMARY KEY` is added to `rankings.pageURL` and `documents.url`. |

```python
schema_sql = benchmark.get_create_tables_sql(dialect="duckdb")
print(schema_sql.splitlines()[:4])
```

This prints the first four lines of the script: `['CREATE TABLE rankings (', '  pageURL VARCHAR(300),', '  pageRank INTEGER,', '  avgDuration INTEGER']`.

#### `AMPLab.get_schema`

<span id="benchbox.amplab.AMPLab.get_schema"></span>

`get_schema() -> dict` returns a mapping from table name to a definition with `name` and `columns`. Each column has `name`, `type` and, for key columns, `primary_key`. The keys are `rankings`, `uservisits` and `documents`. The annotation says `list[dict]`, but the value is a dict.

### Query Methods

#### `AMPLab.get_query`

<span id="benchbox.amplab.AMPLab.get_query"></span>

`get_query(query_id, *, params=None) -> str` returns one query as SQL text.

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `query_id` | `int` or `str` | required | One of `1`, `1a`, `2`, `2a`, `3`, `3a`, `4`, `5`. Integers are converted to strings. |
| `params` | `dict` or `None` | `None` | Values that replace the defaults below. Names that the query does not use are ignored. |

Raises `ValueError` for an unknown id: `Invalid query ID: 9. Available: 1, 1a, 2, 2a, 3, 3a, 4, 5`.

Query groups: scan (`1`, `1a`), join (`2`, `2a`), analytics (`3`, `3a`, `4`, `5`). Parameter names and defaults:

| Parameter | Default | Used by |
| --- | --- | --- |
| `pagerank_threshold` | `1000` | `1`, `1a`, `2a`, `5` |
| `start_date` | `"2000-01-01"` | `2`, `2a`, `3`, `4`, `5` |
| `end_date` | `"2000-01-03"` | `2`, `3` |
| `limit_rows` | `100` | `2`, `2a`, `3`, `3a`, `4`, `5` |
| `search_term` | `"database"` | `3` |
| `min_visits` | `10` | `3`, `4`, `5` |
| `min_revenue` | `1.0` | `4` |
| `min_content_length` | `1000` | `3a` |
| `keyword1`, `keyword2` | `"web"`, `"data"` | `3a` |

```python
scan_query = benchmark.get_query("1", params={"pagerank_threshold": 1500})
join_query = benchmark.get_query("2", params={
    "start_date": "1980-01-01",
    "end_date": "1980-04-01",
    "limit_rows": 100,
})
analytics_query = benchmark.get_query("3", params={"search_term": "google", "min_visits": 10})
```

#### `AMPLab.get_queries`

<span id="benchbox.amplab.AMPLab.get_queries"></span>

`get_queries(dialect=None) -> dict[str, str]` returns all eight queries, keyed by id and rendered with the default parameters.

With a `dialect`, each query is translated with SQLGlot from the `netezza` dialect. DuckDB output for query `1` is `SELECT "pageURL", "pageRank" FROM "rankings" WHERE "pageRank" > 1000` (translation normalises whitespace and drops the trailing semicolon). An unknown dialect name raises `UnknownSqlDialectError`.

```python
queries = benchmark.get_queries()
print(list(queries.keys()))
```

The keys are `['1', '1a', '2', '2a', '3', '3a', '4', '5']`.

### Inherited members

Every other member comes from `BaseBenchmark`. See {doc}`/reference/python-api/base` for what they do. The ids on this page for the inherited members are kept in the table.

| Group | Member | Kind | Notes |
| --- | --- | --- | --- |
| Run and results | <span id="benchbox.amplab.AMPLab.cleanup"></span>`cleanup` | method | |
| Run and results | <span id="benchbox.amplab.AMPLab.create_enhanced_benchmark_result"></span>`create_enhanced_benchmark_result` | method | |
| Run and results | <span id="benchbox.amplab.AMPLab.create_minimal_benchmark_result"></span>`create_minimal_benchmark_result` | method | |
| Run and results | <span id="benchbox.amplab.AMPLab.format_results"></span>`format_results` | method | |
| Run and results | <span id="benchbox.amplab.AMPLab.run_benchmark"></span>`run_benchmark` | method | |
| Run and results | <span id="benchbox.amplab.AMPLab.run_query"></span>`run_query` | method | |
| Run and results | <span id="benchbox.amplab.AMPLab.run_with_platform"></span>`run_with_platform` | method | |
| Run and results | <span id="benchbox.amplab.AMPLab.setup_database"></span>`setup_database` | method | |
| Run and results | <span id="benchbox.amplab.AMPLab.translate_query"></span>`translate_query` | method | |
| Validation | <span id="benchbox.amplab.AMPLab.validate_loaded_data"></span>`validate_loaded_data` | method | |
| Validation | <span id="benchbox.amplab.AMPLab.validate_manifest"></span>`validate_manifest` | method | |
| Validation | <span id="benchbox.amplab.AMPLab.validate_preflight"></span>`validate_preflight` | method | |
| Verbosity and logging | <span id="benchbox.amplab.AMPLab.apply_verbosity"></span>`apply_verbosity` | method | |
| Verbosity and logging | <span id="benchbox.amplab.AMPLab.log_debug_info"></span>`log_debug_info` | method | |
| Verbosity and logging | <span id="benchbox.amplab.AMPLab.log_error_with_debug_info"></span>`log_error_with_debug_info` | method | |
| Verbosity and logging | <span id="benchbox.amplab.AMPLab.log_notice"></span>`log_notice` | method | |
| Verbosity and logging | <span id="benchbox.amplab.AMPLab.log_operation_complete"></span>`log_operation_complete` | method | |
| Verbosity and logging | <span id="benchbox.amplab.AMPLab.log_operation_start"></span>`log_operation_start` | method | |
| Verbosity and logging | <span id="benchbox.amplab.AMPLab.log_verbose"></span>`log_verbose` | method | |
| Verbosity and logging | <span id="benchbox.amplab.AMPLab.log_version_warning"></span>`log_version_warning` | method | |
| Verbosity and logging | <span id="benchbox.amplab.AMPLab.log_very_verbose"></span>`log_very_verbose` | method | |
| Verbosity and logging | <span id="benchbox.amplab.AMPLab.logger"></span>`logger` | property | |
| Verbosity and logging | <span id="benchbox.amplab.AMPLab.quiet"></span>`quiet` | class attribute | |
| Verbosity and logging | <span id="benchbox.amplab.AMPLab.verbose"></span>`verbose` | class attribute | |
| Verbosity and logging | <span id="benchbox.amplab.AMPLab.verbose_enabled"></span>`verbose_enabled` | class attribute | |
| Verbosity and logging | <span id="benchbox.amplab.AMPLab.verbose_level"></span>`verbose_level` | class attribute | |
| Verbosity and logging | <span id="benchbox.amplab.AMPLab.verbosity_settings"></span>`verbosity_settings` | property | |
| Verbosity and logging | <span id="benchbox.amplab.AMPLab.very_verbose"></span>`very_verbose` | class attribute | |
| Data and configuration | <span id="benchbox.amplab.AMPLab.api_surface"></span>`api_surface` | class attribute | |
| Data and configuration | <span id="benchbox.amplab.AMPLab.benchmark_name"></span>`benchmark_name` | property | |
| Data and configuration | <span id="benchbox.amplab.AMPLab.csv_delimiter"></span>`csv_delimiter` | property | `None`. |
| Data and configuration | <span id="benchbox.amplab.AMPLab.csv_null_marker"></span>`csv_null_marker` | property | `None`. |
| Data and configuration | <span id="benchbox.amplab.AMPLab.DATA_SOURCE_BENCHMARK"></span>`DATA_SOURCE_BENCHMARK` | class attribute | `None`: AMPLab generates its own data. |
| Data and configuration | <span id="benchbox.amplab.AMPLab.get_csv_loading_config"></span>`get_csv_loading_config` | method | Returns `["delim='\|'"]` for every table. |
| Data and configuration | <span id="benchbox.amplab.AMPLab.get_data_source_benchmark"></span>`get_data_source_benchmark` | method | |
| Data and configuration | <span id="benchbox.amplab.AMPLab.output_dir"></span>`output_dir` | property | The resolved directory from the constructor argument. |
| Data and configuration | <span id="benchbox.amplab.AMPLab.run_with_platform_api_surface"></span>`run_with_platform_api_surface` | class attribute | |
| Data and configuration | <span id="benchbox.amplab.AMPLab.scale_factor"></span>`scale_factor` | instance attribute | The constructor argument. |
| Data and configuration | <span id="benchbox.amplab.AMPLab.SKIP_DATA_LOADING"></span>`SKIP_DATA_LOADING` | class attribute | Defined on `BaseBenchmark` from 0.4.2, default `False`. Set it to `True` for a benchmark that needs schema objects but no data files. |
| Data and configuration | <span id="benchbox.amplab.AMPLab.tables"></span>`tables` | property | Empty until `generate_data()` has run, then the table-to-path mapping. |

## Usage Examples

### Basic Benchmark Execution

```python
from benchbox import AMPLab
from benchbox.platforms.duckdb import DuckDBAdapter

benchmark = AMPLab(scale_factor=0.1)
data_files = benchmark.generate_data()

adapter = DuckDBAdapter(memory_limit="4GB")
results = adapter.run_benchmark(benchmark)

print(f"Queries: {results.total_queries}")
print(f"Average time: {results.average_query_time:.3f}s")
```

### Query Type Testing

```python
from benchbox import AMPLab
from benchbox.platforms.duckdb import DuckDBAdapter
import time

benchmark = AMPLab(scale_factor=0.01)
benchmark.generate_data()

adapter = DuckDBAdapter()
conn = adapter.create_connection()
adapter.create_schema(benchmark, conn)
adapter.load_data(benchmark, conn, benchmark.output_dir)

query_types = {
    'Scan': ['1', '1a'],
    'Join': ['2', '2a'],
    'Analytics': ['3', '3a']
}

params = {
    'pagerank_threshold': 1000,
    'start_date': '1980-01-01',
    'end_date': '1980-04-01',
    'limit_rows': 100
}

for query_type, query_ids in query_types.items():
    print(f"\n{query_type} Queries:")
    for query_id in query_ids:
        query = benchmark.get_query(query_id, params=params)

        start = time.time()
        result = conn.execute(query).fetchall()
        elapsed = time.time() - start

        print(f"  Query {query_id}: {elapsed*1000:.1f} ms ({len(result)} rows)")
```

### Performance Comparison

```python
from benchbox import AMPLab
from benchbox.platforms.duckdb import DuckDBAdapter
import time

scale_factors = [0.01, 0.1, 0.5]

for sf in scale_factors:
    print(f"\n=== Scale Factor {sf} ===")

    benchmark = AMPLab(scale_factor=sf)
    benchmark.generate_data()

    adapter = DuckDBAdapter()
    conn = adapter.create_connection()
    adapter.create_schema(benchmark, conn)
    adapter.load_data(benchmark, conn, benchmark.output_dir)

    scan_query = benchmark.get_query("1", params={'pagerank_threshold': 1000})

    start = time.time()
    result = conn.execute(scan_query).fetchall()
    elapsed = time.time() - start

    print(f"  Scan query: {elapsed*1000:.1f} ms")
    print(f"  Rows: {len(result)}")
```

## Best Practices

1. **Use Appropriate Scale Factors**

   Use a small scale factor for development, a moderate one for testing, and the full scale for production. Scale factor 0.01 is about 6.6 MB.

   ```python
   dev = AMPLab(scale_factor=0.01)

   test = AMPLab(scale_factor=0.1)

   prod = AMPLab(scale_factor=1.0)
   ```

2. **Parameterize Queries**

   ```python
   params = {
       'pagerank_threshold': 1000,
       'start_date': '1980-01-01',
       'end_date': '1980-04-01',
       'limit_rows': 100,
       'search_term': 'google',
       'min_visits': 10
   }

   query = benchmark.get_query("2", params=params)
   ```

3. **Test Query Types Separately**

   Test scan, join, and analytics performance separately.

   ```python
   scan_queries = ['1', '1a']

   join_queries = ['2', '2a']

   analytics_queries = ['3', '3a', '4', '5']
   ```

## See Also

- {doc}`/benchmarks/amplab` - AMPLab benchmark guide
- {doc}`clickbench` - ClickBench analytics benchmark
- {doc}`tpch` - TPC-H benchmark
- {doc}`/reference/python-api/base` - Base benchmark interface

### External Resources

- [AMPLab Benchmark](https://amplab.cs.berkeley.edu/benchmark/) - Original specification
- [Berkeley AMPLab](https://amplab.cs.berkeley.edu/) - Research lab
