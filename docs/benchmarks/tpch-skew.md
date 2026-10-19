<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# TPC-H Skew Benchmark

```{tags} advanced, concept, tpch-skew, tpc-h, custom-benchmark
```

> **CLI name:** `tpch_skew` - use `benchbox run --benchmark tpch_skew`
>
> **Query templates:** [every TPC-H Skew query](queries/tpch_skew/index.md)

## Overview

The TPC-H Skew benchmark extends the standard TPC-H benchmark with configurable data skew patterns to test database performance under realistic, non-uniform data distributions. Based on the research paper ["Introducing Skew into the TPC-H Benchmark"](https://www.tpc.org/tpctc/tpctc2011/slides_and_papers/introducing_skew_into_the_tpc_h_benchmark.pdf), this benchmark helps evaluate how query optimizers handle skewed data distributions that are common in real-world scenarios.

The benchmark is ideal for testing cardinality estimation accuracy, join strategy selection, and overall optimizer robustness when data doesn't follow uniform distributions.

## Key Features

- **Configurable skew levels** - From uniform (standard TPC-H) to extreme Zipfian distributions
- **Multiple distribution types** - Zipfian, normal, and exponential patterns
- **Preset configurations** - Ready-to-use skew profiles for common scenarios
- **Same 22 queries** - Uses standard TPC-H queries for direct comparison
- **Concurrent stream support** - Full TPC-H compliance testing with skewed data
- **Research-backed methodology** - Based on TPC-TC 2011 research

## Skew Presets

BenchBox provides predefined skew configurations for common testing scenarios:

| Preset | Skew Factor (z) | Description | Use Case |
|--------|-----------------|-------------|----------|
| `none` | 0.0 | Uniform distribution (standard TPC-H) | Baseline comparison |
| `light` | 0.2 | Light skew | Initial optimizer stress testing |
| `moderate` | 0.5 | Moderate skew (default) | Balanced skew testing |
| `heavy` | 0.8 | Heavy skew | Advanced optimizer testing |
| `extreme` | 1.0 | Zipf's law distribution | Edge case testing |
| `realistic` | varies | E-commerce patterns | Real-world simulation |

## Data Model

The TPC-H Skew benchmark uses the same schema as standard TPC-H:

### Tables

| Table | Purpose | Approximate Rows (SF 1) |
|-------|---------|-------------------------|
| **lineitem** | Order line items (largest table) | ~6,000,000 |
| **orders** | Customer orders | ~1,500,000 |
| **partsupp** | Part-supplier relationships | ~800,000 |
| **customer** | Customer information | ~150,000 |
| **part** | Part catalog | ~200,000 |
| **supplier** | Supplier information | ~10,000 |
| **nation** | Nation reference | 25 |
| **region** | Region reference | 5 |

### Skew Applied To

The skew distribution is applied to key columns that affect join cardinality and selectivity:

- **o_custkey** (orders → customer join)
- **l_partkey** (lineitem → part join)
- **l_suppkey** (lineitem → supplier join)
- **ps_partkey** (partsupp → part join)
- **ps_suppkey** (partsupp → supplier join)

## Query Categories

The benchmark uses the standard 22 TPC-H queries, unchanged from the base benchmark. This allows direct comparison between uniform and skewed data performance:

### Scan-Heavy Queries
- [Q1](queries/tpch_skew/q1.md), [Q6](queries/tpch_skew/q6.md): Large aggregations on lineitem
- Most affected by data volume, less by skew

### Join-Heavy Queries
- [Q3](queries/tpch_skew/q3.md), [Q5](queries/tpch_skew/q5.md), [Q7](queries/tpch_skew/q7.md), [Q8](queries/tpch_skew/q8.md), [Q9](queries/tpch_skew/q9.md), [Q10](queries/tpch_skew/q10.md), [Q21](queries/tpch_skew/q21.md): Multi-table joins
- **Most affected by skew** - cardinality estimation errors compound

### Subquery Queries
- [Q2](queries/tpch_skew/q2.md), [Q4](queries/tpch_skew/q4.md), [Q11](queries/tpch_skew/q11.md), [Q15](queries/tpch_skew/q15.md), [Q17](queries/tpch_skew/q17.md), [Q18](queries/tpch_skew/q18.md), [Q20](queries/tpch_skew/q20.md), [Q22](queries/tpch_skew/q22.md): Correlated subqueries
- Affected by both skew and optimizer strategy selection

### Complex Analytics
- [Q13](queries/tpch_skew/q13.md), [Q14](queries/tpch_skew/q14.md), [Q16](queries/tpch_skew/q16.md), [Q19](queries/tpch_skew/q19.md): Aggregation with complex predicates
- Variable impact depending on selectivity

## Usage Examples

### Basic Usage with Preset

```python
from benchbox import TPCHSkew

benchmark = TPCHSkew(scale_factor=1.0)
data_files = benchmark.generate_data()

queries = benchmark.get_queries()

print(benchmark.get_skew_info())
```

This uses the moderate skew preset, which is the default. The queries are the same as in standard TPC-H, and `get_skew_info()` shows the skew configuration.

### Using Different Presets

```python
from benchbox import TPCHSkew

light_benchmark = TPCHSkew(scale_factor=0.1, skew_preset="light")

heavy_benchmark = TPCHSkew(scale_factor=1.0, skew_preset="heavy")

extreme_benchmark = TPCHSkew(scale_factor=1.0, skew_preset="extreme")

realistic_benchmark = TPCHSkew(scale_factor=1.0, skew_preset="realistic")
```

Use `light` skew for initial testing, `heavy` for optimizer stress testing, `extreme` for edge cases, and `realistic` for e-commerce-like patterns.

### Custom Skew Configuration

```python
from benchbox import TPCHSkew
from benchbox.core.tpch_skew import SkewConfiguration

custom_config = SkewConfiguration(
    skew_factor=0.7,
    distribution_type="zipfian",
    attribute_skew_enabled=True,
    join_skew_enabled=True,
    temporal_skew_enabled=False,
)

benchmark = TPCHSkew(
    scale_factor=1.0,
    skew_config=custom_config
)
data_files = benchmark.generate_data()
```

### DuckDB Integration

```python
import duckdb
from benchbox import TPCHSkew
from benchbox.platforms.duckdb import DuckDBAdapter

benchmark = TPCHSkew(scale_factor=0.1, skew_preset="heavy")

benchmark.generate_data()

adapter = DuckDBAdapter(database=":memory:")
adapter.load_benchmark(benchmark)

results = adapter.run_benchmark(benchmark)

for query_id, result in results.items():
    print(f"Q{query_id}: {result['execution_time']:.3f}s")
```

The example generates data with heavy skew, loads it with the DuckDB adapter, runs the queries and prints the time for each. Compare the times with a baseline run.

### Comparing Uniform vs Skewed Performance

```python
from benchbox import TPCH, TPCHSkew
from benchbox.platforms.duckdb import DuckDBAdapter

uniform = TPCH(scale_factor=1.0)
uniform.generate_data()

skewed = TPCHSkew(scale_factor=1.0, skew_preset="heavy")
skewed.generate_data()

for benchmark, name in [(uniform, "Uniform"), (skewed, "Skewed")]:
    adapter = DuckDBAdapter()
    adapter.load_benchmark(benchmark)

    query = benchmark.get_query(3)
    result = adapter.execute_query(query)
    print(f"{name} Q3: {result['execution_time']:.3f}s")
```

The uniform standard TPC-H run is the baseline, and the heavy-skew run is the test. [Q3](queries/tpch_skew/q3.md) (Shipping Priority) is a join-heavy query, so skew affects it the most.

## CLI Options (`--benchmark-option`)

Configure TPC-H Skew via `--benchmark-option KEY=VALUE`:

| Option | Default | Description |
|--------|---------|-------------|
| `skew_preset` | `moderate` | Skew distribution: `none`, `light`, `moderate`, `heavy`, `extreme`, `realistic` |
| `force_regenerate` | - | Force data regeneration (`true`/`false`) |

Accepts hyphenated aliases (e.g. `skew-preset`).

```bash
benchbox run --platform duckdb --benchmark tpch_skew --scale 1 \
  --benchmark-option skew_preset=heavy

benchbox run --help-topic benchmarks
```

The first command runs a heavy skew distribution, and the second lists all preset options.

## Scale Factor Guidelines

| Scale Factor | Data Size | Memory Usage | Use Case |
|--------------|-----------|--------------|----------|
| 0.01 | ~10 MB | < 100 MB | Quick testing |
| 0.1 | ~100 MB | < 500 MB | Development |
| 1.0 | ~1 GB | < 4 GB | Standard benchmark |
| 10.0 | ~10 GB | < 20 GB | Performance testing |

## Performance Characteristics

### Impact of Skew on Query Performance

**Low Impact:**
- [Q1](queries/tpch_skew/q1.md), [Q6](queries/tpch_skew/q6.md): Simple scans and aggregations
- Performance dominated by I/O, not join strategies

**Medium Impact:**
- [Q2](queries/tpch_skew/q2.md), [Q4](queries/tpch_skew/q4.md), [Q11](queries/tpch_skew/q11.md), [Q15](queries/tpch_skew/q15.md): Subqueries with moderate join complexity
- Cardinality estimation errors affect plan selection

**High Impact:**
- [Q3](queries/tpch_skew/q3.md), [Q5](queries/tpch_skew/q5.md), [Q7](queries/tpch_skew/q7.md), [Q8](queries/tpch_skew/q8.md), [Q9](queries/tpch_skew/q9.md), [Q10](queries/tpch_skew/q10.md), [Q21](queries/tpch_skew/q21.md): Complex multi-way joins
- Skew causes significant cardinality estimation errors
- Wrong join order selection common with naive optimizers

### Common Optimizer Failures

1. **Hash join overflow**: Skewed build side exceeds memory estimates
2. **Wrong join order**: Estimated cardinalities differ significantly from actual
3. **Suboptimal parallelism**: Work imbalance due to skewed partitions
4. **Index selection errors**: Skewed data changes selectivity assumptions

## Best Practices

### Testing Methodology

1. **Establish baseline** - Run standard TPC-H first for comparison
2. **Start with moderate skew** - Use `moderate` preset initially
3. **Progress to heavy skew** - Identify optimizer breaking points
4. **Monitor query plans** - Compare plans between uniform and skewed runs

### Interpreting Results

1. **Calculate slowdown ratio** - skewed_time / uniform_time
2. **Identify outliers** - Queries with >2x slowdown indicate optimizer issues
3. **Examine plans** - Look for join order changes, hash spills
4. **Check cardinality estimates** - Compare estimated vs actual row counts

### Optimization Recommendations

1. **Update statistics frequently** - Histogram-based statistics help
2. **Consider adaptive execution** - Runtime join strategy switching
3. **Use skew hints if available** - Some databases support skew-aware hints
4. **Test with realistic skew** - Use `realistic` preset for production planning

## Related Documentation

- [TPC-H Benchmark](tpc-h.md) - Standard TPC-H documentation
- [TPC-Havoc](tpc-havoc.md) - Query syntax variant testing
- [Join Order Benchmark](join-order.md) - Join optimization testing

## External Resources

- [TPC-H Official Specification](http://www.tpc.org/tpch) - TPC-H standard
- [Introducing Skew into TPC-H](https://www.tpc.org/tpctc/tpctc2011/) - Research methodology
- [Cardinality Estimation in Query Optimizers](https://www.vldb.org/pvldb/vol9/p204-leis.pdf) - Related research
