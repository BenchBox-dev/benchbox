# Batch tracker contract (BenchBox side)

BenchBox uses tractable as the canonical tracker. This document records the
consumer boundary for serial and prepared feature batches; the implementation
and API contract live in tractable (ADR 0012, batch v2), not in BenchBox or its
generated skill mirrors. The loop that uses this contract is in
[`batch-feature-delivery.md`](batch-feature-delivery.md).

## Serial workflow

- A serial item follows the ordinary `take` → scoped implementation → verify →
  commit → PR → `finish` flow with exclusive claims per principal. Tracker
  dependencies remain tracker-managed.
- `.todo-batch/` is a local ignored ledger and is never committed.

## Prepared delivery contract (v2)

The runtime must advertise the registered-batch v2 capability: `show_batch`,
`advance_batch_head`, and `recover_batch_owner` in the tool registry, and
`show_batch` reporting `capability.version` 2 or later, receipt schema
`prepared_work_v2`, and a supported store schema of 4. Key on the capability,
not on a package version string. A runtime without it keeps the work in serial
mode.

The leader is the only tracker writer. Members never call a tracker write for
batch items, and while the batch is active the tracker refuses a claim by
anyone but the owner (`E_BATCH_OWNER`). The leader's calls, in order:

1. Create or keep the items, then `register_batch` (with
   `confirm_schema3_cutover` on the first registration of an older store, and
   `develop_ref` set to `refs/remotes/origin/develop`), then `update_item` with
   batch metadata and `needs` replaced wholesale (current needs plus every
   implementation dependency). Metadata before registration is refused.
2. After each landing, `prepare` the member: an independent member is taken
   after landing, but a member with implementation dependencies is taken
   before landing, because the tracker refuses its take once the integration
   head moves. The receipt records
   the batch and member, the source worktree and revisions, the base the member
   merged, the accepted head, the integration head it was integrated at, the
   frozen scope hash, the changed files computed from that base, any overlap
   with earlier members or leader commits, and passed bounded-suite evidence.
   `prepare` releases the claim and leaves the member `open`.
3. For any commit on the integration branch outside a member's range (a review
   fix or a merge of `develop`), `advance_batch_head` with the owner generation,
   the expected previous head, the new head, the reason (`fix` or
   `develop-merge`), and the exact rename-free changed files.
4. After the final PR opens, `bind_batch_pr` with the PR's own repository,
   number, node id, and head. After every head advance, `bind_batch_pr` again
   with the same PR identity, the new head, and `expected_previous_head`.
5. After the PR checks pass on the bound head, `take` then `finish` each member
   with cumulative current-tree evidence, including a current-acceptance rerun
   of every member's receipt command on the final head.

Same-file overlap is allowed and recorded; byte equality of an earlier member's
files is not required. Every earlier member named in an overlap, and every
member touched by a leader fix, must re-prove its own recorded verification on
the final tree. `finish` refuses without that evidence (`E_FINAL_EVIDENCE`). A
merge of `develop` adds no requirement.

Only an explicitly registered same-batch implementation edge may consume a
valid prepared receipt. Ordinary, cross-repository, review, approval, merge,
deployment, and soak dependencies remain done-only. Prepared is evidence of
delivery readiness, not completion: final member verification runs on the
combined integration tree, one final PR binds all members, and closeout retains
exact-tree and attribution evidence.

Edits that keep a receipt: `context` and `context_append`. An edit of
`description`, `acceptance`, `links`, `needs`, or any other non-context field
deletes the member's receipt and its dependents'; prepare it again (its
accepted head is already in the integration head). Registered batch metadata
cannot be edited, and invalidating edits are refused once any member is `done`.

The ledger under `.todo-batch/<batch>/` is the leader's rebuildable cache. It
holds the owner generation, which no tracker tool serves back and which
`uv run -- python scripts/batch_integration.py owner-generation` prints only on request. `show_batch` and
`tractable batch show` (read-only; page every section named in `data.sections`)
are the authoritative reads.

## Recovery

- **Dead leader:** `recover_batch_owner` with the recorded owner and generation
  and a new generation the caller wrote to its own ledger first. It needs no live
  member claim and keeps valid receipts. With the optional all-or-nothing
  `expected_integration_worktree`, `expected_integration_head` and
  `integration_worktree` it also relocates the integration checkout (runtime
  1.0.0 or later advertises `features.integration_worktree_relocation`);
  receipts are never rewritten and old-location final evidence is refused.
- **Abort:** the owner releases every member claim and calls `abort_batch`. It
  invalidates prepared and final evidence, keeps a bound final PR as history,
  and returns recoverable members to ordinary claim and finish. It refuses once
  any member is `done`. It is the fallback when recovery is impossible.
- **Rollback:** the final PR is one squash commit, so `make trunk-revert` reverts
  the whole batch. Do not downgrade a live store in place; restore from an
  accepted snapshot under tractable's migration and recovery contract.

## Catalog and runtime pins

The canonical `todo` skill text (`skills/todo/references/batch.md` in
skill-sync-skills) describes the same contract generically. BenchBox's
`skill-sync.conf` pins the catalog revision; the tracked `.claude/skills`
mirror and the ignored `.agents/skills` materialization are regenerated from
that pin and never hand-edited. `make skill-sync-check` verifies parity when the
ignored materialization is present. The tracker runtime is the tractable wheel
vendored under `_project/scripts/vendor/` and pinned in
`_project/scripts/pyproject.toml`; scripts call it with
`uv run --project _project/scripts --locked -- tractable`.

Upgrading the vendored wheel changes every site that records its version or hash:

- `_project/scripts/pyproject.toml` (the `tractable` path source)
- `_project/scripts/uv.lock`
- `EXPECTED_VERSION` in `_project/scripts/todo_state_contract_check.py`
- `_project/analysis/batch-rollout-evidence.json`
- the `external` entry for the wheel path in `quality/comment-policy.json`
- `TRACTABLE_VERSION` and `WHEEL_SHA256` in `tests/integration/worktree/test_batch_skill_delivery.py`

Changing the catalog pin touches these sites, in this order:

1. `rev` in `skill-sync.conf` for the group that lists `skill = todo`
2. the tracked mirror under `.claude/skills`, regenerated by running `make skill-sync` in a write worktree
3. `_project/analysis/batch-rollout-evidence.json`: `catalog.merge_revision`, `catalog.parent_revision`, `catalog.pr`,
   `benchbox_pin.before` and `benchbox_pin.after`, both `mirrors.*.receipt_revision` values, and every changed file
   hash under `mirrors.tracked_target.files` and `mirrors.agents_target.files`
4. `DELIVERY_CATALOG_REV` in `tests/integration/worktree/test_batch_skill_delivery.py`

`make skill-integrity-check` runs `scripts/skill_sync_ci_policy.py validate`, which reports a stale evidence file
for `benchbox_pin.after`, `catalog.merge_revision`, `mirrors.tracked_target.receipt_revision` and the tracked file hashes
without waiting for the integration test. It never rewrites the evidence.

After the upgrade merges, run `uv sync --project _project/scripts --locked` in the primary clone, restart the
tractable MCP server, and reinstall any globally installed tractable tool with the `[mcp]` extra
(`tractable[mcp]`, as `_project/scripts/pyproject.toml` requires). Dropping the extra has broken the MCP server for several sessions.
