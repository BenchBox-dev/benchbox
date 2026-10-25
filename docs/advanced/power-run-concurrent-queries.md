<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Power Run Iterations and Concurrent Query Execution

```{tags} advanced, guide, tpc-h, performance
```

Comprehensive guide to BenchBox's execution modes for benchmark testing with statistical confidence and throughput analysis.

## Overview

BenchBox provides two execution modes:

- **Power Run Iterations**: Execute multiple test iterations sequentially to gather statistical confidence and eliminate outliers
- **Concurrent Query Execution**: Execute queries in parallel to test throughput, scalability, and multi-user scenarios

These features are essential for:
- Production performance evaluation
- Database stress testing
- Scalability analysis
- Statistical confidence in benchmark results
- Regression testing with confidence intervals

## Power Run Iterations

### What are Power Run Iterations?

Power Run Iterations execute the same benchmark test multiple times, providing statistical analysis of performance consistency. This approach:

- **Eliminates outliers** from single-run measurements
- **Provides confidence intervals** for benchmark results
- **Detects performance variance** and consistency issues
- **Enables statistical comparison** between configurations
- **Follows TPC specifications**: every warm-up and measured TPC-H power iteration runs stream 00, with the same query ordering and substitution parameters

### Key Benefits

1. **Statistical Confidence**: Get mean, median, standard deviation, and confidence intervals
2. **Performance Consistency**: Identify systems with high variance in query times
3. **Outlier Detection**: Automatically identify and handle anomalous runs
4. **Regression Testing**: Compare performance changes with statistical significance
5. **Professional Reporting**: Generate publication-quality benchmark reports

### Configuration Options

```yaml
execution:
  power_run:
    iterations: 5
    warm_up_iterations: 2
    timeout_per_iteration_minutes: 90
    fail_fast: false
    collect_metrics: true
```

- `iterations`: the number of test iterations to run.
- `warm_up_iterations`: warm-up runs, which are excluded from statistics.
- `timeout_per_iteration_minutes`: the maximum time for each iteration.
- `fail_fast`: when true, stop on the first failure. When false, continue.
- `collect_metrics`: collect detailed performance metrics.

Put these settings in `benchbox.yaml`.

### Usage Examples

#### Basic Power Run Setup

```python
import statistics

import duckdb

from benchbox import TPCH
from benchbox.core.tpch.power_test import TPCHPowerTest

tpch = TPCH(scale_factor=0.1)

connection = duckdb.connect("test.db")

power_values = []
for stream_id in range(5):
    power_test = TPCHPowerTest(
        benchmark=tpch,
        connection=connection,
        scale_factor=0.1,
        stream_id=stream_id,
        validation=False,
    )
    result = power_test.run()
    assert result.success, result.errors
    power_values.append(result.power_at_size)

print(f"Average Power@Size: {statistics.mean(power_values):.2f}")
print(f"Std Deviation: {statistics.stdev(power_values):.2f}")
print(f"Confidence: {len(power_values)}/5 iterations successful")
```

This example requires `test.db` to contain a loaded TPC-H SF 0.1 dataset. Generate and load it first, or replace the path with an initialized database.

Power iterations are a plain loop over stream IDs. Each `TPCHPowerTest` run executes the 22 queries in that stream's permutation against a real connection and reports Power@Size. Validation is off because answer sets exist for stream 0 only.

#### Advanced-level Statistical Analysis

```python
import numpy as np
from scipy import stats

confidence_level = 0.95
degrees_freedom = len(power_values) - 1
sample_mean = np.mean(power_values)
sample_standard_error = stats.sem(power_values)

confidence_interval = stats.t.interval(
    confidence_level,
    degrees_freedom,
    sample_mean,
    sample_standard_error
)

print(f"95% Confidence Interval: {confidence_interval[0]:.2f} - {confidence_interval[1]:.2f}")
print(f"Coefficient of Variation: {(np.std(power_values) / sample_mean) * 100:.1f}%")
```

`power_values` comes from the loop above: one Power@Size per stream-ID run. The interval is a 95% confidence interval.

### Sizing Power-Run Loops

`ExecutionConfigHelper.apply_performance_profile()` provides named
configuration presets (`quick`, `standard`, `thorough`, `stress`) for
iteration counts and timeouts — but presets only store settings.
Iterations themselves are a caller loop over stream IDs (one
`TPCHPowerTest` run per iteration), and the per-run `timeout` bounds a
single run. Size the loop to the decision:

#### Quick
- **Use Case**: Rapid development testing
- **Iterations**: 1 (no statistical analysis)
- **Best For**: Development, unit testing

