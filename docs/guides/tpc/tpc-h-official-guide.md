<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# TPC-H Official Benchmark Guide

```{tags} advanced, guide, tpc-h, validation
```

This guide documents BenchBox's TPC-H Power@Size and Throughput@Size phases and its opt-in RF1/RF2 integration. QphH@Size is emitted only when the required evidence is present; see [QphH@Size](#qphh-size).

> **Derived results.** Derived from TPC-H. Results produced by BenchBox do not comply with the TPC-H Specification and must not be compared with official TPC-H results. The TPC publishes official TPC-H results only after an audit.

## Overview

The TPC-H official runner coordinates Power, Throughput, and Maintenance phases. Refresh-function integration is disabled by default and does not make a result comparable to official TPC results.

### What is TPC-H?

TPC-H is a decision support benchmark that consists of a suite of business-oriented ad-hoc queries and concurrent data modifications. The benchmark illustrates decision support systems that examine large volumes of data, execute queries with a high degree of complexity, and give answers to critical business questions.

### QphH@Size

TPC-H QphH@Size is the geometric mean of Power@Size and Throughput@Size. BenchBox emits it only for an official run with complete evidence: all 22 Power queries and RF1/RF2 succeed on update set F; Throughput completes at least two query streams and one successful RF1/RF2 pair per stream on consecutive sets F+1 through F+S; and neither phase has failed queries. Query-subset runs and refresh failures do not qualify.

Enable refresh functions with `--benchmark-option tpch_refresh_functions=true`. The default is `false`; `tpch_refresh_first_set` defaults to 1. The Power phase applies set F only on its final measurement iteration. The Throughput phase runs a separate refresh stream alongside query streams. Both phases record refresh evidence; separate result files qualify only when the Power and Throughput sets are consecutive.

The composite is `round(sqrt(Power@Size × Throughput@Size), 1)`. Results that fail any gate omit `qphh_at_size` and report `qphh_unavailable` reasons. `benchbox metrics qphh` uses the same gate.

BenchBox results are derived results, not audited TPC results. The implementation does not apply the specification's 0.01-second refresh rounding, 1000x clamp, upward Ts rounding, or two-run lower-score rule. Refresh support is declared for DuckDB, SQLite, PostgreSQL and the adapters built on it, Snowflake, and SingleStore.

BenchBox also reports the component metrics:

- **Power@Size** = 3600 × Scale_Factor / geometric mean of the 22 query times, or 22 query times plus RF1 and RF2 when refresh is enabled.
- **Throughput@Size** = (22 × Num_Streams) × 3600 × Scale_Factor / measured throughput interval. The interval includes concurrent RF activity when enabled.

## Key Features

- **Complete TPC-H Implementation**: All 22 queries with proper parameterization
- **Three Test Phases**: Power Test, Throughput Test, and Maintenance Test
- **Power@Size, Throughput@Size, and gated QphH@Size**: QphH is emitted only when official-run and refresh-evidence requirements pass.
- **Comprehensive Reporting**: HTML, text, and CSV report generation
- **Result Validation**: Automatic validation against TPC-H specification
- **Audit Trail**: Run evidence recorded alongside each result
- **Performance Analysis**: Detailed performance metrics and analysis
- **Benchmark Comparison**: Compare results across different runs

## TPC-H Specification Coverage

The implementation provides the TPC-H query, power, throughput, and maintenance phases. Adapter support and refresh evidence are reported per run; BenchBox does not claim TPC compliance or certification.

- **Query Execution**: All 22 TPC-H queries with parameterization
- **Stream Generation**: Query streams with the TPC-H permutation matrix
- **Power Test**: Sequential query execution, optionally with RF1/RF2
- **Throughput Test**: Concurrent query streams, optionally with a concurrent RF stream
- **Maintenance Test**: Standalone RF1/RF2 data modification
- **Metric Calculation**: Power@Size, Throughput@Size, and QphH@Size when its evidence gate passes
- **Result Validation**: Validation and evidence are included in the result

## Installation and Setup

### Prerequisites

- Python 3.11 or higher
- Database system (DuckDB, SQLite, PostgreSQL, Snowflake, etc.)
- TPC-H data generation tools (included with BenchBox)

### Installation

```bash
uv add benchbox
```

## Quick Start

Run the official TPC-H benchmark with the CLI. `--official` marks the run metadata; it is not a claim of TPC compliance, and QphH@Size requires the additional refresh evidence described below.

