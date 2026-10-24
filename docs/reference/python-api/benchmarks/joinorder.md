# Join Order Benchmark API

```{tags} reference, python-api, custom-benchmark
```

Python API reference for BenchBox's public Join Order Benchmark implementation.

## Overview

The public `joinorder` benchmark uses the canonical IMDb 2013 dataset and query set from the Join Order Benchmark (JOB) paper, "How Good Are Query Optimizers, Really?" by Leis et al. It is intended for cardinality estimation and join-order optimization testing on real-world correlated data.

Current contract:

- `scale_factor` must be `1.0`. Other values raise `ValueError`.
- Data comes from the versioned `joinorder-imdb-2013-v1` Parquet package.
- The package contains 21 IMDb-derived tables and 74,190,187 rows.
- The first run downloads and verifies the archive (about 860 MB), then verifies table hashes and row counts from the package manifest. The extracted Parquet files take about 0.9 GB more.
- `JoinOrderQueryManager` exposes all 113 canonical JOB SQL queries.
- The old scalable synthetic generator is now the internal `joinorder_synthetic` benchmark for loader and schema smoke tests.

## Quick Start

CLI:

```bash
uv run -- benchbox run --platform duckdb --benchmark joinorder --scale 1
```

Python:

```python
from benchbox import JoinOrder

benchmark = JoinOrder(scale_factor=1.0)
data_files = benchmark.generate_data()
ddl = benchmark.get_create_tables_sql(dialect="duckdb")
query_1a = benchmark.get_query("1a")

print(len(data_files))
print(query_1a)
```

First-run data is cached under `benchmark_runs/datagen/joinorder_sf1/` by default. If `BENCHBOX_OUTPUT_DIR` is set, the same relative path is resolved under that root.

## JoinOrder Class

### `benchbox.JoinOrder`

<span id="benchbox.joinorder.JoinOrder"></span>

Creates a Join Order Benchmark instance that fetches the canonical IMDb 2013 Parquet package and serves the 113 JOB queries.

**Import:** `from benchbox import JoinOrder` · **Extras:** none

#### Parameters

<span id="benchbox.joinorder.JoinOrder.__init__"></span>

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `scale_factor` | `float` | `1.0` | Must be `1.0`. Any other value, including `0.5` and `2`, raises `ValueError`. |
| `output_dir` | `str`, `Path` or `None` | `None` | Directory for the downloaded archive and the verified Parquet files. When `None`, it is `benchmark_runs/datagen/joinorder_sf1` under the current directory, or under `$BENCHBOX_OUTPUT_DIR/datagen` when that variable is set. |
| `queries_dir` | `str` or `None` | `None` | Directory of custom `*.sql` query files, one query per file, with the file stem as the query id. When it exists, only these files are served (empty files are skipped). When it is `None` or does not exist, the 113 embedded queries are used and no error is raised. |
| `parallel` | `int` | `1` | Accepted for interface compatibility; it does not change how the data is fetched. Must be a positive integer, otherwise `ValueError`. |
| `force_regenerate` | `bool` | `False` | When true, `generate_data()` first deletes the cached Parquet files and archive, then downloads them again. |
| `verbose` | `bool` or `int` | `0` | Log level. |
| `quiet` | `bool` | `False` | Suppress log output. |
| `**kwargs` | keyword arguments | none | Any other keyword is stored as an attribute on the instance and otherwise ignored. |

The constructor does not download anything. Data is fetched by `generate_data()`.

#### Returns

A `JoinOrder` instance. It subclasses `BaseBenchmark`; see {doc}`/reference/python-api/base` for the shared interface.

#### Raises

`ValueError` when `scale_factor` is not `1.0` (the message points to `joinorder_synthetic` for scaled data) or when `parallel` is not a positive integer.

#### Example

```python
from benchbox import JoinOrder

benchmark = JoinOrder(scale_factor=1)
print(benchmark.output_dir)
print(len(benchmark.get_queries()))
try:
    JoinOrder(scale_factor=0.5)
except ValueError as error:
    print(error)
```

