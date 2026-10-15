<!-- markdownlint-disable MD024 -->

# ClickHouse Platform Adapter

```{tags} reference, python-api, clickhouse
```

The ClickHouse adapter provides high-performance columnar database execution for analytical benchmarks.

## Overview

ClickHouse is an open-source column-oriented database management system that provides:

- **Columnar architecture** - Optimized for analytical queries
- **Scalability** - Support for petabyte-scale datasets
- **Flexible deployment** - Server mode or embedded local mode
- **Compression** - Columnar compression for storage efficiency
- **OLAP focus** - Designed for analytical workloads

The ClickHouse adapter supports two modes:

- **Server mode** - Connect to ClickHouse server (local or remote). Needs the `clickhouse` extra (`clickhouse-driver`).
- **Local mode** - Embedded execution using chDB library. Needs the `clickhouse-local` extra (`chdb`). This is the default.

ClickHouse Cloud is a separate platform (`clickhouse-cloud`) and is not a mode of this adapter.

Common use cases:

- Analytical workloads
- Large-scale benchmarking (100GB+)
- Performance comparison with other columnar databases
- Real-time analytics applications

## Quick Start

### Server Mode (Default)

In 0.4.1 `ClickHouseAdapter()` starts in local mode. Server mode is selected with `deployment_mode="server"`; without it the `host`, `port` and credentials below are ignored.

```python
from benchbox.tpch import TPCH
from benchbox.platforms.clickhouse import ClickHouseAdapter

adapter = ClickHouseAdapter(
    deployment_mode="server",
    host="localhost",
    port=9000,
    database="benchmark",
    username="default",
    password=""
)

benchmark = TPCH(scale_factor=1.0)
benchmark.generate_data()
results = benchmark.run_with_platform(adapter)
```

### Local Mode (Embedded)

This example runs embedded ClickHouse with chDB. `database_path` is optional and sets persistent storage.

```python
from benchbox.tpch import TPCH
from benchbox.platforms.clickhouse import ClickHouseAdapter

adapter = ClickHouseAdapter(
    deployment_mode="local",
    database_path="./benchmark.chdb"
)

benchmark = TPCH(scale_factor=0.1)
benchmark.generate_data()
results = benchmark.run_with_platform(adapter)
```

## API Reference

### ClickHouseAdapter Class

<span id="benchbox.platforms.clickhouse.ClickHouseAdapter"></span>

`benchbox.platforms.clickhouse.ClickHouseAdapter` runs BenchBox benchmarks on ClickHouse, either embedded (chDB, the default) or against a ClickHouse server.

**Import:** `from benchbox.platforms.clickhouse import ClickHouseAdapter` · **Extras:** `clickhouse-local` (local mode, installs `chdb`) or `clickhouse` (server mode, installs `clickhouse-driver`)

#### Parameters

All parameters are keyword arguments (the signature is `(**config)`).

Mode selection:

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `deployment_mode` | `str` | `"local"` | `"local"` or `"server"`. `"embedded"` means `"local"`. |
| `mode` | `str` | none | Older name for `deployment_mode`, read when `deployment_mode` is absent. |
| `embedded` | `bool` | none | Older flag: `True` means `"local"`, `False` means `"server"`. Read when neither string key is given. |

Both modes:

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `max_memory_usage` | `str` | `"8GB"` | ClickHouse `max_memory_usage` for the session: a number with an upper-case `KB`, `MB` or `GB` suffix, or a plain byte count. `"2G"` and `"2gb"` are not understood; see `configure_for_benchmark`. |
| `max_execution_time` | `int` | `300` | ClickHouse `max_execution_time`, in seconds. |
| `max_threads` | `int` | `8` in server mode, `4` in local mode | ClickHouse `max_threads`. |
| `disable_result_cache` | `bool` | `True` | Turns the query cache off for accurate timing. |
| `strict_validation` | `bool` | `True` | Raise `ConfigurationError` if the cache-control settings cannot be confirmed. |
| `optimize_after_load` | `bool` | `False` | Run `OPTIMIZE TABLE ... FINAL` on each table after it loads. Only when `tuning_enabled` is true. The time is reported as `phases.post_load_maintenance`, not as load time. |

