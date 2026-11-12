# Tuning evidence: clickhouse-server tpch SF1.0

- Status: completed
- Started 2026-11-12T20:37:25.228725+00:00, finished 2026-11-12T20:41:16.752769+00:00
- Harness version 1, commit ddf6c749fe8dba217a8002a0c7f70ecd98b1d657 (dirty tree)
- Host: joe-mac-mini.local, macOS-27.0.1-arm64-arm-64bit, 10 CPUs, 16.0 GB
- Cache policy: warm (cold-cache support: partial: drops the mark, uncompressed and query caches; the guest page cache is kept)
- Rounds: 9 requested, 9 accepted, warm-up 1, seed 42, 2000 bootstrap resamples, 95% CI
- Per-query timeout 300.0s, host load ceiling 12.0 with 5 retries, throughput streams off
- Data: /Users/joe/Developer/benchmark_runs/tuning-measure-20261112/datagen/tpch_sf1; benchmark queries in the adapter's dialect, default substitution parameters
- Rule thresholds: min_rounds 7, aa_point_low 0.95, aa_point_high 1.05, load_settle_ratio 1.5, slow_query_ratio 1.5

## Arms

| Arm | Load | Session | Template | Engine | Load s | Settle s | Settled | Load+settle s | Notes |
|---|---|---|---|---|---|---|---|---|---|
| N | notuning | notuning | notuning | 25.8.33.6 | 9.6 | 2.0 (clickhouse_merge_settle) | yes | 11.6 | - |
| N2 | notuning | notuning | notuning | 25.8.33.6 | 8.5 | 2.0 (clickhouse_merge_settle) | yes | 10.6 | - |
| S | tuned | notuning | examples/tunings/clickhouse/tpch_tuned.yaml | 25.8.33.6 | 12.7 | 2.0 (clickhouse_merge_settle) | yes | 14.7 | - |
| T | tuned | tuned | examples/tunings/clickhouse/tpch_tuned.yaml | 25.8.33.6 | 10.0 | 2.0 (clickhouse_merge_settle) | yes | 12.1 | the tuned session runs Q21 with the shipped per-query join_algorithm = 'hash' setting |

Load time for S, T includes the adapter's own post-load merge wait.

## Container memory evidence

- Container ch-probe via mocker 0.7.2; runtime container CLI version 1.1.0 (build: release, commit: unspeci)
- Guest memory.max: 5637144576
- OOM kills during the run: 0
- ClickHouse server ran in a Linux VM under Apple container, not under Linux Docker

## Calibration

- Source: in-run arm N2; passed: yes; G 0.9737 [0.5891, 1.1416]
- Noise: |G-1| 0.0263; per-query |ratio-1| median 0.160, max 0.214 (Q5); median per-query CI half-width 0.860

## N2 vs N

- G over 3 queries passing in both arms: 0.9737 [0.5891, 1.1416]
- G with each failed query at 300.0s: 0.9737
- Failures: N none; N2 none
- Sum of medians (common set): N 0.460s, N2 0.488s
- Total elapsed over accepted rounds: N 8.179s, N2 6.723s
- Answer mismatches: none

| Q | N median ms | N2 median ms | Ratio | 95% CI | Rows read ratio | Peak memory ratio |
|---|---|---|---|---|---|---|
| 3 | 133.7 | 121.1 | 0.906 | [0.720, 2.439] | 1.00 | 1.01 |
| 5 | 247.7 | 300.6 | 1.214 | [0.451, 2.203] | 1.00 | 1.00 |
| 18 | 78.6 | 66.0 | 0.840 | [0.377, 1.165] | 1.00 | 0.88 |

## S vs N

- G over 3 queries passing in both arms: 1.1972 [0.7711, 1.3608]
- G with each failed query at 300.0s: 1.1972
- Failures: N none; S none
- Sum of medians (common set): N 0.460s, S 0.599s
- Total elapsed over accepted rounds: N 8.179s, S 7.386s
- Answer mismatches: none
- Verdict: neutral
  - scope: SF1.0, engine {'N': '25.8.33.6', 'S': '25.8.33.6'}, memory.max 5637144576, 2026-11-12T20:41:16.752769+00:00

| Q | N median ms | S median ms | Ratio | 95% CI | Rows read ratio | Peak memory ratio |
|---|---|---|---|---|---|---|
| 3 | 133.7 | 140.9 | 1.054 | [1.002, 1.233] | 0.54 | 1.01 |
| 5 | 247.7 | 372.8 | 1.505 | [0.549, 1.870] | 0.84 | 0.98 |
| 18 | 78.6 | 85.1 | 1.082 | [0.704, 1.590] | 1.94 | 1.02 |

## T vs N

- G over 3 queries passing in both arms: 1.1167 [0.5130, 1.3589]
- G with each failed query at 300.0s: 1.1167
- Failures: N none; T none
- Sum of medians (common set): N 0.460s, T 0.505s
- Total elapsed over accepted rounds: N 8.179s, T 5.834s
- Answer mismatches: none
- Verdict: neutral
  - scope: SF1.0, engine {'N': '25.8.33.6', 'T': '25.8.33.6'}, memory.max 5637144576, 2026-11-12T20:41:16.752769+00:00

| Q | N median ms | T median ms | Ratio | 95% CI | Rows read ratio | Peak memory ratio |
|---|---|---|---|---|---|---|
| 3 | 133.7 | 147.2 | 1.101 | [0.632, 1.695] | 0.54 | 1.01 |
| 5 | 247.7 | 264.4 | 1.068 | [0.341, 1.659] | 0.84 | 0.98 |
| 18 | 78.6 | 93.1 | 1.184 | [0.616, 1.879] | 1.94 | 1.04 |

## Correctness

- Reference arm N; mismatching executions: 0

## Host load per round

| Phase | Round | Attempt | Load before | Load after | Accepted | Reason |
|---|---|---|---|---|---|---|
| warmup | 0 | 0 | - | - | yes |  |
| timed | 0 | 0 | 8.30 | 9.64 | yes |  |
| timed | 1 | 0 | 9.64 | 13.02 | yes |  |
| timed | 2 | 0 | 13.02 | - | no | host load 13.02 above ceiling 12.0 |
| timed | 2 | 1 | 14.87 | - | no | host load 14.87 above ceiling 12.0 |
| timed | 2 | 2 | 7.05 | 7.26 | yes |  |
| timed | 3 | 0 | 7.26 | 7.26 | yes |  |
| timed | 4 | 0 | 7.26 | 7.26 | yes |  |
| timed | 5 | 0 | 7.26 | 7.08 | yes |  |
| timed | 6 | 0 | 7.08 | 6.83 | yes |  |
| timed | 7 | 0 | 6.83 | 6.83 | yes |  |
| timed | 8 | 0 | 6.83 | 6.83 | yes |  |
