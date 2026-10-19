# BenchBox Results Platform Product + Architecture Strategy

**Created:** 2026-03-29
**Revised:** 2026-04-15 (see [Primary Surface Revision](#primary-surface-revision-2026-04-15))
**Originating TODO:** `productize-result-publishing-and-artifact-sharing`

## Executive Summary

BenchBox should not treat local artifact publication, hosted result submission,
and public result analysis as one feature. They are adjacent, but they have
different product contracts, trust models, and operational requirements.

Recommended split:

1. `benchbox publish`
   Local/cloud artifact publication. Publishes canonical result bundles to
   local or cloud storage (S3, R2, etc.). Does not interact with the hosted
   results platform or public corpus.
2. `benchbox submit`
   Public corpus contribution. Packages a canonical bundle with a submission
   manifest and either prepares a PR against `results-data/` (Phase 2) or
   uploads directly to the hosted API (Phase 3).
3. `benchbox.dev/results/`
   A static-first public explorer for browsing, comparing, and analyzing
   curated public results.

### Phase 1 Status: Launched (2026-04-04)

Phase 1 launched on 2026-04-04. The launch corpus had 6 maintainer-run bundles
across 2 cohorts (TPC-H SF 0.01 and SSB/`star_schema` SF 0.01), each with ≥3
platforms. As of 2026-04-12, the repository corpus has expanded to 12 bundles
across 4 cohorts by adding SF 0.1 for both benchmark families. All launch
criteria from the checklist below were met.

### Revised Launch Phases

| Phase | Goal | Write Path | Infrastructure | Priority |
| --- | --- | --- | --- | --- |
| **1: Static Explorer MVP** | Curated seed corpus + read-only explorer at `benchbox.dev/results/` | Maintainer-only: CI-generated results committed under `results-data/` in this repo | Static only: GitHub Pages, no API, no auth, no hosted services | **Ship first** |
| **2: Community Contributions** | Community-submitted results via PR-based workflow | PRs against `results-data/` in this repo with CI validation + automated ingestion | Still static: GitHub Actions validates + merges + rebuilds; extract to a dedicated data repo only if churn justifies it | **Ship when Phase 1 UX is proven** |
| **3: Hosted Platform** | Self-service submission API, org/team spaces, richer features | Hosted API + object storage + async ingest | API server, metadata DB, auth, rate limiting, moderation | **Only if demand warrants the operational burden** |

### Key Architecture Decision

**Phase 1 and Phase 2 require zero backend services.** The entire read path is
static (derived JSON manifests + DuckDB/Parquet snapshots served via GitHub
Pages). The write path is git + CI/CD inside this repository
(`results-data/` → transform → build → deploy).

A hosted API (Phase 3) is explicitly deferred. The "submit via PR" model used by
many successful open-source benchmark databases (e.g., js-framework-benchmark,
ClickBench contribution model) proves that community contributions scale well
without a custom API until volume demands one.

## Product Intent and Positioning

### Core Hypothesis

"People want to browse and compare public benchmark results across platforms."
This is the primary value proposition that Phase 1 must validate.

### Target Audience

The broader data/analytics community - not just existing BenchBox users. The
explorer is a credibility and marketing play: transparent, reproducible,
multi-benchmark results that visitors can explore and compare themselves.

### Differentiator vs ClickBench

ClickBench covers a single workload in a single format. BenchBox's explorer
differentiates on three axes:

1. **Multi-benchmark coverage** - TPC-H, TPC-DS, SSB, and future benchmarks in
   one place, not siloed sites
2. **Rich per-query detail** - execution plans, tuning configurations, validation
   status, companion files
3. **Reproducibility** - any published result can be re-run with `benchbox run`
   using the same parameters

ClickBench was considered and rejected because its format cannot capture what
BenchBox measures (items 2 and 3 above).

> **Note (2026-04-14):** Axes 2 and 3 are currently aspirational. The Phase 1
> explorer exposes a narrow read model (11 fields; see Fidelity Gaps section below)
> that does not yet surface tuning config, execution plans, validation status,
> execution mode, or cost data. These must be realized before the differentiation
> claim is credible to a visitor who clicks through to a result detail page. The
> fidelity gap TODOs (`explorer-extend-manifest-and-pipeline`,
> `explorer-add-tuning-config-visibility`, `explorer-add-methodology-disclosure`)
> address this.

### Explorer as Dynamic Tool

> **Superseded 2026-04-15.** The "dynamic comparison tool, not a curated
> leaderboard" framing was correct as a rejection of vanity-ranking pages
> but wrong as a rejection of the matrix leaderboard pattern. See
> [Primary Surface Revision](#primary-surface-revision-2026-04-15). The
> revised position: **the per-benchmark primary surface is a ClickBench-style
> Platform × Query matrix leaderboard**, with the dynamic Compare page as
> a secondary deeper-analysis surface. Both ship; the matrix is the
> landing view.

The explorer is a **dynamic comparison tool**, not a static shootout page.
Visitors pick benchmark, scale factor, and platforms to build their own
comparisons. This is the core UX - not a curated leaderboard.

### Corpus Size and DuckDB-WASM Justification

The benchmark × platform × scale matrix is large even at launch. Phase 1 should
cover most supported benchmarks and platforms at limited scale factors. DuckDB-WASM
is justified because:

- The corpus needs to be large enough for a dynamic tool to be useful
- It will grow quickly as new platforms and benchmarks are added
- It demonstrates BenchBox's DuckDB expertise (audience alignment)

The primary corpus criterion is **depth per benchmark**: each included benchmark
must have ≥3 comparable platforms at the same scale factor. The ≥30 total bundle
count is a secondary estimate that falls out of the coverage matrix, not an
independent target. Depth is the right primary target because the core deliverable
is a comparison tool - a cohort needs multiple platforms at the same scale to be
meaningful, regardless of total bundle count.

### Visual Identity and Build Pipeline

The explorer is a standalone Vite app with its own build pipeline, but shares the
benchbox.dev visual identity (header, nav, styling). It is not embedded in the
docs or blog - it is a distinct feature of the site.

### SEO

Search engine discoverability is nice-to-have but not a launch blocker. Users
will primarily arrive through benchbox.dev directly. Pre-rendering can be added
post-launch if organic traffic becomes a goal.

### Timeline

No hard deadline or external event. Quality over speed.

### Brand Decision

**Resolved 2026-04-03**: The results explorer lives at `benchbox.dev/results/` under
the BenchBox brand. See
[`results-explorer-brand-ownership.md`](results-explorer-brand-ownership.md) for
the full decision rationale.

**Summary**: Oxbow Research has no independent web presence, domain, or active product
identity - it was an earlier brand concept that was deliberately separated from
BenchBox in git history. The explorer's core value proposition (reproducible results,
`benchbox run` re-execution) is inseparable from BenchBox, making BenchBox placement
the coherent choice. The working default is confirmed; no changes to existing
scaffolding or CI/CD are needed.

## Primary Surface Revision (2026-04-15)

*Added following a strategic review of the explorer vs ClickBench paradigm
and an adversarial review of the initial proposal.*

### What changed and why

The 2026-04-14 fidelity work identified that the explorer dropped too many
bundle fields in its derived read model. The 2026-04-15 review went one
level up: **the UX paradigm itself is wrong**. The current explorer is
browse-first (land on home → navigate to benchmark → get a sorted list →
maybe click Compare). ClickBench is compare-first: the landing view for a
benchmark is already the cross-platform answer.

That is not a cosmetic difference. It is the product definition. The
revised product position:

> The per-benchmark landing view is a Platform × Query matrix leaderboard
> (ClickBench-style). The dynamic Compare page remains as a secondary
> surface for deeper per-query analysis. The detail page remains as the
> reproducibility-story surface. All three ship.

### Why the original "matrix only, dynamic only" framings were both wrong

The original strategy doc rejected a "curated leaderboard" page. That
rejection still stands - there is no vanity-ranking page in the product.
But a **matrix leaderboard** is not a vanity ranking: it is a cohort-aware,
per-query, per-column-normalized view where ranking emerges from the data
rather than from editorial weighting. It is the comparison tool, rendered
densely, rather than a competitor to it.

The initial redesign proposal (see the companion review) was correct in
its main recommendation but wrong in several specifics. The final adopted
shape incorporates the following corrections:

| Area | Initial proposal | Final adopted position |
|---|---|---|
| Artifact key | `(benchmark, scale)` | `(benchmark, scale, phase)` - phase cannot be silently collapsed |
| Color scale | linear min-max | log10 ratio-to-fastest, clamped at 10× |
| Primary ranking metric | geomean_ms default | per-family registry: `power_score` for TPC-H/TPC-DS, `geomean_ms` fallback |
| Trust default | filter to `maintainer-run` only | show all tiers with visible badges; trust is an optional filter |
| Compare URL | full result_ids in query string | short hash IDs with full-form fallback |
| Home page | recent results list | cross-benchmark meta-leaderboard (avg-rank aggregation) + recent results secondary |
| Accessibility | unspecified | aria-labels on every data cell, keyboard nav, reduced-color mode, axe-core in CI |

### Revised derived read model

In addition to the artifacts already specified in [Derived Read Model
Schema](#derived-read-model-schema), the pipeline emits:

1. **`benchmarks/{benchmark}_sf{sf}_{phase}.json.gz`** - one file per
   unique `(benchmark, scale_factor, phase)` tuple, containing the full
   cross-platform query matrix plus per-platform `power_score`,
   `geomean_ms`, `cost_usd`, `trust_label`, `tuning_mode`, and
   `short_id`. Gzip compressed on write.

2. **`short_ids.json`** - lookup table `{short_id: full_result_id}` to
   allow compact Compare URLs without breaking existing long-form URLs.

3. **`meta_leaderboard.json`** - cross-benchmark rank aggregation used
   by the home page. Ranking-eligible platforms with non-null primary metrics
   are ranked within each cohort (cohorts with ≥2 rankable platforms only),
   then aggregated as simple mean of ranks across appearances. Visible but
   unranked variants may be preserved with `rank: null`, but they do not affect
   cohort totals, best/slowest speedups, or average ranks. No weighted
   composite score.

### Revised UX surfaces

| Surface | Revised role |
|---|---|
| Home (`/results/`) | Cross-benchmark meta-leaderboard is the hero panel. Recent results list drops to secondary position. |
| Benchmark landing (`/results/tpch/`) | **Matrix leaderboard** as the default view. List view available via toggle for mobile. |
| Compare (`/results/compare?ids=...`) | Unchanged in role; accepts short IDs. Reached from matrix-row checkboxes. |
| Detail (`/results/r/<id>`) | Unchanged. Surface for methodology disclosure and reproducibility. |

### What explicitly does not change

- **No branded leaderboard page** remains the rule. The matrix leaderboard
  is cohort-aware (single scale, single phase, same benchmark); it is not
  a cross-context vanity ranking.
- **All three differentiators remain** (multi-benchmark coverage, rich
  per-query detail, reproducibility). The matrix leaderboard strengthens
  differentiator #1 by making the multi-benchmark story visible on the
  home page.
- **Cohort-breaking guardrails** (benchmark, scale, tuning mode,
  execution mode, phase) remain enforced. The matrix view exposes all
  five as explicit axes in the filter bar instead of hiding them.

### TODO cluster

Addressed by the `explorer-` TODO cluster added 2026-04-15. See [TODO
Cluster and Priority](#todo-cluster-and-priority) for the full table.

## Explorer Fidelity: Known Gaps and Requirements

*Added 2026-04-14 following a post-launch audit of explorer vs CLI divergence.*

### Canonical Duration Metric

The explorer currently uses `total_duration_s` (wall-clock sum from the bundle's
`run.total_duration_ms`). The CLI uses **geometric mean of per-query times** as
the primary comparison metric for OLAP workloads. These produce different numbers
for the same result, making cross-referencing between CLI output and the explorer
confusing and misleading for results with different query counts.

**Decision:** The explorer's primary comparison metric must be **geometric mean
of per-query execution times (`geomean_ms`)**, computed at pipeline build time
and stored in both the manifest and DuckDB schema. Wall-clock total duration may
be surfaced as a secondary metric with a clear label, but it must not be the
default sort/compare axis. This aligns with CLI behavior and with standard OLAP
benchmarking practice.

**Refinement (2026-04-15):** The geomean-is-canonical decision applies where
the benchmark family has no published aggregate metric (ClickBench, SSB,
custom benchmarks). For **TPC-H and TPC-DS**, the canonical metric is
`power_score` as defined by the TPC specification - users arriving from
published TPC results expect to see that number. The explorer pipeline
exposes a per-family `RANKING_METRIC_BY_FAMILY` registry
(`_project/scripts/explorer_pipeline/models.py`) that selects the primary
metric per benchmark family and serializes the choice into each
`BenchmarkSummary` artifact's `ranking` field - no ranking logic lives in
TypeScript. `geomean_ms` / `display_geomean_ms` remains as the universal
secondary metric shown alongside. Implemented in `explorer-align-ranking-metric-with-tpc-standards`.

### Comparability Model

Two results are **comparability-breaking** on the following dimensions - the
compare view must warn or hard-block when they differ:

| Dimension | Breaking? | Action |
|-----------|-----------|--------|
| Benchmark family | Yes (hard-block) | Already enforced |
| Scale factor | Yes (hard-block) | Already enforced |
| Execution mode (SQL vs DataFrame) | Yes (warn) | Not yet surfaced |
| Tuning mode (tuned vs notuning) | Yes (warn) | Not yet surfaced |
| Query subset vs full benchmark | Yes (warn) | Not yet surfaced |
| Platform version | No (label) | Partially surfaced |

The explorer cannot enforce the tuning and execution-mode warnings until those
fields reach the manifest. See `explorer-extend-manifest-and-pipeline`.

### Pipeline Fidelity Gap

The schema-v2 bundle contains 13 blocks and 50+ fields. The Phase 1 pipeline
transformer emits **11 manifest fields**, silently dropping:

| Category | Dropped Fields | Impact |
|----------|---------------|--------|
| Execution metadata | `execution_mode` (SQL/DataFrame) | Can't distinguish modes |
| Tuning config | `config.tuning_mode`, full config detail | No tuning UI, can't cross-compare |
| Platform identity | `platform.version`, `engine_version` | Only driver version surfaced |
| Run config | `config.platform_options`, `config.compression` | No config disclosure |
| Phase timing | All `phases.*` breakdown | Can't see load vs query time |
| Cost | `cost.total_usd` | No cost analysis |
| Validation | `summary.validation` | No validation status visible |
| Test type | Power vs throughput distinction | Can't label test type |
| Query granularity | `run_type`, `iter`, `stream` per timing | Can't identify warmup vs measurement |

The DuckDB `results` table currently has 11 columns. It should grow to ~20 to
enable meaningful client-side filtering. See `explorer-extend-manifest-and-pipeline`.

### Methodology Transparency

A visitor to a result detail page currently cannot determine:
- Whether `total_duration_s` is a sum, mean, or median
- Whether the run used tuning (a configuration that can significantly affect
  results)
- Whether warmup queries are included in the timing
- What hardware the result was produced on beyond OS/arch/CPU count

Every result detail page must include a **methodology disclosure panel** that
states the aggregation method, tuning state, execution mode, and key environment
parameters. See `explorer-add-methodology-disclosure`.

### Chart Parity Targets

The CLI ships 15 chart types; the explorer ships 2 inline SVG components. Full
parity is not required, but the following gap is strategically significant for
the "dynamic comparison tool" claim:

| Missing chart | Value | Priority |
|---------------|-------|----------|
| Normalized speedup (log scale, baseline-relative) | Primary comparison metric for large perf deltas | High |
| Diverging bar (per-query regression/improvement) | Makes "which queries got slower?" obvious | High |
| Phase breakdown (stacked load vs query) | Distinguishes load bottleneck from query bottleneck | Medium |
| Percentile ladder (P50/P90/P95/P99) | Identifies tail-latency outliers | Medium |
| Cost scatter | "Cost vs performance" is a primary analyst question | Low (Phase 2+) |

See `explorer-add-comparison-charts`.

## Phase 1 MVP Definition

### Primary Deliverable: Dynamic Comparison Tool

The core UX of Phase 1 is a **dynamic comparison tool** where visitors pick a
benchmark, scale factor, and platforms, then see a side-by-side query-level
timing breakdown. This is the feature that differentiates the explorer from a
file listing, makes users come back and share links, and validates the core
hypothesis ("people want to browse and compare public benchmark results").

All other Phase 1 deliverables (home page, browse pages, detail pages) exist to
support navigation into and out of comparisons. They are necessary but secondary.

### What Ships

| Component | Role | Description | Done When |
| --- | --- | --- | --- |
| **Compare view** | **Primary** | Dynamic comparison tool: pick benchmark/scale/platforms, see side-by-side query-level timing breakdown with cohort validation | Compare works for any compatible results in the corpus; shareable URLs |
| **Seed corpus** | Data | Result bundles with depth per benchmark (many platforms at key scale factors) rather than just breadth | Bundles exported, validated, committed under `results-data/bundles/` |
| **Static build pipeline** | Data | Transforms canonical schema-v2 bundles into navigation manifest JSON, per-result detail JSON, and DuckDB database snapshot | Pipeline runs in CI, output deployed to GitHub Pages |
| **Explorer home + browse** | Navigation | Landing page with summary cards; benchmark and platform index pages with filterable result lists and "compare" checkboxes | Users can navigate to any result and select results for comparison |
| **Result detail page** | Supporting | Stable URL per result showing metadata, query timings, validation status, and raw bundle download | Detail page works for all seed corpus results |
| **DuckDB-WASM filtering** | Interaction | Client-side filtering by benchmark, platform, scale factor, date range | Filters work over the full corpus |
| **GitHub Pages integration** | Deployment | Explorer builds and deploys alongside existing landing + docs + blog | Single `git push` to `main` deploys everything |

### What Does NOT Ship in Phase 1

- No user accounts, authentication, or authorization
- No hosted submission API or `benchbox submit` command
- No anonymous or community uploads
- No branded "leaderboard" page, but cohort views may be sorted by total duration
  or geometric mean - this is a sorted table within a validated cohort, not a
  cross-context ranking claim
- No organization accounts or private workspaces
- No moderation, trust labels, or abuse controls (not needed - corpus is maintainer-curated)

### Launch Criteria

1. Seed corpus has sufficient depth for the dynamic comparison tool to show
   meaningful cross-platform comparisons: each included benchmark has ≥3
   comparable platforms at the same scale factor
2. Compare view is the primary entry point and works end-to-end: select
   benchmark/scale/platforms → see query-level timing breakdown → share URL
3. All explorer pages render correctly with real data
4. DuckDB-WASM filtering works in Chrome, Firefox, Safari
5. GitHub Pages deployment succeeds end-to-end from CI
6. Explorer is navigable from the existing benchbox.dev site header/nav
7. Brand ownership decision is resolved and reflected in domain/hosting/identity

## Phase 2: Community Contributions (Deferred)

### Model: Submit via Pull Request

Instead of building a hosted API, Phase 2 uses a PR-based contribution model:

1. Contributor runs `benchbox submit --output ./submission/` which packages
   the canonical schema-v2 bundle with a submission manifest (contributor
   metadata, benchmark context, optional notes)
2. Contributor opens a PR against this repository touching `results-data/`
3. GitHub Actions CI validates: schema conformance, bundle integrity (hash check),
   cohort compatibility, and basic sanity checks (no absurd timings, valid platform)
4. Maintainers review and merge
5. Merge triggers rebuild of derived read models + redeploy of explorer

A dedicated `benchbox-results` repository remains an optional future extraction
if corpus size or contribution volume starts to overwhelm the main repo. It is
not a Phase 1 or early Phase 2 requirement.

### Why PR-Based Before API-Based

| Concern | PR model | API model |
| --- | --- | --- |
| Auth | GitHub identity (free) | Custom auth system (build + operate) |
| Moderation | PR review (familiar, auditable) | Custom moderation UI (build + operate) |
| Abuse prevention | PR rate = human rate | Rate limiting, quotas, captchas |
| Trust labels | Commit author = attribution | Custom trust promotion workflow |
| Operational cost | Zero (GitHub Actions) | API server + DB + storage + monitoring |
| Scalability ceiling | ~100s of submissions/month | Thousands+/month |

The PR model is sufficient until submission volume exceeds what maintainer review
can handle. That threshold is unlikely in the first year of a results platform.

### What Ships in Phase 2

- `benchbox submit` command to package and submit results to the public corpus
- Submission manifest schema (contributor, context, notes)
- CI validation workflow for the data repository
- Trust labels in explorer: "maintainer", "community-submitted", and
  "vendor-supplied" (vendor-produced results, ranked but distinctly badged and
  maintainer-assigned so they cannot be self-asserted)
- Funding disclosure (employer / personal / free-trial / vendor-sponsored /
  grant / unspecified), orthogonal to the trust label
- Contributor guidelines documentation

## Phase 3: Hosted Platform (Deferred)

Phase 3 is explicitly contingent on Phase 2 reaching a scale where the PR model
becomes a bottleneck. Indicators that Phase 3 is needed:

- Submission volume exceeds ~50/month sustained
- Maintainer review becomes a sustained bottleneck
- Users need private/unlisted results (not possible in a public repo)
- Organization/team features are requested by paying or strategic users

### What Would Ship in Phase 3

- Hosted submission API at `api.benchbox.dev`
- Authentication (API keys, OAuth)
- Private and unlisted visibility states
- Automated trust promotion workflow
- Rate limiting, quotas, abuse controls
- Organization/team spaces
- Richer APIs and embedded widgets

The planned Phase 3 operating model is documented in
[`results-phase-3-runbook.md`](results-phase-3-runbook.md).

### Cost and Operational Complexity

A hosted platform requires:

| Component | Estimated Cost | Operational Burden |
| --- | --- | --- |
| API server (e.g., Fly.io, Railway) | $20-100/mo | Deployment, monitoring, on-call |
| Metadata database (Postgres) | $15-50/mo | Backups, migrations, scaling |
| Object storage (S3/R2) | $5-20/mo | Lifecycle policies, access control |
| Auth provider (Auth0/Clerk) | $0-25/mo | Token management, session handling |
| Monitoring (Sentry, metrics) | $0-30/mo | Alert triage, incident response |

**Total: $40-225/month + significant engineering time.** This is only justified
if the results platform becomes a core product with sustained community usage.

## Current BenchBox Constraints

| Constraint | Evidence | Strategy implication |
| --- | --- | --- |
| Canonical results already exist as schema-v2 bundles with companion files | `benchbox/core/results/exporter.py` | All downstream paths ingest the real exported bundle, not a second payload |
| Public site is currently static GitHub Pages assembled from landing + docs + blog | `.github/workflows/docs.yml`, `docs/conf.py` | The public explorer must be a static subsite - no server dependency |
| BenchBox already hints at a hosted service contract | `_project/_archive/specs/cli/config.md` documents `submit_to_service` and `service_url` (archived) | CLI public submission (`benchbox submit`) is a legitimate future direction, but Phase 1 does not require it |
| Existing publishing is process-local | `benchbox/core/publishing/bundle_publisher.py`, `benchbox/core/publishing/store.py` (the earlier `artifacts.py`/`permalink.py` prototype was removed in v0.2.1) | The current bundle publisher does local/cloud artifact publication only; it is not the hosted service architecture |

## Reference Matrix

| Reference | Strong pattern | What BenchBox should copy | What BenchBox should not copy |
| --- | --- | --- | --- |
| Geekbench | Stable public result pages, comparison flows, account-linked online result management, offline vs online distinction | Stable result detail pages, obvious compare actions, explicit separation between local results and hosted results | A closed scoring model or consumer-device-centric assumptions |
| CloudSpecs | Static browser app, GitHub Pages hosting, browser-side DuckDB-WASM analysis over a curated dataset | **Phase 1 reference architecture**: static-first explorer, DuckDB-WASM for browser-side analytics, downloadable snapshots, reproducible analysis artifacts | No-write-path assumptions for the whole product |
| OpenBenchmarking | Centralized submission ecosystem, aggregate comparison, rich result metadata, public/private policy | Cohort-aware comparison, richer metadata, trust labels, aggregate analysis | Day-1 open public firehose without curation, moderation, or clear verification state |
| ASV | Results stored as files, publish to a static website, precomputed regression views | Derived read models published as static assets, regression/change-oriented views, offline-friendly read path | Limiting the product to codebase-over-time regressions only |

## Product Boundary

BenchBox needs three explicit user contracts, but they do NOT all ship at once.

| Contract | Primary actor | Phase | Runtime boundary |
| --- | --- | --- | --- |
| Publish (local/cloud) | BenchBox user sharing files or mirroring artifacts | Independent (existing TODO) | CLI + local/cloud storage backend |
| Explore | Reader/analyst comparing public results | **Phase 1** | Static subsite on GitHub Pages |
| Submit (PR) | Community contributor adding results | **Phase 2** | `benchbox submit --output` → GitHub PR + CI validation |
| Submit (API) | Self-service submitter | **Phase 3** | `benchbox submit` → Hosted API + async ingest |

## Technology Recommendations for Phase 1

### Explorer Frontend Stack

| Choice | Recommendation | Rationale |
| --- | --- | --- |
| **Build tool** | Vite | Fast, modern, used by CloudSpecs reference. Produces optimized static bundles. |
| **Framework** | Vanilla TypeScript + Preact (or no framework) | Minimizes bundle size for a content-heavy site. Preact if component model helps; plain TS if it stays simple. |
| **Browser analytics** | DuckDB-WASM | BenchBox already has deep DuckDB expertise. Enables SQL-powered filtering, comparison, and ad-hoc analysis in the browser. Proven by CloudSpecs. |
| **Data format** | Static JSON manifests + DuckDB database file | JSON for navigation/SEO/fast page loads. DuckDB `.db` file for rich filtering and comparison. |
| **Routing** | File-based with real paths | `/results/tpch/`, `/results/duckdb/`, `/results/r/{result_id}` - not hash routing. Required for stable share URLs and SEO. |
| **Styling** | Tailwind CSS or minimal custom CSS | Consistent with modern static sites. Light enough for GitHub Pages. |

### Derived Read Model Schema

The static build pipeline transforms canonical schema-v2 bundles into:

1. **`manifest.json`** - Global navigation index. Target schema (the recorded planning annotations below distinguish
   fields previously marked as launched from required additions):
   ```json
   {
     "results": [
       {
         "id": "tpch-duckdb-sf1-20260315",
         "benchmark": "tpch",
         "platform": "duckdb",
         "scale_factor": 1.0,
         "run_date": "2026-03-15",
         "total_duration_s": 1.234,
         "geomean_ms": 56.2,
         "query_count": 22,
         "trust_label": "maintainer-run",
         "visibility": "public-curated",
         "driver_version": "1.1.0",
         "platform_version": "1.1.0",
         "execution_mode": "sql",
         "tuning_mode": "tuned",
         "tuning_hash": "abc123",
         "test_type": "power",
         "validation_status": "passed",
         "cost_usd": null,
         "bundle_path": "bundles/tpch-duckdb-sf1-20260315.json"
       }
     ],
     "benchmarks": ["tpch", "tpcds", "ssb"],
     "platforms": ["duckdb", "datafusion", "clickhouse-local", "polars-df"],
     "generated_at": "2026-03-29T00:00:00Z"
   }
   ```

   Recorded planning annotations:

   | Field | Status | Meaning |
   | --- | --- | --- |
   | `id` | Recorded as launched | — |
   | `benchmark` | Recorded as launched | — |
   | `platform` | Recorded as launched | — |
   | `scale_factor` | Recorded as launched | — |
   | `run_date` | Recorded as launched | — |
   | `total_duration_s` | Recorded as launched | wall-clock; secondary metric |
   | `geomean_ms` | Required addition | canonical comparison metric |
   | `query_count` | Recorded as launched | — |
   | `trust_label` | Recorded as launched | — |
   | `visibility` | Recorded as launched | — |
   | `driver_version` | Recorded as launched | — |
   | `platform_version` | Required addition | — |
   | `execution_mode` | Required addition | "sql" \| "dataframe" |
   | `tuning_mode` | Required addition | "tuned" \| "notuning" \| "auto" |
   | `tuning_hash` | Required addition | stable hash for cross-compare grouping |
   | `test_type` | Required addition | "power" \| "throughput" |
   | `validation_status` | Required addition | "passed" \| "failed" \| "skipped" |
   | `cost_usd` | Required addition | null if unavailable |
   | `bundle_path` | Recorded as launched | — |

2. **Per-result detail JSON** - Full query timings + metadata for result pages.
   Target query record:
   ```json
   {
     "id": "tpch-duckdb-sf1-20260315",
     "metadata": {
       "benchmark": "tpch", "platform": "duckdb", "environment": "...",
       "execution_mode": "sql",
       "tuning_mode": "tuned", "tuning_summary": "DuckDB default tuning profile",
       "platform_version": "1.1.0", "engine_version": null
     },
     "queries": [
       {"id": "Q1", "ms": 45.2, "rows": 4, "status": "passed",
        "run_type": "measurement", "iter": 1, "stream": 1}
     ],
     "summary": {
       "total_ms": 1234, "geomean_ms": 56.2,
       "passed": 22, "failed": 0,
       "validation_status": "passed"
     },
     "bundle_download": "bundles/tpch-duckdb-sf1-20260315.json"
   }
   ```

3. **`results.duckdb`** - DuckDB database for browser-side analysis:
   - `results` table: one row per result run - target ~20 columns (see manifest
     target above; all manifest fields should be filterable via DuckDB-WASM)
   - `queries` table: one row per query execution
     `(result_id, query_id, ms, rows, status, run_type, iter, stream)`
   - Enables: `SELECT * FROM queries WHERE benchmark='tpch' AND platform='duckdb' ORDER BY ms`

4. **`bundles/`** - Raw canonical schema-v2 bundles for download

### Compare URL Format

Compare views use query parameters for flexibility:
- `/results/compare?ids=tpch-duckdb-sf1-20260315,tpch-datafusion-sf1-20260315`
- Cohort validation happens client-side: same benchmark + same scale factor required
- **Cohort mismatch is a hard-block, not a warning.** The compare view refuses to render incompatible comparisons (different benchmark or different scale factor). Non-blocking differences (query subset, tuning mode) produce warnings but do not prevent rendering.

### Site Integration

The explorer is built as a standalone Vite app in `results-explorer/`. The
static read-model pipeline writes build inputs to `results-explorer/public/data/`;
the Vite build emits static files to `results-explorer/dist/`; the existing
GitHub Pages workflow then copies that output into `site/results/`.

Build flow:

```
results-data/ + static build pipeline → results-explorer/public/data/
results-explorer/dist/ + [landing page] + [sphinx docs] + [blog] → /site/ → GitHub Pages
```

Navigation integration: add "Results" link to the shared site header/nav.

## Architecture by Phase

### Phase 1 Architecture (Static Only)

```
CI benchmark runs → schema-v2 bundles in results-data/
                                        ↓
                          static build pipeline → results-explorer/public/data/
                                                  ↓
                                            Vite build (dist/)
                                                  ↓
                           GitHub Pages assembly copies dist/ → site/results/
                                                  ↓
                                   GitHub Pages (benchbox.dev/results/)
                                                  ↓
                                     Vite app + DuckDB-WASM in browser
```

No API. No database. No auth. No hosted services.

### Phase 2 Architecture (PR-Based Contributions)

```
Contributor: benchbox submit --output ./submission/
                ↓
        PR touching results-data/ in this repo
                ↓
        CI validates (schema, hash, cohort, sanity)
                ↓
        Maintainer reviews + merges
                ↓
        Same static build pipeline as Phase 1
```

Still no API. Still no hosted services. GitHub is the auth + moderation layer.

### Phase 3 Architecture (Hosted - Deferred)

```
benchbox submit → api.benchbox.dev → object store + metadata DB
                                            ↓
                                    async ingest + validation
                                            ↓
                                    derived read model rebuild
                                            ↓
                                    static explorer update
```

Only build this if Phase 2 PR volume exceeds maintainer capacity.

### Storage Layers (Phase 3 Only)

| Layer | Purpose | Properties |
| --- | --- | --- |
| Object store | Immutable raw bundle + companions | Content-addressable, versioned, durable |
| Metadata store | Submission, run, visibility, trust, cohort metadata | Queryable, transactional, auditable |
| Derived public store | Static projections for public reads | Rebuildable, cacheable, CDN-friendly |

### Result Identity

Result identity is phase-dependent:

| Phase | Identity Scheme |
| --- | --- |
| Phase 1 | `{benchmark}-{platform}-sf{scale}-{date}` - human-readable, derived from bundle metadata |
| Phase 2 | Same, plus contributor attribution from PR author |
| Phase 3 | Adds `bundle_hash` (content identity), `submission_id` (API identity), `result_id` (public identity) |

## Trust, Visibility, and Ranking

Trust complexity scales with phases:

| Phase | Trust Model |
| --- | --- |
| Phase 1 | All results are maintainer-curated. A simple "Maintainer Run" label is fine, but no richer trust model is needed. |
| Phase 2 | Two labels: **maintainer** (generated by BenchBox CI) and **community** (submitted via PR). Both public. |
| Phase 3 | Full trust tiers: private, unlisted, public-self-reported, public-curated, public-verified |

Comparison and ranking should be cohort-aware. Public pages must avoid mixing
incompatible runs across materially different contexts:

- benchmark family and version
- scale factor
- execution mode
- phase set
- query subset vs full benchmark
- tuning mode
- hardware or platform family where relevant

If a cohort is too heterogeneous for a clean ranking, the explorer should fall
back to filters and pairwise comparison rather than pretend the leaderboard is
authoritative.

**No branded "leaderboard" page.** Phase 1 browse views may sort results within
a validated cohort (e.g., by total duration or geometric mean), but this is
presented as a sorted table, not a ranked competition. Cross-context ranking
claims are never shown.

## Impact on Existing Publishing TODO

`productize-result-publishing-and-artifact-sharing` remains the BenchBox-local
artifact publication track. It owns `benchbox publish` workflows for copying
canonical result bundles to local/cloud storage. It does NOT own the results
platform or explorer.

## TODO Cluster and Priority

### Planning and Infrastructure

| TODO | Phase | Priority | Status |
| --- | --- | --- | --- |
| `define-results-platform-product-and-launch-strategy` | Planning | High | Done |
| `resolve-results-explorer-brand-ownership` | Planning | High | Done |
| `build-results-explorer-subsite-on-benchbox-dev` | Phase 1 | High | Done |
| `implement-results-compare-view` | Phase 1 | High | Done |
| `define-hosted-results-contract-and-governance-model` | Phase 2-3 prep | Medium | Done |
| `design-results-ingest-storage-and-derived-read-model` | Phase 3 | Medium | Not started |
| `integrate-benchbox-cli-submit-and-service-auth` | Phase 2-3 | Medium | Not started |
| `operate-results-platform-security-observability-and-abuse-controls` | Phase 3 | Low | Not started |

### Explorer Fidelity (added 2026-04-14)

These TODOs address the gaps identified in the post-launch audit. They are
sequenced: pipeline extension first (all others depend on the richer manifest),
then UI features that consume the new fields.

| TODO | Phase | Priority | Rationale |
| --- | --- | --- | --- |
| `explorer-extend-manifest-and-pipeline` | Phase 1.5 | High | Unblocks all other fidelity work; adds geomean_ms, execution_mode, tuning_mode, tuning_hash, platform_version, test_type, validation_status, cost_usd to manifest + DuckDB |
| `explorer-align-duration-metric` | Phase 1.5 | High | Fixes the CLI vs explorer metric discrepancy; geomean becomes the primary comparison axis |
| `explorer-add-tuning-config-visibility` | Phase 1.5 | Medium-High | Surfaces tuning config in detail pages and enables cross-tuning comparison |
| `explorer-add-methodology-disclosure` | Phase 1.5 | Medium-High | Adds "how this was measured" panel; required before differentiation claims are credible |
| `explorer-add-comparison-charts` | Phase 1.5 | Medium | Normalized speedup + diverging bar charts; closes the most visible CLI parity gap |
| `explorer-comparability-warnings` | Phase 2 | Medium | Warn/block when comparing results that differ on execution mode or tuning; depends on pipeline extension |

### Primary Surface Revision (added 2026-04-15)

These TODOs implement the ClickBench-style matrix leaderboard pivot
documented in [Primary Surface Revision](#primary-surface-revision-2026-04-15).
They are sequenced: artifact first, then matrix UI + ranking, then
trust/URLs, then home-page meta-leaderboard and a11y.

| TODO | Phase | Priority | Rationale |
| --- | --- | --- | --- |
| `explorer-emit-benchmark-summary-artifact` | Phase 1.6 | High | New derived artifact keyed on (benchmark, scale, phase) - foundation for the matrix leaderboard |
| `explorer-matrix-leaderboard-view` | Phase 1.6 | High | Replaces BenchmarkIndex sorted list with ClickBench-style Platform × Query matrix; log-ratio coloring; row-checkbox → Compare |
| `explorer-align-ranking-metric-with-tpc-standards` | Phase 1.6 | High | Per-family registry so TPC-H/TPC-DS rank by power_score; geomean fallback elsewhere |
| `explorer-surface-trust-tiers-as-badges` | Phase 1.6 | Medium-High | Trust tiers as visible badges (not a default hide filter); BenchBox's differentiator made explicit |
| `explorer-compare-url-short-ids` | Phase 1.6 | Medium-High | Short hash IDs avoid URL-length limits; backward-compatible |
| `explorer-cross-benchmark-meta-leaderboard` | Phase 1.6 | Medium | Home page hero: cross-benchmark rank aggregation - leans into multi-benchmark differentiator |
| `explorer-heatmap-accessibility-and-tests` | Phase 1.6 | Medium | aria-labels, keyboard nav, reduced-color mode, axe-core in CI |

## DuckDB-Only Browser Metric Contract (2026-04-18)

*Added as the W1 research gate for TODO `explorer-canonical-browser-duckdb-read-model`.
This section is the schema decision record, direct ingest contract, and cutover plan
that must be locked before any code changes in W2-W7.*

### Definition of Terms

- **Canonical Python reference computation** - the pipeline's own Python implementation
  inside `_project/scripts/explorer_pipeline/` plus read-only imports from
  `benchbox/core/results/`. TypeScript code, pre-calculated CLI JSON artifacts, and
  bridge artifacts are **never** a reference source.

- **Final-value parity** - the rendered user-visible value produced by the DuckDB-backed
  read path equals the canonical Python reference computation for the same inputs, within
  the per-metric tolerance declared in `_project/planning/visible_metrics.yaml`. Parity is
  NOT defined against whatever the CLI, a pre-calculated JSON artifact, or the current TS
  reduction libraries happen to emit today.

- **Source-fidelity parity** - raw fields copied into DuckDB equal the corresponding field
  in the committed JSON bundle verbatim.

- **Bridge artifact** - a metric-bearing JSON file emitted by the pipeline during the
  W2-W4 transition window so pages not yet migrated to DuckDB can still render. Bridge
  artifacts are scaffolding only, regenerated from the same pipeline pass that writes
  DuckDB, never hand-patched, and always subordinate to DuckDB when the two disagree.

### Call-Site Inventory

Every user-visible metric read, current data source, and DuckDB target:

| Surface | Current source | Metrics consumed | Target DuckDB table/view |
|---------|---------------|-----------------|--------------------------|
| `Home.tsx` | `manifest.json` via `getManifest()` | total_results, platform list, benchmark list, power_score, geomean_ms, run_date (Recent Results) | `results` |
| `Home.tsx` | `meta_leaderboard.json` via `getMetaLeaderboard()` | rank, metric_value, speedup_vs_best, avg_rank, n_cohorts | `meta_leaderboard` + `cohort_metadata` |
| `PlatformIndex.tsx` | `manifest.json` via `getManifest()` | power_score, geomean_ms, run_date, trust_label, tuning_mode | `results` (filtered by platform_id) |
| `BenchmarkIndex.tsx` | `manifest.json` via `getManifest()` | scale_factor list, phase list, display_geomean_ms (list view) | `results` (filtered by benchmark) |
| `BenchmarkIndex.tsx` | `benchmarks/*.json.gz` via `getBenchmarkSummary()` | timings (display_ms per query/platform), power_score, display_geomean_ms, is_ranking_eligible, percentile_stats, compliance_class | `benchmark_matrix_cells` + `benchmark_rankings` |
| `Compare.tsx` | DuckDB `short_ids` + detail/result queries | short_id → result_id mapping, display_geomean_ms, power_score, display_ms per query | `short_ids`, `result_detail_metrics`, `query_display_timings`, `results` |
| `Compare.tsx` | Legacy deleted: `compare/{hash16}.json`, `details/{id}.json` | no steady-state metric reads | DuckDB is canonical |
| `ResultDetail.tsx` | DuckDB detail queries | power_score, geomean_ms, display_geomean_ms, total_duration_s, display_timings (display_ms + sample_count), queries (raw timings), environment | `result_detail_metrics` VIEW + `query_display_timings` + `query_executions` |
| `Query.tsx` | DuckDB introspection | schema column names + types (column picker) | `duckdb_columns`/`DESCRIBE` |
| `Query.tsx` | `bench.results` in DuckDB | all `results` columns for workbench queries | `results` (already DuckDB - keep as-is) |

**Bootstrap JSON surviving after W7 (non-metric only):**

If `manifest.json` survives, its top-level keys must be a strict subset of
`{routes, navigation, build_meta, schema_version}`. Any metric-looking key
(`benchmark_summary`, `result_count`, `power_score`, `geomean_ms`, etc.) fails G-8.
The preferred outcome is to delete `manifest.json` entirely in the final W4 slice
and derive all navigation from DuckDB.

`results_schema.json` is deleted in W3 (replaced by DuckDB introspection).

### Ten Canonical DuckDB Tables and Views

All ten surfaces must exist with stable schemas before W3 (frontend migration) begins.
The frozen DDL is in `_project/design/browser-duckdb-schema.sql`.

| # | Name | Kind | Serves |
|---|------|------|--------|
| 1 | `results` | Base table | Home, PlatformIndex, BenchmarkIndex list, Query workbench |
| 2 | `query_display_timings` | Base table | ResultDetail, BenchmarkIndex matrix, Compare |
| 3 | `query_executions` | Base table | ResultDetail individual samples |
| 4 | `result_detail_metrics` | G-11 view | ResultDetail (projects results + environment tables) |
| 5 | `benchmark_matrix_cells` | Base table | BenchmarkIndex matrix heatmap |
| 6 | `benchmark_rankings` | Base table | BenchmarkIndex matrix/ranks views |
| 7 | `platform_index_rows` | G-11 view | PlatformIndex (projects `results`) |
| 8 | `cohort_metadata` | Base table | Home meta-leaderboard (cohort + per-platform ranks) |
| 9 | `meta_leaderboard` | Base table | Home meta-leaderboard (cross-cohort platform summary) |
| 10 | `short_ids` | Base table | Compare URL resolution |

Supporting base tables (not canonical surfaces, required by views):
- `result_environment` - OS, arch, CPU, memory, Python per result
- `result_phase_durations` - phase timing breakdown per result

**G-11 compliance:** Views project only bare column references, bare aliased columns,
`CAST`, and `COALESCE(col, literal)`. No arithmetic, aggregation, CASE, window functions,
or non-whitelisted function calls in view projections.

### Metric Parity Registry

The complete metric registry is at `_project/planning/visible_metrics.yaml`. It classifies
every user-visible metric as `raw_copy` (source-fidelity contract) or `derived`
(final-value parity contract), with `canonical_ref` and `tolerance` per metric.

**Derived metrics with existing Python reference computation:**

| Metric | Python reference |
|--------|-----------------|
| `display_ms` per query | `_project.scripts.explorer_pipeline.transformer._query_display_ms` |
| `display_geomean_ms` | `_project.scripts.explorer_pipeline.transformer._display_geomean_ms` |
| `geomean_ms` | `_project.scripts.explorer_pipeline.transformer._geomean_ms` |
| `compliance_class` | `_project.scripts.explorer_pipeline.transformer._compliance_class` |
| `is_ranking_eligible` | `_project.scripts.explorer_pipeline.models.is_ranking_eligible` |
| `platform_id` | `_project.scripts.explorer_pipeline.models._platform_id` |
| `tuning_hash` | `_project.scripts.explorer_pipeline.transformer._tuning_hash` |
| `short_id` | `_project.scripts.explorer_pipeline.pipeline._build_short_ids` |
| `rank` (cohort) | `_project.scripts.explorer_pipeline.pipeline._build_benchmark_summaries` |
| `rank`, `metric_value`, `speedup_vs_best`, `avg_rank`, `n_cohorts` (meta) | `_project.scripts.explorer_pipeline.pipeline._build_meta_leaderboard` |
| `percentile_p50/p90/p95/p99` | `_project.scripts.explorer_pipeline.transformer._platform_percentile_stats` |

**Derived metrics currently TS-only - must be ported to Python in W2:**

| TS source | Function | Target in W2 |
|-----------|----------|--------------|
| `chartMath.ts: computeRankTable` | Per-query rank across platforms in a cohort | Port into `_project/scripts/explorer_pipeline/` and materialize into `benchmark_matrix_cells` or a companion rank table |
| `chartMath.ts: perQuerySpeedup` | `slowest_ms / fastest_ms` spread | Not persisted; computed as SQL window function `MAX/MIN` in the Compare DuckDB query - no Python porting needed |
| `chartMath.ts: vsSlowestRatio` | `slowest_ms / this_ms` per result per query | Computed from DuckDB query_display_timings/results at read time; the legacy comparison artifact path has been removed |
| `chartMath.ts: colorForCell`, `lightnessForCell` | Heatmap color from timing ratio | Presentation-only (CSS hue/lightness from `display_ms`). No metric value. Stays TS-side. |
| `ranking.ts: primaryMetricFor` | Benchmark → primary metric enum | Already in `models.get_ranking_config()`. Used in Compare.tsx as a fallback when loading DetailResult files. In the DuckDB path, `benchmark_rankings.primary_metric` column carries this. No new Python needed. |
| `compliance.ts: complianceLabel` | Compliance class → display string | Presentation-only formatting. Stays TS-side. |

### Direct Ingest Contract

```
committed JSON bundles (benchmarks/*.json)
  → pipeline validate (SchemaV2Validator)
  → pipeline transform (BundleTransformer)
  → compute derived values in Python (display_ms, display_geomean_ms, short_id,
     is_ranking_eligible, compliance_class, matrix cells, rankings, meta-leaderboard)
  → bulk-insert all ten canonical tables in one DuckDB transaction
  → copy raw bundles to public/data/bundles/ (download-only affordance)
  → [W2-W4 bridge only] regenerate metric-bearing JSON from the same pipeline pass
```

**Rule:** JSON is an immutable source input and a download-only output. It is never a
steady-state metric read source. If a bridge artifact and DuckDB disagree, DuckDB is
canonical and the bridge artifact is regenerated from the same pipeline pass, never
hand-patched.

### Cutover and Deletion Order

| Phase | Pipeline side | Frontend side | Artifacts deleted |
|-------|--------------|---------------|-------------------|
| **W2** | Add ten canonical tables; all existing JSON emitters continue as bridge artifacts | No frontend changes | None |
| **W3** | No pipeline changes | Add `duckdbQueries.ts` + `duckdbSchema.ts`; cut `Query.tsx` from `results_schema.json` | `results_schema.json` (emitter + file) |
| **W4 Home slice** | Delete `meta_leaderboard.json` emitter | Home migrates to DuckDB | `meta_leaderboard.json` |
| **W4 PlatformIndex slice** | - | PlatformIndex migrates to DuckDB | (reads `results` table; no unique artifact) |
| **W4 BenchmarkIndex slice** | Delete `benchmarks/*.json.gz` emitter | BenchmarkIndex migrates to DuckDB | `benchmarks/*.json.gz` files |
| **W4 ResultDetail slice** | Delete `details/*.json` emitter | ResultDetail migrates to DuckDB | `details/*.json` files |
| **W4 Compare + final slice** | Delete `compare/*.json` + `short_ids.json` emitters | Compare migrates to DuckDB; `manifest.ts` deleted; `db.ts` hardening lands | `compare/*.json`, `short_ids.json`, `manifest.ts` |
| **W7** | Final sweep of any remaining legacy emitters | - | `manifest.json` (or demote to routes-only) |

**db.ts hardening** (deferred from W3 to W4 final slice):
- Reject `getDb()` on attach failure instead of `console.warn` + silent continue
- Delete the soft JSON fallback at `db.ts:86-89`
- Delete the stale `db.ts:1-17` "Phase 1/2 scaffold" header comment
- Wire per-page `ErrorMessage` UI (G-7 requirement)

### Research Gate Findings (RG-1 through RG-9)

| Gate | Status | Finding |
|------|--------|---------|
| **RG-1** Cold-start init budget (p50 <1500ms, p95 <3500ms) | TBD | Requires browser measurement against deployed corpus. Measure after W2 builds the full ten-table DB. Accepted risk: proceed to W2; gate must be green before W3 ships to users. |
| **RG-2** HTTP range-reads (≤10% total file for single-row lookup) | HIGH CONFIDENCE PASS | `DuckDBDataProtocol.HTTP` is designed for HTTP range reads; DuckDB-WASM issues `Range: bytes=` requests for column chunks. GitHub Pages returns `Accept-Ranges: bytes` with 206 responses. No implementation change needed. Measure cold single-row lookup after W2 to confirm the ≤10% bound. |
| **RG-3** DB size budget (sub-linear growth, ≤20 MB at 100x) | TBD | Requires synthetic corpus build at 1x, 5x, 20x, 100x. The full ten-table schema (especially `query_executions` with ~3 rows per query per result) will be larger than the current 19-column stub. Measure after W2. If 100x projection exceeds 20 MB, escalate to RG-5. |
| **RG-4** COEP/COOP hosting | HIGH CONFIDENCE PASS | `duckdb.selectBundle()` automatically selects the `mvp` single-threaded bundle when COEP/COOP headers are absent (e.g. GitHub Pages without custom headers). No cross-origin isolation required for the `mvp` bundle. Confirm with bundle selection log after W2. |
| **RG-5** Single DB vs partitioned | N/A unless RG-3 fails | Default: single `results.duckdb`. Activate only if RG-3 measures > revised ceiling. |
| **RG-6** View materialization budget (<250ms p95 cold at 100x) | TBD | Benchmark canonical surface queries against 100x synthetic corpus after W2. Views that fail promote to materialized `CREATE TABLE AS` in W2 before W3 frontend migration begins. |
| **RG-7** Attach-failure UX (per-page ErrorMessage, no silent fallback) | PLANNED FOR W4 | The `db.ts:86-89` soft fallback stays in W2-W3 while manifest-backed pages still exist. The final W4 slice (Compare + manifest.ts deletion) lands the hardening. Per-page ErrorMessage components must exist on all six pages by W4 exit. |
| **RG-8** toJSON fidelity (no precision loss on DOUBLE/BIGINT/DECIMAL) | RISK: MEDIUM | DuckDB-WASM's Arrow→JSON path has known BIGINT→BigInt issues (JSON.stringify throws on BigInt). `queryRows` uses `.toJSON()` which returns plain objects; BIGINT columns must use explicit `CAST(col AS VARCHAR)` or `CAST(col AS DOUBLE)` at the query layer. Plan: audit every `BIGINT` column in the schema and add explicit CASTs in `duckdbQueries.ts`. Cover in G-8 Vitest matrix. |
| **RG-9** Read-only fuzz (adversarial SQL harmlessly fails) | HIGH CONFIDENCE PASS | DuckDB's `ATTACH ... (READ_ONLY)` rejects all DDL and DML at the engine level. HTTPFS, INSTALL, LOAD may be compiled out of the `mvp` bundle or rejected at runtime. Cover in G-9 Vitest suite (W6). |

**Research gate exit criterion met when:** RG-1 and RG-3 are measured (after W2 DB build),
all others are GREEN or have an accepted-risk note above, and the frozen schema in
`_project/design/browser-duckdb-schema.sql` is committed alongside this decision record.

## External Sources

- Geekbench editions: https://www.geekbench.com/editions/
- Geekbench Browser: https://browser.geekbench.com/
- CloudSpecs site: https://cloudspecs.fyi/
- CloudSpecs repository: https://github.com/TUM-DIS/cloudspecs
- OpenBenchmarking: https://openbenchmarking.org/
- airspeed velocity: https://asv.readthedocs.io/en/latest/using.html
- js-framework-benchmark (PR-based contribution model): https://github.com/nicholasgasior/js-framework-benchmark
- ClickBench contribution model: https://github.com/ClickHouse/ClickBench
