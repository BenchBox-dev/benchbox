<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# TPC-DS Benchmark

```{tags} intermediate, concept, tpc-ds
```

> **CLI name:** `tpcds` - use `benchbox run --benchmark tpcds`

## Overview

The TPC-DS (Transaction Processing Performance Council - Decision Support) benchmark is the most systematic and complex decision support benchmark available today. TPC-DS models a retail data warehouse environment with sophisticated analytical queries that test advanced database features including window functions, multi-CTE set operations, OLAP operations, and complex join patterns.

TPC-DS represents the evolution of decision support benchmarks, designed to address the limitations of earlier benchmarks by incorporating modern data warehouse patterns, complex analytical queries, and realistic data distributions that challenge all aspects of a database system's query processing capabilities.

## Key Features

- **99 analytical queries** with varying complexity and selectivity
- **24 data tables** (plus `dbgen_version` metadata table) representing a systematic retail data warehouse
- **Advanced-level SQL features** including window functions, multi-CTE queries, ROLLUP/CUBE
- **Complex query patterns** with nested subqueries and multiple aggregation levels
- **Realistic data distributions** based on real-world retail scenarios
- **Stream processing support** for throughput and concurrency testing
- **Parameter substitution** with sophisticated business logic
- **Multiple scale factors** (SF 1 = ~1GB, SF 100 = ~100GB, etc.)
- **SQL dialect support** via translation capabilities

## Schema Description

The TPC-DS schema models a systematic retail data warehouse with three main areas: **Store Sales**, **Catalog Sales**, and **Web Sales**. The schema includes fact tables, dimension tables, and return tables that capture the complexity of modern retail operations.

### Fact Tables (Core Business Events)

| Table | Purpose | Approximate Rows (SF 1) |
|-------|---------|-------------------------|
| **STORE_SALES** | In-store retail transactions | 2,880,404 |
| **CATALOG_SALES** | Mail-order catalog sales | 1,441,548 |
| **WEB_SALES** | Online web sales | 719,384 |
| **STORE_RETURNS** | In-store returns | 287,514 |
| **CATALOG_RETURNS** | Catalog returns | 144,067 |
| **WEB_RETURNS** | Web returns | 71,763 |
| **INVENTORY** | Daily inventory snapshots | 11,745,000 |

### Dimension Tables (Reference Data)

| Table | Purpose | Approximate Rows (SF 1) |
|-------|---------|-------------------------|
| **DATE_DIM** | Calendar and date attributes | 73,049 |
| **TIME_DIM** | Time of day breakdown | 86,400 |
| **ITEM** | Product catalog | 18,000 |
| **CUSTOMER** | Customer information | 100,000 |
| **CUSTOMER_ADDRESS** | Customer addresses | 50,000 |
| **CUSTOMER_DEMOGRAPHICS** | Customer demographic profiles | 1,920,800 |
| **HOUSEHOLD_DEMOGRAPHICS** | Household characteristics | 7,200 |
| **STORE** | Store location details | 12 |
| **CATALOG_PAGE** | Catalog page information | 11,718 |
| **WEB_SITE** | Website information | 30 |
| **WEB_PAGE** | Web page details | 60 |
| **WAREHOUSE** | Distribution centers | 5 |
| **PROMOTION** | Marketing promotions | 300 |
| **INCOME_BAND** | Income brackets | 20 |
| **REASON** | Return/refund reasons | 35 |
| **CALL_CENTER** | Customer service centers | 6 |
| **SHIP_MODE** | Shipping methods | 20 |

### Schema Relationships

