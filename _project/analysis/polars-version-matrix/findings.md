# Polars version matrix: findings

Method, controls and selection rules: `methodology.md`. Per-comparison values: `polars-version-matrix-comparisons.csv`. Qualification evidence: `qualification.json`.

Totals are the sum of per-query wall times for one power run, in seconds: median (min-max) of three rounds. Cell medians and ranges, outcomes, peak RSS, the largest swap growth per cell and every analyzer comparison are read from `polars-version-matrix-analysis.json` for the 2026-11-20 run (BenchBox commit `82e76af81`, 156 runs). Per-run details are read from the run manifest, which is not committed: exit codes, spill, swap growth in individual rounds, per-run totals of runs that did not qualify, load averages and the round effect. Values marked "computed" are derived from the cell medians and ranges with the same candidate-mover rule; they are not among the analyzer's 58 comparisons.
Setups A, B and C use `--execution-engine default`, `--execution-engine in-memory`, and `--execution-engine streaming`, respectively.

This run repeats the 2026-11-19 run on the same commit, with no other BenchBox work on the host when it started. It supersedes that run and the 2026-10-09 run; see "Earlier runs".

## Summary

- On the default engine, Polars 2.0.0 is faster than 1.44.2 on the three benchmarks where both qualified: TPC-H -45.3%, SSB -36.8%, ClickBench -51.4%. 2.1.0: TPC-H -48.5%, SSB -36.0%, ClickBench -52.5%. All six are candidate movers.
- Most of that comes from running on the streaming engine, and much of the streaming gain was already available on 1.44.2. 1.44.2 streaming (C) against 1.44.2 default (A) is TPC-H -30.7%, SSB -19.1% and ClickBench -41.0% (computed; each pair has non-overlapping ranges), which is 68%, 52% and 80% of 2.0.0's default-settings gain and 63%, 53% and 78% of 2.1.0's.
- Forced to the in-memory engine (setup B), 2.0.0 is within -7.0% to +0.9% of 1.44.2's default and 2.1.0 within -8.1% to +4.9%. None is a candidate mover.
- Streaming against streaming (setup C), 2.0.0 and 2.1.0 are faster than 1.44.2 on every benchmark where both qualified: -14.2% to -45.4% for 2.0.0 and -19.0% to -28.8% for 2.1.0. All seven are candidate movers.
- 2.1.0 against 2.0.0 (computed): across the nine pairs on TPC-H, SSB and ClickBench the medians differ by -8.8% to +4.4%, and none is a candidate mover.
- TPC-DS is decided by memory on this 16 GiB host. Only 2.0.0 default (A), 2.0.0 streaming (C) and 1.44.2 streaming (C) qualified. Every 1.x default run was stopped. 2.0.0 default and streaming swapped at most 0.014 GiB and did not spill. 2.1.0 default and streaming finished every round with every query successful, spilled 0.47 to 0.49 GiB in every round, and ran while the host swapped 0.25 to 4.25 GiB; four of the six runs passed the 1 GiB swap limit, so neither cell is reported. Every 2.1.0 default run (33.69 to 35.72 s) was slower than every 2.0.0 default run (29.68 to 32.83 s).

## Headline comparisons

### 2.0.0 default engine (A) against 1.44.2 default engine (A)

| Benchmark | Candidate | Baseline | Median change | Ranges overlap | Candidate mover |
|---|---|---|---|---|---|
| tpch | 2.0.0 A: 6.11 (6.02-6.13) | 1.44.2 A: 11.17 (10.68-12.23) | -45.3% (1.83x) | no | yes |
| tpcds | 2.0.0 A: 30.47 (29.68-32.83) | 1.44.2 A: not reported | no comparison: 1.44.2 A not reported | | |
| ssb | 2.0.0 A: 3.28 (3.20-3.47) | 1.44.2 A: 5.18 (5.01-6.60) | -36.8% (1.58x) | no | yes |
| clickbench | 2.0.0 A: 2.10 (2.10-2.27) | 1.44.2 A: 4.32 (4.29-4.70) | -51.4% (2.06x) | no | yes |

### 2.1.0 default engine (A) against 1.44.2 default engine (A)

