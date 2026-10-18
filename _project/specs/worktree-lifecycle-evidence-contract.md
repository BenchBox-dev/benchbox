# BenchBox worktree lifecycle evidence contract

Status: draft specification (2026-08-26) as amended 2026-08-31 to authorize
`make branch-prune-merged` — an explicit, operator-driven, `gh`-based reaper
limited to worktree-less local branches still at their historical merge-time PR head —
and amended 2026-10-18 to authorize `make worktree-reap`, an operator-invoked
sweep whose removals are exactly `worktree-finish` apply actions; see §2.

## 1. Problem

Linked-worktree lifecycle today is exact-path and operator-driven:
`make worktree-create` / `make worktree-remove`
(`scripts/worktree_lifecycle.sh`, `make/worktrees.mk`) create and remove one
named worktree at a time, and `worktree-remove` already refuses dirty,
locked, detached, missing, unregistered, and primary-clone targets (see
`docs/operations/dev-loop-worktrees.md`). Per
`docs/development/makefile-architecture.md`, `make/worktree-maintenance.mk`
intentionally holds unrelated reporting targets (`blind-spots-*`,
`soundness-drain-*`) — "worktree cleanup remains exact-path and
operator-driven" — so there is no existing durable record of *which* linked
worktrees carry finished, integrated work versus active work.

This spec fixes what evidence is recorded, what it can and cannot prove, how
uncertainty is classified, who is allowed to produce which part of it, and
what a report is (and is not) authorized to do. The read-only audit adds no
deletion authority. The separately invoked `branch-prune-merged` exception in
§2 adds a destructive local-branch path only after independently recollecting
the complete §7 evidence; it never consumes an audit snapshot as authority.

## 2. Non-goals

- No automatic worktree cleanup. Worktree removal stays operator-driven via
  `make worktree-remove`, `make worktree-finish APPLY=1`, or
  `make worktree-reap APPLY=1` (`docs/internal/dev-loop-worktrees.md`).
  `worktree-reap` previews by default and removes only worktrees for which a
  fresh per-target `worktree-finish` evaluation is actionable and whose HEAD is
  exactly the merged PR's historical merge-time head, using the same apply
  actions; it never removes the primary clone, the invoking worktree, or
  anything that evaluation holds. The authorized branch-only exception is
  `make branch-prune-merged`, which reaps only worktree-less local branches
  still at their historically proven merge-time PR head (operator-driven, `gh`-based, with
  `DRY_RUN=1` preview).
- No change to `worktree-remove`'s existing refusal rules, and no destructive
  Git operation beyond `worktree-finish` apply actions (also reached through
  `worktree-reap`) and `branch-prune-merged` local branch ref deletion.
