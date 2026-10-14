<!-- markdownlint-disable MD024 -->

# Result Analysis API

```{tags} reference, python-api, validation
```

Complete Python API reference for BenchBox result analysis, export, and comparison utilities.

## Overview

BenchBox provides result analysis utilities for benchmark execution data. These tools enable performance analysis, result comparison, statistical analysis, and export in multiple formats.

**Key Features**:

- **Result Export**: Export benchmark results to JSON, CSV, and HTML formats
- **Result Comparison**: Compare results across runs to detect regressions
- **Timing Analysis**: Query timing with statistical analysis
- **Anonymization**: Privacy-preserving result sharing with PII removal
- **Display Utilities**: Formatted output for benchmark results
- **Outlier Detection**: Flag unusually slow or fast queries

## Quick Start

Export and analyze benchmark results:

```python
from benchbox.tpch import TPCH
from benchbox.platforms.duckdb import DuckDBAdapter
from benchbox.core.results.exporter import ResultExporter

benchmark = TPCH(scale_factor=0.01, output_dir="tpch_data")
adapter = DuckDBAdapter()
results = adapter.run_benchmark(benchmark, query_subset=[1, 3, 6])

exporter = ResultExporter(output_dir="benchmark_results")
exported_files = exporter.export_result(results, formats=["json", "csv", "html"])

print(f"Results exported: {exported_files}")
```

On 0.4.1 this prints one line per file while it exports, then the mapping (the execution ID and timestamp in each name change on every run):

```text
Exported JSON: benchmark_results/tpch_sf001_duckdb_20261003_193217_a785a176.json
Exported CSV: benchmark_results/tpch_sf001_duckdb_20261003_193217_a785a176.csv
Exported HTML: benchmark_results/tpch_sf001_duckdb_20261003_193217_a785a176.html
Results exported: {'json': PosixPath('benchmark_results/tpch_sf001_duckdb_20261003_193217_a785a176.json'), 'csv': PosixPath('benchmark_results/tpch_sf001_duckdb_20261003_193217_a785a176.csv'), 'html': PosixPath('benchmark_results/tpch_sf001_duckdb_20261003_193217_a785a176.html')}
```

## API Reference

All names on this page are imported from the module shown on each **Import** line. They need only the base install, except where an **Extras** entry says otherwise.

### Result Exporter

<span id="benchbox.core.results.exporter.ResultExporter"></span>

Exports a `BenchmarkResults` object to JSON, CSV and HTML files, lists and loads exported results, and compares two exported runs.

**Import:** `from benchbox.core.results.exporter import ResultExporter` · **Extras:** none (a cloud storage `output_dir` such as `s3://...` needs the `cloudstorage` extra)

#### Parameters

<span id="benchbox.core.results.exporter.ResultExporter.__init__"></span>

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `output_dir` | `str`, `Path` or `None` | `None` | Directory for exported files. The directory is created when the exporter is constructed. `None` uses `$BENCHBOX_RESULTS_DIR` if set, otherwise `$BENCHBOX_OUTPUT_DIR/results` if set, otherwise `benchmark_runs/results` under the current directory. |
| `anonymize` | `bool` | `True` | Anonymize the exported data. When `True` and no `anonymization_config` is given, the salt is read from the `BENCHBOX_MACHINE_ID_SALT` environment variable if it is set. |
| `anonymization_config` | `AnonymizationConfig` or `None` | `None` | Anonymization settings. Ignored when `anonymize` is `False`. |
| `console` | `rich.console.Console` or `None` | `None` | Where progress lines (`Exported JSON: ...`) and summaries are printed. `None` creates a default console. |
| `plan_history_dir` | `str`, `Path` or `None` | `None` | Directory in which to record plan fingerprints for each exported run. `None` falls back to the `BENCHBOX_PLAN_HISTORY_DIR` environment variable, and records nothing if that is unset. |

Full signature: `(output_dir: 'str | Path | None' = None, anonymize: 'bool' = True, anonymization_config: 'AnonymizationConfig | None' = None, console: 'Console | None' = None, plan_history_dir: 'str | Path | None' = None)`

**Raises:** `FileNotFoundError` when a local `output_dir` cannot be created.

**Returns:** a `ResultExporter` instance. Its `output_dir` attribute is a `pathlib.Path` for a local directory (a cloud path object for a cloud URL), and its `anonymize` attribute holds the flag. When `anonymize` is `True`, `anonymization_manager` is an `AnonymizationManager`; when it is `False`, `anonymization_manager` is `None`.

<span id="benchbox.core.results.exporter.ResultExporter.EXPORTER_NAME"></span>

The class attribute `EXPORTER_NAME` is the string `"benchbox-exporter"`. It is written to the `export.tool` field of every JSON file.

#### `export_result(result, formats=None)`

<span id="benchbox.core.results.exporter.ResultExporter.export_result"></span>
<span id="export_result"></span>

Writes one file per requested format and returns their paths.

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `result` | `BenchmarkResults` | required | The result to export, for example the object returned by a platform adapter's `run_benchmark`. |
| `formats` | `list[str]` or `None` | `None` | Formats to write: `"json"`, `"csv"` or `"html"`. `None` means `["json"]`; an empty list writes nothing and returns `{}`. |

**Returns:** `dict[str, Path]` mapping each format name to the file it wrote. Files are named `<benchmark>_sf<scale token>_<platform>_<timestamp>_<execution id>.<ext>`, for example `tpch_sf001_duckdb_20261003_193217_a785a176.json`. Exporting the same result again overwrites the same files. The JSON file carries `result_schema_version` `2.2` in 0.4.1.

**Raises:** `ResultExportError` (a `RuntimeError` subclass, importable from `benchbox.core.results.exporter`) if any format fails, including an unknown format name such as `"xml"`. Every other requested format is still attempted first, and the message lists each failure.

```python
from benchbox.core.results.exporter import ResultExporter

exporter = ResultExporter(output_dir="benchmark_results")
files = exporter.export_result(results, formats=["json", "csv", "html"])
print(sorted(files))
```

```text
['csv', 'html', 'json']
```

#### `list_results()`

<span id="benchbox.core.results.exporter.ResultExporter.list_results"></span>
<span id="list_results"></span>

Lists the result files in `output_dir`.

