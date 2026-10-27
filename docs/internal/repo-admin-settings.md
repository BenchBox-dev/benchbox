# Repository admin settings runbook

This runbook is the single source of truth for the GitHub admin settings that
the dev-loop workflows depend on. CI workflows, branch protection, and
auto-revert rely on settings outside the repository tree. They are recorded
here so you can re-apply them after a transfer, restore them after accidental
drift, or review them during incident triage.

This document states the required configuration. Verify live settings before
claiming they are applied. Each change also gets a dated audit record under
`_project/decisions/`.

## Required workflow permissions

```text
default_workflow_permissions: read
can_approve_pull_request_reviews: true
```

The default token is read-only on purpose. A workflow that needs to write
declares the specific scopes at the job level, which keeps writes narrow.
`can_approve_pull_request_reviews: true` is left over from the retired
post-merge auto-revert job, which used it to request review from the original
PR author. No current workflow depends on it.

Verify:

```bash
gh api repos/BenchBox-dev/BenchBox/actions/permissions/workflow
```

Apply (admin only):

```bash
gh api -X PUT repos/BenchBox-dev/BenchBox/actions/permissions/workflow \
  -f default_workflow_permissions=read \
  -F can_approve_pull_request_reviews=true
```

History:

- 2026-04-30: `can_approve_pull_request_reviews` changed from `false` to `true`.
  Recorded in
  `_project/decisions/dev-loop-auto-revert-fork-test-2026-04-30.md`.

## Branch ruleset — `develop`

Ruleset name: `develop-squash-only` (id `15611785`), targets
`refs/heads/develop`.

Required status checks:

```text
- core
- explorer
- results-data
- docs
- tooling
- hold
- oracle-gate
```

Five of these contexts are always-reporting result jobs in
`.github/workflows/ci.yml`. `oracle-gate` is a commit status that
`.github/workflows/oracle-gate.yml` posts through the `benchbox-oracle` App;
it passes at once for a change confined to inert paths (see Inert-path
manifest in `docs/internal/oracle-review-v2.md`). `hold` comes from
`.github/workflows/pr-hold.yml` and fails while the PR carries the
`no-auto-merge` label; it re-runs when a label is added or removed.
`.github/workflows/oracle-review.yml` reports no check: it runs a no-op
`review-event` job on review submitted, edited and dismissed events, and its
completion makes `oracle-gate` re-evaluate.

The `ci-paths` job maps changed paths to units with `.github/ci-units.yml`. An
untouched unit reports success at once. A touched unit succeeds only when every
job it requires succeeded; a skipped required job fails it, so a path filter or
broken `if:` never reads as a pass (`scripts/ci_unit_result.py` holds the
rule). All five contexts report on every `pull_request` event, so no required
check stays pending forever on a change that does not touch it.

Markdown routes to the `docs` unit alone unless a test or audit reads it. `.github/ci-units.yml` lists the markdown that counts as code (root agent and release documents, `docs/agent/`, and every document recorded in `_project/inert-doc-readers.json`), `.github/path-filters.yml` lists the same set for `code-ci`, and `tests/unit/scripts/test_ci_units.py` fails when the two disagree or when a document a test reads is routed away from the code tests.

| Context | Runs for | Jobs it requires |
| --- | --- | --- |
| `core` | `benchbox/`, `tests/`, packaging, executable docs code; framing also on test infrastructure or bundled binary changes | lint, unit tests, the coverage-impact medium selection on every PR (changes to paths listed as inert by `.github/inert-paths.txt` are inert; any other fail-closed escalation runs the heavy tier instead), and when heavy inputs change the medium tests, correctness gate, required local-engine cases, plan-capture gate and DataFusion integration; macOS/Windows TPC-H binary framing on heavy or framing inputs; when a change touches Python under `tests/`, `scripts/`, `_project/scripts/` or `benchbox/`, the `windows-tests` job runs the fast tier of the test files the coverage selection picks plus every changed test file on `windows-latest` with Python 3.12 (`ci-paths` publishes them as `windows-test-files.txt`; the nightly `t3:windows` domain stays the full-suite backstop); package smoke and dependency audit on packaging changes |
| `explorer` | explorer pipeline, parity fixtures, `benchbox/core/results/`, `results-data/` | CLI-versus-explorer parity fixtures |
| `results-data` | `results-data/`, submission validator, `benchbox/core/results/` | corpus inventory and validation, submission validator sync, corpus and explorer-pipeline contract tests |
| `docs` | `docs/`, examples, the prompt catalog, CLI and registries | example and docs reference validation, docs generators, spell check |
| `tooling` | every event | content guard, skill integrity, and audit checks by path |
| `oracle-gate` | every PR, from `develop` code on trusted triggers | `benchbox-oracle` success `oracle-verdict` status on the current head, or the stand-in approval, with oracle threads resolved; inert-only changes report success. Posted by the `benchbox-oracle` App, so it needs `ORACLE_APP_ID` and `ORACLE_APP_PRIVATE_KEY` in the `oracle` environment, else nothing is posted and the head cannot merge |

The Windows PR selection excludes tests changed only on `develop` unless the PR changes them or the
coverage, importer or reader selection reaches them. POSIX-only repository
tooling is excluded unless the PR changes its tests; changed tooling tests must
skip unsupported platforms cleanly. Targets are loaded inside Python so their
paths do not expand the Windows process command line. The leg only runs when the target list is
non-empty, so an empty or missing target file fails the job with an error and
a run summary. If selected files contain no eligible fast-tier tests,
the job emits a notice and records that outcome in the run summary.

The public site is built, compared and deployed in `BenchBox-dev/benchbox-site`.

`scripts/ruleset_drift_check.py` compares the live rulesets with this runbook.
With no merge queue it no longer runs inside `ci.yml`. Run it by hand after a
settings change; the nightly workflow also runs it as an advisory check.

