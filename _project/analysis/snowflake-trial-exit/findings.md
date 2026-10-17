# Snowflake trial: tuned-template measurements and corrected evidence

Runs on a Snowflake trial account (X-Small warehouse, result cache disabled)
between 2026-09-17 and 2026-10-17. Estimated total spend: 70.927 credits
(55.527 metered through 2026-10-12 19:00 PT, plus 14.900 warehouse credits
from 2026-10-13 02:00 UTC to the end of the last run on 2026-10-17, plus a 0.5
safety margin; automatic clustering used 0.000 credits). That is below the
85-credit cap set for the program. The account's free-usage contract item runs
from 2026-09-17 to 2026-10-18.

## Tuned versus untuned at scale

Each measured cell loads an untuned and a tuned database from the same
generated data. Tuned tables use the shipped Snowflake templates
(`examples/tunings/snowflake/{tpch,tpcds}_tuned.yaml`: CLUSTER BY on fact
tables, primary and foreign keys without RELY). After loading, automatic
clustering is allowed to settle: every clustered table's average depth stable
within 5% across three polls 10 minutes apart, up to 3 hours. Six power runs
(one warmup and three measured passes each) alternate untuned, tuned, tuned,
untuned, untuned, tuned, with the warehouse suspended before each.
`campaign_analysis.py` compares per-query medians of run means; the verdict
rule was fixed before the runs: benefit if the 95% upper bound of
tuned/untuned is below 0.95, regression if the lower bound is above 1.05,
otherwise no detectable effect.

| Benchmark | Scale | Settled | Average depth (tuned tables) | Clustering credits | Tuned/untuned | 95% CI | Verdict |
|---|---|---|---|---|---|---|---|
| TPC-H | 100 | yes, 3 polls (1,206 s) | LINEITEM 1.01 (419 partitions), ORDERS 1.00 (127) | 0.000 | 0.914 | 0.834–0.996 | no detectable effect |
| TPC-H | 10 | yes, 3 polls (1,219 s) | LINEITEM 1.00 (45), ORDERS 1.00 (16) | 0.000 | 0.979 | 0.919–1.043 | no detectable effect |
| TPC-DS | 100 | yes, 3 polls (1,220 s) | STORE_SALES 1.48 (401), CATALOG_SALES 1.05 (299), WEB_SALES 2.00 (146), returns tables 1.00–1.13 | 0.000 | 0.761 | 0.730–0.793 | benefit |
| TPC-DS | 10 | yes, 3 polls (1,211 s) | STORE_SALES 1.48 (44), CATALOG_SALES 1.00 (36), WEB_SALES 2.00 (15), returns tables 1.00 | 0.000 | 0.925 | 0.898–0.958 | no detectable effect |

Only TPC-DS sf100 shows a benefit: tuned runs took 0.761 of the untuned time
across the geometric mean of 103 statements, 94 of them faster, and the effect
exceeds the spread between runs of the same arm (same-arm pair ratios
0.90–1.02). TPC-DS sf10 leans the same way (80 of 103 faster) but its upper
bound, 0.958, misses the 0.95 threshold, and the effect does not exceed
same-arm noise (0.90–1.06). Neither TPC-H cell shows an effect beyond noise
(14 of 22 and 11 of 22 queries faster). Clustering credits were zero in every
cell: the tables were loaded in a well-clustered state and stayed there, so
these results measure the shipped clustering keys at load time, not long-term
reclustering. Per-query results are in `tuned-vs-untuned-<benchmark>-sf<scale>.json`.

Every measured run was valid: no run reloaded its database (data loading
0 ms), and every query execution succeeded (88 per TPC-H run, 412 per TPC-DS
run). Measurement runs were published with the `maintainer-run` label. One
TPC-H sf10 run was interrupted by an operator error before it recorded a
result; the six-run sequence was restarted from its first run.

### Code and data for each cell

| Cell | BenchBox commit | Tuned template requests | Data |
|---|---|---|---|
| TPC-H sf100, sf10 | `59a74fb64` | primary keys, foreign keys, UNIQUE, CHECK, clustering | dbgen 0.4.2, generated 2026-10-16 (canonical customer and supplier addresses) |
| TPC-DS sf100, sf10 | `876f3dfbe` | primary keys, foreign keys, clustering | bundled TPC-DS v4.0.0 dsdgen, generated 2026-10-16 |

