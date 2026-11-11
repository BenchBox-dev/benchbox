# Release

Drive a BenchBox release through the tag-triggered `release-v2.yml`
workflow. The authoritative runbook is `docs/internal/release-guide.md` in the
BenchBox repo, and the artifact contract is `docs/internal/release-artifacts.md`.
Read both before acting; this file adds only agent behavior. If this file and
the guide disagree, the guide wins.

## Authorization

One release request authorizes the agent part of the flow: the
`make release-prep` pull request, its merge, the pre-tag checks, release
candidate tags (`vX.Y.ZrcN`), the TestPyPI run, and publication verification.
The final `vX.Y.Z` tag and the `pypi` environment approval are owner actions:
prepare them, hand the exact commands to the owner, and stop.

## Flow

1. In a linked worktree: `make release-prep VERSION=X.Y.Z`, curate the
   `## [X.Y.Z]` changelog section, `make release-check VERSION=X.Y.Z
   RELEASE_PRE_TAG=1`, and merge the result as an ordinary pull request to
   `develop`.
2. Wait for the `trunk.yml` push run on the merged commit to finish green with
   a `dist-<sha>-attempt-N` artifact. A commit whose trunk run was replaced
   while waiting, or failed, cannot be released; use a commit that has one.
3. Before tagging, on that exact commit:
   - `make release-check VERSION=X.Y.Z RELEASE_PRE_TAG=1` passes;
   - the latest `release-canary.yml` run is green, under 48 hours old, and ran
     on an ancestor of the commit; report a red or stale canary to the owner
     before tagging.
4. Tag with an annotated PEP 440 tag (`git tag -a vX.Y.Z <sha> -m vX.Y.Z`) and
   push it. A release candidate goes to TestPyPI; a final goes to PyPI after the
   owner approves the `pypi` environment.
5. Follow the `release-v2.yml` run. A final tag may reuse its release
   candidate's UAT evidence (`UAT evidence reuse` job); if it falls back to a
   full UAT run instead, report why before the owner approves.

## Hard rules

- `VERSION` is explicit on every target invocation; never guess it.
- Never rebuild artifacts, retag, move or force-push a published tag.
- Never bypass the release workflow's admission, the ruleset drift check, the
  release UAT gate, or an environment approval.
- Re-run only failed shards for infrastructure failures, record each re-run,
  and stop after one; anything else goes to the owner.
- Post-publication failures: follow the guide's recovery sections rather than
  improvising.

## Publication verification

Confirm, for every published file: the index sha256 equals the admitted
`SHA256SUMS` from the `admitted-<tag>` artifact; `gh attestation verify <file>
--repo BenchBox-dev/benchbox --signer-workflow
BenchBox-dev/benchbox/.github/workflows/release-v2.yml` exits 0 (the signer
match is case-sensitive; use the repository's current lowercase name); and a
`--no-deps` install from the index works. For a final, also confirm the GitHub
release exists. If the workflow is still running, report publication as
pending.

## Report

Version, prep pull request and merge commit, trunk run and dist artifact, tag,
`release-v2.yml` run URL and state, UAT evidence (run or reused run), any
recorded re-runs, index hashes, attestation and install results, GitHub
release, and the canary run consulted. If stopped at a gate, name the gate, the
exact blocker, and the next command.
