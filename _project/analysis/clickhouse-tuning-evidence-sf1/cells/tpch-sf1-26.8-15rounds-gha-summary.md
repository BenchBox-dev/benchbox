# Tuning evidence: clickhouse-server tpch SF1.0

- Status: completed
- Started 2026-11-25T05:23:51.628537+00:00, finished 2026-11-25T05:29:51.693416+00:00
- Harness version 1, commit 62a0c9b0f8dc0eaad44f0f9bcf94ee97e68cbc27
- Host: runnervm0hi8g, Linux-7.0.0-1020-azure-x86_64-with-glibc2.43, 4 CPUs, 15.6 GB
- Cache policy: warm (cold-cache support: partial: drops the mark, uncompressed and query caches; the guest page cache is kept)
- Rounds: 15 requested, 15 accepted, warm-up 1, seed 42, 2000 bootstrap resamples, 95% CI
- Per-query timeout 300.0s, host load ceiling 12.0 with 5 retries, throughput streams off
- Data: /home/runner/work/_temp/data/tpch_sf1; benchmark queries in the adapter's dialect, default substitution parameters
- Rule thresholds: min_rounds 7, aa_point_low 0.95, aa_point_high 1.05, load_settle_ratio 1.5, slow_query_ratio 1.5

## Arms

| Arm | Load | Session | Template | Engine | Load s | Settle s | Settled | Load+settle s | Notes |
|---|---|---|---|---|---|---|---|---|---|
| N | notuning | notuning | notuning | 26.8.46.6 | 13.3 | 2.0 (clickhouse_merge_settle) | yes | 15.3 | - |
| N2 | notuning | notuning | notuning | 26.8.46.6 | 12.6 | 2.0 (clickhouse_merge_settle) | yes | 14.6 | - |
| S | tuned | notuning | examples/tunings/clickhouse/tpch_tuned.yaml | 26.8.46.6 | 15.8 | 2.0 (clickhouse_merge_settle) | yes | 17.8 | - |
| T | tuned | tuned | examples/tunings/clickhouse/tpch_tuned.yaml | 26.8.46.6 | 14.9 | 2.0 (clickhouse_merge_settle) | yes | 16.9 | the tuned session runs Q21 with the shipped per-query join_algorithm = 'hash' setting |

Load time for S, T includes the adapter's own post-load merge wait.

## Container memory evidence

- Container ch via docker Docker version 29.4.2, build 055a478; runtime -
- Guest memory.max: 5637144576
- OOM kills during the run: 0

## Calibration

- Source: in-run arm N2; passed: yes; G 1.0253 [0.9891, 1.0665]
- Noise: |G-1| 0.0253; per-query |ratio-1| median 0.044, max 0.168 (Q18); median per-query CI half-width 0.138

## N2 vs N

- G over 22 queries passing in both arms: 1.0253 [0.9891, 1.0665]
- G with each failed query at 300.0s: 1.0253
- Failures: N none; N2 none
- Sum of medians (common set): N 1.760s, N2 1.811s
- Total elapsed over accepted rounds: N 26.416s, N2 27.092s
- Answer mismatches: none

| Q | N median ms | N2 median ms | Ratio | 95% CI | Rows read ratio | Peak memory ratio |
|---|---|---|---|---|---|---|
| 1 | 156.1 | 141.1 | 0.904 | [0.873, 1.128] | 1.00 | 1.00 |
| 2 | 48.0 | 45.9 | 0.957 | [0.831, 1.175] | 1.00 | 1.01 |
| 3 | 77.6 | 86.8 | 1.118 | [1.016, 1.160] | 1.00 | 1.00 |
| 4 | 54.7 | 56.3 | 1.029 | [0.909, 1.125] | 1.00 | 1.09 |
| 5 | 80.2 | 88.4 | 1.103 | [0.994, 1.224] | 1.00 | 0.99 |
| 6 | 53.2 | 58.1 | 1.091 | [0.939, 1.176] | 1.00 | 1.00 |
| 7 | 78.5 | 79.4 | 1.012 | [0.874, 1.207] | 1.00 | 1.01 |
| 8 | 91.6 | 89.9 | 0.981 | [0.887, 1.078] | 1.00 | 0.99 |
| 9 | 128.9 | 126.9 | 0.985 | [0.861, 1.019] | 1.00 | 1.02 |
| 10 | 117.6 | 121.2 | 1.031 | [0.914, 1.247] | 1.00 | 1.00 |
| 11 | 24.8 | 24.1 | 0.974 | [0.844, 1.199] | 1.00 | 1.00 |
| 12 | 64.3 | 68.3 | 1.062 | [0.918, 1.253] | 1.00 | 1.00 |
| 13 | 85.0 | 92.9 | 1.093 | [0.964, 1.147] | 1.00 | 0.94 |
| 14 | 48.7 | 42.4 | 0.870 | [0.840, 1.143] | 1.00 | 0.99 |
| 15 | 83.9 | 80.7 | 0.963 | [0.855, 1.181] | 1.00 | 1.00 |
| 16 | 34.6 | 33.4 | 0.965 | [0.860, 1.196] | 1.00 | 1.00 |
| 17 | 67.7 | 68.5 | 1.013 | [0.881, 1.109] | 1.00 | 1.00 |
| 18 | 96.6 | 112.8 | 1.168 | [0.953, 1.321] | 1.00 | 1.02 |
| 19 | 103.2 | 115.5 | 1.119 | [0.920, 1.213] | 1.00 | 0.93 |
| 20 | 59.9 | 65.7 | 1.098 | [0.876, 1.155] | 1.00 | 1.00 |
| 21 | 179.1 | 185.5 | 1.036 | [1.005, 1.148] | 1.00 | 1.05 |
| 22 | 25.7 | 26.9 | 1.044 | [0.881, 1.156] | 1.00 | 1.86 |

