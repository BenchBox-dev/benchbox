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
benchmark/tuning combinations require validation.

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

## Tuning files

Run the hash-partitioning profile with:

```bash
benchbox run --platform cedardb --benchmark tpch --scale 0.01 \
  --phases generate,load,power --seed 42 \
  --tuning examples/tunings/cedardb/tpch_tuned.yaml --non-interactive
```

The CedarDB reference documents one table-layout control that BenchBox can drive: `CREATE TABLE ... PARTITION BY HASH (columns)`. The reference states that CedarDB partitions such a table automatically, creating up to 1024 partitions lazily on insert, so BenchBox emits only the `PARTITION BY HASH` clause and no explicit partitions. Only `HASH` partitioning is implemented (the server answers `RANGE` with "partition strategy not implemented yet"), so a date or any other partitioning column is also hashed. Probing CedarDB v2026-09-29 showed that, unlike PostgreSQL, it does not require a primary or unique key to include the partition columns (a unique key outside the partition key is created and enforced), so partitioning and primary keys can be requested together. Foreign keys can be requested too, but a TPC-H load with them enabled failed with foreign key violations from the order in which the tables load, so the example leaves them off.

Each partitioning request is recorded as applied only after `pg_get_partkeydef` is read back and shows `HASH` on the requested columns; anything else is recorded as not applied with the observed definition.

Clustering and sorting are rejected when the tuning file loads, with the reason in the error. CedarDB v2026-09-29 answers `CLUSTER` with "CLUSTER not implemented yet", and its B-tree indexes never set `indisclustered`, so no tuning-file key can physically order rows. The table option `WITH (compression = zstd | none)` and the indexes CedarDB creates for keys are not tuning-file controls.

## See Also

- [PostgreSQL Platform](postgresql.md) - parent dialect and driver
- [pg_duckdb](pg_duckdb.md), [pg_mooncake](pg_mooncake.md) - Postgres-compatible
  analytics alternatives
