# Batch delivery modes

BenchBox has two delivery modes. An explicitly assigned cohesive batch uses
feature delivery mode by default once its prerequisites are installed and
verified. Serial mode covers standalone, cross-repository, review-separated,
approval-separated, or unrelated items. A source PR, catalog revision, or local mirror
does not by itself install the capability required for feature mode. The
decision to use two layers is recorded in
[`batch-integration-loop-2026-11-01.md`](../../_project/decisions/batch-integration-loop-2026-11-01.md);
the tracker side is in [`batch-tracker-contract.md`](batch-tracker-contract.md).

## Serial mode

Use the ordinary claim, implementation, isolated-worktree, verification, review,
merge, deployment, and authority boundaries. Tracker dependencies remain
tracker-managed, and a shared branch must not be used to make independent work
look related.

## Feature delivery mode

Before entering this mode, verify from the active MCP server (not a source
receipt or narrative handoff) that the registered-batch v2 capability is
present: `show_batch`, `advance_batch_head`, and `recover_batch_owner` are in the
tool registry, and `show_batch` reports `capability.version` 2 or later with a
supported store schema. If it is absent, retain serial mode and record the owned
blocker. Prerequisite work that installs or upgrades the capability, or changes
the loop itself, is always delivered serially and cannot use feature mode to
prove itself.

The first `register_batch` on a store below schema 4 performs the one-step
cutover and locks out older clients. Confirm it (`CONFIRM_CUTOVER=1`) only when
develop pins the v2 runtime, `list_items` shows no live claim held by another
worker, and an older client has been shown to refuse with `E_SCHEMA`.

### Two layers

| Layer | Who | Owns |
|---|---|---|
| Leader | One agent session | The batch registration, the one integration branch `feat/<batch>` and its worktree, landing each member, the tracker, the ledger, and the one final PR to `develop` |
| Member | One agent session per member | One worktree and branch `feat/<batch>--<member>` started at the integration head; implementation, scoped checks, and a landing report |

