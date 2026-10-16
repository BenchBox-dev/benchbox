<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Platform Deployment Modes

```{tags} guide, architecture
```

BenchBox supports multiple deployment modes for database platforms, enabling you to run the same benchmark against local, self-hosted, and cloud-managed instances of the same database engine. This guide explains the deployment mode architecture, configuration, and usage patterns.

## Overview

A **deployment mode** represents how a database platform is deployed and accessed:

| Mode | Description | Examples |
|------|-------------|----------|
| **local** | Embedded/in-process, no external server required | DuckDB, chDB (ClickHouse local), Firebolt Core |
| **self-hosted** | User-managed server or cluster | ClickHouse Server, Trino, TimescaleDB |
| **managed** | Vendor-managed cloud service | MotherDuck, ClickHouse Cloud, Starburst Galaxy, Timescale Cloud |

### Why Deployment Modes Matter

- **Same dialect, different infrastructure**: MotherDuck uses DuckDB's SQL dialect but requires cloud authentication
- **Consistent benchmarking**: Compare local vs cloud performance with identical queries
- **Flexible configuration**: Each mode has specific authentication and connection requirements

## Quick Start

### Syntax

Use the first-class platform names or the colon syntax to specify deployment modes:

```bash
benchbox run --platform clickhouse-local --benchmark tpch --scale 0.1
benchbox run --platform clickhouse-server --benchmark tpch --scale 0.1
benchbox run --platform clickhouse-cloud --benchmark tpch --scale 0.1
```

### Platform-Specific Examples

ClickHouse runs locally (chDB), on a self-hosted server, or in ClickHouse Cloud, each as its own platform. Firebolt has a local Docker mode (`core`) and a cloud mode (`cloud`). InfluxDB has a self-hosted mode (`core`) and a managed service (`cloud`). pg-duckdb runs self-hosted by default, or with MotherDuck cloud offload as `pg-duckdb:motherduck`. TimescaleDB runs self-hosted by default, or in Timescale Cloud with `timescaledb:cloud`. Databend runs in Databend Cloud by default, or on a user-managed cluster with `databend:self-hosted`. MotherDuck and Starburst are standalone cloud platforms that support managed mode only.

```bash
benchbox run --platform clickhouse-local --benchmark tpch --scale 0.1
benchbox run --platform clickhouse-server --benchmark tpch --scale 0.1

benchbox run --platform clickhouse-cloud --benchmark tpch --scale 0.1 \
    --platform-option host=abc123.aws.clickhouse.cloud

benchbox run --platform firebolt:core --benchmark tpch --scale 0.1
benchbox run --platform firebolt:cloud --benchmark tpch --scale 0.1
benchbox run --platform influxdb:core --benchmark tsbs-devops --scale 1.0
benchbox run --platform influxdb:cloud --benchmark tsbs-devops --scale 1.0

benchbox run --platform pg-duckdb:self-hosted --benchmark tpch --scale 0.1
benchbox run --platform pg-duckdb:motherduck --benchmark tpch --scale 0.1

benchbox run --platform timescaledb --benchmark tpch --scale 0.1
benchbox run --platform timescaledb:cloud --benchmark tpch --scale 0.1

benchbox run --platform databend --benchmark tpch --scale 0.1
benchbox run --platform databend:self-hosted --benchmark tpch --scale 0.1

benchbox run --platform motherduck --benchmark tpch --scale 0.1
benchbox run --platform starburst --benchmark tpch --scale 0.1
```

### Selecting a deployment from Python

`DatabaseConfig` takes the same selector as `--platform` in its `type` field. Put the deployment after a colon:

```python
from benchbox.core.config import DatabaseConfig

tigerdata = DatabaseConfig(type="timescaledb:cloud", name="tigerdata", options={"host": "abc.tsdb.cloud.timescale.com"})
sail = DatabaseConfig(type="lakesail-df:distributed", name="sail", options={"endpoint": "sc://sail-host:50051"})

print(tigerdata.type, sail.type, sail.execution_mode)
```

```text
timescaledb lakesail dataframe
```

