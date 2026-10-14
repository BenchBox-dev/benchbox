<!-- markdownlint-disable MD024 -->

# Polars Platform Adapter

```{tags} reference, python-api, polars
```

The Polars adapter loads BenchBox benchmark data into Polars lazy frames. It does not run SQL: query execution on Polars goes through the DataFrame adapter.

## Overview

Polars is installed with the `polars` extra (`pip install "benchbox[polars]"`); the base `benchbox` install does not include it. The adapter:

- **Registers each table as a lazy frame** - Data files are scanned, not copied into a database.
- **Reads delimited text and Parquet** - `.tbl`, `.csv` and Parquet files.
- **Keeps no database file** - Loading writes nothing to the working directory.
- **Rejects SQL** - `execute_query` always raises `NotImplementedError`.

To run a benchmark's queries on Polars, use the DataFrame adapter instead: `benchbox.platforms.get_adapter("polars")` and `get_dataframe_adapter("polars-df")` both return a `PolarsDataFrameAdapter`, not the class on this page.

## API Reference

### PolarsAdapter Class

<span id="benchbox.platforms.polars_platform.PolarsAdapter"></span>

`benchbox.platforms.polars_platform.PolarsAdapter` creates a Polars table context, registers a benchmark's data files in it as lazy frames, and reports the row counts.

**Import:** `from benchbox.platforms.polars_platform import PolarsAdapter` · **Extras:** `polars`

#### Parameters

All parameters are keyword arguments (the signature is `(**config)`).

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `working_dir` | `str` or `Path` | `"./polars_working"` | Working directory. The constructor creates it, including missing parents. |
| `execution_mode` | `str` | `"lazy"` | Stored as `execution_mode` and reported by `get_platform_info`. Any value is accepted and none changes how data is loaded. |
| `streaming` | `bool` | `False` | Deprecated and rejected from 0.6.0. `True` logs a warning once per run and has no effect: the adapter runs on the `default` engine and records the default receipt. `False` is accepted silently. |
| `execution_engine` | `str` | `"default"` | Only `default` is accepted; anything else fails before any work. |
| `n_rows` | `int` or `None` | `None` | Row limit applied when delimited text files are scanned. `None` reads all rows. |
| `rechunk` | `bool` | `True` | Passed to the Polars scan functions. |

The adapter also accepts the keys every BenchBox adapter takes, such as `force_recreate`, `show_query_plans` and `tuning_enabled`; see Constructor Parameters on {doc}`duckdb`. Keys it does not recognise are accepted and ignored.

#### Returns

A `PolarsAdapter`. Construction creates the working directory and opens no connection.

#### Raises

`ImportError` when the `polars` package is not installed (`Polars not installed. Install with: pip install polars`).

#### Example

```python
from pathlib import Path

from benchbox import TPCH
from benchbox.platforms.polars_platform import PolarsAdapter

benchmark = TPCH(scale_factor=0.01, output_dir="tpch_data")
benchmark.generate_data()

adapter = PolarsAdapter(working_dir="polars_work")
connection = adapter.create_connection()
adapter.create_schema(benchmark, connection)
row_counts, seconds, timings = adapter.load_data(benchmark, connection, Path("tpch_data"))

print(row_counts["region"], row_counts["lineitem"], sorted(connection.get_tables())[:3])
print(connection.get_table("region").collect()["r_name"].to_list())
print(adapter.get_platform_info()["configuration"])

try:
    adapter.execute_query(connection, "SELECT 1", "q1")
except NotImplementedError as exc:
    print(str(exc)[:30])
```

Output on 0.4.1 with `polars` 1.44.2:

```text
5 60175 ['customer', 'lineitem', 'nation']
['AFRICA', 'AMERICA', 'ASIA', 'EUROPE', 'MIDDLE EAST']
{'working_dir': 'polars_work', 'execution_mode': 'lazy', 'execution_engine': {'requested': 'default', 'applied': None, 'applied_class': None, 'applied_native': {}, 'resolution': 'platform_default', 'observed': 'not_captured', 'observed_source': 'none'}, 'n_rows_limit': None, 'rechunk': True, 'rechunk_effective': True, 'result_cache_enabled': False}
Polars SQL mode is not support
```