```{mermaid}
erDiagram
    DATE_DIM ||--o{ STORE_SALES : sold_on
    DATE_DIM ||--o{ CATALOG_SALES : sold_on
    DATE_DIM ||--o{ WEB_SALES : sold_on
    TIME_DIM ||--o{ STORE_SALES : sold_at
    ITEM ||--o{ STORE_SALES : item_sold
    ITEM ||--o{ CATALOG_SALES : item_sold
    ITEM ||--o{ WEB_SALES : item_sold
    CUSTOMER ||--o{ STORE_SALES : purchased_by
    CUSTOMER ||--o{ CATALOG_SALES : purchased_by
    CUSTOMER ||--o{ WEB_SALES : purchased_by
    STORE ||--o{ STORE_SALES : sold_at
    CATALOG_PAGE ||--o{ CATALOG_SALES : from_page
    WEB_SITE ||--o{ WEB_SALES : from_site
    WEB_PAGE ||--o{ WEB_SALES : from_page
    PROMOTION ||--o{ STORE_SALES : promoted_by
    STORE_SALES ||--o{ STORE_RETURNS : returned_from
    CATALOG_SALES ||--o{ CATALOG_RETURNS : returned_from
    WEB_SALES ||--o{ WEB_RETURNS : returned_from
```

## Query Characteristics

The 99 TPC-DS queries are organized into different complexity categories and test various aspects of analytical query processing.

```{tip}
Each query has a [query template](queries/tpcds/index.md) with a representative
SQL and DataFrame rendering, plus how to extract the exact statement for your
platform and scale.
```

### Query Categories by Complexity

**Reporting Queries (Simple Aggregation):**
- [Q1](queries/tpcds/q1.md), [Q6](queries/tpcds/q6.md), [Q11](queries/tpcds/q11.md), [Q13](queries/tpcds/q13.md): Basic aggregation with simple grouping
- Focus on fundamental aggregation performance

**Interactive Queries (Medium Complexity):**
- [Q3](queries/tpcds/q3.md), [Q7](queries/tpcds/q7.md), [Q19](queries/tpcds/q19.md), [Q27](queries/tpcds/q27.md), [Q42](queries/tpcds/q42.md): Multi-table joins with moderate complexity
- Window functions and ranking operations
- Typical business intelligence patterns

**Deep Analytics (High Complexity):**
- [Q14a/14b](queries/tpcds/q14.md), [Q23a/23b](queries/tpcds/q23.md), [Q24a/24b](queries/tpcds/q24.md): Advanced-level analytical patterns
- Complex joins and mathematical calculations
- Multi-level aggregation and correlation analysis

**Data Mining Queries (Very High Complexity):**
- [Q64](queries/tpcds/q64.md), [Q67](queries/tpcds/q67.md), [Q70](queries/tpcds/q70.md), [Q86](queries/tpcds/q86.md): Statistical analysis and data mining patterns
- Customer segmentation and behavior analysis
- Advanced-level window functions and analytical operations

### Key Query Features

| Feature | Example Queries | Description |
|---------|----------------|-------------|
| **Window Functions** | [Q17](queries/tpcds/q17.md), [Q25](queries/tpcds/q25.md), [Q47](queries/tpcds/q47.md), [Q51](queries/tpcds/q51.md) | ROW_NUMBER(), RANK(), LAG/LEAD |
| **Multi-CTE Analysis** | [Q14a](queries/tpcds/q14.md), [Q23a](queries/tpcds/q23.md) | Chained CTEs for cross-channel analysis |
| **ROLLUP/CUBE** | [Q5](queries/tpcds/q5.md), [Q18](queries/tpcds/q18.md), [Q77](queries/tpcds/q77.md) | OLAP aggregation operations |
| **Complex Joins** | [Q64](queries/tpcds/q64.md), [Q78](queries/tpcds/q78.md), [Q95](queries/tpcds/q95.md) | 6+ table joins with multiple predicates |
| **Correlation Analysis** | [Q35](queries/tpcds/q35.md), [Q38](queries/tpcds/q38.md), [Q87](queries/tpcds/q87.md) | Cross-channel correlation patterns |
| **Time Series** | [Q12](queries/tpcds/q12.md), [Q20](queries/tpcds/q20.md), [Q21](queries/tpcds/q21.md) | Year-over-year and temporal analysis |
| **Ranking/Top-N** | [Q37](queries/tpcds/q37.md), [Q40](queries/tpcds/q40.md), [Q52](queries/tpcds/q52.md) | Customer and product ranking |
| **Statistical Functions** | [Q9](queries/tpcds/q9.md), [Q46](queries/tpcds/q46.md), [Q98](queries/tpcds/q98.md) | STDDEV, VARIANCE, CORR |

