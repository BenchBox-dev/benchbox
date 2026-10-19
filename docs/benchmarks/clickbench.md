<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# ClickBench (ClickHouse Analytics Benchmark)

```{tags} intermediate, concept, clickbench
```

> **CLI name:** `clickbench` - use `benchbox run --benchmark clickbench`

## Overview

ClickBench is a systematic analytics benchmark designed to test the performance of analytical database systems using real-world web analytics data patterns. Originally developed by ClickHouse, this benchmark has become a standard for evaluating OLAP database performance across different systems and architectures.

The benchmark uses a single flat table with web analytics data containing approximately 100 columns and tests 43 different query patterns that are representative of real-world analytics workloads. ClickBench is particularly valuable for testing columnar databases, OLAP engines, and systems designed for high-performance analytics.

## Key Features

- **43 analytical queries** covering diverse performance patterns
- **Single flat table design** - Emphasizes column store performance
- **Real-world data patterns** - Based on actual web analytics data
- **Comprehensive column coverage** - ~100 columns testing various data types
- **Performance-focused** - Designed for precise timing comparisons
- **Cross-system compatibility** - Standard benchmark across different databases
- **Scalable testing** - Configurable dataset sizes for different scenarios

BenchBox generates synthetic ClickBench data for testing and development.

## Schema Description

ClickBench uses a single table called **HITS** that represents web analytics data with systematic coverage of user interactions, browser information, geographic data, and various event attributes.

### HITS Table Structure

The HITS table contains ~100 columns covering all aspects of web analytics:

#### Core Event Data
| Column | Type | Description |
|--------|------|-------------|
| `WatchID` | BIGINT | Unique event identifier |
| `EventTime` | TIMESTAMP | Event timestamp |
| `EventDate` | DATE | Event date |
| `CounterID` | INTEGER | Analytics counter ID |
| `UserID` | BIGINT | User identifier |

#### User and Session Information
| Column | Type | Description |
|--------|------|-------------|
| `UserAgent` | SMALLINT | Browser user agent ID |
| `ClientIP` | INTEGER | Client IP address |
| `RegionID` | INTEGER | Geographic region ID |
| `Age` | SMALLINT | User age |
| `Sex` | SMALLINT | User gender |
| `Income` | SMALLINT | User income bracket |

#### Browser and Device Data
| Column | Type | Description |
|--------|------|-------------|
| `ResolutionWidth` | SMALLINT | Screen resolution width |
| `ResolutionHeight` | SMALLINT | Screen resolution height |
| `IsMobile` | SMALLINT | Mobile device flag |
| `MobilePhone` | SMALLINT | Mobile phone type |
| `MobilePhoneModel` | VARCHAR(255) | Mobile phone model |
| `JavaEnable` | SMALLINT | Java enabled flag |
| `JavascriptEnable` | SMALLINT | JavaScript enabled flag |
| `CookieEnable` | SMALLINT | Cookies enabled flag |

#### Page and Navigation Data
| Column | Type | Description |
|--------|------|-------------|
| `URL` | TEXT | Page URL |
| `Referer` | TEXT | Referrer URL |
| `Title` | TEXT | Page title |
| `IsRefresh` | SMALLINT | Page refresh flag |
| `IsNotBounce` | SMALLINT | Not a bounce flag |

#### Search and Advertising Data
| Column | Type | Description |
|--------|------|-------------|
| `SearchEngineID` | SMALLINT | Search engine identifier |
| `SearchPhrase` | VARCHAR(1024) | Search query phrase |
| `AdvEngineID` | SMALLINT | Advertising engine ID |

#### Technical and Performance Data
| Column | Type | Description |
|--------|------|-------------|
| `SendTiming` | INTEGER | Send timing in milliseconds |
| `DNSTiming` | INTEGER | DNS timing in milliseconds |
| `ConnectTiming` | INTEGER | Connection timing |
| `ResponseStartTiming` | INTEGER | Response start timing |
| `ResponseEndTiming` | INTEGER | Response end timing |
| `FetchTiming` | INTEGER | Fetch timing |

### Data Characteristics

