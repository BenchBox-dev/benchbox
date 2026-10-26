<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Benchmark Family Plugin Seam Future State

```{tags} contributor, architecture
```

Related TODOs: `arch-ssb-family-plugin-seam-pilot`, `benchmark-api-and-core-boundary-cleanup`

Composition prerequisite: [`Core Kernel and Runtime Composition Boundary`](https://benchbox.dev/docs/development/adr/adr-runtime-composition-boundary.html).
The plugin seam must use the selected core-kernel boundary; it must not create
`benchbox.runtime` or a second generic execution helper.

## Decision

Benchmark family logic should move behind an explicit family-plugin seam before
any broad `benchbox/core` split. The seam is a design boundary first, not a
mechanical package move: public wrappers and the CLI keep their current user
contracts while internal family implementations converge on a shared interface.

## Current Surface Classification

| Surface | Tier | Current role |
|---|---|---|
| Top-level wrappers such as `benchbox.TPCH(...)` | `beta-public` | User-facing convenience facades with direct tests. |
| `benchbox.base.BaseBenchmark` | `beta-public` | Public base for wrappers and orchestration helpers. |
| `BaseBenchmark.run_with_platform()` | `beta-public` | Programmatic adapter execution hook used by CLI-adjacent tools and MCP. |
| `benchbox.core.benchmark_loader` | `internal` | Registry-backed runtime loader for core orchestration. |
| `benchbox.core.base_benchmark.BaseBenchmark` | `deprecated` | Internal compatibility module with no remaining production implementation consumers. |
| Benchmark registry metadata | `beta-public` | Source of truth for benchmark identity, discovery, public/internal surface, and class-name mappings. |

## Target Seam

A benchmark family plugin owns:

- benchmark metadata fields needed for discovery;
- the core benchmark class and default constructor policy;
- data generation and manifest behavior for its family;
- query catalog, query IDs, streams or maintenance phases when relevant;
- optional DataFrame query implementations for the same family;
- family-specific scale validation beyond registry defaults;
- result metadata extensions that are explicitly shared through the result
  schema policy.

The shared runtime owns:

- platform adapter execution;
- result bundle construction and schema-version policy;
- validation mode selection and cross-platform comparison;
- lifecycle phase orchestration;
- registry publication and public/internal surface filtering.

## Interface Shape

The first interface should be small and registry-backed:

| Method or field | Purpose |
|---|---|
| `benchmark_id` | Stable registry key. |
| `core_class` | Class used by the internal loader. |
| `public_class_name` | Optional top-level wrapper facade class. |
| `surface` | Public or internal discovery visibility. |
| `default_scale(scale_factor)` | Scale validation and default constructor policy. |
| `create(config, system_profile)` | Construct a family instance without CLI imports. |
| `phases()` | Declare supported generate/load/execute/maintenance phases. |
| `result_metadata()` | Family-owned result fields that are accepted by schema policy. |

This interface is piloted on SSB only. SSB is `support_status: stable`,
declares scale `0.01` (about 1-5 minutes, synthetic data), has
`supports_streams: false`, and already has
`make ssb-cross-surface-equivalence-report`. JoinOrder cannot produce a cheap
before/after measurement (canonical scale 1.0, licensing, no CI path). TPC-DS
streams and compliance would obscure the seam. TPC-H is runnable at 0.01 but
has `supports_streams: true`.

`BenchmarkFamilyPlugin` lives in `benchbox.core.benchmark_registry`. The
registry module's `FAMILY_PLUGIN_IMPORTS` map points at the family object.
The catalog YAML cannot grow a new top-level key without a schema change, so
the pilot map stays next to the registry loader. The internal loader calls
`plugin.create(...)` when a row exists and keeps the heuristic constructor
path for every other family. SSB's public wrapper facade is unchanged.

## Extension-cost measurement (SSB-like family)

The metric is semantic decisions, repeated code, tests, and time. File count
is not the success measure.

### Before the seam

Adding another SSB-like family (simple OLAP, no streams, optional DataFrame)
required:

| Cost | Current path |
|---|---|
| Identity decisions | `benchmark_id`, display name, category, `support_status`, `surface`, scale ladder, streams, DataFrame, estimates, `data_source` |
| Construction decisions | public vs core class name, lazy vs eager export, constructor kwargs, whether the loader `force_regenerate` allowlist must grow |
| Repeated code | wrapper method proxy; loader kwargs dict; importlib class-name heuristic |
| Required tests | wrapper facade proxy, schema/generator, contract-count update, DataFrame/cross-surface if enabled |
| Contributor time | about one focused day after the query/schema work exists, mostly deciding constructor and loader special cases |

### After the SSB pilot

The same hypothetical next family still owns its package, wrapper facade, and
registry identity. The seam removes the loader-heuristic decisions:

| Cost | After SSB plugin |
|---|---|
| Identity decisions | Unchanged registry metadata |
| Construction decisions | Implement `default_scale`, `create`, `phases`, and `result_metadata`; add one `FAMILY_PLUGIN_IMPORTS` row. No loader allowlist edit |
| Repeated code | Wrapper facade remains (non-goal to remove). Constructor policy lives in `create()` instead of another loader branch |
| Required tests | Existing wrapper/schema tests plus the plugin contract. Contract counts stay unchanged when no new family is added |
| Contributor time | Same package and facade work; constructor/loader policy drops to the four plugin methods and one YAML row |

SSB itself is the first row: `FAMILY_PLUGIN_IMPORTS["ssb"]` resolves
`benchbox.core.ssb.family:SSBFamily`. No other family is on the seam.

## Non-Goals

- Removing top-level wrapper facades.
- Making `benchbox.core.benchmark_loader` public.
- Creating a third benchmark base class.
- Moving all benchmark directories in one PR.
- Changing result bundle shape as part of the seam definition.

## Migration Gates

1. Keep wrapper facade tests green, or add a compatibility registry row plus a
   migration path.
2. Keep registry, public discovery, top-level facade, and loader counts under a
   focused drift test.
3. Keep core API files free of concrete platform adapter imports.
4. Migrate `datavault` and `tpcds_obt` off
   `benchbox.core.base_benchmark.BaseBenchmark` before removing that class.
5. Land one pilot family through the seam before any package-level split.

## Rejected Alternatives

| Alternative | Reason rejected |
|---|---|
| Remove wrapper facades now | Existing tests assert wrapper behavior and users have documented imports. |
| Promote the loader as public API | The loader has constructor heuristics and internal fallback behavior that should not become an external contract. |
| Add a new base class | This would increase base-class ambiguity instead of resolving it. |
| Mechanically split `benchbox/core` | File movement without a dependency-direction boundary would only relocate drift. |
