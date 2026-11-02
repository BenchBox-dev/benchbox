# Batch delivery modes

BenchBox has two delivery modes. Serial mode is the default for independent,
cross-repository, review-separated, approval-separated, or unrelated items.
Feature delivery mode is opt-in for related items whose prerequisites are
already installed and verified. A source PR, catalog revision, or local mirror
does not by itself install the capability required for feature mode. The
decision to use two layers is recorded in
[`adr-batch-integration-loop.md`](../internal/adr/adr-batch-integration-loop.md);
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
| Member | One agent session per member | One worktree and branch `feat/<batch>--<member>` started at the integration head; implementation, scoped checks, local review, and a landing report |

The leader is the only tracker writer. A member never calls `take`, `prepare`,
`finish`, `release`, or any other tracker write; while the batch is active the
tracker refuses a claim from anyone but the owner (`E_BATCH_OWNER`). A member
that needs a tracker fact asks the leader or reads it. Members never open pull
requests: `make pr-open` and `make pr-fanout` refuse in a member worktree. The
leader's worktree carries `benchbox.batch.role=integration`, a member's
`benchbox.batch.role=member`, set at creation by the batch targets (a
worktree's `benchbox.*` keys are never inherited).

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
| `make batch-start BATCH= MEMBERS= SCOPE_JSON= PROJECT_ID= [WORKTREE_PATH=] [DEPENDENCIES=member=dep1+dep2,...] [CONFIRM_CUTOVER=1]` | Leader, any worktree | Creates `feat/<batch>` and its worktree from `refs/remotes/origin/develop`, writes the ledger and owner generation, and prints the `register_batch` arguments and an `after_register` block of `update_item` steps |
| `make batch-member-create MEMBER= [WORKTREE_PATH=]` | Leader, integration worktree | Creates `feat/<batch>--<member>` at the current integration head; refuses an existing branch |
| `make batch-member-check [TESTS=a,b]` | Member | Requires a clean branch that contains the integration head and has changes; runs `make pr-preflight` against that head (or the command given as `PREFLIGHT_CMD`, which is recorded), the changelog check, and the member review; records the result bound to the base and head |
| `make batch-land MEMBER= [DEPENDENCIES=a,b]` | Leader | Merges a checked member with `--no-ff` and prints the `take`/`prepare` payload (`prepare` only for a dependent member, which must already be taken) |
| `make batch-advance REASON=fix\|develop-merge` | Leader | Records a leader commit or a develop merge and prints the `advance_batch_head` payload |
| `make batch-status` | Leader | Reconciles the ledger with the tracker and Git (rebuilding a missing ledger) and names the next step |
| `make batch-verify` | Leader | Checks first-parent history, recorded heads, and member ancestry |
| `make batch-evidence [SUITE=name]` | Leader | Reruns each required acceptance on the final head, writes `.todo-batch/<batch>/final-evidence.json`, and prints the `finish` payloads |

The scripts only read the tracker. Every tracker write is printed as a payload
and submitted by the leader through the todo-db MCP tools.

## Protocol

### 1. Register (leader)

1. Create or keep the member items. Put batch notes on an umbrella item that is
   not a member.
2. Write the scope file: a JSON object mapping every member, and only members,
   to a non-empty list of path patterns.
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
- runs the mapped tests plus any it names, through `make batch-member-check
  TESTS=...` (non-Python changes must name tests); it does not run the fast tier;
- runs the member review as part of the check. A change that touches only
  exempt paths is recorded `not-reviewed`, the same bar as a standalone PR
  (`docs/internal/oracle-review-v2.md`);
- stops with a landing report: branch, accepted head, base, changed files,
  tests, and review outcome. It does not push for publication, open a PR, or
  touch the tracker.

### 3. Land (leader), one member at a time

1. `make batch-land MEMBER=<id>` refuses unless the member worktree is clean,
   the member contains the current integration head, the head is unchanged since
   its check, the stored review matches that exact patch with an accepted
   outcome (`clear`, `not-reviewed`, or `override`), and every changed path is
   inside the frozen scope. It also refuses a landing that would push the
   combined file list past the hosted brief cap (below).
2. It merges with `--no-ff` and requires the merged tree to equal the member
   tree; anything else means the head moved and the member merges again. It
   reruns the recorded verification of every earlier member whose files
   overlap, prints warnings for suspicious hunks and for a member preflight that
   was not the default command, and only then pushes a backup of the
   integration branch.
3. For an independent member, the leader then calls `take` and `prepare` with
   the printed payload. A member with implementation dependencies must be taken
   before `batch-land` (land refuses otherwise, because the tracker refuses a
   take once the integration head moves past the recorded head); the printed
   payload is then `prepare` only, using the generation from that take.
   `prepare` records the receipt, releases the claim, and leaves the member
   `open`. Claims are held only for this moment.
4. `make batch-status` names the next step. If a merge succeeded but `prepare`
   did not, `batch-status` reports `merged-unprepared`; running `batch-land`
   again prints the payload without merging.
5. If an overlap rerun fails, landing stops with the merge in place and nothing
   pushed. Landing the member again is refused. The only recovery is to reset
   the integration branch to the pre-merge head the error prints, repair the
   member, and land it again; do not prepare it.

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
4. `make pr-arm` runs the batch check only in a worktree whose
   `benchbox.batch.role` is `integration`, and only when
   `extensions.worktreeConfig` is enabled; it refuses on any failure.
   `pr_landing.py ready` with `delivery_mode: batch` reads the file named by
   `batch_evidence_path`. Readiness binds the final head to the tracker, every
   receipt to an ancestor of that head, current acceptance to the final head,
   the changed files to the recomputed diff and the member scopes, and every
   member to a usable review. The batch tooling, `pr_landing`, and `pr_arm`
   refuse a stale tracker read (an offline cache or one without a revision).

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
leader ends at the merged final PR. After it merges, remove the member and
integration worktrees and branches through the normal worktree finish.

## Sizing: the hosted brief cap

`brief_max_bytes` in `.github/oracle-reviewers.yml` is 100000 and is a hard
limit on batch size. A diff over the cap switches the hosted review to file-list
mode, where only hard read-only reviewers that read files run. A file list over
the cap skips the hosted reviewers entirely. `batch-land` refuses a landing that
pushes the combined file list over the cap, warns when it moves the final brief
out of inline mode, and `batch-status` prints the sizes. When a batch would
exceed the cap, split it into batches that each stay under it.

## Rollback and recovery

- **Batch-wide rollback.** The final PR is one squash commit on `develop`, so
  `make trunk-revert` reverts the whole batch in one PR. There is no per-member
  rollback after the squash.
- **Dead leader.** A new session calls `recover_batch_owner` with the recorded
  owner and generation and a new generation it wrote to its own ledger first. It
  needs no live member claim and keeps valid receipts. `make batch-status` shows
  `owner_generation: missing` when the ledger lacks it. Rebuild the ledger with
  `make batch-status`, which reads the tracker and Git.
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
