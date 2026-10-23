# ADR: sql_compat Phase-Aware Compatibility Pipeline

**Status**: Accepted
**Date**: 2026-04-20

**Superseded in part**: [The SQLGlot fork owns transpilation](adr-sqlglot-fork-owns-transpilation.md)
moves the SQL rewrites and DDL rule bodies described below into the fork, which BenchBox
reaches only through `translate_sql_query` and its sibling wrappers. The phase taxonomy,
the skip policy and the capability registry in this ADR remain in force.

The shipped subsystem lives at [`benchbox/sql_compat/`](../../../benchbox/sql_compat/). Generated
references regenerated from the registry by `scripts/generate_compat_docs.py`:

- [Capability matrix](../../compat/capability-matrix.md)
- [Skip reference](../../compat/skip-reference.md)

---

## Context

BenchBox's SQL compatibility decisions are currently scattered across structurally different sites
that disagree with each other:

| Site | Kind | Problem |
|------|------|---------|
| `dialect_utils.py:108` `translate_sql_query()` | Translation core A | Full-featured: identify policy, SQLite fixups, GROUP BY ALL restoration, pre/post processors |
| `dialect_translation.py:33` `DialectTranslationMixin.translate_sql()` | Translation core B | Bare `sqlglot.transpile`, no identify, no processors, default source = "duckdb" not "netezza" |
| `write_primitives/benchmark.py:171` | Lock bypass | Skips PK lock for `("datafusion","clickhouse","starrocks")` |
| `transaction_primitives/benchmark.py:149` | Lock bypass | Skips PK lock for `("datafusion","clickhouse")` - StarRocks **silently missing** |
| `write_primitives/schema.py:352` `_supports_primary_keys()` | PK predicate | Only excludes `datafusion` - ClickHouse and StarRocks pass as PK-capable |
| `transaction_primitives/schema.py:130` | DDL emit | Unconditional PRIMARY KEY emission - no platform guard |
| `clickhouse/query_transformer.py:47` | Query adapter | `add_subquery_aliases` disabled (regex corrupts Q23/Q87); `joined_subquery_requires_alias=0` session setting used instead |
| `h2odb/benchmark.py:106-141` | Variant selection | `_CLICKHOUSE_Q9`, `_STARROCKS_Q9` class constants replace Q9 wholesale for PERCENTILE syntax |
| `nyctaxi/schema.py`, `tsbs_devops/schema.py` | Schema-gen | `dialect ==` branches mixing type-mapping (legitimate local) with compatibility policy |
| `cli/commands/run.py:804` | Benchmark gate | Reads `caps.unsupported_benchmarks` from PlatformRegistry |
| `vector_search/queries.py` | Query source | `QUERY_VARIANTS` dict - non-sqlglot, non-catalog, platform-version-gated variants |

The net effect: the same capability question (e.g., "does this platform enforce PK uniqueness?") has
three different answers depending on which code path asks, and some answers are silent-corruption
failure modes. StarRocks accepts PRIMARY KEY DDL but silently ignores PKs whose columns are not the
first N columns of the table - a boolean `_supports_primary_keys()` cannot represent this.

---

## Decision

Build a `benchbox/sql_compat/` package that centralizes compatibility decision ownership. The
package defines a typed rule engine that produces `CompatibilityDecision` objects across every
decision point. Existing query-storage shapes (catalog-backed managers, dict-based managers,
`QUERY_VARIANTS`) are **preserved** - the engine plugs in behind each manager as a resolver.
Migration lands behind a `BENCHBOX_COMPAT_REGISTRY` feature flag with a dual-run harness.

### Non-Goals

- **Query-storage format unification** (H2O/CoffeeShop dict managers → catalog): deferred to a
  separate future ADR. The decision engine must work behind the existing managers without requiring
  storage migration.
- sqlglot version upgrade or replacement
- New platform or benchmark additions as part of this change
- Result schema or visualization changes
- Polars/Pandas compatibility beyond DataFrame pipeline-head skip/variant actions

---

## Phase Taxonomy

### Derivation Protocol

The phase list below started as provisional. The inventory tool
(`benchbox/sql_compat/inventory.py`) walks the codebase and classifies every dialect-branching
decision point. The inventory output was reviewed against the provisional taxonomy, and the phase
list was frozen before the engine was scaffolded.

Acceptance criteria for freezing: the inventory must successfully classify at minimum:
- The `benchmark_gate` site at `cli/commands/run.py:804`
- The `QUERY_VARIANTS` / version-gated site at `core/vector_search/queries.py`

### Phase List

| Phase | When it runs | Applicable actions |
|-------|-------------|-------------------|
| `benchmark_gate` | Pre-run preflight, before any execution | `block_benchmark`, `native` |
| `query_source` | Query retrieval - selecting which SQL text to use | `select_variant`, `native` |
| `query_compile` | sqlglot transpile pass | `rewrite_query`, `post_translate`, `native` |
| `query_adapter` | Post-compile per-query platform adjustment | `rewrite_query`, `set_session_policy`, `native` |
| `schema_emit` | DDL generation (`CREATE TABLE`, PK decisions) | `rewrite_ddl`, `native` |
| `ddl_optimize` | Platform-specific DDL transformations | `rewrite_ddl`, `native` |
| `execution_filter` | Per-query skip decision at SQL execution time | `skip_query`, `native` |
| `dataframe_filter` | Per-query skip decision at DataFrame execution time | `skip_query`, `native` |

