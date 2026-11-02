<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Platform Workaround Index

Correct-but-papering-over workarounds in the platform layer: each entry names
the symptom, the workaround, the upstream fix that would allow removal, and
how to verify the upstream fix landed. Sweep this index when an upstream
release notes a matching fix so the workaround does not linger past its need.

## chdb DataFrame fetch SIGABRT on Tuple columns

- **Symptom**: chDB's built-in `format="DataFrame"` SIGABRTs (uncatchable,
  killing the benchmark process) when the result contains a Tuple-typed
  column, for example `read_primitives.array_of_struct` returning
  `Array(Tuple(...))`. The crash also corrupts the on-disk `.chdb` directory
  (`recursive_mutex lock failed`), so later benchmarks fail at session init
  with Code 722 / Code 36 BAD_ARGUMENTS. Observed 2026-04-29 on the
  then-current lock (chdb 4.1.6 / chdb-core 26.1.0, macOS ARM local stress
  run) — the same versions still locked today, so the range includes the
  current lock until the fuller path is re-verified.
- **Workaround**: `ClickHouseLocalClient.execute()` in
  `benchbox/platforms/clickhouse/client.py` fetches `format="ArrowStream"`
  and decodes via pyarrow IPC in `_arrow_to_dataframe` (marked
  `TODO(chdb-tuple-dataframe)`). The DataFrame fetch is a thin wrapper over
  the same Arrow path, so the workaround keeps the perf benefit that replaced
  per-row CSV parsing.
- **Upstream**: [chdb](https://github.com/chdb-io/chdb) issue tracker; the
  `DataFrame` fetch path must survive Tuple-typed columns without aborting.
  Owner: the maintainer files that issue and links the new issue URL from
  the `TODO(chdb-tuple-dataframe)` marker when it exists.
- **Verify**: run the `read_primitives` array-of-struct workload against a
  `format="DataFrame"` fetch on Tuple output; removal is safe when it returns
  rows instead of aborting, with no `.chdb` corruption on repeat runs.
  Narrowing so far (2026-09-26, locked chdb 4.1.6 / chdb-core 26.1.0):
  a bare `Tuple` select, a Decimal-bearing tuple, and the exact
  `array_of_struct` ClickHouse variant shape over small tables all return
  rows via `format="DataFrame"` on macOS ARM, and the reviewer verified the
  catalog query on Linux. The April crash therefore needs the fuller
  benchmark path to reproduce; do not retire the workaround on the minimal
  repro alone.

## ClickHouse DDL via DuckDB dialect plus translate_ddl

- **Symptom**: the ClickHouse schema pipeline asks each benchmark for
  `dialect="duckdb"` and translates it with `translate_ddl` and a ClickHouse
  layout (`Nullable() NOT NULL` handling, `FLOAT[N]` to `Array(Float32)`,
  `DOUBLE[N]` to `Array(Float64)`, ENGINE/ORDER BY injection) in
  `_translate_schema_statement`, instead of consuming ClickHouse-native DDL
  directly.
- **Why not native yet**: `transaction_primitives` and `write_primitives`
  drop their inline PRIMARY KEY under `dialect="clickhouse"` (pk_capability
  rules), and that PK string is what `_extract_primary_key_columns` reads to
  derive ORDER BY. A global dialect swap would silently regress those tables
  to `ORDER BY tuple()`.
- **Decision**: keep the DuckDB-source plus rewrite path. The rewrite set is
  small, covered by schema tests, and explicit. The alternative (teaching PK
  extraction to read benchmark schema metadata, then auditing every
  benchmark's `dialect="clickhouse"` DDL for gaps currently masked by the
  rewrite) is a wider migration: it needs its own TODO with per-benchmark
  ORDER BY regression coverage before the swap. Do not attempt it as a
  drive-by inside unrelated ClickHouse work.
- **Verify**: any future swap must show identical ORDER BY clauses for the
  primitives benchmarks and unchanged schema-test output for all others.
