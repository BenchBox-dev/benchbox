# Recovery

## A gate is not a failure

Do not retry the same call unchanged after a gate.

## Lost claim or restarted session

Claim identity, re-adopting a claim after a restart, `takeover`, and
`E_MULTIPLE_CLAIMS`: SKILL.md, Critical rules.

## Conflicts

After `E_CONFLICT`, re-read with `show_item` and re-evaluate before retrying.
Retries re-apply the logical operation; they never merge or overwrite the newer
state.

## Unknown outcomes

- `E_UNKNOWN`: a push reply was lost and the outcome is undetermined. The
  `recovery` list carries the operation ID. A human reconciles with
  `tractable recover --op-id <id>` before anyone re-applies the work.

## Open deferrals

`E_OPEN_DEFERRALS` means `finish` refused because the task has open deferrals;
the response names the blocking ids, and `show_item` reports them as
`open_deferrals`. Read them with `list_deferrals(from_item=<id>)`, then resolve
each one: `promote_deferral(id=<item>, deferral_id=<n>)` creates a linked
planning item (pass `to_item` to link an existing one instead), and
`dismiss_deferral(id=<item>, deferral_id=<n>, reason=...)` drops it with a
recorded reason. Then retry `finish`.

## Schema errors on a batch call

`E_SCHEMA` on a batch call (the store is below schema 4, or a legacy receipt is
under a v2 batch): follow `references/batch.md`, Register and dispatch. Do not
work around it.

## Batch recovery

Read live state with `show_batch`/`show_item`; ledger hints are not acceptance
proof, and the redacted batch read never returns generation material.

- **`E_BATCH_OWNER`**: you are not the batch owner, and only the owner may
  `take`, `takeover`, `prepare`, or `finish` a member of an active batch. The
  message names the owner. Ask the owner; members hand work to the leader instead
  of claiming. If the owner is dead, recover it (below). A previous owner that
  kept a claim after recovery may `release` it but not prepare or finish.
- **`E_BATCH_HEAD`**: a head is not a recorded head, does not descend from the
  required head, or commits sit between the registered head and the submitted
  one. Read `integration_head` and the log with `show_batch`. If the leader made
  a fix or merged the integration base, record it first with `advance_batch_head`
  and retry `prepare`. If a member's base is not a recorded head, create the
  member worktree again from a recorded head, merge, and re-request landing.
  `E_BATCH_HEAD` also refuses a `develop-merge` advance that is not a real
  two-parent merge, whose merged parent is not under the batch's registered
  `develop_ref`, or on a batch registered without one (the ref is immutable:
  record the merge only if it was set at registration, otherwise it cannot be
  recorded), and a `fix` advance that touches paths outside the union of member
  scopes.
- **Stale integration head** (`E_CONFLICT` on `advance_batch_head` or
  `bind_batch_pr`, or the integration branch moved while a member was working):
  re-read with `show_batch`, reconcile with Git, and retry with the current head
  as `expected_head` / `expected_previous_head`. An identical retry is
  idempotent. Send a member back to merge the current head before landing it.
- **Interrupted land between merge and prepare**: do not merge again or infer
  success from missing ledger rows. Prove the existing merge's parents, tree and
  exact range, repeat leader review and receipt-derived overlap checks, then
  confirm the current owner and renew or re-take the member for a current
  preparation generation. Failed acceptance still blocks preparation. Resolve
  dirty/unfinished state without discarding unrelated work.
- **Dead leader**: privately obtain the expected owner generation from the
  leader's ledger or existing authorized Git-state recovery access. Generate and
  save a fresh 32-hex `new_owner_generation` before calling `recover_batch_owner`
  with the inspected owner, expected generation and reason. An exact retry
  is idempotent; stale generation/owner checks remain. Rebuild operational hints
  from the tracker and Git, not review approval from caches. `show_batch` never
  returns the generation. If the expected generation cannot be obtained, report
  that prerequisite: abort also requires it, so tokenless abort is not a fallback.
- **Lost integration checkout**: require
  `tractable_batch.features.integration_worktree_relocation=true` before supplying
  all of `expected_integration_worktree`, `expected_integration_head` and the new
  `integration_worktree` to owner recovery. The destination must be clean, in the
  registered repository and branch, at the recorded head with required history.
  Owner/generation/path/head changes race through CAS; no unguarded repointing.
  Preserve prepared receipts and historical source paths, then run new final
  checks at the destination. An active partial closeout may relocate at unchanged
  integration/final-PR head, but cannot advance, reprepare, reopen or rebind
  finished work. This does not upgrade legacy review attestations.
- **Interrupted review revision**: if interrupted between the fix commit, the
  tracker advance, the push and the PR rebind (`references/batch.md`, Publish
  and review), reconcile the exact current heads and repeat only the missing
  operation. A helper must not push before the tracker advance.
- **Receipt invalidated by an edit** (after any member is `done` the edit is
  refused with `E_STATE`, "closeout has started"): before closeout, re-prepare:
  `take` the member, then `prepare` at the current integration head; no new
  commit is needed. Re-prepare dependents the same way. Which edits invalidate a
  receipt: SKILL.md, Planning.

## Prepared receipt recovery

If a prepared batch is abandoned, release every live member claim and have the
registered owner call `abort_batch` with the batch owner generation. Abort
refuses with `E_ACTIVE_CLAIMS` while a claim is live, refuses if any member is
already `done`, clears prepared/final member evidence, retains an already-bound
final PR receipt, detaches recoverable non-terminal members from the archived
batch, and returns them to the ordinary claim/finish lifecycle. Expired active
claims return to `open`; live claims must be released first. It is safe to retry
after an unknown publication result. An aborted or completed batch
cannot be reopened by a member or a stale owner.

If `prepare` rejects the source checkout, inspect the exact recorded revision
and clean the isolated worktree before retrying with the current claim
generation. A moved HEAD, dirty checkout, changed member scope, conflict
resolution, or late member invalidates the receipt; do not reset the baseline
or mark the member done. Re-take the member and prepare again after the
leader records the new exact range. If closeout is interrupted, re-read the
member and final integration evidence before retrying `finish`; stale
generations fail closed. A retry uses the currently registered integration
worktree and branch, including an explicitly recovered relocation, and proves
the current tree, accepted heads, frozen scope and final PR. Reuse a completed
final run only while its relevant bindings still match; a new invocation reruns
all member commands and combined checks. Missing historical evidence in a legacy
partial closeout is a real blocker, never permission to fabricate a review.
