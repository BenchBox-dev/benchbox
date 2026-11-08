# SQL execution evidence gate

`make sql-execution-gate` is the evidence every SQLGlot runtime adoption and
every change to SQL translation must show. It executes the translated SQL of
every shipped TPC-H (22), TPC-DS (99), TPC-Havoc (220 variants) and TPC-DS OBT
(90 queries, 93 statements) query on each local engine and compares the returned
rows. It replaces the text-diff golden corpus as the primary
evidence; the corpus remains a secondary view of output text.

## Benchmarks

`--benchmarks` is a comma-separated list; the default is `tpch,tpcds,tpchavoc`.
`tpcds_obt` is not in the default: it adds about 3 minutes of CI wall time
(measured below), so it runs in the nightly `sql-execution-gate-obt` job
(`.github/workflows/nightly-v2.yml`) with the same GHCR Postgres service as
the PR job, and not in the PR tier.

| Name | Queries | Data | Tier |
|---|---|---|---|
| `tpch` | 1 to 22 | TPC-H SF0.01 | PR |
| `tpcds` | 1 to 99 | TPC-DS SF0.01, seed 1001 | PR |
| `tpchavoc` | every id of `TPCHavocBenchmark.get_queries()` (220 variants, `1_v1` onward) | the TPC-H dataset | PR |
| `tpcds_obt` | every statement of the OBT catalog (90 queries; Q14, Q23 and Q24 run as lettered statements `14a`, `14b`, ...) | the OBT built by `TPCDSOBTTransformer` from the gate's TPC-DS data (715 columns) | nightly |

Q8, Q54, Q84 and the inventory queries are recorded OBT exclusions, so `tpcds_obt`
has no cell for them. Requesting `tpcds_obt` also emits the base lettered
statements (`tpcds-parts`: 14a to 24b) and the augmented queries, and
`tpchavoc` needs no dataset of its own.

The report prints a `coverage per benchmark and engine` table (compared, errors,
vacuous unproven, expected differences, unexplained) and `--report` writes it
under `coverage`. Every Havoc variant and every OBT statement is either
executed and compared or named in an `expected_differences.json` entry.

## What it compares

For each benchmark, query and engine the gate produces the SQL through the
shipped path (`get_query(dialect=...)`, which runs `translate_sql_query` with
SQLGlot's default `WARN` unsupported level, plus the DataFusion TPC-H query
transformer), executes it on generated SF0.01 data and checks:

| Check | Fails when |
|---|---|
| `error` | The candidate SQL fails to translate, parse or execute, or exceeds the per-query budget. |
| `row-count` | The row count differs from the TPC reference row counts recorded for SF0.01 (reference parameters). |
| `reference` | The rows differ from DuckDB executing its own dialect of the same candidate query. |
| `vacuous` | The compared result carries no signal: zero rows, or every cell of every row is NULL or 0 (an aggregate over an empty set), and the reference engine agrees. Equality of two such results proves nothing about the translation. |
| `base-equivalence` | An OBT statement (`tpcds_obt`, `tpcds_obt-augmented`) returns rows that differ from the matching TPC-DS statement on the same engine: `tpcds` for a whole query, `tpcds-parts` for a lettered statement, `tpcds-augmented` for the augmented queries. Multi-part OBT queries are compared part by part. |
| `baseline` | The rows differ from the baseline checkout's rows and the difference is not a verified improvement. |
| `missing` | An emitted query has no executed result for a selected engine, for example because its dataset was never built. A cell with no result never passes silently, and an augmented query without a result cannot prove a vacuous base query. |

Engines: SQLite and DuckDB always, DataFusion when installed, PostgreSQL when
`BENCHBOX_POSTGRES_DSN` is set (the CI job provides a service container). PostgreSQL tables are
analyzed after loading so that unindexed joins do not run on default planner
statistics. chDB
is not run: the `chdb` wheel does not load on the maintainer macOS host and no
ClickHouse server is available locally, so ClickHouse output stays covered by
the text corpus until a Linux run is added.

