# Trunk UAT

`.github/workflows/trunk-uat.yml` runs the three release-gate UAT stages against
the trunk distribution of the newest releasable `develop` commit, every night at
02:00 UTC and on demand. UAT is a continuous property of trunk: a release checks
the evidence of a trunk run instead of discovering UAT failures during the
release. It changes no stage config, timeout, memory limit, validator or floor;
it reuses the `release-uat-env` action, the `release-v2.yml` shard matrix and
`make uat-gate-check`.

## Which commit runs

`scripts/trunk_uat.py plan` selects the source commit with
`scripts/release_releasable.py` (see [release-artifacts.md](release-artifacts.md)):
the newest `develop` commit with a successful `trunk.yml` push run and a dist
artifact that passes admission selection. If none of the newest 40 qualifies,
the run fails with `no releasable commit among the newest 40 successful trunk
runs`.

`scripts/trunk_uat.py admit` downloads that commit's trunk dist, verifies the
artifact digest, producer receipt and bundled binaries, and writes an admission
receipt whose tag is `v<version of pyproject.toml at the commit>` and whose tag
object is the commit. That is the same directory shape `release_admitted_dist.py`
verifies, so the `release-uat-env` action installs the trunk dist unchanged. The
distribution is never rebuilt, and nothing is published.

## Deferral, skipping, and the baseline

`plan` makes one of three decisions. Every decision is written to the plan job
summary and the job outputs.

| Decision | Summary text | When |
| --- | --- | --- |
| defer | `deferred: release-v2 run <id> in progress` | Any `release-v2.yml` run is not completed, for a scheduled or a dispatched run. |
| skip | `skipped: unchanged since <sha>` | Scheduled only: no path in the skip path set changed between the skip baseline and the source commit. |
| run | `run: <reason>` | Everything else: dispatch, no baseline, an unreachable baseline commit, or a changed skip path. |

Only the `schedule` event can skip. `workflow_dispatch` and any other trigger
always run unless deferred.

The skip path set is `GUARDED_TREES` imported from
`scripts/release_reuse_uat_evidence.py`, plus `benchbox`, `uv.lock`,
`.github/workflows/trunk-uat.yml` and `.github/workflows/release-v2.yml`.
`tests/unit/workflows/test_trunk_uat_workflow.py` fails if the set stops
covering the reuse guard. A path matches when it equals an entry or lies below
one.

The skip baseline is the newest earlier run on the same branch that executed
shards: the run that uploaded a `trunk-uat-record-<sha>` artifact. A
run uploads that artifact only when at least one cell executed and the run was
not cancelled, so skipped, deferred, cancelled and zero-cell runs never become
the baseline. A red run that executed cells is still the baseline: a scheduled
run with no relevant change skips after it, and `workflow_dispatch` reruns it.
An expired artifact is ignored, so a baseline older than 90 days forces a run.

A skipped run finishes after the plan job. It starts no shard, so it ends in
minutes.

## Jobs

| Job | Purpose |
| --- | --- |
| `plan` | Decision above. Needs `actions: read`. |
| `admit` | Trunk dist of the source commit, uploaded as `admitted-v<version>` for the action. |
| `uat-stage1`, `uat-stage2`, `uat-stage3` | The same 16 shard jobs, matrices, timeouts and step definitions as `release-v2.yml`. A workflow test compares them with the live `release-v2.yml`. |
| `gate` | `make uat-gate-check` over every shard with the dist digest, as `release-uat` does. |
| `record` | Per-cell records, cost, and the zero-cell detector. Runs after a cancellation. |

Concurrency group `trunk-uat` queues a second run behind the first and never
cancels it, so runs never overlap. 16 shards hold 16 of the 20 concurrent jobs
of the Free plan for hours; keep the 02:00 UTC schedule.

## Evidence and history

Every artifact name includes the source commit; every evidence artifact is kept
90 days. Uploads overwrite, so re-running only the failed jobs of a run replaces
the evidence of the shards that ran again and the gate sees every shard.

