# ADR: BenchBox Development Loop Architecture (v2)

## Status

Accepted (2026-09-28).

Supersedes:

- [`docs/internal/merge-queue-governance.md`](../merge-queue-governance.md)
- [`docs/internal/pr-base-branch-policy.md`](../pr-base-branch-policy.md)
- [`docs/internal/adr/adr-independent-publication-authorities.md`](adr-independent-publication-authorities.md) (per decision D2)
- Version-branch release flow in [`docs/internal/release-guide.md`](../release-guide.md)

## Context

BenchBox previously relied on an intricate set of 46 separate GitHub Actions workflows,
a long-lived protected `release` branch alongside `develop`, an automated post-merge
safety net with complex reconciliation, numerous PR-loop Make targets and scripts, and
a multi-workflow publication control plane.

This design accumulated significant operational drag:

- CI runs often took 40–60 minutes across duplicate workflow fanouts.
- PR-loop checks and post-merge jobs drifted from each other.
- The `release` branch required continuous cherry-picks, version-branch maintenance,
  and canary runs that duplicated merge-queue work.
- The publication control plane relied on an external GitHub App and separate branch
  transactions that could stall or disconnect from trunk state.

This ADR establishes the architectural decisions, operational contracts, and migration
guardrails for a consolidated trunk-based development loop. Its original
pre-merge integration mechanism was the GitHub merge queue.

Amended 2026-10-03: the merge queue and the strict up-to-date rule on `develop` are
retired, and a pull request merges by auto-merge once its required checks pass on its
own head. The queue's failures were mostly its own machinery: 38 of the 40 most recent
failed merge-group runs, with no confirmed cross-PR integration failure. A post-merge
workflow, `.github/workflows/trunk.yml`, tests pushes to `develop`, retains pending
runs with `concurrency.queue: max`, and produces verified release distributions.
A red trunk is reverted first. The sections below describe the replacement;
dated queue decisions remain historical. See
`_project/decisions/merge-queue-retirement-2026-10-03.md`.

## Decisions

### D1: Trunk branch remains `develop`

The primary development branch remains `develop`. Renaming `develop` to `main` is
rejected (see Rejected Alternatives) to avoid disruption across downstream forks,
clones, documentation, tooling, and repository configurations.

### D2: Replace the publication control plane

The multi-workflow publication control plane is replaced with a single unified
`site-deploy` workflow for `benchbox.dev`. Entry criteria for the replacement:

- Pinned source and corpus SHAs.
- Retained artifact identity across stages.
- Post-deploy route and digest health probes.
- Durable deployment receipts.
- Exact-artifact rollback support with last-known-good validation.
- One serialized production writer with generation checks.
- Route-manifest assembly (`/` and `/docs/` from latest release tag; `/docs/dev/`,
  blog, and `/results/` from trunk).
- Rollback verified with `github-pages` environment reviewers enabled.

Once `site-deploy` validates in shadow and cuts over, the legacy publication workflows,
publication transaction branch, and publication GitHub App are retired.

### D3: Results corpus layout unchanged

The results corpus homes remain unchanged:

- `results-data/` on `develop` for active working runs and staged data.
- `published-results` as the slim corpus-only branch for public submissions.

### D4: Soundness hold via connector review and thread resolution

Soundness paths are classified by `.github/soundness-paths.txt`. The required
`oracle-review` check binds the Codex connector's review, or the stand-in approval, to
the current head, required thread resolution binds disposition of findings, and the
scheduled digest checks the merged evidence. Code-owner review and PR-body attestation
are retired. The soundness-path revocation is removed and the replacement check
is required in the live ruleset. The `auto-merge-on-open.yml` workflow is deleted:
the `no-auto-merge` label is enforced only when arming, and nothing disarms a PR
that is already armed.

Amended 2026-10-15: `no-auto-merge` stays as the durable owner hold. Refusal at
arming time did not stop agent sessions that un-drafted and merged held PRs with
raw `gh` commands, so the required `hold` check (`.github/workflows/pr-hold.yml`)
now fails while the label is present. A held PR cannot merge, armed or not.

Amended 2026-10-02, updated 2026-10-03: the external review stays, but a PR-body
attestation checked by CI no longer binds it. The author writes its own attestation, so the check
can only test that the text is present; it never tests the review. The review is bound
by three controls:

1. Review findings. The external reviewer's Critical and High findings are posted as PR
   review threads, and the ruleset requires every thread to be resolved before a merge
   (`required_review_thread_resolution`). GitHub lets an author with write access resolve
   a thread, so resolution shows that someone dispositioned the finding, not that it was
   fixed. The digest below is the check on that.
