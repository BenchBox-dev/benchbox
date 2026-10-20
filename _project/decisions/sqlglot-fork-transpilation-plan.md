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
(engine-bug workarounds, physical DDL bodies and benchmark-variant rewrites
move to the fork) and the bounded, retirable framing in
`_project/decisions/sqlglot-soft-fork-plan.md`.

Decisions recorded for this work:

- **Real-engine evidence.** Each moved piece needs a fork test that executes
  the translated SQL. In-process engines (sqlite, duckdb, datafusion, chdb)
  and CI containers (Postgres, QuestDB, Trino, StarRocks, Doris, SingleStore)
  are used where available. A piece with no obtainable engine (BigQuery,
  Snowflake, managed Databricks features) ships with AST tests and a recorded
  gap in its tracker item. The fork has no engine CI today, so building it is
  the first fork slice after the manifest work.
- **Output may change when semantically equivalent.** Each adoption pull
  request lists every output difference over the cumulative unit-test inputs of
  all moved areas and carries a result-version note.
- **Division safeguards are opt-in.** BigQuery, Snowflake, Databricks and
  ClickHouse zero-divisor handling changes semantics (a zero divisor returns
  NULL). The fork exposes `safe_division`, off by default. Each adapter passes
  it explicitly before its local safeguard is deleted, so behavior is unchanged
  at every step. Each adapter's review of result impact is a high-priority
  follow-up item.
- **Benchmark data stays in BenchBox, mechanism moves.** Query-id sets, shard
  and sort key tables, variant dispatch tables and similar data stay in
  BenchBox and are passed to the fork as arguments.
- **The platform-to-dialect mapping lives in BenchBox's platform registry.**
  The fork provides dialects only, so adding a platform does not require a
  fork release. A BenchBox test asserts every registered platform resolves to
  a dialect the fork provides; an unknown platform raises, with no silent
  Postgres fallback.

## Prior art

- `benchbox/utils/dialect_utils.py` `translate_sql_query` is the single entry
  point and is extended, not replaced. Six files under `core/` call it at nine
  sites.
- Paths that bypass it are superseded: `platforms/base/dialect_translation.py`
  (duplicates the DuckDB `ALL` restore and the SQLite fix-up), `base.py`
  `translate_query`, `core/clickbench/benchmark.py`, and about 20 more files that
  call the vendored SQLGlot directly. Some modules legitimately parse without
  generating dialect output (DataFrame translation, equivalence checks,
  qualification); the bypass test lists them with reasons.
- `benchbox/sql_compat/` and its phase architecture stay. Its README defines
  `governance_only` rows as anchors for runtime behavior that stays in an
  adapter-specific path: 22 of the 26 DDL rows are such anchors over live
  adapter code (Firebolt, Redshift, Databend, StarRocks, Athena, ClickHouse and
  others), not dead code. Only SingleStore inherits `BaseDdlOptimizer`.
- Fork conventions: dialect classes under `sqlglot/dialects/` with parsers and
  generators in `sqlglot/parsers/` and `sqlglot/generators/` (pattern:
  `dune.py`); `downstream/relocate.py` already allowlists `sqlglot.dialects.`
  so new dialect modules need no assembler change.
- Fork refresh tooling (`downstream/refresh.py`) replays only patches with
  `status: carried` and treats conflicts outside carried paths as manual. Work
  that edits upstream-owned files (dialect registry lists, the `Dialects` enum,
  existing generators) must therefore be recorded as carried patches with
  `fix: retain <ID>` commits. `status: original` is limited to new files.
- `scripts/check_sqlglot_repro_retirement.py` and
  `scripts/sqlglot_runtime_baseline.json` are extended. The checker calls
  `transpile` directly today, rejects passes without database execution for
  targets other than sqlite and duckdb, and requires each workaround path to
  exist.
- `scripts/sync_sqlglot_runtime.py` and `benchbox/_vendor/sqlglot.json` adopt
  each new wheel. The pinned fork commit `6de7947` is an ancestor of fork `main`.

## Classification

Mechanism always moves. Only benchmark-keyed data stays.

| Area | Items | Moves as |
|---|---|---|
| Wrapper | DuckDB `ORDER BY ALL` restore; SQLite date/interval, EXTRACT, DATE_TRUNC; dialect normalization | Generator behavior; explicit Netezza, Greenplum, Vertica, DataFusion and QuestDB dialects. Callers pass `standard`, `ansi` and `netezza`; the mapping defines each key and its alias |
| Wrapper | TPC-DS `fix_postgres_date_arithmetic` | Fork rewrite keyed on column types or a schema passed as an argument; the date-column gate data stays in BenchBox |
| Wrapper | SQLite discount-bound fold | Generic fork rewrite; the `lineitem` gate stays an argument |
| QuestDB | comma joins, interval arithmetic, substring, CTE column list | New `questdb` dialect generator (the AST version also fixes the nested-parentheses regex bug); the adapter switches from `postgres` to `questdb` |
| Adapters | BigQuery, Snowflake, Databricks, ClickHouse division safeguards | Opt-in `safe_division` fork option |
| Adapters | Databricks alias dedupe, PK `NULLS`; BigQuery unqualified-table scan | Fork rewrite or API |
| ClickHouse, StarRocks, TPC-DS | `ClickHouseQueryTransformer`, StarRocks execute-time rewrites, TPC-DS ClickHouse Q10/Q35 overrides, interval normalization and Q58 fix | Named fork rewrites and generator behavior |
| TPC-DI, ClickBench | SQLite idioms, regex escaping | Generator behavior; flag-column list passed as an argument |
| TPC-Havoc, DataFusion, Spark | AST variant rewrites, Spark transformer, text replaces | Named fork rewrites taking `(expression, dialect, **args)` |
| DataFusion | Q11 and Q20 whole-query replacements | New AST decorrelation work, validated against reference answers in BenchBox |
| DDL | 26 registered rules | Fork `DdlOptions` and `transpile_ddl`; adapter state and tuning values passed in |
| Stays | Variant id sets, `_BIGQUERY_FROMLESS_IDS`, SingleStore key tables, registry rows, `query_adapter` and `query_compile` metadata | BenchBox |

