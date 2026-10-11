# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Result schema 2.3 supports execution-engine receipts, query engine observations,
  compute resource and size, selected deployment, routed gateway, and variant identity.
  Fields are omitted until producers supply them. Legacy engine requests map on read
  without changing legacy variant identity; explicit requests survive JSON re-export.
  Publication drops compute resource names and hashes gateway hosts.
- Platforms declare a `compute_resource` and, where the vendor bills a size,
  a `compute_size` setting. Vendor names (`warehouse`, `engine_name`,
  `workgroup`, `cluster_id`, `spark_pool_name`, `service_id`,
  `application_id`, `cluster_identifier`, `cluster_name`, `warehouse_id`,
  `engine_size`, `cluster_size` and others) keep working as deprecated
  aliases with a once-per-run warning and are removed in 0.6.0. Results
  record the canonical resource kind and size alongside the legacy fields;
  publication drops the resource name. Fabric Data Warehouse uses `database`
  as the canonical name with `warehouse` as a deprecated alias, and Athena
  Spark uses `runtime_version` with `engine_version` as a deprecated alias.

- Result bundles now record the factory-resolved deployment as
  `platform.deployment.selected` with `selected_class`, and non-default
  deployments appear in `platform.variant` (for example `firebolt~cloud`).
  The `--platform name:mode` selector is the canonical spelling on every
  platform that declares deployment modes; `--platform firebolt:cloud`,
  `pg-duckdb:motherduck`, `influxdb:core`, `ducklake:postgres_catalog`,
  `velox:remote`, and `lakesail:distributed` now work end to end, and
  LakeSail `distributed` really selects the distributed adapter.
- The native deployment keys are deprecated aliases of the selector and warn
  once per run: Velox `deployment`, LakeSail `sail_mode`, InfluxDB `mode`,
  ClickHouse `deployment_mode` and `mode` (the MCP `deployment_mode` key is
  retained and resolves the same way), Firebolt `deployment_mode` and
  `firebolt_mode`, DuckLake `deployment_mode`, pg-duckdb `deployment_mode`.
  Saved configurations and credential profiles holding these keys keep
  working. The aliases are removed in 0.6.0.
- The core `execution_engine` setting selects which execution machinery runs a
  query. `benchbox run --execution-engine` sits next to `--mode`, and MCP
  `run_benchmark`/`start_benchmark` accept `execution_engine`; both validate
  against the platform manifest and fail before any work on undeclared values.
  Polars DataFrame runs record a receipt with requested, applied and observed
  values, including literal collect arguments in published bundles. Tuning files
  and deprecated streaming aliases record their selected engine as the request.
  The Polars `engine` platform option is removed; the Polars
  `streaming` option, `--polars-streaming` and the MCP Polars `streaming` key
  are deprecated aliases for the streaming engine.

### Before you upgrade
- Vendor compute names now warn once per run; move saved configurations and
  credential profiles to `compute_resource` and `compute_size` (Fabric Data
  Warehouse: `database`; Athena Spark: `runtime_version`). The old names stop
  working in 0.6.0. When both spellings disagree, the canonical name wins,
  except Databricks, where a `compute_resource` that disagrees with the
  warehouse implied by `http_path` fails the run naming both values.

- Result bundles now use additive schema 2.3. Readers must accept this version.
  Older bundles remain readable and published bundles and IDs are unchanged.

- **Deployment selection is explicit and fail-closed.** A `--platform
  name:mode` selector that disagrees with a deployment platform option (for
  example `--platform firebolt:core --platform-option deployment_mode=cloud`)
  now fails before any work starts instead of silently picking one; pass one
  spelling. Unknown deployment values are rejected the same way.
- **Polars automatic tuning no longer selects a streaming engine on small hosts.**
  `--tuning auto` and `--tuning tuned` on Polars no longer switch to the
  streaming engine on hosts under 16 GB; the engine stays at `default` on
  every host. Use `--execution-engine streaming` instead. Tuning files you
  wrote yourself that set `execution.engine_affinity` or
  `execution.streaming_mode` are still honored.

- **ClickHouse tuned runs are faster, and their results are not comparable with earlier ones.**
  The tuned session settings no longer switch the join algorithm to
  `grace_hash`, which made joins about 2× slower on SF1 TPC-H. The ClickHouse
  TPC-H tuned template no longer partitions `LINEITEM` and `ORDERS` by month;
  the date sort keys stay, and they give the same date-range pruning, so the
  recorded tuning configuration of that template changes. TPC-H Q21 on tuned
  server runs now sets `join_algorithm = 'hash'` for that statement so it fits
  the 5.25 GiB memory envelope. A tuned server-mode load
  now waits, up to 10 minutes, for background merges to finish before the first
  query, and the wait is not counted as load time. Don't compare tuned
  ClickHouse timings recorded before this change with results after it.
- CLI help, MCP descriptions and benchmark descriptions use explicit metadata.
  Python API references retain public contracts independently of source docstrings.
  Generated query implementations and regional price resolvers no longer assign
  explanatory `__doc__` text. Code that reads those docstrings should use query
  metadata or the API reference; callable names, signatures and query behavior
  are preserved.

- **Tuning maintenance runs after the load.** ClickHouse `OPTIMIZE TABLE ...
  FINAL`, Redshift `ANALYZE` and `VACUUM`, Databricks Delta `OPTIMIZE` and
  `ANALYZE`, and Snowflake `RESUME RECLUSTER` ran before any data was loaded,
  on empty tables. They now run after each table loads. ClickHouse `OPTIMIZE`
  is off unless you set the `optimize_after_load` platform option to `true`.
  Redshift keeps its existing isolated `VACUUM` and `ANALYZE` pass over every
  loaded table, so the tuning step adds an `ANALYZE` only when `auto_analyze`
  is off. Databricks does not repeat its load-time `OPTIMIZE` on a table the
  tuning step already optimized. A load-only run (`--phases generate,load`)
  does not run Redshift's isolated pass, so it no longer vacuums tuned tables;
  the next benchmark run does.
  Data-loading time, in total and per table, excludes this maintenance; its
  time is reported as `phases.post_load_maintenance`.
- **ClickHouse primary keys and tuned sort keys.** With a tuned sort key,
  `primary_keys.enabled: false` now takes effect and emits no `PRIMARY KEY`
  clause. Before, it was ignored and a date-first sort key such as
  `(O_ORDERDATE, O_ORDERKEY)` stopped schema creation with a ClickHouse error.
  An enabled primary key that is not a prefix of the tuned sort key now fails
  before any table is created, with a message that says to put the key columns
  first or disable `primary_keys`.
- **Spark, LakeSail and Velox per-query times no longer include `clearCache()`.**
  With `disable_cache` on (the default), these adapters cleared the session
  cache inside each query's timer. They now clear it before the timer starts,
  so recorded query times can drop compared with earlier runs. On a shared
  session another stream's clear can still overlap a timed query during
  throughput and stays inside the throughput total time. Don't compare their
  per-query times recorded before this change with results after it.
- **A throughput run limited to a query subset reports no Throughput@Size.**
  Subset runs follow filtered parameter positions and are not TPC-compliant.
  The run still succeeds and reports query throughput.
- **Throughput now honors `validation_mode` and `--queries`.** TPC-H
  throughput ignored `validation_mode`, so `--validation-mode disabled` still
  failed streams on a row-count mismatch. Neither TPC-H nor TPC-DS throughput
  read the query subset, so `--queries 1,6 --phases power,throughput` ran the
  subset in the power phase and every query in the throughput phase. Both now
  follow the options, and each stream runs its own permutation filtered to the
  subset.
- **A saved stream timeout now applies to throughput runs.**
  `execution.concurrent_queries.stream_timeout_seconds` used to be read by
  nothing; it now sets the timeout of every TPC-H and TPC-DS throughput stream
  (0 disables it), and `cancel_on_timeout` next to it stops a timed-out stream
  before its next query. Config files written by `ExecutionConfigHelper`
  (`apply_performance_profile`, `optimize_for_system`, or any settings update)
  or by an earlier default often hold 1800, 3600, 7200 or 10800. A TPC-DS
  stream that runs longer than a saved value now times out and the run
  reports no Throughput@Size, where before it ran for the 7200 s default.
  Check `execution.concurrent_queries.stream_timeout_seconds` in your config
  file and remove it to keep each benchmark's default (3600 s for TPC-H, 7200 s
  for TPC-DS). The helper no longer writes a stream timeout unless you set one,
  and each throughput run prints the effective timeout and where it came from.