**Returns:** `list[dict]`, newest first by `timestamp`. Each dictionary has the keys `file` (the path), `version` (the result schema version), `benchmark`, `platform`, `scale_factor`, `execution_id`, `timestamp`, `duration` (seconds), `queries` (the total query count) and `status` (the validation status, such as `"passed"`).

Only `*.json` files directly in `output_dir` are read. Companion files such as `*.plans.json`, `*.submission.json` files, files that are not valid JSON and files with an unsupported schema are skipped without an error.

```python
exporter = ResultExporter(output_dir="benchmark_results")
for result in exporter.list_results():
    print(f"{result['benchmark']} on {result['platform']} @ {result['timestamp']}: {result['duration']:.2f}s")
```

```text
TPC-H on DuckDB @ 2026-10-03T19:32:17.241478: 0.59s
```

#### `show_results_summary()`

<span id="benchbox.core.results.exporter.ResultExporter.show_results_summary"></span>
<span id="show_results_summary"></span>

Prints a table of the listed results to the exporter's console and returns `None`. The table has the columns Benchmark, Platform, Timestamp, Duration, Queries and Version, and shows the 10 newest results followed by `... and N more results` when there are more. With no results it prints `No exported results found`.

```python
exporter = ResultExporter(output_dir="benchmark_results")
exporter.show_results_summary()
```

```text

Exported Results (1 total)
Output directory: benchmark_results
┏━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━┓
┃ Benchmark ┃ Platform ┃ Timestamp           ┃ Duration ┃ Queries ┃ Version ┃
┡━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━┩
│ TPC-H     │ DuckDB   │ 2026-10-03 19:32:17 │ 0.59s    │ 2       │ 2.2     │
└───────────┴──────────┴─────────────────────┴──────────┴─────────┴─────────┘
```

#### `load_result_from_file(filepath)`

<span id="benchbox.core.results.exporter.ResultExporter.load_result_from_file"></span>

Reads one result JSON file.

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `filepath` | `Path` | required | The file to read. |

**Returns:** a `dict` with the keys `data` (the parsed JSON), `version`, `result_schema_version` (the same value) and `filepath`. It returns `None` and logs an error if the file cannot be read or parsed.

#### `compare_results(baseline_path, current_path)`

<span id="benchbox.core.results.exporter.ResultExporter.compare_results"></span>
<span id="compare_results"></span>

Compares two exported result files.

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `baseline_path` | `Path` | required | The baseline result JSON file. |
| `current_path` | `Path` | required | The result JSON file to compare against it. |

Pass `Path` objects. With the default `anonymize=True`, a plain string raises `AttributeError: 'str' object has no attribute 'name'`.

**Returns:** a `dict` with these keys:

- `baseline_file` and `current_file`: the file names when the exporter anonymizes, the full paths otherwise.
- `baseline_version` and `current_version`: the result schema versions.
- `generation_compatibility`: a block with `status` (`"compatible"`, `"unknown"` or `"incompatible"`), `compatible` (`True`, `None` or `False`), `warning` and each side's data-generation provenance. Results exported without a data-generation stamp report `"unknown"` and a warning that timing differences may come from different datasets.
- `performance_changes`: for `total_execution_time` and `average_query_time` (seconds), a dictionary with `baseline`, `current`, `change_percent` (rounded to two decimals) and `improved` (`True` when the current value is lower).
- `query_comparisons`: one dictionary per query present in both files, with `query_id`, `baseline_time_ms`, `current_time_ms`, `change_percent` and `improved`.
- `summary`, present only when at least one query was compared: `total_queries_compared`, `improved_queries`, `regressed_queries`, `unchanged_queries` and `overall_assessment`. The assessment is based on the mean of the two `change_percent` values: `significant_improvement` below -10, `improvement` below -5, `significant_regression` above 10, `regression` above 5, and `no_significant_change` otherwise.

If either file cannot be loaded, the result is `{'error': 'Failed to load one or both result files', 'baseline_loaded': True, 'current_loaded': False}` (with the flags showing which file loaded), and nothing is raised.

```python
comparison = exporter.compare_results(baseline_json, current_json)

perf = comparison["performance_changes"]["average_query_time"]
print(f"Average query time: {perf['change_percent']:+.2f}% change")
print(comparison["summary"]["overall_assessment"])
```

```text
Average query time: -22.22% change
significant_improvement
```

#### `export_comparison_report(comparison, output_path=None)`

<span id="benchbox.core.results.exporter.ResultExporter.export_comparison_report"></span>
<span id="export_comparison_report"></span>

Writes a comparison as an HTML report.

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `comparison` | `dict` | required | The dictionary returned by `compare_results`. |
| `output_path` | `Path` or `None` | `None` | Where to write the report. `None` writes `comparison_report_<timestamp>.html` in `output_dir`. |

**Returns:** the `Path` of the report.

**Raises:** `FileNotFoundError` if the parent directory of `output_path` does not exist.

```python
report_path = exporter.export_comparison_report(comparison)
print(f"Comparison report: {report_path}")
```

```text
Comparison report: results/comparison_report_20261003_193352.html
```

### Timing Collector

<span id="benchbox.core.results.timing.TimingCollector"></span>

Collects per-query timings while you run queries, as `QueryTiming` objects.

**Import:** `from benchbox.core.results.timing import TimingCollector` · **Extras:** none

#### Parameters

<span id="benchbox.core.results.timing.TimingCollector.__init__"></span>

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `enable_detailed_timing` | `bool` | `True` | Record phase timings from `time_phase`. When `False`, `time_phase` does nothing and `timing_breakdown` stays empty. |

**Returns:** a `TimingCollector` instance with the flag stored as `enable_detailed_timing` and no completed timings: `get_completed_timings()` returns `[]`.

#### `time_query(query_id, query_name=None)`

<span id="benchbox.core.results.timing.TimingCollector.time_query"></span>
<span id="time_query"></span>

A context manager that times the code inside the `with` block as one query.

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `query_id` | `str` | required | The query identifier. |
| `query_name` | `str` or `None` | `None` | A human-readable name. |