The candidate is the working tree. The baseline is the merge base with the
base branch (`BASE_REF`, else `origin/develop`), checked out in a temporary git
worktree, so the baseline runs its own BenchBox code with its own bundled
wheel. `--candidate-ref` and `--baseline-ref` accept any commit, which is how
the gate is proven against historical wheels. A baseline difference on a
non-DuckDB engine where the candidate matches DuckDB and the baseline did not is
reported as a verified improvement. Any other baseline difference, including
every DuckDB difference, is unexplained.

Rows are compared as multisets (row order is not checked). Numbers use
tolerance, and cross-engine comparison tolerates midnight timestamps against
dates and trailing spaces; baseline comparison on the same engine is strict.

## Vacuous results

A result is vacuous when it has no rows or when no row carries a non-NULL,
non-zero value. A `vacuous` finding is raised for every engine whose result is
vacuous and whose DuckDB reference result is too (DuckDB itself included). The
gate fails closed: a vacuous comparison fails unless it is proven or allowlisted.

- Proven: `scripts/sql_execution_gate/augmentation.py` builds a gate-only
  dataset, `tpcds-augmented`, in the scratch directory; the OBT transformer then
  builds `tpcds_obt-augmented` from it so the same proof covers OBT Q16, Q94 and
  Q95. It gives `catalog_sales`
  and `web_sales` a second warehouse (`(item_sk % 2) + 1`), adds a second
  `warehouse` row, assigns the `ca_state` values and the `web_company_name` that
  the emitted queries filter on to `customer_address` and `web_site`, and runs
  TPC-DS Q16, Q94 and Q95 for the default seed and seed 1001 on every engine
  against DuckDB with the usual `reference` and `baseline` checks. When every
  augmented cell of a query is clean and non-vacuous, the vacuous base result
  is reported as proven by augmented data. The query text, the row-count
  reference and the DuckDB reference comparison are unchanged, and the augmented
  data never leaves the scratch directory and never reaches a published run.
- Allowlisted: a `vacuous` entry in `expected_differences.json` with `match`
  `no rows` or `every cell NULL or 0` and a justification that names the executed
  reason the query returns nothing at SF0.01. `engine` may be a list for this
  check. An entry that no longer matches is stale and fails the gate, so a query
  that gains signal (new seed, new data, new scale) has its entry removed.

The report prints the proven queries, the augmented DuckDB rows, and a section
`NOT PROVEN, vacuous comparisons` with a count per benchmark and engine and one
line per query. `--report` adds `proven_by_augmentation`, `augmented_reference_rows`
and `not_proven`. Never add an entry to hide a vacuity that a seed or the
augmentation could remove, and never loosen the row comparison tolerances.

## Expected differences

`scripts/sql_execution_gate/expected_differences.json` lists every difference
the gate accepts. Each entry names the engine, benchmark, check and queries,
carries a `match` substring that the finding detail must contain (so a different
failure on the same query is still unexplained), and a non-empty
`justification`. Entries for real defects name a `tracker` item and are removed
when the defect is fixed; an entry that no longer matches any finding fails the
gate as stale, except `optional` entries for wall-clock timeouts. Deterministic
engine limits (DataFusion 54.0.0 cannot plan some correlated subqueries of the
Havoc variants) are non-optional entries that quote the engine version and the
exact planner error, so an engine upgrade that fixes them turns the entry stale. Never add an
entry to make a regression pass: fix the fork or the wrapper.

## Running it

```
make sql-execution-gate
make sql-execution-gate SQL_EXECUTION_GATE_ARGS="--baseline-ref origin/develop --report /tmp/gate.json"
make sql-execution-gate SQL_EXECUTION_GATE_ARGS="--candidate-ref <commit> --baseline-ref <commit>"
```

