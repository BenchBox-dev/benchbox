# Seed Corpus Specification

**Created:** 2026-04-02
**Originating TODO:** `generate-seed-corpus-for-results-explorer`
**Related:** `docs/development/benchbox-results-platform-strategy.md`

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
cohorts count platform names, preserving the historical three-platform floor. Bundles in
the explicitly segregated `duckdb-version-matrix/` corpus count platform plus version;
repeated runs of one platform/version do not pad that matrix.

A cohort is one benchmark, scale factor and measured phase. Power timings and
throughput timings are never mixed, and a throughput cohort is further split by
stream count. The phase is the bundle's declared `benchmark.test_type`, lowercased,
with `standard` folded into `power` (the Explorer applies the same rule). Without a
declared type it is the executed phase in `phases` (a phase recorded as `NOT_RUN`
did not run), and without either it is its own `unknown` cohort. Other declared
phases such as `combined` stay separate cohorts.

Only bundles the Explorer can rank contribute an identity. `exclusion_reason` in
`results-data/validate_corpus.py` is the source of truth; it mirrors the Explorer's
`ranking_exclusion_reason`, and a bundle counts only when it returns no reason. It applies
these conditions, in order:

1. The compliance class is not `unofficial_nonstandard` or `unofficial_subscale`.
2. No measurement query failed.
3. The validation status is clean. A `passed` or missing status with failed queries is
   `partial`, and one with a failed or fallback SQL translation is `uncertain`.
4. A `custom` tuning mode has applied tuning evidence (`applied_unverified` or
   `applied_verified`).
5. The timing and coverage contract holds: at least two queries with a valid positive
   display timing, covering at least half of the logical queries.
6. The primary metric for the benchmark and phase is present, finite and positive.
   TPC-H and TPC-DS power cohorts rank on `power_score` (from `power_at_size` only;
   `qphh_at_size`/`qphds_at_size` are non-spec exports the driver no longer validates).
   TPC-H and TPC-DS throughput cohorts rank on `throughput_at_size` (Throughput@Size,
   higher is better). Every other benchmark and phase ranks on the geometric mean of
   display timings.

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
only of `unofficial_subscale` runs (for example TPC-DS at SF0.1 on cloud
warehouses) stays in the corpus without blocking it.

Distinct identities are distinct platform names. The validator does not merge engines
that share a backend (Spark SQL and PySpark, DuckDB and DuckDB-based services), so
reviewers must not rely on them to reach the floor.
`results-data/validate_corpus.py` is the authority; `corpus-inventory.json` lists
platform and version for every cohort and is not an admission count.

## Generation Contract

| Item | Value |
|------|-------|
| Workflow | `.github/workflows/seed-corpus.yml` |
| Trigger | Monthly cron `0 7 1 * *` (07:00 UTC on the 1st) plus manual `workflow_dispatch` |
| Producer identity | `benchbox-bot` via GitHub Actions |
| Benchmark phases | `generate,load,power` |
| Trust label | `maintainer-run` |
| Visibility | `public-curated` |
| Supported identities | DuckDB, DataFusion, Polars DataFrame, and ClickHouse Local; no cloud credentials |

The monthly schedule keeps published-results from stalling until someone remembers
to dispatch. Maintainers can still run a full or single-benchmark refresh via
`workflow_dispatch`.

## Workflow Target Matrix

The authoritative cell list lives in `.github/workflows/seed-corpus.yml`
(`strategy.matrix.include`). Do not treat a frozen table in this document as the
source of truth; the workflow drifts as admitted coverage changes.

The supported matrix contains six local, merge-ready cohorts. TPC-H SF 0.01
and SF 0.1 plus SSB use DuckDB, DataFusion, and Polars DataFrame. TPC-H SF 1
and TPC-DS SF 1 use DuckDB, DataFusion, and ClickHouse Local. Every cohort has
three local comparison identities and no cloud dependency.

| Benchmark | Scale factors |
|-----------|---------------|
| TPC-H | 0.01, 0.1, 1 |
| TPC-DS | 1 |
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

### Version-over-version cohorts

Operator-run version matrices may repeat a platform name at several versions. The admitted
DuckDB matrix is segregated under `results-data/bundles/duckdb-version-matrix/`, which is
the only corpus location where the depth gate counts versions as distinct identities.
Results Explorer disambiguates their rows by engine and resolved driver versions. The runner is
`scripts/run_version_matrix.py`, and its median analyzer is
`scripts/analyze_version_matrix.py`; both take an engine spec from
`scripts/version_matrix_specs.py`. `scripts/run_duckdb_version_matrix.py` and
`scripts/analyze_duckdb_version_matrix.py` run the DuckDB spec. The analyzer emits one median bundle per
version/benchmark cell for promotion; raw repetitions stay outside the checkout. The
current DuckDB matrix is documented in `results-data/CORPUS_NOTES.md` when its bundles
are promoted.

## Benchmark Notes

### Local identity coverage

The workflow includes `polars-df` for TPC-H SF 0.01/0.1 and SSB. TPC-H SF 1
and TPC-DS use `clickhouse-local` as their local third identity because the
admitted matrix does not rely on Polars DataFrame support there.

TPC-DS SF 1 jobs also run with `--official --seed 42`. This benchmark-specific
command is required for the scheduled output to be compliance-admissible; all
other matrix entries retain the generic local command.

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
