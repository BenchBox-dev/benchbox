# Platform Comparison Matrix

```{tags} intermediate, reference
```

This matrix compares BenchBox platform families and links exact platform counts back to runtime registry metadata.

## Platform Overview

BenchBox supports platforms across registry categories and editorial documentation groups. Exact counts below are generated from `PlatformRegistry.get_platform_count_summary()` and checked by unit tests; the later sections remain human-oriented groupings for choosing a platform.

<!-- benchbox-registry-counts:start -->

Platform registry: **52** metadata entries; **48** SQL-capable; **18** DataFrame-capable; **14** dual-mode; support status counts: stable=5, beta=28, experimental=18, deprecated=1.

<!-- benchbox-registry-counts:end -->

| Registry category | Platforms | Notes |
| --- | ---: | --- |
| analytical | 8 | Local and engine-native analytical platforms |
| cloud | 23 | Managed warehouses, cloud Spark, and SaaS surfaces |
| dataframe | 5 | DataFrame-only metadata entries |
| distributed | 6 | Federated, cluster, and distributed SQL engines |
| embedded | 1 | Built-in embedded runtime |
| olap | 4 | PostgreSQL extension, embedded OLAP, and DuckLake lakehouse variants |
| relational | 1 | General relational database support |
| timeseries | 3 | Time-series engines with SQL/query support |

```{note}
**CLI Naming Convention**: DataFrame platforms use the `-df` suffix (e.g., `polars-df`, `pandas-df`) to distinguish them from SQL mode platforms. Some platforms like DataFusion support both modes - see [Hybrid Platforms](#hybrid-platforms-sql--dataframe) for details.
```

---

## Quick Decision Guide

```
1. Local development or testing?
   └── Yes → DuckDB (default) or DataFusion (Arrow-native)
   └── No → Continue to #2

2. Need DataFrame API (not SQL)?
   └── Yes → Polars-df (single-node) or PySpark-df (distributed)
   └── No → Continue to #3

3. Already using Snowflake and want DataFrame API?
   └── Yes → snowpark-connect (PySpark-compatible)
   └── No → Continue to #4

4. Cost is primary concern?
   └── Yes → DuckDB, ClickHouse, or PostgreSQL (self-hosted)
   └── No → Continue to #5

5. Data > 100GB?
   └── No → DuckDB, ClickHouse, or DataFusion
   └── Yes → Continue to #6

6. Which cloud provider?
   └── AWS → Redshift, Athena, or EMR Serverless
   └── GCP → BigQuery or Dataproc Serverless
   └── Azure → Azure Synapse Analytics, Microsoft Fabric Warehouse, or Microsoft Fabric Spark
   └── Multi-cloud → Snowflake or Databricks
```

---

## Deployment Modes

BenchBox supports multiple deployment modes for platforms, enabling the same benchmark to run against local, self-hosted, and cloud-managed instances. Use the colon syntax to specify deployment modes: `--platform platform:mode`.

### Platforms with Multiple Deployment Modes

| Platform        | Default Mode  | Available Modes                              | Syntax Examples                                 |
| --------------- | ------------- | -------------------------------------------- | ----------------------------------------------- |
| **ClickHouse**  | `local`       | `local`, `server`                            | `clickhouse-local`, `clickhouse-server`         |
| **Firebolt**    | `core`        | `core`, `cloud`                              | `firebolt:core`, `firebolt:cloud`               |
| **TimescaleDB** | `self-hosted` | `self-hosted`, `cloud`                       | `timescaledb`, `timescaledb:cloud`              |
| **PySpark**     | `local`       | `local`                                      | `pyspark`                                       |
| **LakeSail**    | `local`       | `local`, `distributed`                       | `lakesail` (+ `lakesail-df` for DataFrame mode) |
| **Velox**       | `local`       | `local`, `remote`                            | `velox:local`, `velox:remote`                   |
| **DuckLake**    | `local`       | `local`, `postgres_catalog`, `cloud_storage` | `ducklake` with `--platform-option catalog=...` |

### First-Class Cloud Platforms (Dialect Inheritance)

| Platform             | Inherits Dialect From | CLI Name           | Notes                        |
| -------------------- | --------------------- | ------------------ | ---------------------------- |
| **ClickHouse Cloud** | ClickHouse            | `clickhouse-cloud` | Managed ClickHouse service   |
| **MotherDuck**       | DuckDB                | `motherduck`       | Serverless DuckDB cloud      |
| **DuckLake**         | DuckDB                | `ducklake`         | Local DuckDB lakehouse (Parquet + SQL catalog) |
| **Starburst**        | Trino                 | `starburst`        | Starburst Galaxy managed Trino |


### Deployment Mode Characteristics

| Mode            | Credentials | Network | Cloud Storage | Examples                                       |
| --------------- | ----------- | ------- | ------------- | ---------------------------------------------- |
| **local**       | No          | No      | No            | DuckDB, chDB, Firebolt Core                    |
| **self-hosted** | Yes         | Yes     | No            | ClickHouse Server, Trino, TimescaleDB, SingleStore |
| **managed**     | Yes         | Yes     | Sometimes     | MotherDuck, ClickHouse Cloud, Starburst Galaxy |

