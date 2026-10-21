# Tuning Configuration Examples

This directory contains example tuning configuration YAML files consumed by
`benchbox run --tuning`. They demonstrate a "tuned" configuration (constraints,
partitioning/sorting, platform-specific optimizations) against a baseline
"notuning" configuration for the same platform/benchmark pair.

## File layout

SQL platforms follow `examples/tunings/<platform>/<benchmark>_tuned.yaml` (and
a matching `<benchmark>_notuning.yaml`):

- `duckdb/` - `tpch`, `tpcds`, `clickbench`, `ssb`, `amplab`, `h2odb`,
  `read_primitives`, `joinorder`, `tpchavoc`
- `databricks/` - `tpch`, `tpcds`, `ssb`, `read_primitives`, `tpchavoc`, plus
  `tpch_liquid_tuned.yaml` / `tpcds_liquid_tuned.yaml` (Liquid Clustering AUTO
  variants of the same logical profile, alongside the legacy Z-ORDER
  `tpch_tuned.yaml` / `tpcds_tuned.yaml`)
- `snowflake/` - `tpch`, `tpcds` (generated from the logical tuning profile
  by `scripts/generate_cloud_tpc_templates.py`; the only certified
  cloud-template platform today because its clustering renders post-load via
  `ALTER TABLE ... CLUSTER BY`, while BigQuery and Redshift layouts stay
  preview-only in the current adapters)
- `clickhouse/` - `tpch`, `tpcds`, `ssb`. One directory serves every
  ClickHouse platform: `clickhouse-local`, `clickhouse-server`,
  `clickhouse-cloud` and the `chdb` alias. TPC-H, SSB and TPC-DS set a MergeTree sort
  key per fact table; TPC-H also partitions `LINEITEM` and `ORDERS` by month.
  All constraints are disabled, because the sort key is ClickHouse's primary
  index
- `timescaledb/` - `tpch`. Converts `lineitem` and `orders` to hypertables
  using their date partition columns, with compression ordering and segmentation.
  Constraints are disabled because the benchmark's logical keys do not include
  the time dimensions.
- `pg-duckdb/` - `tpch`. Partitions `lineitem` and `orders` by their date
  columns and clusters them on their order keys after load. Constraints are
  disabled because the benchmark's logical keys do not include the partition
  columns.
- `paradedb/` - `tpch`. Same heap-layout profile as `pg-duckdb/`: partitions
  `lineitem` and `orders` by their date columns and clusters them on their
  order keys after load, with constraints disabled.
- `citus/` - `tpch`. Hash-distributes `lineitem` and `orders` on their order
  keys so they colocate, replicates the other tables as reference tables, stores
  `part` columnar and clusters `orders` by date after load. Constraints are
  disabled because distributed tables cannot enforce keys that omit the
  distribution column.

DataFrame platforms live under `dataframe/` with a flat
`<platform>_<profile>.yaml` naming (e.g. `polars_optimized.yaml`,
`dask_memory_constrained.yaml`, `cudf_default.yaml`). Only the
`<platform>_optimized.yaml` files for `polars`, `pandas` and `cudf` are found
by `--tuning tuned` (see below). Pass any other file to `--tuning` by path.

## Using with the CLI

The `benchbox run` flag is `--tuning` (there is no `--tuning-config` flag).
It accepts one of the keywords `tuned`, `notuning`, `auto`, or an explicit
path to a YAML file; it defaults to `notuning` when omitted.

```bash
benchbox run --platform duckdb --benchmark tpch --tuning tuned

benchbox run --platform duckdb --benchmark tpch --tuning notuning

benchbox run --platform duckdb --benchmark tpch \
  --tuning examples/tunings/duckdb/tpch_tuned.yaml

benchbox run --platform polars --benchmark tpch --mode dataframe --tuning tuned
benchbox run --platform polars --benchmark tpch --mode dataframe --execution-engine streaming \
  --tuning examples/tunings/dataframe/polars_streaming.yaml
```

The commands, in order:

- `tuned` auto-discovers the platform and benchmark tuned template. This is the primary way to use it.
- `notuning` is the explicit baseline, with no tuning.
- The third command points directly at a file.
- The fourth command uses `tuned` on a DataFrame platform, which resolves `dataframe/polars_optimized.yaml`.
- The last command references another DataFrame profile by path. Any DataFrame profile other than the curated one must be referenced this way.

`examples/unified_runner.py` is a lighter-weight alternative to the `benchbox`
CLI for scripting/automation; it accepts the same `--tuning` values but
**defaults to `tuned`** (the main CLI defaults to `notuning`):

```bash
python examples/unified_runner.py --platform duckdb --benchmark tpch --scale 0.1 --tuning notuning
```