#### Compatibility

- `benchbox.platforms.PolarsAdapter` is the same object; `PlatformRegistry.get_adapter_class("polars")` returns it too.
- The platform name `polars` selects this class in the registry, while `get_adapter("polars")` selects the DataFrame adapter.
- The base `benchbox` install does not include Polars; install the `polars` extra.

### Methods and attributes

#### Construction and configuration

<span id="benchbox.platforms.polars_platform.PolarsAdapter.__init__"></span>
**`__init__(**config)`**: Creates the adapter from keyword arguments. See Parameters above.

<span id="benchbox.platforms.polars_platform.PolarsAdapter.from_config"></span>
**`from_config(config: dict[str, Any])`** (class method): Builds an adapter from a unified configuration dictionary. `working_dir` is used when present and not empty. Otherwise `benchmark` and `scale_factor` are required (a missing key raises `KeyError`) and the working directory is `<output_dir>/<benchmark>_sf<token>/<benchmark>_sf<token>_notuning_noconstraints.polars`, created on the spot; for example `od/tpch_sf001/tpch_sf001_notuning_noconstraints.polars` for `output_dir="od"`. The keys `execution_mode` (default `"lazy"`), `execution_engine` (only `"default"`), `n_rows` (`None`), `rechunk` (`True`) and `force` (stored as `force_recreate`, `False`) are read, along with these pass-through keys when present: `tuning_config`, `tuning_enabled`, `unified_tuning_configuration`, `tuning_source`, `tuning_source_file`, `verbose_enabled` and `very_verbose`. Other keys are dropped, except a legacy `streaming` key, which is passed through. This SQL-mode adapter accepts only the `default` execution engine: `execution_engine="streaming"` raises `UnsupportedExecutionEngineError`, while a true `streaming` value warns and runs on the default engine. Use the `polars-df` platform with `--execution-engine streaming` to run on the Polars streaming engine.

<span id="benchbox.platforms.polars_platform.PolarsAdapter.add_cli_arguments"></span>
**`add_cli_arguments(parser) -> None`** (static method): Adds a `Polars Arguments` group to an `argparse.ArgumentParser`. The group defines `--polars-execution-mode` (`lazy` or `eager`, default `lazy`), `--polars-n-rows` (`int`, default `None`), `--polars-working-dir` (default `None`) and `--polars-rechunk` (flag, default `True`; it cannot be turned off from the command line).

<span id="benchbox.platforms.polars_platform.PolarsAdapter.platform_name"></span>
**`platform_name`** (property): Always the string `'Polars'`.

<span id="benchbox.platforms.polars_platform.PolarsAdapter.get_target_dialect"></span>
**`get_target_dialect() -> str`**: Returns `'dataframe'`, not a SQL dialect.

<span id="benchbox.platforms.polars_platform.PolarsAdapter.get_platform_info"></span>
**`get_platform_info(connection: Any = None) -> dict[str, Any]`**: Returns a `dict` with `platform_type` (`'polars'`), `platform_name`, `connection_mode` (`'in-memory'`), `configuration` (`working_dir`, `execution_mode`, `execution_engine` (a receipt with `requested`, `applied`, `applied_class`, `applied_native`, `resolution`, `observed` and `observed_source`), `n_rows_limit`, `rechunk`, `rechunk_effective` and `result_cache_enabled`, always `False`), and `client_library_version` and `platform_version`, both the installed Polars version.

#### Connection and schema

<span id="benchbox.platforms.polars_platform.PolarsAdapter.create_connection"></span>
**`create_connection(**connection_config) -> PolarsDataFrameContext`**: Returns an empty table context and turns on Polars' global string cache. It opens no file. The context holds the registered tables as lazy frames: `register_table(name, df)` (a `DataFrame` is converted to a lazy frame), `unregister_table(name)`, `get_table(name)` (a `LazyFrame`, or `None` for an unknown name) and `get_tables()` (a list of names).