```{note}
For detailed deployment mode configuration, see [Deployment Modes Guide](deployment-modes.md).
```

---

## Local/Embedded Platforms

Zero-infrastructure platforms that run in-process. Ideal for development, testing, and medium-scale analytics.

### Architecture Comparison

| Feature            | DuckDB            | DataFusion         | SQLite             | Polars             | ClickHouse               |
| ------------------ | ----------------- | ------------------ | ------------------ | ------------------ | ------------------------ |
| **CLI Name**       | `duckdb`          | `datafusion`       | `sqlite`           | `polars-df`        | `clickhouse-local`       |
| **Architecture**   | Embedded Columnar | In-memory Columnar | Embedded Row-based | In-memory Columnar | Embedded/Server Columnar |
| **Storage Format** | Native/Parquet    | Arrow/Parquet      | B-tree Pages       | Arrow/Parquet      | MergeTree                |
| **Query Engine**   | Vectorized        | Vectorized (Rust)  | B-tree             | Vectorized (Rust)  | Vectorized               |
| **Concurrency**    | Read-heavy        | In-memory Only     | Limited Write      | Read-heavy         | High Concurrent          |
| **ACID Support**   | Limited           | None               | Full               | None               | Limited                  |
| **SQL Support**    | Full              | Full               | Full               | **DataFrame only** | Full                     |

```{note}
**ClickHouse Platforms**: ClickHouse is available as three first-class platforms: `clickhouse-local` (chDB, zero-config), `clickhouse-server` (self-hosted), and `clickhouse-cloud` (managed). The legacy bare `clickhouse` selector has been removed and now raises an error naming these replacements. See [Migration Guide](clickhouse-migration.md).
```

### Relative Performance Characteristics

Performance varies based on workload, query complexity, data size, and hardware. The following are general architectural characteristics:

| Feature                | DuckDB             | DataFusion       | SQLite | Polars         | ClickHouse   |
| ---------------------- | ------------------ | ---------------- | ------ | -------------- | ------------ |
| **Storage Type**       | Columnar           | In-memory Arrow  | Row    | In-memory Arrow| Columnar     |
| **Parallelism**        | Multi-threaded     | Multi-threaded   | Single | Multi-threaded | Multi-threaded |
| **Data Loading**       | CSV/Parquet        | Parquet/CSV      | CSV    | Parquet        | Multiple     |
| **Memory Model**       | Disk + RAM         | In-memory        | Disk   | In-memory      | Configurable |
| **Max Recommended SF** | 100                | 50+              | 1      | 100            | 100+         |

### Use Cases

| Use Case                    | Recommended    | Alternatives        |
| --------------------------- | -------------- | ------------------- |
| **Development/Testing**     | DuckDB         | DataFusion, SQLite  |
| **CI/CD Validation**        | DuckDB, SQLite | DataFusion          |
| **PyArrow Workflows**       | DataFusion     | DuckDB              |
| **Research/Academia**       | DuckDB         | ClickHouse          |
| **Cost-Sensitive**          | DuckDB         | ClickHouse          |
| **DataFrame API Preferred** | Polars (`-df`) | PySpark, DataFusion |

### Installation

```bash
uv add duckdb

uv add datafusion

uv add polars

uv add benchbox --extra clickhouse-local

uv add benchbox --extra clickhouse-server
```

In order, these commands install:

- DuckDB, the default, which is included with BenchBox.
- DataFusion.
- Polars, for the DataFrame API only. Use the `polars-df` platform.
- ClickHouse Local (chDB), which needs no configuration.
- ClickHouse Server, which is self-hosted and requires a running instance.

SQLite is built into Python and needs no installation.

---

## Cloud Data Warehouses

Managed cloud platforms with enterprise features, auto-scaling, and petabyte-scale capacity.

```{note}
TimescaleDB also supports a managed cloud deployment mode via TigerData (`timescaledb:cloud`). It is shown separately below because it is a managed PostgreSQL time-series service rather than an MPP warehouse.
```

### Architecture Comparison

| Feature           | BigQuery     | Snowflake          | Databricks           | Redshift               | Synapse         | Fabric DW      | Athena               | Firebolt             |
| ----------------- | ------------ | ------------------ | -------------------- | ---------------------- | --------------- | -------------- | -------------------- | -------------------- |
| **CLI Name**      | `bigquery`   | `snowflake`        | `databricks`         | `redshift`             | `synapse`       | `fabric-dw`    | `athena`             | `firebolt`           |
| **Cloud**         | GCP          | Multi-cloud        | Multi-cloud          | AWS                    | Azure           | Azure          | AWS                  | Multi-cloud          |
| **Architecture**  | Columnar MPP | Columnar MPP       | Columnar MPP (Spark) | Columnar MPP           | Columnar MPP    | Columnar MPP   | Columnar MPP (Trino) | Columnar MPP (Trino) |
| **Storage**       | Capacitor    | Micro-partitions   | Delta Lake           | Columnar Blocks        | Distributed     | OneLake/Delta  | S3/Parquet           | Proprietary          |
| **Compute Model** | Serverless   | Virtual Warehouses | Clusters/Serverless  | Provisioned/Serverless | Dedicated Pools | Capacity Units | Serverless           | Engines              |

