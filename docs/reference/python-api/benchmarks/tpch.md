# TPC-H Benchmark API

```{tags} reference, python-api, tpc-h
```

Python API reference for the TPC-H benchmark.

## Overview

The TPC-H benchmark simulates a decision support system with 22 analytical queries. It models a wholesale supplier with parts, suppliers, customers, and orders.

**Key Features**:

- 22 analytical queries covering complex business scenarios
- 8 tables with realistic relationships
- Parametrized queries for randomization
- Data and queries generated with the TPC-H `dbgen` and `qgen` tools
- Scale factors up to 100000

## Quick Start

The example creates the benchmark, generates the data and runs it on a platform.

```python
from benchbox import TPCH
from benchbox.platforms.duckdb import DuckDBAdapter

benchmark = TPCH(scale_factor=1.0)

benchmark.generate_data()

adapter = DuckDBAdapter()
results = benchmark.run_with_platform(adapter)

print(f"Completed in {results.total_execution_time:.2f}s")
```

The DuckDB adapter needs the `duckdb` package. Scale factor 1.0 writes about 1 GB.

## API Reference

### TPCH Class

#### `benchbox.TPCH`

<span id="benchbox.tpch.TPCH"></span>

Creates a TPC-H benchmark that generates the eight TPC-H tables with `dbgen` and serves the 22 TPC-H queries.

**Import:** `from benchbox import TPCH` · **Extras:** none

##### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `scale_factor` | `float` | `1.0` | Data size multiplier. Must be a number greater than 0 and at most 100000. Values of 1 or more must be whole numbers. |
| `output_dir` | `str`, `Path` or `None` | `None` | Directory for generated data files. When `None`, the directory is `benchmark_runs/datagen/tpch_<sf token>` under the current directory (for example `tpch_sf001` for 0.01), or under `$BENCHBOX_OUTPUT_DIR/datagen` when that variable is set. |
| `**kwargs` | keyword arguments | none | See the list below. |

Keyword arguments that the constructor acts on:

- `parallel` (`int`, default `1`): number of `dbgen` processes. With `parallel` above 1, each large table is written as numbered chunk files (`customer.tbl.1`, `customer.tbl.2`) and its entry in `generate_data()` and `tables` is a list of paths. `nation` and `region` stay a single file.
- `force_regenerate` (`bool`, default `False`): regenerate data even when a valid set exists in `output_dir`.
- `official` (`bool`, default `False`): mark the run as an official run; it only affects `compliance_class`.
- `tpch_refresh_functions` (`bool`, default `False`): opt in to RF1/RF2 during Power and the concurrent RF stream during Throughput. These operations permanently modify database contents.
- `tpch_refresh_first_set` (`int`, default `1`): first DBGen update set used for Power; Throughput uses the following sets. After a run, advance to the next unused set or reload the original data before reusing a set.
- `compress_data` (`bool`): write `.zst` files; add `compression_type="gzip"` for `.gz`. Pass an absolute `output_dir` with compression: on 0.4.1 a relative path made `dbgen` fail with `RuntimeError: dbgen failed for table customer: Open failed for ... dists.dss`.
- `verbose` (`bool` or `int`) and `quiet` (`bool`): log level.

Any other keyword is stored as an attribute on the instance.

The constructor creates no files. Data is written by `generate_data()`.

##### Returns

A `TPCH` instance. It subclasses `BaseBenchmark`; see {doc}`/reference/python-api/base` for the shared interface. The instance has a `compliance_class` attribute, a `TpchComplianceClass` value:

| Value | When |
| --- | --- |
| `official` | `official=True` and `scale_factor` is 1, 10, 30, 100, 300, 1000, 3000, 10000, 30000 or 100000. |
| `unofficial_nonstandard` | `scale_factor` is 1 or more and the run is not official, or the scale factor is not an official one. |
| `unofficial_subscale` | `scale_factor` is below 1. |

##### Raises

- `TypeError` when `scale_factor` is not a number, or `parallel` is not an integer.
- `ValueError` when `scale_factor` is zero or negative, is 1 or more and not a whole number, or exceeds 100000; or when `parallel` is below 1.

##### Example

