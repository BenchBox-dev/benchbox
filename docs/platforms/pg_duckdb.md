<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# pg_duckdb Platform

```{tags} intermediate, guide, pg-duckdb, sql-platform, postgresql-extension
```

> **CLI name:** `pg-duckdb` - use `benchbox run --platform pg-duckdb`

pg_duckdb is a PostgreSQL extension that embeds DuckDB's columnar-vectorized analytics engine inside PostgreSQL, accelerating OLAP queries without requiring SQL changes or data migration. BenchBox provides first-class pg_duckdb support for benchmarking DuckDB-accelerated PostgreSQL workloads.

## Features

- **DuckDB vectorized execution** - Analytical queries routed through DuckDB's high-performance engine
- **No SQL changes needed** - Existing PostgreSQL queries work transparently
- **Configurable execution** - Force DuckDB execution or let PostgreSQL decide per query
- **Parallel table scanning** - Configurable thread count for PostgreSQL heap table scans
- **PostgreSQL compatible** - Uses PostgreSQL COPY for efficient bulk loading
- **MotherDuck integration** - Optional cloud offload for hybrid local+cloud queries
- **Two deployment modes** - Self-hosted and MotherDuck

## Deployment Modes

pg_duckdb supports two deployment modes, selectable via the colon syntax:

```bash
benchbox run --platform pg-duckdb --benchmark tpch --scale 1.0

benchbox run --platform pg-duckdb:motherduck --benchmark tpch --scale 1.0
```

The first command uses self-hosted mode, the default: PostgreSQL with the pg_duckdb extension. The second uses MotherDuck mode for hybrid local and cloud queries.

### Self-Hosted Mode (Default)

Connect to a PostgreSQL server with pg_duckdb extension installed:
- pg_duckdb 1.0+ extension must be installed on the server
- Full database management (CREATE/DROP database) available
- DuckDB execution engine handles analytical queries on heap tables

### MotherDuck Mode

