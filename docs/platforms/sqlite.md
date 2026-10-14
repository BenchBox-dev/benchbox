<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# SQLite Platform

```{tags} intermediate, guide, sqlite, embedded-platform
```

SQLite is the most widely deployed database engine in the world. While optimized for OLTP workloads, BenchBox supports SQLite for comparison benchmarks and scenarios where simplicity is paramount.

## Features

- **Zero configuration** - No server required
- **Single file** - Complete database in one file
- **Cross-platform** - Works everywhere
- **ACID compliant** - Full transaction support
- **Battle-tested** - Decades of production use

## Use Cases

SQLite is useful in BenchBox for:

1. **Baseline comparisons** - Compare OLAP platforms against OLTP baseline
2. **CI/CD testing** - Fast, isolated test environments
3. **Educational** - Learn SQL benchmarking concepts
4. **Embedded scenarios** - Test mobile/edge database performance

## Installation

SQLite is included with Python, so no separate install is needed. Install BenchBox with uv or with pip:

```bash
uv add benchbox

pip install benchbox
```

## Configuration

### Default Usage

```bash
benchbox run --platform sqlite --benchmark tpch --scale 0.01
```

### Database File

The CLI stores the database file under the runs root, in `databases/<benchmark>_sf<scale>/`, and names it from the benchmark, scale factor, and tuning settings. Set `BENCHBOX_OUTPUT_DIR` to move the runs root:

```bash
BENCHBOX_OUTPUT_DIR=/data/benchbox \
  benchbox run --platform sqlite --benchmark tpch --scale 0.1
```

## Adapter Options

Set these through the [Python API](#python-api):

| Option | Default | Description |
|--------|---------|-------------|
| `database_path` | `:memory:` | Path to the SQLite database file |
| `timeout` | 30.0 | Seconds to wait for a locked database before failing |
| `check_same_thread` | false | Only allow a connection on the thread that created it |

### Connection Settings

BenchBox applies fixed PRAGMA settings to every connection: `journal_mode = WAL`, `synchronous = NORMAL`, `cache_size = 10000` (pages), `temp_store = MEMORY`, and `foreign_keys = ON`. They are fixed and cannot be changed from the CLI or the Python API.

## Usage Examples

### Quick Start

```bash
benchbox run --platform sqlite --benchmark tpch --scale 0.01
```

### Python API

```python
from benchbox import TPCH
from benchbox.platforms.sqlite import SQLiteAdapter

adapter = SQLiteAdapter(
    database_path="./benchmark.db",
    timeout=60.0,
)

benchmark = TPCH(scale_factor=0.01)
benchmark.generate_data()
adapter.load_benchmark(benchmark)
results = adapter.run_benchmark(benchmark)
```

## Performance Tuning

### TPC-H Helper Indexes

BenchBox creates a small set of SQLite-specific helper indexes after loading
TPC-H data. These indexes target the order, supplier, nation, and lineitem
access paths used by correlated queries such as Q21, which otherwise require
repeated full scans in SQLite and can dominate local smoke runs even at SF 0.01.

The helper indexes are applied after bulk load so they do not slow row ingestion,
and they preserve query results and status classification. Generated `.tbl` data
is still reusable across TPC-H-derived workloads; only loaded SQLite database
files need cleanup when disk space is constrained.

### Connection PRAGMAs

Write-ahead logging, the page cache size, and in-memory temp storage are already set on every connection (see [Connection Settings](#connection-settings)). BenchBox does not expose other PRAGMAs, such as `mmap_size`.

## Limitations

SQLite has characteristics that affect OLAP benchmarks:

1. **Single-threaded writes** - Only one writer at a time
2. **Row-based storage** - Not optimized for analytical scans
3. **Limited parallelism** - Sequential query execution
4. **Memory constraints** - In-memory limited by process memory

### Recommended Scale Factors

| Scale Factor | Expected Performance |
|--------------|---------------------|
| 0.01 | Fast for smoke; Q21 is sensitive to missing helper indexes |
| 0.1 | Moderate (minutes) |
| 1.0 | Slow (10+ minutes) |
| 10.0+ | Not recommended |

## Comparison with DuckDB

SQLite and DuckDB are optimized for different workload types:

| Aspect | SQLite | DuckDB |
|--------|--------|--------|
| Optimized For | OLTP (transactional) | OLAP (analytical) |
| Storage | Row-based | Columnar |
| Parallelism | Single-thread | Multi-thread |

**Note:** Performance differences vary based on query characteristics, data size, and hardware. Run benchmarks with your specific workloads to compare.

```bash
benchbox run --platform sqlite --benchmark tpch --scale 0.1
benchbox run --platform duckdb --benchmark tpch --scale 0.1
```

## Troubleshooting

### Database Locked

Close other connections to the database file. To wait longer for the lock, raise the connection timeout, in seconds, through the Python API:

```python
from benchbox.platforms.sqlite import SQLiteAdapter

adapter = SQLiteAdapter(database_path="./benchmark.db", timeout=60.0)
```

### Out of Memory

BenchBox keeps SQLite temp storage in memory (`temp_store = MEMORY`), and this cannot be changed. Use a smaller scale factor.

### Slow Queries

SQLite is a row store with limited parallelism, so analytical queries slow down quickly as data grows. Use a smaller scale factor, or compare against [DuckDB](duckdb.md) for larger runs.

## Related Documentation

- [DuckDB](duckdb.md) - Columnar analytical database
- [PostgreSQL](postgresql.md) - Full-featured RDBMS
- [Platform Selection](platform-selection-guide.md)