**Session policy is an action, not a phase.** `set_session_policy` is emitted by a rule in
`query_adapter` phase. It is not promoted to its own phase unless the inventory demonstrates a
case where session-policy decisions must fire independently of query-adapter decisions.

**`post_translate` is an action within `query_compile`**, not a separate phase. Post-processors
applied after sqlglot transpile are part of the compile pass.

**`dataframe_filter` is intentionally distinct from `execution_filter`**: DataFrame-mode skip
decisions share the registry but use a different pipeline head and different action payloads. Two
pipeline heads share the registry; they do not share actions.

---

## Action Vocabulary

Eight actions cover the full decision surface. Every rule produces exactly one action.

| Action | Enum value | Payload fields | Description |
|--------|-----------|---------------|-------------|
| `block_benchmark` | `CompatAction.BLOCK_BENCHMARK` | `reason: str` | Prevent this benchmark from running on this platform |
| `skip_query` | `CompatAction.SKIP_QUERY` | `reason: str`, `query_id: str` | Exclude a query from execution |
| `select_variant` | `CompatAction.SELECT_VARIANT` | `variant_key: str`, `source: str` | Use a pre-written alternative SQL text |
| `rewrite_query` | `CompatAction.REWRITE_QUERY` | `transformer: Callable[[str], str]`, `description: str` | Apply an AST or string transform to the query |
| `rewrite_ddl` | `CompatAction.REWRITE_DDL` | `transformer: Callable[[str], str]`, `description: str` | Apply a transform to a DDL statement |
| `set_session_policy` | `CompatAction.SET_SESSION_POLICY` | `settings: dict[str, Any]`, `issue_url: str \| None` | Emit session settings before query execution; `issue_url` documents why an AST rewrite was not possible |
| `post_translate` | `CompatAction.POST_TRANSLATE` | `transformer: Callable[[str], str]`, `description: str` | Apply a fix after sqlglot transpile (e.g., GROUP BY ALL restoration) |
| `native` | `CompatAction.NATIVE` | - | No modification needed; short-circuits sqlglot for this decision |

`NATIVE` decisions short-circuit sqlglot. `REPLACED` (select_variant) decisions also short-circuit
sqlglot - the variant SQL is already in the target dialect.

---

## Typed Contract

### CompatibilityContext

```python
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal

@dataclass(frozen=True)
class CompatibilityContext:
    platform: str
    platform_version: str | None
    benchmark: str
    query_id: str | None
    phase: Phase
    mode: Literal["sql", "dataframe"]
    dialect: str | None
```

Field notes:

- `platform` is a platform name such as `"starrocks"` or `"clickhouse"`.
- `platform_version` is a version such as `"3.2.1"`, or `None` if not known.
- `benchmark` is a benchmark name such as `"h2odb"` or `"write_primitives"`.
- `query_id` is an ID such as `"Q9"`, or `None` for non-query phases.
- `phase` is one of the `Phase` enum values.
- `dialect` is a sqlglot dialect string, or `None` for non-SQL paths.

### SupportLevel

```python
class SupportLevel(str, Enum):
    NATIVE              = "NATIVE"
    TRANSLATED          = "TRANSLATED"
    REWRITTEN           = "REWRITTEN"
    INFORMATIONAL       = "INFORMATIONAL"
    SKIPPED_QUERY       = "SKIPPED_QUERY"
    SKIPPED_DDL_FRAGMENT = "SKIPPED_DDL_FRAGMENT"
    BLOCKED             = "BLOCKED"
```

- `NATIVE`: works without modification.
- `TRANSLATED`: requires sqlglot translation.
- `REWRITTEN`: requires an additional AST or string rewrite.
- `INFORMATIONAL`: runs, but a platform guarantee is not enforced (renamed from `DEGRADED`).
- `SKIPPED_QUERY`: the query is omitted from the result set.
- `SKIPPED_DDL_FRAGMENT`: auxiliary DDL is suppressed and the workload runs.
- `BLOCKED`: the platform and benchmark combination is unsupported.

> **Addendum (2026-04-26):** `DEGRADED` was renamed to `INFORMATIONAL` and `SKIPPED` was split
> into `SKIPPED_QUERY` / `SKIPPED_DDL_FRAGMENT`. See the Addendum section at the end of this ADR.

### FailureMode

```python
class FailureMode(str, Enum):
    NONE                 = "NONE"
    SYNTAX_ERROR         = "SYNTAX_ERROR"
    SILENT_CORRUPTION    = "SILENT_CORRUPTION"
    UNSUPPORTED_FEATURE  = "UNSUPPORTED_FEATURE"
    PERFORMANCE_REGRESSION = "PERFORMANCE_REGRESSION"
```

