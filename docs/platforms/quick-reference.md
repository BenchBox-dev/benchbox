<!-- markdownlint-disable MD024 -->

<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Multi-Platform Database Support

```{tags} beginner, reference
```

BenchBox supports running benchmarks across multiple database platforms through its platform adapter architecture. This allows you to compare performance, validate query compatibility, and test your applications across different database systems.

Looking ahead? See the [Development Roadmap](../development/roadmap.md) for planned platform and benchmark additions.

## Supported Platforms

### SQL Platforms

| Platform                  | Status    | Description                                                                                                | Installation                                        |
| ------------------------- | --------- | ---------------------------------------------------------------------------------------------------------- | --------------------------------------------------- |
| **DuckDB**                | Built-in  | In-process analytical database                                                                             | `uv add duckdb`                                     |
| **DataFusion**            | Available | In-memory query engine (Apache Arrow)                                                                      | `uv add datafusion`                                 |
| **ClickHouse Local**      | Available | Embedded ClickHouse via chDB (zero-config)                                                                 | `uv add benchbox --extra clickhouse-local`          |
| **ClickHouse Server**     | Available | Self-hosted ClickHouse (clickhouse-driver)                                                                 | `uv add benchbox --extra clickhouse-server`         |
| **ClickHouse Cloud**      | Available | Managed ClickHouse service (HTTPS)                                                                         | `uv add benchbox --extra clickhouse-cloud`          |
| **Databricks SQL**        | Available | Data Intelligence Platform (lakehouse)                                                                     | `uv add databricks-sql-connector`                   |
| **BigQuery**              | Available | Serverless data warehouse (Google Cloud)                                                                   | `uv add google-cloud-bigquery google-cloud-storage` |
| **Redshift**              | Available | Cloud data warehouse (AWS)                                                                                 | `uv add redshift-connector boto3`                   |
| **Snowflake**             | Available | Data Cloud / Multi-cloud data warehouse                                                                    | `uv add snowflake-connector-python`                 |
| **Trino**                 | Available | Distributed SQL (Trino/Starburst)                                                                          | `uv add benchbox --extra trino`                   |
| **PrestoDB**              | Available | Distributed SQL (Meta's Presto)                                                                            | `uv add benchbox --extra presto`                  |
| **LakeSail Sail**         | Available | Rust drop-in Spark replacement (SQL + DataFrame via Spark Connect)                                         | `uv add benchbox --extra lakesail`                  |
| **Apache Gluten + Velox** | Available | Native C++ acceleration for Spark SQL (Linux-only local; Docker on macOS/Windows)                          | `uv add benchbox --extra velox`                     |
| **SQLite**                | Built-in  | Embedded transactional database                                                                            | (built-in)                                          |
| **Azure Platforms**       | Available | Microsoft Fabric Warehouse, Azure Synapse Analytics, Microsoft Fabric Spark, Azure Synapse Analytics Spark | See [Azure Platforms](azure-platforms.md)           |

### DataFrame Platforms (Native API)

BenchBox supports benchmarking DataFrame libraries using their native APIs instead of SQL. This enables direct performance comparison between SQL and DataFrame paradigms on identical workloads. See [DataFrame Platforms](dataframe.md) for full details.

| Platform       | CLI Name        | Status    | Family     | Description                                            | Installation                         |
| -------------- | --------------- | --------- | ---------- | ------------------------------------------------------ | ------------------------------------ |
| **Polars**     | `polars-df`     | Available | Expression | Fast Rust-based DataFrame library with lazy evaluation | (core dependency)                    |
| **Pandas**     | `pandas-df`     | Available | Pandas     | Reference Pandas implementation                        | `uv add benchbox --extra pandas`     |
| **PySpark**    | `pyspark-df`    | Available | Expression | Apache Spark DataFrame API (distributed)               | `uv add benchbox --extra pyspark`    |
| **DataFusion** | `datafusion-df` | Available | Expression | Arrow-native query engine                              | `uv add benchbox --extra datafusion` |
| **LakeSail**   | `lakesail-df`   | Available | Expression | Rust/DataFusion Spark replacement via Spark Connect    | `uv add benchbox --extra lakesail`   |
| Dask           | `dask-df`       | Available | Pandas     | Parallel computing DataFrames                          | `uv add benchbox --extra dask`       |
| cuDF           | `cudf-df`       | Available | Pandas     | NVIDIA GPU-accelerated DataFrames                      | `uv add benchbox --extra cudf`       |

**Quick Start:**

```bash
benchbox run --platform polars-df --benchmark tpch --scale 0.1
benchbox run --platform pandas-df --benchmark tpch --scale 0.1
benchbox run --platform pyspark-df --benchmark tpch --scale 0.1
benchbox run --platform datafusion-df --benchmark tpch --scale 0.1
benchbox run --platform lakesail-df --benchmark tpch --scale 0.1
```

## Quick Start

### 1. Install Dependencies

Install all cloud platforms at once:

```bash
uv add benchbox --extra cloud
```

Or install individual platforms:

```bash
uv add benchbox --extra clickhouse-local

uv add benchbox --extra clickhouse-server

uv add benchbox --extra clickhouse-cloud

uv add benchbox --extra databricks
```

The extras install ClickHouse Local (chDB, zero-config), ClickHouse Server (self-hosted), ClickHouse Cloud (managed) and Databricks SQL.

### 2. Use the Platform Management CLI

BenchBox now includes a dedicated CLI for managing database platforms. This simplifies installation, configuration, and validation.

**List all available platforms and their status:**

```bash
benchbox platforms list
```

**Check the status of a specific platform (e.g., Databricks SQL):**

```bash
benchbox platforms status databricks
```

**Check local provisioning readiness before a run:**

```bash
benchbox platforms check clickhouse-server trino lakesail-df dask-df
benchbox platforms status lakesail-df
```

The readiness check reports unreachable local service ports and LakeSail Spark Connect endpoints
backend packages as environment readiness gaps. It does not start services, initialize Ray/Dask, or mutate benchmark
databases.

**Install missing libraries for a platform (guided):**

```bash
benchbox platforms install bigquery
```

**Enable or disable a platform:**

```bash
benchbox platforms enable snowflake
benchbox platforms disable sqlite
```

**Run an interactive setup wizard:**

```bash
benchbox platforms setup
```

### 3. Run Multi-Platform Benchmark

```python
from benchbox.platforms import get_platform_adapter
from benchbox import TPCH

benchmark = TPCH(scale_factor=0.1)

platforms = ["duckdb", "clickhouse-local"]

for platform_name in platforms:
    print(f"Running on {platform_name}...")

    try:
        adapter = get_platform_adapter(platform_name)
        results = adapter.run_benchmark(benchmark)

        print(f"Completed in {results.duration_seconds:.2f}s")
        print(f"Average query time: {results.average_query_time:.3f}s")
    except Exception as e:
        print(f"Could not run on {platform_name}: {e}")
```

---

## DuckDB

**Type**: In-process analytical database
**Common Use Cases**: Development, testing, small to medium-scale analytics workloads

### Configuration

```python
from benchbox.platforms.duckdb import DuckDBAdapter

adapter = DuckDBAdapter()

adapter = DuckDBAdapter(database_path="benchmark.duckdb")
```

The first adapter uses an in-memory database (the default). The second uses a persistent file database.

---

## Apache DataFusion

**Type**: In-memory query engine (Apache Arrow-based)
**Common Use Cases**: In-process analytics, rapid prototyping, PyArrow workflows, memory-constrained OLAP

### Configuration

```python
from benchbox.platforms.datafusion import DataFusionAdapter

adapter = DataFusionAdapter(
    working_dir="./datafusion_working",
    memory_limit="16G",
    data_format="parquet"
)

adapter = DataFusionAdapter(
    memory_limit="4G",
    data_format="csv",
    target_partitions=4
)
```

The first adapter is the recommended in-memory analytics setup with Parquet. Use `data_format="csv"` for lower memory. The second adapter is a memory-constrained configuration.

---

## ClickHouse

**Type**: Column-oriented OLAP database
**Common Use Cases**: Analytical workloads, OLAP queries, real-time analytics

### Configuration

```python
from benchbox.platforms.clickhouse import ClickHouseAdapter

adapter = ClickHouseAdapter(
    host="localhost",
    port=9000,
    database="benchmark",
    username="default",
    password=""
)
```

Verify that the server is running, then check the credentials. The default user has an empty password.

---

## Databricks SQL

**Type**: Data Intelligence Platform (lakehouse architecture)
**Common Use Cases**: SQL analytics, ML/data science workflows, lakehouse deployments

### Configuration

```python
from benchbox.platforms.databricks import DatabricksAdapter

adapter = DatabricksAdapter(
    server_hostname="dbc-12345678-abcd.cloud.databricks.com",
    http_path="/sql/1.0/warehouses/abcd1234efgh5678",
    access_token="<token>",
    catalog="hive_metastore",
    schema="default"
)
```

---

## BigQuery

**Type**: Serverless data warehouse (Google Cloud)
**Common Use Cases**: Large-scale analytics, petabyte-scale datasets, Google Cloud-native applications

### Configuration

```python
from benchbox.platforms.bigquery import BigQueryAdapter

adapter = BigQueryAdapter(
    project_id="my-benchbox-project",
    dataset_id="benchbox_test",
    credentials_path="/path/to/service-account-key.json",
    location="US"
)
```

---

## Redshift

**Type**: Cloud data warehouse (AWS)
**Common Use Cases**: AWS-native analytics, variable workloads, serverless or provisioned deployments

### Configuration

```python
from benchbox.platforms.redshift import RedshiftAdapter

adapter = RedshiftAdapter(
    host="benchbox-workgroup.123456.us-east-1.redshift-serverless.amazonaws.com",
    port=5439,
    database="benchbox",
    username="admin",
    password="SecurePassword123",
    is_serverless=True,
    workgroup_name="benchbox-workgroup"
)
```

---

## Snowflake

**Type**: Data Cloud (multi-cloud data warehouse)
**Common Use Cases**: Enterprise analytics, multi-cloud deployments, elastic scaling workloads

### Configuration

```python
from benchbox.platforms.snowflake import SnowflakeAdapter

adapter = SnowflakeAdapter(
    account="xy12345.us-east-1",
    username="benchbox_user",
    password="secure_password_123",
    compute_resource="COMPUTE_WH",
    database="BENCHBOX",
    schema="PUBLIC"
)
```

---

## SQLite

**Type**: Embedded transactional database
**Common Use Cases**: Testing, development, small datasets, CI/CD validation

### Configuration

```python
from benchbox.platforms.sqlite import SQLiteAdapter

adapter = SQLiteAdapter()

adapter = SQLiteAdapter(database_path="benchmark.db")
```

The first adapter uses an in-memory database. The second uses a file-based database.

---

## LakeSail Sail

**Type**: Rust-based drop-in Apache Spark replacement (DataFusion core)
**Common Use Cases**: Migrating off Spark with zero client-code changes; benchmarking SQL + DataFrame on the same Rust engine

### Configuration

```bash
uv add benchbox --extra lakesail

uv add pysail
python -m pysail spark server --port 50051

benchbox run --platform lakesail --benchmark tpch --scale 1.0

benchbox run --platform lakesail-df --benchmark tpch --scale 1.0

benchbox run --platform lakesail --benchmark tpch --scale 10.0
```

In order, these commands install the Spark Connect-capable PySpark client, install the Sail server (`pysail`), start it in a separate terminal, run SQL mode, run DataFrame mode and run at a larger scale. Select distributed mode with `--platform lakesail:distributed`; `--platform-option endpoint=sc://host:50051` points the run at the Sail server.

See [LakeSail Platform Guide](lakesail.md) for the full configuration reference.

---

## Apache Gluten + Velox

**Type**: Native C++ query-acceleration plugin for Apache Spark (via the Velox engine)
**Common Use Cases**: Accelerating existing Spark SQL deployments without changing client code; high-performance native execution on Linux

### Configuration

```{important}
Local mode is **Linux-only** - the Gluten Velox bundle jar has no macOS or Windows build. Use Docker (`docker/velox/`) or a remote Linux host on macOS and Windows. The checked-in Docker workflow currently defaults to `linux/amd64`; on Apple Silicon that is for smoke testing only, not timing-valid benchmarks.
```

```bash
uv add benchbox --extra velox

benchbox run --platform velox --benchmark tpch --scale 0.1 \
    --platform-option gluten_jar_path=/opt/gluten-velox-bundle-spark4.0_2.13-linux_amd64-1.6.0.jar \
    --platform-option offheap_size=8g

benchbox run --platform velox:remote \
    --platform-option endpoint=sc://localhost:50051 \
    --benchmark tpch --scale 0.1
```

The first command installs the Velox extra, which pulls `pyspark[connect]>=3.5.0`. The first `benchbox run` is local mode, an in-process SparkSession with the Gluten bundle jar loaded. The second is remote mode, which connects to a pre-started Gluten-enabled Spark Connect server.

See [Velox Platform Guide](velox.md) and [Velox Jar Setup](velox_jar_setup.md) for Gluten bundle jar URLs, checksums, and the provided `benchbox-velox` Docker image.

---

## Troubleshooting

### Common Issues Across Platforms

#### Connection Errors

**Problem**: Unable to connect to database

**Solutions by Platform**:

**DuckDB**:

```python
import os
os.access("benchmark.duckdb", os.W_OK)

adapter = DuckDBAdapter(database_path="/full/path/to/benchmark.duckdb")
```

Check that the file is writable, then use an absolute path.

**ClickHouse**:

```python
import socket
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
result = sock.connect_ex(('localhost', 9000))
if result == 0:
    print("ClickHouse is running")

adapter = ClickHouseAdapter(
    host="localhost",
    port=9000,
    username="default",
    password=""
)
```

**Cloud Platforms (Databricks SQL, BigQuery, Snowflake, Redshift)**:

```python
import os
print(f"DATABRICKS_TOKEN: {'SET' if os.getenv('DATABRICKS_TOKEN') else 'NOT SET'}")
print(f"DATABRICKS_HOST: {os.getenv('DATABRICKS_HOST')}")

from benchbox.platforms.databricks import DatabricksAdapter
adapter = DatabricksAdapter()
try:
    adapter.test_connection()
    print("Connection successful")
except Exception as e:
    print(f"Connection failed: {e}")
```

Verify the environment variables, then test the connection before running the benchmark.

#### Authentication Issues

**BigQuery**:

```bash
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/service-account-key.json"

gcloud auth application-default login
```

Set the credentials with the service account key, or use application default credentials with `gcloud auth application-default login`.

**Databricks SQL**:

```bash
export DATABRICKS_TOKEN="dapi..."
export DATABRICKS_HOST="https://your-workspace.cloud.databricks.com"

databricks configure --token
```

Use a personal access token, or configure credentials through the Databricks CLI with `databricks configure --token`.

**Snowflake**:

```python
from benchbox.platforms.snowflake import SnowflakeAdapter
adapter = SnowflakeAdapter(
    account="xy12345",
    username="user",
    private_key_path="/path/to/rsa_key.p8",
    private_key_passphrase="passphrase"
)
```

#### Out of Memory Errors

**DuckDB**:

```python
adapter = DuckDBAdapter(memory_limit="4GB")

adapter = DuckDBAdapter(
    database_path="large_dataset.duckdb",
    memory_limit="8GB"
)
```

Set a memory limit, and use a persistent database for large datasets.

**ClickHouse**:

```python
adapter = ClickHouseAdapter(
    host="localhost",
    settings={
        "max_memory_usage": "10000000000",
        "max_bytes_before_external_sort": "5000000000"
    }
)
```

Increase the memory limits. The `max_memory_usage` value of 10000000000 is 10 GB.

**Cloud Platforms**:

```python
from benchbox.platforms.bigquery import BigQueryAdapter
adapter = BigQueryAdapter(
    maximum_bytes_billed=10000000000,
    use_query_cache=True
)

from benchbox.platforms.snowflake import SnowflakeAdapter
adapter = SnowflakeAdapter(
    compute_resource="LARGE_WH",
    compute_size="LARGE"
)
```

BigQuery: use the query cache. `maximum_bytes_billed=10000000000` is a 10 GB limit.

Snowflake: increase the warehouse size. Besides `LARGE_WH`, use `X-LARGE` or `2X-LARGE`.

#### Slow Query Performance

**General Debugging**:

```python
import logging
logging.basicConfig(level=logging.DEBUG)

adapter = DuckDBAdapter(enable_profiling=True)

benchmark = TPCH(scale_factor=0.01)
```

Enable verbose logging, run with profiling, and test with a smaller scale factor first (start small).

**Platform-Specific Optimizations**:

**DuckDB**:

```python
adapter = DuckDBAdapter(thread_limit=8)

adapter = DuckDBAdapter(database_path="cached.duckdb")
```

Increase the thread count, and use a persistent database.

**ClickHouse**:

```python
adapter = ClickHouseAdapter(
    host="localhost",
    settings={
        "max_threads": 8,
        "optimize_read_in_order": 1,
        "enable_filesystem_cache": 1
    }
)
```

**Cloud Platforms**:

```python
adapter = DatabricksAdapter(
    http_path="/sql/1.0/warehouses/large-warehouse"
)

adapter = BigQueryAdapter(
    job_priority="BATCH",
    use_legacy_sql=False
)
```

Databricks SQL: use a larger cluster.

BigQuery: use batch priority, which is slower but cheaper.

#### Data Loading Failures

**Check file format**:

```python
conn.execute("""
    CREATE TABLE test AS
    SELECT * FROM read_parquet('data/*.parquet')
""")

conn.execute("""
    CREATE TABLE test AS
    SELECT * FROM read_csv('data/*.csv',
                          delim='|',
                          header=false,
                          auto_detect=true)
""")
```

DuckDB reads multiple formats. The first query reads Parquet. The second reads CSV with an explicit delimiter and schema detection.

**Verify file paths**:

```python
from pathlib import Path

data_dir = Path("./tpch_data")
if not data_dir.exists():
    print(f"Data directory not found: {data_dir}")
else:
    files = list(data_dir.glob("*.parquet"))
    print(f"Found {len(files)} data files")
```

### Platform-Specific Issues

#### DuckDB

**Issue**: Database file is locked

```python
adapter1 = DuckDBAdapter(database_path="db1.duckdb")
adapter2 = DuckDBAdapter(database_path="db2.duckdb")
```

Ensure no other process is using the file, or use separate database files as shown.

#### ClickHouse

**Issue**: "Memory limit exceeded" errors

```python
adapter = ClickHouseAdapter(
    settings={
        "max_memory_usage": "20000000000",
        "max_bytes_before_external_group_by": "10000000000",
        "max_bytes_before_external_sort": "10000000000"
    }
)
```

Increase the limits or enable external operations (spilling group-by and sort to disk).

#### Databricks SQL

**Issue**: "Cluster not found" or "Warehouse not available"

```python
from benchbox.platforms.databricks import DatabricksAdapter

adapter = DatabricksAdapter()
warehouses = adapter.list_warehouses()
print(f"Available warehouses: {warehouses}")

adapter = DatabricksAdapter(
    http_path="/sql/1.0/warehouses/abc123def456"
)
```

Verify the HTTP path: list the available warehouses (`list_warehouses` may not be implemented in every release), then use the correct HTTP path format.

#### BigQuery

**Issue**: "Exceeded quota" or billing errors

```python
from benchbox.platforms.bigquery import BigQueryAdapter

adapter = BigQueryAdapter(
    maximum_bytes_billed=5000000000,
    job_priority="BATCH",
    use_query_cache=True,
    dry_run=True
)
```

Set cost controls. The 5000000000 limit is 5 GB, `BATCH` priority lowers cost, `use_query_cache` reuses cached results, and `dry_run` tests without execution first.

#### Snowflake

**Issue**: Warehouse auto-suspended

```python
from benchbox.platforms.snowflake import SnowflakeAdapter

adapter = SnowflakeAdapter(
    compute_resource="COMPUTE_WH",
    auto_resume=True,
    auto_suspend=300
)
```

Configure auto-resume. `auto_suspend=300` suspends the warehouse after 5 minutes.

### Getting Help

If you encounter issues not covered here:

1. **Check logs**: Enable verbose logging with `--verbose` flag
2. **Test connection**: Use platform's native client to verify connectivity
3. **Review documentation**: See platform-specific guides below
4. **Check GitHub issues**: Search for similar problems
5. **Create an issue**: Report bugs with reproducible examples

## See Also

### Platform Documentation

- **[DataFrame Platforms Guide](dataframe.md)** - Native DataFrame API benchmarking (Polars, Pandas, PySpark, DataFusion)
- **[Platform Selection Guide](platform-selection-guide.md)** - Comprehensive platform comparison and selection criteria
- **[Platform Comparison Matrix](comparison-matrix.md)** - Feature and performance comparison table
- **[ClickHouse Local](clickhouse-local-mode.md)** - Running ClickHouse locally for development
- **[ClickHouse Server](clickhouse-server.md)** - Self-hosted ClickHouse via clickhouse-driver
- **[ClickHouse Migration Guide](clickhouse-migration.md)** - Migrating from legacy `clickhouse` selector
- **[LakeSail Sail](lakesail.md)** - Rust drop-in Spark replacement (SQL + DataFrame via Spark Connect)
- **[Apache Gluten + Velox](velox.md)** - Native C++ acceleration for Spark SQL (Linux-only local, Docker elsewhere)
- **[Velox Jar Setup](velox_jar_setup.md)** - Gluten bundle jar URLs and checksums
- **[Development Roadmap](../development/roadmap.md)** - Planned platform and benchmark additions

### API Reference

- **[Python API Overview](../reference/python-api/index.md)** - Complete Python API documentation
- **[DuckDB Adapter API](../reference/python-api/platforms/duckdb.md)** - DuckDB adapter reference
- **[Base Benchmark API](../reference/python-api/base.md)** - Core benchmark interface

### Getting Started

- **[Getting Started Guide](../usage/getting-started.md)** - Run your first benchmark in 5 minutes
- **[Installation Guide](../usage/installation.md)** - Installation and setup instructions
- **[CLI Quick Reference](../usage/cli-quick-start.md)** - Command-line usage guide
- **[Configuration Handbook](../usage/configuration.md)** - Advanced configuration options

### Benchmarks

- **[Benchmark Catalog](../benchmarks/index.md)** - Available benchmarks overview
- **[TPC-H Benchmark](../benchmarks/tpc-h.md)** - Standard analytical benchmark
- **[TPC-DS Benchmark](../benchmarks/tpc-ds.md)** - Complex decision support queries
- **[ClickBench](../benchmarks/clickbench.md)** - Real-world analytics benchmark

### Advanced Topics

- **[Performance Guide](../advanced/performance.md)** - Performance tuning and optimization
- **[Cloud Storage](../guides/cloud-storage.md)** - S3, GCS, Azure Blob Storage integration
- **[Compression](../guides/compression.md)** - Data compression strategies
- **[Dry Run Mode](../usage/dry-run.md)** - Preview queries without execution
- **[Troubleshooting Guide](../usage/troubleshooting.md)** - Comprehensive troubleshooting
