<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Read Primitives Benchmark

```{tags} intermediate, concept, read-primitives, custom-benchmark, performance
```

> **CLI name:** `read_primitives` - use `benchbox run --benchmark read_primitives`
>
> **Query templates:** [every Read Primitives query](queries/read_primitives/index.md)

## Overview

The Read Primitives benchmark provides focused testing of fundamental database operations without the complexity of business logic. It offers systematic testing of specific database capabilities through 157 primitive SQL queries organized into 31 operation categories.

The benchmark uses the familiar TPC-H schema for data generation while focusing on isolated testing of individual database features. This approach enables developers to identify performance regressions, validate database engine optimizations, and perform focused hardware and configuration comparisons.

## Origins and Attribution

The Read Primitives benchmark combines queries from multiple established sources with BenchBox extensions.

### Primary Sources

#### 1. Apache Impala targeted-perf Workload

The majority of queries derive from **Apache Impala's targeted-perf workload**, a comprehensive suite of performance-focused SQL queries designed to test fundamental database operations in isolation.

- **Source**: [Apache Impala testdata/workloads/targeted-perf](https://github.com/apache/impala/tree/master/testdata/workloads/targeted-perf)
- **License**: Apache License 2.0
- **Purpose**: Focused performance testing of database primitives without business logic complexity

The targeted-perf workload provides systematic testing of:
- Aggregation operations (simple, grouped, materialized)
- Broadcast and shuffle joins
- Filter predicates (selective, non-selective, conjunct ordering)
- Exchange operations (broadcast, shuffle)
- Sorting and ordering operations
- String operations and pattern matching

#### 2. Optimizer Sniff Tests

The 13 `optimizer_*` queries are based on **Justin Jaffray's optimizer sniff test** concepts, which provide focused tests for common query optimizer patterns.

- **Author**: Justin Jaffray
- **Source**: [A Sniff Test for Some Query Optimizers](https://buttondown.com/jaffray/archive/a-sniff-test-for-some-query-optimizers/)
- **Purpose**: Evaluate query optimizer effectiveness across different databases

These queries test optimizer capabilities including:
- Subquery decorrelation (EXISTS to semijoin)
- Redundant operation elimination (DISTINCT elimination)
- Common subexpression elimination
- Predicate pushdown through joins
- Join reordering based on cardinality
- Limit pushdown optimization
- Scalar subquery flattening
- Constant folding and expression simplification
- Column pruning (projection pushdown)
- Subquery transformation (IN to EXISTS)
- Set operation optimization
- Runtime filter generation

See [Optimizer Tests Documentation](../advanced/optimizer-tests.md) for detailed information.

### BenchBox Extensions

BenchBox extends these foundations with modern SQL capabilities:
- **Window functions** (ROW_NUMBER, RANK, LAG, LEAD, aggregate windows)
- **OLAP operations** (CUBE, ROLLUP, GROUPING SETS)
- **Statistical functions** (PERCENTILE_CONT, VARIANCE, STDDEV, CORR)
- **JSON operations** (extraction, aggregation, path queries)
- **Full-text search** (MATCH AGAINST with Boolean mode)
- **Time series analysis** (trend calculation, month-over-month growth)
- **QUALIFY clause** (filtering on window function results)
- **MIN_BY/MAX_BY** aggregate functions

These extensions reflect modern SQL:2016+ features and real-world analytical workload patterns.

### Attribution

- **Impala-derived queries**: Copyright © Apache Software Foundation, Apache License 2.0
- **Optimizer test queries**: Based on concepts by Justin Jaffray
- **BenchBox extensions and implementation**: Copyright 2026 Joe Harris / BenchBox Project, MIT License

## Key Features

- **157 comprehensive primitive queries** covering fundamental database operations
- **31 operation categories** for systematic testing (aggregation, joins, filters, window functions, etc.)
- **Query categorization system** with metadata-driven organization
- **Optimized data generation** leveraging existing TPC-H infrastructure
- **Smart performance testing** with resource-aware optimization capabilities
- **Enhanced developer experience** with intelligent query discovery
- **Extensible architecture** supporting custom primitive operations
- **Multiple scale factors** for different testing scenarios (micro to xlarge)

## Value Proposition

The Read Primitives benchmark provides:

- **Isolated testing** of specific database operations without business logic complexity
- **Performance regression detection** for individual database capabilities
- **Development validation** for database engine optimization features
- **Focused benchmarking** for hardware and configuration comparison
- **Unit testing capabilities** for database-specific features
- **Optimizer sniff tests** for query planner validation

## Query Categories

The 157 primitive queries are organized into 31 categories, each targeting specific database operations:

### Core Operation Categories

| Category | Purpose | Example Operations |
|----------|---------|-------------------|
| **aggregation** | Basic aggregation functions | SUM, COUNT, AVG, MIN, MAX |
| **broadcast_join** | Small-to-large table joins | Dimension to fact table joins |
| **filter** | Predicate evaluation | WHERE clauses with various selectivity |
| **group_by** | Grouping operations | Single and multi-column grouping |
| **join** | General join operations | Inner, outer, cross joins |
| **limit** | Result set limiting | TOP-N queries |
| **order_by** | Sorting operations | Single and multi-column sorting |
| **subquery** | Nested queries | Correlated and uncorrelated subqueries |
| **union** | Set operations | UNION, UNION ALL |
| **window_function** | Window/analytical functions | ROW_NUMBER, RANK, LAG, LEAD |

### Advanced Operation Categories

| Category | Purpose | Example Operations |
|----------|---------|-------------------|
| **case_when** | Conditional logic | CASE expressions |
| **cast** | Type conversions | Data type casting |
| **coalesce** | NULL handling | COALESCE, NULLIF |
| **common_table_expression** | CTEs | WITH clauses |
| **cross_join** | Cartesian products | Cross join patterns |
| **date_functions** | Temporal operations | Date arithmetic, extraction |
| **distinct** | Deduplication | SELECT DISTINCT |
| **exists** | Existence checks | EXISTS/NOT EXISTS subqueries |
| **in_list** | Set membership | IN clause with lists |
| **like** | Pattern matching | LIKE operations |
| **null_handling** | NULL operations | IS NULL, IS NOT NULL |

### Specialized Categories

| Category | Purpose | Example Operations |
|----------|---------|-------------------|
| **optimizer** | Optimizer stress tests | Equivalent query variations |
| **partition_by** | Partitioned analytics | Window functions with PARTITION BY |
| **self_join** | Table self-joins | Hierarchical queries |
| **string_functions** | String operations | CONCAT, SUBSTRING, LENGTH |
| **type_conversion** | Advanced casting | Complex type transformations |

## Schema Description

The Read Primitives benchmark reuses the **TPC-H schema** for data generation, providing familiar and well-understood data patterns. This allows developers to focus on query performance without learning a new schema.

### TPC-H Tables Used

| Table | Purpose | Approximate Rows (SF 1) |
|-------|---------|-------------------------|
| **CUSTOMER** | Customer information | 150,000 |
| **LINEITEM** | Order line items (fact table) | 6,000,000 |
| **NATION** | Countries within regions | 25 |
| **ORDERS** | Order header information | 1,500,000 |
| **PART** | Parts catalog | 200,000 |
| **PARTSUPP** | Part-supplier relationships | 800,000 |
| **REGION** | Geographic regions | 5 |
| **SUPPLIER** | Supplier information | 10,000 |

### Data Generation

The benchmark leverages the existing TPC-H data generation infrastructure:
- **No additional data files** required beyond TPC-H
- **Consistent data patterns** across all primitive queries
- **Referential integrity** maintained automatically
- **Multiple scale factors** supported for different testing needs

## Usage Examples

### Basic Query Execution

```python
from benchbox import ReadPrimitives

primitives = ReadPrimitives(scale_factor=0.01, output_dir="primitives_data")

data_files = primitives.generate_data()

queries = primitives.get_queries()
print(f"Available: {len(queries)} primitive queries")

agg_query = primitives.get_query("aggregation_sum_basic")
print(agg_query)
```

### Category-Based Testing

```python
aggregation_queries = primitives.get_queries_by_category("aggregation")
join_queries = primitives.get_queries_by_category("join")
window_queries = primitives.get_queries_by_category("window_function")

print(f"Aggregation tests: {len(aggregation_queries)}")
print(f"Join tests: {len(join_queries)}")
print(f"Window function tests: {len(window_queries)}")

for query_id, query_sql in aggregation_queries.items():
    result = conn.execute(query_sql).fetchall()
    print(f"{query_id}: {len(result)} rows")
```

### DuckDB Integration Example

```python
import duckdb
from benchbox import ReadPrimitives
import time

primitives = ReadPrimitives(scale_factor=0.01, output_dir="primitives_tiny")
data_files = primitives.generate_data()

conn = duckdb.connect("primitives.duckdb")
schema_sql = primitives.get_create_tables_sql()
conn.execute(schema_sql)

for table_name in primitives.get_available_tables():
    file_path = primitives.tables[table_name.upper()]

    conn.execute(f"""
        INSERT INTO {table_name}
        SELECT * FROM read_csv('{file_path}',
                              delim='|',
                              header=false)
    """)

categories = ["aggregation", "filter", "join", "group_by", "window_function"]

results = {}
for category in categories:
    print(f"\nTesting {category} primitives...")
    category_queries = primitives.get_queries_by_category(category)

    for query_id, query_sql in category_queries.items():
        start_time = time.time()
        result = conn.execute(query_sql).fetchall()
        execution_time = time.time() - start_time

        results[query_id] = {
            'category': category,
            'time': execution_time,
            'rows': len(result)
        }
        print(f"  {query_id}: {execution_time:.3f}s ({len(result)} rows)")

conn.close()
```

### Regression Testing Framework

```python
import time
from statistics import mean, median
from typing import Dict, List

class PrimitivesRegressionTester:
    def __init__(self, primitives: ReadPrimitives, connection):
        self.primitives = primitives
        self.connection = connection

    def run_regression_suite(self, categories: List[str] = None) -> Dict:
        if categories is None:
            categories = ["aggregation", "filter", "join", "group_by"]

        results = {}

        for category in categories:
            print(f"Running {category} regression tests...")
            category_queries = self.primitives.get_queries_by_category(category)

            for query_id, query_sql in category_queries.items():
                times = []

                for _ in range(3):
                    start_time = time.time()
                    result = self.connection.execute(query_sql).fetchall()
                    execution_time = time.time() - start_time
                    times.append(execution_time)

                results[query_id] = {
                    'category': category,
                    'avg_time': mean(times),
                    'median_time': median(times),
                    'min_time': min(times),
                    'times': times,
                    'rows': len(result)
                }

        return results

    def compare_with_baseline(self, current_results: Dict, baseline_results: Dict,
                             threshold: float = 0.1) -> List[Dict]:
        regressions = []

        for query_id, current in current_results.items():
            if query_id not in baseline_results:
                continue

            baseline = baseline_results[query_id]
            time_increase = (current['median_time'] - baseline['median_time']) / baseline['median_time']

            if time_increase > threshold:
                regressions.append({
                    'query_id': query_id,
                    'category': current['category'],
                    'baseline_time': baseline['median_time'],
                    'current_time': current['median_time'],
                    'increase_pct': time_increase * 100
                })

        return regressions

tester = PrimitivesRegressionTester(primitives, conn)

current_results = tester.run_regression_suite()

regressions = tester.compare_with_baseline(current_results, baseline_results)

if regressions:
    print("\n⚠️  Performance Regressions Detected:")
    for reg in regressions:
        print(f"  {reg['query_id']}: {reg['increase_pct']:.1f}% slower")
else:
    print("\n✅ No regressions detected")
```

The framework runs each query three times for stable measurements. `compare_with_baseline` flags a regression when a query is slower than the baseline by more than `threshold` (0.1 means 10%).

### Performance Profiling

```python
from typing import Dict
import time

def profile_primitive_categories(primitives: ReadPrimitives, connection) -> Dict:

    categories = primitives.get_all_categories()
    profile_results = {}

    for category in categories:
        category_queries = primitives.get_queries_by_category(category)

        times = []
        for query_id, query_sql in category_queries.items():
            start_time = time.time()
            result = connection.execute(query_sql).fetchall()
            execution_time = time.time() - start_time
            times.append(execution_time)

        if times:
            profile_results[category] = {
                'query_count': len(category_queries),
                'total_time': sum(times),
                'avg_time': sum(times) / len(times),
                'min_time': min(times),
                'max_time': max(times)
            }

    sorted_categories = sorted(
        profile_results.items(),
        key=lambda x: x[1]['total_time'],
        reverse=True
    )

    print("\nPerformance Profile by Category:")
    print(f"{'Category':<30} {'Queries':<10} {'Total Time':<15} {'Avg Time':<15}")
    print("-" * 70)

    for category, stats in sorted_categories:
        print(f"{category:<30} {stats['query_count']:<10} "
              f"{stats['total_time']:<15.3f} {stats['avg_time']:<15.3f}")

    return dict(sorted_categories)

profile = profile_primitive_categories(primitives, conn)
```

The categories are sorted by total time to identify bottlenecks.

## Performance Characteristics

### Query Execution Patterns

**Fast Primitives (< 10ms on SF 0.01):**
- Simple filters: Single-column predicates
- Basic aggregations: COUNT, SUM without grouping
- Limit queries: TOP-N without sorting

**Medium Primitives (10-100ms on SF 0.01):**
- Join operations: 2-3 table joins
- Group by operations: Single-column grouping
- Window functions: Basic ROW_NUMBER, RANK

**Slower Primitives (> 100ms on SF 0.01):**
- Complex joins: Multi-table joins with filters
- Multi-column grouping: High cardinality groups
- Advanced window functions: LAG, LEAD with partitioning
- String operations: LIKE, regex patterns

### Scale Factor Guidelines

| Scale Factor | Data Size | Query Times | Use Case |
|-------------|-----------|-------------|----------|
| 0.001 (micro) | ~1 MB | Milliseconds | Unit tests |
| 0.01 (small) | ~10 MB | 10-100ms | CI/CD pipelines |
| 0.1 (medium) | ~100 MB | 100ms-1s | Regression testing |
| 1.0 (large) | ~1 GB | 1-10s | Performance validation |
| 10.0 (xlarge) | ~10 GB | 10s-60s | Stress testing |

## Configuration Options

### Basic Configuration

```python
primitives = ReadPrimitives(scale_factor=0.001, output_dir="primitives_dev")

primitives = ReadPrimitives(scale_factor=0.01, output_dir="primitives_ci")

primitives = ReadPrimitives(scale_factor=1.0, output_dir="primitives_prod")
```

The three settings are for development (fast iteration), CI/CD (balanced performance), and production validation (realistic scale).

### Advanced Configuration

```python
primitives = ReadPrimitives(
    scale_factor=0.1,
    output_dir="primitives_data",
    verbose=True,
    parallel=4,
    cache_data=True
)

query_postgres = primitives.get_query("aggregation_sum_basic", dialect="postgres")
query_duckdb = primitives.get_query("aggregation_sum_basic", dialect="duckdb")
query_mysql = primitives.get_query("aggregation_sum_basic", dialect="mysql")
```

`verbose=True` enables detailed logging, `parallel=4` generates data in parallel, and `cache_data=True` caches the generated data. The `get_query` calls translate the query to each target dialect.

## Best Practices

### Data Generation
1. **Reuse TPC-H data** - Generate once, use for all primitive queries
2. **Use appropriate scale factors** - Start small for development
3. **Cache generated data** - Avoid regeneration overhead
4. **Validate data integrity** - Ensure referential integrity

### Query Execution
1. **Category-based testing** - Focus on relevant primitive categories
2. **Multiple iterations** - Run queries multiple times for stable timings
3. **Clear caches** - Between runs for cold performance testing
4. **Monitor resources** - Track CPU, memory, I/O usage

### Regression Testing
1. **Establish baselines** - Record initial performance metrics
2. **Automated comparison** - Use regression testing frameworks
3. **Threshold-based alerts** - Define acceptable performance variance
4. **Category-level analysis** - Identify performance patterns by category

## Common Issues and Solutions

### Data Generation Issues

**Issue: Data generation too slow**
```python
primitives = ReadPrimitives(scale_factor=0.01, parallel=8)
```
Solution: use a smaller scale factor or parallel generation.

**Issue: Out of disk space**
```python
primitives = ReadPrimitives(scale_factor=0.001)
```
Solution: use the micro scale factor (about 1MB) or clean up old data.

### Query Execution Issues

**Issue: Queries timeout on large scale**
```python
primitives = ReadPrimitives(scale_factor=0.01)
```
Solution: start with a smaller scale factor for fast execution.

**Issue: Inconsistent performance measurements**
```python
times = []
for _ in range(5):
    start = time.time()
    result = conn.execute(query_sql).fetchall()
    times.append(time.time() - start)

median_time = sorted(times)[len(times) // 2]
```
Solution: run multiple iterations and use the median.

## DataFrame Support

The Read Primitives benchmark provides **full DataFrame support** for both expression-family and pandas-family platforms, enabling native DataFrame API benchmarking without SQL.

### Platform Support Matrix

| Platform | Family | Status | Implementation Count |
|----------|--------|--------|---------------------|
| **Polars** | Expression | Full support | 149 queries |
| **PySpark** | Expression | Full support | 149 queries |
| **DataFusion** | Expression | Full support | 149 queries |
| **Pandas** | Pandas | Full support | 149 queries |
| **Dask** | Pandas | Full support | 149 queries |
| **cuDF** | Pandas | Full support | 149 queries |

**Note**: Only 3 queries are skipped for DataFrame platforms (correlated subqueries that have no DataFrame equivalent): [`optimizer_exists_to_semijoin`](queries/read_primitives/optimizer-exists-to-semijoin.md), [`optimizer_in_to_exists`](queries/read_primitives/optimizer-in-to-exists.md), [`optimizer_scalar_subquery_flattening`](queries/read_primitives/optimizer-scalar-subquery-flattening.md).

### DataFrame Execution

Run Read Primitives on DataFrame platforms using the CLI:

```bash
benchbox run --platform polars-df --benchmark read-primitives --scale 0.01

benchbox run --platform pandas-df --benchmark read-primitives --scale 0.01

benchbox run --platform pyspark-df --benchmark read-primitives --scale 0.01
```

The three commands run on Polars, Pandas, and PySpark DataFrames, in that order.

### Programmatic DataFrame Usage

```python
from benchbox import ReadPrimitives
from benchbox.core.read_primitives.dataframe_queries import (
    aggregation_distinct_expression_impl,
    aggregation_distinct_pandas_impl,
)
from benchbox.platforms import get_dataframe_adapter

primitives = ReadPrimitives(scale_factor=0.01)
primitives.generate_data()

polars_adapter = get_dataframe_adapter("polars-df", working_dir=str(primitives.output_dir))
ctx = polars_adapter.create_context()
polars_adapter.load_tables_from_data_source(ctx, primitives.output_dir)

result = aggregation_distinct_expression_impl(ctx)
print(result.collect())

pandas_adapter = get_dataframe_adapter("pandas-df", working_dir=str(primitives.output_dir))
pandas_ctx = pandas_adapter.create_context()
pandas_adapter.load_tables_from_data_source(pandas_ctx, primitives.output_dir)
result = aggregation_distinct_pandas_impl(pandas_ctx)
print(result)
```

`create_context()` returns an empty context, and `working_dir` does not register files. Load the generated tables into the context with `load_tables_from_data_source`. The first half runs an expression-family query on Polars, and the second half does the same for a Pandas-family query.

### Expression Family Example

Expression-family queries use `ctx.col()` and `ctx.lit()` for declarative, lazy evaluation:

```python
def aggregation_groupby_large_expression_impl(ctx):
    lineitem = ctx.get_table("lineitem")
    col = ctx.col

    result = lineitem.group_by("l_orderkey", "l_partkey", "l_suppkey").agg(
        col("l_quantity").sum().alias("total_qty"),
        col("l_extendedprice").mean().alias("avg_price"),
    )
    return result
```

### Pandas Family Example

Pandas-family queries use string column access and boolean indexing:

```python
def filter_selective_pandas_impl(ctx):
    lineitem = ctx.get_table("lineitem")

    result = lineitem[lineitem["l_returnflag"] == "R"]
    return result
```

The filter uses Pandas-style boolean indexing.

### DataFrame Query Categories

All 26 SQL query categories have DataFrame implementations:

| Category | Expression Family | Pandas Family | Notes |
|----------|------------------|---------------|-------|
| aggregation | ✅ | ✅ | COUNT, SUM, AVG, COUNT DISTINCT |
| filter | ✅ | ✅ | WHERE clauses, predicate pushdown |
| groupby | ✅ | ✅ | GROUP BY, ROLLUP, CUBE |
| orderby | ✅ | ✅ | Sorting, TOP-N |
| window | ✅ | ✅ | ROW_NUMBER, RANK, LAG/LEAD |
| qualify | ✅ | ✅ | Window filtering (via two-step pattern) |
| broadcast | ✅ | ✅ | Small table broadcast joins |
| string | ✅ | ✅ | LIKE, CONCAT, SUBSTRING |
| optimizer | ⚠️ | ⚠️ | 3 correlated subqueries skipped |

## See Also

### Related Benchmarks

- **[TPC-H Benchmark](tpc-h.md)** - Standard decision support benchmark (shares same schema)
- **[Write Primitives Benchmark](write-primitives.md)** - Companion write operation testing
- **[TPC-DS Benchmark](tpc-ds.md)** - Complex analytical workloads
- **[Join Order Benchmark](join-order.md)** - Join optimization testing
- **[ClickBench](clickbench.md)** - Real-world analytics benchmark
- **[Benchmark Catalog](index.md)** - Complete list of available benchmarks

### Understanding BenchBox

- **[Architecture Overview](../concepts/architecture.md)** - How BenchBox works
- **[Workflow Patterns](../concepts/workflow.md)** - Common benchmarking workflows
- **[Data Model](../concepts/data-model.md)** - Result schema and analysis
- **[Glossary](../concepts/glossary.md)** - Benchmark terminology

### Practical Guides

- **[Getting Started](../usage/getting-started.md)** - Run your first benchmark
- **[CLI Reference](../reference/cli/index.md)** - Complete command documentation
- **[API Reference](../reference/api-reference.md)** - Detailed API documentation
- **[Data Generation Guide](../usage/data-generation.md)** - Advanced generation options
- **[Platform Selection Guide](../platforms/platform-selection-guide.md)** - Choose the right database
