# Tuning evidence: clickhouse-server tpch SF1.0

- Status: completed
- Started 2026-11-25T05:10:39.741801+00:00, finished 2026-11-25T05:14:49.045614+00:00
- Harness version 1, commit 62a0c9b0f8dc0eaad44f0f9bcf94ee97e68cbc27
- Host: ip-10-0-140-211, Linux-7.0.0-1016-aws-x86_64-with-glibc2.43, 4 CPUs, 15.3 GB
- Cache policy: warm (cold-cache support: partial: drops the mark, uncompressed and query caches; the guest page cache is kept)
- Rounds: 9 requested, 9 accepted, warm-up 1, seed 42, 2000 bootstrap resamples, 95% CI
- Per-query timeout 300.0s, host load ceiling 12.0 with 5 retries, throughput streams off
- Data: /home/runner/work/_temp/data/tpch_sf1; benchmark queries in the adapter's dialect, default substitution parameters
- Rule thresholds: min_rounds 7, aa_point_low 0.95, aa_point_high 1.05, load_settle_ratio 1.5, slow_query_ratio 1.5

## Arms

| Arm | Load | Session | Template | Engine | Load s | Settle s | Settled | Load+settle s | Notes |
|---|---|---|---|---|---|---|---|---|---|
| N | notuning | notuning | notuning | 26.8.46.6 | 13.8 | 2.0 (clickhouse_merge_settle) | yes | 15.9 | - |
| N2 | notuning | notuning | notuning | 26.8.46.6 | 13.7 | 2.0 (clickhouse_merge_settle) | yes | 15.8 | - |
| S | tuned | notuning | examples/tunings/clickhouse/tpch_tuned.yaml | 26.8.46.6 | 15.9 | 2.0 (clickhouse_merge_settle) | yes | 17.9 | - |
| T | tuned | tuned | examples/tunings/clickhouse/tpch_tuned.yaml | 26.8.46.6 | 15.8 | 2.0 (clickhouse_merge_settle) | yes | 17.8 | the tuned session runs Q21 with the shipped per-query join_algorithm = 'hash' setting |

Load time for S, T includes the adapter's own post-load merge wait.

## Container memory evidence

- Container ch via docker Docker version 29.4.2, build 055a478; runtime -
- Guest memory.max: 5637144576
- OOM kills during the run: 0

## Calibration

- Source: in-run arm N2; passed: NO; G 1.0504 [0.9771, 1.1072]
- G 1.0504 is outside [0.95, 1.05]
- Noise: |G-1| 0.0504; per-query |ratio-1| median 0.092, max 0.270 (Q3); median per-query CI half-width 0.217

## N2 vs N

- G over 22 queries passing in both arms: 1.0504 [0.9771, 1.1072]
- G with each failed query at 300.0s: 1.0504
- Failures: N none; N2 none
- Sum of medians (common set): N 1.597s, N2 1.689s
- Total elapsed over accepted rounds: N 14.615s, N2 15.128s
- Answer mismatches: none

| Q | N median ms | N2 median ms | Ratio | 95% CI | Rows read ratio | Peak memory ratio |
|---|---|---|---|---|---|---|
| 1 | 128.2 | 123.3 | 0.962 | [0.763, 1.309] | 1.00 | 1.00 |
| 2 | 43.5 | 39.9 | 0.916 | [0.772, 1.166] | 1.00 | 1.07 |
| 3 | 61.0 | 77.5 | 1.270 | [0.972, 1.329] | 1.00 | 1.02 |
| 4 | 52.7 | 52.0 | 0.986 | [0.790, 1.225] | 1.00 | 0.95 |
| 5 | 67.6 | 82.2 | 1.215 | [0.983, 1.286] | 1.00 | 0.92 |
| 6 | 46.4 | 47.2 | 1.019 | [0.889, 1.363] | 1.00 | 1.00 |
| 7 | 74.8 | 74.2 | 0.992 | [0.835, 1.306] | 1.00 | 1.06 |
| 8 | 90.8 | 90.5 | 0.997 | [0.758, 1.155] | 1.00 | 1.00 |
| 9 | 120.2 | 110.6 | 0.920 | [0.756, 1.190] | 1.00 | 0.98 |
| 10 | 120.2 | 103.9 | 0.864 | [0.779, 1.272] | 1.00 | 1.04 |
| 11 | 23.2 | 21.6 | 0.931 | [0.803, 1.195] | 1.00 | 0.98 |
| 12 | 60.7 | 63.5 | 1.046 | [0.837, 1.302] | 1.00 | 1.00 |
| 13 | 83.7 | 94.7 | 1.131 | [0.791, 1.301] | 1.00 | 0.87 |
| 14 | 43.7 | 49.9 | 1.142 | [0.907, 1.245] | 1.00 | 0.92 |
| 15 | 73.5 | 80.7 | 1.097 | [0.926, 1.307] | 1.00 | 1.00 |
| 16 | 31.1 | 35.8 | 1.152 | [0.947, 1.318] | 1.00 | 1.00 |
| 17 | 70.7 | 64.6 | 0.913 | [0.771, 1.220] | 1.00 | 0.81 |
| 18 | 80.7 | 95.1 | 1.178 | [0.884, 1.352] | 1.00 | 0.99 |
| 19 | 98.8 | 112.8 | 1.142 | [0.895, 1.336] | 1.00 | 1.19 |
| 20 | 51.3 | 56.7 | 1.106 | [0.881, 1.270] | 1.00 | 1.00 |
| 21 | 151.3 | 188.6 | 1.246 | [1.027, 1.324] | 1.00 | 0.97 |
| 22 | 23.0 | 23.4 | 1.020 | [0.790, 1.235] | 1.00 | 0.64 |

