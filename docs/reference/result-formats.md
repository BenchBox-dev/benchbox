<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Result Export Formats

```{tags} reference, validation
```

BenchBox exports benchmark results in multiple formats for analysis, visualization, and integration with external tools.

## Export Commands

### Basic Export

```bash
benchbox run --platform duckdb --benchmark tpch --scale 0.1

ls benchmark_runs/results/
```

Results are saved automatically to `benchmark_runs/results/`, with file names such as `tpch_duckdb_sf0.01_20251212_143021.json`.

### Export to Other Formats

```bash
benchbox export --last --format csv

benchbox export --last --format csv --format html

benchbox export benchmark_runs/results/tpch_duckdb_sf0.01_20251212_143021.json --format csv --format html
```

The first command exports the most recent result to CSV, the second exports to several formats, and the third exports a specific result file.

### Custom Output Directory

```bash
benchbox run --platform duckdb --benchmark tpch --output ./my_results/

benchbox run --platform snowflake --benchmark tpch --output s3://bucket/results/
```

The first command writes to a local directory and the second writes to cloud storage.

## JSON Format (Schema v2)

The JSON export is the canonical schema-v2 result bundle containing complete
benchmark details. Schema v2 spans minors `"2.0"` through `"2.3"`. Current runs emit
`"2.3"` in the top-level `result_schema_version` field. Readers accept
`result_schema_version` -> `version` -> `schema_version` in that order, so
bundles written before the rename keep loading.
Consumer policy is intentionally split by use case:

| Consumer | Accepted versions | Behavior |
| --- | --- | --- |
| Producer/exporter | `"2.3"` | New bundles are written with the current producer version. |
| Runtime loader and exporter listing | `"2.0"`, `"2.1"`, `"2.2"`, `"2.3"` | Unknown versions fail closed and should be re-exported. |
| Normalizer | `"2.0"`, `"2.1"`, `"2.2"`, `"2.3"` as v2; other shapes as legacy | Known v2 bundles use exact v2 field mapping; v1.x and unknown shapes use legacy best-effort extraction. |
| Public submission validator | Numeric `2.x` | Forward-compatible for schema-v2 minor versions, but missing or malformed versions are rejected. |
| Explorer pipeline input | `"2.0"`, `"2.1"`, `"2.2"`, `"2.3"` | Unsupported bundles are rejected before explorer read-model projection. |

### Schema Structure

```json
{
  "result_schema_version": "2.2",
  "run": {
    "id": "tpch-duckdb-20260521",
    "timestamp": "2026-05-21T14:30:21.123456Z",
    "total_duration_ms": 45230,
    "query_time_ms": 39800
  },
  "benchmark": {
    "id": "tpch",
    "name": "TPC-H",
    "scale_factor": 0.1
  },
  "platform": {
    "name": "duckdb",
    "version": "1.2.0"
  },
  "config": {
    "execution_mode": "sql"
  },
  "summary": {
    "queries": {
      "total": 22,
      "passed": 22,
      "failed": 0
    },
    "timing": {
      "total_ms": 39800,
      "avg_ms": 1809.1
    },
    "validation": "passed",
    "tpc_metrics": {
      "power_at_size": 89.5
    }
  },
  "phases": {
    "data_generation": { "...": "..." },
    "schema_creation": { "...": "..." },
    "data_loading": { "...": "..." },
    "power_test": { "...": "..." }
  },
  "queries": [
    {
      "id": "Q1",
      "ms": 1520,
      "rows": 4,
      "iter": 1,
      "stream": 0,
      "run_type": "measurement",
      "status": "SUCCESS"
    }
  ],
  "environment": {
    "os": "macOS",
    "arch": "arm64"
  }
}
```

### Field Reference

#### Version

| Field | Type | Description |
| ------- | ------ | ------------- |
| `result_schema_version` | string | Result bundle schema version. Current producer version is `"2.3"`. |
| `version` | string | Compatibility alias emitted with `result_schema_version` during the schema-v2 transition; accepted as a fallback when the new key is absent. If both keys are present, they must match. |
| `schema_version` | string | Oldest key, accepted as a last-resort fallback for pre-rename bundles. |