### Query Variants

Some queries have variants (e.g., [Q14a/Q14b](queries/tpcds/q14.md)) that test slightly different patterns:
- **[Query 14a/14b](queries/tpcds/q14.md)**: Different approaches to cross-channel analysis
- **[Query 23a/23b](queries/tpcds/q23.md)**: Alternative cross-channel query patterns
- **[Query 24a/24b](queries/tpcds/q24.md)**: Different aggregation strategies

## Usage Examples

### Basic Query Generation

```python
from benchbox import TPCDS

tpcds = TPCDS(scale_factor=1.0, output_dir="tpcds_data")

data_files = tpcds.generate_data()

queries = tpcds.get_queries()
print(f"Generated {len(queries)} queries")

query_1 = tpcds.get_query(1, seed=42)
print(query_1)

query_14a = tpcds.get_query("14a", seed=42)
query_14b = tpcds.get_query("14b", seed=42)
```

### Data Generation with C Tools

```python
tpcds = TPCDS(scale_factor=1.0, output_dir="tpcds_sf1", verbose=True)

data_files = tpcds.generate_data()

for table_name in tpcds.get_available_tables():
    table_path = tpcds.output_dir / f"{table_name.lower()}.dat"
    print(f"{table_name}: {table_path} ({table_path.stat().st_size} bytes)")
```

TPC-DS data generation requires the official C tools (`dsdgen` and `dsqgen`). The loop checks the generated files.

### Stream Generation and Testing

```python
tpcds = TPCDS(scale_factor=1.0, output_dir="tpcds_data")

streams = tpcds.generate_streams(
    num_streams=4,
    rng_seed=42,
    streams_output_dir="streams"
)

for i, stream_info in enumerate(tpcds.get_all_streams_info()):
    print(f"Stream {i}:")
    print(f"  Queries: {stream_info['query_count']}")
    print(f"  File: {stream_info['output_file']}")
```

Generate multiple concurrent streams for throughput testing. Each stream contains a random permutation of the queries.

### DuckDB Integration Example

```python
import duckdb
from benchbox import TPCDS

tpcds = TPCDS(scale_factor=0.1, output_dir="tpcds_small")
data_files = tpcds.generate_data()

conn = duckdb.connect("tpcds.duckdb")
schema_sql = tpcds.get_create_tables_sql()
conn.execute(schema_sql)

table_mappings = {
    'call_center': 'call_center.dat',
    'catalog_page': 'catalog_page.dat',
    'catalog_returns': 'catalog_returns.dat',
    'catalog_sales': 'catalog_sales.dat',
    'customer': 'customer.dat',
    'customer_address': 'customer_address.dat',
    'customer_demographics': 'customer_demographics.dat',
    'date_dim': 'date_dim.dat',
    'household_demographics': 'household_demographics.dat',
    'income_band': 'income_band.dat',
    'inventory': 'inventory.dat',
    'item': 'item.dat',
    'promotion': 'promotion.dat',
    'reason': 'reason.dat',
    'ship_mode': 'ship_mode.dat',
    'store': 'store.dat',
    'store_returns': 'store_returns.dat',
    'store_sales': 'store_sales.dat',
    'time_dim': 'time_dim.dat',
    'warehouse': 'warehouse.dat',
    'web_page': 'web_page.dat',
    'web_returns': 'web_returns.dat',
    'web_sales': 'web_sales.dat',
    'web_site': 'web_site.dat'
}

for table_name, file_name in table_mappings.items():
    file_path = tpcds.output_dir / file_name
    if file_path.exists():
        conn.execute(f"""
            INSERT INTO {table_name}
            SELECT * FROM read_csv('{file_path}',
                                  delim='|',
                                  header=false,
                                  nullstr='',
                                  ignore_errors=true)
        """)
        print(f"Loaded {table_name}")

sample_queries = [1, 3, 7, 19, 27, 42]
for query_id in sample_queries:
    try:
        query_sql = tpcds.get_query(query_id, dialect="duckdb")
        result = conn.execute(query_sql).fetchall()
        print(f"Q{query_id}: {len(result)} rows")
    except Exception as e:
        print(f"Q{query_id}: Error - {str(e)[:100]}...")
```

