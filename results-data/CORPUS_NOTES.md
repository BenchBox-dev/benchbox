# Corpus Generation Notes - 2026-04-03

## DuckDB version matrix (operator-run, 2026-08-29)

The Results Explorer corpus includes a reproducible DuckDB version-over-version matrix at
TPC-H, TPC-DS, ClickBench, and SSB SF10. BenchBox's ClickBench generator creates
synthetic data linearly from the scale factor, so it is measured at SF10 like the
other workloads. It uses three independent power runs per cell and reports medians.
The published corpus has one median bundle per version/benchmark cell (28 bundles total);
the 84 raw repetitions are retained only in the external operator output. The package
points are DuckDB 1.0.0, 1.1.3, 1.2.2, 1.3.2, 1.4.4, 1.5.5, and
1.6.0.dev365 (the current latest 1.6 development wheel at capture time).

The runner and analyzer are `scripts/run_duckdb_version_matrix.py` and
`scripts/analyze_duckdb_version_matrix.py`, which run the DuckDB spec of the engine-neutral
`scripts/run_version_matrix.py` and `scripts/analyze_version_matrix.py`. The analyzer's
`--explorer-bundles-dir` option creates the 28 median bundles for promotion. All raw
data, databases, logs, and analysis outputs remain in the external operator output
directory; only those anonymized median bundles are promoted here. DuckDB development wheels can report a separate internal
engine build string, so the raw bundle retains it while Explorer identifies the run by
the resolved package version.

The 28 promoted median bundles carry maintainer-run submission manifests with
per-bundle SHA-256 hashes. Their Apple M4 CPU identity is an operator attestation,
not a recovered measurement: the retained raw matrix archive records the same
Darwin/arm64 client host as the standing single-machine corpus attestation, while
the historical capture path omitted CPU model and vendor. The exact provenance
rewrite and old-to-new result IDs are recorded in
`bundles/cpu-identity-attestation-pr-1946.manifest.json`.

## Platforms Run

### TPC-H (SF 0.01)
- **DuckDB 1.4.3** - PASSED, 22 queries, 3 measurement runs
- **DataFusion 51.0.0** - PASSED, 22 queries, 3 measurement runs
- **Polars 1.37.1** - PASSED, 22 queries, 3 measurement runs (DataFrame mode)

### SSB / Star Schema (SF 0.01)
- **DuckDB 1.4.3** - PASSED, 13 queries, 3 measurement runs
- **DataFusion 51.0.0** - PASSED, 13 queries, 3 measurement runs
- **SQLite 3.50.4** - PASSED, 13 queries, 3 measurement runs

## Skips and Notes