## Target architecture

- BenchBox exposes `translate_sql_query`, `translate_ddl(sql, *, read,
  write_platform, options)` and `apply_rewrite(name, sql_or_expression,
  dialect, **args)` in `benchbox/utils/dialect_utils.py`. `apply_rewrite` ships
  with the first wheel adoption because adapter slices need it; `translate_ddl`
  ships with the DDL wheel adoption.
- Adapters that rewrite already-translated SQL at execution time call
  `apply_rewrite`, not `translate_sql_query`.
- `DdlOptions` is a frozen dataclass defined in the fork. `BaseDdlOptimizer`
  and each adapter's schema-creation path call the wrapper with options built
  from adapter state (ClickHouse tuning clauses, StarRocks table tunings,
  Athena staging, QuestDB partitioning and designated timestamp). Adapter DDL
  methods are deleted per adapter family in the same change that rewires them.
- A repository test fails on direct vendored-SQLGlot calls outside the wrapper
  and `benchbox/sql_compat/`; remaining parse-only uses are listed with reasons.
- Wrapper callers pass `identify` explicitly. Output changes caused by routing
  (ClickBench quoting, TPC-DI `ansi` reads) are covered by a corpus diff.
- The runtime baseline gains a `wrapper` entry mode. The checker gains an
  executor or a stated policy for targets without a local engine.

## Slices

Fork slices (F) land before the adoption slice (A) that consumes them, and a
deletion slice (D) follows its adoption. Each is one pull request unless noted.

| Id | Repo | Content |
|---|---|---|
| F0 | fork | Manifest and refresh tooling: `original` status for new files only, edits to upstream-owned files as carried patches, refresh fixture, docs |
| F0b | fork | Real-engine CI (in-process engines, containers, Python 3.9 constraint) |
| F1 | fork | Netezza, Greenplum, Vertica, DataFusion and QuestDB dialects; date-arithmetic rewrite |
| F2 | fork | SQLite generator fixes and discount-bound fold |
| F3 | fork | DuckDB `GROUP BY ALL` / `ORDER BY ALL` generator fix |
| F4 | fork | QuestDB generator |
| F5 | fork | `safe_division`, alias dedupe, unqualified-table API, Databricks PK `NULLS` |
| F6 | fork | TPC-DI idioms and ClickBench regex escaping |
| F7 | fork | `DdlOptions` and `transpile_ddl` core |
| F8 | fork | Engine-specific DDL (two PRs) |
| F9 | fork | TPC-Havoc and DataFusion named rewrites (two PRs) |
| F10 | fork | ClickHouse, StarRocks and TPC-DS runner rewrites |
| B1 | BenchBox | Route bypass paths through the wrapper; bypass test; corpus diff |
| B2 | BenchBox | Migrate remaining direct importers via `translate_sql_query` (three PRs) |
| B3 | BenchBox | Checker wrapper mode, executor policy |
| A1/D1 | BenchBox | Adopt F0-F5 and add `apply_rewrite`, then delete wrapper fix-ups and the QuestDB rewriter |
| A2/D2 | BenchBox | Adopt F6, then delete adapter safeguards (after each adapter enables `safe_division`) and TPC-DI regexes |
| A3/D3a-c | BenchBox | Adopt F7-F8 and add `translate_ddl` (needs only A1, so DDL is not delayed), then delete DDL rule bodies per adapter family |
| A4/D4 | BenchBox | Adopt F9, then delete Havoc, Spark and DataFusion transformers |
| A5/D5 | BenchBox | Adopt F10, then delete ClickHouse, StarRocks and TPC-DS runner rewrites |

Each adoption builds from a recorded fork commit and carries a cumulative
golden-corpus test: it runs the old BenchBox path and the fork output over the
existing unit-test inputs of every moved area and lists every difference.

## Rollback

Revert one pull request. A fork slice changes `transpile` output for every
caller once a wheel containing it is adopted, so rollback of an adoption
restores the previous wheel, pin and baseline together. A wheel is a superset
of its predecessor, so reverting a deletion slice restores the old code against
the new wheel. Revert in reverse dependency order: D before A.

## Risks

- AST rewrites can differ from the regexes they replace; the cumulative corpus
  diff and result-version note exist for this.
- `make compat-docs-check` fails on stale generated docs when rules are
  re-pointed; each D slice regenerates them.
- Deleting an adapter DDL method before its schema path is rewired breaks
  schema creation; each D3 slice rewires and deletes one adapter family.
- The ClickBench and TPC-DI routing changes alter quoting and read dialect;
  they carry corpus diffs.
- The fork refresh procedure must handle the new work; F0 changes the tooling
  and its fixture before any behavioral slice lands.
- BigQuery, Snowflake and Databricks-managed features have no local engine, so
  their real-engine evidence is a recorded gap.
- DataFusion Q11 and Q20 are whole-query text replacements. A generic version is
  new decorrelation work and must be validated against reference answers.