Output on 0.4.1:

```text
benchmark_runs/datagen/joinorder_sf1
113
joinorder now uses canonical IMDb 2013 data and accepts only scale_factor=1.0; use joinorder_synthetic for scaled synthetic smoke-test data.
```

The first line is an absolute path under the current directory.

#### Compatibility

`benchbox.joinorder.JoinOrder` is the same class. Before this contract the benchmark accepted scale factors other than 1.0 and generated synthetic data; that generator is now `joinorder_synthetic`.

### Constructor

The arguments are in the Parameters table above.

## Data Methods

### `generate_data() -> list[Path]`

<span id="benchbox.joinorder.JoinOrder.generate_data"></span>

Ensures the canonical Parquet data package is present and verified. It downloads the archive on first use, extracts it into `output_dir`, and checks the SHA-256 hashes and row counts of every table. A second call finds the verified files and downloads nothing.

Returns a list of 21 `pathlib.Path` objects in alphabetical table order (`aka_name.parquet` first, `title.parquet` last). `benchmark.tables` maps each table name to the same path. The directory also holds the archive, `checksums.txt`, `data_manifest.toml`, `DATA-LICENSE.md` and `reference_cardinalities.json`.

Needs network access to GitHub releases on the first call.

```python
from benchbox import JoinOrder

benchmark = JoinOrder(scale_factor=1.0)
data_files = benchmark.generate_data()

assert len(data_files) == 21
assert all(path.suffix == ".parquet" for path in data_files)
```

## Schema Methods

### `get_create_tables_sql(dialect="standard", tuning_config=None) -> str`

<span id="benchbox.joinorder.JoinOrder.get_create_tables_sql"></span>

Returns DDL for the 21-table JOB schema, with `PRIMARY KEY` on every `id` column.

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `dialect` | `str` | `"standard"` | Accepted but has no effect: `standard`, `sqlite` and `duckdb` return the same text. |
| `tuning_config` | `UnifiedTuningConfiguration` or `None` | `None` | Accepted but ignored. |

```python
benchmark = JoinOrder(scale_factor=1.0)
ddl = benchmark.get_create_tables_sql(dialect="duckdb")
print(ddl.count("CREATE TABLE"))
```

This prints `21`, the number of tables.

### `get_schema(dialect="sqlite") -> str`

<span id="benchbox.joinorder.JoinOrder.get_schema"></span>

Convenience wrapper that returns the same DDL string as `get_create_tables_sql()`. Unlike other benchmarks it returns a `str`, not a table description.

```python
benchmark = JoinOrder(scale_factor=1.0)
sqlite_ddl = benchmark.get_schema(dialect="sqlite")
print(sqlite_ddl == benchmark.get_create_tables_sql())
```

This prints `True`.

## Query Methods

### `get_query(query_id, *, params=None, dialect=None) -> str`

<span id="benchbox.joinorder.JoinOrder.get_query"></span>

Returns a static JOB query by ID, such as `"1a"` or `"33c"`.

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `query_id` | `str` | required | One of the 113 ids, `"1a"` to `"33c"`. Integers such as `1` are rejected. |
| `params` | `None` | `None` | JOB queries are fixed. Any non-`None` value, even `{}`, raises `ValueError`. |
| `dialect` | `str` or `None` | `None` | Target dialect for SQLGlot translation from PostgreSQL SQL. Without it the canonical text is returned unchanged. An unknown dialect raises `UnknownSqlDialectError`. |

Raises `ValueError` for an unknown id (`Invalid query ID: 34a. Available: 10a, 10b, ...`) and for non-`None` `params` (`JoinOrder queries are static and don't accept parameters`).

```python
benchmark = JoinOrder(scale_factor=1.0)
query = benchmark.get_query("1a")
duckdb_query = benchmark.get_query("1a", dialect="duckdb")
```

The DuckDB form quotes every identifier and puts the query on one line, starting `SELECT MIN("mc"."note") AS "production_note", ...`.

