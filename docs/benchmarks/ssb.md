<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Star Schema Benchmark (SSB)

```{tags} intermediate, concept, ssb
```

> **CLI name:** `ssb` - use `benchbox run --benchmark ssb`

## Overview

The Star Schema Benchmark (SSB) is a simplified variant of TPC-H specifically designed for testing OLAP (Online Analytical Processing) systems and data warehouses. Created by O'Neil et al., SSB transforms the normalized TPC-H schema into a denormalized star schema that better represents typical data warehouse designs and enables more focused testing of analytical query performance.

SSB is particularly valuable for testing columnar databases, in-memory analytics systems, and OLAP engines because it eliminates complex join patterns in favor of simple star schema joins that are more representative of real-world data warehouse queries.

## Key Features

- **Star schema design** - Single fact table with dimension tables
- **13 analytical queries** organized into 4 logical flights
- **Simplified query patterns** - Focus on aggregation and filtering performance
- **OLAP-oriented** - Tests typical data warehouse query patterns
- **Parameterized queries** - Configurable selectivity and date ranges
- **Performance-focused** - Designed to stress aggregation and scan performance
- **Multiple scale factors** - Scalable from small test datasets to large benchmarks

## Schema Description

The SSB schema consists of a single fact table (**LINEORDER**) surrounded by four dimension tables in a classic star schema pattern. This design eliminates the complex join patterns found in TPC-H's normalized schema.

### Fact Table

| Table | Purpose | Approximate Rows (SF 1) |
|-------|---------|-------------------------|
| **LINEORDER** | Sales transactions (fact table) | ~6,000,000 |

### Dimension Tables

| Table | Purpose | Approximate Rows (SF 1) |
|-------|---------|-------------------------|
| **DATE** | Calendar information | ~2,556 (7 years) |
| **CUSTOMER** | Customer master data | ~30,000 |
| **SUPPLIER** | Supplier information | ~2,000 |
| **PART** | Product catalog | ~200,000 |

BenchBox keeps the DATE dimension at 2,556 rows. The generated CUSTOMER, SUPPLIER, PART and LINEORDER row counts are `int(30_000 * scale_factor)`, `int(2_000 * scale_factor)`, `int(200_000 * scale_factor)` and `int(6_000_000 * scale_factor)`, respectively.

The DataFrame query parameter module uses static defaults. `get_parameters(query_id)` returns an `SSBParameters` record whose `params` field is a copy of that query's defaults, or an empty dictionary for an unknown query ID; it does not derive substitutions from a seed.

### Schema Transformation from TPC-H

SSB transforms TPC-H's normalized schema by:

1. **Combining ORDERS and LINEITEM** → **LINEORDER** fact table
2. **Denormalizing CUSTOMER** → Includes region and nation information
3. **Denormalizing SUPPLIER** → Includes region and nation information
4. **Flattening PART hierarchy** → Simplified part categorization
5. **Removing PARTSUPP** → Part-supplier relationships embedded in fact table
6. **Simplified DATE** → Calendar dimension without complex hierarchies

### Schema Relationships

```{mermaid}
erDiagram
    DATE ||--o{ LINEORDER : lo_orderdate
    CUSTOMER ||--o{ LINEORDER : lo_custkey
    SUPPLIER ||--o{ LINEORDER : lo_suppkey
    PART ||--o{ LINEORDER : lo_partkey

    DATE {
        int d_datekey PK
        string d_date
        string d_dayofweek
        string d_month
        int d_year
        int d_yearmonthnum
        string d_yearmonth
        int d_weeknuminyear
        string d_sellingseason
        int d_holidayfl
        int d_weekdayfl
    }

    CUSTOMER {
        int c_custkey PK
        string c_name
        string c_address
        string c_city
        string c_nation
        string c_region
        string c_phone
        string c_mktsegment
    }

    SUPPLIER {
        int s_suppkey PK
        string s_name
        string s_address
        string s_city
        string s_nation
        string s_region
        string s_phone
    }

    PART {
        int p_partkey PK
        string p_name
        string p_mfgr
        string p_category
        string p_brand1
        string p_color
        string p_type
        int p_size
        string p_container
    }

    LINEORDER {
        int lo_orderkey
        int lo_linenumber
        int lo_custkey FK
        int lo_partkey FK
        int lo_suppkey FK
        int lo_orderdate FK
        string lo_orderpriority
        int lo_shippriority
        int lo_quantity
        int lo_extendedprice
        int lo_ordtotalprice
        int lo_discount
        int lo_revenue
        int lo_supplycost
        int lo_tax
        int lo_commitdate
        string lo_shipmode
    }
```

