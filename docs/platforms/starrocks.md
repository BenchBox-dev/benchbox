<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# StarRocks Platform

```{tags} intermediate, guide, starrocks, sql-platform, self-hosted, olap
```

StarRocks is an open-source MPP (Massively Parallel Processing) columnar OLAP database, hosted under the Linux Foundation. It is designed for sub-second analytics on large-scale datasets. BenchBox connects via the MySQL protocol using PyMySQL (port 9030) and supports Stream Load via the BE HTTP API (port 8040) for high-throughput data ingestion. SQLGlot provides official `starrocks` dialect support for SQL translation.

StarRocks is used in production by Airbnb, Alibaba, Coinbase, Pinterest, and Tencent, among others. It delivers competitive performance against ClickHouse, Trino, and Druid on analytical workloads.

## Features

- **MySQL protocol connectivity** - Standard MySQL wire protocol via PyMySQL (port 9030)
- **Stream Load HTTP API** - High-throughput data ingestion via BE HTTP port 8040
- **Full TPC-H support** - All 22 queries with row count validation
- **Full TPC-DS support** - All 99 queries with row count validation
- **Official SQLGlot dialect** - Native `starrocks` dialect for accurate SQL translation
- **Columnar storage** - Duplicate Key model optimized for OLAP workloads
- **Distributed hash partitioning** - Automatic data distribution across nodes
- **Tuning support** - Partitioning, sorting, and distribution tuning at table creation time

## Quick Start

Install the PyMySQL dependency directly or through the StarRocks extra. StarRocks must be running before you configure the connection and run TPC-H:

```bash
uv add pymysql

uv add benchbox --extra starrocks

export STARROCKS_HOST=localhost
export STARROCKS_PORT=9030

benchbox run --platform starrocks --benchmark tpch --scale 0.01
```

### Docker Quick Start

The Docker image runs both the FE and BE. After it starts, verify connectivity with the `mysql` client, then run the benchmark:

```bash
docker run -p 9030:9030 -p 8030:8030 -p 8040:8040 \
    starrocks/allin1-ubuntu:latest

mysql -h 127.0.0.1 -P 9030 -u root -e "SELECT 1"

benchbox run --platform starrocks --benchmark tpch --scale 1.0
```

## Configuration Options

| Option | Environment Variable | Default | Description |
|--------|---------------------|---------|-------------|
| `host` | `STARROCKS_HOST` | `localhost` | StarRocks FE hostname |
| `port` | `STARROCKS_PORT` | `9030` | MySQL protocol port (FE) |
| `username` | `STARROCKS_USER` | `root` | Database username |
| `password` | `STARROCKS_PASSWORD` | `""` | Database password |
| `database` | `STARROCKS_DATABASE` | auto-generated | Target database name |
| `http_port` | `STARROCKS_HTTP_PORT` | `8040` | BE HTTP port for Stream Load |
| `max_execution_time` | - | `300` | Query timeout in seconds |
| `disable_result_cache` | - | `true` | Disable query cache for accurate benchmarking |
| `strict_validation` | - | `true` | Enable strict data validation |
| `deployment_mode` | - | `self-hosted` | Deployment mode identifier |

The first six options are also `--platform-option` names. `max_execution_time`, `disable_result_cache`, `strict_validation`, and `deployment_mode` are not; pass them to `StarRocksAdapter` in Python.

## Data Loading

BenchBox loads data into StarRocks using batch INSERT statements via the MySQL protocol. The adapter handles both TPC pipe-delimited (`.tbl`) and standard CSV formats automatically.

### Loading Process

1. **Schema creation** - Tables are created with StarRocks-optimized DDL (Duplicate Key model, hash distribution)
2. **Type conversion** - DuckDB/standard SQL types are mapped to StarRocks equivalents (e.g., `HUGEINT` to `LARGEINT`, `TEXT` to `VARCHAR(65533)`)
3. **Batch inserts** - Data is loaded in batches of 1,000 rows using parameterized `INSERT INTO ... VALUES` statements
4. **Constraint handling** - Foreign keys are removed (StarRocks does not enforce them); primary keys are converted to Duplicate Key model

### Stream Load (HTTP API)

StarRocks provides a high-throughput Stream Load API on the BE HTTP port (default 8040). While the current adapter uses batch INSERT for broad compatibility, the Stream Load endpoint is available for custom ingestion workflows:

- **Endpoint**: `http://<be_host>:8040/api/<database>/<table>/_stream_load`
- **Protocol**: HTTP PUT with CSV or JSON payload
- **Throughput**: Significantly higher than row-by-row INSERT for large datasets

### Type Mappings

| Source Type | StarRocks Type |
|-------------|---------------|
| `HUGEINT` | `LARGEINT` |
| `INTEGER` | `INT` |
| `STRING` / `TEXT` | `VARCHAR(65533)` |
| `DOUBLE PRECISION` | `DOUBLE` |
| `REAL` | `FLOAT` |
| `BLOB` | `VARCHAR(65533)` |
| `DECIMAL(p,s)` | `DECIMAL(p,s)` (unchanged) |
| `BOOLEAN` | `BOOLEAN` (unchanged) |

## Usage Examples

### Basic Benchmarks

```bash
benchbox run --platform starrocks --benchmark tpch --scale 1.0

benchbox run --platform starrocks --benchmark tpcds --scale 10.0

benchbox run --platform starrocks --benchmark tpch --queries Q1,Q6,Q17
```

### Environment Variable Configuration

