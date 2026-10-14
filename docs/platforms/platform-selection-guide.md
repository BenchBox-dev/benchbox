# Platform Selection Guide

```{tags} beginner, guide, sql-platform
```

This guide helps you choose the optimal database platform for your BenchBox benchmarking needs based on your specific requirements, constraints, and objectives. Start by running `uv run benchbox check-deps --matrix` to see which optional connectors are installed locally, then use `benchbox platforms install <name>` and `benchbox platforms enable <name>` to activate the adapters that match your shortlist.

> Curious about what's being evaluated next? Check the [Development Roadmap](../development/roadmap.md) for planned platform and benchmark additions.

## Quick Start: Choose Your Path

### I want to start immediately with minimal setup
**Look for**: Embedded databases with no external dependencies
- No additional installation or configuration required
- In-process execution without network overhead
- Suitable for learning and experimentation

### I want to minimize costs
**Look for**: Open-source platforms
- No licensing costs
- Run on your own hardware
- Minimal operational overhead

### I need fast analytical queries at scale
**Look for**: Scalable architectures (cloud or self-hosted)
- Column-oriented storage optimized for analytical workloads
- Horizontal scaling capabilities
- Advanced query optimization features

### I want fully managed cloud service
**Look for**: Managed cloud platforms
- No infrastructure management required
- Auto-scaling capabilities
- Enterprise support and SLAs available

### I need enterprise features and governance
**Look for**: Platforms with governance capabilities
- Advanced security and compliance features
- Role-based access control
- Audit logging and governance capabilities

### I want to benchmark DataFrame libraries (not SQL)
**Look for**: DataFrame platform adapters
- Native DataFrame API benchmarking (no SQL)
- Compare libraries on identical workloads
- Measure performance of data science workflows
- See [DataFrame Platforms Guide](dataframe.md) for details

```bash
benchbox run --platform polars-df --benchmark tpch --scale 0.1
benchbox run --platform pandas-df --benchmark tpch --scale 0.1
```

## Platform Extras and Installation Matrix

BenchBox uses optional dependency extras to keep the core installation lightweight. Install just the connectors you need, or combine extras for broader coverage. Always wrap the extras specification in quotes when using shells like zsh.

| Extra              | What it Enables                                           | Install Command                                   |
| ------------------ | --------------------------------------------------------- | ------------------------------------------------- |
| `(none)`           | SQLite only (core package, no DuckDB)                     | `uv add benchbox`                         |
| `[duckdb]`         | DuckDB for local analytics                                | `uv add benchbox --extra duckdb`          |
| `[cloudstorage]`   | Cloudpathlib helpers for S3, GCS, Azure paths             | `uv add benchbox --extra cloudstorage`        |
| `[cloud]`          | BigQuery, Databricks SQL, Redshift, Snowflake connectors  | `uv add benchbox --extra cloud`               |
| `[databricks]`     | Databricks SQL Warehouses + Unity Catalog tooling         | `uv add benchbox --extra databricks`          |
| `[bigquery]`       | BigQuery client libraries + Google Cloud Storage support | `uv add benchbox --extra bigquery`            |
| `[redshift]`       | Redshift connector, boto3, and cloud storage helpers      | `uv add benchbox --extra redshift`            |
| `[snowflake]`      | Snowflake connector + cloud storage helpers               | `uv add benchbox --extra snowflake`           |
| `[trino]`          | Trino/Starburst distributed SQL connector                 | `uv add benchbox --extra trino`               |
| `[presto]`         | PrestoDB (Meta) distributed SQL connector                 | `uv add benchbox --extra presto`              |
| `[clickhouse]`     | ClickHouse native driver                                  | `uv add benchbox --extra clickhouse`          |
| `[all]`            | Every connector and helper listed above                   | `uv add benchbox --extra all`                 |

Need to double-check your environment? Run `benchbox check-deps --matrix` for the full installation matrix or `benchbox check-deps --platform <name>` for targeted guidance.

## Presto Lineage: Trino vs PrestoDB vs Amazon Athena

- **Trino**: Community-driven fork; uses `trino` Python client and `X-Trino-*` headers. Compatible with Starburst deployments.
- **PrestoDB**: Original project maintained by Meta; uses `presto-python-client` with `X-Presto-*` headers. Compatible with Meta ecosystem tools.
- **Amazon Athena**: AWS managed service built on the Presto/Trino lineage; serverless execution without self-managed coordinators.

