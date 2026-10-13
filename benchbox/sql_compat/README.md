# sql_compat — Phase-Aware SQL Compatibility Registry

## Overview

`sql_compat` is the governance layer for every SQL-text or DDL transformation
BenchBox performs on behalf of a specific platform.  Instead of silently
rewriting queries and DDL in adapter code, each intentional transformation is
**registered** in the registry with a `rule_id`, an action type, and a reason.
`compat_lint` walks the rule files at CI time and fails if any transformation
is found in adapter code that has no matching registry entry.

## Design: hybrid governance plus optional runtime dispatch

The registry is the authoritative governance catalogue for SQL compatibility
decisions.  `BaseDdlOptimizer` is the runtime dispatch path for adapters that
opt in; adapters that keep a local rewrite path must still register the behavior
with `RewriteDDLPayload(governance_only=True)` unless the rewrite has an
explicit drift exemption.

Adapter methods remain in `benchbox/platforms/<platform>.py` when the
transformation is tightly coupled to platform state, SDK load loops, or
deployment settings (SHARD KEY syntax, EXTERNAL TABLE locations, schema
prefixes, etc.) and are tested alongside the adapter.  The registry makes those
decisions discoverable, auditable, and enforceable without forcing every
platform through one runtime abstraction.

```
Registry rule     →  declares transformer_id "myplatform_strip_fk"
Adapter method    →  implements def myplatform_strip_fk(self, stmt): ...
BaseDdlOptimizer  →  dispatches each rule to getattr(self, transformer_id)(stmt)
compat_lint       →  verifies every regex/DDL-text branch in adapter code
                     maps to a registered rule_id
```

Decision rules:

- Every detected adapter CREATE TABLE rewrite must be registered under
  `Phase.DDL_OPTIMIZE`, runtime-dispatched by `BaseDdlOptimizer`, or explicitly
  exempted with rationale.
- `governance_only=True` is allowed for runtime behavior that remains in an
  adapter-specific path; it means the rule is authoritative documentation and a
  CI governance anchor, not a dispatch target.
- Local rewrites outside `BaseDdlOptimizer` are allowed when they need adapter
  state or SDK-specific load/create loops, but they must have representative
  runtime tests and a governance rule.
- `compat_lint CLEAN` means no source-detected CREATE TABLE rewrite is
  unregistered or uninspectable. It does not mean every DDL rewrite is routed
  through `BaseDdlOptimizer`, nor does it prove semantic equivalence of the
  rewrite output.

## Phase.DDL_OPTIMIZE — adding a new platform

1. **Register** a `CompatibilityDecision` in
   `benchbox/sql_compat/rules/ddl_optimize/<platform>_ddl_rewrites.py`:

   ```python
   from benchbox.sql_compat.registry import REGISTRY
   from benchbox.sql_compat.context import Phase
   from benchbox.sql_compat.decision import (
       CompatibilityDecision, CompatAction, SupportLevel, FailureMode,
       RewriteDDLPayload,
   )

   REGISTRY.register(
       CompatibilityDecision(
           rule_id="ddl_optimize.myplatform.all.strip_foreign_keys",
           action=CompatAction.REWRITE_DDL,
           support_level=SupportLevel.REWRITTEN,
           failure_mode=FailureMode.SYNTAX_ERROR,
           payload=RewriteDDLPayload(
               transformer_id="myplatform_strip_foreign_keys",
               description="Remove FK clauses unsupported by MyPlatform.",
           ),
           reason="MyPlatform raises error XYZ on FOREIGN KEY clauses.",
       ),
       Phase.DDL_OPTIMIZE,
       "myplatform",
   )
   ```

   `optimize_table_definition()` is inherited, and transformers are dispatched automatically in registration order, so no manual wiring is needed.

2. **Implement** the method in your adapter. Prefer `BaseDdlOptimizer` for new
   platforms when the transform can be expressed as ordered statement-to-
   statement functions:

   ```python
   from typing import ClassVar
   from benchbox.platforms.base.ddl_helpers import strip_foreign_keys
   from benchbox.platforms.base.ddl_optimizer import BaseDdlOptimizer

   class MyPlatformAdapter(BaseDdlOptimizer, PlatformAdapter):
       _platform_key: ClassVar[str] = "myplatform"

       def myplatform_strip_foreign_keys(self, stmt: str) -> str:
           return strip_foreign_keys(stmt)

   ```

   If the adapter must keep a local create/load path instead, set
   `governance_only=True` on the payload and name the local method in the rule
   `reason`.

