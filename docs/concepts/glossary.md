<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Glossary

```{tags} beginner, reference
```

Comprehensive reference for benchmarking, database, and BenchBox-specific terminology.

## A

**Adapter**
: Platform-specific implementation that handles connection management, data loading, and query execution for a particular database system. See [Platform Selection Guide](../platforms/platform-selection-guide.md).

**AMPLab Big Data Benchmark**
: Benchmark comparing performance of analytical SQL engines using big data processing frameworks. Focuses on scan, aggregation, and join operations.

**Analytical Database**
: Database system optimized for OLAP workloads, supporting complex queries over large datasets with aggregations, joins, and analytics. Examples: DuckDB, ClickHouse, Snowflake.

## B

**Benchmark**
: Standardized workload used to measure and compare database performance. BenchBox supports TPC-H, TPC-DS, TPC-DI, SSB, ClickBench, and others.

**BigQuery**
: Google Cloud's fully managed, serverless data warehouse for analytics. Supported via `BigQueryAdapter`.

**Bulk Loading**
: Optimized method for loading large amounts of data into a database, typically faster than row-by-row insertion. Uses platform-specific features like `COPY FROM` or cloud storage staging.

## C

**ClickBench**
: Real-world analytics benchmark based on anonymized web analytics data. Tests aggregation performance across 43 queries.

**ClickHouse**
: Open-source column-oriented database management system optimized for OLAP workloads. Supported via `ClickHouseAdapter`.

**CoffeeShop Benchmark**
: Minimal example benchmark in BenchBox for quick testing and demonstration. Uses tiny dataset with simple queries.

**Column Store**
: Database storage architecture that stores data by columns rather than rows, optimizing for analytical queries that read many rows but few columns.

**Composite Score**
: Single metric combining results from multiple benchmark tests. TPC-H QphH@Size is exported only when its official-run and refresh-evidence gates pass; TPC-DS QphDS@Size is not exported. See those entries.

**Concurrent Streams**
: Multiple query streams executing simultaneously to test throughput performance. Each stream runs a complete set of queries in parallel with other streams.

## D

**Databricks**
: Unified analytics platform built on Apache Spark. Supported via `DatabricksAdapter` for SQL Warehouse and cluster execution.

**Data Generation**
: Process of creating synthetic benchmark data at specified scale factors. Uses tools like `dbgen` (TPC-H) or `dsdgen` (TPC-DS).

**Data Loading**
: Process of importing generated data files into the target database. May involve schema creation, constraints, indexes, and validation.

**Dialect**
: SQL syntax variant specific to a database system. BenchBox uses sqlglot for dialect translation (e.g., PostgreSQL → Snowflake).

**Dry Run**
: Preview mode that generates queries and configuration without executing against a database. Useful for validation and cost estimation.

**DuckDB**
: In-process analytical database with fast query performance. Default platform for BenchBox local testing.

## E

**ETL (Extract, Transform, Load)**
: Data pipeline pattern central to TPC-DI benchmark. Involves extracting data from sources, transforming it, and loading into warehouse.

**Execution Phase**
: Distinct stage of benchmark execution tracked separately: setup, data generation, schema creation, data loading, validation, and query execution.

**Execution Time**
: Time taken to execute a query or complete benchmark run, excluding setup and data loading unless specified.

## G

**Generator**
: Tool that creates benchmark data files. Examples: `dbgen` (TPC-H), `dsdgen` (TPC-DS), `datagen` (TPC-DI).

## H

**H2O.ai Benchmark**
: Benchmark comparing data manipulation tools (databases, dataframes, query engines) using groupby operations.

## I

**Incremental Loading**
: Loading data in batches over time, as tested in TPC-DI benchmark through incremental update batches.

## J

**JoinOrder Benchmark**
: Real-world JOB benchmark based on canonical IMDb 2013 data, testing cardinality estimation and join-order optimization with 113 complex multi-table queries. BenchBox's public `joinorder` benchmark runs at `scale_factor=1.0` only.

