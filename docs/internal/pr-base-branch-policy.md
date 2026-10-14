# PR base branch policy

Ready PRs target an integration branch. Stacking is allowed narrowly:

1. A stack belongs to one agent and one tracker item and is at most three PRs
   deep. Agents must not build on another agent's unmerged branch.
2. Every PR above the bottom one is a **draft** that targets its parent
   branch, so it gets CI there. `make pr-open` always targets `develop`, so
   open an upper draft with `gh pr create --draft --base <parent branch>`.
3. When the parent squash-merges, retarget the child to `develop`, rebase it
   onto the squash commit, let CI rerun, and only then arm it (see
   [After a parent merges](#after-a-parent-merges)). `make pr-arm` refuses a
   PR whose base is not `develop`.

## Allowed bases

| Base | When |
| --- | --- |
| `develop` | Normal development PRs |
| `release` | Release-lane PRs only |
| `published-results` | Published-results lane only |

The only other allowed base is the parent branch of a draft PR in a stack. A
ready PR on any other base, including a sibling feature branch, is out of
policy.

## Why stacked bases used to get zero CI

Before the single `ci.yml`, almost every PR workflow filtered on an
integration branch:

```yaml
on:
  pull_request:
    branches: [develop]
```

Other workflows listed `release` or `published-results` instead.

A PR against `fix/parent` triggered **no** required checks. Its page looked
calm (an empty check list), not broken. The change reached `develop` only
when the parent merged, never validated on its own and attributed to the
parent's PR.

`.github/workflows/ci.yml` has **no** `branches:` filter on `pull_request`, so
a PR against any base now gets the six unit results. Units run against a
parent branch validate the child on top of its parent, not the tree that lands
on `develop`. So the rebase onto `develop` and the CI rerun after it are what
gate the merge.

## Loud failure: the `base-guard` job

The `base-guard` job in `.github/workflows/ci.yml` runs on every
`pull_request`. The `tooling` unit `needs` it, so a bad base turns the
required `tooling` check red. It always reports:

- Base is `develop` / `release` / `published-results`: pass in seconds; the
  normal CI lanes apply.
- Any other base, draft PR: pass; the PR cannot be armed until it is
  retargeted at `develop`.
- Any other base, ready PR: fail, with an explicit message to retarget to
  `develop` after the parent merges.

`ci.yml` also listens for `edited`, `ready_for_review` and
`converted_to_draft`, so retargeting a PR or changing its draft state
re-evaluates it. A PR pointed at a feature branch, or a draft marked ready,
must not keep a stale result. On a ready PR against a non-integration base,
`tooling` is red by design while the other five units still report. This is
expected, not a separate defect.

Unit pins live in `tests/unit/workflows/test_stacked_pr_base_guard.py`.

`published-results` carries only its own workflow, so the guard does not run
there. Porting it is a manual maintainer step (see
`docs/internal/results-phase-2-runbook.md`).

## After a parent merges

`develop` is squash-merge only, so a child stacked on a merged parent must be
rebased before it can land:

1. Retarget the child PR to `develop`.
2. Rebase it onto the squash commit with
   `git rebase --onto origin/develop <old parent tip>` and force-push with
   `--force-with-lease` on the feature branch only.
3. Mark it ready for review (`gh pr ready`). `make pr-arm` rejects drafts, and
   the transition re-runs the base guard and starts an oracle review.
4. Wait for CI on the new head. For a soundness-path change, also wait for an
   oracle success review on the rebased head. The oracle does not review
   drafts or PRs based on a parent branch, so this is the change's first
   review.
5. Arm it with `make pr-arm`.

In a stack three deep (`A <- B <- C`), rewriting `B` leaves `C` on the old
`B` tip. Immediately after force-pushing `B`, rebase `C` onto the new tip
with `git rebase --onto <new B tip> <old B tip>` and force-push it, so `C`
always sits on its parent's current tip. When `B` later squash-merges, the
`<old parent tip>` is `C`'s base at that moment, which isolates `C`'s own
commits.

## "No checks" is not one failure mode

Use the REST `mergeable_state` terms from `docs/internal/pr-triage.md`.
GraphQL `mergeable: CONFLICTING` is the same situation as REST `dirty`.

| Symptom | Cause | What to do |
| --- | --- | --- |
| Guard red on a ready PR; other lanes absent | Base is not an integration branch | Retarget to `develop` (or the correct lane base), or convert to a draft if it is an upper stack PR |
| `mergeable_state: dirty` (GraphQL `mergeable: CONFLICTING`) | Conflicts with the base tip | Rebase/resolve onto the base tip |
| Required checks missing/stuck on an integration base; `mergeable_state: blocked` | CI unfinished, path gate, or ruleset | Inspect check runs — do not retarget |

Do not treat an empty check list on a feature base as "CI is fine", or as the
same problem as `dirty` (conflicts) or `blocked` (unfinished gates) on
`develop`.

On a conflicting PR, GitHub cannot build the merge ref, so no `pull_request`
workflow starts, including `ci.yml`, and no unit reports. Diagnose with
`gh pr view <N> --json mergeable` plus the expected check names. Do not infer
conflicts, or a filter bug, from an empty or partial check list alone.

## Agent checklist

- Ordinary PRs created with `make pr-open` or `gh pr create` target `develop`
  unless the change is explicitly for `release` or `published-results`.
  Parent-based drafts follow the exception below.
- Open a PR with `--base` set to another feature branch only as a draft in a
  stack (one agent, one tracker item, depth 3 or less). Never arm it before
  it is retargeted at `develop`.
- If `base-guard` fails on a ready PR, fix the base. Do not try to "add CI" to
  the stacked base by editing branch filters.
- Short agent-facing summary: `AGENTS.md` → section **Verification and
  close-out** (stacking rule).