```bash
export STARROCKS_HOST=starrocks-fe.example.com
export STARROCKS_PORT=9030
export STARROCKS_USER=benchbox
export STARROCKS_PASSWORD=secret
export STARROCKS_DATABASE=benchmark_db
export STARROCKS_HTTP_PORT=8040

benchbox run --platform starrocks --benchmark tpch --scale 10.0
```

### Custom Database Name

```bash
benchbox run --platform starrocks --benchmark tpch --scale 1.0 \
    --platform-option database=my_benchmarks
```

### Dry Run (Preview)

```bash
benchbox run --platform starrocks --benchmark tpch --scale 1.0 --dry-run ./preview
```

## Architecture

### Adapter Structure

The StarRocks adapter is composed of four mixins plus the main adapter class:

| Module | Class | Responsibility |
|--------|-------|---------------|
| `adapter.py` | `StarRocksAdapter` | Main adapter entry point, data integrity validation |
| `setup.py` | `StarRocksSetupMixin` | Connection management, database lifecycle (create/drop) |
| `workload.py` | `StarRocksWorkloadMixin` | Schema creation, data loading, query execution |
| `tuning.py` | `StarRocksTuningMixin` | Benchmark configuration, session settings, table tuning |
| `metadata.py` | `StarRocksMetadataMixin` | Platform info, CLI arguments, dialect selection |

### Connection Model

```
BenchBox CLI
    |
    v
StarRocksAdapter
    |
    +-- MySQL Protocol (PyMySQL) --> FE (port 9030)
    |       - Schema DDL
    |       - Query execution
    |       - Batch INSERT loading
    |
    +-- HTTP API (Stream Load) --> BE (port 8040)
            - High-throughput data ingestion
```

### Platform Information

At runtime, BenchBox captures platform metadata:

```python
{
    "platform_type": "starrocks",
    "platform_name": "StarRocks",
    "deployment_mode": "self-hosted",
    "configuration": {
        "host": "localhost",
        "port": 9030,
        "database": "benchbox_tpch_sf1",
        "http_port": 8040
    },
    "platform_version": "3.x.x",
    "client_library_version": "1.x.x"
}
```

## Tuning and Optimization

### Automatic Benchmark Configuration

The adapter automatically applies session-level optimizations when running benchmarks:

- **Query timeout**: Set via `query_timeout` (default: 300 seconds)
- **Query cache**: Disabled by default (`enable_query_cache = false`) for accurate timing
- **Planner timeout**: Extended for complex OLAP queries (`new_planner_optimize_timeout = 30000`)
- **Profiling**: Disabled during benchmarks (`enable_profile = false`) to reduce overhead

### Supported Tuning Types

| Tuning Type | Support | Notes |
|-------------|---------|-------|
| Partitioning | Yes | `PARTITION BY` clause at table creation |
| Sorting | Yes | Sort keys via data model key ordering |
| Distribution | Yes | `DISTRIBUTED BY HASH` clause at table creation |

### Table Model

StarRocks uses the **Duplicate Key** model by default, which is optimal for OLAP workloads. The adapter automatically:

1. Removes `PRIMARY KEY` and `FOREIGN KEY` constraints from source DDL
2. Adds `DUPLICATE KEY(<first_column>)` to table definitions
3. Adds `DISTRIBUTED BY HASH(<first_column>) BUCKETS 8` for data distribution

### Custom Platform Settings

Additional StarRocks session variables can be applied through tuning configuration:

```bash
benchbox run --platform starrocks --benchmark tpch --scale 10.0 \
    --tuning tuned
```

## Troubleshooting

### Connection Refused

```
Error: Failed to connect to StarRocks
```

**Solutions:**
1. Verify StarRocks FE is running and accessible on the configured host and port
2. Check that port 9030 (MySQL protocol) is open and not blocked by a firewall
3. For Docker deployments, ensure port mapping is correct (`-p 9030:9030`)
4. Test connectivity directly: `mysql -h <host> -P 9030 -u root`

### Missing PyMySQL Dependency

```
Error: Missing dependencies for starrocks platform: pymysql
```

**Solutions:**
1. Install PyMySQL: `uv add pymysql`
2. Or install the StarRocks extra: `uv add benchbox --extra starrocks`

### Schema Creation Failures

```
Error: Schema creation failed
```

**Solutions:**
1. Check that the StarRocks user has `CREATE TABLE` and `CREATE DATABASE` permissions
2. Verify the target database exists or the user has `CREATE DATABASE` privilege
3. Review StarRocks FE logs for detailed error messages
4. Ensure sufficient disk space and memory on BE nodes

### Slow Data Loading

**Solutions:**
1. Increase `batch_size` for larger datasets (default: 1,000 rows per batch)
2. Consider using Stream Load directly for datasets larger than SF 10
3. Ensure BE nodes have adequate memory and disk I/O capacity
4. Check network latency between BenchBox host and StarRocks cluster

### Query Timeout

```
Error: Query execution exceeded timeout
```

**Solutions:**
1. Increase the timeout through the Python API: `StarRocksAdapter(max_execution_time=600)`
2. Check StarRocks resource utilization (CPU, memory, disk I/O)
3. Verify data distribution is balanced across BE nodes
4. Review the query plan for performance bottlenecks

## See Also

- [Platform Comparison Matrix](comparison-matrix.md) - Compare all platforms
- [Platform Selection Guide](platform-selection-guide.md) - Choosing the right platform
- [TPC-H Benchmark](../benchmarks/tpc-h.md) - TPC-H benchmark guide
- [TPC-DS Benchmark](../benchmarks/tpc-ds.md) - TPC-DS benchmark guide
- [Deployment Modes Guide](deployment-modes.md) - Platform deployment architecture
