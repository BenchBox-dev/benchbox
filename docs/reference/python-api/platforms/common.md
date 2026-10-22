# Common platform adapter API

```{tags} reference, python-api, sql-platform
```

All adapters on the platform pages implement or inherit this lifecycle. Use the adapter page for configuration and platform-specific operations.

## API Reference

### Class

#### `benchbox.platforms.base.adapter.PlatformAdapter`

<span id="benchbox.platforms.base.adapter.PlatformAdapter"></span>

```python
class PlatformAdapter:
    def __init__(self, **config) -> None: ...
```

The base class for platform adapters. It stores common run configuration and coordinates connection, schema, loading, execution, result capture, and close-up. Do not instantiate it directly.

**Import:** `from benchbox.platforms.base.adapter import PlatformAdapter`

##### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `**config` | keyword arguments | optional | Common run configuration, stored by the adapter. |

### Connection and identity

#### PlatformAdapter

##### `benchbox.platforms.base.adapter.PlatformAdapter.from_config`

<span id="benchbox.platforms.base.adapter.PlatformAdapter.from_config"></span>

```python
@classmethod
def from_config(config: dict[str, Any]): ...
```

Creates a platform adapter instance from a unified configuration.

###### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `config` | `dict[str, Any]` | required | Unified configuration dictionary. |

###### Returns

Platform adapter instance.

##### `benchbox.platforms.base.adapter.PlatformAdapter.platform_name`

<span id="benchbox.platforms.base.adapter.PlatformAdapter.platform_name"></span>

**Kind:** property

Returns the name of this database platform.

The default implementation returns the class name. Concrete adapters may override it to provide a user-friendly display name. Lightweight adapters can rely on the default when no custom name is required.

##### `benchbox.platforms.base.adapter.PlatformAdapter.canonical_platform_type`

<span id="benchbox.platforms.base.adapter.PlatformAdapter.canonical_platform_type"></span>

**Kind:** property

Returns the canonical, machine-readable platform type key.

Tuning-capability lookups and metadata persistence must key off a stable identifier (for example `"clickhouse-local"` or `"duckdb"`), not `platform_name`. `platform_name` is a human-facing display string (for example `"ClickHouse Local"` or `"StarRocks"`) that varies by adapter and is never guaranteed to match the lowercase, single-word keys of capability maps such as the compatibility map of `TuningType`.

The value comes from the `type` key in `platform_config` when that key is present. The upstream config plumbing (`core/platform_config.py`) strips `type` from `DatabaseConfig` before the adapter is constructed, so the key does not survive that path on its own. `get_platform_adapter` in `benchbox.platforms` re-injects the resolved canonical registry name (`PlatformRegistry.resolve_platform_name`) into the constructor config, and this property reads it on every adapter that the factory builds.

When no config type is available, the property falls back to a normalized form of `platform_name` (lowercased, spaces collapsed to hyphens). This happens, for example, when an adapter is constructed directly and bypasses the factory, as many unit tests do. The fallback is best-effort only. It does not guarantee a match against any capability map key: a parenthesized display name such as `"ClickHouse (Local)"` normalizes to `"clickhouse-(local)"`. It only avoids a crash on multi-word display strings.

##### `benchbox.platforms.base.adapter.PlatformAdapter.create_connection`

<span id="benchbox.platforms.base.adapter.PlatformAdapter.create_connection"></span>

```python
def create_connection(**connection_config) -> Any: ...
```

Creates and returns a database connection.

###### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `**connection_config` | keyword arguments | optional | Connection-specific parameters. |

###### Returns

`Any`: Database connection object.

##### `benchbox.platforms.base.adapter.PlatformAdapter.close_connection`

<span id="benchbox.platforms.base.adapter.PlatformAdapter.close_connection"></span>

```python
def close_connection(connection: Any) -> None: ...
```

Closes a database connection and cleans up resources.

###### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `connection` | `Any` | required | Database connection to close. |

##### `benchbox.platforms.base.adapter.PlatformAdapter.new_stream_connection`

<span id="benchbox.platforms.base.adapter.PlatformAdapter.new_stream_connection"></span>

```python
def new_stream_connection(connection: Any, *, benchmark_type: str | None = None) -> Any: ...
```

Returns a per-stream execution handle for one concurrent throughput (or connection-pool test) stream.

This is the capability seam for `throughput-independent-sessions-per-stream`. The `connection_factory` closures of the throughput drivers (`benchbox/platforms/base/execution.py`, `_execute_tpch_throughput_test` and `_execute_tpcds_throughput_test`) call this method once per stream instead of unconditionally sharing one cursor. The behavior is therefore a declared, overridable platform capability rather than an implicit one-size-fits-all default.

The `benchmark_type` keyword is `"olap"` for the TPC-H and TPC-DS throughput drivers unless the caller overrides it through run config. It lets `INDEPENDENT_CONNECTION` overrides reproduce the benchmark-type session tuning that the shared connection carries (equivalence dimension 4 in `StreamConnectionCapability`). The keyword is optional so that existing overrides and test doubles keep working unchanged.

The method dispatches on `stream_connection_capability`:

- `SHARED_CURSOR` (the default): returns `_make_stream_cursor(connection)`, which is a cursor of the single shared `connection` passed in, or a `_NoCloseProxy` over it. This is the existing, unchanged fast path. It is correct for embedded engines whose client is documented as thread-safe at cursor level against one process-local database (for example DuckDB; see `docs/benchmarks/tpc-h.md`). No new connections are opened. Closing the returned handle never closes the shared connection: `_NoCloseProxy.close()` is a no-op, and a real cursor's `close()` only closes the cursor. `benchmark_type` is ignored, because the shared connection already carries its tuning.
- `INDEPENDENT_CONNECTION`: server-style adapters (client/server engines whose driver does not support true concurrent statement execution across cursors of one connection) must override this method to open and return a brand-new connection or session, typically ignoring the `connection` argument entirely. The base implementation deliberately raises `NotImplementedError` for this capability value instead of falling back to cursor sharing, so a subclass that declares `INDEPENDENT_CONNECTION` without overriding fails loudly rather than silently reproducing the shared-session bug this capability exists to fix. Overrides should apply `configure_for_benchmark(stream_conn, benchmark_type or "olap")`, plus the `_apply_stream_session_state` hook for connection-establishment state, so that the stream session measures the same tuning as the setup session.
- `UNSUPPORTED`: never reaches this method. The throughput entry points refuse through `require_throughput_stream_capability` before any stream is submitted.

###### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `connection` | `Any` | required | The adapter's shared platform connection, as created by `create_connection`. Used as-is for `SHARED_CURSOR`. Available for reference (for example to read connection parameters) but not required for `INDEPENDENT_CONNECTION` overrides. |
| `benchmark_type` | `str` or `None` | `None` | Benchmark tuning vocabulary (for example `"olap"`) for per-stream session parity. Optional. Overrides replay tuning only when it is supplied, so callers that pass nothing keep their previous behavior. |

###### Returns

A connection-like object suitable for one stream: either a cursor or proxy over the shared connection, or an independent connection.

###### Raises

- `NotImplementedError`: the adapter declares `INDEPENDENT_CONNECTION` and does not override this method.

##### `benchbox.platforms.base.adapter.PlatformAdapter.get_platform_info`

<span id="benchbox.platforms.base.adapter.PlatformAdapter.get_platform_info"></span>

```python
def get_platform_info(connection: Any = None) -> dict[str, Any]: ...
```

Gets platform information for results traceability.

The default implementation returns minimal generic information. Platform adapters should override it to provide richer details, but tests may instantiate lightweight adapters without implementing this method.

### Schema, data, and execution

#### PlatformAdapter

##### `benchbox.platforms.base.adapter.PlatformAdapter.create_schema`

<span id="benchbox.platforms.base.adapter.PlatformAdapter.create_schema"></span>

