<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# TPC-DS Official Benchmark Guide

```{tags} advanced, guide, tpc-ds, validation
```

This guide provides systematic documentation for the TPC-DS official benchmark implementation in BenchBox, including Power@Size and Throughput@Size. BenchBox does not export the composite QphDS@Size; see [QphDS@Size (not exported)](#qphds-size-not-exported).

> **Derived results.** Derived from TPC-DS. Results produced by BenchBox do not comply with the TPC-DS Specification and must not be compared with official TPC-DS results. The TPC publishes official TPC-DS results only after an audit.

## Overview

The TPC-DS official benchmark implementation coordinates all three test phases and reports Power@Size and Throughput@Size. It does not export the composite QphDS@Size (see below). It includes:

- **Complete benchmark execution** with all three phases
- **Power@Size and Throughput@Size** (no composite QphDS@Size; see below)
- **Comprehensive reporting** and validation
- **Validation framework** integration
- **Audit trail functionality** recording run evidence

### Key Features

- **TPC-DS Metrics**: Reports Power@Size and Throughput@Size (see the deviation note above)
- **Complete Implementation**: Power, Throughput, and Maintenance Tests
- **Metrics**: Power@Size and Throughput@Size (`Q = 99 × S`)
- **Multi-format Reporting**: Text, JSON, CSV, HTML reports
- **Validation Framework**: Comprehensive result validation
- **Error Handling**: Robust error handling and recovery
- **Scalable**: Supports any scale factor
- **Database Agnostic**: Works with multiple database systems

## Quick Start

Run the official TPC-DS benchmark with the CLI (this SF1 DuckDB run completes in about a minute and reports a result in the `official` result class):

```bash
benchbox run --official --platform duckdb --benchmark tpcds --scale 1 --seed 42 --output ./official_data
```

### Programmatic Usage

`TPCDSBenchmark.run_official_benchmark` is deprecated: it warns and delegates to the same throughput driver that `benchbox run` uses. For supported runs use `benchbox run --phases power,throughput` (or `PlatformAdapter.run_benchmark` with the throughput phase). The legacy call requires `adapter=` whenever the throughput test runs and returns a dictionary with `success`, `power_at_size`, `throughput_at_size`, and `errors` keys.

### Installation Requirements

```bash
uv add benchbox

git clone https://github.com/BenchBox-dev/BenchBox
cd BenchBox
```

## TPC-DS Specification

The TPC-DS benchmark is the official Transaction Processing Performance Council decision support benchmark. It models the decision support functions of a retail product supplier.

### Key Characteristics

- **99 Queries**: Complex analytical queries with varying complexity
- **Three Test Phases**: Power, Throughput, and Maintenance Tests
- **Scale Factor**: Determines database size (SF=1 ≈ 1GB)
- **Multi-Stream**: Concurrent execution capability
- **Refresh Functions**: Data maintenance operations

### QphDS@Size (not exported)

The **QphDS@Size** (Queries per Hour at Scale Factor) is the TPC-DS primary composite metric. The specification defines it as:

```
QphDS@Size = SF × Q / (T_PT × T_TT × T_DM × T_LD)^(1/4)
```

Where `Q = 99 × S` (S is the number of throughput streams), `T_PT` is the power test time, `T_TT = TT1 + TT2` is the total throughput test time, `T_DM = DM1 + DM2` is the data maintenance time, and `T_LD = 0.01 × S × T_load` is the weighted load time. TPC-DS has no Power@Size metric.

**Deviation from the specification.** BenchBox does not export QphDS@Size. Earlier releases exported the geometric mean of Power@Size and Throughput@Size under that name. That value is not the specification metric, because it omits the data maintenance and load times and uses a different combination, so results carrying it are not comparable with published TPC-DS results. BenchBox runs a single throughput test with no data maintenance between throughput tests, so the specification formula is not yet implemented. Implementing it is tracked as follow-up work.

BenchBox exports two metrics instead:

- `Power@Size = 3600 × Scale_Factor / geometric_mean(power query times)`
- `Throughput@Size = Q × 3600 × Scale_Factor / Throughput_Test_Time`, with `Q = 99 × S`. Template variants 14a/14b, 23a/23b, 24a/24b and 39a/39b count once, so a stream executes 103 statements but is scored as 99 queries. `Throughput_Test_Time` is the wall-clock interval from the first stream's first query to the last stream's last query; connection setup is excluded.

A result with any failed query, or whose throughput phase failed, carries no Throughput@Size.

## Architecture

The TPC-DS official benchmark implementation consists of several key components:

### Core Components

```
benchbox/core/tpcds/
├── benchmark.py           # Main benchmark class
├── official_benchmark.py  # Official benchmark runner
├── reporting.py          # Comprehensive reporting
├── queries.py            # Query management
├── streams.py            # Stream management
├── generator.py          # Data generation
├── schema.py             # Database schema
└── c_tools.py            # C tool integration
```

### Class Hierarchy

```
TPCDSBenchmark
├── run_official_benchmark(connection, ..., adapter=)
└── TPCDSOfficialBenchmark
    └── run_official_benchmark(connection_factory, config, adapter=)
```

Both `run_official_benchmark` methods are deprecated and require `adapter=` (a platform
adapter) whenever the throughput test runs. The adapter supplies the stream capability
gate and one session per stream; without it they raise `TypeError`. The connection factory
must return a new connection on each call, because each phase closes its connection. They
report Power@Size and Throughput@Size only. The throughput phase uses the adapter's target
dialect; `dialect=` applies to the power and maintenance phases only. For supported runs use
`benchbox run --phases throughput`.

## Usage

### Complete Benchmark

```bash
benchbox run --official --platform duckdb --benchmark tpcds --scale 10 --seed 42 \
  --phases power,throughput --streams 4 --output ./official_data
```
### Individual Phases

The first command runs the Power Test only. The second runs the Throughput Test only.

```bash
benchbox run --official --platform duckdb --benchmark tpcds --scale 10 --seed 42 --phases power --output ./official_data

benchbox run --official --platform duckdb --benchmark tpcds --scale 10 --seed 42 --phases throughput --streams 8 --output ./official_data
```

### Custom Configuration

This example uses advanced-level configuration:

```bash
benchbox run --official --platform duckdb --benchmark tpcds --scale 10 --seed 42 \
  --phases power,throughput --streams 6 --validation full --output ./official_data
```

## Configuration

### Benchmark Parameters

| CLI flag       | Default | Description                        |
| -------------- | ------- | ---------------------------------- |
| `--scale`      | 0.01    | Scale factor (1.0 ≈ 1GB)           |
| `--output`     | —       | Directory for generated data       |
| `--seed`       | —       | Random seed for reproducibility    |
| `--phases`     | power   | Comma-separated phases to run      |
| `--streams`    | 2       | Concurrent throughput streams      |
| `--validation` | —       | Validation mode for the run        |

### Programmatic Parameters

`TPCDSBenchmark.run_official_benchmark` is deprecated (see above). Its legacy keyword parameters map to the CLI flags as follows:

| Legacy parameter    | CLI flag        | Default        |
| ------------------- | --------------- | -------------- |
| `num_streams`       | `--streams`     | 2              |
| `power_test`        | `--phases`      | True           |
| `throughput_test`   | `--phases`      | True           |
| `maintenance_test`  | `--phases`      | True           |
| `result_validation` | `--validation`  | True           |
| `output_dir`        | `--output`      | None           |

## Benchmark Phases

### 1. Power Test

The Power Test measures single-stream query processing power by executing all 99 TPC-DS queries sequentially.

```bash
benchbox run --official --platform duckdb --benchmark tpcds --scale 1 --seed 42 --phases power --output ./official_data
```

Read `Power@Size` from the result file's `summary.tpc_metrics` (see [Understanding Results](../../tutorials/understanding-results.md)).

**Characteristics:**
- Sequential execution of all 99 queries
- Single database connection
- Measures database query processing capability
- Contributes to Power@Size metric

### 2. Throughput Test

The Throughput Test measures concurrent query processing capability by executing multiple streams of queries simultaneously.

```bash
benchbox run --official --platform duckdb --benchmark tpcds --scale 1 --seed 42 --phases throughput --streams 2 --output ./official_data
```

Read `Throughput@Size` from the result file's `summary.tpc_metrics`.

**Characteristics:**
- Multiple concurrent streams (default: 2)
- Each stream executes queries in different order
- Measures concurrent processing capability
- Contributes to Throughput@Size metric

### 3. Maintenance Test

> **⚠️ CRITICAL: Database Reload Required After Maintenance Test**
>
> The Maintenance Test permanently modifies database contents through INSERT, UPDATE, and DELETE
> operations on sales and inventory tables. After running the maintenance phase, you **must reload
> the database** before running power or throughput tests again. Failure to reload will result in
> incorrect benchmark results because queries will execute against modified data.
>
> **Proper workflow:** `generate` → `load` → `power` → `throughput` → `maintenance` → **[RELOAD before next power/throughput]**

The TPC-DS Maintenance Test simulates real-world data warehouse update operations by executing data modification statements (INSERT, UPDATE, DELETE) on sales and inventory tables. Unlike Power and Throughput Tests which only read data, **the Maintenance Test permanently changes database contents** by committing transactions that add, modify, and remove records.

#### What the Maintenance Test Does

TPC-DS maintenance operations simulate ongoing warehouse activity such as:
- **New sales transactions** arriving from retail channels
- **Customer returns** being processed
- **Inventory adjustments** based on physical counts
- **Data corrections** from source systems

The test executes a series of data modification operations across multiple tables, cycling through INSERT, UPDATE, and DELETE statements. Each operation modifies a portion of the database and is committed permanently.

#### Affected Tables

The Maintenance Test targets the following sales and inventory tables:

| Table             | Purpose                  | Operation Types        |
| ----------------- | ------------------------ | ---------------------- |
| `catalog_sales`   | Catalog channel sales    | INSERT, UPDATE, DELETE |
| `catalog_returns` | Catalog channel returns  | INSERT, UPDATE, DELETE |
| `web_sales`       | Web channel sales        | INSERT, UPDATE, DELETE |
| `web_returns`     | Web channel returns      | INSERT, UPDATE, DELETE |
| `store_sales`     | Store channel sales      | INSERT, UPDATE, DELETE |
| `store_returns`   | Store channel returns    | INSERT, UPDATE, DELETE |
| `inventory`       | Product inventory levels | INSERT, UPDATE, DELETE |

#### Data Volumes Modified

The maintenance test runs a small rotation of INSERT, UPDATE, and DELETE operations across the sales tables (a few thousand rows at SF1, scaling with the scale factor). Every operation commits permanently.

#### Why Database Reload Is Required

After running the Maintenance Test, **you must reload the database** before running Power or Throughput Tests again. Here's why:

1. **Data Changes Are Committed**: All INSERT, UPDATE, and DELETE operations are committed to the database. The modified data persists permanently.

2. **Query Results Will Differ**: Power and Throughput queries will execute against the modified dataset, producing different results than the baseline. Aggregate functions (SUM, COUNT, AVG) will calculate different values, and JOIN operations may match different rows.

3. **Not Idempotent**: Running the Maintenance Test multiple times modifies different data each time. There's no simple "undo" operation - you must restore from clean data.

4. **TPC Specification Requirement**: The official TPC-DS specification requires benchmarks run on consistent, unmodified datasets. Running Power/Throughput tests on post-maintenance data violates specification and produces invalid results.

#### Complete Code Example

Here's how to properly structure your benchmark workflow with database reload. The workflow has three steps:

1. Run the Power and Throughput tests on clean data.
2. Reload the database before the Maintenance Test, so the maintenance operations start with clean data.
3. Run the Maintenance Test, which permanently modifies the data. The database then contains modified data.

```python
from benchbox.tpcds import TPCDS
from benchbox.platforms.duckdb import DuckDBAdapter
from pathlib import Path

benchmark = TPCDS(scale_factor=1.0, output_dir=Path("./tpcds_data"))
benchmark.generate_data()

print("Step 1: Running Power and Throughput tests on clean database...")
adapter = DuckDBAdapter(database_path="tpcds.duckdb", force_recreate=True)

power_result = adapter.run_benchmark(benchmark, test_execution_type="power")
print(f"Power Test: {power_result.total_execution_time:.2f}s")

throughput_result = adapter.run_benchmark(benchmark, test_execution_type="throughput")
print(f"Throughput Test: {throughput_result.total_execution_time:.2f}s")

print("\n⚠️  Reloading database before Maintenance Test...")
adapter = DuckDBAdapter(database_path="tpcds.duckdb", force_recreate=True)

print("\nStep 3: Running Maintenance Test (will modify database)...")
maintenance_result = adapter.run_benchmark(benchmark, test_execution_type="maintenance")
print(f"Maintenance Test: {maintenance_result.total_execution_time:.2f}s")
print(f"Operations executed: {maintenance_result.total_queries}")

print("\n" + "=" * 70)
print("⚠️  WARNING: DATABASE HAS BEEN MODIFIED")
print("=" * 70)
print("The Maintenance Test permanently modified sales and inventory tables by:")
print(f"  • Inserting new sales/return/inventory records")
print(f"  • Updating existing records")
print(f"  • Deleting old records")
print()
print("To run Power or Throughput tests again, you MUST reload the database")
print("with fresh data. The current database contains modified data that will")
print("produce incorrect benchmark results.")
print("=" * 70)
```

#### CLI Usage

Run the Maintenance Test using the BenchBox CLI. The first command is the complete workflow up to the Throughput
Test. The second reloads the database before running maintenance:

```bash
benchbox run \
  --platform duckdb \
  --benchmark tpcds \
  --scale 1.0 \
  --phases generate,load,power,throughput

benchbox run \
  --platform duckdb \
  --benchmark tpcds \
  --scale 1.0 \
  --phases load,maintenance
```

#### Workflow Summary

```
✓ Correct:   generate → load → power → throughput → maintenance
✓ Correct:   generate → load → power → throughput → maintenance → [RELOAD] → power (if rerunning)
✗ Incorrect: generate → load → power → maintenance → throughput  ❌ (throughput runs on modified data!)
✗ Incorrect: generate → load → maintenance → power → throughput  ❌ (power/throughput run on modified data!)
```

**Key Principle**: Maintenance Test can run immediately after Power/Throughput (no reload needed before Maintenance). However, you **MUST reload** after Maintenance before running Power/Throughput again.

#### Access Maintenance Test Results

Maintenance results use the same result-file shape as power and throughput runs: per-query entries in `queries`, phase timing in `phases.maintenance`, and the run verdict in `summary.validation`.

## Metrics and Calculations

### TPC-DS Derived Metrics

The implementation calculates Power@Size and Throughput@Size. It does not calculate the composite QphDS@Size (see [QphDS@Size (not exported)](#qphds-size-not-exported)):

```python
from benchbox.core.results.loader import load_result_file

results, raw = load_result_file("<runs-root>/results/tpcds_sf1_duckdb_sql_<timestamp>_<id>.json")

print(f"Power@Size: {results.power_at_size:.2f}")
print(f"Throughput@Size: {results.throughput_at_size}")
```

### Calculation Details

#### Power@Size
```
Power@Size = (3600 × Scale_Factor) / geometric_mean(power query times in seconds)
```

#### Throughput@Size
```
Throughput@Size = (99 × Num_Streams × 3600 × Scale_Factor) / Throughput_Test_Time
```

### Additional Metrics

The result file also carries per-phase detail:

```python
timing = raw["summary"]["timing"]
print(f"Average Query Time: {timing['avg_ms']:.1f}ms")
queries = raw["summary"]["queries"]
print(f"Success Rate: {queries['passed']}/{queries['total']}")
print(f"Geometric Mean: {timing['geometric_mean_ms']:.1f}ms")
```

## Reporting

The benchmark generates systematic reports in multiple formats automatically:

### Report Types

1. **Executive Summary** (`executive_summary.txt`)
   - High-level metrics and results
   - Power@Size, Throughput@Size and phase results
   - Overall benchmark status

2. **Detailed Analysis** (`detailed_analysis.txt`)
   - Phase-by-phase breakdown
   - Metric calculations
   - Performance analysis

3. **Query Analysis** (`query_analysis.txt`)
   - Query-level performance data
   - Execution times and statistics
   - Failure analysis

4. **JSON Export** (`benchmark_results.json`)
   - Machine-readable results
   - Complete data export
   - API integration ready

5. **CSV Export** (`query_results.csv`)
   - Spreadsheet-compatible format
   - Query execution data
   - Statistical analysis ready

6. **HTML Report** (`benchmark_report.html`)
   - Standalone report with formatted tables
   - Summary metrics and system information
   - Presentation ready

7. **Validation Checklist Report** (`validation_checklist_report.txt`)
   - Validation checklist
   - Validation results
   - Derived-results disclaimer

8. **Performance Summary** (`performance_summary.txt`)
   - Performance recommendations
   - Optimization suggestions
   - Benchmark insights

### Accessing Reports

Derive shareable artifacts from a result file with `benchbox export`:

```bash
benchbox export <runs-root>/results/tpcds_sf1_duckdb_sql_<timestamp>_<id>.json --format csv --output-dir ./reports/

benchbox export --last --format html --output-dir ./reports/
```

### Custom Reporting

Pipeline embedders can generate the full report set programmatically:

```python
from pathlib import Path
from benchbox.core.tpcds.reporting import TPCDSReportGenerator

generator = TPCDSReportGenerator(output_dir=Path("/custom/path"))

reports = generator.generate_complete_report(result)
```

`result` is the internal `BenchmarkResult` produced by the TPC-DS phase drivers; the returned dict maps report names (`executive_summary`, `detailed_analysis`, `query_analysis`, `json_report`, `csv_export`, `html_report`, `validation_checklist_report`, `performance_summary`) to their files under `<output_dir>/reports/`.

## Validation

### Built-in Validation

The benchmark validates results against the TPC-DS specification. Request full validation and read the verdict from the result file:

```bash
benchbox run --official --platform duckdb --benchmark tpcds --scale 1 --seed 42 --validation full --output ./official_data
```

```python
from benchbox.core.results.loader import load_result_file

results, raw = load_result_file("<runs-root>/results/tpcds_sf1_duckdb_sql_<timestamp>_<id>.json")
print(f"Validation: {raw['summary']['validation']}")
```

### Validation Checklist

The validation framework checks:

- All 99 queries executed in Power Test
- Multi-stream execution in Throughput Test
- Refresh functions executed in Maintenance Test
- No query failures or errors
- Proper parameter generation
- Correct metric calculations
- Result data integrity
- Timing measurements accuracy

### Manual Validation

```python
from benchbox.core.results.loader import load_result_file

results, raw = load_result_file("<runs-root>/results/tpcds_sf1_duckdb_sql_<timestamp>_<id>.json")
passed = raw["summary"]["queries"]["passed"]
total = raw["summary"]["queries"]["total"]
print(f"Success rate: {passed}/{total}")
print(f"Validation: {raw['summary']['validation']}")
```

## Advanced-level Usage

### Custom Database Integration

Custom databases connect through platform adapters, not subclasses. Pick the adapter for the platform (for example `DuckDBAdapter`, `SnowflakeAdapter`), configure it with the platform's connection options, and run the benchmark phases with `benchbox run --platform <name>`. See the [platform guides](../../platforms/index.md) for per-platform setup.

### Performance Tuning

For large scale factors, add streams for throughput. Skip the Maintenance Test if you do not need it:

```bash
benchbox run --official --platform duckdb --benchmark tpcds --scale 100 --seed 42 \
  --phases power,throughput --streams 16 --output ./official_data
```

### Integration with CI/CD

Fail the job when Throughput@Size drops below a minimum threshold (100 here is an example). `jq -e` exits non-zero when the check is false or the metric is missing:

```bash
export BENCHBOX_OUTPUT_DIR=./ci_data
benchbox run --official --platform duckdb --benchmark tpcds --scale 1 --seed 42 \
  --phases throughput --streams 2 --output ./ci_data

jq -e '.summary.tpc_metrics.throughput_at_size > 100' ./ci_data/results/*.json
```

### Batch Processing

```bash
for sf in 1.0 10.0; do
  benchbox run --official --platform duckdb --benchmark tpcds --scale $sf --seed 42 --output ./official_data
done

benchbox results --limit 3
```

## Troubleshooting

### Common Issues

#### 1. Database Connection Issues

If the connection fails, check the platform setup, verify that the database is running, and check the
credentials. See the [platform guides](../../platforms/index.md).

```bash
benchbox check-deps --platform duckdb
```

#### 2. Memory Issues with Large Scale Factors

For large scale factors, use fewer streams and monitor memory usage:

```bash
benchbox run --official --platform duckdb --benchmark tpcds --scale 100 --seed 42 \
  --phases power --streams 2 --output ./official_data
```

```python
import psutil
print(f"Memory usage: {psutil.virtual_memory().percent}%")
```

#### 3. Query Timeouts

Split the phases so a slow phase can be retried alone:

```bash
benchbox run --official --platform duckdb --benchmark tpcds --scale 10 --seed 42 \
  --phases power --output ./official_data

benchbox run --official --platform duckdb --benchmark tpcds --scale 10 --seed 42 \
  --phases throughput --streams 2 --output ./official_data
```

#### 4. Incomplete Results

```python
from benchbox.core.results.loader import load_result_file

results, raw = load_result_file("<runs-root>/results/tpcds_sf1_duckdb_sql_<timestamp>_<id>.json")
if raw["summary"]["validation"] != "passed":
    print("Warning: run did not validate")
print(f"Success rate: {raw['summary']['queries']['passed']}/{raw['summary']['queries']['total']}")
```

### Debug Mode

Enable verbose output with an official scale factor:

```bash
benchbox run --official --platform duckdb --benchmark tpcds --scale 1 --seed 42 -vv --output ./official_data
```
### Error Recovery

Retry a failed run from the shell, waiting 60 seconds between attempts:

```bash
for attempt in 1 2 3; do
  if benchbox run --official --platform duckdb --benchmark tpcds --scale 1 --seed 42 \
    --output ./official_data; then
    break
  fi
  echo "Attempt $attempt failed"
  sleep 60
done
```

## Best Practices

### 1. Scale Factor Selection

Choose a scale factor that matches the purpose. The approximate data sizes are 10MB for `development`, 100MB for
`testing`, 1GB for `small`, 10GB for `medium`, 100GB for `large` and 1TB for `enterprise`.

```python
scale_factors = {
    "development": 0.01,
    "testing": 0.1,
    "small": 1.0,
    "medium": 10.0,
    "large": 100.0,
    "enterprise": 1000.0
}

benchmark = TPCDSBenchmark(scale_factor=scale_factors["testing"])
```

### 2. Resource Management

Point `--output` at a temporary directory for throwaway runs:

```bash
benchbox run --official --platform duckdb --benchmark tpcds --scale 1 --seed 42 --output "$(mktemp -d)"
```

### 3. Performance Monitoring

```bash
START=$(date +%s)
benchbox run --official --platform duckdb --benchmark tpcds --scale 1 --seed 42 --output ./official_data
echo "Benchmark time: $(( $(date +%s) - START ))s"
```

### 4. Result Archival

```python
import json
from datetime import datetime
from benchbox.core.results.loader import load_result_file

results, raw = load_result_file("<runs-root>/results/tpcds_sf1_duckdb_sql_<timestamp>_<id>.json")
archive_data = {
    "timestamp": datetime.now().isoformat(),
    "power_at_size": results.power_at_size,
    "throughput_at_size": results.throughput_at_size,
    "scale_factor": raw["benchmark"]["scale_factor"],
}

with open(f"benchmark_archive_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json", "w") as f:
    json.dump(archive_data, f, indent=2)
```

### 5. Continuous Benchmarking

The loop runs every hour (`sleep 3600`) against your own baseline power number, and alerts when the result falls
more than 5% below it:

```bash
while true; do
  export BENCHBOX_OUTPUT_DIR=./ci_data
  benchbox run --official --platform duckdb --benchmark tpcds --scale 1 --seed 42 \
    --phases power --output ./ci_data
  jq -e '.summary.tpc_metrics.power_at_size > 250000.0 * 0.95' ./ci_data/results/*.json \
    || echo "ALERT: Power@Size regressed"
  sleep 3600
done
```

## Conclusion

The TPC-DS official benchmark implementation coordinates the power, throughput, and maintenance phases with Power@Size and Throughput@Size reporting. It does not export the composite QphDS@Size. Results are derived from TPC-DS and carry the derived-results disclaimer above.

For additional support:
- Review the integration tests in `tests/integration/test_tpcds_official_benchmark.py`
- Consult the TPC-DS specification at http://www.tpc.org/tpcds/

## References

- [TPC-DS Specification](http://www.tpc.org/tpcds/)
- [BenchBox Documentation](../../index.md)
- [TPC-DS Benchmark Overview](../../benchmarks/tpc-ds.md)
- [API Reference](../../reference/api-reference.md)