**Yields:** a `dict` for the execution. Put values in its `"metrics"` dictionary to set fields of the resulting `QueryTiming`: `rows_returned`, `bytes_processed`, `tables_accessed`, `thread_id`, `connection_id`, `cpu_time`, `memory_peak`, `warning_count` and `platform_metrics`. `collector.record_metric(query_id, name, value)` writes to the same dictionary.

When the block ends, a `QueryTiming` is appended to the completed list with `execution_time` set to the elapsed seconds and `status` `"SUCCESS"`. If the block raises, the exception is re-raised after the timing is recorded with `status` `"ERROR"` and the message in `error_message`. The collector sets only these two statuses.

```python
from benchbox.core.results.timing import TimingCollector

collector = TimingCollector()

with collector.time_query("Q1", "Pricing Summary Report") as timing:
    timing["metrics"]["rows_returned"] = 4
    timing["metrics"]["tables_accessed"] = ["lineitem"]

timings = collector.get_completed_timings()
print(timings[0].query_id, timings[0].rows_returned, timings[0].status)
```

```text
Q1 4 SUCCESS
```

#### `time_phase(query_id, phase_name)`

<span id="benchbox.core.results.timing.TimingCollector.time_phase"></span>
<span id="time_phase"></span>

A context manager that times a phase of a query that is being timed by `time_query`.

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `query_id` | `str` | required | The query being timed. It must match an open `time_query` block. |
| `phase_name` | `str` | required | The phase name. |

The duration in seconds is stored in the query's `timing_breakdown` under `phase_name`. The names `"parse"`, `"optimize"`, `"execute"` and `"fetch"` also fill `parse_time`, `optimization_time`, `execution_only_time` and `fetch_time`; any other name appears only in `timing_breakdown`. The call does nothing, and raises nothing, when detailed timing is disabled or no `time_query` block is open for `query_id`.

```python
with collector.time_query("Q2") as timing:
    with collector.time_phase("Q2", "parse"):
        pass
    with collector.time_phase("Q2", "execute"):
        pass

print(sorted(collector.get_completed_timings()[-1].timing_breakdown))
```

```text
['execute', 'parse']
```

#### `record_metric(query_id, metric_name, value)`

<span id="benchbox.core.results.timing.TimingCollector.record_metric"></span>

Stores `value` under `metric_name` in the `"metrics"` dictionary of the open `time_query` block for `query_id`. It does nothing, and raises nothing, if no such block is open. Only the metric names listed under `time_query` reach the `QueryTiming`.

**Returns:** `None`.

#### `get_completed_timings()`

<span id="benchbox.core.results.timing.TimingCollector.get_completed_timings"></span>
<span id="get_completed_timings"></span>

**Returns:** a new `list[QueryTiming]`, in completion order. Changing the list does not change the collector.

#### `clear_completed_timings()`

<span id="benchbox.core.results.timing.TimingCollector.clear_completed_timings"></span>

Removes all completed timings. **Returns:** `None`.

#### `get_timing_summary()`

<span id="benchbox.core.results.timing.TimingCollector.get_timing_summary"></span>
<span id="get_timing_summary"></span>

Summarizes the completed timings.

**Returns:** a `dict`:

- `{}` when nothing has been timed.
- `{'total_queries': n, 'successful_queries': 0}` when every timing failed.
- Otherwise `total_queries`, `successful_queries`, `failed_queries`, `total_execution_time`, `average_execution_time`, `median_execution_time`, `min_execution_time`, `max_execution_time` and `execution_time_stddev`. The time statistics cover only the timings with status `"SUCCESS"`, and the standard deviation is `0` for a single success.

```python
summary = collector.get_timing_summary()
print(summary["total_queries"], summary["successful_queries"], summary["failed_queries"])
```

```text
2 2 0
```

### Timing Analyzer

<span id="benchbox.core.results.timing.TimingAnalyzer"></span>

Computes statistics over a list of `QueryTiming` objects.

**Import:** `from benchbox.core.results.timing import TimingAnalyzer` · **Extras:** none

#### Parameters

<span id="benchbox.core.results.timing.TimingAnalyzer.__init__"></span>

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `timings` | `list[QueryTiming]` | required | The timings to analyze. |

**Returns:** a `TimingAnalyzer` instance. Its `timings` attribute is the list you passed, and `successful_timings` holds the entries whose `status` is `"SUCCESS"`.

Every statistic below uses only the timings whose `status` is `"SUCCESS"`. All times are in seconds. The examples in this section use these timings:

```python
from benchbox.core.results.timing import QueryTiming, TimingAnalyzer

timings = [
    QueryTiming(query_id="Q1", execution_time=1.0, rows_returned=100, timing_breakdown={"parse": 0.1, "execute": 0.9}),
    QueryTiming(query_id="Q2", execution_time=2.0, rows_returned=50, timing_breakdown={"parse": 0.2}),
    QueryTiming(query_id="Q3", execution_time=3.0),
    QueryTiming(query_id="Q4", execution_time=4.0),
    QueryTiming(query_id="Q5", execution_time=100.0),
    QueryTiming(query_id="Q6", execution_time=5.0, status="ERROR", error_message="boom"),
]
analyzer = TimingAnalyzer(timings)
```

#### `get_basic_statistics()`

<span id="benchbox.core.results.timing.TimingAnalyzer.get_basic_statistics"></span>
<span id="get_basic_statistics"></span>

**Returns:** a `dict` with `count`, `total_time`, `mean`, `median`, `min`, `max`, `stdev` and `variance`, or `{}` when there are no successful timings. `stdev` and `variance` are `0` for a single timing.

```python
stats = analyzer.get_basic_statistics()
print(stats["count"], stats["mean"], stats["median"], round(stats["stdev"], 3))
```

```text
5 22.0 3.0 43.618
```

#### `get_percentiles(percentiles=None)`

<span id="benchbox.core.results.timing.TimingAnalyzer.get_percentiles"></span>
<span id="get_percentiles"></span>

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `percentiles` | `list[float]` or `None` | `None` | Percentiles from 0 to 100. `None` means `[50, 75, 90, 95, 99]`. Values outside 0 to 100 are skipped. |

**Returns:** a `dict` mapping each percentile, with the key type you passed, to an execution time, using linear interpolation between neighbouring timings. It is `{}` when there are no successful timings.

```python
print(analyzer.get_percentiles([50, 90, 0, 100, 150]))
```