The HITS table contains realistic web analytics distributions:
- **Temporal patterns**: Realistic time-of-day and seasonal usage patterns
- **Geographic distribution**: Global user base with regional concentrations
- **Device diversity**: Mixed desktop, mobile, and tablet usage
- **Search patterns**: Real search query distributions and patterns
- **User behavior**: Realistic session lengths, bounce rates, and engagement metrics

## Query Characteristics

ClickBench includes 43 queries that test different aspects of analytical performance.

```{tip}
Each query has a [query template](queries/clickbench/index.md) with a
representative SQL and DataFrame rendering, plus how to extract the exact
statement for your platform and scale.
```

### Basic Aggregation Queries (Q1-Q7)

**[Q1](queries/clickbench/q1.md): Simple Count**
```sql
SELECT COUNT(*) FROM hits;
```
- **Purpose**: Test basic table scanning performance
- **Performance focus**: Sequential scan optimization

**[Q2](queries/clickbench/q2.md): Filtered Count**
```sql
SELECT COUNT(*) FROM hits WHERE AdvEngineID <> 0;
```
- **Purpose**: Test filtering with aggregation
- **Performance focus**: Predicate evaluation during scan

**[Q3](queries/clickbench/q3.md): Multiple Aggregations**
```sql
SELECT SUM(AdvEngineID), COUNT(*), AVG(ResolutionWidth) FROM hits;
```
- **Purpose**: Test multiple aggregation functions
- **Performance focus**: Aggregation pipeline efficiency

**[Q5](queries/clickbench/q5.md): Distinct Count**
```sql
SELECT COUNT(DISTINCT UserID) FROM hits;
```
- **Purpose**: Test cardinality estimation and distinct operations
- **Performance focus**: Hash table efficiency for high cardinality

### Grouping and Ordering Queries (Q8-Q19)

**[Q8](queries/clickbench/q8.md): Basic GROUP BY**
```sql
SELECT AdvEngineID, COUNT(*) FROM hits
WHERE AdvEngineID <> 0
GROUP BY AdvEngineID
ORDER BY COUNT(*) DESC;
```
- **Purpose**: Test basic grouping with ordering
- **Performance focus**: Hash aggregation and sorting

**[Q9](queries/clickbench/q9.md): Distinct Users by Region**
```sql
SELECT RegionID, COUNT(DISTINCT UserID) AS u FROM hits
GROUP BY RegionID
ORDER BY u DESC
LIMIT 10;
```
- **Purpose**: Test complex aggregation with TOP-N
- **Performance focus**: Distinct counting within groups

**[Q13](queries/clickbench/q13.md): Search Phrase Analysis**
```sql
SELECT SearchPhrase, COUNT(*) AS c FROM hits
WHERE SearchPhrase <> ''
GROUP BY SearchPhrase
ORDER BY c DESC
LIMIT 10;
```
- **Purpose**: Test string grouping with high cardinality
- **Performance focus**: String hash performance

### String Operations Queries (Q20-Q29)

**[Q21](queries/clickbench/q21.md): String Pattern Matching**
```sql
SELECT COUNT(*) FROM hits WHERE URL LIKE '%google%';
```
- **Purpose**: Test string pattern matching performance
- **Performance focus**: Text search optimization

**[Q22](queries/clickbench/q22.md): Complex String Analysis**
```sql
SELECT SearchPhrase, MIN(URL), COUNT(*) AS c FROM hits
WHERE URL LIKE '%google%' AND SearchPhrase <> ''
GROUP BY SearchPhrase
ORDER BY c DESC
LIMIT 10;
```
- **Purpose**: Test combined string operations with grouping
- **Performance focus**: String processing with aggregation

**[Q29](queries/clickbench/q29.md): Regex Operations**
```sql
SELECT REGEXP_REPLACE(Referer, '^https?://(?:www\\.)?([^/]+)/.*$', '\\1') AS k,
       AVG(LENGTH(Referer)) AS l,
       COUNT(*) AS c,
       MIN(Referer)
FROM hits
WHERE Referer <> ''
GROUP BY k
HAVING COUNT(*) > 100000
ORDER BY l DESC
LIMIT 25;
```
- **Purpose**: Test complex regex and string functions
- **Performance focus**: Regex engine performance

