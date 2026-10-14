(cli-run)=
# `run` - Run Benchmarks

```{tags} reference, cli, intermediate
```

The primary command for executing benchmarks. Supports interactive mode, direct execution, and various execution phases.

## Basic Syntax

```bash
benchbox run [OPTIONS]
```

## Core Options

**Platform and Benchmark Selection:**
- `--platform TEXT`: Platform type
  - SQL platforms: `duckdb`, `sqlite`, `databricks`, `bigquery`, `snowflake`, `redshift`, `clickhouse`, `datafusion`, `polars`, `trino`, `presto`
  - DataFrame platforms: `polars-df`, `pandas-df`, `pyspark-df`, `datafusion-df` (see [DataFrame Platforms](../../platforms/dataframe.md))
- `--benchmark TEXT`: Benchmark name (tpch, tpcds, tpcdi, ssb, clickbench, h2odb, etc.)
- `--scale FLOAT`: Scale factor for data generation (default: 0.01)

**Execution Control:**
- `--phases TEXT`: Benchmark phases to run (default: "power")
  - Available phases:
    - `generate` - Generate benchmark data files
    - `load` - Load data into database
    - `warmup` - Run warmup queries (optional, prepares caches)
    - `power` - Sequential query execution (Power Test)
    - `throughput` - Concurrent query execution (Throughput Test)
    - `maintenance` - **Data modification operations (DATABASE RELOAD REQUIRED AFTER)** - See [TPC-H Maintenance](../../guides/tpc/tpc-h-official-guide.md#maintenance-test) or [TPC-DS Maintenance](../../guides/tpc/tpc-ds-official-guide.md#3-maintenance-test)
  - Use comma-separated list: `--phases generate,load,power`
  - **Warning:** Maintenance phase permanently modifies data; reload database before running power/throughput again
  - **Note:** the number of concurrent streams for the `throughput` phase is set
    with `--streams N` (an advanced option listed by
    `benchbox run --help-topic all`; `--concurrency N` is accepted as an
    alias). The deprecated `run-official` command also spells the setting
    `--streams` — see [Deprecated: `run-official`](#deprecated-run-official)
    below.
- `--force [MODE]`: Force regeneration of data (modes: `all`, `datagen`, `upload`, or `datagen,upload`)
- `--non-interactive`: Use defaults for all prompts (useful for automation)

**Output and Paths:**
- `--output TEXT`: Output directory (supports local paths and cloud storage: s3://, gs://, abfss://)
- `--dry-run OUTPUT_DIR`: Preview configuration and queries without execution

**Query Configuration:**
- `--seed INTEGER`: An RNG seed for query parameter generation (power/throughput tests)
- `--queries TEXT`: Run specific queries in specified order (comma-separated IDs: "1,6,17")
  - **Constraints**:
    - Max 100 queries per run
    - Alphanumeric IDs only (letters, numbers, dash, underscore; max 20 chars each)
    - Only works with `power` and `standard` phases (errors if used with ONLY incompatible phases)
    - Duplicates automatically removed
    - Order preserved as specified
  - **TPC Compliance**: Breaks official TPC-H/TPC-DS compliance (overrides stream permutations)
  - **Valid Ranges**: TPC-H (1-22), TPC-DS (1-99), SSB (1-13)
  - **Examples**:
    ```bash
    benchbox run --platform duckdb --benchmark tpch --queries "Q6"

    benchbox run --platform postgres --benchmark tpch --queries "Q1,Q6,Q17"

    benchbox run --platform duckdb --benchmark tpcds --queries "Q42" --verbose --phases power
    ```
    The first example runs a single query for debugging. The second runs several queries in the order given. The third adds verbose logging for development.

**Tuning and Optimization:**
- `--tuning TEXT`: Tuning mode (default: "notuning")
  - `tuned`: Enable optimizations (constraints for SQL, parallelism for DataFrame)
  - `notuning`: Disable for baseline comparison
  - `auto`: Auto-detect optimal settings based on system profile
  - Path to config file: Custom tuning configuration (YAML)

**Data Compression (Advanced):**
- `--compression TEXT`: Compression configuration (e.g., `zstd`, `zstd:9`, `gzip:6`, `none`)
  - Format: `TYPE` or `TYPE:LEVEL`
  - Types: `zstd` (default), `gzip`, `none`
  - Levels: 1-9 for gzip, 1-22 for zstd

**Validation (Advanced):**
- `--validation TEXT`: Validation mode
  - `exact`: Exact row count validation (default)
  - `loose`: Loose validation (±5% tolerance)
  - `range`: Min/max bounds validation
  - `disabled`: No validation
  - `full`: All validation checks enabled (preflight, postgen, postload, platform check)
  - `preflight`, `postgen`, `postload`, `check-platforms`: Individual validation types
- `--strict-translation`: Fail instead of falling back when SQL dialect translation cannot produce target SQL
- `--client-region TEXT`: Attested client cloud region (e.g. `us-east-1`), recorded in `environment.client_link` with `source: "cli_option"`
- `--client-cloud TEXT`: Attested client cloud provider (`aws`, `gcp`, `azure`)
- `--no-link-probe`: Disable the post-benchmark statement overhead probe (6 metered `SELECT 1` statements on billable warehouses)

**Platform-Specific Configuration:**
- `--platform-option KEY=VALUE`: Platform-specific option (can be used multiple times)
- Use `benchbox platforms status <name>` to view platform details
- Universal keys available on all platforms:
  - `driver_version` - pin the Python driver package version (e.g. `1.2.0`)
  - `driver_auto_install` - auto-install the requested driver via uv if missing (`true`/`false`)
- Athena Spark only: `runtime_version` - record the Spark engine version you expect (e.g. `PySpark engine version 3`); the workgroup sets the version and BenchBox records this value as requested

**Benchmark-Specific Configuration:**
- `--benchmark-option KEY=VALUE`: Benchmark-specific option (can be used multiple times)
- Run `benchbox run --help-topic benchmarks` to see all available options per benchmark
- Selected benchmarks and their options:

  | Benchmark | Option | Default | Description |
  |-----------|--------|---------|-------------|
  | `nyctaxi` | `taxi_types` | - | Taxi types to load: `yellow`, `green`, `hvfhv` (comma-separated) |
  | `nyctaxi` | `year` | `2019` | Year of TLC data (2019-2025) |
  | `nyctaxi` | `months` | - | Months to include, comma-separated (1-12) |
  | `nyctaxi` | `seed` | - | Random seed for reproducibility |
  | `nyctaxi` | `force_regenerate` | - | Force data regeneration (`true`/`false`) |
  | `tpch_skew` | `skew_preset` | `moderate` | Skew distribution: `none`, `light`, `moderate`, `heavy`, `extreme`, `realistic` |
  | `tpch_skew` | `force_regenerate` | - | Force data regeneration |
  | `tsbs_devops` | `num_hosts` | - | Number of simulated hosts |
  | `tsbs_devops` | `duration_days` | - | Duration in days for data generation |
  | `tsbs_devops` | `interval_seconds` | `10` | Measurement interval in seconds |
  | `tsbs_devops` | `start_time` | - | Start time ISO format (e.g. `2019-01-01T00:00:00`) |
  | `tsbs_devops` | `seed` | - | Random seed for reproducibility |
  | `tsbs_devops` | `force_regenerate` | - | Force data regeneration (`true`/`false`) |
  | `datavault` | `hash_algorithm` | `md5` | Hash algorithm for hub/link keys: `md5`, `sha256` |
  | `datavault` | `record_source` | `TPCH` | Record source identifier for audit columns |
  | `datavault` | `force_regenerate` | - | Force data regeneration (`true`/`false`) |
  | `flightdata` | `end_year` | latest | Last year of flight data to include |
  | `flightdata` | `seed` | - | Random seed for reproducibility |
  | `flightdata` | `force_regenerate` | - | Force data regeneration (`true`/`false`) |
  | `joinorder` | `queries_dir` | - | Directory containing custom query files; defaults to the embedded 113 canonical JOB queries |
  | `joinorder` | `force_regenerate` | - | Clear and refetch verified canonical data (`true`/`false`); `joinorder` still requires `--scale 1` |
  | `tpcdi` | `enable_parallel` | `false` | Enable parallel processing for ETL |
  | `tpcdi` | `max_workers` | - | Maximum number of parallel workers |
  | `tpcds_obt` | `dimension_mode` | `full` | OBT dimension mode: `full`, `minimal` |
  | `tpcds_obt` | `channels` | - | Sales channels: `store`, `web`, `catalog` (comma-separated) |
  | `tpcds_obt` | `output_format` | `parquet` | Output format: `dat`, `parquet` |
  | `tpcds_obt` | `tpcds_source_dir` | - | Directory containing TPC-DS source data |
  | `tpcds_obt` | `force_regenerate` | - | Force data regeneration (`true`/`false`) |
  | `vector_search` | `dimensions` | `1536` | Embedding vector dimensions |

  Most options also accept a hyphenated alias (e.g. `taxi-types` for `taxi_types`).

**Logging and Output:**
- `--verbose`, `-v`: Enable verbose logging (use `-vv` for very verbose)
- `--quiet`, `-q`: Suppress all output (overrides verbose flags)

**Help System:**
- `-h, --help`: Show common options
- `--help-topic all`: Show all options including advanced
- `--help-topic examples`: Show categorized usage examples
- `--help-topic benchmarks`: Show benchmark-specific options (see `--benchmark-option` below)

## Usage Examples

### Basic Local Benchmark

```bash
benchbox run --platform duckdb --benchmark tpch --scale 0.01

benchbox run --platform duckdb --benchmark tpch
```

The first command is a simple TPC-H benchmark on DuckDB. The second uses the default settings.

### Cloud Platform Examples

```bash
benchbox run --platform databricks --benchmark tpch --scale 0.1 \
  --output dbfs:/Volumes/workspace/raw/benchmarks/

benchbox run --platform bigquery --benchmark tpcds --scale 0.01 \
  --verbose --phases power

benchbox run --platform snowflake --benchmark tpch --scale 1 \
  --tuning tuned
```

The examples use Databricks with a custom output location, BigQuery with verbose logging, and Snowflake with tuning enabled.

### DataFrame Platform Examples

```bash
benchbox run --platform polars-df --benchmark tpch --scale 1 \
  --tuning auto

benchbox run --platform pandas-df --benchmark tpch --scale 0.1 \
  --platform-option dtype_backend=pyarrow

benchbox run --platform pyspark-df --benchmark tpch --scale 1 \
  --platform-option driver_memory=8g

benchbox run --platform polars --benchmark tpch --scale 0.1
benchbox run --platform polars-df --benchmark tpch --scale 0.1
```

The examples use Polars DataFrame with auto-tuning, Pandas DataFrame with the PyArrow backend, and PySpark DataFrame in local mode. The last two commands compare SQL mode (`polars`) against DataFrame mode (`polars-df`) on the same workload.

### Advanced Phase Control

```bash
benchbox run --benchmark tpch --scale 0.1 --phases generate \
  --output ./tpch-data

benchbox run --platform duckdb --benchmark tpch --scale 0.1 \
  --phases load

benchbox run --platform duckdb --benchmark tpcds --scale 0.01 \
  --phases power,throughput --seed 42
```

The commands, in order, generate data only, load data into the database, and run the full TPC-DS power and throughput test.

### Dry Run and Preview

```bash
benchbox run --platform duckdb --benchmark tpch --scale 0.1 \
  --dry-run ./preview

benchbox run --platform snowflake --benchmark tpcds \
  --phases power --dry-run ./debug --seed 7
```

The first command previews the configuration without running it. The second previews with a specific seed so the queries are reproducible.

### Benchmark-Specific Options

```bash
benchbox run --platform duckdb --benchmark nyctaxi \
  --benchmark-option taxi_types=yellow,green \
  --benchmark-option year=2022

benchbox run --platform duckdb --benchmark tpch_skew --scale 1 \
  --benchmark-option skew_preset=heavy

benchbox run --platform duckdb --benchmark tsbs_devops \
  --benchmark-option num_hosts=100 \
  --benchmark-option interval_seconds=30

benchbox run --platform duckdb --benchmark tpcdi \
  --benchmark-option enable_parallel=true \
  --benchmark-option max_workers=4

benchbox run --platform duckdb --benchmark datavault \
  --benchmark-option hash_algorithm=sha256

benchbox run --help-topic benchmarks
```

The examples, in order:

- NYC Taxi: load only yellow and green trips from 2022.
- TPC-H Skew: use the heavy skew distribution.
- TSBS Devops: set a custom host count and interval.
- TPC-DI: enable parallel ETL with 4 workers.
- Data Vault: use SHA-256 hashing.
- The last command lists all benchmark options.

### Platform-Specific Options

```bash
benchbox run --platform clickhouse-server --benchmark tpch \
  --platform-option secure=true \
  --platform-option port=9440

benchbox run --platform duckdb --benchmark tpch \
  --platform-option driver_version=1.2.0 \
  --platform-option driver_auto_install=true

benchbox run --platform athena-spark --benchmark tpch --scale 1.0 \
  --platform-option compute_resource=my-spark-workgroup \
  --platform-option s3_staging_dir=s3://my-bucket/benchbox \
  --platform-option "runtime_version=PySpark engine version 3"

benchbox platforms status clickhouse
```

The examples, in order:

- ClickHouse server over TLS on port 9440. The platform name picks the deployment (`clickhouse-local` for embedded chDB); the `mode` option is a deprecated alias.
- Pin the driver version, which works on any platform.
- Athena Spark: run in a Spark-enabled workgroup and record the engine version it is configured with.
- The last command shows platform details and capabilities.

## Table Storage Options

**Table Mode:**
- `--table-mode [native|external]`: How tables are created (default: `native`)
  - `native`: Standard materialized tables in the target database
  - `external`: External table or view references (e.g., Parquet-backed)
  - **Note:** Incompatible with `--tuning tuned` when set to `external`

**Table Format:**
- `--table-format TEXT`: Open table format for data files
  - Format: `FORMAT[:COMPRESSION[:PARTITION:COLS...]]`
  - Examples: `parquet`, `delta:snappy`, `iceberg:zstd`, `vortex`
  - Supported formats depend on the platform adapter

**Pre-sorted Data:**
- `--presort [parquet-sorted|delta-sorted|iceberg-sorted]`: Pre-sort data into open table formats
  - Only valid for TPC-H and TPC-DS benchmarks
  - TPC-H sorts on `lineitem.l_shipdate`; TPC-DS sorts on `store_sales.ss_sold_date_sk`

## Cloud Sorted Ingestion Options

These flags control cloud data-organization strategy selection across supported cloud warehouses. They are advanced options - use `--help-topic all` to see them in the CLI help.

- `--sorted-ingestion-mode [off|auto|force]`: Cloud sorted-ingestion strategy mode
- `--sorted-ingestion-method [auto|ctas|z_order|hilbert|liquid_clustering|vacuum_sort]`: Override the clustering method used during sorted ingestion

```bash
benchbox run --platform snowflake --benchmark tpch --scale 1 \
  --sorted-ingestion-mode force \
  --sorted-ingestion-method ctas
```

This runs Snowflake with forced CTAS-based sorted ingestion.

## Databricks Clustering Options

These overrides are Databricks-specific and are passed via `--platform-option`.

- `--platform-option databricks_clustering_strategy=[z_order|liquid_clustering|liquid_clustering_auto|none]`: SQL tuning strategy for Databricks tables
- `--platform-option liquid_clustering_columns=col1,col2`: Comma-separated liquid clustering columns

```bash
benchbox run --platform databricks --benchmark tpch --scale 1 \
  --platform-option databricks_clustering_strategy=z_order

benchbox run --platform databricks --benchmark tpch --scale 1 \
  --platform-option databricks_clustering_strategy=liquid_clustering \
  --platform-option liquid_clustering_columns=l_shipdate,l_orderkey

benchbox run --platform databricks --benchmark tpch --scale 1 \
  --platform-option databricks_clustering_strategy=liquid_clustering_auto
```

The commands, in order, run Databricks with Z-order clustering, with liquid clustering on specific columns, and with automatic liquid clustering.

## Execution Control Options

These flags control monitoring, progress display, memory handling, and caching behavior. They are advanced options - use `--help-topic all` to see them in the CLI help.

- `--mode [sql|dataframe]`: Force execution mode (validates against platform capabilities)
- `--no-monitoring`: Disable metrics collection during benchmark execution
- `--no-progress`: Disable progress bars
- `--ignore-memory-warnings`: Proceed with execution despite low memory warnings
- `--global-cache`: Use shared data cache at `~/.benchbox/datagen/` instead of project-local `benchmark_runs/` directory

## Query Plan Capture Options

- `--capture-plans`: Capture the logical query plan for each executed query and embed it in results.
  Plans are persisted to the results bundle for later comparison or regression detection.
  Display is not affected — plans are captured silently.
  See [Query Plan Analysis](../../features/query-plan-analysis.md) for full details.

- `--show-plans`: Display query plans in the console after each query executes.
  Use for interactive inspection **without** `--capture-plans`. When `--capture-plans`
  is also active, display is suppressed to avoid running EXPLAIN twice; use
  `benchbox show-plan` to inspect plans already captured to the results bundle.

- `--analyze-plans` / `--no-analyze-plans`: The single capture-detail knob (used with
  `--capture-plans`). `--analyze-plans` (default) captures `EXPLAIN (ANALYZE)` plans with actual
  per-operator timing/cardinality, re-running each query once in the isolated post-measurement
  phase (~1× extra cost, outside the measured window). `--no-analyze-plans` captures the static
  (estimated) plan with no re-execution. Write statements are never re-executed regardless (DML
  stays on a non-`ANALYZE` `EXPLAIN`).

- `--normalize-plan-literals`: In addition to the default fingerprint, record a literal-normalized fingerprint (`fingerprint_normalized` in the plans companion file) that collapses queries differing only in literal constants (e.g. parameter substitutions) to the same value. Requires `--capture-plans`; the default `fingerprint` is left unchanged.
  - Example: `--capture-plans --normalize-plan-literals`

- `--plan-config TEXT`: Fine-grained control over query plan capture
  - Format: comma-separated key:value pairs
  - Keys:
    - `queries:ID1,ID2,...` - capture plans for specific query IDs only
    - `strict:true|false` - fail if plan capture encounters errors
  - Requires `--capture-plans` to also be set
  - Example: `--capture-plans --plan-config "queries:Q1,Q6,strict:true"`
  - **Note:** Plan capture records each distinct query exactly once in an isolated
    post-measurement phase, so the old per-iteration sampling keys (`sample:` / `first:`) have
    been removed. On DuckDB the default `EXPLAIN (ANALYZE, FORMAT JSON)` re-runs each query once;
    use `--no-analyze-plans` for estimated plans without that overhead.

> **Cross-run fingerprint comparison and benchmark seeds.** The default
> `plan_fingerprint` embeds filter/join/projection literals, so comparing fingerprints
> across runs that used *different* seeds yields false positives when only a
> seed-driven filter value changed. Hold the seed constant across compared runs, or
> use the opt-in **literal-normalized** fingerprint
> (`QueryPlanDAG.normalized_fingerprint`,
> `create_plan_metadata_from_results(..., normalize_literals=True)`), which masks
> literals so seed-varied plans compare equal. See
> [Query Plan Analysis → Literal normalization](../../features/query-plan-analysis.md#literal-normalization-seed-independent-fingerprints).

(deprecated-run-official)=
## Deprecated: `run-official`

`run-official {tpch|tpcds} [OPTIONS]` is a hidden, deprecated compatibility
shim kept for older scripts. Prefer `run --official` for all new usage — it
covers everything `run-official` does except concurrent-stream count (see
below).

```bash
benchbox run-official {tpch|tpcds} [OPTIONS]
```

- `--platform TEXT` (required): Platform to run on
- `--scale FLOAT` (required): TPC scale factor (must be one of the
  TPC-allowed values: 1, 10, 30, 100, 300, 1000, 3000, 10000, 30000, 100000)
- `--phases TEXT` (required): Comma-separated test phases
- `--streams INTEGER`: Number of concurrent streams for the `throughput`
  phase. **Required** when `--phases` includes `throughput`; must be at least
  **2** (the TPC minimum), and `--streams 0` or `--streams 1` is rejected.
  Sets `BenchmarkConfig.concurrency` (the same field `run`'s throughput driver
  reads internally), so `run-official ... --streams 4` is equivalent to
  running with 4 concurrent throughput streams.
- `--seed INTEGER`: Random seed for reproducible official runs (a warning is
  printed if omitted)
- `--output PATH`: Output directory
- `-v`, `--verbose`: Enable verbose logging
- `--validate-results`: Enable result validation

**Note:** plain `run` (including `run --official`) accepts `--streams N` (with
`--concurrency N` as an alias), an advanced option that sets
`BenchmarkConfig.concurrency`. `run-official --streams N` is therefore
equivalent to `run --streams N` — see the `throughput` phase note above.

## Related

- [Configuration](configuration.md) - Configuration files and environment variables
- [Platform Options](configuration.md#platform-specific-options) - Platform-specific settings
- [Tuning Commands](tuning.md) - Generate and manage tuning configurations
