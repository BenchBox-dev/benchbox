# Polars version matrix: findings

Method, controls and selection rules: `methodology.md`. Per-comparison values: `polars-version-matrix-comparisons.csv`.

Totals are the sum of per-query wall times for one power run, in seconds: median (min–max) of three rounds. Every value below is read from `polars-version-matrix-analysis.json`.
Setups A, B and C use `--execution-engine default`, `--execution-engine in-memory`, and `--execution-engine streaming`, respectively. The committed 2026-10-09 bundles were recorded under the earlier Polars `engine` option name and map to the same requested values (`default`, `in-memory`, and `streaming`).

## Summary

- On the default engine, Polars 2.0.0 is faster than 1.44.2 on the three benchmarks where both completed: TPC-H -42.3%, SSB -38.3%, ClickBench -49.8% (cell totals, median of three rounds). All three are candidate movers.
- Most of that comes from the new default. 2.0.0 collects with the streaming engine when no engine is given; forced to the in-memory engine (setup B) it is within -5.9% to +12.2% of 1.44.2's default on the three benchmarks where it ran, and only SSB (+12.2%, slower) is a candidate mover.
- Streaming against streaming (setup C), 2.0.0 is faster than 1.44.2 on all four benchmarks: -13.8% to -49.0%, all candidate movers.
- TPC-DS is where memory decides the outcome. On the 16 GiB host, 2.0.0 default (A) completed all three rounds; 1.31.0, 1.35.2, 1.40.1 and 1.44.2 default were stopped by the host-safety limit in every round (swap growth 8.9-10.4 GiB), and 2.0.0 in-memory (B) exceeded the swap limit in every round. No run spilled to disk (peak spill 0.0 GiB in all 120 runs), so 2.0.0 completing is not out-of-core spilling.
- Among 1.x versions, the long-run trend is a gradual improvement on SSB (6.75 s on 1.31.0 to 5.24 s on 1.44.2, default engine) and little change on ClickBench (4.56 s to 4.45 s).

## Headline comparisons

### 2.0.0 default engine (A) against 1.44.2 default engine (A)

| Benchmark | Candidate | Baseline | Median change | Ranges overlap | Candidate mover |
|---|---|---|---|---|---|
| tpch | 2.0.0 A: 6.89 (6.06–9.51) | 1.44.2 A: 11.95 (11.19–11.95) | -42.3% (1.73x) | no | yes |
| tpcds | 2.0.0 A | 1.44.2 A | no comparison: 1.44.2 A not reported (outcome resource-policy-exceeded) | | |
| ssb | 2.0.0 A: 3.23 (3.21–3.44) | 1.44.2 A: 5.24 (4.98–5.43) | -38.3% (1.62x) | no | yes |
| clickbench | 2.0.0 A: 2.23 (2.09–2.82) | 1.44.2 A: 4.45 (4.41–4.49) | -49.8% (1.99x) | no | yes |

### 2.0.0 in-memory (B) against 1.44.2 default engine (A)

| Benchmark | Candidate | Baseline | Median change | Ranges overlap | Candidate mover |
|---|---|---|---|---|---|
| tpch | 2.0.0 B: 11.53 (10.42–11.55) | 1.44.2 A: 11.95 (11.19–11.95) | -3.5% (1.04x) | yes | no |
| tpcds | 2.0.0 B | 1.44.2 A | no comparison: 2.0.0 B not reported (outcome resource-policy-exceeded); 1.44.2 A not reported (outcome resource-policy-exceeded) | | |
| ssb | 2.0.0 B: 5.88 (5.75–6.49) | 1.44.2 A: 5.24 (4.98–5.43) | +12.2% (0.89x) | no | yes |
| clickbench | 2.0.0 B: 4.19 (4.06–4.36) | 1.44.2 A: 4.45 (4.41–4.49) | -5.9% (1.06x) | no | no |

### Streaming (C): 2.0.0 against 1.44.2

| Benchmark | Candidate | Baseline | Median change | Ranges overlap | Candidate mover |
|---|---|---|---|---|---|
| tpch | 2.0.0 C: 6.16 (6.10–6.39) | 1.44.2 C: 8.08 (7.59–8.21) | -23.8% (1.31x) | no | yes |
| tpcds | 2.0.0 C: 31.28 (30.37–42.83) | 1.44.2 C: 61.30 (59.75–68.11) | -49.0% (1.96x) | no | yes |
| ssb | 2.0.0 C: 3.37 (3.21–3.40) | 1.44.2 C: 4.22 (4.08–4.29) | -20.1% (1.25x) | no | yes |
| clickbench | 2.0.0 C: 2.20 (2.09–2.21) | 1.44.2 C: 2.55 (2.40–2.83) | -13.8% (1.16x) | no | yes |