### Complex Analytics Queries (Q30-Q43)

**[Q30](queries/clickbench/q30.md): Wide Aggregation**
```sql
SELECT SUM(ResolutionWidth), SUM(ResolutionWidth + 1), ..., SUM(ResolutionWidth + 89)
FROM hits;
```
- **Purpose**: Test wide query processing (89 aggregation columns)
- **Performance focus**: Expression evaluation and vectorization

**[Q37](queries/clickbench/q37.md): Time Series Analysis**
```sql
SELECT SUM(ResolutionWidth),
       SUM(ResolutionWidth + 1),
       SUM(ResolutionWidth + 2)
FROM hits
GROUP BY AdvEngineID, DATE_TRUNC('minute', EventTime)
ORDER BY SUM(ResolutionWidth) DESC
LIMIT 10;
```
- **Purpose**: Test temporal grouping with expressions
- **Performance focus**: Time-based aggregation optimization

**[Q43](queries/clickbench/q43.md): Advanced-level Analytics**
```sql
SELECT CounterID,
       AVG(LENGTH(URL)) AS l,
       COUNT(*) AS c
FROM hits
WHERE URL <> ''
GROUP BY CounterID
HAVING COUNT(*) > 100000
ORDER BY l DESC
LIMIT 25;
```
- **Purpose**: Test complex analytical patterns with HAVING
- **Performance focus**: Two-phase aggregation optimization

## Usage Examples

### Basic Benchmark Setup

```python
from benchbox import ClickBench

clickbench = ClickBench(scale_factor=1.0, output_dir="clickbench_data")

data_files = clickbench.generate_data()

queries = clickbench.get_queries()
print(f"Generated {len(queries)} ClickBench queries")

count_query = clickbench.get_query("Q1")
print(count_query)
```

### Data Generation and Loading

```python
clickbench_small = ClickBench(scale_factor=0.01, output_dir="clickbench_small")
data_files = clickbench_small.generate_data()

hits_file = clickbench_small.output_dir / "hits.csv"
size_mb = hits_file.stat().st_size / (1024 * 1024)
print(f"Generated hits data: {size_mb:.2f} MB")

schema = clickbench_small.get_schema()
print(f"HITS table has {len(schema[0]['columns'])} columns")
```

> **CSV loader preserves empty strings.** `ClickBench.get_csv_loading_config`
> in `benchbox/core/clickbench/benchmark.py` configures the DuckDB CSV
> loader with `nullstr='__NULL__'` - a sentinel chosen because it will
> never appear in the generated data. The effect is that empty `|` `|`
> fields load as empty strings, not NULL. This is required for
> compatibility with the official ClickBench workload: the HITS schema
> declares columns as `NOT NULL`, and many queries (e.g. [Q11](queries/clickbench/q11.md)-[Q15](queries/clickbench/q15.md), [Q22](queries/clickbench/q22.md),
> [Q23](queries/clickbench/q23.md), [Q25](queries/clickbench/q25.md)-[Q29](queries/clickbench/q29.md), [Q31](queries/clickbench/q31.md), [Q32](queries/clickbench/q32.md), [Q37](queries/clickbench/q37.md), [Q38](queries/clickbench/q38.md)) filter empties with
> `<column> <> ''`. If you load the data via a different path (custom
> COPY, `read_csv(nullstr='')`, etc.), apply the same empty-as-empty
> policy or the result validator will diverge from the reference answers.

### DuckDB Integration Example