<span id="benchbox.platforms.polars_platform.PolarsAdapter.create_schema"></span>
**`create_schema(benchmark, connection: Any) -> float`**: Reads the table and column definitions from `benchmark.get_schema()` and returns the elapsed time in seconds. No table is created; tables are registered by `load_data`. Polars enforces no primary or foreign keys, so those settings have no effect.

<span id="benchbox.platforms.polars_platform.PolarsAdapter.configure_for_benchmark"></span>
**`configure_for_benchmark(connection: Any, benchmark_type: str) -> None`**: Does nothing except log a message, and returns `None`.

<span id="benchbox.platforms.polars_platform.PolarsAdapter.validate_platform_capabilities"></span>
**`validate_platform_capabilities(benchmark_type: str)`**: Returns a `ValidationResult` (`is_valid`, `errors`, `warnings`, `details`). `is_valid` is `True` when Polars is importable. `warnings` always holds one entry saying that SQL mode is not available; Polars versions older than 0.20 add another. `details` holds `platform`, `benchmark_type`, `dry_run_mode`, `polars_available`, `working_dir`, `execution_mode`, `sql_mode` (`False`) and `polars_version`.

#### Loading data into tables

<span id="benchbox.platforms.polars_platform.PolarsAdapter.load_data"></span>
**`load_data(benchmark, connection: Any, data_dir: Path) -> tuple[dict[str, int], float, dict[str, Any] | None]`**: Scans the benchmark's data files for every table, registers each as a lazy frame on the connection and returns `(table_row_counts, seconds, per_table_timings)`. `table_row_counts` maps lower-case table names to row counts (TPC-H at scale factor 0.01 gives `customer` 1500 and `lineitem` 60175). `per_table_timings` maps each table to `{"total_ms": ...}`. Counting the rows reads each table once. Text files are read without a header, `.tbl` files split on `|`, and parse errors are ignored. Parquet files are read as Parquet. Raises `ValueError` (`No data files found in <data_dir>`) when no data files are found.

<span id="benchbox.platforms.polars_platform.PolarsAdapter.create_external_tables"></span>
**`create_external_tables(benchmark: Any, connection: Any, data_dir: Path) -> tuple[dict[str, int], float, dict[str, Any] | None]`**: Does the same as `load_data` and returns the same tuple.

<span id="benchbox.platforms.polars_platform.PolarsAdapter.supports_external_tables"></span>
**`supports_external_tables`** (class attribute): `True`.

#### Query execution

<span id="benchbox.platforms.polars_platform.PolarsAdapter.execute_query"></span>
**`execute_query(connection: Any, query: str, query_id: str, benchmark_type: str | None = None, scale_factor: float | None = None, validate_row_count: bool = True, stream_id: int | None = None) -> dict[str, Any]`**: Always raises `NotImplementedError`, whatever the arguments. The message points to a `polars-df` platform for DataFrame execution; in 0.4.1 the DataFrame adapter is `benchbox.platforms.get_dataframe_adapter("polars-df")`.

#### Database management

<span id="benchbox.platforms.polars_platform.PolarsAdapter.check_database_exists"></span>
**`check_database_exists(**connection_config) -> bool`**: Returns `True` when the working directory (or the `working_dir` keyword) contains at least one `.parquet` or `.csv` file directly inside it, and `False` otherwise, including for a missing directory.

<span id="benchbox.platforms.polars_platform.PolarsAdapter.drop_database"></span>
**`drop_database(**connection_config) -> None`**: Deletes the working directory (or the `working_dir` keyword) and everything in it, and logs two warnings. Does nothing when the directory does not exist.

#### Capabilities

<span id="benchbox.platforms.polars_platform.PolarsAdapter.plan_capture_phase_eligible"></span>
**`plan_capture_phase_eligible`** (class attribute): `True`.

<span id="benchbox.platforms.polars_platform.PolarsAdapter.driver_isolation_capability"></span>
**`driver_isolation_capability`** (class attribute): `DriverIsolationCapability.NOT_APPLICABLE` (from `benchbox.platforms.base`).

<span id="benchbox.platforms.polars_platform.PolarsAdapter.supports_tuning_type"></span>
**`supports_tuning_type(tuning_type) -> bool`**: Returns `False` for every `TuningType`.