2. Review signal as a required check. For changes on the narrowed soundness path list,
   the required `oracle-review` status check passes only when the Codex connector app has
   reviewed the current head: its submitted review or thumbs-up reaction, or the stand-in
   approval described below when the connector cannot review. An "eyes"
   reaction alone means the review has started, not finished. A review that exists shows
   that a reviewer looked, not what it examined.
3. Post-merge digest. A scheduled report lists the commits that reached `develop` on the
   narrowed paths and records which review signal each had at merge. It also flags each
   review thread that was resolved with no later commit. It opens a tracker item for
   each commit with no signal or with such a thread. An agent then runs the external
   review and either records a clean result or opens a fix or revert PR.

The first two controls can be bypassed by an author acting alone with the owner's
rights, because every agent shares one account. They make a skipped review visible and
slower to skip; the digest detects a bypass after the merge and is the backstop. A
squash commit is one revert unit, but a revert on a busy trunk is not always clean, so
the backstop does not replace the review before the merge.

The `oracle-review` check is a merge-blocking automated review signal. It supersedes item 3 of
`_project/decisions/codeowner-approving-count-zero-constraint-2026-09-15.md` for paths on
the soundness list only, and only that item. That decision kept such signals advisory
because a batch of failing Codex reviews exhausted the usage limit and deadlocked the
queue. The `codex` CLI, `muse` and `agy` remain available as optional extra depth, each
with its own quota; on their own they do not satisfy the check.

When the connector cannot review, for example at its usage limit, a stand-in review
replaces it. An independent reviewer reviews the exact current head, and an account listed
in `STANDIN_ATTESTERS` (`_project/scripts/oracle_review_check.py`) then posts a PR comment
whose whole text is `Stand-in oracle review: APPROVE <full head SHA>`. The check accepts
that comment only for the exact head, only when it was posted after the head commit and any
retarget and never edited, and never while a connector review thread is unresolved. A new
push needs a new stand-in comment. A plain owner review, thumbs-up or unmarked comment does
not count. The attester account is also the one local automation uses, so the marker
records who vouched for the review, not that a human read it. All four reviewers were
exhausted at once on 2026-10-02, which blocked every workflow change for three days under
the fail-closed rule alone.

The soundness path list covers expected and reference results, digests,
result-validation and equivalence comparators, result capture, corpus overrides,
publication scripts, every workflow, and the arming helper. Release and binary
protections remain. Documentation, ordinary decision records, runbooks, threat
models, and `AGENTS.md` leave the list. The `single-repo-migration.md` decision
remains protected because release curation parses it as configuration. The manifest-based
`oracle-review` check replaces the former `tooling` attestation flag. The external
review requirement is unchanged except for the stand-in review above.
A new push or refresh changes the head and needs fresh CI and a fresh connector review
or stand-in approval before re-arming. No PR-body attestation satisfies or is required by the check.

Amended 2026-10-09: the self-hosted oracle replaced the Codex connector as the review
signal. The required `oracle-review` check now passes only on the `benchbox-oracle` App's
success review of the current head, or the stand-in approval, with no unresolved App
review thread. The connector is no longer accepted and its App is uninstalled; the digest
still counts connector reviews for merges before the cut-over (2026-10-09 00:16 UTC), so
its history stays accurate. Wherever this section says "connector review", read "oracle
review". See [`docs/internal/oracle-review-v2.md`](../oracle-review-v2.md).

Amended 2026-10-24: the required review check is `oracle-gate`, a commit status the
`benchbox-oracle` App posts from `develop` code and the ruleset binds to that App's
integration ID. It applies the same rule as `oracle-review`, which no longer is required
because it runs the workflow file from the pull request. See
[`docs/internal/repo-admin-settings.md`](../repo-admin-settings.md) and
`_project/decisions/oracle-review-check-retired-2026-10-24.md`.

### D5: Retain agent write tooling, retire PR-loop scripts

Agent workspace safety tooling is retained:

- Worktree creation and lifecycle management (`make worktree-create`, `worktree-remove`).
- Git identity pinning and author verification (`agent-write-preflight`, `agent-git-identity`).
- Attribution trailer verification (`check_agent_trailers`).
- Issue tracker state branch operations (`todo-state`).

Legacy PR-loop mechanics may retire only with their callers and safety contracts
accounted for. `pr_landing.py` still supplies the exact-head revision/readiness
transaction, `local_validation.py` supplies shared test-lock coordination, and the
soundness-drain report still diagnoses reviewed but unarmed PRs. These retained
contracts are not removed by queue retirement.

### D6: Codecov retained as informational

Codecov code coverage tracking is retained. The fast-test job in `ci.yml` produces
the coverage report and uploads it to Codecov. Upload failures remain informational;
the required test command's coverage threshold still applies.