```python
import duckdb
from benchbox import ClickBench
import time

clickbench = ClickBench(scale_factor=0.01, output_dir="clickbench_tiny")
data_files = clickbench.generate_data()

conn = duckdb.connect("clickbench.duckdb")
schema_sql = clickbench.get_create_tables_sql()
conn.execute(schema_sql)

hits_file = clickbench.output_dir / "hits.csv"
print(f"Loading data from {hits_file}...")

load_start = time.time()
conn.execute(f"""
    INSERT INTO hits
    SELECT * FROM read_csv('{hits_file}',
                          delim='|', header=false, nullstr='__NULL__')
""")
load_time = time.time() - load_start

row_count = conn.execute("SELECT COUNT(*) FROM hits").fetchone()[0]
print(f"Loaded {row_count:,} rows in {load_time:.2f} seconds")

query_categories = {
    'basic_agg': ['Q1', 'Q2', 'Q3', 'Q4', 'Q5'],
    'grouping': ['Q8', 'Q9', 'Q10', 'Q13', 'Q16'],
    'string_ops': ['Q21', 'Q22', 'Q25', 'Q26'],
    'complex': ['Q28', 'Q31', 'Q37', 'Q43']
}

results = {}
for category, query_ids in query_categories.items():
    print(f"\\nRunning {category} queries...")
    category_results = {}

    for query_id in query_ids:
        try:
            query_sql = clickbench.get_query(query_id)

            start_time = time.time()
            result = conn.execute(query_sql).fetchall()
            execution_time = time.time() - start_time

            category_results[query_id] = {
                'time': execution_time,
                'rows': len(result)
            }
            print(f"  {query_id}: {execution_time:.3f}s ({len(result)} rows)")

        except Exception as e:
            category_results[query_id] = {
                'error': str(e)
            }
            print(f"  {query_id}: ERROR - {str(e)[:50]}...")

    results[category] = category_results

conn.close()

total_time = sum(
    r['time'] for cat_results in results.values()
    for r in cat_results.values()
    if 'time' in r
)
total_queries = sum(
    1 for cat_results in results.values()
    for r in cat_results.values()
    if 'time' in r
)
print(f"\\nSummary: {total_queries} queries executed in {total_time:.2f}s")
```

### Performance Analysis Framework

