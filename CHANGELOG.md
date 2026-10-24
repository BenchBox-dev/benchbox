# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- A pull request that edits only release enforcement files (`Makefile`, `ci.yml`, the release check scripts) no longer fails `ci-paths` when upstream SQLGlot publishes a new release before the fork is refreshed. The freshness check still runs for pull requests that change the release version or edit the freshness check or its waiver file, and in the release workflows.
- The locked `filelock` moves from 3.25.2 to 4.2.4 to clear CVE-2026-112940, CVE-2026-112913 and CVE-2026-112907, which are fixed only in 4.2.1 and later. `[tool.uv] override-dependencies = ["filelock>=4.2.1,<5"]` is required because every resolvable `snowflake-connector-python` declares `filelock<4` and every `snowflake-snowpark-python` caps the connector below 5. The other locked consumers already accept 4.x (`huggingface-hub>=3.10.0`, `torch` unbounded, `tox>=3.20.3`, `virtualenv<5,>=3.24.2`, `python-discovery>=3.15.4`). The only 4.0.0 breaking change in the filelock changelog is the `SoftReadWriteLock` on-disk protocol, which none of them use; the connector imports only `FileLock`, `Timeout` and `BaseFileLock`. The `uv.lock` hashes for 4.2.4 match PyPI (wheel 301241438c57, sdist c6d8663535e1). The wheel grows from 26,759 to 162,475 bytes and the sdist from 40,480 to 652,444 bytes because the package grew from 11 modules and 76,684 bytes of Python to 23 modules and 573,230 bytes (the soft and read-write lock implementations, `_api.py`, `asyncio.py`), and because the sdist has shipped `docs/` since 4.1.0. Remove the override once the connector and snowpark allow `filelock>=4`.

### Added

- Benchmark runs check free space on the output volume before any data is written and fail with the free and total bytes and the largest top-level entries when less than 5% is free. `BENCHBOX_MIN_FREE_DISK_PERCENT` changes the threshold (`0` disables it). `make disk-check` runs the same check by hand and never deletes anything.
- `translate_ddl` accepts an engine type map by name (`type_map="starrocks"`, `"doris"`, `"singlestore"`) so adapters do not import fork modules.
- Result readers accept schema 2.4: the loader, normalizer, explorer input
  and bundle validator read the new optional per-query `request_count`,
  `result_bytes` and `platform_timing`, and the run-level `timing_method`,
  `platform_timing` and `net_estimate_status`, plus
  `environment.client_link.probed_at`. Producers still write 2.3; the fields
  support comparing local and cloud runs with the client round trip removed.
  A run marked `net_estimate_status: eligible` must use `timing_method` 2 and
  must not be embedded.
- `make sql-execution-gate` executes every shipped TPC-H and TPC-DS query on SQLite, DuckDB,
  DataFusion and PostgreSQL (when `BENCHBOX_POSTGRES_DSN` is set) at SF0.01 and compares the
  rows of the candidate tree with the baseline commit, DuckDB and the TPC reference row counts.
  Differences must be listed with a justification in
  `scripts/sql_execution_gate/expected_differences.json`. The `sql-execution-evidence` CI job
  runs it when SQL translation changes, and the pull request template asks for its output on
  every wheel adoption.
- `benchbox.utils.dialect_utils.translate_ddl(ddl, target_dialect, source_dialect, identify, **options)`
  translates DDL with the bundled SQLGlot runtime's `DdlOptions` and engine-specific
  table layouts. It is the only module allowed to import the runtime's DDL module; a
  repository test fails on any other import.
- The golden-corpus DDL area records the differences between the BenchBox DDL
  fix-ups and the runtime's DDL output for every platform.
- `benchbox.utils.dialect_utils.apply_rewrite(name, sql_or_expression, dialect, **args)`
  applies a named rewrite from the bundled SQLGlot runtime to already-translated
  SQL. It is the only module allowed to import the runtime's rewrite functions;
  a repository test fails on any other import.
- `apply_rewrite` tests pin the named registry: every TPC-Havoc, Spark, DataFusion and engine
  rewrite in the bundled SQLGlot runtime resolves by name, an unlisted runtime function fails
  the registry test, and every compat, subqueries, date-arithmetic and alias-deduplication
  rewrite has a documented-output test. The list-aggregate, zero-divisor, ORDER BY alias,
  ClickHouse alias and decimal-division and rollup-NULL rewrites added in 30.24.0.9 are pinned
  and have documented-output tests too.
- The `round_inputs_to_decimal` rewrite added in 30.24.0.10 is pinned in the same registry test and
  has documented-output tests.
- A DataFusion test executes TPC-H Q11, Q16, Q18 and Q20 rewritten by the runtime's generic
  decorrelation rewrites at scale factors 0.01 and 0.1 and compares the rows with the
  recorded TPC-H reference row counts and with the unrewritten query on DuckDB.
- A golden-corpus test runs the BenchBox fix-ups and the bundled runtime's output
  over the inputs of every area whose fix-ups move into the runtime and asserts
  that the differences equal a recorded set.
- Tests pin the `apply_rewrite` behavior of the ClickHouse, StarRocks and TPC-DS runner
  engine rewrites (`lowercase_identifiers`, `decimal_zero_literals`, `add_query_settings`,
  `correlated_exists_to_in`, `add_derived_table_aliases`, `qualify_order_by_columns`), and the
  golden-corpus engines area records two more safe-division inputs from the ClickHouse
  transformer tests.

### Changed

- The Spark, Velox, LakeSail and Databricks adapters build `CREATE TABLE` statements with
  `translate_ddl` instead of rewriting the translated text: the `USING` format, Delta auto-optimize
  and Hudi table properties, `CREATE OR REPLACE`, and the Parquet and ORC constraint and `SMALLINT`
  stripping are `translate_ddl` options built from the adapter configuration. The cloud Spark DDL
  mixin returns table layouts (`generate_table_layout`) and renders them through `translate_ddl`.
  `optimize_spark_table_definition`, `_convert_to_delta_table`, `_convert_to_hudi_table` and
  `SparkDDLGeneratorMixin.generate_tuning_clauses` are removed. Differences from the old text
  output: a leading `/* comment */` is kept, a named `CONSTRAINT` is removed together with its
  key, a Hive partition column is moved out of the column list, a Parquet `SORTED BY` without
  `CLUSTERED BY` is no longer written, and the two Delta auto-optimize properties are listed in
  a different order.
- The comment-policy check in CI and `make comment-policy-check` scans only the files that differ from the base, as the hook already did, and the whole repository after a global change: the policy, the cleanup scope policy, the launcher, a trusted checker file (or an unreadable trusted-file list), or a removed exception or exclusion. Files holding an exception are also scanned when the exception's consumer changes. The ratchet, review gating, new files under exclusions, consumer validation and expired-exception findings still run on every pull request. A new `Comment Policy Nightly` workflow scans the whole of develop every night and on demand (`scripts/run_comment_policy.py --full`); when any file has a violation it fails and opens or updates an issue, and a clean run closes it.
- `pyproject.toml` now requires uv 0.13.4 or newer (`required-version`); older uv releases fail with a clear error instead of running with root certificates that trust the compromised eMudhra emSign CAs.
- `quality/comment-policy.json` no longer has a `completed` list, and a policy that still has one fails schema validation. Every first-party path is in scope, so there is no list of dead completed prefixes left to prune.
- FlightData queries for PostgreSQL are rendered by `translate_sql_query` with the runtime's
  `round_inputs_to_decimal` rewrite instead of local AST code; the `ast_rewrite` allowlist entry for
  `benchbox/core/flightdata/queries.py` is gone. The ROUND casts are unchanged, but the PostgreSQL
  text no longer quotes identifiers (`translate_sql_query` does not quote for `postgres`), so
  `"f"."dep_delay"` is now `f.dep_delay`. All 20 queries execute on PostgreSQL 16.2 before and
  after. No published FlightData bundle targets PostgreSQL, so no published result changes.
