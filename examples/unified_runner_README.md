# Unified Runner Example - README

**Run benchmark on allsupported platforms using `unified_runner.py`**

This guide shows how to run each of the 12 supported benchmarks using the `unified_runner`. All examples use the same tool with different `--benchmark` flags.

The `unified_runner.py` script provides a fully worked **single-file** example of using the core BenchBox modules. The behavior is very similar to the CLI for comparability.

## Quick Start

Run from the `examples` directory. Replace `tpch` with any benchmark name listed below.

```bash
cd examples

python unified_runner.py --platform duckdb --benchmark tpch --scale 0.01 --phases power
```

---

## All Supported Benchmarks

### 1. TPC-H (Standard OLAP)
**Best for:** General analytical query performance, vendor comparison

The examples below run a basic power test, a tuned run, a run of queries 1, 6 and 12 only, and a dry run that previews the plan without executing (output goes to `./preview`).

```bash
python unified_runner.py --platform duckdb --benchmark tpch --scale 0.1 --phases power

python unified_runner.py --platform duckdb --benchmark tpch --scale 1.0 --phases power --tuning tuned

python unified_runner.py --platform duckdb --benchmark tpch --scale 0.1 --phases power --queries 1,6,12

python unified_runner.py --platform duckdb --benchmark tpch --scale 1.0 --phases power --dry-run ./preview
```

**Scale Factor Guide:**
- `0.01` = ~10MB (< 1 min, testing)
- `0.1` = ~100MB (2-5 min, development)
- `1.0` = ~1GB (10-15 min, standard)
- `10.0` = ~10GB (30-60 min, large)

---

### 2. TPC-DS (Complex Analytics)
**Best for:** Complex analytical workloads, BI reporting

```bash
python unified_runner.py --platform duckdb --benchmark tpcds --scale 0.1 --phases power

python unified_runner.py --platform duckdb --benchmark tpcds --scale 1.0 --phases power --tuning tuned
```

**Scale Factor Guide:**
- `0.01` = ~5MB (< 1 min)
- `0.1` = ~50MB (3-10 min)
- `1.0` = ~500MB (20-40 min)
- `10.0` = ~5GB (1-3 hours)

---

### 3. TPC-DI (Data Integration)
**Best for:** ETL performance, data warehouse loading

```bash
python unified_runner.py --platform duckdb --benchmark tpcdi --scale 0.01 --phases generate,load

python unified_runner.py --platform duckdb --benchmark tpcdi --scale 1.0 --phases generate,load
```

**Note:** TPC-DI focuses on data loading rather than query execution. The scale 0.01 command is the standard test (data loading and transformation); the scale 1.0 command is the full workflow.

**Scale Factor Guide:**
- `0.01` = Tiny (< 1 min)
- `1.0` = Small (5-10 min)
- `10.0` = Medium (30-60 min)

---

### 4. SSB (Star Schema Benchmark)
**Best for:** Star schema queries, dimensional modeling

The power test runs 13 queries.

```bash
python unified_runner.py --platform duckdb --benchmark ssb --scale 0.1 --phases power

python unified_runner.py --platform duckdb --benchmark ssb --scale 1.0 --phases power --tuning tuned
```

**Scale Factor Guide:**
- `0.01` = ~5MB (< 1 min)
- `1.0` = ~500MB (5-10 min)
- `10.0` = ~5GB (30-60 min)

---

### 5. ClickBench (Web Analytics)
**Best for:** Real-time analytics, web/log data

The power test runs 43 queries.

```bash
python unified_runner.py --platform duckdb --benchmark clickbench --scale 0.01 --phases power

python unified_runner.py --platform duckdb --benchmark clickbench --scale 0.1 --phases power
```

**Scale Factor Guide:**
- `0.01` = ~1M rows (~100MB, < 1 min)
- `0.1` = ~10M rows (~1GB, 3-5 min)
- `1.0` = ~100M rows (~10GB, 15-30 min)

---

### 6. AMPLab Big Data Benchmark
**Best for:** Big data query patterns, Hadoop/Spark comparison

The power test covers three query types: scan, aggregation and join.

```bash
python unified_runner.py --platform duckdb --benchmark amplab --scale 0.1 --phases power

python unified_runner.py --platform duckdb --benchmark amplab --scale 1.0 --phases power
```