- **A stream count of 1 is no longer raised to 2.** `BenchmarkConfig.concurrency`
  and `RunConfig.concurrent_streams` default to `None`, which runs the
  2-stream default. An explicit count below 2 is rejected when the run
  includes throughput. Saved runs that recorded 1 replay as not set.
- **Legacy throughput APIs now run the supported drivers and are deprecated.**
  `PlatformAdapter.run_throughput_test`, `TPCDSBenchmark.run_throughput_test`,
  `TPCDSBenchmark.run_official_benchmark`,
  `TPCDSOfficialBenchmark.run_official_benchmark` and
  `TPCHOfficialBenchmark.run_official_benchmark` emit a `DeprecationWarning`
  and run the same throughput drivers as `benchbox run`: the dsqgen-based
  TPC-DS driver (per-stream seeds and permutations, no more shuffled stream
  order) and the TPC-H driver. The adapter entry point refuses platforms that
  cannot serve concurrent streams, opens one session per stream and takes the
  shared connection as `connection`; a `connection_factory` argument is
  rejected. The other four now require `adapter=` and raise `TypeError`
  without it, because a bare connection factory cannot be checked for safe
  concurrent sessions; `TPCDSBenchmark.run_throughput_test` also needs
  `connection=`. All of them refuse fewer than 2 streams, and none reports
  Throughput@Size when a stream failed (`throughput_at_size` is `None`).
  `query_timeout`, `max_retries` and `output_dir` on
  `TPCDSBenchmark.run_throughput_test` no longer have any effect and warn when
  you change them. Power@Size from `TPCDSBenchmark.run_power_test` is withheld
  when a query fails.
- **`TPCDSOfficialBenchmark` and `TPCHOfficialBenchmark` work end to end.**
  `TPCDSOfficialBenchmark` used scale factor 1 for the power and throughput
  tests whatever scale you configured; it now uses the configured scale, and
  it publishes no Power@Size unless every power query succeeded.
  `TPCHOfficialBenchmark` ran methods that `TPCHBenchmark` doesn't have and
  `TPCH.run_official_benchmark` failed with a `TypeError`; both now run the
  TPC-H power, throughput and maintenance tests and report each metric only
  from a complete phase.
- **TPC-H Power@Size changes.** Warm-up and measured power iterations now all
  run stream 00, so they use its query ordering and substitution parameters
  instead of streams 1, 2 and 3, in SQL and DataFrame runs. SQL runs now
  also check row counts on every measured iteration under the default
  validation mode. Power results are not comparable with earlier bundles.
- **Throughput streams are numbered 1 to S.** TPC-H and TPC-DS throughput runs
  used streams 0 to S-1, so stream 0 repeated the power ordering. Stream 0 is
  now left to the power test and a new stream S takes its place, which changes
  the queries and parameters each run executes. Throughput results are not
  comparable with earlier bundles. TPC-DS power ordering is unchanged.
- **TPC-H Q13 accepts 41 or 42 rows under non-reference seeds.** Its row count
  depends on the two comment words the seed picks, so a valid throughput
  stream no longer fails against the answer-file count of 42. Runs with the
  default parameters still require 42.
- **Throughput results record how streams are numbered.** The new key
  `phases.throughput_test.stream_numbering` holds the basis and the first
  stream id. `run.streams` in a combined run now counts throughput streams
  only.

### Added

- **Decision record for core execution-variant settings.** A new ADR,
  `docs/development/adr/adr-core-execution-variants.md`, defines the
  platform-neutral settings `execution_engine`, `compute_resource`,
  `compute_size` and `gateway`, how results record and identify them, and how
  existing platform-specific keys such as Polars `streaming` and Databend
  `warehouse` become deprecated aliases. Documentation only; no behavior
  changes yet.
- **Execution-engine declarations in the platform manifest.** Platforms can
  now declare the execution engines they support, each with one of a fixed set
  of engine classes, and the manifest rejects unknown classes, a declared
  `default`, and an engine name that means different classes on different
  platforms. Polars declares `auto`, `in-memory` and `streaming`. Every
  adapter gains a `resolve_execution_engine` hook that accepts only `default`
  until the platform maps its engines. A new test fails when a platform
  registers a bare option key such as `engine`, `mode` or `warehouse`. No
  command-line, MCP or result behavior changes yet.
- **Curated ClickHouse tuning templates.** TPC-H, SSB, and TPC-DS ship tuned
  templates for ClickHouse, so `--tuning tuned` there applies sort keys and
  partitions instead of falling back to session settings.
- **DataFrame tuned profiles.** `--tuning tuned` on Polars, pandas, and cuDF
  now resolves the curated `examples/tunings/dataframe/<platform>_optimized.yaml`
  profile and records mode `tuned`. Platforms without a profile, such as Dask,
  still resolve to `tuned-fallback`.
- **`benchbox tuning validate` accepts SQL tuning files.** For example,
  `benchbox tuning validate examples/tunings/duckdb/tpch_tuned.yaml --platform
  duckdb` checks the file against the platform's capabilities.
- **Applied ledger `satisfied` list.** When a platform realizes a requested
  sort through `ORDER BY` in `CREATE TABLE`, as ClickHouse and StarRocks do,
  the ledger records it as satisfied by that statement instead of dropped.
  Sorts that no executed statement realized are still dropped. This lets
  ClickHouse tuned runs reach `applied_verified`.

### Changed

- **CI database images no longer depend on repeated anonymous Docker Hub pulls.**
  PostgreSQL uses Amazon ECR Public. ClickHouse and Trino use authenticated
  GHCR mirrors pinned by digest; scheduled jobs contact Docker Hub only to copy
  a missing digest. CedarDB retains its digest-pinned mirror. DuckDB and
  CedarDB throughput checks run independently, while their combined commit
  status reports either failure. CI and Documentation install pinned codespell
  with uv instead of building a Docker action.

- **The `--tuning tuned` fallback names what each platform applies.** The
  message said "using basic constraints" everywhere. It now says "engine
  runtime defaults (streaming)" for Polars and "OLAP session pack" for
  ClickHouse, for example.

## [0.4.2] - 2026-10-05

### Before you upgrade

- **BREAKING: Runs now fail on errors they used to hide.** A run fails if any
  query fails validation, including warm-up queries, or if no table loads any
  rows. DataFrame platforms refuse `--phases throughput`; use `--phases power`
  or a SQL platform. FlightData at scale factor 0.1 and above fails when a
  month can't be downloaded. To use synthetic rows for missing months, set the
  `allow_synthetic_fallback` option.
- **Some results can't be compared with 0.4.1.** TPC-DS runs at scale factors
  other than 1 now use query parameters for that scale, which changes up to 12
  queries. DataFrame runs use the same parameters as SQL runs, and TPC-H
  DataFrame runs now follow `--seed`. Power-phase times, and the cost per hour
  based on them, now include the time between queries.
- **Some answers change because they were wrong before.** SQLite TPC-H Q6,
  write primitives on Snowflake and Databricks, TPC-Havoc Q16-Q18 on
  DataFrame platforms, NYC Taxi weekdays on Polars, and many DataFusion
  DataFrame queries now return correct answers.
- **Databricks turns off the result cache.** BenchBox checks that the
  warehouse confirms the setting and stops if it doesn't. To measure with the
  cache, set the `disable_result_cache` option to `false`. When BenchBox
  rebuilds a schema, it now empties tables instead of dropping them, so
  repeated runs no longer use up the Unity Catalog table quota.
- **Regenerate some data.** TPC-H data generated on macOS by earlier versions
  has wrong supplier and customer addresses. FlightData downloads and
  generates its data again on the next run.
- **Stricter submissions.** A TPC-Havoc submission must include every variant.
  A result in which every query was skipped no longer passes integrity
  certification. Nine incorrect TPC-H results from early BigQuery, Databricks
  and Snowflake runs are removed from Results Explorer.
- **DuckDB tuned databases are rebuilt.** The shipped DuckDB tuned templates
  no longer include partitioning or CHECK constraints, so a new tuned run
  records a different `requested_config_hash` than earlier results and the
  cached tuned database is rebuilt on the next run (for example
  `tpch_sf1_custom_pk_fk_uniq_check_part_sort` becomes
  `tpch_sf1_custom_pk_fk_uniq_sort`).
- **Cached TPC-H and TPC-DS data regenerates once.** Dataset manifests now
  record the SHA-256 of the bundled `dbgen` or `dsdgen` binary that wrote the
  data. A cache that records no generator, or that a known-defective `dbgen`
  wrote, is regenerated on the next run. This covers TPC-H data generated on
  macOS by the earlier `dbgen`, which wrote non-canonical supplier and
  customer addresses (Q2, Q10, Q15 and Q20 return different values), and
  TPC-DS data generated on Windows before `dsdgen` options were passed
  correctly, which was always scale factor 1. The TPC-H Skew, TPC-Havoc,
  primitives and Data Vault datasets are built from `dbgen` and regenerate too.