| Benchmark | Candidate | Baseline | Median change | Ranges overlap | Candidate mover |
|---|---|---|---|---|---|
| tpch | 2.1.0 A: 5.75 (5.54-6.05) | 1.44.2 A: 11.17 (10.68-12.23) | -48.5% (1.94x) | no | yes |
| tpcds | 2.1.0 A: not reported | 1.44.2 A: not reported | no comparison: 2.1.0 A not reported; 1.44.2 A not reported | | |
| ssb | 2.1.0 A: 3.32 (3.25-3.38) | 1.44.2 A: 5.18 (5.01-6.60) | -36.0% (1.56x) | no | yes |
| clickbench | 2.1.0 A: 2.05 (2.03-2.11) | 1.44.2 A: 4.32 (4.29-4.70) | -52.5% (2.11x) | no | yes |

### In-memory (B) against 1.44.2 default engine (A)

| Benchmark | Candidate | Baseline | Median change | Ranges overlap | Candidate mover |
|---|---|---|---|---|---|
| tpch | 2.0.0 B: 10.39 (9.78-10.61) | 1.44.2 A: 11.17 (10.68-12.23) | -7.0% (1.08x) | no | no |
| tpch | 2.1.0 B: 10.85 (9.71-13.36) | 1.44.2 A: 11.17 (10.68-12.23) | -2.9% (1.03x) | yes | no |
| tpcds | 2.0.0 B: not reported | 1.44.2 A: not reported | no comparison: 2.0.0 B not reported; 1.44.2 A not reported | | |
| tpcds | 2.1.0 B: not reported | 1.44.2 A: not reported | no comparison: 2.1.0 B not reported; 1.44.2 A not reported | | |
| ssb | 2.0.0 B: 5.23 (4.93-5.33) | 1.44.2 A: 5.18 (5.01-6.60) | +0.9% (0.99x) | yes | no |
| ssb | 2.1.0 B: 5.44 (4.96-5.63) | 1.44.2 A: 5.18 (5.01-6.60) | +4.9% (0.95x) | yes | no |
| clickbench | 2.0.0 B: 4.25 (4.18-4.55) | 1.44.2 A: 4.32 (4.29-4.70) | -1.8% (1.02x) | yes | no |
| clickbench | 2.1.0 B: 3.97 (3.80-4.06) | 1.44.2 A: 4.32 (4.29-4.70) | -8.1% (1.09x) | no | no |

### Streaming (C) against 1.44.2 streaming (C)

| Benchmark | Candidate | Baseline | Median change | Ranges overlap | Candidate mover |
|---|---|---|---|---|---|
| tpch | 2.0.0 C: 6.05 (5.96-6.62) | 1.44.2 C: 7.75 (7.49-8.16) | -21.9% (1.28x) | no | yes |
| tpch | 2.1.0 C: 5.51 (5.43-5.87) | 1.44.2 C: 7.75 (7.49-8.16) | -28.8% (1.41x) | no | yes |
| tpcds | 2.0.0 C: 31.26 (30.40-33.14) | 1.44.2 C: 57.21 (56.48-60.54) | -45.4% (1.83x) | no | yes |
| tpcds | 2.1.0 C: not reported | 1.44.2 C: 57.21 (56.48-60.54) | no comparison: 2.1.0 C not reported | | |
| ssb | 2.0.0 C: 3.35 (3.26-3.50) | 1.44.2 C: 4.19 (4.18-4.47) | -20.2% (1.25x) | no | yes |
| ssb | 2.1.0 C: 3.31 (3.23-3.31) | 1.44.2 C: 4.19 (4.18-4.47) | -21.2% (1.27x) | no | yes |
| clickbench | 2.0.0 C: 2.19 (2.11-2.23) | 1.44.2 C: 2.55 (2.42-2.69) | -14.2% (1.17x) | no | yes |
| clickbench | 2.1.0 C: 2.07 (2.01-2.15) | 1.44.2 C: 2.55 (2.42-2.69) | -19.0% (1.23x) | no | yes |

### 2.1.0 against 2.0.0 (computed)