**Scale Factor Guide:**
- `0.01` = Tiny (< 1 min)
- `1.0` = Small (2-5 min)
- `10.0` = Medium (10-20 min)

---

### 7. H2O.ai Database Benchmark
**Best for:** Data science workloads, GroupBy operations

The power test runs GroupBy-heavy queries.

```bash
python unified_runner.py --platform duckdb --benchmark h2odb --scale 0.1 --phases power
```

**Scale Factor Guide:**
- `0.01` = ~100K rows (< 1 min)
- `1.0` = ~10M rows (2-5 min)
- `10.0` = ~100M rows (10-30 min)

---

### 8. Join Order Benchmark
**Best for:** Query optimizer testing, join algorithm evaluation

The power test runs complex multi-way joins.

```bash
python unified_runner.py --platform duckdb --benchmark joinorder --scale 1 --phases power
```

**Scale Factor Guide:**
- `1.0` is the only supported public `joinorder` scale.
- First use downloads and verifies the canonical IMDb 2013 JOB Parquet package.
- Use the internal `joinorder_synthetic` benchmark only for synthetic loader or schema smoke tests.

---

### 9. Read Primitives (Basic OLAP)
**Best for:** Quick smoke tests, basic functionality validation

The power test runs simple analytical queries. The second command, limited to queries 1, 2 and 3, is a quick CI/CD check.

```bash
python unified_runner.py --platform duckdb --benchmark read_primitives --scale 0.01 --phases power

python unified_runner.py --platform duckdb --benchmark read_primitives --scale 0.01 --phases power --queries 1,2,3
```

**Scale Factor Guide:**
- `0.01` = ~10MB (< 10 seconds, CI/CD)
- `0.1` = ~100MB (< 1 min, smoke testing)
- `1.0` = ~1GB (2-5 min, baseline)

---

### 10. Write Primitives (Write Operations)
**Best for:** Write performance testing, MERGE/UPSERT testing, transaction validation

The first command tests write operations at a small scale; the second runs larger write tests.

```bash
python unified_runner.py --platform duckdb --benchmark write_primitives --scale 0.01 --phases generate,load

python unified_runner.py --platform duckdb --benchmark write_primitives --scale 1.0 --phases generate,load
```

**Scale Factor Guide:**
- `0.01` = ~10MB TPC-H data (< 1 min)
- `1.0` = ~1GB TPC-H data (2-5 min)
- `10.0` = ~10GB TPC-H data (10-30 min)

---

### 11. TPC-H Havoc (Optimizer Stress Test)
**Best for:** Query optimizer validation, edge case testing

The power test runs modified TPC-H queries designed to stress optimizers.

```bash
python unified_runner.py --platform duckdb --benchmark tpchavoc --scale 0.1 --phases power
```

**Note:** Some queries may intentionally fail or perform poorly to test optimizer robustness

**Scale Factor Guide:**
- `0.01` = ~10MB (< 1 min)
- `0.1` = ~100MB (3-10 min)
- `1.0` = ~1GB (15-30 min)

---

## Platform Options

Use `--platform` to run on different databases. `duckdb` runs in memory or against a persistent file, and `sqlite` uses a SQLite database; both need no credentials. `clickhouse-server` targets a self-hosted ClickHouse server. `databricks` (SQL Warehouse), `bigquery`, `snowflake` and `redshift` are cloud platforms and require credentials.

```bash
--platform duckdb
--platform sqlite

--platform clickhouse-server

--platform databricks
--platform bigquery
--platform snowflake
--platform redshift
```

### Cloud Platform Examples

Credentials required by each example:

- Databricks: `DATABRICKS_TOKEN` and `DATABRICKS_HOST`.
- BigQuery: `GOOGLE_APPLICATION_CREDENTIALS` or `BIGQUERY_PROJECT`. The example uses `--dry-run` to preview the run before spending credits.
- Snowflake: Snowflake account credentials.

```bash
python unified_runner.py \
  --platform databricks \
  --benchmark tpch \
  --scale 1.0 \
  --phases power

python unified_runner.py \
  --platform bigquery \
  --benchmark tpch \
  --scale 0.1 \
  --phases power \
  --dry-run ./preview

python unified_runner.py \
  --platform snowflake \
  --benchmark tpch \
  --scale 1.0 \
  --phases power
```

---

## Test Execution Types

