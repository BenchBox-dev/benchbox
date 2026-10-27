# BenchBox CLI Reference

This document is a condensed overview of the `benchbox` CLI.
For exhaustive flags and options, use the detailed [CLI reference](reference/cli/index.md)
and live `benchbox <command> --help`. It provides a stable, searchable
overview of core commands, common flags, and practical invocation patterns.

BenchBox supports SQL and DataFrame benchmark execution across local and
cloud platforms. Most commands can be run in interactive mode or fully
non-interactive mode with explicit flags.

## Command Reference

### Global Patterns

- Base command: `benchbox <command> [options]`
- Non-interactive runs: use `--non-interactive` plus explicit benchmark inputs.
- Help topics: many commands support `--help-topic examples` and `--help-topic all`.
- Verbosity: use `-v` or `-vv` for diagnostics, and `--quiet` for minimal output.

### `benchbox run`

Runs a benchmark lifecycle (generate, load, execute phases) with optional
phase control, compression settings, and platform-specific options.

Common flags:

- `--platform <name>`
- `--benchmark <name>`
- `--scale <float>`
- `--phases <csv>`
- `--non-interactive`
- `--dry-run <output-dir>`
- `--compression <none|gzip|zstd|bzip2|xz>`
- `--seed <int>`

Examples:

- `benchbox run --platform duckdb --benchmark tpch --scale 0.01 --phases power --non-interactive`
- `benchbox run --platform polars-df --benchmark tpch --scale 0.01 --non-interactive`
- `benchbox run --platform duckdb --benchmark tpch --scale 0.1 --dry-run ./preview --phases generate,load`

### `benchbox profile`

Collects system profile and benchmark-oriented diagnostics to support
capacity planning and performance troubleshooting.

### `benchbox validate`

Validates benchmark configuration and execution readiness, including
syntax checks and platform dependency checks where applicable.

### `benchbox benchmarks list`

Lists available benchmark suites with concise descriptions and
expected runtime characteristics.

### `benchbox results`

Displays benchmark execution history and summary metrics from stored
artifacts under `benchmark_runs/`. Use `benchbox results --submitted`
to list hosted submission history and public result URLs.

### `benchbox auth`

Manages hosted submission credentials for `benchbox submit --service`.
Tokens are stored in the OS keyring and can be overridden for automation
with `BENCHBOX_SUBMIT_TOKEN` or the lower-precedence
`BENCHBOX_SERVICE_TOKEN` fallback.

### `benchbox export`

Exports result files to structured output formats for downstream analysis
and reporting automation.

### `benchbox publish`

Publishes an already-exported schema-v2 result bundle to a named storage
destination and records a durable, addressable reference in a persistent
metadata store (`~/.benchbox/published.json`).

Unlike `benchbox export` (which serialises live benchmark results to disk),
`benchbox publish` operates on already-exported files and adds:
- Addressability: a truthful backend-specific reference URI
- Tracking: persistent publication history that survives process restart
- Deduplication: publishing the same bundle twice updates the record

**Supported backends and reference types:**

| Destination | Reference format |
|-------------|-----------------|
| Local directory | `file:///abs/path/to/bundle.json` |
| `s3://bucket/prefix` | `s3://bucket/prefix/bundle.json` |
| `gs://bucket/prefix` | `gs://bucket/prefix/bundle.json` |
| `abfss://...` | `abfss://.../bundle.json` |

**No fake short-codes are generated.** For local and private cloud targets,
BenchBox emits a storage path reference. Public short-link permalinks are
only possible when a resolver service exists and is explicitly configured.

**Persistence guarantees:**
- Local JSON store (`~/.benchbox/published.json`) survives process restart
- Cloud URI references are durable if the underlying cloud storage is
- Records can be removed with `benchbox publish remove <id>` (files are NOT deleted)

Common flags:

- `--target <path-or-uri>` - destination directory or cloud URI prefix
- `--label <maintainer-run|community-submission|ci|local>` - provenance label
- `--dry-run` - preview without publishing
- `--last` - publish the last exported result

Examples:

```
# Publish a specific result to a local directory
benchbox publish run results/tpch_sf1_duckdb.json

# Publish to a custom local directory
benchbox publish run results/tpch_sf1_duckdb.json --target /mnt/shared/benchbox

# Publish to S3
benchbox publish run results/tpch_sf1_duckdb.json --target s3://my-bucket/benchbox

# Publish the most recent result
benchbox publish run --last --benchmark tpch

# Publish after a benchmark run (uses benchbox publish defaults)
benchbox run --platform duckdb --benchmark tpch --publish
benchbox run --platform duckdb --benchmark tpch --publish --publish-target s3://my-bucket/benchbox

# List publication history
benchbox publish list

# Show a specific publication
benchbox publish show abc123def456

# Remove a publication record (does not delete artifact files)
benchbox publish remove abc123def456
```

### `benchbox submit`

Packages or uploads schema-v2 result bundles for the BenchBox results
platform.

Common flags:

- `--last` - use the most recent result file
- `--output <dir>` - create a PR-ready local submission package
- `--service [url]` - upload to the hosted ingest API
- `--visibility <public|unlisted|private>` - hosted visibility
- `--idempotency-key <key>` - override the stable hosted retry key
- `--wait / --no-wait` - poll or return after hosted acceptance
- `--dry-run` - preview without writing files or sending bytes
- `--submitted-by <name>` - override PR manifest submitter name

Examples:

```bash
uv run -- benchbox submit --last --output ./submission

uv run -- benchbox submit --last --service --dry-run

uv run -- benchbox auth login
uv run -- benchbox submit --last --service

uv run -- benchbox results --submitted
```

In order, these commands: package the latest result for PR contribution, preview a hosted upload without credentials or network, log in and upload to the hosted service, and track hosted submissions.

### `benchbox check-deps`

Checks optional platform dependencies and prints installation guidance.

### `benchbox visualize`

Generates ASCII charts from benchmark result JSON files. Auto-discovers
recent results if no files are specified. Supports chart type selection,
templates, themes, and pipe-friendly output modes.

### `benchbox report`

Historical result analysis with subcommands: `rankings` (platform
comparisons), `trends` (performance over time), `regressions` (detect
slowdowns), `import` (load results into database), `stats`, and `list`.

### `benchbox metrics qphh`

Calculates Power@Size and Throughput@Size from TPC-H power and throughput
results. It exports QphH@Size only when official-run metadata and complete
refresh evidence pass the shared gate; otherwise it reports
`qphh_unavailable` reasons. It does not compute QphDS@Size. Scale factor is
auto-detected from the result files.

### `benchbox aggregate`

Aggregates multiple result JSON files into a CSV with geometric mean,
p50/p95/p99 statistics per run. Useful for trend tracking.

### `benchbox datagen`

Standalone data generation (wrapper for `benchbox run --phases generate`).
Supports benchmark, scale, format, and seed options.

### `benchbox setup`

Interactive cloud credential configuration for Databricks, Snowflake,
BigQuery, Redshift, and Athena. Includes validation, status checks,
and Redshift connectivity diagnostics.

### `benchbox show-plan`

Displays a query execution plan from benchmark results as an ASCII tree,
summary, or JSON. Requires plans captured with `--capture-plans`.

### `benchbox plan-history`

Shows query plan evolution across runs with fingerprint tracking and
optional plan flapping detection.

### `benchbox download-answers`

Pre-downloads TPC-H and TPC-DS answer files to a local cache for
offline row-count validation.

### `benchbox compare`

Compares benchmark result files for regression detection. See [results](reference/cli/results.md#compare).

### `benchbox convert`

Converts data to Parquet, Vortex, Delta Lake, or Iceberg. See [convert](reference/cli/convert.md).

### `benchbox platforms`

Manages database platform adapters. See [platforms](reference/cli/platforms.md).

### `benchbox shell`

Launches an interactive SQL shell. See [shell](reference/cli/shell.md).

### `benchbox tuning`

Tuning configuration commands. See [tuning](reference/cli/tuning.md).

## Notes

- Cloud platforms may require explicit output/staging roots.
- TPC scale-factor rules apply (for example, TPC SF >= 1 must be integers).
- Some test and benchmark flows rely on optional extras that are not installed
  in minimal environments.