The config resolves the selector when it is built, exactly as the CLI does: `type` becomes the base platform, the selected deployment is passed to the adapter, and a DataFrame alias such as `lakesail-df` sets `execution_mode="dataframe"` unless you set it yourself. Credential lookups, database file names, DDL generation and dry-run output all use the base platform, and the dry run records the selected deployment. A mode the platform does not declare raises `pydantic.ValidationError`. The deprecated `deployment_mode` option still works with a deprecation warning; the selector is the replacement.

## Platform Deployment Reference

### Platforms with Multiple Deployment Modes

| Platform | Default Mode | Available Modes | Notes |
|----------|--------------|-----------------|-------|
| **ClickHouse** | `local` | `local`, `server` | Prefer first-class names: `clickhouse-local`, `clickhouse-server` |
| **Firebolt** | `core` | `core`, `cloud` | Core is free local Docker deployment |
| **TimescaleDB** | `self-hosted` | `self-hosted`, `cloud` | Cloud mode for Timescale Cloud |
| **Databend** | `cloud` | `cloud`, `self-hosted` | `self-hosted` connects over HTTP on port 8000 unless `ssl` or `port` is set |
| **PySpark** | `local` | `local` | Local single-node Spark |
| **LakeSail** | `local` | `local`, `distributed` | Sail Rust engine; SQL (`lakesail`) and DataFrame (`lakesail-df`) share modes |
| **Velox** | `local` | `local`, `remote` | Gluten + Velox on Spark; `local` is Linux-only (use Docker on macOS/Windows) |
| **InfluxDB** | `cloud` | `core`, `cloud` | `core` is self-hosted OSS, `cloud` is the managed service |
| **DuckLake** | `local` | `local`, `local_catalog_s3`, `postgres_catalog`, `postgres_catalog_s3` | Modes mix catalog and storage axes; the selector and the `deployment_mode` option agree |
| **pg-duckdb** | `self-hosted` | `self-hosted`, `motherduck` | `motherduck` needs a `MOTHERDUCK_TOKEN` for hybrid queries |

### Deprecated deployment aliases

The platform options below are deprecated aliases of the `--platform name:mode` selector. They keep working through the 0.6.0 removal window, warn once per run, and resolve to the same deployment as the selector: Velox `deployment`, LakeSail `deployment_mode` and `sail_mode`, InfluxDB `deployment_mode` and `mode`, ClickHouse `deployment_mode` and `mode`, Firebolt `deployment_mode` and `firebolt_mode`, DuckLake `deployment_mode`, pg-duckdb `deployment_mode`, TimescaleDB `deployment_mode`, Databend `deployment_mode`. If the selector and an alias disagree, the run fails before any work starts.

### First-Class Cloud Platforms

| Platform | CLI Name | Inherits From | Notes |
|----------|----------|---------------|-------|
| **ClickHouse Cloud** | `clickhouse-cloud` | ClickHouse | Managed ClickHouse service (uses clickhouse-connect) |
| **MotherDuck** | `motherduck` | DuckDB | Serverless DuckDB cloud |
| **Starburst** | `starburst` | Trino | Starburst Galaxy managed Trino |
| **Snowflake** | `snowflake` | - | Multi-cloud data warehouse |
| **Databricks** | `databricks` | - | Lakehouse platform |
| **BigQuery** | `bigquery` | - | GCP serverless warehouse |
| **Redshift** | `redshift` | - | AWS data warehouse |

### Platforms Without Deployment Modes (`:local` Suffix)

Platforms that declare no deployment modes — including cloud-only platforms
such as Snowflake and Databricks, as well as embedded engines such as Polars
and SQLite — accept only the `:local` suffix. The suffix is a no-op alias for
the bare platform name, kept so the CLI selector resolver, the adapter
factory, and `PlatformRegistry.supports_deployment_mode` agree. Any other
suffix is rejected with a "Remove the ':…' suffix" error.

```bash
benchbox run --platform snowflake:local --benchmark tpch --scale 1.0
benchbox run --platform databricks:local --benchmark tpch --scale 1.0
```

```{note}
The `:local` suffix on a platform without deployment modes names the
platform's implicit (and only) deployment — it does not mean local
execution. A `snowflake:local` run still executes against Snowflake Cloud.
```