```python
def create_schema(benchmark, connection: Any) -> float: ...
```

Creates the database schema for the benchmark.

###### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `benchmark` | not annotated | required | Benchmark instance with schema definitions. |
| `connection` | `Any` | required | Database connection. |

###### Returns

`float`: Time taken to create the schema, in seconds.

##### `benchbox.platforms.base.adapter.PlatformAdapter.load_data`

<span id="benchbox.platforms.base.adapter.PlatformAdapter.load_data"></span>

```python
def load_data(
    benchmark,
    connection: Any,
    data_dir: Path,
) -> tuple[dict[str, int], float, dict[str, Any] | None]: ...
```

Loads benchmark data into the database using platform-specific methods.

###### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `benchmark` | not annotated | required | Benchmark instance. |
| `connection` | `Any` | required | Database connection. |
| `data_dir` | `Path` | required | Directory containing data files. |

###### Returns

`tuple[dict[str, int], float, dict[str, Any] | None]`: A tuple of `(table_statistics, loading_time_seconds, per_table_timings)`. `per_table_timings` is an optional dict with detailed timing per table.

##### `benchbox.platforms.base.adapter.PlatformAdapter.create_external_tables`

<span id="benchbox.platforms.base.adapter.PlatformAdapter.create_external_tables"></span>

```python
def create_external_tables(
    benchmark: Any,
    connection: Any,
    data_dir: Path,
) -> tuple[dict[str, int], float, dict[str, Any] | None]: ...
```

Registers benchmark tables as external references instead of loading native tables.

Platforms that support external-table mode should override this method and return the same tuple shape as `load_data`.

###### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `benchmark` | `Any` | required | Benchmark instance. |
| `connection` | `Any` | required | Database connection. |
| `data_dir` | `Path` | required | Directory containing source data files or external data roots. |

###### Returns

`tuple[dict[str, int], float, dict[str, Any] | None]`: A tuple of `(table_statistics, loading_time_seconds, per_table_timings)`. `per_table_timings` is an optional dict with detailed timing per table.

##### `benchbox.platforms.base.adapter.PlatformAdapter.execute_query`

<span id="benchbox.platforms.base.adapter.PlatformAdapter.execute_query"></span>

```python
def execute_query(
    connection: Any,
    query: str,
    query_id: str,
    benchmark_type: str | None = None,
    scale_factor: float | None = None,
    validate_row_count: bool = True,
    stream_id: int | None = None,
) -> dict[str, Any]: ...
```

Executes a single query and returns detailed results.

###### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `connection` | `Any` | required | Database connection. |
| `query` | `str` | required | SQL query text. |
| `query_id` | `str` | required | Query identifier. |
| `benchmark_type` | `str` or `None` | `None` | Type of benchmark (for example `"tpch"` or `"tpcds"`), used for row count validation. |
| `scale_factor` | `float` or `None` | `None` | Scale factor used for the query, used for row count validation. |
| `validate_row_count` | `bool` | `True` | Whether to validate the row count against expected results. |
| `stream_id` | `int` or `None` | `None` | Stream identifier for multi-stream benchmarks (for example 0, 1, 2). Used to select stream-specific expected results. `None` indicates stream 0 or single-stream execution. |

###### Returns

`dict[str, Any]`: Dictionary with the execution results: `query_id`, `status` (`"SUCCESS"`, `"FAILED"` or `"DRY_RUN"`), `execution_time_seconds`, `rows_returned`, and optional `row_count_validation` fields.

##### `benchbox.platforms.base.adapter.PlatformAdapter.run_benchmark`

<span id="benchbox.platforms.base.adapter.PlatformAdapter.run_benchmark"></span>

```python
def run_benchmark(benchmark, **run_config) -> EnhancedBenchmarkResults: ...
```

Runs the complete benchmark with enhanced phase tracking.

###### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `benchmark` | not annotated | required | Benchmark instance to execute. |
| `**run_config` | keyword arguments | optional | Runtime configuration options. |

###### Returns

Enhanced benchmark results with detailed phase tracking.

##### `benchbox.platforms.base.adapter.PlatformAdapter.run_enhanced_benchmark`

<span id="benchbox.platforms.base.adapter.PlatformAdapter.run_enhanced_benchmark"></span>

```python
def run_enhanced_benchmark(benchmark, **run_config) -> EnhancedBenchmarkResults: ...
```

Runs the complete benchmark with enhanced phase tracking.

### Capability and tuning hooks

#### PlatformAdapter

##### `benchbox.platforms.base.adapter.PlatformAdapter.validate_platform_capabilities`

<span id="benchbox.platforms.base.adapter.PlatformAdapter.validate_platform_capabilities"></span>

```python
def validate_platform_capabilities(benchmark_type: str) -> ValidationResult: ...
```

Validates platform-specific capabilities for the benchmark.

###### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `benchmark_type` | `str` | required | Type of benchmark (for example `'tpcds'` or `'tpch'`). |

###### Returns

`ValidationResult` with the platform capability validation status.

##### `benchbox.platforms.base.adapter.PlatformAdapter.configure_for_benchmark`

<span id="benchbox.platforms.base.adapter.PlatformAdapter.configure_for_benchmark"></span>

```python
def configure_for_benchmark(connection: Any, benchmark_type: str) -> None: ...
```

Applies platform-specific optimizations for the benchmark type.

###### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `connection` | `Any` | required | Database connection. |
| `benchmark_type` | `str` | required | Type of benchmark (for example `"olap"`, `"oltp"` or `"analytics"`). |

##### `benchbox.platforms.base.adapter.PlatformAdapter.gather_statistics`

<span id="benchbox.platforms.base.adapter.PlatformAdapter.gather_statistics"></span>

```python
def gather_statistics(connection: Any, table_names: list[str]) -> tuple[str, int]: ...
```

Runs the platform's explicit statistics build for the statistics phase.

Returns `(stats_mode, tables_analyzed)`. The default resolves the adapter's existing analyze surface: whole-database `analyze_tables` when available, otherwise per-table `analyze_table`, otherwise `("unsupported", 0)`. Engines whose statistics are built during load (for example Redshift with `auto_analyze`) override this method to report `("auto-on-load", 0)` instead of building statistics twice.

##### `benchbox.platforms.base.adapter.PlatformAdapter.apply_platform_optimizations`

<span id="benchbox.platforms.base.adapter.PlatformAdapter.apply_platform_optimizations"></span>

```python
def apply_platform_optimizations(
    platform_config: PlatformOptimizationConfiguration,
    connection: Any,
) -> None: ...
```

Applies platform-specific optimizations.

###### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `platform_config` | `PlatformOptimizationConfiguration` | required | Platform optimization configuration. |
| `connection` | `Any` | required | Database connection. |

##### `benchbox.platforms.base.adapter.PlatformAdapter.apply_constraint_configuration`

<span id="benchbox.platforms.base.adapter.PlatformAdapter.apply_constraint_configuration"></span>

```python
def apply_constraint_configuration(
    primary_key_config: PrimaryKeyConfiguration,
    foreign_key_config: ForeignKeyConfiguration,
    connection: Any,
) -> None: ...
```

Applies constraint configurations to the database.

###### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `primary_key_config` | `PrimaryKeyConfiguration` | required | Primary key constraint configuration. |
| `foreign_key_config` | `ForeignKeyConfiguration` | required | Foreign key constraint configuration. |
| `connection` | `Any` | required | Database connection. |

##### `benchbox.platforms.base.adapter.PlatformAdapter.get_tuning_introspector`

<span id="benchbox.platforms.base.adapter.PlatformAdapter.get_tuning_introspector"></span>

```python
def get_tuning_introspector() -> Introspector | None: ...
```

Returns a post-load schema introspector for this platform, or `None`.