- No parsing of `.bossmode/control.db` (or any other worktree's copy of it),
  anywhere, by any future phase built on this contract.
- No binding to Bossmode's current `bossmode reconcile` / `run show` /
  `turn show` JSON output as if it were a stability-guaranteed contract —
  that output is operational CLI output today, not a published schema.
- No design of the future Bossmode JSON contract itself. This spec only
  states what BenchBox requires of it before consuming it (§8).

## 3. Scope and identity

Applies to linked worktrees of this repository. The canonical identity is
the GitHub REST/GraphQL repository `id` (a stable numeric/node id), resolved
at evidence-collection time — **not** a hardcoded `owner/name` string.
`BenchBox` was transferred from `joeharris76/BenchBox` to
`BenchBox-dev/BenchBox` on 2026-08-22
(`_project/decisions/github-org-transfer-benchbox-dev-2026-08-21.md`); the
old path now redirects. A contract that pinned `joeharris76/BenchBox` as
"the" repository identity would already be wrong today, and nothing stops a
second transfer. Every evidence record MUST resolve identity by API `id`
(cross-checked against the `git remote` URL as a plausibility check, never
as the source of truth) and MUST NOT assume the current `owner/name` is
permanent.

When querying GitHub APIs, the collector resolves the stable numeric/node `id`
to the currently observed `owner/name` to formulate the request URL. It records
the exact list endpoint that supplied each PR (including query parameters and
page), then validates that the returned payload's repository identity matches
the expected repository `id` before using the response.

## 4. Evidence model (schema-versioned)

An evidence record is a JSON object, one per linked worktree, produced by a
read-only collector. Every record carries a top-level `schema_version`
integer. A consumer that does not recognize `schema_version` MUST refuse to
interpret the record (§5) rather than guess a compatible shape — the same
posture `todo_db.py` takes on a registry schema newer than the CLI
understands (`_project/specs/todo-db-tracker.md`).

```json
{
  "schema_version": 1,
  "generated_at": "2026-08-26T00:00:00Z",
  "repository": {
    "id": 123456789,
    "full_name_observed": "BenchBox-dev/BenchBox",
    "resolved_via": "gh api /repos/{owner}/{repo} --jq .id"
  },
  "worktree": {
    "path": "/Users/joe/Developer/BenchBox.wt-fix-example",
    "registered": true,
    "path_exists": true,
    "is_detached": false,
    "is_locked": false,
    "lock_reason": null,
    "is_dirty": false,
    "branch": "fix/example",
    "head_sha": "abc123..."
  },
  "integration_evidence": [
    {
      "target_branch": "develop",
      "pr_number": 1914,
      "pr_head_sha": "abc123...",
      "pr_base_ref": "develop",
      "pr_merged": true,
      "merge_commit_sha": "def456...",
      "merge_commit_reachable_from_target_tip": true,
      "checked_at": "2026-08-26T00:00:00Z",
      "source": "GET https://api.github.com/repos/{owner}/{repo}/pulls?head={owner}%3Afix%2Fexample&state=all&per_page=100&page=1"
    }
  ],
  "task_run_evidence": {
    "available": false,
    "reason": "no published Bossmode public JSON contract yet (see §8)"
  },
  "classification": {
    "state": "uncertain",
    "reason": "clean and gone-upstream are signals, not proof of integration",
    "signals": ["clean", "gone-upstream"]
  }
}
```

Rules:

- `worktree` models Git state as independent, orthogonal boolean and nullable
  fields rather than a collapsed single enum:
  - `registered`: boolean — whether listed in `git worktree list --porcelain`.
  - `path_exists`: boolean — whether the worktree directory currently exists on disk.
  - `is_detached`: boolean — whether `HEAD` is detached (no symbolic ref).
  - `is_locked`: boolean — whether `git worktree lock` is active.
  - `lock_reason`: string or null — reason provided when locked.
  - `is_dirty`: boolean — whether untracked files, staged changes, or uncommitted modifications exist.
  - `branch`: string or null — the active branch name (null if detached).
  - `head_sha`: string or null — commit SHA at `HEAD` (null if path is missing or unreadable).
  Every consumer and classifier MUST evaluate all of these independent flags;
  no flag may mask or supersede another.
- `integration_evidence` is a list because a worktree can have zero, one, or
  (rebased/retargeted) more than one PR in its history; an empty list is
  valid and means "no merged-PR evidence found," never "not needed."
- `task_run_evidence` is always present and defaults to `available: false`
  with an explicit reason until §8's contract exists. A record MUST NOT
  omit this field or infer it from anything other than that future contract.
- Records are self-contained: a consumer must be able to interpret one
  record without a live Git checkout or GitHub session, for later replay
  and review.
- Future schema changes are additive-first (new optional fields bump a
  minor convention informally; a field removal or meaning change bumps
  `schema_version`), mirroring the todo-db tracker's `SCHEMA_VERSION`
  precedent.

## 5. Fail-closed states

`classification.state` is one of exactly three values:

| State | Meaning | Allowed to imply removal-safe? |
| --- | --- | --- |
| `uncertain` | The default. Evidence collection completed successfully, but collected evidence is only a signal, not proof of current integration, or no merged PR was found for the branch. | No |
| `verified-integrated` | §7's exact merged-PR evidence was found and validated for the worktree's current branch head against at least one structural branch, and no dirty/locked/unregistered/detached git state contradicts it. | No — see §9; even this state is not deletion authority |
| `unavailable` | A required evidence source could not be reached, returned an error, had unreached pagination/truncation, produced an ambiguous parse, or had a schema_version mismatch. | No |

There is no fourth state and no default that means "safe." Any collector
error, timeout, truncated collection / unreached pagination, ambiguous parse,
or unrecognized `schema_version` MUST resolve to `unavailable`, never silently
to `uncertain` or `verified-integrated`. A missing or malformed field inside a
record makes that record `unavailable` as a whole; partial trust in a
corrupt or incomplete record is not permitted.

## 6. Ownership and uncertainty classification

`clean`, `old`, `gone-upstream`, and `lease-expired` are **signals** that MAY
be recorded under `classification.signals`. None of them, alone or combined,
may set `classification.state` to anything but `uncertain` (or
`unavailable`, if collecting them failed):

- **`clean`** (`is_dirty: false`) — a worktree between edits, or one
  whose owner paused mid-task, looks identical to one that is done. Clean is
  not evidence of completion.
- **`old`** (worktree or branch age past some threshold) — long-running
  investigations, blocked-on-review branches, and paused work all age the
  same way finished work does. Age is not evidence of abandonment.
- **`gone-upstream`** (the branch's remote tracking ref reports `[gone]`) —
  true after a squash merge, but equally true after a manual remote branch
  deletion, a force-push rename, or a rebase workflow that never used this
  remote branch at all. `[gone]` alone does not distinguish "squash-merged
  and safe" from "remote housekeeping happened for an unrelated reason."
  This is exactly why §7 requires PR API evidence instead of trusting the
  tracking-ref state.
- **`lease-expired`** (a Bossmode task/run lease has passed its TTL) — a
  lease timeout means Bossmode stopped hearing from a worker, not that the
  worker's work was integrated or worthless. Bossmode owns whether to retry,
  reassign, or archive that task (§8); BenchBox never treats a lease
  timeout as license to touch the worktree or branch.

Any future report or tool built on this contract must classify uncertainty
explicitly and require corroborating integration evidence (§7) before a
human operator is even shown a worktree as a removal candidate. This mirrors
the tracker item's anti-pattern list directly: none of these four signals is
abandonment.

## 7. Structural branches and exact merged-PR evidence

The structural branches are `develop`, `release`, and `published-results`
(`docs/development/pr-base-branch-policy.md` — the only allowed PR bases;
`develop` is squash-merge only). A worktree's branch counts as
integration-evidenced against one of these **only** when all of the
following hold for at least one PR:

1. **Exact PR head and current branch contents** — Proving integration requires
   establishing that the worktree's *current* contents have been merged, not
   merely that *some* historical commit on the branch was once merged.
   All of the following MUST hold:
   1. **Historical PR head match**: `pr_head_sha` matches a commit that was the
      tip of the worktree's branch when the PR was merged — not merely "some
      commit reachable from the branch today," since force-push and rebase can
      change history after the PR closed. That match MUST be established in
      this precedence order, stopping at the first source that resolves it:
      1. **PR event history** (the GitHub PR API's own head-ref timeline) —
         authoritative and durable for as long as the PR exists, independent
         of the local clone's state. Check this source first. A destructive
         branch operation MUST additionally corroborate the final timeline
         head against the PR's paginated commit list and verify that the
         timeline's merged event names the same merge commit as the PR record.
         After rechecking worktree attachment, deletion MUST use an atomic
         expected-old-OID ref update so a concurrent branch change is refused
         rather than deleting the newly written commit.
      2. **Local reflog** — used only when PR event history is unavailable or
         inconclusive. The reflog is local-only and is pruned by `git gc`,
         by ordinary reflog expiry, or simply absent on a fresh clone, so it
         is a fallback, never the primary source; its absence proves nothing
         on its own.

      The former third source, "the collector's own observation," is removed
      because it named no concrete, checkable artifact: a collector run
      observing `worktree.head_sha == pr_head_sha` at collection time is not
      proof the PR's head was ever that commit — it is only today's tip, and
      still requires corroboration from (1) or (2).

      If neither source resolves the match, point 1.1 is unresolved for that
      PR/branch pair, and the record MUST stay `uncertain` (or `unavailable` if
      collection failed) for that branch.
   2. **Current head equality or complete descendant integration**:
      The worktree's current head commit (`worktree.head_sha`) MUST equal
      `pr_head_sha`, OR every commit on the branch between `pr_head_sha` and
      `worktree.head_sha` (e.g. post-merge commits added to the branch) MUST
      also be proven integrated into the target structural branch. If
      `worktree.head_sha != pr_head_sha` and unintegrated descendant commits
      exist on the branch, the worktree contains unintegrated work; the record
      MUST stay `uncertain` — never `verified-integrated`.
2. **Exact base** — `pr_base_ref` is exactly `develop`, `release`, or
   `published-results`. A PR opened against a feature branch is out of
   policy (`pr-base-branch-policy.md`) and is never evidence of integration
   into a structural branch.
3. **Merged state** — the PR API reports `merged: true` (not merely
   `state: closed`; a closed-without-merge PR is not integration evidence).
4. **Reachable merge commit** — `merge_commit_sha` is an ancestor of the
   target structural branch's current tip
   (`git merge-base --is-ancestor <merge_commit_sha> <target_tip>`, or the
   equivalent GitHub compare-API check).

**Never use source-tip ancestry.** Checking whether the worktree branch's
own tip commit is an ancestor of `develop` (`git merge-base --is-ancestor
<branch_tip> develop`) is not valid proof and must never be used to
establish or refute integration. A squash merge's commit on the base branch
has the base branch's prior tip as its only parent — the source branch's
commits are, by construction, never ancestors of that squash commit. A
branch can be **fully integrated** and still fail a source-tip-ancestry
check, and (after a force-push or rebase) a branch tip can coincidentally
become an unrelated ancestor of `develop` without ever having been merged.
Both failure directions are why step 4 checks the **merge commit's**
reachability, sourced from the PR record, rather than the branch tip's.

Absence of PR evidence when collection completes fully is `uncertain`, not
`unmerged` and not `abandoned`. If collection was truncated or incomplete
(e.g., paginated results could not be fully retrieved due to API limits or
network errors), the state MUST resolve to `unavailable` (§5), not
`uncertain`. Only a positive, exact match under points 1–4, with no dirty,
locked, detached, unregistered, or missing worktree state, may set
`classification.state` to `verified-integrated`; every other complete
outcome for that branch stays `uncertain`.

## 8. Controller-provider boundary

Two systems, two ownership domains, one non-negotiable boundary:

- **BenchBox owns repository policy and Git mutation.** Structural branch
  identity, PR base/merge rules, and every current or future worktree
  create/remove operation are BenchBox's alone. Bossmode never mutates Git,
  never opens a worktree, and never decides what "safe to remove" means.
- **Bossmode owns task and run evidence.** Whether a task succeeded, which
  run produced it, lease/TTL state, and evaluation outcomes are Bossmode's
  domain (`.claude/skills/bossmode/SKILL.md`), recorded in its own registry
  (`.bossmode/control.db`, one per checkout, not a shared worktree file per
  that skill's own "Registry Ownership" rules).

The boundary is enforced by the transport, not by intent:

- BenchBox MUST NOT open, query, or otherwise parse any `control.db` file,
  under any filename or path, in this spec or any future phase built on it.
  This holds even for a read-only connection and even from a different
  checkout's copy — the Bossmode skill itself already forbids cross-worktree
  `--db` access for Bossmode's own CLI, and BenchBox has no standing to do
  what Bossmode's own tooling refuses to do.
- BenchBox MAY consume Bossmode task/run evidence only through a **future
  public JSON contract**: a schema that Bossmode publishes with an explicit
  version, a documented stability guarantee, and a compatibility policy for
  breaking changes. It does not exist yet. Today's `bossmode reconcile` /
  `run show` / `turn show` output is real JSON, but it is CLI output for a
  human or an agent operating Bossmode interactively — it carries no
  `schema_version`, no published stability guarantee, and no compatibility
  promise, so it is a draft shape, not the contract. `task_run_evidence`
  (§4) stays `available: false` until Bossmode ships and documents that
  contract, and no future phase may treat the current CLI JSON shape as an
  early or de facto version of it.
- Until that contract exists, every evidence record's ownership picture is
  necessarily Git-only (§7). That is an accepted, explicit gap — not a
  reason to reach for `control.db` as a shortcut.

## 9. Reports are timestamped snapshots, never deletion authority

A report is a point-in-time collection of evidence records, written to a
local, gitignored location (mirroring `_project/verification-logs/`'s
"capture outside git, keep only durable summaries tracked" convention —
`_project/reports/worktree-lifecycle/<UTC-timestamp>.json`, one file per
run, never overwritten). Every report carries the same `generated_at`
timestamp as its records and an explicit, non-optional marker:

```json
"report_authority": {
  "is_deletion_authority": false,
  "note": "A snapshot describes state observed at generated_at. It expires the instant new commits, pushes, PR activity, or task/run events occur. No automation may treat any record in this file as license to act; an operator reads it and, if they choose to act, re-verifies the specific worktree by hand immediately before acting."
}
```

This mirrors `_project/release-evidence/README.md`'s existing separation:
evidence is machine-readable and reviewed, but the **operator** commits or
acts on it deliberately, and the tool that produced it never writes to the
Git tree or acts on its own output. A worktree-lifecycle report has no
committed form at all in this contract (nothing here authorizes adding one)
— it is strictly a local, disposable, re-runnable snapshot. `verified-integrated`
from a report generated an hour ago is not evidence for a decision made now;
the report format itself does not distinguish "stale by one minute" from
"stale by one month," and no future phase may add an implicit
freshness/authority tier to it without revising this document.

## 10. Fixture matrix

Fixture repositories are synthetic, local, disposable Git repos built at
test time — the same pattern `tests/integration/worktree/test_worktree_release_dirty_guard.py`
already uses (`init_repo_with_origin(tmp_path)`: a working repository, with
a bare clone of that same repository added back as its `origin` remote,
entirely under `tmp_path`, no network, no real GitHub call).
This contract extends that pattern with named fixture *builders*, each one
producing one worktree state plus, where relevant, a canned PR-API JSON
fixture (never a live `gh api` call) so §5–§7's logic is testable
deterministically and offline.

| Fixture builder | Git/worktree state it covers | Evidence-model state exercised |
| --- | --- | --- |
| `fixture_clean_active_worktree` | Registered (`registered: true`), clean (`is_dirty: false`), attached (`is_detached: false`), unlocked (`is_locked: false`), path present (`path_exists: true`), branch present, no PR yet | `uncertain` (signal: `clean`, no integration evidence) |
| `fixture_dirty_worktree` | Registered, uncommitted changes present (`is_dirty: true`) | `uncertain`; also exercises `worktree-remove`'s existing dirty refusal as a boundary check |
| `fixture_locked_worktree` | Registered, `git worktree lock` held (`is_locked: true`, `lock_reason: "reason"`) | `uncertain`; exercises the locked-refusal boundary |
| `fixture_detached_worktree` | Registered, detached `HEAD` (`is_detached: true`, `branch: null`) | `uncertain`; exercises the detached-refusal boundary |
| `fixture_missing_directory` | Registered in `git worktree list` but path no longer exists on disk (`path_exists: false`) | `unavailable` (collector cannot inspect git state) |
| `fixture_unregistered_path` | An arbitrary directory that is not a registered worktree at all (`registered: false`) | `unavailable`; also the exact-path-only precedent `worktree-remove` already enforces |
| `fixture_post_merge_commits_unintegrated` | Branch was merged via PR, but new unintegrated commits exist at tip (`worktree.head_sha != pr_head_sha`) | `uncertain` (proves post-merge commits on a previously merged branch keep state `uncertain`) |
| `fixture_gone_upstream_squash_merged` | Branch tracking ref reports `[gone]`; a canned PR JSON fixture shows `merged: true`, matching current head (`worktree.head_sha == pr_head_sha`), base `develop`, and a `merge_commit_sha` that **is** reachable from the fixture `develop` tip | `verified-integrated` for `develop` — the one fixture that should pass §7 in full |
| `fixture_gone_upstream_no_pr_evidence` | Branch tracking ref reports `[gone]`, but complete PR query finds no matching PR fixture (simulates manual remote branch deletion) | `uncertain` — proves `[gone]` alone never yields `verified-integrated` |
| `fixture_gone_upstream_unmerged_pr` | Branch tracking ref reports `[gone]`; canned PR fixture shows `merged: false`, `state: closed` (closed without merge) | `uncertain` — proves closed-not-merged is rejected |
| `fixture_wrong_base_pr` | Canned PR fixture has exact head match but `base.ref` is a feature branch, not a structural branch | `uncertain` — proves an out-of-policy base is rejected even with a real merge |
| `fixture_squash_source_tip_not_ancestor` | Same as `fixture_gone_upstream_squash_merged`, but additionally asserts `git merge-base --is-ancestor <branch_tip> develop` is **false** while the record is still `verified-integrated` | Regression fixture for the anti-pattern itself: proves the contract does not (and structurally cannot) depend on source-tip ancestry |
| `fixture_merge_commit_not_yet_reachable` | Canned PR fixture shows `merged: true` and a `merge_commit_sha`, but the fixture `develop` tip predates it (simulates a stale local fetch) | `uncertain` (point 4 of §7 fails) — never `verified-integrated` on stale local refs |
| `fixture_old_but_active_worktree` | Registered, clean, branch commit dated far in the past, no PR | `uncertain` (signal: `old`) — proves age alone never elevates state |
| `fixture_lease_expired_task` | Paired with any of the above worktree fixtures; `task_run_evidence.available` is forced `true` only inside the fixture harness with a stub payload carrying a lease-expired status | `uncertain` (signal: `lease-expired`); this fixture also documents that no real code path may produce `task_run_evidence.available: true` until §8's contract ships — it exists to test the classifier's handling of the shape, not to exercise a live integration |
| `fixture_schema_version_mismatch` | A hand-built evidence record with `schema_version: 999` (or missing) | `unavailable` — proves fail-closed schema handling (§5) |
| `fixture_incomplete_collection_error` | Collector hits GitHub API error, rate limit, timeout, or unreached pagination truncation | `unavailable` — proves incomplete collection resolves to `unavailable` rather than `uncertain` (§5) |
| `fixture_primary_clone` | The fixture's own primary/bare "origin" checkout, never a linked worktree | Out of scope for every state above; asserts the collector refuses to emit a record for it at all, mirroring `worktree-remove`'s primary-clone refusal |

Each fixture is a builder function returning a ready `tmp_path`-rooted repo
(and, where noted, a canned JSON PR-API fixture file alongside it) — not a
persistent repository committed anywhere in this tree. No fixture, harness,
or future test in this matrix opens `control.db`, calls the real GitHub API,
or calls the real Bossmode CLI; `fixture_lease_expired_task` stubs the
shape of a future §8 payload without asserting it is real.

## 11. Follow-ups (explicitly out of scope here)

- The Bossmode public JSON contract itself (schema, versioning policy,
  publication mechanism) — owned by Bossmode, tracked separately.
- Any collector implementation (the script/module that walks
  `git worktree list --porcelain`, calls the GitHub API, and writes §4
  records) and the fixture builders in §10 — this spec defines what they
  must produce and must never do, not their code.
- Any report-viewing surface (CLI output, dashboard) — §9 defines the
  artifact's authority, not its presentation.
- Any future single-target, operator-driven mutation command that might
  read a report and ask an operator to confirm removal — explicitly
  deferred; this item creates no such path and takes no position on its
  design.
- A committed convention for reports (this spec keeps them local/gitignored
  only, per §9); revisiting that is a separate, explicit decision.

## Assumptions

- "Fixture repositories" (success criterion 2) means local, disposable test
  fixtures in the existing `tests/integration/worktree/` idiom, not
  persistent named repositories checked into the tree or hosted on GitHub.
  This matches the only existing precedent found
  (`init_repo_with_origin` in `test_worktree_release_dirty_guard.py`).
- The repository-identity discrepancy in §3 (task brief named
  `joeharris76/BenchBox`; the live `origin` remote and the 2026-08-22
  transfer decision both show `BenchBox-dev/BenchBox`) is resolved in favor
  of the verified current state, with the transfer decision cited as
  evidence, per `[AUTH-PROVENANCE-001]`'s instruction to classify and state
  a requirement's source when a stale instruction conflicts with verified
  repository policy.