`NONE` means no failure is expected. `SILENT_CORRUPTION` means the query runs but returns wrong results (for example, the StarRocks PK case below).

### Typed Action Payloads

`CompatibilityDecision.payload` is a typed union - not `dict[str, Any]` - so that `frozen=True`
is valid (all payload types are frozen and hashable) and downstream code can pattern-match on
payload type instead of string keys. Callables are referenced by `transformer_id` (a registry
lookup key) rather than inline functions, which keeps payloads serializable.

```python
from __future__ import annotations
from dataclasses import dataclass
from typing import Union

@dataclass(frozen=True)
class BlockBenchmarkPayload:
    reason: str

@dataclass(frozen=True)
class SkipQueryPayload:
    reason: str
    query_id: str

@dataclass(frozen=True)
class SelectVariantPayload:
    variant_key: str
    variant_sql: str

@dataclass(frozen=True)
class RewriteQueryPayload:
    transformer_id: str
    description: str

@dataclass(frozen=True)
class RewriteDDLPayload:
    transformer_id: str
    description: str

@dataclass(frozen=True)
class SetSessionPolicyPayload:
    settings: tuple[tuple[str, str], ...]
    issue_url: str | None

@dataclass(frozen=True)
class PostTranslatePayload:
    transformer_id: str
    description: str

CompatPayload = Union[
    BlockBenchmarkPayload,
    SkipQueryPayload,
    SelectVariantPayload,
    RewriteQueryPayload,
    RewriteDDLPayload,
    SetSessionPolicyPayload,
    PostTranslatePayload,
    None,
]
```

Field notes:

- `SelectVariantPayload.variant_key` is a key into the benchmark's variant dict (for example `"clickhouse"`), and `variant_sql` is the SQL text already in the target dialect.
- `transformer_id` is a registry key that resolves to a `Callable[[str], str]` at runtime.
- `SetSessionPolicyPayload.settings` holds key-value pairs emitted before the query, and `issue_url` links to documentation of why an AST rewrite is not viable.
- `None` is the `NATIVE` action's payload, because it carries no payload.

### CompatibilityDecision

```python
@dataclass(frozen=True)
class CompatibilityDecision:
    rule_id: str
    action: CompatAction
    support_level: SupportLevel
    failure_mode: FailureMode
    payload: CompatPayload
    reason: str
```

`rule_id` has the form `"{phase}.{platform}.{scope}.{slug}"`. `payload` is typed per action, and is `None` for `NATIVE`. `reason` is a human-readable rationale.

### Structured Capability Example - StarRocks PK

Capabilities are **not** bare booleans. The StarRocks PK rule is the canonical example of why:
StarRocks silently ignores PKs whose columns are not the first N columns of the table.

```python
CompatibilityDecision(
    rule_id="schema_emit.starrocks.pk.first_n_columns_only",
    action=CompatAction.REWRITE_DDL,
    support_level=SupportLevel.INFORMATIONAL,
    failure_mode=FailureMode.SILENT_CORRUPTION,
    payload=RewriteDDLPayload(
        transformer_id="starrocks.pk_reorder_or_drop",
        description=(
            "Reorders PK columns to be first N in the table or drops the PK "
            "if reordering is not possible. StarRocks accepts PRIMARY KEY DDL "
            "but silently ignores PKs that violate the first-N-columns rule."
        ),
    ),
    reason=(
        "StarRocks accepts PRIMARY KEY DDL but silently ignores PKs unless "
        "the PK columns are the first N columns of the table. A boolean "
        "pk.uniqueness_enforced cannot express this condition."
    ),
)
```

---

## rule_id Format

```
{phase}.{platform}.{scope}.{slug}
```

| Field | Constraints | Examples |
|-------|-------------|---------|
| `phase` | Lowercase phase name | `schema_emit`, `query_source`, `benchmark_gate` |
| `platform` | Platform key (lowercase) | `starrocks`, `clickhouse`, `datafusion` |
| `scope` | Benchmark name or capability area | `h2odb`, `write_primitives`, `pk`, `tpch` |
| `slug` | Snake-case description of the rule | `first_n_columns_only`, `q9_percentile_syntax`, `lock_bypass` |

Examples:
- `schema_emit.starrocks.pk.first_n_columns_only`
- `query_source.clickhouse.h2odb.q9_percentile_syntax`
- `query_source.starrocks.h2odb.q9_percentile_syntax`
- `schema_emit.starrocks.write_primitives.pk_lock_table_unsupported`
- `schema_emit.datafusion.write_primitives.pk_lock_table_unsupported`
- `query_adapter.clickhouse.tpch.subquery_alias_session_policy`

Note: the write_primitives lock-bypass at `benchmark.py:171` is a DDL operation (creating a lock
table with a PRIMARY KEY column) performed during setup - it is a `schema_emit` decision, not a
`benchmark_gate` one. `benchmark_gate` is reserved for pre-run blocking of an entire
platform×benchmark combination before any execution begins.

---

## @compat_local Decorator