## S vs N

- G over 22 queries passing in both arms: 0.7862 [0.7339, 0.8361]
- G with each failed query at 300.0s: 0.7862
- Failures: N none; S none
- Sum of medians (common set): N 1.597s, S 1.405s
- Total elapsed over accepted rounds: N 14.615s, S 12.762s
- Answer mismatches: none
- Verdict: none
  - scope: SF1.0, engine {'N': '26.8.46.6', 'S': '26.8.46.6'}, memory.max 5637144576, 2026-11-25T05:14:49.045614+00:00
  - finding: CI upper bound 0.8361 is below 1.0
  - withheld because: uncalibrated: no passing A/A run for this platform, benchmark and scale
- Named slow queries: Q18 1.65x (unproven)

| Q | N median ms | S median ms | Ratio | 95% CI | Rows read ratio | Peak memory ratio |
|---|---|---|---|---|---|---|
| 1 | 128.2 | 123.3 | 0.962 | [0.685, 1.208] | 0.99 | 1.00 |
| 2 | 43.5 | 39.3 | 0.902 | [0.749, 1.308] | 1.00 | 1.08 |
| 3 | 61.0 | 51.0 | 0.835 | [0.574, 1.013] | 0.54 | 0.99 |
| 4 | 52.7 | 39.3 | 0.745 | [0.660, 1.034] | 0.81 | 1.01 |
| 5 | 67.6 | 53.4 | 0.790 | [0.709, 0.924] | 0.84 | 1.30 |
| 6 | 46.4 | 11.6 | 0.249 | [0.235, 0.347] | 0.15 | 0.85 |
| 7 | 74.8 | 49.2 | 0.659 | [0.472, 0.768] | 0.46 | 0.97 |
| 8 | 90.8 | 99.3 | 1.094 | [0.757, 1.135] | 0.87 | 1.01 |
| 9 | 120.2 | 113.0 | 0.940 | [0.788, 1.163] | 1.00 | 0.97 |
| 10 | 120.2 | 88.3 | 0.734 | [0.679, 1.093] | 0.81 | 0.98 |
| 11 | 23.2 | 24.3 | 1.047 | [0.804, 1.299] | 1.00 | 0.98 |
| 12 | 60.7 | 37.2 | 0.613 | [0.468, 0.740] | 1.00 | 0.68 |
| 13 | 83.7 | 81.9 | 0.978 | [0.742, 1.273] | 1.00 | 0.87 |
| 14 | 43.7 | 16.3 | 0.373 | [0.307, 0.446] | 0.05 | 0.69 |
| 15 | 73.5 | 22.3 | 0.303 | [0.255, 0.315] | 0.04 | 0.49 |
| 16 | 31.1 | 32.5 | 1.048 | [0.827, 1.261] | 1.00 | 1.00 |
| 17 | 70.7 | 64.1 | 0.907 | [0.793, 1.125] | 1.00 | 0.81 |
| 18 | 80.7 | 132.9 | 1.646 | [1.187, 1.716] | 1.95 | 1.00 |
| 19 | 98.8 | 104.0 | 1.053 | [0.895, 1.324] | 1.00 | 1.23 |
| 20 | 51.3 | 33.4 | 0.651 | [0.622, 0.716] | 0.28 | 1.00 |
| 21 | 151.3 | 161.2 | 1.065 | [1.040, 1.120] | 1.00 | 0.97 |
| 22 | 23.0 | 27.0 | 1.175 | [0.781, 1.263] | 1.00 | 1.00 |

