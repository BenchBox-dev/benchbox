<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Databend Platform

```{tags} intermediate, guide, databend, sql-platform, cloud, olap, cloud-native
```

Databend is a cloud-native, Rust-based data warehouse with Snowflake-compatible SQL, compute/storage separation, and object storage backends (S3, GCS, Azure Blob, MinIO). BenchBox connects via the `databend-driver` Python package (DB-API 2.0 compliant) and uses the Snowflake dialect as a SQLGlot translation proxy for SQL compatibility.

Databend supports two deployment modes: **Databend Cloud** (managed service) and **self-hosted** (user-managed cluster with object storage). Its vectorized query engine, written in Rust, is optimized for analytical workloads with automatic micro-partitioning and clustering key support.

## Features

- **Cloud-native architecture** - Compute/storage separation on object storage (S3, MinIO, GCS, Azure Blob)
- **Snowflake-compatible SQL** - Uses Snowflake dialect as SQLGlot translation proxy (~100% compatibility)
- **Full TPC-H support** - All 22 queries with row count validation
- **Full TPC-DS support** - All 99 queries with row count validation
- **Vectorized Rust engine** - High-performance analytical query execution
- **DB-API 2.0 driver** - Standard Python database connectivity via `databend-driver`
- **Clustering keys** - `CLUSTER BY` clause for optimizing data layout
- **Automatic statistics** - No explicit ANALYZE needed; statistics collected by storage engine
- **Result cache control** - Configurable query result cache (disabled by default for benchmarking)
- **Dual deployment** - Databend Cloud (managed) or self-hosted with MinIO/S3

## Quick Start

Install the `databend-driver` dependency directly or through the Databend extra, then configure the connection for Databend Cloud and run TPC-H:

```bash
uv add databend-driver

uv add benchbox --extra databend

export DATABEND_HOST=tenant--warehouse.gw.databend.com
export DATABEND_USER=benchbox
export DATABEND_PASSWORD=your_password

benchbox run --platform databend --benchmark tpch --scale 0.01
```

### Self-Hosted Quick Start

The Docker image needs MinIO or S3-compatible storage. Select the self-hosted deployment with `databend:self-hosted`. It connects over plain HTTP on port 8000 unless you set `ssl` or `port`, and the result is recorded as the self-hosted deployment, separate from Databend Cloud runs:

```bash
docker run -p 8000:8000 datafuselabs/databend:latest

export DATABEND_HOST=localhost

benchbox run --platform databend:self-hosted --benchmark tpch --scale 0.01
```

### DSN-Based Connection

```bash
benchbox run --platform databend --benchmark tpch --scale 0.01 \
    --platform-option dsn=databend+http://benchbox:benchbox@localhost:8000/benchbox?sslmode=disable
```

## Platform Options

| Option | Environment Variable | Default | Description |
|--------|---------------------|---------|-------------|
| `host` | `DATABEND_HOST` | - | Databend host (required unless DSN is set) |
| `port` | `DATABEND_PORT` | `443` (SSL) / `8000` (no SSL) | Connection port |
| `username` | `DATABEND_USER` | `benchbox` | Database username |
| `password` | `DATABEND_PASSWORD` | - | Database password |
| `database` | `DATABEND_DATABASE` | `benchbox` | Target database name |
| `dsn` | `DATABEND_DSN` | - | Full DSN string (overrides individual params) |
| `warehouse` | `DATABEND_WAREHOUSE` | - | Databend Cloud warehouse name |
| `ssl` | - | `true` for `databend` and `databend:cloud`, `false` for `databend:self-hosted` | SSL/TLS for connections |
| `disable_result_cache` | - | `true` | Disable query result cache for benchmarking |

### DSN Format

The DSN (Data Source Name) follows the format:

```
databend+https://user:password@host:port/database?warehouse=name
databend+http://user:password@host:port/database?sslmode=disable
```

- `databend+https://` is used when SSL is enabled (default for cloud)
- `databend+http://` is used when SSL is disabled (typical for self-hosted) and should include `sslmode=disable`
- Special characters in username/password are automatically URL-encoded

## Data Loading

