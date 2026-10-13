(cli-configuration)=
# Configuration

```{tags} reference, cli, configuration
```

BenchBox supports configuration through multiple sources with the following precedence:

1. **Command-line arguments** (highest priority)
2. **Environment variables**
3. **Configuration files**
4. **Default values** (lowest priority)

## Configuration File Format

BenchBox uses YAML configuration files. Default location: `~/.benchbox/config.yaml`

Example configuration:
```yaml
output:
  compression:
    enabled: true
    type: zstd
    level: 3
  formats:
    - json
    - csv

platforms:
  databricks:
    enabled: true
    warehouse_id: "abc123"

  bigquery:
    enabled: true
    project_id: "my-project"

tuning:
  default_mode: notuning
  enable_constraints: false
```

## Platform Configuration

Each platform can have specific configuration options:

**Databricks:**
```yaml
platforms:
  databricks:
    warehouse_id: "warehouse-id"
    catalog: "main"
    schema: "benchbox"
```

**BigQuery:**
```yaml
platforms:
  bigquery:
    project_id: "my-project"
    dataset: "benchbox"
    location: "US"
```

**Snowflake:**
```yaml
platforms:
  snowflake:
    account: "account-name"
    warehouse: "COMPUTE_WH"
    database: "BENCHBOX"
    schema: "PUBLIC"
```

(cli-env-vars)=
## Environment Variables

BenchBox recognizes these environment variables:

### Core Settings

These variables override the corresponding settings in the configuration file (equivalent to editing `~/.benchbox/config.yaml`):

| Variable | Config Path | Type | Default | Description |
|----------|-------------|------|---------|-------------|
| `BENCHBOX_DATABASE_PREFERRED` | `database.preferred` | string | `duckdb` | Preferred database platform |
| `BENCHBOX_SCALE_FACTOR` | `benchmarks.default_scale` | float | `0.01` | Default scale factor |
| `BENCHBOX_VERBOSE` | `execution.verbose` | boolean | `true` | Enable verbose output (`true`/`1`/`yes`/`on`) |
| `BENCHBOX_MAX_WORKERS` | `execution.max_workers` | integer | `4` | Maximum parallel worker threads |
| `BENCHBOX_TUNING_CONFIG` | `tuning.default_config_file` | string | - | Path to tuning configuration file. Used when `--tuning tuned` resolves with no explicit file (see [Tuning Commands](tuning.md)) |
| `BENCHBOX_OUTPUT_DIR` | runtime path resolver | string | worktree-anchored `benchmark_runs` root | Runtime output root override; it is not exposed as `load_config().output.directory` |
| `BENCHBOX_MEMORY_LIMIT_GB` | `execution.memory_limit_gb` | integer | `0` (auto) | Memory limit in GB; `0` means no limit |

**Boolean parsing:** `true`, `1`, `yes`, `on` (case-insensitive) are treated as true.

### General Settings

- `BENCHBOX_NON_INTERACTIVE=true`: Enable non-interactive mode
- `BENCHBOX_NO_COMPRESSION=true`: Disable data compression
- `BENCHBOX_CONFIG_PATH=/path/to/config.yaml`: Custom config file location

### Advanced Settings

These variables control lower-level behaviors, useful for CI, offline environments, or advanced workflows:

| Variable | Description |
|----------|-------------|
| `BENCHBOX_DATA_DIR` | Override the default local data directory for generated benchmark files |
| `BENCHBOX_CACHE_DIR` | Override the cache directory for DataFrame benchmark data |
| `BENCHBOX_TUNING_PATH` | Override the tuning file search root; BenchBox looks for `{platform}/{benchmark}_tuned.yaml` under this directory |
| `BENCHBOX_QUERY_VALIDATION_MODE` | TPC-DS query validation mode: `exact`, `loose`, `range`, or `skip` (default: `skip`) |
| `BENCHBOX_NO_DOWNLOAD` | Set to `1` or `true` to disable automatic download of expected-answer files; useful in offline/airgapped environments |
| `BENCHBOX_ANSWERS_URL` | Override the base URL for downloading expected-answer archives (default: GitHub releases) |
| `BENCHBOX_ENABLE_EXPERIMENTAL` | Set to `1` or `true` to show experimental platforms in the platform registry |
| `BENCHBOX_DATA_ORG_MAX_IN_MEMORY_BYTES` | Maximum bytes held in memory when sorting generated data files (integer; default: 512 MB) |

### Platform Authentication

**Databricks:**
- `DATABRICKS_TOKEN`: Authentication token
- `DATABRICKS_HOST`: Workspace URL