The `@compat_local` decorator is an attribution mechanism so the inventory linter can distinguish
**legitimate platform-specific rendering** (type mapping, storage layout) from unregistered
compatibility policy. It does not suppress compatibility decisions - it exempts a callable from the
lint rule that requires all dialect branches inside `benchbox/core/` or `benchbox/benchmarks/` to
be registered.

```python
def compat_local(
    kind: Literal["type_mapping", "storage_layout", "rendering"],
    platform_specific: bool,
    reason: str,
) -> Callable:
    pass
```

`compat_local` marks a callable as containing legitimate local platform-specific rendering. It exempts the callable from the `compat_lint` "unregistered dialect branch" rule, and it does not register a rule in the registry. The arguments are:

- `kind`: category of local rendering (`type_mapping`, `storage_layout` or `rendering`).
- `platform_specific`: `True` if the branch is specific to one platform.
- `reason`: why this is legitimate local rendering, not unregistered policy.

### When to Use @compat_local vs Register a Rule

| Situation | Use |
|-----------|-----|
| Type mapping (`INT` → `INT32`, `FLOAT` → `DOUBLE`) | `@compat_local(kind="type_mapping")` |
| Storage layout (columnar store specifics, engine selection) | `@compat_local(kind="storage_layout")` |
| Identifier rendering (quoting style, case folding) | `@compat_local(kind="rendering")` |
| Query behavior difference (`GROUP BY ALL` → keyword restore) | Register in registry |
| PK enforcement semantics | Register in registry |
| Query skip or variant selection | Register in registry |
| DDL correctness (drop FK, reorder PK cols) | Register in registry |

### Granularity Rule

`@compat_local` exempts **all** dialect branches within the decorated callable. This means:

- **Apply at the narrowest scope possible.** Decorate a helper that only does type mapping, not a
  top-level function that mixes type mapping with a PK-policy branch. If a function contains both a
  legitimate local branch and an unregistered compatibility-policy branch, split it into two
  callables before applying the decorator.
- **Mixed-branch functions are a lint error even if decorated.** If a `@compat_local`-decorated
  function contains a branch that matches a `kind` not covered by the decorator arguments (e.g., a
  `schema_emit`-style branch inside a `kind="type_mapping"` function), the linter must flag it as
  a misuse of the decorator. The decorator's `kind` is an assertion, not a blanket exemption.

### Linter Attribution Rule

The lint rule (`scripts/compat_lint.py`) fires when: a dialect-branch (`if dialect`,
`if "platform" in dialect`, etc.) appears inside `benchbox/core/` or `benchbox/benchmarks/` AND
the containing callable is not `@compat_local`-decorated (with a matching `kind`) AND has no
registered rule for the detected platform × phase combination.

The linter runs in **error mode** (exit 1) permanently.

---

## Baseline Schema v1

The baseline artifact (`baseline.v1.jsonl`, written by `benchbox/sql_compat/baseline_tool.py`) records the pre-migration behavior of
every (platform, benchmark, query_id, phase) tuple that has a compatibility decision. It is the
parity baseline for dual-run shadow-mode divergence review.

### Record Shape

```json
{
  "schema_version": 1,
  "platform": "starrocks",
  "benchmark": "h2odb",
  "query_id": "Q9",
  "phase": "query_source",
  "mode": "sql",
  "source_sql_hash": "sha256:abc123...",
  "decision": "REWRITTEN",
  "rule_id": null,
  "final_sql_hash": "sha256:def456...",
  "benchmark_gate_outcome": null
}
```

Fields:
- `schema_version`: integer; increment when the shape changes
- `source_sql_hash`: SHA-256 of the query text before any compatibility decision
- `decision`: one of the `SupportLevel` enum values
- `rule_id`: null in the baseline (no rules exist yet); populated after registry lands
- `final_sql_hash`: SHA-256 of the query text after decisions; null for non-SQL phases
- `benchmark_gate_outcome`: `"allowed"` | `"blocked"` | null (only for `benchmark_gate` phase)

### Version-Bump Protocol

When the baseline schema changes:
1. Rename old file: `baseline.v{N}.jsonl` → archived
2. Create `baseline.v{N+1}.jsonl` with the new shape
3. Update `baseline_tool.py` to emit the new version
4. Update the dual-run harness to consume the new version
5. No old-version file is deleted while the dual-run harness exists (it has since been removed)

### Refresh Protocol

The baseline is regenerated whenever:
- A new platform is added to the test suite
- A new benchmark is onboarded that has compatibility decisions
- A rule is registered that changes an existing decision's `SupportLevel`

The `baseline_tool.py` must be idempotent (same inputs → same outputs) so CI can regenerate and
diff to detect drift.

---

## CLI Preflight (benchmark_gate phase)

`benchmark_gate` decisions are resolved at CLI preflight inside
`_check_benchmark_platform_compatibility()` (`cli/commands/run.py:804`), **before** the SQL or
DataFrame pipeline runs. This is the only place where `block_benchmark` fires.

```
CLI run command
    │
    └── _check_benchmark_platform_compatibility()
            │
            └── Resolver(CompatibilityContext(phase=benchmark_gate, ...))
                    │
                    ├── BLOCK_BENCHMARK → print error, exit(1)
                    └── NATIVE          → continue to execution
```

