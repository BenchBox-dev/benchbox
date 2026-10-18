<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# H2O DB Benchmark

```{tags} intermediate, concept, h2odb
```

> **CLI name:** `h2odb` - use `benchbox run --benchmark h2odb`

## Overview

The H2O DB Benchmark is designed to test analytical database performance using real-world taxi trip data patterns. Originally developed by H2O.ai for their database benchmarking initiative, this benchmark focuses on fundamental analytical operations that are common in data science and machine learning workflows: aggregations, grouping operations, and time-series analysis.

The benchmark is particularly valuable for testing systems used in data science pipelines, as it models the types of exploratory data analysis and feature engineering operations that data scientists perform regularly on large datasets.

## Key Features

- **Real-world data patterns** - Based on NYC taxi trip data structure
- **Data science focus** - Tests operations common in ML pipelines
- **Single-table design** - Emphasizes aggregation and grouping performance
- **Time-series operations** - Tests temporal aggregation patterns
- **Scalable testing** - Configurable data sizes from MB to TB scale
- **Analytics-oriented queries** - Focuses on data exploration patterns
- **Performance measurement** - Designed for precise timing comparisons

BenchBox generates synthetic taxi trip data that mimics the structure and characteristics of the NYC Taxi & Limousine Commission Trip Record Data used in the H2O DB benchmark.

## Schema Description

The H2O DB benchmark uses a single table design based on the NYC Taxi & Limousine Commission Trip Record Data structure:

### Core Table

| Table | Purpose | Approximate Rows (SF 1) |
|-------|---------|-------------------------|
| **TRIPS** | Taxi trip records with fare and location data | 10,000,000 |

### Schema Details

**TRIPS Table Structure:**

| Column | Type | Description |
|--------|------|-------------|
| `vendor_id` | INTEGER | Taxi vendor identifier |
| `pickup_datetime` | TIMESTAMP | Trip start timestamp |
| `dropoff_datetime` | TIMESTAMP | Trip end timestamp |
| `passenger_count` | INTEGER | Number of passengers |
| `trip_distance` | DECIMAL(8,2) | Trip distance in miles |
| `pickup_longitude` | DECIMAL(18,14) | Pickup location longitude |
| `pickup_latitude` | DECIMAL(18,14) | Pickup location latitude |
| `rate_code_id` | INTEGER | Rate code for the trip |
| `store_and_fwd_flag` | VARCHAR(1) | Whether trip was stored before transmission |
| `dropoff_longitude` | DECIMAL(18,14) | Dropoff location longitude |
| `dropoff_latitude` | DECIMAL(18,14) | Dropoff location latitude |
| `payment_type` | INTEGER | Payment method used |
| `fare_amount` | DECIMAL(8,2) | Base fare amount |
| `extra` | DECIMAL(8,2) | Extra charges |
| `mta_tax` | DECIMAL(8,2) | Metropolitan Transportation Authority tax |
| `tip_amount` | DECIMAL(8,2) | Tip amount |
| `tolls_amount` | DECIMAL(8,2) | Tolls paid |
| `improvement_surcharge` | DECIMAL(8,2) | Improvement surcharge |
| `total_amount` | DECIMAL(8,2) | Total trip cost |
| `pickup_location_id` | INTEGER | Pickup location zone ID |
| `dropoff_location_id` | INTEGER | Dropoff location zone ID |
| `congestion_surcharge` | DECIMAL(8,2) | Congestion pricing surcharge |

### Data Characteristics

The TRIPS table contains realistic data distributions that reflect actual taxi usage patterns:

- **Temporal patterns**: Peak hours, seasonal variations, weekday/weekend differences
- **Geographic clusters**: High-density pickup/dropoff areas (airports, Manhattan, etc.)
- **Fare distributions**: Realistic fare amounts, tip percentages, and surcharges
- **Passenger patterns**: Typical passenger counts and trip distances

## Query Characteristics

The H2O DB benchmark includes analytical queries that test different aspects of database performance.

```{tip}
Each query has a [query template](queries/h2odb/index.md) with a representative
SQL and DataFrame rendering, plus how to extract the exact statement for your
platform and scale.
```

