# SQL execution evidence gate

`make sql-execution-gate` is the evidence every SQLGlot runtime adoption and
every change to SQL translation must show. It executes the translated SQL of
every shipped TPC-H (22) and TPC-DS (99) query on each local engine and compares
the returned rows. It replaces the text-diff golden corpus as the primary
evidence; the corpus remains a secondary view of output text.

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
  dataset, `tpcds-augmented`, in the scratch directory. It gives `catalog_sales`
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
gate as stale, except `optional` entries for wall-clock timeouts. Never add an
entry to make a regression pass: fix the fork or the wrapper.

## Running it

```
make sql-execution-gate
make sql-execution-gate SQL_EXECUTION_GATE_ARGS="--baseline-ref origin/develop --report /tmp/gate.json"
make sql-execution-gate SQL_EXECUTION_GATE_ARGS="--candidate-ref <commit> --baseline-ref <commit>"
```

Runtime on a development machine at SF0.01: about 1 minute to emit each side's
queries, about 80 seconds for SQLite (four queries run into the 15 second
budget), a few seconds for DuckDB and DataFusion and about 50 seconds for
PostgreSQL. A run with a baseline takes about 3 to 4 minutes. CI runs the
`sql-execution-evidence` job only when the runtime pin, the wrapper, the
SQL-compat package, the TPC-H and TPC-DS query code, the local engine adapters
or the gate itself change.

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

## TPC-DS OBT equivalence

`make tpcds-obt-equivalence` applies the same vacuity definition when it picks
the parameter seed and when it classifies a query as `unverifiable`: a base part
with no rows, or with only NULL or 0 cells, is not evidence. That test is
DuckDB-only (it loads TPC-DS and the OBT parquet into DuckDB), so it cannot
detect a translation defect on SQLite, PostgreSQL or DataFusion; that coverage
belongs to this gate.