## Query Characteristics

The 13 SSB queries are organized into 4 flights that test different aspects of analytical query performance.

```{tip}
Each query has a [query template](queries/ssb/index.md) with a representative
SQL and DataFrame rendering, plus how to extract the exact statement for your
platform and scale.
```

### Flight 1: Simple Aggregation Queries (Q1.1 - Q1.3)

**Purpose**: Test basic aggregation and filtering performance on the fact table.

| Query | Focus | Key Features |
|-------|-------|-------------|
| **[Q1.1](queries/ssb/q1-1.md)** | Year-based aggregation | Simple date filter, discount range, quantity filter |
| **[Q1.2](queries/ssb/q1-2.md)** | Month-based aggregation | Month granularity, quantity range filter |
| **[Q1.3](queries/ssb/q1-3.md)** | Week-based aggregation | Week granularity, refined quantity range |

**Example Query ([Q1.1](queries/ssb/q1-1.md))**:
```sql
SELECT sum(lo_extendedprice*lo_discount) as revenue
FROM lineorder, date
WHERE lo_orderdate = d_datekey
  AND d_year = 1993
  AND lo_discount between 1 and 3
  AND lo_quantity < 25;
```

### Flight 2: Customer-Supplier Analysis (Q2.1 - Q2.3)

**Purpose**: Test queries with dimension table joins and drill-down analysis.

| Query | Focus | Key Features |
|-------|-------|-------------|
| **[Q2.1](queries/ssb/q2-1.md)** | Brand category analysis | Part category filter, supplier region |
| **[Q2.2](queries/ssb/q2-2.md)** | Brand range analysis | Brand range filter, supplier region |
| **[Q2.3](queries/ssb/q2-3.md)** | Specific brand analysis | Single brand focus, supplier region |

**Example Query ([Q2.1](queries/ssb/q2-1.md))**:
```sql
SELECT sum(lo_revenue), d_year, p_brand1
FROM lineorder, date, part, supplier
WHERE lo_orderdate = d_datekey
  AND lo_partkey = p_partkey
  AND lo_suppkey = s_suppkey
  AND p_category = 'MFGR#12'
  AND s_region = 'AMERICA'
GROUP BY d_year, p_brand1
ORDER BY d_year, p_brand1;
```

### Flight 3: Customer Behavior Analysis (Q3.1 - Q3.4)

**Purpose**: Test complex multi-dimension analysis with customer geography.

| Query | Focus | Key Features |
|-------|-------|-------------|
| **[Q3.1](queries/ssb/q3-1.md)** | Customer nation analysis | Customer and supplier nations |
| **[Q3.2](queries/ssb/q3-2.md)** | City-level analysis | Customer city, supplier city |
| **[Q3.3](queries/ssb/q3-3.md)** | Specific city analysis | Single customer city focus |
| **[Q3.4](queries/ssb/q3-4.md)** | Regional analysis | Customer and supplier regions |

**Example Query ([Q3.1](queries/ssb/q3-1.md))**:
```sql
SELECT c_nation, s_nation, d_year, sum(lo_revenue) as revenue
FROM customer, lineorder, supplier, date
WHERE lo_custkey = c_custkey
  AND lo_suppkey = s_suppkey
  AND lo_orderdate = d_datekey
  AND c_region = 'ASIA'
  AND s_region = 'ASIA'
  AND d_year >= 1992 AND d_year <= 1997
GROUP BY c_nation, s_nation, d_year
ORDER BY d_year asc, revenue desc;
```

### Flight 4: Profit Analysis (Q4.1 - Q4.3)

**Purpose**: Test complex aggregation with profit calculations and temporal analysis.

| Query | Focus | Key Features |
|-------|-------|-------------|
| **[Q4.1](queries/ssb/q4-1.md)** | Regional profit analysis | Customer and supplier regions, profit calculation |
| **[Q4.2](queries/ssb/q4-2.md)** | Nation-specific profit | Single customer nation, profit trends |
| **[Q4.3](queries/ssb/q4-3.md)** | City-specific profit | Customer city, part category, profit analysis |