```python
from benchbox import TPCH

benchmark = TPCH(scale_factor=0.01, output_dir="tpch_data")
files = benchmark.generate_data()
print([path.name for path in files])
print(benchmark.compliance_class.value)
print(TPCH(scale_factor=1, official=True).compliance_class.value)
```

Output on 0.4.1:

```text
['customer.tbl', 'lineitem.tbl', 'nation.tbl', 'orders.tbl', 'part.tbl', 'partsupp.tbl', 'region.tbl', 'supplier.tbl']
unofficial_subscale
official
```

##### Compatibility

`benchbox.tpch.TPCH` is the same class. Five methods fail on 0.4.1: `generate_streams`, `get_stream_info`, `get_all_streams_info`, `run_official_benchmark` and `run_maintenance_test`. Each section below says how.

### Constructor

<span id="benchbox.tpch.TPCH.__init__"></span>

`TPCH(scale_factor=1.0, output_dir=None, **kwargs)`. The arguments are in the Parameters table above.

## Methods

### generate_data()

<span id="benchbox.tpch.TPCH.generate_data"></span>

`generate_data() -> list` runs `dbgen` and returns the data file paths. The result is a list of `pathlib.Path` in alphabetical table order (`customer`, `lineitem`, `nation`, `orders`, `part`, `partsupp`, `region`, `supplier`). With `parallel` above 1, entries for chunked tables are lists of paths. `benchmark.tables` maps each table name to the same paths.

The files are pipe-delimited `.tbl` files. The directory also receives `dists.dss` and `_datagen_manifest.json`. At scale factor 0.01:

| Table | Rows | Size |
| --- | --- | --- |
| `region` | 5 | 384 B |
| `nation` | 25 | 2.2 KB |
| `supplier` | 100 | 14 KB |
| `part` | 2,000 | 235 KB |
| `partsupp` | 8,000 | 1.2 MB |
| `customer` | 1,500 | 239 KB |
| `orders` | 15,000 | 1.6 MB |
| `lineitem` | 60,175 | 7.2 MB |

A second call finds the valid data in `output_dir` and returns at once, unless `force_regenerate=True`.

```python
data_files = benchmark.generate_data()
print(f"Generated {len(data_files)} table files")
```

The example prints `Generated 8 table files`.

### get_query(query_id, \*, params=None, seed=None, scale_factor=None, dialect=None)

<span id="benchbox.tpch.TPCH.get_query"></span>