#### Run-level Flags

| Field | Type | Description |
| ------- | ------ | ------------- |
| `tpc_compliant` | boolean | Optional; false when queries are routed through a gateway. Omitted for native runs. |

#### Benchmark Block

| Field | Type | Description |
| ------- | ------ | ------------- |
| `id` | string | Benchmark identifier (tpch, tpcds, ssb, etc.) |
| `name` | string | Display name |
| `scale_factor` | float | Data scale factor |
| `test_type` | string | Optional run/test classification |

#### Platform Block

| Field | Type | Description |
| ------- | ------ | ------------- |
| `name` | string | Platform identifier |
| `version` | string | Platform/driver version |
| `deployment` | object | Optional normalized deployment metadata |
| `cloud`, `compute`, `storage` | object | Optional normalized environment facets |
| `config` | object | Optional adapter configuration flattened out of `platform_info` (e.g. Databricks clustering strategy below) |
| `tuning` | object | Tuning summary, present on bundles for runs executed by this version except a tuned run that recorded neither a status nor a ledger (see `platform.tuning`) |

Platform-specific extensions should stay inside the existing schema-v2 blocks
where possible. Current canonical locations are `platform.*` for platform
facets and raw platform metadata, `phases.<stage>` for lifecycle-stage
summaries, and `comparisons.*` for cross-engine comparison data. New top-level
keys require a public-contract update and consumer tests.

##### Execution variants (schema 2.3)

These fields are optional and omitted until a producer supplies them. Missing
values are not filled with nulls, except in the `platform.execution_engine`
receipt: a platform with no declared engines records `applied` and
`applied_class` as `null`. Display labels are separate from identity.

| Field | Meaning |
|-------|---------|
| `config.execution_engine` | Requested engine name; `default` leaves the choice to the platform. |
| `platform.execution_engine` | Adapter receipt, ordered as `requested`, `applied`, `applied_class`, `applied_native`, `resolution`, `observed`, `observed_source`. |
| `queries[].execution_engine` | Observed query engine name, or `unknown` when capture produced no receipt. |
| `platform.compute.resource`, `resource_kind`, `size` | Named compute object, its kind, and its size, in that order. Publication drops `resource`; kind and size remain readable. |
| `platform.deployment.selected`, `selected_class` | Selected manifest deployment and its class, before other deployment metadata. |
| `platform.gateway` | Routed gateway receipt, ordered as `name`, `routed`; omitted for native runs. |
| `platform.variant` | `<platform_id>[~<deployment>][+<requested_engine>][@<gateway>]`; default deployment and engine and native gateway add no suffix. |

Receipt resolution is `explicit`, `tuning_profile`, `legacy_option`,
`version_default`, or `platform_default`. Run-level observation may be a
name, `mixed`, or `not_captured`; its source is `explain`, `vendor_log`,
`session_var`, or `none`. Native arguments remain subject to publication
privacy rules. Engine and gateway vocabulary stays readable; gateway hosts
are represented as endpoint pseudonyms.

The loader reads older bundles as requested `default`, with applied and
observed `unknown`. Legacy `config.platform_options.streaming=true` maps to
requested `streaming` and resolution `legacy_option` for Polars DataFrame
bundles only.
`platform.config.engine_requested` maps to its recorded value and resolution
`explicit`, except `default`, which uses `version_default`. Athena's legacy
`platform.compute.engine` reads as `product`. These mappings happen in memory;
published bytes and result IDs are unchanged.

##### `platform.config` (optional)

Adapter configuration recorded per run. The Databricks adapter records its
resolved clustering strategy here as `databricks_clustering_strategy`:
`"z_order"`, `"liquid_clustering"`, `"liquid_clustering_auto"`, or `"none"`
for untuned runs. Bundles predating this field omit it. (`platform.tuning`
carries the requested-tuning summary and never holds this key.)

