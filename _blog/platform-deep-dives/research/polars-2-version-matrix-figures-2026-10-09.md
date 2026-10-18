# Polars 2.0 version matrix: figure mapping and open checks

Source for every number in `outlines/polars-2-version-matrix.md` and the post drafted from it: `polars-version-matrix-analysis.json`, produced by `scripts/analyze_polars_version_matrix.py` over the run manifest (3 rounds, shuffle seed 20261009, BenchBox commit `ecd08a9f0`, qualification SHA-256 `da8ea047afdbc880177f6a295dc8d0e9afd1b95a3f044d906e9c6a26b43d2580`). The same values are in `_project/analysis/polars-version-matrix/findings.md` and the comparison CSV.

## Field mapping

| Figure | Analysis field |
|---|---|
| Cell median and min-max (every table) | `cells[benchmark, setup, version].headline.full_ms.{median,min,max}` divided by 1000 |
| Median change and candidate mover (every comparison) | `comparisons[...]` with `baseline_frame = reference`: `median_change`, `candidate_mover` |
| 42 comparisons, 20 candidate movers | `comparison_count`; count of `candidate_movers` |
| Oldest-version step (TPC-H, setup C, 1.31.0 to 1.40.1, -30.9%) | `comparisons[...]` with `baseline_frame = oldest` |
| "Not reported" and "no result" cells | `cells[...].qualified = false`, with `outcomes` and `reasons` |
| Largest swap growth per cell | `cells[...].swap_out_growth_gib` (largest across rounds) |
| Peak spill 0.0 GiB in all 120 runs | run manifest `records[].spill_peak_gib` (maximum) |
| Load average 3.9 to 39.8; pressure level 1 before every run | run manifest `records[].load_1m_before`, `pressure_before` |
| Crash 17 to 23 s, exit 138 or 139 | run manifest records for 1.31.0 / C / TPC-DS: `elapsed_s`, `exit_code` |
| Query 41: 10 rows on Polars, 12 on DuckDB | qualification file `known_divergences.tpcds["1:41"]` |
| Full-suite against common-query totals differ by 0.02 s or less | `cells[...].headline.full_ms.median` against `common_ms.median` for TPC-DS cells |

## Figures used in the post

| Table | Cells and values |
|---|---|
| Default settings | TPC-H 2.0.0 A 6.89 (6.06-9.51), 1.44.2 A 11.95 (11.19-11.95), -42.3%; SSB 3.23 (3.21-3.44), 5.24 (4.98-5.43), -38.3%; ClickBench 2.23 (2.09-2.82), 4.45 (4.41-4.49), -49.8%; TPC-DS 2.0.0 A 31.01 (30.88-36.94) |
| In-memory | TPC-H 2.0.0 B 11.53 (10.42-11.55), -3.5%; SSB 5.88 (5.75-6.49), +12.2% (candidate mover); ClickBench 4.19 (4.06-4.36), -5.9% |
| Streaming | TPC-H 6.16 (6.10-6.39) against 8.08 (7.59-8.21), -23.8%; TPC-DS 31.28 (30.37-42.83) against 61.30 (59.75-68.11), -49.0%; SSB 3.37 (3.21-3.40) against 4.22 (4.08-4.29), -20.1%; ClickBench 2.20 (2.09-2.21) against 2.55 (2.40-2.83), -13.8% |
| Across 1.x | setup A SSB 6.75, 5.60, 5.25, 5.24, 3.23; ClickBench 4.56, 4.49, 4.30, 4.45, 2.23; setup C TPC-H 11.37, 7.86, 8.08, 6.16; SSB 4.40, 4.08, 4.22, 3.37; ClickBench 2.84, 2.57, 2.55, 2.20 |
| TPC-DS memory | swap growth 8.86, 10.397, 9.024, 9.007 (setup A, 1.31.0 to 1.44.2); 5.695 (2.0.0 B); 2.316 (1.40.1 C); 0.89 (1.31.0 C) |
| TPC-H borderline | 1.31.0 A swap 1.36 GiB in round 3; 1.40.1 A 1.772 and 1.422 GiB in rounds 1 and 2 |

## Checks before publishing

1. Done 2026-10-17: the Polars 2.0 announcement documents that a bare `collect()` uses the streaming engine, and PyPI dates 2.0.0 to 2026-10-06. See `polars-2-version-matrix-verification-2026-10-17.md` (R4, R5).
2. The 1.31.0 streaming TPC-DS crash is not investigated. Run the cell once outside the matrix to get a stack or signal name, or state that it was not investigated.
3. Charts: none yet. If added, take values only from the analysis JSON and cite the field.
4. Re-run the voice and style checks after any edit, including the no-dash rule (ASCII hyphen only).
