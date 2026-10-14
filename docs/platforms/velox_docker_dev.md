<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Velox Docker Dev Workflow

```{tags} guide, velox, gluten, docker, local-dev
```

The Apache Gluten Velox bundle jar is **Linux-only** - there are no prebuilt jars for macOS or Windows, and native builds on those hosts are not supported upstream. The `docker/velox/` image is therefore the primary local development path on macOS and Windows, and the recommended reproducible path on Linux.

This page covers prerequisites, build steps, both Docker workflows, arch selection, memory sizing, CI integration, and troubleshooting. See [Velox Platform](velox.md) for benchmark configuration and adapter options, and [Velox Jar Setup](velox_jar_setup.md) for tarball URLs and SHA verification.

## Prerequisites

| Requirement | Notes |
|---|---|
| Docker Desktop ≥ 4.x (macOS/Windows) or Docker Engine ≥ 24 (Linux) | Docker Compose V2 (`docker compose`, not `docker-compose`) required |
| `docker buildx` | Included with Docker Desktop; on Linux: `docker buildx install` |
| ~12 GB free disk | Image is ~3 GB; add headroom for build cache and benchmark data |
| Internet access at build time | The Dockerfile fetches the Gluten tarball from `downloads.apache.org` during `docker build` |

You do **not** need Java, PySpark, or the Gluten jar on the host. Everything is inside the image.

## Image Overview

The image (`docker/velox/Dockerfile`) layers on top of `apache/spark:4.0.2-scala2.13-java17-python3-ubuntu`:

1. Downloads and SHA-512-verifies the official Apache Gluten 1.6.0 release tarball.
2. Extracts `gluten-velox-bundle-spark4.0_2.13-linux_amd64-1.6.0.jar` to `/opt/gluten.jar`.
3. Builds a thin `ZstdJniCodec` jar so Spark can read `.zst`-compressed benchmark data.
4. Installs BenchBox from the local source tree with `uv pip install ... benchbox[velox]`.

The entrypoint (`docker/velox/entrypoint.sh`) supports three modes: `connect` (Spark-Connect server), `run` (one-shot benchmark), and `shell` (interactive debugging).

## Building the Image

Run all commands from the **project root** - the build context must include the full BenchBox source tree:

```bash
docker build \
  --platform linux/amd64 \
  -f docker/velox/Dockerfile \
  -t benchbox-velox:dev .

docker run --rm benchbox-velox:dev python3 -c \
  "from benchbox.platforms.velox import VeloxAdapter; print('import OK')"
```

The `docker build` command is a quick dev build for a single architecture, with no push. The `docker run` command verifies the build and confirms that Velox loads.

### Distribution Build (docker buildx)

```bash
docker buildx build \
  --platform linux/amd64 \
  -f docker/velox/Dockerfile \
  -t benchbox-velox:1.6.0 \
  --push .
```

### Build Arguments

| ARG | Default | Description |
|---|---|---|
| `GLUTEN_VERSION` | `1.6.0` | Gluten release to download |
| `TARGETARCH` | (auto-detected by buildx) | `amd64` or `arm64`; only `amd64` is supported by the official jar |

## Workflow A - Connect Mode

The host runs `benchbox`; the container runs the Gluten-enabled Spark-Connect server. This is the most flexible workflow: you get the full host BenchBox CLI, local result files, and a clean separation between the client and the Spark+Velox backend.

```bash
cd docker/velox
export BENCHBOX_DATA_DIR=/absolute/path/to/your/data
docker compose up -d velox-connect

docker compose ps velox-connect
docker compose logs -f velox-connect

benchbox run --platform velox:remote \
  --platform-option endpoint=sc://localhost:50051 \
  --benchmark tpch --scale 1.0

docker compose down velox-connect
```

The steps are: start the server detached, wait for the health check to pass, run `benchbox` on the host, and stop the server when done. `BENCHBOX_DATA_DIR` is required and has no default (see the data path contract below). The health check takes about 60-90 seconds on a cold JVM. Use `docker compose ps` to watch the status become "healthy" and `docker compose logs -f` to follow the startup logs.

### Data Path Contract