An introspector corroborates the applied-tuning ledger against the real database catalog. This lets an `applied_unverified` run be upgraded to `applied_verified`, but only when every catalog-backed tuning statement is corroborated (see `benchbox.core.tuning.introspection`). Platforms with a structured catalog (DuckDB, ClickHouse) override this method. The base returns `None`, so a platform without an introspector keeps the honest ledger-derived status.

### Static member inventory

#### PlatformAdapter

##### `benchbox.platforms.base.adapter.PlatformAdapter.add_cli_arguments`

<span id="benchbox.platforms.base.adapter.PlatformAdapter.add_cli_arguments"></span>

```python
@staticmethod
def add_cli_arguments(parser) -> None: ...
```

Adds platform-specific CLI arguments to the argument parser.

###### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `parser` | not annotated | required | `argparse.ArgumentParser` instance to add arguments to. |

##### `benchbox.platforms.base.adapter.PlatformAdapter.is_dry_run`

<span id="benchbox.platforms.base.adapter.PlatformAdapter.is_dry_run"></span>

**Kind:** property

Returns `True` if dry run is active through configuration or execution mode.

##### `benchbox.platforms.base.adapter.PlatformAdapter.materialize_schema_only_tables`

<span id="benchbox.platforms.base.adapter.PlatformAdapter.materialize_schema_only_tables"></span>

```python
def materialize_schema_only_tables(benchmark, connection: Any) -> dict[str, int]: ...
```

Materializes catalog objects for schema-only benchmarks.

The `SKIP_DATA_LOADING` path bypasses `load_data()`, but some adapters only create catalog objects there (DataFusion builds empty tables from the schema recorded by `create_schema()`). Overrides must create empty tables without loading files. The default is a no-op for adapters whose `create_schema()` already materializes the tables.

###### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `benchmark` | not annotated | required | Benchmark instance with schema definitions. |
| `connection` | `Any` | required | Database connection. |

###### Returns

`dict[str, int]`: Mapping of table name to row count (zeros for empty tables).

##### `benchbox.platforms.base.adapter.PlatformAdapter.upload_manifest`

<span id="benchbox.platforms.base.adapter.PlatformAdapter.upload_manifest"></span>

```python
def upload_manifest(manifest_path: Path, remote_path: str) -> bool: ...
```

Uploads a manifest to remote storage. Override it in subclasses if supported.

###### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `manifest_path` | `Path` | required | Local manifest file path. |
| `remote_path` | `str` | required | Remote directory path or URI where the manifest should be uploaded. |

###### Returns

`bool`: `True` if the upload succeeded, `False` if it is unsupported or nothing was uploaded.

##### `benchbox.platforms.base.adapter.PlatformAdapter.reset_statistics`

<span id="benchbox.platforms.base.adapter.PlatformAdapter.reset_statistics"></span>

```python
def reset_statistics(connection: Any, table_names: list[str]) -> str: ...
```

Resets (drops or invalidates) optimizer statistics ahead of a cold-stats rebuild.

This is a sibling of `gather_statistics` for the reset/persist control, which is opt-in through the statistics phase's `reset=True`. It returns a `stats_lifecycle` marker: `"reset"` when statistics were actually cleared, or `"unsupported"` when this adapter has no generic drop-stats primitive that is safe to run generically.

The base default is a documented no-op that always returns `"unsupported"`. Engines must never have this method force an operation that could fail or corrupt state on a platform it was not vetted for. This is a safe fallback rather than a regression: the subsequent `gather_statistics()` call of the statistics phase still runs a full ANALYZE or rebuild that reflects current data, so a cold-stats study remains meaningful even without an explicit reset step. Platform adapters that support a real drop-stats operation should override this method.

##### `benchbox.platforms.base.adapter.PlatformAdapter.run_statistics_phase`

<span id="benchbox.platforms.base.adapter.PlatformAdapter.run_statistics_phase"></span>

```python
def run_statistics_phase(
    benchmark: Any,
    connection: Any,
    *,
    benchmark_name: str = '',
    table_names: list[str] | None = None,
    reset: bool | None = None,
    collect_per_table_timing: bool = False,
) -> StatisticsGatheringPhase | None: ...
```

Runs the opt-in statistics phase between load and query execution.

Returns `None` (phase not run) when the benchmark has not opted in through the registry's `supports_statistics_phase` flag, so legacy benchmarks keep load-includes-stats semantics and their historical bundles stay comparable. Failures are recorded on the phase rather than aborting the run, because queries remain meaningful on unanalyzed data.

###### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `reset` | `bool` or `None` | `None` | Cold-stats versus warm-stats control. `None` (the default) leaves statistics untouched and records no `stats_lifecycle` marker, exactly matching the behavior shipped in PR #980. `True` resets statistics through `reset_statistics()` before rebuilding (cold-stats). `False` explicitly records a `"persist"` marker (warm-stats) without changing behavior. |
| `collect_per_table_timing` | `bool` | `False` | When `True`, and this adapter's statistics build falls back to a per-table ANALYZE loop (no whole-database analyze hook, and `gather_statistics` is not overridden with platform-specific routing), records a per-table wall-clock breakdown on the returned phase. Left `None` otherwise. |

The other parameters (`benchmark`, `connection`, `benchmark_name` and `table_names`) have no description in the source.

##### `benchbox.platforms.base.adapter.PlatformAdapter.driver_isolation_capability`

<span id="benchbox.platforms.base.adapter.PlatformAdapter.driver_isolation_capability"></span>

**Kind:** attribute

Declares whether this adapter can run through an isolated driver runtime. The value controls runtime-resolution support.

##### `benchbox.platforms.base.adapter.PlatformAdapter.stream_connection_capability`

<span id="benchbox.platforms.base.adapter.PlatformAdapter.stream_connection_capability"></span>

**Kind:** attribute

Declares whether concurrent benchmark streams use a shared cursor or require independent connections.

##### `benchbox.platforms.base.adapter.PlatformAdapter.default_service_port`

<span id="benchbox.platforms.base.adapter.PlatformAdapter.default_service_port"></span>

**Kind:** attribute

Provides the default service port used when connection configuration omits one.

##### `benchbox.platforms.base.adapter.PlatformAdapter.supports_external_tables`

<span id="benchbox.platforms.base.adapter.PlatformAdapter.supports_external_tables"></span>

**Kind:** attribute

Advertises whether the adapter implements external-table creation.

##### `benchbox.platforms.base.adapter.PlatformAdapter.plan_capture_phase_eligible`

<span id="benchbox.platforms.base.adapter.PlatformAdapter.plan_capture_phase_eligible"></span>

**Kind:** attribute

Advertises whether benchmark plan capture is available for this adapter.

#### ConnectionLifecycleMixin

`ConnectionLifecycleMixin` (in `benchbox.platforms.base.connection_lifecycle`) is a base class of `PlatformAdapter`. `PlatformAdapter` and every adapter inherit these members.

##### `benchbox.platforms.base.connection_lifecycle.ConnectionLifecycleMixin.test_connection`

<span id="benchbox.platforms.base.connection_lifecycle.ConnectionLifecycleMixin.test_connection"></span>

```python
def test_connection(connection_config: ConnectionConfig | None = None) -> bool: ...
```

Tests database connectivity.

###### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `connection_config` | `ConnectionConfig` or `None` | `None` | Optional connection configuration. |

###### Returns

`bool`: `True` if the connection succeeded, `False` otherwise.

##### `benchbox.platforms.base.connection_lifecycle.ConnectionLifecycleMixin.validate_platform_dependencies`

<span id="benchbox.platforms.base.connection_lifecycle.ConnectionLifecycleMixin.validate_platform_dependencies"></span>

```python
@staticmethod
def validate_platform_dependencies() -> dict[str, bool]: ...
```

Validates that platform-specific dependencies are available.

###### Returns

`dict[str, bool]`: Dictionary mapping dependency names to availability status.

