<!-- markdownlint-disable MD024 -->

# Configuration, Registry and Lifecycle API

```{tags} reference, python-api
```

Contract reference for the configuration models, the platform registry, the benchmark lifecycle runner, the system profiler and the TPC result validator in `benchbox.core`. None of these symbols needs an optional extra. Examples that run a benchmark use the `duckdb` extra for the adapter.

## `benchbox.core.config.BenchmarkConfig`

<span id="benchbox.core.config.BenchmarkConfig"></span>

Describes one benchmark run: which benchmark, at what scale factor, with which queries and run options. It is a Pydantic model that validates its values when it is created.

**Import:** `from benchbox.core.config import BenchmarkConfig` · **Extras:** none

### Parameters

All parameters are keyword-only. Positional arguments raise `TypeError`.

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `name` | `str` | required | The benchmark identifier, such as `"tpch"`. `run_benchmark_lifecycle` selects the benchmark by this name. |
| `display_name` | `str` | required | The human-readable name, such as `"TPC-H"`. |
| `scale_factor` | `float` | `0.01` | Data size multiplier. Must be greater than 0 and at most 100000. |
| `queries` | `list[str]` or `None` | `None` | Query identifiers to run. `None` runs the benchmark's full query set. |
| `concurrency` | `int` or `None` | `None` | Number of concurrent throughput streams. `None` means not set and runs the default of 2 streams. Must be at least 1, and at least 2 when the run includes the throughput phase. |
| `capture_plans` | `bool` | `False` | Add `query_plan`, `plan_fingerprint` and `plan_capture_time_ms` to each query result when the lifecycle runs. |
| `analyze_plans` | `bool` or `None` | `None` | Plan-capture detail. `None` leaves the choice to the platform adapter. |
| `strict_plan_capture` | `bool` | `False` | Strict plan-capture mode. |
| `stats_reset` | `bool` or `None` | `None` | Reset control for the statistics phase. `None` keeps the default. |
| `stats_per_table_timing` | `bool` | `False` | Record per-table timing for the statistics phase. |
| `options` | `dict[str, Any]` | `{}` | Free-form options. The lifecycle reads `table_mode` from it (default `"native"`). |
| `compress_data` | `bool` | `False` | Compress generated data. |
| `compression_type` | `str` | `"zstd"` | One of `zstd`, `gzip`, `lz4`, `snappy` or `none`. |
| `compression_level` | `int` or `None` | `None` | Compression level from 1 to 22, or `None` for the codec default. |
| `test_execution_type` | `str` | `"standard"` | One of `standard`, `power`, `throughput`, `maintenance`, `combined`, `data_only` or `load_only`. |
| `official` | `bool` | `False` | Request TPC-compliant mode. |
| `client_region` | `str` or `None` | `None` | Region of the machine that runs the client, recorded as a locality disclosure. |
| `client_cloud` | `str` or `None` | `None` | Cloud provider of the machine that runs the client, recorded as a locality disclosure. |
| `link_probe` | `bool` | `True` | Flag for the client-link locality disclosure; on by default. |

### Returns

A `BenchmarkConfig` instance. Fields can be changed after creation, and assignments are not validated: `config.scale_factor = -1` is accepted.

### Raises

`pydantic.ValidationError` (a subclass of `ValueError`) when a value fails validation. A missing `name` or `display_name`, a `scale_factor` of 0 or less or above 100000, a `concurrency` below 1, a `concurrency` of 1 with `test_execution_type` `throughput` (or `combined` that includes throughput), a `compression_type` or `test_execution_type` outside the lists above, and a `compression_level` outside 1 to 22 each raise it. The message names the field, for example `concurrency must be at least 1, got: 0`.

### Example

```python
from pydantic import ValidationError

from benchbox.core.config import BenchmarkConfig

benchmark = BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=0.1, queries=["1", "6"])
print(benchmark.scale_factor, benchmark.test_execution_type)

try:
    BenchmarkConfig(name="tpch", display_name="TPC-H", concurrency=0)
except ValidationError as exc:
    print(exc.errors()[0]["msg"])
print(BenchmarkConfig(name="tpch", display_name="TPC-H", unknown_field=1).model_dump().get("unknown_field"))
```

Output on 0.4.1:

```text
0.1 standard
Value error, concurrency must be at least 1, got: 0
None
```

### Compatibility

`benchbox.core.schemas.BenchmarkConfig` is the same object. Unknown keyword arguments are ignored, not rejected.

## `benchbox.core.config.DatabaseConfig`

