# Seed Corpus Specification

**Created:** 2026-04-02
**Originating TODO:** `generate-seed-corpus-for-results-explorer`
**Related:** <https://github.com/BenchBox-dev/benchbox/blob/develop/docs/internal/benchbox-results-platform-strategy.md>

This document belongs in `results-data/` because it describes the corpus contract and
the maintainer-run seed-generation workflow, not scratch analysis. Current checked-in
coverage lives in `results-data/README.md` and `results-data/CORPUS_NOTES.md`; this file
defines the workflow target and storage conventions.

## Purpose

The results explorer only becomes useful once a benchmark × scale cohort has enough
platform depth to support real comparison. The seed corpus is the maintainer-generated
baseline that provides that depth before Phase 2 community submissions or any future
Phase 3 hosted service.

The hard requirement today is still the validator gate in `results-data/validate_corpus.py`:
every committed cohort must have at least 3 distinct comparison identities. Ordinary
cohorts count platform names, preserving the historical three-platform floor. A bundle
that records `platform.variant` counts under that variant string instead of the bare
platform name, so two execution variants of one platform (for example
`polars-df+streaming` alongside `polars-df`) are distinct identities while repeated
runs of one variant do not pad the floor. A bundle that ran in a container (recorded in
`environment.execution_class` or attested in its manifest sidecar) adds a `^container`
suffix, so a container run and a native run of one engine are distinct identities; a bundle
that records no class stays native (see `CORPUS_NOTES.md`, "Execution-class identity"). Versions of
one platform never add identities: the floor counts platforms, engine settings and container runs,
not versions (see [Version-over-version cohorts](#version-over-version-cohorts)).

A cohort is one benchmark, scale factor and measured phase. Power timings and
throughput timings are never mixed, and a throughput cohort is further split by
stream count. The phase is the bundle's declared `benchmark.test_type`, lowercased,
with `standard` folded into `power` (the Explorer applies the same rule). Without a
declared type it is the executed phase in `phases` (a phase recorded as `NOT_RUN`
did not run), and without either it is its own `unknown` cohort. A `combined` cohort
holds full-specification runs and follows the rules under
[Combined cohorts](#combined-cohorts). Other declared phases stay separate cohorts.

Only bundles the Explorer can rank contribute an identity. `exclusion_reason` in
`results-data/validate_corpus.py` is the source of truth; it mirrors the Explorer's
`ranking_exclusion_reason`, and a bundle counts only when it returns no reason. It applies
these conditions, in order:

1. The compliance class is not `unofficial_nonstandard`, and the scale factor is a
   standard scale for the benchmark in the registry
   (`get_scale_factor_rule(benchmark_id).standard`; see "Non-standard scale factors" in
   `CORPUS_NOTES.md`). Subscale TPC-H and TPC-DS
   (`unofficial_subscale`, or no class below SF 1) rank, labelled Subscale · Unofficial;
   a subscale bundle with no stored Power@Size ranks on Power@Size derived from its
   final-iteration timings (see <https://benchbox.dev/docs/development/adr/adr-subscale-tpc-ranking-policy.html>).
2. No measurement query failed.
3. The validation status is clean. A `passed` or missing status with failed queries is
   `partial`, and one with a failed or fallback SQL translation is `uncertain`.
4. A `custom` tuning mode has applied tuning evidence (`applied_unverified` or
   `applied_verified`).
5. The timing and coverage contract holds: at least two queries with a valid positive
   display timing, covering at least half of the logical queries.
6. The primary metric for the benchmark and phase is present, finite and positive.
   TPC-H and TPC-DS power cohorts rank on `power_score` (from `power_at_size` only).
   TPC-H and TPC-DS throughput cohorts rank on `throughput_at_size` (Throughput@Size,
   higher is better). TPC-H and TPC-DS combined cohorts rank on `qphh_at_size`
   (QphH@Size) and `qphds_at_size` (QphDS@SF), higher is better, once the bundle's
   throughput and refresh evidence is clean. Every other benchmark and phase ranks on
   the geometric mean of display timings.

Trust label and visibility come from where a bundle is published, not from its content,
so the validator does not check them.

TPC-H and TPC-DS throughput cohorts are split by stream count, so runs with different
stream counts are never compared. `throughput_at_size` counts only when the throughput phase
is `COMPLETED`, has a non-empty `stream_results` in which every entry has `success: true`, and
records no `errors` or `outstanding_work`; otherwise it is treated as absent even if the bundle
carries a value (a timed-out stream is such a case). A throughput bundle without a positive
`throughput_at_size` contributes no identity, and a cohort in which no bundle has one is
reported as `UNRANKED`.

The depth gate applies to cohorts that publish a ranking:

| Rankable identities | Result |
|---------------------|--------|
| 3 or more | passes |
| 1 or 2 | fails |
| 0 | reported as `UNRANKED` in the validator output; passes |

A cohort with no rankable bundle publishes no ranking, so there is nothing to
read against and nothing for the gate to protect. This is how a cohort made up
only of unrankable runs stays in the corpus without blocking it.

Distinct identities are distinct platform names. The validator does not merge engines
that share a backend (Spark SQL and PySpark, DuckDB and DuckDB-based services), so
reviewers must not rely on them to reach the floor.
`results-data/validate_corpus.py` is the authority; `corpus-inventory.json` lists
platform and version for every cohort and is not an admission count.

### Combined cohorts

A combined cohort is one benchmark and scale factor measured by a full-specification
`--official` run: for TPC-H, the power test with RF1/RF2 plus a throughput test with
two or more streams and a refresh stream; for TPC-DS, load, power, two throughput
tests of four or more streams and two data maintenance tests. The cohort is not split
by stream count, because both composite metrics already contain the stream count.

- **Ranking metric.** TPC-H ranks on QphH@Size (`qphh_at_size`, the square root of
  Power@Size times Throughput@Size). TPC-DS ranks on QphDS@SF (`qphds_at_size`). The
  bundle computes both when it is built. The validator and the Explorer read them
  and never recompute them.
- **Secondary metrics.** Power@Size and Throughput@Size stay visible beside the
  composite. A combined Power@Size includes the RF1 and RF2 times, so it is not the
  same number as the Power@Size of a power cohort, and the two are never shown in one
  column.
- **Clean evidence.** A combined bundle ranks only when every throughput phase is
  `COMPLETED` with all streams successful, no `errors` and no `outstanding_work`, and
  every refresh step succeeded. For TPC-H that is RF1 and RF2 in the power phase,
  a `COMPLETED` refresh stream and `refresh_included: true`. For TPC-DS that is a
  `COMPLETED` `maintenance_test` and `maintenance_test_2`, each with at least one
  operation and every operation `SUCCESS`. A bundle the rule puts in the combined lane
  but whose evidence is not clean is not ranked there: a failed stream, `errors`,
  `outstanding_work` or a configured-versus-executed stream mismatch gives
  `combined_throughput_not_clean`, a failed refresh gives `combined_refresh_not_clean`,
  and a combined bundle without the composite metric gives `missing_primary_metric`.
  When the facts leave out a phase the rule needs for the combined lane, the rule
  places the bundle in the lanes its remaining phases support (power-only and
  throughput, or power-only) and those lanes are scored from timings. The rule's
  maintainer admission also gates staging: an undetermined outcome or a TPC-DI bundle
  is refused with the rule's reason (`not_admitted_<class>`).
- **Load time.** `summary.tpc_metrics.load_scope` records what load time includes:
  `components` lists the timed steps among `schema_creation`, `data_loading`,
  `statistics_gathering`, `post_load_maintenance` and `merge_settle`, and
  `counted_in_qph` is true for TPC-DS, where load time is the `T_LD` term of QphDS@SF,
  and false for TPC-H. Engines load data in different ways, so compare QphDS@SF
  across engines with the scope in view. The Explorer shows it.
- **Transactions.** When a bundle records `transactional: false` on a refresh or
  maintenance step (RF1, RF2 or a maintenance operation), the Explorer shows a
  non-transactional disclosure beside the result, and the bundle still ranks. Bundles
  written before non-transactional refresh is supported carry no such flag.
- **Identities.** The combined cohorts need the same three distinct identities as
  every other cohort. The local identities that can run the full specification are
  DuckDB, ClickHouse Local (chDB only) and CedarDB. DataFusion is not one of them,
  because its DML does not delete the right rows for the subquery predicates the
  refresh functions need.

## Generation Contract

| Item | Value |
|------|-------|
| Workflow | `.github/workflows/seed-corpus.yml` |
| Trigger | Monthly cron `0 7 1 * *` (07:00 UTC on the 1st) plus manual `workflow_dispatch` |
| Producer identity | `benchbox-bot` via GitHub Actions |
| Benchmark phases | `generate,load,power`; official TPC-H cells add `throughput` (2 streams, RF1/RF2), official TPC-DS cells add `throughput,maintenance` (4 streams) |
| Trust label | `maintainer-run` |
| Visibility | `public-curated` |
| Supported identities | DuckDB, DataFusion, Polars DataFrame, ClickHouse Local and CedarDB (a pinned local container); no cloud credentials |

The monthly schedule keeps published-results from stalling until someone remembers
to dispatch. Maintainers can still run a full or single-benchmark refresh via
`workflow_dispatch`.

## Workflow Target Matrix

The authoritative cell list lives in `.github/workflows/seed-corpus.yml`
(`strategy.matrix.include`). Do not treat a frozen table in this document as the
source of truth; the workflow drifts as admitted coverage changes.

The supported matrix contains six local, merge-ready cohorts: TPC-H SF 0.01
and SF 0.1, and SSB SF 0.01 and SF 0.1, each with DuckDB, DataFusion and
Polars DataFrame; and the combined TPC-H SF 1 and TPC-DS SF 1 cohorts, each
with DuckDB, ClickHouse Local and CedarDB. Every cohort has three local
comparison identities and no cloud dependency.

The combined cells run the full specification under `--official`: TPC-H runs
power and a 2-stream throughput phase with RF1/RF2, and TPC-DS runs load, power,
a 4-stream throughput phase and data maintenance. CedarDB cells start the pinned
image from `docker/cedardb/docker-compose.yml` before the run. The power-only
TPC-H SF 1 and TPC-DS SF 1 cohorts published before full-specification runs
(DuckDB, DataFusion and ClickHouse Local) are no longer regenerated and stay at
their published bundles. See [Official runs](#official-runs).

| Benchmark | Scale factors |
|-----------|---------------|
| TPC-H | 0.01, 0.1, 1 (combined) |
| TPC-DS | 1 (combined) |
| SSB | 0.01, 0.1 |

Other checked-in cohorts are historical or operator-restored coverage, not a
promise of monthly regeneration. They remain subject to the same admission
gate when changed.

## Merge Gate Caveats

1. The corpus validator fails any cohort with fewer than 3 comparison identities.
   Each supported workflow cohort has all three local identities, so it remains
   publishable without a cloud secret.
2. Checked-in coverage and the workflow target can diverge briefly after a
   restore or schedule change. Prefer `results-data/corpus-inventory.json` and
   `results-data/README.md` for what is currently committed.
3. The workflow re-cuts `publication/ledger-seed.json` against the current
   `published-results` tip and runs `make ledger-seed-gates` before it opens
   its PR. New bundles enter the seed as `legacy_overlay`. After the seed PR
   merges, the owner merges the auto-mirror PR in the same cycle, then runs
   `make ledger-seed-recut` on a branch from `develop` and opens a PR with the
   result, which moves those bundles to `accepted`.

### Version-over-version cohorts

A result's identity is its platform, its execution variant and its comparison version, all read
from fields in the result file by one function
(`benchbox/core/results/comparison_identity.py`). No folder name, file name or directory
decides identity. The comparison version is resolved by a declarative table
(`benchbox/core/results/comparison_identity.json`) keyed by platform-manifest key. For each platform
family it names, in order, the result-file fields that hold the version and how a pre-release is
recognised; a platform without an entry reads `platform.version`. The table records where a fact
is stored. It is not an admission or ranking rule.

A value that is missing, empty or `unknown` in every named field resolves to `version not recorded`.
Every result with no recorded version shares that one identity, which is never a release and is
never the newest version. A version the table recognises as a pre-release or development build
(for example a development wheel or a release candidate) is recorded as a pre-release and never
stands for its platform on a leaderboard.

The comparison version is never a cohort-floor input. The depth gate counts execution variants
and container runs; any number of versions of one platform is one identity there. Version
matrices are ordinary results under this rule. The runner is `scripts/run_version_matrix.py`,
and its median analyzer is `scripts/analyze_version_matrix.py`; both take an engine spec from
`scripts/version_matrix_specs.py`. `scripts/run_duckdb_version_matrix.py` and
`scripts/analyze_duckdb_version_matrix.py` run the DuckDB spec. The analyzer emits one median
bundle per version/benchmark cell for promotion; raw repetitions stay outside the checkout. The
current DuckDB matrix is documented in `results-data/CORPUS_NOTES.md` when its bundles are
promoted.

## Benchmark Notes

### Local identity coverage

The workflow includes `polars-df` for TPC-H SF 0.01/0.1 and SSB. The frozen
power-only TPC-H SF 1 and TPC-DS SF 1 cohorts use `clickhouse-local` as their
local third identity. Combined (full-specification) cohorts take their three
identities from DuckDB, ClickHouse Local (chDB only) and CedarDB; see
[Combined cohorts](#combined-cohorts).

### Official runs

A matrix entry marked `official: true` adds `--official --seed 42` and its
`streams` value to the generic local command and runs the phases the entry lists;
official TPC-H entries also enable the refresh functions. The stored
`compliance_class` is information the writer derives from the corpus rule; no gate
reads it. Admission and ranking follow from the facts in the result file: the
benchmark, the scale factor, the phases that ran, the streams that completed and the
validation record. `benchbox run --official` still refuses any scale factor that is not
an official scale point (SF 1, 10, 30, and so on).

The staging script, `validate_submission.py` (public and trusted), `benchbox submit`,
`benchbox publish` and `validate_corpus.py` all call `corpus_rule` and agree on the
outcome (`tests/unit/scripts/test_corpus_rule_parity.py`):

| Facts | Public path | Staging and trusted validation | `validate_corpus.py` lane |
|---|---|---|---|
| TPC-H or TPC-DS, standard scale, one or more of power, throughput, refresh | admitted | admitted | the lane the rule names (combined, power-only or throughput) |
| TPC-H or TPC-DS below SF 1 | refused | admitted | subscale |
| any benchmark at a non-standard scale | admitted | admitted | none: admitted and unranked |
| other benchmark at a standard scale | admitted | admitted | its own |
| gateway-routed result | refused | refused by staging | none |

Staging stops at `validate_corpus.admission_reason`; it never reads the rank. A
non-standard-scale result therefore stages and validates as admitted and unranked,
and the TPC-H SF 0.01 and SF 0.1 entries stage as subscale results. The vendor-terms,
anonymization, tuning and timing-coverage checks stay behind the same entry points.
`tests/unit/cli/test_corpus_rule_lane_canary.py` builds one result per lane through the
CLI arguments with a stubbed executor and asserts each enters its lane.

### Timestamp contract

Run age uses `run.timestamp`. A plain `YYYY-MM-DD` is an explicit UTC calendar
date. A complete ISO timestamp with `Z` or an offset is converted to its UTC
calendar date. Legacy complete timestamps without an offset are interpreted as
UTC. Prefixes, malformed times, and trailing text are not age values. Age is
informational only and does not change ranking, filtering, comparison, or
admission behavior.

### Execution phases

Scheduled seed-corpus runs use `--phases generate,load,power`. Throughput and
maintenance are excluded from those runs to keep CI runtime bounded and to standardize
the comparison surface on single-stream power timings. Throughput bundles can still be
committed; they form their own cohorts as described under the depth requirement.

### Query plans

Every seed-corpus cell that is not `official: true` runs with `--capture-plans`
(estimated `EXPLAIN` plans, no `ANALYZE`). Plan capture runs outside the query timer,
so it does not change measured times. The staging script anonymizes the
`{run_id}.plans.json` companion next to the bundle, and the Explorer's plan viewer
loads it. A plan that fails to capture is recorded in the companion and does not fail
the cell.

The official SF 1 cells do not capture plans. A multi-stream run keeps one plan per
query per stream, so their companions would be several megabytes for TPC-H and tens
of megabytes for TPC-DS, beyond what the Explorer's plan viewer loads.

## Storage Layout

Both of the following layouts are valid because the explorer pipeline and validator
scan recursively under `results-data/bundles/`.

### Current checked-in layout

The current repository corpus is flat:

```text
results-data/
  bundles/
    tpch_sf001_duckdb_20260403_093653_9c0925d1.json
    tpch_sf01_polars_df_20260404_191727_mcp_b897c572.json
    ssb_sf01_duckdb_sql_20260404_191819_68f79876.json
```

### Seed-workflow staging layout

The seed-corpus workflow stages new PRs under benchmark/platform/scale directories:

```text
results-data/
  bundles/
    {benchmark}/
      {platform}/
        sf{scale_factor}/
          {run_id}.json
          {run_id}.plans.json
```

Scale-factor directory naming follows the raw CLI `--scale` value:

- `0.01` -> `sf0.01`
- `0.1` -> `sf0.1`
- `1.0` -> `sf1.0`
- `1` -> `sf1`

## Trust and Attribution

All seed-corpus bundles are maintainer-generated and use:

| Field | Value |
|-------|-------|
| Trust label | `maintainer-run` |
| Visibility | `public-curated` |
| Attribution | BenchBox CI / `benchbox-bot` |

These labels are intentionally compatible with the broader results-platform strategy,
which later adds community-submission flows without changing the underlying result
bundle format.