**Example Query ([Q4.1](queries/ssb/q4-1.md))**:
```sql
SELECT d_year, c_nation, sum(lo_revenue - lo_supplycost) as profit
FROM date, customer, supplier, part, lineorder
WHERE lo_custkey = c_custkey
  AND lo_suppkey = s_suppkey
  AND lo_partkey = p_partkey
  AND lo_orderdate = d_datekey
  AND c_region = 'AMERICA'
  AND s_region = 'AMERICA'
  AND (p_mfgr = 'MFGR#1' OR p_mfgr = 'MFGR#2')
GROUP BY d_year, c_nation
ORDER BY d_year, c_nation;
```

## Usage Examples

### Basic Query Generation

```python
from benchbox import SSB

ssb = SSB(scale_factor=1.0, output_dir="ssb_data")

data_files = ssb.generate_data()

queries = ssb.get_queries()
print(f"Generated {len(queries)} SSB queries")

query_1_1 = ssb.get_query("Q1.1", params={
    'year': 1993,
    'discount_min': 1,
    'discount_max': 3,
    'quantity': 25
})
print(query_1_1)
```

### Data Generation and Loading

```python
ssb_small = SSB(scale_factor=0.1, output_dir="ssb_small")
ssb_small.generate_data()

print(f"Available tables: {sorted(ssb_small.tables)}")

schema = ssb_small.get_schema()
for table in schema:
    print(f"Table {table['name']}: {len(table['columns'])} columns")
```

### DuckDB Integration Example

```python
import duckdb
from benchbox import SSB

ssb = SSB(scale_factor=0.01, output_dir="ssb_tiny")
data_files = ssb.generate_data()

conn = duckdb.connect("ssb.duckdb")
schema_sql = ssb.get_create_tables_sql()
conn.execute(schema_sql)

table_mappings = ssb.tables

for table_name, file_path in table_mappings.items():
    conn.execute(f"""
        INSERT INTO {table_name}
        SELECT * FROM read_csv('{file_path}', delim='|', header=false)
    """)
    print(f"Loaded {table_name}")

flight_1_queries = ["Q1.1", "Q1.2", "Q1.3"]
for query_id in flight_1_queries:
    query_sql = ssb.get_query(query_id, params={
        'year': 1993,
        'year_month': 199401,
        'week': 6,
        'discount_min': 1,
        'discount_max': 3,
        'quantity': 25,
        'quantity_min': 26,
        'quantity_max': 35
    })

    result = conn.execute(query_sql).fetchall()
    print(f"{query_id}: {len(result)} rows, Revenue: {result[0][0] if result else 0}")
```

### Performance Testing Framework