<span id="benchbox.core.config.DatabaseConfig"></span>

Describes the target database of a run: its platform type, name and connection settings. Fields that are not declared are kept as extra, platform-specific settings.

**Import:** `from benchbox.core.config import DatabaseConfig` · **Extras:** none

### Parameters

All parameters are keyword-only.

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `type` | `str` | required | The platform type, such as `"duckdb"`. Surrounding spaces are removed. Must not be empty. A platform with deployment modes also accepts the `--platform` selector `"<platform>:<mode>"`, such as `"timescaledb:cloud"`; see [Deployment modes](../../platforms/deployment-modes.md). |
| `name` | `str` | required | The database name. Surrounding spaces are removed. Must not be empty. |
| `connection_string` | `str` or `None` | `None` | Connection string or path. |
| `options` | `dict[str, Any]` | `{}` | Free-form options. |
| `driver_package` | `str` or `None` | `None` | PyPI name of the driver package, such as `"duckdb"`. |
| `driver_version` | `str` or `None` | `None` | Requested driver version. |
| `driver_version_resolved` | `str` or `None` | `None` | Driver version the request resolved to. |
| `driver_version_actual` | `str` or `None` | `None` | Driver version that is installed and running. |
| `driver_runtime_strategy` | `str` or `None` | `None` | How the driver runtime was provided. |
| `driver_runtime_path` | `str` or `None` | `None` | Path of the driver runtime. |
| `driver_runtime_python_executable` | `str` or `None` | `None` | Python executable of the driver runtime. |
| `driver_auto_install` | `bool` | `False` | Allow the driver to be installed on demand. |
| `driver_auto_install_used` | `bool` | `False` | Whether an on-demand install happened. |
| `execution_mode` | `"sql"`, `"dataframe"`, `"data_only"` or `None` | `None` | How queries are executed. |
| `**extra_data` | keyword arguments | none | Any other keyword is stored as an extra field, for example `server_hostname`, and is available as `config.model_extra`. |

### Returns

A `DatabaseConfig` instance with the stripped `type` and `name`. A `"<platform>:<mode>"` selector is resolved: `type` holds the base platform (`"timescaledb"`), the mode is kept for the adapter, and a DataFrame alias such as `"lakesail-df:local"` also sets `execution_mode="dataframe"` when it was not given.

### Raises

`pydantic.ValidationError` when `type` or `name` is missing, empty or only spaces (`database type cannot be empty`, `database name cannot be empty`), when `execution_mode` is not one of the three values, or when a `"<platform>:<mode>"` selector names a mode the platform does not declare.

### Example

```python
from benchbox.core.config import DatabaseConfig

database = DatabaseConfig(type=" duckdb ", name="local", connection_string=":memory:", server_hostname="example")
print(repr(database.type), database.model_extra)
```

Output on 0.4.1:

```text
'duckdb' {'server_hostname': 'example'}
```

### Compatibility

`benchbox.core.schemas.DatabaseConfig` is the same object.

## `benchbox.core.platform_registry.PlatformRegistry`

<span id="benchbox.core.platform_registry.PlatformRegistry"></span>

Looks up platform adapter classes by name, creates adapters, and reports which platforms are available. Everything is a class method: use the class itself, not an instance.

**Import:** `from benchbox.core.platform_registry import PlatformRegistry` · **Extras:** none

### Parameters

The constructor takes no arguments.

### Returns

A `PlatformRegistry` instance, which has no state of its own. The methods below work on the shared registry of built-in adapters.

| Method | Returns |
| --- | --- |
| `resolve_platform_name(platform_name: str)` | `str`: the lower-case canonical name. Aliases map to their platform (`"SQLite3"` gives `"sqlite"`); other names are only lower-cased, so unknown names pass through. |
| `get_all_aliases()` | `dict[str, str]`: a copy of the alias-to-canonical-name map. |
| `get_available_platforms()` | `list[str]`: the registered platform names, whether or not their packages are installed. |
| `get_adapter_class(platform_name: str)` | The adapter class registered for the name. Aliases and upper case are resolved. |
| `create_adapter(platform_name: str, config: dict[str, Any])` | An adapter instance built with the adapter's `from_config(config)`. The required keys depend on the adapter; `{"database_path": ":memory:"}` is enough for DuckDB. |
| `get_platform_availability()` | `dict[str, bool]`: for each registered platform, whether its required libraries are installed. |
| `is_platform_available(platform_name: str)` | `bool`: the entry of `get_platform_availability()` for the name, `False` for an unknown name. Aliases are not resolved. |
| `get_platform_requirements(platform_name: str)` | `str`: the install command, such as `uv add 'duckdb>=1.5,<2'`, or `"Unknown requirements"` for an unknown name. |
| `register_adapter(platform_name: str, adapter_class: type)` | `None`. Registers a third-party adapter under a lower-case name. Registering the same class again is allowed. |