### Basic Aggregation Queries

**[Q1](queries/h2odb/q1.md): Simple Count**
```sql
SELECT COUNT(*) as count
FROM trips;
```
- **Purpose**: Test basic table scanning and counting
- **Performance focus**: Sequential scan optimization

**[Q2](queries/h2odb/q2.md): Sum and Mean**
```sql
SELECT
    SUM(fare_amount) as sum_fare_amount,
    AVG(fare_amount) as mean_fare_amount
FROM trips;
```
- **Purpose**: Test numeric aggregation functions
- **Performance focus**: Aggregation algorithm efficiency

### Grouping Queries

**[Q3](queries/h2odb/q3.md): Single-Column Grouping**
```sql
SELECT
    passenger_count,
    SUM(fare_amount) as sum_fare_amount
FROM trips
GROUP BY passenger_count
ORDER BY passenger_count;
```
- **Purpose**: Test basic GROUP BY performance
- **Performance focus**: Hash aggregation vs. sort-based grouping

**[Q4](queries/h2odb/q4.md): Multi-Aggregate Grouping**
```sql
SELECT
    passenger_count,
    SUM(fare_amount) as sum_fare_amount,
    AVG(fare_amount) as mean_fare_amount
FROM trips
GROUP BY passenger_count
ORDER BY passenger_count;
```
- **Purpose**: Test multiple aggregation functions in single query
- **Performance focus**: Aggregation pipeline efficiency

**[Q5](queries/h2odb/q5.md): Two-Column Grouping**
```sql
SELECT
    passenger_count,
    vendor_id,
    SUM(fare_amount) as sum_fare_amount
FROM trips
GROUP BY passenger_count, vendor_id
ORDER BY passenger_count, vendor_id;
```
- **Purpose**: Test multi-column grouping performance
- **Performance focus**: Hash table size and collision handling

### Temporal Analysis Queries

**[Q7](queries/h2odb/q7.md): Time-Based Grouping**
```sql
SELECT
    EXTRACT(HOUR FROM pickup_datetime) as hour,
    SUM(fare_amount) as sum_fare_amount
FROM trips
GROUP BY EXTRACT(HOUR FROM pickup_datetime)
ORDER BY hour;
```
- **Purpose**: Test date/time function performance
- **Performance focus**: Temporal extraction and grouping

**[Q8](queries/h2odb/q8.md): Complex Temporal Analysis**
```sql
SELECT
    DATE_TRUNC('day', pickup_datetime) as pickup_date,
    passenger_count,
    COUNT(*) as trip_count,
    AVG(fare_amount) as avg_fare,
    AVG(trip_distance) as avg_distance
FROM trips
WHERE pickup_datetime >= '{start_date}'
  AND pickup_datetime < '{end_date}'
GROUP BY DATE_TRUNC('day', pickup_datetime), passenger_count
ORDER BY pickup_date, passenger_count;
```
- **Purpose**: Test complex temporal aggregation with filtering
- **Performance focus**: Date range filtering and multi-level grouping

### Advanced-level Analytics Queries

**[Q9](queries/h2odb/q9.md): Statistical Analysis**
```sql
SELECT
    vendor_id,
    COUNT(*) as trip_count,
    SUM(fare_amount) as total_revenue,
    AVG(fare_amount) as avg_fare,
    STDDEV(fare_amount) as fare_stddev,
    MIN(fare_amount) as min_fare,
    MAX(fare_amount) as max_fare,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY fare_amount) as median_fare
FROM trips
WHERE fare_amount > 0
GROUP BY vendor_id
ORDER BY total_revenue DESC;
```
- **Purpose**: Test statistical function performance
- **Performance focus**: Percentile calculations and complex aggregations

## Usage Examples

### Basic Benchmark Setup

```python
from benchbox import H2ODB

h2odb = H2ODB(scale_factor=1.0, output_dir="h2odb_data")

data_files = h2odb.generate_data()

queries = h2odb.get_queries()
print(f"Generated {len(queries)} H2O DB queries")

count_query = h2odb.get_query("Q1")
print(count_query)
```

### Data Generation at Scale

