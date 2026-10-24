# ADR: SQLGlot Use and Non-Use

## Status

Accepted (codifies existing practice as of 2026-05-03).

**Superseded in part**: [The SQLGlot fork owns transpilation](adr-sqlglot-fork-owns-transpilation.md)
replaces the boundary below that keeps engine bugs, physical DDL and benchmark-neutral
rewrites in BenchBox, and replaces the retirement protocol as the way to remove them.
Benchmark-keyed data, execution, validation and DataFrame translation stay in BenchBox.

**Runtime**: BenchBox ships a pinned pure-Python wheel from
[`BenchBox-dev/benchbox-sqlglot`](https://github.com/BenchBox-dev/benchbox-sqlglot),
based on upstream 30.24.0 with three bounded downstream patches. The runtime
imports as `benchbox._vendor.sqlglot`, not the public `sqlglot` package.

**Maintenance protocol**: Re-review on each immutable fork-pin refresh, when
the phase taxonomy in the
[phase-aware pipeline ADR](adr-sql-compat-phase-aware-pipeline.md) changes,
or before adopting a compiled runtime.

## Date

2026-05-03

## Context

BenchBox runs benchmarks across 36 SQL platforms plus DataFrame engines
(Polars, Pandas, DataFusion-Python, PySpark, Dask, Modin, cuDF, LakeSail).
Cross-platform portability requires translating canonical benchmark SQL into
something each engine accepts and executes correctly. We chose
[SQLGlot](https://github.com/tobymao/sqlglot) for that translation. BenchBox
owns its bounded downstream fork; the pure-Python wheel is bundled inside the
published BenchBox wheel and sdist, with no separate PyPI publication.

Over the last 12 months BenchBox accreted a substantial layer of SQL
infrastructure on top of SQLGlot, organized into seven categories:
dialect normalization, post-generation fixups, three per-platform query
transformers, a 19-platform DDL rewrite registry, hand-written query
overrides, a SQL-to-DataFrame facade, and the
[`sql_compat` rule engine](adr-sql-compat-phase-aware-pipeline.md). This ADR
documents *why* we use SQLGlot for some decisions and deliberately do not
use it for others; the sibling ADR documents *how* the rule engine is
organized.

## Decision

**SQLGlot is the canonical translation engine for query-shaped SQL between
SQL dialects.** Every SQL query travels through SQLGlot at the
`query_compile` phase of the `sql_compat` pipeline unless an explicit
registry decision says otherwise.

**SQLGlot is deliberately not used for** five categories outside what a
syntactic transpiler can structurally do:

1. Engine-semantic query rewrites (well-formed SQL, wrong results)
2. DDL physical-design semantics (engines, sort/shard keys, table formats)
3. Hand-written per-engine query variants (emitter output the engine refuses)
4. SQL-to-DataFrame translation (Polars, Pandas, etc.)
5. Compatibility governance (registry, lint, capability matrix)

### Non-Goals

- Replacing SQLGlot or expanding the three-patch fork into a new SQL engine.
- Adopting `sqlglot[c]` or runtime compilation.
- Treating the current immutable fork pin as permanent.
- Standardizing every dialect-branching call site. Legitimate local
  rendering (type mapping, identifier quoting, storage layout) remains in
  adapter code, gated by `@compat_local`.

### Bundled Wheel Ownership and Refresh

SQLGlot source and upstream patch history remain in
`BenchBox-dev/benchbox-sqlglot`. BenchBox stores only the required pinned
pure-Python wheel as package data, not an unpacked SQLGlot source tree.
The private package bootstrap extends `benchbox._vendor.__path__` to the
wheel's `benchbox/_vendor` directory for standard zipimport. It does not
modify global `sys.path`, install or extract packages at runtime, or alias
the public `sqlglot` module.

Ordinary BenchBox installation and builds use that bundled artifact; they
must not fetch Git or consult a custom package index. Runtime version
reporting uses the bundled module's `__version__`, not
`importlib.metadata.version("sqlglot")`. Preserve the upstream license,
notices, typing marker and provenance in the runtime wheel.

Refresh from a clean checkout of an immutable fork commit: build its private
runtime wheel, update the BenchBox pin/artifact/provenance together, and
review the bounded downstream patch delta. Validate source and installed
wheel behavior, including the utility, adapter mixin and BaseBenchmark
translation entrypoints. Roll back by restoring the preceding pin and its
matching wheel/provenance together, never by selecting a moving branch.
Public-upstream comparisons under `_project/sqlglot-upstream/repros` remain
isolated and intentionally import public SQLGlot.

The SQLite postprocessor is retained conservatively because it mixes
DATE_TRUNC, unknown/timestamp EXTRACT, MONTH/YEAR interval handling and Q6
discount-bound semantics. The fork's typed-date fixes do not justify deleting
those cases. DuckDB ALL restoration, dialect normalization, engine-semantic
rewrites, DDL registries and authored variants remain BenchBox-owned.

---

## Where SQLGlot is Canonical

| Operation | Site | Notes |
|-----------|------|-------|
| Source-to-target SQL translation | `benchbox/utils/dialect_utils.py` `translate_sql_query()` | Centralized wrapper; pre/post processors plug in here |
| Identifier quoting, function/operator renaming, type-syntax conversion | SQLGlot generator | Per-dialect; type *aliasing* (e.g., `VARCHAR` to `STRING` for Doris) is handled at the DDL layer |
| AST parsing | SQLGlot parser | Consumed by `query_adapter` and `query_compile` phases |
| Roundtrip parsing of TPC-base SQL | SQLGlot | Default base is `netezza`; `dialect_utils.resolve_sql_dialect()` maps platform keys to fork dialects and raises for an unknown name |

SQLGlot handles the long tail of SQL syntax variation (joins, CTEs, window
functions, aggregates, basic types, `LIMIT` to `TOP N`, `EXTRACT` to
`DATE_PART`, function renames). This is the 80% case.

---

## Where SQLGlot is Deliberately Not Used

### 1. Engine-Semantic Query Rewrites

**The case**: SQL is well-formed in the target dialect's grammar and
accepted by SQLGlot's generator, but the target engine's planner executes
it incorrectly. User-visible failure mode: "the query ran and returned
wrong results." A syntactic transpiler cannot catch this; the bug lives in
the engine planner, not the SQL.

These cases are tagged `failure_mode=SILENT_CORRUPTION` in the rule
registry; see the `FailureMode` enum in the
[phase-aware pipeline ADR](adr-sql-compat-phase-aware-pipeline.md).

**Steelman and rebuttal**: One could argue a portability-focused transpiler
should ship rewrite recipes for known engine bugs. We do not adopt this
position: SQLGlot's job is dialect-correct emission, and an engine-bug
registry is its own artifact (it could live alongside SQLGlot, in a sidecar
project, or in project-specific layers). We would happily contribute to
such an artifact if one emerges.

**Implementation**:
[`benchbox/platforms/datafusion_tpch_rewrites.py`](../../../benchbox/platforms/datafusion_tpch_rewrites.py)
applies the fork's named rewrites to four TPC-H queries (Q11, Q16, Q18, Q20). The
[`query_adapter` rules](../../../benchbox/sql_compat/rules/query_adapter/datafusion_query_rewrites.py)
register them as `REWRITE_QUERY` actions with a `transformer_id` per query.
Each rewrite is validated against TPC-H reference answers.

**Retirement protocol**: A rewrite is a candidate for retirement when
(a) the engine release notes claim the bug is fixed, (b) the rewrite-bypass
version passes validation against TPC-H references, and (c) the rule is
removed and CI re-runs validation. Retirement is opt-in.

### 2. DDL Physical-Design Semantics

DDL physical design (engines, sort keys, shard keys, table formats,
distribution policies) depends on deployment context. SQLGlot is a *query*
transpiler; this is correctly outside its scope.

Examples we ship per platform:

- ClickHouse: strip `Nullable NOT NULL`; inject `ENGINE=MergeTree() ORDER BY (...)`
- Doris: `TIME`->`VARCHAR(8)`; `STRING`->`VARCHAR(65533)`; `ARRAY[N]`->`ARRAY`; inject `DUPLICATE KEY` + `DISTRIBUTED BY HASH`
- SingleStore: strip FK; `CREATE REFERENCE TABLE` for dimensions; inject `SHARD KEY` + `SORT KEY` (in order)
- Databricks: `CREATE OR REPLACE TABLE ... USING DELTA TBLPROPERTIES(...)`
- pg_mooncake: preserve heap DDL for COPY, then promote loaded tables into mooncake mirrors

**Implementation**: 19 platform-specific modules under
[`benchbox/sql_compat/rules/ddl_optimize/`](../../../benchbox/sql_compat/rules/ddl_optimize/),
whose rewriting is performed by `translate_ddl` (see the
[phase-aware pipeline ADR](adr-sql-compat-phase-aware-pipeline.md)).

**Fabric Warehouse** occupies a third path between "in the registry" and
"not in the registry":
[`fabric_dw_ddl_rewrites.py`](../../../benchbox/sql_compat/rules/ddl_optimize/fabric_dw_ddl_rewrites.py)
registers a `governance_only=True` rule so `compat_lint` can audit the
platform, but the runtime transformation is the `schema` option of
`translate_ddl` (the schema prefix comes from adapter state passed by
`FabricWarehouseAdapter.create_schema`). The registry rule is the
governance marker.

### 3. Hand-Written Per-Engine Query Variants

When SQLGlot's emitter produces SQL the engine refuses, we ship hand-written
variants. The clearest example is H2O Q9 (`PERCENTILE_CONT WITHIN GROUP`):

- ClickHouse needs `quantile()`
- StarRocks needs `PERCENTILE_APPROX()`
- MySQL/SingleStore accepts the ANSI form, but only without the
  multi-expression `WITHIN GROUP ORDER BY (CASE WHEN ... THEN ... ELSE NULL END)`
  decorator that SQLGlot emits

Function translation between vendor-specific ordered-set aggregates is
non-bijective; every choice the emitter could make fails on at least one
engine. Variants live under
[`benchbox/sql_compat/rules/query_source/`](../../../benchbox/sql_compat/rules/query_source/)
(`h2odb_variants.py`, `nyctaxi_variants.py`, `coffeeshop_variants.py`,
`tpcdi_variants.py`, `vector_search_variants.py`). The fallback is a
`SELECT_VARIANT` action at the `query_source` phase, which short-circuits
SQLGlot.

### 4. SQL-to-DataFrame Translation

DataFrame engines (Polars, Pandas, DataFusion-Python, PySpark, Dask,
Modin, cuDF, LakeSail) do not speak SQL; SQLGlot translates SQL into more
SQL, so this is structurally outside its scope.
[`benchbox/platforms/dataframe/unified_frame.py`](../../../benchbox/platforms/dataframe/unified_frame.py)
is the SQL-shaped facade (approximately 4,200 lines, the largest single
file in our cross-platform layer). The `sql_compat` registry's
`dataframe_filter` phase carries skip/variant decisions for DataFrame mode.

### 5. Compatibility Governance

The registry, lint, and capability-matrix layer is BenchBox-specific and
exists to govern the four categories above. SQLGlot has no opinion about
our compatibility policy.

- [`benchbox/sql_compat/`](../../../benchbox/sql_compat/), rule engine
- [`scripts/compat_lint.py`](../../../scripts/compat_lint.py), runs in permanent error mode
- [`scripts/generate_compat_docs.py`](../../../scripts/generate_compat_docs.py), regenerates capability matrix and skip reference
- Generated artifacts: [capability matrix](../../compat/capability-matrix.md), [skip reference](../../compat/skip-reference.md)

---

## Alternatives Considered

- **SQLGlot only (no infrastructure on top)**: rejected. Early BenchBox
  releases operated this way; produced silent validation failures
  (DataFusion Q11/Q16/Q18/Q20), table-creation failures (ClickHouse, Doris),
  and parse errors (QuestDB comma joins, MySQL ordered-set aggregate
  decorators). Every category in the section above is something we tried
  *not* doing.
- **Custom parser/transpiler**: rejected. Building a maintained transpiler
  covering 20+ dialects is years of work; the 20% we add is at the boundary,
  not the core.
- **sqlglotrs (Rust)**: rejected for now. Pure-Python compilation via mypyc
  gets us most of the speedup without a Rust toolchain. SQLGlot performance
  has not been a BenchBox bottleneck.
- **Adopt sqlglot[c] immediately**: deferred. Speedups would benefit our
  workload, but we want to verify wheel availability across our supported
  Python versions and architectures, and we have not rigorously measured
  SQLGlot's share of the BenchBox runtime budget.
- **Hand-write SQL per platform**: rejected. BenchBox ships 22 TPC-H +
  99 TPC-DS queries plus several other suites across 36 platforms;
  manually maintaining ~792 TPC-H query variants alone is operationally
  infeasible.

---

## Consequences

### Positive

- The boundary between "SQLGlot's job" and "BenchBox's job" is documented;
  contributors can decide where new compatibility work belongs without
  re-deriving it.
- SQLGlot upgrades are routine; we do not depend on SQLGlot to do things it
  does not promise.
- Each of the five categories has a separate, documented home; a contributor
  hitting a DataFusion silent-corruption case knows to add a rule in
  `query_adapter`, not file a SQLGlot bug.
- The compatibility surface is auditable via the `sql_compat` inventory tool.

### Negative / Risks

- The infrastructure on top of SQLGlot covers seven categories and grows
  with each new platform; contributors evaluating cross-engine support must
  read this ADR plus the phase-aware pipeline ADR.
- Engine-version drift can re-open closed semantic-rewrite cases. Stale
  rewrites accumulate (the retirement protocol is opt-in).
- Hand-written variants are a maintenance burden when benchmark specs
  change; mitigated by keeping the variant set small.

### Neutral

- We use SQLGlot's main release line at our normal cadence. If we adopt
  `sqlglot[c]` later, this ADR does not need updating; the boundary is the
  same regardless of how SQLGlot is compiled.

### Upstream Contribution Policy

We are open to contributing patches upstream where they fit SQLGlot's
mission: bug fixes in dialect generators (e.g., `GROUP BY ALL` quoting in
DuckDB), and new tests for dialects we exercise heavily (Doris, QuestDB,
DataFusion). We do not expect SQLGlot to absorb engine-semantic rewrites,
DDL physical-design, hand-written variants, DataFrame translation, or
compatibility governance.

---

## How to Decide Where New Work Goes

1. **Does SQLGlot already handle this when called via
   `dialect_utils.translate_sql_query()`?** If yes, no work needed.
2. **Syntactically wrong on the target engine, fix is dialect-generic?**
   Add a post-processor in `dialect_utils.py`; register as a
   `post_translate` action.
3. **Specific to one platform's syntax quirks?** Add a `query_adapter` rule
   with `REWRITE_QUERY` and a `transformer_id`.
4. **SQL is correct but the engine returns wrong results?** Add a
   `query_adapter` rule with `REWRITE_QUERY` that produces the same answer
   through a different shape; validate against reference answers.
   - **4a.** If the AST rewrite is structurally unsafe (corrupts queries
     exercising `EXCEPT`/`INTERSECT`, `GROUP BY` at subquery boundaries,
     etc.) and the engine offers a session knob, prefer
     `SET_SESSION_POLICY` over `REWRITE_QUERY`. Canonical example:
     ClickHouse TPC-DS Q23/Q87 `joined_subquery_requires_alias=0`.
5. **Issue in DDL?** Add a `ddl_optimize` rule and implement the
   transformation as a `translate_ddl` option passed by the platform adapter.
6. **Emitter produces SQL the engine refuses with no clean AST fix?**
   Hand-write a variant in `sql_compat/rules/query_source/`; register a
   `SELECT_VARIANT` action.
7. **DataFrame engine?** Add to `unified_frame.py` or the per-engine adapter;
   use `dataframe_filter` for skip/variant decisions.

If none fit, open an issue or draft PR; the existing taxonomy may need to
extend.

---

## Cross-References

- [ADR: `sql_compat` Phase-Aware Compatibility Pipeline](adr-sql-compat-phase-aware-pipeline.md), implementation of the rule engine.
- [Capability matrix](../../compat/capability-matrix.md), generated platform support levels.
- [Skip reference](../../compat/skip-reference.md), generated skip / suppressed-DDL / informational rules.
- [Dialect translation user guide](../../usage/dialect-translation.md), user-facing distinction between dialect translation and platform adapters.
- [SQLGlot project](https://github.com/tobymao/sqlglot), upstream.
- [Fivetran post on compiling SQLGlot with mypyc (2026-05-01)](https://www.fivetran.com/blog/how-we-accelerated-transpilation-by-compiling-sqlglot-with-mypyc), context for the deferred `sqlglot[c]` decision.
- BenchBox blog post: *"What we built on top of SQLGlot (and why transpilation isn't enough)"* in `building-benchbox`, external-facing version of the same boundary.
