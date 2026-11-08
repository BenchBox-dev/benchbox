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

## State and recovery material

Record the exact named TODO set and current delivery state in the owning tracker
items before claiming work. Follow the repository's state-storage policy; a local
handoff file is not required. For a registered feature batch, use its designated
umbrella item for batch-wide progress.

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
2. Record missing or inconsistent prerequisites as a concrete blocker in the item.
   Use only supported `update_item` fields; put the reason in its context.
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

Renew a claim when necessary. Before an ordinary task's default-identity MCP
server reconnects, checkpoint and release its claim; after reconnect, confirm the
bound store and runtime, then take the item again. An identity-preserving restart
requires a unique stable actor and stopping its previous server first. Never
share an actor across concurrent servers or terminate unrelated clients.

## Context rotation and resume

At a substantial-item boundary, checkpoint the tracker and use a fresh worker
context when supported. Preserve the exact TODO set and authoritative references,
not source dumps or a second session-state document.

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

Repository policy selects prepared delivery; it is not inherently opt-in.
Unrelated, cross-repository and approval-separated work remains serial. Changes
that install or repair the batch mechanism cannot certify themselves through it.

Before registration, require the installed runtime's `tractable_batch` version 2
capability, the expected receipt schema, the required tools, and a supported store
schema. Inspect actual initialize/read responses, not a package-version string
or a static rollout document. If unsupported, use serial mode. An older store's
first registration needs the confirmed schema cutover under the existing
quiescence rules; never reinterpret legacy receipts or create another cutover
merely to adopt a consumer change.

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
  (`--actor`/`TRACTABLE_ACTOR`). A member that needs a tracker fact asks
  the leader or reads it (`show_item`, `show_batch`).
- The leader claims a member (`take`) only around its own landing, prepare,
  or finish, and releases or prepares it immediately. A member with
  implementation edges is taken just before its landing, because the tracker
  refuses that `take` once the integration head moves past the recorded head;
  every other member is taken after its landing. Do not hold member claims
  while members are being implemented: one live claim per worker, and
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

The consumer runs affected earlier members' own recorded commands before
preparing a normal or reconstructed landing, and reruns touched members for
leader fixes. Derive these checks from receipts, never from the presence or
absence of saved ledger failures. Repeat them after interruption.

Final verification is stronger than the tracker's overlap minimum: execute
**every** member's own receipt command on the final integration tree, plus the
combined checks. Preserve the overlap metadata; no byte-equality rule may reject
legitimate later evolution or substitute for behavioral acceptance.

### Member closeout

A member commits its implementation and merges the latest recorded integration
head, resolves conflicts and commits before checking the clean captured range.
It runs scoped checks and local review against that exact post-merge base/head,
then reports branch, base, accepted head, changed files and actual verification.
It neither publishes a PR nor writes the tracker.

### Landing (leader)

For each landing request, one member at a time, in dependency order:

1. For a member with implementation edges, `take` it now and keep its claim
   generation for preparation. Independently capture its base and accepted head;
   check cleanliness, unchanged head and frozen scope before expensive review.
2. The leader invokes the trusted reviewer for that exact range. Pin executable
   and policy to the same full trusted revision, bounded by the registered start
   and fetched integration-base history. Consume that invocation's fresh result;
   a member's config or claimed `clear` is not authorization. Require matching
   base/head/trusted revision and an accepted outcome. Existing history/cache
   carry and adjudication remain valid; exempt-only `not-reviewed` is not a claim
   that a model reviewed the patch.
3. Merge the captured accepted OID, never a moving branch name, with a merge
   commit whose tree equals the member tree. A moved integration base requires
   the member to merge and recheck it first.
4. Execute the receipt-derived overlap checks. Only after they pass, `take` an
   independent member if needed and `prepare` its real source checkout/range,
   accepted and integration heads, scope, dependencies and passed verification.
   Retain the leader-accepted structured review in `verification.review`.
   Preparation releases the claim and leaves the member `open`.
5. For a merge interrupted before preparation, prove its parents/tree/range and
   repeat leader review and overlap checks. Confirm the current owner and renew
   or re-take the member, retaining its current generation for `prepare`, without
   merging again. A missing ledger row is never proof that a check passed.

The receipt is the historical review record. Final evidence and arming validate
its binding without reopening a separate member-result cache or recomputing the
old review environment from the current checkout. Keep ordinary review findings,
disputes and outcome caching; do not introduce signatures or a second attestation
store under the cooperative same-user trust model.

Every commit on the integration branch outside a member's own range (a leader
fix, a merge of the integration base) must be recorded first with
`advance_batch_head`, or the next `prepare` is refused (`E_BATCH_HEAD`).
Interruption between the merge and `prepare` is recovered in
`references/recovery.md`.

### Publish and review

Use one canonical sequence:

1. On the combined clean head, run repository-required prepublication preflight
   and combined local review. Do not add a separate full fast-tier ritual when
   final verification owns that run.
2. Publish the one final PR and bind its repository, number, node ID and exact
   integration head with `bind_batch_pr`.
3. Run final verification: every member's receipt command and the combined fast
   tier execute on the captured clean head. Check HEAD and cleanliness before
   and after commands, then revalidate relevant receipt/ownership/PR bindings.
   Invalidate an earlier usable success at the start of every new invocation;
   publish success atomically only after all required commands pass. Keep
   prepublication preflight separate rather than claiming or repeating it here.
4. Before every arming route, discover registered batch membership from an
   authoritative snapshot independent of local markers or caller delivery mode.
   A true ordinary PR skips full batch evidence, not discovery. Offline,
   malformed or ambiguous discovery refuses. Validate every summary and
   continuation page as authoritative at the same revision, with bounded
   advancing continuations. Never relabel cached data as live.
5. For a batch, require the successful final run, every receipt/review binding,
   scope/ancestry and the current final-PR identity/head, plus unchanged hosted
   review, CI, review and authority gates.

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

Recover a dead owner with its privately obtained expected generation and a new
generation saved before the call. Without the expected generation, neither
recovery nor abort is possible; use existing authorized private recovery access
or report that exact missing prerequisite, never invent a token.

Checkout relocation additionally requires
`tractable_batch.features.integration_worktree_relocation=true` and the complete
tuple `expected_integration_worktree`, `expected_integration_head`,
`integration_worktree`. CAS owner/generation/path/head together; prove a clean
destination in the registered repository/branch at that head. Audit the change,
preserve historical receipts and source paths, and produce fresh new-path final
evidence. A same-head active partial closeout can relocate without reopening
finished members or gaining advance, reprepare or rebind rights.

Do not retrofit legacy review metadata into a new historical result. A partially
closed legacy batch needs pinned pre-switch closeout at its frozen head before
replacement, or an honest missing-evidence blocker.

An owner with the required generation may abort after releasing every member
claim, but never after any member is done. Abort invalidates prepared/final
evidence, keeps the bound PR as history and returns nonterminal members to the
ordinary lifecycle; it is not a tokenless recovery bypass.

A non-`context` edit of a member (`description`, `acceptance`, `links`, `needs`,
`title`, `priority`, `status`, `not_before`, or any mix with `context`) deletes
that member's receipt and its dependents' receipts; prepare the member again
(re-prepare: its accepted head is already in the integration head, so no new
commit is needed) before finishing. Registered batch metadata cannot be edited
(abort to reassign), and once any member is `done` invalidating edits are
refused. Only a `context` edit (including `context_append`) keeps a receipt.
Serial mode remains the default for unrelated, cross-repo, or
approval-separated work.
