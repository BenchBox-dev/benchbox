<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# LakeSail Sail Platform

```{tags} intermediate, guide, lakesail, sql-platform, dataframe-platform, spark-compatible, local
```

LakeSail Sail is a Rust-based, drop-in replacement for Apache Spark built on DataFusion. It delivers 4x faster execution with 94% lower hardware costs compared to Apache Spark (TPC-H SF100). BenchBox supports LakeSail in both SQL mode (`lakesail`) and DataFrame mode (`lakesail-df`), connecting via the standard Spark Connect protocol for zero-rewrite migration from PySpark.

## Features

- **4x faster than Spark** - Rust-based DataFusion engine with significant performance gains at lower cost
- **Zero-rewrite migration** - Uses standard PySpark client via Spark Connect protocol
- **Dual execution modes** - SQL benchmarking (`lakesail`) and DataFrame benchmarking (`lakesail-df`)
- **Spark SQL dialect** - SQLGlot `spark` dialect for query translation
- **Local and external server** - Multi-threaded single-host execution, or a connection to a Sail server that you run yourself
- **Parquet and ORC** - Native support for columnar table formats
- **Adaptive Query Execution** - AQE support for runtime query optimization
- **Full PySpark API** - DataFrame, Column expression, and Window function compatibility

## Quick Start

The commands below install the Spark Connect-capable PySpark client and the Sail server, start a local Sail Spark Connect server, check client and endpoint readiness without starting a server, and then run the SQL and DataFrame benchmarks. Start the server in a separate terminal. SQL mode can start one for you, but DataFrame mode needs a running server.

```bash
uv add benchbox --extra lakesail
uv add pysail

python -m pysail spark server --port 50051

benchbox platforms check lakesail-df

benchbox run --platform lakesail --benchmark tpch --scale 1.0

benchbox run --platform lakesail-df --benchmark tpch --scale 1.0
```

## Configuration

LakeSail Sail connects through the Spark Connect protocol. No additional authentication is required for local deployments.
`benchbox platforms check lakesail` and `benchbox platforms check lakesail-df` only verify client imports and endpoint
reachability; they do not instantiate the adapter or start a Sail server. SQL mode can auto-start a local `pysail`
server when `pysail` is installed and the endpoint is unreachable, but DataFrame mode requires an already-running
endpoint.

To run Sail in Docker from a BenchBox source checkout, use `docker/lakesail/docker-compose.yml`. The compose service
builds a `benchbox-lakesail` image from the public PySail package and starts `sail spark server` on
`sc://localhost:50051`. Like other Spark Connect backends, the server reads files by absolute path; the compose
workflow mounts `BENCHBOX_DATA_DIR` at the same absolute path inside the container.

`BENCHBOX_DATA_DIR` has **no default** and must be exported as an **absolute path** before invoking compose.
A relative value can never equal an absolute host path, which breaks the mount above.
`make test-docker-up-lakesail` validates the variable is set and absolute before starting the stack:

```bash
export BENCHBOX_DATA_DIR=/absolute/path/to/your/data
cd docker/lakesail && docker compose up -d lakesail-connect
```

### Configuration Methods

The CLI runs LakeSail with its defaults. `--platform lakesail:distributed` selects distributed mode, and `endpoint` and `adaptive_enabled` are the `--platform-option` keys:

```bash
benchbox run --platform lakesail --benchmark tpch --scale 1.0

benchbox run --platform lakesail:distributed --platform-option endpoint=sc://my-sail-server:50051 --benchmark tpch --scale 1.0
```

Every setting below can be set by constructing the adapter directly:

```python
from benchbox.platforms.lakesail import LakeSailAdapter

adapter = LakeSailAdapter(
    endpoint="sc://my-sail-server:50051",
    deployment_mode="distributed",
    driver_memory="8g",
    shuffle_partitions=16,
)
```

### Configuration Options

| Option | Default | Description |
|--------|---------|-------------|
| `endpoint` | `sc://localhost:50051` | Sail server Spark Connect URL |
| `deployment_mode` | `local` | Deprecated alias of `--platform lakesail:local\|distributed`; `sail_mode` is an alias of this option |
| `sail_workers` | - | Has no effect: BenchBox logs a warning and does not record it, because Sail takes no worker count from BenchBox |
| `app_name` | `BenchBox-LakeSail` | Application name for the session |
| `driver_memory` | `4g` | Driver memory allocation (e.g., `4g`, `8g`) |
| `shuffle_partitions` | `200` | Number of shuffle partitions |
| `table_format` | `parquet` | Table format: `parquet` or `orc` |
| `adaptive_enabled` | `true` | Enable Adaptive Query Execution (AQE) |
| `disable_cache` | `true` | Disable result caching for accurate benchmarking |
| `spark_config` | `{}` | Additional Spark configuration properties (dict) |