3. **Confirm** `compat_lint` runs clean:

   ```bash
   uv run -- python -m benchbox.sql_compat.inventory --check-ddl-drift
   ```

## BaseDdlOptimizer — automatic dispatch

`BaseDdlOptimizer` (in `benchbox/platforms/base/ddl_optimizer.py`) is a mixin
that adapters can inherit to get automatic dispatch.  On the first call to
`optimize_table_definition(stmt, table_name)` it:

1. Lazily imports `benchbox.sql_compat.rules.ddl_optimize.<platform_key>_ddl_rewrites`
   to ensure rules are registered (the registry is idempotent on re-import).
2. Calls `REGISTRY.resolve_all(ctx)` to get all registered rules in
   registration order.
3. For each rule's `RewriteDDLPayload.transformer_id`, calls
   `getattr(self, transformer_id)(stmt)` and passes the result to the
   next transformer.

**Registration order is application order.**  If one transform depends on
another (e.g., `SHARD KEY` skip-guard needs `REFERENCE TABLE` to already be
in the stmt), register the prerequisite rule first.

## Canonical example: SingleStore

SingleStore has four DDL_OPTIMIZE rules applied in this order:

| # | rule_id suffix                    | transformer_id                    | What it does |
|---|-----------------------------------|-----------------------------------|--------------|
| 1 | `strip_foreign_keys`              | `singlestore_strip_foreign_keys`  | Removes FK clauses (error 2752) |
| 2 | `reference_table_for_dimensions`  | `singlestore_reference_table`     | `CREATE TABLE` → `CREATE REFERENCE TABLE` for nation/region |
| 3 | `inject_shard_key`                | `singlestore_inject_shard_key`    | Injects `SHARD KEY` (skips `REFERENCE TABLE`) |
| 4 | `inject_sort_key`                 | `singlestore_inject_sort_key`     | Injects `SORT KEY` (skips `REFERENCE TABLE`) |

Rule file: `benchbox/sql_compat/rules/ddl_optimize/singlestore_ddl_rewrites.py`
Adapter:   `benchbox/platforms/singlestore.py` — `SingleStoreAdapter` inherits `BaseDdlOptimizer`

## DDL helper input constraint

`strip_primary_keys` and `strip_foreign_keys` are text transformations for
benchmark `CREATE TABLE` statements. Primary-key removal handles inline,
table-level, named, and composite constraints, including nested expressions
in the column list. Both helpers clean trailing commas and return the original
statement when no matching constraint is present. They are not SQL string-literal parsers:
an occurrence of a constraint phrase inside a quoted literal can be mistaken
for a clause and removed. Callers must apply them only to schema statements
whose literals do not contain the relevant constraint phrase. The benchmark
schema emitters satisfy that constraint.

## Multi-rule platforms and resolve() vs resolve_all()

Platforms with **multiple** rules at the same `(phase, platform)` key (like
SingleStore) will cause `REGISTRY.resolve()` to raise
`CompatibilityRegistryConflict`.  Use `REGISTRY.resolve_all(ctx)` for
governance queries on these platforms.

`BaseDdlOptimizer` uses `resolve_all()` internally and is safe for any number
of rules.

## compat_lint enforcement

`benchbox/sql_compat/inventory.py` walks platform source files and flags DDL
rewrite behavior that lacks a matching registry rule. The drift check reports
detected behavior in five buckets:

| Status | Meaning |
|---|---|
| `registered_runtime_behavior` | A registered `DDL_OPTIMIZE` rule dispatches at runtime through `BaseDdlOptimizer`. |
| `registered_governance_only_intent` | The adapter performs the rewrite locally and the registry rule documents/enforces that intent. |
| `explicit_exemption` | The local rewrite is intentionally outside the registry and carries an exemption rationale. |
| `unregistered_detected_behavior` | Source inspection found CREATE TABLE rewrite behavior with no rule or exemption; CI fails. |
| `unknown_uninspectable_behavior` | Source inspection could not safely inspect a platform file; CI fails rather than claiming clean. |

`make compat-docs-check` runs the generated compatibility-doc drift check and
`uv run -- python -m benchbox.sql_compat.inventory --check-ddl-drift`, and the
PR workflow runs that target in the code lint job.