##### `benchbox.platforms.base.connection_lifecycle.ConnectionLifecycleMixin.require_dependencies`

<span id="benchbox.platforms.base.connection_lifecycle.ConnectionLifecycleMixin.require_dependencies"></span>

```python
@staticmethod
def require_dependencies(required: list[str], exit_on_missing: bool = True) -> dict[str, bool]: ...
```

Requires specific dependencies, and optionally exits with a helpful message if any is missing.

###### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `required` | `list[str]` | required | List of required dependency names. |
| `exit_on_missing` | `bool` | `True` | Whether to exit if dependencies are missing. |

###### Returns

`dict[str, bool]`: Dictionary mapping dependency names to availability status.

##### `benchbox.platforms.base.connection_lifecycle.ConnectionLifecycleMixin.get_connection_from_pool`

<span id="benchbox.platforms.base.connection_lifecycle.ConnectionLifecycleMixin.get_connection_from_pool"></span>

```python
def get_connection_from_pool() -> Any: ...
```

Gets a connection from the pool, if the platform supports pooling.

###### Returns

Database connection from the pool, or a new connection.

##### `benchbox.platforms.base.connection_lifecycle.ConnectionLifecycleMixin.get_database_path`

<span id="benchbox.platforms.base.connection_lifecycle.ConnectionLifecycleMixin.get_database_path"></span>

```python
def get_database_path(**connection_config) -> str | None: ...
```

Gets the database file path for file-based databases.

Override this method in platform adapters that use file-based databases.

##### `benchbox.platforms.base.connection_lifecycle.ConnectionLifecycleMixin.check_database_exists`

<span id="benchbox.platforms.base.connection_lifecycle.ConnectionLifecycleMixin.check_database_exists"></span>

```python
def check_database_exists(**connection_config) -> bool: ...
```

Checks whether the database already exists.

For file-based databases, it checks whether the file exists. For server-based databases, it checks whether the database or schema exists on the server.

##### `benchbox.platforms.base.connection_lifecycle.ConnectionLifecycleMixin.check_server_database_exists`

<span id="benchbox.platforms.base.connection_lifecycle.ConnectionLifecycleMixin.check_server_database_exists"></span>

```python
def check_server_database_exists(**connection_config) -> bool: ...
```

Checks whether the database exists on the server (for server-based databases).

Override this method in platform adapters for server-based databases.

##### `benchbox.platforms.base.connection_lifecycle.ConnectionLifecycleMixin.check_benchmark_tables_exist`

<span id="benchbox.platforms.base.connection_lifecycle.ConnectionLifecycleMixin.check_benchmark_tables_exist"></span>

```python
def check_benchmark_tables_exist(**connection_config) -> bool | None: ...
```

Checks whether a managed database has the current benchmark tables.

###### Returns

`True` when the adapter can prove the required tables are present, `False` when the adapter can prove they are missing or unusable, and `None` when the adapter does not implement managed table validation.

##### `benchbox.platforms.base.connection_lifecycle.ConnectionLifecycleMixin.handle_existing_database`

<span id="benchbox.platforms.base.connection_lifecycle.ConnectionLifecycleMixin.handle_existing_database"></span>

```python
def handle_existing_database(**connection_config) -> None: ...
```

Handles an existing database non-interactively for core and programmatic use.

The method validates database compatibility and makes these decisions automatically:

- If `force_recreate=True`, it always recreates the database.
- If the database is valid, it reuses it.
- If the database has issues, it recreates it.
- If `skip_database_management=True`, it skips create/drop management while allowing adapters to opt into table-readiness checks.

##### `benchbox.platforms.base.connection_lifecycle.ConnectionLifecycleMixin.reset_database_in_place`

<span id="benchbox.platforms.base.connection_lifecycle.ConnectionLifecycleMixin.reset_database_in_place"></span>

```python
def reset_database_in_place(**connection_config) -> bool: ...
```

Empties a server-side database for reload without dropping it.

Returns `True` when the database was reset in place, so the caller skips `drop_database`. The default does nothing and returns `False`. Override it where dropping objects is costly, for example where dropped tables keep counting against a catalog quota.

##### `benchbox.platforms.base.connection_lifecycle.ConnectionLifecycleMixin.drop_database`

<span id="benchbox.platforms.base.connection_lifecycle.ConnectionLifecycleMixin.drop_database"></span>

```python
def drop_database(**connection_config) -> None: ...
```

Drops or removes the database on the server (for server-based databases).

Override this method in platform adapters for server-based databases.

##### `benchbox.platforms.base.connection_lifecycle.ConnectionLifecycleMixin.platform_name`

<span id="benchbox.platforms.base.connection_lifecycle.ConnectionLifecycleMixin.platform_name"></span>

**Kind:** attribute

Host-provided platform name (`str`), used in diagnostics and capability selection. This mixin supplies no default.

##### `benchbox.platforms.base.connection_lifecycle.ConnectionLifecycleMixin.logger`

<span id="benchbox.platforms.base.connection_lifecycle.ConnectionLifecycleMixin.logger"></span>

**Kind:** attribute

Holds the adapter logger used for lifecycle and diagnostic messages.

##### `benchbox.platforms.base.connection_lifecycle.ConnectionLifecycleMixin.connection_pool`

<span id="benchbox.platforms.base.connection_lifecycle.ConnectionLifecycleMixin.connection_pool"></span>

**Kind:** attribute

Holds the optional reusable connection pool for adapters that support pooling.

##### `benchbox.platforms.base.connection_lifecycle.ConnectionLifecycleMixin.platform_config`

<span id="benchbox.platforms.base.connection_lifecycle.ConnectionLifecycleMixin.platform_config"></span>

**Kind:** attribute

Holds the platform configuration used to create the adapter.

#### DialectTranslationMixin

`DialectTranslationMixin` (in `benchbox.platforms.base.dialect_translation`) is a base class of `PlatformAdapter`. `PlatformAdapter` and every adapter inherit these members.

##### `benchbox.platforms.base.dialect_translation.DialectTranslationMixin.dialect`

<span id="benchbox.platforms.base.dialect_translation.DialectTranslationMixin.dialect"></span>

**Kind:** property

Returns the SQL dialect for this platform (for sqlglot translation).

##### `benchbox.platforms.base.dialect_translation.DialectTranslationMixin.translate_sql`

<span id="benchbox.platforms.base.dialect_translation.DialectTranslationMixin.translate_sql"></span>

```python
def translate_sql(
    sql: str,
    source_dialect: str = 'standard',
    strict: bool | None = None,
    scope: str | None = None,
) -> str: ...
```

Translates SQL from a source dialect to the platform dialect using sqlglot.

It delegates to the centralized `dialect_utils` pipeline, which provides dialect normalization, the identifier quoting policy, and platform-specific post-fixes (DuckDB `GROUP BY ALL`, SQLite syntax rewrites). It handles multi-statement schema SQL that `translate_sql_query()` does not.

###### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `sql` | `str` | required | SQL query or schema block (may contain multiple statements). |
| `source_dialect` | `str` | `'standard'` | Source SQL dialect (default: standard ANSI DDL). |
| `strict` | `bool` or `None` | `None` | When `True`, raise instead of falling back to the original SQL. When omitted, the active `sql_translation_context` policy is used. |
| `scope` | `str` or `None` | `None` | Workload scope recorded on the outcome. Defaults to `SCHEMA_DDL_SCOPE`, since schema creation is the only production caller. |

###### Returns

`str`: Translated SQL string, preserving multi-statement structure.

##### `benchbox.platforms.base.dialect_translation.DialectTranslationMixin.get_tpc_base_dialect`

<span id="benchbox.platforms.base.dialect_translation.DialectTranslationMixin.get_tpc_base_dialect"></span>

```python
def get_tpc_base_dialect(benchmark_name: str) -> str: ...
```

