<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Platform Documentation

```{tags} guide
```

Documentation for database platforms supported by BenchBox.

## Platform Guides

- [Platform Selection Guide](platform-selection-guide.md) - Choose the right platform for your needs
- [Quick Reference](quick-reference.md) - Quick comparison and feature matrix
- [Comparison Matrix](comparison-matrix.md) - Detailed platform comparison
- [Support Status](support-status.md) - Registry support taxonomy and optional import diagnostics
- [**Deployment Modes**](deployment-modes.md) - Platform deployment architecture (local, self-hosted, cloud)

## DataFrame Platforms (Native API)

BenchBox supports benchmarking DataFrame libraries using their native APIs instead of SQL. This enables direct performance comparison between SQL and DataFrame paradigms on identical workloads.

- [**DataFrame Platforms Overview**](dataframe.md) - Architecture, installation, and usage guide

### Available DataFrame Platforms

| Platform | CLI Name | Family | Status | Documentation |
|----------|----------|--------|--------|---------------|
| **Polars** | `polars-df` | Expression | Production-ready | [Polars DataFrame](polars.md) |
| **Pandas** | `pandas-df` | Pandas | Production-ready | [Pandas DataFrame](pandas-dataframe.md) |
| **Dask** | `dask-df` | Pandas | Production-ready | [Dask DataFrame](dask-dataframe.md) |
| **cuDF** | `cudf-df` | Pandas | Production-ready | [cuDF DataFrame](cudf.md) |
| **PySpark** | `pyspark-df` | Expression | Production-ready | [PySpark DataFrame](pyspark-dataframe.md) |
| **LakeSail** | `lakesail-df` | Expression | Production-ready | [LakeSail DataFrame](lakesail.md) |
| **DataFusion** | `datafusion-df` | Expression | Production-ready | [DataFusion DataFrame](datafusion-dataframe.md) |
| **Databricks** | `databricks-df` | Expression | Production-ready | [Databricks DataFrame](databricks-dataframe.md) |

```bash
benchbox run --platform polars-df --benchmark tpch --scale 0.1
benchbox run --platform pandas-df --benchmark tpch --scale 0.1
benchbox run --platform dask-df --benchmark tpch --scale 0.1
benchbox run --platform cudf-df --benchmark tpch --scale 0.1
benchbox run --platform pyspark-df --benchmark tpch --scale 0.1
benchbox run --platform lakesail-df --benchmark tpch --scale 0.1
benchbox run --platform datafusion-df --benchmark tpch --scale 0.1
benchbox run --platform databricks-df --benchmark tpch --scale 0.1

benchbox run --platform polars --benchmark tpch --scale 0.1
benchbox run --platform polars-df --benchmark tpch --scale 0.1
```

Polars is the recommended starting point because it is fast. Pandas offers a familiar API. Dask is distributed. cuDF runs on GPUs and works on Linux only. PySpark fits the Spark ecosystem. LakeSail runs on Sail, a fast Spark-compatible engine. Databricks uses Databricks Connect.

The last two commands compare SQL and DataFrame execution on the same workload: `--platform polars` is SQL mode and `--platform polars-df` is DataFrame mode.

## SQL Platforms

### Core Local Databases

These platforms are included in the base BenchBox installation with no additional dependencies:

- [**DuckDB**](duckdb.md) - Embedded analytical database (default local platform)
- [**SQLite**](sqlite.md) - Embedded row-store database for lightweight testing

### Local/Embedded Analytics Engines

- [Apache DataFusion](datafusion.md) - Rust-based in-memory query engine
- [ClickHouse Local](clickhouse-local-mode.md) - Embedded ClickHouse via chDB (zero-config)
- [ClickHouse Server](clickhouse-server.md) - Self-hosted ClickHouse via clickhouse-driver
- [Polars](polars.md) - High-performance DataFrame library with SQL support

### Traditional Relational Databases

- [PostgreSQL](postgresql.md) - Open-source relational database with TimescaleDB support
- [CedarDB](cedardb.md) - High-performance HTAP engine over PostgreSQL wire protocol (formerly Umbra)
- [GizmoSQL](gizmosql.md) - DuckDB over Arrow Flight SQL through the official ADBC driver (experimental)

### PostgreSQL Extensions

- [pg_duckdb](pg_duckdb.md) - DuckDB-powered Postgres extension for vectorized OLAP
- [pg_mooncake](pg_mooncake.md) - Columnar storage for Postgres with Iceberg and Delta Lake support
- [paradedb](paradedb.md) - Hybrid search and analytics Postgres distribution (BM25 via pg_search)
- [citus](citus.md) - Distributed Postgres extension with sharded tables

### Self-Hosted OLAP Databases

- [StarRocks](starrocks.md) - MPP columnar OLAP database (MySQL protocol + Stream Load)
- [Apache Doris](doris.md) - MPP real-time analytical database (MySQL protocol + Stream Load)
- [SingleStore](singlestore.md) - Distributed SQL with columnstore analytics (MySQL protocol + LOAD DATA)
- [QuestDB](questdb.md) - Time-series database (PostgreSQL wire protocol + REST API)

### Distributed SQL Engines

- [PrestoDB](presto.md) - Distributed SQL query engine (Facebook fork)
- [Trino](trino.md) - Distributed SQL query engine (community fork, formerly PrestoSQL)
- [Apache Spark](spark.md) - Unified analytics engine for large-scale data processing
- [LakeSail Sail](lakesail.md) - High-performance Spark-compatible engine (Spark Connect)
- [Apache Gluten + Velox](velox.md) - Spark plugin that offloads physical operators to a vectorized C++ engine (Linux-only, Docker on macOS/Windows)

