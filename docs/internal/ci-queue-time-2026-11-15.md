# CI queue time versus run time, 2026-11-15

Purpose: find where PR CI wall time goes after the 2026-11-12 rounds that took
60 to 90 minutes, and whether the cause is runner capacity or workflow ordering.

## Method

For each completed (success or failure) `ci.yml` run triggered by
`pull_request`, every job's queue time is `started_at - created_at` and run
time is `completed_at - started_at` from the jobs API (`filter=latest`). Skipped
and cancelled jobs are excluded. Matrix medium-test shards are pooled. Two
samples: the 30 newest completed runs on 2026-11-15 (a quiet period), and the
40 newest completed runs created on 2026-11-12 (the slow day). Wall time is run
`created_at` to `updated_at`. For the gate jobs that declare `needs:` (`core`,
`docs`, `tooling`, `explorer`, `results-data`) the API creates the job with the
run, so their "queue" column includes the wait for the jobs they depend on;
those figures are an upper bound on pure runner wait.

## Wall time

| Sample | Runs | Median | p90 |
|---|---|---|---|
| 2026-11-15 (quiet) | 30 | 17.6 min | 20.9 min |
| 2026-11-12 (slow day) | 40 | 49.9 min | 89.0 min |

## Per job, 2026-11-12 (minutes)

| Job | n | Queue median | Queue p90 | Run median | Run p90 |
|---|---|---|---|---|---|
| test (ubuntu-latest, 3.14) | 30 | 4.1 | 20.6 | 15.9 | 17.9 |
| medium-test (shard) | 120 | 5.1 | 23.7 | 7.5 | 8.7 |
| correctness-gate (a) | 30 | 6.0 | 22.6 | 6.7 | 7.3 |
| correctness-gate (b) | 30 | 6.4 | 23.6 | 5.4 | 6.5 |
| lint | 30 | 3.7 | 17.3 | 4.8 | 5.5 |
| tuning-liveness | 12 | 3.2 | 14.7 | 5.7 | 6.5 |
| medium-collect | 30 | 5.6 | 23.3 | 1.7 | 1.8 |
| content-guard | 15 | 5.7 | 19.8 | 1.7 | 1.9 |
| example-validation | 29 | 6.4 | 21.9 | 1.0 | 1.1 |
| results-data-check | 17 | 7.3 | 25.4 | 1.6 | 2.0 |
| spellcheck | 29 | 8.4 | 25.8 | 0.3 | 0.4 |
| windows-tests (windows-latest, py3.12) | 27 | 1.7 | 8.3 | 3.6 | 14.8 |
| TPC-H binary framing (macos-latest) | 30 | 1.7 | 11.6 | 1.4 | 1.6 |
| TPC-H binary framing (windows-latest) | 30 | 1.2 | 4.8 | 1.6 | 1.8 |
| sql-execution-evidence | 13 | 5.2 | 15.8 | 2.1 | 2.3 |
| parity-check | 8 | 4.9 | 10.2 | 0.7 | 0.7 |
| skill-integrity | 9 | 3.8 | 12.1 | 1.1 | 1.1 |
| package-smoke | 8 | 3.3 | 6.7 | 1.0 | 1.0 |
| dependency-audit | 8 | 2.2 | 12.4 | 0.7 | 0.8 |
| audit-sha | 8 | 2.3 | 19.5 | 0.6 | 0.7 |
| comment-policy | 30 | 3.6 | 12.4 | 0.7 | 0.8 |
| ci-paths | 30 | 3.3 | 12.4 | 1.0 | 1.4 |
| core | 39 | 1.2 | 7.7 | 0.2 | 0.2 |
| docs | 39 | 6.5 | 17.8 | 0.1 | 0.1 |
| tooling | 39 | 8.0 | 25.4 | 0.1 | 0.5 |
| explorer | 39 | 6.5 | 31.5 | 0.1 | 0.1 |
| results-data | 39 | 6.4 | 28.9 | 0.1 | 0.1 |

## Per job, 2026-11-15 (minutes)

| Job | n | Queue median | Queue p90 | Run median | Run p90 |
|---|---|---|---|---|---|
| test (ubuntu-latest, 3.14) | 18 | 0.1 | 0.1 | 13.8 | 17.9 |
| medium-test (shard) | 72 | 0.0 | 1.0 | 7.7 | 9.7 |
| correctness-gate (a) | 18 | 0.1 | 0.2 | 6.1 | 7.0 |
| correctness-gate (b) | 18 | 0.1 | 1.1 | 5.6 | 6.5 |
| lint | 18 | 0.1 | 0.1 | 4.3 | 5.5 |
| windows-tests (windows-latest, py3.12) | 16 | 0.1 | 1.5 | 3.0 | 15.3 |
| core, docs, tooling, explorer, results-data | 30 each | 0.1 | 0.1 to 1.9 | 0.1 to 0.2 | 0.1 to 0.6 |

