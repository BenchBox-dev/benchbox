---
develop_sha: 7b8484a4c7c9d14062c38552511ba1f38789eaf9
checked_sha: e642bf0e60005a0632023e6473ebbca40a0c9005
measured_at_sha: e642bf0e60005a0632023e6473ebbca40a0c9005
replay_sha: 9fc8a585bcda9970e69c8b7fde4687583356bb01
replay_scope: changed-file tests for the comparator and clock change through make pr-preflight, 20 passed; no query was re-run and no measurement was replaced
---

# TPC-Havoc variants on PrestoDB 0.298.1 (Java)

Question: does every TPC-Havoc variant, rendered for `presto`, run on a PrestoDB
Java server and return the DuckDB result, and what does the `1_v7`
list-aggregate rewrite cost in memory at scale factors 1 to 3?

Answer: 220 of 220 variants and 22 of 22 canonical queries run on PrestoDB with
no engine error. 215 variants and all 22 canonical queries match DuckDB on the
first execution. Four `Q15` variants returned no row on their first execution
and one `Q1` variant returned the right rows in a different order. The `Q15`
misses are exact equality on independently summed `DOUBLE` values, which also
makes the canonical `Q15` return no row about half the time on this server
(not a rewrite defect). The `Q1` order difference is a rewrite defect: the
`QUALIFY` translation leaves `ORDER BY` inside a derived table. `1_v7` needs about
1.9 GiB of per-node memory per scale factor on PrestoDB, 2.4 times the Trino
figure, and fails at SF1 to SF3 under the default limit and at SF3 under a
5 GB limit.

Not run: Amazon Athena, Google BigQuery and Presto C++ ("prestissimo")
workers. No Presto C++ worker was available, so the reported 100,000-element
`REDUCE` input cap is still untested. This record says nothing about those
three engines.

## Variant set

`VARIANT_REGISTRY` (`benchbox/core/tpchavoc/variant_sets`) holds 22 queries with
ten variants each, `1_v1` to `22_v10`: 220 variants. Ids are `<query>_v<n>`;
every `_v7`, `16_v6` and every other id is in that set. The 22 canonical queries
`1` to `22` were run as controls, for 242 executions.

Nine variant ids have a Presto-specific rewrite (`PRESTO_REWRITTEN_VARIANT_IDS`
in `tests/unit/tpchavoc/test_presto_compat.py`, built from `dialect_compat.py`):

| Rule | Ids |
|---|---|
| `inline_select_aliases` (`PRESTO_ALIAS_VARIANT_IDS`) | `3_v7`, `4_v7`, `5_v7`, `7_v7`, `9_v7`, `10_v7`, `11_v7` |
| `filtered_aggregates_to_conditional`, windows only (`PRESTO_WINDOW_FILTER_VARIANT_IDS`) | `2_v7` |
| `order_list_aggregates` + `reduce_list_aggregates` (`LIST_AGGREGATE_VARIANT_IDS`) | `1_v7` |

The other 211 variants have no Presto-specific rule. They go through the generic
DuckDB-to-Presto translation in `TPCHavocBenchmark.get_query`, which changes
the text of all 242 queries.

## Method

Harness: `_project/scripts/tpchavoc_presto_variant_check.py`, run with
`uv run -- python -m _project.scripts.tpchavoc_presto_variant_check`. It calls
`TPCHavocBenchmark.get_query(id, dialect="presto")`, posts the text to the
server's REST protocol (`X-Presto-*` headers) and compares the rows with the
DuckDB result of `get_query(id)`.

