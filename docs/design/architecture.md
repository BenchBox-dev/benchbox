<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# BenchBox Architecture

```{tags} contributor, concept
```

## Read this first: the contract map

[Public Contracts and Support Taxonomy](../reference/public-contracts.md) is
the authority on what BenchBox promises. It classifies every surface by
compatibility tier (`stable-public`, `beta-public`, `internal`,
`experimental`, `deprecated`, `generated`, `repo-only`) and names the source
of truth for each one. This document describes how the code delivers those
surfaces; when the two disagree, the contract map wins, and changing a
mapped surface requires updating the map in the same PR.

Two classifications do most of the work:

- **Compatibility tier** answers "can I depend on this?" for APIs, CLIs,
  generated stores, and docs.
- **`support_status`** answers "is this benchmark or platform a supported
  product?" (`stable`, `beta`, `experimental`, `repo_only`, `deprecated`,
  `document_only`). For benchmarks it controls the label shown next to a
  public entry; it never hides one. Hiding is the separate `surface` gate
  (`public` vs `internal`), and capability flags such as `supports_dataframe`
  are never inferred from either. (Platform discovery helpers additionally
  filter `deprecated` and `document_only` platforms unless the caller opts
  in; see Platform discovery below.)

## Product surfaces and their code homes

| Surface | Tier | Code |
|---|---|---|
| CLI commands and documented options | `beta-public` | `benchbox/cli/commands/`, `docs/reference/cli/` |
| Python wrapper facades (`benchbox.TPCH(...)`) and `BaseBenchmark.run_with_platform` | `beta-public` | `benchbox/__init__.py`, top-level wrapper modules, `benchbox/base.py` |
| Runtime loader and registries behind the facades | `internal` | `benchbox/core/benchmark_loader.py`, `benchbox/core/benchmark_registry.py` |
| Shared run engine below CLI and MCP | `internal` | `benchbox/core/run_service.py` (the `__all__` list is the cross-surface import contract) |
| MCP tools | `beta-public`, deliberately scoped | `benchbox/mcp/`; every omitted CLI control carries a tier in the `docs/reference/mcp.md` omission ledger |
| Result JSON bundles (schema-versioned product data) | `beta-public` | `benchbox/core/results/schema_policy.py`, `benchbox/core/results/schema.py` |
| Explorer browser store (DuckDB + summaries, built from bundles) | `generated` | Explorer publish pipeline (repository tooling); reproducible from source bundles plus pipeline code |
| Public result submissions and their validation behavior | `beta-public` | `scripts/validate_submission.py`, `docs/reference/hosted-results-contract.md`, `docs/contributing-results.md` |
| Semantic chart IDs shared by CLI, MCP, templates, and Explorer | `beta-public` | `benchbox/core/visualization/chart_types.py`, `tests/parity/fixtures/chart_ids.json` (the Explorer registry lives in `BenchBox-dev/benchbox-site`) |
| `benchbox.experimental` namespace | `experimental` | Ships in the wheel for convenience, outside the supported product surface |
| Maintainer scripts, audits, and planning records | `repo-only` | Maintainer tooling, not a user API |

There are currently 22 benchmarks across TPC standards, academic, industry,
real-world, time-series, primitives, AI/ML, and experimental categories
(registry-derived; `test_public_benchmark_count_claims_are_registry_derived`
pins this claim to `list_public_benchmark_ids()`).

## Execution architecture

A run flows through one shared engine, no matter which surface starts it:

```
CLI (`benchbox run`) or MCP (`run_benchmark`)
  → benchbox.core.run_service (shared engine, internal)
    → run_benchmark_lifecycle() (benchbox/core/runner/runner.py)
      → generate → load → execute (power/throughput test types)
        → PlatformAdapter hooks per phase
          → BenchmarkResults
```

Execution phases are declared in `LifecyclePhases` (`generate`, `load`,
`execute`, plus opt-in `statistics`), not inherited. Power and throughput
are test-type executions inside the execute phase, not lifecycle phases.

`BaseBenchmark` (`benchbox/base.py`) remains the public wrapper base and the
benchmark-facing API boundary; it does not own the runtime workflow. Adapter
instances are serial execution objects: one instance may serve sequential
runs, concurrent calls on one instance are not supported, and
`run_benchmark()` resets run-scoped caches at run start.

### SQL path