```text
{50: 3.0, 90: 61.60000000000001, 0: 1.0, 100: 100.0}
```

#### `analyze_query_performance()`

<span id="benchbox.core.results.timing.TimingAnalyzer.analyze_query_performance"></span>
<span id="analyze_query_performance"></span>

**Returns:** a `dict` with five entries:

- `basic_stats`: the result of `get_basic_statistics()`.
- `percentiles`: the result of `get_percentiles()` with its default percentiles.
- `status_breakdown`: a count of timings for each status, over all timings.
- `timing_phases`: for each phase name found in `timing_breakdown`, a dictionary with `count`, `total`, `mean` and `median`. Empty when no timing has a breakdown.
- `throughput_metrics`: `mean_rows_per_second`, `median_rows_per_second`, `max_rows_per_second` and `total_rows_processed`, over the timings that have a `rows_per_second` value. Empty when none do.

```python
analysis = analyzer.analyze_query_performance()
print(analysis["status_breakdown"])
print(analysis["timing_phases"]["parse"]["count"], analysis["throughput_metrics"]["total_rows_processed"])
```

```text
{'SUCCESS': 5, 'ERROR': 1}
2 150
```

#### `identify_outliers(method="iqr", factor=1.5)`

<span id="benchbox.core.results.timing.TimingAnalyzer.identify_outliers"></span>
<span id="identify_outliers"></span>

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `method` | `str` | `"iqr"` | `"iqr"` flags times more than `factor` interquartile ranges outside the first and third quartiles. `"zscore"` flags times more than `factor` standard deviations from the mean. |
| `factor` | `float` | `1.5` | The threshold. The default is 1.5 for both methods; a z-score threshold of 3.0 is common, but you must pass it. |

**Returns:** `list[QueryTiming]` of the outliers, empty when there are no successful timings. With `"zscore"` it is also empty when all times are equal.

**Raises:**

- `ValueError` for any other `method`: `Unknown outlier detection method: x`.
- `statistics.StatisticsError` for `"iqr"` when there are fewer than two successful timings.

```python
print([t.query_id for t in analyzer.identify_outliers(method="zscore", factor=1.5)])
print([t.query_id for t in analyzer.identify_outliers(method="zscore", factor=3.0)])
```

```text
['Q5']
[]
```

#### `compare_query_performance(baseline_timings)`

<span id="benchbox.core.results.timing.TimingAnalyzer.compare_query_performance"></span>
<span id="compare_query_performance"></span>

Compares this analyzer's timings against baseline timings.

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `baseline_timings` | `list[QueryTiming]` | required | The baseline timings. |

**Returns:** a `dict` with `current_stats` and `baseline_stats` (from `get_basic_statistics`), `performance_change` and `regression_analysis`. `performance_change` has an entry for each of `mean`, `median`, `min` and `max` with `current`, `baseline`, `change_percent` and `improved` (`True` when the current value is lower). `regression_analysis` has `is_regression` (mean more than 10% slower), `is_improvement` (mean more than 10% faster) and `severity`: `"critical"` above 50% slower, `"major"` above 25%, `"minor"` above 10%, otherwise `"none"`.

If either side has no successful timings, it returns `{'error': 'Insufficient data for comparison'}`.

```python
baseline = [QueryTiming(query_id="Q1", execution_time=1.0), QueryTiming(query_id="Q2", execution_time=1.0)]
current = [QueryTiming(query_id="Q1", execution_time=1.5), QueryTiming(query_id="Q2", execution_time=1.5)]

comparison = TimingAnalyzer(current).compare_query_performance(baseline)
print(comparison["performance_change"]["mean"]["change_percent"])
print(comparison["regression_analysis"])
```

```text
50.0
{'is_regression': True, 'is_improvement': False, 'severity': 'major'}
```

### Query Timing

<span id="benchbox.core.results.timing.QueryTiming"></span>

A dataclass that holds the timing and outcome of one query execution. `TimingCollector` creates these, and you can create them yourself.

**Import:** `from benchbox.core.results.timing import QueryTiming` · **Extras:** none

#### Parameters

<span id="benchbox.core.results.timing.QueryTiming.__init__"></span>

Only `query_id` is required. Every field is also an attribute of the same name; durations are in seconds.

- <span id="benchbox.core.results.timing.QueryTiming.query_id"></span>`query_id` (`str`): the query identifier.
- <span id="benchbox.core.results.timing.QueryTiming.query_name"></span>`query_name` (`str | None`, default `None`): a human-readable name.
- <span id="benchbox.core.results.timing.QueryTiming.execution_sequence"></span>`execution_sequence` (`int`, default `0`): the position in an execution order.
- <span id="benchbox.core.results.timing.QueryTiming.execution_time"></span>`execution_time` (`float`, default `0.0`): total execution time.
- <span id="benchbox.core.results.timing.QueryTiming.parse_time"></span>`parse_time` (`float | None`, default `None`): SQL parsing time.
- <span id="benchbox.core.results.timing.QueryTiming.optimization_time"></span>`optimization_time` (`float | None`, default `None`): optimization time.
- <span id="benchbox.core.results.timing.QueryTiming.execution_only_time"></span>`execution_only_time` (`float | None`, default `None`): execution time without parsing or fetching.
- <span id="benchbox.core.results.timing.QueryTiming.fetch_time"></span>`fetch_time` (`float | None`, default `None`): result fetching time.
- <span id="benchbox.core.results.timing.QueryTiming.timing_breakdown"></span>`timing_breakdown` (`dict[str, float]`, default `{}`): duration of each named phase.
- <span id="benchbox.core.results.timing.QueryTiming.rows_returned"></span>`rows_returned` (`int`, default `0`): the number of rows returned.
- <span id="benchbox.core.results.timing.QueryTiming.bytes_processed"></span>`bytes_processed` (`int | None`, default `None`): bytes processed.
- <span id="benchbox.core.results.timing.QueryTiming.tables_accessed"></span>`tables_accessed` (`list[str]`, default `[]`): the tables the query read.
- <span id="benchbox.core.results.timing.QueryTiming.timestamp"></span>`timestamp` (`datetime`, default: now): when the query ran. A directly constructed `QueryTiming` uses the local time as a naive `datetime`; `TimingCollector` sets a UTC-aware value.
- <span id="benchbox.core.results.timing.QueryTiming.thread_id"></span>`thread_id` (`str | None`, default `None`): the executing thread.
- <span id="benchbox.core.results.timing.QueryTiming.connection_id"></span>`connection_id` (`str | None`, default `None`): the connection used.
- <span id="benchbox.core.results.timing.QueryTiming.rows_per_second"></span>`rows_per_second` (`float | None`, default `None`): throughput in rows.
- <span id="benchbox.core.results.timing.QueryTiming.bytes_per_second"></span>`bytes_per_second` (`float | None`, default `None`): throughput in bytes.
- <span id="benchbox.core.results.timing.QueryTiming.cpu_time"></span>`cpu_time` (`float | None`, default `None`): CPU time, as supplied by the caller.
- <span id="benchbox.core.results.timing.QueryTiming.memory_peak"></span>`memory_peak` (`int | None`, default `None`): peak memory, as supplied by the caller.
- <span id="benchbox.core.results.timing.QueryTiming.status"></span>`status` (`str`, default `"SUCCESS"`): the outcome. `TimingAnalyzer` treats `"SUCCESS"` as successful and every other value as not.
- <span id="benchbox.core.results.timing.QueryTiming.error_message"></span>`error_message` (`str | None`, default `None`): the error text for a failed query.
- <span id="benchbox.core.results.timing.QueryTiming.warning_count"></span>`warning_count` (`int`, default `0`): the number of warnings.
- <span id="benchbox.core.results.timing.QueryTiming.platform_metrics"></span>`platform_metrics` (`dict[str, Any]`, default `{}`): platform-specific metrics.

