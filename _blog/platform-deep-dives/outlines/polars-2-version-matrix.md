# Outline: Polars 2.0 vs 1.x at SF10, Separating the Default From the Engine

**Post type:** BenchBox in Action (a methodology question answered with real
runs, neutral on the platform).
**Target length:** 2,000-2,500 words.
**Editorial angle:** Not "Polars 2.0 is 2x faster". Thesis: a "2.0 vs 1.x"
number mixes two changes, which engine a bare `collect()` uses and how fast
each engine is. Holding the engine fixed separates them. On our host, most of
the default-settings gain comes from 2.0 switching the default to streaming;
the streaming engine itself also got faster; the in-memory engine barely moved.
The second lesson is about measurement: on a 16 GiB machine, our memory rules
decided which cells finished, and some of those rules measured the host more
than the Polars version.

**Evidence sources:**
- `research/polars-2-version-matrix-verification-2026-10-17.md` (R1-R8,
  how the abort rules worked, errors in the deleted draft, open checks).
- `research/polars-2-version-matrix-figures-2026-10-09.md` (field mapping for
  every timing in `polars-version-matrix-analysis.json`).
- `_project/analysis/polars-version-matrix/findings.md` and `methodology.md`
  on `develop`.

Every timing comes from the analysis JSON. Every Polars behavior claim cites
R4 (official announcement) or is stated as what our runs showed.

**Voice reminders:** "we"; neutral on platforms; median with min-max range
beside every figure; "candidate mover", never "significant"; no em-dashes or
en-dashes (ASCII hyphen for ranges); define scale factor, collect engine and
candidate mover on first use; footnote external facts with access dates.

## Title and frontmatter

- Title (< 60 chars): "Polars 2.0 at SF10: The Default Changed, Not Just the Engine"
  (alternative: "Polars 2.0 vs 1.x: Separating Default From Engine").
- meta_description (150-160 chars): five Polars releases, four benchmarks,
  three engine settings; most of the default-settings gain is the new
  streaming default.
- tags: polars, dataframe, tpc-h, tpc-ds, ssb, clickbench, benchmarking,
  methodology.
- TL;DR (3 sentences, figures from R2):
  1. With each version's defaults, 2.0.0 finished TPC-H, SSB and ClickBench
     38% to 50% faster than 1.44.2 at SF10 on a 16 GiB Apple M4.
  2. Forced onto the in-memory engine, 2.0.0 was within 6% faster to 12%
     slower than 1.44.2; streaming against streaming, 2.0.0 was 14% to 49%
     faster on all four benchmarks.
  3. On TPC-DS, 2.0.0's default finished every round where every 1.x default
     was stopped by our host memory rules, which we describe in detail
     because they shaped the result.

## 1. Introduction (200-300 words)

- Hook: Polars 2.0.0 shipped on 2026-10-06 (R5). Its biggest change is that
  `collect()` on a LazyFrame now defaults to the streaming engine (R4, quote
  and footnote). Define "collect engine" in one sentence: the executor that
  runs a lazy query plan when you call `collect()`.
- The problem with one headline number: it mixes the default change with any
  change in how fast each engine runs.
- What we did: five versions, four benchmarks, three engine settings, three
  rounds each. Scope: Polars DataFrame API (`polars-df`) only, one host.
- Mention the Polars team's own expectation ("easily 5x faster" in aggregate,
  R4) only if attributed as their expectation; our results do not test it.

## 2. What we ran (250-350 words)

- Test environment table (style guide template): Apple M4, 10 cores, 16 GiB,
  mains power, `caffeinate -dims`; Polars 1.31.0, 1.35.2, 1.40.1, 1.44.2,
  2.0.0; TPC-H, TPC-DS, SSB, ClickBench at SF10; `POLARS_MAX_THREADS=10`;
  `rechunk=false`; tuning `notuning`; one warm-up and one measured pass per
  query; 3 rounds, cell order shuffled each round (seed 20261009); BenchBox
  commit `ecd08a9f0` for every run.
- The three setups, with the current flag (R7):
  - A: `--execution-engine default` (no engine argument; 1.x runs in memory,
    2.0.0 runs streaming per R4).
  - B: `--execution-engine in-memory`, 2.0.0 only, compared with 1.44.2 A.
  - C: `--execution-engine streaming`, on 1.31.0, 1.40.1, 1.44.2, 2.0.0.
  - One sentence: the 2026-10-09 runs recorded an earlier Polars-only option
    name with the same three values; BenchBox has since moved the setting to
    a core option that other platforms share.
- Define scale factor and cell total (sum of measured per-query wall times,
  Parquet reads included).
