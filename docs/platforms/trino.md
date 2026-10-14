<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Trino Platform

```{tags} intermediate, guide, trino, sql-platform
```

Trino (formerly PrestoSQL) is a distributed SQL query engine designed for interactive analytics against data sources of all sizes. It's widely used by companies like Netflix, Airbnb, and Lyft for data lake analytics.

## Features

- **Distributed execution** - Query data across multiple workers
- **Federated queries** - Join data from multiple sources (S3, Hive, Iceberg, Delta)
- **Session properties** - Fine-grained query optimization
- **Table formats** - Support for Iceberg, Delta Lake, and Hive
- **Starburst compatible** - Works with Starburst Enterprise

## Important Notes

**Trino vs PrestoDB**: This adapter supports Trino only, NOT PrestoDB (Meta's fork). While they share ancestry, they have diverged significantly since 2019:

- Different Python drivers (`trino` vs `presto-python-client`)
- Different HTTP headers (`X-Trino-*` vs `X-Presto-*`)
- Diverging SQL syntax and functions

For PrestoDB, use the [Presto adapter](presto.md). For AWS managed Trino, use the [Athena adapter](athena.md).

## Installation

```bash
pip install trino

pip install "trino[kerberos]"
```

The first command installs the Trino Python driver. The second adds authentication support.

## Configuration

### Connection Settings

The CLI reads connection settings from the `trino` section of `~/.benchbox/credentials.yaml`. Values written as `${VAR}` are read from the environment:

```yaml
trino:
  host: trino-coordinator.example.com
  port: 8080
  username: analyst
  password: ${TRINO_PASSWORD}
  schema: benchmark
```

| Setting | Default | Description |
|---------|---------|-------------|
| `host` | localhost | Trino coordinator hostname |
| `port` | 8080 | Trino coordinator port |
| `username` | trino | Trino user for query attribution |
| `password` | (none) | Password for LDAP/basic auth |
| `schema` | (from benchmark and scale) | Schema for benchmark tables |
| `http_scheme` | http, or https when a password is set | Connection scheme |
| `verify_ssl` | true | Verify SSL certificates |

### CLI Options

```bash
benchbox run --platform trino --benchmark tpch --scale 1.0 \
  --platform-option catalog=hive
```

### Platform Options

| Option | Default | Description |
|--------|---------|-------------|
| `catalog` | (auto-discovered) | Default catalog (hive, iceberg, delta, memory) |
| `staging_root` | (none) | S3/GCS path for data staging |
| `table_format` | memory | Table format (memory, hive, iceberg, delta) |
| `source_catalog` | (none) | Source catalog for external data loading |
| `driver_version` | (latest) | Pin the trino package version (e.g. `0.336.0`) |
| `driver_auto_install` | false | Auto-install the requested driver version via uv if missing |

### Testing a Specific Trino Connector Version

```bash
benchbox run --platform trino --benchmark tpch \
  --platform-option driver_version=0.336.0 \
  --platform-option driver_auto_install=true \
  --platform-option catalog=hive
```

The driver package for Trino is `trino`.

See {ref}`driver-version-management` for the full guide, including why `uv run` may
revert a manually-installed version and how to work around it.

## Usage Examples

### Basic Benchmark Run

With `host` set in the credentials file:

```bash
benchbox run --platform trino --benchmark tpch --scale 1.0 \
  --platform-option catalog=hive
```

### Iceberg Tables

```bash
benchbox run --platform trino --benchmark tpch --scale 1.0 \
  --platform-option catalog=iceberg \
  --platform-option table_format=iceberg \
  --platform-option staging_root=s3://bucket/staging/
```

### Python API

```python
from benchbox import TPCH
from benchbox.platforms.trino import TrinoAdapter

adapter = TrinoAdapter(
    host="trino-coordinator.example.com",
    port=8080,
    catalog="hive",
    schema="benchmark",
    username="analyst",
)

benchmark = TPCH(scale_factor=1.0)
adapter.load_benchmark(benchmark)
results = adapter.run_benchmark(benchmark)
```

## Deployment Modes

### Local Development (Memory Catalog)

```bash
docker run -d -p 8080:8080 --name trino trinodb/trino

benchbox run --platform trino --benchmark tpch --scale 0.1 \
  --platform-option catalog=memory
```

The first command starts Trino locally in Docker. The second runs the benchmark with the memory catalog.

If you installed Trino via Homebrew, you can start the service with:

```bash
brew install trino
brew services start trino
trino-server run
```

Use either `brew services start trino` or `trino-server run` to start it manually.

BenchBox automatically detects when `localhost:8080` refuses connections and
emits a friendly error explaining that Trino needs to be started (including the
`brew services start trino` reminder) or that you should point BenchBox at a
remote Trino cluster by setting `host` and `port` in the credentials file.

### Production (Hive/Iceberg)

```bash
benchbox run --platform trino --benchmark tpch --scale 10.0 \
  --platform-option catalog=hive \
  --platform-option staging_root=s3://data-lake/staging/
```

### Starburst Enterprise

The Trino adapter is fully compatible with Starburst Enterprise. Point the credentials file at the cluster:

```yaml
trino:
  host: starburst.example.com
  port: 443
  username: analyst
  password: ${STARBURST_PASSWORD}
  http_scheme: https
  verify_ssl: true
```

Then run `benchbox run --platform trino` as usual.

## Performance Tuning

### Session Properties

Common session properties for optimization:

```python
adapter = TrinoAdapter(
    host="trino.example.com",
    session_properties={
        "query_max_memory": "8GB",
        "query_max_memory_per_node": "2GB",
        "join_distribution_type": "AUTOMATIC",
    }
)
```

### Data Format Recommendations

- **Parquet**: Best compression and performance for analytics
- **ORC**: Good alternative with predicate pushdown
- **Iceberg**: Recommended for updates and time-travel

## Query Plan Analysis

```bash
benchbox run --platform trino --benchmark tpch \
  --capture-plans
```

Trino provides detailed EXPLAIN output including:
- Distributed query plan fragments
- Data exchange patterns
- Join strategies
- Partition pruning

## Limitations

- **No local execution** - Requires Trino cluster
- **Infrastructure overhead** - Coordinator and workers needed
- **Cold start** - First queries may be slower

## Troubleshooting

### Connection Timeout

```bash
curl http://trino-host:8080/v1/info
```

This verifies that Trino is accessible.

### Catalog Not Found

```bash
trino --server trino-host:8080 --execute "SHOW CATALOGS"
```

This lists the available catalogs.

### Memory Errors

```sql
SELECT * FROM system.runtime.queries WHERE state = 'RUNNING';
```

This checks memory usage of the running queries.

## Related Documentation

- [Presto Platform](presto.md) - For PrestoDB (Meta fork)
- [Athena Platform](athena.md) - AWS managed Trino service
- [Spark Platform](spark.md) - Alternative distributed engine