See `examples/features/tuning_comparison.py` for a runnable walkthrough of
comparing baseline vs. tuned performance.

## Auto-discovery (`--tuning tuned`)

`--tuning tuned` is the primary day-to-day UX: you don't reference a path,
BenchBox finds the matching template for you. The full resolution order
(covering every `--tuning` value, not just `tuned`) is documented in
[docs/reference/cli/tuning.md](../../docs/reference/cli/tuning.md); the part
relevant to these templates is:

1. `benchbox.yaml`'s `tuning.default_config_file` (overridable with the
   `BENCHBOX_TUNING_CONFIG` environment variable), if set and the file exists.
2. `$BENCHBOX_TUNING_PATH/<platform>/<benchmark>_tuned.yaml`, if
   `BENCHBOX_TUNING_PATH` is set.
3. `examples/tunings/<platform>/<benchmark>_tuned.yaml`, resolved relative to
   the current working directory.
4. `<platform>/<benchmark>_tuned.yaml`, also resolved relative to cwd.
5. A copy packaged with the installed `benchbox` (see
   `benchbox/core/tuning/templates/README.md`).
6. DataFrame platforms only: `examples/tunings/dataframe/<platform>_optimized.yaml`
   (cwd-relative), for `polars`, `pandas` and `cudf`. The run is recorded as
   mode `tuned`.