## S vs N

- G over 22 queries passing in both arms: 0.7660 [0.7375, 0.7924]
- G with each failed query at 300.0s: 0.7660
- Failures: N none; S none
- Sum of medians (common set): N 1.760s, S 1.505s
- Total elapsed over accepted rounds: N 26.416s, S 22.728s
- Answer mismatches: none
- Verdict: benefit
  - scope: SF1.0, engine {'N': '26.8.46.6', 'S': '26.8.46.6'}, memory.max 5637144576, 2026-11-25T05:29:51.693416+00:00
  - finding: CI upper bound 0.7924 is below 1.0

| Q | N median ms | S median ms | Ratio | 95% CI | Rows read ratio | Peak memory ratio |
|---|---|---|---|---|---|---|
| 1 | 156.1 | 123.6 | 0.792 | [0.758, 1.001] | 0.99 | 1.00 |
| 2 | 48.0 | 41.5 | 0.865 | [0.799, 1.073] | 1.00 | 1.01 |
| 3 | 77.6 | 58.1 | 0.749 | [0.625, 0.780] | 0.54 | 1.00 |
| 4 | 54.7 | 49.7 | 0.908 | [0.796, 1.041] | 0.81 | 1.21 |
| 5 | 80.2 | 56.8 | 0.708 | [0.674, 0.804] | 0.84 | 0.92 |
| 6 | 53.2 | 12.6 | 0.236 | [0.219, 0.295] | 0.15 | 0.85 |
| 7 | 78.5 | 49.0 | 0.624 | [0.511, 0.680] | 0.46 | 0.88 |
| 8 | 91.6 | 94.2 | 1.028 | [0.864, 1.079] | 0.87 | 1.01 |
| 9 | 128.9 | 135.7 | 1.053 | [0.921, 1.230] | 1.00 | 0.99 |
| 10 | 117.6 | 90.9 | 0.773 | [0.738, 0.927] | 0.81 | 0.93 |
| 11 | 24.8 | 26.4 | 1.067 | [0.967, 1.211] | 1.00 | 1.00 |
| 12 | 64.3 | 39.0 | 0.607 | [0.498, 0.679] | 1.00 | 0.58 |
| 13 | 85.0 | 92.8 | 1.091 | [0.897, 1.148] | 1.00 | 1.04 |
| 14 | 48.7 | 17.0 | 0.349 | [0.333, 0.401] | 0.05 | 0.81 |
| 15 | 83.9 | 21.3 | 0.254 | [0.223, 0.286] | 0.04 | 0.48 |
| 16 | 34.6 | 35.3 | 1.019 | [0.897, 1.151] | 1.00 | 1.00 |
| 17 | 67.7 | 66.8 | 0.987 | [0.887, 1.121] | 1.00 | 1.03 |
| 18 | 96.6 | 126.4 | 1.309 | [1.158, 1.633] | 1.95 | 1.01 |
| 19 | 103.2 | 108.0 | 1.047 | [0.881, 1.221] | 1.00 | 0.71 |
| 20 | 59.9 | 39.9 | 0.667 | [0.537, 0.702] | 0.28 | 1.00 |
| 21 | 179.1 | 191.1 | 1.067 | [1.045, 1.171] | 1.00 | 0.99 |
| 22 | 25.7 | 29.2 | 1.134 | [0.952, 1.191] | 1.00 | 1.49 |

## T vs N