##### `platform.tuning`

Present on bundles for runs executed by this version. An untuned run
(`--tuning notuning`) carries `validation_status`, normally `not_applicable`,
and nothing else unless the run recorded session settings or a drift check; in
that case the block also carries `applied` and `applied_ledger_hash`. A run
that resolved a tuning configuration adds the fields below. A tuned run whose
status was never derived and that has no applied ledger omits the block's
`validation_status`. Bundles from older runs omit the block, or hold a block
without `validation_status`, and re-exporting one (for example with
`benchbox export`) keeps it that way: the exporter never states a status the
original run did not record, and a stored `not_validated` placeholder counts as
not recorded. The one exception is a bundle with no block that still has an
`.applied.json` companion: re-exporting it adds `validation_status` from the
status recorded in that ledger. Consumers must treat a missing block or a
missing `validation_status` as "not recorded". All requested-configuration
values are **self-attested** - they describe what was *requested*, not an
independently verified record of what physically applied (see
`docs/development/tuning-adr-001-trust-and-hash-semantics.md`).

| Field | Type | Description |
| ------- | ------ | ------------- |
| `tuning_source` | string | Raw `TuningSource` enum value: `explicit_file`, `auto_discovered`, `smart_defaults`, `baseline`, `wizard`, or `fallback`. |
| `requested_config_hash` | string | Full 64-hex-char SHA-256 over the requested `UnifiedTuningConfiguration.to_dict()` (canonical JSON, sorted keys). Identifies the requested template regardless of platform or dict ordering. |
| `validation_status` | string | Execution-derived tuning verification state: `not_applicable`, `noop`, `applied_unverified`, `applied_verified`, or `failed`. Unlike the requested-config fields (which describe intent), this reflects what the execution path *actually did*: `applied_unverified` means at least one tuning statement executed (self-attested), and `applied_verified` means it was additionally **corroborated by a post-load introspection receipt** against the live catalog (the per-statement receipt itself is recorded in `platform.tuning.applied.receipt`). When `platform.tuning.applied` is present, its `status` equals this field. Emitted for runs executed by this version: an untuned run reports `not_applicable`, as does a DataFrame run whose configuration is empty or all defaults; a run that requested tuning and applied nothing reports `noop`; a tuned run reports the status its execution path derived, or the applied ledger's status when none was, and omits the field when it has neither. Absent from bundles whose run never recorded it, and a re-export of such a bundle keeps it absent (apart from the `.applied.json` case described above). |
| `tuning_policy_generation` | string | Explicit tuning-policy generation marker, currently `"adr-003-spark-baseline"` (earlier bundles carry `"adr-003"`). Identifies which generation of the tuning policy this run was produced under, so tuned results from different generations can be flagged as not directly comparable. Sourced from the `TUNING_POLICY_GENERATION` constant (`benchbox/core/tuning/policy_generation.py`), **never** derived from `benchbox_version`. Bundles predating this field omit it; consumers treat that absence as the generation before the marker existed. See `docs/development/tuning-adr-003-baseline-and-single-renderer.md`. |
| `template_evidence` | string | One of `unmeasured`, `measured-benefit`, `measured-neutral`, or `measured-regression`. These values describe a calibrated comparison with `notuning`: no measurement, benefit beyond noise, neutral within noise, or regression beyond noise / an untuned query failure. Each registry entry has a `measurements` list scoped by scale factor, engine version, and a memory object `{basis, value}`: `container_limit` uses the captured container limit, while `host_total_gb` uses captured host memory in GB. Missing or invalid memory metadata cannot match; a mismatch returns `unmeasured`. Only applies to runs sourced from a shipped `*_tuned.yaml` template present in the registry. Never enters `requested_config_hash`. Sourced from `benchbox/core/tuning/profiles/template_evidence.yaml`. |
| `counts.tables_tuned` | number | Number of tables with at least one table-level tuning (partitioning/clustering/distribution/sorting). |
| `counts.tuning_types` | array | Sorted list of tuning categories actually active (constraint names, platform optimization flags, table-tuning clause types). |
| `logical_profile` | object | Optional workload-profile coverage metadata (unrelated to the requested-config hash). |
| `source`, `hash` | string | **Legacy bridge keys**, kept for one schema generation so any external consumer of these documented keys keeps working (this is not the explorer ingest pipeline, which reads tuning facets from `data["config"]`, never from `platform.tuning`). `source` is `"yaml"` when `tuning_source` is `explicit_file`/`auto_discovered`, else `"auto"`. `hash` mirrors `requested_config_hash`. Do not add new readers of these two keys - read `tuning_source`/`requested_config_hash` instead. |

