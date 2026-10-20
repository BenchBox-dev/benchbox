# Move BenchBox SQL transpilation into the SQLGlot fork

## Decision

The SQLGlot fork (`BenchBox-dev/benchbox-sqlglot`, shipped in BenchBox as
`benchbox._vendor.sqlglot`) is permanent and owns SQL transpilation. BenchBox
keeps benchmark definitions, execution, result validation, DataFrame
translation and governance metadata. Fix-ups that today live in BenchBox
move into the fork and the BenchBox copies are deleted. Every supported
platform gets an explicit dialect, and DDL translation is a first-class
fork feature. Generally useful fixes are still offered upstream; BenchBox
correctness does not wait on upstream review.

This supersedes in part `docs/development/adr/adr-sql-compat-phase-aware-pipeline.md`
(engine-bug workarounds, physical DDL bodies and TPC-Havoc variant rewrites
move to the fork) and the bounded, retirable framing in
`_project/decisions/sqlglot-soft-fork-plan.md`.

Decisions recorded for this work:

- **Real-engine evidence.** Each moved piece needs a fork test that executes
  the translated SQL. In-process engines (sqlite, duckdb, datafusion, chdb)
  and CI containers (Postgres, QuestDB, Trino, StarRocks, Doris, SingleStore)
  are used where available. A piece with no obtainable engine (BigQuery,
  Snowflake, managed Databricks features) ships with AST tests and a recorded
  gap in its tracker item.
- **Output may change when semantically equivalent.** Each adoption pull
  request lists every output difference over the existing unit-test inputs and
  carries a result-version note.
- **Division safeguards are opt-in.** BigQuery, Snowflake and Databricks
  zero-divisor handling changes semantics (a zero divisor returns NULL). The
  fork exposes `safe_division`, off by default. Each adapter that must change
  to enable it is tracked as its own high-priority follow-up.
- **Benchmark data stays in BenchBox, mechanism moves.** Query-id sets, shard
  and sort key tables, variant dispatch tables and similar data stay in
  BenchBox and are passed to the fork as arguments.

## Prior art

- `benchbox/utils/dialect_utils.py` `translate_sql_query` is the single entry
  point and is extended, not replaced. Seven callers under `core/` use it.
- Paths that bypass it are superseded: `platforms/base/dialect_translation.py`
  (duplicates the DuckDB `ALL` restore and the SQLite fix-up),
  `base.py` `translate_query`, `core/clickbench/benchmark.py`, and about 20 more
  files that call the vendored SQLGlot directly.
- `benchbox/sql_compat/` and its phase architecture stay. Registry rows remain
  as governance anchors and are re-pointed at fork API names.
- Fork conventions: dialect classes under `sqlglot/dialects/` with parsers and
  generators in `sqlglot/parsers/` and `sqlglot/generators/` (pattern:
  `dune.py`); `downstream/relocate.py` already allowlists `sqlglot.dialects.`
  so new dialect modules need no assembler change; `downstream/patches.json`
  records carried patches.
- `scripts/check_sqlglot_repro_retirement.py` and
  `scripts/sqlglot_runtime_baseline.json` are extended. Today the checker
  calls `transpile` directly, so wrapper output can change while it passes.
- `scripts/sync_sqlglot_runtime.py` and `benchbox/_vendor/sqlglot.json` adopt
  each new wheel.

## Classification

Mechanism always moves. Only benchmark-keyed data stays.

| Area | Items | Moves as |
|---|---|---|
| Wrapper | DuckDB `ORDER BY ALL` restore; SQLite date/interval, EXTRACT, DATE_TRUNC; Netezza date arithmetic; dialect normalization | Dialect and generator behavior; explicit Netezza, Greenplum, Vertica and DataFusion dialects |
| Wrapper | SQLite discount-bound fold | Generic fork rewrite; the `lineitem` gate stays an argument |
| QuestDB | comma joins, interval arithmetic, substring, CTE column list | New `questdb` dialect generator (AST version also fixes the nested-parentheses regex bug) |
| Adapters | BigQuery, Snowflake, Databricks division safeguards | Opt-in `safe_division` fork option |
| Adapters | Databricks alias dedupe, PK `NULLS`; BigQuery unqualified-table scan | Fork rewrite or API |
| TPC-DI, ClickBench | SQLite idioms, regex escaping | Generator behavior; flag-column list passed as an argument |
| TPC-Havoc, DataFusion | AST variant rewrites, Spark transformer, text replaces | Named fork rewrites taking `(expression, dialect, **args)` |
| DDL | 26 registered rules (4 run at runtime) | Fork `DdlOptions` and `transpile_ddl`; adapter config and tuning values passed in |
| Stays | Variant id sets, SingleStore key tables, 22 governance-anchor rows, `query_adapter` metadata | BenchBox |

