<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# CedarDB Platform

```{tags} intermediate, guide, cedardb, sql-platform
```

> **CLI name:** `cedardb` - use `benchbox run --platform cedardb`

CedarDB (formerly Umbra, from TU Munich) is a high-performance standalone RDBMS
supporting both OLAP and OLTP workloads over the PostgreSQL wire protocol.
BenchBox drives CedarDB through its PostgreSQL adapter, with session defaults
on tuned runs and standard `COPY` for bulk loading.

## Features

- **HTAP engine** - OLAP and OLTP on the same storage, compiled query execution
- **PostgreSQL-compatible** - standard Postgres drivers (`psycopg`), tools (`psql`, DBeaver, DataGrip)
- **Standalone** - not a Postgres extension; its own engine and storage
- **Bulk loading** - standard PostgreSQL `COPY` is fully supported
- **Tuned-run session settings** - planner and commit settings applied at connection time on tuned runs only

## Deployment Modes

| Mode          | Description                                                    |
| ------------- | -------------------------------------------------------------- |
| `self-hosted` | Self-hosted CedarDB server over PG wire protocol (default)     |

```bash
benchbox run --platform cedardb --benchmark tpch --scale 1.0
```

This uses the self-hosted mode, which is the default.

## Installation

CedarDB uses the same Python driver as PostgreSQL:

```bash
uv add benchbox --extra postgresql
```

Run a local CedarDB server (see [CedarDB docs](https://cedardb.com/docs/) for
install options). Default PG-wire port is `5432`.

## Configuration

Connection parameters mirror PostgreSQL and are passed with
`--platform-option`:

```bash
benchbox run --platform cedardb --benchmark tpch --scale 1.0 \
  --platform-option host=localhost \
  --platform-option port=5432 \
  --platform-option username=postgres \
  --platform-option database=benchbox
```

Unset options fall back to `host=localhost`, `port=5432`, `username=postgres`,
and `schema=public`. There is no dedicated `CEDARDB_*` environment-variable
surface.

## Supported Benchmarks

CedarDB inherits the PostgreSQL dialect, so it can in principle run any BenchBox
benchmark supported on PostgreSQL - TPC-H, TPC-DS, SSB, Join Order, Read
Primitives, Write Primitives, Transaction Primitives, and similar. Individual
benchmark/tuning combinations require validation; partitioning and clustering
tuning are not yet verified.

## Notes

- Data types, functions, and COPY syntax follow PostgreSQL. Tuned runs apply
  planner session settings (`enable_seqscan`, `enable_hashjoin`,
  `enable_mergejoin`, `cpu_tuple_cost`) during `configure_for_benchmark()`;
  baseline runs apply none. `random_page_cost` is not sent because CedarDB
  rejects it.
- CedarDB is positioned as an HTAP engine - transactional workloads such as
  Transaction Primitives will also run.
- For large scale factors, prefer `--compression zstd` on staged data files to
  speed up `COPY`.

## See Also

- [PostgreSQL Platform](postgresql.md) - parent dialect and driver
- [pg_duckdb](pg_duckdb.md), [pg_mooncake](pg_mooncake.md) - Postgres-compatible
  analytics alternatives