```bash
benchbox run --official --platform duckdb --benchmark tpch --scale 1 --seed 42 --output ./official_data
```

Then read the metrics back programmatically:

```python
from benchbox.core.results.loader import load_result_file

results, raw = load_result_file("<runs-root>/results/tpch_sf1_duckdb_sql_<timestamp>_<id>.json")

print(f"Power@Size: {results.power_at_size:.2f}")
print(f"Throughput@Size: {results.throughput_at_size}")
print(f"Success: {raw['summary']['validation']}")
```

(`results.throughput_at_size` is `None` here because the default official run covers the power phase; add `--phases throughput` for the throughput test.)

## Detailed Usage

### Creating a Benchmark Instance

The parameters are:
- `scale_factor`: the scale factor (1.0 is about 1 GB).
- `output_dir`: the directory for generated data.

```python
from benchbox import TPCH

benchmark = TPCH(
    scale_factor=1.0,
    output_dir="./output",
)
```

### Running the Official Benchmark

Configure the run with `TPCHOfficialBenchmarkConfig`, then execute. An adapter is required so per-stream sessions apply:

```python
from benchbox.core.tpch.official_benchmark import TPCHOfficialBenchmark, TPCHOfficialBenchmarkConfig

config = TPCHOfficialBenchmarkConfig(
    num_streams=2,
    seed=42,
    validation_enabled=True,
    audit_trail=True,
)

official = TPCHOfficialBenchmark(scale_factor=1.0, output_dir="./output")
result = official.run_official_benchmark(connection_factory, config, adapter=adapter)
```

The parameters are:
- `connection_factory`: the database connection factory.
- `config`: a `TPCHOfficialBenchmarkConfig` (stream count, seed, validation, audit trail).
- `adapter`: the platform adapter (required for stream isolation).

### Accessing Results

The result object holds the overall metrics plus per-phase results:

```python
print(f"Success: {result.success}")
print(f"Total Time: {result.total_time}")

print(f"Power@Size: {result.power_at_size}")
print(f"Power queries executed: {result.power_test_result.queries_executed}")

print(f"Throughput@Size: {result.throughput_at_size}")
print(f"Streams: {result.throughput_test_result.streams_successful}/{result.throughput_test_result.streams_executed}")

print(f"Compliance validated: {result.compliance_validated}")
print(f"Errors: {result.errors}")
```

## Test Phases

### Power Test

The Power Test measures single-stream performance by executing all 22 TPC-H queries sequentially. With `tpch_refresh_functions=true`, it also executes RF1, then the queries, then RF2 using `tpch_refresh_first_set`; refreshes run only in the final measurement iteration, not warmups or earlier iterations.

Power@Size uses the geometric mean of 22 query times by default, or 24 terms (22 queries plus RF1 and RF2) when refresh is enabled. Update files are generated before the measured phase. The refresh functions permanently modify database contents.

### Throughput Test

The Throughput Test executes S concurrent query streams. With refresh enabled, one additional refresh stream runs S serial RF1/RF2 pairs on update sets F+1 through F+S, concurrently with the query streams. The measured interval includes the earliest query or refresh start and latest query or refresh end. A failed refresh withholds Throughput@Size.

### Maintenance Test

> **Database state:** The standalone Maintenance phase permanently modifies the database. Power or Throughput with `tpch_refresh_functions=true` also permanently applies refresh functions. Reload the base data before reusing update sets; otherwise, set `tpch_refresh_first_set` to the next unused set.

The standalone Maintenance Test uses the same DBGen update-set generator and RF1/RF2 executor as the Power and Throughput phases. Set F selects DBGen update files; after a combined run using Power set F and throughput sets F+1..F+S, the next unused set is F+S+1. Generated files are prepared before measurement. RF1 and RF2 use one transaction each and process rows in 1,000-row chunks. RF1 rejects duplicate order keys; RF2 verifies all target orders exist, then deletes LINEITEM rows before ORDERS rows.

The update sets carry scale-factor-specific TPC-H data. They do not use a fixed synthetic row count. A dry run can still preview maintenance SQL without applying the refresh functions.

```python
from benchbox import TPCH
from benchbox.platforms.duckdb import DuckDBAdapter

benchmark = TPCH(
    scale_factor=1.0,
    official=True,
    tpch_refresh_functions=True,
    tpch_refresh_first_set=1,
)
benchmark.generate_data()
adapter = DuckDBAdapter(database_path="tpch.duckdb", force_recreate=True)

power_result = adapter.run_benchmark(benchmark, test_execution_type="power")
throughput_result = adapter.run_benchmark(benchmark, test_execution_type="throughput")
```