The Spark server runs inside the container and reads files by their **host-side absolute paths** (BenchBox sends paths over gRPC, not file contents). The compose file bind-mounts `$BENCHBOX_DATA_DIR` at the **same absolute path** inside the container, so host paths resolve identically server-side.

```text
Host:      /data/benchmark_runs/tpch_sf1/lineitem.parquet
Container: /data/benchmark_runs/tpch_sf1/lineitem.parquet
           └── same path, mounted :ro
```

`BENCHBOX_DATA_DIR` has **no default** in the compose file — export it as an **absolute path** before running compose. There is deliberately no fallback: any default that could live in the compose file or a checked-in `.env` can only ever be a directory-relative path (e.g. `./benchmark_runs`), and a relative container mount target can never equal an absolute host path, breaking the exact contract above. `make test-docker-up-velox` validates the variable is set and absolute before invoking compose; a bare `docker compose` run must export it manually:

```bash
export BENCHBOX_DATA_DIR=/mnt/benchdata
docker compose up -d velox-connect
```

Then run `benchbox` so that the paths it sends are under `/mnt/benchdata/`.

The mount is read-only (`:ro`). Spark's managed table warehouse is redirected to `/tmp/spark-warehouse` inside the container.

## Workflow B - All-in-One Runner

Run BenchBox entirely inside the container using an in-process (local) Gluten session. Simpler for one-shot benchmarks, CI jobs, and situations where you don't want to keep a server running.

