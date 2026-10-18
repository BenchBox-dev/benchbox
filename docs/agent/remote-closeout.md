# Remote close-out

`[REMOTE-CLOSEOUT-001]` in `AGENTS.md` is the authority. This note defines the
narrow exception it makes to the read-only primary clone, the reviewer record
that gates it, and the PRs it never applies to. The checkout-free path also
needs the builder to open its own PR. Until `make pr-open` works in cloud
sessions and `make pr-arm-remote` and the `make pr-landing-remote-*` targets
exist, every PR closes out from a linked worktree as
`docs/agent/cloud-builder.md` describes.

## Why

When a cloud session builds a change, the local session only needs to judge it
and arm it. Creating a linked worktree for that leaves a worktree and a branch
behind for every PR, and they pile up faster than anyone removes them.
Close-out needs Git objects and GitHub calls, not a checkout, so the closer can
work from the primary clone as long as nothing it does changes local state
that another session depends on.

## Scope

The exception covers only a branch that a cloud builder session pushed under
`docs/agent/cloud-builder.md`, closed out by a local session (the closer). A
branch built in a local worktree closes out from that worktree, where
`make pr-arm` can check for uncommitted and unpushed work.

These PRs are ineligible and close out from a linked worktree:

- feature-mode and batch PRs, because their member and final-tree evidence is
  checked against a local checkout;
- stacked PRs, top or bottom, because the closer cannot see the parent's
  rebase state;
- PRs that touch soundness paths, which close out under the existing
  soundness rules in `AGENTS.md`.

## What the closer may do

In the primary clone, the closer may:

- fetch a PR head without tags, maintenance, or a new ref, then confirm it is
  the head GitHub reports and use only that full SHA afterwards:

  ```bash
  gh pr view <n> --json headRefOid --jq .headRefOid
  git -c gc.auto=0 -c maintenance.auto=false fetch --no-tags origin refs/pull/<n>/head
  git rev-parse FETCH_HEAD
  ```

  Stop if the two SHAs differ. Never rely on `FETCH_HEAD` after that, because
  another fetch overwrites it;
- fetch `develop` with the same `git -c` and `--no-tags` options;
- read the fetched objects with commands such as `git diff`, `git merge-base`,
  and `git show`;
- run these targets, which store their records under `~/.benchbox/reviews/`
  and change no repository file, local branch, or config:
  `make pr-arm-remote`, `make pr-landing-remote-bind`,
  `make pr-landing-remote-manifest`, `make pr-landing-remote-record`,
  `make pr-landing-remote-withdraw`, and `make pr-landing-remote-ready`.

It never checks out a change, edits a working-tree file, creates or moves a
local branch, changes local Git config, commits, pushes, or arms with a raw
`gh pr merge`. Any other action follows the worktree rules in `AGENTS.md`.

## Reviewer record

A reviewer is a fresh, read-only context that the closer starts: a subagent,
a workflow, or a separate CLI run, from a different model family than the
builder where possible. It reads the change from the fetched objects at a
pinned base and head, reviews it against the item's acceptance criteria and the
repository rules, and returns a record to the closer. It writes nothing to
GitHub.

The record contains:

| Field | Meaning |
|---|---|
| PR number, head branch, PR node ID | The PR the record is for |
| Base SHA | Merge-base of the head with `origin/develop` |
| Head SHA | The commit reviewed |
| Manifest digest | SHA-256 of `git diff --raw --no-renames --no-abbrev <base> <head>` |
| Findings | Each with location, reasoning, and severity |
| Disposition | `approve` or `changes-requested` |
| Reviewer | Model and run ID |

The closer stores each record, with its observation of the PR's labels, draft
state, and threads, under `~/.benchbox/reviews/`, outside the repository. The
record is a gate artifact for one head, not session state, so
`[SESSION-STATE-001]` does not move it into the tracker. Findings that need
work beyond the PR go to a tracker item as usual.