Full signature: `(query_id: str, query_name: Optional[str] = None, execution_sequence: int = 0, execution_time: float = 0.0, parse_time: Optional[float] = None, optimization_time: Optional[float] = None, execution_only_time: Optional[float] = None, fetch_time: Optional[float] = None, timing_breakdown: dict[str, float] = <factory>, rows_returned: int = 0, bytes_processed: Optional[int] = None, tables_accessed: list[str] = <factory>, timestamp: datetime.datetime = <factory>, thread_id: Optional[str] = None, connection_id: Optional[str] = None, rows_per_second: Optional[float] = None, bytes_per_second: Optional[float] = None, cpu_time: Optional[float] = None, memory_peak: Optional[int] = None, status: str = 'SUCCESS', error_message: Optional[str] = None, warning_count: int = 0, platform_metrics: dict[str, typing.Any] = <factory>) -> None`

<span id="benchbox.core.results.timing.QueryTiming.__post_init__"></span>

On construction, `rows_per_second` is set to `rows_returned / execution_time` when both are greater than zero, and `bytes_per_second` is set to `bytes_processed / execution_time` when `execution_time` is greater than zero and `bytes_processed` is not zero. In those cases a value you pass for these two fields is overwritten; otherwise it is kept.

**Returns:** a `QueryTiming` instance with every field set as described above. `timestamp` is the time of construction unless you pass one, and `status` is `"SUCCESS"` unless you pass another value.

#### `to_dict()`

<span id="benchbox.core.results.timing.QueryTiming.to_dict"></span>

**Returns:** a JSON-ready `dict` of all fields. The key for `execution_time` is `execution_time_seconds`, and `timestamp` is an ISO 8601 string.

#### Example

```python
from benchbox.core.results.timing import QueryTiming

timing = QueryTiming(
    query_id="Q1",
    query_name="Pricing Summary Report",
    execution_time=1.234,
    parse_time=0.015,
    optimization_time=0.042,
    execution_only_time=1.150,
    fetch_time=0.027,
    rows_returned=4,
    bytes_processed=1024 * 1024,
    tables_accessed=["lineitem", "orders"],
    status="SUCCESS",
)

print(f"Query: {timing.query_id}")
print(f"Total time: {timing.execution_time:.3f}s")
print(f"Throughput: {timing.rows_per_second:.0f} rows/s")
print(f"Data rate: {timing.bytes_per_second / 1024 / 1024:.2f} MB/s")
print(timing.to_dict()["execution_time_seconds"])
```

```text
Query: Q1
Total time: 1.234s
Throughput: 3 rows/s
Data rate: 0.81 MB/s
1.234
```

### Anonymization Manager

<span id="benchbox.core.results.anonymization.AnonymizationManager"></span>

Removes or replaces identifying data in results so they can be shared.

**Import:** `from benchbox.core.results.anonymization import AnonymizationManager` · **Extras:** none

#### Parameters

<span id="benchbox.core.results.anonymization.AnonymizationManager.__init__"></span>

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `config` | `AnonymizationConfig` or `None` | `None` | The settings. `None` uses `AnonymizationConfig()`. |

**Returns:** an `AnonymizationManager` instance. Its `config` attribute is the configuration you passed, or a default `AnonymizationConfig()` when you passed `None`.

#### `get_anonymous_machine_id()`

<span id="benchbox.core.results.anonymization.AnonymizationManager.get_anonymous_machine_id"></span>
<span id="get_anonymous_machine_id"></span>

**Returns:** `str`, an identifier used in anonymized results. The value recurs across related output and should not be treated as an anonymity or trust signal.

```python
from benchbox.core.results.anonymization import AnonymizationManager

machine_id = AnonymizationManager().get_anonymous_machine_id()
```

#### `anonymize_result_payload(payload)`

<span id="benchbox.core.results.anonymization.AnonymizationManager.anonymize_result_payload"></span>
<span id="anonymize_result_payload"></span>

Anonymizes a whole result payload in one pass.

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `payload` | `dict[str, Any]` | required | The result payload. It is not modified. |

**Returns:** a new `dict`. Matching is by key name:

- Values under secret-looking keys (such as `password`) become `<redacted>`.
- Identifier values such as host names are replaced with pseudonyms. A pseudonym is not identity or provenance evidence.
- Local file paths in values, including inside free text, are replaced, and PII matches in free text become `[REDACTED]`.
- Some keys that identify a location, such as `working_dir`, are dropped.

Running the method again on its own output returned the same payload in the example below.