### Raises

- `get_adapter_class` and `create_adapter`: `ValueError` when the name is not registered (`Platform 'x' not registered. Available: ...`).
- `register_adapter`: `ValueError` when the name is not a valid adapter key (for example `Bad Name!`), is an alias (`sqlite3`), or is already registered to a different class; `TypeError` when `adapter_class` is not a subclass of `PlatformAdapter`.

### Example

```python
from benchbox.core.platform_registry import PlatformRegistry

print(PlatformRegistry.resolve_platform_name("SQLite3"))
print(PlatformRegistry.get_all_aliases()["azure_synapse"])
print(PlatformRegistry.get_adapter_class("duckdb").__name__)
adapter = PlatformRegistry.create_adapter("duckdb", {"database_path": ":memory:"})
print(adapter.platform_name, adapter.database_path)
print(PlatformRegistry.is_platform_available("duckdb"), PlatformRegistry.is_platform_available("not_a_platform"))
print(PlatformRegistry.get_platform_requirements("duckdb"))
try:
    PlatformRegistry.get_adapter_class("not_a_platform")
except ValueError as exc:
    print(str(exc).split(". Available")[0])
```

Output on 0.4.1 with `duckdb` 1.5.6:

```text
sqlite
synapse
DuckDBAdapter
DuckDB :memory:
True False
uv add 'duckdb>=1.5,<2'
Platform 'not_a_platform' not registered
```

### Compatibility

The registered names include platforms whose packages are not installed, so `get_available_platforms()` is longer than the set of platforms that can run. Use `get_platform_availability()` for that. `benchbox.platforms.get_platform_adapter` resolves aliases and then uses this registry; see {doc}`platforms`.

## `benchbox.core.runner.LifecyclePhases`

<span id="benchbox.core.runner.LifecyclePhases"></span>

Chooses which phases `run_benchmark_lifecycle` runs. It is a dataclass of four flags.

**Import:** `from benchbox.core.runner import LifecyclePhases` · **Extras:** none

### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `generate` | `bool` | `True` | Generate the benchmark data. |
| `load` | `bool` | `True` | Load the data into the database. |
| `execute` | `bool` | `True` | Run the queries. |
| `statistics` | `bool` | `False` | Add a statistics phase between load and query. |

### Returns

A `LifecyclePhases` instance. `LifecyclePhases(generate=False, execute=False)` gives `LifecyclePhases(generate=False, load=True, execute=False, statistics=False)`.

### Raises

Nothing it raises itself.

### Compatibility

`benchbox.core.runner.runner.LifecyclePhases` is the same object.

## `benchbox.core.runner.run_benchmark_lifecycle`

<span id="benchbox.core.runner.run_benchmark_lifecycle"></span>

Runs a benchmark from data generation through loading and query execution, and returns its results.

**Import:** `from benchbox.core.runner import run_benchmark_lifecycle` · **Extras:** none for the function; the platform adapter you use may need one (`duckdb` for DuckDB)

### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `benchmark_config` | `BenchmarkConfig` | required | The benchmark to run. |
| `database_config` | `DatabaseConfig` or `None` | required | The target database. `None` is accepted when a `platform_adapter` is given or the run is `data_only`. |
| `system_profile` | `SystemProfile` or `None` | required | The profile from `SystemProfiler().get_system_profile()`. `None` is accepted. |
| `platform_config` | `dict[str, Any]` or `None` | `None` | Adapter settings, used to create the adapter when `platform_adapter` is not given. For DuckDB, `{"database_path": ":memory:"}` works. |
| `platform_adapter` | adapter instance or `None` | `None` | A ready adapter. When given, it is used as is. |
| `phases` | `LifecyclePhases` or `None` | `None` | The phases to run. `None` is `LifecyclePhases()`. |
| `validation_opts` | `ValidationOptions` or `None` | `None` | Preflight, post-generation manifest and post-load validation switches; all off when `None`. |
| `output_root` | `str` or `None` | `None` | Directory for the generated data. |
| `benchmark_instance` | benchmark or `None` | `None` | A ready benchmark object to use instead of creating one from `benchmark_config`. |
| `verbosity` | `VerbositySettings` or `None` | `None` | Console verbosity. `None` reads it from `benchmark_config.options`. |
| `monitor` | `PerformanceMonitor` or `None` | `None` | A performance monitor. `None` creates one. |
| `enable_resource_monitoring` | `bool` | `True` | Track CPU and memory during the run. |
| `execution_context` | `ExecutionContext` or `None` | `None` | Context that is recorded on the results. |

