<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Public Contracts and Support Taxonomy

This page classifies BenchBox surfaces by compatibility tier and names the
source of truth for each one. It is narrower than a full architecture guide.

## Contract Tiers

| Tier | Meaning | Breaking-change rule |
|---|---|---|
| `stable-public` | User-facing surface that should remain compatible across beta patch/minor releases unless a documented migration exists. | Behavior changes come with a migration note and a compatibility registry entry. |
| `beta-public` | User-facing surface exposed during beta. It is supported, but details can change before 1.0 with documented rationale. | Changes ship with updated docs and tests, and a deprecation path when practical. |
| `internal` | Implementation detail. External callers should not depend on it. | Can change at any time; public docs do not promise it. |
| `experimental` | Prototype or research surface. It may ship for convenience without product support. | Can change or disappear; docs label it experimental. |
| `deprecated` | Compatibility surface retained temporarily. | Has a migration path and a target review or removal window in [Backward Compatibility](backward-compatibility.md). |
| `generated` | Output derived from source metadata, schemas, fixtures, or build scripts. | Source metadata is authoritative; hand edits are drift unless explicitly marked editorial. |
| `repo-only` | Contributor, planning, audit, or release-support surface that is not a user product API. | Can change at any time; wheel and API stability do not apply. |

## Public Surface Map

| Surface | Current tier | Compatibility promise | Source of truth |
|---|---|---|---|
| CLI commands and documented options | `beta-public` | Documented commands and option meanings are supported for beta users. Option breadth can change, with release notes and backward-compatible aliases when practical. | `benchbox/cli/commands/`, `docs/reference/cli/` |
| Top-level Python wrapper facades, for example `benchbox.TPCH(...)` | `beta-public` | Wrapper imports and facade methods remain supported. Current count: 21 exported top-level benchmark facades from 23 registry class-name mappings; `ai_primitives` and `joinorder_synthetic` are core-only. | `benchbox/__init__.py`, top-level wrapper modules |
| `benchbox.base.BaseBenchmark` | `beta-public` | Public base for wrapper benchmarks and orchestration helpers. Changes to kwargs, result helpers, or method contracts are recorded in the compatibility registry. | `benchbox/base.py`, `docs/reference/backward-compatibility.md` |
| `BaseBenchmark.run_with_platform` | `beta-public` | Standard programmatic execution hook for CLI-adjacent tools and MCP; callers pass an adapter and run options. | `benchbox/base.py` |
| `benchbox.core.benchmark_loader` | `internal` | Registry-backed runtime loader for CLI/core orchestration. It is not a public Python API and should not be imported by external callers. | `benchbox/core/benchmark_loader.py`, `benchbox/core/benchmark_registry.py` |
| `benchbox.core.run_service` | `internal` | The shared run engine below both CLI and MCP. Names in `__all__` may be imported by `benchbox.cli` and `benchbox.mcp`; anything else is module-private. None of it is a supported external Python API. | `benchbox/core/run_service.py`, `docs/development/adr/adr-one-engine-scoped-surfaces.md` |
| `benchbox.core.base_benchmark.BaseBenchmark` | `deprecated` | Internal compatibility base with no remaining production consumers; not an alias and not the extension path for new benchmarks. Its removal is tracked in the compatibility registry. | `benchbox/core/base_benchmark.py`, `docs/reference/backward-compatibility.md` |
| Adapter subclassing hooks and base mixins | `beta-public` | Adapter authors can depend on documented `PlatformAdapter` hooks, ABC signatures, and adapter authoring docs. | `benchbox/platforms/base/`, `docs/development/adding-new-platforms.md` |
| `PlatformAdapter` lifecycle | `beta-public` | Adapter instances are serial execution objects. One instance may be reused for multiple benchmark runs sequentially; `run_benchmark()` resets run-scoped caches at run start and restores run-config plan-capture overrides at run end. Concurrent calls on one adapter instance are not supported. | `benchbox/platforms/base/adapter.py`, `benchbox/platforms/base/result_capture.py` |
| DataFrame adapter execution path | `beta-public` | Production DataFrame execution routes through `benchbox.core.runner.runner` to `adapter.run_benchmark()`, implemented by `BenchmarkExecutionMixin` for production DataFrame platforms such as `polars-df`, `pandas-df`, `datafusion-df`, and `dask-df`. | `benchbox/core/runner/runner.py`, `benchbox/platforms/dataframe/benchmark_mixin.py` |
| Platform registry metadata | `beta-public` | Registry metadata is the source for platform discovery, capabilities, dependency hints, and platform support status. Renamed platforms keep aliases with a compatibility note. | `benchbox/core/platform_registry.py` |
| MCP tools | `beta-public` | Tool schemas and documented parameters are supported as a scoped surface over the shared BenchBox engine. Business logic lives in `benchbox.core`, below both CLI and MCP, and each surface exposes a chosen subset. The CLI controls that MCP does not expose, and why, are listed in the [MCP Server Reference](mcp.md). MCP result bundles are schema-comparable to CLI bundles. | `benchbox/mcp/`, `docs/reference/mcp.md`, `docs/development/adr/adr-one-engine-scoped-surfaces.md` |

