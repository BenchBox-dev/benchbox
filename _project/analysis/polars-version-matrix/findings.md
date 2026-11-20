# Polars version matrix: findings

Method, controls and selection rules: `methodology.md`. Per-comparison values: `polars-version-matrix-comparisons.csv`. Qualification evidence: `qualification.json`.

Totals are the sum of per-query wall times for one power run, in seconds: median (min-max) of three rounds. Cell medians and ranges, outcomes, peak RSS, the largest swap growth per cell and every analyzer comparison are read from `polars-version-matrix-analysis.json` for the 2026-11-19 run (BenchBox commit `82e76af81`, 156 runs). Per-run details are read from the run manifest, which is not committed: exit codes, spill, swap growth in individual rounds, per-run totals of runs that did not qualify, load averages and the round effect. Values marked "computed" are derived from the cell medians and ranges with the same candidate-mover rule; they are not among the analyzer's 59 comparisons.
Setups A, B and C use `--execution-engine default`, `--execution-engine in-memory`, and `--execution-engine streaming`, respectively.

## Summary

- On the default engine, Polars 2.0.0 is faster than 1.44.2 on the three benchmarks where both qualified: TPC-H -41.7%, SSB -36.2%, ClickBench -49.6%. 2.1.0 is faster still: TPC-H -45.0%, SSB -41.0%, ClickBench -52.8%. All six are candidate movers. The 2026-10-09 run gave -42.3%, -38.3% and -49.8% for 2.0.0.
- Most of that comes from running on the streaming engine, and most of the streaming gain was already available on 1.44.2. 1.44.2 streaming (C) against 1.44.2 default (A) is TPC-H -28.1%, SSB -26.8% and ClickBench -42.4% (computed; each pair has non-overlapping ranges), which is 67%, 74% and 86% of 2.0.0's default-settings gain and 62%, 65% and 80% of 2.1.0's.
- Forced to the in-memory engine (setup B), 2.0.0 is within +4.2% to +15.5% of 1.44.2's default and 2.1.0 within -12.9% to +2.2%; only 2.1.0 on ClickBench (-12.9%) is a candidate mover.
- Streaming against streaming (setup C), 2.0.0 and 2.1.0 are faster than 1.44.2 on every benchmark where both qualified: -7.8% to -41.4% for 2.0.0 and -12.6% to -27.7% for 2.1.0.
- 2.1.0 against 2.0.0 (computed): on the default and streaming engines the medians differ by -8.8% to +4.1%, with overlapping ranges, so there are no candidate movers. On the in-memory engine 2.1.0 is faster on all three benchmarks: TPC-H -18.3% (ranges overlap), SSB -3.9% and ClickBench -16.4% (a candidate mover).
- TPC-DS is decided by memory on this 16 GiB host. Only 2.0.0 default (A), 2.0.0 streaming (C) and 1.44.2 streaming (C) qualified. Every 1.x default run was stopped. 2.1.0 default and streaming finished every round with every query successful and per-run totals close to 2.0.0's, but each round swapped 1.0 to 5.9 GiB on the host and spilled about 0.5 GiB to disk, so the 1 GiB host-wide swap limit ruled them out. 2.0.0 default and streaming swapped at most 0.28 GiB and did not spill.

## Headline comparisons

### 2.0.0 default engine (A) against 1.44.2 default engine (A)

| Benchmark | Candidate | Baseline | Median change | Ranges overlap | Candidate mover |
|---|---|---|---|---|---|
| tpch | 2.0.0 A: 6.53 (6.06-7.59) | 1.44.2 A: 11.20 (10.64-13.28) | -41.7% (1.72x) | no | yes |
| tpcds | 2.0.0 A: 34.13 (30.91-36.43) | 1.44.2 A: not reported | no comparison: 1.44.2 A not reported | | |
| ssb | 2.0.0 A: 3.58 (3.30-4.08) | 1.44.2 A: 5.61 (5.15-7.07) | -36.2% (1.57x) | no | yes |
| clickbench | 2.0.0 A: 2.31 (2.17-2.33) | 1.44.2 A: 4.58 (4.34-4.67) | -49.6% (1.98x) | no | yes |

### 2.1.0 default engine (A) against 1.44.2 default engine (A)