The `caps.unsupported_benchmarks` field on `PlatformCapability` becomes a view computed from
registry `benchmark_gate` rules.

## SQL Pipeline Head

The SQL pipeline head sits at `platforms/base/execution.py` (query execution entry point). It
receives a `CompatibilityContext` and consults the registry before running a query. It handles
query-execution phases only (`query_source`, `query_compile`, `query_adapter`,
`execution_filter`); `benchmark_gate` is handled at CLI preflight above.

```
┌──────────────┐    CompatibilityContext     ┌─────────────┐
│ execution.py │ ──────────────────────────▶ │   Resolver  │
│ (SQL head)   │ ◀── CompatibilityDecision── │             │
└──────────────┘                             └─────────────┘
        │
        ├── SKIP_QUERY       → skip, log
        ├── SELECT_VARIANT   → use variant_sql from payload, bypass sqlglot
        ├── SET_SESSION_POLICY → emit settings, then run query
        ├── REWRITE_QUERY    → apply transformer (via transformer_id), then run
        ├── POST_TRANSLATE   → apply transformer after sqlglot pass
        └── NATIVE           → run original SQL unchanged
```

Actions that apply at the SQL pipeline head: `skip_query`, `select_variant`, `set_session_policy`,
`rewrite_query`, `post_translate`.

The existing `get_platform_skip_queries()` function becomes a thin facade over the registry,
kept for backwards compatibility.

---

## DataFrame Pipeline Head

The DataFrame pipeline head sits at `platforms/dataframe/benchmark_mixin.py`. It shares the same
registry but consults it with `mode="dataframe"` in the context. Only `skip_query` and
`select_variant` actions apply; `rewrite_query`, `set_session_policy`, and `post_translate` do not
apply to DataFrame paths (Polars/Pandas retain existing code paths).

The existing `get_df_platform_skip_queries()` function becomes a thin facade over the registry,
kept for backwards compatibility.

---

## Specificity Precedence for Rule Resolution

When multiple rules match the same `CompatibilityContext`, the most specific rule wins:

1. `platform + benchmark + query_id` (most specific)
2. `platform + benchmark`
3. `platform` (most general)

Within the same specificity tier:
- Version-gated rules (rules with a `platform_version` constraint satisfying
  `context.platform_version`) take precedence over rules without a version constraint.
- If `context.platform_version is None`, no version-gated rule fires for that context; the
  non-versioned rule at the same tier applies. This is the explicit fallback: unknown version →
  non-versioned behavior, never version-gated.

If two rules at the same specificity level produce conflicting actions for the same context, it is
a registry error and the resolver raises `CompatibilityRegistryConflict`.

### Multi-Action Composition

**A query may require multiple compatibility actions in a single execution pass - one per phase.**
The pipeline calls the resolver independently at each phase, so actions compose sequentially:

```
query_source    → SELECT_VARIANT (pick H2O Q9 ClickHouse constant)
query_compile   → NATIVE (variant is already in target dialect, skip sqlglot)
query_adapter   → SET_SESSION_POLICY (joined_subquery_requires_alias=0)
execution_filter → NATIVE
```

Within a single phase, **at most one action fires** per (platform, benchmark, query_id) tuple.
If two rules at the same specificity level match the same phase context, it is a registry error
(raised as `CompatibilityRegistryConflict`).

The composition rule means there is never a need for a "combined" rule that does both a rewrite and
a session policy in one action - split them across `query_compile` and `query_adapter` instead.

---

## CompilationPlan

The resolver pre-computes a `CompilationPlan` at run start for each (platform, benchmark) pair and
caches it in the benchmark execution context. Warmup, power, and throughput phases reuse the same
plan without re-querying the registry.

```python
from __future__ import annotations
from dataclasses import dataclass, field

@dataclass(frozen=True)
class PhasedDecision:
    query_id: str | None
    phase: Phase
    decision: CompatibilityDecision

@dataclass
class CompilationPlan:
    platform: str
    benchmark: str
    decisions: dict[tuple[str | None, Phase], CompatibilityDecision] = field(default_factory=dict)

    def get(self, query_id: str | None, phase: Phase) -> CompatibilityDecision | None:
        return self.decisions.get((query_id, phase)) or self.decisions.get((None, phase))
```

`PhasedDecision` is one resolved decision keyed by `(query_id | None, Phase)`. In `CompilationPlan.decisions`, the outer key is the query ID (`None` applies to all queries in the phase), the inner key is the `Phase`, and the value is the winning `CompatibilityDecision` after specificity resolution. `get` returns the decision for `(query_id, phase)`, falling back to `(None, phase)`.

`CompilationPlan` is not frozen because it is populated incrementally by the resolver during plan
construction. Once the resolver hands it to the benchmark runner it must not be mutated; the
runner enforces this by not retaining a reference to the resolver.

## Dual-Run Harness (deleted - registry is authoritative)