The `textcharts-mcp` external visualization server is intentionally a separate-client dependency, not a bundled or proxied BenchBox tool. The decision and tradeoffs are recorded in `docs/design/textcharts-mcp-boundary.md`; BenchBox publishes only the result-aware chart tools.

| Surface | Current tier | Compatibility promise | Source of truth |
|---|---|---|---|
| Visualization semantic chart IDs | `beta-public` | Result-aware chart IDs accepted by CLI, MCP, templates, ASCII runtime dispatch, and Results Explorer derive from the semantic registry. Raw textcharts primitive IDs are a separate dependency namespace. IDs are deprecated rather than silently removed. | `benchbox/core/visualization/chart_types.py`, `benchbox/core/visualization/ascii_runtime.py`, `benchbox/core/visualization/templates.py`, `results-explorer/src/lib/chartRegistry.ts` |
| `benchbox.core.visualization.render_ascii_chart` | `beta-public` | Compatibility data-first ASCII primitive renderer for callers that already have chart-specific data objects. It intentionally has narrower coverage than the result-aware semantic renderer and excludes `power_bar`, which needs normalized BenchBox result context. | `benchbox/core/visualization/exporters.py` |
| Result JSON bundles | `beta-public` | Schema-versioned result bundles are product data consumed by CLI, submission validation, hosted results, explorer, and SQL/DataFrame comparisons. SQL and DataFrame bundles preserve the cross-mode invariants below. Accepted versions, field semantics, and parity guarantees change only with an update to the schema policy and the hosted-results contract. | `benchbox/core/results/schema_policy.py`, `benchbox/core/results/schema.py`, `docs/reference/result-formats.md`, `docs/reference/hosted-results-contract.md` |
| Explorer read model and generated browser inputs | `generated` | Browser data stores are generated from accepted result bundles and can be reproduced from source bundles and pipeline code. | Results Explorer pipeline scripts, results explorer generated data |
| Public submission validator behavior | `beta-public` | PR-based public result submissions receive deterministic validation errors and privacy and trust handling. | `scripts/validate_submission.py`, `docs/contributing-results.md`, `docs/reference/hosted-results-contract.md` |
| SQL compatibility rule catalog | `internal` | Hybrid governance catalog: every source-detected adapter CREATE TABLE rewrite is runtime-dispatched by `BaseDdlOptimizer`, registered as `governance_only`, or explicitly exempted. | `benchbox/sql_compat/`, `benchbox/platforms/`, `docs/compat/` |
| Generated compatibility docs | `generated` | Generated docs match registry and rule metadata; hand edits are drift unless the section says it is editorial. | `benchbox/sql_compat/`, generated docs under `docs/compat/` |
| `benchbox.experimental` namespace | `experimental` | Ships in the default wheel for developer convenience but is outside the supported beta product surface. It may be promoted, extracted, or removed. | `README.md`, `pyproject.toml` |
| `_project` scripts, audits, and analysis artifacts | `repo-only` | Contributor workflow aids in the source repository; not user-facing API. | Source repository only |
| Contributor Make targets | `repo-only` | Repository workflow commands are maintainer tools, not user APIs; names and behavior can change with repository policy. | `Makefile`, `make/`, `make/help.mk` |
| TODO, DONE, and ADR/future-state docs | `repo-only` | Planning and decision records guide implementation but do not themselves create runtime API. Accepted decisions move into user or developer docs when they become product contracts. | Source repository only |