### Advanced-level Parameter Management

```python
query_reproducible = tpcds.get_query(42, seed=12345)

query_large = tpcds.get_query(42,
                              seed=12345,
                              scale_factor=100.0)

query_stream = tpcds.get_query(42,
                               seed=12345,
                               stream_id=0)
```

Use a specific seed for reproducible results. Passing `scale_factor` makes parameter generation scale-aware, and passing `stream_id` makes it stream-based.

### Scale factor and query parameters

dsqgen derives some query parameters from the scale factor, so the SQL for a
few queries depends on it. Standard runs now generate every query with the scale
factor of the data you loaded. Earlier versions used the scale factor 1 values
at every scale, which made a few queries test different values than the data
called for.

This can change the SQL of these queries when the scale factor is not 1:

| Scale factor | Queries whose SQL can differ |
|--------------|------------------------------|
| 0.01 and 0.1 | [Q9](queries/tpcds/q9.md), [Q44](queries/tpcds/q44.md), [Q46](queries/tpcds/q46.md), [Q68](queries/tpcds/q68.md) |
| 10 | [Q9](queries/tpcds/q9.md), [Q44](queries/tpcds/q44.md), [Q46](queries/tpcds/q46.md), [Q68](queries/tpcds/q68.md), plus [Q1](queries/tpcds/q1.md), [Q16](queries/tpcds/q16.md), [Q27](queries/tpcds/q27.md), [Q34](queries/tpcds/q34.md), [Q36](queries/tpcds/q36.md), [Q73](queries/tpcds/q73.md) |
| 100 and above | [Q9](queries/tpcds/q9.md), [Q44](queries/tpcds/q44.md), [Q46](queries/tpcds/q46.md), [Q68](queries/tpcds/q68.md), plus [Q1](queries/tpcds/q1.md), [Q16](queries/tpcds/q16.md), [Q27](queries/tpcds/q27.md), [Q33](queries/tpcds/q33.md), [Q34](queries/tpcds/q34.md), [Q36](queries/tpcds/q36.md), [Q43](queries/tpcds/q43.md), [Q73](queries/tpcds/q73.md) |

For a given seed some of these queries can come out the same at both scales,
because the random values they draw can coincide.

Runs at scale factor 1 are unchanged. Power and throughput runs already
generated their queries for the data's scale factor and are unchanged. Results
recorded before this change at other scale factors used the old SQL for the
queries above, so don't compare those queries one for one with newer results.

## Query Characteristics

### Query Complexity Categories

**Simple Queries (low complexity):**
- [Q1](queries/tpcds/q1.md), [Q6](queries/tpcds/q6.md), [Q11](queries/tpcds/q11.md), [Q13](queries/tpcds/q13.md): Simple aggregation queries
- [Q16](queries/tpcds/q16.md), [Q21](queries/tpcds/q21.md), [Q32](queries/tpcds/q32.md): Basic joins with simple predicates

**Moderate Queries (medium complexity):**
- [Q3](queries/tpcds/q3.md), [Q7](queries/tpcds/q7.md), [Q19](queries/tpcds/q19.md), [Q27](queries/tpcds/q27.md): Multi-table joins with moderate complexity
- [Q25](queries/tpcds/q25.md), [Q29](queries/tpcds/q29.md), [Q43](queries/tpcds/q43.md): Window functions and ranking
- [Q52](queries/tpcds/q52.md), [Q55](queries/tpcds/q55.md), [Q59](queries/tpcds/q59.md): Customer analysis patterns