| Benchmark | Setup | 2.1.0 | 2.0.0 | Median change | Ranges overlap | Candidate mover |
|---|---|---|---|---|---|---|
| tpch | A | 5.75 (5.54-6.05) | 6.11 (6.02-6.13) | -5.8% | yes | no |
| tpch | B | 10.85 (9.71-13.36) | 10.39 (9.78-10.61) | +4.4% | yes | no |
| tpch | C | 5.51 (5.43-5.87) | 6.05 (5.96-6.62) | -8.8% | no | no |
| ssb | A | 3.32 (3.25-3.38) | 3.28 (3.20-3.47) | +1.3% | yes | no |
| ssb | B | 5.44 (4.96-5.63) | 5.23 (4.93-5.33) | +4.0% | yes | no |
| ssb | C | 3.31 (3.23-3.31) | 3.35 (3.26-3.50) | -1.3% | yes | no |
| clickbench | A | 2.05 (2.03-2.11) | 2.10 (2.10-2.27) | -2.4% | yes | no |
| clickbench | B | 3.97 (3.80-4.06) | 4.25 (4.18-4.55) | -6.4% | no | no |
| clickbench | C | 2.07 (2.01-2.15) | 2.19 (2.11-2.23) | -5.5% | yes | no |

TPC-DS has no 2.1.0 cell to compare: none of its setups qualified.

### 1.44.2 streaming (C) against 1.44.2 default (A) (computed)

| Benchmark | 1.44.2 C | 1.44.2 A | Median change | Ranges overlap | Share of 2.0.0 A gain | Share of 2.1.0 A gain |
|---|---|---|---|---|---|---|
| tpch | 7.75 (7.49-8.16) | 11.17 (10.68-12.23) | -30.7% | no | 68% | 63% |
| ssb | 4.19 (4.18-4.47) | 5.18 (5.01-6.60) | -19.1% | no | 52% | 53% |
| clickbench | 2.55 (2.42-2.69) | 4.32 (4.29-4.70) | -41.0% | no | 80% | 78% |

The share is the 1.44.2 A to C saving divided by the 1.44.2 A to 2.x A saving, from the medians.

## Trend by version

### Setup A

| Benchmark | 1.31.0 | 1.35.2 | 1.40.1 | 1.44.2 | 2.0.0 | 2.1.0 |
|---|---|---|---|---|---|---|
| tpch | not reported | 11.57 (10.91-12.07) | not reported | 11.17 (10.68-12.23) | 6.11 (6.02-6.13) | 5.75 (5.54-6.05) |
| tpcds | not reported | not reported | not reported | not reported | 30.47 (29.68-32.83) | not reported |
| ssb | 6.19 (5.78-6.20) | 5.49 (5.30-5.68) | 5.28 (4.90-5.32) | 5.18 (5.01-6.60) | 3.28 (3.20-3.47) | 3.32 (3.25-3.38) |
| clickbench | 4.42 (4.37-5.12) | 4.38 (4.32-4.51) | 4.28 (4.20-4.40) | 4.32 (4.29-4.70) | 2.10 (2.10-2.27) | 2.05 (2.03-2.11) |

### Setup C

| Benchmark | 1.31.0 | 1.40.1 | 1.44.2 | 2.0.0 | 2.1.0 |
|---|---|---|---|---|---|
| tpch | 10.88 (10.81-11.18) | 8.10 (7.50-8.46) | 7.75 (7.49-8.16) | 6.05 (5.96-6.62) | 5.51 (5.43-5.87) |
| tpcds | not reported | not reported | 57.21 (56.48-60.54) | 31.26 (30.40-33.14) | not reported |
| ssb | 4.17 (4.13-4.23) | 4.02 (3.95-4.16) | 4.19 (4.18-4.47) | 3.35 (3.26-3.50) | 3.31 (3.23-3.31) |
| clickbench | 2.87 (2.85-2.99) | 2.51 (2.36-2.57) | 2.55 (2.42-2.69) | 2.19 (2.11-2.23) | 2.07 (2.01-2.15) |

### Setup B