SQL adapters implement `PlatformAdapter` (`benchbox/platforms/base/adapter.py`):
connection lifecycle, platform DDL, bulk load, and per-query execution.
Queries are defined once per benchmark and translated per dialect with the
private `benchbox._vendor.sqlglot` runtime. Its pinned pure-Python wheel comes
from [BenchBox-dev/benchbox-sqlglot](https://github.com/BenchBox-dev/benchbox-sqlglot)
and ships inside the BenchBox wheel and sdist; ordinary builds and installs
do not fetch Git or a custom package index. SQLGlot source stays in the fork.
Heavy SDK imports stay lazy until the platform is actually used.

### DataFrame path

DataFrame runs enter the same lifecycle, then branch to
`BenchmarkExecutionMixin.run_benchmark()`
(`benchbox/platforms/dataframe/benchmark_mixin.py`), which is the only
production DataFrame lifecycle path. Adapters group by API family
(`ExpressionFamilyAdapter` for Polars/PySpark/DataFusion-style APIs,
`PandasFamilyAdapter` for Pandas/cuDF/Dask-style APIs), so a new
expression-style platform inherits translation, tuning, and execution.

## Data architecture: bundles are the product

The schema-versioned result bundle is the unit everything else consumes:
CLI output, submission validation, hosted results, the explorer, and
SQL/DataFrame comparisons all read the same shape, guarded by the schema
policy. SQL and DataFrame bundles for one benchmark must preserve the
cross-mode parity invariants.

```
run → BenchmarkResults → bundle JSON (beta-public data)
  → validate-submission (deterministic errors, privacy/trust handling)
  → published corpus (results-data/, trust labels + provenance)
    → explorer pipeline (generated DuckDB, summaries, matrix artifacts)
      → Results Explorer (browser) over URL-backed views
```

The explorer's secondary navigation and benchmark browser groupings are
presentation over this pipeline, not new data sources: chart selection,
facets, filters, and anchors already live in explorer URL state, so those
views are shareable as URLs.

### Tuning evidence is honest by construction

Tuning claims follow fail-closed rules end to end. The applied-tuning ledger
records only statements that actually executed, using a shared vocabulary
(`PHASE_SESSION` and friends); benchmarking hygiene applied to every run is
never recorded as tuning, and a run that requested tuning but executed no
tuning-derived statements reports `noop`, never a false `applied`. A platform with no
tuning-derived session surface (Snowpark today) declares that explicitly
rather than inheriting another platform's capture story. Post-load
introspection corroborates the ledger before any `applied_verified` state
is earned, and the explorer renders the recorded verdicts read-only.

## Discovery architecture: gate, label, capability

Three independent registry fields govern how a benchmark appears;
conflating them is the most common extension-point bug:

- `surface` is the **only benchmark discovery gate**. `internal`
  benchmarks stay out of CLI listings, MCP listings, and resources, but
  remain runnable by explicit ID. Platform entries have no `surface`
  field.
- `support_status` is the **product-support label** (`Stable`, `Beta`,
  `Experimental`, …). Public benchmarks are listed with their label; the
  explorer benchmark browser groups on it.
- Capability flags (`supports_dataframe`, platform capabilities, dependency
  hints) drive **routing**, never visibility.

Platform discovery instead filters on `support_status`: the public
`PlatformRegistry.get_sql_platforms()` / `get_dataframe_platforms()` helpers
hide `deprecated` and `document_only` entries unless the caller passes
`include_deprecated=True`. (The `benchbox platforms list` CLI annotates every
registered platform with its status rather than hiding.)

Sources of truth: `benchbox/core/benchmark_registry.py` (benchmarks; the
per-benchmark rationale and promotion criteria live in
`docs/benchmarks/support-status.md` and are drift-checked against the
registry) and `benchbox/core/platform_registry.py` (platforms, reading
adapter import specs and aliases from `benchbox/core/platform_manifest.py`).

## Extension points and their contract obligations

Mechanics for adding benchmarks, platforms, and query variants are covered
in [Custom Benchmarks](../advanced/custom-benchmarks.md) and [Adding New
Platforms](../development/adding-new-platforms.md). Each addition also
carries contract obligations from the map:

- Every registry benchmark and platform entry declares exactly one
  `support_status`; benchmark entries additionally satisfy the
  drift-checked criteria in `docs/benchmarks/support-status.md`.
- A new platform manifest entry wires discovery, capabilities, dependency
  hints, and docs together; aliases need a compatibility note.
- A new semantic chart ID lands in `benchbox/core/visualization/chart_types.py` first, then follows to
  templates, ASCII runtime, Explorer registry, and parity fixtures in the
  same PR; IDs are deprecated, never silently removed.
- A new `CREATE TABLE` rewrite path in an adapter must satisfy the SQL
  compatibility governance in `benchbox/sql_compat/` (`make
  compat-docs-check`).
- Behavior changes to any mapped surface update the contract map (or state
  why it is unchanged) and land docs/tests in the same PR; removals go
  through `docs/reference/backward-compatibility.md`.

## Repository map

| Path | Purpose | Tier |
|---|---|---|
| `benchbox/` | Installable product: benchmarks, platforms, CLI, MCP, results, visualization | `beta-public` surface over `internal` engine |
| `results-data/` | Public result corpus and validation metadata | Published data |
| `docs/` | User, concept, reference, and contributor documentation | Varies by page; the map governs |
| `tests/` | Unit, integration, end-to-end, parity, and live suites | Verification gates per map row |
| `examples/` | Runnable examples, notebooks, tuning files | Illustrative, not contractual |

## Related documentation

- [Public Contracts and Support Taxonomy](../reference/public-contracts.md) — the map itself
- [Benchmark Support Status Criteria](../benchmarks/support-status.md) — per-benchmark rationale
- [Backward Compatibility](../reference/backward-compatibility.md) — deprecation and removal paths
- [MCP Reference](../reference/mcp.md) — scoped surface and omission ledger
- [One-engine scoped surfaces ADR](../development/adr/adr-one-engine-scoped-surfaces.md) — why CLI and MCP share `run_service`
- [Concepts: Architecture](../concepts/architecture.md) — user-facing component tour with examples