```python
h2odb_large = H2ODB(scale_factor=10.0, output_dir="h2odb_large")
data_files = h2odb_large.generate_data()

trips_file = h2odb_large.output_dir / "trips.csv"
size_gb = trips_file.stat().st_size / (1024**3)
print(f"Generated trips data: {size_gb:.2f} GB")
```

### DuckDB Integration Example

```python
import duckdb
from benchbox import H2ODB

h2odb = H2ODB(scale_factor=0.1, output_dir="h2odb_small")
data_files = h2odb.generate_data()

conn = duckdb.connect("h2odb.duckdb")
schema_sql = h2odb.get_create_tables_sql()
conn.execute(schema_sql)

trips_file = h2odb.output_dir / "trips.csv"
conn.execute(f"""
    INSERT INTO trips
    SELECT * FROM read_csv('{trips_file}',
                          header=true,
                          auto_detect=true)
""")

row_count = conn.execute("SELECT COUNT(*) FROM trips").fetchone()[0]
print(f"Loaded {row_count:,} trip records")

query_results = {}

for query_id in ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6"]:
    query_sql = h2odb.get_query(query_id)
    result = conn.execute(query_sql).fetchall()
    query_results[query_id] = len(result)
    print(f"{query_id}: {len(result)} result rows")

temporal_params = {
    'start_date': '2020-01-01',
    'end_date': '2020-01-31'
}

for query_id in ["Q7", "Q8"]:
    query_sql = h2odb.get_query(query_id, params=temporal_params)
    result = conn.execute(query_sql).fetchall()
    query_results[query_id] = len(result)
    print(f"{query_id}: {len(result)} result rows")

conn.close()
```

Queries [Q1](queries/h2odb/q1.md) to [Q6](queries/h2odb/q6.md) are the basic aggregation tests. [Q7](queries/h2odb/q7.md) and [Q8](queries/h2odb/q8.md) are temporal analysis queries and take start and end dates as parameters.

### Performance Benchmarking Framework

