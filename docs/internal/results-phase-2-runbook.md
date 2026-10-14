# Phase 2 Results Operations Runbook

Phase 2 is the PR-based community submission flow for the BenchBox public results
corpus. The product boundary and launch rationale live in
[`docs/internal/benchbox-results-platform-strategy.md`](benchbox-results-platform-strategy.md).
This runbook documents the current operating model only: contributor PRs target
`published-results`, CI validates them, maintainers review them, and the
`BenchBox-dev/benchbox-site` deploy, fed by the core bundle, is the normal path that
rebuilds and deploys the static Explorer; no hosted API is involved.

Authority is split by surface: `develop` owns code, validators, generators,
Explorer admission and presentation policy, the recurring maintainer seed, and the
legacy curated release-preview corpus; `published-results` owns the complete accepted
Phase 2 archive. Each branch generates its inventory from its own tree. Published-only
submissions are expected and are not automatically backported into the legacy curated
Explorer source.

For independent publication, the accepted contract is
[`independent-publication-contract.md`](independent-publication-contract.md): a reviewed
manifest pins an exact `published-results` SHA, and every validator-clean result at that
SHA is publication input by default. Visibility, trust, withdrawal, and ranking
eligibility are orthogonal presentation fields owned with policy on `develop`; they do
not create another corpus authority. A merge to `published-results` proves acceptance,
not live deployment. Only an attested live receipt proves publication.

## 1. Submission Lifecycle

### 1.1 Community submission (Phase 2)

1. Contributor runs `benchbox run ...` and `benchbox submit --output ./submission`.
2. Contributor copies the bundle files plus the generated `<result>.manifest.json` into `results-data/bundles/`.
3. Contributor regenerates `results-data/corpus-inventory.json`.
4. Contributor opens a PR against `published-results`.
5. `Validate Submission` checks schema, hash integrity, timing sanity, and inventory drift.
6. Maintainer reviews, requests fixes if needed, and merges.

### 1.2 Maintainer-run additions (sync from develop)

