# ClickHouse tuned templates at SF1: plans for TPC-H Q3, Q5 and Q18, and SSB and TPC-DS verdicts

Measured 2026-11-12 with `scripts.tuning_evidence`, seed 42, on a ClickHouse server (25.8.33.6) in a
Mocker 0.7.2 container (guest `memory.max` 5637144576, zero OOM kills in every cell) on a 16 GiB macOS
27.0.1 host. Scope of every verdict below: SF1, 5.25 GiB container, ClickHouse 25.8.33.6.

Arms: `N` notuning, `N2` notuning (in-run A/A calibration), `S` tuned layout with the notuning session,
`T` the full tuned template (layout and session). Load time is counted: the verdict rule compares load
plus merge settle.

## TPC-H SF1, all queries (9 rounds)

Cell summary: `cells/tpch-sf1-9rounds-summary.md`.

| Comparison | G over 21 queries | 95% interval | Verdict | Load plus settle |
|---|---|---|---|---|
| A/A (`N2` vs `N`) | 1.0066 | 0.9592 to 1.0464 | passed | 10.7 s vs 10.5 s |
| `S` vs `N` | 0.8226 | 0.7598 to 0.8807 | benefit | 13.9 s vs 10.5 s |
| `T` vs `N` | 0.8230 | 0.7803 to 0.8777 | benefit | 14.4 s vs 10.5 s |

Q21 fails in all four arms at this memory rung (it is a notuning failure too), so it is outside `G`;
`G` with Q21 penalised at 300 s is 0.8304 for `T`. No answer differs between arms. The shipped layout's
verdict stands: the template is a benefit at this tier, and no layout or session setting is retired.

Per query, `T` against `N` (full table in the summary): Q6 0.41, Q14 0.36, Q15 0.31, Q4 0.74, Q12 0.59,
Q20 0.65 are the wins. No query is above the 1.5x bound with an interval lower bound above 1.0, so there
is no named loser. The three queries the probe asked about:

| Query | `S` vs `N` time ratio (interval) | `T` vs `N` time ratio (interval) | Rows read ratio | Peak memory ratio `S` / `T` |
|---|---|---|---|---|
| Q3 | 0.949 (0.841 to 1.112) | 0.973 (0.820 to 1.168) | 0.54 | 1.00 / 0.99 |
| Q5 | 1.358 (0.965 to 1.634) | 1.390 (1.085 to 1.741) | 0.84 | 0.97 / 0.97 |
| Q18 | 1.179 (0.877 to 1.437) | 1.248 (0.935 to 1.687) | 1.94 | 1.18 / 0.99 |

Q3 is not slower under the tuned layout at this tier. The earlier 1.43x came from a different cell and
does not reproduce here. Two queries are slower under `T` with an interval lower bound above 1.0: Q5
(1.390, interval 1.085 to 1.741) and Q16 (1.485, interval 1.097 to 2.064). Both are under the 1.5x
bound, so neither is a named loser or a rule violation. Q16 reads the same rows and has no plan
evidence here; its cause is unproven.

## Plans for Q3, Q5 and Q18 (`tpch-plans/`)

`EXPLAIN` plan, `EXPLAIN indexes = 1` and `EXPLAIN PIPELINE` for `N`, `S` and `T`, taken from the kept
databases of a 9-round probe cell (`tpch-plans/probe.md`, `probe.json`). `EXPLAIN` runs under the
session settings each arm's timed query actually used, read back from `system.query_log`, so the `T`
plans reflect the tuned session. `probe.json` also holds those settings and the full ProfileEvents of
the last timed execution per arm and query. The probe cell ran three queries, so its intervals are too
wide to judge speed (A/A interval 0.59 to 1.14); it is used for plans, rows, bytes, memory and join
counters only.

| Query / arm | Rows read | Bytes read | Peak memory | Marks | Join build / probe rows | User CPU (s) |
|---|---|---|---|---|---|---|
| Q3 `N` | 7,651,215 | 150,176,700 | 369 MB | 945 | 3,969,081 / 177,268 | 0.43 |
| Q3 `S`, `T` | 4,152,367 | 82,354,528 | 370 MB | 515 | 3,969,081 / 177,268 | 0.51, 0.41 |
| Q5 `N` | 7,661,245 | 160,309,831 | 677 MB | 952 | 6,161,241 / 1,438,185 | 0.81 |
| Q5 `S`, `T` | 6,431,581 | 148,013,191 | 671 MB | 799 | 6,161,241 / 1,438,185 | 1.02, 0.72 |
| Q18 `N` | 7,020,362 | 85,714,918 | 155 MB | 866 | 150,399 / 114 | 0.24 |
| Q18 `S`, `T` | 13,652,430 | 119,958,412 | 174 MB, 170 MB | 1685 | 456 / 150,057 | 0.32, 0.31 |