The full detail lives in the same block, so a bundle needs no companion file
to answer what a run requested and what it applied:

- `platform.tuning.requested` holds `constraints` (primary/foreign key, unique,
  and check constraint settings), `platform_optimizations` (non-default values
  only), and `table_tunings` (complete per-table tuning structure).
- `platform.tuning.source_file` is a repo-relative path or
  `"<basename>:<content-hash>"` - never a raw local filesystem path.
- `platform.tuning.applied` holds the applied ledger: `status`, `statements`,
  `dropped`, `satisfied` when an intent was realized by DDL, and the
  introspection `receipt` when one was taken. `applied_ledger_hash` stays on
  `platform.tuning` itself.
  - `dropped` lists requested intents that no executed statement realized, each
    with a `reason`. A dropped intent blocks `applied_verified`. After the data
    loads on a SQL platform, the run checks every requested intent (each
    table's partitioning, clustering, distribution and sorting, each enabled
    constraint type that the benchmark declares, and each enabled platform
    optimization) against the ledger. An intent with no
    executed, failed, dropped or satisfied record is added here. Its reason is
    the capability registry's note when the registry says the platform does not
    render that tuning type, and `adapter rendered no statement` otherwise.
  - `satisfied` lists intents that an executed statement realized as a side of
    its own DDL, such as a ClickHouse or StarRocks sort expressed as `ORDER BY`
    in `CREATE TABLE`, or a primary key declared inline in `CREATE TABLE`.
    Each entry has the `intent`, `satisfied_by` (the index of
    that statement in `statements`), and a `reason`. It is omitted when empty,
    is not part of `applied_ledger_hash`, and never counts as corroboration:
    only the referenced statement's own receipt entry does.

Bundles exported before this were accompanied by `<result>.tuning.json` and
`<result>.applied.json` files carrying the same content. Nothing writes them
now; readers still accept them where they exist.

The `.plans.json` companion file (same base filename, `.plans.json` suffix)
carries captured query plans keyed by query id. Tree depth is bounded by the
run's `plan_max_depth` platform option (default 50): nodes beyond the bound
are serialized as shallow markers carrying `truncated_at_depth`, and the
payload top level always records the applied `max_depth` plus a `truncated`
flag telling consumers whether any node was depth-truncated.

#### Run Block

| Field | Type | Description |
| ------- | ------ | ------------- |
| `timestamp` | string | Run start time (ISO 8601) |
| `id` | string | Run identifier |
| `total_duration_ms` | number | Total benchmark duration |
| `query_time_ms` | number | Total query execution duration |

#### Phases Block

Phases are keyed by lifecycle stage. Each phase block may carry status,
duration, counts, and stage-specific metadata.

```json
{
  "data_generation": {
    "duration_ms": 5230,
    "status": "SUCCESS",
    "tables_generated": 8,
    "total_rows_generated": 150000
  },
  "schema_creation": {
    "duration_ms": 120,
    "status": "SUCCESS",
    "tables_created": 8
  },
  "data_loading": {
    "duration_ms": 3500,
    "status": "SUCCESS",
    "tables_loaded": 8
  },
  "power_test": {
    "duration_ms": 39800,
    "status": "SUCCESS",
    "query_count": 22,
    "geometric_mean_time": 0.482,
    "power_at_size": 82.4,
    "refresh_first_set": 1,
    "refresh_functions": [
      {"function": "RF1", "update_set": 1, "duration_ms": 48, "rows": 100, "status": "SUCCESS", "error": null},
      {"function": "RF2", "update_set": 1, "duration_ms": 55, "rows": 100, "status": "SUCCESS", "error": null}
    ]
  },
  "throughput_test": {
    "duration_ms": 19700,
    "status": "SUCCESS",
    "num_streams": 2,
    "stream_results": []
  }
}
```

