<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Docker Integration Tests

```{tags} contributor, guide, testing
```

Run real integration tests against database engines in Docker containers. No cloud credentials needed.

## Overview

Docker integration tests execute real queries against real database engines running locally in Docker containers. Unlike stub-based smoke tests (which only verify adapter wiring), these tests catch:

- SQL dialect incompatibilities
- Data type mapping errors
- Connection handling bugs
- Schema creation/cleanup failures
- Query execution correctness

**Cost**: Free (Docker only, no cloud credentials).

## Supported Platforms

| Platform | Docker Image | Ports | Protocol |
|----------|-------------|-------|----------|
| ClickHouse | `clickhouse/clickhouse-server:25.8@sha256:0152dd511befe6a2c2ef53e930726179669b08116da78500b37c51c96ff5ee77` | 9000, 8123 | Native TCP / HTTP |
| Trino | `trinodb/trino:480@sha256:1565e8cac299a32dd9177a4da2d748da4ceb9f1560a9c409d1d18fd72ea5253e` | 18080 -> 8080 | HTTP (DBAPI) |
| Presto | `prestodb/presto:0.297` | 18081 -> 8080 | HTTP (DBAPI) |
| PostgreSQL | `public.ecr.aws/docker/library/postgres:18` | 5432 | PostgreSQL wire |
| StarRocks | `starrocks/allin1-ubuntu:3.5.16` | 19030 -> 9030, 18040 -> 8040 | MySQL / HTTP Stream Load |
| Doris | `apache/doris:4.0.3-all-slim` | 19031 -> 9030, 18030 -> 8030 | MySQL / HTTP Stream Load |
| Databend | `datafuselabs/databend` + MinIO | 8000, 19000 -> 9000, 19001 -> 9001 | HTTP/DSN |
| InfluxDB | `influxdb:3-core` | 8181 | HTTP (FlightSQL) |

## CI image registries

PostgreSQL compose pulls the official image from Amazon ECR Public; the
nightly PostgreSQL service uses the same registry with its existing version 17.
Nightly T3 and Docker integration jobs pull the manifest-list digests pinned in
compose from `ghcr.io/benchbox-dev/clickhouse:25.8@sha256:0152dd511befe6a2c2ef53e930726179669b08116da78500b37c51c96ff5ee77`
and `ghcr.io/benchbox-dev/trino:480@sha256:1565e8cac299a32dd9177a4da2d748da4ceb9f1560a9c409d1d18fd72ea5253e`.
The nightly CedarDB throughput job uses `ghcr.io/benchbox-dev/cedardb` with the
unchanged SHA-256 digest from its compose file.

These jobs wait for `mirror-ci-images.yml`, which requires digest-pinned source
references and checks GHCR by that digest on every run. A successful check
records the pinned digest in the run summary without querying Docker Hub.
Only a `MANIFEST_UNKNOWN` response triggers a Skopeo copy from the same pinned
Docker Hub digest; all other registry errors fail closed. Copies preserve all
architectures and manifest digests, then the workflow verifies the GHCR
manifest. A missing mirror needs one successful upstream copy; a rate limit
during that copy fails the mirror job rather than falling back in the consumer.
No Docker Hub credentials are required.

The mirror job uses `GITHUB_TOKEN` with `packages: write`. Consumers log into
GHCR with `packages: read`, so newly created private packages can be pulled
without making them public. The organization must permit package creation,
and existing packages must grant this repository Actions access. Mirror
publication happens only when a calling workflow runs.

Local vendor stacks retain their upstream defaults. `CLICKHOUSE_IMAGE`,
`TRINO_IMAGE`, and `CEDARDB_IMAGE` override the full image reference when a
different registry is needed. The workflow contract tests compare ClickHouse
and Trino CI overrides in both consumers with the complete compose `tag@digest`
references. When an upstream version or digest changes, update the compose
reference and corresponding CI overrides together; keep CedarDB's digest
unchanged across registries. Other compose stacks are not migrated by this CI
configuration.

