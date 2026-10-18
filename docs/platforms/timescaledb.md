<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# TimescaleDB Platform

```{tags} intermediate, guide, timescaledb, sql-platform
```

TimescaleDB is a PostgreSQL extension for time-series workloads with hypertables, native compression, and continuous aggregates. BenchBox supports both self-hosted TimescaleDB and managed TigerData cloud deployments via the same adapter.

## Features

- Automatic hypertable conversion for time-series tables
- Native compression policies for historical data
- PostgreSQL-compatible wire protocol and COPY loading
- Cloud deployment mode with SSL enforcement
- TSBS and SQL benchmark support

## Deployment Modes

Use deployment mode colon syntax with `--platform`:

```bash
benchbox run --platform timescaledb --benchmark tsbs-devops --scale 1.0

benchbox run --platform timescaledb:cloud --benchmark tpch --scale 0.01
```

The first command uses self-hosted mode (the default). The second uses TigerData managed cloud.

### Self-Hosted Mode

Use your own PostgreSQL server with TimescaleDB extension installed.

- Full database lifecycle operations are supported
- Default connection behavior is inherited from the PostgreSQL adapter

### TigerData Cloud Mode

Use TigerData managed PostgreSQL service.

- Requires SSL (`sslmode=require` by default)
- Skips DROP/CREATE database management for managed environments
- Uses `timescaledb:cloud` platform syntax
- `TIGERDATA_*` environment variables are primary; `TIMESCALE_*` are also accepted as fallback

## Cloud Connection Setup

### Option A: Service URL (recommended)

```bash
export TIGERDATA_SERVICE_URL='postgres://tsdbadmin:password@abc123.tsdb.cloud.timescale.com:5432/tsdb?sslmode=require'

benchbox run --platform timescaledb:cloud --benchmark tpch --scale 0.01 --non-interactive
```

Fallback remains supported:

```bash
export TIMESCALE_SERVICE_URL='postgres://tsdbadmin:password@abc123.tsdb.cloud.timescale.com:5432/tsdb?sslmode=require'
```

### Option B: Individual Environment Variables

```bash
export TIGERDATA_HOST='abc123.rc8ft3nbrw.tsdb.cloud.timescale.com'
export TIGERDATA_PASSWORD='your-password'
export TIGERDATA_USER='tsdbadmin'
export TIGERDATA_PORT='5432'
export TIGERDATA_DATABASE='tsdb'

benchbox run --platform timescaledb:cloud --benchmark tpch --scale 0.01 --non-interactive
```

`TIGERDATA_HOST` and `TIGERDATA_PASSWORD` are the required variables. `TIGERDATA_USER` (default `tsdbadmin`), `TIGERDATA_PORT` (default `5432`) and `TIGERDATA_DATABASE` (default `tsdb`) are optional.

Fallback (used if `TIGERDATA_*` is unset):

```bash
export TIMESCALE_HOST='abc123.rc8ft3nbrw.tsdb.cloud.timescale.com'
export TIMESCALE_PASSWORD='your-password'
export TIMESCALE_USER='tsdbadmin'
export TIMESCALE_PORT='5432'
export TIMESCALE_DATABASE='tsdb'
```

## Installation

```bash
uv add benchbox --extra postgresql
```

The `postgresql` extra provides the `psycopg` 3 driver that TimescaleDB uses. Server must have TimescaleDB 2.x extension enabled:

```sql
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;
```

## Common Commands

```bash
benchbox run --platform timescaledb:cloud --benchmark tpch --scale 0.01 --phases power --non-interactive

benchbox run --platform timescaledb --benchmark tsbs-devops --scale 1.0 --non-interactive

benchbox run --platform timescaledb --benchmark tsbs-devops --scale 1.0 \
  --platform-option chunk_interval='1 day' \
  --platform-option compression_enabled=true \
  --platform-option compression_after='7 days' \
  --non-interactive
```

The commands run, in order: a TPC-H smoke test on TigerData cloud, TSBS on self-hosted TimescaleDB, and self-hosted TSBS with compression options.

## Platform Options

| Option | Default | Description |
|--------|---------|-------------|
| `host` | `localhost` (self-hosted) | Server hostname |
| `port` | `5432` | Server port |
| `database` | auto/generated or `tsdb` (cloud) | Database name |
| `username` | `postgres` (self-hosted), `tsdbadmin` (cloud) | Login user |
| `password` | none | Login password |
| `schema` | `public` | Target schema |
| `chunk_interval` | `1 day` | Hypertable chunk interval |
| `compression_enabled` | `false` | Enable automatic compression |
| `compression_after` | `7 days` | Age threshold for compression |
| `service_url` | none | Parsed for cloud mode (`TIGERDATA_SERVICE_URL` or fallback) |

## Tuning files

Run the native TPC-H profile with:

```bash
benchbox run --platform timescaledb --benchmark tpch --scale 0.01 \
  --phases generate,load,power --seed 42 \
  --tuning examples/tunings/timescaledb/tpch_tuned.yaml \
  --platform-option chunk_interval='1 year' --non-interactive
```

Each tuned table needs exactly one partition column with a server-side `DATE`
or timestamp type. That column becomes its hypertable time dimension. The first
distribution column, when present, becomes the native space dimension; every
distribution and clustering column, in tuning order, becomes
`timescaledb.compress_segmentby` in that exact order. Sorting columns become
`timescaledb.compress_orderby` in tuning order; TimescaleDB may append the time
column after the requested keys, and only that suffix is accepted. Sorting or
segmentation requests enable compression, even when the platform's
`compression_enabled` default is false. These are compression-layout settings,
not a guarantee that all rows have already been compressed or that ordinary
query output is sorted.

The tuning file selects columns; `chunk_interval` and `compression_after`
platform options select the intervals. An explicit layout request takes
precedence over the generic compression switch. Without a table tuning entry,
the existing automatic conversion of a column named `time` is unchanged.

TimescaleDB requires unique indexes and primary keys to include every partition
dimension. The example disables primary and foreign keys rather than changing
the benchmark's logical keys. Incompatible requests are recorded as not applied
with the server error. Native requests are checked against
`timescaledb_information.dimensions` and `compression_settings`; successful
readback is recorded with the corresponding intent in the applied tuning ledger.

## SSL Requirements

TigerData cloud requires TLS. BenchBox defaults cloud mode to `sslmode=require`. `sslmode=verify-full` is not yet configured by default in BenchBox.

## Troubleshooting

```bash
psql -c "SELECT extversion FROM pg_extension WHERE extname = 'timescaledb';"

psql "$TIGERDATA_SERVICE_URL"
```

The first command verifies the TimescaleDB extension. The second checks cloud connectivity manually.

## Related

- [Platform Selection Guide](platform-selection-guide.md)
- [PostgreSQL Platform](postgresql.md)
- [Comparison Matrix](comparison-matrix.md)