### Managed PostgreSQL / Time-Series Service

| Platform | CLI Name | Protocol | Authentication | SSL | Storage | Managed |
| -------- | -------- | -------- | -------------- | --- | ------- | ------- |
| **TimescaleDB / TigerData** | `timescaledb:cloud` | PostgreSQL | Password | Required | TigerData managed PostgreSQL storage | Yes |

### Cost Analysis

*Pricing as of January 2026. Verify current rates with providers.*

| Platform       | Compute Pricing    | Storage Pricing     | Data Transfer | Free Tier         |
| -------------- | ------------------ | ------------------- | ------------- | ----------------- |
| **BigQuery**   | $6.25/TiB queried  | $20/TB/month        | $0.12/GB      | 1TB/month queries |
| **Snowflake**  | $2-40/credit/hour  | Included in compute | $0.023/GB     | 30-day trial      |
| **Databricks** | $0.15-0.65/DBU     | Cloud storage       | $0.087/GB     | 14-day trial      |
| **Redshift**   | $0.25-16/hour      | $0.024/GB SSD       | $0.02/GB      | 2-month trial     |
| **Synapse**    | $1.33+/DWU/hour    | $0.023/GB           | $0.05/GB      | 30-day trial      |
| **Fabric DW**  | Capacity Units     | Included            | Included      | 60-day trial      |
| **Athena**     | $5/TB scanned      | S3 pricing          | S3 pricing    | None              |
| **Firebolt**   | Per-engine pricing | Included            | Included      | Free local mode   |
| **TigerData**  | Service tiers      | Included            | Included      | Free trial tiers  |

**Cost estimate context**: Monthly costs assume SF=10 workload (~10GB), 100 queries/day, standard configurations. Actual costs vary significantly based on query complexity, concurrency, and data volume.

### Security & Compliance

| Feature                   | BigQuery  | Snowflake | Databricks | Redshift  | Synapse  | Fabric DW | Athena  | Firebolt  |
| ------------------------- | --------- | --------- | ---------- | --------- | -------- | --------- | ------- | --------- |
| **Encryption at Rest**    | Default   | Default   | Default    | Default   | Default  | Default   | Default | Default   |
| **Encryption in Transit** | HTTPS     | TLS       | HTTPS      | TLS       | TLS      | TLS       | HTTPS   | TLS       |
| **Authentication**        | IAM/OAuth | Users/SSO | Users/SSO  | IAM/Users | Entra ID | Entra ID  | IAM     | Users/SSO |
| **SOC 2**                 | Yes       | Yes       | Yes        | Yes       | Yes      | Yes       | Yes     | Yes       |
| **HIPAA**                 | Yes       | Yes       | Yes        | Yes       | Yes      | Yes       | Yes     | Contact   |
| **FedRAMP**               | Yes       | Yes       | Yes        | Yes       | Yes      | Pending   | Yes     | No        |

TigerData (`timescaledb:cloud`) security profile:
- Encryption at rest: Default
- Encryption in transit: TLS (SSL required by adapter)
- Authentication: Username/password (`TIGERDATA_*` primary, `TIMESCALE_*` fallback)

### Installation

```bash
uv add google-cloud-bigquery google-cloud-storage

uv add snowflake-connector-python

uv add databricks-sql-connector

uv add redshift-connector boto3

uv add pyodbc azure-storage-blob azure-identity

uv add pyodbc azure-identity azure-storage-file-datalake

uv add pyathena boto3

uv add firebolt-sdk
```

In order, these commands install the drivers for BigQuery, Snowflake, Databricks (SQL mode), Redshift, Azure Synapse Analytics, Fabric Warehouse, Amazon Athena, and Firebolt.

---

## Distributed SQL Engines

Federated and distributed query engines for multi-source analytics.

### Architecture Comparison

| Feature          | Trino           | PrestoDB        | Spark SQL           | ClickHouse           | SingleStore            |
| ---------------- | --------------- | --------------- | ------------------- | -------------------- | ---------------------- |
| **CLI Name**     | `trino`         | `presto`        | `spark`             | `clickhouse-server`  | `singlestore`          |
| **Architecture** | Distributed MPP | Distributed MPP | Distributed (Spark) | Distributed Columnar | Distributed Columnar   |
| **Query Engine** | Vectorized      | Vectorized      | Spark Catalyst      | Vectorized           | Vectorized + Code Gen  |
| **Federation**   | Native          | Native          | Via connectors      | Limited              | Limited                |
| **Concurrency**  | High            | High            | Job-based           | Very High            | Very High              |
| **Execution Model** | Interactive MPP | Interactive MPP | Batch-oriented      | Interactive          | Interactive            |

### Architectural Characteristics