`get_query(query_id, *, params=None, seed=None, scale_factor=None, dialect=None, base_dialect=None, **kwargs) -> str` returns one query as SQL text.

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `query_id` | `int` | required | `1` to `22`. Strings and floats such as `"1"` and `1.0` raise `TypeError`. |
| `params` | `dict` or `None` | `None` | Optional keys: `seed`, `scale_factor`, `stream_id` (0 to 40; derives a seed from the stream and the query's position in its permutation) and `permutation` (a list of query ids). The `seed` and `scale_factor` arguments take precedence. Other keys are ignored. |
| `seed` | `int` or `None` | `None` | With `None`, the query uses the TPC-H specification's validation parameters (Q1 subtracts 90 days). With an integer, `qgen` substitutes random parameters, and the same seed always gives the same text (Q1 uses 68 days for seed 1 and 91 for seed 42). |
| `scale_factor` | `float` or `None` | `None` | Scale factor used for scale-dependent parameters, in place of the benchmark's own. Q11's fraction is `0.0001 / scale_factor`: `0.01` at 0.01 and `0.00001` at 10. Must be positive. |
| `dialect` | `str` or `None` | `None` | Target SQL dialect, translated with SQLGlot. An unknown name logs a warning and returns the original text. |
| `base_dialect` | `str` or `None` | `None` | Source dialect for the translation. The default is `netezza`. |

Raises `TypeError` for a non-integer `query_id` or `seed` or a non-numeric `scale_factor`, and `ValueError` for a `query_id` outside 1 to 22 (`Query ID must be 1-22, got 0`) or a `scale_factor` that is not positive.

The first call returns the default query, the second a translated dialect, and the third a query with seed 42 and scale factor 10 for parameter generation.

```python
q1 = benchmark.get_query(1)

q1_bq = benchmark.get_query(1, dialect="bigquery")

q1_param = benchmark.get_query(1, seed=42, scale_factor=10.0)
```

### get_queries(dialect=None, base_dialect=None)

<span id="benchbox.tpch.TPCH.get_queries"></span>

`get_queries(dialect=None, base_dialect=None) -> dict[str, str]` returns all 22 queries keyed `"1"` to `"22"` (string keys), using the default parameters. Each text equals `get_query(int(key))`.

With a `dialect`, each query is translated from `base_dialect` (default `netezza`) with SQLGlot.

The first call gets all queries and prints `Total queries: 22`. The second gets them with dialect translation.

```python
queries = benchmark.get_queries()
print(f"Total queries: {len(queries)}")

queries_bq = benchmark.get_queries(dialect="bigquery")
```

### get_schema()

<span id="benchbox.tpch.TPCH.get_schema"></span>

`get_schema() -> dict` returns a mapping from table name to a definition with `name` and `columns`. The keys, in order, are `region` (3 columns), `nation` (4), `supplier` (7), `part` (9), `partsupp` (5), `customer` (8), `orders` (9) and `lineitem` (16). The annotation says `list[dict]`, but the value is a dict.

```python
schema = benchmark.get_schema()
for name, table in schema.items():
    print(f"{table['name']}: {len(table['columns'])} columns")
```

### get_create_tables_sql(dialect="standard", tuning_config=None)

<span id="benchbox.tpch.TPCH.get_create_tables_sql"></span>

`get_create_tables_sql(dialect="standard", tuning_config=None) -> str` returns the `CREATE TABLE` statements for the eight tables.

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `dialect` | `str` | `"standard"` | Accepted but has no effect: `standard` and `bigquery` return the same script. |
| `tuning_config` | `UnifiedTuningConfiguration` or `None` | `None` | With `None`, no constraints are emitted. A new `UnifiedTuningConfiguration()` enables primary and foreign keys, adding 8 `PRIMARY KEY` and 9 `FOREIGN KEY` clauses. |

```python
from benchbox.core.tuning.interface import UnifiedTuningConfiguration

create_sql = benchmark.get_create_tables_sql()
create_sql_keys = benchmark.get_create_tables_sql(tuning_config=UnifiedTuningConfiguration())
print(create_sql.count("PRIMARY KEY"), create_sql_keys.count("PRIMARY KEY"), create_sql_keys.count("FOREIGN KEY"))
```

The example prints `0 8 9`.

### generate_streams(num_streams=1, rng_seed=None, streams_output_dir=None)

<span id="benchbox.tpch.TPCH.generate_streams"></span>

`generate_streams(num_streams=1, rng_seed=None, streams_output_dir=None) -> list[Path]` is meant to write query stream files for throughput testing.

On 0.4.1 it does not work: every call raises `AttributeError: 'TPCHBenchmark' object has no attribute 'generate_streams'`, whatever the arguments. The same applies to `get_stream_info(stream_id)` and `get_all_streams_info()` below. To get permuted query orders, run the power test (`run_power_test` accepts `stream_id`) or use the platform adapters.

### get_stream_info(stream_id)

<span id="benchbox.tpch.TPCH.get_stream_info"></span>

`get_stream_info(stream_id) -> dict` is meant to describe one generated stream. On 0.4.1 it raises `AttributeError: 'TPCHBenchmark' object has no attribute 'get_stream_info'`.

### get_all_streams_info()

<span id="benchbox.tpch.TPCH.get_all_streams_info"></span>

`get_all_streams_info() -> list[dict]` is meant to describe every generated stream. On 0.4.1 it raises `AttributeError: 'TPCHBenchmark' object has no attribute 'get_all_streams_info'`.

### Test runners

#### run_power_test(connection_factory, config=None)

<span id="benchbox.tpch.TPCH.run_power_test"></span>

`run_power_test(connection_factory, config=None)` runs the 22 queries once, in the stream-0 permutation, and returns a `TPCHPowerTestResult`.

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `connection_factory` | open database connection | required | Despite the name, pass a connection object that has `execute()`, such as the one from `adapter.create_connection()`. A factory function is not called: every query then fails with `'function' object has no attribute 'execute'`, and the result has `success=False` and `power_at_size=0.0`. |
| `config` | `dict` or `None` | `None` | Keyword arguments for the power test: `scale_factor`, `seed`, `stream_id`, `dialect`, `verbose`, `timeout`, `warm_up`, `validation`, `validation_mode` (`"exact"`, `"loose"` or `"disabled"`) and `query_subset` (a list of query ids that replaces the 22-query permutation and makes the run non-compliant). `scale_factor` defaults to `1.0`, not to the benchmark's own scale factor, so pass it. |

The result has `start_time`, `end_time`, `total_time` (seconds), `power_at_size`, `queries_executed`, `queries_successful`, `query_results` (a list of dicts with `query_id`, `position`, `stream_id`, `execution_time_seconds`, `success`, `error`, `result_count`), `success`, `errors`, and `to_dict()`.

```python
from benchbox import TPCH
from benchbox.platforms.duckdb import DuckDBAdapter

benchmark = TPCH(scale_factor=0.01, output_dir="tpch_data")
benchmark.generate_data()
adapter = DuckDBAdapter()
conn = adapter.create_connection()
adapter.create_schema(benchmark, conn)
adapter.load_data(benchmark, conn, benchmark.output_dir)

result = benchmark.run_power_test(conn, {"scale_factor": 0.01, "validation": False, "warm_up": False})
print(type(result).__name__, result.success, result.queries_executed, result.queries_successful)
print([r["query_id"] for r in result.query_results][:5])
```

Output on 0.4.1:

```text
TPCHPowerTestResult True 22 22
[14, 2, 9, 20, 6]
```

#### run_maintenance_test(connection_factory, config=None)

<span id="benchbox.tpch.TPCH.run_maintenance_test"></span>

`run_maintenance_test(connection_factory, config=None)` is meant to run the refresh functions RF1 and RF2. On 0.4.1 it raises `AttributeError: 'TPCHMaintenanceTest' object has no attribute 'run'` for every argument.

#### run_official_benchmark(connection_factory, config=None)

<span id="benchbox.tpch.TPCH.run_official_benchmark"></span>

`run_official_benchmark(connection_factory, config=None, *, adapter)` runs the power, throughput and maintenance tests and reports Power@Size and Throughput@Size. On 0.4.1 it raises `TypeError: scale_factor must be a number, got TPCH` for every argument. In later releases it builds the official benchmark from the facade's scale factor, runs the same TPC-H throughput driver as `benchbox run`, and emits a `DeprecationWarning`. The `adapter` argument (a platform adapter) is required whenever the throughput test is enabled: the streams run behind the platform's stream capability gate with one adapter session per stream, and the factory's first connection is the shared connection. Without it the call raises `TypeError`; use `benchbox run --phases throughput` instead. `connection_factory` must return a new connection on each call, because each phase closes its connection. Power@Size is reported only when the power phase finished all 22 queries, Throughput@Size only when every stream succeeded, and a run with fewer than 2 streams is refused.

`TPCH` has no `run_throughput_test` method on 0.4.1.

## Properties

### tables

<span id="benchbox.tpch.TPCH.tables"></span>

`tables` is a `dict` that maps table name to the data file path (a list of paths for chunked tables). It is empty until `generate_data()` has run. It can be assigned.

```python
from pathlib import Path

for table_name, file_path in benchmark.tables.items():
    size_mb = Path(file_path).stat().st_size / 1024 / 1024
    print(f"{table_name}: {size_mb:.2f} MB at {file_path}")
```

### Inherited members

Every other member comes from `BaseBenchmark`. See {doc}`/reference/python-api/base` for what they do. The ids on this page for the inherited members are kept in the table.

| Group | Member | Kind | Notes |
| --- | --- | --- | --- |
| Run and results | <span id="benchbox.tpch.TPCH.cleanup"></span>`cleanup` | method | |
| Run and results | <span id="benchbox.tpch.TPCH.create_enhanced_benchmark_result"></span>`create_enhanced_benchmark_result` | method | |
| Run and results | <span id="benchbox.tpch.TPCH.create_minimal_benchmark_result"></span>`create_minimal_benchmark_result` | method | |
| Run and results | <span id="benchbox.tpch.TPCH.format_results"></span>`format_results` | method | |
| Run and results | <span id="benchbox.tpch.TPCH.run_benchmark"></span>`run_benchmark` | method | |
| Run and results | <span id="benchbox.tpch.TPCH.run_query"></span>`run_query` | method | |
| Run and results | <span id="benchbox.tpch.TPCH.run_with_platform"></span>`run_with_platform` | method | |
| Run and results | <span id="benchbox.tpch.TPCH.setup_database"></span>`setup_database` | method | |
| Run and results | <span id="benchbox.tpch.TPCH.translate_query"></span>`translate_query` | method | |
| Validation | <span id="benchbox.tpch.TPCH.validate_loaded_data"></span>`validate_loaded_data` | method | |
| Validation | <span id="benchbox.tpch.TPCH.validate_manifest"></span>`validate_manifest` | method | |
| Validation | <span id="benchbox.tpch.TPCH.validate_preflight"></span>`validate_preflight` | method | |
| Verbosity and logging | <span id="benchbox.tpch.TPCH.apply_verbosity"></span>`apply_verbosity` | method | |
| Verbosity and logging | <span id="benchbox.tpch.TPCH.log_debug_info"></span>`log_debug_info` | method | |
| Verbosity and logging | <span id="benchbox.tpch.TPCH.log_error_with_debug_info"></span>`log_error_with_debug_info` | method | |
| Verbosity and logging | <span id="benchbox.tpch.TPCH.log_notice"></span>`log_notice` | method | |
| Verbosity and logging | <span id="benchbox.tpch.TPCH.log_operation_complete"></span>`log_operation_complete` | method | |
| Verbosity and logging | <span id="benchbox.tpch.TPCH.log_operation_start"></span>`log_operation_start` | method | |
| Verbosity and logging | <span id="benchbox.tpch.TPCH.log_verbose"></span>`log_verbose` | method | |
| Verbosity and logging | <span id="benchbox.tpch.TPCH.log_version_warning"></span>`log_version_warning` | method | |
| Verbosity and logging | <span id="benchbox.tpch.TPCH.log_very_verbose"></span>`log_very_verbose` | method | |
| Verbosity and logging | <span id="benchbox.tpch.TPCH.logger"></span>`logger` | property | |
| Verbosity and logging | <span id="benchbox.tpch.TPCH.quiet"></span>`quiet` | class attribute | |
| Verbosity and logging | <span id="benchbox.tpch.TPCH.verbose"></span>`verbose` | class attribute | |
| Verbosity and logging | <span id="benchbox.tpch.TPCH.verbose_enabled"></span>`verbose_enabled` | class attribute | |
| Verbosity and logging | <span id="benchbox.tpch.TPCH.verbose_level"></span>`verbose_level` | class attribute | |
| Verbosity and logging | <span id="benchbox.tpch.TPCH.verbosity_settings"></span>`verbosity_settings` | property | |
| Verbosity and logging | <span id="benchbox.tpch.TPCH.very_verbose"></span>`very_verbose` | class attribute | |
| Data and configuration | <span id="benchbox.tpch.TPCH.api_surface"></span>`api_surface` | class attribute | |
| Data and configuration | <span id="benchbox.tpch.TPCH.benchmark_name"></span>`benchmark_name` | property | |
| Data and configuration | <span id="benchbox.tpch.TPCH.csv_delimiter"></span>`csv_delimiter` | property | `None`. |
| Data and configuration | <span id="benchbox.tpch.TPCH.csv_null_marker"></span>`csv_null_marker` | property | `None`. |
| Data and configuration | <span id="benchbox.tpch.TPCH.DATA_SOURCE_BENCHMARK"></span>`DATA_SOURCE_BENCHMARK` | class attribute | `None`: TPC-H generates its own data. |
| Data and configuration | <span id="benchbox.tpch.TPCH.get_csv_loading_config"></span>`get_csv_loading_config` | method | `None`. |
| Data and configuration | <span id="benchbox.tpch.TPCH.get_data_source_benchmark"></span>`get_data_source_benchmark` | method | |
| Data and configuration | <span id="benchbox.tpch.TPCH.output_dir"></span>`output_dir` | property | The resolved directory from the constructor argument. |
| Data and configuration | <span id="benchbox.tpch.TPCH.run_with_platform_api_surface"></span>`run_with_platform_api_surface` | class attribute | |
| Data and configuration | <span id="benchbox.tpch.TPCH.scale_factor"></span>`scale_factor` | instance attribute | The constructor argument. |
| Data and configuration | <span id="benchbox.tpch.TPCH.SKIP_DATA_LOADING"></span>`SKIP_DATA_LOADING` | class attribute | Defined on `BaseBenchmark` from 0.4.2, default `False`. Set it to `True` for a benchmark that needs schema objects but no data files. |

## Usage Examples

### Basic Benchmark Run

Scale factor 1 is about 1 GB. The example generates the data, runs the benchmark on DuckDB and prints the results.

```python
from benchbox import TPCH
from benchbox.platforms.duckdb import DuckDBAdapter

benchmark = TPCH(scale_factor=1.0)

benchmark.generate_data()

adapter = DuckDBAdapter()
results = benchmark.run_with_platform(adapter)

print(f"Benchmark: {results.benchmark_name}")
print(f"Total time: {results.total_execution_time:.2f}s")
print(f"Queries: {results.successful_queries}/{results.total_queries}")
```

### Multi-Platform Comparison

Run the same generated data through several adapters and compare the timings. This example uses two DuckDB configurations; add other adapters to the dictionary the same way.

```python
from benchbox import TPCH
from benchbox.platforms.duckdb import DuckDBAdapter

benchmark = TPCH(scale_factor=0.1, output_dir="./data/tpch_sf01")
benchmark.generate_data()

platforms = {
    "DuckDB 1GB": DuckDBAdapter(memory_limit="1GB"),
    "DuckDB 4GB": DuckDBAdapter(memory_limit="4GB"),
}

for name, adapter in platforms.items():
    results = benchmark.run_with_platform(adapter)
    print(f"{name}: {results.total_execution_time:.2f}s")
```

### Specific Query Execution

The example loads the data, then executes queries 1, 3, 6 and 10.

```python
from benchbox import TPCH
from benchbox.platforms.duckdb import DuckDBAdapter

benchmark = TPCH(scale_factor=0.1)
benchmark.generate_data()

adapter = DuckDBAdapter()
conn = adapter.create_connection()

adapter.create_schema(benchmark, conn)
adapter.load_data(benchmark, conn, benchmark.output_dir)

for query_id in [1, 3, 6, 10]:
    query = benchmark.get_query(query_id)
    result = adapter.execute_query(conn, query, f"q{query_id}")
    print(f"Q{query_id}: {result['execution_time_seconds']:.3f}s")
```

### Scale Factor Comparison

```python
import os
import pandas as pd
from benchbox import TPCH
from benchbox.platforms.duckdb import DuckDBAdapter

scale_factors = [0.01, 0.1, 1.0]
results_data = []

adapter = DuckDBAdapter()

for sf in scale_factors:
    benchmark = TPCH(scale_factor=sf)
    data_files = benchmark.generate_data()

    results = benchmark.run_with_platform(adapter)

    results_data.append({
        "scale_factor": sf,
        "data_size_gb": sum(os.path.getsize(path) for path in data_files) / 1e9,
        "total_time": results.total_execution_time,
        "avg_query_time": results.average_query_time,
    })

df = pd.DataFrame(results_data)
print(df)
```

### Official Benchmark Tests

On 0.4.1 only the power test of the three official tests works through `TPCH`, and it needs a connection object (see the Test runners section). `run_maintenance_test` and `run_official_benchmark` raise, and there is no `run_throughput_test`. The last call below runs the power test.

```python
from benchbox import TPCH
from benchbox.platforms.duckdb import DuckDBAdapter

benchmark = TPCH(scale_factor=0.01, output_dir="tpch_data")
benchmark.generate_data()

adapter = DuckDBAdapter()
conn = adapter.create_connection()
adapter.create_schema(benchmark, conn)
adapter.load_data(benchmark, conn, benchmark.output_dir)

power_results = benchmark.run_power_test(conn, {"scale_factor": 0.01})
print(power_results.queries_successful, f"{power_results.power_at_size:.1f}")
```

## See Also

- {doc}`index` - Benchmark API overview
- {doc}`tpcds` - TPC-DS benchmark API
- {doc}`../base` - Base benchmark interface
- {doc}`../results` - Results API
- {doc}`/benchmarks/tpc-h` - TPC-H guide
- {doc}`/guides/tpc/tpc-h-official-guide` - Official benchmark guide
