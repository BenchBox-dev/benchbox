# Batch implementation

Use `batch` to implement an explicit named TODO set in dependency order. It
authorizes implementation, branches, commits, and PRs only for that set;
auto-merge is allowed only when CI, not human approval, is the integration gate.
It does not bypass repository, tracker, review, or publication gates.

A batch coordinates bounded contexts, not one long conversation. The tracker,
Git, PR service, CI, artifacts, and human decisions remain authoritative.

## Before you start

1. Read repository instructions, `references/implement.md`,
   `shared-change-framework/SKILL.md`, and `shared-review-protocol/SKILL.md` as
   required for the next action; do not copy their rules into the ledger.
2. Call `get_instructions` with `expect_store` set to the project's
   `owner/repo`, then `list_items(ready_only=true)` to confirm the bound store,
   identity, and live state. Fix or report any failure and stop before claiming
   or editing. With multiple linked worktrees, ensure each worktree has its own
   client session or explicit `--repo-root` (one server instance per worktree).
3. Confirm the exact TODO set, dependencies, integration branch, and existing
   claims, branches, worktrees, and PRs. Reconcile rather than duplicate work.
4. Run any required repository write preflight; fix or report failure and stop.

## Local handoff ledger

Keep one ledger at a stable ignored path outside disposable worktrees, such as
`.todo-batch/<slug>.txt` in the orchestration worktree. Verify it once with
`git check-ignore`; use `.git/info/exclude`, not a committed rule. Never commit it.

Before the first claim or source mutation, initialize the exact named TODO IDs.
Store one latest envelope per item, separated by `---`, and atomically replace
the complete file. Only the serial controller writes it; concurrency is unsupported.

```text
TODO: <id>
STATUS: pending|in_progress|pr_open|waiting|done|blocked
TRACKER_STATE: <observed state>
GENERATION: <claim generation or none>
BRANCH: <branch or none>
WORKTREE: <path or none>
PR: <number or none>
PR_STATE: <open|merged|closed|none>
HEAD: <sha or none>
VERIFICATION: <concise result>
GATE_SNAPSHOT: <gate, concise result/link, or none>
BLOCKER: <owner/reason or none>
NEXT_ACTION: <exact action>
---
```

These are cached observations, not authority for a mutation. Before acting,
revalidate the facts required by `NEXT_ACTION` under the implementation guide,
change framework, and repository policy.

## Schedule bounded work

Run serially. A substantial TODO needs its own implementation, verification,
and review cycle; give it a fresh worker context when supported. Tightly coupled
trivial items may share a context while their combined history remains small.

At each scheduler boundary:

1. Read the ledger once and refresh only state needed to select the next item.
2. Mark missing, malformed, or cyclic items `blocked` with a reason:
   `update_item(id=..., status="blocked", reason=...)`.
3. Select an item only when its dependencies, including required integration
   merges, are satisfied, tracker state permits claim or resume, and no existing
   branch, worktree, or PR must be reconciled first.
4. Start or resume it with `references/implement.md` in a fresh isolated linked
   worktree from the current integration branch. Refresh after dependency merges
   and remove that clean worktree after merge under repository policy.
5. After one focused retry, classify externally resolvable failures as waiting
   and non-resolvable failures as blocked; record the owner and next action.

If no item is eligible and the remainder waits on external gates, checkpoint,
report, and end the batch invocation. Resumption is a new invocation; do not
turn repeated invocations into an implicit polling loop.

## Worker closeout

Follow implementation, verification, commit, PR, and completion contracts by
reference. Before commit or PR, run the required `shared-review-protocol`
review, resolve Critical and Required findings, disposition optional findings
under repository policy, and re-verify substantive fixes.

Do not return full task bodies, diffs, logs, or source dumps. Keep long output
in a temporary artifact and return only its result, short tail, link, or digest.
Return the envelope above:

- after successful item closeout;
- before intentionally ending or compacting a worker context; or
- after a failure that requires later resumption.

Store the envelope before selecting more work; add no per-operation
acknowledgements. After an unexpected end, inspect authoritative live state
before retrying an operation with an unknown outcome.

