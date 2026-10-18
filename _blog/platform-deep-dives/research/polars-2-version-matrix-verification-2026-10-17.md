# Polars 2.0 version matrix: verification notes (2026-10-17)

Companion to `outlines/polars-2-version-matrix.md`. Figure-to-field mapping for
every timing stays in `polars-2-version-matrix-figures-2026-10-09.md`. This
note records what was re-verified on
2026-10-17 and what the earlier draft got wrong.

## Sources

| ID | Source | What it supports |
|---|---|---|
| R1 | Run manifest `~/Developer/benchmark_runs/polars-version-matrix-20261009/matrix-manifest.json` (`complete: true`, 120 records) | Outcomes: 96 completed, 21 `resource-policy-exceeded`, 3 `execution-failure`. Peak RSS max 8.431 GiB (completed runs max 6.777 GiB); no run above 14 GiB. Swap growth max 10.397 GiB. Spill max 0.0 GiB. |
| R2 | `polars-version-matrix-analysis.json` and `_project/analysis/polars-version-matrix/findings.md` (on `develop`) | Every cell median, range and comparison. Unchanged since #2804 apart from wording fixes. |
| R3 | Runner at the run commit: `git show ecd08a9f0:scripts/run_polars_version_matrix.py` | How the abort rules were measured (below). |
| R4 | Polars 2.0 RC announcement, https://pola.rs/posts/announcing-polars-2/ (published 2026-09-02, accessed 2026-10-17) | "Calling `collect` on a `LazyFrame` will now default to the streaming engine." Opt out with `collect(engine="in-memory")` or `pl.Config.set_engine_affinity("in-memory")`. Streaming does not guarantee row order for joins, group-bys and unpivot by default. "Proper out-of-core support" is listed as upcoming work. The 5x figure is the team's expectation, not a measurement. |
| R5 | PyPI JSON, https://pypi.org/pypi/polars/2.0.0/json (accessed 2026-10-17) | 2.0.0 first upload 2026-10-06T11:44Z. |
| R6 | Runtime Wire, https://runtimewire.com/article/polars-2-ritchie-vink-streaming-out-of-core (secondary, accessed 2026-10-17) | Initial spilling covers sorting, window functions and many expressions; joins and group-bys stay in memory. Secondary source: cite only with attribution, or not at all. |
| R7 | `docs/platforms/polars.md:60-74` on `develop` | Current option is the core `--execution-engine {default,in-memory,streaming}`. The `streaming` platform option is a deprecated alias. |
| R8 | `scripts/run_polars_version_matrix.py --help` on `develop` | Current runner flags: `--output-dir`, `--dry-run`, `--shuffle-seed`, `--first-round`, `--rounds`, `--qualification`; run with `uv run --no-sync`. |

## How the abort rules actually worked (R3)

- **14 GiB memory limit.** Sums `psutil` `memory_info().rss` over the process
  tree (lines ~268-276). On macOS, RSS excludes swapped and compressed pages,
  so this limit never tripped: the largest reading was 8.431 GiB, in a run
  where the host swapped out 10.4 GiB (R1). Do not describe it as a working
  limit.
- **1 GiB swap limit.** `swap_out_bytes()` reads the `Swapouts` counter from
  `vm_stat` (lines ~203-209). That counter covers the whole host, so another
  process's swap counts against the running cell. Applied after the run
  (line 95). This was the gate that decided qualification.
- **Host-safety monitor (6 GiB swap or 3 critical-pressure samples).** Checked
  only on every fifth 1-second sample (line ~280), so it fired late: swap growth
  reached 10.4 GiB before the kill. State the limit as "checked about every
  5 seconds", and give the observed overshoot.
- **Spill.** `POLARS_TEMP_DIR` is set to a per-cell directory (line 413), and
  its size is sampled every ~5 seconds and once after exit (lines 281, 298).
  Every reading was 0. Neither R4 nor R5 says whether Polars 2.0.0 writes spill
  files to `POLARS_TEMP_DIR`, and R6 says joins and group-bys do not spill.
  Supportable claim: "we saw no files in the configured temp directory". Not
  supportable: "2.0.0 completed TPC-DS without spilling".

## Consequences of the swap gate (R1)

| Cell | Round 1 | Round 2 | Round 3 |
|---|---|---|---|
| TPC-H A 1.31.0 | completed, 0.598 GiB | completed, 0.559 GiB | exceeded, 1.36 GiB |
| TPC-H A 1.40.1 | exceeded, 1.772 GiB | exceeded, 1.422 GiB | completed, 0.671 GiB |

These two cells are "not reported", which removes the TPC-H default-engine
comparison against the oldest version. A host-wide counter decided both.

## Errors in the deleted 2026-10-09 draft

1. Said "we added an `engine` option to `polars-df`" and used
   `--platform-option engine=streaming`. The current interface is the core
   `--execution-engine` option (R7). The 2026-10-09 bundles recorded the old
   option name with the same values (methodology.md).
2. Said 1.35.2's "streaming engine diverged" on TPC-DS. The probe did not
   attribute the NULL results to Polars or to BenchBox's DataFrame query code
   (findings.md, methodology.md as corrected on `develop`).
3. Described the 14 GiB, 1 GiB and 6 GiB rules as working, exact limits.
   See above.
4. Said "the reason is the engine, not spilling to disk". The spill
   measurement cannot support that.
5. Stated the 2.0 default engine only as something "our runs showed". R4 now
   documents it, and the run data agrees: 2.0.0 setup A and setup C medians
   are within 12% of each other on all four benchmarks (R2).
6. Reproduce command used the old runner invocation without `--no-sync`.

## Still open

- Whether the SF 0.1 cross-surface value gates compare results without
  regard to row order. Matters because streaming does not guarantee order
  (R4). Row-count checks at SF 10 are unaffected.
- `methodology.md` on `develop` still states the 14 GiB rule as written and
  says both "the committed 2026-10-09 bundles" and "Raw result bundles ... are
  not committed". Fix it before the post cites it.
- Whether `_project/analysis/polars-version-matrix/` is reachable by readers
  of the public repository, since the post cites it.