The leader is the only tracker writer. A member never calls `take`, `prepare`,
`finish`, `release`, or any other tracker write; while the batch is active the
tracker refuses a claim from anyone but the owner (`E_BATCH_OWNER`). A member
that needs a tracker fact asks the leader or reads it. Members never open pull
requests: `make pr-open` and `make pr-fanout` refuse in a member worktree. The
leader's worktree carries `benchbox.batch.role=integration`, a member's
`benchbox.batch.role=member`, set at creation by the batch targets (a
worktree's `benchbox.*` keys are never inherited).

The owner check works only when identities differ. tractable derives a worker's
identity from its MCP server process, and subagents share their parent's
server. So each leader runs as its own top-level session (or sets its own
`TRACTABLE_ACTOR`), and a member never runs as a subagent of its leader's session
or of another leader's. Parallel leaders that share one identity also share its
single live-claim slot and block each other's `take`.

Rules that hold for the whole batch:

- The integration branch is never a PR base. The one final PR is the whole
  integration branch against `develop`, reviewed and merged as one change.
- Cloud builders are not members. A cloud session builds one change on its own
  branch and PR (`cloud-builder.md`); it never works on a member or integration
  branch.
- No new CI skips, protected-branch bypasses, credential changes, or
  publication shortcuts. Feature mode never weakens hosted/native review, merge,
  deployment, or authority gates, hold labels, the unresolved-thread refusal, or
  the oracle gate.
- A member that merged in the integration head is a normal branch. Member
  scopes are frozen at registration; a change outside a member's scope is
  refused at landing, and a leader fix cannot widen the union of scopes.
- Batch notes (order, decisions, the current state of the whole batch) go on an
  umbrella item that is never a batch member (`handoffs.md`).

### Commands

| Target | Run by | Does |
|---|---|---|
| `make batch-start BATCH= MEMBERS= SCOPE_JSON= PROJECT_ID= [WORKTREE_PATH=] [DEPENDENCIES=member=dep1+dep2,...] [CONFIRM_CUTOVER=1]` | Leader, any worktree | Refuses fewer than two members (one member is serial work). Appends `changelog.d/*` and `docs/internal/dev-loop-property-ledger.md` to every member's scope before hashing it. Creates `feat/<batch>` and its worktree from `refs/remotes/origin/develop`, writes the ledger and owner generation, and prints the `register_batch` arguments and an `after_register` block of `update_item` steps |
| `make batch-member-create MEMBER= [WORKTREE_PATH=]` | Leader, integration worktree | Creates `feat/<batch>--<member>` at the current integration head; refuses an existing branch, and refuses while the primary clone has uncommitted tracked changes (the files are named) |
| `make batch-member-check [TESTS=a,b]` | Member | Requires a clean branch that contains the integration head and has changes, no uncommitted tracked change anywhere in the primary clone, and no change there under the member's frozen scope (the files are named); refuses a changed path outside the member's frozen scope (the paths are named), with the message `batch-land` uses; runs `make pr-preflight` against that head (or the command given as `PREFLIGHT_CMD`, which is recorded and warned about at landing) and the changelog check, then executes the union of the mapped and named tests and records success only if that command passes and the checkout stays clean at the same head; runs no review: the leader reviews the exact patch at landing |
| `make batch-land MEMBER= [ACCEPT_WRITES=op_id,...]` | Leader | Merges a checked member with `--no-ff` and prints the `take`/`prepare` payload (`prepare` only for a dependent member, which must already be taken); the member's implementation dependencies are read from its registered tracker metadata |
| `make batch-advance REASON=fix\|develop-merge` | Leader | Records a leader commit or a develop merge and prints the `advance_batch_head` payload |
| `make batch-status` | Leader | Reconciles the ledger with the tracker and Git (rebuilding a missing ledger) and names the next step |
| `make batch-verify` | Leader | Checks first-parent history, recorded heads, and member ancestry |
| `make batch-evidence` | Leader | Executes every member's receipt command afresh on the clean final head, writes `.todo-batch/<batch>/final-evidence.json` only after all pass, and prints the `finish` payloads with suite `member-reruns`; a test path a receipt names that a recorded `develop-merge` deleted is dropped from that rerun and listed under `dropped_receipt_paths` in the evidence (a path a member itself deleted still fails, and a receipt left with no verification path is refused) |
| `make batch-finish [EXECUTE=1]` | Leader, integration worktree | After the batch is completed and final PR merged: dry run by default; execute removes qualifying member worktrees/branches, then finishes the integration worktree and local branch last |

The scripts only read the tracker. Every tracker write is printed as a payload
and submitted by the leader through the tractable MCP tools.

## Protocol

### 1. Register (leader)

1. Create or keep the member items. Put batch notes on an umbrella item that is
   not a member.
2. Write the scope file: a JSON object mapping every member, and only members,
   to a non-empty list of path patterns. A batch has at least two members;
   one member is serial work. `batch-start` adds the member's changelog fragment
   and the property ledger to every scope.
3. Run `make batch-start`. It refuses unless `origin/develop` is fetched.
4. Submit `register_batch` with the printed arguments (the first one on an older
   store also carries the cutover confirmation). Registration records one
   repository identity, the owner generation, the integration branch and
   worktree, the start head, `develop_ref`, the ordered members, the frozen
   scopes and their hash, the delivery boundary, and the intended terminal
   outcome. It refuses a member another worker holds a live claim on.
5. Run the `after_register` steps that `batch-start` printed, in order, after
   `register_batch`: one `update_item` per member with the batch metadata
   (`batch={batch_id, member_id, implementation_dependencies}`) and `needs`
   replaced wholesale with the current needs plus every implementation
   dependency. Pass dependencies as `DEPENDENCIES=member=dep1+dep2,...`. On a
   store below schema 4, metadata attached before registration is refused
   (`E_SCHEMA`). A new item takes the same `batch` through `create_item`.

The owner generation is a credential that no tool serves back. It lives only in
the leader's ledger.

### 2. Dispatch and implement (members)

The leader runs `make batch-member-create` for each member that may start and
hands over the item, the scope, and the member rules below. Members run in
parallel. Independence is declared by the frozen scopes and the registered
implementation edges, not discovered at merge time. A dependent member is
dispatched from an integration head that already contains its dependency.

A member:

- implements inside its scope and writes one changelog fragment for its change
  (see `changelog.d/README.md`);
- merges the current integration branch into its branch whenever the head moved;
- runs the union of mapped tests and any it names, through `make batch-member-check
  TESTS=...` (non-Python changes must name tests); a named test that fails or
  collects nothing blocks the check; it does not run the fast tier;
- runs no review. The leader reviews the exact patch at landing, where a change
  that touches only exempt paths is recorded `not-reviewed`, the same bar as a
  standalone PR (`docs/internal/oracle-review-v2.md`);
- stops with a landing report: branch, accepted head, base, changed files,
  and tests. It does not push for publication, open a PR, or
  touch the tracker.

### 3. Land (leader), one member at a time

1. `make batch-land MEMBER=<id>` refuses unless the member worktree is clean,
   the member contains the current integration head, the head is unchanged since
   its check, and every changed path is inside the frozen scope. It also refuses
   a landing the Theseus preflight finds not reviewable, and prints its reasons
   (below). It also refuses a primary clone with uncommitted changes under the
   member's scope, and any tracker write on the member item from its worktree
   creation (or the batch start, for a worktree created before this check)
   through its first landing, other than `take`, `release`, `renew`, `recover`,
   and the leader's recorded `prepare`. Both guards run again just before the
   merge, and the tracker guard runs once more right after it: a member write
   that arrives during the merge undoes the merge and refuses the landing.
   Inspect each listed write with `show_item`; land again with
   `ACCEPT_WRITES=<op_id,...>` to record the reviewed writes in the ledger,
   where later landings of the same member reuse them. The window is bounded
   by tracker order, not only by clock time: writes already on the member item
   when `batch-member-create` ran, and writes after the last one the
   landing's closing check saw, are never counted, so leader writes in the
   same second as either boundary do not block a landing. A `take` from a
   member that shares the leader's identity cannot be told apart from the
   leader's own, which is why identities must differ.
2. The leader runs the trusted local reviewer for the exact captured base and
   head, with the reviewer code and policy pinned to the same trusted
   `origin/develop` commit (which must not predate the batch start). Only an
   accepted outcome (`clear`, `not-reviewed`, or `override`) from that
   invocation lands; the member's own result, config, or claimed outcome never
   does. The accepted result is retained in the receipt.
3. It merges with `--no-ff` and requires the merged tree to equal the member
   tree; anything else means the head moved and the member merges again. It
   reruns the recorded verification of every earlier member whose files
   overlap, prints warnings for suspicious hunks and for a member preflight that
   was not the default command, and only then pushes a backup of the
   integration branch.
3. For an independent member, the leader then calls `take` and `prepare` with
   the printed payload. A member with implementation dependencies must be taken
   before `batch-land`; land reads the member's registered implementation
   dependencies from the tracker and refuses an untaken dependent, because the
   tracker refuses a take once the integration head moves past the recorded
   head. The printed payload is then `prepare` only, using the generation from
   that take.
   `prepare` records the receipt, releases the claim, and leaves the member
   `open`. Claims are held only for this moment.
4. `make batch-status` names the next step. If a merge succeeded but `prepare`
   did not, `batch-status` reports `merged-unprepared`; running `batch-land`
   again recomputes the affected overlap reruns before printing the payload,
   without merging again. A missing ledger row is never proof that a rerun
   passed. A member merge that `batch-land` did not make, or whose ledger entry
   was lost, is prepared only after the same checks as a normal landing (merged
   tree equals the member tree, scope, recorded base, trusted commit on
   `develop`, leader review), and the receipt binds to that merge commit.
   `batch-status` reports an unverifiable merge as "do not prepare it".
5. If an overlap rerun fails, landing stops with the merge in place and nothing
   pushed. The only recovery is to reset the integration branch to the
   pre-merge head the error prints, repair the member, and land it again; do not
   prepare it.

Every commit on the integration branch outside a member's own range must be
recorded (`batch-advance`) before the next `prepare`, or the tracker refuses
(`E_BATCH_HEAD`).

### 4. Publish (leader)

1. Run the fast tier once on the integration tree (`make pr-preflight-fast-tests`,
   which takes the shared test lock) and `make pr-preflight` once. Members run
   only mapped and named tests; the leader runs the fast tier once for the
   combined change.
2. `make pr-open` from the integration worktree, then `bind_batch_pr` with the
   PR's repository, number, node id, and head.
3. `make batch-evidence` (it refuses unless the PR is bound at the integration
   head) writes the evidence the readiness check reads.
4. `make pr-arm`, `make pr-ready` and the remote arming targets discover batch
   membership from the tracker before every arm, reading the registry at a
   pinned state-branch commit and matching the PR's repository, head branch,
   number and node id. A registered batch PR needs full batch evidence whatever
   the caller declares: worktree markers, `delivery_mode: serial`, or an
   omitted adapter argument cannot turn it into an ordinary PR. An ordinary PR
   skips batch evidence, but a tracker outage or an unreadable tracker
   configuration refuses every arm rather than guessing. `BATCH=` asserts the
   PR is a registered batch and refuses otherwise.
   `pr_landing.py ready` with `delivery_mode: batch` reads the file named by
   `batch_evidence_path`. Readiness binds the final head to the tracker, every
   receipt to an ancestor of that head, the fresh final-run results to the final
   head, the changed files to the recomputed diff and the member scopes, and
   every member to the review retained in its receipt. Every tracker page must
   be fresh, carry the summary's revision, and advance; a stale, cached or
   mismatched page is refused.

### 5. Review fixes and develop merges (leader)

A review fix after the PR opens is a leader commit on the integration branch,
never a member re-landing:

1. Commit the fix in the integration worktree.
2. `make batch-advance REASON=fix`. It confirms the fix stays inside the union
   of member scopes, reruns the recorded verification of each member whose files
   it touched, and prints the `advance_batch_head` payload. Submit it.
3. Push, then `bind_batch_pr` with the new head and `expected_previous_head` set
   to the old one. The binding moves with the head; evidence for the old head is
   stale, so run `make batch-evidence` again.

`make batch-advance REASON=develop-merge` merges `origin/develop` with a real
merge commit and records it. It is the way to refresh the branch; a rebase is
not, because recorded heads must stay ancestors. Heads cannot move once any
member is `done`.

### 6. Finish (leader)

After the final PR is green on the bound head, the leader runs the acceptance
reruns (`make batch-evidence`), then `take` and `finish` each member with the
printed payload (`finish` carries the cumulative current-tree evidence). Finish
follows the PR's merge decision; an open or green PR is not proof of merge. The
leader ends at the merged final PR.

After every member is `done`, the batch is completed, and the final PR has
merged, run `make batch-finish` from the integration worktree. It is a dry run
unless `EXECUTE=1`. It removes a clean member worktree and deletes a member
branch (and its remote branch if it matches) only when the branch tip equals the
accepted head in the member's receipt and that head is an ancestor of the merged
final PR's `refs/pull/<N>/head`, fetched in the same run; otherwise the branch is
kept and the reason printed. This basis proves nothing is lost, not integration
(`_project/specs/worktree-lifecycle-evidence-contract.md`, section 7.1).
Last, only after the final PR has merged and every member branch is deleted or
already gone, batch-finish invokes the normal section 7 worktree finish for the
integration worktree and local branch. It reports the finish status in dry runs
and freshly revalidates the completed batch, merged PR and finish evidence before
applying. Refused members or a held integration worktree block this final step.
The final PR's repository may be recorded as an `owner/name` slug or a GitHub URL,
in any letter case and with or without `.git`; it is compared to `origin` in
canonical form. The command exits 0 exactly when its report says `"ok": true`.

## Sizing: the Theseus preflight

Theseus owns how large a change may be and still be reviewed; BenchBox keeps no
size constants. `theseus preflight --policy <policy> --base-ref <ref> --head
<sha> --json` answers whether the change against `origin/develop` can be
reviewed: exit 0 is reviewable, 3 is not, 2 is undetermined (for example the
model catalog cannot be read). `batch-land`, `batch-advance` (both `fix` and
`develop-merge`), `batch-status` and `batch-evidence` run it through the verified
Theseus install. Landing, advancing and evidence refuse on exit 3 or 2 and print
Theseus's reasons verbatim; status prints the answer and refuses nothing.
Refusals include a landing that carries a Theseus lock change (land that on its
own pull request) and a change too large for any reviewer's capacity (split the
batch). Advisories from a reviewable answer print as warnings. Run the preflight
on the members' combined change before `register_batch` (or `make batch-status`
once members exist); do not estimate.