### D7: Ruleset drift detection retained

Ruleset drift verification (`ruleset_drift_check.py`) is retained. It executes as a
advisory check in `nightly.yml`, by hand after a settings change, and as enforced
release-canary/bootstrap validation. It no longer runs inside the required
`tooling` unit. The `RULESET_DRIFT_TOKEN` secret is maintained; required thread
resolution and tag-creation protection remain checked.

### Tag signatures and release verification

Explicit cryptographic tag signatures are dropped. Release integrity relies on:

1. The `v-tag-restricted` ruleset, which restricts creation, update and deletion of
   `refs/tags/v*` for everyone except its bypass actor. The bypass actor is the
   maintainer account that agents also run as, so the ruleset does not bind that
   account; control 5 does.
2. Verification that the tagged commit exists on `develop` and its latest exact-commit
   `trunk.yml` push run on that branch succeeded, without falling back to older success.
3. Admission of the exact wheel and sdist from that run's successful `dist-artifact`
   job, with matching producer-attempt evidence and SHA-256 hashes for release upload.
4. GitHub Actions build provenance attestations.
5. A publish-time re-check, in the publishing job after environment approval, that the
   hosted tag object and its peeled commit still equal the admission receipt.

### Amendment 2026-10-27: evidence reconciliation and owner decisions

The required `ci.yml` units are five: `core`, `explorer`, `results-data`, `docs` and `tooling`. The `hold` check,
the App-bound `oracle-gate` status and the other required contexts are separate controls, not units.

The property ledger (G3) now classifies each safety property as high-risk (merge safety and required contexts,
release integrity, commit identity and attribution, the soundness hold, the oracle gate) or process-only. A
high-risk property is `proven` only when both its pass and its refuse behavior name a test, workflow run or canary
pull request bound to a develop commit, or it names a blocker; a process-only property carries a one-line waiver
reason. The ledger holds the evidence and the open blockers.

Owner decisions of 2026-10-27:

- The per-test 0.5 s duration policy is superseded by the aggregate fast-duration gate.
- The browser check is ported to the site repository and the Explorer-token lint is retired.
- GizmoSQL stays in the release rehearsal candidate.
- Deleting the `release` branch and its ruleset is an owner-run step after the first publication on the new release
  path; this ADR's approval to delete `release` and `publication` branches does not authorize an agent to do it.

## Standing Administrative Approvals

The maintainer has granted standing approvals for the following operational changes
once corresponding preconditions and backups (per Guardrail G5) are satisfied:

1. **Rulesets:**
   - Add new required checks (`core`, `explorer`, `results-data`, `docs`, `tooling`). The `landing` check named
     in the original approval is not a `ci.yml` unit and is not part of this set.
   - Remove legacy checks after the transition window.
   - Historical queue-parameter adjustments are retired with the queue. The dated
     retirement decision records the specific queue, strict-rule and review changes.
   - Delete the obsolete `release-only` ruleset.

2. **Branches:**
   - Delete `release` and `publication` branches after creating immutable archive tags.
   - Delete stale `gh-readonly-queue/*` branches with no active queue entry.
   - Delete merged `auto-revert/*` branches and merged feature/fix branches.

3. **Labels and environments:**
   - Delete legacy incident labels (`incident:develop-red`, `incident:develop-red-revert-conflict`); both were
     deleted on 2026-10-27. Keep `incident:release-canary-red` (owner decision of 2026-10-27) and `no-auto-merge`
     (amendment of 2026-10-15).
   - Create `quarantine` and `t3:*` labels.
   - Delete the `publication-attestation` environment after site deployment cutover.

4. **Secrets and GitHub Apps:**
   - Delete `PUBLICATION_APP_ID` and `PUBLICATION_PRIVATE_KEY`, and uninstall the
     publication GitHub App after `site-deploy` cutover.
   - Delete unused legacy tracker secrets if no repository references exist.

5. **Self-service merge:**
   - Autonomous merge of modernization PRs targeting `develop` once all required checks,
     self-review, and required soundness reviews pass.

6. **Site deployment approval:**
   - Approve pending `github-pages` deployments via GitHub API once pre-deployment
     verification passes.

7. **Release flow rehearsal:**
   - Execute TestPyPI publication rehearsals autonomously.
   - Note: Real PyPI production publication remains strictly owner-initiated.

8. **Live cloud benchmarks (`t3:cloud`):**
   - Wired behind configuration defaulting to disabled; live cloud runs remain off.

9. **Medium-tier test budget:**
   - If T2 exceeds the 15-minute target, standard runner sharding may increase up to 10.
     If still exceeding 15 minutes, accept up to 20 minutes with durable rationale.
     Paid larger runners are not authorized.