```python
import time
from typing import Dict, List
from statistics import mean, median

class H2ODBPerformanceTester:
    def __init__(self, h2odb: H2ODB, connection):
        self.h2odb = h2odb
        self.connection = connection

    def benchmark_query_group(self, query_group: str, iterations: int = 3) -> Dict:
        query_groups = {
            'basic': ['Q1', 'Q2'],
            'grouping': ['Q3', 'Q4', 'Q5', 'Q6'],
            'temporal': ['Q7', 'Q8'],
            'advanced': ['Q9', 'Q10']
        }

        if query_group not in query_groups:
            raise ValueError(f"Invalid query group: {query_group}")

        query_ids = query_groups[query_group]
        results = {}

        params = {
            'start_date': '2020-01-01',
            'end_date': '2020-01-31',
            'min_fare': 5.0,
            'max_fare': 100.0
        }

        for query_id in query_ids:
            print(f"Benchmarking {query_id} ({query_group})...")

            times = []
            for iteration in range(iterations):
                query_sql = self.h2odb.get_query(query_id, params=params)

                start_time = time.time()
                result = self.connection.execute(query_sql).fetchall()
                execution_time = time.time() - start_time

                times.append(execution_time)
                print(f"  Iteration {iteration + 1}: {execution_time:.3f}s")

            results[query_id] = {
                'group': query_group,
                'avg_time': mean(times),
                'median_time': median(times),
                'min_time': min(times),
                'max_time': max(times),
                'rows_returned': len(result),
                'times': times
            }

        return results

    def run_complete_benchmark(self) -> Dict:
        complete_results = {}

        for group in ['basic', 'grouping', 'temporal', 'advanced']:
            print(f"\\nRunning {group.upper()} queries...")
            try:
                group_results = self.benchmark_query_group(group)
                complete_results[group] = group_results
            except Exception as e:
                print(f"Error in {group} queries: {e}")
                complete_results[group] = {'error': str(e)}

        all_times = []
        for group_data in complete_results.values():
            if isinstance(group_data, dict) and 'error' not in group_data:
                for query_data in group_data.values():
                    if isinstance(query_data, dict) and 'times' in query_data:
                        all_times.extend(query_data['times'])

        if all_times:
            complete_results['summary'] = {
                'total_queries': len(all_times) // 3,
                'total_avg_time': mean(all_times),
                'total_median_time': median(all_times),
                'total_min_time': min(all_times),
                'total_max_time': max(all_times)
            }

        return complete_results

    def analyze_aggregation_performance(self) -> Dict:
        aggregation_tests = [
            ('single_agg', 'SELECT passenger_count, SUM(fare_amount) FROM trips GROUP BY passenger_count'),
            ('multi_agg', 'SELECT passenger_count, SUM(fare_amount), AVG(fare_amount), COUNT(*) FROM trips GROUP BY passenger_count'),
            ('two_col_group', 'SELECT passenger_count, vendor_id, SUM(fare_amount) FROM trips GROUP BY passenger_count, vendor_id'),
            ('complex_agg', 'SELECT vendor_id, COUNT(*), SUM(fare_amount), AVG(fare_amount), STDDEV(fare_amount) FROM trips GROUP BY vendor_id')
        ]

        results = {}

        for test_name, query_sql in aggregation_tests:
            print(f"Testing {test_name}...")

            times = []
            for iteration in range(3):
                start_time = time.time()
                result = self.connection.execute(query_sql).fetchall()
                execution_time = time.time() - start_time
                times.append(execution_time)

            results[test_name] = {
                'avg_time': mean(times),
                'rows_returned': len(result),
                'times': times
            }

        return results

performance_tester = H2ODBPerformanceTester(h2odb, conn)

basic_results = performance_tester.benchmark_query_group('basic')
grouping_results = performance_tester.benchmark_query_group('grouping')

print("\\nQuery Group Performance Summary:")
print(f"Basic Queries: {basic_results}")
print(f"Grouping Queries: {grouping_results}")

complete_results = performance_tester.run_complete_benchmark()
print(f"\\nComplete Benchmark Summary: {complete_results.get('summary', 'No summary available')}")

agg_results = performance_tester.analyze_aggregation_performance()
print(f"\\nAggregation Performance Analysis: {agg_results}")
```

The `total_queries` summary divides the number of timings by 3 because each query runs 3 iterations by default.

### Data Science Workflow Integration

```python
import pandas as pd
from benchbox import H2ODB

h2odb = H2ODB(scale_factor=1.0, output_dir="h2odb_ds")
data_files = h2odb.generate_data()

trips_df = pd.read_csv(h2odb.output_dir / "trips.csv")

print("Data Science Operations Performance Test:")

start_time = time.time()
trips_df['hour'] = pd.to_datetime(trips_df['pickup_datetime']).dt.hour
trips_df['day_of_week'] = pd.to_datetime(trips_df['pickup_datetime']).dt.dayofweek
trips_df['trip_duration'] = (pd.to_datetime(trips_df['dropoff_datetime']) -
                            pd.to_datetime(trips_df['pickup_datetime'])).dt.total_seconds()
feature_eng_time = time.time() - start_time
print(f"Feature engineering: {feature_eng_time:.3f}s")

start_time = time.time()
hourly_stats = trips_df.groupby('hour').agg({
    'fare_amount': ['sum', 'mean', 'std', 'count'],
    'trip_distance': ['mean'],
    'passenger_count': ['mean']
}).round(2)
groupby_time = time.time() - start_time
print(f"GroupBy aggregation: {groupby_time:.3f}s")

start_time = time.time()
vendor_stats = trips_df.groupby('vendor_id').agg({
    'fare_amount': ['count', 'sum', 'mean', 'std', 'min', 'max'],
    'tip_amount': ['mean', 'std'],
    'trip_distance': ['mean', 'std']
}).round(2)
stats_time = time.time() - start_time
print(f"Statistical analysis: {stats_time:.3f}s")

print(f"\\nTotal preprocessing time: {feature_eng_time + groupby_time + stats_time:.3f}s")
```

The example loads the data into pandas to simulate ML preprocessing, then times three typical data science operations that H2O DB tests: feature engineering, GroupBy aggregation (similar to the H2O DB queries) and statistical analysis.