## Rollback and recovery

- **Batch-wide rollback.** The final PR is one squash commit on `develop`, so
  `make trunk-revert` reverts the whole batch in one PR. There is no per-member
  rollback after the squash.
- **Dead leader.** From a clean checkout on the integration branch at the
  recorded integration head, run `make batch-recover EXPECTED_OWNER=<worker>
  EXPECTED_OWNER_GENERATION=<gen> REASON=<why>`. It writes a fresh generation to
  the local ledger first, then prints the `recover_batch_owner` payload. It needs
  no live member claim and keeps valid receipts. It also takes over the session
  ownership of the integration worktree and every member worktree that still
  exists, so the successor commits there without `BENCHBOX_WORKTREE_TAKEOVER`;
  each takeover is recorded under `handoffs` in the ledger and printed as
  `worktree_takeover`. `make batch-status` shows
  `owner_generation: missing` when the ledger lacks it, and rebuilds a missing
  ledger from the tracker and Git.
- **Relocated leader.** If the original integration checkout is gone, add
  `RELOCATE=1` from another clean clone whose origin matches the registered
  repository. The tracker moves the integration worktree only when the expected
  old path and integration head still match (runtime capability
  `features.integration_worktree_relocation`). Recovery does not move the head,
  reprepare members, or rebind the PR; prepared receipts and finished members
  are unchanged, and evidence written at the old path is refused. Regenerate it
  with `make batch-evidence` at the new path. Work that was never pushed or
  recorded is not recoverable this way; report it rather than recreating it.