## Usage Examples

### SQL Mode

The examples below run TPC-H at scale factor 1, TPC-DS at scale factor 10, a subset of TPC-H queries, and a dry run that previews execution. `driver_memory` and `shuffle_partitions` are adapter parameters, not CLI options.

```bash
benchbox run --platform lakesail --benchmark tpch --scale 1.0

benchbox run --platform lakesail --benchmark tpcds --scale 10.0

benchbox run --platform lakesail --benchmark tpch --scale 1.0 --queries Q1,Q6,Q17

benchbox run --dry-run ./preview --platform lakesail --benchmark tpch
```

### DataFrame Mode

A custom endpoint or driver memory needs the Python adapter shown below.

```bash
benchbox run --platform lakesail-df --benchmark tpch --scale 1.0
```

### Comparison with Apache Spark

Run the same benchmark on LakeSail Sail and on Apache Spark, then compare the results:

```bash
benchbox run --platform lakesail --benchmark tpch --scale 10.0

benchbox run --platform spark --benchmark tpch --scale 10.0

benchbox compare lakesail_tpch_sf10.json spark_tpch_sf10.json
```

## Python API

### SQL Mode

```python
from benchbox import TPCH
from benchbox.platforms.lakesail import LakeSailAdapter

adapter = LakeSailAdapter(
    endpoint="sc://localhost:50051",
    deployment_mode="local",
    driver_memory="8g",
    shuffle_partitions=16,
    table_format="parquet",
)

benchmark = TPCH(scale_factor=1.0)
benchmark.generate_data()
adapter.load_benchmark(benchmark)
results = adapter.run_benchmark(benchmark)
```

### DataFrame Mode

```python
from benchbox.platforms.dataframe.lakesail_df import LakeSailDataFrameAdapter

adapter = LakeSailDataFrameAdapter(
    endpoint="sc://localhost:50051",
    driver_memory="8g",
    shuffle_partitions=16,
    enable_aqe=True,
)

with adapter as ctx:
    df = ctx.read_parquet(Path("lineitem.parquet"))
    result = df.filter(df["l_quantity"] > 25).groupBy("l_returnflag").count()
    rows = ctx.collect(result)
```

## Architecture

LakeSail Sail replaces the Spark execution engine with a Rust-based runtime built on Apache DataFusion, while maintaining full compatibility with the Spark Connect protocol.

### Spark Connect Integration

```
┌──────────────────┐     Spark Connect     ┌──────────────────────┐
│  PySpark Client   │ ──── Protocol ────>  │   LakeSail Sail       │
│  (standard API)   │     (gRPC)           │   Server              │
└──────────────────┘                       ├──────────────────────┤
                                           │  Query Optimizer      │
                                           │  (DataFusion)         │
                                           ├──────────────────────┤
                                           │  Rust Execution       │
                                           │  Workers              │
                                           └──────────────────────┘
```

Key architectural points:

- **Client**: Standard PySpark library -- no custom client needed
- **Protocol**: Spark Connect (gRPC) for client-server communication
- **SQL Dialect**: Spark SQL, translated via SQLGlot `spark` dialect
- **Optimizer**: DataFusion query optimizer with cost-based optimization
- **Execution**: Rust-based workers for vectorized query processing
- **Constraints**: Primary and foreign keys are informational only (not enforced), matching Spark behavior
- **Session configuration**: LakeSail accepts `spark.conf.set` for Spark keys
  it ignores and does not report which ones it honours. BenchBox therefore does
  not apply a tuning file's `platform_optimizations.spark` map on LakeSail:
  validation warns, and each key is recorded as a dropped intent in the
  applied-tuning ledger. LakeSail has no curated tuning templates.

### Tuning Support

LakeSail supports the following tuning types:

| Tuning Type | Supported | Notes |
|-------------|-----------|-------|
| Partitioning | Yes | `PARTITIONED BY` clause on table creation |
| Sorting | Yes | Sort-based optimizations |
| Primary Keys | Informational | Not enforced, used for optimizer hints |
| Foreign Keys | Informational | Not enforced, used for optimizer hints |

## Deployment Modes

### Local Mode

Single-node, multi-threaded execution. Best for development, testing, and small-to-medium scale benchmarks. `local` is the default deployment.

```bash
benchbox run --platform lakesail --benchmark tpch --scale 1.0
```

- Sail server runs on a single machine
- Multi-threaded query execution via Rust workers
- No cluster coordination overhead
- Default endpoint: `sc://localhost:50051`

### Distributed Mode

