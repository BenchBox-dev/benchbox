<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Benchmarks

```{tags} concept
```

Use this catalog to choose a workload, understand its data and query shape,
and estimate the resources a run needs. BenchBox includes standards-based,
academic, industry, real-world, primitive, and experimental workloads.

List the benchmark names available in your installed version with:

```bash
uv run -- benchbox benchmarks list
```

Start with TPC-H for a general analytical comparison. Choose a focused
primitive workload when you need to isolate one operation, or a domain-specific
benchmark when its schema and query patterns match your system.

Benchmark support status is sourced from `benchbox.core.benchmark_registry`, not
from hand-maintained docs. Current status counts are documented and checked in
the [public contract map](../reference/public-contracts.md). The auditable
per-benchmark rationale and promotion criteria live in
[Benchmark Support Status Criteria](support-status.md). `support_status` is
separate from public discovery visibility and from DataFrame capability.

## Contents

### Roadmap & Research

#### [Development Roadmap](../development/roadmap.md)
Planned platform and benchmark additions, including DataFrame support expansion and upcoming workloads.

### TPC Standards
*Official industry standards for comparing databases*

#### [TPC-H](tpc-h.md)
**TPC-H Decision Support Benchmark**
- **CLI name**: `tpch`
- **Purpose**: Standard decision support and analytical query performance
- **Queries**: 22 business-oriented analytical queries
- **Schema**: 8 tables modeling a wholesale supplier database
- **Scale**: 0.001 to 1000+ scale factors supported
- **Use Cases**: General analytical performance, database comparison, regression testing

#### [TPC-DS](tpc-ds.md)
**TPC-DS Decision Support Benchmark**
- **CLI name**: `tpcds`
- **Purpose**: Complex analytical workloads with advanced SQL features
- **Queries**: 99 complex analytical queries with CTEs, window functions
- **Schema**: 24 tables modeling a retail data warehouse
- **Scale**: 0.001 to 100+ scale factors supported
- **Use Cases**: Advanced-level analytics, complex query optimization, modern SQL feature testing

#### [TPC-DI](tpc-di.md)
**TPC-DI Data Integration Benchmark**
- **CLI name**: `tpcdi`
- **Purpose**: Data integration and ETL performance testing
- **Queries**: Data transformation and loading operations
- **Schema**: Dimensional data warehouse with fact and dimension tables
- **Scale**: Variable based on data volume requirements
- **Use Cases**: ETL performance, data pipeline testing, integration workflows

### Data Modeling Variants
*Alternative schema designs derived from TPC standards*

#### [TPC-H Data Vault](datavault.md)
**TPC-H Data Vault Benchmark**
- **CLI name**: `datavault`
- **Purpose**: TPC-H adapted for Data Vault 2.0 modeling pattern performance testing
- **Queries**: 22 TPC-H queries adapted for Hub-Link-Satellite joins
- **Schema**: 21 tables (7 Hubs, 6 Links, 8 Satellites) from TPC-H source
- **Scale**: Same as TPC-H (0.01 to 100+)
- **Use Cases**: Data Vault architecture evaluation, hub-link-satellite join performance, enterprise DWH patterns

### Academic Benchmarks
*Research benchmarks from academia*

#### [Star Schema Benchmark (SSB)](ssb.md)
**Star Schema Benchmark**
- **CLI name**: `ssb`
- **Purpose**: Star schema analytical query performance
- **Queries**: 13 queries across 4 query flights
- **Schema**: Classic star schema with fact and dimension tables
- **Scale**: 1 to 1000+ scale factors supported
- **Use Cases**: OLAP performance, dimensional modeling validation, columnar database testing

#### [AMPLab](amplab.md)
**AMPLab Big Data Benchmark**
- **CLI name**: `amplab`
- **Purpose**: Big data processing and analytics performance
- **Queries**: 8 web analytics and user behavior queries
- **Schema**: Web analytics data model
- **Scale**: Large-scale data processing focused
- **Use Cases**: Big data performance, distributed computing, web analytics