This example modifies the database in both phases. The next run must use the next unused update set or reload the original data. Adapter support is declared per platform; see [TPC-H refresh support](../../benchmarks/tpc-h.md#tpc-h-derived-metrics).

**Workflow:** `generate` → `load` → `power` → `throughput` → `maintenance`. Reload before reusing update sets or comparing against a fresh dataset.

## Power@Size and Throughput@Size Calculation

Power@Size and Throughput@Size are calculated separately. QphH@Size is emitted only when the official-run and refresh-evidence gates in [QphH@Size](#qphh-size) pass.

### Component Calculations

**Power@Size:**
```
Power@Size = 3600 × Scale_Factor / geometric_mean(22 query times [+ RF1 and RF2 when enabled])
```

**Throughput@Size:**
```
Throughput@Size = 22 × Num_Streams × 3600 × Scale_Factor / Throughput_Test_Time
```

When refresh is enabled, `Throughput_Test_Time` includes the concurrent refresh stream. When disabled, it spans the first query start through the last query completion.

### Example Calculation

For a throughput test with:
- Scale Factor: 1.0
- Throughput Test Time: 150 seconds
- Number of Streams: 2

```python
throughput_at_size = 22 * 2 * 3600 * 1.0 / 150
```

With a 150-second throughput test at SF1 and 2 streams, `throughput_at_size = 1056.0`. A qualifying official run also exports QphH@Size; other runs provide `qphh_unavailable` reasons.

## Reporting and Validation

### Report Generation

Reports show the available Power@Size, Throughput@Size, and QphH@Size values, together with refresh evidence and any QphH refusal reasons. They accept the object returned by `run_official_benchmark` directly.

```python
from benchbox.core.tpch.reporting import TPCHReportGenerator

report_generator = TPCHReportGenerator(output_dir="./reports")

html_report = report_generator.generate_detailed_report(
    result=result,
    report_title="TPC-H Benchmark Report",
    include_detailed_analysis=True,
    include_validation_info=True
)

validation_report = report_generator.generate_validation_report(result=result)

csv_report = report_generator.generate_performance_csv(result=result)
```

### Result Validation

BenchBox runs validation checks on results. Passing them does not make a result comparable to official TPC-H results:

```python
if result.success and result.compliance_validated:
    print("Validation checks passed")
else:
    print("Validation issues found:")
    for error in result.errors:
        print(f"  - {error}")
```

### Benchmark Comparison

```python
comparison = report_generator.compare_results(
    baseline_result=baseline_result,
    current_result=current_result
)

print(f"Power@Size Change: {comparison.relative_change:+.1%}")
print(f"Significant Change: {comparison.significant_change}")

comparison_report = report_generator.generate_comparison_report(
    baseline_result=baseline_result,
    current_result=current_result
)
```

## Derived Run Workflow

To run TPC-H at a TPC-specified scale factor, follow these steps. Results remain derived results:

### 1. Preparation

Use a TPC-specified scale factor:

```python
benchmark = TPCH(
    scale_factor=100.0,
    output_dir="./tpch_data",
)
```

### 2. Data Generation

```python
data_files = benchmark.generate_data()
```

### 3. Database Setup

Set up a production database with proper configuration. Use a database system appropriate for the scale factor.

### 4. Benchmark Execution

Run with the official-mode parameters, using an appropriate number of streams:

```python
config = TPCHOfficialBenchmarkConfig(
    scale_factor=benchmark.scale_factor,
    num_streams=8,
    validation_enabled=True,
    audit_trail=True,
)
result = benchmark.run_official_benchmark(connection_factory, config, adapter=adapter)
```

### 5. Report Generation

```python
report_generator = TPCHReportGenerator(output_dir="./reports")
validation_report = report_generator.generate_validation_report(result=result)
```

### 6. Validation

```python
if result.success and result.compliance_validated:
    print("Validation checks passed")
else:
    print("Address validation issues before publishing results")
```

## Best Practices

### Scale Factor Selection

- **Development/Testing**: Use scale factors 0.01-1.0
- **Performance Testing**: Use scale factors 1-10
- **TPC-specified scale factors**: Use 100, 300, 1000, etc.

### Database Configuration

- **Memory**: Ensure sufficient memory for the dataset
- **Storage**: Use appropriate storage configuration
- **Parallelism**: Configure database for concurrent query execution
- **Indexing**: Create appropriate indexes for TPC-H queries

### Benchmark Configuration

The examples below are, in order, for development, performance testing, and a TPC-specified scale factor:

```python
benchmark = TPCH(scale_factor=0.01, verbose=True)

benchmark = TPCH(scale_factor=10.0, parallel=8)

benchmark = TPCH(scale_factor=100.0, verbose=True)
```

### Stream Configuration

- **Development**: Use 1-2 streams
- **Performance Testing**: Use 2-4 streams
- **TPC-specified scale factors**: Use appropriate number based on system capabilities

## Troubleshooting

### Common Issues

#### Database Connection Issues

Issue: connection timeouts. Solution: increase the connection timeout.

```python
def connection_factory():
    conn = sqlite3.connect("tpch.db", timeout=30)
    return conn
```

#### Query Execution Failures

Issue: query syntax errors. Solution: check SQL dialect compatibility.

```python
query = benchmark.get_query(1, dialect="postgres")
```

#### Memory Issues

Issue: out of memory during execution. Solution: use a smaller scale factor or increase system memory.

```python
benchmark = TPCH(scale_factor=0.1)
```

#### Performance Issues

Issue: slow query execution. Solution: optimize the database configuration and indexing.

### Debugging

Enable verbose logging for detailed debugging:

```python
benchmark = TPCH(verbose=True)
config = TPCHOfficialBenchmarkConfig(verbose=True)
result = benchmark.run_official_benchmark(
    connection_factory, config, adapter=adapter
)
```

Check audit trail logs. With `audit_trail=True`, a `<benchmark>_audit_trail_<timestamp>.txt` file is written to the run output directory.

### Performance Optimization

1. **Database Tuning**: Optimize database configuration
2. **Index Creation**: Create appropriate indexes for TPC-H queries
3. **Memory Allocation**: Ensure sufficient memory for concurrent streams
4. **Storage Configuration**: Use appropriate storage for data and logs
5. **Query Optimization**: Review query execution plans

## Advanced-level Usage

### Custom Connection Factory

```python
def custom_connection_factory():
    conn = sqlite3.connect("tpch.db")
    conn.execute("PRAGMA cache_size=100000")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn
```

### Custom Validation

```python
def custom_validate_result(result):
    if (result.throughput_at_size or 0) < 100:
        result.errors.append("Throughput@Size below minimum threshold")
    return result
```

### Batch Processing

```python
def run_multiple_benchmarks():
    from benchbox.core.tpch.official_benchmark import TPCHOfficialBenchmarkConfig
    scale_factors = [0.1, 0.5, 1.0]
    results = []

    for sf in scale_factors:
        benchmark = TPCH(scale_factor=sf)
        config = TPCHOfficialBenchmarkConfig(scale_factor=sf, num_streams=2)
        result = benchmark.run_official_benchmark(
            connection_factory, config, adapter=adapter
        )
        results.append(result)
    return results
```

## API Reference

### TPCH Class

```python
class TPCH:
    def __init__(self, scale_factor=1.0, output_dir=None, **kwargs):
        pass

    def generate_data(self) -> list:
        pass

    def run_official_benchmark(self, connection_factory, config=None, *, adapter=None) -> TPCHOfficialBenchmarkResult:
        pass

    def get_query(self, query_id, **kwargs) -> str:
        pass
```

### TPCHOfficialBenchmark Class

```python
class TPCHOfficialBenchmark:
    def __init__(self, scale_factor=1.0, output_dir=None, verbose=False, **kwargs):
        pass

    def run_official_benchmark(self, connection_factory, config=None, *, adapter=None) -> TPCHOfficialBenchmarkResult:
        pass
```

### TPCHReportGenerator Class

```python
class TPCHReportGenerator:
    def __init__(self, output_dir=None):
        pass

    def generate_detailed_report(self, result, **kwargs) -> Path:
        pass

    def generate_validation_report(self, result) -> Path:
        pass

    def compare_results(self, baseline_result, current_result) -> ComparisonResult:
        pass
```

## Conclusion

The TPC-H official benchmark implementation coordinates all three test phases with Power@Size and Throughput@Size reporting, systematic reporting, and validation capabilities. Results are derived from TPC-H and carry the derived-results disclaimer above.

For more information, examples, and updates, visit the [BenchBox GitHub repository](https://github.com/BenchBox-dev/BenchBox).