```python
import time
from typing import Dict, List
from statistics import mean, median

class ClickBenchPerformanceTester:
    def __init__(self, clickbench: ClickBench, connection):
        self.clickbench = clickbench
        self.connection = connection

    def benchmark_query_category(self, category: str, iterations: int = 3) -> Dict:
        categories = {
            'scan': ['Q1', 'Q2', 'Q7'],
            'aggregation': ['Q3', 'Q4', 'Q5', 'Q6'],
            'grouping': ['Q8', 'Q9', 'Q10', 'Q11', 'Q12'],
            'string': ['Q13', 'Q14', 'Q21', 'Q22', 'Q23'],
            'complex': ['Q28', 'Q29', 'Q30', 'Q31'],
            'analytics': ['Q37', 'Q40', 'Q41', 'Q42', 'Q43']
        }

        if category not in categories:
            raise ValueError(f"Invalid category: {category}")

        query_ids = categories[category]
        results = {}

        for query_id in query_ids:
            print(f"Benchmarking {query_id} ({category})...")

            times = []
            for iteration in range(iterations):
                query_sql = self.clickbench.get_query(query_id)

                start_time = time.time()
                try:
                    result = self.connection.execute(query_sql).fetchall()
                    execution_time = time.time() - start_time
                    times.append(execution_time)
                except Exception as e:
                    print(f"  Error in iteration {iteration + 1}: {e}")
                    continue

            if times:
                results[query_id] = {
                    'category': category,
                    'avg_time': mean(times),
                    'median_time': median(times),
                    'min_time': min(times),
                    'max_time': max(times),
                    'rows_returned': len(result) if 'result' in locals() else 0,
                    'times': times,
                    'success_rate': len(times) / iterations
                }
                print(f"  Average: {mean(times):.3f}s, Median: {median(times):.3f}s")
            else:
                results[query_id] = {
                    'category': category,
                    'error': 'All iterations failed'
                }

        return results

    def run_complete_benchmark(self) -> Dict:
        complete_results = {}

        categories = ['scan', 'aggregation', 'grouping', 'string', 'complex', 'analytics']
        for category in categories:
            print(f"\\nRunning {category.upper()} queries...")
            try:
                category_results = self.benchmark_query_category(category)
                complete_results[category] = category_results
            except Exception as e:
                print(f"Error in {category} category: {e}")
                complete_results[category] = {'error': str(e)}

        all_times = []
        successful_queries = 0
        total_queries = 0

        for category_data in complete_results.values():
            if isinstance(category_data, dict) and 'error' not in category_data:
                for query_data in category_data.values():
                    total_queries += 1
                    if isinstance(query_data, dict) and 'times' in query_data:
                        all_times.extend(query_data['times'])
                        successful_queries += 1

        if all_times:
            complete_results['summary'] = {
                'total_queries': total_queries,
                'successful_queries': successful_queries,
                'success_rate': successful_queries / total_queries,
                'total_avg_time': mean(all_times),
                'total_median_time': median(all_times),
                'total_min_time': min(all_times),
                'total_max_time': max(all_times),
                'geomean_time': self._geometric_mean(all_times)
            }

        return complete_results

    def _geometric_mean(self, values: List[float]) -> float:
        if not values:
            return 0.0

        product = 1.0
        for value in values:
            product *= value

        return product ** (1.0 / len(values))

    def analyze_column_performance(self) -> Dict:
        column_tests = [
            ('integer_scan', 'SELECT COUNT(*) FROM hits WHERE RegionID > 1000'),
            ('string_scan', "SELECT COUNT(*) FROM hits WHERE SearchPhrase LIKE '%google%'"),
            ('timestamp_scan', "SELECT COUNT(*) FROM hits WHERE EventTime > '2013-07-01'"),
            ('integer_agg', 'SELECT RegionID, COUNT(*) FROM hits GROUP BY RegionID LIMIT 10'),
            ('string_agg', "SELECT SearchPhrase, COUNT(*) FROM hits WHERE SearchPhrase <> ''GROUP BY SearchPhrase LIMIT 10"),
            ('mixed_agg', "SELECT RegionID, SearchPhrase, COUNT(*) FROM hits WHERE SearchPhrase <> ''GROUP BY RegionID, SearchPhrase LIMIT 10")
        ]

        results = {}

        for test_name, query_sql in column_tests:
            print(f"Testing {test_name}...")

            times = []
            for iteration in range(3):
                try:
                    start_time = time.time()
                    result = self.connection.execute(query_sql).fetchall()
                    execution_time = time.time() - start_time
                    times.append(execution_time)
                except Exception as e:
                    print(f"  Error: {e}")
                    break

            if times:
                results[test_name] = {
                    'avg_time': mean(times),
                    'rows_returned': len(result) if 'result' in locals() else 0,
                    'times': times
                }
            else:
                results[test_name] = {'error': 'Failed to execute'}

        return results

performance_tester = ClickBenchPerformanceTester(clickbench, conn)

scan_results = performance_tester.benchmark_query_category('scan')
grouping_results = performance_tester.benchmark_query_category('grouping')

print("\\nCategory Performance Summary:")
print(f"Scan Queries: {scan_results}")
print(f"Grouping Queries: {grouping_results}")

complete_results = performance_tester.run_complete_benchmark()
print(f"\\nComplete Benchmark Summary: {complete_results.get('summary', 'No summary available')}")

column_results = performance_tester.analyze_column_performance()
print(f"\\nColumn Performance Analysis: {column_results}")
```

### ClickHouse Native Integration

This example creates the ClickHouse table with settings suited to performance, loads the data with ClickHouse CSV
import, runs all 43 ClickBench queries, and then calculates performance statistics.