`velox-runner` has no data mount of its own, but compose interpolates the entire file regardless of which service you target, so `BENCHBOX_DATA_DIR` still needs to be exported before running `velox-runner` too, or compose will warn that the variable is unset (harmless here since `velox-runner` has no mount referencing it — see [Data Path Contract](#data-path-contract) above).

```bash
cd docker/velox
export BENCHBOX_DATA_DIR=/absolute/path/to/your/data

docker compose run --rm velox-runner \
  --benchmark tpch --scale 0.01

docker compose run --rm velox-runner \
  --benchmark tpch --scale 1.0 --queries Q1,Q6,Q9,Q17

VELOX_OFFHEAP=24g SPARK_DRIVER_MEM=8g \
docker compose run --rm velox-runner \
  --benchmark tpcds --scale 10.0
```

The first command is a TPC-H SF 0.01 smoke test. The second runs TPC-H SF 1 with specific queries. The third runs TPC-DS SF 10 and raises the memory settings; see the sizing guidance below.

The entrypoint translates `run [args]` into the equivalent of:

```bash
benchbox run --platform velox:local \
  --platform-option gluten_jar_path=/opt/gluten.jar \
  --platform-option offheap_size=${VELOX_OFFHEAP} \
  [args]
```

## Arch Selection

Apache Gluten 1.6.0 publishes an **`amd64`-only** release jar in the official Spark 4.0 tarball. The checked-in Dockerfile and compose file default to `linux/amd64`.

| Host | Supported? | Notes |
|---|---|---|
| Intel Linux | Yes, natively | Full benchmark validity |
| Intel macOS | Yes, via Docker | Natively `linux/amd64`; benchmark timings valid |
| Apple Silicon (M1/M2/M3) | Smoke only | `linux/amd64` runs under Rosetta emulation; timings are invalid for benchmarking. Use a native x86_64 Linux host for real numbers. |
| Windows (Docker Desktop, Linux containers) | Smoke only | Same as Apple Silicon: emulated `linux/amd64`; timings invalid |

**Do not** run timing-sensitive benchmarks on emulated `linux/amd64`. The SIMD paths Velox relies on are emulated, which defeats the purpose of the benchmark.

To force a specific platform:

```bash
VELOX_DOCKER_PLATFORM=linux/amd64 docker compose up -d velox-connect
```

## Memory Sizing

Velox allocates native memory from a pool separate from the JVM heap. Both must fit within the container's memory limit.

| Variable | Default | Controls |
|---|---|---|
| `VELOX_OFFHEAP` | `8g` | `spark.memory.offHeap.size` - Velox's native pool |
| `SPARK_DRIVER_MEM` | `4g` | `spark.driver.memory` - JVM heap |

**Total container memory** ≈ `VELOX_OFFHEAP + SPARK_DRIVER_MEM + ~1 GB overhead`.

Recommended starting points by scale factor:

| Scale Factor | `VELOX_OFFHEAP` | `SPARK_DRIVER_MEM` | Total |
|---|---|---|---|
| SF 0.01-0.1 (smoke) | `4g` | `2g` | ~7 GB |
| SF 1 | `8g` | `4g` | ~13 GB |
| SF 10 | `16g` | `8g` | ~25 GB |
| SF 100 | `32g` | `16g` | ~49 GB |

If Docker Desktop has a memory cap (Settings → Resources), make sure it exceeds the total. Insufficient off-heap causes OOM errors or forces Velox to fall back to JVM execution silently.

```bash
VELOX_OFFHEAP=16g SPARK_DRIVER_MEM=8g docker compose up -d velox-connect
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `VELOX_IMAGE_TAG` | `dev` | Image tag used by compose |
| `VELOX_DOCKER_PLATFORM` | `linux/amd64` | Docker platform for compose services |
| `GLUTEN_VERSION` | `1.6.0` | Gluten version (Dockerfile build arg) |
| `VELOX_OFFHEAP` | `8g` | Off-heap memory budget for Velox |
| `SPARK_DRIVER_MEM` | `4g` | JVM driver heap |
| `SPARK_CONNECT_PORT` | `50051` | Host port exposed for the Spark-Connect server |
| `BENCHBOX_DATA_DIR` | **none — required** | Must be exported as an absolute path; bind-mounted at that same path inside the container. `make test-docker-up-velox` enforces this before invoking compose. |

## CI Integration

For CI pipelines where Docker is available, the all-in-one runner is the simplest integration:

```yaml
- name: Build Velox image
  run: |
    docker build \
      --platform linux/amd64 \
      -f docker/velox/Dockerfile \
      -t benchbox-velox:ci .

- name: Smoke test (TPC-H SF 0.01)
  run: |
    docker run --rm \
      -e VELOX_OFFHEAP=4g \
      -e SPARK_DRIVER_MEM=2g \
      benchbox-velox:ci \
      run --benchmark tpch --scale 0.01 --queries Q1,Q6
```

This is a GitHub Actions example.

The `tests/integration/platforms/test_velox_live.py` integration tests are gated behind the `live_integration` marker and expect to run inside the image:

```bash
docker run --rm benchbox-velox:ci \
  python -m pytest tests/integration/platforms/test_velox_live.py \
  -m live_integration -q
```

## Troubleshooting

### Container starts but never reaches "healthy"

The healthcheck probes `:50051` every 5 s with a 60 s start window. Spark Connect startup takes 30-90 s on a cold JVM. Check logs before concluding failure:

```bash
docker compose logs -f velox-connect
```

Common causes: insufficient memory (JVM crash), missing SPARK_VERSION file (build issue), or the Gluten jar not found at `/opt/gluten.jar`.

### `FileNotFoundException` in Spark logs (connect mode)

The client sent a path that doesn't exist inside the container. The bind-mount must cover the data directory at the same absolute path. See the [Data Path Contract](#data-path-contract) section above.

### `velox_active: false` after connection

The Gluten plugin didn't activate. In connect mode this usually means the pre-started server wasn't configured with Gluten - confirm the server was started via this compose file, not a plain Spark server. In local mode, check that `--platform-option gluten_jar_path=…` points to a valid jar file on Linux.

### Off-heap OOM / excessive JVM fallback

Increase `VELOX_OFFHEAP`. Insufficient off-heap causes Velox to either crash or force `RowToColumnar` inserts before every operator, eliminating the acceleration benefit.

### `ERROR: TARGETARCH=arm64 is unsupported`

The Dockerfile was built with `--platform linux/arm64`. Use `--platform linux/amd64` instead. The checked-in Dockerfile only supports the official amd64 release jar.

### Apple Silicon: benchmark timings look wrong

You are running `linux/amd64` under Rosetta emulation. The timings are not representative of native Velox performance. Use a native x86_64 Linux host for benchmark runs.

## See Also

- [Velox Platform](velox.md) - adapter options, CLI reference, usage examples
- [Velox Jar Setup](velox_jar_setup.md) - tarball URLs, SHA verification, known version table
- [Apache Gluten documentation](https://gluten.apache.org/)