To change the required contexts:

1. In the PR that changes the workflow, update the list above. The drift
   check reads its expected contexts from this list.
2. Update the hosted ruleset immediately after that PR merges.
3. Read the live ruleset back and run the drift check before claiming the
   change.

Other ruleset properties to preserve:

```text
strict_required_status_checks_policy: false
required_linear_history: true
non_fast_forward: true
required_pull_request_reviews:    squash-only PRs
require_extra_approval_for_unattributed_changes: true
deletion: blocked
bypass_actors: (none)
```

Current-base checks are not required. With no merge queue and about thirty
merges a day, the strict policy would invalidate every open PR on every merge.
Instead, `trunk.yml` runs the fast lane, medium tests, correctness gate and
required local-engine cases after each merge; revert a red run with
`make trunk-revert`. When two PRs each pass on an older base but together break
an invariant, that run catches it after merge. If this happens more than about
once a week, turn the strict policy back on.

**Relaxing one required context to repair a broken base.**

Owner only, and only when `develop`'s own content breaks a required check so
no pull request can pass it (`docs/agent/emergency-merge.md` covers what agents
do first).

1. Name one pull request and its head SHA. Confirm from the agent evidence that
   the failure reproduces on current `develop`.
2. Hold competing pull requests: disarm auto-merge on any other armed pull
   request, or wait until none is armed, so only the named head can merge while
   the ruleset is relaxed.
3. Capture the full ruleset before any change:
   `gh api repos/BenchBox-dev/benchbox/rulesets/15611785 > /tmp/ruleset-15611785-before.json`.
4. Remove exactly one required context, the failing one, and nothing else. Never
   touch bypass actors or enforcement. When a non-oracle check is broken, leave
   the `oracle-gate` binding untouched. If `oracle-gate` itself is the broken
   context (a broken checker or workflow on `develop` that fails all PRs
   including its repair), the owner may remove `oracle-gate` for exactly that
   one merge, but only after the owner has personally read the full diff of the
   named head and posted a pull request comment naming that head SHA and stating
   the diff was reviewed. `oracle-gate` is the only required review, so without
   that comment it stays bound; an agent never substitutes for it.
5. Merge the named pull request at the named head.
6. Restore the context immediately, whether the merge succeeded or failed,
   before retrying, starting other work, or recording notes. If `oracle-gate`
   was removed, restore it bound to integration 5201041 (`benchbox-oracle` App).
7. Read back and compare with the capture, using a token that can see
   `bypass_actors`:
   `gh auth token | uv run -- python scripts/ruleset_drift_check.py --token-stdin --compare-capture /tmp/ruleset-15611785-before.json`.
   It ignores only `updated_at`, `_links` and `current_user_can_bypass`, prints
   each differing field (including any context's `integration_id`, where a
   missing binding equals `null`), requires `oracle-gate` to stay bound to
   integration 5201041, fails when `bypass_actors` is missing from the capture
   or the live ruleset or when live enforcement is not `active`, and exits 0
   only when nothing differs. Then run the runbook drift check with
   `--require-bypass-actor-visibility` and without `--compare-capture`.
8. Write the decision record `emergency-merge.md` requires, citing the records
   it names and the ruleset history versions of each window.