- The comment-policy check compares a branch with its base using the strict rules over the whole repository. It no longer scans the base's sources or reports only newly introduced findings, so every violation fails, including old ones; none exist today. Staged runs check the staged files that differ from the base, and the whole repository when the commit changes the policy, the cleanup scope policy, the launcher or a trusted checker file. The policy ratchet, review gating for new exceptions, checks of new files under external exclusions, consumer validation, expired-exception findings and the base-copied launcher are unchanged. The `completed` list in `quality/comment-policy.json` is no longer read or ratcheted, and the policy may omit it.
- The comment-policy scanner builds each Python file's name index once and parses YAML with the C loader when available, falling back to the pure-Python loader on any error. Findings are unchanged; a whole-tree strict scan is more than twice as fast.
- TPC-DS OBT queries load from a hand-owned static catalog (`benchbox/core/tpcds_obt/catalog/queries.yaml`)
  instead of being converted from the TPC-DS templates at startup. Rendered SQL is unchanged. Each entry records the
  source template's SHA-256, its dsqgen placeholders and its identifier map, and unit tests fail when a template,
  define or identifier choice drifts. `benchbox/core/tpcds_obt` no longer imports SQLGlot, and Doris and StarRocks
  OBT queries get derived-table aliases from the fork's `add_derived_table_aliases` rewrite through
  `translate_sql_query`.

- `vector_search` declares its embedding columns with an explicit 4-byte element type on every
  engine (`REAL[n]` on DuckDB and the generic default, `ARRAY<REAL>` for Spark, Databricks, Doris,
  StarRocks, LakeSail, PySpark and Velox), matching the float32 data it generates. Engines that read the
  old `FLOAT` as double precision now store `REAL`: DuckDB (`DOUBLE[n]` to `REAL[n]`), Spark and
  Databricks (`ARRAY<DOUBLE>` to `ARRAY<FLOAT>`), Doris (`ARRAY<DOUBLE>` to `ARRAY<FLOAT>`), Trino
  (`ARRAY(DOUBLE)` to `ARRAY(REAL)`) and Redshift (`DOUBLE PRECISION` to `REAL`). Top-k ids are
  identical, but `vector_search` load and query timings change on those six engines, so do not compare
  them with earlier runs. PostgreSQL, Snowflake, ClickHouse, SingleStore, StarRocks and BigQuery keep
  their column types. A guard test rejects bare `FLOAT` in benchmark schema specs, the Snowflake
  FlightData type map writes `DOUBLE` instead of `FLOAT` (the same 64-bit type), and the StarRocks and
  SingleStore adapters no longer pass `float_as_real` because no benchmark schema declares a bare `FLOAT`.
- DuckDB and SQLite build their schema script with one `translate_ddl` call that also removes the TPC-DS foreign keys, instead of `translate_sql` followed by a regex strip; the generated statements are unchanged. The `ddl_helpers` regex helpers `strip_foreign_keys`, `strip_primary_keys` and `strip_tpcds_foreign_keys` are removed; the inventory keeps the `_optimize_table_definition` / `_transform_create_statement` signature detection so a reintroduced per-adapter DDL hook still fails the drift check; the `sql_compat` README, the SQLGlot and phase-aware-pipeline ADRs and the new-platform guide describe `translate_ddl` instead of per-adapter hooks.
- The TPC-DS OBT schema, ETL, `CREATE TABLE` statement and lineage page come from one declarative model,
  `benchbox/core/tpcds_obt/model.yaml`. The table gains 12 roles and 197 columns (518 to 715 at
  `dimension_mode=full`): the billing and returning customer's current address, demographics and household
  demographics, the billing customer's first sales and first ship-to dates, and the return-side store, call center,
  catalog page and web page. Column lineage is derived from the columns the ETL reads, which corrects `store_sk`
  and other fact columns that named a returns column the ETL never used. A full-width build at scale factor 1 or above
  fails when a calendar day from 1998 to 2002 has no sale or an item never appears, and the manifest records the
  coverage. The OBT benchmark version is 0.2, so published SQL OBT results need a rerun.

- The PostgreSQL, DuckLake, pg_mooncake and QuestDB adapters build `CREATE TABLE` text through
  `translate_ddl` instead of their own rewrites. QuestDB writes `VARCHAR` for `CHAR(n)` and
  `VARCHAR(n)` (QuestDB rejects the lengths), DuckLake quotes identifiers, and the pg_mooncake
  mirror statements come from the SQLGlot runtime. The key
  stripping, `SYMBOL` columns, designated timestamp and partitioning keep their results.
- Trino, Presto and Athena build their `CREATE TABLE` DDL with `translate_ddl` instead of
  adapter-side text rewrites. Table names are written quoted (`"orders"` on Trino and Presto,
  `` `orders` `` on Athena) instead of bare, and Athena columns are backtick-quoted; the
  statements are otherwise equivalent. `ddl_helpers.strip_with_properties` is removed.
  `translate_ddl` records one schema-DDL translation outcome per call, so schema statements still
  appear in the result's SQL translation summary.
- SingleStore `CREATE TABLE` statements are written by the SQLGlot fork's `translate_ddl` (foreign keys
  stripped, lower-case table names, `FLOAT` as `REAL`, `TIMESTAMP` as `DATETIME`, and the TPC-H
  `SHARD KEY`, `SORT KEY` and `REFERENCE TABLE` clauses) instead of adapter text rewrites. The
  `BaseDdlOptimizer` mixin and the `singlestore_*` rewrite methods are removed; the four SingleStore
  DDL rules remain as governance rows naming the `translate_ddl` options. Array columns, which SingleStore
  cannot store, now become `VECTOR(n, F32|F64)` rather than invalid DDL.
- ClickHouse `create_schema` writes its table DDL with `translate_ddl` (MergeTree engine, `Nullable` only
  for the columns the benchmark schema marks nullable, tuned `PARTITION BY` / `ORDER BY` / `PRIMARY KEY`)
  instead of the adapter's own text rewriting. The adapter's `_optimize_table_definition`,
  `_strip_primary_key_constraints` and `Nullable`/key text helpers are removed. Types are spelled the
  ClickHouse way (`Int32`, `String`, `Decimal(15, 2)`, `DateTime`), and tuned key columns that are
  nullable fail with the table name and the column names. Cases are listed in
  `tests/unit/sql_compat/golden_corpus/recorded/area_ddl.json`.
- Databend and Firebolt build their `CREATE TABLE` statements with the SQLGlot fork's `translate_ddl`
  instead of per-adapter text rewrites (`_optimize_table_definition`, `_normalize_table_name_in_sql`).
  Emitted DDL matches the golden corpus; both still drop primary and foreign keys.
- Azure Synapse and Fabric Warehouse build their `CREATE TABLE` statements with `translate_ddl`
  instead of per-adapter text rewrites. Tables are always qualified with the configured
  `[schema]`, and the generated statement is wrapped in a `TABLE_SCHEMA`-guarded
  `IF NOT EXISTS ... EXEC('CREATE TABLE ...')`. Synapse still appends
  `WITH (DISTRIBUTION=<distribution_default>)`, now written without spaces around `=`.
