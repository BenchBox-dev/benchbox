# Release

Drive a BenchBox release with the version-branch flow. The authoritative
runbook is `docs/internal/release-guide.md` in the BenchBox repo — read it
before acting; this file adds only agent behavior. If this file and the
guide disagree, the guide wins.

## Authorization

One release request authorizes the full flow: `make release-cut
VERSION=X.Y.Z` → release PR checks → merge → `make release-finalize
VERSION=X.Y.Z`. Work it end to end; do not stop mid-flow to re-ask for the
finalize step. Stop and ask only when a gate genuinely needs the user
(a required human approval, or evidence that cannot be regenerated).

## Preconditions — check before cutting

Verify, do not assume; each failure has a recovery path in the guide:

1. The latest release-canary run is green, <48h old, and its tested
   `develop` SHA is an ancestor of the intended release head.
2. The cut runs in a clean linked worktree whose HEAD is the freshly fetched
   `origin/develop` commit. `release-cut` enforces this and refuses the primary
   clone, a checkout ahead of or behind that commit, a dirty tree, and a
   `vX.Y.Z` branch or tag that already exists locally or on origin.
3. The required release PR checks pass on the exact PR head before
   finalization.

The three-stage UAT campaign is advisory under the current runbook. Missing,
stale, red, or non-ancestor UAT evidence does not fail `validate-base` or stop
`release-cut`. A future blocking UAT policy needs an explicit runbook change
and rollout on the trusted release base.

## Release notes

Release notes come from `changelog.d/*.json` fragments, never from commit
subjects and never from hand edits to `CHANGELOG.md`, whose `[Unreleased]`
section stays empty. Each pull request adds its own fragment with
`make changelog-entry` (or the internal form, `CHANGELOG_INTERNAL="reason"`,
for a change with no user effect); `make changelog-check` validates it.

Before the cut, `make release-prep VERSION=X.Y.Z` compiles every fragment into
the `## [X.Y.Z]` section, deletes the consumed fragments, and prints a
worksheet and a coverage report. Curate that section: the release gate allows
at most 45 bullets and 160 lines, rejects trailing `(#NNNN)` references and
duplicate `###` headings, and the cut stops while a fragment is pending. Read
"Release notes from fragments" in the guide for the exact rules.

## Hard rules

- `VERSION` is explicit on every target invocation; never guess it.
- Cut and finalize from a linked worktree; both targets refuse the primary
  clone.
- Never bypass or ask to bypass `validate-base`,
  `release-required-result`, or the `release-only` ruleset.
- A failed or interrupted cut is **resumable in place**: re-run `make
  release-cut VERSION=X.Y.Z` from the same worktree, still on `vX.Y.Z`. Resume
  is refused once the branch carries its release commit, once it exists on
  origin, or once fetched `origin/develop` has moved past the starting commit.
  In that last case the branch and its curated files are left intact; preserve
  the authored changelog text before deciding to start over.
  `release-cut-abort` discards only an uncommitted cut and refuses untracked
  files.
- `release-finalize` is resumable after an interrupted merge, tag creation, or
  tag push: re-run the same command. Never retag or force-push.
- Let `release-cut` remove dev-only paths. Do not run `git rm` by hand on the
  release branch. Route classification gaps through
  `scripts/check_release_curation.py` as the guide directs.
- Post-merge and PyPI failures: follow the guide's "Recovering from common
  failures" / "Recovering from a broken PyPI release" sections rather than
  improvising.

## Publication verification

After `release-finalize` pushes the tag, monitor the matching `release.yml`
run through `test-installation`. Confirm the GitHub release and PyPI version
exist before reporting the release complete. If the workflow is still running,
report publication as pending. If it fails, follow the guide's recovery path;
never force-push or replace the tag.

## Report

Version, release PR URL and merge state, tag, finalize result, `release.yml`
run URL and state, GitHub release, PyPI version, installation verification,
and the UAT/canary evidence consulted. If stopped at a gate, name the gate,
the exact blocker, and the resume command.