10. **Waiting windows:**
    - Active observation windows (5 business days shadow and 7 nightly runs)
      are non-blocking for independent tasks. Queue-run windows are historical.

## Guardrails

- **G1 Replacement before removal:** A required check is removed only after its replacement
  has passed a canary set of throwaway pull requests (one change per unit, one cross-unit
  change, one soundness-path change, and one deliberately broken change per unit) AND the
  property ledger (G3) maps every protected property to the replacement. Three real
  merges must then pass under the new checks before the flip is considered complete.
  With the queue retired, merged-tree evidence comes from exact-commit trunk runs;
  this amendment does not assert that the canary or observation evidence is complete.
  (Amended 2026-09-29: the earlier 20-run window assumed the old and new workflows could
  run side by side; the merge groups already saturated the organization's runners, so
  side-by-side operation is not possible and the cutover is a single flip.)
- **G2 Shadow parity:** Superseded by the canary set in G1 for the pull-request workflow.
  Post-merge evidence names exact commits. Scheduled workflows (nightly) still validate with dispatched runs
  before their predecessors retire.
- **G3 Property ledger (`docs/internal/dev-loop-property-ledger.md`):** Every safety
  property -> current guard -> new guard -> proof. Properties include: correctness oracle
  (digest arming, query discrimination, no-skip), SQL self-binding lint, monotonic-clock
  policy, submission validation, corpus trust boundary, artifact privacy, publication
  rollback, explorer snapshot compatibility, public-site visual acceptance, binary integrity,
  wheel installability, dependency bounds, release curation, ruleset/settings drift,
  soundness-path connector review, workflow context validity and exact-commit trunk
  coverage. The retired combined-tree queue property maps to PR checks and post-merge
  validation, which detect composition failures after merge. A test or workflow
  is deleted only if the ledger shows coverage or classifies it pure-process.
- **G4 Reference sweep:** Each deletion PR proves zero references to deleted names in
  workflows, Makefile, make/, scripts/, _project/scripts/, tests/, docs/, AGENTS.md,
  CONTRIBUTING.md, .claude/, .pre-commit-config.yaml, and the skill-sync source, excluding
  CHANGELOG and archives.
- **G5 Backups first:** Export ruleset JSON, environments, labels, branch list, secret names,
  and Pages settings to `_project/archive/dev-loop-v2/` before any GitHub-side change; tag
  branches `archive/<name>-<date>` before deletion.
- **G6 One cluster per PR:** No PR mixes GitHub settings with code deletion; each PR is
  independently revertible.
- **G7 Release freeze:** Active from the start of required-check transition until the first
  new-path PyPI release; the old release path (release branch, test.yml, validate-release-pr,
  release-canary, drift scripts, release-cut targets) stays fully working until then.
- **G8 Secrets and Apps:** Removed only after no workflow, script, or doc references them
  and one full T3 cycle passed; PAT revoked at issuer; each removal is a separate owner-approved
  step.
- **G9 Admin changes:** Admin changes covered by the standing approvals are made by the
  agent after G5 backups; anything outside them stops.
- **G10 Instructions move in lockstep:** Any PR that removes or renames a Make target,
  script, or workflow used by AGENTS.md, CONTRIBUTING.md, .claude/commands, or skills updates
  those (skill-sync source, then re-sync) in the same PR.
- **G11 Settings-doc first:** Before any ruleset change, merge the PR that updates the drift
  checker's expected settings so the live change does not fail `ruleset-drift` closed and
  block the protected workflow. For the 2026-10-03 queue, strict-rule and soundness-review
  retirement only, the owner approved implementation/settings changes before the docs
  and drift expectations; see the dated retirement decision. Backup, replacement,
  review and other administrative approval requirements remain.

## Rejected Alternatives

1. **Renaming default branch from `develop` to `main`:**
   Rejected because renaming causes wide disruption across forks, clones, CI scripts,
   git submodules, issue tracker references, and external links without delivering
   engineering value.

2. **Per-module test selection in PR builds:**
   Rejected because fine-grained dynamic test selection introduces blind spots where
   indirect interface dependencies fail to trigger tests. Coarse-grained unit classification
   with always-reporting checks provides deterministic coverage.

3. **Live cloud platform testing in PR or merge queue:**
   Rejected due to network flakiness, credential exposure risks, high API costs, and
   long runtimes that would violate the 30-minute pre-merge target. Live cloud testing
   is isolated to scheduled nightly T3 execution.

4. **CODEOWNERS-only hold for soundness paths:**
   Rejected because the repository's required approval count is 0 and automated agents
   acting with owner permissions would bypass the intended hold (observed empirically in
   PR #2416). Explicit CI path detection and external adversarial review ensure rigorous
   soundness verification.