- BigQuery builds `CREATE TABLE` text through `translate_ddl` instead of the adapter's own DDL
  rewriter, which is deleted. It still writes `CREATE OR REPLACE TABLE`, qualifies `project.dataset`,
  upper-cases table names and writes `PRIMARY KEY ... NOT ENFORCED`; configured partitioning and
  clustering become a table layout (at most four clustering columns are kept). Temporary tables keep
  their name. The output differs in spelling only: project, dataset and table are written as
  separate identifiers, `STRING(n)` keeps its length, and fixed-size arrays are written as plain arrays.
- Redshift and Snowflake build `CREATE TABLE` text through `translate_ddl` instead of the
  adapters' own DDL rewriters, which are deleted. Redshift still adds `DISTSTYLE AUTO SORTKEY AUTO`
  and Snowflake still writes `CREATE OR REPLACE TABLE`. Snowflake keeps the case of quoted
  identifiers instead of upper-casing them, and fixed-size arrays are written as plain arrays.
- TPC-Havoc, Spark and DataFusion query rewrites run through the bundled SQLGlot fork's named rewrites
  (`translate_sql_query(..., rewrites=...)`); the BenchBox AST rewriters, `SparkTPCHavocQueryTransformer` and the
  regex `DataFusionQueryTransformer` are removed. DataFusion TPC-H Q11, Q18 and Q20 use the fork's decorrelation and
  Q16 now rewrites `NOT IN` to `NOT EXISTS`. The earlier regex never matched the shipped Q16 text, so published
  DataFusion Q16 runs executed the unrewritten `NOT IN` form that the registry marks as silent corruption on
  DataFusion; those Q16 rows and timings, and the plans and timings for Q11, Q18 and Q20, can differ from earlier
  results. The new rewrites are validated against DuckDB and the TPC row counts. The
  TPC-Havoc 17_v4 PostgreSQL text no longer contains `FROM (VALUES (1))`. The TPC-DI EQ7 base query is the derived-table form
  (no `FROM (VALUES (0))` row source), so EQ7 results on engines that rejected the VALUES form change; published EQ7
  results should be treated as superseded.
- TPC-Havoc `1_v7` on Presto, Trino, Athena, BigQuery, Spark and Databricks is translated from its DuckDB text
  with the fork's `order_list_aggregates` (ordered by `l_orderkey, l_linenumber`) and `reduce_list_aggregates`
  rewrites; the BenchBox AST rewriter for the array aggregation is removed and `translate_sql_query` accepts a
  `dialect` entry in a rewrite's arguments to run that rewrite under the target dialect. Presto, Trino and Athena now
  reduce the arrays with `REDUCE`/`ZIP_WITH` instead of `UNNEST`; BigQuery reduces them with `GENERATE_ARRAY`
  subqueries; Databricks uses the fork's `SORT_ARRAY` collect and `AGGREGATE`; Spark, which previously received
  untranslated `LIST` calls, now gets the same text as Databricks. Identifiers are no longer quoted. On Trino 476,
  PrestoDB 0.298.1 and PySpark 4.2.0 at TPC-H tiny scale the new text returns the DuckDB rows (relative tolerance
  1e-9); Athena runs the same text and was not available. BigQuery has no emulator and stays skipped. No published bundle holds a `1_v7` row for these engines; the DuckDB bundles are
  unchanged. Read Primitives output is unchanged on all 21 dialects.
- A pull request title or body edit no longer holds five runners while it waits for the full CI run. The
  `CI (text edit)` check makes one decision and fails at once when the full run on the commit is missing or still
  running; the new `ci-text-edit-refresh` workflow re-runs the failed jobs of the newest `CI (text edit)` run on
  that commit when the full CI run completes. The `oracle-gate` workflow now shares one concurrency group per
  pull request head commit for runs started by `pull_request` or `pull_request_target` workflows, so superseded
  gate runs are cancelled.
- Results anonymization precompiles the key-normalising pattern and caches the
  normalised key and the identifier prefix for a key path (bounded caches, both pure
  functions of their arguments). Anonymizing the committed results corpus is about a third
  faster locally; published output is byte-identical.
- StarRocks and Doris `CREATE TABLE` statements are now written by the SQLGlot fork's `translate_ddl`
  instead of regex rewrites in the adapters. Doris keeps the TPC-H key, distribution and partition tables
  and its `table_model`, bucket and replication options; StarRocks keeps the leading-primary-key rule
  (`PRIMARY KEY` model when the key columns lead the table, else `DUPLICATE KEY` on the first column).
  Output differences: identifiers are quoted, a leading primary key written on one line now selects the
  `PRIMARY KEY` model, and statements other than `CREATE TABLE` on Doris are sent unchanged. A Doris
  `unique` or `aggregate` table model on a table whose key columns do not lead the table now raises
  instead of silently writing a narrower key. A test runs both adapters over every shipped schema.
- **ClickHouse, StarRocks and TPC-DS runner rewrites now run through the bundled SQLGlot runtime.**
  `ClickHouseQueryTransformer`, the StarRocks execute-time regexes and scanner, the ClickHouse
  Q10/Q35 overrides and the TPC-DS interval and Q58 regexes are deleted. ClickHouse execution calls
  `apply_rewrite` (`benchbox.platforms.clickhouse.query_rewrites`), StarRocks execution calls
  `add_derived_table_aliases`, and TPC-DS uses `correlated_exists_to_in` and
  `qualify_order_by_columns`. String literals are no longer edited, `(x + 0.0)` is cast to
  `Decimal(15, 2)`, an existing `SETTINGS` clause is replaced, JOIN-derived and nested StarRocks
  subqueries receive aliases, and a double-quoted StarRocks token keeps its string-literal reading.
  Unparseable SQL still fails before reaching the engine. Q58 on BigQuery, Spark, Databricks and
  SingleStore now qualifies `item_id` in `ORDER BY`; published Q58 results on those targets ran
  the ambiguous form. ClickHouse query text is unchanged for 745 of 749 benchmark queries (the
  rest gain the session setting) and published ClickHouse results do not change.
- TPC-DS query generation no longer copies the whole query-template tree into a temporary directory
  for every `dsqgen` call. It stages only the support files and the one requested template, so a
  call takes about 6 ms instead of about 25 ms locally with byte-identical SQL. The installed
  template tree is never written.
- `benchbox run` registers only the requested platform's adapter instead of importing all 52
  adapters, and checks DataFrame availability for the requested platform only. A local
  `run --dry-run` for Polars drops from about 2.0 s to about 1.2 s and for DuckDB from about
  1.85 s to about 1.3 s. Commands that list platforms still register every adapter, and the
  CLI output, unknown-platform message and exit code are unchanged.
- `ResultValidator.validate_results_exact` now accepts result sets that are exactly equal
  (in the same order, or after sorting when order does not matter) without the per-cell
  tolerant comparison, which makes large cross-surface and qualification comparisons much
  faster. The shortcut never rejects: results containing NaN, non-finite decimals, integers
  too large for a float, or values that cannot be sorted or compared go through the existing
  comparison, so accepted and rejected outcomes and error messages are unchanged.
