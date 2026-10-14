<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# DuckDB Platform

```{tags} intermediate, guide, duckdb, embedded-platform
```

DuckDB is an in-process analytical database optimized for OLAP workloads. Install it via the `[duckdb]` extra and it is ready for local development, testing, and small-to-medium scale benchmarks.

## Features

- **Zero configuration** - No server setup required
- **In-process execution** - Embedded in Python process
- **Columnar vectorized** - Optimized for analytics
- **SQLite-compatible** - File-based persistence
- **Parallel execution** - Multi-threaded queries

## Why DuckDB is the Recommended Starting Point

DuckDB is available via the `[duckdb]` extra and recommended for most local workloads:

1. **No server setup** - Works with no external dependencies after install
2. **Native file format support** - Parquet and CSV support
3. **Local execution** - No network or service variability
4. **Free and open source** - No licensing costs
5. **Suitable for development** - Quick iteration on benchmark workflows

Choose the platform that best fits your specific requirements. See the [Platform Selection Guide](platform-selection-guide.md) for help selecting the right platform for your use case.

## Installation

```bash
uv add benchbox --extra duckdb

pip install "benchbox[duckdb]"
```

The first command installs BenchBox with the DuckDB extra using uv, and the second does the same with pip.

## Configuration

### Default Usage

No configuration needed - DuckDB works immediately:

```bash
benchbox run --platform duckdb --benchmark tpch --scale 0.01
```

### Database File

The CLI stores the database file under the runs root, in `databases/<benchmark>_sf<scale>/`, and names it from the benchmark, scale factor, and tuning settings. Set `BENCHBOX_OUTPUT_DIR` to move the runs root:

```bash
BENCHBOX_OUTPUT_DIR=/data/benchbox \
  benchbox run --platform duckdb --benchmark tpch --scale 1.0
```