- Server: PrestoDB 0.298.1 (`0.298.1-9e1b45f`), Homebrew install at
  `/opt/homebrew/Cellar/prestodb/0.298.1`, Java 17, one node acting as
  coordinator and worker, macOS arm64, port 18081. Started with
  `launcher --etc-dir=/tmp/presto-verify/etc --data-dir=/tmp/presto-verify/data run`
  and `JAVA_HOME` set to the Homebrew OpenJDK 17. The `etc` directory held
  `-server -Xmx6G -XX:+UseG1GC -XX:+ExitOnOutOfMemoryError`, a `tpch` catalog
  (`connector.name=tpch`, `tpch.column-naming=STANDARD`, which gives the `l_*`
  column names the queries use), a `memory` catalog, and a single-node
  `config.properties`. All limits were the defaults, so `query.max-memory-per-node`
  was 614.4 MB (10% of the heap) and `query.max-total-memory-per-node` 1.29 GB.
- Data: `tpch.tiny` (scale factor 0.01; `lineitem` 60,175 rows, `orders` 15,000,
  `partsupp` 8,000, `part` 2,000, `customer` 1,500, `supplier` 100, `nation` 25,
  `region` 5). The harness reads every table from Presto and loads it into an
  in-memory DuckDB 1.5.5 database, so both engines see identical rows. Presto
  `bigint`, `integer`, `double` and `date` columns map to DuckDB `BIGINT`,
  `INTEGER`, `DOUBLE` and `DATE`; `varchar` maps to `VARCHAR`. Prices are
  `DOUBLE` on both sides; the reference is not the `DECIMAL` schema BenchBox
  creates for DuckDB.
- Comparison: row counts and column counts must be equal. Integer cells compare
  exactly. Cells where either side is a float compare with a relative tolerance
  of 1e-8 (`|a-b| / max(|a|, |b|)`), the tolerance of the Trino harness. A `NaN`
  or infinite cell on either side is a mismatch. Strings and dates compare
  exactly (dates as ISO text). The largest relative difference among passing
  cells was 3.1e-13.
- Row order: a query whose DuckDB text has a top-level `ORDER BY` (198 of 242,
  found by a scan that skips parentheses and quotes) is compared in order
  (194 compared and passed or differed in order; the other four had no row on
  the Presto side). The 44 queries without one are compared as multisets after
  sorting both sides by a rounded key. An ordered query whose rows match only
  after sorting is reported `ORDER_DIFFERS`, not `PASS`.
- Reruns: any non-`PASS` result is executed four more times and every status is
  recorded.
- Limits of the check: `tpch.tiny` is small. 80 of the 220 variants return one
  row and 30 return two, so a wrong value in a 1-row aggregate is detected but
  a wrong grouping is not exercised by those. The 22 canonical queries are
  controls, not variants.

## Results on `tpch.tiny`

Counts over all 242 executions on the first run: 237 `PASS`, 4 `MISMATCH`,
1 `ORDER_DIFFERS`, 0 `ERROR`. Over the 220 variants: 215 `PASS`, 4 `MISMATCH`,
1 `ORDER_DIFFERS`, 0 `ERROR`. No variant failed to run, so there is no engine
error text to list. Total query time for all 242 was 65.6 seconds.

| Query | canonical | v1 | v2 | v3 | v4 | v5 | v6 | v7 | v8 | v9 | v10 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Q1 | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | ORDER | PASS | PASS |
| Q2 | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| Q3 | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| Q4 | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| Q5 | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| Q6 | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| Q7 | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| Q8 | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| Q9 | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| Q10 | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| Q11 | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| Q12 | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| Q13 | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| Q14 | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| Q15 | PASS | MISMATCH | PASS | PASS | MISMATCH | PASS | PASS | MISMATCH | PASS | PASS | MISMATCH |
| Q16 | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| Q17 | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| Q18 | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| Q19 | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| Q20 | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| Q21 | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| Q22 | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |

`ORDER` is `ORDER_DIFFERS`. All nine Presto-rewritten ids (`1_v7`, `2_v7`,
`3_v7`, `4_v7`, `5_v7`, `7_v7`, `9_v7`, `10_v7`, `11_v7`) passed; `1_v7` differed
from DuckDB by at most 9.0e-15.

