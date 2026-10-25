<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# GizmoSQL Platform

```{tags} intermediate, guide, gizmosql, sql-platform
```

> **CLI name:** `gizmosql` - use `benchbox run --platform gizmosql`
>
> **Support status:** `experimental`. SQL mode only.

GizmoSQL is a server that exposes DuckDB over Arrow Flight SQL. BenchBox connects to a running
GizmoSQL **Core** server with the official ADBC driver, creates the benchmark tables with DuckDB-dialect
DDL, loads data from the client as bounded Arrow batches, and runs the DuckDB-dialect queries.

BenchBox does not start or manage a GizmoSQL server for you. You provision the endpoint, choose its
credentials and TLS setup, and keep it running. Only the test fixtures in this repository start a local
server automatically.

## Supported scope

| Area | Supported |
| --- | --- |
| Edition | GizmoSQL Core (Enterprise servers connect, but only the Core feature set is validated) |
| Release channel | `stable` releases, 1.37 or newer. The live test lane pins 1.39.0. |
| Backend | DuckDB only. A SQLite-backend server is rejected at connection time. |
| Authentication | Username and password, or a bearer token |
| Loading | Client-side ADBC ingest of `.tbl`, `.dat`, CSV (plain, gzip, Zstandard) and Parquet files |
| Benchmarks | TPC-H and TPC-DS through the DuckDB dialect |

Not supported: the SQLite backend, Enterprise SSO/OIDC and licensed controls, automatic production server
lifecycle, a shared-filesystem server-side loader, and certification of the `lts` release channel or Docker
images. Those may be started by you, but BenchBox has no validated lane for them.

The 1.37 floor exists because client-side loading relies on the server's vectorized Arrow ingest path.

## Installation

```bash
uv add benchbox --extra gizmosql
```

