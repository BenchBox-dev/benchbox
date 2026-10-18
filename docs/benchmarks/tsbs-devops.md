<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# TSBS DevOps Benchmark

```{tags} intermediate, concept, tsbs-devops, custom-benchmark
```

> **CLI name:** `tsbs_devops` - use `benchbox run --benchmark tsbs_devops`
>
> **Query templates:** [every TSBS DevOps query](queries/tsbs_devops/index.md)

## Overview

The Time Series Benchmark Suite (TSBS) DevOps benchmark simulates infrastructure monitoring workloads typical of DevOps and observability platforms. Based on the [official TSBS implementation by Timescale](https://github.com/timescale/tsbs), this benchmark generates realistic time-series data representing CPU, memory, disk, and network metrics from a fleet of monitored hosts.

The benchmark is ideal for evaluating time-series databases, OLAP systems handling temporal data, and infrastructure monitoring solutions.

## Key Features

- **Realistic metrics** - CPU, memory, disk I/O, and network statistics
- **Host metadata** - Tags for region, datacenter, service, team
- **Diurnal patterns** - Realistic daily usage patterns
- **Configurable scale** - From 10 hosts to thousands
- **18 DevOps queries** - Common monitoring and alerting patterns
- **Multiple dialects** - Standard SQL, ClickHouse, TimescaleDB, InfluxDB support

## Data Model

The TSBS DevOps benchmark uses a dimensional model with a tags table and four metric tables:

### Tables

| Table | Purpose | Rows per SF=1 |
|-------|---------|---------------|
| **tags** | Host metadata and dimensions | 100 |
| **cpu** | CPU usage metrics per timestamp | ~1,728,000 |
| **mem** | Memory metrics per timestamp | ~1,728,000 |
| **disk** | Disk I/O metrics per device | ~3,456,000 |
| **net** | Network metrics per interface | ~3,456,000 |

### tags Table

| Column | Type | Description |
|--------|------|-------------|
| `hostname` | VARCHAR | Unique host identifier (PK) |
| `region` | VARCHAR | Cloud region (us-east-1, eu-west-1, etc.) |
| `datacenter` | VARCHAR | Datacenter name |
| `rack` | VARCHAR | Rack identifier |
| `os` | VARCHAR | Operating system (linux, windows) |
| `arch` | VARCHAR | CPU architecture (x86_64, arm64) |
| `team` | VARCHAR | Team owner |
| `service` | VARCHAR | Service name |
| `service_version` | VARCHAR | Service version |
| `service_environment` | VARCHAR | Environment (prod, staging, dev) |

### cpu Table

| Column | Type | Description |
|--------|------|-------------|
| `time` | TIMESTAMP | Measurement timestamp (PK) |
| `hostname` | VARCHAR | Host identifier (PK) |
| `usage_user` | DOUBLE | CPU % in user space |
| `usage_system` | DOUBLE | CPU % in kernel space |
| `usage_idle` | DOUBLE | CPU % idle |
| `usage_nice` | DOUBLE | CPU % nice priority |
| `usage_iowait` | DOUBLE | CPU % waiting for I/O |
| `usage_irq` | DOUBLE | CPU % hardware interrupts |
| `usage_softirq` | DOUBLE | CPU % software interrupts |
| `usage_steal` | DOUBLE | CPU % stolen by hypervisor |
| `usage_guest` | DOUBLE | CPU % running guest VMs |
| `usage_guest_nice` | DOUBLE | CPU % guest nice priority |

### mem Table

| Column | Type | Description |
|--------|------|-------------|
| `time` | TIMESTAMP | Measurement timestamp (PK) |
| `hostname` | VARCHAR | Host identifier (PK) |
| `total` | BIGINT | Total memory bytes |
| `available` | BIGINT | Available memory bytes |
| `used` | BIGINT | Used memory bytes |
| `free` | BIGINT | Free memory bytes |
| `cached` | BIGINT | Cached memory bytes |
| `buffered` | BIGINT | Buffered memory bytes |
| `used_percent` | DOUBLE | Memory usage percent |
| `available_percent` | DOUBLE | Available memory percent |

### disk Table

| Column | Type | Description |
|--------|------|-------------|
| `time` | TIMESTAMP | Measurement timestamp (PK) |
| `hostname` | VARCHAR | Host identifier (PK) |
| `device` | VARCHAR | Disk device name (PK) |
| `reads_completed` | BIGINT | Total read operations (cumulative counter) |
| `reads_merged` | BIGINT | Merged read operations |
| `sectors_read` | BIGINT | Sectors read (cumulative counter) |
| `read_time_ms` | BIGINT | Read time in milliseconds (cumulative counter) |
| `writes_completed` | BIGINT | Total write operations (cumulative counter) |
| `writes_merged` | BIGINT | Merged write operations |
| `sectors_written` | BIGINT | Sectors written (cumulative counter) |
| `write_time_ms` | BIGINT | Write time in milliseconds (cumulative counter) |
| `io_in_progress` | INTEGER | Current I/O operations |
| `io_time_ms` | BIGINT | Total I/O time (cumulative counter) |
| `weighted_io_time_ms` | BIGINT | Weighted I/O time (cumulative counter) |

> **Note:** Columns flagged as *cumulative counter* accumulate across samples
> (see `generator.py:_generate_disk_metrics`). Compute the delta between
> consecutive samples per `(hostname, device)` when reporting rates.

### net Table

| Column | Type | Description |
|--------|------|-------------|
| `time` | TIMESTAMP | Measurement timestamp (PK) |
| `hostname` | VARCHAR | Host identifier (PK) |
| `interface` | VARCHAR | Network interface name (PK) |
| `bytes_recv` | BIGINT | Bytes received (cumulative counter) |
| `bytes_sent` | BIGINT | Bytes sent (cumulative counter) |
| `packets_recv` | BIGINT | Packets received (cumulative counter) |
| `packets_sent` | BIGINT | Packets sent (cumulative counter) |
| `err_in` | BIGINT | Receive errors in this sample window |
| `err_out` | BIGINT | Send errors in this sample window |
| `drop_in` | BIGINT | Dropped incoming packets in this sample window |
| `drop_out` | BIGINT | Dropped outgoing packets in this sample window |

> **Note:** `bytes_*` and `packets_*` columns are cumulative counters
> (see `generator.py:_generate_net_metrics`); compute the delta between
> consecutive samples per `(hostname, interface)` when reporting rates.
> Error and drop columns are per-sample gauges (generated as rare 0/1
> events), so they can be summed over a window rather than differenced.

## Query Categories

The benchmark includes 18 queries organized into categories:

### Single Host Queries
Metrics for individual hosts over time ranges:
- [`single-host-12-hr`](queries/tsbs_devops/single-host-12-hr.md): CPU usage for one host over 12 hours
- [`single-host-1-hr`](queries/tsbs_devops/single-host-1-hr.md): Detailed CPU for one host over 1 hour

### Aggregation Queries
Cross-host aggregations:
- [`cpu-max-all-1-hr`](queries/tsbs_devops/cpu-max-all-1-hr.md): Maximum CPU across all hosts (1 hour)
- [`cpu-max-all-8-hr`](queries/tsbs_devops/cpu-max-all-8-hr.md): Maximum CPU across all hosts (8 hours)

### GroupBy Queries
Time-bucketed aggregations:
- [`double-groupby-1-hr`](queries/tsbs_devops/double-groupby-1-hr.md): CPU grouped by host and minute
- [`double-groupby-5-min`](queries/tsbs_devops/double-groupby-5-min.md): Fine-grained CPU grouping

### Threshold Queries
Alert-style threshold filters:
- [`high-cpu-1-hr`](queries/tsbs_devops/high-cpu-1-hr.md): Hosts with CPU > 90%
- [`high-cpu-12-hr`](queries/tsbs_devops/high-cpu-12-hr.md): Sustained high CPU hosts
- [`low-memory-hosts`](queries/tsbs_devops/low-memory-hosts.md): Hosts with available memory < 10%
- [`net-errors`](queries/tsbs_devops/net-errors.md): Hosts with network errors

### Memory Queries
Memory-specific analytics:
- [`mem-by-host-1-hr`](queries/tsbs_devops/mem-by-host-1-hr.md): Memory statistics per host

### Disk Queries
Disk I/O analytics:
- [`disk-iops-1-hr`](queries/tsbs_devops/disk-iops-1-hr.md): Read/write operations per host
- [`disk-latency`](queries/tsbs_devops/disk-latency.md): Average disk latency analysis

### Network Queries
Network throughput analytics:
- [`net-throughput-1-hr`](queries/tsbs_devops/net-throughput-1-hr.md): Bytes sent/received per host

### Combined Queries
Cross-metric correlation:
- [`resource-utilization`](queries/tsbs_devops/resource-utilization.md): Combined CPU and memory per host

### Lastpoint Queries
Most recent values (common in dashboards):
- [`lastpoint`](queries/tsbs_devops/lastpoint.md): Most recent metrics per host

### Tag-filtered Queries
Filtering by host metadata:
- [`by-region`](queries/tsbs_devops/by-region.md): Metrics filtered by cloud region
- [`by-service`](queries/tsbs_devops/by-service.md): Metrics grouped by service

## Usage Examples

### Basic Benchmark Setup

```python
from benchbox import TSBSDevOps

tsbs = TSBSDevOps(scale_factor=1.0, output_dir="tsbs_data")

data_files = tsbs.generate_data()

queries = tsbs.get_queries()
print(f"Generated {len(queries)} TSBS queries")

cpu_query = tsbs.get_query("cpu-max-all-1-hr")
print(cpu_query)
```

Scale factor 1 generates 100 hosts and 2 days of time-series data.

### Custom Configuration

```python
tsbs_custom = TSBSDevOps(
    scale_factor=0.5,
    output_dir="tsbs_custom",
    num_hosts=50,
    duration_days=7,
    interval_seconds=60,
)
data_files = tsbs_custom.generate_data()
```

These explicit options override the values derived from the scale factor: 50 hosts, 7 days of data and 1-minute (60-second) intervals.

### DuckDB Integration

```python
import duckdb
from benchbox import TSBSDevOps

tsbs = TSBSDevOps(scale_factor=0.1, output_dir="tsbs_small")
data_files = tsbs.generate_data()

conn = duckdb.connect("tsbs.duckdb")
schema_sql = tsbs.get_create_tables_sql(dialect="duckdb")

for stmt in schema_sql.split(";"):
    if stmt.strip():
        conn.execute(stmt)

for table_name, file_path in tsbs.tables.items():
    conn.execute(f"""
        INSERT INTO {table_name}
        SELECT * FROM read_csv('{file_path}', header=true, auto_detect=true)
    """)

for query_id in ["cpu-max-all-1-hr", "high-cpu-1-hr", "lastpoint"]:
    query_sql = tsbs.get_query(query_id)
    result = conn.execute(query_sql).fetchall()
    print(f"{query_id}: {len(result)} rows")

conn.close()
```

### TimescaleDB Integration

```python
from benchbox import TSBSDevOps

tsbs = TSBSDevOps(scale_factor=1.0)

schema_sql = tsbs.get_create_tables_sql(
    dialect="timescale",
    time_partitioning=True,
)
print(schema_sql)
```

This returns the TimescaleDB-optimized schema with hypertables. The output includes statements such as `SELECT create_hypertable('cpu', 'time', ...)`.

### ClickHouse Integration

```python
from benchbox import TSBSDevOps

tsbs = TSBSDevOps(scale_factor=1.0)

schema_sql = tsbs.get_create_tables_sql(
    dialect="clickhouse",
    time_partitioning=True,
)
print(schema_sql)
```

This returns the ClickHouse-optimized schema. The output includes clauses such as `ENGINE = MergeTree() ORDER BY (...) PARTITION BY toYYYYMMDD(time)`.

### InfluxDB Integration

InfluxDB 3.x uses FlightSQL for SQL queries and Line Protocol for data ingestion. BenchBox handles this automatically via the InfluxDB adapter.

```python
from benchbox.platforms.influxdb import InfluxDBAdapter
from benchbox import TSBSDevOps

tsbs = TSBSDevOps(scale_factor=0.1, output_dir="tsbs_influx")
data_files = tsbs.generate_data()

adapter = InfluxDBAdapter(
    deployment_mode="core",
    host="localhost",
    port=8086,
    token="your-influxdb-token",
    database="benchmarks",
    ssl=False,
)

conn = adapter.create_connection()

row_counts, load_time, metadata = adapter.load_data(tsbs, conn, tsbs.output_dir)
print(f"Loaded {metadata['total_rows']:,} rows in {load_time:.2f}s")

for query_id in ["cpu-max-all-1-hr", "high-cpu-1-hr", "lastpoint"]:
    query_sql = tsbs.get_query(query_id, dialect="influxdb")
    exec_time, row_count, _ = adapter.execute_query(conn, query_sql, query_id)
    print(f"{query_id}: {row_count} rows in {exec_time:.3f}s")

adapter.close_connection(conn)
```

The adapter uses Core/OSS mode. InfluxDB creates the schema automatically from Line Protocol writes, and `load_data` converts the CSV files to Line Protocol. The queries use the `influxdb` dialect, which runs as DataFusion SQL.

**InfluxDB Cloud mode:**

The following adapter targets InfluxDB Cloud (Serverless, Dedicated or Clustered).

```python
adapter = InfluxDBAdapter(
    deployment_mode="cloud",
    host="us-east-1-1.aws.cloud2.influxdata.com",
    token="your-cloud-token",
    org="your-org",
    database="benchmarks",
)
```

**Key InfluxDB Considerations:**

- **Line Protocol**: Data is loaded via InfluxDB's native Line Protocol format for optimal ingest performance
- **Schema auto-creation**: Tables (measurements) are auto-created on first write
- **SQL via FlightSQL**: Queries use standard SQL (powered by Apache DataFusion)
- **Tags vs Fields**: hostname becomes a tag (indexed), metrics become fields
- **No DELETE**: InfluxDB Core doesn't support deletes; use retention policies instead

## CLI Options (`--benchmark-option`)

Configure TSBS Devops data generation via `--benchmark-option KEY=VALUE`:

| Option | Default | Description |
|--------|---------|-------------|
| `num_hosts` | - | Number of simulated hosts |
| `duration_days` | - | Duration in days for data generation |
| `interval_seconds` | `10` | Measurement interval in seconds |
| `start_time` | - | Start time in ISO format (e.g. `2019-01-01T00:00:00`) |
| `seed` | - | Random seed for reproducibility |
| `force_regenerate` | - | Force data regeneration (`true`/`false`) |

Accepts hyphenated aliases (e.g. `num-hosts`, `duration-days`).

```bash
benchbox run --platform duckdb --benchmark tsbs_devops --scale 1 \
  --benchmark-option num_hosts=100 \
  --benchmark-option interval_seconds=30 \
  --benchmark-option start_time=2019-01-01T00:00:00
```

This sets a custom host count, interval and start time.

## Scale Factor Guidelines

| Scale Factor | Hosts | Duration | CPU Rows | Total Rows | Data Size | Use Case |
|-------------|-------|----------|----------|------------|-----------|----------|
| 0.01 | 10 | 2 days | ~173K | ~1M | ~93 MB | Quick testing |
| 0.1 | 10 | 2 days | ~173K | ~1M | ~93 MB | Development |
| 1.0 | 100 | 2 days | ~1.7M | ~10M | ~0.93 GB | Standard benchmark |
| 10.0 | 1,000 | 2 days | ~17.3M | ~100M | ~9.3 GB | Performance testing |
| 100.0 | 10,000 | 2 days | ~173M | ~1B | ~93 GB | Large scale testing |

> **Note:** Duration is fixed at 2 days; only host count scales with SF. If both
> hosts *and* duration scaled, total rows would grow as SF² (quadratic) instead of SF (linear),
> making large scale factors disproportionately expensive.
> SF=0.01 and SF=0.1 produce identical output due to the 10-host minimum floor.

## Data Generation Patterns

The generator creates realistic data with:

- **Diurnal CPU patterns**: Higher usage during business hours (9am-5pm)
- **Memory growth**: Gradual memory increase with periodic GC drops
- **Disk I/O bursts**: 5% chance of 10x burst per interval
- **Network errors**: Rare errors (~0.1%) and drops (~0.2%)
- **Tag distributions**: 75% Linux, 50% production, balanced regions

## Performance Characteristics

### Query Performance Patterns

**Single Host Queries:**
- **Bottleneck**: Time range filtering
- **Optimization**: Index on (hostname, time)
- **Typical performance**: Fast (milliseconds)

**Aggregation Queries:**
- **Bottleneck**: Full scan of time range
- **Optimization**: Columnar storage, vectorized execution
- **Typical performance**: Medium (seconds)

**GroupBy Queries:**
- **Bottleneck**: Hash aggregation memory
- **Optimization**: Pre-aggregation, materialized views
- **Typical performance**: Medium to slow

**Threshold Queries:**
- **Bottleneck**: Filtering efficiency
- **Optimization**: Bloom filters, sparse indexes
- **Typical performance**: Fast with good indexes

**Lastpoint Queries:**
- **Bottleneck**: Finding max timestamp per group
- **Optimization**: Specialized last-value indexes
- **Typical performance**: Critical for dashboards

## Best Practices

### Data Generation
1. **Match your monitoring interval** - Use realistic intervals (10s, 30s, 60s)
2. **Scale hosts appropriately** - Test with expected fleet size
3. **Consider retention** - Duration affects storage testing

### Query Optimization
1. **Partition by time** - Essential for time-series databases
2. **Index on hostname** - For single-host query performance
3. **Pre-aggregate** - Materialized views for dashboards

### Time-Series Database Tips
1. **Use native types** - TIMESTAMPTZ, DateTime64
2. **Enable compression** - Time-series compresses well
3. **Consider downsampling** - For long-term storage

## Related Documentation

- [H2ODB Benchmark](h2odb.md) - Data science workloads
- [NYC Taxi Benchmark](nyctaxi.md) - Transportation analytics
- [ClickBench](clickbench.md) - Analytical workloads

## External Resources

- [TSBS GitHub Repository](https://github.com/timescale/tsbs) - Original implementation
- [TimescaleDB Documentation](https://www.tigerdata.com/docs) - Time-series optimization
- [InfluxDB Line Protocol](https://docs.influxdata.com/influxdb/v2/reference/syntax/line-protocol/) - Time-series data format