- **Aborted batch.** After `abort_batch`, `make pr-arm` and the landing readiness check
  follow the tracker: a worktree still carrying `benchbox.batch.role=integration`
  for an `aborted` batch is armed as an ordinary PR.
- **Abort fallback.** If recovery is impossible (the generation is lost and
  nobody has access to the state branch) or the batch should be redone, the owner
  releases every member claim and calls `abort_batch`. Abort invalidates
  prepared and final evidence, keeps an already bound final PR as history, and
  returns recoverable members to ordinary claim and finish. It refuses once any
  member is `done`. Use previews and explicit reversible operations; never delete
  prepared records or clean up destructively as recovery.
- **Edits that invalidate.** Editing a member's `description`, `acceptance`,
  `links`, `needs`, or other non-context fields deletes its receipt and its
  dependents' receipts; prepare it again before finishing. `context` and
  `context_append` edits keep receipts. Registered batch metadata cannot be
  edited; abort to reassign.
- **Moved evidence.** A moved head, member, scope, dependency, or final tree
  invalidates the affected evidence and requires revalidation. Conflicts go to
  the leader or a maintainer; they are not silently absorbed.

## Decision record

The process a past batch ran under is kept as history in
`_project/decisions/batch-process-improvements-base-correction-2026-09-08.md`.