```python
import clickhouse_connect
from benchbox import ClickBench

client = clickhouse_connect.get_client(host='localhost', port=8123)
clickbench = ClickBench(scale_factor=1.0, output_dir="clickbench_data")

data_files = clickbench.generate_data()

create_table_sql = """
CREATE TABLE hits (
    WatchID UInt64,
    JavaEnable UInt8,
    Title String,
    GoodEvent UInt8,
    EventTime DateTime,
    EventDate Date,
    CounterID UInt32,
    ClientIP UInt32,
    RegionID UInt32,
    UserID UInt64,
    CounterClass UInt8,
    OS UInt8,
    UserAgent UInt8,
    URL String,
    Referer String,
    IsRefresh UInt8,
    RefererCategoryID UInt16,
    RefererRegionID UInt32,
    URLCategoryID UInt16,
    URLRegionID UInt32,
    ResolutionWidth UInt16,
    ResolutionHeight UInt16,
    ResolutionDepth UInt8,
    FlashMajor UInt8,
    FlashMinor UInt8,
    FlashMinor2 String,
    NetMajor UInt8,
    NetMinor UInt8,
    UserAgentMajor UInt16,
    UserAgentMinor String,
    CookieEnable UInt8,
    JavascriptEnable UInt8,
    IsMobile UInt8,
    MobilePhone UInt8,
    MobilePhoneModel String,
    Params String,
    IPNetworkID UInt32,
    TraficSourceID Int8,
    SearchEngineID UInt16,
    SearchPhrase String,
    AdvEngineID UInt8,
    IsArtifical UInt8,
    WindowClientWidth UInt16,
    WindowClientHeight UInt16,
    ClientTimeZone Int16,
    ClientEventTime DateTime,
    SilverlightVersion1 UInt8,
    SilverlightVersion2 UInt8,
    SilverlightVersion3 UInt32,
    SilverlightVersion4 UInt16,
    PageCharset String,
    CodeVersion UInt32,
    IsLink UInt8,
    IsDownload UInt8,
    IsNotBounce UInt8,
    FUniqID UInt64,
    OriginalURL String,
    HID UInt32,
    IsOldCounter UInt8,
    IsEvent UInt8,
    IsParameter UInt8,
    DontCountHits UInt8,
    WithHash UInt8,
    HitColor FixedString(1),
    LocalEventTime DateTime,
    Age UInt8,
    Sex UInt8,
    Income UInt8,
    Interests UInt16,
    Robotness UInt8,
    RemoteIP UInt32,
    WindowName Int32,
    OpenerName Int32,
    HistoryLength Int16,
    BrowserLanguage FixedString(2),
    BrowserCountry FixedString(2),
    SocialNetwork String,
    SocialAction String,
    HTTPError UInt8,
    SendTiming UInt32,
    DNSTiming UInt32,
    ConnectTiming UInt32,
    ResponseStartTiming UInt32,
    ResponseEndTiming UInt32,
    FetchTiming UInt32,
    SocialSourceNetworkID UInt8,
    SocialSourcePage String,
    ParamPrice Int64,
    ParamOrderID String,
    ParamCurrency FixedString(3),
    ParamCurrencyID UInt16,
    OpenstatServiceName String,
    OpenstatCampaignID String,
    OpenstatAdID String,
    OpenstatSourceID String,
    UTMSource String,
    UTMMedium String,
    UTMCampaign String,
    UTMContent String,
    UTMTerm String,
    FromTag String,
    HasGCLID UInt8,
    RefererHash UInt64,
    URLHash UInt64,
    CLID UInt32
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(EventDate)
ORDER BY (CounterID, EventDate, intHash32(UserID))
SAMPLE BY intHash32(UserID);
"""

client.execute(create_table_sql)

hits_file = clickbench.output_dir / "hits.csv"
with open(hits_file, 'rb') as f:
    client.insert_file('hits', f, fmt='CSV')

print("Running all 43 ClickBench queries on ClickHouse...")

query_results = {}
for i in range(1, 44):
    query_id = f"Q{i}"

    try:
        query_sql = clickbench.get_query(query_id)

        start_time = time.time()
        result = client.query(query_sql)
        execution_time = time.time() - start_time

        query_results[query_id] = {
            'time': execution_time,
            'rows': len(result.result_rows)
        }

        print(f"{query_id}: {execution_time:.3f}s ({len(result.result_rows)} rows)")

    except Exception as e:
        query_results[query_id] = {'error': str(e)}
        print(f"{query_id}: ERROR - {str(e)[:50]}...")

successful_queries = [r for r in query_results.values() if 'time' in r]
if successful_queries:
    total_time = sum(r['time'] for r in successful_queries)
    avg_time = total_time / len(successful_queries)

    print(f"\\nClickBench Results Summary:")
    print(f"Successful queries: {len(successful_queries)}/43")
    print(f"Total execution time: {total_time:.2f}s")
    print(f"Average query time: {avg_time:.3f}s")
```

## Performance Characteristics