| Benchmark | Candidate | Baseline | Median change | Ranges overlap | Candidate mover |
|---|---|---|---|---|---|
| tpch | 2.1.0 A: 6.16 (6.04-6.73) | 1.44.2 A: 11.20 (10.64-13.28) | -45.0% (1.82x) | no | yes |
| tpcds | 2.1.0 A: not reported | 1.44.2 A: not reported | no comparison: 2.1.0 A not reported; 1.44.2 A not reported | | |
| ssb | 2.1.0 A: 3.31 (3.31-3.94) | 1.44.2 A: 5.61 (5.15-7.07) | -41.0% (1.69x) | no | yes |
| clickbench | 2.1.0 A: 2.16 (2.02-2.37) | 1.44.2 A: 4.58 (4.34-4.67) | -52.8% (2.12x) | no | yes |

### In-memory (B) against 1.44.2 default engine (A)

| Benchmark | Candidate | Baseline | Median change | Ranges overlap | Candidate mover |
|---|---|---|---|---|---|
| tpch | 2.0.0 B: 12.94 (11.47-13.28) | 1.44.2 A: 11.20 (10.64-13.28) | +15.5% (0.87x) | yes | no |
| tpch | 2.1.0 B: 10.58 (10.21-12.71) | 1.44.2 A: 11.20 (10.64-13.28) | -5.6% (1.06x) | yes | no |
| tpcds | 2.0.0 B: not reported | 1.44.2 A: not reported | no comparison: 2.0.0 B not reported; 1.44.2 A not reported | | |
| tpcds | 2.1.0 B: not reported | 1.44.2 A: not reported | no comparison: 2.1.0 B not reported; 1.44.2 A not reported | | |
| ssb | 2.0.0 B: 5.97 (5.56-7.60) | 1.44.2 A: 5.61 (5.15-7.07) | +6.3% (0.94x) | yes | no |
| ssb | 2.1.0 B: 5.73 (5.18-6.90) | 1.44.2 A: 5.61 (5.15-7.07) | +2.2% (0.98x) | yes | no |
| clickbench | 2.0.0 B: 4.77 (4.54-4.99) | 1.44.2 A: 4.58 (4.34-4.67) | +4.2% (0.96x) | yes | no |
| clickbench | 2.1.0 B: 3.99 (3.90-4.30) | 1.44.2 A: 4.58 (4.34-4.67) | -12.9% (1.15x) | no | yes |

### Streaming (C) against 1.44.2 streaming (C)

| Benchmark | Candidate | Baseline | Median change | Ranges overlap | Candidate mover |
|---|---|---|---|---|---|
| tpch | 2.0.0 C: 6.32 (6.12-7.61) | 1.44.2 C: 8.06 (8.02-8.28) | -21.5% (1.27x) | no | yes |
| tpch | 2.1.0 C: 5.83 (5.75-6.55) | 1.44.2 C: 8.06 (8.02-8.28) | -27.7% (1.38x) | no | yes |
| tpcds | 2.0.0 C: 36.53 (33.85-44.86) | 1.44.2 C: 62.27 (59.91-66.54) | -41.4% (1.71x) | no | yes |
| tpcds | 2.1.0 C: not reported | 1.44.2 C: 62.27 (59.91-66.54) | no comparison: 2.1.0 C not reported | | |
| ssb | 2.0.0 C: 3.79 (3.31-4.02) | 1.44.2 C: 4.11 (4.05-4.69) | -7.8% (1.08x) | no | no |
| ssb | 2.1.0 C: 3.45 (3.45-3.93) | 1.44.2 C: 4.11 (4.05-4.69) | -15.9% (1.19x) | no | yes |
| clickbench | 2.0.0 C: 2.21 (2.20-2.44) | 1.44.2 C: 2.64 (2.57-3.09) | -16.1% (1.19x) | no | yes |
| clickbench | 2.1.0 C: 2.30 (2.12-2.39) | 1.44.2 C: 2.64 (2.57-3.09) | -12.6% (1.14x) | no | yes |

### 2.1.0 against 2.0.0 (computed)

