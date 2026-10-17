# UAT Local Platform Provisioning

This runbook is the operator reference for local platforms that require a
server, container, or Spark Connect endpoint before a UAT sweep can reach them.
The index is `docs/operations/local-platform-provisioning.tsv`.

Default UAT behavior is unchanged: unreachable platforms become
`skipped_unreachable` during execute. To fail early, opt in with:

```yaml
preflight:
  local_platforms_check: true
```

For automated platforms, preflight makes one explicit `make uat-bring-up
PLATFORM=<name>` attempt before aborting. Document-only platforms must be
started by the operator.

## Container engine

`resolve_container_cli()` picks the compose-compatible binary: `BENCHBOX_CONTAINER_CLI`
env override (verbatim) > macOS: `mocker` if on `PATH`, else `docker` > every
other platform: always `docker`. A missing resolved binary is a hard error.
Full contract and mocker teardown interaction: `docs/operations/uat-framework.md`
"Container engine resolution".

## Automated Docker-backed platforms

These platforms have UAT-owned compose metadata, healthchecks, and a TCP probe.
They can be started explicitly:

```bash
make uat-bring-up PLATFORM=<platform>
```

Before a timed sweep, pre-fetch a slow stack's images/build ahead of time so a
first-run download doesn't eat into `cleanup.docker_start_timeout_s`:

```bash
make uat-prepull PLATFORM=<platform>
```

This runs `compose pull --ignore-buildable` and then `compose build`.

- `cedardb` — `localhost:5435`, compose file `docker/cedardb/docker-compose.yml`.
- `clickhouse-server` — `localhost:9000`, compose file `docker/clickhouse/docker-compose.yml`; local password is `benchbox`.
- `databend` — `localhost:8000`, compose file `docker/databend/docker-compose.yml`.
- `doris` — `localhost:19031`, compose file `docker/doris/docker-compose.yml`; keep the image-provided JDK path intact.
- `influxdb` — `localhost:8181`, compose file `docker/influxdb/docker-compose.yml`.
- `lakesail` — Spark Connect endpoint `sc://localhost:50051`, compose file `docker/lakesail/docker-compose.yml`; UAT starts only `lakesail-connect` and builds the image from the public PySail package.
- `pg-duckdb` — `localhost:5432`, compose file `docker/postgres-extensions/docker-compose.pg-duckdb.yaml`; mutually exclusive with other PostgreSQL-family stacks on the default port.
- `pg-mooncake` — `localhost:5432`, compose file `docker/postgres-extensions/docker-compose.pg-mooncake.yaml`; mutually exclusive with other PostgreSQL-family stacks on the default port.
- `postgresql` — `localhost:5432`, compose file `docker/postgresql/docker-compose.yml`.
- `presto` — `localhost:18081`, compose file `docker/presto/docker-compose.yml`.
- `questdb` — `localhost:8812`, compose file `docker/questdb/docker-compose.yml`; BenchBox uses HTTP port `19000` for load paths.
- `singlestore` — `localhost:13306`, compose file `docker/singlestore/docker-compose.yml`; local password is `benchbox`.
- `starrocks` — `localhost:19030`, compose file `docker/starrocks/docker-compose.yml`; BenchBox passes FE/HTTP options during execution.
- `timescaledb` — `localhost:5432`, compose file `docker/postgres-extensions/docker-compose.timescaledb.yaml`; mutually exclusive with other PostgreSQL-family stacks on the default port.
- `trino` — `localhost:18080`, compose file `docker/trino/docker-compose.yml`; catalog configuration lives in the compose tree.
- `velox` — Spark Connect endpoint `sc://localhost:50051`, compose file `docker/velox/docker-compose.yml`; UAT starts only `velox-connect`.

## Document-only platforms

None currently. If a future local platform cannot be made project-scoped or
requires native tooling outside Docker, list it here with explicit operator
startup instructions.

## Troubleshooting

- `make uat-bring-up PLATFORM=does-not-exist` returns a non-zero exit and an
  `unknown platform` message.
- `docker ps` must work before any automated bring-up can succeed.
- If a platform remains unreachable after bring-up, inspect the platform's
  compose logs and verify the endpoint in `local-platform-provisioning.tsv`.

## First-run checklist

Setup to complete before the first UAT sweep on a new machine. Each item
names the failure symptom if skipped. This list has been checked on macOS
only.

1. **`uv sync` + per-platform extras** (`tests/uat/matrix.py`
   `PLATFORM_UV_EXTRA` is the source of truth):
   `clickhouse-local`, `clickhouse-server`, `lakesail`, `databend`,
   `influxdb` and `singlestore` each need an extra. Cells invoke
   `uv run --extra <X> --` per platform, so nothing needs pre-installing
   beyond a plain `uv sync` for everything else. Skip this → the first cell
   for that platform records `ModuleNotFoundError` as FAILED, silently
   breaching the validator clean-rate floor.
2. **Container engine** — see "Container engine" above. Skip this →
   `resolve_container_cli()` raises before any compose command runs.
3. **Docker/VM memory ≥ 12 GiB.** Docker Desktop: Settings → Resources →
   Memory. Skip this → velox's Spark/Velox container can fail an SF=1 TPC-H
   pass under the default ~11.7 GB ceiling, especially with other stacks
   running in the same sweep. Apple container/mocker sizes each container's
   VM independently — confirm the host has equivalent headroom free rather
   than raising a shared ceiling.
4. **Doris `vm.max_map_count` ≥ 2000000** — see `docs/platforms/doris.md`.
   Skip this → Doris fails its startup preflight check inside the container
   unless `DORIS_PRIVILEGED=true` is also set.
5. **`make uat-prepull PLATFORM=<name>`** for slow/first-run stacks
   (LakeSail, Velox, Doris) ahead of a timed sweep. Skip this → a multi-GB
   image pull or PySail build silently eats
   `cleanup.docker_start_timeout_s` and the stack records
   `skipped-unreachable` instead of a clear pull/build failure.
6. **First-run costs.** Scale-1.0 TPC-H/TPC-DS datagen is CPU/disk-bound, not
   instant; LakeSail's first build additionally downloads a multi-GB PySail
   package tarball over the network (covered by step 5's prepull). Budget the
   first sweep's wall-clock time accordingly.