### Query Performance Patterns

**Scan Queries ([Q1](queries/clickbench/q1.md), [Q2](queries/clickbench/q2.md), [Q7](queries/clickbench/q7.md)):**
- **Primary bottleneck**: Sequential scan speed and basic filtering
- **Optimization targets**: Column scanning, SIMD operations, predicate pushdown
- **Typical performance**: Milliseconds to seconds
- **Scaling characteristics**: Linear with data size

**Aggregation Queries ([Q3](queries/clickbench/q3.md)-[Q6](queries/clickbench/q6.md)):**
- **Primary bottleneck**: Aggregation function computation
- **Optimization targets**: Vectorized aggregation, parallel execution
- **Typical performance**: Seconds for large datasets
- **Scaling characteristics**: Sub-linear with good optimization

**Grouping Queries ([Q8](queries/clickbench/q8.md)-[Q19](queries/clickbench/q19.md)):**
- **Primary bottleneck**: Hash table construction and group management
- **Optimization targets**: Hash aggregation algorithms, memory efficiency
- **Typical performance**: Seconds to tens of seconds
- **Scaling characteristics**: Depends on cardinality and memory

**String Operations ([Q20](queries/clickbench/q20.md)-[Q29](queries/clickbench/q29.md)):**
- **Primary bottleneck**: String processing and pattern matching
- **Optimization targets**: String function optimization, regex engines
- **Typical performance**: Highly variable, can be slow
- **Scaling characteristics**: Often super-linear for complex operations

**Complex Analytics ([Q30](queries/clickbench/q30.md)-[Q43](queries/clickbench/q43.md)):**
- **Primary bottleneck**: Multiple complex operations combined
- **Optimization targets**: Query plan optimization, vectorization
- **Typical performance**: Minutes for large datasets
- **Scaling characteristics**: Highly dependent on optimization quality

### System Optimization Opportunities

| System Type | Scan Performance | Aggregation | Grouping | String Ops | Complex Analytics |
|-------------|------------------|-------------|----------|------------|------------------|
| **Columnar** | Column scanning | Vectorized ops | Column-wise hash | String columns | Vector processing |
| **Row-based** | Index usage | Row aggregation | Row-wise groups | String indexes | Traditional optimization |
| **In-Memory** | Memory bandwidth | Fast aggregation | In-memory hash | String caching | Memory-configured | <!-- content-ok: cliche -->
| **GPU** | Parallel scanning | GPU aggregation | GPU hash tables | Limited support | Mixed CPU/GPU |

## Configuration Options

### Scale Factor Guidelines

| Scale Factor | Rows | Data Size | Query Times | Use Case |
|-------------|------|-----------|-------------|----------|
| 0.001 | ~100K | ~10 MB | Milliseconds | Development |
| 0.01 | ~1M | ~100 MB | Seconds | Testing |
| 0.1 | ~10M | ~1 GB | 10s-60s | Integration |
| 1.0 | ~100M | ~10 GB | Minutes | Standard benchmark |
| 10.0 | ~1B | ~100 GB | Hours | Large-scale testing |

### Advanced-level Configuration

Generation is controlled by `scale_factor` and `output_dir`. Date ranges, user counts, partitioning, and indices are
fixed by the workload definition, not constructor options:

```python
clickbench = ClickBench(
    scale_factor=1.0,
    output_dir="clickbench_data",
)
```

## Best Practices

### Data Generation
1. **Scale appropriately** - Use realistic scale factors for your testing needs
2. **Consider compression** - ClickBench data compresses well
3. **Partition strategy** - Partition by date for temporal queries
4. **Index planning** - Create indices on frequently filtered columns

### Query Optimization
1. **Column store optimization** - Use columnar storage for scans
2. **String optimization** - Optimize string operations and pattern matching
3. **Memory management** - Monitor memory usage for large aggregations
4. **Parallel execution** - Use parallel query execution where available

### Performance Testing
1. **Multiple iterations** - Run queries multiple times for stable timings
2. **Cold vs. warm runs** - Test both cold and warm query performance
3. **Resource monitoring** - Monitor CPU, memory, and I/O during execution
4. **Cross-system comparison** - Use ClickBench for database comparisons