```python
payload = {
    "working_dir": "/home/alice/project",
    "rows": 100,
    "platform": {"name": "duckdb", "password": "hunter2", "host": "db.example.com"},
    "note": "see /home/alice/x and bob@corp.com",
}

public_payload = manager.anonymize_result_payload(payload)
print(public_payload)
print(manager.anonymize_result_payload(public_payload) == public_payload)
```

```text
{'rows': 100, 'platform': {'name': 'duckdb', 'password': '<redacted>', 'host': '<host pseudonym>'}, 'note': 'see <path pseudonym> and [REDACTED]'}
True
```

#### `anonymize_tuning_payload(payload)`

<span id="benchbox.core.results.anonymization.AnonymizationManager.anonymize_tuning_payload"></span>
<span id="anonymize_tuning_payload"></span>

Anonymizes a tuning companion payload with identifier values consistent with the related result payload.

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `payload` | `dict[str, Any]` | required | The tuning payload. It is not modified. |

**Returns:** a new `dict`. Table and column names in `requested.constraints` and `requested.table_tunings` become `table_` and `column_` pseudonyms (the table names used as keys of `table_tunings` are replaced too). A `source_file` that is a plain repository-relative reference such as `tunings/tpch/duckdb.yaml` is kept; any other value, such as an absolute path, is replaced by a `path_` pseudonym.

```python
tuning = {
    "source_file": "tunings/tpch/duckdb.yaml",
    "requested": {"table_tunings": {"orders": {"sorting": [{"name": "o_orderdate"}]}}},
}
print(manager.anonymize_tuning_payload(tuning))
print(manager.anonymize_tuning_payload({"source_file": "/home/alice/t.yaml"}))
```

```text
{'requested': {'table_tunings': {'<table pseudonym>': {'sorting': [{'name': '<column pseudonym>'}]}}}, 'source_file': 'tunings/tpch/duckdb.yaml'}
{'source_file': '<path pseudonym>'}
```

#### `remove_pii(text)`

<span id="benchbox.core.results.anonymization.AnonymizationManager.remove_pii"></span>
<span id="remove_pii"></span>

Replaces matches of the configured patterns in a string.

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `text` | `str` | required | The text to clean. |

**Returns:** `str`. Every match of `pii_patterns` becomes `[REDACTED]`, then every match of a `custom_sanitizers` key is replaced by its value. Matching ignores case. An empty string (or `None`) is returned as it is.

```python
text = "Contact john@example.com or call 192.168.1.1"
print(manager.remove_pii(text))
```

```text
Contact [REDACTED] or call [REDACTED]
```

```{note}
**Migrating from the pre-consolidation API.** `anonymize_system_profile()`, `sanitize_path()`, `anonymize_query_metadata()`, and `validate_anonymization()` were removed. Call `anonymize_result_payload` on the whole payload instead - it applies all of those transforms in one pass. To confirm a payload is publishable, use `benchbox.core.results.anonymization.find_public_path_leaks`, which returns the offending field paths and replaces the old `validate_anonymization` check.
```

### Anonymization Config

<span id="benchbox.core.results.anonymization.AnonymizationConfig"></span>

A dataclass with the anonymization settings.

**Import:** `from benchbox.core.results.anonymization import AnonymizationConfig` · **Extras:** none

#### Parameters

<span id="benchbox.core.results.anonymization.AnonymizationConfig.__init__"></span>

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `machine_id_salt` | `str` or `None` | `None` | <span id="benchbox.core.results.anonymization.AnonymizationConfig.machine_id_salt"></span>Optional salt used by anonymization for identifier fields; the salt value is not included in the exported result. |
| `pii_patterns` | `list[str]` | email, IPv4 and US SSN patterns | <span id="benchbox.core.results.anonymization.AnonymizationConfig.pii_patterns"></span>Regular expressions that `remove_pii` replaces with `[REDACTED]`. The default list matches IPv4 addresses, email addresses and strings shaped like `123-45-6789`. Passing `[]` turns these matches off. |
| `custom_sanitizers` | `dict[str, str]` | `{}` | <span id="benchbox.core.results.anonymization.AnonymizationConfig.custom_sanitizers"></span>Maps a regular expression to its replacement text, applied by `remove_pii` after the built-in patterns. |

Full signature: `(machine_id_salt: Optional[str] = None, pii_patterns: list[str] = <factory>, custom_sanitizers: dict[str, str] = <factory>) -> None`

**Returns:** an `AnonymizationConfig` instance with the three parameters as attributes of the same name. Nothing is read from the environment; use `from_public_environ` for that.

**Raises:** `TypeError` for an unknown argument. The options `include_machine_id`, `anonymize_paths`, `allowed_path_prefixes`, `include_system_profile`, `anonymize_hostnames` and `anonymize_usernames` were removed: path, host name and user name handling always applies. Drop them from existing configurations.

#### `from_public_environ(*, environ=None, require_salt=False)`

<span id="benchbox.core.results.anonymization.AnonymizationConfig.from_public_environ"></span>

Class method that builds a configuration whose `machine_id_salt` comes from the `BENCHBOX_MACHINE_ID_SALT` environment variable.

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `environ` | `dict[str, str]` or `None` | `None` | The environment to read. `None` reads `os.environ`. |
| `require_salt` | `bool` | `False` | Raise instead of allowing an empty salt. |

**Returns:** an `AnonymizationConfig`. The salt is the stripped variable value, or `None` when it is unset or blank.

**Raises:** `MissingPublicPseudonymSaltError` (a `ValueError` subclass in `benchbox.core.results.anonymization`) when `require_salt` is true and no salt is set.

#### Example

```python
from benchbox.core.results.anonymization import AnonymizationConfig, AnonymizationManager

config = AnonymizationConfig(
    custom_sanitizers={
        r"customer_\d+": "customer_[REDACTED]",
        r"project_[a-z]+": "project_[REDACTED]",
    },
)

manager = AnonymizationManager(config)
print(manager.remove_pii("see CUSTOMER_42 and a@b.io"))
print(AnonymizationConfig.from_public_environ(environ={"BENCHBOX_MACHINE_ID_SALT": " example "}).machine_id_salt is not None)
```

```text
see customer_[REDACTED] and [REDACTED]
True
```

### Display Utilities

<span id="benchbox.core.results.display.display_results"></span>

Prints a short summary of one benchmark result dictionary to standard output.