## Performance Characteristics

### Query Performance Patterns

**Basic Queries ([Q1](queries/h2odb/q1.md)-[Q2](queries/h2odb/q2.md)):**
- **Primary bottleneck**: Sequential scan speed and basic aggregation
- **Optimization targets**: Column store scanning, SIMD operations
- **Typical performance**: Sub-second (10ms-2s depending on data size)
- **Scaling characteristics**: Linear with data size

**Grouping Queries ([Q3](queries/h2odb/q3.md)-[Q6](queries/h2odb/q6.md)):**
- **Primary bottleneck**: Hash table construction and aggregation algorithms
- **Optimization targets**: Hash aggregation efficiency, memory management
- **Typical performance**: Fast to medium, seconds to tens of seconds
- **Scaling characteristics**: Sub-linear with good hash distribution

**Temporal Queries ([Q7](queries/h2odb/q7.md)-[Q8](queries/h2odb/q8.md)):**
- **Primary bottleneck**: Date/time function evaluation and temporal grouping
- **Optimization targets**: Date extraction optimization, temporal indexing
- **Typical performance**: Medium, seconds to minutes
- **Scaling characteristics**: Depends on temporal selectivity

**Advanced-level Analytics ([Q9](queries/h2odb/q9.md)-[Q10](queries/h2odb/q10.md)):**
- **Primary bottleneck**: Statistical function computation, complex aggregations
- **Optimization targets**: Statistical algorithm efficiency, sorting performance
- **Typical performance**: Slower, minutes for large datasets
- **Scaling characteristics**: Often super-linear for percentile calculations

### System Optimization Opportunities

| System Type | Basic Queries | Grouping Queries | Temporal Queries | Analytics Queries |
|-------------|---------------|------------------|------------------|-------------------|
| **Columnar Stores** | Column scanning | Column-wise hash tables | Date column optimization | Vectorized statistics |
| **Row Stores** | Index scanning | Row-wise aggregation | Temporal indexes | Row-wise computation |
| **In-Memory** | SIMD operations | In-memory hash tables | Fast date functions | In-memory sorting |
| **GPU-Accelerated** | Parallel scanning | GPU hash aggregation | GPU date operations | GPU statistical functions |

## Configuration Options

### Scale Factor Guidelines

| Scale Factor | Rows | Data Size | Memory Usage | Use Case |
|-------------|------|-----------|--------------|----------|
| 0.01 | ~100K | ~10 MB | < 100 MB | Development |
| 0.1 | ~1M | ~100 MB | < 500 MB | Testing |
| 1.0 | ~10M | ~1 GB | < 4 GB | Standard benchmark |
| 10.0 | ~100M | ~10 GB | < 20 GB | Performance testing |
| 100.0 | ~1B | ~100 GB | < 100 GB | Large-scale testing |
| 1000.0 | ~10B | ~1 TB | < 500 GB | Production simulation |

### Advanced-level Configuration

```python
h2odb = H2ODB(
    scale_factor=1.0,
    output_dir="h2odb_data",
    date_range_days=365,
    trip_distance_max=50.0,
    fare_amount_max=200.0,
    enable_indexing=True,
    partition_by_date=True,
    compress_output=True
)
```

- `date_range_days`: range of trip dates.
- `trip_distance_max`: maximum trip distance.
- `fare_amount_max`: maximum fare amount.
- `enable_indexing`: create performance indices.
- `partition_by_date`: partition by pickup date.
- `compress_output`: compress generated files.

## Integration Examples

### Apache Spark Integration