BenchBox loads data into Databend using batch INSERT statements via the `databend-driver` Python client. The adapter handles both TPC pipe-delimited (`.tbl`) and standard CSV formats automatically.

### Loading Process

1. **Database creation** - `CREATE DATABASE IF NOT EXISTS` ensures target database exists
2. **Schema creation** - Tables created with Snowflake-compatible DDL, optimized for Databend
3. **Type conversion** - `CHAR(n)` converted to `VARCHAR(n)` (Databend preference); constraints removed
4. **Batch inserts** - Data loaded in batches of 500 rows using `INSERT INTO ... VALUES` statements
5. **Constraint handling** - Primary keys and foreign keys are removed (Databend does not enforce them)

### Type Mappings

| Source Type | Databend Type |
|-------------|--------------|
| `CHAR(n)` | `VARCHAR(n)` |
| `INTEGER` | `INTEGER` (unchanged) |
| `VARCHAR(n)` | `VARCHAR(n)` (unchanged) |
| `DECIMAL(p,s)` | `DECIMAL(p,s)` (unchanged) |
| `DATE` | `DATE` (unchanged) |
| `DOUBLE` | `DOUBLE` (unchanged) |

### Large-Scale Loading

For datasets larger than SF 10, Databend supports high-throughput loading via:

- **COPY INTO** from staged S3/MinIO files (for cloud and self-hosted with object storage)
- **Streaming Load** via HTTP API for parallel ingestion

The current adapter uses INSERT batching for broad compatibility across both deployment modes.

## Usage Examples

### Basic Benchmarks

```bash
benchbox run --platform databend --benchmark tpch --scale 1.0

benchbox run --platform databend --benchmark tpcds --scale 10.0

benchbox run --platform databend --benchmark tpch --queries Q1,Q6,Q17
```

### Databend Cloud Configuration

```bash
export DATABEND_HOST=tenant--warehouse.gw.databend.com
export DATABEND_USER=benchbox
export DATABEND_PASSWORD=your_cloud_password
export DATABEND_WAREHOUSE=my_warehouse

benchbox run --platform databend --benchmark tpch --scale 10.0
```

### Self-Hosted Configuration

Select the self-hosted deployment. SSL is off and the port is 8000 by default:

```bash
export DATABEND_HOST=localhost

benchbox run --platform databend:self-hosted --benchmark tpch --scale 1.0
```

Add `--platform-option ssl=true` to a self-hosted run that terminates TLS.

### Custom Database Name

```bash
benchbox run --platform databend --benchmark tpch --scale 1.0 \
    --platform-option database=my_benchmarks
```

### Dry Run (Preview)

```bash
benchbox run --platform databend --benchmark tpch --scale 1.0 --dry-run ./preview
```

## Architecture

### Adapter Structure

The Databend adapter is a single-file adapter with all functionality in one class:

| Module | Class | Responsibility |
|--------|-------|---------------|
| `adapter.py` | `DatabendAdapter` | Connection, schema, loading, queries, tuning |
| `__init__.py` | `_build_databend_config` | Config builder with credential loading |

### Connection Model

```
BenchBox CLI
    |
    v
DatabendAdapter
    |
    +-- databend-driver (DB-API 2.0)
    |       - BlockingDatabendClient
    |       - DSN-based connection (databend+http:// or databend+https://)
    |
    +-- Databend Cloud (HTTPS, port 443)
    |       - tenant--warehouse.gw.databend.com
    |       - Warehouse-scoped compute
    |
    +-- Self-hosted (HTTP, port 8000)
            - Direct connection to Databend server
            - Object storage backend (MinIO/S3)
```

### SQL Translation Strategy

Databend claims ~100% Snowflake SQL compatibility. BenchBox leverages this by:

1. Translating benchmark SQL from DuckDB dialect to **Snowflake dialect** via SQLGlot
2. Sending Snowflake-compatible SQL directly to Databend
3. Applying edge-case optimizations in `_optimize_table_definition()`:
   - `CHAR(n)` to `VARCHAR(n)` conversion
   - Primary key constraint removal
   - Foreign key constraint removal

### Platform Information