### `get_queries(dialect=None) -> dict[str, str]`

<span id="benchbox.joinorder.JoinOrder.get_queries"></span>

Returns all queries (113 unless `queries_dir` replaced them), translated via SQLGlot when `dialect` is given.

```python
benchmark = JoinOrder(scale_factor=1.0)
queries = benchmark.get_queries()
spark_queries = benchmark.get_queries(dialect="spark")

assert len(queries) == 113
assert "1a" in queries
assert "33c" in queries
```

### Inherited members

Every other member comes from `BaseBenchmark`. See {doc}`/reference/python-api/base` for what they do. The ids on this page for the inherited members are kept in the table.

| Group | Member | Kind | Notes |
| --- | --- | --- | --- |
| Run and results | <span id="benchbox.joinorder.JoinOrder.cleanup"></span>`cleanup` | method | |
| Run and results | <span id="benchbox.joinorder.JoinOrder.create_enhanced_benchmark_result"></span>`create_enhanced_benchmark_result` | method | |
| Run and results | <span id="benchbox.joinorder.JoinOrder.create_minimal_benchmark_result"></span>`create_minimal_benchmark_result` | method | |
| Run and results | <span id="benchbox.joinorder.JoinOrder.format_results"></span>`format_results` | method | |
| Run and results | <span id="benchbox.joinorder.JoinOrder.run_benchmark"></span>`run_benchmark` | method | |
| Run and results | <span id="benchbox.joinorder.JoinOrder.run_query"></span>`run_query` | method | |
| Run and results | <span id="benchbox.joinorder.JoinOrder.run_with_platform"></span>`run_with_platform` | method | |
| Run and results | <span id="benchbox.joinorder.JoinOrder.setup_database"></span>`setup_database` | method | |
| Run and results | <span id="benchbox.joinorder.JoinOrder.translate_query"></span>`translate_query` | method | |
| Validation | <span id="benchbox.joinorder.JoinOrder.validate_loaded_data"></span>`validate_loaded_data` | method | |
| Validation | <span id="benchbox.joinorder.JoinOrder.validate_manifest"></span>`validate_manifest` | method | |
| Validation | <span id="benchbox.joinorder.JoinOrder.validate_preflight"></span>`validate_preflight` | method | |
| Verbosity and logging | <span id="benchbox.joinorder.JoinOrder.apply_verbosity"></span>`apply_verbosity` | method | |
| Verbosity and logging | <span id="benchbox.joinorder.JoinOrder.log_debug_info"></span>`log_debug_info` | method | |
| Verbosity and logging | <span id="benchbox.joinorder.JoinOrder.log_error_with_debug_info"></span>`log_error_with_debug_info` | method | |
| Verbosity and logging | <span id="benchbox.joinorder.JoinOrder.log_notice"></span>`log_notice` | method | |
| Verbosity and logging | <span id="benchbox.joinorder.JoinOrder.log_operation_complete"></span>`log_operation_complete` | method | |
| Verbosity and logging | <span id="benchbox.joinorder.JoinOrder.log_operation_start"></span>`log_operation_start` | method | |
| Verbosity and logging | <span id="benchbox.joinorder.JoinOrder.log_verbose"></span>`log_verbose` | method | |
| Verbosity and logging | <span id="benchbox.joinorder.JoinOrder.log_version_warning"></span>`log_version_warning` | method | |
| Verbosity and logging | <span id="benchbox.joinorder.JoinOrder.log_very_verbose"></span>`log_very_verbose` | method | |
| Verbosity and logging | <span id="benchbox.joinorder.JoinOrder.logger"></span>`logger` | property | |
| Verbosity and logging | <span id="benchbox.joinorder.JoinOrder.quiet"></span>`quiet` | class attribute | |
| Verbosity and logging | <span id="benchbox.joinorder.JoinOrder.verbose"></span>`verbose` | class attribute | |
| Verbosity and logging | <span id="benchbox.joinorder.JoinOrder.verbose_enabled"></span>`verbose_enabled` | class attribute | |
| Verbosity and logging | <span id="benchbox.joinorder.JoinOrder.verbose_level"></span>`verbose_level` | class attribute | |
| Verbosity and logging | <span id="benchbox.joinorder.JoinOrder.verbosity_settings"></span>`verbosity_settings` | property | |
| Verbosity and logging | <span id="benchbox.joinorder.JoinOrder.very_verbose"></span>`very_verbose` | class attribute | |
| Data and configuration | <span id="benchbox.joinorder.JoinOrder.api_surface"></span>`api_surface` | class attribute | |
| Data and configuration | <span id="benchbox.joinorder.JoinOrder.benchmark_name"></span>`benchmark_name` | property | |
| Data and configuration | <span id="benchbox.joinorder.JoinOrder.csv_delimiter"></span>`csv_delimiter` | property | `None`. |
| Data and configuration | <span id="benchbox.joinorder.JoinOrder.csv_null_marker"></span>`csv_null_marker` | property | `None`. |
| Data and configuration | <span id="benchbox.joinorder.JoinOrder.DATA_SOURCE_BENCHMARK"></span>`DATA_SOURCE_BENCHMARK` | class attribute | `None`: the data comes from the JoinOrder package. |
| Data and configuration | <span id="benchbox.joinorder.JoinOrder.get_csv_loading_config"></span>`get_csv_loading_config` | method | `None`: the data is Parquet. |
| Data and configuration | <span id="benchbox.joinorder.JoinOrder.get_data_source_benchmark"></span>`get_data_source_benchmark` | method | |
| Data and configuration | <span id="benchbox.joinorder.JoinOrder.output_dir"></span>`output_dir` | property | The resolved directory from the constructor argument. |
| Data and configuration | <span id="benchbox.joinorder.JoinOrder.run_with_platform_api_surface"></span>`run_with_platform_api_surface` | class attribute | |
| Data and configuration | <span id="benchbox.joinorder.JoinOrder.scale_factor"></span>`scale_factor` | instance attribute | Always `1.0`. |
| Data and configuration | <span id="benchbox.joinorder.JoinOrder.SKIP_DATA_LOADING"></span>`SKIP_DATA_LOADING` | class attribute | Defined on `BaseBenchmark` from 0.4.2, default `False`. Set it to `True` for a benchmark that needs schema objects but no data files. |
| Data and configuration | <span id="benchbox.joinorder.JoinOrder.tables"></span>`tables` | property | Empty until `generate_data()` has run, then the table-to-path mapping. |

