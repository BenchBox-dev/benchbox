<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# pg_mooncake Platform

```{tags} intermediate, guide, pg-mooncake, sql-platform, postgresql-extension, columnstore
```

> **CLI name:** `pg-mooncake` - use `benchbox run --platform pg-mooncake`

pg_mooncake is a PostgreSQL extension that adds native columnstore tables with DuckDB-powered vectorized execution. Data is stored in Parquet format with Iceberg metadata, providing 5-20x columnar compression and top-10 ClickBench performance. BenchBox provides first-class pg_mooncake support for benchmarking columnstore PostgreSQL workloads.

## Features

- **Columnstore mirrors** - BenchBox heap-loads with COPY, then promotes loaded tables with `mooncake.create_table`
- **DuckDB execution** - Vectorized query engine for analytical workloads
- **Columnar compression** - 5-20x compression ratios vs PostgreSQL heap tables
- **Object storage** - Optional S3/GCS backend for Parquet data
- **PostgreSQL compatible** - Uses standard PostgreSQL wire protocol and COPY loading before columnstore promotion
- **Top-10 ClickBench** - Competitive with dedicated OLAP engines on real-world analytical queries

## Quick Start

The first command runs a basic TPC-H benchmark with columnstore mirrors. The second uses a custom connection, and the third uses an S3 storage backend:

```bash
benchbox run --platform pg-mooncake --benchmark tpch --scale 0.01

benchbox run --platform pg-mooncake --benchmark tpch --scale 1.0 \
  --platform-option host=mooncake.example.com \
  --platform-option password=secret

benchbox run --platform pg-mooncake --benchmark tpch --scale 1.0 \
  --platform-option storage_mode=s3 \
  --platform-option mooncake_bucket=s3://my-bucket/mooncake-data
```

## Installation

### Python Dependencies

pg_mooncake uses the same Python driver as PostgreSQL (`psycopg` 3):

```bash
uv add benchbox --extra postgresql
```

### Server Requirements

pg_mooncake must be installed on the PostgreSQL server. The recommended approach is Docker.

**Docker (recommended):**

```bash
docker run -d --name pg-mooncake \
  -e POSTGRES_PASSWORD=benchbox \
  -p 5432:5432 \
  mooncakelabs/pg_mooncake:latest

psql -h localhost -U postgres -c "CREATE EXTENSION pg_mooncake;"
```

The `psql` command verifies that the extension can be created.