The extra installs `adbc-driver-gizmosql>=2.0.13,<3`. The vendor's combined `gizmosql[adbc]` extra is not
used because it conflicts with BenchBox's PyArrow `<25` cap. The driver wheels are published for the
operating systems and glibc baselines listed on the
[adbc-driver-gizmosql PyPI page](https://pypi.org/project/adbc-driver-gizmosql/); install from a platform
that has a matching wheel.

Check the installation:

```bash
benchbox check-deps --platform gizmosql
benchbox platforms status gizmosql
```

## Starting a server

You run the server. With the vendor's `gizmosql_server` binary, a local development server looks like this:

```bash
GIZMOSQL_PASSWORD=change-me gizmosql_server --hostname 127.0.0.1 --port 31337 --username gizmosql \
  --database-filename ./gizmosql.duckdb
```

Other useful flags are `--tls`, `--readonly` and `--backend duckdb` (the only supported backend). Without
`--database-filename` the server is an in-memory database that loses its data on restart. The Python
`gizmosql` package (`uv pip install gizmosql==1.39.0`) downloads the same binary and can launch it for you.

The vendor also publishes Docker images and an `lts` channel. They are mentioned here only as unvalidated
options.

## Connecting

```bash
export GIZMOSQL_USERNAME=gizmosql
export GIZMOSQL_PASSWORD=change-me

benchbox run --platform gizmosql --benchmark tpch --scale 0.01 --non-interactive \
  --platform-option host=gizmo.example.com \
  --platform-option port=31337
```

Credentials come from `--platform-option`, the saved platform credentials, or the `GIZMOSQL_USERNAME`,
`GIZMOSQL_PASSWORD` and `GIZMOSQL_AUTH_TOKEN` environment variables. Prefer the environment variables so the
secret does not appear in shell history.

| Option | Default | Meaning |
| --- | --- | --- |
| `host`, `port` | `localhost`, `31337` | Flight SQL endpoint |
| `username`, `password` | environment | Password authentication |
| `auth_token` | environment | Bearer token, used instead of a password |
| `catalog` | server default | An already-attached DuckDB catalog |
| `schema` | `benchbox_<benchmark>_sf<scale>_<tuning>` | Schema holding the benchmark tables |
| `tls` | `true` | Connect with TLS |
| `tls_ca_cert` | none | PEM CA bundle that signs the server certificate |
| `tls_skip_verify` | `false` | Skip certificate verification (development only) |
| `allow_insecure_connection` | `false` | Required to use `tls=false` or `tls_skip_verify=true` |
| `autocommit` | `true` | ADBC session transaction mode |
| `query_timeout` | `0` | Seconds before a query is cancelled on the server (0 disables) |
| `ingest_batch_bytes` | `8388608` | Target size of each Arrow ingest batch |
| `session_settings` | none | Session-scoped DuckDB settings, `name=value` pairs separated by commas |

`platforms status` and the result files redact passwords, tokens, usernames and credential-bearing URIs.

### TLS

Connections verify TLS by default. A server started without TLS, or with a self-signed certificate, needs an
explicit development opt-in, and BenchBox logs a warning each time it connects that way:

```bash
benchbox run --platform gizmosql --benchmark tpch --non-interactive \
  --platform-option tls=false --platform-option allow_insecure_connection=true
```

Prefer `tls_ca_cert=/path/to/ca.pem` over `tls_skip_verify` for private certificate authorities.

## Shared servers and persistent state

A GizmoSQL server is shared state. BenchBox keeps its footprint small:

- Tables go into one schema per benchmark, scale factor and tuning configuration, so tuned and notuning runs never share tables (override with `schema`). BenchBox
  creates that schema if it does not exist, and never drops schemas or databases.
- If every benchmark table already exists, holds rows, and matches the row counts of the source files, BenchBox
  reuses them and skips schema creation and loading. A non-empty table whose count differs is reported as not
  reusable (on a read-only server it is an error), so the load runs and fails on it. If any table is missing or empty, BenchBox creates what is missing (`CREATE TABLE IF NOT EXISTS`)
  and loads the empty tables. A populated table is reused only when its row count equals the row count of the
  source files; otherwise the load fails with an error naming the table and both counts. BenchBox never deletes
  a partial or stale table: use `--force-recreate` or a fresh schema. Running the load twice cannot duplicate rows.
- Each table loads in a single transaction on its own session. A failed load rolls back, names the table, and
  leaves the table as it was.
- `--force-recreate` drops and recreates only the benchmark tables in the selected schema.
- BenchBox never changes server-wide DuckDB settings. `session_settings` accepts only session-scoped settings
  and rejects global ones such as `threads` or `memory_limit` before sending anything. Session settings are
  replayed on every session BenchBox opens, including throughput streams, and the effective values are recorded
  in the result metadata. They end with the session.

### Read-only servers

A server started with `--readonly` supports query-only runs against preloaded data. Set `schema` to where the
tables live (for example `main`). Schema creation, loading, statistics gathering and write benchmarks fail
before anything is sent to the server, with an error that says the server is read-only. Server-wide `SET`/`RESET`, `PRAGMA` assignments, `CALL`, `INSTALL`
and `LOAD` are refused client-side too.

BenchBox compares each preloaded table's row count with the locally generated source files. If the source files are
not available, it refuses the run; set `allow_unverified_reuse=true` to query the preloaded tables as they are. Tables
kept at load time are recorded as reused with zero load time.

## Timing

Reported times are client-observed: they include Flight SQL and gRPC overhead and result transfer. Do not
compare them directly with embedded DuckDB engine-only timings. Results record
`timing_basis: client_observed_flight_sql`.

## Result types

Arrow Flight SQL has no 128-bit integer, so DuckDB `HUGEINT` results (for example `SUM` over integers) arrive as
`DECIMAL(38,0)` and are returned as `Decimal` values. The values are identical to embedded DuckDB, but the Python
type differs, so compare result digests after converting integral `Decimal` values to `int`.

## Loading behavior

BenchBox reads the table schema from the server and parses each file with that schema, honoring the benchmark's
CSV dialect (delimiter, header, quote character, NULL marker, trailing delimiters and TPC-DS trailing NULL
fields). Gzip and Zstandard files are decompressed while streaming. A row that cannot be parsed fails the load
instead of being skipped.

The GizmoSQL Flight endpoint rejects messages above 16 MiB. BenchBox slices every batch to
`ingest_batch_bytes` (default 8 MiB, maximum 15 MiB), so wide rows stay below the limit.

## Transactions, timeouts and cancellation

Sessions use ADBC autocommit by default, and benchmarks that issue `BEGIN`/`COMMIT` statements work as they do
on DuckDB. With `autocommit=false`, BenchBox commits after schema creation and the ADBC session owns the
transaction, so do not combine it with benchmarks that send their own `BEGIN`.

`query_timeout` cancels the statement on the server and reports the query as failed with a timeout error; the
session stays usable and the server stops the work.

## Query plans

Plan capture runs `EXPLAIN (FORMAT JSON)`, which does not execute the query, and parses it with the DuckDB plan
parser. `analyze_plans` switches to `EXPLAIN (ANALYZE, FORMAT JSON)`, which re-executes each query and is
therefore opt-in.

## Throughput tests

Every throughput stream opens its own ADBC session with the same schema and session settings, so cursors,
transactions and session state are not shared between streams.

## Testing

Unit tests use a mocked driver and need no server. The live lane starts a real GizmoSQL Core server through the
managed Python API, loads BenchBox-generated TPC-H and TPC-DS SF 0.01 data and compares results with embedded
DuckDB:

```bash
make test-live-gizmosql
```

The first run downloads the pinned `gizmosql_server` release from GitHub releases into
`~/.cache/gizmosql`. To work offline, point `GIZMOSQL_SERVER_BINARY` at an existing binary of the pinned
version. The make target fails, rather than skipping, when the binary cannot be provisioned; a bare
`pytest -m live_gizmosql` skips with the reason.

For a full 99-query TPC-DS check, run the benchmark against a local server and compare with DuckDB:

```bash
benchbox run --platform gizmosql --benchmark tpcds --scale 0.01 --non-interactive \
  --platform-option tls=false --platform-option allow_insecure_connection=true
```

## Dialect

GizmoSQL runs the DuckDB dialect (`PLATFORM_DIALECTS["gizmosql"] == "duckdb"`). It registers no DDL rewrite
rules, so there is no GizmoSQL entry in the SQL compatibility DDL registry.