| Benchmark | 2.0.0 | 2.1.0 |
|---|---|---|
| tpch | 10.39 (9.78-10.61) | 10.85 (9.71-13.36) |
| tpcds | not reported | not reported |
| ssb | 5.23 (4.93-5.33) | 5.44 (4.96-5.63) |
| clickbench | 4.25 (4.18-4.55) | 3.97 (3.80-4.06) |

## Cells that did not qualify

Exit codes are shown where the run did not exit 0: 143 is the host sentinel's SIGTERM (see `methodology.md`), 138 and 139 are crashes.


| Benchmark | Setup | Version | Outcomes by round | Peak RSS, worst round (GiB) | Largest swap growth (GiB) | Largest spill (GiB) |
|---|---|---|---|---|---|---|
| tpch | A | 1.31.0 | completed, resource-policy-exceeded, completed | 6.232 | 1.413 | 0.000 |
| tpch | A | 1.40.1 | completed, resource-policy-exceeded, completed | 5.652 | 1.208 | 0.000 |
| tpcds | A | 1.31.0 | execution-failure (143), execution-failure (143), execution-failure (143) | 3.079 | 5.931 | 0.000 |
| tpcds | A | 1.35.2 | execution-failure (143), execution-failure (143), execution-failure (143) | 6.251 | 4.830 | 0.000 |
| tpcds | A | 1.40.1 | execution-failure (143), execution-failure (143), execution-failure (143) | 5.785 | 4.628 | 0.000 |
| tpcds | A | 1.44.2 | execution-failure (143), execution-failure (143), execution-failure (143) | 8.139 | 8.806 | 0.000 |
| tpcds | A | 2.1.0 | resource-policy-exceeded, completed, resource-policy-exceeded | 6.885 | 1.305 | 0.475 |
| tpcds | B | 2.0.0 | completed, resource-policy-exceeded, resource-policy-exceeded | 7.436 | 2.552 | 0.000 |
| tpcds | B | 2.1.0 | completed, resource-policy-exceeded, completed | 7.490 | 1.117 | 0.000 |
| tpcds | C | 1.31.0 | execution-failure (138), execution-failure (139), execution-failure (138) | 5.560 | 0.033 | 0.000 |
| tpcds | C | 1.40.1 | completed, completed, resource-policy-exceeded | 7.940 | 2.190 | 0.000 |
| tpcds | C | 2.1.0 | completed, resource-policy-exceeded, resource-policy-exceeded | 6.546 | 4.246 | 0.487 |

Runs without an exit code in the outcome finished and were ruled out afterwards by the 1 GiB swap limit.

## TPC-DS on 2.0.0 and 2.1.0, run by run

From the run manifest. Every run below exited 0 with every query successful.

| Version | Setup | Round | Total (s) | Host swap growth (GiB) | Spill (GiB) | Outcome |
|---|---|---|---|---|---|---|
| 2.0.0 | A | 1 | 29.68 | 0.000 | 0.000 | completed |
| 2.0.0 | A | 2 | 30.47 | 0.000 | 0.000 | completed |
| 2.0.0 | A | 3 | 32.83 | 0.000 | 0.000 | completed |
| 2.0.0 | C | 1 | 31.26 | 0.014 | 0.000 | completed |
| 2.0.0 | C | 2 | 30.40 | 0.000 | 0.000 | completed |
| 2.0.0 | C | 3 | 33.14 | 0.000 | 0.000 | completed |
| 2.1.0 | A | 1 | 33.69 | 1.090 | 0.465 | resource-policy-exceeded |
| 2.1.0 | A | 2 | 34.08 | 0.249 | 0.475 | completed |
| 2.1.0 | A | 3 | 35.72 | 1.305 | 0.465 | resource-policy-exceeded |
| 2.1.0 | C | 1 | 33.44 | 0.289 | 0.467 | completed |
| 2.1.0 | C | 2 | 41.53 | 4.246 | 0.470 | resource-policy-exceeded |
| 2.1.0 | C | 3 | 34.49 | 1.960 | 0.487 | resource-policy-exceeded |