| Benchmark | Setup | 2.1.0 | 2.0.0 | Median change | Ranges overlap | Candidate mover |
|---|---|---|---|---|---|---|
| tpch | A | 6.16 (6.04-6.73) | 6.53 (6.06-7.59) | -5.6% | yes | no |
| tpch | B | 10.58 (10.21-12.71) | 12.94 (11.47-13.28) | -18.3% | yes | no |
| tpch | C | 5.83 (5.75-6.55) | 6.32 (6.12-7.61) | -7.9% | yes | no |
| ssb | A | 3.31 (3.31-3.94) | 3.58 (3.30-4.08) | -7.5% | yes | no |
| ssb | B | 5.73 (5.18-6.90) | 5.97 (5.56-7.60) | -3.9% | yes | no |
| ssb | C | 3.45 (3.45-3.93) | 3.79 (3.31-4.02) | -8.8% | yes | no |
| clickbench | A | 2.16 (2.02-2.37) | 2.31 (2.17-2.33) | -6.5% | yes | no |
| clickbench | B | 3.99 (3.90-4.30) | 4.77 (4.54-4.99) | -16.4% | no | yes |
| clickbench | C | 2.30 (2.12-2.39) | 2.21 (2.20-2.44) | +4.1% | yes | no |

TPC-DS has no 2.1.0 cell to compare: none of its setups qualified.

### 1.44.2 streaming (C) against 1.44.2 default (A) (computed)

| Benchmark | 1.44.2 C | 1.44.2 A | Median change | Ranges overlap | Share of 2.0.0 A gain | Share of 2.1.0 A gain |
|---|---|---|---|---|---|---|
| tpch | 8.06 (8.02-8.28) | 11.20 (10.64-13.28) | -28.1% | no | 67% | 62% |
| ssb | 4.11 (4.05-4.69) | 5.61 (5.15-7.07) | -26.8% | no | 74% | 65% |
| clickbench | 2.64 (2.57-3.09) | 4.58 (4.34-4.67) | -42.4% | no | 86% | 80% |

The share is the 1.44.2 A to C saving divided by the 1.44.2 A to 2.x A saving, from the medians.

## Trend by version

### Setup A

| Benchmark | 1.31.0 | 1.35.2 | 1.40.1 | 1.44.2 | 2.0.0 | 2.1.0 |
|---|---|---|---|---|---|---|
| tpch | not reported | 12.14 (11.96-13.18) | 14.20 (11.70-16.03) | 11.20 (10.64-13.28) | 6.53 (6.06-7.59) | 6.16 (6.04-6.73) |
| tpcds | not reported | not reported | not reported | not reported | 34.13 (30.91-36.43) | not reported |
| ssb | 6.99 (6.13-7.50) | 6.05 (5.53-6.92) | 5.61 (5.09-7.17) | 5.61 (5.15-7.07) | 3.58 (3.30-4.08) | 3.31 (3.31-3.94) |
| clickbench | 5.04 (4.71-5.79) | 4.62 (4.59-5.77) | 4.72 (4.31-5.06) | 4.58 (4.34-4.67) | 2.31 (2.17-2.33) | 2.16 (2.02-2.37) |

### Setup C

| Benchmark | 1.31.0 | 1.40.1 | 1.44.2 | 2.0.0 | 2.1.0 |
|---|---|---|---|---|---|
| tpch | 11.02 (11.01-12.79) | 7.91 (7.54-9.61) | 8.06 (8.02-8.28) | 6.32 (6.12-7.61) | 5.83 (5.75-6.55) |
| tpcds | not reported | not reported | 62.27 (59.91-66.54) | 36.53 (33.85-44.86) | not reported |
| ssb | 4.33 (4.25-5.01) | 4.10 (4.06-5.14) | 4.11 (4.05-4.69) | 3.79 (3.31-4.02) | 3.45 (3.45-3.93) |
| clickbench | 2.91 (2.90-2.96) | 2.50 (2.50-3.00) | 2.64 (2.57-3.09) | 2.21 (2.20-2.44) | 2.30 (2.12-2.39) |

### Setup B

| Benchmark | 2.0.0 | 2.1.0 |
|---|---|---|
| tpch | 12.94 (11.47-13.28) | 10.58 (10.21-12.71) |
| tpcds | not reported | not reported |
| ssb | 5.97 (5.56-7.60) | 5.73 (5.18-6.90) |
| clickbench | 4.77 (4.54-4.99) | 3.99 (3.90-4.30) |

## Cells that did not qualify

Exit codes are shown where the run did not exit 0: -9 is the runner's safety monitor, 143 is the host sentinel's SIGTERM (see `methodology.md`), 138 and 139 are crashes.