#### [Join Order Benchmark](join-order.md)
**Join Order Optimization Benchmark**
- **CLI name**: `joinorder`
- **Purpose**: Query optimizer join order evaluation
- **Queries**: 113 complex multi-table join queries
- **Schema**: 21 tables modeling IMDB movie database
- **Scale**: Fixed at `--scale 1` using canonical IMDb 2013 JOB data
- **Use Cases**: Query optimizer testing, join algorithm evaluation, cardinality estimation

### Industry Benchmarks
*Real-world benchmarks from practitioners*

#### [ClickBench](clickbench.md)
**ClickHouse Analytical Benchmark**
- **CLI name**: `clickbench`
- **Purpose**: High-performance analytical query testing
- **Queries**: 43 analytical queries from real web analytics
- **Schema**: Web analytics event data
- **Scale**: High-volume analytical workloads
- **Use Cases**: Analytical database performance, real-time analytics, columnar storage

#### [H2ODB](h2odb.md)
**H2O Database Benchmark**
- **CLI name**: `h2odb`
- **Purpose**: Data science and machine learning workloads
- **Queries**: 10 aggregation and groupby operations
- **Schema**: Data science oriented tables
- **Scale**: Memory and computation intensive
- **Use Cases**: Data science performance, in-memory analytics, aggregation optimization

#### [CoffeeShop](coffeeshop.md)
**CoffeeShop Point-of-Sale Benchmark**
- **CLI name**: `coffeeshop`
- **Purpose**: Real-world transactional and analytical workloads
- **Queries**: 11 point-of-sale and reporting queries
- **Schema**: Retail transaction data model
- **Scale**: Configurable based on business volume
- **Use Cases**: Hybrid workload testing, real-world performance validation

### Real-World Data
*Benchmarks built on public real-world datasets*

#### [NYC Taxi](nyctaxi.md)
**NYC Taxi OLAP Benchmark**
- **CLI name**: `nyctaxi`
- **Purpose**: Real-world transportation data analytics
- **Queries**: 25 OLAP queries across 9 categories
- **Schema**: NYC TLC trip records with taxi zones dimension
- **Scale**: From ~96K trips (SF=0.01) to ~96M trips (SF=10 ceiling)
- **Use Cases**: Geospatial analytics, temporal aggregation, multi-dimensional OLAP

#### [Flight Data](flightdata.md)
**US Aviation On-Time Performance Benchmark**
- **CLI name**: `flightdata`
- **Purpose**: Real-world aviation analytics - delays, routes, carriers, seasonality
- **Queries**: 20 OLAP queries across 5 categories (ontime, delay, routes, temporal, carriers)
- **Schema**: BTS On-Time Performance records
- **Scale**: From 1-month sample (SF=0.01, ~10MB) to full corpus (SF≥10, ~10GB+)
- **Use Cases**: Temporal analytics, categorical aggregation, carrier comparisons

### Time-Series Benchmarks
*Workloads built for time-series databases and columnar monitoring engines*

#### [TSBS DevOps](tsbs-devops.md)
**Time Series Benchmark Suite for DevOps**
- **CLI name**: `tsbs_devops`
- **Purpose**: Infrastructure monitoring and observability workloads
- **Queries**: 18 DevOps monitoring queries across 10 categories
- **Schema**: CPU, memory, disk, network metrics with host tags
- **Scale**: From 10 hosts to thousands, configurable duration
- **Use Cases**: Time-series database evaluation, monitoring dashboards, alerting queries

### BenchBox Primitives
*Fundamental database operation testing*

#### [Read Primitives](read-primitives.md)
**Database Read Primitives Benchmark**
- **CLI name**: `read_primitives`
- **Purpose**: Individual database operation testing
- **Queries**: 157 primitive operation queries across 31 categories
- **Schema**: TPC-H schema for familiar data patterns
- **Scale**: Lightweight focused testing
- **Use Cases**: Unit testing, regression detection, optimizer sniff tests, development validation