When refresh is enabled for TPC-H, `power_test` records the refresh set and
function evidence alongside its query count, geometric mean time, and
Power@Size. `throughput_test.num_streams` records the configured stream count,
including when individual stream results are unavailable. These phase fields
are retained when a result file is loaded and exported again.
Refresh function durations use whole milliseconds; a successful function has
at least 1 ms of recorded duration.

`post_load_maintenance` is an optional phase, present only when a platform ran
tuning operations after a table loaded (ClickHouse `OPTIMIZE` when
`optimize_after_load` is enabled, Redshift `ANALYZE` when `auto_analyze` is
off, Databricks Delta `OPTIMIZE` and `ANALYZE`, Snowflake `RESUME RECLUSTER`).
`status` is `FAILED` when a maintenance statement raised or was refused:

```json
{
  "post_load_maintenance": {
    "status": "SUCCESS",
    "duration_ms": 4200,
    "tables_processed": 8
  }
}
```

`duration_ms` is the measured time of those operations across all tables.
`data_loading.duration_ms` and each table's load time exclude it, so load time
stays comparable between runs with and without maintenance.

#### Summary Block

| Field | Type | Description |
| ------- | ------ | ------------- |
| `queries.total` | int | Total query records represented in the bundle |
| `queries.passed` | int | Count of passed queries |
| `queries.failed` | int | Count of failed queries |
| `timing.total_ms` | number | Total query execution time in milliseconds |
| `timing.avg_ms` | number | Average query execution time in milliseconds |
| `validation` | string or object | Validation result summary |
| `tpc_metrics` | object | Optional TPC-style metrics such as `power_at_size` |

#### Environment Block

The top-level `environment` block contains execution environment metadata for the BenchBox client runner, including host operating system, architecture, CPU details, and memory. It also supports optional structured subsections for container isolation and client-to-platform connectivity.

| Field | Type | Description |
| ------- | ------ | ------------- |
| `os` | string | Operating system name (e.g., `macOS`, `Linux`, `Windows`) |
| `arch` | string | Host architecture (e.g., `arm64`, `x86_64`) |
| `cpu_count` | int | Logical CPU count |
| `memory_gb` | number | Total system memory in gigabytes |
| `python` | string | Python interpreter version |
| `client_host` | object | Normalized client host profile |
| `platform_runtime` | object | Target platform runtime environment profile |
| `container` | object | Optional container environment profile |
| `client_link` | object | Optional client-to-platform locality disclosure and statement overhead probe metrics |

##### `environment.client_link` (optional)

Discloses the client execution location and connectivity characteristics relative to remote and cloud data warehouses. Because client-to-platform distance (such as cross-region network latency or WAN round trips) can dominate small query execution times, `client_link` provides standardized, non-identifying locality metrics without publishing private network identifiers (such as raw IP addresses, hostnames, or ports).