| Benchmark | Setup | Version | Outcomes by round | Peak RSS, worst round (GiB) | Largest swap growth (GiB) | Largest spill (GiB) |
|---|---|---|---|---|---|---|
| tpch | A | 1.31.0 | resource-policy-exceeded, resource-policy-exceeded, completed | 7.088 | 2.294 | 0.000 |
| tpcds | A | 1.31.0 | execution-failure (143), execution-failure (143), resource-policy-exceeded (-9) | 4.309 | 6.830 | 0.000 |
| tpcds | A | 1.35.2 | execution-failure (143), resource-policy-exceeded (-9), execution-failure (143) | 8.443 | 7.815 | 0.000 |
| tpcds | A | 1.40.1 | execution-failure (143), execution-failure (143), execution-failure (143) | 6.248 | 7.502 | 0.000 |
| tpcds | A | 1.44.2 | resource-policy-exceeded (-9), resource-policy-exceeded (-9), resource-policy-exceeded (-9) | 8.865 | 7.935 | 0.000 |
| tpcds | A | 2.1.0 | resource-policy-exceeded, resource-policy-exceeded, resource-policy-exceeded | 6.483 | 4.956 | 0.574 |
| tpcds | B | 2.0.0 | resource-policy-exceeded, resource-policy-exceeded, completed | 7.668 | 3.026 | 0.000 |
| tpcds | B | 2.1.0 | resource-policy-exceeded (-9), execution-failure (143), completed | 7.232 | 6.216 | 0.000 |
| tpcds | C | 1.31.0 | execution-failure (139), execution-failure (138), execution-failure (138) | 5.078 | 0.000 | 0.000 |
| tpcds | C | 1.40.1 | resource-policy-exceeded, resource-policy-exceeded, completed | 6.814 | 3.307 | 0.000 |
| tpcds | C | 2.1.0 | resource-policy-exceeded, resource-policy-exceeded, resource-policy-exceeded | 6.535 | 5.922 | 0.510 |

Runs without an exit code in the outcome finished and were ruled out afterwards by the 1 GiB swap limit.

## Candidate movers

59 comparisons were made; 28 are candidate movers (median change of at least 10% with non-overlapping min-max ranges). These are cell totals, not individual queries.

| Benchmark | Candidate | Baseline frame | Baseline | Median change |
|---|---|---|---|---|
| clickbench | 2.0.0 A | oldest | 1.31.0 A | -54.2% |
| clickbench | 2.0.0 C | oldest | 1.31.0 C | -23.9% |
| clickbench | 2.1.0 A | oldest | 1.31.0 A | -57.1% |
| clickbench | 2.1.0 B | oldest | 1.31.0 A | -20.9% |
| clickbench | 2.1.0 C | oldest | 1.31.0 C | -20.7% |
| clickbench | 1.31.0 A | reference | 1.44.2 A | +10.1% |
| clickbench | 2.0.0 A | reference | 1.44.2 A | -49.6% |
| clickbench | 2.0.0 C | reference | 1.44.2 C | -16.1% |
| clickbench | 2.1.0 A | reference | 1.44.2 A | -52.8% |
| clickbench | 2.1.0 B | reference | 1.44.2 A | -12.9% |
| clickbench | 2.1.0 C | reference | 1.44.2 C | -12.6% |
| ssb | 2.0.0 A | oldest | 1.31.0 A | -48.7% |
| ssb | 2.0.0 C | oldest | 1.31.0 C | -12.6% |
| ssb | 2.1.0 A | oldest | 1.31.0 A | -52.6% |
| ssb | 2.1.0 C | oldest | 1.31.0 C | -20.3% |
| ssb | 2.0.0 A | reference | 1.44.2 A | -36.2% |
| ssb | 2.1.0 A | reference | 1.44.2 A | -41.0% |
| ssb | 2.1.0 C | reference | 1.44.2 C | -15.9% |
| tpcds | 2.0.0 C | reference | 1.44.2 C | -41.4% |
| tpch | 1.40.1 C | oldest | 1.31.0 C | -28.2% |
| tpch | 1.44.2 C | oldest | 1.31.0 C | -26.9% |
| tpch | 2.0.0 C | oldest | 1.31.0 C | -42.6% |
| tpch | 2.1.0 C | oldest | 1.31.0 C | -47.1% |
| tpch | 1.31.0 C | reference | 1.44.2 C | +36.8% |
| tpch | 2.0.0 A | reference | 1.44.2 A | -41.7% |
| tpch | 2.0.0 C | reference | 1.44.2 C | -21.5% |
| tpch | 2.1.0 A | reference | 1.44.2 A | -45.0% |
| tpch | 2.1.0 C | reference | 1.44.2 C | -27.7% |