**Complex Queries (high complexity):**
- [Q14a/b](queries/tpcds/q14.md), [Q23a/b](queries/tpcds/q23.md), [Q24a/b](queries/tpcds/q24.md): Advanced-level analytical patterns
- [Q64](queries/tpcds/q64.md), [Q67](queries/tpcds/q67.md), [Q78](queries/tpcds/q78.md): Complex joins and correlations
- [Q95](queries/tpcds/q95.md), [Q98](queries/tpcds/q98.md): Statistical and data mining operations

**Very Complex Queries (highest complexity):**
- [Q70](queries/tpcds/q70.md), [Q86](queries/tpcds/q86.md): Extremely complex analytical patterns
- Queries with large intermediate result sets
- Multi-level aggregation with complex predicates

**Note:** Actual execution times vary significantly based on platform, hardware, configuration, and data characteristics. Run benchmarks to measure performance for your specific environment.

### Resource Utilization Patterns

| Query Type | CPU Usage | Memory Usage | I/O Pattern | Optimization Challenges |
|------------|-----------|--------------|-------------|------------------------|
| Simple Aggregation | Medium | Low | Sequential | Index selection |
| Multi-Join | High | Medium | Random | Join order optimization |
| Window Functions | High | High | Mixed | Partition management |
| Data Mining | Very High | Very High | Mixed | Statistical computation |

## TPC-DS Derived Metrics

> Derived from TPC-DS. These results do not comply with the TPC-DS Specification and must not be compared with official TPC-DS results.

BenchBox exports Power@Size and Throughput@Size when the `power` and `throughput`
phases run successfully. Values are surfaced in the JSON results.

| Metric | Meaning | Source |
|--------|---------|--------|
| **Power@Size** | Single-stream power test throughput at the given scale factor | `power_at_size` - computed from the 99-query power stream |
| **Throughput@Size** | Multi-stream throughput at the given scale factor, scored with `Q = 99 × S` queries | `throughput_at_size` - computed across parallel streams |

BenchBox does not export QphDS@Size. The specification formula is
`SF × Q / (T_PT × T_TT × T_DM × T_LD)^(1/4)` with `Q = 99 × S`, and it needs
the data maintenance and load times. The geometric mean of Power@Size and
Throughput@Size that BenchBox previously exported under this name is not
that metric. Implementing the specification formula is tracked as follow-up
work.

Implementation: `benchbox/core/tpcds/power_test.py` / `TPCMetricsCalculator` (computation).

### Throughput Permutation

The Throughput Test's default stream generation calls the official
`dsqgen -STREAMS N` batch mode (`benchbox/core/tpcds/streams.py::generate_dsqgen_streams`,
`-INPUT templates.lst`) once per run. This single invocation produces the
**official per-stream query ORDERING and the official per-stream substitution
PARAMETERS** for every stream, exactly as the reference TPC-DS kit would --
superseding a previous home-grown Python permutation and RNG-based parameter
jitter that did not reproduce dsqgen's ordering or parameters. Streams are
reproducible for a fixed `(num_streams, base_seed)` pair: ordering is fully
deterministic, and 98 of the 99 query templates' substitution parameters are
byte-stable. The one exception is query46, whose 5-city `IN` list comes from a
dsqgen-internal `ulist(random(1, rowcount("active_cities","store"), uniform), 5)`
substitution that the bundled reference binary does not reproduce even for a
fixed `-RNGSEED` (a property of the binary, not of BenchBox); its ordering slot
is unaffected.

