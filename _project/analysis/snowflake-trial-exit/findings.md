# Snowflake trial: tuned-template measurements and corrected evidence

Runs on a Snowflake trial account (X-Small warehouse, result cache disabled)
between 2026-09-17 and 2026-10-13. Estimated total spend:
56.166 (55.527 metered through 2026-10-12 19:00 PT,
plus 0.139 warehouse use on 2026-10-13 and a 0.5 safety margin; no
Snowsight balance check was needed under the 85-credit cap).

## Tuned versus untuned at scale

The campaign was designed as follows. Each measured cell loads an untuned
and a tuned database from the same generated data. Tuned tables use the
shipped Snowflake templates
(`examples/tunings/snowflake/{tpch,tpcds}_tuned.yaml`: CLUSTER BY on fact
tables, primary and foreign keys without RELY). After loading, automatic
clustering is allowed to settle: every clustered table's average depth
stable within 5% across three polls 10 minutes apart, up to 3 hours. Six
power runs (one warmup and three measured passes each) alternate untuned,
tuned, tuned, untuned, untuned, tuned, with the warehouse suspended before
each. `campaign_analysis.py` compares per-query medians of run means; the
verdict rule was fixed before the runs: benefit if the 95% upper bound of
tuned/untuned is below 0.95, regression if the lower bound is above 1.05,
otherwise no detectable effect.

No measurement cell ran. The smoke test (TPC-H sf0.01 tuned load, tried
twice on 2026-10-13) aborted while writing the tuned-run marker, so the
reuse check, settle step and interleaved runs never started, and cells
C1–C4 were skipped without spending their budgets. Under verbose logging
(`-v`, which the campaign command template used), every Snowflake
`execute_query` call raises `ValueError` after the query itself succeeds,
because it passes the query id where the completion log expects a duration
in seconds, and formatting the id with `:.2f` raises. The exception escapes
`execute_query`, so the metadata-table creation fails, the tuned-run
marker is never written, and the run aborts. Quiet runs are unaffected.
The defect is fixed by
[benchbox#2990](https://github.com/BenchBox-dev/benchbox/pull/2990),
which passes the measured duration at the three call sites (plus the same
one-line defect in the Azure Synapse adapter) with regression tests that run
`execute_query` at verbosity level 1; omitting `-v` also avoids it.

| Benchmark | Scale | Settled | Average depth (tuned tables) | Clustering credits | Tuned/untuned | 95% CI | Verdict |
|---|---|---|---|---|---|---|---|
| TPC-H | 100 | skipped | — | 0.000 | — | — | not run: tuned arm aborts before any measurement (see above) |
| TPC-DS | 100 | skipped | — | 0.000 | — | — | not run: tuned arm aborts before any measurement (see above) |
| TPC-H | 10 | skipped | — | 0.000 | — | — | not run: tuned arm aborts before any measurement (see above) |
| TPC-DS | 10 | skipped | — | 0.000 | — | — | not run: tuned arm aborts before any measurement (see above) |

No per-query movement, same-arm noise comparison, or check problems exist
to report, because no tuned or untuned measurement run completed in this
campaign. The smoke-test evidence (command, log path, statement history,
clustering state) is recorded in the program closeout rather than here.

Primary and foreign keys were declared without RELY, so on Snowflake the
tuned arm's effective mechanism is clustering. Template quality issues found
in review (keys after the date add little pruning; INVENTORY unclustered)
were not changed before measuring.

## Earlier pilot at scale factor 1

An earlier tuned pilot at sf1 (`tuning-pilot-recompute.json`) reported 1.078
(TPC-H) and 0.924 (SSB); both figures included the warmup iteration. On
measured iterations, TPC-H tuned over untuned is 1.086 by mean (95% CI
0.989–1.187) and SSB 0.937 (0.771–1.134) but 1.072 by best-of-run. Runs
were not interleaved and drifted over time, clustering had not settled, and
sf1 tables span only a handful of micro-partitions. The pilot shows nothing
either way. `ssb-tuning-pilot-draft.yaml` is the exact SSB file it used
(configuration hash `f3aa4b688c68b40a…`); it is not a shipped template.

## Comparability of the 2026-10-03 TPC-H baselines

`comparability-matrix-corrected-2026-10-13.json` marks three TPC-H baselines
(sf0.1, sf1, sf10) as shape-changed; the other 47 cells stay
reproducible-exact. Those baselines ran each power iteration on a separate
stream, with a different query order and, when seeded, different qgen
parameters; since commit ff1dec15e every power iteration runs stream 0. Data
generated before commit e23ba66c9 also carries non-canonical customer and
supplier addresses.

## Database reuse

`database-reuse-map-corrected-2026-10-13.json` shows that the read, write and
transaction primitives baselines ran inside the TPC-H databases of the same
scale, and the TPC-DS OBT baseline inside the TPC-DS sf1 database. Before
https://github.com/BenchBox-dev/benchbox/pull/2947, the Snowflake adapter's existing-database check never
authenticated, so every run truncated and reloaded its tables instead of
reusing them. A live check on 2026-10-13 showed no shared database dropped
(the only drops in window were the SF1 tuned databases and the two SF100
untuned databases) and table counts of 32 in each shared TPC-H database and
26 in the shared TPC-DS database.

## Large-scale limits

sf1000 was not run: the generator writes uncompressed `.tbl` chunks, so
TPC-H sf1000 needs about 1 TB of local disk at peak, more than the 926 GiB
host provides. SSB, TPC-DI and the primitives were not run at sf100 because
the benchmark registry rejects those scales.

## Published bundles

Publishing reloads and re-exports unanonymized bundles, which dropped every
recorded phase; the 2026-10-12 published bundles show all phases as NOT_RUN
(https://github.com/BenchBox-dev/benchbox/pull/2949). The local result files keep the real timings, and the
published bundles were left unchanged. Untuned bundles also lost
`tuning_source`, fixed in the same change.

## Code fixes

- Tuning metadata inserts choose the placeholder from the driver paramstyle:
  https://github.com/BenchBox-dev/benchbox/pull/2943.
- Snowflake existing-database check, so compatible databases are reused:
  https://github.com/BenchBox-dev/benchbox/pull/2947. The drop statement still names nothing and is left that way
  until benchmarks that share a database are protected from losing it.
- Phase and tuning-source survival through reload: https://github.com/BenchBox-dev/benchbox/pull/2949.

## Files

| File | Content |
|---|---|
| `tuned-vs-untuned-<benchmark>-sf<scale>.json` | Not produced: no measurement cell ran, so there is no per-cell output |
| `snowflake_campaign.py` | Settle, clustering-state, budget and history helper prepared for the runs; it ran no measurement cell |
| `campaign_analysis.py` | Produces the per-cell analysis files from local result bundles |
| `comparability-matrix-2026-10-12.json` / `-corrected-2026-10-13.json` | Original and corrected comparability matrix |
| `database-reuse-map-2026-10-12.json` / `-corrected-2026-10-13.json` | Original and corrected reuse map |
| `tuning-pilot-recompute.json` | Re-derived sf1 pilot statistics |
| `ssb-tuning-pilot-draft.yaml` | Tuning file used by the sf1 SSB tuned run |
| `rederive_evidence.py` | Produces the pilot, matrix and reuse-map files |
