<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# ParadeDB Platform

```{tags} intermediate, guide, paradedb, sql-platform, postgresql-extension
```

> **CLI name:** `paradedb` - use `benchbox run --platform paradedb`

ParadeDB is a PostgreSQL distribution for hybrid search and analytics workloads. Its `pg_search` extension adds Elasticsearch-compatible BM25 full-text search over ordinary PostgreSQL heap tables. The earlier `pg_analytics` extension is no longer shipped in ParadeDB images. BenchBox provides first-class ParadeDB support for benchmarking analytics workloads on ParadeDB-backed PostgreSQL.

## Features

- **Heap tables** - Benchmark tables stay ordinary PostgreSQL heap tables loaded through the inherited COPY path
- **Extension gating** - Connection setup verifies (or creates) `pg_search` and fails with an actionable error, naming the supported image, when it is missing
- **Version provenance** - The installed `pg_search` version is recorded in platform info
- **PostgreSQL compatible** - Standard PostgreSQL wire protocol, dialect, and COPY loading

## Quick Start

```bash
benchbox run --platform paradedb --benchmark tpch --scale 0.01

benchbox run --platform paradedb --benchmark tpch --scale 1.0 \
  --platform-option host=paradedb.example.com \
  --platform-option password=secret
```

## Installation

### Python Dependencies

ParadeDB uses the same Python driver as PostgreSQL (psycopg v3):

```bash
pip install "benchbox[postgresql]"
```

### Server Requirements

ParadeDB must be installed on the PostgreSQL server. The recommended approach is Docker.

**Docker (recommended):**

```bash
docker run -d --name paradedb \
  -e POSTGRES_PASSWORD=benchbox \
  -p 5432:5432 \
  paradedb/paradedb:0.26.2-pg18

psql -h localhost -U postgres -c "CREATE EXTENSION pg_search;"
```

The supported image is `paradedb/paradedb:0.26.2-pg18` (PostgreSQL 18.6, `pg_search` 0.26.2, `pgvector` 0.8.6; linux/amd64 and linux/arm64). Floating tags such as `latest` change the PostgreSQL major version and extension set without notice, so BenchBox results are only comparable on a pinned tag.

See the [ParadeDB GitHub repository](https://github.com/paradedb/paradedb) for additional installation methods.

## Configuration Options

| Option | Default | Description |
|--------|---------|-------------|
| `host` | `localhost` | PostgreSQL server hostname |
| `port` | `5432` | PostgreSQL server port |
| `database` | auto-generated | Database name |
| `username` | `postgres` | PostgreSQL username |
| `password` | (none) | PostgreSQL password |
| `schema` | `public` | PostgreSQL schema name |

## Recommended Benchmarks

- **TPC-H** - Standard OLAP benchmark on ParadeDB-backed heap tables
- **TPC-DS** - Complex OLAP; same heap-table path as PostgreSQL

## Tuning files

Run the heap-layout TPC-H profile with:

```bash
benchbox run --platform paradedb --benchmark tpch --scale 0.01 \
  --phases generate,load,power --seed 42 \
  --tuning examples/tunings/paradedb/tpch_tuned.yaml --non-interactive
```

ParadeDB benchmark tables are ordinary PostgreSQL heap tables, so the PostgreSQL layout method applies; `pg_search` adds BM25 indexes and plays no part in it. A partitioning column renders `PARTITION BY` at `CREATE TABLE` time (RANGE for dates and timestamps, HASH for integers, LIST otherwise). RANGE tunings get yearly child partitions plus a DEFAULT partition; HASH tunings get fixed hash partitions without one. Partitioning is skipped, and recorded as not applied, while primary or unique keys are requested, because PostgreSQL requires those keys to include the partition column; the example therefore disables them.

After load, clustering and sorting columns merge into one index ordered by tuning order across both lists, and `CLUSTER` rewrites the table in index order. For partitioned tables the leaf partitions carry the clustered indexes. `CLUSTER` cannot run inside a transaction block, so its index commits first and `CLUSTER` runs in autocommit. The index columns are verified from `pg_index` before that commit, and `indisclustered` is verified for every partition afterwards; partitioning strategy and columns are verified from `pg_get_partkeydef`. A pre-commit mismatch is recorded as a failed statement. A post-commit verification failure records the durable statements as executed but the intent as not satisfied, with the verification error as the reason.
