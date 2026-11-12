# Tuning evidence: clickhouse-server ssb SF1.0

- Status: completed
- Started 2026-11-12T20:42:30.073200+00:00, finished 2026-11-12T20:46:03.932706+00:00
- Harness version 1, commit ddf6c749fe8dba217a8002a0c7f70ecd98b1d657 (dirty tree)
- Host: joe-mac-mini.local, macOS-27.0.1-arm64-arm-64bit, 10 CPUs, 16.0 GB
- Cache policy: warm (cold-cache support: partial: drops the mark, uncompressed and query caches; the guest page cache is kept)
- Rounds: 9 requested, 9 accepted, warm-up 1, seed 42, 2000 bootstrap resamples, 95% CI
- Per-query timeout 300.0s, host load ceiling 12.0 with 5 retries, throughput streams off
- Data: /Users/joe/Developer/benchmark_runs/duckdb-tuned-sf10-20261112/datagen/ssb_sf1; benchmark queries in the adapter's dialect, default substitution parameters
- Rule thresholds: min_rounds 7, aa_point_low 0.95, aa_point_high 1.05, load_settle_ratio 1.5, slow_query_ratio 1.5

## Arms

| Arm | Load | Session | Template | Engine | Load s | Settle s | Settled | Load+settle s | Notes |
|---|---|---|---|---|---|---|---|---|---|
| N | notuning | notuning | notuning | 25.8.33.6 | 10.0 | 2.0 (clickhouse_merge_settle) | yes | 12.0 | - |
| N2 | notuning | notuning | notuning | 25.8.33.6 | 10.0 | 2.2 (clickhouse_merge_settle) | yes | 12.2 | - |
| S | tuned | notuning | examples/tunings/clickhouse/ssb_tuned.yaml | 25.8.33.6 | 13.0 | 2.0 (clickhouse_merge_settle) | yes | 15.0 | - |
| T | tuned | tuned | examples/tunings/clickhouse/ssb_tuned.yaml | 25.8.33.6 | 14.1 | 2.0 (clickhouse_merge_settle) | yes | 16.1 | - |

Load time for S, T includes the adapter's own post-load merge wait.

## Container memory evidence

- Container ch-ssb via mocker 0.7.2; runtime container CLI version 1.1.0 (build: release, commit: unspeci)
- Guest memory.max: 5637144576
- OOM kills during the run: 0
- ClickHouse server ran in a Linux VM under Apple container, not under Linux Docker

## Calibration

- Source: in-run arm N2; passed: NO; G 1.0539 [0.9633, 1.1039]
- G 1.0539 is outside [0.95, 1.05]
- Noise: |G-1| 0.0539; per-query |ratio-1| median 0.146, max 0.315 (QQ1.2); median per-query CI half-width 0.259

## N2 vs N

- G over 13 queries passing in both arms: 1.0539 [0.9633, 1.1039]
- G with each failed query at 300.0s: 1.0539
- Failures: N none; N2 none
- Sum of medians (common set): N 2.279s, N2 2.349s
- Total elapsed over accepted rounds: N 21.406s, N2 21.341s
- Answer mismatches: none

| Q | N median ms | N2 median ms | Ratio | 95% CI | Rows read ratio | Peak memory ratio |
|---|---|---|---|---|---|---|
| Q1.1 | 59.6 | 45.8 | 0.769 | [0.643, 0.999] | 1.00 | 1.02 |
| Q1.2 | 47.9 | 62.9 | 1.315 | [0.839, 1.315] | 1.00 | 1.13 |
| Q1.3 | 42.9 | 50.9 | 1.187 | [0.800, 1.536] | 1.00 | 1.14 |
| Q2.1 | 76.1 | 90.3 | 1.186 | [0.859, 1.548] | 1.00 | 0.96 |
| Q2.2 | 66.2 | 59.9 | 0.905 | [0.660, 1.274] | 1.00 | 0.99 |
| Q2.3 | 56.0 | 66.9 | 1.196 | [1.009, 1.544] | 1.00 | 1.01 |
| Q3.1 | 284.8 | 326.3 | 1.146 | [0.970, 1.327] | 1.00 | 0.99 |
| Q3.2 | 199.3 | 234.7 | 1.178 | [0.731, 1.504] | 1.00 | 0.99 |
| Q3.3 | 198.7 | 206.2 | 1.038 | [0.714, 1.208] | 1.00 | 1.01 |
| Q3.4 | 203.0 | 212.2 | 1.045 | [0.877, 1.437] | 1.00 | 0.99 |
| Q4.1 | 133.1 | 127.2 | 0.955 | [0.766, 1.261] | 1.00 | 0.97 |
| Q4.2 | 520.2 | 455.2 | 0.875 | [0.719, 1.122] | 1.00 | 0.99 |
| Q4.3 | 391.3 | 410.6 | 1.049 | [0.767, 1.284] | 1.00 | 1.02 |

## S vs N

- G over 13 queries passing in both arms: 0.8188 [0.7552, 0.8455]
- G with each failed query at 300.0s: 0.8188
- Failures: N none; S none
- Sum of medians (common set): N 2.279s, S 1.615s
- Total elapsed over accepted rounds: N 21.406s, S 15.452s
- Answer mismatches: none
- Verdict: none
  - scope: SF1.0, engine {'N': '25.8.33.6', 'S': '25.8.33.6'}, memory.max 5637144576, 2026-11-12T20:46:03.932706+00:00
  - finding: CI upper bound 0.8455 is below 1.0
  - withheld because: uncalibrated: no passing A/A run for this platform, benchmark and scale