## Trend by version

### Setup A

| Benchmark | 1.31.0 | 1.35.2 | 1.40.1 | 1.44.2 | 2.0.0 |
|---|---|---|---|---|---|
| tpch | not reported | 12.40 (11.87–14.27) | not reported | 11.95 (11.19–11.95) | 6.89 (6.06–9.51) |
| tpcds | not reported | not reported | not reported | not reported | 31.01 (30.88–36.94) |
| ssb | 6.75 (6.07–9.76) | 5.60 (5.29–5.69) | 5.25 (4.93–5.39) | 5.24 (4.98–5.43) | 3.23 (3.21–3.44) |
| clickbench | 4.56 (4.35–6.26) | 4.49 (4.29–6.19) | 4.30 (4.24–5.72) | 4.45 (4.41–4.49) | 2.23 (2.09–2.82) |

### Setup C

| Benchmark | 1.31.0 | 1.35.2 | 1.40.1 | 1.44.2 | 2.0.0 |
|---|---|---|---|---|---|
| tpch | 11.37 (10.99–11.43) | not run | 7.86 (7.59–8.16) | 8.08 (7.59–8.21) | 6.16 (6.10–6.39) |
| tpcds | not reported | not run | not reported | 61.30 (59.75–68.11) | 31.28 (30.37–42.83) |
| ssb | 4.40 (3.99–4.52) | not run | 4.08 (3.79–4.18) | 4.22 (4.08–4.29) | 3.37 (3.21–3.40) |
| clickbench | 2.84 (2.71–2.88) | not run | 2.57 (2.36–2.57) | 2.55 (2.40–2.83) | 2.20 (2.09–2.21) |

### Setup B

| Benchmark | 1.31.0 | 1.35.2 | 1.40.1 | 1.44.2 | 2.0.0 |
|---|---|---|---|---|---|
| tpch | not run | not run | not run | not run | 11.53 (10.42–11.55) |
| tpcds | not run | not run | not run | not run | not reported |
| ssb | not run | not run | not run | not run | 5.88 (5.75–6.49) |
| clickbench | not run | not run | not run | not run | 4.19 (4.06–4.36) |

## Cells that did not qualify

| Benchmark | Setup | Version | Outcomes | Peak RSS in worst round (GiB) | Largest swap growth (GiB) |
|---|---|---|---|---|---|
| tpcds | A | 1.31.0 | resource-policy-exceeded, resource-policy-exceeded, resource-policy-exceeded | 5.07 | 8.86 |
| tpcds | A | 1.35.2 | resource-policy-exceeded, resource-policy-exceeded, resource-policy-exceeded | 8.431 | 10.397 |
| tpcds | A | 1.40.1 | resource-policy-exceeded, resource-policy-exceeded, resource-policy-exceeded | 7.057 | 9.024 |
| tpcds | A | 1.44.2 | resource-policy-exceeded, resource-policy-exceeded, resource-policy-exceeded | 6.896 | 9.007 |
| tpcds | B | 2.0.0 | resource-policy-exceeded, resource-policy-exceeded, resource-policy-exceeded | 7.467 | 5.695 |
| tpcds | C | 1.31.0 | execution-failure, execution-failure, execution-failure | 5.506 | 0.89 |
| tpcds | C | 1.40.1 | resource-policy-exceeded, resource-policy-exceeded, resource-policy-exceeded | 7.292 | 2.316 |
| tpch | A | 1.31.0 | completed, completed, resource-policy-exceeded | 6.764 | 1.36 |
| tpch | A | 1.40.1 | resource-policy-exceeded, resource-policy-exceeded, completed | 6.041 | 1.772 |

## Candidate movers

42 comparisons were made; 20 are candidate movers (median change of at least 10% with non-overlapping min–max ranges). These are cell totals, not individual queries.