Between the two pairs of cells,
[benchbox#3039](https://github.com/BenchBox-dev/benchbox/pull/3039)
(`f479cc8e6`) set `unique_constraints` and `check_constraints` to disabled in
both shipped Snowflake templates. UNIQUE and CHECK constraints are
informational on Snowflake, so no performance effect is expected from the
difference; the tuned mechanism in every cell is clustering. Both arms of each cell used the same commit and the
same data. Loads ran `generate,load,power --iterations 1` (the power result is
not scored) because a load-only `--phases generate,load` run exits with an
error on the current code; that defect is tracked separately.

### How the campaign reached these runs

The first smoke test (TPC-H sf0.01 tuned load, tried twice on 2026-10-13)
aborted while writing the tuned-run marker. Under verbose logging (`-v`),
every Snowflake `execute_query` call raised `ValueError` after the query
itself succeeded, because it passed the query id where the completion log
expects a duration in seconds. Fixed by
[benchbox#2990](https://github.com/BenchBox-dev/benchbox/pull/2990).

A repeated smoke test on 2026-10-15 loaded correctly, but the reuse run
dropped and reloaded the tuned database: tuning validation reported the
template's constraint-only tables (CUSTOMER, PART, PARTSUPP, SUPPLIER) as
missing, because the metadata store keeps no rows for tables without column
tuning. A tuned database was therefore never reused, and every tuned run would
have discarded settled clustering. Fixed by
[benchbox#3012](https://github.com/BenchBox-dev/benchbox/pull/3012); the
campaign's untuned loads also needed
[benchbox#3013](https://github.com/BenchBox-dev/benchbox/pull/3013), which
accepts `--platform-option force_recreate=true`. After both merged, a smoke
test on 2026-10-16 reused the tuned database with no DROP, TRUNCATE or COPY
statements.

The first TPC-DS sf100 attempt on 2026-10-16 was abandoned after two
measurement runs: the post-load row-count check scaled TPC-DS `customer`
linearly (8–12 million rows expected at SF100, 2,000,000 generated), so every
TPC-DS run at SF10 or SF100 failed validation and reloaded its database.
[benchbox#3031](https://github.com/BenchBox-dev/benchbox/pull/3031) checks
every TPC-DS table against the row counts dsdgen generates at SF1, SF10 and
SF100, and both TPC-DS cells were rerun from the start after it merged.

### TPC-DS reason table

The bundled generator is the official TPC-DS v4.0.0 dsdgen. Its scaling table
defines 75 `reason` rows at every scale, while the specification's Table 3-2
lists 35 at SF1 (35/45/55 is the row in older kits). Clause 3.4.2 states that
where the table and dsdgen differ, dsdgen prevails, so the generated data is
correct for v4.0.0. The resulting differences from the official Q85 and Q93
answer sets are classified in
`benchbox/core/tpcds/qualification/classification.py`. Q85 and Q93 are the
statements that join returns to `reason`; Q9 reads only reason row 1, which
exists either way.

### Clean-up

After the last run on 2026-10-17, all 48 BenchBox-created databases in the
trial account (the `*_NOTUNING_NOCONSTRAINTS` and `*_CUSTOM_*` databases) were
dropped and the warehouse was suspended. Snowflake's own databases and the
user's personal database were left in place. Results remain in the local
result bundles, the published bundles and the files listed below.

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
- Verbose completion logging in Snowflake `execute_query`:
  https://github.com/BenchBox-dev/benchbox/pull/2990.
- Tuned-database reuse for templates with constraint-only tables:
  https://github.com/BenchBox-dev/benchbox/pull/3012.
- `force_recreate` accepted as a platform option:
  https://github.com/BenchBox-dev/benchbox/pull/3013.
- TPC-DS post-load row-count checks follow what dsdgen generates at SF1, SF10
  and SF100: https://github.com/BenchBox-dev/benchbox/pull/3031.

## Files

| File | Content |
|---|---|
| `tuned-vs-untuned-<benchmark>-sf<scale>.json` | Per-cell verdict, confidence interval, same-arm noise and per-query ratios for TPC-H sf100 and sf10 and TPC-DS sf100 and sf10 |
| `snowflake_campaign.py` | Settle, clustering-state, budget and statement-history helper used for the runs |
| `campaign_analysis.py` | Produces the per-cell analysis files from local result bundles |
| `comparability-matrix-2026-10-12.json` / `-corrected-2026-10-13.json` | Original and corrected comparability matrix |
| `database-reuse-map-2026-10-12.json` / `-corrected-2026-10-13.json` | Original and corrected reuse map |
| `tuning-pilot-recompute.json` | Re-derived sf1 pilot statistics |
| `ssb-tuning-pilot-draft.yaml` | Tuning file used by the sf1 SSB tuned run |
| `rederive_evidence.py` | Produces the pilot, matrix and reuse-map files |