## SQL vs DataFrame: When to Choose Each

Before diving into platform specifics, consider whether SQL or DataFrame APIs better match your benchmarking needs.

### When to Choose SQL Platforms

Choose SQL platforms (DuckDB, BigQuery, Snowflake, etc.) when:

- **You're benchmarking database systems** - Comparing cloud data warehouses or analytical databases
- **Your production workloads use SQL** - Testing the same queries your applications execute
- **You need distributed execution** - Cloud platforms automatically scale compute
- **Your team thinks in SQL** - Familiar with SQL query patterns and optimization

```bash
benchbox run --platform duckdb --benchmark tpch --scale 1
benchbox run --platform snowflake --benchmark tpch --scale 100
```

### When to Choose DataFrame Platforms

Choose DataFrame platforms (Polars-df, Pandas-df, PySpark-df, etc.) when:

- **You're benchmarking DataFrame libraries** - Comparing Polars vs Pandas vs PySpark
- **Your production code uses DataFrame APIs** - Data science workflows, ML pipelines
- **You want to compare paradigms** - SQL vs native DataFrame performance
- **You need lazy evaluation control** - Fine-grained control over execution plans

```bash
benchbox run --platform polars-df --benchmark tpch --scale 1
benchbox run --platform pandas-df --benchmark tpch --scale 1
```

### Side-by-Side Comparison

| Aspect | SQL Platforms | DataFrame Platforms |
|--------|---------------|---------------------|
| **Query Format** | SQL strings | Native API calls (method chaining) |
| **Execution** | Database engine | Library (in-process or distributed) |
| **Scale Limit** | Petabyte+ (cloud) | Varies: SF 10-100 (single-node), SF 1000+ (PySpark) |
| **Use Case** | Production analytics, data warehousing | Data science, ML pipelines, library evaluation |
| **Debugging** | EXPLAIN plans | Step-through execution, lazy plan inspection |
| **Type Safety** | Runtime (SQL parsing) | Some compile-time (IDE support) |

### DataFrame Platform Quick Reference

| Platform | CLI Name | Best For | Max Scale Factor |
|----------|----------|----------|------------------|
| **Polars** | `polars-df` | High-performance single-node analytics | SF 100 (lazy eval) |
| **Pandas** | `pandas-df` | Data science prototyping, compatibility | SF 10 (memory-bound) |
| **PySpark** | `pyspark-df` | Distributed processing, big data | SF 1000+ (cluster) |
| **DataFusion** | `datafusion-df` | Arrow-native workflows, Rust performance | SF 50+ |
| **LakeSail** | `lakesail-df` | Rust Spark replacement (DataFusion core) via Spark Connect | SF 100+ |

For detailed DataFrame platform documentation, see the [DataFrame Platforms Guide](dataframe.md).

## Spark-Compatible Engines (Non-JVM or Accelerated)

Two native-code engines speak the Spark SQL / DataFrame API without running Spark's full JVM execution path. Both use the standard `pyspark` client via Spark Connect.

| Platform | CLI Name | Execution Model | Local Platform Support | When to Pick |
|----------|----------|-----------------|------------------------|--------------|
| **LakeSail Sail** | `lakesail` (SQL) / `lakesail-df` (DF) | Rust/DataFusion - full drop-in replacement, no JVM | Linux, macOS | You want to migrate off Spark with zero code changes, or benchmark SQL + DataFrame on the same engine. |
| **Apache Gluten + Velox** | `velox` | Plugin - Spark keeps its scheduler; Velox runs the physical plan in C++ | **Linux only** (Docker on macOS/Windows) | You want to accelerate an existing Spark deployment without changing client code; unsupported operators fall back to JVM. |

```bash
benchbox run --platform lakesail --benchmark tpch --scale 1.0

benchbox run --platform lakesail-df --benchmark tpch --scale 1.0

benchbox run --platform velox --benchmark tpch --scale 0.1 \
    --platform-option gluten_jar_path=/opt/gluten-velox-bundle-spark4.0_2.13-linux_amd64-1.6.0.jar

benchbox run --platform velox:remote \
    --platform-option endpoint=sc://localhost:50051 \
    --benchmark tpch --scale 0.1
```

