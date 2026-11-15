# Batch implementation

Use `batch` to implement an explicit named TODO set in dependency order. It
authorizes implementation, branches, commits, and PRs only for that set;
auto-merge is allowed only when CI, not human approval, is the integration gate.
It does not bypass repository, tracker, review, or publication gates.

## Before you start

1. Read the repository instructions, `references/implement.md`,
   `shared-change-framework/SKILL.md`, and `shared-review-protocol/SKILL.md` as
   the next action requires.
2. Confirm the bound store as SKILL.md requires, then
   `list_items(ready_only=true)`. Fix or report any failure and stop before
   claiming or editing. With multiple linked worktrees, give each worktree its
   own client session or an explicit `--repo-root` (one server instance per
   worktree).
3. Confirm the exact TODO set, dependencies, integration branch, and existing
   claims, branches, worktrees, and PRs. Reconcile rather than duplicate work.
4. Run any required repository write preflight; fix or report failure and stop.

## State and recovery material

Before claiming work, record the exact named TODO set and current delivery
state in the owning tracker items, or in the item the repository designates for
batch-wide progress. Follow the repository's state-storage policy.

Repository tooling may maintain an ignored operational ledger. Use that tooling's
path and JSON schema rather than inventing a second text ledger. Only the leader
writes it. Git and the tracker remain authoritative for heads, receipts, scope,
PRs and lifecycle; cached run rows never authorize preparation or acceptance.

Keep claim and batch-owner generations private. Save a new owner generation
before registration or recovery. `show_batch` does not return it, so do not claim
that a deleted credential can be rebuilt from that read. Recover it only through
the existing authorized private ledger or Git-state access; never put generations
or raw registry contents into public progress notes.

A progress checkpoint names the item, tracker state, branch/worktree, PR/head,
verification actually exercised, blocker and exact next action. A member has no
PR of its own; its landing and integration head replace that field. Revalidate
the relevant live facts before acting on a checkpoint.

## Schedule bounded work

In serial mode, run serially; prepared delivery runs members in parallel (see
Prepared feature delivery). A substantial TODO needs its own implementation, verification,
and review cycle; give it a fresh worker context when supported. Tightly coupled
trivial items may share a context while their combined history remains small.

At each scheduler boundary:

1. Read the owning tracker checkpoint and refresh the state needed to select work.
2. Record missing or inconsistent prerequisites as a concrete blocker in the
   item's `context`.
3. Select an item only when its dependencies, including required integration
   merges, are satisfied, tracker state permits claim or resume, and no existing
   branch, worktree, or PR must be reconciled first.
4. Start or resume it with `references/implement.md` in a fresh isolated linked
   worktree from the current integration branch. Refresh after dependency merges
   and remove that clean worktree after merge under repository policy.
5. After one focused retry, classify externally resolvable failures as waiting
   and non-resolvable failures as blocked; record the owner and next action.

When all eligible work waits on external gates, follow the repository's closeout
contract. Use scheduled checks or a bounded blocking watcher, not a busy polling
loop. Pending CI alone is not permission to abandon a required merged-PR closeout.

## Worker closeout

In prepared delivery a member ends at a landing request (see Prepared feature
delivery), not a PR. Otherwise follow implementation, verification, commit, PR,
and completion contracts by reference. Before commit or PR, run the required `shared-review-protocol`
review, resolve Critical and Required findings, disposition optional findings
under repository policy, and re-verify substantive fixes.

Do not return full task bodies, diffs, logs, or source dumps. Keep long output
in a temporary artifact and return only its result, short tail, link, or digest.
Return the checkpoint described above:

- after successful item closeout;
- before intentionally ending or compacting a worker context; or
- after a failure that requires later resumption.

Record the checkpoint before selecting more work; add no per-operation
acknowledgements. After an unexpected end, inspect authoritative live state
before retrying an operation with an unknown outcome.

For serial items, close only at the repository's authorized terminal state. A
dependent item waits for the prerequisite's actual required merge or release;
an open or green PR is not proof of either.

## CI and external waits

Reuse recent gate observations and query only the state needed for the next
action. Monitor pending checks through the repository's supported watcher or
schedule. Fix real failures and retry spurious ejections without bypassing a gate.
Keep verbose output outside context and checkpoint pending/failing names, links
and next actions in the tracker.

Renew a claim when necessary. Follow SKILL.md for reconnects and restarts, and
never terminate another session's MCP client.

## Context rotation and resume

A resumed context reads that checkpoint, confirms the requested set, revalidates
the live facts required by the next action, and continues. Do not replay completed
items or treat an old gate snapshot as current evidence.

## Final report

Report one concise row per item:

```text
TODO | tracker state | PR/state | head | verification | blocker | next action
```

Include tracker references and any repository-permitted private ledger path.
Distinguish merged, released, activated and tracker-complete work. A genuine
blocker needs its owner and exact recovery action, not an invented pass.

## Prepared feature delivery

Repository policy selects prepared delivery. Unrelated, cross-repository, and
approval-separated work stays serial. A change that installs or repairs the
batch mechanism cannot certify itself through it.

Before registration, read the live initialize or `show_batch` response and
require the `tractable_batch` version 2 capability, the expected receipt schema,
the required tools, and a supported store schema, never a package version
string. If any is missing, use serial mode. Never reinterpret legacy receipts.

The loop has two layers. The **leader** registers the batch, owns the one
integration branch and worktree, lands each member, records it in the tracker,
and ships the one final PR. **Members** implement in their own worktrees and
stop at a landing request. Follow the repository's batch protocol for the
concrete commands; this guide names only the tracker calls and the contract.

### Roles

- The leader is the only tracker and ledger writer (role boundaries:
  SKILL.md). A member that needs a tracker fact asks the leader or reads
  `show_item`/`show_batch`.