```python
from pyspark.sql import SparkSession
from pyspark.sql.functions import *
from benchbox import H2ODB

spark = SparkSession.builder \
    .appName("H2ODB-Benchmark") \
    .config("spark.sql.adaptive.enabled", "true") \
    .config("spark.sql.adaptive.coalescePartitions.enabled", "true") \
    .getOrCreate()

h2odb = H2ODB(scale_factor=100, output_dir="/data/h2odb_sf100")
data_files = h2odb.generate_data()

trips_df = spark.read.csv("/data/h2odb_sf100/trips.csv",
                         header=True, inferSchema=True)

trips_df = trips_df.withColumn("pickup_date",
                               to_date("pickup_datetime"))
trips_df = trips_df.repartition(100, "pickup_date")
trips_df.cache()
trips_df.createOrReplaceTempView("trips")

print("Running H2O DB queries on Spark...")

q1_result = spark.sql("SELECT COUNT(*) as count FROM trips")
q1_result.show()

q3_result = spark.sql("""
    SELECT
        passenger_count,
        SUM(fare_amount) as sum_fare_amount
    FROM trips
    GROUP BY passenger_count
    ORDER BY passenger_count
""")
q3_result.show()

q7_result = spark.sql("""
    SELECT
        hour(pickup_datetime) as hour,
        SUM(fare_amount) as sum_fare_amount
    FROM trips
    GROUP BY hour(pickup_datetime)
    ORDER BY hour
""")
q7_result.show()

q3_result.explain(True)

spark.stop()
```

The example partitions the data by pickup date for temporal queries and caches it. [Q1](queries/h2odb/q1.md) is a basic count, [Q3](queries/h2odb/q3.md) groups by passenger count, and [Q7](queries/h2odb/q7.md) is a temporal analysis by hour. `explain(True)` shows the execution plan for [Q3](queries/h2odb/q3.md).

### ClickHouse Integration

```python
import clickhouse_connect
from benchbox import H2ODB

client = clickhouse_connect.get_client(host='localhost', port=8123)
h2odb = H2ODB(scale_factor=10.0, output_dir="h2odb_data")

data_files = h2odb.generate_data()

create_table_sql = """
CREATE TABLE trips (
    vendor_id UInt8,
    pickup_datetime DateTime,
    dropoff_datetime DateTime,
    passenger_count UInt8,
    trip_distance Decimal(8,2),
    pickup_longitude Decimal(18,14),
    pickup_latitude Decimal(18,14),
    rate_code_id UInt8,
    store_and_fwd_flag String,
    dropoff_longitude Decimal(18,14),
    dropoff_latitude Decimal(18,14),
    payment_type UInt8,
    fare_amount Decimal(8,2),
    extra Decimal(8,2),
    mta_tax Decimal(8,2),
    tip_amount Decimal(8,2),
    tolls_amount Decimal(8,2),
    improvement_surcharge Decimal(8,2),
    total_amount Decimal(8,2),
    pickup_location_id UInt16,
    dropoff_location_id UInt16,
    congestion_surcharge Decimal(8,2)
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(pickup_datetime)
ORDER BY (pickup_datetime, vendor_id);
"""

client.execute(create_table_sql)

trips_file = h2odb.output_dir / "trips.csv"
with open(trips_file, 'rb') as f:
    client.insert_file('trips', f, fmt='CSV')

print("Running H2O DB queries on ClickHouse...")

q1_configured = "SELECT count() FROM trips"
q1_result = client.query(q1_configured)
print(f"Q1 result: {q1_result.result_rows}")

q3_configured = """
SELECT
    passenger_count,
    sum(fare_amount) as sum_fare_amount
FROM trips
GROUP BY passenger_count
ORDER BY passenger_count
"""
q3_result = client.query(q3_configured)
print(f"Q3 results: {len(q3_result.result_rows)} groups")

q7_configured = """
SELECT
    toHour(pickup_datetime) as hour,
    sum(fare_amount) as sum_fare_amount
FROM trips
GROUP BY toHour(pickup_datetime)
ORDER BY hour
"""
q7_result = client.query(q7_configured)
print(f"Q7 results: {len(q7_result.result_rows)} hours")
```

The table uses a MergeTree engine partitioned by month, and the queries use ClickHouse-native functions (`count()`, `sum()`, `toHour()`) for [Q1](queries/h2odb/q1.md) (count), [Q3](queries/h2odb/q3.md) (grouping) and [Q7](queries/h2odb/q7.md) (temporal analysis). The CSV is loaded with ClickHouse's CSV import.

## Best Practices