**Import:** `from benchbox.core.results.display import display_results` · **Extras:** none

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `result_data` | `dict[str, Any]` | required | The result summary. The function reads `benchmark`, `scale_factor`, `platform`, `success`, `total_queries`, `successful_queries`, `total_execution_time`, `average_query_time`, `total_duration`, `schema_creation_time` and `data_loading_time`. Missing keys are skipped or shown as `unknown`. |
| `verbosity` | `int` | `0` | At 1 or more, `total_duration`, `schema_creation_time` and `data_loading_time` are also shown, but only when `total_duration` is present. There is no further difference between levels. |

Full signature: `(result_data: 'dict[str, Any]', verbosity: 'int' = 0) -> 'None'`

#### Returns

`None`. The output lines are `Benchmark`, `Scale Factor`, `Platform`, `Benchmark Status` (`PASSED` when `success` is true, `FAILED` otherwise), `Queries: <successful>/<total> successful`, and, only for a successful run, `Query Execution Time` and `Average Query Time`. Durations print as seconds (`12.345s`) or milliseconds (`561.0ms`). The last line is `✅ <NAME> benchmark completed!` for a successful run and `❌ <NAME> benchmark failed.` otherwise, where `<NAME>` is the upper-cased `benchmark` value (`UNKNOWN` when missing).

#### Raises

`KeyError` when `successful_queries` is present without `total_queries`.

#### Example

```python
from benchbox.core.results.display import display_results

result_data = {
    "benchmark": "tpch",
    "scale_factor": 0.01,
    "platform": "duckdb",
    "success": True,
    "total_queries": 22,
    "successful_queries": 22,
    "total_execution_time": 12.345,
    "average_query_time": 0.561,
}

display_results(result_data, verbosity=1)
```

```text
Benchmark: TPCH
Scale Factor: 0.01
Platform: duckdb

Benchmark Status: PASSED
Queries: 22/22 successful
Query Execution Time: 12.345s
Average Query Time: 561.0ms

✅ TPCH benchmark completed!
```

## Usage Examples

### Complete Result Analysis Workflow

Full workflow from benchmark execution to comparison:

```python
from benchbox.tpch import TPCH
from benchbox.platforms.duckdb import DuckDBAdapter
from benchbox.core.results.exporter import ResultExporter

benchmark = TPCH(scale_factor=0.01, output_dir="tpch_data")
adapter = DuckDBAdapter()

exporter = ResultExporter(output_dir="results")

baseline_results = adapter.run_benchmark(benchmark, query_subset=[1, 3, 6])
baseline_json = exporter.export_result(baseline_results, formats=["json", "csv", "html"])["json"]

current_results = adapter.run_benchmark(benchmark, query_subset=[1, 3, 6])
current_json = exporter.export_result(current_results, formats=["json"])["json"]

comparison = exporter.compare_results(baseline_json, current_json)

for metric, change in comparison["performance_changes"].items():
    print(f"{metric}: {change['baseline']:.4f}s -> {change['current']:.4f}s ({change['change_percent']:+.2f}%)")

report_path = exporter.export_comparison_report(comparison)
print(f"Comparison report: {report_path}")

summary = comparison["summary"]
print(summary["overall_assessment"], summary["total_queries_compared"], summary["improved_queries"], summary["regressed_queries"])
```

The adapter and exporter print their own progress lines. The lines from the script look like this; the timings and the report name change on every run:

```text
total_execution_time: 0.0055s -> 0.0042s (-23.64%)
average_query_time: 0.0018s -> 0.0014s (-22.22%)
Comparison report: results/comparison_report_20261003_193352.html
significant_improvement 3 3 0
```

### Detailed Timing Analysis

Collect and analyze query timing:

```python
import duckdb
from benchbox.core.results.timing import TimingCollector, TimingAnalyzer

conn = duckdb.connect()
conn.execute("CREATE TABLE lineitem AS SELECT range AS l_orderkey, range % 50 AS l_quantity FROM range(100000)")

queries = {
    "Q1": "SELECT COUNT(*) FROM lineitem",
    "Q2": "SELECT l_orderkey, COUNT(*) FROM lineitem GROUP BY l_orderkey LIMIT 10",
    "Q3": "SELECT AVG(l_quantity) FROM lineitem",
}

collector = TimingCollector(enable_detailed_timing=True)

for query_id, sql in queries.items():
    with collector.time_query(query_id, f"Query {query_id}"):
        with collector.time_phase(query_id, "execute"):
            cursor = conn.execute(sql)
        with collector.time_phase(query_id, "fetch"):
            rows = cursor.fetchall()
        collector.record_metric(query_id, "rows_returned", len(rows))
        collector.record_metric(query_id, "tables_accessed", ["lineitem"])

timings = collector.get_completed_timings()
analyzer = TimingAnalyzer(timings)

stats = analyzer.get_basic_statistics()
print(f"Queries: {stats['count']}, mean {stats['mean']:.4f}s, median {stats['median']:.4f}s")

for p, value in analyzer.get_percentiles([50, 90]).items():
    print(f"P{int(p)}: {value:.4f}s")

analysis = analyzer.analyze_query_performance()
for phase, phase_stats in analysis["timing_phases"].items():
    print(f"{phase}: {phase_stats['count']} samples, {phase_stats['mean']:.4f}s avg")

print([(t.query_id, t.rows_returned) for t in timings])
print(analyzer.identify_outliers(method="iqr", factor=1.5))
```

```text
Queries: 3, mean 0.0154s, median 0.0015s
P50: 0.0015s
P90: 0.0352s
execute: 3 samples, 0.0152s avg
fetch: 3 samples, 0.0001s avg
[('Q1', 1), ('Q2', 10), ('Q3', 1)]
[]
```

The timing values change on every run; the shape of the output does not.

### Privacy-Preserving Result Export

Export results with anonymization and check the exported file for private paths:

```python
import json

from benchbox.core.results.anonymization import AnonymizationConfig, find_public_path_leaks
from benchbox.core.results.exporter import ResultExporter

anon_config = AnonymizationConfig(
    custom_sanitizers={r"project_\w+": "[PROJECT]"},
)

exporter = ResultExporter(output_dir="public_results", anonymize=True, anonymization_config=anon_config)
files = exporter.export_result(results, formats=["json", "html"])

with open(files["json"]) as f:
    anonymized_data = json.load(f)

print(anonymized_data["export"]["anonymized"])
leaks = find_public_path_leaks(anonymized_data)
print("public-path leaks:", leaks)
```

