<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# MotherDuck Platform

```{tags} intermediate, guide, motherduck, sql-platform, cloud
```

MotherDuck is the managed cloud version of DuckDB, providing serverless analytics with cloud storage integration. BenchBox provides first-class MotherDuck support, inheriting DuckDB's SQL dialect and benchmark compatibility.

## Features

- **Serverless DuckDB** - No infrastructure management
- **DuckDB dialect** - Inherits SQL dialect from DuckDB
- **Token authentication** - Simple token-based auth
- **Hybrid queries** - Access both local and cloud data
- **Auto-scaling** - Automatic resource management

## Quick Start

```bash
uv add duckdb

export MOTHERDUCK_TOKEN=your-token

benchbox run --platform motherduck --benchmark tpch --scale 1.0
```

The first command installs DuckDB, which includes MotherDuck support. The second sets your token. The third runs the benchmark.

## Authentication

### Getting Your Token

1. Sign in to [app.motherduck.com](https://app.motherduck.com)
2. Navigate to [app.motherduck.com/token-request](https://app.motherduck.com/token-request)
3. Copy your authentication token

### Configuration

`benchbox run` reads the token from the `MOTHERDUCK_TOKEN` environment
variable. The token is not a `--platform-option` key.

```bash
export MOTHERDUCK_TOKEN=your-motherduck-token

benchbox run --platform motherduck --benchmark tpch --scale 0.1
```

## Configuration Options

The **Set with** column shows how to set each option: a `--platform-option`
key on `benchbox run`, or a `MotherDuckAdapter` argument in the Python API.

| Option | Set with | Environment Variable | Default | Description |
|--------|----------|---------------------|---------|-------------|
| `token` | Python API | `MOTHERDUCK_TOKEN` | - | Authentication token (required) |
| `database` | `--platform-option` | - | `benchbox` | MotherDuck database name |
| `memory_limit` | Python API | - | `4GB` | Local memory limit for hybrid queries |
| `driver_version` | `--platform-option` | - | (latest) | Pin the duckdb package version (e.g. `1.4.3`) |
| `driver_auto_install` | `--platform-option` | - | false | Auto-install the requested driver version via uv if missing |

### Testing a Specific DuckDB Version with MotherDuck

MotherDuck uses the `duckdb` package as its driver. To pin a specific version:

```bash
benchbox run --platform motherduck --benchmark tpch \
  --platform-option driver_version=1.4.3 \
  --platform-option driver_auto_install=true
```

See {ref}`driver-version-management` for the full guide, including why `uv run` may
revert a manually-installed version and how to work around it.

## Usage Examples

### Basic Benchmark

```bash
benchbox run --platform motherduck --benchmark tpch --scale 1.0

benchbox run --platform motherduck --benchmark tpcds --scale 10.0
```

The first command runs TPC-H at scale factor 1. The second runs TPC-DS at scale factor 10.

### Custom Database Name

```bash
benchbox run --platform motherduck --benchmark tpch --scale 1.0 \
    --platform-option database=my_benchmarks
```

### With Memory Limit

`memory_limit` sets local memory for hybrid queries. It is a
`MotherDuckAdapter` argument, not a `--platform-option` key; see the Python
API example below.

## Python API

```python
from benchbox import TPCH
from benchbox.platforms.motherduck import MotherDuckAdapter

adapter = MotherDuckAdapter(
    token="your-motherduck-token",
    database="benchmarks",
    memory_limit="4GB",
)

benchmark = TPCH(scale_factor=1.0)
benchmark.generate_data()
adapter.load_benchmark(benchmark)
results = adapter.run_benchmark(benchmark)
```

Pass `token` explicitly, or omit it to use the `MOTHERDUCK_TOKEN` environment variable.

## Architecture

MotherDuck inherits from DuckDB, which means:

- **SQL Dialect**: Uses DuckDB's SQL dialect for query translation
- **Data Types**: Same data type mappings as DuckDB
- **Benchmark Compatibility**: Supports all benchmarks that DuckDB supports

```python
from benchbox.core.platform_registry import PlatformRegistry

family = PlatformRegistry.get_platform_family("motherduck")

parent = PlatformRegistry.get_inherited_platform("motherduck")
```

`get_platform_family` returns `"duckdb"`, and `get_inherited_platform` also returns `"duckdb"`.

## Hybrid Queries

MotherDuck supports hybrid queries that access both local and cloud data. The adapter handles this automatically: local data is uploaded to MotherDuck during benchmark loading.

## Comparison: MotherDuck vs DuckDB

| Feature | MotherDuck | DuckDB |
|---------|------------|--------|
| Deployment | Cloud managed | Local embedded |
| Authentication | Token required | None |
| Scaling | Automatic | Manual |
| Data Location | Cloud | Local filesystem |
| Cost | Pay-per-use | Free |
| Best For | Production, large scale | Development, testing |

## When to Use MotherDuck

**Use MotherDuck when:**
- You need cloud-based analytics without infrastructure management
- Running benchmarks at larger scale factors (SF > 10)
- Sharing benchmark databases across team members
- Comparing local vs cloud DuckDB performance

**Use DuckDB instead when:**
- Developing and testing locally
- No cloud credentials available
- Cost is a primary concern
- Running small-scale benchmarks (SF < 1)

## Troubleshooting

### Invalid Token

```
ValueError: MotherDuck requires authentication token.
```

**Solution:** Ensure `MOTHERDUCK_TOKEN` is set, or pass `token=` to `MotherDuckAdapter` in the Python API.

### Connection Failed

```
ConnectionError: Failed to connect to MotherDuck
```

**Solutions:**
1. Verify your token at [app.motherduck.com](https://app.motherduck.com)
2. Check network connectivity
3. Ensure DuckDB >= 0.9.0 is installed (`uv add duckdb>=0.9.0`)

### Database Not Found

MotherDuck databases are created automatically when you first connect. If you see database-related errors:

```bash
benchbox run --platform motherduck --benchmark tpch --scale 0.1 \
    --platform-option database=fresh_benchmark_db
```

This specifies a fresh database name.

## Related Documentation

- [DuckDB Platform](duckdb.md) - Local DuckDB benchmarking
- [Deployment Modes Guide](deployment-modes.md) - Platform deployment architecture
- [Platform Selection Guide](platform-selection-guide.md) - Choose the right platform
- [Getting Started](../usage/getting-started.md) - Quick start guide