The first command runs LakeSail with SQL and the second with the DataFrame API. The first Velox command runs locally on Linux with a Gluten bundle jar. The second connects to a Gluten-enabled Spark Connect server, for example one running in Docker.

See [LakeSail Platform Guide](lakesail.md), [Velox Platform Guide](velox.md), and the [Comparison Matrix](comparison-matrix.md#spark-compatible-engines) for deeper architectural and deployment detail.

---

## Detailed Selection Criteria

### 1. Data Scale Requirements

#### Small Scale (< 1GB)
**Platform Options**: DuckDB, DataFusion, SQLite

```python
from benchbox import TPCH
from benchbox.platforms.duckdb import DuckDBAdapter

benchmark = TPCH(scale_factor=0.1)
adapter = DuckDBAdapter()
results = adapter.run_benchmark(benchmark)

from benchbox.platforms.datafusion import DataFusionAdapter
adapter = DataFusionAdapter(
    memory_limit="4G",
    data_format="parquet"
)
results = adapter.run_benchmark(benchmark)
```

Scale factor 0.1 is about 100MB of data. `DuckDBAdapter()` processes data in memory.

**Characteristics**:
- No additional setup or configuration required
- Embedded databases suitable for small datasets
- Free and lightweight
- Commonly used in CI/CD pipelines
- DataFusion offers PyArrow integration for Arrow-based workflows

#### Medium Scale (1GB - 100GB)
**Platform Options**: BigQuery, ClickHouse, DataFusion, DuckDB

```python
adapter = DuckDBAdapter(
    database_path="analytics.duckdb",
    memory_limit="16GB"
)

from benchbox.platforms.clickhouse import ClickHouseAdapter
adapter = ClickHouseAdapter(
    host="clickhouse-cluster",
    max_memory_usage="32GB",
    max_threads=16
)
```

**Characteristics**:
- Balance of performance and cost
- Support for complex analytical queries
- Moderate resource requirements

#### Large Scale (100GB+)
**Platform Options**: BigQuery, Databricks SQL, Redshift, Snowflake

```python
from benchbox.platforms.snowflake import SnowflakeAdapter
adapter = SnowflakeAdapter(
    warehouse_size="LARGE",
    database="analytics_benchmarks"
)

from benchbox.platforms.bigquery import BigQueryAdapter
adapter = BigQueryAdapter(
    project_id="analytics-project",
    dataset_id="large_scale_benchmarks"
)
```

**Characteristics**:
- Support for petabyte-scale analytics
- Auto-scaling infrastructure
- Managed cloud data warehouse platforms

### 2. Budget and Cost Considerations

#### Free/Open Source Solutions

| Platform       | Cost Structure  | Ongoing Costs        |
| -------------- | --------------- | -------------------- |
| **DuckDB**     | Completely free | Hardware only        |
| **DataFusion** | Completely free | Hardware only        |
| **SQLite**     | Completely free | Hardware only        |
| **ClickHouse** | Open source     | Infrastructure costs |

**Example Setup**:
```python
adapter = DuckDBAdapter(
    database_path="cost_effective.duckdb",
    memory_limit="8GB"
)

from benchbox.platforms.datafusion import DataFusionAdapter
adapter = DataFusionAdapter(
    memory_limit="8G",
    data_format="parquet"
)
```

The DuckDB example sets a memory limit so it uses available RAM efficiently, and the DataFusion example reads Parquet, which is faster than CSV.

#### Pay-per-Use Cloud Solutions

| Platform       | Pricing Model   | Cost Control        |
| -------------- | --------------- | ------------------- |
| **BigQuery**   | $6.25/TiB queried (US/EU/Asia multi-region) | Query limits, slots |
| **Databricks SQL** | DBU consumption | Auto-termination    |

**Cost-Optimized Configuration**:
```python
adapter = BigQueryAdapter(
    maximum_bytes_billed=1000000000,
    job_priority="BATCH",
    query_cache=True
)
```

`maximum_bytes_billed=1000000000` caps each query at 1GB billed. `job_priority="BATCH"` lowers cost. `query_cache=True` reuses earlier results.

#### Provisioned Cloud Solutions

| Platform      | Pricing Model  | Cost Optimization   |
| ------------- | -------------- | ------------------- |
| **Snowflake** | Warehouse time | Auto-suspend/resume |
| **Redshift**  | Cluster hours  | Reserved instances  |

**Cost-Optimized Configuration**:
```python
adapter = SnowflakeAdapter(
    warehouse_size="MEDIUM",
    auto_suspend=60,
    auto_resume=True
)
```

`auto_suspend=60` suspends the warehouse after one minute of inactivity.

### 3. Performance Requirements

#### Latency-Sensitive Applications

**Platform Options**: ClickHouse, DuckDB

```python
adapter = DuckDBAdapter(
    memory_limit="32GB",
    thread_limit=None
)

adapter = ClickHouseAdapter(
    max_threads=32,
    max_memory_usage="64GB",
    compression=True
)
```

The DuckDB example keeps data in memory and uses all CPU cores (`thread_limit=None`). The ClickHouse example enables compression to reduce network I/O.

**Characteristics**:
- In-process or local execution reduces network overhead
- Column-oriented storage optimized for analytical queries
- Query latency depends on data volume, query complexity, and hardware resources

#### Throughput-Optimized Workloads

**Platform Options**: BigQuery, Databricks SQL, Snowflake

```python
adapter = BigQueryAdapter(
    job_priority="INTERACTIVE",
    query_cache=False,
    location="US"
)

adapter = SnowflakeAdapter(
    warehouse_size="X-LARGE",
    multi_cluster_warehouse=True,
    auto_suspend=600
)
```

BigQuery is serverless and scales automatically. Here `query_cache=False` forces fresh results on every run, and `location="US"` selects a multi-region deployment. The Snowflake example uses multi-cluster scaling and a 10-minute auto-suspend, which suits sustained workloads.

**Performance Characteristics**:
- Concurrent user support: Hundreds to thousands of users
- Auto-scaling: Automatic resource adjustment available
- Query queuing: Workload management capabilities

### 4. Operational Requirements

#### Minimal Operations (Fully Managed)

**Platform Options**: BigQuery, Snowflake

```python
adapter = BigQueryAdapter()
```

BigQuery is a fully managed serverless service with no infrastructure to manage, automatic optimization, and built-in monitoring.

**Characteristics**:
- No database administration required
- Automatic updates and maintenance
- Built-in monitoring and alerting
- High uptime SLAs (typically 99.9%+)

#### Some Operations Acceptable

**Platform Options**: ClickHouse (managed), Databricks SQL, Redshift

```python
adapter = DatabricksAdapter(
    cluster_size="large",
    auto_terminate_minutes=30,
    runtime_engine="PHOTON"
)
```

`auto_terminate_minutes=30` controls cost by stopping idle clusters, and `PHOTON` is the vectorized query engine.

**Characteristics**:
- Managed infrastructure with user control
- Configurable performance settings
- Access to advanced features
- Professional support available

#### Full Control Required

**Platform Options**: ClickHouse (self-hosted), DuckDB

```python
adapter = ClickHouseAdapter(
    host="your-optimized-cluster.company.com",
)
```

A self-managed deployment gives you custom cluster configuration, your own monitoring and alerting, and complete control of the data.

**Characteristics**:
- Complete infrastructure control
- Custom optimization opportunities
- Data locality and privacy
- No vendor dependencies

### 5. Team and Organizational Factors

#### Individual Developer/Researcher

**Platform Options**: DuckDB

```python
adapter = DuckDBAdapter()
benchmark = TPCH(scale_factor=1)
results = adapter.run_benchmark(benchmark)
```

**Characteristics**:
- No database administration knowledge required
- Comprehensive documentation and community
- Reproducible results across environments
- Focus on analysis rather than infrastructure

#### Small Team (2-10 people)

**Platform Options**: BigQuery, ClickHouse, or DuckDB

```python
adapter = BigQueryAdapter(
    project_id="team-analytics-project",
    dataset_id="shared_benchmarks",
)
```

A shared Google Cloud project provides collaboration features and cost visibility and controls.

**Characteristics**:
- Shared access and collaboration capabilities
- Cost transparency and controls
- Minimal administrative overhead
- Scalable as team grows

#### Medium Team (10-50 people)

**Platform Options**: Databricks SQL, Snowflake

```python
adapter = SnowflakeAdapter()
```

Snowflake offers role-based access control, multiple warehouses for different workloads, query result sharing, and usage monitoring and governance.

**Characteristics**:
- Advanced security and governance
- Workload isolation capabilities
- Resource management features
- Enterprise support available

#### Large Enterprise (50+ people)

**Platform Options**: Databricks SQL, Redshift, Snowflake

```python
adapter = DatabricksAdapter()
```

Databricks provides Unity Catalog for governance, multiple workspaces, advanced security features, and enterprise support with SLAs.

**Characteristics**:
- Enterprise security and compliance features
- Advanced governance capabilities
- Professional services and support
- Integration with enterprise tools

### 6. Technical Ecosystem

#### Python-Centric Environment

**All platforms supported**. DuckDB and Databricks SQL offer native Python integration:

```python
import duckdb
import pandas as pd

df = pd.read_csv("data.csv")
result = duckdb.query("SELECT * FROM df WHERE value > 100").to_df()

from pyspark.sql import SparkSession
spark = SparkSession.builder.appName("BenchBox").getOrCreate()
```

The first half shows DuckDB reading a pandas DataFrame directly. The second half shows the Spark and Python integration that Databricks SQL uses.

#### SQL-Heavy Teams

**Platform Options**: BigQuery, Redshift, Snowflake

```python
adapter = BigQueryAdapter()
```

BigQuery, Redshift, and Snowflake support ANSI SQL with platform-specific extensions, platform-managed optimization, and a standard SQL interface.

#### Multi-Language Requirements

**Platform Options**: ClickHouse, Databricks SQL

- ClickHouse has client libraries for Python, Java, Go, C++, and JavaScript, an HTTP API usable from any language, and JDBC/ODBC drivers.
- Databricks SQL supports the Spark ecosystem languages (Scala, Python, Java, R, and SQL), REST APIs, and a notebook interface.

### 7. Cloud Strategy

#### Amazon Web Services

**AWS-integrated platform**: Redshift
**Other options**: ClickHouse (EKS), Databricks SQL, DuckDB, Snowflake

```python
adapter = RedshiftAdapter()
```

Redshift integrates with S3 for data loading, IAM for security, CloudWatch for monitoring, and Lambda for automation.

#### Google Cloud Platform

**Google Cloud-integrated platform**: BigQuery
**Other options**: ClickHouse (GKE), Databricks SQL, DuckDB, Snowflake

```python
adapter = BigQueryAdapter(
    project_id="gcp-project",
    location="US",
)
```

Use `location="EU"` instead of `"US"` for data residency in Europe. BigQuery integrates with Cloud Storage, Cloud Functions, Dataflow, and AI/ML services.

#### Microsoft Azure

**Azure-integrated platforms**: Azure Synapse Analytics, Microsoft Fabric
**Multi-cloud platforms on Azure**: Databricks SQL, Snowflake
**Other options**: DuckDB

```python
from benchbox.platforms.azure_synapse import AzureSynapseAdapter
adapter = AzureSynapseAdapter()

from benchbox.platforms.fabric_warehouse import FabricWarehouseAdapter
adapter = FabricWarehouseAdapter()

adapter = DatabricksAdapter(...)
adapter = SnowflakeAdapter(...)
```

- Azure Synapse Analytics supports dedicated or serverless SQL pools, PolyBase staging, and Azure AD authentication.
- Microsoft Fabric Warehouse uses a T-SQL interface through pyodbc, Entra ID authentication, and OneLake staging.
- Databricks and Snowflake are multi-cloud platforms, available on Azure infrastructure as on the other two clouds.

See the [Azure Platforms](azure-platforms.md) page for details on all Azure integrations including Spark adapters (`fabric-spark`, `synapse-spark`).

#### Multi-Cloud or Cloud-Agnostic

**Multi-cloud platforms**: Databricks SQL, Snowflake
**Cloud-agnostic options**: ClickHouse, DuckDB

```python
adapter = DatabricksAdapter()
```

Databricks is available on AWS, Azure, and GCP with a consistent interface across clouds, which reduces cloud vendor lock-in.

#### On-Premises or Hybrid

**Self-hosted options**: ClickHouse, DuckDB
**Hybrid options**: Databricks SQL (private cloud)

```python
adapter = ClickHouseAdapter(
    host="on-prem-cluster.company.com",
)
```

An on-premises deployment gives complete data control, no cloud dependencies, and custom security policies.

## Selection Criteria Summary

### By Primary Goal

```
Learning/Research
├── Small data (< 1GB): Embedded databases (DuckDB, SQLite)
└── Large data (> 1GB): DuckDB or cloud platforms with free tiers

Development/Testing
├── Local development: Embedded databases (no dependencies)
├── CI/CD pipeline: DuckDB or SQLite (embedded)
└── Integration testing: DuckDB (fast setup)

Production Analytics
├── Open-source: ClickHouse, DuckDB (self-hosted)
└── Cloud-native: Cloud platforms (managed services)

Performance Benchmarking
├── Cross-platform comparison: Multiple platforms
└── Single-platform optimization: Platform-specific testing
```

### By Key Decision Factors

```
1. Data scale considerations:
   ├── < 1GB → Embedded databases (DuckDB, SQLite)
   ├── 1-100GB → ClickHouse, DuckDB, or cloud platforms
   └── > 100GB → Cloud platforms (BigQuery, Databricks SQL, Redshift, Snowflake)

2. Budget model:
   ├── No cost → ClickHouse (self-hosted), DuckDB, SQLite
   ├── Pay-per-use → BigQuery, Databricks SQL (usage-based)
   └── Predictable costs → Redshift, Snowflake (provisioned)

3. Team size:
   ├── Individual → DuckDB (minimal setup)
   ├── Small team → Cloud platforms with shared access
   ├── Medium team → Cloud platforms with governance features
   └── Large enterprise → Cloud platforms with enterprise features

4. Cloud environment:
   ├── AWS → Redshift (integrated), or Databricks SQL, Snowflake (multi-cloud)
   ├── Azure → Databricks SQL, Snowflake (multi-cloud)
   ├── Google Cloud → BigQuery (integrated), or Databricks SQL, Snowflake (multi-cloud)
   ├── Multi-cloud → Databricks SQL, Snowflake
   └── On-premises → ClickHouse, DuckDB (self-hosted)

5. Architecture characteristics:
   ├── In-process columnar → ClickHouse, DuckDB (no network overhead)
   ├── Serverless/auto-scaling → BigQuery, Snowflake (elastic compute)
   └── Managed clusters → Cloud platforms (various options)
```

## Platform Combination Strategies

### Development to Production Pipeline

```python
dev_adapter = DuckDBAdapter()

test_adapter = DuckDBAdapter(database_path="test.duckdb")

prod_adapter = SnowflakeAdapter(
    warehouse_size="LARGE",
    database="PRODUCTION"
)

benchmark = TPCH(scale_factor=0.1)
prod_benchmark = TPCH(scale_factor=100)
```

Development is fast, local, and free. Testing is automated and reproducible. Production is scalable and managed. The same benchmark code runs in every environment, with a small scale factor for development and testing and a large one for production.

### Multi-Platform Validation

```python
platforms = [
    ("ClickHouse", ClickHouseAdapter(host="test-cluster")),
    ("DuckDB", DuckDBAdapter()),
    ("Snowflake", SnowflakeAdapter(warehouse_size="MEDIUM")),
]

benchmark = TPCH(scale_factor=1)
results = {}

for name, adapter in platforms:
    results[name] = adapter.run_benchmark(benchmark)
    print(f"{name}: {results[name].total_time:.2f}s")

```

Platforms are listed in alphabetical order. After the loop, compare the query results across platforms for consistency.

### Cost-Performance Optimization

```python
adapter = DuckDBAdapter(memory_limit="16GB")
results = adapter.run_benchmark(benchmark)

if results.total_time > target_sla:
    adapter = SnowflakeAdapter(warehouse_size="LARGE")
    results = adapter.run_benchmark(benchmark)

```

Start with the cost-effective option and upgrade to a cloud platform only when the SLA is missed. Keep monitoring costs and performance over time.

## Migration Strategies

### From SQLite to Production

```python
sqlite_adapter = SQLiteAdapter()
small_benchmark = ReadPrimitivesBenchmark(scale_factor=0.001)
sqlite_results = sqlite_adapter.run_benchmark(small_benchmark)

duckdb_adapter = DuckDBAdapter()
medium_benchmark = TPCH(scale_factor=1)
duckdb_results = duckdb_adapter.run_benchmark(medium_benchmark)

prod_adapter = SnowflakeAdapter(warehouse_size="LARGE")
prod_benchmark = TPCH(scale_factor=100)
prod_results = prod_adapter.run_benchmark(prod_benchmark)
```

Phase 1 validates on SQLite, which is suitable for testing only. Phase 2 tests scale with DuckDB. Phase 3 deploys to production on your chosen cloud platform: Snowflake as shown, `BigQueryAdapter(project_id="production")`, or `RedshiftAdapter(cluster_identifier="prod-cluster")`.

### From DuckDB to Cloud

```python
dev_config = {"scale_factor": 0.1}
dev_adapter = DuckDBAdapter()

migration_config = {"scale_factor": 1}
cloud_adapter = DatabricksAdapter(
    cluster_size="medium",
    auto_terminate_minutes=30
)

prod_config = {"scale_factor": 100}
prod_adapter = DatabricksAdapter(
    cluster_size="large",
    runtime_engine="PHOTON"
)
```

Develop on DuckDB, validate the migration on any cloud platform (`auto_terminate_minutes=30` protects against idle cost), then deploy to production.

## Common Anti-Patterns

### ❌ Wrong Platform Choices

1. **Using SQLite for large datasets**
   ```python
   adapter = SQLiteAdapter()
   benchmark = TPCH(scale_factor=10)
   ```
   SQLite with TPC-H at scale factor 10 will be extremely slow.

2. **Using expensive platforms for development**
   ```python
   adapter = SnowflakeAdapter(warehouse_size="4X-LARGE")
   benchmark = TPCH(scale_factor=0.01)
   ```
   A 4X-Large Snowflake warehouse is expensive and wasted on a tiny dataset.

3. **Ignoring cost controls**
   ```python
   adapter = BigQueryAdapter()
   ```
   This sets no `maximum_bytes_billed`, so pay-per-query platforms risk unexpectedly large bills.

### ✅ Better Alternatives

1. **Progressive scaling approach**
   ```python
   if dataset_size < 1_000_000:
       adapter = DuckDBAdapter()
   elif dataset_size < 100_000_000:
       adapter = ClickHouseAdapter()
   else:
       adapter = BigQueryAdapter()
   ```
   Start small and scale up as needed.

2. **Cost-aware cloud usage**
   ```python
   adapter = BigQueryAdapter(
       maximum_bytes_billed=5_000_000_000,
       job_priority="BATCH"
   )
   ```
   Always set cost controls: here a 5GB billing limit and the lower-cost batch priority.

3. **Environment-specific configurations**
   ```python
   import os

   if os.getenv("ENVIRONMENT") == "development":
       adapter = DuckDBAdapter()
   elif os.getenv("ENVIRONMENT") == "testing":
       adapter = DuckDBAdapter(database_path="test.duckdb")
   else:
       adapter = SnowflakeAdapter(warehouse_size="LARGE")
   ```

## Getting Started

### First-Time Users

1. **DuckDB is included by default**
   ```bash
   uv add benchbox
   ```

   ```python
   from benchbox import TPCH
   from benchbox.platforms.duckdb import DuckDBAdapter

   benchmark = TPCH(scale_factor=0.1)
   adapter = DuckDBAdapter()
   results = adapter.run_benchmark(benchmark)
   ```

2. **Scale factor options**
   - 0.001 (1MB): Rapid iteration
   - 0.01 (10MB): Quick testing
   - 0.1 (100MB): SF0.1 testing
   - 1.0 (1GB): Medium-scale testing

3. **Available benchmarks**
   - TPC-H: Industry-standard analytical benchmark
   - ClickBench: Real-world analytical queries
   - Primitives: Fundamental operation testing

### Moving to Production

1. **Evaluate cloud platforms with test datasets**
2. **Configure cost controls and monitoring**
3. **Validate with realistic data volumes**
4. **Implement security and governance policies**
5. **Plan for capacity and growth**

## Expert Tips

### Performance Optimization

1. **Match platform to workload characteristics**
   - OLAP-heavy: ClickHouse, BigQuery
   - Mixed workloads: Snowflake, Databricks SQL
   - Development: DuckDB

2. **Use appropriate scale factors for testing**
   - Development: 0.001-0.01
   - Integration testing: 0.1-1
   - Performance testing: 10-100+

3. **Use platform-specific optimizations**
   - DuckDB: Memory sizing, threading
   - ClickHouse: Partitioning, ordering
   - BigQuery: Partitioning, clustering
   - Snowflake: Warehouse sizing, clustering

### Cost Management

1. **Implement cost controls early**
2. **Monitor usage patterns and optimize**
3. **Use development/production separation**
4. **Consider reserved capacity for predictable workloads**

### Operational Excellence

1. **Automate deployments and scaling**
2. **Implement proper monitoring and alerting**
3. **Plan for disaster recovery and backup**
4. **Document configuration and procedures**

## Summary

Platform selection depends on your specific requirements. Common platform choices by use case:

### SQL Platforms
- **Initial setup**: DuckDB (included by default, no dependencies)
- **No-cost options**: ClickHouse (self-hosted), DuckDB, SQLite
- **In-process execution**: ClickHouse, DuckDB (columnar architecture, no network overhead)
- **Enterprise teams**: Cloud platforms with governance features
- **Cloud-native deployments**: BigQuery, Databricks SQL, Redshift, Snowflake (managed services)
- **Self-hosted deployments**: ClickHouse, DuckDB (full control)

### Spark-Compatible Engines
- **Drop-in Spark replacement (no JVM)**: LakeSail Sail (`lakesail`, `lakesail-df`) - Rust/DataFusion engine via Spark Connect
- **Accelerate existing Spark jobs**: Apache Gluten + Velox (`velox`) - native C++ operators, Linux-only local mode (Docker on macOS/Windows)

### DataFrame Platforms
- **Single-node analytics**: Polars-df (multi-threaded, lazy evaluation)
- **Data science prototyping**: Pandas-df (familiar API, ecosystem compatibility)
- **Distributed big data**: PySpark-df (cluster-scale processing)
- **Arrow-native workflows**: DataFusion-df (Rust-based, Arrow integration)
- **Spark-compatible Rust engine**: LakeSail-df (via Spark Connect, DataFusion core)
- **GPU acceleration**: cuDF-df (NVIDIA RAPIDS, coming soon)

Install BenchBox with the `[duckdb]` extra for SQL benchmarking. Polars is included for DataFrame benchmarking. Cloud platforms and additional DataFrame libraries can be added as requirements grow.

## See Also

### Platform Documentation

- **[DataFrame Platforms Guide](dataframe.md)** - Native DataFrame API benchmarking (Polars, Pandas, PySpark, DataFusion)
- **[Platform Quick Reference](quick-reference.md)** - Quick setup guides for all platforms
- **[Platform Comparison Matrix](comparison-matrix.md)** - Feature and performance comparison
- **[ClickHouse Local Mode](clickhouse-local-mode.md)** - Running ClickHouse locally
- **[Development Roadmap](../development/roadmap.md)** - Planned platform and benchmark additions
- **[Cloud Storage](../guides/cloud-storage.md)** - Cloud storage integration (S3, GCS, Azure)
- **[Compression](../guides/compression.md)** - Data compression strategies

### Getting Started

- **[Getting Started Guide](../usage/getting-started.md)** - Run your first benchmark in 5 minutes
- **[Installation Guide](../usage/installation.md)** - Installation and setup
- **[CLI Quick Reference](../usage/cli-quick-start.md)** - Command-line usage
- **[Examples](../usage/examples.md)** - Code snippets and patterns

### Understanding BenchBox

- **[Architecture Overview](../concepts/architecture.md)** - How BenchBox works
- **[Workflow Patterns](../concepts/workflow.md)** - Common workflows (dev, cloud, CI/CD)
- **[Data Model](../concepts/data-model.md)** - Result schemas and analysis
- **[Glossary](../concepts/glossary.md)** - Platform and benchmark terminology

### Benchmarks

- **[Benchmark Catalog](../benchmarks/index.md)** - Available benchmarks
- **[TPC-H Benchmark](../benchmarks/tpc-h.md)** - Standard analytical benchmark
- **[TPC-DS Benchmark](../benchmarks/tpc-ds.md)** - Complex decision support
- **[ClickBench](../benchmarks/clickbench.md)** - Real-world analytics

### Advanced Topics

- **[Performance Guide](../advanced/performance.md)** - Performance tuning and optimization
- **[Custom Benchmarks](../advanced/custom-benchmarks.md)** - Creating custom benchmarks
- **[Configuration Handbook](../usage/configuration.md)** - Advanced configuration options
- **[Dry Run Mode](../usage/dry-run.md)** - Preview queries without execution
