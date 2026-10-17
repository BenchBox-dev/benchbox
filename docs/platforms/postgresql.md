<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# PostgreSQL Platform

```{tags} intermediate, guide, postgresql, sql-platform
```

PostgreSQL is a powerful open-source relational database that serves as an excellent baseline for benchmark comparisons. BenchBox supports PostgreSQL 12+ with optional TimescaleDB extensions for time-series workloads.

## Features

- **COPY-based bulk loading** - Efficient data loading using PostgreSQL's COPY command
- **EXPLAIN/EXPLAIN ANALYZE** - Full query plan capture support
- **TimescaleDB support** - Optional time-series extensions for TSBS DevOps benchmark
- **Standard SQL** - Excellent SQL standards compliance

## Installation

PostgreSQL support requires the `psycopg` 3 driver (the `postgresql` extra):

```bash
pip install "benchbox[postgresql]"
```

This installs `psycopg[binary]`. For a standalone install without BenchBox extras, use `pip install "psycopg[binary]"`.

## Configuration

### Environment Variables

```bash
export PGHOST=localhost
export PGPORT=5432
export PGUSER=postgres
export PGPASSWORD=your_password
export PGDATABASE=benchbox
```

### CLI Options

```bash
benchbox run --platform postgresql --benchmark tpch --scale 1.0 \
  --platform-option host=localhost \
  --platform-option port=5432 \
  --platform-option database=benchbox \
  --platform-option username=postgres \
  --platform-option password=yourpassword \
  --platform-option schema=public
```

### Platform Options

| Option | Default | Description |
|--------|---------|-------------|
| `host` | localhost | PostgreSQL server hostname |
| `port` | 5432 | PostgreSQL server port |
| `database` | auto-generated | Database name |
| `username` | postgres | PostgreSQL username |
| `password` | (none) | PostgreSQL password |
| `schema` | public | Target schema |
| `work_mem` | 256MB | Working memory for sorts/hashes |
| `statement_timeout` | (unset) | Per-statement timeout in milliseconds; `0` disables |
| `enable_timescale` | false | Enable TimescaleDB features |
| `driver_version` | (latest) | Pin the psycopg package version (e.g. `3.1.0`) |
| `driver_auto_install` | false | Auto-install the requested driver version via uv if missing |

### Testing a Specific psycopg Version

```bash
benchbox run --platform postgresql --benchmark tpch \
  --platform-option driver_version=3.1.0 \
  --platform-option driver_auto_install=true \
  --platform-option host=localhost
```

The driver package for PostgreSQL is `psycopg[binary]`.

See {ref}`driver-version-management` for the full guide, including why `uv run` may
revert a manually-installed version and how to work around it.

## Usage Examples

### Basic Benchmark Run

```bash
benchbox run --platform postgresql --benchmark tpch --scale 0.1 \
  --platform-option host=localhost \
  --platform-option database=tpch_benchmark
```

### Python API

```python
from benchbox import TPCH
from benchbox.platforms.postgresql import PostgreSQLAdapter

adapter = PostgreSQLAdapter(
    host="localhost",
    port=5432,
    database="benchbox",
    username="postgres",
    password="password",
)

benchmark = TPCH(scale_factor=0.1)
benchmark.generate_data()
adapter.load_benchmark(benchmark)
results = adapter.run_benchmark(benchmark)
```

### TimescaleDB Integration

For time-series benchmarks like TSBS DevOps, enable TimescaleDB:

```bash
benchbox run --platform postgresql --benchmark tsbs-devops --scale 1.0 \
  --platform-option enable_timescale=true \
  --platform-option host=localhost
```

## Performance Tuning

### Recommended Settings

For benchmark workloads, consider these PostgreSQL settings:

```sql
SET work_mem = '256MB';
SET maintenance_work_mem = '1GB';
SET effective_cache_size = '8GB';

shared_buffers = 4GB
effective_io_concurrency = 200
random_page_cost = 1.1
```

The three `SET` statements are connection-level settings, which you set through platform options. The last three lines (`shared_buffers`, `effective_io_concurrency` and `random_page_cost`) are server-level settings that belong in `postgresql.conf`.

### Tuning Mode

BenchBox supports automatic tuning configuration:

```bash
benchbox run --platform postgresql --benchmark tpch \
  --tuning tuned

benchbox run --platform postgresql --benchmark tpch \
  --tuning notuning
```

The first command runs with tuning enabled. The second runs the baseline with no tuning.

A tuning file can partition tables. A `DATE` or `TIMESTAMP` partition column
gets `PARTITION BY RANGE` with yearly child partitions over the benchmark's
date domain (TPC-H and SSB 1992-1998, TPC-DS 1998-2003) plus a `DEFAULT`
partition for any other value; an integer column gets four `HASH` partitions;
any other type gets `LIST` with only the `DEFAULT` partition. PostgreSQL
requires a primary or unique key on a partitioned table to include the
partition column, so while primary or unique keys are requested, BenchBox
skips partitioning and records the request as not applied. Set
`primary_keys.enabled: false` (and `unique_constraints.enabled: false`) to
partition. The PostgreSQL-compatible platforms (CedarDB, Citus, ParadeDB,
pg_duckdb, pg_mooncake, TimescaleDB) do not partition.

## Query Plan Capture

PostgreSQL provides detailed query plan analysis:

```bash
benchbox run --platform postgresql --benchmark tpch \
  --capture-plans
```

This captures EXPLAIN ANALYZE output including:
- Execution time per node
- Actual vs estimated rows
- Memory usage
- I/O statistics

## Limitations

- **No native columnar storage** - Row-oriented by default (consider Citus for columnar)
- **Single-node** - No built-in distributed query execution
- **Memory constraints** - Large datasets may require careful configuration

## Troubleshooting

### Connection Refused

```bash
pg_isready -h localhost -p 5432
```

Verify that PostgreSQL is running, then check `pg_hba.conf` for access rules.

### Permission Denied

```sql
GRANT CREATE ON DATABASE benchbox TO your_user;
GRANT USAGE ON SCHEMA public TO your_user;
```

Grant the necessary permissions.

### Memory Errors

```bash
benchbox run --platform postgresql --benchmark tpcds \
  --platform-option work_mem=512MB
```

Increase `work_mem` for complex queries.

## Related Documentation

- [Platform Selection Guide](platform-selection-guide.md)
- [TSBS DevOps Benchmark](../benchmarks/tsbs-devops.md) - Time-series benchmark with TimescaleDB
- [TPC-H Benchmark](../benchmarks/tpc-h.md)