```text
True
public-path leaks: []
```

`find_public_path_leaks` returns the dotted field paths that still hold a private absolute path. It checks paths only; it does not check for other personal data. Path, host name and user name handling always applies, so only the salt and the PII patterns are configurable.

### Regression Detection

Automated regression detection across benchmark runs:

```python
from pathlib import Path
from benchbox.core.results.exporter import ResultExporter


def check_for_regressions(baseline_file: Path, current_file: Path, threshold: float = 10.0) -> bool:
    exporter = ResultExporter(output_dir="results")
    comparison = exporter.compare_results(baseline_file, current_file)

    if "error" in comparison:
        print(f"Comparison failed: {comparison['error']}")
        return False

    mean_change = comparison["performance_changes"].get("average_query_time")
    if not mean_change:
        print("No performance data available")
        return True

    change_pct = mean_change["change_percent"]
    if change_pct > threshold:
        regressed = [
            q for q in comparison["query_comparisons"]
            if not q["improved"] and q["change_percent"] > threshold
        ]
        print(f"Regression: {change_pct:+.2f}% slower, {len(regressed)} queries over {threshold}%")
        return False

    print(f"No regression: {change_pct:+.2f}%")
    return True


files = sorted(Path("results").glob("tpch_*.json"))
print(check_for_regressions(files[0], files[1]))
print(check_for_regressions(files[0], Path("missing.json")))
```

```text
No regression: -22.22%
True
Comparison failed: Failed to load one or both result files
False
```

(The exporter also logs `Failed to load result from missing.json` to the error log before the second result.) In a CI job, pass the returned value to `sys.exit(0 if passing else 1)`.

## Best Practices

1. **Always Export Results**

   Export results for future comparison and analysis:

   ```python
   from benchbox.core.results.exporter import ResultExporter

   exporter = ResultExporter(output_dir="results")
   exporter.export_result(results, formats=["json", "csv"])
   ```

2. **Enable Anonymization for Shared Results**

   Anonymize results before sharing them publicly, then review the output for sensitive fields; anonymization does not guarantee that all identifying data is removed.

   ```python
   public_exporter = ResultExporter(anonymize=True)

   internal_exporter = ResultExporter(anonymize=False)
   ```

3. **Track Baselines for Regression Detection**

   Maintain baseline results for each major configuration:

   ```python
   baseline_exporter = ResultExporter(output_dir="baselines")
   baseline_files = baseline_exporter.export_result(results, formats=["json"])

   comparison = exporter.compare_results(baseline_files["json"], current_path)
   ```

4. **Use Detailed Timing for Optimization**

   Collect phase timings to identify optimization opportunities:

   ```python
   collector = TimingCollector(enable_detailed_timing=True)

   analyzer = TimingAnalyzer(timings)
   analysis = analyzer.analyze_query_performance()

   for phase, stats in analysis["timing_phases"].items():
       if stats["mean"] > 1.0:
           print(f"Bottleneck: {phase} taking {stats['mean']:.2f}s")
   ```

5. **Monitor for Outliers**

   Identify and investigate timing outliers:

   ```python
   analyzer = TimingAnalyzer(timings)
   outliers = analyzer.identify_outliers(method="iqr", factor=1.5)

   for outlier in outliers:
       print(f"{outlier.query_id}: {outlier.execution_time:.3f}s")
   ```

## Common Issues

### Comparison Schema Mismatch

**Problem**: `compare_results` returns an `error` entry, or a comparison that shows `-100%` changes and no `summary`.

**Cause**: An `error` entry means a file could not be read or parsed; `baseline_loaded` and `current_loaded` show which one failed. `compare_results` does not check that a file is a result. A valid JSON file in another format compares without an error, reports `current_version` as `unknown` or an old version, has zero times, and has an empty `query_comparisons` list.

**Solution**:

```python
from pathlib import Path

comparison = exporter.compare_results(Path(baseline_path), Path(current_path))

if "error" in comparison:
    print(f"Comparison error: {comparison['error']}")
    print(comparison["baseline_loaded"], comparison["current_loaded"])
```

Check that both paths exist, that you passed `Path` objects, and that both files were exported by `export_result` (look for a non-empty `query_comparisons` list and matching `baseline_version` and `current_version`).

### Missing Timing Data

**Problem**: No detailed timing information available

**Solution**:

```python
collector = TimingCollector(enable_detailed_timing=True)

with collector.time_query("Q1") as timing:
    with collector.time_phase("Q1", "execute"):
        pass
```

`time_phase` records nothing unless detailed timing is enabled and the `query_id` matches a `time_query` block that is still open.

### Anonymization Validation Failures

**Problem**: PII detected in anonymized results

**Solution**:

```python
from benchbox.core.results.anonymization import (
    AnonymizationConfig,
    AnonymizationManager,
    find_public_path_leaks,
)

config = AnonymizationConfig(
    custom_sanitizers={
        r"your_pattern": "[REDACTED]",
    }
)

manager = AnonymizationManager(config)

payload_under_test = {"working_dir": "/home/alice/project", "rows": 100}
anonymized = manager.anonymize_result_payload(payload_under_test)

for path in find_public_path_leaks(anonymized):
    print(f"Address: {path}")
```

`custom_sanitizers` is applied by `remove_pii`, which `anonymize_result_payload` also applies to message text such as `note` and `error_message` fields. `anonymize_result_payload` already removes `working_dir`, so the loop above prints nothing for this payload.

## See Also

- {doc}`results` - Result models and data structures
- {doc}`/usage/examples` - Usage examples
- {doc}`/usage/troubleshooting` - Troubleshooting guide
- {doc}`utilities` - Other utility functions
- {doc}`/testing/index` - Testing and validation

### External Resources

- [Rich Console Documentation](https://rich.readthedocs.io/) - Terminal formatting
- [Python Statistics Module](https://docs.python.org/3/library/statistics.html) - Statistical functions
- [JSON Schema](https://json-schema.org/) - Result schema validation