### Non-`PASS` results

| Id | First run | Four reruns | Cause |
|---|---|---|---|
| `1_v8` | `ORDER_DIFFERS`, 4 rows, values equal within 8.3e-15 | `ORDER_DIFFERS` x4 | Rewrite defect, see below |
| `15_v1` | `MISMATCH`, 0 rows against 1 | `PASS`, `MISMATCH`, `MISMATCH`, `MISMATCH` | `DOUBLE` equality, see below |
| `15_v4` | `MISMATCH`, 0 rows against 1 | `PASS` x4 | `DOUBLE` equality |
| `15_v7` | `MISMATCH`, 0 rows against 1 | `PASS`, `PASS`, `MISMATCH`, `MISMATCH` | `DOUBLE` equality |
| `15_v10` | `MISMATCH`, 0 rows against 1 | `MISMATCH`, `MISMATCH`, `MISMATCH`, `PASS` | `DOUBLE` equality |

`1_v8` (Q1 with `QUALIFY count(*) OVER w > 0` and a final `ORDER BY l_returnflag,
l_linestatus`) is rendered for Presto as
`SELECT ... FROM (SELECT DISTINCT ..., COUNT(*) OVER (...) AS "_w" FROM lineitem
WHERE ... ORDER BY "l_returnflag", "l_linestatus") AS "_t" WHERE "_w" > 0`.
The `ORDER BY` sits inside the derived table and the outer query has none, so
the SQL does not define the output order. In the recorded run DuckDB's first
two groups were `A/F` then `N/F` and Presto's were `N/F` then `A/F`, the third
group `N/O` in both; all five Presto executions returned the same rows in a
different order from DuckDB. The first two rows:

| Engine | row 1 | row 2 |
|---|---|---|
| DuckDB | `A, F, 380456.0, 532348211.6499996, ...` | `N, F, 8971.0, 12384801.369999997, ...` |
| PrestoDB | `N, F, 8971.0, 12384801.37, ...` | `A, F, 380456.0, 532348211.6499998, ...` |

The suspected rule is the `QUALIFY` elimination of the fork's Presto generator
(the transform that introduces the `_t` and `_w` aliases); it should hoist the
`ORDER BY` to the outer select. `1_v8` is the only one of the 242 rendered
queries that contains that pattern. It was not fixed in this change.

The four `Q15` variants, and the canonical `Q15`, select the supplier whose
revenue equals the maximum revenue with `=` between two independently computed
`SUM(l_extendedprice * (1 - l_discount))` values. With `DOUBLE` columns the
Presto sums can differ in the last bits from run to run, so the equality drops
the supplier. The canonical `Q15` passed in the recorded run, yet returned one
row in only 9 of 20 further executions of the same text on this server; the
variants returned a row in 8 to 20 of 20 (`15_v1` 11, `15_v2` 16, `15_v3` 12,
`15_v4` 9, `15_v5` 13, `15_v6` 11, `15_v7` 8, `15_v8` 12, `15_v9` 20,
`15_v10` 14). Those twenty-run counts come from an ad hoc loop with the
harness's client on the same tree, not from the harness. A single execution of
`Q15` or any of its variants on this server is therefore not evidence either
way. The mechanism (non-deterministic parallel summation order in the
`DOUBLE` aggregates) is inferred from the varying result of an unchanged query
and was not traced further [INFERENCE]. BenchBox's own TPC-H schema uses
`DECIMAL`, where the equality is exact; that was not exercised here.

## `1_v7` memory at SF1 to SF3

Harness mode `scale`, schemas `tpch.sf1`, `sf2` and `sf3` (no views are needed
because `STANDARD` column naming already yields `l_*` names), three runs of each
query per scale. Peak memory is `queryStats.peakUserMemoryReservation` and
`peakTotalMemoryReservation` from `GET /v1/query/{id}`; both fields exist in
PrestoDB 0.298.1. The canonical `Q1` passed at every scale with a peak user
reservation of at most 1.4 MiB (peak total at most 6.1 MiB).