Server mode:

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `host` | `str` | `"localhost"` | Server host name. |
| `port` | `int` | `9000` | Native-protocol port (use `9440` with `secure=True`). |
| `database` | `str` | `"default"` | Database name. It is created if it does not exist. |
| `username` | `str` | `"default"` | User name. `user` is read when `username` is absent. |
| `password` | `str` | `""` | Password. |
| `secure` | `bool` | `False` | Use TLS. |
| `compression` | `bool` | `False` | Compress client traffic. |
| `insert_block_size` | `int` | `65536` | Rows per insert block. Must be a positive integer other than `1000`. |
| `send_receive_timeout` | `int` | `300` | Socket timeout in seconds. |

Local mode:

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `database_path` | `str` or `None` | `None` | Directory for persistent chDB storage; `.chdb` is appended if the name does not end with it. `None` keeps the data in memory. |
| `data_path` | `str` or `None` | `None` | Recorded and reported by `get_platform_info`; it does not select where data is stored. |
| `force_recreate` | `bool` | `False` | Delete an existing database directory when a connection is created. |

The adapter also accepts the keys every BenchBox adapter takes: see [Constructor Parameters](#constructor-parameters). Keys it does not recognise are accepted and ignored. Server-mode keys passed in local mode are accepted and not used; in local mode `host`, `port`, `database`, `username`, `password`, `secure` and `compression` read back as `None`.

#### Returns

A `ClickHouseAdapter`. Construction opens no connection; call `create_connection()`.

#### Raises

- `ValueError`: the mode is not `local` or `server` (`Invalid ClickHouse deployment mode 'bogus'. Valid modes: local, server`); the mode is `cloud` (`ClickHouse Cloud is now a separate first-class platform.`, use the `clickhouse-cloud` platform); or `insert_block_size` is not an integer (`insert_block_size must be an integer`) or is not positive or equals 1000 (`insert_block_size must be a positive integer other than 1000`).
- `ImportError`: local mode and `chdb` is not installed (`ClickHouse local mode requires chDB but it is not installed.`), or server mode and `clickhouse-driver` is not installed.

Unreachable servers and wrong credentials are not detected here; they fail in `create_connection()`.

#### Example

```python
from benchbox.platforms.clickhouse import ClickHouseAdapter

adapter = ClickHouseAdapter()
print(adapter.deployment_mode, adapter.platform_name, adapter.max_memory_usage, adapter.max_threads)
connection = adapter.create_connection()
adapter.configure_for_benchmark(connection, "tpch")
result = adapter.execute_query(connection, "SELECT 42 AS answer", "q1")
print(result["status"], result["rows_returned"], result["first_row"])
print(repr(adapter.get_query_plan(connection, "SELECT 1")))
print(ClickHouseAdapter.KNOWN_INCOMPATIBLE_QUERIES)

server = ClickHouseAdapter(deployment_mode="server", host="db.example.com", port=9440, secure=True)
print(server.deployment_mode, server.host, server.port, server.database, server.max_threads)

for kwargs in ({"deployment_mode": "bogus"}, {"deployment_mode": "cloud"}, {"deployment_mode": "server", "insert_block_size": 1000}):
    try:
        ClickHouseAdapter(**kwargs)
    except ValueError as exc:
        print(str(exc).splitlines()[0])
```

Output on 0.4.1 with `chdb` 4.4.0 (the server adapter is only constructed, so no server is needed):

```text
local ClickHouse (Local) 8GB 4
SUCCESS 1 (42,)
'Output: 1\n\nReadFromSystemOne'
{'tpcds': [14, 30, 81]}
server db.example.com 9440 default 8
Invalid ClickHouse deployment mode 'bogus'. Valid modes: local, server
ClickHouse Cloud is now a separate first-class platform.
insert_block_size must be a positive integer other than 1000
```

#### Compatibility

- `benchbox.platforms.ClickHouseAdapter` is the same class.
- The default mode is local, not server. In local mode `data_path` does not select persistent storage; `database_path` does.
- Local mode keeps one chDB session per process. After a connection has opened one storage path (or memory), opening a different path in the same process fails with `EmbeddedServer already initialized with path ...`.
- The `clickhouse:local`, `clickhouse:server` and `clickhouse:cloud` platform selectors are deprecated in favour of the platforms `clickhouse-local`, `clickhouse-server` and `clickhouse-cloud`.
- `from_config` reads only `deployment_mode` (or `mode`/`embedded`), `data_path` (default `/tmp/benchbox_ch_local`), `database_path`, `host`, `port`, `username`, `user`, `password`, `secure`, `compression`, `optimize_after_load` and the tuning and verbosity keys. It drops `max_memory_usage`, `max_execution_time`, `max_threads`, `database` and `force`. In local mode it also builds `database_path` as `benchmark_runs/databases/<benchmark>_sf<token>/<benchmark>_sf<token>_notuning_noconstraints.chdb` from `benchmark` and `scale_factor`.

### Constructor Parameters

Every platform adapter accepts these keyword arguments. All are optional.

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `force_recreate` | `bool` | `False` | Recreate an existing database instead of reusing it. |
| `show_query_plans` | `bool` | `False` | Print each query's plan after it runs. |
| `capture_plans` | `bool` | `False` | Add `query_plan` and `plan_fingerprint` to each successful query result. |
| `analyze_plans` | `bool` | `False` | Capture plans with actual timings; each captured `SELECT` is executed once more. |
| `tuning_enabled` | `bool` | `False` | Apply the tuning configuration given in `tuning_config`. |
| `tuning_config` | `UnifiedTuningConfiguration` or `None` | `None` | The tuning configuration (`unified_tuning_configuration` is read as a fallback). |
| `enable_validation` | `bool` | `False` | The benchmark runner passes `validate_row_count=True` to `execute_query`. |
| `dry_run` | `bool` | `False` | Makes `is_dry_run` true. |
| `verbose_enabled`, `very_verbose`, `quiet` | `bool` | `False` | Logging verbosity. `verbose_level` (0, 1 or 2) sets the first two. |

The values are stored as attributes of the same name (`ClickHouseAdapter().force_recreate` is `False`).

### Methods and attributes

Only the members below are defined on `ClickHouseAdapter` itself. Its connection, schema, loading and query methods come from shared mixins with the signatures listed next.

| Method | Returns |
| --- | --- |
| `create_connection(**connection_config)` | A connection: `ClickHouseLocalClient` in local mode (its `execute` returns a list-like of row tuples), a `clickhouse_driver.Client` in server mode. |
| `close_connection(connection)` | `None`. |
| `create_schema(benchmark, connection)` | Seconds as a `float`. |
| `load_data(benchmark, connection, data_dir)` | `(table_row_counts, seconds, None)`; `table_row_counts` maps lower-case table names to row counts. |
| `execute_query(connection, query, query_id, benchmark_type=None, scale_factor=None, validate_row_count=True, stream_id=None)` | A result `dict` with `status` (`'SUCCESS'` or `'FAILED'`), `query_id`, `execution_time_seconds`, `rows_returned` and `first_row`; SQL errors are returned as `status='FAILED'` with `error` and `error_type`, not raised. |
| `get_table_row_count(connection, table)` | The number of rows as an `int`. |
| `configure_for_benchmark(connection, benchmark_type)` | `None`. Applies `max_memory_usage`, `max_execution_time`, `max_threads`, `join_use_nulls = 1` and the query-cache settings to the session. A `max_memory_usage` such as `"2G"` that the adapter cannot parse raises `ValueError` here. |
| `get_platform_info(connection=None)` | A `dict` with `platform_type`, `platform_name` (`'ClickHouse (Local)'` or `'ClickHouse (Server)'`), `connection_mode`, `configuration` and `client_library_version`. |
| `from_config(config)` | A `ClickHouseAdapter`; see Compatibility above. |

These results are for local mode. In server mode the same methods connect to the server and use `SHOW DATABASES` and `DROP DATABASE IF EXISTS` to check for and remove the database.

#### Construction and class attributes

<span id="benchbox.platforms.clickhouse.ClickHouseAdapter.__init__"></span>
**`__init__(**config)`**: Creates the adapter from keyword arguments. See Parameters above. It stores the settings for the chosen mode and opens no connection.

<span id="benchbox.platforms.clickhouse.ClickHouseAdapter.KNOWN_INCOMPATIBLE_QUERIES"></span>
**`KNOWN_INCOMPATIBLE_QUERIES`** (class attribute): `{'tpcds': [14, 30, 81]}`: TPC-DS query numbers that have failed on some ClickHouse versions even after BenchBox's query rewrites (query 14 needs an alias for `INTERSECT DISTINCT`; queries 30 and 81 hit `Code: 48`, query-plan cloning for aggregation steps). The attribute is data only and nothing in the adapter skips these queries. With chDB 4.4.0, all three run with status `SUCCESS` at scale factor 0.01, so the list describes older engine versions.

<span id="benchbox.platforms.clickhouse.ClickHouseAdapter.driver_isolation_capability"></span>
**`driver_isolation_capability`** (class attribute): `DriverIsolationCapability.NOT_FEASIBLE` (from `benchbox.platforms.base`): a requested ClickHouse driver version cannot be run in an isolated runtime.

<span id="benchbox.platforms.clickhouse.ClickHouseAdapter.plan_capture_phase_eligible"></span>
**`plan_capture_phase_eligible`** (class attribute): `True`. Query plans for ClickHouse are captured in a separate pass after the timed run, not inline with the timed queries.

#### Query plans and tuning evidence

<span id="benchbox.platforms.clickhouse.ClickHouseAdapter.get_query_plan"></span>
**`get_query_plan(connection, query: str) -> str | None`**: Returns the logical plan as text from `EXPLAIN PLAN <query>`, with the result rows joined by newlines, or `None` if the statement fails or returns nothing (for a missing table, or a server too old to support `EXPLAIN`). `EXPLAIN PLAN` is used for every mode. For `SELECT 1` in local mode the value is `'Output: 1\n\nReadFromSystemOne'`.

<span id="benchbox.platforms.clickhouse.ClickHouseAdapter.get_query_plan_parser"></span>
**`get_query_plan_parser()`**: Returns a `ClickHouseQueryPlanParser` (from `benchbox.core.query_plans.parsers.clickhouse`) for the text that `get_query_plan` returns.

<span id="benchbox.platforms.clickhouse.ClickHouseAdapter.get_tuning_introspector"></span>
**`get_tuning_introspector()`**: Returns a `ClickHouseTuningIntrospector` (from `benchbox.platforms.clickhouse.introspection`), which reads the `sorting_key` and `partition_key` of `system.tables` to confirm that the tuning clauses recorded for a run were applied.

#### Diagnostics

These methods come from `ClickHouseDiagnosticsMixin`. Both `ClickHouseAdapter.<name>` and `ClickHouseDiagnosticsMixin.<name>` resolve to the same method.

<span id="benchbox.platforms.clickhouse.diagnostics.ClickHouseDiagnosticsMixin.check_server_database_exists"></span>
**`check_server_database_exists(**connection_config) -> bool`**: Tells whether the database exists. In local mode it returns whether the path from `get_database_path` exists on disk, and `False` when there is no path. In server mode it connects with an administrative client and looks for the database name in `SHOW DATABASES`. The `database` key of `connection_config` overrides the adapter's database. Any failure returns `False`.

<span id="benchbox.platforms.clickhouse.diagnostics.ClickHouseDiagnosticsMixin.drop_database"></span>
**`drop_database(**connection_config) -> None`**: Drops the database on a ClickHouse server with `DROP DATABASE IF EXISTS`. In local mode it does nothing. The `database` key of `connection_config` overrides the adapter's database. A failure raises `RuntimeError` (`Failed to drop ClickHouse database: ...`).

<span id="benchbox.platforms.clickhouse.ClickHouseAdapter.get_table_info"></span>
<span id="benchbox.platforms.clickhouse.diagnostics.ClickHouseDiagnosticsMixin.get_table_info"></span>
**`get_table_info(connection, table_name: str) -> dict[str, Any]`**: Returns detailed information about a table in the current database: `columns` (a list of `(name, type)` pairs in column order), `row_count`, `bytes_on_disk` and `compressed_size`. The last three come from `system.parts`. A failure does not raise: the result is `{'error': <message>}`.

<span id="benchbox.platforms.clickhouse.ClickHouseAdapter.optimize_table"></span>
<span id="benchbox.platforms.clickhouse.diagnostics.ClickHouseDiagnosticsMixin.optimize_table"></span>
**`optimize_table(connection, table_name: str) -> None`**: Runs `OPTIMIZE TABLE <table_name> FINAL` to merge the table's parts for better query performance. A failure is logged as a warning and does not raise.

#### Metadata and command-line options

These methods come from `ClickHouseMetadataMixin`.

<span id="benchbox.platforms.clickhouse.metadata.ClickHouseMetadataMixin.add_cli_arguments"></span>
**`add_cli_arguments(parser) -> None`** (static method): Adds the ClickHouse option group to an `argparse` parser: `--data-path` (default `/tmp/benchbox_ch_local`, the path for local-mode data) and `--deployment-mode` (default `local`, `server` or `local`).

<span id="benchbox.platforms.clickhouse.metadata.ClickHouseMetadataMixin.get_database_path"></span>
**`get_database_path(**connection_config) -> str | None`**: Returns the database path for local-mode persistence, or `None`. In server mode it always returns `None`. In local mode it uses the first of these that is set:

1. `connection_config["database_path"]`, if given and not `None`. A `.duckdb` suffix is replaced with `.chdb`, and `.chdb` is appended to any other name that does not already end with it.
2. The adapter's `database_path`, which `from_config` sets.
3. `None`. `check_server_database_exists` then returns `False`.

<span id="benchbox.platforms.clickhouse.metadata.ClickHouseMetadataMixin.get_target_dialect"></span>
**`get_target_dialect() -> str`**: Returns the target SQL dialect, `"clickhouse"`.

#### Delta Lake reads

These methods come from `ClickHouseWorkloadMixin`. They use the probes and resolver in `benchbox.platforms.clickhouse.delta_lake`.

<span id="benchbox.platforms.clickhouse.workload.ClickHouseWorkloadMixin.delta_native_registration"></span>
**`delta_native_registration(connection) -> bool`**: Probes whether the server registers native Delta Lake reads. It runs the `system.table_functions` and `system.table_engines` probes and applies `has_native_delta_registration`. It returns `True` when the server registers both the `deltaLake` function and the `DeltaLake` engine. The `connection` must expose `execute()`. Nothing is cached: the probe is two light system queries, so callers that decide per statement see the current server state.

<span id="benchbox.platforms.clickhouse.workload.ClickHouseWorkloadMixin.delta_reader_for"></span>
**`delta_reader_for(connection, location: str) -> DeltaReader`**: Selects the native or snapshot read path for a Delta table. It probes the server once, then calls `resolve_delta_reader` in `benchbox.platforms.clickhouse.delta_lake` with two verdicts: the base-integration verdict, and the verdict for the `deltaLakeLocal` function. Both are needed because a server can register the base integration without the local alias. `location` is the bucket URL or file system path of the Delta table, and the result is the chosen `DeltaReader`. It raises `ValueError` when the location is empty, blank or unrecognized, or when it has no executable read path (a remote location on a server without native reads).

#### Tuning

These methods come from `ClickHouseTuningMixin`.

<span id="benchbox.platforms.clickhouse.tuning.ClickHouseTuningMixin.get_effective_tuning_configuration"></span>
**`get_effective_tuning_configuration() -> UnifiedTuningConfiguration | None`**: Returns the tuning configuration with ClickHouse's requirements applied. ClickHouse requires primary keys even when tuning is off, so primary keys are always enabled. When there is no configured tuning, the result has foreign keys disabled.

<span id="benchbox.platforms.clickhouse.ClickHouseAdapter.validate_session_cache_control"></span>
<span id="benchbox.platforms.clickhouse.tuning.ClickHouseTuningMixin.validate_session_cache_control"></span>
**`validate_session_cache_control(connection) -> dict[str, Any]`**: Checks that the session-level cache control settings were applied. It reads `use_query_cache`, `enable_writes_to_query_cache` and `enable_reads_from_query_cache` from `system.settings` and expects each to be `0` when `disable_result_cache` is on and `1` otherwise. The result has these keys:

- **`validated`:** `True` when all three settings have the expected value.
- **`cache_disabled`:** `True` when validation passed and the cache is disabled.
- **`settings`:** the settings as read, as a dict of strings.
- **`warnings`:** a list of validation warnings.
- **`errors`:** a list of validation errors.

It raises `ConfigurationError` if validation fails and `strict_validation` is `True`.

<span id="benchbox.platforms.clickhouse.tuning.ClickHouseTuningMixin.supports_tuning_type"></span>
**`supports_tuning_type(tuning_type) -> bool`**: Tells whether ClickHouse supports a tuning type. The supported types are partitioning, sorting, clustering and distribution.

<span id="benchbox.platforms.clickhouse.ClickHouseAdapter.apply_table_tunings"></span>
<span id="benchbox.platforms.clickhouse.tuning.ClickHouseTuningMixin.apply_table_tunings"></span>
**`apply_table_tunings(table_tuning, connection) -> None`**: Applies ClickHouse-specific table tunings from a `TableTuning` object to an existing table. It does nothing when the object has no tunings. Each tuning type is handled differently:

- **Sorting:** runs `optimize_table`, which issues `OPTIMIZE TABLE ... FINAL`.
- **Clustering:** runs `OPTIMIZE TABLE ... FINAL` directly.
- **Partitioning:** only logged, because partitioning is fixed when the table is created.
- **Distribution:** only logged, because it is handled by engine settings.

If the tuning interface cannot be imported, it logs a warning and skips the tunings. Any other error is raised as `ValueError`.

<span id="benchbox.platforms.clickhouse.tuning.ClickHouseTuningMixin.apply_unified_tuning"></span>
**`apply_unified_tuning(unified_config: UnifiedTuningConfiguration, connection) -> None`**: Applies a unified tuning configuration to ClickHouse.

<span id="benchbox.platforms.clickhouse.tuning.ClickHouseTuningMixin.apply_platform_optimizations"></span>
**`apply_platform_optimizations(platform_config: PlatformOptimizationConfiguration, connection) -> None`**: An intentional no-op for ClickHouse today. `PlatformOptimizationConfiguration` models only Databricks and BigQuery style options (z-ordering, liquid clustering, auto-optimize, bloom filters, materialized views), and none of them apply to ClickHouse. ClickHouse session settings (memory, threads, join algorithm, cache control) are applied separately by `configure_for_benchmark`, which reads the adapter's own attributes.

<span id="benchbox.platforms.clickhouse.tuning.ClickHouseTuningMixin.apply_constraint_configuration"></span>
**`apply_constraint_configuration(primary_key_config: PrimaryKeyConfiguration, foreign_key_config: ForeignKeyConfiguration, connection) -> None`**: Logs an informational message for each enabled primary-key and foreign-key setting. It runs no SQL and does not use `connection`. The table-creation hooks handle any constraint DDL the platform supports.

## Configuration Examples

### Server Mode - Local Development

The first adapter connects to the default local server. The second adds authentication.

```python
from benchbox.platforms.clickhouse import ClickHouseAdapter

adapter = ClickHouseAdapter(
    deployment_mode="server",
    host="localhost",
    port=9000,
    database="benchmark"
)

adapter = ClickHouseAdapter(
    deployment_mode="server",
    host="localhost",
    port=9000,
    database="benchmark",
    username="benchmark_user",
    password="secure_password"
)
```

### Server Mode - Production

This example connects to a production server with TLS. Port 9440 is the secure native port.

```python
adapter = ClickHouseAdapter(
    deployment_mode="server",
    host="clickhouse.example.com",
    port=9440,
    database="production_benchmarks",
    username="admin",
    password="production_password",
    secure=True,
    max_memory_usage="32GB",
    max_threads=16
)
```

### Local Mode - Development

The first adapter runs in memory, which is fast but keeps no data. The second uses persistent storage, so the data survives restarts.

```python
adapter = ClickHouseAdapter(deployment_mode="local")

adapter = ClickHouseAdapter(
    deployment_mode="local",
    database_path="./benchmarks/clickhouse_local.chdb"
)
```

### Performance Tuning

Raise `max_memory_usage` for large datasets. `max_execution_time` is in seconds, so 600 is a 10 minute timeout. `max_threads=32` uses all available cores on a 32-core machine. `compression` is disabled by default for compatibility.

```python
adapter = ClickHouseAdapter(
    deployment_mode="server",
    host="localhost",
    database="benchmark",
    max_memory_usage="64GB",
    max_execution_time=600,
    max_threads=32,
    compression=False
)
```

## Data Loading

### Bulk Loading from Files

The example creates the table, then bulk-inserts from a CSV file.

```python
from benchbox.platforms.clickhouse import ClickHouseAdapter

adapter = ClickHouseAdapter(deployment_mode="server", host="localhost", database="benchmark")
conn = adapter.create_connection()

conn.execute("""
    CREATE TABLE lineitem (
        l_orderkey UInt32,
        l_partkey UInt32,
        l_suppkey UInt32,
        l_linenumber UInt8,
        l_quantity Decimal(15, 2),
        l_extendedprice Decimal(15, 2),
        l_discount Decimal(15, 2),
        l_tax Decimal(15, 2),
        l_returnflag String,
        l_linestatus String,
        l_shipdate Date,
        l_commitdate Date,
        l_receiptdate Date,
        l_shipinstruct String,
        l_shipmode String,
        l_comment String
    ) ENGINE = MergeTree()
    ORDER BY (l_orderkey, l_linenumber)
""")

conn.execute("""
    INSERT INTO lineitem
    FROM INFILE 'data/lineitem.tbl'
    FORMAT CSV
""")
```

### Loading from S3

ClickHouse can read directly from S3. The first statement reads public objects. The second passes credentials.

```python
conn.execute("""
    CREATE TABLE lineitem AS
    SELECT * FROM s3(
        'https://s3.amazonaws.com/bucket/lineitem/*.parquet',
        'Parquet'
    )
""")

conn.execute("""
    CREATE TABLE lineitem AS
    SELECT * FROM s3(
        'https://s3.amazonaws.com/bucket/lineitem/*.parquet',
        'aws_access_key_id',
        'aws_secret_access_key',
        'Parquet'
    )
""")
```

## Query Execution

### Execute Queries Directly

`conn.execute` returns a list of tuples. The example runs a simple count and then a more complex analytical query.

```python
from benchbox.platforms.clickhouse import ClickHouseAdapter

adapter = ClickHouseAdapter(deployment_mode="server", host="localhost", database="benchmark")
conn = adapter.create_connection()

result = conn.execute("SELECT COUNT(*) FROM lineitem")
row_count = result[0][0]

result = conn.execute("""
    SELECT
        l_returnflag,
        l_linestatus,
        sum(l_quantity) as sum_qty,
        sum(l_extendedprice) as sum_base_price,
        count(*) as count_order
    FROM lineitem
    WHERE l_shipdate <= '1998-09-01'
    GROUP BY l_returnflag, l_linestatus
    ORDER BY l_returnflag, l_linestatus
""")
```

### Query Plans and Optimization

The first statement gets the query plan. The second analyzes the query pipeline.

```python
plan = conn.execute("""
    EXPLAIN
    SELECT * FROM lineitem
    WHERE l_shipdate > '1995-01-01'
""")
for row in plan:
    print(row[0])

pipeline = conn.execute("""
    EXPLAIN PIPELINE
    SELECT COUNT(*) FROM lineitem
    GROUP BY l_orderkey
""")
```

## Advanced Features

### Table Engines

`MergeTree` is the most common engine for analytics. `ReplacingMergeTree` deduplicates rows by the sorting key.

```python
conn.execute("""
    CREATE TABLE orders (
        o_orderkey UInt32,
        o_custkey UInt32,
        o_orderstatus String,
        o_totalprice Decimal(15, 2),
        o_orderdate Date
    ) ENGINE = MergeTree()
    ORDER BY (o_orderdate, o_orderkey)
    PARTITION BY toYYYYMM(o_orderdate)
""")

conn.execute("""
    CREATE TABLE customer_updates (
        c_custkey UInt32,
        c_name String,
        c_address String,
        update_timestamp DateTime
    ) ENGINE = ReplacingMergeTree(update_timestamp)
    ORDER BY c_custkey
""")
```

### Materialized Views

This view pre-aggregates orders by date.

```python
conn.execute("""
    CREATE MATERIALIZED VIEW orders_by_date
    ENGINE = SummingMergeTree()
    ORDER BY order_date
    AS SELECT
        toDate(o_orderdate) AS order_date,
        count() AS order_count,
        sum(o_totalprice) AS total_revenue
    FROM orders
    GROUP BY order_date
""")
```

### Distributed Queries

This query runs across multiple shards. It needs a cluster setup.

```python
result = conn.execute("""
    SELECT
        l_returnflag,
        count() AS cnt
    FROM cluster('benchmark_cluster', default.lineitem)
    GROUP BY l_returnflag
""")
```

## Best Practices

### Memory Management

1. **Set appropriate memory limits** per query. `max_memory_usage` is the per-query limit:

   ```python
   adapter = ClickHouseAdapter(
       deployment_mode="server",
       host="localhost",
       max_memory_usage="16GB"
   )
   ```

2. **Monitor memory usage** during execution. This query checks the memory of running queries:

   ```python
   result = conn.execute("""
       SELECT
           query,
           memory_usage,
           formatReadableSize(memory_usage) AS readable_memory
       FROM system.processes
       WHERE user = currentUser()
   """)
   ```

3. **Use external aggregation** for large GROUP BY. This setting enables external aggregation automatically:

   ```python
   conn.execute("SET max_bytes_before_external_group_by = 10000000000")
   ```

### Performance Optimization

1. **Choose optimal table engine** and ordering key. Ordering by commonly filtered columns is good. Including all filter columns is better. These are illustrative SQL fragments; supply complete table definitions before execution.

   ```sql
   CREATE TABLE lineitem (...)
   ENGINE = MergeTree()
   ORDER BY (l_shipdate, l_orderkey)

   ORDER BY (l_shipdate, l_returnflag, l_orderkey)
   ```

2. **Use appropriate data types**. Prefer smaller types:

   - `UInt8` instead of `UInt32` for small integers
   - `Date` instead of `DateTime` for date-only fields
   - `LowCardinality(String)` for repeated strings

3. **Partition large tables**. `toYYYYMM` makes monthly partitions. This is an illustrative SQL fragment; supply a complete table definition before execution.

   ```sql
   CREATE TABLE lineitem (...)
   ENGINE = MergeTree()
   PARTITION BY toYYYYMM(l_shipdate)
   ORDER BY (l_orderkey, l_linenumber)
   ```

### Connection Management

1. **Reuse connections** for multiple queries, and close the connection when you are done:

   ```python
   adapter = ClickHouseAdapter(deployment_mode="server", host="localhost")
   conn = adapter.create_connection()

   for query_id in range(1, 23):
       result = conn.execute(queries[query_id])

   adapter.close_connection(conn)
   ```

2. **Set connection timeouts** appropriately. Long-running benchmarks need longer timeouts. `max_execution_time=600` is 10 minutes:

   ```python
   adapter = ClickHouseAdapter(
       deployment_mode="server",
       host="localhost",
       max_execution_time=600
   )
   ```

## Common Issues

### Connection Refused

**Problem**: Cannot connect to ClickHouse server

**Solutions**:

1. Check whether the server is running.
2. Start the server if it is not running.
3. Check that the port is listening.
4. Test the connection. The Python block below also verifies the connection.

```bash
ps aux | grep clickhouse-server

sudo service clickhouse-server start

netstat -ln | grep 9000

clickhouse-client --host=localhost --port=9000
```

```python
import socket
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
result = sock.connect_ex(('localhost', 9000))
if result == 0:
    print("Port 9000 is open")
else:
    print("Cannot connect to port 9000")
```

### Memory Limit Exceeded

**Problem**: Query fails with "Memory limit exceeded"

**Solutions**:

1. Increase the memory limit.
2. Enable external operations.
3. Reduce the scale factor for testing. Start small.

```python
adapter = ClickHouseAdapter(
    deployment_mode="server",
    host="localhost",
    max_memory_usage="32GB"
)

conn = adapter.create_connection()
conn.execute("SET max_bytes_before_external_group_by = 20000000000")
conn.execute("SET max_bytes_before_external_sort = 20000000000")

benchmark = TPCH(scale_factor=0.1)
```

### Local Mode Import Error

**Problem**: `ImportError: ClickHouse local mode requires chDB but it is not installed.` in local mode

**Solution**: Install chDB with the local-mode extra:

```bash
pip install "benchbox[clickhouse-local]"
```

Or switch to server mode, which needs the `clickhouse` extra:

```python
adapter = ClickHouseAdapter(deployment_mode="server", host="localhost")
```

### Slow Query Performance

**Problem**: Queries execute slowly

**Solutions**:

1. Increase the thread count so queries use more CPU cores.
2. Check the query plan. If it shows `FullScanStep` (a table scan), the table may need a better `ORDER BY`.
3. Enable query profiling, run the query, then check the query log. In server mode the log is `system.query_log`. chDB has no `system.query_log`.

```python
adapter = ClickHouseAdapter(
    deployment_mode="server",
    host="localhost",
    max_threads=16
)

plan = conn.execute("EXPLAIN SELECT ...")

conn.execute("SET log_queries = 1")
conn.execute("SET log_query_threads = 1")

result = conn.execute("SELECT ...")

log = conn.execute("""
    SELECT
        query,
        query_duration_ms,
        memory_usage,
        read_rows,
        read_bytes
    FROM system.query_log
    WHERE type = 'QueryFinish'
    ORDER BY event_time DESC
    LIMIT 1
""")
```

## See Also

### Platform Documentation

- {doc}`/platforms/platform-selection-guide` - Choosing ClickHouse vs other platforms
- {doc}`/platforms/quick-reference` - Quick setup for all platforms
- {doc}`/platforms/comparison-matrix` - Feature comparison
- {doc}`/platforms/clickhouse-local-mode` - Local mode guide

### Benchmark Guides

- {doc}`/benchmarks/tpc-h` - TPC-H on ClickHouse
- {doc}`/benchmarks/tpc-ds` - TPC-DS on ClickHouse
- {doc}`/benchmarks/clickbench` - ClickBench on ClickHouse

### API Reference

- {doc}`duckdb` - DuckDB adapter for comparison
- {doc}`../base` - Base benchmark interface
- {doc}`../index` - Python API overview
- {doc}`/reference/api-reference` - High-level API guide

### External Resources

- [ClickHouse Documentation](https://clickhouse.com/docs) - Official ClickHouse docs
- [ClickHouse Performance Guide](https://clickhouse.com/docs/optimize/query-optimization) - Performance tuning
- [chDB Documentation](https://github.com/chdb-io/chdb) - Local mode library
- [ClickHouse Table Engines](https://clickhouse.com/docs/engines/table-engines) - Storage engines