The join algorithm is the same in every arm and query: the pipeline has `FillingRightJoinSide` and
`JoiningTransform`, which is the in-memory hash join. `S` and `T` have identical plans, indexes and
pipelines for all three queries, so the tuned session settings (`optimize_aggregation_in_order`,
`max_bytes_in_join` and the external-spill thresholds) change nothing in these plans.

### Q18: explained

With `(l_shipdate, l_orderkey, l_linenumber)` and `(o_orderdate, o_orderkey)` as sort keys, the
`IN (subquery)` set on `l_orderkey` and `o_orderkey` no longer prunes. `N` reads 57 of 740 `lineitem`
granules and 50 of 186 `orders` granules (the primary key is the order key); `S` and `T` read 740 of 740
and 186 of 186. Rows read double (1.94x), bytes read grow 1.40x, and the planner also swaps the
`orders`/`customer` join sides, so the build side shrinks from 150,399 rows to 456 and the probe side
grows from 114 to 150,057. Peak memory is unchanged within 18%. The time ratio is 1.18 to 1.25 with
intervals that include 1.0, so the pruning loss is established and the slowdown is not at this tier.

### Q3 and Q5: pruning gain, no join change, residual CPU unproven

The logical plan is identical to `N`; only the indexes differ. The date predicate prunes `orders`
(Q3 90 of 186 granules, Q5 33 of 186) and, for Q3, `lineitem` (406 of 740). Q5 reads all 740 `lineitem`
granules in every arm because its `lineitem` side has no predicate. Join algorithm, join build and probe
rows, and peak memory are equal. The only structural change is a narrower read: one read side starts
5 `MergeTreeSelect` streams instead of 8 (`Resize 5 → 8`). The time ratio for Q3 is 0.95 to 0.97, so there
is nothing to explain. For Q5, user CPU in the probe cell is higher under `S` (1.02 s against 0.81 s)
and lower under `T` (0.72 s), so there is no consistent CPU effect, while the time ratio is about 1.4
under both in the full cell. The cause of the Q5 ratio is unproven. Ruled out:
join algorithm, plan shape, join sizes, peak memory, rows read (fewer, not more) and the tuned session
settings (`S` and `T` have the same plan).

The ADR hypothesis that lost order-key ordering costs join locality is not supported for Q3, which is
not slower, or for Q5, whose join inputs and algorithm are unchanged.

The order-key-first and shipdate-first layout arms were not re-measured here; this evidence compares
only `notuning` with the shipped template.

## SSB SF1

Calibration is part of the verdict. The 9-round cell failed its A/A (G 1.0539, interval 0.9633 to
1.1039, outside 0.95 to 1.05; `cells/ssb-sf1-9rounds-uncalibrated-summary.md`) and its `S` and `T`
numbers are withheld. Following the rule, the cell was repeated at 15 rounds:
`cells/ssb-sf1-15rounds-summary.md`.

| Comparison | G over 13 queries | 95% interval | Verdict | Load plus settle |
|---|---|---|---|---|
| A/A (`N2` vs `N`) | 1.0070 | 0.9654 to 1.0614 | passed | 11.6 s vs 11.8 s |
| `S` vs `N` | 0.8046 | 0.7658 to 0.8396 | benefit | 14.0 s vs 11.8 s |
| `T` vs `N` | 0.7484 | 0.7258 to 0.7881 | benefit | 14.6 s vs 11.8 s |

No query fails, no answer differs, and no query is above the 1.5x bound (the largest `T` ratio is Q2.2
at 1.04). Q4.2 and Q4.3 carry the benefit (0.32 and 0.27). The template is kept.

## TPC-DS SF1: not measurable on this host

Three attempts at the cell (9 rounds, arms `N`, `N2`, `S`, `T`) were each killed by the heavy-work
memory-pressure watchdog (host pressure level 4, exit 79) right after the first arm finished loading;
the benchmark processes were at 0.2 GiB and the Mocker guest is not attributed to the holder. The
registry entry stays `unmeasured`, so the benefit rule directs no template change. The TPC-DS template
is not retired: an unmeasured cell yields no verdict.

## Harness defect fixed with this evidence

On the current adapter, ClickHouse sends its own statement id for every query, so the harness's tagged
id never reached `system.query_log` and the per-query rows read, bytes read and peak memory columns were
empty. `scripts/tuning_evidence/platforms.py` now reads the id the adapter used from the result's
platform timing, and the cells above were measured with that fix (harness commit `ddf6c749fe` plus this
change). The fix does not touch timing.

## Remaining

- The ADR-3 addendum in `benchbox-site` (the Q3, Q5 and Q18 narrative above supersedes the 2026-10-10
  evidence paragraph) is outside this repository.
- ClickHouse TPC-DS SF1 needs a quieter host or a smaller footprint.
- ClickHouse SF10 and the Q21 failure at this memory rung are unmeasured.