| Field | Type | Description |
| ------- | ------ | ------------- |
| `collection_status` | string | Locality probe lifecycle status: `"available"` (region and probe recorded), `"partial"` (region or probe recorded, but not both), `"unavailable"` (neither recorded), or `"not_requested"` (collection disabled or skipped). |
| `source` | string | Provenance of client cloud/region metadata: `"observed"` (detected via link-local cloud instance metadata service / IMDS), `"cli_option"` (attested via CLI options `--client-cloud` / `--client-region`), or `"unavailable"`. |
| `client_region` | string \| null | Cloud region where the BenchBox client runner is executing (e.g., `"us-east-1"`, `"eu-west-2"`). Omitted or `null` when running outside known clouds or unavailable. |
| `client_cloud` | string \| null | Cloud provider of the client runner (`"aws"`, `"gcp"`, `"azure"`). Omitted or `null` when running outside known clouds or unavailable. |
| `statement_overhead_ms` | object | Post-benchmark statement round-trip overhead probe against the target platform. Measures baseline client↔platform transaction round-trip latency using repeated lightweight queries (typically 5 `SELECT 1` statements). |
| `statement_overhead_ms.samples` | int | Number of overhead probe query executions recorded (typically `5`). |
| `statement_overhead_ms.min` | float | Minimum statement overhead observed across samples, in milliseconds floor. Serves as the transport/driver floor. |
| `statement_overhead_ms.median` | float | Median statement overhead observed across samples, in milliseconds. |
| `collection_error_class` | string \| null | Optional exception or error class name if locality discovery or overhead probing failed. |
| `collection_error_message` | string \| null | Fixed-template diagnostic (`"<ErrorClass>: statement overhead probe failed"`). Raw error text is never published, so hostnames, IPs, and credentials cannot leak through this field. |

The probe issues 1 warmup plus 5 `SELECT 1` statements on the live connection after the workload succeeds, bounded by a 5-second deadline. On billable warehouses (Snowflake, Athena, Redshift) these are metered statements; pass `--no-link-probe` to skip them. Probe wall time is excluded from the published run `total_duration`. The Results Explorer shows `min`/`median` only; `samples` stays in the bundle.

###### Example: Observed Cloud VM Run

An AWS EC2 runner in `us-east-1` executing benchmarks against a cloud data warehouse in the same cloud region:

```json
{
  "client_link": {
    "collection_status": "available",
    "source": "observed",
    "client_cloud": "aws",
    "client_region": "us-east-1",
    "statement_overhead_ms": {
      "samples": 5,
      "min": 1.42,
      "median": 1.68
    }
  }
}
```

###### Example: Non-Cloud / Laptop Run

A developer running BenchBox on a local workstation or laptop against a remote database without cloud IMDS:

```json
{
  "client_link": {
    "collection_status": "partial",
    "source": "unavailable",
    "client_cloud": null,
    "client_region": null,
    "statement_overhead_ms": {
      "samples": 5,
      "min": 42.15,
      "median": 45.80
    }
  }
}
```

#### Tables Block

The optional top-level `tables` block records per-table load outcomes keyed
by table name.

| Field                    | Type   | Description                                         |
| ------------------------ | ------ | --------------------------------------------------- |
| `tables.{table}.rows`    | number | Rows loaded into the table.                         |
| `tables.{table}.load_ms` | number | Wall-clock load time for the table in milliseconds. |

Absence of `load_ms` means "not measured" and is always accepted; an
explicit `load_ms: 0` is a measured zero and stays distinguishable from a
missing key. Older bundles may omit the block. The Results Explorer does not
read it; it is bundle-level diagnostic data.

#### Export Block

The `export` block records how and when the bundle file was written.

| Field | Type | Description |
| ------- | ------ | ------------- |
| `timestamp` | string | Export time (ISO 8601). |
| `tool` | string | Exporter name. |
| `benchbox_version` | string | BenchBox package version that wrote the bundle. Useful as a provenance marker when bundle semantics change across releases. |
| `anonymized` | boolean | Whether the payload passed the anonymization pass. |

Emitted top-level keys follow `canonical_key_order` in
`benchbox/core/results/schema_specs.yaml` (starting with
`result_schema_version`).

### Query Execution Details

Each query execution record contains:

```json
{
  "id": "Q6",
  "ms": 234,
  "rows": 1,
  "iter": 1,
  "stream": 0,
  "run_type": "measurement",
  "status": "SUCCESS",
  "row_count_validation": {
    "status": "PASSED",
    "expected": 1,
    "actual": 1
  }
}
```