#### [Write Primitives](write-primitives.md)
**Database Write Operations Benchmark (v2)**
- **CLI name**: `write_primitives`
- **Purpose**: Comprehensive non-transactional write operation testing
- **Queries**: 12 write operations across 7 categories (INSERT, UPDATE, DELETE, BULK_LOAD, MERGE, DDL, TRANSACTION)
- **Schema**: TPC-H schema with 16 category-based staging tables
- **Scale**: Configurable via scale factors
- **Platform Support**: Broad (ClickHouse, BigQuery, Snowflake, Databricks, Redshift, DuckDB, SQLite)
- **Use Cases**: Write performance testing, MERGE/UPSERT optimization, CDC workloads, ETL validation

#### [Transaction Primitives](transaction-primitives.md)
**Database Transaction Testing Benchmark**
- **CLI name**: `transaction_primitives`
- **Purpose**: ACID transaction guarantees and isolation level testing
- **Queries**: 12 transaction operations (COMMIT, ROLLBACK, SAVEPOINT, isolation levels)
- **Schema**: TPC-H schema with transaction staging tables
- **Scale**: Configurable via scale factors
- **Platform Support**: Designed for ACID databases (PostgreSQL, MySQL, SQL Server - adapters planned; currently limited support via DuckDB/SQLite)
- **Use Cases**: Transaction isolation testing, ACID compliance validation, concurrency testing

#### [Metadata Primitives](metadata-primitives.md)
**Database Catalog Introspection Benchmark**
- **CLI name**: `metadata_primitives`
- **Purpose**: Catalog introspection performance via INFORMATION_SCHEMA, SHOW, DESCRIBE, PRAGMA
- **Queries**: 62 catalog queries across 10 categories
- **Schema**: Database catalog (no data generation required)
- **Scale**: Configurable via wide tables and view hierarchies
- **Use Cases**: Data catalog integration, BI tool responsiveness, IDE autocomplete, data governance

#### [AI Primitives](ai-primitives.md)
**SQL-Based AI Functions Benchmark**
- **CLI name**: `ai_primitives`
- **Purpose**: Evaluate built-in AI functions in cloud databases
- **Queries**: 16 queries across 4 categories (generative, nlp, transform, embedding)
- **Schema**: TPC-H (uses customer/supplier comments as realistic text)
- **Platform Support**: Snowflake Cortex, BigQuery ML, Databricks AI
- **Cost Control**: Built-in budget enforcement
- **Use Cases**: Platform comparison for AI-native analytics, cost modeling
- **Registry category**: `Primitives` (grouped with the other `*_primitives` benchmarks)

### AI & ML Benchmarks
*Vector similarity and other AI-shaped analytical workloads*

#### [Vector Search](vector-search.md)
**Vector Similarity Search Benchmark**
- **CLI name**: `vector_search`
- **Purpose**: Exact and approximate similarity search over embedding vectors
- **Queries**: 6 queries - exact kNN (cosine + L2), filtered kNN, ground truth, ANN, multi-category
- **Schema**: Synthetic embeddings, 128-dim default (configurable)
- **Platform Support**: DuckDB (primary), pgvector, ClickHouse, Snowflake, StarRocks, Doris
- **Use Cases**: Vector database evaluation, ANN recall testing, embedding store comparison

### BenchBox Experimental
*Experimental benchmarks for specialized testing*

See also [TPC-H Data Vault](datavault.md) under *Data Modeling Variants* above -
shipped in the experimental toctree but documented alongside the other TPC-H
schema variants.

#### [TPC-DS-OBT](tpc-ds-obt.md)
**TPC-DS One Big Table Benchmark**
- **CLI name**: `tpcds_obt`
- **Purpose**: Denormalized wide-table analytical workloads
- **Queries**: 17 TPC-DS queries adapted for single-table schema
- **Schema**: Single denormalized "One Big Table" with all TPC-DS attributes
- **Scale**: Same as TPC-DS (1.0 to 100+)
- **Use Cases**: Wide-table analytics, columnar database testing, denormalization trade-off analysis

#### [TPC-Havoc](tpc-havoc.md)
**TPC-H Syntax Variants Benchmark**
- **CLI name**: `tpchavoc`
- **Purpose**: Query optimizer stress testing through syntax variations
- **Queries**: 220 variants (22 TPC-H queries × 10 syntax variants each)
- **Schema**: TPC-H schema with identical data
- **Scale**: Same as TPC-H but focused on optimization differences
- **Use Cases**: Optimizer robustness testing, syntax transformation analysis, performance variance evaluation

