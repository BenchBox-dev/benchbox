# ADR: A two-layer integration-branch loop for related-item delivery

## Status

Accepted (2026-11-01). The loop is opt-in; serial mode stays the default until a
separate decision changes it. It refines the batch contract in
[`docs/agent/batch-tracker-contract.md`](../../agent/batch-tracker-contract.md)
and the protocol in
[`docs/agent/batch-feature-delivery.md`](../../agent/batch-feature-delivery.md),
and builds on todo-db's batch v2 design (todo-db ADR 0012).

## Context

Related items were delivered either as separate serial PRs, each with its own
review and merge, or through a shared integration worktree where one session did
all the work. The first multiplies review and CI cost for changes that are only
meaningful together. The second serializes implementation, grows one context
until it is unreviewable, and cannot run members in parallel.

The tracker blocked the middle path. Under todo-db 0.12.0 any worker could take
an unclaimed member and strand the leader; a member could start only at the
batch's start head, so one that had merged newer work could not be prepared; the
integration head moved only inside `prepare`, so a review fix could not be
recorded; the final-PR binding could be set once; any edit of a member, even a
note, deleted its receipt; and a dead leader could not be recovered. todo-db
batch v2 (ADR 0012) replaces those semantics, and BenchBox adopts the loop on
that capability.

## Decision

Delivery is two layers, with a hard split between who writes the tracker and who
writes code.

1. **A leader owns the batch.** One session registers it, owns the one
   integration branch `feat/<batch>` and worktree, lands each member, records
   the landing, and ships one final PR to `develop`. The leader is the only
   tracker writer and the only writer of the ledger.
2. **Members own their changes.** Each member works in its own worktree and
   branch `feat/<batch>--<member>`, started at the integration head. It merges
   the current integration head, runs mapped and named tests and a local review
   against that head, and stops with a landing report. It opens no PR and writes
   no tracker state.
3. **Landing is leader-side and checked.** `batch-land` requires an unchanged
   head, a review bound to the exact patch, a change inside the frozen scope, and
   a merge whose tree equals the member's tree. It reruns earlier members whose
   files overlap. The leader then calls `take` and `prepare`.
4. **The integration head moves only by a recorded advance.** Review fixes after
   the PR opens and merges of `develop` are leader commits recorded with
   `advance_batch_head`, and the final-PR binding moves with them
   (`bind_batch_pr` with `expected_previous_head`). Members are never
   re-landed.
5. **The final PR is the unit of review, merge, and rollback.** The integration
   branch is never a PR base; the final PR is reviewed as one change and squash
   merged, so `trunk_revert` reverts the whole batch.
6. **Authority stays where it was.** The tracker is authoritative; the ledger
   under `.todo-batch/<batch>/` is a rebuildable cache; Git is authoritative for
   history. Batch notes go on an umbrella item that is not a member.

The mode stays opt-in. A runtime without the v2 capability keeps serial mode,
and work that installs or changes the loop is delivered serially.

### Choices inside the loop

- **One tier of tests per layer.** Members run mapped and named tests; the
  leader runs the fast tier once on the integration tree. Concurrent members then
  do not multiply heavy work on one host.
- **Member review is local and exact.** A member's review result binds its base
  and head and is stored in the local review cache; the leader trusts the cache,
  not the member's report. A member that changes only exempt paths is recorded
  `not-reviewed`, the same bar a standalone PR meets.
- **Size is bounded by the hosted review.** `brief_max_bytes` (100000) is a hard
  limit: a diff over it reviews in file-list mode, and a file list over it skips
  the hosted reviewers. A landing that would exceed the file-list cap is refused;
  the batch is split instead.
- **One changelog fragment per member**, at most five entries each.
- **Cloud builders are not members.** They build one change on their own
  branch and PR; batch worktrees and the tracker writer are local.
- **Recovery is explicit.** A dead leader is replaced with
  `recover_batch_owner`. When that is impossible, `abort_batch` returns members
  to ordinary claims; it refuses once a member is done.

## Alternatives considered

- **Stacked PRs per member.** Rejected: the stack rule caps depth at three,
  forbids building on another agent's unmerged branch, and reviews each layer
  separately, which is the cost the loop avoids.
- **Members write the tracker.** Rejected: owner-only claims make it unworkable,
  and two writers would race on receipts and heads. One server stays one worker
  identity.
- **Rebase-based integration.** Rejected: recorded heads and receipts must stay
  ancestors of the integration head; merge commits keep them so, and a refresh
  from `develop` is a recorded merge.
- **Making the mode the default now.** Deferred: the default changes only after
  the loop has delivered a batch through itself.

## Consequences

- AGENTS.md gains a member exception to the write close-out rule, `BASE=` in the
  worktree section, leader-only integration worktrees, and a stack-rule exemption
  for members; the detailed protocol lives in the agent docs.
- PR-opening targets refuse in a member worktree; readiness and arming check the
  batch evidence in an integration worktree and fail closed.
- A batch is bounded by the hosted brief cap and by one leader's capacity to land
  and review; larger work is split into batches.
- A lost owner generation is recoverable only through `recover_batch_owner` or
  an abort, so the leader's ledger must survive the session.