| Feature              | Trino               | PrestoDB            | Spark SQL                | ClickHouse         | SingleStore              |
| -------------------- | ------------------- | ------------------- | ------------------------ | ------------------ | ------------------------ |
| **Execution Model**  | Interactive MPP     | Interactive MPP     | Batch/micro-batch        | Interactive columnar | Interactive HTAP        |
| **Optimization**     | Cost-based          | Cost-based          | Catalyst optimizer       | Columnar vectorized | Cost-based + code gen   |
| **Max Scale Factor** | 1000+               | 1000+               | 10000+                   | 1000+              | 1000+                    |
| **Memory Model**     | Distributed         | Distributed         | Distributed              | Distributed        | Distributed              |
| **Typical Use Case** | Ad-hoc federation   | Ad-hoc federation   | Large-scale batch ETL    | Analytical queries | Real-time analytics/HTAP |

### Use Cases

| Use Case                    | Recommended      | Notes                         |
| --------------------------- | ---------------- | ----------------------------- |
| **Data Lake Analytics**     | Trino, Spark SQL | S3/GCS/ADLS support           |
| **Multi-source Federation** | Trino, PrestoDB  | Native connector ecosystem    |
| **Real-time Analytics**     | ClickHouse       | SingleStore, Firebolt         |
| **HTAP (Analytics + OLTP)** | SingleStore      | TimescaleDB                   |
| **Batch ETL**               | Spark SQL        | Native Spark integration      |
| **Starburst Enterprise**    | Trino            | Commercial Trino distribution |

### Installation

```bash
uv add trino

uv add presto-python-client

uv add pyspark

uv add benchbox --extra clickhouse-server

uv add singlestoredb
```

In order, these commands install the drivers for Trino, PrestoDB, Apache Spark SQL, ClickHouse (server mode, which requires a running instance), and SingleStore.

---

## Spark-Compatible Engines

Native-code engines that accept Spark SQL / DataFrame workloads without running Apache Spark's JVM execution path. Both speak the **Spark Connect** protocol, so the standard `pyspark` client is the only client library required.

### Architecture Comparison

| Feature                | LakeSail Sail                | Apache Gluten + Velox              |
| ---------------------- | ---------------------------- | ---------------------------------- |
| **CLI Name**           | `lakesail` (+ `lakesail-df`) | `velox`                            |
| **Engine**             | Rust (DataFusion core)       | C++ (Velox) offloaded from Spark   |
| **Spark relationship** | Drop-in replacement - no JVM | Plugin - retains JVM scheduler     |
| **Execution Mode**     | SQL and DataFrame            | SQL (Spark SQL dialect)            |
| **Protocol**           | Spark Connect                | Spark Connect (remote) / in-process SparkSession (local) |
| **Platform Support**   | Linux, macOS                 | **Linux only** (Docker on macOS/Windows) |
| **Fallback Behavior**  | Native-only (no JVM path)    | Unsupported operators fall back to JVM |
| **Headline Speedup**   | ~4x faster than Spark, TPC-H SF100 | ~3.3x faster than Spark, TPC-H (Gluten community) |

### Deployment Options

| Feature              | LakeSail Sail                      | Apache Gluten + Velox                    |
| -------------------- | ---------------------------------- | ---------------------------------------- |
| **Local**            | Single-node multi-threaded server  | In-process SparkSession with Gluten jar  |
| **Distributed**      | Multi-worker Rust cluster          | Not applicable (single-host native)      |
| **Remote**           | Spark Connect endpoint             | Pre-started Gluten-enabled Spark Connect server |
| **Docker Image**     | Community images                   | `benchbox-velox` (`linux/amd64` by default; custom arm64 jar required for native arm64) |

### Use Cases

| Use Case                                          | Recommended      | Notes                                  |
| ------------------------------------------------- | ---------------- | -------------------------------------- |
| **Drop-in Spark replacement (no JVM)**            | LakeSail         | Rust engine, zero-rewrite migration    |
| **Accelerate existing Spark deployments**         | Velox            | Keep Spark scheduler; offload operators |
| **Benchmark DataFrame + SQL on the same engine**  | LakeSail         | `lakesail` (SQL) and `lakesail-df` (DF) share engine |
| **Linux-only native tuning**                      | Velox            | Requires Gluten bundle jar             |
| **macOS / Windows development**                   | LakeSail         | Runs natively; Velox requires Docker   |

### Installation

```bash
uv add benchbox --extra lakesail
uv add pysail
python -m pysail spark server --port 50051
benchbox run --platform lakesail --benchmark tpch --scale 1.0

uv add benchbox --extra velox
benchbox run --platform velox --benchmark tpch --scale 0.1 \
    --platform-option gluten_jar_path=/opt/gluten-velox-bundle-spark4.0_2.13-linux_amd64-1.6.0.jar
```

Run the `pysail` server in a separate terminal. LakeSail uses the standard PySpark client through Spark Connect. Velox pulls in `pyspark[connect]>=3.5.0` and requires the Gluten bundle jar.

See [LakeSail Platform Guide](lakesail.md), [Velox Platform Guide](velox.md), and [Velox Jar Setup](velox_jar_setup.md) for full setup instructions.

---

## Relational & Time-Series Databases

Traditional relational databases and specialized time-series engines.

### Architecture Comparison

