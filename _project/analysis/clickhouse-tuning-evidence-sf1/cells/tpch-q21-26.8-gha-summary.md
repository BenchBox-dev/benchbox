# Tuning evidence: clickhouse-server tpch SF1.0

- Status: completed
- Started 2026-11-25T05:10:36.872743+00:00, finished 2026-11-25T05:12:24.854102+00:00
- Harness version 1, commit 62a0c9b0f8dc0eaad44f0f9bcf94ee97e68cbc27
- Host: runnervm0hi8g, Linux-7.0.0-1020-azure-x86_64-with-glibc2.43, 4 CPUs, 15.6 GB
- Cache policy: warm (cold-cache support: partial: drops the mark, uncompressed and query caches; the guest page cache is kept)
- Rounds: 9 requested, 9 accepted, warm-up 1, seed 42, 2000 bootstrap resamples, 95% CI
- Per-query timeout 180.0s, host load ceiling 12.0 with 5 retries, throughput streams off
- Data: /home/runner/work/_temp/data/tpch_sf1; benchmark queries in the adapter's dialect, default substitution parameters
- Rule thresholds: min_rounds 7, aa_point_low 0.95, aa_point_high 1.05, load_settle_ratio 1.5, slow_query_ratio 1.5

## Arms

| Arm | Load | Session | Template | Engine | Load s | Settle s | Settled | Load+settle s | Notes |
|---|---|---|---|---|---|---|---|---|---|
| N | notuning | notuning | notuning | 26.8.46.6 | 12.1 | 2.0 (clickhouse_merge_settle) | yes | 14.1 | - |
| N2 | notuning | notuning | notuning | 26.8.46.6 | 11.1 | 2.0 (clickhouse_merge_settle) | yes | 13.2 | - |
| S | tuned | notuning | examples/tunings/clickhouse/tpch_tuned.yaml | 26.8.46.6 | 14.7 | 2.0 (clickhouse_merge_settle) | yes | 16.7 | - |
| T | tuned | tuned | examples/tunings/clickhouse/tpch_tuned.yaml | 26.8.46.6 | 13.7 | 2.0 (clickhouse_merge_settle) | yes | 15.7 | the tuned session runs Q21 with the shipped per-query join_algorithm = 'hash' setting |

Load time for S, T includes the adapter's own post-load merge wait.

## Container memory evidence

- Container ch via docker Docker version 29.4.2, build 055a478; runtime -
- Guest memory.max: 5637144576
- OOM kills during the run: 0

## Calibration

- Source: in-run arm N2; passed: yes; G 0.9748 [0.9410, 1.1350]
- Noise: |G-1| 0.0252; per-query |ratio-1| median 0.120, max 0.138 (Q6); median per-query CI half-width 0.198

## N2 vs N

- G over 2 queries passing in both arms: 0.9748 [0.9410, 1.1350]
- G with each failed query at 180.0s: 0.9748
- Failures: N none; N2 none
- Sum of medians (common set): N 0.162s, N2 0.170s
- Total elapsed over accepted rounds: N 1.502s, N2 1.578s
- Answer mismatches: none

| Q | N median ms | N2 median ms | Ratio | 95% CI | Rows read ratio | Peak memory ratio |
|---|---|---|---|---|---|---|
| 6 | 35.2 | 30.4 | 0.862 | [0.789, 1.264] | 1.00 | 1.00 |
| 21 | 126.8 | 139.8 | 1.103 | [0.894, 1.211] | 1.00 | 1.02 |

## S vs N

- G over 2 queries passing in both arms: 0.5491 [0.5344, 0.6775]
- G with each failed query at 180.0s: 0.5491
- Failures: N none; S none
- Sum of medians (common set): N 0.162s, S 0.146s
- Total elapsed over accepted rounds: N 1.502s, S 1.393s
- Answer mismatches: none
- Verdict: benefit
  - scope: SF1.0, engine {'N': '26.8.46.6', 'S': '26.8.46.6'}, memory.max 5637144576, 2026-11-25T05:12:24.854102+00:00
  - finding: CI upper bound 0.6775 is below 1.0

| Q | N median ms | S median ms | Ratio | 95% CI | Rows read ratio | Peak memory ratio |
|---|---|---|---|---|---|---|
| 6 | 35.2 | 9.9 | 0.281 | [0.265, 0.440] | 0.15 | 0.85 |
| 21 | 126.8 | 136.3 | 1.074 | [0.876, 1.155] | 1.00 | 0.95 |

## T vs N

- G over 2 queries passing in both arms: 0.5555 [0.5371, 0.6344]
- G with each failed query at 180.0s: 0.5555
- Failures: N none; T none
- Sum of medians (common set): N 0.162s, T 0.146s
- Total elapsed over accepted rounds: N 1.502s, T 1.383s
- Answer mismatches: none
- Verdict: benefit
  - scope: SF1.0, engine {'N': '26.8.46.6', 'T': '26.8.46.6'}, memory.max 5637144576, 2026-11-25T05:12:24.854102+00:00
  - finding: CI upper bound 0.6344 is below 1.0

| Q | N median ms | T median ms | Ratio | 95% CI | Rows read ratio | Peak memory ratio |
|---|---|---|---|---|---|---|
| 6 | 35.2 | 10.1 | 0.288 | [0.273, 0.403] | 0.15 | 0.85 |
| 21 | 126.8 | 136.0 | 1.073 | [0.870, 1.117] | 1.00 | 0.99 |

## Correctness

- Reference arm N; mismatching executions: 0

## Host load per round

| Phase | Round | Attempt | Load before | Load after | Accepted | Reason |
|---|---|---|---|---|---|---|
| warmup | 0 | 0 | - | - | yes |  |
| timed | 0 | 0 | 1.66 | 1.69 | yes |  |
| timed | 1 | 0 | 1.69 | 1.69 | yes |  |
| timed | 2 | 0 | 1.69 | 1.63 | yes |  |
| timed | 3 | 0 | 1.63 | 1.63 | yes |  |
| timed | 4 | 0 | 1.63 | 1.90 | yes |  |
| timed | 5 | 0 | 1.90 | 1.90 | yes |  |
| timed | 6 | 0 | 1.90 | 1.83 | yes |  |
| timed | 7 | 0 | 1.83 | 1.83 | yes |  |
| timed | 8 | 0 | 1.83 | 1.76 | yes |  |