```python
import time
from typing import Dict, List

class SSBPerformanceTester:
    def __init__(self, ssb: SSB, connection):
        self.ssb = ssb
        self.connection = connection

    def run_flight_benchmark(self, flight_number: int, iterations: int = 3) -> Dict:
        flight_queries = {
            1: ["Q1.1", "Q1.2", "Q1.3"],
            2: ["Q2.1", "Q2.2", "Q2.3"],
            3: ["Q3.1", "Q3.2", "Q3.3", "Q3.4"],
            4: ["Q4.1", "Q4.2", "Q4.3"]
        }

        if flight_number not in flight_queries:
            raise ValueError(f"Invalid flight number: {flight_number}")

        query_ids = flight_queries[flight_number]
        results = {}

        for query_id in query_ids:
            print(f"Benchmarking {query_id}...")

            times = []
            for iteration in range(iterations):
                query_sql = self.ssb.get_query(query_id)

                start_time = time.time()
                result = self.connection.execute(query_sql).fetchall()
                execution_time = time.time() - start_time

                times.append(execution_time)

            results[query_id] = {
                'avg_time': sum(times) / len(times),
                'min_time': min(times),
                'max_time': max(times),
                'rows_returned': len(result),
                'times': times
            }

        return results

    def _get_query_with_custom_params(self, query_id: str) -> str:
        custom_params = {
            'year': 1993,
            'year_month': 199401,
            'week': 6,
            'discount_min': 1,
            'discount_max': 3,
            'quantity': 25,
            'quantity_min': 26,
            'quantity_max': 35,
            'category': 'MFGR#12',
            'region': 'AMERICA',
            'brand_min': 'MFGR#2221',
            'brand_max': 'MFGR#2228',
            'brand': 'MFGR#2221',
            'nation1': 'UNITED STATES',
            'nation2': 'JAPAN',
            'city': 'UNITED KI1'
        }

        return self.ssb.get_query(query_id, params=custom_params)

    def run_complete_benchmark(self) -> Dict:
        complete_results = {}

        for flight_num in range(1, 5):
            print(f"Running Flight {flight_num}...")
            flight_results = self.run_flight_benchmark(flight_num)
            complete_results[f"Flight_{flight_num}"] = flight_results

        all_times = []
        for flight_data in complete_results.values():
            for query_data in flight_data.values():
                all_times.extend(query_data['times'])

        complete_results['summary'] = {
            'total_queries': sum(len(flight) for flight in complete_results.values() if isinstance(flight, dict)),
            'total_avg_time': sum(all_times) / len(all_times),
            'total_min_time': min(all_times),
            'total_max_time': max(all_times)
        }

        return complete_results

performance_tester = SSBPerformanceTester(ssb, conn)

flight_1_results = performance_tester.run_flight_benchmark(1)
print("Flight 1 Results:")
for query_id, stats in flight_1_results.items():
    print(f"  {query_id}: {stats['avg_time']:.3f}s avg, {stats['rows_returned']} rows")

complete_results = performance_tester.run_complete_benchmark()
```

### Columnar Database Optimization

SSB is particularly well suited to columnar databases. The example below creates a column-store configured copy of the fact table ordered for columnar access, adds projection indices for common query patterns, and indexes the dimension tables:

```python
def optimize_for_columnar(ssb: SSB, connection):

    optimization_sql = """
    CREATE TABLE lineorder_configured AS
    SELECT
        lo_orderdate,
        lo_custkey,
        lo_partkey,
        lo_suppkey,
        lo_quantity,
        lo_extendedprice,
        lo_discount,
        lo_revenue,
        lo_supplycost,
        lo_tax
    FROM lineorder
    ORDER BY lo_orderdate, lo_custkey;

    CREATE INDEX idx_lineorder_date ON lineorder_configured(lo_orderdate);
    CREATE INDEX idx_lineorder_cust ON lineorder_configured(lo_custkey);
    CREATE INDEX idx_lineorder_part ON lineorder_configured(lo_partkey);
    CREATE INDEX idx_lineorder_supp ON lineorder_configured(lo_suppkey);

    CREATE INDEX idx_date_year ON date(d_year);
    CREATE INDEX idx_customer_region ON customer(c_region);
    CREATE INDEX idx_supplier_region ON supplier(s_region);
    CREATE INDEX idx_part_category ON part(p_category);
    """

    connection.execute(optimization_sql)
    print("Applied columnar optimizations")

optimize_for_columnar(ssb, conn)
```

## Performance Characteristics

### Query Performance Patterns

**Flight 1 (Simple Aggregation):**
- **Fastest queries** - Single table scans with aggregation
- **Performance depends on**: Fact table scan speed, aggregation efficiency
- **Typical runtime**: Milliseconds to seconds

**Flight 2 (Basic Star Joins):**
- **Medium complexity** - 2-3 table joins with aggregation
- **Performance depends on**: Join algorithm efficiency, dimension table size
- **Typical runtime**: Seconds

**Flight 3 (Complex Star Joins):**
- **Higher complexity** - 4 table joins with grouping
- **Performance depends on**: Join order optimization, hash table construction
- **Typical runtime**: Seconds to tens of seconds

**Flight 4 (Analytical Queries):**
- **Most complex** - Multi-table joins with calculations
- **Performance depends on**: Query optimization, parallel execution
- **Typical runtime**: Seconds to minutes

### System Optimization Opportunities

| System Type | Optimization Focus | SSB Flight Emphasis |
|-------------|-------------------|-------------------|
| **Columnar Stores** | Column scanning, compression | Flight 1 (aggregation) |
| **In-Memory Systems** | Join algorithms, parallel execution | Flight 3 (multi-joins) |
| **MPP Systems** | Data distribution, parallel aggregation | Flight 4 (complex analytics) |
| **OLAP Engines** | Cube pre-computation, drill-down | All flights |

