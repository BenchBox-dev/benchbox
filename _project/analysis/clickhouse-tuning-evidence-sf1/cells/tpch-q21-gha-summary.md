# Tuning evidence: clickhouse-server tpch SF1.0

- Status: completed
- Started 2026-11-18T16:56:44.200368+00:00, finished 2026-11-18T17:03:40.728315+00:00
- Harness version 1, commit 3847e49e8ce16ee30b0b2da43a17fc788708ba22
- Host: runnervmccnms, Linux-7.0.0-1019-azure-x86_64-with-glibc2.43, 4 CPUs, 15.6 GB
- Cache policy: warm (cold-cache support: partial: drops the mark, uncompressed and query caches; the guest page cache is kept)
- Rounds: 9 requested, 9 accepted, warm-up 1, seed 42, 2000 bootstrap resamples, 95% CI
- Per-query timeout 180.0s, host load ceiling 12.0 with 5 retries, throughput streams off
- Data: /home/runner/work/_temp/data/tpch_sf1; benchmark queries in the adapter's dialect, default substitution parameters
- Rule thresholds: min_rounds 7, aa_point_low 0.95, aa_point_high 1.05, load_settle_ratio 1.5, slow_query_ratio 1.5

## Arms

| Arm | Load | Session | Template | Engine | Load s | Settle s | Settled | Load+settle s | Notes |
|---|---|---|---|---|---|---|---|---|---|
| N | notuning | notuning | notuning | 25.8.33.6 | 13.6 | 2.0 (clickhouse_merge_settle) | yes | 15.7 | - |
| N2 | notuning | notuning | notuning | 25.8.33.6 | 12.2 | 2.0 (clickhouse_merge_settle) | yes | 14.2 | - |
| S | tuned | notuning | examples/tunings/clickhouse/tpch_tuned.yaml | 25.8.33.6 | 16.0 | 2.0 (clickhouse_merge_settle) | yes | 18.1 | - |
| T | tuned | tuned | examples/tunings/clickhouse/tpch_tuned.yaml | 25.8.33.6 | 15.2 | 2.0 (clickhouse_merge_settle) | yes | 17.2 | the tuned session runs Q21 with the shipped per-query join_algorithm = 'hash' setting |

Load time for S, T includes the adapter's own post-load merge wait.

## Container memory evidence

- Container ch via docker Docker version 29.4.2, build 055a478; runtime -
- Guest memory.max: 5637144576
- OOM kills during the run: 0

## Calibration

- Source: in-run arm N2; passed: yes; G 0.9845 [0.9719, 1.0910]
- Noise: |G-1| 0.0155; per-query |ratio-1| median 0.015, max 0.025 (Q6); median per-query CI half-width 0.082

## N2 vs N

- G over 2 queries passing in both arms: 0.9845 [0.9719, 1.0910]
- G with each failed query at 180.0s: 0.9845
- Failures: N none; N2 none
- Sum of medians (common set): N 6.577s, N2 6.534s
- Total elapsed over accepted rounds: N 58.793s, N2 58.677s
- Answer mismatches: none

| Q | N median ms | N2 median ms | Ratio | 95% CI | Rows read ratio | Peak memory ratio |
|---|---|---|---|---|---|---|
| 6 | 58.5 | 57.1 | 0.975 | [0.970, 1.234] | 1.00 | 1.00 |
| 21 | 6518.1 | 6477.4 | 0.994 | [0.964, 1.027] | 1.00 | 1.00 |

## S vs N

- G over 2 queries passing in both arms: 0.6266 [0.6032, 0.7704]
- G with each failed query at 180.0s: 0.6266
- Failures: N none; S none
- Sum of medians (common set): N 6.577s, S 8.665s
- Total elapsed over accepted rounds: N 58.793s, S 77.450s
- Answer mismatches: none
- Verdict: benefit
  - scope: SF1.0, engine {'N': '25.8.33.6', 'S': '25.8.33.6'}, memory.max 5637144576, 2026-11-18T17:03:40.728315+00:00
  - finding: CI upper bound 0.7704 is below 1.0

| Q | N median ms | S median ms | Ratio | 95% CI | Rows read ratio | Peak memory ratio |
|---|---|---|---|---|---|---|
| 6 | 58.5 | 17.3 | 0.296 | [0.271, 0.465] | 0.15 | 0.53 |
| 21 | 6518.1 | 8648.1 | 1.327 | [1.288, 1.369] | 0.99 | 1.00 |

## T vs N

- G over 2 queries passing in both arms: 0.6293 [0.6023, 0.6533]
- G with each failed query at 180.0s: 0.6293
- Failures: N none; T none
- Sum of medians (common set): N 6.577s, T 8.688s
- Total elapsed over accepted rounds: N 58.793s, T 77.939s
- Answer mismatches: none
- Verdict: benefit
  - scope: SF1.0, engine {'N': '25.8.33.6', 'T': '25.8.33.6'}, memory.max 5637144576, 2026-11-18T17:03:40.728315+00:00
  - finding: CI upper bound 0.6533 is below 1.0

| Q | N median ms | T median ms | Ratio | 95% CI | Rows read ratio | Peak memory ratio |
|---|---|---|---|---|---|---|
| 6 | 58.5 | 17.4 | 0.298 | [0.277, 0.326] | 0.15 | 0.53 |
| 21 | 6518.1 | 8670.5 | 1.330 | [1.294, 1.383] | 0.99 | 1.00 |

## Correctness

- Reference arm N; mismatching executions: 0

## Host load per round

| Phase | Round | Attempt | Load before | Load after | Accepted | Reason |
|---|---|---|---|---|---|---|
| warmup | 0 | 0 | - | - | yes |  |
| timed | 0 | 0 | 2.06 | 2.74 | yes |  |
| timed | 1 | 0 | 2.74 | 2.52 | yes |  |
| timed | 2 | 0 | 2.52 | 2.87 | yes |  |
| timed | 3 | 0 | 2.87 | 3.09 | yes |  |
| timed | 4 | 0 | 3.09 | 3.41 | yes |  |
| timed | 5 | 0 | 3.41 | 3.21 | yes |  |
| timed | 6 | 0 | 3.21 | 3.94 | yes |  |
| timed | 7 | 0 | 3.94 | 3.54 | yes |  |
| timed | 8 | 0 | 3.33 | 3.08 | yes |  |