## Limits and what was not shown

- Correctness evidence. Value-level agreement was checked per version and setup with the cross-surface gates, at scale factor 0.01 for TPC-H and TPC-DS and 0.1 for SSB and ClickBench. At the measured scale factor 10 only per-query row counts against DuckDB SQL results were checked, so no claim of value-level correctness at scale factor 10 is made. TPC-DS query 41 returns 10 rows on Polars against 12 on DuckDB; it stays in the full-suite totals and is left out of the common-query totals. In every reported TPC-DS cell the two totals differ by at most 0.021 s at the median and 0.029 s at the minimum or maximum.
- Engine. The recorded engine is the requested collect policy. Streaming fallback and intermediate materialization were not observed.
- Not comparable with DuckDB. Polars timings include reading Parquet in each run; DuckDB matrix timings read a pre-loaded database. No speed comparison with DuckDB is made.
- Candidate movers are cell totals, flagged when the median changes by at least 10% and the min-max ranges do not overlap. They are candidates, not significant results. Three rounds per cell do not support a significance test, and 59 analyzer comparisons plus 12 computed ones were made. No per-query movers are reported: the analyzer works on cell totals only.
- Resource outcomes depend on this host (M4, 10 cores, 16 GiB) and on how the resource rules were measured. Swap growth is measured for the whole host, not per process, and other work was running on the host, so a cell ruled out by the 1 GiB swap limit was decided by a host-wide counter. That applies to the 2.1.0 TPC-DS default and streaming cells and to TPC-H 1.31.0 default (2.29 and 1.59 GiB in its failing rounds). The 14 GiB memory limit was never exceeded.
- 2.1.0 on TPC-DS. Its default and streaming runs spilled 0.47 to 0.57 GiB in every round and finished with every query successful. Their per-run totals, 34.67 to 42.13 s on the default engine and 36.26 to 40.74 s streaming, fall within or near 2.0.0's ranges. The swap they were ruled out on is host-wide; whether 2.1.0 itself caused it, and whether it would occur on a host with more memory, is not established.
- Sentinel stops. The host's heavy-work sentinel sent SIGTERM to eight TPC-DS runs at critical memory pressure (exit 143). Every one had already passed the 1 GiB swap limit when it was stopped (swap growth 1.98 to 7.50 GiB), and the swap-out counter only grows, so each would have been ruled out by that limit had it finished; none of the stops changed which cells qualified. TPC-DS 1.40.1 default (A) lost all three rounds to the sentinel (swap growth 7.50, 5.31 and 3.41 GiB), so its outcome rests on that evidence alone.
- Host load. The 1-minute load average read before each run was 10.8 to 43.1 (median 16.8), partly from the preceding run's threads and partly from other work on the machine. Memory pressure before every run was normal (level 1). The runner does not check for other processes between runs.
- Round effect. Averaged over the 41 qualified cells, round 1 ran 9.4% above each cell median, round 2 0.1% above and round 3 1.1% below. Four runs finished more than 25% above their cell median (SSB cells, rounds 1 to 3). Comparing each cell's fastest round instead of its median gives the default-engine headline against 1.44.2 as TPC-H -43.0% and -43.2%, SSB -36.0% and -35.7%, ClickBench -49.9% and -53.5% for 2.0.0 and 2.1.0, the same direction and similar size.
- 1.35.2 is in setup A only. Its streaming TPC-DS failure in the 2026-10-09 probe is described in `methodology.md`; the setup was not re-probed. No other 1.x version was substituted.
- Setup C on 1.31.0 crashed on TPC-DS (exit 138 or 139, 12 to 15 s into the run) in all three rounds, as it did in the 2026-10-09 run. The cause was not investigated.
- Versions 1.0.0, 1.12.0 and 1.22.0 were excluded after the 2026-10-09 probe (a panic, a row-count mismatch on TPC-DS query 51, and failures on queries 51 and 87).
- All 156 runs used commit 82e76af81, one shuffle seed (20261119), and identical pins; no round was voided.