- Define candidate mover: median change of at least 10% with non-overlapping
  min-max ranges. 42 comparisons, 20 candidates; three rounds cannot support
  a significance test.
- Version selection, accurately:
  - 1.35.2 is not in setup C. At SF 0.01 its streaming TPC-DS results had
    NULLs where DuckDB's SQL results had values (queries 5, 14, 27, 67),
    twice. We did not determine whether Polars or BenchBox's DataFrame query
    code causes this. Do not write "the streaming engine diverged".
  - 1.0.0, 1.12.0 and 1.22.0 were dropped after the probe (panic; TPC-DS Q51
    mismatch; Q51 and Q87 failures).

## 3. Default settings: 2.0.0 against 1.44.2 (250-300 words)

- Table: TPC-H 6.89 (6.06-9.51) vs 11.95 (11.19-11.95), -42.3%; SSB 3.23
  (3.21-3.44) vs 5.24 (4.98-5.43), -38.3%; ClickBench 2.23 (2.09-2.82) vs
  4.45 (4.41-4.49), -49.8%; TPC-DS 2.0.0 31.01 (30.88-36.94), 1.44.2 not
  reported (section 6).
- Note the wide 2.0.0 TPC-H range and that the median sits near the low end.
- Cross-check worth one sentence: 2.0.0's setup A and setup C medians are
  within 12% of each other on all four benchmarks, which fits R4's statement
  that the default is now streaming.

## 4. Same engine: 2.0.0 in memory (250-300 words)

- Table: TPC-H 11.53 (10.42-11.55), -3.5%, no; SSB 5.88 (5.75-6.49), +12.2%,
  candidate (slower); ClickBench 4.19 (4.06-4.36), -5.9%, no; TPC-DS not
  reported for either.
- Point: with the engine held fixed, most of the gain disappears. Show the
  2.0.0 pairs side by side (TPC-H 6.89 vs 11.53; ClickBench 2.23 vs 4.19).
- Practical advice, framed as method: in a before-and-after upgrade test, set
  the engine explicitly (Polars: `collect(engine=...)` or
  `pl.Config.set_engine_affinity`, R4; BenchBox: `--execution-engine`).

## 5. Streaming against streaming, and the 1.x trend (300-350 words)

- Table: TPC-H 6.16 (6.10-6.39) vs 8.08 (7.59-8.21), -23.8%; TPC-DS 31.28
  (30.37-42.83) vs 61.30 (59.75-68.11), -49.0%; SSB 3.37 (3.21-3.40) vs 4.22
  (4.08-4.29), -20.1%; ClickBench 2.20 (2.09-2.21) vs 2.55 (2.40-2.83),
  -13.8%. All four candidates.
- Flag TPC-DS: largest change, wide 2.0.0 range, so a lead for a larger run.
- 1.x trend table (setup A SSB and ClickBench; setup C TPC-H, SSB,
  ClickBench). Streaming's largest 1.x step: TPC-H 1.31.0 to 1.40.1 (11.37 to
  7.86, -30.9% against oldest). 1.40.1 and 1.44.2 within 3.3% on every setup C
  benchmark.
- Omit TPC-H setup A from the trend table, with the reason (section 6).

## 6. What decided TPC-DS: our memory rules on a 16 GiB host (400-500 words)