To choose the exact file, or to use an in-memory database, pass `database_path` to `DuckDBAdapter` in the [Python API](#python-api).

### CLI Options

```bash
benchbox run --platform duckdb --benchmark tpch --scale 1.0 \
  --platform-option threads=8 \
  --platform-option memory_limit=4GB
```

## Platform Options

| Option | Default | Description |
|--------|---------|-------------|
| `threads` | (auto) | Number of threads |
| `memory_limit` | (auto) | Maximum memory usage |
| `temp_directory` | (auto) | Temp file location |
| `driver_version` | (latest) | Pin the DuckDB Python package version (e.g. `1.2.0`) |
| `driver_auto_install` | false | Auto-install the requested driver version via uv if missing |

`max_temp_directory_size` and `progress_bar` are Python/config-file settings, not `--platform-option` names: pass them to `DuckDBAdapter` (or `from_config`) directly, e.g. `DuckDBAdapter(database_path=":memory:", progress_bar=True)`.

### Testing a Specific DuckDB Version

```bash
benchbox run --platform duckdb --benchmark tpch \
  --platform-option driver_version=1.4.3 \
  --platform-option driver_auto_install=true
```

See {ref}`driver-version-management` for the full guide, including why `uv run` may
revert a manually-installed version and how to work around it.

## Usage Examples

### Quick Start

```bash
benchbox run --platform duckdb --benchmark tpch --scale 0.01

benchbox run --platform duckdb --benchmark tpch --scale 0.1 \
  --queries Q1,Q6,Q17
```

### Scale Factor Guide

| Scale Factor | Data Size | Use Case |
|--------------|-----------|----------|
| 0.01 | ~10 MB | Unit testing, CI/CD |
| 0.1 | ~100 MB | Integration testing |
| 1.0 | ~1 GB | Standard benchmarking |
| 10.0 | ~10 GB | Performance testing |

**Note:** Execution times vary based on hardware, query complexity, and configuration. Run benchmarks to establish baselines for your environment.

### With Tuning

```bash
benchbox run --platform duckdb --benchmark tpch --scale 1.0 \
  --tuning tuned
```

The `tuned` mode applies optimizations such as indexes.

### Python API

```python
from benchbox import TPCH
from benchbox.platforms.duckdb import DuckDBAdapter

adapter = DuckDBAdapter()

adapter = DuckDBAdapter(database_path="./benchmarks.duckdb")

benchmark = TPCH(scale_factor=0.1)
benchmark.generate_data()
adapter.load_benchmark(benchmark)
results = adapter.run_benchmark(benchmark)

print(f"Total runtime: {results.total_time:.2f}s")
```

The first `DuckDBAdapter()` call creates an in-memory database. The second creates a persistent one at the given path. Use one or the other.

### Comparison Across Scales

```python
from benchbox import TPCH
from benchbox.platforms.duckdb import DuckDBAdapter

scales = [0.01, 0.1, 1.0]
for sf in scales:
    adapter = DuckDBAdapter()
    benchmark = TPCH(scale_factor=sf)
    benchmark.generate_data()
    adapter.load_benchmark(benchmark)
    results = adapter.run_benchmark(benchmark)
    print(f"SF {sf}: {results.total_time:.2f}s")
```

## Performance Features

### Thread Configuration

```bash
benchbox run --platform duckdb --benchmark tpch \
  --platform-option threads=4
```

This controls parallelism.

### Memory Limits

```bash
benchbox run --platform duckdb --benchmark tpch --scale 10.0 \
  --platform-option memory_limit=8GB
```

This limits memory usage.

### Temporary Storage

For large datasets that exceed memory:

```bash
benchbox run --platform duckdb --benchmark tpch --scale 10.0 \
  --platform-option temp_directory=/fast/ssd/tmp
```

## Data Loading

DuckDB supports fast data loading from multiple formats:

### Default

```bash
benchbox run --platform duckdb --benchmark tpch --scale 1.0
```

Data is generated as `.tbl` flat files (zstd-compressed by default) and loaded automatically.

### Pre-generated Data

```bash
benchbox datagen --benchmark tpch --scale 1.0 --output ./data/tpch
benchbox run --platform duckdb --benchmark tpch --scale 1.0 --output ./data/tpch
```

Generate the data separately, then run the benchmark with the pre-generated data.

### Direct Query (No Load)

For testing queries without loading:

```python
import duckdb

conn = duckdb.connect()
result = conn.execute("""
    SELECT count(*) FROM read_parquet('lineitem/*.parquet')
""").fetchone()
```

The query reads the Parquet files directly.

## Best Practices

### 1. Start Small

Begin with SF 0.01 to validate your workflow:

```bash
benchbox run --platform duckdb --benchmark tpch --scale 0.01
```

### 2. Use In-Memory for Speed

An in-memory database avoids disk I/O. The CLI always writes a database file, so use the Python API for in-memory runs:

```python
from benchbox.platforms.duckdb import DuckDBAdapter

adapter = DuckDBAdapter(database_path=":memory:")
```

### 3. Persist for Development

When iterating on queries, load the data once and reuse the database file:

```bash
benchbox run --platform duckdb --benchmark tpch --scale 1.0 \
  --phases generate,load

benchbox run --platform duckdb --benchmark tpch --scale 1.0 \
  --phases power
```

The first command loads the data once. The second runs the queries as many times as you need. Both use the same database file because the CLI names it from the benchmark, scale factor, and tuning settings.

### 4. Match Production Scale

Test at similar scale to production platforms:

```bash
benchbox run --platform duckdb --benchmark tpch --scale 10.0
```

For example, if you plan to run SF 100 on Snowflake, test at SF 1-10 on DuckDB.

## Troubleshooting

### Out of Memory

```bash
benchbox run --platform duckdb --benchmark tpch --scale 10.0 \
  --platform-option memory_limit=16GB \
  --platform-option temp_directory=/tmp/duckdb
```

Increase the memory limit or set a temp directory so DuckDB can spill to disk.

### Slow Queries

```python
import os

from benchbox.platforms.duckdb import DuckDBAdapter

adapter = DuckDBAdapter(thread_limit=os.cpu_count())
```

This sets the thread count to the number of available cores.

The progress bar is not a `--platform-option`; enable it through the Python API:

```python
from benchbox.platforms.duckdb import DuckDBAdapter

adapter = DuckDBAdapter(database_path=":memory:", progress_bar=True)
```

### Database Locked

```bash
BENCHBOX_OUTPUT_DIR=./benchbox_runs_2 \
  benchbox run --platform duckdb --benchmark tpch
```

DuckDB allows only one connection for write operations. Close other DuckDB connections, or point `BENCHBOX_OUTPUT_DIR` at a different runs root so the run uses a new database file. The new root also holds its own generated data and results.

### Disk Space for Temp Files

```bash
df -h /tmp

benchbox run --platform duckdb --benchmark tpch --scale 10.0 \
  --platform-option temp_directory=/data/tmp
```

Check the space in the temp directory first, then point `temp_directory` at a location with more room.

## Comparison with Other Platforms

| Feature | DuckDB | SQLite | PostgreSQL |
|---------|--------|--------|------------|
| Setup | None | None | Server |
| Best for | OLAP | OLTP | General |
| Parallelism | Multi-thread | Single-thread | Multi-process |
| Memory | In-process | In-process | Separate |
| Scale | ~100 GB | ~10 GB | ~TB |

## Related Documentation

- [SQLite](sqlite.md) - Alternative embedded database
- [DataFusion](datafusion.md) - Rust-based alternative
- [Quick Start](../tutorials/first-benchmark.md) - Getting started tutorial
- [Platform Selection](platform-selection-guide.md)
