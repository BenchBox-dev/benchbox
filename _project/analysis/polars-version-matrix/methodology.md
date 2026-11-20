# Polars version matrix: methodology

## Question

How do Polars 2.0.0 and 2.1.0 perform against earlier 1.x releases on the four BenchBox DataFrame benchmarks, separately for each collect engine?

## Measured configurations

| Item | Value |
|---|---|
| Platform | `polars-df` (DataFrame API, Parquet input) |
| Benchmarks | TPC-H, TPC-DS, SSB, ClickBench at scale factor 10 |
| Versions | 1.31.0, 1.35.2, 1.40.1, 1.44.2, 2.0.0, 2.1.0 |
| Setup A | each version's default `collect()` (`--execution-engine default`) |
| Setup B | in-memory engine (`--execution-engine in-memory`), 2.0.0 and 2.1.0 |
| Setup C | streaming engine (`--execution-engine streaming`), 1.31.0, 1.40.1, 1.44.2, 2.0.0, 2.1.0 |
| Threads | `POLARS_MAX_THREADS=10` |
| Rechunk | off (`rechunk=false`) |
| Rounds | 3, one invocation per cell per round, cell order shuffled each round (seed 20261119) |
| Per invocation | one warm-up pass and one measured pass per query (`--iterations 1`), tuning `notuning` |
| Code | BenchBox commit `82e76af81`, same for every run |
| Host | Apple M4, 10 cores, 16 GiB, macOS 27.0.1, Python 3.12.15, on mains power |
| Run | 2026-11-20 01:16 to 02:36 UTC, 156 runs |

The default engine differs by version: 1.x collects in memory; 2.x collects with the streaming engine. Setup B is therefore compared with 1.44.2 setup A (both in-memory).

