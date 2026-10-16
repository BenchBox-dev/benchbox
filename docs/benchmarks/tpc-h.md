<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# TPC-H Benchmark

```{tags} intermediate, concept, tpc-h
```

> **CLI name:** `tpch` - use `benchbox run --benchmark tpch`

## Overview

TPC-H tests data warehouse performance using 22 analytical queries.

The queries simulate business intelligence workloads and test complex SQL operations on large datasets.

## Key Features

- 22 analytical queries
- 8 table wholesale supplier schema
- Parameterized queries for reproducible tests
- Multiple scale factors (SF 1 = ~1GB)
- OLAP operation coverage
- SQL dialect support
- Stream processing for throughput tests

## Schema Description

TPC-H uses 8 tables modeling a wholesale supplier business:

### Core Tables

| Table | Purpose | Approximate Rows (SF 1) |
|-------|---------|-------------------------|
| **REGION** | Geographic regions | 5 |
| **NATION** | Countries within regions | 25 |
| **SUPPLIER** | Supplier information | 10,000 |
| **CUSTOMER** | Customer information | 150,000 |
| **PART** | Parts catalog | 200,000 |
| **PARTSUPP** | Part-supplier relationships | 800,000 |
| **ORDERS** | Order header information | 1,500,000 |
| **LINEITEM** | Order line items (fact table) | 6,000,000 |

### Schema Relationships

```{mermaid}
erDiagram
    REGION ||--o{ NATION : contains
    NATION ||--o{ CUSTOMER : located_in
    NATION ||--o{ SUPPLIER : located_in
    CUSTOMER ||--o{ ORDERS : places
    SUPPLIER ||--o{ PARTSUPP : supplies
    PART ||--o{ PARTSUPP : supplied_as
    ORDERS ||--o{ LINEITEM : contains
    PART ||--o{ LINEITEM : ordered_as
    SUPPLIER ||--o{ LINEITEM : supplied_by
```

### Key Columns

**LINEITEM** (Fact Table):
- `L_ORDERKEY`, `L_LINENUMBER` (Composite Primary Key)
- `L_PARTKEY`, `L_SUPPKEY` (Foreign Keys)
- `L_QUANTITY`, `L_EXTENDEDPRICE`, `L_DISCOUNT`, `L_TAX` (Measures)
- `L_SHIPDATE`, `L_COMMITDATE`, `L_RECEIPTDATE` (Temporal)

**ORDERS** (Order Header):
- `O_ORDERKEY` (Primary Key)
- `O_CUSTKEY` (Foreign Key to Customer)
- `O_ORDERDATE`, `O_TOTALPRICE`, `O_ORDERSTATUS`

## Query Characteristics

The 22 TPC-H queries test different aspects of query processing. Each query
below links to its [query template](queries/tpch/index.md) — a representative
SQL and DataFrame rendering plus how to extract the exact statement for your
platform and scale.

### Query Categories

