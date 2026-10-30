# Run trunk on the latest develop head only

Date: 2026-10-30
Status: Decided by the owner. Supersedes `trunk-per-commit-runs-2026-10-27.md`.
Related: `docs/internal/merge-queue-governance.md`; `scripts/trunk_revert.py`.

## Decision

1. Pushes to `develop` share the concurrency group `trunk-develop` with
   `cancel-in-progress: false` and no queue. One run is in flight. A push that
   arrives meanwhile waits, and a newer push replaces a waiting one. A started
   run is never cancelled. Runs started by `ci.yml` and by manual dispatch keep
   the per-commit group `trunk-<sha>`, so they never wait behind or replace a
   develop run.
2. A commit that was replaced while waiting has no run of its own. The first red
   run since the last green run covers every commit merged since that green run.
   As before, the culprit is assumed to lie in that first red run's range, and
   later red runs in the same streak are assumed to inherit it. `make pr-arm`,
   `make pr-open` and `make trunk-status` name all of those commits (the oldest
   eight, and a count of newer ones) instead of blaming the run's head commit.
3. A revert of any commit that range covers is exempt from the arm gate. When the
   range holds more than one commit, merging one such revert does not reopen
   arming for other pull requests: only a green run does, because the revert may
   have named the wrong commit. A range of exactly one commit behaves as before.
   If the commits between the last green run and the first red run cannot be
   read in full, the gate refuses and the disarm job fails visibly; it never
   falls back to blaming the head commit.
4. Release admission is unchanged. It needs a successful exact-commit `push` run,
   so a commit that was replaced cannot be released. Release from the newest
   tested head.
5. The stale-run warning is unchanged and stays advisory. It can fire while a
   waiting run sits behind a long run in flight.

## Rationale

On 2026-10-30 fourteen trunk runs were queued, the oldest for more than five
hours, and `develop` showed red. Each push started about twelve jobs from the
20-job pool of the Free plan, so runner wait dominated. Two runs failed in the
`dist-artifact` job's step that waits up to 60 seconds for the jobs API to list
its own job as running; the red state then refused every arming.

The earlier serial queue failed for a different reason: it held every commit, and
merges land a median 13.7 minutes apart against a median 71 minutes to a result.
Holding only the newest waiting push bounds the wait to one run in flight plus
one run, so the queue cannot grow.

## Cost

- The per-commit result is gone for replaced commits. Finding the culprit of a
  red run means choosing among the commits it covers, for example by reverting
  the likeliest and waiting for the next run.
- The gap that the 2026-10-27 decision closed is open again: a commit can be
  merged and never run by itself.
- Time to a result for one commit is up to one run in flight plus its own run.

## Rejected

- Cancelling the run in flight when a push arrives: with merges every 14 minutes
  no run would finish.
- Bounded parallelism of two or three runs: still needs a rule for which waiting
  runs to drop, and the runner pool remains shared with pull request checks.