Returns the base dialect for TPC query generation (qgen and dsqgen).

The default is `'netezza'` for both TPC-DS and TPC-H, for modern SQL compatibility. Adapters may override it to select a closer match if beneficial.

###### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `benchmark_name` | `str` | required | `'tpch'`, `'tpcds'` and so on (case-insensitive). |

###### Returns

`str`: Base dialect string to use when invoking qgen or dsqgen.

##### `benchbox.platforms.base.dialect_translation.DialectTranslationMixin.logger`

<span id="benchbox.platforms.base.dialect_translation.DialectTranslationMixin.logger"></span>

**Kind:** attribute

Holds the adapter logger used for lifecycle and diagnostic messages.

#### TuningHooksMixin

`TuningHooksMixin` (in `benchbox.platforms.base.tuning`) is a base class of `PlatformAdapter`. `PlatformAdapter` and every adapter inherit these members.

##### `benchbox.platforms.base.tuning.TuningHooksMixin.apply_table_tunings`

<span id="benchbox.platforms.base.tuning.TuningHooksMixin.apply_table_tunings"></span>

```python
def apply_table_tunings(table_tuning: TableTuning, connection: Any) -> None: ...
```

Applies tuning configurations to a database table.

Platform adapters should implement this method to apply platform-specific tuning optimizations such as partitioning, clustering, distribution, and sorting.

###### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `table_tuning` | `TableTuning` | required | The tuning configuration to apply. |
| `connection` | `Any` | required | Database connection. |

###### Raises

- `NotImplementedError`: tuning is not supported by the platform.
- `ValueError`: the tuning configuration is invalid for this platform.

##### `benchbox.platforms.base.tuning.TuningHooksMixin.supports_tuning_type`

<span id="benchbox.platforms.base.tuning.TuningHooksMixin.supports_tuning_type"></span>

```python
def supports_tuning_type(tuning_type: TuningTypeT) -> bool: ...
```

Checks whether this platform adapter supports a specific tuning type.

###### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `tuning_type` | `TuningTypeT` | required | The type of tuning to check support for. |

###### Returns

`bool`: `True` if the tuning type is supported by this platform.

##### `benchbox.platforms.base.tuning.TuningHooksMixin.generate_tuning_clause`

<span id="benchbox.platforms.base.tuning.TuningHooksMixin.generate_tuning_clause"></span>

```python
def generate_tuning_clause(table_tuning: TableTuning) -> str: ...
```

Generates platform-specific tuning clauses for CREATE TABLE statements.

The method should generate the SQL clauses to include in CREATE TABLE statements so that the specified tuning configurations are applied.

###### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `table_tuning` | `TableTuning` | required | The tuning configuration for the table. |

###### Returns

`str`: SQL clause string to append to the CREATE TABLE statement (an empty string if no tuning clauses are needed).

###### Raises

- `ValueError`: the tuning configuration is invalid for this platform.

##### `benchbox.platforms.base.tuning.TuningHooksMixin.platform_name`

<span id="benchbox.platforms.base.tuning.TuningHooksMixin.platform_name"></span>

**Kind:** attribute

Host-provided platform name (`str`), used in diagnostics and capability selection. This mixin supplies no default.

#### TuningConfigMixin

`TuningConfigMixin` (in `benchbox.platforms.base.tuning_config`) is a base class of `PlatformAdapter`. `PlatformAdapter` and every adapter inherit these members.

##### `benchbox.platforms.base.tuning_config.TuningConfigMixin.apply_unified_tuning`

<span id="benchbox.platforms.base.tuning_config.TuningConfigMixin.apply_unified_tuning"></span>

```python
def apply_unified_tuning(unified_config: UnifiedTuningConfiguration, connection: Any) -> None: ...
```

Applies a unified tuning configuration to the database.

The method should implement platform-specific logic for applying the full unified tuning configuration, including:

- Schema constraints (primary keys, foreign keys, unique, check).
- Platform-specific optimizations (Z-ordering, auto-optimize and so on).
- Table-level tunings (partitioning, clustering, distribution, sorting).

###### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `unified_config` | `UnifiedTuningConfiguration` | required | Unified tuning configuration to apply. |
| `connection` | `Any` | required | Database connection. |

###### Raises

- `NotImplementedError`: unified tuning is not supported by the platform.
- `ValueError`: the configuration is invalid for this platform.

##### `benchbox.platforms.base.tuning_config.TuningConfigMixin.get_effective_tuning_configuration`

<span id="benchbox.platforms.base.tuning_config.TuningConfigMixin.get_effective_tuning_configuration"></span>

```python
def get_effective_tuning_configuration() -> UnifiedTuningConfiguration | None: ...
```

Gets the effective tuning configuration.

###### Returns

The unified tuning configuration, or `None` if no tuning is configured.

##### `benchbox.platforms.base.tuning_config.TuningConfigMixin.validate_tuning_configuration_for_platform`

<span id="benchbox.platforms.base.tuning_config.TuningConfigMixin.validate_tuning_configuration_for_platform"></span>

```python
def validate_tuning_configuration_for_platform() -> list[str]: ...
```

Validates the current tuning configuration against this platform's capabilities.

###### Returns

`list[str]`: List of validation error messages (empty if there are no errors).

##### `benchbox.platforms.base.tuning_config.TuningConfigMixin.validate_tuning_configuration`

<span id="benchbox.platforms.base.tuning_config.TuningConfigMixin.validate_tuning_configuration"></span>

```python
def validate_tuning_configuration(unified_config: UnifiedTuningConfiguration) -> list[str]: ...
```

Validates a unified tuning configuration against platform capabilities.

###### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `unified_config` | `UnifiedTuningConfiguration` | required | The unified tuning configuration to validate. |

###### Returns

`list[str]`: List of validation error messages (empty if all are valid).

##### `benchbox.platforms.base.tuning_config.TuningConfigMixin.save_tuning_metadata`

<span id="benchbox.platforms.base.tuning_config.TuningConfigMixin.save_tuning_metadata"></span>

```python
def save_tuning_metadata(connection: Any) -> bool: ...
```

Saves tuning metadata to the database for future validation.

###### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `connection` | `Any` | required | Database connection. |

###### Returns

`bool`: `True` if the metadata was saved successfully, `False` otherwise. On a platform with no metadata store (Athena) it returns `False` without raising, and the run's applied-tuning ledger records `metadata_store: unsupported`.

##### `benchbox.platforms.base.tuning_config.TuningConfigMixin.platform_name`

<span id="benchbox.platforms.base.tuning_config.TuningConfigMixin.platform_name"></span>

**Kind:** attribute

Host-provided platform name (`str`), used in diagnostics and capability selection. This mixin supplies no default.

##### `benchbox.platforms.base.tuning_config.TuningConfigMixin.canonical_platform_type`

<span id="benchbox.platforms.base.tuning_config.TuningConfigMixin.canonical_platform_type"></span>

**Kind:** attribute

Host-provided canonical platform identifier (`str`), passed to tuning configuration validation. This mixin supplies no default.

##### `benchbox.platforms.base.tuning_config.TuningConfigMixin.logger`

<span id="benchbox.platforms.base.tuning_config.TuningConfigMixin.logger"></span>

**Kind:** attribute

Holds the adapter logger used for lifecycle and diagnostic messages.

##### `benchbox.platforms.base.tuning_config.TuningConfigMixin.tuning_enabled`

<span id="benchbox.platforms.base.tuning_config.TuningConfigMixin.tuning_enabled"></span>

**Kind:** attribute

Host-provided flag (`bool`) that enables tuning application and metadata persistence. This mixin supplies no default.

#### SortedIngestionMixin

`SortedIngestionMixin` (in `benchbox.platforms.base.sorted_ingestion`) is a base class of `PlatformAdapter`. `PlatformAdapter` and every adapter inherit these members.

