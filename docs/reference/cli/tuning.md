# Tuning Commands

```{tags} reference, cli, tuning
```

Commands for creating and managing tuning configurations for SQL and DataFrame platforms.

(cli-tuning-resolution)=
## How `--tuning` is resolved

`benchbox run --tuning VALUE` accepts the keywords `notuning`, `tuned`, `auto`,
or an explicit path to a YAML file. If `--tuning` is omitted it **defaults to
`notuning`** (baseline, all optimizations disabled).

Resolution happens in this order (see `benchbox/cli/tuning_resolver.py:resolve_tuning`):

0. **Default**: no `--tuning` given -> `notuning`.
1. **Keyword check, before any file lookup**: if the value is exactly
   `notuning`, `auto`, or `tuned` (case-insensitive), it is resolved as that
   keyword immediately - a local file or directory that happens to be named
   `tuned` or `auto` can never shadow the keyword's meaning.
   - `notuning` -> baseline: all constraints disabled.
   - `auto` -> smart-defaults mode (see the DataFrame-only caveat below).
2. **`tuned` keyword resolution** (`_resolve_tuned`):
   1. If `benchbox.yaml`'s `tuning.default_config_file` is set (overridable
      with the `BENCHBOX_TUNING_CONFIG` environment variable) and that file
      exists, it is used - this beats template auto-discovery.
   2. Otherwise, auto-discovery searches, in order:
      1. `$BENCHBOX_TUNING_PATH/<platform>/<benchmark>_tuned.yaml`, if
         `BENCHBOX_TUNING_PATH` is set.
      2. `examples/tunings/<platform>/<benchmark>_tuned.yaml`, resolved
         relative to the **current working directory**.
      3. `<platform>/<benchmark>_tuned.yaml`, also cwd-relative.
      4. **Packaged resource** bundled with the installed `benchbox` package
         itself (`benchbox/core/tuning/packaged_templates.py`) - last resort,
         only reached when none of tiers 1-3 exist. Only a subset of
         platform/benchmark pairs ship a packaged template today (the
         `duckdb`, `databricks`, `snowflake`, and `clickhouse` templates that
         follow the `<benchmark>_tuned.yaml` naming convention); see
         `benchbox/core/tuning/templates/README.md` for the exact list and
         how it's kept in sync with `examples/tunings/`.
      The ClickHouse deployment variants (`clickhouse-local`,
      `clickhouse-server`, `clickhouse-cloud`, and `chdb`) share one template
      directory, `clickhouse`. In tiers 1-3, a directory named for the exact
      variant (for example `clickhouse-cloud/`) is searched before the shared
      `clickhouse/` directory, so a variant-specific template overrides the
      curated one.
      5. **DataFrame platforms only**: the curated profile
         `examples/tunings/dataframe/<platform>_optimized.yaml` (cwd-relative),
         searched after every per-benchmark tier above. `polars`, `pandas`
         and `cudf` ship one; `-df` platform spellings such as `polars-df`
         resolve to the same file. The resolution is recorded as mode
         `tuned` with this file as the tuning source. No packaged copy of
         these profiles exists.
   3. If none of those exist, tuning falls back to the platform's default
      configuration and prints a warning; in an interactive terminal the user
      is also offered the tuning wizard. A DataFrame platform with no
      `<platform>_optimized.yaml` (for example `dask`, which ships
      `dask_distributed.yaml`) ends here. This resolution is recorded as mode
      `tuned-fallback`, which never matches a curated `tuned` run and is
      refused under `--official`. The console line names what the fallback
      applies for the platform, taken from the tuning capability registry:
      `basic constraints` (the default, for example DuckDB), `engine runtime
      defaults (streaming)` (Polars), `engine runtime defaults` (other
      DataFrame platforms) or `OLAP session pack` (ClickHouse).
3. **Explicit file path**: if the value is not one of the three keywords, it
   is checked as a path; if it exists, that file is loaded directly.
4. **Invalid value**: if it's not a keyword and the path doesn't exist,
   `benchbox run` raises an error (with a hint to run `benchbox tuning list`).

