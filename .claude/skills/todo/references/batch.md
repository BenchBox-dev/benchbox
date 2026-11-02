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
the complete file. Only the controller (the leader, in prepared delivery) writes
it. Members never write it, and it is a rebuildable cache: tracker state and Git
are authoritative, and a lost ledger is rebuilt from `show_batch`, `show_item`,
and the integration branch.

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
LANDED: <yes|no>
INTEGRATION_HEAD: <integration head after landing, or none>
VERIFICATION: <concise result>
GATE_SNAPSHOT: <gate, concise result/link, or none>
BLOCKER: <owner/reason or none>
NEXT_ACTION: <exact action>
---
```

In prepared delivery a member's envelope has no PR of its own: record `PR: none`
and use `LANDED` and `INTEGRATION_HEAD`; the final PR and its bound head belong
to the batch header. The ledger also holds the batch owner generation, which
`show_batch` never serves. Standalone
(serial) items keep `PR` and `PR_STATE`.

These are cached observations, not authority for a mutation. Before acting,
revalidate the facts required by `NEXT_ACTION` under the implementation guide,
change framework, and repository policy.

## Schedule bounded work

In serial mode, run serially; prepared delivery runs members in parallel (see
Prepared feature delivery). A substantial TODO needs its own implementation, verification,
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

In prepared delivery a member ends at a landing request (see Prepared feature
delivery), not a PR. Otherwise follow implementation, verification, commit, PR,
and completion contracts by reference. Before commit or PR, run the required `shared-review-protocol`
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

For standalone serial items, when another item depends on the PR, leave it `pr_open` or `waiting` until
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

Prepared mode is opt-in. It requires todo-db 0.14.0 or later. Confirm
`show_batch`, `advance_batch_head` and `recover_batch_owner` are in the tool
registry, and after registration that `show_batch` `capability.version >= 2`;
key on the capability, not on a package version string. If the capability is
absent, or the store schema is outside the advertised
`supported_schema_versions`, stop and use serial mode. A store below schema 4 is
cut over by the first `register_batch` with `confirm_schema3_cutover=true`.
Never reinterpret a
legacy receipt under the new rules.

The loop has two layers. The **leader** registers the batch, owns the one
integration branch and worktree, lands each member, records it in the tracker,
and ships the one final PR. **Members** implement in their own worktrees and
stop at a landing request. Follow the repository's batch protocol for the
concrete commands; this guide names only the tracker calls and the contract.

### Roles

- The leader is the only tracker writer and the only ledger writer. Members
  never call `take`, `prepare`, `finish`, `release`, or any other tracker write
  for batch items; while the batch is active the tracker refuses a claim from
  anyone but the owner (`E_BATCH_OWNER`). The refusal holds only for a distinct
  identity: a member must not use the leader's server instance or actor
  (`--actor`/`TODO_DB_ACTOR`). A member that needs a tracker fact asks
  the leader or reads it (`show_item`, `show_batch`).
- The leader claims a member (`take`) only at the moment it prepares or finishes
  that member, and releases or prepares it immediately. Do not hold member
  claims while members are being implemented: one live claim per worker, and
  `abort_batch` needs every claim released.
- Members work in parallel. Independence is declared by the registered
  implementation edges and frozen scopes, not discovered at merge time.

### Register and dispatch

1. Call `get_instructions` with `expect_store`, then `register_batch` once:
   one repository identity, owner generation, integration branch/worktree,
   immutable start head, an optional immutable `develop_ref` (a
   `refs/remotes/...` ref such as `refs/remotes/origin/develop`; set it
   whenever the integration base may be merged in later, because every
   `develop-merge` advance is refused without it), ordered member IDs, frozen
   per-member scope and its hash, delivery boundary, and intended terminal
   outcome. Registration refuses a member that another worker holds a live
   claim on, duplicate IDs, foreign members, and late enrollment. The first
   registration on a store below schema 4 also needs
   `confirm_schema3_cutover=true`: it performs the one-step cutover to the
   batch schema, is refused while a schema-3 batch is active, and locks out
   older clients, so confirm it only when every process with state-branch
   access is stopped or upgraded. Write the owner generation to the ledger
   before registering; it is a credential and no tool serves it back.
2. After `register_batch` (which performs the schema-4 cutover on an older
   store), attach batch metadata to each member:
   `update_item(id, batch={batch_id, member_id, implementation_dependencies},
   needs=[<current needs plus every implementation dependency>])` for an
   existing item (`needs` is replaced wholesale), or
   `create_item(..., batch=...)` for a new one. Every
   `implementation_dependencies` entry must also be in `needs`.
   Batch metadata is never inferred from `needs`. Never attach metadata before
   registration: below schema 4 it is refused `E_SCHEMA`.
3. Create each member worktree **at the current integration head** (the start
   head for the first dispatch; any head recorded since is also a valid base).
   Record the dispatch base in the ledger and update it each time the member
   merges a newer integration head. Dispatch the member with its item,
   frozen scope, base, and the repository's member-closeout rules.

### Same-file overlap

Two members may legitimately change the same file. Declare that in the frozen
scope when registering; do not rely on a later surprise. Byte-equality of an
earlier member's files is not required. Instead, `prepare` records each overlap
in the receipt (against earlier members, and against leader fix commits made
after the member's base), and every earlier member named in an overlap, every
member touched by a leader fix, and every member whose own receipt records a
fix overlap must re-prove its own acceptance on the **final tree**:
rerun that member's own recorded verification (same suite and command) in the
integration worktree at the final integration head. `finish` refuses without
that current-acceptance evidence (`E_FINAL_EVIDENCE` names the member and the
reason). `show_batch` lists the derived requirement under `acceptance_required`.
A merge from the integration base never adds to it; its files are base content
covered by the final PR's checks. The rerun is attested by the leader; the
tracker checks shape and binding, not execution.

### Member closeout

A member's closeout ends in a **landing request**, not a PR: the member merges
the current integration head into its branch, resolves conflicts, runs its
scoped checks and the local review required by `shared-review-protocol`, commits,
and reports the branch, accepted head, base (the latest recorded integration head merged into the branch), changed files, and verification to
the leader. The member does not push for publication, open a PR, or touch the
tracker. Its final report is the landing request.

### Landing (leader)

For each landing request, one member at a time, in dependency order:

1. Revalidate the member head is unchanged since the request, its review
   result matches that exact patch, and its changed files stay inside the
   frozen scope.
2. Merge the member into the integration branch with a merge commit and confirm
   the merged tree equals the member tree (the member already merged the
   integration head; anything else means the integration head moved: send the
   member back to merge it again).
3. `take` the member, then `prepare` with the integration worktree, the
   source base (the latest recorded integration head merged into the member
   branch, which is also the base its review is bound to), accepted head, new
   integration head, frozen scope hash, implementation edges (the changed files
   are computed by the tracker), and
   passed bounded-suite evidence. `prepare` records the receipt, releases the
   claim, and leaves the member `open`.
4. Record `LANDED: yes` and `INTEGRATION_HEAD` in the ledger.

Every commit on the integration branch outside a member's own range (a leader
fix, a merge of the integration base) must be recorded first with
`advance_batch_head`, or the next `prepare` is refused (`E_BATCH_HEAD`).
Interruption between the merge and `prepare` is recovered in
`references/recovery.md`.

### Publish and review

When every member has landed, publish the integration branch as the one final
PR through the repository's batch protocol, and bind it with `bind_batch_pr`
(`pr_repository` is the PR's own `owner/repo`, `head` equals the registered
integration head). Review the combined diff under
`shared-review-protocol` before publishing; members were reviewed individually
before landing.

Review fixes after the PR opens are **leader commits** on the integration
branch, never member re-landings:

1. commit the fix in the integration worktree;
2. `advance_batch_head(owner_generation=<ledger>, reason="fix",
   expected_head=<old>, head=<new>, changed_files=[...])` (`changed_files` must
   equal the exact rename-free diff `expected_head..head`; a fix stays inside the
   union of member scopes. A merge of the integration base uses
   `reason="develop-merge"` with a real `--no-ff` merge commit whose second
   parent is under the batch's registered `develop_ref`; the call has no ref of
   its own, and a batch registered without `develop_ref` refuses it);
3. push, then `bind_batch_pr(pr_repository=..., number=..., node_id=...,
   head=<new>, expected_previous_head=<old>)` for the same PR identity. The
   binding moves with the head; evidence built for the old head is stale.

An exact retry of either call is idempotent. Heads cannot move once any member
is `done`; resume closeout instead.

### Finish

After the final PR checks pass on the bound head, run each required
current-acceptance rerun in the integration worktree, then for each member
`take` and `finish` with cumulative current-tree evidence that maps every
member's accepted head and base-to-head range, the frozen scope union, the final
PR (repository, number, node id, head), and the integration head. Once every
member finishes, the batch is durably `completed`. Finishing is the leader's act
and follows the PR's merge decision under repository policy; an open or green PR
is not proof of merge. Interrupted closeout is retryable and cannot create a
false done.

### Owner recovery and abort

If the leader's session is dead, a new session calls `recover_batch_owner`
(see `references/recovery.md`) with the recorded owner and its generation and a
new generation it generated and wrote to its own ledger first; the call needs no
live member claim and keeps valid receipts. If work must stop, the
owner calls `abort_batch` after releasing all member claims. Abort is durable
and idempotent: it invalidates prepared and final evidence, retains an already
bound final PR as history, detaches recoverable members from the archived batch,
and returns them to ordinary claim/finish. It refuses once any member is `done`.
Use abort as the fallback when recovery is impossible (generation lost with no
Git access to the state branch) or the batch should be redone.

A non-`context` edit of a member (`description`, `acceptance`, `links`, `needs`,
`title`, `priority`, `status`, `not_before`, or any mix with `context`) deletes
that member's receipt and its dependents' receipts; prepare the member again
(re-prepare: its accepted head is already in the integration head, so no new
commit is needed) before finishing. Registered batch metadata cannot be edited
(abort to reassign), and once any member is `done` invalidating edits are
refused. Only a `context` edit (including `context_append`) keeps a receipt.
Serial mode remains the default for unrelated, cross-repo, or
approval-separated work.