| Query | Type | Key Features |
|-------|------|-------------|
| [Q1](queries/tpch/q1.md) | **Pricing Summary Report** | Aggregation, GROUP BY, no joins |
| [Q2](queries/tpch/q2.md) | **Minimum Cost Supplier** | Nested subqueries, correlated queries |
| [Q3](queries/tpch/q3.md) | **Shipping Priority** | 3-table join, ORDER BY, LIMIT |
| [Q4](queries/tpch/q4.md) | **Order Priority Checking** | EXISTS subquery, temporal predicates |
| [Q5](queries/tpch/q5.md) | **Local Supplier Volume** | 6-table join, regional aggregation |
| [Q6](queries/tpch/q6.md) | **Forecasting Revenue** | Simple filter, aggregation |
| [Q7](queries/tpch/q7.md) | **Volume Shipping** | Nation-to-nation shipping analysis |
| [Q8](queries/tpch/q8.md) | **National Market Share** | Complex aggregation, CASE expressions |
| [Q9](queries/tpch/q9.md) | **Product Type Profit** | Multi-year profit analysis |
| [Q10](queries/tpch/q10.md) | **Returned Item Reporting** | Customer analysis with joins |
| [Q11](queries/tpch/q11.md) | **Important Stock** | Subquery with aggregation |
| [Q12](queries/tpch/q12.md) | **Shipping Modes** | Mail type analysis |
| [Q13](queries/tpch/q13.md) | **Customer Distribution** | Outer join, customer classification |
| [Q14](queries/tpch/q14.md) | **Promotion Effect** | Revenue analysis with CASE |
| [Q15](queries/tpch/q15.md) | **Top Supplier** | VIEW creation, supplier ranking |
| [Q16](queries/tpch/q16.md) | **Parts/Supplier Relationship** | Complex filtering, NOT IN |
| [Q17](queries/tpch/q17.md) | **Small-Quantity-Order Revenue** | Subquery with aggregation |
| [Q18](queries/tpch/q18.md) | **Large Volume Customer** | Customer ranking by volume |
| [Q19](queries/tpch/q19.md) | **Discounted Revenue** | Complex OR conditions |
| [Q20](queries/tpch/q20.md) | **Potential Part Promotion** | Multi-level subqueries |
| [Q21](queries/tpch/q21.md) | **Suppliers Who Kept Orders** | Multiple EXISTS/NOT EXISTS |
| [Q22](queries/tpch/q22.md) | **Global Sales Opportunity** | Geographic analysis |

### Complexity Patterns

- **Simple Queries** (Q1, Q6): Single table aggregation
- **Medium Queries** (Q3, Q5, Q10): Multi-table joins with aggregation
- **Complex Queries** (Q2, Q15, Q20, Q21): Nested subqueries, correlated queries
- **Analytical Queries** (Q7, Q8, Q9): Time-series and trend analysis

## Official TPC-H Metrics

BenchBox reports Power@Size and Throughput@Size. TPC-H refresh functions are
opt-in, so existing runs remain query-only:

```text
--benchmark-option tpch_refresh_functions=true
--benchmark-option tpch_refresh_first_set=1
```

`tpch_refresh_functions` defaults to `false`; `tpch_refresh_first_set` defaults
to the positive integer `1`. DBGen creates the update files in a temporary
directory before measurement. If enabled, Power runs RF1 for set F, all 22
queries, then RF2 for set F; only the final measurement iteration applies
refresh functions. Power@Size uses the geometric mean of the 22 query times
plus RF1 and RF2 (24 terms). With the option disabled, it uses the 22 query
times.

The throughput phase starts one refresh stream alongside its S query streams.
It runs S serial RF1/RF2 pairs on sets F+1 through F+S. The measured interval
runs from the earliest query or refresh stream start to the latest query or
refresh stream end. Refresh safety checks are not timed: the RF1 and RF2
precondition checks for every set run before the interval starts, and the RF2
postcondition checks run after it ends. A failed check or refresh function
records function evidence and withholds Throughput@Size.

Refresh functions permanently change the database. No set cursor is saved.
After a run using Power set F and throughput sets F+1..F+S, pass
`tpch_refresh_first_set=F+S+1` on the next run, or reload the base data and
restart at set 1. For a power-only run, the next set is F+1.

Refresh support is declared per adapter. It is enabled for DuckDB, SQLite,
PostgreSQL and the adapters built on it, Snowflake, and SingleStore. Redshift
and other adapters do not declare it yet. Power or throughput with refresh
enabled fails clearly on DataFrame engines and undeclared adapters. The
standalone maintenance phase on an undeclared adapter completes with one
skipped `maintenance_not_supported` row and runs no refresh SQL.