At runtime, BenchBox captures platform metadata:

```python
{
    "platform_type": "databend",
    "platform_name": "Databend",
    "configuration": {
        "host": "tenant--warehouse.gw.databend.com",
        "database": "benchbox_tpch_sf1"
    },
    "warehouse": "my_warehouse",
    "platform_version": "v1.x.x",
    "client_library_version": "0.x.x"
}
```

## Tuning and Optimization

### Automatic Benchmark Configuration

The adapter automatically applies optimizations when running benchmarks:

- **Query result cache**: Disabled by default (`enable_query_result_cache = 0`) for accurate timing
- **Vectorized engine**: Pre-optimized for analytical workloads (no additional tuning needed)
- **Automatic statistics**: Databend collects statistics via its storage engine; no explicit `ANALYZE` required

### Supported Tuning Types

| Tuning Type | Support | Notes |
|-------------|---------|-------|
| Clustering | Yes | `CLUSTER BY` clause for data layout optimization |
| Partitioning | No | Automatic micro-partitioning (not user-configurable) |
| Distribution | No | Managed by storage engine |

### Clustering Keys

Databend supports `CLUSTER BY` to optimize data layout for frequently queried columns, similar to Snowflake clustering keys. BenchBox applies this automatically via the tuning configuration:

```sql
ALTER TABLE lineitem CLUSTER BY (l_shipdate, l_orderkey)
```

Clustering keys can be specified at table creation time (via `generate_tuning_clause`) or applied post-creation (via `apply_table_tunings`).

### Custom Platform Settings

```bash
benchbox run --platform databend --benchmark tpch --scale 10.0 \
    --tuning tuned
```

## Troubleshooting

### Connection Refused

```
Error: Databend configuration is incomplete
```

**Solutions:**
1. Verify Databend is running and accessible on the configured host and port
2. For cloud: Set `DATABEND_HOST=tenant--warehouse.gw.databend.com`
3. For self-hosted: Set `DATABEND_HOST=localhost` and `DATABEND_PORT=8000`
4. Provide either DSN, individual params, or environment variables

### Missing databend-driver Dependency

```
Error: Missing dependencies for databend platform: databend-driver
```

**Solutions:**
1. Install databend-driver: `uv add databend-driver`
2. Or install the Databend extra: `uv add benchbox --extra databend`
3. Minimum version required: `>=0.28.0`

### SSL/TLS Errors

```
Error: SSL handshake failed
```

**Solutions:**
1. For self-hosted without TLS: `--platform-option ssl=false`
2. For cloud: SSL is required; verify certificate chain
3. Check that the port matches the SSL setting (443 for SSL, 8000 for non-SSL)

### Schema Creation Failures

```
Error: Schema creation failed
```

**Solutions:**
1. Check that the user has `CREATE TABLE` and `CREATE DATABASE` permissions
2. Verify the database exists or the user has `CREATE DATABASE` privilege
3. If tables already exist, the adapter will automatically drop and recreate them
4. Review Databend query logs for detailed error messages

### Slow Data Loading

**Solutions:**
1. For large datasets (SF 10+), consider using Databend's native `COPY INTO` from staged files
2. Ensure adequate network bandwidth between BenchBox host and Databend
3. For self-hosted: verify object storage (MinIO/S3) performance
4. Consider running BenchBox on a machine co-located with the Databend cluster

### Query Result Cache Interference

```
Warning: Query results may be cached
```

**Solutions:**
1. Verify `disable_result_cache` is `true` (default): `--platform-option disable_result_cache=true`
2. The adapter sets `enable_query_result_cache = 0` at session level
3. For Databend Cloud: check that warehouse-level caching is not interfering

## See Also

- [Platform Comparison Matrix](comparison-matrix.md) - Compare all platforms
- [Platform Selection Guide](platform-selection-guide.md) - Choosing the right platform
- [TPC-H Benchmark](../benchmarks/tpc-h.md) - TPC-H benchmark guide
- [TPC-DS Benchmark](../benchmarks/tpc-ds.md) - TPC-DS benchmark guide
- [Deployment Modes Guide](deployment-modes.md) - Platform deployment architecture
