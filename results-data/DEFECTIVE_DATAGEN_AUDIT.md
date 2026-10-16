# Defective Generator Audit: macOS dbgen Address Data

## Verdict

140 of the 395 bundles in the checked-in corpus were generated from TPC-H-family
data written by the defective macOS dbgen builds (pre-#2622), which emitted
non-canonical supplier and customer addresses. Query values that read those
addresses (Q2, Q10, Q15, Q20) are wrong in the affected bundles.

| Tier | Meaning | Bundles |
| --- | --- | --- |
| A | Direct evidence: same-root cache created before the run is non-canonical | 53 |
| B | Presumed: same-root cache regenerated after the run is non-canonical | 52 |
| C | Unresolved: no surviving cache, run predates the 2026-09-24 stale-stamp regeneration | 35 |

Affected benchmarks: tpch (46), tpch_skew (24), read_primitives (18), tpchavoc
(18), datavault (16), transaction_primitives (9), write_primitives (9). All 140
record Darwin/Apple hosts. No benchmark in the list uses dsdgen, so no Windows
TPC-DS scale-factor defect applies to any checked-in bundle.

Per-bundle evidence is in `audit-defective-generators-bundles.csv` (140 rows:
53 A, 52 B, 35 C). Cross-checks performed: `results-data/corpus-inventory.json`
totals 395 bundles; every listed bundle file resolves under
`results-data/bundles/`; recomputed SHA-256 of all 140 primaries matches the
`bundle_hash` recorded in their sidecars (0 mismatches); the packaged
darwin-arm64 dbgen hash is not in the known-defective set.

## Remediation in this change

- Every affected bundle carries `known_defects:
  ["defective-macdbgen-addresses"]` in its `.manifest.json` sidecar (122
  sidecars extended, 18 created for bundles that had none; created sidecars
  record `result_source: internal` so trust labels are unchanged).
- The explorer pipeline reads that entry and sets `ranking_exclusion_reason`
  to `known_defective_data` on both the manifest entry and the detail result,
  which removes the bundle from ranked tables on the next publish. The entry
  is validated against a closed defect vocabulary at submission time; a broken
  or absent sidecar leaves the previous reason untouched.
- Stale-cache reuse is closed by the datagen stamp: manifests now record the
  generator binary hash, and a manifest with no binary stamp or a
  known-defective binary stamp regenerates instead of reusing the cache.
  Observed live: the pre-existing tpch SF0.1 cache without a stamp was
  discarded and rewritten stamped with the fixed darwin-arm64 dbgen hash.
- `results-data/corpus-inventory.json` is intentionally untouched: no bundle
  was added or removed here and trust labels are unchanged, so a regen would
  be a no-op diff.

## Local-engine regeneration

Cheap local cells are re-run with the fixed generator; each re-run lands as a
new bundle and the defective predecessor stays in place, labelled and
unranked. Status:

- tpch SF0.1 on SQLite: re-run started during the audit but produced no
  output in 90 minutes and was terminated; rerunning the cell (for
  example `benchbox run --platform sqlite --benchmark tpch --scale 0.1
  --non-interactive` with `BENCHBOX_OUTPUT_DIR=~/Developer/benchmark_runs`)
  is a follow-up. The stale cache was still discarded and regenerated
  with the fixed binary during the attempt (see cache note above). Fresh
  bundles are not ingested into the corpus by this change; ingesting
  replacements (new bundle files plus inventory update) is a follow-up
  per cell.

Remaining local/dataframe cells (79 bundles across 70 benchmark x platform x
scale cells) are follow-ups, cheapest first: SF0.01/SF0.1 SQLite, DuckDB,
DataFusion, Pandas and Polars across tpch, tpch_skew, tpchavoc,
read_primitives and datavault; then SF1.0 local cells; then the seven DuckDB
SF10 version-matrix medians, the Spark/PySpark SF0.01-SF1.0 cells, and the
single-host cells (CedarDB, ClickHouse Server, DuckLake, LakeSail, StarRocks).

## Paid cloud reruns (follow-ups, not run here)

61 cells need paid re-runs before their defective predecessors can be
superseded. tpch, tpch_skew and tpchavoc at SF0.1, SF1.0 and SF10 on each of
BigQuery, Databricks and Snowflake (27 cells); datavault at SF0.1, SF1.0 and
SF10 on Databricks and Snowflake plus SF0.01, SF0.1, SF1.0 and SF10 on
BigQuery (10 cells); read_primitives at SF0.01 and SF0.1 on all three
(6 cells); transaction_primitives and write_primitives at SF0.01, SF0.1 and
SF1.0 on all three (18 cells).

## Not a defect: TPC-DS reason table size

TPC-DS Q85 and Q93 answers differ from the official answer files, but the data
is not defective. The official TPC-DS v4.0.0 kit's `scaling.dst`, which BenchBox
ships unchanged, generates a 75-row `reason` table at every scale from SF 1 up, so
`sr_reason_sk`, `cr_reason_sk` and `wr_reason_sk` span 1 to 75. Specification
4.0.0 Table 3-2 still lists 35 rows at SF 1, and the official answer files were
not regenerated for v4.0.0. Clause 3.4.2 says dsdgen prevails over the table.
Every platform ran on the same v4.0.0 data, so cross-platform comparisons are
unaffected, and no TPC-DS bundle carries a `known_defects` label.