## T vs N

- G over 22 queries passing in both arms: 0.7759 [0.7206, 0.8303]
- G with each failed query at 300.0s: 0.7759
- Failures: N none; T none
- Sum of medians (common set): N 1.597s, T 1.402s
- Total elapsed over accepted rounds: N 14.615s, T 12.700s
- Answer mismatches: none
- Verdict: none
  - scope: SF1.0, engine {'N': '26.8.46.6', 'T': '26.8.46.6'}, memory.max 5637144576, 2026-11-25T05:14:49.045614+00:00
  - finding: CI upper bound 0.8303 is below 1.0
  - withheld because: uncalibrated: no passing A/A run for this platform, benchmark and scale
- Named slow queries: Q18 1.55x (unproven)

| Q | N median ms | T median ms | Ratio | 95% CI | Rows read ratio | Peak memory ratio |
|---|---|---|---|---|---|---|
| 1 | 128.2 | 111.0 | 0.866 | [0.703, 1.041] | 0.99 | 1.00 |
| 2 | 43.5 | 32.3 | 0.743 | [0.574, 1.011] | 1.00 | 0.18 |
| 3 | 61.0 | 50.1 | 0.821 | [0.632, 0.995] | 0.54 | 0.99 |
| 4 | 52.7 | 37.8 | 0.717 | [0.655, 0.900] | 0.81 | 1.10 |
| 5 | 67.6 | 61.1 | 0.903 | [0.759, 1.008] | 0.84 | 1.18 |
| 6 | 46.4 | 14.3 | 0.308 | [0.277, 0.406] | 0.15 | 0.85 |
| 7 | 74.8 | 39.5 | 0.529 | [0.454, 0.647] | 0.46 | 0.61 |
| 8 | 90.8 | 89.4 | 0.985 | [0.828, 1.144] | 0.87 | 1.07 |
| 9 | 120.2 | 146.8 | 1.221 | [0.775, 1.397] | 1.00 | 0.99 |
| 10 | 120.2 | 87.0 | 0.724 | [0.648, 1.079] | 0.81 | 0.87 |
| 11 | 23.2 | 20.1 | 0.867 | [0.788, 1.065] | 1.00 | 0.98 |
| 12 | 60.7 | 32.2 | 0.530 | [0.454, 0.717] | 1.00 | 0.68 |
| 13 | 83.7 | 74.7 | 0.892 | [0.756, 1.314] | 1.00 | 0.85 |
| 14 | 43.7 | 17.5 | 0.401 | [0.363, 0.433] | 0.05 | 0.69 |
| 15 | 73.5 | 22.0 | 0.300 | [0.222, 0.311] | 0.04 | 0.49 |
| 16 | 31.1 | 30.9 | 0.996 | [0.804, 1.120] | 1.00 | 1.00 |
| 17 | 70.7 | 73.8 | 1.043 | [0.812, 1.329] | 1.00 | 0.89 |
| 18 | 80.7 | 125.4 | 1.553 | [1.322, 1.742] | 1.95 | 1.01 |
| 19 | 98.8 | 102.0 | 1.032 | [0.797, 1.312] | 1.00 | 1.20 |
| 20 | 51.3 | 34.6 | 0.675 | [0.522, 0.753] | 0.28 | 1.00 |
| 21 | 151.3 | 170.6 | 1.128 | [1.029, 1.208] | 1.00 | 0.96 |
| 22 | 23.0 | 28.6 | 1.246 | [0.945, 1.334] | 1.00 | 0.94 |

## Correctness

- Reference arm N; mismatching executions: 0

## Host load per round

| Phase | Round | Attempt | Load before | Load after | Accepted | Reason |
|---|---|---|---|---|---|---|
| warmup | 0 | 0 | - | - | yes |  |
| timed | 0 | 0 | 2.12 | 2.37 | yes |  |
| timed | 1 | 0 | 2.37 | 2.34 | yes |  |
| timed | 2 | 0 | 2.34 | 2.96 | yes |  |
| timed | 3 | 0 | 2.96 | 3.13 | yes |  |
| timed | 4 | 0 | 3.13 | 3.25 | yes |  |
| timed | 5 | 0 | 3.25 | 2.90 | yes |  |
| timed | 6 | 0 | 2.66 | 2.81 | yes |  |
| timed | 7 | 0 | 2.81 | 3.60 | yes |  |
| timed | 8 | 0 | 3.60 | 3.49 | yes |  |
