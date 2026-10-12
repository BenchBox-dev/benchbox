# CI and dev-loop backlog: batch handoff (2026-10-12, revision 4)

**Current status:** See section 11 for the live snapshot of all 75 original items
and the two logged follow-ups, checked on 2026-10-12 at develop tip `cfbcae26d`, with the ordered runbook for all remaining work. Sections 3 and 10 are retained as history. The snapshot is cached: re-read each tracker item and PR
before acting; where this file and the tracker disagree, the tracker plus the
acceptance text governs, and this file's order and boundaries still bind.

For a new agent session. Self-contained. Read `AGENTS.md` first. Every decision
the work needs is settled in section 7, including the ones that were owner
decisions in the tracker; the owner delegated them to this handoff on
2026-10-09. Do not stop to ask about them. Stop only for the conditions in
section 5, "When to stop".

## 1. Objective and item set

Complete the 75 tracker items below with the Todo skill's `batch` action in
**serial mode** (not prepared/feature delivery mode). Code ships as ordinary
pull requests to `develop`. Some work is GitHub administration or workflow
dispatches with no PR; section 7 authorizes each such action.

Each item's description, acceptance list and context in the tracker is the
specification. They were rewritten on 2026-10-09 against `develop` and carry
evidence (`path:line`, run ids, PRs). Read each with `show_item` before
starting it. This handoff adds order, decisions and process; where an item says
"owner decision" or offers options, section 7 is the answer.

Done means: every non-waiting item below is `done` (or `dropped` where section 7
says so), each with the evidence its acceptance names; every calendar-,
programme-, or owner-gated item in section 4.8 is `waiting` with the exact
resume condition recorded in the ledger and, where the tracker supports it, a
`not_before` hold. A single session cannot reach 75 x `done` because canary
05/06 need first-run + 14 days, oracle cleanup is held to 2026-10-23, and
dlv2-84 needs +30 days. The terminal state of one session is therefore a
checkpoint: all else done, waiting items parked with holds. Report it as a
checkpoint, not as completion. Section 10 lists exactly what is still open with
the required steps per group; section 4.9 defines how to pipeline work while
PRs wait on CI and merge so the effort finishes quickly.

| Phase | Items |
|---|---|
| 1 CI signal | `oracle-verdict-workflow-not-soundness-path-20261009`, `ci-docker-hub-unauthenticated-pulls-20261009`, `public-site-visual-baseline-missing-for-base-20261009`, `nightly-windows-pr-visual-approval-20261009`, `t3-drift-corpus-mismatch-20261009`, `t3-drift-dispatch-verify-20261008`, `t3-perf-aggregate-gate-tolerance-decision-20261009`, `t3-durations-refresh-failure-20261009`, `flaky-bigquery-credential-defaults-confirm-prompt`, `heavy-tier-merge-group-dead-code-20261009` |
| 2 Windows | `nightly-v2-liveness-port-20261008`, `nightly-windows-verification-run-20261009`, `nightly-windows-unquarantine-20261008`, `nightly-windows-utf8-reads-20261008`, `agent-write-preflight-quoted-interpreter-20261009`, `windows-session-lock-holder-readable-20261009`, `windows-iceberg-vacuum-local-paths-20261009`, `windows-iceberg-file-uri-locations-20261009`, `windows-pytest-xdist-teardown-hang-20261009`, `nightly-v2-windows-unit-leg-20261009` |
| 3 Nightly T3 | `ruleset-drift-check-home-20261009`, `nightly-v2-throughput-uat-port-20261009`, `cross-surface-mutation-sensitivity-not-enforced-20261009`, `polars-2-compat-post-merge-coverage-20261009`, `nightly-v2-unported-jobs-decision-20261009`, `dlv2-22-nightly-t3-shadow`, `monitor-nightly-cedardb-throughput-gate-and-floor`, `dlv2-70-retire-scheduled-workflows` |
| 4 Oracle | `oracle-review-stale-check-runs-block-merge-20261008`, `oracle-muse-invalid-verdict-output-20261009`, `oracle-self-attested-file-list-reviewers-20261008`, `oracle-author-family-labels-never-applied-20261009`, `oracle-tier-reason-lists-every-file-20261009`, `oracle-strike-count-distinct-patches-20261008`, `oracle-reviewer-checkout-has-no-base-commit-20261009`, `oracle-read-evidence-tightening-20261008`, `oracle-absence-classification-lows-20261008`, `oracle-patch-hash-hunk-positions-20261008`, `oracle-runner-integrity-lows-20261008`, `pr-landing-soundness-refusal-unreachable-20261009`, `unattributed-update-branch-commits-block-merge-20261008`, `actions-runner-queue-oracle-latency-20261009`, `pr-2761-hollow-success-post-merge-review-20261008`, `oracle-checker-drop-legacy-first-line-20261023`, `review-soundness-merge-ceremony` |
| 5 Comment policy | `comment-policy-strict-scope-mismatch`, `comment-policy-digest-and-base-exception-catch22-20261008`, `comment-policy-pinned-notice-drift-guard`, `comment-policy-hook-saturates-shared-host`, `comment-scanner-stdin-piped-interpreter-adapter`, `comment-scanner-unresolved-runner-commands`, `comment-scanner-mdx-jsx-support` |
| 6 Canary | `canary-impact-03-replay-and-go-decision`, `canary-impact-04-shadow-job`, `canary-impact-05-shadow-watch`, `canary-impact-06-required-gate` |
| 7 Release and cleanup | `v-tag-ruleset-allows-tag-moves-20261009`, `dlv2-51-release-yml-v2`, `repo-admin-settings-actions-pr-permission-doc-20261009`, `sync-workflow-stale-branch-cleanup`, `dlv2-80a-github-cleanup-queue-era`, `dlv2-44-precommit-trim`, `dlv2-defer-tpch-digest-darwin-arm64`, `dlv2-52-release-rehearsal-and-first-release`, `dlv2-53-retire-legacy-release-path`, `dlv2-42-retire-dev-loop-scripts-tests` |
| 8 External-gated | `dlv2-62-publication-cutover-and-retirement`, `dlv2-80-github-cleanup`, `dlv2-81-config-cleanup`, `dlv2-82-makefile-rewrite`, `dlv2-83-docs-cleanup`, `dlv2-84-final-audit-and-metrics`, `site-split-52-ruleset-landing-context`, `tuning-liveness-e2e-gates-20261001`, `exasol-docker-ci-smoke` |

## 2. Tracker preflight

- Call the todo-db MCP `get_instructions` before any tracker write.
- State remote `https://github.com/BenchBox-dev/BenchBox`, state branch
  `todo-state`.
- Then `list_items(ready_only=true)`. Ready at handoff time in this set: most
  of phases 1, 4, 5 and the first items of 2, 3, 6 and 7; the rest wait on
  `needs` or `not_before`. Tracker `ready` means claimable; it never overrides
  section 4 order, item acceptance, or section 5 boundaries. Do not `take` an
  item out of section 4 order because it is ready. Do not `finish` an item
  whose acceptance needs samples, runs, or approvals the tracker does not
  encode (for example the CedarDB floor); park it `waiting` with a `not_before`
  hold instead.
- One live claim per worker: `take` one item, `finish` it with the `generation`
  from `take` before taking the next. When one PR covers several items, take
  the first, implement all of them on one branch, and after the PR merges take
  and finish each in turn.
- `update_item` replaces whole fields. To add context, pass the existing text
  plus a new paragraph. Parallel tracker writes can return `E_CONFLICT`; re-read
  and re-apply.
- `E_OFFLINE`: retry once later.

## 3. Initial state (revision 1; 2026-10-09; superseded by section 10)