#### [TPC-H Skew](tpch-skew.md)
**TPC-H Data Skew Benchmark**
- **CLI name**: `tpch_skew`
- **Purpose**: Test optimizer behavior on non-uniform data distributions
- **Queries**: 22 TPC-H queries with configurable skew parameters
- **Schema**: TPC-H schema with Zipfian, normal, or exponential data distributions
- **Scale**: Same as TPC-H (0.01 to 10+)
- **Use Cases**: Skew handling, cardinality estimation testing, join strategy evaluation under data imbalance

##  Benchmark Selection Guide

### By Use Case

#### **General Performance Testing**
- **Primary**: [TPC-H](tpc-h.md) - Industry standard, widely comparable
- **Secondary**: [SSB](ssb.md) - Simpler star schema alternative
- **Advanced**: [TPC-DS](tpc-ds.md) - Complex modern analytical workloads
- **Wide Tables**: [TPC-DS-OBT](tpc-ds-obt.md) - Denormalized single-table analytics
- **Enterprise DWH**: [Data Vault](datavault.md) - Hub-Link-Satellite modeling patterns

#### **Query Optimizer Testing**
- **Join Optimization**: [Join Order Benchmark](join-order.md)
- **General Optimization**: [Read Primitives](read-primitives.md) optimizer tests
- **Syntax Robustness**: [TPC-Havoc](tpc-havoc.md)
- **Skew Handling**: [TPC-H Skew](tpch-skew.md) - Non-uniform data distributions

#### **Specific Operation Testing**
- **Read Operations**: [Read Primitives](read-primitives.md)
- **Write Operations**: [Write Primitives](write-primitives.md)
- **Transaction Operations**: [Transaction Primitives](transaction-primitives.md)
- **Analytical Operations**: [ClickBench](clickbench.md)

```{toctree}
:maxdepth: 2

tpc-standards
academic-benchmarks
industry-benchmarks
real-world-benchmarks
time-series-benchmarks
benchbox-primitives
ai-ml-benchmarks
benchbox-experimental
support-status
queries/index
```

#### **Development and CI/CD**
- **Unit Testing**: [Read Primitives](read-primitives.md) with micro scale factors
- **Regression Testing**: [TPC-H](tpc-h.md) with small scale factors
- **Feature Validation**: Category-specific [Read Primitives](read-primitives.md) queries

#### **Research and Analysis**
- **Optimizer Research**: [Join Order Benchmark](join-order.md)
- **Performance Analysis**: [TPC-DS](tpc-ds.md) complex queries
- **Syntax Impact**: [TPC-Havoc](tpc-havoc.md) variants

### By Database Type

#### **OLTP Databases**
- [TPC-H](tpc-h.md) queries 1, 6, 12 (simple aggregations)
- [Read Primitives](read-primitives.md) focused read operations
- [Write Primitives](write-primitives.md) for UPSERT and write workloads
- [Transaction Primitives](transaction-primitives.md) for ACID compliance testing

#### **OLAP/Analytical Databases**
- [TPC-DS](tpc-ds.md) for complex analytics
- [ClickBench](clickbench.md) for high-performance analytics
- [SSB](ssb.md) for dimensional modeling

#### **Distributed/Big Data Systems**
- [AMPLab](amplab.md) for big data workloads
- [TPC-DS](tpc-ds.md) for complex distributed queries
- [H2ODB](h2odb.md) for data science workloads

#### **In-Memory Databases**
- [H2ODB](h2odb.md) for memory-intensive operations
- [Read Primitives](read-primitives.md) for focused operation testing
- [TPC-H](tpc-h.md) with larger scale factors

### By Performance Characteristics

#### **CPU-Intensive Workloads**
- [TPC-DS](tpc-ds.md) complex queries (Q67, Q78, Q98)
- [Join Order Benchmark](join-order.md) multi-table joins
- [Read Primitives](read-primitives.md) aggregation category

