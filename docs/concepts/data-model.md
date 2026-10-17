<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Data Model

```{tags} concept, intermediate, validation
```

Understanding BenchBox's result schema, data structures, and serialization formats.

## Overview

BenchBox produces structured, machine-readable results that enable:
- **Reproducibility**: Complete execution metadata for result verification
- **Comparison**: Standardized format for cross-platform and temporal analysis
- **Integration**: JSON schema compatible with analysis tools and databases
- **Compliance**: TPC-compatible metric calculation and validation

## Result Schema Hierarchy

Result files use schema v2 (`"result_schema_version": "2.2"`):

```
Result file
├── run (id, timestamp, total_duration_ms, query_time_ms, iterations, streams)
├── benchmark (id, name, scale_factor, test_type, compliance_class)
├── platform (driver_package, driver versions, execution_mode, config)
├── summary
│   ├── queries (total, passed, failed)
│   ├── timing (total_ms, avg_ms, geometric_mean_ms, p50/p90/p95/..., min/max)
│   ├── validation ("passed", "failed", or "partial")
│   └── tpc_metrics (power/throughput values, or suppression reason)
├── queries (one entry per query execution)
├── phases (data_generation, schema_creation, data_loading, power_test, ...)
├── tables (row counts per table)
├── config (full run configuration)
└── environment (OS, CPU, memory, Python, machine metadata)
```

Older files use schema v1; see [Result Schema v1 Reference](../reference/result-schema-v1.md).

## Core Data Structures

### Run Summary

Top-level timing and counts.

**Key Fields**:
- `run.total_duration_ms`: end-to-end wall-clock time.
- `run.query_time_ms`: time spent executing queries.
- `run.iterations` / `run.streams`: measurement iterations and concurrent streams.
- `summary.timing.total_ms`, `avg_ms`, `geometric_mean_ms`: aggregate query timing.
- `summary.queries.total/passed/failed`: execution counts across all iterations and streams.
- `summary.validation`: `passed`, `failed`, or `partial`.
- `benchmark.compliance_class`: for example `unofficial_subscale` below SF1. Subscale TPC-H and TPC-DS runs record
  `summary.tpc_metrics.power_at_size` with `"unofficial": true`; it compares runs within the subscale cohort and is not a
  TPC result. `unofficial_nonstandard` runs record `{"suppressed": true}` instead.

**Example** (trimmed from a real TPC-H SF0.01 DuckDB run; `tpc_metrics` shows the form current releases write for a
subscale run):
```json
{
  "result_schema_version": "2.2",
  "benchmark": {"id": "tpch", "name": "TPC-H", "scale_factor": 0.01},
  "run": {"total_duration_ms": 2426, "query_time_ms": 966, "iterations": 3},
  "summary": {
    "queries": {"total": 66, "passed": 66, "failed": 0},
    "timing": {"total_ms": 966.0, "avg_ms": 14.6, "geometric_mean_ms": 14.5},
    "validation": "passed",
    "tpc_metrics": {"unofficial": true, "reason": "compliance_class=unofficial_subscale", "power_at_size": 4503.0}
  }
}
```

The fields fall into these groups:

- **Identification**: `benchmark.id` (for example `"tpch"`, `"tpcds"` or `"clickbench"`), `platform.driver_package` (for example `"duckdb"` or `"snowflake"`), `run.id` (a unique run identifier) and `run.timestamp` (an ISO 8601 timestamp).
- **Configuration**: `benchmark.scale_factor` (the data size multiplier) and `benchmark.test_type` (`"power"`, `"throughput"`, or similar).
- **Timing summary**: `run.total_duration_ms` is the total execution time, `run.query_time_ms` covers query execution only, and `summary.timing` breaks down measured query time.
- **Query statistics**: `summary.queries.total` counts executions attempted (22 queries × 3 measurement runs = 66 here), with `passed`/`failed` breakdowns.
- **Validation**: `summary.validation` is `"passed"`, `"failed"` or `"partial"`.
- **System context**: `environment` holds hardware and software information, `platform` holds driver versions and run configuration, and `config` holds the full run configuration.

