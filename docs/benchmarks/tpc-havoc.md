<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# TPC-Havoc Benchmark

```{tags} advanced, concept, tpc-havoc, tpc-h, custom-benchmark
```

> **CLI name:** `tpchavoc` - use `benchbox run --benchmark tpchavoc`
>
> **Query templates:** [every TPC-Havoc query](queries/tpchavoc/index.md)

## Overview

TPC-Havoc is an experimental benchmark designed to stress-test query optimizer robustness through syntactic variations. It generates 10 semantically equivalent but syntactically different versions of each TPC-H query, creating 220 total query variants that should produce identical results but may exhibit different execution plans and performance characteristics.

**Status**: Experimental - Under active development

## Purpose and Motivation

### The Optimizer Consistency Problem

Modern query optimizers are expected to recognize semantically equivalent queries and produce similar execution plans regardless of how the SQL is written. However, in practice, optimizers often exhibit significant performance variations based on syntax choices that shouldn't matter.

**Example**: These three queries are semantically identical but may perform very differently:

```sql
SELECT c_name, c_acctbal
FROM customer
WHERE c_acctbal > (SELECT AVG(c_acctbal) FROM customer);

WITH avg_balance AS (SELECT AVG(c_acctbal) AS avg_bal FROM customer)
SELECT c_name, c_acctbal
FROM customer, avg_balance
WHERE c_acctbal > avg_bal;

SELECT c_name, c_acctbal
FROM (
    SELECT c_name, c_acctbal, AVG(c_acctbal) OVER () AS avg_bal
    FROM customer
) WHERE c_acctbal > avg_bal;
```

The three queries use a correlated subquery, a `WITH` clause and a window function, in that order.

A mature optimizer should recognize these equivalences and produce similar plans. TPC-Havoc systematically tests whether this holds across hundreds of query variations.

### Why This Matters

1. **Optimizer Validation**: Identifies weaknesses in query optimization logic
2. **Performance Predictability**: Reveals when syntax choices cause unexpected slowdowns
3. **SQL Portability**: Tests whether queries perform consistently across syntax styles
4. **Developer Guidance**: Shows which SQL patterns are well-optimized vs problematic
5. **Regression Detection**: Catches optimizer regressions across database versions

## How It Works

### Transformation Patterns

TPC-Havoc applies 10 universal SQL transformation patterns to each TPC-H query:

1. **Scalar Subqueries**: Convert aggregations to correlated scalar subqueries in SELECT
2. **HAVING Clauses**: Add post-aggregation filtering even when logically redundant
3. **Explicit JOINs**: Convert implicit joins to explicit INNER/LEFT JOIN syntax
4. **UNION Operations**: Split complex logic into separate queries combined via UNION
5. **Common Table Expressions**: Use WITH clauses to pre-compute intermediate results
6. **Derived Tables**: Wrap queries in inline views for step-by-step computation
7. **Computed Columns**: Pre-calculate complex expressions in CTEs
8. **WHERE IN/EXISTS**: Convert between IN lists, EXISTS, and JOIN patterns
9. **Window Functions**: Replace GROUP BY aggregations with window functions
10. **CASE Expressions**: Add conditional processing throughout the query

### Variant Generation

Each TPC-H query undergoes systematic transformation:

```
TPC-H Query 1 (Revenue by lineitem)
    ├── Q1_V1: Scalar subqueries variant
    ├── Q1_V2: HAVING clause variant
    ├── Q1_V3: Explicit JOINs variant
    ├── ...
    └── Q1_V10: CASE expressions variant

Result: 10 variants that must produce identical results
```

This process repeats for all 22 TPC-H queries, yielding **220 total query variants**.

## Quick Start

```python
from benchbox import TPCHavoc

benchmark = TPCHavoc(scale_factor=1.0)

benchmark.generate_data()

import duckdb
conn = duckdb.connect(":memory:")
benchmark.load_data_to_database(conn)

original_q1 = benchmark.get_query("Q1")

variants_q1 = [benchmark.get_query(f"Q1_V{i}") for i in range(1, 11)]

for i, variant in enumerate(variants_q1, 1):
    result = conn.execute(variant).fetchdf()
    print(f"Q1_V{i}: {len(result)} rows")
```