### CLI compatibility note: deprecated `run-official` quiet-path contract

The hidden deprecated `benchbox run-official` command remains inside the
beta-public CLI surface as a compatibility shim. Its multi-stream throughput
support still routes through `--streams`, but result-path discovery is no longer
allowed to infer from filenames, globs, or mtimes.

When `run-official` is invoked with `--quiet`, it reuses the same contract as
`benchbox run --quiet`: after a successful export, the **final non-empty stdout
line is the JSON result path**. Callers should read that path rather than
search the output directory.

Source of truth: `benchbox/cli/commands/run_official.py`.

## Support Status Taxonomy

`support_status` is a product-support classification for platforms and
benchmarks. It is different from local dependency availability: a stable
platform can be unavailable on a developer machine because an optional SDK is not
installed.

Allowed values:

| Status | Meaning | Packaging | Docs | Registry visibility | MCP exposure | CI coverage | Breakage policy |
|---|---|---|---|---|---|---|---|
| `stable` | Supported product surface for normal users. | Included or installable through documented extras. | Full user docs and examples where relevant. | Listed by default. | Exposed when the MCP surface supports that capability. | Fast/unit plus representative smoke or integration coverage. | Fix promptly or document temporary known issue. |
| `beta` | Supported beta surface with known evolution risk. | Included or installable through documented extras. | Docs must label beta caveats. | Listed by default with beta status. | Exposed if behavior is covered by MCP docs/tests. | Focused tests for core behavior. | Can change with updated docs, tests, and migration guidance. |
| `experimental` | Prototype or research surface. | May ship in default wheel or optional extra, but must be labeled. | Experimental docs only; no support implication. | Hidden or clearly labeled. | Omitted unless the MCP tool explicitly labels it. | Best-effort targeted tests. | May change or be removed without compatibility promise. |
| `repo_only` | Contributor or source-checkout-only surface. | Not promised in wheels. | Developer/project docs only. | Hidden from user discovery. | Not exposed. | Script or workflow checks only when useful. | May change with repo workflow updates. |
| `deprecated` | Temporarily retained compatibility surface. | Retained until target review/removal window. | Migration path required. | Listed with deprecation status or hidden after warning window. | Exposed only if existing clients need it. | Compatibility tests until removal. | Removal follows registry target and release notes. |
| `document_only` | Documented external concept or planned support with no runtime implementation. | No package promise. | Docs must say it is not executable support. | Not listed as runnable. | Not exposed. | Link/static doc checks only. | No runtime breakage claim. |

Platform and benchmark registry metadata carry exactly one `support_status`
for every runtime entry. Benchmark support status is distinct from benchmark
`surface` visibility and from capability flags such as `supports_dataframe`. The
auditable per-benchmark rationale and promotion criteria are in
[Benchmark Support Status Criteria](../benchmarks/support-status.md), whose
per-benchmark status rows are checked against the registry.

## Benchmark Visibility Policy

Three registry fields govern how a benchmark appears on public surfaces, and
they are **independent**.