- The Databricks adapter now requests the bundled SQLGlot runtime's opt-in
  `safe_division` rewrite through `apply_rewrite` after alias deduplication.
  Every division, including nested ones, still becomes `TRY_DIVIDE`, so a zero
  divisor still returns NULL. Queries without a division are now re-rendered by
  the runtime (for example comma joins become `CROSS JOIN`) instead of being
  passed through verbatim; the result sets are unchanged.
- The BigQuery adapter now requests `safe_division` from the bundled SQLGlot
  runtime through `apply_rewrite` instead of its own division rewrite. A zero
  divisor still yields NULL. Nested divisions (`a / (b / c)`, TPC-DS Q58 and Q83)
  are now rewritten at every level, so a zero inner divisor yields NULL instead
  of an error. Queries are no longer emitted with every identifier quoted.
- The Snowflake adapter now requests the bundled SQLGlot runtime's opt-in
  `safe_division` rewrite through `apply_rewrite` before executing a query. A zero
  divisor still yields NULL (`a / NULLIF(b, 0)`); a divisor already written as
  `NULLIF(x, 0)` is no longer wrapped a second time. Queries without a division
  are regenerated from the parsed tree, so their text can differ in formatting
  but not in meaning. No published Snowflake result changes.
- Removed the wrapper fix-ups (DuckDB `ALL` restore, SQLite interval/EXTRACT/DATE_TRUNC rewrites, SQLite discount-bound fold, TPC-DS date-integer regex), `normalize_dialect_for_sqlglot` and `benchbox/platforms/questdb_rewriter.py`. `resolve_sql_dialect` and `PLATFORM_DIALECTS` map platform keys to runtime dialects; an unknown name raises `UnknownSqlDialectError`. `influxdb` resolves to `datafusion`.
- QuestDB now translates to the runtime `questdb` dialect. TPC-H Q15 and every CTE column list keep their column names, which the removed rewriter dropped.
- SQLite month/year intervals on a date with day of month above 28 now fail instead of returning a rolled-over date.
- `netezza`, `greenplum`, `vertica` and `datafusion` use their own runtime dialects instead of postgres; TPC-DI date-difference output changes on ClickHouse, StarRocks, BigQuery, Databricks, DataFusion and Snowflake.
- TPC-DI schema, query, loader, validation and SCD translation now call
  `translate_sql_query` with explicit `identify=False` instead of the vendored
  SQLGlot directly, so the DuckDB `ALL` restore and SQLite fix-ups apply there
  too. Output differences caused by routing: `TPCDIQueryManager.translate_query_text`
  now translates through the wrapper's PostgreSQL mapping of `ansi` instead of
  returning the original text after an unknown-dialect error, and the
  `datafusion` target now translates TPC-DI DDL and validation SQL through the
  same mapping where it previously returned the PostgreSQL text unchanged.
  Translation failures in strict mode now raise `SQLTranslationError` instead of
  being logged and skipped.
- **ClickHouse zero-divisor handling now comes from the bundled SQLGlot runtime.**
  `ClickHouseQueryTransformer.transform` requests the runtime's `safe_division`
  rewrite through `apply_rewrite`, which wraps every non-literal divisor as
  `nullIf(divisor, 0)`, including `Nullable(Decimal)` casts. The previous regex
  missed divisors after a scaled numerator (`x * 100 / SUM(x) OVER (...)`,
  TPC-DS Q12, Q20 and Q98), TPC-DS Q49's `CAST(...) / CAST(...)` ratios and
  TPC-H Q14's `... END) / SUM(...)`; a zero divisor in those expressions
  previously raised an error or produced `inf` and now returns NULL. Results
  differ only when a zero divisor occurs. The query text is also re-rendered by
  SQLGlot (comma joins become `CROSS JOIN`, `date 'x'` becomes `CAST('x' AS DATE)`,
  `NULLIF` becomes `nullIf`), which is semantically equivalent. A query SQLGlot
  cannot parse now fails with `ParseError` before reaching ClickHouse.
  `execution_time_seconds` for ClickHouse queries now starts after the query
  rewrite, so it measures only the engine call.
- **BREAKING: the ParadeDB platform now requires `pg_search`, not `pg_analytics`.**
  Current ParadeDB images no longer ship `pg_analytics`, so the adapter could not
  connect to them. The supported image is `paradedb/paradedb:0.26.2-pg18`
  (PostgreSQL 18.6, `pg_search` 0.26.2); the missing-extension error names it, and
  `latest` is no longer recommended. Servers that only carry `pg_analytics` fail at
  connection time.
- ParadeDB tuning files now apply heap partitioning and clustering/sorting, and the
  adapter claims `PARTITIONING`, `CLUSTERING` and `SORTING`. A TPC-H profile is at
  `examples/tunings/paradedb/tpch_tuned.yaml`.
- pg_mooncake tuning files now apply clustering and sorting, and the adapter claims
  `CLUSTERING` and `SORTING`. `CLUSTER` runs on the loaded heap table before
  `mooncake.create_table` mirrors it, so the mirror's Parquet files inherit the sort
  order; the intent is recorded as applied only after the mirror's files are read back
  and found ordered on the tuned columns. A partitioning request is rejected when the
  tuning file loads, because partitioning the source does not shape the mirror. A TPC-H profile is at
  `examples/tunings/pg-mooncake/tpch_tuned.yaml`.
- CedarDB tuning files now apply hash partitioning, and the adapter claims `PARTITIONING`.
  CedarDB supports only `PARTITION BY HASH` and creates its partitions itself, so every
  partitioning column is hashed and no explicit partitions are emitted; a primary key
  need not include the partition columns. Each request is verified from
  `pg_get_partkeydef`. Clustering and sorting are rejected when the tuning file loads,
  because CedarDB does not implement `CLUSTER`. A TPC-H profile is at
  `examples/tunings/cedardb/tpch_tuned.yaml`.
- The applied-tuning ledger no longer records an intent as satisfied by its executed
  DDL statement after a catalog readback has already recorded it as not applied.
- The bundled SQLGlot runtime moves from 30.24.0.1 to 30.24.0.2 (fork commit
  `010db57fe835`), which adds the `netezza`, `greenplum`, `vertica`, `datafusion`
  and `questdb` dialects, SQLite, DuckDB, Snowflake and Databricks generator
  changes and the rewrite modules behind `apply_rewrite`. A translation corpus of
  every shipped benchmark query across 20 target dialects changes in 16 of 25,240
  outputs. Results published before this change for the affected queries and
  dialects were computed with different SQL:
  - ClickBench Q43, nyctaxi `surcharge-revenue`, `trips-per-day` and
    `trips-per-month`, read_primitives `groupby_all_complex` and
    `timeseries_trend_analysis`, and TSBS `double-groupby-1-hr`,
    `double-groupby-5-min` and `resource-utilization` on SQLite: `TIMESTAMP_TRUNC`
    (not an SQLite function) now emits `STRFTIME` (9 outputs).
  - TPC-Havoc `6_v2` and `14_v2` on Spark, Databricks and Snowflake: the empty
    `GROUP BY ()` is dropped (6 outputs).
  - ClickBench Q29 on Snowflake: the non-capturing group `(?:www\.)?` in the
    `REGEXP_REPLACE` pattern is written as a capturing group, because Snowflake
    lacks non-capturing groups.
- The SQLGlot runtime baseline check (`make sqlglot-repro-retirement-check`) now
  runs the `questdb` case on a QuestDB endpoint named by `BENCHBOX_QUESTDB_DSN`;
  the CI lint job starts a QuestDB service for it. DuckDB `ORDER BY ALL`, `EXTRACT`
  of an input of unknown type on SQLite, and the QuestDB dialect now pass directly
  through the runtime.