## Configuration Options

### Scale Factor Guidelines

| Scale Factor | Fact Table Rows | Data Size | Use Case |
|-------------|-----------------|-----------|----------|
| 0.01 | ~60,000 | ~5 MB | Development, unit testing |
| 0.1 | ~600,000 | ~50 MB | Integration testing |
| 1 | ~6,000,000 | ~500 MB | Standard benchmark |
| 10 | ~60,000,000 | ~5 GB | Performance testing |
| 100 | ~600,000,000 | ~50 GB | Large-scale testing |

### Advanced-level Configuration

In the constructor, `date_range_years` sets the date range (default 7 years), `enable_compression` compresses output files, `partition_fact_table` partitions LINEORDER by date, and `generate_indices` generates the recommended indices. The `query_params` dictionary sets the year for temporal queries, the geographic focus, the product category, and the discount selectivity.

```python
ssb = SSB(
    scale_factor=1.0,
    output_dir="ssb_data",
    date_range_years=7,
    enable_compression=True,
    partition_fact_table=True,
    generate_indices=True
)

query_params = {
    'year': 1995,
    'region': 'ASIA',
    'category': 'MFGR#13',
    'discount_range': (2, 4)
}

query = ssb.get_query("Q2.1", params=query_params)
```

## Integration Examples

### Apache Spark Integration

The example caches the dimension tables for better join performance.

```python
from pyspark.sql import SparkSession
from benchbox import SSB

spark = SparkSession.builder.appName("SSB-Benchmark").getOrCreate()
ssb = SSB(scale_factor=10, output_dir="/data/ssb_sf10")

data_files = ssb.generate_data()

tables = ['date', 'customer', 'supplier', 'part', 'lineorder']
for table_name in tables:
    file_path = f"/data/ssb_sf10/{table_name}.csv"

    df = spark.read.csv(file_path, header=True, inferSchema=True)
    df.createOrReplaceTempView(table_name)

    if table_name != 'lineorder':
        df.cache()

flight_2_queries = ["Q2.1", "Q2.2", "Q2.3"]
for query_id in flight_2_queries:
    query_sql = ssb.get_query(query_id, params={
        'category': 'MFGR#12',
        'region': 'AMERICA',
        'brand_min': 'MFGR#2221',
        'brand_max': 'MFGR#2228',
        'brand': 'MFGR#2221'
    })

    result_df = spark.sql(query_sql)
    result_df.show(10)
    print(f"{query_id} execution plan:")
    result_df.explain(True)
```

### ClickHouse Integration

The example creates ClickHouse tables configured for analytics: the fact table is optimized for ClickHouse, partitioned by year, and followed by the dimension tables. Only the `customer` dimension table is shown; define the other dimension tables the same way. It then loads data with ClickHouse's CSV import and runs the configured SSB queries.

```python
import clickhouse_connect
from benchbox import SSB

client = clickhouse_connect.get_client(host='localhost', port=8123)
ssb = SSB(scale_factor=1.0, output_dir="ssb_data")

data_files = ssb.generate_data()

create_tables_sql = """
CREATE TABLE lineorder (
    lo_orderkey UInt32,
    lo_linenumber UInt8,
    lo_custkey UInt32,
    lo_partkey UInt32,
    lo_suppkey UInt32,
    lo_orderdate Date,
    lo_orderpriority String,
    lo_shippriority UInt8,
    lo_quantity UInt8,
    lo_extendedprice UInt32,
    lo_ordtotalprice UInt32,
    lo_discount UInt8,
    lo_revenue UInt32,
    lo_supplycost UInt32,
    lo_tax UInt8,
    lo_commitdate Date,
    lo_shipmode String
) ENGINE = MergeTree()
PARTITION BY toYear(lo_orderdate)
ORDER BY (lo_orderdate, lo_custkey, lo_partkey);

CREATE TABLE customer (
    c_custkey UInt32,
    c_name String,
    c_address String,
    c_city String,
    c_nation String,
    c_region String,
    c_phone String,
    c_mktsegment String
) ENGINE = MergeTree()
ORDER BY c_custkey;

"""

client.execute(create_tables_sql)

for table_name in ['customer', 'supplier', 'part', 'date', 'lineorder']:
    file_path = ssb.output_dir / f"{table_name}.csv"

    with open(file_path, 'rb') as f:
        client.insert_file(table_name, f, fmt='CSV')

flight_1_results = {}
for query_id in ["Q1.1", "Q1.2", "Q1.3"]:
    query_sql = ssb.get_query(query_id, params={
        'year': 1993,
        'year_month': 199401,
        'week': 6,
        'discount_min': 1,
        'discount_max': 3,
        'quantity': 25,
        'quantity_min': 26,
        'quantity_max': 35
    })

    result = client.query(query_sql)
    flight_1_results[query_id] = result.result_rows
    print(f"{query_id}: {len(result.result_rows)} rows")
```

