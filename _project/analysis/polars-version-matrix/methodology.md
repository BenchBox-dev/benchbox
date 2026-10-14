# Polars version matrix: methodology

## Question

How does Polars 2.0.0 perform against earlier 1.x releases on the four BenchBox DataFrame benchmarks, separately for each collect engine?

## Measured configurations

| Item | Value |
|---|---|
| Platform | `polars-df` (DataFrame API, Parquet input) |
| Benchmarks | TPC-H, TPC-DS, SSB, ClickBench at scale factor 10 |
| Versions | 1.31.0, 1.35.2, 1.40.1, 1.44.2, 2.0.0 |
| Setup A | each version's default `collect()` (`--execution-engine default`) |
| Setup B | in-memory engine (`--execution-engine in-memory`), 2.0.0 only |
| Setup C | streaming engine (`--execution-engine streaming`), 1.31.0, 1.40.1, 1.44.2, 2.0.0 |
| Threads | `POLARS_MAX_THREADS=10` |
| Rechunk | off (`rechunk=false`) |
| Rounds | 3, one invocation per cell per round, cell order shuffled each round (seed 20261009) |
| Per invocation | one warm-up pass and one measured pass per query (`--iterations 1`), tuning `notuning` |
| Code | BenchBox commit `ecd08a9f0`, same for every run |
| Host | Apple M4, 10 cores, 16 GiB, on mains power, `caffeinate -dims` |

The default engine differs by version: 1.x collects in memory; 2.0.0 collects with the streaming engine. Setup B on 2.0.0 is therefore compared with 1.44.2 setup A (both in-memory). 1.35.2 is not in setup C. In the preliminary probe, the TPC-DS cross-surface check at scale factor 0.01 (BenchBox's Polars DataFrame queries against DuckDB SQL) failed for 1.35.2 on the streaming engine, in the first run and in a repeat: queries 5, 14 and 27 returned NULL in an ORDER BY key column near the end of the result, and query 67 returned NULL in four columns of its first row, where the SQL result has values. The probe did not determine whether Polars or the BenchBox DataFrame query code causes this. The same version passed the check with the default and in-memory engines. Versions 1.0.0, 1.12.0 and 1.22.0 were dropped after the probe: 1.0.0 has no `engine` argument and panicked, 1.12.0 returned a mismatching TPC-DS query 51, and 1.22.0 failed TPC-DS queries 51 and 87. Polars 1.35 and later load their native code from a separate runtime package; the package and its version are recorded in each result bundle.

The committed 2026-10-09 bundles were recorded under the earlier Polars `engine` option name and map to the same requested values (`default`, `in-memory`, and `streaming`).

## Timing

A cell total is the sum of per-query wall times of the measured pass, in seconds, including reading the Parquet files in each query. The warm-up pass is not counted. Each cell is reported as the median and the min-max range of three rounds. DuckDB version-matrix timings read a pre-loaded database, so no speed comparison with DuckDB is made.

## Comparisons and candidate movers

Baselines: the reference version 1.44.2 in the same setup (and, for setup B, in setup A), and the oldest version in the setup. A comparison is a candidate mover when the median change is at least 10% and the min-max ranges of the two cells do not overlap. Candidates are leads for follow-up, not significance findings; three rounds are too few for a test, and the number of comparisons is reported with the results.

## Correctness rule

A run is reported only when all of these hold: the installed Polars version, requested engine and `rechunk` setting recorded in the result match the cell; the run's own validation passed, or it was inconclusive and the preliminary qualification evidence for that version, setup and benchmark is clean; no measured query failed; the measured query set equals the reference set; and every query's row count equals the DuckDB SQL reference recorded for the scale factor (TPC-DS query 41 is the one known exception, with 10 rows on Polars against 12 on DuckDB on every version tested; it stays in full-suite totals and is excluded from the common-query totals, with the 10-row count still required). A cell is reported only when all three rounds satisfy the rule. Value-level agreement was checked at scale factor 0.1 per version and setup; at scale factor 10 only row counts were checked.

## Abort rule

A run is stopped, and the cell marked `resource-policy-exceeded`, when resident memory exceeds 14 GiB, host swap-out growth exceeds 1 GiB, or the host safety monitor trips (memory pressure at the critical level for three samples, or swap growth beyond 6 GiB). A run that exceeds 1800 s is stopped. Runs that exit with an error are `execution-failure`. All of these cells are listed rather than imputed.

## Controls

- The host was quiet before the matrix started: 1-minute load average below 2.0 on three samples one minute apart, no other process above 50% CPU, and normal memory pressure.
- Data generation and the Parquet conversion ran once before the first measured run. Generator binary hashes and datagen manifest hashes are recorded in the run manifest and were identical across runs.
- Installed package pins were checked after every run.
- Free disk, peak resident memory, swap growth, peak spill and memory pressure were recorded per run. No run spilled to disk.

## Limits

See the limits section of `findings.md`. Raw result bundles and the run manifest are not committed.