- SQLite translation of `DATE('..') + INTERVAL 'N' DAY` now yields
  `DATETIME(DATE('..'), 'N DAY')`, which evaluates to a timestamp string
  (`1998-09-11 00:00:00`) where the previous fix-up gave a date string
  (`1998-09-11`). The runtime raises `UnsupportedError` at `ErrorLevel.RAISE`
  for month and year offsets that roll over a month end and for
  `DATE_PART('year', ..)`; `translate_sql_query` still applies the BenchBox
  fix-ups to them until they are removed.
  Because SQLite compares dates as text, a lower bound written as a day offset
  (`d >= DATETIME(DATE('1998-04-08'), '-30 DAY')`) excludes rows equal to the
  boundary date and a `<` upper bound includes them, where the previous `DATE(..)`
  form did not; the form was already emitted for typed dates by 30.24.0.1.
- Citus tuning files now drive table placement. `distribution` hash-distributes a
  table on its first column, `reference: true` creates a reference table and
  `columnar: true` converts it to the `citus_columnar` access method; each is read
  back from `pg_dist_partition` or `pg_am` before it is recorded as applied. The
  adapter claims `PARTITIONING`, `CLUSTERING`, `SORTING` and `DISTRIBUTION`. CLUSTER
  is verified on every shard through `run_command_on_shards` and recorded as not
  applied on columnar tables and on partitioned distributed tables. A tuning-file
  entry takes precedence over `--citus-distribution-column`. The new `reference` and
  `columnar` table flags are rejected on any other platform. A TPC-H profile is at
  `examples/tunings/citus/tpch_tuned.yaml`.
- SQLite translation of a whole-day offset on a typed date,
  `DATE('..') + INTERVAL 'N' DAY`, yields `DATE(DATE('..'), 'N DAY')`, which
  evaluates to a date string (`1998-09-11`) like the previous fix-up. The runtime
  raises `UnsupportedError` at `ErrorLevel.RAISE` for month and year offsets that
  roll over a month end and for `DATE_PART('year', ..)`; `translate_sql_query`
  still applies the BenchBox fix-ups to them until they are removed.
- The bundled SQLGlot runtime moves from 30.24.0.2 to 30.24.0.3 (fork commit
  `a4dad47db8ca`). A translation corpus of every shipped benchmark query across
  the 20 target dialects and `questdb` (1,262 queries, 26,502 translations, no
  errors in either runtime) changes in 40 outputs. Results published before this
  change for the affected queries on SQLite were computed with different SQL:
  - SQLite, day offsets on a date column compared with date text: 30.24.0.2
    wrote `DATETIME(DATE(..), 'N DAY')`, whose `YYYY-MM-DD HH:MM:SS` text sorts
    after the date text of the same day, so a lower bound excluded the
    boundary-day rows and a `<` upper bound included them. The runtime now writes
    `DATE(DATE(..), 'N DAY')`. TPC-DS queries 5, 12, 16, 20, 21, 32, 37, 40, 77,
    80, 82, 92, 94, 95 and 98 (15 outputs), the same queries in `tpcds_obt` (12
    outputs), TPC-H Q1, TPC-H Skew Q1 and ten TPC-Havoc `1_v*` variants.
  - BigQuery `3_v9` in TPC-Havoc: the runtime drops `NULLS LAST` from the window
    `ORDER BY` of `FIRST_VALUE` when the key is a `PARTITION BY` key, because
    BigQuery rejects a non-default NULL ordering there and the key is constant
    within a partition.
- `apply_rewrite("drop_literal_row_source", ..)` replaces the one-row
  `(VALUES (1)) AS dual(dual_col)` source of a dead `UNION ALL` leg with a
  `FROM`-less `SELECT`; the golden corpus records TPC-Havoc `17_v4` on
  PostgreSQL as the changed output. `flatten_count_of_count` is available the
  same way. The runtime writes no `FROM VALUES` or `dual` source in any dialect.
- The bundled SQLGlot runtime moves from 30.24.0.3 to 30.24.0.7 (fork commit
  `d880f416014b`). A translation corpus of every shipped benchmark query across
  the 20 target dialects and `questdb` (1,262 queries, 26,502 translations, no
  errors in either runtime) changes in 88 SQLite outputs and no other output.
  SQLite no longer divides possibly-integer operands with integer truncation:
  a division the source dialect marks as possibly truncating is written
  `CAST(dividend AS REAL) / divisor` unless an operand is already real or both
  operands are integers (literal-only integer division keeps PostgreSQL
  semantics). Results published before this change for the affected SQLite
  queries were computed with different SQL.
- The bundled SQLGlot runtime moves from 30.24.0.7 to 30.24.0.8 (fork commit
  `be7ef8643780`). A corpus of 1,127 shipped queries (22 benchmarks, frozen once) across
  21 target dialects (23,667 translations, no errors in either runtime) changes in 843
  outputs. Exact division: possibly-truncating divisions are now written with a cast to
  an approximate numeric on Athena, DataFusion, PostgreSQL, Presto, QuestDB, Redshift,
  Teradata, Trino and T-SQL (90 outputs each: TPC-DS 24, TPC-DS OBT 22, TPC-DI 13,
  TPC-Havoc 11, NYC Taxi 6, CoffeeShop 5, FlightData 4, Read Primitives 2, TPC-H 1,
  TPC-H Skew 1, Data Vault 1). SQLite grouping sets (`ROLLUP`, `CUBE`, `GROUPING`),
  population and sample variance and standard deviation, and parenthesised compound
  operands are now expanded to SQL SQLite runs (33 outputs: TPC-DS 14, TPC-DS OBT 12,
  TPC-DI 3, Read Primitives 3, TPC-Havoc 1). Results published before this change for
  the affected queries on these engines were computed with different SQL; TPC-DS
  Q21, Q78 and Q83 on DataFusion returned truncated ratios and now match DuckDB
  (PostgreSQL is not run by the local gate, which needs `BENCHBOX_POSTGRES_DSN`). `make sql-execution-gate` reports 17 verified improvements and lists five
  DataFusion TPC-DS outputs (Q12, Q36, Q59, Q66, Q98) whose ratio columns now carry
  full double precision instead of six decimals.
- The bundled SQLGlot runtime moves from 30.24.0.8 to 30.24.0.9 (fork commit
  `6084c07075ac`). It adds BigQuery, Snowflake and Synapse table layouts to `translate_ddl`,
  the `reduce_list_aggregates`, `nullif_zero_divisors`, `inline_order_by_aliases`,
  `alias_qualified_projections`, `qualify_alias_shadowed_columns`, `decimal_division_to_double`
  and `use_nulls_for_rollups` rewrites to the `apply_rewrite` registry, and keeps temporary
  table names unqualified. BenchBox does not call the new rewrites or pass layouts to those
  three platforms yet: the 23,667 translations of the 1,127 frozen shipped queries across 21
  target dialects, the 2,600 golden-corpus runtime outputs and `make sql-execution-gate`
  are unchanged from 30.24.0.8, so no published result changes.
- The bundled SQLGlot runtime moves from 30.24.0.9 to 30.24.0.10 (fork commit
  `75016d8ff27e`). It adds the `round_inputs_to_decimal` rewrite to the `apply_rewrite`
  registry and writes a one-parameter wide `DECIMAL(p)` as `BIGNUMERIC` for BigQuery DDL.
  BenchBox ships no one-parameter `DECIMAL`, and its first use of the rewrite (FlightData on
  PostgreSQL) is the next entry: the 23,667
  translations of the 1,127 frozen shipped queries across 21 target dialects, the 2,600
  golden-corpus runtime outputs and `make sql-execution-gate` are unchanged from 30.24.0.9, so
  no published result changes.