The dual-run harness (`benchbox/sql_compat/harness.py`) was deleted at cutover after zero
divergence was confirmed across all registered rules. Feature flag `BENCHBOX_COMPAT_REGISTRY` is
also removed. The registry is now the sole source of truth.

The harness operated in three modes; preserved below for historical reference only:

Feature flag: `BENCHBOX_COMPAT_REGISTRY={off|shadow|on}`. The default moved from `off` to `shadow`
once the harness existed, and to `on` at cutover.

| Mode | Behavior |
|------|---------|
| `off` | Legacy path only. Registry not consulted. |
| `shadow` | Both legacy and registry paths run. Legacy decision is acted on. Registry decision is logged. Divergences written to a local divergence log. |
| `on` | Registry is authoritative. Legacy path disabled. |

### Divergence Log Shape

```json
{
  "rule_id": "schema_emit.starrocks.write_primitives.pk_lock_table_unsupported",
  "platform": "starrocks",
  "benchmark": "write_primitives",
  "query_id": null,
  "phase": "schema_emit",
  "legacy_outcome": "NATIVE",
  "registry_outcome": "REWRITE_DDL"
}
```

The `rule_id` prefix must always match the `phase` field - both reflect the
`CompatibilityContext.phase` used when the resolver was called. A null `rule_id` is valid when the
registry has no matching rule (registry outcome = "no_decision").

Cut-over to `on` mode is gated on divergence reaching **zero** for each migrated site's rule_ids across a
full shadow-mode pass before that site's migration is merged.

---

## Migration Sequence

1. Record this decision, run the inventory and freeze the phase taxonomy, then take the baseline
   snapshot.
2. In parallel with the baseline: add the `@compat_local` decorator and a warn-only linter.
3. Scaffold the `benchbox/sql_compat/` package with no rules.
4. Add the dual-run harness and feature flag.
5. Migrate each decision site behind the harness; merge each only after zero divergence.
6. Cut over: the registry becomes authoritative, and the harness and feature flag are deleted.
7. Add the doc generator and capability matrix.

---

## DDL Centralization

This section documents the DDL centralization scope, resolves the Fabric Warehouse open question,
and records the adapter migration inventory.

### Outcome

- Phase enum frozen: `schema_emit`, `ddl_optimize` cover all DDL ownership. No `ddl_compile` phase
  was added.
- `CompatibilityContext` shape unchanged; no DDL-specific fields added.
- Rule modules live in `benchbox/sql_compat/rules/{phase}/`; no separate transformers tree.
- All 13 adapter platforms have `ddl_optimize` rules registered (see
  `benchbox/sql_compat/rules/ddl_optimize/`). The adapter's own `_optimize_table_definition()`
  method is the rule implementation; the registry entry serves governance and lint purposes.
- `schema_emit` rules cover nyctaxi and tsbs_devops.
- `platform_transform_fn` callback removed from `base/data_loading.py`; Databricks now
  applies `_convert_to_delta_table` in a pre-pass loop before calling `_execute_schema_statements`.
- `transaction_primitives` StarRocks PK rule added (`schema_emit.starrocks.transaction_primitives.pk_lock_table_unsupported`); StarRocks and Doris were absent from the legacy tuple - now documented as registry rules.
- `write_primitives/_supports_primary_keys()` legacy hardcoded platform set removed; fallback is
  now `return True` (no rule → supported), consistent with `transaction_primitives`.
- compat_lint runs in permanent error mode (exit 1 on any unregistered dialect branch in `benchbox/core/`); currently 0 violations.

### Adapter Migration Inventory

All 13 adapter platforms have `ddl_optimize` rules registered. Rule files in
`benchbox/sql_compat/rules/ddl_optimize/`. StarRocks was the reference implementation
established during the original pipeline cutover; the other 12 adapters were migrated as part of
DDL centralization.

| Adapter | File(s) | Hook | DDL concern |
|---------|---------|------|-------------|
| ClickHouse | `platforms/clickhouse/workload.py` | `translate_ddl` layout (`_translate_schema_statement`) | Nullable only for listed columns, ENGINE=MergeTree(), PARTITION BY / ORDER BY / PRIMARY KEY from the tuned layout |
| Databricks | `platforms/databricks/adapter.py` | `_convert_to_delta_table` (pre-pass loop) | CREATE OR REPLACE, USING DELTA, TBLPROPERTIES (config-dependent) |
| Databend | `platforms/databend/adapter.py` | `_optimize_table_definition` | Databend-specific DDL dialect |
| Firebolt | `platforms/firebolt.py` | `_optimize_table_definition` | Firebolt DDL dialect |
| Redshift | `platforms/redshift.py` | `translate_ddl` | Redshift DDL dialect |
| Azure Synapse | `platforms/azure_synapse.py` | `_optimize_table_definition` | Synapse DDL dialect |
| Snowflake | `platforms/snowflake.py` | `translate_ddl` | Snowflake DDL dialect (clustering/tuning) |
| Presto | `platforms/presto_trino_adapter_base.py` | `_schema_ddl` (`translate_ddl`) | Presto DDL dialect |
| Trino | `platforms/presto_trino_adapter_base.py` | `_schema_ddl` (`translate_ddl`) | Trino DDL dialect |
| Spark | `platforms/spark.py` | `_optimize_table_definition` | USING DELTA/ICEBERG/ORC/PARQUET (format from `self.table_format`) |
| Lakesail | `platforms/lakesail.py` | `_optimize_table_definition` | Lakesail DDL dialect |
| Velox | `platforms/velox.py` | `_optimize_table_definition` | USING ORC/PARQUET (format from `self.table_format`) |
| StarRocks | `platforms/starrocks/workload.py` | `_optimize_table_definition` | Strip AUTO_INCREMENT/FK, reconstitute PK as DUPLICATE KEY or PRIMARY KEY model, add DISTRIBUTED BY HASH |