`surface` is the **only discovery gate**. It decides whether a benchmark is
listed or hidden, regardless of `support_status`:

| `surface` | CLI interactive listing | MCP `list_available` / `get_benchmark_info` | MCP resources | Runnable by explicit ID | MCP `get_query_details` |
|---|---|---|---|---|---|
| `public` | Listed and labeled with its status | Exposed, including `support_status` | Exposed, including `support_status` | Yes | Returns metadata including `support_status` |
| `internal` | Hidden | Hidden (`error` / not-found) | Hidden | Yes (explicit-ID exception) | Returns `display_name` and `category` only, **omits `support_status`** |

`support_status` controls the **product-support label** shown next to a public
benchmark; it never hides one. For any `public` benchmark, regardless of status:

| `support_status` | CLI label | Listed by default | DataFrame routing |
|---|---|---|---|
| `stable` | `Stable` | Yes | Per `supports_dataframe` |
| `beta` | `Beta` | Yes | Per `supports_dataframe` |
| `experimental` | `Experimental` | Yes | Per `supports_dataframe` |
| `deprecated` | `Deprecated` | Yes, until the removal window | Per `supports_dataframe` |
| `document_only` | `Document-only` | Yes | Per `supports_dataframe` |
| `repo_only` | `Repo-only` | Only if also `surface: public` (normally `internal`) | Per `supports_dataframe` |

`supports_dataframe` is a **capability flag**: it controls DataFrame routing and
is never inferred from `support_status`. A `beta` benchmark may be
DataFrame-capable; an `experimental` one may not.

**Explicit-ID exception.** Internal benchmarks (for example `joinorder_synthetic`)
stay runnable by exact ID so contributor workflows keep working, but they must
not leak product-support claims onto discovery surfaces. `get_query_details`
therefore returns only neutral identifiers (`display_name`, `category`) for an
internal benchmark and omits `support_status`; `get_benchmark_info`,
`list_available`, and the benchmark resources hide them entirely.

To hide a public benchmark, change its `surface` to `internal` — do not repurpose
`support_status`. Demotion to `deprecated` keeps it listed (with a label) until a
separate `surface`/removal decision.

## Count and Drift Policy

Benchmark API snapshot: **23** registry entries; **23** loader-resolved core families; **22** public discovery entries; **21** top-level Python benchmark facades; **15** lazy facades; **6** eager facades; **2** core-only benchmark IDs. Benchmark support status: **6** stable, **11** beta, **5** experimental, **1** repo-only, **0** deprecated, **0** document-only.

Source-derived counts:

| Source | Current evidence | Contract implication |
|---|---|---|
| `benchbox.core.benchmark_registry` | 23 benchmark metadata entries and 23 loader-resolved IDs; support status counts are stable=6, beta=11, experimental=5, repo_only=1, deprecated=0, document_only=0. | Benchmark count and support claims must derive from registry metadata or avoid exact counts. |
| `benchbox.core.platform_registry.PlatformRegistry.get_all_platform_metadata()` | 52 platform metadata entries: 48 SQL-capable, 18 DataFrame-capable, 14 dual-mode. | README and platform docs must not carry unqualified hand-maintained platform counts. |
| `benchbox.core.results.schema_policy` | Current result schema version: `2.3`; runtime/explorer accepted versions: `2.0`, `2.1`, `2.2`, `2.3`; public submission accepts numeric `2.x`. | Result schema version claims must update with the named consumer policy or defer to this policy module. |

Authoritative count statements should come from the relevant registry metadata.
Editorial lists may remain in narrative docs, but they must not claim to be
exhaustive unless a generated or tested check keeps them synchronized.

README platform name lists print each entry's registry `support_status` next to
the name. A name with zero bundles in `results-data/corpus-inventory.json` is
marked *unproven*. That marker is corpus occupancy, not a new support tier, and
must not be used to hide a platform or to imply live warehouse results.

## Visualization Chart Contract

