# Run trunk per commit in parallel and warn only on a stale run

Date: 2026-10-27
Status: Decided by the owner. Supersedes the `concurrency.queue: max` part of
the rationale for `.github/workflows/trunk.yml` in
`merge-queue-retirement-2026-10-03.md`.
Related: `docs/internal/merge-queue-governance.md`; `scripts/trunk_revert.py`.

## Decision

1. `trunk.yml` uses the concurrency group `trunk-${{ github.sha }}` with
   `cancel-in-progress: false` and no queue. Runs for different commits proceed
   in parallel. A re-run of one commit waits for that commit's earlier run, so a
   commit never runs twice concurrently. When `ci.yml` calls the workflow for a
   pull request, the group is the merge commit of that pull request head, so
   pull request reuse is unaffected.
2. `make pr-open` and `make pr-arm` print a warning, naming the run and its age,
   when the newest `trunk.yml` run on `develop` has been queued, waiting or in
   progress for more than 30 minutes. The warning never blocks.
3. A red `develop` still drafts `pr-open` and refuses `pr-arm`. The red check
   reads the newest completed run, so a newer run still in progress no longer
   hides a red that is over 30 minutes old.
4. Red-streak rule for results arriving out of order: runs are ordered by
   creation time. A completed green on a later commit clears every earlier red
   (it tested a tree containing that commit), even if the earlier red finishes
   later. A green on an earlier commit never clears a later red. A red with an
   unfinished older run and no green between stays undetermined and refuses
   arming without naming a culprit.

## Rationale

Measured on trunk runs: median time to a result 71 minutes, p90 265 minutes.
Runs waited a mean of 88 minutes behind the per-ref concurrency queue against
about 4 minutes for a runner. Merges land a median 13.7 minutes apart, so the
serial queue grew faster than it drained. Per-commit groups remove the queue
wait and give each commit its own result sooner.

## Cost

Each merge now runs 11 to 12 jobs concurrently, drawn from the 20-job
concurrency pool of the Free plan. Bursts of merges can exhaust the pool, so
runner wait replaces queue wait, and the stale-run warning is the signal that
this has happened.