In the same matrix, on the same host, 2.0.0 swapped nothing and did not spill, while every 2.1.0 run spilled and the host swapped during each one. That pattern held in the 2026-11-19 run as well. It points to 2.1.0 using more memory than 2.0.0 for TPC-DS at scale factor 10 on this machine. The swap counter is host-wide, so this is not proof that the 2.1.0 process caused the swap.

## Candidate movers

58 comparisons were made; 34 are candidate movers (median change of at least 10% with non-overlapping min-max ranges). These are cell totals, not individual queries.


| Benchmark | Candidate | Baseline frame | Baseline | Median change |
|---|---|---|---|---|
| clickbench | 1.40.1 C | oldest | 1.31.0 C | -12.5% |
| clickbench | 1.44.2 C | oldest | 1.31.0 C | -11.1% |
| clickbench | 2.0.0 A | oldest | 1.31.0 A | -52.4% |
| clickbench | 2.0.0 C | oldest | 1.31.0 C | -23.8% |
| clickbench | 2.1.0 A | oldest | 1.31.0 A | -53.5% |
| clickbench | 2.1.0 B | oldest | 1.31.0 A | -10.1% |
| clickbench | 2.1.0 C | oldest | 1.31.0 C | -28.0% |
| clickbench | 1.31.0 C | reference | 1.44.2 C | +12.5% |
| clickbench | 2.0.0 A | reference | 1.44.2 A | -51.4% |
| clickbench | 2.0.0 C | reference | 1.44.2 C | -14.2% |
| clickbench | 2.1.0 A | reference | 1.44.2 A | -52.5% |
| clickbench | 2.1.0 C | reference | 1.44.2 C | -19.0% |
| ssb | 1.35.2 A | oldest | 1.31.0 A | -11.4% |
| ssb | 1.40.1 A | oldest | 1.31.0 A | -14.7% |
| ssb | 2.0.0 A | oldest | 1.31.0 A | -47.1% |
| ssb | 2.0.0 B | oldest | 1.31.0 A | -15.6% |
| ssb | 2.0.0 C | oldest | 1.31.0 C | -19.8% |
| ssb | 2.1.0 A | oldest | 1.31.0 A | -46.4% |
| ssb | 2.1.0 B | oldest | 1.31.0 A | -12.2% |
| ssb | 2.1.0 C | oldest | 1.31.0 C | -20.8% |
| ssb | 2.0.0 A | reference | 1.44.2 A | -36.8% |
| ssb | 2.0.0 C | reference | 1.44.2 C | -20.2% |
| ssb | 2.1.0 A | reference | 1.44.2 A | -36.0% |
| ssb | 2.1.0 C | reference | 1.44.2 C | -21.2% |
| tpcds | 2.0.0 C | reference | 1.44.2 C | -45.4% |
| tpch | 1.40.1 C | oldest | 1.31.0 C | -25.6% |
| tpch | 1.44.2 C | oldest | 1.31.0 C | -28.8% |
| tpch | 2.0.0 C | oldest | 1.31.0 C | -44.4% |
| tpch | 2.1.0 C | oldest | 1.31.0 C | -49.3% |
| tpch | 1.31.0 C | reference | 1.44.2 C | +40.5% |
| tpch | 2.0.0 A | reference | 1.44.2 A | -45.3% |
| tpch | 2.0.0 C | reference | 1.44.2 C | -21.9% |
| tpch | 2.1.0 A | reference | 1.44.2 A | -48.5% |
| tpch | 2.1.0 C | reference | 1.44.2 C | -28.8% |

## Earlier runs

The default-engine headline, 2.0.0 against 1.44.2, in each run of this matrix:

| Run | BenchBox commit | Host | TPC-H | SSB | ClickBench |
|---|---|---|---|---|---|
| 2026-10-09 | `ecd08a9f0` | other work active | -42.3% | -38.3% | -49.8% |
| 2026-11-19 | `82e76af81` | other work active | -41.7% | -36.2% | -49.6% |
| 2026-11-20 | `82e76af81` | no other BenchBox work at the start | -45.3% | -36.8% | -51.4% |