Use `--phases` to control what executes. The examples below show, in order: a power test (sequential query execution), a throughput test (concurrent streams), a maintenance test (data modifications), a combined run, data generation only, loading data without running queries, and the full workflow.

```bash
--phases power

--phases throughput --streams 4

--phases maintenance

--phases warmup,power,throughput

--phases generate

--phases load

--phases generate,load,power
```

---

## Common Options

### Query Selection
Use a comma-separated list for specific queries and a dash for a range.

```bash
--queries 1,6,12,14

--queries 1-10
```

### Tuning
`tuned` applies optimizations from `tunings/{platform}/{benchmark}_tuned.yaml`, `notuning` is the baseline without optimizations, and a path selects a custom tuning file.

```bash
--tuning tuned

--tuning notuning

--tuning /path/to/custom_tuning.yaml
```

### Output & Export
In order: preview without execution, export results in several formats, set the output directory, quiet mode (minimal console output), verbose mode (detailed logging), and very verbose.

```bash
--dry-run ./preview_output

--formats json,csv,html

--output-dir ./my_results

--quiet

--verbose

-vv
```

### Data Management
In order: force data regeneration, compress generated data, and set the data location.

```bash
--force

--compress

--output /path/to/data
```

---

## Complete Examples

### Development Workflow
Steps: a quick smoke test, a test of a specific benchmark, a preview of the cloud run, then execution on the cloud.

```bash
python unified_runner.py --platform duckdb --benchmark primitives --scale 0.01 --phases power

python unified_runner.py --platform duckdb --benchmark tpch --scale 0.1 --phases power

python unified_runner.py --platform databricks --benchmark tpch --scale 1.0 --dry-run ./preview

python unified_runner.py --platform databricks --benchmark tpch --scale 1.0 --phases power
```

### Performance Testing
Run a baseline without tuning, then an optimized run with tuning, and compare the results (see the `result_analysis.py` example).

```bash
python unified_runner.py --platform duckdb --benchmark tpch --scale 1.0 --phases power \
  --tuning notuning --output-dir ./results/baseline

python unified_runner.py --platform duckdb --benchmark tpch --scale 1.0 --phases power \
  --tuning tuned --output-dir ./results/tuned

```

### CI/CD Integration
This is a fast validation test that finishes in under 30 seconds.

```bash
python unified_runner.py --platform duckdb --benchmark primitives --scale 0.01 \
  --phases power --quiet --formats json --output-dir ./ci_results
```

### Multi-Platform Comparison
This runs the same benchmark on three platforms.

```bash
for platform in duckdb clickhouse-local databricks; do
  python unified_runner.py --platform $platform --benchmark tpch --scale 1.0 \
    --phases power --output-dir ./comparison/$platform
done
```

---

## Getting Help

The commands below show all options, list available platforms, list available benchmarks, and show platform-specific help.

```bash
python unified_runner.py --help

python unified_runner.py --list-platforms

python unified_runner.py --list-benchmarks

python unified_runner.py --platform databricks --help
```

---

## Next Steps

- **New users:** Start with [getting_started/](getting_started/) examples
- **Learn patterns:** See [PATTERNS.md](PATTERNS.md) for common workflows
- **Find examples:** Browse [INDEX.md](INDEX.md) by goal/platform/benchmark
- **Feature examples:** Explore [features/](features/) for specific capabilities

---

## Troubleshooting

**Import errors:** install the package, optionally with a platform extra.
```bash
uv add benchbox
uv add benchbox --extra databricks
```

**Platform not available:** check the required dependencies, then install platform extras (`cloud` covers all cloud platforms; `databricks` covers a single platform).
```bash
python unified_runner.py --list-platforms

pip install "benchbox[cloud]"
pip install "benchbox[databricks]"
```

**Credentials missing:** set the required environment variables for your platform (Databricks shown).
```bash
export DATABRICKS_TOKEN="your-token"
export DATABRICKS_HOST="https://your-workspace.cloud.databricks.com"
```

**Performance issues:**
- Start with small scale factors (0.01 or 0.1)
- Use `--dry-run` to preview before execution
- Enable tuning with `--tuning tuned`
- Check system resources (RAM, CPU)

---

**Remember:** `unified_runner.py` supports ALL benchmarks and ALL platforms. You don't need separate scripts for each combination!