See the [pg_mooncake GitHub repository](https://github.com/Mooncake-Labs/pg_mooncake) for additional installation methods.

## How It Works

pg_mooncake transforms PostgreSQL into a hybrid analytical database:

1. **Schema creation** - BenchBox creates PostgreSQL heap tables so COPY remains supported
2. **Data loading** - PostgreSQL COPY loads benchmark files into the heap tables
3. **Promotion** - BenchBox renames the loaded heap tables and exposes mooncake mirrors with the original benchmark table names
4. **Query execution** - Queries on the mooncake mirrors are routed through DuckDB's vectorized engine
5. **Results** - Results are returned through the standard PostgreSQL wire protocol

### Storage Architecture

```
PostgreSQL Instance
├── pg_mooncake extension
│   ├── Columnstore Access Method (USING mooncake)
│   ├── pg_moonlink (WAL-based ingestion)
│   └── DuckDB execution engine
└── Storage Backend
    ├── Local disk (default) → Parquet files
    └── Object storage (S3/GCS) → Parquet files with Iceberg metadata
```

## Configuration Options

| Option | Default | Description |
|--------|---------|-------------|
| `host` | `localhost` | PostgreSQL server hostname |
| `port` | `5432` | PostgreSQL server port |
| `database` | auto-generated | Database name |
| `username` | `postgres` | PostgreSQL username |
| `password` | (none) | PostgreSQL password |
| `schema` | `public` | PostgreSQL schema name |
| `storage_mode` | `local` | Storage backend: `local` (disk) or `s3` (object storage) |
| `mooncake_bucket` | (none) | S3/GCS bucket URL (required when `storage_mode=s3`) |

### S3 Storage Configuration

Set the S3 bucket through an environment variable, or pass it as a platform option:

```bash
export MOONCAKE_S3_BUCKET=s3://my-bucket/mooncake-data

benchbox run --platform pg-mooncake --benchmark tpch \
  --platform-option storage_mode=s3 \
  --platform-option mooncake_bucket=s3://my-bucket/data
```

S3 mode requires standard AWS credentials (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`).

## Comparison with Other Platforms

| Aspect | pg_mooncake | pg_duckdb | Native DuckDB | PostgreSQL |
|--------|-------------|-----------|---------------|------------|
| Storage | Columnstore (Parquet) | PostgreSQL heap | DuckDB columnar | PostgreSQL heap |
| Execution | DuckDB vectorized | DuckDB vectorized | DuckDB vectorized | PostgreSQL row-based |
| Compression | 5-20x (columnar) | No (heap) | Yes (columnar) | No (heap) |
| Load path | COPY into heap, then mooncake mirror promotion | COPY into heap | Native load | COPY into heap |
| Workload | OLAP columnstore | OLAP acceleration | Standalone analytics | General purpose |
| ClickBench rank | Top 10 | Not ranked | Top 5 | Not ranked |

## Known Limitations

- **Extension conflicts** - pg_mooncake cannot coexist with standalone pg_duckdb (shared libduckdb.so). Use separate PostgreSQL instances for each.
- **Direct COPY limitation** - pg_mooncake 0.2.0 does not support COPY directly into `USING mooncake` tables; BenchBox uses heap-load plus mirror promotion.
- **Data type support** - Not all PostgreSQL data types are supported on columnstore tables. The extension is actively evolving.
- **No constraints** - Columnstore tables don't support PRIMARY KEY, FOREIGN KEY, or other constraints.
- **Indexes on the mirror** - B-tree and other indexes are not applicable to columnstore tables. Tuning-file clustering builds an index on the source heap table only, to order the rows the mirror is copied from.
- **Server installation required** - pg_mooncake must be pre-installed on the PostgreSQL server; Docker is the recommended setup method.

## Recommended Benchmarks

- **TPC-H** - Standard OLAP benchmark; demonstrates columnstore compression and DuckDB execution benefits
- **ClickBench** - Real-world analytical queries; pg_mooncake is a top-10 performer
- **TPC-DS** - Complex OLAP; note some data types may not be fully supported yet

## Tuning files

Run the clustering profile with:

```bash
benchbox run --platform pg-mooncake --benchmark tpch --scale 0.01 \
  --phases generate,load,power --seed 42 \
  --tuning examples/tunings/pg-mooncake/tpch_tuned.yaml --non-interactive
```

The tables queried are mooncake mirrors, and `mooncake.create_table` takes no layout options. Its `table_config` accepts only moonlink write-path settings (`skip_index_merge`, `skip_data_compaction`, `append_only`, `row_identity`) plus Iceberg and WAL storage locations. `mooncake.optimize_table` runs compaction (`data`, `index` or `full`) and does not set a sort order. A mirror's row order is therefore decided by the order of the heap table it is copied from.

BenchBox uses that. Clustering and sorting columns merge into one index, ordered by tuning order, and `CLUSTER` rewrites the loaded heap table before the mirror is created. The mirror's initial copy reads the heap in physical order, so its Parquet files inherit the sort order. Probing a 50,000-row table on pg_mooncake 0.2.0 showed the effect: an unclustered source produced four files that each spanned the full key range with about 92% of adjacent rows out of order, while a source clustered on the key produced four files with narrow, mostly disjoint key ranges and at most two out-of-order rows per file.

An intent is recorded as applied only after two readbacks: the clustered index on the source heap is read from `pg_index` before `CLUSTER` commits, and the mirror's Parquet files are then read through `duckdb.query` and checked for adjacent rows out of order on the tuned columns (at most 5% in the worst file). A table with no rows is not mirrored, a warehouse the server cannot read (for example an object-storage bucket without credentials) cannot be checked, and a mirror whose files are out of order fails the check; each of those is recorded as not applied with the reason.

A partitioning request is rejected when the tuning file loads, with the reason in the error. The mirror is a single unpartitioned table, and a range-partitioned source produced mirror files that each spanned the full key range, so partitioning the source does not shape the table that queries read. Distribution is not a pg_mooncake concept.
