# Tuning evidence: clickhouse-server tpch SF1.0

- Status: completed
- Started 2026-11-12T21:00:17.518792+00:00, finished 2026-11-12T21:07:22.909042+00:00
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
| N | notuning | notuning | notuning | 25.8.33.6 | 8.4 | 2.0 (clickhouse_merge_settle) | yes | 10.5 | - |
| N2 | notuning | notuning | notuning | 25.8.33.6 | 8.7 | 2.0 (clickhouse_merge_settle) | yes | 10.7 | - |
| S | tuned | notuning | examples/tunings/clickhouse/tpch_tuned.yaml | 25.8.33.6 | 11.9 | 2.0 (clickhouse_merge_settle) | yes | 13.9 | - |
| T | tuned | tuned | examples/tunings/clickhouse/tpch_tuned.yaml | 25.8.33.6 | 12.4 | 2.0 (clickhouse_merge_settle) | yes | 14.4 | the tuned session runs Q21 with the shipped per-query join_algorithm = 'hash' setting |

Load time for S, T includes the adapter's own post-load merge wait.

## Container memory evidence

- Container ch-h via mocker 0.7.2; runtime container CLI version 1.1.0 (build: release, commit: unspeci)
- Guest memory.max: 5637144576
- OOM kills during the run: 0
- ClickHouse server ran in a Linux VM under Apple container, not under Linux Docker

## Calibration

- Source: in-run arm N2; passed: yes; G 1.0066 [0.9592, 1.0464]
- Noise: |G-1| 0.0066; per-query |ratio-1| median 0.061, max 0.203 (Q5); median per-query CI half-width 0.241

## N2 vs N

- G over 21 queries passing in both arms: 1.0066 [0.9592, 1.0464]
- G with each failed query at 300.0s: 1.0063
- Failures: N ['21']; N2 ['21']
- Sum of medians (common set): N 2.493s, N2 2.528s
- Total elapsed over accepted rounds: N 69.450s, N2 73.241s
- Answer mismatches: none

| Q | N median ms | N2 median ms | Ratio | 95% CI | Rows read ratio | Peak memory ratio |
|---|---|---|---|---|---|---|
| 1 | 124.4 | 116.8 | 0.939 | [0.824, 1.178] | 1.00 | 1.00 |
| 2 | 53.6 | 54.1 | 1.010 | [0.850, 1.144] | 1.00 | 0.96 |
| 3 | 149.0 | 152.2 | 1.021 | [0.880, 1.184] | 1.00 | 1.00 |
| 4 | 92.1 | 79.9 | 0.867 | [0.620, 1.129] | 1.00 | 1.01 |
| 5 | 225.1 | 270.8 | 1.203 | [0.838, 1.391] | 1.00 | 0.99 |
| 6 | 42.0 | 44.4 | 1.057 | [0.636, 1.269] | 1.00 | 0.97 |
| 7 | 131.7 | 150.9 | 1.146 | [0.589, 1.431] | 1.00 | 1.00 |
| 8 | 327.4 | 322.3 | 0.984 | [0.815, 1.279] | 1.00 | 1.00 |
| 9 | 463.7 | 469.8 | 1.013 | [0.834, 1.101] | 1.00 | 1.00 |
| 10 | 141.3 | 113.8 | 0.805 | [0.641, 0.965] | 1.00 | 0.99 |
| 11 | 34.3 | 37.0 | 1.077 | [0.902, 1.651] | 1.00 | 0.93 |
| 12 | 63.9 | 52.6 | 0.823 | [0.598, 1.181] | 1.00 | 1.07 |
| 13 | 93.6 | 96.7 | 1.034 | [0.710, 1.289] | 1.00 | 1.03 |
| 14 | 47.0 | 52.1 | 1.110 | [0.975, 1.393] | 1.00 | 1.06 |
| 15 | 66.1 | 64.8 | 0.979 | [0.787, 1.353] | 1.00 | 1.00 |
| 16 | 29.6 | 34.0 | 1.150 | [0.758, 1.977] | 1.00 | 0.94 |
| 17 | 114.2 | 117.3 | 1.027 | [0.881, 1.185] | 1.00 | 1.00 |
| 18 | 88.1 | 86.9 | 0.986 | [0.741, 1.222] | 1.00 | 0.87 |
| 19 | 114.6 | 122.8 | 1.072 | [0.909, 1.488] | 1.00 | 1.03 |
| 20 | 46.4 | 48.4 | 1.044 | [0.894, 1.325] | 1.00 | 0.91 |
| 22 | 45.2 | 40.7 | 0.899 | [0.807, 1.131] | 1.00 | 1.03 |

