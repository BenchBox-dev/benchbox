<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Citus Platform

```{tags} intermediate, guide, citus, sql-platform, postgresql-extension, distributed
```

> **CLI name:** `citus` - use `benchbox run --platform citus`

Citus is a PostgreSQL extension that transparently distributes tables across a coordinator and worker nodes. BenchBox provides first-class Citus support for benchmarking distributed PostgreSQL workloads.

## Features

- **Extension gating** - Connection setup verifies (or creates) `citus` with an actionable install error otherwise
- **Opt-in distribution** - Pass `distribution_column` to distribute every benchmark table with `create_distributed_table` after schema creation
- **Per-table layout from tuning files** - `distribution`, `reference` and `columnar` table entries place each table individually; every placement is read back from the Citus catalogs before it is recorded as applied
- **Graceful fallback** - Tables lacking the distribution column stay coordinator-local with a warning instead of failing the run
- **Version provenance** - The installed `citus` version is recorded in platform info
- **PostgreSQL compatible** - Standard PostgreSQL wire protocol, dialect, and COPY loading

## Quick Start

```bash
benchbox run --platform citus --benchmark tpch --scale 0.01

benchbox run --platform citus --benchmark tpch --scale 1.0 \
  --platform-option host=citus.example.com \
  --platform-option password=secret \
  --platform-option distribution_column=l_orderkey
```

The first command uses coordinator-local tables, which measures single-node Postgres on a Citus server. The second runs distributed TPC-H, distributing tables on `l_orderkey`.

## Installation

### Python Dependencies

Citus uses the same Python driver as PostgreSQL (psycopg v3):

```bash
pip install "benchbox[postgresql]"
```

### Server Requirements

Citus must be installed on the PostgreSQL coordinator (and workers for real distribution). The recommended approach is Docker.

**Docker (recommended):**

```bash
docker run -d --name citus \
  -e POSTGRES_PASSWORD=benchbox \
  -p 5432:5432 \
  citusdata/citus:latest

psql -h localhost -U postgres -c "CREATE EXTENSION citus;"
```

The `psql` command creates the extension and verifies that it is available.

See the [Citus GitHub repository](https://github.com/citusdata/citus) for worker setup and additional installation methods.

## Configuration Options

| Option | Default | Description |
|--------|---------|-------------|
| `host` | `localhost` | Citus coordinator hostname |
| `port` | `5432` | Citus coordinator port |
| `database` | auto-generated | Database name |
| `username` | `postgres` | PostgreSQL username |
| `password` | (none) | PostgreSQL password |
| `schema` | `public` | PostgreSQL schema name |
| `distribution_column` | (none) | Distribute every benchmark table on this column (unset keeps coordinator-local tables) |

## Known Limitations

- **Single distribution column per table** - Citus hash-distributes on one column. A tuning file that lists several `distribution` columns for a table is recorded as not applied. Dimension tables that lack the `distribution_column` flag's column stay coordinator-local by design.
- **Coordinator-only default** - Without `distribution_column` or a tuning file, tables are coordinator-local and the run measures single-node Postgres, not distribution.
- **Planner limits on distributed tables** - Citus rejects some TPC-H shapes over distributed `lineitem`, such as the correlated subqueries in Q17, Q20 and Q22, so a distributed profile completes the power test with those queries failing. Reference tables avoid the local-to-distributed join errors that a flag-only run hits (Q4 and Q20 at scale 0.01).

## Recommended Benchmarks

- **TPC-H** - Standard OLAP benchmark; distribute on `l_orderkey` for lineitem-heavy queries
- **TPC-DS** - Complex OLAP; pick a store-sales key present across the fact tables being measured

## Tuning files

Run the distributed TPC-H profile with:

```bash
benchbox run --platform citus --benchmark tpch --scale 0.01 \
  --phases generate,load,power --seed 42 \
  --tuning examples/tunings/citus/tpch_tuned.yaml --non-interactive
```

Per-table placement keys, all under `table_tunings.<table>`:

| Key | Effect | Catalog readback |
|-----|--------|------------------|
| `distribution` | `create_distributed_table` on the first column (hash) | `pg_dist_partition`: `partmethod` `h`, partition key, colocation id |
| `reference: true` | `create_reference_table`, replicated to every node | `pg_dist_partition`: `partmethod` `n` |
| `columnar: true` | `alter_table_set_access_method(..., 'columnar')` after `CREATE EXTENSION citus_columnar` | `pg_am` access method of the table |

`reference` and `distribution` are mutually exclusive, and `reference` and `columnar` are rejected on every platform except Citus. A tuning-file entry takes precedence over `--platform-option distribution_column`, which then applies only to tables the file does not place. Tables placed in the same `distribution` column type share a colocation group, so `lineitem` and `orders` distributed on their order keys join locally on each shard.

Partitioning and clustering use the PostgreSQL layout method. A partitioned table is distributed after creation, which distributes its partitions. After load, `CLUSTER` is verified from `pg_index` on the coordinator and then on every shard through `run_command_on_shards`; a shard that is not clustered fails the table's post-load step. Two combinations are recorded as not applied with their reason because Citus does not support them: `CLUSTER` on a columnar table, and `CLUSTER` on a partitioned distributed table (Citus does not propagate it to the shards). Columnar conversion of a partitioned table is also recorded as not applied.