DuckDB (the reference, which also writes the Parquet files DataFusion reads) runs
first; every other engine then runs each dataset as its own unit in a separate
process (spawn context, at most CPU count minus one workers), so the slowest
units rather than the sum of the engines set the wall time. Each unit opens its
own connection (PostgreSQL uses a schema per unit). SQLite enforces the
per-query budget with a timer thread that interrupts the connection instead of
a progress handler, so concurrent units do not starve it of the GIL.

Runtime at SF0.01 on CI with a baseline: about 2.5 minutes before this change
(gate step 2m22s at #3475), 1m39s with the default `tpch,tpcds,tpchavoc` scope
(job https://github.com/BenchBox-dev/benchbox/actions/runs/40245215977/job/121326081501),
and 4m55s with `tpcds_obt` added
(job https://github.com/BenchBox-dev/benchbox/actions/runs/40244114464/job/121322714024).
The OBT delta is 3m16s, over the 3 minute PR-tier allowance the owner set, so
OBT stays in the nightly `sql-execution-gate-obt` job and out of the default
scope. CI runs the
`sql-execution-evidence` job only when the runtime pin, the wrapper, the
SQL-compat package, the TPC-H, TPC-DS, TPC-Havoc and TPC-DS OBT query code, the
local engine adapters or the gate itself change.

## Per-query budget

The budget is 15 s per query (`--timeout`) and it is not raised. Every engine
prints `slowest executions per engine` (the 15 slowest candidate-side queries
with their wall time, `TIMED OUT` when the budget interrupted them), and
`--report` writes the same list under `slowest`. A query that shows more than
a third of the budget (5 s) on a hosted run gets an index or a plan fix, never a
longer timeout or an exclusion.

The budget is derived from hosted timings, not local ones. The runner is
`ubuntu-latest` (image ubuntu-26.04) with SQLite 3.46.1; the local development
host runs SQLite 3.53 and plans differently, so a local pass says nothing about
the hosted plan. Ten consecutive hosted runs of one head before the SQLite key
indexes and ten after (rerunning the `sql-execution-evidence` job only):

| Measure | Before: leading-key index only | After: join and correlation keys indexed |
|---|---|---|
| Runs that passed | 2 of 10 | 10 of 10 |
| TPC-DS q16 on SQLite | timed out at 15 s in 8 runs, 9.9 and 14.0 s in the others | under 0.25 s in every run |
| TPC-DS q16 augmented seeds (`q16.1001`, `q16.default`) | 5.6 to 14.2 s | under 0.25 s |
| TPC-Havoc `10_v5` on SQLite | timed out in 2 runs, 7.8 to 14.1 s in the others | under 0.25 s |
| TPC-Havoc `9_v1` on SQLite | 6.0 to 9.7 s | under 1.7 s |
| Slowest SQLite query in a run | 15.0 s (timeout) in 8 runs, 9.9 s and 14.0 s in the others | 2.2 to 4.4 s (`tpchavoc 9_v4`) |
| SQLite engine time | 63 to 115 s | 16 to 34 s |

After the change the slowest SQLite query in any of the ten runs took 4.4 s,
about a third of the budget, so the 15 s budget keeps a margin of 3.4 times over
the worst hosted time.

Before PostgreSQL got the same indexes, TPC-H Q20, the ten Havoc `20_v` variants
and Havoc `8_v10` and `9_v1` reached the 15 s statement timeout on hosted runs
and were recorded as `optional` timeout entries. `PostgresEngine.open` now
creates the same leading-column and key indexes as SQLite, then runs
`VACUUM (ANALYZE)` on each table it loaded so the visibility map allows
index-only scans. Measured on one PostgreSQL 17 container, TPC-H Q20 went from
15.00 s (statement timeout) to under 0.01 s, and the slowest PostgreSQL query of
the default scope became TPC-H Q1 at 1.1 s. Those entries were removed rather
than kept optional, so a regression now fails the gate.

### OBT queries

The nightly `t3:sql-execution-gate-obt` job timed out on the same OBT queries in
every scheduled run from 2026-11-04 to 2026-11-08: PostgreSQL Q58 and Q60 at the
15 s statement timeout in all five runs, PostgreSQL Q83 once (15.00 s on
2026-11-08, 5.45 s on 2026-11-07), and SQLite augmented Q95 in the two runs that
print timings (15.00 s for both seeds on 2026-11-07, 15.00 s and 14.19 s on
2026-11-08). Indexing the key columns did not help because the OBT has no
`*_sk` column that these queries filter or join on. The causes are in the plans:

- Q95 (and Q16 and Q94) self-join the OBT on `sale_id`, so SQLite scanned the
  whole table once per outer row. `sale_id` is not a key-named column.
- Q56, Q58, Q60 and Q33 filter with `IN (SELECT ... FROM obt WHERE ...)` on
  `item_i_item_id`, `item_i_manufact_id` and `sold_date_d_week_seq`, which the
  planner could only answer by scanning the 715-column table per outer row.
- Q83 and Q58 probe `sold_date_d_date` for each outer row with a bitmap heap
  scan over wide rows (66,044 heap blocks in Q83). An index on
  `(sold_date_d_date, sold_date_d_week_seq, channel)` after the `VACUUM` turns
  that probe into an index-only scan with no heap fetches.

`OBT_INDEXES` in `scripts/sql_execution_gate/obt.py` lists the extra indexes both
engines build for the OBT datasets. Measured at SF0.01 on the maintainer host
(PostgreSQL 16.2, SQLite 3.53.1) before and after:

| Query | Before | After |
|---|---|---|
| PostgreSQL Q58 | 15.9 s (timeout) | 0.01 s |
| PostgreSQL Q60 | 13.7 s | 0.4 s |
| PostgreSQL Q83 | 15.0 s (timeout), 6.7 s with the date indexes only | 0.09 s |
| PostgreSQL Q33 | 5.3 s with the other OBT indexes already in place | 0.13 s |
| PostgreSQL Q56 | 4.5 s | 0.18 s |
| SQLite augmented Q95 (both seeds) | 15.0 s (timeout) | 0.17 s or less |
| Slowest PostgreSQL query of the OBT scope | 15.0 s (timeout) | 1.4 s (Q1) |
| Slowest SQLite query of the OBT scope | 15.0 s (timeout) | 1.5 s (Q35) |
| PostgreSQL engine time, OBT scope | 123.6 s | 54.0 s |

The `optional` timeout entries for PostgreSQL OBT Q56, Q58 and Q60 were removed,
so a timeout on any OBT query now fails the nightly gate. PostgreSQL Q58 now
returns the same empty result as the other engines and joins the Q58 vacuous
entry.

## Adoption evidence

The gate output (candidate and baseline wheel versions, per-engine cell counts,
verified improvements and expected differences) is the evidence for a wheel
adoption. An adoption changes the runtime pin under `benchbox/_vendor/`, so CI
runs the `sql-execution-evidence` job on every adoption PR, whether the delivery
workflow (`.github/workflows/sqlglot-delivery.yml`) opened it or a maintainer
adopted a wheel by hand; that job's output is the evidence reviewers read.
Verified improvements are published-result impacts and are listed as such.

## Translator identity in result bundles

A result bundle whose run logged SQL translation outcomes records
`execution.translation.runtime` with the `distribution`, `version`, `fork_sha`,
`upstream_sha` and `wheel_sha256` (the pin's `sha256`) of the bundled SQLGlot
runtime, read from `benchbox/_vendor/sqlglot.json`. The field is additive under
the existing result schema version and the public export and `benchbox submit`
keep it unchanged; `benchbox.validation.bundle` type-checks it when present.
Absent means not recorded: old bundles, DataFrame runs and runs without
translation outcomes carry none, and it is never inferred. SQLGlot also changes
SQL in paths that log no translation outcomes (read-primitives variant
contracts, TPC-DS qualification); such runs show no runtime.

Published bundles cannot be edited, so a bundle that predates the field can be
attested through its manifest sidecar: `sql_translator_version` holds `version`,
`fork_sha`, `upstream_sha`, `wheel_sha256` and a required `evidence` string, and
`scripts/annotate_sql_translator_version.py` writes it from the reconciled
per-bundle wheel table in `results-data/CORPUS_NOTES.md` without touching bundle
JSON. `resolve_translator_version` in `benchbox.validation.bundle` returns the
bundle's own runtime as `recorded`, else the sidecar value as `attested`, else
`not_recorded`. A malformed attestation, or one that disagrees with a recorded
runtime, is an error in the submission validator, `results-data/validate_corpus.py`
and the Explorer pipeline. It changes no ranking identity and carries no SQL
hashes.

## Fork changes

The gate shows what the new wheel does to the shipped queries; it does not show
what changed inside the fork. Fork source review happens in the fork's own pull
requests, not in BenchBox adoption PRs, and adoption PRs do not carry a fork
diff. To inspect a change between two pins, take the old and new `fork_sha`
from `benchbox/_vendor/sqlglot.json` and run `git diff <old> <new>` in a fork
clone; `git merge-base --is-ancestor <old> <new>` tells a rollback from a
refresh.

The evidence and the code that judges it are soundness paths
(`.github/soundness-paths.txt`): the runtime pin and bundled wheel
(`benchbox/_vendor/`), the wrapper (`benchbox/utils/dialect_utils.py`), the
runtime baseline, everything under `scripts/sql_execution_gate/` (the gate
logic and `expected_differences.json`), everything under
`tests/unit/sql_compat/` (the corpus harness, its `recorded/` and `frozen/`
data and the corpus, wrapper and bypass assertion tests), the
`Makefile` and the `make/` fragments (CI runs the gate through
`make sql-execution-gate`), the pin and retirement scripts, and their tests
(`test_sql_execution_gate.py`, which holds the regression proofs below,
`test_sync_sqlglot_runtime.py`, `test_check_sqlglot_repro_retirement.py` and the
wrapper tests under `tests/unit/utils/`). A change to any of them needs an oracle
success review on the current head.

## Regression proofs

The gate is built to catch the regressions that the text corpus accepted:

- The DATETIME day-offset form in wheel 30.24.0.1 (`DATETIME(DATE(x), '-30 DAY')`
  drops the lower boundary row on SQLite). `tests/unit/scripts/test_sql_execution_gate.py`
  executes the old form against DuckDB and requires a `reference` finding.
- Integer truncation in ratio queries (TPC-DS Q21 on SQLite through wheel
  30.24.0.3). The same test file requires a `reference` finding for the old
  `inv_after / inv_before` form.
- Unpadded dates on SQLite (wheel 30.24.0.12; fixed in 30.24.0.13). TPC-DS Q16,
  Q94 and Q95 filtered on NULL date bounds and passed the gate because SF0.01
  holds one warehouse, so the result is `(0, NULL, NULL)` on every engine.
  Running the gate with `--candidate-ref` at the commit before the adoption
  (`bff115553d38905e3e30e04ce3cd286cd59f946b`, runtime 30.24.0.12) exits 1 with
  a SQLite `reference` finding for each of the six augmented Q16, Q94 and Q95
  runs, for example `(0.0, None, None) != (21.0, 108654.84, -11795.43)`. The same
  gate on 30.24.0.13 passes.

- The SQLite list-aggregate dispatch omission (TPC-Havoc `1_v7`, fixed in
  #3426). `test_the_sqlite_list_aggregate_dispatch_omission_fails_the_gate`
  removes `sqlite` from `LIST_AGGREGATE_DIALECTS`, emits `1_v7` through the real
  benchmark and requires an `error` finding for `tpchavoc 1_v7` on sqlite
  (`near "[0]": syntax error`); with the real set the finding is absent. Run on
  the full gate, the same mutation exits 1 with that single unexplained
  difference.
- Unpadded dates in OBT queries (wheel 30.24.0.12). Running the gate with
  `--benchmarks tpcds_obt --engines sqlite,duckdb --candidate-ref <commit>` at a
  commit whose `benchbox/_vendor` is the 30.24.0.12 wheel exits 1: Q16, Q94 and
  Q95 stay vacuous on SQLite and each of the six `tpcds_obt-augmented` runs
  reports a SQLite `reference` finding such as
  `(0.0, None, None) != (21.0, 108654.84, -11795.43)`.
- SQLite TPC-H Q19 and the Havoc `19_v1`..`19_v10` variants timing out at the
  15 s budget on CI. The gate DDL declares no keys, and every `OR` branch of Q19
  carries its own `p_partkey = l_partkey`, so SQLite has no index to drive the
  join and plans `SCAN lineitem` x `SCAN part` (about 120 M row pairs at SF0.01,
  1.2 M progress ticks of 1000 VM ops, about 3.9 s locally). `ANALYZE` does not
  change that plan or the work done (measured identical, so it is not the
  cause). Indexing the leading column of each table when `SqliteEngine.open`
  loads it gives `SCAN lineitem` + `MULTI-INDEX OR` on `part (p_partkey=?)`
  (about 1 K ticks) with the same `(22923.028,)` result.
  `test_sqlite_engine_indexes_the_leading_key_so_an_or_join_stays_cheap`
  asserts the result and a progress-handler operation budget, not a duration.
- SQLite TPC-DS Q16 timing out at the 15 s budget on the hosted runner, and TPC-Havoc
  `10_v5` once. The hosted runner has SQLite 3.46.1. For the correlated
  `EXISTS (SELECT * FROM catalog_sales AS cs2 WHERE cs1.cs_order_number =
  cs2.cs_order_number ...)` subqueries, 3.46.1 plans `SCAN cs2` once per outer
  row (about 27 s on a loaded development host), where 3.53 and 3.54 build an
  automatic partial covering index on `cs_order_number` and finish in under
  0.5 s. The outcome therefore depended on the SQLite version and on runner
  load. `SqliteEngine.open` now indexes the leading column of each table and
  every column that ends in `key`, `_sk`, `_order_number` or `_ticket_number`;
  the OBT datasets index the leading column and the `OBT_INDEXES` of
  `scripts/sql_execution_gate/obt.py` instead. With the indexes 3.46.1
  plans `SEARCH cs2 USING INDEX catalog_sales_cs_order_number_ix` and runs Q16 in
  0.013 s, and all 347 default queries finish in at most 1.6 s on 3.46.1
  (measured with the SQLite 3.46.1 shell on the same SF0.01 data).
  `test_sqlite_engine_indexes_the_correlation_key_so_an_exists_subquery_is_never_a_scan`
  asserts the index in the query plan, which does not depend on the SQLite
  version or on timing, and `test_index_columns_picks_the_leading_column_and_the_join_keys`
  asserts which columns are indexed.

## TPC-DS OBT equivalence

`make tpcds-obt-equivalence` applies the same vacuity definition when it picks
the parameter seed and when it classifies a query as `unverifiable`: a base part
with no rows, or with only NULL or 0 cells, is not evidence. That test is
DuckDB-only (it loads TPC-DS and the OBT parquet into DuckDB), so it cannot
detect a translation defect on SQLite, PostgreSQL or DataFusion; that coverage
belongs to this gate, which runs every OBT statement on every engine, compares
it with the TPC-DS statement on the same engine and with DuckDB, and runs the
vacuity proof on OBT data.
