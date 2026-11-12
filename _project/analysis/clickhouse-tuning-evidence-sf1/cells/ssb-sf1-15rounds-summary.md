# Tuning evidence: clickhouse-server ssb SF1.0

- Status: completed
- Started 2026-11-12T20:56:14.510922+00:00, finished 2026-11-12T20:59:44.085792+00:00
- Harness version 1, commit ddf6c749fe8dba217a8002a0c7f70ecd98b1d657 (dirty tree)
- Host: joe-mac-mini.local, macOS-27.0.1-arm64-arm-64bit, 10 CPUs, 16.0 GB
- Cache policy: warm (cold-cache support: partial: drops the mark, uncompressed and query caches; the guest page cache is kept)
- Rounds: 15 requested, 15 accepted, warm-up 1, seed 42, 2000 bootstrap resamples, 95% CI
- Per-query timeout 300.0s, host load ceiling 12.0 with 5 retries, throughput streams off
- Data: /Users/joe/Developer/benchmark_runs/duckdb-tuned-sf10-20261112/datagen/ssb_sf1; benchmark queries in the adapter's dialect, default substitution parameters
- Rule thresholds: min_rounds 7, aa_point_low 0.95, aa_point_high 1.05, load_settle_ratio 1.5, slow_query_ratio 1.5

## Arms

| Arm | Load | Session | Template | Engine | Load s | Settle s | Settled | Load+settle s | Notes |
|---|---|---|---|---|---|---|---|---|---|
| N | notuning | notuning | notuning | 25.8.33.6 | 9.7 | 2.0 (clickhouse_merge_settle) | yes | 11.8 | - |
| N2 | notuning | notuning | notuning | 25.8.33.6 | 9.5 | 2.0 (clickhouse_merge_settle) | yes | 11.6 | - |
| S | tuned | notuning | examples/tunings/clickhouse/ssb_tuned.yaml | 25.8.33.6 | 11.9 | 2.1 (clickhouse_merge_settle) | yes | 14.0 | - |
| T | tuned | tuned | examples/tunings/clickhouse/ssb_tuned.yaml | 25.8.33.6 | 12.5 | 2.0 (clickhouse_merge_settle) | yes | 14.6 | - |

Load time for S, T includes the adapter's own post-load merge wait.

## Container memory evidence

- Container ch-ssb via mocker 0.7.2; runtime container CLI version 1.1.0 (build: release, commit: unspeci)
- Guest memory.max: 5637144576
- OOM kills during the run: 0
- ClickHouse server ran in a Linux VM under Apple container, not under Linux Docker

## Calibration

- Source: in-run arm N2; passed: yes; G 1.0070 [0.9654, 1.0614]
- Noise: |G-1| 0.0070; per-query |ratio-1| median 0.038, max 0.342 (QQ3.2); median per-query CI half-width 0.153

## N2 vs N

- G over 13 queries passing in both arms: 1.0070 [0.9654, 1.0614]
- G with each failed query at 300.0s: 1.0070
- Failures: N none; N2 none
- Sum of medians (common set): N 2.116s, N2 2.088s
- Total elapsed over accepted rounds: N 32.520s, N2 32.707s
- Answer mismatches: none

| Q | N median ms | N2 median ms | Ratio | 95% CI | Rows read ratio | Peak memory ratio |
|---|---|---|---|---|---|---|
| Q1.1 | 51.8 | 47.5 | 0.917 | [0.791, 1.040] | 1.00 | 0.94 |
| Q1.2 | 44.1 | 48.0 | 1.090 | [0.870, 1.223] | 1.00 | 1.00 |
| Q1.3 | 48.8 | 48.4 | 0.991 | [0.821, 1.087] | 1.00 | 0.88 |
| Q2.1 | 71.0 | 72.2 | 1.017 | [0.908, 1.174] | 1.00 | 0.99 |
| Q2.2 | 56.7 | 57.3 | 1.011 | [0.894, 1.200] | 1.00 | 0.89 |
| Q2.3 | 53.7 | 52.6 | 0.979 | [0.882, 1.152] | 1.00 | 1.01 |
| Q3.1 | 295.2 | 284.1 | 0.962 | [0.806, 1.150] | 1.00 | 1.00 |
| Q3.2 | 168.9 | 226.7 | 1.342 | [1.066, 1.526] | 1.00 | 1.00 |
| Q3.3 | 175.0 | 178.4 | 1.019 | [0.831, 1.277] | 1.00 | 1.00 |
| Q3.4 | 178.0 | 189.6 | 1.065 | [0.945, 1.348] | 1.00 | 0.99 |
| Q4.1 | 103.8 | 100.1 | 0.965 | [0.852, 1.173] | 1.00 | 1.04 |
| Q4.2 | 451.9 | 400.7 | 0.887 | [0.813, 1.073] | 1.00 | 1.00 |
| Q4.3 | 417.6 | 382.7 | 0.916 | [0.878, 1.045] | 1.00 | 1.00 |

## S vs N

- G over 13 queries passing in both arms: 0.8046 [0.7658, 0.8396]
- G with each failed query at 300.0s: 0.8046
- Failures: N none; S none
- Sum of medians (common set): N 2.116s, S 1.503s
- Total elapsed over accepted rounds: N 32.520s, S 23.538s
- Answer mismatches: none
- Verdict: benefit
  - scope: SF1.0, engine {'N': '25.8.33.6', 'S': '25.8.33.6'}, memory.max 5637144576, 2026-11-12T20:59:44.085792+00:00
  - finding: CI upper bound 0.8396 is below 1.0

