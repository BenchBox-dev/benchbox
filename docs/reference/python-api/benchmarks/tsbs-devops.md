# TSBS DevOps API

```{tags} reference, python-api, time-series
```

<!-- markdownlint-disable MD024 -->

Python API reference for the TSBS DevOps benchmark.

## Overview

TSBS DevOps is a time-series benchmark modelled on the Time Series Benchmark Suite. It generates synthetic infrastructure-monitoring data for a fleet of hosts (CPU, memory, disk and network metrics, plus host tags) and serves 18 SQL queries over it, in ten categories.

## `benchbox.TSBSDevOps`

<span id="benchbox.tsbs_devops.TSBSDevOps"></span>

Creates a TSBS DevOps benchmark that generates five monitoring tables and serves 18 queries.

**Import:** `from benchbox import TSBSDevOps` · **Extras:** none

### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `scale_factor` | `float` | `1.0` | Sets the number of hosts: `max(10, int(100 * scale_factor))`, so 0.01 and 0.1 give 10, 1 gives 100 and 10 gives 1,000. Must be positive. Values of 1 or more must be whole numbers. |
| `output_dir` | `str`, `Path` or `None` | `None` | Directory for generated data files. When `None`, the directory is `benchmark_runs/datagen/tsbs_devops_<sf token>` under the current directory (for example `tsbs_devops_sf001` for 0.01), or under `$BENCHBOX_OUTPUT_DIR/datagen` when that variable is set. |
| `num_hosts` | `int` or `None` | `None` | Number of hosts, instead of the value derived from `scale_factor`. `None` and `0` both mean "derive from the scale factor". |
| `duration_days` | `int` or `None` | `None` | Length of the generated series in days. `None` and `0` both give 2 days, at every scale factor. |
| `interval_seconds` | `int` | `10` | Seconds between measurements. |
| `**kwargs` | keyword arguments | none | `seed` (`int`) makes data and query parameters reproducible. `start_time` (`datetime`) sets the first timestamp; the default series starts at `2024-01-01 00:00:00`. `verbose` (`bool` or `int`), `quiet` (`bool`) and `force_regenerate` (`bool`) behave as for the other benchmarks. |

The constructor creates no files. Data is written by `generate_data()`. `num_hosts`, `duration_days` and `interval_seconds` are not range-checked: negative values are accepted, and `interval_seconds=0` fails later with `ZeroDivisionError` in `get_generation_stats()`.

### Returns

A `TSBSDevOps` instance. It subclasses `BaseBenchmark`; see {doc}`/reference/python-api/base` for the shared interface.

### Raises

- `ValueError` when `scale_factor` is zero or negative (`Scale factor must be positive`), or is 1 or more and not a whole number.
- `TypeError` when `scale_factor` is not a number.

### Example

```python
from benchbox import TSBSDevOps

benchmark = TSBSDevOps(
    scale_factor=0.01,
    output_dir="tsbs_data",
    num_hosts=3,
    duration_days=1,
    interval_seconds=600,
    seed=7,
)
files = benchmark.generate_data()
print([str(f) for f in files])
print(benchmark.get_generation_stats()["rows"])
print(benchmark.get_queries_by_category("threshold"))
print(benchmark.get_query("lastpoint").splitlines()[0])
```

Output on 0.4.1:

```text
['tsbs_data/tags.csv', 'tsbs_data/cpu.csv', 'tsbs_data/mem.csv', 'tsbs_data/disk.csv', 'tsbs_data/net.csv']
{'tags': 3, 'cpu': 432, 'mem': 432, 'disk': 864, 'net': 864}
['high-cpu-1-hr', 'high-cpu-12-hr', 'low-memory-hosts', 'net-errors']
SELECT c.hostname, c.time, c.usage_user, c.usage_system, c.usage_idle
```

### Compatibility

`benchbox.tsbs_devops.TSBSDevOps` is the same class.

## Constructor

<span id="benchbox.tsbs_devops.TSBSDevOps.__init__"></span>

`TSBSDevOps(scale_factor=1.0, output_dir=None, num_hosts=None, duration_days=None, interval_seconds=10, **kwargs)`. The arguments are in the Parameters table above.

## Properties

<span id="benchbox.tsbs_devops.TSBSDevOps.num_hosts"></span>
<span id="benchbox.tsbs_devops.TSBSDevOps.duration_days"></span>
<span id="benchbox.tsbs_devops.TSBSDevOps.interval_seconds"></span>
<span id="benchbox.tsbs_devops.TSBSDevOps.tables"></span>

`num_hosts`, `duration_days` and `interval_seconds` return the values in effect after defaults are applied. `tables` is a `dict` that maps table name to data file path; it is empty until `generate_data()` has run.

## Data methods

### generate_data()

<span id="benchbox.tsbs_devops.TSBSDevOps.generate_data"></span>

`generate_data() -> list` writes five comma-separated CSV files with a header row to `output_dir`, plus `_datagen_manifest.json`, and returns the paths in the order `tags`, `cpu`, `mem`, `disk`, `net`.