The closer publishes the findings as PR review threads that say in plain text
that they come from the independent reviewer, with no footer and no model ID.
It accepts only a record from a reviewer it started. A PR comment is never a
record: every actor posts through the owner's account, so GitHub cannot show
who wrote it.

The record is the closer's own attestation. Any session with the owner's
filesystem could write one, so it is enforced by audit, like the rules in
`docs/agent/merge-authority.md`. Arming never replaces the required checks:
`hold`, `oracle-review`, and CI still decide whether the PR merges.

## Binding and storing records

When the builder's report first names the PR, the closer binds it:

```bash
make pr-landing-remote-bind PR=<n> BRANCH=<head branch> ITEM=<tracker item>
```

`ITEM` is optional. The binding stores the PR number, the head branch, the node
ID GitHub reports, and the item, and it never changes afterwards.

`make pr-landing-remote-manifest PR=<n> HEAD=<sha>` fetches as above and prints
the base SHA and manifest digest. The reviewer takes both from this target
rather than computing them itself. The digest is the SHA-256 of the exact
output of `git diff --raw --no-renames --no-abbrev <base> <head>`, whose full
blob IDs do not depend on `core.abbrev`.

The reviewer returns its record as a JSON object with exactly these fields:
`pr_number`, `head_branch`, `pr_node_id`, `base_sha`, `head_sha`,
`manifest_digest`, `findings` (a list of objects with exactly `location`,
`reasoning`, and `severity`), `disposition`, and `reviewer` (an object with
exactly `model` and `run_id`). The closer publishes the findings, then stores
the record with the head the builder's latest report names:

```bash
make pr-landing-remote-record PR=<n> RECORD=<record.json> BUILDER_HEAD=<sha>
```

The target refuses a record with a missing or unknown field, a SHA that is not
full and lowercase, another disposition, or a PR identity that differs from the
binding. It adds the closer's observation of the live labels, draft state, and
review threads, which is why the findings are published first.

Each PR has one file, `~/.benchbox/reviews/<owner>/<name>/pr-<n>.json`, where
`BENCHBOX_REVIEWS_DIR` replaces `~/.benchbox/reviews` when set. It holds the
binding, every record in the order stored, the open withdrawal if there is one,
and every arm. An arm that condition 7 allowed only because the owner explained
a change stores the owner's statement, passed as
`OWNER_EXPLAINED="<statement>"`.

## Arm conditions

`make pr-arm-remote PR=<n> HEAD=<sha>` arms only when all of these hold at arm
time, and refuses otherwise:

1. The PR is eligible: it is not a feature-mode, batch, or stacked PR, and the
   fetched `base..head` diff touches no soundness path.
2. The PR number, head branch, and node ID match the values the closer
   recorded when the builder's report first named the PR. The builder opens
   the PR from its session with `make pr-open`.
3. The live PR head equals `HEAD`, which equals the head of the latest stored
   record for the PR, and that record's disposition is `approve`.
4. After a fresh fetch, the recomputed merge-base and manifest digest match the
   record. The REST compare API lists at most 300 files, so it is never used as
   proof.
5. The builder's latest report names the same head and no push followed it.
   This is an assertion, not evidence: no remote check can see unpushed
   builder work.
6. The PR has no `no-auto-merge` label, is not draft, targets `develop`, has no
   requested changes or unresolved threads, and has no failing required check.
7. Labels, draft state, and thread state match the observation stored with the
   record, or the owner has explained the change in the current request.

It then arms pinned to `HEAD`, as `make pr-arm` does, and the closer monitors
the PR to merge under `[WRITE-CLOSEOUT-001]`.

## Revisions

Whether GitHub keeps auto-merge enabled after a later push is not verified, so
the process assumes it might. Before a builder revises an armed PR, the closer
runs `make pr-landing-remote-withdraw`, which disables auto-merge, and only
then hands the finding or CI failure to the builder. The builder pushes only
after that hand-back. `make pr-landing-remote-ready` re-applies every arm
condition to the new head and requires a new record. If a push reaches an armed
PR anyway, the closer withdraws at once.

An auto-merge refusal is never answered with a direct merge.