- SQLite TPC-DS translation marks the TPC-DS `DATE` columns through the runtime's
  `mark_date_columns` rewrite on the parsed tree (`translate_sql_query(date_columns=..)`),
  so Q72's `d3.d_date > d1.d_date + 5` adds five whole days to the date text instead of
  adding 5 to the leading number of the text. Q72 is the only shipped TPC-DS query whose
  SQLite text changes; TPC-DS Q72 results published for SQLite were computed with the
  unmarked predicate.
- The DDL golden corpus passes the runtime's new DDL options (`table_case`,
  `strip_with_properties`, QuestDB layout fields, SingleStore `DATETIME`, StarRocks
  `float_as_real`) and records a classification and reason (`equivalent`,
  `legacy-bug` or `fork-gap`) for every remaining difference; each classification
  rule verifies the shape of the difference it labels, and a test fails on an
  unclassified difference or a `fork-gap`. Array columns become `VECTOR(n, F32|F64)`
  on SingleStore and `VECTOR(n)` on a SQL Server or Azure SQL target with the
  `vector_type` option; on every other T-SQL target (Synapse, Fabric) the runtime
  raises a `ValueError` instead of writing an array type those engines reject.
- The BigQuery, Snowflake and Databricks adapters no longer carry their own AST
  division safeguards, the Databricks output-alias deduplication, the unqualified-table
  scan, the `PRIMARY KEY ... NULLS` syntax fix, the TPC-DI SQLite-idiom regexes
  (`JULIANDAY`, `DATE('now')`, flag comparisons) or the ClickBench Snowflake
  regex-group rewrite; the bundled SQLGlot runtime owns each of them. The Databricks
  adapter now applies alias deduplication (top-level `SELECT` only) and `safe_division`
  in one parse and returns the query text untouched when neither changes it.
  `translate_sql_query` accepts `rewrites=` to apply named runtime rewrites to the source
  tree before generation; `apply_rewrites` applies several to one query with a single
  parse; `unqualified_table_names` lists the tables a BigQuery statement leaves
  unqualified. TPC-DI now renders `IsCurrent`, `TT_IS_SELL` and `HolidayFlag`
  comparisons as booleans for BigQuery (`= TRUE`) and Databricks (`IS TRUE`) at
  translation time, and the runtime's generators emit the date arithmetic: PostgreSQL
  `JULIANDAY` differences are `EXTRACT(epoch ...) / 86400` cast to `BIGINT`, DuckDB uses
  `DATE_DIFF('DAY', ...)`, relative dates use interval arithmetic. Zero divisors still
  return NULL. The golden corpus records the remaining text differences.

### Fixed

- The Velox Docker image builds again. BenchBox now runs in a uv-managed Python 3.12 virtual environment with `pyspark` pinned to the Spark version in the base image, instead of the base image's Python 3.10.
- Trino and Presto `create_schema` on the Hive connector no longer writes `NOT NULL`, which Trino 476 rejects with
  `Catalog 'hive' does not support non-null column`; TPC-H (8 tables) and TPC-DS (25 tables) now create on a Trino
  Hive catalog. No published result can use this path, because schema creation failed on it.

- The SQLite loader stores a NULL for an empty field in a nullable column instead of
  empty text. Before, `SUM` over an all-empty group returned `0.0` where DuckDB returns
  `NULL`, which made TPC-DS Q20 and Q98 differ between SQLite and DuckDB (405,815 cells
  at SF 0.1). The table's declared null marker decides: `.tbl` and `.dat` data (marker
  `""`) nulls every nullable column, text included, as the DuckDB loader does; a
  marker-less `.csv` nulls only nullable non-text columns and keeps empty text. Columns
  declared `NOT NULL` keep their empty string. SQLite results produced before this fix
  on data with empty fields (TPC-DS, AMPLab) should be rerun.
- `EnhancedSCDType2Processor.process_scd_changes` now writes its changes to the database. It
  expires the current version of a changed history-tracking dimension row (`IsCurrent` 0 and
  `EndDate` set to the new `EffectiveDate`, TPC-DI clause 4.4.1.3), inserts the new version
  and new natural keys, and overwrites the non-SCD attribute columns and `BatchID` in place on
  rows whose only changes are non-SCD. Before this it only counted them. Statements use bound
  parameters and run in one transaction; the returned counts are the rows the database reports
  as affected, and `scd_type2_changes` counts one per superseded key so that the new, SCD,
  updated and unchanged counts add up to `records_processed`. A failed read of the current dimension or a failed `BEGIN` now aborts the run
  before any write instead of treating the table as empty. Table and column names are checked
  against an allow-list before any SQL is built, a caller-owned transaction is protected with
  a SAVEPOINT (the run fails closed on connections without SAVEPOINT support, such as DuckDB),
  and non-SCD attributes that are NULL on both sides no longer count as changed. A run that fails
  and rolls back reports zero for the new, SCD and updated counts, and a failed `COMMIT` is
  rolled back so the transaction is never left open. The processor
  is not called by the TPC-DI ETL pipeline, so published TPC-DI results are unchanged.
- A pull request title or body edit no longer leaves an armed pull request blocked
  with every required check green. The CI run such an edit starts now reports each
  required check (`core`, `explorer`, `results-data`, `docs`, `tooling`) by copying
  the verdict of the newest full CI run on the same head commit, waiting while that
  run is in progress. Before, its required jobs were skipped under other names, and
  GitHub kept the pull request blocked until a push or a close and reopen started a
  full run.

- Tuned runs no longer abort, or lose the tuned-run marker, when the tuning
  metadata store does not work on the platform:
  - Athena has no metadata store. A tuned run proceeds without a marker and records
    `metadata_store: unsupported` in its applied-tuning ledger. Because BenchBox
    cannot tell later whether such a database was tuned, it now refuses to reuse an
    existing Athena database and recreates it.
  - Synapse and Fabric Warehouse get valid T-SQL: `IF OBJECT_ID(...) IS NULL CREATE
    TABLE` with `DATETIME2` (the old `TIMESTAMP` column is a `ROWVERSION`), `TOP 1`
    instead of `LIMIT 1`, and `DELETE` without `WHERE TRUE`. DDL runs through the
    cursor, because the adapters' query path fetches a result set that DDL does not
    return. Fabric Warehouse gets no lookup index, which it does not support.
  - BigQuery creates the store with `CREATE TABLE IF NOT EXISTS`; the adapter used
    to rewrite the plain `CREATE TABLE` to `CREATE OR REPLACE`, so each new manager
    emptied the table and its marker.
- TPC-H and TPC-DS data generation no longer hang when the filesystem cannot
  flush. Both ran the global `sync` command with no timeout; a process stuck in
  uninterruptible I/O wait cannot be signalled, so generation waited as long as
  the stall lasted. The flush now has a 30 second bound, is abandoned when it
  does not finish, and logs a warning that timing measurements may include
  unflushed writes.
- TPC-DI `EQ3` counted the rows of a nested `COUNT(*)` for `source_records`, which is
  always 1. It now counts the `DimCustomer` rows of the batch, so `source_records`
  changes on every platform and `transformation_success_rate` is unchanged; previously
  only ClickHouse, StarRocks and Doris rewrote it at translation time.