### Data Generation
1. **Scale appropriately** - Use realistic scale factors for your system capacity
2. **Consider temporal distribution** - Generate realistic date ranges
3. **Optimize data types** - Use appropriate precision for decimal fields
4. **Partition strategy** - Partition by date for temporal query performance

### Query Optimization
1. **Index strategy** - Create indices on frequently grouped columns
2. **Aggregation optimization** - Use hash aggregation where possible
3. **Memory management** - Monitor memory usage for large grouping operations
4. **Parallel execution** - Use parallel aggregation for large datasets

### Performance Testing
1. **Warm-up queries** - Run queries multiple times to account for caching
2. **Resource monitoring** - Monitor CPU, memory, and I/O during execution
3. **Baseline establishment** - Establish performance baselines for regression testing
4. **Statistical validation** - Use multiple iterations for reliable timing

## Common Issues and Solutions

### Performance Issues

**Issue: Slow aggregation queries on large datasets**

Solution: create appropriate indices and use columnar storage.

```sql
CREATE INDEX idx_trips_passenger_count ON trips(passenger_count);
CREATE INDEX idx_trips_vendor_id ON trips(vendor_id);
CREATE INDEX idx_trips_pickup_hour ON trips(EXTRACT(HOUR FROM pickup_datetime));

CREATE TABLE trips_columnar (
    LIKE trips
) USING PARQUET
PARTITIONED BY (DATE_TRUNC('month', pickup_datetime));
```

**Issue: Memory issues with large GROUP BY operations**

Solution: use incremental aggregation or external sorting. The first `SET` statement is for PostgreSQL and the second is for ClickHouse. Alternatively, break large groups into smaller pieces, such as the monthly chunk below.

```sql
SET work_mem = '2GB';
SET max_memory_usage = 8000000000;

SELECT passenger_count, vendor_id, SUM(fare_amount)
FROM trips
WHERE pickup_datetime >= '2020-01-01'
  AND pickup_datetime < '2020-02-01'
GROUP BY passenger_count, vendor_id;
```

### Data Quality Issues

**Issue: Incorrect temporal analysis results**

Solution: ensure proper timezone handling and date parsing. The `pickup_datetime >= '1900-01-01'` filter removes invalid dates.

```sql
SELECT
    EXTRACT(HOUR FROM pickup_datetime AT TIME ZONE 'UTC') as hour,
    SUM(fare_amount) as sum_fare_amount
FROM trips
WHERE pickup_datetime IS NOT NULL
  AND pickup_datetime >= '1900-01-01'
GROUP BY EXTRACT(HOUR FROM pickup_datetime AT TIME ZONE 'UTC')
ORDER BY hour;
```

**Issue: Unexpected aggregation results**

Solution: handle NULL values and outliers appropriately. The `passenger_count BETWEEN 0 AND 10` filter removes unrealistic values.

```sql
SELECT
    passenger_count,
    COUNT(*) as trip_count,
    SUM(CASE WHEN fare_amount > 0 THEN fare_amount ELSE 0 END) as sum_fare_amount,
    AVG(CASE WHEN fare_amount BETWEEN 0 AND 1000 THEN fare_amount END) as avg_fare_amount
FROM trips
WHERE passenger_count BETWEEN 0 AND 10
GROUP BY passenger_count
ORDER BY passenger_count;
```

## Related Documentation

- [ClickBench](clickbench.md) - Analytics-focused benchmark
- [AMPLab Benchmark](amplab.md) - Big data processing benchmark
- [Read Primitives Benchmark](read-primitives.md) - Basic database operations
- [Architecture Guide](../design/architecture.md) - BenchBox design principles
- [Usage Guide](../usage/README.md) - General usage patterns

## External Resources

- [H2O.ai DB Benchmark](https://h2oai.github.io/db-benchmark/) - Original benchmark specification
- [NYC Taxi Data](https://www1.nyc.gov/site/tlc/about/tlc-trip-record-data.page) - Source data format
- [Database Performance Analysis](https://duckdblabs.github.io/db-benchmark/) - Performance comparisons
- [Data Science Pipeline Optimization](https://docs.h2o.ai/h2o/latest-stable/h2o-docs/data-science.html) - H2O.ai data science resources