- The leader claims a member (`take`) only around its own landing, prepare,
  or finish, and releases or prepares it immediately. A member with
  implementation edges is taken just before its landing, because the tracker
  refuses that `take` once the integration head moves past the recorded head;
  every other member is taken after its landing. Do not hold member claims
  while members are being implemented: a worker holds one live claim per
  batch, and `abort_batch` needs every claim released.
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
   Tell the member that every file-tool call (read, edit, write) must use an
   absolute path inside its own worktree: a relative path resolves against the
   primary clone, not the worktree.

### Verification and same-file overlap

Members execute the union of mapped and explicitly named checks on their captured
clean base/head before recording success. Named checks supplement mapped checks.
A command or suite label is not execution evidence; failures and unexecuted
requested acceptance cannot become a test pass. Label any permitted lint-only
verification accurately.

Declare legitimate same-file overlap in the frozen scope. `prepare` records
overlaps against earlier members and intervening fixes, and the tracker derives
its minimum `acceptance_required` set from those receipts and the integration
log. It validates current-acceptance bindings, not command execution.

Before preparing any landing, normal or reconstructed, the leader reruns the
recorded commands of every earlier member the receipts list as overlapping, and
reruns touched members after a leader fix. Take the list from receipts, not the
ledger, and repeat it after an interruption.

Final verification is stronger than the tracker's overlap minimum: it runs
**every** member's receipt command plus the combined checks on the final
integration tree.

### Member closeout

A member commits its implementation and merges the latest recorded integration
head, resolves conflicts and commits before checking the clean captured range.
It runs scoped checks against that exact post-merge base/head, then reports
branch, base, accepted head, changed files and actual verification. A member
review is not required: the leader's trusted review at landing is the one that
counts. Run one only where the repository's protocol asks for it, as early
feedback the leader does not trust.
It neither publishes a PR nor writes the tracker.

### Landing (leader)

For each landing request, one member at a time, in dependency order:

1. For a member with implementation edges, `take` it now and keep its claim
   generation for preparation. Independently capture its base and accepted head;
   check cleanliness, unchanged head and frozen scope before expensive review.
2. Review the exact captured base..head with the repository's trusted
   reviewer, run by the leader. A member's own config or claimed result is not
   authorization. Require an accepted outcome (`clear`, `not-reviewed`, or
   `override`) bound to that base and head.
3. Merge the captured accepted OID, never a moving branch name, with a merge
   commit whose tree equals the member tree. A moved integration base requires
   the member to merge and recheck it first.
4. Execute the receipt-derived overlap checks. Only after they pass, `take` an
   independent member if needed and `prepare` its real source checkout/range,
   accepted and integration heads, scope, dependencies and passed verification.
   Retain the leader-accepted structured review in `verification.review`.
   Preparation releases the claim and leaves the member `open`.

The receipt is the review record. Later evidence and arming check its binding;
they do not re-review.

Every commit on the integration branch outside a member's own range (a leader
fix, a merge of the integration base) must be recorded first with
`advance_batch_head`, or the next `prepare` is refused (`E_BATCH_HEAD`).
Interruption between the merge and `prepare` is recovered in
`references/recovery.md`.

### Publish and review

Use one canonical sequence:

1. On the combined clean head, run the repository's prepublication preflight
   and a combined review.
2. Publish the one final PR and bind its repository, number, node ID and exact
   integration head with `bind_batch_pr`.
3. Run final verification on the captured clean head: every member's receipt
   command plus the combined checks. Confirm HEAD and cleanliness before and
   after, then revalidate receipt, ownership, and PR bindings. Record success
   only after every command passes.
4. Before arming auto-merge on any PR, check the tracker for a registered batch
   that owns it. If the tracker is unreachable or the answer is ambiguous, do
   not arm. A batch PR always needs full batch evidence, whatever local markers
   say.
5. For a batch, require the successful final run, every receipt/review binding,
   scope/ancestry and the current final-PR identity/head, plus unchanged hosted
   review, CI, oracle and authority gates.

Review fixes are leader commits, never member re-landings. Withdraw auto-merge
through the existing PR lifecycle before publishing any advance. Commit and
verify the full fix interval; submit `advance_batch_head` with exact endpoint
diff and expected head; only then push the captured head, CAS-rebind the same PR,
run new evidence/review/CI and rearm. Recorded multi-commit fix intervals are
valid; unrecorded gaps or trailing commits are not. Integration-base merges keep
their real two-parent topology and registered `develop_ref` checks.

An exact retry of either call is idempotent. Heads cannot move once any member
is `done`; resume closeout instead.

### Finish

After the bound final PR has **actually merged**, validate the existing successful
final run against current head, checkout, receipt commands, owner epoch and PR
identity/head. If these bindings still match, use that completed run rather than
unconditionally invoking final verification again. Relevant drift or relocation
requires a new invocation, which executes all commands afresh; selective old
member-time cache rows are never sufficient.

For each member, `take` and `finish` with cumulative current-tree evidence mapping
every accepted head/range, frozen scope, integration head and final PR. An open
or green PR is not merge proof. Once all members finish, the batch is completed.
Interrupted closeout remains retryable.

Only after that completed state and fresh merged-PR proof may cleanup remove
member worktrees/branches with clean, nonforeign, unlocked worktrees, accepted
tips proven contained in the merged final PR head, and expected-OID/remote-lease
protection. Remove the leader's integration worktree and local branch **last**,
after member cleanup, using normal worktree-finish proof and the same safety
checks. Never use the member deletion basis or source-tip ancestry alone for
leader cleanup.

### Owner recovery and abort

Dead owner, checkout relocation, abort, and receipt invalidation:
`references/recovery.md`.