`benchbox/core/visualization/chart_types.py` is the semantic result-aware chart
registry for CLI, MCP, templates, ASCII runtime dispatch, and Results Explorer.

`benchbox.core.visualization.render_ascii_chart` is a compatibility data-first
renderer for callers that already hold primitive chart payloads. It intentionally
does not cover `power_bar`, because `power_bar` needs normalized BenchBox result
context and is rendered through `render_ascii_chart_from_results()`. Raw
textcharts primitive IDs such as `bar` or `heatmap` are dependency internals, not
BenchBox semantic chart IDs.

BenchBox MCP exposes only the result-aware `suggest_charts` and `generate_chart`
tools. It does not register or proxy the external `textcharts-mcp` server.
When a user configures both servers, `textcharts_*` tools are a separate raw
primitive rendering namespace; BenchBox MCP `chart_type` values remain semantic
IDs from the registry above, and template names come from
`benchbox.core.visualization.templates`.
Installing the `textcharts` Python dependency does not change this MCP
registration boundary. Any future bundle or proxy proposal requires a separate
product, security, and support decision with its own contract and acceptance
tests; no client should infer one from the dependency alone.

## SQL/DataFrame Result Bundle Invariants

SQL and DataFrame runs may use different engines and execution models, but their
exported result bundles must remain schema-comparable.

Fields that must match for the same benchmark, scale, query subset, and run
configuration:

- Result schema version.
- Benchmark identity: `benchmark.id`, `benchmark.name`, `benchmark.scale_factor`, and test type.
- Reproducibility config, excluding execution-mode-specific values: compression, seed, phases, and query subset.
- Presence of the standard phase keys.
- Query IDs in exported `queries`.
- Row counts when both modes report them.
- Validation state and absence/presence of exported error records.
- Execution metadata key shape, except conditional SQL translation metadata
  under `execution.translation`.
- Timing field names and units: run-level milliseconds and query-level `ms`.

Allowed differences:

- `execution.mode` and `config.mode` are expected to be `sql` vs `dataframe`.
- Platform name, version, client version, family, and driver metadata can differ.
- Phase status may differ when the DataFrame benchmark owns loading; for example,
  SQL can report `data_loading=SUCCESS` or `COMPLETED` while DataFrame reports
  `SKIPPED`.
- Optional `tables` blocks can be absent when a DataFrame benchmark manages or
  skips generic loading.
- `execution.translation` is SQL-only additive metadata and may appear when SQL
  dialect translation was attempted. DataFrame bundles are not expected to emit
  matching translation metadata.
- `config.query_parameters` is DataFrame-only for now: a TPC-DS DataFrame run
  lists, query by query, which queries it bound to the `dsqgen -LOG` values of
  the SQL power test's `-RNGSEED` for each stream (queries with a parameter
  adapter) and which ran on `default_parameters.yaml`. SQL bundles do not
  record it.
- Exact timing values must not be compared across modes.
- `config.query_parameters` records the TPC-H substitution parameter convention.
  Power runs and supported DataFrame runs use `qgen -d` without a seed, or
  `qgen -r (seed + 1000 * stream_id)` with a seed. Standard SQL uses `qgen -d`.
  SQL Throughput uses `base_seed + 1001 * stream_id + query_position`, with
  a default base seed of 42 and positions starting at zero in each stream's
  permutation. TPC-H and TPC-DS SQL Throughput streams are numbered 1 to S,
  as in the specifications; stream 0 is the Power stream. With up to 40
  throughput streams none repeats it. With more, the TPC-H permutation wraps
  and stream 41 repeats the power ordering; the stream count is not capped.
  The throughput phase records this basis under
  `phases.throughput_test.stream_numbering`. Combined SQL lists the requested phases separately; refresh
  functions do not use qgen parameters. Unsupported harnesses that fall back
  to Standard SQL record the default parameters.

## Result Model Extension Policy

