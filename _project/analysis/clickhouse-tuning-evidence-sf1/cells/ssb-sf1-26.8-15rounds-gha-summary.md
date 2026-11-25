# Tuning evidence: clickhouse-server ssb SF1.0

- Status: completed
- Started 2026-11-25T05:10:20.947589+00:00, finished 2026-11-25T05:15:13.726559+00:00
- Harness version 1, commit 62a0c9b0f8dc0eaad44f0f9bcf94ee97e68cbc27
- Host: runnervm0hi8g, Linux-7.0.0-1020-azure-x86_64-with-glibc2.43, 4 CPUs, 15.6 GB
- Cache policy: warm (cold-cache support: partial: drops the mark, uncompressed and query caches; the guest page cache is kept)
- Rounds: 15 requested, 15 accepted, warm-up 1, seed 42, 2000 bootstrap resamples, 95% CI
- Per-query timeout 300.0s, host load ceiling 12.0 with 5 retries, throughput streams off
- Data: /home/runner/work/_temp/data/ssb_sf1; benchmark queries in the adapter's dialect, default substitution parameters
- Rule thresholds: min_rounds 7, aa_point_low 0.95, aa_point_high 1.05, load_settle_ratio 1.5, slow_query_ratio 1.5

## Arms

| Arm | Load | Session | Template | Engine | Load s | Settle s | Settled | Load+settle s | Notes |
|---|---|---|---|---|---|---|---|---|---|
| N | notuning | notuning | notuning | 26.8.46.6 | 10.3 | 2.0 (clickhouse_merge_settle) | yes | 12.3 | - |
| N2 | notuning | notuning | notuning | 26.8.46.6 | 10.0 | 2.0 (clickhouse_merge_settle) | yes | 12.0 | - |
| S | tuned | notuning | examples/tunings/clickhouse/ssb_tuned.yaml | 26.8.46.6 | 11.2 | 2.0 (clickhouse_merge_settle) | yes | 13.2 | - |
| T | tuned | tuned | examples/tunings/clickhouse/ssb_tuned.yaml | 26.8.46.6 | 11.3 | 2.0 (clickhouse_merge_settle) | yes | 13.4 | - |

Load time for S, T includes the adapter's own post-load merge wait.

## Container memory evidence

- Container ch via docker Docker version 29.4.2, build 055a478; runtime -
- Guest memory.max: 5637144576
- OOM kills during the run: 0

## Calibration

- Source: in-run arm N2; passed: yes; G 1.0085 [0.9386, 1.0512]
- Noise: |G-1| 0.0085; per-query |ratio-1| median 0.022, max 0.134 (QQ3.3); median per-query CI half-width 0.174

## N2 vs N

- G over 13 queries passing in both arms: 1.0085 [0.9386, 1.0512]
- G with each failed query at 300.0s: 1.0085
- Failures: N none; N2 none
- Sum of medians (common set): N 0.890s, N2 0.892s
- Total elapsed over accepted rounds: N 13.143s, N2 13.261s
- Answer mismatches: none

| Q | N median ms | N2 median ms | Ratio | 95% CI | Rows read ratio | Peak memory ratio |
|---|---|---|---|---|---|---|
| Q1.1 | 60.9 | 59.8 | 0.982 | [0.879, 1.069] | 1.00 | 0.95 |
| Q1.2 | 58.1 | 56.8 | 0.978 | [0.784, 1.032] | 1.00 | 0.99 |
| Q1.3 | 49.7 | 50.7 | 1.019 | [0.877, 1.224] | 1.00 | 1.00 |
| Q2.1 | 104.4 | 100.8 | 0.966 | [0.792, 1.284] | 1.00 | 1.16 |
| Q2.2 | 88.5 | 90.5 | 1.022 | [0.806, 1.288] | 1.00 | 1.51 |
| Q2.3 | 74.2 | 74.3 | 1.001 | [0.758, 1.095] | 1.00 | 0.88 |
| Q3.1 | 62.8 | 63.1 | 1.004 | [0.871, 1.257] | 1.00 | 1.00 |
| Q3.2 | 47.4 | 50.2 | 1.060 | [0.833, 1.161] | 1.00 | 1.33 |
| Q3.3 | 35.7 | 40.5 | 1.134 | [0.976, 1.275] | 1.00 | 1.48 |
| Q3.4 | 34.6 | 33.8 | 0.976 | [0.808, 1.207] | 1.00 | 1.05 |
| Q4.1 | 75.5 | 73.4 | 0.972 | [0.819, 1.240] | 1.00 | 1.02 |
| Q4.2 | 71.6 | 73.2 | 1.022 | [0.814, 1.207] | 1.00 | 1.09 |
| Q4.3 | 126.3 | 124.7 | 0.987 | [0.922, 1.016] | 1.00 | 0.77 |

## S vs N

- G over 13 queries passing in both arms: 0.8901 [0.8508, 0.9184]
- G with each failed query at 300.0s: 0.8901
- Failures: N none; S none
- Sum of medians (common set): N 0.890s, S 0.791s
- Total elapsed over accepted rounds: N 13.143s, S 11.767s
- Answer mismatches: none
- Verdict: benefit
  - scope: SF1.0, engine {'N': '26.8.46.6', 'S': '26.8.46.6'}, memory.max 5637144576, 2026-11-25T05:15:13.726559+00:00
  - finding: CI upper bound 0.9184 is below 1.0