This section replaces the old draft's memory section. Describe the rules as
they worked, not as they were specified (verification note, "How the abort
rules actually worked").

- 6a. The outcome table (10 TPC-DS cells): four 1.x defaults stopped by the
  host-safety monitor in every round (swap growth 8.86-10.40 GiB); 2.0.0 B and
  1.40.1 C over the swap limit; 1.31.0 C crashed (exit 138 or 139, 17-23 s,
  not investigated, probe had marked it clean); 2.0.0 A, 2.0.0 C, 1.44.2 C
  completed.
- 6b. How each rule actually behaved:
  - Process memory limit (14 GiB): never tripped. macOS RSS leaves out
    swapped and compressed pages; the largest reading was 8.4 GiB in a run
    where the host swapped out 10.4 GiB.
  - Swap limit (1 GiB): read from the host-wide `vm_stat` swap-out counter,
    so it measures the whole machine. This was the rule that mattered.
  - Host-safety monitor (6 GiB swap or sustained critical pressure): checked
    about every 5 seconds, so runs reached 10.4 GiB before being stopped.
- 6c. Consequence beyond TPC-DS: TPC-H default on 1.31.0 (0.60, 0.56, then
  1.36 GiB) and 1.40.1 (1.77, 1.42, then 0.67 GiB) were ruled out by that
  host-wide counter, so there is no default-engine TPC-H comparison against
  the oldest version.
- 6d. What we can and cannot say:
  - Can: 2.0.0's default engine finished TPC-DS at SF10 on this host in all
    three rounds; every 1.x default did not; 2.0.0 forced in memory did not.
  - Cannot: that 2.0.0 finished without spilling. We set `POLARS_TEMP_DIR`
    per cell and saw no files there, but we have not confirmed that 2.0.0
    spills to that directory. Say "we saw no files in the configured temp
    directory".
  - These outcomes describe this host, not a version's memory ceiling.
- 6e. What we would change (BenchBox method lesson, brief): measure memory
  including swap per process tree, check safety limits every sample, and
  attribute swap to the benchmark process. Only promise this if tracker items
  exist; otherwise state it as what the numbers need, not as a roadmap.

## 7. What these numbers do not show (250-300 words)

- Correctness: value-level checks at SF 0.1 per version and setup; at SF10,
  row counts only against DuckDB SQL. TPC-DS Q41 returns 10 rows on Polars
  and 12 on DuckDB on every version (in full-suite totals; common-query totals
  differ by 0.02 s or less).
- Engine fallback: we recorded the requested engine, not whether operators
  fell back to in-memory execution.
- No comparison with DuckDB: Polars timings include Parquet reads; the DuckDB
  matrix reads a pre-loaded database.
- Host load: load average before runs 3.9-39.8 (median 12.8) from the
  previous run's threads; memory pressure normal before every run; quiet-host
  gate applied once at the start.
- Late-run slowdown: seven runs more than 25% above their cell median, all
  among the last eight of round 3; round 3 averaged 10.6% above cell medians;
  cause unknown. Fastest-round comparison gives TPC-H -45.9%, SSB -35.6%,
  ClickBench -52.6%, same direction.
- Cell totals only: no per-query movers, and `polars-df` has no plan capture.

## 8. Reproduce it (150-200 words)

- Runner dry run with the current flags (R8), run from the checkout with
  `uv run --no-sync` so the installed Polars is not reverted between cells:

  ```bash
  $ uv run --no-sync -- python scripts/run_polars_version_matrix.py \
      --output-dir ~/benchmark_runs/polars-matrix \
      --shuffle-seed 20261009 \
      --qualification qualification.json \
      --dry-run
  ```

- One cell with the current option (R7):

  ```bash
  $ POLARS_MAX_THREADS=10 benchbox run --non-interactive --quiet --iterations 1 \
      --platform polars-df --benchmark tpch --scale 10 --tuning notuning \
      --platform-option rechunk=false --execution-engine streaming
  ```

  Verify both commands against `develop` before publishing.
- Analysis, CSV and methodology location; raw bundles stay outside the repo.
- Note: on a 16 GiB machine, expect TPC-DS cells to hit the memory rules.

## 9. Conclusions (150-200 words)

- 2.0.0 was faster than 1.44.2 in every comparison where both finished, on
  the default and on the streaming engine.
- The size depends on what you hold fixed: against 1.x defaults, most of the
  gain is the new default; between in-memory engines, the versions are close;
  between streaming engines, 2.0.0 leads by 14% to 49%.
- Method takeaway: name the engine in any upgrade comparison, and check that
  your memory limits measure the process you think they measure.
- Invite runs on other hardware and reports of the 1.31.0 streaming TPC-DS
  crash.

## References

- R4 Polars 2.0 announcement (2026-09-02, accessed 2026-10-17).
- R5 PyPI release record for 2.0.0 (accessed 2026-10-17).
- BenchBox findings, methodology and CSV on `develop`.
- Research notes (both files above).

## Charts (decide at draft time)

- Candidate: grouped bars per benchmark for 2.0.0 A, 2.0.0 B, 1.44.2 A, with
  min-max whiskers. Values from the analysis JSON only; cite the field.

## Before drafting

1. Fix `methodology.md` on `develop`: describe the 14 GiB, 1 GiB and 6 GiB
   rules as measured (section 6b) and resolve the "committed bundles" vs "not
   committed" contradiction. The post should not cite a methodology page that
   disagrees with it.
2. Confirm the SF 0.1 cross-surface gates compare values without regard to
   row order (streaming does not guarantee order, R4). If they are
   order-sensitive, say so in section 7.
3. Confirm readers can reach `_project/analysis/polars-version-matrix/` in the
   public repository; otherwise cite a reachable path.
4. Optional, strengthens section 6: TPC-DS at SF 3, or on a host with more
   RAM, to get a like-for-like default-engine TPC-DS comparison. Not required
   for this outline.