Fabric Warehouse is **not** in this list - see resolution below.

**Presto/Trino invocation note**: `PrestoTrinoAdapterBase.create_schema` builds the statements with
`_schema_ddl`, which calls `translate_ddl`, and passes the finished list to the standalone
`execute_schema_statements()` helper.

### Fabric Warehouse Decision: Operational, Not a DDL Compat Rule

`FabricWarehouseAdapter._optimize_table_definition` injects a schema-qualified table name using
`self.schema` (runtime adapter configuration, not SQL content). The operation is:

```python
table_sql = table_sql.replace(
    f"CREATE TABLE {table_name}",
    f"CREATE TABLE [{self.schema}].[{table_name}]",
)
```

This is **operational context injection**, not a DDL compatibility rewrite:
- It depends on `self.schema`, which is adapter state set from connection configuration.
- A pure `Callable[[str], str]` cannot express this without closure capture of adapter config.
- The decision mirrors how catalog/schema setup is handled in other adapters: it stays in
  `create_schema()` as an operational responsibility.

**Decision**: Fabric Warehouse's `_optimize_table_definition` is NOT migrated to a `ddl_optimize`
registry rule. It remains adapter-local operational logic. No `RewriteDDLPayload` extension is
needed.

### Config-Dependent Transforms (Databricks, Spark, Velox)

Some DDL hooks depend on adapter config:
- **Databricks** `_convert_to_delta_table`: `TBLPROPERTIES` block conditional on `self.delta_auto_optimize`
- **Spark** `_optimize_table_definition`: `USING DELTA|ICEBERG|ORC|PARQUET` selected by `self.table_format`
- **Velox** `_optimize_table_definition`: `USING ORC|PARQUET` selected by `self.table_format`