`PermutationMode` (`sequential` / `random` / `tpcds`) and
`create_standard_streams`/`TPCDSStreamManager` remain in the codebase and are
still used by the single-query `get_query()` path (power test, dry-run,
dataframe query resolution) and as an inline fallback for direct/unit callers
that bypass the throughput test's `run()` pre-generation step
(`enable_preflight=False`). They are **not** the throughput default, and their
ordering does not match the official dsqgen permutation.

### GIL / Result-Materialization Note

For embedded engines (DuckDB, SQLite, DataFusion, ...) that release the GIL
during native query execution but not while converting result rows into
Python objects, fetching a query's full result set (`cursor.fetchall()`)
re-acquires the GIL for the duration of that conversion. Every concurrent
stream that materializes a result at the same time serializes on this step,
so larger result sets and higher stream counts inflate
`execution_time_seconds` (and therefore depress `Throughput@Size`)
independently of the engine's actual query-execution concurrency - an
artifact of Python result handling, not of the platform under test.
`benchbox/core/tpcds/throughput_test.py` avoids forcing this materialization
to compute `result_count`: it reads the platform adapter's already-computed
row count directly
(`benchbox.platforms.base.connection_wrappers.count_query_rows`) instead of
calling `fetchall()` a second time and discarding the rows. If you build a
custom harness that calls `fetchall()` per stream to inspect or log results,
be aware that under this GIL constraint doing so measures Python object
materialization throughput as much as it measures the database engine.

### Maintenance Operations

When the `maintenance` phase is included, BenchBox runs the TPC-DS-prescribed
INSERT/UPDATE/DELETE operations between the power and throughput streams
(`benchbox/core/tpcds/maintenance_operations.py`):

- **Inserts**: `store_sales`, `catalog_sales`, `web_sales`, and their return tables
- **Updates**: `customer`, `item`, `inventory`
- **Deletes**: expired sales and returns rows
- **Bulk**: inventory-wide updates

The maintenance runner (`benchbox/core/tpcds/maintenance_test.py`) reports a
pass/fail outcome that is reflected in the executive summary alongside the
composite metrics.

## Configuration Options

### Scale Factor Guidelines

| Scale Factor | Data Size | Use Case |
|-------------|-----------|----------|
| 0.01 | ~10 MB | Unit testing, development |
| 0.1 | ~100 MB | Integration testing |
| 1 | ~1 GB | Standard benchmark |
| 10 | ~10 GB | Performance testing |
| 100 | ~100 GB | Enterprise simulation |
| 1000+ | ~1+ TB | Large-scale testing |

**Subscale behavior:** Scale factors below 1 are supported for development and
testing. Runs at SF < 1 are allowed by default, but remain unofficial and are
not compliance-valid. They record Power@Size marked unofficial, and the
Results Explorer ranks them in their own cohort labelled Subscale · Unofficial.

**Note:** Query execution times depend on platform, hardware, query complexity, and configuration. Run benchmarks to establish baselines for your environment.

### Advanced-level Configuration

```python
tpcds = TPCDS(
    scale_factor=1.0,
    output_dir="tpcds_data",
    verbose=True,
    parallel=8,
    use_c_tools=True
)

c_tools = tpcds.c_tools
query_manager = tpcds.queries
data_generator = tpcds.generator
```

`verbose=True` enables detailed logging, `parallel=8` generates data in parallel, and `use_c_tools=True` uses the official TPC-DS C tools. The last three attributes give direct access to the C tools, query management and data generation components.

## Integration Examples

### Distributed Database Testing

This example generates data at a large scale factor for a distributed system and loads it with partition-aware tables for Spark. The `CASE` expression is abbreviated and stands for the full list of partitions.

```python
tpcds_large = TPCDS(scale_factor=100, output_dir="/shared/tpcds_sf100")
data_files = tpcds_large.generate_data()

for table_name in tpcds_large.get_available_tables():
    table_path = f"/shared/tpcds_sf100/{table_name.lower()}.dat"

    spark.sql(f"""
        CREATE TABLE {table_name}
        USING DELTA
        PARTITIONED BY (partition_col)
        LOCATION '/delta/{table_name}'
        AS SELECT *,
               CASE
                   WHEN {table_name}_sk % 100 < 10 THEN 'p0'
                   WHEN {table_name}_sk % 100 < 20 THEN 'p1'
                   ELSE 'p9'
               END as partition_col
        FROM read_csv('{table_path}', delimiter='|', header=false)
    """)
```