### Configuration Inheritance

Some platforms inherit SQL dialect and configuration from parent platforms:

| Platform | Parent | Inherited Features |
|----------|--------|-------------------|
| MotherDuck | DuckDB | SQL dialect, data types, benchmark compatibility |
| Starburst | Trino | SQL dialect, connector syntax, session properties |

This means MotherDuck automatically uses DuckDB's query translator and Starburst uses Trino's.

---

## ClickHouse Deployment Modes

ClickHouse has three first-class platform names, plus a legacy `clickhouse` selector for backwards compatibility.

```{note}
The bare `clickhouse` selector has been removed (it now raises an error). The colon syntax (`clickhouse:local`, `clickhouse:server`) is deprecated but still works. Use the first-class names below. See [Migration Guide](clickhouse-migration.md).
```

### clickhouse-local (chDB)

Zero-configuration ClickHouse local mode via the chDB library.

```bash
uv add benchbox --extra clickhouse-local

benchbox run --platform clickhouse-local --benchmark tpch --scale 0.1
```

Installing the extra pulls in chDB.

**Characteristics:**
- No server installation required
- In-process execution using chDB
- Same query engine as ClickHouse server
- Ideal for development and testing

### clickhouse-server (Self-Hosted)

Connect to a self-hosted ClickHouse server or cluster.

```bash
uv add benchbox --extra clickhouse-server

export CLICKHOUSE_HOST=localhost
export CLICKHOUSE_PORT=9000
export CLICKHOUSE_USER=default
export CLICKHOUSE_PASSWORD=secret

benchbox run --platform clickhouse-server --benchmark tpch --scale 1.0

benchbox run --platform clickhouse-server --benchmark tpch --scale 1.0 \
    --platform-option host=clickhouse.example.com \
    --platform-option port=9000 \
    --platform-option username=benchuser \
    --platform-option password=secret
```

The first run uses the environment variables. The second passes the same settings inline as platform options.

**Connection Parameters:**

| Parameter | Environment Variable | Default | Description |
|-----------|---------------------|---------|-------------|
| `host` | `CLICKHOUSE_HOST` | `localhost` | Server hostname |
| `port` | `CLICKHOUSE_PORT` | `9000` | Native protocol port |
| `username` | `CLICKHOUSE_USER` | `default` | Database username |
| `password` | `CLICKHOUSE_PASSWORD` | - | Database password |
| `database` | - | `default` | Target database |
| `secure` | - | `false` | Enable TLS |

### ClickHouse Cloud (First-Class Platform)

ClickHouse Cloud is now a **first-class platform** (`clickhouse-cloud`), not a deployment mode. This follows the pattern established by MotherDuck (DuckDB) and Starburst (Trino).

```bash
uv add benchbox --extra clickhouse-cloud

export CLICKHOUSE_CLOUD_HOST=abc123.us-east-2.aws.clickhouse.cloud
export CLICKHOUSE_CLOUD_PASSWORD=your-password
export CLICKHOUSE_CLOUD_USER=default

benchbox run --platform clickhouse-cloud --benchmark tpch --scale 1.0

benchbox run --platform clickhouse-cloud --benchmark tpch --scale 1.0 \
    --platform-option host=abc123.us-east-2.aws.clickhouse.cloud \
    --platform-option password=your-password
```

`CLICKHOUSE_CLOUD_USER` is optional and defaults to `default`. The second run passes the connection settings inline as platform options.

**Connection Parameters:**

| Parameter | Environment Variable | Default | Description |
|-----------|---------------------|---------|-------------|
| `host` | `CLICKHOUSE_CLOUD_HOST` | - | Cloud hostname (required) |
| `password` | `CLICKHOUSE_CLOUD_PASSWORD` | - | Password (required) |
| `username` | `CLICKHOUSE_CLOUD_USER` | `default` | Username |
| `port` | - | `8443` | HTTPS port |
| `database` | - | `default` | Target database |

**Notes:**
- Always uses HTTPS (port 8443)
- Compression enabled by default for network efficiency
- Result cache disabled by default for accurate benchmarking

