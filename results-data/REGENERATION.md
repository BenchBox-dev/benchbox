# Regeneration Checklist - Tuned Cells Dropped 2026-07-16

**Related TODO:** `tuning-corpus-drop-pre-provenance-tuned-bundles-20260716`
**Decision:** Joe, 2026-07-16 (tuning follow-up batch gate)

## What happened

The 2026-07 tuning remediation (#1176 w0 discovery) proved the tuning config
never reached platform adapters on the direct CLI path, so every bundle in the
checked-in seed corpus claiming `execution.tuning_mode == "tuned"` was almost
certainly physically untuned. Cross-submission comparisons against those
bundles are unsound, so they were removed rather than relabeled.

325 bundles (plus their 325 `.manifest.json` sidecars, 650 files total) were
removed from `results-data/bundles/`. The 203 bundles with no `tuning_mode`
recorded were left untouched - they ingest honestly as "not recorded" and
were never a soundness problem.

After removal: `results-data/corpus-inventory.json` was regenerated
(`uv run -- python scripts/generate_corpus_inventory.py --write`) and every
remaining cohort still meets the >=3-platform depth gate in
`results-data/validate_corpus.py`.

## How to find the removed bundles

The exact file list is in the git history of the removal commit (see the
commit body on the `chore/tuning-corpus-drop` branch / PR). The sweep logic
lives in `_project/scripts/explorer_pipeline/transformer.py::_tuning_mode`,
which reads `config.tuning_mode` first and falls back to
`execution.tuning_mode` for seed-corpus-era bundles.

## Benchmarks with zero remaining coverage

Four benchmarks had no non-tuned bundles in the corpus at all, so removing
their tuned bundles drops them from the checked-in corpus entirely until
regenerated: **ai_primitives**, **joinorder_synthetic** (distinct from the
surviving `joinorder` IMDB benchmark, which keeps 3 not-recorded bundles),
**tpcds** (the standalone TPC-DS benchmark; `tpcds_obt` is unaffected and
still has not-recorded coverage), and **vector_search**. `results-data/corpus-inventory.json` will not list these
benchmark ids again until at least one tuned or not-recorded bundle is
re-added for each.

## Regeneration checklist

Once a platform's tuned path is verified to actually reach adapters
(post-#1176/#1180), the following benchmark x scale cells need fresh
maintainer runs with `--tuning tuned` (or equivalent) to restore tuned
coverage. Platforms are listed as they appeared in the dropped bundles for
that cell; a regenerated cell does not need to reproduce every platform
below, but each dropped platform's tuned result is no longer represented
anywhere in the corpus until it is re-run.

| Benchmark | Scale Factor | Platforms Removed |
|-----------|--------------|--------------------|
| ai_primitives | 0.01 | Apache Doris, ClickHouse Local, DuckDB, Presto, SQLite, StarRocks |
| ai_primitives | 0.1 | ClickHouse Local, Presto, SQLite, StarRocks |
| ai_primitives | 1.0 | ClickHouse Local, StarRocks |
| amplab | 0.01 | Apache Doris, ClickHouse Local, Dask, DataFusion, DuckDB, Pandas, Polars, PySpark, SQLite, Spark, StarRocks |
| amplab | 0.1 | Apache Doris, ClickHouse Local, Dask, DataFusion, DuckDB, Pandas, Polars, PySpark, SQLite, Spark, StarRocks |
| amplab | 1.0 | Apache Doris, ClickHouse Local, Dask, DataFusion, DuckDB, Pandas, Polars, PySpark, SQLite, Spark, StarRocks |
| clickbench | 1.0 | Apache Doris, ClickHouse Local, DataFusion, DuckDB, Spark, StarRocks |
| coffeeshop | 0.01 | Apache Doris, ClickHouse Local, Dask, DataFusion, DuckDB, Pandas, Polars, PostgreSQL, Spark, StarRocks |
| coffeeshop | 0.1 | Apache Doris, ClickHouse Local, Dask, DataFusion, DuckDB, Pandas, Polars, PostgreSQL, Spark, StarRocks |
| coffeeshop | 1.0 | Apache Doris, ClickHouse Local, Dask, DataFusion, DuckDB, Pandas, Polars, PostgreSQL, Spark, StarRocks |
| datavault | 0.01 | Apache Doris, ClickHouse Local, DuckDB, StarRocks |
| datavault | 0.1 | Apache Doris, ClickHouse Local, DuckDB, StarRocks |
| datavault | 1.0 | Apache Doris, ClickHouse Local, DuckDB, StarRocks |
| flightdata | 0.01 | Apache Doris, ClickHouse Local, DuckDB, Polars, StarRocks |
| flightdata | 0.1 | Apache Doris, ClickHouse Local, DuckDB, Polars, StarRocks |
| flightdata | 1.0 | ClickHouse Local |
| h2odb | 0.01 | ClickHouse Local, Dask, DataFusion, DuckDB, Pandas, Polars, PostgreSQL, PySpark, Spark, StarRocks |
| h2odb | 0.1 | ClickHouse Local, Dask, DataFusion, DuckDB, Pandas, Polars, PostgreSQL, PySpark, Spark, StarRocks |
| h2odb | 1.0 | ClickHouse Local, Dask, DataFusion, DuckDB, Pandas, Polars, PostgreSQL, PySpark, Spark, StarRocks |
| joinorder_synthetic | 1.0 | ClickHouse Local, DuckDB, SQLite, Spark, StarRocks |
| metadata_primitives | 1.0 | DuckDB, Pandas, Polars, PySpark, StarRocks |
| nyctaxi | 0.01 | Dask, DuckDB, Pandas, Polars, PostgreSQL, PySpark, StarRocks |
| nyctaxi | 0.1 | Dask, DuckDB, Pandas, Polars, PostgreSQL, PySpark, StarRocks |
| nyctaxi | 1.0 | Dask, Pandas, Polars, PostgreSQL, PySpark, StarRocks |
| read_primitives | 0.01 | ClickHouse Local, DataFusion, DuckDB, Polars |
| read_primitives | 0.1 | ClickHouse Local, DataFusion, DuckDB |
| ssb | 0.01 | Apache Doris, ClickHouse Local, Dask, DataFusion, DuckDB, Pandas, Polars, PostgreSQL, PySpark, SQLite, Spark, StarRocks |
| ssb | 0.1 | Apache Doris, ClickHouse Local, Dask, DataFusion, DuckDB, Pandas, Polars, PostgreSQL, PySpark, SQLite, Spark, StarRocks |
| ssb | 1.0 | Apache Doris, ClickHouse Local, Dask, DataFusion, DuckDB, Pandas, Polars, PostgreSQL, PySpark, SQLite, Spark, StarRocks |
| tpcdi | 0.01 | Apache Doris, DuckDB, StarRocks |
| tpcdi | 0.1 | Apache Doris, DuckDB, StarRocks |
| tpcdi | 1.0 | Apache Doris, DuckDB, StarRocks |
| tpcds | 0.01 | Polars |
| tpcds | 0.1 | Polars |
| tpcds | 1.0 | Polars |
| tpcds_obt | 1.0 | DuckDB, Pandas, Polars, PySpark |
| tpch | 0.01 | CedarDB, ClickHouse Local, Dask, DataFusion, DuckDB, Polars, PostgreSQL, Presto, PySpark, SQLite, Spark, StarRocks |
| tpch | 0.1 | CedarDB, ClickHouse Local, Dask, DataFusion, DuckDB, Polars, PySpark, Spark, StarRocks |
| tpch | 1.0 | CedarDB, ClickHouse Local, Dask, DataFusion, DuckDB, Polars, PySpark, Spark, StarRocks |
| tpch_skew | 0.01 | ClickHouse Local, DataFusion, DuckDB, Pandas, Polars, PostgreSQL, PySpark, SQLite, Spark, StarRocks |
| tpch_skew | 0.1 | ClickHouse Local, DataFusion, DuckDB, Pandas, Polars, PySpark, Spark, StarRocks |
| tpch_skew | 1.0 | ClickHouse Local, DataFusion, DuckDB, Pandas, Polars, PySpark, Spark, StarRocks |
| tsbs_devops | 0.01 | ClickHouse Local, Dask, DuckDB, Pandas, Polars, PostgreSQL, PySpark, StarRocks |
| tsbs_devops | 0.1 | ClickHouse Local, Dask, DuckDB, Pandas, Polars, PostgreSQL, PySpark, StarRocks |
| tsbs_devops | 1.0 | ClickHouse Local, Dask, DuckDB, Pandas, Polars, PostgreSQL, PySpark, StarRocks |
| vector_search | 0.01 | ClickHouse Local, DuckDB, StarRocks |
| vector_search | 0.1 | ClickHouse Local, DuckDB, StarRocks |
| vector_search | 1.0 | ClickHouse Local, DuckDB, StarRocks |

48 benchmark x scale cells, 325 removed platform-tuned bundles total (some
cells have more than one bundle per platform, e.g. DataFusion sql + df
variants both landed under `tuning_mode=tuned`).

## Regeneration steps

1. Confirm the platform's tuned path is fixed and covered by a soundness test
   (see #1176 / #1180 follow-up work).
2. Re-run: `benchbox run --platform <platform> --benchmark <benchmark> --scale <sf> --tuning tuned --phases generate,load,power`
3. Package and add the bundle under `results-data/bundles/`.
4. Regenerate the inventory: `uv run -- python scripts/generate_corpus_inventory.py --write`
5. Re-run `uv run -- python results-data/validate_corpus.py` to confirm the
   cohort still meets the >=3-platform depth gate.

---

# Corpus coverage after the 2026-08-23 trust boundary
**Checked-in validation:** The checked-in inventory and `results-data/validate_corpus.py`
are the current source of corpus counts and validation status. Regenerate and check
the inventory before relying on either value.

## Trust boundary

The 2026-08-28 trust cut (PRs #1939 develop, #1940 published-results) withdrew
every result run before 2026-08-23. That left a 9-bundle / 3-cohort corpus. The
withdrawal was intentional; coverage has to be rebuilt with fresh runs only.
Do not restore withdrawn bundles from git history.

## Covered cohorts

The checked-in inventory records **244 bundles**, **16 benchmarks**, and **42
cohorts**, all at the >=3-identity floor. These
benchmarks already have comparable local coverage and must not be re-run for
for coverage recovery:

| Benchmark | Live scales with >=3 identities |
|-----------|----------------------------------|
| amplab | 0.01, 0.1, 1.0 |
| clickbench | 1.0 (plus DuckDB-only SF 10 version-matrix cells) |
| coffeeshop | 0.01, 0.1, 1.0 |
| h2odb | 0.01, 0.1, 1.0 |
| joinorder | 1.0 |
| read_primitives | 0.01, 0.1 |
| ssb | 0.01, 0.1, 1.0 (plus DuckDB-only SF 10 version-matrix cells) |
| tpcds | 1.0 (plus SF 10 cells) |
| tpch | 0.01, 0.1, 1.0 (plus DuckDB-only SF 10 version-matrix cells) |
| tpch_skew | 0.01, 0.1, 1.0 |

## Alias note: `star_schema`

`star_schema` is a CLI / historical schema-v2 alias of **`ssb`**
(`benchbox/cli/commands/run.py` maps `star_schema` → `ssb`; current SSB
bundles use `benchmark.id = "ssb"`). It is **not** a separate remaining-gap
benchmark. Do not admit a duplicate `star_schema` cohort beside live `ssb`
coverage.

## Zero-coverage local cells recorded on 2026-09-04

Benchmarks with **no** checked-in primary bundles after the trust cut:

| Benchmark | Smallest local target cell | Notes |
|-----------|----------------------------|-------|
| datavault | SF 0.01 × >=3 local platforms | Prefer DuckDB, DataFusion, ClickHouse Local / SQLite |
| flightdata | SF 0.01 × >=3 local platforms | Same local platform set |
| metadata_primitives | SF 1.0 × >=3 local platforms | Scale is unused for sizing; needs three validation-passed platforms |
| nyctaxi | SF 0.01 × >=3 local platforms | |
| tpcdi | SF 0.01 × >=3 local platforms | |
| tpcds_obt | SF 0.01 × >=3 local platforms | Recorded as SF 1.0 when the benchmark accepted only SF 1; it has accepted the TPC-DS range (SF 0.01 to 100000) since 2026-11-11 |
| tpchavoc | SF 0.01 × >=3 local platforms | Experimental; large query count |
| write_primitives | SF 0.01 × >=3 local platforms | Needs three validation-passed local platforms |
| star_schema | — | Alias of `ssb` — **not admitted as a separate cell** |

Skip SF 10 expansion for this coverage work. No cloud-vendor account signup
(Databricks / Snowflake / BigQuery). The supported seed lane is local-only and
does not require ClickHouse Cloud credentials.

## Admission rules for uncovered cells

1. Fresh post-2026-08-23 runs only; never copy withdrawn git-history bundles.
2. Real validation phase must execute; admit only when
   `summary.validation == passed`.
3. Do not commit a `benchbox submit` community sidecar unless a community
   submission is genuinely wanted. Sidecar absence continues to derive
   `maintainer-run` trust in `scripts/generate_corpus_inventory.py`.
4. Prefer existing local platforms (DuckDB, DataFusion, SQLite, Polars,
   ClickHouse Local) at the smallest supported scale.
5. Each admitted cohort must meet the >=3-platform floor in
   `results-data/validate_corpus.py`.

## Recorded coverage outcome

Recorded when replacement bundles were copied into `results-data/bundles/`
and the inventory regenerated:

| Benchmark | Admitted cohorts | Deferred |
|-----------|------------------|----------|
| datavault | SF 0.01, SF 0.1 (DuckDB, DataFusion, ClickHouse Local) | SF 1.0 — no full-suite DataFusion validation-passed third platform in local output |
| flightdata | SF 0.01, SF 0.1, SF 1.0 | — |
| nyctaxi | SF 0.01, SF 0.1, SF 1.0 | — |
| tpcdi | SF 0.01, SF 0.1, SF 1.0 | — |
| tpcds_obt | SF 1.0; SF 0.01 and SF 0.1 added 2026-11-11 (DuckDB, DataFusion, Apache Doris, and LakeSail at SF 0.01) | LakeSail SF 0.1: the run failed, see `CORPUS_NOTES.md` |
| tpchavoc | SF 0.01, SF 0.1, SF 1.0 | — |
| vector_search | — (see deferral) | DEFERRED 2026-09-26: single DuckDB tuned-fallback SF 0.01 run (applied_unverified) withheld from promotion, never committed to the seed corpus — each committed cohort needs >=3 comparison identities per results-data/validate_corpus.py. Restore only with two more genuine validation-passed platform runs (ClickHouse Local and StarRocks per the dropped-cell list; DataFusion has no vector_search query path). SF 0.1, SF 1.0 — deferred, no tuned path run yet |
| joinorder_synthetic | — (see deferral) | DEFERRED 2026-09-26: single DuckDB tuned-fallback SF 1.0 run (applied_unverified) withheld from promotion, never committed — the internal surface has no inventory exclusion in scripts/generate_corpus_inventory.py or results-data/validate_corpus.py, so both consumers count it as a one-identity cohort and the depth gate fails. Restore only with two more genuine validation-passed platform runs, or implement the exclusion consistently first. |
| ai_primitives | — (see waiver) | WAIVED 2026-09-26: DuckDB tuned path enables FK enforcement that breaks ai_primitives load order (bulk-load constraint violations on customer/lineitem/nation/orders/partsupp/supplier); notuning succeeds. Tuned regen blocked on the benchmark's load-order defect, not on tuning. |
| metadata_primitives | — (see deferral) | DuckDB + DataFusion validation-passed locally; ClickHouse Local incompatible (ACL privileges); SQLite fresh run 2026-09-04 failed all 51 queries |
| write_primitives | — (see deferral) | Only DuckDB validation-passed locally; DataFusion SF 0.01 failed missing write-ops tables; ClickHouse Local SF 0.01 failed schema NOT NULL; SQLite/Spark/Polars remain partial |
| star_schema | not applicable | Alias of `ssb`; do not duplicate |