`row_count_validation` was introduced in schema 2.2 and is optional. When present, it records the bounded
per-query evidence behind the run-level validation status; `SKIPPED` or
`ERROR` evidence cannot support a clean validation pass.

## CSV Format

CSV export provides tabular query-level data for spreadsheet analysis.

### Query Results CSV

One row per query execution. Abbreviated example:

```text
query_id,execution_time_ms,rows_returned,status,error_message,iteration,stream
Q1,1520,4,SUCCESS,,1,0
Q2,892,460,SUCCESS,,1,0
Q3,1230,10,SUCCESS,,1,0
...
```

## HTML Format

HTML export generates a standalone report with formatted tables.

```bash
benchbox export --last --format html

benchbox export benchmark_runs/results/tpch_duckdb_sf0.01_20251212_143021.json --format html
```

The HTML report includes:

- Summary metrics card
- Query results table with timing data
- Phase duration breakdown
- Validation status table
- Platform and configuration details

## Visualizing Results

Use `benchbox visualize` to generate ASCII charts from result files:

```bash
benchbox visualize benchmark_runs/results/tpch_sf001_duckdb_sql_<timestamp>_<id>.json

benchbox visualize benchmark_runs/results/*.json --chart-type performance_bar

benchbox visualize benchmark_runs/results/*.json --no-color > charts.txt
```
The first command visualizes a specific result file, the second renders one chart type, and the last saves plain-text output to a file. Find file names with `benchbox results` or `benchbox results --paths`.

See the [Visualization Guide](../visualization/overview.md) for chart types, templates, and customization options.

## Loading Results in Python

### Load JSON Results

```python
import json
from pathlib import Path

result_file = Path("benchmark_runs/results/tpch_sf001_duckdb_sql_<timestamp>_<id>.json")
with result_file.open() as f:
    results = json.load(f)

tpc = results['summary']['tpc_metrics']
print(f"Power at Size: {tpc.get('power_at_size')}")
print(f"Total time: {results['summary']['timing']['total_ms']}ms")

for query in results['queries']:
    print(f"{query['id']}: {query['ms']}ms")
```

(`power_at_size` is absent when the run is unofficial or a query failed; use `.get()` as shown.)

```python
import glob
import json
import pandas as pd

paths = sorted(glob.glob("benchmark_runs/results/tpch_sf001_duckdb_sql_*.json"))
with open(paths[-1]) as f:
    results = json.load(f)

queries = results['queries']
df = pd.DataFrame(queries)

print(df.describe())
print(df.groupby('id')['ms'].mean())
```

### Load CSV Results

```python
import pandas as pd

df = pd.read_csv("benchmark_runs/results/tpch_sf001_duckdb_<timestamp>_<id>.csv")

print(f"Total queries: {len(df)}")
print(f"Mean execution time: {df['execution_time_ms'].mean():.2f}ms")
print(f"Slowest query: {df.loc[df['execution_time_ms'].idxmax(), 'query_id']}")
```

## Visualization Examples

### CLI Visualization

```bash
benchbox visualize benchmark_runs/results/tpch_sf001_duckdb_sql_<timestamp>_<id>.json

benchbox visualize duckdb_result.json sqlite_result.json --template head_to_head

benchbox visualize tpcds_result.json --chart-type query_histogram
```

The first command renders all applicable charts for a result file. The second compares multiple platforms, and the third draws a per-query histogram (split automatically for large benchmarks).

### Python API Visualization

```python
from benchbox.core.visualization import ResultPlotter
from benchbox.core.visualization.ascii import BarChart
from benchbox.core.visualization.ascii.bar_chart import BarData

plotter = ResultPlotter.from_sources(["results/duckdb.json", "results/sqlite.json"])

bar_data = [BarData(label=r.platform, value=r.total_time_ms or 0) for r in plotter.results]
chart = BarChart(data=bar_data, title="Platform Comparison")
print(chart.render())

from benchbox.core.visualization.exporters import export_ascii

export_ascii(
    ascii_content=chart.render(),
    output_dir="./charts",
    base_name="platform_comparison",
    format="txt",
)
```