For full documentation, see [ClickHouse Cloud Platform](clickhouse-cloud.md).

---

## Firebolt Deployment Modes

Firebolt provides both a free local deployment (Core) and managed cloud service.

### Core Mode (Local Docker)

Free, self-hosted Firebolt via Docker with the same query engine as cloud.

```bash
docker run -i --rm --ulimit memlock=8589934592:8589934592 \
  --security-opt seccomp=unconfined -p 127.0.0.1:3473:3473 \
  -v ./firebolt-core-data:/firebolt-core/volume \
  ghcr.io/firebolt-db/firebolt-core:preview-rc

uv add firebolt-sdk

benchbox run --platform firebolt:core --benchmark tpch --scale 0.1 \
    --platform-option url=http://localhost:3473
```

The `docker run` command starts Firebolt Core, `uv add` installs the SDK, and the last command runs the benchmark against it.

**Configuration:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `url` | `http://localhost:3473` | Firebolt Core endpoint |
| `database` | `benchbox` | Database name (auto-created) |

**Characteristics:**
- No authentication required
- Same vectorized query engine as cloud
- Ideal for development and local testing
- Databases created implicitly on connection

### Cloud Mode (Firebolt Cloud)

Connect to Firebolt Cloud managed service with OAuth authentication.

```bash
export FIREBOLT_CLIENT_ID=your-client-id
export FIREBOLT_CLIENT_SECRET=your-client-secret
export FIREBOLT_ACCOUNT_NAME=your-account
export FIREBOLT_ENGINE_NAME=your-engine

export SERVICE_ACCOUNT_ID=your-client-id
export SERVICE_ACCOUNT_SECRET=your-client-secret

benchbox run --platform firebolt:cloud --benchmark tpch --scale 1.0

benchbox run --platform firebolt:cloud --benchmark tpch --scale 1.0 \
    --platform-option client_id=your-client-id \
    --platform-option client_secret=your-client-secret \
    --platform-option account_name=your-account \
    --platform-option compute_resource=your-engine
```

The `SERVICE_ACCOUNT_ID` and `SERVICE_ACCOUNT_SECRET` variables are alternative names for the client ID and secret, and are also supported. The second run passes the settings inline as platform options.

**Connection Parameters:**

| Parameter | Environment Variable | Required | Description |
|-----------|---------------------|----------|-------------|
| `client_id` | `FIREBOLT_CLIENT_ID` / `SERVICE_ACCOUNT_ID` | Yes | OAuth client ID |
| `client_secret` | `FIREBOLT_CLIENT_SECRET` / `SERVICE_ACCOUNT_SECRET` | Yes | OAuth client secret |
| `account_name` | `FIREBOLT_ACCOUNT_NAME` | Yes | Firebolt account name |
| `compute_resource` | `FIREBOLT_ENGINE_NAME` | Yes | Engine to use for queries |
| `database` | `FIREBOLT_DATABASE` | No | Database name (default: `benchbox`) |
| `api_endpoint` | `FIREBOLT_API_ENDPOINT` | No | API endpoint (default: `api.app.firebolt.io`) |

**Notes:**
- Create service account credentials in Firebolt console: Settings > Service Accounts
- Result cache disabled by default for accurate benchmarking
- Cloud mode requires all four credential parameters

---

## TimescaleDB Deployment Modes

TimescaleDB extends PostgreSQL with time-series capabilities and supports both self-hosted and managed cloud deployments.

### Self-Hosted Mode (Default)

Connect to a self-hosted TimescaleDB server.

```bash
uv add benchbox --extra postgresql

benchbox run --platform timescaledb --benchmark tpch --scale 0.1 \
    --platform-option host=localhost \
    --platform-option username=postgres \
    --platform-option password=secret
```

**Connection Parameters:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `host` | `localhost` | TimescaleDB server hostname |
| `port` | `5432` | PostgreSQL port |
| `username` | `postgres` | Database username |
| `password` | - | Database password |
| `database` | auto-generated | Target database |
| `sslmode` | `prefer` | SSL connection mode |

### Cloud Mode (Timescale Cloud)