QphH@Size is exported only for official TPC-H results when Power completed all
22 queries and successful RF1/RF2 on F, throughput completed at least two
streams with one successful RF1/RF2 pair per stream on F+1..F+S, and neither
result contains failed queries. The composite is
`round(sqrt(Power@Size × Throughput@Size), 1)`. Unqualified results omit
`qphh_at_size` and include `qphh_unavailable` reasons. Separate Power and
throughput result files qualify only when both carry refresh evidence and
their update sets are consecutive. `benchbox metrics qphh` applies the same
gate and reports the refusal reason.

The implementation does not yet apply the specification's 0.01-second RF
rounding, 1000x clamp, upward Ts rounding, or two-run lower-score rule.

### Refresh Functions (RF1 / RF2)

The standalone maintenance phase uses the same DBGen update-set generator and
transactional executors as Power and throughput. RF1 rejects already-applied
order keys; RF2 verifies its delete keys before deleting LINEITEM and ORDERS.
Rows are processed in 1,000-row chunks. The SQL preview remains available for
dry runs.

### DataFrame Coverage

All 22 TPC-H queries have DataFrame equivalents in
`benchbox/core/tpch/dataframe_queries.py` (one `qN_expression_impl` function
per query, plus native-pandas variants for several queries). The DataFrame
and SQL paths are validated against the same result set.

## Usage Examples

### Basic Query Generation

```python
from benchbox import TPCH

tpch = TPCH(scale_factor=1.0, output_dir="tpch_data")

data_files = tpch.generate_data()

queries = tpch.get_queries()
print(f"Generated {len(queries)} queries")

query_1 = tpch.get_query(1, seed=42)
print(query_1)
```

### Data Generation with Custom Scale

```python
tpch_small = TPCH(scale_factor=0.1, output_dir="tpch_small")
data_files = tpch_small.generate_data()

for file_path in data_files:
    table_name = file_path.stem
    print(f"{table_name}: {file_path}")
```

### SQL Dialect Translation

Queries are automatically translated to the requested dialect via sqlglot:

```python
query_postgres = tpch.get_query(1, dialect="postgres")
query_duckdb = tpch.get_query(1, dialect="duckdb")
query_mysql = tpch.get_query(1, dialect="mysql")
```

### DuckDB Integration Example

```python
import duckdb
from benchbox import TPCH

tpch = TPCH(scale_factor=0.01, output_dir="tpch_tiny")
data_files = tpch.generate_data()

conn = duckdb.connect("tpch.duckdb")
schema_sql = tpch.get_create_tables_sql()
conn.execute(schema_sql)

for table_name in tpch.get_available_tables():
    file_path = tpch.tables[table_name.upper()]
    table_name_lower = table_name.lower()

    conn.execute(f"""
        INSERT INTO {table_name_upper}
        SELECT * FROM read_csv('{file_path}',
                              delim='|',
                              header=false)
    """)

for query_id in range(1, 23):
    query_sql = tpch.get_query(query_id, dialect="duckdb")
    result = conn.execute(query_sql).fetchall()
    print(f"Q{query_id}: {len(result)} rows")
```

### Stream Processing

```python
stream_files = tpch.generate_streams(
    num_streams=4,
    rng_seed=42,
    streams_output_dir="streams"
)

for i, stream_info in enumerate(tpch.get_all_streams_info()):
    print(f"Stream {i}: {stream_info['query_count']} queries")
    print(f"  Permutation: {stream_info['permutation']}")
    print(f"  Output file: {stream_info['output_file']}")
```