See: [Result Formats](../reference/result-formats.md)

### Query Entries

One entry per query execution (22 queries × (1 warm-up + 3 measurement) iterations = 88 entries here).

**Structure**:
```python
{
    "id": str,
    "ms": float,
    "rows": int,
    "status": str,
    "stream": int,
    "iter": int,
    "run_type": str,
    "test_type": str,
}
```

Field meanings: `id` is the query id (for example `"Q1"`); `ms` is execution time in milliseconds; `rows` is rows returned; `status` is SUCCESS, ERROR, or TIMEOUT; `run_type` is warmup or measurement; `test_type` is power or throughput.

**Example**:
```json
{
  "id": "Q1",
  "ms": 14.6,
  "rows": 4,
  "status": "SUCCESS",
  "stream": 0,
  "iter": 2,
  "run_type": "measurement",
  "test_type": "power"
}
```


### Execution Phases

The `phases` object records per-phase timing with `duration_ms` and `status` entries for `data_generation`, `schema_creation`, `data_loading`, `power_test`, `throughput_test`, and `validation`. Per-table row counts live in `tables` (`{"lineitem": {"rows": 60175}, ...}`).

Result files do not embed query SQL text. To inspect the SQL a run executes, preview it first with `benchbox run --dry-run <dir>`: the preview writes one `query_<id>.sql` file per query.

## System Profile

Hardware and software context for reproducibility lives in `environment` (`os`, `cpu_model`, `cpu_count`, `memory_gb`, `python`, plus machine metadata). The summary box on the console prints the same context (OS, Python, CPUs, memory, driver).

## Platform Info

Platform-specific metadata lives in `platform`: `driver_package`, resolved and actual driver versions, `execution_mode` (`sql` or `dataframe`), and the effective `config` (connection mode, memory limit, thread limit, and any `--platform-option` values).

**Example** (trimmed from a real DuckDB run):
```json
{
  "driver_package": "duckdb",
  "driver_version_resolved": "1.5.5",
  "driver_version_actual": "1.5.5",
  "config": {
    "connection_mode": "file",
    "execution_mode": "sql",
    "memory_limit": "8GB"
  }
}
```

## Validation Results

Correctness verification is the run-level `summary.validation` verdict (`passed`, `failed`, or `partial`), computed by comparing per-query row counts against expected values. Per-query entries carry the outcome as `status` (`SUCCESS`, `ERROR`, or `TIMEOUT`); a failed query includes an error message.

## Serialization Formats

### JSON (Primary Format)

**Default output format** for all benchmark results.

**Features**:
- Human-readable
- Standard library support (Python `json` module)
- Compatible with analysis tools (jq, pandas, etc.)

**Example**:
```bash
cat results.json | jq '.queries[] | {id, ms}'
```

```python
import json
import pandas as pd
df = pd.DataFrame(json.load(open("results.json"))["queries"])
```

The first command pretty-prints the query entries with jq. The Python snippet loads the query entries into pandas.

### CSV (Export Format)

**Use case**: Simplified analysis in spreadsheets

```bash
benchbox export results.json --format csv --output-dir ./
```

This exports the query results to CSV.

**CSV Columns** (from a real export):
```
query_id,execution_time_ms,rows_returned,status,error_message,iteration,stream
14,21.4,1,SUCCESS,,0,0
2,12.8,4,SUCCESS,,0,0
...
```

### Parquet (Archival Format)

**Use case**: Long-term storage, data lakes

```python
import pandas as pd
import json

with open("results.json") as f:
    data = json.load(f)

query_data = []
for q in data["queries"]:
    query_data.append({
        "query_id": q["id"],
        "execution_time_ms": q["ms"],
        "status": q["status"],
    })

df = pd.DataFrame(query_data)
df.to_parquet("results.parquet", compression="snappy")
```

This converts results to Parquet using pandas. It loads the JSON results, extracts the query results, and saves them as a DataFrame in Parquet format.

**Benefits**:
- Compressed (smaller file size)
- Column-oriented (fast analytical queries)
- Schema evolution support