| Artifact | Contents |
| --- | --- |
| `trunk-uat-<sha>-stage<N>-<shard>` | The shard's `uat_*` run directory and `dist-digest.txt`. |
| `trunk-uat-gate-<sha>` | `uat-gate-summary.json` (the combined gate verdict) and `dist-digest.txt`. |
| `trunk-uat-record-<sha>` | `trunk-uat-record.json` and `cells-history.jsonl`. Present only for executed runs. |

`trunk-uat-record.json` binds the run to `source_sha` and `dist_digest` (the
SHA-256 of `dist-digest.txt`, the value passed to `uat-gate-check`) and records
the gate `verdict`, cell counts, per-shard state and hours, `runner_hours`,
`wall_hours`, and `over_gate`.

The per-cell history has two readable forms, both built from the shard evidence
the harness already writes. The artifacts of successive runs are the
append-only log.

`cells-history.jsonl` is one JSON row per executed cell (schema `1`):

- run: `run_id`, `run_attempt`, `event`, `source_sha`, `dist_digest`;
- cell: `stage`, `shard`, `platform`, `benchmark`, `scale`;
- result: `status`, `terminal_state`, `elapsed_s`, `peak_rss_gib` (from the
  `# UAT_PEAK_RSS_GIB` marker in the cell log, else null);
- failure: `failure_kind` and `failure_signature` exactly as the harness
  classified them in `cells.jsonl` (null for a passed cell);
- attempt: `attempt`, `retry_signature`, `retry_outcome`, `first_attempt_status`
  and `first_attempt_elapsed_s` from the infrastructure retry, with `attempt`
  1 when the harness wrote no retry.

Print the rows of the newest executed runs, newest first:

```bash
uv run -- python scripts/trunk_uat.py history --branch develop --run-limit 30
```

Python callers use `scripts.trunk_uat.history_rows(branch=..., run_limit=...)`.

`scripts/uat_rubric.py` reads a directory of run directories instead. Download
the shard run directories of the newest executed runs into that layout
(`<run id>-<sha prefix>/stage<N>-<shard>/cells.jsonl` and the rest of the run
directory) and point the rubric at it:

```bash
uv run -- python scripts/trunk_uat.py fetch-runs --output ~/trunk-uat-history --run-limit 30
uv run -- python scripts/uat_rubric.py --history-dir ~/trunk-uat-history --output-dir ~/trunk-uat-rubric
```

## Failure detectors

- The `record` job fails the run with `zero cells executed: <n> shard evidence
  directories held no cells.jsonl rows (<job results>)` when no shard reported a
  cell, including when `admit` failed and no shard ran. Such a run is not a
  baseline and uploads no record artifact.
- The `gate` job fails unless every shard job succeeded and the combined gate is
  green.
- The `record` summary prints `DECISION GATE` when a run used more than 30
  runner-hours or a shard ran longer than 6 hours. Stop and take the numbers to
  the owner before the schedule continues.

## Interaction with `release-v2`

A `release-v2.yml` run that is not completed defers trunk UAT (above). The
full-UAT fallback of a release must cancel any in-progress trunk UAT run before
its shards start, with:

```bash
uv run -- python scripts/trunk_uat.py cancel --reason "release-v2 run $GITHUB_RUN_ID full UAT fallback" \
  --summary "$GITHUB_STEP_SUMMARY"
```

The caller needs `actions: write`. The command cancels every unfinished
`trunk-uat.yml` run and records the run ids in the caller's summary. The
cancelled run's `record` job still runs and records
`cancelled: release-v2 run <id> in progress (full UAT fallback)`; a cancelled run
is never a skip baseline.

## Operating it

Run it now on `develop`:

```bash
gh workflow run trunk-uat.yml --ref develop
```

Calibrate a change to the workflow on a branch before it merges: add a temporary
`push` trigger for that branch, push, and record the run's wall time,
runner-hours and per-shard state from the `record` job summary. Remove the
trigger before the pull request. A push run executes unconditionally, uploads
evidence on the branch, and is never the baseline of a `develop` run. A workflow
test fails if the final file has any trigger other than `schedule` and
`workflow_dispatch`.

Rollback: revert the commit that added `trunk-uat.yml`. `release-v2.yml` keeps
its own full-UAT fallback and does not depend on this workflow.
