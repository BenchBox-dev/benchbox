# ClickHouse Local Mode

```{tags} intermediate, guide, clickhouse, local-platform
```

BenchBox supports ClickHouse in two deployment targets, plus a separate first-class cloud platform:

- **Local Mode**: Uses chDB for in-process ClickHouse execution
- **Server Mode**: Connects to an external ClickHouse server
- **ClickHouse Cloud**: Separate first-class platform → see [ClickHouse Cloud](clickhouse-cloud.md)

```{note}
**ClickHouse Cloud** is now a first-class platform (`--platform clickhouse-cloud`), not a deployment mode. This follows the pattern established by MotherDuck and Starburst.
```

## Overview

ClickHouse Local Mode uses [chDB](https://github.com/chdb-io/chdb), the official in-process ClickHouse engine, to run ClickHouse queries directly in Python without requiring a separate ClickHouse server installation.

### Key Benefits

- **Zero Server Setup**: No ClickHouse server installation required
- **Native Performance**: In-process execution eliminates IPC overhead
- **Development Friendly**: Perfect for testing, development, and quick analysis
- **Same SQL Compatibility**: Full ClickHouse SQL dialect support
- **Easy Installation**: Single `uv add chdb` command

### Capability boundary: ACL introspection

The embedded chDB connection used by `clickhouse-local` runs as its built-in
`default` user and does not expose the grants required to inspect
`system.users`, `system.roles`, or `system.grants`. The metadata-primitives ACL
workload therefore requires `clickhouse-server` (or ClickHouse Cloud) with an
appropriately privileged user; it is unsupported in local mode. Non-ACL catalog queries remain supported in local mode.

## Installation

### Prerequisites

- Python 3.11+
- Supported platforms: macOS and Linux (x86_64 and ARM64)

### Install chDB

```bash
uv add chdb

uv run -- python -c "import chdb; print(chdb.chdb_version())"
```

The second command verifies the installation.

### Install BenchBox with ClickHouse Support

```bash
uv add benchbox

uv sync --group dev
```

## Usage

### Basic Usage

```bash
benchbox run --platform clickhouse-local --benchmark tpch --scale 0.01

benchbox run --platform clickhouse-local --benchmark tpch --scale 0.01 \
  --platform-option data_path=/tmp/benchmark_data

benchbox run --platform clickhouse-server --benchmark tpch --scale 0.01 \
  --platform-option host=localhost \
  --platform-option port=9000
```

The first command runs TPC-H in ClickHouse local mode. The second runs it with a custom data path. The third runs the same benchmark in server mode for comparison.

### CLI Arguments

#### Platform Selection
- `--platform clickhouse-local` - Use ClickHouse local mode via chDB
- `--platform clickhouse-server` - Use ClickHouse server (see [ClickHouse Server](clickhouse-server.md))
- `--platform clickhouse-cloud` - Use ClickHouse Cloud (see [ClickHouse Cloud](clickhouse-cloud.md))

```{deprecated} v0.2.0
The bare `clickhouse` selector has been removed and now raises an error naming the first-class replacements. The legacy colon syntax (`clickhouse:local`, `clickhouse:server`) still works but emits deprecation warnings. Use the first-class names above. See [Migration Guide](clickhouse-migration.md).
```

#### Local Mode Specific Arguments
- `--platform-option data_path=PATH` - Optional data path for file operations

#### Server Mode Arguments
- `--platform-option host=HOST` - ClickHouse server host
- `--platform-option port=PORT` - ClickHouse server port
- `--platform-option username=USER` - Username for server authentication
- `--platform-option password=PASS` - Password for server authentication
- `--platform-option secure=true` - Use TLS connection

## Performance Characteristics

### Local Mode
- **Memory Usage**: Lower baseline memory (~50-200MB)
- **Startup Time**: No network connection setup required
- **Query Execution**: Columnar engine for analytical workloads
- **Scalability**: Suited for small to medium datasets (< 10GB)
- **Concurrency**: Single-process, sequential query execution

### Server Mode
- **Memory Usage**: Higher baseline (server overhead)
- **Startup Time**: Network connection overhead
- **Query Execution**: Same columnar engine, distributed architecture available
- **Scalability**: Designed for large datasets (TB+)
- **Concurrency**: Multi-client support, parallel query execution

## When to Use Each Mode

### Use Local Mode When:
- **Development & Testing**: Quick benchmark development and validation
- **CI/CD Pipelines**: Automated testing without infrastructure setup
- **Data Analysis**: Interactive data exploration and analysis
- **Prototyping**: Rapid benchmark prototyping and iteration
- **Small to Medium Data**: Datasets under 10GB
- **Single-User Scenarios**: Personal analysis and development

### Use Server Mode When:
- **Production Benchmarking**: Large-scale production environment testing
- **Large Datasets**: Working with multi-TB datasets
- **Multi-User Access**: Shared benchmark environments
- **Enterprise Deployments**: Integration with existing ClickHouse infrastructure
- **Performance Testing**: Maximum throughput and scalability testing
- **Cluster Configurations**: Testing distributed ClickHouse setups


## Examples

### TPC-H Benchmark
```bash
benchbox run --platform clickhouse-local --benchmark tpch --scale 0.01

benchbox run --platform clickhouse-local --benchmark tpch --scale 1.0
```

Scale 0.01 is a small scale for development. Scale 1.0 is a medium scale for testing.

### ClickBench Benchmark
```bash
benchbox run --platform clickhouse-local --benchmark clickbench
```

### Custom Data Directory
```bash
benchbox run --platform clickhouse-local --benchmark tpch --scale 0.1 \
  --platform-option data_path=/path/to/benchmark/data
```

## Troubleshooting

### Common Issues and Solutions

#### 1. chDB Not Installed
```
Error: ClickHouse local mode requires chDB but it is not installed.
```

**Solution:**
```bash
uv add chdb
```

#### 2. Platform Not Supported
```
Error: chDB installation failed or not compatible with your platform
```

**Solution:**
- Ensure you're on macOS or Linux (x86_64/ARM64)
- Ensure your environment is synced with `uv sync --group dev`
- Check Python version: `uv run -- python --version` (3.11+ required)

#### 3. Memory Issues with Large Datasets
```
Error: Memory limit exceeded or system running out of memory
```

**Solution:**
- Use smaller scale factors for testing
- Switch to server mode for large datasets
- Monitor system memory usage

#### 4. Query Performance Issues
```
Queries running slower than expected in local mode
```

**Solution:**
- Local mode is optimized for small-medium datasets
- For large datasets or maximum performance, use server mode
- Consider data partitioning or smaller scale factors

### Getting Help

1. **Check Installation**: Verify chDB is properly installed
   ```bash
   uv run -- python -c "import chdb; print('chDB version:', chdb.chdb_version())"
   ```

2. **Verbose Output**: Run with verbose logging
   ```bash
   benchbox run --platform clickhouse-local --benchmark tpch --scale 0.01 -v
   ```

3. **Compare Deployments**: Test both platforms to isolate issues
   ```bash
   benchbox run --platform clickhouse-local --benchmark tpch --scale 0.01

   benchbox run --platform clickhouse-server --benchmark tpch --scale 0.01
   ```

## Advanced Usage

### Performance Tuning

While local mode has fewer tuning options than server mode, you can optimize performance:

```bash
benchbox run --platform clickhouse-local --benchmark tpch --scale 0.1

top -p $(pgrep -f benchbox)
```

The `top` command monitors memory usage during execution.

### Tuned Runs

`--tuning tuned` applies a curated template for TPC-H, SSB and TPC-DS:
MergeTree sort keys, plus monthly partitions on the TPC-H `LINEITEM` and
`ORDERS` tables. The same templates serve the local, server and cloud platforms
(`examples/tunings/clickhouse/`, packaged with BenchBox). For other
benchmarks there is no curated template, so `--tuning tuned` resolves to the
fallback. ClickHouse's "OLAP session pack" session settings apply to tuned runs
either way.

```bash
benchbox run --platform clickhouse-local --benchmark tpch --tuning tuned
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

### Integration with Other Tools

```bash
benchbox run --platform clickhouse-local --benchmark tpch --scale 0.01 --output ./clickhouse-run

find ./clickhouse-run/results -name '*.json'

for benchmark in tpch tpcds ssb; do
  echo "Running $benchmark..."
  benchbox run --platform clickhouse-local --benchmark "$benchmark" --scale 0.01
done
```

The first command writes JSON results under `./clickhouse-run/results`; `find` lists those files. The loop runs multiple
benchmarks.

## Technical Details

### Architecture

- **chDB Integration**: Uses official ClickHouse local engine
- **Connection Management**: Persistent connection maintains table state
- **Query Execution**: Direct SQL execution without network overhead
- **Result Processing**: Native Python data type conversion
- **Error Handling**: Comprehensive error messages with resolution guidance

### File Formats

Local mode supports all standard formats:
- CSV, TSV (tab-separated)
- Parquet (future enhancement)
- JSON (future enhancement)

### Limitations

- **Single Process**: No multi-process parallelism
- **Memory Bounds**: Limited by available system memory
- **No Clustering**: Single-node execution only
- **No Replication**: No built-in data redundancy

## Switching Between Platforms

### From Server to Local

```bash
benchbox run --platform clickhouse-server --benchmark tpch --scale 0.01 \
  --platform-option host=localhost \
  --platform-option port=9000

benchbox run --platform clickhouse-local --benchmark tpch --scale 0.01
```

The first command is the server mode command. The second is the local mode equivalent.

### From Local to Server

```bash
benchbox run --platform clickhouse-local --benchmark tpch --scale 0.01

benchbox run --platform clickhouse-server --benchmark tpch --scale 0.01 \
  --platform-option host=localhost \
  --platform-option port=9000
```

The first command is the current local mode command. The second is the server mode equivalent and requires a ClickHouse server.

For full migration details from the legacy `clickhouse` selector, see the [Migration Guide](clickhouse-migration.md).

## Contributing

To contribute to ClickHouse local mode support:

1. **Testing**: Run the ClickHouse local mode test suite
   ```bash
   uv run -- python -m pytest tests/unit/platforms/test_clickhouse_local.py -q
   ```

2. **Development**: Set up development environment
   ```bash
   uv sync --group dev
   uv add chdb
   ```

3. **Bug Reports**: Include system information and chDB version
   ```bash
   uv run -- python -c "import chdb, platform; print(f'chDB: {chdb.chdb_version()}, Platform: {platform.platform()}')"
   ```

## References

- [chDB Official Repository](https://github.com/chdb-io/chdb)
- [ClickHouse Documentation](https://clickhouse.com/docs)
- [BenchBox Platform Documentation](index.md)
- [TPC-H Benchmark Guide](../benchmarks/tpc-h.md)