| Feature            | PostgreSQL       | TimescaleDB                | InfluxDB           |
| ------------------ | ---------------- | -------------------------- | ------------------ |
| **CLI Name**       | `postgresql`     | `timescaledb`              | `influxdb`         |
| **Type**           | Relational RDBMS | Time-series (PG extension) | Time-series        |
| **Storage**        | Row-based (heap) | Hypertables + compression  | Columnar (Parquet) |
| **Query Language** | PostgreSQL SQL   | PostgreSQL SQL             | SQL (FlightSQL)    |
| **ACID Support**   | Full             | Full                       | Limited            |
| **Compression**    | Optional (TOAST) | Native                     | Native             |

### Performance Characteristics

| Feature                 | PostgreSQL          | TimescaleDB         | InfluxDB            |
| ----------------------- | ------------------- | ------------------- | ------------------- |
| **Query Latency**       | Low                 | Low                 | Very Low            |
| **Write Throughput**    | Medium              | High                | Very High           |
| **Time-series Queries** | Manual optimization | Native optimization | Native optimization |
| **Max Recommended SF**  | 10                  | 100                 | 100                 |
| **Best For**            | OLTP workloads      | Time-series OLAP    | IoT/metrics         |

### Use Cases

| Use Case                  | Recommended | Notes                    |
| ------------------------- | ----------- | ------------------------ |
| **OLTP Benchmarking**     | PostgreSQL  | Traditional workloads    |
| **Time-series Analytics** | TimescaleDB | Continuous aggregates    |
| **IoT/Metrics**           | InfluxDB    | High-cardinality support |
| **Hybrid OLTP/OLAP**      | TimescaleDB | Best of both worlds      |

### Installation

```bash
uv add benchbox --extra postgresql

uv add benchbox --extra influxdb
```

The `postgresql` extra provides the `psycopg` 3 driver; the `influxdb` extra provides `influxdb3-python`.

In order, these commands install the drivers for PostgreSQL, TimescaleDB (which uses the same driver as PostgreSQL), and InfluxDB.

---

## Managed Spark Services

Cloud-managed Apache Spark environments for distributed processing.

### AWS Spark Services

| Feature          | AWS Glue       | EMR Serverless             | Athena for Spark           |
| ---------------- | -------------- | -------------------------- | -------------------------- |
| **CLI Name**     | `glue`         | `emr-serverless`           | `athena-spark`             |
| **Type**         | Managed ETL    | Serverless Spark           | Interactive Spark          |
| **Startup Time** | 2-5 minutes    | ~30s (warm), 2-3min (cold) | ~30s (warm), 1-2min (cold) |
| **Pricing**      | $0.44/DPU-hour | vCPU + Memory              | DPU-hour                   |
| **Use Case**     | ETL pipelines  | Batch processing           | Interactive analysis       |
| **Data Catalog** | Glue Catalog   | Glue Catalog               | Glue Catalog               |

### GCP Spark Services

| Feature          | Dataproc              | Dataproc Serverless   |
| ---------------- | --------------------- | --------------------- |
| **CLI Name**     | `dataproc`            | `dataproc-serverless` |
| **Type**         | Managed Clusters      | Serverless Batches    |
| **Startup Time** | 1-2 minutes           | 30-60 seconds         |
| **Pricing**      | Per-second            | Per-second            |
| **Use Case**     | Long-running clusters | Ad-hoc batches        |
| **Metastore**    | Hive Metastore        | Hive Metastore        |

### Azure Spark Services

| Feature      | Fabric Spark     | Synapse Spark        |
| ------------ | ---------------- | -------------------- |
| **CLI Name** | `fabric-spark`   | `synapse-spark`      |
| **Type**     | SaaS Spark       | Enterprise Spark     |
| **Storage**  | OneLake          | ADLS Gen2            |
| **Auth**     | Entra ID         | Entra ID             |
| **Pricing**  | Capacity Units   | vCore-hour           |
| **Use Case** | Fabric ecosystem | Enterprise analytics |

### Cross-Cloud Comparison

| Feature              | Glue | EMR Serverless | Dataproc | Dataproc Serverless | Fabric Spark | Synapse Spark |
| -------------------- | ---- | -------------- | -------- | ------------------- | ------------ | ------------- |
| **Serverless**       | Yes  | Yes            | No       | Yes                 | Yes          | No            |
| **DataFrame Mode**   | Yes  | Yes            | Yes      | Yes                 | Yes          | Yes           |
| **SQL Mode**         | Yes  | Yes            | Yes      | Yes                 | Yes          | Yes           |
| **Auto-scaling**     | Yes  | Yes            | Manual   | Yes                 | Yes          | Manual        |
| **Spot/Preemptible** | No   | Yes            | Yes      | No                  | No           | No            |

### Multi-Cloud Spark Alternatives

| Feature              | Onehouse Quanton    | Databricks          |
| -------------------- | ------------------- | ------------------- |
| **CLI Name**         | `quanton`           | `databricks`        |
| **Type**             | Serverless Spark    | Managed Spark       |
| **Table Formats**    | Hudi, Iceberg, Delta| Delta (native)      |
| **XTable Support**   | Yes                 | No                  |
| **Pricing**          | Compute-hours       | DBU-hours           |
| **Use Case**         | Multi-format lakes  | Lakehouse           |
| **Cloud Support**    | AWS                 | AWS, Azure, GCP     |