##### `benchbox.platforms.base.sorted_ingestion.SortedIngestionMixin.get_sorted_ingestion_capability`

<span id="benchbox.platforms.base.sorted_ingestion.SortedIngestionMixin.get_sorted_ingestion_capability"></span>

```python
def get_sorted_ingestion_capability() -> dict[str, Any]: ...
```

Returns sorted-ingestion capability metadata for the current platform.

##### `benchbox.platforms.base.sorted_ingestion.SortedIngestionMixin.resolve_sorted_ingestion_strategy`

<span id="benchbox.platforms.base.sorted_ingestion.SortedIngestionMixin.resolve_sorted_ingestion_strategy"></span>

```python
def resolve_sorted_ingestion_strategy() -> tuple[str, str]: ...
```

Resolves the sorted-ingestion mode and method with capability guardrails.

##### `benchbox.platforms.base.sorted_ingestion.SortedIngestionMixin.get_sorted_ingestion_metadata`

<span id="benchbox.platforms.base.sorted_ingestion.SortedIngestionMixin.get_sorted_ingestion_metadata"></span>

```python
def get_sorted_ingestion_metadata() -> dict[str, Any]: ...
```

Returns configured and resolved sorted-ingestion metadata for result reporting.

##### `benchbox.platforms.base.sorted_ingestion.SortedIngestionMixin.apply_ctas_sort`

<span id="benchbox.platforms.base.sorted_ingestion.SortedIngestionMixin.apply_ctas_sort"></span>

```python
def apply_ctas_sort(table_name: str, tuning_config: Any, connection: Any) -> bool: ...
```

Applies CTAS-based sorting after a table load when sorting is configured.

This shared implementation performs table-tuning lookup, sort-column extraction, identifier validation, dry-run SQL capture, and execution. Platform adapters enable CTAS sorting by overriding `_build_ctas_sort_sql()`. Adapters that return `None` are treated as unsupported and safely skipped.

##### `benchbox.platforms.base.sorted_ingestion.SortedIngestionMixin.platform_name`

<span id="benchbox.platforms.base.sorted_ingestion.SortedIngestionMixin.platform_name"></span>

**Kind:** attribute

Host-provided platform name (`str`), used in diagnostics and capability selection. This mixin supplies no default.

##### `benchbox.platforms.base.sorted_ingestion.SortedIngestionMixin.logger`

<span id="benchbox.platforms.base.sorted_ingestion.SortedIngestionMixin.logger"></span>

**Kind:** attribute

Holds the adapter logger used for lifecycle and diagnostic messages.

### Additional inherited members

#### PhaseTrackingMixin

`PhaseTrackingMixin` (in `benchbox.platforms.base.phase_tracking`) is a base class of `PlatformAdapter`. `PlatformAdapter` and every adapter inherit these members.

##### `benchbox.platforms.base.phase_tracking.PhaseTrackingMixin.get_table_row_count`

<span id="benchbox.platforms.base.phase_tracking.PhaseTrackingMixin.get_table_row_count"></span>

```python
def get_table_row_count(connection: Any, table: str) -> int: ...
```

Gets the row count for a table using a platform-specific API.

The default implementation uses the cursor pattern. Platforms such as BigQuery that do not support `cursor()` can override it to use their specific APIs.

###### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `connection` | `Any` | required | Database connection. |
| `table` | `str` | required | Table name. |

###### Returns

`int`: Row count as an integer, or 0 if it cannot be determined.

##### `benchbox.platforms.base.phase_tracking.PhaseTrackingMixin.logger`

<span id="benchbox.platforms.base.phase_tracking.PhaseTrackingMixin.logger"></span>

**Kind:** attribute

Holds the adapter logger used for lifecycle and diagnostic messages.

#### ResultCaptureMixin

`ResultCaptureMixin` (in `benchbox.platforms.base.result_capture`) is a base class of `PlatformAdapter`. `PlatformAdapter` and every adapter inherit these members.

##### `benchbox.platforms.base.result_capture.ResultCaptureMixin.get_normalized_result_metadata`

<span id="benchbox.platforms.base.result_capture.ResultCaptureMixin.get_normalized_result_metadata"></span>

```python
def get_normalized_result_metadata(
    *,
    connection: Any | None = None,
    platform_info: Mapping[str, Any] | None = None,
) -> dict[str, Any]: ...
```

Returns normalized runtime and deployment metadata for result construction.

Subclasses may override this optional hook to provide richer observed metadata. The default maps the legacy `get_platform_info()` output into conservative normalized blocks.

##### `benchbox.platforms.base.result_capture.ResultCaptureMixin.display_query_plan_if_enabled`

<span id="benchbox.platforms.base.result_capture.ResultCaptureMixin.display_query_plan_if_enabled"></span>

```python
def display_query_plan_if_enabled(connection: Any, query: str, query_id: str) -> None: ...
```

Displays the query execution plan if `show_query_plans` is enabled.

The display is suppressed while `capture_plans` is active: capture already runs EXPLAIN in the isolated post-measurement phase, so displaying here would issue EXPLAIN a second time. The logic is centralized here, rather than at each call site, so that new adapters cannot accidentally run EXPLAIN twice.

###### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `connection` | `Any` | required | Database connection. |
| `query` | `str` | required | SQL query text. |
| `query_id` | `str` | required | Query identifier. |

##### `benchbox.platforms.base.result_capture.ResultCaptureMixin.get_query_plan`

<span id="benchbox.platforms.base.result_capture.ResultCaptureMixin.get_query_plan"></span>

```python
def get_query_plan(connection: Any, query: str) -> str | None: ...
```

Gets the query execution plan for analysis.

Override this method in platform adapters to provide platform-specific plans.

###### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `connection` | `Any` | required | Database connection. |
| `query` | `str` | required | SQL query text. |

###### Returns

`str | None`: Query execution plan as a string, or `None` if not available.

##### `benchbox.platforms.base.result_capture.ResultCaptureMixin.get_query_plan_parser`

<span id="benchbox.platforms.base.result_capture.ResultCaptureMixin.get_query_plan_parser"></span>

```python
def get_query_plan_parser(): ...
```

Gets the query plan parser for this platform.

Override this method in platform adapters to provide a platform-specific parser.

###### Returns

`QueryPlanParser` instance, or `None` if not available.

##### `benchbox.platforms.base.result_capture.ResultCaptureMixin.capture_query_plan`

<span id="benchbox.platforms.base.result_capture.ResultCaptureMixin.capture_query_plan"></span>

```python
def capture_query_plan(connection: Any, query: str, query_id: str) -> tuple[Any, float]: ...
```

Captures a structured query plan using the platform-specific parser.

The method calls `get_query_plan()` to obtain the EXPLAIN output and parses it into a `QueryPlanDAG`. It returns timing information so that the overhead of capture is observable.

By default (`analyze_plans=False`), DuckDB uses plain `EXPLAIN (FORMAT JSON)`, which captures the estimated plan without re-executing the query. Set `analyze_plans=True` in the adapter config to opt into `EXPLAIN (ANALYZE, FORMAT JSON)`. That re-executes every captured SELECT once to include actual per-operator timing and cardinality, which costs roughly 2x wall-clock time for a `--capture-plans` run and perturbs cache state. The first time this method actually captures a plan with `analyze_plans` enabled in a run, it prints a one-time notice (guarded by `_analyze_plans_notice_printed`).

Plan fingerprints exclude timing and cardinality by design, so structural comparisons are unaffected by this setting. See the plan fingerprint stability contract in `benchbox/core/results/query_plan_models.py` for what fingerprint equality does and does not guarantee.

**Capture timing (pre-execution or post-execution) is a design decision.** All adapters capture the plan after the timed execution block (the post-execution plan). This is intentional and is the supported default:

- With `analyze_plans=True` (opt-in) it yields the actual plan that ran, including real per-operator timing and cardinality (EXPLAIN ANALYZE). This is the most useful artifact for profiling, at the cost of re-execution.
- The structural fingerprint is unaffected by pre-execution or post-execution timing, because it excludes costs and row estimates. A plan captured before execution and one captured after it hash to the same fingerprint as long as the planner's chosen shape is identical.
- A pre-execution capture mode (the plan as decided before any stats change) would be marginally more stable for cross-run regression detection, but it is not implemented. The stats-independence of the fingerprint already provides that stability, so a separate timing mode is unnecessary. If a true pre-execution plan is ever required, add an opt-in `capture_plan_timing: pre | post` config (default `post`) here rather than changing the default behavior.

**Multi-stream behavior (`stream_id`).** Plan capture is per query execution. In a multi-stream (concurrent) run, each stream executes and captures its own plan independently, so the result set contains one plan record per `(query_id, stream_id)`. The plans are not deduplicated or averaged, which preserves per-stream provenance. Because the fingerprint is structural, every stream that runs the same query against the same schema on the same engine version is expected to produce the same `plan_fingerprint`. The engine-dependent caveat in `query_plan_models.py` applies: some parsers, for example DuckDB, fold an estimated cardinality into the signature, which stays identical across streams as long as the cardinality estimate is stable. Only the execution stats (timing and per-operator cardinality) differ between streams. Consumers that want a single representative plan per query should deduplicate by `plan_fingerprint`. A fingerprint mismatch across streams of the same query indicates a genuine plan-shape (or estimate) divergence worth investigating.

###### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `connection` | `Any` | required | Database connection. |
| `query` | `str` | required | SQL query text. |
| `query_id` | `str` | required | Query identifier. |

###### Returns

Tuple of `(QueryPlanDAG | None, capture_time_ms)`.

##### `benchbox.platforms.base.result_capture.ResultCaptureMixin.execute_query_with_plan_capture`

<span id="benchbox.platforms.base.result_capture.ResultCaptureMixin.execute_query_with_plan_capture"></span>

```python
def execute_query_with_plan_capture(
    execute: Callable[..., dict[str, Any]],
    connection: Any,
    query: str,
    query_id: str,
    benchmark_type: str | None = None,
    scale_factor: float | None = None,
    validate_row_count: bool = True,
    stream_id: int | None = None,
) -> dict[str, Any]: ...
```

Runs one query through the shared executor, then merges plan capture.

Firebolt, Presto/Trino, PostgreSQL, SingleStore, and Doris each wrapped the shared cursor execution with the same two lines: delegate to the parent executor, then merge SUCCESS-guarded plan fields into the result. This method owns that idiom so the copies cannot drift. Each adapter keeps its thin `execute_query` override (and its platform docstring) and forwards its own `super().execute_query` as `execute`. Adapters with genuinely different semantics keep their bespoke overrides: Redshift (FAILED guard and display logic), pg_mooncake (transaction retry) and QuestDB (rewriter path).

###### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `execute` | `Callable[..., dict[str, Any]]` | required | Bound parent `execute_query` to delegate to. |
| `connection` | `Any` | required | Database connection. |
| `query` | `str` | required | SQL query text. |
| `query_id` | `str` | required | Query identifier. |
| `benchmark_type` | `str` or `None` | `None` | Benchmark family for validation. |
| `scale_factor` | `float` or `None` | `None` | Scale factor for validation. |
| `validate_row_count` | `bool` | `True` | Whether to validate row counts. |
| `stream_id` | `int` or `None` | `None` | Throughput stream identifier. |

###### Returns

`dict[str, Any]`: Query result dict with plan fields merged when captured.

##### `benchbox.platforms.base.result_capture.ResultCaptureMixin.validate_loaded_data`

<span id="benchbox.platforms.base.result_capture.ResultCaptureMixin.validate_loaded_data"></span>

```python
def validate_loaded_data(
    connection: Any,
    benchmark_type: str,
    scale_factor: float,
) -> ValidationResult: ...
```

Validates the database state after data loading using platform-specific methods.

###### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `connection` | `Any` | required | Database connection. |
| `benchmark_type` | `str` | required | Type of benchmark (for example `'tpcds'` or `'tpch'`). |
| `scale_factor` | `float` | required | Scale factor for the benchmark. |

###### Returns

`ValidationResult` with the database validation status.

##### `benchbox.platforms.base.result_capture.ResultCaptureMixin.validate_row_counts`

<span id="benchbox.platforms.base.result_capture.ResultCaptureMixin.validate_row_counts"></span>

```python
def validate_row_counts(connection: Any, expected_counts: dict[str, int]): ...
```

Validates actual row counts against expected counts.

###### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `connection` | `Any` | required | Database connection. |
| `expected_counts` | `dict[str, int]` | required | Dictionary mapping table names to expected row counts. |

###### Returns

`ValidationResult` with the row count comparison results.

##### `benchbox.platforms.base.result_capture.ResultCaptureMixin.logger`

<span id="benchbox.platforms.base.result_capture.ResultCaptureMixin.logger"></span>

**Kind:** attribute

Holds the adapter logger used for lifecycle and diagnostic messages.

#### TestDriversMixin

`TestDriversMixin` (in `benchbox.platforms.base.execution`) is a base class of `PlatformAdapter`. `PlatformAdapter` and every adapter inherit these members.

##### `benchbox.platforms.base.execution.TestDriversMixin.enable_dry_run`

<span id="benchbox.platforms.base.execution.TestDriversMixin.enable_dry_run"></span>

```python
def enable_dry_run(connection: Any = None) -> None: ...
```

Enables dry-run mode for SQL capture without execution.

##### `benchbox.platforms.base.execution.TestDriversMixin.disable_dry_run`

<span id="benchbox.platforms.base.execution.TestDriversMixin.disable_dry_run"></span>

```python
def disable_dry_run(connection: Any = None) -> None: ...
```

Disables dry-run mode and returns to normal execution.

##### `benchbox.platforms.base.execution.TestDriversMixin.capture_sql`

<span id="benchbox.platforms.base.execution.TestDriversMixin.capture_sql"></span>

```python
def capture_sql(sql: str, operation_type: str = 'query', table_name: str | None = None) -> None: ...
```

Captures a SQL statement for dry-run mode.

###### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `sql` | `str` | required | The SQL statement to capture. |
| `operation_type` | `str` | `'query'` | Type of operation (query, ddl, dml and so on). |
| `table_name` | `str` or `None` | `None` | Associated table name, if applicable. |

##### `benchbox.platforms.base.execution.TestDriversMixin.get_captured_sql`

<span id="benchbox.platforms.base.execution.TestDriversMixin.get_captured_sql"></span>

```python
def get_captured_sql() -> dict[str, str]: ...
```

Returns the captured SQL statements as a dictionary for dry-run display.

###### Returns

`dict[str, str]`: Dictionary of `query_id` to SQL statements.

##### `benchbox.platforms.base.execution.TestDriversMixin.run_power_test`

<span id="benchbox.platforms.base.execution.TestDriversMixin.run_power_test"></span>

```python
def run_power_test(benchmark, **kwargs) -> dict[str, Any]: ...
```

Runs the TPC power test, which measures single-stream query performance.

The power test executes queries sequentially in a single stream to measure the database's ability to process complex analytical queries efficiently. It focuses on query optimization and execution performance.

###### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `benchmark` | not annotated | required | Benchmark instance with queries and data. |
| `**kwargs` | keyword arguments | optional | Configuration options for the power test: `query_timeout` (maximum time per query; default platform-specific), `query_subset` (list of specific queries to run; default all) and `validation` (whether to validate query results; default `True`). |

###### Returns

`dict[str, Any]`: Dictionary containing the power test results, with these keys:

- `test_type`: `"power"`.
- `total_execution_time`: total time for all queries, in seconds.
- `query_count`: number of queries executed.
- `successful_queries`: number of successful query executions.
- `failed_queries`: number of failed query executions.
- `query_results`: list of individual query execution results.
- `geometric_mean`: geometric mean of query execution times.
- `validation_status`: overall validation result status.

##### `benchbox.platforms.base.execution.TestDriversMixin.run_throughput_test`

<span id="benchbox.platforms.base.execution.TestDriversMixin.run_throughput_test"></span>

```python
def run_throughput_test(benchmark, **kwargs) -> dict[str, Any]: ...
```

Runs the TPC throughput test, which measures concurrent multi-stream performance.

The throughput test executes multiple concurrent query streams to measure the database's ability to handle concurrent analytical workloads. It focuses on scalability and concurrent query processing.

###### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `benchmark` | not annotated | required | Benchmark instance with queries and data. |
| `**kwargs` | keyword arguments | optional | Configuration options for the throughput test: `stream_count` (number of concurrent query streams; default platform-specific), `query_timeout` (maximum time per query; default platform-specific), `warmup_runs` (number of warmup iterations per stream; default 1), `measurement_runs` (number of measurement iterations; default 1) and `validation` (whether to validate query results; default `True`). |

###### Returns

`dict[str, Any]`: Dictionary containing the throughput test results, with these keys:

- `test_type`: `"throughput"`.
- `stream_count`: number of concurrent streams used.
- `total_execution_time`: total wall-clock time, in seconds.
- `aggregate_query_time`: sum of all query execution times.
- `queries_per_hour`: throughput metric (queries per hour).
- `stream_results`: list of individual stream execution results.
- `validation_status`: overall validation result status.

##### `benchbox.platforms.base.execution.TestDriversMixin.run_maintenance_test`

<span id="benchbox.platforms.base.execution.TestDriversMixin.run_maintenance_test"></span>

```python
def run_maintenance_test(benchmark, **kwargs) -> dict[str, Any]: ...
```

Runs the TPC maintenance test, which measures data modification performance.

The maintenance test executes data modification operations (INSERT, UPDATE, DELETE) to measure the database's ability to handle data maintenance workloads while concurrent query streams are running.

###### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `benchmark` | not annotated | required | Benchmark instance with maintenance functions and data. |
| `**kwargs` | keyword arguments | optional | Configuration options for the maintenance test: `maintenance_operations` (list of operations to perform; default all), `concurrent_streams` (number of concurrent query streams; default 1), `batch_size` (size of maintenance operation batches; default platform-specific) and `validation` (whether to validate results; default `True`). |

###### Returns

`dict[str, Any]`: Dictionary containing the maintenance test results, with these keys:

- `test_type`: `"maintenance"`.
- `operations_executed`: number of maintenance operations performed.
- `total_execution_time`: total time for all operations, in seconds.
- `operation_results`: list of individual operation execution results.
- `concurrent_query_impact`: impact on concurrent query performance.
- `data_integrity_status`: data consistency validation results.
- `validation_status`: overall validation result status.

#### VerbosityMixin

`VerbosityMixin` (in `benchbox.utils.verbosity`) is a base class of `PlatformAdapter`. `PlatformAdapter` and every adapter inherit these members.

##### `benchbox.utils.verbosity.VerbosityMixin.logger`

<span id="benchbox.utils.verbosity.VerbosityMixin.logger"></span>

**Kind:** property

Returns the logger configured for the verbosity mixin consumer.

##### `benchbox.utils.verbosity.VerbosityMixin.apply_verbosity`

<span id="benchbox.utils.verbosity.VerbosityMixin.apply_verbosity"></span>

```python
def apply_verbosity(settings: VerbositySettings) -> None: ...
```

Applies verbosity settings to the mixin consumer.

##### `benchbox.utils.verbosity.VerbosityMixin.verbosity_settings`

<span id="benchbox.utils.verbosity.VerbosityMixin.verbosity_settings"></span>

**Kind:** property

Returns the current verbosity settings.

##### `benchbox.utils.verbosity.VerbosityMixin.log_verbose`

<span id="benchbox.utils.verbosity.VerbosityMixin.log_verbose"></span>

```python
def log_verbose(message: str) -> None: ...
```

Logs only when verbose mode is enabled.

##### `benchbox.utils.verbosity.VerbosityMixin.log_notice`

<span id="benchbox.utils.verbosity.VerbosityMixin.log_notice"></span>

```python
def log_notice(message: str) -> None: ...
```

Logs a default-visible operational notice while respecting quiet mode.

##### `benchbox.utils.verbosity.VerbosityMixin.log_very_verbose`

<span id="benchbox.utils.verbosity.VerbosityMixin.log_very_verbose"></span>

```python
def log_very_verbose(message: str) -> None: ...
```

Logs only when very-verbose mode is enabled.

##### `benchbox.utils.verbosity.VerbosityMixin.log_operation_start`

<span id="benchbox.utils.verbosity.VerbosityMixin.log_operation_start"></span>

```python
def log_operation_start(operation: str, details: str = '') -> None: ...
```

Logs the start of a named adapter operation, including optional details.

##### `benchbox.utils.verbosity.VerbosityMixin.log_operation_complete`

<span id="benchbox.utils.verbosity.VerbosityMixin.log_operation_complete"></span>

```python
def log_operation_complete(
    operation: str,
    duration: float | None = None,
    details: str = '',
) -> None: ...
```

Logs completion of a named adapter operation with optional duration and details.

##### `benchbox.utils.verbosity.VerbosityMixin.log_debug_info`

<span id="benchbox.utils.verbosity.VerbosityMixin.log_debug_info"></span>

```python
def log_debug_info(context: str = 'Debug') -> None: ...
```

Logs comprehensive debug information, including version details.

##### `benchbox.utils.verbosity.VerbosityMixin.log_error_with_debug_info`

<span id="benchbox.utils.verbosity.VerbosityMixin.log_error_with_debug_info"></span>

```python
def log_error_with_debug_info(error: Exception, context: str = 'Error') -> None: ...
```

Logs an error with comprehensive debug information.

##### `benchbox.utils.verbosity.VerbosityMixin.log_version_warning`

<span id="benchbox.utils.verbosity.VerbosityMixin.log_version_warning"></span>

```python
def log_version_warning() -> None: ...
```

Logs version consistency warnings, if any exist.

##### `benchbox.utils.verbosity.VerbosityMixin.verbose_level`

<span id="benchbox.utils.verbosity.VerbosityMixin.verbose_level"></span>

**Kind:** attribute

Current integer verbosity level. Defaults to `0`. `apply_verbosity` copies the level of the supplied settings.

##### `benchbox.utils.verbosity.VerbosityMixin.verbose_enabled`

<span id="benchbox.utils.verbosity.VerbosityMixin.verbose_enabled"></span>

**Kind:** attribute

Flag controlling verbose logging. Defaults to `False` and is copied from the supplied settings.

##### `benchbox.utils.verbosity.VerbosityMixin.very_verbose`

<span id="benchbox.utils.verbosity.VerbosityMixin.very_verbose"></span>

**Kind:** attribute

Flag controlling detailed logging. Defaults to `False`. CLI flag normalization enables it at level 2 or higher unless quiet.

##### `benchbox.utils.verbosity.VerbosityMixin.quiet`

<span id="benchbox.utils.verbosity.VerbosityMixin.quiet"></span>

**Kind:** attribute

Quiet-output flag. Defaults to `False`. CLI flag normalization makes quiet take precedence over verbosity.

##### `benchbox.utils.verbosity.VerbosityMixin.verbose`

<span id="benchbox.utils.verbosity.VerbosityMixin.verbose"></span>

**Kind:** attribute

Legacy verbose-output flag. Defaults to `False`. `apply_verbosity` sets it to `settings.verbose_enabled and not settings.quiet`.
