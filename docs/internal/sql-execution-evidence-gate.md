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
| `baseline` | The rows differ from the baseline checkout's rows and the difference is not a verified improvement. |

Engines: SQLite and DuckDB always, DataFusion when installed, PostgreSQL when
`BENCHBOX_POSTGRES_DSN` is set (the CI job provides a service container). chDB
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
`tests/unit/sql_compat/golden_corpus/` (harness, `recorded/` and `frozen/`),
and the pin, retirement and fork-diff scripts. A change to any of them needs an
oracle success review on the current head.

## Regression proofs

The gate is built to catch the two regressions that the text corpus accepted:

- The DATETIME day-offset form in wheel 30.24.0.1 (`DATETIME(DATE(x), '-30 DAY')`
  drops the lower boundary row on SQLite). `tests/unit/scripts/test_sql_execution_gate.py`
  executes the old form against DuckDB and requires a `reference` finding.
- Integer truncation in ratio queries (TPC-DS Q21 on SQLite through wheel
  30.24.0.3). The same test file requires a `reference` finding for the old
  `inv_after / inv_before` form.