- G over 22 queries passing in both arms: 0.7556 [0.7276, 0.7793]
- G with each failed query at 300.0s: 0.7556
- Failures: N none; T none
- Sum of medians (common set): N 1.760s, T 1.517s
- Total elapsed over accepted rounds: N 26.416s, T 22.568s
- Answer mismatches: none
- Verdict: benefit
  - scope: SF1.0, engine {'N': '26.8.46.6', 'T': '26.8.46.6'}, memory.max 5637144576, 2026-11-25T05:29:51.693416+00:00
  - finding: CI upper bound 0.7793 is below 1.0

| Q | N median ms | T median ms | Ratio | 95% CI | Rows read ratio | Peak memory ratio |
|---|---|---|---|---|---|---|
| 1 | 156.1 | 126.2 | 0.809 | [0.767, 0.930] | 0.99 | 1.00 |
| 2 | 48.0 | 31.2 | 0.649 | [0.633, 0.864] | 1.00 | 0.19 |
| 3 | 77.6 | 56.8 | 0.733 | [0.650, 0.780] | 0.54 | 1.00 |
| 4 | 54.7 | 48.9 | 0.895 | [0.808, 1.056] | 0.81 | 1.21 |
| 5 | 80.2 | 61.7 | 0.770 | [0.704, 0.855] | 0.84 | 0.96 |
| 6 | 53.2 | 14.5 | 0.272 | [0.236, 0.306] | 0.15 | 0.85 |
| 7 | 78.5 | 43.3 | 0.552 | [0.449, 0.643] | 0.46 | 0.58 |
| 8 | 91.6 | 93.2 | 1.018 | [0.864, 1.091] | 0.87 | 1.01 |
| 9 | 128.9 | 154.7 | 1.200 | [0.920, 1.257] | 1.00 | 1.02 |
| 10 | 117.6 | 96.9 | 0.824 | [0.734, 0.994] | 0.81 | 0.83 |
| 11 | 24.8 | 21.8 | 0.879 | [0.802, 1.107] | 1.00 | 1.00 |
| 12 | 64.3 | 39.1 | 0.608 | [0.500, 0.690] | 1.00 | 0.75 |
| 13 | 85.0 | 92.4 | 1.087 | [0.831, 1.158] | 1.00 | 0.99 |
| 14 | 48.7 | 17.0 | 0.350 | [0.334, 0.409] | 0.05 | 0.84 |
| 15 | 83.9 | 20.9 | 0.249 | [0.219, 0.283] | 0.04 | 0.48 |
| 16 | 34.6 | 33.4 | 0.966 | [0.861, 1.044] | 1.00 | 1.00 |
| 17 | 67.7 | 76.3 | 1.127 | [0.909, 1.171] | 1.00 | 1.15 |
| 18 | 96.6 | 124.1 | 1.285 | [1.177, 1.449] | 1.95 | 1.03 |
| 19 | 103.2 | 108.1 | 1.048 | [0.879, 1.191] | 1.00 | 0.83 |
| 20 | 59.9 | 35.2 | 0.588 | [0.496, 0.671] | 0.28 | 1.00 |
| 21 | 179.1 | 192.6 | 1.076 | [1.030, 1.199] | 1.00 | 0.97 |
| 22 | 25.7 | 28.7 | 1.115 | [0.988, 1.245] | 1.00 | 1.14 |

## Correctness

- Reference arm N; mismatching executions: 0

## Host load per round

| Phase | Round | Attempt | Load before | Load after | Accepted | Reason |
|---|---|---|---|---|---|---|
| warmup | 0 | 0 | - | - | yes |  |
| timed | 0 | 0 | 2.60 | 2.40 | yes |  |
| timed | 1 | 0 | 2.21 | 2.85 | yes |  |
| timed | 2 | 0 | 2.85 | 2.82 | yes |  |
| timed | 3 | 0 | 2.82 | 2.33 | yes |  |
| timed | 4 | 0 | 2.33 | 2.65 | yes |  |
| timed | 5 | 0 | 2.44 | 2.36 | yes |  |
| timed | 6 | 0 | 2.36 | 2.92 | yes |  |
| timed | 7 | 0 | 2.92 | 2.78 | yes |  |
| timed | 8 | 0 | 2.56 | 2.50 | yes |  |
| timed | 9 | 0 | 2.50 | 2.32 | yes |  |
| timed | 10 | 0 | 2.14 | 2.04 | yes |  |
| timed | 11 | 0 | 2.04 | 2.17 | yes |  |
| timed | 12 | 0 | 2.17 | 3.94 | yes |  |
| timed | 13 | 0 | 3.94 | 4.09 | yes |  |
| timed | 14 | 0 | 4.09 | 3.77 | yes |  |