`benchmark_config`, `database_config` and `system_profile` are positional-or-keyword; every other parameter is keyword-only.

### Returns

`benchbox.core.results.models.BenchmarkResults`. The fields used below are `benchmark_name`, `platform`, `scale_factor`, `total_queries`, `successful_queries`, `failed_queries`, `validation_status` and `query_results` (a list with one `dict` per query).

- **All phases on (the default):** the queries listed in `benchmark_config.queries` run; `queries=None` runs the full set (22 queries for TPC-H).
- **Load without execute** (`LifecyclePhases(execute=False)`): the data is loaded, no query runs, `total_queries` is 0 and `validation_status` is `NOT_RUN`.
- **`test_execution_type="data_only"`:** the data is generated and a result with no queries is returned, with no adapter needed.
- **Existing data:** when `output_root` already holds a dataset for the same benchmark and scale factor, generation reuses it and the progress output says so.

The run prints progress to the console.

### Raises

- `ValueError` when `benchmark_config.name` is not a known benchmark (`Unknown benchmark 'x'. Available: ...`).
- `RuntimeError` when the execute or load phase needs an adapter and none can be built, for example `database_config=None` with no `platform_adapter` (`Cannot execute benchmark: platform adapter not initialized...`).
- `KeyError` (`'benchmark'`) when `platform_adapter` is not given, `database_config` is, and `platform_config` is missing. Pass an adapter, or a `platform_config` that the adapter's `from_config` accepts.

### Example

```python
from benchbox.core.config import BenchmarkConfig, DatabaseConfig
from benchbox.core.runner import LifecyclePhases, run_benchmark_lifecycle
from benchbox.core.system import SystemProfiler
from benchbox.platforms.duckdb import DuckDBAdapter

profile = SystemProfiler().get_system_profile()
benchmark = BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=0.01, queries=["1", "6"])
database = DatabaseConfig(type="duckdb", name="duckdb", connection_string=":memory:")

results = run_benchmark_lifecycle(
    benchmark,
    database,
    profile,
    platform_adapter=DuckDBAdapter(database_path=":memory:"),
    output_root="tpch_data",
)
print(results.benchmark_name, results.platform, results.scale_factor)
print(results.total_queries, results.successful_queries, results.failed_queries)

load_only = run_benchmark_lifecycle(
    benchmark,
    database,
    profile,
    platform_adapter=DuckDBAdapter(database_path=":memory:"),
    phases=LifecyclePhases(generate=False, execute=False),
    output_root="tpch_data",
)
print(load_only.total_queries, load_only.validation_status)
```

The progress lines that the run prints are left out. The result lines on 0.4.1 with `duckdb` 1.5.6:

```text
TPC-H DuckDB 0.01
2 2 0
0 NOT_RUN
```

### Compatibility

`benchbox.core.runner.runner.run_benchmark_lifecycle` is the same object.

## `benchbox.core.system.SystemProfiler`

<span id="benchbox.core.system.SystemProfiler"></span>

Collects a profile of the machine that runs the benchmark: operating system, CPU, memory and disk.

**Import:** `from benchbox.core.system import SystemProfiler` · **Extras:** none

### Parameters

The constructor takes no arguments.

### Returns

A `SystemProfiler` instance. Its method `get_system_profile()` takes no arguments and returns a `SystemProfile` (a Pydantic model in `benchbox.core.schemas`) with these fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `os_name`, `os_version`, `architecture` | `str` | Operating system name, release and machine architecture. |
| `cpu_model` | `str` or `None` | CPU model name, `None` when it cannot be determined. |
| `cpu_identity_provenance` | `"measured"`, `"inferred"`, `"user_attested"` or `None` | How `cpu_model` was found. The profiler sets `"measured"` or `"inferred"`, or `None` with no model. |
| `cpu_cores_physical`, `cpu_cores_logical` | `int` | Core counts, each at least 1. |
| `memory_total_gb`, `memory_available_gb` | `float` | Memory in GiB. |
| `python_version` | `str` | The running Python version. |
| `disk_space_gb` | `float` | Free space of the root file system `/`, in GiB. |
| `timestamp` | `datetime` | When the profile was taken. |
| `hostname` | `str` or `None` | The machine's network name. |