## Schema Versioning

### Current Version: 2.3

Schema v2.3 is the newest schema minor for BenchBox result bundles, emitted by current runs. It
uses top-level `result_schema_version`, `run`, `benchmark`, `platform`,
`summary`, `queries`, and optional companion blocks such as `phases`,
`environment`, `normalized_cost`, `validation`, and `comparisons`.
Example bundles in this guide illustrate the v2 bundle structure.
Runtime loading and explorer generation intentionally accept only known v2
minor versions (`"2.0"`, `"2.1"`, `"2.2"`, and `"2.3"`). The public submission validator accepts
numeric `2.x` versions to allow forward-compatible submissions, but it rejects
missing values and malformed strings such as `"2.x"`. Legacy v1.x result shapes
are not runtime-loadable; they are handled only by the normalizer's best-effort
compatibility path.

### Version History

| Version | Changes |
| --------- | --------- |
| 2.3 | Added optional execution-engine receipts, compute resource and size, selected deployment, gateway, and variant identity; legacy reads preserve published bundles |
| 2.2 | Added bounded per-query `row_count_validation` evidence; top-level version key renamed to `result_schema_version` with `version`/`schema_version` fallback reads |
| 2.1 | Added typed result and companion metadata used by the previous producer |
| 2.0 | First schema-v2 bundle contract consumed by loader, submissions, and explorer |
| 1.x | Legacy shape supported only by normalization helpers |

### Loading Legacy Results

```python
import json

from benchbox.core.results.normalizer import normalize_result_dict

with open("old_result.json") as f:
    result = normalize_result_dict(json.load(f))
print(f"Schema version family: {result.schema_version}")
```

## Anonymization

Results are anonymized by default to remove sensitive information:

- Connection strings → redacted
- Selected identifiers → omitted or represented by pseudonyms according to the published field-set policy
- Usernames, IP addresses, and emails → stripped
- API keys and tokens → redacted

Published identifiers may be represented as `<kind>_<12 hex>` pseudonyms.
Some identifier fields, including `machine_id`, are omitted from public output;
others may retain pseudonyms. Pseudonyms can group retained values, but they
are not provenance or trust signals.

```python
from benchbox.core.results.exporter import ResultExporter
from benchbox.core.results.anonymization import AnonymizationConfig

config = AnonymizationConfig(
    custom_sanitizers={r"\bacct-\d+\b": "[REDACTED]"},
)

exporter = ResultExporter(anonymize=True, anonymization_config=config)
```

`custom_sanitizers` lists extra regexes stripped from free-text fields, on top
of the built-in IP, email and SSN patterns.


### Disable Anonymization

Pass `anonymize=False` to `ResultExporter`. Exports then contain raw local
paths and host details, so treat them as private and do not submit them.

## Integration Examples

### Export to Data Warehouse

```python
import json
import pandas as pd

with open("results.json") as f:
    results = json.load(f)

queries = []
for q in results['queries']:
    queries.append({
        'run_id': results['run']['id'],
        'benchmark': results['benchmark']['id'],
        'platform': results['platform']['name'],
        'scale_factor': results['benchmark']['scale_factor'],
        **q
    })

df = pd.DataFrame(queries)
```

From here you can upload the DataFrame to a warehouse, for example with `df.to_sql('benchmark_queries', engine, if_exists='append')`.

### CI/CD Integration

```bash
benchbox run --platform duckdb --benchmark tpch --scale 0.01 \
  --output ./results/

uv run -- python -c "
import json
import sys

with open('results/tpch_duckdb_sf0.01_*.json') as f:
    results = json.load(f)

power = results['summary']['tpc_metrics']['power_at_size']
if power < 50:
    print(f'FAIL: Power@Size {power} below threshold 50')
    sys.exit(1)
print(f'PASS: Power@Size {power}')
"
```

## Related Documentation

- [Getting Started](../usage/getting-started.md)
- [Python API](python-api/results.md)
- [Understanding Results](../tutorials/understanding-results.md)
