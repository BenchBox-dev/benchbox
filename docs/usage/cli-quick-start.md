<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# BenchBox CLI quick reference

```{tags} beginner, cli, reference
```

BenchBox ships with a full-featured CLI implemented with Click. This guide focuses on the commands and flags you will touch most often.

## Essential Commands

| Command | What it does |
| --- | --- |
| `benchbox profile` | Inspect CPU, memory, Python version, and platform availability. |
| `benchbox benchmarks list` | Show all shipped benchmarks and their categories. |
| `benchbox run` | Launch the interactive workflow or execute a scripted run. |
| `benchbox shell` | Open an interactive SQL shell to explore benchmark databases. |
| `benchbox platforms` | Manage database platforms: list, enable, disable, check dependencies. |
| `benchbox check-deps` | Summarize connector status and show install commands. |
| `benchbox tuning init` | Scaffold a tuning YAML grouped by platform and benchmark. |
| `benchbox results --limit N` | Display recent benchmark summaries. |

> Tip: `uv run -- benchbox <command>` automatically uses the project’s virtual environment. Activate the venv manually if you prefer to invoke `benchbox` directly.

## Running Benchmarks

The `run` subcommand accepts a rich set of options. The defaults favor the interactive experience; provide flags to drive unattended CI jobs.

### Core options

- `--platform TEXT` - Target platform (`duckdb`, `sqlite`, `clickhouse`, `databricks`, `bigquery`, `redshift`, `snowflake`, …).
- `--benchmark TEXT` - Benchmark module (`tpch`, `tpcds`, `ssb`, `primitives`, etc.).
- `--scale FLOAT` - Data scale factor (default: `0.01`).
- `--phases TEXT` - Comma-separated phases to run: `generate`, `load`, `warmup`, `power`, `throughput`, `maintenance` (default: `power`).
- `--non-interactive` - Assume defaults for every prompt (pair with explicit flags in CI).
- `--output PATH` - Root directory for generated data and artifacts (supports `s3://`, `gs://`, `abfss://`).
- `--dry-run PATH` - Produce the full execution plan without running it (queries, seeds, expected data volume).
- `--seed INTEGER` - Force a deterministic RNG seed for TPC benchmark parameter generation.
- `--force [MODE]` - Force regeneration (modes: `all`, `datagen`, `upload`, or `datagen,upload`).

### Validation and tuning

- `--validation MODE` - Validation mode: `exact`, `loose`, `range`, `disabled`, `full` (enables all checks).
- `--strict-translation` - Fail closed on SQL dialect translation fallback; use for CI and public correctness runs.
- `--tuning tuned|notuning|PATH` - Apply optional tuning configs (`tuned` enables platform-specific constraints; supply a YAML path to use a custom profile).
- `--platform-option KEY=VALUE` - Pass adapter-specific options (repeatable). Use `benchbox platforms status <name>` to see platform details.
- `--platform-option driver_version=X.Y.Z` - Pin a platform driver package (DuckDB, Databricks connector, etc.). Add `driver_auto_install=true` or export `BENCHBOX_DRIVER_AUTO_INSTALL=1` to let BenchBox install the requested build before execution.

### Logging & resource control

- `--verbose / -v` (repeatable) - Increase logging detail (`-v` = INFO, `-vv` = DEBUG).
- `--quiet / -q` - Suppress console output (overrides `-v`).
- `--capture-plans` - Capture query execution plans during benchmark runs.
- `--compression TYPE[:LEVEL]` - Compression settings (e.g., `zstd`, `zstd:9`, `gzip:6`, `none`).

### Help system

- `--help` - Show common options.
- `--help-topic all` - Show all options including advanced.
- `--help-topic examples` - Show categorized usage examples.

### Common execution patterns

The commands below run, in order:

1. An interactive workflow with guided prompts.
2. A non-interactive run for CI or cron jobs.
3. A preview of the entire plan (queries, file layout, seeds) without running it.
4. Specific queries only, for debugging or focused testing.
5. A single failing query with verbose output, for debugging.
6. A listing of all CLI examples.
7. A view of platform status and capabilities.
8. Two runs that compare two DuckDB releases without switching environments, one for `driver_version=1.0.0` and
   one for `1.1.0`.

```bash
uv run -- benchbox run

uv run -- benchbox run \
  --platform duckdb \
  --benchmark tpch \
  --scale 0.1 \
  --non-interactive

uv run -- benchbox run \
  --platform duckdb \
  --benchmark tpch \
  --scale 0.1 \
  --dry-run ./preview

uv run -- benchbox run \
  --platform duckdb \
  --benchmark tpch \
  --queries "Q1,Q6,Q17" \
  --phases power

uv run -- benchbox run \
  --platform postgres \
  --benchmark tpcds \
  --queries "Q42" \
  --verbose \
  --phases power

uv run -- benchbox run --help-topic examples

uv run -- benchbox platforms status databricks

uv run -- benchbox run \
  --platform duckdb \
  --benchmark tpch \
  --platform-option driver_version=1.0.0 \
  --output ./runs/v1.0.0

uv run -- benchbox run \
  --platform duckdb \
  --benchmark tpch \
  --platform-option driver_version=1.1.0 \
  --output ./runs/v1.1.0
```

## Results and Artefacts

This shows the latest run summary: duration, validation status and failures.

```bash
uv run -- benchbox results --limit 1
```

BenchBox stores generated data and results under `benchmark_runs/` by default.
Use `--output PATH` to choose another local or supported remote root. For a
remote root, BenchBox adds the benchmark and scale suffix just as it does for a
local directory:

```bash
uv run -- benchbox run \
  --platform databricks \
  --benchmark tpch \
  --scale 0.01 \
  --output dbfs:/Volumes/workspace/raw/source/
```