## M

**Maintenance Function**
: Data modification operations (INSERT, UPDATE, DELETE) executed in the Maintenance Test phase of TPC benchmarks. These operations **permanently modify the database** and require a database reload before running additional power or throughput tests. In TPC-H, these are called Refresh Functions (RF1 and RF2). See [TPC-H Maintenance Test](../guides/tpc/tpc-h-official-guide.md#maintenance-test).

**Measurement Interval**
: Time period during which performance metrics are collected for throughput test calculations.

**Metric Tons**
: TPC-H scale factor 1 represents approximately 1 GB of data or 1 metric ton of goods in the business scenario.

## O

**OLAP (Online Analytical Processing)**
: Workload type characterized by complex read queries, aggregations, and analytics over large datasets. Most BenchBox benchmarks are OLAP-focused.

**OLTP (Online Transaction Processing)**
: Workload type characterized by many short read/write transactions. Not the primary focus of BenchBox but relevant for mixed workloads.

## P

**Parquet**
: Columnar storage file format commonly used for analytical workloads. Default output format for BenchBox data generation.

**Performance Run**
: Official benchmark execution following TPC rules for result reporting and comparison.

**Platform**
: Target database system for benchmark execution. BenchBox supports DuckDB, ClickHouse, Databricks, Snowflake, BigQuery, Redshift.

**Power Test**
: Single-stream benchmark execution measuring query response time. Runs each query once in sequence. Produces a geometric mean and Power@Size.

**Read Primitives**
: Microbenchmark suite testing fundamental database operations (scans, filters, aggregations, joins) using TPC-H data.

## Q

**QphDS@Size**
: TPC-DS composite performance metric: "Queries per hour at database size". **Not exported by BenchBox.** The specification formula is `SF × Q / (T_PT × T_TT × T_DM × T_LD)^(1/4)` with `Q = 99 × S`, and it needs the data maintenance and load times. BenchBox previously exported the geometric mean of Power@Size and Throughput@Size under this name, which is not the specification metric, so it no longer exports any QphDS@Size. Throughput@Size is scored with `Q = 99 × S`. See the [TPC-DS official guide](../guides/tpc/tpc-ds-official-guide.md#qphds-size-not-exported).

**QphH@Size**
: TPC-H composite performance metric: "Queries per hour at database size". BenchBox exports it only for official runs with 22 successful Power queries and successful RF1/RF2 on update set F, plus at least two complete Throughput streams with consecutive RF1/RF2 sets F+1..F+S. Failed queries, refresh functions, or query subsets prevent export; `qphh_unavailable` explains the reason. See the [TPC-H official guide](../guides/tpc/tpc-h-official-guide.md#qphh-size).

**Query ID**
: Unique identifier for a benchmark query. Examples: `"q1"`, `"query42"`, `"Q01"`.

**Query Parameter**
: Variable value substituted into query template during execution. TPC benchmarks use random parameters for different runs.

**Query Stream**
: Sequence of queries executed in a specific order. Throughput tests use multiple concurrent streams.

**Query Substitution**
: Process of replacing parameter placeholders in query templates with actual values.

**Query Template**
: Query with parameter placeholders that can be instantiated multiple times with different values.

## R

**Redshift**
: Amazon Web Services cloud data warehouse. Supported via `RedshiftAdapter`.

**Refresh Function (RF)**
: TPC maintenance operations that permanently modify data. TPC-H RF1 inserts DBGen update rows; RF2 deletes selected orders and their lineitems. With `tpch_refresh_functions=true`, Power includes RF1/RF2 and Throughput runs a concurrent refresh stream; it is disabled by default. Advance `tpch_refresh_first_set` to the next unused update set or reload original data before reusing sets. The standalone Maintenance phase also applies RF1/RF2. See [TPC-H refresh functions](../guides/tpc/tpc-h-official-guide.md#maintenance-test).

**Result Schema**
: Standardized JSON format for BenchBox benchmark results. Includes query timings, metadata, system profile, and validation status.

**Row Store**
: Database storage architecture that stores data by complete rows, optimizing for transactional workloads.

## S

**Scale Factor (SF)**
: Multiplier controlling benchmark data size. SF=1 is standard size (typically 1 GB for TPC-H). SF=0.01 is 1% size. SF=10 is 10x size.

**Schema**
: Database table definitions including columns, data types, constraints, and relationships.

**Snowflake**
: Cloud data warehouse platform. Supported via `SnowflakeAdapter`.

**SQL Dialect**
: See Dialect.

**SSB (Star Schema Benchmark)**
: Simplified benchmark derived from TPC-H, using a star schema design with a single fact table and four dimension tables.

**Setup Phase**
: Pre-execution stage including data generation, schema creation, and data loading. Typically excluded from performance measurements.

## T

**Throughput Test**
: Multi-stream benchmark execution measuring sustained query throughput. Runs multiple query streams concurrently. Produces queries-per-hour metric.

**TPC (Transaction Processing Performance Council)**
: Organization defining standard database benchmarks including TPC-H, TPC-DS, and TPC-DI.

**TPC-C**
: OLTP benchmark measuring transaction processing performance. Not currently supported by BenchBox.

**TPC-DI (Data Integration)**
: Benchmark simulating end-to-end data integration scenario with ETL processes, incremental updates, and data quality validation.

**TPC-DS (Decision Support)**
: Complex analytical benchmark with 99 queries covering advanced SQL features: multi-table joins, subqueries, window functions, rollups.

**TPC-H (Ad-Hoc Query)**
: Widely-used analytical benchmark with 22 queries simulating business intelligence workloads. Focus on joins, aggregations, and sorting.

**TPCHavoc**
: Modified version of TPC-H queries designed to test query optimizer robustness with challenging query patterns.

**Tuning**
: Platform-specific optimizations applied to tables (partitioning, clustering, sorting, indexes) to improve query performance.

## V

**Validation**
: Process of verifying benchmark results correctness by checking row counts, result sets, or checksums against expected values.

**Variant**
: TPC-DS query variation with different parameter seeds or substitution values. Used to test optimizer across diverse workload patterns.

## W

**Warehouse**
: Data warehouse system designed for analytical queries. Also refers to Databricks SQL Warehouse or Snowflake Warehouse compute resources.

**Workload**
: Complete set of operations executed against a database, including queries and maintenance functions.

## Related Concepts

### Performance Metrics

- **Execution Time**: Time to run queries (excludes setup)
- **Setup Time**: Time for data generation, schema creation, loading
- **Geometric Mean**: Average of query times using geometric mean formula (∏(x_i))^(1/n)
- **Query Throughput**: Queries per hour sustained across concurrent streams

### Data Formats

- **Parquet**: Columnar format (default)
- **CSV**: Text format (legacy compatibility)
- **JSON**: Structured text format
- **ORC**: Optimized Row Columnar format

### BenchBox CLI Concepts

- **`--scale`**: Set scale factor
- **`--dry-run`**: Preview mode without execution
- **`--output`**: Results output directory
- **`--queries`**: Run specific queries only (e.g. `Q1,Q6,Q17`)
- **`--verbose`**: Enable detailed logging

## See Also

- [Getting Started Guide](../usage/getting-started.md) - Basic BenchBox usage
- [Benchmark Catalog](../benchmarks/index.md) - Available benchmarks
- [Platform Selection Guide](../platforms/platform-selection-guide.md) - Choosing a database
- [TPC-H Guide](../benchmarks/tpc-h.md) - TPC-H benchmark details
- [TPC-DS Guide](../benchmarks/tpc-ds.md) - TPC-DS benchmark details
- [Configuration Handbook](../usage/configuration.md) - CLI flags and options
