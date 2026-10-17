# Results API

```{tags} reference, python-api, validation
```

Reference for the object that holds the outcome of a benchmark run, `BenchmarkResults`, and for the patterns that read, save and analyze it.

## Overview

The results API gives structured access to benchmark execution data:

- **`BenchmarkResults`**: a dataclass that holds the whole run.
- **`query_results`**: a list with one dict per query execution (there is no `QueryResult` class).
- **Phase timings**: `data_loading_time`, `schema_creation_time` and, in `execution_phases`, a phase breakdown.
- **Validation**: `validation_status` and `validation_details`.

`BenchmarkResults` has no serialization methods. `ResultExporter` writes a result to JSON, CSV or HTML, and `dataclasses.asdict` turns it into a dict.

## Quick Start

Run a benchmark and read the result. This needs the `duckdb` package.

```python
from benchbox.tpch import TPCH
from benchbox.platforms.duckdb import DuckDBAdapter

benchmark = TPCH(scale_factor=0.01)
results = benchmark.run_with_platform(DuckDBAdapter())

print(f"Benchmark: {results.benchmark_name}")
print(f"Platform: {results.platform}")
print(f"Total time: {results.total_execution_time:.2f}s")
print(f"Success rate: {results.successful_queries}/{results.total_queries}")

for qr in results.query_results[:3]:
    print(f"{qr['query_id']}: {qr['execution_time']:.3f}s ({qr['status']})")
```

Output (times vary):

```text
Benchmark: TPC-H
Platform: DuckDB
Total time: 0.06s
Success rate: 22/22
Q1: 0.003s (SUCCESS)
Q2: 0.005s (SUCCESS)
Q3: 0.002s (SUCCESS)
```

## Core Classes

### BenchmarkResults

#### `benchbox.core.results.models.BenchmarkResults`

<span id="benchbox.core.results.models.BenchmarkResults"></span>

The container for everything a run produced: identity, timings, query results, validation outcome and platform metadata.

**Import:** `from benchbox.core.results.models import BenchmarkResults` · **Extras:** none

##### Parameters

<span id="benchbox.core.results.models.BenchmarkResults.__init__"></span>`BenchmarkResults` is a dataclass. Every field below is both a constructor parameter and an attribute, and the attributes can be reassigned. Nine fields are required; the rest have defaults. The constructor also accepts a private `_benchmark_id_override`, which is not part of the contract.

The constructor stores what it is given and derives nothing. It does not check types, and it does not compute `failed_queries`, `total_execution_time`, `average_query_time` or any other total from `query_results`. A run through `run_with_platform` fills them. When you build a result yourself, pass them in.

**Identity and counts:**

These nine are required.

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| <span id="benchbox.core.results.models.BenchmarkResults.benchmark_name"></span>`benchmark_name` | `str` | required | Display name of the benchmark, for example `TPC-H`. |
| <span id="benchbox.core.results.models.BenchmarkResults.platform"></span>`platform` | `str` | required | Name of the platform that ran it, for example `DuckDB`. |
| <span id="benchbox.core.results.models.BenchmarkResults.scale_factor"></span>`scale_factor` | `float` | required | Scale factor of the data. |
| <span id="benchbox.core.results.models.BenchmarkResults.execution_id"></span>`execution_id` | `str` | required | Identifier of the run, a short string such as `872016fb`. |
| <span id="benchbox.core.results.models.BenchmarkResults.timestamp"></span>`timestamp` | `datetime` | required | Time of the run, as a `datetime`. A run executed through `run_with_platform` produced a `datetime` without a time zone. |
| <span id="benchbox.core.results.models.BenchmarkResults.duration_seconds"></span>`duration_seconds` | `float` | required | Duration of the whole run in seconds. |
| <span id="benchbox.core.results.models.BenchmarkResults.total_queries"></span>`total_queries` | `int` | required | Number of queries attempted. |
| <span id="benchbox.core.results.models.BenchmarkResults.successful_queries"></span>`successful_queries` | `int` | required | Number of queries that succeeded. |
| <span id="benchbox.core.results.models.BenchmarkResults.failed_queries"></span>`failed_queries` | `int` | required | Number of queries that failed. |