This example initializes the benchmark (which inherits TPC-H data and schema), generates and loads the data, fetches the original TPC-H Query 1 and all 10 variants of Query 1, and then runs the variants and compares them.

## Use Cases

### 1. Optimizer Robustness Testing

Test whether your database optimizer produces consistent plans across syntax variations:

```python
from benchbox import TPCHavoc
import time

benchmark = TPCHavoc(scale_factor=1.0)
conn = setup_database()

results = {}
for variant_id in range(1, 11):
    query = benchmark.get_query(f"Q8_V{variant_id}")

    start = time.time()
    result = conn.execute(query).fetchdf()
    elapsed = time.time() - start

    results[f"Q8_V{variant_id}"] = elapsed

times = list(results.values())
print(f"Min: {min(times):.2f}s, Max: {max(times):.2f}s")
print(f"Variance: {max(times) / min(times):.2f}x")
```

`setup_database()` stands for your own database connection. The example tests the Query 8 variants (an 8-way join) and reports the variance across them.

**Expected Result**: Low variance (< 2x) indicates robust optimizer
**Problem Indicator**: High variance (> 5x) reveals optimizer weaknesses

### 2. Performance Regression Detection

Catch optimizer regressions between database versions:

```bash
baseline_result=$(benchbox run --benchmark tpchavoc --platform postgres --scale 1.0 --output ./baseline-run --quiet)
new_result=$(benchbox run --benchmark tpchavoc --platform postgres --scale 1.0 --output ./new-run --quiet)

benchbox compare "$baseline_result" "$new_result" --fail-on-regression 50%
```

The first run is the baseline on the current database version, and the second runs after the upgrade. The `compare` command fails if any result regresses by more than 50%.

### 3. Cross-Database Optimizer Comparison

Compare optimizer maturity across database systems:

```python
from benchbox import TPCHavoc

benchmark = TPCHavoc(scale_factor=0.1)
benchmark.generate_data()

databases = {
    "DuckDB": duckdb_connection,
    "ClickHouse": clickhouse_connection,
    "Snowflake": snowflake_connection
}

for db_name, conn in databases.items():
    benchmark.load_data_to_database(conn)

    variances = []
    for query_num in range(1, 23):
        times = []
        for variant in range(1, 11):
            query = benchmark.get_query(f"Q{query_num}_V{variant}")
            elapsed = measure_query_time(conn, query)
            times.append(elapsed)

        variance = max(times) / min(times)
        variances.append(variance)

    avg_variance = sum(variances) / len(variances)
    print(f"{db_name}: Average variance {avg_variance:.2f}x")
```

### 4. SQL Pattern Analysis

Identify which SQL patterns are well-optimized:

```python
from collections import defaultdict

pattern_performance = defaultdict(list)

for query_num in range(1, 23):
    for variant_num in range(1, 11):
        query_id = f"Q{query_num}_V{variant_num}"
        pattern = benchmark.get_variant_pattern(variant_num)

        elapsed = measure_query(query_id)
        pattern_performance[pattern].append(elapsed)

for pattern, times in pattern_performance.items():
    avg_time = sum(times) / len(times)
    print(f"{pattern}: {avg_time:.3f}s average")
```

`get_variant_pattern` returns the pattern name, such as "CTE" or "Window Function". The loop finds which patterns are fastest and slowest on average.

## Key Features

### Result Equivalence Guarantees

Every variant is validated to produce **identical results** to the original TPC-H query:

- Exact row counts match
- Column values match within floating-point tolerance
- Sort order preserved where specified
- Automated validation in test suite

### SQL Standard Compliance

All transformations use **SQL:2016 standard features**:

- No database-specific extensions
- No vendor-specific optimizations
- Portable across DuckDB, ClickHouse, Snowflake, BigQuery, Redshift, and other SQL:2016 compliant databases
- No proprietary hints or directives

### TPC-H Integration

TPC-Havoc inherits all TPC-H infrastructure:

- Uses identical data and schema as TPC-H
- Same scale factors (0.001 to 1000+)
- Same data generation tools
- No additional data preparation required

## Query Catalog

### Simple Aggregation Queries
- **Q1, Q6**: Aggregation syntax variations, window functions, CTEs

### Join-Heavy Queries
- **Q3, Q5, Q7, Q8, Q9, Q10, Q12, Q14**: JOIN syntax variations, subquery conversions