## Target architecture

- BenchBox exposes three functions in `benchbox/utils/dialect_utils.py`:
  `translate_sql_query`, `translate_ddl(sql, *, read, write_platform, options)`
  and `apply_rewrite(name, sql_or_expression, dialect, **args)`. All other
  code calls these.
- The fork exports a `PLATFORM_DIALECTS` mapping covering every supported
  platform. A missing platform raises; there is no silent Postgres fallback.
- `DdlOptions` is a frozen dataclass defined in the fork. `BaseDdlOptimizer`
  calls the wrapper instead of dispatching with `getattr`; adapter methods are
  deleted in the same change.
- A repository test fails on direct vendored-SQLGlot calls outside the wrapper
  and `benchbox/sql_compat/`.
- The runtime baseline gains a `wrapper` entry mode so retirement checks
  exercise the wrapper and execute output on the named engine.

## Slices

Fork slices (F) land before the adoption slice (A) that consumes them, and a
deletion slice (D) follows its adoption. Each is one pull request.

| Id | Repo | Content |
|---|---|---|
| F0 | fork | Reconcile the pinned fork commit with `main`; record fork-original work in `patches.json` |
| F1 | fork | Netezza, Greenplum, Vertica, DataFusion and QuestDB dialects; `PLATFORM_DIALECTS` |
| F2 | fork | SQLite generator fixes and discount-bound fold |
| F3 | fork | DuckDB `GROUP BY ALL` / `ORDER BY ALL` generator fix |
| F4 | fork | QuestDB generator |
| F5 | fork | `safe_division`, alias dedupe, unqualified-table API, Databricks PK `NULLS` |
| F6 | fork | TPC-DI idioms and ClickBench regex escaping |
| F7 | fork | `DdlOptions` and `transpile_ddl` core |
| F8 | fork | Engine-specific DDL (ClickHouse, Doris, StarRocks, Hive-family) |
| F9 | fork | TPC-Havoc and DataFusion named rewrites |
| B1 | BenchBox | Route bypass paths through the wrapper; add the bypass test |
| B2 | BenchBox | Migrate remaining direct importers |
| B3 | BenchBox | Checker wrapper mode |
| A1/D1 | BenchBox | Adopt F0-F4, then delete wrapper fix-ups and the QuestDB rewriter |
| A2/D2 | BenchBox | Adopt F5-F6, then delete adapter safeguards and TPC-DI regexes |
| A3/D3 | BenchBox | Adopt F7-F8, then delete DDL rule bodies and rewire `BaseDdlOptimizer` |
| A4/D4 | BenchBox | Adopt F9, then delete Havoc and DataFusion transformers |

Each adoption pull request includes a golden-corpus test that runs the old
BenchBox path and the fork output over the existing unit-test inputs and lists
every difference.

## Rollback

Revert one pull request. Fork slices are inert until adopted. A wheel is a
superset of its predecessor, so reverting a deletion slice restores the old
code against the new wheel. Revert in reverse dependency order: D before A.

## Risks

- AST rewrites can differ from the regexes they replace; the corpus diff and
  result-version note exist for this.
- `make compat-docs-check` fails on stale generated docs when rules are
  re-pointed; each D slice regenerates them.
- Deleting an adapter DDL method without rewiring `getattr` dispatch breaks
  DDL at runtime; D3 changes both together.
- Several moved functions had no confirmed call sites or tests; each D slice
  verifies before deleting.
- BigQuery, Snowflake and Databricks-managed features have no local engine, so
  their real-engine evidence is a recorded gap.