CI and Documentation spellcheck install `codespell[toml]==2.4.3` with `uvx`
instead of building a Docker action. This matches the version in the replaced
action's requirements at commit
`e3258cca84ce02b0bb36a3d41f2c18f719a0cc1a`; the ignore file, skip patterns, and
non-blocking job policy remain unchanged.

## Quick Start

```bash
make test-docker-clickhouse

make test-docker-all
```

The first command tests a single platform: it starts Docker, runs the tests, and stops Docker. The second tests all Docker platforms sequentially.

## Available Makefile Targets

| Target | Description |
|--------|-------------|
| `make test-docker-<platform>` | Full lifecycle: start service, run tests, stop service |
| `make test-docker-up-<platform>` | Start service only (for interactive debugging) |
| `make test-docker-down-<platform>` | Stop service and remove volumes |
| `make test-docker-all` | Run all Docker platform tests sequentially |
| `make test-docker-up-all` | Start all Docker services |
| `make test-docker-down-all` | Stop all Docker services |

Replace `<platform>` with: `clickhouse`, `trino`, `presto`, `postgresql`, `starrocks`, `doris`, `databend`, `influxdb`.

## Running Tests Manually

If you prefer to manage Docker services separately:

```bash
make test-docker-up-clickhouse

uv run -- python -m pytest -m "live_clickhouse" --tb=short -v

make test-docker-down-clickhouse
```

The steps are: start the service, run the tests with verbose output, and stop the service when done.

Or using pytest markers directly:

```bash
uv run -- python -m pytest -m "docker_integration" --tb=short -v

uv run -- python -m pytest -m "live_postgresql" --tb=short -v
```

The first command runs all Docker integration tests, and the services must be running. The second runs a specific platform.

## Apple Container Without Docker Desktop

On Apple-silicon macOS without Docker Desktop, the same stacks run through
`mocker` (a Docker-compatible CLI over the Apple `container` runtime).
`CONTAINER_ENGINE=mocker` swaps only the compose driver; the compose files
stay unmodified. This is local-dev only and must not run in CI. The steps
below mirror the `postgres-integration` CI job:

```bash
CONTAINER_ENGINE=mocker make test-docker-up-postgresql

set -o pipefail
uv run -- python -m pytest tests/integration/platforms/test_postgresql_live.py \
  -m "live_postgresql" --tb=short -v -p no:cacheprovider \
  | tee /tmp/pg_live_output.txt
if ! grep -Eq '[0-9]+ passed' /tmp/pg_live_output.txt; then
  echo "PostgreSQL live integration tests produced no passing tests (all skipped or none collected); refusing to report a silent pass."
  exit 1
fi

PGHOST=localhost PGPORT=5432 PGUSER=benchbox PGPASSWORD=benchbox \
  PGDATABASE=benchbox_test uv run -- python -m pytest \
  tests/integration/platforms/test_tpchavoc_postgres_equivalence.py \
  -m "live_postgresql" -n 0 --tb=short -v -p no:cacheprovider \
  | tee /tmp/pg_tpchavoc_output.txt
if ! grep -Eq '[0-9]+ passed' /tmp/pg_tpchavoc_output.txt; then
  echo "TPC-Havoc PostgreSQL variant-equivalence sample produced no passing tests (all skipped or none collected); refusing to report a silent pass."
  exit 1
fi

CONTAINER_ENGINE=mocker make test-docker-down-postgresql
```

The steps are:

1. Start PostgreSQL through the sanctioned pipeline.
2. Run the CI live-integration step verbatim, including its anti-skip guard. Without `pipefail`, `tee`, and the `grep`, an all-skipped run (service unreachable after `up --wait`) exits 0 and silently renders as green.
3. Run the CI TPC-Havoc equivalence sample verbatim, with the same guard.
4. Stop the service when done.