The latest bounded, read-only wall-time and runner-minute remeasure, taken
while the merge queue was still in use, is in
[`_project/analysis/ci-waste-remeasure-2026-08-31.md`](https://github.com/BenchBox-dev/BenchBox/blob/develop/_project/analysis/ci-waste-remeasure-2026-08-31.md).
It keeps `pull_request` and `merge_group` evidence separate and does not
authorize changing required contexts or skipping jobs.

The six-unit CI retired the strict-base refresh shadow
(`develop-refresh-shadow.yml`) and the `pr-certification-identity` and
`pr-certification-lanes` artifacts `pr.yml` uploaded for it. The shadow was
observational and never a required context. `scripts/pr_refresh_certification.py`
remains as a library for a later activation gate, which would need to bind a
full run to a specific head, base, merge tree, workflow fingerprint, and lane
set from `ci.yml`. A base advance alone does not require a refresh. Resolve
conflicts with `make pr-refresh`; any resulting new head needs fresh CI and review.

Verify:

```bash
gh api repos/BenchBox-dev/BenchBox/rulesets/15611785 --jq '
  {
    target: .target,
    enforcement: .enforcement,
    bypass_actors: [.bypass_actors[]?.actor_type],
    required_checks: [
      .rules[]
      | select(.type == "required_status_checks")
      | .parameters.required_status_checks[]?.context
    ],
    required_check_bindings: [
      .rules[]
      | select(.type == "required_status_checks")
      | .parameters.required_status_checks[]?
      | {context, integration_id}
    ],
    strict_base: (
      .rules[]
      | select(.type == "required_status_checks")
      | .parameters.strict_required_status_checks_policy
    ),
    linear_history: any(.rules[]; .type == "required_linear_history"),
    non_fast_forward: any(.rules[]; .type == "non_fast_forward"),
    deletion: any(.rules[]; .type == "deletion"),
    merge_queue: any(.rules[]; .type == "merge_queue")
  }'
```

History of the required contexts:

- 2026-04-30: the required status check changed from
  `["lint", "test (ubuntu-latest, 3.12)"]` to `["ci-required-result"]`.
  Recorded with full before and after JSON in
  `_project/decisions/dev-loop-path-filter-smoke-test-2026-04-30.md`.
- 2026-09-29: `ci-required-result` was replaced by the six unit contexts
  reported from `ci.yml`, as mapped in `.github/ci-units.yml`.
- 2026-10-03 and 2026-10-04: the merge queue and the strict up-to-date rule
  were removed, and `oracle-review` became required. Recorded in
  `_project/decisions/merge-queue-retirement-2026-10-03.md`.
- 2026-10-21: `oracle-gate` became required, bound to the `benchbox-oracle`
  App (`integration_id` 5201041); `oracle-review` stays required. Recorded
  with full before and after JSON in
  `_project/decisions/oracle-gate-required-check-2026-10-21.md`.
- 2026-10-24: `oracle-review` stopped being required, leaving the App-bound
  `oracle-gate` as the only required oracle check; the hosted ruleset is
  changed right after this runbook change merges. Recorded in
  `_project/decisions/oracle-review-check-retired-2026-10-24.md`.

### No merge queue

`develop-squash-only` (ruleset id `15611785`) has had no `merge_queue` rule
since 2026-10-03; see `_project/decisions/merge-queue-retirement-2026-10-03.md`.
Agents arm a PR with `make pr-arm`, which enables auto-merge; the PR
squash-merges when the required checks pass and every review thread is
resolved. The drift check reports a `merge_queue` rule that reappears.

The required `oracle-gate` check (`.github/workflows/oracle-gate.yml`) passes
when any of these holds:

- The change touches only inert paths (see Inert-path manifest in `docs/internal/oracle-review-v2.md`).
- The latest `oracle-verdict` commit status on the current head, created by the
  `benchbox-oracle` App after any retarget, is success, and none of the App's
  review threads is unresolved. Review bodies are not read, so editing or
  deleting a review cannot change the result; statuses from any other creator
  and the legacy `oracle-review-shadow` context are ignored.
- An account in `STANDIN_ATTESTERS` (`_project/scripts/oracle_review_check.py`)
  posted `Stand-in oracle review: APPROVE <full head SHA>` for the exact head,
  after any retarget and after the oracle's latest verdict status on that head. Per
  `docs/agent/merge-authority.md`, an agent posts a stand-in only when the owner
  names the PR and its head in the current request, with a preceding `Owner instruction`
  comment naming the PR, head, and covered findings. The comment is unedited and
  no oracle thread is unresolved.

A plain owner comment does not count. See [soundness drain](soundness-drain.md).
A stand-in comment makes `oracle-gate` re-evaluate at once. Resolving a review
thread starts no workflow run; `make pr-arm` and `make pr-arm-remote` dispatch
`oracle-gate` when the head's status is not `success`. For a PR that is already
armed, run `gh workflow run oracle-gate.yml --ref develop -f pr=<number>`, or
wait for the next sweep, which runs on every push to `develop` and on a
schedule.

The live `develop-squash-only` ruleset binds `oracle-gate` to
`integration_id` 5201041, the `benchbox-oracle` App's ID
(`gh api apps/benchbox-oracle --jq .id`, checked 2026-10-23), which matches
`ORACLE_GATE_INTEGRATION_ID` in `_project/scripts/ruleset_review_enforcement.py`.
Before `oracle-review` was dropped, the two contexts were compared on the 198
pull request heads reviewed from 2026-10-22: they agreed on 195; on one,
`oracle-gate` passed after the oracle's success while the `oracle-review` run
had gone stale, and two heads were replaced before the gate ran.

`.github/workflows/oracle-gate.yml` runs only `develop` code and posts
`oracle-gate` for the pull request head as a commit status through the same
oracle App. The unit test fails when any other workflow could post that
context, but a same-repo pull request with `statuses: write` could still post
a status with that name, so the workflow alone is not the enforcement; the
integration-ID binding is (see the residual-risk note below). It applies the
checker's rule unchanged: success when only exempt paths changed, when the
App's latest `oracle-verdict` status on the head is success with no unresolved
oracle thread, or when a valid stand-in covers the exact head, and failure otherwise.

The workflow posts the context only when `ORACLE_APP_ID` and
`ORACLE_APP_PRIVATE_KEY` are set as secrets in the `oracle` environment; when
either is missing it only logs the decision with `--no-post`. With the check
required, a head that never receives the context cannot merge. Check that both
names exist with
`gh api repos/BenchBox-dev/benchbox/environments/oracle/secrets --jq '.secrets[].name'`
(names only; never read values).

The `oracle` environment deploys only from the `develop` branch (custom branch
policies with one `develop` entry). `scripts/ruleset_drift_check.py` fails when
that policy widens, because any branch allowed to deploy there can mint the App
token.

Read it with
`gh api repos/BenchBox-dev/benchbox/environments/oracle --jq .deployment_branch_policy`
(expect `{"custom_branch_policies":true,"protected_branches":false}`) and
`gh api repos/BenchBox-dev/benchbox/environments/oracle/deployment-branch-policies --jq '.branch_policies[]|[.type,.name]'`
(expect only `["branch","develop"]`). Verified on 2026-10-26. To restore it:

```bash
gh api -X PUT repos/BenchBox-dev/benchbox/environments/oracle \
  -F 'deployment_branch_policy[protected_branches]=false' \
  -F 'deployment_branch_policy[custom_branch_policies]=true'
gh api -X POST repos/BenchBox-dev/benchbox/environments/oracle/deployment-branch-policies \
  -f name=develop -f type=branch
```

### Soundness-path review enforcement

The required `develop-squash-only` policy combines `oracle-gate` with
`required_review_thread_resolution: true`, `required_approving_review_count: 0`
and no bypass actors. Code-owner review is disabled; CODEOWNERS is retired.
All review threads must be resolved before merging.

The soundness gate, as operated:

- `.github/soundness-paths.txt`, read by `_project/scripts/soundness_paths.py`,
  defines the soundness-critical surface used by the merge digest and landing
  guards. `oracle-gate` requires a review unless the diff touches only inert
  paths (`.github/inert-paths.txt`, read by `_project/scripts/inert_paths.py`),
  including changes to the gate itself. Any non-inert PR needs the
  `benchbox-oracle` App's success review of its current head, or the stand-in
  approval, with every oracle thread resolved.
- **Residual risk:** `oracle-gate` is evaluated from `develop` code on trusted
  triggers, so a pull request cannot change the check that judges it. What
  stops a pull request workflow with `statuses: write` from posting a forged
  `oracle-gate` success to its own head is the ruleset binding of the required
  check to the `benchbox-oracle` App's integration ID. GitHub documents that a
  required check bound to an app is satisfied only by a check run or commit
  status that app set ("If the status is set by any other person or
  integration, merging won't be allowed", [available rules for
  rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets#require-status-checks-to-pass-before-merging)).
  This repository has observed only the positive case: the App's own
  `oracle-gate` commit status satisfies the check (pull request #3340,
  2026-10-23). The forged case is untested here. To test it, on a throwaway
  pull request whose App-posted `oracle-gate` is `failure`, post `oracle-gate`
  `success` with a user token
  (`gh api repos/BenchBox-dev/benchbox/statuses/<head> -f context=oracle-gate -f state=success`)
  and confirm `gh pr view <n> --json mergeStateStatus` stays `BLOCKED` and the
  merge box still lists `oracle-gate` as required; then close the pull
  request. The daily soundness merge digest runs from `develop` and flags a
  soundness-path commit that merged without an oracle success review or
  another review signal. The develop review check
  (`review_enforcement_findings`) fails unless `oracle-gate` is required with
  the App's integration ID, but the drift check's context comparison ignores
  `integration_id`, so read the binding by hand after any ruleset edit with the
  `required_check_bindings` field of the Verify command above, or
  `gh api repos/BenchBox-dev/benchbox/rulesets/15611785 --jq '[.rules[] | select(.type == "required_status_checks") | .parameters.required_status_checks[]? | {context, integration_id}]'`,
  and compare the `oracle-gate` entry with `gh api apps/benchbox-oracle --jq .id`.
- `make pr-open` does not arm auto-merge when it creates a PR. `make pr-arm`
  (or `make pr-open READY=1`) arms it after a live check of the PR, so a PR
  cannot merge while a follow-up commit is still being written. Arm only when
  implementation and review fixes are finished on the pushed head.
- `make pr-open` checks a non-ancestor branch with `git merge-tree` and allows
  conflict-free publication without requiring the develop tip to be an
  ancestor. It does not verify a merge queue or merge `develop` itself. Refresh
  a conflicting PR near its landing turn, then rerun CI and get review on the
  resulting head before arming.
- No workflow arms auto-merge. One workflow disarms: when a `trunk.yml` run
  on `develop` completes red, `.github/workflows/trunk-red-disarm.yml` disables
  auto-merge on each open PR against `develop` that has it enabled and that
  `scripts/trunk_revert.py arm-gate` would refuse, through the GraphQL
  `disablePullRequestAutoMerge` mutation. That mutation needs `contents: write`
  in addition to `pull-requests: write`; `pull-requests: write` alone is rejected
  with `Resource not accessible by integration`. Bare `gh pr create` does not
  auto-arm. The old `ready_for_review` arm point never fired (drafts were
  unused) and was deleted per
  `_project/decisions/auto-merge-policy-consolidation-2026-08-06.md`, D2.
- Durable holds are **draft** and label **`no-auto-merge`**. Arming checks both
  (`make pr-arm-auto-merge`, `pr-arm` and `pr-ready` refuse to arm). The nightly
  green-unmerged sweep never enables auto-merge and does not classify either
  hold as stranded. Adding the label to an armed PR fails the required `hold`
  check, so the PR cannot merge even though auto-merge stays on. Disable
  auto-merge too (`gh pr merge <n> --disable-auto`) so the PR shows no false
  intent to land. See `docs/internal/pr-triage.md` "Durable auto-merge holds".
- `make pr-arm` refuses drafts, non-develop bases, durable holds, requested
  changes, unresolved threads, unpublished work, and a remote head that differs
  from local HEAD. It enables squash auto-merge for the exact reviewed head.
  Required checks and thread resolution control the merge.
- A new push invalidates the prior head's review. Before re-arming a
  soundness-path PR, wait for a current-head oracle review and a successful
  `oracle-gate`. The retired code-owner self-approval deadlock does not
  require manual merging under this policy.

`scripts/ruleset_drift_check.py` imports the shared
`review_enforcement_findings` predicate. Through
`DEVELOP_REVIEW_RULE_ENFORCED = True`, it treats missing thread resolution or a
missing App-bound `oracle-gate` required check as a **blocking** finding. The daily
`release-canary.yml` run and the `validate-release-pr.yml` bootstrap use the
same path. For an immediate live check, run the standalone checker:

```bash
gh api repos/BenchBox-dev/BenchBox/rules/branches/develop \
  | uv run --project _project/scripts -- python _project/scripts/ruleset_review_enforcement.py --rules-file -
```

The predicate deliberately does not assert `required_approving_review_count`:
that setting is branch-wide and would gate every develop PR. A green drift
check is evidence of configuration only, not proof that a PR passed its checks,
received current-head review, or merged.

`tests/unit/release/test_ruleset_review_enforcement.py` and
`tests/unit/release/test_ruleset_drift_review_coverage.py` cover the review
predicate and its blocking default with explicit override.

## Branch ruleset — `release`

Ruleset name: `release-only`, targets `refs/heads/release`.

Release-only branch. Direct pushes are not allowed; releases land through the
`release-cut` and `release-finalize` Make targets in `release-guide.md`.

Required status checks:

```text
- validate-base
- release-required-result
```

`validate-base` is the branch-shape guard in
`.github/workflows/validate-release-pr.yml`. It allows only release branches
named `vX.Y.Z`, with an optional suffix.

`release-required-result` is the umbrella job in `.github/workflows/test.yml`
for release PR correctness. It aggregates:

- the required fast lane
- the bounded real-result correctness gate (`make test-correctness-gate`)
- the credential-free integration-not-slow suite
- the complete medium tier, in four shards
- the isolated exact-one-wheel package smoke
- dependency upper-bound checks
- release-branch curation checks

Use this context in the ruleset instead of individual matrix job names such as
`test-package (...)`.

Other ruleset properties to preserve:

```text
strict_required_status_checks_policy: false
required_linear_history: true
non_fast_forward: true
require_extra_approval_for_unattributed_changes: true
deletion: blocked
bypass_actors: (none)
```

Verify (the ruleset id varies; list, then inspect):

```bash
gh api repos/BenchBox-dev/BenchBox/rulesets --jq '.[] | {id, name, target}'
gh api repos/BenchBox-dev/BenchBox/rulesets/<release-ruleset-id> --jq '
  {
    target: .target,
    enforcement: .enforcement,
    bypass_actors: [.bypass_actors[]?.actor_type],
    required_checks: [
      .rules[]
      | select(.type == "required_status_checks")
      | .parameters.required_status_checks[]?.context
    ],
    strict_base: (
      .rules[]
      | select(.type == "required_status_checks")
      | .parameters.strict_required_status_checks_policy
    ),
    linear_history: any(.rules[]; .type == "required_linear_history"),
    non_fast_forward: any(.rules[]; .type == "non_fast_forward"),
    deletion: any(.rules[]; .type == "deletion")
  }'
```

If the live ruleset differs from this runbook, update the ruleset or this
document before relying on release-required enforcement. Green `validate-base`
and `release-required-result` runs are mandatory only if the ruleset also
requires both contexts.

## Tag creation and release-environment protections

In `.github/workflows/release.yml`, the `verify-tag-on-release` job asserts that
the triggering ref (tag push or `workflow_dispatch`) is `release` or an ancestor
of it before build and publish run, and the real-PyPI `Publish to PyPI` step
also requires `startsWith(github.ref, 'refs/tags/v')`. Those are workflow-level
(tooling) controls. The two sections below are the repo-admin half of the fix:
who can create a `v*` tag at all, and whether the `pypi` environment requires
human approval. The develop-PR `GITHUB_TOKEN` cannot read or write
either (no `administration` scope); only an admin PAT can.

### Tag creation restricted to release flow (enforced)

Current control: a tag-protection ruleset targeting `refs/tags/v*` restricts tag
*creation* to the release automation identity or specific maintainer actors, as
`develop-squash-only` restricts pushes to `refs/heads/develop`.
`verify-tag-on-release` stops a stray tag from reaching build and publish only
if the tagged commit is not on `release`. It cannot stop someone with push
access from creating a `v*` tag *on* a release commit out of band, such as
re-tagging an old release commit or tagging a version out of sequence. The
ruleset closes that gap by restricting who may create the tag at all.

Verify by listing rulesets and finding one whose `conditions.ref_name.include`
targets `refs/tags/v*` with `target: "tag"`:

```bash
gh api repos/BenchBox-dev/BenchBox/rulesets --jq '.[] | {id, name, target, conditions}'
```

Live verification on 2026-07-21 shows `v-tag-restricted` (ruleset id
`18774756`) is `active`, targets `refs/tags/v*`, has a `creation` rule, and
has the release-finalize bypass actor `User:57046` with `bypass_mode: always`.
`make release-finalize` needs that bypass to push its tag; confirm it stays
scoped to that identity.

An admin applied the ruleset on 2026-07-10, so no PR-side admin change is
needed. The original application command, kept as history:

```bash
gh api -X POST repos/BenchBox-dev/BenchBox/rulesets \
  -f name='v-tag-restricted' \
  -f target='tag' \
  -f enforcement='active' \
  -f 'conditions[ref_name][include][]=refs/tags/v*' \
  -f 'conditions[ref_name][exclude][]=' \
  -f 'rules[][type]=creation'
```

The live ruleset differs from this command: it also has the
release-finalize bypass actor (`User:57046`). To narrow the actors further,
for example to a release-bot identity, draft a new `bypass_actors` and
`rules` payload with the admin first. A write task's own PR cannot apply
it, because the develop-PR `GITHUB_TOKEN` has no `administration` scope to
read or write repository rulesets.

Drift detection (added 2026-07-05 under the
`tag-and-pypi-environment-admin-hardening` TODO):
`_project/scripts/ruleset_review_enforcement.py` has a
`tag_protection_findings()` predicate and an enforced `TAG_RULESET_ENFORCED`
flag. Feed it the live tag rulesets, fetching each one in full. The list
endpoint omits conditions and rules, which the predicate correctly treats as
"not protected".

```bash
ids=$(gh api repos/BenchBox-dev/BenchBox/rulesets --jq '.[].id')
for id in $ids; do gh api repos/BenchBox-dev/BenchBox/rulesets/$id; done \
  | jq -s '.' \
  | uv run -- python _project/scripts/ruleset_review_enforcement.py --rulesets-file -
```

`TAG_RULESET_ENFORCED` is `True`, so a missing or incomplete `v*`-tag
ruleset is a blocking finding and the check exits non-zero. Before the
ruleset was applied, the flag was `False` and such a ruleset printed only
a `WARNING (non-blocking):` line.

The predicate flags a ruleset that:

- is not `active`
- has a `ref_name.include` that does not cover `refs/tags/v*` (or `~ALL`) under
  GitHub's fnmatch ref-glob semantics. An `include` or `exclude` of
  `refs/tags/*` counts the same as the literal `refs/tags/v*`; matching is not
  limited to a byte-identical string.
- lacks a `creation`, `update`, or `deletion` rule. Without `update` and
  `deletion`, an actor with write access can delete a published `v*` tag and
  re-push it, or move it, after the release workflow admitted its commit.
- has `bypass_actors` explicitly set to `[]`. A structurally valid ruleset with
  zero bypass actors would block `make release-finalize`'s
  `git push origin v$(VERSION)` and break releases, so this is a finding, not
  an advisory.

Adding the `update` and `deletion` rules is an owner settings change. As an
admin, either add the two rules in the ruleset's settings page (Settings,
Rules, Rulesets, `v-tag-restricted`, Restrict updates, Restrict deletions) or
run:

```bash
gh api repos/BenchBox-dev/BenchBox/rulesets/18774756 \
  | jq '{name, target, enforcement, conditions, bypass_actors,
         rules: ((.rules | map({type})) + [{type: "update"}, {type: "deletion"}] | unique_by(.type))}' \
  | gh api -X PUT repos/BenchBox-dev/BenchBox/rulesets/18774756 --input -
```

Re-run `scripts/ruleset_drift_check.py` with an admin token afterwards. The
bypass actor still lets the release identity create tags; it can also move or
delete them, so the release workflow re-checks the hosted tag at publish time.

A non-empty `bypass_actors` list is not a failure, because
`make release-finalize` needs a bypass path to tag. The check instead prints a
`CONFIRM before enforcing:` line listing the bypass actors; verify the list is
the release identity only, not a broad Write or Admin role. An explicit
`enforce_tag_rule=False` call remains for migration fixtures, but the live
default is blocking.

CI wiring (added with the fnmatch and empty-bypass hardening above):
`scripts/ruleset_drift_check.py`'s `tag_creation_findings()` calls
`tag_protection_findings()` and `tag_bypass_advisory()` on every full ruleset
that `release-canary.yml`'s `ruleset-drift` job already fetches with
`RULESET_DRIFT_TOKEN` for the `develop-squash-only` and `release-only` checks,
so no second API call is needed. The daily canary run reports tag-ruleset drift
as a blocking finding, so it depends only on the canary running at all.

Live-state note:

```text
# Tag-creation ruleset live state
# checked: 2026-07-21  by: joeharris76 (admin)
# ruleset id: 18774756  enforcement: active  conditions.ref_name.include: [refs/tags/v*]
# rules: [creation]  bypass_actors: [User:57046 (always)]
```

Applied 2026-07-10: `v-tag-restricted` (id 18774756) is live and active. Its
bypass list is a single `User` actor (57046), confirmed to be the
release-finalize identity only, not a broad Write or Admin role;
`release-finalize`'s `git push origin v$(VERSION)` needs it. With this
confirmed, `TAG_RULESET_ENFORCED` in
`_project/scripts/ruleset_review_enforcement.py` is `True`, so a regression
(ruleset deleted, made inactive, ref narrowed, creation rule dropped, or bypass
emptied) is a blocking drift finding, not a warning.

### `pypi` environment required-reviewers gate (configured; observed 2026-08-10)

`release.yml`'s `publish` job scopes the real-PyPI publish to the GitHub
`environment: pypi` (and `test-pypi` for the test-PyPI path), the native
mechanism for a required-reviewers (human approval) gate. The workflow file
does not prove that `pypi` has `required_reviewers` configured; only a live
environments API read does. GitHub enforces a configured gate at deployment
time. `scripts/ruleset_drift_check.py` reads that API in the release-canary
drift job and fails closed when the environment, required-reviewers rule,
reviewer identity, admin-bypass posture, self-review posture, or deployment
policy differs from the pins below. It reuses `RULESET_DRIFT_TOKEN`, so that
token needs environment-read visibility as well as full ruleset visibility.

Verify:

```bash
gh api repos/BenchBox-dev/BenchBox/environments/pypi \
  --jq '{name, can_admins_bypass, protection_rules: [.protection_rules[] | {type, prevent_self_review, reviewers: [.reviewers[]?.reviewer.login]}]}'
```

Live verification on 2026-08-10 (command above) shows the gate is configured:

```text
# pypi environment live state
# checked: 2026-08-10  by: joeharris76 (admin)
# command: gh api repos/BenchBox-dev/BenchBox/environments/pypi --jq '{name, can_admins_bypass, protection_rules: [.protection_rules[] | {type, prevent_self_review, reviewers: [.reviewers[]?.reviewer.login]}]}'
# observed: {"name":"pypi","can_admins_bypass":true,"protection_rules":[{"reviewers":["joeharris76"],"type":"required_reviewers","prevent_self_review":false}]}
# type: required_reviewers  reviewer login(s): joeharris76  (User id 57046)
# can_admins_bypass: true  prevent_self_review: false  wait_timer: null  deployment_branch_policy: null
```

No admin change is needed. `can_admins_bypass: true` with `prevent_self_review:
false` is the accepted single-admin, self-review posture: one maintainer is both
the required reviewer and an admin who can approve or bypass their own
deployment. The release canary continuously detects a removed rule, an emptied
or replaced reviewer set, a deleted environment, or a changed bypass or
self-review flag. When it reports drift, re-run the verify command above to
diagnose.

Deployment policies (applied 2026-10-23): both environments admit only
selected refs, so a workflow run on an arbitrary branch cannot request them.
`pypi` accepts deployments from tags matching `v*` only. `test-pypi` accepts
tags matching `v*` and the `develop` and `release` branches, which keeps a
`release.yml` TestPyPI dispatch from those branches working. A manual
`release-v2.yml` dispatch deploys under the ref it was started from, so start
it from the release tag
(`gh workflow run release-v2.yml --ref vX.Y.Z -f tag=vX.Y.Z`); a dispatch from
a branch is refused by the `pypi` policy. The drift check pins both policies
and the `deployment_branch_policy` flags, and fails closed on an added,
removed or retyped policy.

Verify:

```bash
for e in pypi test-pypi; do
  gh api repos/BenchBox-dev/BenchBox/environments/$e \
    --jq '{name, deployment_branch_policy}'
  gh api repos/BenchBox-dev/BenchBox/environments/$e/deployment-branch-policies \
    --jq '[.branch_policies[] | {type, name}]'
done
```

`test-pypi` intentionally has no required reviewers, to keep dry-run publish
paths low-friction; its only protection is the deployment policy above. Observed
on 2026-08-05, before the deployment policy existed:

```bash
gh api repos/BenchBox-dev/BenchBox/environments/test-pypi --jq '{name, protection_rules}'
```

The command returned `{"name":"test-pypi","protection_rules":[]}`; it now lists
one `branch_policy` rule and still no `required_reviewers`. Do not copy the
real-PyPI `required_reviewers` rule onto `test-pypi` unless a future policy
explicitly wants that friction on the test path.

Re-apply reference (admin only, if the live check shows a gate or policy
missing). The environments `PUT` replaces the whole configuration, so always
send the reviewers and flags together with the deployment policy, then create
each policy:

```bash
gh api -X PUT repos/BenchBox-dev/BenchBox/environments/pypi \
  -F wait_timer=0 -F prevent_self_review=false -F can_admins_bypass=true \
  -f 'reviewers[][type]=User' -F 'reviewers[][id]=57046' \
  -F 'deployment_branch_policy[protected_branches]=false' \
  -F 'deployment_branch_policy[custom_branch_policies]=true'
gh api -X POST repos/BenchBox-dev/BenchBox/environments/pypi/deployment-branch-policies \
  -f name='v*' -f type=tag

gh api -X PUT repos/BenchBox-dev/BenchBox/environments/test-pypi \
  -F wait_timer=0 -F can_admins_bypass=true \
  -F 'deployment_branch_policy[protected_branches]=false' \
  -F 'deployment_branch_policy[custom_branch_policies]=true'
gh api -X POST repos/BenchBox-dev/BenchBox/environments/test-pypi/deployment-branch-policies \
  -f name='v*' -f type=tag
gh api -X POST repos/BenchBox-dev/BenchBox/environments/test-pypi/deployment-branch-policies \
  -f name=develop -f type=branch
gh api -X POST repos/BenchBox-dev/BenchBox/environments/test-pypi/deployment-branch-policies \
  -f name=release -f type=branch
```

## Release canary and ruleset drift

Release readiness has one scheduled and manual canary:

```text
workflow: .github/workflows/release-canary.yml
schedule: daily at 08:00 UTC
freshness_sla: 48h
blocking_suite: (slow or resource_heavy) and not (stress or live_integration)
advisory_suites: stress, live_integration, live cloud credentials
```

Long-running UAT is an advisory campaign; release readiness requires the
blocking release canary. For the optional UAT report, see
`docs/internal/release-guide.md` "UAT matrix campaign evidence (advisory)".

`validate-release-pr.yml` keeps the required context name `validate-base`, but
that job now also runs `scripts/release_readiness_check.py` for release PRs. It
fails when the latest completed `release-canary.yml` run is missing, red, older
than 48 hours, or when the tested `develop` SHA in the canary summary artifact
is not an ancestor of the release PR head. Scheduled canary runs start from the
default branch, then check out `develop` before running release evidence and
recording `commit_sha` in `release-canary-summary.json`. For the first release
that introduces `release-canary.yml`, before GitHub can run it from the default
branch, `validate-base` runs the same non-fast canary suite and ruleset drift
check inline as bootstrap evidence.

The ruleset drift check runs as an advisory job in `nightly.yml`, on manual
invocation, and as a blocking release check in `release-canary.yml` and the
`validate-release-pr.yml` bootstrap. It is not part of the required develop
`tooling` result. Nightly and canary drift jobs check out `develop`; the release
bootstrap restores the checker and its review helper from `develop` into the
curated release checkout. Only the drift step gets the admin-visible token.

The script parses this runbook for `develop-squash-only` and `release-only`,
then compares the live rulesets for required status check contexts, strict-base
settings, bypass actors, linear history, non-fast-forward protection, deletion
protection, and target refs. For `develop-squash-only` it also applies the
`review_enforcement_findings` predicate, as described in "Soundness-path
review enforcement" above.

The hosted checks use the repository secret `RULESET_DRIFT_TOKEN`, which has
enough ruleset write or admin visibility for the API to expose `bypass_actors`.
The default `GITHUB_TOKEN` is intentionally not used. If GitHub API access
fails, the canary is red, and release PRs then fail on stale or red canary
evidence instead of silently trusting comments. The "Bootstrap ruleset
drift evidence" step in `validate-release-pr.yml` runs the same script with
the same token, so both CI call sites get the same review-rule coverage
without a separate code path.

### Scheduled activation of `release-canary.yml` (RESOLVED 2026-07-08)

> **RESOLVED 2026-07-08 by the default-branch switch (Decision A,
> `branch-default-switch-to-develop`).** The GitHub default branch is now
> `develop` (`gh api repos/BenchBox-dev/BenchBox --jq .default_branch` →
> `develop`). GitHub runs `on.schedule` workflows only from the **default**
> branch's copy, so every scheduled workflow authored on `develop` now registers
> and fires directly; the "land it on `main`" problem below no longer exists.
> Verified 2026-07-08 with the Actions list-workflows API: `release-canary.yml`
> (id 309070628) and two other scheduled workflows (since retired) are
> registered (26 workflows, up from 19). Options (a), (b) and (c) and the admin
> steps below are **superseded** history. The `nightly.yml`
> `scheduled-workflow-liveness` guard now runs from `develop` directly, with no
> "until its file lands on `main`" caveat. The canary's first scheduled run is
> expected **RED** on the broken 0.3.0 PyPI release (see
> `release-recovery-v0-3-1`); that is the canary working, not a regression.

Historical live state observed 2026-07-05 (`release-canary-scheduled-activation`
TODO):

- `git ls-tree origin/main --name-only .github/workflows/` did **not** contain
  `release-canary.yml`; the file existed only on `develop`.
- The Actions list-workflows API returned 19 registered workflows, without
  release-canary (a workflow absent from the default branch with no historical
  runs is never registered). Its `on.schedule` cron had **never fired**, and
  none of its jobs (pypi-latest-installability, ruleset-drift,
  credential-free-non-fast, and the release-canary-result aggregator) had run.
- Same class, other instances: `phase3-promotion-review.yml` (quarterly) and
  `orphaned-commit-detector.yml` (weekly) were also on `develop` but absent from
  `origin/main`, so their schedules never fired either. The six-unit CI retired
  both, so this history needs no action.

Activation options considered while the default branch was still `main`:

- **(a) An admin lands the current `release-canary.yml` on `main` out of
  band** (cherry-pick or push through the documented admin flow; `main` is
  push-restricted, so this is admin-only). Fastest path, but the content on
  `main` goes stale between release cuts unless (c) also holds.
- **(b) Wait for the next release cut to carry it.** No extra action, but the
  canary stays dead until v0.3.1 ships: the exact window it guards.
- **(c) Keep `main`'s copy a minimal, stable scheduled shell that checks out
  `develop` for current logic.** The file already has this shape: the test and
  drift jobs check out `develop` through `RELEASE_CANARY_REF`, and
  pypi-latest-installability needs no checkout. Only future
  *workflow-structure* edits (job graph, permissions, schedule) need re-landing
  on `main`; test and drift *content* tracks `develop` automatically.

**Recommendation (recorded 2026-07-05): (a) and (c) together.** Land the
current `develop` copy of `release-canary.yml` on `main` once (it is already
the (c) shell), and keep the logic on `develop`.

Admin steps (maintainer action, never an agent push):

1. Land `develop`'s `.github/workflows/release-canary.yml` on `main` through the
   admin and release flow.
2. Confirm registration: the workflow appears in
   `gh api repos/BenchBox-dev/BenchBox/actions/workflows --jq '.workflows[].path'`.
3. Trigger `workflow_dispatch` (or wait for the next 08:00 UTC cron) and
   record the run URL here as proof of life.
4. **Expect the first result to be RED.** pypi-latest-installability correctly
   fails on the broken 0.3.0 PyPI release (`ModuleNotFoundError: pandas` on a
   clean install). That is the canary working. Cross-link the red run in
   `release-recovery-v0-3-1` as live pressure for the recovery release. Do not
   weaken the canary to get a green first run.

Once the canary is live, the `scheduled-workflow-liveness` job in
`nightly.yml` (added 2026-07-05) checks daily that every workflow in the
`develop` tree that declares `on.schedule` has an `event=schedule` run
within a window set by its cron: 3 days for daily, 9 for weekly, 35 for
monthly, and 100 when the cron names a month. A newly registered workflow
gets one window of grace before its first scheduled run, so a dead
scheduled workflow cannot go unnoticed.

The emergency override is intentionally explicit and SHA-scoped. Admins may set
both repository variables below, then remove them after the release:

```text
RELEASE_READINESS_OVERRIDE_SHA: exact release PR head SHA
RELEASE_READINESS_OVERRIDE_REASON: incident or approval record
```

The `validate-base` job summary records the override. Do not use it for routine
canary failures; fix the non-fast canary, ruleset drift, or GitHub API access
and let the canary return to green.

## Repository labels

The retired post-merge auto-revert job created the `incident:develop-red` and
`incident:develop-red-revert-conflict` labels on demand. No workflow, script,
test, or document reads them, and both labels were deleted from the repository
on 2026-10-27. The branch namespaces of the retired merge queue
(`gh-readonly-queue/develop/*`) and of the auto-revert job (`auto-revert/*`)
were deleted at the same time, together with the remote branches of closed and
merged pull requests. Neither namespace is created any more.

Verify:

```bash
gh label list --search incident
git ls-remote --heads origin 'gh-readonly-queue/*' 'auto-revert/*'
```

The label search lists only `incident:release-canary-red` and
`incident:todo-export-failed`; the branch listing is empty.

## Re-applying after a transfer or restore

For an ownership transfer, follow `docs/internal/github-org-transfer.md`, which
documents the 2026-08-21 cutover to org `BenchBox-dev`: gates G0–G7b, Pages
serving-only versus publish, the exclusive Pages domain lock (do not click org
Verify before G4; verify org `protected_domain` after G4), environment
`deployment-branch` policies, `RULESET_DRIFT_TOKEN` authentication, and
**never recreate** `joeharris76/BenchBox`.

The numbered steps below are the subset of the transfer runbook that still
applies after a backup restore, a ruleset wipe, or drift **without** a change
of owner. Re-apply in this order:

1. Workflow permissions (`gh api -X PUT … actions/permissions/workflow`).
2. Develop ruleset: recreate `develop-squash-only` with the required contexts
   above. The ruleset id will change; update this file and the `Makefile` and
   `scripts/` references that hard-code it. In workflows, prefer resolving the
   live ruleset **by name** (`develop-squash-only`); a stale numeric id is not
   authority.
3. Verify with the `gh api … rulesets/<id> --jq …` command above.
4. Open a no-op pull request against develop and confirm the five unit results
   and `oracle-gate` report on it. Arm the exact head for squash auto-merge
   and confirm its merged commit has a successful `trunk.yml` run. This
   validates workflow permissions end to end.

## Out-of-scope

This runbook covers only the GitHub admin settings that the dev-loop and
release workflows depend on, including the `pypi` and `test-pypi`
environments and the `RULESET_DRIFT_TOKEN` secret. Other settings are out
of scope: collaborators, other secrets, webhooks, Pages, other
environments, and deploy keys. If one becomes load-bearing, give it its
own runbook.