1.35.2 is not in setup C. In the probe for the 2026-10-09 matrix, the TPC-DS cross-surface check at scale factor 0.01 (BenchBox's Polars DataFrame queries against DuckDB SQL) failed for 1.35.2 on the streaming engine, in the first run and in a repeat: queries 5, 14 and 27 returned NULL in an ORDER BY key column near the end of the result, and query 67 returned NULL in four columns of its first row, where the SQL result has values. That probe did not determine whether Polars or the BenchBox DataFrame query code causes this. The setup was not re-probed for this run. Versions 1.0.0, 1.12.0 and 1.22.0 were dropped after that probe: 1.0.0 has no `engine` argument and panicked, 1.12.0 returned a mismatching TPC-DS query 51, and 1.22.0 failed TPC-DS queries 51 and 87. Polars 1.35 and later load their native code from a separate runtime package; the package and its version are recorded in each result bundle.

A previous run of this matrix on 2026-10-09 (five versions, 120 runs, BenchBox commit `ecd08a9f0`) is superseded by this one. Its default-engine headline (2.0.0 against 1.44.2) agreed with this run to within 2.1 percentage points on every benchmark.

## Qualification

`qualification.json` in this directory is the qualification file the runner and analyzer require; the spec pins its SHA-256.

- Eligibility. Before the matrix, every version and engine in the spec was run through the cross-surface value gate (`benchbox.core.equivalence.cross_surface`) for each benchmark, at the commit the matrix ran on. The gate compares the Polars DataFrame results with DuckDB SQL results on the values, not only the row counts, at its own scale factor: 0.01 for TPC-H and TPC-DS, 0.1 for SSB and ClickBench. It checks returned order where the SQL has an ORDER BY, and compares rows as a set where it does not. All 52 rows are clean. ClickBench was gated with its default backends, because its classified query 18 exception (LIMIT 10 with no ORDER BY, so any ten groups are valid) is accepted only when the pandas cell also runs.
- Reference row counts. One DuckDB SQL power run per benchmark at scale factor 10, on data generated at the same commit, gives the row count of every query. They match the reference used for the 2026-10-09 matrix on all 207 queries.
- Known divergence. TPC-DS query 41 returns 10 rows on Polars against 12 on DuckDB at scale factor 10. It was rechecked on 1.44.2 and 2.1.0 for this run.

## Timing

A cell total is the sum of per-query wall times of the measured pass, in seconds, including reading the Parquet files in each query. The warm-up pass is not counted. Each cell is reported as the median and the min-max range of three rounds. DuckDB version-matrix timings read a pre-loaded database, so no speed comparison with DuckDB is made.

## Comparisons and candidate movers

The analyzer compares each cell with the reference version 1.44.2 in the same setup (and, for setup B, in setup A), and with the oldest version in the setup. A comparison is a candidate mover when the median change is at least 10% and the min-max ranges of the two cells do not overlap. Candidates are leads for follow-up, not significance findings: three rounds are too few for a test, and the number of comparisons is reported with the results. The analyzer compares common-query totals, which leave out TPC-DS query 41; for the other benchmarks they equal the full totals.

Comparisons between 2.1.0 and 2.0.0, and between 1.44.2 streaming and 1.44.2 default, are not analyzer frames. `findings.md` computes them from the cell medians and ranges with the same rule and marks them as computed.

## Correctness rule

A run is reported only when all of these hold: the installed Polars version, requested engine and `rechunk` setting recorded in the result match the cell; the run's own validation passed, or it was inconclusive and the qualification evidence for that version, setup and benchmark is clean; no measured query failed; the measured query set equals the reference set; and every query's row count equals the reference, apart from the known divergence above, whose 10-row count is still required. TPC-DS query 41 stays in full-suite totals and is left out of common-query totals. A cell is reported only when all three rounds satisfy the rule. At scale factor 10 only row counts were checked.

## Resource rules

Three rules mark a run `resource-policy-exceeded`. They are described here as the runner applied them.

- Host safety monitor. This is the only rule that stops a run. It trips when memory pressure is at the critical level on three consecutive checks, or when host swap-out growth passes 6 GiB. The runner checks on every fifth 1-second sample, so a run can pass the limit before it is stopped.
- Swap limit, 1 GiB. Applied after the run ends. Swap growth is the change in the `Swapouts` counter of `vm_stat`, which counts the whole host, so swap caused by any other process counts against the run.
- Memory limit, 14 GiB. Applied after the run ends, to the peak of resident set size summed over the process tree and sampled once a second. Resident set size on macOS leaves out pages that have been swapped out or compressed. The largest reading was 8.865 GiB, so this limit was never exceeded.

A run that exceeds 1800 s is stopped. A run that exits with an error is `execution-failure`. All of these cells are listed rather than imputed.

Two host guards outside the runner also applied. The matrix ran under the BenchBox heavy-work lock, whose watchdog was set to warn rather than stop for this run. The host's heavy-work sentinel runs separately in kill mode: when memory pressure is critical and BenchBox processes together pass its 8 GiB budget, it sends SIGTERM to the largest. It stopped eight TPC-DS runs this way; they are recorded as `execution-failure` with exit code 143. All eight were in cells that did not qualify on other rounds for memory reasons (see `findings.md`).

## Spill

Each run had its own `POLARS_TEMP_DIR` and its own process temporary directory (`TMPDIR`). Spill is the size of the first plus any `polars-*/spill` directory under the second, where Polars writes out-of-core spill files, sampled every second and once after the run. The 2026-10-09 matrix measured only `POLARS_TEMP_DIR`, so its reading of zero did not show whether any run spilled.

## Controls

- Data generation and the Parquet conversion ran once before the first measured run. Generator binary hashes and datagen manifest hashes are recorded in the run manifest and were identical across runs.
- Installed package pins were checked after every run.
- Free disk, peak resident memory, swap growth, spill and memory pressure were recorded per run.
- The host was not idle. Other work on the machine continued during the run, and the 1-minute load average read before each run was 10.8 to 43.1 (median 16.8). Memory pressure before every run was normal (level 1).

## Limits

See the limits section of `findings.md`. Raw result bundles, the run manifest and the analysis JSON are not committed.
