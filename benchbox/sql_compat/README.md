# sql_compat — Phase-Aware SQL Compatibility Registry

## Overview

`sql_compat` is the governance layer for every SQL-text or DDL transformation
BenchBox performs on behalf of a specific platform.  Instead of silently
rewriting queries and DDL in adapter code, each intentional transformation is
**registered** in the registry with a `rule_id`, an action type, and a reason.
`compat_lint` walks the rule files at CI time and fails if any transformation
is found in adapter code that has no matching registry entry.

## Design: governance catalogue over the fork's DDL transpiler

The registry is the authoritative governance catalogue for SQL compatibility
decisions.  DDL rewriting is performed by the SQLGlot fork through
`benchbox.utils.dialect_utils.translate_ddl`; each rewrite is registered with
`RewriteDDLPayload(governance_only=True)` unless the rewrite has an explicit
drift exemption.

Adapters keep only benchmark data (key, shard and partition tables) and the
tuning-to-option mapping.  The registry makes those decisions discoverable,
auditable, and enforceable.

```
Registry rule     →  declares transformer_id "translate_ddl:strip_foreign_keys"
Adapter           →  passes the matching option to translate_ddl
compat_lint       →  verifies every regex/DDL-text branch in adapter code
                     maps to a registered rule_id
```

Decision rules:

- Every detected adapter CREATE TABLE rewrite must be registered under
  `Phase.DDL_OPTIMIZE` or explicitly exempted with rationale.
- `governance_only=True` means the rule is authoritative documentation and a
  CI governance anchor, not a dispatch target.
- `compat_lint CLEAN` means no source-detected CREATE TABLE rewrite is
  unregistered or uninspectable. It does not prove semantic equivalence of the
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
               transformer_id="translate_ddl:strip_foreign_keys",
               description="Remove FK clauses unsupported by MyPlatform.",
           ),
           reason="MyPlatform raises error XYZ on FOREIGN KEY clauses.",
       ),
       Phase.DDL_OPTIMIZE,
       "myplatform",
   )
   ```

   DDL rewriting itself is performed by the SQLGlot fork through
   `benchbox.utils.dialect_utils.translate_ddl`; the rule is a governance anchor
   (`governance_only=True`) whose `transformer_id` names the `translate_ddl`
   option that performs the rewrite (for example
   `translate_ddl:strip_foreign_keys`).

2. **Wire** the adapter by building the `translate_ddl` options from adapter
   state and calling it on the generated schema script; keep only benchmark data
   (key, shard and partition tables) in BenchBox:

   ```python
   from benchbox.utils.dialect_utils import translate_ddl

   class MyPlatformAdapter(PlatformAdapter):
       def _schema_statements(self, benchmark):
           script = self._create_schema_with_tuning(benchmark, source_dialect="standard")
           return translate_ddl(script, "myplatform", "standard", strip_foreign_keys=True)
   ```

3. **Confirm** `compat_lint` runs clean:

   ```bash
   uv run -- python -m benchbox.sql_compat.inventory --check-ddl-drift
   ```

## Canonical example: SingleStore

SingleStore has four DDL_OPTIMIZE governance rules, all performed by
`translate_ddl` in one pass:

| # | rule_id suffix                    | transformer_id                              | What it does |
|---|-----------------------------------|---------------------------------------------|--------------|
| 1 | `strip_foreign_keys`              | `translate_ddl:strip_foreign_keys`          | Removes FK clauses (error 2752) |
| 2 | `reference_table_for_dimensions`  | `translate_ddl:singlestore.reference_tables`| `CREATE TABLE` → `CREATE REFERENCE TABLE` for nation/region |
| 3 | `inject_shard_key`                | `translate_ddl:singlestore.shard_keys`      | Injects `SHARD KEY` (skips `REFERENCE TABLE`) |
| 4 | `inject_sort_key`                 | `translate_ddl:singlestore.sort_keys`       | Injects `SORT KEY` (skips `REFERENCE TABLE`) |

Rule file: `benchbox/sql_compat/rules/ddl_optimize/singlestore_ddl_rewrites.py`
Adapter:   `benchbox/platforms/singlestore.py` — `SingleStoreAdapter._schema_statements` supplies the shard, sort and reference-table data

## Multi-rule platforms and resolve() vs resolve_all()

Platforms with **multiple** rules at the same `(phase, platform)` key (like
SingleStore) will cause `REGISTRY.resolve()` to raise
`CompatibilityRegistryConflict`.  Use `REGISTRY.resolve_all(ctx)` for
governance queries on these platforms.

Every `DDL_OPTIMIZE` rule on such a platform is a governance anchor, so callers
that enumerate them should use `resolve_all()`.

## compat_lint enforcement

`benchbox/sql_compat/inventory.py` walks platform source files and flags DDL
rewrite behavior that lacks a matching registry rule. The drift check reports
detected behavior in five buckets:

| Status | Meaning |
|---|---|
| `registered_runtime_behavior` | A registered `DDL_OPTIMIZE` rule is wired to runtime code that rewrites the statement. |
| `registered_governance_only_intent` | The adapter performs the rewrite locally and the registry rule documents/enforces that intent. |
| `explicit_exemption` | The local rewrite is intentionally outside the registry and carries an exemption rationale. |
| `unregistered_detected_behavior` | Source inspection found CREATE TABLE rewrite behavior with no rule or exemption; CI fails. |
| `unknown_uninspectable_behavior` | Source inspection could not safely inspect a platform file; CI fails rather than claiming clean. |

`make compat-docs-check` runs the generated compatibility-doc drift check and
`uv run -- python -m benchbox.sql_compat.inventory --check-ddl-drift`, and the
PR workflow runs that target in the code lint job.