The data root for this run is `dbfs:/Volumes/workspace/raw/source/tpch_sf01`.

Supported remote schemes depend on the installed platform and storage extras.
Preview a run with `--dry-run` before it writes data.

## Exporting Results

Re-export existing benchmark results in different formats without re-running. The commands export, in order: the most
recent result to CSV, a specific result to an HTML report, one result to several formats at once, the latest TPC-H
result with filtering, and the latest result to a custom directory.

```bash
uv run -- benchbox export --last --format csv

uv run -- benchbox export benchmark_runs/results/tpch_sf1_duckdb.json --format html

uv run -- benchbox export --last --format csv --format html --format json

uv run -- benchbox export --last --benchmark tpc_h --format csv

uv run -- benchbox export --last --format html --output-dir ./reports/
```

**Common Use Cases:**
- **CSV** for spreadsheet analysis (Excel, Google Sheets)
- **HTML** for standalone reports with formatted tables
- **JSON** for programmatic analysis and archival

## Visualizing Results

Generate ASCII charts from benchmark results directly in the terminal. The commands, in order, auto-detect the latest
result and render all applicable charts, visualize a specific result file, compare multiple result files, render a
specific chart type, and save plain-text output to a file with ANSI colors stripped (`--no-color`).

```bash
uv run -- benchbox visualize

uv run -- benchbox visualize benchmark_runs/results/tpch_duckdb_sf0.01_*.json

uv run -- benchbox visualize duckdb.json sqlite.json --template head_to_head

uv run -- benchbox visualize benchmark_runs/results/latest.json --chart-type performance_bar

uv run -- benchbox visualize benchmark_runs/results/latest.json --no-color > charts.txt
```

See the [Visualization Guide](../visualization/overview.md) for chart types, templates, and customization options.

## Platform Management

The `platforms` command helps you discover, enable, and configure database platforms. The commands, in order, list
all available platforms with their status, show detailed information about one platform, enable a platform for use in
benchmarks, disable a platform, give installation guidance for missing dependencies, check whether enabled platforms
are ready, and start an interactive setup wizard.

```bash
uv run -- benchbox platforms list

uv run -- benchbox platforms status duckdb

uv run -- benchbox platforms enable clickhouse

uv run -- benchbox platforms disable sqlite

uv run -- benchbox platforms install databricks

uv run -- benchbox platforms check --enabled-only

uv run -- benchbox platforms setup
```

**Platform Management vs Credential Setup:**
- `benchbox platforms` manages platform *availability* (dependencies installed, enabled/disabled)
- `benchbox setup --platform <name>` manages *credentials* for cloud platforms
- For cloud platforms, you need both: enable the platform AND configure credentials

**Typical Cloud Platform Workflow:** check that the platform dependencies are installed, install them if needed
(follow the guidance shown), enable the platform, configure credentials, then verify that everything is ready.
```bash
uv run -- benchbox platforms status databricks

uv add databricks-sql-connector

uv run -- benchbox platforms enable databricks

uv run -- benchbox setup --platform databricks

uv run -- benchbox platforms check databricks
```

### Platform-specific options

Adapters expose special settings through repeatable
`--platform-option KEY=VALUE` arguments. Inspect the platform guide before
using them; credentials belong in the environment or the setup workflow, not
in committed command files.

```bash
uv run -- benchbox run \
  --platform clickhouse-server \
  --benchmark tpch \
  --platform-option secure=true \
  --platform-option port=9440
```

The platform name picks the ClickHouse deployment: `clickhouse-server` (or
`clickhouse:server`) for a server, `clickhouse-local` (or `clickhouse:local`)
for embedded chDB.

See [platform-specific configuration](../reference/cli/configuration.md#platform-specific-options)
for the supported keys.

## Interactive SQL Shell

Open an interactive SQL shell to explore benchmark databases, debug queries, and inspect data. The commands, in order,
discover and connect to available databases interactively, list all available databases, connect to the most recent
database, connect to a specific benchmark database, filter by scale factor, connect directly to a database file, and
use a custom output directory.

```bash
uv run -- benchbox shell

uv run -- benchbox shell --list

uv run -- benchbox shell --last

uv run -- benchbox shell --last --benchmark tpch

uv run -- benchbox shell --benchmark tpch --scale 1.0

uv run -- benchbox shell --database benchmark.duckdb

uv run -- benchbox shell --output ./my-benchmarks
```

**Shell Features:**
- **DuckDB & SQLite**: Full interactive shells with `.tables`, `.schema`, `.info` commands
- **Command History**: Navigate previous commands with arrow keys
- **Query Timing**: Automatic execution time measurement
- **Database Discovery**: Automatically finds databases in benchmark_runs/

**Common Use Cases:**
- Verify data loaded correctly after benchmarks
- Debug individual queries before running full suite
- Explore table schemas and row counts
- Compare data across different scale factors

## Dependency Checks & Tuning Templates

The commands summarize optional dependencies and extras guidance, focus on a single adapter with verbose remediation,
and generate a tuning skeleton for your project.

```bash
uv run -- benchbox check-deps --matrix

uv run -- benchbox check-deps --platform snowflake --verbose

uv run -- benchbox tuning init --platform duckdb
```

## Working With uv

- `uv run -- benchbox …` keeps dependency management simple: no manual activation required.
- Use `uvx benchbox …` to execute without installing into the current project.
- If you are using a traditional virtual environment, run `source .venv/bin/activate` (or the Windows equivalent) and call `benchbox` directly.

For a full explanation of workflows, visit the [usage overview](index.md). When you need to customise execution further, the [configuration guide](configuration.md) explains YAML profiles, environment overrides, and validation rules in detail.