### Polars-df SSB - Skipped (0 queries)
`polars-df` with SSB benchmark emitted 0 queries during execution ("No queries
found for execution"). SSB DataFrame queries are not implemented for the Polars
platform. SQLite was used as the third platform for SSB instead.

### Export bug fix
The `benchbox export` command failed with `TypeError: type NoneType doesn't
define __round__ method` when `load_time_ms` was `None` in `table_statistics`.
Fixed in `benchbox/core/results/schema.py` by guarding `round()` calls with
`is not None` checks.

### Export --last filter caveat
`benchbox export --last --benchmark ssb --platform duckdb` did not find SSB
results because the schema v2 `benchmark.id` field is `star_schema` (not `ssb`).
The `--last --benchmark` filter compares against the JSON `benchmark.id`, so
results were exported directly by filename.

## Cohort Depth
Both cohorts meet the >=3-platform depth criterion required for the Compare view:
- `tpch SF=0.01`: DuckDB, DataFusion, Polars
- `star_schema SF=0.01`: DuckDB, DataFusion, SQLite

## Zero-query DataFrame withdrawal (2026-08-24)

Sixty legacy DataFrame bundles were withdrawn because they reported
`summary.validation=passed` after executing zero queries. They are
non-measurements, not truthful partial results. Each bundle's manifest was
removed with it.

Removing only those bundles would have left ten represented cohorts below the
three-platform corpus floor. Because truthful replacements were not yet
available, the remaining 17 SQL bundles in those cohorts were temporarily
withdrawn too; those 17 were not classified as invalid. This preserves the
cohort invariant without counting empty results as coverage:

- AMPLab SF 0.1 and 1.0
- CoffeeShop SF 0.1 and 1.0
- H2O-DB SF 0.1 and 1.0
- SSB SF 1.0
- TSBS DevOps SF 0.01, 0.1, and 1.0

The removal commit contains the exact 77-bundle and 77-manifest path list.
Restore a cohort only with at least three truthful platform results. Fresh
DataFrame results additionally require real validation evidence; execution
success alone is not a validation pass. NYC Taxi and TSBS DevOps regeneration
also waits for their native temporal-literal fix.

## Legacy validation-claim normalization (2026-08-25)

The 136-bundle develop corpus contained 52 legacy bundles whose
`phases.validation.status` was `NOT_RUN` while `summary.validation` claimed
`passed` (23 bundles) or `partial` (29 bundles). These are historical claims,
not rerun evidence. Their summary status is now `not_run`; their query timing
and failure records remain unchanged.

This preserves truthful partial measurements as non-ranking capability
evidence. It does not promote failed queries or infer validation results. A
future rerun may replace the `not_run` claim only when the validation phase
records actual evidence. Submission admission also rejects a `passed` or
`partial` summary claim paired with an unrun validation phase.

## Pre-2026-08-23 results withdrawn (2026-08-28)

All 130 bundles run before 2026-08-23 were removed from the develop corpus,
together with their 114 `.manifest.json` sidecars (244 files). What remains is
9 bundles across 3 cohorts.

**This was a trust decision, not a soundness finding.** Unlike the 2026-07-16
tuned drop (#1176 proved the tuning config never reached platform adapters) and
the 2026-08-24 zero-query withdrawal (bundles claimed `passed` after executing
nothing), no defect was found in the removed results. The maintainer no longer
trusts measurements taken before 2026-08-23 and asked for them to be withdrawn.
Do not go looking for a bug report; there isn't one.

What went:

- 12 bundles from the original 2026-04-03/04 corpus generation
- 111 bundles from the 2026-05-02 maintainer UAT sweep (committed in #164)
- 3 JoinOrder bundles from the 2026-05-12 canonical UAT
- 4 SSB seed-lane bundles from 2026-07-30

Removing them would have left TPC-H SF1 holding only Polars and PySpark, below
the three-platform floor in `validate_corpus.py`. A fresh maintainer DuckDB run
at TPC-H SF1 (DuckDB 1.3.2, 66 queries, 0 failed, validation PASSED) was added
in the preceding commit to hold the cohort. It also gives that cohort a SQL
reference point against two DataFrame-mode results.

The three migration manifests in `bundles/` were deliberately kept:
`path-privacy-migration.manifest.json` is the `DEFAULT_MANIFEST` in
`_project/scripts/results_explorer_corpus_migrate.py` and is name-referenced by
`sync-results-data-to-published.yml`. They are tooling audit records, excluded
from bundle discovery by `COMPANION_SUFFIXES`.

Restoring any withdrawn cohort means fresh runs, not reverting this commit.
`REGENERATION.md` is the precedent for how to document what a restore needs.
The removal commit carries the exact 244-path list.

## Cloud TPC-H results withdrawn (2026-09-25)

The nine TPC-H bundles from the first live cloud runs (BigQuery, Databricks,
and Snowflake at SF 0.1, 1, and 10, run 2026-09-18/19) were removed from the
corpus with their nine `.manifest.json` sidecars (18 files). The maintainer
identified the published results as incorrect. They must not be shown in the
Results Explorer or used for comparisons.

Removed public result IDs:

- `tpch-bigquery-sf0.1-20260918-97c7acc0`, `tpch-bigquery-sf1.0-20260918-47b8218f`,
  `tpch-bigquery-sf10.0-20260918-ad3f00af`
- `tpch-databricks-sf0.1-20260918-45dbaeea`, `tpch-databricks-sf1.0-20260918-01a3150a`,
  `tpch-databricks-sf10.0-20260918-a0ee4f11`
- `tpch-snowflake-sf0.1-20260919-c5d751f5`, `tpch-snowflake-sf1.0-20260919-11abeacf`,
  `tpch-snowflake-sf10.0-20260919-08fb3536`

The TPC-H SF 0.1 and SF 1 cohorts keep at least three local platforms. The
SF 10 cohort retains its seven local DuckDB version-matrix results; only
cloud-platform coverage at SF 10 is gone.
The same paths must also be removed from `published-results` by a
deletion-only PR, because the mirror's union overlay does not propagate
develop-side deletions. Restoring cloud coverage means fresh runs, not
reverting this removal.

## Live cloud corpus (2026-10-02)

84 maintainer-run bundles from live BigQuery, Snowflake and Databricks runs
(2026-09-20 to 2026-10-01) were added with `result_source: internal`
submission manifests. They cover 28 benchmark/scale cohorts, one bundle per
platform in each, so every cohort meets the three-identity floor:
nyctaxi, flightdata, tsbs_devops, tpch_skew, datavault and tpchavoc at SF 0.1,
1 and 10; tpcds_obt at SF 1; read_primitives at SF 0.01 and 0.1;
write_primitives and transaction_primitives at SF 0.01, 0.1 and 1; and
metadata_primitives at SF 1. This is the first coverage for
metadata_primitives, write_primitives, transaction_primitives and tsbs_devops.

Before publication, every bundle was re-run through the public anonymiser
(`_project/scripts/results_explorer_corpus_migrate.py`) after it gained rules
for the Databricks `warehouse_id` and saved cloud `default_output_location`,
which earlier runs had stored raw. Only those fields and the content-derived
result IDs changed.

What these results are and are not:

- Databricks and Snowflake runs disabled the result cache per session and
  record a validated cache receipt; BigQuery jobs run with
  `use_query_cache=False` (not recorded in the bundle).
- FlightData SF 10 uses real BTS months only.
- Three BigQuery bundles carry reviewed validator overrides
  (`<bundle>.override.json`): `timing-plateau` for tpch_skew SF 0.1, and
  `small-scale-floor` for transaction_primitives SF 0.01 and SF 0.1.
- Write and transaction bundles raise the `result-rows-empty` warning because
  DML operations report no result rows; operations the benchmark catalog marks
  unsupported on a platform (for example `RETURNING`, bulk loads) are
  recorded as SKIPPED, not failed.
- Five older BigQuery bundles (nyctaxi SF 10, flightdata SF 0.1/1/10,
  tpch_skew SF 10) predate the `provenance.source` field; their manifests
  record `result_source: internal`.

## Live cloud corpus, second batch (2026-10-03)

19 maintainer-run bundles from live BigQuery and Snowflake runs (2026-09-19 to
2026-09-20) were added with `result_source: internal` submission manifests.
Each joins an existing cohort, so the cohort count is unchanged:
SSB at SF 0.1, 1 and 10 on Snowflake; AMPLab at SF 0.1 and 1 and JoinOrder at
SF 1 on BigQuery and Snowflake; ClickBench at SF 1 on BigQuery and Snowflake;
and CoffeeShop and H2ODB at SF 0.1 and 1 on BigQuery and Snowflake.

The runs had no submission manifests. Each manifest was written from the
original run output, and every bundle was re-run through the public anonymiser
(`_project/scripts/results_explorer_corpus_migrate.py`), so no raw bucket,
dataset or warehouse name remains.

What these results are and are not:

- Every bundle passes `scripts/validate_submission.py --require-manifest` with
  no error, warning or override.
- BigQuery jobs ran with the query cache disabled in configuration. Snowflake
  runs declare the result cache off in configuration. Neither bundle records a
  cache-control receipt, so both claims rest on the declared setting.
- ClickBench timings move little across scale: from SF 0.1 to SF 10 the
  geometric mean grows 1.45x on BigQuery and 1.33x on Snowflake. BenchBox's
  generator creates 1,000,000 rows per scale factor, so fixed per-query
  overhead dominates. Only the SF 1 cells are included.
- Not included: Databricks cells (the runs enabled the result cache and have
  no receipt), BigQuery SSB (needs validator overrides), ClickBench SF 0.1 and
  SF 10 (scale-invariant), TPC-DS and TPC-DI. TPC-H is covered in the next
  section.

## Live cloud TPC-H, fresh runs (2026-10-03)

Nine maintainer-run TPC-H bundles from live runs on 2026-10-03 were added with
`result_source: internal` submission manifests: SF 0.1, 1 and 10 on BigQuery,
Snowflake and Databricks. They replace the cloud coverage withdrawn on
2026-09-25 and were run on code that includes the cloud fixes made since:
Snowflake and Databricks session cache receipts, load hardening, region
capture and the Snowflake query-history fix.

What these results are and are not:

- The SF 1 and SF 10 runs used `--official` with `--seed 42` and are stamped
  `compliance_class: official`. The SF 0.1 runs are
  `compliance_class: unofficial_subscale`, as every sub-SF-1 TPC-H run is; they
  are carried like the other unofficial cohorts and no official TPC-H metrics
  are computed for them.
- Four bundles pass `scripts/validate_submission.py --require-manifest` with
  no error, warning or override: SF 1 on all three platforms and SF 10 on
  BigQuery. Snowflake SF 10 and Databricks SF 10 pass with the overrides
  described below. The three SF 0.1 bundles fail the submission class gate for
  the reason above and are admitted through the lenient mirror lane.
- Snowflake and Databricks runs disabled the result cache per session and
  record a validated cache receipt with the cache disabled. BigQuery jobs ran
  with the query cache disabled in configuration.
- Row counts match the TPC-H scale factor (LINEITEM 600,572 at SF 0.1,
  6,001,215 at SF 1 and 59,986,052 at SF 10) and all 22 queries ran in every
  bundle.
- Two bundles carry reviewed validator overrides (`<bundle>.override.json`):
  `scale-invariant` for Snowflake SF 10 and for Databricks SF 10. On both
  platforms the geometric mean grows about 1.35x from SF 0.1 to SF 10 while
  LINEITEM grows 100x. The result cache is confirmed off and measurement runs
  at 0.8 to 0.9 of warmup, so per-query overhead explains the flat timings.
- The BigQuery SF 0.1 bundle also shows a `timing-plateau` finding (per-query
  means span 824 to 1173 ms, a fixed per-job latency floor); it is carried in
  the lenient lane without an override because the class gate already applies.

## TPC-DI cloud data comparison (2026-10-03)

Four TPC-DI SQL bundles cover SF 0.1 and SF 1 on BigQuery and Snowflake. The
BigQuery measurements were retained from 2026-09-20. Snowflake loaded fresh
data generated with seed 42 and generation algorithm version 1 on 2026-10-03.
All four bundles execute the complete 38-query set without failures and pass
strict submission validation with their manifests without overrides. SF 10
and the Databricks bundles are described in the next section.

At each scale, all sixteen table row counts agree across the two platforms.
Total rows are 299,864 at SF 0.1 and 1,442,264 at SF 1.
QVQ12 and QAQ7 result row counts also agree. The Snowflake runs record validated
cache-off receipts; the retained BigQuery configuration records its query cache
as disabled.

These checks establish agreement in recorded table counts and the two named
query counts. They do not establish full result-value or dataset-content
equivalence. The retained BigQuery bundles have no generation-version or
generation-hash fields. At SF 1, existing local TPC-DI bundles report QVQ12=9
and QAQ7=1, while these cloud bundles report QVQ12=32 and QAQ7=2. Their timings
must be assessed with that result difference in mind.

The public anonymization pass preserves query results, timings, table counts
and phase evidence. Submission manifests record the hashes of the public
bundles. Original measurements remain in the operator output archive.

## BigQuery SSB timing (2026-10-03)

Three BigQuery SSB SQL bundles from 2026-09-19 cover SF 0.1, SF 1 and SF 10.
Query caching is recorded off, and measurement runs at 0.95 to 0.97 of warmup.
Loaded rows grow from 625,757 to 62,322,557 and scanned bytes from 0.846 GB to
84.476 GB, while the geometric mean grows only 1.37x (894 to 1,228 ms) and the
median per-query mean 1.34x. At SF 0.1 and SF 1, per-query means span 758 to
1,142 ms and 765 to 1,070 ms.

These results are consistent with fixed per-job and client latency dominating
light queries. Provider queue and execution timings were not recorded, so that
explanation is an inference. The SF 0.1 and SF 1 bundles carry approved
timing-plateau overrides, and the SF 0.1 and SF 10 bundles carry approved
scale-invariant overrides.

## TPC-DI at SF 10 and on Databricks (2026-10-04)

This adds TPC-DI SF 10 on BigQuery and Snowflake, and SF 0.1, SF 1 and SF 10 on
Databricks, to the four TPC-DI cloud bundles described above. Databricks loaded
data generated on 2026-10-03 with seed 42 and recorded validated cache-disabled
receipts. All nine cloud bundles execute the complete 38-query set without
failures and pass strict submission validation, including combined scale
validation, without overrides.

At each scale, all sixteen table row counts agree across the three platforms.
Total rows are 299,864 at SF 0.1, 1,442,264 at SF 1 and 11,891,664 at SF 10.
QVQ12 and QAQ7 result row counts also agree across the three platforms at every
scale (100 and 1 at SF 0.1, 32 and 2 at SF 1, 1 and 1 at SF 10). The caveats of
the section above still apply: these checks do not establish full result-value
equivalence, and the local TPC-DI bundles return different QVQ12 and QAQ7 counts.

## Cloud TPC-DS bundles (2026-10-04)

Nine TPC-DS SQL bundles cover SF 0.1, SF 1 and SF 10 on BigQuery, Snowflake and
Databricks. The BigQuery measurements were retained from 2026-09-20. Snowflake
and Databricks were rerun on 2026-10-03 with validated cache-disabled receipts.
The earlier Snowflake runs had a 4.2-second per-query floor from a harness
defect, and the earlier Databricks runs had the result cache on; these runs have
neither problem. All three platforms load the same row counts at each scale
(1,176,171 at SF 0.1, 19,557,376 at SF 1 and 191,496,659 at SF 10), and every
query part succeeds.

Five bundles are unofficial and keep their recorded classifications. The SF 0.1
bundles on all three platforms are `unofficial_subscale`, and BigQuery SF 1 and
SF 10 are `unofficial_nonstandard` because they ran without `--official`. They
make no official TPC-DS claim and are carried in the lenient publication lane.
Snowflake and Databricks SF 1 and SF 10 ran with `--official --seed 42`.

On Snowflake, scanned bytes grow from 1.047 GB to 51.102 GB between SF 0.1 and
SF 10 while the geometric mean grows only 1.38x (899 to 1,239 ms). The limited
scaling is consistent with per-query overhead and warehouse performance at these
data volumes; provider timings were not recorded, so the cause is not fully
decomposed. The Snowflake SF 0.1 and SF 10 bundles carry approved
scale-invariant overrides. Databricks timings grow 1.57x over the same range and
need no override.

## Cloud ClickBench (2026-10-04)

Seven ClickBench SQL bundles add SF 0.1 and SF 10 on BigQuery and Snowflake, and
SF 0.1, SF 1 and SF 10 on Databricks, beside the published BigQuery and
Snowflake SF 1 bundles. ClickBench loads 100,000, 1,000,000 and 10,000,000 rows
at SF 0.1, 1 and 10. All 43 queries complete at every scale. Caching is recorded
off on BigQuery and Snowflake, and Databricks records validated cache-disabled
receipts.

Across the 100x row increase, the BigQuery geometric mean grows 1.45x while
scanned bytes grow from 0.690 GB to 51.713 GB. The Snowflake geometric mean
grows 1.33x, and its output row counts match BigQuery for every query at every
scale. Measured client statement overhead is 364 to 408 ms on BigQuery and 82
to 90 ms on Snowflake, and the heavier queries grow at least 2x on both. This
is consistent with fixed job and client overhead dominating the lighter
queries rather than cached results or a load defect. Provider queue and
execution phases were not captured. Each BigQuery and Snowflake SF 0.1 and
SF 10 bundle carries an approved scale-invariant override. The Databricks
geometric mean grows 1.68x (366 to 616 ms) and needs no override.

Databricks does not return the same results as BigQuery and Snowflake for two
queries. Q29 returns one row at SF 1 and SF 10, where BigQuery and Snowflake
return 0 and 6, because the Databricks SQL keeps the `\1` regular-expression
backreference instead of Spark's `$1`, so every referrer falls into one group.
Q43 returns one row at every scale, where BigQuery and Snowflake return 2, 22
and 159; its cause has not been identified. The other 41 queries return the
same row counts on all three platforms. Q29 and Q43 timings on Databricks
measure different work and are not comparable with the other platforms.

## Databricks cache-off reruns (2026-10-04)

Ten Databricks SQL bundles add SSB SF 0.1, 1 and 10, AMPLab SF 0.1 and 1,
JoinOrder SF 1, CoffeeShop SF 0.1 and 1, and H2ODB SF 0.1 and 1 to cohorts that
already hold BigQuery and Snowflake bundles. The earlier Databricks runs of
these cells had the result cache on and were not published. These runs on
2026-10-03 record validated cache-disabled receipts and region us-east-2. Every
query succeeds in three measurement rounds, each bundle loads the same total row
count as the Snowflake bundle of its benchmark and scale, and all ten pass
strict submission validation, including combined scale validation, without
overrides.

## Public-path single-pass status (2026-08-05)

Verified with `results_explorer_corpus_migrate.py` dry-run: 0/207 bundles changed under the current public anonymization pass. The `test_rederiv_fresh_public_pass_equals_curated_for_all_fields` gate pins the fixed point.

---

# Regeneration - strip residual empty `client_host` (2026-08-05 / 2026-08-06)

**Related PR:** #1614 (`fix/strip-empty-client-host-corpus`)
**Date:** 2026-08-05 (local) / 2026-08-06 (UTC commit)

## Reason

After `machine_id` was dropped from public environment maps, some already
public-shaped primary bundles retained residual empty `client_host: {}`
objects. Those hollow maps were identifier-only leftovers, not real host
profiles. Anonymization policy now **always omits empty optional environment
maps** (including already-empty `{}` residuals) so the public shape has no
hollow blocks.

## What landed together

Code change and corpus re-derive shipped in one fixed-point commit so stored
bytes match the fresh public shape:

1. **Policy** (`benchbox/core/results/anonymization.py`): omit empty optional
   maps under `_PUBLIC_EMPTY_OPTIONAL_MAP_KEYS` even when the stored input was
   already `{}` (not only when non-empty content was stripped to empty).
2. **Corpus**: re-derived **105** primary bundles under
   `results-data/bundles/` (plus inventory refresh) so checked-in bytes match
   re-anonymization output. Count verified as the primary-bundle delta vs
   `origin/develop` on this branch (105 `results-data/bundles/*.json` primaries;
   not plans/tuning/manifest sidecars).

No full corpus rewrite beyond those residual hollow maps; non-empty
`client_host` profiles and unrelated fields were left alone. Anonymization
policy was not expanded beyond empty optional map omission.

## How to verify

- Unit: `tests/unit/core/results/test_anonymization.py` —
  `test_already_empty_client_host_is_omitted` (and related public-unread
  identifier drop cases).
- Corpus fixed point: re-anonymize primary bundles and assert byte-identical
  publication (existing re-derived / fixed-point corpus gates; no empty
  `client_host` objects remain in primary bundles).
- Spot check: `rg -n '"client_host": \{\}' results-data/bundles` should not
  match residual hollow maps in primary result JSON.


## CPU identity backfill (2026-08-29) — OPERATOR ATTESTATION, NOT MEASURED

Every bundle in this corpus now carries `cpu_model: "Apple M4"`,
`cpu_vendor: "Apple"`, and `cpu_identity_provenance: "user_attested"`, which
the Explorer read model normalizes to the family `apple_silicon`. **These values
were not measured. They are an operator attestation.**

### Why no measured value exists

The capture path was defective, in three independent ways:

1. `get_system_info` sourced `cpu_model` from `platform.processor()`, which on
   Darwin returns the bare architecture `"arm"`. `normalize_cpu_family("arm")`
   is `"unknown"`, so even where a value was recorded it said nothing.
2. `SystemInfo.to_dict` emitted `cpu_cores` / `total_memory_gb` / `os_version`
   while `ClientHostEnvironment.from_system_profile` reads `cpu_count` /
   `memory_gb` / `os_release`, so those three were silently dropped from every
   bundle, and `cpu_vendor` was never produced at all.
3. The DataFrame adapters descend from a hierarchy that never runs the SQL
   path's environment capture, so 43 of these 151 bundles recorded no client
   host whatsoever.

Defects 1 and 2 are fixed in `fix/cpu-identity-capture-source`. Defect 3 is
tracked as `dataframe-client-host-capture-gap`. Across the 3,845 raw local
results only 4 carry a CPU, and all four post-date those fixes — so there was
nothing in the archive to recover.

### The attestation

The project maintainer attests that every run in this corpus executed on a
single machine — natively, or driving Apple container Linux images whose
engines share that host's CPU. No other machine has been used in the project's
development.

Recorded evidence is consistent with it but does not by itself establish the
model: every bundle that records a client host records `Darwin`/`arm64`, and
the raw local archive shows a single `machine_id`. `arm64` + `Darwin` implies
Apple Silicon; it does not distinguish an M1 from an M4. The specific model
rests on the attestation alone.

### What was and was not written

Only the CPU identity and its typed provenance were written. For the 43 DataFrame bundles
that had no client host, `os`, `arch` and `python` were **not** synthesized:
the attestation covers which machine ran the corpus, not a given run's OS
release or interpreter version. That gap closes forward, not retroactively.

### Result IDs were renumbered

`result_id` embeds a SHA-256 prefix of the raw bundle bytes, so all 151 were
renumbered. Precedent: `path-privacy-migration` and `unread-identifier-field-drop`
each renumbered all 207 entries of the corpus of their day. Every old → new
mapping is recorded in `results-data/bundles/cpu-identity-attestation.manifest.json`.
The later in-band provenance migration and its second result-ID mapping are
recorded in `results-data/bundles/cpu-identity-provenance-v2.manifest.json`.
Readers distinguish attested values from measured ones through the typed
`cpu_identity_provenance` field rather than by guessing from historical context.

Reproduce with:

    uv run -- python _project/scripts/results_explorer_cpu_attestation_backfill.py
    # add --write to apply
