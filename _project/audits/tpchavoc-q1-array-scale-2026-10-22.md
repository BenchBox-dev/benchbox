---
develop_sha: a51e484d1d1f60bef6047c5ee33ad2418c6042bd
checked_sha: 9bf1f52dbb32bdc616deccc979fdc45a038db9e4
measured_at_sha: 9bf1f52dbb32bdc616deccc979fdc45a038db9e4
---

# TPC-Havoc Q1 `1_v7` ARRAY_AGG rewrite at scale on Trino

Question: does the Presto-family `1_v7` rewrite (four ordered `ARRAY_AGG` columns,
then `CROSS JOIN UNNEST`) run at TPC-H scale factor 1 and above without
exceeding Trino's memory limits, and where does it stop?

Answer: it passes at SF1 under stock limits and returns the canonical Q1
result. Its memory grows linearly with scale factor, so it exceeds Trino's
default per-node limit from SF2 on a 6 GiB heap. Where its peak could be read,
the canonical Q1 stayed at or below 16 MiB.

## Method

`_project/scripts/tpchavoc_trino_q1_scale_check.py` runs the canonical Q1 and
`1_v7` (both rendered for `trino` by `TPCHavocBenchmark`) against Trino's
generated `tpch` data through views in the `memory` catalog that rename columns
to the `l_*` form. No table data is stored. It compares every result cell with a
relative tolerance of 1e-8 and reads each query's peak user memory from
`/v1/query/{id}` (`queryStats.peakUserMemoryReservation`).

- Trino 476 (Homebrew), one node acting as coordinator and worker, macOS arm64,
  bundled SQLGlot runtime 30.24.0.7, tree clean at `9bf1f52d`.
- The Trino launcher's `run` mode stops the JVM on this machine; start the
  server by invoking `java -cp '<libexec>/lib/*' ... io.trino.server.TrinoServer`
  directly with `-Dconfig=<config.properties>`, `plugin.dir` and
  `catalog.config-dir` set, and `tpch` and `memory` catalogs.
- Three repeats per query. Peaks vary by run, so every run is listed.

Replay:

```bash
uv run -- python _project/scripts/tpchavoc_trino_q1_scale_check.py \
  --server http://localhost:<port> --scales 1 2 3 10 --repeats 3 --output <file>.json
```

## Stock limits: 6 GiB heap, `query.max-memory-per-node` = 1.80 GB (1843 MiB)

| SF | `lineitem` rows | `1_v7` result | Peak user memory per run (MiB) | Max relative difference from canonical |
|---|---|---|---|---|
| 1 | 6,001,215 | PASS, 4 groups | 805, 799, 799 (about 44% of the limit) | 3.9e-12 |
| 2 | 11,997,996 | FAIL `EXCEEDED_LOCAL_MEMORY_LIMIT` | 1512 at abort | n/a |
| 3 | 17,996,609 | FAIL `EXCEEDED_LOCAL_MEMORY_LIMIT` | 1653 at abort | n/a |
| 10 | 59,986,052 | FAIL `EXCEEDED_LOCAL_MEMORY_LIMIT` | 1513 at abort | n/a |

Every failure names `HashAggregationOperator` as the top consumer (1.48 GB,
1.64 GB and 1.65 GB), which holds the `ARRAY_AGG` states. The canonical query
passed at every scale.

## Raised limits: 8 GiB heap, `query.max-memory-per-node` = 6 GB

| SF | Peak user memory per run (MiB) | Wall seconds per run | Max relative difference |
|---|---|---|---|
| 1 | 799, 799, 799 | 4.2, 4.2, 4.4 | 3.9e-12 |
| 2 | 1827, 1512, 1523 | 8.9, 8.8, 9.1 | 3.3e-12 |
| 3 | 2267, 2795, 2740 | 14.4, 14.2, 14.1 | 3.0e-11 |
| 5 | 4404, 4404, 3836 | 24.5, 23.0, 23.2 | 5.3e-11 |

All scales returned the same four groups as the canonical query. Peak memory
is about 0.75 to 0.93 GiB per scale factor, linear in `lineitem` rows. SF2
reached 1827 MiB in one run, so its stock-limit outcome depends on timing.

## Limits of this evidence

- Peaks of queries that finish in about a second or less can read 0.0 MiB:
  canonical Q1 did so at SF0.1 and SF1 and in one SF2 run, and `1_v7` read 2 to
  29 MiB at SF0.1. Trino updates reservations on a poll of about one second, so
  queries that finish sooner can report nothing. Only `1_v7` peaks from SF1 up
  (3 s or longer) are used above. Readable canonical peaks across both
  configurations were 5.6 to 16.2 MiB.
- This is Trino, not Presto. The Presto C++ `REDUCE` element limit that
  motivated the rewrite is not exercised, and no Presto C++ worker was run.
- One node. A multi-node cluster divides the work by group key, and Q1 has only
  four group keys, so the largest group still lands on one node. That split was
  not measured.
- Scales above SF5 were not run to completion. Growth beyond SF5 is an
  extrapolation of the linear trend, not a measurement.
