# Recovery

## A gate is not a failure

`kind: "gate"` results are the tracker working. Read `recovery`, act on it, and
continue. Do not retry the same call unchanged.

## Lost claim or restarted session

Re-adopting your own claim with `take` mints a fresh generation and refreshes its
lease. A default MCP identity changes across server restarts: before a planned
reconnect, checkpoint and release an ordinary task's claim; confirm the intended
store/runtime after reconnect, then take it again. A stable unique actor supports
identity-preserving restart only after its previous server stops.

If someone else holds the claim, inspect it with `show_item`. Use `takeover` only
when that exact holder's session cannot resume, naming the holder and reason.
Never share an actor across concurrent servers or take a live claim to avoid
coordination.

`E_MULTIPLE_CLAIMS` means you already hold a live claim on another task. Finish
or `release` it first — one live claim per worker.

## Conflicts

`E_CONFLICT` means a competing writer changed the task first. Re-read with
`show_item`, re-evaluate whether your operation still applies, and only then
retry. Retries re-apply the logical operation; they never merge or overwrite
the newer state.

## Offline and unknown outcomes

- `E_OFFLINE`: the remote is unreachable. `list_items` / `show_item` may serve a
  cached revision marked `stale`; mutations fail rather than succeeding locally.
  Retry when reachable.
- `E_UNKNOWN`: a push reply was lost and the outcome is undetermined. The
  `recovery` list carries the operation ID. A human reconciles with
  `tractable recover --op-id <id>` before anyone re-applies the work.

## Schema and state errors

`E_SCHEMA` means the state format is newer than the installed package; a human
upgrades. On a batch call it also means the store is below schema 4 (register
with `confirm_schema3_cutover=true` first) or a legacy receipt sits under a v2
batch. `E_STATE` explains itself in the message — a malformed request, an
unknown task, or a rejected transition. `E_CURSOR_STALE` means restart listing
from the first page.

## Batch recovery

Prepared delivery requires the actual installed runtime's `tractable_batch` version
2 capability and supported store/receipt schemas. Read live state with
`show_batch`/`show_item`; ledger hints are not acceptance proof, and private
generation material is not recoverable from the redacted batch read.

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
- **Review revisions**: withdraw auto-merge before publication. Commit and verify
  the complete leader-fix interval, submit the expected-head tracker advance,
  push the captured head, and CAS-rebind the same PR. A helper must not push
  before the tracker advance. If interrupted between those steps, reconcile the
  exact current heads and repeat only the missing operation. After a head or
  binding change, run new final evidence/review/CI before rearming. Once any
  member is done, heads and bindings cannot change; resume closeout.
- **Receipt invalidated by an edit**: only `context`/`context_append` edits keep a
  receipt; any other edit of a member deletes it and its dependents' receipts.
  Registered batch metadata edits are refused, and once any member is `done`
  invalidating edits are refused (`E_STATE`, "closeout has started"). Before
  closeout, prepare the member again (re-prepare): `take` it, then `prepare` at
  the current integration head; its accepted head is already contained, so no new
  commit is needed. Dependents are re-prepared the same way.
- **`E_SCHEMA` on a batch call**: the store is below schema 4, or a legacy
  receipt is under a v2 batch. A human upgrades the clients and the first
  `register_batch` with `confirm_schema3_cutover=true` performs the cutover; do
  not work around it.

## Prepared receipt recovery

Prepared mode is unavailable when the `tractable_batch` version 2 capability is
absent or the store schema is outside the advertised `supported_schema_versions`:
stop and use the serial claim/finish loop. A store below schema 4 is cut over by
the first `register_batch` with `confirm_schema3_cutover=true`. Do not migrate a legacy per-item receipt into a
registered batch by assertion.

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