- Post-load clustering on PostgreSQL-family adapters no longer fails with
  `CLUSTER cannot run inside a transaction block`. Plain `postgresql` runs with a
  clustering or sorting tuning previously recorded the `CREATE INDEX` and `CLUSTER`
  as failed. The index now commits first and `CLUSTER` runs in autocommit; both
  partitioning (`pg_get_partkeydef`) and clustering (`pg_index.indisclustered`, per
  partition) are verified against the catalog. A verification failure after commit
  records the durable statements as executed and the intent as not satisfied.
- TPC-DI `EQ8` and `EQ6` now use decimal literals where the arithmetic is meant to be
  exact. `EQ8` computes the error resolution rates as `45.0 / 50 * 100`,
  `13.0 / 15 * 100`, `2.0 / 3 * 100` and `20.0 / 25 * 100`; `EQ6` computes the memory
  headroom term as `(1000 - N) / 10.0`. Engines that apply integer division to integer
  literals (PostgreSQL semantics, SQLite) previously returned a resolution rate of `0`
  for `45 / 50 * 100` instead of `90`, and truncated the `EQ6` memory headroom term
  (`93` rather than `93.6`; the `EQ6` performance rating is capped at 100, so its
  output is unchanged). The recorded `EQ6` and `EQ8` results in published TPC-DI
  bundles hold timings and row counts only, so no recorded field changes.