### Installation

```bash
uv add boto3

uv add google-cloud-dataproc google-cloud-storage

uv add azure-identity azure-storage-file-datalake requests

uv add requests boto3
```

In order, these commands install the dependencies for AWS Spark services (Glue, EMR Serverless, Athena for Spark), GCP Spark services (Dataproc, Dataproc Serverless), Azure Spark services (Fabric Spark, Synapse Spark), and Onehouse Quanton.

---

## DataFrame Platforms

Native DataFrame API libraries for programmatic data manipulation.

```{important}
**Naming Convention**: All DataFrame platforms use the `-df` suffix in CLI names to distinguish them from SQL-mode platforms. For example, use `polars-df` for Polars DataFrame API or `datafusion-df` for DataFusion DataFrame API.
```

### Expression Family (Lazy Evaluation)

| Feature          | Polars                | PySpark        | DataFusion      | Databricks      | Snowpark Connect     | LakeSail            |
| ---------------- | --------------------- | -------------- | --------------- | --------------- | -------------------- | ------------------- |
| **CLI Name**     | `polars-df`           | `pyspark-df`   | `datafusion-df` | `databricks-df` | `snowpark-connect`   | `lakesail-df`       |
| **Language**     | Rust + Python         | Scala + Python | Rust + Python   | Scala + Python  | Python               | Rust + Python       |
| **Execution**    | Lazy                  | Lazy           | Lazy            | Lazy            | Lazy (pushdown)      | Lazy                |
| **Parallelism**  | Multi-threaded        | Distributed    | Multi-threaded  | Distributed     | Snowflake compute    | Multi-threaded / distributed |
| **Memory Model** | In-memory + streaming | Cluster-bound  | In-memory       | Cluster-bound   | Snowflake-managed    | In-memory + streaming |
| **Arrow Native** | Yes                   | Yes            | Yes             | Yes             | No (Snowflake types) | Yes                 |

### Pandas Family (Eager/Lazy)

| Feature               | Pandas          | Dask         | cuDF             |
| --------------------- | --------------- | ------------ | ---------------- |
| **CLI Name**          | `pandas-df`     | `dask-df`    | `cudf-df`        |
| **Execution**         | Eager           | Lazy         | Eager            |
| **Parallelism**       | Single-threaded | Distributed  | GPU              |
| **API Compatibility** | Reference       | Near drop-in | Near drop-in     |
| **Best For**          | Small datasets  | Distributed  | GPU acceleration |

### Performance Characteristics

| Platform             | Query Latency | Throughput | Memory Usage      | Max SF |
| -------------------- | ------------- | ---------- | ----------------- | ------ |
| **Polars**           | Very Low      | Very High  | Efficient         | 100    |
| **PySpark**          | Medium        | Very High  | Cluster-bound     | 1000+  |
| **DataFusion**       | Very Low      | High       | Efficient         | 50     |
| **Databricks**       | Medium        | Very High  | Cluster-bound     | 1000+  |
| **Snowpark Connect** | Medium        | High       | Snowflake-managed | 1000+  |
| **LakeSail**         | Low           | Very High  | Efficient         | 100+   |
| **Pandas**           | Low           | Medium     | High              | 10     |
| **Dask**             | Medium        | High       | Distributed       | 1000+  |
| **cuDF**             | Very Low      | Very High  | GPU VRAM          | 50     |

### GPU Acceleration

| Feature      | cuDF            | PySpark (RAPIDS) |
| ------------ | --------------- | ---------------- |
| **Hardware** | NVIDIA GPU      | NVIDIA GPU       |
| **Platform** | Linux only      | Linux only       |
| **API**      | Pandas-like     | Spark DataFrame  |
| **Memory**   | GPU VRAM        | GPU + Host       |
| **Use Case** | Single-node GPU | Distributed GPU  |

### Installation

```bash
uv add polars
uv add pyspark
uv add datafusion

uv add databricks-connect

uv add "snowflake-snowpark-python[pandas]"

uv add pyspark pyarrow

uv add pandas
uv add dask[distributed]

pip install cudf-cu12 --extra-index-url=https://pypi.nvidia.com
```

The commands install, in order:

- The expression-family libraries: Polars, PySpark (requires Java 17 or 21), and DataFusion.
- Databricks DataFrame, which requires Databricks Connect.
- Snowpark Connect, which is PySpark-compatible on Snowflake.
- LakeSail DataFrame, which uses Spark Connect through the standard PySpark client.
- The Pandas family: Pandas and Dask.
- cuDF, which needs an NVIDIA GPU and Linux. It uses `pip` because of its CUDA dependencies.

---

## Hybrid Platforms (SQL + DataFrame)

These platforms support both SQL and native DataFrame execution modes. Use the appropriate CLI name based on your preferred execution paradigm.