## Working with Results

### Python API

```python
from benchbox.core.results.loader import load_result_file

results, raw = load_result_file("results.json")

print(f"Benchmark: {results.benchmark_name}")
print(f"Duration: {results.duration_seconds:.2f}s")
print(f"Success rate: {results.successful_queries}/{results.total_queries}")

for qr in results.query_results:
    if qr["status"] == "SUCCESS":
        print(f"{qr['query_id']}: {qr['execution_time_ms']:.1f}ms")
```

The example loads results from JSON, accesses their fields, and iterates through the query results.

### Command-Line Tools

```bash
jq '.queries[] | select(.ms > 15)' results.json

jq '{benchmark: .benchmark.id, total_ms: .summary.timing.total_ms, avg_ms: .summary.timing.avg_ms}' results.json

benchbox compare baseline.json current.json
```

The first command selects query entries slower than 15 milliseconds with jq. The second extracts a timing summary. The last compares two result files.

### Analysis Examples

#### Calculate Geometric Mean

```python
import math

query_times = [qr["execution_time_ms"] for qr in results.query_results
               if qr["status"] == "SUCCESS"]
geomean = math.prod(query_times) ** (1.0 / len(query_times))
print(f"Geometric mean: {geomean:.1f}ms")
```

#### Detect Regressions

```python
def compare_results(baseline, current, threshold=1.1):
    baseline_times = {qr["query_id"]: qr["execution_time_ms"]
                      for qr in baseline.query_results}
    current_times = {qr["query_id"]: qr["execution_time_ms"]
                     for qr in current.query_results}

    regressions = []
    for qid in baseline_times:
        if qid in current_times:
            ratio = current_times[qid] / baseline_times[qid]
            if ratio > threshold:
                regressions.append((qid, ratio))

    return regressions
```

#### Export to DataFrame

```python
import pandas as pd

df = pd.DataFrame([
    {
        "query_id": qr["query_id"],
        "execution_time_ms": qr["execution_time_ms"],
        "status": qr["status"],
        "rows_returned": qr["rows_returned"],
    }
    for qr in results.query_results
])

print(df.describe())
print(df.groupby("status").count())
```

The code converts the results to a DataFrame, then analyzes them.

## Schema Evolution

BenchBox uses schema versioning to manage result format evolution.

**Current Version**: v2 (`result_schema_version: "2.2"`)

**Schema Rules**:
- New fields may be added (with defaults)
- Existing fields won't be removed or renamed
- Type changes are breaking (trigger major version)

## Best Practices

### Result Storage

1. **Version Control**: Store result JSON files in git for history tracking
2. **Naming Convention**: Result files are named `{benchmark}_sf{scale}_{platform}_{mode}_{timestamp}_{run-id}.json` (for example `tpch_sf001_duckdb_sql_20261009_230647_9bf6431b.json`); keep those names so `benchbox results` and `benchbox compare` resolve them
3. **Archival**: Compress old results with gzip or convert to Parquet
4. **Metadata**: Include git commit SHA in execution_id for traceability

### Result Analysis

1. **Geometric Mean**: Use for TPC benchmark reporting (not arithmetic mean)
2. **Outliers**: Investigate queries with >3x variance from baseline
3. **Validation**: Always check `summary.validation` before trusting results
4. **Context**: Compare results with matching `environment` values

### Performance Monitoring

1. **Baseline**: Establish baseline results with known-good configuration
2. **Trending**: Track results over time to detect gradual degradation
3. **Alerting**: Set thresholds for acceptable regression (e.g., 10%)
4. **Root Cause**: Cross-reference system_profile for environmental changes

## Related Documentation

- [Result Formats](../reference/result-formats.md) - Current schema v2 field documentation
- [Architecture](architecture.md) - How results fit into system design
- [Workflow](workflow.md) - Result collection in different workflows
- [Performance Monitoring](../advanced/performance.md) - Analyzing results over time
- [TPC Validation](../guides/tpc/tpc-validation-guide.md) - Validation for compliance