| Table | Rows |
| --- | --- |
| `tags` | one per host |
| `cpu` | hosts × timestamps |
| `mem` | hosts × timestamps |
| `disk` | 2 × hosts × timestamps |
| `net` | 2 × hosts × timestamps |

The number of timestamps is `duration_days × 86400 / interval_seconds`. With 3 hosts, 1 day and 600 seconds that is 144 timestamps.

### get_generation_stats()

<span id="benchbox.tsbs_devops.TSBSDevOps.get_generation_stats"></span>

`get_generation_stats() -> dict` returns `num_hosts`, `duration_days`, `interval_seconds`, `num_timestamps`, `rows` (a table-to-count dict) and `total_rows`. It works before `generate_data()` because it computes the counts from the configuration; the example above gives a `total_rows` of 2,595.

### get_schema()

<span id="benchbox.tsbs_devops.TSBSDevOps.get_schema"></span>

`get_schema() -> dict` returns a mapping from table name to a definition with `description` and `columns`. The keys, in order, are `tags`, `cpu`, `mem`, `disk` and `net`. Unlike most benchmarks, `columns` is a dict of column name to `{"type": ..., "description": ...}`, not a list.

### get_create_tables_sql(dialect="standard", tuning_config=None)

<span id="benchbox.tsbs_devops.TSBSDevOps.get_create_tables_sql"></span>

`get_create_tables_sql(dialect="standard", tuning_config=None) -> str` returns the `CREATE TABLE` statements for the five tables. `standard`, `duckdb`, `clickhouse` and `timescale` each return their own script; any other name returns the `standard` script.

### get_benchmark_info()

<span id="benchbox.tsbs_devops.TSBSDevOps.get_benchmark_info"></span>

`get_benchmark_info() -> dict` returns `name`, `description`, `reference`, `version`, `scale_factor`, `num_hosts`, `duration_days`, `interval_seconds`, `num_queries` (18), `query_categories`, `tables` and `metrics`.

## Query methods

### get_queries(dialect=None)

<span id="benchbox.tsbs_devops.TSBSDevOps.get_queries"></span>

`get_queries(dialect=None) -> dict[str, str]` returns all 18 queries keyed by id. `dialect` is accepted and ignored: the SQL is the same for every value.

The ids and their categories:

| Category | Query ids |
| --- | --- |
| `single-host` | `single-host-12-hr`, `single-host-1-hr` |
| `aggregation` | `cpu-max-all-1-hr`, `cpu-max-all-8-hr` |
| `groupby` | `double-groupby-1-hr`, `double-groupby-5-min` |
| `threshold` | `high-cpu-1-hr`, `high-cpu-12-hr`, `low-memory-hosts`, `net-errors` |
| `memory` | `mem-by-host-1-hr` |
| `disk` | `disk-iops-1-hr`, `disk-latency` |
| `network` | `net-throughput-1-hr` |
| `combined` | `resource-utilization` |
| `lastpoint` | `lastpoint` |
| `tags` | `by-region`, `by-service` |

Host names and time windows in the SQL are chosen at random from the configured hosts and time range. Each call to `get_query()` or `get_queries()` can return different text for the same id. With a `seed`, two instances built with the same arguments return the same sequence of texts.

### get_query(query_id, \*, params=None)

<span id="benchbox.tsbs_devops.TSBSDevOps.get_query"></span>

`get_query(query_id, *, params=None, **kwargs) -> str` returns one query as SQL text.

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `query_id` | `str` | required | One of the ids above. Integers are rejected, although the signature allows `int`. |
| `params` | `dict` or `None` | `None` | Replaces the random choice. `hostname`, `start_time` and `end_time` are honoured by `single-host-1-hr`; for example `params={"hostname": "host_0"}` fixes the host. |

Raises `ValueError` for an unknown id: `Unknown query: nope. Available: ['single-host-12-hr', ...]`.

### get_query_info(query_id)

<span id="benchbox.tsbs_devops.TSBSDevOps.get_query_info"></span>

`get_query_info(query_id) -> dict` returns `id`, `name`, `description`, `category`, `sql` (the template, with `{hostname}`-style placeholders) and `params` (such as `{'duration_hours': 12}`). Raises `ValueError` for an unknown id.

### get_queries_by_category(category)

<span id="benchbox.tsbs_devops.TSBSDevOps.get_queries_by_category"></span>

`get_queries_by_category(category) -> list[str]` returns the query ids in one category. An unknown category returns `[]`.

## Running the queries

All 18 queries run on DuckDB against the generated data.

```python
from benchbox.platforms.duckdb import DuckDBAdapter

adapter = DuckDBAdapter(database=":memory:")
conn = adapter.create_connection()
adapter.create_schema(benchmark, conn)
adapter.load_data(benchmark, conn, benchmark.output_dir)
for query_id, sql in benchmark.get_queries().items():
    conn.execute(sql).fetchall()
print(len(conn.execute(benchmark.get_query("lastpoint")).fetchall()))
```

Output on 0.4.1, after the adapter's progress lines:

```text
3
```

## Inherited members

Every other member comes from `BaseBenchmark`: platform runs (`run_with_platform`), validation, logging, `output_dir`, `scale_factor` and the loading configuration. See {doc}`/reference/python-api/base`.