| Q | N median ms | S median ms | Ratio | 95% CI | Rows read ratio | Peak memory ratio |
|---|---|---|---|---|---|---|
| Q1.1 | 60.9 | 45.4 | 0.745 | [0.675, 0.897] | 1.00 | 1.35 |
| Q1.2 | 58.1 | 46.7 | 0.803 | [0.667, 0.841] | 1.00 | 0.58 |
| Q1.3 | 49.7 | 44.0 | 0.885 | [0.673, 1.030] | 1.00 | 0.20 |
| Q2.1 | 104.4 | 95.2 | 0.912 | [0.727, 1.183] | 1.00 | 0.86 |
| Q2.2 | 88.5 | 87.1 | 0.985 | [0.899, 1.203] | 1.00 | 1.41 |
| Q2.3 | 74.2 | 68.3 | 0.920 | [0.830, 1.002] | 1.00 | 1.10 |
| Q3.1 | 62.8 | 52.4 | 0.834 | [0.706, 1.084] | 1.00 | 0.75 |
| Q3.2 | 47.4 | 37.7 | 0.795 | [0.731, 0.935] | 1.00 | 1.32 |
| Q3.3 | 35.7 | 36.6 | 1.026 | [0.880, 1.142] | 1.00 | 1.25 |
| Q3.4 | 34.6 | 36.6 | 1.057 | [0.888, 1.165] | 1.00 | 1.00 |
| Q4.1 | 75.5 | 70.4 | 0.932 | [0.809, 1.104] | 1.00 | 0.92 |
| Q4.2 | 71.6 | 63.5 | 0.887 | [0.733, 1.046] | 1.00 | 1.01 |
| Q4.3 | 126.3 | 107.2 | 0.848 | [0.720, 0.945] | 1.00 | 0.75 |

## T vs N

- G over 13 queries passing in both arms: 0.8906 [0.8389, 0.9247]
- G with each failed query at 300.0s: 0.8906
- Failures: N none; T none
- Sum of medians (common set): N 0.890s, T 0.790s
- Total elapsed over accepted rounds: N 13.143s, T 11.751s
- Answer mismatches: none
- Verdict: benefit
  - scope: SF1.0, engine {'N': '26.8.46.6', 'T': '26.8.46.6'}, memory.max 5637144576, 2026-11-25T05:15:13.726559+00:00
  - finding: CI upper bound 0.9247 is below 1.0

| Q | N median ms | T median ms | Ratio | 95% CI | Rows read ratio | Peak memory ratio |
|---|---|---|---|---|---|---|
| Q1.1 | 60.9 | 49.2 | 0.807 | [0.653, 0.906] | 1.00 | 1.00 |
| Q1.2 | 58.1 | 42.0 | 0.723 | [0.638, 0.828] | 1.00 | 0.20 |
| Q1.3 | 49.7 | 46.2 | 0.930 | [0.798, 1.048] | 1.00 | 0.20 |
| Q2.1 | 104.4 | 101.6 | 0.973 | [0.878, 1.216] | 1.00 | 0.99 |
| Q2.2 | 88.5 | 80.1 | 0.905 | [0.821, 1.096] | 1.00 | 1.15 |
| Q2.3 | 74.2 | 66.0 | 0.890 | [0.751, 0.955] | 1.00 | 0.84 |
| Q3.1 | 62.8 | 55.2 | 0.879 | [0.774, 1.088] | 1.00 | 0.70 |
| Q3.2 | 47.4 | 41.0 | 0.865 | [0.684, 0.961] | 1.00 | 0.90 |
| Q3.3 | 35.7 | 37.3 | 1.045 | [0.907, 1.168] | 1.00 | 1.24 |
| Q3.4 | 34.6 | 36.1 | 1.042 | [0.750, 1.124] | 1.00 | 0.97 |
| Q4.1 | 75.5 | 62.7 | 0.831 | [0.759, 1.099] | 1.00 | 0.96 |
| Q4.2 | 71.6 | 62.2 | 0.869 | [0.795, 1.024] | 1.00 | 0.98 |
| Q4.3 | 126.3 | 110.2 | 0.872 | [0.729, 1.007] | 1.00 | 0.97 |

## Correctness

- Reference arm N; mismatching executions: 0

## Host load per round

| Phase | Round | Attempt | Load before | Load after | Accepted | Reason |
|---|---|---|---|---|---|---|
| warmup | 0 | 0 | - | - | yes |  |
| timed | 0 | 0 | 2.14 | 2.44 | yes |  |
| timed | 1 | 0 | 2.65 | 2.68 | yes |  |
| timed | 2 | 0 | 2.46 | 2.59 | yes |  |
| timed | 3 | 0 | 2.59 | 2.66 | yes |  |
| timed | 4 | 0 | 2.66 | 2.57 | yes |  |
| timed | 5 | 0 | 2.57 | 2.41 | yes |  |
| timed | 6 | 0 | 2.41 | 2.49 | yes |  |
| timed | 7 | 0 | 2.49 | 2.58 | yes |  |
| timed | 8 | 0 | 2.58 | 2.45 | yes |  |
| timed | 9 | 0 | 2.25 | 2.15 | yes |  |
| timed | 10 | 0 | 2.06 | 2.14 | yes |  |
| timed | 11 | 0 | 2.14 | 2.11 | yes |  |
| timed | 12 | 0 | 2.11 | 2.34 | yes |  |
| timed | 13 | 0 | 2.34 | 2.29 | yes |  |
| timed | 14 | 0 | 2.29 | 2.41 | yes |  |