| Q | N median ms | S median ms | Ratio | 95% CI | Rows read ratio | Peak memory ratio |
|---|---|---|---|---|---|---|
| Q1.1 | 51.8 | 38.7 | 0.748 | [0.680, 0.907] | 1.00 | 0.67 |
| Q1.2 | 44.1 | 42.6 | 0.966 | [0.773, 1.107] | 1.00 | 0.62 |
| Q1.3 | 48.8 | 43.7 | 0.896 | [0.804, 1.087] | 1.00 | 0.59 |
| Q2.1 | 71.0 | 70.4 | 0.991 | [0.872, 1.099] | 1.00 | 0.98 |
| Q2.2 | 56.7 | 50.6 | 0.893 | [0.800, 1.025] | 1.00 | 0.88 |
| Q2.3 | 53.7 | 49.5 | 0.922 | [0.803, 1.072] | 1.00 | 0.82 |
| Q3.1 | 295.2 | 284.2 | 0.963 | [0.804, 1.169] | 1.00 | 0.98 |
| Q3.2 | 168.9 | 197.3 | 1.168 | [0.933, 1.267] | 1.00 | 0.99 |
| Q3.3 | 175.0 | 154.6 | 0.883 | [0.751, 1.192] | 1.00 | 1.00 |
| Q3.4 | 178.0 | 165.0 | 0.927 | [0.735, 1.134] | 1.00 | 0.98 |
| Q4.1 | 103.8 | 111.4 | 1.074 | [0.880, 1.223] | 1.00 | 1.05 |
| Q4.2 | 451.9 | 164.2 | 0.363 | [0.281, 0.439] | 1.00 | 0.98 |
| Q4.3 | 417.6 | 130.4 | 0.312 | [0.253, 0.355] | 1.00 | 0.98 |

## T vs N

- G over 13 queries passing in both arms: 0.7484 [0.7258, 0.7881]
- G with each failed query at 300.0s: 0.7484
- Failures: N none; T none
- Sum of medians (common set): N 2.116s, T 1.316s
- Total elapsed over accepted rounds: N 32.520s, T 21.040s
- Answer mismatches: none
- Verdict: benefit
  - scope: SF1.0, engine {'N': '25.8.33.6', 'T': '25.8.33.6'}, memory.max 5637144576, 2026-11-12T20:59:44.085792+00:00
  - finding: CI upper bound 0.7881 is below 1.0

| Q | N median ms | T median ms | Ratio | 95% CI | Rows read ratio | Peak memory ratio |
|---|---|---|---|---|---|---|
| Q1.1 | 51.8 | 44.6 | 0.862 | [0.753, 0.979] | 1.00 | 0.57 |
| Q1.2 | 44.1 | 40.0 | 0.907 | [0.760, 1.089] | 1.00 | 0.52 |
| Q1.3 | 48.8 | 41.4 | 0.848 | [0.676, 1.013] | 1.00 | 0.51 |
| Q2.1 | 71.0 | 71.0 | 1.000 | [0.888, 1.120] | 1.00 | 1.00 |
| Q2.2 | 56.7 | 58.9 | 1.039 | [0.958, 1.130] | 1.00 | 0.87 |
| Q2.3 | 53.7 | 54.6 | 1.016 | [0.893, 1.214] | 1.00 | 0.87 |
| Q3.1 | 295.2 | 230.2 | 0.780 | [0.665, 0.853] | 1.00 | 0.98 |
| Q3.2 | 168.9 | 155.7 | 0.922 | [0.777, 1.182] | 1.00 | 1.01 |
| Q3.3 | 175.0 | 141.1 | 0.806 | [0.656, 1.049] | 1.00 | 0.99 |
| Q3.4 | 178.0 | 122.3 | 0.687 | [0.597, 0.909] | 1.00 | 0.99 |
| Q4.1 | 103.8 | 101.3 | 0.976 | [0.835, 1.080] | 1.00 | 1.08 |
| Q4.2 | 451.9 | 142.8 | 0.316 | [0.263, 0.373] | 1.00 | 0.98 |
| Q4.3 | 417.6 | 112.2 | 0.269 | [0.250, 0.332] | 1.00 | 0.97 |

## Correctness

- Reference arm N; mismatching executions: 0

## Host load per round

| Phase | Round | Attempt | Load before | Load after | Accepted | Reason |
|---|---|---|---|---|---|---|
| warmup | 0 | 0 | - | - | yes |  |
| timed | 0 | 0 | 7.09 | 6.85 | yes |  |
| timed | 1 | 0 | 6.85 | 7.02 | yes |  |
| timed | 2 | 0 | 7.02 | 7.35 | yes |  |
| timed | 3 | 0 | 7.35 | 7.37 | yes |  |
| timed | 4 | 0 | 7.37 | 7.58 | yes |  |
| timed | 5 | 0 | 7.21 | 11.36 | yes |  |
| timed | 6 | 0 | 10.77 | 10.31 | yes |  |
| timed | 7 | 0 | 10.31 | 9.79 | yes |  |
| timed | 8 | 0 | 9.79 | 8.76 | yes |  |
| timed | 9 | 0 | 8.76 | 9.44 | yes |  |
| timed | 10 | 0 | 9.44 | 9.41 | yes |  |
| timed | 11 | 0 | 9.41 | 8.96 | yes |  |
| timed | 12 | 0 | 8.96 | 8.41 | yes |  |
| timed | 13 | 0 | 8.41 | 8.18 | yes |  |
| timed | 14 | 0 | 8.18 | 7.92 | yes |  |