`BenchmarkResults` is an internal producer model; schema-v2 result JSON is the
compatibility boundary. Exported keys are not moved or removed without a
migration that covers the loader, validator, explorer, MCP, and public
submission.

Platform-specific structured data should use the narrowest existing exported
location first:

- Platform identity, deployment, cloud, compute, storage, and raw platform
  details serialize under the `platform` block.
- Lifecycle stage summaries serialize under `phases.<stage>`.
- Cross-engine or cross-platform comparisons serialize under `comparisons`.
- Invocation and validation metadata serialize under `execution` and `summary`.
- New top-level keys require a public-contract update, schema-validator update,
  loader behavior, and explorer input policy review.

A future `platform_extensions` block is acceptable only for additive structured
data that has no existing canonical block. Existing fields may move there only
with an alias period: old exported keys keep loading and writing until all
documented consumers are migrated. Unknown nested keys inside documented blocks
may be ignored by read-model consumers, but unknown top-level keys remain
schema-governed and must not bypass schema-v2 validation.

Current extension inventory:

| Internal field/source | Current exported key | Loader behavior | Downstream consumers | Recommended action |
|---|---|---|---|---|
| `native_comparison` | `comparisons.native_duckdb` | Reconstructs `NativeComparison` from `comparisons.native_duckdb`. | Result loader/exporter; public schema validation now accepts the producer key; explorer ignores the comparison block. | Keep exported key; do not move before a comparison read-model design exists. |
| `ExecutionPhases.migration` | `phases.migration` | Reconstructs summary-level `MigrationPhase`; per-table stats are intentionally not serialized. | Result loader/exporter; explorer reads phase durations from `phases.*`. | Keep as canonical lifecycle-stage location. |
| `SetupPhase.statistics_gathering` | `phases.statistics` | Not reconstructed (setup sub-phases serialize as flat summaries); omitted entirely when the opt-in statistics phase did not run. | Result exporter; explorer reads phase durations from `phases.*`; `stats_mode` (`explicit` / `auto-on-load` / `unsupported`) records where statistics time landed. `stats_lifecycle` (`reset` / `unsupported` / `persist`, opt-in via `--stats-reset`/`--no-stats-reset`) and `per_table_ms` (opt-in via `--stats-per-table-timing`) are both additive and omitted when the corresponding control was not used. | Keep as canonical lifecycle-stage location; legacy runs without the phase (or without the reset/per-table controls) stay byte-identical. |
| `SetupPhase.post_load_maintenance` | `phases.post_load_maintenance` | Not reconstructed (setup sub-phases serialize as flat summaries); omitted entirely when no post-load tuning operation ran. | Result exporter; explorer reads phase durations from `phases.*`. `duration_ms` is the measured time of the post-load tuning hooks across all tables (for example ClickHouse `OPTIMIZE`, Redshift `ANALYZE` with `auto_analyze` off, Databricks Delta `OPTIMIZE`, Snowflake `RESUME RECLUSTER`) and is excluded from `phases.data_loading.duration_ms`; `tables_processed` counts distinct tables. | Keep as canonical lifecycle-stage location; legacy runs without the phase stay byte-identical. |
| Standalone pg_mooncake migration script | Top-level `migration` in script-emitted payloads | Not reconstructed by result loader. | Script-local reports only; not a canonical schema-v2 result extension. | Leave separate or convert to `phases.migration`; do not generalize this top-level key. |
| `platform_info`, `platform_metadata`, `platform_raw_config`, `platform_raw_metadata` | `platform.config`, `platform.raw_config`, `platform.raw_metadata` | Reconstructs platform info and raw metadata blocks. | Loader/exporter, anonymizer, explorer platform/version extraction. | Keep; move internally only if exported keys remain stable. |
| `platform_deployment`, `platform_cloud`, `platform_compute`, `platform_storage` | `platform.deployment`, `platform.cloud`, `platform.compute`, `platform.storage` | Reconstructs the normalized platform facets. | Loader/exporter, environment compatibility tests, explorer environment facets. | Keep as canonical platform facets. |
| `execution_context` | `execution` and selected `config` fields | Loader preserves selected execution metadata, not the full internal context. | CLI/MCP/exporter/explorer metadata consumers. | Keep selected exported fields; expand only by explicit schema policy. |
| `_benchmark_id_override` | `benchmark.id` | Reconstructs from `benchmark.id`. | Filename builder, loader, explorer IDs, result parity tests. | Keep internal compatibility field until builder and loader identity handling are redesigned. |
| `client_link` | `environment.client_link` | Reconstructs client locality and statement overhead metadata. | Result loader/exporter, validation, results explorer read model. | Safe non-identifying locality metrics (`client_region`, `client_cloud`, `statement_overhead_ms`); no raw IP, hostnames, or ports are published. |