## S vs N

- G over 21 queries passing in both arms: 0.8226 [0.7598, 0.8807]
- G with each failed query at 300.0s: 0.8299
- Failures: N ['21']; S ['21']
- Sum of medians (common set): N 2.493s, S 2.385s
- Total elapsed over accepted rounds: N 69.450s, S 74.969s
- Answer mismatches: none
- Verdict: benefit
  - scope: SF1.0, engine {'N': '25.8.33.6', 'S': '25.8.33.6'}, memory.max 5637144576, 2026-11-12T21:07:22.909042+00:00
  - finding: CI upper bound 0.8807 is below 1.0

| Q | N median ms | S median ms | Ratio | 95% CI | Rows read ratio | Peak memory ratio |
|---|---|---|---|---|---|---|
| 1 | 124.4 | 107.8 | 0.866 | [0.797, 1.044] | 0.99 | 0.91 |
| 2 | 53.6 | 60.8 | 1.135 | [0.860, 1.395] | 1.00 | 0.99 |
| 3 | 149.0 | 141.4 | 0.949 | [0.841, 1.112] | 0.54 | 1.00 |
| 4 | 92.1 | 58.9 | 0.640 | [0.473, 0.752] | 0.81 | 0.21 |
| 5 | 225.1 | 305.6 | 1.358 | [0.965, 1.634] | 0.84 | 0.97 |
| 6 | 42.0 | 23.6 | 0.562 | [0.264, 0.660] | 0.16 | 0.55 |
| 7 | 131.7 | 99.9 | 0.759 | [0.563, 1.010] | 0.46 | 1.01 |
| 8 | 327.4 | 319.6 | 0.976 | [0.839, 1.167] | 0.87 | 1.01 |
| 9 | 463.7 | 499.0 | 1.076 | [0.813, 1.177] | 1.00 | 1.01 |
| 10 | 141.3 | 121.4 | 0.859 | [0.671, 1.145] | 0.43 | 1.01 |
| 11 | 34.3 | 27.8 | 0.809 | [0.643, 1.064] | 1.00 | 1.01 |
| 12 | 63.9 | 33.3 | 0.522 | [0.457, 0.670] | 0.36 | 0.77 |
| 13 | 93.6 | 97.8 | 1.045 | [0.713, 1.860] | 1.00 | 0.98 |
| 14 | 47.0 | 17.1 | 0.363 | [0.278, 0.516] | 0.05 | 0.46 |
| 15 | 66.1 | 20.8 | 0.314 | [0.253, 0.528] | 0.05 | 0.66 |
| 16 | 29.6 | 39.2 | 1.325 | [0.887, 1.555] | 1.00 | 1.01 |
| 17 | 114.2 | 112.0 | 0.980 | [0.867, 1.165] | 1.00 | 1.00 |
| 18 | 88.1 | 103.8 | 1.179 | [0.877, 1.437] | 1.94 | 1.18 |
| 19 | 114.6 | 117.1 | 1.022 | [0.974, 1.449] | 1.00 | 0.97 |
| 20 | 46.4 | 28.2 | 0.608 | [0.536, 0.833] | 0.28 | 0.48 |
| 22 | 45.2 | 50.1 | 1.109 | [0.766, 1.396] | 1.00 | 0.96 |

## T vs N