### Performance Benchmarking Framework

```python
import time
from statistics import mean, median
from typing import Dict, List

class TPCDSBenchmark:
    def __init__(self, tpcds: TPCDS, connection):
        self.tpcds = tpcds
        self.connection = connection

    def benchmark_query_set(self, query_ids: List[int],
                           iterations: int = 3) -> Dict:
        results = {}

        for query_id in query_ids:
            print(f"Benchmarking Q{query_id}...")
            query_results = []

            for iteration in range(iterations):
                query_sql = self.tpcds.get_query(query_id,
                                                seed=42 + iteration,
                                                dialect="duckdb")

                start_time = time.time()
                try:
                    result = self.connection.execute(query_sql).fetchall()
                    execution_time = time.time() - start_time

                    query_results.append({
                        'iteration': iteration,
                        'time': execution_time,
                        'rows': len(result),
                        'status': 'success'
                    })
                except Exception as e:
                    execution_time = time.time() - start_time
                    query_results.append({
                        'iteration': iteration,
                        'time': execution_time,
                        'rows': 0,
                        'status': 'error',
                        'error': str(e)
                    })

            successful_runs = [r for r in query_results if r['status'] == 'success']
            if successful_runs:
                times = [r['time'] for r in successful_runs]
                results[query_id] = {
                    'mean_time': mean(times),
                    'median_time': median(times),
                    'min_time': min(times),
                    'max_time': max(times),
                    'success_rate': len(successful_runs) / len(query_results),
                    'avg_rows': mean([r['rows'] for r in successful_runs]),
                    'runs': query_results
                }
            else:
                results[query_id] = {
                    'mean_time': 0,
                    'median_time': 0,
                    'success_rate': 0,
                    'runs': query_results
                }

        return results

benchmark = TPCDSBenchmark(tpcds, conn)

fast_queries = [1, 6, 11, 13, 16, 21]
medium_queries = [3, 7, 19, 27, 42, 52]
complex_queries = [14, 23, 24, 64, 78]

fast_results = benchmark.benchmark_query_set(fast_queries)
medium_results = benchmark.benchmark_query_set(medium_queries)
complex_results = benchmark.benchmark_query_set(complex_queries)
```

## Best Practices

### Data Generation
1. **Use official C tools** - TPC-DS requires dsdgen/dsqgen for compliance
2. **Parallel generation** - Use multiple processes for large scale factors
3. **Validate completeness** - Ensure all 24 tables are generated
4. **Check file formats** - Verify pipe-delimited format and encoding

### Query Execution
1. **Start with simple queries** - Test [Q1](queries/tpcds/q1.md), [Q6](queries/tpcds/q6.md), [Q11](queries/tpcds/q11.md) first
2. **Use appropriate timeouts** - Complex queries can run for hours
3. **Monitor resource usage** - Watch memory consumption for large queries
4. **Test query variants** - Include both [Q14a](queries/tpcds/q14.md) and [Q14b](queries/tpcds/q14.md) in testing

### Performance Testing
1. **Warm-up runs** - Execute queries multiple times for stable timings
2. **Resource monitoring** - Track CPU, memory, and I/O during execution
3. **Incremental complexity** - Start with simple queries, progress to complex
4. **Statistical analysis** - Use geometric mean for overall performance scores

## Common Issues and Solutions

### Data Generation Issues

**Issue: dsdgen/dsqgen not found**
```bash
cd _sources/tpc-ds/tools
make
```

Ensure the TPC-DS C tools are compiled and available.

**Issue: Out of disk space during generation**
```python
tpcds = TPCDS(scale_factor=0.1, output_dir="/external/storage")
```