Connect pg_duckdb to MotherDuck for hybrid queries:
- Requires MOTHERDUCK_TOKEN for authentication
- Enables queries that join local PostgreSQL tables with MotherDuck cloud data
- Sign up at [motherduck.com](https://app.motherduck.com/token)

**MotherDuck Configuration:**

```bash
export MOTHERDUCK_TOKEN=your-token-here

benchbox run --platform pg-duckdb:motherduck --benchmark tpch --scale 1.0
```

Set the MotherDuck token, then run the benchmark in MotherDuck hybrid mode.

## Data Lake Support Status

Parquet loads work through the inherited PostgreSQL path: the Parquet file
is streamed client-side via pyarrow into `COPY`, exactly like `postgresql`.
The embedded DuckDB engine is not used for file reads.
Native loads stay tbl-first like `postgresql`.

BenchBox has no Delta Lake or Iceberg read path for pg_duckdb. An explicit
`--format delta` request fails immediately with an error that names the
platform.

Cloud reads need credentials at query time, never in code or committed
config. MotherDuck mode remains the supported cloud variant (token via the
`MOTHERDUCK_TOKEN` environment variable). Direct S3 Parquet reads
additionally need object-storage credentials and Parquet file management,
and are not verified as supported.

## Installation

### Python Dependencies

pg_duckdb uses the same Python driver as PostgreSQL (`psycopg` 3):

```bash
uv add benchbox --extra postgresql
```

### Server Requirements

pg_duckdb must be installed on the PostgreSQL server. See the
[pg_duckdb GitHub repository](https://github.com/duckdb/pg_duckdb) for installation instructions.

**Docker (recommended for testing):**

```bash
docker run -d --name pg-duckdb \
  -e POSTGRES_PASSWORD=benchbox \
  -p 5432:5432 \
  pgduckdb/pgduckdb:18-v1.1.1

psql -h localhost -U postgres -c "CREATE EXTENSION pg_duckdb;"
```

The `psql` command creates the extension, which also verifies it.

## Quick Start

```bash
benchbox run --platform pg-duckdb --benchmark tpch --scale 0.01

benchbox run --platform pg-duckdb --benchmark tpch --scale 1.0 \
  --platform-option host=pgduckdb.example.com \
  --platform-option password=secret

benchbox run --platform pg-duckdb --benchmark tpch --scale 1.0 \
  --execution-engine postgres

benchbox run --platform pg-duckdb --benchmark tpch --scale 1.0 \
  --platform-option postgres_scan_threads=8
```

The four commands run, in order: a basic TPC-H benchmark, a benchmark with a
custom connection, a benchmark using PostgreSQL's normal query routing, and a
benchmark that configures parallel scan threads with `postgres_scan_threads`.

## Configuration Options

| Option | Default | Description |
|--------|---------|-------------|
| `host` | `localhost` | PostgreSQL server hostname |
| `port` | `5432` | PostgreSQL server port |
| `database` | auto-generated | Database name |
| `username` | `postgres` | PostgreSQL username |
| `password` | (none) | PostgreSQL password |
| `schema` | `public` | PostgreSQL schema that holds the benchmark tables |
| `execution_engine` | `default` (`duckdb`) | Choose `duckdb` or PostgreSQL's normal query routing with `postgres` |
| `force_execution` | - | Deprecated alias: `true` selects `duckdb`; `false` selects `postgres` |
| `postgres_scan_threads` | `0` (auto) | Threads for parallel PostgreSQL table scanning |

## Tuning files

Run the native TPC-H profile with:

```bash
benchbox run --platform pg-duckdb --benchmark tpch --scale 0.01 \
  --phases generate,load,power --seed 42 \
  --tuning examples/tunings/pg-duckdb/tpch_tuned.yaml --non-interactive
```

BenchBox tables on pg_duckdb are PostgreSQL heap tables, so the PostgreSQL
layout method applies. A partitioning column renders `PARTITION BY` at
`CREATE TABLE` time (RANGE for dates, HASH for integers, LIST otherwise).
RANGE tunings get yearly child partitions over the benchmark date domain plus
a DEFAULT partition; HASH tunings get fixed hash partitions without one.
Partitioning is skipped, and recorded as not applied, while primary or unique
keys are requested, because PostgreSQL requires those keys to include the
partition column; the example therefore disables them.

After load, clustering and sorting columns merge into one index ordered by
tuning order across both lists, and `CLUSTER` rewrites the table in index
order. For partitioned tables the leaf partitions carry the clustered indexes.
`CLUSTER` cannot run inside a transaction block, so its index commits first
and `CLUSTER` runs in autocommit. The index columns are verified from
`pg_index` before that commit, and `indisclustered` is verified for every
partition afterwards; partitioning strategy and columns are verified from
`pg_get_partkeydef`. A pre-commit mismatch is recorded as a failed statement.
A post-commit verification failure records the durable statements as executed
but the intent as not satisfied, with the verification error as the reason.
This is a heap-layout method only; it does not sort data inside DuckDB or
Parquet files.

## How It Works

pg_duckdb accelerates queries by routing them through DuckDB's vectorized execution engine:

1. **Schema creation** - Standard PostgreSQL DDL (CREATE TABLE) is used
2. **Data loading** - PostgreSQL COPY loads data into standard heap tables
3. **Query execution** - `execution_engine=default` resolves to `duckdb` and forces DuckDB execution. `postgres` sets `duckdb.force_execution=false`, allowing PostgreSQL's normal routing.
4. **Results** - Results are returned through the standard PostgreSQL wire protocol
5. **Engine receipt** - Result bundles record the requested and applied engine in `platform.execution_engine`.

### Performance Characteristics

- **Up to ~4x faster** than PostgreSQL with full indexes on TPC-H
- **Up to 1500x faster** on specific TPC-DS queries vs unindexed PostgreSQL
- **Row-oriented storage** - Data remains in PostgreSQL heap format (no columnar compression)
- **Best for** analytical scans, aggregations, and joins
- **Less benefit for** point lookups and index-dependent OLTP queries

## Comparison with Other Platforms

| Aspect | pg_duckdb | Native DuckDB | PostgreSQL |
|--------|-----------|---------------|------------|
| Storage | PostgreSQL heap | DuckDB columnar | PostgreSQL heap |
| Execution | DuckDB vectorized | DuckDB vectorized | PostgreSQL row-based |
| SQL dialect | PostgreSQL | DuckDB | PostgreSQL |
| Data loading | COPY (psycopg 3) | Direct file read | COPY (psycopg 3) |
| Compression | No (heap tables) | Yes (columnar) | No (heap tables) |
| Best for | Adding analytics to existing PG | Standalone analytics | Transactional + basic analytics |

## Known Limitations

- **Extension conflicts** - pg_duckdb cannot coexist with pg_mooncake (shared libduckdb.so). Use separate PostgreSQL instances for each.
- **Row-oriented storage** - Queries operate on PostgreSQL's row-oriented heap tables, missing DuckDB's columnar compression benefits.
- **Server installation required** - pg_duckdb must be pre-installed on the PostgreSQL server; BenchBox cannot install it remotely.
- **GUC permissions** - Thread count tuning may require superuser or pg_duckdb role privileges.
- **No data-lake read paths** - No Delta Lake/Iceberg support and no verified S3 reads; see Data Lake Support Status above.

## Recommended Benchmarks

- **TPC-H** - Standard OLAP benchmark; shows pg_duckdb's analytical acceleration
- **TPC-DS** - Complex OLAP benchmark; demonstrates vectorized execution benefits
- **ClickBench** - Real-world analytical queries; good for comparison with columnar stores