For shadow-harness purposes this is acceptable: the `harness_resolve()` call verifies that the
registry knows a DDL rewrite is required for this platform, not that the transformer output matches.
The transformer itself (`transformer_id` lookup) is not invoked during shadow mode. Full
transformer parity (verifying the rule's transformer produces identical output to the legacy hook)
was deferred to the cutover to `on` mode.

### The `platform_transform_fn` Callback

- All adapters except Fabric Warehouse have `ddl_optimize` registry rules (see above).
- `platform_transform_fn` is removed from `benchbox/platforms/base/data_loading.py`. Databricks
  applies `_convert_to_delta_table` in a statements pre-pass before calling
  `_execute_schema_statements`.

---

## Consequences

### Positive

- Every compatibility decision is auditable: rule_id, support_level, failure_mode, and reason are
  all first-class fields.
- Silent-failure modes (StarRocks PK first-N-columns) are expressed structurally, not as comments.
- New platform/benchmark additions acquire compatibility rules in one place.
- Generated capability matrix documents platform support levels for users.
- Linter in error mode prevents future drift.

### Negative / Risks

- Large surface area: about 15 files. Mitigated by feature flag + dual-run harness.
- Snapshot sign-off for translation entrypoint unification requires per-adapter diff
  review; cannot be auto-approved.
- The inventory tool may surface additional decision sites not listed in the Context table,
  expanding scope. New sites must be explicitly triaged before they are migrated.

---

## Addendum: Taxonomy Refinement (2026-04-26)

### Context

After the pipeline and its generated docs shipped (2026-04-26), a review of the
eight skip/block/degrade rows in `docs/compat/skip-reference.md` surfaced five problems: two naming
gaps that bias readers toward wrong interpretations, one overloaded enum value, one doc-structure
gap, and one silent-corruption window the pipeline was designed to close but had not fully closed.

### Decisions

#### (a) PK Coverage Policy

Every BenchBox platform that can host `write_primitives` or `transaction_primitives` MUST have a
registered PK-capability rule in `benchbox/sql_compat/rules/schema_emit/`. The rule MUST classify
into one of the four buckets defined below. The `legacy_bypass` fallback tuples in
`core/write_primitives/benchmark.py` and `core/transaction_primitives/benchmark.py` are deleted. No platform may rely on the fallback path.

A parity test in `tests/unit/sql_compat/test_pk_coverage_parity.py` enforces this invariant:
every platform in the write_primitives-supported set must resolve to a non-None PK rule.

#### (b) Revised SupportLevel Taxonomy for PK / Skip Rules

The original six-value `SupportLevel` enum is refined as follows. Values not listed here are
unchanged (`NATIVE`, `TRANSLATED`, `REWRITTEN`, `BLOCKED`).

| Old value | New value | Semantics |
|-----------|-----------|-----------|
| `DEGRADED` | `INFORMATIONAL` | Workload runs end-to-end, but a documented platform-level guarantee (typically PK uniqueness) is not provided. Named to match vendor terminology (Snowflake, Redshift, BigQuery, Databricks docs use this word). Removes the false suggestion of performance regression. |
| `SKIPPED` | `SKIPPED_QUERY` | A specific query is omitted from the result set entirely. User-visible: result count decreases. |
| `SKIPPED` | `SKIPPED_DDL_FRAGMENT` | An auxiliary DDL statement (e.g., a PK lock-table CREATE) is suppressed, but the workload itself runs end-to-end. Not user-visible in result counts. |

#### (c) Rationale for DEGRADED → INFORMATIONAL

`DEGRADED` carries performance-regression connotations. The actual semantic - "the workload runs,
but a documented platform-level guarantee is not enforced" - is how cloud DW vendors document their
own behavior: Snowflake calls it "INFORMATIONAL constraints", Redshift calls them "INFORMATIONAL"
in its `ALTER TABLE` docs, BigQuery's DDL reference uses the same word, Databricks Delta Lake
documentation calls unenforced PKs "informational". Renaming to `INFORMATIONAL` aligns BenchBox
with vendor documentation and makes the capability matrix immediately legible to readers with DW
fluency.

#### (d) SupportLevel Migration Table

| Rule | Old value | New value | Notes |
|------|-----------|-----------|-------|
| `schema_emit.starrocks.*.pk_lock_table_unsupported` (×2) | `DEGRADED` | `INFORMATIONAL` | StarRocks accepts PK DDL but does not enforce uniqueness |
| `schema_emit.doris.*.pk_lock_table_unsupported` (×2) | `DEGRADED` | `INFORMATIONAL` | Doris DUP_KEYS model; PK DDL accepted but not enforced |
| `schema_emit.datafusion.*.pk_lock_table_unsupported` (×2) | `SKIPPED` | `SKIPPED_DDL_FRAGMENT` | DataFusion rejects PK syntax at parse time; workload runs without the lock table |
| `query_source.starrocks.vector_search.q2_lt_32_skip` | `SKIPPED` | `SKIPPED_QUERY` | Q2 omitted from result set on StarRocks <3.2 |

#### (e) Reason-String Convention

Rule `reason` fields must lead with the user-visible consequence, then state the platform-specific
cause. An approved set of consequence prefixes is defined in
`benchbox/sql_compat/_reason_conventions.py` and enforced (warn-only) by `scripts/compat_lint.py`.

Approved prefixes:

| Prefix | Used with |
|--------|-----------|
| `"Benchmark blocked at preflight."` | `BLOCKED` rules |
| `"Query <id> omitted from results."` | `SKIPPED_QUERY` rules |
| `"Workload runs; <auxiliary DDL> is suppressed."` | `SKIPPED_DDL_FRAGMENT` rules |
| `"Workload runs; <semantic gap> is not enforced."` | `INFORMATIONAL` rules |

The lint check is warn-only. It moves to error mode once all existing rules comply.

#### (f) Doc Restructure Plan for skip-reference.md

The generator (`scripts/generate_compat_docs.py: _render_skip_reference`) is restructured to
partition the skip reference into two top-level sections:

- **"Will not run"**: `BLOCKED` + `SKIPPED_QUERY` rows - outcomes that remove a query or benchmark
  from the result set. Users observe these as missing entries in their result counts.
- **"Runs with caveats"**: `SKIPPED_DDL_FRAGMENT` + `INFORMATIONAL` rows - the workload completes
  end-to-end, but with documented internal gaps (suppressed DDL, unenforced semantic guarantees).

#### (g) NoConstraintEnforcementMixin Cross-Check Policy

Every platform adapter that inherits from
`benchbox.platforms.base.no_constraint_mixin.NoConstraintEnforcementMixin` is in-tree evidence
that PK uniqueness is not enforced at runtime (the mixin no-ops `apply_constraint_configuration`).
Such adapters MUST resolve to a PK rule with `support_level` of `INFORMATIONAL` or
`SKIPPED_DDL_FRAGMENT` - NEVER `NATIVE`. Registering a `NATIVE` PK rule for a mixin user would
be a registry/adapter parity bug: the registry would claim PK is enforced while the runtime
explicitly skips enforcement.

Current mixin users (as of 2026-04-26): `DataFusionAdapter`, `PolarsAdapter`, `CuDFAdapter`.
DataFusion classifies as `SKIPPED_DDL_FRAGMENT` (parser refuses PK DDL). Polars and cuDF are
DataFrame platforms; they are out of scope for SQL-mode `write_primitives` /
`transaction_primitives`, but if either platform ever runs SQL-mode primitives, the rule must be
`INFORMATIONAL` (not `NATIVE`).

A structural parity assertion in `test_pk_coverage_parity.py::test_no_constraint_mixin_parity`
enforces this invariant across future platform additions.