Connects to an external Sail server and never starts a local one. Select it with the platform selector:

```bash
benchbox run --platform lakesail:distributed --benchmark tpch --scale 100.0
```

- The endpoint (`sc://localhost:50051` by default) must already be reachable; BenchBox fails instead of starting a server
- The server's size and topology are whatever you deployed; BenchBox neither creates nor changes a multi-node run, and the recorded mode does not show how many nodes served it
- Set the server up as described in the LakeSail documentation

## Comparison: LakeSail vs Apache Spark

| Feature | LakeSail Sail | Apache Spark |
|---------|---------------|--------------|
| Language | Rust (DataFusion) | Scala/Java (JVM) |
| Performance | ~4x faster (TPC-H SF100) | Baseline |
| Hardware Cost | ~94% lower | Baseline |
| API Compatibility | Full PySpark API | Native |
| SQL Dialect | Spark SQL | Spark SQL |
| Connection Protocol | Spark Connect | Native / Spark Connect |
| Migration Effort | Zero rewrites | N/A |
| Local Mode | Multi-threaded Rust | JVM-based |
| Distributed Mode | External Sail server, deployed separately | JVM executors |
| DataFrame API | Full PySpark compatibility | Native |
| Table Formats | Parquet, ORC | Parquet, ORC, Delta, Iceberg |
| Maturity | Newer | Battle-tested |

### When to Use LakeSail

**Use LakeSail when:**
- You want Spark compatibility with native execution performance (avoiding JVM overhead)
- Migrating from an existing PySpark/Spark SQL workload with no code changes
- Running OLAP and analytics benchmarks where execution speed matters
- Hardware cost reduction is a priority
- You need both SQL and DataFrame benchmarking on the same engine

**Use Apache Spark instead when:**
- You need the broadest ecosystem of connectors and integrations
- You require Delta Lake or Iceberg table format support
- You depend on Spark-specific plugins or UDFs not yet supported by Sail
- You need a battle-tested production platform with long-term support history

## Troubleshooting

### Connection Refused

```
Failed to connect to LakeSail Sail: Connection refused
```

**Solutions:**
1. Verify the Sail server is running and accepting connections
2. Check the endpoint URL format: `sc://host:port` (default: `sc://localhost:50051`)
3. Verify the port is not blocked by a firewall
4. Check server logs for startup errors

### PySpark Not Installed

```
PySpark not installed. Install with: uv add benchbox --extra lakesail
```

**Solutions:**
1. Install the Spark Connect-capable PySpark client: `uv add benchbox --extra lakesail`
2. LakeSail uses the standard PySpark API through Spark Connect; no LakeSail-specific client is needed in BenchBox.

### Database Creation Failed

```
Failed to create database: ...
```

**Solutions:**
1. Verify the Sail server has write permissions
2. Check that the database name uses only alphanumeric characters and underscores
3. Try connecting with a simpler database name: `--platform-option database=test`

### Query Execution Timeout

```
Query execution timed out
```

**Solutions:**
1. Increase driver memory: `driver_memory="16g"`
2. Adjust shuffle partitions: `shuffle_partitions=32`
3. For large-scale benchmarks, point BenchBox at a larger Sail server that you run: `--platform lakesail:distributed`
4. Check Sail server resource availability

### Table Already Exists

```
Table 'lineitem' already exists
```

**Solutions:**
1. The adapter handles this automatically by dropping and recreating tables
2. Force data regeneration: `benchbox run --force all --platform lakesail ...`
3. Manually drop the database: connect to the Sail server and run `DROP DATABASE <name> CASCADE`

## Compressed Data Files

BenchBox generates zstd-compressed data files by default (e.g., `lineitem.tbl.1.zst`). LakeSail's Sail engine supports reading compressed CSV files, but its CSV reader defaults to `UNCOMPRESSED` and does not auto-detect compression from file extensions.

BenchBox handles this automatically -- during data loading, it detects the compression type from the file extension and passes the appropriate `compression` option to the Spark CSV reader. No user action is required.

If you encounter `No files found in the specified paths` errors during data loading, ensure you are running a current version of BenchBox that includes this auto-detection. As a workaround, you can regenerate data without compression:

```bash
benchbox run --platform lakesail --benchmark tpch --compression none --force datagen
```

## See Also

- [Spark Platform](spark.md) - Apache Spark benchmarking (for comparison)
- [Deployment Modes Guide](deployment-modes.md) - Platform deployment architecture
- [Platform Selection Guide](platform-selection-guide.md) - Choose the right platform
- [Getting Started](../usage/getting-started.md) - Quick start guide
- [LakeSail Documentation](https://lakesail.com) - Official LakeSail documentation portal