- `origin/develop` is at `0a3c27f53` (#2849, 2026-10-09 21:48 UTC). The items
  were last checked against `34de720b8`; #2849 changed only Windows tests.
- The primary clone `/Users/joe/Developer/BenchBox` is on `develop` at
  `d6b966b48` with an uncommitted change to `quality/comment-policy.json` that
  belongs to someone else. Do not touch, commit or revert it.
- Required status contexts on ruleset `develop-squash-only` (15611785): `core`,
  `explorer`, `results-data`, `docs`, `landing`, `tooling`, `oracle-review`.
  Other rulesets: `release-only` (19149459, branch `release`),
  `v-release-branches-minimal` (15611787, `refs/heads/v*`, non-fast-forward
  only; no `v*` branch exists), `v-tag-restricted` (18774756, `refs/tags/v*`,
  `creation` only, bypass user 57046 always).
- Environments: `pypi` (required reviewers, owner), `test-pypi` (no rules),
  `oracle` (branch policy `develop`), `github-pages`, `publication-attestation`.
- Latest tags: `v0.4.2` (`627496f04`), `v0.4.1`, `v0.4.0`. `CHANGELOG.md` has an
  `[Unreleased]` section that starts with "Before you upgrade".
- Open PRs that items depend on:

  | PR | Head | State | Relevance |
  |---|---|---|---|
  | #2835 | `3d902ba85` | open; only "Public-site visual regression" failing | nightly-v2 Windows (`t3:windows`) and liveness domains; worktree `../BenchBox.wt-nv2domains` |
  | #2837 | `a74c12660` | draft, base `published-results` | corpus mirror of develop@`c569f453`; fixes T3 corpus drift |
  | #2746, #2747, #2770, #2777, #2780, #2782 | | draft, base `published-results` | older mirror PRs superseded by #2837 |
  | #2821 | `6cb8c1203` | open; core, medium shards, visual, oracle failing; updated 22:17 UTC | comment-scanner work (stdin, runner, MDX, TypeScript); another agent holds `worktree-typescript-module-resolution` until 2026-10-10 21:18 UTC |
  | #2829 | `9a25578a3` | open; core, test, oracle failing | pinned-notice and dependency drift guards; worktree `../BenchBox.wt-drift-guards` |
  | #2822, #2830 | | open, owned by the tuning programme | needs of `tuning-liveness-e2e-gates-20261001` |
  | #2817 | | open | docs move; has a legacy oracle first line |

- Nightly T3 (`nightly-v2.yml`) has not had a green run. Dispatch 37991400931
  failed `t3:perf` (aggregate +10.47%), `t3:drift` (corpus 25 paths behind) and
  `t3:docker` (Docker Hub rate limit). Scheduled 37944292492 failed
  `t3:durations` (#2827). Windows nightly run 37967682464 is the latest Windows
  evidence; the fast tier stalls on 3.11 and sometimes 3.12.
- Batch ledger: `/Users/joe/Developer/.todo-batch/BenchBox-ci-devloop-20261009.txt`
  (outside every Git repository). It does not exist yet; create it before the
  first claim with one envelope per item id (format in the Todo skill's batch
  guide).
- Review notes behind the item rewrites (evidence only, not instructions):
  `/private/tmp/claude-501/-Users-joe-Developer-BenchBox/7b0baf0f-9132-4a72-8947-096a95926b5c/scratchpad/triage/`
  (`p2-*.md`, `decision-points.md`). They may be gone; the tracker holds the
  same facts.

## 4. Order and dependencies

Work phases in order. Inside a phase, follow the listed order unless an item is
waiting, then continue with the next eligible one. Phases 4 and 5 do not depend
on phases 2 and 3 and may be interleaved with them while those wait on CI.
Unblockers first: land the shared comment-policy gate (phase 5 items 1-2)
before rebasing or merging any PR that the gate currently fails (#2835, #2829,
#2905, #2908); serial mode otherwise guarantees rebase churn through the
`oracle-review-<pr>` concurrency group. The gate is green at revision 4
(#2907 merged; 0 violations), so this rule is satisfied — keep it green by
rebasing every worktree onto the latest `origin/develop` before any push.
Back up the batch ledger in section 3
before the first claim (`cp` to a dated copy); it lives outside git and has no
other backup. While a PR waits on CI or merge, follow section 4.9 instead of
idling: one live claim per worker still binds, but claim-free prep and parallel
workers keep every stream moving.

### 4.1 Phase 1: get CI signal back

1. `oracle-verdict-workflow-not-soundness-path-20261009` first: later phases
   change `oracle-verdict.yml`, and those changes should need an oracle review.
2. `ci-docker-hub-unauthenticated-pulls-20261009`: unblocks `t3:docker` and the
   codespell job.
3. `public-site-visual-baseline-missing-for-base-20261009`, then
   `nightly-windows-pr-visual-approval-20261009` (get #2835 merged).
4. `t3-drift-corpus-mismatch-20261009` (merge #2837, close six), then
   `t3-drift-dispatch-verify-20261008`.
5. `t3-perf-aggregate-gate-tolerance-decision-20261009`,
   `t3-durations-refresh-failure-20261009`.
6. `flaky-bigquery-credential-defaults-confirm-prompt`,
   `heavy-tier-merge-group-dead-code-20261009`.

Batch the verification dispatches: once items 2, 4 and 5 have merged, one
`nightly-v2` dispatch with `run_drift=true` serves all of them.

### 4.2 Phase 2: Windows

1. `nightly-v2-liveness-port-20261008`: lands with #2835 (decision D-W1).
2. `nightly-windows-verification-run-20261009`: after #2835 merges, one
   `nightly.yml` dispatch from `develop`.
3. `nightly-windows-unquarantine-20261008` from that evidence.
4. One PR for `nightly-windows-utf8-reads-20261008`,
   `agent-write-preflight-quoted-interpreter-20261009` and
   `windows-session-lock-holder-readable-20261009` (test and script fixes).
5. `windows-iceberg-vacuum-local-paths-20261009` (deletes files; own PR).
6. `windows-iceberg-file-uri-locations-20261009` (own PR).
7. `windows-pytest-xdist-teardown-hang-20261009` (investigation, then fix or
   skip).
8. `nightly-v2-windows-unit-leg-20261009` last: needs 4 to 7 and three
   no-stall Windows runs.

### 4.3 Phase 3: Nightly T3 complete, then retire nightly.yml

1. `ruleset-drift-check-home-20261009` (must land before dlv2-53 and dlv2-70).
2. `nightly-v2-throughput-uat-port-20261009`.
3. `cross-surface-mutation-sensitivity-not-enforced-20261009`.
4. `polars-2-compat-post-merge-coverage-20261009`.
5. `nightly-v2-unported-jobs-decision-20261009` (ports per D-T6).
6. `dlv2-22-nightly-t3-shadow`: after phase 1 and items 1 to 5, two or three
   green dispatches.
7. `monitor-nightly-cedardb-throughput-gate-and-floor`: waits for samples
   (section 4.8).
8. `dlv2-70-retire-scheduled-workflows`: after dlv2-22, the Windows unit leg,
   items 1 to 5, and one green scheduled nightly-v2 run.

### 4.4 Phase 4: Oracle

Every PR here is very-high tier and is reviewed by the oracle running
`develop`'s code, so a PR cannot test its own reviewer change in CI. Each item
names its post-merge check. Do not `finish` a phase 4 item on merge alone:
attach the named post-merge evidence (run id, head, verdict) to the item
context first, then `finish`. Order:

1. `oracle-muse-invalid-verdict-output-20261009` evidence capture **before
   2026-10-16** (the attempt artifacts expire then); the code change can come
   later.
2. `oracle-review-stale-check-runs-block-merge-20261008` is done; PR #2900
   merged on 2026-10-11. Skip its implementation. Use its recovery command
   from `docs/internal/soundness-drain.md` if another PR carries stale checks.
3. One PR: `oracle-author-family-labels-never-applied-20261009`,
   `oracle-tier-reason-lists-every-file-20261009`,
   `oracle-strike-count-distinct-patches-20261008`.
4. One PR: `oracle-muse-invalid-verdict-output-20261009` (parser),
   `oracle-self-attested-file-list-reviewers-20261008`.
5. `oracle-reviewer-checkout-has-no-base-commit-20261009`, then
   `oracle-read-evidence-tightening-20261008`.
6. One PR: `oracle-absence-classification-lows-20261008`,
   `oracle-patch-hash-hunk-positions-20261008`,
   `oracle-runner-integrity-lows-20261008`.
7. `pr-landing-soundness-refusal-unreachable-20261009`.
8. `unattributed-update-branch-commits-block-merge-20261008`.
9. `actions-runner-queue-oracle-latency-20261009` (measure across one working
   day while other work runs).
10. `pr-2761-hollow-success-post-merge-review-20261008`.
11. On or after 2026-10-23: `oracle-checker-drop-legacy-first-line-20261023`,
    then `review-soundness-merge-ceremony`.

### 4.5 Phase 5: Comment policy

1. `comment-policy-strict-scope-mismatch`.
2. `comment-policy-digest-and-base-exception-catch22-20261008`: removes most
   two-PR friction for everything after it.
3. `comment-policy-pinned-notice-drift-guard` (rework #2829).
4. `comment-policy-hook-saturates-shared-host` (record the baseline first: the
   runner-commands item measures against it).
5. PR #2821 has merged. `comment-scanner-unresolved-runner-commands` is done;
   `comment-scanner-stdin-piped-interpreter-adapter` and
   `comment-scanner-mdx-jsx-support` remain open. Verify each acceptance against
   the merged PR and close or fix only the remaining work.

### 4.6 Phase 6: Canary

03, then 04, then 05 (14-day window), then 06. These are serial by design.

### 4.7 Phase 7: Release and cleanup

1. `v-tag-ruleset-allows-tag-moves-20261009`.
2. `dlv2-51-release-yml-v2` (ready now; independent of Nightly T3).
3. `repo-admin-settings-actions-pr-permission-doc-20261009`,
   `sync-workflow-stale-branch-cleanup` (after #2837 merges),
   `dlv2-80a-github-cleanup-queue-era`, `dlv2-44-precommit-trim`,
   `dlv2-defer-tpch-digest-darwin-arm64`.
4. `dlv2-52-release-rehearsal-and-first-release` after dlv2-51 and dlv2-22.
5. `dlv2-53-retire-legacy-release-path` after dlv2-52 and the ruleset-drift
   home.
6. `dlv2-42-retire-dev-loop-scripts-tests` after dlv2-70.

### 4.8 Original anticipated waits (2026-10-09; current state is in section 10)

| Item | Waits for | Resume when |
|---|---|---|
| `canary-impact-05`, `-06` | 14 days of shadow runs after 04's first live run | first-run date + 14 days (set `not_before` on 05 when 04 records the date) |
| `monitor-nightly-cedardb-throughput-gate-and-floor` | at least 5 same-runner-class scheduled samples | the floor log says 5 of 5; until then leave the item unclaimed and record the sample count on the item |
| `oracle-checker-drop-legacy-first-line-20261023`, `review-soundness-merge-ceremony` | `not_before` 2026-10-23 | that date |
| `dlv2-52` (final release) | owner approval of the `pypi` deployment | owner approves; then verify per the item |
| `dlv2-53`, `dlv2-42`, `dlv2-70` | the chain above | their `needs` are done |
| `dlv2-62`, `dlv2-80`, `-81`, `-82`, `-83`, `-84`, `site-split-52` | the site-split programme (site-split-50, -51, -53, -54, -99) | those items are done; do not work the site-split programme in this batch |
| `tuning-liveness-e2e-gates-20261001` | #2822 → `tuning-sort-evidence-attested-20261001`, and #2830 (tuning programme, other owners) | both needs done |
| `exasol-docker-ci-smoke` | owner priority for Exasol (gate G1); not given | out of this batch; leave `blocked` |
| `dlv2-84` 30-day metrics | 30 days after the last cleanup merges | the follow-up item's `not_before` |

### 4.9 Pipelining while PRs wait on CI and merge

One live claim per worker still binds: a session that holds a claim on a
`pr_open` item keeps it until merge, then finishes with the merge evidence.
Speed comes from parallel workers and claim-free prep, not from holding two
claims. Assign at most one workstream per worker from the table below; a
second worker takes the next stream. A single worker with a PR waiting on CI
never idles: the right column lists claim-free work that needs no `take`.

| Workstream | Entry condition at revision 4 | While its PR waits on CI, do claim-free |
|---|---|---|
| A Comment-policy gate | gate green; strict-scope fix implemented but uncommitted in `../BenchBox.wt-comment-policy-strict-scope` (branch `fix/comment-policy-strict-scope`, based on `78f51c3cb`) | rebase that worktree onto latest `origin/develop`, re-run `make comment-policy-strict` and the pinned-wheel test, then take `comment-policy-strict-scope-mismatch` and open its PR |
| B Oracle batch | #2911 open (absence fix, CI green, oracle pending) | capture the raw muse evidence for `oracle-muse-invalid-verdict-output-20261009` (artifacts expire 2026-10-16): local `muse exec` first, else a diagnostic CI run uploading redacted output; capturing needs no claim, `take` only to record and finish |
| C Phase-1 verification | A merged; #2837 already merged | fire one combined `nightly-v2` dispatch with `run_drift=true` (D-A7) serving the Docker, drift-corpus and perf-floor items together, then take and finish each from its evidence |
| D Windows | #2835 merged | dispatch one `nightly.yml` run from `develop` for `nightly-windows-verification-run-20261009`; while it runs, prep the UTF-8/preflight/lock-holder PR in a fresh worktree |
| E Release rehearsal | dlv2-51 ready; dlv2-22 needs C | rehearse `0.5.0rc1` against `test-pypi` up to (not through) the owner `pypi` approval; a missing TestPyPI trusted publisher is a stop per section 5 |

Merged-but-open items need no new code: where a merged PR already satisfies
an item's acceptance (#2829 satisfies the pinned-notice drift guard,
#2835 satisfies the visual-approval and liveness items, #2907 satisfies the
shell-payload follow-up), `take` the item, verify the merge SHA plus the
named evidence, and `finish` with the generation. Do this before opening new
PRs in the same area. Rebase every worktree onto the latest `origin/develop`
before any push: develop moves several PRs per day and a stale base rebuilds
baselines and re-triggers oracle reviews through the
`oracle-review-<pr>` concurrency group.

## 5. Rules and boundaries

From `AGENTS.md`, the item descriptions and the owner's standing instructions:

- Worktrees only: `make worktree-create BRANCH=<prefix>/<slug>
  WORKTREE_PATH=../BenchBox.wt-<slug>` (prefix `fix/`, `feat/`, `chore/` or
  `docs/`), `cd` there, `make agent-write-preflight`. Never write in the primary
  clone. Remove each worktree after its PR merges. Reuse an existing worktree
  only for a PR this batch takes over (#2835, #2829).
- Git identity Joe Harris <joeharris76@gmail.com> (check before the first
  commit). No attribution lines in commits, PR text or comments. Stage explicit
  paths; never `git add -A`. `git mv` for renames.
- Python through `uv run -- ...`. Tests: `uv run -- pytest <paths>` (the repo's
  `pytest.ini` loads the session-isolation plugin; `-o addopts=""` breaks tests
  that call `session_home()`).
- No explanatory comments or docstrings in code, tests or workflow `run:`
  blocks (COMMENT-POLICY-001); run `make comment-policy-check`.
- Durable text (commits, PR titles and bodies, docs, issue comments) is written
  for a reader who never saw this session: no tracker ids, "handoff", "batch",
  "phase", CI counts or session narration.
- Before publishing each PR: self-review with the `code` review action and fix
  Critical/Required findings; `make pr-preflight` once; `make pr-open`.
- Never weaken the required `oracle-review` check, its stand-in rule, or the
  soundness path list. Adding paths is allowed. Do not post stand-ins; the
  oracle decides every soundness PR in this batch.
- Never use GitHub's "Update branch"; rebase locally and push
  `--force-with-lease`.
- Never approve a deployment to the `pypi` environment, and never run live
  cloud tests (`BENCHBOX_T3_CLOUD_ENABLED` stays unset).
- Do not run `agy` with `--dangerously-skip-permissions`.
- Do not work items outside section 1, and do not take over PRs or claims owned
  by other sessions except as section 7 allows.
- Check CI on a schedule, never in a tight loop. One dispatch per verification
  need; when everything waits on CI, checkpoint the ledger and end the
  invocation (the batch guide's resume rule).

**When to stop** (report with the ledger path and exact next action):
a denied permission; an owner-only action that section 7 does not authorize
(for example creating a TestPyPI trusted publisher, adding a secret, a paid
plan); the oracle listing a Critical/High defect you cannot fix within the
item's scope; any change that would weaken a required check or the soundness
list; a failing check outside these items that blocks merge after one retry;
a destructive admin action whose before-state differs from what the item
documents.

## 6. Known failure modes

- **Stale failing `oracle-review` runs block merge** until fixed by phase 4
  item 2. Re-run failed runs one at a time (`gh run rerun <id>`, wait for each);
  parallel re-runs cancel each other through the `oracle-review-<pr>`
  concurrency group. Close and reopen the PR to fire a fresh `pull_request`
  event if re-runs reuse a stale payload.
- **A rebase or new push changes the head.** The oracle reviews the new head:
  an unchanged patch is carried, a changed one gets a follow-up review.
- **Three DO NOT SHIP decisions** make the oracle refuse a PR. Open a new PR
  from the same branch and close the old one. A tier label or retarget after a
  DO NOT SHIP can double-count a strike (phase 4 item 3 fixes it).
- **Oracle dispatch** runs only from `develop`:
  `gh workflow run oracle-verdict.yml --ref develop -f pr=<n>`. A dispatched run
  does not count for the required check; a `pull_request` event does.
- **Comment policy reads exceptions only from the base**, and some are keyed to
  a whole-file digest. Until phase 5 item 2 lands, an edit to such a file needs
  its digest registered in `quality/comment-policy.json` in a separate small PR
  first. Put new tests in new files to avoid it. `sha256sum -c` in workflow
  scripts is flagged; use `test "$(...)" = ...`.
- **Public-site visual regression fails when the base's baseline does not exist
  yet** (the Documentation push for the base runs about 80 minutes). Re-run the
  failed workflow after the base's Documentation run finishes, or dispatch
  `gh workflow run docs.yml --ref develop -f baseline_source_sha=<base sha>`.
- **Docker Hub rate limits** fail `t3:docker` and codespell until phase 1 item 2.
  Do not count those failures against code changes.
- **Windows cannot be reproduced on this Mac.** Windows fixes need a dispatched
  Windows run; one dispatch per verification need.
- **The Windows fast tier stalls at 90–95%** and runs to the 90-minute job
  timeout. Read by step result, not job conclusion.
- **zsh does not word-split `$VAR`;** use `${=VAR}` or `xargs`. macOS has no
  `timeout`.
- **Codex in CI** needs the AppArmor user-namespace sysctl already in the oracle
  workflow; do not remove it.
- **Other sessions are active** in this repository (tuning, site-split,
  comment-scanner). Before taking over any PR or worktree, check the tracker
  claim and the PR's last update.

## 7. Decisions (all settled)

The owner delegated every decision below on 2026-10-09. Record each in its
item's context when you apply it ("Decision (owner, 2026-10-09): ...").

### Owner actions authorized for this batch

- **D-A1 Merge #2837.** Mark it ready, confirm its `corpus-mirror/validate-bundles`
  check passed, that its diff touches only the mirrored corpus paths, and that
  it was built from the current `published-results` tip (`2f9298eb3`; if the tip
  moved, re-run the sync workflow and use the new PR instead). Merge it with the
  method earlier mirror PRs used (check #2727). Then close #2746, #2747, #2770,
  #2777, #2780 and #2782 with the comment "Superseded by #2837." and delete their
  branches.
- **D-A2 Public-site visual approval slot.** For a batch PR whose only failure is
  changed public-site captures that the PR did not cause (the same captures
  differ on a docs-free PR against the same base, or the base baseline was
  rebuilt by a merged develop change), review the
  `public-site-visual-diagnostics-*` artifact, then set repository variables
  `APPROVED_HEAD_SHA=<full head sha>` and `APPROVAL_REASON=<one line naming the
  develop change that caused the diff>`, re-run the failed workflow, and clear
  both variables immediately after that run starts, whether it passes or fails.
  Confirm the clear with a variables read before any other push. Each use needs
  its own one-line reason tied to the current head; a new head needs a new
  review. Never use the slot for a capture the PR changed on
  purpose without describing it in the PR, and never for a missing capture.
- **D-A3 `v-tag-restricted` ruleset.** Add `update` and `deletion` rules to
  ruleset 18774756, keeping the existing bypass actor. Record the rule set in
  `scripts/ruleset_drift_check.py`. The ruleset alone does not stop bypass actor
  57046, so enforcement for release tags also lives in the dlv2-51 publish-time
  tag check, which must run under the `pypi`/`test-pypi` environment protection
  (not as an unprotected workflow step on `develop`), and changes to that check
  are themselves soundness-path changes requiring oracle review. Also do the publish-time tag check in
  dlv2-51 (the bypass means the ruleset alone does not stop the owner account
  that agents share).
- **D-A4 `v-release-branches-minimal`.** Delete ruleset 15611787 in dlv2-53
  together with 19149459. No `v*` branch exists and the trunk release flow
  creates none.
- **D-A5 GitHub cleanup.** dlv2-80a and dlv2-80 carry standing approvals for
  branch, label and secret deletion; archive tags first as they say.
- **D-A6 Tags.** You may create the annotated release-candidate and release tags
  in dlv2-52. Publishing to PyPI stays behind the owner's `pypi` approval.
- **D-A7 Dispatches.** You may dispatch `nightly-v2.yml`, `nightly.yml`,
  `docs.yml` and `oracle-verdict.yml` from `develop` as the items need, one per
  verification need.

### Phase 1

- **D-1 Docker Hub.** Add no Docker Hub secret. Pull official images from
  `public.ecr.aws/docker/library/<image>`. For vendor images (Trino,
  ClickHouse, CedarDB, any other), use the vendor's non-Docker-Hub registry if
  it publishes one; otherwise copy the pinned tag to
  `ghcr.io/benchbox-dev/<image>` with a workflow that uses `GITHUB_TOKEN`
  (`packages: write`) scoped to the copy job and pull from there. Pin digests where the workflow pins
  them today, and record image provenance on the item. Install codespell with uv instead of the Docker action.
- **D-2 Visual baseline timing.** Do both: set
  `PUBLIC_SITE_VISUAL_BASELINE_WAIT_SECONDS` in `ci.yml` to cover the
  Documentation push run (measure its p90 duration from recent develop runs and
  round up to the next 10 minutes), and narrow `SITE_PATHS` in `docs.yml` to the
  files the site build reads so the site-equivalent ancestor fallback works.
  The comparison itself does not change.
- **D-3 #2835.** Rebase it on `develop`. If the visual job still fails only on
  captures it did not cause, apply D-A2. Merge it whole (Windows and liveness
  together) if it is green; split liveness into its own PR only if the Windows
  half is still blocked when phase 2 starts.
  Revision 4: #2835 merged on 2026-10-12. Its items still need `take` plus
  merge-SHA evidence and `finish`; do not reopen the PR.
- **D-4 Perf aggregate floor.** Option (c) is decided. Add
  `--min-aggregate-regression-delta` to `benchbox compare`; the aggregate fails
  only when it exceeds both the percentage and the floor. Set the floor to the
  same-runner-class spread rounded up to the next 10 ms: 70 ms today (604.7 −
  539.8 = 64.9). Record the method in `docs/development/perf-smoke.md`, pass the
  flag in `nightly-v2.yml` and `perf-smoke.yml`, and fix that doc's "Per-PR"
  wording and its "re-run once" advice (no blind re-runs).
- **D-5 Flaky BigQuery test.** Fix the cause; no retry, skip or loosened
  assertion. Add `rich.prompt.Confirm` to `REGISTERED_GLOBALS`.

### Phase 2

- **D-W1 Liveness.** Ship as the `t3:liveness` domain from #2835 (port, not a
  "not needed" reason).
- **D-W2 UTF-8.** Fix each read with `encoding="utf-8"`; do not set
  `PYTHONUTF8=1`. Extend `scripts/check_windows_antipatterns.py` to `tests/` and
  `scripts/` with a ratchet baseline of today's count.
- **D-W3 Preflight quoting.** Strip one pair of matching surrounding single or
  double quotes from `INSTALL_PYTHON`; new Linux test in a new test file.
- **D-W4 Session lock.** Lock one byte at offset `1 << 30`; if a Windows run
  shows that fails, skip both tests on Windows with the reason. One green Windows
  run is not conclusive on the stalling fast tier (section 6): require two
  consecutive no-stall runs before removing skips or closing the xdist/Iceberg
  verification.
- **D-W5 Iceberg file URIs.** On `win32` only, write the table location as
  `str(path)`. Verify on a Windows run that pyiceberg opens it; if it does,
  remove the three skips; if not, revert to the current form, keep the skips
  and document Windows Iceberg locations as unsupported.
- **D-W6 xdist stall.** Turn on `faulthandler_timeout` (and per-worker
  progress) in the Windows job to find the stuck test. Fix it if the cause is in
  BenchBox code or tests; otherwise skip that test on Windows with the reason
  and the dump excerpt.
- **D-W7 Windows unit leg timeout.** Start at 75 minutes; after the first
  no-coverage run set it to 1.5 × that run's fast-test step, rounded up to 5
  minutes.
- **D-W8 Unquarantine.** Remove the quarantine from a leg only if every step
  passed in the verification dispatch; a failing leg keeps it and gets an item.

### Phase 3

- **D-T1 Ruleset drift home.** A `t3:ruleset-drift` domain in `nightly-v2.yml`
  using `RULESET_DRIFT_TOKEN` stored as an Actions secret with the minimum scope
  the drift script needs; never log it, and rotate it if a run leaks it.
- **D-T2 Throughput port.** A `t3:throughput` domain. The floor-history lookup
  reads scheduled runs of both `nightly.yml` and `nightly-v2.yml` (and both
  artifact name forms) during the transition, so the sample count carries over.
- **D-T3 CedarDB connection loss.** Inspect the container logs of the next
  dispatched throughput run. If the SSL EOF does not recur in two runs, record
  it as a known flake with the evidence.
- **D-T4 Mutation sensitivity.** A `t3:mutation` domain in `nightly-v2.yml` that
  opens an issue on failure (not blocking trunk). Reuse `t3:durations` data over
  new instrumentation where the item's acceptance allows it.
- **D-T5 Polars 2 coverage.** Add the job to `trunk.yml` if its runtime is 10
  minutes or less; otherwise a nightly-v2 domain.
- **D-T6 Unported nightly.yml jobs.** Rule: port unless a kept workflow already
  runs the same tests or check at the same or higher cadence (cite it), or the
  job monitors machinery being retired. Settled calls:

  | Job | Disposition |
  |---|---|
  | `test` Linux legs | covered by `trunk.yml` fast and medium tiers |
  | `test` macOS legs | port: a macOS 3.12 fast-tier nightly-v2 domain |
  | `test` Windows legs | `t3:windows` (3.12); the 3.11 Windows leg is dropped (3.11 is covered on Linux and macOS by the nightly-v2 matrix) |
  | `slow-tier-observation` | drop; its two uses move to `t3:mutation` (D-T4) and `t3:durations` |
  | `duckdb-preview` | port (issue-opening) |
  | `binary-framing` | covered if the nightly-v2 matrix runs the framing test for every bundled binary; otherwise port |
  | `check-test-patterns` | covered if `ci.yml` runs the same check; otherwise port |
  | `security` | port |
  | `sqlglot-generator` | port |
  | `integration-smoke` (incl. Windows) | port |
  | `integration-table-formats` | port |
  | `postgres-integration` | covered if `t3:docker` postgres runs the same tests; otherwise port |
  | `cloud-live-plan-capture` | move into the existing `t3:cloud` domain (stays off) |
  | `integration` | port |
  | `test-package` | covered if `t3:install` builds and installs the package the same way; otherwise port |
  | `parity` | port |
  | `website-audit` | port as-is; site-split-50 deletes it later |
  | `scheduled-workflow-liveness` | `t3:liveness` (D-W1) |
  | `throughput-uat`, `throughput-uat-signal` | `t3:throughput` (D-T2) |
  | `pyspark-tests` | port |
  | `green-unmerged-sweep` | drop (queue-era sweep; dlv2-42 deletes the script) |
  | `ruleset-drift` | `t3:ruleset-drift` (D-T1) |

  `extension-smoke.yml`: delete in dlv2-70 if `t3:extension` covers it.
- **D-T7 dlv2-22 acceptance.** Two green dispatches are enough when both pass
  every non-cloud domain; `t3:cloud` stays off and is not required.

### Phase 4

- **D-O1 Stale check runs.** Preferred design: report `oracle-review` once per
  head so a later result replaces an earlier one (for example a commit status
  named `oracle-review` written on the head by the evaluation, triggered by
  `pull_request` and by the oracle post job, with the review trigger removed).
  Prove it on a canary PR: an early failure followed by an oracle success leaves
  the PR mergeable with no re-run, and no path produces a skipped (passing)
  required check. If that cannot be proven inside the item, ship the floor
  instead: one documented command (a Make target that re-runs the PR's failed
  `oracle-review` runs serially and fires a fresh `pull_request` event), and
  fix `docs/operations/soundness-drain.md:30-32`. Either way, thread replies
  must start at most one evaluation.
- **D-O2 Author family.** Decided: delete the label lookup, hard-code the claude
  exclusion (keep its current effect; opus stays diversity-exempt at very-high).
- **D-O3 Muse output.** Capture raw muse output first (local `muse exec` if the
  CLI works here, else a diagnostic CI run that uploads raw redacted output).
  Then: `parse_output` takes the last complete JSON object that validates as a
  verdict (as the claude stream fallback does), and the muse prompt restates the
  required keys. No reviewer-order change.
- **D-O4 Self-attested muse.** Set muse `reads_files: false` until it leaves a
  read trace; record the cost (more pending results when codex is out of quota)
  in `_project/decisions/oracle-review-protocol-2026-10-08.md`.
- **D-O5 Tier reason.** Cap each reason at the first 10 paths plus "and N more",
  in `classifier.py` (the plan keeps the full list only where it already does).
- **D-O6 Strike count.** Fix in `report.finalize`: count a strike only when the
  current digest is not already in the history. The unreadable-diff case
  (`unread-<head>`) is scoped out explicitly with the reason: an unreadable
  diff yields a pending result, not a DO NOT SHIP, so it never adds a strike.
  Verify that claim in the code; if it is false, give it a stable digest of the
  PR's base and head trees instead.
- **D-O7 Base commit.** A second shallow fetch: `git fetch --depth=1 origin
  <base_sha>` after the head checkout (public repository; no credentials).
- **D-O8 Read evidence.** Implement the acceptance list as written, against the
  base-ref commands D-O7 makes possible.
- **D-O9 Absence lows.** Redact the auth detail with the same `excerpt()`
  redaction as other details; parse the claude and codex reset formats the item
  lists; record the empty muse quota table as accepted with the reason unless a
  real muse quota message is captured during the batch.
- **D-O10 Patch hash.** Keep the function-context text after `@@` in the hashed
  hunk header; drop only the line numbers.
- **D-O11 Runner integrity.** (1) If any reviewer credential is an OAuth
  refresh-token file that a run can rotate (codex `auth.json`, claude
  credentials), restore a per-credential concurrency group
  (`oracle-cred-<reviewer>`, `cancel-in-progress: false`) on the attempt jobs;
  otherwise accept the removal and document why. (2) A later pending or
  withheld oracle review does **not** void a stand-in; only a later decisive
  verdict does. Document it in `docs/operations/oracle-review-v2.md`.
- **D-O12 pr_landing.** Make `ready_failures` require a passing `oracle-review`
  check for soundness PRs (as `pr_arm.py` does) instead of APPROVED, and delete
  the unreachable `arm_current_pr` soundness branch.
- **D-O13 Update branch.** Document the rule in
  `docs/operations/repo-admin-settings.md` and add a warning to the PR tooling
  when the head's newest commit has committer `web-flow`. Small additions to
  that doc are fine; dlv2-83's rewrite of it waits for site-split-53.
- **D-O14 Runner queue.** No paid plan. After measuring, implement only changes
  that cut concurrent jobs without weakening a required check (cancel
  superseded runs, merge small oracle jobs, path-filter heavy jobs), in this
  item rather than new items.
- **D-O15 #2761 review.** Run the hardened reviewer locally (the oracle runner
  with the real codex or claude harness) on the nine soundness files of
  `47ee38575^..47ee38575`, with read evidence. Record the verdict on the item.
  Fix any Critical or High defect in a PR; record lower ones on the item.
- **D-O16 Legacy first line (after 2026-10-23).** For each open PR whose latest
  oracle review still uses the old first line, re-run the oracle (a new push or
  `/oracle-review` comment), then remove the legacy line and the rename leftovers
  the item lists.
- **D-O17 Merge ceremony review (after 2026-10-23).** Write the review the item
  asks for. Keep the soundness path list and the ceremony as they are (the owner
  does not weaken the list); record the recommendations in a decision record and
  close the item.

### Phase 5

- **D-C1 Strict scope.** As the item says: strict applies the ownership manifest
  by default and the stale wheel entry becomes 0.9.1.
- **D-C2 Same-PR exceptions (the catch-22 owner decision).** A PR's own new
  exception entries count in PR mode only when the PR changes
  `quality/comment-policy.json`. That file is on the soundness list, so the
  required oracle review covers every such exception; no exception can pass
  without that review. Keep the base-only rule for everything else. Narrow the
  digest to the consuming symbol plus the bindings it resolves through, and fix
  `docs/development/comment-policy.md:90`. Ordering warning: while the gate
  fails on a PR, that PR cannot merge to earn its own exception. Land the
  strict-scope and catch-22 fixes first; do not rebase gated PRs (#2835, #2829,
  #2905, #2908) onto exception-dependent heads to get around the gate, and do
  not add broad exceptions to force them green.
- **D-C3 #2829.** Take it over (its worktree is `../BenchBox.wt-drift-guards`;
  check for a live claim first). Move the pinned-notice check into the required
  gate (`scripts/run_comment_policy.py`), cover `LICENSE` and `COPYRIGHT.md`,
  drop the message for external notices (they already fail closed), and fix the
  two tests it breaks. Before merging, confirm `make audit-raw-check` fails on
  drift (its `ci.yml` step is `continue-on-error`) and finish
  `dependency-audit-raw-drift-guard` only if it does.
  Revision 4: #2829 merged on 2026-10-12. Verify the merged head against the
  acceptance (drift step in the required gate, LICENSE/COPYRIGHT coverage, no
  external-notice message, `make audit-raw-check` fails on drift), then take
  and finish the item; no takeover needed.
- **D-C4 Hook cost.** Per-blob result cache keyed on the checker digest, the
  policy digest, the blob id and the digests of any file it resolves payloads
  from; full scan when the checker or policy changes. Measure before and after
  on a quiet host (load below 2.0). If the gate already meets its budget, keep
  the measurement and skip the cache.
- **D-C5 #2821 (superseded: #2821 merged 2026-10-11).** Do not take over or
  split #2821. Verify `comment-scanner-stdin-piped-interpreter-adapter` and
  `comment-scanner-mdx-jsx-support` against the merged head: close each only
  with the acceptance evidence on the item, otherwise open a narrow follow-up
  PR per remaining gap (stdin, MDX), each with a regression test, with MDX gaps
  failing closed (coverage error). Carry over oracle findings D1 to D4 from the
  merged review the same way. The TypeScript part stays with its own item.

### Phase 6

- **D-K1 Canary placement (D1).** Post-merge in `trunk.yml` (option a), as a
  job-level `continue-on-error` shadow. The pre-registered criteria stand:
  recall 100% on the replay, mean ≤40 runner-minutes per merge, p90 wall ≤30
  minutes, fallback rate above 30% reported, all normalized to the same runner
  class and recorded with owner, window, and run ids on the item. Go if met; if not, drop 04 to 06
  with the evidence and delete `scripts/canary_impact.py` and its tests in
  dlv2-42.
- **D-K2 Promotion (D2).** At day 14: promote (06) if zero misses, at least 20
  shadow runs and cost within D-K1; if fewer than 20 runs, extend 7 days once;
  if a miss occurs, fix the selector and restart the 14 days once, and on a
  second miss stop and drop 06 with the evidence.
- The oracle review is the maintainer review for these workflow PRs; merge by
  hand after checks pass, not by auto-merge.

### Phase 7

- **D-R1 dlv2-51.** Name the workflow `release-v2.yml` until dlv2-53 replaces
  `release.yml`. Implement the publish-time tag check (the tag object still
  resolves to the admitted commit) as well as D-A3, running under the target
environment protection per D-A3. Test end to end against
  `test-pypi`. If TestPyPI has no trusted publisher for this workflow, that is a
  stop (section 5).
- **D-R2 dlv2-52 version.** Next version `0.5.0` (the `[Unreleased]` changelog
  section has user-facing "Before you upgrade" changes); rehearse `0.5.0rc1` on
  TestPyPI. The rehearsal needs dlv2-22 accepted first. After the final tag, the
  item waits for the owner's `pypi` approval; then verify hashes, attestation
  and `t3:install` per the item.
- **D-R3 dlv2-44.** Delete a script used only by a removed hook if `git grep`
  finds no other use; otherwise leave it for dlv2-81.
- **D-R4 dlv2-42.** Keep `tests/unit/test_contributing_pr_open_auto_merge_docs.py`
  if `CONTRIBUTING.md` still documents `make pr-open` auto-merge. Keep
  `scripts/canary_impact.py` unless D-K1 is no-go.
- **D-R5 dlv2-defer-tpch-digest-darwin-arm64.** This Mac is Apple Silicon
  (`arm64`): run `make test-correctness-gate` at SF=1 here. If Q2, Q10 and Q15
  match, remove the stale guidance at `AGENTS.md:125` and
  `docs/operations/release-guide.md:50-51` and close. If they do not, document
  the gate as Linux-only there instead and keep the item open with the digests.
- **D-R6 Waiting chain (dlv2-81 to -84, when unblocked).** `path-filters.yml`
  stays. `pytest-ci.ini` stays unless all six Makefile targets work unchanged
  with `pytest.ini`. dlv2-82 keeps its dlv2-53 edge (no `make/release.mk`
  move). dlv2-84 measures with `dev_loop_pr_metrics.py` from a worktree at
  baseline commit `64f2f1f6`, and redefines T2 as "PR required-check
  time-to-green p95 ≤15 minutes"; the 30-day comparison is a follow-up item with
  `not_before`.
- **D-R7 tuning-liveness.** Wait for its needs; do not land a partial status
  matrix.
- **D-R8 Exasol.** Not prioritized. `exasol-docker-ci-smoke` stays `blocked`.

## 8. Verification and review gates

- Per item: every acceptance criterion met with the evidence the item names
  (run ids, PR numbers, test names), recorded in the item's context before
  `finish`. An open or green PR is not proof of merge.
- Per PR: `code` review action with Critical/Required fixed; `make pr-preflight`;
  required checks green; an oracle success review on soundness PRs; merged.
  For phase 4 reviewer changes, the post-merge check named in the item is part of
  the gate: no `finish` without its run id on the item. For merged-but-open
  items, `finish` needs the merge SHA plus the acceptance evidence, not a new PR.
- Tests for touched areas, for example:
  `uv run -- pytest tests/unit/scripts/oracle_reviewers tests/unit/scripts/test_oracle_review_check.py tests/unit/workflows`,
  `uv run -- pytest tests/unit/scripts/test_comment_policy.py`, and
  `make comment-policy-check`.
- Nightly T3: a dispatched `nightly-v2` run with every non-cloud domain green
  (D-T7), recorded on dlv2-22 and the drift and perf items.
- Final report (Todo batch guide format): one row per item with tracker state,
  PR and state, head, verification, blocker and next action, plus the ledger
  path. List every `waiting` item with its resume condition from section 4.8.

## 9. Pointers

- Prior handoffs: `_project/handoffs/2026-10-09-oracle-followups-batch-handoff.md`
  (oracle process and failure modes), `_project/handoffs/2026-10-08-nightly-run-fixes-handoff.md`.
- Decision records: `_project/decisions/merge-queue-retirement-2026-10-03.md`,
  `_project/decisions/oracle-review-protocol-2026-10-08.md`,
  `docs/development/adr/adr-dev-loop-v2.md` (D4, D5, D7).
- Operations docs: `docs/operations/oracle-review-v2.md`,
  `docs/operations/soundness-drain.md`, `docs/operations/nightly-t3.md`,
  `docs/operations/release-artifacts.md`, `docs/internal/public-site-visual-baseline.md`.

## 10. Current status (2026-10-12, revision 4)

The 75 original item states below were read live from the `todo-state` tracker
on 2026-10-12 at develop tip `045d742d7`. Counts: **7 done, 1 active, 39 open
and ready, 21 open but not ready, and 7 blocked**. That is 7/75 (9.3%)
complete, with the absence-classification fix as PR #2911 awaiting merge.
“Ready” is tracker claimability only; it does not override the order,
acceptance, external gates, or owner-only boundaries in this handoff. Recheck
the tracker before each claim. The original batch ledger is
`/Users/joe/Developer/.todo-batch/BenchBox-ci-devloop-20261009.txt`; it is a
cached work log, not the source of current tracker state. Since revision 2,
#2829, #2835 and #2907 merged, #2910 closed unmerged, and the comment-policy
gate is green (0 violations) — but the tracker items behind those merges are
still open and need `take` plus merge evidence and `finish` (section 4.9).

### Phase 1 — CI signal

- **Done (3):** `oracle-verdict-workflow-not-soundness-path-20261009`,
  `public-site-visual-baseline-missing-for-base-20261009`,
  `t3-durations-refresh-failure-20261009`.
- **Open, ready (3):** `ci-docker-hub-unauthenticated-pulls-20261009`,
  `t3-drift-corpus-mismatch-20261009`,
  `heavy-tier-merge-group-dead-code-20261009`.
- **Open, not ready (1):** `t3-perf-aggregate-gate-tolerance-decision-20261009`.
- **Blocked (3):** `nightly-windows-pr-visual-approval-20261009`,
  `t3-drift-dispatch-verify-20261008`,
  `flaky-bigquery-credential-defaults-confirm-prompt`.

### Phase 2 — Windows

- **Done (2):** `nightly-v2-liveness-port-20261008`,
  `nightly-windows-unquarantine-20261008`.
- **Open, ready (5):** `agent-write-preflight-quoted-interpreter-20261009`,
  `windows-session-lock-holder-readable-20261009`,
  `windows-iceberg-vacuum-local-paths-20261009`,
  `windows-iceberg-file-uri-locations-20261009`,
  `windows-pytest-xdist-teardown-hang-20261009`.
- **Open, not ready (1):** `nightly-windows-verification-run-20261009`.
- **Blocked (2):** `nightly-windows-utf8-reads-20261008`,
  `nightly-v2-windows-unit-leg-20261009`.

### Phase 3 — Nightly T3

- **Open, ready (6):** `ruleset-drift-check-home-20261009`,
  `nightly-v2-throughput-uat-port-20261009`,
  `cross-surface-mutation-sensitivity-not-enforced-20261009`,
  `polars-2-compat-post-merge-coverage-20261009`,
  `nightly-v2-unported-jobs-decision-20261009`,
  `monitor-nightly-cedardb-throughput-gate-and-floor`.
  Tracker readiness does not satisfy acceptance: the floor still needs five
  same-runner-class scheduled samples; inspect the floor log before closing it.
- **Open, not ready (2):** `dlv2-22-nightly-t3-shadow`,
  `dlv2-70-retire-scheduled-workflows`.

### Phase 4 — Oracle

- **Done (1):** `oracle-review-stale-check-runs-block-merge-20261008`.
- **Active (1):** `oracle-absence-classification-lows-20261008` — fix committed
  and open as PR #2911 (CI green, oracle review pending on reviewer capacity).
- **Open, ready (12):** `oracle-muse-invalid-verdict-output-20261009`,
  `oracle-self-attested-file-list-reviewers-20261008`,
  `oracle-author-family-labels-never-applied-20261009`,
  `oracle-tier-reason-lists-every-file-20261009`,
  `oracle-strike-count-distinct-patches-20261008`,
  `oracle-reviewer-checkout-has-no-base-commit-20261009`,
  `oracle-absence-classification-lows-20261008`,
  `oracle-patch-hash-hunk-positions-20261008`,
  `oracle-runner-integrity-lows-20261008`,
  `pr-landing-soundness-refusal-unreachable-20261009`,
  `unattributed-update-branch-commits-block-merge-20261008`,
  `actions-runner-queue-oracle-latency-20261009`,
  `pr-2761-hollow-success-post-merge-review-20261008`.
- **Open, not ready (3):** `oracle-read-evidence-tightening-20261008`,
  `oracle-checker-drop-legacy-first-line-20261023`,
  `review-soundness-merge-ceremony`.

### Phase 5 — Comment policy

- **Done (1):** `comment-scanner-unresolved-runner-commands`.
- **Open, ready (5):** `comment-policy-strict-scope-mismatch`,
  `comment-policy-digest-and-base-exception-catch22-20261008`,
  `comment-policy-hook-saturates-shared-host`,
  `comment-scanner-stdin-piped-interpreter-adapter`,
  `comment-scanner-mdx-jsx-support`.
- **Blocked (1):** `comment-policy-pinned-notice-drift-guard`.

### Phase 6 — Canary

- **Open, ready (1):** `canary-impact-03-replay-and-go-decision`.
- **Open, not ready (3):** `canary-impact-04-shadow-job`,
  `canary-impact-05-shadow-watch`, `canary-impact-06-required-gate`.

### Phase 7 — Release and cleanup

- **Open, ready (7):** `v-tag-ruleset-allows-tag-moves-20261009`,
  `dlv2-51-release-yml-v2`,
  `repo-admin-settings-actions-pr-permission-doc-20261009`,
  `sync-workflow-stale-branch-cleanup`, `dlv2-80a-github-cleanup-queue-era`,
  `dlv2-44-precommit-trim`, `dlv2-defer-tpch-digest-darwin-arm64`.
- **Open, not ready (3):** `dlv2-52-release-rehearsal-and-first-release`,
  `dlv2-53-retire-legacy-release-path`,
  `dlv2-42-retire-dev-loop-scripts-tests`.

### Phase 8 — External gates

- **Open, not ready (8):** `dlv2-62-publication-cutover-and-retirement`,
  `dlv2-80-github-cleanup`, `dlv2-81-config-cleanup`,
  `dlv2-82-makefile-rewrite`, `dlv2-83-docs-cleanup`,
  `dlv2-84-final-audit-and-metrics`, `site-split-52-ruleset-landing-context`,
  `tuning-liveness-e2e-gates-20261001`.
- **Blocked (1):** `exasol-docker-ci-smoke`; the tracker still lacks owner
  priority for Exasol.

### Live PRs and cross-item blockers

- #2835 is **MERGED** (2026-10-12). Its visual-approval and liveness items
  remain open: take each, verify the merge SHA plus the named evidence, and
  finish. Do not reopen the PR.
- #2821 is **MERGED**. Its runner-command item is done; the stdin and MDX
  tracker items remain open and need acceptance reconciliation.
- #2829 is **MERGED** (2026-10-12). Its pinned-notice item remains open:
  verify the merged head against the acceptance (section 7 D-C3 as amended),
  then take and finish. Reconcile remaining conflicts with the merged #2821
  only if the acceptance check finds them.
- #2837 is **MERGED**. Its corpus item remains open pending the combined
  `nightly-v2` verification; the separate drift-dispatch item is blocked.
- #2888 is **MERGED**. The Docker-pull item remains open until the combined
  verification confirms the Docker domain and codespell job.
- #2890 is **MERGED** and its public-site baseline item is done.
- #2900 is **MERGED** and completes the O1 stale-check item.
- #2907 is **MERGED** (2026-10-12) and the comment-policy gate is green
  (0 violations at `045d742d7`). The shell-payload follow-up is verifiable now:
  confirm the ten coverage errors are gone on develop, then take and finish it.
- #2910 (revert of #2821) is **CLOSED unmerged**: #2821 stands. No action.
- #2911 is **OPEN** (absence-classification fix, head `3d432d3c6`): CI green,
  oracle review pending. The current oracle outage (codex reviewers erroring)
  blocks its merge; while it waits, work stream B in section 4.9.
- #2905 is **OPEN/BLOCKED** at head
  `0034da936f8b390596f3927af1c060f13e646051`. Its `oracle-review`,
  `medium-test (shard 1)`, and `core` checks fail, so required gates still block
  merge despite an Oracle SHIP result. The gate is green at revision 4, so
  re-read its checks before acting: fix what still fails in scope.
- #2908 is **OPEN/BLOCKED** at head
  `df9bdbf03c81f9824e9d56c746c63fe3c48f4c18`. At the revision-3 check it fails
  `comment-policy`, `tooling`, `oracle-review`, plus `explorer`, `core`, and
  `docs` (run `38167645111` showed the `comment-policy` + `tooling` subset with
  Linux tests passing at the time). Re-read its checks before acting; the
  BigQuery flake's root cause remains unproven.
- The prepared O2/O5/O6 changes remain uncommitted in
  `/Users/joe/Developer/BenchBox.wt-oracle-reviewer-diversity-strikes`.
  That worktree is dirty and 18 commits behind `origin/develop`; preserve its
  changes, refresh the tracker claim, and reconcile it with current `develop`
  before continuing.

### Logged follow-ups and current stop conditions

- `comment-policy-shell-payload-coverage-20261011` is **open, not ready** but
  verifiable at revision 4: #2907 merged and the gate shows 0 violations on
  develop `045d742d7`. Confirm the ten coverage errors are gone, then take and
  finish it. Do not weaken the required gate or add broad exceptions.
- `native-task-model-tier-selection-20261011` is **blocked**. The native
  dispatch interfaces available here expose agent roles but no explicit Sol or
  Luna selector. The user forbids external agents. Do not claim a tier-specific
  dispatch without a native run proving it. Until proven, every D-A7 dispatch
  verification (nightly-v2, nightly, docs, oracle-verdict) and the D-O15 local
  harness run are blocked, not just the two follow-ups.
- The separate follow-up handoff
  `_project/handoffs/2026-10-11-ci-devloop-followups-batch-handoff.md` is
  staged but uncommitted on `docs/ci-devloop-followups-20261011`; its recorded
  commit attempt is blocked by the same comment-policy coverage errors. Its
  ledger is `/Users/joe/Developer/.todo-batch/BenchBox-ci-devloop-followups-20261011.txt`.
- The immediate dated risk is the raw Muse evidence for
  `oracle-muse-invalid-verdict-output-20261009`, whose attempt artifacts expire
  on 2026-10-16. The Oracle first-line cleanup and merge-ceremony review are
  held until 2026-10-23. The canary, release, site-split, tuning, and Exasol
  gates remain subject to the tracker dependencies and owner-only boundaries.
- The live tracker snapshot supersedes the readiness wording in sections 2 and
  4.8 but does not change the 75-item scope or the decisions in section 7.
  Resolve the shared comment-policy gate and the native model-selection
  blocker, then resume the ordered batch; do not bypass either blocker.

Revision 4 refreshes the live status at develop `045d742d7` (#2829, #2835 and
#2907 merged; #2910 closed unmerged; gate green; #2911 open), adds the
section 4.9 pipelining sequence so parallel workers and claim-free prep keep
moving while PRs wait on CI and merge, and marks merged-but-open items as
take-plus-finish with merge evidence. Revision 3 corrected the terminal-state definition, the D-C5 split order,
the #2908 snapshot, the D-A2 clear rule, the D-A3/D-R1 enforcement point, the
phase 4 post-merge finish gate, the D-C2 ordering warning, the dispatch-blocker
scope, and the unblockers-first order. Revision 2 added the current tracker states for all 75 original items, the two
follow-ups, and live PR/blocker evidence. The revision 1 state in section 3 is
retained as history.

## 11. Current status and runbook (2026-10-12, revision 5)

Live tracker read at develop tip `cfbcae26d`: **7 done, 2 active, 62 open
(41 ready, 21 not ready), 4 blocked** — 9.3% done. Since revision 4, #2905
(heavy-tier dead code) merged; no item finished. The two active claims are
`comment-policy-strict-scope-mismatch` (PR #2915, this session) and
`oracle-absence-classification-lows-20261008` (PR #2911, another worker).

### Merged-but-open: take, verify, finish (no new code)

In this order: `heavy-tier-merge-group-dead-code-20261009` (#2905, merged
2026-10-12), `comment-policy-pinned-notice-drift-guard` (#2829, merge
`65b1443e`), `nightly-windows-pr-visual-approval-20261009` (#2835, merge
`c660e615`). Each was unblocked from a stale flag in revision 5 with its
merge evidence on the item; each still needs `take` plus merge-SHA and
acceptance evidence and `finish`. `nightly-v2-liveness-port-20261008` is
already done; do not reopen #2835.

### PR #2915 state and binding refinements

PR #2915 (`comment-policy-strict-scope-mismatch`, branch
`fix/comment-policy-strict-scope`) is at `6e1e4342e`, rebased on
`cfbcae26d`, `pr-preflight` green. Three oracle rounds answered
(5 defects, then 2, then 0 new with priors carried); a fourth verdict is
pending. Binding refinements from implementation (recorded on the item):

- Strict ownership defaults to `merge-base(HEAD, origin/develop)`, not HEAD.
  This deviates from the acceptance letter and keeps its testable criterion:
  on a clean develop checkout the merge-base is HEAD, proven by strict
exiting 0 over 7029 files with 0 violations. The Makefile carries no
explicit base wiring.
- A vendored wheel is skipped only when the scanned bytes equal the base
  revision bytes and the base lockfile pins their hash; any swap fails closed
  as a coverage-error. A PR that legitimately bumps a wheel flags strict
  until merge by design; it merges through the comparison gate plus oracle
  review of the soundness-path registry change.
- Existence checks read the staged index under `--staged`; baseline scans
  verify against the base revision.
- Digest-payload exceptions recount to 70 on current develop (acceptance
  text says 68); the claiming session for the catch-22 item uses the live
  number for the docs fix.

### Evidence captured without a claim

- Muse invalid output: 1 of 2 required invalid answers captured via local
  `muse exec` mirroring the runner (extra-data class, parser-confirmed),
  retained with provenance and digests at
  `~/.todo-batch/muse-invalid-20261012/`; 7 valid SHIP controls alongside.
  Local hunt paused on diminishing returns; the reliable path to the second
  invalid is the diagnostic CI sidecar in the ledger recommendation.
  Artifacts expire 2026-10-16.
- Catch-22 design prepped in the ledger (symbol-scoped digest sketch,
  D-C2 ordering, fixture regeneration warning).

### Ordered runbook for the remainder

1. Land #2915 (rerun oracle-review serially on success, arm, merge),
   `finish` the strict-scope item with the merge SHA, releasing the claim.
2. Take-finish the three merged-but-open items above, then
   `comment-policy-digest-and-base-exception-catch22-20261008` (needs #2915
   green; regenerates the digest fixtures it invalidates in the same PR).
3. Resume phase order from section 4: phase-1 verification dispatch
   (one combined `nightly-v2` with `run_drift=true` for docker, drift-corpus
   and perf-floor items), phase-2 Windows (verification dispatch first),
   phase-3 T3 completion, phase-4 oracle batches with per-item post-merge
   evidence, phase-6 canary serially, phase-7 release chain.
4. Calendar-gated items stay parked: canary 05/06 (+14 days), oracle
   first-line cleanup and merge ceremony (2026-10-23), dlv2-84 metrics
   (+30 days). Owner-gated items (`pypi` approval, Exasol priority,
   site-split/tuning programmes) wait untouched.
5. Stop conditions in section 5 still bind, plus: never rebase a gated PR
   onto exception-dependent heads; never weaken a required check to satisfy
   an acceptance letter — record the deviation and its equivalence proof
   on the item instead.