When another item depends on the PR, leave it `pr_open` or `waiting` until
verified merged. Otherwise close it with `finish(id=..., generation=...)` after
PR creation and completion. An open or green PR is not proof of merge.

## CI and external waits

Before querying a gate, reuse its snapshot from this invocation. Take at most
one concise snapshot per gate, store it in the item envelope, and do not query it
again before the invocation ends. Use bounded fields; keep verbose output
outside context and retain only pending/failing names, links, and next action.

If a gate remains pending, record `waiting`. If the claim lease might expire
first, `renew(id=..., generation=...)`, or `release` it so other workers are
not blocked. Checkpoint and end the worker. Continue independent eligible work
without retaining that history; if all work waits, end the invocation. Never
sleep or poll in an implementation context.

## Context rotation and resume

At every substantial-item boundary, persist the envelope and discard the worker
transcript. Rotate or compact the controller too; its handoff preserves the
exact TODO set and ledger path, not logs, readiness output, or discussion.

If fresh contexts are unavailable, checkpoint and compact at a safe item
boundary. Do not measure occupancy or compact uncheckpointed work. If neither
option exists and history grows large, stop with the ledger and resume action.

A resumed context must:

1. confirm that the requested TODO set exactly matches the ledger;
2. read the ledger once and select its current item and `NEXT_ACTION`;
3. revalidate only the live facts required by that action; and
4. continue without replaying completed items, logs, or discussion.

## Final report

Report one concise row per item:

```text
TODO | tracker state | PR/state | head | verification | blocker | next action
```

Include the ledger path and distinguish open, merged, tracker-complete, waiting,
and blocked work. If anything remains non-terminal, provide the exact resume
action rather than waiting in the current context.

## Prepared feature delivery

Prepared mode is opt-in and requires a server advertising the compatible
registered-batch capability and schema version. The integrator first calls
`register_batch` once with one repository identity, owner generation,
integration branch/worktree, immutable start head, ordered member IDs, frozen
per-member scope and its hash, delivery boundary, and intended terminal
outcome. Duplicate batch IDs, duplicate or foreign member enrollment, and
late membership fail closed. Each member is then created with matching batch
metadata and an explicit implementation edge before it is claimed.

For each member, `take` one item, implement in an isolated worktree, and run
the bounded upstream suite in that clean exact checkout. Call `prepare` with
the exact source worktree and revision, that member's actual source base,
accepted member head, current integration head, frozen scope hash,
implementation edges, and passed evidence. A dependent member normally starts
from its predecessor's accepted head; do not relabel that range as the batch
start. Stored verification commands are records for a human to run;
the tracker does not execute them. `prepare` releases the claim and leaves the
member `open`; it never marks the member done. Readiness accepts only a
same-batch implementation edge whose receipt is present at the registered
integration head and matches repository, base, scope, owner generation, and
member identity. Review, external dependencies, approvals, merge, deployment,
and soak gates still require ordinary `done` state.

The integrator owns branch assembly, binds exactly one final PR with
`bind_batch_pr`, and rechecks the registered clean integration worktree and
branch on every retry. Final evidence must map every member's accepted head
and per-member base-to-head range, match the frozen scope union, final PR, repository,
and current integration head, and match the actual Git tree. Re-take each
member and call `finish` only after cumulative current-tree evidence exists.
If work must stop, the registered owner calls `abort_batch` after releasing all
member claims. Abort is durable and idempotent: it invalidates prepared and
final member evidence, retains an already-bound final PR as immutable history,
detaches recoverable members from the archived batch, and returns them to the
ordinary claim/finish lifecycle. It refuses if any member is already `done`,
so partial closeout remains resumable and terminal items are never reopened.
Once every declared member finishes with final evidence, the batch is durably
`completed`; completed or aborted batches reject later preparation, binding,
abort, or member edits.
Interrupted or partial closeout is retryable and cannot create a false done.
Any member, scope, dependency, conflict, or late-enrollment change invalidates
affected prepared and final evidence transitively. Scope patterns are not
treated as a complete static overlap solver: the authority is each real Git
changed-file check plus accepted-content comparison, which rejects a later
member that overwrites an earlier accepted path. Serial mode remains the
default for unrelated, cross-repo, or approval-separated work.