## Best Practices

### Data Generation
1. **Use appropriate scale factors** - Start small for development, scale up for performance testing
2. **Consider data distribution** - SSB data follows specific distribution patterns
3. **Optimize for target system** - Generate data formats suited to your database
4. **Partition fact tables** - Consider date-based partitioning for large scale factors

### Query Optimization
1. **Start with Flight 1** - Test basic aggregation performance first
2. **Optimize join orders** - Ensure smallest dimension tables are accessed first
3. **Use column indices** - Create indices on commonly filtered columns
4. **Test with different parameters** - Vary selectivity to understand performance characteristics

### Performance Testing
1. **Warm-up queries** - Run queries multiple times to account for caching
2. **Monitor resource usage** - Track CPU, memory, and I/O during execution
3. **Test incremental complexity** - Progress through flights methodically
4. **Compare with baselines** - Establish performance baselines for regression testing

## Common Issues and Solutions

### Performance Issues

**Issue: Slow fact table scans in Flight 1**

Solution: ensure proper indexing and partitioning.
```sql
CREATE INDEX idx_lineorder_orderdate ON lineorder(lo_orderdate);
CREATE INDEX idx_lineorder_discount ON lineorder(lo_discount);
CREATE INDEX idx_lineorder_quantity ON lineorder(lo_quantity);
```

**Issue: Inefficient joins in Flight 3**

Solution: optimize join order by listing the smallest tables first, from small to large. The other join conditions are omitted here.
```sql
SELECT c_nation, s_nation, d_year, sum(lo_revenue) as revenue
FROM date, customer, supplier, lineorder
WHERE lo_orderdate = d_datekey
  AND lo_custkey = c_custkey
  AND lo_suppkey = s_suppkey
```

### Data Loading Issues

**Issue: Memory issues with large scale factors**

Solution: use streaming data generation. `stream_generation=True` generates data in chunks, and `chunk_size=1000000` sets 1M rows per chunk.
```python
ssb = SSB(
    scale_factor=100,
    output_dir="/data/ssb_large",
    stream_generation=True,
    chunk_size=1000000
)
```

**Issue: Incorrect query results**

Solution: validate data generation and parameterization.
```python
validation_queries = [
    "SELECT COUNT(*) FROM lineorder",
    "SELECT MIN(d_year), MAX(d_year) FROM date",
    "SELECT COUNT(DISTINCT c_region) FROM customer"
]

for vq in validation_queries:
    result = conn.execute(vq).fetchone()
    print(f"Validation: {result}")
```

## Related Documentation

- [TPC-H Benchmark](tpc-h.md) - Original normalized schema version
- [ClickBench](clickbench.md) - Analytics-focused benchmark
- [Read Primitives Benchmark](read-primitives.md) - Basic database operations
- [Architecture Guide](../design/architecture.md) - BenchBox design principles
- [Usage Guide](../usage/README.md) - General usage patterns

## External Resources

- [Original SSB Paper](https://www.cs.umb.edu/~poneil/StarSchemaB.PDF) - "Star Schema Benchmark" by O'Neil et al.
- [SSB Query Details](http://www.cs.umb.edu/~poneil/StarSchemaB.PDF) - Complete query specifications
- [OLAP Performance Testing](https://en.wikipedia.org/wiki/Star_schema) - Star schema design principles
- [Data Warehouse Benchmarking](https://link.springer.com/chapter/10.1007/978-3-642-10424-4_17) - Industry benchmarking practices
