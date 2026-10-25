---
develop_sha: 9a184b6195702bfde8c049712e400c67ad213890
checked_sha: 62ad33caf5c96ed49886ed1c443fe9ac1ff01be2
measured_at_sha: 62ad33caf5c96ed49886ed1c443fe9ac1ff01be2
---

# TPC-Havoc Q1 `1_v7` list-aggregate rewrite at scale on Trino

Question: does the Presto-family `1_v7` text run at TPC-H scale factor 1 and
above without exceeding Trino's memory limits, and where does it stop?

Answer: it returns the canonical Q1 result at SF1 to SF5. Under Trino's default
per-node limit it passes at SF1 and SF2 and fails from SF3. Memory grows
linearly with scale factor, at about 0.8 GiB per scale factor, so no scale is
safe without a larger per-node limit. The canonical Q1 needs 16 MiB or less.

## What was measured

For Trino, Presto and Athena, `1_v7` is translated from its DuckDB text by the
fork's `order_list_aggregates` and `reduce_list_aggregates` rewrites
(`translate_list_aggregate_variant` in `benchbox/core/tpchavoc/dialect_compat.py`).
The text holds four `ARRAY_AGG(... ORDER BY l_orderkey, l_linenumber)` columns
per `(l_returnflag, l_linestatus)` group. The outer query reduces them with
`REDUCE`, `ZIP_WITH`, `TRANSFORM` and `ZIP` and counts rows with `CARDINALITY`.
Every row of a group is held in those arrays before any value is summed.

## Method

`_project/scripts/tpchavoc_trino_q1_scale_check.py` runs the canonical Q1 and
`1_v7` (both rendered for `trino` by `TPCHavocBenchmark`) against Trino's
generated `tpch` data through views in the `memory` catalog that rename columns
to the `l_*` form. No table data is stored. It compares every result cell with a
relative tolerance of 1e-8 and reads each query's peak user memory from
`/v1/query/{id}` (`queryStats.peakUserMemoryReservation`).

- Trino 484 (Homebrew) on Java 27, one node acting as coordinator and worker,
  macOS arm64, bundled SQLGlot runtime 30.24.0.13, tree clean at `62ad33caf`.
- Start the server by invoking `java -cp '<libexec>/lib/*'` directly with
  `--add-modules=jdk.incubator.vector`, `--enable-native-access=ALL-UNNAMED`,
  `-Dconfig=<config.properties>`, `plugin.dir` and `catalog.config-dir` set, and
  `tpch` and `memory` catalogs. Trino 484 refuses to start without the Vector
  API module.
- Three repeats per query. Peaks vary by run, so every run is listed.

Replay:

```bash
uv run -- python _project/scripts/tpchavoc_trino_q1_scale_check.py \
  --server http://localhost:<port> --scales 1 2 3 10 --repeats 3 --output <file>.json
```

## Stock limits: 6 GiB heap, `query.max-memory-per-node` = 1.80 GB (1843 MiB)

| SF | `lineitem` rows | `1_v7` result | Peak user memory per run (MiB) | Max relative difference from canonical |
|---|---|---|---|---|
| 1 | 6,001,215 | PASS, 4 groups | 823, 823, 831 (about 45% of the limit) | 3.9e-12 |
| 2 | 11,997,996 | PASS, 4 groups | 1595, 1595, 1560 (about 87% of the limit) | 3.3e-12 |
| 3 | 17,996,609 | FAIL `EXCEEDED_LOCAL_MEMORY_LIMIT` | 1.75 GB allocated at abort | n/a |
| 10 | 59,986,052 | FAIL `EXCEEDED_LOCAL_MEMORY_LIMIT` | 1.76 GB allocated at abort | n/a |

Both failures name `HashAggregationOperator` as the top consumer (1.65 GB),
which holds the `ARRAY_AGG` states. The canonical query passed at every scale.

## Raised limits: 8 GiB heap, `query.max-memory-per-node` = 6 GB

| SF | Peak user memory per run (MiB) | Max relative difference |
|---|---|---|
| 1 | 799, 799, 816 | 3.9e-12 |
| 2 | 1560, 1592, 1595 | 3.3e-12 |
| 3 | 2393, 2481, 2339 | 3.0e-11 |
| 5 | 3897, 3897, 3897 | 5.3e-11 |

All scales returned the same four groups as the canonical query. Peak memory per
scale factor is 780 to 830 MiB at SF1 to SF5, so it is linear in `lineitem` rows.
With the stock limit that puts the failure between SF2 (87% of the limit) and SF3.

## Limits of this evidence

- Peaks of queries that finish in about a second or less can read 0.0 MiB
  (canonical Q1 at SF1 on stock limits). Trino updates reservations on a poll of
  about one second. Only `1_v7` peaks are used above; they come from queries that
  ran for several seconds. Canonical peaks reached at most 14.8 MiB.
- Wall time is recorded by the harness but is not reported here. Two other Trino
  servers were running on the machine, and the runs for `1_v7` at SF3 and SF5
  ranged from 57 to 127 seconds. Treat time as uncontrolled.
- This is Trino, not Presto. The current text reduces with `REDUCE`, and Presto
  C++ workers are reported to cap `REDUCE` input at 100,000 elements, which a
  group of millions of rows exceeds. That cap was not tested here. The pull
  request that introduced this text checked Trino and PrestoDB at TPC-H tiny
  scale only. This record does not exercise a Presto C++ worker, and none was
  available.
- One node. A multi-node cluster divides work by group key, and Q1 has only four
  group keys, so the largest group still lands on one node. That split was not
  measured.
- Scales above SF5 were not run to completion on the raised limits. SF10 was
  run only on the stock limit, where it fails. Growth beyond SF5 is an
  extrapolation of the linear trend, not a measurement.