| Q | N median ms | S median ms | Ratio | 95% CI | Rows read ratio | Peak memory ratio |
|---|---|---|---|---|---|---|
| Q1.1 | 59.6 | 45.4 | 0.762 | [0.576, 0.877] | 1.00 | 0.68 |
| Q1.2 | 47.9 | 57.0 | 1.191 | [0.587, 1.399] | 1.00 | 0.58 |
| Q1.3 | 42.9 | 49.1 | 1.144 | [0.841, 1.279] | 1.00 | 0.58 |
| Q2.1 | 76.1 | 72.5 | 0.952 | [0.798, 1.138] | 1.00 | 0.99 |
| Q2.2 | 66.2 | 67.5 | 1.020 | [0.576, 1.219] | 1.00 | 0.87 |
| Q2.3 | 56.0 | 56.4 | 1.007 | [0.856, 1.391] | 1.00 | 0.78 |
| Q3.1 | 284.8 | 324.8 | 1.140 | [0.976, 1.236] | 1.00 | 1.00 |
| Q3.2 | 199.3 | 181.4 | 0.910 | [0.656, 1.100] | 1.00 | 1.00 |
| Q3.3 | 198.7 | 179.9 | 0.905 | [0.653, 1.153] | 1.00 | 1.00 |
| Q3.4 | 203.0 | 167.6 | 0.825 | [0.723, 1.240] | 1.00 | 0.99 |
| Q4.1 | 133.1 | 131.8 | 0.990 | [0.722, 1.393] | 1.00 | 1.02 |
| Q4.2 | 520.2 | 162.1 | 0.312 | [0.263, 0.437] | 1.00 | 0.97 |
| Q4.3 | 391.3 | 119.8 | 0.306 | [0.269, 0.405] | 1.00 | 0.99 |

## T vs N

- G over 13 queries passing in both arms: 0.8148 [0.7401, 0.8523]
- G with each failed query at 300.0s: 0.8148
- Failures: N none; T none
- Sum of medians (common set): N 2.279s, T 1.544s
- Total elapsed over accepted rounds: N 21.406s, T 14.332s
- Answer mismatches: none
- Verdict: none
  - scope: SF1.0, engine {'N': '25.8.33.6', 'T': '25.8.33.6'}, memory.max 5637144576, 2026-11-12T20:46:03.932706+00:00
  - finding: CI upper bound 0.8523 is below 1.0
  - withheld because: uncalibrated: no passing A/A run for this platform, benchmark and scale

| Q | N median ms | T median ms | Ratio | 95% CI | Rows read ratio | Peak memory ratio |
|---|---|---|---|---|---|---|
| Q1.1 | 59.6 | 55.2 | 0.926 | [0.664, 1.313] | 1.00 | 0.59 |
| Q1.2 | 47.9 | 64.0 | 1.337 | [0.711, 1.470] | 1.00 | 0.58 |
| Q1.3 | 42.9 | 46.1 | 1.075 | [0.902, 1.883] | 1.00 | 0.58 |
| Q2.1 | 76.1 | 80.7 | 1.061 | [0.845, 1.116] | 1.00 | 0.88 |
| Q2.2 | 66.2 | 66.4 | 1.003 | [0.708, 1.395] | 1.00 | 0.98 |
| Q2.3 | 56.0 | 60.9 | 1.089 | [0.852, 1.500] | 1.00 | 0.66 |
| Q3.1 | 284.8 | 287.0 | 1.008 | [0.819, 1.068] | 1.00 | 1.01 |
| Q3.2 | 199.3 | 160.7 | 0.806 | [0.613, 0.963] | 1.00 | 0.99 |
| Q3.3 | 198.7 | 153.7 | 0.773 | [0.615, 0.987] | 1.00 | 1.00 |
| Q3.4 | 203.0 | 166.6 | 0.821 | [0.641, 1.123] | 1.00 | 1.00 |
| Q4.1 | 133.1 | 119.7 | 0.899 | [0.679, 1.190] | 1.00 | 0.99 |
| Q4.2 | 520.2 | 156.3 | 0.300 | [0.188, 0.398] | 1.00 | 0.98 |
| Q4.3 | 391.3 | 127.1 | 0.325 | [0.283, 0.396] | 1.00 | 0.99 |

## Correctness

- Reference arm N; mismatching executions: 0

## Host load per round

| Phase | Round | Attempt | Load before | Load after | Accepted | Reason |
|---|---|---|---|---|---|---|
| warmup | 0 | 0 | - | - | yes |  |
| timed | 0 | 0 | 7.95 | 7.82 | yes |  |
| timed | 1 | 0 | 7.82 | 7.70 | yes |  |
| timed | 2 | 0 | 7.70 | 7.60 | yes |  |
| timed | 3 | 0 | 7.60 | 9.72 | yes |  |
| timed | 4 | 0 | 9.72 | 9.12 | yes |  |
| timed | 5 | 0 | 9.12 | 9.27 | yes |  |
| timed | 6 | 0 | 9.27 | 8.45 | yes |  |
| timed | 7 | 0 | 8.45 | 8.59 | yes |  |
| timed | 8 | 0 | 8.59 | 8.89 | yes |  |