#### Standard
- **Use Case**: Balanced performance testing
- **Iterations**: 3
- **Best For**: Regular performance evaluation

#### Thorough
- **Use Case**: Comprehensive analysis
- **Iterations**: 5
- **Best For**: Production evaluation, research

#### Stress
- **Use Case**: Maximum statistical confidence
- **Iterations**: 10
- **Best For**: Official benchmarking, publications

## Concurrent Query Execution

### What is Concurrent Query Execution?

Concurrent Query Execution runs multiple query streams simultaneously to test:

- **Throughput performance** under multi-user load
- **Scalability characteristics** as load increases
- **Resource contention** behavior
- **Multi-threading efficiency** of database engines
- **Real-world performance** with concurrent users

### Key Benefits

1. **Throughput Measurement**: Queries per second under concurrent load
2. **Scalability Testing**: Performance scaling with concurrent streams
3. **Resource Analysis**: CPU, memory, I/O utilization under load
4. **Multi-User Simulation**: Real-world concurrent access patterns
5. **Bottleneck Identification**: Find system limitations and contention points

### Configuration Options

The values below are an illustrative example, not the shipped defaults — see
[Configuration reference → `execution`](../reference/cli/configuration.md#execution)
for the default value of every field (`max_concurrent` defaults to **2**,
not the `4` used here):

```yaml
execution:
  concurrent_queries:
    enabled: true
    max_concurrent: 4
    query_timeout_seconds: 600
    stream_timeout_seconds: 7200
    cancel_on_timeout: true
    retry_failed_queries: true
    max_retries: 5
```

Defaults: `enabled` false, `max_concurrent` 2, `query_timeout_seconds` 300,
`stream_timeout_seconds` unset (3600 for TPC-H, 7200 for TPC-DS),
`cancel_on_timeout` false, `retry_failed_queries` true, `max_retries` 3.

`stream_timeout_seconds` and `cancel_on_timeout` apply to the `throughput`
phase of `benchbox run`: a value in the config file reaches every TPC-H and
TPC-DS throughput stream. Leave `stream_timeout_seconds` out to keep the
per-benchmark default. The other `concurrent_queries` settings are read by the
`ExecutionConfigHelper` API only; `benchbox run` does not use them.

### Usage Examples

#### Basic Concurrent Execution

```python
import duckdb

from benchbox import TPCH
from benchbox.core.tpch.throughput_test import TPCHThroughputTest

tpch = TPCH(scale_factor=0.1)

connection = duckdb.connect("throughput.db")

throughput_test = TPCHThroughputTest(
    benchmark=tpch,
    connection_factory=lambda: connection.cursor(),
    scale_factor=0.1,
    num_streams=4,
    verbose=False,
)
result = throughput_test.run()

print(f"Streams: {result.streams_successful}/{result.streams_executed} successful")
for stream in result.stream_results:
    print(f"Stream {stream.stream_id}: "
          f"{stream.queries_successful}/{stream.queries_executed} queries, "
          f"{stream.duration:.2f}s")
```

This example requires `throughput.db` to contain a loaded TPC-H SF 0.1 dataset. Generate and load it first, or replace the path with an initialized database.

One throughput test owns all of its streams. The connection factory hands each stream its session, and `StreamRunner` executes the streams concurrently with fail-closed accounting.

#### Scalability Analysis

```python
concurrency_levels = [1, 2, 4, 8]
throughput_results = {}

for level in concurrency_levels:
    print(f"\nTesting with {level} concurrent streams...")

    level_test = TPCHThroughputTest(
        benchmark=tpch,
        connection_factory=lambda: connection.cursor(),
        scale_factor=0.1,
        num_streams=level,
    )
    level_result = level_test.run()

    successful = sum(s.queries_successful for s in level_result.stream_results)
    executed = sum(s.queries_executed for s in level_result.stream_results)
    throughput_results[level] = {
        'successful': successful,
        'success_rate': successful / executed if executed else 0.0,
        'avg_duration': level_result.total_time / level if level else 0.0,
    }

print(f"\n Scalability Analysis:")
print(f"{'Streams':<8} {'Queries OK':<12} {'Success Rate':<12}")
print("-" * 38)

for level, metrics in throughput_results.items():
    print(f"{level:<8} {metrics['successful']:<12} {metrics['success_rate']:<12.1%}")
```

The loop tests scalability across the different concurrency levels.

## System Optimization

### Automatic Resource-Based Configuration

BenchBox automatically optimizes execution settings based on available system resources:

```python
import psutil
from benchbox.utils import ExecutionConfigHelper

config_helper = ExecutionConfigHelper()

cpu_cores = psutil.cpu_count()
memory_gb = psutil.virtual_memory().total / (1024**3)

config_helper.optimize_for_system(cpu_cores=cpu_cores, memory_gb=memory_gb)

summary = config_helper.get_execution_summary()
print(f"Optimized for {cpu_cores} cores, {memory_gb:.1f}GB RAM:")
print(f"- Max concurrent streams: {summary['concurrent_queries']['max_streams']}")
print(f"- Power run timeout: {summary['power_run']['settings']['timeout_per_iteration_minutes']} min")
```

`optimize_for_system` tunes settings automatically from the system specs, and `get_execution_summary` shows the configured settings.

### Optimization Rules

**CPU Core Optimization:**
- `max_concurrent = min(8, max(2, cpu_cores // 4))`
- Prevents over-subscription of CPU resources
- Scales appropriately from 2-core to 32+ core systems

**Memory Optimization:**
- **< 8GB**: Extended timeouts (120min power, 600s queries)
- **8-16GB**: Standard timeouts (60min power, 300s queries)
- **> 16GB**: Reduced timeouts (45min power, 180s queries)

**Storage Optimization:**
- SSD systems: Higher concurrency, shorter timeouts
- HDD systems: Lower concurrency, longer timeouts
- Network storage: Conservative settings with retries

## Comprehensive Testing Workflow

### Production Evaluation Workflow

```python
import statistics

import duckdb

from benchbox import TPCH
from benchbox.core.tpch.power_test import TPCHPowerTest
from benchbox.core.tpch.throughput_test import TPCHThroughputTest
from benchbox.utils import ExecutionConfigHelper

config_helper = ExecutionConfigHelper()
config_helper.apply_performance_profile('thorough')

import psutil
config_helper.optimize_for_system(
    cpu_cores=psutil.cpu_count(),
    memory_gb=psutil.virtual_memory().total / (1024**3)
)

tpch = TPCH(scale_factor=1.0)
connection = duckdb.connect("production_test.db")

print("Phase 1: Power Run Analysis")
power_values = []
for stream_id in range(5):
    power_test = TPCHPowerTest(
        benchmark=tpch,
        connection=connection,
        scale_factor=1.0,
        stream_id=stream_id,
        validation=(stream_id == 0),
    )
    power_result = power_test.run()
    assert power_result.success, power_result.errors
    power_values.append(power_result.power_at_size)

print(f" Single-Stream Performance:")
print(f"  Average: {statistics.mean(power_values):.2f} Power@Size")
print(f"  Std Dev: {statistics.stdev(power_values):.2f}")
print(f"  Range: {min(power_values):.2f} - {max(power_values):.2f}")

print("\nPhase 2: Concurrent Throughput Analysis")
throughput_test = TPCHThroughputTest(
    benchmark=tpch,
    connection_factory=lambda: connection.cursor(),
    scale_factor=1.0,
    num_streams=4,
    verbose=False,
)
concurrent_result = throughput_test.run()
assert concurrent_result.success, concurrent_result.errors

successful = sum(s.queries_successful for s in concurrent_result.stream_results)
executed = sum(s.queries_executed for s in concurrent_result.stream_results)
print(f" Multi-Stream Performance:")
print(f"  Streams: {concurrent_result.streams_successful}/{concurrent_result.streams_executed}")
print(f"  Success Rate: {successful}/{executed}")

print(f"\n Performance Analysis:")
print(f"  Single-stream efficiency: {statistics.mean(power_values):.2f} Power@Size")
print(f"  Performance consistency: ±{statistics.stdev(power_values):.1f} Power@Size")
```

The workflow has four steps: system analysis and optimization (the `thorough` profile gives comprehensive testing), benchmark setup at production scale (SF 1.0), power run testing for statistical confidence, and concurrent throughput testing. Validation runs for stream 0 only because answer sets exist for stream 0 only.

## Best Practices

### Power Run Iterations

1. **Iteration Count Selection**:
   - Development: 1-3 iterations
   - Performance evaluation: 3-5 iterations
   - Official benchmarking: 5-10 iterations

2. **Warm-up Iterations**:
   - Always include 1-2 warm-up iterations
   - Warm-up eliminates cold-start effects
   - Excluded from statistical calculations

3. **Timeout Configuration**:
   - Small scale factors: 30-60 minutes
   - Large scale factors: 60-180 minutes
   - Factor in system performance characteristics

4. **Statistical Analysis**:
   - Calculate confidence intervals for comparisons
   - Use coefficient of variation to assess consistency
   - Report both mean and median values

### Concurrent Query Execution

1. **Concurrency Selection**:
   - Start with `CPU cores / 4` for initial testing
   - Scale up to find appropriate concurrent level
   - Monitor system resource utilization

2. **Resource Monitoring**:
   - Watch for CPU over-subscription
   - Monitor memory usage and swapping
   - Check I/O bottlenecks and disk utilization

3. **Timeout Management**:
   - Set generous timeouts for initial testing
   - Adjust based on observed query execution times
   - Do not retry measured queries: retries invalidate TPC timing semantics,
     so a failed stream fails the run instead of being retried

4. **Result Interpretation**:
   - Linear scaling indicates good parallelization
   - Sub-linear scaling suggests resource contention
   - Throughput decrease indicates over-subscription

### System Optimization

1. **Resource Assessment**:
   - Use system profiling before configuration
   - Consider available vs. total memory
   - Account for other system processes

2. **Environment Configuration**:
   - Disable unnecessary background processes
   - Set appropriate database configuration
   - Ensure adequate temporary disk space

3. **Validation**:
   - Always validate configurations before execution
   - Test with smaller scale factors first
   - Monitor resource usage during execution

## TPC Specification Compliance

BenchBox ensures compliance with official TPC specifications for query ordering in both power and throughput tests.

### Power Run Compliance

Every TPC-H power run iteration, warm-up and measured, runs stream 00 of the TPC specification: the same query permutation and the same substitution parameters, so iterations are repeats of one workload and can be compared directly. Row-count validation applies to every measured iteration under the default validation mode. TPC-DS power iterations keep their existing stream ordering.

```python
config = PowerRunSettings()
config.iterations = 5
```

All five iterations repeat stream 0. For TPC-H, its permutation is `[14, 2, 9, 20, 6, 17, 18, 8, 21, 13, 3, 22, 16, 4, 11, 15, 1, 10, 19, 5, 7, 12]`.

### Throughput Compliance

Throughput streams are numbered 1 to S, as in the TPC specifications. Stream 0 is the power stream, so no throughput stream repeats the power ordering, and each throughput stream uses its own permutation and parameters.

Stream N uses TPC-H permutation N, or TPC-DS dsqgen stream N. A 4-stream throughput test runs streams 1, 2, 3 and 4.

Results record this under `phases.throughput_test.stream_numbering`.

### Why TPC Compliance Matters

1. **Valid Comparisons**: Results comparable to official TPC publications
2. **Optimizer Testing**: Each throughput stream uses a different ordering, which stresses different optimization paths
3. **Statistical Validity**: Repeated stream 00 iterations measure variance on one fixed workload
4. **Research Validity**: Academic research can rely on specification-compliant results

### Verification

Check result compliance:
```python
assert result.query_results[0]['query_id'] == 14
assert result.query_results[0]['stream_id'] == 0
assert result.query_results[0]['position'] == 1
```

These assertions verify that the first query in a TPC-H power test follows the stream permutation. Stream 0 starts with query 14, the stream ID is recorded, and the position is the query's place in the permutation.

For detailed compliance documentation, see:
[TPC-H Official Guide → Specification Coverage](../guides/tpc/tpc-h-official-guide.md#tpc-h-specification-coverage),
[TPC-DS Official Guide](../guides/tpc/tpc-ds-official-guide.md), and the shared
[TPC Patterns Usage Guide](../guides/tpc/tpc-patterns-usage.md) (stream/permutation
implementation used by both benchmarks).

## Troubleshooting

### Common Issues

**Power Run Failures:**
- **Memory exhaustion**: Reduce scale factor or increase timeout
- **High variability**: Check for system interference or thermal throttling
- **Iteration failures**: Enable detailed logging to identify specific issues

**Concurrent Execution Problems:**
- **Deadlocks**: Reduce concurrency level or enable retries
- **Resource contention**: Lower concurrent stream count
- **Timeout errors**: Increase timeout values or optimize queries

**Configuration Issues:**
- **Invalid settings**: Use validation before execution
- **System limitations**: Apply auto-optimization for current hardware
- **Profile conflicts**: Reset to default before applying new profiles

### Performance Optimization Tips

1. **Database Configuration**:
   - Optimize memory allocation (75% of available RAM)
   - Enable appropriate number of worker threads
   - Configure temporary storage on fast disks

2. **System Tuning**:
   - Disable CPU frequency scaling during tests
   - Set high-performance power profile
   - Minimize background process interference

3. **Test Environment**:
   - Use dedicated systems for benchmarking
   - Ensure consistent thermal conditions
   - Minimize network and disk I/O interference

This systematic approach to power run iterations and concurrent query execution provides professional-grade benchmarking capabilities with statistical rigor and real-world relevance.