**BigQuery:**
- `GOOGLE_APPLICATION_CREDENTIALS`: Service account key file path

**Snowflake:**
- `SNOWFLAKE_USER`: Username
- `SNOWFLAKE_PASSWORD`: Password
- `SNOWFLAKE_ACCOUNT`: Account identifier

**Redshift:**
- `AWS_ACCESS_KEY_ID`: AWS access key
- `AWS_SECRET_ACCESS_KEY`: AWS secret key

**ClickHouse:**
- `CLICKHOUSE_HOST`: Server hostname
- `CLICKHOUSE_USER`: Username
- `CLICKHOUSE_PASSWORD`: Password

(cli-platform-options)=
## Platform-Specific Options

Each platform supports specific options via `--platform-option KEY=VALUE`:

### Universal Keys (All Platforms)

These keys are available for every platform:

| Key | Description |
|-----|-------------|
| `driver_version` | Pin the Python driver package to a specific version (e.g. `1.2.0`). Useful for reproducing results or testing a specific connector release. |
| `driver_auto_install` | When `true`, automatically installs the requested `driver_version` via uv if the package is not already present. |

```{note}
`uv run benchbox run` syncs the environment to `uv.lock` before Python starts, which
can silently revert a version you installed manually. Use `driver_version` +
`driver_auto_install=true` or `uv run --with "pkg==X"` to reliably test a specific
version. See {ref}`driver-version-management` for the full guide.
```

Example: pin DuckDB driver and auto-install it:
```bash
benchbox run --platform duckdb --benchmark tpch \
  --platform-option driver_version=1.2.0 \
  --platform-option driver_auto_install=true
```

Example: pin Snowflake connector version:
```bash
benchbox run --platform snowflake --benchmark tpch \
  --platform-option driver_version=3.12.0 \
  --platform-option driver_auto_install=true \
  --platform-option account=xy12345.us-east-1 \
  --platform-option warehouse=COMPUTE_WH
```

### Compute Settings

On platforms that run on a named compute resource, `compute_resource` names
it and `compute_size` sets its size. BenchBox takes each setting from the first
place that has it:

1. `--platform-option` on the command line;
2. the saved credential profile written by `benchbox setup`;
3. the option's default.

A value on the command line overrides the saved profile. Two spellings that
disagree in the same place, such as `warehouse=A` and `compute_resource=B`
on one command line, stop the run before any work and name both values.

On Redshift, `compute_resource` names the workgroup when the host is a
Serverless endpoint (`*.redshift-serverless.amazonaws.com`) and the cluster
when it is a provisioned endpoint (`*.redshift.amazonaws.com`). With any other
host, use `workgroup_name` or `cluster_identifier`. On Databricks,
`compute_resource` names the SQL warehouse and sets the HTTP path to
`/sql/1.0/warehouses/<id>`.

The `--platform name:mode` deployment selector follows the same rule: it
overrides a deployment key, such as LakeSail `sail_mode`, in the saved profile.

### Athena Spark Runtime Version

On Athena Spark, the Spark-enabled workgroup sets the engine version. A session
cannot choose it, so BenchBox never sends a version when it starts one.

| Key | Description |
|-----|-------------|
| `runtime_version` | Spark engine version you expect the workgroup to run (e.g. `PySpark engine version 3`). Results record it as `runtime_version` with `runtime_version_source: requested`. Change the version on the workgroup itself. `engine_version` is a deprecated alias, removed in 0.6.0. |

```bash
benchbox run --platform athena-spark --benchmark tpch \
  --platform-option compute_resource=my-spark-workgroup \
  --platform-option s3_staging_dir=s3://my-bucket/benchbox \
  --platform-option "runtime_version=PySpark engine version 3"
```

### ClickHouse Options

- `mode=local`: Use local ClickHouse instance
- `secure=true`: Enable TLS encryption
- `port=9000`: Custom port number
- `database=default`: Target database name

Example:
```bash
benchbox run --platform clickhouse-local --benchmark tpch \
  --platform-option mode=local \
  --platform-option secure=true \
  --platform-option port=9440
```

### View Platform Details

Use `benchbox platforms status` to see platform information and capabilities:

```bash
benchbox platforms status clickhouse
benchbox platforms status databricks
```

(cli-config-sections)=
## Configuration Sections Reference

Complete reference for all settings in `~/.benchbox/config.yaml` (or `./benchbox.yaml`). Unset values fall back to the defaults shown here.

### `system`