**Query data:**

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| <span id="benchbox.core.results.models.BenchmarkResults.query_results"></span>`query_results` | `list[dict[str, Any]]` | `[]` | One dict per query execution. See [Query result entries](#query-result-entries). |
| <span id="benchbox.core.results.models.BenchmarkResults.per_query_timings"></span>`per_query_timings` | `list[dict[str, Any]] \| None` | `[]` | Optional list of detailed per-query timing dicts, used for CSV export and analysis. |
| <span id="benchbox.core.results.models.BenchmarkResults.query_definitions"></span>`query_definitions` | `dict[str, dict[str, QueryDefinition]] \| None` | `None` | Optional nested mapping of the SQL text and parameters that were run. The values are `QueryDefinition` objects with `sql` and `parameters` attributes. |
| <span id="benchbox.core.results.models.BenchmarkResults.query_subset"></span>`query_subset` | `list[str] \| None` | `None` | Optional list of query ids the run was restricted to. |
| <span id="benchbox.core.results.models.BenchmarkResults.concurrency_level"></span>`concurrency_level` | `int \| None` | `None` | Optional concurrency level of the run. |
| <span id="benchbox.core.results.models.BenchmarkResults.execution_phases"></span>`execution_phases` | `ExecutionPhases \| None` | `None` | Optional phase breakdown of the run. Read [`ExecutionPhases`](#executionphases) before relying on it. |

**Timings and loaded data:**

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| <span id="benchbox.core.results.models.BenchmarkResults.total_execution_time"></span>`total_execution_time` | `float` | `0.0` | Seconds spent executing queries. This excludes data loading and schema creation. |
| <span id="benchbox.core.results.models.BenchmarkResults.average_query_time"></span>`average_query_time` | `float` | `0.0` | Mean query execution time in seconds. |
| <span id="benchbox.core.results.models.BenchmarkResults.geometric_mean_execution_time"></span>`geometric_mean_execution_time` | `float \| None` | `None` | Geometric mean of the query execution times in seconds, when the run computed it. |
| <span id="benchbox.core.results.models.BenchmarkResults.data_loading_time"></span>`data_loading_time` | `float` | `0.0` | Seconds spent loading data. |
| <span id="benchbox.core.results.models.BenchmarkResults.schema_creation_time"></span>`schema_creation_time` | `float` | `0.0` | Seconds spent creating the schema. |
| <span id="benchbox.core.results.models.BenchmarkResults.total_rows_loaded"></span>`total_rows_loaded` | `int` | `0` | Rows loaded across all tables. |
| <span id="benchbox.core.results.models.BenchmarkResults.data_size_mb"></span>`data_size_mb` | `float` | `0.0` | Size of the loaded data in megabytes. |
| <span id="benchbox.core.results.models.BenchmarkResults.table_statistics"></span>`table_statistics` | `dict[str, int]` | `{}` | Statistics per table, keyed by table name. |

**Benchmark identity and metrics:**

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| <span id="benchbox.core.results.models.BenchmarkResults.test_execution_type"></span>`test_execution_type` | `str` | `'standard'` | Kind of test the run performed. BenchBox uses `standard`, `power`, `throughput` and `maintenance`. |
| <span id="benchbox.core.results.models.BenchmarkResults.power_at_size"></span>`power_at_size` | `float \| None` | `None` | TPC power metric, when the run computed one. Subscale TPC runs (`compliance_class` `unofficial_subscale`) compute it for comparison within the subscale cohort only; it is not a TPC result. |
| <span id="benchbox.core.results.models.BenchmarkResults.throughput_at_size"></span>`throughput_at_size` | `float \| None` | `None` | TPC throughput metric, when the run computed one. |
| <span id="benchbox.core.results.models.BenchmarkResults.qph_at_size"></span>`qph_at_size` | `float \| None` | `None` | QphH@Size for an official TPC-H result with complete Power and Throughput phases and successful, consecutive RF1/RF2 evidence; otherwise `None`. QphDS@Size is not exported. |
| <span id="benchbox.core.results.models.BenchmarkResults.qphh_unavailable"></span>`qphh_unavailable` | `list[str] \| None` | `None` | Reasons QphH@Size was withheld, such as nonofficial scale, failed queries, query subsets, incomplete phases, or missing refresh evidence. |
| <span id="benchbox.core.results.models.BenchmarkResults.compliance_class"></span>`compliance_class` | `str \| None` | `None` | Comparability class of the methodology. A TPC-H run at scale factor 0.01 reported `unofficial_subscale`; TPC-DS also uses `official` and `unofficial_nonstandard`. |
| <span id="benchbox.core.results.models.BenchmarkResults.benchmark_version"></span>`benchmark_version` | `str \| None` | `None` | Optional version of the benchmark definition. |
| <span id="benchbox.core.results.models.BenchmarkResults.dataset_version"></span>`dataset_version` | `str \| None` | `None` | Optional dataset identity for manifest-backed benchmarks. |
| <span id="benchbox.core.results.models.BenchmarkResults.manifest_hash"></span>`manifest_hash` | `str \| None` | `None` | Optional manifest hash of the dataset, for manifest-backed benchmarks. |
| <span id="benchbox.core.results.models.BenchmarkResults.data_archive_hash"></span>`data_archive_hash` | `str \| None` | `None` | Optional hash of the dataset archive, for manifest-backed benchmarks. |
| <span id="benchbox.core.results.models.BenchmarkResults.data_generation_version"></span>`data_generation_version` | `int \| None` | `None` | Version of the data generator that produced the data. Results with different versions must not be compared as if equal. `None` for results that predate the stamp. |
| <span id="benchbox.core.results.models.BenchmarkResults.data_generation_hash"></span>`data_generation_hash` | `str \| None` | `None` | Fingerprint of the generator inputs behind the data. Distinguishes datasets whose specifications changed without a version change. `None` for results that predate the stamp. |

**Validation:**

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| <span id="benchbox.core.results.models.BenchmarkResults.validation_status"></span>`validation_status` | `str` | `'PASSED'` | Overall validation outcome. The default is `PASSED`; BenchBox's console output also recognises `FAILED` and `PARTIAL`. |
| <span id="benchbox.core.results.models.BenchmarkResults.validation_details"></span>`validation_details` | `dict[str, Any] \| None` | `None` | Optional dict of validation findings. Its keys are not fixed. |

**Platform, driver and machine:**

The inner keys of these dicts are filled by the platform adapters and vary by platform. They are not part of the contract.

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| <span id="benchbox.core.results.models.BenchmarkResults.platform_info"></span>`platform_info` | `dict[str, Any] \| None` | `None` | Optional dict describing the platform. A DuckDB run had `platform_name`, `execution_mode`, `platform_version`, `client_library_version` and `configuration`. |
| <span id="benchbox.core.results.models.BenchmarkResults.platform_metadata"></span>`platform_metadata` | `dict[str, Any] \| None` | `None` | Optional dict of additional platform metadata. |
| <span id="benchbox.core.results.models.BenchmarkResults.platform_deployment"></span>`platform_deployment` | `PlatformDeploymentMetadata \| dict[str, Any] \| None` | `None` | Optional description of how the platform is deployed. |
| <span id="benchbox.core.results.models.BenchmarkResults.platform_cloud"></span>`platform_cloud` | `PlatformCloudMetadata \| dict[str, Any] \| None` | `None` | Optional description of the cloud the platform runs in. |
| <span id="benchbox.core.results.models.BenchmarkResults.platform_compute"></span>`platform_compute` | `PlatformComputeMetadata \| dict[str, Any] \| None` | `None` | Optional description of the platform's compute resources. |
| <span id="benchbox.core.results.models.BenchmarkResults.platform_storage"></span>`platform_storage` | `PlatformStorageMetadata \| dict[str, Any] \| None` | `None` | Optional description of the platform's storage. |
| <span id="benchbox.core.results.models.BenchmarkResults.platform_raw_config"></span>`platform_raw_config` | `dict[str, Any] \| None` | `None` | Optional dict of the platform configuration the run used. |
| <span id="benchbox.core.results.models.BenchmarkResults.platform_raw_metadata"></span>`platform_raw_metadata` | `dict[str, Any] \| None` | `None` | Optional dict of raw metadata reported by the platform. |
| <span id="benchbox.core.results.models.BenchmarkResults.execution_environment"></span>`execution_environment` | `NormalizedExecutionEnvironment \| dict[str, Any] \| None` | `None` | Optional description of the machine that ran the client. |
| <span id="benchbox.core.results.models.BenchmarkResults.system_profile"></span>`system_profile` | `dict[str, Any] \| None` | `None` | Optional dict describing the machine. A Linux run had `os_type`, `os_version`, `architecture`, `cpu_model`, `cpu_cores`, `memory_gb` and `python_version`, among others. |
| <span id="benchbox.core.results.models.BenchmarkResults.database_name"></span>`database_name` | `str \| None` | `None` | Optional name of the database the run used. |
| <span id="benchbox.core.results.models.BenchmarkResults.anonymous_machine_id"></span>`anonymous_machine_id` | `str \| None` | `None` | Optional anonymised machine identifier. |
| <span id="benchbox.core.results.models.BenchmarkResults.execution_metadata"></span>`execution_metadata` | `dict[str, Any] \| None` | `None` | Optional dict of run metadata. When it holds a non-empty string under `benchmark_id`, that string becomes [`benchmark_id`](#benchbox.core.results.models.BenchmarkResults.benchmark_id). |
| <span id="benchbox.core.results.models.BenchmarkResults.execution_context"></span>`execution_context` | `dict[str, Any] \| None` | `None` | Optional dict of the parameters the run was started with. |
| <span id="benchbox.core.results.models.BenchmarkResults.engine_version"></span>`engine_version` | `str \| None` | `None` | Optional version of the database engine or service that ran the queries. |
| <span id="benchbox.core.results.models.BenchmarkResults.engine_version_source"></span>`engine_version_source` | `str \| None` | `None` | Optional note on where `engine_version` came from, for example `sql_query`, `api`, `connection_metadata` or `driver_coupled`. |
| <span id="benchbox.core.results.models.BenchmarkResults.driver_package"></span>`driver_package` | `str \| None` | `None` | Optional name of the Python driver package. |
| <span id="benchbox.core.results.models.BenchmarkResults.driver_version_requested"></span>`driver_version_requested` | `str \| None` | `None` | Optional driver version that was requested. |
| <span id="benchbox.core.results.models.BenchmarkResults.driver_version_resolved"></span>`driver_version_resolved` | `str \| None` | `None` | Optional driver version that resolution selected. |
| <span id="benchbox.core.results.models.BenchmarkResults.driver_version_actual"></span>`driver_version_actual` | `str \| None` | `None` | Optional driver version that was loaded. |
| <span id="benchbox.core.results.models.BenchmarkResults.driver_runtime_strategy"></span>`driver_runtime_strategy` | `str \| None` | `None` | Optional strategy used to provide the driver at run time. |
| <span id="benchbox.core.results.models.BenchmarkResults.driver_runtime_path"></span>`driver_runtime_path` | `str \| None` | `None` | Optional path of the driver runtime. |
| <span id="benchbox.core.results.models.BenchmarkResults.driver_runtime_python_executable"></span>`driver_runtime_python_executable` | `str \| None` | `None` | Optional Python executable used for the driver runtime. |
| <span id="benchbox.core.results.models.BenchmarkResults.driver_auto_install"></span>`driver_auto_install` | `bool` | `False` | Whether the driver was installed automatically. |

**Tuning:**

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| <span id="benchbox.core.results.models.BenchmarkResults.tunings_applied"></span>`tunings_applied` | `dict[str, Any] \| None` | `None` | Optional dict of the tuning configuration the run requested. |
| <span id="benchbox.core.results.models.BenchmarkResults.tuning_config_hash"></span>`tuning_config_hash` | `str \| None` | `None` | Optional SHA-256 hash of the requested tuning configuration. It identifies the request, not what the platform executed. |
| <span id="benchbox.core.results.models.BenchmarkResults.applied_tuning_ledger"></span>`applied_tuning_ledger` | `dict[str, Any] \| None` | `None` | Optional dict recording the tuning statements the run executed. `None` when no tuning ran. |
| <span id="benchbox.core.results.models.BenchmarkResults.applied_ledger_hash"></span>`applied_ledger_hash` | `str \| None` | `None` | Optional SHA-256 hash of the executed tuning statements. Distinct from `tuning_config_hash`. |
| <span id="benchbox.core.results.models.BenchmarkResults.tuning_source_file"></span>`tuning_source_file` | `str \| None` | `None` | Optional reference to the tuning template: a repository-relative path, or `<basename>:<content-hash>` for a template outside the repository. |
| <span id="benchbox.core.results.models.BenchmarkResults.tuning_source"></span>`tuning_source` | `str \| None` | `None` | Optional name of how the tuning was chosen, for example `auto_discovered`, `explicit_file`, `wizard`, `fallback`, `smart_defaults` or `baseline`. |
| <span id="benchbox.core.results.models.BenchmarkResults.tuning_validation_status"></span>`tuning_validation_status` | `str` | `'not_validated'` | Outcome of validating the applied tuning. The default is `not_validated`; a DuckDB run without tuning reported `not_applicable`. |
| <span id="benchbox.core.results.models.BenchmarkResults.tuning_metadata_saved"></span>`tuning_metadata_saved` | `bool` | `False` | Whether tuning metadata was saved. |

**Performance, cost and plans:**

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| <span id="benchbox.core.results.models.BenchmarkResults.performance_characteristics"></span>`performance_characteristics` | `dict[str, Any]` | `{}` | Dict of performance data. Empty by default. |
| <span id="benchbox.core.results.models.BenchmarkResults.performance_summary"></span>`performance_summary` | `dict[str, Any]` | `{}` | Dict summarising query performance, for example `success_rate`, `throughput_qps` and `average_query_time_ms` in a DuckDB run. Empty by default. |
| <span id="benchbox.core.results.models.BenchmarkResults.summary_metrics"></span>`summary_metrics` | `dict[str, Any]` | `{}` | Dict of additional summary metrics. Empty by default. |
| <span id="benchbox.core.results.models.BenchmarkResults.cost_summary"></span>`cost_summary` | `dict[str, Any] \| None` | `None` | Optional dict of cost estimates. |
| <span id="benchbox.core.results.models.BenchmarkResults.resource_utilization"></span>`resource_utilization` | `dict[str, Any] \| None` | `None` | Optional dict of system resource readings. |
| <span id="benchbox.core.results.models.BenchmarkResults.query_plans_captured"></span>`query_plans_captured` | `int` | `0` | Number of queries whose plan was captured. |
| <span id="benchbox.core.results.models.BenchmarkResults.plan_capture_failures"></span>`plan_capture_failures` | `int` | `0` | Number of plan captures that failed. |
| <span id="benchbox.core.results.models.BenchmarkResults.plan_capture_errors"></span>`plan_capture_errors` | `list[dict[str, str]]` | `[]` | List of dicts describing plan capture failures. |
| <span id="benchbox.core.results.models.BenchmarkResults.plan_comparison_summary"></span>`plan_comparison_summary` | `dict[str, Any] \| None` | `None` | Optional dict comparing plans across runs or platforms. |
| <span id="benchbox.core.results.models.BenchmarkResults.total_plan_capture_time_ms"></span>`total_plan_capture_time_ms` | `float` | `0.0` | Total time spent capturing plans, in milliseconds. |
| <span id="benchbox.core.results.models.BenchmarkResults.avg_plan_capture_overhead_pct"></span>`avg_plan_capture_overhead_pct` | `float` | `0.0` | Average plan capture time as a percentage of query time. |
| <span id="benchbox.core.results.models.BenchmarkResults.max_plan_capture_time_ms"></span>`max_plan_capture_time_ms` | `float` | `0.0` | Longest single plan capture, in milliseconds. |
| <span id="benchbox.core.results.models.BenchmarkResults.native_comparison"></span>`native_comparison` | `NativeComparison \| None` | `None` | Optional comparison of pg_duckdb and native DuckDB timings. Set only by a pg_duckdb comparison run. |

**Provenance and output:**

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| <span id="benchbox.core.results.models.BenchmarkResults.funding"></span>`funding` | `str \| None` | `None` | Optional note on how the run was paid for. |
| <span id="benchbox.core.results.models.BenchmarkResults.result_source"></span>`result_source` | `str \| None` | `None` | Optional hint about who produced the result (`internal`, `community` or `vendor`). It is advisory only. |
| <span id="benchbox.core.results.models.BenchmarkResults.output_filename"></span>`output_filename` | `str \| None` | `None` | Optional file name that `ResultExporter` uses, minus its extension, for the exported files. |

##### Query result entries

Each item of `query_results` is a plain dict. An entry from a single-stream DuckDB run through `run_with_platform` has these keys:

| Key | Meaning |
| --- | --- |
| `query_id` | The query, for example `Q1`. |
| `status` | `SUCCESS` or `FAILED`. BenchBox's failure check treats a measurement entry as failed unless its status is `SUCCESS` or `SKIPPED`. |
| `execution_time` | Execution time in seconds. |
| `execution_time_seconds` | Execution time in seconds. |
| `execution_time_ms` | Execution time in milliseconds. |
| `rows_returned` | Number of rows the query returned. |
| `iteration` | Iteration number, starting at 1. |
| `stream_id` | Stream number, `0` for a single-stream run. |
| `run_type` | `measurement` for entries that count towards the result. |

Entries for other test types can have other keys. A failed throughput test, for example, has `execution_time_seconds` and an `error` message but no `execution_time`. Failed entries carry the message under `error`. When you mix test types, read with `qr.get("execution_time")`.

`ResultExporter.export_result` raises `ResultExportError` when an entry holds duration keys that disagree, such as `execution_time_ms=1.0` next to `execution_time=0.0022`.

##### Properties

<span id="benchbox.core.results.models.BenchmarkResults.benchmark_id"></span>**`benchmark_id`** (`str`, read-only) returns the identifier of the benchmark. It is the first of these that applies:

1. The value of `execution_metadata["benchmark_id"]`, when it is a non-empty string.
2. The benchmark name in lower case, with spaces and hyphens replaced by underscores and repeated underscores collapsed: `TPC-H Power` gives `tpc_h_power`, `Join Order` gives `join_order`.

A result returned by `run_with_platform` can carry an identifier that differs from this rule: a TPC-H run returned `tpch`.

##### Returns

A `BenchmarkResults` instance. `run_with_platform` also returns this type, and the Quick Start shows reading it.

##### Raises

`TypeError` when a required argument is missing, from the dataclass constructor. It raises nothing itself.

##### Example

```python
from datetime import datetime

from benchbox.core.results.models import BenchmarkResults

results = BenchmarkResults(
    benchmark_name="TPC-H Power",
    platform="DuckDB",
    scale_factor=1,
    execution_id="demo",
    timestamp=datetime(2026, 10, 3, 12, 0, 0),
    duration_seconds=1.0,
    total_queries=2,
    successful_queries=2,
    failed_queries=0,
)

print(results.benchmark_id)
print(results.total_execution_time)
print(results.query_results)
print(results.validation_status)
```

Output:

```text
tpc_h_power
0.0
[]
PASSED
```

##### Compatibility

`BenchmarkResults` in 0.4.1 has no `to_json_file`, `from_json_file`, `to_json`, `to_dict` or `from_dict` method, and no `QueryResult` class backs `query_results`. Calling the missing methods raises `AttributeError`. Use `ResultExporter` and `dataclasses.asdict` instead; see [Saving Results](#saving-results).

<span id="benchbox.core.results.models.BenchmarkResults.flightdata_source_provenance"></span>`flightdata_source_provenance` (`dict[str, Any] | None`, default `None`) holds the source provenance from the data-generation manifest of a FlightData run, and is `None` for other benchmarks. Exported results carry it as `source_provenance`. Added in 0.4.2.

### ExecutionPhases

`BenchmarkResults.execution_phases` holds a breakdown of the run into setup (data generation, schema creation, data loading, validation), power test, throughput test, maintenance test and migration phases, or `None` when the run did not record one.

The class that holds it, `ExecutionPhases`, and its attributes are not part of the public contract and may change. See [Not part of the public contract](#not-part-of-the-public-contract). For setup timings that are part of the contract, read `data_loading_time` and `schema_creation_time`.

### Nested execution phase records

All records below are mutable dataclasses except `ThroughputOutstandingWork`, which describes a dictionary. Required fields have no constructor default; a nullable required field must still be supplied. List factories create independent containers. Producers supply timestamp strings and status values; these dataclasses do not enforce a timestamp format or status enum.

#### `benchbox.core.results.models.TableGenerationStats`

<span id="benchbox.core.results.models.TableGenerationStats"></span>

One table's generated row count, data size in bytes, output file path, and generation time in milliseconds. Optional failure fields retain attempted row and byte counts and producer-supplied error details.

**Import:** `from benchbox.core.results.models import TableGenerationStats` · **Extras:** none

##### Fields

| Field | Type | Default |
| --- | --- | --- |
| `generation_time_ms` | `int` | required |
| `status` | `str` | required |
| `rows_generated` | `int` | required |
| `data_size_bytes` | `int` | required |
| `file_path` | `str` | required |
| `error_type` | `str \| None` | `None` |
| `error_message` | `str \| None` | `None` |
| `rows_attempted` | `int \| None` | `None` |
| `bytes_attempted` | `int \| None` | `None` |
| `error_timestamp` | `str \| None` | `None` |

#### `benchbox.core.results.models.DataGenerationPhase`

<span id="benchbox.core.results.models.DataGenerationPhase"></span>

Generation duration in milliseconds, table and row counts, and total data size in bytes. `per_table_stats` maps table names to generation records.

**Import:** `from benchbox.core.results.models import DataGenerationPhase` · **Extras:** none

##### Fields

| Field | Type | Default |
| --- | --- | --- |
| `duration_ms` | `int` | required |
| `status` | `str` | required |
| `tables_generated` | `int` | required |
| `total_rows_generated` | `int` | required |
| `total_data_size_bytes` | `int` | required |
| `per_table_stats` | `dict[str, TableGenerationStats]` | required |

#### `benchbox.core.results.models.TableCreationStats`

<span id="benchbox.core.results.models.TableCreationStats"></span>

One table's creation duration in milliseconds, applied constraint count, created index count, and optional producer-supplied failure details.

**Import:** `from benchbox.core.results.models import TableCreationStats` · **Extras:** none

##### Fields

| Field | Type | Default |
| --- | --- | --- |
| `creation_time_ms` | `int` | required |
| `status` | `str` | required |
| `constraints_applied` | `int` | required |
| `indexes_created` | `int` | required |
| `error_type` | `str \| None` | `None` |
| `error_message` | `str \| None` | `None` |
| `error_timestamp` | `str \| None` | `None` |

#### `benchbox.core.results.models.SchemaCreationPhase`

<span id="benchbox.core.results.models.SchemaCreationPhase"></span>

Schema-creation duration in milliseconds and table, constraint, and index counts. `per_table_creation` maps table names to creation records.

**Import:** `from benchbox.core.results.models import SchemaCreationPhase` · **Extras:** none

##### Fields

| Field | Type | Default |
| --- | --- | --- |
| `duration_ms` | `int` | required |
| `status` | `str` | required |
| `tables_created` | `int` | required |
| `constraints_applied` | `int` | required |
| `indexes_created` | `int` | required |
| `per_table_creation` | `dict[str, TableCreationStats]` | required |

#### `benchbox.core.results.models.TableLoadingStats`

<span id="benchbox.core.results.models.TableLoadingStats"></span>

One table's reported row count and loading duration in milliseconds. Optional failure fields retain processed and successful row counts and producer-supplied error details.

**Import:** `from benchbox.core.results.models import TableLoadingStats` · **Extras:** none

##### Fields

| Field | Type | Default |
| --- | --- | --- |
| `rows` | `int` | required |
| `load_time_ms` | `int` | required |
| `status` | `str` | required |
| `error_type` | `str \| None` | `None` |
| `error_message` | `str \| None` | `None` |
| `rows_processed` | `int \| None` | `None` |
| `rows_successful` | `int \| None` | `None` |
| `error_timestamp` | `str \| None` | `None` |

#### `benchbox.core.results.models.DataLoadingPhase`

<span id="benchbox.core.results.models.DataLoadingPhase"></span>

Loading duration in milliseconds and loaded row and table counts. `per_table_stats` maps table names to loading records.

**Import:** `from benchbox.core.results.models import DataLoadingPhase` · **Extras:** none

##### Fields

| Field | Type | Default |
| --- | --- | --- |
| `duration_ms` | `int` | required |
| `status` | `str` | required |
| `total_rows_loaded` | `int` | required |
| `tables_loaded` | `int` | required |
| `per_table_stats` | `dict[str, TableLoadingStats]` | required |

#### `benchbox.core.results.models.ValidationPhase`

<span id="benchbox.core.results.models.ValidationPhase"></span>

Setup validation duration in milliseconds, outcome text for row counts, schema and integrity checks, and optional additional validation details.

**Import:** `from benchbox.core.results.models import ValidationPhase` · **Extras:** none

##### Fields

| Field | Type | Default |
| --- | --- | --- |
| `duration_ms` | `int` | required |
| `row_count_validation` | `str` | required |
| `schema_validation` | `str` | required |
| `data_integrity_checks` | `str` | required |
| `validation_details` | `dict[str, Any] \| None` | `None` |

#### `benchbox.core.results.models.StatisticsGatheringPhase`

<span id="benchbox.core.results.models.StatisticsGatheringPhase"></span>

Optimizer-statistics duration in milliseconds. `stats_mode` is `explicit` for a measured ANALYZE build; `auto-on-load` and `unsupported` record a duration of 0.

**Import:** `from benchbox.core.results.models import StatisticsGatheringPhase` · **Extras:** none

`stats_lifecycle` is `reset` when statistics were invalidated before the build, `unsupported` when a requested reset was unavailable, or `persist` for an explicit warm-statistics choice. None means that the control was unused. `per_table_ms` provides an opt-in per-table ANALYZE breakdown in milliseconds. Whole-database hooks, auto-on-load, and unsupported modes leave it None. Serialization omits it when None or empty.

##### Fields

| Field | Type | Default |
| --- | --- | --- |
| `duration_ms` | `int` | required |
| `status` | `str` | required |
| `stats_mode` | `str` | required |
| `tables_analyzed` | `int` | `0` |
| `error_message` | `str \| None` | `None` |
| `stats_lifecycle` | `str \| None` | `None` |
| `per_table_ms` | `dict[str, int] \| None` | `None` |

#### `benchbox.core.results.models.SetupPhase`

<span id="benchbox.core.results.models.SetupPhase"></span>

Optional stages grouped under `ExecutionPhases.setup`. A missing stage is represented by None.

**Import:** `from benchbox.core.results.models import SetupPhase` · **Extras:** none

##### Fields

| Field | Type | Default |
| --- | --- | --- |
| `data_generation` | `DataGenerationPhase \| None` | `None` |
| `schema_creation` | `SchemaCreationPhase \| None` | `None` |
| `data_loading` | `DataLoadingPhase \| None` | `None` |
| `validation` | `ValidationPhase \| None` | `None` |
| `statistics_gathering` | `StatisticsGatheringPhase \| None` | `None` |

#### `benchbox.core.results.models.PowerTestPhase`

<span id="benchbox.core.results.models.PowerTestPhase"></span>

Power-test timestamps, duration in milliseconds, and query records. `geometric_mean_time` uses seconds. `power_at_size` is the power metric supplied by the benchmark result producer.

**Import:** `from benchbox.core.results.models import PowerTestPhase` · **Extras:** none

##### Fields

| Field | Type | Default |
| --- | --- | --- |
| `start_time` | `str` | required |
| `end_time` | `str` | required |
| `duration_ms` | `int` | required |
| `query_executions` | `list[QueryExecution]` | required |
| `geometric_mean_time` | `float` | required |
| `power_at_size` | `float` | required |

#### `benchbox.core.results.models.ThroughputStream`

<span id="benchbox.core.results.models.ThroughputStream"></span>

One throughput stream's identifier, timestamps, duration in milliseconds, query records, and success or failure outcome.

**Import:** `from benchbox.core.results.models import ThroughputStream` · **Extras:** none

##### Fields

| Field | Type | Default |
| --- | --- | --- |
| `stream_id` | `int` | required |
| `start_time` | `str` | required |
| `end_time` | `str` | required |
| `duration_ms` | `int` | required |
| `query_executions` | `list[QueryExecution]` | required |
| `success` | `bool` | `True` |
| `error_message` | `str \| None` | `None` |

#### `benchbox.core.results.models.ThroughputOutstandingWork`

<span id="benchbox.core.results.models.ThroughputOutstandingWork"></span>

Dictionary shape for workers remaining after phase completion. Both keys are required: `stream_ids` identifies the workers, and `cleanup_state` records their producer-reported cleanup state.

**Import:** `from benchbox.core.results.models import ThroughputOutstandingWork` · **Extras:** none

##### Fields

| Field | Type | Default |
| --- | --- | --- |
| `stream_ids` | `list[int]` | required key |
| `cleanup_state` | `str` | required key |

#### `benchbox.core.results.models.ThroughputTestPhase`

<span id="benchbox.core.results.models.ThroughputTestPhase"></span>

Throughput timestamps, duration in milliseconds, configured stream count, stream records and executed-query count. `throughput_at_size` is a required argument that may be None when no metric is available. `errors` contains phase error messages; `outstanding_work` retains optional evidence about workers remaining after phase completion.

**Import:** `from benchbox.core.results.models import ThroughputTestPhase` · **Extras:** none

##### Fields

| Field | Type | Default |
| --- | --- | --- |
| `start_time` | `str` | required |
| `end_time` | `str` | required |
| `duration_ms` | `int` | required |
| `num_streams` | `int` | required |
| `streams` | `list[ThroughputStream]` | required |
| `total_queries_executed` | `int` | required |
| `throughput_at_size` | `float \| None` | required |
| `success` | `bool` | `True` |
| `errors` | `list[str]` | fresh list |
| `outstanding_work` | `ThroughputOutstandingWork \| None` | `None` |

#### `benchbox.core.results.models.MaintenanceOperation`

<span id="benchbox.core.results.models.MaintenanceOperation"></span>

One maintenance operation's identifier, type, affected table, duration in milliseconds, affected-row count and outcome.

**Import:** `from benchbox.core.results.models import MaintenanceOperation` · **Extras:** none

##### Fields

| Field | Type | Default |
| --- | --- | --- |
| `operation` | `str` | required |
| `operation_type` | `str` | required |
| `table` | `str` | required |
| `execution_time_ms` | `int` | required |
| `rows_affected` | `int` | required |
| `status` | `str` | required |
| `error_message` | `str \| None` | `None` |

#### `benchbox.core.results.models.MaintenanceTestPhase`

<span id="benchbox.core.results.models.MaintenanceTestPhase"></span>

Maintenance timestamps, duration in milliseconds, operation records and query execution records.

**Import:** `from benchbox.core.results.models import MaintenanceTestPhase` · **Extras:** none

##### Fields

| Field | Type | Default |
| --- | --- | --- |
| `start_time` | `str` | required |
| `end_time` | `str` | required |
| `duration_ms` | `int` | required |
| `maintenance_operations` | `list[MaintenanceOperation]` | required |
| `query_executions` | `list[QueryExecution]` | required |

#### `benchbox.core.results.models.MigrationTableStats`

<span id="benchbox.core.results.models.MigrationTableStats"></span>

One table's migration duration in milliseconds and storage sizes before and after migration, with their change, in bytes.

**Import:** `from benchbox.core.results.models import MigrationTableStats` · **Extras:** none

##### Fields

| Field | Type | Default |
| --- | --- | --- |
| `duration_ms` | `int` | required |
| `status` | `str` | required |
| `storage_before_bytes` | `int` | required |
| `storage_after_bytes` | `int` | required |
| `storage_delta_bytes` | `int` | required |
| `error_message` | `str \| None` | `None` |

#### `benchbox.core.results.models.MigrationPhase`

<span id="benchbox.core.results.models.MigrationPhase"></span>

pg_mooncake heap-to-columnstore migration duration in milliseconds, migrated and failed table counts, and aggregate storage sizes and change in bytes. `per_table_stats` maps table names to migration records.

**Import:** `from benchbox.core.results.models import MigrationPhase` · **Extras:** none

##### Fields

| Field | Type | Default |
| --- | --- | --- |
| `duration_ms` | `int` | required |
| `status` | `str` | required |
| `tables_migrated` | `int` | required |
| `tables_failed` | `int` | required |
| `storage_before_bytes` | `int` | required |
| `storage_after_bytes` | `int` | required |
| `storage_delta_bytes` | `int` | required |
| `per_table_stats` | `dict[str, MigrationTableStats]` | required |

## Working with Results

### Loading Results

BenchBox 0.4.1 has no public loader that rebuilds a `BenchmarkResults` from a file. `BenchmarkResults` has no `from_json_file` or `from_dict` method. `ResultExporter.load_result_from_file` reads an exported file and returns its parsed content in a dict:

```python
from benchbox.core.results.exporter import ResultExporter

exporter = ResultExporter(output_dir="benchmark_runs")
paths = exporter.export_result(results)

loaded = exporter.load_result_from_file(paths["json"])
print(sorted(loaded))
print(loaded["version"])
print(loaded["data"]["benchmark"]["name"], loaded["data"]["summary"]["queries"])
```

Output:

```text
Exported JSON: benchmark_runs/tpch_sf001_duckdb_20261003_193125_196d1732.json
['data', 'filepath', 'result_schema_version', 'version']
2.2
TPC-H {'failed': 0, 'passed': 22, 'total': 22}
```

The `data` entry is the exported JSON document, not a `BenchmarkResults`. See {doc}`result-analysis` for the exporter and the comparison helpers.

### Saving Results

`ResultExporter` writes the result. `export_result` returns a dict that maps each format (`json` by default, also `csv` and `html`) to the path it wrote:

```python
from benchbox.core.results.exporter import ResultExporter

exporter = ResultExporter(output_dir="exports")
paths = exporter.export_result(results, formats=["json", "csv"])
print(sorted(paths))
```

Output:

```text
Exported JSON: exports/tpch_sf001_duckdb_20261003_193125_196d1732.json
Exported CSV: exports/tpch_sf001_duckdb_20261003_193125_196d1732.csv
['csv', 'json']
```

To get a plain dict, use `dataclasses.asdict`. It keeps `datetime` values as they are, so give `json.dumps` a `default`:

```python
import dataclasses
import json

payload = dataclasses.asdict(results)
print(json.dumps({key: payload[key] for key in ("benchmark_name", "platform", "scale_factor")}))
print(type(payload["timestamp"]).__name__)
```

Output:

```text
{"benchmark_name": "TPC-H", "platform": "DuckDB", "scale_factor": 0.01}
datetime
```

## Analyzing Results

The examples in this section use this small result, built by hand so that the output is the same on every run:

```python
from datetime import datetime

from benchbox.core.results.models import BenchmarkResults

results = BenchmarkResults(
    benchmark_name="TPC-H",
    platform="DuckDB",
    scale_factor=0.1,
    execution_id="demo01",
    timestamp=datetime(2026, 10, 3, 12, 0, 0),
    duration_seconds=2.0,
    total_queries=4,
    successful_queries=3,
    failed_queries=1,
    query_results=[
        {"query_id": "Q1", "status": "SUCCESS", "execution_time": 0.5, "rows_returned": 4, "stream_id": 0},
        {"query_id": "Q6", "status": "SUCCESS", "execution_time": 0.125, "rows_returned": 1, "stream_id": 0},
        {"query_id": "Q9", "status": "SUCCESS", "execution_time": 1.0, "rows_returned": 175, "stream_id": 0},
        {"query_id": "Q13", "status": "FAILED", "execution_time": 0.0, "rows_returned": 0, "stream_id": 0,
         "error": "out of memory"},
    ],
    total_execution_time=1.625,
    average_query_time=1.625 / 3,
)
```

### Query Performance Analysis

```python
import statistics

slowest = sorted(results.query_results, key=lambda qr: qr["execution_time"], reverse=True)

print("Slowest queries:")
for qr in slowest[:3]:
    print(f"{qr['query_id']}: {qr['execution_time']:.3f}s")

times = [qr["execution_time"] for qr in results.query_results if qr["status"] == "SUCCESS"]

print(f"Median: {statistics.median(times):.3f}s")
print(f"Mean: {statistics.mean(times):.3f}s")
print(f"Stdev: {statistics.stdev(times):.3f}s")
```

Output:

```text
Slowest queries:
Q9: 1.000s
Q1: 0.500s
Q6: 0.125s
Median: 0.500s
Mean: 0.542s
Stdev: 0.439s
```

### Geometric Mean Calculation

Standard for TPC benchmarks. `BenchmarkResults.geometric_mean_execution_time` holds the value when the run computed it. To compute it yourself:

```python
import math


def geometric_mean(values):
    if not values:
        return 0.0
    return math.prod(values) ** (1.0 / len(values))


times = [qr["execution_time"] for qr in results.query_results if qr["status"] == "SUCCESS"]
print(f"Geometric mean: {geometric_mean(times):.3f}s")
```

Output:

```text
Geometric mean: 0.397s
```

### Result Comparison

Compare two benchmark runs:

```python
import dataclasses


def compare_results(baseline, current, threshold=1.1):
    baseline_times = {
        qr["query_id"]: qr["execution_time"]
        for qr in baseline.query_results
        if qr["status"] == "SUCCESS"
    }
    current_times = {
        qr["query_id"]: qr["execution_time"]
        for qr in current.query_results
        if qr["status"] == "SUCCESS"
    }

    regressions = []
    improvements = []

    for qid in baseline_times:
        if qid in current_times:
            ratio = current_times[qid] / baseline_times[qid]
            entry = {
                "query_id": qid,
                "baseline": baseline_times[qid],
                "current": current_times[qid],
                "ratio": ratio,
            }
            if ratio > threshold:
                regressions.append(entry)
            elif ratio < (1 / threshold):
                improvements.append(entry)

    return {"regressions": regressions, "improvements": improvements}


baseline = results
current = dataclasses.replace(
    results,
    query_results=[
        {**qr, "execution_time": qr["execution_time"] * factor}
        for qr, factor in zip(results.query_results, (1.0, 0.5, 1.5, 1.0))
    ],
)
comparison = compare_results(baseline, current, threshold=1.1)

print(f"Found {len(comparison['regressions'])} regressions:")
for r in comparison["regressions"]:
    print(f"  {r['query_id']}: {r['ratio']:.2f}x slower")
print(f"Found {len(comparison['improvements'])} improvements:")
for r in comparison["improvements"]:
    print(f"  {r['query_id']}: {1 / r['ratio']:.2f}x faster")
```

Output:

```text
Found 1 regressions:
  Q9: 1.50x slower
Found 1 improvements:
  Q6: 2.00x faster
```

### Export to DataFrame

For analysis in pandas:

```python
import pandas as pd

df = pd.DataFrame(
    [
        {
            "query_id": qr["query_id"],
            "execution_time": qr["execution_time"],
            "status": qr["status"],
            "rows_returned": qr["rows_returned"],
            "stream_id": qr["stream_id"],
        }
        for qr in results.query_results
    ]
)

print(df["execution_time"].describe())
print(df.groupby("status").size())

successful = df[df["status"] == "SUCCESS"]
successful.plot(x="query_id", y="execution_time", kind="bar")
```

Output:

```text
count    4.000000
mean     0.406250
std      0.449247
min      0.000000
25%      0.093750
50%      0.312500
75%      0.625000
max      1.000000
Name: execution_time, dtype: float64
status
FAILED     1
SUCCESS    3
dtype: int64
```

## Validation Results

### Check Result Correctness

```python
import dataclasses


def report(result):
    if result.validation_status == "PASSED":
        print("All validation checks passed")
    else:
        print(f"Validation {result.validation_status}:")
        for check, details in (result.validation_details or {}).items():
            print(f"  {check}: {details}")


report(results)
report(dataclasses.replace(
    results,
    validation_status="FAILED",
    validation_details={"row_count_matches": False},
))
```

Output:

```text
All validation checks passed
Validation FAILED:
  row_count_matches: False
```

### Row Count Validation

```python
def validate_row_counts(results, expected_counts):
    mismatches = []

    for qr in results.query_results:
        if qr["query_id"] in expected_counts:
            expected = expected_counts[qr["query_id"]]
            if qr["rows_returned"] != expected:
                mismatches.append({
                    "query_id": qr["query_id"],
                    "expected": expected,
                    "actual": qr["rows_returned"],
                })

    return mismatches


expected_counts = {
    "Q1": 4,
    "Q6": 1,
    "Q9": 170,
}

mismatches = validate_row_counts(results, expected_counts)
if mismatches:
    print("Row count mismatches found:")
    for m in mismatches:
        print(f"  {m['query_id']}: expected {m['expected']}, got {m['actual']}")
```

Output:

```text
Row count mismatches found:
  Q9: expected 170, got 175
```

## System Information

### Access Execution Context

These dicts are filled by runs that go through a platform adapter, and they are `None` on a result you build by hand. `quickstart_results` below is the result from the [Quick Start](#quick-start). The keys shown are those a Linux DuckDB run produced; the contract does not fix them.

```python
print(f"OS: {quickstart_results.system_profile['os_type']} {quickstart_results.system_profile['os_version']}")
print(f"CPU: {quickstart_results.system_profile['cpu_model']}")
print(f"RAM: {quickstart_results.system_profile['memory_gb']:.1f}GB")
print(f"Python: {quickstart_results.system_profile['python_version']}")

print(f"Platform: {quickstart_results.platform_info['platform_name']}")
print(f"Version: {quickstart_results.platform_info['platform_version']}")
print(f"Client library: {quickstart_results.platform_info['client_library_version']}")
```

Output:

```text
OS: Linux 6.18.44-fc-v64
CPU: Intel(R) Xeon(R) Processor @ 2.10GHz
RAM: 15.7GB
Python: 3.11.15
Platform: DuckDB
Version: 1.5.6
Client library: 1.5.6
```

### Execution Metadata

These fields are filled by runs that go through a platform adapter. `quickstart_results` below is the result from the [Quick Start](#quick-start). The keys inside `performance_summary` are those a DuckDB run produced.

```python
print(f"Schema creation: {quickstart_results.schema_creation_time:.3f}s")
print(f"Data loading: {quickstart_results.data_loading_time:.3f}s")

summary = quickstart_results.performance_summary
print(f"Success rate: {summary['success_rate']:.0%}")
print(f"Throughput: {summary['throughput_qps']:.1f} queries/s")
print(f"Geometric mean: {quickstart_results.geometric_mean_execution_time:.4f}s")
```

Output (values vary):

```text
Schema creation: 0.000s
Data loading: 0.005s
Success rate: 100%
Throughput: 58.8 queries/s
Geometric mean: 0.0024s
```

`BenchmarkResults.execution_phases` also holds per-phase timings, but its class is not part of the public contract. See [ExecutionPhases](#executionphases).

## Performance Tracking

### Time Series Analysis

Track performance over time. Export each run with `ResultExporter` into one directory, then read the files back with `json`:

```python
import dataclasses
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from benchbox.core.results.exporter import ResultExporter

exporter = ResultExporter(output_dir="history")
for day, factor in ((1, 1.0), (2, 1.2)):
    run = dataclasses.replace(
        results,
        execution_id=f"run{day}",
        timestamp=datetime(2026, 10, day, 12, 0, 0),
        query_results=[{**qr, "execution_time": qr["execution_time"] * factor} for qr in results.query_results],
    )
    exporter.export_result(run)


def load_historical_results(results_dir):
    rows = []
    for file_path in sorted(Path(results_dir).glob("**/*.json")):
        data = json.loads(file_path.read_text())
        for query in data["queries"]:
            rows.append({
                "timestamp": data["run"]["timestamp"],
                "query_id": query["id"],
                "execution_time": query["ms"] / 1000,
                "status": query["status"],
                "platform": data["platform"]["name"],
                "scale_factor": data["benchmark"]["scale_factor"],
            })
    return pd.DataFrame(rows)


df = load_historical_results("history")

trends = df.groupby(["query_id", "timestamp"])["execution_time"].mean()
print(trends)
```

Output:

```text
Exported JSON: history/tpc_h_sf01_duckdb_20261001_120000_run1.json
Exported JSON: history/tpc_h_sf01_duckdb_20261002_120000_run2.json
query_id  timestamp
1         2026-10-01T12:00:00    0.500
          2026-10-02T12:00:00    0.600
13        2026-10-01T12:00:00    0.000
          2026-10-02T12:00:00    0.000
6         2026-10-01T12:00:00    0.125
          2026-10-02T12:00:00    0.150
9         2026-10-01T12:00:00    1.000
          2026-10-02T12:00:00    1.200
Name: execution_time, dtype: float64
```

### Regression Detection

Automated regression alerting:

```python
def detect_regressions(current_results, baseline_results, threshold=1.15):
    alerts = []

    baseline_map = {
        qr["query_id"]: qr["execution_time"]
        for qr in baseline_results.query_results
        if qr["status"] == "SUCCESS"
    }

    for qr in current_results.query_results:
        if qr["status"] != "SUCCESS":
            continue

        if qr["query_id"] in baseline_map:
            baseline_time = baseline_map[qr["query_id"]]
            ratio = qr["execution_time"] / baseline_time

            if ratio > threshold:
                alerts.append({
                    "severity": "HIGH" if ratio > 1.5 else "MEDIUM",
                    "query_id": qr["query_id"],
                    "baseline": baseline_time,
                    "current": qr["execution_time"],
                    "regression": f"{(ratio - 1) * 100:.1f}%",
                })

    return sorted(alerts, key=lambda x: x["current"], reverse=True)


alerts = detect_regressions(current, baseline, threshold=1.15)
if alerts:
    print(f"{len(alerts)} performance regressions detected:")
    for alert in alerts:
        print(f"  [{alert['severity']}] {alert['query_id']}: +{alert['regression']} regression")
```

Output:

```text
1 performance regressions detected:
  [MEDIUM] Q9: +50.0% regression
```

## Best Practices

### Result Storage

1. **Let the exporter name the files.** `ResultExporter` builds names from the benchmark, scale factor, platform, timestamp and run id. Set `output_filename` to choose one:

```python
from benchbox.core.results.exporter import ResultExporter

results.output_filename = "tpch_sf1_duckdb_baseline.json"
paths = ResultExporter(output_dir="benchmark_runs").export_result(results)
print(paths["json"].name)
```

Output:

```text
Exported JSON: benchmark_runs/tpch_sf1_duckdb_baseline.json
tpch_sf1_duckdb_baseline.json
```

1. **Version control results** for important baselines:

   ```bash
   git add benchmark_runs/baseline/tpch_sf1_duckdb.json
   git commit -m "Add TPC-H SF1 baseline"
   ```

2. **Compress old results**:

   ```bash
   gzip benchmark_runs/archive/*.json
   ```

### Result Analysis

1. **Always check validation status** before trusting results
2. **Use geometric mean** for TPC benchmarks (not arithmetic mean)
3. **Compare like with like** (same scale factor, system profile)
4. **Account for variance** with multiple runs

### Performance Monitoring

1. **Establish baseline** with known-good configuration
2. **Track over time** to detect gradual degradation
3. **Set alert thresholds** (e.g., 15% regression)
4. **Investigate outliers** before dismissing as noise

## Cost Calculation and Result Enrichment

These APIs estimate costs from packaged prices and resource metadata. Query and phase totals do not certify invoices or infrastructure idle costs. See {doc}`../../development/adr/adr-billing-unit-tb-tib-contract` for billing units.

### validate_resource_usage

#### `benchbox.core.cost.calculator.validate_resource_usage`

<span id="benchbox.core.cost.calculator.validate_resource_usage"></span>

```python
def validate_resource_usage(
    platform: str,
    resource_usage: dict[str, Any],
) -> tuple[bool, list[str]]: ...
```

Lowercase the platform and check key presence against `cost_specs.yaml`. Required keys and at-least-one groups must be present. Values are not checked for type or sign. Unexpected keys produce warnings without invalidating the input. Unknown platforms return `True` with a no-schema warning. The input is not changed.

**Import:** `from benchbox.core.cost.calculator import validate_resource_usage` · **Extras:** none

### CostCalculator

#### `benchbox.core.cost.calculator.CostCalculator`

<span id="benchbox.core.cost.calculator.CostCalculator"></span>

Construct without arguments. Cloud calculation keys are `snowflake`, `bigquery`, `redshift`, `databricks`, `databricks-df`, `athena`, `synapse`, `fabric_dw` and `firebolt`. Lookups lowercase names but do not resolve display aliases; other keys can be local or unsupported.

**Import:** `from benchbox.core.cost.calculator import CostCalculator` · **Extras:** none

##### `benchbox.core.cost.calculator.CostCalculator.is_local_platform`

<span id="benchbox.core.cost.calculator.CostCalculator.is_local_platform"></span>

```python
def is_local_platform(platform: str) -> bool: ...
```

Test the local/self-hosted platform set. Zero cloud compute cost does not measure hardware, storage or operating costs.

##### `benchbox.core.cost.calculator.CostCalculator.calculate_query_cost`

<span id="benchbox.core.cost.calculator.CostCalculator.calculate_query_cost"></span>

```python
def calculate_query_cost(
    platform: str,
    resource_usage: dict[str, Any],
    platform_config: dict[str, Any],
    validate: bool = True,
) -> QueryCost | None: ...
```

Return an estimate in packaged `CURRENCY`. Validation logs warnings but does not prevent calculation when invalid. Local platforms return zero. Unsupported platforms and exceptions inside a platform calculator return `None` after logging; earlier input/schema errors can propagate. Missing query costs are not evidence of free execution.

Fallback pricing can produce an estimate marked by `pricing_details["price_unavailable"]`. Normalized publication rejects that estimate. Snowflake uses metered warehouse `credits_used` when present; `credits_used_cloud_services` is not warehouse billing. Otherwise runtime and warehouse size can support an estimate. Runtime preference is numeric `execution_time_ms`, `execution_time_seconds`, then `total_elapsed_time_ms`; millisecond values are divided by 1,000. Booleans are not runtime measurements. Estimates exclude idle periods and multicluster scaling and can overcount concurrent warehouse use.

Byte-priced tables declare `tebibyte` (`1024**4` bytes) or `terabyte` (`10**12` bytes). Retain that distinction when providing byte counts; unit labels alone are insufficient.

`benchbox/core/cost/cost_specs.yaml` defines presence-validation schemas. The calculators consume these inputs; defaults below apply to missing configuration keys and support estimates, not observed deployment proof:

- Snowflake uses `edition="standard"`, `cloud="aws"` and `region="us-east-1"`. Runtime estimation uses a truthy per-query `warehouse_size` before configuration `warehouse_size`.
- BigQuery uses truthy `bytes_billed` before `bytes_processed` and `location="us"`. List-rate estimation starts at byte zero; it does not model the monthly free tier.
- Redshift needs `execution_time_seconds` and uses `node_type="dc2.large"`, `node_count=1` and `region="us-east-1"`. It estimates runtime-based query cost, excluding cluster idle time; total cluster spend needs full runtime.
- Databricks uses metered `dbu_consumed` when non-`None`; otherwise `execution_time_seconds` and `cluster_size_dbu_per_hour` are both needed. Defaults are `cloud="aws"`, `tier="premium"` and `workload_type="all_purpose"`. This is DBU cost only and excludes underlying cloud compute charges.
- Athena needs `data_scanned_bytes` and uses `region="us-east-1"`; legacy adapter `cost_usd` is ignored.
- Synapse lowercases `mode` and treats missing/falsy mode as `"serverless"`. That mode needs `bytes_processed`; all other mode values take the dedicated branch using `execution_time_seconds` and `dwu_level="dw100c"`. Both branches use `region="eastus"`. Dedicated pricing is per selected pool-hour, not multiplied by DWUs.
- Fabric uses non-`None` `cu_seconds`; otherwise it needs `execution_time_seconds` and resolves CU count from `sku="f64"`. It uses `region="eastus"` and converts CU-seconds to CU-hours.
- Firebolt uses non-`None` `fbu_consumed`; otherwise it needs `execution_time_seconds` and estimates consumption from hourly rate for `node_type="m"` and `node_count=1`. With metered FBUs, missing node type is recorded as `"unknown"` instead.

##### `benchbox.core.cost.calculator.CostCalculator.calculate_phase_cost`

<span id="benchbox.core.cost.calculator.CostCalculator.calculate_phase_cost"></span>

```python
def calculate_phase_cost(phase_name: str, query_costs: list[QueryCost]) -> PhaseCost: ...
```

Sum compute costs, including concurrent queries. This is summed spend, not cost per hour of phase wall time. `None` placeholders are omitted from totals and stored costs; query count remains the input list length. Empty input produces zero and no individual costs. Duration and stream count are not inferred. Callers must use packaged currency consistently; no conversion or matching-currency validation is performed.

##### `benchbox.core.cost.calculator.CostCalculator.calculate_benchmark_cost`

<span id="benchbox.core.cost.calculator.CostCalculator.calculate_benchmark_cost"></span>

```python
def calculate_benchmark_cost(
    phase_costs: list[PhaseCost],
    platform_details: dict[str, Any] | None = None,
) -> BenchmarkCost: ...
```

Delegate to `BenchmarkCost.from_phase_costs` using packaged `CURRENCY`. Phase amounts must use that currency. Storage is not automatically added to the compute total.

##### `benchbox.core.cost.calculator.CostCalculator.calculate_normalized_benchmark_cost`

<span id="benchbox.core.cost.calculator.CostCalculator.calculate_normalized_benchmark_cost"></span>

```python
def calculate_normalized_benchmark_cost(
    platform: str,
    benchmark_cost: BenchmarkCost,
    platform_config: dict[str, Any],
) -> tuple[NormalizedCost, list[str]]: ...
```

Return a normalized record and availability warnings. Local platforms return explicit zero with `not_applicable_local` and no warnings. Cloud normalization needs computed phases, billing unit, cloud/region and applicable warehouse, cluster or node sizing. Defaulted metadata, fallback price markers, missing pricing provenance or pricing older than 90 days make the amount unavailable. Concurrent Snowflake phases containing runtime-estimated credits are also unavailable.

Success converts the existing compute total through `Decimal(str(total_cost))` without recalculating queries. Scope is `compute_only`. Warnings yield `normalized_cost_usd=None` while preserving deployment and model provenance.

### validate_platform_config

#### `benchbox.core.cost.integration.validate_platform_config`

<span id="benchbox.core.cost.integration.validate_platform_config"></span>

```python
def validate_platform_config(
    platform: str,
    config: dict[str, Any],
) -> tuple[bool, list[str]]: ...
```

Check packaged requirements after lowercasing the platform. Missing or `None` values warn. Dedicated Synapse additionally needs `dwu_level`; Databricks needs `workload_type` or `warehouse_type`. Unknown platforms return `True` without warnings. This checks presence, not types or price coverage, and does not change configuration.

**Import:** `from benchbox.core.cost.integration import validate_platform_config` · **Extras:** none

### canonical_cost_platform_key

#### `benchbox.core.cost.integration.canonical_cost_platform_key`

<span id="benchbox.core.cost.integration.canonical_cost_platform_key"></span>

```python
def canonical_cost_platform_key(results: BenchmarkResults) -> str: ...
```

Resolve `platform_type`, `platform_name`, `name`, then `platform`. For each key, inspect `platform_info`, its `configuration`, then that mapping's nested `configuration`. Fall back to `results.platform`; return an empty string when absent. Strip/lowercase tokens, remove display mode suffixes and normalize spaces/underscores before registry aliases. Canonical `fabric_dw` and `clickhouse_cloud` retain underscores.

**Import:** `from benchbox.core.cost.integration import canonical_cost_platform_key` · **Extras:** none

### add_cost_estimation_to_results

#### `benchbox.core.cost.integration.add_cost_estimation_to_results`

<span id="benchbox.core.cost.integration.add_cost_estimation_to_results"></span>

```python
def add_cost_estimation_to_results(
    results: BenchmarkResults,
    platform_config: dict[str, Any] | None = None,
) -> BenchmarkResults: ...
```

Enrich and return the same object. Without platform identity it is unchanged. Otherwise calculate available query costs, phase/compute totals, optional storage estimates, warnings and `cost_summary["normalized_cost"]`. Query dictionaries receive `cost`; objects with that attribute receive its value. Fallback-priced query amounts are stored as `None`.

**Import:** `from benchbox.core.cost.integration import add_cost_estimation_to_results` · **Extras:** none

Explicit configuration overrides are used directly. Otherwise extract normalized facets and legacy metadata. Observed compute sizing may support publication; requested, inferred or defaulted values support estimates while marking normalized cost unavailable. Missing sizing fields merge across representations while retaining field provenance. Databricks configured `cluster_size` is not observed warehouse size. Override callers remain responsible for truthful metadata and `_defaulted_fields`; overrides are not independently authenticated.

Validation logs warnings and continues. Positive loaded-data size adds a separate storage estimate for at least one hour, without changing compute total or normalized scope. Exceptions inside enrichment are logged and return the same object; prior mutations are not rolled back. Identity resolution precedes this handler, so errors there can propagate.

## Cost Records and Availability

The records in `benchbox.core.cost.models` carry estimates and availability metadata. Creating a query, phase or benchmark cost record does not validate currency consistency or nonnegative amounts. Normalized costs enforce the availability rules below.

### DeploymentMetadata

#### `benchbox.core.cost.models.DeploymentMetadata`

<span id="benchbox.core.cost.models.DeploymentMetadata"></span>

Frozen deployment context. Every field defaults to `None`: `cloud_provider: str | None`, `cloud_region: str | None`, `instance_type: str | None`, `warehouse_size: str | None`, `node_count: int | None`, `cluster_size: str | None`, `storage_format: str | None` and `storage_tier: str | None`. The class stores supplied values without resolving deployment defaults.

**Import:** `from benchbox.core.cost.models import DeploymentMetadata` · **Extras:** none

##### `benchbox.core.cost.models.DeploymentMetadata.to_dict`

<span id="benchbox.core.cost.models.DeploymentMetadata.to_dict"></span>

```python
def to_dict() -> dict[str, str | int | None]: ...
```

Return all eight named fields, including those whose values are `None`.

### NormalizedCost

#### `benchbox.core.cost.models.NormalizedCost`

<span id="benchbox.core.cost.models.NormalizedCost"></span>

Frozen cost with required fields `normalized_cost_usd: Decimal | None`, `cost_model_version: str`, `cost_model_source: str`, `cost_scope: CostScope`, `cost_status: CostStatus`, `billing_unit: str` and `pricing_region: str`. The optional `deployment: DeploymentMetadata` gets a new empty context per instance. `CostScope` names `"compute_only"` and `"compute_plus_storage"`; `CostStatus` names `"normalized"`, `"not_applicable_local"` and `"unavailable"`. These Literal annotations are not runtime enum checks.

**Import:** `from benchbox.core.cost.models import NormalizedCost` · **Extras:** none

Non-`None` amounts are converted through `Decimal(str(value))`. Construction rejects a negative amount, a normalized status without an amount, a local-not-applicable status without explicit zero, and an unavailable status carrying any amount. Local zero is not comparable to normalized cloud spend.

##### `benchbox.core.cost.models.NormalizedCost.cost_usd`

<span id="benchbox.core.cost.models.NormalizedCost.cost_usd"></span>

**Type:** `Decimal | None`

Deprecated compatibility alias. Return the amount only when status is `"normalized"` and scope is `"compute_only"`; otherwise return `None`, including for storage-inclusive costs.

##### `benchbox.core.cost.models.NormalizedCost.to_dict`

<span id="benchbox.core.cost.models.NormalizedCost.to_dict"></span>

```python
def to_dict() -> dict[str, Any]: ...
```

Return every named field plus `cost_usd`. Both amount fields serialize as decimal strings or `None`; deployment serializes using `to_dict`. Decimal precision is retained rather than converted to a binary float.

### QueryCost

#### `benchbox.core.cost.models.QueryCost`

<span id="benchbox.core.cost.models.QueryCost"></span>

```python
class QueryCost:
    def __init__(
        self,
        compute_cost: float,
        currency: str = "USD",
        pricing_details: dict[str, Any] = ...,
    ) -> None: ...
```

Store the compute amount in `currency` and platform pricing context. `pricing_details` defaults to a new empty dictionary per instance.

**Import:** `from benchbox.core.cost.models import QueryCost` · **Extras:** none

##### `benchbox.core.cost.models.QueryCost.to_dict`

<span id="benchbox.core.cost.models.QueryCost.to_dict"></span>

```python
def to_dict() -> dict[str, Any]: ...
```

Return `compute_cost`, `currency` and `pricing_details`. The pricing dictionary is reused, not deep-copied.

### PhaseCost

#### `benchbox.core.cost.models.PhaseCost`

<span id="benchbox.core.cost.models.PhaseCost"></span>

```python
class PhaseCost:
    def __init__(
        self,
        phase_name: str,
        total_cost: float,
        query_count: int,
        currency: str = "USD",
        query_costs: list[QueryCost] | None = None,
        wall_clock_duration_seconds: float | None = None,
        concurrent_streams: int | None = None,
    ) -> None: ...
```

Store a phase total, query count and optional individual costs. The duration is in seconds; concurrent streams is a count. Supplying query costs does not recompute or check the supplied total.

**Import:** `from benchbox.core.cost.models import PhaseCost` · **Extras:** none

##### `benchbox.core.cost.models.PhaseCost.to_dict`

<span id="benchbox.core.cost.models.PhaseCost.to_dict"></span>

```python
def to_dict() -> dict[str, Any]: ...
```

Always return `phase_name`, `total_cost`, `query_count` and `currency`. Include each optional field only when non-`None`. Serialize query costs recursively. When duration is positive, also include `effective_cost_per_hour = total_cost / (duration / 3600)`. Zero or negative duration is retained without that derived field.

### BenchmarkCost

#### `benchbox.core.cost.models.BenchmarkCost`

<span id="benchbox.core.cost.models.BenchmarkCost"></span>

```python
class BenchmarkCost:
    def __init__(
        self,
        total_cost: float,
        currency: str = "USD",
        phase_costs: list[PhaseCost] = ...,
        platform_details: dict[str, Any] = ...,
        cost_model: str | None = None,
        warnings: list[str] = ...,
        storage_cost: float | None = None,
    ) -> None: ...
```

Store a run total, phase breakdown and pricing context. The list and dictionary defaults create new containers per instance. `storage_cost` is optional; setting it does not automatically change `total_cost`. `cost_model` and `warnings` carry calculation limitations without themselves enforcing publication availability.

**Import:** `from benchbox.core.cost.models import BenchmarkCost` · **Extras:** none

##### `benchbox.core.cost.models.BenchmarkCost.to_dict`

<span id="benchbox.core.cost.models.BenchmarkCost.to_dict"></span>

```python
def to_dict() -> dict[str, Any]: ...
```

Always return `total_cost`, `currency`, recursively serialized `phase_costs` and `platform_details`. Include `cost_model` and `warnings` only when truthy; include `storage_cost` whenever it is non-`None`, including zero. Platform details and warnings are not deep-copied.

##### `benchbox.core.cost.models.BenchmarkCost.from_phase_costs`

<span id="benchbox.core.cost.models.BenchmarkCost.from_phase_costs"></span>

```python
@classmethod
def from_phase_costs(
    phase_costs: list[PhaseCost],
    platform_details: dict[str, Any] | None = None,
    currency: str = "USD",
) -> BenchmarkCost: ...
```

Sum phase totals and retain the supplied phase list. A truthy platform details dictionary is reused; absent or empty input produces a new empty dictionary. This method does not validate matching phase currencies or add storage cost. Callers must supply phases whose currencies all match the requested `currency`; this precondition is not checked.

### normalized_cost_allows_direct_total

#### `benchbox.core.cost.models.normalized_cost_allows_direct_total`

<span id="benchbox.core.cost.models.normalized_cost_allows_direct_total"></span>

```python
def normalized_cost_allows_direct_total(
    normalized_cost: Mapping[str, Any] | None,
) -> bool: ...
```

Allow `None` for legacy results with no normalized block. Otherwise require a mapping whose status is `"normalized"` or `"not_applicable_local"` and whose `normalized_cost_usd` is non-`None`. This predicate checks availability, not numeric validity or comparability.

**Import:** `from benchbox.core.cost.models import normalized_cost_allows_direct_total` · **Extras:** none

### cost_status_of

#### `benchbox.core.cost.models.cost_status_of`

<span id="benchbox.core.cost.models.cost_status_of"></span>

```python
def cost_status_of(cost_summary: Mapping[str, Any] | None) -> str | None: ...
```

Return the string status from the nested `normalized_cost` mapping, or `None` when either mapping or the string status is absent. Unknown string values are returned unchanged.

**Import:** `from benchbox.core.cost.models import cost_status_of` · **Extras:** none

### published_total_cost

#### `benchbox.core.cost.models.published_total_cost`

<span id="benchbox.core.cost.models.published_total_cost"></span>

```python
def published_total_cost(cost_summary: Mapping[str, Any] | None) -> float | None: ...
```

Return `None` when the summary is not a mapping or its normalized block rejects a direct total. Otherwise return `cost_summary.get("total_cost")` unchanged, including for legacy summaries. This helper does not convert or validate the stored numeric value.

**Import:** `from benchbox.core.cost.models import published_total_cost` · **Extras:** none

### unavailable_cost_warning

#### `benchbox.core.cost.models.unavailable_cost_warning`

<span id="benchbox.core.cost.models.unavailable_cost_warning"></span>

```python
def unavailable_cost_warning(warnings: list[str] | tuple[str, ...] | None) -> str | None: ...
```

Return the first string beginning with the exact, case-sensitive prefix `"normalized cost unavailable"`, or `None`. Non-string entries are ignored. TCO and optimizer consumers use this marker to reject unavailable object-level estimates.

**Import:** `from benchbox.core.cost.models import unavailable_cost_warning` · **Extras:** none

## Storage Cost Estimates

### estimate_storage_cost

#### `benchbox.core.cost.storage.estimate_storage_cost`

<span id="benchbox.core.cost.storage.estimate_storage_cost"></span>

```python
def estimate_storage_cost(
    platform: str,
    total_bytes: int,
    storage_duration_hours: float,
    region: str = "us-east-1",
) -> dict[str, Any]: ...
```

Estimate USD storage spend using the packaged storage price table. Platform lookup lowercases the name. Region lookup strips and lowercases the region before mapping it to a pricing tier. Missing platform prices use `23.00`; a known platform with a missing tier uses its US tier, then `23.00`. Those fallbacks are estimates and carry no availability status.

**Import:** `from benchbox.core.cost.storage import estimate_storage_cost` · **Extras:** none

The formula is `(total_bytes / 1024**4) * price_per_tb_month * (storage_duration_hours / 730)`. `storage_tb` therefore uses a binary tebibyte divisor despite the TB label. Negative inputs are not rejected. Compression, replication, retention features, snapshots and backups are not separately modeled.

##### Returns

`storage_cost` in USD, `storage_tb`, `price_per_tb_month`, `duration_hours` and the platform's packaged `note` or a default estimate note. This calculation does not modify a benchmark cost record.

## Cloud Scan Price Resolution

These functions resolve the per-terabyte scanned-data rates used in cost estimates. Prices come from the packaged pricing tables, so callers should use the returned value rather than assume a fixed rate. Both return a `PriceResolution` with `value`, `table`, `resolved_key`, `fallback_used`, `unit` and `reason`.

For both functions, region inputs are stripped and lowercased before lookup. `resolved_key` is a one-element tuple containing the selected region, including the default region on fallback. `unit` records the table's declared billing unit, or `None` when unspecified. A fallback supplies a human-readable `reason`; listed hits have `reason=None`. Callers must inspect `fallback_used` to distinguish a listed price from a default estimate.

### resolve_athena_price_per_tb

#### `benchbox.core.cost.pricing.resolve_athena_price_per_tb`

<span id="benchbox.core.cost.pricing.resolve_athena_price_per_tb"></span>

```python
def resolve_athena_price_per_tb(region: str = "") -> PriceResolution: ...
```

Resolve the Athena scanned-data rate for an AWS region, using the `athena_price_per_tb` table. A listed region returns its own rate. An omitted or unlisted region returns the `us-east-1` rate and sets `fallback_used=True`.

**Import:** `from benchbox.core.cost.pricing import resolve_athena_price_per_tb` · **Extras:** none

### resolve_synapse_serverless_price_per_tb

#### `benchbox.core.cost.pricing.resolve_synapse_serverless_price_per_tb`

<span id="benchbox.core.cost.pricing.resolve_synapse_serverless_price_per_tb"></span>

```python
def resolve_synapse_serverless_price_per_tb(region: str = "") -> PriceResolution: ...
```

Resolve the Azure Synapse Serverless SQL Pool scanned-data rate for an Azure region, using the `synapse_serverless_price_per_tb` table. A listed region returns its own rate. An omitted or unlisted region returns the `eastus` rate and sets `fallback_used=True`.

**Import:** `from benchbox.core.cost.pricing import resolve_synapse_serverless_price_per_tb` · **Extras:** none

## Price and Quantity Resolution

Pricing lookups use packaged tables rather than live vendor catalogs. Every price and billing-quantity resolver returns `PriceResolution`; consumers must inspect fallback status before publishing amounts. A selected bucket is a table policy, not independent proof of a location's current vendor rate.

The packaged compute estimates exclude enterprise/reserved/commitment discounts, storage and network/data-transfer charges. In `pricing_data.yaml`, each price table must carry provenance keys `source`, `retrieved`, `upstream_published`, `method` and `verified_regions`; the latter is a list. Each byte-priced table must also declare its billing unit. These schema requirements preserve provenance and divisor checks, not price certification.

### PriceResolution

#### `benchbox.core.cost.pricing.PriceResolution`

<span id="benchbox.core.cost.pricing.PriceResolution"></span>

```python
class PriceResolution:
    def __init__(
        self,
        value: float | int | None,
        table: str,
        resolved_key: tuple[str, ...],
        fallback_used: bool,
        unit: str | None = None,
        reason: str | None = None,
    ) -> None: ...
```

Frozen lookup record for both prices and quantities. `value` can be absent; `resolved_key` is a resolver-supplied lookup identity. Some fallbacks retain requested normalized labels rather than an existing table cell. Inspect `fallback_used`, `reason` and `table` together; the key alone does not prove a stored cell supplied the amount. Scalar tables use an empty tuple. A designed priced bucket such as a table's `other` tier need not be marked fallback; a guessed default is marked. `reason` explains fallback and `unit` records an applicable billing unit. Construction does not validate these relationships.

**Import:** `from benchbox.core.cost.pricing import PriceResolution` · **Extras:** none

### get_table_provenance

#### `benchbox.core.cost.pricing.get_table_provenance`

<span id="benchbox.core.cost.pricing.get_table_provenance"></span>

```python
def get_table_provenance(table: str) -> dict[str, Any] | None: ...
```

Return a shallow copy of dictionary provenance for the exact table name, or `None` when absent. Nested values are not deep-copied.

**Import:** `from benchbox.core.cost.pricing import get_table_provenance` · **Extras:** none

### get_table_unit

#### `benchbox.core.cost.pricing.get_table_unit`

<span id="benchbox.core.cost.pricing.get_table_unit"></span>

```python
def get_table_unit(table: str) -> str | None: ...
```

Return a table's declared string unit or `None`. Byte units distinguish `tebibyte` from `terabyte`; absence does not infer either divisor.

**Import:** `from benchbox.core.cost.pricing import get_table_unit` · **Extras:** none

### resolve_snowflake_credit_price

#### `benchbox.core.cost.pricing.resolve_snowflake_credit_price`

<span id="benchbox.core.cost.pricing.resolve_snowflake_credit_price"></span>

```python
def resolve_snowflake_credit_price(
    edition: str,
    cloud: str,
    region: str,
) -> PriceResolution: ...
```

Resolve `snowflake_credit_prices` by normalized edition/cloud and mapped region tier. Edition accepts hyphens/spaces as underscores. Missing cells use standard/aws/us pricing and mark fallback; if that cell is absent the implementation estimate is `2.00`. A designed `other` tier is not fallback merely because the region mapped there.

**Import:** `from benchbox.core.cost.pricing import resolve_snowflake_credit_price` · **Extras:** none

### resolve_bigquery_price_per_tb

#### `benchbox.core.cost.pricing.resolve_bigquery_price_per_tb`

<span id="benchbox.core.cost.pricing.resolve_bigquery_price_per_tb"></span>

```python
def resolve_bigquery_price_per_tb(location: str) -> PriceResolution: ...
```

Strip/lowercase the location. Multi-region labels, captured exact locations and recognized continental branches resolve to packaged cells. An exact non-`other` cell takes precedence over continental branches. The unmatched `other` bucket is a guessed rate and marks fallback. The table's declared unit accompanies the result; despite this function's name, the packaged BigQuery byte divisor is a tebibyte.

**Import:** `from benchbox.core.cost.pricing import resolve_bigquery_price_per_tb` · **Extras:** none

### resolve_redshift_node_price

#### `benchbox.core.cost.pricing.resolve_redshift_node_price`

<span id="benchbox.core.cost.pricing.resolve_redshift_node_price"></span>

```python
def resolve_redshift_node_price(node_type: str, region: str) -> PriceResolution: ...
```

Strip/lowercase inputs and resolve `redshift_node_prices`. A known node with no exact region uses its `other` bucket without marking fallback. An unknown node returns the packaged implementation's default node-hour estimate `1.00` with fallback marked. These results estimate USD per node-hour.

**Import:** `from benchbox.core.cost.pricing import resolve_redshift_node_price` · **Extras:** none

### resolve_databricks_dbu_price

#### `benchbox.core.cost.pricing.resolve_databricks_dbu_price`

<span id="benchbox.core.cost.pricing.resolve_databricks_dbu_price"></span>

```python
def resolve_databricks_dbu_price(
    cloud: str,
    tier: str,
    workload_type: str,
) -> PriceResolution: ...
```

Normalize inputs and resolve `databricks_dbu_prices`. Workload hyphens and spaces become underscores; `serverless_sql` maps to `sql_serverless` and `sql_compute` to `sql_pro`. Missing cells use aws/premium/all_purpose with fallback marked; if that default cell is absent the implementation estimate is `0.55`. The result is a DBU price, not a warehouse size rate.

**Import:** `from benchbox.core.cost.pricing import resolve_databricks_dbu_price` · **Extras:** none

### resolve_databricks_warehouse_dbu_per_hour

#### `benchbox.core.cost.pricing.resolve_databricks_warehouse_dbu_per_hour`

<span id="benchbox.core.cost.pricing.resolve_databricks_warehouse_dbu_per_hour"></span>

```python
def resolve_databricks_warehouse_dbu_per_hour(warehouse_size: str) -> PriceResolution: ...
```

Strip and case-match warehouse labels. Return quantity unit `DBU/hour`; unknown sizes use a conservative `2.0` estimate and mark fallback so the amount cannot support normalized publication.

**Import:** `from benchbox.core.cost.pricing import resolve_databricks_warehouse_dbu_per_hour` · **Extras:** none

### resolve_snowflake_warehouse_credits_per_hour

#### `benchbox.core.cost.pricing.resolve_snowflake_warehouse_credits_per_hour`

<span id="benchbox.core.cost.pricing.resolve_snowflake_warehouse_credits_per_hour"></span>

```python
def resolve_snowflake_warehouse_credits_per_hour(warehouse_size: str) -> PriceResolution: ...
```

Normalize case and size-label separators. Return quantity unit `credits/hour`; unknown labels use the Medium `4.0` estimate with fallback marked. The selected size spelling is retained in known keys.

**Import:** `from benchbox.core.cost.pricing import resolve_snowflake_warehouse_credits_per_hour` · **Extras:** none

### resolve_synapse_dedicated_price

#### `benchbox.core.cost.pricing.resolve_synapse_dedicated_price`

<span id="benchbox.core.cost.pricing.resolve_synapse_dedicated_price"></span>

```python
def resolve_synapse_dedicated_price(dwu_level: str, region: str) -> PriceResolution: ...
```

Strip/lowercase the DWU level and map the region tier. A known level with no tier cell uses its US cell and marks fallback; an unknown level uses DW100c US pricing with fallback marked. Missing US default cells use the implementation estimate `1.20`. The amount is the hourly price for the selected pool level, not a price to multiply by the DWU count.

**Import:** `from benchbox.core.cost.pricing import resolve_synapse_dedicated_price` · **Extras:** none

### resolve_fabric_cu_price

#### `benchbox.core.cost.pricing.resolve_fabric_cu_price`

<span id="benchbox.core.cost.pricing.resolve_fabric_cu_price"></span>

```python
def resolve_fabric_cu_price(region: str) -> PriceResolution: ...
```

Map the region to a packaged capacity-unit hourly price. A designed `other` tier is returned without fallback marking, including when used after no direct tier cell exists.

**Import:** `from benchbox.core.cost.pricing import resolve_fabric_cu_price` · **Extras:** none

### resolve_fabric_sku_cu_count

#### `benchbox.core.cost.pricing.resolve_fabric_sku_cu_count`

<span id="benchbox.core.cost.pricing.resolve_fabric_sku_cu_count"></span>

```python
def resolve_fabric_sku_cu_count(sku: str) -> PriceResolution: ...
```

Strip/lowercase a SKU and return quantity unit `CU`. Unknown SKUs use the F2 quantity `2` with fallback marked. This is a quantity, not a price.

**Import:** `from benchbox.core.cost.pricing import resolve_fabric_sku_cu_count` · **Extras:** none

### resolve_firebolt_fbu_rate

#### `benchbox.core.cost.pricing.resolve_firebolt_fbu_rate`

<span id="benchbox.core.cost.pricing.resolve_firebolt_fbu_rate"></span>

```python
def resolve_firebolt_fbu_rate(node_type: str) -> PriceResolution: ...
```

Strip/lowercase the node label and return quantity unit `FBU/hour`. Unknown nodes use the packaged M rate and mark fallback.

**Import:** `from benchbox.core.cost.pricing import resolve_firebolt_fbu_rate` · **Extras:** none

### resolve_firebolt_fbu_price

#### `benchbox.core.cost.pricing.resolve_firebolt_fbu_price`

<span id="benchbox.core.cost.pricing.resolve_firebolt_fbu_price"></span>

```python
def resolve_firebolt_fbu_price() -> PriceResolution: ...
```

Return the packaged scalar `firebolt_fbu_price` with an empty resolved key and no fallback. Its presence does not establish provenance freshness.

**Import:** `from benchbox.core.cost.pricing import resolve_firebolt_fbu_price` · **Extras:** none

### get_pricing_age_days

#### `benchbox.core.cost.pricing.get_pricing_age_days`

<span id="benchbox.core.cost.pricing.get_pricing_age_days"></span>

```python
def get_pricing_age_days(table: str | None = None) -> int | None: ...
```

For a table, parse its provenance retrieval date and return calendar days since that date, using local `date.today()`. Missing, unknown or malformed dates return `None`. Without a table use the file-level validation date; absence also returns `None`. Future dates yield negative ages.

**Import:** `from benchbox.core.cost.pricing import get_pricing_age_days` · **Extras:** none

### is_pricing_stale

#### `benchbox.core.cost.pricing.is_pricing_stale`

<span id="benchbox.core.cost.pricing.is_pricing_stale"></span>

```python
def is_pricing_stale(threshold_days: int = 90) -> bool: ...
```

Test whether the file-level age is strictly greater than the threshold. Unknown file-level age returns `False`; callers needing publication assurance must check each relevant table's provenance separately.

**Import:** `from benchbox.core.cost.pricing import is_pricing_stale` · **Extras:** none

## Cost Projections

These projections extrapolate supplied costs. They do not forecast vendor prices, validate invoices or automatically include idle infrastructure costs. Rates are fractions: `0.1` growth means 10%, and `0.2` discount means 20%.

### GrowthModel

#### `benchbox.core.cost.tco.GrowthModel`

<span id="benchbox.core.cost.tco.GrowthModel"></span>

Enum members `NONE="none"`, `LINEAR="linear"` and `COMPOUND="compound"` select flat, additive or compounded usage growth.

**Import:** `from benchbox.core.cost.tco import GrowthModel` · **Extras:** none

### DiscountType

#### `benchbox.core.cost.tco.DiscountType`

<span id="benchbox.core.cost.tco.DiscountType"></span>

Enum members `NONE="none"`, `RESERVED="reserved"`, `COMMITTED_USE="committed_use"`, `ENTERPRISE="enterprise"` and `VOLUME="volume"` label the discount configuration.

**Import:** `from benchbox.core.cost.tco import DiscountType` · **Extras:** none

### GrowthConfig

#### `benchbox.core.cost.tco.GrowthConfig`

<span id="benchbox.core.cost.tco.GrowthConfig"></span>

```python
class GrowthConfig:
    def __init__(
        self,
        model: GrowthModel = GrowthModel.NONE,
        annual_rate: float = 0.0,
        data_growth_rate: float | None = None,
    ) -> None: ...
```

Store rates without range validation. The separate data rate is metadata; the calculator's cost multiplier uses `annual_rate`.

**Import:** `from benchbox.core.cost.tco import GrowthConfig` · **Extras:** none

##### `benchbox.core.cost.tco.GrowthConfig.get_data_growth_rate`

<span id="benchbox.core.cost.tco.GrowthConfig.get_data_growth_rate"></span>

```python
def get_data_growth_rate() -> float: ...
```

Return the separate data rate or `annual_rate` when it is `None`.

##### `benchbox.core.cost.tco.GrowthConfig.calculate_multiplier`

<span id="benchbox.core.cost.tco.GrowthConfig.calculate_multiplier"></span>

```python
def calculate_multiplier(year: int) -> float: ...
```

Year numbers are one-based. The first year, earlier values and no-growth mode return one. Later linear growth is additive and compound growth multiplicative from the first-year base. Unknown model values return one.

### DiscountConfig

#### `benchbox.core.cost.tco.DiscountConfig`

<span id="benchbox.core.cost.tco.DiscountConfig"></span>

```python
class DiscountConfig:
    def __init__(
        self,
        discount_type: DiscountType = DiscountType.NONE,
        discount_percent: float = 0.0,
        commitment_years: int = 1,
        effective_start_year: int = 1,
    ) -> None: ...
```

Store the discount fraction and commitment metadata without range checks. `commitment_years` does not limit the years receiving a discount.

**Import:** `from benchbox.core.cost.tco import DiscountConfig` · **Extras:** none

##### `benchbox.core.cost.tco.DiscountConfig.get_discount_multiplier`

<span id="benchbox.core.cost.tco.DiscountConfig.get_discount_multiplier"></span>

```python
def get_discount_multiplier(year: int) -> float: ...
```

Return one for no discount or before its start year; otherwise return `1 - discount_percent`. The discount persists in later years.

### BudgetThreshold

#### `benchbox.core.cost.tco.BudgetThreshold`

<span id="benchbox.core.cost.tco.BudgetThreshold"></span>

```python
class BudgetThreshold:
    def __init__(self, name: str, amount: float, period: str = "annual") -> None: ...
```

Amount uses the projected currency. Period labels are `monthly`, `annual` and `total`; construction does not validate them.

**Import:** `from benchbox.core.cost.tco import BudgetThreshold` · **Extras:** none

##### `benchbox.core.cost.tco.BudgetThreshold.is_exceeded`

<span id="benchbox.core.cost.tco.BudgetThreshold.is_exceeded"></span>

```python
def is_exceeded(cost: float, period: str) -> bool: ...
```

Compare strictly greater than the threshold. Convert only monthly to annual or annual to monthly when labels differ; other mismatches are compared without conversion.

### BudgetAlert

#### `benchbox.core.cost.tco.BudgetAlert`

<span id="benchbox.core.cost.tco.BudgetAlert"></span>

```python
class BudgetAlert:
    def __init__(
        self,
        threshold: BudgetThreshold,
        actual_cost: float,
        year: int,
        period: str,
        message: str,
    ) -> None: ...
```

Store the triggered threshold, projected amount, one-based year, period and message. The amount is a projection, despite the `actual_cost` name.

**Import:** `from benchbox.core.cost.tco import BudgetAlert` · **Extras:** none

### YearlyProjection

#### `benchbox.core.cost.tco.YearlyProjection`

<span id="benchbox.core.cost.tco.YearlyProjection"></span>

```python
class YearlyProjection:
    def __init__(
        self,
        year: int,
        calendar_year: int,
        base_cost: float,
        growth_multiplier: float,
        discount_multiplier: float,
        projected_cost: float,
        cumulative_cost: float,
        monthly_cost: float,
    ) -> None: ...
```

Store one-based and calendar years, monetary amounts and applied multipliers.

**Import:** `from benchbox.core.cost.tco import YearlyProjection` · **Extras:** none

##### `benchbox.core.cost.tco.YearlyProjection.to_dict`

<span id="benchbox.core.cost.tco.YearlyProjection.to_dict"></span>

```python
def to_dict() -> dict[str, Any]: ...
```

Return all fields; round amounts to two decimals and multipliers to four.

### TCOProjection

#### `benchbox.core.cost.tco.TCOProjection`

<span id="benchbox.core.cost.tco.TCOProjection"></span>

```python
class TCOProjection:
    def __init__(
        self,
        platform: str,
        base_annual_cost: float,
        currency: str = "USD",
        projection_years: int = 5,
        start_year: int = ...,
        growth_config: GrowthConfig = ...,
        discount_config: DiscountConfig = ...,
        yearly_projections: list[YearlyProjection] = ...,
        total_tco: float = 0.0,
        average_annual_cost: float = 0.0,
        budget_alerts: list[BudgetAlert] = ...,
        metadata: dict[str, Any] = ...,
    ) -> None: ...
```

Default start year is the local current year. Configurations and containers get new defaults per instance. Construction does not derive totals.

**Import:** `from benchbox.core.cost.tco import TCOProjection` · **Extras:** none

##### `benchbox.core.cost.tco.TCOProjection.to_dict`

<span id="benchbox.core.cost.tco.TCOProjection.to_dict"></span>

```python
def to_dict() -> dict[str, Any]: ...
```

Serialize projections and enum values, round monetary summary fields to two decimals and retain metadata by reference. Discount serialization omits `effective_start_year`. Alert serialization uses threshold name and amount and omits the alert/threshold period; it is not a lossless reconstruction of every constructor field.

### TCOCalculator

#### `benchbox.core.cost.tco.TCOCalculator`

<span id="benchbox.core.cost.tco.TCOCalculator"></span>

Construct without arguments and with no budget thresholds.

**Import:** `from benchbox.core.cost.tco import TCOCalculator` · **Extras:** none

##### `benchbox.core.cost.tco.TCOCalculator.add_budget_threshold`

<span id="benchbox.core.cost.tco.TCOCalculator.add_budget_threshold"></span>

```python
def add_budget_threshold(threshold: BudgetThreshold) -> None: ...
```

Append and retain the supplied threshold; repeated additions are allowed.

##### `benchbox.core.cost.tco.TCOCalculator.clear_budget_thresholds`

<span id="benchbox.core.cost.tco.TCOCalculator.clear_budget_thresholds"></span>

```python
def clear_budget_thresholds() -> None: ...
```

Remove every registered threshold.

##### `benchbox.core.cost.tco.TCOCalculator.calculate_tco`

<span id="benchbox.core.cost.tco.TCOCalculator.calculate_tco"></span>

```python
def calculate_tco(
    benchmark_cost: BenchmarkCost,
    annual_runs: int = 1,
    projection_years: int = 5,
    growth_config: GrowthConfig | None = None,
    discount_config: DiscountConfig | None = None,
    platform: str | None = None,
    start_year: int | None = None,
) -> TCOProjection: ...
```

Multiply per-run cost by annual runs and apply configured yearly growth and discounts. Preserve benchmark currency. Absent platform uses `platform_details["platform"]` or `"unknown"`; absent or zero start year uses the local current year. Costs and rates are not range-checked. Supply a positive integer horizon: zero raises `ZeroDivisionError`. Horizons are not restricted to the example values one, three and five.

Each registered non-total threshold yields at most one alert, at its first exceeded year; total thresholds check the complete projected sum. A warning beginning `"normalized cost unavailable"` rejects the input with `ValueError`. This gate checks the object warning marker, not a separate normalized result block. No cost regeneration occurs.

##### `benchbox.core.cost.tco.TCOCalculator.calculate_tco_from_annual_cost`

<span id="benchbox.core.cost.tco.TCOCalculator.calculate_tco_from_annual_cost"></span>

```python
def calculate_tco_from_annual_cost(
    annual_cost: float,
    platform: str,
    projection_years: int = 5,
    growth_config: GrowthConfig | None = None,
    discount_config: DiscountConfig | None = None,
    currency: str = "USD",
    start_year: int | None = None,
) -> TCOProjection: ...
```

Wrap the supplied annual amount as a benchmark cost and calculate with one annual run. Other projection and alert rules remain the same.

##### `benchbox.core.cost.tco.TCOCalculator.compare_platforms`

<span id="benchbox.core.cost.tco.TCOCalculator.compare_platforms"></span>

```python
def compare_platforms(projections: list[TCOProjection]) -> dict[str, Any]: ...
```

Return an error dictionary for empty input; otherwise rank ascending TCO and report savings against the largest total. Monetary values round to two decimals, savings percentages to one. Zero/negative largest totals produce zero savings percentages. Currency and horizon come from the cheapest projection without checking all inputs agree; callers must supply comparable currencies and horizons. No input ordering is changed.

### create_standard_tco_scenarios

#### `benchbox.core.cost.tco.create_standard_tco_scenarios`

<span id="benchbox.core.cost.tco.create_standard_tco_scenarios"></span>

```python
def create_standard_tco_scenarios(
    benchmark_cost: BenchmarkCost,
    annual_runs: int = 12,
) -> dict[str, TCOProjection]: ...
```

Return three five-year scenarios: conservative has no growth/discount; moderate has 10% compound growth and 15% reserved discount with three-year commitment metadata; aggressive has 25% compound growth and no discount. These are fixed assumptions, not recommendations or predicted vendor rates.

**Import:** `from benchbox.core.cost.tco import create_standard_tco_scenarios` · **Extras:** none

## Optimization Records and Analysis

Optimization estimates are rule outputs, not guaranteed savings. Recommendations can overlap; the reported sum does not deduplicate competing changes.

### OptimizationCategory

#### `benchbox.core.cost.optimizer.OptimizationCategory`

<span id="benchbox.core.cost.optimizer.OptimizationCategory"></span>

Members `PLATFORM_TIER="platform_tier"`, `REGION="region"`, `RESOURCE_SIZING="resource_sizing"`, `PRICING_MODEL="pricing_model"`, `QUERY="query"` and `DATA_MANAGEMENT="data_management"` label opportunities.

**Import:** `from benchbox.core.cost.optimizer import OptimizationCategory` · **Extras:** none

### ConfidenceLevel

#### `benchbox.core.cost.optimizer.ConfidenceLevel`

<span id="benchbox.core.cost.optimizer.ConfidenceLevel"></span>

`HIGH="high"` labels pricing-based estimates, `MEDIUM="medium"` typical patterns and `LOW="low"` rough estimates. These are labels, not statistical confidence intervals or independent price certification.

**Import:** `from benchbox.core.cost.optimizer import ConfidenceLevel` · **Extras:** none

### ImplementationEffort

#### `benchbox.core.cost.optimizer.ImplementationEffort`

<span id="benchbox.core.cost.optimizer.ImplementationEffort"></span>

`TRIVIAL="trivial"`, `LOW="low"`, `MEDIUM="medium"` and `HIGH="high"` label rough minutes, hours, days and weeks of effort.

**Import:** `from benchbox.core.cost.optimizer import ImplementationEffort` · **Extras:** none

### SavingsEstimate

#### `benchbox.core.cost.optimizer.SavingsEstimate`

<span id="benchbox.core.cost.optimizer.SavingsEstimate"></span>

```python
class SavingsEstimate:
    def __init__(
        self,
        amount: float,
        currency: str = "USD",
        period: str = "annual",
        confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM,
        range_low: float | None = None,
        range_high: float | None = None,
        percentage: float | None = None,
    ) -> None: ...
```

Store estimated savings per period and optional monetary range/percentage. Construction does not validate ranges or convert currencies.

**Import:** `from benchbox.core.cost.optimizer import SavingsEstimate` · **Extras:** none

##### `benchbox.core.cost.optimizer.SavingsEstimate.to_dict`

<span id="benchbox.core.cost.optimizer.SavingsEstimate.to_dict"></span>

```python
def to_dict() -> dict[str, Any]: ...
```

Include amount, currency, period and confidence value. Include optional fields only when non-`None`; round amounts/ranges to two decimals and percentage to one.

### ImplementationGuide

#### `benchbox.core.cost.optimizer.ImplementationGuide`

<span id="benchbox.core.cost.optimizer.ImplementationGuide"></span>

```python
class ImplementationGuide:
    def __init__(
        self,
        steps: list[str],
        prerequisites: list[str] = ...,
        risks: list[str] = ...,
        rollback: str | None = None,
        estimated_time: str | None = None,
    ) -> None: ...
```

Store actions, requirements, risks and optional rollback/time guidance. Default prerequisite and risk lists are new per instance.

**Import:** `from benchbox.core.cost.optimizer import ImplementationGuide` · **Extras:** none

##### `benchbox.core.cost.optimizer.ImplementationGuide.to_dict`

<span id="benchbox.core.cost.optimizer.ImplementationGuide.to_dict"></span>

```python
def to_dict() -> dict[str, Any]: ...
```

Always include steps; include other fields only when truthy. Lists are reused, not deep-copied.

### Recommendation

#### `benchbox.core.cost.optimizer.Recommendation`

<span id="benchbox.core.cost.optimizer.Recommendation"></span>

```python
class Recommendation:
    def __init__(
        self,
        id: str,
        title: str,
        description: str,
        category: OptimizationCategory,
        savings: SavingsEstimate,
        effort: ImplementationEffort,
        guide: ImplementationGuide,
        priority: int = 50,
        platform: str | None = None,
        current_config: dict[str, Any] | None = None,
        recommended_config: dict[str, Any] | None = None,
        metadata: dict[str, Any] = ...,
    ) -> None: ...
```

Store a recommendation and configurations. Higher priority sorts first; the nominal 1–100 score is not clamped. IDs are not checked for uniqueness.

**Import:** `from benchbox.core.cost.optimizer import Recommendation` · **Extras:** none

##### `benchbox.core.cost.optimizer.Recommendation.to_dict`

<span id="benchbox.core.cost.optimizer.Recommendation.to_dict"></span>

```python
def to_dict() -> dict[str, Any]: ...
```

Serialize enum values, savings and guide. Optional platform/configuration and metadata fields appear only when truthy; configuration dictionaries and metadata are reused rather than deep-copied.

### OptimizationReport

#### `benchbox.core.cost.optimizer.OptimizationReport`

<span id="benchbox.core.cost.optimizer.OptimizationReport"></span>

```python
class OptimizationReport:
    def __init__(
        self,
        recommendations: list[Recommendation] = ...,
        total_potential_savings: float = 0.0,
        currency: str = "USD",
        platform: str | None = None,
        analysis_date: str = ...,
        benchmark_cost: BenchmarkCost | None = None,
        metadata: dict[str, Any] = ...,
    ) -> None: ...
```

New containers are created per instance. Default analysis date is a local, timezone-naive ISO timestamp. Direct construction does not sort or sum.

**Import:** `from benchbox.core.cost.optimizer import OptimizationReport` · **Extras:** none

##### `benchbox.core.cost.optimizer.OptimizationReport.to_dict`

<span id="benchbox.core.cost.optimizer.OptimizationReport.to_dict"></span>

```python
def to_dict() -> dict[str, Any]: ...
```

Serialize recommendations and count, round total savings to two decimals and include currency, platform, date and metadata. The original benchmark cost is omitted; metadata is reused.

##### `benchbox.core.cost.optimizer.OptimizationReport.get_by_category`

<span id="benchbox.core.cost.optimizer.OptimizationReport.get_by_category"></span>

```python
def get_by_category(category: OptimizationCategory) -> list[Recommendation]: ...
```

Return matching recommendations in existing order.

##### `benchbox.core.cost.optimizer.OptimizationReport.get_quick_wins`

<span id="benchbox.core.cost.optimizer.OptimizationReport.get_quick_wins"></span>

```python
def get_quick_wins(
    max_effort: ImplementationEffort = ImplementationEffort.LOW,
) -> list[Recommendation]: ...
```

Include effort levels through the supplied maximum and sort descending by savings amount. Invalid effort values raise `ValueError`.

### CostOptimizer

#### `benchbox.core.cost.optimizer.CostOptimizer`

<span id="benchbox.core.cost.optimizer.CostOptimizer"></span>

Construct without arguments and register the built-in rules.

**Import:** `from benchbox.core.cost.optimizer import CostOptimizer` · **Extras:** none

##### `benchbox.core.cost.optimizer.CostOptimizer.analyze`

<span id="benchbox.core.cost.optimizer.CostOptimizer.analyze"></span>

```python
def analyze(
    benchmark_cost: BenchmarkCost,
    platform_config: dict[str, Any] | None = None,
    annual_runs: int = 12,
) -> OptimizationReport: ...
```

`annual_runs` is expected benchmark executions per year; yearly estimates scale the supplied single-run cost by this count. Resolve `platform_config["platform"]`, then `benchmark_cost.platform_details["platform"]`, then `"unknown"`. Supply actual deployment settings: built-in rules read these fields with rule-specific missing-key defaults:

- Snowflake edition changes read `edition` (`""`), `cloud` (`"aws"`) and `region` (`"us-east-1"`); only enterprise/business-critical editions qualify. Region changes read `region` (`""`), `cloud` (`"aws"`) and `edition` (`"standard"`).
- Databricks tier changes read `tier` (`""`), `cloud` (`"aws"`) and `workload_type` (`"sql_warehouse"`); only enterprise/premium tiers qualify. Workload changes read `workload_type` (`""`), `cloud` (`"aws"`) and `tier` (`"premium"`); only `"all_purpose"` qualifies.
- BigQuery region changes read `location` (`""`). Data-scan estimation instead defaults `location` to `"us"`. The latter reads bytes from `benchmark_cost.platform_details["pricing_details"]` under `"total_bytes_processed"`; missing/zero bytes are estimated from total cost and resolved price using `1024**4` bytes per billing TB.
- Redshift region changes read `region` (`""`), `node_type` (`"dc2.large"`) and `node_count` (1). Node migration reads `node_type` (`""`), `region` (`"us-east-1"`) and `node_count` (1); only `ds2`-prefixed types qualify.

Evaluate built-in rules. Sort recommendations by descending priority and sum their savings amounts without currency conversion or overlap adjustment. Preserve report currency from the benchmark; individual savings records retain their own currency labels.

An unavailable-cost warning suppresses every rule and returns no recommendations. Fallback/absent pricing suppresses affected rules; `metadata["suppressed_rules"]` records rule and reason. Other rule exceptions are skipped without a suppression entry. Metadata also records annual runs, rules evaluated and recommendations generated. No platform configuration or benchmark cost is changed.

<span id="not-part-of-public-contract"></span>

## Not part of the public contract

`ExecutionPhases` and its attributes `setup`, `power_test`, `throughput_test`, `maintenance_test` and `migration` are internal names that may change without notice, so do not build on them.

<span id="benchbox.core.results.models.ExecutionPhases"></span><span id="benchbox.core.results.models.ExecutionPhases.__init__"></span><span id="benchbox.core.results.models.ExecutionPhases.setup"></span><span id="benchbox.core.results.models.ExecutionPhases.power_test"></span><span id="benchbox.core.results.models.ExecutionPhases.throughput_test"></span><span id="benchbox.core.results.models.ExecutionPhases.maintenance_test"></span><span id="benchbox.core.results.models.ExecutionPhases.migration"></span>

## See Also

### Conceptual Documentation

- {doc}`/concepts/data-model` - Complete data model reference
- {doc}`/concepts/architecture` - How results fit into BenchBox
- {doc}`/concepts/workflow` - Result collection in workflows

### API Reference

- {doc}`base` - Base benchmark interface
- {doc}`index` - Python API overview
- {doc}`/reference/api-reference` - High-level API guide

### Guides

- {doc}`/advanced/performance` - Performance monitoring
- {doc}`/guides/tpc/tpc-validation-guide` - TPC compliance validation
- {doc}`/usage/examples` - Result analysis examples

### External Resources

- {doc}`/reference/result-schema-v1` - JSON schema specification