## Findings

- Run times are the same on both days (test 13.8 vs 15.9 minutes, medium shard
  7.7 vs 7.5). Only queue time moved: a typical job waited 0.1 minute on the
  quiet day and 4 to 8 minutes at the median, 17 to 26 minutes at p90, on the
  slow day. The 32-minute difference in median wall time is queue time.
- That pattern is runner capacity: every job class was delayed, including
  0.3-minute jobs such as `spellcheck` (queue p90 25.8 minutes). Workflow
  ordering cannot delay a job that has no `needs:` and runs for seconds.
- Ordering adds a floor, not the spike. The critical path on a quiet day is
  `test` (13.8 min median) with `medium-collect` then `medium-test` (1.7 + 7.7)
  and `correctness-gate` (6.1) in parallel, then the gate jobs; 17.6 minutes
  median wall time is about that path. The placeholder gate jobs run for 0.1 to
  0.2 minutes, so the placeholder pattern costs little itself; it only looks
  long because those jobs start last and inherit any queue.
- On the slow day required `core`, `docs` and `tooling` appear delayed because
  they follow the slowest queued jobs, not because they queued by themselves.

## Gate decision: stale-trunk check stays advisory

`scripts/trunk_revert.py arm-gate` warns, never refuses, when the newest
`trunk.yml` run on `develop` has been pending over 30 minutes. This is a
recorded owner decision (`_project/decisions/trunk-per-commit-runs-2026-10-27.md`,
kept in `trunk-latest-head-runs-2026-10-30.md`), and the measurements above are
the reason refusing would be wrong: the queue is a shared capacity limit that
PR CI and trunk draw from together. Refusing to arm while trunk is queued would
stop every PR for the length of a capacity shortage, and would not shorten it.
The rationale is next to the gate's other rules in
`docs/internal/merge-queue-governance.md`. Red-trunk refusal and the
required-check rules are unchanged, so no PR merges with fewer checks.

## Capacity recommendation (owner decision, not applied)

The 20-concurrent-job pool of the Free plan is the bottleneck. One PR run
creates about 19 jobs (this sample: 768 timed jobs across 40 runs, the sum of
the n column above, counting medium shards and excluding skipped ones) and one trunk run about 12, so a
single PR run can fill the pool and any overlap queues. Options in order of
cost:

1. Upgrade the organization to GitHub Team: the standard-runner pool for private
   repositories rises from 20 to 60 concurrent jobs. At the 2026-11-12 load
   this would remove most of the 4 to 8 minute median and 17 to 26 minute p90
   queue, bringing wall time toward the 17.6-minute quiet-day figure.
2. If the plan stays: keep trunk from competing with PR CI (it already runs one
   `develop` run at a time) and prefer fewer, larger jobs only where run time
   is under a minute (`spellcheck`, `results-data-check`, `example-validation`
   and `comment-policy` each hold a runner slot for under 1 minute of work).
   Folding them into one job would cut about 3 slots per run, roughly 15 percent
   of one run's footprint, at no spend. Applied on 2026-11-16 without changing
   a required check name; see the note below.
3. Larger runners do not help: run time is constant; only queue time varies.

## After: the four short checks in one job (2026-11-16)

`comment-policy`, `spellcheck`, `results-data-check` and `example-validation`
now run as steps of one `short-checks` job. The `docs`, `results-data` and
`tooling` gates read one result per check from that job, so the required check
names and what each gate enforces are unchanged.

Measured on the change's own pull request runs, counting jobs that ran:

| Run | Changed paths | Jobs before | Jobs after | `short-checks` queue | `short-checks` run |
|---|---|---|---|---|---|
| [43894241317](https://github.com/BenchBox-dev/benchbox/actions/runs/43894241317) | workflow, docs, tests | 23 | 20 | 0.3 min | 2.9 min |
| [43897810483](https://github.com/BenchBox-dev/benchbox/actions/runs/43897810483) | one test file | 18 | 18 | 0.2 min | 0.5 min |

"Jobs before" is the same run with the four checks as separate jobs. The saving
is three slots when both the docs and results-data checks run, two when only
the docs checks run, and none when neither runs, because `comment-policy` ran
alone in that case already. In the 2026-11-12 sample above the docs checks ran
in 29 of 30 runs and `results-data-check` in 17, so most runs save two or three.

The merged job takes about 3 minutes when every check runs, against a longest
single check of 1.6 minutes before. It is still far off the critical path
(`test`, about 14 minutes), and wall time for the first run was 18.5 minutes,
within the quiet-day range above. These runs were on a quiet day, so they show
the job count, not the effect on busy-day queue time. That needs a sample from
a day with the 2026-11-12 load.