### Cloud Data Warehouses

- [**ClickHouse Cloud**](clickhouse-cloud.md) - Managed ClickHouse service (inherits ClickHouse dialect)
- [**Snowflake**](snowflake.md) - Cloud-native data platform (AWS, Azure, GCP)
- [**Databricks**](databricks.md) - Lakehouse platform with Unity Catalog support
- [**Onehouse Quanton**](quanton.md) - Serverless Spark with multi-format support (Hudi, Iceberg, Delta)
- [**Google BigQuery**](bigquery.md) - Serverless data warehouse
- [**Amazon Redshift**](redshift.md) - Columnar data warehouse
- [**MotherDuck**](motherduck.md) - Serverless DuckDB cloud (inherits DuckDB dialect)
- [**DuckLake**](ducklake.md) - DuckDB lakehouse format (Parquet data + SQL catalog; inherits DuckDB dialect)
- [**Starburst**](starburst.md) - Managed Trino / Starburst Galaxy (inherits Trino dialect)
- [Amazon Athena](athena.md) - Serverless query service for S3
- [Firebolt](firebolt.md) - High-performance cloud analytics (Core + Cloud modes)
- [Databend](databend.md) - Cloud-native OLAP warehouse (Snowflake-compatible SQL, S3/MinIO storage)
- [Azure Synapse Analytics](azure-platforms.md) - Microsoft cloud analytics platform
- [Microsoft Fabric Warehouse](microsoft-fabric.md) - T-SQL warehouse with OneLake-native storage
- [Microsoft Fabric Lakehouse SQL](fabric-lakehouse.md) - Read-only SQL endpoint over Fabric lakehouses
- [TimescaleDB / TigerData](timescaledb.md) - TimescaleDB with managed TigerData cloud mode (`timescaledb:cloud`)

### GPU-Accelerated Platforms

- [CUDF](cudf.md) - NVIDIA RAPIDS GPU-accelerated DataFrames

## Platform Categories

### By Installation Complexity

**Zero Config** (included in base install):
- DuckDB, SQLite

**Single Extra** (one `pip install` command):
- DataFusion, Polars, PostgreSQL, ClickHouse

**Cloud SDK Required** (authentication setup needed):
- Databricks SQL, Databend, BigQuery, Redshift, Snowflake, Amazon Athena, Firebolt, Azure Synapse Analytics

**Infrastructure Required** (external cluster needed):
- Trino, Presto, Spark, LakeSail, Gluten+Velox (Linux/Docker), StarRocks, Doris, QuestDB, ClickHouse (server mode)

### By Use Case

**Local Development & Testing:**
- DuckDB (recommended), SQLite, DataFusion, Polars

**Production Benchmarking:**
- Databricks, Snowflake, BigQuery, Redshift

**Self-Hosted Analytics:**
- ClickHouse, StarRocks, Doris, QuestDB, PostgreSQL, Trino, Presto, Spark, LakeSail, Gluten+Velox

**GPU Workloads:**
- CUDF (NVIDIA GPUs required)

## Quick Start by Platform

### Local Platforms (No Setup Required)

```bash
benchbox run --platform duckdb --benchmark tpch --scale 0.01

benchbox run --platform sqlite --benchmark tpch --scale 0.01
```

DuckDB is the default and is included in the base install. SQLite is also included in the base install.

### Cloud Platforms (Credentials Required)

```bash
benchbox run --platform databricks --benchmark tpch --scale 1.0

benchbox run --platform bigquery --benchmark tpch --scale 1.0

benchbox run --platform snowflake --benchmark tpch --scale 1.0
```

Databricks requires `DATABRICKS_TOKEN` and `DATABRICKS_HOST`. BigQuery requires `GOOGLE_APPLICATION_CREDENTIALS`. Snowflake requires `SNOWFLAKE_USER`, `SNOWFLAKE_PASSWORD` and `SNOWFLAKE_ACCOUNT`.

## Future Platforms

- [Development Roadmap](../development/roadmap.md) - Planned platform and benchmark additions

## Related Documentation

- [Getting Started](../usage/getting-started.md) - Quick start guide
- [Cloud Storage Guide](../guides/cloud-storage.md) - Cloud storage configuration for staging data
- [Platform Configuration](../usage/configuration.md) - Detailed configuration options

```{toctree}
:maxdepth: 1

platform-selection-guide
quick-reference
comparison-matrix
support-status
deployment-modes
dataframe
duckdb
sqlite
polars
pandas-dataframe
modin-dataframe
dask-dataframe
cudf
pyspark-dataframe
datafusion-dataframe
databricks-dataframe
spark
lakesail
velox
velox_jar_setup
starrocks
doris
singlestore
questdb
databend
cedardb
gizmosql
clickhouse-local-mode
clickhouse-server
clickhouse-cloud
clickhouse-migration
postgresql
pg_duckdb
pg_mooncake
paradedb
citus
presto
trino
snowflake
databricks
bigquery
redshift
motherduck
ducklake
starburst
quanton
athena
athena-spark
firebolt
microsoft-fabric
fabric-lakehouse
azure-platforms
datafusion
influxdb
timescaledb
aws-glue
gcp-dataproc
dataproc-serverless
emr-serverless
fabric-spark
synapse-spark
snowpark-connect
```