Each step must report at least one passing test, as in CI, so an all-skipped
run cannot pass silently.

## Pytest Markers

| Marker | Description |
|--------|-------------|
| `docker_integration` | All Docker-based integration tests |
| `live_integration` | All live tests (Docker + cloud) |
| `live_clickhouse` | ClickHouse-specific tests |
| `live_trino` | Trino-specific tests |
| `live_presto` | Presto-specific tests |
| `live_postgresql` | PostgreSQL-specific tests |
| `live_starrocks` | StarRocks-specific tests |
| `live_doris` | Doris-specific tests |
| `live_databend` | Databend-specific tests |
| `live_influxdb` | InfluxDB-specific tests |

## Graceful Skip Behavior

Tests skip automatically when their Docker service isn't running:

```
SKIPPED [1] test_clickhouse_live.py:44: Skipping ClickHouse Docker test: service not reachable at localhost:9000
```

This means:
- `make test-fast` is never affected by Docker availability
- CI pipelines can selectively enable Docker platforms
- Developers only need Docker for the platforms they're working on

## Platform Notes

### Host Port Overrides

The Docker test compose files use unique default host ports so `make test-docker-up-all`
can start every platform together. If you override those defaults and reintroduce a port
collision, run the affected platforms sequentially.

### Doris Security Defaults

The checked-in Doris stack defaults to `DORIS_PRIVILEGED=false`. Only opt into
`DORIS_PRIVILEGED=true` when your Docker VM still needs the container to raise
`vm.max_map_count` at startup; configuring the Docker VM once is the narrower
alternative.

### Stack Isolation

BenchBox Docker test targets use isolated Docker Compose project names, so running
`make test-docker-<platform>` does not tear down an unrelated stack you already started
with the same compose file. The tracked `make test-docker-up-<platform>` and
`make test-docker-down-<platform>` targets still pair together for interactive debugging.

### Default Credentials

All Docker services use test-only credentials defined in their compose files:

| Platform | User | Password | Database |
|----------|------|----------|----------|
| ClickHouse | `default` | (empty) | `default` |
| Trino | `benchbox` | (none) | (none) |
| Presto | `benchbox` | (none) | (none) |
| PostgreSQL | `benchbox` | `benchbox` | `benchbox_test` |
| StarRocks | `root` | (empty) | (none) |
| Doris | `root` | (empty) | (none) |
| Databend | `benchbox` | `benchbox` | `default` |
| InfluxDB | (auth disabled) | (none) | `benchbox_test` |

### Startup Times

Most services start in under 10 seconds. StarRocks and Doris can take 30-60 seconds. The `docker compose up --wait` flag (used by Makefile targets) blocks until the healthcheck passes.

## Comparison: Test Tiers

| Tier | What It Tests | When It Runs | Cost |
|------|--------------|-------------|------|
| **Stub smoke tests** | Adapter wiring, SQL patterns, config validation | Always (CI, local) | Free |
| **Docker integration** | Real queries, data loading, schema CRUD | On demand (`make test-docker-*`) | Free |
| **Cloud live tests** | Full cloud workflow with real credentials | On demand (`make test-live-*`) | $0.05-0.20/run |

## Troubleshooting

### Service won't start

```bash
docker info

lsof -i :9000

docker compose -f docker/clickhouse/docker-compose.yml logs
```

The commands check that the Docker daemon is running, check for port conflicts (9000 is the ClickHouse port here), and view the service logs.

### Tests skip even though Docker is running

The `skip_unless_docker_service()` helper probes `localhost:<port>` with a 2-second timeout. If the service is still starting, wait for the healthcheck:

```bash
docker compose -f docker/clickhouse/docker-compose.yml up --wait
```

### Clean up stale containers

```bash
make test-docker-down-all
docker compose -f docker/clickhouse/docker-compose.yml down -v
```

The first command stops all Docker services. The second does the same manually for one service.