### Added

- **ParadeDB and Citus platforms (experimental).** Use `--platform paradedb`
  or `--platform citus`. On Citus, the `distribution_column` option spreads
  tables across workers.
- **More data sources.** DuckLake can store data on Google Cloud Storage and
  Azure as well as S3. NYC Taxi can load For-Hire Vehicle trips and run three
  queries on them. The synthetic Join Order Benchmark has all 113 queries, up
  from 13, each with pandas and Polars versions.
- **More tuning templates.** BenchBox ships Snowflake templates for TPC-H and
  TPC-DS. SSB can now be tuned.
- **Choose TPC-DS query values.** In Python,
  `DSQGenBinary.generate_with_parameters` now applies the values you pass, and
  `DSQGenBinary.generate_parameter_log` lists the values used for a seed and
  scale factor. BenchBox includes the qualification values from the TPC-DS
  specification.

### Changed

- **Vector Search is stable.** It remains SQL-only.
- **Faster loads and queries.** Loading is faster on BigQuery, ClickHouse
  Cloud and PostgreSQL. Snowflake no longer pauses after each query. pandas
  runs TPC-DS Q78 in about 0.3 seconds at scale factor 0.1, down from about
  14 seconds.
- **TPC-Havoc runs on more engines.** Variants that were skipped now run on
  BigQuery, Databricks, Snowflake, PostgreSQL-family engines, ClickHouse and
  DataFusion. `--dry-run` saves the same SQL a real run would use.
- **Result files always record tuning status.** Every result now includes
  `platform.tuning.validation_status`, including runs without tuning. No
  existing field changes.

### Fixed

- **DataFrame answers match SQL.** All 103 TPC-DS queries return the same
  answers as SQL on Polars, pandas and DataFusion. TPC-H Q1, Q3, Q5, Q10, Q14
  and Q15 are fixed on DataFusion. TPC-H Q17 returns the right answer on empty
  input, and Dask sorts NULLs like the other platforms.
- **DataFrame runs no longer crash when two runs share the data cache.**
- **Cloud platforms.** TPC-DS One Big Table on Snowflake and BigQuery no longer
  runs against an empty table. Generated data for eight more benchmarks now
  reaches cloud storage. Data Vault, TSBS DevOps and the read, write,
  transaction and metadata primitives run on Snowflake, BigQuery and
  Databricks, and skip unsupported queries with a reason. Exasol, Presto and
  Trino load data correctly.
- **Tuning.** The tuning wizard saves the table layouts you choose. DuckDB
  tuned runs no longer report false index mismatches, and TPC-DS tuned runs
  load on engines that enforce primary keys. Runs without tuning reuse their
  database. The console reports only tuning work that succeeded. DuckLake no
  longer deletes its data partway through a run.
- **Command line.** `--show-plans` prints query plans. Selectors such as
  `--platform clickhouse:local` work, and an unknown deployment lists the
  ones that exist.
- **Data generation.** TPC-DS generation on Windows uses the scale factor you
  ask for. TPC-H Skew at scale factor 10 fits in 16 GB of memory. Read
  Primitives data loads on the first run.
- **Other fixes.** Seeded TPC-H runs no longer report false failures on Q11,
  Q16, Q18 and Q20. Anonymized results hide more cloud account details.
  SQLite throughput streams no longer mix up rows. TPC-DS One Big Table works
  from an installed package. Results Explorer recovers from startup errors and
  no longer stalls on links to missing results.

## [0.4.1] - 2026-09-24

### Before you upgrade

- **BREAKING: Python 3.11 or later is required.** Python 3.10 reaches end of
  life in October 2026. We plan to require Python 3.12 after Python 3.11
  reaches end of life in October 2027.
- **BREAKING: Newer dependencies.** DataFrame platforms need pandas 3 and
  `dask[distributed]>=2025.1.0`; with pandas 2, BenchBox reports `pandas-df`
  and Dask as unavailable. DuckDB must be at least 1.5 and below 2.0,
  DataFusion 54 or later, and `databricks-connect` below 19.
- **BREAKING: Removed features.** Modin is no longer supported: use `pandas-df`
  or `dask-df` instead of the `modin` and `modin-df` platforms and extras.
  `PowerRunExecutor` and `ConcurrentQueryExecutor` are removed; see
  `docs/advanced/power-run-concurrent-queries.md` for replacements.

### Added

- **More lakehouse and external-table support.** ClickHouse can read Delta
  tables, Redshift Spectrum and BigQuery can read Iceberg tables, and Databricks
  can use Hudi tables. `--table-mode external` works on Athena Spark, EMR
  Serverless, Dataproc Serverless, and Glue. Delta and Hudi tables support
  optimize and vacuum; Iceberg tables support vacuum.
- **Better comparisons in Results Explorer.** You can browse results by engine
  version, compare several runs against a baseline, and preview your own
  results before you submit them.
- **Repeatable downloaded datasets.** NYC Taxi and FlightData download a fixed
  set of files, so every run starts from the same source window.
- **More detail in result files.** Results record the tuning you requested and
  the tuning BenchBox applied, the client's cloud region, and each table's load
  time where the loader captures per-table measurements.
- **Throughput concurrency option.** `benchbox run --streams` sets how many
  query streams a throughput test runs at once.
- **More DataFrame queries.** TPC-DS One Big Table has 17 DataFrame queries, up
  from 3.
- **DuckDB 2.0 preview.** BenchBox can read query plans from the DuckDB 2.0
  preview. Stable DuckDB 1.x remains the default.

### Changed

- **Stricter checks on submitted results.** Unofficial TPC-H runs can no longer
  be submitted, as was already true for TPC-DS. A submission must include every
  query in the benchmark. BenchBox also warns, without blocking, when timings
  look implausible.
- **No more guessed costs.** When BenchBox doesn't know a price, size, or
  region, it reports the cost as unavailable instead of guessing. Prices now
  come from vendor price lists, and Snowflake cost reflects run time and
  warehouse size.
- **Safer throughput tests.** BenchBox waits for timed-out queries to stop
  before the next phase starts. If the throughput phase fails, results from
  `benchbox run` omit Throughput@Size.

### Fixed

- **Cloud platforms.** ClickBench, TPC-DS, TPC-DI, and FlightData now load and
  run on BigQuery, Snowflake, and Databricks. Several Spark platform issues are
  fixed, including TPC-Havoc queries that Spark rejected.
- **Correct results.** DataFrame queries for Data Vault and FlightData return
  the same results as SQL, and results record the real CPU model.
- **Other fixes.** BenchBox no longer drops benchmark options you pass on the
  command line, and exporting results no longer fails on Windows.

## [0.4.0] - 2026-08-27

> **Post-publication accounting correction:** This section was reconciled on
> `develop` after v0.4.0 was published. It records shipped changes omitted from
> the immutable tag's changelog. The tag and PyPI artifacts were not modified.

### Removed

- **BREAKING: bare `clickhouse` platform alias removed** - The temporary
  compatibility alias that let `--platform clickhouse` (and `get_adapter(
  "clickhouse")`) resolve to a first-class platform was added in v0.2.1 as a
  deprecation shim and is removed in this release (window v0.2.1 → v0.4.0).
  Use `clickhouse-local` (embedded chDB), `clickhouse-server` (self-hosted), or
  `clickhouse-cloud` (managed); the explicit `clickhouse:local` /
  `clickhouse:server` / `clickhouse:cloud` selectors also remain available.
  Passing bare `clickhouse` now raises a `ValueError` naming these replacements
  instead of silently defaulting to a deployment mode. The `ch` CLI shorthand
  now resolves to `clickhouse-local`.
- **BREAKING: `databricks-connect` install extra removed** - Replace
  `benchbox[databricks-connect]` with `benchbox[cloud-spark-databricks]`. This
  renames only the BenchBox install extra; it still installs the upstream
  `databricks-connect` package. Other DataFrame install extras are unchanged.

### New