| Platform             | SQL Mode     | DataFrame Mode     | Default Mode | Notes                               |
| -------------------- | ------------ | ------------------ | ------------ | ----------------------------------- |
| **DataFusion**       | `datafusion` | `datafusion-df`    | SQL          | Arrow-native both modes             |
| **PySpark**          | `spark`      | `pyspark-df`       | DataFrame    | SparkSQL vs DataFrame API           |
| **Databricks**       | `databricks` | `databricks-df`    | SQL          | SQL Warehouse vs Databricks Connect |
| **Snowpark Connect** | N/A          | `snowpark-connect` | DataFrame    | PySpark-compatible API on Snowflake |
| **LakeSail**         | `lakesail`   | `lakesail-df`      | SQL          | Rust/DataFusion engine, Spark Connect |

```{note}
**Polars** is DataFrame-only (`polars-df`). SQL mode was removed due to fundamental limitations in Polars' SQL implementation (no implicit joins, limited subquery support) that make it incompatible with TPC benchmarks. For SQL benchmarks, use `duckdb` or another SQL-native platform.
```

**Usage Example:**
```bash
benchbox run --platform polars-df --benchmark tpch --scale 1

benchbox run --platform datafusion --benchmark tpch --scale 1
benchbox run --platform datafusion-df --benchmark tpch --scale 1

benchbox run --platform databricks --benchmark tpch --scale 10
benchbox run --platform databricks-df --benchmark tpch --scale 10

benchbox run --platform snowpark-connect --benchmark tpch --scale 10
```

Polars supports DataFrame mode only. DataFusion supports SQL mode (`datafusion`) and DataFrame mode (`datafusion-df`). Databricks runs against a SQL Warehouse (`databricks`) or through Databricks Connect (`databricks-df`). Snowpark Connect runs DataFrame workloads on Snowflake.

---

## Benchmark Support Matrix

### TPC Benchmarks

| Benchmark  | Local              | Cloud DW | Distributed | Time-Series             | DataFrame                   |
| ---------- | ------------------ | -------- | ----------- | ----------------------- | --------------------------- |
| **TPC-H**  | All                | All      | All         | PostgreSQL, TimescaleDB | All                         |
| **TPC-DS** | DuckDB, ClickHouse | All      | All         | N/A                     | Polars, PySpark, Databricks |
| **SSB**    | All                | All      | All         | N/A                     | All                         |

### Analytics Benchmarks

| Benchmark      | Local              | Cloud DW | Distributed       | Time-Series | DataFrame           |
| -------------- | ------------------ | -------- | ----------------- | ----------- | ------------------- |
| **ClickBench** | DuckDB, ClickHouse | All      | ClickHouse        | N/A         | Polars              |
| **H2O.ai**     | DuckDB             | All      | Spark             | N/A         | All                 |
| **AMPLab**     | DuckDB             | All      | Spark, Databricks | N/A         | PySpark, Databricks |

### Time-Series Benchmarks

| Benchmark       | PostgreSQL | TimescaleDB | InfluxDB |
| --------------- | ---------- | ----------- | -------- |
| **TSBS DevOps** | Limited    | Full        | Native   |
| **TSBS IoT**    | Limited    | Full        | Native   |

---

## Scale Factor Recommendations

| Scale Factor   | Dataset Size | Recommended Platforms                       |
| -------------- | ------------ | ------------------------------------------- |
| **0.001-0.01** | 1-10MB       | DuckDB, DataFusion, SQLite                  |
| **0.1-1**      | 100MB-1GB    | DuckDB, DataFusion, ClickHouse, Polars      |
| **1-10**       | 1-10GB       | DuckDB, ClickHouse, BigQuery, All DataFrame |
| **10-100**     | 10-100GB     | DuckDB, ClickHouse, All Cloud DW, PySpark   |
| **100-1000**   | 100GB-1TB    | BigQuery, Snowflake, Databricks, Redshift   |
| **1000+**      | 1TB+         | BigQuery, Snowflake, Databricks, Spark      |

---

## Cost Comparison (All Categories)

*All cost estimates as of January 2026. Based on SF=10 workload (~10GB data), 100 queries/day.*

### Free/Open Source

| Platform     | Type            | Cost               |
| ------------ | --------------- | ------------------ |
| DuckDB       | Local           | Free               |
| DataFusion   | Local           | Free               |
| SQLite       | Local           | Free               |
| Polars       | Local/DataFrame | Free               |
| ClickHouse   | Local/Server    | Free (self-hosted) |
| PostgreSQL   | Relational      | Free (self-hosted) |
| Trino/Presto | Distributed     | Free (self-hosted) |
| Spark        | Distributed     | Free (self-hosted) |

### Cloud Pay-Per-Use

| Platform | Typical Monthly Cost | Best For                   | Cost Basis    |
| -------- | -------------------- | -------------------------- | ------------- |
| BigQuery | $50-5000             | GCP workloads              | $6.25/TiB queried |
| Athena   | $50-2000             | S3 data lake               | $5/TB scanned |
| Firebolt | $100-3000            | High-performance analytics | Per-engine    |

### Cloud Provisioned