Connect to Timescale Cloud managed service.

```bash
export TIMESCALE_SERVICE_URL=postgres://user:pass@abc123.tsdb.cloud.timescale.com:5432/tsdb?sslmode=require

export TIMESCALE_HOST=abc123.rc8ft3nbrw.tsdb.cloud.timescale.com
export TIMESCALE_PASSWORD=your-password
export TIMESCALE_USER=tsdbadmin

benchbox run --platform timescaledb:cloud --benchmark tpch --scale 1.0
```

Set either the service URL (preferred) or the individual host and password variables. `TIMESCALE_USER` is optional and defaults to `tsdbadmin`.

**Connection Parameters:**

| Parameter | Environment Variable | Default | Description |
|-----------|---------------------|---------|-------------|
| `service_url` | `TIMESCALE_SERVICE_URL` | - | Full connection URL (preferred) |
| `host` | `TIMESCALE_HOST` | - | Cloud hostname (required if no URL) |
| `password` | `TIMESCALE_PASSWORD` / `PGPASSWORD` | - | Password (required) |
| `username` | `TIMESCALE_USER` / `PGUSER` | `tsdbadmin` | Username |
| `port` | `TIMESCALE_PORT` / `PGPORT` | `5432` | PostgreSQL port |
| `database` | `TIMESCALE_DATABASE` / `PGDATABASE` | `tsdb` | Database name |

**Service URL Format:**
```
postgres://username:password@hostname:port/database?sslmode=require
```

**Notes:**
- Cloud mode always uses SSL (`sslmode=require`)
- Database management (DROP/CREATE) disabled in cloud mode
- Find your service URL in the Timescale Cloud console

---

## MotherDuck (Cloud DuckDB)

MotherDuck is a managed cloud service for DuckDB, inheriting DuckDB's SQL dialect.

```bash
uv add duckdb

export MOTHERDUCK_TOKEN=your-token

benchbox run --platform motherduck --benchmark tpch --scale 1.0
```

The DuckDB package includes MotherDuck support. BenchBox reads the token from `MOTHERDUCK_TOKEN`; there is no platform option for it.

**Configuration:**

| Parameter | Environment Variable | Default | Description |
|-----------|---------------------|---------|-------------|
| `token` | `MOTHERDUCK_TOKEN` | - | MotherDuck auth token (required) |
| `database` | - | `benchbox` | MotherDuck database name |
| `memory_limit` | - | `4GB` | Local memory limit for hybrid queries |