## JoinOrderQueryManager

`JoinOrderQueryManager` is not one of the listed public symbols, so its import path is not part of the contract. Use it directly when you only need the SQL catalog.

```python
from benchbox.core.joinorder.queries import JoinOrderQueryManager

manager = JoinOrderQueryManager()
assert manager.get_query_count() == 113
query_ids = manager.get_query_ids()
query_1a = manager.get_query("1a")
```

The manager also supports an optional query directory, with the same rules as the `queries_dir` argument above:

```python
manager = JoinOrderQueryManager("/path/to/job/queries")
custom_queries = manager.get_all_queries()
```

## Data Provenance

BenchBox's `joinorder-imdb-2013-v1` package is derived from the Harvard Dataverse `imdb_pg11` archive, DOI `10.7910/DVN/2QYZBT`. The source represents the May 2013 IMDb list-file snapshot parsed with IMDbPY into the 21-table relational schema used by the JOB paper, restored into PostgreSQL, and converted to Parquet for repeatable BenchBox execution.

Dataset provenance and redistribution notes are in `benchbox/core/joinorder/DATA-LICENSE.md` and are copied next to the data as `DATA-LICENSE.md`.

## References

- Benchmark guide: {doc}`/benchmarks/join-order`
- JOB paper: <http://www.vldb.org/pvldb/vol9/p204-leis.pdf>
- Query corpus: <https://github.com/gregrahn/join-order-benchmark>
- Dataset DOI: <https://doi.org/10.7910/DVN/2QYZBT>