System profiling and detection settings.

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `auto_profile` | boolean | `true` | Automatically detect system capabilities on startup |
| `save_profile` | boolean | `true` | Persist the detected system profile to disk |
| `profile_cache_hours` | integer | `24` | Hours to cache the system profile before re-detecting |

### `database`

Database connection settings.

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `preferred` | string | `duckdb` | Default platform when none is specified on the CLI |
| `connection_timeout` | integer | `30` | Connection timeout in seconds |
| `auto_detect` | boolean | `true` | Automatically detect available database platforms |

### `benchmarks`

Default benchmark execution parameters.

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `default_scale` | float | `0.01` | Default scale factor |
| `timeout_minutes` | integer | `60` | Maximum benchmark execution time |
| `max_memory_gb` | integer | `8` | Maximum memory allocation hint in GB |
| `continue_on_error` | boolean | `false` | Continue running remaining queries when one fails |

### `output`

Result output and export settings.

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `formats` | list | `[json, console]` | Output format list |
| `directory` | string | runtime-resolved | Results directory; resolved from the runtime output root rather than a legacy config key |
| `timestamp_format` | string | `%Y%m%d_%H%M%S` | Timestamp format for result filenames |
| `submit_to_service` | boolean | `false` | Legacy placeholder; use `benchbox submit --service` for hosted upload |
| `service_url` | string | `https://api.benchbox.dev/v1` | Default hosted results service URL placeholder |
| `compression.enabled` | boolean | `true` | Enable result file compression |
| `compression.type` | string | `zstd` | Compression algorithm (`zstd`, `gzip`) |
| `compression.level` | integer | `null` | Compression level; `null` uses the algorithm default |

### `execution`

Performance and execution control settings.

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `parallel_queries` | boolean | `false` | Execute queries in parallel |
| `max_workers` | integer | `4` | Maximum worker threads for parallel execution |
| `memory_limit_gb` | integer | `0` | Memory cap in GB; `0` means no limit (auto) |
| `verbose` | boolean | `true` | Enable verbose progress output |

**`execution.power_run`** - settings for multi-iteration power runs:

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `iterations` | integer | `3` | Number of measurement iterations per query |
| `warm_up_iterations` | integer | `1` | Warm-up iterations before measurement (not included in stats) |
| `timeout_per_iteration_minutes` | integer | `60` | Timeout for each iteration |
| `fail_fast` | boolean | `false` | Stop immediately when any iteration fails |
| `collect_metrics` | boolean | `true` | Collect resource metrics during execution |

**`execution.concurrent_queries`** - settings for throughput / concurrent-stream runs:

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `enabled` | boolean | `false` | Enable concurrent query streams |
| `max_concurrent` | integer | `2` | Number of concurrent query streams |
| `query_timeout_seconds` | integer | `300` | Per-query timeout in concurrent mode |
| `stream_timeout_seconds` | integer | unset | Timeout in seconds for each throughput stream in `benchbox run`. Unset keeps the benchmark default (3600 for TPC-H, 7200 for TPC-DS) |
| `cancel_on_timeout` | boolean | `false` | Signal a timed-out throughput stream to stop before its next query |
| `retry_failed_queries` | boolean | `true` | Retry failed queries in concurrent streams |
| `max_retries` | integer | `3` | Maximum retry attempts per query |

Only `stream_timeout_seconds` and `cancel_on_timeout` are read by `benchbox run`. The other settings are read only through `ExecutionConfigHelper`.

### `tuning`

Table tuning and optimization settings.

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `default_config_file` | string | `null` | Default tuning YAML file used when `--tuning tuned` finds no explicit file; see [Tuning Commands](tuning.md) for the full resolution order |
| `validate_on_load` | boolean | `true` | Validate tuning configurations when loaded |
| `allow_platform_incompatible` | boolean | `false` | Allow tuning configs that contain platform-incompatible directives |

---

Example full configuration file:

```yaml
system:
  profile_cache_hours: 48

database:
  preferred: duckdb

benchmarks:
  default_scale: 1.0
  timeout_minutes: 120
  continue_on_error: true

output:
  directory: ./results
  compression:
    enabled: true
    type: zstd

execution:
  max_workers: 8
  memory_limit_gb: 16
  power_run:
    iterations: 5
    warm_up_iterations: 2

tuning:
  default_config_file: ./tuning/my_tuning.yaml
```

## Related

- [Run Command](run.md) - Platform and tuning options during execution
- [Platforms Command](platforms.md) - Platform management and setup
- [Platform Documentation](../../platforms/index.md) - Detailed platform guides