- A validation mismatch or `--force-recreate` on Snowflake no longer targets the
  whole database. Benchmarks that declare a data-source benchmark (read, write and
  transaction primitives, TPC-DS OBT) run inside that benchmark's database, so
  dropping it would delete every other benchmark's tables there. Snowflake now
  resets in place: it drops the tables the requesting benchmark declares in its
  schema (for the primitives that includes the TPC-H tables they query, but not
  other benchmarks' tables), drops the tuning metadata table so a later run cannot
  trust stale metadata, keeps the database, and fails without removing anything
  when it cannot tell which tables those are. External tables are dropped with
  `DROP EXTERNAL TABLE`. `drop_database` and the `USE DATABASE`/`USE SCHEMA`
  fallbacks now render the identifier unquoted, as `CREATE DATABASE` does; the
  quoted lowercase form never matched the uppercase database, so the drop was a
  silent no-op that the log reported as "Dropped database".
- On server-side platforms that drop the database on a validation mismatch, BenchBox
  now refuses when validation found tables that do not belong to the requesting
  benchmark ("Extra tables found"), and names them, instead of deleting them.
  `--force-recreate` still drops the whole database. File-based databases (DuckDB,
  SQLite, DataFusion, Polars) are still deleted and rebuilt on a mismatch.

### Fixed

- The bundled SQLGlot licence notice keeps LF line endings in Windows checkouts and
  the nested-archive import probe accepts Windows path separators, so the SQLGlot
  runtime tests pass on Windows. The BigQuery Iceberg test helper disposes its
  catalog engine.
- A `DatabaseConfig`, such as the one `PlatformHookRegistry.build_database_config`
  returns, no longer shows secrets when it is printed, formatted or logged. Its
  `repr`, `str` and log output redact secret-named options and fields
  (`password`, `token`, `secret`, `api_key`, `connection_string` and similar) and
  the credentials embedded in URIs, while the connection code still reads the real
  values. Before, a Snowflake config built from saved credentials printed the
  password in full. A test-session probe seeds a sentinel credential in the
  caller's home and checks that an isolated pytest run never reads it and writes
  nothing to that home; a live-only run still reads it.

### Security

- BenchBox now requires `certifi` 2026.10.19 or newer. Older releases of the CA
  bundle that `requests` uses still trust the compromised eMudhra emSign
  certificate authorities, so an attacker on the network path could impersonate
  a cloud endpoint and capture the tokens that `requests`-based adapters send,
  such as the Fabric and Synapse Spark adapters. Upgrading BenchBox now upgrades
  `certifi` as well. See the
  [Python advisory](https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576).
- Releases publish with `pypa/gh-action-pypi-publish` 1.14.3, which removes the
  same authorities from the CA bundle that Sigstore uses for Trusted Publishing
  and attestations. Release jobs install pip 26.2.2 and their helper packages
  from hash-pinned wheels, so an older pip on the runner cannot be served a
  substituted package.
- The `benchbox-agent`, Velox and LakeSail images install Python packages with
  uv 0.13.5 instead of pip. Rebuild existing images to pick up the change.
- The `bigquery`, `cloud`, `cloud-spark`, `cloud-spark-gcp`,
  `cloud-spark-databricks`, `lakesail`, `velox` and `all` extras now require
  `grpcio` 1.84.1 or newer. Older `grpcio` releases verify TLS against their own
  bundled root list, which still trusts the roots the compromised eMudhra emSign
  intermediates chain to, so BigQuery Storage, Dataproc, Databricks Connect and
  Spark Connect connections stayed exposed even after the `certifi` upgrade.

## [0.5.0rc1] - 2026-10-20

### Before you upgrade

- **BREAKING: Tuning validation fails on tuning the platform does not apply.**
  Earlier releases accepted these files and ran without the tuning. Run
  `benchbox tuning validate --platform <platform>` on your saved tuning files
  first; each error names the tuning type and why it is not applied. Unique
  and check constraints fail everywhere, because no benchmark schema carries
  them, and files that leave them out no longer request them. Layout tuning
  fails on BigQuery, Redshift, Trino, Azure Synapse and other platforms that
  only preview it. Redshift sorting needs `sorted_ingestion_mode` set to `auto`
  or `force`. Databricks and Fabric Warehouse reject partitioning. Primary and
  foreign keys fail where the adapter removes them, including Athena.
- **BREAKING: Some option spellings are deprecated and stop working in 0.6.0.**
  Each logs one warning per run. Use `--platform name:mode` instead of
  `deployment_mode`, `mode`, `sail_mode` and similar options. Use
  `compute_resource` and `compute_size` instead of vendor names such as
  `warehouse`, `engine_name`, `workgroup` and `cluster_id`. Use
  `--execution-engine streaming` instead of `--polars-streaming`. Saved
  configurations and credential profiles keep working until then.
- **BREAKING: A selector and an option that disagree now fail.** For example,
  `--platform firebolt:core --platform-option deployment_mode=cloud` stops
  before any work. A compute value that disagrees with the same value spelled
  another way stops the run too. Pass one spelling.
- **Throughput and Power results are not comparable with 0.4.2.** Throughput
  streams are numbered 1 to S instead of 0 to S-1, and TPC-H power iterations
  all use stream 00. Throughput now honors `--queries` and `validation_mode`,
  and a run limited to a query subset reports no Throughput@Size. A saved
  `stream_timeout_seconds` now applies to throughput runs; remove it from your
  config file to keep each benchmark's default.
- **ClickHouse tuned results are not comparable with earlier ones.** Tuned
  runs are about 2x faster on TPC-H joins, the TPC-H template no longer
  partitions `LINEITEM` and `ORDERS`, and tuned loads wait for background
  merges. Tuning maintenance on ClickHouse, Redshift, Databricks and Snowflake
  runs after each table loads; ClickHouse `OPTIMIZE` needs
  `--platform-option optimize_after_load=true`.
- **Spark, LakeSail and Velox results are not comparable with earlier ones.**
  Untuned runs use engine defaults for adaptive execution and shuffle, and
  per-query times no longer include clearing the session cache. A tuning
  file's `spark` map applies on Velox and is ignored on LakeSail.
- **Cached databases are rebuilt once in some cases.** DuckDB `auto` runs no
  longer request CHECK constraints, and nested tuning settings change the
  generated database name. Drop the old databases.
- **TPC-H maintenance only runs on adapters that support refresh.** DuckDB,
  SQLite, PostgreSQL and its derivatives, Snowflake and SingleStore apply the
  update sets; other adapters skip the phase. Refresh functions in Power and
  Throughput need `tpch_refresh_functions=true`.
- **Several options that were ignored now take effect.** DuckDB `threads` and
  `temp_directory`, SQLite `database_path`, `database_name` and `timeout`, and
  a `--platform-option` value equal to its default now apply, overriding a
  saved credential profile. Remove an option you do not want applied.
- **Dask and Polars engine changes.** Dask `use_distributed=false` with
  `scheduler_address` fails; use `--execution-engine distributed`. Polars
  `--tuning auto` no longer switches to streaming on small hosts, and
  `write_primitives` on `polars-df` refuses a non-default engine.
- **Comparisons refuse mixed variants.** `benchbox compare` and MCP
  `compare_results` refuse results whose deployment, execution engine or
  gateway differ unless you pass `--allow-heterogeneous-comparison`.
- **Result bundles use additive schema 2.3.** Readers must accept it. Older
  bundles remain readable.
- **BenchBox bundles its own SQLGlot, and some translated SQL changed.**
  Installing BenchBox no longer installs public `sqlglot`, and expression
  objects from a separate `sqlglot` install cannot be passed to BenchBox. Rerun
  earlier results for these queries: `STDDEV` and `VARIANCE` on MySQL, Doris,
  StarRocks and SingleStore now use the sample form (TPC-DI AQ5, AQ6 and AQ9,
  read_primitives `statistical_variance_stddev`); `array_sort` and
  `array_distinct` on DuckDB; `approx_quantile_groupby` on BigQuery; and
  `pivot_basic` on StarRocks, which used to lose its PIVOT clause. ClickBench
  on SQLite (Q19, Q43) and on DataFusion, Netezza, ANSI, standard, Vertica and
  Greenplum targets now uses different SQL, so earlier results there are not
  comparable.
- **SingleStore has no `cloud` mode.** Use `--platform singlestore` for Helios
  and self-managed clusters. `databend:self-hosted` now connects over HTTP on
  port 8000 by default.

### Added

- **Execution engines.** `--execution-engine` and the MCP `execution_engine`
  parameter choose how a query runs, checked against what the platform
  declares. Polars supports `default`, `in-memory` and `streaming`, Dask
  `threaded` and `distributed`, and pg-duckdb `duckdb` and `postgres`. Results
  record the requested, applied and observed engine.
- **Deployment selectors everywhere.** `--platform firebolt:cloud`,
  `pg-duckdb:motherduck`, `influxdb:core`, `ducklake:postgres_catalog`,
  `velox:remote`, `lakesail:distributed` and `timescaledb:cloud` work end to
  end, in MCP, and in Python as `DatabaseConfig(type="timescaledb:cloud")`.
  Results record the selected deployment, and variants rank separately.
- **Compute resource and size.** Platforms share `compute_resource` and
  `compute_size` options in place of vendor names. Results record them, and
  public bundles drop the resource name.
- **Subscale results are ranked.** TPC-H and TPC-DS runs below scale factor 1
  record Power@Size marked unofficial and rank in their own "Subscale ·
  Unofficial" cohort. `benchbox submit` and `benchbox publish` still refuse
  them.
- **TPC-H row counts at scale factors 0.01 and 0.1.** Every query is checked
  against exact counts, so DataFrame runs no longer report UNCERTAIN there.
- **TPC-H refresh functions and QphH@Size.** Power can include RF1 and RF2,
  Throughput can run a concurrent refresh stream, and `benchbox metrics qphh`
  prints QphH@Size when both results carry refresh evidence.
- **More tuning on PostgreSQL.** Tuned runs apply date and hash partitioning,
  and index and cluster tuned columns after each load.
- **Clearer tuning evidence.** Tuned results list requests the platform ignored
  in `platform.tuning.applied.dropped`. DataFrame tuned profiles and curated
  ClickHouse templates ship, `benchbox tuning platforms` lists Spark with its
  own capabilities, and `benchbox tuning validate` accepts SQL tuning files.
- **Session and engine details in results.** Spark, LakeSail and Velox runs
  record the live session configuration and warn when a setting did not take
  effect. DuckDB, SQLite and DataFusion results record the engine version.

### Changed

- **`--tuning tuned` fallback messages** name what each platform applies, such
  as "OLAP session pack" for ClickHouse.
- **Publishing fails closed.** `benchbox publish` verifies the anonymized files
  before copying them, and submission validation rejects unreadable plan,
  tuning and applied-tuning companion files.
- **Legacy throughput APIs are deprecated.** `run_throughput_test` and
  `run_official_benchmark` warn and run the same drivers as `benchbox run`.
  They need `adapter=` and at least 2 streams. `TPCDSOfficialBenchmark` now
  uses the scale factor you configure.

### Fixed

- **Tuning.** Tuned PostgreSQL runs no longer stop on the tuned-run marker.
  Tuned SQLite TPC-DS runs load without foreign keys. A tuning file that sets
  only some constraint types no longer requests the rest. Primary-key tuning
  on ClickHouse and chDB reaches `applied_verified`. Spark's `tuned` session
  settings now apply once and survive the defaults.
- **Dry runs.** `--dry-run --tuning tuned` shows the tuning DDL without the
  platform driver and notes tuning the platform skips. TSBS DevOps previews its
  queries, and saved credentials no longer appear in preview files.
- **Data and runs.** `--force datagen` and `--force all` regenerate data and
  recreate the database. Load-only runs exit 0 after a successful load. Sorted
  ingestion no longer runs unsafe rewrites on Athena, Azure Synapse and
  Redshift. Generated database names stay stable when nested tuning keys
  are reordered.
- **Platform options.** Alias platform names such as `sqlite3` and `pg_duckdb`
  accept their options. Options now reach EMR Serverless, Dataproc, Synapse
  Spark, Fabric, PySpark and Snowpark Connect, and Redshift accepts
  `workgroup_name` and `cluster_identifier`. A saved credential profile no
  longer blocks command-line compute settings.
- **Platforms.** Athena Spark starts a session, and `athena-spark` accepts its
  documented options. `benchbox run --platform quanton` builds its
  configuration. InfluxDB accepts `mode` and `deployment_mode`. Databricks
  `compute_resource` sets the warehouse HTTP path.
- **Results and reports.** `benchbox report rankings` lists imported runs.
  `benchbox compare` and `benchbox report` no longer mix deployments.
  Anonymized bundles drop the compute resource name. The MCP server no longer
  reconfigures Python's root logger. PostgreSQL-family baseline runs no longer
  issue planner session settings, which belong to tuned runs only, so baseline
  results pass the public privacy validation. ClickBench translation raises an
  error instead of returning untranslated ClickHouse SQL.

### Documentation

- **Tuning platform support.** A new reference page lists, for every platform,
  the shipped templates and the tuning types applied at execution.
- **Corrected `--platform-option` examples** use keys the CLI accepts.

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
[0.5.0rc1]: https://github.com/BenchBox-dev/BenchBox/compare/v0.4.2...v0.5.0rc1
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