Default limits, `-Xmx6G`, `query.max-memory-per-node` = 614.4 MB (586 MiB):

| SF | `lineitem` rows | `1_v7` | Allocated at abort | Peak user reservation read after abort |
|---|---|---|---|---|
| 1 | 6,001,215 | FAIL `EXCEEDED_LOCAL_MEMORY_LIMIT` | 614.36 MB | 530 MiB |
| 2 | 11,997,996 | FAIL `EXCEEDED_LOCAL_MEMORY_LIMIT` | 613.37 MB | 458 MiB |
| 3 | 17,996,609 | FAIL `EXCEEDED_LOCAL_MEMORY_LIMIT` | 614.39 MB | 609 MiB |

All three name `HashAggregationOperator` (`SINGLE;ORDER_BY;`) as the consumer of
about 614 MB. The abort reads below the limit because the reservation is polled;
the allocation at abort is the limit.

Raised limits, `-Xmx8G`, `query.max-memory-per-node` = `query.max-total-memory-per-node`
= 5 GB, `experimental.reserved-pool-enabled=false`:

| SF | `lineitem` rows | `1_v7` | Peak user reservation per run (MiB) | Wall seconds per run | Max relative difference from canonical |
|---|---|---|---|---|---|
| 1 | 6,001,215 | PASS, 4 rows | 1915, 1894, 1894 | 26.9, 47.6, 65.3 | 3.9e-12 |
| 2 | 11,997,996 | PASS, 4 rows | 3820, 3789, 3789 | 174.0, 32.0, 29.4 | 3.3e-12 |
| 3 | 17,996,609 | FAIL `EXCEEDED_LOCAL_MEMORY_LIMIT` (total limit 5 GB) | 4168 read after abort; 5.00 GB allocated, `HashAggregationOperator` 4.98 GB | n/a | n/a |

Peak user and peak total reservations were equal for `1_v7`. Memory is linear in
`lineitem` rows at about 1.9 GiB per scale factor (1894 to 1915 MiB at SF1, 3789
to 3820 MiB at SF2), against 760 to 835 MiB per scale factor on Trino 484 in
`tpchavoc-q1-array-scale-2026-10-22.md`. By that slope SF3 would need about
5.6 GiB [INFERENCE]; it was not run with a limit above 5 GB, because a 10 GB
heap does not fit beside other JVMs on the 16 GiB test machine. Wall times are
uncontrolled; another user's JVMs were running and the SF2 first run took 174
seconds against 29 to 32 for the next two.

## Limits of this evidence

- PrestoDB Java only, one node, `tpch.tiny` for correctness and `tpch.sf1` to
  `sf3` for memory. Athena, BigQuery and Presto C++ workers were not run.
- The reference is DuckDB on the data Presto generated, with `DOUBLE` prices, not
  BenchBox-generated data with `DECIMAL`.
- `Q15` results from a single execution are non-deterministic on this server.
- The 20-run `Q15` loop was an ad hoc script, not committed.
- Row-order handling relies on a scan of the DuckDB text for a top-level
  `ORDER BY`; order ties are not distinguished from a wrong order.
- Raw JSON output, server logs and the Presto configuration directories were kept
  under `/tmp` and are not committed.

## Harness changes after the measurement

The measurement used the harness at `e642bf0e6`. Commit `9fc8a585b` changes two
things in it: unordered comparison now matches rows inside each group of equal
rounded sort keys when the positional comparison fails, and query durations use
`mono_time` and `elapsed_seconds` from `benchbox.utils.clock`. A comparison that
passed positionally is unchanged, so no `PASS` in this record depends on the
change. The four `MISMATCH` results differ in row count and the `ORDER_DIFFERS`
result is decided before the new matching runs, so neither is affected either.
Only the unit tests were run again; the numbers above are the original
measurement.