| Platform   | Typical Monthly Cost | Best For               | Cost Basis     |
| ---------- | -------------------- | ---------------------- | -------------- |
| Snowflake  | $100-10000           | Multi-cloud enterprise | Credits/hour   |
| Databricks | $200-5000            | ML/Lakehouse           | DBU/hour       |
| Redshift   | $100-5000            | AWS enterprise         | Node-hours     |
| Synapse    | $200-5000            | Azure enterprise       | DWU-hours      |
| Fabric     | Capacity-based       | Microsoft ecosystem    | Capacity Units |

---

## Use Case Recommendations

### By Team Size

| Team Profile             | Recommended           | Alternatives       |
| ------------------------ | --------------------- | ------------------ |
| **Individual Developer** | DuckDB                | DataFusion, Polars |
| **Small Team (2-5)**     | DuckDB, ClickHouse    | PostgreSQL         |
| **Medium Team (5-20)**   | BigQuery, Snowflake   | Databricks         |
| **Enterprise**           | Snowflake, Databricks | Redshift, Synapse  |

### By Workload

| Workload                  | Recommended           | Alternatives         |
| ------------------------- | --------------------- | -------------------- |
| **Ad-hoc Analytics**      | DuckDB, BigQuery      | Athena, Snowflake    |
| **Production Dashboards** | Snowflake, BigQuery   | Redshift, Databricks |
| **ML/Data Science**       | Databricks, PySpark   | Snowflake, BigQuery  |
| **Real-time Analytics**   | ClickHouse            | Firebolt             |
| **Time-series IoT**       | TimescaleDB, InfluxDB | ClickHouse           |
| **Data Lake**             | Databricks, Athena    | Trino, Spark         |

### By Cloud Provider

| Cloud           | Data Warehouse        | Spark Service       | Serverless |
| --------------- | --------------------- | ------------------- | ---------- |
| **AWS**         | Redshift              | EMR Serverless      | Athena     |
| **GCP**         | BigQuery              | Dataproc Serverless | BigQuery   |
| **Azure**       | Synapse, Fabric DW    | Fabric Spark        | Fabric DW  |
| **Multi-cloud** | Snowflake, Databricks | Databricks          | Snowflake  |

---

## Migration Complexity

| From \ To      | DuckDB | BigQuery | Snowflake | Databricks |
| -------------- | ------ | -------- | --------- | ---------- |
| **DuckDB**     | -      | Medium   | Medium    | Hard       |
| **BigQuery**   | Medium | -        | Easy      | Medium     |
| **Snowflake**  | Medium | Easy     | -         | Medium     |
| **Databricks** | Hard   | Medium   | Medium    | -          |
| **Redshift**   | Medium | Medium   | Easy      | Medium     |

---

## Summary by Category

### Local/Embedded
- **DuckDB**: Default choice for development, testing, and medium-scale analytics
- **DataFusion**: Arrow-native, ideal for PyArrow workflows
- **SQLite**: Lightweight testing and CI/CD
- **Polars**: High-performance DataFrame library (DataFrame API only, use `polars-df`)
- **ClickHouse**: High-throughput analytics (local or server mode)

### Cloud Data Warehouses
- **BigQuery**: GCP serverless, pay-per-query
- **Snowflake**: Multi-cloud enterprise, elastic scaling
- **Databricks**: Lakehouse, ML/data science
- **Redshift**: AWS-native, provisioned or serverless
- **Synapse**: Azure enterprise, T-SQL compatible
- **Fabric DW**: Microsoft ecosystem, OneLake integration
- **Athena**: AWS serverless, S3 data lake
- **Firebolt**: High-performance vectorized analytics

### Distributed SQL
- **Trino**: Federated SQL, data lake analytics
- **PrestoDB**: Meta's fork, similar to Trino
- **Spark SQL**: Batch processing, Spark ecosystem
- **ClickHouse**: Real-time OLAP, high concurrency
- **SingleStore**: Distributed HTAP, columnstore analytics (MySQL protocol)

### Spark-Compatible Engines
- **LakeSail Sail**: Rust drop-in Spark replacement (DataFusion core), SQL + DataFrame via Spark Connect
- **Apache Gluten + Velox**: Native C++ acceleration plugin for Spark SQL (Linux-only local mode; Docker on macOS/Windows)

### Relational & Time-Series
- **PostgreSQL**: Traditional RDBMS baseline
- **TimescaleDB**: Time-series on PostgreSQL
- **InfluxDB**: IoT and metrics workloads

### Managed Spark
- **AWS**: Glue, EMR Serverless, Athena for Spark
- **GCP**: Dataproc, Dataproc Serverless
- **Azure**: Fabric Spark, Synapse Spark

### DataFrame
- **Expression Family**: Polars (single-node), PySpark (distributed), DataFusion (Arrow-native), Databricks (managed), Snowpark Connect (Snowflake-native), LakeSail (Rust/DataFusion via Spark Connect)
- **Pandas Family**: Pandas (reference), Dask (distributed), cuDF (GPU-accelerated)

---

## Resources

- **Individual Platform Guides**: See platform-specific docs in this section
- **Platform Selection Guide**: [platform-selection-guide.md](platform-selection-guide.md)
- **DataFrame Platforms**: [dataframe.md](dataframe.md)
- **Getting Started**: [../usage/getting-started.md](../usage/getting-started.md)