#### **I/O-Intensive Workloads**
- [TPC-H](tpc-h.md) with large scale factors
- [ClickBench](clickbench.md) analytical scans
- [AMPLab](amplab.md) big data operations

#### **Memory-Intensive Workloads**
- [H2ODB](h2odb.md) groupby operations
- [TPC-DS](tpc-ds.md) with window functions
- Canonical [Join Order Benchmark](join-order.md) at fixed `--scale 1`

## Common Patterns

### Quick Performance Test
Run a representative subset of TPC-H queries for standard analytical performance, and use Read Primitives for focused operation testing:
```python
from benchbox import TPCH, ReadPrimitives

tpch = TPCH(scale_factor=0.1)
key_queries = [1, 3, 6, 12]

read_primitives = ReadPrimitives(scale_factor=0.01)
perf_critical = read_primitives.get_queries_by_category("aggregation")
```

### Comprehensive Evaluation
```python
from benchbox import TPCH, TPCDS, JoinOrder

benchmarks = {
    "tpch": TPCH(scale_factor=1.0),
    "tpcds": TPCDS(scale_factor=1.0),
    "joinorder": JoinOrder(scale_factor=1.0),
}

for name, benchmark in benchmarks.items():
    queries = benchmark.get_queries()
    print(f"{name}: {len(queries)} queries")
```

### Regression Testing
Use a small scale factor for fast execution, and allow 10% variance against the baseline:
```python
from benchbox import ReadPrimitives

read_primitives = ReadPrimitives(scale_factor=0.01)
regression_queries = [
    "aggregation_distinct",
    "broadcast_join_two_tables",
    "filter_selective",
    "limit",
]

for query_id in regression_queries:
    query_sql = read_primitives.get_query(query_id)
    print(f"{query_id}: {len(query_sql)} characters")
```

## Resource Characteristics

### Query Complexity by Benchmark

| Benchmark  | Simple Queries     | Complex Queries        | Total Queries |
| ---------- | ------------------ | ---------------------- | ------------- |
| TPC-H      | Q1, Q6, Q14        | Q2, Q9, Q21            | 22            |
| TPC-DS     | Q1, Q6, Q11        | Q14, Q67, Q78          | 99            |
| SSB        | Q1.1-Q1.3          | Q4.1-Q4.3              | 13            |
| Join Order | 2-3 table joins    | 6+ table joins         | 113           |
| Primitives | Single-table scans | Complex aggregations | 157           |
| ClickBench | Simple scans       | Multi-column analytics | 43            |

**Note:** Execution times vary significantly based on platform, hardware, and configuration. Run benchmarks to establish baselines for your environment.

### Memory Requirements (Scale Factor 1.0)

| Benchmark  | Data Size | Working Memory | Peak Memory |
| ---------- | --------- | -------------- | ----------- |
| TPC-H      | ~1 GB     | ~2 GB          | ~4 GB       |
| TPC-DS     | ~3 GB     | ~6 GB          | ~12 GB      |
| SSB        | ~600 MB   | ~1 GB          | ~2 GB       |
| Join Order | 74M rows  | engine-specific | engine-specific |
| Primitives | ~1 GB     | ~2 GB          | ~4 GB       |

## Integration Examples

Each benchmark documentation includes:
- **Setup instructions** for popular databases
- **Performance tuning** recommendations
- **Common pitfalls** and troubleshooting
- **Integration patterns** for different use cases
- **Expected results** and validation guidelines

## Getting Help

### Benchmark-Specific Issues
- Check individual benchmark documentation for known issues
- Review database-specific integration examples
- Validate scale factor and configuration settings

### General Support
- [Usage Guide](../usage/README.md) for general BenchBox usage
- [API Reference](../reference/api-reference.md) for detailed API documentation
- [GitHub Issues](https://github.com/BenchBox-dev/benchbox/issues) for bug reports
- [GitHub Discussions](https://github.com/BenchBox-dev/benchbox/discussions) for questions

---

*Choose the right benchmark for your specific testing needs. For general performance evaluation, start with TPC-H. For focused testing, use Primitives. For optimizer research, explore Join Order Benchmark.*