### Subquery-Intensive Queries
- **Q2, Q4, Q11, Q16, Q17, Q18, Q20, Q21, Q22**: Subquery transformations, EXISTS ↔ IN conversions

### Complex Aggregation Queries
- **Q13, Q15**: Multi-level aggregation patterns, derived tables

### Set Operation Queries
- **Q19**: Complex OR conditions, UNION splits, CASE expressions

## CLI Usage

```bash
benchbox run --benchmark tpchavoc --platform duckdb --scale 1.0

benchbox run --benchmark tpchavoc --platform clickhouse-local --queries Q1_V1,Q1_V2,Q1_V3

benchbox run --benchmark tpchavoc --platform duckdb --queries Q8_V1,Q8_V2,Q8_V3

benchbox run --benchmark tpchavoc --platform snowflake
```

The commands run all queries, specific variants, and all variants of one query. There is no pattern option, so list the variants explicitly. The last command shows that variance across variants is reported in the result bundle.

## Performance Characteristics

### Expected Query Time Variance

| Optimizer Quality | Typical Variance | Description |
|------------------|------------------|-------------|
| Excellent | 1.0-1.5x | Recognizes most equivalences |
| Good | 1.5-3.0x | Handles common patterns well |
| Fair | 3.0-5.0x | Struggles with some syntax |
| Poor | 5.0x+ | Significant optimization gaps |

### Scale Factor Recommendations

- **0.01**: Development, fast iteration (< 1 minute per variant)
- **0.1**: Integration testing (< 5 minutes per variant)
- **1.0**: Standard benchmarking (< 30 minutes per variant)
- **10.0**: Stress testing (hours per variant)

## Limitations and Considerations

### Experimental Status

TPC-Havoc is under active development. Current limitations:

- Not all 220 variants implemented yet (progressive rollout)
- Variant coverage varies by query complexity
- Some transformations may not be applicable to all queries
- Performance baselines still being established

### Interpretation Guidelines

**High variance doesn't always mean bad optimizer**:

1. **Expected variance**: Some syntax genuinely requires different execution strategies
2. **Hardware effects**: I/O, caching, and parallelism can introduce variance
3. **Data characteristics**: Skewed data may cause legitimate plan differences
4. **Query complexity**: Complex queries naturally show more variance

**Use TPC-Havoc to**:
- Identify problematic patterns for further investigation
- Track optimizer improvements over time
- Compare relative maturity across databases

**Don't use TPC-Havoc to**:
- Make absolute "Database X is better" claims
- Optimize individual queries (use TPC-H for that)
- Replace comprehensive performance testing

## API Reference

### TPCHavoc Class

```python
class TPCHavoc(BaseBenchmark):

    def __init__(self, scale_factor: float = 1.0, output_dir: Optional[str] = None):
        pass

    def get_query(self, query_id: str, *, params: Optional[dict] = None) -> str:
        pass

    def get_all_variants(self, query_num: int) -> dict[str, str]:
        pass

    def get_variant_description(self, query_id: str) -> str:
        pass

    def validate_variant(self, query_id: str, connection) -> bool:
        pass
```

- `TPCHavoc(...)` initializes the benchmark and inherits from TPC-H.
- `get_query` returns a query variant by ID, such as `Q1`, `Q1_V5` or `Q8_V10`.
- `get_all_variants` returns all 10 variants for a query number from 1 to 22.
- `get_variant_description` describes the transformation pattern used.
- `validate_variant` checks that the variant produces results identical to the original.

## Related Benchmarks

- **[TPC-H](tpc-h.md)**: Base benchmark for data and schema
- **[Join Order Benchmark](join-order.md)**: Alternative optimizer testing approach
- **[Read Primitives](read-primitives.md)**: Lower-level operation testing

## Research and References

TPC-Havoc is inspired by research on query optimizer testing:

- **Equivalence testing**: Ensuring semantically equivalent queries produce same results
- **Performance variance analysis**: Measuring optimizer sensitivity to syntax
- **SQL transformation patterns**: Cataloging equivalent query forms

## Future Development

Planned enhancements:

- Complete implementation of all 220 variants
- Automated variance analysis and reporting
- Query plan comparison tools
- Extended pattern library beyond 10 universal patterns
- Integration with query plan visualization

## License

Copyright 2026 Joe Harris / BenchBox Project

Licensed under the MIT License.
