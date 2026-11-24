# ADR: Catalog Runtime Parse vs. Build-Time Codegen

**Status:** Accepted - 2026-05-25
**Decision owner:** BenchBox shrink campaign
**Operative docs:** `_project/goal-shrink-core-code.md`,
`_project/DONE/main/shrink-followup-codegen-vs-runtime-parse-adr.yaml`
**Related decisions:** `_project/decisions/shrink-objective.md`

## Context

The shrink campaign moved benchmark metadata, specs, and query catalog content
out of maintained Python into structured files. That work followed an implicit
runtime-parse pattern:

- Six pre-existing catalog loaders already read YAML through explicit loader
  functions: `ai_primitives`, `metadata_primitives`, `primitives`,
  `read_primitives`, `transaction_primitives`, and `write_primitives`.
- Commit #579 moved TPC-DS specs to YAML using the same general pattern.
- Later registry/spec migrations initially preserved module-level constants by
  eagerly parsing YAML during import. The follow-up
  `_project/DONE/main/shrink-followup-registry-lazy-cached-load.yaml` measured
  that eager import cost and converted it to lazy cached access.

The credible alternative is build-time codegen: treat YAML as canonical but
generate Python modules from it so runtime imports do not parse YAML and static
tools can see generated symbols directly. That is a legitimate architecture,
but it adds a build step, generated-file churn, and separate generated-Python
accounting. The shrink control document needed one canonical catalog pattern so
future reductions do not decide case by case.

## Decision

BenchBox's canonical catalog pattern is **human-authored structured source
loaded through lazy runtime accessors**.

Use YAML, CSV, SQL files, TOML, or another reviewable structured format when
the content is data, metadata, or declared query surface. Load it from package
resources only inside explicit accessors or lazy module attributes, not at
module import time. Cache the parsed result when the accessor preserves
module-level compatibility names, sits on a hot path, or is expected to be
called repeatedly in one process.

Build-time generated Python is **not** the default catalog pattern and earns no
official shrink credit unless a future explicit ADR supersedes this decision.
Generated Python may still be used for a separately justified product or tooling
reason, but it must be tracked as generated surface and cannot silently reduce
the maintained-Python distance.

When legacy imports require old constants or callables, preserve them with
explicit registries, typed mappings, or PEP 562 `__getattr__` wrappers. Do not
reintroduce dynamic `globals()` mutation or generated-name synthesis as a way to
hide maintenance surface.

## Evidence

| Implementation | Source Shape | Loading Shape | Notes |
| --- | --- | --- | --- |
| `benchbox/core/write_primitives/catalog/loader.py` | YAML operation catalog | Runtime loader function | Reads package resource only inside `_load_catalog_payload()`; `load_write_primitives_catalog()` calls it explicitly. |
| `benchbox/core/primitives/catalog/loader.py` | YAML operation/query catalogs | Runtime loader helper | Shared `_load_yaml()` centralizes resource reads and validation for two catalog families. |
| `benchbox/core/ai_primitives/catalog/loader.py` | YAML query catalog | Runtime loader function | Same lazy-by-call convention as write primitives. |
| `benchbox/core/metadata_primitives/catalog/loader.py` | YAML query catalog | Runtime loader function | Small direct loader; no import-time parse. |
| `benchbox/core/benchmark_registry.py` | YAML benchmark metadata | Lazy cached module data | `SingleFlightValueCache` (formerly `functools.lru_cache`) builds derived structures on first access; `__getattr__` preserves public names. |
| `benchbox/core/results/benchmark_specs.py` | YAML benchmark specs | Lazy cached module data | `SingleFlightValueCache` plus `__getattr__` pattern for compatibility exports. |

The registry lazy-load follow-up measured the import regression from eager YAML
loads before repairing it:

| Surface | Eager module self-time | Isolated parse cost | Lazy cached self-time after repair |
| --- | ---: | ---: | ---: |
| `benchmark_registry` | ~10.2 ms | 8.98 ms | ~0.40 ms |
| `benchmark_specs` | ~12.6 ms | 10.70 ms | ~0.57 ms |

Those numbers show the real hazard is module-scope parsing, not the existence
of structured catalog files. Lazy cached access removes the import-time
regression without adding a generated-Python build pipeline.

## Decision Matrix

| Option | Import/runtime cost | Static findability and typing | Build/review cost | Consistency | Shrink accounting |
| --- | --- | --- | --- | --- | --- |
| Lazy runtime parse, cache hot accessors | No import-time parse; first-use parse only; repeat calls cached where needed | Good when paired with typed containers, explicit registries, schema validation, and fingerprints | Low; source files stay human-authored and diffable | Matches existing loader direction and lazy registry/spec repair | Eligible only for genuine maintained-Python reduction; relocation remains uncredited unless approved |
| Build-time Python codegen from YAML | No runtime parse for generated modules | Strong for generated symbols, but source of truth moves behind a build step | High; generated churn, tooling, packaging, and review rules required | Breaks the current runtime-loader convention | Generated lines tracked separately; no official shrink credit by default |
| Eager runtime YAML parse | First import pays I/O and parse cost | Same as runtime parse after import | Low | Conflicts with import-loading guardrail | Rejected; measured import-time regression |
| No canonical pattern | Depends on local choice | Depends on local choice | Low initially, high over time | Drift across catalogs | Rejected; reopens the same gate for every shrink slice |

## Required Pattern For Future Catalog Shrink

Future Python-to-catalog/query reductions must satisfy all of these technical
gates before credit can even be considered. These gates are necessary, not
sufficient: the shrink objective's ledger formula still decides whether the
change is genuine maintained-Python reduction or uncredited relocation.

1. The structured file is the human-authored canonical source, or the PR cites
   a newer ADR that says otherwise.
2. Loading is lazy. Hot-path, compatibility-export, or repeated-load surfaces
   are cached.
3. The catalog has schema or typed validation exercised by tests or a project
   gate.
4. Query surface remains reviewable. SQL in YAML uses block scalars, not escaped
   newline blobs; external `.sql` files are preferred when they improve review.
5. Public import names, callable names, registry keys, categories, and benchmark
   semantics are fingerprinted when they can change.
6. Generated Python, if any, is marked generated, ledgered separately, and
   uncredited unless a future ADR changes the accounting rule.

## Alternatives Rejected

### Build-Time Generated Python From Catalog Source

This gives direct grep and type-check visibility for generated symbols and can
avoid first-use YAML parsing. It loses on current evidence because BenchBox
already has a runtime-loader convention, the measured import problem was solved
by lazy cached access, and codegen would add a new generated-artifact lifecycle
with no demonstrated maintenance win large enough to justify it.

### Eager Runtime Parse At Module Import

Rejected. The registry/spec repair measured a material import-time cost from
eager YAML parse. Future migrations should not recreate that shape without a
linked exception, measurement, and budget.

### Case-By-Case Local Decisions

Rejected. Shrink work is especially prone to metric pressure. Without a
canonical pattern, future slices would repeatedly trade reviewability,
performance, generated churn, and credit accounting differently.

## Consequences

- The codegen/runtime-source gate in the shrink control document is resolved
  conservatively.
- TPC-DS generated DataFrame query shrink can proceed only if it preserves
  static findability, schema/typed validation, query reviewability, and lazy
  runtime access. It cannot use generated Python to claim official credit.
- Existing runtime catalogs do not need churn merely to conform. The decision
  governs new migrations and touched surfaces.
- A future codegen proposal is still possible, but it must be a separate ADR
  with measured benefits, build/publishing rules, generated-file accounting,
  and review guidance.
