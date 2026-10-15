# docker/velox — Apache Gluten + Velox Image

Builds a `linux/amd64` image containing Apache Spark 4.0.2 with the Gluten Velox bundle jar pre-installed. The image supports two operating modes: a long-running Spark-Connect server for host-driven benchmarks, and a one-shot runner that executes `benchbox run` entirely inside the container.

## Build

Run from the **project root** (the build context must include the full BenchBox source tree). The first command is a quick single-architecture dev build. The second is a distribution build and requires `docker buildx` with a multi-arch builder:

```bash
docker build \
  --platform linux/amd64 \
  -f docker/velox/Dockerfile \
  -t benchbox-velox:dev .

docker buildx build \
  --platform linux/amd64 \
  -f docker/velox/Dockerfile \
  -t benchbox-velox:1.6.0 \
  --push .
```

The Dockerfile downloads the official Apache Gluten 1.6.0 release tarball, verifies the published SHA-512, and extracts the bundle jar. See [docs/platforms/velox_jar_setup.md](../../docs/platforms/velox_jar_setup.md) for tarball URLs and the version table.

## Services

| Service | Command | Purpose |
|---|---|---|
| `velox-connect` | `docker compose up -d velox-connect` | Start a Gluten-enabled Spark-Connect server on `:50051`. Run `benchbox` on the host, connected to the containerized backend. |
| `velox-runner` | `docker compose run --rm velox-runner [args]` | Run a complete BenchBox benchmark end-to-end inside the container. |

## Workflow A — Connect Mode (host client, container backend)

Start the server, wait for `docker compose ps velox-connect` to report `healthy` (about 90 seconds), then run `benchbox` on the host:

```bash
docker compose up -d velox-connect

docker compose ps velox-connect

benchbox run --platform velox:remote \
  --platform-option endpoint=sc://localhost:50051 \
  --benchmark tpch --scale 1.0
```

**Data path contract:** The server runs inside Docker and reads files by their host-side absolute paths. The compose file bind-mounts `$BENCHBOX_DATA_DIR` at the same absolute path inside the container. The host path BenchBox sends over gRPC must equal the in-container mount path, or every load fails with file-not-found.

`BENCHBOX_DATA_DIR` has **no default** — export it as an **absolute path** before running compose. There is no fallback because any working default here could only ever be a directory-relative path, and a relative container mount target can never equal an absolute host path, breaking the data path contract for exactly the case a default would exist to serve (a checked-in `docker/velox/.env` with a relative fallback was tried and reverted for this reason). `make test-docker-up-velox` validates the variable is set and absolute before invoking compose; a bare `docker compose` run must export it manually:

```bash
export BENCHBOX_DATA_DIR=/mnt/benchbox-data
docker compose up -d velox-connect
```

A nested default (`${VAR:-${OTHER}}`) and the `${VAR:?message}` required-variable form are both unsupported by [mocker](../../docs/operations/uat-framework.md), the Apple-silicon local Docker-compatible engine — mocker leaves either form unresolved instead of erroring or substituting — which is why the mount uses the bare `${BENCHBOX_DATA_DIR}` form with no modifier at all.

## Workflow B — All-in-one Runner

The first command runs TPC-H at scale factor 1 inside the container. The second runs selected TPC-DS queries.

```bash
docker compose run --rm velox-runner \
  --benchmark tpch --scale 1.0

docker compose run --rm velox-runner \
  --benchmark tpcds --scale 10.0 \
  --queries Q1,Q6,Q17
```

## Memory Sizing

Velox uses off-heap memory allocated independently of the JVM heap. Both must fit within the container memory limit (`docker run --memory`).

| Variable | Default | Controls |
|---|---|---|
| `VELOX_OFFHEAP` | `8g` | `spark.memory.offHeap.size` — Velox native memory pool |
| `SPARK_DRIVER_MEM` | `4g` | `spark.driver.memory` — JVM heap |

Total container memory needed: `VELOX_OFFHEAP + SPARK_DRIVER_MEM + ~1g overhead`. For SF 10 workloads, `24g` off-heap and `8g` driver is a reasonable starting point.

```bash
VELOX_OFFHEAP=24g SPARK_DRIVER_MEM=8g docker compose up -d velox-connect
```

## Arch Selection

Apache Gluten 1.6.0 publishes an `amd64`-only release jar. The Dockerfile and compose file default to `linux/amd64`.

- **Apple Silicon (arm64):** `linux/amd64` runs under Rosetta emulation. Acceptable for smoke testing and setup; timing numbers are invalid under emulation.
- **Intel Linux/Mac:** `linux/amd64` runs natively — valid for benchmarking.
- **For timing-valid benchmark runs on arm64:** use a native x86_64 Linux host.

To force `amd64` explicitly and override host architecture detection:

```bash
VELOX_DOCKER_PLATFORM=linux/amd64 docker compose up -d velox-connect
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `VELOX_IMAGE_TAG` | `dev` | Image tag used by compose |
| `VELOX_DOCKER_PLATFORM` | `linux/amd64` | Docker platform for compose services |
| `GLUTEN_VERSION` | `1.6.0` | Gluten version passed to the Dockerfile build arg |
| `VELOX_OFFHEAP` | `8g` | Off-heap memory budget for Velox |
| `SPARK_DRIVER_MEM` | `4g` | JVM driver heap |
| `SPARK_CONNECT_PORT` | `50051` | Host port for the Spark-Connect server |
| `BENCHBOX_DATA_DIR` | **none — required** | Host data directory, bind-mounted into the container at the same absolute path. Must be exported and absolute; `make test-docker-up-velox` enforces this before invoking compose. Compose interpolates the whole file, so `velox-runner` also needs this to resolve even though it has no data mount of its own. |

## Entrypoint Modes

The entrypoint dispatches on its first argument:

| Argument | Behavior |
|---|---|
| `connect` (default) | Start a Gluten-enabled Spark-Connect server in the foreground |
| `run [args]` | Run `benchbox run --platform velox:local ...` |
| `shell` | Drop into bash for debugging |
| `<anything else>` | `exec` the argument directly |

## Troubleshooting

**Container starts but `velox-connect` never reaches "healthy"**

The healthcheck probes `:50051` every 5 s with a 60 s start window. Spark Connect startup takes 30–60 s on a cold JVM. Check logs before assuming failure:

```bash
docker compose logs -f velox-connect
```

**`FATAL: could not detect Spark version from PySpark`**

The base image tag in the Dockerfile doesn't match a valid PySpark installation. Rebuild after confirming the `apache/spark` tag exists on Docker Hub.

**`ERROR: Apache Gluten ... publishes only an amd64 release jar`**

The build was attempted with `--platform linux/arm64`. The checked-in Dockerfile only supports `linux/amd64` for the official Gluten 1.6.0 jar. Use `--platform linux/amd64` or provide a custom arm64 jar.

### Off-heap OOM / excessive JVM fallback

Increase `VELOX_OFFHEAP`. Check `docker compose logs velox-connect` for `OutOfMemoryError` messages. A minimum of `8g` off-heap is required; `16–24g` for SF 10+ workloads.

**Path-mirroring failures (`FileNotFoundException` in Spark logs)**

The Spark server resolves paths sent by the client. If the host path BenchBox sends doesn't exist inside the container at the same absolute path, every file open fails. Set `BENCHBOX_DATA_DIR` to the absolute path of your data directory and re-start the service.