- G over 21 queries passing in both arms: 0.8230 [0.7803, 0.8777]
- G with each failed query at 300.0s: 0.8304
- Failures: N ['21']; T ['21']
- Sum of medians (common set): N 2.493s, T 2.299s
- Total elapsed over accepted rounds: N 69.450s, T 70.334s
- Answer mismatches: none
- Verdict: benefit
  - scope: SF1.0, engine {'N': '25.8.33.6', 'T': '25.8.33.6'}, memory.max 5637144576, 2026-11-12T21:07:22.909042+00:00
  - finding: CI upper bound 0.8777 is below 1.0

| Q | N median ms | T median ms | Ratio | 95% CI | Rows read ratio | Peak memory ratio |
|---|---|---|---|---|---|---|
| 1 | 124.4 | 129.6 | 1.042 | [0.715, 1.228] | 0.99 | 0.91 |
| 2 | 53.6 | 55.8 | 1.041 | [0.824, 1.219] | 1.00 | 0.95 |
| 3 | 149.0 | 144.9 | 0.973 | [0.820, 1.168] | 0.54 | 0.99 |
| 4 | 92.1 | 68.5 | 0.744 | [0.456, 0.895] | 0.81 | 0.22 |
| 5 | 225.1 | 312.8 | 1.390 | [1.085, 1.741] | 0.84 | 0.97 |
| 6 | 42.0 | 17.1 | 0.406 | [0.274, 0.635] | 0.16 | 0.54 |
| 7 | 131.7 | 109.0 | 0.828 | [0.673, 1.135] | 0.46 | 1.02 |
| 8 | 327.4 | 244.1 | 0.746 | [0.638, 0.907] | 0.87 | 1.00 |
| 9 | 463.7 | 421.0 | 0.908 | [0.730, 1.076] | 1.00 | 1.00 |
| 10 | 141.3 | 146.8 | 1.039 | [0.717, 1.259] | 0.43 | 1.00 |
| 11 | 34.3 | 33.3 | 0.971 | [0.727, 1.501] | 1.00 | 1.16 |
| 12 | 63.9 | 37.9 | 0.593 | [0.403, 0.951] | 0.36 | 0.80 |
| 13 | 93.6 | 90.6 | 0.968 | [0.756, 1.376] | 1.00 | 1.00 |
| 14 | 47.0 | 16.9 | 0.361 | [0.267, 0.493] | 0.05 | 0.43 |
| 15 | 66.1 | 20.8 | 0.314 | [0.243, 0.537] | 0.05 | 0.66 |
| 16 | 29.6 | 43.9 | 1.485 | [1.097, 2.064] | 1.00 | 1.04 |
| 17 | 114.2 | 115.7 | 1.013 | [0.928, 1.223] | 1.00 | 0.99 |
| 18 | 88.1 | 109.9 | 1.248 | [0.935, 1.687] | 1.94 | 0.99 |
| 19 | 114.6 | 111.1 | 0.970 | [0.909, 1.449] | 1.00 | 1.00 |
| 20 | 46.4 | 30.0 | 0.648 | [0.510, 0.761] | 0.28 | 0.49 |
| 22 | 45.2 | 39.4 | 0.871 | [0.819, 1.662] | 1.00 | 0.95 |

## Correctness

- Reference arm N; mismatching executions: 0

## Host load per round

| Phase | Round | Attempt | Load before | Load after | Accepted | Reason |
|---|---|---|---|---|---|---|
| warmup | 0 | 0 | - | - | yes |  |
| timed | 0 | 0 | 7.03 | 6.88 | yes |  |
| timed | 1 | 0 | 6.88 | 7.04 | yes |  |
| timed | 2 | 0 | 6.64 | 6.21 | yes |  |
| timed | 3 | 0 | 6.21 | 5.89 | yes |  |
| timed | 4 | 0 | 5.89 | 6.37 | yes |  |
| timed | 5 | 0 | 6.37 | 6.24 | yes |  |
| timed | 6 | 0 | 6.24 | 5.88 | yes |  |
| timed | 7 | 0 | 5.88 | 6.01 | yes |  |
| timed | 8 | 0 | 6.01 | 5.87 | yes |  |
