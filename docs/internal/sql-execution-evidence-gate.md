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

## Adoption evidence

Each wheel adoption PR pastes the gate output (candidate and baseline wheel
versions, per-engine cell counts, verified improvements and expected
differences) into its description. Verified improvements are published-result
impacts and are listed as such.

## Fork diff

The gate shows what the new wheel does to the shipped queries; it does not show
what changed inside the fork. Each wheel adoption PR therefore also carries the
fork diff from the previous pinned `fork_sha` to the new one, so the oracle and
human reviewers read the translation semantics they are admitting:

```
make sqlglot-fork-diff OUT=/tmp/fork-diff.md
make sqlglot-fork-diff OLD=<old fork_sha> NEW=<new fork_sha> OUT=/tmp/fork-diff.md
```

`OLD` defaults to the `fork_sha` in `benchbox/_vendor/sqlglot.json` at
`BASE_REF` (default `origin/develop`); `NEW` defaults to the pin in the working
tree. The tool fetches both commits from the fork into a throwaway bare
repository and writes Markdown with the commit list, the diffstat and the
per-file diff, `downstream/` and `sqlglot/benchbox/` first. Files that do not
fit the size budget (50 KB by default) are listed by name, not dropped; read
them with `git diff <old> <new> -- <path>` in a fork clone. Paste the output
into the PR description next to the gate output. A rollback adoption prints
`Direction: not-descendant`.

The evidence and the code that judges it are soundness paths
(`.github/soundness-paths.txt`): the runtime pin and bundled wheel
(`benchbox/_vendor/`), the wrapper (`benchbox/utils/dialect_utils.py`), the
runtime baseline, everything under `scripts/sql_execution_gate/` (the gate
logic and `expected_differences.json`), everything under
`tests/unit/sql_compat/` (the corpus harness, its `recorded/` and `frozen/`
data and the corpus, wrapper and bypass assertion tests), the
`Makefile` and the `make/` fragments (CI runs the gate through
`make sql-execution-gate`), the pin, retirement and fork-diff scripts, and
their tests (`test_sql_execution_gate.py`, which holds the regression proofs
below, `test_sync_sqlglot_runtime.py`, `test_check_sqlglot_repro_retirement.py`,
`test_sqlglot_fork_diff.py` and the wrapper tests
under `tests/unit/utils/`). A change to any of them needs an oracle success
review on the current head.

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

## TPC-DS OBT equivalence

`make tpcds-obt-equivalence` applies the same vacuity definition when it picks
the parameter seed and when it classifies a query as `unverifiable`: a base part
with no rows, or with only NULL or 0 cells, is not evidence. That test is
DuckDB-only (it loads TPC-DS and the OBT parquet into DuckDB), so it cannot
detect a translation defect on SQLite, PostgreSQL or DataFusion; that coverage
belongs to this gate, which runs every OBT statement on every engine, compares
it with the TPC-DS statement on the same engine and with DuckDB, and runs the
vacuity proof on OBT data.