## Common Issues and Solutions

### Performance Issues

**Issue: Slow string operations**

Solution: create appropriate indices and use configured string functions. The last statement shows a
database-specific string optimization: `ILIKE` is a case-insensitive match.
```sql
CREATE INDEX idx_hits_search_phrase ON hits(SearchPhrase);
CREATE INDEX idx_hits_url ON hits USING hash(URL);

SELECT COUNT(*) FROM hits WHERE SearchPhrase ILIKE '%google%';
```

**Issue: Memory exhaustion on complex queries**

Solution: optimize memory usage and use incremental processing. `max_memory_usage` sets a 10GB limit, and
`max_bytes_before_external_group_by` enables external aggregation (spilling to disk) above 5GB. You can also break
down complex queries, as the final example does.
```sql
SET max_memory_usage = 10000000000;
SET max_bytes_before_external_group_by = 5000000000;

SELECT * FROM (
    SELECT RegionID, COUNT(*) as cnt FROM hits GROUP BY RegionID
) WHERE cnt > 1000;
```

### Data Loading Issues

**Issue: Slow data loading**

Solution: load with a single `COPY`/`INSERT ... SELECT` per file instead of row-by-row inserts, and generate once at the target scale rather than regenerating per run.
```python
clickbench = ClickBench(
    scale_factor=1.0,
    output_dir="clickbench_data",
)
```

**Issue: Schema mismatches across databases**

Solution: use database-specific schema adaptations. The first statement is for PostgreSQL, the second for MySQL and
the third for DuckDB.
```sql
ALTER TABLE hits ALTER COLUMN EventTime TYPE TIMESTAMP;

ALTER TABLE hits MODIFY COLUMN Title TEXT CHARACTER SET utf8mb4;

ALTER TABLE hits ALTER COLUMN ResolutionWidth TYPE USMALLINT;
```

## See Also

### Related Benchmarks

- **[TPC-H Benchmark](tpc-h.md)** - Standard decision support benchmark
- **[TPC-DS Benchmark](tpc-ds.md)** - Complex analytical workloads
- **[H2O.ai Benchmark](h2odb.md)** - Data science focused analytics
- **[AMPLab Benchmark](amplab.md)** - Big data processing benchmark
- **[Star Schema Benchmark (SSB)](ssb.md)** - OLAP focused testing
- **[Join Order Benchmark](join-order.md)** - Real-world IMDB join testing
- **[Benchmark Catalog](index.md)** - Complete benchmark list

### Understanding BenchBox

- **[Architecture Overview](../concepts/architecture.md)** - Component design and data flow
- **[Workflow Patterns](../concepts/workflow.md)** - Benchmarking workflow examples
- **[Data Model](../concepts/data-model.md)** - Result schema documentation
- **[Glossary](../concepts/glossary.md)** - Benchmark terminology reference

### Getting Started

- **[Getting Started Guide](../usage/getting-started.md)** - Run your first benchmark in 5 minutes
- **[CLI Quick Reference](../usage/cli-quick-start.md)** - Command-line usage
- **[Configuration Handbook](../usage/configuration.md)** - Advanced configuration
- **[Examples](../usage/examples.md)** - Code snippets and patterns

### Platform-Specific Guides

- **[Platform Selection Guide](../platforms/platform-selection-guide.md)** - Choosing the right database
- **[ClickHouse Local Mode](../platforms/clickhouse-local-mode.md)** - Running ClickHouse locally
- **[Platform Quick Reference](../platforms/quick-reference.md)** - Platform setup guides
- **[Performance Tuning](../advanced/performance.md)** - Optimization strategies

### External Resources

- **[ClickBench GitHub](https://github.com/ClickHouse/ClickBench)** - Official benchmark repository
- **[ClickBench.com Results](https://benchmark.clickhouse.com/)** - Cross-database performance comparisons
- **[ClickBench Methodology](https://github.com/ClickHouse/ClickBench)** - Detailed methodology
- **[DuckDB Labs Benchmark](https://duckdblabs.github.io/db-benchmark/)** - Updated benchmark results