> **DuckDB concurrency note:** The TPC-H throughput phase shares a single
> DuckDB connection across streams and issues each stream's queries through
> a per-stream cursor (`_make_stream_cursor` in
> `benchbox/platforms/base/execution.py`). DuckDB's Python client is
> thread-safe only at the cursor level, not the connection level - see the
> [DuckDB multi-thread guide](https://duckdb.org/docs/stable/guides/python/multiple_threads).
> If you run BenchBox throughput tests from your own harness against DuckDB,
> follow the same pattern: one connection, one cursor per thread.

> **GIL / result-materialization note:** for embedded engines (DuckDB,
> SQLite, DataFusion, ...) that release the GIL during native query
> execution but not while converting result rows into Python objects,
> fetching a query's full result set (`cursor.fetchall()`) re-acquires the
> GIL for the duration of that conversion. Every concurrent stream that
> materializes a result at the same time serializes on this step, so larger
> result sets and higher stream counts inflate `execution_time_seconds` (and
> therefore depress `Throughput@Size`) independently of the engine's actual
> query-execution concurrency - an artifact of Python result handling, not
> of the platform under test. BenchBox's throughput drivers
> (`benchbox/core/tpch/throughput_test.py`,
> `benchbox/core/tpcds/throughput_test.py`) avoid forcing this
> materialization to compute `result_count`: they read the platform
> adapter's already-computed row count directly
> (`benchbox.platforms.base.connection_wrappers.count_query_rows`) instead
> of calling `fetchall()` a second time. If you build a custom harness that
> calls `fetchall()` per stream to inspect or log results, be aware that
> under this GIL constraint doing so measures Python object materialization
> throughput as much as it measures the database engine.

## Query Characteristics

### Query Complexity Categories

**Simple Queries (low complexity):**
- Q1, Q6: Single-table aggregation with simple predicates
- Q14: Simple join with CASE expression

**Moderate Queries (medium complexity):**
- Q3, Q5, Q7, Q10, Q12: Multi-table joins with moderate complexity
- Q4, Q13: EXISTS/outer join patterns

**Complex Queries (high complexity):**
- Q2, Q15, Q17, Q20, Q21: Complex nested subqueries
- Q8, Q9: Multi-year analytical queries
- Q16, Q18, Q19, Q22: Complex filtering and ranking

**Note:** Actual execution times vary significantly based on platform, hardware, configuration, and data characteristics. Run benchmarks to measure performance for your specific environment.

### Resource Utilization

| Query Type | CPU Usage | Memory Usage | I/O Pattern |
|------------|-----------|--------------|-------------|
| Aggregation | High | Medium | Sequential |
| Multi-join | Medium | High | Random |
| Subquery | High | High | Mixed |
| Analytical | Medium | Medium | Sequential |

## Configuration Options

### Scale Factor Guidelines

| Scale Factor | Data Size | Use Case |
|-------------|-----------|----------|
| 0.01 | ~10 MB | Development, unit testing |
| 0.1 | ~100 MB | Integration testing |
| 1 | ~1 GB | Standard benchmark |
| 10 | ~10 GB | Performance testing |
| 100+ | ~100+ GB | Production simulation |

### Advanced Configuration

`verbose=True` enables detailed logging and `parallel=4` runs data generation in parallel. In `get_query()`, `seed` makes parameters reproducible, `scale_factor` overrides the scale factor, and `dialect` sets the target dialect.

```python
tpch = TPCH(
    scale_factor=1.0,
    output_dir="tpch_data",
    verbose=True,
    parallel=4
)

query = tpch.get_query(
    query_id=1,
    seed=42,
    scale_factor=1.0,
    dialect="duckdb"
)
```

## Integration Examples

### Databricks Integration

```python
from benchbox import TPCH

tpch = TPCH(scale_factor=10, output_dir="/dbfs/tpch_sf10")
data_files = tpch.generate_data()

for table_name in tpch.get_available_tables():
    spark.sql(f"""
        CREATE TABLE IF NOT EXISTS {table_name}
        USING DELTA
        LOCATION '/dbfs/tpch_sf10/{table_name.lower()}.tbl'
        OPTIONS (
            'path' '/dbfs/tpch_sf10/{table_name.lower()}.tbl',
            'delimiter' '|',
            'header' 'false'
        )
    """)

for query_id in range(1, 23):
    query_sql = tpch.get_query(query_id, dialect="spark")
    df = spark.sql(query_sql)
    df.show()
```

### Performance Testing Framework

```python
import time
from statistics import mean, median

def benchmark_queries(tpch, connection, query_list=None):
    if query_list is None:
        query_list = range(1, 23)

    results = {}

    for query_id in query_list:
        times = []

        for run in range(3):
            query_sql = tpch.get_query(query_id, dialect="duckdb")

            start_time = time.time()
            result = connection.execute(query_sql).fetchall()
            execution_time = time.time() - start_time

            times.append(execution_time)

        results[query_id] = {
            'times': times,
            'mean': mean(times),
            'median': median(times),
            'rows': len(result)
        }

    return results

results = benchmark_queries(tpch, conn)
for query_id, stats in results.items():
    print(f"Q{query_id}: {stats['median']:.2f}s ({stats['rows']} rows)")
```

## Best Practices

### Data Generation
1. **Use appropriate scale factors** for your testing needs
2. **Generate data once** and reuse for multiple test runs
3. **Parallel generation** for large scale factors (SF ≥ 10)
4. **Validate data integrity** after generation

### Query Execution
1. **Warm up queries** before timing measurements
2. **Use consistent seeds** for reproducible parameter generation
3. **Test incremental complexity** (start with simple queries)
4. **Monitor resource usage** during execution

### Performance Testing
1. **Run multiple iterations** to account for variance
2. **Clear caches** between runs for cold performance
3. **Use representative scale factors** for your use case
4. **Profile both power and throughput** metrics

## Common Issues and Solutions

### Data Generation Issues

**Issue: Out of memory during generation**

Solution: use a smaller scale factor or increase the number of parallel processes.
```python
tpch = TPCH(scale_factor=1.0, parallel=8)
```

**Issue: Slow data generation**

Solution: check disk I/O and use SSD storage.
```python
tpch = TPCH(output_dir="/fast/ssd/path")
```

### Query Execution Issues

**Issue: Query timeout on large scale factors**

Solution: start with a smaller scale factor and optimize queries.
```python
tpch = TPCH(scale_factor=0.1)
query = tpch.get_query(21, dialect="duckdb")
```

**Issue: Parameter substitution errors**

Solution: use explicit seeds for reproducible parameters.
```python
query = tpch.get_query(1, seed=42)
```

## See Also

### Related Benchmarks

- **[TPC-DS Benchmark](tpc-ds.md)** - More complex decision support queries (99 queries)
- **[TPC-DI Benchmark](tpc-di.md)** - Data integration and ETL testing
- **[Star Schema Benchmark (SSB)](ssb.md)** - Simplified OLAP testing derived from TPC-H
- **[TPCHavoc](tpc-havoc.md)** - TPC-H query variants for optimizer stress testing
- **[Read Primitives Benchmark](read-primitives.md)** - Microbenchmarks using TPC-H data
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
- **[Platform Selection Guide](../platforms/platform-selection-guide.md)** - Choose the right database
- **[Platform Quick Reference](../platforms/quick-reference.md)** - Setup for each platform
- **[Data Generation Guide](../usage/data-generation.md)** - Advanced generation options
- **[Dry Run Mode](../usage/dry-run.md)** - Preview queries before execution

### Official TPC-H Resources

- **[Official TPC-H Benchmark Guide](../guides/tpc/tpc-h-official-guide.md)** - Compliance testing with BenchBox
- **[TPC Validation Guide](../guides/tpc/tpc-validation-guide.md)** - Result validation and compliance
- **[TPC Patterns Usage](../guides/tpc/tpc-patterns-usage.md)** - Parameter generation and stream patterns

### External Resources

- **[TPC-H Specification](http://www.tpc.org/tpch/)** - Official specification document
- **[TPC-H Published Results](http://www.tpc.org/tpch/results/)** - Vendor-submitted results
- **[TPC White Papers](http://www.tpc.org/information/white_papers.asp)** - Research using TPC benchmarks