| Benchmark | Candidate | Baseline frame | Baseline | Median change |
|---|---|---|---|---|
| clickbench | 2.0.0 A | oldest | 1.31.0 A | -51.0% |
| clickbench | 2.0.0 A | reference | 1.44.2 A | -49.8% |
| clickbench | 2.0.0 C | oldest | 1.31.0 C | -22.4% |
| clickbench | 2.0.0 C | reference | 1.44.2 C | -13.8% |
| ssb | 1.31.0 A | reference | 1.44.2 A | +28.8% |
| ssb | 1.35.2 A | oldest | 1.31.0 A | -16.9% |
| ssb | 1.40.1 A | oldest | 1.31.0 A | -22.2% |
| ssb | 1.44.2 A | oldest | 1.31.0 A | -22.3% |
| ssb | 2.0.0 A | oldest | 1.31.0 A | -52.1% |
| ssb | 2.0.0 A | reference | 1.44.2 A | -38.3% |
| ssb | 2.0.0 B | reference | 1.44.2 A | +12.2% |
| ssb | 2.0.0 C | oldest | 1.31.0 C | -23.4% |
| ssb | 2.0.0 C | reference | 1.44.2 C | -20.1% |
| tpcds | 2.0.0 C | reference | 1.44.2 C | -49.0% |
| tpch | 2.0.0 A | reference | 1.44.2 A | -42.3% |
| tpch | 1.31.0 C | reference | 1.44.2 C | +40.7% |
| tpch | 1.40.1 C | oldest | 1.31.0 C | -30.9% |
| tpch | 1.44.2 C | oldest | 1.31.0 C | -28.9% |
| tpch | 2.0.0 C | oldest | 1.31.0 C | -45.9% |
| tpch | 2.0.0 C | reference | 1.44.2 C | -23.8% |

## Limits and what was not shown

- Correctness evidence. Value-level agreement was checked per version and setup at scale factor 0.1 (cross-surface gates). At the measured scale factor 10 only per-query row counts against the DuckDB SQL results were checked, so no claim of value-level correctness at scale factor 10 is made. TPC-DS query 41 returns 10 rows on Polars against 12 on DuckDB on every Polars version tested; it stays in the full-suite totals and is excluded from the common-query totals. The two totals differ by at most 0.02 s in every reported TPC-DS cell.
- Engine. The recorded engine is the requested collect policy. Streaming fallback and intermediate materialization were not observed.
- Not comparable with DuckDB. Polars timings include reading Parquet in each run; DuckDB matrix timings read a pre-loaded database. No speed comparison with DuckDB is made.
- Candidate movers are cell totals (sum over a benchmark's queries), flagged when the median changes by at least 10% and the min-max ranges do not overlap. They are candidates, not significant results. Three rounds per cell do not support a significance test, and 42 comparisons were made. No per-query movers or query plans are reported: the `polars-df` platform has no plan-capture path, and the analyzer works on cell totals only.
- Resource outcomes depend on this host (M4, 10 cores, 16 GiB). A cell is "not reported" when any round was stopped by the swap or memory limit, or crashed. The TPC-H cells for 1.31.0 (swap 1.36 GiB in the failing round) and 1.40.1 (1.77 and 1.42 GiB) failed only narrowly against the 1 GiB limit; the same versions completed other rounds. Swap growth is measured for the whole host, not per process.
- Load between runs. The 1-minute load average read before each run was 3.9 to 39.8 (median 12.8), because it still reflected the preceding run's ten threads. Memory pressure before every run was normal (level 1). The quiet-machine gate was applied once before the matrix started; the runner does not re-check for other processes between runs.
- Late-run slowdown. Seven runs finished more than 25% above their cell median, and all seven were among the last eight runs of round 3 (its final four minutes). Averaged over cells, round 3 ran 10.6% above each cell median, against 3.8% below for round 1 and 0.1% below for round 2. The cause is not established. Cell order is shuffled, so the slowdown fell on different cells, and a median of three absorbs one slow run, but it widens min-max ranges and makes the no-overlap rule stricter. Comparing each cell's fastest round instead of its median gives 2.0.0 against 1.44.2 on the default engine as TPC-H -45.9%, SSB -35.6% and ClickBench -52.6%, the same direction and similar size.
- 1.35.2 is in setups A and B but not C, because its streaming engine diverged on TPC-DS in a preliminary probe. No other 1.x version was substituted.
- Setup C on 1.31.0 crashed on TPC-DS (exit 138 or 139, 17 to 23 s into the run) in all three rounds. A preliminary probe had marked that cell clean. The cause was not investigated.
- Versions 1.0.0, 1.12.0 and 1.22.0 were excluded after the probe (a panic, a row-count mismatch on TPC-DS query 51, and failures on queries 51 and 87).
- All 120 runs used commit ecd08a9f0, one shuffle seed (20261009), and identical pins; no round was voided.