Maintainer-side corpus changes — the `seed-corpus.yml` workflow's monthly
schedule and on-demand (`workflow_dispatch`) refresh (see
[`corpus-refresh.md`](corpus-refresh.md)), ad-hoc UAT integrations like PR #164, validator updates — land on
`develop` first because that is where the project's tooling and tests live.
The
[`sync-results-data-to-published.yml`](https://github.com/BenchBox-dev/BenchBox/blob/develop/.github/workflows/sync-results-data-to-published.yml)
workflow watches `develop` for changes under the slim-branch allowlist
paths (`results-data/bundles/`, the corpus docs, the two vendored
validators, plus `corpus-inventory.json`) and opens a **draft** PR against
`published-results` mirroring those changes. The mirror PR never
auto-merges — a maintainer reviews and flips it ready when the develop-side
change is ready to surface on the public corpus branch.

**Publication fixed-point gate.** Before building a mirror branch or opening
a PR, the sync workflow runs the same check as
`tests/unit/scripts/test_corpus_privacy_invariant.py::test_rederived_corpus_publishes_byte_identically_to_what_is_stored`
against the develop content at `GITHUB_SHA` (the bytes about to be mirrored).
If re-anonymization would rewrite any primary bundle, the run fails loudly and
does **not** open a mirror. That keeps `published-results` from receiving a
corpus that Explorer publication would immediately rewrite, and sequences any
in-flight re-derivation automatically: the mirror waits until develop is at
the fixed point, then proceeds unattended. A red sync run with drift still
present is the expected signal while re-derivation is open — not a stuck
manual gate.

For `results-data/bundles/`, the apply is a **union overlay**, not a wipe:
develop's files overwrite matching paths on `published-results`, and paths
that exist only on `published-results` (accepted archive submissions that were
not separately curated onto `develop`) are left intact. A previous
wipe-then-checkout strategy deleted
those published-only submissions whenever develop was also ahead.
Develop-side *path deletions* do not auto-propagate (a deleted seed path that
still exists only on published-results becomes published-only and is kept).
After overlay, the workflow regenerates `corpus-inventory.json` from the
unioned tree so community-only paths remain inventory-listed. Other allowlist
files (docs, validators) still use per-path checkout / removal. The scheduled
[`corpus-drift-check.yml`](https://github.com/BenchBox-dev/BenchBox/blob/develop/.github/workflows/corpus-drift-check.yml)
canary classifies develop-ahead vs published-only and never recommends a
wipe-based full mirror while published-only paths exist.

#### Public deletion / privacy takedown (intentional path removal)

The union overlay **intentionally does not** propagate develop-side deletions
inside `results-data/bundles/`. If a path is removed on `develop` but still
exists only on `published-results`, it becomes **published-only** and is kept
on the next mirror. That protects community submissions the automated sync
must never wipe.

For a maintainer-approved removal from the public archive, open a hand-created
PR against `published-results` that is **deletion-only**: it may remove the
affected primary bundle, every same-stem `.manifest.json`, `.plans.json`,
`.tuning.json`, and `.applied.json` companion, and regenerate the inventory,
but it must not add or replace bundles. Submission CI rejects a primary
deletion while any of those companions remains. If an affected bundle path
also exists on `develop`, remove it from `develop` first as part of the same
coordinated change; otherwise the next union mirror will copy it back onto
`published-results`. There is no
durable archive exclusion or tombstone mechanism in this workflow. After the
`develop` removal is in place, open the deletion-only `published-results` PR.
PRs #1882 and #1940 are worked examples. During the A0 freeze, do not delete
paths from `published-results` or `develop`; use the presentation withdrawal
and artifact-access suppression procedure below.

Maintainer and seed additions to the public archive land on `develop` first,
then sync through the mirror — never via a hand-opened maintainer PR that
adds or replaces bundles. Community submissions remain hand-opened PRs against
`published-results` as in §1.1. For maintainer/seed content, run:

```bash
gh workflow run sync-results-data-to-published.yml
```

Review and merge the resulting `auto/results-mirror-*` PR. Hand-opened
*maintainer archive-content* PRs against `published-results` must stay
deletion-only; do not use them to add maintainer or seed archive content. A
hand-opened workflow-only PR is the required exception whenever
`.github/workflows/validate-submission.yml` changes on `develop`, because the
mirror token cannot update workflow files. The sync uses a union
overlay so an automated mirror never deletes public content. Its four-signal
waiver for trusted mirror content requires all of the following: base
`published-results`, `head.repo.fork == false`, PR author
`github-actions[bot]`, and `head_ref` matching `auto/results-mirror-*`. Do not
relax this waiver to make a hand-opened addition pass. If one change both
adds and deletes paths, mirror last: complete the reviewed deletion first,
then land the addition on `develop` and run the mirror so the corpus floor is
never violated on the public branch.

During the A0 migration freeze, privacy remediation, takedown, or mistaken
publication authorizes presentation withdrawal and artifact-access suppression,
not accepted-archive deletion:

1. Record the authorized withdrawal, affected public IDs, actor, reason, and time.
2. Carry the withdrawal into publication desired state, rebuild presentation without the
   affected rows, deploy, probe externally, and require an attested live receipt before
   reporting the takedown live.
3. Suppress accessible derived artifacts where the provider permits, without claiming
   that Git history or accepted source bytes were erased.
4. **Do not** delete a path from `published-results` or `develop` while the A0 freeze is
   active. One-maintainer takedown authority does not supersede the preservation floor.

Irreversible source-byte erasure requires a separately approved incident plan that either
releases the applicable A0 preservation gate or explicitly supersedes it. That plan must
inventory Git history, workflow artifacts, caches, mirrors, and inventory consequences.
Only then may operators use a narrow reviewed deletion PR; a full-tree wipe remains
forbidden, and the deletion merge is never proof that public bytes are gone.

If the workflow's heuristics ever miss a path (or a one-off mirror is
needed outside the trigger conditions), trigger it manually via
`workflow_dispatch` from the Actions tab.

#### Why the mirror PR needs its own check, and what still doesn't run there

The mirror PR is opened by `sync-results-data-to-published.yml` using its own
`GITHUB_TOKEN`, so its author is `github-actions[bot]`. GitHub does not start
the `pull_request` workflow unattended for that bot-created event. A maintainer
may explicitly approve or rerun an `action_required` check, but that is a human
action and not the mirror's automatic evidence. #1542 shipped with an empty
`statusCheckRollup` for exactly this reason: not zero failures, zero checks.

The sync workflow now closes that gap itself: before opening or updating the
PR, it runs the same two corpus gates `validate-submission.yml` would have
(bundle validation via `scripts/validate_submission.py`, inventory freshness
via `scripts/generate_corpus_inventory.py --check`) against the exact content
it is about to mirror, and posts the result as a `corpus-mirror/validate-bundles`
commit status on the mirror branch's head SHA. A commit status is a plain
Statuses-API write, not a workflow trigger, so it is unaffected by the
GITHUB_TOKEN recursion rule above and needed no new credential — only a
`statuses: write` permission grant on the token this workflow already holds.

Trusted same-repository mirror branches may carry truthful
`summary.validation=partial` maintainer results; they remain non-ranking.
Community submissions stay stricter: a manifest is required, the query list
must be non-empty, and `summary.validation` must be `passed` with no failed
measurement evidence. The independently rerunnable submission workflow uses
the same four-signal mirror trust gate (base `published-results`, same
repository, the `auto/results-mirror-*` branch pattern, and
`github-actions[bot]` as the PR author) before allowing partial validation or
a vendor-subtree addition.

A separate, narrower gap remains and is **not** fixed by the above:
`published-results` carries exactly one workflow file
(`validate-submission.yml`). The `base-guard` job in `ci.yml` — the repo-wide
check that every PR gets regardless of base, added precisely so a PR against
an unexpected base branch cannot show zero checks — cannot run against
`published-results` because its file simply does not exist there, whatever
its trigger says. It cannot be added by the sync workflow either:
`GITHUB_TOKEN` cannot push changes under `.github/workflows/` regardless of
the `permissions:` block granted to it (a hard-coded GitHub Actions
restriction — see the "workflow file itself is NOT auto-mirrored" section of
[`adr-published-results-slim-corpus-branch.md`](adr/adr-published-results-slim-corpus-branch.md),
which already documents this exact restriction for `validate-submission.yml`
edits). Porting the `base-guard` job (or any future repo-wide guard) onto
`published-results` is therefore a **manual** maintainer step. Copy only the
`base-guard` job into a small standalone workflow on that branch (not the whole
of `ci.yml`, whose units do not apply there), and read the current job with:

```bash
git show origin/develop:.github/workflows/ci.yml | sed -n '/^  base-guard:/,/^  # Classifier/p'
```

then apply and push directly to `published-results` (or a PR against it) as
a maintainer with `contents: write` access. In practice this matters less
than it sounds: the base guard only protects against a PR whose base is
some *other* branch entirely, and every route onto `published-results`
(contributor submissions, this sync workflow) already targets it directly —
but a future stacked-PR mistake against this branch would still see zero
checks until this manual port happens. Treat it as tracked debt, not a
silent gap.

Two honest limits on what the new status does and does not buy, so nobody
reads more protection into a green badge than is there:

- **It is advisory, not enforcing.** No ruleset targets `published-results` —
  the repo's rulesets target `develop`, `release`, `v*` branches, and tags — so
  a maintainer can still flip a mirror PR ready and merge it with the status
  red. The status makes the verdict *visible*; the maintainer review step the
  draft state exists for is still what enforces it.
- **It is self-attested.** The run that builds the mirror content is the run
  that validates it and posts the verdict, so it catches bad *content*
  (a bundle that fails schema, hash, or privacy validation; a stale inventory)
  but not tampering with the mirror branch after the fact. A later
  `--force-with-lease` push from another run re-posts against the new head SHA,
  so a stale green status cannot ride on top of new bytes — but a direct manual
  push to `auto/results-mirror-*` would leave the last status attached to an
  older SHA, and the PR would show no status for the new one rather than a
  wrong one.

### 1.3 Explorer publish path

The static Explorer at `benchbox.dev/results/` is built and deployed from
`BenchBox-dev/benchbox-site` by its `.github/workflows/deploy.yml`. The site takes the snapshot and
corpus from the certified core bundle (`site-inputs-<sha>`, built by `site-inputs.yml`) and builds the
UI from its own `explorer/` copy of `results-explorer/`. A change to `results-explorer/` in this
repository reaches production only when it is replayed into that repository by pull request; see
"Replaying core changes" in its `AGENTS.md`. `docs.yml` does not deploy.
`published-results` is **not** the Explorer's build source — it is the
corpus-archive branch that contributor PRs target and that mirrors develop's
`results-data/`.

> **Curated-preview state:** `release-cut` retains `results-explorer/`,
> `results-data/`, and the three publication helpers under
> `_project/scripts/`; all other `_project/` content remains development-only.
> The `docs.yml` build still gates Explorer steps on
> `hashFiles('results-explorer/package.json')`, but a release that includes the
> application now fails closed when the corpus, helper set, or generated
> snapshot is missing. Production Pages is deployed from
> `BenchBox-dev/benchbox-site`.
>
> This is a curated preview, not a broad leaderboard or full-cohort claim.
> Completion evidence must pin the release SHA, artifact digest, deployment
> ID, prior rollback target, deploy receipt, route/header checks, range
> reads, bundle privacy scan, and live custom-domain behavior. A develop
> checkout, manual workflow build, or local static server is not deployed-site
> evidence.

## 2. Maintainer Review Checklist

- For community PRs, accept only complete, non-empty benchmark runs with
  `summary.validation=passed`, plausible metadata, and timings.
- For trusted maintainer mirror PRs, truthful partial results may be retained
  as non-ranking capability evidence when the limitation is documented.
- Reject any bundle that fails its applicable CI policy, omits required
  schema-v2 fields, or obviously misstates environment details.
- Reject or quarantine partial results whose cause is unknown or whose metadata
  would make them ranking-eligible.
- Confirm the bundle path and filenames are coherent with the existing corpus naming.
- Close stale contributor PRs after 14 days without response, with a short thank-you note.

Suggested review reply for missing fixes:

```text
Thanks for the submission. CI found issues we need fixed before merge. Please address the failing checks, rerun the local validation commands from docs/contributing-results.md, and push an update to this PR.
```

## 3. CI Failure Triage

Use the exact validator output in `scripts/validate_submission.py` when replying so the
guidance matches the code.

- `Unsupported schema version`: the submitter exported an old result shape; ask them to rerun with a current BenchBox build.
- `Hash mismatch`: the bundle changed after packaging; ask them to rerun `benchbox submit`.
- `All query timings are 0ms` or negative durations: reject until the benchmark is rerun.
- `Unknown benchmark id` or `Unknown platform name`: verify whether this is a legitimate new surface before merging.
- Inventory drift: ask the contributor to run `uv run -- python scripts/generate_corpus_inventory.py --write` and recommit.

Never bypass red validation checks and merge anyway. If the validator is wrong, fix the
validator in a separate PR first.

## 4. Backfilling `corpus-inventory.json`

When a PR updates bundles but forgets the inventory:

```bash
uv run -- python scripts/generate_corpus_inventory.py --write
git add results-data/corpus-inventory.json
git commit -m "chore: refresh corpus inventory"
```

If you are fixing the contributor branch yourself, explain that in the PR before pushing.

## 4b. Corpus Path Privacy

Every JSON file under `results-data/` must be free of private absolute paths.
`tests/unit/scripts/test_corpus_privacy_invariant.py` scans the whole corpus on
every code CI run, not just the files a PR touched. #1467 cleaned the corpus;
this gate is what stops it regressing. A changed-files scan cannot do that job:
a bundle merged before the scan existed is invisible to it permanently.

The gate is marked `fast`, so it runs inside `code-test`, which
the `core` unit requires. A corpus-only diff still routes to `code-test`;
the same test file pins both facts so the gate cannot become decorative.

To re-migrate after importing legacy bundles:

```bash
uv run -- python _project/scripts/results_explorer_corpus_migrate.py
```

Dry run by default; add `--write` to apply. It reuses the canonical
`AnonymizationManager` and preserves values that are already public hashes, so
re-running it on the current corpus is a verified no-op (207 unchanged). It
writes an audit trail to
`results-data/bundles/path-privacy-migration.manifest.json` recording old/new
hashes and result IDs — never a private value.

Migrating rewrites `result_id` for every changed bundle. The Explorer derives
result IDs from the *published* bytes, so regenerate the inventory afterwards
(§4) and expect detail URLs to move.

## 5. Rolling Back a Bad Merge

First classify whether the merge introduced an accepted bundle. During the A0
freeze, an accepted bundle must remain in the archive tip and remain recoverable.
For an accepted bundle, do **not** run `git revert` against `published-results`.
Request presentation withdrawal, deploy the suppression generation, and retain
the accepted source bytes. Removing those bytes requires a separately approved
erasure exception and evidence plan.

The archive-reversing procedure below is reserved for a merge that changed only
unaccepted staging or non-corpus material, or for an explicitly approved erasure
exception. Use a fresh branch off the affected target branch. Set `REMOTE` to the
repo you are operating against. Because `published-results` uses squash merges,
select the exact merged PR commit and inspect it before reverting. Do not use
`git log --merges` or `git revert -m`.

```bash
REMOTE=public
PR_NUMBER=<published-results-pr-number>
git fetch "$REMOTE" published-results
git switch -c rollback-results "$REMOTE/published-results"
BAD_SHA="$(gh pr view "$PR_NUMBER" --repo BenchBox-dev/BenchBox --json mergeCommit --jq '.mergeCommit.oid')"
git show --stat --oneline "$BAD_SHA"
git revert "$BAD_SHA"
git push "$REMOTE" HEAD:published-results
```

Stop after `git show` unless the commit is the exact non-accepted change approved for archive reversal. Run `git revert` and `git push` only after that check.

Then comment on the reverted PR explaining why archive reversal was permitted,
whether the material was broken or merely misleading, and whether a corrected
resubmission is welcome. Record the erasure approval when that exception was used.

## 6. Re-triggering an Explorer Rebuild

If the corpus is correct but the explorer build needs to rerun:

```bash
gh workflow run docs.yml --repo BenchBox-dev/BenchBox
gh run watch --repo BenchBox-dev/BenchBox
```

Use `workflow_dispatch` only after confirming there is no newer push already
rebuilding the site. Note that `workflow_dispatch` runs the `build` job but
**not** the legacy `deploy` job — the legacy Pages deploy is gated on
`github.event_name == 'push' && github.ref == 'refs/heads/release'` — so a manual
run validates the build without publishing. A publish uses the candidate build plus
transaction promotion in §1.3, not a push to `release`.

## 7. Data Locations

- Target branch: `published-results`
- Corpus root: `results-data/`
- Bundles: `results-data/bundles/`
- Inventory: `results-data/corpus-inventory.json`
- Community sidecar: `<result>.manifest.json` (legacy `submission-manifest.json` is still accepted)
- Generated explorer read model: `results-explorer/public/data/`

The explorer pipeline treats sidecar presence as the trust-label contract for community
submissions.

`published-results` is a slim, corpus-only branch by design. The exact allowlist of
paths that may live on it (and the matching exclusion list applied at the slim-down)
is documented in
[`docs/internal/adr/adr-published-results-slim-corpus-branch.md`](adr/adr-published-results-slim-corpus-branch.md).
A submission PR that adds files outside the allowlist should be redirected to
`develop` instead.

## 8. Code Locations

| Surface | Path |
| --- | --- |
| Submit CLI | `benchbox/cli/commands/submit.py` |
| Inventory generator | `scripts/generate_corpus_inventory.py` |
| Submission validator | `scripts/validate_submission.py` |
| Validation workflow | `.github/workflows/validate-submission.yml` |
| Explorer pipeline | `_project/scripts/explorer_pipeline/` |
| Contributor guide | `docs/contributing-results.md` |

## 9. Verification Commands

Run these locally before concluding the platform is healthy:

```bash
uv run -- python scripts/validate_submission.py results-data/bundles/
uv run -- python scripts/generate_corpus_inventory.py --check
uv run -- python results-data/validate_corpus.py
uv run -- python -m pytest tests/unit/scripts/test_validate_submission.py tests/unit/scripts/test_generate_corpus_inventory.py -q
cd results-explorer && npm run typecheck && npm run build
```

## 10. Escalation

Escalate when any of the following are true:

- The validator or inventory generator appears wrong rather than the submission.
- The docs workflow rebuild fails after a clean merge.
- A trust-label or visibility bug would publish misleading provenance.
- A rollback would remove more than the intended submission.
- The failure depends on infrastructure or GitHub permissions rather than repo code.

When escalating, link the relevant PR, the failing workflow run, and the exact file or
validator message that triggered the escalation.
