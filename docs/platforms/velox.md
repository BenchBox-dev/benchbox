<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Apache Gluten + Velox Platform

```{tags} intermediate, guide, velox, gluten, sql-platform, spark-compatible, accelerated, local
```

Apache Gluten is a plugin to Apache Spark that offloads physical operators to [Velox](https://velox-lib.io/) - a vectorized, SIMD-optimized C++ query engine with unified off-heap memory. Spark retains its distributed scheduler, fault tolerance, and SQL/DataFrame APIs; Gluten intercepts the post-Catalyst physical plan and runs it natively in Velox. Reference performance (Gluten community, March 2024): **~3.3x faster** overall on TPC-H vs. Apache Spark, with individual queries up to **~23x** faster.

## Features

- **Native C++ execution** - Gluten routes Spark physical operators through Velox; unsupported operators transparently fall back to JVM execution.
- **Columnar shuffle** - `ColumnarShuffleManager` is mandatory for meaningful acceleration (the adapter enforces this).
- **Velox activity probe** - `get_platform_info()` runs `EXPLAIN SELECT count(*) FROM range(10)` and returns `velox_active: true/false` so you can confirm the plugin is live.
- **Engine observation** - `platform.execution_engine.observed` records `velox` or `standard` from the existing probe with `observed_source="explain"`. These observed values cannot be selected; `execution_engine=default` preserves platform behavior.
- **Plan annotation** - `get_query_plan()` labels every plan with `Velox native execution: YES/NOT DETECTED` and flags real JVM fallbacks (without false-positives on `VeloxColumnarToRow`).
- **Two deployment modes** - `local` (in-process SparkSession) and `remote` (connect to a pre-started Gluten-enabled Spark-Connect server).
- **Docker-first** on macOS / Windows - the provided image tracks the official Apache release tarball and defaults to `linux/amd64`.

## Platform Requirements - Linux Only

The Gluten Velox bundle jar is **Linux-only**. There are no prebuilt jars for macOS or Windows, and native builds on those hosts are **not supported**.

- **Linux x86_64:** fully supported with the official Apache Gluten 1.6.0 release jar.
- **Linux aarch64:** BenchBox can use a self-built/custom jar, but Apache Gluten 1.6.0 does not ship an official prebuilt arm64 Spark 4.0 jar.
- **macOS (Apple Silicon or Intel):** use Docker or a remote Linux host. The checked-in Docker workflow defaults to `linux/amd64`; on Apple Silicon that means emulation, which is acceptable for smoke testing but invalid for benchmark timings.
- **Windows:** use Docker Desktop with the Linux-container backend.

## Quick Start

### Option A - Docker (recommended on macOS/Windows)

Build the image first. Then pick one of two workflows. (a) All-in-one: run the benchmark inside the container with
`docker compose run`. (b) Host-driver and container-backend: start a Gluten-enabled Spark-Connect server, then run
`benchbox` on the host.

```bash
cd docker/velox
docker build \
    --platform linux/amd64 \
    -t benchbox-velox:dev ../..

docker compose run --rm velox-runner --benchmark tpch --scale 0.1

docker compose up -d velox-connect
benchbox run --platform velox:remote \
    --platform-option endpoint=sc://localhost:50051 \
    --benchmark tpch --scale 0.1
```

See [Velox Docker Dev Workflow](velox_docker_dev.md) for a full walkthrough of both Docker workflows, arch selection, memory sizing, and CI integration. See [Velox Jar Setup](velox_jar_setup.md) for the published release tarball and verification steps.

### Option B - Native Linux

Install the Velox extra, which pulls in `pyspark[connect]>=3.5.0`. Also download the Gluten Velox bundle jar for your
Spark 4.0, Scala 2.13 and architecture (see `docs/platforms/velox_jar_setup.md` for release tarballs and verification
steps). The `benchbox run` command then runs locally with Gluten wired into an in-process SparkSession.

```bash
uv add benchbox --extra velox

benchbox run --platform velox --benchmark tpch --scale 0.1 \
    --platform-option gluten_jar_path=/opt/gluten-velox-bundle-spark4.0_2.13-linux_amd64-1.6.0.jar \
    --platform-option offheap_size=8g
```

## Configuration

### CLI Options

```bash
benchbox run --platform velox:local --benchmark tpch --scale 1.0 \
    --platform-option gluten_jar_path=/opt/gluten.jar \
    --platform-option offheap_size=16g \
    --platform-option driver_memory=8g \
    --platform-option shuffle_partitions=200 \
    --table-format parquet
```

### Configuration Options

Options in the "CLI" column are passed as `--platform-option KEY=VALUE`; the
rest are adapter constructor parameters only.

| Option | CLI | Default | Description |
|--------|-----|---------|-------------|
| `deployment` | yes | `local` | Deprecated alias of `--platform velox:local\|remote`: `local` (in-process SparkSession, Linux only) or `remote` (connect to Spark-Connect) |
| `endpoint` | yes | `sc://localhost:50051` | Spark-Connect endpoint for `remote` mode |
| `gluten_jar_path` | yes (alias: `jar`) | - | Absolute path to the Gluten bundle jar (required for `local` mode) |
| `offheap_size` | yes | `8g` | `spark.memory.offHeap.size` for Velox (sized separately from JVM heap) |
| `driver_memory` | yes | `4g` | JVM driver heap |
| `shuffle_partitions` | yes | `200` | `spark.sql.shuffle.partitions` |
| `adaptive_enabled` | yes | `true` | Enable Adaptive Query Execution (AQE) |
| `gluten_version` | no | `1.6.0` | Gluten version (informational, surfaced in `platform_info`) |
| `app_name` | no | `BenchBox-Velox` | Spark application name |
| `spark_config` | no | `{}` | Extra Spark configuration (dict). `spark.shuffle.manager` cannot be overridden in `local` mode. |
| `disable_cache` | no | `true` | Disables `spark.sql.inMemoryColumnarStorage.enabled` for clean timings |

The table format is a first-class Velox platform option (`parquet`, `orc`,
`delta`, `iceberg`, `hudi`; default `parquet`), set via `--platform-option
table_format=<format>` or the `table_format` config key. For `delta`,
`iceberg`, and `hudi` the adapter emits the matching SQL extension plus
session-catalog wiring so Gluten has a readable table to accelerate. Those
extensions require the corresponding Delta/Iceberg/Hudi JARs on the Spark
classpath; the conf alone is necessary but not sufficient without the jars.
Supply them via `--platform-option lakehouse_jars=<jar1,jar2>` (local paths,
remote URIs, or Maven coordinates; required for lakehouse formats in `local`
mode, where missing jars fail session creation instead of failing later with
a class-not-found error). In `remote` mode the server owns its classpath.

### Mandatory Gluten Configuration (Local Mode)

The adapter sets these automatically when `deployment="local"`:

- `spark.plugins = org.apache.gluten.GlutenPlugin`
- `spark.memory.offHeap.enabled = true`
- `spark.memory.offHeap.size = <offheap_size>`
- `spark.shuffle.manager = org.apache.spark.shuffle.sort.ColumnarShuffleManager`
- `spark.jars = <gluten_jar_path>`
- `spark.driver.extraClassPath = <gluten_jar_path>`
- `spark.executor.extraClassPath = <gluten_jar_path>`

The `extraClassPath` entries are required because the Gluten plugin class is loaded by `SparkContext.initializeSparkContext` before `spark.jars` promotions reach the executor classpath. Without them the plugin silently no-ops.

Overriding `spark.shuffle.manager` via `spark_config` raises `ValueError` - `ColumnarShuffleManager` is required for shuffle acceleration.

## Usage Examples

### Local Mode (Linux or inside Docker)

The first command runs TPC-H SF1 with 16 GB off-heap memory. The second runs TPC-DS SF10 with specific queries. The
third is a dry-run preview.

```bash
benchbox run --platform velox --benchmark tpch --scale 1.0 \
    --platform-option gluten_jar_path=/opt/gluten.jar --platform-option offheap_size=16g

benchbox run --platform velox --benchmark tpcds --scale 10.0 \
    --queries Q1,Q6,Q17 \
    --platform-option gluten_jar_path=/opt/gluten.jar --platform-option offheap_size=24g \
    --platform-option driver_memory=12g --platform-option shuffle_partitions=400

benchbox run --dry-run ./preview --platform velox --benchmark tpch --scale 1.0
```

### Remote Mode (Spark-Connect)

First start a Gluten-enabled Spark-Connect server. Then run `benchbox` on the host, connecting to the container.

```bash
cd docker/velox
docker compose up -d velox-connect

benchbox run --platform velox:remote --benchmark tpch --scale 1.0 \
    --platform-option endpoint=sc://localhost:50051
```

The adapter does **not** auto-start a server - if `sc://host:port` is unreachable, connection fails with a clear error.

### Accelerated-Spark Tier Comparison

The commands run an Apache Spark baseline, the same workload with Gluten + Velox, and LakeSail Sail (Rust/DataFusion).
The last command compares the three results.

```bash
benchbox run --platform spark --benchmark tpch --scale 10.0

benchbox run --platform velox --benchmark tpch --scale 10.0 \
    --platform-option gluten_jar_path=/opt/gluten.jar --platform-option offheap_size=24g

benchbox run --platform lakesail --benchmark tpch --scale 10.0

benchbox compare spark_tpch_sf10.json velox_tpch_sf10.json lakesail_tpch_sf10.json
```

## Python API

```python
from benchbox import TPCH
from benchbox.platforms.velox import VeloxAdapter

adapter = VeloxAdapter(
    deployment="local",
    gluten_jar_path="/opt/gluten-velox-bundle-spark4.0_2.13-linux_amd64-1.6.0.jar",
    offheap_size="16g",
    driver_memory="8g",
    shuffle_partitions=200,
    table_format="parquet",
)

benchmark = TPCH(scale_factor=1.0)
benchmark.generate_data()
adapter.load_benchmark(benchmark)
results = adapter.run_benchmark(benchmark)

info = adapter.get_platform_info(connection=adapter._spark_session)
print("Velox active:", info["velox_active"])
```

The last two lines confirm that native execution happened.

## Architecture

```
┌────────────────────┐
│  PySpark Client     │  (user-facing SQL / DataFrame API)
└──────────┬─────────┘
           │
┌──────────▼─────────┐
│  Spark Catalyst     │  (parse, analyze, logical optimize)
└──────────┬─────────┘
           │   physical plan
┌──────────▼─────────┐       fallback
│  Gluten Plugin      │─────────────────┐
│  (plan translation) │                 │
└──────────┬─────────┘                 │
           │                           │
┌──────────▼─────────┐       ┌─────────▼──────────┐
│  Velox (C++)        │       │  Spark JVM Engine   │
│  Vectorized exec    │       │  (unsupported ops)  │
└────────────────────┘       └────────────────────┘
```

- **Plan translation:** Gluten intercepts the physical plan after Catalyst and rewrites supported operators into `VeloxColumnar*` nodes.
- **Shuffle:** `ColumnarShuffleManager` exchanges Arrow-format columnar batches, avoiding per-row serialization.
- **Off-heap memory:** Velox allocates from a separate native pool (`spark.memory.offHeap.size`), independent of the JVM heap.
- **Fallback:** Operators that Gluten can't translate run in the JVM, with `ColumnarToRow` / `RowToColumnar` nodes bridging format boundaries. Fallback is functional but forfeits acceleration.

### Tuning Support

| Tuning Type | Supported | Notes |
|-------------|-----------|-------|
| Partitioning | Yes | `PARTITIONED BY` added to `CREATE TABLE` |
| Sorting | No | Plain Parquet/ORC tables have no DDL sort key; handled at query time via AQE/CBO |
| Clustering | No | |
| Primary Keys | Informational | Not enforced (Spark behavior) |
| Foreign Keys | Informational | Not enforced (Spark behavior) |

## Comparison: Velox vs. Spark vs. LakeSail

| Dimension | Apache Gluten + Velox | Apache Spark | LakeSail Sail |
|-----------|-----------------------|--------------|---------------|
| Execution engine | Velox (C++, vectorized, SIMD) | JVM (Scala/Java) | DataFusion (Rust) |
| API | PySpark SQL / DataFrame | PySpark SQL / DataFrame | PySpark SQL / DataFrame |
| SQL dialect | Spark SQL | Spark SQL | Spark SQL |
| Performance (TPC-H community numbers) | ~3.3x vs. Spark | Baseline | ~4x vs. Spark (SF100) |
| Protocol | In-process or Spark-Connect | Native | Spark-Connect only |
| Fault tolerance / scheduling | Spark's (unchanged) | Native | Sail |
| Operator coverage | Most OLAP ops; JVM fallback for unsupported | Full | DataFusion catalogue |
| Local platform support | Linux only (Docker elsewhere) | All | All |
| Maturity | Apache project | Battle-tested | Emerging |

### When to Use Velox

**Use Velox when:**
- You already run Spark and want native acceleration without leaving the Spark API.
- Your workload is OLAP-shaped (scans, filters, joins, aggregations on Parquet/ORC).
- You want to benchmark the "accelerated-Spark" tier against Spark, LakeSail, DataFusion, and cloud engines.
- Linux hosts (or Docker) are acceptable.

**Use Apache Spark instead when:**
- You need Delta Lake / Iceberg / Hudi write paths Gluten doesn't yet accelerate.
- You rely on UDFs or Spark operators without Velox equivalents (the JVM fallback will remove the acceleration benefit).
- You're on a non-Linux host and can't run Docker.

**Use LakeSail instead when:**
- You want a pure-Rust replacement rather than a Spark plugin.
- You need DataFrame-mode benchmarks on the same engine (Velox adapter is SQL-only).

## Troubleshooting

### `velox_active` is `false`

The probe `EXPLAIN SELECT count(*) FROM range(10)` did not produce `VeloxColumnar*` nodes. Most likely causes:

1. The Gluten jar is not on the classpath - check `platform_info.gluten_jar` points to the right filename and the file exists.
2. `spark.plugins` was overridden via `spark_config`. Remove the override.
3. `spark.shuffle.manager` was overridden - the adapter rejects this in local mode but a pre-started remote server may have it mis-configured.
4. You are running on macOS/Windows natively rather than Linux/Docker. The jar will load but Velox will crash or silently fall back.

### `JVM fallback: DETECTED` in query plan

`ColumnarToRow` or `RowToColumnar` nodes appeared in the plan (excluding `VeloxColumnarToRow`, which is the native result-materialization node - not a fallback). Some operators in your query aren't supported by Velox and ran in the JVM. Check [Gluten's operator support matrix](https://gluten.apache.org/) and consider rewriting the query, or accept partial acceleration.

### `gluten_jar_path is required for local deployment mode`

Pass `--platform-option gluten_jar_path=/path/to/gluten-velox-bundle-*.jar` (alias: `--platform-option jar=…`) or set the `gluten_jar_path` config key. On macOS/Windows, use the Docker workflow instead.

### `Cannot connect to Spark-Connect server at sc://...`

The server isn't running or isn't reachable. The adapter does not auto-start servers.

```bash
cd docker/velox
docker compose up -d velox-connect
docker compose logs velox-connect
```

The `logs` command shows startup errors.

### Out-of-memory / excessive JVM fallback

Velox uses off-heap memory. Increase `--platform-option offheap_size=<size>` (default 8g). If you see `RowToColumnar` inserted before every operator, the Gluten plugin may not have loaded - check the probe above.

## See Also

- [Velox Docker Dev Workflow](velox_docker_dev.md) - Docker workflows, arch selection, memory sizing, CI integration
- [Velox Jar Setup](velox_jar_setup.md) - release tarball URLs, checksum verification, and extracted jar names
- [Apache Spark Platform](spark.md) - JVM baseline for comparison
- [LakeSail Sail Platform](lakesail.md) - Rust/DataFusion alternative
- [Apache Gluten](https://gluten.apache.org/) - upstream project documentation
- [Velox](https://velox-lib.io/) - upstream execution engine documentation