**Get Your Token:**
Visit [app.motherduck.com/token-request](https://app.motherduck.com/token-request)

**Characteristics:**
- Uses DuckDB SQL dialect
- Supports hybrid local/cloud queries
- Automatic data transfer to MotherDuck cloud

---

## Starburst (Managed Trino)

Starburst Galaxy is a managed Trino service, inheriting Trino's SQL dialect.

```bash
uv add trino

export STARBURST_HOST=my-cluster.trino.galaxy.starburst.io
export STARBURST_USER=joe@example.com/accountadmin
export STARBURST_PASSWORD=your-password

benchbox run --platform starburst --benchmark tpch --scale 1.0
```

Starburst has no platform options for these settings; set them in the environment as shown.

**Connection Parameters:**

| Parameter | Environment Variable | Required | Description |
|-----------|---------------------|----------|-------------|
| `host` | `STARBURST_HOST` | Yes | Galaxy cluster hostname |
| `username` | `STARBURST_USER` / `STARBURST_USERNAME` | Yes | User email or email/role |
| `password` | `STARBURST_PASSWORD` | Yes | Password or API key |
| `role` | `STARBURST_ROLE` | No | Role (appended to username if not included) |
| `catalog` | `STARBURST_CATALOG` | No | Default catalog |
| `port` | `STARBURST_PORT` | No | Port (default: 443) |

**Username Format:**
```
email/role (e.g., joe@example.com/accountadmin)
```

If you provide the role separately via `STARBURST_ROLE`, it will be automatically appended to the username.

**Characteristics:**
- Uses Trino SQL dialect
- Always uses HTTPS (port 443)
- Supports Iceberg, Hive, and Delta table formats
- SSL verification enabled by default

---

## LakeSail Deployment Modes

LakeSail Sail is a Rust-based drop-in replacement for Apache Spark. Both SQL (`lakesail`) and DataFrame (`lakesail-df`) modes share the same deployment model - the adapter connects to a running Sail server via the Spark Connect protocol using the standard `pyspark` client.

### Local Mode (Default)

Single-node multi-threaded execution. Start a Sail server locally, then connect over Spark Connect.

```bash
uv add benchbox --extra lakesail

uv add pysail
python -m pysail spark server --port 50051

benchbox run --platform lakesail --benchmark tpch --scale 1.0

benchbox run --platform lakesail-df --benchmark tpch --scale 1.0
```

The extra installs the Spark Connect-capable PySpark client, `pysail` installs the Sail server, and `python -m pysail spark server` starts it locally (run it in a separate terminal and leave it running). The two runs are the SQL and DataFrame benchmarks. The CLI accepts the `--platform-option` keys `endpoint` and `adaptive_enabled`, and `--platform lakesail:distributed` selects distributed mode. Other tuning overrides need the Python adapter (see the LakeSail Platform Guide).

### Distributed Mode

Connects to an external Sail server and never starts a local one. The recorded mode says only that the server was external, not how many nodes it has.

```python
from benchbox.platforms.lakesail import LakeSailAdapter

adapter = LakeSailAdapter(
    deployment_mode="distributed",
    endpoint="sc://my-sail-cluster:50051",
)
```

**Connection Parameters** (`endpoint` and `adaptive_enabled` are also `--platform-option` keys; the rest are adapter constructor only):

| Parameter | Default | Notes |
|-----------|---------|-------|
| `endpoint` | `sc://localhost:50051` | Spark Connect URL |
| `deployment_mode` | `local` | `local` or `distributed`. Deprecated alias of `--platform lakesail:local\|distributed` |
| `sail_workers` | - | Has no effect; BenchBox logs a warning and does not record it |
| `driver_memory` | `4g` | Driver memory allocation |
| `shuffle_partitions` | `200` | Shuffle partition count |
| `table_format` | `parquet` | `parquet` or `orc` |
| `adaptive_enabled` | `true` | Adaptive Query Execution (AQE) |

See [LakeSail Platform Guide](lakesail.md) for the full option reference.

**Characteristics:**
- No JVM on the execution path - Rust/DataFusion engine only
- Zero-rewrite migration from PySpark - standard PySpark client
- SQL and DataFrame modes share the same engine and configuration

---

## Velox (Apache Gluten) Deployment Modes

Apache Gluten offloads Spark SQL physical operators to [Velox](https://velox-lib.io/), a vectorized C++ query engine. The Velox adapter supports two deployment modes: `local` (in-process SparkSession with the Gluten jar loaded) and `remote` (connect to a pre-started Spark Connect server that already has Gluten wired in).

```{important}
**Linux-only runtime.** The Gluten Velox bundle jar is Linux-only - there are no prebuilt jars for macOS or Windows, and native builds on those hosts are not supported. The checked-in Docker workflow currently targets `linux/amd64` because Apache Gluten 1.6.0 does not publish an official Spark 3.5 arm64 jar. On Apple Silicon, that Docker path is suitable for smoke testing under emulation, but not for timing-valid benchmarks.
```

### Local Mode (Default)

In-process SparkSession with Gluten plugged in. Linux x86_64 host with the official jar, a custom-built Linux arm64 jar, or inside the `benchbox-velox` Docker container.

```bash
uv add benchbox --extra velox

benchbox run --platform velox --benchmark tpch --scale 0.1 \
    --platform-option gluten_jar_path=/opt/gluten-velox-bundle-spark4.0_2.13-linux_amd64-1.6.0.jar \
    --platform-option offheap_size=8g
```

The Velox extra pulls in `pyspark[connect]>=3.5.0`. Provide the Gluten bundle jar yourself; see `velox_jar_setup.md` for release tarballs and verification steps.

The adapter sets the mandatory Gluten configuration automatically:

- `spark.plugins = org.apache.gluten.GlutenPlugin`
- `spark.memory.offHeap.enabled = true`
- `spark.memory.offHeap.size = <offheap_size>`
- `spark.shuffle.manager = org.apache.spark.shuffle.sort.ColumnarShuffleManager`
- `spark.jars = <gluten_jar_path>`
- `spark.driver.extraClassPath = <gluten_jar_path>`
- `spark.executor.extraClassPath = <gluten_jar_path>`

The `extraClassPath` entries are required: the Gluten plugin class is loaded before `spark.jars` promotions reach the executor classpath, so without them the plugin silently no-ops.

Overriding `spark.shuffle.manager` via `spark_config` raises `ValueError` - `ColumnarShuffleManager` is required for shuffle acceleration.

### Remote Mode (Spark Connect)

Connect to a pre-started Gluten-enabled Spark Connect server (including the `benchbox-velox` Docker image). Works on any host that can reach the endpoint - the server itself must still run on Linux.

```bash
docker compose up -d velox-connect

benchbox run --platform velox:remote \
    --platform-option endpoint=sc://localhost:50051 \
    --benchmark tpch --scale 0.1
```

The `docker compose` command starts the Gluten-enabled Spark Connect server (the provided Docker image), and the `benchbox run` command drives the benchmark from the host.

**Configuration Reference:**

Options marked "yes" are passed as `--platform-option KEY=VALUE`.

| Parameter | CLI | Default | Notes |
|-----------|-----|---------|-------|
| `deployment` | deprecated alias; use `--platform velox:local` or `--platform velox:remote` | `local` | `local` (in-process, Linux-only) or `remote` (Spark Connect) |
| `endpoint` | yes | `sc://localhost:50051` | Spark Connect URL for `remote` mode |
| `gluten_jar_path` | yes (alias: `jar`) | - | Absolute path to the Gluten bundle jar (required for `local` mode) |
| `offheap_size` | yes | `8g` | `spark.memory.offHeap.size` for Velox |
| `driver_memory` | yes | `4g` | JVM driver heap |
| `shuffle_partitions` | yes | `200` | `spark.sql.shuffle.partitions` |
| `adaptive_enabled` | yes | `true` | Adaptive Query Execution (AQE) |
| `gluten_version` | no | `1.6.0` | Informational; surfaced in `platform_info` |

The table format is set with the `benchbox run --table-format` option.

### Verifying Velox Is Active

After a run, use the platform info probe and query plan inspection to confirm Velox handled the queries rather than silently falling back to JVM execution:

- `get_platform_info()` runs `EXPLAIN SELECT count(*) FROM range(10)` and returns `velox_active: true/false`.
- `get_query_plan()` labels each plan with `Velox native execution: YES/NOT DETECTED` and flags real JVM fallbacks (without false-positives on `VeloxColumnarToRow`).

See [Velox Platform Guide](velox.md) and [Velox Jar Setup](velox_jar_setup.md) for release tarball URLs, verification steps, and Docker build instructions.

---

## Architecture: DeploymentCapability System

The deployment mode system is built on the `DeploymentCapability` dataclass in `platform_registry.py`:

```python
@dataclass
class DeploymentCapability:

    mode: Literal["local", "self-hosted", "managed"]
    requires_credentials: bool = False
    requires_cloud_storage: bool = False
    requires_network: bool = False
    default_for_platform: bool = False
    display_name: str = ""
    description: str = ""
    dependencies: list[str] = field(default_factory=list)
    auth_methods: list[str] = field(default_factory=list)
```

### Registry API

Query deployment information programmatically:

```python
from benchbox.core.platform_registry import PlatformRegistry

modes = PlatformRegistry.get_available_deployment_modes("clickhouse")

default = PlatformRegistry.get_default_deployment("clickhouse")

supported = PlatformRegistry.supports_deployment_mode("clickhouse", "server")

cap = PlatformRegistry.get_deployment_capability("clickhouse", "server")

needs_storage = PlatformRegistry.requires_cloud_storage_for_deployment("clickhouse", "server")
```

For the base ClickHouse platform, these calls return:

- `get_available_deployment_modes`: `['local', 'server']`
- `get_default_deployment`: `'local'`
- `supports_deployment_mode("clickhouse", "server")`: `True`
- `get_deployment_capability("clickhouse", "server")`: `DeploymentCapability(mode='self-hosted', requires_credentials=True, ...)`
- `requires_cloud_storage_for_deployment("clickhouse", "server")`: `False`

ClickHouse Cloud is a separate first-class platform, not a deployment mode. Use `--platform clickhouse-cloud` (see `clickhouse-cloud.md`).

### Platform Family and Inheritance

Platforms can inherit SQL dialect and configuration from parent platforms:

```python
family = PlatformRegistry.get_platform_family("motherduck")

parent = PlatformRegistry.get_inherited_platform("motherduck")

parent = PlatformRegistry.get_inherited_platform("starburst")
```

`get_platform_family("motherduck")` returns `'duckdb'` and is used for dialect inheritance. `get_inherited_platform` is used for configuration inheritance: it returns `'duckdb'` for MotherDuck and `'trino'` for Starburst.

---

## Authentication Best Practices

### Environment Variables (Recommended)

Store credentials in environment variables to avoid exposing them in command history:

```bash
export CLICKHOUSE_CLOUD_HOST=...
export CLICKHOUSE_CLOUD_PASSWORD=...

export FIREBOLT_CLIENT_ID=...
export FIREBOLT_CLIENT_SECRET=...
export FIREBOLT_ACCOUNT_NAME=...
export FIREBOLT_ENGINE_NAME=...

export MOTHERDUCK_TOKEN=...

export STARBURST_HOST=...
export STARBURST_USER=...
export STARBURST_PASSWORD=...

export TIMESCALE_SERVICE_URL=...
```

### Credential Manager

BenchBox includes a credential manager for secure storage. It covers Databricks,
Snowflake, BigQuery, Redshift, Athena, MotherDuck, and SingleStore; other
platforms use the environment variables above.

```bash
benchbox setup --platform snowflake

benchbox run --platform snowflake --benchmark tpch --scale 1.0
```

`benchbox setup` saves credentials securely, and later runs load them automatically.

### Platform-Specific Notes

| Platform | Auth Method | Notes |
|----------|-------------|-------|
| ClickHouse Cloud | Password | Basic auth over HTTPS |
| Firebolt Cloud | OAuth | Service account credentials |
| MotherDuck | Token | Single token authentication |
| Starburst | Password/API Key | Email/role username format |
| Timescale Cloud | Password | PostgreSQL-style auth with SSL |

---

## Troubleshooting

### Common Errors

**"Invalid deployment mode"**
```
ValueError: Invalid ClickHouse deployment mode 'cluster'. Valid modes: local, server
```
Solution: Use one of the valid modes for the platform.

**"Missing required credentials"**
```
ValueError: ClickHouse Cloud requires host configuration.
```
Solution: Set the required environment variables or use `--platform-option`.

**"Connection refused"**
```
ConnectionRefusedError: Cannot connect to Firebolt Core at http://localhost:3473
```
Solution: Ensure the local service (Docker container) is running.

### Debug Mode

Enable verbose logging to debug connection issues:

```bash
benchbox run --platform clickhouse-cloud --benchmark tpch --scale 0.1 -v

benchbox run --platform clickhouse-cloud --benchmark tpch --scale 0.1 -vv
```

`-vv` is very verbose and includes connection parameters.

---

## See Also

- [Platform Comparison Matrix](comparison-matrix.md) - Compare all platforms
- [Platform Selection Guide](platform-selection-guide.md) - Choose the right platform
- [ClickHouse Local Mode](clickhouse-local-mode.md) - Detailed chDB guide
- [Firebolt](firebolt.md) - Detailed Firebolt guide
- [LakeSail Sail](lakesail.md) - Rust-based Spark replacement
- [Apache Gluten + Velox](velox.md) - Native C++ acceleration for Spark SQL
- [Velox Jar Setup](velox_jar_setup.md) - Gluten release tarball URLs, verification steps, and extracted jar names
- [Getting Started](../usage/getting-started.md) - Quick start guide