```{note}
Steps 2.2, 2.3 and 2.5 are **cwd-relative**. `--tuning tuned` only auto-discovers
the templates bundled in `examples/tunings/` (or a cwd-relative
`<platform>/` directory) when `benchbox` is run from a checkout of this
repository (or another directory with its own equivalent template tree). If
BenchBox is installed as a package and run from elsewhere, auto-discovery
falls through to step 2.4 - the packaged-resource tier - which resolves for
the platform/benchmark pairs it covers; otherwise it falls through further to
the basic-config fallback unless `BENCHBOX_TUNING_PATH` is set to a directory
with the same `<platform>/<benchmark>_tuned.yaml` layout. A resolution from
the packaged tier is recorded with a distinct provenance source
(`TuningSource.PACKAGED_RESOURCE`), never conflated with a cwd/env
auto-discovered template.
```

```{warning}
`--tuning auto` prints a "smart defaults" message, but real system-profile
smart defaults (`benchbox/cli/tuning_runtime.py:resolve_dataframe_tuning_config`)
are implemented for **DataFrame platforms only** today. On SQL platforms,
`--tuning auto` proceeds with a basic, untuned configuration and
`benchbox run` prints an explicit warning saying so.
```

`BENCHBOX_TUNING_CONFIG` and `BENCHBOX_TUNING_PATH` are the only tuning-related
environment variables that affect resolution; see
[Configuration - Environment Variables](configuration.md#cli-env-vars).

(cli-tuning-init)=
## `tuning init` - Create Tuning Configuration

Generate sample unified tuning configurations for specific platforms.

### Options

- `--platform TEXT`: Target platform (required) - duckdb, databricks, snowflake, etc.
- `--mode [sql|dataframe|auto]`: Tuning mode (default: auto-detect from platform)
- `--profile [default|optimized|streaming|memory-constrained|gpu]`: Configuration profile (DataFrame mode only)
- `--smart-defaults`: Use auto-detected system-optimal settings (DataFrame mode only)
- `--output TEXT`: Output file path. Default depends on mode:
  - SQL mode: `<platform>_tuning.yaml` (e.g. `duckdb_tuning.yaml`)
  - DataFrame mode: `<platform>_<profile>_tuning.yaml` (e.g. `polars_optimized_tuning.yaml`)

### Usage Examples

```bash
benchbox tuning init --platform databricks

benchbox tuning init --platform snowflake \
  --output ./configs/snowflake-tuning.yaml
```

The first command creates a sample tuning configuration for Databricks and writes `databricks_tuning.yaml`. The second creates one for Snowflake at a custom output path.

(cli-tuning-dataframe)=
## DataFrame Tuning via `tuning` Commands

The unified `tuning` command group handles both SQL and DataFrame platforms.
Use `--mode dataframe` (or let it auto-detect from the platform name).

### Create DataFrame Tuning Configuration

```bash
benchbox tuning init --platform polars

benchbox tuning init --platform polars --smart-defaults

benchbox tuning init --platform pandas --mode dataframe --profile memory-constrained

benchbox tuning init --platform dask --output ./configs/dask_tuning.yaml
```

The first command creates a sample Polars tuning configuration and auto-detects DataFrame mode. The second creates smart defaults based on your system. The third sets DataFrame mode explicitly with a profile. The fourth writes to a custom output path.

**Options for DataFrame mode:**

- `--platform TEXT`: Target platform (`polars`, `pandas`, `dask`, `cudf`)
- `--mode [sql|dataframe|auto]`: Tuning mode (default: auto)
- `--profile [default|optimized|streaming|memory-constrained|gpu]`: Configuration profile. On a platform that lets you select an execution engine (Polars), the profiles do not set `engine_affinity` or `streaming_mode`, and the command prints how to choose the engine at run time, for example `--execution-engine streaming`
- `--output TEXT`: Output file path
- `--smart-defaults`: Use auto-detected system-optimal settings

### Validate Configuration

`--platform` is required. It selects which kind of file is validated:

- **DataFrame platform** (`datafusion`, `polars`, `pandas`, `dask`, `cudf`):
  the file is loaded as a DataFrame tuning file and checked against that
  platform. SQL-format YAML files are not rejected here: SQL-only sections
  such as `primary_keys` and `table_tunings` are silently ignored and only
  the DataFrame sections are validated, so a "valid" result does not mean
  the SQL content was checked. Validate the file your platform will
  actually load.
- **SQL platform** (any other platform in the tuning capability registry,
  including aliases such as `clickhouse-local`; run `benchbox tuning
  platforms` to list them): the file is loaded as a SQL tuning file and
  validated with `UnifiedTuningConfiguration.validate_for_platform_detailed`.
  Tuning types the platform does not support are errors on platforms with
  explicit compatibility data and warnings otherwise (schema constraints are
  always warnings). The capability registry adds a warning for each enabled
  tuning type the platform accepts but does not render yet. Errors exit
  non-zero. A DataFrame tuning file passed with a SQL platform is rejected.
  An unknown platform name exits with code 2 and lists the accepted names.

`--mode [sql|dataframe|auto]` (default: auto) selects the file kind for
platforms with both modes (`datafusion`); auto detects it from the platform.
A SQL DataFusion tuning file is validated with `--mode sql`. The same rule
holds at run time: SQL-mode runs never resolve a DataFrame profile
(`examples/tunings/dataframe/*_optimized.yaml`) during template discovery,
and a DataFrame tuning file passed to a SQL-mode run is refused with an
actionable error instead of loading as an empty configuration.

```bash
benchbox tuning validate polars_tuning.yaml --platform polars
benchbox tuning validate examples/tunings/duckdb/tpch_tuned.yaml --platform duckdb
benchbox tuning validate datafusion_tuned.yaml --platform datafusion --mode sql
```

### Show Smart Defaults

DataFrame platforms only. `tuning defaults` has no SQL form.

```bash
benchbox tuning defaults --platform polars
```

## Configuration Categories

DataFrame tuning supports these configuration sections:

| Category | Settings | Description |
|----------|----------|-------------|
| `parallelism` | `thread_count`, `worker_count`, `threads_per_worker` | CPU resource allocation |
| `memory` | `memory_limit`, `chunk_size`, `spill_to_disk`, `rechunk_after_filter` | Memory management |
| `execution` | `streaming_mode`, `lazy_evaluation`, `engine_affinity` | Execution behavior |
| `data_types` | `dtype_backend`, `enable_string_cache`, `auto_categorize_strings` | Type handling |
| `io` | `memory_pool`, `memory_map`, `pre_buffer`, `row_group_size` | I/O optimization |
| `gpu` | `enabled`, `device_id`, `spill_to_host`, `pool_type` | GPU settings (cuDF) |
| `write` | `sort_by`, `partition_by`, `row_group_size`, `target_file_size_mb`, `repartition_count`, `compression`, `compression_level`, `dictionary_columns`, `skip_dictionary_columns`, `data_page_version` | Write-time physical layout and Parquet encoding |

For complete configuration reference, see [DataFrame Platforms - Tuning](../../platforms/dataframe.md#dataframe-tuning).

(cli-tuning-list)=
## `tuning list` - List Available Templates

List the tuning templates found under `examples/tunings/` (cwd-relative,
same as `--tuning tuned` auto-discovery step 2.2 above), optionally filtered
by platform and/or benchmark. Unlike `--tuning tuned`, `tuning list` does
**not** consult `BENCHBOX_TUNING_PATH`. If no cwd-relative `examples/tunings/`
directory exists (e.g. `benchbox` is installed as a package and run outside a
repository checkout), `tuning list` falls back to the same packaged-resource
tier as `--tuning tuned` (step 2.4 above), listing whatever packaged templates
are bundled with the installed package instead of reporting nothing.

```bash
benchbox tuning list
benchbox tuning list --platform duckdb
benchbox tuning list --platform duckdb --benchmark tpch
benchbox tuning list --benchmark tpcds
```

### Options

- `--platform TEXT`: Filter to a specific platform (e.g. `duckdb`, `snowflake`)
- `--benchmark TEXT`: Filter to a specific benchmark (e.g. `tpch`, `tpcds`)

(cli-tuning-show)=
## `tuning show` - Show Resolved Tuning Configuration

Show what a given `--tuning` value would actually resolve to for a
platform/benchmark - useful for checking which file (if any) `--tuning tuned`
or `--tuning auto` would pick up before running a full benchmark.

```bash
benchbox tuning show tuned --platform duckdb --benchmark tpch
benchbox tuning show ./my-tuning.yaml
benchbox tuning show auto --platform polars
benchbox tuning show notuning
```

### Arguments and Options

- `TUNING_ARG` (positional, default: `tuned`): `tuned`, `notuning`, `auto`, or a file path
- `--platform TEXT`: Target platform, used for template discovery
- `--benchmark TEXT`: Target benchmark, used for template discovery

(cli-tuning-platforms)=
## `tuning platforms` - List Platform Tuning Capabilities

Show which SQL and DataFrame platforms support tuning and a summary of their
key tuning options; does not take any options.

```bash
benchbox tuning platforms
```

## Related

- [Run Command](run.md) - Using `--tuning` option during benchmark execution
- [DataFrame Platforms](../../platforms/dataframe.md) - DataFrame platform documentation