7. If none of the above exist, the run falls back to a generic configuration
   (basic constraints unless the platform has its own fallback), prints a
   warning that no template was found, and logs a line that names what the
   fallback applies (for example "OLAP session pack" on ClickHouse or "engine
   runtime defaults (streaming)" on Polars). An interactive terminal is also offered the tuning
   wizard. A DataFrame platform with no `_optimized.yaml`, such as Dask, is
   recorded as mode `tuned-fallback`, not `tuned`.

For the ClickHouse platforms, a directory named for the exact platform (for
example `clickhouse-cloud/`) is searched before the shared `clickhouse/`
directory in steps 2 to 4, so it can override the curated template.

Steps 3, 4 and 6 are **cwd-relative**, so `--tuning tuned` only auto-discovers
the templates in this directory when `benchbox` is run from a checkout of this
repository (or another directory that contains its own `examples/tunings/`).
If BenchBox is installed as a package and run elsewhere, step 5 still finds
the packaged templates, which cover DuckDB, Databricks, Snowflake and
ClickHouse but not the DataFrame profiles. To use your own templates, set
`BENCHBOX_TUNING_PATH` to a directory with the same
`<platform>/<benchmark>_tuned.yaml` layout - see the next section.

## Custom tuning directory - `BENCHBOX_TUNING_PATH`

Point `--tuning tuned` at a different template collection without relying on
the working directory:

```bash
export BENCHBOX_TUNING_PATH=/path/to/my-tunings
benchbox run --platform duckdb --benchmark tpch --tuning tuned
```

The directory must use the same `<platform>/<benchmark>_tuned.yaml` layout.

## Default file via `benchbox.yaml` / `BENCHBOX_TUNING_CONFIG`

Set a default file that `--tuning tuned` uses before falling back to
auto-discovery:

```yaml
tuning:
  default_config_file: ./tuning/my_tuning.yaml
```

Put this setting in `benchbox.yaml`.

or override it at runtime without editing the file:

```bash
export BENCHBOX_TUNING_CONFIG=./tuning/my_tuning.yaml
benchbox run --platform duckdb --benchmark tpch --tuning tuned
```

## Inspecting templates

```bash
benchbox tuning list

benchbox tuning list --platform duckdb --benchmark tpch

benchbox tuning show tuned --platform duckdb --benchmark tpch

benchbox tuning validate examples/tunings/clickhouse/tpch_tuned.yaml --platform clickhouse-local
```

The first command lists everything available under `examples/tunings/`. The second filters by platform and benchmark. The third shows what `--tuning` would resolve to, including which file, if any. The fourth checks a SQL tuning file against a platform's capabilities.

## Configuration structure

All tuning configurations follow the unified tuning format with these
sections:

### Constraint Configuration
- `primary_keys` - Primary key constraint settings
- `foreign_keys` - Foreign key constraint settings
- `unique_constraints` - Unique constraint settings
- `check_constraints` - Check constraint settings

### Platform Optimizations
- `platform_optimizations` - Platform-specific features (Z-ordering, auto-optimize, bloom filters, etc.)

### Table-Level Tunings
- `table_tunings` - Per-table optimizations (partitioning, clustering, distribution, sorting)

### Metadata
- `_metadata` - Configuration metadata including database, benchmark, and type information

## TPC Logical Tuning Profile

TPC-H and TPC-DS tuned templates consume a shared logical profile in
`benchbox/core/tuning/profiles/tpc.yaml`. The profile records workload-level
candidate columns, query evidence, accepted baseline columns, and
low-evidence candidates that must stay excluded unless new evidence changes the
decision.

Platform templates map that logical profile into platform-native mechanisms:
Databricks keeps the existing `*_tuned.yaml` files as legacy Z-ORDER renderings
and adds `*_liquid_tuned.yaml` files for Liquid Clustering AUTO. DuckDB uses
partitioning plus sorting and sorted layout semantics. The physical mechanisms
are different, so `tuning_mode == "tuned"` means "same logical profile coverage
where mapped", not "identical storage features".

Databricks Liquid templates set `physical_rendering_id:
databricks_liquid_auto`, keep ZORDER disabled, and avoid per-table partitioning
or distribution fields. The listed table columns are logical workload intent;
with `CLUSTER BY AUTO`, Databricks chooses effective Liquid keys asynchronously.

Run the checked-template profile gate with:

```bash
uv run -- python _project/scripts/tuning_profile_check.py --benchmarks tpch,tpcds --platforms databricks,duckdb --strict
```

See `docs/usage/tpc-tuning-profiles.md` for the profile schema, current
Databricks/DuckDB mapping matrix, result metadata fields, and comparison
caveats. Do not treat a benchmark-specific tuned template and a
basic-constraints fallback as equivalent tuned runs.

## Tuned vs No-Tuning Configurations

### Tuned Configurations
- Enable the constraints that suit the platform (primary keys, foreign keys, unique constraints, check constraints). The ClickHouse templates disable all of them
- Include table-level optimizations (partitioning on date columns, sorting on key columns)
- Enable platform-specific features (Databricks: Z-ordering/Liquid Clustering, auto-optimize, bloom filters)

### Maintenance after the load

Some platforms run maintenance after a table loads, on tuned runs and only for
tables the tuning configuration covers:

- ClickHouse `OPTIMIZE TABLE ... FINAL`, only when you set the
  `optimize_after_load` platform option to `true` (off by default).
- Redshift `ANALYZE`, only when `auto_analyze` is `false`. Redshift's own
  `VACUUM` and `ANALYZE` pass already covers every loaded table by default.
- Databricks Delta `OPTIMIZE` and `ANALYZE`. Every Databricks load also runs a
  plain `OPTIMIZE` inside the load time while `enable_delta_optimization` is
  on; tuned tables get theirs here instead, so it runs once.
- Snowflake `RESUME RECLUSTER`.

This time is not part of data-loading time. Results report it as
`phases.post_load_maintenance`.

### ClickHouse sort keys and primary keys

The ClickHouse tuned templates set `primary_keys.enabled: false`, which is
honored for the tables they sort: those get no `PRIMARY KEY` clause and the sort
key alone defines the index. A table the template does not tune keeps the
schema's primary key and derives its `ORDER BY` from it. If you enable `primary_keys` in your own file, its columns must be a
prefix of the table's tuned sort key. Otherwise the run fails before any table
is created. Put the key columns first in the sort key, or disable
`primary_keys`.

### No-Tuning Configurations
- Disable all constraints for fastest data loading
- No table-level optimizations
- No platform-specific features enabled
- Provide baseline performance for comparison

Actual performance impact varies by platform, benchmark, and data volume; run
both configurations yourself with `benchbox run --tuning tuned` /
`--tuning notuning` and compare results rather than relying on a fixed
multiplier.

## Best Practices

1. **Development and Testing**: Use `--tuning notuning` for fast iteration
2. **Performance Evaluation**: Use `--tuning tuned` for realistic production performance
3. **Benchmarking**: Compare both configurations to understand optimization impact
4. **Production**: Adapt tuned configurations to your specific workload requirements

## Customization

You can customize any configuration file by:

1. Copying an existing configuration
2. Modifying the tuning parameters for your workload
3. Validating the configuration with your benchmark
4. Saving the custom configuration for reuse

Example customization:
```yaml
table_tunings:
  LINEITEM:
    table_name: LINEITEM
    partitioning:
    - name: L_SHIPDATE
      type: DATE
      order: 1
    sorting:
    - name: L_ORDERKEY
      type: INTEGER
      order: 1
    - name: L_PARTKEY
      type: INTEGER
      order: 2
```

This is a custom TPC-H configuration with specific partitioning. The `sorting` entries add custom sorting.

For the full `--tuning` precedence order and the `tuning` command group
(`init`, `validate`, `defaults`, `list`, `show`, `platforms`), see
[docs/reference/cli/tuning.md](../../docs/reference/cli/tuning.md).