- **Results Explorer preview** - Browse and compare published benchmark results at
  [benchbox.dev/results/](https://benchbox.dev/results/). Explore cross-platform
  leaderboards, per-query timings, hardware details, comparison tools, and SQL
  queries over the public data. The preview contains a curated set of results,
  not a complete or certified ranking.
- **DuckLake platform** - Run benchmarks against DuckLake (DuckDB lakehouse
  format: Parquet table data + SQL-database catalog metadata) via
  `--platform ducklake`. The catalog backend (`--platform-option
  catalog=duckdb|sqlite|postgres`) and the Parquet `data_path` (local or
  `s3://`) can be selected independently. Four documented deployment modes have
  passed TPC-H scale-factor-1 correctness validation. Existing catalogs are
  reused across runs and `--force` rebuilds them; requires DuckDB >= 1.3,
  installable as `benchbox[ducklake]`. Beta.

### Added

- **Result provenance and funding labels** - Results Explorer now shows whether
  each published run came from BenchBox maintainers, a community contributor,
  or a platform vendor, together with any disclosed funding source. These labels
  appear in rankings, comparisons, and result details, with an in-page
  explanation of what they mean. Community submissions remain visible but are
  excluded from ranked tables.
- **Local MCP connections over HTTP** - Local MCP clients can now connect with
  `benchbox-mcp --transport streamable-http`; existing stdio integrations
  continue to work unchanged. Authenticated non-local deployments also provide
  persistent benchmark jobs, but shared deployment remains deferred and is not
  supported for production use in this release.

### Changed

- **GitHub repository moved to the `BenchBox-dev` organization** - BenchBox is
  now hosted at [github.com/BenchBox-dev/BenchBox](https://github.com/BenchBox-dev/BenchBox).
  Public project links, issue and release tooling, CI, and package metadata now
  use the organization-owned repository. Existing Git remotes using
  `github.com/joeharris76/BenchBox.git` continue to redirect; update local
  remotes to the organization URL when convenient.

### Fixed

- **Correct data volumes for Write and Transaction Primitives** - Reusing
  benchmark data created for a different scale factor could produce successful
  results against the wrong amount of staged data. BenchBox now detects and
  rebuilds that data automatically on the next run; no action is needed.
- **TPC generators recover from incompatible bundled tools** - The bundled
  TPC-H generators now support macOS 15. BenchBox also checks bundled TPC-H and
  TPC-DS generators before use and automatically builds a compatible version
  when needed.
- **Correct TPC throughput metrics in newly exported results** - TPC-H and
  TPC-DS throughput drivers now include every executed query in
  Throughput@Size, correcting the 22x and 99x understatements produced by
  earlier result versions. Historical result bundles remain unchanged records.
- **Removed dead `BENCHBOX_TUNING_ENABLED` env var** - This variable set the
  `tuning.enabled` config key, which nothing at runtime ever read (only a unit
  test did); docs incorrectly claimed it "activates tuned runs in CI". Use
  `--tuning tuned` / `--tuning auto` on `benchbox run` to actually enable
  tuning; `BENCHBOX_TUNING_CONFIG` still works to point at a default tuning
  file. If you were setting `BENCHBOX_TUNING_ENABLED`, it had no effect and can
  simply be removed.
- **Accurate `--tuning auto` guidance for SQL platforms** - SQL runs now explain
  that `auto` uses a basic constraints-only configuration: primary-key,
  foreign-key, unique, and check constraints are enabled, with no other tuning.
  DataFrame platforms continue to use smart defaults.
- **DataFusion accepts Python string table paths** - Programmatic DataFusion
  loads now accept paths supplied as either Python strings or `pathlib.Path`
  objects.

### Security

- **Expanded secret redaction in result exports and common errors** - Result
  exports now remove connection credentials, account identifiers, and
  service-account keys. MotherDuck, DuckLake, and MCP benchmark-execution errors
  also redact known secrets, including values carried by chained exceptions.
  Some other MCP errors can still include backend-provided exception text, so
  avoid credentials in values that a backend might repeat in an error.

## [0.3.1] - 2026-07-09

### New

- **Databricks Liquid Clustering** - Added TPC-H and TPC-DS tuning profiles that
  apply Databricks Liquid Clustering for more representative runs.

### Added

- **Benchmark support-status diagnostics** - You can now check which benchmarks
  and platforms are supported, and why a given combination is gated, from
  registry-backed metadata instead of inferring it from docs.
- **Safer prompt composer guidance** - The `/prompts/` page now flags
  paid-platform runs, surfaces platform footguns, and adds provenance, MCP, and
  CLI-hygiene guidance for agent-generated benchmark runs.

### Fixed

- **`-df` platform names select DataFrame mode** - `benchbox run --platform
  datafusion-df` (and `lakesail-df`) now runs the DataFrame adapter without
  needing `--mode dataframe`; alias normalization previously dropped the
  suffix and fell back to the platform's SQL default. An explicit `--mode`
  still wins.
- **Clean install now imports** - `pip install benchbox` imports cleanly
  without pulling pandas. Optional engine and DataFrame imports (ClickHouse
  local, DataFrame mode) are now loaded lazily, so the base install stays
  minimal. This resolves the v0.3.0 clean-install failure.
- **Consistent TPC-H data across platforms** - Bundled Linux and Windows
  `dbgen` now emit rows without a stray trailing delimiter, matching macOS, so
  generated TPC-H `.tbl` column counts are consistent on every platform.
- **Trino table creation** - JoinOrder `CREATE TABLE` no longer emits
  primary-key clauses that Trino rejects.
- **JoinOrder across engines** - SQLite now indexes JoinOrder tables, Spark
  disables broadcast joins for the fixed dataset, and Presto/Spark run paths
  were corrected for more reliable JoinOrder runs.
- **Spark warehouse location** - Spark now writes its warehouse to the run's
  database directory instead of the current working directory.
- **Chart rendering** - Power-run charts render for benchmarks without TPC
  metrics, and JoinOrder chart labels are preserved.
- **Light-mode readability** - Restored prompt-copy contrast on the landing and
  prompt pages in light mode.

### Changed

- **Leaner, more maintainable codebase** - Completed a large quality pass that
  removed dead code and consolidated duplicated internals across benchmarks and
  platform adapters, with runtime behavior preserved by focused tests.
- **Registry-derived platform lists** - Landing and prompt pages now derive
  their platform and command examples from the live registry, so the lists stay
  in sync with actually supported platforms.

## [0.3.0] - 2026-05-16

### New

- **Prompt composer page** - Added `/prompts/`, a page that turns benchmark
  choices into copyable prompts for coding agents. It covers platform,
  benchmark, scale, CLI, and MCP choices with safe defaults for local and
  cloud runs.
- **Sketch benchmarking coverage** - Added `write_primitives` workloads for
  DataSketches Theta, KLL, Top-K, DuckDB CPC/REQ, ClickHouse aggregate-state
  sketches, and Redshift HLL. The new workloads cover persist, merge, requery,
  storage-size validation, and parameter sweeps where supported.
- **Approximate read coverage** - Added `read_primitives` queries for
  approximate distinct counts, approximate quantiles, and approximate top-k,
  plus DataFrame implementations where supported.

### Added

- **Complete JoinOrder query set** - Added all 113 Join Order Benchmark SQL
  queries, reference cardinalities, and fixture tests for query predicates.
- **Benchmark data provenance** - Published result bundles can now record the
  dataset version, manifest hash, and data archive hash for benchmark datasets.
- **PySpark sketch DataFrame support** - Added aggregate persist/merge
  operation types, PySpark capability flags, and PySpark HLL/top-K helpers so
  sketch workflows can run through DataFrame managers.
- **Per-platform validation SQL** - Added `ValidationQuery.platform_overrides`
  so validation queries can declare engine-specific SQL bodies or explicit
  skips.

### Fixed

- **JoinOrder benchmark correctness** - `joinorder` now uses the real Join
  Order Benchmark IMDb 2013 dataset. This fixes a user-raised comparability
  issue where BenchBox-generated data was being used for JoinOrder runs. The
  benchmark is now limited to scale factor 1 because JOB is a fixed dataset.
- **Approximate aggregate semantics** - Fixed `read_primitives` approximate
  query coverage so names and implementations match the work being measured.
  `intrinsic_appx_median` is now `approx_quantile_groupby`, PySpark and
  DataFusion use sketch-backed quantile implementations, and Redshift has an
  `APPROXIMATE PERCENTILE_DISC` variant.

### Changed

- **JoinOrder synthetic data renamed** - The old synthetic JoinOrder generator
  is now `joinorder_synthetic` and is kept out of the released benchmark list.
  Existing `joinorder_sf1_*` published-result bundles were re-tagged as
  `joinorder_synthetic_sf1_*` so they are not confused with results from the
  real IMDb dataset.
- **DataFrame approximate coverage is more explicit** - `SKIP_FOR_DATAFRAME`
  for `read_primitives` shrank from 7 to 5. `approx_quantiles_array` and
  `approx_top_k_lineitem` remain PySpark-only at the DataFrame layer with
  documented rationale.

## [0.2.1] - 2026-04-26

### New

- **Benchmarks**
  - **Vector Search** - 6 kNN/ANN queries with recall@k and latency metrics;
    dialect variants for DuckDB, pgvector, Snowflake, ClickHouse, StarRocks,
    and Doris.
  - **FlightData** - 20 analytical queries over US BTS on-time aviation data
    with DataFrame support.
  - **NYC Taxi expansion** - Green Taxi, For-Hire Vehicle (FHV), and
    High-Volume FHV trip records now supported alongside Yellow Taxi; select
    via `--benchmark-option taxi_types=…`.
- **Platform adapters**
  - **Apache Doris** - Open-source MPP database powering real-time analytics at
    Baidu, Meituan, and JD.com; Uses the MySQL wire protocol.
  - **CedarDB** - HTAP engine (transactional + analytical) based on research from
    TU Munich (formerly Umbra). JIT-compiled queries. PostgreSQL wire compatible.
  - **StarRocks** - Sub-second columnar OLAP with best-in-class materialized
    views and data-lake federation
  - **SingleStore** - HTAP engine with unified row-store + columnstore for real-time
    ingest and simultaneous analytical queries without ETL; cloud and self-managed.
  - **QuestDB** - New time-series database with designated timestamps, automatic
    time partitioning, and millions-of-rows-per-second ingestion.
  - **Apache Gluten + Velox** - Spark accelerator, replaces JVM operators with
    Meta's C++ Velox engine to provide speedup with no application rewrites.
  - **Snowpark Connect and Onehouse Quanton restored** - Both platforms are
    back in the supported list after being removed in v0.2.0.

### Added

- **Scale factor harmonization** - Benchmarks with adjustable scale factors now
  target roughly 1 GB of uncompressed CSV data at SF=1, giving BenchBox a more
  consistent scale model. Output sizes at a given SF will change for affected
  benchmarks. Spec-locked benchmarks such as TPC-H, TPC-DS, SSB, ClickBench,
  and DataVault are unchanged.
- **Unofficial TPC-DS scales** (SF < 1) now work out of the box, backed by
  patched `dsdgen` binaries bundled with BenchBox. Runs are non-comparable to
  published TPC-DS results.
- **DataFrame execution coverage** - DataFrame mode now works on Metadata
  Primitives, Transaction Primitives, TPC-Havoc, FlightData, and JoinOrder.
- **New CLI flags** - `--benchmark-option K=V` (repeatable, for
  benchmark-specific parameters such as `taxi_types=yellow,green`) and
  `--iterations` (power-test measurement count).
- **HTTP TLS certificate validation** for HTTP-based data loading.
- **Advanced pg_mooncake and pg_duckdb benchmarking options.**
- **SQL compatibility subsystem** - Added `benchbox/sql_compat/` to centralize
  benchmark gating, query variants, query rewrites, schema emission, and DDL
  optimization.
- **Extended compressed data generation** - TSBS, FlightData, and NYC Taxi
  generators now support BenchBox compression options and write compression
  metadata manifests.
- **Broader platform coverage** - Added Docker-based live smoke tests for
  Firebolt Core and added SHA-256 DataVault hash-key support in the SQL ETL
  path.

### Fixed

- **Interactive `benchbox run` wizard** - Resolved phase-vs-query prompt
  confusion, `Q`-prefix query ID mismatches, invalid scale-factor
  recommendations, misleading tuning preview, and dead Test Execution Type
  input.
- **Platform config credential precedence** - Saved credentials now correctly
  override registered defaults across all builders.
- **Platform format preference defaults** - Data format selection now
  correctly defaults to each platform's preferred format.
- **ClickHouse correctness** - TPC-DS power tests now report `FAILED` when no
  queries execute (previously passed silently); added dialect overrides for
  `tpcdi`, `coffeeshop`, `h2odb` Q9, `nyctaxi` EXTRACT, `tpchavoc` Q6, and
  `read_primitives`; query error messages are now surfaced in results (pattern
  also extended to Redshift and Firebolt).
- **Apache Doris and StarRocks stability** - Hardened bulk loading, SQL
  compatibility, Docker startup, ARM64 support, timeouts, and type handling
  across `tpcdi`, `tpcds_obt`, `vector_search`, ClickBench, NYC Taxi, and
  primitives workloads.
- **QuestDB compatibility and loading** - Expanded query rewrites, improved
  `/imp` CSV loading with zstd and multi-chunk support, added platform option
  registration and HTTPS support, and stabilized table reuse.
- **Cross-adapter correctness fixes** - Resolved CedarDB benchmark failures,
  improved LakeSail local-mode startup and file scanning, added Firebolt S3
  staging and better load errors, improved Databend connection and cache
  behavior, and hardened pg_mooncake/pg_duckdb columnstore migration.
- **Dependency and test reliability** - Migrated pg-family adapters to
  psycopg3, relaxed a DataFusion v53 ordering assertion, raised the
  `pyarrow` cap to `<25.0.0`, and restored benchmark timeout enforcement on
  macOS.
- **Velox adapter** local mode now sets `spark.driver.extraClassPath` and
  `spark.executor.extraClassPath` alongside `spark.jars`. The Gluten plugin
  is loaded by `SparkContext.initializeSparkContext` before `spark.jars`
  promotions reach the executor classpath, so without these entries the
  plugin silently no-ops and queries run on plain Spark. The Docker entrypoint
  was already setting these for the server-mode path; adapter local mode is
  now consistent.
- **Velox adapter** error message for missing `gluten_jar_path` no longer
  references the removed `--velox-jar` legacy flag; it now points at the
  canonical `--platform-option gluten_jar_path=…` syntax.

### Changed

- **ClickHouse split into three platforms** - `clickhouse-local`,
  `clickhouse-server`, and `clickhouse-cloud` replace the single `clickhouse`
  platform, driven by a standardized `deployment_mode` contract.
- **TPC-DS-OBT now defaults to `parquet` output** - BenchBox now writes OBT
  artifacts as parquet by default for much smaller output size and better
  handling of wide, sparse tables. Use `--benchmark-option output_format=dat`
  to keep the old behavior.
- **Platform config builders consolidated** - Databend, Spark, ClickHouse
  Cloud, Snowflake, Databricks, and the remaining builders now share
  `build_platform_config()` with a consistent merge order.
- **Docker test platform image upgrades** - Local test platform images
  updated to latest stable versions. PostgreSQL and pg_duckdb have been
  upgraded to v18; all other images track their current stable releases.
- **Spark adapter** (`--platform spark`) now strips `PRIMARY KEY`, `FOREIGN KEY`,
  `UNIQUE` (both inline column-level and table-level), and `CHECK` constraints
  (including those with nested-paren expressions like `CHECK ((a > 0) AND (b > 0))`)
  from `CREATE TABLE` statements when running with `--table-format parquet`
  or `orc`, and rewrites `SMALLINT` to `INT` to avoid `CAST_INVALID_INPUT`
  on values above 32767. This unlocks benchmarks whose schemas carry these
  constraints (`write_primitives`, `metadata_primitives`,
  `transaction_primitives`, `datavault`, ClickBench) on Spark V1 datasource
  tables for the first time. `--table-format delta` and `iceberg` (V2 catalog
  tables) preserve constraints and `SMALLINT` as before.
- **LakeSail adapter** picks up the same constraint stripping and `SMALLINT`
  upcast through the shared Spark helpers; previously these benchmarks failed
  at `CREATE TABLE`. The orphaned-warehouse pre-purge that the Velox adapter
  introduced is now wired into LakeSail too, defending long-running pysail
  servers against `LOCATION_ALREADY_EXISTS` from prior on-disk warehouse data.

### Internal

- Lifted the three identical `apply_constraint_configuration`,
  `apply_unified_tuning`, and `apply_platform_optimizations` bodies from
  spark / lakesail / velox into a `SparkLikeAdapterMixin` next to the
  existing Spark helpers.
- The `optimize_spark_table_definition` helper now uses balanced-paren
  matching for table-level constraints so DDL with nested-paren CHECK
  clauses no longer corrupts when stripped.


## [0.2.0] - 2026-04-01

### Added

- **On-demand TPC answer file downloads** - Wheel installs now fetch missing
  TPC-H and TPC-DS answer files automatically for row-count validation, or
  pre-populate the cache with `benchbox download-answers` for offline and
  air-gapped environments. Supports `--benchmark tpch|tpcds|all`, `--force`,
  `--show-cache-dir`, SHA-256 verification, retries, and `BENCHBOX_NO_DOWNLOAD=1`.
- **Adaptive Redshift connection timeout** - Connection timeout now adjusts to
  deployment state: paused or resuming provisioned clusters get a longer timeout,
  and Serverless gets a cold-start floor to reduce spurious connection failures.
- **Cloud platform live integration tests** - Added live smoke tests for
  Firebolt, Starburst Galaxy, MotherDuck, pg_duckdb, and pg_mooncake with
  per-platform markers, fixtures, Makefile targets, Docker Compose harness for
  PostgreSQL extensions, and multi-extension comparison orchestration.
- **Read Primitives Redshift dialect coverage** - Expanded Redshift Read
  Primitives support from 0 to 40+ queries with dialect-specific rewrites for
  window functions, fulltext operators, array operations, and statistical
  functions. Queries without semantically-exact Redshift equivalents are skipped
  rather than approximated.

### Fixed

- **Redshift data-loading reliability** - Fixed S3 upload retry failures caused
  by botocore stream rewind errors, `DROP DATABASE` timeouts (extended socket
  timeout to 300 s and pre-terminate active connections), `Is a directory` errors
  with multi-format manifests, stale native datagen reuse, Serverless-compatible
  system table queries (`sys_serverless_usage` vs `stv_cluster_configuration`),
  and `pg_stat_activity` column references (`procpid` vs `pid`). Platform format
  preferences now take priority over manifest defaults.
- **Dask DataFrame `.isin()` runtime errors** - Seven TPC-H queries (Q4, Q16,
  Q18, Q20, Q21, Q22) and five TPC-DS queries (Q37, Q41, Q45, Q82, Q83) passed
  lazy Dask Series to `.isin()`, causing `TypeError` at runtime. All call sites
  now materialize via `.compute()` before `.isin()`.
- **BigQuery and format loading correctness** - Standardized text files (`.tbl`)
  as the default load source for SQL platforms while preserving columnar
  preferences for catalog platforms. Enabled parquet-first native loads on
  BigQuery and fixed GCS blob naming to preserve compound suffixes
  (`.parquet.zst` not `.zst`).
- **Data-source database resolution** - Shared-data benchmarks (e.g. Read
  Primitives) now resolve the source benchmark's database name instead of
  generating independent empty databases, fixing silent failures in
  multi-benchmark workflows.
- **TPC-H validation noise on measurement runs** - Non-stream-0 power runs no
  longer emit spurious "Validation skipped" warnings for all 22 queries on every
  measurement iteration.
- **Monitoring without optional extras** - Benchmark execution and progress
  reporting now degrade gracefully when the monitoring extra is not installed
  instead of failing during import.
- **Redshift adapter-only validation** - Repaired live test guard and validation
  logic so adapter-only (non-admin) Redshift connections no longer crash during
  row-count validation.

### Changed

- **Project status promoted to Beta** - BenchBox is now `Development Status ::
  4 - Beta` in package metadata.
- **Release-quality hardening** - Raised the coverage threshold to 80%, added
  behavioral coverage across adapter SQL generation, CLI paths, credential
  prompts, and adapter lifecycle flows, removed unreachable code, cleaned up
  tautological assertions and overspecified mocks, and accelerated
  `coverage-fast` runs with parallel execution.
- **Dead code cleanup** - Removed 9 unused platform imports and prefixed 12
  unused function parameters with `_` across the adapter layer.
- **Redshift format selection logic** - Scoped Redshift-specific format
  overrides to native COPY loads only and added platform-preference priority over
  manifest preferences, fixing incorrect format choices in mixed-format
  environments.

### Removed

- **Legacy `plot` CLI command** - Removed `benchbox plot` and its undeclared
  matplotlib dependency. Use `benchbox visualize` for terminal chart rendering.
- **Snowpark Connect and Onehouse Quanton platforms** - Removed from the
  supported SQL platform list (31 → 29).

## [0.1.5] - 2026-03-10

### Added

- **Textcharts standalone library** - Extracted all 15 ASCII chart types and base rendering
  primitives into an independent `textcharts` package under `packages/textcharts/`. The library
  has its own `pyproject.toml`, README with chart gallery and API reference, and zero BenchBox
  dependencies. Clean standalone names (`BarChart`, `Histogram`, `Heatmap`, etc.) are exported
  alongside BenchBox-compatible aliases. BenchBox now depends on textcharts as a path dependency
  with compatibility shims preserving existing import paths.
- **Open table format loading** - Added runtime loading support for external table formats
  (Delta Lake, Iceberg, Hudi) through adapter-level `load_table` implementations for Spark
  mixin platforms, cloud SQL platforms, and Snowflake/ClickHouse adapters. Format support is
  gated on adapter configuration so it is only available on platforms that implement it.
- **Expanded format capability registry** - Registered format capabilities for Hudi,
  Presto/Trino, Snowflake, ClickHouse, Redshift, BigQuery, and Spark-based platforms including
  cloud lakehouse variants (EMR, Dataproc, Glue, Fabric Spark, Synapse Spark, Dataproc
  Serverless). Removed registrations for platforms without actual loading code (including
  LakeSail for delta/iceberg/hudi).
- **Mutation testing** - Added `mutmut` mutation testing targeting 5 critical modules
  (`duckdb.py`, `adapter.py`, `runner.py`, `chart_generator.py`, `run.py`) with a
  `make mutation-test` Makefile target for manual quality reviews.

### Fixed

- **Format capability registry accuracy** - Normalized platform display names to match registry
  keys and removed platforms from format registrations where adapter code has no actual loading
  implementation.
- **CLI recursive import** - Fixed a circular lazy-import in the benchmarks module that caused a
  `RecursionError` on CLI startup.
- **CoffeeShop SA2 query** - Corrected `group_by` column name from `'name'` to
  `'product_name'`.
- **Textcharts API migration** - Migrated to textcharts v0.1.2 API after breaking changes,
  renamed `ASCII*`-prefixed classes across 10 source and test files, removed 3 unused deprecated
  factory imports, and regenerated golden snapshots for neutralized defaults.
- **pytest-xdist worker title patch** - Tightened the xdist worker title monkeypatch to prevent
  test pollution across parallel workers.
- **Comprehensive Windows CI compatibility** - Fixed 80+ Windows test failures spanning path
  separators (`.as_posix()` for forward-slash comparison), file encoding (`encoding="utf-8"`
  for `write_text()`), numpy int32 overflow on 64-bit multiplication, Rich Console width on
  headless CI, Python ABI tag format differences (`.pyd` vs `.so`), `Path.touch()` vs
  `time.time()` mocking, NTFS directory `st_size` returning 0, Windows CWD locks preventing
  temp directory cleanup, and `shutil.copytree` replacing symlinks for TPC-DS template setup.
- **TPC-DS dsqgen Windows option prefix** - Fixed dsqgen invocation on Windows where the binary
  expects `/` option prefixes instead of `-` (`OPTION_START` in `r_params.c`), and switched to
  relative paths to stay under dsqgen's 80-char `PARAM_MAX_LEN` buffer.
- **Missing `tpcds.idx` distribution file** - Added the required TPC-DS distribution index file
  to Windows binary packages (both x86_64 and ARM64).
- **Throughput test timer resolution** - Used `time.perf_counter()` for throughput duration
  calculation to avoid zero-duration results from low-resolution `time.time()` on Windows.

### Changed

- **Test suite quality overhaul** - Deleted 13 hollow coverage-theater test files and replaced
  them with behavior-verifying tests for DuckDB, SQLite, and DataFusion adapters. Replaced mock
  credential tests with real file-based tests. Strengthened 150 `is-not-None` assertions across
  5 test files, replaced hollow `isinstance` assertions with behavioral checks, and swapped
  `MagicMock` for `SimpleNamespace` on attribute-only objects. Removed per-file coverage
  enforcement in favor of a suite-wide 60% threshold.
- **~316 rendering tests migrated to textcharts** - Pure chart-rendering tests moved from
  BenchBox's test suite to the standalone textcharts library, with shim import smoke tests
  retained in BenchBox to verify re-export paths.
- **Pytest lane restructure** - Converted test lanes from implicit timing heuristics to explicit
  source markers with measured-timing-based rebucketing. Restored a lightweight fast lane,
  serialized stress tests, re-laned cloud adapter tests to `slow+cloud_import`, and documented
  pytest-xdist safety requirements.
- **Chart subtitle simplified** - Migrated chart subtitle storage from a metadata dict to a
  plain string, removing an unnecessary layer of indirection.
- **Verbose logging extracted** - Moved verbose logging configuration from `run.py` into a
  dedicated `cli/verbose_logging.py` module.
- **Visualization constants** - Extracted magic numbers into named constants across
  visualization modules.

## [0.1.4] - 2026-03-03

### Added

- **`power_bar` chart type** - Added a horizontal bar chart for TPC Power@Size comparisons.
  Higher values are treated as better (opposite of `performance_bar`), powered by
  `summary.tpc_metrics.power_at_size` and exposed in `NormalizedResult`.
- **`power_bar` template coverage** - Added to `flagship`, `head_to_head`, `trends`,
  `regression_triage`, and `executive_summary`. The chart renders only when TPC metric data is
  present and is skipped for non-TPC runs.
- **Driver-version-aware chart labeling** - Multi-platform chart series labels and run summaries
  now include driver version context so version comparisons stay explicit in rendered output.
- **Runtime ABI validation for isolated drivers** - Added ABI compatibility checks to isolated
  runtime discovery so driver auto-install paths fail fast with actionable validation errors
  instead of late runtime crashes.
- **Presorted data-generation modes for table formats** - Added `parquet-sorted` output mode,
  plus `delta-sorted` and `iceberg-sorted` organization paths with clustering primitives
  (z-order, Hilbert, partition-aware sorting) and `cluster-by` tuning integration.

### Fixed

- **Query plan capture correctness and persistence** - Fixed multiple plan-capture defects:
  forwarding `capture_plans` through `RunConfig`, DuckDB JSON plan parsing edge cases,
  preservation of `query_plan` through normalization, and `show-plan` / `compare-plans`
  loading through the standard result-file path.
- **SSB dot-notation query IDs** - `--queries` now accepts IDs like `Q2.1`, and plan-oriented
  CLI flows preserve dotted IDs instead of normalizing them away.
- **Result timing pipeline accuracy** - Fixed datagen/load timing propagation end-to-end,
  including per-table load timings in `table_statistics`, corrected load-phase duration keying,
  datagen phase duration and manifest stats in metadata, and explicit total duration override
  propagation in result builders. Data-only runs now correctly execute generation, and
  `force_regenerate` is forwarded through CLI and runner paths.
- **ASCII visualization readability under skewed data** - Fixed outlier handling across chart
  types (bar, histogram, stacked, scatter, line, CDF, percentile ladder, heatmap), addressed
  zero-heavy fallback truncation edge cases, improved natural query sorting and color cycling,
  and raised effective render width cap from 120 to 400 characters.
- **`--quiet` output contract for automation** - Quiet mode now emits only the bare result
  filepath to stdout, removing decorative output that broke script parsing.
- **Runtime environment stability** - Fixed interpreter targeting for driver auto-install,
  corrected `auto_install_used` state propagation, and resolved SIGSEGV-class failures when
  `driver_auto_install=true` reused an already-matching version.
- **Additional correctness fixes** - Restored `ai_primitives` registry resolution fallback,
  corrected SQLite `force_recreate` option handling, fixed SSB customer row-count expectation in
  `SSBRowCountStrategy`, and resolved visualize command crashes / multi-series rendering issues.

### Changed

- **Plan-capture default now uses actual execution timing** - `--capture-plans` now defaults to
  `EXPLAIN (ANALYZE, FORMAT JSON)` behavior via `analyze_plans=True`, recording measured timing
  in captured plans. Users can opt out with `analyze_plans: false` for estimate-only capture.
- **Benchmark runtime/result internals harmonized** - Refactored enhanced result construction to
  use a shared factory path and aligned canonical runtime behavior for benchmarks like NYC Taxi
  and TSBS DevOps.
- **`make test-all` resource policy and parallelism** - Resource-heavy tests are now serialized
  to prevent machine stalls, while slow/performance suites are moved to a dedicated stress lane.
  The test suite also replaces fixed sleeps with bounded polling, reduces fixture/harness
  duplication, and shifts selected CLI/e2e coverage to in-process runners for faster execution.
- **CI quality gates tightened** - Added required table-format integration coverage and promoted
  doc checks (linkcheck, example validation, docstring coverage) plus security audit policy
  controls to blocking CI behavior.

## [0.1.3] - 2026-02-23

### Added

- **Driver version pinning** - New `--platform-option driver_version=X.Y.Z` flag to pin any
  platform's Python driver to a specific version. Pair with `driver_auto_install=true` to have
  BenchBox install the requested version automatically via `uv`. All platforms support these
  options; the active driver version is now displayed in the run announcement line.
- **Bulk multi-shard table loading** - New `load_table_bulk()` interface on `FileFormatHandler`
  lets the DataLoader ingest multi-shard tables in a single native call instead of
  row-by-row iteration. DuckDB (CSV, Parquet) and ClickHouse Native handlers are the first
  implementations; benchmark runs with sharded TPC-DS data are measurably faster.
- **Greyscale / no-color ASCII chart fallbacks** - All seven ASCII chart types now have
  fill-pattern and glyph-based differentiation when color is unavailable (e.g. CI logs,
  `NO_COLOR`, piped output). `ASCIIBarChart` uses Unicode fill blocks, `ASCIIComparisonBar`
  uses hatch patterns, and the heatmap cell shading is legible without ANSI colors. A
  standardized `no_color` detection path is shared across all chart renderers, and
  ASCII-only rendering is covered by new unit tests.
- **Five new ASCII chart types** - Percentile ladder, stacked bar, sparkline table, CDF
  (cumulative distribution), rank table, and normalized speedup (log₂-scaled baseline
  comparison). All types are registered in the chart registry, accessible via CLI and MCP,
  and included in the flagship/default chart templates.
- **Post-run summary charts** - Charts are automatically generated and displayed in the
  terminal after every benchmark run and included in MCP `run_benchmark` responses.
- **Three new chart template bundles** - `latency_deep_dive`, `regression_triage`, and
  `executive_summary` template sets added alongside the existing default and flagship bundles.
- `fabric-dw` as a preferred CLI alias for `fabric_dw` platform

### Fixed

- **Driver auto-install version switching** - `sys.modules` and metadata caching could return
  stale package metadata after `driver_auto_install` swapped in a different driver version,
  causing the wrong version to be used for the remainder of the run. The module cache is
  now invalidated on version switch.
- **DataFrame cache path mismatch** - DataFrame mode cached generated data under a different
  directory structure than SQL mode, forcing redundant data generation when switching between
  modes on the same scale factor. Both modes now share a flat directory layout.
- **ClickHouse zstd double-decompression** - `ClickHouseNativeHandler` was applying manual
  zstd decompression on top of the driver's built-in decompression, corrupting data for
  compressed bulk loads.
- **Platform display names** - Corrected display names for Amazon Athena (was "AWS Athena"),
  Google Cloud Dataproc (was "GCP Dataproc"), Microsoft Azure platforms, Databricks (now
  "Databricks SQL"), and `adapter.get_platform_info()` propagated to match.
- CLI warning logged when a platform option's default value is not in the declared choices list
- Ranking normalization crash when all metric values are negative finite numbers
- PySpark SIGINT handler hanging `pytest-xdist` workers in medium-speed test runs
- `--validation-mode` CLI prompt crash when `spec.default` is not a string

### Changed

- **Four platform drivers moved to optional extras** - DuckDB (`benchbox[duckdb]`), Polars
  (`benchbox[polars]`), ClickHouse Cloud (`benchbox[clickhouse-cloud]`), and psycopg2
  (`benchbox[postgresql]`) are no longer hard dependencies. Users installing BenchBox now get
  a leaner core and can pin each driver independently. `pip install benchbox[all]` restores
  the previous behaviour.
- All user-facing terminal output in the run pipeline now flows through `emit()`, making
  `--quiet` suppression and output capture in tests consistent.
- `BaseQueryCatalogMixin` and `TranslatableQueryMixin` extracted from duplicate query-catalog
  implementations across benchmark classes.

## [0.1.2] - 2026-02-09

### Added

- **DataFrame mode for all benchmarks** - Complete DataFrame query implementations across all
  18 benchmarks including TPC-DS (102 queries), TPC-H (22 queries), SSB (13q), ClickBench (43q),
  NYC Taxi (25q), TSBS DevOps (18q), H2ODB, AMPLab, CoffeeShop, TPC-H Skew, and Data Vault.
  DataFrame platforms now include Polars, DuckDB, DataFusion, PySpark, Pandas, Modin, Dask,
  and cuDF (GPU).
- **ASCII chart visualizations** - Replaced Plotly HTML charts with terminal-native ASCII
  rendering. Seven chart types: performance bar, distribution box, query heatmap, comparison bar,
  diverging bar, summary box, and query latency histogram. Charts include ANSI colors, Unicode
  box-drawing, and best/worst highlighting.
- **14 new SQL platform adapters** - PostgreSQL, Trino, PrestoDB, Apache Spark, AWS Athena,
  Azure Synapse, Microsoft Fabric, Firebolt, MotherDuck, InfluxDB 3.x, TimescaleDB, ClickHouse
  Cloud (first-class), Onehouse Quanton, and managed Spark variants (EMR, Dataproc, Glue,
  Fabric Spark, Synapse Spark, Dataproc Serverless).
- **Open table format support** - Delta Lake, Apache Iceberg, Apache Hudi, DuckLake, and Vortex
  columnar format with format conversion orchestration and manifest v2 for multi-format tracking.
- **Physical tuning DDL generation** - Platform-specific DDL generators for DuckDB, Snowflake,
  Redshift, BigQuery, ClickHouse, Firebolt, PostgreSQL, TimescaleDB, Trino/Presto/Athena, and
  Spark family (Delta, Iceberg, Parquet, Hive) with sort keys, partitioning, clustering, and
  compression support.
- **Query plan capture and comparison** - Plan parsers for DuckDB, PostgreSQL, Redshift,
  DataFusion, and SQLite. Comparison engine with regression detection, fingerprinting, historical
  tracking with flapping detection, and CLI visualization.
- **Interactive CLI wizard** - Guided benchmark configuration with platform selection, tuning
  wizard, scale factor validation, phase/query selection, onboarding system, and persistent
  preferences.
- **TPC-DI benchmark** - Complete implementation across 4 phases: core schema, query suite,
  ETL pipeline, and validation/testing.
- **Cross-platform comparison engine** - `benchbox compare` command with multi-platform
  analysis, SQL vs DataFrame comparison, and unified visualization.
- **Unified tuning configuration** - YAML-based tuning system with per-platform DDL generation,
  write-time physical layout configuration, and dry-run preview support.
- Cloud storage and deployment modes for S3/GCS/ADLS/DBFS with credential setup wizard and
  cost estimation for Snowflake, Redshift, Synapse, Fabric, and Firebolt
- TPC compliance improvements: stream-aware validation, query permutations, warmup/measurement
  iterations, maintenance operations (RF1/RF2), and `--seed` for reproducibility
- Configurable compression (zstd, gzip, none) across all benchmarks and data generation
- New benchmarks: AI/ML Primitives, Metadata Primitives, Write Primitives, Transaction Primitives
- MCP server: `suggest_charts` and `generate_chart` tools, platform/mode parameters
- `--queries` flag for running specific query subsets, `--validation-mode` flag,
  tiered `--help`

### Fixed

- **TPC-DS data generation reliability** - Fixed segfaults with fractional scale factors,
  parallel generation errors, streaming compression, and chunked file handling.
- **Cloud platform stability** - Fixed credential refresh errors, schema creation ordering,
  UC Volume uploads, S3 key handling, and BigQuery/Snowflake/Redshift/Databricks adapter issues.
- **Type safety** - Multi-phase type checking campaign resolving 150+ type errors across
  production code with proper annotations and TYPE_CHECKING imports.
- **SQL dialect translation** - Fixed SQLGlot compatibility for DuckDB, ClickHouse, DataFusion,
  and Netezza dialects; resolved reserved keyword quoting and identifier case sensitivity.
- Security hardening: SQL injection prevention, parameterized queries, path traversal protection
- CLI hanging in non-interactive mode, progress display precision, `--quiet` mode propagation
- TPC compliance: correct stream permutations, maintenance phase SQL execution, Power@Size
  calculation parity between SQL and DataFrame modes

### Changed

- Dropped Plotly HTML charts in favor of ASCII-only rendering
- Lazy-load cloud platform adapters to speed up CLI startup and test suite
- Optimized TPC-DS smoke tests with selective table generation

## [0.1.1] - 2026-01-24

### Fixed

- **Critical: TPC-H/TPC-DS query templates missing from wheel distribution** - BenchBox installed
  from PyPI via wheel could not run TPC-H or TPC-DS benchmarks because query template files were
  stored outside the package tree and excluded from wheels. Templates are now bundled inside
  `benchbox/_binaries/*/templates/` with a resolution utility that checks the bundled location
  first and falls back to `_sources/` for development installs.
- **dsqgen path buffer overflow** - TPC-DS query generation could fail on systems with long temp
  directory paths (e.g., macOS `/var/folders/...`) due to dsqgen's internal 80-char path buffer.
  Fixed by using short symlinks in the temp directory.
- Python 3.10 compatibility for CLI, version utilities, and `tomllib` imports
- Windows CI test failures and cross-platform compatibility issues
- DuckDB version compatibility in tests
- MCP server: XSS prevention and strengthened path traversal checks

### Added

- MCP server: 7 new tools (`get_query_details`, `detect_regressions`, `get_performance_trends`,
  `aggregate_results`, `get_query_plan`, `export_results`, `export_summary`) and 2 prompts
- GitHub Actions PyPI publishing with trusted publishers
- Release automation: `--push`, `--auto-continue`, CI validation integration, bidirectional sync
- Platform adoption tiers (`recommended`, `supported`, `experimental`, `preview`) replacing
  boolean `recommended` field

### Changed

- Minimum Python version explicitly documented as 3.10
- MCP server refactored to use public API instead of CLI internals
- Benchmark metadata centralized into single registry
- Per-platform TPC-DS query template duplicates removed (530 files, ~4MB saved from wheel)
- MANIFEST.in expanded to include TPC patches, EULAs, and compilation infrastructure for
  sdist users who build from source

## [0.1.0] - 2026-01-10 (Initial Release)

> **Alpha Software**: BenchBox is alpha software. APIs may change without notice, features may be incomplete, and production use is not recommended. See [DISCLAIMER.md](DISCLAIMER.md) for full details.

### Overview

BenchBox v0.1.0 is the **initial public release** of the database benchmarking framework. BenchBox makes it simple to run industry-standard benchmarks (TPC-H, TPC-DS) on analytical databases, from embedded engines like DuckDB to cloud data warehouses like Snowflake and Databricks.

### What's Included

**Benchmarks** (18 total):
- **TPC Standards**: TPC-H (22 queries), TPC-DS (99 queries), TPC-DI
- **Academic**: SSB, AMPLab, JoinOrder (IMDB dataset)
- **Industry**: ClickBench, H2ODB, NYC Taxi, TSBS DevOps, CoffeeShop
- **Data Modeling**: TPC-H Data Vault
- **BenchBox Primitives**: Read Primitives, Write Primitives, Transaction Primitives
- **Experimental**: TPC-DS-OBT, TPC-Havoc, TPC-H Skew

**SQL Platforms** (16 total):
- **Embedded**: DuckDB, SQLite, DataFusion
- **Cloud Data Warehouses**: Snowflake, Databricks, BigQuery, Redshift, Azure Synapse
- **Analytical Databases**: ClickHouse, Trino, Presto, Firebolt, InfluxDB
- **General Purpose**: PostgreSQL, Spark, Athena

**DataFrame Platforms** (8 total):
- **Expression Family**: Polars, DataFusion, DuckDB, PySpark
- **Pandas Family**: Pandas, Modin, Dask, cuDF (GPU)

**Core Features**:
- Self-contained data generation (no external tools required)
- Automatic SQL dialect translation between platforms
- CLI with dry-run support, progress bars, and rich output
- Programmatic Python API for integration
- Result export in JSON, CSV, and HTML formats

### Quick Start

```bash
pip install benchbox

benchbox run --platform duckdb --benchmark tpch --scale 0.01

benchbox run --platform polars-df --benchmark tpch --scale 0.01
```

### Links

- **Documentation**: [GitHub Repository](https://github.com/BenchBox-dev/BenchBox)
- **Discussions**: [Ask questions and request features](https://github.com/BenchBox-dev/BenchBox/discussions)
- **PyPI**: [pypi.org/project/benchbox](https://pypi.org/project/benchbox/)

[Unreleased]: https://github.com/BenchBox-dev/BenchBox/compare/v0.4.2...HEAD
[0.4.2]: https://github.com/BenchBox-dev/BenchBox/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/BenchBox-dev/BenchBox/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/BenchBox-dev/BenchBox/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/BenchBox-dev/BenchBox/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/BenchBox-dev/BenchBox/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/BenchBox-dev/BenchBox/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/BenchBox-dev/BenchBox/compare/v0.1.5...v0.2.0
[0.1.5]: https://github.com/BenchBox-dev/BenchBox/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/BenchBox-dev/BenchBox/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/BenchBox-dev/BenchBox/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/BenchBox-dev/BenchBox/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/BenchBox-dev/BenchBox/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/BenchBox-dev/BenchBox/releases/tag/v0.1.0