Use a smaller scale factor or external storage.

### Query Execution Issues

**Issue: Query timeout on complex queries**
```python
conn.execute("SET query_timeout='1800s'")
tpcds = TPCDS(scale_factor=0.1)
```

Increase the timeout (1800 seconds is 30 minutes) and use a smaller scale factor (a smaller dataset) for testing.

**Issue: Out of memory on analytical queries**
```python
conn.execute("SET memory_limit='8GB'")
conn.execute("SET max_memory='8GB'")
```

Configure memory limits and optimize queries.

**Issue: Parameter substitution errors**
```python
query = tpcds.get_query(42, seed=12345)
```

Use explicit seeds and validate query IDs. The same seed gives reproducible parameters.

## Streaming Data Generation

BenchBox includes patched TPC-DS binaries that support streaming data generation via stdout. This enables efficient workflows that reduce disk I/O and memory usage.

### Using the FILTER Flag

The `-FILTER Y` flag outputs generated data to stdout instead of creating files:

```bash
dsdgen -TABLE ship_mode -SCALE 1 -FILTER Y | zstd > ship_mode.dat.zst

dsdgen -TABLE date_dim -SCALE 1 -FILTER Y -RNGSEED 12345 | gzip > date_dim.dat.gz

dsdgen -TABLE customer -SCALE 1 -FILTER Y | \
  duckdb -c "COPY customer FROM '/dev/stdin' (DELIMITER '|')"
```

The first command writes one table to stdout, so it can be piped to compression. The second generates with a fixed seed for reproducibility. The third loads directly into a database, here DuckDB.

### Benefits

- **Reduced disk I/O**: Data streams directly to compression or database
- **Lower memory usage**: No intermediate files needed
- **Faster pipelines**: Eliminates write-then-read overhead
- **Parallel compression**: Each table can be compressed independently

### Streaming in Python

```python
import subprocess
import gzip
from pathlib import Path

def generate_table_compressed(table_name: str, scale: int, output_path: Path):
    dsdgen = Path("_binaries/tpc-ds/darwin-arm64/dsdgen")
    tools_dir = Path("_sources/tpc-ds/tools")

    with gzip.open(output_path, "wt") as f:
        proc = subprocess.Popen(
            [str(dsdgen), "-TABLE", table_name, "-SCALE", str(scale),
             "-FILTER", "Y", "-RNGSEED", "1", "-TERMINATE", "N"],
            cwd=tools_dir,
            stdout=subprocess.PIPE,
            text=True
        )
        for line in proc.stdout:
            f.write(line)
        proc.wait()

generate_table_compressed("ship_mode", 1, Path("ship_mode.dat.gz"))
```

### Source Patches

The streaming capability requires patches to the TPC-DS source that fix two bugs
in the official TPC-DS tools. See `_sources/tpc-ds/PATCHES.md` for details.

These patches were originally developed by [Greg Rahn](https://github.com/gregrahn)
in the [tpcds-kit](https://github.com/gregrahn/tpcds-kit) repository.

## Related Documentation

- [TPC-H Benchmark](tpc-h.md) - Simpler decision support benchmark
- [Star Schema Benchmark](ssb.md) - OLAP-focused testing
- [ClickBench](clickbench.md) - Analytics benchmark
- [Architecture Guide](../design/architecture.md) - BenchBox design principles
- [Advanced-level Usage](../advanced/index.md) - Complex benchmark scenarios

## External Resources

- [TPC-DS Specification](http://www.tpc.org/tpcds/) - Official TPC-DS documentation
- [TPC-DS Results](http://www.tpc.org/tpcds/results/) - Published benchmark results
- [TPC-DS Tools](http://www.tpc.org/tpc_documents_current_versions/current_specifications.asp) - Official C tools download
- [Query Analysis Papers](http://www.tpc.org/information/white_papers.asp) - Academic research using TPC-DS