Two results from the 2026-11-19 run did not repeat here and should not be cited. In that run 2.0.0 in memory was 4.2% to 15.5% slower than 1.44.2's default, and 2.1.0 in memory was 16.4% faster than 2.0.0 on ClickBench and 18.3% on TPC-H. In this run the in-memory cells of 2.0.0, 2.1.0 and 1.44.2 are within 8.1% of each other on every benchmark. The engine-switch share for SSB also moved between runs (51%, 74%, then 52% of 2.0.0's gain), so it is the least stable figure here.

## Limits and what was not shown

- Correctness evidence. Value-level agreement was checked per version and setup with the cross-surface gates, at scale factor 0.01 for TPC-H and TPC-DS and 0.1 for SSB and ClickBench. At the measured scale factor 10 only per-query row counts against DuckDB SQL results were checked, so no claim of value-level correctness at scale factor 10 is made. TPC-DS query 41 returns 10 rows on Polars against 12 on DuckDB; it stays in the full-suite totals and is left out of the common-query totals. In every reported TPC-DS cell the two totals differ by at most 0.019 s at the median and 0.030 s at the minimum or maximum.
- Engine. The recorded engine is the requested collect policy. Streaming fallback and intermediate materialization were not observed.
- Not comparable with DuckDB. Polars timings include reading Parquet in each run; DuckDB matrix timings read a pre-loaded database. No speed comparison with DuckDB is made.
- Candidate movers are cell totals, flagged when the median changes by at least 10% and the min-max ranges do not overlap. They are candidates, not significant results. Three rounds per cell do not support a significance test, and 58 analyzer comparisons plus 12 computed ones were made. No per-query movers are reported: the analyzer works on cell totals only.
- Resource outcomes depend on this host (M4, 10 cores, 16 GiB) and on how the resource rules were measured. Swap growth is measured for the whole host, not per process. Besides TPC-DS, the 1 GiB swap limit ruled out TPC-H on the default engine for 1.31.0 (1.41 GiB in one round) and 1.40.1 (1.21 GiB in one round); both completed their other two rounds under the limit. The 14 GiB memory limit was never exceeded.
- 2.1.0 on TPC-DS. See the run-by-run table. The cells are not reported because four of six runs passed the swap limit. Whether 2.1.0 would swap or spill on a host with more memory is not established.
- Sentinel stops. The host's heavy-work sentinel sent SIGTERM to twelve runs at critical memory pressure (exit 143): all three rounds of TPC-DS on the default engine for 1.31.0, 1.35.2, 1.40.1 and 1.44.2. Every one had already passed the 1 GiB swap limit when it was stopped (swap growth 2.45 to 8.81 GiB), and the swap-out counter only grows, so each would have been ruled out by that limit had it finished. For these four cells the outcome rests on that evidence alone.
- Host state. No other agent session was running when the run started, and the 1-minute load average was 1.9. The host was not monitored for other work during the run. The load average read before each run was 4.3 to 19.4 (median 13.9), which includes the preceding run's threads. Memory pressure before every run was normal (level 1).
- Round effect. Averaged over the 40 qualified cells, round 1 ran 2.9% below each cell median, round 2 3.5% above and round 3 1.7% above. Comparing each cell's fastest round instead of its median gives the default-engine headline against 1.44.2 as TPC-H -43.7% and -48.1%, SSB -36.1% and -35.1%, ClickBench -51.2% and -52.8% for 2.0.0 and 2.1.0, the same direction and similar size.
- Peak RSS is the largest once-a-second sample in the worst of three rounds. For the same cell it differed by up to 2.7 GiB between this run and the 2026-11-19 run, so it is a rough guide.
- 1.35.2 is in setup A only. Its streaming TPC-DS failure in the 2026-10-09 probe is described in `methodology.md`; the setup was not re-probed. No other 1.x version was substituted.
- Setup C on 1.31.0 crashed on TPC-DS (exit 138 or 139, 12 to 15 s into the run) in all three rounds, as it did in both earlier runs. The cause was not investigated.
- Versions 1.0.0, 1.12.0 and 1.22.0 were excluded after the 2026-10-09 probe (a panic, a row-count mismatch on TPC-DS query 51, and failures on queries 51 and 87).
- All 156 runs used commit 82e76af81, one shuffle seed (20261120), and identical pins; no round was voided.
