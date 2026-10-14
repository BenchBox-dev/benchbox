<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# ClickHouse Cloud Platform

```{tags} intermediate, guide, clickhouse, sql-platform, cloud
```

ClickHouse Cloud is the managed cloud version of ClickHouse, providing serverless and dedicated compute options with automatic scaling. BenchBox provides first-class ClickHouse Cloud support, inheriting ClickHouse's SQL dialect and benchmark compatibility.

## Features

- **Managed ClickHouse** - No infrastructure management
- **ClickHouse dialect** - Inherits SQL dialect from ClickHouse
- **HTTPS connectivity** - Secure connection via port 8443
- **Serverless & dedicated** - Flexible compute options
- **Auto-scaling** - Automatic resource management
- **Compression** - Network-efficient data transfer

## Quick Start

```bash
uv add benchbox --extra clickhouse-cloud

export CLICKHOUSE_CLOUD_HOST=abc123.us-east-2.aws.clickhouse.cloud
export CLICKHOUSE_CLOUD_PASSWORD=your-password

benchbox run --platform clickhouse-cloud --benchmark tpch --scale 1.0
```

The first command installs the ClickHouse Cloud extra, the exports set your credentials, and the last command runs the benchmark.

## Authentication

### Getting Your Credentials

1. Sign in to [clickhouse.cloud](https://console.clickhouse.cloud/)
2. Navigate to your service
3. Click "Connect" to get your connection details:
   - **Host**: `abc123.us-east-2.aws.clickhouse.cloud`
   - **Port**: `8443` (HTTPS)
   - **Username**: Usually `default`
   - **Password**: Your service password

### Configuration

**Environment Variables (recommended):**

```bash
export CLICKHOUSE_CLOUD_HOST=abc123.us-east-2.aws.clickhouse.cloud
export CLICKHOUSE_CLOUD_PASSWORD=your-password
export CLICKHOUSE_CLOUD_USER=default

benchbox run --platform clickhouse-cloud --benchmark tpch --scale 1.0
```

`CLICKHOUSE_CLOUD_USER` is optional and defaults to `default`.

**CLI Options:**

```bash
benchbox run --platform clickhouse-cloud --benchmark tpch --scale 1.0 \
    --platform-option host=abc123.us-east-2.aws.clickhouse.cloud \
    --platform-option password=your-password \
    --platform-option username=default
```

## Configuration Options

| Option | Environment Variable | Default | Description |
|--------|---------------------|---------|-------------|
| `host` | `CLICKHOUSE_CLOUD_HOST` | - | Cloud hostname (required) |
| `password` | `CLICKHOUSE_CLOUD_PASSWORD` | - | Authentication password (required) |
| `username` | `CLICKHOUSE_CLOUD_USER` | `default` | Username |
| `database` | - | `default` | Target database |
| `max_memory_usage` | - | `0` | Max memory per query (bytes) |
| `max_execution_time` | - | `600` | Query timeout (seconds) |
| `disable_result_cache` | - | `true` | Disable result cache for benchmarking |
| `compression` | - | `true` | Enable network compression |
| `optimize_after_load` | - | `false` | Run `OPTIMIZE TABLE ... FINAL` on each table after it loads, on tuned runs only |
| `driver_version` | - | (latest) | Pin the clickhouse-connect package version (e.g. `0.10.0`) |
| `driver_auto_install` | - | false | Auto-install the requested driver version via uv if missing |

`max_memory_usage`, `max_execution_time`, `disable_result_cache`, and
`compression` are `ClickHouseCloudAdapter` arguments in the Python API.
`benchbox run` does not accept them as `--platform-option` keys.

### Testing a Specific clickhouse-connect Version

```bash
benchbox run --platform clickhouse-cloud --benchmark tpch \
  --platform-option driver_version=0.10.0 \
  --platform-option driver_auto_install=true \
  --platform-option host=your-instance.clickhouse.cloud \
  --platform-option password=your-password
```

The driver package for ClickHouse Cloud is `clickhouse-connect` (the HTTP-based connector).
This is distinct from the `[clickhouse]` extra which uses `clickhouse-driver` (the TCP binary protocol).

See {ref}`driver-version-management` for the full guide, including why `uv run` may
revert a manually-installed version and how to work around it.

## Usage Examples

### Basic Benchmark

```bash
benchbox run --platform clickhouse-cloud --benchmark tpch --scale 1.0

benchbox run --platform clickhouse-cloud --benchmark tpcds --scale 10.0

benchbox run --platform clickhouse-cloud --benchmark clickbench
```

The commands run TPC-H at scale factor 1, TPC-DS at scale factor 10, and ClickBench (ClickHouse's own benchmark).

### Custom Database Name

```bash
benchbox run --platform clickhouse-cloud --benchmark tpch --scale 1.0 \
    --platform-option database=my_benchmarks
```

### Performance Tuning

The per-query memory limit and timeout are Python API arguments:

```python
from benchbox.platforms.clickhouse_cloud import ClickHouseCloudAdapter

adapter = ClickHouseCloudAdapter(
    host="abc123.us-east-2.aws.clickhouse.cloud",
    password="your-password",
    max_memory_usage=16000000000,
    max_execution_time=300,
)
```

`max_memory_usage` raises the memory limit for complex queries, and
`max_execution_time` sets a query timeout in seconds.

### Tuned Runs

`--tuning tuned` applies a curated template for TPC-H, SSB and TPC-DS:
MergeTree sort keys, plus monthly partitions on the TPC-H `LINEITEM` and
`ORDERS` tables. The same templates serve the local, server and cloud platforms
(`examples/tunings/clickhouse/`, packaged with BenchBox). For other
benchmarks there is no curated template, so `--tuning tuned` resolves to the
fallback. ClickHouse's "OLAP session pack" session settings apply to tuned runs
either way.

```bash
benchbox run --platform clickhouse-cloud --benchmark tpch --tuning tuned
```

The templates disable every constraint, so a table the template sorts has no
`PRIMARY KEY` clause and its sort key is the index. A table the template does
not tune keeps the schema's primary key and derives its `ORDER BY` from it. If you supply a template that enables
`primary_keys`, its columns must be a prefix of the tuned sort key, or the run
fails before any table is created.

`OPTIMIZE TABLE ... FINAL` is off by default. To run it on each table after it
loads, set `--platform-option optimize_after_load=true`. It runs on tuned runs
only. Its time is reported as `phases.post_load_maintenance` and is not
counted in data-loading time.

### Dry Run (Preview)

```bash
benchbox run --platform clickhouse-cloud --benchmark tpch --scale 1.0 --dry-run ./preview
```

This previews what will be executed without running it.

## Comparison with Base ClickHouse

| Feature | ClickHouse (local) | ClickHouse (server) | ClickHouse Cloud |
|---------|-------------------|---------------------|------------------|
| **CLI Name** | `clickhouse-local` | `clickhouse-server` | `clickhouse-cloud` |
| **Infrastructure** | None (chDB) | Self-hosted | Managed |
| **Driver** | chdb | clickhouse-driver | clickhouse-connect |
| **Port** | N/A | 9000 (native) | 8443 (HTTPS) |
| **Authentication** | None | Optional | Required |
| **Best For** | Development | Production self-hosted | Production cloud |

## Platform Syntax

The canonical platform name is `clickhouse-cloud`. The `clickhouse:cloud` syntax is also accepted as an alias.

```bash
benchbox run --platform clickhouse-cloud --benchmark tpch --scale 1.0
```

## Troubleshooting

### Connection Refused

```
Error: Cannot connect to ClickHouse Cloud
```

**Solutions:**
1. Verify your host URL is correct (should end with `.clickhouse.cloud`)
2. Check that your IP is allowed in the ClickHouse Cloud console
3. Ensure the service is running (not paused)

### Authentication Failed

```
Error: Authentication failed for user 'default'
```

**Solutions:**
1. Verify your password is correct
2. Check if the user has the required permissions
3. Try resetting the password in the ClickHouse Cloud console

### Missing Host Configuration

```
Error: ClickHouse Cloud requires host configuration.
```

**Solutions:**
1. Set `CLICKHOUSE_CLOUD_HOST` environment variable
2. Or use `--platform-option host=your-host.clickhouse.cloud`

## Platform Information

At runtime, BenchBox captures platform metadata:

```python
{
    "platform_type": "clickhouse-cloud",
    "platform_name": "ClickHouse Cloud",
    "connection_mode": "cloud",
    "configuration": {
        "deployment": "managed",
        "host": "abc123.us-east-2.aws.clickhouse.cloud",
        "port": 8443,
        "secure": True
    }
}
```

## See Also

- [ClickHouse Local Mode](clickhouse-local-mode.md) - Embedded ClickHouse via chDB
- [Deployment Modes Guide](deployment-modes.md) - Platform deployment architecture
- [Platform Comparison Matrix](comparison-matrix.md) - Compare all platforms
- [TPC-H Benchmark](../benchmarks/tpc-h.md) - TPC-H benchmark guide