Memory and disk values are 0.0 when `psutil` is not installed.

### Raises

Nothing it raises itself.

### Example

```python
from benchbox.core.system import SystemProfiler

profile = SystemProfiler().get_system_profile()
print(type(profile).__name__, sorted(profile.model_dump())[:4])
print(profile.cpu_cores_logical >= profile.cpu_cores_physical >= 1)
print(profile.cpu_identity_provenance in (None, "measured", "inferred"))
```

Output on 0.4.1:

```text
SystemProfile ['architecture', 'cpu_cores_logical', 'cpu_cores_physical', 'cpu_identity_provenance']
True
True
```

## `benchbox.core.tpc_validation.TPCResultValidator`

<span id="benchbox.core.tpc_validation.TPCResultValidator"></span>

Checks a dictionary of TPC test results against seven validators and returns a report with an overall result, issues and metrics.

**Import:** `from benchbox.core.tpc_validation import TPCResultValidator` · **Extras:** none

### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `config` | `dict[str, Any]` or `None` | `None` | Validator settings under a `"validators"` key, one entry per validator: `completeness`, `query_result`, `timing`, `data_integrity`, `metrics`, `compliance` and `certification`. `create_default_config()` returns a full example. `None` uses each validator's own defaults. |

### Returns

A `TPCResultValidator` instance with the seven validators in `validators`.

| Method | Returns |
| --- | --- |
| `validate(test_results: dict[str, Any], validation_level: ValidationLevel = ValidationLevel.STANDARD)` | A `ValidationReport`. |
| `save_report(report: ValidationReport, output_path: Path)` | `None`. Writes the report as JSON and creates missing parent directories. |
| `load_report(input_path: Path)` | The `ValidationReport` that `save_report` wrote. |
| `create_default_config()` | `dict[str, Any]`: a configuration with default settings for every validator. |

`validate` reads these keys of `test_results`: `benchmark_name`, `scale_factor`, `test_start_time`, `test_end_time`, `query_results` (a mapping from query id to a dict with `status`, `execution_time_seconds` and `row_count`), `data_generation` and `metrics`. A missing key is reported as an `ERROR` issue by the `completeness` validator; it does not raise. A query counts as successful when its `status` is `"success"`.

A `ValidationReport` has these attributes:

- `overall_result`: a `ValidationResult` (`PASSED`, `FAILED`, `WARNING` or `SKIPPED`). Any `ERROR` issue makes it `FAILED`; otherwise any `WARNING` issue makes it `WARNING`.
- `validator_results`: a `dict` from validator name to its `ValidationResult`.
- `issues`: a list of `ValidationIssue` objects with `level` (`ERROR`, `WARNING` or `INFO`), `message` and `validator_name`.
- `metrics`: counts and a `validation_score` (the percentage of validators that passed).
- `execution_summary`: `total_queries`, `successful_queries`, `failed_queries` and `success_rate`.
- `validation_level`: the level passed to `validate`. It is recorded on the report and does not change the checks.
- `certification_status`: `READY`, `CONDITIONAL` or `NOT_READY`, set by the `certification` validator.
- `to_dict()`: the report as a JSON-ready dictionary.

### Raises

`validate` raises `AttributeError` when `test_results` is not a dictionary (for example `None`). A validator that fails on its own is caught and reported as an `ERROR` issue.

### Example

```python
from benchbox.core.tpc_validation import TPCResultValidator, ValidationLevel

validator = TPCResultValidator()
report = validator.validate(
    {
        "benchmark_name": "TPC-H",
        "scale_factor": 0.01,
        "query_results": {
            "1": {"status": "success", "execution_time_seconds": 0.5, "row_count": 4},
            "2": {"status": "failed"},
        },
    },
    ValidationLevel.BASIC,
)
print(report.overall_result.value, report.validation_level.value)
print(report.execution_summary["successful_queries"], report.execution_summary["failed_queries"])
print({name: result.value for name, result in report.validator_results.items()})
print(report.metrics["passed_validators"], report.metrics["total_validators"])
```

Output on 0.4.1:

```text
failed basic
1 1
{'completeness': 'failed', 'query_result': 'failed', 'timing': 'passed', 'data_integrity': 'passed', 'metrics': 'passed', 'compliance': 'failed', 'certification': 'failed'}
3 7
```

The `ValidationLevel` and `ValidationResult` enums and the `ValidationReport` class are importable from `benchbox.core.tpc_validation`; they are not listed as separate public symbols.