### Client Link Locality Metadata

`environment.client_link` metadata discloses client-to-platform locality and statement overhead probe measurements. All published fields (`client_region`, `client_cloud`, and `statement_overhead_ms`) are safe non-identifying locality metrics; no raw IP, hostnames, or ports are published.

## Translation and Validation Mode Policy

SQL translation is mode-aware. Interactive/local runs may fail open by returning source SQL with a
warning, but CI, publishing, compatibility governance, and any caller making a
public correctness claim must use strict translation or treat fallback metadata
as uncertainty.

| Mode | Translation failure policy | Result-bundle policy | Consumer expectation |
|---|---|---|---|
| Interactive/local default | Fail open and warn. | `execution.translation.status="fallback"` when translation falls back; `summary.validation="uncertain"` if the result otherwise looked clean. | Users can keep experimenting, but the bundle is not a clean correctness claim. |
| CI and compatibility governance | Fail closed with CLI `--strict-translation`, or by setting `strict_translation=true`, `translation_strict=true`, or `sql_translation_strict=true` in benchmark/runtime options. | Strict failures raise before a result is published as successful. | Gates should fail rather than accept untranslated SQL. |
| Publishing and explorer ingestion | Reject non-clean validation statuses or translation fallback metadata. | `not_run`, `not_validated`, `unknown`, and `uncertain` are non-clean validation statuses; `execution.translation.status="fallback"` and `"failed"` are non-clean translation statuses. | Hosted/public views must not rank or present unchecked/fallback runs as validated results. |

Translation metadata is exported under `execution.translation` without a schema
version bump because it is an additive field inside the existing execution
block. It records strict mode, aggregate status, attempt counts, translators,
source/target dialects, warning/error categories, and compact grouped outcomes.
`summary.validation="passed"` is reserved for runs with evidence that validation
actually ran. When no validation record or validation details exist, lifecycle
finalization labels the result `not_run` instead of `passed`.
Post-load validation that is not applicable because an adapter does not expose
connection validation hooks records no validation stage; exceptions after a
validation attempt remain failed validation stages.
Failed per-query validation, including a warm-up query, prevents a clean pass
and makes the CLI report failure. Aggregate query counts and timings continue
to describe measurement executions.

TPC-H SF1 answer files use `qgen -d` defaults. No numeric seed identifies that
parameter set. Q11, Q16, Q18 and Q20 retain exact answer-file row-count checks
for the defaults; other substitution parameters use the existing query-specific
range or loose checks. This does not change value-comparison tolerances.
`set_reference_seed_context` records this choice for the current thread:
`True` selects exact validation for the defaults, `False` selects the existing
query-specific range or loose checks, and `None` preserves exact validation
when the parameter context is unknown.
The bounded correctness-gate digest snapshot records the qgen seed it was
generated with; the gate and its regeneration reuse it. The current snapshot
records `reference_seed: null`, so both run qgen `-d` without a seed.
The snapshot detects regressions against DuckDB and is separate from the
official answer files.
